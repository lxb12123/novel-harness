# Polished Onboarding Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-default “开始一本书” form with the approved A+A1 desktop onboarding, while making TXT import and blank-book creation atomic and real on disk.

**Architecture:** Keep `Setup` as the public React entry but split its local flow into focused onboarding components. Add one discriminated `/api/projects/bootstrap` endpoint backed by a new domain service that stages files, uses one outer SQLite transaction, and removes only its own artifacts on failure. Existing create/import routes remain compatible; request-scoped API connections explicitly allow FastAPI worker-thread handoff.

**Tech Stack:** Python 3.12, SQLite, FastAPI/Pydantic, React 18, TypeScript, TanStack Query, Zustand, Vitest/Testing Library, hand-written responsive CSS.

---

## File map

**Create**

- `src/novel_harness/onboarding.py` — book-root allocation and atomic import/blank bootstrap service.
- `tests/test_onboarding.py` — service-level filesystem/database rollback tests.
- `tests/test_onboarding_api.py` — HTTP contract for the discriminated bootstrap endpoint.
- `frontend/src/components/onboarding/OnboardingShell.tsx` — full-screen approved A layout.
- `frontend/src/components/onboarding/StartChooser.tsx` — two launch actions.
- `frontend/src/components/onboarding/ImportReview.tsx` — selected-file confirmation flow.
- `frontend/src/components/onboarding/BlankBookForm.tsx` — blank-book naming flow.
- `frontend/src/components/Setup.test.tsx` — onboarding component behavior and accessibility tests.
- `frontend/src/api/client.test.ts` — UTF-8/GBK file decoding tests.

**Modify**

- `src/novel_harness/db.py` — opt-in SQLite cross-thread connection parameter.
- `src/novel_harness/api/deps.py` — request connections use the cross-thread-safe setting.
- `src/novel_harness/project.py` — non-committing project insert owned by the project module.
- `src/novel_harness/importer.py` — validated in-memory text preparation and prepared import.
- `src/novel_harness/api/app.py` — thin bootstrap route and reuse centralized root allocation.
- `tests/test_api.py` — deterministic request-connection thread-handoff regression.
- `tests/test_project.py` — transaction-owned project insertion.
- `tests/test_importer.py` — prepared text import behavior.
- `tests/test_frontend_contract.py` — freeze the real bootstrap response.
- `frontend/src/__fixtures__/api.json` — regenerated real API fixture.
- `frontend/src/api/types.ts` — bootstrap request/result types.
- `frontend/src/api/hooks.ts` — bootstrap mutation and precise cache invalidation.
- `frontend/src/test/harness.tsx` — bootstrap fixture handler.
- `frontend/src/components/Setup.tsx` — local onboarding state coordinator.
- `frontend/src/styles.css` — approved A+A1 visual system, responsive and dark variants.
- `docs/ARCHITECTURE.md` — current route/fixture/source/test counts only after verification.

No dependency or schema migration is required.

---

### Task 1: Make request-scoped SQLite connections safe across FastAPI worker threads

**Files:**

- Modify: `src/novel_harness/db.py:71-95`
- Modify: `src/novel_harness/api/deps.py:62-68`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the deterministic failing thread-handoff test**

Add imports and this test to `tests/test_api.py`:

```python
from concurrent.futures import ThreadPoolExecutor

from novel_harness.api.deps import get_conn


def test_request_connection_survives_fastapi_worker_thread_handoff(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NH_DB", book["db"])
    dependency = get_conn()
    conn = next(dependency)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(
                lambda: conn.execute("SELECT name FROM project WHERE id = ?", (book["pid"],))
                .fetchone()[0]
            ).result()
        assert result == "青云记"
    finally:
        dependency.close()


def test_parallel_http_requests_keep_connections_request_local(
    client: TestClient, book: dict[str, str]
) -> None:
    path = f"/api/projects/{book['pid']}/chapters"

    def fetch(_: int) -> int:
        return client.get(path).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(fetch, range(20)))

    assert statuses == [200] * 20
```

- [ ] **Step 2: Run the test and confirm the current failure**

Run:

```bash
uv run pytest -q tests/test_api.py::test_request_connection_survives_fastapi_worker_thread_handoff tests/test_api.py::test_parallel_http_requests_keep_connections_request_local
```

Expected: the deterministic handoff test fails with `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`; the HTTP test also exercises 20 real concurrent requests.

- [ ] **Step 3: Add an explicit connection option and use it only for HTTP requests**

Change `db.connect` to preserve the safe default outside the API:

