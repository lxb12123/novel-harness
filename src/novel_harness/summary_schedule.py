"""全书总结自治调度（2026-08-18 文档 §2.2 / §4 / Step 2）。

30 分钟自治 = 智能路：不依赖任何点击，后台定期扫全书做对齐。本模块只回答
「哪些章该补/该覆写、按什么优先级」——**全部确定性查库，不调 LLM**（文档 §8）：
「哪章没总结 / 哪章不对齐」是数据库能直接回答的问题，LLM 只在真正把正文压缩成
总结的那一瞬间出场（`RollingSummarizer.ensure`，由 dispatcher 调）。

── 三态判定（§2.2）────────────────────────────────────────────────────────
对每一章（有当前正文快照的）：
  - `paired`   = 有 ACTIVE 总结 head **且** head 的 `source_sha256 == 当前快照
                 text_sha256`（意思配对：正文没再动过）；
  - `missing`  = 没有 ACTIVE 总结 head（没总结过 / 撤回 / head 被清）；
  - `stale`    = 有 ACTIVE 总结 head，但 `source_sha256 != 当前 text_sha256`
                 （正文改过、总结没跟着覆写 → 该覆写，文档 §2.2「不对齐」）。
「异常」（生成失败 / 卡住）不在本模块：那是 attempt/通知轨迹的事，`missing`
会自然覆盖它（重试等于再补一次）、`stale` 由指纹覆盖，队列纪律（§6）保证
单条失败不阻塞后续。

── 权重（§4）──────────────────────────────────────────────────────────────
写第 N 章时，往回 Δ = N - chapter 的章：
  - Δ ≤ WINDOW（默认 10）→ 权重 1.0，**必调度**（最近历史最重要）；
  - Δ > WINDOW → `max(0, 1 - Δ/CAP)`（默认 CAP=50）越远越低，省预算。
调度器按 `(权重, 章号)` 排序处理；缺与不对齐两类同走这一个权重。

── 出口（§2.3）────────────────────────────────────────────────────────────
本模块只决定「该不该 + 按什么序」，入队走既有 `ensure_refresh_coverage`
（幂等补缺，写 `chapter_refresh_attempt` = 系统记录）——不新造第二条管线。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .chapter_refresh import ensure_refresh_coverage
from .db import Connection

__all__ = [
    "SUMMARY_WINDOW",
    "SUMMARY_DECAY_CAP",
    "BookChapterStatus",
    "ChapterSummaryState",
    "book_summary_status",
    "chapter_summary_anomaly",
    "reconcile_anomaly_notifications",
    "scan_chapter_summary_state",
    "weight_for_chapter",
    "schedule_alignment",
]

SUMMARY_WINDOW: int = 10
"""写得近的这一段（Δ ≤ 10）权重满 1.0，回忆上下文最重要（文档 §4）。"""

SUMMARY_DECAY_CAP: int = 50
"""`max(0, 1 - Δ/CAP)` 的衰减分母（在近窗口之外起算）。"""


@dataclass(frozen=True, slots=True)
class ChapterSummaryState:
    chapter_number: int
    state: Literal["paired", "missing", "stale"]
    weight: float
    """`0.0` = 调度器这一轮不该碰它（权重衰减到 0 或它在未来章）。"""

    @property
    def needs_work(self) -> bool:
        return self.state != "paired" and self.weight > 0.0


def weight_for_chapter(*, draft_chapter: int, chapter_number: int) -> float:
    """往回 Δ 的权重（§4，确定性公式）。

    - `Δ == 0`（就是正在写的这章）→ 权重 0：写本章绝不用本章总结（天条），
      它也不该被这轮调度碰（防抖的同一条理由）；
    - `Δ < 0`（未来章）→ 权重 0：写作只调用「本章之前」的总结，未来章不在面；
    - `1 ≤ Δ ≤ WINDOW` → 权重 1.0，必调度（最近历史最重要）；
    - `Δ > WINDOW` → `max(0, 1 - (Δ-WINDOW)/CAP)` 越远越低，省预算。
    """
    delta = draft_chapter - chapter_number
    if delta <= 0:
        return 0.0
    if delta <= SUMMARY_WINDOW:
        return 1.0
    return max(0.0, 1.0 - (delta - SUMMARY_WINDOW) / SUMMARY_DECAY_CAP)


def scan_chapter_summary_state(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
) -> tuple[ChapterSummaryState, ...]:
    """确定性扫全书：每章三态 + 权重，按（权重, 章号）降序排好。

    `draft_chapter` = 正在写的章（权重以它为原点往回量）。焦点章防抖不在这层做
    （调度方用 §3 单独豁免），这里只给每章的状态和权重。
    """
    rows = conn.execute(
        """
        SELECT c.number AS number,
               src_snap.text_sha256 AS source_sha,
               cur_snap.text_sha256 AS text_sha
          FROM chapter c
          JOIN chapter_snapshot cur_snap
            ON cur_snap.chapter_id = c.id
           AND cur_snap.text_sha256 = c.text_sha256
          LEFT JOIN chapter_summary_head h ON h.chapter_id = c.id
          LEFT JOIN chapter_summary s
            ON s.id = h.current_summary_id
           AND s.status = 'ACTIVE'
          LEFT JOIN chapter_snapshot src_snap
            ON src_snap.id = s.source_snapshot_id
         WHERE c.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    out: list[ChapterSummaryState] = []
    for row in rows:
        num = int(row["number"])
        text_sha = str(row["text_sha"]) if row["text_sha"] else None
        src_sha = str(row["source_sha"]) if row["source_sha"] else None
        if src_sha is None:
            state: Literal["paired", "missing", "stale"] = "missing"
        elif text_sha is not None and src_sha != text_sha:
            state = "stale"
        else:
            state = "paired"
        out.append(
            ChapterSummaryState(
                chapter_number=num,
                state=state,
                weight=weight_for_chapter(
                    draft_chapter=draft_chapter, chapter_number=num
                ),
            )
        )
    # 先按（权重降序，章号升序）——“先处理谁”由权重 + 稳定序决定（同一轮内）。
    out.sort(key=lambda item: (-item.weight, item.chapter_number))
    return tuple(out)


