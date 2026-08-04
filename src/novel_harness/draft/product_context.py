"""产品起草用的确定性、仅 CANON 事件记忆。

本模块只消费窄 ``EventStore`` 契约；不直接查 SQLite、不做语义检索——
安全边界是显式的「在场/知情者集合」检查。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, field_validator

from ..events import CharacterProfileView, EventStore, EventView
from ..graph import (
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeRef,
)

RECENT_CHAPTERS = 8
OLDER_EVENT_LIMIT = 12


class ResolvedProductEvent(EventView):
    """把 ``EventView`` 收窄成深度不可变的参与集合。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    participants: tuple[NodeRef, ...] = ()
    knowers: tuple[NodeRef, ...] = ()
    revealed_facts: tuple[NodeRef, ...] = ()

    @classmethod
    def of(cls, view: EventView) -> ResolvedProductEvent:
        return cls(
            event=view.event,
            participants=tuple(view.participants),
            knowers=tuple(view.knowers),
            revealed_facts=tuple(view.revealed_facts),
        )


class ResolvedProductContext(BaseModel):
    """允许进入产品写作调用的完整记忆前言。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cast: tuple[NodeRef, ...]
    profiles: tuple[CharacterProfileView, ...]
    recent_events: tuple[ResolvedProductEvent, ...]
    background_events: tuple[ResolvedProductEvent, ...]

    @field_validator("recent_events", "background_events", mode="before")
    @classmethod
    def _freeze_event_views(cls, value: object) -> object:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                ResolvedProductEvent.of(item) if isinstance(item, EventView) else item
                for item in value
            )
        return value


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

    recent_start = max(1, draft_chapter - RECENT_CHAPTERS)
    recent = tuple(view for view in safe if view.event.chapter_number >= recent_start)
    older = [view for view in safe if view.event.chapter_number < recent_start]

    return ResolvedProductContext(
        cast=resolved_cast,
        profiles=profiles,
        recent_events=recent,
        background_events=tuple(older[-OLDER_EVENT_LIMIT:]),
    )


__all__ = [
    "OLDER_EVENT_LIMIT",
    "RECENT_CHAPTERS",
    "ResolvedProductContext",
    "ResolvedProductEvent",
    "build_product_context",
]
