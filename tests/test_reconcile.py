"""把库和磁盘对一遍 —— **作者不按任何按钮，而且便宜到可以每次回焦都做**。

── 它替掉的是哪颗按钮 ────────────────────────────────────────────────────

「读回改动」要求作者先理解一件他不该知道的事：**屏幕上的正文来自磁盘，库里那份
快照来自这颗按钮**。他不点，后台整理分析的就是旧正文。

## 这份文件钉三样

1. **快路真的不读盘。** 判据是 `reread`（真去读了哪几章）——它是 0 才谈得上
   「每次切回标签页都做一遍」。722 章实测：4ms，零读盘（旧的整条 sync 是 66ms）。
2. **一章坏掉不许拖垮整本。** `sync` 撞上切不出一章的文件直接抛，于是整本都对不上。
   那语义对「作者点了导入」是对的（他在等结果），对这条**没人按过**的路径是错的。
3. **`deep` 那一级不是冗余。** `mtime` 会撒谎，而撒谎那一档只有全量 hash 抓得到。

**第 3 条是这份文件里最容易被删掉的一条**——它测的是一个「几乎不会发生」的情况，
而它一旦发生，作者改过的那一章会**永远**读不回来，且屏幕上没有任何东西说得出来。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph

BOOK = "第一章 甲\n\n一。\n\n第二章 乙\n\n二。\n\n第三章 丙\n\n三。\n"


@pytest.fixture
def book() -> dict[str, str]:
    tmp = Path(tempfile.mkdtemp())
    db, root = tmp / "b.db", tmp / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="t", root_path=str(root)).id
    src = tmp / "s.txt"
    src.write_text(BOOK, encoding="utf-8")
    importer.import_book(SqliteStoryGraph(conn), pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _reconcile(client: TestClient, pid: str, *, deep: bool = False) -> dict:
    r = client.post(f"/api/projects/{pid}/reconcile", params={"deep": str(deep).lower()})
    assert r.status_code == 200, r.text
    return r.json()


def _write(book: dict[str, str], chapter: int, text: str) -> None:
    (Path(book["root"]) / "chapters" / f"{chapter:04d}.md").write_text(text, encoding="utf-8")


def test_nothing_changed_reads_not_a_single_file(client: TestClient, book: dict[str, str]) -> None:
    """**这一条是整个第二层的前提。**

    「每次切回标签页都对一遍」只有在没变时代价接近零才成立。判据是 `reread == []`
    ——一章都没去读盘，于是切章解析和逐章的库查找也全都没发生。
    """
    body = _reconcile(client, book["pid"])
    assert body["checked"] == 3
    assert body["reread"] == [], "什么都没改却去读了文件 —— 快路没生效"
    assert body["refreshed"] == []


def test_a_chapter_edited_outside_is_read_back(client: TestClient, book: dict[str, str]) -> None:
    _write(book, 2, "第二章 乙\n\n他在 WPS 里改了这一段。\n")

    body = _reconcile(client, book["pid"])
    assert body["reread"] == [2]
    assert body["refreshed"] == [2]

    # 读完之后新的 stat 记下来了 —— **不记的话这一章会永远重读**。
    assert _reconcile(client, book["pid"])["reread"] == []


def test_a_file_touched_without_changing_content_settles_after_one_read(
    client: TestClient, book: dict[str, str]
) -> None:
    """内容一模一样、只是被 touch 过（`rsync`、存了一份相同的内容）。

    第一次会读它（stat 对不上，本来就该读），但**不落新快照**（sha 相同），
    而且**第二次不再读**——新 stat 已经记下了。少了后半句，这一章每次回焦都重读。
    """
    path = Path(book["root"]) / "chapters" / "0002.md"
    os.utime(path, ns=(0, 0))

    first = _reconcile(client, book["pid"])
    assert first["reread"] == [2]
    assert first["refreshed"] == [], "内容没变却落了新快照 —— 历史会被刷满一模一样的版本"

    assert _reconcile(client, book["pid"])["reread"] == []


def test_one_broken_chapter_does_not_take_the_whole_book_with_it(
    client: TestClient, book: dict[str, str]
) -> None:
    """第 1 章章标写坏（作者把「第一章 甲」改成了「甲」）。

    **第 3 章那次真实的改动仍然要读回来。** `sync` 在这一档会整条抛，于是整本书
    一章都对不上——那语义对「作者点了导入」是对的（他在等一个结果），对这条
    没人按过的路径是错的。
    """
    _write(book, 1, "甲\n\n一。\n")
    _write(book, 3, "第三章 丙\n\n这一段是新写的。\n")

    body = _reconcile(client, book["pid"])

    assert [r["chapter"] for r in body["refused"]] == [1]
    assert "恰好" in body["refused"][0]["message"], "拒绝的理由要说得出为什么"
    assert body["refreshed"] == [3], "一章坏掉把别的章的改动也拖没了"


def test_deep_catches_what_a_lying_mtime_hides(client: TestClient, book: dict[str, str]) -> None:
    """**`deep` 那一级不是冗余。**

    `rsync -t` / `cp -p` / 从备份恢复 / 某些编辑器都会**保留原 mtime**。这里模拟
    最坏那一种：内容换了、字节数恰好一样、mtime 被塞回改动之前。

    快路（只 stat）**看不见它**——这是 Git 自己也有的病（"racy git"）。
    在此之前兜这一档的是作者手点「读回改动」，那颗按钮退休之后就只剩开书时那一次
    `deep`。**删掉这条测试 = 那一次 deep 迟早会被当成多余的开销删掉。**
    """
    path = Path(book["root"]) / "chapters" / "0002.md"
    before = path.stat()
    original = path.read_text(encoding="utf-8")
    disguised = original.replace("二。", "貳。")  # 同字节数，不同内容
    assert len(disguised.encode()) == len(original.encode()), "这条测试要的是等长改动"
    path.write_text(disguised, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert _reconcile(client, book["pid"])["reread"] == [], "快路居然发现了 —— 这条测试在空转"

    deep = _reconcile(client, book["pid"], deep=True)
    assert deep["reread"] == [1, 2, 3], "deep 该把每一章都读一遍"
    assert deep["refreshed"] == [2], "deep 也没抓到那次伪装的改动"


def test_reconcile_costs_nothing(client: TestClient, book: dict[str, str]) -> None:
    """**它一分钱都不许花。** 判据是 `model_call` 一行都没多——起草 / 抽取 / 总结
    全都往那张表里写，而这条路径是作者没按过的，一次意外扣费都不能有。
    """
    conn = connect(Path(book["db"]))
    try:
        before = conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0]
    finally:
        conn.close()

    _write(book, 2, "第二章 乙\n\n改了。\n")
    _reconcile(client, book["pid"])
    _reconcile(client, book["pid"], deep=True)

    conn = connect(Path(book["db"]))
    try:
        assert conn.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == before
    finally:
        conn.close()
