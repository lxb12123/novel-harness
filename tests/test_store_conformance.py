"""**同一份可执行规格，跑在 FakeGraph 和真 SqliteStoryGraph 上。**

── 这个文件为什么必须存在 ────────────────────────────────────────────────

`test_knowledge.py` 的全部断言（含 README 第一行承诺的 ch87/ch88/ch152）跑在 `FakeGraph`
上，而那个 Fake **自己手写了一遍** `knowledge_matrix` 的闭世界推导和那五个时态条件。
`test_checks.py` 的 R2/R3 同理。于是「232 个测试全绿」这句话在生产查询路径上是虚的：
`queries.knowledge_edges_at` 的 SQL、`KNOWS 压 BELIEVES`、`NodeLabel.SECRET` 校验、
重复边的 StoreError、`secret_ids` 的默认列序——一条都没被执行过。唯一碰到真
`knowledge_matrix` 的测试只断言了 `chapter<1` 抛 ValueError，**够不到 SQL**。

Fake 本身没有错：它快、它是 Protocol 的参考实现、它让 panel/checks 的测试不依赖建库。
错的是**没有任何东西把它钉在生产实现上**。一份被两处独立手写的规格，早晚会在其中一处
悄悄漂移——而漂移的形态恰好是本项目最怕的那个：`valid_to > :ch` 写成 `>=`，Fake 绿着，
真库在第 143 章同时返回「知道」和「不知道」。

**所以这里的每一条断言都跑两遍：一遍打 Fake，一遍打真库。** Fake 从此不能单方面漂移——
它漂了，`[real]` 那一半是绿的、`[fake]` 那一半红，指向明确。

── 选型：参数化，不是「再抄一遍断言」 ────────────────────────────────────

任务允许退而求其次写一份「只打真库」的重复断言。没有这么做，理由是那样等于把同一份
规格写成第三份手抄本——而这个文件要解决的问题正是「规格有两份手抄本」。这里的做法是
把场景降解成纯数据（`World`），再让 `build_*_store` fixture 把同一个 `World` 分别铺进
Fake 和真 SQLite。**断言只有一份，后端有两个。**

Fake 兜不住的三条（label 校验 / 重复边 StoreError / secret_ids 默认列序）在文件末尾单列，
标着 `real only` 并写明 Fake 为什么够不着——它们是这次审计点名的「生产路径未覆盖」的余数。

── 真库怎么建 ────────────────────────────────────────────────────────────

走 `db.connect` + `db.migrate`（不是 `test_state_at.py` 那份手搓 executescript）：这个文件
要证的是**装配起来的生产路径**是对的，那就该连 PRAGMA 和 user_version 闸门一起跑。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

import pytest
from test_checks import FakeGraph as RulesFakeGraph
from test_knowledge import FakeGraph as KnowledgeFakeGraph

from novel_harness import importer, project
from novel_harness.checks import CheckContext
from novel_harness.checks.dead_speaks import check
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.graph import (
    AliasKind,
    ChapterSpec,
    Edge,
    EdgeProps,
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    HealthValue,
    InformationScope,
    KnowledgeState,
    Node,
    NodeLabel,
    NodeNotFound,
    NodeProps,
    StoreError,
    StoryGraph,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.graph.store import ChapterWriteConflict
from novel_harness.text.chapterize import chapterize
from novel_harness.panel import knowledge_matrix

PID = "project:conf:01J0"
SHA = "c" * 64
EV = "evidence:conf:01J8"
"""World 里的边挂证据时用的那一条。真库侧会按需把它连同快照链一起建出来
（DB 的 `(evidence_id IS NULL) = (evidence_status = 'NONE')` 是硬 CHECK，Fake 没有这条，
所以 World 必须两边都显式给值，否则两个后端拿到的 `cell.evidence_id` 会不一样）。"""


# ══════════════════════════════════════════════════════════════════════════
# 场景 = 纯数据。两个后端铺同一份。
# ══════════════════════════════════════════════════════════════════════════


def _node(node_id: str, label: NodeLabel, name: str, **props: object) -> Node:
    return Node(id=node_id, project_id=PID, label=label, name=name, props=NodeProps(**props))


# 秘密的 id **按声明顺序升序**：ULID 的创建顺序就是作者的声明顺序（ADR 0003），而真库的
# secret_ids 按 id 排。列在这里升序，两个后端的默认列序才可能对上——对不上就是 bug，
# 见 test_secret_ids_default_order_is_the_declaration_order。
XIAO_JUE = _node("character:conf:01J1", NodeLabel.CHARACTER, "萧决")
GU_QINGYIN = _node("character:conf:01J2", NodeLabel.CHARACTER, "顾清音")
LI_GUANJIA = _node("character:conf:01J3", NodeLabel.CHARACTER, "李管家")
BLOODLINE = _node("secret:conf:01J4", NodeLabel.SECRET, "血脉秘密")
XUANTIE = _node("secret:conf:01J5", NodeLabel.SECRET, "玄铁令下落")
QINGYUN = _node("location:conf:01J6", NodeLabel.LOCATION, "青云城主府")
BEIHUANG = _node("location:conf:01J7", NodeLabel.LOCATION, "北荒")

CAST = (XIAO_JUE, GU_QINGYIN, LI_GUANJIA, BLOODLINE, XUANTIE, QINGYUN, BEIHUANG)


@dataclass(frozen=True)
class World:
    """一个场景的全部输入。**两个后端各自把它铺成一个 StoryGraph。**"""

    nodes: tuple[Node, ...] = CAST
    edges: tuple[Edge, ...] = ()
    extra_aliases: Mapping[str, tuple[Node, ...]] = field(default_factory=dict)
    """本名之外的称呼。一个 surface 指向多个节点 = 歧义（「师兄」）。"""


def _edge(
    src: Node,
    dst: Node,
    edge_type: EdgeType,
    valid_from: int,
    *,
    valid_to: int | None = None,
    scope: InformationScope = InformationScope.CANON,
    status: EdgeStatus = EdgeStatus.ACTIVE,
    evidence_id: str | None = None,
    evidence_status: EvidenceStatus = EvidenceStatus.NONE,
    **props: object,
) -> Edge:
    return Edge(
        id=f"edge:conf:{src.id}-{dst.id}-{edge_type.value}-{valid_from}-{scope.value}",
        project_id=PID,
        src=src.id,
        dst=dst.id,
        type=edge_type,
        props=EdgeProps(**props),
        valid_from_chapter=valid_from,
        valid_to_chapter=valid_to,
        information_scope=scope,
        status=status,
        evidence_id=evidence_id,
        evidence_status=evidence_status,
    )


def _alias_index(world: World) -> dict[str, list[Node]]:
    """真库 `alias` 表里该有的那些行，也是两个 Fake 各自的别名字典。

    每个节点一条 canonical（surface == node.name，见 001_init 的 idx_alias_canonical），
    外加 World 声明的额外称呼。
    """
    index: dict[str, list[Node]] = {n.name: [n] for n in world.nodes}
    for surface, targets in world.extra_aliases.items():
        index.setdefault(surface, []).extend(targets)
    return index


# ══════════════════════════════════════════════════════════════════════════
# 后端二：真库
# ══════════════════════════════════════════════════════════════════════════


def _fresh_db() -> Connection:
    conn = connect(IN_MEMORY)
    migrate(conn)
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (PID, "一致性测试书", ".")
    )
    return conn


def _ensure_evidence(conn: Connection, ev_id: str) -> None:
    """最小可用证据链：Chapter 节点 → chapter → chapter_snapshot → evidence。

    只有它齐了，`evidence_status='FRESH'|'STALE'` 的边才建得出来——那两个值在 DB 上与
    evidence_id 同生同死。Fake 不需要这一整条链，**这正是两个后端唯一不对称的地方**，
    所以它被关在这个函数里，World 那一层看不见。
    """
    if conn.execute("SELECT 1 FROM evidence WHERE id = ?", (ev_id,)).fetchone():
        return
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES (?,?,?,?)",
        ("chapter:conf:0143", PID, NodeLabel.CHAPTER.value, "第一百四十三章"),
    )
    conn.execute(
        "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
        ("chapter:conf:0143", PID, 143, "chapters/143.md", SHA),
    )
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        ("snap:conf:0143", "chapter:conf:0143", "他知道了那个秘密。", SHA),
    )
    conn.execute(
        """INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index, quote_text,
                                 quote_sha256, chapter_id, para_index_hint)
           VALUES (?,?,?,?,?,?,?,?)""",
        (ev_id, PID, "snap:conf:0143", 0, "他知道了那个秘密。", SHA, "chapter:conf:0143", 0),
    )


def _populate(conn: Connection, world: World) -> Connection:
    """把 World 铺进真库。

    直接写 `edge` 表而不是走 `upsert_edge`：`valid_to_chapter` / `status` 在生产路径上只有
    supersede 写得了（EdgeSpec 里根本没这两个字段），而这一组测试要问的正是「区间/状态
    已经长这样时，查询怎么答」。同 test_state_at.py 的 add_edge。
    """
    for n in world.nodes:
        conn.execute(
            "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
            (n.id, n.project_id, n.label.value, n.name, n.props.model_dump_json()),
        )
        if n.label is NodeLabel.SECRET:
            # 扩展表：主键就是 node.id。secret_ids（secrets=None 的那条路）只读这张表。
            conn.execute("INSERT INTO secret (id, project_id) VALUES (?,?)", (n.id, n.project_id))

    for surface, targets in _alias_index(world).items():
        for t in targets:
            kind = AliasKind.CANONICAL if surface == t.name else AliasKind.ALIAS
            conn.execute(
                """INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules)
                   VALUES (?,?,?,?,?,?)""",
                (f"alias:{t.id}:{surface}", PID, t.id, surface, kind.value, int(len(surface) >= 2)),
            )

    for e in world.edges:
        if e.evidence_id is not None:
            _ensure_evidence(conn, e.evidence_id)
        conn.execute(
            """INSERT INTO edge (id, project_id, src, dst, type, props_json, valid_from_chapter,
                                 valid_to_chapter, information_scope, status, evidence_id,
                                 evidence_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                e.id, e.project_id, e.src, e.dst, e.type.value, e.props.model_dump_json(),
                e.valid_from_chapter, e.valid_to_chapter, e.information_scope.value,
                e.status.value, e.evidence_id, e.evidence_status.value,
            ),
        )
    return conn


