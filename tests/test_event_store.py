"""SQLite ``EventStore`` integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import threading
from typing import NamedTuple

import pytest

from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.events import (
    CharacterProfilePatch,
    EventNotFound,
    EventReferenceError,
    EventScopeError,
    EventStore,
    EventStoreError,
    ProvisionalEventSpec,
)
from novel_harness.graph import (
    AliasKind,
    AliasSpec,
    ChapterSpec,
    EdgeSource,
    EdgeStatus,
    EvidenceSpec,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
import novel_harness.graph.queries as graph_queries
from novel_harness.project import create as create_project


class SeededEvent(NamedTuple):
    project_id: str
    graph: SqliteStoryGraph
    character_id: str
    secret_id: str
    location_id: str
    evidence_id: str


def _seed_event(conn: Connection, *, chapter_number: int = 10) -> SeededEvent:
    project_id = create_project(conn, name=f"青云记-{chapter_number}", root_path=".").id
    graph = SqliteStoryGraph(conn)
    character = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="顾清音")
    )
    secret = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.FACTION,
            name="玄铁令来历",
        )
    )
    location = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="渡口")
    )
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=chapter_number,
            heading=f"第{chapter_number}章 渡口",
            path=f"chapters/{chapter_number:04d}.md",
            text="顾清音在渡口交给萧决一枚玄铁令。\n",
        )
    )
    evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=project_id,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=0,
            quote_text="顾清音在渡口交给萧决一枚玄铁令。",
        )
    )
    return SeededEvent(
        project_id,
        graph,
        character.id,
        secret.id,
        location.id,
        evidence.id,
    )


def _add_evidence(
    graph: SqliteStoryGraph,
    project_id: str,
    chapter_number: int,
) -> str:
    quote = f"第{chapter_number}章的新证据。"
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=chapter_number,
            heading=f"第{chapter_number}章",
            path=f"chapters/{chapter_number:04d}.md",
            text=f"{quote}\n",
        )
    )
    return graph.put_evidence(
        EvidenceSpec(
            project_id=project_id,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=0,
            quote_text=quote,
        )
    ).id


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


def test_sqlite_event_store_exposes_the_repository_contract(conn: Connection) -> None:
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:test")

    assert isinstance(store, EventStore)
    assert store.event("project:missing", "event:missing") is None


def test_sqlite_event_store_has_a_production_event_id_factory(conn: Connection) -> None:
    assert isinstance(SqliteEventStore(conn), EventStore)


def test_put_provisional_derives_time_and_fixed_repository_fields(conn: Connection) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:fixed")

    view = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="顾清音交出玄铁令。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.91,
        )
    )

    assert view.event.id == "event:fixed"
    assert view.event.chapter_number == 10
    assert view.event.information_scope is InformationScope.PROVISIONAL
    assert view.event.status is EdgeStatus.ACTIVE
    assert view.event.source is EdgeSource.EXTRACTOR
    assert view.event.evidence_status is EvidenceStatus.FRESH
    assert [node.id for node in view.participants] == [seeded.character_id]
    assert [node.id for node in view.knowers] == [seeded.character_id]


def test_put_provisional_uses_the_immutable_audit_snapshot_chapter(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn, chapter_number=10)
    relocation_target = seeded.graph.put_chapter(
        ChapterSpec(
            project_id=seeded.project_id,
            number=11,
            heading="第11章 新渡口",
            path="chapters/0011.md",
            text="顾清音带着玄铁令去了新渡口。\n",
        )
    )
    conn.execute(
        "UPDATE evidence SET chapter_id = ? WHERE id = ?",
        (relocation_target.id, seeded.evidence_id),
    )
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:audit-chapter")

    view = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="顾清音交出玄铁令。",
            evidence_id=seeded.evidence_id,
            knower_ids=[seeded.character_id],
            confidence=0.91,
        )
    )

    assert view.event.chapter_number == 10
    knower_chapter = conn.execute(
        "SELECT valid_from_chapter FROM event_knower WHERE event_id = ?",
        (view.event.id,),
    ).fetchone()[0]
    assert knower_chapter == 10


def test_put_provisional_rejects_a_non_character_participant_atomically(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:invalid")

    with pytest.raises(EventReferenceError, match="Character"):
        store.put_provisional(
            ProvisionalEventSpec(
                project_id=seeded.project_id,
                summary="顾清音抵达渡口。",
                evidence_id=seeded.evidence_id,
                participant_ids=[seeded.location_id],
                confidence=0.8,
            )
        )

    count = conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    ("field", "match"),
    [
        ("knower_ids", "knower.*Character"),
    ],
)
def test_put_provisional_validates_every_incidence_label(
    conn: Connection,
    field: str,
    match: str,
) -> None:
    seeded = _seed_event(conn)
    invalid_id = seeded.location_id if field == "knower_ids" else seeded.character_id
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:invalid")
    values: dict[str, object] = {
        "project_id": seeded.project_id,
        "summary": "抽取事件。",
        "evidence_id": seeded.evidence_id,
        "confidence": 0.8,
        field: [invalid_id],
    }

    with pytest.raises(EventReferenceError, match=match):
        store.put_provisional(ProvisionalEventSpec.model_validate(values))

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0


def test_put_provisional_rejects_cross_project_evidence_and_nodes(conn: Connection) -> None:
    left = _seed_event(conn, chapter_number=10)
    right = _seed_event(conn, chapter_number=11)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:invalid")

    with pytest.raises(EventReferenceError, match="evidence.*项目"):
        store.put_provisional(
            ProvisionalEventSpec(
                project_id=left.project_id,
                summary="跨项目证据。",
                evidence_id=right.evidence_id,
                confidence=0.8,
            )
        )
    with pytest.raises(EventReferenceError, match="participant.*项目"):
        store.put_provisional(
            ProvisionalEventSpec(
                project_id=left.project_id,
                summary="跨项目人物。",
                evidence_id=left.evidence_id,
                participant_ids=[right.character_id],
                confidence=0.8,
            )
        )

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0


def test_put_provisional_surfaces_missing_evidence_as_a_typed_error(conn: Connection) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:invalid")

    with pytest.raises(EventReferenceError, match="evidence.*不存在"):
        store.put_provisional(
            ProvisionalEventSpec(
                project_id=seeded.project_id,
                summary="没有证据。",
                evidence_id="evidence:missing",
                confidence=0.8,
            )
        )

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 0


def test_event_round_trips_a_typed_view(conn: Connection) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:roundtrip")
    created = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="顾清音交出玄铁令。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.91,
        )
    )

    assert store.event(seeded.project_id, created.event.id) == created
    assert store.event("project:wrong", created.event.id) is None


def test_event_references_survive_alias_addition_and_change(conn: Connection) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:stable-ref")
    created = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="顾清音交出玄铁令。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            confidence=0.91,
        )
    )
    alias = seeded.graph.add_alias(
        AliasSpec(
            project_id=seeded.project_id,
            node_id=seeded.character_id,
            surface="顾姑娘",
            kind=AliasKind.ALIAS,
        )
    )
    conn.execute("UPDATE alias SET surface = '清音姑娘' WHERE id = ?", (alias.id,))

    reloaded = store.event(seeded.project_id, created.event.id)

    assert reloaded == created
    assert reloaded is not None
    assert reloaded.participants[0].id == seeded.character_id


def test_put_provisional_reuses_the_evidence_anchor_without_overwriting(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:first", "event:must-not-be-used"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    first = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="原始摘要。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.91,
        )
    )

    repeated = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="不得覆盖的摘要。",
            evidence_id=seeded.evidence_id,
            confidence=0.1,
        )
    )

    assert repeated == first
    assert repeated.event.summary == "原始摘要。"
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_participant").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_knower").fetchone()[0] == 1


def test_put_provisional_deduplicates_and_sorts_incidence(conn: Connection) -> None:
    seeded = _seed_event(conn)
    second_character = seeded.graph.upsert_node(
        NodeSpec(project_id=seeded.project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:dedup")

    view = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="两人交换秘密。",
            evidence_id=seeded.evidence_id,
            participant_ids=[second_character.id, seeded.character_id, second_character.id],
            knower_ids=[seeded.character_id, second_character.id, seeded.character_id],
            confidence=0.9,
        )
    )

    assert [node.id for node in view.participants] == sorted(
        {seeded.character_id, second_character.id}
    )
    assert [node.id for node in view.knowers] == sorted({seeded.character_id, second_character.id})
    assert conn.execute("SELECT COUNT(*) FROM event_participant").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM event_knower").fetchone()[0] == 2


def test_clone_to_canon_copies_the_hyperedge_and_preserves_the_source(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:source", "event:canon"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="抽取摘要。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.91,
        )
    )

    canon = store.clone_to_scope(
        source.event.id,
        InformationScope.CANON,
        summary="作者确认后的摘要。",
    )

    assert canon.event.id == "event:canon"
    assert canon.event.information_scope is InformationScope.CANON
    assert canon.event.summary == "作者确认后的摘要。"
    assert canon.event.derived_from_event_id == source.event.id
    assert canon.event.chapter_number == source.event.chapter_number
    assert canon.event.evidence_id == source.event.evidence_id
    assert canon.event.confidence == source.event.confidence
    assert canon.event.source == source.event.source
    assert canon.participants == source.participants
    assert canon.knowers == source.knowers
    assert store.event(seeded.project_id, source.event.id) == source
    scopes = conn.execute(
        "SELECT information_scope FROM event_knower ORDER BY information_scope"
    ).fetchall()
    assert [row[0] for row in scopes] == ["CANON", "PROVISIONAL"]


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    [("evidence_status", "STALE"), ("status", "RETRACTED")],
)
@pytest.mark.parametrize("target_scope", [InformationScope.CANON, InformationScope.REJECTED])
def test_clone_to_scope_rejects_unavailable_sources_without_writes(
    conn: Connection,
    column: str,
    invalid_value: str,
    target_scope: InformationScope,
) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:source", "event:must-not-exist"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="不可克隆的抽取摘要。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.9,
        )
    )
    conn.execute(
        f"UPDATE story_event SET {column} = ? WHERE id = ?",
        (invalid_value, source.event.id),
    )

    with pytest.raises(EventStoreError):
        store.clone_to_scope(source.event.id, target_scope)

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_participant").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_knower").fetchone()[0] == 1


def test_clone_to_scope_rolls_back_when_inserted_clone_cannot_be_hydrated(
    conn: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:source", "event:must-roll-back"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="抽取摘要。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            knower_ids=[seeded.character_id],
            confidence=0.9,
        )
    )
    monkeypatch.setattr(graph_queries, "event_views_at", lambda *_args, **_kwargs: [])

    with pytest.raises(EventStoreError, match="不可读"):
        store.clone_to_scope(source.event.id, InformationScope.CANON)

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_participant").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_knower").fetchone()[0] == 1


def test_clone_to_scope_rejects_missing_or_non_provisional_sources(conn: Connection) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:source", "event:canon", "event:unused"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))

    with pytest.raises(EventNotFound, match="event:missing"):
        store.clone_to_scope("event:missing", InformationScope.CANON)

    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="抽取摘要。",
            evidence_id=seeded.evidence_id,
            confidence=0.9,
        )
    )
    canon = store.clone_to_scope(source.event.id, InformationScope.CANON)
    with pytest.raises(EventScopeError, match="PROVISIONAL"):
        store.clone_to_scope(canon.event.id, InformationScope.REJECTED)


@pytest.mark.parametrize(
    "scope",
    [InformationScope.PROVISIONAL, InformationScope.PLANNED],
)
def test_clone_to_scope_accepts_only_terminal_review_targets(
    conn: Connection,
    scope: InformationScope,
) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:source")
    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="抽取摘要。",
            evidence_id=seeded.evidence_id,
            confidence=0.9,
        )
    )

    with pytest.raises(EventScopeError, match="CANON.*REJECTED"):
        store.clone_to_scope(source.event.id, scope)
    with pytest.raises(ValueError, match="summary"):
        store.clone_to_scope(source.event.id, InformationScope.CANON, summary="")

    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 1


def test_rejected_clone_is_idempotent_but_not_publicly_queryable(conn: Connection) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:source", "event:rejected", "event:must-not-be-used"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    source = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="抽取摘要。",
            evidence_id=seeded.evidence_id,
            knower_ids=[seeded.character_id],
            confidence=0.9,
        )
    )

    first = store.clone_to_scope(source.event.id, InformationScope.REJECTED)
    repeated = store.clone_to_scope(
        source.event.id,
        InformationScope.REJECTED,
        summary="不得覆盖。",
    )

    assert first == repeated
    assert first.event.id == "event:rejected"
    assert first.event.summary == "抽取摘要。"
    assert first.event.information_scope is InformationScope.REJECTED
    with pytest.raises(ValueError, match="不可读"):
        store.event(seeded.project_id, first.event.id)
    assert conn.execute("SELECT COUNT(*) FROM story_event").fetchone()[0] == 2


def test_events_for_chapter_obeys_half_open_boundaries_and_scope_isolation(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:provisional", "event:canon"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    provisional = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="第十章事件。",
            evidence_id=seeded.evidence_id,
            confidence=0.9,
        )
    )
    canon = store.clone_to_scope(provisional.event.id, InformationScope.CANON)
    conn.execute(
        "UPDATE story_event SET valid_to_chapter = 143 WHERE project_id = ?",
        (seeded.project_id,),
    )

    assert store.events_for_chapter(seeded.project_id, 9, InformationScope.PROVISIONAL) == []
    assert store.events_for_chapter(seeded.project_id, 10, InformationScope.PROVISIONAL) == [
        provisional
    ]
    assert store.events_for_chapter(seeded.project_id, 142, InformationScope.PROVISIONAL) == [
        provisional
    ]
    assert store.events_for_chapter(seeded.project_id, 143, InformationScope.PROVISIONAL) == []
    assert store.events_for_chapter(seeded.project_id, 142, InformationScope.CANON) == [canon]


def test_events_for_characters_matches_participants_and_current_knowers(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    later_knower = seeded.graph.upsert_node(
        NodeSpec(project_id=seeded.project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:memory")
    event = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="顾清音独自看见玄铁令。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            confidence=0.9,
        )
    )
    later_evidence_id = _add_evidence(seeded.graph, seeded.project_id, 143)
    conn.execute(
        """
        INSERT INTO event_knower (
            event_id, project_id, character_id, valid_from_chapter,
            information_scope, evidence_id, evidence_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event.id,
            seeded.project_id,
            later_knower.id,
            143,
            InformationScope.PROVISIONAL.value,
            later_evidence_id,
            EvidenceStatus.FRESH.value,
        ),
    )

    assert store.events_for_characters(
        seeded.project_id,
        [seeded.character_id],
        142,
        InformationScope.PROVISIONAL,
    ) == [event]
    assert (
        store.events_for_characters(
            seeded.project_id,
            [later_knower.id],
            142,
            InformationScope.PROVISIONAL,
        )
        == []
    )
    learned = store.events_for_characters(
        seeded.project_id,
        [later_knower.id],
        143,
        InformationScope.PROVISIONAL,
    )
    assert [node.id for node in learned[0].knowers] == [later_knower.id]
    conn.execute(
        "UPDATE event_knower SET evidence_status = 'STALE' WHERE character_id = ?",
        (later_knower.id,),
    )
    assert (
        store.events_for_characters(
            seeded.project_id,
            [later_knower.id],
            143,
            InformationScope.PROVISIONAL,
        )
        == []
    )
    assert (
        store.events_for_characters(
            seeded.project_id,
            [],
            143,
            InformationScope.PROVISIONAL,
        )
        == []
    )


