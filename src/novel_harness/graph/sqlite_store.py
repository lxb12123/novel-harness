"""`GraphStore`（= `StoryGraph` + `CanonWriter`）的 SQLite 实现（PLAN §8 Day 4）。

分工：**SQL 在 `queries.py`，编排在这里。** 这个文件里一条时态过滤都没有——
它只做五件 SQL 做不了的事：入参校验（scope / hops / NodeNotFound）、事务边界、
supersede 的分支决定、把边投影成 `StateSnapshot` / `Subgraph`，
以及写入侧那些「必须同生」的组合（节点 + canonical 别名 / 章节 + 快照）。

`quote_hash` 从 `decisions.py` import，**不在这里重新实现**（那份 docstring 立过
「别在别处再实现一遍」）：`evidence.quote_sha256` 和 `chapter.text_sha256` 是它仅有的
两个消费者，而两份实现里只要有一份哪天加了 `.strip()`，快照去重和证据锚就在那一刻
各说各话。`graph/ → decisions → db` 无环（decisions 不 import graph）。

`graph/` 是全系统唯一允许 import sqlite3 的目录（`tests/test_arch_guard.py` 拦 import，
不靠纪律）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager

from ..decisions import quote_hash
from ..ids import EntityType, new_id
from ..text import anchor
from . import queries
from .models import (
    LocationVisit,
    MIN_RULE_SURFACE_LEN,
    CANONICAL_ALIAS_LABELS,
    AliasHit,
    AliasKind,
    AliasSpec,
    AUTO_CANON_CORRECTABLE_EDGE_TYPES,
    CanonEdgeEditResult,
    CanonEdgeView,
    ChapterCommitToken,
    ChapterSnapshot,
    ChapterSpec,
    ChapterText,
    Edge,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    Evidence,
    EvidenceSpec,
    EvidenceStatus,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    NodeSpec,
    NodeUsage,
    Resolution,
    RetirementReport,
    RelationValue,
    StateSnapshot,
    StateValue,
    StoredAlias,
    StoredChapter,
    Subgraph,
    UpsertResult,)
from .store import (
    HOP2_EDGE_TYPES,
    MAX_HOPS,
    MAX_SUBGRAPH_NODES,
    QUERYABLE_SCOPES,
    ChapterWriteConflict,
    CanonEdgeRefused,
    NodeNotFound,
    QuoteMismatch,
    SnapshotInUse,
    SnapshotIsCurrent,
    StoreError,
)



def _default_edge_id(project_id: str) -> str:
    return new_id(EntityType.EDGE, project_id)


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """一个方法一个事务（`store.StoryGraph` 的实现约束 2）。

    显式 BEGIN IMMEDIATE 而不是 `with conn:`：后者在 db.py 把 `isolation_level` 设成
    None（autocommit）时**什么都不做**，于是 supersede 的「闭合旧边 + 插新边」会变成
    两个独立事务——中间崩一次就留下重叠区间。已经在事务里则不嵌套（SQLite 无嵌套事务）。
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