Build = Callable[[World], StoryGraph]


@pytest.fixture
def real_store() -> Iterator[Callable[[World], SqliteStoryGraph]]:
    conns: list[Connection] = []

    def _build(world: World) -> SqliteStoryGraph:
        conn = _populate(_fresh_db(), world)
        conns.append(conn)
        return SqliteStoryGraph(conn)

    yield _build
    for c in conns:
        c.close()


@pytest.fixture(params=["fake", "real"])
def matrix_store(request: pytest.FixtureRequest, real_store: Build) -> Build:
    """认知矩阵那条路的两个后端。**下面每条断言都跑两遍。**"""
    if request.param == "real":
        return real_store
    return lambda world: KnowledgeFakeGraph(
        list(world.nodes), list(world.edges), {s: list(t) for s, t in world.extra_aliases.items()}
    )


@pytest.fixture(params=["fake", "real"])
def rules_store(request: pytest.FixtureRequest, real_store: Build) -> Build:
    """规则那条路的两个后端（它要的是 resolve + state_at，不是矩阵）。"""
    if request.param == "real":
        return real_store
    return lambda world: RulesFakeGraph(_alias_index(world), list(world.edges))


def test_both_backends_satisfy_the_protocol(matrix_store: Build, rules_store: Build) -> None:
    # 参数化本身要是配错了（比如 real 那一半悄悄退化成 fake），下面全部断言会以假的方式
    # 通过。这条钉住「两个后端确实是两个东西」。
    assert isinstance(matrix_store(World()), StoryGraph)
    assert isinstance(rules_store(World()), StoryGraph)