@pytest.mark.parametrize("scope", [InformationScope.PLANNED, InformationScope.REJECTED])
def test_event_temporal_reads_reject_invalid_chapters_and_scopes(
    conn: Connection,
    scope: InformationScope,
) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:unused")

    with pytest.raises(EventScopeError, match="CANON.*PROVISIONAL"):
        store.events_for_chapter(seeded.project_id, 10, scope)
    with pytest.raises(EventScopeError, match="CANON.*PROVISIONAL"):
        store.events_for_characters(seeded.project_id, [seeded.character_id], 10, scope)
    with pytest.raises(ValueError, match="章号"):
        store.events_for_chapter(seeded.project_id, 0, InformationScope.CANON)
    with pytest.raises(ValueError, match="章号"):
        store.events_for_characters(
            seeded.project_id,
            [seeded.character_id],
            0,
            InformationScope.CANON,
        )


def test_event_temporal_reads_suppress_stale_and_retracted_events(conn: Connection) -> None:
    seeded = _seed_event(conn)
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:suppressed")
    event = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="会失效的事件。",
            evidence_id=seeded.evidence_id,
            confidence=0.9,
        )
    )

    conn.execute(
        "UPDATE story_event SET evidence_status = 'STALE' WHERE id = ?",
        (event.event.id,),
    )
    assert store.events_for_chapter(seeded.project_id, 10, InformationScope.PROVISIONAL) == []
    assert store.event(seeded.project_id, event.event.id) is None
    conn.execute(
        "UPDATE story_event SET evidence_status = 'FRESH', status = 'RETRACTED' WHERE id = ?",
        (event.event.id,),
    )
    assert store.events_for_chapter(seeded.project_id, 10, InformationScope.PROVISIONAL) == []
    assert store.event(seeded.project_id, event.event.id) is None


