"""换章即后台：一章「写完了」之后该自动做掉的两件事（滚动总结 + 事件抽取）的薄 HTTP 壳。

## 触发点是「离开某一章」，不是「保存某一章」

这不是实现偏好，是成本事实。`draft/rolling_summary.py` 的幂等键是
``sha256(build_summary_messages(chapter.text))`` —— **哈希是正文的**。所以「每次保存后
自动总结」会在作者写一章的过程中每存一次就换一次哈希、重新付一次费：写一小时存 30 次
= 30 次模型调用。作者要的是「**写完后**」，而机器能识别的、最接近「这一章我写完了」的
信号是**换章**。前端在换章时打 `POST …/autopilot`，这里不设「保存即跑」的入口。

## 三条纪律

1. **真异步**：付费那一步走 FastAPI 的 `BackgroundTasks`（和 `api/extraction.py` 同一套
   机制，不另造第二套）；请求本身只做「决定要不要跑」这点纯读判断。
2. **幂等且不重复付费**：总结走 `RollingSummarizer.ensure`（同章同 prompt 只付一次），
   抽取走 `ExtractionRunner.enqueue`（同快照同 prompt 复用同一条 run）。这里**不绕过**
   它们中的任何一个——本模块只是提前把它们的答案读出来告诉前端，付不付钱仍由它们裁。
3. **失败不许静默**（ARCHITECTURE §10 约束 8）。这条在自动链路里比在手动链路里重要得多：
   作者没点过按钮，所以他不会去找结果——「什么都没发生」和「跑完了什么都没有」长一样的
   那一刻，这个能力就等于不存在。所以：
   - 没配模型 / 章没正文 / 抽取炸了，**POST 的回执和 GET 的出参里都说得出来**；
   - 后台总结的失败在进程里留痕（下面的 `_JobRegistry`），GET 分得开
     「还没生成」和「生成失败了，因为 X」。

## 已知的边界（写在这儿，免得下一个人以为是 bug）

- **改过正文的章不会被自动重新总结**：已有一条总结就判 `skipped`（契约里的「已经有的
  跳过」）。抽取那边则会——新正文 = 新快照 = 新 run。这条不对称是既有行为
  （`SummaryStore.coverage()` 也只问「有没有」不问「新不新」），要改得连它一起改。
  作者仍可用 `POST …/summary` 显式重生成，那条会按新正文的哈希重新付费。
- **作者撤回过的章一律不派**（`retracted`，迁移 013）。判据是 `SummaryStore.latest()`
  而不是 `get()`：后者对撤回过的章回 None，也就是「还没生成」——于是他撤掉一份、
  切走一章，后台立刻替他买一份回来，**顺带把他刚做的动作抹掉**。想要新的一份，
  按「重新生成」那颗按钮（同上一条：花钱的动作只由他自己按）。
- **`_JobRegistry` 是进程内的**：重启后「上次后台总结失败过」这件事就没了，GET 会退回
  说「还没生成」。它是给「本次会话里刚发生的事」用的，不是审计。抽取那边不吃这个亏
  ——`extraction_run` 表落了盘。
"""

from __future__ import annotations

from enum import StrEnum
from threading import Lock
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..draft.provider import ProviderError
from ..draft.rolling_summary import (
    RollingSummarizer,
    SummaryChapterNotFound,
    SummaryGenerationError,
    SummaryState,
    SummaryStore,
)
from ..extract.metrics import metrics_for_range
from ..extract.runner import (
    ExtractionChapterNotFound,
    ExtractionRunner,
    ExtractionRunStatus,
)
from ..graph.store import StoryGraph
from .deps import (
    build_summarizer,
    get_conn,
    get_extraction_runner,
    get_store,
    load_project,
    model_configuration_error,
)


router = APIRouter()
ChapterNumber = Annotated[int, Path(ge=1)]

MAX_TRACKED_JOBS = 512
"""进程内后台总结留痕的上限。超了丢最旧的——它是「刚刚发生了什么」，不是审计账本。"""


