"""固定章节刷新协调器（Task 5）—— 固定 DAG、幂等 coverage、lease/fencing、真并行。

本文件**只用 stub adapter**：summary/extraction 的 head CAS 是 Task 6/9 的活，
这里证明的是协调器本身——分支并行、final gate 不把初次 PASSED 当最终闸门、
lease 抢领与旧 token 失效、coverage 幂等不重复付费。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.chapter_refresh import (
    BRANCH_EXTRACTION,
    BRANCH_SUMMARY,
    BranchContext,
    ChapterRefreshCoordinator,
    claim_attempt,
    create_manual_attempt,
    ensure_refresh_coverage,
    heartbeat,
    release_attempt,
)
from novel_harness.db import Connection, connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph


class Stub:
    """可编程的 BranchAdapter：记调用、可阻塞在 barrier、可抛异常。"""

    def __init__(
        self,
        barrier: threading.Barrier | None = None,
        note: str = "done",
        fail: Exception | None = None,
    ) -> None:
        self.barrier = barrier
        self.note = note
        self.fail = fail
        self.calls: list[BranchContext] = []

    def run(self, ctx: BranchContext) -> str:
        self.calls.append(ctx)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if self.fail is not None:
            raise self.fail
        return self.note


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    c = connect(tmp_path / "refresh.db")
    migrate(c)
    yield c
    c.close()


def _seed_chapter(conn: Connection, tmp_path: Path) -> tuple[str, str]:
    """一本一章的书，返回 (project_id, chapter_id)。"""
    root = tmp_path / "book"
    pid = project.create(conn, name="刷新测试", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    text = "第一章 甲\n\n萧决走进来了。\n"
    src = root / "s.txt"
    root.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    current = next(ct for ct in store.current_snapshots(pid) if ct.number == 1)
    return pid, current.chapter_id


def _snapshot_id(conn: Connection, pid: str) -> str:
    return next(
        ct.snapshot_id for ct in SqliteStoryGraph(conn).current_snapshots(pid) if ct.number == 1
    )


def _coordinator(conn: Connection) -> ChapterRefreshCoordinator:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])

    def factory() -> Connection:
        return connect(path)

    return ChapterRefreshCoordinator(
        conn,
        connection_factory=factory,
        store_factory=lambda c: SqliteStoryGraph(c),
    )


def test_validation_blocked_stops_downstream_with_zero_summary_calls(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """验证阻断 → 总结/抽取调用数为 0，旧分支保持未启动。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = create_manual_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        trigger_key="manual:1",
    )
    # 直接把验证状态预置成 BLOCKED，让 run 跳过验证、也跳过下游。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET validation_state = 'BLOCKED' WHERE id = ?",
        (attempt_id,),
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1", ttl_seconds=60)
    assert token is not None

    summary = Stub()
    extraction = Stub()
    outcome = _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
    )
    assert summary.calls == [] and extraction.calls == []
    assert outcome["validation"] == "blocked"


