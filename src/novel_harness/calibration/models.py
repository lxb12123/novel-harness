"""写前校准的**全部** Pydantic 契约（frozen + extra="forbid"）。

规格书：`docs_dev/2026-08-17-模式二写前校准与写作执行简报任务.md` §6。
字段约束照抄那份文档；类名不必照抄，但**形状必须一致**。

── 这里的每一个类型都是一道闸 ─────────────────────────────────────────

- `SceneProposal` 没有 goal/task/event 自由文本字段，不接受模型自报 `AUTHOR_INTENT`；
- `EvidenceEnvelope.display_text` 由后端按 fact_type 固定渲染，不接受 Agent 自填；
- `SceneBrief` 不携带秘密安全约束（安全约束由起草后端重算）；
- 所有展示文字必须能通过 `report_item_id` / `source_refs` 反查来源。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..graph import NodeRef


class EpistemicKind(StrEnum):
    """认识状态八类（ADR 0033 §决策）。模型不能自报 `AUTHOR_INTENT`。"""

    AUTHOR_INTENT = "AUTHOR_INTENT"
    """作者看过类型化任务卡后明确确认的本次意图。服务端绑定整张卡，不是模型的话。"""

    AUTHOR_CANON_FACT = "AUTHOR_CANON_FACT"
    """作者经声明/修正链让其生效的章级事实。可作硬依据，仍服从有效期。"""

    AUTHOR_BACKGROUND = "AUTHOR_BACKGROUND"
    """作者手写、但未走 Canon 声明/修正链的背景。只作软背景。"""

    EXTRACTED_CURRENT = "EXTRACTED_CURRENT"
    """机器抽取，证据与当前正文快照一致。只能作有证据提示。"""

    OBSERVED_TEXT = "OBSERVED_TEXT"
    """原文精确提及、共同出现等字符串观察。只证明「写过/提到过」。"""

    MACHINE_SUMMARY = "MACHINE_SUMMARY"
    """与当前正文一致的机器章节总结。只作背景，不能用于硬冲突。"""

    MACHINE_INFERENCE = "MACHINE_INFERENCE"
    """Agent 对任务、动机、关系推进和情节走向的推演。只作写作建议。"""

    UNKNOWN = "UNKNOWN"
    """缺数据、陈旧、被截断、歧义或当前能力不可回答。不得补写成事实。"""


class FactType(StrEnum):
    """后端封闭事实类型。**不接受任意图节点类型。**"""

    BODY_LIMITATION = "BODY_LIMITATION"
    """公开身体限制（如「行动受限」）。只有封闭 value_key 才可证明公开。"""

    STATE = "STATE"
    """一般 HAS_STATE 状态。"""

    LOCATION = "LOCATION"
    """LOCATED_AT。"""

    DEATH = "DEATH"
    """生死。"""

    APPEARED = "APPEARED"
    """是否已登场。"""

    RELATIONSHIP_STAGE = "RELATIONSHIP_STAGE"
    """RELATED_TO 的关系阶段。只返回记录的关系阶段，不声称情感解释正确。"""

    EVENT = "EVENT"
    CHAPTER_SUMMARY = "CHAPTER_SUMMARY"
    PROFILE = "PROFILE"
    FORESHADOW = "FORESHADOW"
    """伏笔。生产读口未完成前固定返回能力缺失，不造空实现。"""


class Freshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNVERIFIED = "UNVERIFIED"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


class AgentVisibility(StrEnum):
    SAFE_FACT = "SAFE_FACT"
    SAFE_LABEL_ONLY = "SAFE_LABEL_ONLY"
    HIDDEN = "HIDDEN"


class WriterVisibility(StrEnum):
    SAFE_FACT = "SAFE_FACT"
    SAFE_LABEL_ONLY = "SAFE_LABEL_ONLY"
    HIDDEN = "HIDDEN"


class Sealability(StrEnum):
    READY_TO_SEAL = "READY_TO_SEAL"
    NEEDS_AUTHOR = "NEEDS_AUTHOR"
    STALE = "STALE"


class CalibrationStatus(StrEnum):
    OPEN = "OPEN"
    NEEDS_AUTHOR = "NEEDS_AUTHOR"
    READY_FOR_DRAFT = "READY_FOR_DRAFT"
    STALE = "STALE"
    REVOKED = "REVOKED"


class AuthorResolution(StrEnum):
    """作者面对旧事实的三个选择。"""

    FOLLOW_OLD = "FOLLOW_OLD"
    """遵循旧事实：不产通知。"""

    STORY_PROGRESSION = "STORY_PROGRESSION"
    """正常剧情发展：旧事实仍成立，本章是状态变化或例外；只留当轮选择。"""

    RETCON_NON_SAFETY = "RETCON_NON_SAFETY"
    """推翻非安全旧设定：作为 AUTHOR_INTENT 进入本稿，同时产持久 handoff。"""


class DirectiveKind(StrEnum):
    """封闭指令码。**不在这个集合里的创作细节不能进 Writer**（除非新增可机械验证的类型）。"""

    ENTER_LOCATION = "ENTER_LOCATION"
    SEARCH_FOR = "SEARCH_FOR"
    TEST_CHARACTER = "TEST_CHARACTER"
    DEFER_REVEAL = "DEFER_REVEAL"
    ADVANCE_CLUE = "ADVANCE_CLUE"


class AuthorTurnRef(BaseModel):
    """服务端绑定的作者 turn 标识。**模型不能自报或替换。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)


