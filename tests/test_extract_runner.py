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
        self.chapters = []
        self.result = result or CompletionResult(
            text=_analysis_json(),
            model="extractor-test-model",
            finish_reason="stop",
            prompt_tokens=123,
            completion_tokens=45,
        )

    def __call__(self, chapter):
        self.calls += 1
        self.chapters.append(chapter)
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
        "tokens_in, tokens_out, ms FROM model_call WHERE id = ?",
        ("call:fixed",),
    ).fetchone()
    assert row["capability"] == "extractor"
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


def test_run_uses_the_immutable_enqueued_snapshot_after_current_text_changes(seed: Seed) -> None:
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

    assert result.status is ExtractionRunStatus.SUCCEEDED
    assert analyzer.chapters[0].text == QUOTE + "\n"
    assert analyzer.chapters[0].snapshot_id == queued.snapshot_id
    assert queued.snapshot_id != current_snapshot


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