def schedule_alignment(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
    ruleset_epoch: int,
    ruleset_hash: str,
    focused_chapter: int | None = None,
    limit: int = 20,
) -> dict[int, str]:
    """把本轮该补/该覆写的章送进既有对齐队列；返回 `{章号: 处理结论}`。

    - 只处理 `needs_work`（missing 或 stale 且权重 > 0）的章；
    - 焦点中的那一章豁免（§3 防抖：正写的章不动）；
    - 入队走 `ensure_refresh_coverage`（missing 与 stale 都带总结分支 →
      dispatcher 会 `ensure` 重新生成 = 覆写），结果写进 `chapter_refresh_attempt`
      （系统记录）。`limit` 是这一轮预算（权重最低的先被砍掉）。

    返回的结论是给 UI/日志看的，不是断言：`focused`/`paired`/`weight:0` 都是
    「这轮不碰它」的正当理由，不是失败。
    """
    states = scan_chapter_summary_state(
        conn, project_id, draft_chapter=draft_chapter
    )
    decisions: dict[int, str] = {}
    scheduled = 0
    for item in states:
        if item.chapter_number == focused_chapter:
            decisions[item.chapter_number] = "focused"
            continue
        if not item.needs_work:
            decisions[item.chapter_number] = (
                "paired" if item.state == "paired" else f"weight:{item.weight:.2f}"
            )
            continue
        if scheduled >= limit:
            decisions[item.chapter_number] = "budget"
            continue
        snap = conn.execute(
            """
            SELECT cs.id
              FROM chapter_snapshot cs
              JOIN chapter c ON c.id = cs.chapter_id
             WHERE c.project_id = ? AND c.number = ?
               AND cs.text_sha256 = c.text_sha256
             LIMIT 1
            """,
            (project_id, item.chapter_number),
        ).fetchone()
        if snap is None:
            decisions[item.chapter_number] = "no_snapshot"
            continue
        try:
            decision = ensure_refresh_coverage(
                conn,
                project_id=project_id,
                chapter_id=_chapter_id(conn, project_id, item.chapter_number),
                snapshot_id=str(snap["id"]),
                generation=_generation(conn, project_id, item.chapter_number),
                ruleset_epoch=ruleset_epoch,
                ruleset_hash=ruleset_hash,
            )
        except Exception:  # noqa: BLE001 —— 单章入队失败不阻塞整轮调度（§6）
            decisions[item.chapter_number] = "enqueue_failed"
            continue
        if decision.processing == "attention_required":
            # 同 basis 已有一个终态 FAILED/BLOCKED 的 coverage attempt：不自动重付
            # （Task 16 纪律），这章要作者手动处理。不算本轮入队预算。
            decisions[item.chapter_number] = "attention_required"
            continue
        decisions[item.chapter_number] = (
            "queued" if item.state == "missing" else "queued_overwrite"
        )
        scheduled += 1
    return decisions


