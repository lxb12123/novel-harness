"""滚动总结：幂等生成、确定性读取、写入审计。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

import pytest

from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import (
    CompletionResult,
    ProviderError,
    ProviderFailureKind,
)
from novel_harness.draft.rolling_summary import (
    SUMMARIZER_CAPABILITY,
    RollingSummarizer,
    SummaryChapterNotFound,
    SummaryGenerationError,
    SummaryRequest,
    SummaryStore,
)
from novel_harness.draft.summarize import (
    SUMMARY_VERSION,
    build_summary_messages,
)
from novel_harness.graph import ChapterSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


QUOTE = "顾清音在渡口把玄铁令交给萧决，随即动身前往北荒。"
SUMMARY_TEXT = "顾清音在渡口把玄铁令交给萧决，随后独自前往北荒。"


@dataclass(frozen=True)
class Seed:
    path: Path
    project_id: str

    def connection(self) -> Connection:
        return connect(self.path)


@pytest.fixture
def seed(tmp_path) -> Seed:
    path = tmp_path / "rolling-summary.db"
    conn = connect(path)
    migrate(conn)
    project_id = create_project(conn, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(conn)
    for number, text in ((1, QUOTE + "\n"), (2, QUOTE + "\n"), (3, QUOTE + "\n")):
        graph.put_chapter(
            ChapterSpec(
                project_id=project_id,
                number=number,
                heading=f"第{number}章",
                path=f"chapters/{number:04d}.md",
                text=text,
            )
        )
    conn.close()
    return Seed(path=path, project_id=project_id)


class Summarizer:
    def __init__(self, text: str = SUMMARY_TEXT) -> None:
        self.calls = 0
        self.requests: list[SummaryRequest] = []
        self.result = CompletionResult(
            text=text,
            model="summarizer-test-model",
            finish_reason="stop",
            prompt_tokens=88,
            completion_tokens=32,
        )

    def __call__(self, request: SummaryRequest) -> CompletionResult:
        self.calls += 1
        self.requests.append(request)
        return self.result


def _runner(seed: Seed, analyzer, **kwargs) -> RollingSummarizer:
    made = {"calls": 0}

    def call_id(_project_id: str) -> str:
        made["calls"] += 1
        return f"call:{made['calls']}"

    def summary_id(_project_id: str) -> str:
        return f"summary:{made['calls']}"

    return RollingSummarizer(
        seed.connection,
        analyzer,
        summary_id_factory=kwargs.pop("summary_id_factory", summary_id),
        call_id_factory=kwargs.pop("call_id_factory", call_id),
        **kwargs,
    )


def test_summary_prompt_is_versioned_deterministic_and_contains_exact_text() -> None:
    first = build_summary_messages("正文第一段。")
    second = build_summary_messages("正文第一段。")
    assert SUMMARY_VERSION
    assert first == second
    assert first[-1] == {"role": "user", "content": "正文第一段。"}
    assert "不超过 120 个中文字符" in first[0]["content"]


def test_ensure_generates_once_and_reuses_without_another_call(seed: Seed) -> None:
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)

    first = runner.ensure(seed.project_id, 1)
    second = runner.ensure(seed.project_id, 1)

    assert first == second
    assert first.summary == SUMMARY_TEXT
    assert first.schema_version == SUMMARY_VERSION
    assert first.model_call_id == "call:1"
    assert analyzer.calls == 1

    conn = seed.connection()
    try:
        row = conn.execute(
            """
            SELECT capability, params_json, prompt_hash, chapter_number FROM model_call
            WHERE id = 'call:1'
            """
        ).fetchone()
        assert row["capability"] == "summarizer"
        # 总结的是第 1 章，账也记在第 1 章上（迁移 009）。反查照旧兜着旧行。
        assert row["chapter_number"] == 1
        assert json.loads(row["params_json"])["schema_version"] == SUMMARY_VERSION
        summary = SummaryStore(conn).get(seed.project_id, 1)
        assert summary is not None and summary.summary == SUMMARY_TEXT
    finally:
        conn.close()


def test_ensure_missing_chapter_raises_without_paid_call(seed: Seed) -> None:
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    with pytest.raises(SummaryChapterNotFound):
        runner.ensure(seed.project_id, 99)
    assert analyzer.calls == 0


def test_ensure_empty_summary_writes_no_summary_but_still_bills_the_call(
    seed: Seed,
) -> None:
    """答了空文本：一行总结都不留，但**那一次调用要上账**。

    ── 这条断言 2026-09-09 翻了个面 ────────────────────────────────────
    它原来写的是 `COUNT(*) FROM model_call == 0`，名字叫「persists nothing」——
    也就是把「没写出总结」读成了「什么都没发生」。**那两件事不是一件**：请求已经
    发出去，端点已经答了，钱可能已经付掉（`record_failed_call` 的 docstring），
    只是答出来的东西不能用。账上看不见的钱是查不出来的钱。

    端点答上了话，所以**模型名是知道的**，不该记成 unknown。
    """
    analyzer = Summarizer(text="   \n")
    runner = _runner(seed, analyzer)
    with pytest.raises(SummaryGenerationError):
        runner.ensure(seed.project_id, 1)
    conn = seed.connection()
    try:
        assert SummaryStore(conn).get(seed.project_id, 1) is None
        row = conn.execute(
            "SELECT capability, model, call_state, chapter_number FROM model_call"
        ).fetchone()
        assert row is not None
        assert (row["capability"], row["call_state"]) == (SUMMARIZER_CAPABILITY, "FAILED")
        assert row["model"] == "summarizer-test-model"
        assert row["chapter_number"] == 1
    finally:
        conn.close()


def test_ensure_records_a_failed_call_and_closes_the_job_when_the_provider_raises(
    seed: Seed,
) -> None:
    """provider 抛异常：一行 FAILED 的账 + 一张收成终态的单。

    ── 这条钉的是 2026-09-08 那次真书事故 ──────────────────────────────
    端点（opencode Go）开始强制要 `x-opencode-session`，每一次调用都是
    `400 MissingSessionID`。31 章的总结批量失败，而在这条路补上审计之前：
    `model_call` 里 `capability='summarizer'` 的 FAILED 是 **0 条**，
    `summary_generation_job` 攒了 68 张停在 `RUNNING` 的单。当天能查出原因
    靠的是抽取那一侧的行——**总结自己一个字都没留下**。

    `model` 记成 `unknown` 是对的：调用是在还没听到任何回答时炸的。
    """
    failure = ProviderError("模型调用失败(400 MissingSessionID)", kind=ProviderFailureKind.UNKNOWN)

    def explode(_request: SummaryRequest) -> CompletionResult:
        raise failure

    runner = _runner(seed, explode)
    with pytest.raises(ProviderError):
        runner.ensure(seed.project_id, 1)

    conn = seed.connection()
    try:
        assert SummaryStore(conn).get(seed.project_id, 1) is None
        row = conn.execute(
            """
            SELECT capability, model, call_state, error_type, error_message,
                   in_artifact, prompt_hash, tokens_in, tokens_out, cost, chapter_number
              FROM model_call
            """
        ).fetchone()
        assert row is not None
        assert (row["capability"], row["call_state"]) == (SUMMARIZER_CAPABILITY, "FAILED")
        assert row["model"] == "unknown"
        assert row["error_type"] == ProviderFailureKind.UNKNOWN.value
        assert "MissingSessionID" in row["error_message"]
        # 发出去的那份 prompt 照记（事后排查的第一个问题就是「这次发的是哪一份」）；
        # 答案那一侧的数留 NULL——没答上来就是没有这些数，绝不估。
        assert row["in_artifact"] and row["prompt_hash"]
        assert (row["tokens_in"], row["tokens_out"], row["cost"]) == (None, None, None)
        assert row["chapter_number"] == 1
        # 单收成终态：一张永远 RUNNING 的单会被调度那侧当成「卡住了」反复重抢。
        assert [r["status"] for r in conn.execute("SELECT status FROM summary_generation_job")] == [
            "FAILED"
        ]
    finally:
        conn.close()


def test_for_range_returns_latest_summary_per_chapter_in_order(seed: Seed) -> None:
    runner = _runner(seed, Summarizer())
    runner.ensure(seed.project_id, 2)
    runner.ensure(seed.project_id, 1)
    conn = seed.connection()
    try:
        store = SummaryStore(conn)
        rows = store.for_range(seed.project_id, 1, 3)
        assert [row.chapter_number for row in rows] == [1, 2]
        # 只读接口不动数据库；缺章不出现。
        assert store.get(seed.project_id, 3) is None
    finally:
        conn.close()


def test_summary_table_enforces_one_call_per_prompt_via_unique_key(
    seed: Seed,
) -> None:
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    stored = runner.ensure(seed.project_id, 1)
    conn = seed.connection()
    try:
        # 手工再插一条同 (project, chapter, version, prompt_hash) 的行必须被唯一键拒绝。
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO chapter_summary (
                    id, project_id, chapter_number, summary,
                    schema_version, prompt_hash
                ) VALUES (?, ?, 1, '重复', ?, ?)
                """,
                (
                    "summary:dup",
                    seed.project_id,
                    SUMMARY_VERSION,
                    stored.prompt_hash,
                ),
            )
    finally:
        conn.close()
