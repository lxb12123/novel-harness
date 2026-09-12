"""Strict graph-boundary tests for promoting provisional edges."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.graph import (
    ChapterSpec,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeSpec,
)
from novel_harness.graph.review_store import EdgeReviewStore, EdgeReviewValidationError
from novel_harness.graph.sqlite_review import SqliteEdgeReviewStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


def _seed(conn: Connection):
    project_id = create_project(conn, name="青云记-review-edge", root_path=".").id
    graph = SqliteStoryGraph(conn)
    hero = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="顾清音")
    )
    peer = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    old_place = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="青云城")
    )
    new_place = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="北荒")
    )
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=2,
            heading="第二章",
            path="chapters/0002.md",
            text="顾清音离开青云城，踏入北荒。\n她与萧决结为盟友。\n",
        )
    )
    location_evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=project_id,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=0,
            quote_text="顾清音离开青云城，踏入北荒。",
        )
    )
    relation_evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=project_id,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=1,
            quote_text="她与萧决结为盟友。",
        )
    )
    provisional_location = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=new_place.id,
            type=EdgeType.LOCATED_AT,
            props=EdgeProps(value="北荒"),
            valid_from_chapter=2,
            information_scope=InformationScope.PROVISIONAL,
            confidence=0.63,
            source=EdgeSource.EXTRACTOR,
            evidence_id=location_evidence.id,
        )
    ).edge
    provisional_relation = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=peer.id,
            type=EdgeType.RELATED_TO,
            props=EdgeProps(value="盟友"),
            valid_from_chapter=2,
            information_scope=InformationScope.PROVISIONAL,
            confidence=0.88,
            source=EdgeSource.EXTRACTOR,
            evidence_id=relation_evidence.id,
        )
    ).edge
    return (
        project_id,
        graph,
        hero,
        peer,
        old_place,
        new_place,
        provisional_location,
        provisional_relation,
    )


def test_hydrate_preserves_requested_order_and_returns_typed_nodes(conn: Connection) -> None:
    project_id, graph, _hero, _peer, _old, _new, location, relation = _seed(conn)
    store = SqliteEdgeReviewStore(conn, graph)

    hydrated = store.hydrate_provisional(project_id, [relation.id, location.id])

    assert isinstance(store, EdgeReviewStore)
    assert [item.edge.id for item in hydrated] == [relation.id, location.id]
    assert hydrated[0].src.name in {"顾清音", "萧决"}
    assert hydrated[0].dst.name in {"顾清音", "萧决"}
    assert hydrated[1].src.name == "顾清音"
    assert hydrated[1].dst.name == "北荒"


@pytest.mark.parametrize("ids", [[], ["same", "same"]], ids=["empty", "duplicate"])
def test_hydrate_rejects_empty_or_duplicate_selection(
    conn: Connection, ids: list[str]
) -> None:
    project_id, graph, *_ = _seed(conn)
    with pytest.raises(EdgeReviewValidationError):
        SqliteEdgeReviewStore(conn, graph).hydrate_provisional(project_id, ids)


def test_hydrate_rejects_missing_cross_project_non_provisional_and_invalid_lifecycle(
    conn: Connection,
) -> None:
    project_id, graph, _hero, _peer, _old, _new, location, relation = _seed(conn)
    other_id = create_project(conn, name="别书", root_path=".").id
    store = SqliteEdgeReviewStore(conn, graph)

    with pytest.raises(EdgeReviewValidationError):
        store.hydrate_provisional(project_id, ["edge:missing"])
    with pytest.raises(EdgeReviewValidationError):
        store.hydrate_provisional(other_id, [location.id])

    canon = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=relation.src,
            dst=relation.dst,
            type=relation.type,
            props=relation.props,
            valid_from_chapter=relation.valid_from_chapter,
            information_scope=InformationScope.CANON,
            evidence_id=relation.evidence_id,
        )
    ).edge
    with pytest.raises(EdgeReviewValidationError):
        store.hydrate_provisional(project_id, [canon.id])

    conn.execute("UPDATE edge SET evidence_status = 'STALE' WHERE id = ?", (location.id,))
    with pytest.raises(EdgeReviewValidationError):
        store.hydrate_provisional(project_id, [location.id])
    conn.execute("UPDATE edge SET evidence_status = 'FRESH' WHERE id = ?", (location.id,))
    conn.execute("UPDATE edge SET status = ? WHERE id = ?", (EdgeStatus.RETRACTED.value, location.id))
    with pytest.raises(EdgeReviewValidationError):
        store.hydrate_provisional(project_id, [location.id])


def test_clone_preserves_edge_fields_supersedes_canon_and_keeps_provisional(
    conn: Connection,
) -> None:
    project_id, graph, hero, _peer, old_place, _new, location, _relation = _seed(conn)
    graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=1,
            information_scope=InformationScope.CANON,
        )
    ).edge

    (promoted,) = SqliteEdgeReviewStore(conn, graph).clone_to_canon(
        project_id, [location.id]
    )

    assert promoted.edge.information_scope is InformationScope.CANON
    assert promoted.edge.props == location.props
    assert promoted.edge.valid_from_chapter == location.valid_from_chapter
    assert promoted.edge.confidence == location.confidence
    assert promoted.edge.source is location.source
    assert promoted.edge.evidence_id == location.evidence_id
    # 升 CANON 之后，第 2 章那条接管；第 1 章那条**原样留着**，只是不再是当前值。
    # （2026-09-06 之前这里断言的是 `old_canon.valid_to_chapter == 2`——
    #  那一列随 ADR 0043 下线，「谁盖住谁」改在读的时候算，所以改成断言行为。）
    assert graph.state_at(project_id, hero.id, 1).location.id == old_place.id
    assert graph.state_at(project_id, hero.id, 2).location.id == promoted.edge.dst
    source = conn.execute(
        "SELECT information_scope, status, valid_to_chapter FROM edge WHERE id = ?",
        (location.id,),
    ).fetchone()
    assert tuple(source) == ("PROVISIONAL", "ACTIVE", None)


def test_clone_validates_whole_batch_before_writing_and_can_promote_a_subset(
    conn: Connection,
) -> None:
    project_id, graph, *_prefix, location, relation = _seed(conn)
    store = SqliteEdgeReviewStore(conn, graph)

    with pytest.raises(EdgeReviewValidationError):
        store.clone_to_canon(project_id, [location.id, "edge:missing"])
    assert conn.execute(
        "SELECT COUNT(*) FROM edge WHERE project_id = ? AND information_scope = 'CANON'",
        (project_id,),
    ).fetchone()[0] == 0

    (only_relation,) = store.clone_to_canon(project_id, [relation.id])
    assert only_relation.edge.type is EdgeType.RELATED_TO
    canon_source_ids = {
        row[0]
        for row in conn.execute(
            "SELECT evidence_id FROM edge WHERE project_id = ? AND information_scope = 'CANON'",
            (project_id,),
        ).fetchall()
    }
    assert canon_source_ids == {relation.evidence_id}

