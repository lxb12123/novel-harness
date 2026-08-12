"""滚动总结：幂等的后台章节摘要生成与读取。

摘要不是作者确认的事实（不进 decision_log），进写作 prompt 时明确标注
「机器摘要，仅背景」。同一章同一 prompt 只付一次调用，由 DB 唯一键兜底。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
import json
from time import perf_counter
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..extract.call_audit import record_call
from ..extract.control import AuditedCompletion
from ..graph import ChapterText
from ..ids import EntityType, new_id
from .provider import CompletionResult
from .summarize import SUMMARY_VERSION, SummaryMessage, build_summary_messages


ROLLING_WINDOW: Final = 30
"""写第 X 章时最多带上的旧章节摘要数（只覆盖「近八章事件窗口」之前的章节）。"""


class SummaryChapterNotFound(LookupError):
    """请求的章节没有当前不可变快照。"""


class SummaryGenerationError(RuntimeError):
    """总结器返回了空文本。"""


class ChapterSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    chapter_number: int = Field(ge=1)
    summary: str = Field(min_length=1)
    schema_version: str
    prompt_hash: str
    model_call_id: str | None = None
    created_at: str


class ChapterSummaryStatus(BaseModel):
    """一章在滚动总结上的状态。**三种「没有」必须分得开**（ARCHITECTURE §10 约束 8）。

    ``has_text=False`` = 这一章还没有正文快照，压根没得总结；
    ``has_text=True`` 且 ``summary is None`` = 有正文、**没生成过**（要花钱，只由作者显式触发）；
    两者在界面上长成同一个「- 暂无」，作者就永远不知道自己少喂了什么给写作模型——
    而那正是 2026-08-06 盘点出来的病（`chapter_summary` 表恒空且没有任何东西提示）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    has_text: bool
    summary: str | None = None
    created_at: str | None = None


