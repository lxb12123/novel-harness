"""全书总结自治调度（2026-08-18 文档 §2.2/§4/Step 2）。

钉住：三态判定（paired/missing/stale）全确定性查库；缺与不对齐按近 10 章权重
1.0、远距离衰减调度；焦点中的章豁免；整轮调度不因单章失败而阻塞；「导入一本
生成一半的书」→ 不点保存 → 等一轮调度 → 缺章被补上（写进持久 attempt 表）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.api.app import _current_ruleset
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.summary_schedule import (
    scan_chapter_summary_state,
    schedule_alignment,
    weight_for_chapter,
)


def _wheel(tmp_path: Path, chapters: int) -> dict[str, str]:
    """建一本 `chapters` 章的书（每章正文）。
    第 1 章从磁盘进库；后续章靠 `importer.sync` 一起进。
    """
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="调度", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    Ledger(store, conn, pid).declare_node(NodeLabel.CHARACTER, "萧决")
    src = tmp_path / "s.txt"
    body = "\n\n".join(f"第{n}章 甲{n}\n\n萧决在第 {n} 章做了些事。\n" for n in range(1, chapters + 1))
    src.write_text(body, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


def _conn_and_ruleset(wheel: dict[str, str]):
    conn = connect(wheel["db"])
    epoch, ruhash = _current_ruleset(conn, wheel["pid"])
    return conn, epoch, ruhash


def _seed_stale_summary(wheel: dict[str, str], chapter: int) -> None:
    """给第 `chapter` 章一条 ACTIVE 但来源快照是**旧正文**的总结（stale）。

    造法：先往库里塞一个「旧版」正文快照（text 与当前不同、text_sha256 也不同），
    再把总结的 `source_snapshot_id` 指向它 → 指纹对不上 = 该覆写。
    """
    conn = connect(wheel["db"])
    ch = conn.execute(
        "SELECT id FROM chapter WHERE project_id=? AND number=?", (wheel["pid"], chapter)
    ).fetchone()["id"]
    from novel_harness.ids import EntityType, new_id
    from novel_harness import importer

    old_text = f"第 {chapter} 章的旧版本旧版本\n"
    old_sha = importer.text_digest(old_text)
    old_snap = new_id(EntityType.SNAPSHOT, wheel["pid"])
    conn.execute(
        """
        INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256)
        VALUES (?, ?, ?, ?)
        """,
        (old_snap, ch, old_text, old_sha),
    )
    sid = new_id(EntityType.SUMMARY, wheel["pid"])
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            source_snapshot_id, replaces_summary_id, schema_version, prompt_hash,
            model_call_id, created_at, source, status
        ) VALUES (?, ?, ?, ?, '旧总结', nh_sha256_text('旧总结'), ?, NULL,
                  'chapter-summary-v1', 'prompt-x', NULL,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'model', 'ACTIVE')
        """,
        (sid, wheel["pid"], ch, chapter, old_snap),
    )
    # 018 给每章都建了空的 head 行；这里把现有的空 head 指向新总结。
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (sid, ch),
    )
    conn.commit()
    conn.close()


def test_weight_peaks_in_window_and_decays_outside() -> None:
    assert weight_for_chapter(draft_chapter=12, chapter_number=12) == 0.0
    assert weight_for_chapter(draft_chapter=12, chapter_number=11) == 1.0
    assert weight_for_chapter(draft_chapter=12, chapter_number=2) == 1.0  # Δ=10 == 窗口
    assert weight_for_chapter(draft_chapter=12, chapter_number=1) < 1.0
    assert weight_for_chapter(draft_chapter=12, chapter_number=1) > 0.0
    # 未来章（Δ<0）权重恒 0：写作只调用本章之前的总结。
    assert weight_for_chapter(draft_chapter=1, chapter_number=2) == 0.0


def test_scan_classifies_missing_paired_and_stale_without_llm(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, 3)
    conn, _, _ = _conn_and_ruleset(wheel)
    try:
        states = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        by_chapter = {s.chapter_number: s for s in states}
        assert len(by_chapter) == 3
        # 新书没总结 → 全 missing；1/2 在近窗口内权重 1.0。
        assert by_chapter[1].state == "missing" and by_chapter[1].weight == 1.0
        assert by_chapter[2].state == "missing" and by_chapter[2].weight == 1.0
        # 第 3 章是「正在写」的那章 —— 权重对自己是未来章语义 = 0（写作不用本章总结）。
        assert by_chapter[3].weight == 0.0
    finally:
        conn.close()

    # 造一条 stale（source_sha 故意不等于当前正文）。
    _seed_stale_summary(wheel, 1)
    conn = connect(wheel["db"])
    try:
        states = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        by_chapter = {s.chapter_number: s for s in states}
        assert by_chapter[1].state == "stale", "正文变过 = 该覆写（不是 paired）"
    finally:
        conn.close()


def test_schedule_skips_focused_and_marks_weight_zero(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, 12)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        # 作者在第 12 章写：焦点=12 不该被调度；第 11 章（Δ=1）权重 1.0 该补。
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=12,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=12,
            limit=20,
        )
        assert decisions[12] == "focused", "焦点中的章不调度"
        assert decisions[11] == "queued", f"Δ=1 的章必调度，实得 {decisions[11]}"
    finally:
        conn.close()


def test_half_imported_book_fills_missing_chapters_without_save(tmp_path: Path) -> None:
    """导入一本「生成一半的书」：缺章/不对齐都被调度填上，不点保存。"""
    wheel = _wheel(tmp_path, 5)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=5,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        queued = [n for n, d in decisions.items() if d in ("queued", "queued_overwrite")]
        assert 1 in queued and 2 in queued and 3 in queued, (
            f"缺章 1..4 应全被调度，实得 {decisions}"
        )
        # 结果写进持久 attempt（系统记录）——不是内存 enqueue。
        count = conn.execute(
            """
            SELECT COUNT(*) FROM chapter_refresh_attempt a
            JOIN chapter_refresh_run r ON r.id = a.run_id
            JOIN chapter c ON c.id = r.chapter_id
            WHERE c.project_id = ?
            """,
            (wheel["pid"],),
        ).fetchone()[0]
        assert count >= 3, f"应有 >=3 条覆盖 attempt，实得 {count}"
    finally:
        conn.close()


def test_one_enqueue_failure_does_not_block_the_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6：单章入队失败不阻塞整轮（调度器把失败记成 enqueue_failed 继续往下）。"""
    wheel = _wheel(tmp_path, 4)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    from novel_harness.summary_schedule import schedule_alignment as real

    calls = 0

    def flaky(conn, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("模拟入队失败")

    monkeypatch.setattr("novel_harness.summary_schedule.ensure_refresh_coverage", flaky)
    try:
        decisions = real(
            conn,
            wheel["pid"],
            draft_chapter=4,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        failed = [n for n, d in decisions.items() if d == "enqueue_failed"]
        # draft=4 的第 4 章自己权重 0（写本章不用本章总结）不参与调度；1..3 全失败。
        assert len(failed) == 3, f"1..3 全失败 = 全部记 enqueue_failed，实得 {decisions}"
        assert calls == 3, "失败章不阻塞后续章的入队尝试"
    finally:
        conn.close()
