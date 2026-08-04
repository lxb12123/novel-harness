"""显式 M4 后台抽取的薄 HTTP 壳。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query

from ..events import EventView
from ..events.store import EventStore
from ..extract.runner import (
    ExtractionChapterNotFound,
    ExtractionRun,
    ExtractionRunner,
    ExtractionRunNotFound,
)
from ..graph import InformationScope
from ..graph.store import StoryGraph
from .deps import get_event_store, get_extraction_runner, get_store, load_project


router = APIRouter()
ChapterNumber = Annotated[int, Path(ge=1)]


class EventReadScope(StrEnum):
    PROVISIONAL = InformationScope.PROVISIONAL.value
    CANON = InformationScope.CANON.value


def _run_not_found(run_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "extraction_run_not_found", "run_id": run_id},
    )


@router.post(
    "/api/projects/{project_id}/chapters/{chapter}/extract",
    status_code=202,
    response_model=ExtractionRun,
)
def extract_chapter(
    chapter: ChapterNumber,
    background_tasks: BackgroundTasks,
    proj: Any = Depends(load_project),
    runner: ExtractionRunner = Depends(get_extraction_runner),
) -> ExtractionRun:
    try:
        run = runner.enqueue(proj.id, chapter)
    except ExtractionChapterNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "chapter": chapter},
        ) from exc
    background_tasks.add_task(runner.run, run.id)
    return run


@router.get(
    "/api/projects/{project_id}/extractions/{run_id}",
    response_model=ExtractionRun,
)
def extraction_status(
    run_id: str,
    proj: Any = Depends(load_project),
    runner: ExtractionRunner = Depends(get_extraction_runner),
) -> ExtractionRun:
    try:
        run = runner.get(run_id)
    except ExtractionRunNotFound as exc:
        raise _run_not_found(run_id) from exc
    if run.project_id != proj.id:
        raise _run_not_found(run_id)
    return run


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
            detail={"error": "chapter_not_found", "chapter": chapter},
        )
    return events.events_for_chapter(
        proj.id,
        chapter,
        InformationScope(scope.value),
    )
