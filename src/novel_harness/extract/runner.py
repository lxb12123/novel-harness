"""一次付费章节分析调用的幂等后台执行。"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from time import perf_counter

from ..db import Connection
from ..draft.provider import CompletionResult
from ..graph import ChapterText
from ..graph.sqlite_events import SqliteEventStore
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_store import SqliteStoryGraph
from ..ids import EntityType, new_id
from .analyze import parse_analysis
from .call_audit import record_model_call
from .control import (
    RUN_COLUMNS,
    AnalysisRequest,
    AuditedCompletion,
    ExtractionChapterNotFound,
    ExtractionRun,
    ExtractionRunError,
    ExtractionRunnerError,
    ExtractionRunNotFound,
    ExtractionRunStateError,
    ExtractionRunStatus,
    prompt_bytes,
    to_run,
)
from .models import RawChapterAnalysis
from .prompt import ANALYSIS_SCHEMA_VERSION
from .service import ExtractionReport, ExtractionService

__all__ = [
    "ExtractionChapterNotFound",
    "ExtractionRun",
    "ExtractionRunError",
    "ExtractionRunner",
    "ExtractionRunnerError",
    "ExtractionRunNotFound",
    "ExtractionRunStateError",
    "ExtractionRunStatus",
]


def _default_run_id(project_id: str) -> str:
    return new_id(EntityType.EXTRACTION_RUN, project_id)


def _default_call_id(project_id: str) -> str:
    return new_id(EntityType.CALL, project_id)


class ExtractionRunner:
    """每条连接各自持有，并用 CAS 保证一个 run 至多付一次调用。"""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        analyzer: Callable[[AnalysisRequest], CompletionResult],
        *,
        parser: Callable[[str], RawChapterAnalysis] = parse_analysis,
        run_id_factory: Callable[[str], str] = _default_run_id,
        call_id_factory: Callable[[str], str] = _default_call_id,
    ) -> None:
        self._connections = connection_factory
        self._analyzer = analyzer
        self._parser = parser
        self._new_run_id = run_id_factory
        self._new_call_id = call_id_factory

    def enqueue(
        self,
        project_id: str,
        chapter_number: int,
        *,
        force: bool = False,
    ) -> ExtractionRun:
        if isinstance(chapter_number, bool) or not isinstance(chapter_number, int):
            raise TypeError("chapter_number must be an integer")
        if chapter_number < 1:
            raise ValueError("chapter_number must be at least 1")
        conn = self._connections()
        try:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = conn.execute(
                """
                SELECT chapter_snapshot.id, chapter_snapshot.text
                FROM chapter
                JOIN chapter_snapshot
                  ON chapter_snapshot.chapter_id = chapter.id
                 AND chapter_snapshot.text_sha256 = chapter.text_sha256
                WHERE chapter.project_id = ? AND chapter.number = ?
                """,
                (project_id, chapter_number),
            ).fetchone()
            if snapshot is None:
                raise ExtractionChapterNotFound(
                    f"chapter {chapter_number} has no current snapshot in project {project_id}"
                )
            encoded_prompt = prompt_bytes(str(snapshot["text"]))
            prompt_hash = sha256(encoded_prompt).hexdigest()
            existing = conn.execute(
                f"""
                SELECT {RUN_COLUMNS}
                FROM extraction_run
                WHERE project_id = ? AND snapshot_id = ?
                  AND schema_version = ? AND prompt_hash = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    project_id,
                    snapshot["id"],
                    ANALYSIS_SCHEMA_VERSION,
                    prompt_hash,
                ),
            ).fetchone()
            if force and existing is not None and existing["status"] == "FAILED":
                # 「重跑本章」：把失败的 run 重置回 PENDING，清掉上次调用的链接。
                # 不删行、不新建行——DB 唯一键守住「同一章同一 prompt 一条 run」，
                # model_call 审计记录保留，新调用会重新挂接。
                conn.execute(
                    """
                    UPDATE extraction_run
                    SET status = 'PENDING', errors_json = '[]',
                        valid_event_count = 0, discarded_event_count = 0,
                        proposal_count = 0, model_call_id = NULL,
                        started_at = NULL, finished_at = NULL
                    WHERE id = ? AND status = 'FAILED'
                    """,
                    (existing["id"],),
                )
                existing = self._fetch_row(conn, existing["id"])
            if existing is None:
                run_id = self._new_run_id(project_id)
                conn.execute(
                    """
                    INSERT INTO extraction_run (
                        id, project_id, chapter_number, snapshot_id,
                        schema_version, prompt_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        project_id,
                        chapter_number,
                        snapshot["id"],
                        ANALYSIS_SCHEMA_VERSION,
                        prompt_hash,
                    ),
                )
                existing = self._fetch_row(conn, run_id)
            conn.commit()
            return to_run(existing)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, run_id: str) -> ExtractionRun:
        """只读一个 run：不抢占、不调用付费分析器。"""
        conn = self._connections()
        try:
            row = self._fetch_row(conn, run_id)
            if row is None:
                raise ExtractionRunNotFound(f"extraction run not found: {run_id}")
            return to_run(row)
        finally:
            conn.close()

    def run(self, run_id: str) -> ExtractionRun:
        conn = self._connections()
        try:
            claimed, claimed_here = self._claim(conn, run_id)
            if not claimed_here:
                return claimed
            chapter = self._immutable_chapter(conn, claimed)
            request = AnalysisRequest(chapter)
            if request.prompt_hash != claimed.prompt_hash:
                return self._mark_failed(
                    conn,
                    run_id,
                    ExtractionRunError(
                        code="prompt_drift",
                        message="chapter analysis prompt no longer matches the queued run",
                    ),
                )
            started = perf_counter()
            try:
                completion = self._analyzer(request)
                if not isinstance(completion, CompletionResult):
                    raise TypeError("analyzer must return CompletionResult")
            except Exception:
                return self._mark_failed(
                    conn,
                    run_id,
                    ExtractionRunError(
                        code="provider_failure",
                        message="chapter analysis provider failed",
                    ),
                )
            elapsed_ms = max(0, int((perf_counter() - started) * 1_000))
            try:
                audited = AuditedCompletion.from_result(completion)
                call_id = record_model_call(
                    conn,
                    claimed,
                    request,
                    audited,
                    elapsed_ms=elapsed_ms,
                    call_id_factory=self._new_call_id,
                )
            except Exception:
                return self._mark_failed(
                    conn,
                    run_id,
                    ExtractionRunError(
                        code="call_record_failure",
                        message="chapter analysis call could not be audited",
                    ),
                )
            try:
                analysis = self._parser(audited.text)
                if not isinstance(analysis, RawChapterAnalysis):
                    raise TypeError("parser must return RawChapterAnalysis")
            except Exception:
                return self._mark_failed(
                    conn,
                    run_id,
                    ExtractionRunError(
                        code="analysis_format",
                        message="chapter analysis was not valid schema JSON",
                    ),
                )
            try:
                return self._ingest_success(
                    conn,
                    claimed,
                    chapter,
                    analysis,
                    call_id=call_id,
                )
            except Exception:
                conn.rollback()
                return self._mark_failed(
                    conn,
                    run_id,
                    ExtractionRunError(
                        code="ingest_failure",
                        message="chapter analysis could not be ingested",
                    ),
                )
        finally:
            conn.close()

    def _claim(self, conn: Connection, run_id: str) -> tuple[ExtractionRun, bool]:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(conn, run_id)
            if row is None:
                raise ExtractionRunNotFound(f"extraction run not found: {run_id}")
            run = to_run(row)
            if run.status is not ExtractionRunStatus.PENDING:
                conn.commit()
                return run, False
            changed = conn.execute(
                """
                UPDATE extraction_run
                SET status = 'RUNNING',
                    started_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ? AND status = 'PENDING'
                """,
                (run_id,),
            )
            if changed.rowcount != 1:
                raise ExtractionRunStateError(f"could not claim pending run: {run_id}")
            claimed = self._fetch_row(conn, run_id)
            conn.commit()
            return to_run(claimed), True
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _immutable_chapter(conn: Connection, run: ExtractionRun) -> ChapterText:
        row = conn.execute(
            """
            SELECT chapter_snapshot.chapter_id, chapter_snapshot.text
            FROM chapter_snapshot
            JOIN chapter ON chapter.id = chapter_snapshot.chapter_id
            WHERE chapter_snapshot.id = ?
              AND chapter.project_id = ? AND chapter.number = ?
            """,
            (run.snapshot_id, run.project_id, run.chapter_number),
        ).fetchone()
        if row is None:
            raise ExtractionRunStateError(
                f"run snapshot no longer matches its project/chapter: {run.id}"
            )
        return ChapterText(
            chapter_id=row["chapter_id"],
            number=run.chapter_number,
            snapshot_id=run.snapshot_id,
            text=row["text"],
        )

    def _ingest_success(
        self,
        conn: Connection,
        run: ExtractionRun,
        chapter: ChapterText,
        analysis: RawChapterAnalysis,
        *,
        call_id: str,
    ) -> ExtractionRun:
        conn.execute("BEGIN IMMEDIATE")
        graph = SqliteStoryGraph(conn)
        report = ExtractionService(
            conn=conn,
            graph=graph,
            event_store=SqliteEventStore(conn),
            proposal_store=SqliteProposalStore(conn),
        ).ingest(
            run.project_id,
            chapter,
            analysis,
            prompt_hash=run.prompt_hash,
        )
        self._mark_succeeded(conn, run.id, call_id, report)
        row = self._fetch_row(conn, run.id)
        conn.commit()
        return to_run(row)

    @staticmethod
    def _mark_succeeded(
        conn: Connection,
        run_id: str,
        call_id: str,
        report: ExtractionReport,
    ) -> None:
        changed = conn.execute(
            """
            UPDATE extraction_run
            SET status = 'SUCCEEDED', errors_json = '[]',
                valid_event_count = ?, discarded_event_count = ?, proposal_count = ?,
                model_call_id = ?,
                finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND status = 'RUNNING' AND model_call_id = ?
            """,
            (
                report.valid_event_count,
                report.discarded_event_count,
                report.proposal_count,
                call_id,
                run_id,
                call_id,
            ),
        )
        if changed.rowcount != 1:
            raise ExtractionRunStateError(f"could not complete RUNNING run: {run_id}")

    def _mark_failed(
        self,
        conn: Connection,
        run_id: str,
        error: ExtractionRunError,
    ) -> ExtractionRun:
        errors_json = json.dumps(
            [error.model_dump(mode="json")],
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE extraction_run
                SET status = 'FAILED', errors_json = ?,
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ? AND status = 'RUNNING'
                """,
                (errors_json, run_id),
            )
            if changed.rowcount != 1:
                raise ExtractionRunStateError(f"could not fail RUNNING run: {run_id}")
            row = self._fetch_row(conn, run_id)
            conn.commit()
            return to_run(row)
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _fetch_row(conn: Connection, run_id: str):
        return conn.execute(
            f"SELECT {RUN_COLUMNS} FROM extraction_run WHERE id = ?",
            (run_id,),
        ).fetchone()
