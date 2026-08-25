"""认知边界矩阵 —— **整个项目的头牌**（PLAN §3.2 / §8 Day 5 / README 第一行）。

```
┌─ 认知边界 · 第 152 章 · 场景 3 ─────────────────────┐
│  在场角色          血脉秘密        玄铁令下落        │
│  萧决              ✓ 知道 (ch88)   ✓ 知道 (ch120)    │
│  顾清音            ✗ 不知道        ✗ 不知道          │
│  李管家            ⚠ 错误认知      ✗ 不知道          │
│                      (ch103 起：以为已泄露)          │
└──────────────────────────────────────────────────────┘
```

**纯集合查询：零 LLM、零 NLP、不读正文、零误报——因为它对文本不做任何断言。**
它只是在告诉作者：你自己在第 88 章告诉过它的事。

**闭世界推导：不存在 KNOWS/BELIEVES 边 ⇒ UNKNOWN**（ADR 0005 / 改 9）。零
`DOES_NOT_KNOW` 边——实体化它会额外产生约 67,500 条边，而推导是 O(已声明秘密)。

为什么它必须是数据库查询而不是模型调用（这条决定了它值不值得存在）：
「列出所有知道 X 的人」是 **list 型**问题，正是 ToM 研究里模型失败的那种（GPT-4 在
FANToM 上跨题型一致性 26.6%，人类 87.5%），而且推理模型在高阶 ToM 上反而更差——
**这封死了「等模型变强就好了」这条退路。** 抽取是开放式枚举，校验是封闭式判定，
**声明+查询是零判定。**

── 这个模块为什么这么薄 ──────────────────────────────────────────────

矩阵的 SQL 归 `graph/queries.py`（§5.5：全系统时态过滤只写一次）。这里是面板/API/
draft 的 D 分区共用的那个入口，它做且只做两件 store 不该替它做的事：把 scope 挡在
可读层内，以及把「不填 secrets = 全书秘密」这条面板默认说清楚。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..graph import InformationScope, KnowledgeMatrix, StoryGraph
from .scope import require_queryable_scope


def knowledge_matrix(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
    *,
    secrets: Sequence[str] | None = None,
    scope: InformationScope = InformationScope.CANON,
    unresolved: Sequence[str] = (),
) -> KnowledgeMatrix:
    """`cast × secret` 的认知矩阵。

    Args:
        cast: 在场角色的 **node_id**，顺序即面板的行序。由作者在场景块里声明
            （`<!-- nh: cast=萧决,顾清音,李管家 -->`），不是抽的。称呼 → node_id 的
            解析是调用方的活（`panel.constraints.resolve_cast`）——因为「这个称呼有歧义」
            要在 UI 上问作者，不能由面板猜一个。
        unresolved: 作者声明了、但**解析不出唯一节点**的称呼，原样带进出参。
            调用方拿 `resolve_cast()` 的 `.unresolved` 填它。不填的代价是面板安静地
            少一行：作者声明了 3 个人、面板画出 2 行、而他以为系统对第 3 个人没意见。
            这一条 `KnowledgeMatrix._check_complete` 拦不到——它只校验已知行 × 已知列。
        secrets: 列序。`None` = 本项目全部秘密。若作者把一个秘密拆成了子事实
            （ADR 0005 用它替代 `PARTIALLY_KNOWS`），父秘密和子事实**都会成列**——
            要不要折叠是面板层的判断，图层不猜。
        scope: 默认 CANON。面板要灰显 PROVISIONAL 就用 PROVISIONAL 再调一次
            （§5.4：PROVISIONAL 只喂检索和面板灰显，**永不断言为真**——所以灰显是
            两次调用两种渲染，不是一次调用混在一起）。

    Returns:
        **完整的笛卡尔积**，`len(cells) == len(cast) * len(secrets)`，UNKNOWN 格必须
        物化（`KnowledgeMatrix` 的 validator 强制）。闭世界推导下「没有这一格」和
        「他不知道」是两个意思，而**面板上少一格 = 作者以为系统没意见 = 说漏嘴**。

    Raises:
        ValueError: `scope` 不在 `QUERYABLE_SCOPES`。
        NodeNotFound: cast / secrets 里有 id 不在本项目。
    """
    require_queryable_scope(scope)
    matrix = store.knowledge_matrix(project_id, chapter, cast, secrets=secrets, scope=scope)
    if not unresolved:
        return matrix
    # store 收的是 node_id，它没有机会知道哪些称呼没解析出来——这个字段只能在这里挂上。
    return matrix.model_copy(update={"unresolved_cast": list(unresolved)})
