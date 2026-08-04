"""M4 闭区间章段的只读接受度指标。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection

SQLITE_MAX_INTEGER = 2**63 - 1
MAX_CHAPTER_RANGE = 10_000


class MetricsProjectNotFound(LookupError):
    """请求的项目不存在。"""


class ChapterConflictMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    conflict_count: int = Field(ge=0)


class ExtractionRangeMetrics(BaseModel):
    """一个闭区间章段的预注册 M4 信号。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    first_chapter: int = Field(ge=1)
    last_chapter: int = Field(ge=1)
    total_runs: int = Field(ge=0)
    succeeded_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    valid_event_count: int = Field(ge=0)
    discarded_event_count: int = Field(ge=0)
    pending_proposals: int = Field(ge=0)
    resolved_proposals: int = Field(ge=0)
    accepted_reviews: int = Field(ge=0)
    edited_reviews: int = Field(ge=0)
    rejected_reviews: int = Field(ge=0)
    review_acceptance_rate: float | None = Field(default=None, ge=0, le=1)
    conflicts: tuple[ChapterConflictMetric, ...]
    max_conflicts_per_chapter: int = Field(ge=0)


def _chapter_number(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be at least 1")
    if value > SQLITE_MAX_INTEGER:
        raise ValueError(f"{field} exceeds SQLite's maximum integer")
    return value


def metrics_for_range(
    conn: Connection,
    project_id: str,
    first_chapter: int,
    last_chapter: int,
) -> ExtractionRangeMetrics:
    """度量抽取/审阅结果，不改动调用方拥有的事务。

    调用方没有事务时，临时只读事务给三条查询一份 SQLite 快照，随后回滚；
    调用方自有事务则原样保留。
    """

    first = _chapter_number(first_chapter, "first_chapter")
    last = _chapter_number(last_chapter, "last_chapter")
    if first > last:
        raise ValueError("first_chapter must not exceed last_chapter")
    if last - first + 1 > MAX_CHAPTER_RANGE:
        raise ValueError(
            f"chapter range may contain at most {MAX_CHAPTER_RANGE} chapters"
        )

    owns_snapshot = not conn.in_transaction
    if owns_snapshot:
        conn.execute("BEGIN")
    try:
        if conn.execute("SELECT 1 FROM project WHERE id = ?", (project_id,)).fetchone() is None:
            raise MetricsProjectNotFound(f"project not found: {project_id}")

        run = conn.execute(
            """
            SELECT
              COUNT(*) AS total_runs,
              COALESCE(SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END), 0)
                AS succeeded_runs,
              COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0)
                AS failed_runs,
              COALESCE(SUM(valid_event_count), 0) AS valid_event_count,
              COALESCE(SUM(discarded_event_count), 0) AS discarded_event_count
            FROM extraction_run
            WHERE project_id = ? AND chapter_number BETWEEN ? AND ?
            """,
            (project_id, first, last),
        ).fetchone()
        proposal = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0)
                AS pending_proposals,
              COALESCE(SUM(CASE WHEN status <> 'PENDING' THEN 1 ELSE 0 END), 0)
                AS resolved_proposals,
              COALESCE(SUM(CASE WHEN status = 'ACCEPTED' THEN 1 ELSE 0 END), 0)
                AS accepted_reviews,
              COALESCE(SUM(CASE WHEN status = 'EDITED' THEN 1 ELSE 0 END), 0)
                AS edited_reviews,
              COALESCE(SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END), 0)
                AS rejected_reviews
            FROM proposal_set
            WHERE project_id = ? AND chapter_number BETWEEN ? AND ?
              AND kind <> 'provisional_confirm'
            """,
            (project_id, first, last),
        ).fetchone()
        conflict_rows = conn.execute(
            """
            SELECT chapter_number, COALESCE(SUM(item_count), 0) AS conflict_count
            FROM proposal_set
            WHERE project_id = ? AND chapter_number BETWEEN ? AND ?
              AND kind = 'edge_conflict'
            GROUP BY chapter_number
            ORDER BY chapter_number
            """,
            (project_id, first, last),
        ).fetchall()
    finally:
        if owns_snapshot:
            conn.rollback()

    conflict_by_chapter = {
        int(row["chapter_number"]): int(row["conflict_count"]) for row in conflict_rows
    }
    conflicts = tuple(
        ChapterConflictMetric(
            chapter_number=chapter,
            conflict_count=conflict_by_chapter.get(chapter, 0),
        )
        for chapter in range(first, last + 1)
    )
    accepted = int(proposal["accepted_reviews"])
    edited = int(proposal["edited_reviews"])
    rejected = int(proposal["rejected_reviews"])
    reviewed = accepted + edited + rejected

    return ExtractionRangeMetrics(
        project_id=project_id,
        first_chapter=first,
        last_chapter=last,
        total_runs=int(run["total_runs"]),
        succeeded_runs=int(run["succeeded_runs"]),
        failed_runs=int(run["failed_runs"]),
        valid_event_count=int(run["valid_event_count"]),
        discarded_event_count=int(run["discarded_event_count"]),
        pending_proposals=int(proposal["pending_proposals"]),
        resolved_proposals=int(proposal["resolved_proposals"]),
        accepted_reviews=accepted,
        edited_reviews=edited,
        rejected_reviews=rejected,
        review_acceptance_rate=accepted / reviewed if reviewed else None,
        conflicts=conflicts,
        max_conflicts_per_chapter=max(
            (item.conflict_count for item in conflicts), default=0
        ),
    )


__all__ = [
    "ChapterConflictMetric",
    "ExtractionRangeMetrics",
    "MAX_CHAPTER_RANGE",
    "MetricsProjectNotFound",
    "SQLITE_MAX_INTEGER",
    "metrics_for_range",
]