# ══════════════════════════════════════════════════════════════════════════
# 1. 头牌：ch87 UNKNOWN / ch88 KNOWS / ch152 KNOWS（README 第一行的那句承诺）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("chapter", "expected"),
    [
        (87, KnowledgeState.UNKNOWN),
        (88, KnowledgeState.KNOWS),
        (152, KnowledgeState.KNOWS),
    ],
)
def test_knows_since_chapter_88(
    matrix_store: Build, chapter: int, expected: KnowledgeState
) -> None:
    """§8 Day 5 点名的那条。**这一条此前只在 Fake 上绿过。**"""
    store = matrix_store(World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),)))

    cell = knowledge_matrix(store, PID, chapter, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
        XIAO_JUE.id, BLOODLINE.id
    )

    assert cell.state is expected
    # ch87 那格不许带 since_chapter，否则面板渲染出「✗ 不知道 (ch88)」这种自相矛盾的东西。
    assert cell.since_chapter == (88 if expected is KnowledgeState.KNOWS else None)


def test_believes_carries_believed_value(matrix_store: Build) -> None:
    store = matrix_store(
        World(
            edges=(
                _edge(
                    LI_GUANJIA, BLOODLINE, EdgeType.BELIEVES, 103, believed_value="以为已泄露"
                ),
            )
        )
    )

    cell = knowledge_matrix(store, PID, 152, [LI_GUANJIA.id], secrets=[BLOODLINE.id]).cell(
        LI_GUANJIA.id, BLOODLINE.id
    )

    assert cell.state is KnowledgeState.BELIEVES
    assert cell.believed_value == "以为已泄露"
    assert cell.since_chapter == 103


