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
from .graph import ChapterCommitToken, RetirementReport, StoryGraph
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

SummaryAlignment = Literal["paired", "missing", "stale", "retracted"]
"""一章的总结与正文的关系（`summary_alignment` 的出参）。"""


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


@dataclass(frozen=True, slots=True)
class AttemptTarget:
    """一条 attempt 的坐标：**哪个项目的第几章、第几版正文**。"""

    project_id: str
    chapter_number: int
    source_generation: int

    @property
    def authored(self) -> bool:
        """这一版正文是**作者存出来的**，不是导进来的那一版。

        `snapshot_generation` 只在 text hash 真正切换时 +1（018）：`1` = 这一章自打
        进库就没被改过（`import_book` 落的那一版，或者刚 `append_chapter` 出来的空章），
        `≥ 2` = 作者（或写作助手）至少存过它一次。

        **判据在这儿，不在调度那一侧**：它是「保存之后」那句话唯一说得清的机械形态，
        而拿它当门槛的那件事（事后语义核对要不要花这一次钱）代价不对称——
        判错成真只是多花一次钱，判错成假是漏掉一次告警，而后者作者永远不会知道。
        """
        return self.source_generation > 1


def attempt_chapter(conn: Connection, attempt_id: str) -> AttemptTarget | None:
    """这条 attempt 干的是哪一章。`None` = 这条 attempt 已经不在了。

    调度那一侧（`api/background_runtime.py`）跑完固定 DAG 之后还要接着做两件不属于
    DAG 的事——物化通知 outbox、跑事后语义核对——而它手上只有一个 attempt id。
    坐标从这里出，**不在第二个地方再拼一遍那三张表的 JOIN**：拼错的后果是把一章的
    告警记到另一章头上，而通知看起来完全正常。
    """
    row = conn.execute(
        """
        SELECT r.project_id AS project_id, c.number AS number,
               r.source_generation AS generation
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
          JOIN chapter c ON c.id = r.chapter_id
         WHERE a.id = ?
        """,
        (attempt_id,),
    ).fetchone()
    if row is None:
        return None
    return AttemptTarget(
        project_id=str(row["project_id"]),
        chapter_number=int(row["number"]),
        source_generation=int(row["generation"]),
    )


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
    **系统该不该自己掏钱补一份**。答案是不该。

    ── ⚠️ 「撤回」之后**系统**不补，但**作者**按得回来（2026-09-05 定型）────────

    本函数的判据一个字没改过，改的是它旁边那颗按钮的存在与否，而两次都值得记着：

    - **2026-08-25**：手动入口（`POST …/summary` / `create_manual_attempt`）整条删掉，
      总结只剩两个自动触发。于是这条纪律的后果变成「撤回过的章永远拿不回机器总结」
      ——那**不是**当时想要的后果，只是没人算这一笔。
    - **2026-09-05**：`POST …/summary` 加回来了（维护者原话：「撤回了之后肯定这个
      底部要留一个重新生成的按钮供用户立即生成」）。

    **本函数照旧不动**：撤回过的章不算缺，系统永远不会自己掏钱把它买回来，
    包括作者后来又改了那一章的正文。分界线是**「谁按的、谁付钱」**——
    自动那条路不许花他没按过的钱去抹掉他刚做的动作；他自己按的那颗按钮不受这条约束。
    所以撤回之后有两条回头路，都由他发起：自己写一段（`PATCH …/summary`，不花钱），
    或者点「重新生成」（`POST …/summary`，跑一次模型）。
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


