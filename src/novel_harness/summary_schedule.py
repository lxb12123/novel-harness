"""全书总结自治调度（2026-08-18 文档 §2.2 / §4 / Step 2）。

30 分钟自治 = 智能路：不依赖任何点击，后台定期扫全书做对齐。本模块只回答
「哪些章该补/该覆写、按什么优先级」——**全部确定性查库，不调 LLM**（文档 §8）：
「哪章没总结 / 哪章不对齐」是数据库能直接回答的问题，LLM 只在真正把正文压缩成
总结的那一瞬间出场（`RollingSummarizer.ensure`，由 dispatcher 调）。

**定期扫描是主路，保存不是必需**（2026-08-22）。正文变更的来源不止保存：作者在
WPS 里改稿压根不经过工作台、导入一整本书一次保存都没发生过、保存失败或进程崩了。
所以扫描必须自给自足，不能指望有人来通知它；保存时顺手触发只是加速，触发与否
都不影响正确性。

── 两个独立的问题（§2.2 / 2026-08-22 任务 B）──────────────────────────────
「有没有可用的总结」和「挂着的那份是不是照旧正文写的」是**两个**问题，判据各自
独立，但**只有一份实现**：`chapter_refresh.summary_alignment`。每章的答案：
  - `paired`    = 有 ACTIVE 总结且它照的就是当前正文 → 不动；
  - `missing`   = 没有可用总结 → 下「补缺」单；
  - `stale`     = 有 ACTIVE 总结但照的是旧正文 → 下「覆写」单（2026-08-22 裁定：
                  真去重买一份并覆盖，焦点防抖 + `ensure` 幂等挡住「改个错别字买一次」）；
  - `retracted` = 作者亲手撤掉的 → **不算缺**，一律不动（他删一次系统买回来一次，
                  等于花他没按过的钱抹掉他刚做的动作）。
调度器报什么（`queued` / `queued_overwrite`）与真正下的单里有没有总结那一项，
因此不可能分叉——**分叉过一次**：扫描器判 stale 报「已排覆写」，下单那一步问的却是
「缺哪几样」，答「不缺」，于是一单没下（2026-08-22 实测）。
「异常」（生成失败 / 卡住）不在本模块：那是 attempt/通知轨迹的事，`missing`
会自然覆盖它（重试等于再补一次）、`stale` 由指纹覆盖，队列纪律（§6）保证
单条失败不阻塞后续。

── 权重（§4）：只分名额，不当门槛 ─────────────────────────────────────────
写第 N 章时，往回 Δ = N - chapter 的章：
  - Δ ≤ WINDOW（默认 10）→ 权重 1.0（最近历史最重要）；
  - Δ > WINDOW → `max(0, 1 - (Δ-WINDOW)/CAP)`（CAP=50 → **往回 60 章归零**）。

**要干活的章全部进候选池，不管离作者多远**（2026-08-22）。权重的唯一用途是
**分配一轮的名额**：池子按（权重降序，离原点近的先）排队，一轮取前 `limit` 个，
取不完下一轮继续，直到池子空。权重归零只意味着「排队尾」。
从前它同时被当成准入门槛，而原点是作者**当前打开的那一章**——作者跳回去看第 2 章
的那一刻，一本 158 章的书有 156 章瞬间退出调度、缺总结也永远补不上。
唯一保留的例外是**焦点章**（作者正盯着的那一章不碰，防抖，与远近无关）。

── 出口（§2.3）────────────────────────────────────────────────────────────
本模块只决定「该不该 + 按什么序」，入队走既有 `ensure_refresh_coverage`
（幂等 coverage，写 `chapter_refresh_attempt` = 系统记录）——不新造第二条管线。

── 三个触发源，一种单（2026-08-22）────────────────────────────────────────
定期扫描（`schedule_alignment`，范围 = 全书）、扫描时的三态判定、以及**写作时
真的取到了这几章**（`request_summary_backfill`，范围 = 这一次要用的那个窗口）。
三者判据同一份、下单动作同一份（`_place_order`）、结论词表同一份——
入口多一个，能分叉的地方**一个都不许多**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .chapter_refresh import (
    BRANCH_SUMMARY,
    SummaryAlignment,
    ensure_refresh_coverage,
    summary_alignment,
)
from .db import Connection

__all__ = [
    "SUMMARY_WINDOW",
    "SUMMARY_DECAY_CAP",
    "QUEUED_OUTCOMES",
    "ROUND_LIMIT",
    "BookChapterStatus",
    "ChapterSummaryState",
    "book_summary_status",
    "chapter_summary_anomaly",
    "reconcile_anomaly_notifications",
    "request_summary_backfill",
    "scan_chapter_summary_state",
    "weight_for_chapter",
    "schedule_alignment",
]

SUMMARY_WINDOW: int = 10
"""写得近的这一段（Δ ≤ 10）权重满 1.0，回忆上下文最重要（文档 §4）。"""

SUMMARY_DECAY_CAP: int = 50
"""`max(0, 1 - (Δ-WINDOW)/CAP)` 的衰减分母：**往回 60 章（WINDOW+CAP）归零**。

