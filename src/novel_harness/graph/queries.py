"""**全系统时态过滤的唯一实现**（PLAN §5.5 / §8 Day 4）。

这个文件的存在理由只有一条：闭开区间 `[valid_from, valid_to)` 的那五个条件，
**在整个仓库里只允许出现一次**。多写一次就多一个漏掉 `evidence_status != 'STALE'`
或者把 `>` 写成 `>=` 的机会，而它的产物是 `state_at` 返回两条互斥边 → 规则误报 →
M3 的「误报 <1 条/章」生死线崩。`tests/test_arch_guard.py` 拦 import，这个文件收敛 SQL。

本文件是 `graph/` 内部实现，**不是**对外接口：出参已经是 `models.py` 的类型
（`dict` / `sqlite3.Row` 在这里就死掉了），但调用方永远该走 `store.StoryGraph`。
`graph/__init__.py` 故意不再出口它。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from typing import Any, Final, NamedTuple

from .models import (
    UNDIRECTED_EDGE_TYPES,
    AliasKind,
    AuditPointer,
    ChapterSpec,
    ChapterText,
    Edge,
    EdgeProps,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    Evidence,
    EvidenceStatus,
    Exclusivity,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    RelocatePointer,
    SecretDetail,
    StoredAlias,
)

# ══════════════════════════════════════════════════════════════════════════
# 时态过滤 —— 唯一的一份
# ══════════════════════════════════════════════════════════════════════════

TEMPORAL_WHERE: Final = """
      valid_from_chapter <= :ch
  AND (valid_to_chapter IS NULL OR valid_to_chapter > :ch)
  AND information_scope = :scope
  AND status = 'ACTIVE'
  AND evidence_status != 'STALE'
"""
"""§5.5 逐字抄来的五个条件。**缺一不可，且不许在别处再写一遍。**

- `valid_to_chapter > :ch` 而不是 `>=`：闭开区间。vf=10/vt=143 时 ch143 **不命中**。
- `evidence_status != 'STALE'`：靠 `evidence_status` NOT NULL + 'NONE' 哨兵值成立。
  若那列可空，`NULL != 'STALE'` 在 SQL 三值逻辑里求值为 NULL 即假，这条 WHERE 会
  **静默丢掉每一条作者声明的无证据边**——而作者声明正是整个产品（ADR 0004）。
