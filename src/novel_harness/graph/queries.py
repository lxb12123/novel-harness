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

from ..events.models import EventCharacterRole, EventView, StoryEvent
from .models import (
    MIN_RULE_SURFACE_LEN,
    UNDIRECTED_EDGE_TYPES,
    AliasKind,
    AuditPointer,
    ChapterSnapshot,
    ChapterSpec,
    ChapterText,
    ChapterUsage,
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
    NodeRef,
    NodeUsage,
    RelocatePointer,
    RetirementReport,
    SnapshotUsage,
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

_EVENT_COLS: Final = (
    "id, project_id, chapter_number, "
    "COALESCE((SELECT v.summary FROM event_summary_head h "
    "          JOIN event_summary_version v ON v.id = h.current_version_id "
    "          WHERE h.event_id = story_event.id), summary) AS summary, "
    "information_scope, status, confidence, source, evidence_id, evidence_status, "
    "derived_from_event_id"
)


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


def to_story_event(row: dict[str, Any]) -> StoryEvent:
    return StoryEvent(
        id=row["id"],
        project_id=row["project_id"],
        chapter_number=row["chapter_number"],
        summary=row["summary"],
        information_scope=row["information_scope"],
        status=row["status"],
        confidence=row["confidence"],
        source=row["source"],
        evidence_id=row["evidence_id"],
        evidence_status=row["evidence_status"],
        derived_from_event_id=row["derived_from_event_id"],
    )


def _in_clause(prefix: str, values: Sequence[str]) -> tuple[str, dict[str, str]]:
    """IN (...) 的具名参数展开。

    全库统一具名参数：`TEMPORAL_WHERE` 用 `:ch` / `:scope`，而 sqlite3 不允许
    具名和 qmark 混用——混了会在运行时报一个很难读的错。
    """
    keys = [f"{prefix}{i}" for i in range(len(values))]
    sql = ", ".join(f":{k}" for k in keys)
    return sql, dict(zip(keys, values, strict=True))


class EventLocator(NamedTuple):
    chapter_number: int
    information_scope: InformationScope


def event_locator(
    conn: sqlite3.Connection,
    project_id: str,
    event_id: str,
) -> EventLocator | None:
    cur = conn.execute(
        """
        SELECT chapter_number, information_scope
        FROM story_event
        WHERE project_id = :pid AND id = :eid
        """,
        {"pid": project_id, "eid": event_id},
    )
    rows = _rows(cur)
    if not rows:
        return None
    return EventLocator(
        chapter_number=rows[0]["chapter_number"],
        information_scope=InformationScope(rows[0]["information_scope"]),
    )


def event_id_by_anchor(
    conn: sqlite3.Connection,
    project_id: str,
    evidence_id: str,
    scope: InformationScope,
) -> str | None:
    cur = conn.execute(
        """
        SELECT id FROM story_event
        WHERE project_id = :pid AND evidence_id = :evidence_id
          AND information_scope = :scope
        """,
        {"pid": project_id, "evidence_id": evidence_id, "scope": scope.value},
    )
    rows = _rows(cur)
    return str(rows[0]["id"]) if rows else None


def event_chapter_from_evidence_audit(
    conn: sqlite3.Connection,
    project_id: str,
    evidence_id: str,
) -> int:
    """Derive immutable event time from evidence's audit snapshot, never relocation state."""
    cur = conn.execute(
        """
        SELECT chapter.number AS chapter_number
        FROM evidence
        JOIN chapter_snapshot AS snapshot
          ON snapshot.id = evidence.chapter_snapshot_id
        JOIN chapter ON chapter.id = snapshot.chapter_id
        WHERE evidence.id = :evidence_id
          AND evidence.project_id = :pid
          AND chapter.project_id = :pid
        """,
        {"pid": project_id, "evidence_id": evidence_id},
    )
    rows = _rows(cur)
    if not rows:
        raise LookupError(
            f"evidence audit 不存在或不属于项目：project={project_id}, evidence={evidence_id}"
        )
    return int(rows[0]["chapter_number"])


def event_ids_at(
    conn: sqlite3.Connection,
    project_id: str,
    chapter: int,
    scope: InformationScope,
) -> list[str]:
    cur = conn.execute(
        f"""
        SELECT id FROM story_event
        WHERE project_id = :pid AND {TEMPORAL_WHERE}
        ORDER BY chapter_number, id
        """,
        {"pid": project_id, "ch": chapter, "scope": scope.value},
    )
    return [str(row["id"]) for row in _rows(cur)]


def event_ids_for_characters_at(
    conn: sqlite3.Connection,
    project_id: str,
    character_ids: Collection[str],
    chapter: int,
    scope: InformationScope,
) -> list[str]:
    ids = sorted(set(character_ids))
    if not ids:
        return []
    placeholders, id_params = _in_clause("character", ids)
    cur = conn.execute(
        f"""
        WITH visible_event AS (
            SELECT * FROM story_event
            WHERE project_id = :pid AND {TEMPORAL_WHERE}
        ),
        visible_knower AS (
            SELECT * FROM event_knower
            WHERE project_id = :pid AND {TEMPORAL_WHERE}
        )
        SELECT event.id AS id
        FROM visible_event AS event
        WHERE EXISTS (
            SELECT 1 FROM event_participant AS participant
            WHERE participant.event_id = event.id
              AND participant.status = 'ACTIVE'
              AND participant.character_id IN ({placeholders})
        ) OR EXISTS (
            SELECT 1 FROM visible_knower AS knower
            WHERE knower.event_id = event.id
              AND knower.character_id IN ({placeholders})
        )
        ORDER BY event.chapter_number, event.id
        """,
        {"pid": project_id, "ch": chapter, "scope": scope.value, **id_params},
    )
    return [str(row["id"]) for row in _rows(cur)]


def event_views_at(
    conn: sqlite3.Connection,
    project_id: str,
    event_ids: Collection[str],
    chapter: int,
    scope: InformationScope,
) -> list[EventView]:
    """Hydrate visible event hyperedges and their incidence into typed views."""
    ids = sorted(set(event_ids))
    if not ids:
        return []
    placeholders, id_params = _in_clause("event", ids)
    params = {"pid": project_id, "ch": chapter, "scope": scope.value, **id_params}
    event_rows = _rows(
        conn.execute(
            f"""
            SELECT {_EVENT_COLS}
            FROM story_event
            WHERE project_id = :pid
              AND {TEMPORAL_WHERE}
              AND id IN ({placeholders})
            ORDER BY chapter_number, id
            """,
            params,
        )
    )
    visible_ids = [row["id"] for row in event_rows]
    if not visible_ids:
        return []
    visible_placeholders, visible_params = _in_clause("visible", visible_ids)
    incidence_params = {
        "pid": project_id,
        "ch": chapter,
        "scope": scope.value,
        **visible_params,
    }
    incidence_rows = _rows(
        conn.execute(
            f"""
            WITH visible_knower AS (
                SELECT * FROM event_knower
                WHERE project_id = :pid AND {TEMPORAL_WHERE}
            )
            SELECT ep.event_id AS event_id, 'participant' AS role,
                   n.id AS node_id, n.label AS label, n.name AS name
            FROM event_participant AS ep
            JOIN node AS n ON n.id = ep.character_id AND n.project_id = ep.project_id
            WHERE ep.project_id = :pid AND ep.status = 'ACTIVE'
              AND ep.event_id IN ({visible_placeholders})
            UNION ALL
            SELECT ek.event_id AS event_id, 'knower' AS role,
                   n.id AS node_id, n.label AS label, n.name AS name
            FROM visible_knower AS ek
            JOIN node AS n ON n.id = ek.character_id AND n.project_id = ek.project_id
            WHERE ek.event_id IN ({visible_placeholders})
            ORDER BY event_id, role, node_id
            """,
            incidence_params,
        )
    )
    incidence: dict[str, dict[str, dict[str, NodeRef]]] = {}
    for row in incidence_rows:
        ref = NodeRef(id=row["node_id"], label=row["label"], name=row["name"])
        incidence.setdefault(row["event_id"], {}).setdefault(row["role"], {})[ref.id] = ref

    views: list[EventView] = []
    for row in event_rows:
        roles = incidence.get(row["id"], {})
        views.append(
            EventView(
                event=to_story_event(row),
                participants=list(roles.get(EventCharacterRole.PARTICIPANT, {}).values()),
                knowers=list(roles.get(EventCharacterRole.KNOWER, {}).values()),
            )
        )
    return views


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


def find_state_dim(conn: sqlite3.Connection, project_id: str, dim_key: str) -> list[Node]:
    """按 `props.dim_key` 找 StateDim。**它才是这类节点的身份**（`node.name` 是显示名，
    作者随时会把「生死」改成「健康」）。

    判据写成 `label='StateDim' AND json_extract(props_json,'$.dim_key') = :key`，
    与 `idx_state_dim_key` 那条 UNIQUE 索引**同一个表达式**——写成别的形状
    （比如先取全部 StateDim 再在 Python 里比）就用不上那条索引，而且会和索引的
    唯一性判据漂开。

    返回 `list` 而不是 `Node | None`，理由同 `find_node_by_name`：
    索引建立之前进来的行让「撞出两行」在物理上仍是可能的，而那是一个 StoreError
    （`is_dead` 的 `any()` 会让 dead 永远压过 alive），不是「那就再建一个」。
    """
    cur = conn.execute(
        f"""
        SELECT {_NODE_COLS} FROM node
        WHERE project_id = :pid
          AND label = :label
          AND json_extract(props_json, '$.dim_key') = :key
        ORDER BY id
        """,
        {"pid": project_id, "label": NodeLabel.STATE_DIM.value, "key": dim_key},
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


def merge_node_props(
    conn: sqlite3.Connection,
    node: Node,
    patch: dict[str, Any],
) -> Node:
    values = node.props.model_dump()
    values.update(patch)
    return update_node_props(conn, node.id, NodeProps.model_validate(values))


def update_node_name(conn: sqlite3.Connection, node_id: str, name: str) -> Node:
    """改显示名。**canonical 别名不跟着改。**

    ⚠️ 两个调用方，两种用法，别混：

    - `put_chapter`：Chapter 节点**没有** canonical 别名（不在 `CANONICAL_ALIAS_LABELS`
      里），所以它调这一个就够了；
    - `sqlite_store.rename_node`：花名册条目**有** canonical 别名，它在同一个事务里
      紧接着调 `update_canonical_alias_surface`。只改一个的后果是正文里叫新名字的地方
      再也匹配不到他（`mentions.py` 那条 alternation 编的是别名表，不是 node.name）。
    """
    cur = conn.execute(
        f"UPDATE node SET name = :name WHERE id = :id RETURNING {_NODE_COLS}",
        {"id": node_id, "name": name},
    )
    return to_node(_rows(cur)[0])


def update_canonical_alias_surface(
    conn: sqlite3.Connection, node_id: str, surface: str
) -> None:
    """把这个节点的 canonical 别名改成新的显示名。**只动 canonical 那一条。**

    别的别名（「凤辣子」）是作者/抽取另外登记的称呼，改本名跟它们无关——
    顺手一起改会把那些称呼抹掉，而它们是 canon（ADR 0004）。

    `usable_for_rules` 跟着新名字重算：`upsert_node` 建它的时候判据就是
    `len(name) >= 2`（schema 那条 CHECK 也是这么写的），改名之后不重算的话，
    一个从「凌」改成「凌霄」的人会永远匹配不到正文。
    """
    conn.execute(
        "UPDATE alias SET surface = :surface, usable_for_rules = :usable"
        " WHERE node_id = :id AND kind = :kind",
        {
            "id": node_id,
            "surface": surface,
            "usable": int(len(surface) >= MIN_RULE_SURFACE_LEN),
            "kind": AliasKind.CANONICAL.value,
        },
    )


def node_usage(conn: sqlite3.Connection, project_id: str, node_id: str) -> NodeUsage:
    """引擎在这个花名册条目上记了多少东西。**删它之前问这个**（见 `NodeUsage`）。

    只数两样：**关系**（`edge.src|dst`）和**情节名单**（`event_participant` /
    `event_knower`）。别名和 `summary_mention` 故意不数——理由写在 `NodeUsage` 上。

    情节两张表用 `UNION` 去重：一个人同时是在场和知情时只算一条情节，
    相加会报出一个比真实条数大的数，而那个数会被原样念给作者听
    （同 `chapter_usage` 里 `edges` 那条 `OR` 的理由）。
    """
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM edge
             WHERE project_id = :pid AND (src = :nid OR dst = :nid)) AS edges,
          (SELECT COUNT(*) FROM (
             SELECT event_id FROM event_participant
              WHERE project_id = :pid AND character_id = :nid
             UNION
             SELECT event_id FROM event_knower
              WHERE project_id = :pid AND character_id = :nid
          )) AS events,
          (SELECT name FROM node WHERE id = :nid) AS name
        """,
        {"pid": project_id, "nid": node_id},
    ).fetchone()
    return NodeUsage(
        node_id=node_id,
        name=str(row["name"] or ""),
        edges=int(row["edges"]),
        events=int(row["events"]),
    )


def delete_node(conn: sqlite3.Connection, node_id: str) -> None:
    """把这个节点从库里抹掉。别名和 `summary_mention` 跟着 CASCADE 走。

    **不检查引用**——那是调用方（`sqlite_store.delete_node`）的活，它要在同一个事务里
    先问 `node_usage`。这里真有人引着的话外键会 CASCADE（不是抛），
    所以那一步不是「最后一道」，是**唯一**一道。
    """
    conn.execute("DELETE FROM node WHERE id = :id", {"id": node_id})


def insert_alias(
    conn: sqlite3.Connection,
    alias_id: str,
    *,
    project_id: str,
    node_id: str,
    surface: str,
    kind: AliasKind,
    usable_for_rules: bool,
    source: str = "author",
    derived_from_alias_id: str | None = None,
) -> StoredAlias:
    """收散参数而不是 `AliasSpec`：canonical 别名（`upsert_node` 建的那条）在
    `AliasSpec` 里根本构造不出来——那条 validator 是故意的。

    `source` / `derived_from_alias_id`（023 / Task 11）：作者改机器别名 → 新 author
    行以 `derived_from_alias_id` 指回机器行，不丢掉原 evidence。"""
    conn.execute(
        """
        INSERT INTO alias (
            id, project_id, node_id, surface, kind, usable_for_rules,
            source, derived_from_alias_id
        ) VALUES (:id, :pid, :nid, :surface, :kind, :usable, :source, :derived)
        """,
        {
            "id": alias_id,
            "pid": project_id,
            "nid": node_id,
            "surface": surface,
            "kind": kind.value,
            "usable": int(usable_for_rules),
            "source": source,
            "derived": derived_from_alias_id,
        },
    )
    return StoredAlias(
        id=alias_id,
        project_id=project_id,
        node_id=node_id,
        surface=surface,
        kind=kind,
        usable_for_rules=usable_for_rules,
        source=source,
        derived_from_alias_id=derived_from_alias_id,
    )


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

    **只对无向类型放开 dst**，不是对所有类型加个 OR：LOCATED_AT 的 src 是人、dst 是地点，
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
        WHERE a.project_id = :pid
          AND a.status = 'ACTIVE'
          -- 022 / §5：解析只读 ACTIVE alias；extractor 的还必须至少有一条
          -- FRESH alias_evidence（§4.5 的自动条件；作者 alias 不要求伪造证据）。
          AND (a.source = 'author' OR EXISTS (
            SELECT 1 FROM alias_evidence ae
             WHERE ae.alias_id = a.id AND ae.status = 'FRESH'
          ))
          {where}
        ORDER BY length(a.surface) DESC, a.surface ASC, n.id ASC
        """,
        params,
    )
    return _rows(cur)