def test_evidence_id_reaches_the_cell(matrix_store: Build) -> None:
    """FRESH 的边照常开火，且它的 evidence_id 要能到面板——「✓ 知道 (ch88)」旁边那个
    可以点回原文的指针（ADR 0006 的双指针）就靠它。"""
    store = matrix_store(
        World(
            edges=(
                _edge(
                    XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88,
                    evidence_id=EV, evidence_status=EvidenceStatus.FRESH,
                ),
            )
        )
    )

    cell = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
        XIAO_JUE.id, BLOODLINE.id
    )

    assert cell.state is KnowledgeState.KNOWS
    assert cell.evidence_id == EV


# ══════════════════════════════════════════════════════════════════════════
# 2. 闭世界：无 KNOWS 边 ⇒ UNKNOWN。全库零 DOES_NOT_KNOW 边，那是设计（ADR 0005）。
# ══════════════════════════════════════════════════════════════════════════


def test_closed_world_unknown_cells_are_materialized(matrix_store: Build) -> None:
    """零 KNOWS 边 ⇒ 每一格都是 UNKNOWN，且**每一格都存在**。

    「没有这一格」和「他不知道」是两个意思。面板上少一格 = 作者以为系统没意见 = 说漏嘴。
    """
    store = matrix_store(World())

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id, GU_QINGYIN.id])

    assert len(matrix.cells) == len(matrix.characters) * len(matrix.secrets) == 4
    assert {c.state for c in matrix.cells} == {KnowledgeState.UNKNOWN}
    assert {c.since_chapter for c in matrix.cells} == {None}


def test_closed_world_needs_no_does_not_know_edge(matrix_store: Build) -> None:
    """UNKNOWN 是**推导**，不是一条边。萧决知道血脉秘密、对玄铁令一无所知——
    后者那一格没有任何边支撑它，它照样必须是一个断言。"""
    store = matrix_store(World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),)))

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id, XUANTIE.id])

    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.KNOWS
    assert matrix.cell(XIAO_JUE.id, XUANTIE.id).state is KnowledgeState.UNKNOWN


def test_cast_and_secret_order_is_the_panel_layout(matrix_store: Build) -> None:
    """行序 = 作者写的 cast 顺序，列序 = 传入的 secrets 顺序。面板逐格核对靠它对得上。"""
    cast = [LI_GUANJIA.id, XIAO_JUE.id, GU_QINGYIN.id]

    matrix = knowledge_matrix(matrix_store(World()), PID, 152, cast, secrets=[XUANTIE.id])

    assert [c.id for c in matrix.characters] == cast
    assert [s.id for s in matrix.secrets] == [XUANTIE.id]


# ══════════════════════════════════════════════════════════════════════════
# 3. KNOWS 压 BELIEVES
# ══════════════════════════════════════════════════════════════════════════


def test_knows_overrides_believes_when_both_exist(matrix_store: Build) -> None:
    """两条边都有效时，真知道了就不再是错误认知（§8 Day 5 的 CASE WHEN 顺序）。

    (人,秘密) 的 exclusivity 是 single_per_src_dst，而它是**按 type 分组**的——所以
    KNOWS 和 BELIEVES 谁也挤不掉谁，这一格靠的是投影时的优先级，不是 supersede。
    压错方向的产物是：面板告诉作者「李管家以为已泄露」，而他其实**真的知道**，
    于是这一场的 must_not_reveal 少了一条。
    """
    store = matrix_store(
        World(
            edges=(
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.BELIEVES, 103, believed_value="以为已泄露"),
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.KNOWS, 120),
            )
        )
    )

    cell = knowledge_matrix(store, PID, 152, [LI_GUANJIA.id], secrets=[BLOODLINE.id]).cell(
        LI_GUANJIA.id, BLOODLINE.id
    )

    assert cell.state is KnowledgeState.KNOWS
    assert cell.since_chapter == 120
    # believed_value 只属于 BELIEVES。KNOWS 那一格带着它 = 面板同时显示两个互斥状态。
    assert cell.believed_value is None


def test_believes_still_shows_before_he_actually_knows(matrix_store: Build) -> None:
    """反面：KNOWS 从 ch120 起才有效，ch119 那一格必须还是错误认知。
    压 BELIEVES 是**时态之后**的事——先过五条件，再谈优先级。
    """
    store = matrix_store(
        World(
            edges=(
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.BELIEVES, 103, believed_value="以为已泄露"),
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.KNOWS, 120),
            )
        )
    )

    cell = knowledge_matrix(store, PID, 119, [LI_GUANJIA.id], secrets=[BLOODLINE.id]).cell(
        LI_GUANJIA.id, BLOODLINE.id
    )

    assert cell.state is KnowledgeState.BELIEVES
    assert cell.believed_value == "以为已泄露"