class SqliteStoryGraph:
    """`GraphStore` 的唯一实现 —— 即 `StoryGraph`（读 + upsert_edge）与 `CanonWriter`
    （建节点 / 别名 / 秘密 / 章节 / 快照 / 证据）两个 Protocol 的并集。

    **一个对象一条连接**，这是 `transaction()` 能罩住 `upsert_edge` 的前提
    （见 `store.GraphStore` 的论证）。

    Args:
        conn: 已经 `migrate()` 过、且 `PRAGMA foreign_keys=ON` 的连接（db.py 的活）。
            本类**不碰** `row_factory` 等连接配置——那是 `connect()` 的所有物，
            在这里改会让别的消费者拿到意料之外的行类型。
        edge_id_factory: 只为测试留的缝。默认走 `ids.new_id`——ID 的形状由 ADR 0003
            定死、由 `tests/test_ids.py` 的 golden 值钉住，本文件不该有第二份定义。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        edge_id_factory: Callable[[str], str] = _default_edge_id,
    ) -> None:
        self._conn = conn
        self._new_edge_id = edge_id_factory

    # ── 内部校验 ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_scope(scope: InformationScope) -> None:
        if scope not in QUERYABLE_SCOPES:
            # PLANNED 可写不可读是改 7 的要求：硬约束下沉为 filter，泄漏在**物理上**
            # 不可能发生。这一行就是「未来剧情泄漏率结构上恒为 0」的全部实现。
            raise ValueError(
                f"scope={scope} 不可读。只允许 {sorted(s.value for s in QUERYABLE_SCOPES)}："
                "PLANNED 今天没有任何读路径（原来经 panel/constraints.py 转译的那条窄出口"
                "2026-08-31 也删了，ADR 0041），REJECTED 只是防重抽的坟场"
            )

    @staticmethod
    def _check_chapter(chapter: int) -> None:
        """章号从 1 起。**读路径也要校验，不能只校验写路径。**

        写侧这条不变量是硬的（`EdgeSpec.valid_from_chapter` 是 `Field(ge=1)`，chapter 表是
        `CHECK(number >= 1)`），读侧却收 0 / 负数照单全收，然后静默返回一个语义上不可能
        存在的答案：`state_at(pid, 萧决, 0)` 给出一份**空**快照，而空快照在闭世界下是一个
        **断言**（`is_dead` 的 docstring：「没有 health=dead 的边 ⇒ 活着」）——于是人物卡
        理直气壮地告诉作者「他在第 0 章还活着、不在任何地方」，而不是承认这个问题问错了。

        触发形态是任何一次 off-by-one：0-based 的场景索引、「上一章」在第 1 章时算成 0
        （§5.2 的 F 分区就是 `WHERE chapter = N-1`）。它把调用方的一个 off-by-one 放大成
        头牌面板上一个看起来完全正常的错误答案——本模块处处「让它响亮地死」，唯独章号
        这一个入口曾经是静默的。

        **上界不管**：超过全书章数返回「最新状态」是闭开区间的正确语义，不是 bug。
        """
        if chapter < 1:
            raise ValueError(
                f"章号从 1 起（chapter 表 CHECK(number >= 1)），得到 {chapter}；"
                "第 0 章不存在，静默返回全 UNKNOWN 会把一个 off-by-one 变成面板上的错误答案"
            )

    def _require_node(self, project_id: str, node_id: str, *, what: str) -> Node:
        node = queries.fetch_node(self._conn, project_id, node_id)
        if node is None:
            raise NodeNotFound(f"{what} 不在项目 {project_id} 里：{node_id}")
        return node

    # ── resolve ───────────────────────────────────────────────────────────

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        rows = queries.alias_rows(self._conn, project_id, surfaces)
        by_surface: dict[str, list[AliasHit]] = {}
        for r in rows:
            by_surface.setdefault(r["surface"], []).append(
                AliasHit(
                    node=queries.to_node(r),
                    kind=r["kind"],
                    usable_for_rules=bool(r["usable_for_rules"]),
                )
            )

        if surfaces is None:
            # 角色册：SQL 已按长度降序排好，直接可喂 alternation 编译。
            ordered = [Resolution(surface=s, hits=h) for s, h in by_surface.items()]
        else:
            # 与入参**一一对应且同序**，重复的 surface 也各返回一条。解析不到的返回
            # hits=[]，不许静默丢——否则调用方分不清「没这个人」和「我没问过这个人」。
            ordered = [Resolution(surface=s, hits=by_surface.get(s, [])) for s in surfaces]

        if rules_only:
            return [r for r in ordered if r.usable_for_rules]
        return ordered

    # ── 校准层窄读（ADR 0033）：来源字段不被收窄掉 ────────────────────────

    def canon_version(self, project_id: str) -> int:
        row = self._conn.execute(
            "SELECT canon_version FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise NodeNotFound(f"项目不存在：{project_id}")
        return int(row["canon_version"])

    # ── state_at ──────────────────────────────────────────────────────────

    def state_at(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> StateSnapshot:
        self._check_scope(scope)
        self._check_chapter(chapter)
        node = self._require_node(project_id, node_id, what="node_id")
        edges = queries.out_edges_at(self._conn, project_id, node_id, chapter, scope)
        # 对端一次取齐：有向边的对端是 dst，无向边（RELATED_TO）的对端由 `peer_of` 算——
        # 它有一半概率是 src（ADR 0008），只取 dst 会让 `relations` 里一半的人查不到。
        dsts = queries.fetch_nodes(
            self._conn, project_id, [e.dst for e in edges] + [e.peer_of(node_id) for e in edges]
        )

        def _dst(edge: Edge) -> Node:
            n = dsts.get(edge.dst)
            if n is None:
                # 跨项目误引用。ULID 的 project_short 前缀就是为了让它在日志里一眼可见。
                raise StoreError(f"边 {edge.id} 的 dst {edge.dst} 不在项目 {project_id} 里")
            return n

        def _peer(edge: Edge) -> Node:
            n = dsts.get(edge.peer_of(node_id))
            if n is None:
                raise StoreError(
                    f"边 {edge.id} 的对端 {edge.peer_of(node_id)} 不在项目 {project_id} 里"
                )
            return n

        located = [e for e in edges if e.type is EdgeType.LOCATED_AT]
        if len(located) > 1:
            # LOCATED_AT 的 exclusivity=single_per_src 保证至多一条。出现两条 =
            # supersede 漏了，而下一步就是 R4 误报。**不许悄悄取第一条。**
            raise StoreError(
                f"{node_id} 在第 {chapter} 章有 {len(located)} 条 LOCATED_AT "
                f"（{[e.id for e in located]}）：supersede 漏了，不是渲染问题"
            )

        states = [
            StateValue(
                dim=_dst(e),
                dim_key=_dst(e).props.dim_key,
                value=e.props.value,
                value_key=e.props.value_key,
                since_chapter=e.valid_from_chapter,
                evidence_id=e.evidence_id,
            )
            for e in edges
            if e.type is EdgeType.HAS_STATE
        ]
        self._check_one_value_per_dim(node_id, chapter, states)
        relations = [
            RelationValue(
                edge_id=e.id,
                peer=_peer(e),
                value=e.props.value,
                since_chapter=e.valid_from_chapter,
                evidence_id=e.evidence_id,
            )
            for e in edges
            if e.type is EdgeType.RELATED_TO
        ]
        return StateSnapshot(
            node=node,
            chapter=chapter,
            scope=scope,
            edges=edges,
            location=_dst(located[0]) if located else None,
            states=states,
            relations=relations,
        )

    def state_history(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[StateValue]:
        """这个人截至第 `chapter` 章、**每一格填过的每一个值**（新的在前）。

        ── 为什么它只在这个类上，不进 `StoryGraph` 那个 Protocol ────────────
        往 Protocol 上加方法 = 两个 `FakeGraph` 各长一个存根，而 `@runtime_checkable`
        只查方法**存在**——那些存根会照样让 `isinstance` 为真、却什么都不做
        （`store.py` 里 `CanonWriter` 那一段把这条写死过）。这个方法只有一个消费者
        （人物卡「状态」那一格），没有第二种实现要对齐，所以留在具体类上。

        **和 `state_at().states` 是两个问题，别合并**：那个答「他现在什么样」（每格一条，
        喂模型、判 `is_dead` 的就是它），这个答「这一格填过什么」（每格多条，只给人看）。
        """
        self._check_scope(scope)
        self._check_chapter(chapter)
        self._require_node(project_id, node_id, what="node_id")
        edges = queries.history_edges_through(
            self._conn, project_id, node_id, chapter, scope, EdgeType.HAS_STATE
        )
        dims = queries.fetch_nodes(self._conn, project_id, [e.dst for e in edges])
        owned = queries.author_owned_edge_ids(
            self._conn, project_id, [e.id for e in edges]
        )
        out: list[StateValue] = []
        for edge in edges:
            dim = dims.get(edge.dst)
            if dim is None:
                raise StoreError(f"边 {edge.id} 的 dst {edge.dst} 不在项目 {project_id} 里")
            out.append(
                StateValue(
                    dim=dim,
                    dim_key=dim.props.dim_key,
                    value=edge.props.value,
                    value_key=edge.props.value_key,
                    since_chapter=edge.valid_from_chapter,
                    evidence_id=edge.evidence_id,
                    author_owned=edge.id in owned,
                )
            )
        return out

    def state_dimension_names(self, project_id: str, *, limit: int = 60) -> list[str]:
        """这本书已经用过的状态字段名（用得多的在前）。抽取的 prompt 拿它去复用。

        **不在 `StoryGraph` 那个 Protocol 上**：同 `state_history` 的理由——
        只有一个消费者，没有第二种实现要对齐。
        """
        return queries.state_dimension_names(self._conn, project_id, limit)

    def location_history(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[LocationVisit]:
        """这个人截至第 `chapter` 章**待过的每一个地方**（新的在前）。

        和 `state_history` 是同一件事的第二个格子（作者 2026-09-06：「所在地也用
        这种机制」），所以底下走的是同一条查询（`history_edges_through`），只是
        换个边类型。

        ── 它为什么值得存在 ──────────────────────────────────────────────
        位置是抽取里最爱变的一格，也是「同一处两个叫法」最多的一格（真书上 298 条
        待确认里 142 条是位置）。只印当前那一个，作者看不出系统把他从哪儿挪到了哪儿；
        把走过的地方按章列出来，他一眼就能看出「宁荣街」和「荣国府」是不是同一处。
        """
        self._check_scope(scope)
        self._check_chapter(chapter)
        self._require_node(project_id, node_id, what="node_id")
        edges = queries.history_edges_through(
            self._conn, project_id, node_id, chapter, scope, EdgeType.LOCATED_AT
        )
        places = queries.fetch_nodes(self._conn, project_id, [e.dst for e in edges])
        owned = queries.author_owned_edge_ids(
            self._conn, project_id, [e.id for e in edges]
        )
        out: list[LocationVisit] = []
        for edge in edges:
            place = places.get(edge.dst)
            if place is None:
                raise StoreError(f"边 {edge.id} 的 dst {edge.dst} 不在项目 {project_id} 里")
            out.append(
                LocationVisit(
                    place=place,
                    since_chapter=edge.valid_from_chapter,
                    evidence_id=edge.evidence_id,
                    author_owned=edge.id in owned,
                )
            )
        return out

    @staticmethod
    def _check_one_value_per_dim(node_id: str, chapter: int, states: list[StateValue]) -> None:
        """对齐上面 LOCATED_AT 那道守卫，接的是 HAS_STATE 那条漏网。

        `HAS_STATE` 的 exclusivity 是 single_per_src_dst——但**语义上的维度键是
        `dst.props.dim_key`，不是 dst 的节点 id**。两个 StateDim 节点共享 dim_key
        （「健康」和「生死」）时，supersede 认为它们是两个不同的维度，一条都不闭合，
        于是一个 ch89 死、ch100 复活的人物在 ch152 同时挂着 dead 和 alive 两条有效边，
        而 `StateSnapshot.is_dead` 的 `any()` 让 dead 永远压过 alive → R3 对全书每一句
        「萧决道：」报死人说话 → M3 的「误报 <1 条/章」当场崩。

        schema 的 `idx_state_dim_key` 已经让那种数据进不来。这道守卫是第二层，接的是
        它建立之前就已经在库里的行、以及任何绕开它的写入路径——**只做一层的话，M4 的
        抽取器将来仍能绕开**。（反过来只做这一层也不行：作者会在面板上撞见一个自己
        修不了的异常。）

        `dim_key` 为 None 的维度按 dst 节点 id 分组：那类维度没有规则消费（ADR 0005 的
        增长规则说它就不该有键），但同一个节点上出现两条边同样是 supersede 漏了。
        """
        seen: dict[tuple[str, str], StateValue] = {}
        for s in states:
            key = ("dim_key", s.dim_key) if s.dim_key is not None else ("dim_node", s.dim.id)
            first = seen.get(key)
            if first is not None:
                raise StoreError(
                    f"{node_id} 在第 {chapter} 章的「{key[1]}」维度上有两个互斥值"
                    f"（{first.dim.name}={first.value_key or first.value} / "
                    f"{s.dim.name}={s.value_key or s.value}）：supersede 漏了，不是渲染问题"
                )
            seen[key] = s

    # ── subgraph ──────────────────────────────────────────────────────────

    def subgraph(
        self,
        project_id: str,
        center: str,
        chapter: int,
        *,
        hops: int = 1,
        edge_types: Collection[EdgeType] | None = None,
        scope: InformationScope = InformationScope.CANON,
        max_nodes: int | None = None,
    ) -> Subgraph:
        self._check_scope(scope)
        self._check_chapter(chapter)
        if not 1 <= hops <= MAX_HOPS:
            # 不许静默截断成 2——那会让调用方以为自己拿到了 3 跳。
            raise ValueError(f"hops 必须在 1..{MAX_HOPS}（实测 3 跳 = 30 倍爆炸且信息量不增），得到 {hops}")
        center_node = self._require_node(project_id, center, what="center")

        # 两层显式 JOIN，**不用递归 CTE**（§5.5）：hops≤2 时它更快更好读，且 UNION 版
        # 会把同一节点在 hop=1 和 hop=2 各返回一行（Subgraph 的 validator 抓这个）。
        hop1 = queries.incident_edges_at(
            self._conn, project_id, [center], chapter, scope, edge_types
        )
        seen_ids: list[str] = [center]
        for e in hop1:
            for nid in (e.src, e.dst):
                if nid not in seen_ids:
                    seen_ids.append(nid)

        found_edges: dict[str, Edge] = {e.id: e for e in hop1}
        if hops == 2:
            frontier = [nid for nid in seen_ids if nid != center]
            # 第 2 跳**必须**带类型过滤：调用方没指定时用 HOP2_EDGE_TYPES。放开
            # HAS_STATE / MEMBER_OF / LOCATED_AT 任一条 = 2 跳返回全书。
            hop2_types = edge_types if edge_types is not None else HOP2_EDGE_TYPES
            for e in queries.incident_edges_at(
                self._conn, project_id, frontier, chapter, scope, hop2_types
            ):
                found_edges.setdefault(e.id, e)
                for nid in (e.src, e.dst):
                    if nid not in seen_ids:
                        seen_ids.append(nid)

        # 作者在设置页填的数压过默认值；没填（`None`）就用引擎那一档。
        # **不在这儿读设置**：`graph/` 不认识 `settings`，那个数由装配层穿进来
        # （`api/app.py` 的 subgraph 路由）。
        cap = MAX_SUBGRAPH_NODES if max_nodes is None else max_nodes
        truncated = len(seen_ids) > cap
        kept_ids = seen_ids[:cap]  # center 永远在第 0 位
        kept = set(kept_ids)
        nodes_by_id = queries.fetch_nodes(self._conn, project_id, kept_ids)
        missing = kept - nodes_by_id.keys()
        if missing:
            raise StoreError(f"子图里的节点不在项目 {project_id} 里（跨项目引用？）：{sorted(missing)}")
        return Subgraph(
            center=center_node,
            chapter=chapter,
            hops=hops,
            scope=scope,
            nodes=[nodes_by_id[i] for i in kept_ids],
            edges=[e for e in found_edges.values() if e.src in kept and e.dst in kept],
            truncated=truncated,
        )

    # ── upsert_edge ───────────────────────────────────────────────────────

    def upsert_edge(self, spec: EdgeSpec) -> UpsertResult:
        # evidence_id 与 evidence_status 两列同生同死（DB 的 CHECK 强制），所以
        # EdgeSpec 里没有 evidence_status——它在这里由 evidence_id 推导，只此一处。
        evs = EvidenceStatus.NONE if spec.evidence_id is None else EvidenceStatus.FRESH
        with _transaction(self._conn):
            self._require_node(spec.project_id, spec.src, what="src")
            self._require_node(spec.project_id, spec.dst, what="dst")

            existing = queries.find_by_identity(self._conn, spec)
            if existing is not None:
                # 撞幂等键 = 重跑。**只**更新 props 类字段，不跑 supersede、不碰
                # valid_to_chapter、不碰 status（理由见 store.upsert_edge 的契约：
                # 碰了会让 M4 断点续跑时复活已闭合的边 → 重叠区间 → 两条互斥边）。
                edge = queries.update_edge_facets(self._conn, existing.id, spec, evs)
                return UpsertResult(edge=edge, created=False)

            exclusivity = queries.exclusivity_of(self._conn, spec.type)
            # **只找同一章的那一条**（ADR 0043）。从前这里找的是整个语义槽里所有还开着
            # 的边，然后按先后各自闭合 / 撤回 / 抛「乱序」——而「谁盖住谁」现在在读的
            # 时候算（`queries.CURRENT_EDGE_CTE`），写的时候只剩同章更正这一件事。
            #
            # **别把这里改回「顺手把更早的那条闭合掉」**：那正是 2026-09-05 的病根——
            # 补全队列按「离作者正在写的那一章多近」倒着跑（那是对的，ADR 0036），
            # 于是每一条更早的事实都撞上一条更晚的，整章回滚。真书 62 章因此没有事件。
            same_chapter = queries.find_same_chapter_conflicts(self._conn, spec, exclusivity)

            edge = queries.insert_edge(self._conn, self._new_edge_id(spec.project_id), spec, evs)
            # 同章更正（「他在青云城…然后去了北荒」都在 ch151）：前一条撤回。
            # 它**不能**表达成闭合——`[151,151)` 是空区间，意思是「这条事实从未成立」。
            retracted = [queries.retract_edge(self._conn, old.id) for old in same_chapter]
            return UpsertResult(edge=edge, created=True, closed=[], retracted=retracted)

    # ── CanonWriter ───────────────────────────────────────────────────────

    def transaction(self) -> AbstractContextManager[None]:
        return _transaction(self._conn)

    def find_edge_by_identity(self, spec: EdgeSpec) -> Edge | None:
        return queries.find_by_identity(self._conn, spec)

    def upsert_node(self, spec: NodeSpec) -> Node:
        with _transaction(self._conn):
            found = queries.find_node_by_name(self._conn, spec.project_id, spec.label, spec.name)
            if len(found) > 1:
                # 只有本方法建得出节点，而它是幂等的——所以这个状态不该存在。
                # 它若存在，resolve(name) 会返回 2 个 hit → ambiguous →
                # usable_for_rules 为假 → 面板上整行消失，且没有一步会报错。
                raise StoreError(
                    f"项目 {spec.project_id} 里有 {len(found)} 个 label={spec.label} 的"
                    f"「{spec.name}」（{[n.id for n in found]}）：幂等键撞出多行，"
                    "resolve 会把它读成歧义称呼，而歧义称呼在面板上是整行消失"
                )
            if found:
                # 重复声明 = 更 props。
                return queries.update_node_props(self._conn, found[0].id, spec.props)

            node_id = new_id(EntityType.for_node_label(spec.label), spec.project_id)
            node = queries.insert_node(
                self._conn,
                node_id,
                project_id=spec.project_id,
                label=spec.label,
                name=spec.name,
                props=spec.props,
            )
            if spec.label in CANONICAL_ALIAS_LABELS:
                queries.insert_alias(
                    self._conn,
                    new_id(EntityType.ALIAS, spec.project_id),
                    project_id=spec.project_id,
                    node_id=node.id,
                    surface=spec.name,
                    kind=AliasKind.CANONICAL,
                    usable_for_rules=len(spec.name) >= MIN_RULE_SURFACE_LEN,
                )
            return node

    def set_first_appearance(self, project_id: str, node_id: str, chapter: int) -> Node:
        with _transaction(self._conn):
            node = self._require_node(project_id, node_id, what="node_id")
            # merge 不是 update：整列覆盖会抹掉抽取写进去的人物档案，且不报错。
            return queries.merge_node_props(
                self._conn, node, {"first_appears_chapter": chapter}
            )

    def ensure_state_dim(self, project_id: str, dim_key: str, name: str) -> Node:
        with _transaction(self._conn):
            found = queries.find_state_dim(self._conn, project_id, dim_key)
            if len(found) > 1:
                # idx_state_dim_key 是 UNIQUE，所以这只可能是索引建立之前进来的行。
                # 放它过去的产物：supersede 认为那是两个维度，一条都不闭合，
                # 而 is_dead 的 any() 让 dead 永远压过 alive → R3 对全书每一句
                # 「萧决道：」报死人说话。
                raise StoreError(
                    f"项目 {project_id} 里有 {len(found)} 个 dim_key={dim_key!r} 的状态维度"
                    f"（{[n.id for n in found]}）：supersede 会把它们当成两个维度，"
                    "一条都不闭合，于是同一个人同时挂着两个互斥的状态"
                )
            if found:
                # **不改名**：作者改过的显示名不该被一次声明悄悄改回去（see store.py）。
                return found[0]
            node_id = new_id(EntityType.for_node_label(NodeLabel.STATE_DIM), project_id)
            # 走 insert_node 而不是 upsert_node：后者的幂等键是 name，而这里的身份是
            # dim_key，两者在「作者改过名」时会分叉。到这一行时 dim_key 已经确认无主。
            return queries.insert_node(
                self._conn,
                node_id,
                project_id=project_id,
                label=NodeLabel.STATE_DIM,
                name=name,
                props=NodeProps(dim_key=dim_key),
            )

    def add_alias(self, spec: AliasSpec) -> StoredAlias:
        with _transaction(self._conn):
            self._require_node(spec.project_id, spec.node_id, what="node_id")
            return queries.insert_alias(
                self._conn,
                new_id(EntityType.ALIAS, spec.project_id),
                project_id=spec.project_id,
                node_id=spec.node_id,
                surface=spec.surface,
                kind=spec.kind,
                usable_for_rules=spec.usable_for_rules,
                source=spec.source,
            )

    def aliases_of(self, project_id: str, node_id: str) -> list[StoredAlias]:
        """一个节点的全部别名（含 canonical），canonical 优先、其余按创建时间。"""
        rows = self._conn.execute(
            """
            SELECT id, project_id, node_id, surface, kind, usable_for_rules,
                   source, status, derived_from_alias_id, created_at
              FROM alias
             WHERE project_id = ? AND node_id = ?
             ORDER BY (kind = 'canonical') DESC, created_at, id
            """,
            (project_id, node_id),
        ).fetchall()
        return [
            StoredAlias(
                id=r["id"],
                project_id=r["project_id"],
                node_id=r["node_id"],
                surface=r["surface"],
                kind=AliasKind(r["kind"]),
                usable_for_rules=bool(r["usable_for_rules"]),
                source=r["source"],
                status=r["status"],
                derived_from_alias_id=r["derived_from_alias_id"],
            )
            for r in rows
            if r["status"] == "ACTIVE"
        ]

    def retract_alias(self, project_id: str, alias_id: str) -> StoredAlias:
        with _transaction(self._conn):
            row = queries.fetch_alias(self._conn, alias_id)
            if row is None:
                raise self._alias_missing(project_id, alias_id)
            if row["project_id"] != project_id:
                raise self._alias_missing(project_id, alias_id)
            if row["kind"] == AliasKind.CANONICAL.value:
                raise CanonEdgeRefused("canonical 是本名索引，不能撤回：改本名请改节点本身")
            if row["status"] != "ACTIVE":
                raise CanonEdgeRefused(f"alias {alias_id} 已经撤回了")
            queries.retract_alias(self._conn, alias_id)
            return self._read_alias(alias_id)

    def edit_alias(
        self,
        project_id: str,
        alias_id: str,
        *,
        surface: str | None = None,
        usable_for_rules: bool | None = None,
    ) -> StoredAlias:
        with _transaction(self._conn):
            row = queries.fetch_alias(self._conn, alias_id)
            if row is None or row["project_id"] != project_id:
                raise self._alias_missing(project_id, alias_id)
            if row["kind"] == AliasKind.CANONICAL.value:
                raise CanonEdgeRefused("canonical 是本名索引，不能改：改本名请改节点本身")
            new_surface = surface if surface is not None else row["surface"]
            new_usable = (
                usable_for_rules if usable_for_rules is not None else bool(row["usable_for_rules"])
            )
            # 不管原来是谁写的，改过 = 作者接手：撤回旧行 + 新建 author 派生行
            # （§5 022：作者版不随旧机器证据失效）。
            queries.retract_alias(self._conn, alias_id)
            new_id_value = new_id(EntityType.ALIAS, project_id)
            return queries.insert_alias(
                self._conn,
                new_id_value,
                project_id=project_id,
                node_id=row["node_id"],
                surface=new_surface,
                kind=AliasKind(row["kind"]),
                usable_for_rules=new_usable,
                source="author",
                derived_from_alias_id=alias_id,
            )

    def reassign_alias(
        self, project_id: str, alias_id: str, *, to_node_id: str
    ) -> StoredAlias:
        with _transaction(self._conn):
            row = queries.fetch_alias(self._conn, alias_id)
            if row is None or row["project_id"] != project_id:
                raise self._alias_missing(project_id, alias_id)
            if row["kind"] == AliasKind.CANONICAL.value:
                raise CanonEdgeRefused("canonical 是本名索引，不能改归属")
            # 目标必须是同项目 Character。
            target = self._require_node(project_id, to_node_id, what="目标人物")
            if target.label is not NodeLabel.CHARACTER:
                raise CanonEdgeRefused(f"别名只能改到 Character，{to_node_id} 是 {target.label.value}")
            queries.retract_alias(self._conn, alias_id)
            new_id_value = new_id(EntityType.ALIAS, project_id)
            return queries.insert_alias(
                self._conn,
                new_id_value,
                project_id=project_id,
                node_id=to_node_id,
                surface=row["surface"],
                kind=AliasKind(row["kind"]),
                usable_for_rules=bool(row["usable_for_rules"]),
                source="author",
                derived_from_alias_id=alias_id,
            )

    def _read_alias(self, alias_id: str) -> StoredAlias:
        row = queries.fetch_alias(self._conn, alias_id)
        if row is None:  # pragma: no cover - 写入事务内读不回只能是库坏了
            raise RuntimeError(f"alias 写入后读不回：{alias_id}")
        return StoredAlias(
            id=row["id"],
            project_id=row["project_id"],
            node_id=row["node_id"],
            surface=row["surface"],
            kind=AliasKind(row["kind"]),
            usable_for_rules=bool(row["usable_for_rules"]),
            source=row["source"],
            status=row["status"],
            derived_from_alias_id=row["derived_from_alias_id"],
        )

    @staticmethod
    def _alias_missing(project_id: str, alias_id: str) -> Exception:
        from .store import NodeNotFound

        return NodeNotFound(f"alias {alias_id} 不在项目 {project_id} 里")

    def retire_stale_extractor_facts(
        self, project_id: str, chapter_id: str, current_snapshot_id: str
    ) -> RetirementReport:
        with _transaction(self._conn):
            retirement = queries.retire_stale_extractor_facts(
                self._conn, project_id, chapter_id, current_snapshot_id
            )
            queries.bump_canon_once_if_retired_canon(
                self._conn, project_id, retirement
            )
            return retirement

    def chapter_disk_stats(self, project_id: str) -> dict[int, tuple[int | None, int | None]]:
        return queries.chapter_disk_stats(self._conn, project_id)

    def put_chapter(self, spec: ChapterSpec) -> StoredChapter:
        with _transaction(self._conn):
            return self._put_chapter_locked(spec)

    def _put_chapter_locked(self, spec: ChapterSpec) -> StoredChapter:
        """`put_chapter` 的事务体（`commit_chapter_snapshot` 在同一事务里复用）。

        调用方必须已持有 `_transaction`——本函数绝不自己 BEGIN/COMMIT。
        """
        sha = quote_hash(spec.text)
        row = queries.find_chapter_by_number(self._conn, spec.project_id, spec.number)
        if row is None:
            chapter_id = new_id(EntityType.CHAPTER, spec.project_id)
            generation = 1
            # Chapter 节点和 chapter 行**同生**，而且没有 canonical 别名
            # （CANONICAL_ALIAS_LABELS 里没有它）：300 章 = 300 条章标进角色册。
            queries.insert_node(
                self._conn,
                chapter_id,
                project_id=spec.project_id,
                label=NodeLabel.CHAPTER,
                name=spec.heading,
                props=NodeProps(),
            )
            queries.insert_chapter(self._conn, chapter_id, spec, sha)
            # 018：每章从出生起就有一行 summary head（current 可为 NULL），
            # 否则「首次生成」的 expected-null CAS 会更新零行（不变量 5/20）。
            queries.insert_chapter_summary_head(self._conn, chapter_id)
            created = True
        else:
            chapter_id = row.id
            # 单调 generation：只有当前 text hash 真正切换才加一（018）。
            # 从 S2 还原到历史 S1 也是切换 —— 否则 S1 的旧任务会借 ABA 复活。
            generation = row.snapshot_generation
            node = self._require_node(spec.project_id, chapter_id, what="chapter 节点")
            if node.name != spec.heading:
                queries.update_node_name(self._conn, chapter_id, spec.heading)
            # **一个字节都没变就不写。** `sync` 会对整本书每一章都调到这里，
            # 而 2026-08-14 起它还会在**每次回到标签页时**跑一遍（回焦对齐）——
            # 无条件 UPDATE 的话，722 章的书每对一次就搅一遍全书的 WAL，
            # 而其中 721 章一个字节没动。
            #
            # 判据是 `text_sha256` **加上那两列 stat**：
            #
            # · sha 相等 ⇒ `heading`/`title`（从正文切出来的）和 `path`（由幂等键
            #   `number` 定）全都相等，那次 UPDATE 唯一会改的是 `updated_at`，
            #   而那一列没有任何读者依赖它跳动。
            # · **但 stat 也必须相等才能跳过**。文件被 touch 过（`rsync` / 保存了
            #   一份一模一样的内容）时 sha 不变而 mtime 变了——不把新 stat 记下来，
            #   下一次回焦检查又会判它「变了」，于是**这一章永远重读**（迁移 015）。
            if (row.text_sha256, row.disk_mtime_ns, row.disk_size) != (
                sha,
                spec.disk_mtime_ns,
                spec.disk_size,
            ):
                if row.text_sha256 != sha:
                    generation = row.snapshot_generation + 1
                queries.update_chapter(
                    self._conn, chapter_id, spec, sha, snapshot_generation=generation
                )
            created = False

        snapshot_id = queries.find_snapshot(self._conn, chapter_id, sha)
        snapshot_created = snapshot_id is None
        if snapshot_id is None:
            snapshot_id = queries.insert_snapshot(
                self._conn,
                new_id(EntityType.SNAPSHOT, spec.project_id),
                chapter_id,
                spec.text,
                sha,
            )
        return StoredChapter(
            id=chapter_id,
            project_id=spec.project_id,
            number=spec.number,
            title=spec.title,
            path=spec.path,
            text_sha256=sha,
            snapshot_id=snapshot_id,
            snapshot_generation=generation,
            created=created,
            snapshot_created=snapshot_created,
        )

    def commit_chapter_snapshot(
        self, spec: ChapterSpec, *, expected_text_sha256: str
    ) -> ChapterCommitToken:
        """保存路径的单一图层事务：CAS → 落快照 → 退休 → 至多一次 canon bump。

        - `expected_text_sha256` 是调用方依据的 **DB current hash**（保存入口已在
          章级锁内把 DB 对齐到磁盘并校验过；这一层是第二道、也是最后一道闸）。
        - 旧 hash/generation 必须在这个 `BEGIN IMMEDIATE` 内读取——不能由事务外
          调用方传一个会过期的 previous 值。
        - 退休失败（含注入测试）⇒ 快照和 CAS 一起回滚：快照不能半提交。
        - 返回的 token 是保存后所有自动任务的唯一输入（ADR 0029）。
        """
        with _transaction(self._conn):
            row = queries.find_chapter_by_number(self._conn, spec.project_id, spec.number)
            previous_hash = row.text_sha256 if row is not None else None
            # 从没进过库的章（DB 行缺失）没有「previous hash」可比：expected 的 409
            # 保护在保存入口（expected vs 磁盘）已经完成，这里放行首笔提交。
            if previous_hash is not None and previous_hash != expected_text_sha256:
                raise ChapterWriteConflict(expected_text_sha256, previous_hash)
            stored = self._put_chapter_locked(spec)
            retirement = queries.retire_stale_extractor_facts(
                self._conn, spec.project_id, stored.id, stored.snapshot_id
            )
            queries.bump_canon_once_if_retired_canon(
                self._conn, spec.project_id, retirement
            )
            # 021 / Task 9：正文换了，锚在旧快照上的 PENDING 提案退出待确认
            # （status 仍是 PENDING 审计状态）。与快照提交同一事务（不变量 20）。
            queries.supersede_obsolete_proposals(
                self._conn, spec.project_id, spec.number, stored.snapshot_id
            )
            return ChapterCommitToken(
                project_id=spec.project_id,
                chapter_id=stored.id,
                chapter_number=stored.number,
                source_snapshot_id=stored.snapshot_id,
                source_generation=stored.snapshot_generation,
                text_sha256=stored.text_sha256,
                text=spec.text,
                changed=previous_hash != stored.text_sha256,
                # 这份账算完不许扔：它是「你刚改的这一段，原本支撑着 N 条已确认的
                # 事实」那句话的全部数据，而它从前只喂了一次 canon bump 就没了。
                retirement=retirement,
            )

    # ══════════════════════════════════════════════════════════════════════
    # Canon 边纠错（020 / Task 8）——「先可逆、后自动」的入口
    # ══════════════════════════════════════════════════════════════════════

    def canon_edge_view(self, project_id: str, edge_id: str) -> CanonEdgeView:
        with _transaction(self._conn):
            edge = self._editable_canon_edge(project_id, edge_id)
            return self._edge_view(project_id, edge)

    def edit_canon_edge(
        self,
        project_id: str,
        edge_id: str,
        *,
        new_src: str,
        new_dst: str,
        props: EdgeProps,
        expected_canon_version: int,
        action: str = "EDIT",
    ) -> CanonEdgeEditResult:
        with _transaction(self._conn):
            edge = self._editable_canon_edge(project_id, edge_id)
            slot_key = queries.canon_edge_slot_key(edge)
            canon_version = self._require_canon_version(project_id)
            if canon_version != expected_canon_version:
                from ..project import StaleBaseVersion

                raise StaleBaseVersion(
                    project_id, expected=expected_canon_version, current=canon_version
                )
            # 类型化校验（与节点 label 一起在这里收口）。
            self._validate_correction_target(project_id, edge, new_src, new_dst, props)

            identity_changed = (
                new_src != edge.src or new_dst != edge.dst or edge.status is not EdgeStatus.ACTIVE
            )
            if identity_changed:
                result = self._replace_edge(
                    project_id, edge, slot_key, new_src, new_dst, props, action
                )
            else:
                result = self._patch_edge_props(project_id, edge, slot_key, props, action)
            return result

    def retract_canon_edge(
        self,
        project_id: str,
        edge_id: str,
        *,
        expected_canon_version: int,
    ) -> CanonEdgeEditResult:
        with _transaction(self._conn):
            edge = self._editable_canon_edge(project_id, edge_id)
            slot_key = queries.canon_edge_slot_key(edge)
            canon_version = self._require_canon_version(project_id)
            if canon_version != expected_canon_version:
                from ..project import StaleBaseVersion

                raise StaleBaseVersion(
                    project_id, expected=expected_canon_version, current=canon_version
                )
            before_props = edge.props.model_dump_json()
            old_override = queries.supersede_active_override(
                self._conn, project_id, slot_key
            )
            decision_id = self._append_canon_edge_decision(
                project_id, edge, action="RETRACT", before_props=before_props,
                after_props=None,
            )
            override_id = new_id(EntityType.EDGE, project_id)
            queries.insert_canon_override(
                self._conn,
                override_id=override_id,
                project_id=project_id,
                slot_key=slot_key,
                edge_type=edge.type.value,
                source_edge_id=edge.id,
                replacement_edge_id=None,
                before_props_json=before_props,
                after_props_json=None,
                action="RETRACT",
                decision_log_id=decision_id,
            )
            if old_override is not None:
                queries.link_override_chain(self._conn, old_override, override_id)
            queries.retract_edge(self._conn, edge.id)
            canon_version = self._bump_canon(project_id)
            view = CanonEdgeView(
                edge_id=edge.id,
                edge_type=edge.type,
                src=edge.src,
                dst=edge.dst,
                props=edge.props,
                valid_from_chapter=edge.valid_from_chapter,
                source=edge.source,
                evidence_id=edge.evidence_id,
                evidence_status=edge.evidence_status,
                author_owned=False,
                slot_key=slot_key,
                canon_version=canon_version,
            )
            return CanonEdgeEditResult(
                edge_id=edge.id,
                replacement_edge_id=None,
                canon_version=canon_version,
                retracted=True,
                view=view,
            )

    def _editable_canon_edge(self, project_id: str, edge_id: str) -> Edge:
        """读取 + 可编辑资格校验（allowlist / current CANON / 非裸 STALE）。"""
        try:
            edge = queries.fetch_edge(self._conn, edge_id)
        except LookupError as exc:
            raise CanonEdgeRefused(str(exc)) from exc
        if edge.project_id != project_id:
            raise CanonEdgeRefused(f"edge {edge_id} 不属于项目 {project_id}")
        if edge.type not in AUTO_CANON_CORRECTABLE_EDGE_TYPES:
            raise CanonEdgeRefused(
                f"edge type {edge.type.value} 不在可纠错 allowlist，不得 auto-Canon"
            )
        if edge.status is not EdgeStatus.ACTIVE or edge.information_scope is not InformationScope.CANON:
            raise CanonEdgeRefused(f"edge {edge_id} 不是 current CANON（已撤回/非 CANON）")
        override = queries.active_canon_override_for_edge(
            self._conn, project_id, edge_id
        )
        protected = override is not None
        if edge.evidence_status is EvidenceStatus.STALE and not protected:
            raise CanonEdgeRefused(
                f"edge {edge_id} 是裸 STALE 机器边（无 ACTIVE override 保护），拒绝纠错"
            )
        return edge

    def _validate_correction_target(
        self,
        project_id: str,
        edge: Edge,
        new_src: str,
        new_dst: str,
        props: EdgeProps,
    ) -> None:
        """类型化目标校验：跨项目/错误 label 在这里收口（§4.6）。"""
        def _require(role: str, node_id: str, label: NodeLabel) -> None:
            node = self._require_node(project_id, node_id, what=role)
            if node.label is not label:
                raise CanonEdgeRefused(
                    f"{role} {node_id} 的 label 是 {node.label.value}，需要 {label.value}"
                )

        if edge.type is EdgeType.LOCATED_AT:
            _require("src", new_src, NodeLabel.CHARACTER)
            _require("dst", new_dst, NodeLabel.LOCATION)
        elif edge.type is EdgeType.HAS_STATE:
            _require("src", new_src, NodeLabel.CHARACTER)
            _require("dst", new_dst, NodeLabel.STATE_DIM)
            # `dim_key` 不在这条闸里：多数维度没有机器键、`None` 是它们的正常状态
            # （2026-08-27 裁定）。上面 `_require("dst", ..., STATE_DIM)` 已经确保
            # 目标真的是个维度节点——这才是「这条边关于哪个维度」的身份校验，
            # 字符串键管不到、也不该管。这里只剩 `value` 这一件事必须非空。
            if not props.value:
                raise CanonEdgeRefused("HAS_STATE 需要 value")
        elif edge.type is EdgeType.RELATED_TO:
            _require("src", new_src, NodeLabel.CHARACTER)
            _require("dst", new_dst, NodeLabel.CHARACTER)

    def _replace_edge(
        self,
        project_id: str,
        edge: Edge,
        slot_key: str,
        new_src: str,
        new_dst: str,
        props: EdgeProps,
        action: str,
    ) -> CanonEdgeEditResult:
        """identity 改变：软撤回旧边 + ACTIVE override + 建/恢复 replacement。"""
        spec = EdgeSpec(
            project_id=project_id,
            src=new_src,
            dst=new_dst,
            type=edge.type,
            props=props,
            valid_from_chapter=edge.valid_from_chapter,
            information_scope=InformationScope.CANON,
            source=EdgeSource.AUTHOR,
            evidence_id=None,
        )
        restored = queries.find_edge_by_identity_any_status(self._conn, spec)
        before_props = edge.props.model_dump_json()
        if restored is not None:
            replacement_id = restored.id
            queries.restore_retracted_edge(self._conn, replacement_id)
            queries.update_edge_props_only(self._conn, replacement_id, props.model_dump_json())
        else:
            replacement_id = self._new_edge_id(project_id)
            queries.insert_edge(
                self._conn, replacement_id, spec, EvidenceStatus.NONE
            )
        decision_id = self._append_canon_edge_decision(
            project_id, edge, action=action, before_props=before_props,
            after_props=props.model_dump_json(),
        )
        old_override = queries.supersede_active_override(self._conn, project_id, slot_key)
        override_id = new_id(EntityType.EDGE, project_id)
        queries.insert_canon_override(
            self._conn,
            override_id=override_id,
            project_id=project_id,
            slot_key=slot_key,
            edge_type=edge.type.value,
            source_edge_id=edge.id,
            replacement_edge_id=replacement_id,
            before_props_json=before_props,
            after_props_json=props.model_dump_json(),
            action=action,
            decision_log_id=decision_id,
        )
        if old_override is not None:
            queries.link_override_chain(self._conn, old_override, override_id)
        queries.retract_edge(self._conn, edge.id)
        canon_version = self._bump_canon(project_id)
        current = queries.fetch_edge(self._conn, replacement_id)
        view = self._edge_view(project_id, current)
        return CanonEdgeEditResult(
            edge_id=current.id,
            replacement_edge_id=current.id,
            canon_version=canon_version,
            retracted=False,
            view=view,
        )

    def _patch_edge_props(
        self,
        project_id: str,
        edge: Edge,
        slot_key: str,
        props: EdgeProps,
        action: str,
    ) -> CanonEdgeEditResult:
        """identity 不变：先 append before/after override，再只更新 props 投影。"""
        before_props = edge.props.model_dump_json()
        old_override = queries.supersede_active_override(self._conn, project_id, slot_key)
        decision_id = self._append_canon_edge_decision(
            project_id, edge, action=action, before_props=before_props,
            after_props=props.model_dump_json(),
        )
        override_id = new_id(EntityType.EDGE, project_id)
        queries.insert_canon_override(
            self._conn,
            override_id=override_id,
            project_id=project_id,
            slot_key=slot_key,
            edge_type=edge.type.value,
            source_edge_id=edge.id,
            replacement_edge_id=edge.id,
            before_props_json=before_props,
            after_props_json=props.model_dump_json(),
            action=action,
            decision_log_id=decision_id,
        )
        if old_override is not None:
            queries.link_override_chain(self._conn, old_override, override_id)
        queries.update_edge_props_only(self._conn, edge.id, props.model_dump_json())
        canon_version = self._bump_canon(project_id)
        current = queries.fetch_edge(self._conn, edge.id)
        view = self._edge_view(project_id, current)
        return CanonEdgeEditResult(
            edge_id=current.id,
            replacement_edge_id=current.id,
            canon_version=canon_version,
            retracted=False,
            view=view,
        )

    def _edge_view(self, project_id: str, edge: Edge) -> CanonEdgeView:
        override = queries.active_canon_override_for_edge(self._conn, project_id, edge.id)
        return CanonEdgeView(
            edge_id=edge.id,
            edge_type=edge.type,
            src=edge.src,
            dst=edge.dst,
            props=edge.props,
            valid_from_chapter=edge.valid_from_chapter,
            source=edge.source,
            evidence_id=edge.evidence_id,
            evidence_status=edge.evidence_status,
            author_owned=override is not None,
            slot_key=queries.canon_edge_slot_key(edge),
            canon_version=self._require_canon_version(project_id),
        )

    def _require_canon_version(self, project_id: str) -> int:
        row = self._conn.execute(
            "SELECT canon_version FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise CanonEdgeRefused(f"project {project_id} 不存在")
        return int(row["canon_version"])

    def _bump_canon(self, project_id: str) -> int:
        row = self._conn.execute(
            "UPDATE project SET canon_version = canon_version + 1 "
            "WHERE id = ? RETURNING canon_version",
            (project_id,),
        ).fetchone()
        return int(row["canon_version"])

    def _append_canon_edge_decision(
        self,
        project_id: str,
        edge: Edge,
        *,
        action: str,
        before_props: str,
        after_props: str | None,
    ) -> str:
        from .. import decisions

        kind = (
            decisions.DecisionKind.CANON_EDGE_EDIT
            if action != "RETRACT"
            else decisions.DecisionKind.CANON_EDGE_RETRACT
        )
        return decisions.append(
            self._conn,
            project_id=project_id,
            kind=kind,
            decision=decisions.Verdict.ACCEPT,
            subject_name=edge.id,
            chapter_number=edge.valid_from_chapter,
            payload={
                "edge_id": edge.id,
                "edge_type": edge.type.value,
                "src": edge.src,
                "dst": edge.dst,
                "valid_from_chapter": edge.valid_from_chapter,
                "before_props_json": before_props,
                "after_props_json": after_props,
            },
        ).id

    def current_chapter_id(self, project_id: str, number: int) -> str | None:
        row = queries.find_chapter_by_number(self._conn, project_id, number)
        return row.id if row is not None else None

    def current_chapter_hash(self, project_id: str, number: int) -> str | None:
        row = queries.find_chapter_by_number(self._conn, project_id, number)
        return row.text_sha256 if row is not None else None

    def current_chapter_generation(self, project_id: str, number: int) -> int | None:
        row = queries.find_chapter_by_number(self._conn, project_id, number)
        return row.snapshot_generation if row is not None else None

    def current_snapshots(self, project_id: str) -> list[ChapterText]:
        return queries.current_snapshots(self._conn, project_id)

    def chapter_snapshots(self, project_id: str, number: int) -> list[ChapterSnapshot]:
        return queries.chapter_snapshots(self._conn, project_id, number)

    def delete_chapter_snapshot(self, project_id: str, number: int, snapshot_id: str) -> None:
        # 查引用和删必须在同一个事务里：中间隔着一次声明的话，检查过的 usage=0 到 DELETE
        # 那一刻已经不成立，于是外键在最后一步才炸——报出来的是 IntegrityError 不是 SnapshotInUse。
        with _transaction(self._conn):
            ctx = queries.snapshot_context(self._conn, snapshot_id)
            if ctx is None:
                raise StoreError(f"快照不存在：{snapshot_id}")
            if ctx.project_id != project_id:
                # `chapter_snapshot` 表里没有 project_id 列，schema 拦不住跨项目引用
                # （同 put_evidence），只能在这一层收口。
                raise StoreError(
                    f"快照 {snapshot_id} 属于项目 {ctx.project_id}，不是 {project_id}"
                )
            if ctx.chapter_number != number:
                raise StoreError(
                    f"快照 {snapshot_id} 属于第 {ctx.chapter_number} 章，不是第 {number} 章"
                )
            # 「是不是当前那条」问 chapter_snapshots，不在这儿重算一遍 sha 等值：
            # 那个判据（`s.text_sha256 = c.text_sha256` 精确等值，不是「最新那条」）
            # 全系统只有它一处定义，抄第二份就迟早和它漂开。
            snaps = queries.chapter_snapshots(self._conn, project_id, number)
            if any(s.snapshot_id == snapshot_id and s.is_current for s in snaps):
                raise SnapshotIsCurrent(f"快照 {snapshot_id} 是第 {number} 章当前正文对应的那条")
            usage = queries.snapshot_usage(self._conn, snapshot_id)
            if not usage.is_free():
                raise SnapshotInUse(usage)
            queries.delete_snapshot(self._conn, snapshot_id)

    def node_usage(self, project_id: str, node_id: str) -> NodeUsage:
        self._require_node(project_id, node_id, what="node_id")
        return queries.node_usage(self._conn, project_id, node_id)

    def delete_node(self, project_id: str, node_id: str) -> NodeUsage:
        # 数引用和删在**同一个事务**里：`usage` 是回执要用的信息，
        # 不再是「删不删得掉」的判据——2026-08-28 起挂着关系/情节也直接删，
        # 调用方（`api/characters.py`）负责在这同一个事务外那一层给受影响的事件
        # 挂通知（`event_cast_changed`）。
        with _transaction(self._conn):
            self._require_node(project_id, node_id, what="node_id")
            usage = queries.node_usage(self._conn, project_id, node_id)
            queries.delete_node(self._conn, node_id)
            return usage

    def rename_node(self, project_id: str, node_id: str, name: str) -> Node:
        # 改名 = 改两处：`node.name`（显示真相）和 canonical 别名的 surface
        # （`mentions.py` 那条 alternation 编的是别名表）。**同一个事务**，
        # 调用方没有机会只改一半 —— 只改前者，正文里叫新名字的地方就再也匹配不到他。
        with _transaction(self._conn):
            node = self._require_node(project_id, node_id, what="node_id")
            new_name = name.strip()
            if not new_name:
                raise StoreError("新名字不能是空白")
            if new_name == node.name:
                return node
            clash = [
                found
                for found in queries.find_node_by_name(
                    self._conn, project_id, node.label, new_name
                )
                if found.id != node_id
            ]
            if clash:
                # 幂等键 `(project_id, label, name)` 撞了。放行的后果是 `resolve` 返回
                # 两个 hit ⇒ `Resolution.ambiguous` ⇒ `usable_for_rules` 为假 ⇒
                # **面板上整行消失**，而没有任何一步会报错（同 `upsert_node` 那条论证）。
                raise StoreError(
                    f"项目 {project_id} 里已经有一个叫「{new_name}」的"
                    f"{node.label.value}（{clash[0].id}）：两个同名的会让称呼变成歧义，"
                    "而歧义在面板上是整行消失"
                )
            renamed = queries.update_node_name(self._conn, node_id, new_name)
            if node.label in CANONICAL_ALIAS_LABELS:
                queries.update_canonical_alias_surface(self._conn, node_id, new_name)
            return renamed


    def get_evidence(self, project_id: str, evidence_id: str) -> Evidence | None:
        try:
            ev = queries.fetch_evidence(self._conn, evidence_id)
        except LookupError:
            return None
        # 跨项目引用当作不存在：evidence 的两个指针都不带 project_id（见 fetch_evidence），
        # 这一层是唯一能收口的地方——别把别的项目的原文片段发出去。
        return ev if ev.project_id == project_id else None

    def put_evidence(self, spec: EvidenceSpec) -> Evidence:
        with _transaction(self._conn):
            ctx = queries.snapshot_context(self._conn, spec.chapter_snapshot_id)
            if ctx is None:
                raise StoreError(f"快照不存在：{spec.chapter_snapshot_id}")
            if ctx.project_id != spec.project_id:
                # 跨项目引用。evidence 的两个指针分别外键到 chapter_snapshot 和 chapter，
                # 两条都不带 project_id，所以 schema 拦不住这一条。
                raise StoreError(
                    f"快照 {spec.chapter_snapshot_id} 属于项目 {ctx.project_id}，"
                    f"不是 {spec.project_id}"
                )

            paras = anchor.paragraphs(ctx.text)
            sliced = (
                anchor.find_one(paras[spec.para_index], spec.quote_text, spec.occurrence_k)
                if spec.para_index < len(paras)
                else None
            )
            if sliced is None:
                # 段号越界 / 第 k 次不存在 / 那个位置上是别的字——三者是同一种失败：
                # 这个锚在这份快照上定位不到，于是没有子串可以取哈希。
                raise QuoteMismatch(
                    f"引语在第 {ctx.chapter_number} 章第 {spec.para_index} 段第 "
                    f"{spec.occurrence_k} 次的位置上不是逐字原文："
                    f"「{spec.quote_text}」。这份快照共 {len(paras)} 段。"
                    "证据的哈希只能对「从快照里切出来的那个子串」取（ADR 0006 配套第 3 条），"
                    "切不出来就不该有 evidence 行"
                )

            return queries.insert_evidence(
                self._conn,
                new_id(EntityType.EVIDENCE, spec.project_id),
                project_id=spec.project_id,
                chapter_snapshot_id=spec.chapter_snapshot_id,
                chapter_id=ctx.chapter_id,
                para_index=spec.para_index,
                occurrence_k=spec.occurrence_k,
                # 切出来的子串，**不是 spec.quote_text**。M1 精确匹配下两者逐字节相等，
                # M4 的模糊路径上不等——形状今天就对，那天才是加法。
                quote_text=sliced,
                quote_sha256=quote_hash(sliced),
            )
