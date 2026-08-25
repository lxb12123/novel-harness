"""M4 事件记忆与提案聚类的仓储契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..graph.models import InformationScope
from .models import (
    CharacterProfilePatch,
    CharacterProfileView,
    EventCastEdit,
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


class ProposalObsolete(ProposalStoreError):
    """PENDING 但 `currentness=OBSOLETE`：它锚的那版正文已经不是当前。

    不是作者裁决过（`status` 仍是 PENDING），是正文已经往前走了一版——直接审阅
    返回 409「正文已变化」，历史查询仍读得到（021 / Task 9）。
    """


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

    def events_for_one_character(
        self,
        project_id: str,
        character_id: str,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[EventView]:
        """**这个人的全部事件**，按章号升序。**故意不收 `chapter`。**

        「这个人经历过什么」和「在第 N 章那个时点看他有哪些事」是两个问题。
        前者要整条线，后者是 `events_for_characters`（它收 `chapter`）。
        2026-08-25 的裁定：全给 + 每条带章号，切片交给界面。
        完整论证在 `queries.event_ids_for_one_character`。
        """
        ...

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


class EventCastError(EventStoreError):
    """请求改的名单里有解析不了、跨项目、或者不是 Character 的东西。"""


@runtime_checkable
class EventCastStore(Protocol):
    """改一条**已经生效（CANON）**的事件的在场/知情名单。

    **它故意不是 `EventStore` 的第 8 个方法。** `EventStore` 有一个测试用的 Fake
    （`tests/test_product_context.py`），而 `@runtime_checkable` 只查方法**存在**——
    往 Protocol 上加方法只会逼那个 Fake 长一个什么都不做的存根，然后
    `isinstance(fake, EventStore)` 照样为真。窄接口另开一个，同 `EdgeReviewStore`。

    **入参收的是绝对集合，不是增删两个列表**：作者在 UI 上勾的是「这件事谁在场」，
    发回来的就该是勾完的结果。差集由实现算（它才知道库里现在是什么），于是
    「重发一次同样的请求」天然是个空操作，而不是把同一个人加两遍。
    """

    def edit_cast(
        self,
        project_id: str,
        event_id: str,
        *,
        knower_ids: Sequence[str] | None = None,
        participant_ids: Sequence[str] | None = None,
    ) -> EventCastEdit:
        """`None` = 这一维不动（不是「清空」）。两维都是 `None` 时抛 `ValueError`。"""
        ...


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

    def rebase_to_current(
        self,
        proposal_id: str,
        to_canon_version: int,
    ) -> ProposalRecord: ...

    def attach_decision(self, proposal_id: str, decision_id: str) -> ProposalRecord: ...

    def unaudited(self, project_id: str) -> list[ProposalRecord]: ...