# ══════════════════════════════════════════════════════════════════════════
# 写：upsert_edge 的零件（supersede 的编排在 sqlite_store.py）
# ══════════════════════════════════════════════════════════════════════════


def exclusivity_of(conn: sqlite3.Connection, edge_type: EdgeType) -> Exclusivity:
    cur = conn.execute("SELECT exclusivity FROM edge_type WHERE type = :t", {"t": edge_type.value})
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
    snapshot_generation: int
    disk_mtime_ns: int | None = None
    disk_size: int | None = None


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
        SELECT id, number, title, path, text_sha256, snapshot_generation,
               disk_mtime_ns, disk_size
          FROM chapter
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
        INSERT INTO chapter (id, project_id, number, title, path, text_sha256,
                             snapshot_generation, disk_mtime_ns, disk_size)
        VALUES (:id, :pid, :number, :title, :path, :sha, 1, :mtime, :size)
        """,
        {
            "id": chapter_id,
            "pid": spec.project_id,
            "number": spec.number,
            "title": spec.title,
            "path": spec.path,
            "sha": sha,
            "mtime": spec.disk_mtime_ns,
            "size": spec.disk_size,
        },
    )


def insert_chapter_summary_head(conn: sqlite3.Connection, chapter_id: str) -> None:
    """新章同事务预建 `chapter_summary_head` 行（020）——head 行不是「有了总结才有」，
    是「这一章存在就有」，current 指向 ACTIVE/RETRACTED 版本或 NULL。"""
    conn.execute(
        "INSERT INTO chapter_summary_head (chapter_id, current_summary_id) VALUES (?, NULL)",
        (chapter_id,),
    )


def update_chapter(
    conn: sqlite3.Connection, chapter_id: str, spec: ChapterSpec, sha: str,
    *, snapshot_generation: int,
) -> None:
    """`sync` 的落点（今天的入口是 `POST …/sync`）：作者在自己的编辑器里改了这一章。

    `number` 不在这里——它是幂等键，改它就是换一章。`updated_at` 显式重写：它的
    DEFAULT 只在 INSERT 时生效。
    """
    conn.execute(
        """
        UPDATE chapter
           SET title = :title, path = :path, text_sha256 = :sha,
               snapshot_generation = :generation,
               disk_mtime_ns = :mtime, disk_size = :size,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE id = :id
        """,
        {
            "id": chapter_id,
            "title": spec.title,
            "path": spec.path,
            "sha": sha,
            "generation": snapshot_generation,
            "mtime": spec.disk_mtime_ns,
            "size": spec.disk_size,
        },
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


def chapter_snapshots(
    conn: sqlite3.Connection, project_id: str, number: int
) -> list[ChapterSnapshot]:
    """某章的**全部**快照（内容去重后的历史版本），按 created_at 升序。章不存在 → `[]`。

    `is_current` 判据同 `current_snapshots`：`s.text_sha256 = c.text_sha256` 的精确等值，
    不是「最新那条」——作者把一章改回旧版时，当前指向的是那条旧快照，不是时间上最新的。
    """
    ch = find_chapter_by_number(conn, project_id, number)
    if ch is None:
        return []
    cur = conn.execute(
        """
        SELECT id AS snapshot_id, text_sha256, text, created_at
        FROM chapter_snapshot
        WHERE chapter_id = :cid
        ORDER BY created_at
        """,
        {"cid": ch.id},
    )
    return [
        ChapterSnapshot(**r, is_current=(r["text_sha256"] == ch.text_sha256)) for r in _rows(cur)
    ]


def snapshot_usage(conn: sqlite3.Connection, snapshot_id: str) -> SnapshotUsage:
    """这条快照被多少条记录引着。**删之前问这个。**

    五个计数各对应一张外键到 `chapter_snapshot` 且**没有 CASCADE** 的表。写死这五张
    是有意的：新增第六个引用方时这里不会自动跟上，但那时 `delete_snapshot` 会撞外键
    直接抛——**宁可炸也不要静默删掉别人的出处**（这正是不加 CASCADE 的理由）。
    020 补的两张：`extraction_analysis` / `extraction_application` 都引 snapshot_id。
    """
    cur = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM evidence              WHERE chapter_snapshot_id = :sid) AS evidence,
          (SELECT COUNT(*) FROM extraction_run        WHERE snapshot_id          = :sid) AS extraction_runs,
          (SELECT COUNT(*) FROM proposal_set          WHERE snapshot_id          = :sid) AS proposal_sets,
          (SELECT COUNT(*) FROM extraction_analysis   WHERE snapshot_id          = :sid) AS extraction_analyses,
          (SELECT COUNT(*) FROM extraction_application WHERE snapshot_id         = :sid) AS extraction_applications
        """,
        {"sid": snapshot_id},
    )
    return SnapshotUsage(snapshot_id=snapshot_id, **_rows(cur)[0])


