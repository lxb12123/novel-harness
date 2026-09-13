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
import seed
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
    """导入一半生成好的书 → 不点保存 → autonomy 补缺章 → pump 变成真总结。

    1/2 章总结和抽取都齐了，3/4 章两样都没有——**只有后两章该被排上**。
    （2026-08-25 之前这条只给 1/2 造总结，抽取那一维当时还没人问。）
    """
    db, pid, _ = _seed_book(tmp_path, 4)
    for ch in (1, 2):
        _seed_paired_summary(db, pid, ch)
        seed.applied_extraction(db, pid, ch)

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


def test_autonomy_does_not_double_queue_chapters_with_nothing_left_to_do(
    tmp_path: Path,
) -> None:
    """§2.2：该做的都做完了的章不重复入队——自治轮是收敛的。

    **「做完」= 两维都做完**（2026-08-25）：总结配对**且**抽取跑过。
    只造总结那一半的话这条会红，而那正是这次改动要的行为。
    """
    db, pid, _ = _seed_book(tmp_path, 3)
    for ch in (1, 2, 3):
        _seed_paired_summary(db, pid, ch)
        seed.applied_extraction(db, pid, ch)
    runtime = _runtime(db)

    enqueued = runtime.autonomy_once()
    assert enqueued == 0, f"两维都齐了不应再入队，实得 {enqueued}"


def test_a_book_whose_summaries_are_all_paired_still_gets_extraction_ordered(
    tmp_path: Path,
) -> None:
    """**这次改动的核心断言**：总结全齐、抽取一次没跑 → 照样下单，且单里带抽取那一支。

    2026-08-25 之前这本书的扫描结论全是 `paired`，**下了 0 张单**——真书 158 章里有
    155 章正是这个形态（导进来的，从没保存过），于是它们永远不会被抽取。
    """
    from novel_harness.chapter_refresh import BRANCH_EXTRACTION, BRANCH_SUMMARY
    from novel_harness.summary_schedule import schedule_alignment

    db, pid, _ = _seed_book(tmp_path, 3)
    for ch in (1, 2, 3):
        _seed_paired_summary(db, pid, ch)  # 抽取那一维故意不造

    conn = connect(db)
    try:
        states = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, pid, draft_chapter=5)
        }
        for ch in (1, 2, 3):
            assert states[ch].state == "paired", "前提：总结那一维是齐的"
            assert states[ch].extraction == "missing", "前提：抽取那一维是空的"
            assert states[ch].needs_work, "总结齐了不等于这一章没活要干"

        decisions = schedule_alignment(
            conn, pid, draft_chapter=5, ruleset_epoch=1, ruleset_hash="hash"
        )
        conn.commit()
        assert set(decisions.values()) == {"queued_extraction"}, decisions

        # 单**自带抽取那一支**，且**不带总结那一支**（总结本来就不缺，别重买）。
        masks = [
            int(row["missing_branch_mask"])
            for row in conn.execute(
                "SELECT missing_branch_mask FROM chapter_refresh_attempt"
            ).fetchall()
        ]
        assert masks, "一张单都没下"
        for mask in masks:
            assert mask & BRANCH_EXTRACTION, f"单里没有抽取那一支：mask={mask}"
            assert not mask & BRANCH_SUMMARY, f"总结不缺却排了总结：mask={mask}"
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-13：没配好不扫、配好那一刻就扫（作者第一次用桌面版指出来的）
# ══════════════════════════════════════════════════════════════════════════


def _loop_once(runtime: BackgroundRuntime) -> None:
    """跑 `_loop` 的一拍：第一次 `sleep` 就叫停。"""
    def stop(_: float) -> None:
        runtime.stop()

    runtime._sleep = stop  # noqa: SLF001
    runtime._loop()  # noqa: SLF001


def _attempts(db: str) -> int:
    conn = connect(db)
    try:
        return int(conn.execute("SELECT count(*) FROM chapter_refresh_attempt").fetchone()[0])
    finally:
        conn.close()


def test_sweep_waits_for_the_model_to_be_configured(tmp_path: Path) -> None:
    """模型服务没配好，到点也不下单——下了也只是二十张必败的单和二十条「后台任务未完成」，
    而作者还没填钥匙。配好之后同一拍就下单。"""
    db, _, _ = _seed_book(tmp_path, 3)
    configured = {"on": False}
    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,
        autonomy_seconds=3600.0,
        first_sweep_seconds=0.0,  # 到点了
        configured=lambda: configured["on"],
    )
    _loop_once(runtime)
    assert _attempts(db) == 0, "没配好就不该下单"

    configured["on"] = True
    runtime.kick()
    runtime._stop.clear()  # noqa: SLF001
    _loop_once(runtime)
    assert _attempts(db) == 3, "配好 + kick 之后这一拍就该把缺的章都排上"


def test_kick_brings_the_next_sweep_forward(tmp_path: Path) -> None:
    """默认要等一个间隔；`kick()` 之后下一拍就扫。"""
    db, _, _ = _seed_book(tmp_path, 2)
    runtime = _runtime(db)  # autonomy_seconds=3600：不 kick 的话这一拍不会扫
    _loop_once(runtime)
    assert _attempts(db) == 0

    runtime.kick()
    runtime._stop.clear()  # noqa: SLF001
    _loop_once(runtime)
    assert _attempts(db) == 2
