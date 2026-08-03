"""Text-first append-only audit material for M4 review decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .. import decisions
from ..db import Connection
from ..events import (
    EventView,
    ProposalAlreadyResolved,
    ProposalAuditSnapshot,
    ProposalNotFound,
    ProposalRecord,
    ProposalStore,
)
from ..graph import Evidence, NodeRef
from ..graph.review_store import ReviewableEdge
from .proposal_models import (
    DecisionAuditError,
    NewCharacterItem,
    ProposalAction,
    ProposalShapeError,
)


@dataclass(frozen=True)
class AuditEnvelope:
    payload: dict[str, Any]
    subject_name: str | None
    quote_text: str | None
    quote_sha256: str | None
    chapter_number: int | None
    para_index: int | None

    def snapshot(self) -> ProposalAuditSnapshot:
        return ProposalAuditSnapshot(
            payload=self.payload,
            subject_name=self.subject_name,
            quote_text=self.quote_text,
            quote_sha256=self.quote_sha256,
            chapter_number=self.chapter_number,
            para_index=self.para_index,
        )

    @classmethod
    def from_snapshot(cls, snapshot: ProposalAuditSnapshot) -> AuditEnvelope:
        return cls(**snapshot.model_dump(mode="python"))


def _evidence_payload(evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.id,
        "quote": evidence.audit.quote_text,
        "quote_sha256": evidence.audit.quote_sha256,
        "chapter_number": evidence.chapter_number,
        "para_index": evidence.audit.para_index,
        "occurrence_k": evidence.relocate.occurrence_k,
    }


def _event_payload(view: EventView, evidence: Evidence) -> dict[str, Any]:
    return {
        "event_id": view.event.id,
        "derived_from_event_id": view.event.derived_from_event_id,
        "summary": view.event.summary,
        "participants": [node.name for node in view.participants],
        "knowers": [node.name for node in view.knowers],
        "revealed_facts": [node.name for node in view.revealed_facts],
        "evidence": _evidence_payload(evidence),
    }


def _edge_payload(item: ReviewableEdge, evidence: Evidence) -> dict[str, Any]:
    return {
        "edge_id": item.edge.id,
        "type": item.edge.type.value,
        "src_name": item.src.name,
        "dst_name": item.dst.name,
        "value": item.edge.props.value,
        "valid_from_chapter": item.edge.valid_from_chapter,
        "evidence": _evidence_payload(evidence),
    }


def _character_payload(
    candidate: NewCharacterItem,
    character: NodeRef | None,
) -> dict[str, Any]:
    return {
        "character_id": None if character is None else character.id,
        "name": candidate.surface,
        "profile": candidate.profile.model_dump(mode="json"),
        "evidence": None,
    }


def build_audit_envelope(
    *,
    proposal_id: str | None,
    action: ProposalAction,
    status: str,
    canon_version: int,
    kind: str,
    events: Sequence[tuple[EventView, Evidence]] = (),
    edges: Sequence[tuple[ReviewableEdge, Evidence]] = (),
    characters: Sequence[tuple[NewCharacterItem, NodeRef | None]] = (),
) -> AuditEnvelope:
    event_payloads = [_event_payload(view, evidence) for view, evidence in events]
    edge_payloads = [_edge_payload(item, evidence) for item, evidence in edges]
    character_payloads = [
        _character_payload(candidate, character) for candidate, character in characters
    ]
    payload: dict[str, Any] = {
        "proposal_id": proposal_id,
        "action": action.value,
        "status": status,
        "canon_version": canon_version,
        "kind": kind,
        "events": event_payloads,
        "edges": edge_payloads,
        "characters": character_payloads,
    }
    if events:
        first_view, first_evidence = events[0]
        subject_name = first_view.participants[0].name if first_view.participants else None
    elif edges:
        first_edge, first_evidence = edges[0]
        subject_name = first_edge.src.name
    else:
        first_evidence = None
        subject_name = None
    quote_text = None if first_evidence is None else first_evidence.audit.quote_text
    return AuditEnvelope(
        payload=payload,
        subject_name=subject_name,
        quote_text=quote_text,
        quote_sha256=None if quote_text is None else decisions.quote_hash(quote_text),
        chapter_number=None if first_evidence is None else first_evidence.chapter_number,
        para_index=None if first_evidence is None else first_evidence.audit.para_index,
    )


def append_audit(
    conn: Connection,
    *,
    project_id: str,
    action: ProposalAction,
    envelope: AuditEnvelope,
) -> decisions.Decision:
    verdict = {
        ProposalAction.ACCEPT: decisions.Verdict.ACCEPT,
        ProposalAction.EDIT: decisions.Verdict.EDIT,
        ProposalAction.REJECT: decisions.Verdict.REJECT,
        ProposalAction.BYSTANDER: decisions.Verdict.REJECT,
    }[action]
    return decisions.append(
        conn,
        project_id=project_id,
        kind=decisions.DecisionKind.PROPOSAL_REVIEW,
        decision=verdict,
        payload=envelope.payload,
        subject_name=envelope.subject_name,
        quote_text=envelope.quote_text,
        chapter_number=envelope.chapter_number,
        para_index=envelope.para_index,
    )


def _proposal_decisions(
    conn: Connection,
    proposal: ProposalRecord,
) -> list[decisions.Decision]:
    return [
        decision
        for decision in decisions.read(
            conn,
            proposal.project_id,
            kind=decisions.DecisionKind.PROPOSAL_REVIEW,
        )
        if decision.payload.get("proposal_id") == proposal.id
    ]


def _validate_decision(
    proposal: ProposalRecord,
    decision: decisions.Decision,
) -> None:
    snapshot = proposal.audit_envelope
    action = proposal.resolution_action
    if snapshot is None or action is None or proposal.resolved_canon_version is None:
        raise ProposalShapeError(
            f"proposal {proposal.id} 缺少 durable audit metadata；拒绝推断历史动作"
        )
    expected_verdict = {
        "accept": decisions.Verdict.ACCEPT,
        "edit": decisions.Verdict.EDIT,
        "reject": decisions.Verdict.REJECT,
        "bystander": decisions.Verdict.REJECT,
    }[action.value]
    actual = {
        "payload": decision.payload,
        "subject_name": decision.subject_name,
        "quote_text": decision.quote_text,
        "quote_sha256": decision.quote_sha256,
        "chapter_number": decision.chapter_number,
        "para_index": decision.para_index,
    }
    if (
        decision.kind != decisions.DecisionKind.PROPOSAL_REVIEW
        or decision.decision is not expected_verdict
        or actual != snapshot.model_dump(mode="python")
    ):
        raise ProposalShapeError(
            f"decision {decision.id} 与 proposal {proposal.id} 的 durable audit 不一致"
        )


def ensure_proposal_audit(
    conn: Connection,
    proposals: ProposalStore,
    proposal_id: str,
) -> tuple[decisions.Decision, ProposalRecord]:
    """Append/attach exactly one matching immutable decision for a terminal proposal."""
    if conn.in_transaction:
        raise RuntimeError("proposal audit 必须在没有外层事务的连接上执行")
    proposal = proposals.get_by_id(proposal_id)
    if proposal is None:
        raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
    if proposal.status.value == "PENDING":
        raise ProposalAlreadyResolved(f"proposal {proposal_id} 仍是 PENDING，没有审计空洞")
    if (
        proposal.audit_envelope is None
        or proposal.resolution_action is None
        or proposal.resolved_canon_version is None
    ):
        raise ProposalShapeError(
            f"proposal {proposal.id} 缺少 durable audit metadata；拒绝推断历史动作"
        )

    if proposal.decision_log_id is not None:
        attached = next(
            (
                decision
                for decision in decisions.read(conn, proposal.project_id)
                if decision.id == proposal.decision_log_id
            ),
            None,
        )
        if attached is None:
            raise ProposalShapeError(
                f"proposal {proposal.id} 附加的 decision {proposal.decision_log_id} 不存在"
            )
        _validate_decision(proposal, attached)
        return attached, proposal

    matches = _proposal_decisions(conn, proposal)
    if len(matches) > 1:
        raise ProposalShapeError(
            f"proposal {proposal.id} 对应 {len(matches)} 条决策日志，拒绝猜测"
        )
    decision = matches[0] if matches else None
    if decision is None:
        action = ProposalAction(proposal.resolution_action.value)
        envelope = AuditEnvelope.from_snapshot(proposal.audit_envelope)
        try:
            decision = append_audit(
                conn,
                project_id=proposal.project_id,
                action=action,
                envelope=envelope,
            )
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            matches = _proposal_decisions(conn, proposal)
            if len(matches) > 1:
                raise ProposalShapeError(
                    f"proposal {proposal.id} 对应 {len(matches)} 条决策日志，拒绝猜测"
                ) from exc
            if not matches:
                raise DecisionAuditError(
                    f"proposal {proposal.id} 业务已提交，但决策日志写入失败",
                    proposal_id=proposal.id,
                    canon_version=proposal.resolved_canon_version,
                    fact_ids=tuple((*proposal.event_ids, *proposal.edge_ids)),
                ) from exc
            decision = matches[0]
    _validate_decision(proposal, decision)
    try:
        attached = proposals.attach_decision(proposal.id, decision.id)
    except Exception as exc:
        raise DecisionAuditError(
            f"proposal {proposal.id} 业务与日志已提交，但审计附加失败",
            proposal_id=proposal.id,
            canon_version=proposal.resolved_canon_version,
            decision_id=decision.id,
            fact_ids=tuple((*proposal.event_ids, *proposal.edge_ids)),
        ) from exc
    return decision, attached
