"""模型调用的审计原子持久化（抽取 / 滚动总结 / 起草 / 写作助手共用）。"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Final

from pydantic import BaseModel, ConfigDict

from ..db import Connection
from ..ids import artifact_id
from .control import AnalysisRequest, AuditedCompletion, ExtractionRun, ExtractionRunStateError


class ModelCallReceipt(BaseModel):
    """一次模型调用的**账单原料**：`record_call()` 除 conn / project_id / id 工厂之外的全部入参。

    形状照着下面那个函数的签名长，是为了让持有 conn 的那一层是一次**平移**而不是一次
    翻译——翻译的地方就是能悄悄漏字段的地方，而漏掉的那个字段会让日志页少算一笔钱。
    （`tests/test_chat_api.py` 有一条按字段名对签名的断言钉着这句话。）

    **它住在这儿而不是 `agent/loop.py`**（2026-08-11 搬的）：起草侧
    （`draft/product_draft.py`）也产同一种原料，而 `draft/` 不许 import `agent/`。
    放在 `record_call` 隔壁还有一个好处——改签名的人抬眼就看得见要跟着改的那个模型。

    `capability` / `schema_version` **没有默认值**：搬过来之前它们默认是写作助手那一档，
    于是起草侧少写一个参数就会把一次起草记成一次聊天，而日志页上那两行长得一模一样。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    """`model_call.capability`。**新长出一种花钱的动作就要去
    `activity.py::_CAPABILITY_LABEL` 补一行中文**，那张表认不出的是原样回吐的。"""

    schema_version: str
    model: str
    finish_reason: str | None = None
    prompt_hash: str
    prompt_bytes: bytes
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    """**供应商报的那个数，没报就是 `None`。** 这一层不许替它补一个估算值：
    账上的零和「没报」是两件事（`CostTotals` 把 `NULL` 折成 0 那条已知病就是这么来的）。
    闸门那一侧另算（`agent/loop.py` 的 `charged`），两个消费者两套规矩。"""

    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    """输入里有多少不用重新算 / 有多少为下次存了起来（`draft/provider.py::CacheUsage`）。

    **同上一条的规矩：没报就是 `None`，不是 0**，而这两个数糊错的代价更大——
    「这条端点不支持缓存」和「这次一次都没命中」指向两个相反的动作（去查怎么开启 /
    去查前缀被谁弄脏了）。这里默认 `None` 是照着上面两个 token 数的形状长的：
    一个忘了传的生产者只会**少报**，不会**报错**。真正钉住「有没有传」的是端到端那条测试
    （`tests/test_cache_usage.py`），不是这个默认值。
    """

    elapsed_ms: int = 0

    cost: float | None = None
    """这一次**大概**花了多少美元（`draft/windows.py::estimate_cost`）。

    **`None` = 算不出来**，不是 0 —— 同上面几个 token 数的规矩。算不出来有三种：
    供应商没报 token 数、公共表里没这个模型的单价、或者这条路由的主机名我们不认识
    （自建端点：那儿的「标价」是一句关于作者钱包的假话）。

    ── 为什么这个数在**调用方**算，不在 `record_call` 里算 ────────────────

    单价要按 `(base_url, model)` 查（主机名是那道闸），而 `record_call` 只拿得到
    `model`。更要紧的是 `activity.py` 那条既有规矩：**账本只照抄**。
    让写库那一层做乘法，等于把 `_estimate_tokens` 那种东西请进这两列 ——
    而一笔用估算 token 乘出来的钱，在屏幕上和一笔真钱长得一模一样。

    ⚠️ **它是标价估算，不是账单。** 作者可能有折扣、走中转、用免费额度。
    屏幕上必须带那个「约」字（`frontend` 那侧钉着）。
    """


