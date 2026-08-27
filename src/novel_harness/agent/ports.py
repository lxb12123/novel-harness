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
| `summaries` | 问「第 a–b 章的滚动总结覆盖成什么样」 | 生成总结（要花钱；2026-08-25 起只有两个系统自动触发，谁都不能手动点） |
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
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..draft.context import DraftContext
from ..draft.length import DraftLanguage
from ..draft.product_context import memory_units_available
from ..draft.rolling_summary import ChapterSummaryStatus, SummarySnapshotWatermark
# 轨道那几个模块里，**只借 `TrackClash` 这一个名字**：它是三个数（第几句 / 跟第几章 /
# 冲突类型），手里没有轨道原文。`Track` / `build_track` / `chapters_after_mentioning` /
# `AdvisoryOutcome` 一律不许进 `agent/`——这一层的整个意义就是「agent 拿不到轨道」。
# `tests/test_track_isolation.py::test_the_agent_layer_borrows_exactly_one_name...` 钉着它。
from ..advisory_review import TrackClash
from ..events import EventView
from ..extract.call_audit import ModelCallReceipt
from ..graph import InformationScope, StoryGraph
from ..calibration.models import AuthorTurnRef
from ..calibration.store import CalibrationStore
from .candidates import DraftCandidate, StoredDraft


class ToolRefused(Exception):
    """工具拒绝执行，且**理由要说给模型听**（歧义的称呼、没接线的能力、不是人物的节点）。

    它不是 bug 的信号，是编排层的一个正常出口：`dispatch()` 把它变成一条
    `ok=False` 的 `ToolOutcome` 交回对话，模型据此换个问法重来。

    ── `calls`：**拒绝不等于没花钱** ────────────────────────────────────────

    表里只有一个工具花钱（其余是纯读、落盘那个只动磁盘、问作者和记规矩那两个只转述）。
    剩下那一个不是：`draft_chapter`
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
    """起草第 N 章的一稿（**chapter + calibration_id**，ADR 0033）。

    **这里没有、也永远不会有约束字段**（ADR 0019 边界二）：不许说破什么由后端当场
    从第 N 章重新算，你上一轮看到的那份清单对这一章可能已经过期了。

    **也没有自由文本 goal**（ADR 0033）：`goal_spec` 只从不可变校准产物读取——
    外层 Agent 不负责抄写事实文字或目标散文，Writer 拿到的是校准层实际产出的版本。

    它和 `DraftFn` 放在一起而不是和别的工具入参放在一起，是因为它是**注入契约的一半**：
    起草侧收的就是 `(DraftAsk, DraftContext)`，而这两件东西里都没有模型给的约束。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(
        ge=1,
        description="起草第几章。约束由后端按这个章号当场计算。",
    )
    calibration_id: str = Field(
        min_length=1,
        description=(
            "seal_scene_brief 返回的那个不可变编号。"
            "起草目标只从它读取，你不需要也不应该在这里传任何目标文字。"
        ),
    )


class DraftProduct(BaseModel):
    """生成一稿之后交回来的东西：**它是哪一稿 + 它花了多少**。

    ── 这里**没有正文**，是有意的（ADR 0022）────────────────────────────────

    正文进了这个出参，就会跟着 `ToolOutcome.content` 进对话历史，而跟模型说话的接口是
    **无状态**的：每一轮把整个消息数组从头重发。三稿 ≈ 9,000 字从生成那一刻起
    **每一轮都在被重发**，直到会话结束——而默认没有任何东西会去拿掉它。
    所以正文落在候选表里（`agent/candidates.py`），这儿只带回**认得出是哪一稿**的那几样：
    id、定长预览、那一稿的自述。要全文得按 id 单取一次（`DraftDesk.recall`），
    而那一次是模型显式决定的。

    ── 为什么不是一个裸 `str`（3.4 报的那条余债）────────────────────────────

    裸 `str` 那一版有两个洞，而它们都不是「以后再说」那一档：

    1. **loop 的成本闸看不见起草。** `TurnLimits.max_calls_per_step` 只管次数不管钱，
       而表里恰恰有一个工具每次是一次真的模型调用。回执带回 `calls` 之后，那几笔钱
       进 `charged`，`max_tokens` 那道闸才罩得住它。
    2. **账上没有它。** 「`/draft` 一行 `model_call` 都不写」是这个仓库记在
       `docs_dev` 里的已知病；把起草接进一个 `ledger` 必填的地方却不带回执，
       等于把那个洞原样搬进来，而且是明知故犯。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: DraftCandidate
    """这一稿的摘要行（id / 第几稿 / 字数 / 自述 / 定长预览）。**正文不在里面。**"""

    calls: tuple[ModelCallReceipt, ...] = ()
    """每一次真的模型调用一份（生成 + 至多一次续写）。**loop 负责落账和计闸。**"""


class LandingReport(BaseModel):
    """把某一稿写进那一章之后交回来的东西（ADR 0021 的机制，ADR 0022 把它拆成了一个动作）。

    **写没写成只有落盘侧知道**（磁盘在它手里），而模型要据此决定下一句说什么——
    「我写进第 12 章了」和「你刚改过这一章，我没敢覆盖」是两句完全不同的话。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    landed: bool = False
    note: str = ""
    """说给模型听的那一句：写进哪儿了 / 为什么没写。**每一种结局都要说得出口。**"""