# ══════════════════════════════════════════════════════════════════════════
# 4. 闭开区间 [valid_from, valid_to)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("chapter", "hit"),
    [(9, False), (10, True), (142, True), (143, False), (150, False)],
)
def test_closed_open_interval_boundary_on_knows(
    matrix_store: Build, chapter: int, hit: bool
) -> None:
    """vf=10, vt=143 → ch9 ✗ / ch10 ✓ / ch142 ✓ / **ch143 ✗** / ch150 ✗。

    ch143 是全组唯一有价值的一个：`valid_to > :ch` 写成 `>=` 只在这一章上错。
    test_state_at.py 已经在真库上把这五个数钉在 LOCATED_AT 上了——这里钉的是
    `knowledge_edges_at`，**它是另一条 SQL**，共享的只有 TEMPORAL_WHERE 那个常量。
    哪天有人在那条 SQL 里手写一遍条件（而不是拼常量），这一条会红。
    """
    store = matrix_store(
        World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 10, valid_to=143),))
    )

    cell = knowledge_matrix(store, PID, chapter, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
        XIAO_JUE.id, BLOODLINE.id
    )

    assert (cell.state is KnowledgeState.KNOWS) is hit
    assert cell.since_chapter == (10 if hit else None)


def test_open_ended_knows_holds_forever(matrix_store: Build) -> None:
    store = matrix_store(World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),)))

    def state(ch: int) -> KnowledgeState:
        return knowledge_matrix(store, PID, ch, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
            XIAO_JUE.id, BLOODLINE.id
        ).state

    assert state(87) is KnowledgeState.UNKNOWN
    assert state(88) is KnowledgeState.KNOWS
    # 上界不管是对的：超过全书章数返回「最新状态」是闭开区间的正确语义。
    assert state(99999) is KnowledgeState.KNOWS


# ══════════════════════════════════════════════════════════════════════════
# 5. 另外三个条件：PROVISIONAL / STALE / RETRACTED 一律不开火
# ══════════════════════════════════════════════════════════════════════════


def test_provisional_never_leaks_into_the_canon_matrix(matrix_store: Build) -> None:
    """PROVISIONAL「永不断言为真」：默认那次查询里它必须完全不存在，
    灰显是**第二次调用**，不是混在同一份结果里。"""
    prov = InformationScope.PROVISIONAL
    store = matrix_store(
        World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88, scope=prov),))
    )

    canon = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])
    grey = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id], scope=prov)

    assert canon.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN
    assert grey.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.KNOWS


def test_canon_and_provisional_rows_coexist_without_mixing(matrix_store: Build) -> None:
    """同一个 (人,秘密) 两层各一行是必须允许的（幂等键含 information_scope）。
    两层的查询互不串味——串了就是抽取器的猜测污染了 Canon。"""
    prov = InformationScope.PROVISIONAL
    store = matrix_store(
        World(
            edges=(
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.BELIEVES, 103, believed_value="以为已泄露"),
                _edge(LI_GUANJIA, BLOODLINE, EdgeType.KNOWS, 88, scope=prov),
            )
        )
    )

    canon = knowledge_matrix(store, PID, 152, [LI_GUANJIA.id], secrets=[BLOODLINE.id])
    grey = knowledge_matrix(store, PID, 152, [LI_GUANJIA.id], secrets=[BLOODLINE.id], scope=prov)

    assert canon.cell(LI_GUANJIA.id, BLOODLINE.id).state is KnowledgeState.BELIEVES
    assert grey.cell(LI_GUANJIA.id, BLOODLINE.id).state is KnowledgeState.KNOWS


def test_stale_evidence_stops_firing(matrix_store: Build) -> None:
    """ADR 0006：依据被作者改没了 ⇒ 立刻停火。这条边等于不存在。"""
    store = matrix_store(
        World(
            edges=(
                _edge(
                    XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88,
                    evidence_id=EV, evidence_status=EvidenceStatus.STALE,
                ),
            )
        )
    )

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN


