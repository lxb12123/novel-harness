"""新书启动：把项目行、初始稿件与快照作为一个有界的原子操作创建。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import importer, project
from .db import Connection
from .graph.sqlite_store import SqliteStoryGraph
from .text import Chapterization, SkippedTocEntry
from .text.language import detect_language

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
    while os.path.lexists(root):
        root = base / f"{slug}-{suffix}"
        suffix += 1
    return root


def create_legacy_root(base: Path, name: str) -> Path:
    """Create a legacy API root without reusing any existing filesystem entry."""
    base.mkdir(parents=True, exist_ok=True)
    return _reserve_book_root(base, name)


def _reserve_book_root(base: Path, name: str) -> Path:
    """Atomically reserve a never-reused final root, retrying suffixes after races."""
    while True:
        candidate = next_book_root(base, name)
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            # Another process won after next_book_root() checked. The directory itself is its
            # reservation, even while empty, so retry and advance to the next stable suffix.
            continue
        return candidate


def _promote_stage(stage: Path, final_root: Path) -> None:
    """Move every staged top-level item into this invocation's reserved final directory."""
    for child in stage.iterdir():
        child.rename(final_root / child.name)
    stage.rmdir()


def _cleanup_owned_paths(*paths: Path | None) -> list[tuple[Path, BaseException]]:
    """Best-effort cleanup of owned paths, retrying each failed removal exactly once.

    Cleanup must remain subordinate to the business failure being handled: even
    ``KeyboardInterrupt`` or ``SystemExit`` from one removal cannot replace it or prevent the
    other owned path from being attempted. ``FileNotFoundError`` means the goal is already met.
    """
    retry: list[Path] = []
    for path in paths:
        if path is None:
            continue
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except BaseException:
            retry.append(path)

    failures: list[tuple[Path, BaseException]] = []
    for path in retry:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except BaseException as error:
            # One bounded retry is enough; cleanup can never replace the original exception.
            failures.append((path, error))
    return failures


def _prepared_book(
    *, mode: BootstrapMode, text: str | None
) -> tuple[Chapterization, list[SkippedTocEntry]]:
    if mode == "import":
        if text is None:
            raise ValueError("import 模式必须提供 text")
        return importer.prepare_text(text, source="浏览器选择的 TXT")
    if mode == "blank":
        if text is not None:
            raise ValueError("blank 模式不接受 text")
        return importer.prepare_text(importer.empty_chapter_text(1), source="新建空白书")
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

    Cleanup is deliberately bounded to paths created exclusively by this invocation: the stage
    returned by ``mkdtemp`` and the final directory won by an exclusive ``mkdir``. A pre-existing
    or concurrently-created candidate is never selected for cleanup.
    """
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("书名不能为空")

    # Preparation precedes even books_root creation, so invalid input has no persistent effect.
    book, skipped_toc = _prepared_book(mode=mode, text=text)
    # 国际化第一批 ②：import 模式此刻手上就有全书正文，不必等 sync 之后再补一次探测。
    # blank 模式没有真正的正文（只有一章空模板），language 留 None → project.insert()
    # 落回 SQL 的 DEFAULT 'zh'，等作者写了字、下一次 sync 再补判定。
    detected_language = (
        detect_language(book.preamble + "\n" + "\n".join(c.body for c in book.chapters))
        if mode == "import"
        else None
    )

    books_root.mkdir(parents=True, exist_ok=True)
    final_root: Path | None = None
    stage: Path | None = None

    try:
        # mkdir(exist_ok=False) is the portable no-clobber primitive. An empty final directory is
        # therefore an ownership reservation, never a reusable candidate for another bootstrap.
        final_root = _reserve_book_root(books_root, cleaned_name)
        stage = Path(tempfile.mkdtemp(prefix=".nh-bootstrap-", dir=books_root))
        store = SqliteStoryGraph(conn)
        with store.transaction():
            created = project.insert(
                conn, name=cleaned_name, root_path=str(final_root), language=detected_language
            )
            if mode == "import":
                report = importer.import_prepared(
                    store, created.id, book=book, skipped_toc=skipped_toc, root=stage
                )
            else:
                first = stage / importer.chapter_path(1)
                first.parent.mkdir(parents=True, exist_ok=True)
                first.write_text(importer.empty_chapter_text(1), encoding="utf-8")
                importer.sync(store, created.id, stage)
                report = None
            result = BootstrapResult(
                project=created,
                import_report=report,
            )
            _promote_stage(stage, final_root)
        return result
    except BaseException as original:
        # _transaction catches failures inside its body, but commit itself happens after that
        # handler. Roll back here as well to cover a commit() exception with an open transaction.
        try:
            if conn.in_transaction:
                conn.rollback()
        except BaseException:
            # Cleanup errors must not replace the business/commit exception promised to callers.
            pass

        # Ownership comes from mkdtemp and atomic mkdir, not a racy stat-before-delete check.
        # Cooperative bootstrap calls never replace these reservations: they see them as occupied
        # and choose a suffix. Arbitrary hostile filesystem mutation is outside this local-service
        # boundary; within it, neither pathname can belong to a competitor.
        for path, error in _cleanup_owned_paths(stage, final_root):
            original.add_note(
                f"bootstrap cleanup may have leaked {path}: {type(error).__name__}: {error}"
            )
        raise
