"""角色册条目的**删和改名**，以及左栏那一列出场章数（2026-08-25）。

── 这个文件为什么必须和「抽取自动建人物」同一批落地 ──────────────────────

抽取从这一天起**认不出就建**（[ADR 0020](../docs/adr/0020-clean-extraction-auto-canon.md)
的补记）。它会认错——真书上的实例是「袭人」：那本书满篇「寒气袭人」「香气袭人」，
模型把它当成人报上来，引擎照建不误。

**自动建 + 不能删 = 单向阀。** 那个错会永远留在角色册里，往「这一章提到了谁」和
喂给模型的上下文里塞噪声，而作者没有任何办法清掉它。所以这两条路由不是「顺手加的
功能」，是那条裁定的**配套**——两者一起进仓库，或者都不进。

── 删除的语义：2026-08-28 起从「拒绝」换成「直接删 + 事后通知」───────────

到 `node` 的那几条外键（`alias` / `summary_mention` / `edge.src|dst` /
`event_participant` / `event_knower`）**全是 ON DELETE CASCADE**，所以一句
`DELETE FROM node` 技术上就过了，而且一声不吭——**这件事从来没变过**。变的是
拿它怎么办：曾经的做法是数出来非零就拒绝（同 `delete_chapter`），维护者裁定
换成「删照做，把挡路的东西变成剩下的人身上一条看得见的通知」
（`event_cast_changed`，完整论证在 `graph.models.NodeUsage` 和
`api/characters.py::delete_node`）。**这条通知只告警,不阻断**——它不进
`BLOCKING_KINDS`,不会连带停掉哪一章的总结/抽取,这一点本文件下面有专门的
测试钉着。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import EdgeSpec, EdgeType, InformationScope, NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.graph.store import StoreError
from novel_harness.project import create as create_project
from novel_harness.system_notifications import BLOCKING_KINDS


class World:
    def __init__(self, tmp: Path) -> None:
        self.path = tmp / "b.db"
        self.root = tmp / "book"
        self.conn = connect(self.path)
        migrate(self.conn)
        self.pid = create_project(self.conn, name="角色册", root_path=str(self.root)).id
        self.graph = SqliteStoryGraph(self.conn)
        ledger = Ledger(self.graph, self.conn, self.pid)
        self.hero = ledger.declare_node(NodeLabel.CHARACTER, "萧决").id
        # 「袭人」：模型从「寒气袭人」里认出来的那个假人物。**这个 fixture 的主角。**
        self.ghost = ledger.declare_node(NodeLabel.CHARACTER, "袭人").id
        self.place = ledger.declare_node(NodeLabel.LOCATION, "北荒").id
        src = tmp / "s.txt"
        self.root.mkdir(parents=True, exist_ok=True)
        src.write_text(
            "第一章 甲\n\n萧决走进了北荒，寒气袭人。\n", encoding="utf-8"
        )
        importer.import_book(self.graph, self.pid, txt=src, root=self.root)
        self.conn.commit()

    def canon(self) -> int:
        return project.require_canon_version(self.conn, self.pid)

    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("NH_DB", str(self.path))
        from novel_harness.api.app import app

        return TestClient(app)

    def summary(self, chapter: int, text: str) -> None:
        from novel_harness.draft.rolling_summary import save_author_summary

        save_author_summary(
            self.conn, project_id=self.pid, chapter_number=chapter, text=text
        )
        self.conn.commit()


@pytest.fixture
def world() -> World:
    return World(Path(tempfile.mkdtemp()))


# ══════════════════════════════════════════════════════════════════════════
# ① 删：不管挂没挂东西，都删得掉；挂着东西的数出来当回执
# ══════════════════════════════════════════════════════════════════════════


def test_a_node_nothing_points_at_can_be_deleted(world: World) -> None:
    """「袭人」身上只有它自己的 canonical 别名 —— 删得掉。

    **这一条就是自动建人物那条裁定的出口。** 它红了 = 单向阀回来了。
    """
    usage = world.graph.node_usage(world.pid, world.ghost)
    assert usage.is_free() and usage.name == "袭人"

    deleted = world.graph.delete_node(world.pid, world.ghost)

    assert deleted.is_free()
    assert world.graph.resolve(world.pid, ["袭人"])[0].hits == []
    # 它自己的名字跟着一起没（`alias` 是 CASCADE）——人没了名字跟着没，天经地义。
    assert (
        world.conn.execute(
            "SELECT COUNT(*) FROM alias WHERE node_id = ?", (world.ghost,)
        ).fetchone()[0]
        == 0
    )


def test_a_node_an_edge_points_at_is_deleted_and_the_edge_goes_with_it(world: World) -> None:
    """有关系引着它 → 照样删掉，`NodeUsage` 只是回执，不是闸。

    `edge.src|dst` → `node` 是 CASCADE：那条边跟着一起没——2026-08-28 起这不是
    「悄悄蒸发」的坏事，是裁定要的行为（防线搬到了 `event_cast_changed` 通知那边，
    边没有对应的通知机制，因为关系不像事件那样"还有别人在场"这件事值得说）。
    """
    world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.pid,
            src=world.hero,
            dst=world.place,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=1,
            information_scope=InformationScope.CANON,
        )
    )
    world.conn.commit()

    usage = world.graph.delete_node(world.pid, world.place)

    assert usage.edges == 1 and usage.name == "北荒"
    assert world.graph.resolve(world.pid, ["北荒"])[0].hits == []
    # 那条边真的没了——不是「删了一半」。
    assert world.graph.state_at(world.pid, world.hero, 1).location is None


def test_an_event_roster_survives_the_delete_with_a_smaller_cast(world: World) -> None:
    """情节名单里有他 → 照样删掉，**事件本身不会跟着没**，只是名单少一个人。

    `event_participant`/`event_knower` → `node` 是 CASCADE：级联删的是这个人
    在名单里的那一行，`story_event` 那一行没有任何外键指着某个具体角色，
    删不到它。**在场和知情算同一条情节，不相加**（同 `chapter_usage` 里
    `edges` 那条 `OR` 的理由）——这条纪律没变，变的只是「数出来非零」以后不再拒绝。
    """
    from novel_harness.events import ProvisionalEventSpec
    from novel_harness.graph import EvidenceSpec
    from novel_harness.graph.sqlite_events import SqliteEventStore

    snapshot_id = str(
        world.conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1",
            (world.pid,),
        ).fetchone()["id"]
    )
    evidence = world.graph.put_evidence(
        EvidenceSpec(
            project_id=world.pid,
            chapter_snapshot_id=snapshot_id,
            para_index=2,
            quote_text="萧决走进了北荒，寒气袭人。",
        )
    )
    events = SqliteEventStore(world.conn)
    view = events.put_provisional(
        ProvisionalEventSpec(
            project_id=world.pid,
            summary="萧决走进北荒。",
            evidence_id=evidence.id,
            participant_ids=[world.hero],
            knower_ids=[world.hero],  # 同一个人两处都在
            confidence=0.9,
        )
    )
    world.conn.commit()

    usage = world.graph.node_usage(world.pid, world.hero)
    assert usage.events == 1, f"在场 + 知情该只算一条情节，实得 {usage.events}"

    deleted = world.graph.delete_node(world.pid, world.hero)
    world.conn.commit()

    assert deleted.events == 1 and deleted.name == "萧决"
    # 事件本身还在，只是名单空了——不是「情节跟着人一起没」。
    still_there = events.event(world.pid, view.event.id)
    assert still_there is not None
    assert still_there.participants == [] and still_there.knowers == []


def test_summary_mentions_never_count_as_usage(world: World) -> None:
    """倒排索引行**不算**「挡路」—— 它是派生数据，下一次 `_ensure` 重算。

    这条差集今天仍然决定通知触不触发的边界（`event_cast_changed` 只在
    `NodeUsage.events > 0` 时才有事件要通知），只是不再决定删不删得掉。
    """
    from novel_harness.summary_index import ensure_index

    world.summary(1, "寒气袭人，萧决走进北荒。")
    ensure_index(world.conn, world.graph, world.pid)
    world.conn.commit()

    rows = world.conn.execute(
        "SELECT COUNT(*) FROM summary_mention WHERE node_id = ?", (world.ghost,)
    ).fetchone()[0]
    assert rows > 0, "前提：这段总结里确实提到了「袭人」，不然这条在空转"

    assert world.graph.node_usage(world.pid, world.ghost).is_free()
    world.graph.delete_node(world.pid, world.ghost)
    assert (
        world.conn.execute(
            "SELECT COUNT(*) FROM summary_mention WHERE node_id = ?", (world.ghost,)
        ).fetchone()[0]
        == 0
    ), "索引行没跟着走 —— 那就是悬空引用"


def test_a_deleted_node_stops_showing_up_in_who_this_chapter_mentions(
    world: World,
) -> None:
    """删完之后，「这一章提到了谁」里再也没有它。

    **这一条是删除真正的验收**：`summary_mention` 那张倒排表和 `mentioned_cast`
    走的是两条独立的路（前者按总结重扫，后者现编 alternation 打正文）。
    只验前者的话，一个删掉的名字会继续从正文那一侧冒出来——而作者删它正是因为
    不想再在上下文里看见它。
    """
    from novel_harness.mentioned import mentioned_cast
    from novel_harness.summary_index import ensure_index, mentions_in_chapter

    world.summary(1, "寒气袭人，萧决走进北荒。")
    ensure_index(world.conn, world.graph, world.pid)
    world.conn.commit()

    paras = ["萧决走进了北荒，寒气袭人。"]
    assert "袭人" in mentioned_cast(world.graph, world.pid, paras), (
        "前提：删之前它确实会从正文里被认出来，不然这条在空转"
    )
    assert any(
        hit.node.name == "袭人"
        for hit in mentions_in_chapter(world.conn, world.graph, world.pid, 1)
    ), "前提：删之前它确实在这一章的总结芯片里"

    world.graph.delete_node(world.pid, world.ghost)
    world.conn.commit()

    assert "袭人" not in mentioned_cast(world.graph, world.pid, paras)
    assert all(
        hit.node.name != "袭人"
        for hit in mentions_in_chapter(world.conn, world.graph, world.pid, 1)
    )


def test_the_whole_db_has_no_dangling_reference_after_a_delete(world: World) -> None:
    """删完之后 `PRAGMA foreign_key_check` 一条都不许报。

    **这是「不许留下悬空引用」那条要求的机器判据**，不是靠逐表数一遍——
    逐表数会漏掉下一张引着 `node` 的新表，而这一条不会。
    """
    world.summary(1, "寒气袭人，萧决走进北荒。")
    from novel_harness.summary_index import ensure_index

    ensure_index(world.conn, world.graph, world.pid)
    world.graph.delete_node(world.pid, world.ghost)
    world.conn.commit()

    assert world.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert world.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ══════════════════════════════════════════════════════════════════════════
# ② 改名：两处一起改
# ══════════════════════════════════════════════════════════════════════════


def test_rename_moves_the_canonical_alias_with_it(world: World) -> None:
    """`node.name` 和 canonical 别名**同一个事务里一起改**。

    只改前者的后果：正文里叫新名字的地方再也匹配不到他（`mentions.py` 那条
    alternation 编的是别名表，不是 `node.name`），而没有任何一步会报错。
    """
    renamed = world.graph.rename_node(world.pid, world.ghost, "花袭人")

    assert renamed.name == "花袭人"
    assert world.graph.resolve(world.pid, ["袭人"])[0].hits == [], "旧名字还认得出人"
    hit = world.graph.resolve(world.pid, ["花袭人"])[0].unique_node
    assert hit is not None and hit.id == world.ghost


def test_rename_recomputes_usable_for_rules(world: World) -> None:
    """新名字够长了，`usable_for_rules` 要跟着回来。

    `upsert_node` 建 canonical 别名时判据是 `len(name) >= 2`（schema 那条 CHECK 也是
    这么写的）。改名不重算的话，一个从「凌」改成「凌霄」的人会永远匹配不到正文——
    而作者看到的只是「改完名字之后这个人从检查里消失了」。
    """
    ledger = Ledger(world.graph, world.conn, world.pid)
    short = ledger.declare_node(NodeLabel.CHARACTER, "凌").id
    world.conn.commit()
    assert (
        world.conn.execute(
            "SELECT usable_for_rules FROM alias WHERE node_id = ? AND kind = 'canonical'",
            (short,),
        ).fetchone()[0]
        == 0
    ), "前提：1 字名的 canonical 别名不许被规则拿去匹配正文（ADR 0004）"

    world.graph.rename_node(world.pid, short, "凌霄")

    assert (
        world.conn.execute(
            "SELECT usable_for_rules FROM alias WHERE node_id = ? AND kind = 'canonical'",
            (short,),
        ).fetchone()[0]
        == 1
    )


def test_rename_refuses_a_name_that_already_exists(world: World) -> None:
    """撞上同 label 同名 → 拒绝。

    放行的后果是 `resolve` 返回两个 hit ⇒ 歧义 ⇒ `usable_for_rules` 为假 ⇒
    **面板上整行消失**，而没有一步会报错（同 `upsert_node` 那条论证）。
    """
    with pytest.raises(StoreError, match="已经有一个"):
        world.graph.rename_node(world.pid, world.ghost, "萧决")

    with pytest.raises(StoreError, match="空白"):
        world.graph.rename_node(world.pid, world.ghost, "   ")

    # 同名但**不同 label** 不算撞：角色册里一个叫「北荒」的人和一个叫「北荒」的地点
    # 是两件事，`resolve` 靠 label 分得开。
    renamed = world.graph.rename_node(world.pid, world.ghost, "北荒")
    assert renamed.name == "北荒"


# ══════════════════════════════════════════════════════════════════════════
# ③ 事件挂在角色下面（2026-08-25）
#
# 维护者的原话：「事件是**比较小的一条总结**。只放到和它相关的那个角色下面……
# 一件事情如果跟好多人相关，那就放到每个相关人的下面。」
#
# 存储那一侧本来就是这个形状（`event_participant` 是多对多），缺的只是「按人看」
# 那个出口——今天跟事件有关的路由全是按章看或按事件 id 看。
# ══════════════════════════════════════════════════════════════════════════


def _put_event(
    world: World,
    *,
    chapter: int,
    para: int,
    quote: str,
    summary: str,
    participants: list[str],
    knowers: list[str] | None = None,
) -> str:
    from novel_harness.events import ProvisionalEventSpec
    from novel_harness.graph import EvidenceSpec
    from novel_harness.graph.sqlite_events import SqliteEventStore

    snapshot_id = str(
        world.conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = ? AND cs.text_sha256 = c.text_sha256",
            (world.pid, chapter),
        ).fetchone()["id"]
    )
    evidence = world.graph.put_evidence(
        EvidenceSpec(
            project_id=world.pid,
            chapter_snapshot_id=snapshot_id,
            para_index=para,
            quote_text=quote,
        )
    )
    view = SqliteEventStore(world.conn).put_provisional(
        ProvisionalEventSpec(
            project_id=world.pid,
            summary=summary,
            evidence_id=evidence.id,
            participant_ids=participants,
            knower_ids=knowers or [],
            confidence=0.9,
        )
    )
    world.conn.commit()
    return view.event.id


def test_one_event_with_three_people_shows_up_under_all_three(world: World) -> None:
    """**一件事跟三个人相关 → 三个人名下都查得到。** 这就是裁定的那句话。

    存储不用改：`event_participant` 已经是多对多，一件事挂三行。
    这一条钉的是「按人看」这个出口真的把那三行都读出来了。
    """
    from novel_harness.graph.sqlite_events import SqliteEventStore

    third = Ledger(world.graph, world.conn, world.pid).declare_node(
        NodeLabel.CHARACTER, "顾清音"
    ).id
    world.conn.commit()
    event_id = _put_event(
        world,
        chapter=1,
        para=2,
        quote="萧决走进了北荒，寒气袭人。",
        summary="三个人在北荒碰了面。",
        participants=[world.hero, world.ghost, third],
    )

    events = SqliteEventStore(world.conn)
    for who in (world.hero, world.ghost, third):
        rows = events.events_for_one_character(world.pid, who, InformationScope.PROVISIONAL)
        assert [view.event.id for view in rows] == [event_id], f"{who} 名下没有这件事"
        # 每一行都带**整份**名单：界面才说得出「还有：…」。
        assert len(rows[0].participants) == 3


def test_the_timeline_is_ordered_by_chapter_and_never_sliced(world: World) -> None:
    """按章号升序，**且不按任何「当前章」切片**（2026-08-25 的裁定：全给 + 每条带章号）。

    换成后端切的话，作者点开一个人只看得到当前章之前的部分，而他打开角色册
    正是为了看整条线。这一条同时是「别让下一个人以为忘了做时态」的机器判据。
    """
    from novel_harness.graph.sqlite_events import SqliteEventStore

    src = world.root.parent / "more.txt"
    src.write_text(
        "第一章 甲\n\n萧决走进了北荒，寒气袭人。\n\n"
        "第二章 乙\n\n他在城门口等了很久。\n\n"
        "第三章 丙\n\n雪停了。\n",
        encoding="utf-8",
    )
    importer.import_book(world.graph, world.pid, txt=src, root=world.root)
    world.conn.commit()

    third = _put_event(
        world, chapter=3, para=2, quote="雪停了。", summary="第三章那件事。",
        participants=[world.hero],
    )
    first = _put_event(
        world, chapter=1, para=2, quote="萧决走进了北荒，寒气袭人。",
        summary="第一章那件事。", participants=[world.hero],
    )

    rows = SqliteEventStore(world.conn).events_for_one_character(
        world.pid, world.hero, InformationScope.PROVISIONAL
    )
    assert [view.event.id for view in rows] == [first, third], "没按章号排"
    assert [view.event.chapter_number for view in rows] == [1, 3]


def test_a_retracted_event_leaves_the_timeline(world: World) -> None:
    """撤回过的事件不在线上 —— 它的语义是「这件事从未发生过」。

    掉的只是那两条章号边界，`status = 'ACTIVE'` 这一条一个都不许再掉：
    把作者亲手撤掉的一条摆回他的时间线，比不给他这条线更糟。
    """
    from novel_harness.graph.sqlite_events import SqliteEventStore

    event_id = _put_event(
        world, chapter=1, para=2, quote="萧决走进了北荒，寒气袭人。",
        summary="要被撤回的那件事。", participants=[world.hero],
    )
    events = SqliteEventStore(world.conn)
    assert events.events_for_one_character(
        world.pid, world.hero, InformationScope.PROVISIONAL
    ), "前提：撤回之前它在线上"

    world.conn.execute(
        "UPDATE story_event SET status = 'RETRACTED' WHERE id = ?", (event_id,)
    )
    world.conn.commit()

    assert (
        events.events_for_one_character(
            world.pid, world.hero, InformationScope.PROVISIONAL
        )
        == []
    )


def test_the_character_events_route_returns_the_timeline(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP 那一层：按章排、每条带章号和参与人。**只出 CANON。**"""
    from novel_harness.graph import InformationScope as Scope
    from novel_harness.graph.sqlite_events import SqliteEventStore

    event_id = _put_event(
        world, chapter=1, para=2, quote="萧决走进了北荒，寒气袭人。",
        summary="两个人在北荒碰了面。", participants=[world.hero, world.ghost],
    )
    # 升 CANON —— 这条路由只出确认过的。
    SqliteEventStore(world.conn).clone_to_scope(event_id, Scope.CANON)
    world.conn.commit()

    with world.client(monkeypatch) as client:
        rows = client.get(
            f"/api/projects/{world.pid}/characters/{world.hero}/events"
        ).json()

    assert [r["chapter_number"] for r in rows] == [1]
    assert rows[0]["summary"] == "两个人在北荒碰了面。"
    assert sorted(p["name"] for p in rows[0]["participants"]) == sorted(["袭人", "萧决"])
    # 窄引用：`Node.props` 一个字段都不出。
    assert all(set(p) == {"id", "label", "name"} for p in rows[0]["participants"])