class SafeArg(BaseModel):
    """封闭指令的安全参数：只允许机器键、NodeRef 显示名、布尔/纯数值/章号。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    value: str | int | float | bool


class DirectiveCandidate(BaseModel):
    """Agent 提出的一条封闭指令候选。**不能塞任务/event/goal 散文。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DirectiveKind
    actor_surface: str | None = None
    target_surface: str | None = None
    object_surface: str | None = None
    location_surface: str | None = None
    basis: Literal["AGENT_INFERRED"] = "AGENT_INFERRED"


class IntendedCastMember(BaseModel):
    """预计人物的一个称呼。basis 固定 AGENT_INFERRED。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    surface: str = Field(min_length=1)
    basis: Literal["AGENT_INFERRED"] = "AGENT_INFERRED"


class SceneProposal(BaseModel):
    """Agent 提交给校准层的临时创意提案（§6.1）。

    **没有 goal / task / event 自由文本字段**：模型只能提交封闭指令码、安全引用和
    固定 `AGENT_INFERRED`；不能提交任务散文、event beat 散文、`must_not_reveal`、
    `forbidden_entities`、秘密正文、Canon 写入或章号有效期字段。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1, description="要校准第几章（AS OF 第几章，纯查询坐标）。")
    intended_cast: tuple[IntendedCastMember, ...] = Field(
        default=(),
        description=(
            "预计人物：作者当前要求里点名的人 + 你根据上下文补出的人。"
            "每一个都用作者在正文里的叫法。只用于检索与写作意图，不是本章实际在场名单。"
        ),
    )
    directive_candidates: tuple[DirectiveCandidate, ...] = Field(
        default=(),
        description=(
            "封闭指令候选：只能从 ENTER_LOCATION / SEARCH_FOR / TEST_CHARACTER / "
            "DEFER_REVEAL / ADVANCE_CLUE 里选，参数只能引用花名册上的人物/地点/物件/秘密"
            "显示名。不要在这里写任务散文或事件概括。"
        ),
    )
    viewpoint_surface: str | None = Field(
        default=None,
        description="视角人物（花名册上的称呼，只解析 NodeRef）。",
    )
    tone_code: str | None = Field(default=None, description="封闭语气码。")
    pacing_code: str | None = Field(default=None, description="封闭节奏码。")
    ending_code: str | None = Field(default=None, description="封闭结尾码。")


class SourceWatermark(BaseModel):
    """校准产物的水位。**不是含义不明的字符串。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    author_turn_id: str
    author_request_sha256: str = Field(min_length=64, max_length=64)
    canon_version: int = Field(ge=0)
    target_sha256: str
    supporting_chapter_hashes: tuple[tuple[int, str], ...] = ()
    summary_ids: tuple[str, ...] = ()


class EvidenceAnchor(BaseModel):
    """(para_index, quote_text, occurrence_k) 三元锚。**禁止 offset**（ADR 0006）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    para_index: int = Field(ge=0)
    quote_text: str
    occurrence_k: int = Field(ge=0)


