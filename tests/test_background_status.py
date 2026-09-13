"""顶栏那盏灯读的一行（`GET /api/projects/{pid}/background`，2026-09-13）。

作者第一次用桌面版：「一旦我切换到角色栏……再返回这个状态就丢失」「我都不知道现在是
成功了还是失败了」。这条路由把「有没有活在跑」变成一个随时能问的事实；这里钉住的是
它和 dispatcher 用同一条判据（`chapter_refresh.OUTSTANDING_WHERE`）、在跑和排着分得开、
配没配好说实话。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.api.background_runtime import BackgroundRuntime
from novel_harness.chapter_refresh import claim_attempt
from novel_harness.db import connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    db = tmp_path / "light.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="灯", root_path=str(root)).id
    src = tmp_path / "s.txt"
    src.write_text(
        "\n\n".join(f"第{n}章 甲{n}\n\n萧决在第 {n} 章做了些事。\n" for n in (1, 2, 3)),
        encoding="utf-8",
    )
    importer.import_book(SqliteStoryGraph(conn), pid, txt=src, root=root)
    conn.commit()
    conn.close()
    monkeypatch.setenv("NH_DB", str(db))
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return {"db": str(db), "pid": pid}


@pytest.fixture
def client(book: dict[str, str]) -> Iterator[TestClient]:
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def test_idle_book_reports_nothing_running_and_whether_the_model_is_configured(
    client: TestClient, book: dict[str, str]
) -> None:
    r = client.get(f"/api/projects/{book['pid']}/background")
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": False, "running": [], "queued": []}

    client.put(
        "/api/settings",
        json={"base_url": "https://x.example", "model": "m", "api_key": "sk-123456"},
    )
    assert client.get(f"/api/projects/{book['pid']}/background").json()["configured"] is True


def test_queued_and_running_are_told_apart(client: TestClient, book: dict[str, str]) -> None:
    """排了单但没人领 = queued；被 dispatcher 领走（lease 未过期）= running。
    同一章不会两边都出现。"""
    # 让扫描给三章都下单（只写 attempt，不付模型——两个 factory 永远不会被叫到）。
    def never() -> object:
        raise AssertionError("这条测试不该跑到模型")

    BackgroundRuntime(
        db_path=book["db"],
        connection_factory=lambda: connect(book["db"]),
        runner_factory=never,
        summarizer_factory=never,
        owner="light-test",
        poll_seconds=100,
    ).autonomy_once()
    conn = connect(book["db"])
    try:
        first = conn.execute(
            "SELECT a.id FROM chapter_refresh_attempt a JOIN chapter_refresh_run r ON r.id=a.run_id "
            "JOIN chapter c ON c.id=r.chapter_id WHERE c.number=1"
        ).fetchone()["id"]
        r = client.get(f"/api/projects/{book['pid']}/background").json()
        assert r["running"] == [] and r["queued"] == [1, 2, 3]

        assert claim_attempt(conn, first, owner="light-test", ttl_seconds=60) is not None
        conn.commit()
        r = client.get(f"/api/projects/{book['pid']}/background").json()
        assert r["running"] == [1] and r["queued"] == [2, 3]
    finally:
        conn.close()


def test_a_manual_analysis_run_shows_up_too(client: TestClient, book: dict[str, str]) -> None:
    """「分析本章」那颗按钮下的单在 `extraction_run` 表里，不经过 attempt——灯也得认。

    不走 `POST …/extract`：TestClient 会在响应之后同步把 BackgroundTasks 跑完，没有模型
    那条 run 当场就 FAILED；这里只要「排着 / 在跑」那两拍。"""
    from novel_harness.api.deps import get_extraction_runner

    runner = get_extraction_runner()
    run = runner.enqueue(book["pid"], 2)
    r = client.get(f"/api/projects/{book['pid']}/background").json()
    assert r["queued"] == [2] and r["running"] == []

    conn = connect(book["db"])
    try:
        conn.execute("UPDATE extraction_run SET status='RUNNING' WHERE id=?", (run.id,))
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"/api/projects/{book['pid']}/background").json()
    assert r["running"] == [2] and r["queued"] == []
