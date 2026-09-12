"""Prompt 的框：`assemble.py`（冻结）和 `product_assemble.py` 共用的双语词表。

国际化第二批。写作提示本体（`ZH_WRITING_PROMPT`/`EN_WRITING_PROMPT`）早就双语了，
装它的框——【上文】【在场】这些标题、章节行的格式、列表分隔符——一直是写死的中文。

**每个词条在 ZH/EN 两侧都必须有值**：`tests/test_prompt_terms.py` 拿 `PromptTerm`
穷举，漏译一项就地 `KeyError`，红在这份表上，不是红在某个作者的英文书里。

住在 `assemble.py`/`product_assemble.py` 之外，是因为两个文件都要用，
而 `assemble.py` 不许依赖 `product_assemble.py`（后者本来就在导入前者）。
这份表只有纯字符串，不带任何依赖，两边都能安全导入。
"""

from __future__ import annotations

from enum import StrEnum

from .length import DraftLanguage


class PromptTerm(StrEnum):
    """一个词条的键。成员名即穷举——新增一条必须同时进 `_TERMS`。"""

    PRIOR_TEXT = "prior_text"
    PRESENT = "present"
    THIS_SCENE = "this_scene"
    LIST_SEPARATOR = "list_separator"
    CONTINUATION_GOAL = "continuation_goal"

    FOLLOWING_TEXT = "following_text"
    FOLLOWING_TEXT_INSTRUCTION = "following_text_instruction"
    ROLLING_SUMMARY_HEADING = "rolling_summary_heading"
    EARLIER_RELATED_EVENTS = "earlier_related_events"
    RECENT_EVENTS = "recent_events"
    PRESENT_PROFILES = "present_profiles"
    NONE_YET = "none_yet"
    CHAPTER_LINE = "chapter_line"  # .format(chapter=, summary=)

    STORY_MEMORY_HEADING = "story_memory_heading"
    STORY_MEMORY_INTRO = "story_memory_intro"

    PROFILE_DETAIL = "profile_detail"  # .format(label=, value=)
    PROFILE_DETAIL_SEPARATOR = "profile_detail_separator"
    PROFILE_NO_DETAILS = "profile_no_details"
    PROFILE_LINE = "profile_line"  # .format(name=, suffix=)
    PROFILE_LABEL_GENDER = "profile_label_gender"
    PROFILE_LABEL_PERSONALITY = "profile_label_personality"
    PROFILE_LABEL_BACKGROUND = "profile_label_background"
    PROFILE_LABEL_NOTES = "profile_label_notes"

    EVENT_PARTICIPANTS = "event_participants"  # .format(participants=)
    EVENT_LINE = "event_line"  # .format(chapter=, summary=, suffix=)

    STANDING_RULES_HEADING = "standing_rules_heading"
    STANDING_RULE_FORBIDDEN = "standing_rule_forbidden"  # .format(literal=)