```python
def connect(path: str | Path, *, check_same_thread: bool = True) -> Connection:
    target = str(path)
    if target != IN_MEMORY:
        Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)
        target = str(Path(target).expanduser())

    conn = sqlite3.connect(target, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.create_function("nh_json_canonical", 1, canonical_json_text, deterministic=True)
    conn.create_function("nh_sha256_text", 1, _sha256_text, deterministic=True)
    conn.create_function("nh_utf8_text", 1, _utf8_text, deterministic=True)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if target != IN_MEMORY:
        _enable_wal(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

Change `get_conn` only:

```python
def get_conn() -> Iterator[Connection]:
    """一请求一连接；FastAPI 可在线程池中的不同线程执行依赖、路由和清理。"""
    conn = connect(_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
```

Do not change `ensure_schema()` or background extraction connections.

- [ ] **Step 4: Run focused API tests**

Run:

```bash
uv run pytest -q tests/test_api.py::test_request_connection_survives_fastapi_worker_thread_handoff tests/test_api.py::test_parallel_http_requests_keep_connections_request_local tests/test_api.py::test_matrix_never_leaks_secret_props tests/test_api.py::test_roster_and_chapters
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/novel_harness/db.py src/novel_harness/api/deps.py tests/test_api.py
git commit -m "fix: allow request sqlite thread handoff"
```

---

### Task 2: Expose transaction-owned project insertion and prepared text import

**Files:**

- Modify: `src/novel_harness/project.py:58-82`
- Modify: `src/novel_harness/importer.py:230-280`
- Test: `tests/test_project.py`
- Test: `tests/test_importer.py`

- [ ] **Step 1: Write failing tests for caller-owned project transactions**

Add to `tests/test_project.py`:

```python
def test_insert_leaves_commit_to_the_caller(tmp_path: Path) -> None:
    path = tmp_path / "book.db"
    conn = connect(path)
    migrate(conn)
    conn.execute("BEGIN IMMEDIATE")
    inserted = project.insert(conn, name="未提交", root_path="books/未提交")
    assert project.get(conn, inserted.id) is not None
    conn.rollback()

    verify = connect(path)
    try:
        assert project.get(verify, inserted.id) is None
    finally:
        verify.close()
        conn.close()


def test_create_keeps_its_existing_commit_semantics(tmp_path: Path) -> None:
    path = tmp_path / "book.db"
    conn = connect(path)
    migrate(conn)
    created = project.create(conn, name="已提交", root_path="books/已提交")
    conn.close()

    verify = connect(path)
    try:
        assert project.get(verify, created.id) is not None
    finally:
        verify.close()
```

- [ ] **Step 2: Write failing prepared-import tests**

Add to `tests/test_importer.py`:

```python
def test_prepare_text_rejects_zero_chapters_before_any_write() -> None:
    with pytest.raises(ImportRefused, match="一个章标都没切出来"):
        prepare_text("这是没有章标的正文", source="browser.txt")


def test_import_prepared_writes_and_syncs_without_a_temp_txt(tmp_path: Path) -> None:
    conn = connect(IN_MEMORY)
    migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(tmp_path)).id
    store = SqliteStoryGraph(conn)
    prepared = prepare_text("第一章 初见\n\n风起。\n", source="browser.txt")

    report = import_prepared(store, pid, book=prepared, root=tmp_path)

    assert report.chapter_count == 1
    assert (tmp_path / "chapters/0001.md").read_text(encoding="utf-8") == "第一章 初见\n\n风起。\n"
    assert [row.number for row in store.current_snapshots(pid)] == [1]
```

Import `prepare_text` and `import_prepared` from `novel_harness.importer` and use the repository’s existing fixture/import style.

- [ ] **Step 3: Run the four tests and confirm missing APIs**

Run:

```bash
uv run pytest -q tests/test_project.py::test_insert_leaves_commit_to_the_caller tests/test_project.py::test_create_keeps_its_existing_commit_semantics tests/test_importer.py::test_prepare_text_rejects_zero_chapters_before_any_write tests/test_importer.py::test_import_prepared_writes_and_syncs_without_a_temp_txt
```

Expected: FAIL because `project.insert`, `prepare_text`, and `import_prepared` do not exist.

- [ ] **Step 4: Refactor project creation without changing its public behavior**

Implement in `project.py`:

```python
def insert(conn: Connection, *, name: str, root_path: str) -> Project:
    """Insert one project row; the caller owns commit/rollback."""
    if not name:
        raise ValueError("name 不能为空：它是作者在 nh init 之后唯一认得出这个库的东西")
    if not root_path:
        raise ValueError("root_path 不能为空：正文在磁盘上（ADR 0007），没有它就没有 chapters/")
    row = conn.execute(
        """
        INSERT INTO project (id, name, root_path)
        VALUES (?, ?, ?)
        RETURNING id, name, root_path, canon_version
        """,
        (new_project_id(), name, root_path),
    ).fetchone()
    return _row_to_project(row)


def create(conn: Connection, *, name: str, root_path: str) -> Project:
    project = insert(conn, name=name, root_path=root_path)
    conn.commit()
    return project
```

- [ ] **Step 5: Split importer validation from persistence**

Implement in `importer.py`:

```python
def prepare_text(text: str, *, source: str) -> Chapterization:
    book = chapterize(text.removeprefix("\ufeff"))
    if not book.chapters:
        raise ImportRefused(
            f"{source} 里一个章标都没切出来（认的是行首的「第N章/节/回」）。\n"
            "  零章不是「这本书是空的」，是「切章器没认出这本书的章标写法」。"
        )
    return book


def import_prepared(
    store: GraphStore,
    project_id: str,
    *,
    book: Chapterization,
    root: Path,
) -> ImportReport:
    written, unchanged = explode(book, root)
    synced = sync(store, project_id, root)
    return ImportReport(
        chapter_count=len(book.chapters),
        preamble_chars=len(book.preamble.strip()),
        written=written,
        unchanged=unchanged,
        synced=synced,
    )


