"""Text-first append-only audit material for M4 review decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .. import decisions
from ..db import Connection
from ..events import EventView
from ..graph import Evidence, NodeRef
from ..graph.review_store import ReviewableEdge
from .proposal_models import NewCharacterItem, ProposalAction


@dataclass(frozen=True)
class AuditEnvelope:
    payload: dict[str, Any]
    subject_name: str | None
    quote_text: str | None
    chapter_number: int | None
    para_index: int | None


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
    return AuditEnvelope(
        payload=payload,
        subject_name=subject_name,
        quote_text=None if first_evidence is None else first_evidence.audit.quote_text,
        chapter_number=None if first_evidence is None else first_evidence.chapter_number,
        para_index=None if first_evidence is None else first_evidence.audit.para_index,
    )


def append_audit(
    conn: Connection,
    *,
    project_id: str,
    action: ProposalAction,
    envelope: AuditEnvelope,
):
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

