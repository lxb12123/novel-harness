"""面板 —— **不读正文的那一半产品**（PLAN §3.2 / ADR 0004）。

面板不是挑刺器，是**记忆外挂**。它不说「你错了」，它说「这是你告诉过我的」。
这个区别不是措辞：它是「真实使用」批判者 fatal #4（「作者要多写字，不要少写错」）
唯一被工程手段回应掉的那一半。

三个模块，一条共同的性质：**零 LLM、零 NLP、不读正文，因此零误报**——
它们对文本不做任何断言，只做集合查询。因此 `panel/` 里没有一行 import 到
`draft/provider.py`：面板这一半产品**根本不需要模型**，稿子不会因为它出你的电脑。
（PLAN §5.2 里那个「纯本地模式开关」——`complete()` 抛 `LocalOnlyMode`——**至今没有实现**，
`provider.py` 只有 `ProviderError`。别拿一个不存在的开关当上面这句话的理由：
它成立靠的是「不调」，不是「调不了」。）

| 模块 | 内容 |
|---|---|
| `state` | 人物卡：所在地 / 状态维度 / 生死 |
| `constraints` | 场景 cast 解析：作者写的称呼原文 → 节点，解析不出唯一节点的留给作者判断 |
| `scope` | 读路径那道 scope 闸（PLANNED / REJECTED 挡在外面）——**这一层唯一的安全断言** |

（`constraints` 原来还产出 `forbidden_entities`——PLANNED 进 prompt 的转译闸门。
**2026-08-31 删了**，见 [ADR 0041](../../../docs/adr/0041-forbidden-entities-cut.md)。
PLANNED 边本身仍然没有读路径，`scope` 那道闸没有跟着松动。）
"""

from __future__ import annotations

from .constraints import ResolvedCast, SceneConstraints, UnresolvedCast, resolve_cast
from .scope import require_queryable_scope
from .state import cast_states, character_state

__all__ = [
    "ResolvedCast",
    "SceneConstraints",
    "UnresolvedCast",
    "cast_states",
    "character_state",
    "require_queryable_scope",
    "resolve_cast",
]
