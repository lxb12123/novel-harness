"""一次会话里工具能碰到的**全部**东西 —— 碰不到的就是碰不到，不是「记得别碰」。

规格书是 [`docs/adr/0019-agent-loop-not-graph.md`](../../../docs/adr/0019-agent-loop-not-graph.md)。
`ToolContext` 原本长在 `tools.py` 里；索引层（`index.py`）落地时它被抽到这儿，理由是
**两个模块都要收它，而它自己不许认识任何一个工具**——否则就成环了。抽出来还有一个
副作用是好的：注入口全部集中在一处，「模型能碰到什么」这个问题有唯一一页可读。

── 这里的每一个字段都是一道闸，不是一个可选参数 ──────────────────────────

`ToolContext` **只有 `StoryGraph`，没有连接、没有游标、没有 `CanonWriter`**。读图走那五个
方法，时态过滤全系统只在 `graph/queries.py` 实现一次（铁律 3）；而没有写入面意味着
「模型改了作者的 canon」**在类型层就不可能**，不是靠工具表里恰好没写那一条。

同样的道理适用于后来加进来的两个只读端口：摘要住在 SQLite 里，索引层要读它——但
**要读它就注入一个窄的只读端口，不是把 `Connection` 塞进来**。一条活连接进了这个
dataclass，「模型改不了 canon」当场从类型保证退回纪律。所以：

| 端口 | 它能干什么 | 它**不能**干什么 |
|---|---|---|
| `summaries` | 问「第 a–b 章的滚动总结覆盖成什么样」 | 生成总结（要花钱，只由作者显式触发） |
| `events` | 问「这几个人同时出现在哪些已确认事件里」 | `put_provisional` / `clone_to_scope` / `update_profile` |

`EventStore` 那个协议上有三个写方法，所以**不许把它整个收进来**——收窄成
`EventIndex` 之后，写入面在类型上就不存在了。这与「不收 `CanonWriter`」是同一条理由。

── 三个 `None` 是接线口，不是可选功能（诚实说明）──────────────────────────

`drafter` / `summaries` / `events` 缺席时，对应的工具**明确说自己没接线**，不假装能干活。
`root_path` 缺席时约束退化成**全禁**（fail-closed），而索引层的正文那几层直接说自己是瞎的
——这两种退化的方向不同是有意的：约束漏一条的代价是崩人设，索引少一条的代价是模型
少知道一件事，而后者只要**说出来**就不会变成错误结论。

── `working_chapter` 为什么在这儿，而不是某个工具的入参 ────────────────────

作者写到第几章是**会话的事实**，不是模型可以主张的东西。放进入参 = 模型能谎报自己
在看的是「现在」，于是「这条来自你还没写到的地方」这个标记由被标记者自己填。
它在这里也**永远不写进任何一行数据**（约束 10 / ADR 0006）：这个 dataclass 上没有写路径。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..draft.context import DraftContext
from ..draft.product_context import memory_units_available
from ..draft.rolling_summary import ChapterSummaryStatus
from ..events import EventView
from ..extract.call_audit import ModelCallReceipt
from ..graph import InformationScope, StoryGraph


class ToolRefused(Exception):
    """工具拒绝执行，且**理由要说给模型听**（歧义的称呼、没接线的能力、不是人物的节点）。

    它不是 bug 的信号，是编排层的一个正常出口：`dispatch()` 把它变成一条
    `ok=False` 的 `ToolOutcome` 交回对话，模型据此换个问法重来。

    ── `calls`：**拒绝不等于没花钱** ────────────────────────────────────────

    表里六个工具有五个是纯读，拒了确实一分钱没花。第六个不是：`draft_chapter`
    一次是一到两次真的模型调用（生成 + 至多一次续写，ADR 0011 D3），而
    **第一次答上来、续写那次断线**是一档真会发生的失败——那时钱已经付掉了。

    这些回执必须跟着拒绝一起交回 `dispatch`，否则它们连同 `ToolOutcome.calls`
    一起消失，而**账本和成本闸同时失明**（`agent/loop.py::bill` 是同一个入口）。
    方向和 `/draft` 那个已知洞一样是偏低，而偏低的数在界面上自称是全部。
    """

    def __init__(self, message: str, *, calls: tuple[ModelCallReceipt, ...] = ()) -> None:
        super().__init__(message)
        self.calls: tuple[ModelCallReceipt, ...] = calls
        """拒之前**已经真的发生过**的那几次模型调用。默认空 = 这次拒绝没花钱。"""


class DraftAsk(BaseModel):
    """起草第 N 章的一稿。

    **这里没有、也永远不会有约束字段**（ADR 0019 边界二）：不许说破什么由后端当场
    从第 N 章重新算，你上一轮看到的那份清单对这一章可能已经过期了。

    它和 `DraftFn` 放在一起而不是和别的工具入参放在一起，是因为它是**注入契约的一半**：
    起草侧收的就是 `(DraftAsk, DraftContext)`，而这两件东西里都没有模型给的约束。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(
        ge=1,
        description="起草第几章。约束由后端按这个章号当场计算。",
    )
    goal: str = Field(
        min_length=1,
        description="这一场要写什么（一两句话说清目标）。",
    )


