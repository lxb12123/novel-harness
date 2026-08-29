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
            label=NodeLabel.FACTION,
            name="玄铁令来历",
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
) -> RawEvent:
    return RawEvent(
        summary="顾清音交出玄铁令。",
        quote=quote,
        participants=participants,
        knowers=knowers,
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
        project_id=pid, label=NodeLabel.FACTION, name="玄铁盟",
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


def test_unknown_surfaces_become_characters_but_ambiguous_ones_never_do(
    seed: Seed, conn: Connection
) -> None:
    """**认不出就建，认不准就整条拒收。**（2026-08-25 裁定，ADR 0020 补记）

    这两半必须一起看，否则容易读成「以后什么都建」：

    - `unknown`（花名册里查无此人）→ **建**。从前它进 `new_character` 提案等作者确认，
      而事件那一侧「一个参与者都认不出就整条丢」——空花名册上两条规矩互锁。
    - `ambiguous`（「师兄」同时指向两个**已经存在**的人）→ **照旧整条拒收**。
      建第三个「师兄」只会让歧义更重，而 ADR 0004 说产品从不替作者挑。
    """
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
                _event(participants=("不存在的路人",), knowers=()),
            ),
            profiles=profiles,
        ),
        prompt_hash="prompt:profiles",
    )

    # 两条事件**都留下了**：从前第二条因为「不存在的路人」认不出而整条丢。
    assert (report.valid_event_count, report.discarded_event_count) == (2, 0)
    # 歧义的「师兄」照旧被拒——它不是「不认识」，是「认识两个」。
    assert any(
        reason.kind == "character_profile"
        and reason.index == 1
        and reason.outcome is DiscardOutcome.AMBIGUOUS_SURFACE
        for reason in report.discarded
    )
    # 不再有 `new_character` 提案：人已经建好了，没什么可问的。
    assert SqliteProposalStore(conn).pending(seed.project_id) == []

    made = seed.graph.resolve(seed.project_id, ["新来客", "不存在的路人", "师兄"])
    assert made[0].unique_node is not None and made[0].unique_node.name == "新来客"
    assert made[1].unique_node is not None and made[1].unique_node.name == "不存在的路人"
    assert made[2].ambiguous, "「师兄」本来就指向两个人，不该多出第三个"

    # 画像跟着建出来的人一起落库（攒着不写 = 下一章再问一次同一个人）。
    fresh = SqliteEventStore(conn).profile(seed.project_id, made[0].unique_node.id)
    assert (fresh.gender, fresh.personality) == ("男", "谨慎")
    # **已知人物的档案照旧只读**，不被这一次抽取覆盖。
    profile = SqliteEventStore(conn).profile(seed.project_id, seed.hero_id)
    assert (profile.gender, profile.personality) == ("女", None)


def test_unknown_incidental_surfaces_are_dropped_not_guessed(
    seed: Seed, conn: Connection
) -> None:
    """匿名配角**也进图谱**（2026-08-25 裁定推翻了 M4_DESIGN 的「路人不进图谱」）。

    2026-08-04 真书首跑发现：整章事件因「相亲小姐姐/服务员/路人」这类匿名配角全部被弃。
    当时的修法是「未知面丢弃、事件保留已知参与者」——**它治的是事件，没治花名册**：
    这一章的「服务员」下一章还是认不出，而一个所有参与者都是生面孔的事件照样整条丢。

    今天的判据统一成一条：**认不出就建**。代价照实说——「服务员」「全体师生」
    这种一次性称呼会长期占着花名册（真书上的实例是「袭人」，它还会误命中
    「寒气袭人」）。**出口是花名册的删除入口**（`DELETE …/nodes/{id}`，同一批改动）：
    自动建 + 不能删 = 单向阀。
    """
    report = _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            events=(
                _event(
                    participants=("顾清音", "不存在的路人"),
                    knowers=("顾清音", "服务员", "全体师生"),
                ),
            )
        ),
        prompt_hash="prompt:bystanders",
    )

    assert (report.valid_event_count, report.discarded_event_count) == (1, 0)
    (event_id,) = report.event_ids
    view = SqliteEventStore(conn).event(seed.project_id, event_id)
    assert view is not None
    assert sorted(node.name for node in view.participants) == ["不存在的路人", "顾清音"]
    assert sorted(node.name for node in view.knowers) == ["全体师生", "服务员", "顾清音"]
    # 花名册里真的多了这几个人（下一章它们就认得出了 —— 死锁的另一半）。
    made = seed.graph.resolve(seed.project_id, ["服务员", "全体师生"])
    assert all(r.unique_node is not None for r in made)


