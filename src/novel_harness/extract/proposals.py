"""Atomic M4 author review, passive confirmation, and post-commit audit recovery."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import Iterator

from .. import project
from ..db import Connection
from ..events import (
    EventStore,
    EventStoreError,
    EventView,
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalRecord,
    ProposalResolutionMark,
    ProposalStore,
)
from ..graph import (
    EdgeStatus,
    Evidence,
    EvidenceStatus,
    GraphStore,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
)
from ..graph.review_store import (
    EdgeReviewStore,
    EdgeReviewValidationError,
    ReviewableEdge,
)
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from .proposal_audit import append_audit, build_audit_envelope
from .proposal_models import (
    DecisionAuditError,
    NewCharacterItem,
    ProposalAction,
    ProposalActionError,
    ProposalResolution,
    ProposalReview,
    ProposalReviewError,
    ProposalShapeError,
    ProvisionalConfirmation,
    ValidatedProposal,
)
from .proposal_validation import validate_hydrated_facts, validate_proposal_shape

__all__ = [
    "DecisionAuditError",
    "ProposalAction",
    "ProposalActionError",
    "ProposalResolution",
    "ProposalReview",
    "ProposalReviewError",
    "ProposalShapeError",
    "ProvisionalConfirmation",
    "confirm_provisional_edge",
    "confirm_provisional_edges",
    "confirm_provisional_event",
    "confirm_provisional_events",
    "recover_proposal_audit",
    "review_proposal",
]


@contextmanager
def _business_transaction(conn: Connection) -> Iterator[None]:
    if conn.in_transaction:
        raise RuntimeError("author review 必须在没有外层事务的连接上启动")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _hydrate_events(
    event_store: EventStore,
    evidence_store: EdgeReviewStore,
    project_id: str,
    event_ids: Sequence[str],
) -> tuple[tuple[EventView, ...], dict[str, Evidence]]:
    views: list[EventView] = []
    evidence: dict[str, Evidence] = {}
    for event_id in event_ids:
        view = event_store.event(project_id, event_id)
        if view is None:
            raise ProposalShapeError(
                f"event {event_id} 不存在、跨项目或当前不可审阅"
            )
        if (
            view.event.information_scope is not InformationScope.PROVISIONAL
            or view.event.status is not EdgeStatus.ACTIVE
            or view.event.evidence_status is not EvidenceStatus.FRESH
        ):
            raise ProposalShapeError(f"event {event_id} 必须是 ACTIVE/FRESH PROVISIONAL")
        views.append(view)
        evidence[event_id] = evidence_store.evidence(project_id, view.event.evidence_id)
    return tuple(views), evidence


def _hydrate_cluster(
    proposal: ProposalRecord,
    review: ProposalReview,
    event_store: EventStore,
    edge_store: EdgeReviewStore,
) -> tuple[
    ValidatedProposal,
    tuple[EventView, ...],
    dict[str, Evidence],
    tuple[ReviewableEdge, ...],
    dict[str, Evidence],
]:
    validated = validate_proposal_shape(proposal, review)
    events, event_evidence = _hydrate_events(
        event_store, edge_store, proposal.project_id, proposal.event_ids
    )
    try:
        edges = (
            edge_store.hydrate_provisional(proposal.project_id, proposal.edge_ids)
            if proposal.edge_ids
            else ()
        )
    except EdgeReviewValidationError as exc:
        raise ProposalShapeError(str(exc)) from exc
    edge_evidence = {
        item.edge.id: edge_store.evidence(
            proposal.project_id, item.edge.evidence_id or ""
        )
        for item in edges
    }
    validate_hydrated_facts(
        validated,
        events,
        event_evidence,
        edges,
        edge_evidence,
    )
    return validated, events, event_evidence, edges, edge_evidence


def _create_characters(
    graph: GraphStore,
    project_id: str,
    candidates: Sequence[NewCharacterItem],
) -> tuple[NodeRef, ...]:
    surfaces = [candidate.surface for candidate in candidates]
    if len(surfaces) != len(set(surfaces)):
        raise ProposalShapeError("new_character cluster 里 surface 不能重复")
    resolutions = graph.resolve(project_id, surfaces)
    if len(resolutions) != len(surfaces):
        raise ProposalShapeError("resolve 没有与 new_character surfaces 一一对应")
    if any(resolution.unique_node is not None or resolution.ambiguous for resolution in resolutions):
        raise ProposalShapeError("new_character 在锁内必须仍然 unknown，不允许合并或猜测")
    created: list[NodeRef] = []
    for candidate in candidates:
        profile = candidate.profile
        node = graph.upsert_node(
            NodeSpec(
                project_id=project_id,
                label=NodeLabel.CHARACTER,
                name=candidate.surface,
                props=NodeProps(
                    gender=profile.gender,
                    personality=profile.personality,
                    background=profile.background,
                    character_notes=profile.character_notes,
                ),
            )
        )
        created.append(NodeRef.of(node))
    return tuple(created)


def _clone_events(
    event_store: EventStore,
    event_ids: Sequence[str],
    *,
    edited_summary: str | None = None,
) -> tuple[EventView, ...]:
    out: list[EventView] = []
    try:
        for event_id in event_ids:
            out.append(
                event_store.clone_to_scope(
                    event_id,
                    InformationScope.CANON,
                    summary=edited_summary,
                )
            )
    except EventStoreError as exc:
        raise ProposalShapeError(str(exc)) from exc
    return tuple(out)


def _audit_pairs(
    facts: Sequence,
    source_ids: Sequence[str],
    evidence_by_source: dict[str, Evidence],
):
    return tuple(
        (fact, evidence_by_source[source_id])
        for fact, source_id in zip(facts, source_ids, strict=True)
    )


def _resolution(
    proposal: ProposalRecord,
    *,
    status: str,
    canon_version: int,
    decision_id: str,
    events: tuple[EventView, ...],
    edges: tuple[ReviewableEdge, ...],
    characters: tuple[NodeRef, ...],
) -> ProposalResolution:
    return ProposalResolution(
        proposal_id=proposal.id,
        status=status,
        canon_version=canon_version,
        decision_id=decision_id,
        event=events[0] if len(events) == 1 else None,
        character=characters[0] if len(characters) == 1 else None,
        events=events,
        edges=edges,
        characters=characters,
    )


def review_proposal(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    review: ProposalReview,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProposalResolution:
    proposals = proposal_store or SqliteProposalStore(conn)
    edge_reviews = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    with _business_transaction(conn):
        proposal = proposals.get_by_id(proposal_id)
        if proposal is None:
            raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
        if proposal.status.value != "PENDING":
            raise ProposalAlreadyResolved(
                f"proposal {proposal_id} 已是 {proposal.status.value}，不能再次处理"
            )
        current = project.require_canon_version(conn, proposal.project_id)
        if proposal.base_canon_version != current:
            raise project.StaleBaseVersion(
                proposal.project_id,
                expected=proposal.base_canon_version,
                current=current,
            )
        if review.expected_canon_version != current:
            raise project.StaleBaseVersion(
                proposal.project_id,
                expected=review.expected_canon_version,
                current=current,
            )
        validated, source_events, event_evidence, source_edges, edge_evidence = (
            _hydrate_cluster(proposal, review, events, edge_reviews)
        )
        canon_events: tuple[EventView, ...] = ()
        canon_edges: tuple[ReviewableEdge, ...] = ()
        characters: tuple[NodeRef, ...] = ()
        if review.action in {ProposalAction.ACCEPT, ProposalAction.EDIT}:
            canon_events = _clone_events(
                events,
                proposal.event_ids,
                edited_summary=review.edited_summary,
            )
            if proposal.edge_ids:
                try:
                    canon_edges = edge_reviews.clone_to_canon(
                        proposal.project_id, proposal.edge_ids
                    )
                except EdgeReviewValidationError as exc:
                    raise ProposalShapeError(str(exc)) from exc
            if validated.characters:
                characters = _create_characters(
                    graph, proposal.project_id, validated.characters
                )
        status = {
            ProposalAction.ACCEPT: "ACCEPTED",
            ProposalAction.EDIT: "EDITED",
            ProposalAction.REJECT: "REJECTED",
            ProposalAction.BYSTANDER: "REJECTED",
        }[review.action]
        proposals.mark_resolved(
            proposal.id,
            ProposalResolutionMark(status=status),
        )
        if review.action in {ProposalAction.ACCEPT, ProposalAction.EDIT}:
            canon_version = project.compare_and_bump_canon_version(
                conn, proposal.project_id, current
            )
        else:
            canon_version = current

    audit_events = canon_events or source_events
    audit_edges = canon_edges or source_edges
    envelope = build_audit_envelope(
        proposal_id=proposal.id,
        action=review.action,
        status=status,
        canon_version=canon_version,
        kind=proposal.kind,
        events=_audit_pairs(audit_events, proposal.event_ids, event_evidence),
        edges=_audit_pairs(audit_edges, proposal.edge_ids, edge_evidence),
        characters=tuple(
            (candidate, characters[index] if characters else None)
            for index, candidate in enumerate(validated.characters)
        ),
    )
    fact_ids = (*proposal.event_ids, *proposal.edge_ids)
    try:
        decision = append_audit(
            conn,
            project_id=proposal.project_id,
            action=review.action,
            envelope=envelope,
        )
    except Exception as exc:
        raise DecisionAuditError(
            f"proposal {proposal.id} 业务已提交，但决策日志写入失败",
            proposal_id=proposal.id,
            canon_version=canon_version,
            fact_ids=tuple(fact_ids),
        ) from exc
    try:
        proposals.attach_decision(proposal.id, decision.id)
    except Exception as exc:
        raise DecisionAuditError(
            f"proposal {proposal.id} 业务与日志已提交，但审计附加失败",
            proposal_id=proposal.id,
            canon_version=canon_version,
            decision_id=decision.id,
            fact_ids=tuple(fact_ids),
        ) from exc
    return _resolution(
        proposal,
        status=status,
        canon_version=canon_version,
        decision_id=decision.id,
        events=canon_events,
        edges=canon_edges,
        characters=characters,
    )


from .proposal_confirm import (  # noqa: E402 — public facade after review implementation
    confirm_provisional_edge,
    confirm_provisional_edges,
    confirm_provisional_event,
    confirm_provisional_events,
)


def recover_proposal_audit(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProposalResolution:
    """Lazily load recovery so it can reuse the private, already-initialized review helpers."""
    from .proposal_recovery import recover_proposal_audit as recover

    return recover(
        conn,
        graph,
        events,
        proposal_id,
        proposal_store=proposal_store,
        edge_review_store=edge_review_store,
    )
