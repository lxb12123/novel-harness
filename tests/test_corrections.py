"""改一条**已经生效（CANON）的事实** —— 「事后可查可改」里的那个「改」。

作者推翻了「事前逐条确认」：抽取直接生效，但每步留痕、错了能看见能改。**这条退路必须先通，
自动生效才敢开**——否则中间会有一段时间是「系统自动改了作者的书，而他改不回来」。

本文件量的是那条退路的三件事：

1. **改得动。** KNOWS ↔ BELIEVES（唯一靠推断得来的那一维上最高频的错）、事件的
   `knowers` / `participants`。
2. **改完还查得到改过什么。** 旧的那一行留着（`status='RETRACTED'`），
   `decision_log` 里那条 EDIT 记的是「改成了什么」，不是「改过了」。
3. **改的过程里作者一次章号都没敲。** 新事实的 `valid_from` 继承自被改的那条，
   而那个数一路回到证据（约束 10 / ADR 0006）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness import corrections, decisions, project
from novel_harness.corrections import (
    CorrectionRefused,
    FactNotFound,
    correct_event_cast,
    correct_knowledge,
)
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.events import ProvisionalEventSpec
from novel_harness.graph import (
    ChapterSpec,
    EdgeSource,
    EdgeStatus,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    KnowledgeState,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph import queries
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# 泄漏物：作者写在 Secret 上的东西，**出现在任何出参或任何一条日志里都是泄漏**
# （`graph.models.NodeRef` 的 docstring 有实测形态）。
TWIST = "萧决其实是魔尊之子第200章揭晓"
SECRET_DESC = "血脉的真相是他母亲换了孩子"

KNOWS_QUOTE = "顾清音在藏书阁里读到了血脉秘密的真相。"
EVENT_QUOTE = "顾清音在风雪中救下了身受重伤的萧决。"
SPARE_QUOTE = "李管家守在门外一言不发。"


@dataclass(frozen=True)
class World:
    conn: Connection
    project_id: str
    graph: SqliteStoryGraph
    events: SqliteEventStore
    hero_id: str
    peer_id: str
    third_id: str
    secret_id: str
    place_id: str
    event_id: str

    def version(self) -> int:
        return project.require_canon_version(self.conn, self.project_id)

    def edge(self, edge_id: str) -> Any:
        return queries.fetch_edge(self.conn, edge_id)

    def cell(self, chapter: int = 7) -> Any:
        matrix = self.graph.knowledge_matrix(
            self.project_id, chapter, [self.hero_id], secrets=[self.secret_id]
        )
        return matrix.cell(self.hero_id, self.secret_id)

    def edits(self, kind: decisions.DecisionKind) -> list[decisions.Decision]:
        return decisions.read(self.conn, self.project_id, kind=kind)

    def knower_rows(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT character_id, status FROM event_knower WHERE event_id = ?"
            " AND information_scope = 'CANON'",
            (self.event_id,),
        ).fetchall()
        return {row["character_id"]: row["status"] for row in rows}

    def participant_rows(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT character_id, status FROM event_participant WHERE event_id = ?",
            (self.event_id,),
        ).fetchall()
        return {row["character_id"]: row["status"] for row in rows}


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def world(conn: Connection) -> World:
    """一条已生效的 KNOWS + 一条已生效的事件。**两者都走生产写路径建出来。**

    KNOWS 走 `Ledger.declare_knows`（引语定章号），事件走
    `put_provisional` → `clone_to_scope(CANON)`（抽取 → 审阅通过的那条链）。
    直接 INSERT 会让这些测试量的是一份手写数据，而不是引擎。
    """
    pid = project.create(conn, name="青云记-corrections", root_path=".").id
    graph = SqliteStoryGraph(conn)
    ledger = Ledger(graph, conn, pid)
    hero = ledger.declare_node(NodeLabel.CHARACTER, "顾清音")
    peer = ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    third = ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    place = ledger.declare_node(NodeLabel.LOCATION, "北荒")
    secret = graph.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST}),
            secret=SecretDetail(description=SECRET_DESC),
        )
    )
    chapter = graph.put_chapter(
        ChapterSpec(
            project_id=pid,
            number=7,
            heading="第七章 藏书阁",
            path="chapters/0007.md",
            text=f"{KNOWS_QUOTE}\n{EVENT_QUOTE}\n{SPARE_QUOTE}\n",
        )
    )
    conn.commit()

    ledger.declare_knows(who="顾清音", secret="血脉秘密", quote=KNOWS_QUOTE)

    events = SqliteEventStore(conn)
    evidence = graph.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=chapter.snapshot_id,
            para_index=1,
            quote_text=EVENT_QUOTE,
        )
    )
    provisional = events.put_provisional(
        ProvisionalEventSpec(
            project_id=pid,
            summary="顾清音救下重伤的萧决。",
            evidence_id=evidence.id,
            participant_ids=[hero.id, peer.id],
            knower_ids=[hero.id, peer.id],
            confidence=0.62,
        )
    )
    canon_event = events.clone_to_scope(provisional.event.id, InformationScope.CANON)
    conn.commit()
    return World(
        conn=conn,
        project_id=pid,
        graph=graph,
        events=events,
        hero_id=hero.id,
        peer_id=peer.id,
        third_id=third.id,
        secret_id=secret.id,
        place_id=place.id,
        event_id=canon_event.event.id,
    )


def _flip(world: World, to_type: EdgeType, believed_value: str | None = None, **kwargs: Any):
    return correct_knowledge(
        world.conn,
        world.graph,
        world.project_id,
        character_id=world.hero_id,
        secret_id=world.secret_id,
        to_type=to_type,
        believed_value=believed_value,
        expected_canon_version=kwargs.pop("expected_canon_version", world.version()),
        **kwargs,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. KNOWS ↔ BELIEVES
# ══════════════════════════════════════════════════════════════════════════


def test_knows_becomes_believes_and_the_old_row_stays_in_the_database(world: World) -> None:
    """**这是本文件的头条。** 抽错的那条 KNOWS 在矩阵上压着 BELIEVES——它让面板对作者说
    「这个人已经知道了」，而他其实信着一个错版本。

    改法是撤回 + 写新的，不是原地改类型：旧行留着（`RETRACTED`），所以「系统当初猜的是
    什么」查得到。
    """
    before = world.cell()
    assert before.state is KnowledgeState.KNOWS and before.since_chapter == 7
    old_edge_id = queries.current_knowledge_edges(
        world.conn, world.project_id, world.hero_id, world.secret_id
    )[0].id

    result = _flip(world, EdgeType.BELIEVES, "以为血脉秘密早已被人说破")

    after = world.cell()
    assert after.state is KnowledgeState.BELIEVES
    assert after.believed_value == "以为血脉秘密早已被人说破"
    # ★ 章号一个字没变，而作者从头到尾没敲过它。
    assert after.since_chapter == 7 == result.since_chapter
    assert result.from_type is EdgeType.KNOWS and result.to_type is EdgeType.BELIEVES
    assert result.retracted_edge_id == old_edge_id

    old = world.edge(old_edge_id)
    assert old.status is EdgeStatus.RETRACTED, "旧行必须留着——查得到改过什么是这条退路的一半价值"
    assert old.valid_to_chapter is None, (
        "撤回不是闭合：写 valid_to 等于编了一段「他到第 N 章才不知道」的假历史，"
        "而那段历史在人物卡上长得完全正常"
    )
    new = world.edge(result.edge_id)
    assert new.evidence_id == old.evidence_id, "改的是同一条证据的读法，证据不该换"
    assert new.source is EdgeSource.AUTHOR


def test_believes_becomes_knows(world: World) -> None:
    _flip(world, EdgeType.BELIEVES, "以为已泄露")
    result = _flip(world, EdgeType.KNOWS)
    assert world.cell().state is KnowledgeState.KNOWS
    assert world.cell().believed_value is None
    assert result.from_type is EdgeType.BELIEVES


def test_flipping_back_does_not_silently_lose_the_cell(world: World) -> None:
    """**没有这条守卫，「改回来」会静默丢掉整格。**

    第二次改回 KNOWS 时，新边撞的是第一次被撤回的那条边的**幂等键**，
    而 `upsert_edge` 的契约明写它会当成重跑：只更 props、`status` 一个字节不动。
    于是 KNOWS 和 BELIEVES 两条边同时停在 RETRACTED，这一格变成 UNKNOWN，
    **而没有任何一步会报错**。`corrections` 走 `restore_canon` 就是为了这一条。
    """
    _flip(world, EdgeType.BELIEVES, "以为已泄露")
    _flip(world, EdgeType.KNOWS)
    _flip(world, EdgeType.BELIEVES, "又以为已泄露")
    result = _flip(world, EdgeType.KNOWS)

    cell = world.cell()
    assert cell.state is KnowledgeState.KNOWS, "来回改四次之后这一格不许消失"
    assert cell.since_chapter == 7
    assert world.edge(result.edge_id).status is EdgeStatus.ACTIVE
    live = queries.current_knowledge_edges(
        world.conn, world.project_id, world.hero_id, world.secret_id
    )
    assert [e.type for e in live] == [EdgeType.KNOWS], "同一格上不许同时留下两条 current 边"


def test_the_edit_is_written_into_the_decision_log_with_what_it_became(world: World) -> None:
    """`decisions.py` 说 EDIT 是三种里最有价值的一个：**它记的是系统猜错了、而作者亲手
    给出了正确答案**。所以 payload 里必须有 from 和 to 两侧，只记「改过了」等于没记。"""
    result = _flip(world, EdgeType.BELIEVES, "以为血脉秘密早已被人说破")

    (decision,) = world.edits(decisions.DecisionKind.KNOWLEDGE_EDIT)
    assert decision.id == result.decision_id
    assert decision.decision is decisions.Verdict.EDIT
    assert decision.actor == "author"
    assert decision.subject_name == "顾清音", "人名，不是 ID（§5.7）"
    assert decision.payload["from"]["edge_type"] == "KNOWS"
    assert decision.payload["from"]["believed_value"] is None
    assert decision.payload["to"]["edge_type"] == "BELIEVES"
    assert decision.payload["to"]["believed_value"] == "以为血脉秘密早已被人说破"
    assert decision.payload["valid_from_chapter"] == 7
    assert decision.payload["retracted_edge_ids"] == [decision.payload["from"]["edge_id"]]
    # 锚是文本引语，不是 ID，也不是 offset（decisions.py 的模块 docstring）。
    assert decision.quote_text == KNOWS_QUOTE
    assert decision.quote_sha256 == decisions.quote_hash(KNOWS_QUOTE)
    assert decision.chapter_number == 7


def test_actor_is_not_hardwired_to_the_author(world: World) -> None:
    """自动生效那条链是另一个 agent 的活，但它一旦来调这里，写下的边不该冒充作者
    亲手确认过的东西。"""
    result = _flip(world, EdgeType.BELIEVES, "以为已泄露", actor="autopilot")
    (decision,) = world.edits(decisions.DecisionKind.KNOWLEDGE_EDIT)
    assert decision.actor == "autopilot"
    assert world.edge(result.edge_id).source is EdgeSource.SYSTEM


def test_correcting_a_cell_into_what_it_already_is_refuses(world: World) -> None:
    before = world.version()
    with pytest.raises(CorrectionRefused):
        _flip(world, EdgeType.KNOWS)
    assert world.version() == before, "拒绝的代价必须是「什么都没发生」，包括不许推进版本"


def test_believes_without_a_value_and_knows_with_one_both_refuse(world: World) -> None:
    with pytest.raises(CorrectionRefused):
        _flip(world, EdgeType.BELIEVES, "   ")
    with pytest.raises(CorrectionRefused):
        _flip(world, EdgeType.KNOWS, "他知道的就是真的那一版")
    assert world.cell().state is KnowledgeState.KNOWS


def test_an_unknown_cell_has_nothing_to_correct(world: World) -> None:
    """闭世界下 UNKNOWN 不是一条事实，是「没有边」——改不了，只能声明（而声明要引语）。"""
    with pytest.raises(FactNotFound):
        correct_knowledge(
            world.conn,
            world.graph,
            world.project_id,
            character_id=world.peer_id,
            secret_id=world.secret_id,
            to_type=EdgeType.BELIEVES,
            believed_value="以为已泄露",
            expected_canon_version=world.version(),
        )


def test_a_location_in_the_secret_slot_refuses(world: World) -> None:
    with pytest.raises(CorrectionRefused):
        correct_knowledge(
            world.conn,
            world.graph,
            world.project_id,
            character_id=world.hero_id,
            secret_id=world.place_id,
            to_type=EdgeType.BELIEVES,
            believed_value="以为已泄露",
            expected_canon_version=world.version(),
        )


def test_a_stale_canon_version_refuses_before_touching_anything(world: World) -> None:
    with pytest.raises(project.StaleBaseVersion):
        _flip(world, EdgeType.BELIEVES, "以为已泄露", expected_canon_version=world.version() + 3)
    assert world.cell().state is KnowledgeState.KNOWS
    assert world.edits(decisions.DecisionKind.KNOWLEDGE_EDIT) == []


def test_the_correction_bumps_canon_exactly_once(world: World) -> None:
    before = world.version()
    result = _flip(world, EdgeType.BELIEVES, "以为已泄露")
    assert world.version() == before + 1 == result.canon_version


# ══════════════════════════════════════════════════════════════════════════
# 2 + 3. 事件的 knowers / participants
# ══════════════════════════════════════════════════════════════════════════


def _edit_cast(world: World, **kwargs: Any):
    return correct_event_cast(
        world.conn,
        world.graph,
        world.events,
        world.project_id,
        world.event_id,
        expected_canon_version=kwargs.pop("expected_canon_version", world.version()),
        **kwargs,
    )


def test_knowers_can_be_added_and_removed_without_deleting_a_row(world: World) -> None:
    """`knowers` 是抽取里**唯一靠推断得来的那一维**（谁在场是文本里写着的，谁因此知道了
    是猜的），所以它是最需要改的一维。删一个人 = 那一行 status 改成 RETRACTED，行留着。"""
    result = _edit_cast(world, knower_ids=[world.hero_id, world.third_id])

    assert {ref.id for ref in result.event.knowers} == {world.hero_id, world.third_id}
    assert [ref.id for ref in result.knowers_added] == [world.third_id]
    assert [ref.id for ref in result.knowers_removed] == [world.peer_id]
    rows = world.knower_rows()
    assert rows[world.peer_id] == "RETRACTED", "删掉的知情人那一行必须留着"
    assert rows[world.hero_id] == "ACTIVE" and rows[world.third_id] == "ACTIVE"


def test_participants_can_be_added_and_removed_without_deleting_a_row(world: World) -> None:
    result = _edit_cast(world, participant_ids=[world.hero_id, world.third_id])

    assert {ref.id for ref in result.event.participants} == {world.hero_id, world.third_id}
    rows = world.participant_rows()
    assert rows[world.peer_id] == "RETRACTED"
    assert rows[world.third_id] == "ACTIVE"


def test_a_retracted_participant_stops_pulling_the_event_into_context(world: World) -> None:
    """留着行不等于留着影响：读路径必须看不见它，否则「删掉」只是 UI 上的假动作。"""
    assert world.events.events_for_characters(
        world.project_id, [world.peer_id], 7, InformationScope.CANON
    )
    _edit_cast(world, participant_ids=[world.hero_id], knower_ids=[world.hero_id])
    assert (
        world.events.events_for_characters(
            world.project_id, [world.peer_id], 7, InformationScope.CANON
        )
        == []
    )


def test_re_adding_a_removed_person_restores_the_same_row(world: World) -> None:
    """加人先看有没有那一行——插第二行会直接撞主键（两张表的主键都不含 status）。"""
    _edit_cast(world, knower_ids=[world.hero_id], participant_ids=[world.hero_id])
    _edit_cast(
        world,
        knower_ids=[world.hero_id, world.peer_id],
        participant_ids=[world.hero_id, world.peer_id],
    )
    assert world.knower_rows()[world.peer_id] == "ACTIVE"
    assert world.participant_rows()[world.peer_id] == "ACTIVE"


def test_the_cast_edit_is_written_into_the_decision_log(world: World) -> None:
    result = _edit_cast(world, knower_ids=[world.hero_id, world.third_id])

    (decision,) = world.edits(decisions.DecisionKind.EVENT_EDIT)
    assert decision.id == result.decision_id
    assert decision.decision is decisions.Verdict.EDIT
    knowers = decision.payload["knowers"]
    assert [ref["name"] for ref in knowers["added"]] == ["李管家"]
    assert [ref["name"] for ref in knowers["removed"]] == ["萧决"]
    # 光有 diff 不够：中间夹进第二次编辑之后，只有 after 还能说出「现在是什么样」。
    assert {ref["name"] for ref in knowers["after"]} == {"顾清音", "李管家"}
    assert decision.quote_text == EVENT_QUOTE
    assert decision.chapter_number == 7


def test_an_edit_that_changes_nothing_is_refused(world: World) -> None:
    """空操作不该在那张只增不改的表里留一条「编辑」——它会把重放变成一串噪音。"""
    with pytest.raises(CorrectionRefused):
        _edit_cast(world, knower_ids=[world.hero_id, world.peer_id])
    assert world.edits(decisions.DecisionKind.EVENT_EDIT) == []


def test_a_location_cannot_be_added_to_the_cast(world: World) -> None:
    with pytest.raises(CorrectionRefused):
        _edit_cast(world, participant_ids=[world.hero_id, world.place_id])
    assert world.participant_rows()[world.peer_id] == "ACTIVE", "整次编辑判死，不是跳过那一个"


def test_editing_a_provisional_event_is_refused(world: World) -> None:
    """PROVISIONAL 的那条走审阅队列的 `edit`；本模块只改已经生效的事实。"""
    provisional = queries.event_id_by_anchor(
        world.conn,
        world.project_id,
        world.events.event(world.project_id, world.event_id).event.evidence_id,
        InformationScope.PROVISIONAL,
    )
    with pytest.raises(FactNotFound):
        correct_event_cast(
            world.conn,
            world.graph,
            world.events,
            world.project_id,
            provisional,
            knower_ids=[world.hero_id],
            expected_canon_version=world.version(),
        )


def test_the_cast_edit_needs_at_least_one_dimension(world: World) -> None:
    with pytest.raises(CorrectionRefused):
        _edit_cast(world)


# ══════════════════════════════════════════════════════════════════════════
# 出参收窄：秘密的内容一个字都不许出去
# ══════════════════════════════════════════════════════════════════════════


def test_no_correction_output_or_log_ever_serializes_secret_content(world: World) -> None:
    """`NodeProps` / `EdgeProps` 都是 `extra="allow"`，而这条边的 dst 按定义是一个 Secret。
    整份序列化出去就是**保密清单自己泄密**——所以出参只有 `NodeRef`，日志同理。"""
    result = _flip(world, EdgeType.BELIEVES, "以为血脉秘密早已被人说破")
    cast = _edit_cast(world, knower_ids=[world.hero_id, world.third_id])

    payloads = [
        result.model_dump_json(),
        cast.model_dump_json(),
        *(
            row["payload_json"]
            for row in world.conn.execute(
                "SELECT payload_json FROM decision_log WHERE project_id = ?",
                (world.project_id,),
            ).fetchall()
        ),
    ]
    for blob in payloads:
        assert TWIST not in blob, "Secret 的 props 泄漏了"
        assert SECRET_DESC not in blob, "secret 扩展表的描述泄漏了"
        assert '"props"' not in blob, "出参里出现了 props —— 只许出 NodeRef 的三个字段"

    # 反向断言：显示名**必须**在（面板本来就渲染它，收得太紧就没法给作者看这是哪一格）。
    assert "血脉秘密" in result.model_dump_json()


# ══════════════════════════════════════════════════════════════════════════
# HTTP：同一条能力在浏览器里也到得了
# ══════════════════════════════════════════════════════════════════════════


def _version(client: TestClient, pid: str) -> int:
    return client.get(f"/api/projects/{pid}").json()["canon_version"]


def test_http_knowledge_edit_flips_the_cell_and_leaks_nothing(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = book["pid"]
    declared = client.post(
        f"/api/projects/{pid}/declare/knows",
        json={
            "who": "萧决",
            "secret": "血脉秘密",
            "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
        },
    )
    assert declared.status_code == 200, declared.text

    response = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为血脉秘密只是市井传闻",
            "expected_canon_version": _version(client, pid),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_type"] == "KNOWS" and body["to_type"] == "BELIEVES"
    assert body["since_chapter"] == 1, "章号是产物：作者在这次请求里一个数字都没敲"
    from test_api import TWIST as API_TWIST

    assert API_TWIST not in response.text

    matrix = client.get(f"/api/projects/{pid}/chapters/1/matrix?cast=萧决").json()
    cell = next(c for c in matrix["cells"] if c["secret_id"] == book["血脉秘密"])
    assert cell["state"] == "BELIEVES"
    assert cell["believed_value"] == "以为血脉秘密只是市井传闻"


def test_http_knowledge_edit_maps_its_three_failures(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = book["pid"]
    missing = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为已泄露",
            "expected_canon_version": _version(client, pid),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "fact_not_found"

    client.post(
        f"/api/projects/{pid}/declare/knows",
        json={
            "who": "萧决",
            "secret": "血脉秘密",
            "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
        },
    )
    blank = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "expected_canon_version": _version(client, pid),
        },
    )
    assert blank.status_code == 422
    stale = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为已泄露",
            "expected_canon_version": _version(client, pid) + 5,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "stale_base_version"


def test_http_event_cast_edit_removes_a_knower(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = book["pid"]
    conn = connect(book["db"])
    try:
        graph = SqliteStoryGraph(conn)
        events = SqliteEventStore(conn)
        # 段号由定位算出来，不手填——手填的那个数会在正文改一个字之后变成一条假证据。
        candidate = Ledger(graph, conn, pid).locate("李管家什么也没说。")[0]
        evidence = graph.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=candidate.snapshot_id,
                para_index=candidate.para_index,
                occurrence_k=candidate.occurrence_k,
                quote_text=candidate.matched_text,
            )
        )
        provisional = events.put_provisional(
            ProvisionalEventSpec(
                project_id=pid,
                summary="李管家守口如瓶。",
                evidence_id=evidence.id,
                participant_ids=[book["萧决"], book["李管家"]],
                knower_ids=[book["萧决"], book["李管家"]],
                confidence=0.5,
            )
        )
        canon = events.clone_to_scope(provisional.event.id, InformationScope.CANON)
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        f"/api/projects/{pid}/canon/events/{canon.event.id}/cast",
        json={
            "knower_ids": [book["萧决"]],
            "expected_canon_version": _version(client, pid),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [ref["name"] for ref in body["knowers_removed"]] == ["李管家"]
    assert [ref["id"] for ref in body["event"]["knowers"]] == [book["萧决"]]
    assert set(body["knowers_removed"][0]) == {"id", "label", "name"}


def test_http_proposal_edit_route_exists_and_fixes_the_knowers(
    client: TestClient, book: dict[str, str]
) -> None:
    """**审阅队列这条路在此之前根本到不了 `edit`**：库里有这个动作，路由表里没有。

    「改」这条退路在 UI 上不存在时，作者面对一条 knowers 抽错的事件只有整条收下或整条丢掉。
    两条路能力不一致，他会学会先 reject 再重来，而那正好丢掉了证据链。

    ⚠️ **这条测试绿了两天，而屏幕上仍然只有两个按钮**：前端的 `ProposalAction` 联合里
    没有 `"edit"`，这条路由一个调用方都没有。**「路由通了」不等于「作者做得到」**——
    浏览器那一侧归 `frontend/src/components/ProposalReviewTab.test.tsx`
    （「改一改再收下」那一组，2026-08-13 补），两条一起才算这个洞补上了。
    """
    from test_api import _seed_low_confidence_proposal

    pid = book["pid"]
    proposal_id, _event_id, base = _seed_low_confidence_proposal(book)

    response = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/edit",
        json={
            "knower_ids": [book["萧决"]],
            "expected_canon_version": base,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "EDITED"
    assert body["canon_version"] == base + 1
    assert [ref["id"] for ref in body["event"]["knowers"]] == [book["萧决"]]

    empty = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/edit",
        json={"expected_canon_version": base + 1},
    )
    assert empty.status_code == 422, "一次什么都不改的 edit 不是编辑"


def test_http_event_cast_edit_404s_on_an_unknown_event(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = book["pid"]
    response = client.post(
        f"/api/projects/{pid}/canon/events/event:nope/cast",
        json={"knower_ids": [book["萧决"]], "expected_canon_version": _version(client, pid)},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "fact_not_found"


def test_the_module_never_reaches_for_a_chapter_number(world: World) -> None:
    """约束 10 在**运行时**的复读：整条改正链路里，`valid_from` 只可能是被改那条边的值。

    上面 `test_knows_becomes_believes...` 已经断言了结果，这一条断言的是**来源**——
    把库里那条边的章号改掉，改正出来的新边必须跟着变，而不是跟着任何一个常量。
    """
    edge = queries.current_knowledge_edges(
        world.conn, world.project_id, world.hero_id, world.secret_id
    )[0]
    assert edge.valid_from_chapter == 7
    result = _flip(world, EdgeType.BELIEVES, "以为已泄露")
    assert result.since_chapter == edge.valid_from_chapter
    assert json.loads(
        world.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind = 'knowledge_edit'"
        ).fetchone()["payload_json"]
    )["valid_from_chapter"] == edge.valid_from_chapter


def test_corrections_module_exposes_no_update_or_delete_entry_point() -> None:
    """同 `decisions.py` 的那条自守卫：改正层的公开名字里不许出现 delete / update。

    「改一条事实」的正确形状永远是撤回 + 写新的；一个 `delete_fact()` 出现在这个模块的
    API 面上，就是把 append-only 从「物理上做不到」降级成「大家记得别用」。
    """
    banned = [
        name
        for name in dir(corrections)
        if not name.startswith("_")
        and any(word in name.lower() for word in ("delete", "remove", "update", "drop"))
    ]
    assert banned == [], f"corrections.py 长出了修改/删除入口：{banned}"
