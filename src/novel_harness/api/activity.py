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

`cast` 落在这里的理由不同：它要读花名册（「这个称呼是不是只指他一个人」），而
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
from ..graph import AliasKind
from .deps import get_conn, get_store, load_project


router = APIRouter()

EntryId = Annotated[str, Path(min_length=1)]


def _endpoints(project_id: str, jump: ActivityJump) -> tuple[str, ...]:
    """这个坐标今天能被哪几条路由改。**全仓唯一一张 `target → 编辑入口` 的表。**

    - `KNOWLEDGE_CELL` → `corrections.correct_knowledge`
    - `EVENT_CAST` → `corrections.correct_event_cast`
    - `PROPOSAL` → 三条审阅路由（accept / reject / edit），**只在指名到某一条提案时给**
    - `CHAPTER` → 空：它是兜底坐标，明说「只能定位，今天没有编辑入口」

    最后一条不是偷懒。自动升上去的**边**就落在这一档：抽取只产
    `LOCATED_AT` / `HAS_STATE` / `RELATED_TO`，而 `corrections.py` 只改
    KNOWS↔BELIEVES 和事件名单——**今天没有任何路由能改一条自动生效的位置边**。
    这正是 [ADR 0020](../../docs/adr/0020-clean-extraction-auto-canon.md) 写在
    「什么条件下推翻本 ADR」里的那一条（「出现『作者改不回来』的形态」）。
    编一个按钮出来会让这个条件永远观测不到。
    """
    base = f"/api/projects/{project_id}"
    if jump.target is JumpTarget.KNOWLEDGE_CELL:
        return (f"{base}/canon/knowledge",)
    if jump.target is JumpTarget.EVENT_CAST and jump.event_id:
        return (f"{base}/canon/events/{jump.event_id}/cast",)
    if jump.target is JumpTarget.PROPOSAL and jump.proposal_id:
        return (
            f"{base}/proposals/{jump.proposal_id}/accept",
            f"{base}/proposals/{jump.proposal_id}/reject",
            f"{base}/proposals/{jump.proposal_id}/edit",
        )
    return ()


class _Surfaces:
    """花名册的一次性视图：node_id → 作者能认的称呼。

    **一个请求最多查一次**（`store.resolve(pid, None)` 是全项目一次索引扫描），
    因为一页日志里可能有几十行都指向认知矩阵，一行查一次就是几十次全表读。
    """

    def __init__(self, store: Any, project_id: str) -> None:
        self._store = store
        self._project_id = project_id
        self._by_node: dict[str, str] | None = None

    def of(self, node_id: str) -> str | None:
        """这个人的称呼。**歧义的一律不算**（同名两个人 → 谁都不给）。

        本名（`canonical`，`upsert_node` 建节点时写的那一条）优先，别名兜底。
        反过来会把化名摆到「只看：…」那一行上——而化名在这本书里可能正是
        「别人还不知道他是谁」的编码（ADR 0004），拿它当筛选条件读起来像另一个人。
        """
        if self._by_node is None:
            table: dict[str, str] = {}
            canonical: set[str] = set()
            for resolution in self._store.resolve(self._project_id, None):
                node = resolution.unique_node
                # unique_node 为 None ⇒ 这个称呼指向不止一个人。跳过它就是「不替作者挑」
                # （ADR 0004）：宁可这一行补不出来，也不能把矩阵指到另一个人身上。
                if node is None or node.id in canonical:
                    continue
                is_canonical = any(hit.kind is AliasKind.CANONICAL for hit in resolution.hits)
                if is_canonical or node.id not in table:
                    table[node.id] = resolution.surface
                if is_canonical:
                    canonical.add(node.id)
            self._by_node = table
        return self._by_node.get(node_id)


def _cast(surfaces: _Surfaces, jump: ActivityJump) -> tuple[str, ...]:
    """跳过去之后右栏那份在场里还要**多算上**谁。**只有认知矩阵那一档有值。**

    矩阵的行由本章正文推（ADR 0018），而 `valid_from` 由引语定（ADR 0006）——
    一句满是代词的声明会让坐标指向一章「他一次都没被点名」的正文，那一行不在表上，
    高亮和编辑入口一起落空。这里把那个人显式交出去，由 `_effective_cast` 的
    `include`（**只加不减**）把那一行加回来——**不是替换推导**：替换掉的话，
    右栏的写作提醒会按「只有他一个人在场」重算，禁令跟着少一批（ADR 0018 §3）。

    **`EVENT_CAST` 那一档故意没有**：它跳的是「已确认情节」那一格里的一份名单，
    不是矩阵的一行；给了坐标只会在右栏那几格里凭空多出一个谁也没要求过的人。
    `PROPOSAL` / `CHAPTER` 同理。

    查不到称呼（没登记过 / 全都有歧义）就返回空——那时退回今天的行为，
    界面照旧说「没有在这一章找到刚才那一格」。**编一个坐标出来才是错的那一侧。**
    """
    if jump.target is not JumpTarget.KNOWLEDGE_CELL or not jump.character_id:
        return ()
    surface = surfaces.of(jump.character_id)
    return (surface,) if surface else ()


def _with_jump(surfaces: _Surfaces, project_id: str, entry: ActivityEntry) -> ActivityEntry:
    if entry.jump is None:
        return entry
    jump = entry.jump.model_copy(
        update={
            "endpoints": _endpoints(project_id, entry.jump),
            "cast": _cast(surfaces, entry.jump),
        }
    )
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
    surfaces = _Surfaces(store, proj.id)
    return page.model_copy(
        update={"entries": tuple(_with_jump(surfaces, proj.id, e) for e in page.entries)}
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
        raise HTTPException(
            status_code=404,
            detail={"error": "activity_entry_not_found", "entry_id": entry_id},
        )
    return detail.model_copy(
        update={"entry": _with_jump(_Surfaces(store, proj.id), proj.id, detail.entry)}
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
