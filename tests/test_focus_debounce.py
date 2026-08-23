"""当前章焦点防抖（2026-08-18 文档 §3 / Step 1）。

钉住：正在写的那一章，保存触发**不排总结**（但验证/抽取照跑）；一旦切走（焦点
移到别章或心跳过期），那一章才重新够格；已经入队的任务即使作者回来也不停。

**2026-08-23 起还钉执行层**：从前防抖只挡到「下单」为止——单上把总结位剥掉了，
协调器却一眼都不看那张单，照样调了一次总结模型（实测）。于是「作者改一下午、
存三十次，一次都不买」这句话在执行层不成立，而「覆写单真去重买一份」那条裁定的
成本论证靠的正是它。链条整条钉在
`test_a_long_afternoon_of_saves_buys_nothing_and_leaving_buys_exactly_one`。
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


def _counting_runtime(db: str, calls: dict[str, int]):
    """真协调器 + 会数数的桩 adapter：一次「调模型」就是 `calls` 上的一笔。

    防抖这条纪律的单位是**钱**，所以守卫必须数模型调用，不能只看 attempt 的状态列
    ——状态列在出 bug 的那一版里也是对的（mask 剥了总结位），真金白银照样花了。
    """
    from novel_harness.api.background_runtime import BackgroundRuntime
    from novel_harness.draft.provider import CompletionResult
    from novel_harness.draft.rolling_summary import RollingSummarizer
    from novel_harness.extract import RawChapterAnalysis
    from novel_harness.extract.runner import ExtractionRunner

    def conn_factory():
        return connect(db)

    def extraction(_request):
        calls["extraction"] = calls.get("extraction", 0) + 1
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(), state_updates=(), character_profiles=()
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    def summarize(_request):
        calls["summary"] = calls.get("summary", 0) + 1
        return CompletionResult(
            text="这一章萧决做了些事。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    return BackgroundRuntime(
        db_path=db,
        connection_factory=conn_factory,
        runner_factory=lambda: ExtractionRunner(conn_factory, extraction),
        summarizer_factory=lambda: RollingSummarizer(conn_factory, summarize),
        owner="test",
        poll_seconds=100,
        autonomy_seconds=3600.0,
    )


def test_a_long_afternoon_of_saves_buys_nothing_and_leaving_buys_exactly_one(
    client: TestClient, book: dict[str, str]
) -> None:
    """整条防抖链（2026-08-23）：**不是「每保存一次买一次」，是「每离开一次改动过的章买一次」。**

        作者正在改第 1 章        → 这一章不排总结（焦点防抖）
        他改了一下午、存了 30 次 → 一次都不买
        切走                     → 才够格
        生成总结                 → 幂等（同一份正文只买一次）

    第二步从前是假的：单上没有总结那一支，执行层却不看单，照跑不误——三十次保存
    就是三十次模型调用。第三、四步是「覆写单真去重买一份」那条裁定的另一半：
    够格之后**真的买**（不是只提示），而且同一份正文只买一次。
    """
    pid = book["pid"]
    calls: dict[str, int] = {}
    runtime = _counting_runtime(book["db"], calls)

    client.post(f"/api/projects/{pid}/focus", json={"chapter": 1})
    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    text, sha = body["markdown"], body["text_sha256"]
    for i in range(30):
        text = text + f"\n他又改了第 {i} 遍。\n"
        saved = client.put(
            f"/api/projects/{pid}/chapters/1/text",
            json={"markdown": text, "expected_text_sha256": sha},
        )
        assert saved.status_code == 200, saved.text
        sha = saved.json()["text_sha256"]
        runtime.pump_once()
        # 后台那半小时一次的扫描也照样扫到它 —— 焦点章是扫描唯一的例外。
        runtime.autonomy_once()
        runtime.pump_once()
    assert calls.get("summary", 0) == 0, (
        f"作者正改着这一章，30 次保存买了 {calls.get('summary', 0)} 次总结 —— "
        "防抖在执行层没生效（单上剥了总结位，协调器却不看单）"
    )

    # 切走 → 这一章才够格。定期扫描是主路（不必再保存一次）。
    client.post(f"/api/projects/{pid}/focus", json={"chapter": 2})
    assert runtime.autonomy_once() == 1, "切走之后这一章该进这一轮的名额"
    runtime.pump_once()
    assert calls.get("summary", 0) == 1, (
        f"切走之后该买且只买一份，实得 {calls.get('summary', 0)} 次"
    )

    # 幂等：同一份正文再扫再跑，一分钱都不再花。
    assert runtime.autonomy_once() == 0, "补完再扫不许产生新单"
    runtime.pump_once()
    assert calls.get("summary", 0) == 1, "同一份正文被买了第二次（幂等破了）"

    # ── 第二轮：他回来又改一下午。这一次库里已经挂着一份总结了 = **覆写**场景 ──
    # 「覆写单真去重买一份、自动覆盖」那条裁定的成本论证就在这儿：覆写的频率不是
    # 保存次数，是**离开次数**。
    client.post(f"/api/projects/{pid}/focus", json={"chapter": 1})
    for i in range(30):
        text = text + f"\n回来又改了第 {i} 遍。\n"
        saved = client.put(
            f"/api/projects/{pid}/chapters/1/text",
            json={"markdown": text, "expected_text_sha256": sha},
        )
        assert saved.status_code == 200, saved.text
        sha = saved.json()["text_sha256"]
        runtime.pump_once()
        runtime.autonomy_once()
        runtime.pump_once()
    assert calls.get("summary", 0) == 1, (
        f"改的是已经有总结的章，30 次保存买了 {calls.get('summary', 0) - 1} 次覆写"
    )

    client.post(f"/api/projects/{pid}/focus", json={"chapter": 2})
    assert runtime.autonomy_once() == 1, "切走之后这一章的覆写单该下出来"
    runtime.pump_once()
    assert calls.get("summary", 0) == 2, (
        "**每离开一次改动过的章买一次** —— 这一次是覆写，"
        f"实得总共 {calls.get('summary', 0)} 次"
    )
    assert runtime.autonomy_once() == 0, "覆写完再扫不许再下单"


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
