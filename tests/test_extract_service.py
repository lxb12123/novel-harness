"""Transactional ingestion of validated chapter analysis into M4 memory."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from novel_harness.db import Connection, connect, migrate
from novel_harness.events import CharacterProfilePatch, ProposalCreate
from novel_harness.extract import (
    RawChapterAnalysis,
    RawCharacterProfile,
    RawEvent,
    RawStateUpdate,
)
from novel_harness.extract.service import DiscardOutcome, ExtractionService
from novel_harness.graph import (
    AliasSpec,
    ChapterSpec,
    ChapterText,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


EVENT_QUOTE = "顾清音在渡口把玄铁令交给萧决。"
LOCATION_QUOTE = "萧决看见顾清音仍然停留在渡口。"
STATE_QUOTE = "顾清音的修为终于突破到了金丹境界。"
RELATION_QUOTE = "顾清音与萧决正式结成了生死盟友。"
REPEATED_QUOTE = "夜风吹过空无一人的青石长街。"
CHAPTER_TEXT = "\n".join(
    (
        EVENT_QUOTE,
        LOCATION_QUOTE,
        STATE_QUOTE,
        RELATION_QUOTE,
        REPEATED_QUOTE,
        REPEATED_QUOTE,
    )
) + "\n"


@dataclass(frozen=True)
class Seed:
    project_id: str
    graph: SqliteStoryGraph
    chapter: ChapterText
    hero_id: str
    sidekick_id: str
    harbor_id: str
    mountain_id: str
    dimension_id: str
    secret_id: str


@pytest.fixture
def conn(tmp_path) -> Iterator[Connection]:
    connection = connect(tmp_path / "extract-service.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def seed(conn: Connection) -> Seed:
    project_id = create_project(conn, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(conn)
    hero = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(gender="女", main_character=True),
        )
    )
    sidekick = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    harbor = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="渡口")
    )
    mountain = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="北荒")
    )
    dimension = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.STATE_DIM,
            name="修为",
            props=NodeProps(dim_key="cultivation"),
        )
    )
    graph.add_alias(AliasSpec(project_id=project_id, node_id=dimension.id, surface="修为"))
    secret = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.SECRET,
            name="玄铁令来历",
            secret=SecretDetail(),
        )
    )
    graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=7,
            heading="第七章 渡口",
            path="chapters/0007.md",
            text=CHAPTER_TEXT,
        )
    )
    return Seed(
        project_id=project_id,
        graph=graph,
        chapter=graph.current_snapshots(project_id)[0],
        hero_id=hero.id,
        sidekick_id=sidekick.id,
        harbor_id=harbor.id,
        mountain_id=mountain.id,
        dimension_id=dimension.id,
        secret_id=secret.id,
    )


def _event(
    *,
    quote: str = EVENT_QUOTE,
    confidence: float = 0.91,
    participants: tuple[str, ...] = ("顾清音", "萧决"),
    knowers: tuple[str, ...] = ("顾清音",),
    revealed_facts: tuple[str, ...] = ("玄铁令来历",),
) -> RawEvent:
    return RawEvent(
        summary="顾清音交出玄铁令。",
        quote=quote,
        participants=participants,
        knowers=knowers,
        revealed_facts=revealed_facts,
        confidence=confidence,
    )


def _analysis(
    *,
    events: tuple[RawEvent, ...] | None = None,
    states: tuple[RawStateUpdate, ...] = (),
    profiles: tuple[RawCharacterProfile, ...] = (),
) -> RawChapterAnalysis:
    return RawChapterAnalysis(
        events=events or (_event(),),
        state_updates=states,
        character_profiles=profiles,
    )


def _service(conn: Connection, seed: Seed, *, proposals=None) -> ExtractionService:
    return ExtractionService(
        conn=conn,
        graph=seed.graph,
        event_store=SqliteEventStore(conn),
        proposal_store=proposals or SqliteProposalStore(conn),
    )


def test_ingest_persists_one_unique_event_with_source_evidence(seed: Seed, conn: Connection) -> None:
    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(), prompt_hash="prompt:one"
    )

    assert (report.valid_event_count, report.discarded_event_count) == (1, 0)
    assert (report.valid_state_update_count, report.proposal_count) == (0, 0)
    view = SqliteEventStore(conn).event(seed.project_id, report.event_ids[0])
    assert view is not None
    assert [node.id for node in view.participants] == [seed.hero_id, seed.sidekick_id]
    assert [node.id for node in view.knowers] == [seed.hero_id]
    assert [node.id for node in view.revealed_facts] == [seed.secret_id]
    evidence = seed.graph.get_evidence(seed.project_id, view.event.evidence_id)
    assert evidence is not None
    assert evidence.audit.quote_text == EVENT_QUOTE
    assert evidence.audit.chapter_snapshot_id == seed.chapter.snapshot_id


def test_ingest_maps_all_three_state_shapes_to_provisional_edges(
    seed: Seed, conn: Connection
) -> None:
    states = (
        RawStateUpdate(
            kind="location", subject="顾清音", object="渡口",
            quote=LOCATION_QUOTE, confidence=0.95,
        ),
        RawStateUpdate(
            kind="state", subject="顾清音", dimension="修为", value="金丹",
            quote=STATE_QUOTE, confidence=0.96,
        ),
        RawStateUpdate(
            kind="relationship", subject="顾清音", object="萧决", value="生死盟友",
            quote=RELATION_QUOTE, confidence=0.97,
        ),
    )
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(states=states),
        prompt_hash="prompt:states",
    )

    assert report.valid_state_update_count == 3
    assert len(report.edge_ids) == 3
    snapshot = seed.graph.state_at(
        seed.project_id,
        seed.hero_id,
        seed.chapter.number,
        scope=InformationScope.PROVISIONAL,
    )
    by_type = {edge.type: edge for edge in snapshot.edges}
    assert snapshot.location is not None and snapshot.location.id == seed.harbor_id
    assert by_type[EdgeType.LOCATED_AT].source is EdgeSource.EXTRACTOR
    assert by_type[EdgeType.HAS_STATE].dst == seed.dimension_id
    assert by_type[EdgeType.HAS_STATE].props.value == "金丹"
    relation = by_type[EdgeType.RELATED_TO]
    assert relation.peer_of(seed.hero_id) == seed.sidekick_id
    assert relation.props.value == "生死盟友"
    assert report.proposal_count == 0


def test_ambiguous_and_below_threshold_quotes_are_discarded_with_reasons(
    seed: Seed, conn: Connection
) -> None:
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(events=(
            _event(quote=REPEATED_QUOTE),
            _event(quote="这是一段完全不存在于本章正文里的虚构引语。"),
        )),
        prompt_hash="prompt:bad-quotes",
    )

    assert (report.valid_event_count, report.discarded_event_count) == (0, 2)
    assert [reason.outcome for reason in report.discarded] == [
        DiscardOutcome.AMBIGUOUS,
        DiscardOutcome.BELOW_THRESHOLD,
    ]
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_canon_state_conflicts_cluster_into_one_linked_proposal(
    seed: Seed, conn: Connection
) -> None:
    seed.graph.upsert_edge(EdgeSpec(
        project_id=seed.project_id, src=seed.hero_id, dst=seed.mountain_id,
        type=EdgeType.LOCATED_AT, valid_from_chapter=1,
        information_scope=InformationScope.CANON,
    ))
    seed.graph.upsert_edge(EdgeSpec(
        project_id=seed.project_id, src=seed.hero_id, dst=seed.dimension_id,
        type=EdgeType.HAS_STATE, props=EdgeProps(value="筑基"),
        valid_from_chapter=1, information_scope=InformationScope.CANON,
    ))
    states = (
        RawStateUpdate(
            kind="location", subject="顾清音", object="渡口",
            quote=LOCATION_QUOTE, confidence=0.94,
        ),
        RawStateUpdate(
            kind="state", subject="顾清音", dimension="修为", value="金丹",
            quote=STATE_QUOTE, confidence=0.93,
        ),
    )
    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(states=states),
        prompt_hash="prompt:conflicts",
    )

    assert report.proposal_count == 1
    proposal = SqliteProposalStore(conn).pending(seed.project_id)[0]
    assert proposal.kind == "edge_conflict"
    assert proposal.edge_ids == sorted(report.edge_ids)
    assert proposal.item_count == 2
    assert {item["proposed"]["quote"] for item in proposal.items} == {
        LOCATION_QUOTE, STATE_QUOTE,
    }
    assert all(item["current"]["edge_id"] for item in proposal.items)
    canon = seed.graph.state_at(seed.project_id, seed.hero_id, seed.chapter.number)
    assert canon.location is not None and canon.location.id == seed.mountain_id
    assert canon.states[0].value == "筑基"


def test_low_confidence_main_character_items_cluster_and_point_seven_is_not_low(
    seed: Seed, conn: Connection
) -> None:
    low_state = RawStateUpdate(
        kind="location", subject="顾清音", object="渡口",
        quote=LOCATION_QUOTE, confidence=0.69,
    )
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(events=(_event(confidence=0.69),), states=(low_state,)),
        prompt_hash="prompt:low",
    )
    proposal = SqliteProposalStore(conn).pending(seed.project_id)[0]
    assert proposal.kind == "low_confidence_main"
    assert proposal.event_ids == list(report.event_ids)
    assert proposal.edge_ids == list(report.edge_ids)
    assert proposal.item_count == 2

    # A separate project proves exactly 0.70 is not low.
    pid = create_project(conn, name="边界", root_path="boundary").id
    graph = SqliteStoryGraph(conn)
    graph.upsert_node(NodeSpec(
        project_id=pid, label=NodeLabel.CHARACTER, name="顾清音",
        props=NodeProps(main_character=True),
    ))
    graph.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name="萧决"))
    graph.upsert_node(NodeSpec(
        project_id=pid, label=NodeLabel.SECRET, name="玄铁令来历", secret=SecretDetail(),
    ))
    graph.put_chapter(ChapterSpec(
        project_id=pid, number=7, heading="第七章", path="boundary/0007.md",
        text=EVENT_QUOTE + "\n",
    ))
    boundary = ExtractionService(
        conn=conn, graph=graph, event_store=SqliteEventStore(conn),
        proposal_store=SqliteProposalStore(conn),
    ).ingest(
        pid, graph.current_snapshots(pid)[0],
        _analysis(events=(_event(confidence=0.70),)),
        prompt_hash="prompt:boundary",
    )
    assert boundary.proposal_count == 0


def test_profile_and_incidental_surface_policy_never_guesses_or_creates_nodes(
    seed: Seed, conn: Connection
) -> None:
    other = seed.graph.upsert_node(
        NodeSpec(project_id=seed.project_id, label=NodeLabel.CHARACTER, name="陆沉")
    )
    seed.graph.add_alias(AliasSpec(
        project_id=seed.project_id, node_id=seed.hero_id, surface="师兄"
    ))
    seed.graph.add_alias(AliasSpec(
        project_id=seed.project_id, node_id=other.id, surface="师兄"
    ))
    profiles = (
        RawCharacterProfile(surface="新来客", gender="男", personality="谨慎", confidence=0.84),
        RawCharacterProfile(surface="师兄", background="来历不明", confidence=0.82),
        RawCharacterProfile(surface="顾清音", gender="男", personality="冲动", confidence=0.99),
    )
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            events=(
                _event(),
                _event(participants=("不存在的路人",), knowers=(), revealed_facts=()),
            ),
            profiles=profiles,
        ),
        prompt_hash="prompt:profiles",
    )

    assert (report.valid_event_count, report.discarded_event_count) == (1, 1)
    assert any(
        reason.kind == "character_profile"
        and reason.index == 1
        and reason.outcome is DiscardOutcome.AMBIGUOUS_SURFACE
        for reason in report.discarded
    )
    proposals = SqliteProposalStore(conn).pending(seed.project_id)
    assert [proposal.kind for proposal in proposals] == ["new_character"]
    assert proposals[0].items[0]["profile"]["surface"] == "新来客"
    resolutions = seed.graph.resolve(seed.project_id, ["新来客", "不存在的路人"])
    assert resolutions[0].hits == resolutions[1].hits == []
    profile = SqliteEventStore(conn).profile(seed.project_id, seed.hero_id)
    assert (profile.gender, profile.personality) == ("女", None)


class _ExplodingProposalStore:
    def create(self, proposal: ProposalCreate):
        raise RuntimeError(f"proposal write failed: {proposal.kind}")


def test_unexpected_proposal_failure_rolls_back_business_writes(
    seed: Seed, conn: Connection
) -> None:
    with pytest.raises(RuntimeError, match="proposal write failed"):
        _service(conn, seed, proposals=_ExplodingProposalStore()).ingest(
            seed.project_id,
            seed.chapter,
            _analysis(events=(_event(confidence=0.40),)),
            prompt_hash="prompt:rollback",
        )

    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert SqliteEventStore(conn).events_for_chapter(
        seed.project_id, seed.chapter.number, InformationScope.PROVISIONAL
    ) == []


def test_reused_service_does_not_cache_main_character_across_ingests(
    seed: Seed, conn: Connection
) -> None:
    profiles = SqliteEventStore(conn)
    service = ExtractionService(
        conn=conn,
        graph=seed.graph,
        event_store=profiles,
        proposal_store=SqliteProposalStore(conn),
    )
    first = service.ingest(
        seed.project_id,
        seed.chapter,
        _analysis(events=(_event(confidence=0.40),)),
        prompt_hash="prompt:first",
    )
    profiles.update_profile(
        seed.project_id,
        seed.hero_id,
        CharacterProfilePatch(main_character=False),
    )

    second = service.ingest(
        seed.project_id,
        seed.chapter,
        _analysis(events=(_event(confidence=0.40),)),
        prompt_hash="prompt:second",
    )

    assert first.proposal_count == 1
    assert second.proposal_count == 0
