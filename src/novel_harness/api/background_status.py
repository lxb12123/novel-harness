"""右上那盏灯读的一行：这本书现在有没有活在跑、排了几章（2026-09-13）。

作者第一次用桌面版的原话：「一旦我切换到角色栏……再返回这个状态就丢失」「我都不知道
现在是成功了还是失败了」。后台整理（保存触发、30 分钟扫描、「分析本章」）从前只在
活动记录里留痕，屏幕上没有一处**当下**说得出「有东西正在跑」——一颗按钮的三态是它
自己的局部状态，换个 tab 就没了。这条路由把那件事变成一行可以每隔几秒问一次的事实，
顶栏那盏灯（`frontend/src/components/TopBar.tsx::StatusLight`）读它。

**只读、不花钱、不写任何东西。** 两张表：`chapter_refresh_attempt`（保存 / 扫描下的单，
判据借 `chapter_refresh.OUTSTANDING_WHERE`，和 dispatcher 领单用的是同一条）和
`extraction_run`（「分析本章」那颗按钮直接下的单）。「在跑」和「排着」分开报：
灯亮黄靠前者，后者只进悬浮那句话。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from ..chapter_refresh import OUTSTANDING_WHERE, iso_timestamp
from ..db import Connection
from .deps import get_conn, load_project, model_configured

router = APIRouter()


@router.get("/api/projects/{project_id}/background")
def background_status(
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    now = iso_timestamp(time.time())
    attempts = conn.execute(
        f"""
        SELECT c.number AS chapter,
               (a.lease_expires_at IS NOT NULL AND a.lease_expires_at > :now) AS live
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
          JOIN chapter c ON c.id = r.chapter_id
         WHERE r.project_id = :pid AND ({OUTSTANDING_WHERE})
        """,
        {"pid": proj.id, "now": now},
    ).fetchall()
    runs = conn.execute(
        """
        SELECT chapter_number AS chapter, status
          FROM extraction_run
         WHERE project_id = :pid AND status IN ('PENDING', 'RUNNING')
        """,
        {"pid": proj.id},
    ).fetchall()
    running = sorted(
        {int(row["chapter"]) for row in attempts if row["live"]}
        | {int(row["chapter"]) for row in runs if row["status"] == "RUNNING"}
    )
    queued = sorted(
        ({int(row["chapter"]) for row in attempts if not row["live"]}
         | {int(row["chapter"]) for row in runs if row["status"] == "PENDING"})
        - set(running)
    )
    return {"configured": model_configured(), "running": running, "queued": queued}
