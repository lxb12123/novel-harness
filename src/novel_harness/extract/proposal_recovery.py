"""Repair the narrow post-commit gap between proposal state and decision audit."""

from __future__ import annotations

from .. import decisions
from ..db import Connection
from ..events import EventStore, ProposalAlreadyResolved, ProposalNotFound, ProposalStore
from ..graph import GraphStore, InformationScope, NodeRef
from ..graph.review_store import EdgeReviewStore
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from .proposal_audit import append_audit, build_audit_envelope
from .proposal_models import (
    DecisionAuditError,
    ProposalAction,
    ProposalReview,
    ProposalShapeError,
)


def _action(status: str, kind: str, payload: dict | None) -> ProposalAction:
    if payload is not None:
        try:
            return ProposalAction(payload["action"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ProposalShapeError("existing proposal audit action 不合法") from exc
    if status == "ACCEPTED":
        return ProposalAction.ACCEPT
    if status == "EDITED":
        return ProposalAction.EDIT
    if status == "REJECTED":
        return ProposalAction.BYSTANDER if kind == "new_character" else ProposalAction.REJECT
    raise ProposalAlreadyResolved(f"proposal 仍是 {status}，没有可恢复的终态审计")


def _canon_events(events: EventStore, project_id: str, sources, status: str):
    if status not in {"ACCEPTED", "EDITED"}:
        return ()
    out = []
    for source in sources:
        candidates = events.events_for_chapter(
            project_id,
            source.event.chapter_number,
            InformationScope.CANON,
        )
        matches = [
            view
            for view in candidates
            if view.event.derived_from_event_id == source.event.id
        ]
        if len(matches) != 1:
            raise ProposalShapeError(
                f"event {source.event.id} 对应的 CANON clone 数量是 {len(matches)}"
            )
        out.append(matches[0])
    return tuple(out)


def recover_proposal_audit(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
):
    # Imported lazily to keep the public facade free of a module cycle.
    from .proposals import _audit_pairs, _hydrate_cluster, _resolution

    proposals = proposal_store or SqliteProposalStore(conn)
    edge_reviews = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    proposal = proposals.get_by_id(proposal_id)
    if proposal is None:
        raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
    if proposal.status.value == "PENDING":
        raise ProposalAlreadyResolved(f"proposal {proposal_id} 仍是 PENDING，没有审计空洞")
    matches = [
        decision
        for decision in decisions.read(
            conn, proposal.project_id, kind=decisions.DecisionKind.PROPOSAL_REVIEW
        )
        if decision.payload.get("proposal_id") == proposal.id
    ]
    if len(matches) > 1:
        raise ProposalShapeError(
            f"proposal {proposal.id} 对应 {len(matches)} 条决策日志，拒绝猜测"
        )
    existing = matches[0] if matches else None
    action = _action(
        proposal.status.value,
        proposal.kind,
        None if existing is None else existing.payload,
    )
    review = ProposalReview(
        action=action,
        expected_canon_version=proposal.base_canon_version,
        edited_summary="recovered edit" if action is ProposalAction.EDIT else None,
    )
    validated, source_events, event_evidence, source_edges, edge_evidence = _hydrate_cluster(
        proposal, review, events, edge_reviews
    )
    canon_events = _canon_events(
        events, proposal.project_id, source_events, proposal.status.value
    )
    characters: tuple[NodeRef, ...] = ()
    if proposal.status.value == "ACCEPTED" and validated.characters:
        resolved = graph.resolve(
            proposal.project_id, [candidate.surface for candidate in validated.characters]
        )
        if any(item.unique_node is None for item in resolved):
            raise ProposalShapeError("accepted new_character 的节点无法唯一恢复")
        characters = tuple(NodeRef.of(item.unique_node) for item in resolved if item.unique_node)
    canon_version = (
        int(existing.payload["canon_version"])
        if existing is not None
        else proposal.base_canon_version
        + (1 if proposal.status.value in {"ACCEPTED", "EDITED"} else 0)
    )
    if existing is None:
        audit_events = canon_events or source_events
        envelope = build_audit_envelope(
            proposal_id=proposal.id,
            action=action,
            status=proposal.status.value,
            canon_version=canon_version,
            kind=proposal.kind,
            events=_audit_pairs(audit_events, proposal.event_ids, event_evidence),
            edges=_audit_pairs(source_edges, proposal.edge_ids, edge_evidence),
            characters=tuple(
                (candidate, characters[index] if characters else None)
                for index, candidate in enumerate(validated.characters)
            ),
        )
        try:
            existing = append_audit(
                conn,
                project_id=proposal.project_id,
                action=action,
                envelope=envelope,
            )
        except Exception as exc:
            raise DecisionAuditError(
                f"proposal {proposal.id} 审计恢复写入失败",
                proposal_id=proposal.id,
                canon_version=canon_version,
                fact_ids=tuple((*proposal.event_ids, *proposal.edge_ids)),
            ) from exc
    try:
        proposals.attach_decision(proposal.id, existing.id)
    except Exception as exc:
        raise DecisionAuditError(
            f"proposal {proposal.id} 审计恢复附加失败",
            proposal_id=proposal.id,
            canon_version=canon_version,
            decision_id=existing.id,
            fact_ids=tuple((*proposal.event_ids, *proposal.edge_ids)),
        ) from exc
    return _resolution(
        proposal,
        status=proposal.status.value,
        canon_version=canon_version,
        decision_id=existing.id,
        events=canon_events,
        edges=(),
        characters=characters,
    )

