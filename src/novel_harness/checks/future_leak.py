"""R2 `FUTURE_LEAK`：正文提前提到「第 K 章才首现」的实体（ADR 0005 表）。

判据：`node.props.first_appears_chapter = K > 当前章 N`，而正文出现了该节点的
**可用称呼**（`resolve(rules_only=True)`，ADR 0004 的 usable_for_rules 闸门）。
集合判断，零语义：专名是作者亲选的，「第 K 章才首现」也是作者填的——两侧都是
作者自己敲进去的字（同 R4 的「作者声明 vs 作者声明」）。

── 与 R3 的分工（避免同一事件报两条）──────────────────────────────────────

**本规则只查非角色节点**（Location / Faction / Object / Secret…）。角色的「未登场」
归 R3 `DEAD_SPEAKS` 管，而且 R3 用的是高信号位置（说话人标签），不是任意叙述提及
——把角色塞进 R2 会让「未登场角色在叙述里被提起」也开火，那正是 R3 刻意不碰的
误报形态（ADR 0005 的 R3 注释：`萧决当年……` 不触发）。

── 闭嘴条件（每一条都是 FP 的入口）───────────────────────────────────────

1. `ctx.paragraphs is None` → `[]`（面板链路不喂正文，读正文的规则不许报错）。
2. 称呼解析不到唯一可用节点 → 闭嘴（`usable_for_rules`）。
3. `first_appears_chapter is None`（一开始就在）或 `<= 当前章` → 已登场，不报。
4. 节点 label 是角色 → 交给 R3。
"""

from __future__ import annotations

from ..graph import Node, NodeLabel, TextAnchor
from ..text.mentions import compile_alternation, find_mentions
from .base import CheckContext, Issue

RULE = "R2"
ISSUE_TYPE = "FUTURE_LEAK"


def _first_appears(node: Node) -> int | None:
    return node.props.first_appears_chapter


def check(ctx: CheckContext) -> list[Issue]:
    if ctx.paragraphs is None:
        return []

    roster = ctx.store.resolve(ctx.project_id, rules_only=True)
    future = [
        res
        for res in roster
        if res.usable_for_rules
        and (node := res.unique_node) is not None
        and node.label is not NodeLabel.CHARACTER
        and _first_appears(node) is not None
        and _first_appears(node) > ctx.chapter
    ]
    if not future:
        return []

    pattern = compile_alternation([res.surface for res in future])
    by_surface = {res.surface: res for res in future}
    issues: list[Issue] = []
    for hit in find_mentions(ctx.paragraphs, pattern):
        node = by_surface[hit.matched_text].unique_node
        assert node is not None
        first = _first_appears(node)
        issues.append(
            Issue(
                rule=RULE,
                issue_type=ISSUE_TYPE,
                chapter=ctx.chapter,
                anchor=TextAnchor(
                    para_index=hit.para_index,
                    quote_text=hit.matched_text,
                    occurrence_k=hit.occurrence_k,
                ),
                message=(
                    f"「{hit.matched_text}」要到第 {first} 章才首现，"
                    f"第 {ctx.chapter} 章正文里不该出现。"
                ),
                suggested_action=(
                    f"如果它该在此刻出场，把它的 first_appears_chapter 改到 ≤ {ctx.chapter}；"
                    "否则删掉这次提前提及。"
                ),
            )
        )
    return issues
