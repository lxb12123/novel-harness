"""supersede（PLAN §8 Day 4 点名的第二组）。

> 「`LOCATED_AT`(single_per_src) 写入新边自动闭合旧边；**`state_at` 绝不返回两条互斥边**」

第二句是本文件的全部理由。§5.5 说得很直白：state_at 同时返回「在青云城」和「在北荒」
→ 规则误报 → M3 的「误报 <1 条/章」生死线崩。所以这里有一条 `test_never_two_*`
把整本书逐章扫一遍——不是抽查某一章。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from novel_harness.graph import (
    EdgeProps,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeNotFound,
    SupersedeConflict,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from test_state_at import PID, add_node, new_db

CANON = InformationScope.CANON


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = new_db()
    add_node(c, "xiao", NodeLabel.CHARACTER, "萧决")
    add_node(c, "gu", NodeLabel.CHARACTER, "顾清音")
    add_node(c, "li", NodeLabel.CHARACTER, "李管家")
    add_node(c, "qingyun", NodeLabel.LOCATION, "青云城")
    add_node(c, "beihuang", NodeLabel.LOCATION, "北荒")
    add_node(c, "youquan", NodeLabel.LOCATION, "幽泉窟")
    add_node(c, "health", NodeLabel.STATE_DIM, "健康", '{"dim_key": "health"}')
    add_node(c, "cult", NodeLabel.STATE_DIM, "修为", '{"dim_key": "cultivation"}')
    add_node(c, "sword", NodeLabel.OBJECT, "玄铁令")
    add_node(c, "token", NodeLabel.OBJECT, "青云令")
    yield c
    c.close()


@pytest.fixture
def graph(conn: sqlite3.Connection) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


def spec(
    src: str,
    dst: str,
    type_: EdgeType,
    valid_from: int,
    *,
    scope: InformationScope = CANON,
    props: EdgeProps | None = None,
    confidence: float = 1.0,
) -> EdgeSpec:
    return EdgeSpec(
        project_id=PID,
        src=src,
        dst=dst,
        type=type_,
        valid_from_chapter=valid_from,
        information_scope=scope,
        props=props or EdgeProps(),
        confidence=confidence,
    )


def located_at(graph: SqliteStoryGraph, node: str, chapter: int) -> str | None:
    loc = graph.state_at(PID, node, chapter).location
    return loc.name if loc else None


# ══════════════════════════════════════════════════════════════════════════
# single_per_src：LOCATED_AT
# ══════════════════════════════════════════════════════════════════════════


def test_new_edge_closes_old_one(graph: SqliteStoryGraph) -> None:
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10))
    res = graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151))

    assert res.created is True
    assert [e.valid_to_chapter for e in res.closed] == [151]
    assert res.retracted == []
    # 闭开区间：旧边活到 150 为止，新边从 151 起。交接点上不许有重叠。
    assert located_at(graph, "xiao", 150) == "青云城"
    assert located_at(graph, "xiao", 151) == "北荒"


def test_never_two_mutually_exclusive_edges(graph: SqliteStoryGraph) -> None:
    """**这条断言崩了，M3 的「误报 <1 条/章」生死线就崩了。**

    逐章扫 1..200，而不是抽查：supersede 写错的典型形态是只在某一个交接章上重叠一格。
    """
    for ch, loc in [(10, "qingyun"), (77, "beihuang"), (78, "youquan"), (151, "qingyun")]:
        graph.upsert_edge(spec("xiao", loc, EdgeType.LOCATED_AT, ch))

    for ch in range(1, 201):
        edges = [
            e for e in graph.state_at(PID, "xiao", ch).edges if e.type is EdgeType.LOCATED_AT
        ]
        assert len(edges) <= 1, f"第 {ch} 章同时有 {len(edges)} 条 LOCATED_AT：{edges}"
        # state_at 自己也会在这里炸（location 至多一个是 exclusivity 保证的），
        # 但断言写在这里是为了让失败信息直接指向那一章。
        graph.state_at(PID, "xiao", ch)

    assert located_at(graph, "xiao", 9) is None
    assert located_at(graph, "xiao", 10) == "青云城"
    assert located_at(graph, "xiao", 76) == "青云城"
    assert located_at(graph, "xiao", 77) == "北荒"  # 只活了一章
    assert located_at(graph, "xiao", 78) == "幽泉窟"
    assert located_at(graph, "xiao", 150) == "幽泉窟"
    assert located_at(graph, "xiao", 151) == "青云城"


def test_same_chapter_correction_retracts(graph: SqliteStoryGraph) -> None:
    """同章更正（「他在青云城…… 然后他去了北荒」都在第 151 章）。

    闭合成 `[151,151)` 会被 DB 的 CHECK 拒（空区间 = 这条事实从未成立），所以唯一
    正确的表达是撤回。这就是 EdgeStatus 保留 RETRACTED 的全部理由。
    """
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))
    res = graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151))

    assert res.closed == []
    assert [e.status for e in res.retracted] == [EdgeStatus.RETRACTED]
    assert located_at(graph, "xiao", 151) == "北荒"
    assert located_at(graph, "xiao", 999) == "北荒"


def test_out_of_order_raises_instead_of_guessing(graph: SqliteStoryGraph) -> None:
    """v1 的 supersede **只进不退**。往中间插一条更早的事实要先回答「先前那条事实在
    后一条结束后要不要恢复」——v1 没有消费者，而猜错的产物是重叠区间。"""
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))
    with pytest.raises(SupersedeConflict, match="乱序"):
        graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 10))


def test_out_of_order_leaves_no_trace(conn: sqlite3.Connection, graph: SqliteStoryGraph) -> None:
    # 抛异常前不许半途改库：一条已插入但没跑完 supersede 的边就是重叠区间本身。
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))
    with pytest.raises(SupersedeConflict):
        graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 10))
    assert conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0] == 1
    assert located_at(graph, "xiao", 151) == "青云城"


def test_closed_edge_is_not_reclosed(graph: SqliteStoryGraph) -> None:
    """已经闭合的 `[10,143)` 与新边 `[151,∞)` **不重叠**，它是「后来他又走了」的正常历史。

    契约只说「同 (src,type) 的其它 ACTIVE 边」，没说要看区间是否还开着。少了那个条件，
    这里的第三次 upsert 会把 `[10,143)` 重写成 `[10,151)`，凭空把萧决在 143–150 章
    塞回青云城——正是这套机制要防的错位事实。
    """
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10))
    graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 143))
    # 第 151 章他回青云城；[10,143) 那条与它无关，不该被再动一次。
    res = graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))

    assert [e.valid_from_chapter for e in res.closed] == [143]
    assert located_at(graph, "xiao", 142) == "青云城"
    assert located_at(graph, "xiao", 143) == "北荒"
    assert located_at(graph, "xiao", 150) == "北荒"
    assert located_at(graph, "xiao", 151) == "青云城"


# ══════════════════════════════════════════════════════════════════════════
# single_per_src_dst / multi
# ══════════════════════════════════════════════════════════════════════════


def test_has_state_closes_same_dim_only(graph: SqliteStoryGraph) -> None:
    """(人, 维度) 单值。突破金丹→元婴闭合的是「修为」那条，**不许碰「健康」那条**——
    single_per_src 和 single_per_src_dst 写混的形态就是这里。"""
    alive = EdgeProps(value="活着", value_key="alive")
    graph.upsert_edge(spec("xiao", "health", EdgeType.HAS_STATE, 1, props=alive))
    graph.upsert_edge(spec("xiao", "cult", EdgeType.HAS_STATE, 60, props=EdgeProps(value="金丹")))
    res = graph.upsert_edge(
        spec("xiao", "cult", EdgeType.HAS_STATE, 120, props=EdgeProps(value="元婴"))
    )

    assert [e.dst for e in res.closed] == ["cult"]
    snap = graph.state_at(PID, "xiao", 130)
    assert {s.dim.name: s.value for s in snap.states} == {"健康": "活着", "修为": "元婴"}
    assert snap.is_dead is False


def test_related_to_closes_same_pair_only(graph: SqliteStoryGraph) -> None:
    graph.upsert_edge(spec("xiao", "gu", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))
    graph.upsert_edge(spec("xiao", "li", EdgeType.RELATED_TO, 10, props=EdgeProps(value="主仆")))
    res = graph.upsert_edge(
        spec("xiao", "gu", EdgeType.RELATED_TO, 143, props=EdgeProps(value="断绝师门"))
    )

    # RELATED_TO 无向（ADR 0008）：(src,dst) 在 EdgeSpec 构造时就规范化成 (min,max)，
    # 所以对端要用 peer_of 取——这里 "gu" < "xiao"，那条边是 (gu, xiao)，`e.dst` 是 "xiao"。
    assert [e.peer_of("xiao") for e in res.closed] == ["gu"]
    edges = {e.peer_of("xiao"): e.props.value for e in graph.state_at(PID, "xiao", 150).edges}
    assert edges == {"gu": "断绝师门", "li": "主仆"}


# ══════════════════════════════════════════════════════════════════════════
# RELATED_TO 无向（ADR 0008）
# ══════════════════════════════════════════════════════════════════════════


def test_symmetric_relation_declared_from_both_sides_is_one_edge(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """**这条打的是 PLAN §3.1 自己的 GIF 主角。**

    对称关系（师兄妹 / 夫妻 / 仇敌）作者会自然地两侧各声明一次，而图层没有任何东西
    阻止或警告这件事。RELATED_TO 曾经是有向的，于是第 143 章「断绝师门」只落 c1→c2
    一条边时，supersede 只闭合 c1→c2 的旧边，c2→c1 的「师兄妹」[10,∞) 原封不动地继续
    有效——**同一个关系两个互斥的值同时是 CANON**，而 §5.4 的定义是 CANON「可断言为真、
    可开火」。

    它比别的 bug 更难堵，因为**它不需要任何脏数据**：全程走 upsert_edge 的正常路径、
    每一步都成功、UpsertResult 也如实报告了它闭合了什么，没有任何一个环节有机会察觉不对。
    讽刺的是它恰好打在 §3.1 的 GIF 上（作者写「师兄」→ 右侧红字「第 143 章两人已断绝
    师门关系」）：从顾清音那一侧查，图会说他俩还是师兄妹，红字不会亮。

    修法是无向 + `(min,max)` 规范化（ADR 0008），所以反向声明**撞的是幂等键**，
    连第二行都建不出来。
    """
    first = graph.upsert_edge(spec("xiao", "gu", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))
    mirror = graph.upsert_edge(spec("gu", "xiao", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))

    assert mirror.created is False, "反向声明的是同一个事实，不是第二条边"
    assert mirror.edge.id == first.edge.id
    assert conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0] == 1


def test_both_sides_agree_after_the_relation_changes(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """第 143 章断绝师门之后，**两侧查出来必须是同一个答案**。"""
    graph.upsert_edge(spec("xiao", "gu", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))
    graph.upsert_edge(spec("gu", "xiao", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))
    res = graph.upsert_edge(spec("xiao", "gu", EdgeType.RELATED_TO, 143, props=EdgeProps(value="断绝")))

    assert [e.props.value for e in res.closed] == ["师兄妹"]
    for ch, want in [(142, "师兄妹"), (143, "断绝"), (150, "断绝")]:
        xiao_side = [e.props.value for e in graph.state_at(PID, "xiao", ch).edges]
        gu_side = [e.props.value for e in graph.state_at(PID, "gu", ch).edges]
        assert xiao_side == gu_side == [want], f"第 {ch} 章两侧不一致：{xiao_side} vs {gu_side}"
    # 全书逐章：两侧永远不许各执一词，也永远不许同时有两条。
    for ch in range(1, 201):
        assert len(graph.state_at(PID, "gu", ch).edges) <= 1


def test_undirected_edge_is_visible_from_both_ends(graph: SqliteStoryGraph) -> None:
    """存储方向是 `(min,max)` 抛硬币的结果，所以**只查 src 会让一半的人物卡关系栏消失**。

    这条钉的是 `out_edges_at` 对无向类型放开 dst 的那一半——它和上面的规范化必须成对，
    只做规范化的话，「顾清音和萧决是什么关系」的答案会取决于两个 ULID 的字典序。
    """
    graph.upsert_edge(spec("xiao", "gu", EdgeType.RELATED_TO, 10, props=EdgeProps(value="师兄妹")))

    for node, peer in [("xiao", "gu"), ("gu", "xiao")]:
        edges = graph.state_at(PID, node, 50).edges
        assert [e.props.value for e in edges] == ["师兄妹"], f"{node} 侧看不见这条关系"
        assert edges[0].peer_of(node) == peer


def test_directed_types_are_not_read_from_both_ends(
    conn: sqlite3.Connection, graph: SqliteStoryGraph
) -> None:
    """反面，也是这条改动最容易改坏的方向：**只对无向类型放开 dst**。

    KNOWS 的 src 是人、dst 是秘密，反向查是无意义的；LOCATED_AT 反向查会让「青云城」
    这个节点的状态快照里冒出所有到过它的人。§5.5 的 `WHERE src = :node` 对它们仍然成立。
    """
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10))
    assert graph.state_at(PID, "qingyun", 50).edges == []


def test_undirected_normalization_happens_in_the_type_layer() -> None:
    """规范化在 `EdgeSpec` 的构造函数里，不是在 upsert_edge 里调一次。

    调一次 = 一条纪律，而漏掉它的产物是 ADR 0008 那条 bug。顺带钉住 pydantic 的一个坑：
    after-validator **返回 `model_copy` 会被静默丢弃**（只发一条 UserWarning），
    那正好是这条规范化失效的形态——所以它用 `object.__setattr__`。
    """
    forward = spec("xiao", "gu", EdgeType.RELATED_TO, 10)
    backward = spec("gu", "xiao", EdgeType.RELATED_TO, 10)

    assert (forward.src, forward.dst) == (backward.src, backward.dst) == ("gu", "xiao")
    # 有向类型一个字都不许动。
    located = spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10)
    assert (located.src, located.dst) == ("xiao", "qingyun")


def test_multi_never_closes(graph: SqliteStoryGraph) -> None:
    """OWNS / MEMBER_OF / PLANTED_IN / RESOLVED_IN：可以多条同时有效。
    一个人当然可以同时拥有玄铁令和青云令。"""
    graph.upsert_edge(spec("xiao", "sword", EdgeType.OWNS, 10))
    res = graph.upsert_edge(spec("xiao", "token", EdgeType.OWNS, 20))

    assert res.closed == []
    assert res.retracted == []
    assert {e.dst for e in graph.state_at(PID, "xiao", 50).edges} == {"sword", "token"}


# ══════════════════════════════════════════════════════════════════════════
# 幂等键（M4 的后台批跑 + 断点续跑靠它）
# ══════════════════════════════════════════════════════════════════════════


def test_rerun_updates_props_only_and_never_resurrects(graph: SqliteStoryGraph) -> None:
    """撞幂等键 = 重跑。**不跑 supersede、不碰 valid_to、不碰 status。**

    这三个「不」是生死线：若重跑把已闭合旧边的 valid_to 重置成 NULL，它会复活成
    `[10,∞)` 与 `[151,∞)` 重叠 → state_at 返两条互斥边 → 正是 §5.5 点名的死法。
    """
    first = graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10))
    graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151))

    # M4 的抽取器断点续跑，把第 10 章那条又抽了一遍（这次带上了置信度和 props）。
    again = graph.upsert_edge(
        spec(
            "xiao", "qingyun", EdgeType.LOCATED_AT, 10,
            props=EdgeProps(value="城主府"), confidence=0.8,
        )
    )

    assert again.created is False
    assert again.edge.id == first.edge.id
    assert again.closed == [] and again.retracted == []
    assert again.edge.props.value == "城主府"  # props 类字段更新了
    assert again.edge.confidence == 0.8
    assert again.edge.valid_to_chapter == 151  # **没有复活**
    assert located_at(graph, "xiao", 151) == "北荒"
    for ch in range(1, 201):
        graph.state_at(PID, "xiao", ch)  # 逐章确认没有第二条互斥边


def test_rerun_does_not_revive_retracted_edge(graph: SqliteStoryGraph) -> None:
    """已知缺口，**故意的**：「把一条 RETRACTED 的事实重新声明回来」在 v1 不生效——
    它命中幂等键、被当成重跑、静默无事。要它时加第 6 个方法 `revive`，别改 conflict
    分支拿生死线换边缘 UX。这条测试是那个缺口的书面存档。"""
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))
    graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151))
    res = graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 151))

    assert res.created is False
    assert res.edge.status is EdgeStatus.RETRACTED
    assert located_at(graph, "xiao", 151) == "北荒"


# ══════════════════════════════════════════════════════════════════════════
# 分层隔离（原则 5）
# ══════════════════════════════════════════════════════════════════════════


def test_provisional_never_supersedes_canon(graph: SqliteStoryGraph) -> None:
    """**这是原则 5「Agent 不得直接修改正式 Canon」的物理实现。**

    跨层 supersede 会让抽取器写的 PROVISIONAL 边闭合作者的 CANON 边，而且是静默地。
    """
    graph.upsert_edge(spec("xiao", "qingyun", EdgeType.LOCATED_AT, 10))
    res = graph.upsert_edge(
        spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151, scope=InformationScope.PROVISIONAL)
    )

    assert res.closed == []  # 作者的 CANON 边一根汗毛都没动
    assert located_at(graph, "xiao", 151) == "青云城"
    assert graph.state_at(
        PID, "xiao", 151, scope=InformationScope.PROVISIONAL
    ).location.name == "北荒"


def test_promotion_is_a_new_row_in_the_canon_layer(graph: SqliteStoryGraph) -> None:
    """scope 之间没有提升通道：PROVISIONAL → CANON 就是在 CANON 层 upsert 同一事实
    （幂等键含 scope ⇒ 新行）。原 PROVISIONAL 行留在自己层里，不影响 state_at。"""
    prov = graph.upsert_edge(
        spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151, scope=InformationScope.PROVISIONAL)
    )
    canon = graph.upsert_edge(spec("xiao", "beihuang", EdgeType.LOCATED_AT, 151))

    assert canon.created is True
    assert canon.edge.id != prov.edge.id
    assert located_at(graph, "xiao", 151) == "北荒"


def test_planned_is_writable(graph: SqliteStoryGraph) -> None:
    """PLANNED **可写不可读**：伏笔「第 47 章埋了 X，计划第 200 章回收」就是它。
    读路径把它挡在外面（见 test_state_at 的 test_unreadable_scopes_raise）。"""
    res = graph.upsert_edge(
        spec("xiao", "youquan", EdgeType.LOCATED_AT, 200, scope=InformationScope.PLANNED)
    )
    assert res.created is True
    assert located_at(graph, "xiao", 200) is None  # CANON 层什么都没有


def test_upsert_unknown_node_raises(graph: SqliteStoryGraph) -> None:
    with pytest.raises(NodeNotFound):
        graph.upsert_edge(spec("查无此人", "qingyun", EdgeType.LOCATED_AT, 10))
    with pytest.raises(NodeNotFound):
        graph.upsert_edge(spec("xiao", "查无此地", EdgeType.LOCATED_AT, 10))
