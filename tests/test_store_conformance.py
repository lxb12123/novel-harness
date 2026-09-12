"""**同一份可执行规格，跑在 FakeGraph 和真 SqliteStoryGraph 上。**

── 这个文件为什么必须存在 ────────────────────────────────────────────────

`test_fake_graph.py` 的断言跑在 `FakeGraph` 上，而那个 Fake **自己手写了一遍**那五个
时态条件。`test_checks.py` 的 R2/R3 同理。于是「测试全绿」这句话在生产查询路径上是虚的：
真 SQL、`NodeLabel` 校验、重复边的 StoreError——一条都没被执行过。

（这段原话点名的是 `knowledge_matrix` 那条路：闭世界推导、`KNOWS 压 BELIEVES`、
`secret_ids` 的默认列序。**那条路 2026-08-25 随秘密下线删了**，ADR 0039——
但这个文件的理由一个字没变，只是载体换成了 `state_at` 和 `resolve`。）

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

Fake 兜不住的那几条在文件末尾单列，标着 `real only` 并写明 Fake 为什么够不着——
它们是这次审计点名的「生产路径未覆盖」的余数。

── 真库怎么建 ────────────────────────────────────────────────────────────

走 `db.connect` + `db.migrate`（不是 `test_state_at.py` 那份手搓 executescript）：这个文件
要证的是**装配起来的生产路径**是对的，那就该连 PRAGMA 和 user_version 闸门一起跑。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

import pytest
from test_checks import FakeGraph as RulesFakeGraph
from test_fake_graph import FakeGraph as KnowledgeFakeGraph

from novel_harness import importer, project
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.graph import (
    AliasKind,
    ChapterSpec,
    Edge,
    EdgeProps,
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    StoryGraph,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.panel import character_state
from novel_harness.graph.store import ChapterWriteConflict
from novel_harness.text.chapterize import chapterize

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


XIAO_JUE = _node("character:conf:01J1", NodeLabel.CHARACTER, "萧决")
GU_QINGYIN = _node("character:conf:01J2", NodeLabel.CHARACTER, "顾清音")
LI_GUANJIA = _node("character:conf:01J3", NodeLabel.CHARACTER, "李管家")
QINGYUN = _node("location:conf:01J6", NodeLabel.LOCATION, "青云城主府")
BEIHUANG = _node("location:conf:01J7", NodeLabel.LOCATION, "北荒")

CAST = (XIAO_JUE, GU_QINGYIN, LI_GUANJIA, QINGYUN, BEIHUANG)


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
    """`state_at` 那条路的两个后端。**下面每条断言都跑两遍。**"""
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
# 4. 闭开区间 [valid_from, valid_to)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("scope", [InformationScope.PLANNED, InformationScope.REJECTED])
def test_planned_and_rejected_have_no_read_path(
    matrix_store: Build, scope: InformationScope
) -> None:
    """改 7：泄漏在**物理上**不可能，不是「大概率不会」。

    （载体 2026-08-24 从认知矩阵换成了人物状态卡——矩阵随秘密下线删了，而这道闸
    `require_queryable_scope` 一个字没改，它现在住在 `panel/scope.py`。）
    """
    store = matrix_store(World())

    with pytest.raises(ValueError, match="不可读"):
        character_state(store, PID, XIAO_JUE.id, 152, scope=scope)


# ══════════════════════════════════════════════════════════════════════════
# 6.（空缺）**规则打真库那一节没有了**
#
# ⚠️ 2026-09-05：这一节先后是 R4、然后是 R3（[ADR 0027] → [ADR 0042]），
# 现在**系统规则一条不剩**，它没有主语了，整节删掉。
#
# **删掉的是什么，说清楚**：它曾是唯一一处「拿一条真规则同时打 FakeGraph 和真库」的
# 地方——`RulesFakeGraph` 手写了一遍五条件时态过滤，那一节把它钉在生产 `state_at` 上。
# 今天唯一还在跑的规则是作者自定义的 `forbidden_literal`，它只读段落、根本不碰图，
# 所以**没有任何规则能承担这个角色**，硬留一节等于留一节假戏。
#
# **那条缝没有裸奔**：上面第 4 节（闭开区间 / PLANNED / 已撤回）打的就是同两个后端的
# `resolve` + `state_at`，Fake 漂了照样红。真正丢掉的只有「经由一条规则」这一层间接。
# 哪天再有一条读图的系统规则，把这一节按原样加回来。
# ══════════════════════════════════════════════════════════════════════════

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
