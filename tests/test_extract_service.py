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
LOCATION_SUPERSEDED_QUOTE = "顾清音先在北荒雪原短暂停留片刻。"
STATE_QUOTE = "顾清音的修为终于突破到了金丹境界。"
STATE_FINAL_QUOTE = "此战之后，顾清音的修为已稳固在元婴境界。"
RELATION_QUOTE = "顾清音与萧决正式结成了生死盟友。"
RELATION_FINAL_QUOTE = "翌日清晨，萧决与顾清音彻底决裂成了宿敌。"
REPEATED_QUOTE = "夜风吹过空无一人的青石长街。"
CHAPTER_TEXT = "\n".join(
    (
        EVENT_QUOTE,
        LOCATION_SUPERSEDED_QUOTE,
        LOCATION_QUOTE,
        STATE_QUOTE,
        STATE_FINAL_QUOTE,
        RELATION_QUOTE,
        RELATION_FINAL_QUOTE,
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


@pytest.mark.parametrize("kind", ["location", "state", "relationship"])
def test_same_graph_key_keeps_only_last_located_state_update(
    kind: str, seed: Seed, conn: Connection
) -> None:
    if kind == "location":
        seed.graph.upsert_edge(EdgeSpec(
            project_id=seed.project_id, src=seed.hero_id, dst=seed.mountain_id,
            type=EdgeType.LOCATED_AT, valid_from_chapter=1,
            information_scope=InformationScope.CANON,
        ))
        states = (
            RawStateUpdate(
                kind="location", subject="顾清音", object="北荒",
                quote=LOCATION_SUPERSEDED_QUOTE, confidence=0.91,
            ),
            RawStateUpdate(
                kind="location", subject="顾清音", object="渡口",
                quote=LOCATION_QUOTE, confidence=0.92,
            ),
        )
        expected_subject = seed.hero_id
        expected_target = seed.harbor_id
        expected_value = None
        expected_quote = LOCATION_QUOTE
    elif kind == "state":
        seed.graph.upsert_edge(EdgeSpec(
            project_id=seed.project_id, src=seed.hero_id, dst=seed.dimension_id,
            type=EdgeType.HAS_STATE, props=EdgeProps(value="筑基"),
            valid_from_chapter=1, information_scope=InformationScope.CANON,
        ))
        states = (
            RawStateUpdate(
                kind="state", subject="顾清音", dimension="修为", value="金丹",
                quote=STATE_QUOTE, confidence=0.91,
            ),
            RawStateUpdate(
                kind="state", subject="顾清音", dimension="修为", value="元婴",
                quote=STATE_FINAL_QUOTE, confidence=0.92,
            ),
        )
        expected_subject = seed.hero_id
        expected_target = seed.dimension_id
        expected_value = "元婴"
        expected_quote = STATE_FINAL_QUOTE
    else:
        seed.graph.upsert_edge(EdgeSpec(
            project_id=seed.project_id, src=seed.hero_id, dst=seed.sidekick_id,
            type=EdgeType.RELATED_TO, props=EdgeProps(value="陌路"),
            valid_from_chapter=1, information_scope=InformationScope.CANON,
        ))
        states = (
            RawStateUpdate(
                kind="relationship", subject="顾清音", object="萧决", value="生死盟友",
                quote=RELATION_QUOTE, confidence=0.91,
            ),
            RawStateUpdate(
                kind="relationship", subject="萧决", object="顾清音", value="宿敌",
                quote=RELATION_FINAL_QUOTE, confidence=0.92,
            ),
        )
        expected_subject = seed.sidekick_id
        expected_target = seed.hero_id
        expected_value = "宿敌"
        expected_quote = RELATION_FINAL_QUOTE

    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(states=states),
        prompt_hash=f"prompt:last-{kind}",
    )

    assert report.valid_state_update_count == 1
    assert [(reason.index, reason.outcome) for reason in report.discarded] == [
        (0, DiscardOutcome.SUPERSEDED_IN_ANALYSIS)
    ]
    rows = conn.execute(
        """
        SELECT id, status, evidence_id FROM edge
        WHERE project_id = ? AND information_scope = 'PROVISIONAL'
        """,
        (seed.project_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "ACTIVE"
    assert report.edge_ids == (rows[0]["id"],)
    linked = next(
        edge for edge in seed.graph.state_at(
            seed.project_id, seed.hero_id, seed.chapter.number,
            scope=InformationScope.PROVISIONAL,
        ).edges
        if edge.id == rows[0]["id"]
    )
    assert linked.props.value == expected_value
    assert {linked.src, linked.dst} == {expected_subject, expected_target}
    evidence = seed.graph.get_evidence(seed.project_id, linked.evidence_id or "")
    assert evidence is not None and evidence.audit.quote_text == expected_quote
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2

    proposal = SqliteProposalStore(conn).pending(seed.project_id)[0]
    assert proposal.kind == "edge_conflict"
    assert proposal.edge_ids == [linked.id]
    assert proposal.item_count == 1
    proposed = proposal.items[0]["proposed"]
    assert proposed == {
        "edge_id": linked.id,
        "subject_id": expected_subject,
        "target_id": expected_target,
        "value": expected_value,
        "quote": expected_quote,
    }


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


def test_low_confidence_relationship_checks_main_character_at_either_end(
    seed: Seed, conn: Connection
) -> None:
    relationship = RawStateUpdate(
        kind="relationship",
        subject="萧决",
        object="顾清音",
        value="生死盟友",
        quote=RELATION_QUOTE,
        confidence=0.69,
    )

    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(states=(relationship,)),
        prompt_hash="prompt:relationship-main-object",
    )

    proposal = SqliteProposalStore(conn).pending(seed.project_id)[0]
    assert proposal.kind == "low_confidence_main"
    assert proposal.edge_ids == list(report.edge_ids)


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


def test_unknown_incidental_surfaces_are_dropped_not_guessed(
    seed: Seed, conn: Connection
) -> None:
    """匿名配角与未声明事实是常态：未知面丢弃，事件保留已知参与者。

    2026-08-04 真书首跑发现：整章事件因「相亲小姐姐/服务员/路人」这类匿名
    配角全部被弃。路人不进图谱（M4_DESIGN），但也不该整条事件陪葬——
    未知面丢弃、歧义/错类仍整条拒收（绝不替作者猜）。
    """
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            events=(
                _event(
                    participants=("顾清音", "不存在的路人"),
                    knowers=("顾清音", "服务员", "全体师生"),
                    revealed_facts=("玄铁令来历", "模型生成的未声明事实句"),
                ),
            )
        ),
        prompt_hash="prompt:bystanders",
    )

    assert (report.valid_event_count, report.discarded_event_count) == (1, 0)
    (event_id,) = report.event_ids
    view = SqliteEventStore(conn).event(seed.project_id, event_id)
    assert view is not None
    assert [node.id for node in view.participants] == [seed.hero_id]
    assert [node.id for node in view.knowers] == [seed.hero_id]
    assert [node.id for node in view.revealed_facts] == [seed.secret_id]