def import_text(
    store: GraphStore,
    project_id: str,
    *,
    text: str,
    source: str,
    root: Path,
) -> ImportReport:
    return import_prepared(
        store,
        project_id,
        book=prepare_text(text, source=source),
        root=root,
    )
```

Refactor existing `import_book()` to read UTF-8-SIG and delegate to `import_text`; retain its current error messages and return model.

- [ ] **Step 6: Run importer and project suites**

Run:

```bash
uv run pytest -q tests/test_project.py tests/test_importer.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/novel_harness/project.py src/novel_harness/importer.py tests/test_project.py tests/test_importer.py
git commit -m "refactor: prepare atomic book bootstrap"
```

---

### Task 3: Add the atomic onboarding service

**Files:**

- Create: `src/novel_harness/onboarding.py`
- Create: `tests/test_onboarding.py`

- [ ] **Step 1: Write service-level success and rollback tests**

Create `tests/test_onboarding.py` with helpers that migrate a temporary database, then add these cases:

```python
from pathlib import Path

import pytest

from novel_harness import onboarding, project
from novel_harness.db import connect, migrate
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import ImportRefused


def setup_db(tmp_path: Path):
    conn = connect(tmp_path / "book.db")
    migrate(conn)
    return conn, tmp_path / "books"


def test_import_bootstrap_creates_project_files_and_index(tmp_path: Path) -> None:
    conn, books = setup_db(tmp_path)
    result = onboarding.bootstrap_project(
        conn,
        books_root=books,
        mode="import",
        name="青云记",
        text="第一章 初见\n\n风起。\n第二章 夜雨\n\n雨落。\n",
    )

    assert result.project.name == "青云记"
    assert result.initial_chapter == 1
    assert result.import_report is not None
    assert result.import_report.chapter_count == 2
    root = Path(result.project.root_path)
    assert sorted(path.name for path in (root / "chapters").iterdir()) == ["0001.md", "0002.md"]
    assert [row.number for row in SqliteStoryGraph(conn).current_snapshots(result.project.id)] == [1, 2]


def test_blank_bootstrap_creates_a_real_first_chapter(tmp_path: Path) -> None:
    conn, books = setup_db(tmp_path)
    result = onboarding.bootstrap_project(
        conn, books_root=books, mode="blank", name="新书", text=None
    )
    assert result.import_report is None
    assert (Path(result.project.root_path) / "chapters/0001.md").read_text(encoding="utf-8") == "第一章\n\n"
    assert [row.number for row in SqliteStoryGraph(conn).current_snapshots(result.project.id)] == [1]


def test_zero_chapter_import_leaves_no_project_or_book_directory(tmp_path: Path) -> None:
    conn, books = setup_db(tmp_path)
    with pytest.raises(ImportRefused):
        onboarding.bootstrap_project(
            conn, books_root=books, mode="import", name="坏书", text="没有章标"
        )
    assert project.list_all(conn) == []
    assert not (books / "坏书").exists()


def test_file_write_failure_rolls_back_database_and_removes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, books = setup_db(tmp_path)
    original_write_text = Path.write_text

    def fail_chapter_write(path: Path, *args, **kwargs):
        if path.name == "0001.md":
            raise OSError("injected file write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_chapter_write)
    with pytest.raises(OSError, match="injected file write failure"):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="写入失败书",
            text="第一章\n\n正文。\n",
        )
    assert project.list_all(conn) == []
    assert list(books.glob(".nh-bootstrap-*")) == []
    assert not (books / "写入失败书").exists()


def test_sync_failure_rolls_back_database_and_removes_written_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, books = setup_db(tmp_path)

    def fail_sync(*args, **kwargs):
        raise RuntimeError("injected sync failure")

    monkeypatch.setattr(onboarding.importer, "sync", fail_sync)
    with pytest.raises(RuntimeError, match="injected sync failure"):
        onboarding.bootstrap_project(
            conn,
            books_root=books,
            mode="import",
            name="失败书",
            text="第一章\n\n正文。\n",
        )
    assert project.list_all(conn) == []
    assert list(books.glob(".nh-bootstrap-*")) == []
    assert not (books / "失败书").exists()


class CommitFailingConnection:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def commit(self) -> None:
        raise RuntimeError("injected commit failure")


def test_commit_failure_rolls_back_database_and_removes_final_directory(tmp_path: Path) -> None:
    conn, books = setup_db(tmp_path)
    failing = CommitFailingConnection(conn)

    with pytest.raises(RuntimeError, match="injected commit failure"):
        onboarding.bootstrap_project(
            failing,
            books_root=books,
            mode="blank",
            name="提交失败书",
            text=None,
        )

    assert project.list_all(conn) == []
    assert list(books.glob(".nh-bootstrap-*")) == []
    assert not (books / "提交失败书").exists()