def _head_outdated(conn: Connection, chapter_id: str) -> bool:
    """第二个问题：head 上那份 **ACTIVE** 总结，是不是照**旧正文**写的（该覆写）。

    这是与「缺不缺」**各自独立**的一问（2026-08-22）。它必须单独问，因为
    `missing_branch_mask` 只表达得了「缺哪几样」——一份挂着的旧总结在那份清单里
    永远答「不缺」，于是扫描器判 stale、报「已排覆写」，下的单里却一项总结都没有。

    - 撤回 tombstone 不是 ACTIVE → 恒 False（他删一次系统买回来一次的坑，见
      `_head_missing`）；
    - `source_snapshot_id` 为 NULL（019 迁移里反推不出来的 legacy 行）→ 不算旧：
      我们答不出「它照的是哪一版」，就不拿作者的钱去赌一个答案。
    """
    row = conn.execute(
        """
        SELECT 1
          FROM chapter_summary_head h
          JOIN chapter_summary s
            ON s.id = h.current_summary_id AND s.status = 'ACTIVE'
          JOIN chapter_snapshot src ON src.id = s.source_snapshot_id
          JOIN chapter c ON c.id = h.chapter_id
         WHERE h.chapter_id = :cid
           AND src.text_sha256 <> c.text_sha256
        """,
        {"cid": chapter_id},
    ).fetchone()
    return row is not None


def _head_retracted(conn: Connection, chapter_id: str) -> bool:
    """head 指着的是作者的撤回 tombstone —— 这一章「他不要总结」。"""
    row = conn.execute(
        """
        SELECT 1
          FROM chapter_summary_head h
          JOIN chapter_summary s ON s.id = h.current_summary_id
         WHERE h.chapter_id = :cid AND s.status = 'RETRACTED'
        """,
        {"cid": chapter_id},
    ).fetchone()
    return row is not None


def summary_alignment(
    conn: Connection, project_id: str, chapter_id: str
) -> SummaryAlignment:
    """这一章的总结该不该动 —— 两个独立的问题，一个出口（2026-08-22）。

    - `missing`   = 没有可用的总结（`_head_missing`）→ 下「补缺」单；
    - `stale`     = 有 ACTIVE 总结但它照的是旧正文（`_head_outdated`）→ 下「覆写」单；
    - `retracted` = 作者亲手撤掉的 → 一律不动（不是「缺」，见 `_head_missing`）；
    - `paired`    = 有总结、照的就是当前正文 → 不动。

    **调度器和 `_default_missing_mask` 必须用这同一个答案。** 从前调度器自己写了一份
    SQL 判三态、下单那一步另问「缺哪几样」，两份判据分叉的后果是报了「已排覆写」却
    一单没下（2026-08-22 实测）。单一实现让那种分叉写不出来。
    """
    if _head_missing(conn, project_id, chapter_id):
        return "missing"
    if _head_outdated(conn, chapter_id):
        return "stale"
    if _head_retracted(conn, chapter_id):
        return "retracted"
    return "paired"


ExtractionAlignment = Literal["applied", "missing"]
"""这一章的抽取产物对不对得上当前正文。**只有两态，故意不是三态。**

「旧不旧」这个问题在这一侧问不出来：`extraction_application_head` 是按
(project, chapter, generation) 那一次 refresh run 挂的，正文一变 generation 就变，
**旧那一版的 application 天然不算数**——所以 `missing` 已经把「没跑过」和
「跑的是旧正文」两件事一起答完了。总结那一侧要分 `missing` / `stale`，是因为
`chapter_summary_head` 是按章挂的、跨 generation 存活。
"""


def extraction_alignment(
    conn: Connection, project_id: str, chapter_id: str, generation: int
) -> ExtractionAlignment:
    """这一章的抽取跑过没有 —— **调度器问这个，`_default_missing_mask` 问的是同一件事**。

    ⚠️ 它和 `_application_missing` 是**同一个判据的两个入口**，差别只在有没有 run：
    下单那一步已经 `create_run` 过，拿得到 `run_id`；调度器在下单**之前**问，
    那时 run 可能还不存在——**而「没有 run」正是「一次都没跑过」最常见的形态**
    （158 章导进来、只保存过 3 章的那本书，其余 155 章一个 run 都没有）。

    2026-08-25 之前调度器根本不问这个问题：`ChapterSummaryState.needs_work` 只看总结，
    于是那 155 章**永远不会被排上**，抽取也就永远不跑。见 ADR 0020 的补记。
    """
    run = find_run(conn, project_id, chapter_id, generation)
    if run is None:
        return "missing"
    return "missing" if _application_missing(conn, str(run["id"])) else "applied"