class Dispatch(StrEnum):
    """POST 的回执：这一次**派没派活出去**，以及没派的原因。"""

    QUEUED = "queued"
    """派了：后台会真的跑（可能命中幂等而不付费，那由引擎裁）。"""
    SKIPPED = "skipped"
    """没派，因为已经有了。"""
    NO_TEXT = "no_text"
    """没派，因为这一章没有当前正文快照——没得总结、也没得抽。"""
    RUNNING = "running"
    """没派，因为上一次派的还在跑。"""
    FAILED = "failed"
    """没派，因为上一次跑失败了。**自动链路不自动重试**：重试要花钱，得作者点。"""
    RETRACTED = "retracted"
    """没派，因为**作者亲手撤回过这一章的总结**（迁移 013）。

    同 FAILED 那条的道理，而且更硬：重来要花钱、得作者点，何况这一次「重来」还会
    把他刚做的那个动作抹掉。判据必须是 `SummaryStore.latest()` 而不是 `get()`——
    后者对撤回过的章回 None，于是「他撤掉、切走、后台立刻又生成一份」。"""
    UNCONFIGURED = "unconfigured"
    """没派，因为模型还没配好。"""


class Readiness(StrEnum):
    """GET 的出参：这一章的后台活**做到哪了**。"""

    READY = "ready"
    MISSING = "missing"
    """有正文、能跑、但还没有结果。这是「该催作者/该等」的那一种零。"""
    RETRACTED = "retracted"
    """有正文、生成过、**作者亲手撤回了**。和 MISSING 分开是因为下一步动作相反：
    那一种要催他去补，这一种是他刚做完的事。"""
    NO_TEXT = "no_text"
    """这一章还没写。和 MISSING 是两件事，别合并显示。"""
    RUNNING = "running"
    FAILED = "failed"
    UNCONFIGURED = "unconfigured"


class AutopilotError(BaseModel):
    """一条「为什么没发生」。**零永远带着理由。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["model", "summary", "extraction"]
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class AutopilotDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    summary: Dispatch
    extraction: Dispatch
    extraction_run_id: str | None = None
    """抽取那条 run 的 id。给前端一个**精确**的追查句柄：
    `GET /api/projects/{pid}/extractions/{run_id}` 拿得到错误码和原文。"""
    errors: tuple[AutopilotError, ...] = ()


class AutopilotStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    summary_ready: bool
    extraction_ready: bool
    running: bool
    summary_state: Readiness
    extraction_state: Readiness
    errors: tuple[AutopilotError, ...] = ()


class _SummaryJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    running: bool
    error: AutopilotError | None = None


class _JobRegistry:
    """后台总结的进程内留痕。**存在的理由只有一条**：`chapter_summary` 表只记成功，

    于是「后台替作者跑了一次、炸了」在库里一个字节都没有，GET 会说「还没生成」——
    那正是 §10 约束 8 点名的「静默的零和真的零长得一样」。抽取不需要这个（run 落了盘）。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[tuple[str, int], _SummaryJob] = {}

    def mark_running(self, project_id: str, chapter: int) -> None:
        self._put(project_id, chapter, _SummaryJob(running=True))

    def mark_failed(self, project_id: str, chapter: int, error: AutopilotError) -> None:
        self._put(project_id, chapter, _SummaryJob(running=False, error=error))

    def mark_done(self, project_id: str, chapter: int) -> None:
        with self._lock:
            self._jobs.pop((project_id, chapter), None)

    def get(self, project_id: str, chapter: int) -> _SummaryJob | None:
        with self._lock:
            return self._jobs.get((project_id, chapter))

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _put(self, project_id: str, chapter: int, job: _SummaryJob) -> None:
        key = (project_id, chapter)
        with self._lock:
            self._jobs.pop(key, None)
            self._jobs[key] = job
            while len(self._jobs) > MAX_TRACKED_JOBS:
                self._jobs.pop(next(iter(self._jobs)))


JOBS = _JobRegistry()
"""模块级单例。测试用 `JOBS.clear()` 隔离。"""


def _model_error(reason: str) -> AutopilotError:
    return AutopilotError(stage="model", code="model_not_configured", message=reason)


