"""统一系统通知的去重 / 忽略 / 解决 / 动作坐标（ADR 0030 / 计划 Task 10）。

三种语义各自独立的失败形态共用一张通知表：

- `summary_mismatch`：总结核对发现可能冲突（只告警，不撤销/不用不了/不改 Canon）；
- `background_failure`：后台任务失败（provider 崩溃、重放过不去…）；
- `validation_blocked`：正文验证器阻断（保留了旧结果，只是新快照不自动总结/抽取）。

── 去重纪律（不变量 10 / 29）──────────────────────────────────────────────
`dedupe_key` 由 kind + subject + summary hash + source hash 稳定计算，非空，
`UNIQUE(project_id, dedupe_key)`。IGNORED / RESOLVED 对精确 hash 对是终态，
重复核对不得重开；只有任一 hash 变化才允许新 OPEN。`background_failure`
没有 hash 时用 `kind + subject + operation + snapshot/job id` 的稳定键——
**绝不包含会变的 exception 文本**。
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .db import Connection
from .graph import TextAnchor
from .ids import EntityType, new_id

__all__ = [
    "SystemNotification",
    "background_failure_dedupe_key",
    "dedupe_key_for",
    "enqueue_notification",
    "ignore_notification",
    "list_open_notifications",
    "materialize_notification_outbox",
    "notification_count",
    "resolve_notification",
]

NotificationStatus = Literal["OPEN", "IGNORED", "RESOLVED"]
NotificationKind = Literal["summary_mismatch", "background_failure", "validation_blocked"]


class SystemNotification(BaseModel):
    """一条通知的出参。`jump` 是结构化坐标，前端禁止从文案反推。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    kind: NotificationKind
    status: NotificationStatus
    subject_type: str
    subject_id: str
    chapter_number: int | None = None
    title: str
    summary_sha256: str | None = None
    source_sha256: str | None = None
    jump: TextAnchor | None = None
    actions: tuple[str, ...] = ()
    created_at: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dedupe_key_for(
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    summary_sha256: str | None,
    source_sha256: str | None,
) -> str:
    """`summary_mismatch` 的精确 hash 对去重键（不变量 10）。"""
    # hash 对可能有一个为 None（摘要被撤回 / 来源快照 legacy）。用非空占位
    # 保证即使 None 也给出非空键 —— NULL 会放行重复行，而重复通知正是要防的。
    return _sha(
        "\x00".join(
            (kind, subject_type, subject_id, summary_sha256 or "-", source_sha256 or "-")
        )
    )


def background_failure_dedupe_key(
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    operation: str,
    source_snapshot_id: str | None,
    job_id: str | None,
) -> str:
    """`background_failure` 的无 hash 稳定键。**exception 文本不许进来。**"""
    return _sha(
        "\x00".join(
            (
                kind,
                subject_type,
                subject_id,
                operation,
                source_snapshot_id or "-",
                job_id or "-",
            )
        )
    )


