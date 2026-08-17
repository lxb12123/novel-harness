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

import fcntl
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.graph import ChapterInUse, ChapterUsage
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import (
    ChapterChanged,
    ChapterLockTimeout,
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


def _seed_book(store: SqliteStoryGraph, pid: str, tmp_path: Path) -> None:
    import_book(store, pid, txt=BOOK, root=tmp_path)


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


# ══════════════════════════════════════════════════════════════════════════
# 保存回执：写盘前结构预检 / 原子替换 / 章级锁 / snapshot_generation
# ══════════════════════════════════════════════════════════════════════════


def test_validate_chapter_markdown_rejects_bad_structure_and_accepts_good() -> None:
    good = importer.validate_chapter_markdown("第一章 血脉\n\n正文。\n")
    assert good is not None and good.raw_heading == "第一章 血脉"
    with pytest.raises(SyncRefused):
        importer.validate_chapter_markdown(
            "第一章 甲\n\n正文。\n\n第二章 乙\n\n再来一段。\n"
        )
    with pytest.raises(SyncRefused):
        importer.validate_chapter_markdown("卷首没有章标的一整段。\n")


def test_save_structure_failure_leaves_disk_and_db_untouched(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    disk_before = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    db_hash_before = store.current_chapter_hash(pid, 1)

    with pytest.raises(SyncRefused):
        importer.save_chapter(
            store,
            pid,
            tmp_path,
            1,
            "第一章 甲\n\n正文。\n\n第二章 乙\n\n再来一段。\n",
            expected_sha256=db_hash_before,
        )
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == disk_before
    assert store.current_chapter_hash(pid, 1) == db_hash_before


def test_save_new_hash_bumps_generation_and_returns_receipt(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    base = store.current_chapter_hash(pid, 1)
    receipt = importer.save_chapter(
        store, pid, tmp_path, 1, "第一章 血脉\n\n新正文。\n", expected_sha256=base
    )
    assert receipt.changed is True
    assert receipt.saved_to_disk is True
    assert receipt.indexed is True
    assert receipt.snapshot_generation == 2
    assert receipt.snapshot_id is not None
    assert receipt.text_sha256 == importer.text_digest("第一章 血脉\n\n新正文。\n")
    assert store.current_chapter_hash(pid, 1) == receipt.text_sha256


def test_save_same_hash_keeps_generation_and_snapshot(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    base = store.current_chapter_hash(pid, 1)
    same = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    receipt = importer.save_chapter(store, pid, tmp_path, 1, same, expected_sha256=base)
    assert receipt.changed is False
    assert receipt.snapshot_generation == 1
    assert receipt.snapshot_id is not None
    # 同 hash 连续保存：generation 不涨、快照不新增。
    again = importer.save_chapter(store, pid, tmp_path, 1, same, expected_sha256=base)
    assert again.changed is False
    assert again.snapshot_generation == 1
    assert again.snapshot_id == receipt.snapshot_id


def test_restoring_a_historical_snapshot_is_a_change_with_a_new_generation(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    s1_text = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    s1_hash = importer.text_digest(s1_text)
    s1_snapshot = store.current_chapter_hash(pid, 1)
    assert store.current_chapter_generation(pid, 1) == 1

    r2 = importer.save_chapter(
        store, pid, tmp_path, 1, "第一章 血脉\n\n第二版正文。\n", expected_sha256=s1_hash
    )
    assert r2.changed is True
    assert r2.snapshot_generation == 2
    assert store.current_chapter_generation(pid, 1) == 2

    # 从 S2 还原到历史已有的 S1 —— 识别为变化、新 generation（ABA 防护）。
    r3 = importer.save_chapter(store, pid, tmp_path, 1, s1_text, expected_sha256=r2.text_sha256)
    assert r3.changed is True
    assert r3.snapshot_generation == 3
    assert store.current_chapter_generation(pid, 1) == 3
    assert store.current_chapter_hash(pid, 1) == s1_hash
    assert s1_snapshot is not None  # 快照按内容去重，S1 那条被复用


def test_save_expected_hash_conflict_refuses_before_writing(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    disk_before = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    with pytest.raises(ChapterChanged):
        importer.save_chapter(
            store,
            pid,
            tmp_path,
            1,
            "第一章 血脉\n\n不该落盘。\n",
            expected_sha256="a" * 64,
        )
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == disk_before


def test_save_reconciles_stale_db_before_writing_and_409_on_stale_base(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """磁盘被外部改过（WPS）、DB 还没跟上：保存先收编磁盘版，再按新 base 判断。"""
    _seed_book(store, pid, tmp_path)
    old_base = store.current_chapter_hash(pid, 1)
    external = "第一章 血脉\n\n作者在 WPS 里写的新版，还没同步。\n"
    (tmp_path / "chapters/0001.md").write_text(external, encoding="utf-8")
    assert store.current_chapter_hash(pid, 1) == old_base

    # 客户端依据的是旧 base → 先 reconcile 到磁盘版，再发现 base 过期 → 409。
    with pytest.raises(ChapterChanged):
        importer.save_chapter(
            store, pid, tmp_path, 1, "第一章 血脉\n\n新保存。\n", expected_sha256=old_base
        )
    # reconcile 已经把外部版收进库（ADR 0021 的退路），磁盘仍是外部版。
    assert store.current_chapter_hash(pid, 1) == importer.text_digest(external)
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == external


def test_save_with_current_base_after_stale_db_reconciles_then_writes(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    external = "第一章 血脉\n\n外部新版。\n"
    (tmp_path / "chapters/0001.md").write_text(external, encoding="utf-8")
    receipt = importer.save_chapter(
        store, pid, tmp_path, 1, "第一章 血脉\n\n作者保存。\n", expected_sha256=importer.text_digest(external)
    )
    assert receipt.changed is True
    assert receipt.indexed is True
    assert store.current_chapter_hash(pid, 1) == receipt.text_sha256
    # 被覆盖的外部版先进了版本历史。
    assert importer.text_digest(external) in {
        s.text_sha256 for s in store.chapter_snapshots(pid, 1)
    }


def test_directory_fsync_failure_returns_durability_failed(
    store: SqliteStoryGraph, pid: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.replace 成功但目录 fsync 失败：202 形态，不谎报旧文件仍在。"""
    _seed_book(store, pid, tmp_path)
    base = store.current_chapter_hash(pid, 1)
    real_fsync = os.fsync
    calls = 0

    def flaky_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:  # 第一次是临时文件的 fsync，第二次是目录的 fsync
            raise OSError("模拟目录 fsync 失败")
        real_fsync(fd)

    monkeypatch.setattr(importer.os, "fsync", flaky_fsync)
    receipt = importer.save_chapter(
        store, pid, tmp_path, 1, "第一章 血脉\n\n已替换但持久性未确认。\n", expected_sha256=base
    )
    assert receipt.processing == "durability_failed"
    assert receipt.saved_to_disk is True
    assert receipt.indexed is False
    assert "已替换但持久性未确认" in (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")


def test_db_commit_failure_returns_sync_failed_then_reconcile_repairs(
    store: SqliteStoryGraph, pid: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写盘成功但图层提交失败：202 sync_failed，随后 reconcile 把库补回来。"""
    _seed_book(store, pid, tmp_path)
    base = store.current_chapter_hash(pid, 1)
    real_commit = store.commit_chapter_snapshot
    failed = False

    def flaky_commit(spec, *, expected_text_sha256):  # type: ignore[no-untyped-def]
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("模拟图层提交失败")
        return real_commit(spec, expected_text_sha256=expected_text_sha256)

    monkeypatch.setattr(store, "commit_chapter_snapshot", flaky_commit)
    receipt = importer.save_chapter(
        store, pid, tmp_path, 1, "第一章 血脉\n\nDB 第一次没赶上。\n", expected_sha256=base
    )
    assert receipt.processing == "sync_failed"
    assert receipt.saved_to_disk is True
    assert receipt.indexed is False
    # reconcile（save_chapter 内部那次）把库修好了。
    assert store.current_chapter_hash(pid, 1) == receipt.text_sha256


def test_save_lock_timeout_leaves_everything_untouched(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    _seed_book(store, pid, tmp_path)
    disk_before = (tmp_path / "chapters/0001.md").read_text(encoding="utf-8")
    db_before = store.current_chapter_hash(pid, 1)
    lock_dir = tmp_path / ".novel-harness" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"{store.current_chapter_id(pid, 1)}.lock"
    handle = lock_file.open("w")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(ChapterLockTimeout):
            importer.save_chapter(
                store, pid, tmp_path, 1, "第一章 血脉\n\n锁超时。\n", expected_sha256=db_before,
                lock_timeout=0.05,
            )
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == disk_before
    assert store.current_chapter_hash(pid, 1) == db_before


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


# ══════════════════════════════════════════════════════════════════════════
# 新起一章（书架上那颗「＋」）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "一"),
        (2, "二"),
        (9, "九"),
        # 十位打头的两位数不念那个「一」
        (10, "十"),
        (11, "十一"),
        (19, "十九"),
        (20, "二十"),
        (99, "九十九"),
        (100, "一百"),
        # 中间的 0 补**一个**「零」
        (101, "一百零一"),
        # …但 `一百一十` 的那个「一」要念（上面那一刀只切在开头）
        (110, "一百一十"),
        (999, "九百九十九"),
        # 末尾那些 0 不补零
        (1000, "一千"),
        (1001, "一千零一"),
        (1010, "一千零一十"),
        (9999, "九千九百九十九"),
    ],
)
def test_chinese_number(value: int, expected: str) -> None:
    assert importer.chinese_number(value) == expected


def test_every_generated_marker_chapterizes_back_to_one_chapter() -> None:
    """**这是这颗按钮的绊线。**

    章标是切章的锚：`chinese_number` 哪天写出一个 `CHAPTER_RE` 认不出的形状，症状不是
    「章号难看」，是那一章 sync 不进库——而作者在界面上只会看到「保存被拒」。
    """
    from novel_harness.text.chapterize import chapterize

    for number in (1, 2, 10, 11, 100, 101, 110, 999, 1000, 9999):
        text = importer.empty_chapter_text(number)
        cut = chapterize(text)
        # preamble 必须是空的：章标前面多出一个字符，那一章的 para_index 全体偏一位。
        assert len(cut.chapters) == 1, text
        assert cut.preamble == "", text


def test_append_chapter_takes_the_next_number_and_syncs(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)

    number, report = importer.append_chapter(store, pid, tmp_path)

    assert number == 4  # 夹具是 3 章
    assert (tmp_path / importer.chapter_path(4)).read_text(encoding="utf-8") == "第四章\n\n"
    assert [c.number for c in report.added] == [4]
    # 磁盘先、DB 跟：新起的那一章当场就在库里，不用再按一次「读回改动」。
    assert 4 in {c.number for c in store.current_snapshots(pid)}


def test_append_chapter_on_an_empty_book_starts_at_one(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    number, _ = importer.append_chapter(store, pid, tmp_path)

    assert number == 1
    assert (tmp_path / importer.chapter_path(1)).read_text(encoding="utf-8") == "第一章\n\n"


def test_append_chapter_skips_over_a_hole_instead_of_reusing_a_number(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """**取最大号 +1，不是「文件个数 +1」。** 作者自己删掉中间一章之后，
    后者会算出一个已经存在的号，然后撞在下面那条「不覆盖」上——或者更糟，覆盖掉正文。"""
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / importer.chapter_path(2)).unlink()

    number, _ = importer.append_chapter(store, pid, tmp_path)

    assert number == 4


def test_append_chapter_never_overwrites(
    store: SqliteStoryGraph, pid: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个进程同时按「＋」：后到的那次宁可炸，也不许把先到的那一章正文清空。

    那道缝在「列完目录」和「写文件」之间，从外面按不出来——所以这儿把目录**钉成旧的**，
    正是另一个进程抢先建好第 4 章之后、这一次调用手上那份过期清单的样子。
    """
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    (tmp_path / importer.chapter_path(4)).write_text("第四章\n\n别动我。\n", encoding="utf-8")
    stale = importer.chapter_files(tmp_path)[:3]
    monkeypatch.setattr(importer, "chapter_files", lambda root: stale)

    with pytest.raises(FileExistsError):
        importer.append_chapter(store, pid, tmp_path)

    assert "别动我" in (tmp_path / importer.chapter_path(4)).read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# 删掉一章（章目录里那颗「⋯」）
#
# 这一组只管**磁盘那一半**：拒不拒绝是图层的活（`tests/test_canon_writer.py`
# 的 delete_chapter 那一组），这儿钉的是「拒绝了的时候文件有没有被动过」
# 和「删掉的稿子去哪儿了」。
# ══════════════════════════════════════════════════════════════════════════


def test_remove_chapter_moves_the_file_instead_of_deleting_it(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """**那是作者的稿子。** 这颗按钮离「新起一章」只有一列的距离，按错的代价
    不该是「三小时的字没了」——所以它挪进 `deleted/`，而不是 unlink。"""
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    original = (tmp_path / importer.chapter_path(2)).read_text(encoding="utf-8")

    moved = importer.remove_chapter(store, pid, tmp_path, 2)

    assert not (tmp_path / importer.chapter_path(2)).exists()
    assert moved.read_text(encoding="utf-8") == original  # 一个字节都没少
    assert moved.parent == tmp_path / importer.DELETED_DIR
    # 引擎当它不存在：`chapter_files` 只认 chapters/NNNN.md。
    assert [c.number for c in importer.chapter_files(tmp_path)] == [1, 3]
    assert 2 not in {c.number for c in store.current_snapshots(pid)}


def test_remove_chapter_leaves_a_hole_and_does_not_renumber(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """**章号不重排。** 每一条边和每一条情节的 `valid_from` 都钉在章号上，
    重排一次等于把整本书的时态挪位——而那件事没有任何一处会报错。"""
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)

    importer.remove_chapter(store, pid, tmp_path, 2)

    assert [c.number for c in importer.chapter_files(tmp_path)] == [1, 3]
    # 洞留着，下一章接在最大号后面（`append_chapter` 取的就是最大号 +1）。
    assert importer.append_chapter(store, pid, tmp_path)[0] == 4


def test_remove_chapter_does_not_clobber_an_earlier_deletion(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """新建 → 删 → 再新建 → 再删：两次都是 `0004.md`。**第二次不许盖掉第一次。**"""
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    importer.append_chapter(store, pid, tmp_path)
    (tmp_path / importer.chapter_path(4)).write_text("第四章\n\n头一版。\n", encoding="utf-8")
    importer.sync(store, pid, tmp_path)
    first = importer.remove_chapter(store, pid, tmp_path, 4)

    importer.append_chapter(store, pid, tmp_path)
    (tmp_path / importer.chapter_path(4)).write_text("第四章\n\n第二版。\n", encoding="utf-8")
    importer.sync(store, pid, tmp_path)
    second = importer.remove_chapter(store, pid, tmp_path, 4)

    assert first != second
    assert "头一版" in first.read_text(encoding="utf-8")
    assert "第二版" in second.read_text(encoding="utf-8")


def test_remove_chapter_that_is_not_on_disk(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    with pytest.raises(importer.ChapterMissing):
        importer.remove_chapter(store, pid, tmp_path, 9)


def test_remove_chapter_keeps_the_file_when_the_library_refuses(
    store: SqliteStoryGraph, pid: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**先库后盘**，跟 `save_chapter` 的「磁盘先、DB 跟」是反的——因为这一次库那边
    会拒绝。盘先动的话，作者会看见文件没了、然后弹一句「删不掉」，两件事同时成立。
    """

    def refuse(project_id: str, number: int) -> object:
        raise ChapterInUse(
            ChapterUsage(
                chapter_number=number,
                evidence=3,
                edges=0,
                events=0,
                extraction_runs=0,
                proposal_sets=0,
            )
        )

    importer.import_book(store, pid, txt=BOOK, root=tmp_path)
    monkeypatch.setattr(store, "delete_chapter", refuse)

    with pytest.raises(ChapterInUse):
        importer.remove_chapter(store, pid, tmp_path, 2)

    assert (tmp_path / importer.chapter_path(2)).is_file()
    assert not (tmp_path / importer.DELETED_DIR).exists()
