"""Author review turns selected provisional M4 facts into audited canon."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading

from pydantic import ValidationError
import pytest

from novel_harness import decisions
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.decisions import DecisionKind, read as read_decisions
from novel_harness.events import (
    ProposalAlreadyResolved,
    ProposalCreate,
    ProposalValidationError,
    ProvisionalEventSpec,
)
from novel_harness.extract.models import RawCharacterProfile
from novel_harness.extract.proposals import (
    ConfirmationConflict,
    DecisionAuditError,
    ProposalAction,
    ProposalReview,
    ProposalShapeError,
    confirm_provisional_edges,
    confirm_provisional_event,
    confirm_provisional_events,
    hydrate_proposal_names,
    recover_proposal_audit,
    review_proposal,
)
from novel_harness.extract.proposal_validation import endpoint_key_names, referenced_node_ids
from novel_harness.graph import (
    ChapterSpec,
    Edge,
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


def _make_edge_conflict_proposal(
    world: ReviewWorld,
    *,
    update_kind: str,
    current: Edge,
    proposed_edge_id: str,
    quote: str,
):
    proposed = world.edge_reviews.hydrate_provisional(
        world.project_id, [proposed_edge_id]
    )[0].edge
    current_target = (
        current.peer_of(world.hero_id)
        if current.type is EdgeType.RELATED_TO
        else current.dst
    )
    proposed_target = (
        proposed.peer_of(world.hero_id)
        if proposed.type is EdgeType.RELATED_TO
        else proposed.dst
    )
    return _make_proposal(
        world,
        kind="edge_conflict",
        items=[
            {
                "update_kind": update_kind,
                "current": {
                    "edge_id": current.id,
                    "subject_id": world.hero_id,
                    "target_id": current_target,
                    "value": None if update_kind == "location" else current.props.value,
                },
                "proposed": {
                    "edge_id": proposed.id,
                    "subject_id": world.hero_id,
                    "target_id": proposed_target,
                    "value": proposed.props.value,
                    "quote": quote,
                },
            }
        ],
        edge_ids=[proposed.id],
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


def test_batch_created_proposal_auto_rebases_when_reviewed_at_current(
    world: ReviewWorld,
) -> None:
    """批量抽取后跨章审阅：base 落后不是错误，作者按当前版本审阅即自动 rebase。

    2026-08-04 验收暴露：先抽完 1–3 章再统一审，接受第一章的提案 bump canon 后，
    其余提案 base 落后直接 409，没有恢复路径。现在只要 expected == current，
    审阅前把 base 推进到 current，并对当前 canon 重验事实。
    """
    first = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    relation = world.edge_reviews.hydrate_provisional(
        world.project_id, [world.relation_edge_id]
    )[0]
    second_event = world.events.put_provisional(
        ProvisionalEventSpec(
            project_id=world.project_id,
            summary="顾清音与萧决结盟。",
            evidence_id=relation.edge.evidence_id,
            participant_ids=[world.hero_id, world.peer_id],
            knower_ids=[world.hero_id, world.peer_id],
            confidence=0.92,
        )
    )
    second = _make_proposal(
        world,
        items=[
            {
                "source_kind": "event",
                "event_id": second_event.event.id,
                "summary": second_event.event.summary,
                "confidence": second_event.event.confidence,
                "quote": world.relation_quote,
            }
        ],
        event_ids=[second_event.event.id],
    )
    assert first.base_canon_version == second.base_canon_version == 0

    _review(world, first.id, ProposalAction.ACCEPT)  # canon 0 -> 1

    # 第二条提案 base 仍为 0，但作者按当前版本（1）明确审阅 → 自动 rebase 后接受。
    result = _review(world, second.id, ProposalAction.ACCEPT)
    assert result.status == "ACCEPTED"
    assert result.canon_version == 2
    assert require_canon_version(world.conn, world.project_id) == 2
    assert len(read_decisions(world.conn, world.project_id)) == 2


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


def test_edit_can_fix_the_knowers_the_extractor_guessed(world: ReviewWorld) -> None:
    """**`edit` 不能只改一句话。**

    `knowers` 是抽取里唯一靠推断得来的那一维（谁在场是文本里写着的，谁因此知道了是猜的）。
    只能改 summary 的时候，作者面对一条 knowers 抽错的事件只有两个选择：整条 reject
    （丢掉一条真实存在的事实，连证据链一起丢），或者 accept 一条错的。

    队列这条路必须和「改一条已经生效的事实」（`corrections.py`）能力一致——否则作者会
    学会先 reject 再重来。
    """
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    source_before = world.events.event(world.project_id, world.event_id)

    result = _review(
        world,
        proposal.id,
        ProposalAction.EDIT,
        edited_knower_ids=(world.hero_id,),
    )

    assert result.status == "EDITED"
    assert result.event is not None
    assert [ref.id for ref in result.event.knowers] == [world.hero_id]
    assert {ref.id for ref in result.event.participants} == {world.hero_id, world.peer_id}
    assert result.event.event.summary == source_before.event.summary, "没改的那一维不许动"
    # 源是 PROVISIONAL，作者的审阅动作不该改写它（改的是克隆出来的那条 CANON）。
    assert world.events.event(world.project_id, world.event_id) == source_before

    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.decision is decisions.Verdict.EDIT
    assert decision.payload["events"][0]["knowers"] == ["顾清音"]


def test_edit_can_fix_participants_and_the_summary_in_one_go(world: ReviewWorld) -> None:
    third = world.graph.upsert_node(
        NodeSpec(project_id=world.project_id, label=NodeLabel.CHARACTER, name="李管家")
    )
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    result = _review(
        world,
        proposal.id,
        ProposalAction.EDIT,
        edited_summary="顾清音救下萧决，李管家在旁看着。",
        edited_participant_ids=(world.hero_id, world.peer_id, third.id),
    )

    assert result.event.event.summary == "顾清音救下萧决，李管家在旁看着。"
    assert {ref.name for ref in result.event.participants} == {"顾清音", "萧决", "李管家"}
    (decision,) = read_decisions(world.conn, world.project_id)
    assert set(decision.payload["events"][0]["participants"]) == {"顾清音", "萧决", "李管家"}


def test_edit_refuses_a_cast_member_that_is_not_a_character(world: ReviewWorld) -> None:
    place = world.graph.upsert_node(
        NodeSpec(project_id=world.project_id, label=NodeLabel.LOCATION, name="青云城")
    )
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    with pytest.raises(ProposalShapeError):
        _review(
            world,
            proposal.id,
            ProposalAction.EDIT,
            edited_knower_ids=(world.hero_id, place.id),
        )
    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"


def test_edit_shape_accepts_a_lone_cast_change_and_rejects_junk() -> None:
    assert ProposalReview(
        action="edit", expected_canon_version=0, edited_knower_ids=("a",)
    ).edited_summary is None
    with pytest.raises(ValidationError):
        ProposalReview(action="edit", expected_canon_version=0, edited_knower_ids=("a", "a"))
    with pytest.raises(ValidationError):
        ProposalReview(action="edit", expected_canon_version=0, edited_participant_ids=("",))
    with pytest.raises(ValidationError):
        ProposalReview(action="accept", expected_canon_version=0, edited_knower_ids=("a",))
    # 空集合是合法的一次编辑（「这件事其实没人知道」），不是「没改」。
    assert ProposalReview(
        action="edit", expected_canon_version=0, edited_knower_ids=()
    ).edited_knower_ids == ()


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


def test_proposal_base_rebases_when_reviewed_at_current_but_expected_lag_still_stale(
    world: ReviewWorld,
) -> None:
    """批量抽取后的跨章审阅：base 落后在作者按当前版本确认时自动 rebase。

    expected != current 仍是真正的 stale（作者的 UI 落后于库），照旧 409。
    """
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    assert project_module.compare_and_bump_canon_version(world.conn, world.project_id, 0) == 1
    world.conn.commit()

    result = review_proposal(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        ProposalReview(action="accept", expected_canon_version=1),
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )
    assert result.status == "ACCEPTED"
    assert result.canon_version == 2
    assert require_canon_version(world.conn, world.project_id) == 2

    second = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    with pytest.raises(StaleBaseVersion):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            second.id,
            ProposalReview(action="accept", expected_canon_version=0),
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )
    assert world.proposals.get(world.project_id, second.id).status.value == "PENDING"


def test_accept_rebases_pending_siblings_from_the_same_extraction_cohort(
    world: ReviewWorld,
) -> None:
    first = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    sibling = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )
    final_sibling = _make_proposal(
        world,
        items=[_edge_item(world, world.relation_edge_id, world.relation_quote)],
        edge_ids=[world.relation_edge_id],
    )

    assert _review(world, first.id, ProposalAction.ACCEPT).canon_version == 1
    rebased = world.proposals.get(world.project_id, sibling.id)
    assert rebased is not None
    assert rebased.status.value == "PENDING"
    assert rebased.base_canon_version == 1
    assert world.proposals.get(
        world.project_id, final_sibling.id
    ).base_canon_version == 1

    assert _review(world, sibling.id, ProposalAction.ACCEPT).canon_version == 2
    assert world.proposals.get(
        world.project_id, final_sibling.id
    ).base_canon_version == 2
    assert _review(world, final_sibling.id, ProposalAction.ACCEPT).canon_version == 3
    assert require_canon_version(world.conn, world.project_id) == 3
    assert len(read_decisions(world.conn, world.project_id)) == 3


def test_edit_rebases_pending_siblings_from_the_same_extraction_cohort(
    world: ReviewWorld,
) -> None:
    edited = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    sibling = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )

    result = _review(
        world,
        edited.id,
        ProposalAction.EDIT,
        edited_summary="顾清音在风雪中救下萧决。",
    )

    assert result.canon_version == 1
    assert world.proposals.get(world.project_id, sibling.id).base_canon_version == 1


def test_accept_rebases_only_the_exact_non_null_extraction_cohort(
    world: ReviewWorld,
) -> None:
    selected = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    same_cohort = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )
    different_prompt = world.proposals.create(
        ProposalCreate(
            project_id=world.project_id,
            kind="low_confidence_main",
            items=[_edge_item(world, world.relation_edge_id, world.relation_quote)],
            chapter_number=world.chapter_number,
            snapshot_id=world.snapshot_id,
            base_canon_version=0,
            schema_version="m4.analysis.v1",
            prompt_hash="prompt:other",
            edge_ids=[world.relation_edge_id],
        )
    )
    incomplete_identity = world.proposals.create(
        ProposalCreate(
            project_id=world.project_id,
            kind="new_character",
            items=[{"surface": "陆青禾", "profile": {"confidence": 0.8}}],
            chapter_number=world.chapter_number,
            base_canon_version=0,
            schema_version="m4.analysis.v1",
            prompt_hash="prompt:test",
        )
    )

    _review(world, selected.id, ProposalAction.ACCEPT)

    assert world.proposals.get(
        world.project_id, same_cohort.id
    ).base_canon_version == 1
    assert world.proposals.get(
        world.project_id, different_prompt.id
    ).base_canon_version == 0
    assert world.proposals.get(
        world.project_id, incomplete_identity.id
    ).base_canon_version == 0


def test_reject_does_not_rebase_same_cohort_siblings(world: ReviewWorld) -> None:
    rejected = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    sibling = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )

    result = _review(world, rejected.id, ProposalAction.REJECT)

    assert result.canon_version == 0
    assert world.proposals.get(world.project_id, sibling.id).base_canon_version == 0


def test_saving_new_text_marks_pending_proposals_obsolete_and_rejects_direct_review(
    conn: Connection, world: ReviewWorld
) -> None:
    """020 / Task 9：保存 S3 后，S2 的旧 PENDING 提案退出待确认。

    - `pending()` 不再返回它（当前待确认列表只读 `PENDING + CURRENT`）；
    - 直接审阅 → `ProposalObsolete`（HTTP 层 409）——它锚的那版正文已经不是当前；
    - 历史仍读得到，且不伪造 ACCEPTED/REJECTED resolution metadata。
    """
    from novel_harness.events import ProposalObsolete

    # 一条锚在 chapter 7 当前快照上的 PENDING 提案。
    proposal = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    assert any(
        p.id == proposal.id for p in world.proposals.pending(world.project_id)
    ), "前提交到一条待确认 —— 下面是空转"

    # 保存新正文（产品保存路径：`commit_chapter_snapshot` 在同一事务把旧快照
    # 锚的 PENDING 提案标 OBSOLETE —— 不变量 20）。
    world.graph.commit_chapter_snapshot(
        ChapterSpec(
            project_id=world.project_id,
            number=world.chapter_number,
            heading="第七章 改",
            path="chapters/0007.md",
            text="第七章 改\n\n这是一段完全不同的新正文。\n",
        ),
        expected_text_sha256=world.graph.current_chapter_hash(
            world.project_id, world.chapter_number
        ),
    )
    conn.commit()

    with pytest.raises(ProposalObsolete):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            ProposalReview(action=ProposalAction.REJECT, expected_canon_version=0),
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )
    assert all(
        p.id != proposal.id for p in world.proposals.pending(world.project_id)
    ), "保存新正文后旧提案还在待确认列表"
    record = world.proposals.get(world.project_id, proposal.id)
    assert record is not None and record.currentness == "OBSOLETE"
    assert record.status.value == "PENDING", "OBSOLETE 不改 status：那是作者裁决的领域"
    assert record.resolution_action is None and record.resolved_canon_version is None


class _RebaseFailingStore:
    def __init__(self, real: SqliteProposalStore) -> None:
        self.real = real

    def __getattr__(self, name: str):
        return getattr(self.real, name)

    def rebase_pending_cohort(self, *_args, **_kwargs):
        raise RuntimeError("rebase injection")


def test_rebase_failure_rolls_back_canon_resolution_and_siblings(
    world: ReviewWorld,
) -> None:
    selected = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    sibling = _make_proposal(
        world,
        items=[_edge_item(world, world.location_edge_id, world.location_quote)],
        edge_ids=[world.location_edge_id],
    )

    with pytest.raises(RuntimeError, match="rebase injection"):
        review_proposal(
            world.conn,
            world.graph,
            world.events,
            selected.id,
            ProposalReview(action="accept", expected_canon_version=0),
            proposal_store=_RebaseFailingStore(world.proposals),
            edge_review_store=world.edge_reviews,
        )

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, selected.id).status.value == "PENDING"
    assert world.proposals.get(world.project_id, sibling.id).base_canon_version == 0
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 0


def test_review_accepts_edge_conflict_while_current_canon_still_matches(
    world: ReviewWorld,
) -> None:
    old_place = world.graph.upsert_node(
        NodeSpec(
            project_id=world.project_id,
            label=NodeLabel.LOCATION,
            name="青云城",
        )
    )
    north = world.graph.resolve(world.project_id, ["北荒"])[0].unique_node
    assert north is not None
    current = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="location",
        current=current,
        proposed_edge_id=world.location_edge_id,
        quote=world.location_quote,
    )

    result = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert result.status == "ACCEPTED"
    assert result.canon_version == 1
    assert result.edges[0].edge.dst == north.id


def test_rebased_edge_conflict_still_revalidates_current_canon(
    world: ReviewWorld,
) -> None:
    old_place = world.graph.upsert_node(
        NodeSpec(
            project_id=world.project_id,
            label=NodeLabel.LOCATION,
            name="青云城",
        )
    )
    north = world.graph.resolve(world.project_id, ["北荒"])[0].unique_node
    assert north is not None
    current = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    selected = _make_proposal(
        world,
        items=[_event_item(world)],
        event_ids=[world.event_id],
    )
    conflict = _make_edge_conflict_proposal(
        world,
        update_kind="location",
        current=current,
        proposed_edge_id=world.location_edge_id,
        quote=world.location_quote,
    )

    _review(world, selected.id, ProposalAction.ACCEPT)
    assert world.proposals.get(
        world.project_id, conflict.id
    ).base_canon_version == 1
    world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=north.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    )

    with pytest.raises(ProposalShapeError, match="current|Canon"):
        _review(world, conflict.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 1
    still_pending = world.proposals.get(world.project_id, conflict.id)
    assert still_pending.status.value == "PENDING"
    assert still_pending.base_canon_version == 1


def test_review_rejects_replaced_current_edge_when_graph_write_bypasses_ledger(
    world: ReviewWorld,
) -> None:
    old_place = world.graph.upsert_node(
        NodeSpec(
            project_id=world.project_id,
            label=NodeLabel.LOCATION,
            name="青云城",
        )
    )
    north = world.graph.resolve(world.project_id, ["北荒"])[0].unique_node
    assert north is not None
    current = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="location",
        current=current,
        proposed_edge_id=world.location_edge_id,
        quote=world.location_quote,
    )

    # 防御性回归：绕过 Ledger 写 Canon 不会 bump 版本，但锁内事实复核仍必须挡住旧 proposal。
    replacement = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=north.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    assert replacement.id != current.id
    assert require_canon_version(world.conn, world.project_id) == 0

    with pytest.raises(ProposalShapeError, match="current|Canon"):
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"
    assert read_decisions(world.conn, world.project_id) == []


def test_review_rejects_changed_current_value_even_when_edge_id_is_unchanged(
    world: ReviewWorld,
) -> None:
    current = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=world.peer_id,
            type=EdgeType.RELATED_TO,
            props=EdgeProps(value="盟友"),
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="relationship",
        current=current,
        proposed_edge_id=world.relation_edge_id,
        quote=world.relation_quote,
    )

    changed = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=world.peer_id,
            type=EdgeType.RELATED_TO,
            props=EdgeProps(value="故交"),
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    assert changed.id == current.id
    assert require_canon_version(world.conn, world.project_id) == 0

    with pytest.raises(ProposalShapeError, match="current|Canon"):
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"


def test_review_rejects_changed_current_location_value_with_the_same_edge_id(
    world: ReviewWorld,
) -> None:
    old_place = world.graph.upsert_node(
        NodeSpec(
            project_id=world.project_id,
            label=NodeLabel.LOCATION,
            name="青云城",
        )
    )
    current = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="location",
        current=current,
        proposed_edge_id=world.location_edge_id,
        quote=world.location_quote,
    )

    changed = world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.project_id,
            src=world.hero_id,
            dst=old_place.id,
            type=EdgeType.LOCATED_AT,
            props=EdgeProps(value="DRIFTED"),
            valid_from_chapter=world.chapter_number,
            information_scope=InformationScope.CANON,
        )
    ).edge
    assert changed.id == current.id
    assert require_canon_version(world.conn, world.project_id) == 0

    with pytest.raises(ProposalShapeError, match="value"):
        _review(world, proposal.id, ProposalAction.ACCEPT)

    assert require_canon_version(world.conn, world.project_id) == 0
    assert world.proposals.get(world.project_id, proposal.id).status.value == "PENDING"


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


def test_append_that_commits_then_raises_is_reconciled_without_a_duplicate(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    real_append = decisions.append

    def append_then_raise(*args, **kwargs):
        real_append(*args, **kwargs)
        raise OSError("transport reported failure after commit")

    monkeypatch.setattr(decisions, "append", append_then_raise)

    reviewed = _review(world, proposal.id, ProposalAction.ACCEPT)

    assert reviewed.status == "ACCEPTED"
    (decision,) = read_decisions(world.conn, world.project_id)
    assert reviewed.decision_id == decision.id
    assert world.proposals.unaudited(world.project_id) == []


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


@pytest.mark.parametrize(
    "action",
    [ProposalAction.REJECT, ProposalAction.BYSTANDER],
)
def test_recovery_preserves_the_exact_new_character_action(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
    action: ProposalAction,
) -> None:
    profile = RawCharacterProfile(surface="陆青禾", confidence=0.8)
    proposal = _make_proposal(
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
    real_append = decisions.append

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        _review(world, proposal.id, action)

    monkeypatch.setattr(decisions, "append", real_append)
    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )

    assert recovered.status == "REJECTED"
    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.payload["action"] == action.value


def test_recovery_uses_the_committed_audit_snapshot_after_sources_go_stale(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    real_append = decisions.append

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        _review(world, proposal.id, ProposalAction.ACCEPT)
    world.conn.execute(
        "UPDATE story_event SET evidence_status = 'STALE' WHERE id = ?",
        (world.event_id,),
    )
    world.conn.commit()

    monkeypatch.setattr(decisions, "append", real_append)
    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )

    assert recovered.canon_version == 1
    (decision,) = read_decisions(world.conn, world.project_id)
    assert decision.payload["events"][0]["evidence"]["quote"] == world.event_quote


def test_recovery_rejects_a_caller_owned_transaction_without_committing_it(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    real_append = decisions.append

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        _review(world, proposal.id, ProposalAction.ACCEPT)
    monkeypatch.setattr(decisions, "append", real_append)

    world.conn.execute(
        "UPDATE project SET name = '不应被恢复提交' WHERE id = ?",
        (world.project_id,),
    )
    assert world.conn.in_transaction
    with pytest.raises(RuntimeError, match="外层事务|caller-owned|没有外层事务"):
        recover_proposal_audit(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )
    assert world.conn.in_transaction
    world.conn.rollback()
    assert (
        world.conn.execute(
            "SELECT name FROM project WHERE id = ?", (world.project_id,)
        ).fetchone()[0]
        == "青云记-review"
    )


def test_concurrent_audit_recovery_appends_exactly_one_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "audit-recovery-race.sqlite"
    seed = connect(database)
    migrate(seed)
    project_id = create_project(seed, name="青云记-audit-race", root_path=".").id
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
        )
    )
    real_append = decisions.append

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        review_proposal(
            seed,
            SqliteStoryGraph(seed),
            SqliteEventStore(seed),
            proposal.id,
            ProposalReview(action="reject", expected_canon_version=0),
        )
    monkeypatch.setattr(decisions, "append", real_append)
    seed.close()

    import novel_harness.extract.proposal_audit as audit_module

    real_audit = audit_module.append_audit
    barrier = threading.Barrier(2)

    def racing_audit(*args, **kwargs):
        barrier.wait(timeout=5)
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(audit_module, "append_audit", racing_audit)
    results = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        worker_conn = connect(database)
        try:
            result = recover_proposal_audit(
                worker_conn,
                SqliteStoryGraph(worker_conn),
                SqliteEventStore(worker_conn),
                proposal.id,
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
    assert errors == []
    assert len(results) == 2
    assert len({result.decision_id for result in results}) == 1
    verify = connect(database)
    try:
        assert len(read_decisions(verify, project_id)) == 1
    finally:
        verify.close()


def test_terminal_proposal_rejects_an_unrelated_same_project_decision(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        _review(world, proposal.id, ProposalAction.ACCEPT)
    monkeypatch.undo()
    unrelated = decisions.append(
        world.conn,
        project_id=world.project_id,
        kind=DecisionKind.NODE_DECLARE,
        decision=decisions.Verdict.ACCEPT,
        payload={"proposal_id": proposal.id},
    )

    with pytest.raises(ProposalValidationError, match="proposal_review|payload|审计"):
        world.proposals.attach_decision(proposal.id, unrelated.id)
    assert world.proposals.unaudited(world.project_id)[0].id == proposal.id


def test_forged_proposal_review_cannot_poison_recovery_for_the_same_proposal(
    world: ReviewWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )

    def fail_append(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(decisions, "append", fail_append)
    with pytest.raises(DecisionAuditError):
        _review(world, proposal.id, ProposalAction.ACCEPT)
    monkeypatch.undo()
    terminal = world.proposals.get(world.project_id, proposal.id)
    assert terminal is not None and terminal.audit_envelope is not None
    forged = dict(terminal.audit_envelope.payload)
    forged["action"] = "reject"
    forged["canon_version"] = 777
    with pytest.raises(sqlite3.IntegrityError, match="durable audit envelope"):
        decisions.append(
            world.conn,
            project_id=world.project_id,
            kind=DecisionKind.PROPOSAL_REVIEW,
            decision=decisions.Verdict.REJECT,
            payload=forged,
        )
    if world.conn.in_transaction:
        world.conn.rollback()

    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )
    assert recovered.decision_id is not None
    assert world.proposals.unaudited(world.project_id) == []


def test_recovery_refuses_duplicate_history_even_when_one_decision_is_attached(
    world: ReviewWorld,
) -> None:
    proposal = _make_proposal(
        world, items=[_event_item(world)], event_ids=[world.event_id]
    )
    reviewed = _review(world, proposal.id, ProposalAction.ACCEPT)
    assert reviewed.decision_id is not None
    world.conn.execute("DROP TRIGGER decision_log_one_proposal_review_insert")
    world.conn.execute(
        """
        INSERT INTO decision_log (
            id, project_id, kind, subject_name, quote_text, quote_sha256,
            chapter_number, para_index, payload_json, decision, actor
        )
        SELECT 'decision:duplicate-history', project_id, kind, subject_name,
               quote_text, quote_sha256, chapter_number, para_index,
               payload_json, decision, actor
        FROM decision_log WHERE id = ?
        """,
        (reviewed.decision_id,),
    )
    world.conn.commit()

    with pytest.raises(ProposalShapeError, match="2 条决策日志"):
        recover_proposal_audit(
            world.conn,
            world.graph,
            world.events,
            proposal.id,
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )


def test_recovery_revalidates_audit_headers_against_terminal_proposal(
    world: ReviewWorld,
) -> None:
    profile = RawCharacterProfile(surface="陆青禾", confidence=0.8)
    proposal = _make_proposal(
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
    _review(world, proposal.id, ProposalAction.REJECT)
    changed_id = "proposal:tampered-terminal-id"
    world.conn.execute("DROP TRIGGER proposal_resolution_metadata_immutable")
    world.conn.execute("DROP TRIGGER proposal_resolution_metadata_update")
    world.conn.execute(
        "UPDATE proposal_set SET id = ? WHERE id = ?",
        (changed_id, proposal.id),
    )
    world.conn.commit()

    with pytest.raises(ProposalShapeError, match="audit.*proposal|header|头字段"):
        recover_proposal_audit(
            world.conn,
            world.graph,
            world.events,
            changed_id,
            proposal_store=world.proposals,
            edge_review_store=world.edge_reviews,
        )


def test_recovery_attaches_semantically_equal_unsorted_audit_payload(
    world: ReviewWorld,
) -> None:
    profile = RawCharacterProfile(surface="陆青禾", confidence=0.8)
    proposal = _make_proposal(
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
    payload_json = (
        '{"status":"REJECTED","proposal_id":"'
        + proposal.id
        + '","kind":"new_character","events":[{"z":1,"a":2}],"edges":[],'
        '"characters":[],"canon_version":0,"action":"reject"}'
    )
    world.conn.execute(
        """
        UPDATE proposal_set
        SET status = 'REJECTED', resolution_action = 'reject',
            resolved_canon_version = 0,
            audit_envelope_json = json_object('payload', json(?)),
            resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE id = ?
        """,
        (payload_json, proposal.id),
    )
    world.conn.commit()

    recovered = recover_proposal_audit(
        world.conn,
        world.graph,
        world.events,
        proposal.id,
        proposal_store=world.proposals,
        edge_review_store=world.edge_reviews,
    )

    assert recovered.status == "REJECTED"
    assert recovered.decision_id is not None
    stored = world.proposals.get(world.project_id, proposal.id)
    assert stored is not None and stored.decision_log_id == recovered.decision_id


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
    assert decision.payload["proposal_id"] == confirmation.confirmation_id


def test_confirm_event_retry_returns_the_same_receipt_version_and_decision(
    world: ReviewWorld,
) -> None:
    first = confirm_provisional_event(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=0,
    )

    retry = confirm_provisional_event(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=0,
    )

    assert retry == first
    assert first.confirmation_id
    assert first.canon_version == 1
    assert require_canon_version(world.conn, world.project_id) == 1
    assert len(read_decisions(world.conn, world.project_id)) == 1
    assert world.conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 1


def test_confirm_edge_reordered_retry_is_idempotent_and_overlap_conflicts(
    world: ReviewWorld,
) -> None:
    first = confirm_provisional_edges(
        world.conn,
        world.graph,
        world.project_id,
        [world.location_edge_id, world.relation_edge_id],
        expected_canon_version=0,
        edge_review_store=world.edge_reviews,
    )
    retry = confirm_provisional_edges(
        world.conn,
        world.graph,
        world.project_id,
        [world.relation_edge_id, world.location_edge_id],
        expected_canon_version=0,
        edge_review_store=world.edge_reviews,
    )

    assert retry == first
    with pytest.raises(ConfirmationConflict, match="already|已确认|overlap"):
        confirm_provisional_edges(
            world.conn,
            world.graph,
            world.project_id,
            [world.location_edge_id],
            expected_canon_version=1,
            edge_review_store=world.edge_reviews,
        )
    assert require_canon_version(world.conn, world.project_id) == 1
    assert len(read_decisions(world.conn, world.project_id)) == 1


def test_completed_confirmation_retry_ignores_later_canon_bumps(
    world: ReviewWorld,
) -> None:
    original = confirm_provisional_event(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=0,
    )
    confirm_provisional_edges(
        world.conn,
        world.graph,
        world.project_id,
        [world.relation_edge_id],
        expected_canon_version=1,
        edge_review_store=world.edge_reviews,
    )

    retry = confirm_provisional_event(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=0,
    )

    assert retry == original
    assert require_canon_version(world.conn, world.project_id) == 2
    assert len(read_decisions(world.conn, world.project_id)) == 2


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
    assert exc_info.value.proposal_id is not None
    assert exc_info.value.canon_version == 1
    assert exc_info.value.fact_ids == (
        world.location_edge_id,
        world.relation_edge_id,
    )
    assert require_canon_version(world.conn, world.project_id) == 1
    assert world.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE information_scope = 'CANON'"
    ).fetchone()[0] == 2
    receipt_id = exc_info.value.proposal_id
    receipt = world.conn.execute(
        """
        SELECT kind, status, decision_log_id FROM proposal_set WHERE id = ?
        """,
        (receipt_id,),
    ).fetchone()
    assert tuple(receipt) == ("provisional_confirm", "ACCEPTED", None)

    monkeypatch.undo()
    recovered = confirm_provisional_edges(
        world.conn,
        world.graph,
        world.project_id,
        [world.relation_edge_id, world.location_edge_id],
        expected_canon_version=0,
        edge_review_store=world.edge_reviews,
    )
    assert recovered.confirmation_id == receipt_id
    assert recovered.canon_version == 1
    assert require_canon_version(world.conn, world.project_id) == 1
    assert len(read_decisions(world.conn, world.project_id)) == 1


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


# ══════════════════════════════════════════════════════════════════════════
# 队列的**读端**：id → 显示名归后端，界面不拿 id 去查
# ══════════════════════════════════════════════════════════════════════════
#
# 病历：`ProposalReviewTab` 曾经写 `rosterMap.get(id) ?? id.slice(-6)`，
# 花名册认不出时屏幕上是 `n:ID22`（`"location:ID22"` 的后六位）。
# **那条兜底不是边角**——理由见 `test_the_queue_answer_is_self_sufficient`。


def test_the_queue_carries_display_names_for_the_bare_ids_in_its_items(
    world: ReviewWorld,
) -> None:
    """`node_refs` 覆盖 items 里的每一个裸 id，且**只出窄引用**。"""
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="location",
        current=world.edge_reviews.hydrate_provisional(
            world.project_id, [world.location_edge_id]
        )[0].edge,
        proposed_edge_id=world.location_edge_id,
        quote=world.location_quote,
    )
    hydrated = hydrate_proposal_names(world.edge_reviews, world.project_id, [proposal])[0]

    wanted = set(referenced_node_ids(proposal.items))
    assert wanted, "这条提案的 items 里一个节点 id 都没有 —— 样本坏了"
    assert {ref.id for ref in hydrated.node_refs} == wanted
    # 出的是 `{id,label,name}`，不是 `Node`：这些 id 里可能有 Secret，而 props 装的
    # 正是秘密的内容（ARCHITECTURE §10.3 / `graph.models.NodeRef`）。
    assert all(set(ref.model_dump()) == {"id", "label", "name"} for ref in hydrated.node_refs)
    assert {ref.name for ref in hydrated.node_refs} == {"顾清音", "北荒"}
    # `items` 一个字节都没动 —— 它是落库的那份，审阅要拿它和存储事实逐字比对。
    assert hydrated.items == proposal.items


def test_the_queue_answer_is_self_sufficient(world: ReviewWorld) -> None:
    """**一条提案自己就说得出它提到的每一个名字，不需要第二次查询。**

    这才是 `n:ID22` 的真身。花名册（`/roster`）**不是**「只收人物」——它走
    `resolve(pid, None)`，而 `upsert_node` 每建一个节点都会写一条 canonical 别名，
    所以任何 label 的节点都在里面。真正的缝在**两次查询的时间差**：
    `["roster", pid]` 和 `["proposals", pid, chapter]` 是两条独立缓存，
    后台抽取（autopilot / 自动升 CANON）会造出新节点，而没有任何一条路径保证
    花名册那份在提案那份之后重取过。差一拍，屏幕上就是一串截断的内部编号。

    自足的出参让这一整类失败在结构上不存在：名字和 id 在**同一个响应**里。
    """
    proposal = _make_edge_conflict_proposal(
        world,
        update_kind="relationship",
        current=world.edge_reviews.hydrate_provisional(
            world.project_id, [world.relation_edge_id]
        )[0].edge,
        proposed_edge_id=world.relation_edge_id,
        quote=world.relation_quote,
    )
    hydrated = hydrate_proposal_names(world.edge_reviews, world.project_id, [proposal])[0]
    named = {ref.id for ref in hydrated.node_refs}
    assert set(referenced_node_ids(hydrated.items)) <= named, (
        "items 里有 id 在这份响应里查不到名字 —— 界面又得去查花名册了"
    )


def test_an_id_the_engine_cannot_recognise_gets_no_invented_name(
    world: ReviewWorld,
) -> None:
    """认不出的 id **不出现在名单里**，而且不许把整页队列一起打不开。

    `node_refs` 对幽灵 id 会抛（写路径要的就是这个）；读端逐个再试一遍，
    认得出的照给。前端那边渲染成「—」——绝不编一个名字出来。
    """
    proposal = _make_proposal(
        world,
        kind="edge_conflict",
        items=[
            {
                "update_kind": "location",
                "current": {
                    "edge_id": world.location_edge_id,
                    "subject_id": world.hero_id,
                    "target_id": "location:ghost:01KZZZ",
                    "value": None,
                },
                "proposed": {
                    "edge_id": world.location_edge_id,
                    "subject_id": world.hero_id,
                    "target_id": world.peer_id,
                    "value": None,
                    "quote": world.location_quote,
                },
            }
        ],
        edge_ids=[world.location_edge_id],
    )
    hydrated = hydrate_proposal_names(world.edge_reviews, world.project_id, [proposal])[0]
    known = {ref.id for ref in hydrated.node_refs}
    assert "location:ghost:01KZZZ" not in known
    assert {world.hero_id, world.peer_id} <= known


def test_the_endpoint_key_names_still_match_the_item_models() -> None:
    """**守卫**：`referenced_node_ids` 认的那两个键必须真的是 edge item 上的字段。

    它读的是**存进 `items_json` 的那份 JSON**，所以只能抄字面量。模型改名而这里
    没跟上的后果是：一个 id 都收集不到 ⇒ `node_refs` 空 ⇒ 界面整片「—」，
    **而没有任何东西会报错**。
    """
    mine, real = endpoint_key_names()
    assert mine <= real, f"这两个键不在 edge item 模型上了：{sorted(mine - real)}"
    assert mine == {"subject_id", "target_id"}
    assert "edge_id" not in mine, "edge_id 是边的主键，不是它指向的节点"


def test_referenced_node_ids_skips_what_it_cannot_read() -> None:
    """读不懂的 item 静默跳过 —— 这是读端，一条坏 item 不该让整页队列打不开。

    （形状不合法该在 `validate_proposal_shape` 那里被拦住并说清楚，不在这里。）
    """
    assert referenced_node_ids(["不是 object", 7, None]) == ()
    assert referenced_node_ids([{"current": "也不是 object"}]) == ()
    # 顺序 = 出现顺序，且去重（同一个人同时是 current 和 proposed 的 subject 是常态）。
    assert referenced_node_ids(
        [
            {
                "current": {"subject_id": "character:a", "target_id": "location:b"},
                "proposed": {"subject_id": "character:a", "target_id": "location:c"},
            }
        ]
    ) == ("character:a", "location:b", "location:c")
