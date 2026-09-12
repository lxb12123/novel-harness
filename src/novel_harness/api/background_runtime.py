"""保存触发的唯一后台运行时（Task 16）＋ 30 分钟自治调度（2026-08-18 文档 §2.2）。

保存 / reconcile / 显式「重新整理」三件事都只做一件事：持久地写一个
`chapter_refresh_attempt`（lease/fence 化），然后唤醒这个 dispatcher。谁都不许
直接调模型、谁都不许把「内存 enqueue 成功」当业务提交成功——模型调用只在
本模块的 adapter 里发生，一次模型调用 = 一张表里的一行，重启后能接着跑。

wake signal 丢了也不怕：dispatcher 轮询 `recover_claimable`，任何一次启动 /
定时扫描都能把 PENDING / 过期 RUNNING 重新 claim（不变量 18）。本模块提供的是
尽力而为的即时唤醒 + 一定做得到的持久扫描的组合。

30 分钟自治（文档 §2.2 智能路）= 这层的另一条 入口：不依赖任何点击，后台每
`autonomy_seconds` 秒扫一次全书，把缺总结 / 不对齐的章写进同一个
`chapter_refresh_attempt`，然后由同一条 pump 波次把它们变成真实结果。调度坐标
（`draft_chapter` / 焦点豁免）来自 focus 模块；「哪章该补」是纯查库的
`summary_schedule` 决定，不调 LLM（§8）。

**这条循环是主路，保存不是必需**（2026-08-22）：作者在 WPS 里改稿、导入一整本
写好的书、进程崩掉——每一种都不经过保存，只要覆写依赖保存就必漏。所以扫描自给
自足：要干活的章**全部**进候选池（权重只分配一轮的名额，不当准入门槛），一轮取
`autonomy_limit` 个，取不完下一轮接着取，直到池子空。补完就停 —— 幂等收敛，
不反复花钱。
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import threading
import time
from typing import Any

from ..advisory_review import Reviewer, review_saved_chapter
from ..chapter_refresh import (
    BranchAdapter,
    ChapterRefreshCoordinator,
    attempt_chapter,
    recover_claimable,
)
from ..checks.service import current_ruleset
from ..db import Connection
from ..draft.rolling_summary import RollingSummarizer
from ..extract.runner import ExtractionRunner
from ..focus import is_focused, resolve_draft_origin
from ..graph.sqlite_store import SqliteStoryGraph
from ..project import list_all
from ..summary_schedule import (
    QUEUED_OUTCOMES,
    book_summary_status,
    reconcile_anomaly_notifications,
    schedule_alignment,
)
from ..system_notifications import materialize_notification_outbox

__all__ = ["BackgroundRuntime", "build_runtime", "new_connection_factory"]

_log = logging.getLogger(__name__)

AUTONOMY_INTERVAL: float = 30 * 60.0
"""自治调度默认间隔：30 分钟（文档 §2.2）。可注入（测试/演示用短间隔）。"""

AUTONOMY_LIMIT: int = 20
"""每一轮每个项目的**名额**（文档 §4 / §6 `limit`）。

