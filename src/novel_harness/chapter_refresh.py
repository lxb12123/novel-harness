"""固定章节刷新协调器 —— 唯一的自动流水线（ADR 0029 / 计划 Task 5）。

**不提供通用 DAG API。** 它只表达这一条固定 DAG：

    正文验证器（availability 先行；blocked/error 停下游）
        → 总结 ∥ 抽取（两个**独立连接**并行）
        → final gate（alias 阶段确认后才 PASSED）

并且只消费持久 `chapter_refresh_run / chapter_refresh_attempt`（018 迁移）——
进程提交后立刻退出也不能漏刷新（不变量 20 的持久一半）。

Task 5 只交付协调器本身，用 stub adapter 证明固定 DAG、幂等 coverage、lease/fencing
与真实并行。**Task 16 接通的只有保存那一条**（`api/app.py::_trigger_refresh`）：
换章那条不但没接，反而被摘掉了——作者切走不等于他保存过，凭空付费总结/抽取是
「双重 autopilot」的旧设计。换章今天只上报一个免费焦点心跳（`focus.py`）。
summary 的 head CAS（Task 6）和抽取的 application CAS（Task 9）届时挂到同一套
claim/lease/fence 上。

── lease 与 fencing token 的口径（不变量 17）──────────────────────────────

lease 只控制**谁可以工作**，不代表模型只调用一次。claim 在事务中递增单调
`fencing_token`；所有状态写入必须带 claimed token 且 lease_owner 匹配，否则 CAS
失败。旧 token 即使 provider 晚到也只能留审计。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from .checks.service import SnapshotValidationReport, validate_snapshot
from .db import Connection
from .graph import ChapterCommitToken, StoryGraph
from .ids import EntityType, new_id
from .text import paragraphs as split_paragraphs


BRANCH_STATES: Final = (
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "REUSED",
    "BLOCKED",
    "FAILED",
    "SUPERSEDED",
)

TERMINAL_BRANCH_STATES: Final = frozenset({"SUCCEEDED", "REUSED", "BLOCKED", "FAILED", "SUPERSEDED"})

TERMINAL_ALIAS_PHASES: Final = frozenset({"UNCHANGED", "COMPLETE", "FAILED_BEFORE_CHANGE"})


class AttemptNotFound(RuntimeError):
    """claim/run 时 attempt 行不见了（被删 = 数据损坏，不是正常流程）。"""


class BranchAdapter(Protocol):
    """一个下游分支的 worker 契约（Task 5 的 stub / Task 6/9 的真 worker）。"""

    def run(self, ctx: BranchContext) -> str:
        """在独立连接上干活，返回给 attempt 状态列填的 note。"""
        ...


@dataclass(frozen=True, slots=True)
class BranchContext:
    """下游 worker 的只读上下文。token 是唯一正文输入（ADR 0029）。"""

    project_id: str
    chapter_id: str
    chapter_number: int
    token: ChapterCommitToken
    ruleset_epoch: int
    ruleset_hash: str
    attempt_id: str
    fencing_token: int
    lease_owner: str
    conn: Connection
    store: StoryGraph


@dataclass(frozen=True, slots=True)
class CoverageDecision:
    """`ensure_refresh_coverage` 的结论：哪些分支缺、复用还是新建 attempt。"""

    missing_branch_mask: int
    attempt_id: str | None
    reused: bool
    processing: Literal["reused", "queued", "attention_required"]


BRANCH_VALIDATION: Final = 1
BRANCH_SUMMARY: Final = 2
BRANCH_EXTRACTION: Final = 4


def _run_row(conn: Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, project_id, chapter_id, source_snapshot_id, source_generation "
        "FROM chapter_refresh_run WHERE id = ?",
        (run_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def find_run(
    conn: Connection, project_id: str, chapter_id: str, generation: int
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, project_id, chapter_id, source_snapshot_id, source_generation
          FROM chapter_refresh_run
         WHERE project_id = ? AND chapter_id = ? AND source_generation = ?
        """,
        (project_id, chapter_id, generation),
    ).fetchone()
    return dict(row) if row is not None else None