去掉准入门槛之后这个数只影响一轮 20 个名额的先后，不再影响补不补
（2026-08-22 裁定：不改代码保持 60，把文档那句改成 60）。"""

ROUND_LIMIT: int = 20
"""一轮最多下多少单（**名额，不是上限**）：取不完的下一轮接着取，直到池子空。

三个触发源共用这一个数（定期扫描、扫描时的三态判定、写作时取到缺 / 旧），
因为它编码的是同一件事——一单 = 一次模型调用，一轮别一次买太多。"""

QUEUED_OUTCOMES: frozenset[str] = frozenset({"queued", "queued_overwrite"})
"""`schedule_alignment` 的结论里「这一轮真下了单」的那几种。

调度方（`api/background_runtime.py`）数入队条数时用它，别再抄一份字面量——
抄出来的那一份会在新增结论时静默漏数。"""


@dataclass(frozen=True, slots=True)
class ChapterSummaryState:
    chapter_id: str
    chapter_number: int
    state: SummaryAlignment
    weight: float
    """排队用的分数，**不是门槛**：只决定这一轮的名额给谁（0.0 = 排队尾，下一轮再来）。"""

    @property
    def needs_work(self) -> bool:
        """这一章要不要下单。`retracted` 不算缺（他删一次，系统别买回来一次）。"""
        return self.state in ("missing", "stale")


def weight_for_chapter(*, draft_chapter: int, chapter_number: int) -> float:
    """往回 Δ 的排队分数（§4，确定性公式）。**它只排序，不决定补不补。**

    - `Δ ≤ 0`（正在写的这章 / 未来章）→ 0.0：排在队尾。**这不是豁免**——
      「正在写的章不碰」由 `schedule_alignment` 的 `focused_chapter` 一条口子表达，
      与远近无关（有焦点时原点就是焦点章，Δ=0 那一章正是它）；
    - `1 ≤ Δ ≤ WINDOW` → 1.0（最近历史最重要）；
    - `Δ > WINDOW` → `max(0, 1 - (Δ-WINDOW)/CAP)`，往回 60 章归零 = 并列队尾。
    """
    delta = draft_chapter - chapter_number
    if delta <= 0:
        return 0.0
    if delta <= SUMMARY_WINDOW:
        return 1.0
    return max(0.0, 1.0 - (delta - SUMMARY_WINDOW) / SUMMARY_DECAY_CAP)


def scan_chapter_summary_state(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
) -> tuple[ChapterSummaryState, ...]:
    """确定性扫全书：每章四态 + 权重，排好队。**有正文的章一个都不落下。**

    `draft_chapter` = 正在写的章（权重以它为原点往回量）。焦点章防抖不在这层做
    （调度方用 §3 单独豁免），这里只给每章的状态和排队分数。

    每章的状态问 `chapter_refresh.summary_alignment`——**不在这儿写第二份判据**：
    下单那一步用的就是它，两边同一个答案，「报了已排覆写、实际没排」才写不出来。
    """
    rows = conn.execute(
        """
        SELECT c.id AS chapter_id, c.number AS number
          FROM chapter c
          JOIN chapter_snapshot cur_snap
            ON cur_snap.chapter_id = c.id
           AND cur_snap.text_sha256 = c.text_sha256
         WHERE c.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    out = [
        ChapterSummaryState(
            chapter_id=str(row["chapter_id"]),
            chapter_number=int(row["number"]),
            state=summary_alignment(conn, project_id, str(row["chapter_id"])),
            weight=weight_for_chapter(
                draft_chapter=draft_chapter, chapter_number=int(row["number"])
            ),
        )
        for row in rows
    ]
    # 排队：权重高的先；**同权重按离原点近的先**——归零点之外全并列 0.0，没有这一项
    # 的话一本长书的队尾会从第 1 章开始补，离作者最远的反而最先花钱。
    out.sort(
        key=lambda item: (
            -item.weight,
            abs(draft_chapter - item.chapter_number),
            item.chapter_number,
        )
    )
    return tuple(out)