```

Add the directory-collision test explicitly:

```python
def test_existing_book_directory_is_never_reused_or_changed(tmp_path: Path) -> None:
    conn, books = setup_db(tmp_path)
    occupied = books / "同名"
    occupied.mkdir(parents=True)
    marker = occupied / "作者原稿.md"
    marker.write_text("不要改我", encoding="utf-8")

    result = onboarding.bootstrap_project(
        conn, books_root=books, mode="blank", name="同名", text=None
    )

    assert Path(result.project.root_path) == books / "同名-2"
    assert marker.read_text(encoding="utf-8") == "不要改我"
    assert (books / "同名-2/chapters/0001.md").exists()
```

- [ ] **Step 2: Run the new suite and verify it fails**

Run:

```bash
uv run pytest -q tests/test_onboarding.py
```

Expected: FAIL because `novel_harness.onboarding` does not exist.

- [ ] **Step 3: Implement the focused service**

Create `src/novel_harness/onboarding.py` with:

```python
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

_UNSAFE_PATH = re.compile(r'[/\\:*?"<>|]')
BootstrapMode = Literal["import", "blank"]


class BootstrapResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    project: project.Project
    initial_chapter: int = 1
    import_report: importer.ImportReport | None = None


def next_book_root(base: Path, name: str) -> Path:
    slug = _UNSAFE_PATH.sub("", name).strip() or "book"
    root = base / slug
    suffix = 2
    while root.exists():
        root = base / f"{slug}-{suffix}"
        suffix += 1
    return root


def create_legacy_root(base: Path, name: str) -> Path:
    slug = _UNSAFE_PATH.sub("", name).strip() or "book"
    root = base / slug
    suffix = 2
    while root.exists() and any(root.iterdir()):
        root = base / f"{slug}-{suffix}"
        suffix += 1
    root.mkdir(parents=True, exist_ok=True)
    return root


def bootstrap_project(
    conn: Connection,
    *,
    books_root: Path,
    mode: BootstrapMode,
    name: str,
    text: str | None,
) -> BootstrapResult:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("name 不能为空")
    if mode == "import":
        if text is None:
            raise ValueError("导入模式必须提供 text")
        prepared = importer.prepare_text(text, source="浏览器选择的 TXT")
    elif mode == "blank":
        if text is not None:
            raise ValueError("空白书模式不接受 text")
        prepared = importer.prepare_text("第一章\n\n", source="系统生成的第一章")
    else:
        raise ValueError(f"未知的启动模式：{mode}")

    books_root.mkdir(parents=True, exist_ok=True)
    final_root = next_book_root(books_root, clean_name)
    stage = Path(tempfile.mkdtemp(prefix=".nh-bootstrap-", dir=books_root))
    moved = False
    store = SqliteStoryGraph(conn)
    try:
        with store.transaction():
            created = project.insert(conn, name=clean_name, root_path=str(final_root))
            report = importer.import_prepared(store, created.id, book=prepared, root=stage)
            result = BootstrapResult(
                project=created,
                initial_chapter=1,
                import_report=report if mode == "import" else None,
            )
            stage.rename(final_root)
            moved = True
        return result
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        target = final_root if moved else stage
        if target.exists():
            shutil.rmtree(target)
        raise
```

Document that `stage` and `final_root` are created exclusively by this invocation, making cleanup bounded and safe.

- [ ] **Step 4: Run service and architecture-guard tests**

Run:

```bash
uv run pytest -q tests/test_onboarding.py tests/test_arch_guard.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/novel_harness/onboarding.py tests/test_onboarding.py
git commit -m "feat: bootstrap books atomically"
```

---

### Task 4: Expose and freeze the bootstrap HTTP contract

**Files:**

- Modify: `src/novel_harness/api/app.py`
- Create: `tests/test_onboarding_api.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `frontend/src/__fixtures__/api.json`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_onboarding_api.py` with the exact temporary app fixture and request cases below:

```python
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness.db import connect, migrate


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "book.db"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    monkeypatch.setenv("NH_DB", str(db_path))
    monkeypatch.setenv("NH_BOOKS_DIR", str(tmp_path / "books"))

    from novel_harness.api.app import app

    with TestClient(app) as test_client:
        yield test_client


