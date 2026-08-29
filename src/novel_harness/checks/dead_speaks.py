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

── 2026-08-28：加了英文那半（`ctx.language == "en"`）─────────────────────

R3 是引擎今天唯一剩下的规则（R2 2026-08-27 因结构性打不着火被砍，ADR 0040），
而它是**闸**不是提示——验证不过整章的总结/抽取直接停（`chapter_refresh.py`）。
所以这条规则的「宁可漏检也不误报」不是偏好，是硬约束（ADR 0005：误报
5–20 条/章 = 作者弃用）。

**中文那半（`SPEAKER_VERBS` / `_VERB_TAIL` / `_name_and_verb_pattern`）
一字节没动**——旁边加了一组独立的英文实现，`check()` 顶部按 `ctx.language`
分流成两条独立路径，各自把命中归约成 `(hit, name)` 对，只在
`_issues_from_named_hits` 这一段汇合（那段不含任何语言相关的字，两个分支
共用是为了不让"作者能读懂的中文措辞"分岔成两份、以后改一处忘了改另一处）。

**判据换了锚点，不是换了严格程度**：中文靠"名字紧贴动词"（`萧决道`，
中间不许有字）；英文这个锚立不住——英文语法要求动词和姓名之间必须有空格，
`saidElizabeth` 不是合法英文。**英文的锚是"紧跟在右引号后面的第一个词"**：
`"……” said Elizabeth`，动词是紧跟引号（+ 那个必然存在的空格）的第一个词，
姓名紧跟动词。方向是动词先、姓名后，不是反过来——`Marcus said, "……"`
这种作者先报名字的写法**故意不识别**：宁可漏掉这一类真违规，也不多认一个
句首大写词（那类漏检不伤人，误报会）。

**姓名右边界（`\\b`）是英文独有的，中文不需要**：中文那边"名字后面紧跟的是
不是那 11 个动词之一"本身是个很窄的字符集，不会有"任意词延伸"的问题；
英文姓名后面理论上能接任何字母，`said Elizabeth` 不加边界会把
`said Elizabethans`（假设的词形）当命中。**实测过这个具体假阳性类别**：
两本 Gutenberg 公版书（*Pride and Prejudice* / *Moby-Dick*，本地跑，不进
仓库）里，动词+姓名一共 435 次真实命中，姓名后面紧跟撇号所有格
（`Elizabeth's mother` 这种——真正说话的是 mother 不是 Elizabeth，`\\b`
单独挡不住这类）的次数是 **0**。所以只加 `\\b` 挡子串延伸，不收紧到"姓名后
必须是标点"（那会连带丢掉 `said Elizabeth quietly.` 这类真命中——两本书里
分别是 8/210 和 18/163，占比不算小）。**这是一个已知但样本内零命中的窄
假阳性类别，不是被彻底堵死的。**

**动词表只收过去式**：`says/asks/…` 这类现在时形式在同一份两本书样本里
——**样本是两本 19 世纪小说，都是过去式叙事**——花名册匹配版命中 0 次；
现在时叙事的书是存在的（当代小说里不算罕见），这两本书的零命中**证明不了**
"现在时不需要"，只说明"这个样本没覆盖到"。等真的撞上现在时叙事的英文书
再补，成本几乎为零（多加一份动词表，锚点/边界逻辑照抄）。

