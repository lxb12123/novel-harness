"""SQLite ``ProposalStore`` integration tests."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import threading

import pytest

from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.decisions import DecisionKind, Verdict, append as append_decision
from novel_harness.events import (
    ProposalCreate,
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalResolutionMark,
    ProposalStatus,
    ProposalStore,
    ProposalValidationError,
    ProvisionalEventSpec,
)
from novel_harness.graph import (
    ChapterSpec,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


def _seed_link_targets(conn: Connection) -> tuple[str, str, str, str]:
    project_id = create_project(conn, name="青云记-links", root_path=".").id
    graph = SqliteStoryGraph(conn)
    first = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="顾清音")
    )
    second = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    edge = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=first.id,
            dst=second.id,
            type=EdgeType.RELATED_TO,
            valid_from_chapter=1,
            information_scope=InformationScope.PROVISIONAL,
        )
    ).edge
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=1,
            heading="第一章",
            path="chapters/0001.md",
            text="顾清音见到了萧决。\n",
        )
    )
    evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=project_id,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=0,
            quote_text="顾清音见到了萧决。",
        )
    )
    event = SqliteEventStore(
        conn,
        event_id_factory=lambda pid: f"event:proposal-target:{pid}",
    ).put_provisional(
        ProvisionalEventSpec(
            project_id=project_id,
            summary="两人相遇。",
            evidence_id=evidence.id,
            participant_ids=[first.id, second.id],
            confidence=0.9,
        )
    )
    return project_id, event.event.id, edge.id, chapter.snapshot_id


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


def test_sqlite_proposal_store_exposes_the_repository_contract(conn: Connection) -> None:
    store = SqliteProposalStore(conn, proposal_id_factory=lambda _project_id: "proposal:test")

    assert isinstance(store, ProposalStore)
    assert store.get("project:missing", "proposal:missing") is None


def test_sqlite_proposal_store_has_a_production_id_factory(conn: Connection) -> None:
    assert isinstance(SqliteProposalStore(conn), ProposalStore)


def test_create_serializes_structured_items_and_round_trips_a_record(
    conn: Connection,
) -> None:
    project_id = create_project(conn, name="青云记", root_path=".").id
    conn.execute("UPDATE project SET canon_version = 3 WHERE id = ?", (project_id,))
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:fixed",
    )
    proposal = ProposalCreate(
        project_id=project_id,
        kind="entity_resolution",
        summary="顾姑娘可能指向顾清音。",
        items=[
            {
                "surface": "顾姑娘",
                "confidence": 0.6,
                "aliases": ["清音", "グー・チンイン", "🙂"],
                "meta": {"语言": "中文", "confirmed": False},
            }
        ],
        confidence=0.6,
        base_canon_version=3,
        schema_version="m4.v1",
        prompt_hash="prompt-a",
    )

    created = store.create(proposal)

    assert created.id == "proposal:fixed"
    assert created.status is ProposalStatus.PENDING
    assert created.item_count == 1
    assert created.items == proposal.items
    assert created.created_at.endswith("Z")
    assert store.get(project_id, created.id) == created
    row = conn.execute(
        "SELECT items_json, item_count FROM proposal_set WHERE id = ?",
        (created.id,),
    ).fetchone()
    assert row["items_json"] == json.dumps(
        proposal.items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert row["item_count"] == proposal.item_count


def test_create_defensively_rejects_non_finite_constructed_payloads(
    conn: Connection,
) -> None:
    project_id = create_project(conn, name="青云记-strict-json", root_path=".").id
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:invalid-json",
    )
    bypassed_validation = ProposalCreate.model_construct(
        project_id=project_id,
        kind="invalid_json",
        summary="",
        items=[{"nested": [float("nan")]}],
        confidence=None,
        chapter_number=None,
        snapshot_id=None,
        base_canon_version=0,
        schema_version=None,
        prompt_hash=None,
        event_ids=[],
        edge_ids=[],
    )

    with pytest.raises(ProposalValidationError, match="strict UTF-8 JSON"):
        store.create(bypassed_validation)

    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0


@pytest.mark.parametrize(
    "mutated_value",
    [float("nan"), "顾\ud800姑娘"],
    ids=["nan", "lone-surrogate"],
)
def test_create_rejects_mutated_nested_items_before_any_sql_writes(
    conn: Connection,
    mutated_value: object,
) -> None:
    project_id, event_id, edge_id, _snapshot_id = _seed_link_targets(conn)
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:mutated-json",
    )
    proposal = ProposalCreate(
        project_id=project_id,
        kind="mutated_json",
        items=[{"nested": {"value": "valid"}}],
        event_ids=[event_id],
        edge_ids=[edge_id],
    )
    root = proposal.items[0]
    assert isinstance(root, dict)
    nested = root["nested"]
    assert isinstance(nested, dict)
    nested["value"] = mutated_value

    with pytest.raises(ProposalValidationError, match="strict UTF-8 JSON"):
        store.create(proposal)

    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM proposal_event").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM proposal_edge").fetchone()[0] == 0


def test_create_links_provisional_events_and_edges_atomically(conn: Connection) -> None:
    project_id, event_id, edge_id, snapshot_id = _seed_link_targets(conn)
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:linked",
    )

    created = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="edge_conflict",
            items=[{"conflict": "relationship"}],
            chapter_number=1,
            snapshot_id=snapshot_id,
            event_ids=[event_id, event_id],
            edge_ids=[edge_id, edge_id],
        )
    )

    assert created.event_ids == [event_id]
    assert created.edge_ids == [edge_id]
    assert conn.execute("SELECT COUNT(*) FROM proposal_event").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM proposal_edge").fetchone()[0] == 1


@pytest.mark.parametrize("link_kind", ["event", "edge"])
def test_create_rejects_non_provisional_links_without_leaving_a_proposal(
    conn: Connection,
    link_kind: str,
) -> None:
    project_id, event_id, edge_id, _snapshot_id = _seed_link_targets(conn)
    canon_event = SqliteEventStore(
        conn,
        event_id_factory=lambda _project_id: "event:canon-target",
    ).clone_to_scope(event_id, InformationScope.CANON)
    edge_row = conn.execute(
        "SELECT src, dst, type, valid_from_chapter FROM edge WHERE id = ?",
        (edge_id,),
    ).fetchone()
    canon_edge = (
        SqliteStoryGraph(conn)
        .upsert_edge(
            EdgeSpec(
                project_id=project_id,
                src=edge_row["src"],
                dst=edge_row["dst"],
                type=edge_row["type"],
                valid_from_chapter=edge_row["valid_from_chapter"],
                information_scope=InformationScope.CANON,
            )
        )
        .edge
    )
    links = (
        {"event_ids": [canon_event.event.id]}
        if link_kind == "event"
        else {"edge_ids": [canon_edge.id]}
    )
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:invalid",
    )

    with pytest.raises(ProposalValidationError, match="PROVISIONAL"):
        store.create(
            ProposalCreate(
                project_id=project_id,
                kind="edge_conflict",
                items=[{"conflict": link_kind}],
                **links,
            )
        )

    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0


@pytest.mark.parametrize("link_kind", ["event", "edge"])
def test_create_rejects_cross_project_links_atomically(
    conn: Connection,
    link_kind: str,
) -> None:
    left_project, _left_event, _left_edge, _snapshot = _seed_link_targets(conn)
    _right_project, right_event, right_edge, _right_snapshot = _seed_link_targets(conn)
    links = {"event_ids": [right_event]} if link_kind == "event" else {"edge_ids": [right_edge]}
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:cross-project",
    )

    with pytest.raises(ProposalValidationError, match="项目.*PROVISIONAL"):
        store.create(
            ProposalCreate(
                project_id=left_project,
                kind="cross_project",
                items=[{"link": link_kind}],
                **links,
            )
        )

    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0


def test_create_surfaces_project_chapter_snapshot_and_base_validation(
    conn: Connection,
) -> None:
    project_id, _event_id, _edge_id, snapshot_id = _seed_link_targets(conn)
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:invalid-context",
    )

    with pytest.raises(ProposalValidationError, match="项目.*不存在"):
        store.create(
            ProposalCreate(
                project_id="project:missing",
                kind="context",
                items=[{"case": "project"}],
            )
        )
    with pytest.raises(ProposalValidationError, match="chapter_number"):
        store.create(
            ProposalCreate(
                project_id=project_id,
                kind="context",
                items=[{"case": "chapter"}],
                chapter_number=99,
            )
        )
    with pytest.raises(ProposalValidationError, match="snapshot.*chapter"):
        store.create(
            ProposalCreate(
                project_id=project_id,
                kind="context",
                items=[{"case": "snapshot"}],
                chapter_number=2,
                snapshot_id=snapshot_id,
            )
        )
    conn.execute("UPDATE project SET canon_version = 2 WHERE id = ?", (project_id,))
    with pytest.raises(ProposalValidationError, match="base_canon_version"):
        store.create(
            ProposalCreate(
                project_id=project_id,
                kind="context",
                items=[{"case": "base"}],
                base_canon_version=1,
            )
        )

    assert conn.execute("SELECT COUNT(*) FROM proposal_set").fetchone()[0] == 0


def test_pending_is_stably_ordered_and_isolated_by_project_and_chapter(
    conn: Connection,
) -> None:
    project_id, _event_id, _edge_id, snapshot_id = _seed_link_targets(conn)
    other_project = create_project(conn, name="别书", root_path=".").id
    generated = iter(["proposal:b", "proposal:a", "proposal:other"])
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: next(generated),
    )
    chapter_record = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="chapter",
            items=[{"item": "chapter"}],
            chapter_number=1,
            snapshot_id=snapshot_id,
        )
    )
    unscoped_record = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="global",
            items=[{"item": "global"}],
        )
    )
    store.create(
        ProposalCreate(
            project_id=other_project,
            kind="other",
            items=[{"item": "other"}],
        )
    )
    conn.execute(
        "UPDATE proposal_set SET created_at = '2026-08-03T12:00:00.000Z'",
    )

    assert [record.id for record in store.pending(project_id)] == ["proposal:a", "proposal:b"]
    chapter_pending = store.pending(project_id, chapter_number=1)
    assert [record.id for record in chapter_pending] == [chapter_record.id]
    assert unscoped_record.id not in {record.id for record in chapter_pending}
    assert [record.id for record in store.pending(other_project)] == ["proposal:other"]


def test_pending_hydrates_every_record_from_one_wal_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "pending-snapshot.sqlite"
    reader_conn = connect(database)
    migrate(reader_conn)
    project_id = create_project(reader_conn, name="青云记-pending-snapshot", root_path=".").id
    reader_store = SqliteProposalStore(
        reader_conn,
        proposal_id_factory=lambda _project_id: "proposal:snapshot",
    )
    created = reader_store.create(
        ProposalCreate(
            project_id=project_id,
            kind="snapshot",
            items=[{"item": "pending"}],
        )
    )
    hydration_started = threading.Event()
    resolution_done = threading.Event()
    failures: list[BaseException] = []
    failures_lock = threading.Lock()
    hydration_seen = False

    def coordinate(statement: str) -> None:
        nonlocal hydration_seen
        normalized = " ".join(statement.upper().split())
        if (
            not hydration_seen
            and normalized.startswith("SELECT ID, PROJECT_ID, KIND")
            and "FROM PROPOSAL_SET" in normalized
        ):
            hydration_seen = True
            hydration_started.set()
            if not resolution_done.wait(timeout=5):
                with failures_lock:
                    failures.append(TimeoutError("writer did not resolve proposal"))

    reader_conn.set_trace_callback(coordinate)

    def resolve() -> None:
        if not hydration_started.wait(timeout=5):
            with failures_lock:
                failures.append(TimeoutError("reader did not begin hydration"))
            resolution_done.set()
            return
        writer_conn = connect(database)
        try:
            SqliteProposalStore(writer_conn).mark_resolved(
                created.id,
                ProposalResolutionMark(status="ACCEPTED"),
            )
        except BaseException as exc:
            with failures_lock:
                failures.append(exc)
        finally:
            writer_conn.close()
            resolution_done.set()

    writer = threading.Thread(target=resolve, daemon=True)
    writer.start()
    try:
        pending = reader_store.pending(project_id)
    finally:
        reader_conn.set_trace_callback(None)
    writer.join(timeout=10)

    assert not writer.is_alive()
    assert hydration_seen
    assert not failures
    assert [record.id for record in pending] == [created.id]
    assert all(record.status is ProposalStatus.PENDING for record in pending)
    final = reader_store.get(project_id, created.id)
    assert final is not None and final.status is ProposalStatus.ACCEPTED
    reader_conn.close()


def test_pending_does_not_commit_a_caller_owned_transaction(conn: Connection) -> None:
    project_id = create_project(conn, name="青云记-caller-transaction", root_path=".").id
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:uncommitted",
    )
    conn.execute("BEGIN IMMEDIATE")
    created = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="caller_transaction",
            items=[{"item": "uncommitted"}],
        )
    )

    assert store.pending(project_id) == [created]
    assert conn.in_transaction
    conn.rollback()
    assert store.get(project_id, created.id) is None


def test_mark_resolved_is_one_way_and_distinguishes_missing_from_terminal(
    conn: Connection,
) -> None:
    project_id = create_project(conn, name="青云记-review", root_path=".").id
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:review",
    )
    pending = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="review",
            items=[{"item": "review"}],
        )
    )
    conn.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("decision:review", project_id, "proposal_review", "{}", "accept"),
    )
    canon_before = conn.execute(
        "SELECT canon_version FROM project WHERE id = ?", (project_id,)
    ).fetchone()[0]

    with pytest.raises(ProposalNotFound, match="proposal:missing"):
        store.mark_resolved(
            "proposal:missing",
            ProposalResolutionMark(status="ACCEPTED"),
        )
    resolved = store.mark_resolved(
        pending.id,
        ProposalResolutionMark(
            status="ACCEPTED",
            decision_log_id="decision:review",
        ),
    )

    assert resolved.status is ProposalStatus.ACCEPTED
    assert resolved.resolved_at is not None and resolved.resolved_at.endswith("Z")
    assert resolved.decision_log_id == "decision:review"
    assert store.pending(project_id) == []
    assert (
        conn.execute("SELECT canon_version FROM project WHERE id = ?", (project_id,)).fetchone()[0]
        == canon_before
    )
    with pytest.raises(ProposalAlreadyResolved, match="ACCEPTED"):
        store.mark_resolved(
            pending.id,
            ProposalResolutionMark(status="REJECTED"),
        )


def test_mark_resolved_rejects_an_invalid_decision_reference_atomically(
    conn: Connection,
) -> None:
    project_id = create_project(conn, name="青云记-invalid-decision", root_path=".").id
    store = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _project_id: "proposal:pending",
    )
    pending = store.create(
        ProposalCreate(
            project_id=project_id,
            kind="review",
            items=[{"item": "review"}],
        )
    )

    with pytest.raises(ProposalValidationError, match="decision_log"):
        store.mark_resolved(
            pending.id,
            ProposalResolutionMark(
                status="ACCEPTED",
                decision_log_id="decision:missing",
            ),
        )

    assert store.get(project_id, pending.id) == pending


def test_attach_decision_is_terminal_only_same_project_and_idempotent(conn: Connection) -> None:
    project_id = create_project(conn, name="青云记-attach", root_path=".").id
    other_id = create_project(conn, name="别书-attach", root_path=".").id
    store = SqliteProposalStore(
        conn, proposal_id_factory=lambda _project_id: "proposal:attach"
    )
    pending = store.create(
        ProposalCreate(project_id=project_id, kind="review", items=[{"item": "review"}])
    )
    decision = append_decision(
        conn,
        project_id=project_id,
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.ACCEPT,
    )
    append_decision(
        conn,
        project_id=other_id,
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.ACCEPT,
    )
    different_decision = append_decision(
        conn,
        project_id=project_id,
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.ACCEPT,
    )

    with pytest.raises(ProposalValidationError, match="terminal|PENDING"):
        store.attach_decision(pending.id, decision.id)
    store.mark_resolved(pending.id, ProposalResolutionMark(status="ACCEPTED"))
    attached = store.attach_decision(pending.id, decision.id)
    assert attached.decision_log_id == decision.id
    assert store.attach_decision(pending.id, decision.id) == attached

    with pytest.raises(ProposalValidationError, match="already|different|已"):
        store.attach_decision(pending.id, different_decision.id)
    assert store.get(project_id, pending.id) == attached


def test_attach_decision_rejects_cross_project_and_unaudited_lists_only_holes(
    conn: Connection,
) -> None:
    project_id = create_project(conn, name="青云记-holes", root_path=".").id
    other_id = create_project(conn, name="别书-holes", root_path=".").id
    generated = iter(["proposal:hole", "proposal:audited", "proposal:pending"])
    store = SqliteProposalStore(conn, proposal_id_factory=lambda _pid: next(generated))
    hole = store.create(
        ProposalCreate(project_id=project_id, kind="review", items=[{"n": 1}])
    )
    audited = store.create(
        ProposalCreate(project_id=project_id, kind="review", items=[{"n": 2}])
    )
    pending = store.create(
        ProposalCreate(project_id=project_id, kind="review", items=[{"n": 3}])
    )
    store.mark_resolved(hole.id, ProposalResolutionMark(status="REJECTED"))
    store.mark_resolved(audited.id, ProposalResolutionMark(status="ACCEPTED"))
    wrong = append_decision(
        conn,
        project_id=other_id,
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.ACCEPT,
    )
    with pytest.raises(ProposalValidationError, match="项目|project"):
        store.attach_decision(audited.id, wrong.id)
    right = append_decision(
        conn,
        project_id=project_id,
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.ACCEPT,
    )
    store.attach_decision(audited.id, right.id)

    assert [record.id for record in store.unaudited(project_id)] == [hole.id]
    assert store.get(project_id, pending.id) == pending
