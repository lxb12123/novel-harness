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
                revealed_facts=(),
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
    """020 / Task 9：run 创建后正文换过版本 → 晚到结果 SUPERSEDED。

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
    assert "secret provider detail" not in failed.errors[0].message
    conn = seed.connection()
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    conn.close()


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
    assert "call id unavailable" not in failed.errors[0].message
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
