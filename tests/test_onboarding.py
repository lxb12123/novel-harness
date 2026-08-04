"""新书启动服务：项目行、稿子目录与首批快照必须一起成功或一起消失。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from novel_harness import onboarding, project
from novel_harness.db import Connection, connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import ImportRefused


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    connection = connect(tmp_path / "story.db")
    migrate(connection)
    yield connection
    connection.close()


def _stages(books: Path) -> list[Path]:
    return list(books.glob(".nh-bootstrap-*")) if books.exists() else []


def _project_rows(conn: Any) -> list[project.Project]:
    return project.list_all(conn)


def _snapshot_numbers(conn: Any, project_id: str) -> list[int]:
    return [item.number for item in SqliteStoryGraph(conn).current_snapshots(project_id)]


def test_import_bootstraps_two_chapters_and_snapshots(
    conn: Connection, tmp_path: Path
) -> None:
    books = tmp_path / "books"

    result = onboarding.bootstrap_project(
        conn,
        books_root=books,
        mode="import",
        name="青云记",
        text="第一章 初见\n\n风起。\n\n第二章 重逢\n\n云归。\n",
    )

    assert result.project.name == "青云记"
    assert result.initial_chapter == 1
    assert result.import_report is not None
    assert result.import_report.chapter_count == 2
    root = Path(result.project.root_path)
    assert (root / "chapters/0001.md").is_file()
    assert (root / "chapters/0002.md").is_file()
    assert _snapshot_numbers(conn, result.project.id) == [1, 2]
    assert _stages(books) == []


def test_blank_bootstraps_exact_first_chapter_and_snapshot(
    conn: Connection, tmp_path: Path
) -> None:
    result = onboarding.bootstrap_project(
        conn,
        books_root=tmp_path / "books",
        mode="blank",
        name="未命名故事",
        text=None,
    )

    assert result.initial_chapter == 1
    assert result.import_report is None
    root = Path(result.project.root_path)
    assert (root / "chapters/0001.md").read_text(encoding="utf-8") == "第一章\n\n"
    assert _snapshot_numbers(conn, result.project.id) == [1]


def test_zero_chapter_import_leaves_no_project_or_directory(
    conn: Connection, tmp_path: Path
) -> None:
    books = tmp_path / "books"
    final = books / "没有章标"

    with pytest.raises(ImportRefused):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="没有章标",
            text="只有正文，没有任何章标。",
        )

    assert _project_rows(conn) == []
    assert not final.exists()
    assert _stages(books) == []


def test_chapter_write_failure_rolls_back_database_and_cleans_owned_paths(
    conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = tmp_path / "books"
    original_write_text = Path.write_text

    def fail_first_chapter(path: Path, data: str, **kwargs: Any) -> int:
        if path.name == "0001.md":
            raise OSError("disk full")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_first_chapter)

    with pytest.raises(OSError, match="disk full"):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="写盘失败",
            text="第一章\n\n正文。\n",
        )

    assert _project_rows(conn) == []
    assert not (books / "写盘失败").exists()
    assert _stages(books) == []


def test_sync_failure_after_explode_rolls_back_and_cleans_owned_paths(
    conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = tmp_path / "books"

    def fail_sync(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("sync exploded")

    monkeypatch.setattr(onboarding.importer, "sync", fail_sync)

    with pytest.raises(RuntimeError, match="sync exploded"):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="同步失败",
            text="第一章\n\n已经写到 stage。\n",
        )

    assert _project_rows(conn) == []
    assert not (books / "同步失败").exists()
    assert _stages(books) == []


class _CommitFailureConnection:
    """在 SQLite 真 commit 之前抛错，其余行为委托给真连接。"""

    def __init__(self, wrapped: Connection) -> None:
        self._wrapped = wrapped

    @property
    def in_transaction(self) -> bool:
        return self._wrapped.in_transaction

    def commit(self) -> None:
        raise RuntimeError("commit failed")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def test_commit_failure_rolls_back_and_removes_promoted_directory(
    conn: Connection, tmp_path: Path
) -> None:
    books = tmp_path / "books"
    proxy = _CommitFailureConnection(conn)

    with pytest.raises(RuntimeError, match="commit failed"):
        onboarding.bootstrap_project(
            proxy,  # type: ignore[arg-type]
            books_root=books,
            mode="blank",
            name="提交失败",
            text=None,
        )

    assert not conn.in_transaction
    assert _project_rows(conn) == []
    assert not (books / "提交失败").exists()
    assert _stages(books) == []


def test_existing_book_directory_is_never_overwritten(conn: Connection, tmp_path: Path) -> None:
    books = tmp_path / "books"
    occupied = books / "同名"
    occupied.mkdir(parents=True)
    marker = occupied / "作者原稿.md"
    marker.write_text("不可覆盖", encoding="utf-8")

    result = onboarding.bootstrap_project(
        conn,
        books_root=books,
        mode="blank",
        name="同名",
        text=None,
    )

    assert marker.read_text(encoding="utf-8") == "不可覆盖"
    assert Path(result.project.root_path) == books / "同名-2"
    assert (books / "同名-2/chapters/0001.md").is_file()


def test_concurrent_empty_candidate_is_preserved_and_bootstrap_uses_next_suffix(
    conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = tmp_path / "books"
    original_next = onboarding.next_book_root
    raced: dict[str, int] = {}

    def create_competitor_before_reservation(base: Path, name: str) -> Path:
        candidate = original_next(base, name)
        if not raced:
            candidate.mkdir()
            raced["inode"] = candidate.stat().st_ino
        return candidate

    monkeypatch.setattr(onboarding, "next_book_root", create_competitor_before_reservation)

    result = onboarding.bootstrap_project(
        conn,
        books_root=books,
        mode="blank",
        name="并发同名",
        text=None,
    )

    competitor = books / "并发同名"
    assert competitor.is_dir()
    assert competitor.stat().st_ino == raced["inode"]
    assert list(competitor.iterdir()) == []
    assert Path(result.project.root_path) == books / "并发同名-2"
    assert (books / "并发同名-2/chapters/0001.md").is_file()


def test_failure_cleanup_uses_owned_reservations_without_stat_then_delete(
    conn: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = tmp_path / "books"
    competitor = books / "清理竞态"
    competitor.mkdir(parents=True)
    competitor_inode = competitor.stat().st_ino
    cleanup_started = False
    original_stat = Path.stat

    def fail_sync(*args: Any, **kwargs: Any) -> None:
        nonlocal cleanup_started
        cleanup_started = True
        raise RuntimeError("sync failed before promotion")

    def forbid_cleanup_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if cleanup_started and path.name.startswith(".nh-bootstrap-"):
            raise AssertionError("cleanup must not authorize rmtree with a prior Path.stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(onboarding.importer, "sync", fail_sync)
    monkeypatch.setattr(Path, "stat", forbid_cleanup_stat)

    with pytest.raises(RuntimeError, match="sync failed before promotion"):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="清理竞态",
            text="第一章\n\n正文。\n",
        )

    assert competitor.stat().st_ino == competitor_inode
    assert list(competitor.iterdir()) == []
    assert not (books / "清理竞态-2").exists()
    assert _stages(books) == []


def test_next_book_root_sanitizes_and_never_reuses_existing_paths(tmp_path: Path) -> None:
    books = tmp_path / "books"
    books.mkdir()
    (books / "坏书名").mkdir()
    (books / "坏书名-2").write_text("occupied", encoding="utf-8")

    assert onboarding.next_book_root(books, ' 坏/书:*?"名<>| ') == books / "坏书名-3"
    assert onboarding.next_book_root(books, "////") == books / "book"


def test_create_legacy_root_reuses_only_an_existing_empty_directory(tmp_path: Path) -> None:
    books = tmp_path / "books"
    empty = books / "旧接口"
    empty.mkdir(parents=True)

    assert onboarding.create_legacy_root(books, "旧接口") == empty

    (empty / "作者原稿.md").write_text("保留", encoding="utf-8")
    created = onboarding.create_legacy_root(books, "旧接口")
    assert created == books / "旧接口-2"
    assert created.is_dir()
    assert (empty / "作者原稿.md").read_text(encoding="utf-8") == "保留"


@pytest.mark.parametrize(
    ("mode", "text"),
    [("import", None), ("blank", "unexpected"), ("mystery", None)],
)
def test_invalid_mode_or_text_contract_writes_nothing(
    conn: Connection, tmp_path: Path, mode: str, text: str | None
) -> None:
    books = tmp_path / "books"

    with pytest.raises(ValueError):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode=mode,  # type: ignore[arg-type]
            name="参数错误",
            text=text,
        )

    assert _project_rows(conn) == []
    assert not books.exists()


def test_blank_project_rejects_whitespace_name_before_any_write(
    conn: Connection, tmp_path: Path
) -> None:
    books = tmp_path / "books"

    with pytest.raises(ValueError):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="blank",
            name=" \t ",
            text=None,
        )

    assert _project_rows(conn) == []
    assert not books.exists()