def chapter_usage(
    conn: sqlite3.Connection, project_id: str, chapter_id: str, number: int
) -> ChapterUsage:
    """引擎在这一章上记了多少东西。**删整章之前问这个。**

    五个计数各对应一条「删了这一章就会跟着没」的路：

    - `evidence.chapter_id` → CASCADE，跟着蒸发；
    - `edge` 两条：**从这一章生效的**（`valid_from_chapter`）和**指着这一章那个节点的**
      （`src`/`dst`，PLANTED_IN / RESOLVED_IN），后者走 node 的 CASCADE 无声消失。
      两条用 `OR` 数进同一个 `edges`，不是相加——一条边可能同时满足两边，
      相加会报出一个比真实条数大的数，而那个数会被原样念给作者听；
    - `story_event.chapter_number`：记在这一章名下的情节；
    - `extraction_run` / `proposal_set`：跟着快照走的两张审计表（同 `snapshot_usage`）。

    **`valid_to_chapter` 故意不算**：一条「在第 n 章失效」的边说的是别处那件事在这儿结束了，
    它的出处不在这一章。把它算进来，删任何一章都会被自己以外的历史挡住。
    """
    cur = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM evidence WHERE chapter_id = :cid) AS evidence,
          (SELECT COUNT(*) FROM edge
             WHERE project_id = :pid
               AND (valid_from_chapter = :number OR src = :cid OR dst = :cid)) AS edges,
          (SELECT COUNT(*) FROM story_event
             WHERE project_id = :pid AND chapter_number = :number) AS events,
          (SELECT COUNT(*) FROM extraction_run WHERE snapshot_id IN
             (SELECT id FROM chapter_snapshot WHERE chapter_id = :cid)) AS extraction_runs,
          (SELECT COUNT(*) FROM proposal_set   WHERE snapshot_id IN
             (SELECT id FROM chapter_snapshot WHERE chapter_id = :cid)) AS proposal_sets
        """,
        {"pid": project_id, "cid": chapter_id, "number": number},
    )
    return ChapterUsage(chapter_number=number, **_rows(cur)[0])


def delete_chapter(conn: sqlite3.Connection, project_id: str, chapter_id: str) -> None:
    """把这一章从库里抹掉：证据 → 快照 → 节点（`chapter` 行跟着节点的 CASCADE 走）。

    **顺序是这个函数的全部内容，不是风格。** 一句 `DELETE FROM node` 本来就能靠级联
    删干净，但级联的执行次序不由我们定：`evidence.chapter_snapshot_id` 到
    `chapter_snapshot` **没有 CASCADE**，快照先被级联掉的那一刻，还活着的证据行就
    撞外键了。自己按依赖倒序删，就没有「中途那一瞬间」这回事。

    **不检查引用**——那是调用方（`sqlite_store.delete_chapter`）的活，它要在同一个事务里
    先问 `chapter_usage`。这里真有人引着的话外键会抛，那是最后一道，不是第一道。
    """
    conn.execute("DELETE FROM evidence WHERE chapter_id = :cid", {"cid": chapter_id})
    conn.execute("DELETE FROM chapter_snapshot WHERE chapter_id = :cid", {"cid": chapter_id})
    # 删 node 而不是删 chapter：两者同生（`put_chapter`），只删 chapter 会在库里留下
    # 一个没有章的 Chapter 节点，而它照样会出现在按 label 扫的地方。
    conn.execute(
        "DELETE FROM node WHERE id = :cid AND project_id = :pid",
        {"cid": chapter_id, "pid": project_id},
    )


def delete_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> int:
    """删掉一条快照，返回删掉的行数（0 = 本来就不存在）。

    **不检查引用**——那是调用方（`sqlite_store.delete_chapter_snapshot`）的活，它要在
    同一个事务里先问 `snapshot_usage`。这里真被引着的话 `PRAGMA foreign_keys=ON`
    会抛 IntegrityError，那是最后一道，不是第一道。
    """
    cur = conn.execute("DELETE FROM chapter_snapshot WHERE id = :sid", {"sid": snapshot_id})
    return int(cur.rowcount)


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


# ══════════════════════════════════════════════════════════════════════════
# 换快照之后：让锚在旧正文上的抽取事实退休
# ══════════════════════════════════════════════════════════════════════════

_RETIRE_SELECT: Final = """
    SELECT e.id FROM evidence e
     WHERE e.project_id = :pid
       AND e.chapter_id = :chapter
       AND e.chapter_snapshot_id != :current