- `status = 'ACTIVE'`：RETRACTED = 这条事实从未成立过（同章更正）。
"""

_EDGE_COLS: Final = (
    "id, project_id, src, dst, type, props_json, "
    "valid_from_chapter, valid_to_chapter, information_scope, status, "
    "confidence, source, evidence_id, evidence_status"
)

_NODE_COLS: Final = "id, project_id, label, name, props_json"


# ══════════════════════════════════════════════════════════════════════════
# 行 → 模型
# ══════════════════════════════════════════════════════════════════════════


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    """不依赖调用方把 `conn.row_factory` 设成什么。

    store 不该去改调用方连接上的配置——db.py 的 `connect()` 是那个配置的唯一主人。
    """
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def to_node(row: dict[str, Any]) -> Node:
    return Node(
        id=row["id"],
        project_id=row["project_id"],
        label=row["label"],
        name=row["name"],
        props=NodeProps.model_validate_json(row["props_json"]),
    )


def to_edge(row: dict[str, Any]) -> Edge:
    return Edge(
        id=row["id"],
        project_id=row["project_id"],
        src=row["src"],
        dst=row["dst"],
        type=row["type"],
        props=EdgeProps.model_validate_json(row["props_json"]),
        valid_from_chapter=row["valid_from_chapter"],
        valid_to_chapter=row["valid_to_chapter"],
        information_scope=row["information_scope"],
        status=row["status"],
        confidence=row["confidence"],
        source=row["source"],
        evidence_id=row["evidence_id"],
        evidence_status=row["evidence_status"],
    )


def _in_clause(prefix: str, values: Sequence[str]) -> tuple[str, dict[str, str]]:
    """IN (...) 的具名参数展开。

    全库统一具名参数：`TEMPORAL_WHERE` 用 `:ch` / `:scope`，而 sqlite3 不允许
    具名和 qmark 混用——混了会在运行时报一个很难读的错。
    """
    keys = [f"{prefix}{i}" for i in range(len(values))]
    sql = ", ".join(f":{k}" for k in keys)
    return sql, dict(zip(keys, values, strict=True))


# ══════════════════════════════════════════════════════════════════════════
# 节点
# ══════════════════════════════════════════════════════════════════════════


def fetch_node(conn: sqlite3.Connection, project_id: str, node_id: str) -> Node | None:
    cur = conn.execute(
        f"SELECT {_NODE_COLS} FROM node WHERE project_id = :pid AND id = :nid",
        {"pid": project_id, "nid": node_id},
    )
    rows = _rows(cur)
    return to_node(rows[0]) if rows else None


def fetch_nodes(
    conn: sqlite3.Connection, project_id: str, node_ids: Collection[str]
) -> dict[str, Node]:
    ids = sorted(set(node_ids))
    if not ids:
        return {}
    placeholders, params = _in_clause("n", ids)
    cur = conn.execute(
        f"SELECT {_NODE_COLS} FROM node WHERE project_id = :pid AND id IN ({placeholders})",
        {"pid": project_id, **params},
    )
    return {r["id"]: to_node(r) for r in _rows(cur)}


def find_node_by_name(
    conn: sqlite3.Connection, project_id: str, label: NodeLabel, name: str
) -> list[Node]:
    """`upsert_node` 的幂等键 `(project_id, label, name)`。

    返回 `list` 而不是 `Node | None`：`idx_node_name` 是普通 INDEX 不是 UNIQUE，所以
    「撞出两行」在物理上是可能的，而调用方必须能把那种情况和「没找到」分开处理
    （它是个 StoreError，不是「那就再建一个」）。
    """
    cur = conn.execute(
        f"""
        SELECT {_NODE_COLS} FROM node
        WHERE project_id = :pid AND label = :label AND name = :name
        ORDER BY id
        """,
        {"pid": project_id, "label": label.value, "name": name},
    )
    return [to_node(r) for r in _rows(cur)]


def insert_node(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    project_id: str,
    label: NodeLabel,
    name: str,
    props: NodeProps,
) -> Node:
    """收散参数而不是 `NodeSpec`：Chapter 节点（`put_chapter` 建的那个）在 `NodeSpec`
    里根本构造不出来——那条 validator 是故意的，见它的理由。"""
    cur = conn.execute(
        f"""
        INSERT INTO node (id, project_id, label, name, props_json)
        VALUES (:id, :pid, :label, :name, :props)
        RETURNING {_NODE_COLS}
        """,
        {
            "id": node_id,
            "pid": project_id,
            "label": label.value,
            "name": name,
            "props": props.model_dump_json(),
        },
    )
    return to_node(_rows(cur)[0])


def update_node_props(conn: sqlite3.Connection, node_id: str, props: NodeProps) -> Node:
    cur = conn.execute(
        f"UPDATE node SET props_json = :props WHERE id = :id RETURNING {_NODE_COLS}",
        {"id": node_id, "props": props.model_dump_json()},
    )
    return to_node(_rows(cur)[0])


def update_node_name(conn: sqlite3.Connection, node_id: str, name: str) -> Node:
    """改显示名。**canonical 别名不跟着改**（`put_chapter` 是唯一调用方，而 Chapter
    节点没有 canonical 别名）。真要给人物改名，那是 M4 的别名合并，不是这里。"""
    cur = conn.execute(
        f"UPDATE node SET name = :name WHERE id = :id RETURNING {_NODE_COLS}",
        {"id": node_id, "name": name},
    )
    return to_node(_rows(cur)[0])


def insert_alias(
    conn: sqlite3.Connection,
    alias_id: str,
    *,
    project_id: str,
    node_id: str,
    surface: str,
    kind: AliasKind,
    usable_for_rules: bool,
) -> StoredAlias:
    """收散参数而不是 `AliasSpec`：canonical 别名（`upsert_node` 建的那条）在
    `AliasSpec` 里根本构造不出来——那条 validator 是故意的。"""
    conn.execute(
        """
        INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules)
        VALUES (:id, :pid, :nid, :surface, :kind, :usable)
        """,
        {
            "id": alias_id,
            "pid": project_id,
            "nid": node_id,
            "surface": surface,
            "kind": kind.value,
            "usable": int(usable_for_rules),
        },
    )
    return StoredAlias(
        id=alias_id,
        project_id=project_id,
        node_id=node_id,
        surface=surface,
        kind=kind,
        usable_for_rules=usable_for_rules,
    )


def insert_secret(
    conn: sqlite3.Connection, node_id: str, project_id: str, detail: SecretDetail
) -> None:
    """`secret` 扩展表那一行。`label` 列不传：它有 DEFAULT 'Secret' + CHECK，
    存在的唯一理由是给复合外键当锚（见 001_init.sql）。"""
    conn.execute(
        """
        INSERT INTO secret (id, project_id, description, sub_of)
        VALUES (:id, :pid, :desc, :sub_of)
        """,
        {
            "id": node_id,
            "pid": project_id,
            "desc": detail.description,
            "sub_of": detail.sub_of,
        },
    )


def secret_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    """本项目全部秘密，按 id 升序 = ULID 的创建顺序 = 作者声明顺序（ADR 0003）。

    父秘密和子事实（`sub_of`）**都会返回**：要不要折叠是面板层的判断，图层不猜。
    """
    cur = conn.execute(
        "SELECT id FROM secret WHERE project_id = :pid ORDER BY id",
        {"pid": project_id},
    )
    return [r["id"] for r in _rows(cur)]


# ══════════════════════════════════════════════════════════════════════════
# 读：时态过滤的三个消费者
# ══════════════════════════════════════════════════════════════════════════


_UNDIRECTED_SQL, _UNDIRECTED_PARAMS = _in_clause(
    "u", sorted(t.value for t in UNDIRECTED_EDGE_TYPES)
)


def out_edges_at(
    conn: sqlite3.Connection,
    project_id: str,
    node_id: str,
    chapter: int,
    scope: InformationScope,
) -> list[Edge]:
    """`state_at` 的正身。**出边 + 无向边的两端**。

    §5.5 的 SQL 只写了 `WHERE src = :node`，那是在「所有边都有向」这个前提下写的。
    `UNDIRECTED_EDGE_TYPES`（ADR 0008）打破了那个前提：RELATED_TO 按 `(min,max)` 存，
    方向是抛硬币，只查 src 会让「顾清音和萧决是什么关系」这个问题的答案取决于两个 ULID
    的字典序——一半的人物卡上关系栏凭空消失。

    **只对无向类型放开 dst**，不是对所有类型加个 OR：KNOWS 的 src 是人、dst 是秘密，
    反向查是无意义的；LOCATED_AT 反向查会让「青云城」这个节点的状态快照里冒出
    所有到过它的人。入边的正经消费者是 `subgraph(hops=1)`（`incident_edges_at`）。

    列序对齐 `idx_edge_src(project_id, src, type, valid_from_chapter)`；反向那半走
    `idx_edge_dst`。
    """
    cur = conn.execute(
        f"""
        SELECT {_EDGE_COLS} FROM edge
        WHERE project_id = :pid
          AND (src = :node OR (dst = :node AND type IN ({_UNDIRECTED_SQL})))
          AND {TEMPORAL_WHERE}
        ORDER BY type, valid_from_chapter, id
        """,
        {
            "pid": project_id,
            "node": node_id,
            "ch": chapter,
            "scope": scope.value,
            **_UNDIRECTED_PARAMS,
        },
    )
    return [to_edge(r) for r in _rows(cur)]


def knowledge_edges_at(
    conn: sqlite3.Connection,
    project_id: str,
    cast: Sequence[str],
    secrets: Sequence[str],
    chapter: int,
    scope: InformationScope,
) -> list[Edge]:
    """认知矩阵的料（§8 Day 5）：cast × secret 上的 KNOWS / BELIEVES。

    Day 5 的 SQL 用两个 LEFT JOIN 在 SQL 里拼 CASE；这里改成「一次取边、在 Python 里
    铺笛卡尔积」，因为闭世界的 UNKNOWN 格**必须被物化**（`KnowledgeMatrix` 的 validator
    会强制），而 CROSS JOIN 版把「哪些格该存在」这个断言留在了 SQL 里，测不到。
    """
    if not cast or not secrets:
        return []
    c_sql, c_params = _in_clause("c", cast)
    s_sql, s_params = _in_clause("s", secrets)
    cur = conn.execute(
        f"""
        SELECT {_EDGE_COLS} FROM edge
        WHERE project_id = :pid
          AND type IN ('KNOWS', 'BELIEVES')
          AND src IN ({c_sql}) AND dst IN ({s_sql})
          AND {TEMPORAL_WHERE}
        """,
        {"pid": project_id, "ch": chapter, "scope": scope.value, **c_params, **s_params},
    )
    return [to_edge(r) for r in _rows(cur)]


def incident_edges_at(
    conn: sqlite3.Connection,
    project_id: str,
    frontier: Collection[str],
    chapter: int,
    scope: InformationScope,
    edge_types: Collection[EdgeType] | None,
) -> list[Edge]:
    """子图的一跳展开：**出边 + 入边**（入边的消费者就是它，也是 `idx_edge_dst` 的）。

    `edge_types=None` = 不过滤。第 2 跳的调用方**必须**传类型（见 store.HOP2_EDGE_TYPES）：
    v1 的星形边是 HAS_STATE / MEMBER_OF / LOCATED_AT，放开任一条 = 2 跳返回全书。
    """
    ids = sorted(set(frontier))
    if not ids:
        return []
    if edge_types is not None and not edge_types:
        return []
    f_sql, f_params = _in_clause("f", ids)
    type_sql = ""
    type_params: dict[str, str] = {}
    if edge_types is not None:
        t_sql, type_params = _in_clause("t", sorted(t.value for t in edge_types))
        type_sql = f"AND type IN ({t_sql})"
    cur = conn.execute(
        f"""
        SELECT {_EDGE_COLS} FROM edge
        WHERE project_id = :pid
          AND (src IN ({f_sql}) OR dst IN ({f_sql}))
          {type_sql}
          AND {TEMPORAL_WHERE}
        ORDER BY id
        """,
        {"pid": project_id, "ch": chapter, "scope": scope.value, **f_params, **type_params},
    )
    return [to_edge(r) for r in _rows(cur)]


# ══════════════════════════════════════════════════════════════════════════
# 别名解析
# ══════════════════════════════════════════════════════════════════════════


def alias_rows(
    conn: sqlite3.Connection, project_id: str, surfaces: Sequence[str] | None
) -> list[dict[str, Any]]:
    """`surfaces=None` = 全项目花名册，**按 surface 长度降序**。

    降序不是审美：mentions.py 把它直接编译成正则 alternation，而 leftmost-first 的
    alternation 里长的必须排前面，否则「顾清音」会被「清音」抢先匹配掉。
    同长度按 surface 升序，让花名册在两次运行之间稳定（正则一变，全部 mention 就变）。
    """
    where = ""
    params: dict[str, Any] = {"pid": project_id}
    if surfaces is not None:
        wanted = sorted(set(surfaces))
        if not wanted:
            return []
        s_sql, s_params = _in_clause("s", wanted)
        where = f"AND a.surface IN ({s_sql})"
        params.update(s_params)
    cur = conn.execute(
        f"""
        SELECT a.surface AS surface, a.kind AS kind, a.usable_for_rules AS usable_for_rules,
               {", ".join(f"n.{c} AS {c}" for c in _NODE_COLS.split(", "))}
        FROM alias a JOIN node n ON n.id = a.node_id
        WHERE a.project_id = :pid {where}
        ORDER BY length(a.surface) DESC, a.surface ASC, n.id ASC
        """,
        params,
    )
    return _rows(cur)


# ══════════════════════════════════════════════════════════════════════════
# 写：upsert_edge 的零件（supersede 的编排在 sqlite_store.py）
# ══════════════════════════════════════════════════════════════════════════


def exclusivity_of(conn: sqlite3.Connection, edge_type: EdgeType) -> Exclusivity:
    cur = conn.execute(
        "SELECT exclusivity FROM edge_type WHERE type = :t", {"t": edge_type.value}
    )
    rows = _rows(cur)
    if not rows:
        # edge.type 对 edge_type 建的是外键，所以正常路径下这里不可能空。
        # 空了说明 001_init 的那 9 行 INSERT 没跑——让它响亮地死，别当成「没有互斥性」。
        raise LookupError(f"edge_type 表里没有 {edge_type}：001_init.sql 的 9 行种子数据没进库")
    return Exclusivity(rows[0]["exclusivity"])


def find_by_identity(conn: sqlite3.Connection, spec: EdgeSpec) -> Edge | None:
    """幂等键 `(project_id, src, dst, type, valid_from_chapter, information_scope)`。

    就是 `idx_edge_identity` 那个唯一索引（§2.2a「幂等变成一个唯一索引」）。
    含 `information_scope` 是故意的：抽取器的 PROVISIONAL 行在物理上碰不到作者的 CANON 行。
    """
    cur = conn.execute(
        f"""
        SELECT {_EDGE_COLS} FROM edge
        WHERE project_id = :pid AND src = :src AND dst = :dst AND type = :type
          AND valid_from_chapter = :vf AND information_scope = :scope
        """,
        {
            "pid": spec.project_id,
            "src": spec.src,
            "dst": spec.dst,
            "type": spec.type.value,
            "vf": spec.valid_from_chapter,
            "scope": spec.information_scope.value,
        },
    )
    rows = _rows(cur)
    return to_edge(rows[0]) if rows else None


def find_conflicts(
    conn: sqlite3.Connection, spec: EdgeSpec, exclusivity: Exclusivity
) -> list[Edge]:
    """按 exclusivity 找会与新边**区间重叠**的旧边。三条约束，每条都是必需的：

    1. `information_scope = spec.information_scope`（**硬要求**）：跨层 supersede
       会让抽取器的 PROVISIONAL 边去闭合作者的 CANON 边 = Agent 直接改 Canon =
       原则 5 静默破掉。
    2. `status = 'ACTIVE'`：RETRACTED 的边从未成立过，没有区间可闭合。
    3. `valid_to_chapter IS NULL OR valid_to_chapter > :vf`（**契约没写，但缺了就是 bug**）：
       一条已被闭合的 `[10,143)` 与新边 `[151,∞)` 根本不重叠，它是「后来他又走了」的
       正常历史。少了这个条件，下面的 `old.vf < new.vf` 分支会把它重写成 `[10,151)`，
       凭空把人物在 143–150 章塞回青云城——那正是这套机制要防的重叠/错位事实。

    **这里故意没有「反向再搜一遍 (dst,src)」。** 无向边（RELATED_TO）的两个方向在
    `EdgeSpec` 的构造函数里就已经塌缩成同一个 `(min,max)`（ADR 0008），所以 `spec.src` /
    `spec.dst` 天然就是规范化后的那一对，一次搜索就够。反过来说：**如果哪天有人把
    规范化去掉，这个函数会静默地只闭合一半**——那就是 ADR 0008 记的那条 bug。
    """
    if exclusivity is Exclusivity.MULTI:
        # MEMBER_OF / OWNS / PLANTED_IN / RESOLVED_IN：可以多条同时有效，无冲突可言。
        return []
    dst_clause = "AND dst = :dst" if exclusivity is Exclusivity.SINGLE_PER_SRC_DST else ""
    cur = conn.execute(
        f"""
        SELECT {_EDGE_COLS} FROM edge
        WHERE project_id = :pid AND src = :src AND type = :type {dst_clause}
          AND information_scope = :scope
          AND status = 'ACTIVE'
          AND (valid_to_chapter IS NULL OR valid_to_chapter > :vf)
        ORDER BY valid_from_chapter, id
        """,
        {
            "pid": spec.project_id,
            "src": spec.src,
            "dst": spec.dst,
            "type": spec.type.value,
            "scope": spec.information_scope.value,
            "vf": spec.valid_from_chapter,
        },
    )
    return [to_edge(r) for r in _rows(cur)]


def insert_edge(
    conn: sqlite3.Connection, edge_id: str, spec: EdgeSpec, evidence_status: EvidenceStatus
) -> Edge:
    conn.execute(
        """
        INSERT INTO edge (id, project_id, src, dst, type, props_json,
                          valid_from_chapter, valid_to_chapter, information_scope,
                          status, confidence, source, evidence_id, evidence_status)
        VALUES (:id, :pid, :src, :dst, :type, :props,
                :vf, NULL, :scope, 'ACTIVE', :conf, :source, :ev, :evs)
        """,
        {
            "id": edge_id,
            "pid": spec.project_id,
            "src": spec.src,
            "dst": spec.dst,
            "type": spec.type.value,
            "props": spec.props.model_dump_json(),
            "vf": spec.valid_from_chapter,
            "scope": spec.information_scope.value,
            "conf": spec.confidence,
            "source": spec.source.value,
            "ev": spec.evidence_id,
            "evs": evidence_status.value,
        },
    )
    return fetch_edge(conn, edge_id)


def update_edge_facets(
    conn: sqlite3.Connection, edge_id: str, spec: EdgeSpec, evidence_status: EvidenceStatus
) -> Edge:
    """撞上幂等键时**只**改这五列。

    `valid_to_chapter` / `status` 不在这里是全部要点：M4 的抽取后台批跑 + 断点续跑会
    重复 upsert 同一条边，若这里把 `valid_to` 重置成 NULL，一条已闭合的旧边会复活成
    `[88,∞)` 与 `[120,∞)` 重叠 → state_at 返两条互斥边。见 store.upsert_edge 的契约。
    """
    conn.execute(
        """
        UPDATE edge SET props_json = :props, confidence = :conf, source = :source,
                        evidence_id = :ev, evidence_status = :evs
        WHERE id = :id
        """,
        {
            "id": edge_id,
            "props": spec.props.model_dump_json(),
            "conf": spec.confidence,
            "source": spec.source.value,
            "ev": spec.evidence_id,
            "evs": evidence_status.value,
        },
    )
    return fetch_edge(conn, edge_id)


def close_edge(conn: sqlite3.Connection, edge_id: str, valid_to: int) -> Edge:
    """闭合：`valid_to_chapter = new.valid_from`。DB 的 CHECK 会拒掉空区间。"""
    conn.execute(
        "UPDATE edge SET valid_to_chapter = :vt WHERE id = :id",
        {"id": edge_id, "vt": valid_to},
    )
    return fetch_edge(conn, edge_id)


def retract_edge(conn: sqlite3.Connection, edge_id: str) -> Edge:
    """同章更正：闭合成 `[151,151)` 会被 DB 的 CHECK 拒（空区间 = 从未成立），
    正确表达只能是撤回。这就是 EdgeStatus 需要 RETRACTED 的唯一理由。"""
    conn.execute(
        "UPDATE edge SET status = :st WHERE id = :id",
        {"id": edge_id, "st": EdgeStatus.RETRACTED.value},
    )
    return fetch_edge(conn, edge_id)


def fetch_edge(conn: sqlite3.Connection, edge_id: str) -> Edge:
    cur = conn.execute(f"SELECT {_EDGE_COLS} FROM edge WHERE id = :id", {"id": edge_id})
    rows = _rows(cur)
    if not rows:
        raise LookupError(f"edge 不存在：{edge_id}")
    return to_edge(rows[0])


# ══════════════════════════════════════════════════════════════════════════
# 章节 / 快照 / 证据
#
# 这三张表**不在** tests/test_arch_guard.py 的 GRAPH_TABLES 名单里，也就是说守卫
# 允许在 graph/ 之外对它们写 SQL。它们仍然放在这里，理由与守卫无关：
#   chapter.id 走 `REFERENCES node(id, project_id, label)` 复合外键 ⇒ 落一章 = 先建一个
#   Chapter 节点 ⇒ 两者必须在**一个事务**里。把它放到 graph/ 外面，就把「Chapter 节点和
#   chapter 行同生」变成了调用方的纪律——而一个没有 chapter 行的 Chapter 节点没有
#   number，number 是 state_at 的全序键。
#   且 `secret` 在名单里、`chapter` 不在，而两者的 schema 形状一模一样（扩展表、
#   主键 = node.id、复合外键连 label）。两个同形的东西走两条规矩 = 下一个贡献者只能靠猜。
# ══════════════════════════════════════════════════════════════════════════


class ChapterRow(NamedTuple):
    """`chapter` 表那一行（不含快照）。

    NamedTuple 而不是 Pydantic：它**不越过 StoryGraph 接口**（`put_chapter` 出的是
    `StoredChapter`），是 graph/ 内部的中间量。「dict / sqlite3.Row 禁止越界」那条
    在这里已经满足了——行在这个文件里就死掉了。
    """

    id: str
    number: int
    title: str
    path: str
    text_sha256: str


class SnapshotContext(NamedTuple):
    """一条快照连它所属章节的身份。`put_evidence` 靠它把两个指针钉在同一章上。"""

    text: str
    chapter_id: str
    chapter_number: int
    project_id: str


def find_chapter_by_number(
    conn: sqlite3.Connection, project_id: str, number: int
) -> ChapterRow | None:
    """`put_chapter` 的幂等键 `(project_id, number)` —— schema 的 UNIQUE。"""
    cur = conn.execute(
        """
        SELECT id, number, title, path, text_sha256 FROM chapter
        WHERE project_id = :pid AND number = :number
        """,
        {"pid": project_id, "number": number},
    )
    rows = _rows(cur)
    return ChapterRow(**rows[0]) if rows else None


def insert_chapter(conn: sqlite3.Connection, chapter_id: str, spec: ChapterSpec, sha: str) -> None:
    """`sha` 由调用方现算（`put_chapter` 用 `decisions.quote_hash(spec.text)`）——
    `ChapterSpec` 里没有这个字段，见那份 docstring。"""
    conn.execute(
        """
        INSERT INTO chapter (id, project_id, number, title, path, text_sha256)
        VALUES (:id, :pid, :number, :title, :path, :sha)
        """,
        {
            "id": chapter_id,
            "pid": spec.project_id,
            "number": spec.number,
            "title": spec.title,
            "path": spec.path,
            "sha": sha,
        },
    )


def update_chapter(conn: sqlite3.Connection, chapter_id: str, spec: ChapterSpec, sha: str) -> None:
    """`nh sync` 的落点：作者在自己的编辑器里改了这一章。

    `number` 不在这里——它是幂等键，改它就是换一章。`updated_at` 显式重写：它的
    DEFAULT 只在 INSERT 时生效。
    """
    conn.execute(
        """
        UPDATE chapter
           SET title = :title, path = :path, text_sha256 = :sha,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE id = :id
        """,
        {"id": chapter_id, "title": spec.title, "path": spec.path, "sha": sha},
    )


def find_snapshot(conn: sqlite3.Connection, chapter_id: str, sha: str) -> str | None:
    """按 `UNIQUE(chapter_id, text_sha256)` 去重：快照是证据的锚，不是版本历史，
    同内容只需要存在一次。"""
    cur = conn.execute(
        "SELECT id FROM chapter_snapshot WHERE chapter_id = :cid AND text_sha256 = :sha",
        {"cid": chapter_id, "sha": sha},
    )
    rows = _rows(cur)
    return str(rows[0]["id"]) if rows else None


def insert_snapshot(
    conn: sqlite3.Connection, snapshot_id: str, chapter_id: str, text: str, sha: str
) -> str:
    conn.execute(
        """
        INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256)
        VALUES (:id, :cid, :text, :sha)
        """,
        {"id": snapshot_id, "cid": chapter_id, "text": text, "sha": sha},
    )
    return snapshot_id


def current_snapshots(conn: sqlite3.Connection, project_id: str) -> list[ChapterText]:
    """每一章的**当前**快照，按章号升序。

    判据是 `s.text_sha256 = c.text_sha256` 的**精确等值**。写成
    `ORDER BY s.created_at DESC LIMIT 1` 是错的：那是在「哪条快照是当前的」这件事上猜，
    而 `chapter.text_sha256` 已经把答案写在那儿了。两者在正常路径上恰好同解，所以猜错
    不会有任何症状——直到作者把一章改回它上一个版本（快照按内容去重、不新建，于是
    「最新的那条」指向的是那份已经被改掉的正文），此时定位出来的证据锚在旧文本上。
    """
    cur = conn.execute(
        """
        SELECT c.id AS chapter_id, c.number AS number, s.id AS snapshot_id, s.text AS text
        FROM chapter c
        JOIN chapter_snapshot s
          ON s.chapter_id = c.id AND s.text_sha256 = c.text_sha256
        WHERE c.project_id = :pid
        ORDER BY c.number
        """,
        {"pid": project_id},
    )
    return [ChapterText(**r) for r in _rows(cur)]


def snapshot_context(conn: sqlite3.Connection, snapshot_id: str) -> SnapshotContext | None:
    cur = conn.execute(
        """
        SELECT s.text AS text, c.id AS chapter_id, c.number AS chapter_number,
               c.project_id AS project_id
        FROM chapter_snapshot s JOIN chapter c ON c.id = s.chapter_id
        WHERE s.id = :sid
        """,
        {"sid": snapshot_id},
    )
    rows = _rows(cur)
    return SnapshotContext(**rows[0]) if rows else None


_EVIDENCE_COLS: Final = (
    "e.id AS id, e.project_id AS project_id, "
    "e.chapter_snapshot_id AS chapter_snapshot_id, e.para_index AS para_index, "
    "e.quote_text AS quote_text, e.quote_sha256 AS quote_sha256, "
    "e.chapter_id AS chapter_id, e.para_index_hint AS para_index_hint, "
    "e.occurrence_k AS occurrence_k, c.number AS chapter_number"
)


def to_evidence(row: dict[str, Any]) -> Evidence:
    """`chapter_number` 在 evidence 表里**没有对应列**——它由 JOIN chapter 填。

    这不是遗漏：它是 `Evidence` 作为出参对调用方的承诺，而声明层正是靠 `ev.chapter_number`
    写 `valid_from`（§5.9：作者永不填章号，章号由证据决定）。存一份在 evidence 行里
    则是把 `chapter.number` 抄第二遍——同名字段存两处就需要一个同步器。
    """
    return Evidence(
        id=row["id"],
        project_id=row["project_id"],
        chapter_number=row["chapter_number"],
        audit=AuditPointer(
            chapter_snapshot_id=row["chapter_snapshot_id"],
            para_index=row["para_index"],
            quote_text=row["quote_text"],
            quote_sha256=row["quote_sha256"],
        ),
        relocate=RelocatePointer(
            chapter_id=row["chapter_id"],
            quote_sha256=row["quote_sha256"],
            para_index_hint=row["para_index_hint"],
            occurrence_k=row["occurrence_k"],
        ),
    )


def insert_evidence(
    conn: sqlite3.Connection,
    evidence_id: str,
    *,
    project_id: str,
    chapter_snapshot_id: str,
    chapter_id: str,
    para_index: int,
    occurrence_k: int,
    quote_text: str,
    quote_sha256: str,
) -> Evidence:
    """两个指针一次落下。**写入这一刻 `para_index_hint == para_index`**：审计的那个
    指向不可变快照、永不更新；重定位的那个跟着作者改稿漂（M4 的 relocate 就地更新它）。

    `quote_text` / `quote_sha256` 收的必须是**从快照里切出来的那个子串**和它的哈希，
    不是调用方传进来的串（ADR 0006 配套第 3 条）——`put_evidence` 是唯一的调用方，
    这条在那里被 `EvidenceSpec` 的形状钉死。
    """
    conn.execute(
        """
        INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index,
                              quote_text, quote_sha256,
                              chapter_id, para_index_hint, occurrence_k)
        VALUES (:id, :pid, :sid, :para, :quote, :sha, :cid, :para, :k)
        """,
        {
            "id": evidence_id,
            "pid": project_id,
            "sid": chapter_snapshot_id,
            "para": para_index,
            "quote": quote_text,
            "sha": quote_sha256,
            "cid": chapter_id,
            "k": occurrence_k,
        },
    )
    return fetch_evidence(conn, evidence_id)


def fetch_evidence(conn: sqlite3.Connection, evidence_id: str) -> Evidence:
    cur = conn.execute(
        f"""
        SELECT {_EVIDENCE_COLS}
        FROM evidence e JOIN chapter c ON c.id = e.chapter_id
        WHERE e.id = :id
        """,
        {"id": evidence_id},
    )
    rows = _rows(cur)
    if not rows:
        raise LookupError(f"evidence 不存在：{evidence_id}")
    return to_evidence(rows[0])
