"""滚动总结：幂等生成、确定性读取、写入审计。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

import pytest

from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import (
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
            SELECT capability, params_json, prompt_hash FROM model_call
            WHERE id = 'call:1'
            """
        ).fetchone()
        assert row["capability"] == "summarizer"
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


def test_ensure_empty_summary_raises_and_persists_nothing(seed: Seed) -> None:
    analyzer = Summarizer(text="   \n")
    runner = _runner(seed, analyzer)
    with pytest.raises(SummaryGenerationError):
        runner.ensure(seed.project_id, 1)
    conn = seed.connection()
    try:
        assert SummaryStore(conn).get(seed.project_id, 1) is None
        assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 0
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