"""
"""这一章里**锚在非当前快照**上的证据。

`chapter_snapshot_id` 是**审计指针**（永不更新，指着当年那一版），`chapter_id` 是
重定位指针（指着这一章）。所以这两个条件合起来就是「它当年锚的那一版正文，
已经不是现在磁盘上那一版了」。
"""


def retire_stale_extractor_facts(
    conn: sqlite3.Connection, project_id: str, chapter_id: str, current_snapshot_id: str
) -> RetirementReport:
    """把这一章里锚在**旧快照**上的**抽取器**事实标成 `STALE`。

    返回精确的 `RetirementReport`（退了哪些 ID、其中多少是 Writer 可见的 CANON），
    不能只给 rowcount——`chapter_refresh_run` 的 outbox 要记精确 ID，canon bump
    要问「有没有退到 CANON」。

    ── 为什么必须有这一下 ────────────────────────────────────────────────────

    作者在别的软件里改了第 88 章 → 后台整理按新快照重新分析一遍 → 而旧那一版的
    事实**还活着**。实测过（2026-08-14）：同一章分析两次，`story_event` 从 2 条变 4 条
    （一模一样两组），`edge` 里同一个人同时在两个地点 ACTIVE——**R4 会报一条正文里
    根本不存在的位置冲突**，而作者对着稿子完全看不懂系统在说什么。

    `STALE` 这一档从 001_init 就写在 `TEMPORAL_WHERE` 里、`location_conflict.py` 也
    照着它写了「STALE 立刻停火」，**但生产上一个写入方都没有**（`importer.py` 模块头
    写着「那是 M4」）。这个函数就是那个写入方。

    ── 为什么是 STALE 不是 RETRACTED ────────────────────────────────────────

    `RETRACTED` 的语义是「这条事实从未成立过」（同章更正）。而改稿之后那条事实
    **可能仍然是真的**，只是它的引语不在正文里了——依据没了，不等于结论错了。
    这正是 ADR 0006「STALE 立刻停火」那一档：停火，不是宣判。

    ── 为什么只退休 `source = 'extractor'` ──────────────────────────────────

    **作者手动加的、改过的那些一条都不许动。** 退休它们等于系统吃掉了作者的决定，
    那比重复更糟——他会发现自己刚补的一条认知在改了个错别字之后消失了，
    而没有任何地方告诉他为什么。`event_knower` 没有 `source` 列，它跟着它的事件走。
    """
    params = {"pid": project_id, "chapter": chapter_id, "current": current_snapshot_id}
    retired_edges = conn.execute(
        f"""
        UPDATE edge SET evidence_status = 'STALE'
         WHERE project_id = :pid
           AND source = 'extractor'
           AND evidence_status = 'FRESH'
           AND evidence_id IN ({_RETIRE_SELECT})
        RETURNING id, information_scope
        """,
        params,
    ).fetchall()
    retired_events = conn.execute(
        f"""
        UPDATE story_event SET evidence_status = 'STALE'
         WHERE project_id = :pid
           AND source = 'extractor'
           AND evidence_status = 'FRESH'
           AND evidence_id IN ({_RETIRE_SELECT})
        RETURNING id, information_scope
        """,
        params,
    ).fetchall()
    # 知情名单没有自己的 `source`，判据是「它挂的那条事件刚被退休了」。
    retired_knowers = conn.execute(
        """
        UPDATE event_knower SET evidence_status = 'STALE'
         WHERE project_id = :pid
           AND evidence_status = 'FRESH'
           AND event_id IN (
                 SELECT id FROM story_event
                  WHERE project_id = :pid AND evidence_status = 'STALE'
             )
        RETURNING event_id
        """,
        {"pid": project_id},
    )
    return RetirementReport(
        project_id=project_id,
        chapter_id=chapter_id,
        current_snapshot_id=current_snapshot_id,
        retired_edge_ids=tuple(str(row["id"]) for row in retired_edges),
        retired_event_ids=tuple(str(row["id"]) for row in retired_events),
        retired_knower_event_ids=tuple(str(row["event_id"]) for row in retired_knowers),
        touched_canon_edges=sum(
            1 for row in retired_edges if row["information_scope"] == InformationScope.CANON
        ),
        touched_canon_events=sum(
            1 for row in retired_events if row["information_scope"] == InformationScope.CANON
        ),
    )


def mark_canon_event_cast_author(
    conn: sqlite3.Connection,
    event_id: str,
    project_id: str,
    decision_log_id: str,
) -> None:
    """021 / Task 9：作者改过名单的整套 incidence 视为作者覆盖，机器重放不得再碰。

    `cast_owner` 是「当前解释由谁接管」；`story_event.source` 仍是 extractor 起源
    （正文换快照时旧机器事实照旧退休、作者修正不随它走）。SQL 只住 graph 层。
    """
    conn.execute(
        "UPDATE story_event SET cast_owner = 'author', cast_decision_log_id = ? "
        "WHERE id = ? AND project_id = ?",
        (decision_log_id, event_id, project_id),
    )


def event_reconciliation_ready(
    conn: sqlite3.Connection, project_id: str, event_id: str
) -> bool:
    """022 / Task 10：一条事件摘要是否仍是当前可用（核对该不该调模型）。

    status ACTIVE + evidence FRESH 且仍在 PROVISIONAL/CANON 才核；被撤回 /
    证据已失效的事件只解决旧 OPEN 通知，不调模型。SQL 只住 graph 层。
    """
    row = conn.execute(
        """
        SELECT 1 FROM story_event e
         WHERE e.project_id = ? AND e.id = ?
           AND e.status = 'ACTIVE' AND e.evidence_status = 'FRESH'
           AND e.information_scope IN ('PROVISIONAL','CANON')
         LIMIT 1
        """,
        (project_id, event_id),
    ).fetchone()
    return row is not None


def supersede_obsolete_proposals(
    conn: sqlite3.Connection,
    project_id: str,
    chapter_number: int,
    new_snapshot_id: str,
) -> int:
    """这一章保存了新正文：PENDING 提案若锚的不是新快照 → OBSOLETE（021 / Task 9）。

    必须在 `commit_chapter_snapshot` 的**同一事务**里调用（不变量 20）：正文已变
    和旧提案退出待确认不能拆成两个原子性。status 一字不改（003 的审计触发器
    只把非 PENDING 当作者裁决），`superseded_by_snapshot_id` 记下是谁顶的。
    """
    cur = conn.execute(
        """
        UPDATE proposal_set
           SET currentness = 'OBSOLETE',
               superseded_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               superseded_by_snapshot_id = :new_snapshot
         WHERE project_id = :pid AND chapter_number = :chapter
           AND status = 'PENDING' AND currentness = 'CURRENT'
           AND snapshot_id IS NOT NULL AND snapshot_id <> :new_snapshot
        """,
        {"new_snapshot": new_snapshot_id, "pid": project_id, "chapter": chapter_number},
    )
    return cur.rowcount


def bump_canon_once_if_retired_canon(
    conn: sqlite3.Connection, project_id: str, retirement: RetirementReport
) -> int:
    """退休使 Writer 可见 Canon 集合变化时，至多 bump 一次 `canon_version`。

    零 CANON 变化不 bump。返回事务内的新（或原）版本——调用方拿它写
    `chapter_refresh_run.canon_version_before/after`。
    """
    row = conn.execute(
        "SELECT canon_version FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    current = int(row["canon_version"])
    if not retirement.effective_canon_changed:
        return current
    bumped = conn.execute(
        "UPDATE project SET canon_version = canon_version + 1 WHERE id = ? RETURNING canon_version",
        (project_id,),
    ).fetchone()
    return int(bumped["canon_version"])


# ══════════════════════════════════════════════════════════════════════════
# 事件摘要版本（019 / Task 7）—— 所有 event_summary_* 的 SQL 只住在这儿
# ══════════════════════════════════════════════════════════════════════════


def event_summary_current_row(
    conn: sqlite3.Connection, event_id: str
) -> dict[str, Any] | None:
    """一个事件的当前摘要版本（head 指向的那一行）；无版本 → None。"""
    return conn.execute(
        """
        SELECT v.id, v.project_id, v.event_id, v.source_snapshot_id, v.evidence_sha256,
               v.summary, v.summary_sha256, v.source, v.status,
               v.replaces_version_id, v.created_at
          FROM event_summary_head h
          JOIN event_summary_version v ON v.id = h.current_version_id
         WHERE h.event_id = ?
        """,
        (event_id,),
    ).fetchone()


def insert_event_summary_version(
    conn: sqlite3.Connection,
    *,
    version_id: str,
    project_id: str,
    event_id: str,
    source_snapshot_id: str | None,
    evidence_sha256: str | None,
    summary: str,
    source: str,
    status: str,
    replaces_version_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO event_summary_version (
            id, project_id, event_id, source_snapshot_id, evidence_sha256,
            summary, summary_sha256, source, status, replaces_version_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            project_id,
            event_id,
            source_snapshot_id,
            evidence_sha256,
            summary,
            _sha256_hex(summary),
            source,
            status,
            replaces_version_id,
        ),
    )


def switch_event_summary_head(
    conn: sqlite3.Connection, event_id: str, new_version_id: str, expected: str | None
) -> bool:
    row = conn.execute(
        """
        UPDATE event_summary_head
           SET current_version_id = :new_id,
               machine_intent_seq = machine_intent_seq + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE event_id = :eid AND current_version_id IS :expected
        RETURNING event_id
        """,
        {"new_id": new_version_id, "eid": event_id, "expected": expected},
    ).fetchone()
    return row is not None


def event_summary_history_rows(
    conn: sqlite3.Connection, event_id: str
) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT id, project_id, event_id, source_snapshot_id, evidence_sha256,
               summary, summary_sha256, source, status, replaces_version_id, created_at
          FROM event_summary_version
         WHERE event_id = ?
         ORDER BY created_at, rowid
        """,
        (event_id,),
    ).fetchall()


