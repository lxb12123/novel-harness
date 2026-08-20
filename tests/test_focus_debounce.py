"""当前章焦点防抖（2026-08-18 文档 §3 / Step 1）。

钉住：正在写的那一章，保存触发**不排总结**（但验证/抽取照跑）；一旦切走（焦点
移到别章或心跳过期），那一章才重新够格；已经入队的任务即使作者回来也不停。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.focus import FOCUS_TTL, report_focus
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.declare import Ledger


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="焦点", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    Ledger(store, conn, pid).declare_node(NodeLabel.CHARACTER, "萧决")
    src = tmp_path / "s.txt"
    src.write_text("第一章 甲\n\n萧决走进了青云城。\n", encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NH_DB", book["db"])
    monkeypatch.setenv("NH_BACKGROUND_RUNTIME", "0")
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _summary_attempts(book: dict[str, str], chapter: int) -> int:
    from novel_harness.db import connect as c

    conn = c(book["db"])
    row = conn.execute(
        """
        SELECT COUNT(*) FROM chapter_refresh_attempt a
        JOIN chapter_refresh_run r ON r.id = a.run_id
        JOIN chapter ch ON ch.id = r.chapter_id
        WHERE ch.project_id = ? AND ch.number = ?
        """,
        (book["pid"], chapter),
    ).fetchone()
    conn.close()
    return int(row[0])


def test_focus_upsert_is_single_row_and_expiry_releases(client, book) -> None:
    pid = book["pid"]
    conn = connect(book["db"])
    try:
        report_focus(conn, pid, 1)
        report_focus(conn, pid, 1)
        conn.commit()
        rows = conn.execute(
            "SELECT COUNT(*) FROM chapter_focus WHERE project_id = ?", (pid,)
        ).fetchone()[0]
        assert rows == 1, "同一个项目只许有一行焦点"
        cur = conn.execute(
            "SELECT chapter_number FROM chapter_focus WHERE project_id = ?", (pid,)
        ).fetchone()
        assert int(cur[0]) == 1
    finally:
        conn.close()

    import novel_harness.focus as focus_mod

    # 过期 = 人不在 → 解除保护。
    conn = connect(book["db"])
    try:
        conn.execute(
            "UPDATE chapter_focus SET updated_at = ? WHERE project_id = ?",
            (
                (datetime.now(timezone.utc) - FOCUS_TTL - timedelta(seconds=1)).isoformat(),
                pid,
            ),
        )
        conn.commit()
        assert focus_mod.focused_on(conn, pid) is None
    finally:
        conn.close()


def test_save_while_focused_skips_summary_but_schedules_other_branches(
    client: TestClient, book: dict[str, str]
) -> None:
    """正写的第 1 章 + 保存：验证/抽取照排，但**不排总结**（防抖 §3）。"""
    pid = book["pid"]
    r = client.post(f"/api/projects/{pid}/focus", json={"chapter": 1})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    new_text = body["markdown"] + "\n萧决在结尾补了一句。\n"
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": new_text, "expected_text_sha256": body["text_sha256"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["changed"] is True

    conn = connect(book["db"])
    try:
        missing = conn.execute(
            """
            SELECT missing_branch_mask FROM chapter_refresh_attempt a
            JOIN chapter_refresh_run r ON r.id = a.run_id
            JOIN chapter c ON c.id = r.chapter_id
            WHERE c.project_id = ? AND c.number = 1
            ORDER BY a.created_at DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    # mask 不该含总结位（bit 2 == 4 值 2 的那一位）。
    mask = int(missing[0]) if missing else 0
    assert (mask & 0b010) == 0, f"焦点中的章不许排总结分支，mask={mask:b}"
    # 但至少要排了点别的（验证/抽取，位 0b100 或 0b001）。
    assert (mask & 0b101) != 0, f"验证/抽取分支必须照排，mask={mask:b}"


def test_save_after_leaving_schedules_summary(client: TestClient, book) -> None:
    """切走后（焦点移到别的章）保存：总结分支恢复够格。"""
    pid = book["pid"]
    client.post(f"/api/projects/{pid}/focus", json={"chapter": 2})  # 作者切到第 2 章

    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    # 第 1 章已不是焦点章，保存它 → 总结分支该排。
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": body["markdown"] + "\n萧决又补了一句。\n",
              "expected_text_sha256": body["text_sha256"]},
    )
    assert saved.status_code == 200, saved.text

    conn = connect(book["db"])
    try:
        missing = conn.execute(
            """
            SELECT missing_branch_mask FROM chapter_refresh_attempt a
            JOIN chapter_refresh_run r ON r.id = a.run_id
            JOIN chapter c ON c.id = r.chapter_id
            WHERE c.project_id = ? AND c.number = 1
            ORDER BY a.created_at DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    mask = int(missing[0]) if missing else 0
    assert (mask & 0b010) != 0, f"切走后总结分支必须恢复，mask={mask:b}"


def _chapter_markdown(book):
    from novel_harness.db import connect as c

    conn = c(book["db"])
    row = conn.execute(
        "SELECT cs.text FROM chapter_snapshot cs JOIN chapter ch ON ch.id = cs.chapter_id "
        "WHERE ch.project_id = ? AND ch.number = 1 AND cs.text_sha256 = ch.text_sha256",
        (book["pid"],),
    ).fetchone()
    conn.close()
    return row["text"]


def test_returning_to_a_chapter_does_not_stop_its_queued_summary(
    client: TestClient, book: dict[str, str]
) -> None:
    """「回来不停队」：总结已入队（PENDING）后作者切回来，队列照跑不被打断。

    §3 关键：防抖只挡「从未离开过、正写着的当时那一刻」，不挡已入队的后续。
    """
    pid = book["pid"]
    # 作者在第 2 章（焦点在别处）→ 第 1 章够格，保存后总结入队（PENDING）。
    client.post(f"/api/projects/{pid}/focus", json={"chapter": 2})
    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": body["markdown"] + "\n萧决又补了一句。\n",
              "expected_text_sha256": body["text_sha256"]},
    )
    conn = connect(book["db"])
    try:
        row = conn.execute(
            """
            SELECT a.id, a.summary_state FROM chapter_refresh_attempt a
            JOIN chapter_refresh_run r ON r.id = a.run_id
            JOIN chapter c ON c.id = r.chapter_id
            WHERE c.project_id = ? AND c.number = 1 AND a.summary_state = 'PENDING'
            ORDER BY a.created_at DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
        assert row is not None, "切走保存后第 1 章的总结应该已经入队（PENDING）"
        attempt_id = row["id"]
    finally:
        conn.close()

    # 作者切回第 1 章（焦点回到本章）→ 入队的任务**不停**。
    client.post(f"/api/projects/{pid}/focus", json={"chapter": 1})
    conn = connect(book["db"])
    try:
        row = conn.execute(
            "SELECT summary_state FROM chapter_refresh_attempt WHERE id = ?", (attempt_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["summary_state"] == "PENDING", (
        "回到本章必须不停掉已入队的总结任务（不变量：防抖不撤销已排队动作）"
    )
