"""按需 Agent 修复的 adapter 契约（Task 9 / §10）。

只定义版本化 `RepairRequest` / `RepairPlan` / `ExecutionReceipt`，**不实现**：

- 不实现 notice 状态（那是并行通知任务的）；
- 不决定 resolved、不编排总结/抽取；
- 不扩张 Agent 写图权限（Canon 修正只生成 `REQUEST_CANON_CORRECTION`）；
- 安全修订候选能力完成前不暴露「自动修复」。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RepairActionKind(StrEnum):
    ADJUST_CURRENT_BRIEF = "ADJUST_CURRENT_BRIEF"
    REQUEST_CANON_CORRECTION = "REQUEST_CANON_CORRECTION"
    REVISE_CHAPTER = "REVISE_CHAPTER"
    REGENERATE_SUMMARY = "REGENERATE_SUMMARY"
    RERUN_EXTRACTION = "RERUN_EXTRACTION"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class RepairAuthorization(StrEnum):
    PLAN_ONLY = "PLAN_ONLY"
    GENERATE_CANDIDATE = "GENERATE_CANDIDATE"
    APPLY_EXACT_SCOPE = "APPLY_EXACT_SCOPE"


class ExecutionReceiptStatus(StrEnum):
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    SAVED = "SAVED"
    REFUSED = "REFUSED"
    CANON_CORRECTION_REQUESTED = "CANON_CORRECTION_REQUESTED"
    PENDING_AUTHOR = "PENDING_AUTHOR"


class RepairRequest(BaseModel):
    """通知系统用结构化 notice ID 启动 Agent，不重贴散文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notice_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    repair_goal: str = Field(min_length=1)
    """给作者看的方案目标。**不直接转发给正文 Writer。**"""

    authorization: RepairAuthorization = RepairAuthorization.PLAN_ONLY
    chapter: int | None = Field(default=None, ge=1)


class RepairAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RepairActionKind
    target: str
    reason: str = ""
    source_refs: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    reversible: bool = True


class RepairPlan(BaseModel):
    """先方案，后执行。计划本身不是 Canon。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notice_id: str
    repair_goal: str
    authorization: RepairAuthorization
    actions: tuple[RepairAction, ...] = ()
    definite_scope: tuple[str, ...] = ()
    """LOCAL：未发现可确定的跨章触达；不等于保证无影响。"""

    review_scope: tuple[str, ...] = ()
    """CROSS_CHAPTER：存在时态区间、硬规则、失效证据或后续精确提及，需要列章复核。"""

    unknown_scope: tuple[str, ...] = ()
    """POSSIBLE_RESTRUCTURE：可能需要大改，必须由作者确认。"""


class ExecutionReceiptItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    action: RepairActionKind
    status: ExecutionReceiptStatus
    note: str = ""
    draft_id: str | None = None


class ExecutionReceipt(BaseModel):
    """逐章回执。供并行保存闭环消费；摘要/抽取/通知解决状态不由本任务编排。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notice_id: str
    project_id: str
    items: tuple[ExecutionReceiptItem, ...] = ()