def create_event_summary_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    project_id: str,
    event_id: str,
    source_snapshot_id: str,
    source_generation: int,
    source_sha256: str,
    expected_head: str | None,
    intent_seq: int,
    trigger_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO summary_generation_job (
            id, project_id, target_type, chapter_id, event_id,
            source_snapshot_id, source_generation, source_sha256,
            refresh_attempt_id, required_ruleset_epoch, required_ruleset_hash,
            expected_head_version_id, required_machine_intent_seq,
            trigger_key, trigger_source, status
        ) VALUES (?, ?, 'EVENT', NULL, ?, ?, ?, ?, NULL, NULL, NULL,
                  ?, ?, ?, 'manual', 'PENDING')
        """,
        (
            job_id,
            project_id,
            event_id,
            source_snapshot_id,
            source_generation,
            source_sha256,
            expected_head,
            intent_seq,
            trigger_key,
        ),
    )


def event_summary_job_basis(
    conn: sqlite3.Connection, project_id: str, event_id: str
) -> dict[str, Any] | None:
    """regenerate 冻结 job basis 用的只读查询（story_event/evidence/snapshot）。"""
    row = conn.execute(
        """
        SELECT e.project_id, e.information_scope, e.valid_from_chapter,
               ev.chapter_snapshot_id, ch.snapshot_generation,
               ev.quote_text, ev.quote_sha256 AS evidence_sha256,
               h.current_version_id, h.machine_intent_seq
          FROM story_event e
          LEFT JOIN evidence ev ON ev.id = e.evidence_id
          LEFT JOIN chapter_snapshot cs ON cs.id = ev.chapter_snapshot_id
          LEFT JOIN chapter ch ON ch.id = cs.chapter_id
          JOIN event_summary_head h ON h.event_id = e.id
         WHERE e.id = ? AND e.project_id = ?
        """,
        (event_id, project_id),
    ).fetchone()
    return dict(row) if row is not None else None


def event_information_scope(
    conn: sqlite3.Connection, project_id: str, event_id: str
) -> str | None:
    row = conn.execute(
        "SELECT information_scope FROM story_event WHERE id = ? AND project_id = ?",
        (event_id, project_id),
    ).fetchone()
    return str(row["information_scope"]) if row is not None else None


# ══════════════════════════════════════════════════════════════════════════
# Canon 边纠错（020 / Task 8）—— slot key 与 override 的 SQL 唯一住址
# ══════════════════════════════════════════════════════════════════════════


def canon_edge_slot_key(edge: Edge) -> str:
    """一个语义槽的唯一键（§4.6）。

    location=`subject+type+valid_from`；state=`subject+type+dim_key+valid_from`；
    relation=`normalized_pair+type+valid_from`。纠错、抽取 staging、auto-Canon
    promotion 和重放全部调用它——禁止各自拼字符串。
    """
    subject = edge.src
    if edge.type is EdgeType.RELATED_TO:
        subject = "|".join(sorted((edge.src, edge.dst)))
    parts = [subject, edge.type.value]
    if edge.type is EdgeType.HAS_STATE:
        parts.append(edge.props.dim_key or "")
    parts.append(str(edge.valid_from_chapter))
    return "|".join(parts)


def active_canon_override_for_slot(
    conn: sqlite3.Connection, project_id: str, slot_key: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, project_id, slot_key, edge_type, source_edge_id, replacement_edge_id,
               before_props_json, after_props_json, action, decision_log_id, status,
               supersedes_override_id, created_at
          FROM canon_edge_override
         WHERE project_id = ? AND slot_key = ? AND status = 'ACTIVE'
        """,
        (project_id, slot_key),
    ).fetchone()