def test_author_declared_edge_without_evidence_survives_the_stale_filter(
    matrix_store: Build,
) -> None:
    """`evidence_status='NONE'` 是哨兵值不是「空」。

    真库那一半才是这条的意义：SQL 里 `NULL != 'STALE'` 在三值逻辑下求值为 NULL 即假，
    那一列若可空，`knowledge_edges_at` 会**静默丢掉每一条作者声明的无证据边**——
    而作者声明正是整个产品（ADR 0004）。Fake 用的是 Python 的 `is not`，它天然测不到这个。
    """
    store = matrix_store(World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),)))

    cell = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
        XIAO_JUE.id, BLOODLINE.id
    )

    assert cell.state is KnowledgeState.KNOWS
    assert cell.evidence_id is None


def test_retracted_edge_stops_firing(matrix_store: Build) -> None:
    """同章更正撤回的边不算数（status 与时态正交）。"""
    store = matrix_store(
        World(
            edges=(
                _edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88, status=EdgeStatus.RETRACTED),
            )
        )
    )

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN


@pytest.mark.parametrize("scope", [InformationScope.PLANNED, InformationScope.REJECTED])
def test_planned_and_rejected_have_no_read_path(
    matrix_store: Build, scope: InformationScope
) -> None:
    """改 7：泄漏在**物理上**不可能，不是「大概率不会」。"""
    store = matrix_store(World())

    with pytest.raises(ValueError, match="不可读"):
        knowledge_matrix(store, PID, 152, [XIAO_JUE.id], scope=scope)


# ══════════════════════════════════════════════════════════════════════════
# 6. R3 DEAD_SPEAKS —— 打真库，不是打 FakeGraph
#
# ⚠️ **2026-08-14 这一节从 R4 换成了 R3**（[ADR 0027](../docs/adr/0027-scene-blocks-cut.md)
# 砍掉了 R4）。**换而不是删**，理由就是这个文件存在的理由：`RulesFakeGraph`
# （`test_checks.py` 里那份）手写了一遍五条件时态过滤，而 R4 那一节是**唯一**把它钉在
# 生产 `state_at` 上的地方。整节删掉的话，那个 Fake 从此可以单方面漂移，
# 而 `test_checks.py` 会一路绿着——正是本文件开头描述的那个故障。
#
# 换过来之后打的还是同两个后端方法（`resolve` + `state_at`），只是入口规则不同。
# ══════════════════════════════════════════════════════════════════════════

HEALTH_DIM = _node("state:conf:01JA", NodeLabel.STATE_DIM, "健康", dim_key="health")

R3_CAST = (*CAST, HEALTH_DIM)
"""R3 要一个状态维度节点才能表达「死了」（`HAS_STATE` 的 dst）。"""


def _dead_world(**edge_kwargs: object) -> World:
    """萧决在第 89 章死了。`edge_kwargs` 用来把这条边推进那五个条件的某一个里去。"""
    return World(
        nodes=R3_CAST,
        edges=(
            _edge(
                XIAO_JUE,
                HEALTH_DIM,
                EdgeType.HAS_STATE,
                89,
                value_key=HealthValue.DEAD,
                **edge_kwargs,  # type: ignore[arg-type]
            ),
        ),
    )


def _r3_ctx(store: StoryGraph, *, chapter: int = 151) -> CheckContext:
    return CheckContext(
        store=store,
        project_id=PID,
        chapter=chapter,
        paragraphs=["萧决道：「我还没死。」"],
    )


def test_r3_fires_when_a_dead_character_speaks(rules_store: Build) -> None:
    """图上他第 89 章死了，第 151 章的正文里还挂着他的对话标签。"""
    issues = check(_r3_ctx(rules_store(_dead_world())))

    assert len(issues) == 1
    assert issues[0].rule == "R3"
    assert issues[0].issue_type == "DEAD_SPEAKS"
    assert "萧决" in issues[0].message
    # 锚永远是三元组，永不 offset（ADR 0006）。
    assert issues[0].anchor.para_index == 0
    assert issues[0].anchor.quote_text == "萧决"


@pytest.mark.parametrize(("chapter", "expected"), [(88, 0), (89, 1), (151, 1)])
def test_r3_starts_firing_at_the_death_chapter(
    rules_store: Build, chapter: int, expected: int
) -> None:
    """`[valid_from, valid_to)` 的下界在这里是**可见的产品行为**：死亡那一章起才算死。"""
    assert len(check(_r3_ctx(rules_store(_dead_world()), chapter=chapter))) == expected


def test_r3_is_silent_when_nothing_is_declared(rules_store: Build) -> None:
    """闭世界：图上没有任何状态 ≠ 他死了。"""
    assert check(_r3_ctx(rules_store(World()))) == []