def test_new_character_proposals_are_split_per_surface(
    seed: Seed, conn: Connection
) -> None:
    """每个未知人物独立成一条提案：作者才能逐人「接受为角色 / 标为路人」。"""
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            profiles=(
                RawCharacterProfile(
                    surface="陆青禾", gender="女", personality="隐忍", confidence=0.8
                ),
                RawCharacterProfile(
                    surface="白峰", gender="男", personality="沮丧", confidence=0.7
                ),
            )
        ),
        prompt_hash="prompt:split-new-characters",
    )

    assert report.proposal_count == 2
    proposals = SqliteProposalStore(conn).pending(seed.project_id)
    assert [proposal.kind for proposal in proposals] == [
        "new_character",
        "new_character",
    ]
    assert all(proposal.item_count == 1 for proposal in proposals)
    assert {proposal.items[0]["surface"] for proposal in proposals} == {
        "陆青禾",
        "白峰",
    }


def test_each_new_character_row_says_who_it_is(seed: Seed, conn: Connection) -> None:
    """待确认队列那一行**自己说清是谁** —— 不许再是一句通用话（2026-08-23）。

    真书上的后果实测过：22 条 `new_character` 提案的 `summary` 去重之后**只有 1 条**
    不同文本（「抽取发现尚未登记的人物画像。」），名字和画像明明就躺在 `items` 里。
    它们从 2026-08-15 一条都没被确认过——而这些人不确认，抽出来的事件就一件都落不了地
    （认不出参与者会被整条丢），花名册空着 ⇒ 下一章接着全丢。

    这条红了 = 那句写死的通用话回来了。**断言的是「各不相同 + 带名字」**，
    不是某一句具体措辞——措辞可以改，「说不清是谁」不行。
    """
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            profiles=(
                RawCharacterProfile(
                    surface="陆青禾",
                    gender="女",
                    background="药王谷弃徒",
                    personality="隐忍",
                    confidence=0.8,
                ),
                RawCharacterProfile(surface="白峰", gender="男", confidence=0.7),
            )
        ),
        prompt_hash="prompt:new-character-rows-name-themselves",
    )

    assert report.proposal_count == 2
    summaries = [p.summary for p in SqliteProposalStore(conn).pending(seed.project_id)]
    assert len(set(summaries)) == 2, f"两条提案长得一模一样：{summaries}"
    assert all(
        any(name in text for name in ("陆青禾", "白峰")) for text in summaries
    ), summaries
    # 有画像就带上（背景最认得出人）；三样都空时只报名字，不编一句。
    assert any("药王谷弃徒" in text for text in summaries), summaries


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


