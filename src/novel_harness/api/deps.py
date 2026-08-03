"""装配层 —— 壳里**唯一**开连接的地方（因此在 test_arch_guard 的 CONNECTION_OPENERS 里）。

复刻 cli.py 的两条纪律：
- `_db_path()` 用「存在才连」的语义（对应 cli 的 `_connect_existing`）——绝不裸 `connect`
  一个不存在的路径，否则 sqlite 会建一个空库，给出一张「看起来正常、全 UNKNOWN」的假矩阵。
- 一请求一连接：`get_conn` 被 FastAPI 在单个请求内缓存，所以 `get_store` 和 `load_project`
  共享同一条连接（M1 之后写路径要靠这个：Ledger 与 store 必须同连接，transaction() 才盖得住）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, HTTPException

from .. import project as project_mod
from ..db import Connection, connect, migrate
from ..declare import Ledger
from ..draft.capabilities import ReasoningEffort, plan_structured_call, resolve_capabilities
from ..draft.provider import CompletionResult, ProviderConfig, complete
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
        raise RuntimeError("环境变量 NH_DB 未设置：指向 nh init / seed_demo 建好的库")
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
    """一请求一连接，请求结束即关。FastAPI 在单请求内缓存本依赖 → 全链路共用一条。"""
    conn = connect(_db_path())
    try:
        yield conn
    finally:
        conn.close()


def get_store(conn: Connection = Depends(get_conn)) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


def get_event_store(conn: Connection = Depends(get_conn)) -> SqliteEventStore:
    """Request-scoped event reader sharing the exact connection used by load_project."""
    return SqliteEventStore(conn)


def _extraction_provider_config() -> ProviderConfig:
    """Resolve BYOK settings only when the background runner actually calls the model."""
    user = load_user_settings()
    return ProviderConfig(
        base_url=user.base_url or os.environ.get("NH_LLM_BASE_URL", ""),
        model=user.model or os.environ.get("NH_LLM_MODEL", ""),
        api_key=user.api_key or os.environ.get("NH_LLM_API_KEY", ""),
        temperature=None,
    )


def _analyze_extraction(request: AnalysisRequest) -> CompletionResult:
    """Send the request's audited wire messages through one validated structured plan."""
    config = _extraction_provider_config()
    capability = resolve_capabilities(config.base_url, config.model)
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
