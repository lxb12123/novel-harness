"""本章正文里提到了花名册中的哪些称呼 —— 把「谁在场」从**作者的输入**变成**引擎的推导**。

── 为什么不在 `panel/`，也不在 `text/` ──────────────────────────────────

`panel/` 那一半产品的性质写在它的 `__init__` 里：「零 LLM、零 NLP、**不读正文**，
因此零误报」。这个模块读正文，放进去会把那句话变成假的。
`text/` 是纯字符串层，不认识 store。
所以它是应用层的一次合成，和 `declare.py` / `importer.py` 同级。

── 为什么「被提到」可以拿来当 cast 用，而这不是语义判断 ──────────────────

它**不回答**「谁在这一场里」。它回答的是一个集合问题：**花名册里的哪些称呼，
在这一章的正文里出现过。** 用的就是 `text/mentions.py` 那条正则 alternation，
和 R2 FUTURE_LEAK 同一份实现，一个语义判断都没有（ADR 0005 铁律）。

拿它当 cast 之所以成立，是因为它落在**唯一安全的那一侧**：

    SceneConstraints.must_not_reveal 的判据是
    「在场的人里**至少有一个**还不知道（或持错误认知）」

  · cast **多**算一个人 → must_not_reveal **多**一条 → fail-closed，代价是少写一段
  · cast **少**算一个人 → must_not_reveal **少**一条 → fail-open，代价是崩人设

`panel/constraints.scene_constraints` 的 docstring 里那个「李管家静默地从 cast 里消失，
血脉秘密从 must_not_reveal 里消失」就是后一种，它是这个仓库修过的真 bug。

而「被提到的人」是「真正在场的人」的**超集**——回忆里的死人、被议论的第三方、
信里写到的名字都会进来。**超集恰好落在多算那一侧。**

── 因此：出参叫 `mentioned`，不叫 `cast`，UI 上也不许写「在场」──────────

叫错名字等于向作者承诺引擎读懂了剧情。它没有，它在数字符串。
作者看到「本章提到：萧决、顾清音」会自己判断这是不是他要的；
看到「在场：萧决、顾清音」则会以为系统确认过他们在同一个屋子里。
"""

from __future__ import annotations

from collections.abc import Sequence

from .graph import NodeLabel, StoryGraph
from .text.mentions import compile_alternation, find_mentions

__all__ = ["mentioned_cast"]


def mentioned_cast(
    store: StoryGraph,
    project_id: str,
    paragraphs: Sequence[str],
) -> list[str]:
    """本章正文里出现过的花名册称呼，按**首次出现顺序**去重。

    Args:
        paragraphs: 本章正文，`text.paragraphs()` 切好的（全库唯一定义，§1.4）。

    Returns:
        称呼原文（surface），**不是 node_id** —— 下游 `scene_constraints` 收的就是原文，
        而且必须收原文：解析留给调用方会让歧义称呼静默消失（那个 docstring 讲了整件事）。
        顺序稳定：同一份正文永远得到同一个列表，因为它就是右栏矩阵的行序。

    Notes:
        `rules_only=True` 已经把「恰好一个候选且未被标不可用」之外的全滤掉了，
        所以这里不再自己判一遍——`琴声清音袅袅` 在「清音」不可用时天然不命中，
        不是这里语义过滤，是它根本没进 alternation。歧义称呼（「师兄」→ 8 个人）
        同理天然出局，而 fail-closed 的方向没变：少认一个人 = 少一行矩阵，
        `scene_constraints` 那边照旧多禁。

        **只收 Character。** 花名册里还有地点、秘密、物件——`血脉秘密` 出现在正文里
        是常事，但把它塞进 cast 会让认知矩阵长出一行「血脉秘密知道血脉秘密吗」。
        这一步是查 `node.label`，仍然是集合判断。

        一个人的多个称呼同时出现（「顾清音」在第 1 段、「清音」在第 3 段）会返回两个
        surface。**这是对的**：下游 `resolve_cast` 按 node_id 去重，他仍然是一行。
    """
    surfaces = [
        r.surface
        for r in store.resolve(project_id, None, rules_only=True)
        if r.hits and r.hits[0].node.label is NodeLabel.CHARACTER
    ]
    if not surfaces:
        return []
    pattern = compile_alternation(surfaces)
    seen: dict[str, None] = {}
    for hit in find_mentions(paragraphs, pattern):
        seen.setdefault(hit.matched_text, None)
    return list(seen)