def schedule_alignment(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
    ruleset_epoch: int,
    ruleset_hash: str,
    focused_chapter: int | None = None,
    limit: int = ROUND_LIMIT,
) -> dict[int, str]:
    """把本轮该补/该覆写的章送进既有对齐队列；返回 `{章号: 处理结论}`。

    - 处理所有 `needs_work`（missing 或 stale）的章，**不管离作者多远**；
    - 焦点中的那一章豁免（§3 防抖：正写的章不动）——这是唯一的例外；
    - 入队走 `ensure_refresh_coverage`（missing 与 stale 都让默认 mask 带上
      `BRANCH_SUMMARY` → dispatcher 会 `ensure` 生成/覆写），结果写进
      `chapter_refresh_attempt`（系统记录）。`limit` 是这一轮的**名额**，不是上限：
      取不完的下一轮接着取，直到池子空（幂等收敛，补完就停）。

    返回的结论是给 UI/日志看的，不是断言：`focused`/`paired`/`retracted`/`budget`
    都是「这轮不碰它」的正当理由，不是失败。**只有单子里真的带着总结那一项才报
    `queued` / `queued_overwrite`**（今天这两者对不上，见模块头）。
    """
    states = scan_chapter_summary_state(
        conn, project_id, draft_chapter=draft_chapter
    )
    decisions: dict[int, str] = {}
    scheduled = 0
    for item in states:
        if item.chapter_number == focused_chapter:
            decisions[item.chapter_number] = "focused"
            continue
        if not item.needs_work:
            decisions[item.chapter_number] = item.state
            continue
        if scheduled >= limit:
            decisions[item.chapter_number] = "budget"
            continue
        outcome = _place_order(
            conn, project_id, item, ruleset_epoch=ruleset_epoch, ruleset_hash=ruleset_hash
        )
        decisions[item.chapter_number] = outcome
        if outcome in QUEUED_OUTCOMES:
            scheduled += 1
    return decisions


def _place_order(
    conn: Connection,
    project_id: str,
    item: ChapterSummaryState,
    *,
    ruleset_epoch: int,
    ruleset_hash: str,
) -> str:
    """给一章下一单（`needs_work` 的章才配调），返回这一章的结论。

    **三个触发源下的是同一种单**，所以这一步只有一份实现：定期扫描、扫描时的三态判定、
    以及写作时取总结取到缺 / 旧（`request_summary_backfill`）。抄第二份的下场是
    「报了已排、实际没排」在新入口上再犯一次——那正是 2026-08-22 刚修掉的病。
    """
    snap = conn.execute(
        """
        SELECT cs.id
          FROM chapter_snapshot cs
          JOIN chapter c ON c.id = cs.chapter_id
         WHERE c.project_id = ? AND c.number = ?
           AND cs.text_sha256 = c.text_sha256
         LIMIT 1
        """,
        (project_id, item.chapter_number),
    ).fetchone()
    if snap is None:
        return "no_snapshot"
    try:
        decision = ensure_refresh_coverage(
            conn,
            project_id=project_id,
            chapter_id=item.chapter_id,
            snapshot_id=str(snap["id"]),
            generation=_generation(conn, project_id, item.chapter_number),
            ruleset_epoch=ruleset_epoch,
            ruleset_hash=ruleset_hash,
        )
    except Exception:  # noqa: BLE001 —— 单章入队失败不阻塞整轮调度（§6）
        return "enqueue_failed"
    if decision.processing == "attention_required":
        # 同 basis 已有一个终态 FAILED/BLOCKED 的 coverage attempt：不自动重付
        # （Task 16 纪律），这章要作者手动处理。不算本轮入队预算。
        return "attention_required"
    if not decision.missing_branch_mask & BRANCH_SUMMARY:
        # 报什么 = 下了什么。单子里没有总结那一项就不许自称排了总结的活——
        # 这正是 2026-08-22 那个 bug 的形状（报 queued_overwrite、一单没下）。
        return "no_summary_branch"
    return "queued_overwrite" if item.state == "stale" else "queued"


