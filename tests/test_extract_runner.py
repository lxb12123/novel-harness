"""Background extraction run state machine and paid-call idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import threading

import pytest

import novel_harness.extract.runner as runner_module
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.extract import (
    ANALYSIS_SCHEMA_VERSION,
    RawChapterAnalysis,
    RawEvent,
    build_analysis_messages,
    parse_analysis,
)
from novel_harness.extract.runner import (
    ExtractionChapterNotFound,
    ExtractionRunNotFound,
    ExtractionRunner,
    ExtractionRunStatus,
)
from novel_harness.graph import ChapterSpec, NodeLabel, NodeProps, NodeSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import artifact_id
from novel_harness.project import create as create_project


QUOTE = "顾清音在渡口把玄铁令交给萧决。"


@dataclass(frozen=True)
class Seed:
    path: Path
    project_id: str

    def connection(self) -> Connection:
        return connect(self.path)


@pytest.fixture
def seed(tmp_path) -> Seed:
    path = tmp_path / "extract-runner.db"
    conn = connect(path)
    migrate(conn)
    project_id = create_project(conn, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(conn)
    graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(main_character=True),
        )
    )
    graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=3,
            heading="第三章 渡口",
            path="chapters/0003.md",
            text=QUOTE + "\n",
        )
    )
    conn.close()
    return Seed(path=path, project_id=project_id)


def _analysis_json(*, confidence: float = 0.92, quote: str = QUOTE) -> str:
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="顾清音交出玄铁令。",
                quote=quote,
                participants=("顾清音",),
                knowers=("顾清音",),
                confidence=confidence,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()


class Analyzer:
    def __init__(self, result: CompletionResult | None = None) -> None:
        self.calls = 0
        self.requests = []
        self.result = result or CompletionResult(
            text=_analysis_json(),
            model="extractor-test-model",
            finish_reason="stop",
            prompt_tokens=123,
            completion_tokens=45,
        )

    def __call__(self, request):
        self.calls += 1
        self.requests.append(request)
        return self.result


def _runner(seed: Seed, analyzer, **kwargs) -> ExtractionRunner:
    return ExtractionRunner(
        seed.connection,
        analyzer,
        run_id_factory=kwargs.pop("run_id_factory", lambda _pid: "report:fixed"),
        call_id_factory=kwargs.pop("call_id_factory", lambda _pid: "call:fixed"),
        **kwargs,
    )


def test_enqueue_is_immediate_content_addressed_and_reuses_the_same_run(seed: Seed) -> None:
    analyzer = Analyzer()
    made_ids = 0

    def run_id(_project_id: str) -> str:
        nonlocal made_ids
        made_ids += 1
        return f"report:{made_ids}"

    runner = _runner(seed, analyzer, run_id_factory=run_id)
    first = runner.enqueue(seed.project_id, 3)
    second = runner.enqueue(seed.project_id, 3)

    assert first == second
    assert first.status is ExtractionRunStatus.PENDING
    assert analyzer.calls == 0
    assert made_ids == 1
    canonical = json.dumps(
        build_analysis_messages(QUOTE + "\n"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert first.prompt_hash == sha256(canonical).hexdigest()
    assert first.schema_version == ANALYSIS_SCHEMA_VERSION


def test_enqueue_force_resets_a_failed_run_for_a_new_paid_call(
    seed: Seed,
) -> None:
    calls: list[object] = []

    def failing_analyzer(request: object) -> object:
        calls.append(request)
        raise RuntimeError("boom")

    runner = _runner(seed, failing_analyzer)
    first = runner.enqueue(seed.project_id, 3)
    assert runner.run(first.id).status is ExtractionRunStatus.FAILED

    forced = runner.enqueue(seed.project_id, 3, force=True)
    assert forced.id == first.id  # 同一章同一 prompt 仍是同一条 run
    assert forced.status is ExtractionRunStatus.PENDING
    assert forced.model_call_id is None
    assert forced.errors == ()
    # 不带 force 的幂等复用返回重置后的 PENDING run，不会重复付费调用。
    again = runner.enqueue(seed.project_id, 3)
    assert again == forced
    assert len(calls) == 1


def test_get_is_a_pure_read_that_never_claims_or_calls_the_analyzer(seed: Seed) -> None:
    analyzer = Analyzer()
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)

    loaded = runner.get(queued.id)

    assert loaded == queued
    assert loaded.status is ExtractionRunStatus.PENDING
    assert analyzer.calls == 0
    with pytest.raises(ExtractionRunNotFound):
        runner.get("extraction_run:missing")
    assert analyzer.calls == 0


@pytest.mark.parametrize(
    ("chapter_number", "error_type"),
    [
        (True, TypeError),
        (False, TypeError),
        (3.0, TypeError),
        ("3", TypeError),
        (None, TypeError),
        (0, ValueError),
        (-1, ValueError),
    ],
)
def test_enqueue_rejects_invalid_chapter_number_before_opening_a_connection(
    seed: Seed,
    chapter_number: object,
    error_type: type[Exception],
) -> None:
    opened = 0

    def connection() -> Connection:
        nonlocal opened
        opened += 1
        return seed.connection()

    runner = ExtractionRunner(connection, Analyzer())

    with pytest.raises(error_type):
        runner.enqueue(seed.project_id, chapter_number)  # type: ignore[arg-type]

    assert opened == 0


def test_default_run_id_uses_the_extraction_run_entity_type(seed: Seed) -> None:
    runner = ExtractionRunner(seed.connection, Analyzer())

    queued = runner.enqueue(seed.project_id, 3)

    assert queued.id.startswith("extraction_run:")


def test_success_records_extractor_call_and_second_run_never_pays_again(seed: Seed) -> None:
    analyzer = Analyzer()
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)

    succeeded = runner.run(queued.id)
    again = runner.run(queued.id)

    assert succeeded.status is ExtractionRunStatus.SUCCEEDED
    assert again == succeeded
    assert analyzer.calls == 1
    assert succeeded.valid_event_count == 1
    assert succeeded.discarded_event_count == 0
    assert succeeded.proposal_count == 0
    assert succeeded.model_call_id == "call:fixed"
    conn = seed.connection()
    row = conn.execute(
        "SELECT capability, model, params_json, prompt_hash, in_artifact, out_artifact, "
        "tokens_in, tokens_out, ms, chapter_number FROM model_call WHERE id = ?",
        ("call:fixed",),
    ).fetchone()
    assert row["capability"] == "extractor"
    # 「这一次是为哪一章花的」由账自己答（迁移 009）。反查（`extraction_run.model_call_id`）
    # 照旧兜着这一列出现之前的旧行，两条路都在，见 `activity._call_chapter`。
    assert row["chapter_number"] == 3
    assert row["model"] == "extractor-test-model"
    assert row["prompt_hash"] == queued.prompt_hash
    assert json.loads(row["params_json"]) == {
        "finish_reason": "stop",
        "schema_version": ANALYSIS_SCHEMA_VERSION,
    }
    prompt_bytes = json.dumps(
        build_analysis_messages(QUOTE + "\n"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert row["in_artifact"] == artifact_id(prompt_bytes)
    assert row["out_artifact"] == artifact_id(_analysis_json().encode())
    assert (row["tokens_in"], row["tokens_out"]) == (123, 45)
    assert row["ms"] >= 0
    conn.close()


def test_a_late_run_after_the_text_moved_is_superseded_not_ingested(seed: Seed) -> None:
    """021 / Task 9：run 创建后正文换过版本 → 晚到结果 SUPERSEDED。

    旧契约是「run 照旧用创建时那份不可变快照跑完」——Task 9 把它推翻了：晚到
    结果永远不能 ingest / 建提案 / auto-Canon（正文已经往前走，旧结果对着的是
    作者已经看不见的那一版）。prompt 仍按**创建时**的快照算（run 不该在运行中
    重新读"当前正文"），但**写盘前**的 basis 检查会把它拦下。
    """
    analyzer = Analyzer()
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)
    conn = seed.connection()
    graph = SqliteStoryGraph(conn)
    graph.put_chapter(
        ChapterSpec(
            project_id=seed.project_id,
            number=3,
            heading="第三章 改稿",
            path="chapters/0003.md",
            text="这是一份完全不同的新正文，不含旧引语。\n",
        )
    )
    current_snapshot = graph.current_snapshots(seed.project_id)[0].snapshot_id
    conn.close()

    result = runner.run(queued.id)

    assert result.status is ExtractionRunStatus.SUPERSEDED
    # 模型调用根本没发生（basis 检查在付费之前）——旧快照的 run 不给新正文掏钱。
    assert analyzer.calls == 0
    assert queued.snapshot_id != current_snapshot
    conn = seed.connection()
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0
    conn.close()


def test_a_run_claimed_before_the_save_pays_but_still_does_not_ingest(seed: Seed) -> None:
    """模型调用期间正文又变了：钱已经花了，但结果不能进库（审计保留）。

    `_basis_current` 检查两处：付费前（省钱）和 ingest 前（晚到防护）。这一条
    走的是第二处——claim 之后、模型调用期间保存了 S3。
    """
    gate: dict[str, bool] = {"released": False}

    class GatedAnalyzer:
        calls = 0

        def __call__(self, request):
            GatedAnalyzer.calls += 1
            # 模拟 provider 慢：调用进行中，作者保存了新正文。
            import time

            while not gate["released"]:
                time.sleep(0.005)
            return CompletionResult(
                text=_analysis_json(),
                model="extractor-test-model",
                finish_reason="stop",
                prompt_tokens=123,
                completion_tokens=45,
            )

    runner = _runner(seed, GatedAnalyzer())
    queued = runner.enqueue(seed.project_id, 3)

    conn = seed.connection()
    graph = SqliteStoryGraph(conn)
    import threading

    result: list = []

    def complete() -> None:
        result.append(runner.run(queued.id))

    worker = threading.Thread(target=complete)
    worker.start()
    # 等 provider 真的开始跑（call 已进入阻塞），再保存 S3。
    while GatedAnalyzer.calls == 0:
        import time

        time.sleep(0.005)
    graph.put_chapter(
        ChapterSpec(
            project_id=seed.project_id,
            number=3,
            heading="第三章 改稿",
            path="chapters/0003.md",
            text="这是一份完全不同的新正文，不含旧引语。\n",
        )
    )
    conn.close()
    gate["released"] = True
    worker.join()

    run = result[0]
    assert run.status is ExtractionRunStatus.SUPERSEDED
    assert run.model_call_id is not None  # 调用审计保留
    conn = seed.connection()
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0
    conn.close()


def test_malformed_json_is_parsed_once_failed_and_never_retried(seed: Seed) -> None:
    analyzer = Analyzer(CompletionResult(
        text="not valid JSON",
        model="bad-json-model",
        finish_reason="stop",
        prompt_tokens=9,
        completion_tokens=4,
    ))
    parse_calls = 0

    def counted_parser(text: str):
        nonlocal parse_calls
        parse_calls += 1
        return parse_analysis(text)

    runner = _runner(seed, analyzer, parser=counted_parser)
    queued = runner.enqueue(seed.project_id, 3)
    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert (analyzer.calls, parse_calls) == (1, 1)
    assert failed.model_call_id == "call:fixed"
    assert failed.errors[0].code == "analysis_format"
    conn = seed.connection()
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0
    conn.close()


def test_provider_exception_fails_without_graph_writes_or_retry(seed: Seed) -> None:
    calls = 0

    def explode(_chapter):
        nonlocal calls
        calls += 1
        raise RuntimeError("secret provider detail")

    runner = _runner(seed, explode)
    queued = runner.enqueue(seed.project_id, 3)
    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert calls == 1
    assert failed.model_call_id is None
    assert failed.errors[0].code == "provider_failure"
    # **provider 那句原话不许进 `errors_json`**：那份 JSON 会整份发给浏览器，
    # 而 provider 的错误文案里可能带着请求内容甚至 key 片段。原话只进
    # `model_call.error_message`（见下面那条）。
    assert "secret provider detail" not in failed.errors[0].message
    conn = seed.connection()
    # ⚠️ 2026-08-25 之前这一行断言的是 `== 0`。**那正是这次要修的东西**：
    # 失败的调用照样可能计费，而账上一行都没有。见下面那条专门的测试。
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# provider 失败：记账 + 分档（2026-08-25，真书上撞出来的）
# ══════════════════════════════════════════════════════════════════════════


def test_a_failed_provider_call_still_lands_one_row_in_the_ledger(seed: Seed) -> None:
    """**没答上来的那一次也要记一行。**

    2026-08-25 真书实测：跑一次失败的抽取，`model_call` 跑前 6 行、跑后还是 6 行
    ——那两列 `error_type` / `error_message` 在这条路上一次都没填过。
    **失败的调用照样可能计费**（按请求计、按已生成 token 计的供应商都有），
    而账本上看不见的钱是查不出来的钱。
    """
    from novel_harness.draft.provider import ProviderError, ProviderFailureKind

    def explode(_chapter):
        raise ProviderError("模型调用失败:余额不足", kind=ProviderFailureKind.QUOTA)

    runner = _runner(seed, explode)
    failed = runner.run(runner.enqueue(seed.project_id, 3).id)
    assert failed.status is ExtractionRunStatus.FAILED

    conn = seed.connection()
    try:
        rows = conn.execute(
            "SELECT capability, call_state, error_type, error_message, chapter_number, "
            "in_artifact, out_artifact, tokens_in, tokens_out, cost FROM model_call"
        ).fetchall()
        assert len(rows) == 1, f"失败的调用没进账：{rows}"
        row = rows[0]
        assert row["call_state"] == "FAILED"
        assert row["capability"] == "extractor"
        assert row["chapter_number"] == 3
        assert row["error_type"] == ProviderFailureKind.QUOTA
        assert "余额不足" in row["error_message"], "原话没进账 —— 那就查不出是为什么失败的"
        # prompt 是我们自己发出去的，哈希是确定的：「这一次发的是哪一份 prompt」
        # 正是事后排查要问的第一个问题。
        assert row["in_artifact"], "连发出去的那份 prompt 都没记"
        # **没答上来就是没有这些数**：绝不估（同「供应商报没报」那条规矩）。
        assert (row["out_artifact"], row["tokens_in"], row["tokens_out"], row["cost"]) == (
            None, None, None, None,
        )
    finally:
        conn.close()


def test_the_run_says_which_kind_of_provider_failure_it_was(seed: Seed) -> None:
    """401 和「连不上」不是同一件事，作者屏幕上也不许是同一句话。

    这一条钉的是那次实测：`opencode.ai/zen/v1` 余额耗尽返回 **401**，
    而屏幕上写的是「没能连上你配置的模型服务」——连上了，是账走错门，
    作者被那句话指去查网络和地址，查一天查不出来。

    分档判据在 `draft.provider.ProviderFailureKind`（**只看状态码，不读文案**）。
    """
    from novel_harness.draft.provider import ProviderError, ProviderFailureKind

    from test_wording_guard import BACKEND_MESSAGES, _ts_const_object_entry

    def run_error_label(code: str) -> str:
        """`ExtractionErrorCode` 原始值 → zh 那一路的话，读的是唯一那张前端表

        （`backendMessages.ts` 的 `RUN_ERROR_LABEL`，国际化第四批·笔二起
        译文搬去了前端，这里不重新拼一份）。"""
        source = BACKEND_MESSAGES.read_text(encoding="utf-8")
        return _ts_const_object_entry(source, "RUN_ERROR_LABEL", code, "zh")

    seen: dict[ProviderFailureKind, str] = {}
    for index, kind in enumerate(ProviderFailureKind):
        def explode(_chapter, _kind=kind):
            raise ProviderError("boom", kind=_kind)

        # **每一档一个新的 call id**：失败现在也记账了，共用一个固定 id 会在第二档
        # 撞 `model_call` 的主键——那本身就是「这一行真的写进去了」的旁证。
        runner = _runner(seed, explode, call_id_factory=lambda _pid, i=index: f"call:{i}")
        failed = runner.run(runner.enqueue(seed.project_id, 3, force=True).id)
        seen[kind] = failed.errors[0].code

    # 五档各自一个码，一个都不许重。
    assert len(set(seen.values())) == len(ProviderFailureKind), seen
    assert seen[ProviderFailureKind.AUTH] == "provider_auth"
    assert seen[ProviderFailureKind.QUOTA] == "provider_quota"
    assert seen[ProviderFailureKind.UNKNOWN] == "provider_failure"

    # 屏幕上那几句话也各不相同，且**都不出现 HTTP 状态码**（那是机器码）。
    said = {run_error_label(code) for code in seen.values()}
    assert len(said) == len(seen), f"两档翻成了同一句话：{said}"
    import re

    for text in said:
        assert not re.search(r"\b[45]\d\d\b", text), f"作者的话里出现了状态码：{text}"
    # 「没能连上」这句只许留给真的连不上那一档。
    assert run_error_label("provider_unreachable") == "无法连接所配置的模型服务"
    assert "连上" not in run_error_label("provider_quota")


def test_ingest_failure_rolls_back_business_data_but_retains_model_history(
    seed: Seed, monkeypatch
) -> None:
    analyzer = Analyzer(CompletionResult(
        text=_analysis_json(confidence=0.40),
        model="extractor-test-model",
        finish_reason="stop",
    ))

    def explode_create(self, proposal):
        raise RuntimeError(f"proposal unavailable: {proposal.kind}")

    monkeypatch.setattr(runner_module.SqliteProposalStore, "create", explode_create)
    runner = _runner(seed, analyzer)
    failed = runner.run(runner.enqueue(seed.project_id, 3).id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert failed.model_call_id == "call:fixed"
    assert failed.errors[0].code == "ingest_failure"
    conn = seed.connection()
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0
    conn.close()


def test_compare_and_set_allows_only_one_concurrent_analyzer(seed: Seed) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_analyzer(_chapter):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return CompletionResult(text=_analysis_json(), model="concurrent-model")

    runner = _runner(seed, blocking_analyzer)
    queued = runner.enqueue(seed.project_id, 3)
    results = []
    errors = []

    def first_run() -> None:
        try:
            results.append(runner.run(queued.id))
        except BaseException as exc:  # pragma: no cover - asserted empty below
            errors.append(exc)

    thread = threading.Thread(target=first_run)
    thread.start()
    assert entered.wait(timeout=5)
    competing = runner.run(queued.id)
    assert competing.status is ExtractionRunStatus.RUNNING
    assert calls == 1
    release.set()
    thread.join(timeout=5)

    assert errors == []
    assert results[0].status is ExtractionRunStatus.SUCCEEDED
    assert calls == 1


def test_typed_not_found_errors(seed: Seed) -> None:
    runner = _runner(seed, Analyzer())
    with pytest.raises(ExtractionChapterNotFound):
        runner.enqueue(seed.project_id, 999)
    with pytest.raises(ExtractionRunNotFound):
        runner.run("report:missing")


def test_process_interrupt_is_not_swallowed_as_a_provider_failure(seed: Seed) -> None:
    def interrupt(_chapter):
        raise KeyboardInterrupt

    runner = _runner(seed, interrupt)
    queued = runner.enqueue(seed.project_id, 3)

    with pytest.raises(KeyboardInterrupt):
        runner.run(queued.id)


def test_analyzer_input_and_audit_artifact_share_one_prompt_bound_request(seed: Seed) -> None:
    received = []

    def analyze(request):
        received.append(request)
        return CompletionResult(text=_analysis_json(), model="request-model")

    runner = _runner(seed, analyze)
    queued = runner.enqueue(seed.project_id, 3)
    result = runner.run(queued.id)

    request = received[0]
    assert result.status is ExtractionRunStatus.SUCCEEDED
    assert request.chapter.snapshot_id == queued.snapshot_id
    assert request.wire_messages() == build_analysis_messages(request.chapter.text)
    assert request.prompt_hash == queued.prompt_hash
    conn = seed.connection()
    in_artifact = conn.execute(
        "SELECT in_artifact FROM model_call WHERE id = ?",
        (result.model_call_id,),
    ).fetchone()[0]
    conn.close()
    assert in_artifact == artifact_id(request.prompt_bytes)


def test_prompt_drift_fails_before_the_paid_call_and_never_retries(seed: Seed) -> None:
    analyzer = Analyzer()
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)
    conn = seed.connection()
    conn.execute(
        "UPDATE extraction_run SET prompt_hash = ? WHERE id = ?",
        ("0" * 64, queued.id),
    )
    conn.commit()
    conn.close()

    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert failed.errors[0].code == "prompt_drift"
    assert analyzer.calls == 0


@pytest.mark.parametrize(
    "completion",
    [
        CompletionResult.model_construct(
            text=_analysis_json(),
            model="bad\ud800model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        ),
        CompletionResult.model_construct(
            text="bad\ud800text",
            model="model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        ),
        CompletionResult.model_construct(
            text=_analysis_json(),
            model="model",
            finish_reason="bad\ud800finish",
            prompt_tokens=1,
            completion_tokens=1,
        ),
        CompletionResult.model_construct(
            text=_analysis_json(),
            model="model",
            finish_reason="stop",
            prompt_tokens=-1,
            completion_tokens=1,
        ),
        CompletionResult.model_construct(
            text=_analysis_json(),
            model="model",
            finish_reason="stop",
            prompt_tokens=True,
            completion_tokens=1,
        ),
        CompletionResult.model_construct(
            text=_analysis_json(),
            model="model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1.5,
        ),
    ],
    ids=[
        "surrogate-model",
        "surrogate-text",
        "surrogate-finish-reason",
        "negative-token",
        "bool-token",
        "non-integer-token",
    ],
)
def test_invalid_completion_audit_fails_terminally_without_retry(
    seed: Seed,
    completion: CompletionResult,
) -> None:
    analyzer = Analyzer(completion)
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)

    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert failed.errors[0].code == "call_record_failure"
    assert failed.model_call_id is None
    assert analyzer.calls == 1


def test_none_token_counts_are_valid_audit_values(seed: Seed) -> None:
    analyzer = Analyzer(CompletionResult(
        text=_analysis_json(),
        model="tokenless-model",
        prompt_tokens=None,
        completion_tokens=None,
    ))
    runner = _runner(seed, analyzer)

    result = runner.run(runner.enqueue(seed.project_id, 3).id)

    assert result.status is ExtractionRunStatus.SUCCEEDED
    conn = seed.connection()
    tokens = conn.execute(
        "SELECT tokens_in, tokens_out FROM model_call WHERE id = ?",
        (result.model_call_id,),
    ).fetchone()
    conn.close()
    assert tuple(tokens) == (None, None)


def test_call_id_failure_after_paid_call_is_terminal_and_not_retried(seed: Seed) -> None:
    """付过钱之后审计失败 = 终态，不重试；**而失败的原因要留得下来。**

    ⚠️ **最后那条断言 2026-09-05 反过来了。** 它原来写的是
    `assert "call id unavailable" not in message` —— 即「异常文本不许进这一列」。
    那条纪律的代价当天在真书上量出来了：`extraction_run` 里 62 章分析失败，三个
    `except Exception` 各自只落一句固定的话，于是**库里、日志里、屏幕上全是同一句**，
    唯一的办法是再跑一次去猜（那还得再花一次钱）。实际原因是
    `SupersedeConflict`（倒着分析导致的乱序插入），而它本来就写在那个异常里。

    `ExtractionRunError.message` 按定义是**写给维护者的英文诊断**
    （`extract/control.py` 的字段 docstring），作者那一侧只读 `code`。
    「不上作者的屏幕」这条真正的保护在
    `test_wording_guard.py::test_the_review_panels_run_endpoint_forwards_the_code_never_the_diagnostic`
    ——它断言那句固定的话一个字都不出现在面板读的那份出参里，而那句话是新消息的
    前缀，所以整条消息漏出去它照样会红。
    """
    analyzer = Analyzer()

    def fail_call_id(_project_id: str) -> str:
        raise RuntimeError("call id unavailable")

    runner = _runner(seed, analyzer, call_id_factory=fail_call_id)
    queued = runner.enqueue(seed.project_id, 3)

    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert failed.errors[0].code == "call_record_failure"
    assert failed.errors[0].message.startswith("chapter analysis call could not be audited")
    assert "RuntimeError: call id unavailable" in failed.errors[0].message
    assert analyzer.calls == 1


def test_sql_failure_while_recording_a_paid_call_is_terminal(seed: Seed) -> None:
    conn = seed.connection()
    conn.execute(
        """
        INSERT INTO model_call (id, project_id, capability, model, prompt_hash)
        VALUES ('call:fixed', ?, 'fixture', 'fixture-model', 'fixture-hash')
        """,
        (seed.project_id,),
    )
    conn.commit()
    conn.close()
    analyzer = Analyzer()
    runner = _runner(seed, analyzer)
    queued = runner.enqueue(seed.project_id, 3)

    failed = runner.run(queued.id)
    again = runner.run(queued.id)

    assert failed.status is ExtractionRunStatus.FAILED
    assert again == failed
    assert failed.errors[0].code == "call_record_failure"
    assert failed.model_call_id is None
    assert analyzer.calls == 1


def test_recording_interrupt_is_not_swallowed_as_an_audit_failure(seed: Seed) -> None:
    analyzer = Analyzer()

    def interrupt_call_id(_project_id: str) -> str:
        raise KeyboardInterrupt

    runner = _runner(seed, analyzer, call_id_factory=interrupt_call_id)
    queued = runner.enqueue(seed.project_id, 3)

    with pytest.raises(KeyboardInterrupt):
        runner.run(queued.id)

    assert analyzer.calls == 1


# ══════════════════════════════════════════════════════════════════════════
# 全丢了不许静默报成功（2026-08-23，027）
# ══════════════════════════════════════════════════════════════════════════


def _unanchorable_analysis() -> str:
    """一件事，**引语在这一章的正文里找不到** —— 会被整条丢掉。

    ⚠️ 2026-08-25 之前这儿造的是「参与者不在角色册里」。那条路今天不丢了
    （认不出就建，ADR 0020 补记）——**而这个告警本身没有变**：它问的是
    「模型抽到了、我们一件都没留下」，不是「为什么没留下」。

    换成引语对不上，是因为那是今天**仍然会整条丢**的常见形态：模型把原话改写了
    一遍（`locate_quote` 的 min_ratio=0.90 卡住），这件事在真书上照样发生。
    """
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="贾环在渡口拿走玄铁令。",
                quote="这一句在这一章的正文里一个字都对不上。",
                participants=("贾环",),
                knowers=("贾环",),
                confidence=0.92,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()


def _notifications(conn: Connection, project_id: str):
    from novel_harness.system_notifications import (
        list_open_notifications,
        materialize_notification_outbox,
    )

    materialize_notification_outbox(conn, project_id=project_id, lease_owner="t")
    return list_open_notifications(conn, project_id)


def test_a_chapter_that_kept_nothing_does_not_pass_as_a_quiet_success(seed: Seed) -> None:
    """模型抽到了、我们一件都没留下 —— **作者必须知道**（2026-08-23 真书上的哑告警）。

    真书实测：第 1 / 2 / 158 章各抽到 12 / 11 / 12 件事，**留下 0 件**，
    而三次 run 全是 `SUCCEEDED` + `errors_json='[]'`。当时的丢弃条件只有一条
    （事件里的人在角色册里认不出来 ⇒ 整条丢），角色册又是空的，于是：

        角色册空 → 认不出 → 全丢 → 角色册还是空 → 下一章接着全丢

    整本书的图谱因此是空的，**而没有任何一处告诉过作者**。

    **那条死锁 2026-08-25 从根上解开了**（认不出就建，ADR 0020 补记），
    但**这个告警照旧要在**：它问的不是那一条成因，是「模型抽到了、一件都没留下」
    这个结果——引语对不上、称呼有歧义、错类，每一条都还能把一整章清空。
    这条红了 = 那个哑告警回来了。
    """
    analyzer = Analyzer(
        CompletionResult(
            text=_unanchorable_analysis(),
            model="extractor-test-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )
    )
    run = _runner(seed, analyzer)
    enqueued = run.enqueue(seed.project_id, 3)
    finished = run.run(enqueued.id)

    # run 本身仍然是「成功」——它确实跑完了，这一点不改（改了会牵动整条状态机）。
    assert finished.status is ExtractionRunStatus.SUCCEEDED
    assert (finished.valid_event_count, finished.discarded_event_count) == (0, 1)

    conn = seed.connection()
    notices = _notifications(conn, seed.project_id)
    kinds = [n.kind for n in notices]
    assert "extraction_yielded_nothing" in kinds, (
        f"一件都没留下却一条通知都没落——哑告警回来了。实得 {kinds}"
    )

    only = next(n for n in notices if n.kind == "extraction_yielded_nothing")
    assert only.title_code == "extraction_yielded_nothing_title"
    # 这条丢弃是因为引语锚不上（`_unanchorable_analysis` 的 quote 对不上正文），
    # 不是因为认不出参与者——`unresolved` 因此是 False，不是这份夹具的巧合。
    assert only.title_params == {"lost": 1, "unresolved": False, "proposal_count": 0}
    # **不阻断**：新档不许混进阻断那一侧，否则作者整理一次老章就把下游停了。
    from novel_harness.system_notifications import BLOCKING_KINDS

    assert only.kind not in BLOCKING_KINDS
    conn.close()


def test_a_chapter_that_kept_something_stays_quiet(seed: Seed) -> None:
    """留下了东西就**不报** —— 对照组，否则上一条可能是「永远报」而不是「该报才报」。

    判据是「valid==0 **且** discarded>0」：「模型明明抽到了、我们一件都没留住」才是异常。

    ⚠️ 顺带记一条**这一轮没修的**：`valid==0 且 discarded==0` 今天**不可达**——
    `RawChapterAnalysis.events` 的下限是 1，所以「这一章本来就没有事件」（写景、独白）
    在这一层压根表达不出来，模型只能硬编一件事出来。那个洞归它自己那一轮。
    """
    analyzer = Analyzer()  # 默认那份：参与者「顾清音」在角色册里
    run = _runner(seed, analyzer)
    finished = run.run(run.enqueue(seed.project_id, 3).id)

    assert finished.valid_event_count == 1 and finished.discarded_event_count == 0
    conn = seed.connection()
    assert [n.kind for n in _notifications(conn, seed.project_id)] == []
    conn.close()