def record_call(
    conn: Connection,
    *,
    project_id: str,
    capability: str,
    model: str,
    finish_reason: str | None,
    schema_version: str,
    prompt_hash: str,
    prompt_bytes: bytes,
    text: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    elapsed_ms: int,
    cost: float | None = None,
    chapter_number: int | None = None,
    call_id_factory: Callable[[str], str],
) -> str:
    """写入一条 model_call 审计行并返回 call id（调用方负责自己的业务表）。

    两个缓存参数**没有默认值**，和这个签名上的其它入参一致：这是全库唯一的写入口，
    每一条路径都必须把自己那个答案说出来，包括「我这条路不知道」（显式传 `None`）。
    给了默认值就等于让一条新长出来的路径**静默**地不报——而不报和报 0 在这两列上
    是两个相反的结论（见 `008_cache_usage.sql`）。

    ── `chapter_number` 为什么反而**有**默认值（和上面两个不一样）────────────
    两条理由，缺一条都不成立：

    1. **糊错的代价不同。** 缓存那两列糊成 0，「不支持」和「一次都没命中」互换，
       指向两个相反的动作；章号这一列的 `NULL` 有一条**兜底路径**
       （`activity._call_chapter` 照旧从 `extraction_run` / `chapter_summary` 反查），
       而且认不出来时屏幕上是「未记录」——一个诚实的空，不是一个骗人的零。
    2. **今天有一个写入方在别的 agent 手里**（`api/chat.py::_ledger`，写作助手那一档）。
       没有默认值 = 那条路当场 `TypeError`，也就是**用一次崩溃换一个静默**——
       更差。**这个默认值是一条欠账，不是一个设计**：
       `tests/test_call_chapter.py::test_no_new_silent_ledger` 盯着它别再扩散
       （已知不填的集合只许缩，不许长）。

    填不出章号的路径**照旧传 `None`（或不传），不许编一个**：起草工具起的是
    `DraftAsk.chapter` 那一章，和作者此刻停在第几章可以不是同一个数。
    """
    call_id = call_id_factory(project_id)
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call id factory must return a non-empty string")
    call_id.encode("utf-8")
    if not capability or not isinstance(capability, str):
        raise ValueError("capability must be a non-empty string")
    params_json = json.dumps(
        {"finish_reason": finish_reason, "schema_version": schema_version},
        sort_keys=True,
        separators=(",", ":"),
    )
    out_bytes = text.encode("utf-8")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO model_call (
            id, project_id, capability, model, params_json, prompt_hash,
            in_artifact, out_artifact, tokens_in, tokens_out, ms,
            cache_read_tokens, cache_write_tokens, chapter_number, cost
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            project_id,
            capability,
            model or "unknown",
            params_json,
            prompt_hash,
            artifact_id(prompt_bytes),
            artifact_id(out_bytes),
            prompt_tokens,
            completion_tokens,
            elapsed_ms,
            cache_read_tokens,
            cache_write_tokens,
            chapter_number,
            cost,
        ),
    )
    return call_id


EXTRACTOR_CAPABILITY: Final = "extractor"
"""抽取那一路在 `model_call.capability` 上的取值。**两个写入方，一个定义**
（成功那条 `record_model_call`、失败那条 `record_failed_call`）——抄成两份字面量的话，
账上会长出两类看起来无关的行，而它们是同一件事的两个结局。"""


def record_failed_call(
    conn: Connection,
    *,
    project_id: str,
    capability: str,
    model: str | None,
    prompt_hash: str | None,
    prompt_bytes: bytes,
    elapsed_ms: int,
    error_type: str,
    error_message: str,
    chapter_number: int | None,
    call_id_factory: Callable[[str], str],
) -> str:
    """**没答上来的那一次也要记一行。** 一条 FAILED `model_call`，一步落库。

    ── 为什么它必须存在（2026-08-25 实测撞出来的）──────────────────────────

    `model_call` 有 `error_type` / `error_message` 两列，而抽取那条路
    **一次都没填过**：provider 抛异常 → run 标 FAILED → 就没了。那天真书上跑了一次，
    跑前 6 行、跑后还是 6 行，**作者每一次失败的尝试在账上都不存在**。

    这不只是记账好看：**失败的调用照样可能计费**（供应商按请求计、按已生成的
    token 计的都有），而账本上看不见的钱是查不出来的钱。

    ── 它记什么、不记什么 ──────────────────────────────────────────────

    - `tokens_*` / `cost` / `out_artifact` **全部留 NULL**：没答上来就是没有这些数。
      「供应商报没报」那条规矩在这里不变——**没报的留 NULL，绝不估**。
    - `in_artifact` 照记：prompt 是我们自己发出去的，它的哈希是确定的，
      而「这一次发的是哪一份 prompt」正是事后排查要问的第一个问题。
    - `error_type` 是**分档的机器码**（`ProviderFailureKind` 之类），
      `error_message` 是写给维护者的诊断。**两者都不上作者的屏幕**——
      屏幕上那句话由 `activity._RUN_ERROR_LABEL` 从 run 的 `code` 翻，
      见那一节（同 `ExtractionRunError.message` 的规矩）。

    **不提交**：调用方（`runner._mark_failed`）要把这一行和 run 的状态写在同一个
    事务里，否则崩在中间会留下一条没有 run 的孤儿账。
    """
    call_id = call_id_factory(project_id)
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call id factory must return a non-empty string")
    if not capability or not isinstance(capability, str):
        raise ValueError("capability must be a non-empty string")
    conn.execute(
        """
        INSERT INTO model_call (
            id, project_id, capability, model, params_json, prompt_hash,
            in_artifact, chapter_number, ms,
            call_state, error_type, error_message, finished_at
        ) VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, 'FAILED', ?, ?,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        """,
        (
            call_id,
            project_id,
            capability,
            model or "unknown",
            prompt_hash,
            artifact_id(prompt_bytes),
            chapter_number,
            elapsed_ms,
            error_type,
            error_message,
        ),
    )
    return call_id