def test_r3_is_silent_when_the_speaker_surface_is_ambiguous(rules_store: Build) -> None:
    """「师兄」一章里可能是 8 个人 → 跳过，不猜（`usable_for_rules`）。"""
    store = rules_store(
        World(
            nodes=R3_CAST,
            edges=_dead_world().edges,
            extra_aliases={"师兄": (XIAO_JUE, GU_QINGYIN)},
        )
    )
    context = CheckContext(
        store=store, project_id=PID, chapter=151, paragraphs=["师兄道：「我还没死。」"]
    )

    assert check(context) == []


def test_r3_never_fires_on_provisional(rules_store: Build) -> None:
    """§5.4：拿抽取器的猜测报错 = 用 Agent 的猜测去质疑作者 = 原则 5 的反面。"""
    assert check(_r3_ctx(rules_store(_dead_world(scope=InformationScope.PROVISIONAL)))) == []


def test_r3_stops_firing_on_stale_evidence(rules_store: Build) -> None:
    """「依据没了还在质疑作者」是最伤的那类误报——这就是 STALE 90% 的价值。"""
    world = _dead_world(evidence_id=EV, evidence_status=EvidenceStatus.STALE)
    assert check(_r3_ctx(rules_store(world))) == []


def test_r3_never_fires_on_retracted(rules_store: Build) -> None:
    assert check(_r3_ctx(rules_store(_dead_world(status=EdgeStatus.RETRACTED)))) == []


# ══════════════════════════════════════════════════════════════════════════
# 7. real only —— Fake 够不着的那些
#
# 下面每一条都不是「懒得给 Fake 实现」：它们要么是 SQL 的属性（ORDER BY），要么是
# sqlite_store 在 Fake 之外多做的入参校验/守卫。Fake 是**参考实现**不是生产实现，
# 给它补上这些只会让它更像一份手抄本。审计点名的「生产路径未覆盖」，余数就在这一节。
# ══════════════════════════════════════════════════════════════════════════


def test_secret_ids_default_order_is_the_declaration_order(
    real_store: Callable[[World], SqliteStoryGraph],
) -> None:
    """`secrets=None`（面板的唯一路径）的列序 = id 升序 = ULID 创建顺序 = 作者声明顺序。

    **这是一条 ORDER BY 的断言，Fake 结构上测不到**：它按 dict 插入序返回，那恰好是
    「碰巧对了」。真库不加 ORDER BY 时 SQLite 也会碰巧按 rowid 给出插入序——所以这里
    **故意把插入序打乱**：没有那条 ORDER BY，列序就变成插入序，面板的列头会在
    「重跑一次导入」之后换位置，而作者是按列的位置逐格核对的。
    """
    scrambled = (XUANTIE, XIAO_JUE, BLOODLINE, GU_QINGYIN)
    store = real_store(World(nodes=scrambled))

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id])

    assert [s.id for s in matrix.secrets] == sorted([BLOODLINE.id, XUANTIE.id])
    assert [s.name for s in matrix.secrets] == ["血脉秘密", "玄铁令下落"]


def test_secrets_default_is_every_secret_in_the_project(matrix_store: Build) -> None:
    """`secrets=None` = 全书秘密。声明序 == id 序时两个后端必须给出同一份列头。"""
    store = matrix_store(World(edges=(_edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),)))

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id])

    assert [s.name for s in matrix.secrets] == ["血脉秘密", "玄铁令下落"]
    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.KNOWS
    assert matrix.cell(XIAO_JUE.id, XUANTIE.id).state is KnowledgeState.UNKNOWN


def test_non_secret_node_is_rejected_as_a_column(
    real_store: Callable[[World], SqliteStoryGraph],
) -> None:
    """`secrets=[一个人物的 id]` → 当场拒。

    Fake 直接 `NodeRef.of(self._nodes[s])`，**它会把一个人当成秘密列进面板列头**且毫无
    怨言。真实的触发路径不是调用方手滑：001_init 的复合外键（`secret.label`）挡住了
    「Character 节点登记成秘密」，这道运行时校验是它的第二层。
    """
    store = real_store(World())

    with pytest.raises(ValueError, match="label=Secret"):
        knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[GU_QINGYIN.id])


