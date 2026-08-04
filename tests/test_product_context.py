"""Safe, deterministic event memory for product drafting."""

from __future__ import annotations

from dataclasses import dataclass, field

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


def test_product_context_uses_previous_eight_chapters_and_twelve_nearest_older_events() -> None:
    from novel_harness.draft.product_context import build_product_context

    # draft ch20 -> recent is ch12..19. Older candidates deliberately arrive in reverse order.
    recent = [_event(f"event:r{chapter:02d}", chapter) for chapter in range(12, 20)]
    older = [_event(f"event:o{index:02d}", 1 + index // 2) for index in range(18)]
    store = FakeEventStore(list(reversed([*older, *recent])))

    result = build_product_context(store, PID, (ALICE, BOB), draft_chapter=20)

    assert [(item.event.chapter_number, item.event.id) for item in result.recent_events] == [
        (chapter, f"event:r{chapter:02d}") for chapter in range(12, 20)
    ]
    expected_older = sorted(older, key=lambda item: (item.event.chapter_number, item.event.id))[-12:]
    assert tuple(view.event.id for view in result.background_events) == tuple(
        view.event.id for view in expected_older
    )


def test_product_context_takes_rolling_summaries_only_before_the_recent_window() -> None:
    from novel_harness.draft.product_context import build_product_context
    from novel_harness.draft.rolling_summary import ROLLING_WINDOW, ChapterSummary

    store = FakeEventStore([_event(f"event:{chapter:02d}", chapter) for chapter in range(1, 13)])
    summaries = [
        ChapterSummary(
            id=f"summary:{chapter:02d}",
            project_id=PID,
            chapter_number=chapter,
            summary=f"第{chapter}章摘要",
            schema_version="chapter-summary-v1",
            prompt_hash="prompt:hash",
            created_at="<ts>",
        )
        for chapter in range(1, 13)
    ]

    result = build_product_context(
        store, PID, (ALICE,), draft_chapter=13, summaries=summaries
    )

    # draft ch13 的近八章是 ch5..12；滚动总结只覆盖更早的 ch1..4。
    assert [item.chapter_number for item in result.rolling_summaries] == [1, 2, 3, 4]

    # 超过 ROLLING_WINDOW 时只保留最晚的 30 章。
    many = [
        ChapterSummary(
            id=f"summary:{chapter:03d}",
            project_id=PID,
            chapter_number=chapter,
            summary=f"第{chapter}章摘要",
            schema_version="chapter-summary-v1",
            prompt_hash="prompt:hash",
            created_at="<ts>",
        )
        for chapter in range(1, 40)
    ]
    wide = build_product_context(store, PID, (ALICE,), draft_chapter=40, summaries=many)
    assert len(wide.rolling_summaries) == ROLLING_WINDOW
    assert wide.rolling_summaries[0].chapter_number == 2
    assert wide.rolling_summaries[-1].chapter_number == 31


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