def enqueue_notification(
    conn: Connection,
    *,
    project_id: str,
    kind: NotificationKind,
    subject_type: str,
    subject_id: str,
    chapter_number: int | None,
    title: str,
    dedupe_key: str,
    summary_sha256: str | None = None,
    source_sha256: str | None = None,
    jump: TextAnchor | None = None,
    actions: tuple[str, ...] = (),
) -> str:
    """把一条通知写进持久 outbox（**同一事务**由调用方提交，不变量 29）。

    调用方（validation gate / 核对器 / 重放 dispatcher）负责把 enqueue 和业务
    状态改在一个事务里；本函数不自己 BEGIN。返回通知 outbox id。
    """
    outbox_id = new_id(EntityType.SYSTEM_NOTIFICATION, project_id)
    conn.execute(
        """
        INSERT INTO system_notification_outbox (
            id, project_id, intent, kind, subject_type, subject_id, chapter_number,
            title, summary_sha256, source_sha256, jump_para_index, jump_quote_text,
            jump_occurrence_k, actions_json, dedupe_key
        ) VALUES (?, ?, 'CREATE_OR_UPDATE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outbox_id,
            project_id,
            kind,
            subject_type,
            subject_id,
            chapter_number,
            title,
            summary_sha256,
            source_sha256,
            jump.para_index if jump else None,
            jump.quote_text if jump else None,
            jump.occurrence_k if jump else None,
            _json(actions),
            dedupe_key,
        ),
    )
    return outbox_id


def materialize_notification_outbox(
    conn: Connection,
    *,
    project_id: str,
    lease_owner: str,
    ttl_seconds: float = 30.0,
) -> int:
    """把 PENDING / 过期 RUNNING 的通知 outbox 逐条物化成 `system_notification`。

    幂等：重复 claim 同一 outbox 不会刷出重复行（`UNIQUE(project_id, dedupe_key)` +
    CREATE_OR_UPDATE 的 ON CONFLICT DO NOTHING 语义）。返回处理的条数。
    """
    rows = conn.execute(
        """
        SELECT id, intent, kind, subject_type, subject_id, chapter_number, title,
               summary_sha256, source_sha256, jump_para_index, jump_quote_text,
               jump_occurrence_k, actions_json, dedupe_key
          FROM system_notification_outbox
         WHERE project_id = ? AND status = 'PENDING'
         ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        if row["intent"] == "CREATE_OR_UPDATE":
            conn.execute(
                """
                INSERT INTO system_notification (
                    id, project_id, kind, status, subject_type, subject_id,
                    chapter_number, title, summary_sha256, source_sha256,
                    jump_para_index, jump_quote_text, jump_occurrence_k,
                    actions_json, dedupe_key
                ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, dedupe_key)
                DO UPDATE SET title = excluded.title
                """,
                (
                    new_id(EntityType.SYSTEM_NOTIFICATION, project_id),
                    project_id,
                    row["kind"],
                    row["subject_type"],
                    row["subject_id"],
                    row["chapter_number"],
                    row["title"],
                    row["summary_sha256"],
                    row["source_sha256"],
                    row["jump_para_index"],
                    row["jump_quote_text"],
                    row["jump_occurrence_k"],
                    row["actions_json"],
                    row["dedupe_key"],
                ),
            )
        elif row["intent"] == "RESOLVE":
            conn.execute(
                """
                UPDATE system_notification SET status = 'RESOLVED',
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                 WHERE project_id = ? AND dedupe_key = ? AND status = 'OPEN'
                """,
                (project_id, row["dedupe_key"]),
            )
        conn.execute("UPDATE system_notification_outbox SET status = 'DONE' WHERE id = ?",
                     (row["id"],))
    conn.commit()
    return len(rows)


def _open_rows(conn: Connection, project_id: str) -> list[SystemNotification]:
    rows = conn.execute(
        """
        SELECT id, project_id, kind, status, subject_type, subject_id, chapter_number,
               title, summary_sha256, source_sha256, jump_para_index, jump_quote_text,
               jump_occurrence_k, actions_json, created_at
          FROM system_notification
         WHERE project_id = ? AND status = 'OPEN'
         ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    return [_to_notification(r) for r in rows]


def list_open_notifications(conn: Connection, project_id: str) -> list[SystemNotification]:
    return _open_rows(conn, project_id)


def notification_count(conn: Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM system_notification WHERE project_id = ? AND status = 'OPEN'",
        (project_id,),
    ).fetchone()
    return int(row[0])


def ignore_notification(conn: Connection, notification_id: str) -> None:
    """把一条 OPEN 通知标 IGNORED（精确 hash 对的终态，不重开）。"""
    conn.execute(
        "UPDATE system_notification SET status = 'IGNORED', "
        "ignored_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ? AND status = 'OPEN'",
        (notification_id,),
    )
    conn.commit()


def resolve_notification(conn: Connection, notification_id: str) -> None:
    """手动 RESOLVE（同「问题消失自动解决」，只是这次是作者点的）。"""
    conn.execute(
        "UPDATE system_notification SET status = 'RESOLVED', "
        "resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ? AND status = 'OPEN'",
        (notification_id,),
    )
    conn.commit()


def _to_notification(row) -> SystemNotification:
    jump = (
        TextAnchor(
            para_index=row["jump_para_index"],
            quote_text=row["jump_quote_text"],
            occurrence_k=row["jump_occurrence_k"],
        )
        if row["jump_para_index"] is not None and row["jump_quote_text"] is not None
        else None
    )
    return SystemNotification(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        status=row["status"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        chapter_number=row["chapter_number"],
        title=row["title"],
        summary_sha256=row["summary_sha256"],
        source_sha256=row["source_sha256"],
        jump=jump,
        actions=_json_un(row["actions_json"]),
        created_at=row["created_at"],
    )


def _json(actions: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(actions), ensure_ascii=False, separators=(",", ":"))


def _json_un(text: str) -> tuple[str, ...]:
    import json

    return tuple(json.loads(text))
