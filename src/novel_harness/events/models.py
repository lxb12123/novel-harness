"""M4 event-memory domain models.

Events are independent hyperedges. They deliberately are not graph ``Node`` objects and do not
extend the frozen ``NodeLabel`` / ``EdgeType`` schema.
"""

from __future__ import annotations

from enum import StrEnum
import json

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, model_validator

from ..graph.models import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeRef,
)


class EventCharacterRole(StrEnum):
    PARTICIPANT = "participant"
    KNOWER = "knower"


class ProposalStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"


class ProposalResolutionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"


class StoryEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    chapter_number: int = Field(ge=1)
    summary: str = Field(min_length=1)
    information_scope: InformationScope
    status: EdgeStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: EdgeSource
    evidence_id: str
    evidence_status: EvidenceStatus
    derived_from_event_id: str | None = None


class EventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: StoryEvent
    participants: list[NodeRef] = Field(default_factory=list)
    knowers: list[NodeRef] = Field(default_factory=list)
    revealed_facts: list[NodeRef] = Field(default_factory=list)


class ProvisionalEventSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    summary: str = Field(min_length=1)
    evidence_id: str
    participant_ids: list[str] = Field(default_factory=list)
    knower_ids: list[str] = Field(default_factory=list)
    revealed_fact_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ProposalCreate(BaseModel):
    """待确认聚类的公开输入；存储序列化由仓储实现负责。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    kind: str = Field(min_length=1)
    summary: str = ""
    items: list[JsonValue] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    chapter_number: int | None = Field(default=None, ge=1)
    snapshot_id: str | None = None
    base_canon_version: int = Field(default=0, ge=0)
    schema_version: str | None = None
    prompt_hash: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _items_are_strict_utf8_json(self) -> ProposalCreate:
        try:
            json.dumps(self.items, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("items must be strict UTF-8 JSON") from exc
        return self

    @computed_field
    @property
    def item_count(self) -> int:
        return len(self.items)


class ProposalRecord(ProposalCreate):
    """``proposal_set`` 及其关联表读出的不可变记录。"""

    id: str
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: str
    resolved_at: str | None = None
    decision_log_id: str | None = None


class ProposalResolutionMark(BaseModel):
    """仓储层完成一次提案状态落盘所需的最小字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ProposalResolutionStatus
    decision_log_id: str | None = None


class CharacterProfilePatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    main_character: bool | None = None


class CharacterProfileView(BaseModel):
    """Narrow character profile returned by ``EventStore``; arbitrary ``NodeProps`` stay inside."""

    model_config = ConfigDict(frozen=True)

    character: NodeRef
    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    main_character: bool | None = None
