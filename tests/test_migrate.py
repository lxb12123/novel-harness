"""db.py + numbered SQL migrations（PLAN §8 Day 2：连跑两次 migrate 不报错）。

本文件测两件事：

1. **闸门**：幂等由 `PRAGMA user_version` 提供，不由 DDL 提供。001_init.sql 里没有一个
   IF NOT EXISTS，重跑必报错——所以「跑两次不报错」实际测的是闸门有没有拦住第二次。
2. **约束电池**：schema 里每条 CHECK / FK / UNIQUE 都逐条打一遍。它们不是装饰——
   每一条都对应一个「写错了会得到错误答案」的场景，注释里写了是哪个。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from novel_harness.db import IN_MEMORY, MigrationError, connect, migrate, user_version
from novel_harness.decisions import DecisionKind, quote_hash, read as read_decisions
from novel_harness.ids import EntityType, new_id, new_project_id

# ══════════════════════════════════════════════════════════════════════════
# fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "nh.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def project(conn: sqlite3.Connection) -> str:
    pid = new_project_id()
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)", (pid, "青云", "/x"))
    return pid


def _node(conn: sqlite3.Connection, project_id: str, label: str, name: str) -> str:
    nid = new_id(EntityType.for_node_label(label), project_id)
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES (?, ?, ?, ?)",
        (nid, project_id, label, name),
    )
    return nid


def _edge(conn: sqlite3.Connection, project_id: str, src: str, dst: str, **over: object) -> str:
    eid = new_id(EntityType.EDGE, project_id)
    cols: dict[str, object] = {
        "id": eid,
        "project_id": project_id,
        "src": src,
        "dst": dst,
        "type": "LOCATED_AT",
        "valid_from_chapter": 10,
        "information_scope": "CANON",
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO edge ({names}) VALUES ({marks})", tuple(cols.values()))
    return eid


# ══════════════════════════════════════════════════════════════════════════
# 闸门
# ══════════════════════════════════════════════════════════════════════════


def test_migrate_twice_is_idempotent(tmp_path: Path) -> None:
    """§8 Day 2 的验收。第二次跑必须是**零语句**，而不是「跑了但没报错」——
    001_init.sql 重跑一定会撞 `table project already exists`，能不报错只有一种可能：
    闸门拦住了它。"""
    c = connect(tmp_path / "nh.db")
    assert user_version(c) == 0
    assert migrate(c) == 3
    assert migrate(c) == 3  # 不抛
    assert user_version(c) == 3
    c.close()


def test_migrate_twice_on_fresh_connections(tmp_path: Path) -> None:
    # 闸门存的是库上的 user_version，不是进程里的一个变量——换条连接也必须记得。
    path = tmp_path / "nh.db"
    c1 = connect(path)
    migrate(c1)
    c1.close()
    c2 = connect(path)
    assert migrate(c2) == 3
    assert user_version(c2) == 3
    c2.close()


def test_migrate_is_not_self_idempotent_without_the_gate(conn: sqlite3.Connection) -> None:
    """反面钉死：绕开闸门直接重跑 DDL **必须**炸。

    如果哪天有人给 001_init.sql 加上 IF NOT EXISTS 让它「更安全」，这条会红。那不是
    更安全——那是把「跑错了迁移」变成静默通过，而静默通过正是这个项目一路在砍的东西。
    """
    from importlib.resources import files

    sql = (files("novel_harness") / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        conn.executescript(sql)


def test_migrate_refuses_newer_database(conn: sqlite3.Connection) -> None:
    # 用旧代码往一个更新 schema 的库里写 = 按老假设改新数据。只能拒绝。
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="99"):
        migrate(conn)


def test_migration_files_are_readable_from_package() -> None:
    # wheel 里没有源码树：这条测的是 importlib.resources 这条路（`uvx novel-harness`
    # 是改 11 的唯一安装叙事，`__file__` 拼路径在那里就是坏的）。
    from importlib.resources import files

    root = files("novel_harness") / "migrations"
    names = sorted(e.name for e in root.iterdir() if e.name.endswith(".sql"))
    assert names == [
        "001_init.sql",
        "002_m4_events.sql",
        "003_proposal_audit_recovery.sql",
    ]
    assert "PRAGMA user_version = 1" in (root / "001_init.sql").read_text(encoding="utf-8")
    assert "PRAGMA user_version = 2" in (root / "002_m4_events.sql").read_text(encoding="utf-8")
    assert "PRAGMA user_version = 3" in (
        root / "003_proposal_audit_recovery.sql"
    ).read_text(encoding="utf-8")


def test_populated_v1_database_migrates_without_changing_existing_rows(tmp_path: Path) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v1.db")
    v1_sql = (files("novel_harness") / "migrations" / "001_init.sql").read_text(
        encoding="utf-8"
    )
    c.executescript(v1_sql)
    assert user_version(c) == 1

    project_id = "project:v1-existing"
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,?)",
        (project_id, "旧书", "/old", 7),
    )
    character_id = _node(c, project_id, "Character", "旧人物")
    c.execute(
        "INSERT INTO proposal_set "
        "(id, project_id, kind, summary, item_count, items_json, confidence, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("proposal:v1", project_id, "alias_cluster", "旧提案", 2, '["a","b"]', 0.8, "PENDING"),
    )
    before_project = tuple(c.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone())
    before_node = tuple(c.execute("SELECT * FROM node WHERE id = ?", (character_id,)).fetchone())
    before_proposal = tuple(
        c.execute(
            "SELECT id, project_id, kind, summary, item_count, items_json, confidence, status, "
            "created_at, resolved_at, decision_log_id FROM proposal_set WHERE id = 'proposal:v1'"
        ).fetchone()
    )

    assert migrate(c) == 3
    assert migrate(c) == 3
    assert tuple(c.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()) == before_project
    assert tuple(c.execute("SELECT * FROM node WHERE id = ?", (character_id,)).fetchone()) == before_node
    assert (
        tuple(
            c.execute(
                "SELECT id, project_id, kind, summary, item_count, items_json, confidence, status, "
                "created_at, resolved_at, decision_log_id FROM proposal_set WHERE id = 'proposal:v1'"
            ).fetchone()
        )
        == before_proposal
    )
    added = c.execute(
        "SELECT chapter_number, snapshot_id, base_canon_version, schema_version, prompt_hash "
        "FROM proposal_set WHERE id = 'proposal:v1'"
    ).fetchone()
    assert tuple(added) == (None, None, 0, None, None)
    c.close()


def test_m4_tables_and_proposal_columns_are_present(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {
        "story_event",
        "event_participant",
        "event_knower",
        "event_reveal",
        "proposal_event",
        "proposal_edge",
        "extraction_run",
    } <= tables

    proposal_columns = {row[1] for row in conn.execute("PRAGMA table_info(proposal_set)")}
    assert {
        "chapter_number",
        "snapshot_id",
        "base_canon_version",
        "schema_version",
        "prompt_hash",
        "resolution_action",
        "resolved_canon_version",
        "audit_envelope_json",
    } <= proposal_columns


def test_populated_v2_database_backfills_attached_proposal_audit(tmp_path: Path) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    assert user_version(c) == 2
    project_id = "project:v2-audit"
    proposal_id = "proposal:v2-audit"
    decision_id = "decision:v2-audit"
    payload = {
        "proposal_id": proposal_id,
        "action": "accept",
        "status": "ACCEPTED",
        "canon_version": 5,
        "kind": "low_confidence_main",
        "events": [],
        "edges": [],
        "characters": [],
    }
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,?)",
        (project_id, "v2 旧书", "/old", 5),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', ?, 'accept')
        """,
        (decision_id, project_id, json.dumps(payload, sort_keys=True)),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, decision_log_id,
            base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', ?, 4)
        """,
        (proposal_id, project_id, decision_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT resolution_action, resolved_canon_version, audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert row["resolution_action"] == "accept"
    assert row["resolved_canon_version"] == 5
    assert json.loads(row["audit_envelope_json"])["payload"] == payload
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        c.execute(
            "UPDATE proposal_set SET resolution_action = 'reject' WHERE id = ?",
            (proposal_id,),
        )
    c.rollback()
    c.close()


def test_v2_migration_attaches_one_matching_proposal_review_gap(tmp_path: Path) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2-attach-gap.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = "project:v2-attach-gap"
    proposal_id = "proposal:v2-attach-gap"
    decision_id = "decision:v2-attach-gap"
    payload = {
        "proposal_id": proposal_id,
        "action": "accept",
        "status": "ACCEPTED",
        "canon_version": 5,
        "kind": "low_confidence_main",
        "events": [],
        "edges": [],
        "characters": [],
    }
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,?)",
        (project_id, "v2 审计挂接空洞", "/old", 5),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', ?, 'accept')
        """,
        (decision_id, project_id, json.dumps(payload, sort_keys=True)),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 4)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert row["decision_log_id"] == decision_id
    assert row["resolution_action"] == "accept"
    assert row["resolved_canon_version"] == 5
    assert json.loads(row["audit_envelope_json"])["payload"] == payload
    c.close()


def test_v2_migration_refuses_duplicate_matching_proposal_reviews(tmp_path: Path) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2-duplicate-audit.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = "project:v2-duplicate-audit"
    proposal_id = "proposal:v2-duplicate-audit"
    payload = {
        "proposal_id": proposal_id,
        "action": "reject",
        "status": "REJECTED",
        "canon_version": 4,
        "kind": "low_confidence_main",
        "events": [],
        "edges": [],
        "characters": [],
    }
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,?)",
        (project_id, "v2 重复审计", "/old", 4),
    )
    for decision_id in ("decision:v2-duplicate-one", "decision:v2-duplicate-two"):
        c.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES (?, ?, 'proposal_review', ?, 'reject')
            """,
            (decision_id, project_id, json.dumps(payload, sort_keys=True)),
        )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, decision_log_id,
            base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'REJECTED', ?, 4)
        """,
        (proposal_id, project_id, "decision:v2-duplicate-one"),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert row["decision_log_id"] == "decision:v2-duplicate-one"
    assert tuple(row)[1:] == (None, None, None)
    c.close()


def test_v2_migration_quarantines_utf8_blob_kind_duplicate_history(
    tmp_path: Path,
) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2-blob-kind-duplicate.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = "project:v2-blob-kind-duplicate"
    proposal_id = "proposal:v2-blob-kind-duplicate"
    payload = json.dumps(
        {
            "proposal_id": proposal_id,
            "action": "accept",
            "status": "ACCEPTED",
            "kind": "low_confidence_main",
            "canon_version": 1,
        },
        sort_keys=True,
    )
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,1)",
        (project_id, "v2 BLOB kind 重复审计", "/old"),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', ?, 'accept')
        """,
        ("decision:v2-text-kind", project_id, payload),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, CAST(? AS BLOB), ?, 'accept')
        """,
        ("decision:v2-blob-kind", project_id, b"proposal_review", payload),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 0)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
    assert len(
        read_decisions(c, project_id, kind=DecisionKind.PROPOSAL_REVIEW)
    ) == 2
    c.close()


