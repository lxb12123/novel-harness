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
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import (
    ChapterChanged,
    ChapterLockTimeout,
    ImportRefused,
    SyncRefused,
    TocSkipBookChanged,
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
    book, skipped_toc = importer.prepare_text(text, source="browser.txt")

    report = importer.import_prepared(
        store, pid, book=book, skipped_toc=skipped_toc, root=tmp_path
    )

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
# 目录页双计（2026-08-27，维护者拍板）：只在导入时过滤，保存/reconcile 不碰
# ══════════════════════════════════════════════════════════════════════════
#
# `drop_toc_duplicates()` 本身的 4 种情形钉在 `tests/test_chapterize.py`
# （目录在开头 / 目录在结尾 / 空占位章保住 / 分卷重启两个都保住）。这一节钉的是
# 那份判据在**导入路径之外**的行为：`sync`/`save_chapter`/`reconcile` 共用的是
# 裸 `chapterize()`，不认识 `drop_toc_duplicates()`——作者手动清空一章，哪怕
# 巧合跟另一章同名同标题，也不许被这一层悄悄丢掉。


def test_sync_does_not_apply_the_toc_filter(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """手写两个文件直接摆上磁盘（不经过 `import_book`）：一个空、一个跟它同名同标题
    非空——`drop_toc_duplicates()` 的判据照抄一遍会把第 1 章判成目录丢掉。
    `sync()` 走的是裸 `chapterize()`，两章都必须原样收进库。
    """
    (tmp_path / "chapters").mkdir()
    (tmp_path / chapter_path(1)).write_text("第一章 山门\n\n", encoding="utf-8")
    (tmp_path / chapter_path(2)).write_text(
        "第一章 山门\n\n萧决拾级而上。\n", encoding="utf-8"
    )

    report = sync(store, pid, tmp_path)

    assert len(report.added) == 2
    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2]
    numbered = {ct.number: ct.text for ct in store.current_snapshots(pid)}
    assert numbered[1] == "第一章 山门\n\n", "空占位章不许被保存/同步路径吞掉"


def test_save_chapter_keeps_a_manually_emptied_chapter_even_with_a_same_titled_twin(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """作者把已经写好的一章清空重来，且巧合跟另一章同名同标题：`save_chapter`
    （Ctrl-S / agent 落稿共用的那一条路）同样不认识 `drop_toc_duplicates()`。
    """
    text = "第一章 山门\n\n萧决拾级而上。\n\n第一章 山门\n\n剑光落下。\n"
    raw = chapterize(text)
    assert len(raw.chapters) == 2, "先确认两章 marker/title 真的完全相同"

    book, skipped_toc = importer.prepare_text(text, source="t.txt")
    assert skipped_toc == [], "两章都非空，导入这一步本来就不该丢任何一章"
    importer.import_prepared(store, pid, book=book, skipped_toc=skipped_toc, root=tmp_path)

    from novel_harness.importer import save_chapter

    receipt = save_chapter(store, pid, tmp_path, 2, "第一章 山门\n\n")

    assert receipt.saved_to_disk and receipt.indexed
    assert (tmp_path / chapter_path(2)).read_text(encoding="utf-8") == "第一章 山门\n\n"
    numbered = {ct.number: ct.text for ct in store.current_snapshots(pid)}
    assert numbered[2] == "第一章 山门\n\n", "手动清空的这一章不许被任何后续动作丢掉"


def test_import_book_reports_which_toc_entries_it_dropped(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> None:
    """`ImportReport.skipped_toc` 是撤销要读的那份数据——位置和标题行都要对。"""
    text = (
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
    src = tmp_path / "src.txt"
    src.write_text(text, encoding="utf-8")
    root = tmp_path / "book"

    report = import_book(store, pid, txt=src, root=root)

    assert report.chapter_count == 2
    assert [(e.position, e.raw_heading) for e in report.skipped_toc] == [
        (1, "第一章 山门"),
        (2, "第二章 落幕"),
    ]


def test_real_gutenberg_book_with_a_toc_page(store: SqliteStoryGraph, pid: str, tmp_path: Path) -> None:
    """*Moby-Dick*（Gutenberg #2701）实测——**书本身不进仓库**（维护者的边界），
    只在本地缓存存在时才跑；一个干净 clone 上这条测试整体跳过，不是假绿。

    报的是真实三个数：`chapterize()` 切出多少（含目录双计）、丢了多少、剩多少。
    已知不完美（`drop_toc_duplicates` docstring 记着）：目录里 3 条标题换了行、
    最后一条吞了 ETYMOLOGY/EXTRACTS 前言，这 4 条正文非空，判据本身不丢它们
    ——切出 270、丢 131、剩 139，不是 270/135/135。这是这份判据换来「零阈值」
    之后已知的代价，不是这条测试要断言的 bug。
    """
    scratch = Path(
        "/private/tmp/claude-501/-Users-lixibin-Desktop-novel-harness/"
        "c1219d65-6371-4fcb-825b-dc607108a45d/scratchpad/moby_dick.txt"
    )
    if not scratch.exists():
        pytest.skip("Moby-Dick 本地缓存不在——它不进仓库，这条测试只在本机验证用")

    src = tmp_path / "moby_dick.txt"
    src.write_text(scratch.read_text(encoding="utf-8"), encoding="utf-8")
    root = tmp_path / "book"

    raw = chapterize(scratch.read_text(encoding="utf-8"))
    report = import_book(store, pid, txt=src, root=root)

    found, dropped, kept = len(raw.chapters), len(report.skipped_toc), report.chapter_count
    assert (found, dropped, kept) == (270, 131, 139)
    assert kept == found - dropped
    assert [ct.number for ct in store.current_snapshots(pid)] == list(range(1, kept + 1))


# ══════════════════════════════════════════════════════════════════════════
# 撤销「目录跳过」（032）
# ══════════════════════════════════════════════════════════════════════════


def _import_with_toc(
    store: SqliteStoryGraph, pid: str, tmp_path: Path
) -> tuple[importer.ImportReport, Path]:
    """目录排在最前面、3 个真章排在后面——撤销要把这 3 个占位章插回最前面。"""
    text = (
        "第一章 山门\n"
        "第二章 落幕\n"
        "第三章 归途\n"
        "\n"
        "第一章 山门\n"
        "\n"
        "萧决拾级而上。\n"
        "\n"
        "第二章 落幕\n"
        "\n"
        "剑光落下。\n"
        "\n"
        "第三章 归途\n"
        "\n"
        "他终于回家了。\n"
    )
    src = tmp_path / "src.txt"
    src.write_text(text, encoding="utf-8")
    root = tmp_path / "book"
    report = import_book(store, pid, txt=src, root=root)
    return report, root


def test_undo_toc_skip_restores_chapters_at_original_positions_in_order(
    store: SqliteStoryGraph, conn: Connection, pid: str, tmp_path: Path
) -> None:
    """撤销一次：3 个目录占位章插回最前面，原来的 3 个真章整体后移 3 位，顺序不变。"""
    report, root = _import_with_toc(store, pid, tmp_path)
    assert report.chapter_count == 3
    assert [c.body for c in chapterize(
        (root / chapter_path(1)).read_text(encoding="utf-8")
    ).chapters] == ["萧决拾级而上。"]
    fingerprint = importer.toc_skip_fingerprint(conn, store, pid)

    restored = importer.undo_toc_skip(
        store, conn, pid, root, skipped=report.skipped_toc, expected_fingerprint=fingerprint
    )

    assert restored == 3
    # `ChapterFile.title` 取的是文件首个非空行（marker+title 那一整行），
    # 不是 `Chapter.title`（regex group(2) 那半）——见 `chapter_files()` 的 docstring。
    files = [(f.number, f.title) for f in importer.chapter_files(root)]
    assert files == [
        (1, "第一章 山门"),
        (2, "第二章 落幕"),
        (3, "第三章 归途"),
        (4, "第一章 山门"),
        (5, "第二章 落幕"),
        (6, "第三章 归途"),
    ]
    assert (root / chapter_path(1)).read_text(encoding="utf-8") == "第一章 山门\n\n", (
        "插回的占位章必须用记下来的原文标题，不是新生成的"
    )
    bodies = {
        ct.number: ct.text for ct in store.current_snapshots(pid)
    }
    assert bodies[1] == "第一章 山门\n\n"
    assert "萧决拾级而上。" in bodies[4]
    assert "他终于回家了。" in bodies[6]
    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2, 3, 4, 5, 6]


def test_undo_toc_skip_refuses_when_the_book_has_changed(
    store: SqliteStoryGraph, conn: Connection, pid: str, tmp_path: Path
) -> None:
    """这本书导入之后被存过一次——撤销必须拒绝，不许在作者脚下换号。"""
    report, root = _import_with_toc(store, pid, tmp_path)
    fingerprint = importer.toc_skip_fingerprint(conn, store, pid)

    from novel_harness.importer import save_chapter

    save_chapter(store, pid, root, 1, "第一章 山门\n\n萧决拾级而上，风很大。\n")

    with pytest.raises(TocSkipBookChanged, match="改过了"):
        importer.undo_toc_skip(
            store, conn, pid, root, skipped=report.skipped_toc, expected_fingerprint=fingerprint
        )
    # 拒绝必须是全有全无：磁盘和库都不许被半途改动。
    assert [ct.number for ct in store.current_snapshots(pid)] == [1, 2, 3]


def test_undo_toc_skip_refuses_after_a_declaration_even_without_a_text_edit(
    store: SqliteStoryGraph, conn: Connection, pid: str, tmp_path: Path
) -> None:
    """正文一个字没改，但作者声明了一件事——canon_version 跳了，撤销照样拒绝。

    这条测试存在的理由：只看章文本哈希会漏掉这条路径（声明引的是已经写在磁盘上的
    原文，不改那句话本身），`toc_skip_fingerprint` 把 `canon_version` 也算进去
    正是为了堵住它。
    """
    report, root = _import_with_toc(store, pid, tmp_path)
    fingerprint = importer.toc_skip_fingerprint(conn, store, pid)

    from novel_harness.declare import Ledger
    from novel_harness.graph import NodeLabel

    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_first_appearance(of="萧决", quote="萧决拾级而上。")
    conn.commit()

    with pytest.raises(TocSkipBookChanged):
        importer.undo_toc_skip(
            store, conn, pid, root, skipped=report.skipped_toc, expected_fingerprint=fingerprint
        )


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


