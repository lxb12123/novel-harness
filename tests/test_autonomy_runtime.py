"""30 分钟自治调度接进 BackgroundRuntime 后的端到端（2026-08-18 文档 Step 2）。

钉住文档 §2.2 的假定：导入一本「生成一半的书」，**不点保存、不按任何键**，
`autonomy_once` 把缺总结的章按权重写进 `chapter_refresh_attempt`，随后同一
runtime 的 `pump_once` 用桩 adapter 把它变成真实总结（不真正付模型）。同时钉住
§3：焦点中的章（正写的章）在自治轮里被豁免。adapter 全用桩，协调器真跑 DAG。
"""

from __future__ import annotations

from pathlib import Path

from novel_harness import importer, project
from novel_harness.api.background_runtime import BackgroundRuntime
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import RollingSummarizer, SummaryRequest
from novel_harness.extract import RawChapterAnalysis, RawEvent
from novel_harness.extract.runner import ExtractionRunner
from novel_harness.focus import report_focus
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import EntityType, new_id
from novel_harness.summary_schedule import scan_chapter_summary_state


def _seed_book(tmp_path: Path, chapters: int) -> tuple[str, str, Path]:
    """建一本 `chapters` 章的书（第 1 章磁盘进库，后续靠 importer.sync 一起进）。"""
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="自治", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    Ledger(store, conn, pid).declare_node(NodeLabel.CHARACTER, "萧决")
    src = tmp_path / "s.txt"
    body = "\n\n".join(
        f"第{n}章 甲{n}\n\n萧决在第 {n} 章做了些事。\n" for n in range(1, chapters + 1)
    )
    src.write_text(body, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return str(db), pid, root


def _seed_paired_summary(db: str, pid: str, chapter: int) -> None:
    """给第 `chapter` 章一条 ACTIVE 且来源指向**当前**正文快照的总结（paired）。

    与 `test_summary_schedule._seed_stale_summary` 相对：那个指向旧快照（stale），
    这个指向当前快照（配对）——用于造「这本书之前已经生成过总结」的半成品书。
    """
    conn = connect(db)
    ch = conn.execute(
        "SELECT id FROM chapter WHERE project_id=? AND number=?", (pid, chapter)
    ).fetchone()["id"]
    snap = conn.execute(
        "SELECT cs.id FROM chapter_snapshot cs "
        "JOIN chapter c ON c.id = cs.chapter_id "
        "WHERE cs.chapter_id = ? AND cs.text_sha256 = c.text_sha256 LIMIT 1",
        (ch,),
    ).fetchone()["id"]
    sid = new_id(EntityType.SUMMARY, pid)
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            source_snapshot_id, replaces_summary_id, schema_version, prompt_hash,
            model_call_id, created_at, source, status
        ) VALUES (?, ?, ?, ?, '已生成', nh_sha256_text('已生成'), ?, NULL,
                  'chapter-summary-v1', 'prompt-x', NULL,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'model', 'ACTIVE')
        """,
        (sid, pid, ch, chapter, snap),
    )
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (sid, ch),
    )
    conn.commit()
    conn.close()


def _conn_factory(db: str):
    from novel_harness.db import connect as _connect

    return lambda: _connect(db)


def _stub_runner(db: str):
    def analyzer(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决到了。",
                        quote="萧决做了些事。",
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return ExtractionRunner(_conn_factory(db), analyzer)


def _stub_summarizer(db: str):
    def analyzer(_request: SummaryRequest) -> CompletionResult:
        return CompletionResult(
            text="这一章萧决做了些事。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return RollingSummarizer(_conn_factory(db), analyzer)


def _runtime(db: str) -> BackgroundRuntime:
    return BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,  # 不自动跑；我们手动 pump / autonomy
        autonomy_seconds=3600.0,
    )


def test_autonomy_schedules_missing_and_pump_fills_them_without_save(
    tmp_path: Path,
) -> None:
    """导入一半生成好的书 → 不点保存 → autonomy 补缺章 → pump 变成真总结。"""
    db, pid, _ = _seed_book(tmp_path, 4)
    _seed_paired_summary(db, pid, 1)
    _seed_paired_summary(db, pid, 2)

    runtime = _runtime(db)
    # 没有焦点：调度坐标 = 前沿章号 + 1 = 5，全部旧章都在 Δ≥1 的过去侧计权。
    enqueued = runtime.autonomy_once()
    assert enqueued == 2, f"缺章 3/4 应入队，实得 {enqueued}"

    runtime.pump_once()

    conn = connect(db)
    try:
        states = {s.chapter_number: s for s in scan_chapter_summary_state(conn, pid, draft_chapter=5)}
        for ch in (1, 2, 3, 4):
            assert states[ch].state == "paired", (
                f"第 {ch} 章应已配对，实得 {states[ch].state}（缺口章没被补上）"
            )
    finally:
        conn.close()


def test_autonomy_exempts_focused_chapter(tmp_path: Path) -> None:
    """§3：焦点中的章（作者正写的那章）在自治轮里被豁免，其余缺章照常入队。"""
    db, pid, _ = _seed_book(tmp_path, 4)
    runtime = _runtime(db)

    # 作者正盯着第 4 章（心跳不过期）。
    conn = connect(db)
    try:
        report_focus(conn, pid, 4)
        conn.commit()
    finally:
        conn.close()

    # 只改 focus 表不应该向 chapter_refresh_attempt 写一行。
    count = connect(db).execute(
        "SELECT COUNT(*) FROM chapter_refresh_attempt"
    ).fetchone()[0]
    assert count == 0, "上报焦点（免费心跳）不应触发任何付费工作"

    enqueued = runtime.autonomy_once()
    assert enqueued == 3, f"缺章 1..3 应入队而 4 豁免，实得 {enqueued}"

    conn = connect(db)
    try:
        rows = conn.execute(
            """
            SELECT c.number, a.summary_state
              FROM chapter_refresh_attempt a
              JOIN chapter_refresh_run r ON r.id = a.run_id
              JOIN chapter c ON c.id = r.chapter_id
             WHERE c.project_id = ?
            """,
            (pid,),
        ).fetchall()
        numbers = {int(r["number"]) for r in rows}
        assert numbers == {1, 2, 3}, f"焦点章 4 不该有 attempt，实得 {sorted(numbers)}"
    finally:
        conn.close()


def test_autonomy_does_not_double_queue_paired_chapters(tmp_path: Path) -> None:
    """§2.2：已配对（正文没再动过）的章不重复入队——自治轮是收敛的。"""
    db, pid, _ = _seed_book(tmp_path, 3)
    _seed_paired_summary(db, pid, 1)
    _seed_paired_summary(db, pid, 2)
    _seed_paired_summary(db, pid, 3)
    runtime = _runtime(db)

    enqueued = runtime.autonomy_once()
    assert enqueued == 0, f"全书已配对不应再入队，实得 {enqueued}"
