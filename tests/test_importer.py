"""导入器 —— TXT → `chapters/NNNN.md` → 库。

**全部走真库**（`db.connect(IN_MEMORY)` + `db.migrate` + 真 `SqliteStoryGraph`）+ 真磁盘
（`tmp_path`），零 Fake：这一层要证的是「写进盘的字节、快照存的字节、chapterize 读回来
的字节是同一份」，而 Fake 对那件事一无所知。

书用 `tests/fixtures/demo_novel.txt`（**46 行、3 章**——`scripts/demo.sh` 的
`NH_DEMO_CHAPTERS=3` 是权威）。它证明的只是「切章器在手写的脏数据上不切歪」，
对真书的分卷重启和几十种卷标题写法一个字都没说（那条验收至今 BLOCKED）。

这里最重要的一条是 `test_a_reexport_reads_back_as_exactly_one_chapter`：往
`chapter_text()` 里加一个 `# ` 前缀会让每个文件都切出零章，而那一刻 chapters/ 看起来
比现在漂亮——它是 round-trip 的绊线。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import (
    ImportRefused,
    SyncRefused,
    chapter_path,
    chapter_text,
    import_book,
    sync,
)
from novel_harness.text import chapterize

BOOK: Path = Path(__file__).parent / "fixtures" / "demo_novel.txt"


@pytest.fixture
def conn() -> Iterator[Connection]:
    c = connect(IN_MEMORY)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def store(conn: Connection) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


@pytest.fixture
def pid(conn: Connection) -> str:
    return project.create(conn, name="青云记", root_path=".").id


def _chapter_rows(store: SqliteStoryGraph, pid: str) -> list[tuple[int, str, str]]:
    """(number, chapter_id, snapshot_id)，按章序。走 `current_snapshots` —— 不裸 SQL。"""
    return [(ct.number, ct.chapter_id, ct.snapshot_id) for ct in store.current_snapshots(pid)]


# ══════════════════════════════════════════════════════════════════════════
# 纯函数
# ══════════════════════════════════════════════════════════════════════════


def test_chapter_path_pads_to_four_digits_and_stays_posix() -> None:
    assert chapter_path(1) == "chapters/0001.md"
    assert chapter_path(88) == "chapters/0088.md"
    # 补零只是为了让 ls 的字典序等于章序；1000 章之后它不再补，路径仍然合法。
    assert chapter_path(1000) == "chapters/1000.md"


def test_chapter_text_does_not_prefix_the_heading_with_a_hash() -> None:
    """`# 第一章` 匹配不上 `CHAPTER_RE`（行首只容得下空白）。这条是 round-trip 的地基。"""
    text = chapter_text("第一章 少年萧决", "正文")
    assert text.startswith("第一章 少年萧决\n\n")
    assert not text.startswith("#")
    assert text.endswith("\n")


def test_chapter_text_with_empty_body_has_one_blank_line_and_round_trips() -> None:
    text = chapter_text("第一章", "")

    assert text == "第一章\n\n"
    assert len(chapterize(text).chapters) == 1


# ══════════════════════════════════════════════════════════════════════════
# import_book：写盘 + 落库
# ══════════════════════════════════════════════════════════════════════════


def test_import_writes_one_file_per_chapter_and_lands_three_chapters(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    report = import_book(store, pid, txt=BOOK, root=tmp_path)

    assert report.chapter_count == 3
    assert report.written == ["chapters/0001.md", "chapters/0002.md", "chapters/0003.md"]
    assert report.unchanged == []
    for rel in report.written:
        assert (tmp_path / rel).is_file()

    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2, 3]
    assert [c.number for c in report.synced.added] == [1, 2, 3]
    assert report.synced.refreshed == []


def test_preamble_is_reported_but_lands_in_no_chapter(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """书名 / 简介 / **第一个卷标题**都在第一个章标之前。丢掉它是静默丢数据。"""
    report = import_book(store, pid, txt=BOOK, root=tmp_path)

    assert report.preamble_chars > 0
    assert "青云记" in BOOK.read_text(encoding="utf-8-sig")
    assert "青云记" not in (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    # 「第一卷 风起青云」不匹配 CHAPTER_RE（卷 不在 [章节回] 里）→ 它在 preamble 里，
    # 不在第一章的 body 里。
    assert "第一卷" not in (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")


def test_first_chapter_file_starts_with_the_raw_heading_then_a_blank_line(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    import_book(store, pid, txt=BOOK, root=tmp_path)

    lines = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8").split("\n")
    assert lines[0] == "第一章 少年萧决"
    assert lines[1] == ""


def test_a_reexport_reads_back_as_exactly_one_chapter(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """round-trip：写出去的每个文件必须能被**那一个**切章器切回恰好一章。

    这条钉住的是 `chapter_text()` 不加 `# ` 前缀。加了它，chapters/ 变成再也读不回来
    的东西，而失败形态是「sync 说每个文件零章」——离原因很远。
    """
    import_book(store, pid, txt=BOOK, root=tmp_path)

    for rel in ("chapters/0001.md", "chapters/0002.md", "chapters/0003.md"):
        book = chapterize((tmp_path / rel).read_text(encoding="utf-8"))
        assert len(book.chapters) == 1, rel
        assert book.preamble == "", rel


def test_the_number_comes_from_text_order_not_from_the_printed_chapter_number(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """fixture 的第三章标题行是「第三章」（无标题），但决定 0003.md 的是 index。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)

    third = (tmp_path / "chapters/0003.md").read_text(encoding="utf-8")
    assert third.startswith("第三章\n\n")
    assert "你身上流的不是萧家的血" in third