def test_the_deadlock_and_the_key_that_opens_it(seed: Seed, conn: Connection) -> None:
    """死锁的两半，一条测试里演一遍（2026-08-23 真书上撞出来的）。

    ── 上半：花名册里没有这个人 ⇒ 事件整条丢 ────────────────────────────

    作者的 158 章真书上，抽取跑过三章，每次都 `SUCCEEDED`、`errors_json='[]'`，
    而**留下 0 件**（12 / 11 / 12 全丢）。整本书的图谱是空的：人物 0、边 0、
    事件 0、证据 0。丢弃条件只有一条 —— 事件里的人在花名册里认不出来。

        花名册空 → 认不出 → 全丢 → 花名册还是空 → 下一章接着全丢

    唯一出口是 `new_character` 提案，而那 22 条从 2026-08-15 一条没被确认过。

    ── 下半：那个人一登记，同一份 analysis 立刻留得下 ──────────────────

    **这一半是「钥匙确实能开那把锁」的证据。** 真书上跑这一遍要重新付一次模型调用
    （那本书停在 `user_version=16`，早于 021，没有可重放的规范 analysis），
    所以机制在这儿钉，账在那儿算。

    这条红了 = 要么丢弃判据变了，要么确认人物不再解得开它 —— 两种都得当场知道。
    """
    # 参与者**全部**认不出 —— 丢弃判据是 `if not participants`，也就是
    # 「一个都解不出来才丢」。混一个认得出的人进去，这条事件会活下来（只留认得出的
    # 那部分），所以真书全丢的成因是花名册**空**，不是「有生面孔」。
    stranger = _analysis(events=(_event(participants=("陆青禾",), knowers=("陆青禾",)),))

    before = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, stranger, prompt_hash="prompt:deadlock-locked"
    )
    assert (before.valid_event_count, before.discarded_event_count) == (0, 1), (
        "花名册里没有「陆青禾」，这一件事本该被整条丢掉 —— 丢弃判据变了？"
    )

    # 作者在待确认队列上点了「接受为角色」—— 那一步落地就是花名册里多一个人。
    seed.graph.upsert_node(
        NodeSpec(
            project_id=seed.project_id, label=NodeLabel.CHARACTER, name="陆青禾"
        )
    )

    after = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, stranger, prompt_hash="prompt:deadlock-opened"
    )
    assert after.valid_event_count == 1 and after.discarded_event_count == 0, (
        "人已经在花名册里了，同一份 analysis 却还是留不下 —— 死锁没被打开"
    )
