"""版本化滚动总结的 CAS 纪律（Task 6 / ADR 0030）。

钉住四条：NULL head 的首次 CAS、作者编辑立即切 head 且机器行保留、
「重新总结」冻结 head + 递增 intent + supersede 旧 job、机器晚到 CAS 失败
只留 result 审计（不产生一条假装生效过的 ACTIVE 版本）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_draft_api import _generate
from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import (
    RollingSummarizer,
    SummaryRequest,
    save_author_summary,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph


MODEL = "deepseek-v4-flash"
BOOK_TEXT = "第一章 甲\n\n萧决走进了青云城主府。\n"


class StubAnalyzer:
    """可编程总结器：返回固定文本，或在调用中途让作者改掉 head。"""

    def __init__(
        self,
        text: str = "萧决到了青云城主府。",
        *,
        on_call: Any = None,
    ) -> None:
        self.text = text
        self.on_call = on_call

    def __call__(self, request: SummaryRequest) -> CompletionResult:
        if self.on_call is not None:
            self.on_call()
        return CompletionResult(text=self.text, model=MODEL, finish_reason="stop")


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="t", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    src = tmp_path / "s.txt"
    src.write_text(BOOK_TEXT, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


@pytest.fixture
def client(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch, configured_model: None
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    import novel_harness.api.deps as deps_mod

    monkeypatch.setattr(
        deps_mod,
        "complete",
        lambda messages, *, config=None, plan=None, client=None: CompletionResult(
            text="萧决到了青云城主府。", model=MODEL, finish_reason="stop"
        ),
    )
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _head(book: dict[str, str], chapter: int = 1) -> dict[str, Any] | None:
    conn = connect(book["db"])
    try:
        row = conn.execute(
            """
            SELECT s.id, s.summary, s.source, s.status, s.replaces_summary_id
              FROM chapter c
              JOIN chapter_summary_head h ON h.chapter_id = c.id
              JOIN chapter_summary s ON s.id = h.current_summary_id
             WHERE c.project_id = ? AND c.number = ?
            """,
            (book["pid"], chapter),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def test_first_machine_summary_cas_from_null_head(book: dict[str, str]) -> None:
    pid = book["pid"]
    runner = RollingSummarizer(
        lambda: connect(book["db"]),
        StubAnalyzer(),
        summary_id_factory=lambda p: f"summary:{p}:1",
        call_id_factory=lambda p: f"call:{p}:1",
    )
    created = runner.ensure(pid, 1)
    head = _head(book)
    assert head is not None
    assert head["id"] == created.id
    assert head["source"] == "model" and head["status"] == "ACTIVE"
    assert head["replaces_summary_id"] is None
    conn = connect(book["db"])
    try:
        job = conn.execute(
            "SELECT status FROM summary_generation_job WHERE project_id = ?", (pid,)
        ).fetchone()
        assert job["status"] == "SUCCEEDED"
        result = conn.execute(
            "SELECT summary_sha256 FROM summary_generation_result WHERE job_id IN "
            "(SELECT id FROM summary_generation_job WHERE project_id = ?)",
            (pid,),
        ).fetchone()
        assert result is not None
    finally:
        conn.close()


def test_author_edit_switches_head_and_keeps_machine_row(book: dict[str, str]) -> None:
    pid = book["pid"]
    runner = RollingSummarizer(
        lambda: connect(book["db"]),
        StubAnalyzer(),
        summary_id_factory=lambda p: f"summary:{p}:1",
        call_id_factory=lambda p: f"call:{p}:1",
    )
    machine = runner.ensure(pid, 1)
    conn = connect(book["db"])
    try:
        save_author_summary(conn, project_id=pid, chapter_number=1, text="作者自己写的。")
    finally:
        conn.close()
    head = _head(book)
    assert head is not None
    assert head["source"] == "author"
    assert head["replaces_summary_id"] == machine.id
    conn = connect(book["db"])
    try:
        # 机器行仍在版本历史里。
        row = conn.execute(
            "SELECT source, status FROM chapter_summary WHERE id = ?", (machine.id,)
        ).fetchone()
        assert dict(row) == {"source": "model", "status": "ACTIVE"}
    finally:
        conn.close()


def test_stale_expected_version_is_409(client: TestClient, book: dict[str, str]) -> None:
    pid = book["pid"]
    _generate(book, 1)
    current = client.get(f"/api/projects/{pid}/chapters/1/summary").json()
    assert current["version_id"] is not None

    r = client.patch(
        f"/api/projects/{pid}/chapters/1/summary",
        json={"summary": "作者新版", "expected_version_id": "stale-version"},
    )
    assert r.status_code == 409
    # 作者输入没被吞：head 仍是机器那一条。
    assert client.get(f"/api/projects/{pid}/chapters/1/summary").json()["summary"] != "作者新版"


def test_late_machine_cas_failure_leaves_only_result_audit(
    book: dict[str, str],
) -> None:
    """模型调用期间作者改了 head：机器晚到 CAS 失败，只留 result 审计，
    不产生一条假装生效过的 ACTIVE 版本。"""
    pid = book["pid"]
    conn = connect(book["db"])
    try:
        # head=A 是作者版：ensure 的复用判据（要求 MODEL 行）不命中，强制走模型调用。
        save_author_summary(conn, project_id=pid, chapter_number=1, text="作者第一版。")
    finally:
        conn.close()

    def author_edits_during_model_call() -> None:
        other = connect(book["db"])
        try:
            save_author_summary(other, project_id=pid, chapter_number=1, text="作者抢跑。")
        finally:
            other.close()

    late = RollingSummarizer(
        lambda: connect(book["db"]),
        StubAnalyzer(text="晚到的机器总结。", on_call=author_edits_during_model_call),
        summary_id_factory=lambda p: f"summary:{p}:1",
        call_id_factory=lambda p: f"call:{p}:1",
    )
    result = late.ensure(pid, 1)
    head = _head(book)
    assert head["source"] == "author"
    assert head["summary"] == "作者抢跑。"
    # 晚到的机器文本没有成为版本（ensure 把作者版原样还了回来）。
    assert result.id == head["id"]
    conn = connect(book["db"])
    try:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM chapter_summary WHERE summary = '晚到的机器总结。'"
        ).fetchone()
        assert rows["n"] == 0
        job = conn.execute(
            "SELECT status FROM summary_generation_job WHERE project_id = ? ORDER BY created_at DESC",
            (pid,),
        ).fetchone()
        assert job["status"] == "SUPERSEDED"
    finally:
        conn.close()
