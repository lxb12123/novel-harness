"""产品起草用的确定性、仅 CANON 事件记忆。

本模块只消费窄 ``EventStore`` 契约；不直接查 SQLite、不做语义检索——
安全边界是显式的「在场/知情者集合」检查。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from fractions import Fraction
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..events import CharacterProfileView, EventStore, EventView
from ..graph import (
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeRef,
)
from .length import DraftLanguage, count_units
from .rolling_summary import ChapterSummary
from .summarize import SUMMARY_MAX_CHARS


class MemoryBudget(BaseModel):
    """记忆层的**字数**预算，`count_units` 口径（中文数非空白字符）。

    ── 为什么不再是「近八章 / 12 条 / 30 章」──────────────────────────────

    那是三个各自为政的魔法数，**单位还不统一**（章 / 条 / 章）。而「章」根本不是一个单位：
    800 字的章和 5000 字的章都算「1」，于是同一句「近八章」在两本书上给出去的上下文能差六倍。
    换成字数之后只有一个量纲，而且它直接就是要控制的那个量——prompt 有多大。

    ── 砍的顺序（ADR 0019 边界五：Prune Before Summarize）────────────────

    预算不够时从**下往上**砍：滚动总结先走（它本来就是压缩过的、而且丢了能重新生成），
    然后是更早事件，近期事件最后。逐字上文和约束不在这个预算里——它们在 `assemble`，
    优先级更高，**永远不许被记忆层挤掉**。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    recent_events: int = Field(default=8_000, ge=0)
    """【近期事件】。它同时决定**窗口边界**：往回数整章，数到这个预算用完。"""

    background_events: int = Field(default=3_000, ge=0)
    """【更早的相关事件】。"""

    rolling_summaries: int = Field(default=6_000, ge=0)
    """【更早章节滚动总结】。单章摘要上限 120 字，所以 6,000 约等于 50 章。"""

    @classmethod
    def for_context(cls, available_units: int) -> MemoryBudget:
        """把一份**可用额度**按固定比例切三份。

        **比例是形状，额度才是量。** 上面那三个默认值是「不知道模型是谁」时的兜底，
        真跑起来应该走 `memory_units_available()` 从模型的真实窗口倒推——
        1M 窗口的模型和 32k 窗口的模型不该拿同一个绝对数字，那正是「近八章」的老毛病
        换了个单位而已。

        比例 4 : 1 : 2 的理由：近期事件最直接（是「刚发生了什么」），滚动总结第二
        （它覆盖全书、但已经压缩过），更早事件最少（最容易被近期和总结两头覆盖到）。
        """
        available = max(0, available_units)
        return cls(
            recent_events=available * 4 // 7,
            background_events=available * 1 // 7,
            rolling_summaries=available * 2 // 7,
        )

    @property
    def total(self) -> int:
        return self.recent_events + self.background_events + self.rolling_summaries


DEFAULT_MEMORY_BUDGET = MemoryBudget()

TOKENS_PER_UNIT = 2
"""字 → token 的保守换算。**和 `capabilities.plan_call` 里 `max_units * 2` 同一个数**——
那儿已经定过一次，这儿不许再定第二个（中文一个字通常 0.6–1.5 token，取 2 是往贵了算）。"""

MEMORY_CONTEXT_SHARE = Fraction(1, 3)
"""记忆层最多占「窗口减去输出预留」的几分之几。

**这是本模块唯一剩下的自由参数，而它是个比例不是绝对量**——换个模型它自动缩放。
不取满的三个理由：① 逐字上文和约束的优先级高于记忆，得给它们留地方
（ADR 0019 边界五）；② 输入 token 是真金白银，1M 窗口不等于每次都该塞 1M；
③ 塞太满会 lost-in-the-middle，中间那段等于没给。"""

MEMORY_UNITS_CEILING = 60_000
"""再大的窗口也不超过这个字数。**这条是成本闸不是能力闸。**

没有它，一个 1M 窗口的模型每次起草都会被塞进 ~160k 字的记忆——作者会在账单上发现这件事，
而不是在界面上。要放开就调它，它是一个数不是一堆散落的常量。"""


def memory_units_available(
    max_context_tokens: int | None,
    reserved_output_tokens: int,
) -> int:
    """记忆层能用多少**字**，从模型的真实上下文窗口倒推。

    `max_context_tokens is None`（能力表没登记这个模型）→ 回落到 `DEFAULT_MEMORY_BUDGET`
    的总量。**不猜一个大窗口**：猜大了的后果是发出去被供应商拒，而那是最贵的失败时机
    （同 `provider.py` 对配置自洽性的那条理由）。
    """
    if max_context_tokens is None:
        return DEFAULT_MEMORY_BUDGET.total
    headroom = max(0, max_context_tokens - max(0, reserved_output_tokens))
    units = int(Fraction(headroom, TOKENS_PER_UNIT) * MEMORY_CONTEXT_SHARE)
    return min(units, MEMORY_UNITS_CEILING)


