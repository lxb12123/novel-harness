"""Immutable request, response, and stored-item shapes for proposal review."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..events import EventView
from ..graph import NodeRef
from ..graph.review_store import ReviewableEdge
from .models import RawCharacterProfile


_STRICT = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
StrictVersion = Annotated[int, Field(strict=True, ge=0)]


class ProposalAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    BYSTANDER = "bystander"


class ProposalReview(BaseModel):
    """审阅队列里作者对一个聚类的裁决。

    ── `edit` 能改三样，不是一样 ─────────────────────────────────────────────

    只能改 `edited_summary` 的时候，作者面对一条 knowers 抽错了的事件只有两个选择：
    整条 reject（丢掉一条真实存在的事实，再自己重新声明一遍），或者 accept 一条错的。
    **而 `knowers` 恰好是抽取里唯一靠推断得来的那一维**（谁在场是文本里写着的，
    谁因此知道了是猜的），也就是最需要改的那一维。

    队列这条路和「改一条已经生效的事实」（`corrections.py`）能力必须一致：
    两条路能力不一致的时候，作者会学会先 reject 再重来，而那正好丢掉了证据链。

    `edited_*_ids` 收的是**绝对集合**（改完之后是这些人），不是增删列表——同
    `EventCastStore.edit_cast`，重发一次是空操作。`None` = 这一维不动。
    """

    model_config = _STRICT

    action: ProposalAction
    expected_canon_version: StrictVersion
    edited_summary: str | None = None
    edited_knower_ids: tuple[str, ...] | None = None
    edited_participant_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _edit_shape(self) -> Self:
        edits = (self.edited_summary, self.edited_knower_ids, self.edited_participant_ids)
        if self.action is ProposalAction.EDIT:
            if all(value is None for value in edits):
                raise ValueError(
                    "edit 必须至少改一样：edited_summary / edited_knower_ids / "
                    "edited_participant_ids"
                )
            if self.edited_summary is not None and not self.edited_summary.strip():
                raise ValueError("edited_summary 给了就不能是空白")
        elif any(value is not None for value in edits):
            raise ValueError("只有 edit 允许 edited_summary / edited_*_ids")
        for ids in (self.edited_knower_ids, self.edited_participant_ids):
            if ids is None:
                continue
            if any(not node_id for node_id in ids):
                raise ValueError("名单里的 id 不能为空")
            if len(ids) != len(set(ids)):
                raise ValueError("名单里的 id 不能重复")
        return self


class ProposalResolution(BaseModel):
    model_config = _STRICT

    proposal_id: str
    status: Literal["ACCEPTED", "REJECTED", "EDITED"]
    canon_version: StrictVersion
    decision_id: str
    event: EventView | None = None
    character: NodeRef | None = None
    events: tuple[EventView, ...] = ()
    edges: tuple[ReviewableEdge, ...] = ()
    characters: tuple[NodeRef, ...] = ()


class ProvisionalConfirmation(BaseModel):
    model_config = _STRICT

    confirmation_id: str
    project_id: str
    canon_version: StrictVersion
    decision_id: str
    events: tuple[EventView, ...] = ()
    edges: tuple[ReviewableEdge, ...] = ()


class ProposalReviewError(Exception):
    """Base class for author-review orchestration failures."""


class ProposalShapeError(ProposalReviewError, ValueError):
    """Stored proposal items and links do not form a known review shape."""


class ProposalActionError(ProposalShapeError):
    """An action is not legal for the proposal's validated shape."""


class ConfirmationConflict(ProposalReviewError):
    """A passive fact already belongs to another durable confirmation receipt."""


class DecisionAuditError(ProposalReviewError):
    """Business state committed, but its append-only audit is missing or unattached."""

    def __init__(
        self,
        message: str,
        *,
        proposal_id: str | None,
        canon_version: int,
        decision_id: str | None = None,
        fact_ids: tuple[str, ...] = (),
    ) -> None:
        self.proposal_id = proposal_id
        self.canon_version = canon_version
        self.decision_id = decision_id
        self.fact_ids = fact_ids
        super().__init__(message)


class ProposedEdgeItem(BaseModel):
    model_config = _STRICT

    edge_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    value: str | None = None
    quote: str = Field(min_length=1)


class CurrentEdgeItem(BaseModel):
    model_config = _STRICT

    edge_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    value: str | None = None


class LowConfidenceEventItem(BaseModel):
    model_config = _STRICT

    source_kind: Literal["event"]
    event_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    quote: str = Field(min_length=1)


class LowConfidenceStateItem(BaseModel):
    model_config = _STRICT

    source_kind: Literal["state_update"]
    update_kind: Literal["location", "state", "relationship"]
    confidence: float = Field(ge=0, le=1)
    proposed: ProposedEdgeItem


class EdgeConflictItem(BaseModel):
    model_config = _STRICT

    update_kind: Literal["location", "state", "relationship"]
    current: CurrentEdgeItem
    proposed: ProposedEdgeItem


class NewCharacterItem(BaseModel):
    model_config = _STRICT

    surface: str = Field(min_length=2)
    profile: RawCharacterProfile
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _outer_matches_profile(self) -> Self:
        if self.surface != self.profile.surface:
            raise ValueError("new_character 外层 surface 必须与 profile.surface 相同")
        if self.confidence != self.profile.confidence:
            raise ValueError("new_character 外层 confidence 必须与 profile.confidence 相同")
        return self


class ValidatedProposal(BaseModel):
    """Internal deeply immutable interpretation of one stored cluster."""

    model_config = _STRICT

    event_items: tuple[LowConfidenceEventItem, ...] = ()
    edge_items: tuple[LowConfidenceStateItem | EdgeConflictItem, ...] = ()
    characters: tuple[NewCharacterItem, ...] = ()
