"""Safe, deterministic event memory for product drafting."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

import pytest

from novel_harness.events import CharacterProfileView, EventView, StoryEvent
from novel_harness.graph import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeRef,
)


PID = "project:memory"
ALICE = NodeRef(id="character:alice", label=NodeLabel.CHARACTER, name="顾清音")
BOB = NodeRef(id="character:bob", label=NodeLabel.CHARACTER, name="萧决")
CAROL = NodeRef(id="character:carol", label=NodeLabel.CHARACTER, name="李管家")


def _event(
    event_id: str,
    chapter: int,
    *,
    summary: str | None = None,
    participants: tuple[NodeRef, ...] = (ALICE,),
    knowers: tuple[NodeRef, ...] = (ALICE, BOB),
    scope: InformationScope = InformationScope.CANON,
    status: EdgeStatus = EdgeStatus.ACTIVE,
    evidence_status: EvidenceStatus = EvidenceStatus.FRESH,
) -> EventView:
    return EventView(
        event=StoryEvent(
            id=event_id,
            project_id=PID,
            chapter_number=chapter,
            summary=summary or f"第{chapter}章事件",
            information_scope=scope,
            status=status,
            confidence=0.9,
            source=EdgeSource.EXTRACTOR,
            evidence_id=f"evidence:{event_id}",
            evidence_status=evidence_status,
        ),
        participants=list(participants),
        knowers=list(knowers),
    )


@dataclass
class FakeEventStore:
    events: list[EventView]
    calls: list[tuple[str, tuple[str, ...], int, InformationScope]] = field(
        default_factory=list
    )

    def events_for_characters(
        self,
        project_id: str,
        character_ids: tuple[str, ...],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self.calls.append((project_id, tuple(character_ids), chapter, scope))
        return list(self.events)

    def profile(self, project_id: str, character_id: str) -> CharacterProfileView:
        assert project_id == PID
        character = {ALICE.id: ALICE, BOB.id: BOB}[character_id]
        return CharacterProfileView(
            character=character,
            personality=f"{character.name}的人物性格",
        )


def test_product_context_is_fail_closed_to_safe_prior_canon_events() -> None:
    from novel_harness.draft.product_context import build_product_context

    valid = _event("event:valid", 11, summary="两人共同目睹玄铁令被毁。")
    contaminants = [
        _event("event:same", 12),
        _event("event:future", 13),
        _event("event:no-cast-participant", 10, participants=(CAROL,)),
        _event("event:partial-knowers", 10, knowers=(ALICE,)),
        _event("event:provisional", 10, scope=InformationScope.PROVISIONAL),
        _event("event:retracted", 10, status=EdgeStatus.RETRACTED),
        _event("event:stale", 10, evidence_status=EvidenceStatus.STALE),
    ]
    store = FakeEventStore([*contaminants, valid])

    result = build_product_context(store, PID, (ALICE, BOB), draft_chapter=12)

    assert result.cast == (ALICE, BOB)
    assert tuple(profile.character for profile in result.profiles) == (ALICE, BOB)
    assert tuple(view.event.id for view in result.recent_events) == (valid.event.id,)
    assert result.background_events == ()
    assert store.calls == [(PID, (ALICE.id, BOB.id), 12, InformationScope.CANON)]


def test_recent_window_is_a_word_budget_not_a_chapter_count() -> None:
    """**「近八章」换成了字数预算。**

    章不是一个单位：800 字的章和 5000 字的章都算「1」，于是同一句「近八章」在两本书上
    给出去的上下文能差六倍。现在只有一个量纲，而且它直接就是要控制的那个量。
    """
    from novel_harness.draft.product_context import MemoryBudget, build_product_context

    # 每条事件摘要 5 个中文字（`_event` 造的 summary），一章一条。
    store = FakeEventStore([_event(f"event:{chapter:02d}", chapter) for chapter in range(1, 21)])

    tight = build_product_context(
        store, PID, (ALICE,), draft_chapter=21, budget=MemoryBudget(recent_events=15)
    )
    loose = build_product_context(
        store, PID, (ALICE,), draft_chapter=21, budget=MemoryBudget(recent_events=50)
    )

    # 预算大 → 窗口深。**同一份数据，边界只由预算决定。**
    assert tight.recent_from_chapter > loose.recent_from_chapter
    assert len(tight.recent_events) < len(loose.recent_events)


def test_the_boundary_lands_on_a_whole_chapter() -> None:
    """按字数切会让「回头给第 15 章补 500 字」把边界挪一格、前缀缓存全失效
    （[ADR 0019](../docs/adr/0019-agent-loop-not-graph.md) 边界六）。落在章上只有跨章才失效。
    """
    from novel_harness.draft.product_context import MemoryBudget, build_product_context

    store = FakeEventStore(
        [_event(f"event:{chapter:02d}-{index}", chapter) for chapter in range(1, 11) for index in "ab"]
    )
    result = build_product_context(
        store, PID, (ALICE,), draft_chapter=11, budget=MemoryBudget(recent_events=25)
    )

    kept = {view.event.chapter_number for view in result.recent_events}
    # 每一章要么两条都在，要么两条都不在——**没有半章**。
    assert all(
        sum(1 for view in result.recent_events if view.event.chapter_number == chapter) == 2
        for chapter in kept
    )
    assert min(kept) == result.recent_from_chapter


def test_one_fat_chapter_still_gets_in_rather_than_leaving_an_empty_window() -> None:
    """一章的事件自己就超预算时照样收它。

    否则「上一章特别热闹」的结果是**近期窗口为空**——那比超预算糟得多，而且没有任何提示。
    """
    from novel_harness.draft.product_context import MemoryBudget, build_product_context

    store = FakeEventStore([_event(f"event:9-{index}", 9) for index in range(10)])
    result = build_product_context(
        store, PID, (ALICE,), draft_chapter=10, budget=MemoryBudget(recent_events=1)
    )

    assert len(result.recent_events) == 10
    assert result.recent_from_chapter == 9


def test_rolling_summaries_do_not_depend_on_the_event_window() -> None:
    """**这一条是修一个我自己引入的 bug。**

    曾经是「只取早于事件窗口起点的总结」，于是事件稀疏时（抽取还没跑）边界一路退到第 1 章，
    滚动总结**一条都进不去**——静默地退回「AI 没有记忆」。两者是互补不是分层：
    事件是「发生了一件什么事」，总结是「这一整章讲了什么」。
    """
    from novel_harness.draft.product_context import build_product_context
    from novel_harness.draft.rolling_summary import ChapterSummary

    def summary(chapter: int) -> ChapterSummary:
        return ChapterSummary(
            id=f"summary:{chapter:02d}",
            project_id=PID,
            chapter_id=f"chapter:{chapter:02d}",
            chapter_number=chapter,
            summary=f"第{chapter}章摘要",
            summary_sha256=sha256(f"第{chapter}章摘要".encode("utf-8")).hexdigest(),
            schema_version="chapter-summary-v1",
            prompt_hash="prompt:hash",
            created_at="<ts>",
        )

    summaries = [summary(chapter) for chapter in range(1, 13)]

    # 一条事件都没有：以前这会把边界推到第 1 章，总结全被滤掉。
    empty = build_product_context(
        FakeEventStore([]), PID, (ALICE,), draft_chapter=13, summaries=summaries
    )
    assert [item.chapter_number for item in empty.rolling_summaries] == list(range(1, 13))

    # 事件铺满：总结照样在，允许少量重叠（单章摘要 ≤120 字，漏掉整章背景贵得多）。
    dense = build_product_context(
        FakeEventStore([_event(f"event:{c:02d}", c) for c in range(1, 13)]),
        PID,
        (ALICE,),
        draft_chapter=13,
        summaries=summaries,
    )
    assert [item.chapter_number for item in dense.rolling_summaries] == list(range(1, 13))


def test_older_layers_are_dropped_oldest_first() -> None:
    """砍的顺序：更早的先走。ADR 0019 边界五 Prune Before Summarize 的落点。"""
    from novel_harness.draft.product_context import MemoryBudget, build_product_context
    from novel_harness.draft.rolling_summary import ChapterSummary

    summaries = [
        ChapterSummary(
            id=f"summary:{chapter:03d}",
            project_id=PID,
            chapter_id=f"chapter:{chapter:03d}",
            chapter_number=chapter,
            summary=f"第{chapter}章摘要",
            summary_sha256=sha256(f"第{chapter}章摘要".encode("utf-8")).hexdigest(),
            schema_version="chapter-summary-v1",
            prompt_hash="prompt:hash",
            created_at="<ts>",
        )
        for chapter in range(1, 40)
    ]
    result = build_product_context(
        FakeEventStore([]),
        PID,
        (ALICE,),
        draft_chapter=40,
        summaries=summaries,
        budget=MemoryBudget(rolling_summaries=40),
    )

    kept = [item.chapter_number for item in result.rolling_summaries]
    assert kept, "预算再紧也至少留一条——空着而不说，就是又一个静默的零"
    assert kept[-1] == 39, "留的是**最近**的那些"
    assert kept == sorted(kept), "出参仍按章号升序，不是收集顺序"


@pytest.mark.parametrize(
    ("cast", "chapter", "match"),
    [
        ((), 12, "cast"),
        ((ALICE,), 0, "chapter"),
        ((ALICE, ALICE), 12, "unique"),
    ],
)
def test_product_context_rejects_unsafe_call_shapes(
    cast: tuple[NodeRef, ...], chapter: int, match: str
) -> None:
    from novel_harness.draft.product_context import build_product_context

    with pytest.raises(ValueError, match=match):
        build_product_context(FakeEventStore([]), PID, cast, draft_chapter=chapter)


def test_product_context_output_is_deeply_immutable() -> None:
    from novel_harness.draft.product_context import build_product_context

    result = build_product_context(
        FakeEventStore([_event("event:immutable", 11)]),
        PID,
        (ALICE, BOB),
        draft_chapter=12,
    )

    with pytest.raises(TypeError):
        result.cast[0] = BOB  # type: ignore[index]
    with pytest.raises(Exception):
        result.cast = (BOB,)  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.recent_events[0].knowers[0] = CAROL  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.recent_events[0].participants.clear()  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════════
# 预算从模型的真实窗口倒推，不是写死的字数
# ══════════════════════════════════════════════════════════════════════════


def test_budget_scales_with_the_model_context_window() -> None:
    """**「8000 字」和「近八章」是同一种病，只是换了单位。**

    绝对量不随模型变：1M 窗口的模型和 32k 窗口的模型拿同一个数，前者浪费、后者溢出。
    会缩放的是**比例**。这条钉住「换个模型预算真的跟着变」——
    它退化回常量的时候不会有任何别的东西红。
    """
    from novel_harness.draft.product_context import MemoryBudget, memory_units_available

    small = memory_units_available(32_000, 8_000)
    large = memory_units_available(200_000, 8_000)

    assert small < large, "窗口大的应该拿得多"
    assert MemoryBudget.for_context(small).recent_events < (
        MemoryBudget.for_context(large).recent_events
    )


def test_a_huge_window_is_capped_by_the_cost_gate_not_filled() -> None:
    """1M 窗口不等于每次起草都该塞 1M。

    `MEMORY_UNITS_CEILING` 是**成本闸不是能力闸**——没有它，作者会在账单上
    发现这件事，而不是在界面上。
    """
    from novel_harness.draft.product_context import MEMORY_UNITS_CEILING, memory_units_available

    assert memory_units_available(1_000_000, 8_000) == MEMORY_UNITS_CEILING
    assert memory_units_available(10_000_000, 8_000) == MEMORY_UNITS_CEILING


def test_an_unregistered_model_falls_back_instead_of_guessing_big() -> None:
    """能力表没登记这个模型时**不猜一个大窗口**。

    猜大了的后果是发出去被供应商拒——而那是最贵的失败时机（同 `provider.py`
    对配置自洽性的那条理由：宁可启动就红，不要跑到一半死）。
    """
    from novel_harness.draft.product_context import DEFAULT_MEMORY_BUDGET, memory_units_available

    assert memory_units_available(None, 8_000) == DEFAULT_MEMORY_BUDGET.total


def test_a_tiny_window_does_not_go_negative() -> None:
    """输出预留比整个窗口还大（配置错了）时给 0，不给负数。"""
    from novel_harness.draft.product_context import MemoryBudget, memory_units_available

    assert memory_units_available(4_000, 8_000) == 0
    assert MemoryBudget.for_context(0).total == 0
