"""「章号 + 目标 → 一稿正文」的**唯一实现**。

在这个文件存在之前，这条路径整个内联在 `api/api/app.py` 的 `/draft` 路由体里（约 120 行：
capability 探测 → 记忆预算 → 长度策略 → form 选择 → 三条装配分支 → `generate_draft`）。
（那个「form 选择」2026-08-25 删了——三臂随 M2 一起退役，见 `assemble.py` 的模块 docstring。）
3.1 落地工具表时就报过它：

> 今天没有任何后端函数能收「章号 → 一稿正文」……**把它抄进工具表 = 第二条会漂的起草路径**。

所以模式二接起草的第一刀不是「在 `agent/` 里再写一份」，是把那 120 行提到这儿，
让 HTTP 壳和 agent 的起草工具**调同一个函数**。

── 这一层收什么、不收什么 ────────────────────────────────────────────────

**收一份算好的 `DraftContext`，不收 cast、不收 store。** 约束由调用方从
`panel/constraints.py` 那个唯一闸门算好再递进来（HTTP 那边从作者填的在场名单算，
agent 那边从正文推，ADR 0018）。这条分工不是洁癖：

- 约束集只许有一个入口（`tests/test_draft_boundary.py` 的 `WRITER_BANNED` 把
  `resolve_cast` 钉在墙外），这一层自己解析 cast 就是给第二份推导开门；
- agent 那条路上 `_scene_context()` **已经**算过一次（工具要拿 `must_not_reveal`
  当回执），再算一次就是同一份约束的两次查询——中间图变了的话两份还会不一样。

**它也不落盘。** 写不写进第 N 章是 ADR 0021 的事，发生在拿到这一稿之后，
走 `importer.save_chapter()` 那条既有路径（磁盘先、DB 跟）；行内续写（ADR 0015）
按定义就不该落盘。一个函数只回答「写出来是什么」。

── 账单原料在出参里，但这一层不记账 ──────────────────────────────────────

`ChapterDraft.calls` 是 1–2 份 `ModelCallReceipt`（一次生成 + 至多一次续写，ADR 0011 D3），
**记账的动作留给持有 conn 的那一层**（同 `agent/loop.py` 的 `ledger`：
「`ToolContext` 上没有 conn」是边界一的一部分）。

出参里带着它，是因为**不带就没人记得补**：`/draft` 那条路今天一行 `model_call` 都不写，
于是日志页显示的是真实花销的一小部分、看起来却像全部（`docs_dev` 的「已知限制」第一条）。
agent 那条路的 `ledger` 是必填参数，接上这一层之后那笔钱才真的落到账上。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Literal, Protocol, runtime_checkable

from ..events import EventStore
from ..extract.call_audit import ModelCallReceipt
from ..graph import NodeLabel
from ..calibration.models import SceneBrief
from ..calibration.render import render_scene_brief, render_target_chapter
from .assemble import (
    CONTINUATION_GOAL,
    WRITE_RULE_FORBIDDEN_HINTS,
    assemble,
    product_tail_limit,
)
from .capabilities import ProviderCapabilities, ResolvedCallPlan
from .context import DraftContext, ResolvedConstraints, UnknownCastConstraints
from .generate import DraftAttempt, DraftResult, generate_draft
from .length import DraftLanguage, LengthSpec
from .product_assemble import assemble_continuation, assemble_product
from .product_context import (
    MemoryBudget,
    build_product_context,
    memory_units_available,
    select_rolling_summaries,
    summary_window_chapters,
)
from .provider import ProviderConfig
from .rolling_summary import (
    ChapterSummary,
    ChapterSummaryStatus,
    SummarySnapshotWatermark,
)

WRITER_CAPABILITY: Final = "writer"
"""`model_call.capability` 这一列的取值。**`activity._CAPABILITY_LABEL` 里已经有它**
（`writer → 起草`）——那一行在此之前是死的，因为起草从来没写过账。"""

PRODUCT_DRAFT_SCHEMA_VERSION: Final = "m5.draft.v1"
"""这一稿的 prompt 结构版本，进 `model_call.params_json`。

