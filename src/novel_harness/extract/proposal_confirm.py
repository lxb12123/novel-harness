"""Explicit passive confirmation of selected provisional facts."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
import hashlib
import json
from typing import Iterator

from .. import project
from ..db import Connection
from ..decisions import DEFAULT_ACTOR
from ..events import EventStore, EventStoreError, EventView
from ..graph import (
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    GraphStore,
    InformationScope,
)
from ..graph.review_store import EdgeReviewStore, EdgeReviewValidationError
from ..graph.sqlite_proposals import ConfirmationReceipt, SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from ..ids import EntityType, new_id
from .proposal_audit import build_audit_envelope, ensure_proposal_audit
from .proposal_models import (
    ConfirmationConflict,
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


def _receipt_summary(actor: str, what: str) -> str:
    """回执摘要按 actor 分岔——「作者确认的」印在系统自己升上去的那条上就是假话。"""
    who = "作者确认的被动" if actor == DEFAULT_ACTOR else "系统自动生效的"
    return f"{who}{what}回执"


def _request_hash(fact_kind: str, fact_ids: tuple[str, ...]) -> str:
    canonical = json.dumps(
        [fact_kind, *sorted(fact_ids)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _event_items(pairs) -> list[dict]:
    return [
        {
            "source_kind": "event",
            "event_id": view.event.id,
            "summary": view.event.summary,
            "confidence": view.event.confidence,
            "quote": evidence.audit.quote_text,
        }
        for view, evidence in pairs
    ]


def _edge_items(pairs) -> list[dict]:
    items = []
    for item, evidence in pairs:
        kind = "relationship" if item.edge.type is EdgeType.RELATED_TO else "location"
        items.append(
            {
                "source_kind": "state_update",
                "update_kind": kind,
                "confidence": item.edge.confidence,
                "proposed": {
                    "edge_id": item.edge.id,
                    "subject_id": item.edge.src,
                    "target_id": item.edge.dst,
                    "value": item.edge.props.value,
                    "quote": evidence.audit.quote_text,
                },
            }
        )
    return items


def _create_event_receipt(
    proposals: SqliteProposalStore,
    project_id: str,
    *,
    request_hash: str,
    base_canon_version: int,
    resolved_canon_version: int,
    pairs,
    selected: tuple[str, ...],
    chapter_number: int,
    actor: str,
) -> ConfirmationReceipt:
    confirmation_id = new_id(EntityType.PROPOSAL, project_id)
    envelope = build_audit_envelope(
        proposal_id=confirmation_id,
        action=ProposalAction.ACCEPT,
        status="ACCEPTED",
        canon_version=resolved_canon_version,
        kind="provisional_confirm",
        events=pairs,
        actor=actor,
    )
    return proposals.create_confirmation_receipt(
        receipt_id=confirmation_id,
        project_id=project_id,
        fact_kind="event",
        request_hash=request_hash,
        base_canon_version=base_canon_version,
        resolved_canon_version=resolved_canon_version,
        items=_event_items(pairs),
        event_ids=selected,
        edge_ids=(),
        envelope=envelope.snapshot().model_dump(mode="json"),
        summary=_receipt_summary(actor, "事件"),
        chapter_number=chapter_number,
    )


def _create_edge_receipt(
    proposals: SqliteProposalStore,
    project_id: str,
    *,
    request_hash: str,
    base_canon_version: int,
    resolved_canon_version: int,
    pairs,
    selected: tuple[str, ...],
    chapter_number: int,
    actor: str,
) -> ConfirmationReceipt:
    confirmation_id = new_id(EntityType.PROPOSAL, project_id)
    envelope = build_audit_envelope(
        proposal_id=confirmation_id,
        action=ProposalAction.ACCEPT,
        status="ACCEPTED",
        canon_version=resolved_canon_version,
        kind="provisional_confirm",
        edges=pairs,
        actor=actor,
    )
    return proposals.create_confirmation_receipt(
        receipt_id=confirmation_id,
        project_id=project_id,
        fact_kind="edge",
        request_hash=request_hash,
        base_canon_version=base_canon_version,
        resolved_canon_version=resolved_canon_version,
        items=_edge_items(pairs),
        event_ids=(),
        edge_ids=selected,
        envelope=envelope.snapshot().model_dump(mode="json"),
        summary=_receipt_summary(actor, "关系"),
        chapter_number=chapter_number,
    )


def _receipt_confirmation(
    events: EventStore | None,
    evidence_store: EdgeReviewStore,
    project_id: str,
    receipt: ConfirmationReceipt,
    decision_id: str,
) -> ProvisionalConfirmation:
    payload = receipt.envelope["payload"]
    if receipt.fact_kind == "event":
        views: list[EventView] = []
        for item in payload["events"]:
            view = events.event(project_id, item["event_id"])
            if view is None:
                raise ProposalShapeError(
                    f"确认回执 {receipt.id} 的 CANON 事件不可读：{item['event_id']}"
                )
            views.append(view)
        return ProvisionalConfirmation(
            confirmation_id=receipt.id,
            project_id=project_id,
            canon_version=receipt.resolved_canon_version,
            decision_id=decision_id,
            events=tuple(views),
        )
    edge_ids = [item["edge_id"] for item in payload["edges"]]
    edges = evidence_store.hydrate_current_canon(project_id, edge_ids)
    return ProvisionalConfirmation(
        confirmation_id=receipt.id,
        project_id=project_id,
        canon_version=receipt.resolved_canon_version,
        decision_id=decision_id,
        edges=edges,
    )


def _resolve_receipt(
    conn: Connection,
    proposals: SqliteProposalStore,
    events: EventStore | None,
    evidence_store: EdgeReviewStore,
    project_id: str,
    receipt: ConfirmationReceipt,
) -> ProvisionalConfirmation:
    if receipt.decision_log_id is None:
        decision, _ = ensure_proposal_audit(conn, proposals, receipt.id)
        decision_id = decision.id
    else:
        decision_id = receipt.decision_log_id
    return _receipt_confirmation(
        events, evidence_store, project_id, receipt, decision_id
    )


def confirm_provisional_events(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    project_id: str,
    event_ids: Sequence[str],
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
    actor: str = DEFAULT_ACTOR,
) -> ProvisionalConfirmation:
    selected = _ids(event_ids, "event")
    evidence_store = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    proposals = SqliteProposalStore(conn)
    request_hash = _request_hash("event", selected)
    existing = proposals.confirmation_receipt(
        project_id, fact_kind="event", request_hash=request_hash
    )
    if existing is not None:
        return _resolve_receipt(
            conn, proposals, events, evidence_store, project_id, existing
        )
    with _transaction(conn):
        current = _version(conn, project_id, expected_canon_version)
        conflicts = proposals.confirmation_conflicts(
            project_id, fact_kind="event", fact_ids=selected
        )
        if conflicts:
            raise ConfirmationConflict(
                f"事件已确认并归属其他回执：{', '.join(conflicts)}"
            )
        _sources, evidence = _event_sources(
            events, evidence_store, project_id, selected
        )
        try:
            promoted = tuple(
                events.clone_to_scope(event_id, InformationScope.CANON)
                for event_id in selected
            )
        except EventStoreError as exc:
            raise ProposalShapeError(str(exc)) from exc
        canon_version = project.compare_and_bump_canon_version(
            conn, project_id, current
        )
        pairs = tuple(zip(promoted, evidence, strict=True))
        receipt = _create_event_receipt(
            proposals,
            project_id,
            request_hash=request_hash,
            base_canon_version=current,
            resolved_canon_version=canon_version,
            pairs=pairs,
            selected=selected,
            chapter_number=evidence[0].chapter_number,
            actor=actor,
        )
    decision, _ = ensure_proposal_audit(conn, proposals, receipt.id)
    return _receipt_confirmation(
        events, evidence_store, project_id, receipt, decision.id
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
    actor: str = DEFAULT_ACTOR,
) -> ProvisionalConfirmation:
    return confirm_provisional_events(
        conn,
        graph,
        events,
        project_id,
        [event_id],
        expected_canon_version=expected_canon_version,
        edge_review_store=edge_review_store,
        actor=actor,
    )


def confirm_provisional_edges(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    edge_ids: Sequence[str],
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
    actor: str = DEFAULT_ACTOR,
) -> ProvisionalConfirmation:
    selected = _ids(edge_ids, "edge")
    edge_store = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    proposals = SqliteProposalStore(conn)
    request_hash = _request_hash("edge", selected)
    existing = proposals.confirmation_receipt(
        project_id, fact_kind="edge", request_hash=request_hash
    )
    if existing is not None:
        return _resolve_receipt(
            conn, proposals, events=None, evidence_store=edge_store,
            project_id=project_id, receipt=existing,
        )
    with _transaction(conn):
        current = _version(conn, project_id, expected_canon_version)
        conflicts = proposals.confirmation_conflicts(
            project_id, fact_kind="edge", fact_ids=selected
        )
        if conflicts:
            raise ConfirmationConflict(
                f"关系已确认并归属其他回执：{', '.join(conflicts)}"
            )
        try:
            sources = edge_store.hydrate_provisional(project_id, selected)
            evidence = tuple(
                edge_store.evidence(project_id, item.edge.evidence_id or "")
                for item in sources
            )
            promoted = edge_store.clone_to_canon(project_id, selected)
        except EdgeReviewValidationError as exc:
            raise ProposalShapeError(str(exc)) from exc
        canon_version = project.compare_and_bump_canon_version(
            conn, project_id, current
        )
        pairs = tuple(zip(promoted, evidence, strict=True))
        receipt = _create_edge_receipt(
            proposals,
            project_id,
            request_hash=request_hash,
            base_canon_version=current,
            resolved_canon_version=canon_version,
            pairs=pairs,
            selected=selected,
            chapter_number=evidence[0].chapter_number,
            actor=actor,
        )
    decision, _ = ensure_proposal_audit(conn, proposals, receipt.id)
    return _receipt_confirmation(
        events=None, evidence_store=edge_store, project_id=project_id,
        receipt=receipt, decision_id=decision.id,
    )


def confirm_provisional_edge(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    edge_id: str,
    *,
    expected_canon_version: int,
    edge_review_store: EdgeReviewStore | None = None,
    actor: str = DEFAULT_ACTOR,
) -> ProvisionalConfirmation:
    return confirm_provisional_edges(
        conn,
        graph,
        project_id,
        [edge_id],
        expected_canon_version=expected_canon_version,
        edge_review_store=edge_review_store,
        actor=actor,
    )
