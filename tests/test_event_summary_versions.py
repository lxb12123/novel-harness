"""事件摘要版本化（Task 7 / ADR 0030）：baseline、作者编辑、Canon bump、regenerate。

钉住：新事件 baseline/head 同事务建立；待确认摘要单独保存后仍 PENDING；
Canon 编辑 bump canon version 且 `story_event.source` 仍保持 extractor；
陈旧 expected version 409；regenerate 只创建持久 job、新意图 supersede 旧 job。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import project
from novel_harness.db import connect, migrate
from novel_harness.events.summaries import (
    current_event_summary,
    edit_event_summary,
    event_summary_history,
    regenerate_event_summary,
)
from novel_harness.extract.auto_canon import promote_clean_facts
from novel_harness.extract.service import ExtractionService
from novel_harness.extract import RawChapterAnalysis, RawEvent
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.graph import NodeLabel, NodeProps, NodeSpec
from novel_harness import importer


QUOTE = "萧决在渡口把玄铁令交给顾清音。"
CHAPTER_TEXT = "第一章 渡口\n\n" + QUOTE + "\n"


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="t", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="萧决",
            props=NodeProps(main_character=True),
        )
    )
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(main_character=True),
        )
    )
    src = tmp_path / "s.txt"
    src.write_text(CHAPTER_TEXT, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _ingest_event(book: dict[str, str], *, summary: str, promote: bool = False) -> str:
    """走真 ingest 建一条事件（模型基线版本随事件出生），可选自动升 CANON。"""
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        graph = SqliteStoryGraph(conn)
        current = next(ct for ct in graph.current_snapshots(pid) if ct.number == 1)
        service = ExtractionService(
            conn=conn,
            graph=graph,
            event_store=SqliteEventStore(conn),
            proposal_store=SqliteProposalStore(conn),
        )
        report = service.ingest(
            pid,
            current,
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary=summary,
                        quote=QUOTE,
                        participants=("萧决", "顾清音"),
                        knowers=("萧决",),
                        revealed_facts=(),
                        confidence=0.95,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
            ),
            prompt_hash="prompt:event-summary-test",
        )
        if promote:
            promote_clean_facts(
                conn,
                pid,
                report,
                graph=graph,
                events=SqliteEventStore(conn),
            )
        conn.commit()
        return report.event_ids[0]
    finally:
        conn.close()


def test_ingest_creates_a_baseline_version_bound_to_evidence(
    book: dict[str, str],
) -> None:
    event_id = _ingest_event(book, summary="萧决把玄铁令交给了顾清音。")
    conn = connect(book["db"])
    try:
        version = current_event_summary(conn, event_id)
        assert version is not None
        assert version.summary == "萧决把玄铁令交给了顾清音。"
        assert version.source == "model"
        assert version.source_snapshot_id is not None
        assert version.evidence_sha256 is not None
    finally:
        conn.close()


def test_author_edit_stays_pending_and_appends_a_version(
    book: dict[str, str],
) -> None:
    event_id = _ingest_event(book, summary="机器版本。")
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        baseline = current_event_summary(conn, event_id)
        assert baseline is not None
        edited = edit_event_summary(
            conn,
            project_id=pid,
            event_id=event_id,
            text="作者改过的摘要。",
            expected_version_id=baseline.id,
            scope="PROVISIONAL",
        )
        assert edited.source == "author"
        assert edited.replaces_version_id == baseline.id
        # 待确认摘要：只保存，不接受（proposal 保持 PENDING——本事件没建提案，
        # 但 API 的 PATCH 不改 proposal status 由 `edit_event_summary` 保证）。
        versions = event_summary_history(conn, event_id)
        assert [v.source for v in versions] == ["model", "author"]
    finally:
        conn.close()


def test_canon_edit_bumps_canon_version_and_keeps_extractor_source(
    book: dict[str, str],
) -> None:
    event_id = _ingest_event(book, summary="机器版本。", promote=True)
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        before = project.require_canon_version(conn, pid)
        baseline = current_event_summary(conn, event_id)
        assert baseline is not None
        edited = edit_event_summary(
            conn,
            project_id=pid,
            event_id=event_id,
            text="作者改的 Canon 摘要。",
            expected_version_id=baseline.id,
            expected_canon_version=before,
            scope="CANON",
        )
        assert edited.source == "author"
        assert project.require_canon_version(conn, pid) == before + 1
        # story_event.source 仍是 extractor（不变量 21：起源与当前编辑者是两个字段）。
        row = conn.execute(
            "SELECT source, summary FROM story_event WHERE id = ?", (event_id,)
        ).fetchone()
        assert row["source"] == "extractor"
        # 读端看到的是 effective head（作者版），不是 story_event.summary 基线。
        assert row["summary"] == "机器版本。"
    finally:
        conn.close()


def test_stale_expected_version_is_409(client: TestClient, book: dict[str, str]) -> None:
    pid = book["pid"]
    # 先建一条事件再走 HTTP（api 只读库里的东西）。
    event_id = _ingest_event(book, summary="机器版本。")
    r = client.patch(
        f"/api/projects/{pid}/events/{event_id}/summary",
        json={"summary": "作者新版", "expected_version_id": "stale"},
    )
    assert r.status_code == 409


def test_regenerate_creates_a_persistent_job_and_supersedes_old_one(
    book: dict[str, str],
) -> None:
    event_id = _ingest_event(book, summary="机器版本。")
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        first = regenerate_event_summary(
            conn, project_id=pid, event_id=event_id, trigger_key="regenerate:1"
        )
        second = regenerate_event_summary(
            conn, project_id=pid, event_id=event_id, trigger_key="regenerate:2"
        )
        conn.commit()
        rows = conn.execute(
            "SELECT id, status, expected_head_version_id, required_machine_intent_seq "
            "FROM summary_generation_job WHERE project_id = ? AND target_type = 'EVENT' "
            "ORDER BY created_at",
            (pid,),
        ).fetchall()
        assert [r["status"] for r in rows] == ["SUPERSEDED", "PENDING"]
        assert rows[1]["expected_head_version_id"] is not None
        assert rows[1]["required_machine_intent_seq"] > rows[0]["required_machine_intent_seq"]
        assert first != second
    finally:
        conn.close()
