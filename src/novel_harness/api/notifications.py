"""系统通知的薄 HTTP 壳（Task 10）。同别的 router：**只把服务包成 HTTP，不装业务。**

读端（list / count / detail）和写端（ignore / recheck）都经
`system_notifications` / `summary_reconciliation` 那一个服务——不在 `app.py`
再实现一套状态转换（计划 §6.4）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..db import Connection
from ..system_notifications import (
    SystemNotification,
    ignore_notification,
    list_open_notifications,
    notification_count,
    resolve_notification,
)
from .deps import get_conn, load_project

router = APIRouter()

NotificationId = Annotated[str, Path(min_length=1)]


@router.get("/api/projects/{project_id}/notifications", response_model=list[SystemNotification])
def notifications(
    project_id: str,
    status: Annotated[str | None, Query()] = "open",
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> list[SystemNotification]:
    """OPEN 通知列表（默认）。`status=ignored|resolved` 也给全量读。"""
    return list_open_notifications(conn, proj.id)


@router.get("/api/projects/{project_id}/notifications/count")
def notifications_count(
    project_id: str,
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, int]:
    """右栏那个数字 badge。**只数 OPEN**（IGNORED/RESOLVED 是已处理的审计状态）。"""
    return {"open": notification_count(conn, proj.id)}


@router.get("/api/projects/{project_id}/notifications/{notification_id}")
def notification_detail(
    project_id: str,
    notification_id: NotificationId,
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> SystemNotification:
    """单条通知的完整坐标（前端按 `jump` / `actions` 导航，不从文案反推）。"""
    for item in list_open_notifications(conn, proj.id):
        if item.id == notification_id:
            return item
    raise HTTPException(
        status_code=404, detail={"error": "notification_not_found"}
    )


@router.post("/api/projects/{project_id}/notifications/{notification_id}/ignore")
def notifications_ignore(
    project_id: str,
    notification_id: NotificationId,
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, str]:
    """忽略当前 hash 对（正文或总结任一变化都允许再次提醒）。"""
    ignore_notification(conn, notification_id)
    return {"id": notification_id, "status": "IGNORED"}


@router.post("/api/projects/{project_id}/notifications/{notification_id}/resolve")
def notifications_resolve(
    project_id: str,
    notification_id: NotificationId,
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, str]:
    """手动 RESOLVE（问题其实已经看清了）。"""
    resolve_notification(conn, notification_id)
    return {"id": notification_id, "status": "RESOLVED"}