def _existing_validation_report(
    conn: Connection,
    project_id: str,
    snapshot_id: str,
    generation: int,
    ruleset_epoch: int,
) -> tuple[str, str] | None:
    """这一版正文在这个规则集下**已经验过**的那份报告 `(id, gate)`；没有 → None。

    「验证那一支缺不缺」和「协调器该复用哪一份」是同一个问题，所以只有这一份实现：
    `_default_missing_mask` 拿它答前者，`ChapterRefreshCoordinator.run` 拿它答后者。
    分成两份的下场正是本轮要修的那个病——单上说「验证不缺」，执行层却自己又验一遍
    （或者反过来：跳过一份 `blocked` 的报告，让下游照跑）。

    取最新那一条：同一版正文可能先后有 `initial` 和 `post_alias` 两份，而 alias
    之后那份才是最终结论。legacy 行（018 之前的）没有 `chapter_snapshot_id`，
    这条 WHERE 天然把它们排除在外。
    """
    row = conn.execute(
        """
        SELECT id, gate FROM validation_report
         WHERE project_id = ? AND chapter_snapshot_id = ?
           AND source_generation = ? AND ruleset_epoch = ?
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (project_id, snapshot_id, generation, ruleset_epoch),
    ).fetchone()
    return (str(row["id"]), str(row["gate"])) if row is not None else None


def _application_missing(conn: Connection, run_id: str) -> bool:
    """`head` 行本身不代表「已经有抽取结果」——`activate_extraction_application`

    在**第一次尝试**（哪怕最终 SUPERSEDED）时就已经 upsert 出这一行；021 的存量回填
    也给每个 refresh run 都建了一行，`current_application_id` 是 NULL（那本书从没
    抽取成功过）。真正的判据是 `current_application_id` 有没有指向一条真实的
    CURRENT application，不是「这一行存不存在」——只查行存在会让这类 head 永远
    读成「不缺」，抽取因此永远不会被排上（158 章的旧书导进来，`autonomy_once` 每
    次都判定这一支「不缺」，角色册永远建不出人）。
    """
    row = conn.execute(
        "SELECT 1 FROM extraction_application_head "
        "WHERE refresh_run_id = ? AND current_application_id IS NOT NULL",
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
    retirement: RetirementReport | None = None,
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
        # `retirement` 只在**建 run 的那一次**落库：一个 (project, chapter, generation)
        # 对应一次保存事务，那一次退休了什么是这一版正文的定值。同 hash 重存走下面
        # 那一支（run 已存在），而那时 `retire_stale_extractor_facts` 本来就改 0 行
        # ——它幂等，没有旧锚时不动任何东西。所以「复用 run」不会把账丢掉。
        run_id = create_run(
            conn,
            project_id=project_id,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=generation,
            retired_edge_ids=retirement.retired_edge_ids if retirement else (),
            retired_event_ids=retirement.retired_event_ids if retirement else (),
            retired_knower_event_ids=(
                retirement.retired_knower_event_ids if retirement else ()
            ),
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
    """默认的缺口计算：验证报告 / 总结 head / 抽取 application 三个分支。

    **总结那一支问的是两件事**（2026-08-22）：缺一份、或挂着的那份照的是旧正文。
    这份 mask 只表达得了「缺哪几样」，所以「旧不旧」必须在进 mask 之前就答完
    （`summary_alignment`）——否则一份挂着的旧总结永远答「不缺」，覆写就永远不发生。
    """
    mask = 0
    verified = _existing_validation_report(
        conn, project_id, snapshot_id, generation, ruleset_epoch
    )
    if verified is None:
        mask |= BRANCH_VALIDATION
    # 两个独立的问题，同一支活：缺一份 → 补缺；挂着的那份照的是旧正文 → 覆写。
    if summary_alignment(conn, project_id, chapter_id) in ("missing", "stale"):
        mask |= BRANCH_SUMMARY
    if _application_missing(conn, run_id):
        mask |= BRANCH_EXTRACTION
    return mask


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
    expires = iso_timestamp(now + ttl_seconds)
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
        {"id": attempt_id, "owner": owner, "expires": expires, "now": iso_timestamp(now)},
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
        {"id": attempt_id, "owner": owner, "expires": iso_timestamp(time.time() + ttl_seconds), "token": token},
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
        f"""
        SELECT id FROM chapter_refresh_attempt
         WHERE (lease_expires_at IS NULL OR lease_expires_at < :now)
           AND ({OUTSTANDING_WHERE})
        """,
        {"now": iso_timestamp(time.time())},
    ).fetchall()
    claimed: list[tuple[str, int]] = []
    for row in rows:
        token = claim_attempt(conn, row["id"], owner=owner, ttl_seconds=ttl_seconds)
        if token is not None:
            claimed.append((str(row["id"]), token))
    conn.commit()
    return claimed


OUTSTANDING_WHERE = """
    ((validation_state IN ('PENDING','RUNNING')
      OR summary_state IN ('PENDING','RUNNING')
      OR extraction_state IN ('PENDING','RUNNING'))
     OR (final_gate_state = 'PENDING'
         AND validation_state IN ('SUCCEEDED','REUSED')))
"""
"""「这张单还没做完」的判据（不带 lease 那一半）。dispatcher 领单（`recover_claimable`）
和顶栏那盏灯（`api/background_status.py`）用的是**同一条**——两处各写一份的话，灯会在
dispatcher 已经不认的单上一直亮着，或者反过来。"""


def iso_timestamp(ts: float) -> str:
    """和表里 `strftime('%Y-%m-%dT%H:%M:%fZ')` 同一种写法的时间戳（毫秒精度那一档能比大小）。"""
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
        """执行固定 DAG：验证 → alias 阶段 → 总结 ∥ 抽取 → final gate。

        ── 只跑单上排了的那几支（`missing_branch_mask`，2026-08-23）────────────

        **「排了哪几支」和「跑了哪几支」必须是同一个答案。** 从前这里一眼都不看
        mask，只要那一列还是 `PENDING` 就跑——于是焦点防抖在下单那一层把总结位
        剥掉了（`ensure_refresh_coverage(skip_summary=True)` → mask=0b101），执行层
        照样调了一次总结模型（2026-08-22 实测）。作者正改着的那一章，改一下午存
        三十次就买三十次，而防抖这条纪律的成本论证靠的恰恰是「一次都不买」。

        它和「报了覆写、单里没总结」是同一个病的另一层：**报什么、排什么、做什么，
        三者之间要有人对过。** 这里就是第三面的那个人，
        `test_the_coordinator_runs_exactly_the_branches_the_order_carries` 钉着它。

        排了却没做的分支落什么状态：
        - 验证：单上没有它 = 这一版正文已经验过了 → `REUSED` 并指向那份报告
          （**连结论一起复用**：那份报告是 blocked/error 时下游照样停）；
        - 总结 / 抽取：`SUPERSEDED` = 「这张单上没有这一支」。这一层分不出两种
          原因（上游判它不缺 / 焦点防抖故意不排），也不需要分——**但不能不落**：
          留在 `PENDING` 的话这条 attempt 永远算「还有活」，每次 pump 都被重新
          claim 一次，却什么都不做。
        """
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
        # 这张单排了哪几支。**下面每一支都先问它**，别再问「那一列还是不是 PENDING」。
        ordered = int(attempt["missing_branch_mask"])
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
        pending_validation = attempt["validation_state"] in ("PENDING", "RUNNING")
        if pending_validation and not ordered & BRANCH_VALIDATION:
            settled = self._reuse_validation_report(
                attempt_id,
                owner=owner,
                token=token,
                project_id=run["project_id"],
                snapshot_id=run["source_snapshot_id"],
                generation=int(run["source_generation"]),
                ruleset_epoch=epoch,
            )
            if settled is not None:
                return settled
        elif pending_validation:
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
                    # 标题和锚都由通知层从报告里取（M1-c）——**哪一段、哪一句、哪条规则**
                    # 本来就在 Issue 上，以前停在报告里没跟着通知走，作者点不过去。
                    # **不用再查这本书的界面语言**（国际化第四批 Phase B）：通知发的是
                    # 码 + 参数，渲染在前端按当前界面语言进行，后端不用再猜。
                    from .system_notifications import enqueue_validation_blocked

                    enqueue_validation_blocked(
                        self._conn,
                        report=validation,
                        attempt_id=attempt_id,
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

        threads: list[threading.Thread] = []
        for column, branch, adapter in (
            ("summary_state", BRANCH_SUMMARY, summary_adapter),
            ("extraction_state", BRANCH_EXTRACTION, extraction_adapter),
        ):
            if not ordered & branch:
                # 单上没有这一支：**一次 adapter 都不许调**（调了就是花钱），
                # 但要落一个终态，否则这条 attempt 永远算「还有活」。
                _branch_update(
                    self._conn, attempt_id, owner=owner, token=token,
                    column=column, state="SUPERSEDED",
                )
                results[column] = "not_ordered"
                continue
            threads.append(threading.Thread(target=_worker, args=(column, adapter)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return {"validation": "passed", "final_gate": final_report_id or "PENDING", **results}

    def _reuse_validation_report(
        self,
        attempt_id: str,
        *,
        owner: str,
        token: int,
        project_id: str,
        snapshot_id: str,
        generation: int,
        ruleset_epoch: int,
    ) -> dict[str, str] | None:
        """单上没有验证那一支 → 复用这一版正文已有的那份报告，**不重跑**。

        返回 `None` = 可以继续走下游；返回 dict = 这一次到此为止（那就是 `run`
        的出参）。

        **复用的是结论，不只是「跑过了」**：那份报告要是 blocked/error，下游同样
        停。少了这一半，一张只缺总结的单就能绕过闸门，把一章已经判定阻断的正文
        拿去总结和抽取——而闸门存在的全部理由就是不让那件事发生。
        通知不重发：产出那份报告的那次尝试已经发过了。
        """
        found = _existing_validation_report(
            self._conn, project_id, snapshot_id, generation, ruleset_epoch
        )
        gate = found[1] if found is not None else None
        if gate == "passed":
            _branch_update(
                self._conn, attempt_id, owner=owner, token=token,
                column="validation_state", state="REUSED", reused_id=found[0],
            )
            self._set_initial_report(attempt_id, found[0])
            return None
        if gate in ("blocked", "error"):
            _branch_update(
                self._conn, attempt_id, owner=owner, token=token,
                column="validation_state", state="BLOCKED" if gate == "blocked" else "FAILED",
            )
            return {"validation": gate}
        # 上游说这一支不缺，库里却没有一份**能答话**的报告（找不到 / legacy /
        # superseded）。这张单答不出「放不放行」，就不替它答：下游不跑，等下一张
        # 带验证位的单去补。假装放行的代价是拿没验过的正文去花钱。
        _branch_update(
            self._conn, attempt_id, owner=owner, token=token,
            column="validation_state", state="SUPERSEDED",
        )
        return {"validation": "not_ordered"}

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
            SELECT id, run_id, ruleset_epoch, ruleset_hash, missing_branch_mask,
                   validation_state, summary_state, extraction_state, alias_phase,
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
