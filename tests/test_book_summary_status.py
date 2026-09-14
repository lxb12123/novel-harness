"""全书总结状态视图 + 异常通知（2026-08-18 文档 §6 / Step 4）。

钉住：
- `book_summary_status` 逐章三态（empty/missing/stale/paired）+ has_text + 权重，
  全部查库（不调 LLM）；
- 最近一次总结 attempt 终态 FAILED/BLOCKED = 异常标记（§6）；
- `reconcile_anomaly_notifications`：异常章建一条 `background_failure` OPEN（稳定
  键去重，重复扫不重开），好转解决旧 OPEN、下次再异常又能重开；
- `autonomy_once` 把异常标记同步成通知（同一事务）；
- HTTP 视图 `GET …/summary-status` 一次给全貌（读路径不写库）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.chapter_refresh import MAX_AUTO_RETRIES
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import EntityType, new_id
from novel_harness.summary_schedule import (
    book_summary_status,
    chapter_summary_anomaly,
    reconcile_anomaly_notifications,
)


def _seed_book(tmp_path: Path, chapters: int) -> dict[str, str]:
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="状态", root_path=str(root)).id
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
    return {"db": str(db), "pid": pid, "root": str(root)}


def _seed_summary(db: str, pid: str, chapter: int, *, stale: bool) -> str:
    """给第 `chapter` 章一条 ACTIVE 总结并切 head；`stale` 时指向**旧**正文快照。

    返回 summary 的 id（供断言）。ACTIVE 行的校验要求 sha == sha256(summary)。
    """
    import hashlib

    conn = connect(db)
    ch = conn.execute(
        "SELECT id FROM chapter WHERE project_id=? AND number=?", (pid, chapter)
    ).fetchone()["id"]
    if stale:
        # 旧快照 = 插一条与当前正文不同的快照作 source（指纹对不上 = stale）。
        snap_id = new_id(EntityType.SNAPSHOT, pid)
        conn.execute(
            "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256) "
            "VALUES (?, ?, '旧版', ?)",
            (snap_id, ch, "0" * 64),
        )
        source_snapshot = snap_id
    else:
        snap_row = conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE cs.chapter_id = ? AND cs.text_sha256 = c.text_sha256 LIMIT 1",
            (ch,),
        ).fetchone()
        source_snapshot = snap_row["id"]
    body = f"第 {chapter} 章的总结"
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    sid = new_id(EntityType.SUMMARY, pid)
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            schema_version, prompt_hash, source, status, created_at,
            source_snapshot_id
        ) VALUES (?, ?, ?, ?, ?, ?, 'chapter-summary-v1', ?, 'model', 'ACTIVE',
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?)
        """,
        (sid, pid, ch, chapter, body, sha, f"prompt:{sid}", source_snapshot),
    )
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (sid, ch),
    )
    conn.commit()
    conn.close()
    return sid


_attempt_seq = 0


def _seed_failed_attempt(conn, pid: str, chapter_id: str, *, seed: str = "") -> str:
    global _attempt_seq

    from test_chapter_refresh import an_attempt

    _attempt_seq += 1
    snap = conn.execute(
        "SELECT id FROM chapter_snapshot WHERE chapter_id=? LIMIT 1", (chapter_id,)
    ).fetchone()["id"]
    # **每次 seed 都是一张新的单**：coverage 的唯一键带着 (run, mask)，同一个 run 上
    # 同一个 mask 只会有一张。生产上「好转之后又失败一次」的来法是**正文换了一版**
    # ⇒ 新 generation ⇒ 新 run ⇒ 新单，所以这儿也走 generation 递增，
    # 而不是把一张已经 SUCCEEDED 的单硬掰回 FAILED（那个状态生产上不存在）。
    attempt = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snap,
        generation=_attempt_seq,
    )
    conn.execute(
        "UPDATE chapter_refresh_attempt SET summary_state = 'FAILED' "
        "WHERE id = ? AND summary_state = 'PENDING'",
        (attempt,),
    )
    conn.commit()
    return attempt


