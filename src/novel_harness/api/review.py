"""M4 提案审阅、被动确认，以及**改一条已经生效的事实**的薄 HTTP 壳。

路由只负责校验/装载/调用抽取服务并回吐 Pydantic 出参；这里不放 SQL 也不放业务规则。

`/canon/…` 那两条是「事后可查可改」的**改**：抽取直接生效之后，作者第一次看见那条事实时
它已经是 CANON，所以退路必须在 CANON 上，而不只在审阅队列里。业务在 `corrections.py`。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import project
from ..corrections import (
    CorrectionRefused,
    EventCastCorrection,
    FactNotFound,
    KnowledgeCorrection,
    correct_event_cast,
    correct_knowledge,
)
from ..db import Connection
from ..events import (
    EventCastStore,
    EventStore,
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalRecord,
    ProposalStore,
    ProposalValidationError,
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
from ..graph import EdgeType, GraphStore
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from .deps import get_conn, get_event_store, get_store, load_project


router = APIRouter()
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


class KnowledgeEditRequest(BaseModel):
    """把这一格从 KNOWS 改成 BELIEVES（或反过来）。

    **入参里没有章号，一个都没有**（ARCHITECTURE §10 约束 10 / ADR 0006）：改的是
    「这条事实说错了」，不是「它从第几章开始成立」——后者由证据说了算，作者不记得
    也不该被问。`tests/test_no_chapter_input.py` 有一条守卫在扫这个 schema。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    character_id: NodeId
    secret_id: NodeId
    to_type: Literal["KNOWS", "BELIEVES"]
    believed_value: str | None = None
    """`to_type="BELIEVES"` 时必填：他以为的那一版。面板 §3.2 渲染的就是它。"""

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
    "/api/projects/{project_id}/canon/knowledge",
    response_model=KnowledgeCorrection,
)
def edit_canon_knowledge(
    body: KnowledgeEditRequest,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: GraphStore = Depends(get_store),
) -> KnowledgeCorrection:
    """「他知道 X」改成「他以为 X」，或者反过来。**旧的那条留着（RETRACTED）。**"""
    try:
        return correct_knowledge(
            conn,
            graph,
            proj.id,
            character_id=body.character_id,
            secret_id=body.secret_id,
            to_type=EdgeType(body.to_type),
            believed_value=body.believed_value,
            expected_canon_version=body.expected_canon_version,
        )
    except (FactNotFound, CorrectionRefused, project.StaleBaseVersion) as exc:
        raise _correction_error(exc) from exc


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
