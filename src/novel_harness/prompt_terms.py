"""跨模块的运行时双语消息表（国际化第三批·下半场再下一批）。

`agent/prompt_terms.py` 有一份同形状的表，但那份只给模式二（`agent/` 五个文件）用，
且 `agent/__init__.py` 会连带拉起 `agent/index.py` → `agent/ports.py` →
`advisory_review.py` 这条链——**本模块的消费者里恰好有 `advisory_review.py`
自己**，所以从这儿导入 `agent.prompt_terms` 会成环（`system_notifications.py`
→ `agent` 包初始化 → `agent/index.py` → `agent/ports.py` → `advisory_review.py`
→ 又要导 `system_notifications.py` 自己）。

这份表只给**不属于 `agent/` 的运行时消息**用：通知标题、非模式二的异常文案。
和 `agent/prompt_terms.py`/`draft/prompt_terms.py` 同一个纪律——语言缺一侧
`KeyError`，不静默漏一句中文。

**键故意写的是字面量 `"zh"`/`"en"`，不是 `DraftLanguage.ZH`/`.EN`。**
`DraftLanguage` 是 `StrEnum`（`ZH = "zh"`），两种写法运行时逐字节等价——
`message()` 收一个真的 `DraftLanguage` 实例照样能在这张表里查到，因为
`DraftLanguage.ZH == "zh"` 且哈希相同。但**模块顶层导入 `draft.length` 会成环**：
本模块的消费者之一是 `draft/context.py`（`draft/__init__.py` 眼下的模块导入图
会先拉起 `assemble.py` → `context.py` → 回头 `import prompt_terms`），谁先
初始化决定另一边看到的是不是半成品——`api/app.py` 先导本模块时踩过这个洞
（第一次实现留了 `from .draft.length import DraftLanguage` 在顶层，`api.app`
一 import 就 `ImportError: cannot import name 'message' from partially
initialized module`）。字面量键 + `TYPE_CHECKING` 把这条依赖降成纯标注，
彻底不进运行时，新增消费者不用再担心谁先导的问题。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .draft.length import DraftLanguage

_MESSAGES: dict[str, dict[str, str]] = {
    # ── system_notifications.py ──────────────────────────────────────────
    "import_toc_skipped_title": {
        "zh": (
            "跳过了 {count} 个只有标题、没有正文的章 —— 看起来你的文件里带了一页目录。"
        ),
        "en": (
            "Skipped {count} chapters that had only a heading and no body "
            "text — looks like your file has a table of contents page in it."
        ),
    },
    "validation_blocked_paragraph": {
        "zh": "第 {n} 段",
        "en": "paragraph {n}",
    },
    "validation_blocked_tail": {
        "zh": "新正文不会再自动生成总结与情节。",
        "en": "New prose won't automatically generate summaries or events anymore.",
    },
    "more_items_suffix": {
        "zh": "（另有 {rest} 处）",
        "en": " ({rest} more)",
    },
    # ── extract/runner.py ─────────────────────────────────────────────────
    "extraction_yielded_nothing_why_unresolved": {
        "zh": "它们提到的人在花名册里还认不出来",
        "en": "the people they mention aren't recognized in the roster yet",
    },
    "extraction_yielded_nothing_why_lost": {
        "zh": "它们都没能落库",
        "en": "none of them made it into the book",
    },
    "extraction_yielded_nothing_tail_with_proposals": {
        "zh": (
            "这一次提了 {count} 条待确认，确认之后重新整理这一章，事件才留得下。"
        ),
        "en": (
            "This time it raised {count} items for you to confirm — after "
            "you confirm them, re-run the extraction for this chapter so "
            "the events can stick."
        ),
    },
    "extraction_yielded_nothing_tail_no_roster": {
        "zh": "花名册里先得有人，这一章的事件才留得下。",
        "en": (
            "The roster needs people in it first before this chapter's "
            "events can stick."
        ),
    },
    "extraction_yielded_nothing_title": {
        "zh": "这一章整理完了，但 {lost} 件事一件都没留下 —— {why}。{tail}",
        "en": (
            "This chapter finished processing, but {lost} events didn't "
            "make it in at all — {why}. {tail}"
        ),
    },
    # ── advisory_review.py ────────────────────────────────────────────────
    "clash_conflict_setting": {
        "zh": "设定对不上",
        "en": "setting doesn't match",
    },
    "clash_conflict_timeline": {
        "zh": "时间线对不上",
        "en": "timeline doesn't match",
    },
    "clash_conflict_knowledge": {
        "zh": "谁在什么时候知道什么，对不上",
        "en": "who knew what and when doesn't match",
    },
    "clash_title": {
        "zh": "第 {sentence} 句 ↔ 第 {chapter} 章：{conflict}。{more}",
        "en": "Sentence {sentence} ↔ chapter {chapter}: {conflict}.{more}",
    },
    # ── panel/constraints.py ──────────────────────────────────────────────
    "unresolved_cast_ambiguous": {
        "zh": (
            "第 {chapter} 章的场景里这些称呼解析不出唯一角色：{unresolved}。"
            "请在面板上指定他们是谁——「师兄」在一章里可能指 8 个人，"
            "系统猜错的产物是一个此刻在场的人从这一场的在场名单里静默消失"
        ),
        "en": (
            "In chapter {chapter}'s scene, these names don't resolve to a "
            "single character: {unresolved}. Please specify who they are "
            'on the panel — a name like "senior brother" could mean any '
            "of 8 people in one chapter, and a wrong guess by the system "
            "means someone who's actually present silently vanishes from "
            "this scene's cast list"
        ),
    },
    # ── draft/context.py ──────────────────────────────────────────────────
    "unresolved_cast_no_cast_declared": {
        "zh": (
            "第 {chapter} 章的这一场没有声明在场角色（`cast=`）。"
            "空着的在场名单和「这一场真的没有人」在出参上长得一模一样，"
            "而前者不该被当成后者发给模型。请在场景块里写明这一场有谁"
        ),
        "en": (
            "Chapter {chapter}'s scene doesn't declare who's present "
            "(`cast=`). An empty cast list and \"truly nobody is here\" "
            "look identical in the output, and the former shouldn't be "
            "sent to the model as the latter. Please write who's in this "
            "scene in the scene block"
        ),
    },
    # ── draft/rolling_summary.py ─────────────────────────────────────────
    "summary_text_rejected_empty": {
        "zh": "这一段是空的。要清掉这一章的总结，用「撤回」。",
        "en": (
            'This text is empty. To clear this chapter\'s summary, use '
            '"retract" instead.'
        ),
    },
    "summary_text_rejected_too_long": {
        "zh": (
            "这一段太长了（{length} 字，最多 {max_chars} 字）。"
            "这里是给写作模型看的背景，写得太长会把更早那几章的总结挤出去。"
        ),
        "en": (
            "This text is too long ({length} characters, {max_chars} at "
            "most). This is background the writing model reads — writing "
            "too much here crowds out the summaries of earlier chapters."
        ),
    },
    # ── draft/windows.py ──────────────────────────────────────────────────
    "model_windows_refresh_empty": {
        "zh": "拉回来的内容里一个对话模型都没有，没有覆盖原来那份。",
        "en": (
            "There wasn't a single chat model in what came back, so the "
            "original list was not overwritten."
        ),
    },
    # ── api/app.py ────────────────────────────────────────────────────────
    "chapter_number_at_least_one": {
        "zh": "章号至少是 1",
        "en": "Chapter number must be at least 1",
    },
    "chapter_exists_message": {
        "zh": "这一章刚刚已经被建出来了（另一个窗口？）。刷新一下就能看见它。",
        "en": (
            "This chapter was just created (from another window?). "
            "Refresh and you'll see it."
        ),
    },
    "chapter_missing_message": {
        "zh": "第 {chapter} 章已经不在了。刷新一下就对得上了。",
        "en": (
            "Chapter {chapter} is no longer there. Refresh and things "
            "will line up again."
        ),
    },
    "cast_could_not_resolve_prefix": {
        "zh": "在场角色解析不了：{exc}",
        "en": "Could not resolve who's present: {exc}",
    },
    "model_not_configured": {
        "zh": (
            "模型没配好：{exc} —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，"
            "或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。"
        ),
        "en": (
            'The model isn\'t configured: {exc} — go to the ⚙ "AI '
            'Settings" in the top bar and fill in the endpoint / model / '
            "key, or set NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY."
        ),
    },
    "model_windows_pull_failed": {
        "zh": (
            "没能拉到那份公开的模型表（{exc_type}）。"
            "原来那份还在用，什么都没改。网络好了再试一次。"
        ),
        "en": (
            "Couldn't fetch the public model list ({exc_type}). The "
            "existing list is still in use, nothing changed. Try again "
            "once your network is back."
        ),
    },
}


def message(key: str, language: DraftLanguage, **kwargs: object) -> str:
    """按语言取一条运行时消息，用 `kwargs` 填模板。缺一侧翻译时 `KeyError`——
    和 `agent.prompt_terms.message` 同一个纪律：宁可当场报错，也不让一句中文
    漏进英文书的响应里。
    """
    return _MESSAGES[key][language].format(**kwargs)


__all__ = ["message"]