def test_unknown_state_dimension_gets_created_not_discarded(
    seed: Seed, conn: Connection
) -> None:
    """**认不出的维度，直接建。**（2026-08-27 裁定）—— `_create_unknown_characters`
    在维度这一侧的姊妹条，但手法不同（见 `_write_state` 里的说明）：不经别名表，
    直接靠 `upsert_node` 按 `(project, label, name)` find-or-create——`StateDim`
    进花名册会让 mentions.py 拿维度名去正文里做字面匹配（`CANONICAL_ALIAS_LABELS`
    的说明），所以这条路故意不挂别名。

    从前：「灵力」这种没预先建过的维度在 `resolve_ids` 那步查无此维度，整条
    `state_update` 被丢（`UNKNOWN_SURFACE`）。今天它应该被建成一个新的 StateDim。
    """
    states = (
        RawStateUpdate(
            kind="state", subject="顾清音", dimension="灵力", value="小成",
            quote=STATE_QUOTE, confidence=0.9,
        ),
    )
    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(states=states),
        prompt_hash="prompt:new-dimension",
    )

    assert (report.valid_state_update_count, len(report.discarded)) == (1, 0)
    snapshot = seed.graph.state_at(
        seed.project_id, seed.hero_id, seed.chapter.number,
        scope=InformationScope.PROVISIONAL,
    )
    (state,) = snapshot.states
    assert state.dim.name == "灵力"
    assert state.dim.id != seed.dimension_id, "不该复用「修为」那个节点"
    assert state.value == "小成"
    # 新维度不配机器键：没有规则要查它（2026-08-27 裁定第二条）。
    assert state.dim_key is None
    # 也不进花名册：mentions.py 靠这条挡住维度名满篇字面匹配。
    resolved = seed.graph.resolve(seed.project_id, ["灵力"])
    assert resolved[0].unique_node is None


def test_blank_state_dimension_is_discarded_not_a_crash(
    seed: Seed, conn: Connection
) -> None:
    """模型偶尔会把 `dimension` 写成空白——这是不可信输入，不能让它一路冲到
    `upsert_node`（空名字会撞 `NodeSpec.name` 的 `min_length=1`）。
    `prepare_state_update` 要在那之前就挡住。
    """
    states = (
        RawStateUpdate(
            kind="state", subject="顾清音", dimension="   ", value="小成",
            quote=STATE_QUOTE, confidence=0.9,
        ),
    )
    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(states=states),
        prompt_hash="prompt:blank-dimension",
    )
    assert report.valid_state_update_count == 0
    assert [r.outcome for r in report.discarded] == [DiscardOutcome.UNKNOWN_SURFACE]


def test_unknown_location_gets_created_not_discarded(
    seed: Seed, conn: Connection
) -> None:
    """**认不出的地点，直接建。**（2026-08-27，`_create_unknown_locations`，
    `_create_unknown_characters` 的姊妹条）。`Location` 本来就在
    `CANONICAL_ALIAS_LABELS` 里，建完照旧挂 canonical 别名，所以下一次同一称呼
    照常能解析——和维度那条不一样。
    """
    states = (
        RawStateUpdate(
            kind="location", subject="顾清音", object="荒漠驿站",
            quote=LOCATION_QUOTE, confidence=0.9,
        ),
    )
    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(states=states),
        prompt_hash="prompt:new-location",
    )

    assert (report.valid_state_update_count, len(report.discarded)) == (1, 0)
    snapshot = seed.graph.state_at(
        seed.project_id, seed.hero_id, seed.chapter.number,
        scope=InformationScope.PROVISIONAL,
    )
    assert snapshot.location is not None and snapshot.location.name == "荒漠驿站"
    # Location 在花名册里：下次同一个称呼能正常解析（同人物那条路，不是维度那条）。
    resolved = seed.graph.resolve(seed.project_id, ["荒漠驿站"])
    assert resolved[0].unique_node is not None


def create(self, proposal: ProposalCreate):
        raise RuntimeError(f"proposal write failed: {proposal.kind}")


# ══════════════════════════════════════════════════════════════════════════
# 累计信息量（2026-08-25）—— **只算，不拿它决定问不问**
# ══════════════════════════════════════════════════════════════════════════


def test_information_units_count_only_what_the_model_wrote(seed: Seed) -> None:
    """判据是**四个自由文本字段的字符数之和**，名字和置信度不算。

    名字长不代表这个人重要；置信度是个概率不是信息量。
    """
    from novel_harness.extract.service import profile_information_units

    blank = RawCharacterProfile(surface="路人甲", confidence=0.5)
    assert profile_information_units(blank) == 0, "什么都没写就是 0，不是「有这一行所以算 1」"

    written = RawCharacterProfile(
        surface="贾环",  # 名字不计
        gender="男",  # 1
        personality="敏感多疑",  # 4
        background="荣国府庶子",  # 5
        character_notes="  与宝玉不睦  ",  # 5（首尾空白不算）
        confidence=0.5,  # 不计
    )
    assert profile_information_units(written) == 1 + 4 + 5 + 5


