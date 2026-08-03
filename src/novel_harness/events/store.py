"""Repository contracts for M4 event memory and proposal clusters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..graph.models import InformationScope
from .models import (
    CharacterProfilePatch,
    CharacterProfileView,
    EventView,
    ProposalCreate,
    ProposalRecord,
    ProposalResolutionMark,
    ProvisionalEventSpec,
)


class EventStoreError(Exception):
    """Base class for concrete event-repository failures."""


class EventReferenceError(EventStoreError):
    """An evidence or incidence reference is missing, cross-project, or the wrong label."""


class EventNotFound(EventStoreError):
    """An event id does not identify a stored event."""


class EventScopeError(EventStoreError, ValueError):
    """An event operation was requested for a disallowed information scope."""


class ProposalStoreError(Exception):
    """Base class for concrete proposal-repository failures."""


class ProposalValidationError(ProposalStoreError):
    """A proposal references storage state that cannot form a coherent cluster."""


class ProposalNotFound(ProposalStoreError):
    """A proposal id does not identify a stored proposal."""


class ProposalAlreadyResolved(ProposalStoreError):
    """A terminal proposal cannot be resolved a second time."""


@runtime_checkable
class EventStore(Protocol):
    def put_provisional(self, spec: ProvisionalEventSpec) -> EventView: ...

    def clone_to_scope(
        self,
        event_id: str,
        scope: InformationScope,
        *,
        summary: str | None = None,
    ) -> EventView: ...

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]: ...

    def events_for_chapter(
        self,
        project_id: str,
        chapter_number: int,
        scope: InformationScope,
    ) -> list[EventView]: ...

    def event(self, project_id: str, event_id: str) -> EventView | None: ...

    def profile(self, project_id: str, character_id: str) -> CharacterProfileView: ...

    def update_profile(
        self,
        project_id: str,
        character_id: str,
        patch: CharacterProfilePatch,
    ) -> CharacterProfileView: ...


@runtime_checkable
class ProposalStore(Protocol):
    """Storage boundary only; proposal review semantics are introduced in a later task."""

    def create(self, proposal: ProposalCreate) -> ProposalRecord: ...

    def pending(
        self,
        project_id: str,
        chapter_number: int | None = None,
    ) -> list[ProposalRecord]: ...

    def get(self, project_id: str, proposal_id: str) -> ProposalRecord | None: ...

    def get_by_id(self, proposal_id: str) -> ProposalRecord | None: ...

    def mark_resolved(
        self,
        proposal_id: str,
        resolution: ProposalResolutionMark,
    ) -> ProposalRecord: ...

    def rebase_pending_cohort(
        self,
        resolved_proposal_id: str,
        from_canon_version: int,
        to_canon_version: int,
    ) -> int: ...

    def attach_decision(self, proposal_id: str, decision_id: str) -> ProposalRecord: ...

    def unaudited(self, project_id: str) -> list[ProposalRecord]: ...