DraftFn = Callable[[DraftAsk, DraftContext], DraftProduct]
"""生成一稿的接线口。**收两件东西，而约束那件是后端算的**（边界二）。**它不落盘。**"""


@runtime_checkable
class DraftDesk(Protocol):
    """起草这件事的**全部**接线口：生成 / 落盘 / 按 id 读回。

    ── 为什么是三个动作而不是一个（ADR 0022）──────────────────────────────

    | 动作 | 花钱 | 动书 | 谁决定 |
    |---|---|---|---|
    | `write` | 花 | **不动** | 模型 |
    | `land` | 不花 | 动 | 模型（**仍然不问作者**，ADR 0021 精神不变） |
    | `recall` | 不花 | 不动 | 模型（只在作者要合并两版时） |

    方向清楚时模型「生成 + 立刻落盘」，作者的体验和 ADR 0021 一模一样；
    方向不清楚时它生成几稿、**不落盘**，把 id + 预览 + 自述摆在对话里让作者挑。
    合成一个动作的那一版下，一批三稿的真实行为是「第一稿落盘 ⇒ 后两稿的底稿全过期 ⇒
    被 sha 闸拒掉」——作者要三版、拿到一版、付了三份钱。

    ── 为什么是一个端口而不是三个字段 ────────────────────────────────────

    三件事握着**同一批东西**（那条连接、那个 `GraphStore`、那张候选表）。拆成三个注入口，
    装配层就能只接其中两个，而「生成得了、落不了盘」这种半接线状态没有任何东西会报错。
    """

    def write(self, ask: DraftAsk, ctx: DraftContext) -> DraftProduct:
        """生成一稿并收进候选表。**不动书。**"""
        ...

    def land(self, candidate_id: str) -> LandingReport:
        """把某一稿写进它那一章。**不问作者**，唯一的闸是「拒绝覆盖他更晚改过的那一章」。"""
        ...

    def recall(self, candidate_id: str) -> StoredDraft:
        """按 id 把一稿的全文拿回来。找不到时 `raise ToolRefused`。"""
        ...


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

    def snapshot_watermark(
        self,
        project_id: str,
        chapter_number: int,
    ) -> SummarySnapshotWatermark | None: ...


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

    language: DraftLanguage = DraftLanguage.ZH
    """这本书的语言（`Project.language`）。**只管人设/工具 schema 用哪种语言**
    （国际化第三批，`loop.py::run_turn` 拿它选 `tool_declarations`/规矩前缀）——
    不影响任何判断逻辑，也不是这一轮投影的坐标（那是 `working_chapter` 的事）。
    默认 ZH：这个仓库绝大多数既有调用方（尤其是测试）构造 `ToolContext` 时
    压根不知道语言这回事，给它们一个不用改的默认值，比逼着几十个调用点都补一位
    更不容易漏改一处。生产上唯一的构造点（`api/chat.py::_tool_context`）会显式传。
    """

    root_path: str | None = None
    """项目根目录。`None` = 读不到正文 ⇒ 在场推不出来 ⇒ 约束**退化成全禁**（fail-closed）。"""

    drafter: DraftDesk | None = None
    """起草那一摊（生成 / 落盘 / 读回，见 `DraftDesk`）。
    `None` = 那三条工具各自明确回一句「没接线」，而不是假装写了一稿。"""

    summaries: SummaryIndex | None = None
    """滚动总结的只读端口。`None` = `chapter_summaries` 明确回一句「没接线」。"""

    events: EventIndex | None = None
    """已确认事件的只读端口。`None` = 人物轴的「事件」那一条明确说自己是瞎的。"""

    calibrations: CalibrationStore | None = None
    """写前校准的非 Canon artifact 存储（ADR 0033，迁移 017）。

    它是 `ToolContext` 上**第一个带写路径的端口**，但写面只有两张表：
    `calibration_artifact` / `calibration_handoff_outbox`。Canon、正文、会话
    仍然一个都碰不到——「模型改不了作者的 canon」没有被这一条打开。

    `None` = `calibrate_scene` / `seal_scene_brief` 明确回一句「没接线」。
    """

    author_turn: AuthorTurnRef | None = None
    """当前会话里**作者最新一条消息**的服务端绑定（turn id + 原话哈希）。

    模型不能自报或替换：`calibrate_scene` / `seal_scene_brief` 从这儿取绑定，
    入参模型上**没有**这个格子。`None` = 这段会话还没有作者消息可绑定，
    校准工具明确拒绝。
    """

    frontier_chapter: int | None = None
    """全书最大章号（`focus.frontier_chapter`）。**只用来判「作者是不是在改一章旧的」。**

    `working_chapter < frontier_chapter` ⇒ 这一轮的投影里多一句提醒，让模型知道
    后面还有已经写完的章、可以去调 `check_track`（轨道阶段 3）。
    **判断在系统这边，调用在模型那边**——做成「必须验」就把 ADR 0019 的
    「循环归模型，不归代码」破了。

    `None` = 这一轮没算（装配层没给）⇒ 不提醒。往「不提醒」那一侧偏是安全的一侧：
    多提醒一次只是浪费几十个字，而这一位算错不会让任何东西泄漏。
    """

    track_check: TrackCheck | None = None
    """轨道核对（轨道阶段 3）。`None` = `check_track` 明确回一句「没接线」。

    **这一位就是 ADR 0019 边界一那句话的字面落点**：「工具表就是权限边界」——
    **那个工具有权看轨道，agent 没有。** 端口收进来的是一个已经判完的结论
    （`TrackClash`：第几句 / 跟第几章 / 冲突类型），轨道原文一个字都不过这条边。
    """

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

    db_lock: AbstractContextManager[Any] | None = None
    """**碰库要排的那道队**（ADR 0022 的批内并发）。`None` = 这一轮不并发，不用排。

    ── 这不是防御性编程，是 CPython 的一条硬事实 ──────────────────────────

    一批稿可以同时跑（起草没有副作用了），而它们共用装配层那**一条** SQLite 连接。
    `check_same_thread=False` 只是把「别的线程不许碰」这个断言关掉，**它不让连接变成
    线程安全的**：`sqlite3` 的预备语句缓存是按连接的，两条线程同时执行同一句 SQL 会拿到
    同一个 statement，当场 `InterfaceError: bad parameter or other API misuse`。
    实测过（4 线程 × 同一句 SELECT，几百次之内必炸），不是理论风险。

    所以：**`concurrent=True` 的工具，它碰库的每一段都必须在这把锁里**。
    慢的那一段（一次模型调用，几十秒）在锁外面——所以排队不影响并发的收益，
    库那几毫秒排成一队，HTTP 那几十秒是并行的。

    loop 那条线程不需要这把锁：批执行器**等整个并发窗口跑完**才回到串行处理
    （落库、记账、落盘都在那之后），所以工作线程和 loop 线程永远不会同时碰库。
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

    @property
    def db_guard(self) -> AbstractContextManager[Any]:
        """碰库那几行外面套的东西。没接锁时是一个空壳（不并发就不用排队）。"""
        return nullcontext() if self.db_lock is None else self.db_lock


class TrackVerdict(BaseModel):
    """一次轨道核对的结论。**只有结论，没有轨道。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    clashes: tuple[TrackClash, ...] = ()
    """`TrackClash` 是三个数（第几句 / 跟第几章 / 冲突类型），**不许长第四个**——
    多一个 `reason: str` 就等于给轨道原文开了一条进出参的路
    （`advisory_review.TrackClash` 的注释讲了整件事，那儿有一条钉字段名的守卫）。"""

    note: str = ""
    """**零要带着理由**（§10 约束 8）：「没抵触」和「压根没核对」（在最前沿写 /
    核对模型没配 / 这一份已经付过钱）在模型眼里长成同一个空清单，而这两件事的
    下一步动作完全相反。"""


@runtime_checkable
class TrackCheck(Protocol):
    """轨道核对的只读端口。**装配层持有轨道，agent 只拿得到结论。**"""

    def __call__(self, chapter: int) -> TrackVerdict: ...


__all__ = [
    "DraftAsk",
    "DraftCandidate",
    "DraftDesk",
    "DraftFn",
    "DraftProduct",
    "EventIndex",
    "LandingReport",
    "LedgerFn",
    "ModelCallReceipt",
    "StoredDraft",
    "SummaryIndex",
    "ToolContext",
    "TrackCheck",
    "TrackVerdict",
    "ToolRefused",
]