def test_v2_migration_quarantines_shared_decision_attachment(tmp_path: Path) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2-shared-attachment.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = "project:v2-shared-attachment"
    first_id = "proposal:v2-shared-first"
    second_id = "proposal:v2-shared-second"
    decision_id = "decision:v2-shared"
    payload = {
        "proposal_id": first_id,
        "action": "accept",
        "status": "ACCEPTED",
        "canon_version": 5,
        "kind": "low_confidence_main",
        "events": [],
        "edges": [],
        "characters": [],
    }
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,?)",
        (project_id, "v2 共享挂接", "/old", 5),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', ?, 'accept')
        """,
        (decision_id, project_id, json.dumps(payload, sort_keys=True)),
    )
    for proposal_id in (first_id, second_id):
        c.execute(
            """
            INSERT INTO proposal_set (
                id, project_id, kind, items_json, status, decision_log_id,
                base_canon_version
            ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', ?, 4)
            """,
            (proposal_id, project_id, decision_id),
        )
    c.commit()

    assert migrate(c) == 3
    rows = c.execute(
        """
        SELECT id, resolution_action, resolved_canon_version, audit_envelope_json
        FROM proposal_set WHERE project_id = ? ORDER BY id
        """,
        (project_id,),
    ).fetchall()
    assert [row["id"] for row in rows] == sorted((first_id, second_id))
    assert all(tuple(row)[1:] == (None, None, None) for row in rows)
    triggers = {
        row[0]
        for row in c.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    assert {
        "proposal_decision_once_insert",
        "proposal_decision_once_update",
    } <= triggers
    c.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate-key",
        "duplicate-nested",
        "escaped-key",
        "lone-surrogate",
        "overflow-integer",
        "boolean-version",
    ],
)
def test_v2_migration_quarantines_ambiguous_audit_payload(
    tmp_path: Path,
    corruption: str,
) -> None:
    from importlib.resources import files

    c = connect(tmp_path / f"v2-ambiguous-{corruption}.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = f"project:v2-ambiguous-{corruption}"
    proposal_id = f"proposal:v2-ambiguous-{corruption}"
    if corruption == "duplicate-key":
        payload_json = (
            '{"proposal_id":"'
            + proposal_id
            + '","proposal_id":"proposal:other","action":"accept",'
            '"status":"ACCEPTED","kind":"low_confidence_main",'
            '"canon_version":1}'
        )
    elif corruption == "duplicate-nested":
        payload_json = (
            '{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"summary":"first","summary":"last"}]}'
        )
    elif corruption == "escaped-key":
        payload_json = (
            '{"proposal_id":"'
            + proposal_id
            + '","\\u0061ction":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1}'
        )
    elif corruption == "lone-surrogate":
        payload_json = (
            '{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"summary":"\\ud800"}]}'
        )
    elif corruption == "overflow-integer":
        payload_json = (
            '{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"x":9223372036854775808}]}'
        )
    else:
        payload_json = json.dumps(
            {
                "proposal_id": proposal_id,
                "action": "accept",
                "status": "ACCEPTED",
                "kind": "low_confidence_main",
                "canon_version": True,
            },
            sort_keys=True,
        )
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,1)",
        (project_id, "v2 歧义审计", "/old"),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', ?, 'accept')
        """,
        (f"decision:v2-ambiguous-{corruption}", project_id, payload_json),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 0)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
    c.close()


def test_v2_migration_quarantines_invalid_utf8_payload_without_stalling(
    tmp_path: Path,
) -> None:
    from importlib.resources import files

    c = connect(tmp_path / "v2-invalid-utf8-payload.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = "project:v2-invalid-utf8-payload"
    proposal_id = "proposal:v2-invalid-utf8-payload"
    payload = (
        b'{"proposal_id":"proposal:v2-invalid-utf8-payload",'
        b'"action":"accept","status":"ACCEPTED",'
        b'"kind":"low_confidence_main","canon_version":1,"note":"\xff"}'
    )
    assert c.execute(
        "SELECT json_valid(CAST(? AS TEXT))", (payload,)
    ).fetchone()[0] == 1
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,1)",
        (project_id, "v2 非法 UTF-8 payload", "/old"),
    )
    c.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, 'proposal_review', CAST(? AS TEXT), 'accept')
        """,
        ("decision:v2-invalid-utf8-payload", project_id, payload),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 0)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
    c.close()


@pytest.mark.parametrize("column", ["id", "ts", "subject_name", "actor"])
def test_v2_migration_quarantines_invalid_utf8_decision_fields(
    tmp_path: Path,
    column: str,
) -> None:
    from importlib.resources import files

    c = connect(tmp_path / f"v2-invalid-utf8-{column}.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = f"project:v2-invalid-utf8-{column}"
    proposal_id = f"proposal:v2-invalid-utf8-{column}"
    payload = json.dumps(
        {
            "proposal_id": proposal_id,
            "action": "accept",
            "status": "ACCEPTED",
            "kind": "low_confidence_main",
            "canon_version": 1,
        },
        sort_keys=True,
    )
    values: dict[str, object] = {
        "id": f"decision:v2-invalid-utf8-{column}",
        "ts": "2026-08-03T12:00:00.000Z",
        "subject_name": "顾清音",
        "actor": "author",
    }
    values[column] = b"\xff"
    field_names = tuple(values)
    placeholders = [
        "CAST(? AS TEXT)" if field == column else "?" for field in field_names
    ]
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,1)",
        (project_id, "v2 非法 UTF-8 decision", "/old"),
    )
    c.execute(
        f"""
        INSERT INTO decision_log (
            {", ".join(field_names)}, project_id, kind, payload_json, decision
        ) VALUES (
            {", ".join(placeholders)}, ?, 'proposal_review', ?, 'accept'
        )
        """,
        (*values.values(), project_id, payload),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 0)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
    c.close()


@pytest.mark.parametrize("corruption", ["bad-hash", "bad-chapter", "bad-para"])
def test_v2_migration_quarantines_invalid_decision_audit_fields(
    tmp_path: Path,
    corruption: str,
) -> None:
    from importlib.resources import files

    c = connect(tmp_path / f"v2-invalid-audit-{corruption}.db")
    root = files("novel_harness") / "migrations"
    c.executescript((root / "001_init.sql").read_text(encoding="utf-8"))
    c.executescript((root / "002_m4_events.sql").read_text(encoding="utf-8"))
    project_id = f"project:v2-invalid-audit-{corruption}"
    proposal_id = f"proposal:v2-invalid-audit-{corruption}"
    payload = json.dumps(
        {
            "proposal_id": proposal_id,
            "action": "accept",
            "status": "ACCEPTED",
            "kind": "low_confidence_main",
            "canon_version": 1,
        },
        sort_keys=True,
    )
    quote_text = "abc" if corruption == "bad-hash" else None
    quote_sha256 = "0" * 64 if corruption == "bad-hash" else None
    chapter_number = 0 if corruption == "bad-chapter" else None
    para_index = -1 if corruption == "bad-para" else None
    c.execute(
        "INSERT INTO project (id, name, root_path, canon_version) VALUES (?,?,?,1)",
        (project_id, "v2 非法审计字段", "/old"),
    )
    c.execute(
        """
        INSERT INTO decision_log (
            id, project_id, kind, payload_json, decision, quote_text,
            quote_sha256, chapter_number, para_index
        ) VALUES (?, ?, 'proposal_review', ?, 'accept', ?, ?, ?, ?)
        """,
        (
            f"decision:v2-invalid-audit-{corruption}",
            project_id,
            payload,
            quote_text,
            quote_sha256,
            chapter_number,
            para_index,
        ),
    )
    c.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, base_canon_version
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 0)
        """,
        (proposal_id, project_id),
    )
    c.commit()

    assert migrate(c) == 3
    row = c.execute(
        """
        SELECT decision_log_id, resolution_action, resolved_canon_version,
               audit_envelope_json
        FROM proposal_set WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
    c.close()


def test_proposal_audit_triggers_reject_incomplete_or_duplicate_history(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    proposal_id = "proposal:audit-guard"
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (?,?,?,?)",
        (proposal_id, project, "low_confidence_main", "[{}]"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            "UPDATE proposal_set SET status = 'ACCEPTED' WHERE id = ?",
            (proposal_id,),
        )

    payload_data = {
        "proposal_id": proposal_id,
        "action": "accept",
        "status": "ACCEPTED",
        "kind": "low_confidence_main",
        "canon_version": 1,
    }
    payload = json.dumps(payload_data, sort_keys=True)
    conn.execute(
        """
        UPDATE proposal_set
        SET status = 'ACCEPTED', resolution_action = 'accept',
            resolved_canon_version = 1, audit_envelope_json = ?
        WHERE id = ?
        """,
        (json.dumps({"payload": payload_data}, sort_keys=True), proposal_id),
    )
    conn.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES ('decision:audit-one', ?, 'proposal_review', ?, 'accept')
        """,
        (project, payload),
    )
    with pytest.raises(sqlite3.IntegrityError, match="already exists"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-two', ?, 'proposal_review', ?, 'accept')
            """,
            (project, payload),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ambiguous"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-ambiguous', ?, 'proposal_review', ?, 'reject')
            """,
            (
                project,
                '{"proposal_id":"proposal:first","proposal_id":"proposal:last"}',
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ambiguous"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-nested-ambiguous', ?, 'proposal_review', ?, 'reject')
            """,
            (
                project,
                '{"proposal_id":"proposal:nested","kind":"low_confidence_main",'
                '"events":[{"summary":"first","summary":"last"}]}',
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ambiguous"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-escaped-key', ?, 'proposal_review', ?, 'reject')
            """,
            (
                project,
                '{"\\u0070roposal_id":"proposal:escaped",'
                '"kind":"low_confidence_main"}',
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ambiguous"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-overflow', ?, 'proposal_review', ?, 'reject')
            """,
            (
                project,
                '{"proposal_id":"proposal:overflow",'
                '"kind":"low_confidence_main",'
                '"events":[{"x":9223372036854775808}]}',
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="ambiguous"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES ('decision:audit-surrogate', ?, 'proposal_review', ?, 'reject')
            """,
            (
                project,
                '{"proposal_id":"proposal:surrogate",'
                '"kind":"low_confidence_main",'
                '"events":[{"summary":"\\ud800"}]}',
            ),
        )
    conn.rollback()


def test_proposal_audit_trigger_rejects_missing_required_payload_key(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    proposal_id = "proposal:audit-null-guard"
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (?,?,?,?)",
        (proposal_id, project, "low_confidence_main", "[{}]"),
    )
    envelope = json.dumps(
        {
            "payload": {
                "action": "accept",
                "status": "ACCEPTED",
                "kind": "low_confidence_main",
                "canon_version": 1,
            }
        },
        sort_keys=True,
    )

    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            """
            UPDATE proposal_set
            SET status = 'ACCEPTED',
                resolution_action = 'accept',
                resolved_canon_version = 1,
                audit_envelope_json = ?
            WHERE id = ?
            """,
            (envelope, proposal_id),
        )

    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            """
            INSERT INTO proposal_set (
                id, project_id, kind, items_json, status, resolution_action,
                resolved_canon_version, audit_envelope_json
            ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'ACCEPTED', 'accept', 1, ?)
            """,
            ("proposal:audit-null-guard-insert", project, envelope),
        )
    conn.rollback()