def _open_bg_count(db: str, pid: str) -> int:
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM system_notification "
            "WHERE project_id=? AND kind='background_failure' AND status='OPEN'",
            (pid,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_book_status_classifies_empty_missing_paired_stale(tmp_path: Path) -> None:
    wheel = _seed_book(tmp_path, 4)
    _seed_summary(wheel["db"], wheel["pid"], 1, stale=False)  # paired
    _seed_summary(wheel["db"], wheel["pid"], 2, stale=True)   # stale

    conn = connect(wheel["db"])
    try:
        # 把第 3 章设成「没有当前快照」= 空章（正文的 text_sha 对不上任何快照）。
        conn.execute(
            "UPDATE chapter SET text_sha256 = 'deadbeef' "
            "WHERE project_id=? AND number=3",
            (wheel["pid"],),
        )
        conn.commit()
        states = book_summary_status(conn, wheel["pid"], draft_chapter=5)
        by = {s.chapter_number: s for s in states}
        assert by[1].state == "paired" and by[1].has_text is True
        assert by[2].state == "stale" and by[2].has_text is True
        assert by[3].state == "empty" and by[3].has_text is False
        assert by[4].state == "missing" and by[4].has_text is True
        # 权重反馈：draft=5 时 1..4 都在近 10 章窗口内 = 1.0。
        assert all(s.weight == 1.0 for s in by.values())
        # 没有失败 attempt → 全部无异常。
        assert all(not s.anomaly for s in by.values())
    finally:
        conn.close()


def test_anomaly_detection_and_notification_lifecycle(tmp_path: Path) -> None:
    wheel = _seed_book(tmp_path, 1)
    conn = connect(wheel["db"])
    try:
        chapter_id = conn.execute(
            "SELECT id FROM chapter WHERE project_id=? AND number=1",
            (wheel["pid"],),
        ).fetchone()["id"]
        assert chapter_summary_anomaly(conn, wheel["pid"], chapter_id) is False

        _seed_failed_attempt(conn, wheel["pid"], chapter_id)
        assert chapter_summary_anomaly(conn, wheel["pid"], chapter_id) is True

        # 异常章 → 建一条 background_failure OPEN（稳定键；重复扫不重开）。
        statuses = book_summary_status(conn, wheel["pid"], draft_chapter=1)
        assert reconcile_anomaly_notifications(conn, wheel["pid"], statuses) == 1
        conn.commit()
        assert _open_bg_count(wheel["db"], wheel["pid"]) == 1
        assert reconcile_anomaly_notifications(conn, wheel["pid"], statuses) == 0
        conn.commit()
        assert _open_bg_count(wheel["db"], wheel["pid"]) == 1, "去重：异常期间不重复开"

        # 好转 → 旧 OPEN 被解决；下次再异常又能重开。
        conn.execute("UPDATE chapter_refresh_attempt SET summary_state = 'SUCCEEDED'")
        conn.commit()
        healthy = book_summary_status(conn, wheel["pid"], draft_chapter=1)
        assert reconcile_anomaly_notifications(conn, wheel["pid"], healthy) == 0
        conn.commit()
        assert _open_bg_count(wheel["db"], wheel["pid"]) == 0, "好转解决旧 OPEN"

        _seed_failed_attempt(conn, wheel["pid"], chapter_id)
        failed_again = book_summary_status(conn, wheel["pid"], draft_chapter=1)
        assert reconcile_anomaly_notifications(conn, wheel["pid"], failed_again) == 1
        conn.commit()
        assert _open_bg_count(wheel["db"], wheel["pid"]) == 1, "再异常重新 OPEN"
    finally:
        conn.close()


def _runtime(db: str):
    from novel_harness.api.background_runtime import BackgroundRuntime

    def conn_factory():
        from novel_harness.db import connect as _connect

        return _connect(db)

    return BackgroundRuntime(
        db_path=db,
        connection_factory=conn_factory,
        runner_factory=lambda: _dummy_runner(db),
        summarizer_factory=lambda: _dummy_summarizer(db),
        owner="test",
        poll_seconds=100,
        autonomy_seconds=3600.0,
    )


def test_autonomy_once_emits_and_heals_anomaly_notification(tmp_path: Path) -> None:
    """集成：真实 coverage attempt 失败 → autonomy 下一轮标异常并通知，好转即解决。"""
    wheel = _seed_book(tmp_path, 1)
    runtime = _runtime(wheel["db"])

    # 第一轮自治：缺章入队（coverage attempt）。
    assert runtime.autonomy_once() == 1
    # 模拟生成失败：像真的跑完了那样，验证过、抽取过、总结那一支 FAILED（三支都终态——
    # 还有一支没跑完的单是「还在跑」，扫描只会幂等复用它）。
    def fail_pending() -> None:
        conn = connect(wheel["db"])
        try:
            conn.execute(
                "UPDATE chapter_refresh_attempt SET validation_state = 'SUCCEEDED', "
                "extraction_state = 'SUCCEEDED', summary_state = 'FAILED' "
                "WHERE summary_state = 'PENDING' AND trigger_kind = 'coverage'"
            )
            conn.commit()
        finally:
            conn.close()

    fail_pending()

    # 第二轮自治：最近一张 FAILED → **再下一张**（2026-09-14 起有限次自动重试）。
    # 异常与通知按「最近一次 attempt」算，而这一轮先下单再对账：新那张是 PENDING，
    # 所以**重试中的章不红、不通知**——只有机器放弃了（够数）才把账推到作者面前。
    for n in range(1, MAX_AUTO_RETRIES + 1):
        assert runtime.autonomy_once() == 1, f"第 {n} 次重试要再下一张"
        assert _open_bg_count(wheel["db"], wheel["pid"]) == 0, "重试中不通知"
        fail_pending()

    # 够数了：这一轮不再下单，最近一张停在 FAILED → 红、开通知。
    assert runtime.autonomy_once() == 0, "attention_required 不算本轮入队"
    assert _open_bg_count(wheel["db"], wheel["pid"]) == 1

    # 好转：全部 SUCCEEDED → 下一轮自治解决旧 OPEN。
    conn = connect(wheel["db"])
    try:
        conn.execute("UPDATE chapter_refresh_attempt SET summary_state = 'SUCCEEDED'")
        conn.commit()
    finally:
        conn.close()
    runtime.autonomy_once()
    assert _open_bg_count(wheel["db"], wheel["pid"]) == 0


def test_summary_status_endpoint_reports_whole_book(tmp_path: Path) -> None:
    wheel = _seed_book(tmp_path, 3)
    _seed_summary(wheel["db"], wheel["pid"], 1, stale=False)

    import os

    os.environ["NH_DB"] = wheel["db"]
    os.environ["NH_BACKGROUND_RUNTIME"] = "0"
    try:
        from novel_harness.api.app import app

        with TestClient(app) as client:
            r = client.get(f"/api/projects/{wheel['pid']}/summary-status")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["draft_chapter"] == 4  # 无焦点 → 前沿章号 + 1
            assert body["focused_chapter"] is None
            assert len(body["chapters"]) == 3
            states = {row["chapter_number"]: row for row in body["chapters"]}
            assert states[1]["state"] == "paired"
            assert states[2]["state"] == "missing" and states[3]["state"] == "missing"
            assert all(not row["anomaly"] for row in body["chapters"])
            # 视图出参不含内部 chapter_id（对外只给作者看得懂的字段）。
            assert "chapter_id" not in body["chapters"][0]

            r2 = client.get(
                f"/api/projects/{wheel['pid']}/summary-status", params={"draft_chapter": 2}
            )
            assert r2.status_code == 200
            assert r2.json()["draft_chapter"] == 2
    finally:
        os.environ.pop("NH_DB", None)
        os.environ.pop("NH_BACKGROUND_RUNTIME", None)


def _dummy_runner(db: str):
    from novel_harness.draft.provider import CompletionResult
    from novel_harness.extract import RawChapterAnalysis
    from novel_harness.extract.runner import ExtractionRunner

    def analyzer(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(),
                state_updates=(),
                character_profiles=(),
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    from novel_harness.db import connect as _connect

    return ExtractionRunner(lambda: _connect(db), analyzer)


def _dummy_summarizer(db: str):
    from novel_harness.draft.provider import CompletionResult
    from novel_harness.draft.rolling_summary import RollingSummarizer

    def analyzer(_request):
        return CompletionResult(
            text="这一章萧决做了些事。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    from novel_harness.db import connect as _connect

    return RollingSummarizer(lambda: _connect(db), analyzer)
