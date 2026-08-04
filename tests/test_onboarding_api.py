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