@pytest.mark.parametrize("column", ["id", "ts", "subject_name", "actor"])
def test_proposal_review_trigger_rejects_invalid_utf8_decision_fields(
    conn: sqlite3.Connection,
    project: str,
    column: str,
) -> None:
    proposal_id = f"proposal:future-invalid-utf8-{column}"
    payload = json.dumps(
        {
            "proposal_id": proposal_id,
            "action": "reject",
            "status": "REJECTED",
            "kind": "low_confidence_main",
            "canon_version": 0,
        },
        sort_keys=True,
    )
    values: dict[str, object] = {
        "id": f"decision:future-invalid-utf8-{column}",
        "ts": "2026-08-03T12:00:00.000Z",
        "subject_name": "顾清音",
        "actor": "author",
    }
    values[column] = b"\xff"
    field_names = tuple(values)
    placeholders = [
        "CAST(? AS TEXT)" if field == column else "?" for field in field_names
    ]

    with pytest.raises(sqlite3.IntegrityError, match="metadata is invalid"):
        conn.execute(
            f"""
            INSERT INTO decision_log (
                {", ".join(field_names)}, project_id, kind, payload_json, decision
            ) VALUES (
                {", ".join(placeholders)}, ?, 'proposal_review', ?, 'reject'
            )
            """,
            (*values.values(), project, payload),
        )
    conn.rollback()


