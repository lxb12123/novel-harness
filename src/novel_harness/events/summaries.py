"""事件摘要的版本选择与业务规则（ADR 0030 / Task 7）。

只表达「版本怎么选、谁有权改、什么时候 bump」；`event_summary_*` 的 SQL
全在 `graph/queries.py`（经 `SqliteEventStore`），本模块不写第二份事件过滤。

作者改 Canon 事件摘要：只新增 AUTHOR summary version，**不能把
`story_event.source` 改成 author**——否则正文换快照时旧机器事实会逃过退休
（不变量 21）。「事实最初由谁产生」和「当前摘要最后由谁编辑」是两个字段。
"""

from __future__ import annotations


from .. import decisions, project
from ..db import Connection
from ..events.models import EventSummaryVersion
from ..ids import EntityType, new_id
from ..graph.sqlite_events import SqliteEventStore


EVENT_SUMMARY_MAX_CHARS: int = 500


class EventSummaryNotFound(LookupError):
    """请求的 event 不存在或已被停火。"""


class EventSummaryTextRejected(ValueError):
    """作者交上来的那段摘要收不下。`str(exc)` 是给作者看的那句话。"""


class EventSummaryEditConflict(RuntimeError):
    """PATCH 的 `expected_version_id` 与当前 head 不一致（作者/机器已先行改动）。"""


def _default_version_id(project_id: str) -> str:
    return new_id(EntityType.SUMMARY, project_id)


def current_event_summary(
    conn: Connection, event_id: str, *, store: SqliteEventStore | None = None
) -> EventSummaryVersion | None:
    return (store or SqliteEventStore(conn)).event_summary_current(event_id)


def event_summary_history(
    conn: Connection, event_id: str, *, store: SqliteEventStore | None = None
) -> list[EventSummaryVersion]:
    return (store or SqliteEventStore(conn)).event_summary_history(event_id)


def create_event_summary_baseline(
    conn: Connection,
    *,
    project_id: str,
    event_id: str,
    summary: str,
    source_snapshot_id: str | None,
    evidence_sha256: str | None,
    store: SqliteEventStore | None = None,
) -> str:
    """事件出生时的机器摘要基线（抽取 ingest 调用，不调模型）。

    基线版本绑定 evidence 快照；head 从 NULL 切到基线。之后的作者编辑追加新版本。
    """
    store = store or SqliteEventStore(conn)
    version_id = _default_version_id(project_id)
    store.event_summary_insert(
        version_id=version_id,
        project_id=project_id,
        event_id=event_id,
        source_snapshot_id=source_snapshot_id,
        evidence_sha256=evidence_sha256,
        summary=summary,
        source="model",
        status="ACTIVE",
        replaces_version_id=None,
    )
    if not store.event_summary_switch_head(event_id, version_id, expected=None):
        raise EventSummaryEditConflict(
            f"event {event_id} 首次 baseline 的 expected-null head CAS 失败"
        )
    return version_id