_TERMS: dict[PromptTerm, dict[DraftLanguage, str]] = {
    PromptTerm.STANDING_RULES_HEADING: {
        DraftLanguage.ZH: "【作者定下的规矩】（一直有效）",
        DraftLanguage.EN: "[Standing rules from the author] (always in effect)",
    },
    PromptTerm.STANDING_RULE_FORBIDDEN: {
        DraftLanguage.ZH: "- 不要写出「{literal}」。",
        DraftLanguage.EN: '- Do not write "{literal}".',
    },
    PromptTerm.PRIOR_TEXT: {
        DraftLanguage.ZH: "【上文】",
        DraftLanguage.EN: "[Prior text]",
    },
    PromptTerm.PRESENT: {
        DraftLanguage.ZH: "【在场】",
        DraftLanguage.EN: "[Present]",
    },
    PromptTerm.THIS_SCENE: {
        DraftLanguage.ZH: "【这一场要写】",
        DraftLanguage.EN: "[This scene]",
    },
    PromptTerm.LIST_SEPARATOR: {
        DraftLanguage.ZH: "、",
        DraftLanguage.EN: ", ",
    },
    PromptTerm.CONTINUATION_GOAL: {
        DraftLanguage.ZH: "顺着上文往下写，接住作者已经起的头，不要另起一段新情节。",
        DraftLanguage.EN: (
            "Continue from the prior text; pick up the thread the author started. "
            "Do not begin a new plot line."
        ),
    },
    PromptTerm.FOLLOWING_TEXT: {
        DraftLanguage.ZH: "【下文】",
        DraftLanguage.EN: "[Following text]",
    },
    PromptTerm.FOLLOWING_TEXT_INSTRUCTION: {
        DraftLanguage.ZH: (
            "以下是这一章接下来已经写好的正文。不要重写它、不要改动它，"
            "你写的这一段要能自然接上它的开头。"
        ),
        DraftLanguage.EN: (
            "The following prose is already written. Do not rewrite or alter it; "
            "your passage must lead naturally into it."
        ),
    },
    PromptTerm.ROLLING_SUMMARY_HEADING: {
        DraftLanguage.ZH: "【更早章节滚动总结】",
        DraftLanguage.EN: "[Earlier chapter summaries]",
    },
    PromptTerm.EARLIER_RELATED_EVENTS: {
        DraftLanguage.ZH: "【更早的相关事件】",
        DraftLanguage.EN: "[Earlier related events]",
    },
    PromptTerm.RECENT_EVENTS: {
        DraftLanguage.ZH: "【近八章事件】",
        DraftLanguage.EN: "[Events in the last eight chapters]",
    },
    PromptTerm.PRESENT_PROFILES: {
        DraftLanguage.ZH: "【在场人物资料】",
        DraftLanguage.EN: "[Character profiles]",
    },
    PromptTerm.NONE_YET: {
        DraftLanguage.ZH: "- 暂无",
        DraftLanguage.EN: "- none",
    },
    PromptTerm.CHAPTER_LINE: {
        DraftLanguage.ZH: "- 第 {chapter} 章：{summary}",
        DraftLanguage.EN: "- Ch. {chapter}: {summary}",
    },
    PromptTerm.STORY_MEMORY_HEADING: {
        DraftLanguage.ZH: "已生效的故事记忆",
        DraftLanguage.EN: "[Active story memory]",
    },
    PromptTerm.STORY_MEMORY_INTRO: {
        DraftLanguage.ZH: (
            "以下人物资料与事件是当前已生效的记忆（系统自动整理的部分只当线索，"
            "作者亲自确认过的才当既定事实）。"
        ),
        DraftLanguage.EN: (
            "The following character profiles and events are the story memory "
            "currently in effect (parts the system organized automatically are "
            "clues only; only what the author has personally confirmed counts as "
            "settled fact)."
        ),
    },
    PromptTerm.PROFILE_DETAIL: {
        DraftLanguage.ZH: "{label}：{value}",
        DraftLanguage.EN: "{label}: {value}",
    },
    PromptTerm.PROFILE_DETAIL_SEPARATOR: {
        DraftLanguage.ZH: "；",
        DraftLanguage.EN: "; ",
    },
    PromptTerm.PROFILE_NO_DETAILS: {
        DraftLanguage.ZH: "暂无补充资料",
        DraftLanguage.EN: "no additional details yet",
    },
    PromptTerm.PROFILE_LINE: {
        DraftLanguage.ZH: "- {name}：{suffix}",
        DraftLanguage.EN: "- {name}: {suffix}",
    },
    PromptTerm.PROFILE_LABEL_GENDER: {
        DraftLanguage.ZH: "性别",
        DraftLanguage.EN: "Gender",
    },
    PromptTerm.PROFILE_LABEL_PERSONALITY: {
        DraftLanguage.ZH: "性格",
        DraftLanguage.EN: "Personality",
    },
    PromptTerm.PROFILE_LABEL_BACKGROUND: {
        DraftLanguage.ZH: "背景",
        DraftLanguage.EN: "Background",
    },
    PromptTerm.PROFILE_LABEL_NOTES: {
        DraftLanguage.ZH: "备注",
        DraftLanguage.EN: "Notes",
    },
    PromptTerm.EVENT_PARTICIPANTS: {
        DraftLanguage.ZH: "（涉及：{participants}）",
        DraftLanguage.EN: " (involving {participants})",
    },
    PromptTerm.EVENT_LINE: {
        DraftLanguage.ZH: "- 第 {chapter} 章：{summary}{suffix}",
        DraftLanguage.EN: "- Ch. {chapter}: {summary}{suffix}",
    },
}


def term(key: PromptTerm, language: DraftLanguage) -> str:
    """按语言取一个词条。缺一侧的翻译当场 `KeyError`——不静默退回另一种语言。"""
    return _TERMS[key][language]


__all__ = ["PromptTerm", "term"]
