"""`uvx novel-harness` 的入口。

v1 的分发叙事（ADR 0007）：一条命令，不装 Docker。**那条命令是 `nh serve`**。

2026-08-02 决定（[ADR 0012](../docs/adr/0012-book-owns-its-db.md)）：**库跟着书走，
没有全局默认位置。** 一本小说 = 一个文件夹，`book.db` 住在那个文件夹里（跟稿子一起
搬家，路径永不写进库）。本入口不带参数时：

1. 从当前目录**向上**找最近的 `book.db`——找到就开那本书（等于 `nh serve --db <它>`）；
2. 找不到 → 作者不在书文件夹里，给一句人话 + 两条出路。

带参数时原样交给 `nh`（`--help` / `serve` / `import` …照常可用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__
from .cli import app


def _find_book_db(start: str | Path) -> Path | None:
    """从 ``start`` 向上找最近的 ``book.db``。

    **只向上、不向下**：A 书的库只会被「在 A 文件夹里启动」命中，B 书在隔壁
    永远不会被串到（多本书物理隔离，见 ADR 0012）。找到文件系统根为止。
    """
    directory = Path(start)
    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / "book.db"
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        print(__version__)
        return
    if argv:
        # 带子命令/旗标 → 原样交给 nh（serve / import / check / --help…）。
        app(argv)
        return

    book_db = _find_book_db(Path.cwd())
    if book_db is not None:
        app(["serve", "--db", str(book_db)])
        return

    print(
        "Novel Harness：没找到书。\n"
        "一本小说 = 一个文件夹，book.db 住在那个文件夹里。\n"
        "  ① 已经有一本书：cd 进它的文件夹，再运行 novel-harness\n"
        "  ② 还没有书：先建一个空库，再导入稿子——\n"
        "     novel-harness serve --db /你的书文件夹/book.db\n"
        "（命令行全貌见 `novel-harness --help`。）"
    )


if __name__ == "__main__":
    main()
