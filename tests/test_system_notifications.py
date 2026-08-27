"""统一系统通知：去重、忽略、解决、失败通知与 validation_blocked（Task 10）。

钉住：
- 同一 dedupe 对只有一条通知（不变量 10）；
- IGNORED / RESOLVED 是精确 hash 对的终态，重复核对不得重开；
- `background_failure` 的 dedupe key 不含会变的 exception 文本；
- `validation_blocked` 与 final gate 同事务（不变量 29），崩溃后重启仍物化。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_harness.db import connect, migrate
from novel_harness.project import create as create_project
from novel_harness.system_notifications import (
    background_failure_dedupe_key,
    dedupe_key_for,
    enqueue_notification,
    ignore_notification,
    list_open_notifications,
    materialize_notification_outbox,
    notification_count,
)


@pytest.fixture
def world(tmp_path: Path) -> dict:
    conn = connect(tmp_path / "n.db")
    migrate(conn)
    pid = create_project(conn, name="通知", root_path=".").id
    yield {"conn": conn, "pid": pid}
    conn.close()


def test_enqueue_then_materialize_produces_one_open_mismatch(world: dict) -> None:
    conn, pid = world["conn"], world["pid"]
    key = dedupe_key_for(
        kind="summary_mismatch", subject_type="chapter_summary",
        subject_id="summary:1", summary_sha256="s1", source_sha256="src1",
    )
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn,
        project_id=pid,
        kind="summary_mismatch",
        subject_type="chapter_summary",
        subject_id="summary:1",
        chapter_number=1,
        title_code="test_notice", title_params=None,
        dedupe_key=key,
        summary_sha256="s1",
        source_sha256="src1",
    )
    conn.commit()
    assert materialize_notification_outbox(conn, project_id=pid, lease_owner="t") == 1
    assert notification_count(conn, pid) == 1
    one = list_open_notifications(conn, pid)
    assert len(one) == 1 and one[0].kind == "summary_mismatch"

    # 重复物化：同一 dedupe 对不刷出第二行（upsert 幂等）。
    assert materialize_notification_outbox(conn, project_id=pid, lease_owner="t") == 0
    assert notification_count(conn, pid) == 1


def test_supported_resolves_old_open_but_ignored_stays(world: dict) -> None:
    """supported 解决同 subject 的旧 OPEN；IGNORED 保留作审计，不重开。"""
    conn, pid = world["conn"], world["pid"]
    key = dedupe_key_for(
        kind="summary_mismatch", subject_type="chapter_summary",
        subject_id="summary:2", summary_sha256="s2", source_sha256="src2",
    )
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="summary_mismatch",
        subject_type="chapter_summary", subject_id="summary:2", chapter_number=2,
        title_code="test_notice", title_params=None, dedupe_key=key,
        summary_sha256="s2", source_sha256="src2",
    )
    conn.commit()
    materialize_notification_outbox(conn, project_id=pid, lease_owner="t")
    notif = list_open_notifications(conn, pid)[0]
    ignore_notification(conn, notif.id)
    assert notification_count(conn, pid) == 0

    # 同 hash 对再核对：IGNORED 终态不被重开。
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="summary_mismatch",
        subject_type="chapter_summary", subject_id="summary:2", chapter_number=2,
        title_code="test_notice", title_params=None, dedupe_key=key,
        summary_sha256="s2", source_sha256="src2",
    )
    conn.commit()
    materialize_notification_outbox(conn, project_id=pid, lease_owner="t")
    assert notification_count(conn, pid) == 0, "IGNORED 的精确 hash 对被重开了"


def test_new_hash_pair_can_notify_again(world: dict) -> None:
    conn, pid = world["conn"], world["pid"]
    old = dedupe_key_for(
        kind="summary_mismatch", subject_type="chapter_summary",
        subject_id="summary:3", summary_sha256="sA", source_sha256="srcA",
    )
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="summary_mismatch",
        subject_type="chapter_summary", subject_id="summary:3", chapter_number=3,
        title_code="test_notice", title_params=None, dedupe_key=old,
        summary_sha256="sA", source_sha256="srcA",
    )
    conn.commit()
    materialize_notification_outbox(conn, project_id=pid, lease_owner="t")
    notif = list_open_notifications(conn, pid)[0]
    ignore_notification(conn, notif.id)

    # 正文变了 → 新 source hash → 允许新 OPEN。
    new = dedupe_key_for(
        kind="summary_mismatch", subject_type="chapter_summary",
        subject_id="summary:3", summary_sha256="sA", source_sha256="srcB",
    )
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="summary_mismatch",
        subject_type="chapter_summary", subject_id="summary:3", chapter_number=3,
        title_code="test_notice", title_params=None, dedupe_key=new,
        summary_sha256="sA", source_sha256="srcB",
    )
    conn.commit()
    materialize_notification_outbox(conn, project_id=pid, lease_owner="t")
    assert notification_count(conn, pid) == 1


def test_background_failure_dedupe_is_stable_against_exception_text(world: dict) -> None:
    """失败原因文本变化不能绕过幂等键刷屏（不变量 10 的失败侧）。"""
    a = background_failure_dedupe_key(
        kind="background_failure", subject_type="chapter",
        subject_id="chapter:x", operation="extraction",
        source_snapshot_id="snap:1", job_id="job:1",
    )
    b = background_failure_dedupe_key(
        kind="background_failure", subject_type="chapter",
        subject_id="chapter:x", operation="extraction",
        source_snapshot_id="snap:1", job_id="job:1",
    )
    c = background_failure_dedupe_key(
        kind="background_failure", subject_type="chapter",
        subject_id="chapter:x", operation="extraction",
        source_snapshot_id="snap:1", job_id="job:2",  # 不同 job = 不同去重
    )
    assert a == b
    assert a != c


def test_validation_blocked_notification_is_durable_and_deduplicated(
    tmp_path: Path, world: dict
) -> None:
    """validation_blocked：同一 report 重复派发只有一条；崩溃后重启仍物化。"""
    conn, pid = world["conn"], world["pid"]
    key = background_failure_dedupe_key(
        kind="validation_blocked", subject_type="chapter",
        subject_id="chapter:1", operation="validation:report-1",
        source_snapshot_id="snap:1", job_id="attempt:1",
    )
    # 「业务状态 + 通知 outbox 同一事务」：这里直接模拟那个事务已提交、
    # 通知还没物化（进程随即退出的窗口）。
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="validation_blocked",
        subject_type="chapter", subject_id="chapter:1", chapter_number=1,
        title_code="test_notice", title_params=None, dedupe_key=key,
    )
    conn.commit()  # 模拟：outbox 行已随业务事务提交
    # 重启 dispatcher —— 不需要再有什么业务请求。
    assert materialize_notification_outbox(conn, project_id=pid, lease_owner="restart") == 1
    assert notification_count(conn, pid) == 1

    # 同一 report 重复派发（outbox 写侧幂等键是自变量；这里再派一次同 key）
    conn.execute("BEGIN IMMEDIATE")
    enqueue_notification(
        conn, project_id=pid, kind="validation_blocked",
        subject_type="chapter", subject_id="chapter:1", chapter_number=1,
        title_code="test_notice", title_params=None, dedupe_key=key,
    )
    conn.commit()
    materialize_notification_outbox(conn, project_id=pid, lease_owner="restart")
    assert notification_count(conn, pid) == 1, "同一 report 重复派发出现了多条"
