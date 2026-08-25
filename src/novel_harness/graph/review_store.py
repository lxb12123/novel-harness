"""M4 作者审阅用的窄图边界契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .models import Edge, Evidence, NodeRef


class EdgeReviewError(Exception):
    """审阅期边仓储失败的基类。"""


class EdgeReviewValidationError(EdgeReviewError, ValueError):
    """请求的边集合并非全部可提升的 PROVISIONAL 状态。"""


class ReviewableEdge(BaseModel):
    """一条类型化边 + 两端可安全展示的名字。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge: Edge
    src: NodeRef
    dst: NodeRef


@runtime_checkable
class EdgeReviewStore(Protocol):
    def hydrate_provisional(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]: ...

    def hydrate_current_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]: ...

    def clone_to_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]: ...

    def evidence(self, project_id: str, evidence_id: str) -> Evidence: ...

    # ── 作者事后改一条已生效事实用的四个（见 corrections.py）─────────────────

    def node_refs(
        self,
        project_id: str,
        node_ids: Sequence[str],
    ) -> tuple[NodeRef, ...]:
        """按 id 取**窄引用**，与入参同序。不存在或跨项目 → 抛，不静默丢。

        故意只出 `NodeRef`：这些 id 里有作者还没写到的实体，而 `Node.props` 装的正是
        「第 200 章才揭晓」那类内容（`NodeRef` 的 docstring 有实测泄漏形态）。
        图层之外没有第二个按 id 取节点的口子。
        """
        ...
