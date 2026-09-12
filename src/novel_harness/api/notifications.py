"""系统通知的薄 HTTP 壳（Task 10）。同别的 router：**只把服务包成 HTTP，不装业务。**

读端（list / count / detail）和写端（ignore / recheck）都经
`system_notifications` / `summary_reconciliation` 那一个服务——不在 `app.py`
再实现一套状态转换（计划 §6.4）。

`undo-toc-skip`（032）是这条纪律唯一一处需要**两个**服务的写端：`system_notifications`
（读 payload / 标 RESOLVED）之外还要 `importer.undo_toc_skip`（真的把文件挪回去）。
业务判断（撤销安不安全）仍然全在 `importer.py` 里，这一层只是把两次调用串起来。
"""

from __future__ import annotations

from pathlib import Path as FilePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from .. import importer
from ..db import Connection
from ..proposal_notifications import pending_proposal_notifications
from ..system_notifications import (
    SystemNotification,
    ignore_notification,
    list_open_notifications,
    notification_count,
    notification_payload,
    resolve_notification,
)
from .deps import get_conn, get_store, load_project
from .review import get_proposal_store

router = APIRouter()

NotificationId = Annotated[str, Path(min_length=1)]


def _merged_open_notifications(
    conn: Connection, project_id: str
) -> list[SystemNotification]:
    """真通知（`system_notification` 表）+ 现读现拼的待确认提案，按时间合成一条流。

    两边各自已经按 `created_at` 排过序，这里只做一次归并排序，不发明第二套
    「谁该排在前面」的判据——`SystemNotifications.tsx` 按 `kind` 分组时假定
    的是「同一档的条目挨着出现」，跟这两个来源先后合并的顺序无关（分组用的是
    首次出现位置的 `Map`，见组件里的注释）。 """
    proposal_store = get_proposal_store(conn)
    merged = [
        *list_open_notifications(conn, project_id),
        *pending_proposal_notifications(proposal_store, project_id),
    ]
    merged.sort(key=lambda item: item.created_at)
    return merged


@router.get("/api/projects/{project_id}/notifications", response_model=list[SystemNotification])
def notifications(
    project_id: str,
    status: Annotated[str | None, Query()] = "open",
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> list[SystemNotification]:
    """OPEN 通知列表（默认）。`status=ignored|resolved` 也给全量读。

    2026-08-31：待确认提案并进来了（不进 `system_notification` 表，见
    `proposal_notifications.py` 顶部那段说明），所以这条**不再单纯读一张表**。
    `status` 参数目前对提案那半没有意义——提案没有 IGNORED/RESOLVED 这两档，
    `.pending()` 本来就只返回未处理的；`status=ignored|resolved` 传进来时
    这半份数据仍然是空的（不是 bug，是提案压根没有那两个状态）。
    """
    return _merged_open_notifications(conn, proj.id)


@router.get("/api/projects/{project_id}/notifications/count")
def notifications_count(
    project_id: str,
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, int]:
    """右栏那个数字 badge。**只数 OPEN**（IGNORED/RESOLVED 是已处理的审计状态）。

    2026-08-31：待确认提案的数目也算进来了——badge 现在是「有多少件事等你看」，
    不是「有多少条 `system_notification` 表里的行」，跟 tab 底下真实会画出来
    的条目数对齐。
    """
    proposal_store = get_proposal_store(conn)
    return {
        "open": (
            notification_count(conn, proj.id)
            + len(pending_proposal_notifications(proposal_store, proj.id))
        )
    }


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


@router.post("/api/projects/{project_id}/notifications/{notification_id}/undo-toc-skip")
def notifications_undo_toc_skip(
    project_id: str,
    notification_id: NotificationId,
    store: Any = Depends(get_store),
    conn: Connection = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """撤销一次「目录跳过」：把丢掉的占位章插回原来的位置（032）。

    只对本项目**当前 OPEN 的** `import_toc_skipped` 通知生效——同 `notification_detail`
    的范围检查，比 `ignore`/`resolve` 那两条更严一格：这条会真的动文件，
    找错通知的代价比「忽略错了一条」大得多。

    业务判断全在 `importer.undo_toc_skip`：这本书自导入起变过 → 409，
    不猜、不半途插一部分。成功后把通知标 RESOLVED——它的任务完成了。
    """
    item = next(
        (i for i in list_open_notifications(conn, proj.id) if i.id == notification_id),
        None,
    )
    if item is None or item.kind != "import_toc_skipped":
        raise HTTPException(status_code=404, detail={"error": "notification_not_found"})

    payload = notification_payload(conn, notification_id)
    if payload is None:
        raise HTTPException(status_code=404, detail={"error": "notification_not_found"})

    skipped = [
        importer.SkippedTocEntry(position=position, raw_heading=raw_heading)
        for position, raw_heading in payload["skipped"]
    ]
    try:
        restored = importer.undo_toc_skip(
            store,
            conn,
            proj.id,
            FilePath(proj.root_path),
            skipped=skipped,
            expected_fingerprint=payload["fingerprint"],
        )
    except importer.TocSkipBookChanged as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "toc_skip_book_changed", "message": str(exc)},
        ) from exc

    resolve_notification(conn, notification_id)
    return {"id": notification_id, "status": "RESOLVED", "restored": restored}