def _has_current_text(graph: StoryGraph, project_id: str, chapter: int) -> bool:
    """这一章有没有**当前**快照。

    判据必须是 `is_current`，不能是「有没有快照」：改过正文的章会留下历史快照，而
    `RollingSummarizer._chapter` 和 `ExtractionRunner.enqueue` 都只认
    `chapter_snapshot.text_sha256 == chapter.text_sha256` 那一条。判宽了就会告诉前端
    「能跑」，然后后台立刻 `SummaryChapterNotFound`。
    """
    return any(snapshot.is_current for snapshot in graph.chapter_snapshots(project_id, chapter))


def _summary_job(summarizer: RollingSummarizer, project_id: str, chapter: int) -> None:
    """后台总结的包装：**一个异常都不许逃出去**，全部换成留痕。

    逃出去的后台异常在 ASGI 层被吞掉（或在 TestClient 里炸成一个和作者无关的栈），
    两种结局都让作者看到「什么都没发生」。所以这里 catch-all，然后由 GET 说出来。
    """
    JOBS.mark_running(project_id, chapter)
    try:
        summarizer.ensure(project_id, chapter)
    except SummaryChapterNotFound:
        JOBS.mark_failed(
            project_id,
            chapter,
            AutopilotError(
                stage="summary",
                code="chapter_not_found",
                message=f"第 {chapter} 章没有当前正文快照，后台总结没跑。",
            ),
        )
    except SummaryGenerationError as exc:
        JOBS.mark_failed(
            project_id,
            chapter,
            AutopilotError(
                stage="summary",
                code="empty_summary",
                message=f"总结器返回了空文本：{exc}",
            ),
        )
    except ProviderError as exc:
        JOBS.mark_failed(
            project_id,
            chapter,
            AutopilotError(stage="summary", code="provider_failure", message=f"模型调用失败：{exc}"),
        )
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：后台异常逃出去 = 静默
        JOBS.mark_failed(
            project_id,
            chapter,
            AutopilotError(
                stage="summary",
                code="summary_failure",
                message=f"后台总结失败：{type(exc).__name__}: {exc}",
            ),
        )
    else:
        JOBS.mark_done(project_id, chapter)


@router.post(
    "/api/projects/{project_id}/chapters/{chapter}/autopilot",
    status_code=202,
    response_model=AutopilotDispatch,
)
def run_autopilot(
    chapter: ChapterNumber,
    background_tasks: BackgroundTasks,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: StoryGraph = Depends(get_store),
    runner: ExtractionRunner = Depends(get_extraction_runner),
    summarizer: RollingSummarizer = Depends(build_summarizer),
    config_error: str | None = Depends(model_configuration_error),
) -> AutopilotDispatch:
    """这一章「写完了」（= 作者切走了），把该在后台做的都做掉。幂等。

    **总是 202，哪怕一件事都没派出去**：这是给「换章」这个无人值守的动作用的端点，
    模型没配好就弹一个 4xx 会变成每换一章骂作者一次。真相在回执体里——`summary` /
    `extraction` 两个字段说清楚派没派、没派为什么，`errors` 给人话。
    """
    errors: list[AutopilotError] = []
    if config_error is not None:
        errors.append(_model_error(config_error))
    has_text = _has_current_text(graph, proj.id, chapter)

    # **`latest()` 不是 `get()`**：撤回过的章在 `get()` 眼里就是「还没生成」，
    # 于是作者撤掉一份总结、切走一章，后台立刻替他重新买一份回来（见 `Dispatch.RETRACTED`）。
    stored = SummaryStore(conn).latest(proj.id, chapter)
    if not has_text:
        summary = Dispatch.NO_TEXT
    elif stored is not None and stored.status is SummaryState.RETRACTED:
        summary = Dispatch.RETRACTED
    elif stored is not None:
        summary = Dispatch.SKIPPED
    elif config_error is not None:
        summary = Dispatch.UNCONFIGURED
    else:
        background_tasks.add_task(_summary_job, summarizer, proj.id, chapter)
        summary = Dispatch.QUEUED

    run_id: str | None = None
    if not has_text:
        extraction = Dispatch.NO_TEXT
    elif config_error is not None:
        extraction = Dispatch.UNCONFIGURED
    else:
        try:
            run = runner.enqueue(proj.id, chapter)
        except ExtractionChapterNotFound:
            # 只可能是和一次删章/改章撞上了；照实说，别把它记成「跑过了」。
            extraction = Dispatch.NO_TEXT
        else:
            run_id = run.id
            if run.status is ExtractionRunStatus.PENDING:
                background_tasks.add_task(runner.run, run.id)
                extraction = Dispatch.QUEUED
            elif run.status is ExtractionRunStatus.RUNNING:
                extraction = Dispatch.RUNNING
            elif run.status is ExtractionRunStatus.SUCCEEDED:
                extraction = Dispatch.SKIPPED
            else:
                extraction = Dispatch.FAILED
                first = run.errors[0] if run.errors else None
                errors.append(
                    AutopilotError(
                        stage="extraction",
                        code=first.code if first else "extraction_failed",
                        message=(
                            f"第 {chapter} 章上一次抽取失败："
                            f"{first.message if first else '没有留下错误信息'}"
                            "。自动链路不重试（要再付一次钱），请在章节页手动重跑。"
                        ),
                    )
                )

    return AutopilotDispatch(
        chapter=chapter,
        summary=summary,
        extraction=extraction,
        extraction_run_id=run_id,
        errors=tuple(errors),
    )