def request_summary_backfill(
    conn: Connection,
    project_id: str,
    *,
    first_chapter: int,
    last_chapter: int,
    ruleset_epoch: int,
    ruleset_hash: str,
    limit: int = ROUND_LIMIT,
) -> dict[int, str]:
    """**第三个触发源**：写作时真的取到了这几章，缺 / 旧的就下同样的单（2026-08-22）。

    和定期扫描的差别只有**范围**：那边是全书没有范围，这边只问「这一次要用的那几章」
    （行内续写按字数预算算出来的那个窗口）。判据、下单动作、结论词表全部共用——
    这儿一个字的判断都没有，`summary_alignment` 是唯一出口，`_place_order` 是唯一动作。

    **不当场补。** 生成一章总结是一次模型调用、几秒起步，而续写的整个预算是 400 毫秒
    （ADR 0019 边界：模式一塞不下第二次往返）。所以这一次先不给那几章，单下在
    `chapter_refresh_attempt` 里，后台补完下一次续写就有了——**永久修好，不是每次将就**。

    返回 `{章号: 结论}`，词表同 `schedule_alignment`。**只有 `paired` 意味着这一章
    这一次用得上**：其余每一种（含 `retracted`、`budget`、`no_snapshot`）都是
    「这一次别拿它进 prompt」的正当理由。调用方不必逐个认识它们，只认 `paired`。

    没有 `focused_chapter` 这一格：窗口是 `[first, last]` 且 `last < 正在写的那一章`，
    作者正盯着的那一章按定义不在里面。
    """
    if first_chapter < 1 or last_chapter < first_chapter:
        return {}
    rows = conn.execute(
        """
        SELECT c.id AS chapter_id, c.number AS number
          FROM chapter c
          JOIN chapter_snapshot cur_snap
            ON cur_snap.chapter_id = c.id
           AND cur_snap.text_sha256 = c.text_sha256
         WHERE c.project_id = ? AND c.number BETWEEN ? AND ?
        """,
        (project_id, first_chapter, last_chapter),
    ).fetchall()
    states = [
        ChapterSummaryState(
            chapter_id=str(row["chapter_id"]),
            chapter_number=int(row["number"]),
            state=summary_alignment(conn, project_id, str(row["chapter_id"])),
            # 权重在这条路上没有用武之地：范围本身就是优先级——
            # 「写作时真的取到了它」比「离作者当前那章多远」强得多（近的先补即可）。
            weight=0.0,
        )
        for row in rows
    ]
    states.sort(key=lambda item: -item.chapter_number)
    decisions: dict[int, str] = {}
    ordered = 0
    for item in states:
        if not item.needs_work:
            decisions[item.chapter_number] = item.state
            continue
        if ordered >= limit:
            decisions[item.chapter_number] = "budget"
            continue
        outcome = _place_order(
            conn, project_id, item, ruleset_epoch=ruleset_epoch, ruleset_hash=ruleset_hash
        )
        decisions[item.chapter_number] = outcome
        if outcome in QUEUED_OUTCOMES:
            ordered += 1
    return decisions


def _generation(conn: Connection, project_id: str, number: int) -> int:
    """当前 generation（存在 `chapter.snapshot_generation`，不是快照表的列）。"""
    row = conn.execute(
        "SELECT snapshot_generation FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, number),
    ).fetchone()
    if row is None or row["snapshot_generation"] is None:
        return 1
    return int(row["snapshot_generation"])


