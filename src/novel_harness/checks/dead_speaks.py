"""R3 `DEAD_SPEAKS`：已死/未登场角色开口说话（ADR 0005 表）。

判据：正文里 `(角色可用称呼)(说话动词)`，而该角色在第 N 章的快照
（`state_at`，五条件时态过滤）是 `is_dead` 或 `未登场`。

── 为什么只在高信号位置开火 ──────────────────────────────────────────────

`萧决道：「……」` 且萧决第 89 章已死、现在是第 152 章——死人没有对话标签。
而 `萧决当年……`（别人提到死者）不在标签位置，不触发。这就是
「集合判断 vs 语义判断」这条线的价值：名字紧贴说话动词是**机械事实**，
「他在回忆死者」才是语义判断（ADR 0005 的 R3 注释）。

── 闭嘴条件（每一条都是 FP 的入口）───────────────────────────────────────

1. `ctx.paragraphs is None` → `[]`（面板链路不喂正文）。
2. 称呼解析不到唯一可用角色 → 闭嘴（`usable_for_rules`）。
3. 名字后面不是说话动词 → 闭嘴（位置不对，不算说话）。
4. `state_at` 显示没死且已登场 → 闭嘴（正常说话）。
"""

from __future__ import annotations

import re

from ..graph import NodeLabel, TextAnchor
from ..text.mentions import find_mentions
from .base import CheckContext, Issue

RULE = "R3"
ISSUE_TYPE = "DEAD_SPEAKS"

# 与 scripts/probe_speaker_tags.py 的动词表同源（ADR 0005 说那才是可执行副本）。
# 本规则比探针少一样东西：不认名字和动词之间的空格——R3 要的是「紧贴」，
# 空格一出现「萧决 道」就从标签位置退化回叙述（零歧义优先）。
SPEAKER_VERBS = (
    r"(?:道|说道|问道|答道|冷笑道|笑道|"
    r"开口道|沉声道|低声道|喝道|叹道)"
)
_VERB_TAIL = re.compile(SPEAKER_VERBS + r"$")


def _name_and_verb_pattern(surfaces: list[str]) -> re.Pattern[str]:
    """名字+动词的 alternation。与 mentions.compile_alternation 同一条排序纪律：
    长度降序（顾清音道 在 清音道 之前），让同一位置的最长称呼先试。
    """
    ordered = sorted(surfaces, key=lambda s: (-len(s), s))
    return re.compile(
        "|".join(f"{re.escape(s)}{SPEAKER_VERBS}" for s in ordered)
    )


def check(ctx: CheckContext) -> list[Issue]:
    if ctx.paragraphs is None:
        return []

    roster = ctx.store.resolve(ctx.project_id, rules_only=True)
    characters = [
        res
        for res in roster
        if res.usable_for_rules
        and res.unique_node is not None
        and res.unique_node.label is NodeLabel.CHARACTER
    ]
    if not characters:
        return []

    pattern = _name_and_verb_pattern([res.surface for res in characters])
    by_surface = {res.surface: res for res in characters}
    issues: list[Issue] = []
    for hit in find_mentions(ctx.paragraphs, pattern):
        name = _VERB_TAIL.sub("", hit.matched_text)
        res = by_surface.get(name)
        if res is None or res.unique_node is None:
            continue  # 匹配到的名字不在花名册里（防御；正常不该发生）
        node = res.unique_node
        snapshot = ctx.store.state_at(ctx.project_id, node.id, ctx.chapter)
        if snapshot.is_dead or not snapshot.has_appeared():
            if snapshot.is_dead:
                message = f"「{name}」在第 {ctx.chapter} 章已经死了，不该有对话标签。"
                action = "删掉这句对白，或把它改成别人转述/回忆。"
            else:
                first = node.props.first_appears_chapter
                message = (
                    f"「{name}」要到第 {first} 章才登场，"
                    # 「他」不是「它」：R3 只查 Character，而这句话是印给作者看的。
                    f"第 {ctx.chapter} 章不该有他的对话。"
                )
                action = (
                    f"如果他本来就该在这里说话，去他头一回露面的那一段，"
                    f"把那句原文记成他的首次登场；否则删掉第 {ctx.chapter} 章这句对白。"
                )
            issues.append(
                Issue(
                    rule=RULE,
                    issue_type=ISSUE_TYPE,
                    chapter=ctx.chapter,
                    anchor=TextAnchor(
                        para_index=hit.para_index,
                        quote_text=name,
                        occurrence_k=hit.occurrence_k,
                    ),
                    message=message,
                    suggested_action=action,
                )
            )
    return issues
