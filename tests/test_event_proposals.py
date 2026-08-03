"""Author review turns selected provisional M4 facts into audited canon."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import threading

from pydantic import ValidationError
import pytest

from novel_harness import decisions
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.decisions import DecisionKind, read as read_decisions
from novel_harness.events import (
    ProposalAlreadyResolved,
    ProposalCreate,
    ProvisionalEventSpec,
)
from novel_harness.extract.models import RawCharacterProfile
from novel_harness.extract.proposals import (
    DecisionAuditError,
    ProposalAction,
    ProposalReview,
    ProposalShapeError,
    confirm_provisional_edges,
    confirm_provisional_event,
    confirm_provisional_events,
    recover_proposal_audit,
    review_proposal,
)
from novel_harness.graph import (
    ChapterSpec,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_review import SqliteEdgeReviewStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import StaleBaseVersion, create as create_project, require_canon_version
from novel_harness import project as project_module


@dataclass(frozen=True)
class ReviewWorld:
    conn: Connection
    project_id: str
    graph: SqliteStoryGraph
    events: SqliteEventStore
    proposals: SqliteProposalStore
    edge_reviews: SqliteEdgeReviewStore
    chapter_number: int
    snapshot_id: str
    hero_id: str
    peer_id: str
    event_id: str
    event_quote: str
    location_edge_id: str
    location_quote: str
    relation_edge_id: str
    relation_quote: str


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def world(conn: Connection) -> ReviewWorld:
    project_id = create_project(conn, name="青云记-review", root_path=".").id
    graph = SqliteStoryGraph(conn)
    hero = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(main_character=True),
        )
    )
    peer = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    place = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="北荒")
    )
    event_quote = "顾清音在风雪中救下了身受重伤的萧决。"
    location_quote = "两人随即动身，一同踏入了茫茫北荒。"
    relation_quote = "经过此战，两人终于结为生死盟友。"
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=7,
            heading="第七章",
            path="chapters/0007.md",
            text=f"{event_quote}\n{location_quote}\n{relation_quote}\n",
        )
    )

    def evidence(para: int, quote: str):
        return graph.put_evidence(
            EvidenceSpec(
                project_id=project_id,
                chapter_snapshot_id=chapter.snapshot_id,
                para_index=para,
                quote_text=quote,
            )
        )

    event_evidence = evidence(0, event_quote)
    location_evidence = evidence(1, location_quote)
    relation_evidence = evidence(2, relation_quote)
    events = SqliteEventStore(conn)
    event = events.put_provisional(
        ProvisionalEventSpec(
            project_id=project_id,
            summary="顾清音救下重伤的萧决。",
            evidence_id=event_evidence.id,
            participant_ids=[hero.id, peer.id],
            knower_ids=[hero.id, peer.id],
            confidence=0.61,
        )
    )
    location = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=7,
            information_scope=InformationScope.PROVISIONAL,
            confidence=0.64,
            source=EdgeSource.EXTRACTOR,
            evidence_id=location_evidence.id,
        )
    ).edge
    relation = graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=peer.id,
            type=EdgeType.RELATED_TO,
            props=EdgeProps(value="生死盟友"),
            valid_from_chapter=7,
            information_scope=InformationScope.PROVISIONAL,
            confidence=0.66,
            source=EdgeSource.EXTRACTOR,
            evidence_id=relation_evidence.id,
        )
    ).edge
    proposals = SqliteProposalStore(conn)
    return ReviewWorld(
        conn=conn,
        project_id=project_id,
        graph=graph,
        events=events,
        proposals=proposals,
        edge_reviews=SqliteEdgeReviewStore(conn, graph),
        chapter_number=7,
        snapshot_id=chapter.snapshot_id,
        hero_id=hero.id,
        peer_id=peer.id,
        event_id=event.event.id,
        event_quote=event_quote,
        location_edge_id=location.id,
        location_quote=location_quote,
        relation_edge_id=relation.id,
        relation_quote=relation_quote,
    )


def _event_item(world: ReviewWorld) -> dict[str, object]:
    event = world.events.event(world.project_id, world.event_id)
    assert event is not None
    return {
        "source_kind": "event",
        "event_id": world.event_id,
        "summary": event.event.summary,
        "confidence": event.event.confidence,
        "quote": world.event_quote,
    }


def _edge_item(world: ReviewWorld, edge_id: str, quote: str) -> dict[str, object]:
    (edge,) = world.edge_reviews.hydrate_provisional(world.project_id, [edge_id])
    kind = "relationship" if edge.edge.type is EdgeType.RELATED_TO else "location"
    subject_id, target_id = edge.edge.src, edge.edge.dst
    return {
        "source_kind": "state_update",
        "update_kind": kind,
        "confidence": edge.edge.confidence,
        "proposed": {
            "edge_id": edge.edge.id,
            "subject_id": subject_id,
            "target_id": target_id,
            "value": edge.edge.props.value,
            "quote": quote,
        },
    }


def _make_proposal(
    world: ReviewWorld,
    *,
    kind: str = "low_confidence_main",
    items: list[object],
    event_ids: list[str] | None = None,
    edge_ids: list[str] | None = None,
):
    return world.proposals.create(
        ProposalCreate(
            project_id=world.project_id,
            kind=kind,
            items=items,
            chapter_number=world.chapter_number,
            snapshot_id=world.snapshot_id,
            base_canon_version=require_canon_version(world.conn, world.project_id),
            schema_version="m4.analysis.v1",
            prompt_hash="prompt:test",
            event_ids=event_ids or [],
            edge_ids=edge_ids or [],
        )
    )


def _review(world: ReviewWorld, proposal_id: str, action: ProposalAction, **kwargs):
    return review_proposal(
        world.conn,
        world.graph,
        world.events,
        proposal_id,
        ProposalReview(
            action=action,
            expected_canon_version=require_canon_version(world.conn, world.project_id),
            **kwargs,
        ),
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )


def test_review_models_are_strict_frozen_and_validate_edit_shape() -> None:
    review = ProposalReview(action="accept", expected_canon_version=0)
    assert review.action is ProposalAction.ACCEPT
    with pytest.raises(ValidationError):
        ProposalReview(action="accept", expected_canon_version=True)
    with pytest.raises(ValidationError):
        ProposalReview(action="edit", expected_canon_version=0)
    with pytest.raises(ValidationError):
        ProposalReview(action="reject", expected_canon_version=0, edited_summary="x")
    with pytest.raises(ValidationError):
        ProposalReview(action="edit", expected_canon_version=0, edited_summary="   ")
    with pytest.raises(ValidationError):
        ProposalReview(action="accept", expected_canon_version=0, extra="no")
    with pytest.raises(ValidationError):
        review.action = ProposalAction.REJECT  # type: ignore[misc]


def test_accept_event_only_clones_derived_event_bumps_once_and_audits_text(
    world: ReviewWorld,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    result = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert result.status == "ACCEPTED"
    assert result.canon_version == 1
    assert len(result.events) == 1 and result.event == result.events[0]
    assert result.event is not None
    assert result.event.event.information_scope is InformationScope.CANON
    assert result.event.event.derived_from_event_id == world.event_id
    assert require_canon_version(world.conn, world.project_id) == 1
    source = world.events.event(world.project_id, world.event_id)
    assert source is not None and source.event.information_scope is InformationScope.PROVISIONAL
    (decision,) = read_decisions(world.conn, world.project_id, kind=DecisionKind.PROPOSAL_REVIEW)
    assert result.decision_id == decision.id
    assert decision.subject_name == "顾清音"
    assert decision.quote_text == world.event_quote
    assert decision.quote_sha256 == decisions.quote_hash(world.event_quote)
    assert decision.chapter_number == world.chapter_number
    assert decision.para_index == 0
    item = decision.payload["events"][0]
    assert item["summary"] == "顾清音救下重伤的萧决。"
    assert item["participants"] == ["顾清音", "萧决"]
    assert item["knowers"] == ["顾清音", "萧决"]
    assert item["evidence"]["quote"] == world.event_quote
    assert item["evidence"]["occurrence_k"] == 0


def test_accept_mixed_cluster_promotes_every_event_and_edge_with_one_bump(
    world: ReviewWorld,
) -> None:
    proposal = _make_proposal(
        world,
        items=[
            _event_item(world),
            _edge_item(world, world.location_edge_id, world.location_quote),
            _edge_item(world, world.relation_edge_id, world.relation_quote),
        ],
        event_ids=[world.event_id],
        edge_ids=[world.location_edge_id, world.relation_edge_id],
    )

    result = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert len(result.events) == 1
    assert {item.edge.type for item in result.edges} == {
        EdgeType.LOCATED_AT,
        EdgeType.RELATED_TO,
    }
    assert result.canon_version == 1
    assert require_canon_version(world.conn, world.project_id) == 1
    (decision,) = read_decisions(world.conn, world.project_id)
    assert [item["type"] for item in decision.payload["edges"]] == [
        EdgeType.LOCATED_AT.value,
        EdgeType.RELATED_TO.value,
    ]
    assert {item["src_name"] for item in decision.payload["edges"]} <= {"顾清音", "萧决"}
    assert {item["evidence"]["quote"] for item in decision.payload["edges"]} == {
        world.location_quote,
        world.relation_quote,
    }


def test_accept_edge_only_proposal_returns_every_promoted_edge(world: ReviewWorld) -> None:
    proposal = _make_proposal(
        world,
        items=[
            _edge_item(world, world.location_edge_id, world.location_quote),
            _edge_item(world, world.relation_edge_id, world.relation_quote),
        ],
        edge_ids=[world.location_edge_id, world.relation_edge_id],
    )

    result = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert result.events == () and result.event is None
    assert len(result.edges) == 2
    assert result.canon_version == 1


def test_edit_is_event_only_and_changes_only_the_canon_summary(world: ReviewWorld) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    source_before = world.events.event(world.project_id, world.event_id)

    result = _review(
        world,
        proposal.id,
        ProposalAction.EDIT,
        edited_summary="顾清音在北荒救下萧决，两人结伴。",
    )

    assert result.status == "EDITED"
    assert result.event is not None
    assert result.event.event.summary == "顾清音在北荒救下萧决，两人结伴。"
    assert result.event.event.derived_from_event_id == world.event_id
    assert world.events.event(world.project_id, world.event_id) == source_before
    assert result.canon_version == 1
    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.payload["action"] == "edit"
    assert decision.payload["events"][0]["summary"] == result.event.event.summary


def test_edit_rejects_edge_or_multi_event_shapes_before_writes(world: ReviewWorld) -> None:
    edge_proposal = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )
    with pytest.raises(ProposalShapeError):
        _review(
            world,
            edge_proposal.id,
            ProposalAction.EDIT,
            edited_summary="修改不应该用于边。",
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, edge_proposal.id).status.value == "PENDING"


def test_reject_and_bystander_create_no_canon_and_do_not_bump(world: ReviewWorld) -> None:
    rejected = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    reject_result = _review(world, rejected.id, ProposalAction.REJECT)
    assert reject_result.status == "REJECTED"
    assert reject_result.canon_version == 0

    profile = RawCharacterProfile(
        surface="陆青禾",
        gender="女",
        personality="冷静",
        background="北荒医师",
        character_notes="随身携带银针",
        confidence=0.82,
    )
    bystander = _make_proposal(
        world,
        kind="new_character",
        items=[
            {
                "surface": profile.surface,
                "profile": profile.model_dump(mode="json"),
                "confidence": profile.confidence,
            }
        ],
    )
    bystander_result = _review(world, bystander.id, ProposalAction.BYSTANDER)
    assert bystander_result.status == "REJECTED"
    assert bystander_result.characters == ()
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.graph.resolve(world.project_id, [profile.surface])[0].unique_node is None
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


def test_accept_cluster_creates_multiple_characters_profiles_and_canonical_aliases(
    world: ReviewWorld,
) -> None:
    profiles = [
        RawCharacterProfile(
            surface="陆青禾",
            gender="女",
            personality="冷静",
            background="北荒医师",
            character_notes="随身携带银针",
            confidence=0.82,
        ),
        RawCharacterProfile(
            surface="裴无妄",
            gender="男",
            personality="洒脱",
            background="游侠",
            character_notes="喜好烈酒",
            confidence=0.77,
        ),
    ]
    proposal = _make_proposal(
        world,
        kind="new_character",
        items=[
            {
                "surface": profile.surface,
                "profile": profile.model_dump(mode="json"),
                "confidence": profile.confidence,
            }
            for profile in profiles
        ],
    )

    result = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert [character.name for character in result.characters] == ["陆青禾", "裴无妄"]
    assert result.character is None
    assert result.canon_version == 1
    for profile, character in zip(profiles, result.characters, strict=True):
        resolution = world.graph.resolve(world.project_id, [profile.surface])[0]
        assert resolution.unique_node is not None
        assert resolution.unique_node.id == character.id
        stored = world.events.profile(world.project_id, character.id)
        assert stored.gender == profile.gender
        assert stored.personality == profile.personality
        assert stored.background == profile.background
        assert stored.character_notes == profile.character_notes
    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.quote_text is None and decision.quote_sha256 is None
    assert decision.payload["characters"][0]["evidence"] is None
    assert decision.payload["characters"][0]["profile"]["surface"] == "陆青禾"


def test_new_character_shape_is_revalidated_and_duplicate_surfaces_are_atomic(
    world: ReviewWorld,
) -> None:
    profile = RawCharacterProfile(surface="陆青禾", confidence=0.8)
    invalid = _make_proposal(
        world,
        kind="new_character",
        items=[
            {
                "surface": "外层不同",
                "profile": profile.model_dump(mode="json"),
                "confidence": profile.confidence,
            }
        ],
    )
    with pytest.raises(ProposalShapeError):
        _review(world, invalid.id, ProposalAction.ACCEPT)
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.graph.resolve(world.project_id, [profile.surface])[0].unique_node is None

    duplicate = _make_proposal(
        world,
        kind="new_character",
        items=[
            {
                "surface": profile.surface,
                "profile": profile.model_dump(mode="json"),
                "confidence": profile.confidence,
            },
            {
                "surface": profile.surface,
                "profile": profile.model_dump(mode="json"),
                "confidence": profile.confidence,
            },
        ],
    )
    with pytest.raises(ProposalShapeError, match="surface"):
        _review(world, duplicate.id, ProposalAction.ACCEPT)
    assert require_canon_version(world.conn, world.project_id) == 0


def test_request_or_proposal_base_stale_and_duplicate_resolution_leave_zero_writes(
    world: ReviewWorld,
) -> None:
    request_stale = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    with pytest.raises(StaleBaseVersion):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            request_stale.id,
            ProposalReview(action="accept", expected_canon_version=1),
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )
    assert require_canon_version(world.conn, world.project_id) == 0

    accepted = _review(world, request_stale.id, ProposalAction.ACCEPT)
    assert accepted.canon_version == 1
    with pytest.raises(ProposalAlreadyResolved):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            request_stale.id,
            ProposalReview(action="accept", expected_canon_version=1),
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )
    assert require_canon_version(world.conn, world.project_id) == 1
    assert len(read_decisions(world.conn, world.project_id)) == 1


def test_proposal_base_stale_after_another_canon_change(world: ReviewWorld) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    assert project_module.compare_and_bump_canon_version(world.conn, world.project_id, 0) == 1
    world.conn.commit()

    with pytest.raises(StaleBaseVersion):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            ProposalReview(action="accept", expected_canon_version=1),
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )

    assert require_canon_version(world.conn, world.project_id) == 1
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


def test_second_invalid_link_rolls_back_first_clone_and_resolution(world: ReviewWorld) -> None:
    proposal = _make_proposal(
        world,
        items=[
            _event_item(world),
            _edge_item(world, world.location_edge_id, world.location_quote),
            _edge_item(world, world.relation_edge_id, world.relation_quote),
        ],
        event_ids=[world.event_id],
        edge_ids=[world.location_edge_id, world.relation_edge_id],
    )
    world.conn.execute(
        "UPDATE edge SET evidence_status = 'STALE' WHERE id = ?",
        (world.relation_edge_id,),
    )
    world.conn.commit()

    with pytest.raises(ProposalShapeError):
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


def test_mark_or_cas_failure_rolls_back_every_business_write(
    world: ReviewWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    def fail_cas(*_args, **_kwargs):
        raise RuntimeError("CAS injection")

    monkeypatch.setattr(project_module, "compare_and_bump_canon_version", fail_cas)
    with pytest.raises(RuntimeError, match="CAS injection"):
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


class _MarkFailingStore:
    def __init__(self, real: SqliteProposalStore) -> None:
        self.real = real

    def __getattr__(self, name: str):
        return getattr(self.real, name)

    def mark_resolved(self, *_args, **_kwargs):
        raise RuntimeError("mark injection")


def test_mark_failure_rolls_back_event_edge_character_and_version(world: ReviewWorld) -> None:
    proposal = _make_proposal(
        world,
        items=[
            _event_item(world),
            _edge_item(world, world.location_edge_id, world.location_quote),
        ],
        event_ids=[world.event_id],
        edge_ids=[world.location_edge_id],
    )
    with pytest.raises(RuntimeError, match="mark injection"):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            ProposalReview(action="accept", expected_canon_version=0),
            proposal_store=_MarkFailingStore(world.proposals),
            edge_review_store=world.edge_reviews,
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


def test_append_failure_leaves_auditable_hole_and_recovery_appends_once(
    world: ReviewWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    real_append = decisions.append

    def fail_append(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError) as exc_info:
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert exc_info.value.proposal_id == proposal.id
    assert exc_info.value.canon_version == 1
    assert exc_info.value.decision_id is None
    assert require_canon_version(world.conn, world.project_id) == 1
    assert [record.id for record in world.proposals.unaudited(world.project_id)] == [proposal.id]
    assert read_decisions(world.conn, world.project_id) == []

    monkeypatch.setattr(decisions, "append", real_append)
    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )
    assert recovered.decision_id is not None
    assert len(read_decisions(world.conn, world.project_id)) == 1
    assert world.proposals.unaudited(world.project_id) == []
    assert recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    ).decision_id == recovered.decision_id
    assert len(read_decisions(world.conn, world.project_id)) == 1


class _AttachFailingStore:
    def __init__(self, real: SqliteProposalStore) -> None:
        self.real = real

    def __getattr__(self, name: str):
        return getattr(self.real, name)

    def attach_decision(self, proposal_id: str, decision_id: str):
        raise RuntimeError(f"attach failed: {proposal_id}/{decision_id}")


def test_attach_failure_preserves_log_and_recovery_does_not_duplicate_it(
    world: ReviewWorld,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    with pytest.raises(DecisionAuditError) as exc_info:
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            ProposalReview(action="accept", expected_canon_version=0),
            proposal_store=_AttachFailingStore(world.proposals),
            edge_review_store=world.edge_reviews,
        )

    decision_id = exc_info.value.decision_id
    assert decision_id is not None
    assert len(read_decisions(world.conn, world.project_id)) == 1
    assert world.proposals.unaudited(world.project_id)[0].id == proposal.id
    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )
    assert recovered.decision_id == decision_id
    assert len(read_decisions(world.conn, world.project_id)) == 1


def test_two_connections_compete_and_only_one_resolves_and_bumps(tmp_path: Path) -> None:
    database = tmp_path / "review-race.sqlite"
    seed = connect(database)
    migrate(seed)
    project_id = create_project(seed, name="青云记-race", root_path=".").id
    profile = RawCharacterProfile(surface="陆青禾", confidence=0.8)
    proposal = SqliteProposalStore(seed).create(
        ProposalCreate(
            project_id=project_id,
            kind="new_character",
            items=[
                {
                    "surface": profile.surface,
                    "profile": profile.model_dump(mode="json"),
                    "confidence": profile.confidence,
                }
            ],
            base_canon_version=0,
        )
    )
    seed.close()
    barrier = threading.Barrier(2)
    results = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        worker_conn = connect(database)
        try:
            graph = SqliteStoryGraph(worker_conn)
            barrier.wait(timeout=5)
            result = review_proposal(
                worker_conn,
                graph,
                SqliteEventStore(worker_conn),
                proposal.id,
                ProposalReview(action="accept", expected_canon_version=0),
            )
            with guard:
                results.append(result)
        except BaseException as exc:
            with guard:
                errors.append(exc)
        finally:
            worker_conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert len(errors) == 1 and isinstance(errors[0], ProposalAlreadyResolved)
    verify = connect(database)
    try:
        assert require_canon_version(verify, project_id) == 1
        assert len(read_decisions(verify, project_id)) == 1
        assert (
            verify.execute(
                "SELECT COUNT(*) FROM node WHERE project_id = ? AND name = '陆青禾'",
                (project_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        verify.close()


def test_confirm_event_batch_clones_all_selected_and_bumps_once(world: ReviewWorld) -> None:
    relation = world.edge_reviews.hydrate_provisional(
        world.project_id, [world.relation_edge_id]
    )[0]
    second = world.events.put_provisional(
        ProvisionalEventSpec(
            project_id=world.project_id,
            summary="顾清音与萧决结为生死盟友。",
            evidence_id=relation.edge.evidence_id,
            participant_ids=[world.hero_id, world.peer_id],
            knower_ids=[world.hero_id, world.peer_id],
            confidence=0.9,
        )
    )

    confirmation = confirm_provisional_events(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        [world.event_id, second.event.id],
        expected_canon_version=0,
    )

    assert confirmation.canon_version == 1
    assert [event.event.derived_from_event_id for event in confirmation.events] == [
        world.event_id,
        second.event.id,
    ]
    assert require_canon_version(world.conn, world.project_id) == 1
    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.payload["kind"] == "provisional_confirm"
    assert len(decision.payload["events"]) == 2


def test_confirm_single_event_and_edge_subset_are_explicit_author_actions(
    world: ReviewWorld,
) -> None:
    event_confirmation = confirm_provisional_event(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=0,
    )
    assert event_confirmation.canon_version == 1

    edge_confirmation = confirm_provisional_edges(
        world.conn,
        world.graph,
        world.project_id,
        [world.relation_edge_id],
        expected_canon_version=1,
        edge_review_store=world.edge_reviews,
    )
    assert edge_confirmation.canon_version == 2
    assert [item.edge.id for item in edge_confirmation.edges]
    canon_evidence = {
        row[0]
        for row in world.conn.execute(
            "SELECT evidence_id FROM edge WHERE information_scope = 'CANON'"
        ).fetchall()
    }
    relation_evidence = world.edge_reviews.hydrate_provisional(
        world.project_id, [world.relation_edge_id]
    )[0].edge.evidence_id
    location_evidence = world.edge_reviews.hydrate_provisional(
        world.project_id, [world.location_edge_id]
    )[0].edge.evidence_id
    assert relation_evidence in canon_evidence
    assert location_evidence not in canon_evidence
    assert len(read_decisions(world.conn, world.project_id)) == 2


def test_confirm_edge_batch_failure_is_atomic_and_audit_failure_keeps_canon(
    world: ReviewWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ProposalShapeError):
        confirm_provisional_edges(
            world.conn,
            world.graph,
            world.project_id,
            [world.location_edge_id, "edge:missing"],
            expected_canon_version=0,
            edge_review_store=world.edge_reviews,
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0

    def fail_append(*_args, **_kwargs):
        raise OSError("audit disk full")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError) as exc_info:
        confirm_provisional_edges(
            world.conn,
            world.graph,
            world.project_id,
            [world.location_edge_id, world.relation_edge_id],
            expected_canon_version=0,
            edge_review_store=world.edge_reviews,
        )
    assert exc_info.value.proposal_id is None
    assert exc_info.value.canon_version == 1
    assert exc_info.value.fact_ids == (
        world.location_edge_id,
        world.relation_edge_id,
    )
    assert require_canon_version(world.conn, world.project_id) == 1
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 2


@pytest.mark.parametrize("selection", [[], ["duplicate", "duplicate"]])
def test_confirm_rejects_empty_or_duplicate_batches_without_bump(
    world: ReviewWorld, selection: list[str]
) -> None:
    with pytest.raises((ProposalShapeError, ValueError)):
        confirm_provisional_events(
            world.conn,
            world.graph,
            world.events,
            world.project_id,
            selection,
            expected_canon_version=0,
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert read_decisions(world.conn, world.project_id) == []


def test_confirm_rejects_closed_event_without_partial_canon(world: ReviewWorld) -> None:
    world.conn.execute(
        "UPDATE story_event SET valid_to_chapter = 8 WHERE id = ?",
        (world.event_id,),
    )
    world.conn.commit()

    with pytest.raises(ProposalShapeError):
        confirm_provisional_event(
            world.conn,
            world.graph,
            world.events,
            world.project_id,
            world.event_id,
            expected_canon_version=0,
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0