@dataclass(frozen=True, slots=True)
class BookChapterStatus:
    """全书总结状态视图的一行（文档 §6 / Step 4）：逐章三态 + 异常标记 + 权重。

    `state` 只按**正文有没有、总结配不配**推：
    - `empty`   = 这一章还没有正文（没得总结）；
    - `missing` = 有正文、没有 ACTIVE 总结；
    - `stale`   = 有总结但来源指纹 != 当前正文（不对齐，该覆写）；
    - `paired`  = 有总结且指纹对得上（配对，不动）。
    `anomaly` = 该章最近一次总结 attempt 终态 FAILED/BLOCKED（§5 末行：异常标记，
    不阻塞其它章）。`weight` 是同一轮调度会给它的权重（§4 反馈）。
    """

    chapter_id: str
    chapter_number: int
    has_text: bool
    state: Literal["empty", "paired", "missing", "stale"]
    weight: float
    anomaly: bool


def chapter_summary_anomaly(
    conn: Connection, project_id: str, chapter_id: str
) -> bool:
    """这一章最近一次总结 attempt 是不是终态 FAILED/BLOCKED（§6 异常标记）。

    "卡住"（PENDING/RUNNING 过期未收敛）由 `recover_claimable` 在 dispatcher 侧
    重抢，不在这里算异常；这里只看**已经定性失败**的尝试。
    """
    row = conn.execute(
        """
        SELECT a.summary_state
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ? AND r.chapter_id = ?
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (project_id, chapter_id),
    ).fetchone()
    return (
        row is not None and str(row["summary_state"]) in ("FAILED", "BLOCKED")
    )


def _latest_attempt_states(
    conn: Connection, project_id: str
) -> dict[str, tuple[str, str]]:
    """每章最近一次 attempt 的 `(attempt_id, summary_state)`（一次查询，N+1 收敛）。"""
    rows = conn.execute(
        """
        SELECT r.chapter_id AS chapter_id, a.id AS attempt_id,
               a.summary_state AS s
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ?
           AND a.id = (
                SELECT a2.id
                  FROM chapter_refresh_attempt a2
                  JOIN chapter_refresh_run r2 ON r2.id = a2.run_id
                 WHERE r2.project_id = ? AND r2.chapter_id = r.chapter_id
                 ORDER BY a2.created_at DESC, a2.id DESC
                 LIMIT 1
           )
        """,
        (project_id, project_id),
    ).fetchall()
    return {
        str(row["chapter_id"]): (str(row["attempt_id"]), str(row["s"]))
        for row in rows
    }


def _latest_attempt(
    conn: Connection, project_id: str, chapter_id: str
) -> Any | None:
    """这一章最近一次 attempt（带 run 的 source_snapshot_id，供通知去重锚定）。"""
    return conn.execute(
        """
        SELECT a.id AS attempt_id, a.summary_state, r.source_snapshot_id
          FROM chapter_refresh_attempt a
          JOIN chapter_refresh_run r ON r.id = a.run_id
         WHERE r.project_id = ? AND r.chapter_id = ?
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (project_id, chapter_id),
    ).fetchone()


