"""`GraphStore`（= `StoryGraph` + `CanonWriter`）的 SQLite 实现（PLAN §8 Day 4）。

分工：**SQL 在 `queries.py`，编排在这里。** 这个文件里一条时态过滤都没有——
它只做五件 SQL 做不了的事：入参校验（scope / hops / NodeNotFound）、事务边界、
supersede 的分支决定、把边投影成 `StateSnapshot` / `KnowledgeMatrix` / `Subgraph`，
以及写入侧那些「必须同生」的组合（节点 + canonical 别名 + secret 行 / 章节 + 快照）。

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
from typing import Final

from ..decisions import quote_hash
from ..ids import EntityType, new_id
from ..text import anchor
from . import queries
from .models import (
    CANONICAL_ALIAS_LABELS,
    AliasHit,
    AliasKind,
    AliasSpec,
    ChapterSpec,
    ChapterSnapshot,
    ChapterText,
    Edge,
    EdgeSpec,
    EdgeType,
    Evidence,
    EvidenceSpec,
    EvidenceStatus,
    InformationScope,
    KnowledgeCell,
    KnowledgeMatrix,
    KnowledgeState,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
    Resolution,
    StateSnapshot,
    StateValue,
    StoredAlias,
    StoredChapter,
    Subgraph,
    UpsertResult,
)
from .store import (
    HOP2_EDGE_TYPES,
    MAX_HOPS,
    MAX_SUBGRAPH_NODES,
    QUERYABLE_SCOPES,
    NodeNotFound,
    QuoteMismatch,
    SnapshotInUse,
    SnapshotIsCurrent,
    StoreError,
    SupersedeConflict,
)

MIN_RULE_SURFACE_LEN: Final = 2
"""canonical 别名的 `usable_for_rules` 阈值 —— `alias` 表那条
`CHECK (usable_for_rules = 0 OR length(surface) >= 2)` 在应用层的同一个数。

