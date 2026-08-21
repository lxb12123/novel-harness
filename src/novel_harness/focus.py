"""当前章焦点（025 / 2026-08-18 文档 §3 「当前章防抖」）。

「作者此刻正盯着哪一章」是全系统唯一的当前章信息来源。保存传的 chapter 是
「保存哪一章」，不等于作者的眼睛停在哪一章（他可能在 WPS 里改第 7 章、切去
第 10 章预览再切回）。焦点记录就是为了回答「作者现在在哪」这一个问题。

── 它不触发任何工作 ─────────────────────────────────────────────────────
写入端是换章 / 开书时打的一个免费心跳（只 upsert 一行 + 时间戳），绝不进
总结 / 抽取 / 付费。距离被删除的旧 autopilot（「换章→立刻对刚离开的章付费
总结」）只有一点相同：都是换章上报；工作量的差别是天壤——这里只记位置。

── 防抖语义（读端）──────────────────────────────────────────────────────
`is_focused(chapter)` = 焦点在那一章 **且心跳未过期**（TTL，默认 2 分钟）。
过期 = 人不在 / 换了浏览器标签页不管了 → 解除保护，调度可以碰那一章。
`focused_elsewhere()` = 焦点存在但在别的章（作者切走了）→ 旧章失去保护。
「回来不停队」不在这里表达：已入队的任务由队列自身的 lease/fence 决定，
本模块只回答「现在够不够格被调度」，不撤销任何已排队的动作。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import Connection

__all__ = [
    "FOCUS_TTL",
    "focus_current_chapter",
    "is_focused",
    "focused_on",
    "report_focus",
    "resolve_draft_origin",
]

FOCUS_TTL = timedelta(minutes=2)
"""心跳有效期：超过这么久没有新心跳，就当作作者不在这了（解除防抖）。"""


def report_focus(conn: Connection, project_id: str, chapter_number: int) -> None:
    """作者说「我现在在第 chapter_number 章」。免费心跳，只记位置。

    不校验章号是否存在（正文可能在磁盘上还没进库——开书时汇报的是「我要停在
    那一章」，不要求它在库里）。幂等 upsert，单行。
    """
    conn.execute(
        """
        INSERT INTO chapter_focus (project_id, chapter_number, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT (project_id) DO UPDATE SET
          chapter_number = excluded.chapter_number,
          updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (project_id, chapter_number),
    )


def focused_on(
    conn: Connection, project_id: str, *, now: datetime | None = None
) -> int | None:
    """当前有效聚焦的章号；没有或心跳过期 → None。"""
    row = conn.execute(
        "SELECT chapter_number, updated_at FROM chapter_focus WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    updated = _parse_iso(str(row["updated_at"]))
    if updated + FOCUS_TTL < now:
        return None
    return int(row["chapter_number"])


def is_focused(conn: Connection, project_id: str, chapter_number: int) -> bool:
    """这一章现在是不是「作者正盯着的那一章」且心跳未过期。"""
    return focused_on(conn, project_id) == chapter_number


def focus_current_chapter(conn: Connection, project_id: str) -> int | None:
    """等价于 `focused_on`——命名对齐「当前章」措辞，供调度器读取。"""
    return focused_on(conn, project_id)


def resolve_draft_origin(
    conn: Connection, project_id: str
) -> tuple[int, int | None]:
    """本轮调度/状态视图的坐标 `(draft_chapter, focused_chapter)`（文档 §3/§4）。

    - 有有效焦点：draft_chapter = 焦点章（权重从它往回量），focused_chapter =
      同一章（防抖豁免）——正写的章这轮不碰；
    - 无焦点（人走开 / 心跳过期）：以「前沿章号 + 1」为原点——这样全本旧章都
      落在权重公式的 Δ ≥ 1 过去侧按距离计权，没有任何章被 Δ=0 意外豁免。
    """
    focused = focused_on(conn, project_id)
    if focused is not None:
        return focused, focused
    row = conn.execute(
        "SELECT COALESCE(MAX(number), 0) + 1 FROM chapter WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row[0]), None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
