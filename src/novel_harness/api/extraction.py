"""显式 M4 后台抽取的薄 HTTP 壳。

这一层曾经多干一件事：把「这次为什么没跑成」翻成中文（`ExtractionRunView`）。
国际化第四批·笔二起，这一层**不再翻译**——`errors` 直接转发 `ExtractionErrorCode`
的原始值，整句由前端 `messageForCode("run_error", language, {code})` 按当前界面
语言渲染（唯一那张表在 `frontend/src/backendMessages.ts` 的 `RUN_ERROR_LABEL`）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from ..events import EventView
from ..events.store import EventStore
from ..extract.runner import (
    ExtractionChapterNotFound,
    ExtractionRun,
    ExtractionRunner,
    ExtractionRunNotFound,
    ExtractionRunStatus,
)
from ..graph import InformationScope
from ..graph.store import StoryGraph
from .deps import get_event_store, get_extraction_runner, get_store, load_project


router = APIRouter()
ChapterNumber = Annotated[int, Path(ge=1)]


class EventReadScope(StrEnum):
    PROVISIONAL = InformationScope.PROVISIONAL.value
    CANON = InformationScope.CANON.value


class ExtractionRunView(BaseModel):
    """一次整理**给作者看的**那一份。和 `ExtractionRun`（审计真相）只差一个字段。

    ── `errors` 是 `ExtractionErrorCode` 的原始值，不是 `{code, message}` ─────────

    `ExtractionRunError.message` 是写给**维护者**的英文诊断（`extract/control.py`
    那儿写着「它永远不上作者的屏幕」），这一条纪律没变：`message` 依然一个字都不出去。

    **变的是 `code` 那半**（国际化第四批·笔二）：以前这里在出门前就把 `code` 翻成
    中文（`activity.run_error_label`），因为后端知道要说中文；现在界面语言独立于
    书的语言，后端答不出「读这句话的人用什么界面语言」，所以只转发原始 `code`，
    前端拿它去 `messageForCode("run_error", language, {code})` 按当前界面语言渲染。
    形状和日志页展开层的 `ActivityDetail.errors` 一样是 `tuple[str, ...]`——
    同一件事在两条读端上长同一个样，前端也就不必认两种形状。

    **不许在这里写第二张翻译表。** 唯一一份在 `frontend/src/backendMessages.ts`
    的 `RUN_ERROR_LABEL`，`tests/test_wording_guard.py` 拿 `ExtractionErrorCode`
    枚举本身钉着它的完整性。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    chapter_number: int = Field(ge=1)
    snapshot_id: str
    status: ExtractionRunStatus
    errors: tuple[str, ...] = ()
    valid_event_count: int = Field(ge=0)
    discarded_event_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    model_call_id: str | None = None
    schema_version: str
    prompt_hash: str
    source_generation: int | None = Field(default=None, ge=1)
    required_ruleset_epoch: int | None = Field(default=None, ge=1)
    required_ruleset_hash: str | None = None
    fencing_token: int = 0
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


def _view(run: ExtractionRun) -> ExtractionRunView:
    """审计模型 → 出参模型。**唯一的转换点**，两条路由都过它。"""
    return ExtractionRunView(
        **run.model_dump(exclude={"errors"}),
        errors=tuple(error.code for error in run.errors),
    )


def _run_not_found(run_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "extraction_run_not_found"},
    )


@router.post(
    "/api/projects/{project_id}/chapters/{chapter}/extract",
    status_code=202,
    response_model=ExtractionRunView,
)
def extract_chapter(
    chapter: ChapterNumber,
    background_tasks: BackgroundTasks,
    force: Annotated[bool, Query()] = False,
    proj: Any = Depends(load_project),
    runner: ExtractionRunner = Depends(get_extraction_runner),
) -> ExtractionRunView:
    try:
        run = runner.enqueue(proj.id, chapter, force=force)
    except ExtractionChapterNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "params": {"chapter": chapter}},
        ) from exc
    background_tasks.add_task(runner.run, run.id)
    return _view(run)


@router.get(
    "/api/projects/{project_id}/extractions/{run_id}",
    response_model=ExtractionRunView,
)
def extraction_status(
    run_id: str,
    proj: Any = Depends(load_project),
    runner: ExtractionRunner = Depends(get_extraction_runner),
) -> ExtractionRunView:
    try:
        run = runner.get(run_id)
    except ExtractionRunNotFound as exc:
        raise _run_not_found(run_id) from exc
    if run.project_id != proj.id:
        raise _run_not_found(run_id)
    return _view(run)


@router.get(
    "/api/projects/{project_id}/chapters/{chapter}/events",
    response_model=list[EventView],
)
def chapter_events(
    chapter: ChapterNumber,
    scope: EventReadScope = Query(...),
    proj: Any = Depends(load_project),
    graph: StoryGraph = Depends(get_store),
    events: EventStore = Depends(get_event_store),
) -> list[EventView]:
    if not graph.chapter_snapshots(proj.id, chapter):
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "params": {"chapter": chapter}},
        )
    return events.events_for_chapter(
        proj.id,
        chapter,
        InformationScope(scope.value),
    )