def book_summary_status(
    conn: Connection,
    project_id: str,
    *,
    draft_chapter: int,
) -> tuple[BookChapterStatus, ...]:
    """全书总结状态视图（Step 4）：每一章的三态 + 权重 + 异常标记。

    与 `scan_chapter_summary_state` 的区别：这里是**给作者看全貌**的视图，包含
    没有正文的章（`empty`）；那是**给调度器用**的，只挑有正文的章。

    **两者对「撤回过」的说法故意不同，别把它「修」成一致**：这边照旧显示
    `missing`（作者要看得见自己撤了，同 `SummaryStore.coverage()`），调度那边算
    `retracted`（系统不该自己掏钱补一份回来）。两个问题，两个答案。
    """
    rows = conn.execute(
        """
        SELECT c.id AS chapter_id, c.number AS number,
               (cur_snap.id IS NOT NULL) AS has_text,
               src_snap.text_sha256 AS source_sha,
               cur_snap.text_sha256 AS text_sha
          FROM chapter c
          LEFT JOIN chapter_snapshot cur_snap
            ON cur_snap.chapter_id = c.id
           AND cur_snap.text_sha256 = c.text_sha256
          LEFT JOIN chapter_summary_head h ON h.chapter_id = c.id
          LEFT JOIN chapter_summary s
            ON s.id = h.current_summary_id AND s.status = 'ACTIVE'
          LEFT JOIN chapter_snapshot src_snap ON src_snap.id = s.source_snapshot_id
         WHERE c.project_id = ?
         ORDER BY c.number
        """,
        (project_id,),
    ).fetchall()
    attempt_states = _latest_attempt_states(conn, project_id)
    out: list[BookChapterStatus] = []
    for row in rows:
        chapter_id = str(row["chapter_id"])
        num = int(row["number"])
        has_text = bool(row["has_text"])
        text_sha = str(row["text_sha"]) if row["text_sha"] else None
        src_sha = str(row["source_sha"]) if row["source_sha"] else None
        if not has_text:
            state: Literal["empty", "paired", "missing", "stale"] = "empty"
        elif src_sha is None:
            state = "missing"
        elif text_sha is not None and src_sha != text_sha:
            state = "stale"
        else:
            state = "paired"
        out.append(
            BookChapterStatus(
                chapter_id=chapter_id,
                chapter_number=num,
                has_text=has_text,
                state=state,
                weight=weight_for_chapter(
                    draft_chapter=draft_chapter, chapter_number=num
                ),
                anomaly=(
                    attempt_states.get(chapter_id, ("", ""))[1]
                    in ("FAILED", "BLOCKED")
                ),
            )
        )
    return tuple(out)


def reconcile_anomaly_notifications(
    conn: Connection,
    project_id: str,
    statuses: tuple[BookChapterStatus, ...],
) -> int:
    """把异常标记同步成 `background_failure` 通知（§5 末行 / §6 / Step 4）。

    - 异常章：按「章 + operation=summary + **失败的那条 attempt**」找最新一次失败
      attempt，用它的稳定键 upsert 一条 OPEN——同一失败重复扫不重开（不变量 10）；
      好转后旧 OPEN 被解决，将来**新的**失败（新 attempt = 新键）又能重新 OPEN；
    - 好转章：解决该章所有 OPEN 的 `background_failure`（本章目前只有总结失败会产
      生这个 kind；validation 走 `validation_blocked`，不受影响）。

    直接写 `system_notification`（同 `finalize_reconciliation_run` 的模式）；调用方
    （`autonomy_once`）在自己的事务里提交。返回本轮新建的条数。
    """
    from .ids import EntityType, new_id
    from .system_notifications import background_failure_dedupe_key

    created = 0
    for item in statuses:
        latest = _latest_attempt(conn, project_id, item.chapter_id)
        if latest is not None and latest["summary_state"] in ("FAILED", "BLOCKED"):
            dedupe = background_failure_dedupe_key(
                kind="background_failure",
                subject_type="chapter",
                subject_id=item.chapter_id,
                operation="summary",
                source_snapshot_id=latest["source_snapshot_id"],
                job_id=latest["attempt_id"],
            )
            row = conn.execute(
                """
                INSERT INTO system_notification (
                    id, project_id, kind, status, subject_type, subject_id,
                    chapter_number, title, dedupe_key
                ) VALUES (?, ?, 'background_failure', 'OPEN', 'chapter', ?, ?,
                          ?, ?)
                ON CONFLICT (project_id, dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    new_id(EntityType.SYSTEM_NOTIFICATION, project_id),
                    project_id,
                    item.chapter_id,
                    item.chapter_number,
                    f"第 {item.chapter_number} 章的总结生成异常，已跳过不阻塞其它章",
                    dedupe,
                ),
            ).fetchone()
            created += row is not None
        else:
            conn.execute(
                """
                UPDATE system_notification SET status = 'RESOLVED',
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                 WHERE project_id = ? AND kind = 'background_failure'
                   AND subject_type = 'chapter' AND subject_id = ?
                   AND status = 'OPEN'
                """,
                (project_id, item.chapter_id),
            )
    return created
