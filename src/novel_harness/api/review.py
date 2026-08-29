"""M4 提案审阅、被动确认，以及**改一条已经生效的事实**的薄 HTTP 壳。

路由只负责校验/装载/调用抽取服务并回吐 Pydantic 出参；这里不放 SQL 也不放业务规则。

`/canon/…` 那几条是「事后可查可改」的**改**：抽取直接生效之后，作者第一次看见那条事实时
它已经是 CANON，所以退路必须在 CANON 上，而不只在审阅队列里。业务在 `corrections.py`。

"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import project
from ..corrections import (
    CorrectionRefused,
    EventCastCorrection,
    FactNotFound,
    correct_event_cast,
)
from ..db import Connection
from ..events import (
    EventCastStore,
    EventStore,
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalObsolete,
    ProposalRecord,
    ProposalStore,
    ProposalValidationError,
)
from ..events.models import EventSummaryVersion
from ..events.summaries import (
    EventSummaryEditConflict,
    EventSummaryNotFound,
    EventSummaryTextRejected,
    current_event_summary,
    edit_event_summary,
    event_summary_history,
    regenerate_event_summary,
)
from ..extract.proposals import (
    ConfirmationConflict,
    ProposalAction,
    ProposalResolution,
    ProposalReview,
    ProposalShapeError,
    ProvisionalConfirmation,
    confirm_provisional_edges,
    confirm_provisional_events,
    hydrate_proposal_names,
    review_proposal,
)
from ..graph import EdgeType, GraphStore, NodeLabel
from ..graph.models import (
    CanonEdgeEditResult,
    CanonEdgeView,
    EdgeProps,
    StateDimView,
)
from ..graph.queries import fetch_node
from ..graph.queries import list_state_dims as _list_state_dims
from ..graph.store import CanonEdgeRefused
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from ..graph.sqlite_events import SqliteEventStore
from .deps import get_conn, get_event_store, get_store, load_project


router = APIRouter()


class LocationEdgeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["location"] = "location"
    character_id: str | None = None
    """改归属：把整条事实改到另一个 Character（省略 = 保持原主体）。"""
    location_id: str | None = None
    """改地点：目标 Location（省略 = 保持原地）。"""
    expected_canon_version: int


class StateEdgeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["state"] = "state"
    subject_id: str | None = None
    dim_node_id: str
    """挑的是 StateDim **节点**，不是 `dim_key` 字符串：多数维度没有键
    （2026-08-27 裁定），node id 才是它们唯一的身份。`/canon/state-dims` 给全量列表。"""
    value: str
    value_key: str | None = None
    expected_canon_version: int


class RelationEdgeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["relation"] = "relation"
    peer_id: str | None = None
    """改对端：替换关系另一端（省略 = 保持原对端）。"""
    display: str | None = None
    """关系显示值（存进 EdgeProps 的 `display` 额外字段）。"""
    expected_canon_version: int


CanonEdgeEdit = Annotated[
    Union[LocationEdgeEdit, StateEdgeEdit, RelationEdgeEdit],
    Field(discriminator="kind"),
]


@router.get("/api/projects/{project_id}/canon/edges/{edge_id}")
def get_canon_edge(
    edge_id: str,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> CanonEdgeView:
    """读取一条可纠错 Canon 边（§6.6）：只返回 allowlist 中的 current CANON。"""
    try:
        return store.canon_edge_view(proj.id, edge_id)
    except CanonEdgeRefused as exc:
        raise HTTPException(409, {"error": "canon_edge_refused", "message": str(exc)})


@router.get("/api/projects/{project_id}/canon/state-dims")
def get_state_dims(
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> list[StateDimView]:
    """这个项目里全部 StateDim 节点，给「维度」下拉框用（Task 8 补记）。

    **前端认 id，不认 `dim_key`**：多数维度没有机器键（2026-08-27 裁定），
    id 才是它们唯一稳定的身份——这份列表就是它们的花名册。
    """
    return [StateDimView.of(n) for n in _list_state_dims(conn, proj.id)]


@router.patch("/api/projects/{project_id}/canon/edges/{edge_id}")
def patch_canon_edge(
    edge_id: str,
    body: CanonEdgeEdit,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> CanonEdgeEditResult:
    """类型化修改/改归属一条 Canon 边（无章号字段，extra=forbid）。"""
    try:
        edge = store.canon_edge_view(proj.id, edge_id)
    except CanonEdgeRefused as exc:
        raise HTTPException(409, {"error": "canon_edge_refused", "message": str(exc)})
    new_src, new_dst, props = _canon_edge_target(conn, proj.id, edge, body)
    try:
        return store.edit_canon_edge(
            proj.id,
            edge_id,
            new_src=new_src,
            new_dst=new_dst,
            props=props,
            expected_canon_version=body.expected_canon_version,
        )
    except CanonEdgeRefused as exc:
        raise HTTPException(409, {"error": "canon_edge_refused", "message": str(exc)})
    except project.StaleBaseVersion as exc:
        raise HTTPException(
            409,
            {"error": "stale_canon_version", "params": {"expected": exc.expected, "current": exc.current}},
        )


@router.delete("/api/projects/{project_id}/canon/edges/{edge_id}")
def delete_canon_edge(
    edge_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> CanonEdgeEditResult:
    """软撤回一条 Canon 边（override tombstone；expected_canon_version 防旧表单）。"""
    row = conn.execute(
        "SELECT canon_version FROM project WHERE id = ?", (proj.id,)
    ).fetchone()
    expected = int(row["canon_version"])
    try:
        return store.retract_canon_edge(
            proj.id, edge_id, expected_canon_version=expected
        )
    except CanonEdgeRefused as exc:
        raise HTTPException(409, {"error": "canon_edge_refused", "message": str(exc)})
    except project.StaleBaseVersion as exc:
        raise HTTPException(
            409,
            {"error": "stale_canon_version", "params": {"expected": exc.expected, "current": exc.current}},
        )


def _canon_edge_target(
    conn: Connection, project_id: str, edge: CanonEdgeView, body: CanonEdgeEdit
) -> tuple[str, str, EdgeProps]:
    """把类型化请求译成 (new_src, new_dst, props)。kind 必须与 edge type 匹配。"""
    if body.kind == "location":
        if edge.edge_type is not EdgeType.LOCATED_AT:
            raise HTTPException(422, {"error": "bad_request", "message": "这条边不是位置边"})
        if body.character_id is None and body.location_id is None:
            raise HTTPException(422, {"error": "bad_request", "message": "至少要改一个目标"})
        return (
            body.character_id or edge.src,
            body.location_id or edge.dst,
            edge.props,
        )
    if body.kind == "state":
        if edge.edge_type is not EdgeType.HAS_STATE:
            raise HTTPException(422, {"error": "bad_request", "message": "这条边不是状态边"})
        dim_node = fetch_node(conn, project_id, body.dim_node_id)
        if dim_node is None or dim_node.label is not NodeLabel.STATE_DIM:
            raise HTTPException(422, {"error": "bad_request", "message": "这不是一个维度"})
        # `dim_key` 跟着目标节点走，不是作者/前端能自由填的字符串：health 节点
        # 自带 "health"，别的维度节点自带 None（2026-08-27 裁定）——挑哪个节点，
        # `dim_key` 就照那个节点的真身份写，绝不会凭空长出一个没人建过的键。
        return (
            body.subject_id or edge.src,
            dim_node.id,
            EdgeProps(dim_key=dim_node.props.dim_key, value=body.value, value_key=body.value_key),
        )
    if body.kind == "relation":
        if edge.edge_type is not EdgeType.RELATED_TO:
            raise HTTPException(422, {"error": "bad_request", "message": "这条边不是关系边"})
        if body.peer_id is not None:
            new_src, new_dst = sorted((edge.src, body.peer_id))
        else:
            new_src, new_dst = edge.src, edge.dst
        props = edge.props
        if body.display is not None:
            props = EdgeProps.model_validate({**edge.props.model_dump(), "display": body.display})
        return new_src, new_dst, props
    raise HTTPException(422, {"error": "bad_request", "message": "未知的编辑类型"})


class EventSummaryEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    expected_version_id: str | None = None
    expected_canon_version: int | None = None


@router.get("/api/projects/{project_id}/events/{event_id}/summary")
def get_event_summary(
    event_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> EventSummaryVersion | None:
    """一个事件的当前摘要版本（PROVISIONAL / CANON 共用）。"""
    version = current_event_summary(conn, event_id)
    if version is None:
        raise HTTPException(404, {"error": "event_summary_not_found"})
    return version


@router.get("/api/projects/{project_id}/events/{event_id}/summary/history")
def get_event_summary_history(
    event_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> list[EventSummaryVersion]:
    return event_summary_history(conn, event_id)


@router.patch("/api/projects/{project_id}/events/{event_id}/summary")
def patch_event_summary(
    event_id: str,
    body: EventSummaryEditBody,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> EventSummaryVersion:
    """作者编辑事件摘要：只追加 AUTHOR 版本并切 head，proposal 仍 PENDING。

    Canon 事件摘要改变 Writer 实际 Canon → 必须带 `expected_canon_version`，
    同一事务 bump canon version 并写 decision log（`events/summaries.py`）。
    """
    store = SqliteEventStore(conn)
    scope = store.event_information_scope(proj.id, event_id)
    if scope is None:
        raise HTTPException(404, {"error": "event_not_found"})
    try:
        return edit_event_summary(
            conn,
            project_id=proj.id,
            event_id=event_id,
            text=body.summary,
            expected_version_id=body.expected_version_id,
            expected_canon_version=body.expected_canon_version,
            scope=scope,
        )
    except EventSummaryTextRejected as exc:
        raise HTTPException(422, str(exc))
    except EventSummaryEditConflict as exc:
        raise HTTPException(409, str(exc))


@router.post("/api/projects/{project_id}/events/{event_id}/summary/regenerate")
def regenerate_event_summary_route(
    event_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """事件摘要显式重新总结：只创建持久 job（EVENT target），不同步调模型。"""
    from ..ids import EntityType, new_id

    try:
        job_id = regenerate_event_summary(
            conn,
            project_id=proj.id,
            event_id=event_id,
            trigger_key=f"event-regenerate:{new_id(EntityType.SUMMARY, proj.id)}",
        )
    except EventSummaryNotFound:
        raise HTTPException(404, {"error": "event_not_found"})
    conn.commit()
    return {"queued": True, "job_id": job_id}
ChapterNumber = Annotated[int, Path(ge=1)]
ProposalId = Annotated[str, Path(min_length=1)]
EventId = Annotated[str, Path(min_length=1)]
NodeId = Annotated[str, Field(min_length=1)]


def get_proposal_store(conn: Connection = Depends(get_conn)) -> SqliteProposalStore:
    """请求级提案读取器，与项目共用同一条连接。"""
    return SqliteProposalStore(conn)


def get_edge_review_store(
    conn: Connection = Depends(get_conn),
    store: GraphStore = Depends(get_store),
) -> SqliteEdgeReviewStore:
    """请求级窄读取器。**队列读端只用它的 `node_refs`**（id → 显示名，不出 `Node`）。"""
    return SqliteEdgeReviewStore(conn, store)


class ProvisionalConfirmRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_kind: Literal["event", "edge"]
    fact_ids: list[str] = Field(min_length=1)
    expected_canon_version: int = Field(ge=0)


class ProposalAcceptRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_canon_version: int = Field(ge=0)


class ProposalRejectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["reject", "bystander"]
    expected_canon_version: int = Field(ge=0)


class ProposalEditRequest(BaseModel):
    """接受这条提案，但先按作者改的来。三样至少改一样（`ProposalReview` 会拒空编辑）。

    和 `/canon/events/{id}/cast` 收的是同一种东西（绝对集合、`null` = 不动），
    **能力也一样**——两条路能力不一致的时候，作者会学会先 reject 再重来，
    而那正好丢掉了证据链。同样没有章号字段。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    edited_summary: str | None = None
    knower_ids: list[NodeId] | None = None
    participant_ids: list[NodeId] | None = None
    expected_canon_version: int = Field(ge=0)


