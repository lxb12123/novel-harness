"""SQLite implementation of the event-memory repository contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..db import Connection
from ..events.models import (
    EventCharacterRole,
    CharacterProfilePatch,
    CharacterProfileView,
    EventCastEdit,
    EventSummaryVersion,
    EventView,
    ProvisionalEventSpec,
    StoryEvent,
)
from ..events.store import (
    EventCastError,
    EventNotFound,
    EventReferenceError,
    EventScopeError,
    EventStoreError,
)
from ..ids import EntityType, new_id
from . import queries
from .models import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    Node,
    NodeLabel,
    NodeRef,
)
from .sqlite_store import _transaction
from .store import QUERYABLE_SCOPES


def _default_event_id(project_id: str) -> str:
    return new_id(EntityType.EVENT, project_id)


def _row_to_event_summary(row: Any) -> EventSummaryVersion:
    return EventSummaryVersion(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        event_id=str(row["event_id"]),
        source_snapshot_id=(
            None if row["source_snapshot_id"] is None else str(row["source_snapshot_id"])
        ),
        evidence_sha256=(
            None if row["evidence_sha256"] is None else str(row["evidence_sha256"])
        ),
        summary=str(row["summary"]),
        summary_sha256=str(row["summary_sha256"]),
        source=str(row["source"]),
        status=str(row["status"]),
        replaces_version_id=(
            None if row["replaces_version_id"] is None else str(row["replaces_version_id"])
        ),
        created_at=str(row["created_at"]),
    )


class SqliteEventStore:
    def __init__(
        self,
        conn: Connection,
        *,
        event_id_factory: Callable[[str], str] = _default_event_id,
    ) -> None:
        self._conn = conn
        self._new_event_id = event_id_factory

    @staticmethod
    def _check_read(scope: InformationScope, chapter: int) -> None:
        if scope not in QUERYABLE_SCOPES:
            raise EventScopeError(f"scope={scope.value} 不可读；只允许 CANON / PROVISIONAL")
        if chapter < 1:
            raise ValueError(f"章号从 1 起，得到 {chapter}")

    # ── 事件摘要版本（019 / Task 7）：SQL 全在 queries.py，这里只做形状 ──

    def event_summary_current(self, event_id: str) -> EventSummaryVersion | None:
        row = queries.event_summary_current_row(self._conn, event_id)
        return None if row is None else _row_to_event_summary(row)

    def event_summary_history(self, event_id: str) -> list[EventSummaryVersion]:
        return [
            _row_to_event_summary(row)
            for row in queries.event_summary_history_rows(self._conn, event_id)
        ]

    def event_summary_insert(
        self,
        *,
        version_id: str,
        project_id: str,
        event_id: str,
        source_snapshot_id: str | None,
        evidence_sha256: str | None,
        summary: str,
        source: str,
        status: str,
        replaces_version_id: str | None,
    ) -> None:
        queries.insert_event_summary_version(
            self._conn,
            version_id=version_id,
            project_id=project_id,
            event_id=event_id,
            source_snapshot_id=source_snapshot_id,
            evidence_sha256=evidence_sha256,
            summary=summary,
            source=source,
            status=status,
            replaces_version_id=replaces_version_id,
        )

    def event_summary_switch_head(
        self, event_id: str, new_version_id: str, expected: str | None
    ) -> bool:
        return queries.switch_event_summary_head(
            self._conn, event_id, new_version_id, expected
        )

    def create_event_summary_job(
        self,
        *,
        job_id: str,
        project_id: str,
        event_id: str,
        source_snapshot_id: str,
        source_generation: int,
        source_sha256: str,
        expected_head: str | None,
        intent_seq: int,
        trigger_key: str,
    ) -> None:
        queries.create_event_summary_job(
            self._conn,
            job_id=job_id,
            project_id=project_id,
            event_id=event_id,
            source_snapshot_id=source_snapshot_id,
            source_generation=source_generation,
            source_sha256=source_sha256,
            expected_head=expected_head,
            intent_seq=intent_seq,
            trigger_key=trigger_key,
        )

    def event_summary_job_basis(
        self, project_id: str, event_id: str
    ) -> dict[str, Any] | None:
        return queries.event_summary_job_basis(self._conn, project_id, event_id)

    def event_information_scope(self, project_id: str, event_id: str) -> str | None:
        return queries.event_information_scope(self._conn, project_id, event_id)

    def put_provisional(self, spec: ProvisionalEventSpec) -> EventView:
        with _transaction(self._conn):
            try:
                evidence = queries.fetch_evidence(self._conn, spec.evidence_id)
            except LookupError as exc:
                raise EventReferenceError(f"evidence 不存在：{spec.evidence_id}") from exc
            if evidence.project_id != spec.project_id:
                raise EventReferenceError(
                    f"evidence {evidence.id} 属于项目 {evidence.project_id}，不是 {spec.project_id}"
                )
            try:
                event_chapter = queries.event_chapter_from_evidence_audit(
                    self._conn,
                    spec.project_id,
                    evidence.id,
                )
            except LookupError as exc:
                raise EventReferenceError(str(exc)) from exc
            nodes = queries.fetch_nodes(
                self._conn,
                spec.project_id,
                {*spec.participant_ids, *spec.knower_ids},
            )
            expected = (
                (EventCharacterRole.PARTICIPANT, spec.participant_ids, NodeLabel.CHARACTER),
                (EventCharacterRole.KNOWER, spec.knower_ids, NodeLabel.CHARACTER),
            )
            for role, node_ids, label in expected:
                for node_id in sorted(set(node_ids)):
                    node = nodes.get(node_id)
                    if node is None:
                        raise EventReferenceError(f"{role} {node_id} 不在项目 {spec.project_id} 里")
                    if node.label is not label:
                        raise EventReferenceError(
                            f"{role} 必须引用 {label.value}，{node_id} 是 {node.label.value}"
                        )
            existing_id = queries.event_id_by_anchor(
                self._conn,
                spec.project_id,
                spec.evidence_id,
                InformationScope.PROVISIONAL,
            )
            if existing_id is not None:
                existing = self.event(spec.project_id, existing_id)
                if existing is None:
                    raise EventStoreError(
                        f"幂等锚指向不可读事件：project={spec.project_id}, event={existing_id}"
                    )
                return existing
            event_id = self._new_event_id(spec.project_id)
            self._conn.execute(
                """
                INSERT INTO story_event (
                    id, project_id, chapter_number, summary, valid_from_chapter,
                    information_scope, status, confidence, source, evidence_id, evidence_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    spec.project_id,
                    event_chapter,
                    spec.summary,
                    event_chapter,
                    InformationScope.PROVISIONAL.value,
                    EdgeStatus.ACTIVE.value,
                    spec.confidence,
                    EdgeSource.EXTRACTOR.value,
                    evidence.id,
                    EvidenceStatus.FRESH.value,
                ),
            )
            # 018：每个事件出生就有 head 行（current 可为 NULL）。
            self._conn.execute(
                "INSERT INTO event_summary_head (event_id) VALUES (?)", (event_id,)
            )
            participant_ids = sorted(set(spec.participant_ids))
            knower_ids = sorted(set(spec.knower_ids))
            for character_id in participant_ids:
                self._conn.execute(
                    "INSERT INTO event_participant (event_id, project_id, character_id) "
                    "VALUES (?, ?, ?)",
                    (event_id, spec.project_id, character_id),
                )
            for character_id in knower_ids:
                self._conn.execute(
                    """
                    INSERT INTO event_knower (
                        event_id, project_id, character_id, valid_from_chapter,
                        information_scope, evidence_id, evidence_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        spec.project_id,
                        character_id,
                        event_chapter,
                        InformationScope.PROVISIONAL.value,
                        evidence.id,
                        EvidenceStatus.FRESH.value,
                    ),
                )

        return EventView(
            event=StoryEvent(
                id=event_id,
                project_id=spec.project_id,
                chapter_number=event_chapter,
                summary=spec.summary,
                information_scope=InformationScope.PROVISIONAL,
                status=EdgeStatus.ACTIVE,
                confidence=spec.confidence,
                source=EdgeSource.EXTRACTOR,
                evidence_id=evidence.id,
                evidence_status=EvidenceStatus.FRESH,
            ),
            participants=[NodeRef.of(nodes[node_id]) for node_id in participant_ids],
            knowers=[NodeRef.of(nodes[node_id]) for node_id in knower_ids],
        )

    def clone_to_scope(
        self,
        event_id: str,
        scope: InformationScope,
        *,
        summary: str | None = None,
    ) -> EventView:
        if scope not in {InformationScope.CANON, InformationScope.REJECTED}:
            raise EventScopeError(
                f"clone_to_scope 的目标只允许 CANON / REJECTED，得到 {scope.value}"
            )
        if summary is not None and not summary:
            raise ValueError("summary 若提供就不能为空")
        with _transaction(self._conn):
            source = self._conn.execute(
                """
                SELECT project_id, chapter_number, summary, information_scope, status,
                       valid_to_chapter, evidence_id, evidence_status
                FROM story_event
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
            if source is None:
                raise EventNotFound(f"event 不存在：{event_id}")
            if source["information_scope"] != InformationScope.PROVISIONAL.value:
                raise EventScopeError(
                    f"clone_to_scope 的源必须是 PROVISIONAL，{event_id} 是 "
                    f"{source['information_scope']}"
                )
            if (
                source["status"] != EdgeStatus.ACTIVE.value
                or source["evidence_status"] != EvidenceStatus.FRESH.value
                or source["valid_to_chapter"] is not None
            ):
                raise EventStoreError(
                    f"clone_to_scope 的源必须是 ACTIVE / FRESH / 未闭合，"
                    f"{event_id} 是 {source['status']} / {source['evidence_status']} / "
                    f"valid_to={source['valid_to_chapter']}"
                )
            project_id = str(source["project_id"])
            chapter_number = int(source["chapter_number"])
            existing_id = queries.event_id_by_anchor(
                self._conn,
                project_id,
                str(source["evidence_id"]),
                scope,
            )
            if existing_id is not None:
                existing = queries.event_views_at(
                    self._conn,
                    project_id,
                    [existing_id],
                    chapter_number,
                    scope,
                )
                if not existing:
                    raise EventStoreError(
                        f"幂等 clone 锚指向不可读事件：project={project_id}, event={existing_id}"
                    )
                return existing[0]
            event_summary = str(source["summary"]) if summary is None else summary
            clone_id = self._new_event_id(project_id)
            self._conn.execute(
                """
                INSERT INTO story_event (
                    id, project_id, chapter_number, summary,
                    valid_from_chapter, valid_to_chapter, information_scope, status,
                    confidence, source, evidence_id, evidence_status, derived_from_event_id
                )
                SELECT ?, project_id, chapter_number, ?,
                       valid_from_chapter, valid_to_chapter, ?, status,
                       confidence, source, evidence_id, evidence_status, id
                FROM story_event WHERE id = ?
                """,
                (clone_id, event_summary, scope.value, event_id),
            )
            self._conn.execute(
                "INSERT INTO event_summary_head (event_id) VALUES (?)", (clone_id,)
            )
            self._conn.execute(
                """
                INSERT INTO event_participant (event_id, project_id, character_id, status)
                SELECT ?, project_id, character_id, status
                FROM event_participant WHERE event_id = ?
                """,
                (clone_id, event_id),
            )
            self._conn.execute(
                """
                INSERT INTO event_knower (
                    event_id, project_id, character_id, valid_from_chapter,
                    valid_to_chapter, information_scope, status, evidence_id, evidence_status
                )
                SELECT ?, project_id, character_id, valid_from_chapter,
                       valid_to_chapter, ?, status, evidence_id, evidence_status
                FROM event_knower WHERE event_id = ?
                """,
                (clone_id, scope.value, event_id),
            )
            views = queries.event_views_at(
                self._conn,
                project_id,
                [clone_id],
                chapter_number,
                scope,
            )
            if not views:
                raise EventStoreError(
                    f"新 clone 插入后不可读：project={project_id}, event={clone_id}"
                )
            return views[0]

    # ── 作者事后改一条已生效事件的名单（EventCastStore）────────────────────────

    def edit_cast(
        self,
        project_id: str,
        event_id: str,
        *,
        knower_ids: Sequence[str] | None = None,
        participant_ids: Sequence[str] | None = None,
    ) -> EventCastEdit:
        """把这条 CANON 事件的知情/在场名单改成给定的**绝对集合**。

        ── 两条纪律，都是「别让它变成删除」──────────────────────────────────

        1. **删一个人 = 那一行 status 改成 RETRACTED，行留着。** 两张表的语义在这里是
           一样的：这条事实（他知道 / 他在场）从未成立过，抽错了。`event_knower` 从 002
           起就有 status；`event_participant` 的那一列是 005 补的，理由写在那份迁移里。
        2. **加一个人先看有没有那一行**：改回来（RETRACTED → ACTIVE）而不是插第二行。
           插第二行在 `event_knower` 上会直接撞主键，在 `event_participant` 上也会——
           两张表的主键都不含 status。

        **入参里没有章号**（约束 10）。新加的 knower 行的 `valid_from_chapter` 只能是
        事件自己的章号，而那个数一路回到证据（002 的两个触发器把这条钉死：
        `event_knower` 的章必须等于它那条证据的章，且不得早于事件本身）。
        """
        if knower_ids is None and participant_ids is None:
            raise ValueError("edit_cast 至少要给 knower_ids / participant_ids 之一")
        with _transaction(self._conn):
            view = self.event(project_id, event_id)
            if view is None:
                raise EventNotFound(f"event 不存在或跨项目：{event_id}")
            event = view.event
            if event.information_scope is not InformationScope.CANON:
                raise EventScopeError(
                    f"只能改已生效（CANON）的事件，{event_id} 是 {event.information_scope.value}"
                    "——PROVISIONAL 的那条走审阅队列的 edit"
                )
            if (
                event.status is not EdgeStatus.ACTIVE
                or event.evidence_status is not EvidenceStatus.FRESH
            ):
                raise EventStoreError(
                    f"event {event_id} 是 {event.status.value} / {event.evidence_status.value}，"
                    "不是一条还成立、依据也还在的事实"
                )

            current_knowers = {ref.id: ref for ref in view.knowers}
            current_participants = {ref.id: ref for ref in view.participants}
            want_knowers = (
                set(current_knowers) if knower_ids is None else set(knower_ids)
            )
            want_participants = (
                set(current_participants) if participant_ids is None else set(participant_ids)
            )
            refs = self._character_refs(project_id, want_knowers | want_participants)

            knowers_added = sorted(want_knowers - set(current_knowers))
            knowers_removed = sorted(set(current_knowers) - want_knowers)
            participants_added = sorted(want_participants - set(current_participants))
            participants_removed = sorted(set(current_participants) - want_participants)

            for character_id in knowers_added:
                self._add_knower(project_id, event, character_id)
            for character_id in knowers_removed:
                self._retract_knower(project_id, event_id, character_id)
            for character_id in participants_added:
                self._add_participant(project_id, event_id, character_id)
            for character_id in participants_removed:
                self._retract_participant(project_id, event_id, character_id)

            after = queries.event_views_at(
                self._conn,
                project_id,
                [event_id],
                event.chapter_number,
                InformationScope.CANON,
            )
            if not after:
                raise EventStoreError(f"改完之后事件读不回来：{event_id}")
            return EventCastEdit(
                event=after[0],
                knowers_added=tuple(refs[i] for i in knowers_added),
                knowers_removed=tuple(current_knowers[i] for i in knowers_removed),
                participants_added=tuple(refs[i] for i in participants_added),
                participants_removed=tuple(
                    current_participants[i] for i in participants_removed
                ),
            )

    def _character_refs(
        self, project_id: str, node_ids: set[str]
    ) -> dict[str, NodeRef]:
        """名单里的每一个 id 都必须是本项目的 Character，否则整次编辑判死。

        不猜、不跳过：一个悄悄被忽略的 id 的产物是「作者以为他把某人加进去了」，
        而认知矩阵上那一格看起来完全正常。
        """
        if not node_ids:
            return {}
        nodes = queries.fetch_nodes(self._conn, project_id, node_ids)
        out: dict[str, NodeRef] = {}
        for node_id in sorted(node_ids):
            node = nodes.get(node_id)
            if node is None:
                raise EventCastError(f"{node_id} 不在项目 {project_id} 里")
            if node.label is not NodeLabel.CHARACTER:
                raise EventCastError(
                    f"名单里只能是 Character，{node_id} 是 {node.label.value}"
                )
            out[node_id] = NodeRef.of(node)
        return out

    def _add_knower(self, project_id: str, event: StoryEvent, character_id: str) -> None:
        existing = self._conn.execute(
            """
            SELECT status FROM event_knower
            WHERE event_id = ? AND project_id = ? AND character_id = ?
              AND valid_from_chapter = ? AND information_scope = ?
            """,
            (
                event.id,
                project_id,
                character_id,
                event.chapter_number,
                InformationScope.CANON.value,
            ),
        ).fetchone()
        if existing is not None:
            self._conn.execute(
                """
                UPDATE event_knower SET status = ?
                WHERE event_id = ? AND project_id = ? AND character_id = ?
                  AND valid_from_chapter = ? AND information_scope = ?
                """,
                (
                    EdgeStatus.ACTIVE.value,
                    event.id,
                    project_id,
                    character_id,
                    event.chapter_number,
                    InformationScope.CANON.value,
                ),
            )
            return
        self._conn.execute(
            """
            INSERT INTO event_knower (
                event_id, project_id, character_id, valid_from_chapter,
                information_scope, status, evidence_id, evidence_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                project_id,
                character_id,
                # ★ 章号只可能是事件自己的章（= 它那条证据的章）。作者的输入到不了这一行。
                event.chapter_number,
                InformationScope.CANON.value,
                EdgeStatus.ACTIVE.value,
                event.evidence_id,
                EvidenceStatus.FRESH.value,
            ),
        )

    def _retract_knower(self, project_id: str, event_id: str, character_id: str) -> None:
        # 不带任何区间条件：作者说的是「他根本不知道这件事」，不是「他到第 N 章才不知道」。
        # 也因此这里没有第二份时态过滤——`TEMPORAL_WHERE` 仍然只有 queries.py 那一份。
        self._conn.execute(
            """
            UPDATE event_knower SET status = ?
            WHERE event_id = ? AND project_id = ? AND character_id = ?
              AND information_scope = ? AND status = ?
            """,
            (
                EdgeStatus.RETRACTED.value,
                event_id,
                project_id,
                character_id,
                InformationScope.CANON.value,
                EdgeStatus.ACTIVE.value,
            ),
        )

    def _add_participant(self, project_id: str, event_id: str, character_id: str) -> None:
        existing = self._conn.execute(
            "SELECT status FROM event_participant "
            "WHERE event_id = ? AND project_id = ? AND character_id = ?",
            (event_id, project_id, character_id),
        ).fetchone()
        if existing is not None:
            self._conn.execute(
                "UPDATE event_participant SET status = ? "
                "WHERE event_id = ? AND project_id = ? AND character_id = ?",
                (EdgeStatus.ACTIVE.value, event_id, project_id, character_id),
            )
            return
        self._conn.execute(
            "INSERT INTO event_participant (event_id, project_id, character_id, status) "
            "VALUES (?, ?, ?, ?)",
            (event_id, project_id, character_id, EdgeStatus.ACTIVE.value),
        )

    def _retract_participant(
        self, project_id: str, event_id: str, character_id: str
    ) -> None:
        self._conn.execute(
            "UPDATE event_participant SET status = ? "
            "WHERE event_id = ? AND project_id = ? AND character_id = ? AND status = ?",
            (
                EdgeStatus.RETRACTED.value,
                event_id,
                project_id,
                character_id,
                EdgeStatus.ACTIVE.value,
            ),
        )

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self._check_read(scope, chapter)
        event_ids = queries.event_ids_for_characters_at(
            self._conn,
            project_id,
            character_ids,
            chapter,
            scope,
        )
        return queries.event_views_at(
            self._conn,
            project_id,
            event_ids,
            chapter,
            scope,
        )

    def events_for_one_character(
        self,
        project_id: str,
        character_id: str,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[EventView]:
        """**这个人的全部事件**，按章号升序。见 `queries.event_ids_for_one_character`。

        ── 为什么按章分组补名单，而不是再写一条 incidence SQL ────────────────

        每一行的在场 / 知情名单由 `event_views_at` 补，而它按 `:ch` 做时态过滤。
        这里把事件按**它自己的 `valid_from_chapter`** 分组，逐组调一次——于是每一行
        看到的是「这件事在它自己那一章的名单」，而这正是时间线该显示的东西。

        代价是 N 次查询（N = 这个人涉及的**不同章数**，一本 158 章的书上界就是 158，
        IN 列表都很短）。**换来的是 incidence 那段 SQL 全仓仍然只有一份**——
        抄第二份的下场见 `summary_alignment` 那次「报了没做」。这一层是面板读，
        不在写路径上，这个交换是划算的。
        """
        if scope not in QUERYABLE_SCOPES:
            raise EventScopeError(f"scope={scope.value} 不可读；只允许 CANON / PROVISIONAL")
        rows = queries.event_ids_for_one_character(
            self._conn, project_id, character_id, scope
        )
        by_chapter: dict[int, list[str]] = {}
        for event_id, chapter in rows:
            by_chapter.setdefault(chapter, []).append(event_id)
        views: list[EventView] = []
        for chapter, event_ids in by_chapter.items():
            views.extend(
                queries.event_views_at(self._conn, project_id, event_ids, chapter, scope)
            )
        views.sort(key=lambda view: (view.event.chapter_number, view.event.id))
        return views

    def events_for_chapter(
        self,
        project_id: str,
        chapter_number: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self._check_read(scope, chapter_number)
        event_ids = queries.event_ids_at(self._conn, project_id, chapter_number, scope)
        return queries.event_views_at(
            self._conn,
            project_id,
            event_ids,
            chapter_number,
            scope,
        )

    def event(self, project_id: str, event_id: str) -> EventView | None:
        locator = queries.event_locator(self._conn, project_id, event_id)
        if locator is None:
            return None
        self._check_read(locator.information_scope, locator.chapter_number)
        views = queries.event_views_at(
            self._conn,
            project_id,
            [event_id],
            locator.chapter_number,
            locator.information_scope,
        )
        return views[0] if views else None

    def profile(self, project_id: str, character_id: str) -> CharacterProfileView:
        return self._profile_of(self._character(project_id, character_id))

    def _character(self, project_id: str, character_id: str) -> Node:
        node = queries.fetch_node(self._conn, project_id, character_id)
        if node is None:
            raise EventReferenceError(f"Character {character_id} 不在项目 {project_id} 里")
        if node.label is not NodeLabel.CHARACTER:
            raise EventReferenceError(
                f"profile 只接受 Character，{character_id} 是 {node.label.value}"
            )
        return node

    def update_profile(
        self,
        project_id: str,
        character_id: str,
        patch: CharacterProfilePatch,
    ) -> CharacterProfileView:
        with _transaction(self._conn):
            node = self._character(project_id, character_id)
            updated = queries.merge_node_props(
                self._conn,
                node,
                patch.model_dump(exclude_unset=True),
            )
        return self._profile_of(updated)

    @staticmethod
    def _profile_of(node: Node) -> CharacterProfileView:
        return CharacterProfileView(
            character=NodeRef.of(node),
            gender=node.props.gender,
            personality=node.props.personality,
            background=node.props.background,
            character_notes=node.props.character_notes,
            main_character=node.props.main_character,
        )
