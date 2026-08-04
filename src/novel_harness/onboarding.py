"""新书启动：把项目行、初始稿件与快照作为一个有界的原子操作创建。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
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
_RESERVATION_MARKER = ".nh-bootstrap-reserved"
_ALLOCATOR_LOCK = threading.RLock()


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
    """Create a root with the legacy API rule: an existing empty directory is reusable."""
    with _ALLOCATOR_LOCK:
        slug = _book_slug(name)
        root = base / slug
        suffix = 2
        while root.exists() and any(root.iterdir()):
            root = base / f"{slug}-{suffix}"
            suffix += 1
        root.mkdir(parents=True, exist_ok=True)
        return root


def _reserve_book_root(base: Path, name: str) -> Path:
    """Atomically reserve a never-reused final root, retrying suffixes after races."""
    with _ALLOCATOR_LOCK:
        while True:
            candidate = next_book_root(base, name)
            try:
                candidate.mkdir(exist_ok=False)
            except FileExistsError:
                # Another bootstrap won after next_book_root() checked. Its reservation is
                # ownership, so retry and let stable suffix calculation move forward.
                continue
            try:
                (candidate / _RESERVATION_MARKER).write_text("reserved\n", encoding="utf-8")
            except BaseException:
                candidate.rmdir()
                raise
            return candidate


def _promote_stage(stage: Path, final_root: Path) -> None:
    """Move every staged top-level item into this invocation's reserved final directory."""
    for child in stage.iterdir():
        child.rename(final_root / child.name)
    stage.rmdir()
    (final_root / _RESERVATION_MARKER).unlink()


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

    Cleanup is deliberately bounded to paths created exclusively by this invocation: the stage
    returned by ``mkdtemp`` and the final directory won by an exclusive ``mkdir``. A pre-existing
    or concurrently-created candidate is never selected for cleanup.
    """
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("name 不能为空")

    # Preparation precedes even books_root creation, so invalid input has no persistent effect.
    book = _prepared_book(mode=mode, text=text)

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
            created = project.insert(conn, name=cleaned_name, root_path=str(final_root))
            report = importer.import_prepared(store, created.id, book=book, root=stage)
            result = BootstrapResult(
                project=created,
                import_report=report if mode == "import" else None,
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
