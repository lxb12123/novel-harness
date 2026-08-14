"""**后台整理不可能分析旧正文** —— 这是保证，不是尽力而为。

── 这条缝长什么样 ────────────────────────────────────────────────────────

抽取是**按快照跑的**（`extract/runner.py` 那条 `text_sha256 = chapter.text_sha256`
的 JOIN），而在 2026-08-14 之前，全系统**没有任何读路径**比较过这两个东西：

    chapter.text_sha256    库以为磁盘上是什么
    磁盘文件的真实内容       磁盘上真的是什么

唯一比过的是**保存**那条路（`importer.save_chapter` 的 `expected_sha256`，防写作助手
覆盖作者刚敲的字）。读路径一处都没有。

于是：作者在 WPS 里写完第 N 章 → 切走（换章触发后台整理）→ **它照着旧正文分析**，
右栏「待确认」里摆出来的是上一版的情节。**而屏幕上没有任何东西说它读的是旧的**——
这是这个仓库反复在修的那一种失败：看起来完全正常的假页面。

在此之前唯一的出路是作者去点「读回改动」，而那颗按钮要求他先理解
「屏幕上的正文来自磁盘、库里的快照来自那颗按钮」——**这个分工本来就不该让他知道**。

── 判据 ──────────────────────────────────────────────────────────────────

只断言两头，中间一层都不碰（同 `test_extractor_feeds_r3.py`：哑火那十一天里每一层
单看都是对的，断的是层与层之间）：

    磁盘上那一章改过了   →   `POST …/autopilot` 之后，库里那份快照就是磁盘那份

**外加一条基线**：不改磁盘时它一条快照都不多落。少了这条，一个「每次都重写一遍」的
实现照样全绿，而那种实现会把作者的历史刷满一堆一模一样的版本。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph

BOOK = (
    "第一章 山门\n"
    "\n"
    "萧决拾级而上，山门在雾里。\n"
    "\n"
    "第二章 落幕\n"
    "\n"
    "剑光落下。\n"
)


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="山门记", root_path=str(root)).id
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK, encoding="utf-8")
    importer.import_book(SqliteStoryGraph(conn), pid, txt=txt, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 模型没配 —— **这条测试一分钱都不该花**。后台整理照旧会跑「读回这一章」那一步
    # （它不调模型），只是总结/抽取会以 `model_not_configured` 退回。
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _snapshots(db: str, pid: str, chapter: int) -> list[str]:
    """第 `chapter` 章在库里的全部快照正文，按写入顺序。"""
    conn = connect(Path(db))
    try:
        rows = conn.execute(
            "SELECT cs.text FROM chapter_snapshot cs JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = ? ORDER BY cs.created_at, cs.id",
            (pid, chapter),
        ).fetchall()
        return [r["text"] for r in rows]
    finally:
        conn.close()


def _autopilot(client: TestClient, pid: str, chapter: int) -> dict:
    r = client.post(f"/api/projects/{pid}/chapters/{chapter}/autopilot")
    assert r.status_code == 202, r.text
    return r.json()


def test_a_chapter_edited_outside_is_read_back_before_anything_runs(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这份文件的头等大事。** 在别的软件里改完，后台整理看到的就是新那份。"""
    改过的 = "第二章 落幕\n\n剑光落下，萧决再没有起来。\n"
    (Path(book["root"]) / "chapters" / "0002.md").write_text(改过的, encoding="utf-8")

    # 这一下之前，库里还是导入时那一份（作者没点过任何东西）。
    assert 改过的 not in _snapshots(book["db"], book["pid"], 2)

    _autopilot(client, book["pid"], 2)

    assert _snapshots(book["db"], book["pid"], 2)[-1] == 改过的, (
        "后台整理跑起来了，而库里那份快照还是旧的 —— 它分析的就是旧正文"
    )


def test_nothing_changed_means_nothing_written(
    client: TestClient, book: dict[str, str]
) -> None:
    """**基线：磁盘没变就一条快照都不多落。**

    少了这条，一个「每次换章都重写一遍」的实现照样绿——而那种实现会把作者的
    「历史」刷满一堆一模一样的版本，等他真要还原时根本挑不出来。
    """
    before = _snapshots(book["db"], book["pid"], 2)
    _autopilot(client, book["pid"], 2)
    _autopilot(client, book["pid"], 2)
    assert _snapshots(book["db"], book["pid"], 2) == before


def test_a_broken_chapter_heading_is_said_out_loud(
    client: TestClient, book: dict[str, str]
) -> None:
    """章标写坏了 → **回执里说得出来**，不是静默地什么都不做（约束 8）。

    这一档是真的会发生的：作者在 WPS 里把「第二章 落幕」改成了「落幕」，于是切章器
    认不出它。**后果是这一章从此既总结不了也抽不了**，而在这条之前他完全看不出为什么
    ——后台动作没有回执可看，屏幕上只是「什么都没发生」。
    """
    (Path(book["root"]) / "chapters" / "0002.md").write_text("落幕\n\n剑光落下。\n", encoding="utf-8")

    body = _autopilot(client, book["pid"], 2)

    stages = [e["stage"] for e in body["errors"]]
    assert "manuscript" in stages, body
    said = next(e for e in body["errors"] if e["stage"] == "manuscript")
    assert said["code"] == "chapter_file_unreadable"
    assert "第 2 章" in said["message"]