def test_two_active_knows_edges_at_one_chapter_blow_up(
    real_store: Callable[[World], SqliteStoryGraph],
) -> None:
    """(人,秘密) 的 exclusivity 是 single_per_src_dst，同一章两条有效 KNOWS = supersede 漏了。

    **不许悄悄取第一条**：Fake 的 `_cell` 就是取第一条（它 `return` 在循环里），于是一个
    数据层的 bug 会变成面板上一个看起来完全正常的答案。这里让它炸——同 state_at 对
    「两条 LOCATED_AT」的处置。
    """
    store = real_store(
        World(
            edges=(
                _edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 88),
                _edge(XIAO_JUE, BLOODLINE, EdgeType.KNOWS, 120),
            )
        )
    )

    with pytest.raises(StoreError, match="supersede"):
        knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    # 反面：ch119 只有一条有效，正常出答案——这道守卫不许把正常矩阵也炸了。
    assert knowledge_matrix(store, PID, 119, [XIAO_JUE.id], secrets=[BLOODLINE.id]).cell(
        XIAO_JUE.id, BLOODLINE.id
    ).since_chapter == 88


def test_cross_project_leakage_is_impossible(
    real_store: Callable[[World], SqliteStoryGraph],
) -> None:
    """全部时态查询都带 `project_id = :pid`。**Fake 的每个方法都 `del project_id`**，
    所以「别的书的边泄漏进这本书的矩阵」在 Fake 上是结构性测不到的。"""
    store = real_store(World())

    with pytest.raises(NodeNotFound):
        knowledge_matrix(store, "project:conf:别的书", 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])


# ══════════════════════════════════════════════════════════════════════════
# real only —— 保存提交令牌：ABA generation / CAS / 退休失败回滚
# ══════════════════════════════════════════════════════════════════════════


def _chapter_spec(pid: str, number: int, text: str) -> ChapterSpec:
    chapter = chapterize(text).chapters[0]
    return ChapterSpec(
        project_id=pid,
        number=number,
        heading=chapter.raw_heading,
        title=chapter.title,
        path=f"chapters/{number:04d}.md",
        text=text,
    )


def test_commit_chapter_snapshot_generations_are_monotonic_across_aba() -> None:
    """S1→S2→S1 得到三个不同、单调递增的 generation；相同 S1 连续保存不递增。"""
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    s1 = "第一章 甲\n\n第一版。\n"
    s2 = "第一章 甲\n\n第二版。\n"
    s1_sha = importer.text_digest(s1)

    t1 = store.commit_chapter_snapshot(_chapter_spec(pid, 1, s1), expected_text_sha256=s1_sha)
    t2 = store.commit_chapter_snapshot(_chapter_spec(pid, 1, s2), expected_text_sha256=t1.text_sha256)
    t3 = store.commit_chapter_snapshot(_chapter_spec(pid, 1, s1), expected_text_sha256=t2.text_sha256)
    assert (t1.source_generation, t2.source_generation, t3.source_generation) == (1, 2, 3)
    assert t1.text_sha256 == t3.text_sha256 == s1_sha
    # 第一轮 S1 token 在第三轮 S1 已 current 时仍因 generation 不同而失效。
    assert t1.source_generation != t3.source_generation

    t4 = store.commit_chapter_snapshot(_chapter_spec(pid, 1, s1), expected_text_sha256=t3.text_sha256)
    assert t4.changed is False
    assert t4.source_generation == 3
    conn.close()


def test_commit_chapter_snapshot_cas_rejects_stale_expected() -> None:
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    s1 = "第一章 甲\n\n第一版。\n"
    s1_sha = importer.text_digest(s1)
    store.commit_chapter_snapshot(_chapter_spec(pid, 1, s1), expected_text_sha256=s1_sha)

    with pytest.raises(ChapterWriteConflict):
        store.commit_chapter_snapshot(
            _chapter_spec(pid, 1, "第一章 甲\n\n不该落。\n"),
            expected_text_sha256="a" * 64,
        )
    assert store.current_chapter_hash(pid, 1) == s1_sha
    conn.close()


def test_retire_failure_rolls_back_the_snapshot_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退休失败 ⇒ 快照不能半提交：CAS/快照和退休必须在同一个事务里。"""
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    s1 = "第一章 甲\n\n第一版。\n"
    s1_sha = importer.text_digest(s1)
    store.commit_chapter_snapshot(_chapter_spec(pid, 1, s1), expected_text_sha256=s1_sha)

    import novel_harness.graph.queries as queries

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("注入：退休失败")

    monkeypatch.setattr(queries, "retire_stale_extractor_facts", boom)
    with pytest.raises(RuntimeError, match="退休失败"):
        store.commit_chapter_snapshot(
            _chapter_spec(pid, 1, "第一章 甲\n\n第二版。\n"),
            expected_text_sha256=s1_sha,
        )
    assert store.current_chapter_hash(pid, 1) == s1_sha
    assert store.current_chapter_generation(pid, 1) == 1
    conn.close()