def test_bootstrap_import_returns_project_and_real_report(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={
            "mode": "import",
            "name": "青云记",
            "text": "第一章 初见\n\n风起。\n第二章 夜雨\n\n雨落。\n",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["name"] == "青云记"
    assert body["initial_chapter"] == 1
    assert body["import_report"]["chapter_count"] == 2
    assert (tmp_path / "books/青云记/chapters/0001.md").exists()


def test_bootstrap_blank_rejects_text(client: TestClient) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={"mode": "blank", "name": "空白书", "text": "不允许"},
    )
    assert response.status_code == 422


def test_bootstrap_import_error_leaves_projects_empty(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects/bootstrap",
        json={"mode": "import", "name": "坏书", "text": "没有章标"},
    )
    assert response.status_code == 409
    assert client.get("/api/projects").json() == []
    assert not (tmp_path / "books/坏书").exists()
```

- [ ] **Step 2: Run the API suite and confirm 404**

Run:

```bash
uv run pytest -q tests/test_onboarding_api.py
```

Expected: FAIL because `/api/projects/bootstrap` returns 404.

- [ ] **Step 3: Add discriminated request models and the thin route**

In `api/app.py`, import `Literal` and `onboarding`, then add:

```python
class ImportBootstrap(BaseModel):
    mode: Literal["import"]
    name: str
    text: str


class BlankBootstrap(BaseModel):
    mode: Literal["blank"]
    name: str


BootstrapBody = Annotated[ImportBootstrap | BlankBootstrap, Field(discriminator="mode")]


@app.post("/api/projects/bootstrap", response_model=onboarding.BootstrapResult)
def bootstrap_project(body: BootstrapBody, conn: Any = Depends(get_conn)) -> Any:
    return onboarding.bootstrap_project(
        conn,
        books_root=books_root(),
        mode=body.mode,
        name=body.name,
        text=body.text if isinstance(body, ImportBootstrap) else None,
    )
```

Replace `_new_book_root()`’s private sanitization with `onboarding.create_legacy_root(books_root(), body.name)` and remove `_UNSAFE_PATH` from `app.py`; existing `POST /api/projects` keeps its response and commit behavior.

- [ ] **Step 4: Freeze one real bootstrap import response**

In `tests/test_frontend_contract.py`, after the initial `projects` grab, add:

```python
grab(
    "bootstrapImport",
    client.post(
        "/api/projects/bootstrap",
        json={
            "mode": "import",
            "name": "契约样书",
            "text": "第一章 契约\n\n正文。\n",
        },
    ),
)
```

Regenerate and inspect the fixture:

```bash
NH_UPDATE_FIXTURES=1 uv run pytest -q tests/test_frontend_contract.py
git diff -- frontend/src/__fixtures__/api.json
```

Expected: one new `bootstrapImport` object containing `project`, `initial_chapter`, and a real `import_report`.

- [ ] **Step 5: Update the single authoritative architecture counts**

The new endpoint changes current runtime counts from 39/38 to 40/39 and the contract fixture count from 28 to 29. Update only the matching current-state statements in `docs/ARCHITECTURE.md`; do not copy these numbers into README or frontend README.

- [ ] **Step 6: Run HTTP contract and documentation guards**

Run:

```bash
uv run pytest -q tests/test_onboarding_api.py tests/test_frontend_contract.py tests/test_doc_numbers.py tests/test_arch_guard.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/novel_harness/api/app.py tests/test_onboarding_api.py tests/test_frontend_contract.py frontend/src/__fixtures__/api.json docs/ARCHITECTURE.md
git commit -m "feat: expose atomic book bootstrap"
```

---

### Task 5: Add frontend bootstrap types, decoding coverage, and mutation hook

**Files:**

- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/test/harness.tsx`
- Create: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Write UTF-8 and GBK decoding tests**

Create `frontend/src/api/client.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { readTextFile } from "./client";

describe("readTextFile", () => {
  it("reads UTF-8 Chinese text", async () => {
    const file = new File([new TextEncoder().encode("第一章\n正文")], "utf8.txt");
    await expect(readTextFile(file)).resolves.toBe("第一章\n正文");
  });

  it("falls back to GBK when UTF-8 contains replacement characters", async () => {
    const bytes = Uint8Array.from([0xb5, 0xda, 0xd2, 0xbb, 0xd5, 0xc2]);
    const file = new File([bytes], "gbk.txt");
    await expect(readTextFile(file)).resolves.toBe("第一章");
  });
});
```

- [ ] **Step 2: Run decoding tests**

Run:

```bash
cd frontend && npm test -- src/api/client.test.ts
```

Expected: both pass on the pinned Node runtime. If Node lacks the GBK decoder, the test must fail rather than weakening the browser behavior contract.

- [ ] **Step 3: Add exact TypeScript contracts**

Add to `frontend/src/api/types.ts`:

```typescript
export type BootstrapRequest =
  | { mode: "import"; name: string; text: string }
  | { mode: "blank"; name: string };

export interface BootstrapResult {
  project: Project;
  initial_chapter: number;
  import_report: ImportReport | null;
}
```

- [ ] **Step 4: Add the mutation hook and real fixture handler**

In `frontend/src/api/hooks.ts`:

```typescript
export function useBootstrapProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: BootstrapRequest) =>
      api.post<BootstrapResult>("/api/projects/bootstrap", input),
    onSuccess: (result) => {
      const pid = result.project.id;
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["chapters", pid] });
      qc.invalidateQueries({ queryKey: ["roster", pid] });
    },
  });
}
```

Import `BootstrapRequest` and `BootstrapResult`. In `frontend/src/test/harness.tsx`, add before the generic projects handler:

```typescript
{ method: "POST", match: /\/api\/projects\/bootstrap$/, body: fixtures.bootstrapImport },
```

- [ ] **Step 5: Run type/build checks**

Run:

```bash
cd frontend && npm test -- src/api/client.test.ts && npm run build
```

Expected: decoding tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/hooks.ts frontend/src/test/harness.tsx frontend/src/api/client.test.ts
git commit -m "feat: add onboarding bootstrap client"
```

---

### Task 6: Build the local onboarding state machine with component tests

**Files:**

- Create: `frontend/src/components/Setup.test.tsx`
- Create: `frontend/src/components/onboarding/OnboardingShell.tsx`
- Create: `frontend/src/components/onboarding/StartChooser.tsx`
- Create: `frontend/src/components/onboarding/ImportReview.tsx`
- Create: `frontend/src/components/onboarding/BlankBookForm.tsx`
- Modify: `frontend/src/components/Setup.tsx`

- [ ] **Step 1: Write failing chooser and import-flow tests**

Create `Setup.test.tsx` with this exact test harness header, then add the assertions below:

```typescript
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { useCoords } from "../store";
import { fixtures, renderWithApi } from "../test/harness";
import { Setup } from "./Setup";

beforeEach(() => {
  useCoords.setState({ projectId: null, chapter: 1 });
});


it("starts with two clear launch actions and no native filename field", async () => {
  renderWithApi(<Setup />);
  expect(screen.getByRole("button", { name: /导入现有小说/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /新建空白小说/ })).toBeInTheDocument();
  expect(screen.queryByLabelText("书名")).not.toBeInTheDocument();
  expect(screen.getByLabelText("选择 TXT")).toHaveClass("setup-file-input");
});

it("reviews a selected TXT before creating anything", async () => {
  const user = userEvent.setup();
  renderWithApi(<Setup />);
  const file = new File(["第一章 初见\n\n风起。\n"], "青云记.txt", { type: "text/plain" });
  await user.upload(screen.getByLabelText("选择 TXT"), file);
  expect(screen.getByLabelText("书名")).toHaveValue("青云记");
  expect(screen.getByText("青云记.txt")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导入并进入工作台" })).toBeEnabled();
  expect(useCoords.getState().projectId).toBeNull();
});

it("sets the project only after bootstrap succeeds", async () => {
  const user = userEvent.setup();
  renderWithApi(<Setup />);
  await user.upload(
    screen.getByLabelText("选择 TXT"),
    new File(["第一章\n\n正文。\n"], "青云记.txt")
  );
  await user.click(screen.getByRole("button", { name: "导入并进入工作台" }));
  await waitFor(() =>
    expect(useCoords.getState().projectId).toBe(fixtures.bootstrapImport.project.id),
  );
  expect(useCoords.getState().chapter).toBe(1);
});
```

Import `fixtures` from `../test/harness`; the assertion above stays coupled to the generated backend contract instead of hard-coding a normalized ID.

- [ ] **Step 2: Write failing blank/error/drawer tests**

Add:

```typescript
it("creates a blank book through a named confirmation state", async () => {
  const user = userEvent.setup();
  renderWithApi(<Setup />);
  await user.click(screen.getByRole("button", { name: /新建空白小说/ }));
  await user.type(screen.getByLabelText("书名"), "新书");
  expect(screen.getByRole("button", { name: "创建并进入工作台" })).toBeEnabled();
});

it("keeps file and title visible when import is refused", async () => {
  const user = userEvent.setup();
  renderWithApi(<Setup />, [
    {
      method: "POST",
      match: /\/api\/projects\/bootstrap$/,
      status: 409,
      body: { error: "import_refused", message: "切不出章节" },
    },
  ]);
  await user.upload(screen.getByLabelText("选择 TXT"), new File(["坏正文"], "坏书.txt"));
  await user.click(screen.getByRole("button", { name: "导入并进入工作台" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("切不出章节");
  expect(screen.getByLabelText("书名")).toHaveValue("坏书");
});

it("drawer mode omits the brand panel and closes after success", async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  renderWithApi(<Setup onClose={onClose} />);
  expect(screen.queryByText("让长篇小说中的每个人，只知道此刻该知道的事。")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /新建空白小说/ }));
  await user.type(screen.getByLabelText("书名"), "新书");
  await user.click(screen.getByRole("button", { name: "创建并进入工作台" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
});

it("operates both launch actions from the keyboard in visual order", async () => {
  const user = userEvent.setup();
  renderWithApi(<Setup />);
  const importButton = screen.getByRole("button", { name: /导入现有小说/ });
  const blankButton = screen.getByRole("button", { name: /新建空白小说/ });
  const fileInput = screen.getByLabelText("选择 TXT") as HTMLInputElement;
  const openPicker = vi.spyOn(fileInput, "click");

  await user.tab();
  expect(importButton).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(openPicker).toHaveBeenCalledOnce();
  await user.tab();
  expect(blankButton).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(screen.getByLabelText("书名")).toHaveFocus();
});
```

- [ ] **Step 3: Run the suite and confirm current Setup fails**

Run:

```bash
cd frontend && npm test -- src/components/Setup.test.tsx
```

Expected: FAIL because current Setup renders a visible book-name field and has no two-entry flow.

- [ ] **Step 4: Implement focused presentational components**

Use these public props:

```typescript
export interface StartChooserProps {
  onPickImport: () => void;
  onPickBlank: () => void;
  inputRef: React.RefObject<HTMLInputElement>;
  onFile: (file: File) => void;
}

export interface ImportReviewProps {
  file: File;
  name: string;
  onName: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
  pending: boolean;
  error: string | null;
}

export interface BlankBookFormProps {
  name: string;
  onName: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
  pending: boolean;
  error: string | null;
}
```

`OnboardingShell` accepts `{ compact: boolean; children: ReactNode }`; compact mode renders only the right flow panel. `StartChooser` owns the hidden input label `选择 TXT`, but `Setup` owns the ref and file callback.

Render the approved copy verbatim: `Novel Harness`, `让长篇小说中的每个人，只知道此刻该知道的事。`, the three local-first promises, `Novel workspace`, `从哪里开始？`, and `打开已有手稿，或创建一本全新的小说。`. The chooser buttons use the names/descriptions `导入现有小说` / `选择 TXT，自动识别书名并切分章节` and `新建空白小说` / `创建第一章，从零开始写`. Give the hidden file input `tabIndex={-1}` because the visible import button is its keyboard-operable trigger; after switching to either form, focus the book-name input.

- [ ] **Step 5: Implement Setup as the local coordinator**

Use this state shape and success boundary:

```typescript
type Mode = "choose" | "import-review" | "blank-name";

const [mode, setMode] = useState<Mode>("choose");
const [file, setFile] = useState<File | null>(null);
const [text, setText] = useState("");
const [name, setName] = useState("");
const bootstrap = useBootstrapProject();

function enter(result: BootstrapResult) {
  setProject(result.project.id);
  setChapter(result.initial_chapter);
  onClose?.();
}

async function selectFile(next: File) {
  setFile(next);
  setName(next.name.replace(/\.txt$/i, ""));
  setText(await readTextFile(next));
  setMode("import-review");
}

function submitImport() {
  bootstrap.mutate(
    { mode: "import", name: name.trim(), text },
    { onSuccess: enter },
  );
}

function submitBlank() {
  bootstrap.mutate(
    { mode: "blank", name: name.trim() },
    { onSuccess: enter },
  );
}
```

Render `ApiError.message` in a `role="alert"` block. Back returns to `choose` and calls `bootstrap.reset()`; import errors do not clear `file`, `text`, or `name`.

- [ ] **Step 6: Run component tests and all frontend tests**

Run:

```bash
cd frontend && npm test -- src/components/Setup.test.tsx && npm test
```

Expected: Setup tests and the full Vitest suite pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Setup.tsx frontend/src/components/Setup.test.tsx frontend/src/components/onboarding
git commit -m "feat: add guided onboarding states"
```

---

### Task 7: Apply the approved A+A1 styling and complete verification

**Files:**

- Modify: `frontend/src/styles.css`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Add semantic onboarding tokens and approved layout**

Extend the light and dark variable blocks with onboarding-only semantic colors:

```css
:root {
  --welcome-side: #eceef1;
  --welcome-surface: #ffffff;
  --welcome-action: #242831;
  --welcome-action-ink: #ffffff;
  --welcome-soft: #f4f5f7;
  --welcome-shadow: 0 24px 70px rgba(24, 28, 36, 0.13);
}

@media (prefers-color-scheme: dark) {
  :root {
    --welcome-side: #1b1d22;
    --welcome-surface: #22252b;
    --welcome-action: #f1f2f4;
    --welcome-action-ink: #202329;
    --welcome-soft: #2b2e35;
    --welcome-shadow: 0 24px 70px rgba(0, 0, 0, 0.32);
  }
}
```

Replace the seven-line legacy setup section with selectors matching the approved mockup:

```css
.setup-full { min-height: 100%; display: grid; place-items: center; padding: 40px; background: var(--panel); }
.onboarding-shell { width: min(1120px, 100%); min-height: min(680px, calc(100vh - 80px)); display: grid; grid-template-columns: 37% 63%; overflow: hidden; border: 1px solid var(--line); border-radius: 16px; background: var(--welcome-surface); box-shadow: var(--welcome-shadow); }
.onboarding-identity { padding: 64px 52px 42px; display: flex; flex-direction: column; background: var(--welcome-side); border-right: 1px solid var(--line); }
.onboarding-logo { width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px; background: var(--welcome-action); color: var(--welcome-action-ink); font-size: 17px; font-weight: 700; }
.onboarding-brand { margin-top: 22px; font-size: 24px; font-weight: 690; letter-spacing: -.045em; }
.onboarding-tagline { max-width: 240px; margin-top: 13px; color: var(--dim); font-size: 13px; line-height: 1.7; }
.onboarding-promises { margin-top: auto; display: grid; gap: 9px; color: var(--dim); font-size: 11px; }
.onboarding-flow { display: grid; place-items: center; padding: 58px 70px; background: var(--welcome-surface); }
.onboarding-panel { width: min(470px, 100%); }
.onboarding-eyebrow { color: var(--dim); font-size: 10px; font-weight: 650; letter-spacing: .11em; text-transform: uppercase; }
.onboarding-title { margin: 10px 0 0; font-size: 30px; line-height: 1.15; font-weight: 700; letter-spacing: -.05em; }
.onboarding-lead { margin: 10px 0 0; color: var(--dim); font-size: 13px; line-height: 1.6; }
.onboarding-actions { display: grid; gap: 10px; margin-top: 28px; }
.onboarding-action { width: 100%; display: flex; align-items: center; gap: 13px; padding: 15px 16px; border-radius: 11px; text-align: left; background: var(--welcome-surface); transition: border-color 140ms ease, background-color 140ms ease, color 140ms ease, transform 140ms ease; }
.onboarding-action.primary { border-color: var(--welcome-action); background: var(--welcome-action); color: var(--welcome-action-ink); box-shadow: 0 7px 18px rgba(24, 28, 35, .18); }
.onboarding-action-icon { width: 32px; height: 32px; flex: 0 0 32px; display: grid; place-items: center; border-radius: 8px; background: var(--welcome-soft); color: var(--ink); }
.onboarding-action.primary .onboarding-action-icon { background: rgba(255, 255, 255, .14); color: inherit; }
.onboarding-action-copy { min-width: 0; display: grid; gap: 3px; }
.onboarding-action-name { font-size: 13px; font-weight: 650; line-height: 1.25; }
.onboarding-action-description { color: var(--dim); font-size: 11px; line-height: 1.45; }
.onboarding-action.primary .onboarding-action-description { color: color-mix(in srgb, currentColor 72%, transparent); }
.onboarding-action:hover:not(:disabled) { transform: translateY(-1px); }
.onboarding-action:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent) 35%, transparent); outline-offset: 2px; }
.setup-file-input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.onboarding-form { margin-top: 24px; display: grid; gap: 14px; }
.onboarding-form input[type="text"] { width: 100%; min-height: 40px; padding: 9px 11px; border-radius: 8px; }
.onboarding-file { padding: 12px; border: 1px solid var(--line); border-radius: 9px; background: var(--welcome-soft); }
.onboarding-file-name { overflow: hidden; font-size: 12px; font-weight: 620; text-overflow: ellipsis; white-space: nowrap; }
.onboarding-file-meta { margin-top: 3px; color: var(--dim); font-size: 10px; }
.onboarding-error { padding: 10px 12px; border-radius: 8px; background: var(--warnbg); color: var(--warn); }
.onboarding-form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.onboarding-promise { display: flex; align-items: center; gap: 7px; }
.onboarding-promise-mark { width: 15px; height: 15px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--ink); font-size: 9px; }
.onboarding-footer { display: flex; justify-content: space-between; gap: 12px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--dim); font-size: 10px; }
.onboarding-shell.compact { display: block; min-height: 0; border: 0; border-radius: 0; box-shadow: none; }
.onboarding-shell.compact .onboarding-flow { padding: 0; }

@media (max-width: 900px) {
  .setup-full { padding: 20px; }
  .onboarding-shell { grid-template-columns: 1fr; min-height: calc(100vh - 40px); }
  .onboarding-identity { display: none; }
  .onboarding-flow { padding: 42px 28px; }
}

@media (max-width: 600px) {
  .setup-full { padding: 0; }
  .onboarding-shell { min-height: 100vh; border: 0; border-radius: 0; }
  .onboarding-flow { padding: 32px 20px; }
  .onboarding-title { font-size: 26px; }
  .onboarding-footer { flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  .onboarding-action { transition: none; }
  .onboarding-action:hover:not(:disabled) { transform: none; }
}
```

Use the child class names in this CSS block verbatim in the presentational components so the visual contract stays explicit.

- [ ] **Step 2: Run focused visual component tests and build**

Run:

```bash
cd frontend && npm test -- src/components/Setup.test.tsx src/api/client.test.ts && npm run build
```

Expected: tests pass and Vite build writes the package web UI successfully.

- [ ] **Step 3: Run full backend and frontend verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
cd frontend && npm test
cd frontend && npm run build
```

Expected: zero failures and zero Ruff errors.

- [ ] **Step 4: Update only authoritative current-state numbers**

Use the full pytest/vitest output and runtime route/fixture guards to update the single current-state counts in `docs/ARCHITECTURE.md`. Do not add copies to README, CLAUDE.md, frontend README, or the implementation plan.

- [ ] **Step 5: Manually verify the real browser flow**

With the development servers running, verify:

1. Light system appearance matches approved A+A1 hierarchy.
2. System dark appearance remains legible and preserves hierarchy.
3. Importing a valid TXT enters a real chapter and leaves the source TXT untouched.
4. Importing a zero-chapter TXT stays on the review panel and leaves no project/directory.
5. Creating a blank book opens a real `chapters/0001.md`.
6. At a viewport below 900px, the brand column hides and no horizontal scrolling appears.
7. Tab/Shift-Tab and Enter operate every action with a visible focus ring.

- [ ] **Step 6: Inspect the final diff for generated or user data**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: no `book.db*`, `books/`, `.superpowers/`, or built `webui/` artifacts are staged.

- [ ] **Step 7: Commit the visual completion**

```bash
git add frontend/src/styles.css docs/ARCHITECTURE.md
git commit -m "feat: polish book onboarding experience"
```

---

## Final acceptance checklist

- [ ] Approved A+A1 full-screen layout is implemented without a new UI dependency.
- [ ] Full page and compact drawer share one local state machine.
- [ ] Native file input is visually hidden but accessible.
- [ ] Import success produces real Markdown chapters and selects chapter 1.
- [ ] Blank success produces a real `chapters/0001.md` and snapshot.
- [ ] Every failure path leaves no new project, final directory, or staging directory.
- [ ] Existing create/import APIs retain their contracts.
- [ ] Request-scoped SQLite connections survive FastAPI thread handoff.
- [ ] Backend tests, Ruff, Vitest, TypeScript build, light/dark, responsive, and keyboard checks pass.