class DraftProduct(BaseModel):
    """起草侧交回来的东西：**一稿正文 + 它花了多少 + 它落没落盘**。

    ── 为什么不是一个裸 `str`（3.4 报的那条余债）────────────────────────────

    裸 `str` 那一版有两个洞，而它们都不是「以后再说」那一档：

    1. **loop 的成本闸看不见起草。** `TurnLimits.max_calls_per_step` 只管次数不管钱，
       而表里恰恰有一个工具每次是一次真的模型调用。回执带回 `calls` 之后，那几笔钱
       进 `charged`，`max_tokens` 那道闸才罩得住它。
    2. **账上没有它。** 「`/draft` 一行 `model_call` 都不写」是这个仓库记在
       `docs_dev` 里的已知病；把起草接进一个 `ledger` 必填的地方却不带回执，
       等于把那个洞原样搬进来，而且是明知故犯。

    ── `saved` / `note` 为什么在这儿（ADR 0021）────────────────────────────

    起草完**直接写磁盘，不弹框**。写没写成只有起草侧知道（磁盘在它手里），而模型
    要据此决定下一句说什么——「我写进第 12 章了」和「你刚改过这一章，我没敢覆盖」
    是两句完全不同的话。所以它是回执的一部分，不是一个副作用。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    calls: tuple[ModelCallReceipt, ...] = ()
    """每一次真的模型调用一份（生成 + 至多一次续写）。**loop 负责落账和计闸。**"""

    saved: bool = False
    """这一稿写没写进磁盘上的那一章（ADR 0021）。"""

    note: str = ""
    """给模型看的一句话：写进哪儿了 / 为什么没写。**空 = 没什么要交代的。**"""


DraftFn = Callable[[DraftAsk, DraftContext], DraftProduct]
"""起草的接线口。**收两件东西，而约束那件是后端算的**（边界二）。"""


LedgerFn = Callable[[ModelCallReceipt], None]
"""记账的接线口。**必填，没有 `None` 这个取值**（`run_turn` 的参数）。

它收一份原料就落一行 `model_call`。持有 conn 的那一层去实现它——
`ToolContext` 上没有 conn 是边界一的一部分（见模块 docstring），别为了记账把它塞回去。

