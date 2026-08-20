"""模式二写前校准 —— 类型化、只读、可反查的起草前数据链（ADR 0033）。

规格书：`docs_dev/2026-08-17-模式二写前校准与写作执行简报任务.md` + `docs/adr/0033`。

模块划分：

- `models.py`   —— 全部 Pydantic 契约（frozen + extra="forbid"）
- `visibility.py` —— fact type → agent/writer 可见性白名单
- `render.py`   —— 后端固定模板渲染（Writer 简报 / goal_spec / 目标章正文）
- `freshness.py` —— 摘要新鲜度的确定性判据
- `store.py`    —— 非 Canon artifact 存储（SQLite，可过期、可重建、按内容幂等）
- `calibrate.py` —— `calibrate_scene`：确定性身份/时态/集合/覆盖校准
- `seal.py`     —— `seal_scene_brief`：重新校验 + 固定渲染 + 签发不可变 ID
- `handoff.py`  —— `ContinuityConflictHandoff` producer 契约
- `repair.py`   —— `RepairRequest` / `RepairPlan` / `ExecutionReceipt` adapter 契约

依赖方向：本包**不 import `agent/`**；`agent/` 反过来收本包的端口与类型。
"""

from __future__ import annotations

from .handoff import (
    ContinuityConflictHandoff,
    ProspectiveStructuralImpact,
    ReferencedFact,
    ReviewCandidate,
    build_retcon_handoff,
)
from .models import (
    AgentVisibility,
    AuthorInstructionRef,
    AuthorResolution,
    AuthorTurnRef,
    BriefDirective,
    CalibrationReport,
    CalibrationStatus,
    Completeness,
    ContinuityFact,
    CoverageReceipt,
    DeterministicConflict,
    DirectiveCandidate,
    DirectiveKind,
    DoNotAssume,
    EpistemicKind,
    EventBeat,
    EvidenceAnchor,
    EvidenceEnvelope,
    FactType,
    Freshness,
    IntendedCastMember,
    MachineDirective,
    NormalizedCastMember,
    ProjectedDirective,
    SafeArg,
    SceneBrief,
    SceneProposal,
    Sealability,
    SealedCalibration,
    SourceWatermark,
    TargetChapterSnapshot,
    WriterVisibility,
)
from .repair import (
    ExecutionReceipt,
    ExecutionReceiptItem,
    ExecutionReceiptStatus,
    RepairAction,
    RepairActionKind,
    RepairAuthorization,
    RepairPlan,
    RepairRequest,
)
from .store import (
    CalibrationNotFound,
    CalibrationRefused,
    CalibrationStore,
    CalibrationStoreError,
)
from .visibility import agent_visibility_of, writer_visibility_of

__all__ = [
    "AgentVisibility",
    "AuthorInstructionRef",
    "AuthorResolution",
    "AuthorTurnRef",
    "BriefDirective",
    "CalibrationNotFound",
    "CalibrationRefused",
    "CalibrationReport",
    "CalibrationStatus",
    "CalibrationStore",
    "CalibrationStoreError",
    "Completeness",
    "ContinuityConflictHandoff",
    "ContinuityFact",
    "CoverageReceipt",
    "DeterministicConflict",
    "DirectiveCandidate",
    "DirectiveKind",
    "DoNotAssume",
    "EpistemicKind",
    "EventBeat",
    "EvidenceAnchor",
    "EvidenceEnvelope",
    "ExecutionReceipt",
    "ExecutionReceiptItem",
    "ExecutionReceiptStatus",
    "FactType",
    "Freshness",
    "IntendedCastMember",
    "MachineDirective",
    "NormalizedCastMember",
    "ProjectedDirective",
    "ProspectiveStructuralImpact",
    "ReferencedFact",
    "RepairAction",
    "RepairActionKind",
    "RepairAuthorization",
    "RepairPlan",
    "RepairRequest",
    "ReviewCandidate",
    "SafeArg",
    "SceneBrief",
    "SceneProposal",
    "Sealability",
    "SealedCalibration",
    "SourceWatermark",
    "TargetChapterSnapshot",
    "WriterVisibility",
    "agent_visibility_of",
    "build_retcon_handoff",
    "writer_visibility_of",
]
