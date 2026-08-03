"""SQLite implementation of the event-memory repository contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..db import Connection
from ..events.models import (
    CharacterProfilePatch,
    CharacterProfileView,
    EventView,
    ProvisionalEventSpec,
    StoryEvent,
)
from ..events.store import (
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
                {*spec.participant_ids, *spec.knower_ids, *spec.revealed_fact_ids},
            )
            expected = (
                ("participant", spec.participant_ids, NodeLabel.CHARACTER),
                ("knower", spec.knower_ids, NodeLabel.CHARACTER),
                ("reveal", spec.revealed_fact_ids, NodeLabel.SECRET),
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
            participant_ids = sorted(set(spec.participant_ids))
            knower_ids = sorted(set(spec.knower_ids))
            reveal_ids = sorted(set(spec.revealed_fact_ids))
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
            for secret_id in reveal_ids:
                self._conn.execute(
                    "INSERT INTO event_reveal (event_id, project_id, secret_id) VALUES (?, ?, ?)",
                    (event_id, spec.project_id, secret_id),
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
            revealed_facts=[NodeRef.of(nodes[node_id]) for node_id in reveal_ids],
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
                SELECT project_id, chapter_number, summary, information_scope, evidence_id
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
                """
                INSERT INTO event_participant (event_id, project_id, character_id)
                SELECT ?, project_id, character_id
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
            self._conn.execute(
                """
                INSERT INTO event_reveal (event_id, project_id, secret_id)
                SELECT ?, project_id, secret_id
                FROM event_reveal WHERE event_id = ?
                """,
                (clone_id, event_id),
            )
        views = queries.event_views_at(
            self._conn,
            project_id,
            [clone_id],
            chapter_number,
            scope,
        )
        return views[0]

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
        node = self._character(project_id, character_id)
        with _transaction(self._conn):
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
