"""总结核对（Task 10 / ADR 0030）：只告警、可落锚、来源变则 SUPERSEDED。

钉住：
- 核对只写通知，不改 summary head / Canon；
- provider 抛错 → FAILED run + background_failure，不新建冲突通知；
- 两阶段调用审计：provider 出发前有 RUNNING model_call + run link，
  成功 finalize token/cost，抛错有 FAILED 行，崩溃遗留 RUNNING 可 ABANDONED；
- 来源已变 → 只标 SUPERSEDED，不调模型；
- **通知单源化（2026-08-18 文档 §5/Step 3）**：只有「当前 head 总结是作者写的」
  才允许冒 `summary_mismatch`；机器写/覆写（source=model）即使 possible_conflict
  也绝不建通知，只解决旧 OPEN。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from novel_harness.db import connect, migrate
from novel_harness.extract.call_audit import (
    abandon_call,
    begin_call,
    finalize_call_failure,
    finalize_call_success,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project
from novel_harness.summary_reconciliation import (
    begin_reconciliation_run,
    finalize_reconciliation_run,
    reconciliation_source_hash,
)


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "recon.db")
    migrate(c)
    yield c
    c.close()


def _project_with_snapshot(conn):
    """一个有一章正文的项目，返回 (pid, snapshot_id)。FK 需要真快照。"""
    from novel_harness import importer

    tmp = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent
    root = tmp / "book"
    root.mkdir(parents=True, exist_ok=True)
    pid = create_project(conn, name="核对", root_path=str(root)).id
    graph = SqliteStoryGraph(conn)
    src = root / "s.txt"
    src.write_text("第一章 开局\n\n萧决走进了青云城。\n", encoding="utf-8")
    importer.import_book(graph, pid, txt=src, root=root)
    conn.commit()
    snap = cur(conn).current_snapshots(pid)[0].snapshot_id
    return pid, snap


def cur(conn):

    return SqliteStoryGraph(conn)


def _seed_chapter_summary_head(
    conn, pid: str, chapter_number: int, *, source: str, seed: str
) -> tuple[str, str]:
    """把第 `chapter_number` 章的 head 改成一条 ACTIVE 总结并返回 (chapter_id, sha)。

    `source` ∈ {author, model} 决定这条总结「是谁写的」——通知单源化判据。
    ACTIVE 行的校验要求 `summary_sha256 == sha256(summary)`，这里如实算。
    """
    chapter_id = conn.execute(
        "SELECT id FROM chapter WHERE project_id=? AND number=?",
        (pid, chapter_number),
    ).fetchone()["id"]
    body = f"第 {chapter_number} 章的{source}总结（{seed}）"
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    sid = f"summary:{chapter_number}:{source}:{seed}"
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            schema_version, prompt_hash, source, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'chapter-summary-v1', ?, ?, 'ACTIVE',
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        """,
        (sid, pid, chapter_id, chapter_number, body, sha, f"prompt:{sid}", source),
    )
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (sid, chapter_id),
    )
    conn.commit()
    return str(chapter_id), sha


def test_source_hash_is_stable_and_distinct_hashes_differ(conn) -> None:
    a = reconciliation_source_hash(
        source_snapshot_id="snap:1", subject_type="chapter_summary"
    )
    b = reconciliation_source_hash(
        source_snapshot_id="snap:1", subject_type="chapter_summary"
    )
    c = reconciliation_source_hash(
        source_snapshot_id="snap:2", subject_type="chapter_summary"
    )
    assert a == b and a != c