class EvidenceEnvelope(BaseModel):
    """一条校准证据（§6.2）。

    `display_text` 只由后端按 fact_type 固定渲染；`agent_visibility` /
    `writer_visibility` 由后端封闭类型规则派生，Agent 和 LLM 都不能填写。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    kind: EpistemicKind
    fact_type: FactType
    display_text: str = ""
    chapter: int | None = None
    valid_from_chapter: int | None = None
    valid_to_chapter: int | None = None
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_id: str | None = None
    evidence_status: str | None = None
    anchor: EvidenceAnchor | None = None
    snapshot_id: str | None = None
    freshness: Freshness = Freshness.UNVERIFIED
    completeness: Completeness = Completeness.COMPLETE
    truncated: bool = False
    blind_reasons: tuple[str, ...] = ()
    agent_visibility: AgentVisibility = AgentVisibility.SAFE_FACT
    writer_visibility: WriterVisibility = WriterVisibility.SAFE_FACT


class NormalizedCastMember(BaseModel):
    """解析成功的预计人物：称呼原文 + NodeRef + basis。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    surface: str
    node: NodeRef
    basis: Literal["AGENT_INFERRED"] = "AGENT_INFERRED"


class DeterministicConflict(BaseModel):
    """只有确定性结构问题能进这里；语义张力是 Agent 的 `MACHINE_INFERENCE`。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Literal[
        "UNRESOLVED_NAME",
        "IMPOSSIBLE_STATE",
        "SAFETY_RETCON_PENDING",
    ]
    message: str
    chapter: int | None = None


class CoverageReceipt(BaseModel):
    """校准查到哪里、缺什么、被截断没有。空结果不能裸解释为「从未发生」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    queried_chapters: tuple[int, ...] = ()
    missing_summaries: tuple[int, ...] = ()
    truncated: bool = False
    blind_reasons: tuple[str, ...] = ()


class CalibrationReport(BaseModel):
    """给 Agent 和审计用的详细报告（§6.3）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """inspection_id（ULID）。"""

    project_id: str
    chapter: int
    author_turn_id: str
    author_request_sha256: str = Field(min_length=64, max_length=64)
    proposal: SceneProposal | None = None
    """封存时重用的原始提案（服务端保存，Agent 不必重新提交）。"""

    source_watermark: SourceWatermark
    sealability: Sealability
    normalized_cast: tuple[NormalizedCastMember, ...] = ()
    agent_safe_facts: tuple[EvidenceEnvelope, ...] = ()
    writer_safe_fact_ids: tuple[str, ...] = ()
    deterministic_conflicts: tuple[DeterministicConflict, ...] = ()
    warnings: tuple[str, ...] = ()
    unknowns: tuple[EvidenceEnvelope, ...] = ()
    coverage_receipt: CoverageReceipt


class AuthorInstructionRef(BaseModel):
    """作者确认记录：绑定完整 directive 联合、safe args、turn/hash 与 card hash。

    只有服务端能构造它（handler 从当前会话绑定）；仅有指向模型文字的 answer ID
    不算作者确认。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str
    request_sha256: str = Field(min_length=64, max_length=64)
    card_hash: str
    directive_union_hash: str
    safe_args: tuple[SafeArg, ...] = ()
    confirmation_id: str


class BriefDirective(BaseModel):
    """SceneBrief 里一条封闭指令。`basis` 由来源类型派生，模型不能填成作者来源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    directive_kind: DirectiveKind
    safe_args: tuple[SafeArg, ...] = ()
    basis: EpistemicKind = EpistemicKind.MACHINE_INFERENCE


class AuthorInstruction(BriefDirective):
    """已确认作者指令：只有完整 `AuthorInstructionRef` 能构造它。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    author_instruction_ref: AuthorInstructionRef