def active_canon_override_for_edge(
    conn: sqlite3.Connection, project_id: str, edge_id: str
) -> sqlite3.Row | None:
    """`replacement_edge_id = :edge_id` 的 ACTIVE override —— 该边当前由作者接管。"""
    return conn.execute(
        """
        SELECT id, project_id, slot_key, edge_type, source_edge_id, replacement_edge_id,
               before_props_json, after_props_json, action, decision_log_id, status,
               supersedes_override_id, created_at
          FROM canon_edge_override
         WHERE project_id = ? AND replacement_edge_id = ? AND status = 'ACTIVE'
        """,
        (project_id, edge_id),
    ).fetchone()


def insert_canon_override(
    conn: sqlite3.Connection,
    *,
    override_id: str,
    project_id: str,
    slot_key: str,
    edge_type: str,
    source_edge_id: str,
    replacement_edge_id: str | None,
    before_props_json: str,
    after_props_json: str | None,
    action: str,
    decision_log_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO canon_edge_override (
            id, project_id, slot_key, edge_type, source_edge_id, replacement_edge_id,
            before_props_json, after_props_json, action, decision_log_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            override_id,
            project_id,
            slot_key,
            edge_type,
            source_edge_id,
            replacement_edge_id,
            before_props_json,
            after_props_json,
            action,
            decision_log_id,
        ),
    )


