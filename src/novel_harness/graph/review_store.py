"""M4 作者审阅用的窄图边界契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .models import Edge, EdgeProps, Evidence, NodeRef


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

        故意只出 `NodeRef`：这些 id 里有 Secret，而 `Node.props` 装的正是秘密的内容
        （`NodeRef` 的 docstring 有实测泄漏形态）。图层之外没有第二个按 id 取节点的口子。
        """
        ...

    def current_knowledge(
        self,
        project_id: str,
        character_id: str,
        secret_id: str,
    ) -> tuple[ReviewableEdge, ...]:
        """这一格上此刻有效的 KNOWS / BELIEVES。**入参里没有章号**（约束 10）。"""
        ...

    def retract_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]:
        """把一条 current CANON 边标成 RETRACTED（「这条事实从未成立过」）。

        **不是删除**：行留着，只是 `TEMPORAL_WHERE` 的 `status = 'ACTIVE'` 不再放它出来。
        为什么是 RETRACTED 而不是写 `valid_to`：作者改的是**同一条证据的读法**
        （「他知道」其实是「他以为」），不是「他到第 N 章不知道了」——后者写出来是
        一段假的历史区间，而它在人物卡上长得完全正常。
        """
        ...

    def restore_canon(
        self,
        project_id: str,
        edge_id: str,
        *,
        props: EdgeProps,
    ) -> ReviewableEdge:
        """把一条 RETRACTED 的 CANON 边改回 ACTIVE 并换上新的 props。**改回来那条路。**

        存在的理由见 `queries.activate_edge`：没有它，「KNOWS→BELIEVES→KNOWS」的
        第二次会撞幂等键、被 upsert 当成重跑，两条边同时停在 RETRACTED，这一格静默消失。
        """
        ...