class ProjectedDirective(BriefDirective):
    """未确认的请求投影：强度固定 MACHINE_INFERENCE + 作者 turn 引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    author_turn_ref: AuthorTurnRef


class MachineDirective(BriefDirective):
    """机器写作建议（TEST_CHARACTER / ADVANCE_CLUE 一类）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_refs: tuple[str, ...] = ()


class EventBeat(BriefDirective):
    """事件节拍。`basis` 同样由来源类型派生。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_refs: tuple[str, ...] = ()


class ContinuityFact(BaseModel):
    """给 Writer 的连续性依据：只接 writer-safe item ID，可反查报告。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    report_item_id: str
    basis: EpistemicKind
    fact_type: FactType | None = None
    """渲染/UnknownCast 过滤用。展示文字仍然由后端渲染，不接受 Agent 自填。"""

    display_text: str = ""
    """后端在 seal 时从 `EvidenceEnvelope.display_text` 固定渲染的副本。"""

    strength: str
    """只能由来源规则派生（HARD / SUGGESTION），不能让模型把 EXTRACTED 升格成硬命令。"""


class DoNotAssume(BaseModel):
    """未知项：Writer 不得擅自断言。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    reason_code: str
    source_refs: tuple[str, ...] = ()


class SceneBrief(BaseModel):
    """给 Writer 的短执行简报（§6.5）。**不携带秘密安全约束。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    intended_cast: tuple[NodeRef, ...] = ()
    author_instructions: tuple[AuthorInstruction, ...] = ()
    projected_request_directives: tuple[ProjectedDirective, ...] = ()
    continuity_facts: tuple[ContinuityFact, ...] = ()
    machine_directives: tuple[MachineDirective, ...] = ()
    event_beats: tuple[EventBeat, ...] = ()
    do_not_assume: tuple[DoNotAssume, ...] = ()
    viewpoint_ref: NodeRef | None = None
    tone_code: str | None = None
    pacing_code: str | None = None
    ending_code: str | None = None


class SealedCalibration(BaseModel):
    """不可变、可起草的产物（§6.4）。`draft_chapter` 只接受 READY_FOR_DRAFT。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_id: str
    project_id: str
    chapter: int
    author_turn_id: str
    author_request_sha256: str = Field(min_length=64, max_length=64)
    goal_spec: str
    """由结构化作者选择、封闭指令码与安全引用组成；**不存任意目标散文**。"""

    author_resolution: AuthorResolution | None = None
    source_watermark: SourceWatermark
    source_inspection_id: str
    scene_brief: SceneBrief
    status: CalibrationStatus = CalibrationStatus.READY_FOR_DRAFT


class TargetChapterSnapshot(BaseModel):
    """一次起草唯一的目标章快照（§8.4）。所有下游共用同一个对象。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    text: str
    sha256: str = Field(min_length=64, max_length=64)


class ReferencedFact(BaseModel):
    """handoff 里引用的旧事实。敏感引语另由作者 UI 读取，不在这里。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    kind: EpistemicKind
    fact_type: FactType
    valid_from_chapter: int | None = None
    valid_to_chapter: int | None = None


class ProspectiveStructuralImpact(BaseModel):
    """修改尚未发生时的预期结构影响。只有回执才能写已发生结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["would_stale", "would_supersede", "would_change_interval"]
    target: str


class ReviewCandidate(BaseModel):
    """待复核候选，**不能改名成「确定受影响章节」**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["exact_mention", "related_event", "related_summary", "later_chapter"]
    chapter: int = Field(ge=1)
    note: str = ""


class ContinuityConflictHandoff(BaseModel):
    """交给并行通知系统的结构化冲突结果（§6.6）。只有 RETCON 生成。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_id: str
    project_id: str
    chapter: int
    author_turn_id: str
    proposal_item: str
    inferred_tension: str
    """明标 MACHINE_INFERENCE。"""

    referenced_facts: tuple[ReferencedFact, ...] = ()
    author_choice: Literal["RETCON"] = "RETCON"
    prospective_structural_impacts: tuple[ProspectiveStructuralImpact, ...] = ()
    review_candidates: tuple[ReviewCandidate, ...] = ()
    unknowns: tuple[str, ...] = ()
    source_watermark: SourceWatermark
