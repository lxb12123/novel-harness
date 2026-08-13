"""R4 `LOCATION_CONFLICT`：场景块声明的 `loc` 与人物当前所在地冲突。

**作者声明 vs 作者声明，不读正文，零 FP**（ADR 0005 的表）。

这是 v1 现在就能做的**唯一**一条规则：

- R2 FUTURE_LEAK / R3 DEAD_SPEAKS 要读正文，等 `text/mentions.py`（M3）
- R5 ADDRESS_CONFLICT 已砍（2026-08-02：真书样本显式标签覆盖率 8.2% < 10%，
  ADR 0014）
- R1 认知边界不是规则，是面板（`panel/knowledge.py`）

零 FP 的来源不是「调得准」，是**两侧都是作者自己敲进去的字**：一侧是他此刻写在场景块
里的 `loc`，另一侧是他确认过的 CANON `LOCATED_AT` 边。规则不对文本做任何断言，
所以它没有可误报的余地——**只要它在任何一侧不确定时闭嘴。**

── 四个闭嘴条件（每一个都是 FP 的入口，删一个就是删掉零 FP）──────────────

1. `loc` 解析不到唯一节点（歧义或没这个地方）→ 闭嘴
2. `loc` 解析到的不是 `Location` 节点（作者打错了）→ 闭嘴
3. cast 里的称呼解析不到唯一节点 → 跳过这个人
4. 图上根本没有这个人的 `LOCATED_AT` 边 → 闭嘴（闭世界：没声明过在哪 ≠ 不在这）

STALE 的边不用在这里挡：`state_at` 的五条件过滤已经把它们滤掉了（`evidence_status
!= 'STALE'`）。**这正是 ADR 0006「STALE 立刻停火」90% 的价值落地的地方**——依据被
作者改没了就别再拿它质疑作者。
"""

from __future__ import annotations

from ..graph import EdgeType, Node, NodeLabel, Resolution, StoryGraph, TextAnchor
from .base import FIRE_SCOPE, CheckContext, Issue

RULE = "R4"
ISSUE_TYPE = "LOCATION_CONFLICT"


def _unique_location(res: Resolution) -> Node | None:
    """闭嘴条件 1 + 2。

    这里用 `Resolution.unique_node` 而**不是** `Resolution.usable_for_rules`，这是个
    有意的区分：`usable_for_rules` 多带的那条约束是「短别名不许去匹配正文」（ADR 0004
    ——「音」「决」是灾难），而 R4 **不匹配正文**，它读的是作者亲手敲在场景块里的
    `loc=`。一个 1 字地名（`渊`）在这里是合法的。歧义则必须挡——那是跨行事实，
    `unique_node` 正是它的判据。
    """
    node = res.unique_node
    if node is None or node.label is not NodeLabel.LOCATION:
        return None
    return node


def check(ctx: CheckContext) -> list[Issue]:
    """纯函数。不读 `ctx.paragraphs`，一次都不读。"""
    issues: list[Issue] = []
    store: StoryGraph = ctx.store

    for scene in ctx.scenes:
        if scene.loc is None:
            continue

        # 一次 resolve 拿全场景要的 surface：契约保证返回与入参同序、且解析不到的
        # surface 也会返回 hits=[] 的 Resolution（不许静默丢），所以下标是对齐的。
        resolutions = store.resolve(ctx.project_id, [scene.loc, *scene.cast])
        declared = _unique_location(resolutions[0])
        if declared is None:
            continue

        for res in resolutions[1:]:
            character = res.unique_node
            if character is None:
                continue

            snapshot = store.state_at(ctx.project_id, character.id, ctx.chapter, scope=FIRE_SCOPE)
            actual = snapshot.location
            if actual is None or actual.id == declared.id:
                continue

            since = next(
                (
                    e.valid_from_chapter
                    for e in snapshot.edges
                    if e.type is EdgeType.LOCATED_AT and e.dst == actual.id
                ),
                None,
            )
            issues.append(
                Issue(
                    rule=RULE,
                    issue_type=ISSUE_TYPE,
                    chapter=ctx.chapter,
                    anchor=TextAnchor(para_index=scene.para_index, quote_text=scene.decl_text),
                    message=_message(scene.number, character, declared, actual, since, ctx.chapter),
                    suggested_action=(
                        f"把场景 {scene.number} 里写的地点改成「{actual.name}」，"
                        f"或者在第 {ctx.chapter} 章里找到他到「{declared.name}」的那句原文，"
                        f"把{character.name}的去处记到那儿"
                    ),
                )
            )

    return issues


def _message(
    scene_number: int,
    character: Node,
    declared: Node,
    actual: Node,
    since: int | None,
    chapter: int,
) -> str:
    where = f"位于「{actual.name}」" if since is None else f"自第 {since} 章起位于「{actual.name}」"
    return (
        f"场景 {scene_number} 声明在「{declared.name}」，但{character.name}在第 {chapter} 章{where}"
    )
