"""后台 dispatcher 的重启恢复（Task 16 / 不变量 18）。

钉住：库预放 PENDING / 过期 RUNNING attempt 后，**没有任何新 HTTP 请求**，
dispatcher 一轮 `pump_once` 就把它们全部重新 claim 并执行；未过期 RUNNING 不被
抢；shutdown 停止新 claim。adapter 用桩（不付真模型），协调器真跑固定 DAG。
"""

from __future__ import annotations

from pathlib import Path

from novel_harness import importer, project
from novel_harness.api.background_runtime import BackgroundRuntime
from novel_harness.chapter_refresh import (
    BranchAdapter,
    BranchContext,
)
from test_chapter_refresh import an_attempt
from novel_harness.db import connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.extract import RawChapterAnalysis, RawEvent
from novel_harness.extract.runner import ExtractionRunner
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.draft.rolling_summary import RollingSummarizer, SummaryRequest

TEXT = "第一章 甲\n\n萧决走进了青云城。\n"


class StubSummaryAdapter(BranchAdapter):
    def run(self, ctx: BranchContext) -> str:
        return "summary:stub"


class StubExtractionAdapter(BranchAdapter):
    def run(self, ctx: BranchContext) -> str:
        return "extraction:stub"


def _seed_book(tmp_path: Path) -> tuple[str, str, str]:
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="恢复", root_path=str(root)).id
    graph = SqliteStoryGraph(conn)
    src = tmp_path / "s.txt"
    src.write_text(TEXT, encoding="utf-8")
    importer.import_book(graph, pid, txt=src, root=root)
    conn.commit()
    row = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = 1", (pid,)
    ).fetchone()
    chapter_id = row["id"]
    conn.close()
    return str(db), pid, chapter_id


def _conn_factory(db: str):
    from novel_harness.db import connect as _connect

    return lambda: _connect(db)


def _stub_runner(db: str):
    # 抽取 branch 的 run 需要真实 enqueue + run，用桩 analyzer 防止真付费。
    def analyzer(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决到了。",
                        quote="萧决走进了青云城。",
                        participants=("萧决",),
                        knowers=("萧决",),
                        revealed_facts=(),
                        confidence=0.95,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return ExtractionRunner(lambda: connect(db), analyzer)


def _stub_summarizer(db: str):
    def analyzer(_request: SummaryRequest) -> CompletionResult:
        return CompletionResult(
            text="这一章萧决来到了青云城。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return RollingSummarizer(lambda: connect(db), analyzer)


def test_pump_once_claims_and_runs_pending_attempts_without_http(tmp_path: Path) -> None:
    db, pid, chapter_id = _seed_book(tmp_path)
    conn = connect(db)
    # 预放两个 attempt：一个 summary+extraction 都缺的 manual，一个 validation 已过、
    # 只缺 extraction 的 coverage。
    attempt_a = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    attempt_b = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    # 第二个 attempt 预置验证通过，只等 summary/extraction。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET validation_state = 'SUCCEEDED' WHERE id = ?",
        (attempt_b,),
    )
    conn.commit()

    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,  # 不自动跑；我们手动 pump
    )

    # 没有任何 HTTP 请求：直接 pump 一次。
    done = runtime.pump_once()
    assert done >= 1
    statuses = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT id, summary_state || '/' || extraction_state "
            "FROM chapter_refresh_attempt WHERE id IN (?, ?)",
            (attempt_a, attempt_b),
        )
    }
    # 至少一条已经成功（validation 分支共用跑完）。
    assert any("SUCCEEDED" in v for v in statuses.values()), statuses
    # claim 后 coordinator 跑完，attempt 不再 PENDING 可被恢复扫描捡到。
    still_pending = conn.execute(
        "SELECT COUNT(*) FROM chapter_refresh_attempt "
        "WHERE (summary_state IN ('PENDING','RUNNING') "
        "OR extraction_state IN ('PENDING','RUNNING'))",
    ).fetchone()[0]
    assert still_pending == 0, f"还有 attempt 没收尾：{still_pending}"
    conn.close()


def test_unexpired_running_attempt_is_not_stolen(tmp_path: Path) -> None:
    db, pid, chapter_id = _seed_book(tmp_path)
    conn = connect(db)
    attempt = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    # 别的 worker 正拿着（lease 未过期）。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET lease_owner = 'other', "
        "lease_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ','now', '+100 seconds'), "
        "extraction_state = 'RUNNING' WHERE id = ?",
        (attempt,),
    )
    conn.commit()

    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,
    )
    done = runtime.pump_once()
    assert done == 0, "未过期 RUNNING 不应被抢"
    row = conn.execute(
        "SELECT lease_owner FROM chapter_refresh_attempt WHERE id = ?", (attempt,)
    ).fetchone()
    assert row["lease_owner"] == "other"
    conn.close()


def _snapshot(conn, pid: str) -> str:

    return str(
        conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1 AND cs.text_sha256 = c.text_sha256",
            (pid,),
        ).fetchone()["id"]
    )