**`message`/`suggested_action` 不跟 `ctx.language` 走，两个分支报出来的
中文措辞完全一样**——这不是偷懒，是故意的：这两个字段该显示成哪种语言，
正确的轴是**读它的作者当前把界面设成哪种语言**，不是被检查的书是什么语言
（同国际化第四批"界面语言独立于书"那条铁律——一个中文界面的作者可能在写
英文小说，他要看的是中文解释）。`Issue` 这个类型今天还没有搬进 code+params
+ 前端渲染那条路（`backendMessages.ts` 那套目前只覆盖通知和部分 4xx），
把 R3 一条规则的措辞单独改成跟书语言走，会现在就骗对、将来接口语言和书
语言不一致时骗错。**这个缺口在加英文匹配之前不存在**（R3 从前只在中文书上
开火，中文解释谈不上是缺口），**从这一刀起才第一次可能出现**"英文书 +
中文解释"同框：等 `Issue` 真的搬进 code+params 那一批时，这里要跟着换成
传 `issue_type`/参数化 key，由前端按作者的界面语言渲染，不是继续加语言判断。
"""

from __future__ import annotations

import re

from ..graph import NodeLabel, Resolution, TextAnchor
from ..text.anchor import Located
from ..text.mentions import find_mentions
from .base import CheckContext, Issue

RULE = "R3"
ISSUE_TYPE = "DEAD_SPEAKS"

# ══════════════════════════════════════════════════════════════════════════
# 中文
# ══════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════
# 英文（2026-08-28，见模块头「加了英文那半」的完整论证与实测数字）
# ══════════════════════════════════════════════════════════════════════════

# 只收过去式，闭合词表——每一个都在两本真实英文小说里实测过有真命中。
# 现在时形式（says/asks/…）同一份样本里零命中，但样本只有两本 19 世纪小说，
# 不代表现在时叙事不需要，见模块头。
SPEAKER_VERBS_EN = (
    r"(?:said|asked|replied|cried|exclaimed|whispered|muttered|"
    r"answered|shouted|added|observed|continued|returned)"
)

# 紧跟在右引号（弯引号/直引号）之后的那一个空格——真实排版是「引号 空格 动词」，
# 不是「引号动词」紧贴。固定宽度 lookbehind（quote 一个字符 + `\s` 一个字符），
# Python re 支持。这是中英语法差异，不是同一个判据换了语言——中文那半原样保留
# 「紧贴」，见上面 SPEAKER_VERBS 那段。
_QUOTE_THEN_SPACE_EN = r"(?<=[”\"]\s)"
_VERB_HEAD_EN = re.compile(r"^" + SPEAKER_VERBS_EN + r"\s+")


def _name_and_verb_pattern_en(surfaces: list[str]) -> re.Pattern[str]:
    """动词+姓名的 alternation（方向和中文相反，见模块头「判据换了锚点」）。

    姓名右边界 `\\b`：挡子串延伸进下一个词（`Elizabeth` 不会吃进
    `Elizabethans`）。不挡撇号所有格（`Elizabeth's`）——实测两本书 435 次
    真命中里这类零出现，收紧成本大于收益，见模块头。
    """
    ordered = sorted(surfaces, key=lambda s: (-len(s), s))
    name_alt = "|".join(re.escape(s) for s in ordered)
    return re.compile(
        _QUOTE_THEN_SPACE_EN + SPEAKER_VERBS_EN + r"\s+(?:" + name_alt + r")\b"
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

    surfaces = [res.surface for res in characters]
    by_surface = {res.surface: res for res in characters}

    named_hits: list[tuple[Located, str]]
    if ctx.language == "en":
        pattern = _name_and_verb_pattern_en(surfaces)
        named_hits = [
            (hit, _VERB_HEAD_EN.sub("", hit.matched_text))
            for hit in find_mentions(ctx.paragraphs, pattern)
        ]
    else:
        pattern = _name_and_verb_pattern(surfaces)
        named_hits = [
            (hit, _VERB_TAIL.sub("", hit.matched_text))
            for hit in find_mentions(ctx.paragraphs, pattern)
        ]

    return _issues_from_named_hits(ctx, named_hits, by_surface)


def _issues_from_named_hits(
    ctx: CheckContext,
    named_hits: list[tuple[Located, str]],
    by_surface: dict[str, Resolution],
) -> list[Issue]:
    """两个语言分支共用的尾段：判断 `is_dead`/`has_appeared`，拼 `Issue`。

    不含任何语言相关的字——中英文两条路径进来之前已经各自把命中归约成
    `(hit, name)` 对，这里往后看不出正文原来是哪种语言写的。`message`/
    `suggested_action` 维持中文硬编码，理由见模块头「message 不跟
    ctx.language 走」那一段。
    """
    issues: list[Issue] = []
    for hit, name in named_hits:
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