**改了装配形状就要改这个数**：账上那一行是事后唯一能回答「当时发出去的是什么形状」的
东西，而 `in_artifact` 只存哈希、不存原文。
"""


class DraftRefused(Exception):
    """这一稿在**发出去之前**被拒了，理由说得出口（文风里写了禁令词）。

    不继承 `ValueError`：那样它会被 `api/app.py` 的全局 `ValueError` handler 兜住，
    而两个调用方要给作者的话不一样（HTTP 那边是 422，agent 那边是一条贴回对话的
    `ok=False`）。异常类型分得开，两边的措辞才分得开。
    """


@runtime_checkable
class SummarySource(Protocol):
    """滚动总结的只读端口（`draft.rolling_summary.SummaryStore` 结构上满足它）。

    **比 `agent.ports.SummaryIndex` 多一个 `for_range`**，因为起草要的是内容
    （装进记忆前言），索引要的只是「有没有」。两个协议各自只收自己用得到的方法，
    这样「谁能拿到什么」在类型上就是准的。
    """

    def for_range(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> list[ChapterSummary]: ...

    def coverage(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> list[ChapterSummaryStatus]: ...

    def snapshot_watermark(
        self, project_id: str, chapter_number: int
    ) -> SummarySnapshotWatermark | None: ...


@dataclass(frozen=True, slots=True)
class SummaryBackfillReply:
    """`SummaryBackfill.request()` 的回话：这一次别用谁、给谁下了单。"""

    unusable: frozenset[int] = frozenset()
    """这一次不许进 prompt 的章号。**默认拒**：答不出「配对」的一律在里面
    （缺 / 旧 / 作者撤回过 / 名额用完 / 这一章压根没查到）。一份停在旧正文上的
    总结比没有总结更坏——缺是瞎，过期是说错，而模型手里只有那一段字。"""

    ordered: tuple[int, ...] = ()
    """真的下了单的那几章。**回执里报的是它**，不是 `unusable`：后者含「作者自己
    撤掉的」和「这一章没正文」，把它们说成「该补而没补」就是催他去做一件他刚做过
    相反决定的事。"""


@runtime_checkable
class SummaryBackfill(Protocol):
    """**写作时取总结取到缺 / 旧就下一单**（第三个触发源，2026-08-22）。

    定期扫描和扫描时的三态判定已经是两个入口，这里只是再加一个：真的取到了这一章，
    发现缺 / 旧，就下同样的单（`summary_schedule.request_summary_backfill`）。
    **不当场补**——生成一章总结是一次模型调用、几秒起步，而续写的整个预算是 400 毫秒
    （ADR 0019 明写模式一塞不下第二次往返）。这一次先不给那几章，下一次就有了。

    为什么是一个**单独的端口**而不是 `SummarySource` 上多一个方法：那个协议的定义是
    「滚动总结的**只读**端口」，而下单是写。两件事分两个口子，「谁能改什么」才在类型上是准的。
    """

    def request(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> SummaryBackfillReply:
        """给这个闭区间里缺 / 旧的章下单，并说清这一次哪几章用不上。

        **唯一出口是 `chapter_refresh.summary_alignment`**：实现这个协议的那一层
        不许自己再问一遍「有没有总结 / 旧不旧」——两份判据分叉的后果 2026-08-22
        实测过一次（报了「已排覆写」，一单没下）。
        """
        ...


@dataclass(frozen=True)
class ChapterDraftRequest:
    """作者这一次想要什么样的一稿。**没有一个字段装得下约束**（ADR 0019 边界二）。

    长度是**请求参数**不是后端设置（[ADR 0013](../../../docs/adr/0013-draft-length-is-a-request-parameter.md)）：
    「这一次想写多少」是意愿，不是书的数据。
    """

    goal: str
    length: LengthSpec
    mode: Literal["chapter", "continuation"] = "chapter"
    previous_tail: str = ""
    following_text: str = ""
    """光标**后面**那截同章正文。**只有 `continuation` 那一支读它。**

    作者跳回去改第 2 章时，光标后面那几千字是**已经写好的正文**——模型看不到它，
    写出来的一段就可能跟紧接着的下一段接不上，或者干脆把它重写一遍。
    渲染成【下文】块的是 `product_assemble.assemble_continuation`，那儿写死了
    「别重写」这句话（不说清楚模型会把它当成待写的段落）。
    """

    write_rule: str = ""
    brief: SceneBrief | None = None
    """已封存的 `SceneBrief`（ADR 0033）。只有 PRODUCT 分支渲染它。"""

    target_chapter_text: str | None = None
    """目标章当前正文（重写/续写已有章时）。**一次读取的快照，不是磁盘现读。**"""


TARGET_CHAPTER_UNITS: Final = 16_000
"""目标章当前正文分区的最多字数。超出确定性截断并给覆盖回执（ADR 0033 §8.4）。"""


@dataclass(frozen=True)
class ChapterDraft:
    """一稿正文 + 它的两份回执（记忆层装了什么 / 花了多少）。"""

    result: DraftResult
    memory: dict[str, Any]
    calls: tuple[ModelCallReceipt, ...] = field(default_factory=tuple)
    """每一次真的模型调用一份（生成 1 次 + 续写至多 1 次）。**调用方负责落账。**"""


def memory_receipt(
    note: str,
    *,
    assembled: bool = False,
    profiles: int = 0,
    recent_events: int = 0,
    background_events: int = 0,
    rolling_summaries: int = 0,
    unsummarized_chapters: list[int] | None = None,
) -> dict[str, Any]:
    """起草响应里的「记忆层这一稿到底装了什么」回执。

    **零必须带着理由一起出现**（ARCHITECTURE §10 约束 8，同 `/check` 的 `rules_run`）：
    「0 条滚动总结」既可能是「这本书还没写到第 10 章，那一层本来就是空的」，也可能是
    「有 12 章该总结而一条都没生成」。两者在界面上长成同一个「- 暂无」，作者就永远不会
    知道自己少喂了什么给模型——这正是 2026-08-06 盘点里那条「没有任何东西提示作者」。
    """
    return {
        "assembled": assembled,
        "note": note,
        "profiles": profiles,
        "recent_events": recent_events,
        "background_events": background_events,
        "rolling_summaries": rolling_summaries,
        "unsummarized_chapters": list(unsummarized_chapters or []),
    }


def check_request(request: ChapterDraftRequest) -> None:
    """把请求里那个自由字符串验掉。**纯函数，重复调用无副作用。**

    `draft_chapter()` 自己会调它；HTTP 壳**在算约束之前**也调一次，为的是保住 422 的
    先后顺序（文风里写了禁令词和在场角色解析不了同时发生时，作者收到的仍然是文风那一句）。
    两次调用一份实现，重复的是执行不是代码。

    ⚠️ 2026-08-25 之前它还验一个 `form`（PRODUCT / X0 / X1 / X2 四选一）并返回选中的那一臂。
    三臂随 M2 一起删了（见 `assemble.py` 的模块 docstring），**它现在没有返回值**——
    留着这个函数是因为上面那条「先后顺序」的理由跟 form 无关，文风那条校验仍然要在
    算约束之前跑。
    """
    write_rule = request.write_rule.strip()
    if write_rule:
        hits = [w for w in WRITE_RULE_FORBIDDEN_HINTS if w in write_rule]
        if hits:
            raise DraftRefused(
                "自定义文风里不能出现这些词："
                + " / ".join(hits)
                + "——这几个词是引擎自己在管的事，写进文风里只会和它打架。"
            )


def _prompt_digest(messages: Sequence[Any]) -> tuple[bytes, str]:
    """账上那一行的 `in_artifact` / `prompt_hash`。

    编码口径和另外两个花钱的能力一致（`extract/control.py::_encode_messages`、
    `agent/loop.py::_prompt_digest`）：`ensure_ascii=False` + `sort_keys=True` +
    紧凑分隔符。**三处独立编码是今天的形状**，各自只要自己前后一致就能回答
    「这两次调用发出去的是不是同一份」——但谁把其中一处改了，跨能力的比较就不成立了。
    """
    encoded = json.dumps(
        [dict(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded, sha256(encoded).hexdigest()


def _receipt(attempt: DraftAttempt, elapsed_ms: int) -> ModelCallReceipt:
    prompt_bytes, prompt_hash = _prompt_digest(attempt.messages)
    return ModelCallReceipt(
        capability=WRITER_CAPABILITY,
        schema_version=PRODUCT_DRAFT_SCHEMA_VERSION,
        model=attempt.result.model,
        finish_reason=attempt.result.finish_reason,
        prompt_hash=prompt_hash,
        prompt_bytes=prompt_bytes,
        text=attempt.result.text,
        # 供应商报的那个数，没报就是 `None`。**这一层不替它补一个估算值**
        # （账上的零和「没报」是两件事）。
        prompt_tokens=attempt.result.prompt_tokens,
        completion_tokens=attempt.result.completion_tokens,
        # **钱照抄，不在这儿算**（`provider._priced` 已经填好，理由见
        # `ModelCallReceipt.cost`：单价要按 base_url 查，而这一层只有 model）。
        cost=attempt.result.cost,
        # 同上一条：缓存命中量也是照抄。**一份回执 = 一次调用**（续写是第二次调用、
        # 第二份回执），所以这里取的是这一次 attempt 自己的数，不是整稿的合计——
        # 「前缀被弄脏了」正是要逐次看才看得出来的。
        cache_read_tokens=(
            None if attempt.result.cache is None else attempt.result.cache.read_tokens
        ),
        cache_write_tokens=(
            None if attempt.result.cache is None else attempt.result.cache.written_tokens
        ),
        elapsed_ms=elapsed_ms,
    )


def draft_chapter(
    ctx: DraftContext,
    *,
    request: ChapterDraftRequest,
    project_id: str,
    config: ProviderConfig,
    capability: ProviderCapabilities,
    plan: ResolvedCallPlan,
    events: EventStore,
    summaries: SummarySource,
    backfill: SummaryBackfill | None = None,
    on_call: Callable[[ModelCallReceipt], None] | None = None,
    db_lock: AbstractContextManager[Any] | None = None,
    client: Any = None,
) -> ChapterDraft:
    """按 `ctx` 里那个章号起一稿。**`/draft` 和 agent 的起草工具共用这一个函数。**

    Args:
        ctx: 已经算好的约束。**类型本身就是「这份约束退没退化」的答案**
            （`ResolvedConstraints` = 在场都解析成功；`UnknownCastConstraints` = 全禁）。
            章号从它身上取，**不另收一个 `chapter` 参数**——两个来源必须相等的东西，
            迟早有一天不相等。
        events / summaries: 记忆前言的两个只读来源。`events` **只在 PRODUCT 且在场里有
            人物时**被碰到；`summaries` 行内续写也要（那一格 2026-08-22 接上了）。
            三臂（X0/X1/X2）两个都不碰。
        backfill: 续写取总结时**缺 / 旧的章往待办里下单**的口子（第三个触发源）。
            `None` = 没接这条线，于是**这一稿不带滚动总结**——不是「不下单但照样带」：
            「能不能用」和「要不要补」是同一个判据的两个出口（`summary_alignment`），
            问不出前者就没有资格回答后者，而把一份可能停在旧正文上的总结喂给模型
            比不给更坏（缺是瞎，过期是说错）。整章起草那条路不看这一位。
        on_call: **每一次真的模型调用一落地就叫一次**，不等整份 `ChapterDraft` 拼好。
            出参上的 `calls` 只在**成功**那条路上交得出去，而这条路会在中途失败：
            第一次答上来了、续写那次断线（ADR 0011 D3 的第二次调用），那时钱已经付掉，
            异常一抛 `calls` 就没了。要给这一档记账的调用方传它——
            **`/draft` 不传**（它至今一行账都不写，那是另一个已知洞，这一刀不动它）。
        db_lock: **装配那一段碰库时排的队**（ADR 0022 的批内并发）。这个函数的前半截
            要查事件、查摘要，而模式二会**同时跑好几稿**、共用一条 SQLite 连接——
            一条连接被两条线程同时用是 `InterfaceError`，实测过（见
            `agent/ports.py::ToolContext.db_lock`）。锁只罩前半截，
            后面那次模型调用（几十秒）在锁外面，所以并发的收益一点没少。
            **`/draft` 和行内续写不传**：它们本来就一次只跑一稿，`None` = 一个空壳，
            那条路上的行为逐字节不变。
        client: **作者按「停」要能中途生效时**，传一个把信号包在里面的客户端
            （`agent/model.py::cancellable_client`）。原样透传给 `generate_draft` →
            `complete()`，这一层自己不认识「取消」。

            ⚠️ **「能停下来」是确定的，「省钱」不是。** 停下来做的两件事是：不再收后面的
            片、把那条 HTTP 连接关掉。**断开连接 ≠ 停止生成 ≠ 停止计费**——服务端要不要
            跟着停由供应商决定，这一层管不着，所以别把这颗按钮说成省钱按钮。
            它确定省下的是**时间**（作者不用干等一稿写完）和**后面那几次调用**
            （信号亮着 loop 就不再动手）。

            **`None`（默认）= 停不下来**，退化成「这一稿写完才停」——那不是坏了，
            那是没接线的那一档（`/draft` 和三臂都在这一档上）。

    Raises:
        DraftRefused: 文风里写了引擎自己在管的那几个禁令词。
        panel.constraints.UnresolvedCast: 由 `build_product_context` 的 label 校验转成
            的输入错误（调用方映成给作者的话）。
        generate.CallInterrupted: 作者在生成到一半时按了停。**它是 `ProviderError` 的
            子类**，所以只关心「这一稿没写成」的调用方一个字都不用改；要接住那半截的
            （`agent/drafting.py`）先捕它。已经发出去的每一次调用都进过 `on_call`。
        provider.ProviderError: 模型这一次没答上来。
    """
    check_request(request)
    write_rule = request.write_rule.strip()
    chapter = ctx.chapter
    continuation = request.mode == "continuation"

    assemble_args = {
        # ADR 0015 D3：续写的 goal 是后端常量，请求里那个已被调用方校验为空。
        "goal": CONTINUATION_GOAL if continuation else request.goal,
        "length": request.length,
        "previous_tail": request.previous_tail,
        # **逐字上文按窗口取量**（ADR 0019 边界五）。`GATE_TAIL_CODE_POINTS` 那个 800 字
        # 是 X0 对照臂当年的定义，它存在是为了证明「给得少会崩」——拿它当产品档跑，
        # 作者看到的就是「AI 写出来的东西前言不搭后语」。三臂删掉之后这里不再有分支：
        # **产品只有一条路，它按窗口算。**
        "previous_tail_limit": product_tail_limit(
            capability.max_context_tokens, plan.request_token_budget
        ),
        "write_rule": write_rule or None,
    }

    # **碰库的只有这一段**（`_with_memory` 里那几次查询），所以锁只罩这一段。
    with db_lock if db_lock is not None else nullcontext():
        if continuation:
            messages, memory = _with_rolling_summaries(
                ctx,
                assemble_args,
                project_id=project_id,
                chapter=chapter,
                language=request.length.language,
                capability=capability,
                plan=plan,
                summaries=summaries,
                backfill=backfill,
                following_text=request.following_text,
            )
        else:
            messages, memory = _with_memory(
                ctx,
                assemble_args,
                project_id=project_id,
                chapter=chapter,
                language=request.length.language,
                capability=capability,
                plan=plan,
                events=events,
                summaries=summaries,
            )
            if request.brief is not None or request.target_chapter_text:
                messages = _append_execution_plan(messages, ctx, request)

    receipts: list[ModelCallReceipt] = []
    mark = perf_counter()

    def bill(attempt: DraftAttempt) -> None:
        """一次真的模型调用 = 一份账单原料。**逐次计时**，不是整段除以二：
        续写那一次通常比第一次短，把两次摊平会让日志页上的「耗时」两行都是假的。

        **当场交给 `on_call`**，不攒到最后：攒到最后的那一份只在成功那条路上交得出去，
        而「第一次成了、续写断线」是一档真会发生的失败——那时钱已经付掉了。
        """
        nonlocal mark
        now = perf_counter()
        receipt = _receipt(attempt, max(0, int((now - mark) * 1_000)))
        receipts.append(receipt)
        if on_call is not None:
            on_call(receipt)
        mark = now

    result = generate_draft(
        messages, length=request.length, config=config, plan=plan, client=client, on_attempt=bill
    )
    return ChapterDraft(result=result, memory=memory, calls=tuple(receipts))


def _append_execution_plan(
    messages: list[dict[str, str]],
    ctx: DraftContext,
    request: ChapterDraftRequest,
) -> list[dict[str, str]]:
    """把「本稿执行计划」+「目标章当前正文」作为**独立分区**追加到产品 prompt。

    不拼进 write rule、约束块或另一份 goal_spec；安全约束由 `assemble()` 那块
    单独渲染。UnknownCast 下 `render_scene_brief(unknown_cast=True)` 按白名单
    再收窄一次（fail-closed）。
    """
    sections: list[str] = []
    if request.target_chapter_text:
        text, truncated = render_target_chapter(
            request.target_chapter_text, max_units=TARGET_CHAPTER_UNITS
        )
        sections.append("【目标章当前正文】")
        sections.append(text)
        if truncated:
            sections.append(
                "（覆盖回执：本章正文超过预算，以上只给了开头一段；"
                "需要全文请使用未来的修订能力，不能把缺的部分当成不存在。）"
            )
    if request.brief is not None:
        sections.append(
            render_scene_brief(
                request.brief,
                unknown_cast=isinstance(ctx, UnknownCastConstraints),
            )
        )
    if sections:
        messages = [*messages, {"role": "system", "content": "\n".join(sections)}]
    return messages


def _summary_is_current(
    summaries: SummarySource,
    project_id: str,
    item: ChapterSummary,
) -> bool:
    """DB 级摘要新鲜度：总结行不得早于当前快照行；无水位 = 无法证明 = 排除。"""
    watermark = summaries.snapshot_watermark(project_id, item.chapter_number)
    if watermark is None:
        return False
    return item.created_at >= watermark.snapshot_created_at


def _with_rolling_summaries(
    ctx: DraftContext,
    assemble_args: dict[str, Any],
    *,
    project_id: str,
    chapter: int,
    language: DraftLanguage | str,
    capability: ProviderCapabilities,
    plan: ResolvedCallPlan,
    summaries: SummarySource,
    backfill: SummaryBackfill | None,
    following_text: str = "",
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """行内续写的记忆：**只有前几章的滚动总结那一格**（2026-08-22）。

    ── 为什么只接这一格 ──────────────────────────────────────────────────

    这条路的预算是「作者停手 400 毫秒就要出结果」。这一格是**纯查库**：
    一次 `for_range`（总结行单章 ≤120 字）+ 一次按章号问状态，量由预算算死
    （`summary_window_chapters`），没有图查询、没有窗口边界推导。
    档案和事件那两格进不来——它们要查图、要按字数往回数整章，那是整章起草的开销。

    在这之前这一支**整个跳过记忆**，理由写在注释里也是延迟，不是正确性；
    结果是续写送出去的全部内容约 404 个字符，而库里的总结一条没给。

    ── 超预算怎么砍 ──────────────────────────────────────────────────────

    走 `select_rolling_summaries`（从最旧那头砍），**和整章起草是同一份实现**。
    两条路各写一遍的下场是「同一本书，续写记得的和起草记得的不是同几章」。

    ── 下面每一条退化分支也走 `assemble_continuation` ────────────────────────

    它不只装总结，还装【下文】（光标后那截已经写好的正文）。两格都空时它与
    `assemble()` 逐字节相同（`test_continuation_memory` 钉着这条性质），所以
    「总结这一格空了」不再是「顺手把下文也丢掉」的理由——那两格互不相干。
    """
    render = partial(assemble_continuation, ctx, following_text=following_text, **assemble_args)
    budget = MemoryBudget.for_context(
        memory_units_available(capability.max_context_tokens, plan.request_token_budget)
    ).rolling_summaries
    window = summary_window_chapters(budget)
    first = max(1, chapter - window)
    if chapter <= 1 or window <= 0:
        return render(()), memory_receipt(
            "这一稿没有前几章的总结：这一章之前没有可总结的章。"
        )
    if backfill is None:
        # 没接下单口 ⇒ 问不出「这一章的总结能不能用」⇒ 不给。见 `draft_chapter` 的
        # `backfill` 参数说明：过期的总结比缺总结更坏，而这一层无从分辨。
        return render(()), memory_receipt(
            "这一稿没有前几章的总结：这条调用没接总结待办，无从判断哪几章的总结还算数。"
        )
    reply = backfill.request(project_id, first, chapter - 1)
    kept = select_rolling_summaries(
        [
            item
            for item in summaries.for_range(project_id, first, chapter - 1)
            if item.chapter_number not in reply.unusable
        ],
        draft_chapter=chapter,
        budget=budget,
        language=language,
    )
    if not kept:
        return render(()), memory_receipt(
            "这一稿没有前几章的总结：这个窗口里还没有一章的总结是照当前正文写的。"
            "缺的那几章已经排进后台待办，下一次续写就有了。",
            unsummarized_chapters=list(reply.ordered),
        )
    return render(kept), memory_receipt(
        "这一稿带上了前几章的滚动总结（只有这一格；人物档案与事件是整章起草才装的）。",
        assembled=True,
        rolling_summaries=len(kept),
        unsummarized_chapters=list(reply.ordered),
    )


def _with_memory(
    ctx: DraftContext,
    assemble_args: dict[str, Any],
    *,
    project_id: str,
    chapter: int,
    language: DraftLanguage | str,
    capability: ProviderCapabilities,
    plan: ResolvedCallPlan,
    events: EventStore,
    summaries: SummarySource,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """产品档：已确认记忆前言 + 它的回执。装不上就说出来，不静默退化。"""
    if not isinstance(ctx, ResolvedConstraints):
        # 不知道谁在场 ⇒ 没有「谁的档案」可查（`UnknownCastConstraints` 上没有 cast，
        # 那是有意的：空列表会让下游以为「查过了，确实没人」）。
        return assemble(ctx, **assemble_args), memory_receipt(
            "这一稿没有记忆前言：不知道这一场有谁在，档案与事件记忆无从查起"
            "（约束和禁令照常生效，而且是全禁那一侧）。"
        )

    # **必须过滤 label。** `resolve_cast` 认的是花名册里的全部称呼，不只人物：
    # 作者在「在场角色」里写一个地点名（「青云城主府」），它会唯一解析成 Location
    # 节点、穿过 `require_resolved_cast()`，然后在 `build_product_context` 的
    # 「cast must contain only Character references」上炸成 500。
    # ADR 0018 之后这条路更容易走到——在场是从正文里数出来的，数出来的是称呼。
    characters = [ref for ref in ctx.characters if ref.label is NodeLabel.CHARACTER]
    if not characters:
        # 退化不是错误（约束照常生效，禁令一条不少），但**不许静默**：
        # 少了记忆前言的稿子和多了记忆前言的稿子长得不一样，作者有权知道是哪一种。
        return assemble(ctx, **assemble_args), memory_receipt(
            "这一稿没有记忆前言：在场称呼里没有一个解析成人物，"
            "档案与事件记忆无从查起（约束和禁令照常生效）。"
        )

    # **先装配，再算覆盖率**，顺序不能反：窗口边界现在由字数预算倒推
    # （`recent_event_boundary`），不再是写死的「近八章」，所以在
    # `build_product_context` 跑完之前没人知道边界在哪。
    # 总结行很短（单章 ≤120 字），整本取回来也就几十 KB，让引擎去筛比
    # 在这儿先算一遍边界安全——**边界只许有一处**。
    # **预算从模型的真实窗口倒推，不是写死的字数。**
    # 「近八章」的老毛病是绝对量：1M 窗口和 32k 窗口拿同一个数。换成字数只是
    # 换了单位，没治病。真正会缩放的是「占可用上下文的几分之几」——
    # `MEMORY_CONTEXT_SHARE`（比例）+ `MEMORY_UNITS_CEILING`（成本闸）。
    budget = MemoryBudget.for_context(
        memory_units_available(capability.max_context_tokens, plan.request_token_budget)
    )
    all_summaries = sorted(summaries.for_range(project_id, 1, chapter - 1), key=lambda s: s.chapter_number)
    # 摘要新鲜度（ADR 0033 §8.2）：无法证明与当前快照一致的总结不进 Writer。
    fresh_summaries = [
        item
        for item in all_summaries
        if _summary_is_current(summaries, project_id, item)
    ]
    product = build_product_context(
        events,
        project_id,
        characters,
        draft_chapter=chapter,
        summaries=fresh_summaries,
        budget=budget,
        language=language,
    )
    coverage = summaries.coverage(project_id, 1, product.recent_from_chapter - 1)
    return assemble_product(ctx, product, **assemble_args), memory_receipt(
        "已确认记忆前言已装配（人物档案 + 近期事件 + 更早章节滚动总结）。",
        assembled=True,
        profiles=len(product.profiles),
        recent_events=len(product.recent_events),
        background_events=len(product.background_events),
        rolling_summaries=len(product.rolling_summaries),
        unsummarized_chapters=[
            row.chapter_number for row in coverage if row.has_text and row.summary is None
        ],
    )


__all__ = [
    "PRODUCT_DRAFT_SCHEMA_VERSION",
    "WRITER_CAPABILITY",
    "ChapterDraft",
    "ChapterDraftRequest",
    "DraftRefused",
    "SummaryBackfill",
    "SummaryBackfillReply",
    "SummarySource",
    "check_request",
    "draft_chapter",
    "memory_receipt",
]
