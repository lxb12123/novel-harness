"""M4 事件记忆与提案聚类的仓储契约。"""

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
    """具体事件仓储失败的基类。"""


class EventReferenceError(EventStoreError):
    """证据或参与引用缺失、跨项目、或标签不对。"""


class EventNotFound(EventStoreError):
    """该事件 id 没有对应已存储的事件。"""


class EventScopeError(EventStoreError, ValueError):
    """对不允许的 information_scope 请求了事件操作。"""


class ProposalStoreError(Exception):
    """具体提案仓储失败的基类。"""


class ProposalValidationError(ProposalStoreError):
    """提案引用的存储状态无法构成自洽的聚类。"""


class ProposalNotFound(ProposalStoreError):
    """该提案 id 没有对应已存储的提案。"""


class ProposalAlreadyResolved(ProposalStoreError):
    """终态提案不能被第二次处理。"""


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
    """仅定义存储边界；提案审阅语义在后续任务引入。"""

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