def test_the_score_adds_up_across_chapters_and_never_doubles_on_a_rerun(
    seed: Seed, conn: Connection
) -> None:
    """**章与章之间相加；同一章重跑是覆盖。**

    这两句必须一起验，它们是 029 为什么是一张表而不是 `node.props` 上一个标量的
    全部理由：累加写在标量上时，第二次跑同一章就是第二次加，而**没有任何东西会红**
    ——分数只是慢慢变大，看起来完全正常。
    """
    from novel_harness.graph.queries import character_information_totals

    first = RawCharacterProfile(surface="顾清音", background="药王谷弃徒", confidence=0.8)
    _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(profiles=(first,)), prompt_hash="p1"
    )
    after_one = character_information_totals(conn, seed.project_id)[seed.hero_id]
    assert after_one == len("药王谷弃徒")

    # **同一章再跑一次**（prompt 换了 = 真实的重跑形态）：不许翻倍。
    _service(conn, seed).ingest(
        seed.project_id, seed.chapter, _analysis(profiles=(first,)), prompt_hash="p2"
    )
    assert character_information_totals(conn, seed.project_id)[seed.hero_id] == after_one

    # 换一章、内容更多：这一次才该相加。
    seed.graph.put_chapter(
        ChapterSpec(
            project_id=seed.project_id,
            number=8,
            heading="第八章 北荒",
            path="chapters/0008.md",
            text=CHAPTER_TEXT,
        )
    )
    later = next(
        snap
        for snap in seed.graph.current_snapshots(seed.project_id)
        if snap.number == 8
    )
    second = RawCharacterProfile(
        surface="顾清音", background="药王谷弃徒", personality="隐忍", confidence=0.8
    )
    _service(conn, seed).ingest(
        seed.project_id, later, _analysis(profiles=(second,)), prompt_hash="p3"
    )

    assert character_information_totals(conn, seed.project_id)[seed.hero_id] == (
        len("药王谷弃徒") + len("药王谷弃徒") + len("隐忍")
    ), "换一章之后没相加 —— 累加是这条设计的全部意义（单章判据一定会误判重要配角）"


def test_a_character_already_in_the_roster_keeps_scoring(
    seed: Seed, conn: Connection
) -> None:
    """裁定第一条：**已在花名册 → 不问，内容并进去，分数继续累加。**

    「顾清音」建 fixture 时就在册。她的档案照旧只读（不被这一次抽取覆盖），
    但**分数照记**——这两件事是分开的。
    """
    from novel_harness.graph.queries import character_information_totals

    _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            profiles=(
                RawCharacterProfile(surface="顾清音", personality="冲动", confidence=0.9),
            )
        ),
        prompt_hash="p:known",
    )

    assert character_information_totals(conn, seed.project_id)[seed.hero_id] == len("冲动")
    # 档案没被覆盖（fixture 里她的 personality 本来是空的，抽取写的那句不进去）。
    assert SqliteEventStore(conn).profile(seed.project_id, seed.hero_id).personality is None


def test_only_characters_get_a_score(seed: Seed, conn: Connection) -> None:
    """错类的 surface 不记分。

    一行挂在地点身上的分会让「按分排序的花名册」里冒出一个不是人的东西，
    而那一行看起来完全正常。schema 那条复合外键是最后一道，这是第一道。
    """
    from novel_harness.graph.queries import character_information_totals

    _service(conn, seed).ingest(
        seed.project_id,
        seed.chapter,
        _analysis(
            profiles=(
                RawCharacterProfile(surface="渡口", background="很长的一段话", confidence=0.9),
            )
        ),
        prompt_hash="p:wrong-label",
    )

    assert seed.harbor_id not in character_information_totals(conn, seed.project_id)


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
    """**死锁解开了**：一份全是生面孔的 analysis 打进空花名册，事件真的落库。

    ── 那把锁当年是什么样 ────────────────────────────────────────────────

    作者的 158 章真书上，抽取跑过三章，每次都 `SUCCEEDED`、`errors_json='[]'`，
    而**留下 0 件**（12 / 11 / 12 全丢）。整本书的图谱是空的：人物 0、边 0、
    事件 0、证据 0。丢弃条件只有一条 —— 事件里的人在花名册里认不出来。

        花名册空 → 认不出 → 全丢 → 花名册还是空 → 下一章接着全丢

    唯一出口是 `new_character` 提案，而那 22 条从 2026-08-15 一条没被确认过。

    ── 钥匙（2026-08-25 裁定）────────────────────────────────────────────

    **认不出就建，不要问**（ADR 0020 补记）：`_create_unknown_characters` 在解析事件
    **之前**把人物位上的生面孔全部建成 Character，于是 `if not participants` 那条
    丢弃分支在空花名册上再也走不到。

    这条红了 = 自动建人物那一步没跑，或者事件那一侧又长出了别的丢弃条件 ——
    两种都得当场知道，因为它们都会让真书回到「跑了、成功了、什么都没留下」。
    """
    stranger = _analysis(events=(_event(participants=("陆青禾",), knowers=("陆青禾",)),))

    report = _service(conn, seed).ingest(
        seed.project_id, seed.chapter, stranger, prompt_hash="prompt:deadlock-opened"
    )

    assert (report.valid_event_count, report.discarded_event_count) == (1, 0), (
        "一份全是生面孔的 analysis 又被整条丢了 —— 死锁回来了"
    )
    made = seed.graph.resolve(seed.project_id, ["陆青禾"])[0]
    assert made.unique_node is not None, "事件留下了，人却没建出来 —— 下一章还会全丢"

    view = SqliteEventStore(conn).event(seed.project_id, report.event_ids[0])
    assert view is not None
    assert [node.id for node in view.participants] == [made.unique_node.id]