def supersede_active_override(
    conn: sqlite3.Connection, project_id: str, slot_key: str
) -> str | None:
    """把该槽现有的 ACTIVE override 标 SUPERSEDED（append-only，历史不删）。

    返回被 supersede 的旧 override id——调用方在新 override 插入后补 FK 链接。
    """
    row = conn.execute(
        """
        UPDATE canon_edge_override
           SET status = 'SUPERSEDED'
         WHERE project_id = :pid AND slot_key = :slot AND status = 'ACTIVE'
        RETURNING id
        """,
        {"pid": project_id, "slot": slot_key},
    ).fetchone()
    return str(row["id"]) if row is not None else None


def link_override_chain(
    conn: sqlite3.Connection, old_override_id: str, new_override_id: str
) -> None:
    conn.execute(
        "UPDATE canon_edge_override SET supersedes_override_id = ? WHERE id = ?",
        (new_override_id, old_override_id),
    )


def find_edge_by_identity_any_status(
    conn: sqlite3.Connection, spec: EdgeSpec
) -> Edge | None:
    """按身份找边（含 RETRACTED）——A→B→A 恢复旧 identity 时复用旧行。"""
    row = conn.execute(
        """
        SELECT id, project_id, src, dst, type, valid_from_chapter, valid_to_chapter,
               information_scope, status, confidence, props_json, source, evidence_id,
               evidence_status
          FROM edge
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
    ).fetchone()
    return None if row is None else to_edge(row)


def restore_retracted_edge(
    conn: sqlite3.Connection, edge_id: str
) -> None:
    """A→B→A：把 RETRACTED 旧行恢复成 ACTIVE current（origin/evidence 原样保留，
    author ownership 由 ACTIVE override 赋予）。"""
    conn.execute(
        """
        UPDATE edge SET status = 'ACTIVE', valid_to_chapter = NULL
         WHERE id = ?
        """,
        (edge_id,),
    )


def update_edge_props_only(conn: sqlite3.Connection, edge_id: str, props_json: str) -> None:
    """identity 不变时的投影更新：**只**改 props（020 专用），
    严禁普通 `update_edge_facets` 改 source/evidence/status/vf。"""
    conn.execute(
        """
        UPDATE edge SET props_json = :props
         WHERE id = :id
        """,
        {"id": edge_id, "props": props_json},
    )


def _sha256_hex(text: str) -> str:
    """summary_sha256 的唯一实现：UTF-8 原始字节（§4.4，不做 strip/归一化）。"""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chapter_disk_stats(
    conn: sqlite3.Connection, project_id: str
) -> dict[int, tuple[int | None, int | None]]:
    """`{章号: (记下的 mtime_ns, 记下的 size)}`。**一次查询问完整本书。**

    值里的 `None` = 这一章的 stat 没记过（老库、或从非文件路径落的）——调用方必须把
    它当成「不知道 ⇒ 重读」（迁移 015：那是 fail-safe 的那一侧，而且读过一次就自愈）。
    """
    cur = conn.execute(
        "SELECT number, disk_mtime_ns, disk_size FROM chapter WHERE project_id = :pid",
        {"pid": project_id},
    )
    return {int(r["number"]): (r["disk_mtime_ns"], r["disk_size"]) for r in _rows(cur)}


# ══════════════════════════════════════════════════════════════════════════
# 别名生命周期（023 / Task 11）：软撤回 / 改归属 / 改 surface
# ══════════════════════════════════════════════════════════════════════════


def fetch_alias(conn: sqlite3.Connection, alias_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, project_id, node_id, surface, kind, usable_for_rules,
               source, status, derived_from_alias_id, created_at, updated_at
          FROM alias
         WHERE id = ?
        """,
        (alias_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def retract_alias(conn: sqlite3.Connection, alias_id: str) -> None:
    """软撤回：status → RETRACTED（tombstone，历史保留、允许重新登记）。"""
    conn.execute(
        "UPDATE alias SET status = 'RETRACTED', "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ? AND status = 'ACTIVE'",
        (alias_id,),
    )


def add_alias_evidence(
    conn: sqlite3.Connection,
    *,
    alias_id: str,
    evidence_id: str,
    source_snapshot_id: str,
    source_generation: int,
    extraction_application_id: str | None,
) -> None:
    """机器别名的一条支撑证据（幂等：同 evidence + generation 只记一次）。"""
    conn.execute(
        """
        INSERT OR IGNORE INTO alias_evidence (
            alias_id, evidence_id, source_snapshot_id, source_generation,
            extraction_application_id, status
        ) VALUES (?, ?, ?, ?, ?, 'FRESH')
        """,
        (
            alias_id,
            evidence_id,
            source_snapshot_id,
            source_generation,
            extraction_application_id,
        ),
    )


def stale_alias_evidence_for_snapshot(
    conn: sqlite3.Connection, project_id: str, snapshot_id: str
) -> int:
    """章节提交新快照时，把该章旧 snapshot 的机器 alias_evidence 标 STALE。"""
    cur = conn.execute(
        "UPDATE alias_evidence SET status = 'STALE' "
        "WHERE source_snapshot_id = ? AND status = 'FRESH' AND alias_id IN ("
        "  SELECT a.id FROM alias a WHERE a.project_id = ? AND a.source = 'extractor'"
        ")",
        (snapshot_id, project_id),
    )
    return cur.rowcount


# ══════════════════════════════════════════════════════════════════════════
# 抽取别名 identity-first（Task 12）—— alias/evidence SQL 的唯一住址
# ══════════════════════════════════════════════════════════════════════════


def active_alias_surfaces(conn: sqlite3.Connection, project_id: str) -> set[str]:
    """全项目 ACTIVE surface 集合（判「当前未映射」的原料）。"""
    rows = conn.execute(
        "SELECT surface FROM alias WHERE project_id = ? AND status = 'ACTIVE'",
        (project_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def existing_active_alias(
    conn: sqlite3.Connection, project_id: str, surface: str
) -> dict[str, Any] | None:
    """surface 的 ACTIVE 别名；`ambiguous=True` = 它指向 >1 个不同人物。"""
    rows = conn.execute(
        "SELECT id, node_id FROM alias "
        "WHERE project_id = ? AND surface = ? AND status = 'ACTIVE'",
        (project_id, surface),
    ).fetchall()
    if not rows:
        return None
    distinct = {str(r["node_id"]) for r in rows}
    return {
        "id": str(rows[0]["id"]),
        "node_id": str(rows[0]["node_id"]),
        "ambiguous": len(distinct) > 1,
    }


def insert_alias_evidence_quote(
    conn: sqlite3.Connection,
    *,
    evidence_id: str,
    project_id: str,
    chapter_id: str,
    chapter_snapshot_id: str,
    quote_text: str,
    para_index: int,
) -> None:
    """自动别名的证据行（quote 必须逐字落锚，§4.5 条件 3 的锚侧）。"""
    from ..decisions import quote_hash

    conn.execute(
        """
        INSERT INTO evidence (
            id, project_id, chapter_id, chapter_snapshot_id,
            para_index, quote_text, quote_sha256, para_index_hint, occurrence_k
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            evidence_id,
            project_id,
            chapter_id,
            chapter_snapshot_id,
            para_index,
            quote_text,
            quote_hash(quote_text),
        ),
    )
