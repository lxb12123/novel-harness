"""保存触发的唯一后台运行时（Task 16）。

保存 / reconcile / 显式「重新整理」三件事都只做一件事：持久地写一个
`chapter_refresh_attempt`（lease/fence 化），然后唤醒这个 dispatcher。谁都不许
直接调模型、谁都不许把「内存 enqueue 成功」当业务提交成功——模型调用只在
本模块的 adapter 里发生，一次模型调用 = 一张表里的一行，重启后能接着跑。

wake signal 丢了也不怕：dispatcher 轮询 `recover_claimable`，任何一次启动 /
定时扫描都能把 PENDING / 过期 RUNNING 重新 claim（不变量 18）。本模块提供的是
尽力而为的即时唤醒 + 一定做得到的持久扫描的组合。
"""

from __future__ import annotations

from collections.abc import Callable
import os
import threading
import time
from typing import Any

from ..chapter_refresh import (
    BranchAdapter,
    ChapterRefreshCoordinator,
    recover_claimable,
)
from ..db import Connection
from ..draft.rolling_summary import RollingSummarizer
from ..extract.runner import ExtractionRunner
from ..graph.sqlite_store import SqliteStoryGraph

__all__ = ["BackgroundRuntime", "build_runtime", "new_connection_factory"]


def new_connection_factory(db_path: str) -> Callable[[], Connection]:
    """每一条 worker / coordinator 连接独立实例（真并行需要各自连接不共享事务）。

    制造连接本身由装配层（`api/deps.py::background_connection_factory`）做——
    本模块只收工厂，不 import `connect`（`test_arch_guard.py` 的 CONNECTION_OPENERS）。
    `db_path` 保留以便将来扩展（测试用 `lambda: connect(tmp)` 注入自己的工厂）。
    """
    from .deps import background_connection_factory

    return background_connection_factory()


class _ExtractionAdapter(BranchAdapter):
    """真实抽取分支：独立连接上 enqueue + run（一次 run 至多付一次模型）。"""

    def __init__(self, runner_factory: Callable[[], ExtractionRunner]) -> None:
        self._runner_factory = runner_factory

    def run(self, ctx: Any) -> str:
        runner = self._runner_factory()
        queued = runner.enqueue(ctx.project_id, ctx.chapter_number)
        done = runner.run(queued.id)
        return f"extraction:{done.status.value}"


class _SummaryAdapter(BranchAdapter):
    """真实滚动总结分支：独立连接上 `ensure`（幂等，付费一次）。"""

    def __init__(self, summarizer_factory: Callable[[], RollingSummarizer]) -> None:
        self._summarizer_factory = summarizer_factory

    def run(self, ctx: Any) -> str:
        summarizer = self._summarizer_factory()
        summarizer.ensure(ctx.project_id, ctx.chapter_number)
        return "summary:active"


class BackgroundRuntime:
    """把持久 attempt 变成真实的验证 → 总结 ∥ 抽取结果。

    不是常驻线程池：dispatcher 一次「波次」claim 一批、跑完、睡一小会儿再扫。
    TestClient 里由 lifespan 驱动；测试注入 `sleep` / 固定 factory 来收敛。
    """

    def __init__(
        self,
        *,
        db_path: str,
        runner_factory: Callable[[], ExtractionRunner],
        summarizer_factory: Callable[[], RollingSummarizer],
        connection_factory: Callable[[], Connection] | None = None,
        owner: str = "background",
        poll_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._db_path = db_path
        self._conn_factory = connection_factory or new_connection_factory(db_path)
        self._coordination = ChapterRefreshCoordinator(
            self._conn_factory(),
            connection_factory=self._conn_factory,
            store_factory=lambda c: SqliteStoryGraph(c),
        )
        self._runner_factory = runner_factory
        self._summarizer_factory = summarizer_factory
        self._owner = owner
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="dsh-background", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def pump_once(self) -> int:
        """跑一波：claim 可做的 attempt 并执行固定 DAG。返回执行了几条。"""
        conn = self._conn_factory()
        try:
            claimed = recover_claimable(conn, owner=self._owner)
        finally:
            conn.close()
        for attempt_id, token in claimed:
            self._run_one(attempt_id, token)
        return len(claimed)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.pump_once()
            except Exception:
                # 单波失败不炸线程：下一波靠 lease 过期重抢，不留孤儿。
                pass
            self._sleep(self._poll_seconds)

    def _run_one(self, attempt_id: str, token: int) -> None:
        conn = self._conn_factory()
        try:
            self._coordination.run(
                attempt_id,
                owner=self._owner,
                token=token,
                summary_adapter=_SummaryAdapter(self._summarizer_factory),
                extraction_adapter=_ExtractionAdapter(self._runner_factory),
                alias_adapter=None,
            )
        finally:
            conn.close()


def build_runtime(*, db_path: str | None = None, **kwargs) -> BackgroundRuntime:
    """装配一个后台运行时。`db_path` 默认取环境变量 NH_DB。

    真实 adapter 用 deps 里的 runner / summarizer factory；测试注入桩。
    """
    if db_path is None:
        raw = os.environ["NH_DB"]
        db_path = str(raw)
    from . import deps

    kwargs.setdefault("runner_factory", deps.get_extraction_runner)
    kwargs.setdefault("summarizer_factory", deps.get_summarizer)
    return BackgroundRuntime(db_path=db_path, **kwargs)
