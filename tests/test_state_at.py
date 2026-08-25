"""闭开区间 `[valid_from, valid_to)` 的边界（PLAN §8 Day 4 点名的第一组）。

> 「边界必须单测：valid_from=10, valid_to=143 → ch9 不命中、ch10 命中、ch142 命中、
>   **ch143 不命中**、ch150 不命中。这是整个时态模型最容易错的地方。」

本文件的 fixture 自己读 001_init.sql 建库，**不经过 db.py**：db.py 的 user_version 闸门
和 PRAGMA 有它自己的 test_migrate.py，图层的测试不该因为那边红了就跟着红。
代价是这里的 `PRAGMA foreign_keys=ON` 是第二份——所以下面第一条测试直接断言它开着，
不然本文件所有 REFERENCES 都只是注释，测出来的东西是假的。
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files

import pytest

from novel_harness.graph import (
    EdgeType,
    HealthValue,
    InformationScope,
    NodeLabel,
    NodeNotFound,
    StoreError,
    StoryGraph,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph

PID = "project:test"
SHA = "a" * 64


def new_db() -> sqlite3.Connection:
    sql = (files("novel_harness") / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    # 必须在 executescript 之后：PRAGMA foreign_keys 在事务里是个静默的 no-op。
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)", (PID, "测试书", "."))
    return conn


def add_node(
    conn: sqlite3.Connection,
    node_id: str,
    label: NodeLabel,
    name: str,
    props: str = "{}",
) -> str:
    conn.execute(
        "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
        (node_id, PID, label.value, name, props),
    )
    return node_id


def add_edge(
    conn: sqlite3.Connection,
    edge_id: str,
    src: str,
    dst: str,
    type_: EdgeType,
    valid_from: int,
    valid_to: int | None = None,
    *,
    scope: InformationScope = InformationScope.CANON,
    status: str = "ACTIVE",
    evidence_id: str | None = None,
    evidence_status: str = "NONE",
    props: str = "{}",
) -> str:
    """直接写库。**测试是唯一有资格这么做的地方**：`valid_to_chapter` / `status` 在
    生产路径上只有 upsert_edge 的 supersede 写得了（EdgeSpec 里根本没这两个字段），
    而这一组测试要的正是「区间已经长这样时，state_at 怎么答」。"""
    conn.execute(
        """INSERT INTO edge (id, project_id, src, dst, type, props_json, valid_from_chapter,
                             valid_to_chapter, information_scope, status, evidence_id,
                             evidence_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            edge_id, PID, src, dst, type_.value, props, valid_from, valid_to,
            scope.value, status, evidence_id, evidence_status,
        ),
    )
    return edge_id


def add_evidence(conn: sqlite3.Connection, ev_id: str = "ev1") -> str:
    """一条最小可用的证据：Chapter 节点 → chapter → chapter_snapshot → evidence。
    只有它齐了，`evidence_status='FRESH'|'STALE'` 的边才建得出来（DB 有 FK + 同生同死 CHECK）。"""
    add_node(conn, "ch143", NodeLabel.CHAPTER, "第一百四十三章")
    conn.execute(
        "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
        ("ch143", PID, 143, "chapters/143.md", SHA),
    )
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        ("snap1", "ch143", "他在青云城。", SHA),
    )
    conn.execute(
        """INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index, quote_text,
                                 quote_sha256, chapter_id, para_index_hint)
           VALUES (?,?,?,?,?,?,?,?)""",
        (ev_id, PID, "snap1", 0, "他在青云城。", SHA, "ch143", 0),
    )
    return ev_id


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = new_db()
    add_node(c, "xiao", NodeLabel.CHARACTER, "萧决")
    add_node(c, "qingyun", NodeLabel.LOCATION, "青云城")
    add_node(c, "beihuang", NodeLabel.LOCATION, "北荒")
    yield c
    c.close()


@pytest.fixture
def graph(conn: sqlite3.Connection) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


def test_foreign_keys_actually_on(conn: sqlite3.Connection) -> None:
    # 这条测的是 fixture 自己：外键没开的话，本文件后面全部 REFERENCES 都是注释，
    # 「跨项目引用会被拦住」之类的断言会以假的方式通过。
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        add_edge(conn, "e_bad", "xiao", "不存在的节点", EdgeType.LOCATED_AT, 1)


def test_store_satisfies_protocol(graph: SqliteStoryGraph) -> None:
    assert isinstance(graph, StoryGraph)