class EventCastEditRequest(BaseModel):
    """改一条已生效事件的知情 / 在场名单。

    两个字段收的都是**绝对集合**（改完之后是这些人），`null` = 这一维不动。
    同样没有章号字段：新知情人的生效章只能是事件自己那一章。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    knower_ids: list[NodeId] | None = None
    participant_ids: list[NodeId] | None = None
    expected_canon_version: int = Field(ge=0)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "bad_request", "message": message})


def _conflict(error: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=409, detail={"error": error, **extra})


def _review_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProposalNotFound):
        return HTTPException(
            status_code=404, detail={"error": "proposal_not_found", "proposal_id": exc.args[0]}
        )
    if isinstance(exc, project.StaleBaseVersion):
        return _conflict(
            "stale_base_version",
            expected=exc.expected,
            current=exc.current,
            project_id=exc.project_id,
        )
    if isinstance(exc, ProposalAlreadyResolved):
        return _conflict("proposal_already_resolved")
    if isinstance(exc, ProposalObsolete):
        # 409 而不是 404：提案还在（历史可查），只是它锚的正文已经不是当前。
        # 作者该做的是**先看看新正文**，不是重发一次旧裁决。
        return _conflict("proposal_obsolete", message=str(exc))
    if isinstance(exc, ConfirmationConflict):
        return _conflict("confirmation_conflict", message=str(exc))
    if isinstance(exc, ProposalShapeError | ProposalValidationError):
        return _bad_request(str(exc))
    raise exc


def _correction_error(exc: Exception) -> HTTPException:
    """改正层的三类失败翻成 HTTP。**不注册全局 handler**：这三个异常只有这两条路由抛，
    而全局 handler 是给「任何路由都可能撞上」的那种错准备的。"""
    if isinstance(exc, FactNotFound):
        return HTTPException(
            status_code=404, detail={"error": "fact_not_found", "message": str(exc)}
        )
    if isinstance(exc, CorrectionRefused):
        return _bad_request(str(exc))
    return _review_error(exc)


@router.post(
    "/api/projects/{project_id}/canon/events/{event_id}/cast",
    response_model=EventCastCorrection,
)
def edit_canon_event_cast(
    event_id: EventId,
    body: EventCastEditRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
    events: EventCastStore = Depends(get_event_store),
) -> EventCastCorrection:
    """改一条已生效事件的知情 / 在场名单。**删掉的那一行留着（RETRACTED）。**"""
    try:
        return correct_event_cast(
            conn,
            graph,
            events,
            proj.id,
            event_id,
            knower_ids=body.knower_ids,
            participant_ids=body.participant_ids,
            expected_canon_version=body.expected_canon_version,
        )
    except (FactNotFound, CorrectionRefused, project.StaleBaseVersion) as exc:
        raise _correction_error(exc) from exc


@router.post(
    "/api/projects/{project_id}/chapters/{chapter}/provisional/confirm",
    response_model=ProvisionalConfirmation,
)
def confirm_provisional(
    chapter: ChapterNumber,
    body: ProvisionalConfirmRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
    events: EventStore = Depends(get_event_store),
) -> ProvisionalConfirmation:
    try:
        if body.fact_kind == "event":
            return confirm_provisional_events(
                conn,
                graph,
                events,
                proj.id,
                body.fact_ids,
                expected_canon_version=body.expected_canon_version,
            )
        return confirm_provisional_edges(
            conn,
            graph,
            proj.id,
            body.fact_ids,
            expected_canon_version=body.expected_canon_version,
        )
    except (ProposalShapeError, ProposalValidationError) as exc:
        raise _bad_request(str(exc)) from exc
    except ConfirmationConflict as exc:
        raise _conflict("confirmation_conflict", message=str(exc)) from exc
    except project.StaleBaseVersion as exc:
        raise _review_error(exc) from exc


@router.get(
    "/api/projects/{project_id}/chapters/{chapter}/proposals",
    response_model=list[ProposalRecord],
)
def chapter_proposals(
    chapter: ChapterNumber,
    status: Annotated[Literal["PENDING"] | None, Query()] = None,
    proj: Any = Depends(load_project),
    proposals: ProposalStore = Depends(get_proposal_store),
    review_store: SqliteEdgeReviewStore = Depends(get_edge_review_store),
) -> list[ProposalRecord]:
    """待审队列。出参**带着 `items` 里那些 id 的显示名**（`node_refs`）。

    界面自己拿 id 去花名册里查是行不通的：那是另一条独立缓存的查询，后台抽取造出的
    新节点会在它里面缺席一拍，而那一拍的产物是屏幕上一串截断的内部编号。
    §10.3：闸门只出 `NodeRef`。
    """
    # 今天只有 PENDING 是真实队列；其它值都是调用方的 bug。
    return list(hydrate_proposal_names(review_store, proj.id, proposals.pending(proj.id, chapter)))


@router.post(
    "/api/projects/{project_id}/proposals/{proposal_id}/accept",
    response_model=ProposalResolution,
)
def accept_proposal(
    proposal_id: ProposalId,
    body: ProposalAcceptRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
    events: EventStore = Depends(get_event_store),
) -> ProposalResolution:
    try:
        return review_proposal(
            conn,
            graph,
            events,
            proposal_id,
            ProposalReview(
                action=ProposalAction.ACCEPT,
                expected_canon_version=body.expected_canon_version,
            ),
        )
    except (ProposalShapeError, ProposalValidationError) as exc:
        raise _bad_request(str(exc)) from exc
    except (ProposalNotFound, project.StaleBaseVersion, ProposalAlreadyResolved) as exc:
        raise _review_error(exc) from exc


@router.post(
    "/api/projects/{project_id}/proposals/{proposal_id}/reject",
    response_model=ProposalResolution,
)
def reject_proposal(
    proposal_id: ProposalId,
    body: ProposalRejectRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
    events: EventStore = Depends(get_event_store),
) -> ProposalResolution:
    action = ProposalAction.BYSTANDER if body.action == "bystander" else ProposalAction.REJECT
    try:
        return review_proposal(
            conn,
            graph,
            events,
            proposal_id,
            ProposalReview(
                action=action,
                expected_canon_version=body.expected_canon_version,
            ),
        )
    except (ProposalShapeError, ProposalValidationError) as exc:
        raise _bad_request(str(exc)) from exc
    except (ProposalNotFound, project.StaleBaseVersion, ProposalAlreadyResolved) as exc:
        raise _review_error(exc) from exc


@router.post(
    "/api/projects/{project_id}/proposals/{proposal_id}/edit",
    response_model=ProposalResolution,
)
def edit_proposal(
    proposal_id: ProposalId,
    body: ProposalEditRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
    events: EventStore = Depends(get_event_store),
) -> ProposalResolution:
    """接受这条提案，但按作者改过的样子落进 CANON。

    **这个洞补了两次，中间隔着两天，而中间那两天它看起来是补好的。**
    第一次（路由这一层）之后，`edit` 从「只存在于库里」变成「有一条 HTTP 路由能到达」——
    可浏览器里的作者面对一条 knowers 抽错的事件**仍然只有 accept 和 reject 两个按钮**：
    前端的 `ProposalAction` 联合里没有 `"edit"`，`useReviewProposal` 是个二分支，
    这条路由一个调用方都没有。**这份 docstring 当时用完成时写着「那正是这次要补的洞」，
    而洞只补了后端一半**——它是本仓「最后一厘米没接」那个形态的又一例，
    也是「已做完文档说没做」的反面：**没做完，文档说做完了**。

    2026-08-13 接上了另一半：`ProposalReviewTab.tsx::ProposalEditor`（概要 + 在场 + 知情
    三样，勾选框和「已确认的情节」那一格共用 `CastPicker.tsx`）。
    """
    try:
        return review_proposal(
            conn,
            graph,
            events,
            proposal_id,
            ProposalReview(
                action=ProposalAction.EDIT,
                expected_canon_version=body.expected_canon_version,
                edited_summary=body.edited_summary,
                edited_knower_ids=(
                    None if body.knower_ids is None else tuple(body.knower_ids)
                ),
                edited_participant_ids=(
                    None if body.participant_ids is None else tuple(body.participant_ids)
                ),
            ),
        )
    except (ProposalShapeError, ProposalValidationError) as exc:
        raise _bad_request(str(exc)) from exc
    except (ProposalNotFound, project.StaleBaseVersion, ProposalAlreadyResolved) as exc:
        raise _review_error(exc) from exc
