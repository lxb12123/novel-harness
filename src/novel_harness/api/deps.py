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
from ..graph.sqlite_store import SqliteStoryGraph


def _db_path() -> Path:
    raw = os.environ.get("NH_DB")
    if not raw:
        raise RuntimeError("环境变量 NH_DB 未设置：指向 nh init / seed_demo 建好的库")
    path = Path(raw)
    if not path.exists():
        raise RuntimeError(f"NH_DB 指向的库不存在：{path}（不让 connect 建一个空库出来）")
    return path


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
