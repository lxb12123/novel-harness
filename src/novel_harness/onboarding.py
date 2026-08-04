"""新书启动：把项目行、初始稿件与快照作为一个有界的原子操作创建。"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import importer, project
from .db import Connection
from .graph.sqlite_store import SqliteStoryGraph
from .text import Chapterization

BootstrapMode = Literal["import", "blank"]


class BootstrapResult(BaseModel):
    """新书启动后可交给界面的最小结果。"""

    model_config = ConfigDict(frozen=True)

    project: project.Project
    initial_chapter: int = 1
    import_report: importer.ImportReport | None = None


_UNSAFE_PATH = re.compile(r'[/\\:*?"<>|]')


def _book_slug(name: str) -> str:
    return _UNSAFE_PATH.sub("", name).strip() or "book"


def next_book_root(base: Path, name: str) -> Path:
    """Return a never-reused root candidate, adding stable numeric suffixes on collision."""
    slug = _book_slug(name)
    root = base / slug
    suffix = 2
    while root.exists():
        root = base / f"{slug}-{suffix}"
        suffix += 1
    return root


def create_legacy_root(base: Path, name: str) -> Path:
    """Create a root with the legacy API rule: an existing empty directory is reusable."""
    slug = _book_slug(name)
    root = base / slug
    suffix = 2
    while root.exists() and any(root.iterdir()):
        root = base / f"{slug}-{suffix}"
        suffix += 1
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prepared_book(*, mode: BootstrapMode, text: str | None) -> Chapterization:
    if mode == "import":
        if text is None:
            raise ValueError("import 模式必须提供 text")
        return importer.prepare_text(text, source="浏览器选择的 TXT")
    if mode == "blank":
        if text is not None:
            raise ValueError("blank 模式不接受 text")
        return importer.prepare_text("第一章\n\n", source="新建空白书")
    raise ValueError(f"未知的新书启动模式：{mode}")


def bootstrap_project(
    conn: Connection,
    *,
    books_root: Path,
    mode: BootstrapMode,
    name: str,
    text: str | None,
) -> BootstrapResult:
    """Atomically create one project, its manuscript directory, chapters, and snapshots.

    Cleanup is deliberately bounded to paths created exclusively by this invocation: its
    random stage directory, or the final directory only after that exact stage was promoted.
    A pre-existing or concurrently-created candidate is never selected for cleanup.
    """
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("name 不能为空")

    # Preparation precedes even books_root creation, so invalid input has no persistent effect.
    book = _prepared_book(mode=mode, text=text)

    books_root.mkdir(parents=True, exist_ok=True)
    final_root = next_book_root(books_root, cleaned_name)
    stage = Path(tempfile.mkdtemp(prefix=".nh-bootstrap-", dir=books_root))
    stage_stat = stage.stat()
    stage_identity = (stage_stat.st_dev, stage_stat.st_ino)
    promoted = False
    store = SqliteStoryGraph(conn)

    try:
        with store.transaction():
            created = project.insert(conn, name=cleaned_name, root_path=str(final_root))
            report = importer.import_prepared(store, created.id, book=book, root=stage)
            if mode == "blank":
                # importer.chapter_text preserves a terminal body newline; an empty body would
                # otherwise serialize as three newlines. Normalize the blank seed, then sync that
                # exact manuscript byte sequence so the current snapshot still mirrors the disk.
                first = stage / "chapters/0001.md"
                first.write_text("第一章\n\n", encoding="utf-8")
                importer.sync(store, created.id, stage)
            result = BootstrapResult(
                project=created,
                import_report=report if mode == "import" else None,
            )
            stage.rename(final_root)
            promoted = True
        return result
    except BaseException:
        # _transaction catches failures inside its body, but commit itself happens after that
        # handler. Roll back here as well to cover a commit() exception with an open transaction.
        try:
            if conn.in_transaction:
                conn.rollback()
        except BaseException:
            # Cleanup errors must not replace the business/commit exception promised to callers.
            pass

        # Identity closes the tiny async-exception window between rename() returning and setting
        # promoted=True, while keeping a racing/pre-existing final directory outside our boundary.
        candidate = final_root if promoted or not stage.exists() else stage
        try:
            candidate_stat = candidate.stat()
            if (candidate_stat.st_dev, candidate_stat.st_ino) == stage_identity:
                shutil.rmtree(candidate)
        except FileNotFoundError:
            pass
        except BaseException:
            # Preserve the original exception even if best-effort filesystem cleanup itself fails.
            pass
        raise
