"""只告警的总结核对服务（ADR 0030 / 计划 Task 10）。

**核对永远不能撤销总结、阻止使用或修改 Canon。** 它只产生一种副作用：
持久化一个可能冲突的 `system_notification`（`summary_mismatch`）。supported 只
解决旧的 OPEN，不新建。模型失败、JSON 不合法或来源已变，分别记 FAILED 或
SUPERSEDED，**不创建新通知**。

**通知单源化（2026-08-18 文档 §5）：** 冲突通知只在「作者动作落在总结上」时
冒出来——违反 `_current_summary_source == "author"`（即当前 head 总结是机器写 /
机器覆写的）绝不建通知。正文→总结方向（写与覆写）是自动对齐，永远安静。

── 它和 `checks/` 的关系 ──────────────────────────────────────────────────
`checks/` 是**正文验证器**：对不可变快照跑确定性规则，report 是功能闸门。
总结核对是**语义模型核对**（ADR 0005 的窄例外）——它不进入 checks/、不阻断
总结、不修改 Canon，输出的是结构化可定位的结果（不输出百分比置信度）。

── 设计摘要 ──────────────────────────────────────────────────────────────
outbox 行由写侧在 "current snapshot / summary head / event head / Canon
evidence fingerprint 改变" 的**同一事务**里插入（不变量 9）；本模块只负责
claim 并执行 outbox，绝不靠进程内 enqueue。每次真实 provider 出发前用
`begin_call` 落 RUNNING model_call + `summary_reconciliation_call` link；
成功/失败/崩溃分别 finalize（不变量 30）。
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as _dt
import hashlib
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .db import Connection
from .ids import EntityType, new_id
from .graph import TextAnchor

__all__ = [
    "ReconciliationFailure",
    "ReconciliationProvider",
    "SummaryReconciliationResult",
    "begin_reconciliation_run",
    "claim_next_outbox",
    "finalize_reconciliation_run",
    "reconciliation_source_hash",
    "reconciliation_subject_ready",
    "supersede_reconciliation_run",
]

_CHECKER_SCHEMA = "summary-reconciliation-v1"


def _iso_from_epoch(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%fZ"
    )


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReconciliationFailure(RuntimeError):
    """核对失败（provider 抛错 / 解析不了 / 落不了锚）。记 FAILED + 通知。"""


class SummaryReconciliationResult(BaseModel):
    """核对器只允许返回结构化、可定位的结果（§4.4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Literal["supported", "possible_conflict"]
    explanation: str
    anchors: tuple[TextAnchor, ...] = ()


class ReconciliationProvider(Protocol):
    """核对模型的契约。返回结构化结果，失败抛 `ReconciliationFailure`。"""

    def reconcile(self, subject_type: str, subject: object) -> SummaryReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class ReconcileTask:
    """一条已 claim 的 outbox 行的输入视图。"""

    outbox_id: str
    project_id: str
    subject_type: str
    subject_id: str
    chapter_number: int | None
    checked_against_snapshot_id: str
    source_generation: int | None
    source_sha256: str
    summary_sha256: str | None
    checker_schema: str
    checker_prompt_hash: str
    fencing_token: int


def reconciliation_source_hash(
    *,
    source_snapshot_id: str,
    subject_type: str,
    evidence=None,
) -> str:
    """`source_sha256` 的口径只有一份（§4.4）：

    - 滚动总结（chapter_summary）→ `chapter_snapshot.text_sha256`；
    - 待确认 / Canon 情节摘要 → 按 `evidence.id + evidence.quote_sha256 +
      evidence.chapter_snapshot_id` 排序后得到的稳定 hash。
    核对器不自行发明第三种来源指纹。
    """
    if subject_type == "chapter_summary":
        if evidence is not None:
            raise ValueError("chapter_summary 的来源指纹只接受 snapshot hash")
        return _sha256_hex(source_snapshot_id + "\x00chapter")
    if subject_type in ("proposal_event", "canon_event"):
        if evidence is None:
            raise ValueError("事件摘要的来源指纹需要事件证据集合")
        parts = []
        for item in evidence:
            qsha = getattr(item, "quote_sha256", None) or _sha256_hex(
                item.quote_text or ""
            )
            snap = getattr(item, "chapter_snapshot_id", None) or source_snapshot_id
            parts.append(f"{item.id}\x00{qsha}\x00{snap}")
        return _sha256_hex("\x01".join(sorted(parts)))
    raise ValueError(f"unknown subject_type: {subject_type}")


