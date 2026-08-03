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
    model_config = _STRICT

    action: ProposalAction
    expected_canon_version: StrictVersion
    edited_summary: str | None = None

    @model_validator(mode="after")
    def _edit_shape(self) -> Self:
        if self.action is ProposalAction.EDIT:
            if self.edited_summary is None or not self.edited_summary.strip():
                raise ValueError("edit 必须提供非空 edited_summary")
        elif self.edited_summary is not None:
            raise ValueError("只有 edit 允许 edited_summary")
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

