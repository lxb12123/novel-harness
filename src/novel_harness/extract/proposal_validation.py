"""Strict interpretation of open JSON proposal clusters at the review boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from ..events import EventView, ProposalRecord
from ..graph import EdgeType, Evidence
from ..graph.review_store import ReviewableEdge
from .proposal_models import (
    EdgeConflictItem,
    LowConfidenceEventItem,
    LowConfidenceStateItem,
    NewCharacterItem,
    ProposalAction,
    ProposalActionError,
    ProposalReview,
    ProposalShapeError,
    ValidatedProposal,
)


_EXPECTED_EDGE_TYPE = {
    "location": EdgeType.LOCATED_AT,
    "state": EdgeType.HAS_STATE,
    "relationship": EdgeType.RELATED_TO,
}


def _endpoints_match(
    edge: ReviewableEdge,
    subject_id: str,
    target_id: str,
) -> bool:
    if edge.edge.type is EdgeType.RELATED_TO:
        return {subject_id, target_id} == {edge.edge.src, edge.edge.dst}
    return subject_id == edge.edge.src and target_id == edge.edge.dst


def validate_proposal_shape(
    proposal: ProposalRecord,
    review: ProposalReview,
) -> ValidatedProposal:
    try:
        if proposal.kind == "low_confidence_main":
            event_items: list[LowConfidenceEventItem] = []
            edge_items: list[LowConfidenceStateItem] = []
            for raw in proposal.items:
                if not isinstance(raw, Mapping):
                    raise ProposalShapeError("low_confidence_main item 必须是 object")
                if raw.get("source_kind") == "event":
                    event_items.append(LowConfidenceEventItem.model_validate(raw))
                elif raw.get("source_kind") == "state_update":
                    edge_items.append(LowConfidenceStateItem.model_validate(raw))
                else:
                    raise ProposalShapeError("low_confidence_main source_kind 不合法")
            validated = ValidatedProposal(
                event_items=tuple(event_items), edge_items=tuple(edge_items)
            )
        elif proposal.kind == "edge_conflict":
            validated = ValidatedProposal(
                edge_items=tuple(EdgeConflictItem.model_validate(raw) for raw in proposal.items)
            )
        elif proposal.kind == "new_character":
            validated = ValidatedProposal(
                characters=tuple(NewCharacterItem.model_validate(raw) for raw in proposal.items)
            )
        else:
            raise ProposalShapeError(f"不支持的 proposal kind：{proposal.kind}")
    except ValidationError as exc:
        raise ProposalShapeError(f"proposal items 形状不合法：{exc}") from exc

    item_event_ids = tuple(item.event_id for item in validated.event_items)
    item_edge_ids = tuple(item.proposed.edge_id for item in validated.edge_items)
    if len(item_event_ids) != len(set(item_event_ids)):
        raise ProposalShapeError("proposal event items 不能重复")
    if len(item_edge_ids) != len(set(item_edge_ids)):
        raise ProposalShapeError("proposal edge items 不能重复")
    if set(item_event_ids) != set(proposal.event_ids):
        raise ProposalShapeError(
            f"proposal event items/links 不一致：{item_event_ids}/{proposal.event_ids}"
        )
    if set(item_edge_ids) != set(proposal.edge_ids):
        raise ProposalShapeError(
            f"proposal edge items/links 不一致：{item_edge_ids}/{proposal.edge_ids}"
        )
    if proposal.kind == "new_character" and (proposal.event_ids or proposal.edge_ids):
        raise ProposalShapeError("new_character proposal 不得链接 event/edge")

    if review.action is ProposalAction.EDIT and not (
        len(proposal.event_ids) == 1
        and not proposal.edge_ids
        and not validated.characters
    ):
        raise ProposalActionError("edit 只允许恰好 1 个 event，且不得含 edge/new_character")
    if review.action is ProposalAction.BYSTANDER and proposal.kind != "new_character":
        raise ProposalActionError("bystander 只允许 new_character proposal")
    if proposal.kind == "new_character" and review.action is ProposalAction.EDIT:
        raise ProposalActionError("new_character 不允许 edit")
    return validated


def validate_hydrated_facts(
    validated: ValidatedProposal,
    events: Sequence[EventView],
    event_evidence: Mapping[str, Evidence],
    edges: Sequence[ReviewableEdge],
    edge_evidence: Mapping[str, Evidence],
) -> None:
    event_by_id = {view.event.id: view for view in events}
    for item in validated.event_items:
        view = event_by_id.get(item.event_id)
        if view is None:
            raise ProposalShapeError(f"event item 没有对应 link：{item.event_id}")
        evidence = event_evidence[item.event_id]
        if item.summary != view.event.summary:
            raise ProposalShapeError(f"event {item.event_id} summary 与存储事实不一致")
        if item.confidence != view.event.confidence:
            raise ProposalShapeError(f"event {item.event_id} confidence 与存储事实不一致")
        if item.quote != evidence.audit.quote_text:
            raise ProposalShapeError(f"event {item.event_id} quote 与原文证据不一致")

    edge_by_id = {item.edge.id: item for item in edges}
    for item in validated.edge_items:
        proposed = item.proposed
        hydrated = edge_by_id.get(proposed.edge_id)
        if hydrated is None:
            raise ProposalShapeError(f"edge item 没有对应 link：{proposed.edge_id}")
        edge = hydrated.edge
        if edge.type is not _EXPECTED_EDGE_TYPE[item.update_kind]:
            raise ProposalShapeError(f"edge {edge.id} type/update_kind 不一致")
        endpoints_match = _endpoints_match(
            hydrated,
            proposed.subject_id,
            proposed.target_id,
        )
        if not endpoints_match or proposed.value != edge.props.value:
            raise ProposalShapeError(f"edge {edge.id} proposed 文本与存储事实不一致")
        if isinstance(item, LowConfidenceStateItem) and item.confidence != edge.confidence:
            raise ProposalShapeError(f"edge {edge.id} confidence 与存储事实不一致")
        if proposed.quote != edge_evidence[edge.id].audit.quote_text:
            raise ProposalShapeError(f"edge {edge.id} quote 与原文证据不一致")


def validate_current_canon_facts(
    validated: ValidatedProposal,
    proposed_edges: Sequence[ReviewableEdge],
    current_edges: Sequence[ReviewableEdge],
) -> None:
    proposed_by_id = {item.edge.id: item for item in proposed_edges}
    current_by_id = {item.edge.id: item for item in current_edges}
    for item in validated.edge_items:
        if not isinstance(item, EdgeConflictItem):
            continue
        claimed = item.current
        current = current_by_id.get(claimed.edge_id)
        proposed = proposed_by_id.get(item.proposed.edge_id)
        if current is None or proposed is None:
            raise ProposalShapeError("edge_conflict current/proposed 没有对应存储事实")
        edge = current.edge
        if edge.type is not _EXPECTED_EDGE_TYPE[item.update_kind]:
            raise ProposalShapeError(
                f"current Canon edge {edge.id} type/update_kind 不一致"
            )
        if not edge.holds_at(proposed.edge.valid_from_chapter):
            raise ProposalShapeError(
                f"current Canon edge {edge.id} 在提案章节并不成立"
            )
        if not _endpoints_match(current, claimed.subject_id, claimed.target_id):
            raise ProposalShapeError(
                f"current Canon edge {edge.id} endpoints 与提案记录不一致"
            )
        if claimed.value != edge.props.value:
            raise ProposalShapeError(
                f"current Canon edge {edge.id} value 与提案记录不一致"
            )
        if claimed.subject_id != item.proposed.subject_id:
            raise ProposalShapeError("edge_conflict current/proposed subject 不一致")
        if (
            item.update_kind != "location"
            and claimed.target_id != item.proposed.target_id
        ):
            raise ProposalShapeError("edge_conflict current/proposed target 不一致")
