"""Real HTTP coverage for the explicit M4 extraction/event slice."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness import project
from novel_harness.activity import run_error_label
from novel_harness.db import connect, migrate
from novel_harness.draft.capabilities import ReasoningEffort, StructuredCallPlan
from novel_harness.draft.provider import CompletionResult, ProviderConfig
from novel_harness.extract.control import AnalysisRequest
from novel_harness.extract.models import RawChapterAnalysis, RawEvent
from novel_harness.extract.runner import ExtractionRunner
from novel_harness.events import ProvisionalEventSpec
from novel_harness.graph import (
    ChapterSpec,
    ChapterText,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.api.deps import get_conn, get_extraction_runner


CHAPTER_TEXT = "第一章 渡口\n\n顾清音在渡口把玄铁令交给萧决。\n"


@pytest.fixture
def extraction_book(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "extraction-api.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(root)).id
    SqliteStoryGraph(conn).put_chapter(
        ChapterSpec(
            project_id=pid,
            number=1,
            heading="第一章 渡口",
            path="chapters/0001.md",
            text=CHAPTER_TEXT,
        )
    )
    chapters = root / "chapters"
    chapters.mkdir(parents=True)
    chapters.joinpath("0001.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    other_root = tmp_path / "other-book"
    other_pid = project.create(conn, name="别册", root_path=str(other_root)).id
    conn.close()
    return {"db": str(db), "pid": pid, "other_pid": other_pid, "root": str(root)}


@pytest.fixture
def extraction_client(
    extraction_book: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", extraction_book["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "empty-settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    app.dependency_overrides.clear()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_explicit_extract_returns_202_and_background_config_failure_is_queryable(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    pid = extraction_book["pid"]

    queued = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")

    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "PENDING"
    run_id = queued.json()["id"]
    loaded = extraction_client.get(f"/api/projects/{pid}/extractions/{run_id}")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["status"] == "FAILED"
    # 出参是**作者的话**，不是 `{code, message}`：这条端点的唯一消费者是审阅面板，
    # 而 `ExtractionRunError.message` 是写给维护者的英文诊断（`extract/control.py`）。
    # 措辞的唯一出处是 `activity._RUN_ERROR_LABEL`，这里不抄第二份字面量。
    assert loaded.json()["errors"] == [run_error_label("provider_failure")]
    assert "chapter analysis provider failed" not in loaded.text
    assert "provider_failure" not in loaded.text


def test_extract_force_rerun_creates_a_fresh_run(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    pid = extraction_book["pid"]

    first = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")
    assert first.status_code == 202, first.text
    first_id = first.json()["id"]

    # 幂等：不带 force 复用同一条 run。
    same = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")
    assert same.json()["id"] == first_id

    # 「重跑本章」：force=true 把失败的 run 重置回 PENDING（同一条 run，可再付一次调用）。
    forced = extraction_client.post(
        f"/api/projects/{pid}/chapters/1/extract?force=true"
    )
    assert forced.status_code == 202, forced.text
    assert forced.json()["status"] == "PENDING"
    assert forced.json()["id"] == first_id


def _analysis_json() -> str:
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="顾清音交出玄铁令。",
                quote="顾清音在渡口把玄铁令交给萧决。",
                participants=(),
                knowers=(),
                confidence=0.95,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()


def test_dependency_override_runs_once_and_repeated_post_reuses_the_terminal_run(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    calls: list[AnalysisRequest] = []

    def analyze(request: AnalysisRequest) -> CompletionResult:
        calls.append(request)
        return CompletionResult(text=_analysis_json(), model="api-test-model")

    runner = ExtractionRunner(
        lambda: connect(Path(extraction_book["db"])),
        analyze,
    )
    extraction_client.app.dependency_overrides[get_extraction_runner] = lambda: runner
    pid = extraction_book["pid"]

    first = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")
    second = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")
    loaded = extraction_client.get(
        f"/api/projects/{pid}/extractions/{first.json()['id']}"
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == "PENDING"
    assert second.json()["status"] == "SUCCEEDED"
    assert first.json()["id"] == second.json()["id"] == loaded.json()["id"]
    assert loaded.json()["status"] == "SUCCEEDED"
    assert len(calls) == 1


def test_background_transport_failure_is_persisted_instead_of_delaying_post_500(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    calls = 0

    def fail_transport(_request: AnalysisRequest) -> CompletionResult:
        nonlocal calls
        calls += 1
        raise RuntimeError("private transport detail")

    runner = ExtractionRunner(
        lambda: connect(Path(extraction_book["db"])),
        fail_transport,
    )
    extraction_client.app.dependency_overrides[get_extraction_runner] = lambda: runner
    pid = extraction_book["pid"]

    queued = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract")
    loaded = extraction_client.get(
        f"/api/projects/{pid}/extractions/{queued.json()['id']}"
    )

    assert queued.status_code == 202
    assert loaded.status_code == 200
    assert loaded.json()["status"] == "FAILED"
    assert loaded.json()["errors"] == [run_error_label("provider_failure")]
    assert "private transport detail" not in loaded.text
    assert calls == 1


def test_extraction_routes_hide_missing_and_cross_project_runs(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    pid = extraction_book["pid"]
    other_pid = extraction_book["other_pid"]
    queued = extraction_client.post(f"/api/projects/{pid}/chapters/1/extract").json()

    assert extraction_client.post(
        "/api/projects/project:missing/chapters/1/extract"
    ).status_code == 404
    missing_chapter = extraction_client.post(
        f"/api/projects/{pid}/chapters/999/extract"
    )
    assert missing_chapter.status_code == 404
    assert missing_chapter.json()["detail"]["error"] == "chapter_not_found"
    assert extraction_client.post(
        f"/api/projects/{pid}/chapters/0/extract"
    ).status_code == 422

    missing_run = extraction_client.get(
        f"/api/projects/{pid}/extractions/extraction_run:missing"
    )
    cross_project = extraction_client.get(
        f"/api/projects/{other_pid}/extractions/{queued['id']}"
    )
    assert missing_run.status_code == cross_project.status_code == 404
    assert missing_run.json()["detail"]["error"] == "extraction_run_not_found"
    assert cross_project.json()["detail"] == missing_run.json()["detail"] | {
        "run_id": queued["id"]
    }


class _SpyRunner:
    def __init__(self) -> None:
        self.enqueue_calls = 0
        self.run_calls = 0

    def enqueue(
        self, _project_id: str, _chapter: int, *, force: bool = False
    ) -> Any:
        self.enqueue_calls += 1
        raise AssertionError("non-extract routes must not enqueue")

    def run(self, _run_id: str) -> Any:
        self.run_calls += 1
        raise AssertionError("non-extract routes must not run")


def test_save_sync_and_import_do_not_implicitly_trigger_extraction(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    spy = _SpyRunner()
    extraction_client.app.dependency_overrides[get_extraction_runner] = lambda: spy
    pid = extraction_book["pid"]

    base = extraction_client.get(f"/api/projects/{pid}/chapters/1/text").json()["text_sha256"]
    saved = extraction_client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": CHAPTER_TEXT, "expected_text_sha256": base},
    )
    synced = extraction_client.post(f"/api/projects/{pid}/sync")
    made = extraction_client.post("/api/projects", json={"name": "新书"})
    imported = extraction_client.post(
        f"/api/projects/{made.json()['id']}/import",
        json={"text": "第一章 新篇\n\n这里是新书正文。\n"},
    )

    assert saved.status_code == synced.status_code == made.status_code == 200
    assert imported.status_code == 200, imported.text
    assert (spy.enqueue_calls, spy.run_calls) == (0, 0)


def test_production_analyzer_uses_exact_request_messages_and_validated_off_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import novel_harness.api.deps as api_deps

    config = ProviderConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="secret",
    )
    observed: dict[str, Any] = {}

    def fake_complete(messages, *, config, plan):
        observed.update(messages=messages, config=config, plan=plan)
        return CompletionResult(text=_analysis_json(), model=config.model)

    monkeypatch.setattr(api_deps, "_extraction_provider_config", lambda: config)
    monkeypatch.setattr(api_deps, "complete", fake_complete)
    request = AnalysisRequest(
        ChapterText(
            chapter_id="chapter:test",
            number=1,
            snapshot_id="artifact:test",
            text=CHAPTER_TEXT,
        )
    )

    result = api_deps._analyze_extraction(request)

    assert result.model == config.model
    assert observed["messages"] == request.wire_messages()
    assert observed["config"] is config
    plan = observed["plan"]
    assert isinstance(plan, StructuredCallPlan)
    assert plan.visible_token_budget == api_deps.EXTRACTION_VISIBLE_TOKEN_BUDGET == 8_192
    assert plan.prompt_token_budget == len(request.prompt_bytes)
    assert plan.reasoning_requested is plan.reasoning_effective is ReasoningEffort.OFF


def _seed_scoped_events(extraction_book: dict[str, str]) -> dict[str, str]:
    conn = connect(Path(extraction_book["db"]))
    pid = extraction_book["pid"]
    graph = SqliteStoryGraph(conn)
    character = graph.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps.model_validate({"plot_note": "不得从事件接口泄漏"}),
        )
    )
    secret = graph.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.FACTION,
            name="玄铁令来历",
            props=NodeProps.model_validate({"twist": "真正来历不得泄漏"}),
        )
    )
    chapter_text = (
        "第二章 夜谈\n\n"
        "顾清音在渡口把玄铁令交给萧决。\n"
        "萧决把玄铁令收入怀中，独自离开。\n"
    )
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=pid,
            number=2,
            heading="第二章 夜谈",
            path="chapters/0002.md",
            text=chapter_text,
        )
    )
    first_evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=2,
            quote_text="顾清音在渡口把玄铁令交给萧决。",
        )
    )
    second_evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=3,
            quote_text="萧决把玄铁令收入怀中，独自离开。",
        )
    )
    generated = iter(("event:z", "event:a", "event:canon"))
    events = SqliteEventStore(conn, event_id_factory=lambda _pid: next(generated))
    first = events.put_provisional(
        ProvisionalEventSpec(
            project_id=pid,
            summary="顾清音交出玄铁令。",
            evidence_id=first_evidence.id,
            participant_ids=[character.id],
            knower_ids=[character.id],
            confidence=0.91,
        )
    )
    events.put_provisional(
        ProvisionalEventSpec(
            project_id=pid,
            summary="萧决带走玄铁令。",
            evidence_id=second_evidence.id,
            participant_ids=[character.id],
            confidence=0.88,
        )
    )
    canon = events.clone_to_scope(first.event.id, InformationScope.CANON)
    conn.close()
    return {"character": character.id, "secret": secret.id, "canon": canon.event.id}


def test_events_endpoint_is_stable_narrow_and_strictly_scope_isolated(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    ids = _seed_scoped_events(extraction_book)
    pid = extraction_book["pid"]

    provisional = extraction_client.get(
        f"/api/projects/{pid}/chapters/2/events",
        params={"scope": "PROVISIONAL"},
    )
    canon = extraction_client.get(
        f"/api/projects/{pid}/chapters/2/events",
        params={"scope": "CANON"},
    )

    assert provisional.status_code == canon.status_code == 200
    assert [view["event"]["id"] for view in provisional.json()] == ["event:a", "event:z"]
    assert {
        view["event"]["information_scope"] for view in provisional.json()
    } == {"PROVISIONAL"}
    assert [view["event"]["id"] for view in canon.json()] == [ids["canon"]]
    assert {view["event"]["information_scope"] for view in canon.json()} == {"CANON"}
    assert set(provisional.json()[0]) == {
        "event",
        "participants",
        "knowers",
    }
    assert "不得从事件接口泄漏" not in provisional.text
    assert "真正来历不得泄漏" not in provisional.text
    participant = provisional.json()[0]["participants"][0]
    assert set(participant) == {"id", "label", "name"}

    assert extraction_client.get(
        f"/api/projects/{pid}/chapters/2/events",
        params={"scope": "PLANNED"},
    ).status_code == 422
    assert extraction_client.get(
        f"/api/projects/{pid}/chapters/2/events"
    ).status_code == 422
    assert extraction_client.get(
        f"/api/projects/{pid}/chapters/999/events",
        params={"scope": "CANON"},
    ).status_code == 404
    assert extraction_client.get(
        f"/api/projects/{pid}/chapters/0/events",
        params={"scope": "CANON"},
    ).status_code == 422


def test_event_read_uses_one_request_cached_connection(
    extraction_client: TestClient,
    extraction_book: dict[str, str],
) -> None:
    _seed_scoped_events(extraction_book)
    opened = 0

    def counted_conn():
        nonlocal opened
        opened += 1
        conn = connect(Path(extraction_book["db"]))
        try:
            yield conn
        finally:
            conn.close()

    extraction_client.app.dependency_overrides[get_conn] = counted_conn
    response = extraction_client.get(
        f"/api/projects/{extraction_book['pid']}/chapters/2/events",
        params={"scope": "PROVISIONAL"},
    )

    assert response.status_code == 200, response.text
    assert opened == 1
