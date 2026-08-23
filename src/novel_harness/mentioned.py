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

from .graph import NodeLabel, Resolution, StoryGraph
from .text.mentions import compile_alternation, find_mentions

__all__ = ["mentioned_cast"]


def _ambiguous_candidates(resolution: Resolution) -> list[str]:
    """一个歧义称呼背后的**全部人物候选**的正式名。不是歧义 / 没有人物候选就是空。

    ── 判据是「让它不可用的唯一原因是不是歧义」──────────────────────────

    `Resolution.usable_for_rules` 把 ADR 0004 的两条合并成了一个布尔值（歧义 +
    短别名/作者标了不可用）。展开只许放开**前一条**：一个字的「决」、作者亲手标掉的
    「清音」在正文里会疯狂误命中，把它们放回来是往 cast 里灌噪声，而不是补一个人。
    所以这里要求**每一条候选别名自己都是可用的**——不可用的原因只剩歧义那一个。
    """
    if not resolution.ambiguous:
        return []
    if not all(hit.usable_for_rules for hit in resolution.hits):
        return []
    return [hit.node.name for hit in resolution.hits if hit.node.label is NodeLabel.CHARACTER]


def mentioned_cast(
    store: StoryGraph,
    project_id: str,
    paragraphs: Sequence[str],
    *,
    expand_ambiguous: bool = False,
) -> list[str]:
    """本章正文里出现过的花名册称呼，按**首次出现顺序**去重。

    Args:
        paragraphs: 本章正文，`text.paragraphs()` 切好的（全库唯一定义，§1.4）。
        expand_ambiguous: 歧义称呼（「师兄」→ 8 个人）命中时，**把 8 个候选全都算进来**，
            而不是让它出局。默认 `False` = 老行为。见下面 Notes 最后一段。

    Returns:
        称呼原文（surface），**不是 node_id** —— 下游 `scene_constraints` 收的就是原文，
        而且必须收原文：解析留给调用方会让歧义称呼静默消失（那个 docstring 讲了整件事）。
        展开出来的那几个是候选的**正式名**（canonical 别名，本身唯一可解析），
        不是「师兄」两个字——原样交出去下游只会把它判成 unresolved，也就是全禁。
        顺序稳定：同一份正文永远得到同一个列表，因为它就是右栏矩阵的行序。

    Notes:
        `rules_only=True` 已经把「恰好一个候选且未被标不可用」之外的全滤掉了，
        所以这里不再自己判一遍——`琴声清音袅袅` 在「清音」不可用时天然不命中，
        不是这里语义过滤，是它根本没进 alternation。

        **只收 Character。** 花名册里还有地点、秘密、物件——`血脉秘密` 出现在正文里
        是常事，但把它塞进 cast 会让认知矩阵长出一行「血脉秘密知道血脉秘密吗」。
        这一步是查 `node.label`，仍然是集合判断。

        一个人的多个称呼同时出现（「顾清音」在第 1 段、「清音」在第 3 段）会返回两个
        surface。**这是对的**：下游 `resolve_cast` 按 node_id 去重，他仍然是一行。

        ── `expand_ambiguous`：只加不减的那一侧（2026-08-22）────────────────

        歧义称呼出局在**这一层**是 fail-closed 的（少一行矩阵 ⇒ 下游多禁），但下游
        「多禁」的具体形态是**整章全书全禁**——安全，却安全得没用：AI 拿着一份
        「什么都别碰」的清单写不出能用的东西。展开成全部候选仍然落在同一侧
        （判据是「在场至少有一个人还不知道」，**多算一个人只会多一批禁令**），
        但它算得出一份具体的清单。同 `api/app.py::_effective_cast` 的 `include`：
        **只往里加人，绝不减人。**

        **两条 alternation 必须合成一条**（不是扫两遍再并起来）：「师兄」和「小师兄」
        同时在册时，分两遍扫会让「师兄」在「小师兄」里面也命中一次。多算仍然安全，
        但 `text/mentions.py` 的最长优先是这一层唯一的机械纪律，破一处就没了。

        ── ⚠️ 右栏**故意**不传这个参数，那不是漏的（2026-08-22 裁定）──────────

        今天只有模式二传 `True`（`agent/tools.py::_derived_cast_from_text`）；
        `api/app.py::_chapter_mentions` 走默认档，`panel/constraints.py::resolve_cast`
        也**不**展开歧义（歧义照旧进 `unresolved`）。**于是同一章正文，右栏数出来的人
        比模式二少——这是设计，不是缝。**

        两侧要的东西不同：
        - **模型这一侧**要的是保险，判据是「宁可多禁」，8 个候选全算在场是对的；
        - **右栏这一侧**是给作者看的，他问的是「这一章谁在场」。把 8 个候选摆上去
          是噪声，而且正犯了「引擎的机制不上作者的屏」那条。

        **别为了「统一」把这个参数推到右栏去**——那是把一份给模型的保险清单
        当成给人看的事实来渲染。要改先回去读这条裁定。
    """
    expansion: dict[str, list[str]] = {}
    surfaces: list[str] = []
    for resolution in store.resolve(project_id, None):
        if resolution.usable_for_rules:
            if resolution.hits[0].node.label is NodeLabel.CHARACTER:
                surfaces.append(resolution.surface)
            continue
        if not expand_ambiguous:
            continue
        candidates = _ambiguous_candidates(resolution)
        if candidates:
            surfaces.append(resolution.surface)
            expansion[resolution.surface] = candidates
    if not surfaces:
        return []
    pattern = compile_alternation(surfaces)
    seen: dict[str, None] = {}
    for hit in find_mentions(paragraphs, pattern):
        for surface in expansion.get(hit.matched_text, [hit.matched_text]):
            seen.setdefault(surface, None)
    return list(seen)