def test_decision_kind_must_be_real_utf8_text_not_a_blob_discriminator(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    payload = json.dumps(
        {
            "proposal_id": "proposal:blob-kind",
            "action": "reject",
            "status": "REJECTED",
            "kind": "low_confidence_main",
            "canon_version": 0,
        },
        sort_keys=True,
    )

    with pytest.raises(sqlite3.IntegrityError, match="kind must be UTF-8 TEXT"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES (?, ?, CAST(? AS BLOB), ?, 'reject')
            """,
            ("decision:blob-kind", project, b"proposal_review", payload),
        )
    conn.rollback()


def test_legacy_utf8_blob_kind_occupies_the_proposal_history_slot(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    proposal_id = "proposal:legacy-blob-kind-slot"
    payload_data = {
        "proposal_id": proposal_id,
        "action": "reject",
        "status": "REJECTED",
        "kind": "low_confidence_main",
        "canon_version": 0,
    }
    payload = json.dumps(payload_data, sort_keys=True)
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (?,?,?,?)",
        (proposal_id, project, "low_confidence_main", "[{}]"),
    )
    conn.execute("DROP TRIGGER decision_log_kind_text_insert")
    conn.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES (?, ?, CAST(? AS BLOB), ?, 'reject')
        """,
        ("decision:legacy-blob-kind-slot", project, b"proposal_review", payload),
    )
    conn.execute(
        """
        UPDATE proposal_set
        SET status = 'REJECTED', resolution_action = 'reject',
            resolved_canon_version = 0, audit_envelope_json = ?
        WHERE id = ?
        """,
        (json.dumps({"payload": payload_data}, sort_keys=True), proposal_id),
    )

    with pytest.raises(sqlite3.IntegrityError, match="already exists"):
        conn.execute(
            """
            INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
            VALUES (?, ?, 'proposal_review', ?, 'reject')
            """,
            ("decision:text-kind-must-not-duplicate", project, payload),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM decision_log WHERE project_id = ?", (project,)
    ).fetchone()[0] == 1
    conn.rollback()


@pytest.mark.parametrize("corruption", ["bad-hash", "bad-chapter", "bad-para"])
def test_proposal_review_trigger_rejects_invalid_anchor_fields(
    conn: sqlite3.Connection,
    project: str,
    corruption: str,
) -> None:
    proposal_id = f"proposal:future-invalid-anchor-{corruption}"
    payload = json.dumps(
        {
            "proposal_id": proposal_id,
            "action": "reject",
            "status": "REJECTED",
            "kind": "low_confidence_main",
            "canon_version": 0,
        },
        sort_keys=True,
    )
    quote_text = "abc" if corruption == "bad-hash" else None
    quote_sha256 = "0" * 64 if corruption == "bad-hash" else None
    chapter_number = 0 if corruption == "bad-chapter" else None
    para_index = -1 if corruption == "bad-para" else None

    with pytest.raises(sqlite3.IntegrityError, match="metadata is invalid"):
        conn.execute(
            """
            INSERT INTO decision_log (
                id, project_id, kind, payload_json, decision, quote_text,
                quote_sha256, chapter_number, para_index
            ) VALUES (?, ?, 'proposal_review', ?, 'reject', ?, ?, ?, ?)
            """,
            (
                f"decision:future-invalid-anchor-{corruption}",
                project,
                payload,
                quote_text,
                quote_sha256,
                chapter_number,
                para_index,
            ),
        )
    conn.rollback()


def test_proposal_audit_trigger_rejects_forged_attachment_on_insert(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    decision_payload = {
        "proposal_id": "proposal:real-audit-owner",
        "action": "reject",
        "status": "REJECTED",
        "kind": "low_confidence_main",
        "canon_version": 0,
    }
    conn.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, resolution_action,
            resolved_canon_version, audit_envelope_json
        ) VALUES (
            'proposal:real-audit-owner', ?, 'low_confidence_main', '[{}]',
            'REJECTED', 'reject', 0, ?
        )
        """,
        (project, json.dumps({"payload": decision_payload}, sort_keys=True)),
    )
    conn.execute(
        """
        INSERT INTO decision_log (id, project_id, kind, payload_json, decision)
        VALUES ('decision:forged-insert', ?, 'proposal_review', ?, 'reject')
        """,
        (project, json.dumps(decision_payload, sort_keys=True)),
    )
    forged_payload = {**decision_payload, "proposal_id": "proposal:forged-insert"}
    envelope = json.dumps({"payload": forged_payload}, sort_keys=True)

    with pytest.raises(sqlite3.IntegrityError, match="durable audit envelope"):
        conn.execute(
            """
            INSERT INTO proposal_set (
                id, project_id, kind, items_json, status, resolution_action,
                resolved_canon_version, audit_envelope_json, decision_log_id
            ) VALUES (
                'proposal:forged-insert', ?, 'low_confidence_main', '[{}]',
                'REJECTED', 'reject', 0, ?, 'decision:forged-insert'
            )
            """,
            (project, envelope),
        )
    conn.rollback()


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate-key",
        "duplicate-envelope",
        "duplicate-nested",
        "duplicate-top-field",
        "escaped-key",
        "lone-surrogate",
        "overflow-integer",
        "boolean-version",
    ],
)
def test_proposal_audit_trigger_rejects_ambiguous_payload(
    conn: sqlite3.Connection,
    project: str,
    corruption: str,
) -> None:
    proposal_id = f"proposal:audit-ambiguous-{corruption}"
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (?,?,?,?)",
        (proposal_id, project, "low_confidence_main", "[{}]"),
    )
    if corruption == "duplicate-key":
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","proposal_id":"proposal:other","action":"accept",'
            '"status":"ACCEPTED","kind":"low_confidence_main",'
            '"canon_version":1}}'
        )
    elif corruption == "boolean-version":
        envelope = json.dumps(
            {
                "payload": {
                    "proposal_id": proposal_id,
                    "action": "accept",
                    "status": "ACCEPTED",
                    "kind": "low_confidence_main",
                    "canon_version": True,
                }
            },
            sort_keys=True,
        )
    elif corruption == "duplicate-envelope":
        valid_payload = json.dumps(
            {
                "proposal_id": proposal_id,
                "action": "accept",
                "status": "ACCEPTED",
                "kind": "low_confidence_main",
                "canon_version": 1,
            },
            sort_keys=True,
        )
        envelope = (
            '{"payload":'
            + valid_payload
            + ',"payload":{"proposal_id":"proposal:other"}}'
        )
    elif corruption == "duplicate-nested":
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"summary":"first","summary":"last"}]}}'
        )
    elif corruption == "duplicate-top-field":
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1},'
            '"subject_name":"first","subject_name":"last"}'
        )
    elif corruption == "escaped-key":
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","\\u0061ction":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1}}'
        )
    elif corruption == "overflow-integer":
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"x":9223372036854775808}]}}'
        )
    else:
        envelope = (
            '{"payload":{"proposal_id":"'
            + proposal_id
            + '","action":"accept","status":"ACCEPTED",'
            '"kind":"low_confidence_main","canon_version":1,'
            '"events":[{"summary":"\\ud800"}]}}'
        )

    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            """
            UPDATE proposal_set
            SET status = 'ACCEPTED', resolution_action = 'accept',
                resolved_canon_version = 1, audit_envelope_json = ?
            WHERE id = ?
            """,
            (envelope, proposal_id),
        )
    conn.rollback()


@pytest.mark.parametrize("corruption", ["unexpected", "bad-hash", "bad-chapter"])
def test_proposal_audit_trigger_rejects_invalid_top_envelope(
    conn: sqlite3.Connection,
    project: str,
    corruption: str,
) -> None:
    proposal_id = f"proposal:audit-top-{corruption}"
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (?,?,?,?)",
        (proposal_id, project, "low_confidence_main", "[{}]"),
    )
    envelope: dict[str, object] = {
        "payload": {
            "proposal_id": proposal_id,
            "action": "accept",
            "status": "ACCEPTED",
            "kind": "low_confidence_main",
            "canon_version": 1,
        }
    }
    if corruption == "unexpected":
        envelope["unexpected"] = 1
    elif corruption == "bad-hash":
        envelope["quote_text"] = "abc"
        envelope["quote_sha256"] = "0" * 64
    else:
        envelope["chapter_number"] = 0

    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            """
            UPDATE proposal_set
            SET status = 'ACCEPTED', resolution_action = 'accept',
                resolved_canon_version = 1, audit_envelope_json = ?
            WHERE id = ?
            """,
            (json.dumps(envelope, sort_keys=True), proposal_id),
        )
    conn.rollback()


@pytest.mark.parametrize("column", ["id", "project_id"])
def test_terminal_proposal_identity_is_immutable(
    conn: sqlite3.Connection,
    project: str,
    column: str,
) -> None:
    proposal_id = f"proposal:immutable-{column}"
    payload = {
        "proposal_id": proposal_id,
        "action": "reject",
        "status": "REJECTED",
        "kind": "low_confidence_main",
        "canon_version": 0,
    }
    conn.execute(
        """
        INSERT INTO proposal_set (
            id, project_id, kind, items_json, status, resolution_action,
            resolved_canon_version, audit_envelope_json
        ) VALUES (?, ?, 'low_confidence_main', '[{}]', 'REJECTED', 'reject', 0, ?)
        """,
        (proposal_id, project, json.dumps({"payload": payload}, sort_keys=True)),
    )
    if column == "project_id":
        new_value = "project:immutable-other"
        conn.execute(
            "INSERT INTO project (id, name, root_path) VALUES (?, '另一本书', '/other')",
            (new_value,),
        )
    else:
        new_value = "proposal:immutable-renamed"

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            f"UPDATE proposal_set SET {column} = ? WHERE id = ?",
            (new_value, proposal_id),
        )
    conn.rollback()


def test_proposal_audit_trigger_rejects_null_id(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        conn.execute(
            "INSERT INTO proposal_set (id, project_id, kind, items_json) VALUES (NULL,?,?,?)",
            (project, "low_confidence_main", "[{}]"),
        )
    conn.rollback()


# ══════════════════════════════════════════════════════════════════════════
# connect 的两条 PRAGMA：缺一条 schema 就不成立
# ══════════════════════════════════════════════════════════════════════════


def test_connect_enables_foreign_keys(conn: sqlite3.Connection) -> None:
    # SQLite 默认**关闭**外键。不设它，001_init.sql 里每一条 REFERENCES 都只是注释。
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_registers_strict_json_canonicalizer(conn: sqlite3.Connection) -> None:
    canonical = conn.execute(
        "SELECT nh_json_canonical(?)",
        ('{"z":{"b":2,"a":1},"a":"青云"}',),
    ).fetchone()[0]
    assert canonical == '{"a":"青云","z":{"a":1,"b":2}}'
    unsafe = (
        '{"x":1,"x":2}',
        '{"x":9223372036854775808}',
        '{"x":"\\ud800"}',
        '{"x":1e999}',
    )
    assert all(
        conn.execute("SELECT nh_json_canonical(?)", (raw,)).fetchone()[0] is None
        for raw in unsafe
    )
    canonical_blob = conn.execute(
        "SELECT nh_json_canonical(CAST(? AS BLOB))",
        (b'{"z":2,"a":1}',),
    ).fetchone()[0]
    assert canonical_blob == '{"a":1,"z":2}'
    assert conn.execute(
        "SELECT nh_json_canonical(CAST(? AS BLOB))", (b'{"x":"\xff"}',)
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT nh_sha256_text(CAST(? AS BLOB))", ("原文".encode(),)
    ).fetchone()[0] == quote_hash("原文")
    assert conn.execute(
        "SELECT nh_sha256_text(CAST(? AS BLOB))", (b"\xff",)
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT nh_utf8_text(CAST(? AS BLOB))", ("顾清音".encode(),)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT nh_utf8_text(CAST(? AS BLOB))", (b"\xff",)
    ).fetchone()[0] == 0


def test_connect_sets_wal(tmp_path: Path) -> None:
    c = connect(tmp_path / "nh.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    c.close()


def test_connect_survives_a_busy_database_during_wal_switch(tmp_path: Path) -> None:
    """**首次运行的并发竞态。** `PRAGMA journal_mode = WAL` 需要独占锁，而它
    **不走 busy handler**——实测它 0.00s 就返回 SQLITE_BUSY，同一条连接上的普通写
    则老老实实等满 `BUSY_TIMEOUT_MS`。

    也就是说这条 PRAGMA 恰好**绕开**了 busy_timeout 想提供的保护：两个进程同时打开一个
    全新的库（API server 和 watchdog 各 connect() 一次就够），其中一个会崩在 connect()
    里，而且崩在 `migrate()` 有机会排队之前。首次运行正是 `uvx novel-harness` 的第一
    印象，也正是 §8 M0 的验收。

    这里用一条持着 BEGIN IMMEDIATE 的连接把库占住来复现那个窗口：connect() 必须
    **拿得到一条能用的连接**，而不是抛 `database is locked`。
    """
    path = tmp_path / "nh.db"
    holder = sqlite3.connect(path)
    holder.execute("PRAGMA journal_mode = DELETE")  # 让库确实不是 WAL，还原首跑那一刻
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("CREATE TABLE squat (x)")
    try:
        c = connect(path)  # 修之前：sqlite3.OperationalError: database is locked
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        c.close()
    finally:
        holder.rollback()
        holder.close()


def test_concurrent_first_migrate_does_not_race(tmp_path: Path) -> None:
    """**闸门本身也race。** 两个进程都在事务外读到 `user_version = 0`，然后先后进 DDL，
    后者必然撞 `table project already exists`——实测 6 进程用 barrier 对齐时 6 个挂 5 个。

    `BEGIN IMMEDIATE` 单独治不好它（排队之后那个陈旧的 0 还在手里），所以 `_apply` 的
    判据是「事务整个回滚了 + 版本真的推进到了 = 我们只是迟到了」。**判据必须是版本，
    不是异常长得像 already exists**——后者会把一个真写坏了的迁移一起放行。

    用线程 + barrier 逼出那个窗口（每条连接各自独立，与多进程同构）。
    """
    path = tmp_path / "nh.db"
    connect(path).close()  # 先把库落到磁盘上，让 4 条连接开在同一个文件上
    n = 4
    barrier = threading.Barrier(n)
    results: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        c = connect(path)
        try:
            barrier.wait()
            v = migrate(c)
        except Exception as exc:  # noqa: BLE001 —— 要的就是「任何异常都算失败」
            v = exc
        finally:
            c.close()
        with lock:
            results.append(v)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [3] * n, f"并发首跑必须全部成功，实得 {results}"
    c = connect(path)
    assert user_version(c) == 3
    assert c.execute("SELECT COUNT(*) FROM edge_type").fetchone()[0] == 9
    c.close()


def test_connect_in_memory_works(tmp_path: Path) -> None:
    # 内存库不支持 WAL，会静默停在 memory 模式。这没关系（没有并发读者），
    # 但 connect() 不能因此炸——测试和 CLI 的 --dry-run 都走这条。
    c = connect(IN_MEMORY)
    assert migrate(c) == 3
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_connect_creates_parent_dirs(tmp_path: Path) -> None:
    c = connect(tmp_path / "a" / "b" / "nh.db")
    assert migrate(c) == 3
    c.close()


def test_foreign_keys_actually_bite(conn: sqlite3.Connection, project: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO node (id, project_id, label, name) VALUES (?, ?, ?, ?)",
            ("x", "project:不存在", "Character", "萧决"),
        )


# ══════════════════════════════════════════════════════════════════════════
# 约束电池：被 SQL 拒绝的
# ══════════════════════════════════════════════════════════════════════════


def test_reject_unknown_node_label(conn: sqlite3.Connection, project: str) -> None:
    # Volume / Scene / Skill / Event 在 v1 没有消费者（ADR 0005 的增长规则）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO node (id, project_id, label, name) VALUES (?, ?, ?, ?)",
            ("n1", project, "Volume", "第二卷"),
        )


def test_reject_invalid_props_json(conn: sqlite3.Connection, project: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
            ("n1", project, "Character", "萧决", "{not json"),
        )


@pytest.mark.parametrize("scope", ["CURRENT", "OPTIONAL", "DRAFT"])
def test_reject_dead_information_scopes(
    conn: sqlite3.Connection, project: str, scope: str
) -> None:
    """CURRENT 是**推导不是存储**（§5.4），OPTIONAL 已永久删除，DRAFT ≡ PROVISIONAL。

    存 CURRENT 就要回答「一条边被 supersede 时谁负责把 CURRENT 摘掉」——那个同步问题
    会在 M4 以误报的形式爆炸，正好是这个产品最怕的东西。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, information_scope=scope)


