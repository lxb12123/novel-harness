"""活动日志的薄 HTTP 壳（ADR 0020 的「可查」）。

三条路由：折叠行一页、某一条的展开详情、底栏那一格的运行 + 花销汇总。
业务全在 `novel_harness/activity.py`，这里只做「装载 → 调用 → 回吐 Pydantic」。

── 壳在这里多干两件事：把 `jump` 补上 endpoints 和 cast ──────────────────

引擎算出的 `ActivityJump` 只有坐标（跳到哪一格 / 哪条事件 / 哪条提案），**没有路由**
——路由表是壳的知识，引擎不该知道 HTTP 长什么样（同 `app.py` 第一行那条「只把引擎
函数包成 HTTP，不装业务」）。所以这里有唯一一张 `target → 编辑路由` 的表。

它存在的理由不是方便，是**让「不许发明一个还不存在的编辑目标」可机器验证**：
`tests/test_activity.py` 把每条 jump 吐出来的路径拿去和 `app.routes` 对，
指向一条不存在的路由就红。空元组是一个断言（「今天这东西改不了」），不是没填。

`cast` 落在这里的理由不同：它要读角色册（「这个称呼是不是只指他一个人」），而
`novel_harness/activity.py` 的规矩是**本模块不读图**。见 `_cast()`。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..activity import (
    ActivityDetail,
    ActivityEntry,
    ActivityJump,
    ActivityPage,
    JumpTarget,
    RunsPanel,
    read_activity,
    read_entry,
    read_runs,
)
from ..db import Connection
from .deps import get_conn, get_store, load_project


router = APIRouter()

EntryId = Annotated[str, Path(min_length=1)]


def _endpoints(project_id: str, jump: ActivityJump) -> tuple[str, ...]:
    """这个坐标今天能被哪几条路由改。**全仓唯一一张 `target → 编辑入口` 的表。**

        - `EVENT_CAST` → `corrections.correct_event_cast`
    - `PROPOSAL` → 三条审阅路由（accept / reject / edit），**只在指名到某一条提案时给**
    - `SUMMARY` → 章节总结那一个资源（同一条路径四个动作：读 / 生成 / 改 / 撤回）
    - `EXTRACTION_RETRY` → 重跑那一章的整理
    - `CANON_EDGE` → `canon/edges/{edge_id}`（Task 8：自动升上去的地点/状态/关系边
      现在有真实修改 / 撤回 / 改归属入口）
    - `CHAPTER` → 空：它是兜底坐标，明说「只能定位，今天没有编辑入口」

    这张表出的是**路径**，不带查询串 ──────────────────────────────────────
    重跑那一条真打的时候要带 `?force=true`（没有它，`enqueue` 见到那条已经失败的 run
    就原样还回来，**接口 202、屏幕没反应**——比没有按钮更糟）。参数留给调用方带，
    理由是这张表只回答「有没有这条路」，而
    `tests/test_activity.py::test_every_jump_endpoint_resolves_to_a_real_route`
    比的是路由表里的路径。两头别各写一份：
    `tests/test_activity.py::test_a_failed_run_offers_a_real_retry_that_actually_reruns_it`
    从这里拿到路径真打一次（还带一个「不带 force」的探针），
    `frontend/src/components/ActivityLog.test.tsx` 那侧扫的是请求 URL 以它开头
    **且**带上了 `force`。
    """
    base = f"/api/projects/{project_id}"
    if jump.target is JumpTarget.EVENT_CAST and jump.event_id:
        return (f"{base}/canon/events/{jump.event_id}/cast",)
    if jump.target is JumpTarget.PROPOSAL and jump.proposal_id:
        return (
            f"{base}/proposals/{jump.proposal_id}/accept",
            f"{base}/proposals/{jump.proposal_id}/reject",
            f"{base}/proposals/{jump.proposal_id}/edit",
        )
    if jump.target is JumpTarget.SUMMARY and jump.chapter_number is not None:
        return (f"{base}/chapters/{jump.chapter_number}/summary",)
    if jump.target is JumpTarget.EXTRACTION_RETRY and jump.chapter_number is not None:
        return (f"{base}/chapters/{jump.chapter_number}/extract",)
    if jump.target is JumpTarget.CANON_EDGE and jump.edge_id:
        return (f"{base}/canon/edges/{jump.edge_id}",)
    return ()
def _with_jump(project_id: str, entry: ActivityEntry) -> ActivityEntry:
    if entry.jump is None:
        return entry
    jump = entry.jump.model_copy(update={"endpoints": _endpoints(project_id, entry.jump)})
    return entry.model_copy(update={"jump": jump})


@router.get("/api/projects/{project_id}/activity", response_model=ActivityPage)
def activity(
    actor: Annotated[str | None, Query(description="按 actor 过滤，今天是 author / system")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(description="上一页返回的 next_cursor，原样回传")] = None,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    store: Any = Depends(get_store),
) -> ActivityPage:
    """日志页的折叠行，新的在前。

    `actors` 里的计数**不受 `actor` 过滤影响**：它要回答的正是「我筛掉了多少」
    （ADR 0020：作者点过的 30 次会被淹没在几千条 system 行里）。

    游标形状不对 → 引擎抛 `ValueError` → 全局 handler 映成 422，**不静默回到第一页**。
    """
    page = read_activity(conn, proj.id, actor=actor, limit=limit, cursor=cursor)
    return page.model_copy(
        update={"entries": tuple(_with_jump(proj.id, e) for e in page.entries)}
    )


@router.get("/api/projects/{project_id}/activity/{entry_id}", response_model=ActivityDetail)
def activity_detail(
    entry_id: EntryId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    store: Any = Depends(get_store),
) -> ActivityDetail:
    """展开某一条：跑了什么、结果是什么、花了多少。

    404 有确切含义（这一条不在，或者不属于这本书），和 501「这个能力还没做」分得开。
    """
    detail = read_entry(conn, proj.id, entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail={"error": "activity_entry_not_found"})
    return detail.model_copy(
        update={"entry": _with_jump(proj.id, detail.entry)}
    )


@router.get("/api/projects/{project_id}/runs", response_model=RunsPanel)
def runs(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> RunsPanel:
    """底栏「最近运行 / Token / 成本」。

    **这条路由 2026-08-10 之前是 501，理由是「`model_call` 表今天是空的」——那句话
    早就过期了**：M4 的后台抽取和滚动总结都在记账（`extract/call_audit.py`）。

    `totals.cost` 今天恒为 `null` 而不是 `0.0`，因为 `model_call.cost` 至今没有写入方
    （BYOK 之下引擎不知道作者签的是什么单价）。`priced_calls` 把这个「零」的理由
    一起发出去，前端才分得开「没花钱」和「没记账」（§10 约束 8）。
    """
    return read_runs(conn, proj.id, limit=limit)