不是上限：权重只决定这 20 个名额给谁，排不上的下一轮接着排。一本 158 章的旧书
刚接进来时会连补约 4 小时（每半小时 20 章），补完就停。"""


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
        reviewer_factory: Callable[[], Reviewer] | None = None,
        connection_factory: Callable[[], Connection] | None = None,
        owner: str = "background",
        poll_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        autonomy_seconds: float = AUTONOMY_INTERVAL,
        autonomy_limit: int = AUTONOMY_LIMIT,
    ) -> None:
        self._db_path = db_path
        self._conn_factory = connection_factory or new_connection_factory(db_path)
        self._runner_factory = runner_factory
        self._summarizer_factory = summarizer_factory
        # `None` = 这个运行时不做事后语义核对（桩运行时、只验调度的测试）。**不是降级**：
        # 核对是加在 DAG 后面的一件事，没有它前面每一支的行为一字不变。
        self._reviewer_factory = reviewer_factory
        self._owner = owner
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        self._autonomy_seconds = autonomy_seconds
        self._autonomy_limit = autonomy_limit
        self._next_autonomy = time.monotonic() + self._autonomy_seconds
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="dsh-background", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def pump_once(self) -> int:
        """跑一波：claim 可做的 attempt 并执行固定 DAG。返回执行了几条。

        **返回的是「执行了几条 attempt」，不是「花了几次钱」**：协调器只跑单上排了的
        那几支（`missing_branch_mask`），焦点防抖剥掉总结位的那种单跑完一分钱都不花。
        """
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
                # **但必须留痕**：这里从前是纯 `pass`，于是一个跨线程连接把整条
                # 执行线掐死了一周，而作者屏幕上只是「总结一直没补上」——
                # 后台的失败形态是「什么都没发生」，不留痕就没有任何人看得见。
                _log.exception("后台执行波次失败：这一波的 attempt 全部没跑完")
            if time.monotonic() >= self._next_autonomy:
                try:
                    self.autonomy_once()
                except Exception:
                    # 单轮自治失败不炸线程：下一轮再扫（attempt 是持久重试的基础）。
                    _log.exception("后台扫描轮失败：这一轮没给任何章下单")
                self._next_autonomy = time.monotonic() + self._autonomy_seconds
            self._sleep(self._poll_seconds)

    def autonomy_once(self) -> int:
        """30 分钟自治的一轮：扫全部项目，给缺总结/不对齐的章建 attempt。

        只写 `chapter_refresh_attempt`（系统记录）并 commit，不付模型——真正生成
        由后续 `pump_once` 的 adapter 承担（文档 §2.3：「只补缺的那一步」）。返回
        本轮入队条数。单项目失败（库/规则集缺行）不阻断其他项目，下一轮重试。
        """
        conn = self._conn_factory()
        try:
            enqueued = 0
            for book in list_all(conn):
                project_id = book.id
                try:
                    draft_chapter, focused_chapter = self._resolve_draft(
                        conn, project_id
                    )
                    epoch, ruleset_hash = current_ruleset(conn, project_id)
                    decisions = schedule_alignment(
                        conn,
                        project_id,
                        draft_chapter=draft_chapter,
                        ruleset_epoch=epoch,
                        ruleset_hash=ruleset_hash,
                        focused_chapter=focused_chapter,
                        limit=self._autonomy_limit,
                    )
                    # Step 4：异常标记 → background_failure 通知（同一事务，§5/§6）。
                    statuses = book_summary_status(
                        conn, project_id, draft_chapter=draft_chapter
                    )
                    reconcile_anomaly_notifications(conn, project_id, statuses)
                    conn.commit()
                    enqueued += sum(
                        1
                        for outcome in decisions.values()
                        if outcome in QUEUED_OUTCOMES
                    )
                except Exception:  # noqa: BLE001
                    # 单项目失败不拖垮整轮（§6 纪律的聚合层）；下一轮会自动重扫。
                    conn.rollback()
            return enqueued
        finally:
            conn.close()

    def _resolve_draft(self, conn: Connection, project_id: str) -> tuple[int, int | None]:
        """本轮调度的坐标 `(draft_chapter, focused_chapter)`（文档 §3/§4）。"""
        return resolve_draft_origin(conn, project_id)

    def _run_one(self, attempt_id: str, token: int) -> None:
        """跑一条 attempt 的固定 DAG，**再做两件不属于 DAG 的事**。

        两件都在闸门外边，因为它们都不许影响「这一章能不能被整理」：

        1. **物化通知 outbox。** `enqueue_*` 只写 outbox（和业务状态同事务，不变量 29），
           **总得有人把它搬进 `system_notification`**，否则作者的右栏永远看不见那条
           已经落库的通知。这里是那个消费者。无条件做——阻断那一条正是这么来的。
        2. **事后语义核对**（`advisory_review`）。两道门都要过：
           - **闸门放行了**——一章已经判定阻断的正文不值得再为它花一次模型调用；
           - **这一版正文是作者存出来的**（`AttemptTarget.authored`）。接一本 158 章
             的旧书进来时，补总结那一轮会为每一章下一张单，**在那儿顺手核对一遍等于
             把接书的成本翻一倍**。这一批做的是「保存之后验一遍」，不是「扫全书找矛盾」
             ——后者是另一个决定（它有它自己的成本和一次几十条通知的噪声），
             要做就单独做，别从这条缝里溜进来。
        """
        conn = self._conn_factory()
        try:
            # **协调器用的必须是这条线程自己刚开的连接。** sqlite3 的连接默认
            # `check_same_thread=True`：在别的线程上碰它，第一条 SELECT 就抛
            # `ProgrammingError`。从前这里用的是构造时（lifespan 协程所在的线程）
            # 开的那一条长命连接，而 `_loop` 跑在 `dsh-background` 线程上——
            # 于是**每一波都死在第一条 attempt 的第一次查询上**，被 `_loop` 那个
            # `except Exception` 吞掉：claim 照旧发生（`fencing_token` 一路涨），
            # 分支状态一个都不动，什么日志都没有。真书 book.db 就是这么在
            # 135 章缺总结上卡了一周（2026-09-05 修）。
            # 每条 attempt 现开现关也顺手把那条永不关闭的连接去掉了。
            coordination = ChapterRefreshCoordinator(
                conn,
                connection_factory=self._conn_factory,
                store_factory=lambda c: SqliteStoryGraph(c),
            )
            outcome = coordination.run(
                attempt_id,
                owner=self._owner,
                token=token,
                summary_adapter=_SummaryAdapter(self._summarizer_factory),
                extraction_adapter=_ExtractionAdapter(self._runner_factory),
                alias_adapter=None,
            )
            target = attempt_chapter(conn, attempt_id)
            if target is None:
                return
            if outcome.get("validation") == "passed" and target.authored:
                self._review_chapter(conn, target.project_id, target.chapter_number)
            materialize_notification_outbox(
                conn, project_id=target.project_id, lease_owner=self._owner
            )
        finally:
            conn.close()

    def _review_chapter(self, conn: Connection, project_id: str, chapter: int) -> None:
        """保存之后的那一遍语义核对（M1-b / 轨道阶段 2）。**只告警，不阻断，不抛。**

        ── 为什么这里也问一次焦点 ──────────────────────────────────────────
        它是**第二个会花钱的后台动作**，所以它适用和总结那一支一模一样的那条防抖
        （2026-08-18 §3）：作者正盯着的那一章，他还在改，这一版正文不算数。不问的话
        「改一下午存三十次」就变成三十次核对——那正是总结那一支刚修好的病，
        换个模块又长一遍。**判据借的是同一个 `is_focused`，不是第二份口径。**

        （真被连着叫两次也不会连着付两次钱：核对器的幂等判据是 prompt 的内容哈希。）
        """
        if self._reviewer_factory is None:
            return
        if is_focused(conn, project_id, chapter):
            return
        try:
            review_saved_chapter(
                conn,
                SqliteStoryGraph(conn),
                project_id,
                chapter,
                reviewer=self._reviewer_factory(),
            )
        except Exception:  # noqa: BLE001
            # 核对器自己已经把两问各自的失败收成一句 note；这一层兜住的是它外面那圈
            # （模型没配好、装配炸了、库被别的连接锁着）。**这条路是后台的，它的失败
            # 形态是「什么都没发生」，不是作者屏幕上的一个栈。**
            pass


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
    kwargs.setdefault("reviewer_factory", deps.get_advisory_reviewer)
    return BackgroundRuntime(db_path=db_path, **kwargs)