def test_temporal_event_lists_use_stable_chapter_then_id_order(conn: Connection) -> None:
    seeded = _seed_event(conn)
    generated = iter(["event:z10", "event:z142", "event:a142"])
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: next(generated))
    first = store.put_provisional(
        ProvisionalEventSpec(
            project_id=seeded.project_id,
            summary="第十章。",
            evidence_id=seeded.evidence_id,
            participant_ids=[seeded.character_id],
            confidence=0.9,
        )
    )
    later = []
    for summary in ("第一条第142章。", "第二条第142章。"):
        later.append(
            store.put_provisional(
                ProvisionalEventSpec(
                    project_id=seeded.project_id,
                    summary=summary,
                    evidence_id=_add_evidence(seeded.graph, seeded.project_id, 142),
                    participant_ids=[seeded.character_id],
                    confidence=0.9,
                )
            )
        )

    expected_ids = [first.event.id, later[1].event.id, later[0].event.id]
    assert [
        view.event.id
        for view in store.events_for_chapter(seeded.project_id, 142, InformationScope.PROVISIONAL)
    ] == expected_ids
    assert [
        view.event.id
        for view in store.events_for_characters(
            seeded.project_id,
            [seeded.character_id],
            142,
            InformationScope.PROVISIONAL,
        )
    ] == expected_ids