def _chapter_id(conn: Connection, project_id: str, number: int) -> str:
    row = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, number),
    ).fetchone()
    if row is None:
        raise LookupError(f"chapter {number} not in project {project_id}")
    return str(row["id"])


def _generation(conn: Connection, project_id: str, number: int) -> int:
    """当前 generation（存在 `chapter.snapshot_generation`，不是快照表的列）。"""
    row = conn.execute(
        "SELECT snapshot_generation FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, number),
    ).fetchone()
    if row is None or row["snapshot_generation"] is None:
        return 1
    return int(row["snapshot_generation"])


@dataclass(frozen=True, slots=True)
class BookChapterStatus:
    """全书总结状态视图的一行（文档 §6 / Step 4）：逐章三态 + 异常标记 + 权重。

    `state` 只按**正文有没有、总结配不配**推：
    - `empty`   = 这一章还没有正文（没得总结）；
    - `missing` = 有正文、没有 ACTIVE 总结；
    - `stale`   = 有总结但来源指纹 != 当前正文（不对齐，该覆写）；
    - `paired`  = 有总结且指纹对得上（配对，不动）。
    `anomaly` = 该章最近一次总结 attempt 终态 FAILED/BLOCKED（§5 末行：异常标记，
    不阻塞其它章）。`weight` 是同一轮调度会给它的权重（§4 反馈）。
    """

    chapter_id: str
    chapter_number: int
    has_text: bool
    state: Literal["empty", "paired", "missing", "stale"]
    weight: float
    anomaly: bool


def chapter_summary_anomaly(
    conn: Connection, project_id: str, chapter_id: str
) -> bool:
    """这一章最近一次总结 attempt 是不是终态 FAILED/BLOCKED（§6 异常标记）。

    "卡住"（PENDING/RUNNING 过期未收敛）由 `recover_claimable` 在 dispatcher 侧
    重抢，不在这里算异常；这里只看**已经定性失败**的尝试。
    """
    row = conn.execute(
        """
        SELECT a.summary_state
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ? AND r.chapter_id = ?
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (project_id, chapter_id),
    ).fetchone()
    return (
        row is not None and str(row["summary_state"]) in ("FAILED", "BLOCKED")
    )


def _latest_attempt_states(
    conn: Connection, project_id: str
) -> dict[str, tuple[str, str]]:
    """每章最近一次 attempt 的 `(attempt_id, summary_state)`（一次查询，N+1 收敛）。"""
    rows = conn.execute(
        """
        SELECT r.chapter_id AS chapter_id, a.id AS attempt_id,
               a.summary_state AS s
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ?
           AND a.id = (
                SELECT a2.id
                  FROM chapter_refresh_attempt a2
                  JOIN chapter_refresh_run r2 ON r2.id = a2.run_id
                 WHERE r2.project_id = ? AND r2.chapter_id = r.chapter_id
                 ORDER BY a2.created_at DESC, a2.id DESC
                 LIMIT 1
           )
        """,
        (project_id, project_id),
    ).fetchall()
    return {
        str(row["chapter_id"]): (str(row["attempt_id"]), str(row["s"]))
        for row in rows
    }


def _latest_attempt(
    conn: Connection, project_id: str, chapter_id: str
) -> Any | None:
    """这一章最近一次 attempt（带 run 的 source_snapshot_id，供通知去重锚定）。"""
    return conn.execute(
        """
        SELECT a.id AS attempt_id, a.summary_state, r.source_snapshot_id
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ? AND r.chapter_id = ?
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (project_id, chapter_id),
    ).fetchone()