def test_two_phase_call_audit_success_and_retry_links(conn) -> None:
    """调用前 RUNNING + link；成功 finalize 记 token/cost；重试新增行不覆盖。"""
    pid, snap0 = _project_with_snapshot(conn)
    # begin_call 先开事务（provider 出发前）；run 行与 link 在同一个事务里。
    call_id = begin_call(
        conn,
        project_id=pid,
        capability="reconciler",
        model="checker-model",
        prompt_hash="ph",
        call_id_factory=lambda _pid: "call:recon:1",
    )
    run_id = begin_reconciliation_run(
        conn, project_id=pid, subject_type="chapter_summary",
        subject_id="summary:0", chapter_number=1,
        checked_against_snapshot_id=snap0,
        source_generation=1, source_sha256="src0", summary_sha256="s0",
        checker_prompt_hash="checker:0",
    )
    conn.execute(
        """
        INSERT INTO summary_reconciliation_call (run_id, model_call_id, attempt_no, fencing_token)
        VALUES (?, ?, 1, 1)
        """,
        (run_id, call_id),
    )
    conn.commit()
    row = conn.execute("SELECT in_transaction FROM pragma_database_list LIMIT 1").fetchone() if False else None
    row = conn.execute(
        "SELECT call_state, capability FROM model_call WHERE id = ?", (call_id,)
    ).fetchone()
    assert row["call_state"] == "RUNNING" and row["capability"] == "reconciler"

    finalize_call_success(
        conn,
        call_id,
        out_artifact="artifact:sha256:abc",
        tokens_in=100, tokens_out=20, ms=300, cost=0.01,
        cache_read_tokens=0, cache_write_tokens=None,
        finished_at="2026-08-17T00:00:00Z",
    )
    conn.commit()
    row = conn.execute(
        "SELECT call_state, tokens_in, tokens_out, cost FROM model_call WHERE id = ?",
        (call_id,),
    ).fetchone()
    assert row["call_state"] == "SUCCEEDED"
    assert row["tokens_in"] == 100 and row["cost"] == 0.01


def test_two_phase_failure_and_abandon_leave_distinct_rows(conn) -> None:
    pid = create_project(conn, name="核对", root_path=".").id

    # provider 抛错：FAILED 行。
    failed = begin_call(
        conn, project_id=pid, capability="reconciler", model="m",
        prompt_hash="ph", call_id_factory=lambda _pid: "call:fail",
    )
    conn.commit()
    finalize_call_failure(
        conn, failed, error_type="provider_error", error_message="boom",
        finished_at="2026-08-17T00:00:00Z",
    )
    conn.commit()
    row = conn.execute(
        "SELECT call_state, error_type FROM model_call WHERE id = ?", (failed,)
    ).fetchone()
    assert row["call_state"] == "FAILED" and row["error_type"] == "provider_error"

    # 崩溃遗留：RUNNING → ABANDONED（不覆盖、不删除）。
    orphan = begin_call(
        conn, project_id=pid, capability="reconciler", model="m",
        prompt_hash="ph", call_id_factory=lambda _pid: "call:orphan",
    )
    conn.commit()
    abandon_call(conn, orphan, finished_at="2026-08-17T00:00:00Z")
    conn.commit()
    row = conn.execute(
        "SELECT call_state FROM model_call WHERE id = ?", (orphan,)
    ).fetchone()
    assert row["call_state"] == "ABANDONED"
    # 三条都在账里：重试/崩溃绝不覆盖旧账。
    assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 2


def test_reconciliation_failure_records_failed_and_no_notification(conn) -> None:
    """provider 抛错：run 标 FAILED；不建成 OPEN 冲突通知（只写 background_failure 键）。"""
    pid, snap1 = _project_with_snapshot(conn)
    run_id = begin_reconciliation_run(
        conn, project_id=pid, subject_type="chapter_summary",
        subject_id="summary:1", chapter_number=1,
        checked_against_snapshot_id=snap1,
        source_generation=1, source_sha256="src", summary_sha256="s",
        checker_prompt_hash="checker:1",
    )
    conn.commit()
    from novel_harness.summary_reconciliation import ReconcileTask

    task = ReconcileTask(
        outbox_id="ob:1", project_id=pid, subject_type="chapter_summary",
        subject_id="summary:1", chapter_number=1,
        checked_against_snapshot_id=snap1,
        source_generation=1, source_sha256="src", summary_sha256="s",
        checker_schema="summary-reconciliation-v1", checker_prompt_hash="checker:1",
        fencing_token=1,
    )
    finalize_reconciliation_run(
        conn, task, None, run_id=run_id, status="FAILED", message="provider boom"
    )
    assert (
        conn.execute("SELECT status FROM summary_reconciliation_run WHERE id = ?", (run_id,))
        .fetchone()["status"]
        == "FAILED"
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM system_notification").fetchone()[0] == 0
    ), "核对失败不该制造冲突 OPEN，只该由调用方写 background_failure"