@router.get(
    "/api/projects/{project_id}/chapters/{chapter}/autopilot",
    response_model=AutopilotStatus,
)
def autopilot_status(
    chapter: ChapterNumber,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    graph: StoryGraph = Depends(get_store),
    config_error: str | None = Depends(model_configuration_error),
) -> AutopilotStatus:
    """这一章的后台活做到哪了（前端起草前的兜底）。**不派活、不花钱、不建 run。**

    抽取那半边读的是 `extraction_run` 的**计数**（`extract.metrics`），所以它回答的是
    「这一章曾经成功抽取过没有」，分不清「成功的那次是不是当前这版正文」。要精确答案就用
    POST 回执里的 `extraction_run_id` 去查 `GET …/extractions/{run_id}`。这里刻意收得住：
    状态端点自己去 `enqueue` 一条 run 出来，就把「查一下」变成了副作用。
    """
    errors: list[AutopilotError] = []
    has_text = _has_current_text(graph, proj.id, chapter)
    stored = SummaryStore(conn).latest(proj.id, chapter)
    job = JOBS.get(proj.id, chapter)

    if stored is not None and stored.status is SummaryState.RETRACTED:
        summary_state = Readiness.RETRACTED
    elif stored is not None:
        summary_state = Readiness.READY
    elif not has_text:
        summary_state = Readiness.NO_TEXT
    elif job is not None and job.running:
        summary_state = Readiness.RUNNING
    elif job is not None and job.error is not None:
        summary_state = Readiness.FAILED
        errors.append(job.error)
    elif config_error is not None:
        summary_state = Readiness.UNCONFIGURED
    else:
        summary_state = Readiness.MISSING

    metrics = metrics_for_range(conn, proj.id, chapter, chapter)
    in_flight = metrics.total_runs - metrics.succeeded_runs - metrics.failed_runs
    if not has_text:
        extraction_state = Readiness.NO_TEXT
    elif in_flight > 0:
        extraction_state = Readiness.RUNNING
    elif metrics.succeeded_runs > 0:
        extraction_state = Readiness.READY
    elif metrics.failed_runs > 0:
        extraction_state = Readiness.FAILED
        errors.append(
            AutopilotError(
                stage="extraction",
                code="extraction_failed",
                message=(
                    f"第 {chapter} 章的抽取跑失败过 {metrics.failed_runs} 次、没有一次成功。"
                    "错误详情在 POST 回执给的 run id 里。"
                ),
            )
        )
    elif config_error is not None:
        extraction_state = Readiness.UNCONFIGURED
    else:
        extraction_state = Readiness.MISSING

    if config_error is not None and Readiness.UNCONFIGURED in (summary_state, extraction_state):
        errors.append(_model_error(config_error))

    return AutopilotStatus(
        chapter=chapter,
        summary_ready=summary_state is Readiness.READY,
        extraction_ready=extraction_state is Readiness.READY,
        running=Readiness.RUNNING in (summary_state, extraction_state),
        summary_state=summary_state,
        extraction_state=extraction_state,
        errors=tuple(errors),
    )
