"""花名册条目的**删和改名**，以及左栏那一列出场章数（2026-08-25）。

── 这个文件为什么必须和「抽取自动建人物」同一批落地 ──────────────────────

抽取从这一天起**认不出就建**（[ADR 0020](../docs/adr/0020-clean-extraction-auto-canon.md)
的补记）。它会认错——真书上的实例是「袭人」：那本书满篇「寒气袭人」「香气袭人」，
模型把它当成人报上来，引擎照建不误。

**自动建 + 不能删 = 单向阀。** 那个错会永远留在花名册里，往「这一章提到了谁」和
喂给模型的上下文里塞噪声，而作者没有任何办法清掉它。所以这两条路由不是「顺手加的
功能」，是那条裁定的**配套**——两者一起进仓库，或者都不进。

── 删除的语义：拒绝，不是连带删除 ────────────────────────────────────────

到 `node` 的那几条外键（`alias` / `summary_mention` / `edge.src|dst` /
`event_participant` / `event_knower`）**全是 ON DELETE CASCADE**，所以一句
`DELETE FROM node` 技术上就过了，而且一声不吭。这里和 `delete_chapter` 走同一套：
数出来非零就拒绝，把挡路的东西数给作者看。完整论证在 `graph.models.NodeUsage`。
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
from novel_harness.graph.store import NodeInUse, StoreError
from novel_harness.project import create as create_project


class World:
    def __init__(self, tmp: Path) -> None:
        self.path = tmp / "b.db"
        self.root = tmp / "book"
        self.conn = connect(self.path)
        migrate(self.conn)
        self.pid = create_project(self.conn, name="花名册", root_path=str(self.root)).id
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
# ① 删：什么都没挂的删得掉，挂着东西的拒绝
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


def test_a_node_an_edge_points_at_is_refused_with_the_count(world: World) -> None:
    """有关系引着它 → 拒绝，并把挡路的条数数出来。

    **拒绝而不是连带删除**：`edge.src|dst` → `node` 是 CASCADE，删得掉而且一声不吭。
    引擎记住的东西是这个产品唯一的资产，不能在作者按一颗按钮的时候悄悄蒸发。
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

    with pytest.raises(NodeInUse) as caught:
        world.graph.delete_node(world.pid, world.place)

    assert caught.value.usage.edges == 1
    assert "北荒" in str(caught.value)
    # 反证：那条边还在（拒绝是拒绝，不是「删了一半」）。
    assert world.graph.state_at(world.pid, world.hero, 1).location is not None


def test_an_event_roster_also_blocks_the_delete(world: World) -> None:
    """情节名单里有他 → 同样拒绝。**在场和知情算同一条情节，不相加。**

    相加会报出一个比真实条数大的数，而那个数会被原样念给作者听
    （同 `chapter_usage` 里 `edges` 那条 `OR` 的理由）。
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
    SqliteEventStore(world.conn).put_provisional(
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
    with pytest.raises(NodeInUse):
        world.graph.delete_node(world.pid, world.hero)


def test_summary_mentions_never_block_the_delete(world: World) -> None:
    """倒排索引行**不算**「挡路」—— 它是派生数据，下一次 `_ensure` 重算。

    这个差集正是让删除对**自动建错的那批**真的可用的原因：「袭人」是从一句
    「寒气袭人」里建出来的，它身上只有一条别名和几行索引，一条边、一件事都没有。
    把索引算进去的话，它一被写进某段总结就再也删不掉了。
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

    # 同名但**不同 label** 不算撞：花名册里一个叫「北荒」的人和一个叫「北荒」的地点
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

    换成后端切的话，作者点开一个人只看得到当前章之前的部分，而他打开花名册
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
# ③ HTTP：两条路由 + 花名册那一列
# ══════════════════════════════════════════════════════════════════════════


def test_the_roster_carries_the_appearance_count_in_the_same_response(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """左栏那一行要显示「贾环 · 42 章」——**这个数跟花名册同一条出参回来**。

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
    assert all("props" not in row for row in rows), "花名册不许整体序列化 Node.props"


def test_the_delete_route_refuses_with_a_stale_canon_version(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """拿着一份旧花名册点删除 → 409，不是「删掉了一个他没看见的东西」。"""
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

        # 删完立刻不在花名册里了（前端那一行「删完立刻消失」量的就是这个）。
        names = {row["name"] for row in client.get(f"{base}/roster").json()}
        assert "袭人" not in names


def test_the_delete_route_hands_the_blocking_counts_to_the_author(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """挡住了要说清挡路的是什么 —— 不是一句「删不掉」。"""
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
        blocked = client.delete(
            f"{base}/nodes/{world.place}", params={"expected_canon_version": current}
        )

    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert detail["error"] == "node_in_use"
    assert detail["params"]["edges"] == 1
    assert detail["params"]["name"] == "北荒"


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