**它不在 `ToolContext` 上**，也不该在：工具不记账，工具只把自己花掉的那几笔
（`DraftProduct.calls`）交回给 loop，由 loop 交给这个函数。
"""


@runtime_checkable
class SummaryIndex(Protocol):
    """滚动总结的**只读**端口。

    **只有 `coverage`，故意没有 `for_range`。** 差别就是「缺摘要不许静默」：`for_range`
    只返回有的那些，缺哪几章得靠调用方拿区间去减——而没人会记得减，于是「那几章什么都
    没发生」和「那几章我没总结过」在返回里长得一模一样。`coverage` 把三态
    （没写 / 写了没总结 / 有）都物化出来，索引层照抄它就不会撒谎。

    `draft.rolling_summary.SummaryStore` 结构上已经满足它，**这里不实现第二个**。
    """

    def coverage(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummaryStatus]: ...


@runtime_checkable
class EventIndex(Protocol):
    """已确认事件的**只读**端口（`graph.sqlite_events.SqliteEventStore` 结构上满足它）。

    收窄自 `events.EventStore`，砍掉的是它的三个写方法。这不是洁癖：那个协议进了
    `ToolContext` 的那一刻，模型就有了一条把 PROVISIONAL 事件写进库的路径，
    而「模型改不了作者的 canon」本来是类型保证。
    """

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]: ...


@dataclass(frozen=True)
class ToolContext:
    """一次会话里工具可用的全部能力。见模块 docstring。"""

    store: StoryGraph
    project_id: str

    root_path: str | None = None
    """项目根目录。`None` = 读不到正文 ⇒ 在场推不出来 ⇒ 约束**退化成全禁**（fail-closed）。"""

    drafter: DraftFn | None = None
    """起草实现。`None` = `draft_chapter` 明确回一句「没接线」，而不是假装写了一稿。"""

    summaries: SummaryIndex | None = None
    """滚动总结的只读端口。`None` = `chapter_summaries` 明确回一句「没接线」。"""

    events: EventIndex | None = None
    """已确认事件的只读端口。`None` = 人物轴的「事件」那一条明确说自己是瞎的。"""

    working_chapter: int | None = None
    """作者此刻在写第几章。**只用来标「这是你还没写到的地方」，不挡任何东西。**

    ADR 0019 边界二自己写着「残余代价（接受）」：模型的推理可能被陈旧/超前的认知污染，
    但**写出来的正文受的是当前章的约束**（起草工具只收章号，约束后端当场算）。硬挡会把
    「因为知道第 200 章会怎样才回头改第 40 章」这个正常修稿动作一起挡掉，那不是这条
    ADR 要的东西。所以这里只标不挡——模型知道自己在看未来就能自我克制，而作者在
    聊天里也看得见，**而「作者看得见」正是那条 ADR 接受这个代价的前提之一**。

    `None` = 不知道他在写第几章 ⇒ **不标**，并且把「没标」这件事说出来
    （猜一个数比不标更坏：标错方向的提示比没有提示更容易被信）。

    ── 「只标不挡」在 3.3 之后有一个例外，写在这儿免得两边各说各的 ──────────────

    `agent/loop.py::project()` 拿它当**投影坐标**，会丢掉绑在**更后面**的章上的工具返回
    （ADR 0019 边界五）。那仍然不是「挡」——工具照样查得到任何一章，模型在**当前这一步**
    也照样读得到返回；被丢的是它**下一步**还看不看得见。

    判据是 `>` 不是 `!=`，而这条差别就是为了这个字段：模型可以为任意一章查约束、起草，
    所以「坐标 ≠ 模型正在写的那一章」是常态。`!=` 在那种常态下会把**正确的那份长清单**
    删掉、把过期的短清单留下（`ch40 ⊇ ch90`）；`>` 让坐标错的时候错在 fail-closed 那侧。
    实测在 `tests/test_agent_loop_projection.py` 第二节。
    """

    max_context_tokens: int | None = None
    """模型的上下文窗口（`draft.capabilities.ProviderCapabilities.max_context_tokens`）。

    `None` = 能力表没登记这个模型 ⇒ `memory_units_available` 回落到
    `DEFAULT_MEMORY_BUDGET.total`。**这儿不猜第二个数**：猜大了的后果是发出去被供应商拒。
    """

    reserved_output_tokens: int = 0
    """这次调用给输出留的 token（`draft.capabilities.CallPlan.request_token_budget`）。"""

    @property
    def return_units(self) -> int:
        """**一次工具返回最多给多少字**（`count_units` 口径）。

        ── 为什么是从模型窗口倒推的，而且倒推只写一次 ────────────────────────

        直接复用 `draft.product_context.memory_units_available()`：那儿已经握着
        「占可用上下文的几分之几」（`MEMORY_CONTEXT_SHARE`）和「再大的窗口也不超过多少字」
        （`MEMORY_UNITS_CEILING`，那是**成本闸不是能力闸**）这两个自由参数。
        在这儿再写一份倒推 = 又一个 1M 窗口和 32k 窗口拿同一个绝对数字的地方，
        而那正是 2026-08-10 刚从起草侧清掉的病。

        ── 为什么是「一次返回」而不是像 `MemoryBudget` 那样切三份 ──────────────

        `MemoryBudget.for_context()` 把一份额度切给**同一个 prompt 里同时装配的三段**；
        索引的四层不是那个形状——它们是**四次不同时刻的工具调用**，没有哪一刻它们同框。
        把 4:1:2 套到这儿是拿一个比例去解一个它没在解的问题。这里要的是一条
        「单次返回的天花板」，因为工具返回会**累积**进持久化的对话，一次塞满窗口的返回
        在第三轮就把会话挤爆了。取值等于「一次起草的记忆前言」——那是同一个数量级里
        唯一有先例的量，不是新拍的。
        """
        return memory_units_available(self.max_context_tokens, self.reserved_output_tokens)

    @property
    def future_from_chapter(self) -> int | None:
        """第几章起算「作者还没写到那儿」。`None` = 不知道他写到哪儿，这次不标。"""
        return None if self.working_chapter is None else self.working_chapter + 1

    def is_future(self, chapter: int) -> bool:
        """第 `chapter` 章在不在作者当前进度之后。不知道进度时一律 `False`（不标）。"""
        return self.working_chapter is not None and chapter > self.working_chapter


__all__ = [
    "DraftAsk",
    "DraftFn",
    "DraftProduct",
    "EventIndex",
    "LedgerFn",
    "ModelCallReceipt",
    "SummaryIndex",
    "ToolContext",
    "ToolRefused",
]