def reconciliation_subject_ready(
    conn: Connection,
    *,
    project_id: str,
    subject_type: str,
    subject_id: str,
    checked_against_snapshot_id: str,
    source_generation: int | None,
) -> bool:
    """subject 是否仍是当前可用的 ACTIVE/FRESH —— 不是就不调模型，只解决旧 OPEN。

    - chapter_summary → 该章当前 head 仍指向 subject（且非 tombstome）；
    - proposal_event / canon_event → event 仍 ACTIVE + FRESH。
    """
    if subject_type == "chapter_summary":
        row = conn.execute(
            """
            SELECT 1
              FROM chapter_summary_head h
              JOIN chapter_summary s ON s.id = h.current_summary_id
              JOIN chapter c ON c.id = h.chapter_id
             WHERE c.project_id = ? AND s.id = ?
               AND s.status = 'ACTIVE'
             LIMIT 1
            """,
            (project_id, subject_id),
        ).fetchone()
        return row is not None
    # story_event SQL 只住 graph 层（arch guard）；经 queries 做一次原子读。
    from .graph import queries

    return queries.event_reconciliation_ready(conn, project_id, subject_id)


def begin_reconciliation_run(
    conn: Connection,
    *,
    project_id: str,
    subject_type: str,
    subject_id: str,
    chapter_number: int | None,
    checked_against_snapshot_id: str,
    source_generation: int | None,
    source_sha256: str,
    summary_sha256: str | None,
    checker_prompt_hash: str,
) -> str:
    """插入一条 RUNNING 核对 run 并返回 run_id（**共享调用方的活跃事务**）。

    调用方先用 `extract.call_audit.begin_call` 开事务（provider 出发前），再把
    run 行和 `summary_reconciliation_call` link 一起插进来，一次提交（不变量 30）。
    本函数**不自己 BEGIN**：它必须在调用方的事务里跑。
    """
    run_id = new_id(EntityType.SUMMARY_RECONCILIATION_RUN, project_id)
    conn.execute(
        """
        INSERT INTO summary_reconciliation_run (
            id, project_id, subject_type, subject_id, chapter_number,
            checked_against_snapshot_id, source_generation, source_sha256,
            summary_sha256, checker_schema, checker_prompt_hash, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING')
        """,
        (
            run_id,
            project_id,
            subject_type,
            subject_id,
            chapter_number,
            checked_against_snapshot_id,
            source_generation,
            source_sha256,
            summary_sha256,
            _CHECKER_SCHEMA,
            checker_prompt_hash,
        ),
    )
    return run_id