def test_profile_returns_only_the_narrow_character_fields(conn: Connection) -> None:
    seeded = _seed_event(conn)
    seeded.graph.upsert_node(
        NodeSpec(
            project_id=seeded.project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(
                gender="女",
                personality="谨慎",
                background="京城",
                first_appears_chapter=3,
                private_note="不得越界",
            ),
        )
    )
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:unused")

    profile = store.profile(seeded.project_id, seeded.character_id)

    assert profile.character.id == seeded.character_id
    assert profile.gender == "女"
    assert profile.personality == "谨慎"
    assert profile.background == "京城"
    assert set(profile.model_dump()) == {
        "character",
        "gender",
        "personality",
        "background",
        "character_notes",
        "main_character",
    }


def test_update_profile_merges_unset_and_explicit_none_without_touching_identity(
    conn: Connection,
) -> None:
    seeded = _seed_event(conn)
    seeded.graph.upsert_node(
        NodeSpec(
            project_id=seeded.project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(
                gender="女",
                personality="谨慎",
                background="京城",
                first_appears_chapter=3,
                private_note="保留",
            ),
        )
    )
    aliases_before = conn.execute(
        "SELECT id, surface FROM alias WHERE node_id = ? ORDER BY id",
        (seeded.character_id,),
    ).fetchall()
    store = SqliteEventStore(conn, event_id_factory=lambda _project_id: "event:unused")

    updated = store.update_profile(
        seeded.project_id,
        seeded.character_id,
        CharacterProfilePatch(
            personality="果断",
            background=None,
            main_character=True,
        ),
    )

    assert updated.gender == "女"
    assert updated.personality == "果断"
    assert updated.background is None
    assert updated.main_character is True
    row = conn.execute(
        """
        SELECT name,
               json_extract(props_json, '$.first_appears_chapter'),
               json_extract(props_json, '$.private_note')
        FROM node WHERE id = ?
        """,
        (seeded.character_id,),
    ).fetchone()
    assert tuple(row) == ("顾清音", 3, "保留")
    aliases_after = conn.execute(
        "SELECT id, surface FROM alias WHERE node_id = ? ORDER BY id",
        (seeded.character_id,),
    ).fetchall()
    assert [tuple(row) for row in aliases_after] == [tuple(row) for row in aliases_before]


