"""Idempotent background execution of one paid chapter-analysis call."""

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
from ..ids import EntityType, artifact_id, new_id
from .analyze import parse_analysis
from .control import (
    RUN_COLUMNS,
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
    # extraction_run is the persisted report of a background execution.
    return new_id(EntityType.REPORT, project_id)


def _default_call_id(project_id: str) -> str:
    return new_id(EntityType.CALL, project_id)


class ExtractionRunner:
    """Own each connection and use CAS so a run can pay for at most one call."""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        analyzer: Callable[[ChapterText], CompletionResult],
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

    def enqueue(self, project_id: str, chapter_number: int) -> ExtractionRun:
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
                """,
                (
                    project_id,
                    snapshot["id"],
                    ANALYSIS_SCHEMA_VERSION,
                    prompt_hash,
                ),
            ).fetchone()
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

    def run(self, run_id: str) -> ExtractionRun:
        conn = self._connections()
        try:
            claimed, claimed_here = self._claim(conn, run_id)
            if not claimed_here:
                return claimed
            chapter = self._immutable_chapter(conn, claimed)
            encoded_prompt = prompt_bytes(chapter.text)
            started = perf_counter()
            try:
                completion = self._analyzer(chapter)
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
            call_id = self._record_call(
                conn,
                claimed,
                completion,
                prompt_bytes=encoded_prompt,
                elapsed_ms=elapsed_ms,
            )
            try:
                analysis = self._parser(completion.text)
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

    def _record_call(
        self,
        conn: Connection,
        run: ExtractionRun,
        completion: CompletionResult,
        *,
        prompt_bytes: bytes,
        elapsed_ms: int,
    ) -> str:
        call_id = self._new_call_id(run.project_id)
        params_json = json.dumps(
            {
                "finish_reason": completion.finish_reason,
                "schema_version": run.schema_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        out_bytes = completion.text.encode("utf-8", errors="surrogatepass")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO model_call (
                    id, project_id, capability, model, params_json, prompt_hash,
                    in_artifact, out_artifact, tokens_in, tokens_out, ms
                ) VALUES (?, ?, 'extractor', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    run.project_id,
                    completion.model or "unknown",
                    params_json,
                    run.prompt_hash,
                    artifact_id(prompt_bytes),
                    artifact_id(out_bytes),
                    completion.prompt_tokens,
                    completion.completion_tokens,
                    elapsed_ms,
                ),
            )
            changed = conn.execute(
                """
                UPDATE extraction_run SET model_call_id = ?
                WHERE id = ? AND status = 'RUNNING' AND model_call_id IS NULL
                """,
                (call_id, run.id),
            )
            if changed.rowcount != 1:
                raise ExtractionRunStateError(
                    f"run stopped being RUNNING while recording call: {run.id}"
                )
            conn.commit()
            return call_id
        except BaseException:
            conn.rollback()
            raise

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