def test_the_character_events_route_refuses_a_non_character(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拿一个地点去问「他的事件」→ 422，不是一个空表。

    空表会让作者以为「这个地点没发生过事」，而真相是这个问题问错了
    （§10 约束 8：静默的零和真的零不许长得一样）。
    """
    with world.client(monkeypatch) as client:
        r = client.get(f"/api/projects/{world.pid}/characters/{world.place}/events")
    assert r.status_code == 422, r.text


# ══════════════════════════════════════════════════════════════════════════
# ③ HTTP：两条路由 + 角色册那一列
# ══════════════════════════════════════════════════════════════════════════


def test_the_roster_carries_the_appearance_count_in_the_same_response(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """左栏那一行要显示「贾环 · 42 章」——**这个数跟角色册同一条出参回来**。

    多一次往返就是多一次会失败、会晚到的东西，而它只是一行字里的一个数。
    """
    world.summary(1, "萧决走进北荒。")

    with world.client(monkeypatch) as client:
        rows = client.get(f"/api/projects/{world.pid}/roster").json()

    by_name = {row["name"]: row for row in rows}
    assert by_name["萧决"]["appearance_chapters"] == 1
    assert by_name["北荒"]["appearance_chapters"] == 1
    # 没被任何一段总结提到的条目是 0，**不是缺这个键**——静默的零和真的零不许
    # 长得一样（§10 约束 8），而「缺键」在前端会渲染成 `undefined`。
    assert by_name["袭人"]["appearance_chapters"] == 0
    assert all("props" not in row for row in rows), "角色册不许整体序列化 Node.props"


def test_the_delete_route_refuses_with_a_stale_canon_version(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """拿着一份旧角色册点删除 → 409，不是「删掉了一个他没看见的东西」。"""
    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        stale = client.delete(
            f"{base}/nodes/{world.ghost}", params={"expected_canon_version": 0}
        )
        current = client.get(base).json()["canon_version"]
        assert stale.status_code == 409 or current == 0, stale.text

        ok = client.delete(
            f"{base}/nodes/{world.ghost}", params={"expected_canon_version": current}
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["name"] == "袭人" and body["usage"]["edges"] == 0

        # 删完立刻不在角色册里了（前端那一行「删完立刻消失」量的就是这个）。
        names = {row["name"] for row in client.get(f"{base}/roster").json()}
        assert "袭人" not in names


def test_the_delete_route_succeeds_and_hands_the_counts_to_the_author(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """挂着关系也是 200，不是 409——`usage` 只是回执，说清带走了什么。"""
    world.graph.upsert_edge(
        EdgeSpec(
            project_id=world.pid,
            src=world.hero,
            dst=world.place,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=1,
            information_scope=InformationScope.CANON,
        )
    )
    world.conn.commit()

    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        resp = client.delete(
            f"{base}/nodes/{world.place}", params={"expected_canon_version": current}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "北荒"
    assert body["usage"]["edges"] == 1


def _put_three_person_event(world: World):
    """三个人（`world.hero` + 两个新建的）都在场的一件事，落在第 1 章原文的引语上。

    测试专用 helper——`event_cast_changed` 那两条 API 测试都要这份夹具，
    抽出来避免同一段 setup 抄两遍。
    """
    from novel_harness.events import ProvisionalEventSpec
    from novel_harness.graph import EvidenceSpec
    from novel_harness.graph.sqlite_events import SqliteEventStore

    ledger = Ledger(world.graph, world.conn, world.pid)
    second = ledger.declare_node(NodeLabel.CHARACTER, "顾清音").id
    third = ledger.declare_node(NodeLabel.CHARACTER, "李管家").id
    snapshot_id = str(
        world.conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1",
            (world.pid,),
        ).fetchone()["id"]
    )
    evidence = world.graph.put_evidence(
        EvidenceSpec(
            project_id=world.pid,
            chapter_snapshot_id=snapshot_id,
            para_index=2,
            quote_text="萧决走进了北荒，寒气袭人。",
        )
    )
    events = SqliteEventStore(world.conn)
    provisional = events.put_provisional(
        ProvisionalEventSpec(
            project_id=world.pid,
            summary="三人在北荒相遇。",
            evidence_id=evidence.id,
            participant_ids=[world.hero, second, third],
            knower_ids=[],
            confidence=0.9,
        )
    )
    # `events_for_one_character` 默认只看 CANON（同 `character_events` 路由的口径：
    # PROVISIONAL 是抽取器猜的、没确认的，混进作者能看见的线就是把猜测当事实）——
    # `event_cast_changed` 通知走的是同一条口径，所以测试夹具也必须是 CANON，
    # 不然这条通知在 PROVISIONAL 事件上结构性地永远不会触发。
    view = events.clone_to_scope(provisional.event.id, InformationScope.CANON)
    world.conn.commit()
    return events, view, second, third


def test_the_delete_route_notifies_the_remaining_cast(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删一个牵扯 3 个人的情节里的一个人 → 人没了、事件还在、剩下两个人身上
    （这件事上）查得到「掉了一个参与者」的通知，带着能点过去的锚。
    """
    events, view, second, third = _put_three_person_event(world)

    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        resp = client.delete(
            f"{base}/nodes/{world.hero}", params={"expected_canon_version": current}
        )
        assert resp.status_code == 200, resp.text
        # 2 条：`clone_to_scope` 是克隆不是搬迁，PROVISIONAL 原件和 CANON 副本
        # 都挂着这个人，`node_usage` 如实数两条——这是生产会有的真实形状
        # （auto-Canon 从不撤走 PROVISIONAL 原件），不是测试夹具的巧合。
        assert resp.json()["usage"]["events"] == 2

        notifications = client.get(f"{base}/notifications").json()

    matches = [n for n in notifications if n["kind"] == "event_cast_changed"]
    assert len(matches) == 1, notifications
    note = matches[0]
    assert note["subject_type"] == "canon_event" and note["subject_id"] == view.event.id
    assert note["title_params"] == {"removed_name": "萧决", "remaining_count": 2}
    assert note["jump"]["quote_text"] == "萧决走进了北荒，寒气袭人。"
    # 双保险：这条通知不在阻断名单里（下面那条测试钉的是常量本身）。
    assert note["kind"] not in BLOCKING_KINDS

    still_there = events.event(world.pid, view.event.id)
    assert still_there is not None
    assert {p.id for p in still_there.participants} == {second, third}


def test_both_remaining_cast_members_see_the_same_notification_on_their_own_card(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一件事跟三个人相关，删掉一个之后，**剩下两个人各自的 `character_events()`**
    （不是同一次请求，是两次独立的、按人查的调用）都要拼得出同一条通知——
    `subject_id` 相同、`id` 相同，不是两条内容一样但各是各的。

    这条测的是维护者点名的那个边界：`character_events()` 按人查，通知按事件挂，
    「同一条事件挂在三个人名下」时不能只有一个人身上带得出这条通知。
    """
    _, view, second, third = _put_three_person_event(world)

    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        client.delete(
            f"{base}/nodes/{world.hero}", params={"expected_canon_version": current}
        )

        second_row = next(
            r
            for r in client.get(f"{base}/characters/{second}/events").json()
            if r["event_id"] == view.event.id
        )
        third_row = next(
            r
            for r in client.get(f"{base}/characters/{third}/events").json()
            if r["event_id"] == view.event.id
        )

    assert second_row["cast_changed"] is not None
    assert third_row["cast_changed"] is not None
    assert second_row["cast_changed"]["id"] == third_row["cast_changed"]["id"]
    assert second_row["cast_changed"]["subject_id"] == view.event.id
    assert second_row["cast_changed"]["title_params"] == {
        "removed_name": "萧决",
        "remaining_count": 2,
    }


def test_a_clean_event_has_no_cast_changed_flag(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没被动过的事件，`cast_changed` 就是 `None`——不是空对象，不是缺字段。"""
    _, view, second, _third = _put_three_person_event(world)

    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        row = next(
            r
            for r in client.get(f"{base}/characters/{second}/events").json()
            if r["event_id"] == view.event.id
        )

    assert row["cast_changed"] is None


def test_deleting_an_unreferenced_node_creates_no_notification(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删一个谁也不牵扯的人 → 干净删掉，没有多余标记。"""
    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        resp = client.delete(
            f"{base}/nodes/{world.ghost}", params={"expected_canon_version": current}
        )
        assert resp.status_code == 200, resp.text
        usage = resp.json()["usage"]
        assert usage["edges"] == 0 and usage["events"] == 0

        notifications = client.get(f"{base}/notifications").json()

    assert notifications == []


def test_event_cast_changed_is_not_a_blocking_kind() -> None:
    """删人不该换来「这一章的自动整理停了」——`event_cast_changed` 永不进
    `BLOCKING_KINDS`。挂错档的后果是作者删一个人就把总结/抽取停掉，而那不会
    有任何东西报错，只会表现成「总结怎么一直不更新」（`system_notifications.py`
    模块头那条纪律）。
    """
    assert "event_cast_changed" not in BLOCKING_KINDS
    assert BLOCKING_KINDS == frozenset({"validation_blocked"})


def test_the_rename_route_returns_a_narrow_ref(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """改名的回执是 `NodeRef`（id/label/name），**不带 props**。"""
    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        renamed = client.patch(
            f"{base}/nodes/{world.ghost}",
            json={"name": "花袭人", "expected_canon_version": current},
        )

    assert renamed.status_code == 200, renamed.text
    assert set(renamed.json()) == {"id", "label", "name"}
    assert renamed.json()["name"] == "花袭人"


def test_the_rename_route_reports_a_clash_as_409(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with world.client(monkeypatch) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(base).json()["canon_version"]
        clash = client.patch(
            f"{base}/nodes/{world.ghost}",
            json={"name": "萧决", "expected_canon_version": current},
        )

    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"]["error"] == "rename_refused"
    # 这句话原样摆给作者看，所以它必须是中文的、说得出为什么。
    assert "歧义" in clash.json()["detail"]["message"]