它在这里的**唯一**用途是让 1 字名的人物（真书里有）建得出节点：不判这一下，
`upsert_node` 会拿 `usable_for_rules=1` 去撞那条 CHECK，于是**建节点整个失败**。
schema 的立场是「短 surface 可以存在，只是不许被规则拿去匹配正文」（ADR 0004：
「音」「决」去正文里匹配 = 满篇误报），不是「1 字名的人不许进这本书」。
"""


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
                "PLANNED 的唯一出口是 panel/constraints.py 转译成 must_not_reveal / "
                "forbidden_entities，REJECTED 只是防重抽的坟场"
            )

    @staticmethod
    def _check_chapter(chapter: int) -> None:
        """章号从 1 起。**读路径也要校验，不能只校验写路径。**

        写侧这条不变量是硬的（`EdgeSpec.valid_from_chapter` 是 `Field(ge=1)`，chapter 表是
        `CHECK(number >= 1)`），读侧却收 0 / 负数照单全收，然后静默返回一个语义上不可能
        存在的答案：`knowledge_matrix(pid, 0, cast)` 给出一个格格 UNKNOWN 的**完整**矩阵，
        而闭世界推导下 UNKNOWN 是一个**断言**（models.py：「这不是「查不到」，是一个断言」）——
        于是面板理直气壮地告诉作者「在场三个人对全部秘密一无所知」，而不是承认这个问题问错了。

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
            # 花名册：SQL 已按长度降序排好，直接可喂 alternation 编译。
            ordered = [Resolution(surface=s, hits=h) for s, h in by_surface.items()]
        else:
            # 与入参**一一对应且同序**，重复的 surface 也各返回一条。解析不到的返回
            # hits=[]，不许静默丢——否则调用方分不清「没这个人」和「我没问过这个人」。
            ordered = [Resolution(surface=s, hits=by_surface.get(s, [])) for s in surfaces]

        if rules_only:
            return [r for r in ordered if r.usable_for_rules]
        return ordered

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
        dsts = queries.fetch_nodes(self._conn, project_id, [e.dst for e in edges])

        def _dst(edge: Edge) -> Node:
            n = dsts.get(edge.dst)
            if n is None:
                # 跨项目误引用。ULID 的 project_short 前缀就是为了让它在日志里一眼可见。
                raise StoreError(f"边 {edge.id} 的 dst {edge.dst} 不在项目 {project_id} 里")
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
        return StateSnapshot(
            node=node,
            chapter=chapter,
            scope=scope,
            edges=edges,
            location=_dst(located[0]) if located else None,
            states=states,
        )

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

    # ── knowledge_matrix ──────────────────────────────────────────────────

    def knowledge_matrix(
        self,
        project_id: str,
        chapter: int,
        cast: Sequence[str],
        *,
        secrets: Sequence[str] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> KnowledgeMatrix:
        self._check_scope(scope)
        self._check_chapter(chapter)
        _reject_dups(cast, "cast")
        characters = [self._require_node(project_id, cid, what="cast") for cid in cast]

        if secrets is None:
            secret_list = queries.secret_ids(self._conn, project_id)
        else:
            _reject_dups(secrets, "secrets")
            secret_list = list(secrets)
        secret_nodes = [self._require_node(project_id, sid, what="secret") for sid in secret_list]
        for n in secret_nodes:
            if n.label is not NodeLabel.SECRET:
                raise ValueError(f"secrets 只接受 label=Secret 的节点：{n.id} 是 {n.label}")

        edges = queries.knowledge_edges_at(
            self._conn, project_id, [n.id for n in characters], secret_list, chapter, scope
        )
        found: dict[tuple[str, str, EdgeType], Edge] = {}
        for e in edges:
            key = (e.src, e.dst, e.type)
            if key in found:
                # (人, 秘密) 的 exclusivity 是 single_per_src_dst，同一章两条 = supersede
                # 漏了。面板上「他既知道又不知道」是错误答案，让它炸。
                raise StoreError(
                    f"({e.src}, {e.dst}, {e.type}) 在第 {chapter} 章有两条有效边"
                    f"（{found[key].id} / {e.id}）：supersede 漏了"
                )
            found[key] = e

        cells: list[KnowledgeCell] = []
        for c in characters:
            for s in secret_nodes:
                # KNOWS 压 BELIEVES（§8 Day 5 的 CASE WHEN 顺序）：真知道了就不再是错误认知。
                k = found.get((c.id, s.id, EdgeType.KNOWS))
                b = found.get((c.id, s.id, EdgeType.BELIEVES))
                hit = k or b
                if hit is None:
                    # 闭世界：无边 ⇒ 不知道。UNKNOWN 格**必须物化**——「没有这一格」和
                    # 「他不知道」是两个意思，面板上少一格 = 作者以为系统没意见 = 说漏嘴。
                    cells.append(
                        KnowledgeCell(
                            character_id=c.id, secret_id=s.id, state=KnowledgeState.UNKNOWN
                        )
                    )
                    continue
                cells.append(
                    KnowledgeCell(
                        character_id=c.id,
                        secret_id=s.id,
                        state=(
                            KnowledgeState.KNOWS if hit is k else KnowledgeState.BELIEVES
                        ),
                        since_chapter=hit.valid_from_chapter,
                        believed_value=hit.props.believed_value if hit is b else None,
                        evidence_id=hit.evidence_id,
                    )
                )
        return KnowledgeMatrix(
            project_id=project_id,
            chapter=chapter,
            scope=scope,
            # 窄引用：矩阵是 D 分区的料，整份序列化进 prompt，而 Secret 节点的 props 里
            # 装的就是秘密的内容（见 NodeRef 的论证）。这里传 Node 会被 pydantic 拒——
            # 那是故意的：忘记收窄要当场炸，不能靠纪律。
            characters=[NodeRef.of(n) for n in characters],
            secrets=[NodeRef.of(n) for n in secret_nodes],
            cells=cells,
        )

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

        truncated = len(seen_ids) > MAX_SUBGRAPH_NODES
        kept_ids = seen_ids[:MAX_SUBGRAPH_NODES]  # center 永远在第 0 位
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
            conflicts = queries.find_conflicts(self._conn, spec, exclusivity)

            # 先把乱序全部检出来再动手：事务回滚兜得住，但「先炸再改」让失败路径
            # 不依赖回滚的正确性。
            for old in conflicts:
                if old.valid_from_chapter > spec.valid_from_chapter:
                    raise SupersedeConflict(
                        f"乱序插入：已有 {old.type} 边 {old.id} 的 valid_from="
                        f"{old.valid_from_chapter} 晚于新边的 {spec.valid_from_chapter}。"
                        "v1 的 supersede 只进不退（§5.9：valid_from 由证据决定、证据按章推进），"
                        "正确处理它得先回答「先前那条事实在后一条结束后要不要恢复」——"
                        "猜错的产物是重叠区间，所以宁可抛"
                    )

            edge = queries.insert_edge(self._conn, self._new_edge_id(spec.project_id), spec, evs)
            closed: list[Edge] = []
            retracted: list[Edge] = []
            for old in conflicts:
                if old.valid_from_chapter < spec.valid_from_chapter:
                    closed.append(
                        queries.close_edge(self._conn, old.id, spec.valid_from_chapter)
                    )
                else:
                    # ==：同章更正（「他在青云城…然后去了北荒」都在 ch151）。闭合成
                    # [151,151) 是空区间，DB 的 CHECK 会拒——正确表达只能是撤回。
                    retracted.append(queries.retract_edge(self._conn, old.id))
            return UpsertResult(edge=edge, created=True, closed=closed, retracted=retracted)

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
                # 重复声明 = 更 props。**不重写 secret 行**：description / sub_of 的
                # 修改是一次独立的编辑，不是「再声明一次」的副作用。
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
            if spec.secret is not None:
                queries.insert_secret(self._conn, node.id, spec.project_id, spec.secret)
            return node

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
            )

    def put_chapter(self, spec: ChapterSpec) -> StoredChapter:
        sha = quote_hash(spec.text)
        with _transaction(self._conn):
            row = queries.find_chapter_by_number(self._conn, spec.project_id, spec.number)
            if row is None:
                chapter_id = new_id(EntityType.CHAPTER, spec.project_id)
                # Chapter 节点和 chapter 行**同生**，而且没有 canonical 别名
                # （CANONICAL_ALIAS_LABELS 里没有它）：300 章 = 300 条章标进花名册。
                queries.insert_node(
                    self._conn,
                    chapter_id,
                    project_id=spec.project_id,
                    label=NodeLabel.CHAPTER,
                    name=spec.heading,
                    props=NodeProps(),
                )
                queries.insert_chapter(self._conn, chapter_id, spec, sha)
                created = True
            else:
                chapter_id = row.id
                node = self._require_node(spec.project_id, chapter_id, what="chapter 节点")
                if node.name != spec.heading:
                    queries.update_node_name(self._conn, chapter_id, spec.heading)
                queries.update_chapter(self._conn, chapter_id, spec, sha)
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
                created=created,
                snapshot_created=snapshot_created,
            )

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


def _reject_dups(ids: Sequence[str], what: str) -> None:
    """重复 id 会让 KnowledgeMatrix 的笛卡尔积 validator 报一个读不懂的错
    （want 去重了、got 没有）。在这里拦，给调用方一句人话。"""
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if list(ids).count(i) > 1})
        raise ValueError(f"{what} 有重复 id：{dup}")
