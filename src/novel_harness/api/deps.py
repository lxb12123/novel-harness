"""装配层 —— 壳里**唯一**开连接的地方（因此在 test_arch_guard 的 CONNECTION_OPENERS 里）。

两条纪律（原是从命令行面复刻来的，那一面 2026-08-20 已删，见 ADR 0034；今天另一处装配层
是 `api/launch.py`，它**要**建库所以走的是另一套）：
- `_db_path()` 用「存在才连」的语义——绝不裸 `connect`
  一个不存在的路径，否则 sqlite 会建一个空库，给出一张「看起来正常、全 UNKNOWN」的假矩阵。
- 一请求一连接：`get_conn` 被 FastAPI 在单个请求内缓存，所以 `get_store` 和 `load_project`
  共享同一条连接（M1 之后写路径要靠这个：Ledger 与 store 必须同连接，transaction() 才盖得住）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import ValidationError

from .. import project as project_mod
from ..db import Connection, connect, migrate
from ..declare import Ledger
from ..draft.capabilities import (
    CapabilityError,
    ProviderCapabilities,
    ReasoningEffort,
    plan_call,
    plan_structured_call,
    resolve_capabilities,
)
from ..draft.discovery import resolve_with_discovery
from ..draft.provider import CompletionResult, ProviderConfig, complete
from ..draft.rolling_summary import RollingSummarizer, SummaryRequest
from ..draft.summarize import SUMMARY_LENGTH
from ..draft.windows import capabilities_from_author
from ..extract.control import AnalysisRequest
from ..extract.runner import ExtractionRunner
from ..graph.sqlite_events import SqliteEventStore
from ..graph.sqlite_store import SqliteStoryGraph
from ..settings import load as load_user_settings


EXTRACTION_VISIBLE_TOKEN_BUDGET = 8_192
"""Maximum visible JSON output for one bounded 1-12 event chapter analysis."""


def _db_path() -> Path:
    raw = os.environ.get("NH_DB")
    if not raw:
        raise RuntimeError("环境变量 NH_DB 未设置：指向启动器（api/launch.py）或 seed_demo 建好的库")
    path = Path(raw)
    if not path.exists():
        raise RuntimeError(f"NH_DB 指向的库不存在：{path}（不让 connect 建一个空库出来）")
    return path


def books_root() -> Path:
    """新书的稿子目录落在哪儿。作者从 UI 建书时不该、也不用敲文件系统路径（非程序员）——
    服务器在这个基目录下按书名给它开一个子目录。默认 `<NH_DB 同级>/books`，可用
    `NH_BOOKS_DIR` 覆盖。正文在磁盘（ADR 0007），所以这个目录**是稿子，不是导出物**。
    """
    raw = os.environ.get("NH_BOOKS_DIR")
    return Path(raw) if raw else _db_path().parent / "books"


def ensure_schema() -> None:
    """启动时跑一次：确认库在、schema 到位（migrate 幂等）。"""
    conn = connect(_db_path())
    try:
        migrate(conn)
    finally:
        conn.close()


def get_conn() -> Iterator[Connection]:
    """一请求一连接，请求结束即关。

    FastAPI 的依赖、路由和清理步骤可能在不同的线程池工作线程执行；同一请求内仍由
    FastAPI 缓存本依赖，因此全链路共用这一条连接。
    """
    conn = connect(_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def background_connection_factory() -> Callable[[], Connection]:
    """装配层给后台 runtime 的**连接工厂**（每个 worker 一条独立连接）。

    放在这里是因为只有装配层有资格开连接（`test_arch_guard.py` 的
    CONNECTION_OPENERS）；`api/background_runtime.py` 只收工厂、不 import connect。
    """
    path = _db_path()
    return lambda: connect(path)


def get_store(conn: Connection = Depends(get_conn)) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


def get_event_store(conn: Connection = Depends(get_conn)) -> SqliteEventStore:
    """Request-scoped event reader sharing the exact connection used by load_project."""
    return SqliteEventStore(conn)


def _byok_config(temperature: float | None) -> ProviderConfig:
    """Resolve BYOK settings only when a background job actually calls the model.

    设置页优先、环境变量兜底的**顺序**是产品决定（桌面作者没有环境变量，钥匙在设置页里）；
    它在本壳里有三个消费者（抽取 / 总结 / 起草），所以这个顺序只在这里写一次。
    """
    user = load_user_settings()
    return ProviderConfig(
        base_url=user.base_url or os.environ.get("NH_LLM_BASE_URL", ""),
        model=user.model or os.environ.get("NH_LLM_MODEL", ""),
        api_key=user.api_key or os.environ.get("NH_LLM_API_KEY", ""),
        temperature=temperature,
    )


def resolve_route_capabilities(config: ProviderConfig) -> ProviderCapabilities:
    """这条路由的能力，**作者在设置页手填的窗口压在最上面**。

    ── 为什么壳里所有解析都必须走这一个函数 ────────────────────────────────

    同 `_byok_config` 那条理由：顺序只写一次。这里的顺序是
    `作者手填 → 注册表 → 端点自报 → 公共快照 → unknown`，而漏掉手填那一档的地方
    会**静默**地少给上文（`product_tail_limit` 从上万字塌回 800），
    症状是「模型忽然变笨」——没有任何一处会红，作者也说不出哪儿不对。

    ── 走的是既有的那个口子，不是第二条路径 ──────────────────────────────

    `resolve_capabilities` 的 `operator_override` 档本来就是为这件事留的，
    顺带把「override 的路由必须等于请求的路由」验掉（`capabilities_from_author`
    是从同一条能力上长出来的，所以这条断言恒真——它拦的是以后有人手工造一份塞进来）。

    ⚠️ **写作助手（模式二）那条今天不走这儿**：`agent/model.py` 自己调
    `resolve_with_discovery`。哪天要让手填的数也管到聊天，改的是那一处，不是这儿再抄一份。
    """
    capability = resolve_with_discovery(config.base_url, config.model)
    override = capabilities_from_author(capability, load_user_settings().context_window)
    if override is None:
        return capability
    return resolve_capabilities(config.base_url, config.model, operator_override=override)


def _extraction_provider_config() -> ProviderConfig:
    return _byok_config(0.3)


def _analyze_extraction(request: AnalysisRequest) -> CompletionResult:
    """Send the request's audited wire messages through one validated structured plan."""
    config = _extraction_provider_config()
    capability = resolve_route_capabilities(config)
    plan = plan_structured_call(
        EXTRACTION_VISIBLE_TOKEN_BUDGET,
        ReasoningEffort.OFF,
        capability,
        prompt_token_budget=len(request.prompt_bytes),
    )
    return complete(request.wire_messages(), config=config, plan=plan)