def claim_next_outbox(
    conn: Connection,
    *,
    owner: str,
    ttl_seconds: float,
    now: float | None = None,
) -> ReconcileTask | None:
    """claim 一条 PENDING / 过期 RUNNING 的核对 outbox。

    与刷新 attempt 同一套 lease/fence 纪律（不变量 17）：claim 在事务中递增
    fencing token；只有过期 lease 可重抢。返回 None = 没有可做的。
    """
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
    expires_iso = _iso_from_epoch(now + ttl_seconds)
    ran_out = _iso_from_epoch(now)
    row = conn.execute(
        """
        SELECT id, project_id, subject_type, subject_id, chapter_number,
               checked_against_snapshot_id, source_generation, source_sha256,
               summary_sha256, checker_schema, checker_prompt_hash, fencing_token
          FROM summary_reconciliation_outbox
         WHERE status = 'PENDING'
            OR (status = 'RUNNING' AND lease_expires_at < ?)
         ORDER BY created_at, id
         LIMIT 1
        """,
        (ran_out,),
    ).fetchone()
    if row is None:
        return None
    fencing = conn.execute(
        "SELECT COALESCE(MAX(fencing_token), 0) + 1 AS n "
        "FROM summary_reconciliation_outbox WHERE project_id = ?",
        (row["project_id"],),
    ).fetchone()["n"]
    conn.execute(
        """
        UPDATE summary_reconciliation_outbox
           SET status = 'RUNNING', lease_owner = :owner,
               lease_expires_at = :expires, fencing_token = :fencing
         WHERE id = :id
        """,
        {
            "id": row["id"],
            "owner": owner,
            "expires": expires_iso,
            "fencing": int(fencing),
        },
    )
    conn.commit()
    return ReconcileTask(
        outbox_id=row["id"],
        project_id=row["project_id"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        chapter_number=row["chapter_number"],
        checked_against_snapshot_id=row["checked_against_snapshot_id"],
        source_generation=row["source_generation"],
        source_sha256=row["source_sha256"],
        summary_sha256=row["summary_sha256"],
        checker_schema=row["checker_schema"],
        checker_prompt_hash=row["checker_prompt_hash"],
        fencing_token=int(fencing),
    )


def _current_summary_source(
    conn: Connection, task: ReconcileTask
) -> Literal["model", "author"] | None:
    """subject 当前 head 总结是谁写的（通知单源化，文档 §5）。

    - `chapter_summary`（subject_id = chapter_id）→ 章总结 head 的 `source`;
    - `proposal_event` / `canon_event`（subject_id = event_id）→ 事件摘要
      head 的 `source`;
    - head 不在 / 不是 ACTIVE → `None`（没有「当前总结」可核对，自然不通知）。
    机器写或覆写会把 head 顶成 `model`，作者手动保存才是 `author`——这条判据
    就是「作者动作落在总结上」的落地：正文自动对齐永远安静，作者手改对不上才响。
    """
    if task.subject_type == "chapter_summary":
        row = conn.execute(
            """
            SELECT s.source
              FROM chapter_summary_head h
              JOIN chapter_summary s ON s.id = h.current_summary_id
              JOIN chapter c ON c.id = h.chapter_id
             WHERE c.project_id = :pid AND c.id = :subject
               AND s.status = 'ACTIVE'
             LIMIT 1
            """,
            {"pid": task.project_id, "subject": task.subject_id},
        ).fetchone()
        return None if row is None else str(row["source"])
    if task.subject_type in ("proposal_event", "canon_event"):
        row = conn.execute(
            """
            SELECT v.source
              FROM event_summary_head h
              JOIN event_summary_version v ON v.id = h.current_version_id
             WHERE h.event_id = :subject AND v.project_id = :pid
               AND v.status = 'ACTIVE'
             LIMIT 1
            """,
            {"pid": task.project_id, "subject": task.subject_id},
        ).fetchone()
        return None if row is None else str(row["source"])
    return None


def finalize_reconciliation_run(
    conn: Connection,
    task: ReconcileTask,
    result: SummaryReconciliationResult | None,
    *,
    run_id: str,
    status: Literal["SUCCEEDED", "FAILED", "SUPERSEDED"],
    message: str | None = None,
    possible_conflict: bool = False,
    summary_sha256: str | None = None,
    source_sha256: str | None = None,
    subject_title: str | None = None,
) -> None:
    """把核对 run 定稿，并据结果 upsert/resolve 系统通知（同事务）。

    - `possible_conflict` **且当前 head 总结是作者写的** → upsert OPEN
      （dedupe = kind+subject+summary+source，不变量 10；already
      IGNORED/RESOLVED 不重开）；
    - `possible_conflict` 但 head 已被机器写/覆写（`source == model`）→ **不建**
      通知（单源化：正文自动对齐安静），照常走下面的 SUCCEEDED 解决旧 OPEN；
    - `supported` → 解决该 subject 的旧 OPEN（IGNORED 保留作审计）；
    - FAILED / SUPERSEDED → 只记 run，不新建冲突通知。
    """
    conn.execute("BEGIN IMMEDIATE")
    verdict = (
        "possible_conflict"
        if possible_conflict and status == "SUCCEEDED"
        else "supported"
        if status == "SUCCEEDED"
        else None
    )
    conn.execute(
        """
        UPDATE summary_reconciliation_run
           SET status = :status, verdict = :verdict, explanation = :message,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE id = :run_id
        """,
        {
            "status": status,
            "verdict": verdict,
            "message": message,
            "run_id": run_id,
        },
    )
    conn.execute(
        "UPDATE summary_reconciliation_outbox SET status = :status "
        "WHERE id = :outbox_id AND fencing_token = :fence",
        {"status": status, "outbox_id": task.outbox_id, "fence": task.fencing_token},
    )
    authored = _current_summary_source(conn, task) == "author"
    if (
        possible_conflict
        and status == "SUCCEEDED"
        and summary_sha256
        and source_sha256
        and authored
    ):
        dedupe = _sha256_hex(f"{task.subject_type}\x00{task.subject_id}"
                             f"\x00{summary_sha256}\x00{source_sha256}")
        head = subject_title or ""
        conn.execute(
            """
            INSERT INTO system_notification (
                id, project_id, kind, status, subject_type, subject_id,
                chapter_number, title, summary_sha256, source_sha256, dedupe_key
            ) VALUES (?, ?, 'summary_mismatch', 'OPEN', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (project_id, dedupe_key) DO NOTHING
            """,
            (
                new_id(EntityType.SYSTEM_NOTIFICATION, task.project_id),
                task.project_id,
                task.subject_type,
                task.subject_id,
                task.chapter_number,
                head,
                summary_sha256,
                source_sha256,
                dedupe,
            ),
        )
    elif status == "SUCCEEDED":
        conn.execute(
            """
            UPDATE system_notification
               SET status = 'RESOLVED', resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE project_id = ? AND subject_type = ? AND subject_id = ?
               AND status = 'OPEN' AND kind = 'summary_mismatch'
            """,
            (task.project_id, task.subject_type, task.subject_id),
        )
    conn.commit()


def supersede_reconciliation_run(
    conn: Connection,
    outbox_id: str,
) -> None:
    """来源已变 / subject 已不在：只把 run 标 SUPERSEDED，不调模型、不通知。"""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        UPDATE summary_reconciliation_outbox SET status = 'SUPERSEDED'
        WHERE id = ?
        """,
        (outbox_id,),
    )
    conn.commit()


def enqueue_reconciliation_outbox(
    conn: Connection,
    *,
    project_id: str,
    subject_type: str,
    subject_id: str,
    chapter_number: int | None,
    checked_against_snapshot_id: str,
    source_generation: int | None,
    source_sha256: str,
    summary_sha256: str | None,
) -> str | None:
    """在当前 snapshot / summary head / event head 改变的**同一事务**里排核对。

    幂等（不变量 10 的 outbox 侧）：同一 (subject, summary hash, source hash)
    已存在 PENDING/RUNNING 就不重复排。返回 outbox id；None = 已排过。
    **调用方负责在同一事务里提交。** 不能提交后再内存 enqueue（不变量 9）。
    """
    row = conn.execute(
        """
        SELECT 1 FROM summary_reconciliation_outbox
         WHERE project_id = ? AND subject_type = ? AND subject_id = ?
           AND summary_sha256 IS ? AND source_sha256 = ?
           AND status IN ('PENDING','RUNNING')
         LIMIT 1
        """,
        (project_id, subject_type, subject_id, summary_sha256, source_sha256),
    ).fetchone()
    if row is not None:
        return None
    outbox_id = new_id(EntityType.SUMMARY_RECONCILIATION_OUTBOX, project_id)
    conn.execute(
        """
        INSERT INTO summary_reconciliation_outbox (
            id, project_id, subject_type, subject_id, chapter_number,
            checked_against_snapshot_id, source_generation, source_sha256,
            summary_sha256, checker_schema, checker_prompt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outbox_id,
            project_id,
            subject_type,
            subject_id,
            chapter_number,
            checked_against_snapshot_id,
            source_generation,
            source_sha256,
            summary_sha256,
            "summary-reconciliation-v1",
            "summary-reconciliation-v1",
        ),
    )
    return outbox_id
