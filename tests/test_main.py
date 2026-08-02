"""`novel-harness` 入口：库跟着书走（ADR 0012）。

决定（2026-08-02）：一本小说 = 一个文件夹，`book.db` 住在文件夹里；
入口不带参数时**只向上**找最近的 `book.db`，找不到就给人话而不是猜一个全局位置。
"""

from __future__ import annotations

import sys

from novel_harness import __version__
from novel_harness.__main__ import _find_book_db, main


def test_finds_book_db_in_the_current_directory(tmp_path) -> None:
    db = tmp_path / "book.db"
    db.write_text("x", encoding="utf-8")
    assert _find_book_db(tmp_path) == db


def test_finds_book_db_by_walking_up(tmp_path) -> None:
    book = tmp_path / "我的小说"
    drafts = book / "书稿"
    drafts.mkdir(parents=True)
    db = book / "book.db"
    db.write_text("x", encoding="utf-8")
    assert _find_book_db(drafts) == db


def test_never_crosses_into_a_sibling_book(tmp_path) -> None:
    """多本书隔离：A 的库在 A 文件夹里，从 B 启动不会串到 A。"""
    a = tmp_path / "A"
    a.mkdir()
    (a / "book.db").write_text("x", encoding="utf-8")
    b = tmp_path / "B"
    b.mkdir()
    assert _find_book_db(b) is None


def test_no_book_anywhere_returns_none(tmp_path) -> None:
    assert _find_book_db(tmp_path) is None


def test_main_prints_human_instructions_when_no_book(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(sys, "argv", ["novel-harness"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "novel_harness.__main__._find_book_db", lambda start: None
    )
    monkeypatch.setattr("novel_harness.__main__.app", lambda argv: None)
    main()
    out = capsys.readouterr().out
    assert "没找到书" in out
    assert "cd" in out


def test_main_delegates_to_serve_with_the_found_book(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["novel-harness"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "novel_harness.__main__._find_book_db",
        lambda start: tmp_path / "book.db",
    )
    monkeypatch.setattr("novel_harness.__main__.app", calls.append)
    main()
    assert calls == [["serve", "--db", str(tmp_path / "book.db")]]


def test_subcommand_delegates_verbatim(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["novel-harness", "--help"])
    monkeypatch.setattr("novel_harness.__main__.app", calls.append)
    main()
    assert calls == [["--help"]]


def test_version_flag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["novel-harness", "--version"])
    main()
    assert capsys.readouterr().out.strip() == __version__