# ══════════════════════════════════════════════════════════════════════════
# §8 Day 4 点名的那五个数
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("chapter", "hit"),
    [(9, False), (10, True), (142, True), (143, False), (150, False)],
)
def test_closed_open_interval_boundary(
    conn: sqlite3.Connection, graph: SqliteStoryGraph, chapter: int, hit: bool
) -> None:
    """valid_from=10, valid_to=143 → ch9 ✗ / ch10 ✓ / ch142 ✓ / **ch143 ✗** / ch150 ✗。

    ch143 是全组唯一有价值的一个：把 `valid_to > :ch` 写成 `>=` 只会在这一章上错，
    而它错的形态是「他既在青云城又在北荒」= 两条互斥边 = R4 误报。
    """
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10, 143)
    snap = graph.state_at(PID, "xiao", chapter)
    assert (snap.location is not None) is hit
    assert len(snap.edges) == (1 if hit else 0)
    if hit:
        assert snap.location is not None
        assert snap.location.name == "青云城"


def test_open_ended_edge_holds_forever(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10, None)
    assert graph.state_at(PID, "xiao", 9).location is None
    assert graph.state_at(PID, "xiao", 10).location is not None
    assert graph.state_at(PID, "xiao", 99999).location is not None


def test_edge_holds_at_matches_state_at(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    # Edge.holds_at 是模型层对同一个区间的第二份表述。它只管区间不管 scope/status，
    # 所以只有在「其余四个条件都满足」时才该与 state_at 一致——这里就是那个前提下的对表。
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10, 143)
    edge = graph.state_at(PID, "xiao", 10).edges[0]
    for ch in (9, 10, 142, 143, 150):
        assert edge.holds_at(ch) == bool(graph.state_at(PID, "xiao", ch).edges)


# ══════════════════════════════════════════════════════════════════════════
# 另外四个条件（少一个就是一类静默错误）
# ══════════════════════════════════════════════════════════════════════════


