"""M4 event-memory domain models.

Events are independent hyperedges. They deliberately are not graph ``Node`` objects and do not
extend the frozen ``NodeLabel`` / ``EdgeType`` schema.
"""

from __future__ import annotations

from enum import StrEnum
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, model_validator

from ..decisions import quote_hash
from ..graph.models import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeRef,
)
from ..json_contract import strict_json_dumps


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


class ProposalResolutionAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    BYSTANDER = "bystander"


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


class EventCastEdit(BaseModel):
    """作者改完一条已生效事件的在场/知情名单之后，**改成了什么**。

    出参是 `NodeRef` 不是 `Node`（见 `graph.models.NodeRef` 的实测泄漏形态）：
    这份东西会整份进 `decision_log.payload_json`，而 `NodeProps` 是 `extra="allow"` 的。

    `event` 是改完之后重新读出来的那一份，不是调用方拼的——「我以为我改成了什么」
    和「库里现在是什么」必须由同一次读回答。
    """

    model_config = ConfigDict(frozen=True)

    event: EventView
    knowers_added: tuple[NodeRef, ...] = ()
    knowers_removed: tuple[NodeRef, ...] = ()
    participants_added: tuple[NodeRef, ...] = ()
    participants_removed: tuple[NodeRef, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(
            self.knowers_added
            or self.knowers_removed
            or self.participants_added
            or self.participants_removed
        )


class EventSummaryVersion(BaseModel):
    """一条事件摘要版本（019 / Task 7）。

    `story_event.summary` 保留为迁移基线，生产读路径全部改读 effective head——
    版本历史里每一行都真的生效过（ADR 0030）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    event_id: str
    source_snapshot_id: str | None = None
    evidence_sha256: str | None = None
    summary: str
    summary_sha256: str
    source: Literal["model", "author", "legacy"] = "model"
    status: Literal["ACTIVE", "RETRACTED"] = "ACTIVE"
    replaces_version_id: str | None = None
    created_at: str


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


class ProposalAuditSnapshot(BaseModel):
    """Exact append-only decision material committed with the business result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload: dict[str, JsonValue]
    subject_name: str | None = None
    quote_text: str | None = None
    quote_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    chapter_number: int | None = Field(default=None, ge=1)
    para_index: int | None = Field(default=None, ge=0)
    # actor 不在这一层，它在 `payload["actor"]` 里 —— 003 的
    # `proposal_resolution_metadata_*` 触发器把本模型的**顶层键集合**逐字写死了
    # （payload / subject_name / quote_text / quote_sha256 / chapter_number / para_index），
    # 多一个顶层键就是 `RAISE(ABORT)`。payload 内部没有这条白名单。

    @model_validator(mode="after")
    def _quote_hash_matches_text(self) -> ProposalAuditSnapshot:
        try:
            strict_json_dumps(self.model_dump(mode="json"))
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise ValueError(
                "proposal audit snapshot must be finite int64 strict UTF-8 JSON"
            ) from exc
        if self.quote_text is None:
            if self.quote_sha256 is not None:
                raise ValueError("quote_sha256 requires quote_text")
            return self
        try:
            expected = quote_hash(self.quote_text)
        except UnicodeError as exc:
            raise ValueError("quote_text must be valid UTF-8") from exc
        if self.quote_sha256 != expected:
            raise ValueError("quote_sha256 must exactly match quote_text")
        return self


class ProposalRecord(ProposalCreate):
    """``proposal_set`` 及其关联表读出的不可变记录。"""

    id: str
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: str
    resolved_at: str | None = None
    decision_log_id: str | None = None
    resolution_action: ProposalResolutionAction | None = None
    resolved_canon_version: int | None = Field(default=None, ge=0)
    audit_envelope: ProposalAuditSnapshot | None = None

    currentness: Literal["CURRENT", "OBSOLETE"] = "CURRENT"
    """正文时效（021 / Task 9）：OBSOLETE 表示「它锚的那版正文已经不是当前了」。

    `status` 仍只表达作者裁决（PENDING/ACCEPTED/…）；OBSOLETE 的 PENDING 提案
    不出现在当前待确认列表、直接审阅返回 409，但历史查询仍读得到——它不伪造
    ACCEPTED/REJECTED resolution metadata。"""

    superseded_by_snapshot_id: str | None = None
    """把这一条顶成 OBSOLETE 的那一版正文快照。"""

    node_refs: tuple[NodeRef, ...] = ()
    """`items` 里那些**裸 id** 对应的显示名（id / label / name）。

    ── 它为什么必须存在 ─────────────────────────────────────────────────
    `items` 是开放 JSON，里面装的是 `subject_id` / `target_id` 这种引擎内部主键。
    在它存在之前，界面被迫自己拿 id 去花名册里查名字，查不到就**把 id 截断了摆上屏**
    （`n:ID22`，`"location:ID22".slice(-6)`）。

    **失败的机制是两次查询的时间差，不是「花名册只收人物」**（它走
    `resolve(pid, None)`，而 `upsert_node` 每建一个节点都写一条 canonical 别名，
    所以什么 label 都在里面）：花名册和提案队列在浏览器里是两条独立缓存，
    后台抽取和自动升 CANON 会造出新节点，而没有任何一条路径保证前者在后者之后重取过。
    差一拍，屏幕上就是一串截断的内部编号。

    **出参自足**让这一整类失败在结构上不存在：名字和 id 在同一个响应里。
    ARCHITECTURE §10.3 的原话是「闸门只出 `NodeRef`（id/label/name），不出 `Node`」。

    **不是 `Node`**：这些 id 里可能有 Secret，而 `Node.props` 装的正是秘密的内容
    （见 `graph.models.NodeRef` 的两种实测泄漏形态）。

    存储层不填它（它不落库，`items_json` 一个字节没变）：由读端在出接口前补上。
    认不出来的 id **不在这里出现**（不编一个假名字），界面那边说「—」。
    """


class ProposalResolutionMark(BaseModel):
    """Business result plus the durable audit outbox committed in the same transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ProposalResolutionStatus
    action: ProposalResolutionAction
    canon_version: int = Field(ge=0)
    audit_envelope: ProposalAuditSnapshot
    decision_log_id: str | None = None

    @model_validator(mode="after")
    def _status_matches_action(self) -> ProposalResolutionMark:
        expected = {
            ProposalResolutionAction.ACCEPT: ProposalResolutionStatus.ACCEPTED,
            ProposalResolutionAction.EDIT: ProposalResolutionStatus.EDITED,
            ProposalResolutionAction.REJECT: ProposalResolutionStatus.REJECTED,
            ProposalResolutionAction.BYSTANDER: ProposalResolutionStatus.REJECTED,
        }[self.action]
        if self.status is not expected:
            raise ValueError(
                f"proposal status {self.status.value} 与 action {self.action.value} 不一致"
            )
        return self


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