def get_extraction_runner() -> ExtractionRunner:
    """Build an overrideable runner whose paid execution owns independent connections."""
    path = _db_path()
    return ExtractionRunner(lambda: connect(path), _analyze_extraction)


def _summary_provider_config() -> ProviderConfig:
    return _byok_config(None)


def agent_provider_config() -> ProviderConfig:
    """写作助手（模式二）的连接参数。**第四个消费者，仍然只有这一份 BYOK 顺序。**

    不发 temperature（同总结那一档）：`SAMPLING_STRICT_MODELS` 里的模型会对非默认采样
    参数返回 400，而作者换模型不该换出一个「写作助手突然用不了」。
    """
    return _byok_config(None)


def _analyze_summary(request: SummaryRequest) -> CompletionResult:
    config = _summary_provider_config()
    capability = resolve_route_capabilities(config)
    plan = plan_call(
        SUMMARY_LENGTH,
        ReasoningEffort.OFF,
        capability,
        prompt_token_budget=len(request.prompt_bytes),
    )
    return complete(request.messages, config=config, plan=plan)


def model_configuration_error() -> str | None:
    """模型配好了没：配好了返 `None`，没配好返**一句作者照着能做的话**。

    抽出来是因为它有两个语气不同的消费者，而语气差别是产品差别，不是风格：
    - 作者**亲手点**的付费动作（`get_summarizer`）→ 没配好就该当场 422 顶回去；
    - **后台自动**跑的链路（`api/autopilot.py`）→ 换一次章弹一次 422 是骚扰，
      它要的是把这句话装进回执里（§10 约束 8：什么都没发生时必须说得出为什么）。

    判据只看 `base_url` / `model`（`resolve_capabilities` 的入参），三个 provider
    档（抽取 / 总结 / 起草）共用同一份 BYOK 设置，所以问一次就够。

    走 `resolve_route_capabilities` 而不是裸的解析，是为了让**作者填了一个不合法的窗口**
    （比如小到装不下任何一次调用）也在这儿被说成一句人话，而不是等到付费那一刻才炸。
    """
    try:
        config = _summary_provider_config()
        resolve_route_capabilities(config)
    except (ValidationError, ValueError, CapabilityError) as exc:
        return (
            f"模型没配好：{exc} —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，"
            "或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。"
        )
    return None


def build_summarizer() -> RollingSummarizer:
    """造一个总结器，**不问「模型配好了没」**（问不问由调用方决定，见上）。

    付费执行**自己开连接**（同 `get_extraction_runner`）：`ensure` 里有一段
    `BEGIN IMMEDIATE`，借请求那条连接会把整个请求的读路径一起锁进去。
    """
    path = _db_path()
    return RollingSummarizer(lambda: connect(path), _analyze_summary)


def get_summarizer() -> RollingSummarizer:
    """章节滚动总结器。**在这里就把「模型配好了没」问一次。**

    问题不是防御性编程：不问的话，没配钥匙的作者点「生成总结」拿到的是
    `RollingSummarizer.ensure` 中途抛出的 pydantic / capability 开发者输出——被全局
    handler 接住原样发成一句 422 英文，而他能做的动作是「去顶栏 ⚙ 填三个框」。
    依赖层抛 HTTPException 是本模块既有的做法（`load_project`）。
    """
    reason = model_configuration_error()
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    return build_summarizer()


def load_project(project_id: str, conn: Connection = Depends(get_conn)) -> project_mod.Project:
    """项目存在闸门：不存在 → 404（区分「项目 id 错」和「书还没开」）。"""
    proj = project_mod.get(conn, project_id)
    if proj is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "project_not_found", "project_id": project_id},
        )
    return proj


def get_ledger(
    store: SqliteStoryGraph = Depends(get_store),
    conn: Connection = Depends(get_conn),
    proj: project_mod.Project = Depends(load_project),
) -> Ledger:
    """作者的声明入口（写路径）。**装配层唯一构 Ledger 的地方**——复刻 cli._ledger。

    `store` 和 `conn` 必须是**同一条连接**（`Ledger` 的契约：图和 decision_log 要在
    一个事务里同落）。这在这里天然成立：`get_conn` 被 FastAPI 在单请求内缓存，
    `get_store` 和本依赖拿到的是同一个 conn。`load_project` 先跑 → 项目不存在直接 404，
    Ledger 不会拿到一个空项目号。
    """
    return Ledger(store, conn, proj.id)
