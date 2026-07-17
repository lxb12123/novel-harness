"""面板 —— **不读正文的那一半产品**（PLAN §3.2 / ADR 0004）。

面板不是挑刺器，是**记忆外挂**。它不说「你错了」，它说「这是你告诉过我的」。
这个区别不是措辞：它是「真实使用」批判者 fatal #4（「作者要多写字，不要少写错」）
唯一被工程手段回应掉的那一半。

三个模块，一条共同的性质：**零 LLM、零 NLP、不读正文，因此零误报**——
它们对文本不做任何断言，只做集合查询。所以它们在纯本地模式下完好无损
（`complete()` 抛 `LocalOnlyMode`，而面板本来就不调它），这也是「你的稿子不出你的
电脑」这句话成立的原因。

| 模块 | 内容 |
|---|---|
| `knowledge` | ★ 认知边界矩阵（头牌，README 第一行） |
| `state` | 人物卡：所在地 / 状态维度 / 生死 |
| `constraints` | PLANNED 进 prompt 的唯一闸门：must_not_reveal / forbidden_entities |
"""

from __future__ import annotations

from .constraints import (
    ForbiddenEntity,
    ResolvedCast,
    SceneConstraints,
    UnresolvedCast,
    forbidden_entities,
    resolve_cast,
    scene_constraints,
)
from .knowledge import knowledge_matrix, require_queryable_scope
from .state import cast_states, character_state

__all__ = [
    "ForbiddenEntity",
    "ResolvedCast",
    "SceneConstraints",
    "UnresolvedCast",
    "cast_states",
    "character_state",
    "forbidden_entities",
    "knowledge_matrix",
    "require_queryable_scope",
    "resolve_cast",
    "scene_constraints",
]
