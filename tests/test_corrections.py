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
)
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.events import ProvisionalEventSpec
from novel_harness.graph import (
    ChapterSpec,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
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
    place_id: str
    event_id: str

    def version(self) -> int:
        return project.require_canon_version(self.conn, self.project_id)

    def edge(self, edge_id: str) -> Any:
        return queries.fetch_edge(self.conn, edge_id)

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
    """一条已生效的事件。**走生产写路径建出来**：
    `put_provisional` → `clone_to_scope(CANON)`（抽取 → 审阅通过的那条链）。
    直接 INSERT 会让这些测试量的是一份手写数据，而不是引擎。

    （2026-08-24 之前这儿还建一条 KNOWS 边。秘密下线之后 `declare_knows` 和
    改它的那两条链都没了，ADR 0039。）
    """
    pid = project.create(conn, name="青云记-corrections", root_path=".").id
    graph = SqliteStoryGraph(conn)
    ledger = Ledger(graph, conn, pid)
    hero = ledger.declare_node(NodeLabel.CHARACTER, "顾清音")
    peer = ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    third = ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    place = ledger.declare_node(NodeLabel.LOCATION, "北荒")
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
        place_id=place.id,
        event_id=canon_event.event.id,
    )
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


def test_no_correction_output_or_log_ever_serializes_node_props(world: World) -> None:
    """`NodeProps` / `EdgeProps` 都是 `extra="allow"` —— **作者写在节点上的任何东西
    都会原样穿过 `model_dump_json()`**。所以出参只许有 `NodeRef` 的三个字段，日志同理。

    （2026-08-24 之前这条量的是秘密节点上的 `props.twist` 和 `secret` 扩展表的描述。
    秘密下线之后毒药换成人物节点上的 `props.twist`——**判据一个字没改**。）
    """
    world.graph.upsert_node(
        NodeSpec(
            project_id=world.project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps.model_validate({"twist": TWIST}),
        )
    )
    world.conn.commit()
    cast = _edit_cast(world, knower_ids=[world.hero_id, world.third_id])

    payloads = [
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
        assert TWIST not in blob, "节点 props 里作者写的东西泄漏了"
        assert '"props"' not in blob, "出参里出现了 props —— 只许出 NodeRef 的三个字段"

    # 反向断言：显示名**必须**在（收得太紧就没法给作者看这是哪一条）。
    assert "顾清音" in cast.model_dump_json()


# ══════════════════════════════════════════════════════════════════════════
# HTTP：同一条能力在浏览器里也到得了
# ══════════════════════════════════════════════════════════════════════════


def _version(client: TestClient, pid: str) -> int:
    return client.get(f"/api/projects/{pid}").json()["canon_version"]
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