def record_receipt(
    conn: Connection,
    receipt: ModelCallReceipt,
    *,
    project_id: str,
    chapter_number: int | None,
    call_id_factory: Callable[[str], str],
) -> str:
    """把一份账单原料落成一行 `model_call` **并提交**。逐字段平移，不翻译。

    ── 为什么这一层要提交 ────────────────────────────────────────────────
    `record_call` 自己开 `BEGIN IMMEDIATE` 但不提交（它的调用方通常还要写一张业务表，
    两边要在一个事务里落）。**账单原料这条路上没有业务表**——这一行就是全部，
    而产它的地方（`draft/product_draft.py` 的 `on_call`）是**一落地就叫一次**：
    第一次答上来了、续写那次断线是一档真会发生的失败，那时钱已经付掉。
    不当场提交 = 请求结束连接一关，那一行连同已付的钱一起消失。

    ── `chapter_number` 由**持有 conn 的这一层**说，不由生产者说 ─────────────
    生产者（`ModelCallReceipt`）上没有这个字段，是因为它不一定知道：同一份原料，
    `/draft` 那条路的答案是 `ctx.chapter`，写作助手那条路的答案是 `DraftAsk.chapter`
    （**不是**作者此刻停在第几章）。让原料带一个可能是猜的数，比留空更坏。
    **没有默认值**：这条路上的每一个调用方都得把自己那个答案说出来，包括「不知道」。

    **失败不吞**：账记不上的那一次调用已经花过钱了，而一条静默失败的记账
    就是日志页第二次骗人（同 `api/chat.py::_ledger`）。
    """
    try:
        call_id = record_call(
            conn,
            project_id=project_id,
            capability=receipt.capability,
            model=receipt.model,
            finish_reason=receipt.finish_reason,
            schema_version=receipt.schema_version,
            prompt_hash=receipt.prompt_hash,
            prompt_bytes=receipt.prompt_bytes,
            text=receipt.text,
            prompt_tokens=receipt.prompt_tokens,
            completion_tokens=receipt.completion_tokens,
            cache_read_tokens=receipt.cache_read_tokens,
            cache_write_tokens=receipt.cache_write_tokens,
            cost=receipt.cost,
            elapsed_ms=receipt.elapsed_ms,
            chapter_number=chapter_number,
            call_id_factory=call_id_factory,
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return call_id


def record_model_call(
    conn: Connection,
    run: ExtractionRun,
    request: AnalysisRequest,
    completion: AuditedCompletion,
    *,
    elapsed_ms: int,
    call_id_factory: Callable[[str], str],
) -> str:
    try:
        call_id = record_call(
            conn,
            project_id=run.project_id,
            capability=EXTRACTOR_CAPABILITY,
            model=completion.model,
            finish_reason=completion.finish_reason,
            schema_version=run.schema_version,
            prompt_hash=request.prompt_hash,
            prompt_bytes=request.prompt_bytes,
            text=completion.text,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            cache_read_tokens=completion.cache_read_tokens,
            cache_write_tokens=completion.cache_write_tokens,
            cost=completion.cost,
            elapsed_ms=elapsed_ms,
            # 这一次抽取是为哪一章花的。**反查那条路照旧成立**（`extraction_run` 就在
            # 下面这条 UPDATE 里指回来），这一列是给「新行不必反查」用的——
            # 而旧行只有反查，所以两条路都得留着（见 `009_call_chapter.sql`）。
            chapter_number=run.chapter_number,
            call_id_factory=call_id_factory,
        )
        changed = conn.execute(
            """
            UPDATE extraction_run SET model_call_id = ?
            WHERE id = ? AND status = 'RUNNING' AND model_call_id IS NULL
            """,
            (call_id, run.id),
        )
        if changed.rowcount != 1:
            raise ExtractionRunStateError(
                f"run stopped being RUNNING while recording call: {run.id}"
            )
        conn.commit()
        return call_id
    except BaseException:
        conn.rollback()
        raise


# ══════════════════════════════════════════════════════════════════════════
# 两阶段模型调用审计（022 / Task 10，不变量 30）
# ══════════════════════════════════════════════════════════════════════════
#
# `record_call` 只覆盖「调用成功」那一条：provider 已经答上来了，一步落库。
# 进程崩溃 / provider 抛错 / lease 丢失后的重试，都需要把「这次调用**正在花
# 钱**」这一步也记下来 —— 否则同一业务 run 的多次真实付费尝试会互相覆盖，
# 而账本上只有一次。这三条是给那些两阶段调用方（总结核对 / 事件摘要重生成）
# 用的：
#
#   begin_call      → 事务内插入 RUNNING model_call（provider 出发前）
#   finalize_success→ 成功：token / cache / cost / finished_at
#   finalize_failure→ 失败：error_type / error_message / finished_at
#   abandon_call    → 进程崩溃遗留的 RUNNING，lease 过期后标 ABANDONED
#
# 「供应商报没报」的规矩在这里不变：报出来的数原样写，没报的留 NULL，绝不估。


def begin_call(
    conn: Connection,
    *,
    project_id: str,
    capability: str,
    model: str | None,
    params_json: str = "{}",
    prompt_hash: str | None = None,
    in_artifact: str | None = None,
    out_artifact: str | None = None,
    provider_name: str | None = None,
    profile_name: str | None = None,
    chapter_number: int | None = None,
    call_id_factory: Callable[[str], str],
) -> str:
    """provider 出发前：事务内插入一条 RUNNING model_call 并返回 call id。

    调用方随后在自己的业务事务里 INSERT 对应的 link 行（`summary_reconciliation_call`
    之类）。这一层**不提交**：和调用方要写的 link 必须在同一个事务里（否则崩溃点
    在这两步之间，会留下一条没有归属的 RUNNING）。
    """
    call_id = call_id_factory(project_id)
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("call id factory must return a non-empty string")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO model_call (
            id, project_id, capability, model, params_json, prompt_hash,
            in_artifact, out_artifact, chapter_number,
            call_state, provider_name, profile_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
        """,
        (
            call_id,
            project_id,
            capability,
            model or "unknown",
            params_json,
            prompt_hash,
            in_artifact,
            out_artifact,
            chapter_number,
            provider_name,
            profile_name,
        ),
    )
    return call_id


def finalize_call_success(
    conn: Connection,
    call_id: str,
    *,
    out_artifact: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    ms: int,
    cost: float | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    finished_at: str,
) -> None:
    """成功：补 completion / token / cache / cost，并把 state 改成 SUCCEEDED。"""
    changed = conn.execute(
        """
        UPDATE model_call
           SET call_state = 'SUCCEEDED',
               out_artifact = ?, tokens_in = ?, tokens_out = ?, ms = ?,
               cost = ?, cache_read_tokens = ?, cache_write_tokens = ?,
               finished_at = ?
         WHERE id = ? AND call_state = 'RUNNING'
        """,
        (
            out_artifact,
            tokens_in,
            tokens_out,
            ms,
            cost,
            cache_read_tokens,
            cache_write_tokens,
            finished_at,
            call_id,
        ),
    )
    if changed.rowcount != 1:
        raise LookupError(f"no RUNNING model_call to finalize: {call_id}")


def finalize_call_failure(
    conn: Connection,
    call_id: str,
    *,
    error_type: str,
    error_message: str,
    finished_at: str,
) -> None:
    """失败：写 error_type / error_message，state → FAILED。"""
    changed = conn.execute(
        """
        UPDATE model_call
           SET call_state = 'FAILED', error_type = ?, error_message = ?,
               finished_at = ?
         WHERE id = ? AND call_state = 'RUNNING'
        """,
        (error_type, error_message, finished_at, call_id),
    )
    if changed.rowcount != 1:
        raise LookupError(f"no RUNNING model_call to fail: {call_id}")


def abandon_call(
    conn: Connection,
    call_id: str,
    *,
    finished_at: str,
) -> None:
    """进程崩溃遗留的 RUNNING：lease 过期后标 ABANDONED（不覆盖、不删除）。"""
    conn.execute(
        """
        UPDATE model_call
           SET call_state = 'ABANDONED', finished_at = ?
         WHERE id = ? AND call_state = 'RUNNING'
        """,
        (finished_at, call_id),
    )