def test_provisional_never_fires_in_canon_query(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """PROVISIONAL 永不开火（§5.4）。它对面板灰显可见，只是要显式再查一次。"""
    add_edge(
        conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10,
        scope=InformationScope.PROVISIONAL,
    )
    assert graph.state_at(PID, "xiao", 50).location is None
    grey = graph.state_at(PID, "xiao", 50, scope=InformationScope.PROVISIONAL)
    assert grey.location is not None
    assert grey.scope is InformationScope.PROVISIONAL


def test_canon_and_provisional_coexist_without_mixing(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    # 同一事实的两层行共存是必须允许的（幂等键含 scope）。两层的查询互不串味。
    add_edge(conn, "e_canon", "xiao", "qingyun", EdgeType.LOCATED_AT, 10)
    add_edge(
        conn, "e_prov", "xiao", "beihuang", EdgeType.LOCATED_AT, 10,
        scope=InformationScope.PROVISIONAL,
    )
    assert graph.state_at(PID, "xiao", 50).location is not None
    assert graph.state_at(PID, "xiao", 50).location.name == "青云城"
    grey = graph.state_at(PID, "xiao", 50, scope=InformationScope.PROVISIONAL)
    assert grey.location is not None
    assert grey.location.name == "北荒"


@pytest.mark.parametrize("scope", [InformationScope.PLANNED, InformationScope.REJECTED])
def test_unreadable_scopes_raise(graph: SqliteStoryGraph, scope: InformationScope) -> None:
    """改 7：硬约束下沉为 filter，泄漏在**物理上**不可能发生。这就是那个 filter——
    PLANNED 边没有任何读路径能把它捞进 Writer prompt。"""
    with pytest.raises(ValueError, match="不可读"):
        graph.state_at(PID, "xiao", 50, scope=scope)


def test_retracted_edge_never_held(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10, status="RETRACTED")
    assert graph.state_at(PID, "xiao", 50).location is None


def test_stale_evidence_stops_firing(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    """ADR 0006：证据没了就**立刻停火**——「依据没了还在质疑作者」是最伤的那类误报。"""
    ev = add_evidence(conn)
    add_edge(
        conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10,
        evidence_id=ev, evidence_status="STALE",
    )
    assert graph.state_at(PID, "xiao", 50).location is None
    conn.execute("UPDATE edge SET evidence_status='FRESH' WHERE id='e1'")
    assert graph.state_at(PID, "xiao", 50).location is not None


def test_author_declared_edge_without_evidence_survives_the_stale_filter(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """**这是本文件最重要的一条。**

    `evidence_status='NONE'` 是哨兵值不是「空」。若那列可空，`NULL != 'STALE'` 在 SQL
    三值逻辑里求值为 NULL 即假，state_at 会静默丢掉**每一条作者声明的无证据边**——
    而作者声明正是整个产品（ADR 0004）。表现形式是面板整片空白且没有任何报错。
    """
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10)  # evidence_id 为 NULL
    snap = graph.state_at(PID, "xiao", 50)
    assert snap.location is not None
    assert snap.edges[0].evidence_id is None


def test_state_at_returns_out_edges_only(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    # §5.5 的 SQL 是 WHERE src=:node。入边的消费者是 subgraph(hops=1)，不是这里。
    add_edge(conn, "e_in", "xiao", "qingyun", EdgeType.LOCATED_AT, 10)
    assert graph.state_at(PID, "qingyun", 50).edges == []


def test_unknown_node_raises(graph: SqliteStoryGraph) -> None:
    with pytest.raises(NodeNotFound):
        graph.state_at(PID, "查无此人", 50)


def test_cross_project_node_is_not_found(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    conn.execute("INSERT INTO project (id, name, root_path) VALUES ('project:other','别的书','.')")
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) "
        "VALUES ('other','project:other','Character','路人')"
    )
    with pytest.raises(NodeNotFound):
        graph.state_at(PID, "other", 50)


# ══════════════════════════════════════════════════════════════════════════
# 投影：location / states / is_dead
# ══════════════════════════════════════════════════════════════════════════


def test_two_locations_at_once_blows_up(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    """LOCATED_AT 的 exclusivity 保证至多一条。出现两条 = supersede 漏了。

    **不许悄悄取第一条**：悄悄取的那一下会把一个数据层的 bug 变成一条 R4 误报，
    而误报 <1 条/章 是 M3 的生死线。让它炸在这里。
    """
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10)
    add_edge(conn, "e2", "xiao", "beihuang", EdgeType.LOCATED_AT, 20)
    with pytest.raises(StoreError, match="supersede"):
        graph.state_at(PID, "xiao", 50)


def test_two_values_on_one_dim_blows_up(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    """**§5.5 点名的那个死法，走 HAS_STATE 那条路。**

    `HAS_STATE` 的 exclusivity 是 single_per_src_dst，但语义上的维度键是
    `dst.props.dim_key`，**不是 dst 的节点 id**——两个 StateDim 节点共享 dim_key 时
    supersede 认为它们是两个维度，一条都不闭合。后果直击 R3：一个 ch89 死、ch100 复活的
    人物在 ch152 仍被 `is_dead` 判定为死（`any()` 让 dead 永远压过 alive），
    于是 R3 对全书每一句「萧决道：」报死人说话。

    `LOCATED_AT` 在 state_at 里有一道 `len(located) > 1 就 raise` 的守卫专门接这类漏网，
    `states` 那条列表推导曾经一道守卫都没有。schema 的 idx_state_dim_key 是第一层
    （让脏数据进不来），这道是第二层（接它建立之前的行和绕开它的写入路径）。
    """
    # 绕开 idx_state_dim_key 直接造脏数据：这一层守卫要接的正是「索引拦不到的那些行」。
    conn.execute("DROP INDEX idx_state_dim_key")
    add_node(conn, "health", NodeLabel.STATE_DIM, "健康", '{"dim_key": "health"}')
    add_node(conn, "shengsi", NodeLabel.STATE_DIM, "生死", '{"dim_key": "health"}')
    add_edge(conn, "e_dead", "xiao", "health", EdgeType.HAS_STATE, 89,
             props='{"value": "死", "value_key": "dead"}')
    add_edge(conn, "e_alive", "xiao", "shengsi", EdgeType.HAS_STATE, 100,
             props='{"value": "活", "value_key": "alive"}')

    with pytest.raises(StoreError, match="supersede"):
        graph.state_at(PID, "xiao", 152)


def test_two_edges_on_one_keyless_dim_blows_up(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """没有 dim_key 的维度按 dst 节点 id 分组：同一个节点上两条边同样是 supersede 漏了。"""
    add_node(conn, "mood", NodeLabel.STATE_DIM, "心境")  # 无 dim_key
    add_edge(conn, "e1", "xiao", "mood", EdgeType.HAS_STATE, 10, props='{"value": "平静"}')
    add_edge(conn, "e2", "xiao", "mood", EdgeType.HAS_STATE, 20, props='{"value": "暴怒"}')

    with pytest.raises(StoreError, match="supersede"):
        graph.state_at(PID, "xiao", 50)


def test_distinct_dims_coexist(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    """反面：不同维度必须能同时有效，**无键的维度也要能有多个**。

    这道守卫改错的方向是「把正常人物卡也炸了」——那比它要防的 bug 更响。
    """
    add_node(conn, "health", NodeLabel.STATE_DIM, "健康", '{"dim_key": "health"}')
    add_node(conn, "cult", NodeLabel.STATE_DIM, "修为", '{"dim_key": "cultivation"}')
    add_node(conn, "mood", NodeLabel.STATE_DIM, "心境")
    add_node(conn, "luck", NodeLabel.STATE_DIM, "气运")
    add_edge(conn, "e1", "xiao", "health", EdgeType.HAS_STATE, 1,
             props='{"value": "活着", "value_key": "alive"}')
    add_edge(conn, "e2", "xiao", "cult", EdgeType.HAS_STATE, 60, props='{"value": "金丹"}')
    add_edge(conn, "e3", "xiao", "mood", EdgeType.HAS_STATE, 10, props='{"value": "平静"}')
    add_edge(conn, "e4", "xiao", "luck", EdgeType.HAS_STATE, 10, props='{"value": "大凶"}')

    snap = graph.state_at(PID, "xiao", 100)

    assert {s.dim.name: s.value for s in snap.states} == {
        "健康": "活着", "修为": "金丹", "心境": "平静", "气运": "大凶"
    }
    assert snap.is_dead is False


# ══════════════════════════════════════════════════════════════════════════
# 章号：写侧硬、读侧曾经静默
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("chapter", [0, -1, -999])
def test_read_path_rejects_chapters_below_one(graph: SqliteStoryGraph, chapter: int) -> None:
    """写侧这条不变量是硬的（`EdgeSpec.valid_from_chapter` 是 `Field(ge=1)`、chapter 表是
    `CHECK(number >= 1)`），读侧曾经 0 / 负数照单全收，**静默返回一个语义上不可能存在的
    答案**。

    `state_at(pid, 萧决, 0)` 返回一份**空**快照——而闭世界下空快照是一个**断言**
    （`is_dead` 的 docstring：「没有 health=dead 的边 ⇒ 活着」），于是人物卡理直气壮地
    告诉作者「他在第 0 章还活着、不在任何地方」，而不是承认这个问题问错了。

    触发形态是任何一次 off-by-one（0-based 场景索引、「上一章」在第 1 章时算成 0——
    §5.2 的 F 分区就是 `WHERE chapter = N-1`）。它把调用方的一个 off-by-one 放大成了
    头牌面板上一个看起来完全正常的错误答案。
    """
    for call in (
        lambda: graph.state_at(PID, "xiao", chapter),
        lambda: graph.subgraph(PID, "xiao", chapter),
    ):
        with pytest.raises(ValueError, match="章号从 1 起"):
            call()


def test_read_path_accepts_chapters_beyond_the_book(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """**上界不管是对的**：超过全书章数返回「最新状态」是闭开区间的正确语义，不是 bug。"""
    add_edge(conn, "e1", "xiao", "qingyun", EdgeType.LOCATED_AT, 10)
    snap = graph.state_at(PID, "xiao", 10**18)
    assert snap.location is not None
    assert snap.chapter == 10**18


def test_is_dead_is_temporal(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    """死亡是一条 HAS_STATE 边不是 node 上的字段——**因为它必须是时态的**。

    存成 node.props.status='dead' 的话，萧决在第 89 章死了会让 R3 在第 50 章也报
    「死人说话」：100% 误报。
    """
    add_node(conn, "health", NodeLabel.STATE_DIM, "健康", '{"dim_key": "health"}')
    add_edge(
        conn, "e_dead", "xiao", "health", EdgeType.HAS_STATE, 89,
        props='{"value": "陨落", "value_key": "dead"}',
    )
    assert graph.state_at(PID, "xiao", 50).is_dead is False  # 闭世界：无 dead 边 ⇒ 活着
    assert graph.state_at(PID, "xiao", 89).is_dead is True
    assert graph.state_at(PID, "xiao", 152).is_dead is True

    snap = graph.state_at(PID, "xiao", 152)
    assert [s.dim_key for s in snap.states] == ["health"]
    assert snap.states[0].value == "陨落"
    assert snap.states[0].value_key == HealthValue.DEAD
    assert snap.states[0].since_chapter == 89


def test_states_projection_carries_dim_node(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    # 修为维度**故意没有 value_key**（ADR 0005 的增长规则：没有规则消费就不该有键）。
    add_node(conn, "cult", NodeLabel.STATE_DIM, "修为", '{"dim_key": "cultivation"}')
    add_edge(conn, "e1", "xiao", "cult", EdgeType.HAS_STATE, 60, props='{"value": "金丹"}')
    snap = graph.state_at(PID, "xiao", 88)
    assert snap.states[0].dim.name == "修为"
    assert snap.states[0].value == "金丹"
    assert snap.states[0].value_key is None
    assert snap.is_dead is False


def test_has_appeared(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    add_node(conn, "you", NodeLabel.LOCATION, "幽泉窟", '{"first_appears_chapter": 200}')
    assert graph.state_at(PID, "you", 151).has_appeared() is False
    assert graph.state_at(PID, "you", 200).has_appeared() is True
    assert graph.state_at(PID, "xiao", 1).has_appeared() is True  # None = 一开始就在