@pytest.mark.parametrize("edge_type", ["APPEARS_IN", "DOES_NOT_KNOW", "PARTIALLY_KNOWS", "CAUSES"])
def test_reject_edge_types_outside_the_nine(
    conn: sqlite3.Connection, project: str, edge_type: str
) -> None:
    """edge.type 走的是 **FK 到 edge_type 表**，不是 CHECK。

    外键让「一条边的类型没有互斥性语义」在物理上不可能——CHECK 只保证类型名合法，
    保证不了 exclusivity 那一行也在，而 supersede 全靠那一行决定挤掉谁。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, type=edge_type)


def test_edge_type_is_schema_not_data(conn: sqlite3.Connection) -> None:
    """`edge_type` 那 9 行是 **schema，不是数据**。两个动词各自够狠：

    - INSERT 一行 = 复活一个 ADR 0005 判了**永久删除**的类型。`DOES_NOT_KNOW` 是组合
      爆炸炸弹（实体化后额外 67,500 条边），而 edge.type 的外键只查这张表在不在——
      `test_reject_edge_types_outside_the_nine` 测的是「FK 拦住了未登记的类型」，
      它没测「登记表本身是敞开的」。
    - UPDATE 一行 = supersede **静默失效**。`exclusivity='multi'` 之后 upsert_edge 谁都
      不挤，state_at 同时返回「在青云城」和「在北荒」——**这正是 §5.5 逐字点名的那个 fatal**。

    schema 花大力气用 FK 保证「一条边的类型一定有互斥性语义」，却没保证「那个语义是对的」。
    """
    with pytest.raises(sqlite3.IntegrityError, match="写迁移"):
        conn.execute("INSERT INTO edge_type (type, exclusivity) VALUES ('DOES_NOT_KNOW','multi')")
    with pytest.raises(sqlite3.IntegrityError, match="写迁移"):
        conn.execute("UPDATE edge_type SET exclusivity = 'multi' WHERE type = 'LOCATED_AT'")
    with pytest.raises(sqlite3.IntegrityError, match="写迁移"):
        conn.execute("DELETE FROM edge_type WHERE type = 'KNOWS'")
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM edge_type").fetchone()[0] == 9
    row = conn.execute("SELECT exclusivity FROM edge_type WHERE type='LOCATED_AT'").fetchone()
    assert row["exclusivity"] == "single_per_src"


def test_reject_wrong_label_in_secret_table(conn: sqlite3.Connection, project: str) -> None:
    """一个 `label='Character'` 的节点**不许**登记成秘密。

    头注释第 3 条把「扩展表主键 = node.id」称作让 §8 / §5.8 / Day 5 三处自洽的唯一读法，
    但那条读法在外键上只成立了一半：`REFERENCES node(id)` 保证 secret.id 是**某个** node，
    没保证它是一个 **Secret** node。之后 §8 Day 5 的矩阵 SQL（`JOIN secret s ON k.dst=s.id`）
    会把一个**人**当成秘密列进面板的列头——而 R1 的卖点是「零误报」。

    sqlite_store 的运行时 label 校验拦得住它，代价是**整个项目**的面板一起黑掉
    （secrets=None 是面板唯一路径：先取全表再逐个校验），且错误指向 s:fake 而不是导入器。
    一行坏数据不该有全项目的爆炸半径。
    """
    c = _node(conn, project, "Character", "萧决")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO secret (id, project_id, description) VALUES (?,?,'')", (c, project)
        )


def test_reject_wrong_label_in_chapter_table(conn: sqlite3.Connection, project: str) -> None:
    loc = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
            (loc, project, 1, "chapters/1.md", "c" * 64),
        )


def test_reject_self_loop_edge(conn: sqlite3.Connection, project: str) -> None:
    # LOCATED_AT(萧决, 萧决) / KNOWS(萧决, 萧决)：v1 的 9 类关系没有一条有合法自环。
    a = _node(conn, project, "Character", "萧决")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, a)


@pytest.mark.parametrize("table", ["edge_src", "edge_dst", "alias", "secret", "chapter"])
def test_reject_cross_project_reference(
    conn: sqlite3.Connection, project: str, table: str
) -> None:
    """跨项目引用从「§5.3 的日志里一眼可见」升级成**物理上不可能**。

    `edge` / `alias` / `chapter` / `secret` 都自带一列 `project_id REFERENCES project(id)`，
    但曾经没有任何约束要求它等于所引节点的 `node.project_id`。四张表都能写出跨项目的行，
    而且后果是**静默**的：一条 project_id=B、两端却都属于 A 的边对 `state_at(A)` 永久
    隐形（全部时态查询都带 `project_id = :pid`）；一条 project_id=B 指向 A 的节点的别名
    对 A 的 mention 匹配永久隐形。

    store 层的 `_require_node` 挡得住其中一部分（可达面很小，所以这条定 minor），
    但那是**应用层兜住了**，而这份 schema 在别处（edge.type→edge_type）明确选择了
    「事前不可能」而不是「事后可见」。
    """
    conn.execute("INSERT INTO project (id, name, root_path) VALUES ('project:B', '乙', '/b')")
    mine = _node(conn, project, "Character", "萧决")
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES ('nB','project:B','Location','北荒')"
    )
    theirs = "nB"

    with pytest.raises(sqlite3.IntegrityError):
        if table == "edge_src":
            # 边的 project_id=B、两端却都属于 A：这条边对 state_at(A) 永久隐形。
            _edge(conn, "project:B", mine, _node(conn, project, "Location", "青云城"))
        elif table == "edge_dst":
            _edge(conn, project, mine, theirs)
        elif table == "alias":
            conn.execute(
                "INSERT INTO alias (id, project_id, node_id, surface, kind)"
                " VALUES ('a1','project:B',?,'清音仙子','alias')",
                (mine,),
            )
        elif table == "secret":
            s = _node(conn, project, "Secret", "血脉秘密")
            conn.execute(
                "INSERT INTO secret (id, project_id, description) VALUES (?,'project:B','')", (s,)
            )
        else:
            ch = _node(conn, project, "Chapter", "第一章")
            conn.execute(
                "INSERT INTO chapter (id, project_id, number, path, text_sha256)"
                " VALUES (?,'project:B',1,'chapters/1.md',?)",
                (ch, "c" * 64),
            )


def test_reject_duplicate_state_dim_key(conn: sqlite3.Connection, project: str) -> None:
    """**一个 dim_key 一个节点。**

    两个 StateDim 节点共享 `dim_key='health'`（「健康」和「生死」）时，supersede 按 dst 的
    **节点 id** 找冲突边、不是按 dim_key，于是「ch89 死、ch100 复活」两条边谁也不闭合谁，
    `StateSnapshot.is_dead` 的 `any()` 让 dead 永远压过 alive → R3 对全书每一句
    「萧决道：」报死人说话 → M3 的「误报 <1 条/章」当场崩。

    而 `dim_key` 存在的理由恰恰是「作者随时会把 name 从『健康』改成『生死』」——
    即「同一维度有多个表述」是被**预期**的，只是预期在 name 上，不在节点上。
    """
    conn.execute(
        "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
        ("d1", project, "StateDim", "健康", '{"dim_key": "health"}'),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
            ("d2", project, "StateDim", "生死", '{"dim_key": "health"}'),
        )


def test_allow_state_dims_with_different_keys_and_no_key(
    conn: sqlite3.Connection, project: str
) -> None:
    """反面：不同 dim_key 必须共存（修为 / 健康），**没有 dim_key 的也必须能存多个**。

    那条唯一索引带 `WHERE ... IS NOT NULL`，因为「没有键」不是一个维度身份——
    ADR 0005 的增长规则说没有规则消费就不该有键，而 SQLite 里多个 NULL 本就不冲突。
    这条钉的是那半个 WHERE：删掉它，第二个无键 StateDim 就进不来了。
    """
    for i, (name, props) in enumerate(
        [("健康", '{"dim_key": "health"}'), ("修为", '{"dim_key": "cultivation"}'),
         ("心境", "{}"), ("气运", "{}")]
    ):
        conn.execute(
            "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
            (f"d{i}", project, "StateDim", name, props),
        )
    assert conn.execute("SELECT COUNT(*) FROM node WHERE label='StateDim'").fetchone()[0] == 4


def test_reject_duplicate_chapter_path(conn: sqlite3.Connection, project: str) -> None:
    """与 `UNIQUE(project_id, number)` 同一类绊线、同一个论证。

    正文在磁盘上（ADR 0007），watchdog 监视文件、**按 path 反查章节**、触发 revalidate。
    两章共用 `chapters/151.md` 时「这个文件改了 → 该更新哪一章的 text_sha256 / 该重算
    哪一章的证据」没有确定答案。而 M0 的验收是「切出章数与目录数完全一致」——
    一个把两章切到同一个 path 的导入 bug 恰好不会被那条验收抓到（章数对得上），
    却会在 M1 变成一个查不出来源的面板 bug。
    """
    c1 = _node(conn, project, "Chapter", "第一章")
    c2 = _node(conn, project, "Chapter", "第二章")
    conn.execute(
        "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
        (c1, project, 1, "chapters/151.md", "c" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
            (c2, project, 2, "chapters/151.md", "c" * 64),
        )


def test_reject_empty_chapter_path(conn: sqlite3.Connection, project: str) -> None:
    # path 是 NOT NULL 但可以是 ''，而 '' 是「导入器没填」的形状，不是一个路径。
    c1 = _node(conn, project, "Chapter", "第一章")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
            (c1, project, 1, "", "c" * 64),
        )


def test_reject_status_superseded(conn: sqlite3.Connection, project: str) -> None:
    # 「被挤掉」已经由 valid_to_chapter 表达了。再存一个状态位 = 把刚砍掉的 CURRENT
    # 同步问题原样请回来。
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, status="SUPERSEDED")


@pytest.mark.parametrize("valid_to", [10, 9, 0])
def test_reject_empty_interval(conn: sqlite3.Connection, project: str, valid_to: int) -> None:
    """空区间 = 「这条事实从未成立」= supersede 的 bug，不是一条事实。

    这条 CHECK 逼出了 RETRACTED 的设计：同章更正（「他在青云城…然后去了北荒」都在
    ch151）不能闭合成 [151,151)，只能撤回。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, valid_from_chapter=10, valid_to_chapter=valid_to)


def test_accept_half_open_interval(conn: sqlite3.Connection, project: str) -> None:
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    _edge(conn, project, a, b, valid_from_chapter=10, valid_to_chapter=143)  # 不抛


def test_reject_evidence_status_without_evidence(conn: sqlite3.Connection, project: str) -> None:
    """两列同生同死。

    这是 schema 里最危险的一个坑：§5.5 的 state_at 写的是 `evidence_status != 'STALE'`，
    而 SQL 里 `NULL != 'STALE'` 求值为 NULL 即假——若这列可空，那条查询会**静默丢掉
    每一条作者声明的无证据边**，而作者声明正是整个产品（ADR 0004）。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, evidence_status="FRESH")  # evidence_id 是 NULL


def test_reject_evidence_without_status(conn: sqlite3.Connection, project: str, tmp_path) -> None:
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    ev = _evidence(conn, project, a)
    with pytest.raises(sqlite3.IntegrityError):
        # 有证据却说自己 'NONE'
        _edge(conn, project, a, b, evidence_id=ev, evidence_status="NONE")


def test_no_evidence_edge_survives_the_stale_filter(
    conn: sqlite3.Connection, project: str
) -> None:
    """上一条的正面：哨兵值 'NONE' 让作者声明的无证据边正确通过 state_at 的过滤。

    这条是那个坑的**回归测试**——它长得像在测 SQLite 的三值逻辑，实际测的是
    「面板会不会整片空白且没有任何报错」。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    _edge(conn, project, a, b, valid_from_chapter=10)
    rows = conn.execute(
        "SELECT id FROM edge WHERE src = ? AND evidence_status != 'STALE'", (a,)
    ).fetchall()
    assert len(rows) == 1


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_reject_confidence_out_of_range(
    conn: sqlite3.Connection, project: str, confidence: float
) -> None:
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, confidence=confidence)


def test_reject_duplicate_idempotency_key(conn: sqlite3.Connection, project: str) -> None:
    """upsert_edge 的幂等键（§2.2a「幂等变成一个唯一索引」）。这就是那个索引。"""
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    _edge(conn, project, a, b, valid_from_chapter=10, information_scope="CANON")
    with pytest.raises(sqlite3.IntegrityError):
        _edge(conn, project, a, b, valid_from_chapter=10, information_scope="CANON")


