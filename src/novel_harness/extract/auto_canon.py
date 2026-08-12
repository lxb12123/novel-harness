"""没进例外 bucket 的抽取结果自动升 CANON —— 静默生效，只留痕。

**这一步的前提是「退路先到位」**：作者事后改得掉（`corrections.py` + `/canon/…`）、
查得见（`decision_log`，`actor='system'`）。顺序反过来会有一段时间是「系统自动改你的书，
而你改不回来」，那正是 2026-08-10 那次裁决明确不要的东西。

── 三条不许动的性质 ───────────────────────────────────────────────────────

1. **静默。** 自动升不产生任何要作者点的东西：不推队列、不弹提示。三个例外 bucket
   （`edge_conflict` / `low_confidence_main` / `new_character`）照旧进提案队列，
   它们是作者唯一还愿意被打扰的地方。
2. **失败方向是 fail-safe。** 升不上去就让事实留在 PROVISIONAL——没升上去的事实不进
   写作上下文，那是安全的一侧。**绝不因此把整个抽取 run 标成失败**：正文已经抽完了，
   那次模型钱已经花了，把 run 标红只会让作者重跑一次再花一次。
3. **幂等靠 `request_hash`，不靠这里。** 同一组事实的第二次调用命中既有回执，
   既不再升一次也不再写一条日志（`proposal_confirm.py` 的既有机制）。

── 为什么不在 `ExtractionService.ingest()` 里做 ─────────────────────────────

`proposal_confirm._transaction` 明写「必须在无外层事务的连接上启动」，而 `ingest()` 全程
跑在 `runner._ingest_success` 的 `BEGIN IMMEDIATE` 里。所以自动升只能在那次 commit
**之后**发生，是两次事务不是一次——这也正好符合第 2 条：业务已经落库，升不升是另一件事。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import project
from ..db import Connection
from ..decisions import SYSTEM_ACTOR
from ..events import EventStore
from ..graph import GraphStore
from ..graph.sqlite_events import SqliteEventStore
from ..graph.sqlite_store import SqliteStoryGraph
from .ingest_helpers import ExtractionReport
from .proposal_confirm import confirm_provisional_edges, confirm_provisional_events

__all__ = ["AutoPromotion", "AutoPromotionFailure", "promote_clean_facts"]


class AutoPromotionFailure(BaseModel):
    """一组没升上去的事实。**它们仍是 PROVISIONAL，不是丢了。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_kind: Literal["event", "edge"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    reason: str


class AutoPromotion(BaseModel):
    """一次自动升 CANON 的结果。出参是 Pydantic（铁律 4），调用方不解 dict。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    canon_version: int = Field(ge=0)
    promoted_event_ids: tuple[str, ...] = ()
    promoted_edge_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    """这批事实归属的 `decision_log` 行。**幂等重跑时是同一批 id，不是新写的行**——
    判「有没有多写一条」要去数表，不能数这里。"""

    failures: tuple[AutoPromotionFailure, ...] = ()


def promote_clean_facts(
    conn: Connection,
    project_id: str,
    report: ExtractionReport,
    *,
    graph: GraphStore | None = None,
    events: EventStore | None = None,
) -> AutoPromotion:
    """把 `report` 里的干净事实升成 CANON，每组写一条 `actor='system'` 的决策日志。

    干净集合由 `ExtractionService.ingest()` 算好（落库的 − 进了 bucket 的），**这里不重算**：
    重算一遍就是第二份「什么算干净」的判据，而两份判据迟早会分岔。
    """
    if conn.in_transaction:
        # 这不是数据错误而是接线错误，所以它响：`confirm_provisional_*` 在外层事务里
        # 会静默地把「回执已提交」和「日志已提交」拆到两个不同的原子性里。
        raise RuntimeError("自动升 CANON 必须在无外层事务的连接上执行（业务事务已 commit 之后）")

    store = graph or SqliteStoryGraph(conn)
    event_store = events or SqliteEventStore(conn)
    promoted_events: tuple[str, ...] = ()
    promoted_edges: tuple[str, ...] = ()
    decision_ids: list[str] = []
    failures: list[AutoPromotionFailure] = []

    if report.clean_event_ids:
        try:
            confirmation = confirm_provisional_events(
                conn,
                store,
                event_store,
                project_id,
                report.clean_event_ids,
                expected_canon_version=project.require_canon_version(conn, project_id),
                actor=SYSTEM_ACTOR,
            )
        except Exception as exc:
            # 宽捕获是这里的**规格**不是偷懒：任何一种失败（版本竞争 / 已被别的回执认领 /
            # 边不再是 ACTIVE-FRESH / 日志写不下去）的正确动作都是同一个——留在 PROVISIONAL。
            failures.append(
                AutoPromotionFailure(
                    fact_kind="event",
                    fact_ids=report.clean_event_ids,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            promoted_events = report.clean_event_ids
            decision_ids.append(confirmation.decision_id)

    if report.clean_edge_ids:
        try:
            # 版本在这儿重读一次：上面那组升成功的话已经 bump 过了。
            confirmation = confirm_provisional_edges(
                conn,
                store,
                project_id,
                report.clean_edge_ids,
                expected_canon_version=project.require_canon_version(conn, project_id),
                actor=SYSTEM_ACTOR,
            )
        except Exception as exc:  # 同上：失败的方向永远是「不升」
            failures.append(
                AutoPromotionFailure(
                    fact_kind="edge",
                    fact_ids=report.clean_edge_ids,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            promoted_edges = report.clean_edge_ids
            decision_ids.append(confirmation.decision_id)

    return AutoPromotion(
        project_id=project_id,
        canon_version=project.require_canon_version(conn, project_id),
        promoted_event_ids=promoted_events,
        promoted_edge_ids=promoted_edges,
        decision_ids=tuple(decision_ids),
        failures=tuple(failures),
    )
