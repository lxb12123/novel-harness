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
from typing import Literal

from .chapter_refresh import ensure_refresh_coverage
from .db import Connection

__all__ = [
    "SUMMARY_WINDOW",
    "SUMMARY_DECAY_CAP",
    "ChapterSummaryState",
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
            ensure_refresh_coverage(
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
