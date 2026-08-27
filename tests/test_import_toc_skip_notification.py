"""导入丢目录页假章的通知 + 撤销，走真 HTTP（032）。

`text/chapterize.py::drop_toc_duplicates()` 本身的判据钉在 `tests/test_chapterize.py`；
`importer.py` 的过滤范围（只在导入、不在保存）、真书三个数、撤销的重命名/指纹机制
钉在 `tests/test_importer.py`。**这个文件钉的是它们之上那一层**：/import 打了通知
没有、通知长什么样、点「撤销」这个动作是不是真的把章找回来、点第二次/书变过之后
是不是正确地拒绝——全程走 `TestClient`，不直接调 Python 函数。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOC_BOOK = (
    "第一章 山门\n"
    "第二章 落幕\n"
    "\n"
    "第一章 山门\n"
    "\n"
    "萧决拾级而上。\n"
    "\n"
    "第二章 落幕\n"
    "\n"
    "剑光落下。\n"
)

NO_TOC_BOOK = "第一章 山门\n\n萧决拾级而上。\n\n第二章 落幕\n\n剑光落下。\n"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from novel_harness.db import connect, migrate

    db = tmp_path / "book.db"
    conn = connect(db)  # ensure_schema() 拒绝在库不存在时自己建一个空的，先落一份
    migrate(conn)
    conn.close()
    monkeypatch.setenv("NH_DB", str(db))
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _bootstrap(client: TestClient, *, name: str, text: str) -> str:
    r = client.post(
        "/api/projects/bootstrap", json={"mode": "import", "name": name, "text": text}
    )
    assert r.status_code == 200, r.text
    return r.json()["project"]["id"]


def _notifications(client: TestClient, pid: str) -> list[dict]:
    r = client.get(f"/api/projects/{pid}/notifications")
    assert r.status_code == 200, r.text
    return r.json()


def test_import_with_a_toc_page_files_a_notification_with_an_undo_action(
    client: TestClient,
) -> None:
    pid = _bootstrap(client, name="带目录的书", text=TOC_BOOK)

    items = _notifications(client, pid)
    toc = [n for n in items if n["kind"] == "import_toc_skipped"]
    assert len(toc) == 1, f"应该恰好一条 import_toc_skipped 通知：{items}"
    notice = toc[0]
    # 出参不带渲染好的句子（国际化第四批 Phase B）：`title_code` + `title_params`
    # 才是措辞的源，前端拿它们去 `backendMessages.ts` 按界面语言渲染整句。
    assert notice["title_code"] == "import_toc_skipped_title"
    assert notice["title_params"] == {"count": 2}
    assert notice["actions"] == ["undo_toc_skip"]


def test_import_into_an_existing_project_also_files_the_notification(client: TestClient) -> None:
    """`/import`（往已有项目导，不是 `/bootstrap` 新建）走的是另一个 handler——
    两条路都要打通知，不能只顾了先写的那一条。"""
    created = client.post("/api/projects", json={"name": "先建个空壳"})
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    r = client.post(f"/api/projects/{pid}/import", json={"text": TOC_BOOK})
    assert r.status_code == 200, r.text

    items = _notifications(client, pid)
    toc = [n for n in items if n["kind"] == "import_toc_skipped"]
    assert len(toc) == 1
    assert toc[0]["actions"] == ["undo_toc_skip"]


def test_import_without_a_toc_collision_files_no_notification(client: TestClient) -> None:
    """绝大多数书：一条 `import_toc_skipped` 通知都不该出现。"""
    pid = _bootstrap(client, name="没有目录的书", text=NO_TOC_BOOK)

    items = _notifications(client, pid)
    assert [n for n in items if n["kind"] == "import_toc_skipped"] == []


def test_clicking_undo_restores_the_chapters_in_order_and_resolves_the_notice(
    client: TestClient,
) -> None:
    pid = _bootstrap(client, name="点一次撤销", text=TOC_BOOK)
    notice = next(n for n in _notifications(client, pid) if n["kind"] == "import_toc_skipped")

    before = client.get(f"/api/projects/{pid}/chapters")
    assert before.status_code == 200
    assert len(before.json()) == 2

    r = client.post(f"/api/projects/{pid}/notifications/{notice['id']}/undo-toc-skip")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": notice["id"], "status": "RESOLVED", "restored": 2}

    after = client.get(f"/api/projects/{pid}/chapters")
    assert after.status_code == 200
    numbers_and_titles = [(c["number"], c["title"]) for c in after.json()]
    assert numbers_and_titles == [
        (1, "第一章 山门"),
        (2, "第二章 落幕"),
        (3, "第一章 山门"),
        (4, "第二章 落幕"),
    ]

    ch1 = client.get(f"/api/projects/{pid}/chapters/1/text")
    assert ch1.status_code == 200
    assert ch1.json()["markdown"] == "第一章 山门\n\n"
    ch3 = client.get(f"/api/projects/{pid}/chapters/3/text")
    assert "萧决拾级而上。" in ch3.json()["markdown"]

    # 通知已经 RESOLVED：默认列表（只给 OPEN）里不再出现。
    assert [n for n in _notifications(client, pid) if n["kind"] == "import_toc_skipped"] == []


def test_undo_refuses_after_the_book_changed_and_leaves_everything_untouched(
    client: TestClient,
) -> None:
    pid = _bootstrap(client, name="书变过再撤销", text=TOC_BOOK)
    notice = next(n for n in _notifications(client, pid) if n["kind"] == "import_toc_skipped")

    ch1 = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={
            "markdown": "第一章 山门\n\n萧决拾级而上，风很大。\n",
            "expected_text_sha256": ch1["text_sha256"],
        },
    )
    assert saved.status_code == 200, saved.text

    r = client.post(f"/api/projects/{pid}/notifications/{notice['id']}/undo-toc-skip")

    assert r.status_code == 409, r.text
    assert "改过了" in r.json()["detail"]["message"]
    # 拒绝之后通知还在 OPEN——没有被误标 RESOLVED，作者还能重新导入原文件补救。
    assert notice["id"] in [
        n["id"] for n in _notifications(client, pid) if n["kind"] == "import_toc_skipped"
    ]
    after = client.get(f"/api/projects/{pid}/chapters")
    assert len(after.json()) == 2, "拒绝必须是全有全无，章数不许被半途改动"


def test_undo_on_an_unrelated_notification_id_is_refused(client: TestClient) -> None:
    pid = _bootstrap(client, name="乱指一个通知号", text=NO_TOC_BOOK)

    r = client.post(f"/api/projects/{pid}/notifications/does-not-exist/undo-toc-skip")

    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "notification_not_found"
