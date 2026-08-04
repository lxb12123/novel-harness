"""M4 提案审阅与被动确认的薄 HTTP 壳。

路由只负责校验/装载/调用抽取服务并回吐 Pydantic 出参；这里不放 SQL 也不放业务规则。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import project
from ..db import Connection
from ..events import (
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
    review_proposal,
)
from ..graph import GraphStore
from ..graph.sqlite_proposals import SqliteProposalStore
from .deps import get_conn, get_event_store, get_store, load_project


router = APIRouter()
ChapterNumber = Annotated[int, Path(ge=1)]
ProposalId = Annotated[str, Path(min_length=1)]


def get_proposal_store(conn: Connection = Depends(get_conn)) -> SqliteProposalStore:
    """请求级提案读取器，与项目共用同一条连接。"""
    return SqliteProposalStore(conn)


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
) -> list[ProposalRecord]:
    # 今天只有 PENDING 是真实队列；其它值都是调用方的 bug。
    return proposals.pending(proj.id, chapter)


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