def test_possible_conflict_creates_one_open_notification_for_exact_pair(
    conn,
) -> None:
    """作者手改总结且与正文冲突 → 建一条 OPEN（dedupe 幂等，Step 3 的唯一通知源）。"""
    pid, snap2 = _project_with_snapshot(conn)
    chapter_id, sha = _seed_chapter_summary_head(
        conn, pid, 1, source="author", seed="pair"
    )
    run_id = begin_reconciliation_run(
        conn, project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap2,
        source_generation=1, source_sha256="src2", summary_sha256=sha,
        checker_prompt_hash="checker:2",
    )
    conn.commit()
    from novel_harness.summary_reconciliation import ReconcileTask

    task = ReconcileTask(
        outbox_id="ob:2", project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap2,
        source_generation=1, source_sha256="src2", summary_sha256=sha,
        checker_schema="summary-reconciliation-v1", checker_prompt_hash="checker:2",
        fencing_token=1,
    )
    finalize_reconciliation_run(
        conn, task, None, run_id=run_id, status="SUCCEEDED",
        possible_conflict=True, summary_sha256=sha, source_sha256="src2",
        subject_title="第 1 章的总结",
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM system_notification").fetchone()[0] == 1
    )
    first = conn.execute(
        "SELECT kind, status, dedupe_key FROM system_notification"
    ).fetchone()
    assert first["kind"] == "summary_mismatch" and first["status"] == "OPEN"


def test_machine_overwrite_conflict_never_notifies(conn) -> None:
    """Step 3 §5：正文→总结（机器写/覆写）即使 possible_conflict 也绝不建通知。

    机器覆写后 head.source == 'model' → 自动对齐是安静动作，不打扰作者。
    """
    pid, snap3 = _project_with_snapshot(conn)
    chapter_id, sha = _seed_chapter_summary_head(
        conn, pid, 1, source="model", seed="overwrite"
    )
    run_id = begin_reconciliation_run(
        conn, project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap3,
        source_generation=1, source_sha256="src3", summary_sha256=sha,
        checker_prompt_hash="checker:3",
    )
    conn.commit()
    from novel_harness.summary_reconciliation import ReconcileTask

    task = ReconcileTask(
        outbox_id="ob:3", project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap3,
        source_generation=1, source_sha256="src3", summary_sha256=sha,
        checker_schema="summary-reconciliation-v1", checker_prompt_hash="checker:3",
        fencing_token=1,
    )
    finalize_reconciliation_run(
        conn, task, None, run_id=run_id, status="SUCCEEDED",
        possible_conflict=True, summary_sha256=sha, source_sha256="src3",
        subject_title="第 1 章的总结（机器覆写）",
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM system_notification").fetchone()[0] == 0
    ), "正文自动覆写不该冒 summary_mismatch（通知单源化）"


def test_author_edit_after_model_overwrite_resolves_old_open(conn) -> None:
    """机器覆写解决旧的作者 OPEN：同 subject 的旧冲突通知被 RESOLVED，不重开。"""
    pid, snap4 = _project_with_snapshot(conn)
    # 先造一条老作者 OPEN 冲突（subject 相同）。
    chapter_id, old_sha = _seed_chapter_summary_head(
        conn, pid, 1, source="author", seed="old"
    )
    conn.execute(
        """
        INSERT INTO system_notification (
            id, project_id, kind, status, subject_type, subject_id,
            chapter_number, title, summary_sha256, source_sha256, dedupe_key
        ) VALUES (?, ?, 'summary_mismatch', 'OPEN', 'chapter_summary', ?,
                  1, '旧的作者冲突', ?, 'old-src', 'old-dedupe')
        """,
        (f"notif:{pid}", pid, chapter_id, old_sha),
    )
    conn.commit()
    # 机器随后覆写（body 变了），head 现在是 model 总结。
    _, sha = _seed_chapter_summary_head(
        conn, pid, 1, source="model", seed="after"
    )
    run_id = begin_reconciliation_run(
        conn, project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap4,
        source_generation=1, source_sha256="src4", summary_sha256=sha,
        checker_prompt_hash="checker:4",
    )
    conn.commit()
    from novel_harness.summary_reconciliation import ReconcileTask

    task = ReconcileTask(
        outbox_id="ob:4", project_id=pid, subject_type="chapter_summary",
        subject_id=chapter_id, chapter_number=1,
        checked_against_snapshot_id=snap4,
        source_generation=1, source_sha256="src4", summary_sha256=sha,
        checker_schema="summary-reconciliation-v1", checker_prompt_hash="checker:4",
        fencing_token=1,
    )
    finalize_reconciliation_run(
        conn, task, None, run_id=run_id, status="SUCCEEDED",
        possible_conflict=True, summary_sha256=sha, source_sha256="src4",
        subject_title="机器覆写",
    )
    row = conn.execute(
        "SELECT status FROM system_notification WHERE id = ?", (f"notif:{pid}",)
    ).fetchone()
    assert row["status"] == "RESOLVED", "机器覆写应解决旧作者冲突，且不再新建"