def edit_event_summary(
    conn: Connection,
    *,
    project_id: str,
    event_id: str,
    text: str,
    expected_version_id: str | None,
    expected_canon_version: int | None = None,
    scope: str = "PROVISIONAL",
    bump_canon: bool = True,
    store: SqliteEventStore | None = None,
) -> EventSummaryVersion:
    """作者编辑事件摘要：只追加 AUTHOR 版本并切 head（proposal 仍 PENDING）。

    Canon 事件摘要会改变 Writer 实际看到的 Canon：同一事务 bump canon version
    并写 decision log（要求 `expected_canon_version`）。
    """
    body = text.strip()
    if not body:
        raise EventSummaryTextRejected("摘要不能是空的。")
    if len(body) > EVENT_SUMMARY_MAX_CHARS:
        raise EventSummaryTextRejected(
            f"摘要太长了（{len(body)} 字，最多 {EVENT_SUMMARY_MAX_CHARS} 字）。"
        )
    store = store or SqliteEventStore(conn)
    current = store.event_summary_current(event_id)
    if expected_version_id is not None:
        actual = current.id if current is not None else None
        if expected_version_id != actual:
            raise EventSummaryEditConflict(
                f"expected event summary head {expected_version_id!r}, current {actual!r}"
            )
    if current is not None and current.summary == body and current.source == "author":
        return current

    version_id = _default_version_id(project_id)
    store.event_summary_insert(
        version_id=version_id,
        project_id=project_id,
        event_id=event_id,
        source_snapshot_id=current.source_snapshot_id if current else None,
        evidence_sha256=current.evidence_sha256 if current else None,
        summary=body,
        source="author",
        status="ACTIVE",
        replaces_version_id=current.id if current else None,
    )
    if not store.event_summary_switch_head(
        event_id, version_id, expected=current.id if current else None
    ):
        raise EventSummaryEditConflict("event summary head CAS failed")

    # 021 / Task 10：event summary head 切换与「要核对这条新摘要」同一事务
    # （不变量 9）。`source_sha256` 是事件 evidence 的稳定指纹（§4.4），
    # 不是本模块自造的第三种来源哈希。
    from ..summary_reconciliation import enqueue_reconciliation_outbox

    basis = store.event_summary_job_basis(project_id, event_id)
    if basis is not None:
        enqueue_reconciliation_outbox(
            conn,
            project_id=project_id,
            subject_type="proposal_event" if scope == "PROVISIONAL" else "canon_event",
            subject_id=event_id,
            chapter_number=None,
            checked_against_snapshot_id=basis["chapter_snapshot_id"],
            source_generation=int(basis["snapshot_generation"] or 1),
            source_sha256=basis["evidence_sha256"]
            or _evidence_fingerprint(basis.get("quote_text") or ""),
            summary_sha256=version_id,
        )

    if scope == "CANON" and bump_canon:
        if expected_canon_version is None:
            raise EventSummaryEditConflict("Canon 摘要编辑必须带 expected_canon_version")
        project.compare_and_bump_canon_version(conn, project_id, expected_canon_version)
        decisions.append(
            conn,
            project_id=project_id,
            kind=decisions.DecisionKind.EVENT_SUMMARY_EDIT,
            decision=decisions.Verdict.ACCEPT,
            subject_name=event_id,
            chapter_number=None,
            payload={"event_id": event_id, "summary_sha256": version_id},
        )
    saved = store.event_summary_current(event_id)
    if saved is None:  # pragma: no cover
        raise RuntimeError("event summary insert is unreadable")
    return saved


def regenerate_event_summary(
    conn: Connection,
    *,
    project_id: str,
    event_id: str,
    trigger_key: str,
    store: SqliteEventStore | None = None,
) -> str:
    """事件摘要显式重新总结：创建持久 EVENT job，不直接调模型。

    同一事务递增 event head 的 machine intent、supersede 旧未完成 job、
    冻结 current head 与 evidence fingerprint——作者随后编辑时机器晚到只留 result。
    """
    store = store or SqliteEventStore(conn)
    row = store.event_summary_job_basis(project_id, event_id)
    if row is None:
        raise EventSummaryNotFound(f"event {event_id} 不存在或不在本项目")
    intent = conn.execute(
        """
        UPDATE event_summary_head
           SET machine_intent_seq = machine_intent_seq + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE event_id = ?
        RETURNING machine_intent_seq
        """,
        (event_id,),
    ).fetchone()["machine_intent_seq"]
    conn.execute(
        """
        UPDATE summary_generation_job SET status = 'SUPERSEDED'
         WHERE project_id = ? AND target_type = 'EVENT' AND event_id = ?
           AND status IN ('PENDING','RUNNING')
        """,
        (project_id, event_id),
    )
    job_id = _default_version_id(project_id)
    store.create_event_summary_job(
        job_id=job_id,
        project_id=project_id,
        event_id=event_id,
        source_snapshot_id=row["chapter_snapshot_id"],
        source_generation=int(row["snapshot_generation"] or 1),
        source_sha256=row["evidence_sha256"] or _evidence_fingerprint(row["quote_text"]),
        expected_head=row["current_version_id"],
        intent_seq=intent,
        trigger_key=trigger_key,
    )
    return job_id


def _evidence_fingerprint(quote_text: str | None) -> str:
    """按 evidence.id + quote_sha256 + chapter_snapshot_id 排序后稳定 hash 的口径
    （§4.4）：单一证据在这里退回 quote 的 sha256（完整口径随 Task 9 多证据扩展）。"""
    from ..decisions import quote_hash

    return quote_hash(quote_text or "")