def test_summary_and_extraction_run_in_parallel(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """两个 stub 同时进入 barrier：调用顺序证明不了并行，barrier 通过才证明。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = create_manual_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        trigger_key="manual:parallel",
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None

    barrier = threading.Barrier(2)
    summary = Stub(barrier=barrier, note="summary-ok")
    extraction = Stub(barrier=barrier, note="extraction-ok")
    outcome = _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
        alias_adapter=Stub(note="unchanged"),
    )
    assert outcome["summary_state"] == "summary-ok"
    assert outcome["extraction_state"] == "extraction-ok"
    assert len(summary.calls) == len(extraction.calls) == 1
    row = conn.execute(
        "SELECT summary_state, extraction_state, final_gate_state, final_validation_report_id "
        "FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row["summary_state"] == "SUCCEEDED"
    assert row["extraction_state"] == "SUCCEEDED"
    assert row["final_gate_state"] == "PASSED"
    assert row["final_validation_report_id"] is not None


def test_alias_not_started_blocks_the_final_gate(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """初次 PASSED 但 alias_phase=NOT_STARTED：summary 候选 READY 也不能切 head
    （final gate 保持 PENDING）；alias 阶段确认 UNCHANGED 后才 PASSED。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = create_manual_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        trigger_key="manual:gate",
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None

    coord = _coordinator(conn)
    outcome = coord.run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=Stub(note="candidate-ready"),
        extraction_adapter=Stub(),
        # 不给 alias adapter：NOT_STARTED 保持非终态
    )
    assert outcome["final_gate"] == "PENDING"
    row = conn.execute(
        "SELECT final_gate_state, alias_phase FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row["final_gate_state"] == "PENDING"
    assert row["alias_phase"] == "NOT_STARTED"

    # 第二次运行：alias 阶段确认未变化 → final gate PASSED + final report 存在。
    release_attempt(conn, attempt_id, owner="w1", token=token)
    token2 = claim_attempt(conn, attempt_id, owner="w2")
    assert token2 is not None
    outcome2 = coord.run(
        attempt_id,
        owner="w2",
        token=token2,
        summary_adapter=Stub(note="re-run"),
        extraction_adapter=Stub(),
        alias_adapter=Stub(note="unchanged"),
    )
    assert outcome2["final_gate"] is not None
    row2 = conn.execute(
        "SELECT final_gate_state, final_validation_report_id FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row2["final_gate_state"] == "PASSED"
    assert row2["final_validation_report_id"] is not None


def test_heartbeat_keeps_lease_and_stale_token_cas_fails(
    conn: Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """heartbeat 正常 → 第二 worker 抢不到；心跳停了 → 新 claim 拿到更大的 token，
    旧 worker 的状态写 CAS 失败。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = create_manual_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        trigger_key="manual:lease",
    )
    conn.commit()
    now = [time.time()]
    monkeypatch.setattr("novel_harness.chapter_refresh.time.time", lambda: now[0])
    ttl = 60.0

    token1 = claim_attempt(conn, attempt_id, owner="w1", ttl_seconds=ttl, now=now[0])
    assert token1 == 1
    # heartbeat 续租 → 第二 worker 在过期前抢不到。
    now[0] += 10
    assert heartbeat(conn, attempt_id, owner="w1", token=token1, ttl_seconds=ttl) is True
    assert claim_attempt(conn, attempt_id, owner="w2", ttl_seconds=ttl, now=now[0]) is None

    # 心跳停了（TTL 过期）→ w2 抢到，fencing token 单调递增。
    now[0] += 60
    token2 = claim_attempt(conn, attempt_id, owner="w2", ttl_seconds=ttl, now=now[0])
    assert token2 == 2

    # 旧 worker 恢复：owner/token 都不匹配 → 状态写失败。
    from novel_harness import chapter_refresh

    ok = chapter_refresh._branch_update(
        conn, attempt_id, owner="w1", token=1, column="summary_state", state="SUCCEEDED"
    )
    assert ok is False
    row = conn.execute(
        "SELECT summary_state FROM chapter_refresh_attempt WHERE id = ?", (attempt_id,)
    ).fetchone()
    assert row["summary_state"] == "PENDING"


def test_coverage_is_idempotent_and_manual_creates_new_intent(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """同 hash 保存：只为缺失分支建 coverage attempt，第二次复用同一 attempt；
    terminal FAILED → attention_required；manual 意图另建。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = next(
        ct.snapshot_id for ct in SqliteStoryGraph(conn).current_snapshots(pid) if ct.number == 1
    )

    def missing_summary_and_extraction(c: Connection, p: str, ch: str, run: str) -> int:
        return BRANCH_SUMMARY | BRANCH_EXTRACTION

    first = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=missing_summary_and_extraction,
    )
    assert first.processing == "queued"
    assert first.missing_branch_mask == BRANCH_SUMMARY | BRANCH_EXTRACTION
    assert first.attempt_id is not None

    second = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=missing_summary_and_extraction,
    )
    assert second.attempt_id == first.attempt_id
    assert second.processing == "queued"

    # 覆盖完整 → reused，不建 attempt。
    full = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=lambda c, p, ch, run: 0,
    )
    assert full.processing == "reused" and full.reused is True

    # 同 basis 已 FAILED → attention_required，不自动重付。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET summary_state = 'FAILED' WHERE id = ?",
        (first.attempt_id,),
    )
    conn.commit()
    failed = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=missing_summary_and_extraction,
    )
    assert failed.processing == "attention_required"

    # 显式「重新整理」→ 新 manual intent，不被 coverage 唯一键吞掉。
    manual = create_manual_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        trigger_key=f"manual:{time.time_ns()}",
    )
    assert manual != first.attempt_id