def test_reimport_is_idempotent(store: SqliteStoryGraph, pid: str, tmp_path: Path) -> None:
    first = import_book(store, pid, txt=BOOK, root=tmp_path)
    before = _chapter_rows(store, pid)

    second = import_book(store, pid, txt=BOOK, root=tmp_path)

    assert second.written == []
    assert second.unchanged == first.written
    assert second.synced.added == []
    assert second.synced.refreshed == []
    assert second.synced.unchanged_count == 3
    assert _chapter_rows(store, pid) == before


def test_import_refuses_when_a_target_file_differs_and_writes_nothing(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """覆盖作者的稿子是这个项目最不能犯的错。**没有 --force。**

    这也是「中间插一章」那个洞的守卫：插章 → 后续 index 全体 +1 → 0088.md 要被写成
    原来的第 87 章 → 内容不同 → 当场拒绝。放它过去的话没有任何一条 UNIQUE 会炸，
    而图里每一条 valid_from=88 从此指着另一章。
    """
    import_book(store, pid, txt=BOOK, root=tmp_path)
    victim = tmp_path / "chapters/0002.md"
    edited = victim.read_text(encoding="utf-8").replace("顾清音", "顾清吟")
    victim.write_text(edited, encoding="utf-8")
    snapshots_before = [ct.snapshot_id for ct in store.current_snapshots(pid)]

    with pytest.raises(ImportRefused) as excinfo:
        import_book(store, pid, txt=BOOK, root=tmp_path)

    assert excinfo.value.conflicts == ["chapters/0002.md"]
    # 作者改的那个字还在——一个字节都没被写回去。
    assert victim.read_text(encoding="utf-8") == edited
    # 而且 explode 在写任何东西之前就抛了，所以 sync 根本没跑：零新增快照。
    assert [ct.snapshot_id for ct in store.current_snapshots(pid)] == snapshots_before


def test_import_refuses_a_txt_with_zero_chapters(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """零章不是「这本书是空的」，是「切章器没认出这本书的章标写法」。"""
    txt = tmp_path / "无章标.txt"
    txt.write_text("一整本没有任何章标的书。\n就这样。\n", encoding="utf-8")

    with pytest.raises(ImportRefused) as excinfo:
        import_book(store, pid, txt=txt, root=tmp_path)

    assert excinfo.value.conflicts == []
    assert not (tmp_path / "chapters").exists()


def test_prepare_text_rejects_zero_chapters_before_any_write() -> None:
    with pytest.raises(ImportRefused, match="一个章标都没切出来"):
        importer.prepare_text("这是没有章标的正文", source="browser.txt")


def test_import_prepared_writes_and_syncs_without_a_temp_txt(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    text = "第一章 初见\n\n风起。\n"
    book = importer.prepare_text(text, source="browser.txt")

    report = importer.import_prepared(store, pid, book=book, root=tmp_path)

    assert report.chapter_count == 1
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == text
    assert [ct.number for ct in store.current_snapshots(pid)] == [1]


# ══════════════════════════════════════════════════════════════════════════
# sync：日常回路
# ══════════════════════════════════════════════════════════════════════════


def test_sync_picks_up_an_edit_as_a_new_snapshot_and_keeps_the_old_one(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """ADR 0007：chapters/*.md **就是稿子**。作者在 VSCode 里改的字必须能被 declare 定位到。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)
    before = {ct.number: ct.snapshot_id for ct in store.current_snapshots(pid)}
    victim = tmp_path / "chapters/0002.md"
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("顾清音", "顾清吟"), encoding="utf-8"
    )

    report = sync(store, pid, tmp_path)

    assert [c.number for c in report.refreshed] == [2]
    assert report.added == []
    assert report.unchanged_count == 2
    after = {ct.number: ct for ct in store.current_snapshots(pid)}
    # 当前快照换了一条（text_sha256 跟着正文走），另外两章原地不动。
    assert after[2].snapshot_id != before[2]
    assert after[2].snapshot_id == report.refreshed[0].snapshot_id
    assert "顾清吟" in after[2].text
    assert after[1].snapshot_id == before[1]
    assert after[3].snapshot_id == before[3]


def test_sync_adds_a_chapter_the_author_wrote_by_hand(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """「刚写完的第 301 章」这条路：没有 sync，import 就是一次性的。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / "chapters/0004.md").write_text(
        "第四章 玉佩\n\n　　那半块玉佩在雪地里发着微光。\n", encoding="utf-8"
    )

    report = sync(store, pid, tmp_path)

    assert [c.number for c in report.added] == [4]
    assert report.added[0].title == "玉佩"
    assert report.added[0].path == "chapters/0004.md"
    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2, 3, 4]


def test_sync_ignores_files_that_are_not_chapters(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """`chapters/` 是作者的工作区，不是我们的输出目录。他的笔记不是错误。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / "chapters/notes.md").write_text("大纲：第五章让李管家死。\n", encoding="utf-8")

    report = sync(store, pid, tmp_path)

    assert report.ignored_files == ["chapters/notes.md"]
    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2, 3]


def test_sync_refuses_a_file_whose_first_line_is_not_a_heading(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / "chapters/0005.md").write_text("　　忘了写章标就开始写正文。\n", encoding="utf-8")

    with pytest.raises(SyncRefused) as excinfo:
        sync(store, pid, tmp_path)

    assert excinfo.value.path == "chapters/0005.md"


def test_sync_refuses_a_file_with_two_headings_in_it(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """number 来自文件名，一个文件里的第二章会被静默吞掉——那是章序断裂。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / "chapters/0006.md").write_text(
        "第六章 甲\n\n正文甲。\n\n第七章 乙\n\n正文乙。\n", encoding="utf-8"
    )

    with pytest.raises(SyncRefused) as excinfo:
        sync(store, pid, tmp_path)

    assert excinfo.value.path == "chapters/0006.md"


def test_sync_on_an_empty_root_is_not_an_error(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    report = sync(store, pid, tmp_path)

    assert report.added == []
    assert report.ignored_files == []


def test_stored_text_is_the_whole_file_byte_for_byte(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """写进盘的字节、快照存的字节必须是同一份——错开一个字节，每条证据的 para_index 全偏。"""
    import_book(store, pid, txt=BOOK, root=tmp_path)

    for ct in store.current_snapshots(pid):
        on_disk = (tmp_path / chapter_path(ct.number)).read_text(encoding="utf-8")
        assert ct.text == on_disk


# ══════════════════════════════════════════════════════════════════════════
# 守卫
# ══════════════════════════════════════════════════════════════════════════


def test_the_importer_takes_no_conn_and_no_chapter_number() -> None:
    """约束 10：签名里没有一个位置能让作者填章号；也没有 conn（导入不写 decision_log）。"""
    import inspect

    for fn in (importer.import_book, importer.sync, importer.explode):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"conn", "chapter", "valid_from", "since", "at", "ch", "number"}
