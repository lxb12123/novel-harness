"""Explicit passive confirmation of selected provisional facts."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import Iterator

from .. import project
from ..db import Connection
from ..events import EventStore, EventStoreError, EventView
from ..graph import EdgeStatus, EvidenceStatus, GraphStore, InformationScope
from ..graph.review_store import EdgeReviewStore, EdgeReviewValidationError
from ..graph.sqlite_review import SqliteEdgeReviewStore
from .proposal_audit import append_audit, build_audit_envelope
from .proposal_models import (
    DecisionAuditError,
    ProposalAction,
    ProposalShapeError,
    ProvisionalConfirmation,
)


@contextmanager
def _transaction(conn: Connection) -> Iterator[None]:
    if conn.in_transaction:
        raise RuntimeError("provisional confirmation 必须在无外层事务的连接上启动")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _ids(ids: Sequence[str], what: str) -> tuple[str, ...]:
    selected = tuple(ids)
    if not selected or any(not item for item in selected):
        raise ProposalShapeError(f"{what} 确认集合不能为空")
    if len(selected) != len(set(selected)):
        raise ProposalShapeError(f"{what} 确认集合不能重复")
    return selected


def _version(conn: Connection, project_id: str, expected: int) -> int:
    current = project.require_canon_version(conn, project_id)
    if current != expected:
        raise project.StaleBaseVersion(project_id, expected=expected, current=current)
    return current


def _event_sources(
    events: EventStore,
    evidence_store: EdgeReviewStore,
    project_id: str,
    event_ids: Sequence[str],
):
    views: list[EventView] = []
    evidence = []
    for event_id in event_ids:
        view = events.event(project_id, event_id)
        if view is None or (
            view.event.information_scope is not InformationScope.PROVISIONAL
            or view.event.status is not EdgeStatus.ACTIVE
            or view.event.evidence_status is not EvidenceStatus.FRESH
        ):
            raise ProposalShapeError(
                f"event {event_id} 必须是同项目 ACTIVE/FRESH PROVISIONAL"
            )
        views.append(view)
        evidence.append(evidence_store.evidence(project_id, view.event.evidence_id))
    return tuple(views), tuple(evidence)


def _append_confirmation(
    conn: Connection,
    *,
    project_id: str,
    canon_version: int,
    events,
    edges,
    fact_ids: tuple[str, ...],
) -> str:
    envelope = build_audit_envelope(
        proposal_id=None,
        action=ProposalAction.ACCEPT,
        status="ACCEPTED",
        canon_version=canon_version,
        kind="provisional_confirm",
        events=events,
        edges=edges,
    )
    try:
        return append_audit(
            conn,
            project_id=project_id,
            action=ProposalAction.ACCEPT,
            envelope=envelope,
        ).id
    except Exception as exc:
        raise DecisionAuditError(
            "provisional confirmation 已提交，但决策日志写入失败",
            proposal_id=None,
            canon_version=canon_version,
            fact_ids=fact_ids,
        ) from exc


def confirm_provisional_events(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    project_id: str,
    event_ids: Sequence[str],
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProvisionalConfirmation:
    selected = _ids(event_ids, "event")
    evidence_store = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    with _transaction(conn):
        current = _version(conn, project_id, expected_canon_version)
        _sources, evidence = _event_sources(
            events, evidence_store, project_id, selected
        )
        promoted: list[EventView] = []
        try:
            for event_id in selected:
                promoted.append(events.clone_to_scope(event_id, InformationScope.CANON))
        except EventStoreError as exc:
            raise ProposalShapeError(str(exc)) from exc
        canon_version = project.compare_and_bump_canon_version(conn, project_id, current)
    pairs = tuple(zip(promoted, evidence, strict=True))
    decision_id = _append_confirmation(
        conn,
        project_id=project_id,
        canon_version=canon_version,
        events=pairs,
        edges=(),
        fact_ids=selected,
    )
    return ProvisionalConfirmation(
        project_id=project_id,
        canon_version=canon_version,
        decision_id=decision_id,
        events=tuple(promoted),
    )


def confirm_provisional_event(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    project_id: str,
    event_id: str,
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProvisionalConfirmation:
    return confirm_provisional_events(
        conn,
        graph,
        events,
        project_id,
        [event_id],
        expected_canon_version=expected_canon_version,
        edge_review_store=edge_review_store,
    )


def confirm_provisional_edges(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    edge_ids: Sequence[str],
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProvisionalConfirmation:
    selected = _ids(edge_ids, "edge")
    edge_store = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    with _transaction(conn):
        current = _version(conn, project_id, expected_canon_version)
        try:
            sources = edge_store.hydrate_provisional(project_id, selected)
            evidence = tuple(
                edge_store.evidence(project_id, item.edge.evidence_id or "")
                for item in sources
            )
            promoted = edge_store.clone_to_canon(project_id, selected)
        except EdgeReviewValidationError as exc:
            raise ProposalShapeError(str(exc)) from exc
        canon_version = project.compare_and_bump_canon_version(conn, project_id, current)
    pairs = tuple((item, ev) for item, ev in zip(promoted, evidence, strict=True))
    decision_id = _append_confirmation(
        conn,
        project_id=project_id,
        canon_version=canon_version,
        events=(),
        edges=pairs,
        fact_ids=selected,
    )
    return ProvisionalConfirmation(
        project_id=project_id,
        canon_version=canon_version,
        decision_id=decision_id,
        edges=promoted,
    )


def confirm_provisional_edge(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    edge_id: str,
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProvisionalConfirmation:
    return confirm_provisional_edges(
        conn,
        graph,
        project_id,
        [edge_id],
        expected_canon_version=expected_canon_version,
        edge_review_store=edge_review_store,
    )