def create_run(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    generation: int,
    retired_edge_ids: Sequence[str] = (),
    retired_event_ids: Sequence[str] = (),
    retired_knower_event_ids: Sequence[str] = (),
    canon_version_before: int | None = None,
    canon_version_after: int | None = None,
) -> str:
    """一个 (project, chapter, generation) 的 run 行（与 generation 指针同事务）。

    调用方（保存路径 / 迁移回填）负责把它放进同一个事务；这里不自己 BEGIN。
    """
    import json

    run_id = new_id(EntityType.REFRESH_RUN, project_id)
    conn.execute(
        """
        INSERT INTO chapter_refresh_run (
            id, project_id, chapter_id, source_snapshot_id, source_generation,
            retired_edge_ids_json, retired_event_ids_json, retired_knower_event_ids_json,
            canon_version_before, canon_version_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            project_id,
            chapter_id,
            snapshot_id,
            generation,
            json.dumps(list(retired_edge_ids), ensure_ascii=False),
            json.dumps(list(retired_event_ids), ensure_ascii=False),
            json.dumps(list(retired_knower_event_ids), ensure_ascii=False),
            canon_version_before,
            canon_version_after,
        ),
    )
    return run_id


def _head_missing(conn: Connection, project_id: str, chapter_id: str) -> bool:
    """**自动**派活时，这一章算不算「缺滚动总结」（019 之前恒 True；Task 6 接真表）。

    缺 = 这一章从来没有过总结（没有 head 行，或 head 还指着 NULL）。

    ── ⚠️ 撤回过的章**不算缺** ────────────────────────────────────────────────
    这是本函数唯一容易写反的地方，而写反的代价是**花作者的钱去抹掉他刚做的动作**：
    他点了「撤回这一章的总结」，下一次保存就被系统重新买一份回来。
    ARCHITECTURE 把这条写死过：「`get()` / `latest()` 的差别是钱……用 `get()` 的话
    作者撤掉的那一章会在他保存后下一秒被自动买回来——一次他没按过的付费调用，
    顺带抹掉他刚做的动作」。2026-08-20 合并保存闭环任务时实测发现代码和这句话相反
    （判据写的是 `status = 'ACTIVE'`，于是 RETRACTED 被当成「缺」），且没有任何测试
    盖着——**这半条纪律原本只由已删掉的 `api/autopilot.py` 实现着**（它拿 `latest()`
    + `retracted` 标志跳过），随那个模块一起没了。现在由
    `test_a_retracted_summary_is_not_bought_back_by_the_next_save` 钉住。

    **和 `SummaryStore.coverage()` 不矛盾**：那边是给界面看的读端，撤回后照旧显示
    「这一章没有总结」（作者要看得见自己撤了）。这边回答的是另一个问题——
    **系统该不该自己掏钱补一份**。答案是不该：想要新的一份他自己点「重新生成」
    （走 `create_manual_attempt` / `POST …/summary`，他按的，所以花钱合理）。
    """
    row = conn.execute(
        """
        SELECT 1
          FROM chapter_summary_head h
         WHERE h.chapter_id = :cid
           AND h.current_summary_id IS NOT NULL
        """,
        {"cid": chapter_id},
    ).fetchone()
    return row is None


def _application_missing(conn: Connection, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM extraction_application_head WHERE refresh_run_id = ?",
        (run_id,),
    ).fetchone()
    return row is None


def activate_extraction_application(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    generation: int,
    analysis_run_id: str,
    ruleset_epoch: int | None,
    ruleset_hash: str | None,
    resolution_hash: str | None = None,
) -> tuple[str, str]:
    """抽取 run 成功后，在 (project, chapter, generation) 的 refresh run 上建立 application。

    返回 `(application_id, status)`，status 是 `CURRENT` 或 `SUPERSEDED`。

    ── 为什么必须走 head 的 intent CAS（021 / Task 9）────────────────────────

    同一个 generation 可以有多个合法 attempt（save / manual / ruleset / replay），
    各自持有自己的 fencing token。它们竞争的是**同一个** `extraction_application_head`
    （以 refresh run 为主键）：建 application 时原子递增 `intent_seq`，只有最新 intent
    可以成为 CURRENT；旧 intent 的 application 即使模型晚到也只能留审计。
    DB 的 partial unique（每个 refresh run 至多一条 CURRENT）再兜一道底——
    两个 attempt 不能各自建 head 绕过竞争。

    `conn` 必须在**无外层事务**的连接上（调用方已把 ingest 业务事务 commit 完）。
    """
    run = find_run(conn, project_id, chapter_id, generation)
    if run is None:
        run_id = create_run(
            conn,
            project_id=project_id,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=generation,
        )
    else:
        run_id = run["id"]
    # 原子递增 intent：两个并发 attempt 各自拿到不同序号，且序号单调。
    row = conn.execute(
        """
        INSERT INTO extraction_application_head (refresh_run_id, intent_seq)
        VALUES (?, 1)
        ON CONFLICT (refresh_run_id)
        DO UPDATE SET intent_seq = extraction_application_head.intent_seq + 1,
                      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        RETURNING intent_seq
        """,
        (run_id,),
    ).fetchone()
    intent_seq = int(row["intent_seq"])
    application_id = new_id(EntityType.EXTRACTION_APPLICATION, project_id)
    conn.execute(
        """
        INSERT INTO extraction_application (
            id, project_id, refresh_run_id, analysis_run_id, snapshot_id,
            source_generation, resolution_hash, ruleset_epoch, ruleset_hash,
            required_intent_seq, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STAGED')
        """,
        (
            application_id,
            project_id,
            run_id,
            analysis_run_id,
            snapshot_id,
            generation,
            resolution_hash,
            ruleset_epoch,
            ruleset_hash,
            intent_seq,
        ),
    )
    # 只有 head 的**最新 intent** 可以成为 CURRENT：先退休旧 CURRENT（它的 intent
    # 已经落后），再把这条 CAS 成 CURRENT。partial unique 防止两个 intent 同时自称
    # CURRENT，但 CAS 前置的「退休旧 CURRENT」才是语义闸——SQLite 不保证两个
    # 事务按 intent 顺序提交，必须显式比 intent。
    conn.execute(
        """
        UPDATE extraction_application
           SET status = 'SUPERSEDED'
         WHERE refresh_run_id = :run_id
           AND status = 'CURRENT'
           AND required_intent_seq < :intent
        """,
        {"run_id": run_id, "intent": intent_seq},
    )
    activated = conn.execute(
        """
        UPDATE extraction_application
           SET status = 'CURRENT'
         WHERE id = :app_id
           AND status = 'STAGED'
           AND required_intent_seq = (
                 SELECT intent_seq FROM extraction_application_head
                  WHERE refresh_run_id = :run_id
               )
        """,
        {"app_id": application_id, "run_id": run_id},
    )
    status = "CURRENT" if activated.rowcount == 1 else "SUPERSEDED"
    if status == "SUPERSEDED":
        conn.execute(
            "UPDATE extraction_application SET status = 'SUPERSEDED' WHERE id = ?",
            (application_id,),
        )
    conn.execute(
        "UPDATE extraction_application_head SET current_application_id = ? "
        "WHERE refresh_run_id = ? AND intent_seq = ?",
        (application_id if status == "CURRENT" else None, run_id, intent_seq),
    )
    conn.commit()
    return application_id, status


def ensure_refresh_coverage(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    generation: int,
    ruleset_epoch: int,
    ruleset_hash: str,
    expected_summary_head: str | None = None,
    missing_check: Callable[[Connection, str, str, str], int] | None = None,
    skip_summary: bool = False,
) -> CoverageDecision:
    """同 hash 保存的幂等补缺：只为缺失分支建 coverage attempt，不重复付费。

    - 覆盖完整 → `reused`（不建 attempt，复用已有结果）。
    - 有缺口 → 复用同 trigger key 的既有 attempt（幂等），否则新建并返回 `queued`。
    - 同 basis 已有 terminal FAILED/BLOCKED → `attention_required`，不自动重付；
      只有显式「重新整理/重新总结」创建新的 manual intent。

    `missing_branch_mask` 的位：1=验证报告，2=总结 head，4=抽取 application。
    """
    run = find_run(conn, project_id, chapter_id, generation)
    if run is None:
        run_id = create_run(
            conn,
            project_id=project_id,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=generation,
        )
    else:
        run_id = run["id"]

    if missing_check is not None:
        mask = missing_check(conn, project_id, chapter_id, run_id)
    else:
        mask = _default_missing_mask(
            conn, project_id, chapter_id, run_id, snapshot_id, generation, ruleset_epoch
        )
    if skip_summary:
        # 当前章防抖（2026-08-18 §3）：作者正盯着的那一章不排总结分支，别的照跑。
        mask &= ~BRANCH_SUMMARY

    if mask == 0:
        return CoverageDecision(0, None, reused=True, processing="reused")

    trigger_key = f"coverage:{mask}"
    row = conn.execute(
        """
        SELECT id, summary_state, extraction_state, validation_state
          FROM chapter_refresh_attempt
         WHERE run_id = ? AND workflow_version = 1 AND ruleset_epoch = ?
           AND trigger_kind = 'coverage' AND missing_branch_mask = ?
        """,
        (run_id, ruleset_epoch, mask),
    ).fetchone()
    if row is not None:
        any_failed = any(
            row[key] in ("FAILED", "BLOCKED")
            for key in ("validation_state", "summary_state", "extraction_state")
        )
        if any_failed:
            return CoverageDecision(mask, row["id"], reused=True, processing="attention_required")
        return CoverageDecision(mask, row["id"], reused=True, processing="queued")

    attempt_id = new_id(EntityType.REFRESH_ATTEMPT, project_id)
    conn.execute(
        """
        INSERT INTO chapter_refresh_attempt (
            id, run_id, workflow_version, ruleset_epoch, ruleset_hash,
            trigger_kind, trigger_key, missing_branch_mask, expected_summary_head,
            validation_state, summary_state, extraction_state
        ) VALUES (?, ?, 1, ?, ?, 'coverage', ?, ?, ?, 'PENDING', 'PENDING', 'PENDING')
        """,
        (attempt_id, run_id, ruleset_epoch, ruleset_hash, trigger_key, mask, expected_summary_head),
    )
    return CoverageDecision(mask, attempt_id, reused=False, processing="queued")


def _default_missing_mask(
    conn: Connection,
    project_id: str,
    chapter_id: str,
    run_id: str,
    snapshot_id: str,
    generation: int,
    ruleset_epoch: int,
) -> int:
    """默认的缺口计算：验证报告 / 总结 head / 抽取 application 三个分支。"""
    mask = 0
    report = conn.execute(
        """
        SELECT 1 FROM validation_report
         WHERE project_id = ? AND chapter_snapshot_id = ?
           AND source_generation = ? AND ruleset_epoch = ?
         LIMIT 1
        """,
        (project_id, snapshot_id, generation, ruleset_epoch),
    ).fetchone()
    if report is None:
        mask |= BRANCH_VALIDATION
    if _head_missing(conn, project_id, chapter_id):
        mask |= BRANCH_SUMMARY
    if _application_missing(conn, run_id):
        mask |= BRANCH_EXTRACTION
    return mask


def create_manual_attempt(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    generation: int,
    ruleset_epoch: int,
    ruleset_hash: str,
    trigger_key: str,
    missing_branch_mask: int = BRANCH_VALIDATION | BRANCH_SUMMARY | BRANCH_EXTRACTION,
    expected_summary_head: str | None = None,
) -> str:
    """显式「重新整理」：新的 manual intent，不能被 coverage 唯一键吞掉。"""
    run = find_run(conn, project_id, chapter_id, generation)
    if run is None:
        run_id = create_run(
            conn,
            project_id=project_id,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=generation,
        )
    else:
        run_id = run["id"]
    attempt_id = new_id(EntityType.REFRESH_ATTEMPT, project_id)
    conn.execute(
        """
        INSERT INTO chapter_refresh_attempt (
            id, run_id, workflow_version, ruleset_epoch, ruleset_hash,
            trigger_kind, trigger_key, missing_branch_mask, expected_summary_head,
            validation_state, summary_state, extraction_state
        ) VALUES (?, ?, 1, ?, ?, 'manual', ?, ?, ?, 'PENDING', 'PENDING', 'PENDING')
        """,
        (attempt_id, run_id, ruleset_epoch, ruleset_hash, trigger_key, missing_branch_mask, expected_summary_head),
    )
    return attempt_id


def claim_attempt(
    conn: Connection,
    attempt_id: str,
    *,
    owner: str,
    ttl_seconds: float = 60.0,
    now: float | None = None,
) -> int | None:
    """原子 claim：拿到单调 fencing token。返回 None = 不可领（未过期/已终态）。"""
    now = time.time() if now is None else now
    expires = _iso(now + ttl_seconds)
    row = conn.execute(
        """
        UPDATE chapter_refresh_attempt
           SET lease_owner = :owner, lease_expires_at = :expires,
               fencing_token = fencing_token + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE id = :id
           AND (lease_expires_at IS NULL OR lease_expires_at <= :now)
           AND ((validation_state IN ('PENDING','RUNNING')
                 OR summary_state IN ('PENDING','RUNNING')
                 OR extraction_state IN ('PENDING','RUNNING'))
                OR (final_gate_state = 'PENDING'
                    AND validation_state IN ('SUCCEEDED','REUSED')))
        RETURNING fencing_token
        """,
        {"id": attempt_id, "owner": owner, "expires": expires, "now": _iso(now)},
    ).fetchone()
    conn.commit()
    return int(row["fencing_token"]) if row is not None else None


def heartbeat(
    conn: Connection, attempt_id: str, *, owner: str, token: int, ttl_seconds: float
) -> bool:
    """在 TTL/3 前续租；owner+token 不匹配 = False（lease 已被抢走）。"""
    row = conn.execute(
        """
        UPDATE chapter_refresh_attempt
           SET lease_expires_at = :expires,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE id = :id AND lease_owner = :owner AND fencing_token = :token
        RETURNING id
        """,
        {"id": attempt_id, "owner": owner, "expires": _iso(time.time() + ttl_seconds), "token": token},
    ).fetchone()
    conn.commit()
    return row is not None


def release_attempt(conn: Connection, attempt_id: str, *, owner: str, token: int) -> None:
    conn.execute(
        """
        UPDATE chapter_refresh_attempt
           SET lease_owner = NULL, lease_expires_at = NULL
         WHERE id = :id AND lease_owner = :owner AND fencing_token = :token
        """,
        {"id": attempt_id, "owner": owner, "token": token},
    )
    conn.commit()


def recover_claimable(
    conn: Connection, *, owner: str, ttl_seconds: float = 60.0
) -> list[tuple[str, int]]:
    """启动恢复：扫描所有可领的 attempt（PENDING/过期 RUNNING），逐个 claim。

    返回 `(attempt_id, fencing_token)`——调用方拿着 token 直接 run，**不要再 claim
    一次**（同一 owner 的未过期 lease 会让第二次 claim 返回 None，跑不出来）。
    """
    rows = conn.execute(
        """
        SELECT id FROM chapter_refresh_attempt
         WHERE (lease_expires_at IS NULL OR lease_expires_at < :now)
           AND ((validation_state IN ('PENDING','RUNNING')
                 OR summary_state IN ('PENDING','RUNNING')
                 OR extraction_state IN ('PENDING','RUNNING'))
                OR (final_gate_state = 'PENDING'
                    AND validation_state IN ('SUCCEEDED','REUSED')))
        """,
        {"now": _iso(time.time())},
    ).fetchall()
    claimed: list[tuple[str, int]] = []
    for row in rows:
        token = claim_attempt(conn, row["id"], owner=owner, ttl_seconds=ttl_seconds)
        if token is not None:
            claimed.append((str(row["id"]), token))
    conn.commit()
    return claimed


def _iso(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%fZ")


def _branch_update(
    conn: Connection,
    attempt_id: str,
    *,
    owner: str,
    token: int,
    column: str,
    state: str,
    reused_id: str | None = None,
) -> bool:
    if reused_id is not None:
        reused_column = {
            "validation_state": "reused_validation_report_id",
            "summary_state": "reused_summary_version_id",
            "extraction_state": "reused_application_id",
        }[column]
        row = conn.execute(
            f"""
            UPDATE chapter_refresh_attempt
               SET {column} = :state, {reused_column} = :reused_id,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = :id AND lease_owner = :owner AND fencing_token = :token
            RETURNING id
            """,
            {"id": attempt_id, "owner": owner, "token": token, "state": state, "reused_id": reused_id},
        ).fetchone()
    else:
        row = conn.execute(
            f"""
            UPDATE chapter_refresh_attempt
               SET {column} = :state, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = :id AND lease_owner = :owner AND fencing_token = :token
            RETURNING id
            """,
            {"id": attempt_id, "owner": owner, "token": token, "state": state},
        ).fetchone()
    conn.commit()
    return row is not None


class ChapterRefreshCoordinator:
    """一条 attempt 的固定 DAG 执行器。

    Args:
        conn: 协调器主连接（validation / 状态写）。
        connection_factory: 每个下游 worker 的独立连接（真并行需要各自连接）。
        store_factory: 每个下游 worker 的 store（独立连接上的 StoryGraph）。
    """

    def __init__(
        self,
        conn: Connection,
        *,
        connection_factory: Callable[[], Connection],
        store_factory: Callable[[Connection], StoryGraph],
    ) -> None:
        self._conn = conn
        self._connection_factory = connection_factory
        self._store_factory = store_factory

    def run(
        self,
        attempt_id: str,
        *,
        owner: str,
        token: int,
        summary_adapter: BranchAdapter,
        extraction_adapter: BranchAdapter,
        alias_adapter: BranchAdapter | None = None,
        ttl_seconds: float = 60.0,
    ) -> dict[str, str]:
        """执行固定 DAG：验证 → alias 阶段 → 总结 ∥ 抽取 → final gate。"""
        attempt = self._attempt(attempt_id)
        if attempt is None:
            raise AttemptNotFound(attempt_id)
        run = _run_row(self._conn, attempt["run_id"])
        if run is None:
            raise AttemptNotFound(attempt_id)
        if attempt["validation_state"] in ("BLOCKED", "FAILED", "SUPERSEDED"):
            return {"validation": attempt["validation_state"].lower()}
        token_ctx = ChapterCommitToken(
            project_id=run["project_id"],
            chapter_id=run["chapter_id"],
            chapter_number=self._chapter_number(run["project_id"], run["chapter_id"]),
            source_snapshot_id=run["source_snapshot_id"],
            source_generation=int(run["source_generation"]),
            text_sha256="",
            text="",
            changed=False,
        )
        snapshot = self._snapshot_text(run["project_id"], run["source_snapshot_id"])
        if snapshot is None:
            _branch_update(
                self._conn, attempt_id, owner=owner, token=token,
                column="validation_state", state="FAILED",
            )
            return {"validation": "FAILED"}
        token_ctx = ChapterCommitToken(
            project_id=run["project_id"],
            chapter_id=run["chapter_id"],
            chapter_number=token_ctx.chapter_number,
            source_snapshot_id=run["source_snapshot_id"],
            source_generation=int(run["source_generation"]),
            text_sha256=self._conn.execute(
                "SELECT text_sha256 FROM chapter_snapshot WHERE id = ?",
                (run["source_snapshot_id"],),
            ).fetchone()["text_sha256"],
            text=snapshot,
            changed=False,
        )

        epoch = int(attempt["ruleset_epoch"])
        ruleset_hash = attempt["ruleset_hash"]
        branch_ctx = BranchContext(
            project_id=run["project_id"],
            chapter_id=run["chapter_id"],
            chapter_number=token_ctx.chapter_number,
            token=token_ctx,
            ruleset_epoch=epoch,
            ruleset_hash=ruleset_hash,
            attempt_id=attempt_id,
            fencing_token=token,
            lease_owner=owner,
            conn=self._conn,
            store=self._store_factory(self._conn),
        )

        # ── ① 正文验证（快照绑定）────────────────────────────────────────
        validation: SnapshotValidationReport | None = None
        if attempt["validation_state"] in ("PENDING", "RUNNING"):
            _branch_update(
                self._conn, attempt_id, owner=owner, token=token,
                column="validation_state", state="RUNNING",
            )
            try:
                validation = validate_snapshot(
                    self._conn,
                    branch_ctx.store,
                    token_ctx,
                    ruleset_epoch=epoch,
                    ruleset_hash=ruleset_hash,
                    phase="initial",
                    refresh_attempt_id=attempt_id,
                    paragraphs=split_paragraphs(snapshot),
                )
            except Exception as exc:  # noqa: BLE001 —— store/规则异常按 error 阻断
                _branch_update(
                    self._conn, attempt_id, owner=owner, token=token,
                    column="validation_state", state="FAILED",
                )
                return {"validation": "FAILED", "error": str(exc)}
            if validation.gate in ("blocked", "error"):
                # 029 不变量：final gate 变 BLOCKED 与通知 outbox **同事务**。
                # 通知可以晚显示，不能因进程在两步之间退出而永久丢失。
                if validation.gate == "blocked":
                    from .system_notifications import (
                        background_failure_dedupe_key,
                        enqueue_notification,
                    )

                    enqueue_notification(
                        self._conn,
                        project_id=branch_ctx.project_id,
                        kind="validation_blocked",
                        subject_type="chapter",
                        subject_id=branch_ctx.chapter_id,
                        chapter_number=branch_ctx.chapter_number,
                        title=(
                            f"第 {branch_ctx.chapter_number} 章的正文检查发现需要留意的地方，"
                            "新正文不会再自动生成总结与情节"
                        ),
                        dedupe_key=background_failure_dedupe_key(
                            kind="validation_blocked",
                            subject_type="chapter",
                            subject_id=branch_ctx.chapter_id,
                            operation=f"validation:{validation.id}",
                            source_snapshot_id=branch_ctx.token.source_snapshot_id,
                            job_id=attempt_id,
                        ),
                    )
                _branch_update(
                    self._conn, attempt_id, owner=owner, token=token,
                    column="validation_state", state="BLOCKED" if validation.gate == "blocked" else "FAILED",
                )
                return {"validation": validation.gate}
            _branch_update(
                self._conn, attempt_id, owner=owner, token=token,
                column="validation_state", state="SUCCEEDED",
            )
            self._set_initial_report(attempt_id, validation.id)

        # ── ② alias 阶段（Task 12 接真 adapter；Task 5 只接受注入）────────
        # NOT_STARTED/CHANGED 时 final gate 保持 PENDING：初次 PASSED 报告不能
        # 冒充最终闸门（别名可能补齐 R2/R3 的输入，二次验证才作数）。
        if attempt["alias_phase"] in ("NOT_STARTED", "CHANGED") and alias_adapter is not None:
            try:
                alias_note = alias_adapter.run(branch_ctx)
            except Exception as exc:  # noqa: BLE001
                self._set_alias_phase(attempt_id, "FAILED_BEFORE_CHANGE")
                return {"validation": "passed", "alias": f"FAILED_BEFORE_CHANGE: {exc}"}
            if alias_note == "changed":
                post = self._run_post_alias_validation(branch_ctx, token_ctx, epoch, ruleset_hash, attempt_id)
                if post is None:
                    _branch_update(
                        self._conn, attempt_id, owner=owner, token=token,
                        column="validation_state", state="BLOCKED",
                    )
                    self._set_alias_phase(attempt_id, "CHANGED")
                    return {"validation": "blocked_post_alias"}
                self._set_alias_phase(attempt_id, "COMPLETE")
                self._set_final_report(attempt_id, post.id)
            else:
                self._set_alias_phase(attempt_id, "UNCHANGED")

        # ── ③ final gate：alias 阶段终态 + 初次验证通过才 PASSED ─────────
        final_report_id = self._finalize_gate_if_terminal(attempt_id, owner=owner, token=token)

        # ── ④ 总结 ∥ 抽取：两个独立连接并行 ──────────────────────────────
        results: dict[str, str] = {}
        def _worker(column: str, adapter: BranchAdapter) -> None:
            worker_conn = self._connection_factory()
            try:
                state_row = worker_conn.execute(
                    f"SELECT {column} FROM chapter_refresh_attempt WHERE id = ?",
                    (attempt_id,),
                ).fetchone()
                if state_row is not None and state_row[column] in TERMINAL_BRANCH_STATES:
                    results[column] = "reused"
                    return
                worker_ctx = BranchContext(
                    project_id=branch_ctx.project_id,
                    chapter_id=branch_ctx.chapter_id,
                    chapter_number=branch_ctx.chapter_number,
                    token=token_ctx,
                    ruleset_epoch=epoch,
                    ruleset_hash=ruleset_hash,
                    attempt_id=attempt_id,
                    fencing_token=token,
                    lease_owner=owner,
                    conn=worker_conn,
                    store=self._store_factory(worker_conn),
                )
                _branch_update(
                    worker_conn, attempt_id, owner=owner, token=token,
                    column=column, state="RUNNING",
                )
                note = adapter.run(worker_ctx)
                _branch_update(
                    worker_conn, attempt_id, owner=owner, token=token,
                    column=column, state="SUCCEEDED",
                )
                results[column] = note
            except Exception as exc:  # noqa: BLE001
                _branch_update(
                    worker_conn, attempt_id, owner=owner, token=token,
                    column=column, state="FAILED",
                )
                results[column] = f"FAILED: {exc}"
            finally:
                worker_conn.close()

        threads = [
            threading.Thread(target=_worker, args=("summary_state", summary_adapter)),
            threading.Thread(target=_worker, args=("extraction_state", extraction_adapter)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return {"validation": "passed", "final_gate": final_report_id or "PENDING", **results}

    def _run_post_alias_validation(
        self,
        branch_ctx: BranchContext,
        token_ctx: ChapterCommitToken,
        epoch: int,
        ruleset_hash: str,
        attempt_id: str,
    ) -> SnapshotValidationReport | None:
        """别名集合变化后的二次验证（phase=post_alias）；blocked → None。"""
        report = validate_snapshot(
            self._conn,
            branch_ctx.store,
            token_ctx,
            ruleset_epoch=epoch,
            ruleset_hash=ruleset_hash,
            phase="post_alias",
            refresh_attempt_id=attempt_id,
            paragraphs=split_paragraphs(token_ctx.text),
        )
        if report.gate in ("blocked", "error"):
            return None
        return report

    def _attempt(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT id, run_id, ruleset_epoch, ruleset_hash, validation_state,
                   summary_state, extraction_state, alias_phase,
                   initial_validation_report_id, final_gate_state, final_validation_report_id
              FROM chapter_refresh_attempt WHERE id = ?
            """,
            (attempt_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _chapter_number(self, project_id: str, chapter_id: str) -> int:
        row = self._conn.execute(
            "SELECT number FROM chapter WHERE id = ? AND project_id = ?",
            (chapter_id, project_id),
        ).fetchone()
        return int(row["number"]) if row is not None else 0

    def _snapshot_text(self, project_id: str, snapshot_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT text FROM chapter_snapshot WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return str(row["text"]) if row is not None else None

    def _set_initial_report(self, attempt_id: str, report_id: str) -> None:
        self._conn.execute(
            """
            UPDATE chapter_refresh_attempt
               SET initial_validation_report_id = ?
             WHERE id = ?
            """,
            (report_id, attempt_id),
        )
        self._conn.commit()

    def _set_alias_phase(self, attempt_id: str, phase: str) -> None:
        self._conn.execute(
            """
            UPDATE chapter_refresh_attempt
               SET alias_phase = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (phase, attempt_id),
        )
        self._conn.commit()

    def _set_final_report(self, attempt_id: str, report_id: str) -> None:
        self._conn.execute(
            """
            UPDATE chapter_refresh_attempt
               SET final_validation_report_id = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (report_id, attempt_id),
        )
        self._conn.commit()

    def _finalize_gate_if_terminal(self, attempt_id: str, *, owner: str, token: int) -> str | None:
        attempt = self._attempt(attempt_id)
        if attempt is None:
            return None
        if attempt["final_gate_state"] != "PENDING":
            return attempt["final_validation_report_id"]
        if attempt["alias_phase"] not in TERMINAL_ALIAS_PHASES:
            return None
        if attempt["validation_state"] not in ("SUCCEEDED", "REUSED"):
            return None
        # COMPLETE 时 final report 是 post-alias 报告；否则用初次报告。
        final_report = attempt["final_validation_report_id"] or attempt["initial_validation_report_id"]
        row = self._conn.execute(
            """
            UPDATE chapter_refresh_attempt
               SET final_gate_state = 'PASSED', final_validation_report_id = :report,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = :id AND lease_owner = :owner AND fencing_token = :token
            RETURNING final_validation_report_id
            """,
            {"id": attempt_id, "report": final_report, "owner": owner, "token": token},
        ).fetchone()
        self._conn.commit()
        return str(row["final_validation_report_id"]) if row is not None else None
