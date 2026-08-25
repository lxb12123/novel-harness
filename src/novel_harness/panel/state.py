"""人物当前状态 —— 人物卡的数据源（PLAN §9）。

`state_at` 的两条语义在这里原样透传，别在下游重新发明：

- **出边 + 无向边的两端**（ADR 0008）。§5.5 的 SQL 只写了 `WHERE src = :node`，那是在
  「所有边都有向」的前提下写的——RELATED_TO 按 `(min,max)` 存，方向是抛硬币，只查 src
  会让一半人物卡的关系栏凭空消失。取对端用 `Edge.peer_of()`，别写 `e.dst`。
  有向边的入边仍然用 `subgraph(hops=1)`。
- **闭开区间 `[valid_from, valid_to)`**，且五个过滤条件缺一不可（区间 / scope /
  status / evidence_status != STALE）。全系统只有 `graph/queries.py` 写这一次。

「死了」是一条 `HAS_STATE` 边而不是 `node.props.status`，因为**它必须是时态的**：
存成节点字段的话，萧决第 89 章死了会让 R3 在第 50 章也报「死人说话」——那是 100%
误报，而误报 <1 条/章 是 M3 的生死线。判据收敛在 `StateSnapshot.is_dead`，
规则不许自己拼（更不许去解析作者写的「陨落 / 坐化 / 兵解」——那是 ADR 0005 的铁律）。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..graph import InformationScope, StateSnapshot, StoryGraph
from .scope import require_queryable_scope


def character_state(
    store: StoryGraph,
    project_id: str,
    node_id: str,
    chapter: int,
    *,
    scope: InformationScope = InformationScope.CANON,
) -> StateSnapshot:
    """某人在第 `chapter` 章的状态快照（所在地 / 各状态维度 / 全部有效边）。

    Args:
        scope: 默认 CANON。PROVISIONAL 是面板灰显那一次调用——**灰显的边永不开火、
            永不断言为真**，所以它必须是另一次调用、另一种渲染，不能混进同一份快照。

    Raises:
        ValueError: `scope` 不在 `QUERYABLE_SCOPES`。
        NodeNotFound: `node_id` 不在本项目。

    Notes:
        `StateSnapshot.location` 至多一个是 `LOCATED_AT` 的 exclusivity 保证的，
        不是这里挑的。**如果它同时是青云城和北荒，那是 supersede 漏了，应当炸出来**，
        不许在渲染层悄悄取第一个——那会把一个写入期的 bug 变成一条查不出来源的误报。
    """
    require_queryable_scope(scope)
    return store.state_at(project_id, node_id, chapter, scope=scope)


def cast_states(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
    *,
    scope: InformationScope = InformationScope.CANON,
) -> list[StateSnapshot]:
    """整个场景在场角色的状态，**与 `cast` 同序**（顺序 = 面板行序 = 作者写的顺序）。

    Args:
        cast: node_id。称呼 → id 的解析是调用方的活（`store.resolve`）。
    """
    require_queryable_scope(scope)
    return [store.state_at(project_id, node_id, chapter, scope=scope) for node_id in cast]
