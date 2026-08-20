"""迁移之前先给有数据的库拷一份备份（`db.backup_before_migration`）。

── 这条为什么存在 ────────────────────────────────────────────────────────
「写迁移之前先把 dev server 停掉」这条**规矩失败过两次**：005 和 008 两次都真的把
作者那本 722 章的书自动升了级，没有备份、没有确认（两次都无害，但那是运气）。
第三次不该再靠记性。它同时是给真作者的保险——一次坏迁移落在那本书上不可恢复，
而作者不会手动备份。

── 这份文件里最值钱的一条 ──────────────────────────────────────────────
`test_the_backup_carries_what_is_still_only_in_the_wal`：库是 WAL 模式，
**只拷主文件会得到一份看起来一切正常、其实少了最近全部改动的备份**。
所以它先证明那个陷阱是真的（朴素拷贝真的丢），再证明我们没踩进去。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import date
from importlib.resources import files
from pathlib import Path

import pytest

from novel_harness.db import (
    BACKUP_LABEL,
    IN_MEMORY,
    MigrationError,
    backup_before_migration,
    backup_path,
    connect,
    migrate,
    user_version,
)

OLD_VERSION = 7
"""从第几版升上来。7 是 `007_draft_candidate.sql`——挑一个真存在的中间版本，
这样跑的是真的 `ALTER TABLE`，不是一个编出来的迁移。"""


def _book_at(tmp_path: Path, version: int = OLD_VERSION, name: str = "book.db"):
    """一个停在第 `version` 版、里面已经有东西的库。返回 `(路径, 打开着的连接)`。"""
    path = tmp_path / name
    conn = connect(path)
    root = files("novel_harness") / "migrations"
    for entry in sorted(e.name for e in root.iterdir() if e.name.endswith(".sql")):
        if int(entry[:3]) > version:
            continue
        conn.executescript((root / entry).read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES ('project:old', '青云记', '/x')"
    )
    conn.commit()
    assert user_version(conn) == version
    return path, conn


def _backups(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if BACKUP_LABEL in p.name)


def _books_inside(path: Path) -> set[str]:
    """备份里那本书的名字。**打不开或没有那张表都算「丢了」**——朴素拷贝的两种长相。"""
    try:
        conn = sqlite3.connect(path)
        try:
            return {str(row[0]) for row in conn.execute("SELECT name FROM project")}
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


# ══════════════════════════════════════════════════════════════════════════
# 一、什么时候拷
# ══════════════════════════════════════════════════════════════════════════


def test_an_upgrade_copies_the_book_before_touching_it(tmp_path: Path) -> None:
    path, conn = _book_at(tmp_path)
    try:
        assert migrate(conn) > OLD_VERSION
    finally:
        conn.close()

    backups = _backups(tmp_path)
    assert len(backups) == 1
    backup = backups[0]
    # 备份停在**升级之前**那一版：它是回退用的，不是一份新库的副本。
    frozen = connect(backup)
    try:
        assert user_version(frozen) == OLD_VERSION
        assert "chapter_number" not in {
            row[1] for row in frozen.execute("PRAGMA table_info(model_call)")
        }, "备份里出现了升级之后才有的列 —— 那说明它是升级之后拷的"
    finally:
        frozen.close()
    assert _books_inside(backup) == {"青云记"}


def test_a_database_already_at_the_latest_version_is_never_copied(tmp_path: Path) -> None:
    """**日常那千百次重启一份都不拷**（含开着 `--reload` 的 dev server）。

    拷的条件是「这一次真的会跑 DDL」。不然作者的文件夹里每天会长出十几个 12MB 的文件。
    """
    path, conn = _book_at(tmp_path)
    try:
        migrate(conn)
        first = _backups(tmp_path)
        for _ in range(5):
            migrate(conn)
        assert _backups(tmp_path) == first
    finally:
        conn.close()


def test_a_brand_new_empty_database_is_not_copied(tmp_path: Path) -> None:
    """空库（`user_version == 0`）没有任何东西可丢。启动器首次建库走这条。"""
    conn = connect(tmp_path / "new.db")
    try:
        migrate(conn)
    finally:
        conn.close()
    assert _backups(tmp_path) == []


def test_an_in_memory_database_has_nothing_to_copy(tmp_path: Path) -> None:
    """内存库没有文件，也没处放备份。**不能因此炸掉**——半个测试套件跑在内存库上。"""
    conn = connect(IN_MEMORY)
    try:
        assert backup_before_migration(conn, from_version=1) is None
        assert migrate(conn) > OLD_VERSION
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 二、怎么拷 —— WAL 那个会静默出错的坑
# ══════════════════════════════════════════════════════════════════════════


def test_the_backup_carries_what_is_still_only_in_the_wal(tmp_path: Path) -> None:
    """**这条是这份文件的理由。**

    库是 WAL 模式（作者目录下真的躺着 `book.db-wal`）。刚提交、还没 checkpoint 的
    那些事务只在 `-wal` 里，`shutil.copyfile` 主文件会得到一份**陈旧甚至打不开**的备份
    ——而它看起来一切正常，等到真要用的那天才发现。

    所以这里先证明陷阱是真的（朴素拷贝真的丢了那本书），再证明走 SQLite 自己的通路
    （`VACUUM INTO`）没丢。
    """
    path, conn = _book_at(tmp_path)
    try:
        conn.execute(
            "INSERT INTO project (id, name, root_path) VALUES ('project:wal', '只在日志里的书', '/y')"
        )
        conn.commit()
        # 没有 checkpoint：这一刻主文件里还没有它。
        assert (tmp_path / f"{path.name}-wal").stat().st_size > 0

        naive = tmp_path / "naive-copy.db"
        shutil.copyfile(path, naive)

        migrate(conn)
    finally:
        conn.close()

    assert "只在日志里的书" not in _books_inside(naive), (
        "朴素拷贝居然没丢东西 —— 这条测试的前提（WAL 里还压着没 checkpoint 的数据）"
        "已经不成立，它现在在验一个空集"
    )
    backup = _backups(tmp_path)[0]
    assert _books_inside(backup) == {"青云记", "只在日志里的书"}


def test_the_final_name_only_ever_appears_after_the_copy_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先写临时名、再原子改名。

    磁盘写满时 `VACUUM INTO` 可能留下一个**半截**文件；它要是直接顶着最终的名字，
    下一次启动会看见「今天这一版已经拷过了」而跳过 —— 一份残缺的备份冒充好的，
    比没有备份更糟。这里量的是**写的时候用的是另一个名字**。
    """
    seen: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst):  # type: ignore[no-untyped-def]
        seen.append((str(src), str(dst)))
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    path, conn = _book_at(tmp_path)
    try:
        migrate(conn)
    finally:
        conn.close()

    backup = _backups(tmp_path)[0]
    renames = [pair for pair in seen if pair[1] == str(backup)]
    assert len(renames) == 1, "备份不是改名进来的 —— 半截文件会顶着最终的名字留下"
    assert renames[0][0] != str(backup)
    assert "拷贝中" in renames[0][0]
    assert not list(tmp_path.glob("*拷贝中*")), "临时文件没清干净"