class ResolvedProductEvent(EventView):
    """把 ``EventView`` 收窄成深度不可变的参与集合。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    participants: tuple[NodeRef, ...] = ()
    knowers: tuple[NodeRef, ...] = ()

    @classmethod
    def of(cls, view: EventView) -> ResolvedProductEvent:
        return cls(
            event=view.event,
            participants=tuple(view.participants),
            knowers=tuple(view.knowers),
        )


class RollingSummaryView(BaseModel):
    """一章的机器滚动摘要（仅背景，不是作者确认的事实）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    summary: str = Field(min_length=1)


class ResolvedProductContext(BaseModel):
    """允许进入产品写作调用的完整记忆前言。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cast: tuple[NodeRef, ...]
    profiles: tuple[CharacterProfileView, ...]
    recent_from_chapter: int = Field(ge=1)
    """「近期事件」窗口从第几章开始。**由预算倒推，不是写死的章数。**

    出参带着它，是因为调用方（`/summaries` 端点、起草回执）需要**同一个**边界。
    让它们各自再算一遍就是又一个会漂的常量——这个模块刚从三个魔法数里出来。
    """

    recent_events: tuple[ResolvedProductEvent, ...]
    background_events: tuple[ResolvedProductEvent, ...]
    rolling_summaries: tuple[RollingSummaryView, ...] = ()

    @field_validator("recent_events", "background_events", mode="before")
    @classmethod
    def _freeze_event_views(cls, value: object) -> object:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                ResolvedProductEvent.of(item) if isinstance(item, EventView) else item
                for item in value
            )
        return value


def _units(text: str, language: DraftLanguage | str) -> int:
    """全库唯一的计数口径（`draft/length.py`）。**别在这儿另发明一个。**"""
    return count_units(text, language)


def recent_event_boundary(
    events: Sequence[ResolvedProductEvent],
    draft_chapter: int,
    *,
    budget: int,
    language: DraftLanguage | str = DraftLanguage.ZH,
) -> int:
    """「近期事件」窗口的起始章号：**往回数整章，数到字数够为止。**

    ── 为什么落在整章边界上，而不是按字数切开 ────────────────────────────

    按字数切会让「回头给第 15 章补 500 字」把窗口边界挪动一格，于是前缀缓存全失效
    （ADR 0019 边界六）。落在章上，只有真的跨过一整章才失效。

    ── 为什么至少收一章 ──────────────────────────────────────────────────

    一章的事件如果自己就超预算，`spent and ...` 那个短路让它照样进来。否则
    「上一章特别热闹」的结果是**近期窗口为空**——那比超预算糟得多，而且没有任何提示。

    没有事件时返回 `draft_chapter`（空窗口）：早于它的都归「更早」，行为和以前一致。
    """
    if draft_chapter < 1:
        raise ValueError("draft_chapter must be at least 1")
    by_chapter: dict[int, int] = {}
    for view in events:
        number = view.event.chapter_number
        by_chapter[number] = by_chapter.get(number, 0) + _units(view.event.summary, language)

    spent = 0
    start = draft_chapter
    for chapter in sorted(by_chapter, reverse=True):
        cost = by_chapter[chapter]
        if spent and spent + cost > budget:
            break
        spent += cost
        start = chapter
    return start


def _take_from_newest(
    items: Sequence[Any],
    *,
    budget: int,
    text_of: Callable[[Any], str],
    language: DraftLanguage | str,
) -> tuple[Any, ...]:
    """从最新的一端往回收，收到预算用完。**返回仍按原（升序）顺序。**

    倒着收是因为砍要砍最旧的：更早的东西对「接下来写什么」贡献最小。
    """
    kept: list[Any] = []
    spent = 0
    for item in reversed(items):
        cost = _units(text_of(item), language)
        if kept and spent + cost > budget:
            break
        spent += cost
        kept.append(item)
    kept.reverse()
    return tuple(kept)


def summary_window_chapters(budget_units: int) -> int:
    """这一格的字数预算换算成「往回够几章」。**只有这一处做这个换算。**

    单章摘要的上限是 `SUMMARY_MAX_CHARS`（120 字，写死在总结 prompt 里），所以
    「预算 ÷ 120」就是这一格装得下的章数上界。它是**问哪几章要总结**用的范围
    （行内续写的第三个触发源），不是裁剪判据——真正装多少仍由
    `select_rolling_summaries` 按实际字数收，短摘要多的书自然多装几章。
    """
    return max(0, budget_units) // SUMMARY_MAX_CHARS


def select_rolling_summaries(
    summaries: Sequence[ChapterSummary],
    *,
    draft_chapter: int,
    budget: int,
    language: DraftLanguage | str = DraftLanguage.ZH,
) -> tuple[RollingSummaryView, ...]:
    """把「本章之前的那些总结」收成一格，超预算从**最旧**那头砍。

    **整章起草和行内续写共用这一份**（2026-08-22 接续写时提出来的）：两条路
    各写一遍「怎么筛、超了砍哪头」，迟早有一天砍的方向不一样，而症状是
    「同一本书，续写记得的和起草记得的不是同几章」——没有任何东西会红。
    """
    rolling_all = [
        RollingSummaryView(chapter_number=item.chapter_number, summary=item.summary)
        for item in sorted(summaries, key=lambda item: item.chapter_number)
        if item.chapter_number < draft_chapter
    ]
    return _take_from_newest(
        rolling_all,
        budget=budget,
        text_of=lambda item: item.summary,
        language=language,
    )


def _is_writer_safe(
    view: EventView,
    *,
    project_id: str,
    cast_ids: frozenset[str],
    draft_chapter: int,
) -> bool:
    event = view.event
    participant_ids = {participant.id for participant in view.participants}
    knower_ids = {knower.id for knower in view.knowers}
    return (
        event.project_id == project_id
        and event.information_scope is InformationScope.CANON
        and event.status is EdgeStatus.ACTIVE
        and event.evidence_status is EvidenceStatus.FRESH
        and event.chapter_number < draft_chapter
        and bool(participant_ids & cast_ids)
        and cast_ids <= knower_ids
    )


def build_product_context(
    events: EventStore,
    project_id: str,
    cast: Sequence[NodeRef],
    *,
    draft_chapter: int,
    summaries: Sequence[ChapterSummary] = (),
    budget: MemoryBudget = DEFAULT_MEMORY_BUDGET,
    language: DraftLanguage | str = DraftLanguage.ZH,
) -> ResolvedProductContext:
    """为一次产品起草解析档案与滚动的安全事件窗口。

    只向仓储要 ``CANON`` 数据；并在类型化结果上重查一遍安全条件，让实现 bug
    无法把 PROVISIONAL / STALE / 本章 / 部分知情的内容变成给写作模型的断言。
    """

    resolved_cast = tuple(cast)
    if not resolved_cast:
        raise ValueError("cast must contain at least one resolved Character")
    if draft_chapter < 1:
        raise ValueError("draft_chapter must be at least 1")
    if any(character.label is not NodeLabel.CHARACTER for character in resolved_cast):
        raise ValueError("cast must contain only Character references")

    cast_ids = tuple(character.id for character in resolved_cast)
    if len(set(cast_ids)) != len(cast_ids):
        raise ValueError("cast Character ids must be unique")
    cast_id_set = frozenset(cast_ids)

    profiles = tuple(events.profile(project_id, character_id) for character_id in cast_ids)
    if tuple(profile.character.id for profile in profiles) != cast_ids:
        raise ValueError("EventStore returned a profile for the wrong cast Character")

    candidates = events.events_for_characters(
        project_id,
        cast_ids,
        draft_chapter,
        InformationScope.CANON,
    )
    safe = sorted(
        (
            ResolvedProductEvent.of(view)
            for view in candidates
            if _is_writer_safe(
                view,
                project_id=project_id,
                cast_ids=cast_id_set,
                draft_chapter=draft_chapter,
            )
        ),
        key=lambda view: (view.event.chapter_number, view.event.id),
    )

    recent_start = recent_event_boundary(
        safe, draft_chapter, budget=budget.recent_events, language=language
    )
    recent = tuple(view for view in safe if view.event.chapter_number >= recent_start)
    older = [view for view in safe if view.event.chapter_number < recent_start]
    # **总结的窗口和事件的窗口互相独立，故意的。**
    #
    # 曾经是「只取早于 recent_start 的总结」，那让两个窗口耦合在一起，而耦合的方向是坏的：
    # 事件稀疏时（抽取还没跑、或者那几章确实没什么可抽的）边界一路退到第 1 章，
    # 于是**滚动总结一条都进不去**——回到「AI 没有记忆」那个洞，而且是静默的。
    #
    # 两者本来就是互补而不是分层：事件是「发生了一件什么事」，总结是「这一整章讲了什么」。
    # 一章有一条事件不代表它的其余三千字不值得给。少量重叠（近几章同时有事件和总结）
    # 是可接受的代价——单章摘要上限 120 字，而漏掉整章背景的代价大得多。
    return ResolvedProductContext(
        cast=resolved_cast,
        profiles=profiles,
        recent_from_chapter=recent_start,
        recent_events=recent,
        background_events=_take_from_newest(
            older,
            budget=budget.background_events,
            text_of=lambda view: view.event.summary,
            language=language,
        ),
        rolling_summaries=select_rolling_summaries(
            summaries,
            draft_chapter=draft_chapter,
            budget=budget.rolling_summaries,
            language=language,
        ),
    )


__all__ = [
    "DEFAULT_MEMORY_BUDGET",
    "MEMORY_CONTEXT_SHARE",
    "MEMORY_UNITS_CEILING",
    "TOKENS_PER_UNIT",
    "MemoryBudget",
    "ResolvedProductContext",
    "ResolvedProductEvent",
    "RollingSummaryView",
    "build_product_context",
    "memory_units_available",
    "recent_event_boundary",
    "select_rolling_summaries",
    "summary_window_chapters",
]