class SummaryStore:
    """chapter_summary 的只读仓储。"""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, project_id: str, chapter_number: int) -> ChapterSummary | None:
        row = self._conn.execute(
            """
            SELECT id, project_id, chapter_number, summary, schema_version,
                   prompt_hash, model_call_id, created_at
            FROM chapter_summary
            WHERE project_id = ? AND chapter_number = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (project_id, chapter_number),
        ).fetchone()
        return None if row is None else _row_to_summary(row)

    def for_range(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummary]:
        """返回闭区间内每章最新的一条摘要，按章号升序。"""
        rows = self._conn.execute(
            """
            SELECT summary.id, summary.project_id, summary.chapter_number,
                   summary.summary, summary.schema_version, summary.prompt_hash,
                   summary.model_call_id, summary.created_at
            FROM chapter_summary AS summary
            WHERE summary.project_id = ? AND summary.chapter_number BETWEEN ? AND ?
              AND NOT EXISTS (
                SELECT 1 FROM chapter_summary AS newer
                WHERE newer.project_id = summary.project_id
                  AND newer.chapter_number = summary.chapter_number
                  AND newer.rowid > summary.rowid
              )
            ORDER BY summary.chapter_number
            """,
            (project_id, first_chapter, last_chapter),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def coverage(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummaryStatus]:
        """闭区间内**每一章**的总结状态，按章号升序。

        和 ``for_range`` 的差别就是这个模块存在的理由：``for_range`` 只返回有的那些，
        「缺哪几章」得靠调用方自己拿区间去减——而没人会记得减。这里把缺的那些也物化出来，
        且分得开「没写」和「写了没总结」（见 ``ChapterSummaryStatus``）。

        ``last_chapter < first_chapter`` 返回空表而不是报错：写第 3 章时滚动总结窗口
        本来就是空的（近八章走事件记忆），那是**正常态**，不是作者输错了。
        """
        if first_chapter < 1:
            raise ValueError("章号区间的下界至少是 1")
        if last_chapter < first_chapter:
            return []
        rows = self._conn.execute(
            """
            SELECT chapter.number AS number
            FROM chapter
            JOIN chapter_snapshot
              ON chapter_snapshot.chapter_id = chapter.id
             AND chapter_snapshot.text_sha256 = chapter.text_sha256
            WHERE chapter.project_id = ? AND chapter.number BETWEEN ? AND ?
            """,
            (project_id, first_chapter, last_chapter),
        ).fetchall()
        # 判据和 `RollingSummarizer._chapter` 是同一条（当前快照存在才总结得了），
        # 否则界面会请作者去生成一份引擎当场会拒的东西。
        with_text = {int(row["number"]) for row in rows}
        summaries = {
            summary.chapter_number: summary
            for summary in self.for_range(project_id, first_chapter, last_chapter)
        }
        out: list[ChapterSummaryStatus] = []
        for number in range(first_chapter, last_chapter + 1):
            summary = summaries.get(number)
            out.append(
                ChapterSummaryStatus(
                    chapter_number=number,
                    has_text=number in with_text,
                    summary=None if summary is None else summary.summary,
                    created_at=None if summary is None else summary.created_at,
                )
            )
        return out


def _row_to_summary(row: Any) -> ChapterSummary:
    return ChapterSummary(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        chapter_number=int(row["chapter_number"]),
        summary=str(row["summary"]),
        schema_version=str(row["schema_version"]),
        prompt_hash=str(row["prompt_hash"]),
        model_call_id=(
            None if row["model_call_id"] is None else str(row["model_call_id"])
        ),
        created_at=str(row["created_at"]),
    )


def _default_summary_id(project_id: str) -> str:
    return new_id(EntityType.SUMMARY, project_id)


def _default_call_id(project_id: str) -> str:
    return new_id(EntityType.CALL, project_id)


def _messages_bytes(messages: list[SummaryMessage]) -> bytes:
    return json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """一次绑定 prompt 的总结请求；派生字段不能独立提供。"""

    chapter: ChapterText
    messages: tuple[SummaryMessage, ...] = field(init=False)
    prompt_bytes: bytes = field(init=False, repr=False)
    prompt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        messages = tuple(build_summary_messages(self.chapter.text))
        encoded = _messages_bytes(list(messages))
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "prompt_bytes", encoded)
        object.__setattr__(self, "prompt_hash", sha256(encoded).hexdigest())


class RollingSummarizer:
    """own 每条连接，靠 DB 唯一键保证「同一章同一 prompt 至多付一次」。"""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        analyzer: Callable[[SummaryRequest], CompletionResult],
        *,
        summary_id_factory: Callable[[str], str] = _default_summary_id,
        call_id_factory: Callable[[str], str] = _default_call_id,
    ) -> None:
        self._connections = connection_factory
        self._analyzer = analyzer
        self._new_summary_id = summary_id_factory
        self._new_call_id = call_id_factory

    def ensure(self, project_id: str, chapter_number: int) -> ChapterSummary:
        """幂等：已有摘要直接返回；否则付费生成并落库。"""
        conn = self._connections()
        try:
            chapter = self._chapter(conn, project_id, chapter_number)
            request = SummaryRequest(chapter)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id, project_id, chapter_number, summary, schema_version,
                       prompt_hash, model_call_id, created_at
                FROM chapter_summary
                WHERE project_id = ? AND chapter_number = ?
                  AND schema_version = ? AND prompt_hash = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (project_id, chapter_number, SUMMARY_VERSION, request.prompt_hash),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return _row_to_summary(existing)
            conn.commit()

            started = perf_counter()
            completion = self._analyzer(request)
            if not isinstance(completion, CompletionResult):
                raise TypeError("summarizer must return CompletionResult")
            audited = AuditedCompletion.from_result(completion)
            summary = audited.text.strip()
            if not summary:
                raise SummaryGenerationError("summarizer returned empty text")
            elapsed_ms = max(0, int((perf_counter() - started) * 1_000))

            try:
                call_id = record_call(
                    conn,
                    project_id=project_id,
                    capability="summarizer",
                    model=audited.model,
                    finish_reason=audited.finish_reason,
                    schema_version=SUMMARY_VERSION,
                    prompt_hash=request.prompt_hash,
                    prompt_bytes=request.prompt_bytes,
                    text=audited.text,
                    prompt_tokens=audited.prompt_tokens,
                    completion_tokens=audited.completion_tokens,
                    elapsed_ms=elapsed_ms,
                    call_id_factory=self._new_call_id,
                )
                summary_id = self._new_summary_id(project_id)
                conn.execute(
                    """
                    INSERT INTO chapter_summary (
                        id, project_id, chapter_number, summary,
                        schema_version, prompt_hash, model_call_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary_id,
                        project_id,
                        chapter_number,
                        summary,
                        SUMMARY_VERSION,
                        request.prompt_hash,
                        call_id,
                    ),
                )
                conn.commit()
                created = self._read(conn, project_id, chapter_number)
                if created is None:
                    raise RuntimeError("summary insert is unreadable")
                return created
            except Exception:
                conn.rollback()
                existing = self._read(conn, project_id, chapter_number)
                if existing is not None:
                    # 并发另一路已写入：唯一键竞态的赢家，幂等返回。
                    return existing
                raise
        finally:
            conn.close()

    def _chapter(
        self,
        conn: Connection,
        project_id: str,
        chapter_number: int,
    ) -> ChapterText:
        row = conn.execute(
            """
            SELECT chapter_snapshot.chapter_id, chapter.number,
                   chapter_snapshot.id, chapter_snapshot.text
            FROM chapter
            JOIN chapter_snapshot
              ON chapter_snapshot.chapter_id = chapter.id
             AND chapter_snapshot.text_sha256 = chapter.text_sha256
            WHERE chapter.project_id = ? AND chapter.number = ?
            """,
            (project_id, chapter_number),
        ).fetchone()
        if row is None:
            raise SummaryChapterNotFound(
                f"chapter {chapter_number} has no current snapshot in project {project_id}"
            )
        return ChapterText(
            chapter_id=str(row["chapter_id"]),
            number=int(row["number"]),
            snapshot_id=str(row["id"]),
            text=str(row["text"]),
        )

    def _read(
        self,
        conn: Connection,
        project_id: str,
        chapter_number: int,
    ) -> ChapterSummary | None:
        return SummaryStore(conn).get(project_id, chapter_number)


__all__ = [
    "ChapterSummary",
    "ChapterSummaryStatus",
    "ROLLING_WINDOW",
    "RollingSummarizer",
    "SummaryChapterNotFound",
    "SummaryGenerationError",
    "SummaryRequest",
    "SummaryStore",
]
