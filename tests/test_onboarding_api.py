"""新书启动 HTTP 契约：路由只是 onboarding 原子操作的薄壳。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness.api.app import app
from novel_harness.db import connect, migrate


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "story.db"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    monkeypatch.setenv("NH_DB", str(db_path))
    monkeypatch.setenv("NH_BOOKS_DIR", str(tmp_path / "books"))
    with TestClient(app) as test_client:
        yield test_client


def test_bootstrap_import_creates_project_chapters_and_report(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={
            "mode": "import",
            "name": "青云记",
            "text": "第一章 初见\n\n风起。\n\n第二章 重逢\n\n云归。\n",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["name"] == "青云记"
    assert body["initial_chapter"] == 1
    assert body["import_report"]["chapter_count"] == 2
    assert (tmp_path / "books" / "青云记" / "chapters" / "0001.md").is_file()


def test_bootstrap_blank_rejects_import_text_field(client: TestClient) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={"mode": "blank", "name": "空白书", "text": "不应接受"},
    )

    assert response.status_code == 422, response.text


def test_bootstrap_zero_chapter_import_leaves_no_project_or_files(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={"mode": "import", "name": "没有章标", "text": "只有正文，没有任何章标。"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"] == "import_refused"
    assert client.get("/api/projects").json() == []
    books = tmp_path / "books"
    assert not (books / "没有章标").exists()
    assert list(books.glob(".nh-bootstrap-*")) == []


def test_a_bootstrapped_book_can_be_analyzed(client: TestClient) -> None:
    """从工作台「新建 / 导入」建出来的书，「分析本章」得能起步（202），不能 500。

    2026-09-13 之前 `bootstrap_project()` 走的 `project.insert()` 不写 ruleset 基线行，
    `POST …/extract` 在 `runner.enqueue()` 撞 `ExtractionRunStateError` 500——桌面版第一次
    上手作者点了那颗按钮，屏幕只闪一下，什么都没说。基线行现在跟 project 行同一笔事务写。
    """
    made = client.post(
        "/api/projects/bootstrap",
        json={"mode": "import", "name": "能分析", "text": "第一章 初见\n\n风起。\n"},
    )
    assert made.status_code == 200, made.text
    pid = made.json()["project"]["id"]

    queued = client.post(f"/api/projects/{pid}/chapters/1/extract")
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "PENDING"