def test_reject_short_usable_alias(conn: sqlite3.Connection, project: str) -> None:
    """ADR 0004 说「2 字以下别名 UI 层直接拒绝录入」——UI 层的规矩能被绕过
    （脚本导入、测试 fixture、M4 抽取器），这条不能。「音」去匹配「琴声清音袅袅」是灾难。"""
    n = _node(conn, project, "Character", "顾清音")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules)"
            " VALUES (?,?,?,?,?,1)",
            ("a1", project, n, "音", "nickname"),
        )


def test_reject_second_canonical_alias(conn: sqlite3.Connection, project: str) -> None:
    n = _node(conn, project, "Character", "顾清音")
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,'canonical')",
        ("a1", project, n, "顾清音"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind)"
            " VALUES (?,?,?,?,'canonical')",
            ("a2", project, n, "清音仙子"),
        )


def test_reject_duplicate_chapter_number(conn: sqlite3.Connection, project: str) -> None:
    """导入期的绊线（§8 已为它预留半天）。

    chapter.number 是**全书顺序位置**，不是正文里印的章号——印号会因分卷重启和番外
    重复，而 state_at 的 `valid_from_chapter <= :ch` 要求它是全序键。让脏数据在导入时炸，
    好过在 M1 变成一个查不出来源的面板 bug。
    """
    _chapter(conn, project, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _chapter(conn, project, 1)


def test_reject_chapter_number_zero(conn: sqlite3.Connection, project: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _chapter(conn, project, 0)


def test_reject_duplicate_snapshot(conn: sqlite3.Connection, project: str) -> None:
    """§6 S3 的「插之前比 text_sha256 去重」变成数据库强制：debounce 每 800ms 插一行 = GB 级。

    快照是证据的不可变锚，不是版本历史——同内容只需要存在一次，所以丢掉
    「作者改回 A 又改回来」的时间线是正确的，不是有损的。
    """
    ch = _chapter(conn, project, 1)
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        ("s1", ch, "正文", "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
            ("s2", ch, "正文", "a" * 64),
        )


def test_reject_bad_quote_sha256_length(conn: sqlite3.Connection, project: str) -> None:
    ch = _chapter(conn, project, 1)
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        ("s1", ch, "正文", "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index, quote_text,"
            " quote_sha256, chapter_id, para_index_hint) VALUES (?,?,?,?,?,?,?,?)",
            ("e1", project, "s1", 0, "他死了", "abc", ch, 0),
        )


def test_reject_negative_para_index(conn: sqlite3.Connection, project: str) -> None:
    # 0-based 全库统一（ADR 0006）。-1 是「没找到」在别的语言里的写法，不是一个段号。
    ch = _chapter(conn, project, 1)
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        ("s1", ch, "正文", "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index, quote_text,"
            " quote_sha256, chapter_id, para_index_hint) VALUES (?,?,?,?,?,?,?,?)",
            ("e1", project, "s1", -1, "他死了", "b" * 64, ch, 0),
        )


def test_reject_self_sub_secret(conn: sqlite3.Connection, project: str) -> None:
    s = _node(conn, project, "Secret", "主角是魔尊转世")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO secret (id, project_id, description, sub_of) VALUES (?,?,?,?)",
            (s, project, "", s),
        )


def test_reject_bad_decision_verdict(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decision_log (id, project_id, kind, payload_json, decision)"
            " VALUES (?,?,?,?,?)",
            ("d1", "p", "alias_merge", "{}", "maybe"),
        )


def test_reject_bad_proposal_status(conn: sqlite3.Connection, project: str) -> None:
    # CHECK 死是因为 UI 只查 status='PENDING'：写错一个值 = 这一聚类**永远不再露面**。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proposal_set (id, project_id, kind, status) VALUES (?,?,?,?)",
            ("ps1", project, "alias_cluster", "OPEN"),
        )