# ══════════════════════════════════════════════════════════════════════════
# 三、叫什么名字
# ══════════════════════════════════════════════════════════════════════════


def test_the_name_says_what_it_is_and_cannot_be_confused_with_the_live_book() -> None:
    """作者会在自己的文件夹里看见它。名字要自己解释自己，且不会被误当成正在用的那个。"""
    name = backup_path(
        Path("/书/book.db"), from_version=7, today=date(2026, 8, 12)
    ).name
    assert name == "book.db.升级前备份-2026-08-12-第7版.db"
    assert name.startswith("book.db")  # 排序挨着它，一眼看出属于哪本书
    assert name != "book.db"  # 正在用的那个的名字**恰好**是它，删不错
    assert name.endswith(".db")  # 真要回退时，改个名字就能用


def test_two_upgrades_on_the_same_day_are_two_different_files(tmp_path: Path) -> None:
    """一次 schema 升级 = 一份备份，名字里带着「从第几版」。

    所以堆积量是**这些年 schema 改过几次**（迁移文件的条数），不是「开过几次工作台」。
    没有自动清理：删掉的是作者唯一的回退路径，那不该由程序替他决定。
    """
    first = backup_path(Path("/书/book.db"), from_version=7, today=date(2026, 8, 12))
    second = backup_path(Path("/书/book.db"), from_version=8, today=date(2026, 8, 12))
    assert first != second