def test_update_profile_serializes_concurrent_disjoint_patches(tmp_path: Path) -> None:
    database = tmp_path / "profile-concurrency.sqlite"
    setup = connect(database)
    migrate(setup)
    seeded = _seed_event(setup)
    project_id = seeded.project_id
    character_id = seeded.character_id
    setup.close()

    before_write = threading.Barrier(2)
    failures: list[BaseException] = []
    failures_lock = threading.Lock()

    def apply_patch(patch: CharacterProfilePatch) -> None:
        worker_conn = connect(database)

        def coordinate(statement: str) -> None:
            if statement.strip().upper() == "BEGIN IMMEDIATE":
                before_write.wait(timeout=5)

        worker_conn.set_trace_callback(coordinate)
        try:
            SqliteEventStore(worker_conn).update_profile(project_id, character_id, patch)
        except BaseException as exc:
            with failures_lock:
                failures.append(exc)
        finally:
            worker_conn.close()

    workers = [
        threading.Thread(
            target=apply_patch,
            args=(CharacterProfilePatch(gender="女"),),
            daemon=True,
        ),
        threading.Thread(
            target=apply_patch,
            args=(CharacterProfilePatch(personality="果断"),),
            daemon=True,
        ),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert not failures
    verify_conn = connect(database)
    try:
        profile = SqliteEventStore(verify_conn).profile(project_id, character_id)
    finally:
        verify_conn.close()
    assert profile.gender == "女"
    assert profile.personality == "果断"


def test_profile_requires_a_same_project_character(conn: Connection) -> None:
    seeded = _seed_event(conn, chapter_number=10)
    other = _seed_event(conn, chapter_number=11)
    store = SqliteEventStore(conn)

    for character_id in (seeded.location_id, other.character_id):
        with pytest.raises(EventReferenceError, match="Character"):
            store.profile(seeded.project_id, character_id)
        with pytest.raises(EventReferenceError, match="Character"):
            store.update_profile(
                seeded.project_id,
                character_id,
                CharacterProfilePatch(personality="无效"),
            )