def test_decision_log_is_append_only(conn: sqlite3.Connection) -> None:
    """引擎层的 append-only（decisions.py 的 API 面是另一层）。

    纪律会在某个赶时间的下午被绕过；触发器不会。
    """
    conn.execute(
        "INSERT INTO decision_log (id, project_id, kind, payload_json, decision)"
        " VALUES ('d1','p','alias_merge','{}','accept')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="只增不改"):
        conn.execute("UPDATE decision_log SET decision = 'reject' WHERE id = 'd1'")
    with pytest.raises(sqlite3.IntegrityError, match="只增不删"):
        conn.execute("DELETE FROM decision_log WHERE id = 'd1'")


def test_decision_log_survives_project_delete(conn: sqlite3.Connection, project: str) -> None:
    """`decision_log` 一个外键都没有——**连 project_id 都没有**。

    它必须比它记录的一切活得更久。指向 project 的外键会让「删掉项目」顺手删掉
    唯一不可重建的资产，而 ADR 0003 立这张表就是为了防这个。
    """
    conn.execute(
        "INSERT INTO decision_log (id, project_id, kind, payload_json, decision)"
        " VALUES (?,?,?,?,?)",
        ("d1", project, "knows_declare", "{}", "accept"),
    )
    _node(conn, project, "Character", "萧决")
    conn.execute("DELETE FROM project WHERE id = ?", (project,))
    # 节点跟着项目走了（CASCADE），确认留下了。
    assert conn.execute("SELECT COUNT(*) FROM node").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 1


def test_evidence_cannot_be_deleted_while_referenced(
    conn: sqlite3.Connection, project: str
) -> None:
    # v1 evidence 行永不删除：edge.evidence_id 是 NO ACTION，删被引用的 evidence 会 FK 报错。
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    ev = _evidence(conn, project, a)
    _edge(conn, project, a, b, evidence_id=ev, evidence_status="FRESH")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM evidence WHERE id = ?", (ev,))


# ══════════════════════════════════════════════════════════════════════════
# M4：事件超边 / 提案关联 / 后台抽取
# ══════════════════════════════════════════════════════════════════════════


def test_story_event_copies_the_temporal_graph_checks(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    _story_event(conn, project, "event:valid", evidence_id)

    with pytest.raises(sqlite3.IntegrityError):
        _story_event(conn, project, "event:empty", evidence_id, summary="", information_scope="CANON")
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(conn, project, "event:scope", evidence_id, information_scope="DRAFT")
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(
            conn,
            project,
            "event:interval",
            evidence_id,
            information_scope="CANON",
            valid_to_chapter=143,
        )
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(
            conn,
            project,
            "event:status",
            evidence_id,
            information_scope="CANON",
            status="SUPERSEDED",
        )
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(
            conn,
            project,
            "event:confidence",
            evidence_id,
            information_scope="CANON",
            confidence=1.01,
        )
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(
            conn,
            project,
            "event:evidence",
            evidence_id,
            information_scope="CANON",
            evidence_status="NONE",
        )
    with pytest.raises(sqlite3.IntegrityError):
        _story_event(conn, project, "event:duplicate-anchor", evidence_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT OR REPLACE INTO story_event "
            "(id, project_id, chapter_number, summary, valid_from_chapter, "
            "information_scope, evidence_id) VALUES (?,?,?,?,?,?,?)",
            (
                "event:anchor-replacement",
                project,
                143,
                "另一条事件。",
                143,
                "PROVISIONAL",
                evidence_id,
            ),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM story_event WHERE id = 'event:valid'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["insert", "update"])
def test_story_event_chapter_must_match_its_evidence(
    conn: sqlite3.Connection, project: str, mutation: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")

    with pytest.raises(sqlite3.IntegrityError):
        if mutation == "insert":
            _story_event(
                conn,
                project,
                "event:evidence-chapter-insert",
                evidence_id,
                chapter_number=144,
                valid_from_chapter=144,
            )
        else:
            event_id = "event:evidence-chapter-update"
            _story_event(conn, project, event_id, evidence_id)
            conn.execute(
                "UPDATE story_event SET chapter_number = 144, valid_from_chapter = 144 "
                "WHERE id = ?",
                (event_id,),
            )


@pytest.mark.parametrize("mutation", ["insert", "update"])
def test_event_knower_chapter_must_match_its_evidence(
    conn: sqlite3.Connection, project: str, mutation: str
) -> None:
    event_evidence = _evidence(conn, project, "unused")
    later_evidence = _evidence(conn, project, "unused", chapter_number=144)
    event_id = "event:knower-evidence-chapter"
    _story_event(conn, project, event_id, event_evidence)
    character_id = _node(conn, project, "Character", "顾清音")

    if mutation == "update":
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
            (event_id, project, character_id, 143, "PROVISIONAL", event_evidence, "FRESH"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        if mutation == "insert":
            conn.execute(
                "INSERT INTO event_knower "
                "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
                "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
                (event_id, project, character_id, 143, "PROVISIONAL", later_evidence, "FRESH"),
            )
        else:
            conn.execute(
                "UPDATE event_knower SET evidence_id = ? WHERE event_id = ?",
                (later_evidence, event_id),
            )


def test_event_incidence_requires_same_project_and_expected_labels(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    event_id = "event:incidence"
    _story_event(conn, project, event_id, evidence_id)
    character_id = _node(conn, project, "Character", "顾清音")
    other_character_id = _node(conn, project, "Character", "萧决")
    secret_id = _node(conn, project, "Secret", "玄铁令的来历")

    conn.execute(
        "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?,?,?)",
        (event_id, project, character_id),
    )
    conn.execute(
        "INSERT INTO event_reveal (event_id, project_id, secret_id) VALUES (?,?,?)",
        (event_id, project, secret_id),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?,?,?)",
            (event_id, project, character_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?,?,?)",
            (event_id, project, secret_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_reveal (event_id, project_id, secret_id) VALUES (?,?,?)",
            (event_id, project, character_id),
        )

    other_project = new_project_id()
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (other_project, "别书", "/other")
    )
    foreign_character = _node(conn, other_project, "Character", "别书人物")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?,?,?)",
            (event_id, project, foreign_character),
        )

    conn.execute(
        "INSERT INTO event_knower "
        "(event_id, project_id, character_id, valid_from_chapter, valid_to_chapter, "
        "information_scope, evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?,?)",
        (event_id, project, other_character_id, 143, 200, "PROVISIONAL", evidence_id, "FRESH"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, valid_to_chapter, "
            "information_scope, evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, project, character_id, 143, 143, "PROVISIONAL", evidence_id, "FRESH"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
            (event_id, project, character_id, 142, "CANON", evidence_id, "FRESH"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_status) VALUES (?,?,?,?,?,?)",
            (event_id, project, character_id, 143, "PROVISIONAL", "FRESH"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
            (event_id, project, character_id, 143, "DRAFT", evidence_id, "FRESH"),
        )

    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind) VALUES (?,?,?)",
        ("proposal:event-replace", project, "edge_conflict"),
    )
    conn.execute(
        "INSERT INTO proposal_event (proposal_id, project_id, event_id) VALUES (?,?,?)",
        ("proposal:event-replace", project, event_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO story_event SELECT * FROM story_event WHERE id = ?",
        (event_id,),
    )
    for table in ("event_participant", "event_knower", "event_reveal", "proposal_event"):
        assert conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE event_id = ?", (event_id,)
        ).fetchone()[0] == 1

    snapshot_id = conn.execute(
        "SELECT chapter_snapshot_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO extraction_run "
        "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
        "VALUES (?,?,?,?,?,?)",
        ("run:project-delete", project, 143, snapshot_id, "m4.v1", "prompt-delete"),
    )
    conn.execute("DELETE FROM project WHERE id = ?", (project,))
    for table in (
        "story_event",
        "event_participant",
        "event_knower",
        "event_reveal",
        "proposal_set",
        "proposal_event",
        "extraction_run",
    ):
        assert conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project,)
        ).fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_story_event_delete_is_blocked_while_referenced(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    event_id = "event:delete-blocked"
    _story_event(conn, project, event_id, evidence_id)
    character_id = _node(conn, project, "Character", "顾清音")
    conn.execute(
        "INSERT INTO event_participant (event_id, project_id, character_id) VALUES (?,?,?)",
        (event_id, project, character_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM story_event WHERE id = ?", (event_id,))


def test_event_knower_scope_must_match_its_story_event(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    event_id = "event:provisional-scope"
    _story_event(conn, project, event_id, evidence_id)
    character_id = _node(conn, project, "Character", "顾清音")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
            (event_id, project, character_id, 143, "CANON", evidence_id, "FRESH"),
        )


def test_story_event_cannot_move_after_a_knower_depends_on_its_chapter(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    event_id = "event:parent-update"
    _story_event(conn, project, event_id, evidence_id)
    character_id = _node(conn, project, "Character", "顾清音")
    conn.execute(
        "INSERT INTO event_knower "
        "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
        "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
        (event_id, project, character_id, 143, "PROVISIONAL", evidence_id, "FRESH"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE story_event SET chapter_number = 144, valid_from_chapter = 144 WHERE id = ?",
            (event_id,),
        )


@pytest.mark.parametrize("consumer", ["story_event", "event_knower"])
@pytest.mark.parametrize(
    "mutation", ["update_snapshot", "replace_snapshot", "update_project", "replace_project"]
)
def test_event_temporal_coherence_survives_evidence_parent_mutations(
    conn: sqlite3.Connection,
    project: str,
    consumer: str,
    mutation: str,
) -> None:
    evidence_id, _, _ = _event_evidence_consumer(conn, project, consumer)

    if mutation.endswith("snapshot"):
        replacement_evidence = _evidence(conn, project, "unused", chapter_number=144)
    else:
        other_project = new_project_id()
        conn.execute(
            "INSERT INTO project (id, name, root_path) VALUES (?,?,?)",
            (other_project, "别书", "/other-evidence-parent"),
        )
        replacement_evidence = _evidence(conn, other_project, "unused", chapter_number=143)
    replacement_snapshot, replacement_chapter = conn.execute(
        "SELECT chapter_snapshot_id, chapter_id FROM evidence WHERE id = ?",
        (replacement_evidence,),
    ).fetchone()

    with pytest.raises(sqlite3.IntegrityError):
        if mutation == "update_snapshot":
            conn.execute(
                "UPDATE evidence SET chapter_snapshot_id = ? WHERE id = ?",
                (replacement_snapshot, evidence_id),
            )
        elif mutation == "update_project":
            conn.execute(
                "UPDATE evidence SET project_id = ? WHERE id = ?",
                (other_project, evidence_id),
            )
        else:
            replacement_project = other_project if mutation == "replace_project" else project
            conn.execute(
                "INSERT OR REPLACE INTO evidence "
                "(id, project_id, chapter_snapshot_id, para_index, quote_text, quote_sha256, "
                "chapter_id, para_index_hint, occurrence_k) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    replacement_project,
                    replacement_snapshot,
                    0,
                    "后来得知。",
                    "f" * 64,
                    replacement_chapter,
                    0,
                    0,
                ),
            )


@pytest.mark.parametrize("consumer", ["story_event", "event_knower"])
@pytest.mark.parametrize("mutation", ["update", "replace"])
def test_event_temporal_coherence_survives_snapshot_parent_mutations(
    conn: sqlite3.Connection,
    project: str,
    consumer: str,
    mutation: str,
) -> None:
    _, snapshot_id, _ = _event_evidence_consumer(conn, project, consumer)
    later_chapter_id = _chapter(conn, project, 144)

    with pytest.raises(sqlite3.IntegrityError):
        if mutation == "update":
            conn.execute(
                "UPDATE chapter_snapshot SET chapter_id = ? WHERE id = ?",
                (later_chapter_id, snapshot_id),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO chapter_snapshot (id, chapter_id, text, text_sha256) "
                "VALUES (?,?,?,?)",
                (snapshot_id, later_chapter_id, "后来得知。", "f" * 64),
            )


@pytest.mark.parametrize("consumer", ["story_event", "event_knower"])
@pytest.mark.parametrize("field", ["number", "project_id"])
def test_event_temporal_coherence_survives_chapter_parent_updates(
    conn: sqlite3.Connection,
    project: str,
    consumer: str,
    field: str,
) -> None:
    _, _, chapter_id = _event_evidence_consumer(conn, project, consumer)

    with pytest.raises(sqlite3.IntegrityError):
        if field == "number":
            conn.execute("UPDATE chapter SET number = 144 WHERE id = ?", (chapter_id,))
        else:
            other_project = new_project_id()
            conn.execute(
                "INSERT INTO project (id, name, root_path) VALUES (?,?,?)",
                (other_project, "别书", "/other-chapter-parent"),
            )
            conn.execute(
                "UPDATE chapter SET project_id = ? WHERE id = ?", (other_project, chapter_id)
            )


def test_story_event_allows_evidence_relocation_fields_to_change(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id, _, _ = _event_evidence_consumer(conn, project, "story_event")
    relocated_chapter = _chapter(conn, project, 144)

    conn.execute(
        "UPDATE evidence SET chapter_id = ?, para_index_hint = 7, occurrence_k = 2 "
        "WHERE id = ?",
        (relocated_chapter, evidence_id),
    )

    row = conn.execute(
        "SELECT chapter_id, para_index_hint, occurrence_k FROM evidence WHERE id = ?",
        (evidence_id,),
    ).fetchone()
    assert tuple(row) == (relocated_chapter, 7, 2)


@pytest.mark.parametrize("child_kind", ["proposal", "extraction"])
@pytest.mark.parametrize("parent_update", ["snapshot_chapter", "chapter_number"])
def test_snapshot_coherence_survives_parent_updates(
    conn: sqlite3.Connection,
    project: str,
    child_kind: str,
    parent_update: str,
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    snapshot_id, chapter_id = conn.execute(
        "SELECT chapter_snapshot_id, chapter_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()

    if child_kind == "proposal":
        conn.execute(
            "INSERT INTO proposal_set "
            "(id, project_id, kind, chapter_number, snapshot_id) VALUES (?,?,?,?,?)",
            ("proposal:parent-update", project, "edge_conflict", 143, snapshot_id),
        )
    else:
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
            "VALUES (?,?,?,?,?,?)",
            ("run:parent-update", project, 143, snapshot_id, "m4.v1", "prompt-a"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        if parent_update == "snapshot_chapter":
            later_chapter_id = _chapter(conn, project, 144)
            conn.execute(
                "UPDATE chapter_snapshot SET chapter_id = ? WHERE id = ?",
                (later_chapter_id, snapshot_id),
            )
        else:
            conn.execute("UPDATE chapter SET number = 144 WHERE id = ?", (chapter_id,))


@pytest.mark.parametrize("consumer", ["proposal", "extraction"])
def test_snapshot_replace_preserves_referencing_child_coherence(
    conn: sqlite3.Connection, project: str, consumer: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    snapshot_id, chapter_id = conn.execute(
        "SELECT chapter_snapshot_id, chapter_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()
    if consumer == "proposal":
        conn.execute(
            "INSERT INTO proposal_set "
            "(id, project_id, kind, chapter_number, snapshot_id) VALUES (?,?,?,?,?)",
            ("proposal:replace", project, "edge_conflict", 143, snapshot_id),
        )
    else:
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
            "VALUES (?,?,?,?,?,?)",
            ("run:replace", project, 143, snapshot_id, "m4.v1", "prompt-a"),
        )

    replace_sql = (
        "INSERT OR REPLACE INTO chapter_snapshot (id, chapter_id, text, text_sha256) "
        "VALUES (?,?,?,?)"
    )
    conn.execute(replace_sql, (snapshot_id, chapter_id, "他死了。", "d" * 64))

    later_chapter_id = _chapter(conn, project, 144)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(replace_sql, (snapshot_id, later_chapter_id, "后来得知。", "f" * 64))


def test_proposal_links_only_reference_provisional_rows(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    provisional_event = "event:proposal-provisional"
    canon_event = "event:proposal-canon"
    _story_event(conn, project, provisional_event, evidence_id)
    _story_event(conn, project, canon_event, evidence_id, information_scope="CANON")
    character_id = _node(conn, project, "Character", "顾清音")
    location_id = _node(conn, project, "Location", "渡口")
    provisional_edge = _edge(
        conn,
        project,
        character_id,
        location_id,
        information_scope="PROVISIONAL",
    )
    canon_edge = _edge(conn, project, character_id, location_id, information_scope="CANON")
    conn.execute(
        "INSERT INTO proposal_set (id, project_id, kind, status) VALUES (?,?,?,?)",
        ("proposal:m4", project, "edge_conflict", "PENDING"),
    )

    conn.execute(
        "INSERT INTO proposal_event (proposal_id, project_id, event_id) VALUES (?,?,?)",
        ("proposal:m4", project, provisional_event),
    )
    conn.execute(
        "INSERT INTO proposal_edge (proposal_id, project_id, edge_id) VALUES (?,?,?)",
        ("proposal:m4", project, provisional_edge),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proposal_event (proposal_id, project_id, event_id) VALUES (?,?,?)",
            ("proposal:m4", project, canon_event),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proposal_edge (proposal_id, project_id, edge_id) VALUES (?,?,?)",
            ("proposal:m4", project, canon_edge),
        )


def test_extraction_run_checks_status_counters_and_idempotency(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    snapshot_id = conn.execute(
        "SELECT chapter_snapshot_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO extraction_run "
        "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
        "VALUES (?,?,?,?,?,?)",
        ("run:one", project, 143, snapshot_id, "m4.v1", "prompt-a"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash, status) "
            "VALUES (?,?,?,?,?,?,?)",
            ("run:bad-status", project, 143, snapshot_id, "m4.v2", "prompt-a", "DONE"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
            "VALUES (?,?,?,?,?,?)",
            ("run:duplicate", project, 143, snapshot_id, "m4.v1", "prompt-a"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash, "
            "valid_event_count) VALUES (?,?,?,?,?,?,?)",
            ("run:negative", project, 143, snapshot_id, "m4.v2", "prompt-b", -1),
        )

    other_project = new_project_id()
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (other_project, "别书", "/other")
    )
    foreign_evidence = _evidence(conn, other_project, "unused")
    foreign_snapshot = conn.execute(
        "SELECT chapter_snapshot_id FROM evidence WHERE id = ?", (foreign_evidence,)
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, schema_version, prompt_hash) "
            "VALUES (?,?,?,?,?,?)",
            ("run:foreign", project, 143, foreign_snapshot, "m4.v2", "prompt-c"),
        )

    conn.execute(
        "INSERT INTO model_call (id, project_id, capability, model, prompt_hash) "
        "VALUES (?,?,?,?,?)",
        ("call:referenced", project, "extractor", "test-model", "prompt-model"),
    )
    conn.execute(
        "INSERT INTO extraction_run "
        "(id, project_id, chapter_number, snapshot_id, model_call_id, schema_version, "
        "prompt_hash) VALUES (?,?,?,?,?,?,?)",
        (
            "run:referenced-call",
            project,
            143,
            snapshot_id,
            "call:referenced",
            "m4.v2",
            "prompt-model",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM model_call WHERE id = 'call:referenced'")


def test_extraction_run_model_call_requires_the_same_project(
    conn: sqlite3.Connection, project: str
) -> None:
    evidence_id = _evidence(conn, project, "unused")
    snapshot_id = conn.execute(
        "SELECT chapter_snapshot_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()[0]
    other_project = new_project_id()
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?,?,?)",
        (other_project, "别书", "/other-model-call"),
    )
    conn.execute(
        "INSERT INTO model_call (id, project_id, capability, model, prompt_hash) "
        "VALUES (?,?,?,?,?)",
        ("call:foreign", other_project, "extractor", "test-model", "prompt-a"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO extraction_run "
            "(id, project_id, chapter_number, snapshot_id, model_call_id, schema_version, "
            "prompt_hash) VALUES (?,?,?,?,?,?,?)",
            ("run:foreign-call", project, 143, snapshot_id, "call:foreign", "m4.v1", "prompt-a"),
        )


# ══════════════════════════════════════════════════════════════════════════
# 约束电池：**必须被允许**的（这一组比上面那组更容易在改 schema 时被误伤）
# ══════════════════════════════════════════════════════════════════════════


def test_allow_canon_and_provisional_side_by_side(conn: sqlite3.Connection, project: str) -> None:
    """同一事实的 CANON 行与 PROVISIONAL 行**共存**——三层图谱的地基。

    幂等键里含 information_scope 就是为了这个：抽取器（PROVISIONAL）在物理上碰不到
    作者的 CANON 行，原则 5「Agent 不得直接修改正式 Canon」由此从纪律变成数据库约束。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Location", "青云城")
    _edge(conn, project, a, b, valid_from_chapter=10, information_scope="CANON")
    _edge(conn, project, a, b, valid_from_chapter=10, information_scope="PROVISIONAL")
    assert conn.execute("SELECT COUNT(*) FROM edge WHERE src = ?", (a,)).fetchone()[0] == 2


def test_allow_ambiguous_surface(conn: sqlite3.Connection, project: str) -> None:
    """「师兄」映射到 2 个 node 必须存得下——**歧义是跨行事实，不是一条坏数据**。

    它在查询时算（Resolution.ambiguous），存不进 alias 表。alias 做实体消解会正好摧毁
    这个项目最有价值的信号（ADR 0004：「顾姑娘/清音/魔尊」编码的是关系阶段和认知边界）。
    """
    a = _node(conn, project, "Character", "萧决")
    b = _node(conn, project, "Character", "顾清音")
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,'title')",
        ("a1", project, a, "师兄"),
    )
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,'title')",
        ("a2", project, b, "师兄"),
    )
    rows = conn.execute(
        "SELECT COUNT(DISTINCT node_id) FROM alias WHERE project_id = ? AND surface = '师兄'",
        (project,),
    ).fetchone()[0]
    assert rows == 2


def test_allow_short_alias_when_not_usable_for_rules(
    conn: sqlite3.Connection, project: str
) -> None:
    # 1 字名的人物是合法的。ADR 0004 要防的是短别名**去匹配正文**，不是短名字存在。
    n = _node(conn, project, "Character", "决")
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules)"
        " VALUES (?,?,?,?,'canonical',0)",
        ("a1", project, n, "决"),
    )


def test_length_counts_code_points_not_utf16_units(
    conn: sqlite3.Connection, project: str
) -> None:
    """SQLite 的 `length()` 数 code point，不是 UTF-16 单元。

    网文人名爱用生僻字（扩展 B 区如 𤩝 在 JS 里算 2 个单位）。如果 length() 数的是
    UTF-16 单元，「𤩝」这个 1 字名就会被 `length >= 2` 放行去匹配正文——一个只在
    特定章节出现的、会被当成小 bug 调两周的误报源。
    """
    assert conn.execute("SELECT length('𤩝')").fetchone()[0] == 1
    n = _node(conn, project, "Character", "𤩝")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules)"
            " VALUES (?,?,?,?,'canonical',1)",
            ("a1", project, n, "𤩝"),
        )


def test_allow_all_nine_edge_types(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT type, exclusivity FROM edge_type ORDER BY type").fetchall()
    got = {r["type"]: r["exclusivity"] for r in rows}
    assert got == {
        "LOCATED_AT": "single_per_src",
        "HAS_STATE": "single_per_src_dst",
        "RELATED_TO": "single_per_src_dst",
        "KNOWS": "single_per_src_dst",
        "BELIEVES": "single_per_src_dst",
        "MEMBER_OF": "multi",
        "OWNS": "multi",
        "PLANTED_IN": "multi",
        "RESOLVED_IN": "multi",
    }


def test_secret_and_chapter_share_the_node_id(conn: sqlite3.Connection, project: str) -> None:
    """`node` 是全部 8 类节点的唯一身份表，`secret` / `chapter` 是扩展表（主键 = node.id）。

    §8 Day 5 的矩阵 SQL 写的是 `k.dst = s.id`——只有在这种读法下它才成立，
    同时 edge.src/dst 还能永远 REFERENCES node(id)。
    """
    s = _node(conn, project, "Secret", "主角是魔尊转世")
    conn.execute(
        "INSERT INTO secret (id, project_id, description) VALUES (?,?,?)", (s, project, "")
    )
    c = _node(conn, project, "Character", "萧决")
    _edge(conn, project, c, s, type="KNOWS", valid_from_chapter=88)
    row = conn.execute(
        "SELECT n.name FROM edge k JOIN secret s ON k.dst = s.id JOIN node n ON n.id = s.id"
        " WHERE k.src = ? AND k.type = 'KNOWS'",
        (c,),
    ).fetchone()
    # 显示名只有一份，在 node.name 上（secret 表没有 name 列）。
    assert row["name"] == "主角是魔尊转世"


# ══════════════════════════════════════════════════════════════════════════
# helpers（放在最后：它们是脚手架，不是被测对象）
# ══════════════════════════════════════════════════════════════════════════


def _chapter(conn: sqlite3.Connection, project_id: str, number: int) -> str:
    cid = _node(conn, project_id, "Chapter", f"第{number}章")
    conn.execute(
        "INSERT INTO chapter (id, project_id, number, path, text_sha256) VALUES (?,?,?,?,?)",
        (cid, project_id, number, f"{number:04d}.txt", "c" * 64),
    )
    return cid


def _evidence(
    conn: sqlite3.Connection,
    project_id: str,
    _src: str,
    *,
    chapter_number: int = 143,
) -> str:
    ch = _chapter(conn, project_id, chapter_number)
    snap = new_id(EntityType.SNAPSHOT, project_id)
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) VALUES (?,?,?,?)",
        (snap, ch, "他死了。", "d" * 64),
    )
    eid = new_id(EntityType.EVIDENCE, project_id)
    conn.execute(
        "INSERT INTO evidence (id, project_id, chapter_snapshot_id, para_index, quote_text,"
        " quote_sha256, chapter_id, para_index_hint) VALUES (?,?,?,?,?,?,?,?)",
        (eid, project_id, snap, 0, "他死了。", "e" * 64, ch, 0),
    )
    return eid


def _event_evidence_consumer(
    conn: sqlite3.Connection, project_id: str, consumer: str
) -> tuple[str, str, str]:
    if consumer == "story_event":
        evidence_id = _evidence(conn, project_id, "unused")
        _story_event(conn, project_id, "event:parent-coherence", evidence_id)
    else:
        event_evidence = _evidence(conn, project_id, "unused", chapter_number=142)
        evidence_id = _evidence(conn, project_id, "unused")
        event_id = "event:knower-parent-coherence"
        _story_event(
            conn,
            project_id,
            event_id,
            event_evidence,
            chapter_number=142,
            valid_from_chapter=142,
        )
        character_id = _node(conn, project_id, "Character", "顾清音")
        conn.execute(
            "INSERT INTO event_knower "
            "(event_id, project_id, character_id, valid_from_chapter, information_scope, "
            "evidence_id, evidence_status) VALUES (?,?,?,?,?,?,?)",
            (event_id, project_id, character_id, 143, "PROVISIONAL", evidence_id, "FRESH"),
        )

    snapshot_id, chapter_id = conn.execute(
        "SELECT chapter_snapshot_id, chapter_id FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()
    return evidence_id, snapshot_id, chapter_id


def _story_event(
    conn: sqlite3.Connection,
    project_id: str,
    event_id: str,
    evidence_id: str,
    **overrides: object,
) -> None:
    values: dict[str, object] = {
        "id": event_id,
        "project_id": project_id,
        "chapter_number": 143,
        "summary": "顾清音在渡口交给萧决一枚玄铁令。",
        "valid_from_chapter": 143,
        "information_scope": "PROVISIONAL",
        "status": "ACTIVE",
        "confidence": 0.9,
        "source": "extractor",
        "evidence_id": evidence_id,
        "evidence_status": "FRESH",
    }
    values.update(overrides)
    columns = ", ".join(values)
    markers = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO story_event ({columns}) VALUES ({markers})", tuple(values.values())
    )
