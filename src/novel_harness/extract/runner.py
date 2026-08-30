"""一次付费章节分析调用的幂等后台执行。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final
from hashlib import sha256
import json
from dataclasses import dataclass
from time import perf_counter

from ..db import Connection
from ..draft.provider import CompletionResult, ProviderFailureKind
from ..graph import ChapterText
from ..graph.sqlite_events import SqliteEventStore
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_store import SqliteStoryGraph
from ..ids import EntityType, new_id
from .analyze import parse_analysis
from .auto_canon import promote_clean_facts
from .call_audit import EXTRACTOR_CAPABILITY, record_failed_call, record_model_call
from .control import (
    RUN_COLUMNS,
    AnalysisRequest,
    AuditedCompletion,
    ExtractionChapterNotFound,
    ExtractionErrorCode,
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


@dataclass(frozen=True, slots=True)
class _FailedCall:
    """一次**没答上来**的调用，记账要的那几个字段。

    `model` 是 `None`：这一层拿不到 provider 那一侧最终用的模型名（那是 `analyzer`
    闭包里的事），而**编一个比留空更糟**——账上一个错的模型名会让「这本书是用哪个
    模型跑的」这个问题永远答错。`record_failed_call` 会把它落成 `'unknown'`。
    """

    project_id: str
    model: str | None
    prompt_hash: str | None
    prompt_bytes: bytes
    elapsed_ms: int
    error_type: str
    error_message: str
    chapter_number: int | None


# ── provider 失败的五档，**每一档一个字面量**（2026-08-25）────────────────────
#
# 写成一张字面量表而不是一次 `.get()` 拼装，是因为
# `tests/test_wording_guard.py::_runner_error_literals` 用 **AST** 扫这个文件里
# 每一处 `ExtractionRunError(code=枚举, message=字面量)`，再和枚举成员对齐。
# 拼装出来的码在那个扫描器眼里根本不存在 —— 于是「新加一种失败方式却忘了给它一句
# 中文」这件事会静默通过，而那正是这张表要拦的。
#
# ⚠️ `message` 是写给**维护者**的英文诊断，而且**必须是固定字面量**：
# `tests/test_extract_runner.py::test_provider_exception_fails_without_graph_writes_or_retry`
# 钉着「provider 那句原话不许进 `errors_json`」—— 那份 JSON 会整份发给浏览器，
# 而 provider 的错误文案里可能带着请求内容甚至 key 片段。
# **原话只进 `model_call.error_message`**（那一列全仓没有任何读端发给前端）。
_PROVIDER_ERRORS: Final[dict[str, ExtractionRunError]] = {
    ProviderFailureKind.AUTH: ExtractionRunError(
        code=ExtractionErrorCode.PROVIDER_AUTH,
        message="chapter analysis provider rejected the credentials",
    ),
    ProviderFailureKind.QUOTA: ExtractionRunError(
        code=ExtractionErrorCode.PROVIDER_QUOTA,
        message="chapter analysis provider reported no quota or balance",
    ),
    ProviderFailureKind.UNREACHABLE: ExtractionRunError(
        code=ExtractionErrorCode.PROVIDER_UNREACHABLE,
        message="chapter analysis provider could not be reached",
    ),
    ProviderFailureKind.UPSTREAM: ExtractionRunError(
        code=ExtractionErrorCode.PROVIDER_UPSTREAM,
        message="chapter analysis provider returned a server error",
    ),
}

_PROVIDER_UNKNOWN: Final = ExtractionRunError(
    code=ExtractionErrorCode.PROVIDER_FAILURE,
    message="chapter analysis provider failed",
)
"""**说不清是哪一档**。它同时是 2026-08-25 之前所有 provider 失败写的那个码，
所以老行读得回来（`errors_json` 是 append-only 的审计资产）。"""


def _provider_error(exc: BaseException) -> ExtractionRunError:
    """provider 异常 → 这一次该记哪个码。**分档在 provider 那一层，这里只查表。**

    判据是 HTTP 状态码（`draft.provider.ProviderFailureKind`，那儿写了为什么不读文案）。
    拿不到 `kind`（不是 `ProviderError`，比如 analyzer 自己抛了 TypeError）就落到
    `PROVIDER_FAILURE` —— **一句诚实的「说不清」，不是一句听起来很具体的假话**。
    """
    return _PROVIDER_ERRORS.get(getattr(exc, "kind", None), _PROVIDER_UNKNOWN)


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
                SELECT chapter_snapshot.id, chapter_snapshot.text,
                       chapter.id AS chapter_id, chapter.snapshot_generation
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
            # 021 / Task 9：run 创建时冻结当前 generation + ruleset basis。
            # 完成时若这些已不是当前值 → SUPERSEDED（S1→S2→S1 的 ABA 只能靠
            # generation 认出来，snapshot/hash 会再次相同）。
            generation = int(snapshot["snapshot_generation"])
            ruleset = conn.execute(
                "SELECT epoch, ruleset_hash FROM validation_ruleset_state WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if ruleset is None:
                raise ExtractionRunStateError(
                    f"project {project_id} 没有 ruleset state —— 迁移/创建损坏"
                )
            ruleset_epoch, ruleset_hash = int(ruleset["epoch"]), str(ruleset["ruleset_hash"])
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
            if existing is not None and (
                existing["source_generation"] != generation
                or existing["required_ruleset_epoch"] != ruleset_epoch
                or existing["required_ruleset_hash"] != ruleset_hash
            ):
                # 同一 content-addressed 正文，但 basis 已经往前走（S1→S2→S1 的
                # 第三轮 S1 / 规则集 A→B→A）：旧 basis 的 run 不能冒充新 basis
                # 的结果。把它标 SUPERSEDED（审计保留），为当前 basis 建新 run。
                conn.execute(
                    "UPDATE extraction_run SET status = 'SUPERSEDED' WHERE id = ?",
                    (existing["id"],),
                )
                existing = None
            if existing is None:
                run_id = self._new_run_id(project_id)
                fencing = conn.execute(
                    "SELECT COALESCE(MAX(fencing_token), 0) + 1 AS next "
                    "FROM extraction_run WHERE project_id = ?",
                    (project_id,),
                ).fetchone()["next"]
                conn.execute(
                    """
                    INSERT INTO extraction_run (
                        id, project_id, chapter_number, snapshot_id,
                        schema_version, prompt_hash, source_generation,
                        required_ruleset_epoch, required_ruleset_hash, fencing_token
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        project_id,
                        chapter_number,
                        snapshot["id"],
                        ANALYSIS_SCHEMA_VERSION,
                        prompt_hash,
                        generation,
                        ruleset_epoch,
                        ruleset_hash,
                        int(fencing),
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
                        code=ExtractionErrorCode.PROMPT_DRIFT,
                        message="chapter analysis prompt no longer matches the queued run",
                    ),
                )
            if not self._basis_current(conn, claimed):
                # 021 / Task 9：run 创建后正文 generation 或 ruleset 已经往前走。
                # 晚到结果永远不能 ingest / 建提案 / auto-Canon（S1→S2→S1 的 ABA
                # 只能靠 generation 认出来）。model_call 与 run 历史照旧保留。
                return self._mark_superseded(
                    conn,
                    run_id,
                    "run 创建后正文已经换过版本或规则集已更新，结果不再适用",
                )
            started = perf_counter()
            try:
                completion = self._analyzer(request)
                if not isinstance(completion, CompletionResult):
                    raise TypeError("analyzer must return CompletionResult")
            except Exception as exc:
                # **没答上来的那一次也要记一行**（2026-08-25）：`model_call` 那两列
                # `error_type` / `error_message` 在这条路上一次都没填过，于是作者
                # 每一次失败的尝试在账上都不存在——而失败的调用照样可能计费。
                # 记账和标 run 在**同一个事务**里（`_mark_failed` 收口）。
                return self._mark_failed(
                    conn,
                    run_id,
                    _provider_error(exc),
                    failed_call=_FailedCall(
                        project_id=claimed.project_id,
                        model=None,
                        prompt_hash=claimed.prompt_hash,
                        prompt_bytes=request.prompt_bytes,
                        elapsed_ms=max(0, int((perf_counter() - started) * 1_000)),
                        error_type=str(getattr(exc, "kind", "") or type(exc).__name__),
                        error_message=str(exc)[:2000],
                        chapter_number=claimed.chapter_number,
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
                        code=ExtractionErrorCode.CALL_RECORD_FAILURE,
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
                        code=ExtractionErrorCode.ANALYSIS_FORMAT,
                        message="chapter analysis was not valid schema JSON",
                    ),
                )
            # 模型调用期间正文又变了：调用已经花了钱，但结果不能进库（审计保留）。
            if not self._basis_current(conn, claimed):
                return self._mark_superseded(
                    conn,
                    run_id,
                    "模型调用期间正文又换了版本或规则集已更新，结果不再适用",
                )
            try:
                succeeded, report = self._ingest_success(
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
                        code=ExtractionErrorCode.INGEST_FAILURE,
                        message="chapter analysis could not be ingested",
                    ),
                )
            # 自动升 CANON 在业务事务 commit **之后**，且**在上面那个 try 之外**：
            # `_transaction` 要求无外层事务，而升不上去绝不该把已经抽完、已经付过钱的
            # run 标成 FAILED（`promote_clean_facts` 自己 fail-safe，见该模块 docstring）。
            # 021 / Task 9：promotion 前**再次**确认 basis 仍 current——晚到结果
            # 即使侥幸过了 ingest 也不能复活 Canon。
            if self._basis_current(conn, claimed):
                from ..chapter_refresh import activate_extraction_application

                activate_extraction_application(
                    conn,
                    project_id=claimed.project_id,
                    chapter_id=chapter.chapter_id,
                    snapshot_id=claimed.snapshot_id,
                    generation=claimed.source_generation or 1,
                    analysis_run_id=claimed.id,
                    ruleset_epoch=claimed.required_ruleset_epoch,
                    ruleset_hash=claimed.required_ruleset_hash,
                )
                promote_clean_facts(conn, claimed.project_id, report)
            return succeeded
        finally:
            conn.close()

    def _basis_current(self, conn: Connection, run: ExtractionRun) -> bool:
        """run 的冻结 basis（snapshot/generation/ruleset）是否仍是当前值（021 / Task 9）。

        判据是 **generation + ruleset epoch/hash**，不是 snapshot/hash 等值：
        S1(g1)→S2(g2)→S1(g3) 时 snapshot/hash 会再次相同，只有 generation 不同。
        冻结字段为空 = legacy run（021 迁移前的行），无法核对时**按当前处理**——
        迁移回填只可能发生在旧库上，而旧库没有 generation 语义可对照。
        """
        row = conn.execute(
            """
            SELECT chapter.snapshot_generation, chapter.text_sha256,
                   chapter_snapshot.id AS snapshot_id
              FROM chapter
              JOIN chapter_snapshot
                ON chapter_snapshot.chapter_id = chapter.id
               AND chapter_snapshot.text_sha256 = chapter.text_sha256
             WHERE chapter.project_id = ? AND chapter.number = ?
            """,
            (run.project_id, run.chapter_number),
        ).fetchone()
        if row is None:
            return False
        if run.source_generation is not None and row["snapshot_generation"] != run.source_generation:
            return False
        if run.required_ruleset_epoch is not None:
            ruleset = conn.execute(
                "SELECT epoch, ruleset_hash FROM validation_ruleset_state WHERE project_id = ?",
                (run.project_id,),
            ).fetchone()
            if (
                ruleset is None
                or int(ruleset["epoch"]) != run.required_ruleset_epoch
                or str(ruleset["ruleset_hash"]) != (run.required_ruleset_hash or "")
            ):
                return False
        return True

    def _mark_superseded(self, conn: Connection, run_id: str, reason: str) -> ExtractionRun:
        """晚到的 run 标 SUPERSEDED：只留审计，不 ingest、不建提案、不 auto-Canon。"""
        errors_json = json.dumps(
            [ExtractionRunError(code="superseded", message=reason).model_dump(mode="json")],
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE extraction_run
                SET status = 'SUPERSEDED', errors_json = ?,
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ? AND status = 'RUNNING'
                """,
                (errors_json, run_id),
            )
            if changed.rowcount != 1:
                raise ExtractionRunStateError(f"could not supersede RUNNING run: {run_id}")
            row = self._fetch_row(conn, run_id)
            conn.commit()
            return to_run(row)
        except BaseException:
            conn.rollback()
            raise

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
    ) -> tuple[ExtractionRun, ExtractionReport]:
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
        # 021 / Task 9：规范 analysis JSON 落库（严格 Pydantic 校验后的形状），
        # 供别名纠正后确定性重放；不保存不可验证的自由文本。
        conn.execute(
            """
            INSERT OR REPLACE INTO extraction_analysis (
                run_id, project_id, snapshot_id, schema_version, analysis_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.project_id,
                run.snapshot_id,
                ANALYSIS_SCHEMA_VERSION,
                analysis.model_dump_json(),
            ),
        )
        self._mark_succeeded(conn, run.id, call_id, report)
        _notify_if_nothing_survived(conn, run, report)
        row = self._fetch_row(conn, run.id)
        conn.commit()
        return to_run(row), report

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
        *,
        failed_call: _FailedCall | None = None,
    ) -> ExtractionRun:
        """把 run 标 FAILED。给了 `failed_call` 就**在同一个事务里**补一行账。

        同一个事务不是洁癖：崩在两步之间的话，要么留下一条没有 run 的孤儿账，
        要么留下一次「失败了但账上没有」——而后者正是这一刀要修的东西。
        """
        errors_json = json.dumps(
            [error.model_dump(mode="json")],
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            if failed_call is not None:
                record_failed_call(
                    conn,
                    project_id=failed_call.project_id,
                    capability=EXTRACTOR_CAPABILITY,
                    model=failed_call.model,
                    prompt_hash=failed_call.prompt_hash,
                    prompt_bytes=failed_call.prompt_bytes,
                    elapsed_ms=failed_call.elapsed_ms,
                    error_type=failed_call.error_type,
                    error_message=failed_call.error_message,
                    chapter_number=failed_call.chapter_number,
                    call_id_factory=self._new_call_id,
                )
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


# ══════════════════════════════════════════════════════════════════════════
# 全丢了不许静默报成功（2026-08-23，027）
# ══════════════════════════════════════════════════════════════════════════

_NO_PARTICIPANTS: Final = "event has no resolvable participants"
"""`extract/service.py` 里那条唯一的事件丢弃理由的原文。**比对字面量是有意的**：
它是那一侧的出参，改了措辞这儿就该跟着改——而跟不上时症状是通知少说一句话，
不是崩，所以下面用的是「命中才敢下这个结论」的写法。"""


def _notify_if_nothing_survived(
    conn: Connection, run: ExtractionRun, report: ExtractionReport
) -> None:
    """这一章整理完了、模型也答了，**但一件都没留下** —— 得让作者知道。

    ── 这是 2026-08-23 在作者 158 章真书上撞出来的哑告警 ──────────────────

        章    模型抽到的事件   引擎留下的   丢掉的      run 状态
        1          12            0          12      SUCCEEDED / errors=[]
        2          11            0          11      SUCCEEDED / errors=[]
        158        12            0          12      SUCCEEDED / errors=[]

    整本书的图谱因此是空的（人物 0 / 边 0 / 事件 0 / 证据 0），而**没有任何一处
    告诉过作者**。丢弃条件只有一条：事件里的人在角色册里认不出来 ⇒ 整条丢；
    而角色册空着，所以下一章接着全丢——**它是个死锁，而且是静默的**。

    ── 判据为什么是「valid==0 且 discarded>0」，不是「valid==0」 ───────────

    **「模型明明抽到了，我们一件都没留住」才是异常**；产出为零本身不是。

    ⚠️ `valid==0 且 discarded==0` 今天**不可达**：`RawChapterAnalysis.events` 的下限
    是 1，所以「这一章本来就没有事件」（写景、独白）在那一层压根表达不出来——模型只能
    硬编一件事出来。那个洞是另一件事，这儿这一半条件是**为它准备的**：真放开下限那天，
    没有产出可言的一章不该跟着报一条通知。

    ── 不阻断 ────────────────────────────────────────────────────────────

    走 027 那一档，它不在 `BLOCKING_KINDS` 里：总结和抽取两支该跑照跑。
    这一次没失败，只是产出为零——挂 `background_failure` 会让作者去查一个
    不存在的故障。
    """
    if report.valid_event_count or not report.discarded_event_count:
        return
    from ..system_notifications import enqueue_extraction_yielded_nothing

    lost = report.discarded_event_count
    # 只有当丢弃理由**真的**是「认不出人」时才敢这么说。理由换了别的（将来多一档
    # 丢弃条件）就退回中性措辞——宁可少说一句，也不给作者指一个错方向。
    # **发这个布尔值，不发挑好的那句话**（国际化第四批 Phase B）：中文/英文用哪句
    # "为什么"、"还有几条待确认"要不要出现，是前端 `backendMessages.ts` 模板函数
    # 的分支逻辑，后端只送事实。
    unresolved = sum(
        reason.kind == "event" and _NO_PARTICIPANTS in reason.detail
        for reason in report.discarded
    )
    enqueue_extraction_yielded_nothing(
        conn,
        project_id=run.project_id,
        snapshot_id=run.snapshot_id,
        chapter_number=run.chapter_number,
        title_code="extraction_yielded_nothing_title",
        title_params={
            "lost": lost,
            "unresolved": unresolved == lost,
            "proposal_count": report.proposal_count,
        },
    )