def book_summary_status(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
) -> tuple[BookChapterStatus, ...]:
    """全书总结状态视图（Step 4）：每一章的三态 + 权重 + 异常标记。

    与 `scan_chapter_summary_state` 的区别：这里是**给作者看全貌**的视图，包含
    没有正文的章（`empty`）；那是**给调度器用**的，只挑有正文的章。
    """
    rows = conn.execute(
        """
        SELECT c.id AS chapter_id, c.number AS number,
               (cur_snap.id IS NOT NULL) AS has_text,
               src_snap.text_sha256 AS source_sha,
               cur_snap.text_sha256 AS text_sha
          FROM chapter c
          LEFT JOIN chapter_snapshot cur_snap
            ON cur_snap.chapter_id = c.id
           AND cur_snap.text_sha256 = c.text_sha256
          LEFT JOIN chapter_summary_head h ON h.chapter_id = c.id
          LEFT JOIN chapter_summary s
            ON s.id = h.current_summary_id AND s.status = 'ACTIVE'
          LEFT JOIN chapter_snapshot src_snap ON src_snap.id = s.source_snapshot_id
         WHERE c.project_id = ?
         ORDER BY c.number
        """,
        (project_id,),
    ).fetchall()
    attempt_states = _latest_attempt_states(conn, project_id)
    out: list[BookChapterStatus] = []
    for row in rows:
        chapter_id = str(row["chapter_id"])
        num = int(row["number"])
        has_text = bool(row["has_text"])
        text_sha = str(row["text_sha"]) if row["text_sha"] else None
        src_sha = str(row["source_sha"]) if row["source_sha"] else None
        if not has_text:
            state: Literal["empty", "paired", "missing", "stale"] = "empty"
        elif src_sha is None:
            state = "missing"
        elif text_sha is not None and src_sha != text_sha:
            state = "stale"
        else:
            state = "paired"
        out.append(
            BookChapterStatus(
                chapter_id=chapter_id,
                chapter_number=num,
                has_text=has_text,
                state=state,
                weight=weight_for_chapter(
                    draft_chapter=draft_chapter, chapter_number=num
                ),
                anomaly=(
                    attempt_states.get(chapter_id, ("", ""))[1]
                    in ("FAILED", "BLOCKED")
                ),
            )
        )
    return tuple(out)


def reconcile_anomaly_notifications(
    conn: Connection,
    project_id: str,
    statuses: tuple[BookChapterStatus, ...],
) -> int:
    """把异常标记同步成 `background_failure` 通知（§5 末行 / §6 / Step 4）。

    - 异常章：按「章 + operation=summary + **失败的那条 attempt**」找最新一次失败
      attempt，用它的稳定键 upsert 一条 OPEN——同一失败重复扫不重开（不变量 10）；
      好转后旧 OPEN 被解决，将来**新的**失败（新 attempt = 新键）又能重新 OPEN；
    - 好转章：解决该章所有 OPEN 的 `background_failure`（本章目前只有总结失败会产
      生这个 kind；validation 走 `validation_blocked`，不受影响）。

    直接写 `system_notification`（同 `finalize_reconciliation_run` 的模式）；调用方
    （`autonomy_once`）在自己的事务里提交。返回本轮新建的条数。
    """
    from .ids import EntityType, new_id
    from .system_notifications import background_failure_dedupe_key

    created = 0
    for item in statuses:
        latest = _latest_attempt(conn, project_id, item.chapter_id)
        if latest is not None and latest["summary_state"] in ("FAILED", "BLOCKED"):
            dedupe = background_failure_dedupe_key(
                kind="background_failure",
                subject_type="chapter",
                subject_id=item.chapter_id,
                operation="summary",
                source_snapshot_id=latest["source_snapshot_id"],
                job_id=latest["attempt_id"],
            )
            row = conn.execute(
                """
                INSERT INTO system_notification (
                    id, project_id, kind, status, subject_type, subject_id,
                    chapter_number, title, dedupe_key
                ) VALUES (?, ?, 'background_failure', 'OPEN', 'chapter', ?, ?,
                          ?, ?)
                ON CONFLICT (project_id, dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    new_id(EntityType.SYSTEM_NOTIFICATION, project_id),
                    project_id,
                    item.chapter_id,
                    item.chapter_number,
                    f"第 {item.chapter_number} 章的总结生成异常，已跳过不阻塞其它章",
                    dedupe,
                ),
            ).fetchone()
            created += row is not None
        else:
            conn.execute(
                """
                UPDATE system_notification SET status = 'RESOLVED',
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                 WHERE project_id = ? AND kind = 'background_failure'
                   AND subject_type = 'chapter' AND subject_id = ?
                   AND status = 'OPEN'
                """,
                (project_id, item.chapter_id),
            )
    return created
