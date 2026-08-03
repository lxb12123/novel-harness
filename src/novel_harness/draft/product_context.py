"""Deterministic, Canon-only event memory for product drafting.

This module consumes the narrow ``EventStore`` contract.  It does not query SQLite directly and
does not perform semantic retrieval: the safety boundary is an explicit cast/knower set check.
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
    """An ``EventView`` narrowed to deeply immutable incidence collections."""

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
    """The complete memory preface allowed to reach a product writer call."""

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
    """Resolve profiles and the rolling safe event window for one product draft.

    The repository is asked only for ``CANON`` data.  The checks are repeated on the typed result
    so an implementation bug cannot turn provisional, stale, same-chapter, or partially-known
    material into a writer assertion.
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