def test_an_existing_backup_is_never_overwritten(tmp_path: Path) -> None:
    """同一天、同一版上已经拷过了（迁移失败后重启就是这一档）⇒ **保留更早那一份**。

    更早的那份是「出事之前」的，比刚才这一份值钱。
    """
    path, conn = _book_at(tmp_path)
    dest = backup_path(path, from_version=OLD_VERSION)
    dest.write_bytes(b"old-backup")
    try:
        assert backup_before_migration(conn, from_version=OLD_VERSION) == dest
    finally:
        conn.close()
    assert dest.read_bytes() == b"old-backup"
    assert _backups(tmp_path) == [dest]


# ══════════════════════════════════════════════════════════════════════════
# 四、拷不成怎么办：拒绝迁移
# ══════════════════════════════════════════════════════════════════════════


def test_a_backup_that_cannot_be_written_stops_the_upgrade(tmp_path: Path) -> None:
    """拷不成 ⇒ **不迁**。库一个字都没改，版本原地不动。

    方向的理由：正文在磁盘上（ADR 0007），书今天照样写；而库里那些不可重建的东西
    （`decision_log`，§10 约束 7）因此没被动过。反过来「照迁但吵一声」——
    那行黄字双击起服务的作者根本不会读，而这条路上唯一能救命的就是这份备份。
    """
    path, conn = _book_at(tmp_path)
    # 目标位置被一个**目录**占着：`VACUUM INTO` 写得出来，改名一定失败。
    backup_path(path, from_version=OLD_VERSION).mkdir()
    try:
        with pytest.raises(MigrationError) as exc:
            migrate(conn)
        assert user_version(conn) == OLD_VERSION, "拷不成却还是迁了"
        assert "备份" in str(exc.value)
        # 说得出是哪本书、往哪儿拷、以及他能做什么。
        assert str(path.name) in str(exc.value)
        assert "磁盘" in str(exc.value)
    finally:
        conn.close()
    assert not list(tmp_path.glob("*拷贝中*")), "失败之后留下了半截文件"


@pytest.mark.skipif(os.geteuid() == 0, reason="root 无视目录权限")
def test_a_read_only_folder_refuses_the_upgrade_instead_of_migrating_anyway(
    tmp_path: Path,
) -> None:
    """另一种「拷不成」：文件夹不让写。**同一个方向**。"""
    folder = tmp_path / "只读"
    folder.mkdir()
    path, conn = _book_at(folder)
    mode = folder.stat().st_mode
    folder.chmod(0o500)
    try:
        with pytest.raises(MigrationError):
            migrate(conn)
        assert user_version(conn) == OLD_VERSION
    finally:
        folder.chmod(mode)
        conn.close()
    assert _backups(folder) == []


def test_pending_writes_are_committed_into_the_backup_not_lost(tmp_path: Path) -> None:
    """`migrate()` 收到一条**正开着写事务**的连接是既有形状。

    `VACUUM` 不能在事务里跑，所以这里替它提交了——**这不是新增的副作用**：
    下面 `_apply` 的 `executescript` 本来就会隐式 COMMIT 掉外面那个事务。
    要紧的是那些行**必须进备份**：它们随后就被迁移一起提交进库了，
    另开一条连接去拷会得到一份从落地那一刻起就是残的备份。
    """
    path, conn = _book_at(tmp_path)
    try:
        conn.execute(
            "INSERT INTO project (id, name, root_path) VALUES ('project:open', '没提交的书', '/z')"
        )
        assert conn.in_transaction
        migrate(conn)
    finally:
        conn.close()

    assert _books_inside(_backups(tmp_path)[0]) == {"青云记", "没提交的书"}
    assert _books_inside(path) == {"青云记", "没提交的书"}
