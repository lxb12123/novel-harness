"""写作助手（模式二）的 HTTP 壳 —— **`agent/` 这一层的唯一调用方**。

规格书是 [ADR 0019](../../docs/adr/0019-agent-loop-not-graph.md)。引擎侧四个文件
（`ports` / `index` / `tools` / `loop`）在这之前**在 `src/` 里一个调用方都没有**——
这个仓库栽过四次「能力建好了、最后一厘米没接」（`docs_dev/2026-08-06-…`），
这个文件就是那根线。

九条路由：开一段 / 列出来 / 看一段 / 删一段 / **跑一轮**（两种收法）/ **停** /
**现在生效的规矩**（摆出来 / 取消一条，见最下面那一节）。

── 跑一轮为什么有两条路由，而它们**不可能漂**（ADR 0024 决策二/三）────────────

| 路由 | 收法 | 谁在用 |
|---|---|---|
| `POST …/turn` | 跑完才回，一个 `TurnReceipt` | `curl`、老客户端、**「换回请求/响应」那条退路** |
| `POST …/turn/events` | `text/event-stream`，边跑边喊，最后一帧还是同一个 `TurnReceipt` | 工作台 |

两条走的是**同一个 `_TurnRun`**，差别只有一个参数（`on_event` 传不传），而
`agent/loop.py` 保证了「不传 = 行为逐字节不变」。所以第一条不是第二条的旧版本，
它就是第二条把事件流摘掉之后剩下的东西。`tests/test_chat_stream.py` 有一条
拿同一个剧本跑两遍、逐字段对拷回执的断言钉着这句话。

**引擎不认识传输**：`agent/` 里没有一个字节提到 SSE。这一层做三件适配器的活——
把 `TurnEvent` 序列化成帧、把慢/断的消费者挡在引擎外面（见 `_turn_frames`）、
把「停」翻成 `Cancellation`（那条仍然是 `POST …/stop`，见下）。

**为什么不是 WebSocket。** 双向那一条能把「停」变成同一条 socket 上的一条消息，
于是「过期的 stop」那道比对（`_Running.stop`）结构上就不需要了。**但它买错了东西**：
`agent/loop.py::EventFn` 已经定过「一个掉线的浏览器不该把这一轮弄崩」——也就是说
socket 断了这一轮**还在跑、还在花钱**。把「停」搬到那条 socket 上，
等于让作者唯一的插手方式跟着显示通道一起死。`POST …/stop` 是**另一个请求**，
它在流断掉之后照样送得到。第二条理由是这一层的形状：这个壳每一条路由都是同步的，
`run_turn` 是阻塞的、握着一条 `check_same_thread=False` 的连接；
`StreamingResponse` 收一个**同步生成器**，那一轮仍然跑在线程池里（今天就是这样），
而 FastAPI 的 WebSocket 端点必须是 `async def`，那会在刚刚钉死的并发模型上再叠一层。

── 三件这一层必须自己做对的事 ────────────────────────────────────────────

1. **`working_chapter` 不是可选项。** `project()` 在 `chapter is None` 时**不过滤**，
   而那不是安全默认值，是「没接线」默认值——正是 fail-open 的那一侧（第 90 章的
   `must_not_reveal` 是第 40 章那份的子集，留着它模型就以为只有两条不能说）。
   所以 `TurnBody.chapter` 是**必填**，没有默认值。它是查询坐标（AS OF），
   **不写进任何一行数据**（约束 10：作者永不填章号作为声明坐标；`ToolContext` 上
   根本没有写路径）。

2. **账必须真的落到日志页上。** `run_turn` 产出 `ModelCallReceipt`，它的字段是照着
   `record_call` 的签名长的——所以这里是一次**平移**，不是一次翻译。
   （翻译的地方就是能悄悄漏字段的地方，而漏掉的那个字段会让日志页少算一笔钱。
   `tests/test_chat_api.py` 有一条按字段名对签名的断言钉着这句话。）
   验收不是「`model_call` 里有行」，是 **`GET /activity` 里看得见**：
   `activity._CAPABILITY_LABEL` 里那条 `agent → 写作助手`。

3. **对话的原文不是这条路由的出参。** 出去的是一份**投影**：作者说的话 + 助手说的话。
   工具返回一条都不出去——它们是 `model_dump_json()` 出来的内部模型，里面躺着
   `NodeRef` 的裸 id（`secret:…:01J…`）。那种东西一旦被前端原样渲染就是屏幕上的
   研发术语（`frontend/src/test/screenGuard.ts` 的第三张网认的就是 `前缀:标识`），
   而**收窄的最强形态是根本没发出去**（同 `activity.py` 那次把 `params_json` 从
   SELECT 里删掉）。

── 边界三：草稿存哪儿 ────────────────────────────────────────────────────

`chat_session` / `chat_message` 里**没有一列是正文的落点**，也没有任何读路径拿它们
回答「第 N 章是什么」——正文的真相源在磁盘上（ADR 0007），版本锚在 `chapter_snapshot`。

**2026-08-11（ADR 0021）起草那一稿直接写磁盘**，走的仍然是那条既有路径
（`importer.save_chapter`，也就是 `PUT …/chapters/{n}/text` 调的同一个函数：
磁盘先、DB 跟）。落盘的动作在 `agent/drafting.py` 造的那个闭包里，**这两张表一列都没多**。
唯一的闸是「拒绝覆盖作者比它更晚改过的那一章」，不是「问一句可以吗」。

**反过来仍然是错的**：把助手说过的话写进 `chapter_snapshot`，「哪一份正文是真的」
就有了第二个答案，而且是作者在界面上看得见的那一个。落盘之所以不违反这一条，
是因为写的是**磁盘**，快照是 `sync` 从磁盘读回来的结果——方向没有反。

── 作者的规矩：这一层只搬运，判据一条都不在这儿（ADR 0023 决策二）──────────

ADR 0023 押的退路是两件事：**看得见 + 能取消**。引擎侧齐了（`agent/rules.py`），
在这两条路由之前它在浏览器里**一个调用方都没有**——也就是说那条退路只兑现了一半：
规矩记得下、过得了期，而作者看不见它、也点不掉它。

**这一层不许自己写第二套判据**，两条都是纯搬运：

| 问题 | 谁回答 |
|---|---|
| 这一章现在哪几条生效 | `rules.live_rules()` |
| 有几条已经不作数了（零态的那句理由） | `rules.expired_rule_count()` |
| 取消一条要动哪几份 | `rules.revocation()` + 读端的 `_revoked_indices` |

最后那一行是**这条按钮唯一会假绿的地方**：同一条规矩记过两遍时读端只摆最后一条，
作者点的也只能是那一条，**而前面那一遍已经是章级的**。只划掉那个下标 =
按钮按了、规矩还在、且没有任何东西会报错。引擎那一侧按**身份**（同一章的同一串字）
撤掉每一份，所以这儿收一个 `seq` 就够——**在路由层重写一遍那个循环就是第二份判据**。
"""

from __future__ import annotations

import queue
from collections.abc import Iterator
from contextlib import AbstractContextManager
from threading import Event, Lock, RLock, Thread
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    EventFn,
    LedgerFn,
    ModelCallReceipt,
    ModelPort,
    PersistFn,
    Projection,
    Role,
    StopReason,
    TurnEvent,
    TurnLimits,
    run_turn,
    stop_wording,
)
from ..agent.candidates import DraftCandidate, DraftCandidateStore
from ..agent.drafting import ChapterDesk, chapter_drafter
from ..agent.model import ProviderModelPort, agent_call_plan
from ..agent.ports import ToolContext
from ..agent.rules import AuthorRule, expired_rule_count, live_rules, revocation
from ..agent.tools import AuthorQuestion
from ..agent.store import ChatConcurrency, ChatSessionRow, ChatStore, StoredChat
from ..db import Connection
from ..draft.capabilities import CapabilityError, ProviderCapabilities, ResolvedCallPlan
from ..draft.provider import ProviderConfig
from ..draft.rolling_summary import SummaryStore
from ..extract.call_audit import record_call
from ..graph.sqlite_events import SqliteEventStore
from ..graph.store import GraphStore
from ..ids import EntityType, new_id
from .deps import agent_provider_config, get_conn, get_store, load_project, model_configuration_error


router = APIRouter()

ChatId = Annotated[str, Path(min_length=1)]

AGENT_PARALLEL_TOOLS = 3
"""一批之内最多几个工具同时跑（`TurnLimits.parallel_tools`）。

**放开它的动作只许发生在这一层**，理由是一条只有这一层知道的事实：
`api/deps.py::get_conn` 是拿 `check_same_thread=False` 开的连接，跨线程用得了；
CLI 和测试里那些是默认的 `True`，同一句 SQL 换条线程执行就当场 `ProgrammingError`。
所以 `agent/loop.py` 的默认值是 1（串行），而不是「保守起见先关着」。

**为什么是 3 而不是 6**（`max_calls_per_step` 那个上限）：并发的那几条今天只有起草，
而三稿正是作者会要的那个数（ADR 0022 的例子从头到尾是「写三个版本让我挑」）。
再往上加同时在飞的模型调用，省下来的时间越来越少、一次撞上限速的概率越来越大，
而**撞上之后作者看到的是几稿一起失败**。
"""

TITLE_FROM_FIRST_SAID = 24
"""会话还没有名字时，拿作者第一句话的前多少个字当标题。

**只在标题为空时做一次**，之后作者改了就是他的。不做的话侧栏上是一排「新的对话」，
而作者要找的是三个月前那一段——那时唯一能认出它的东西就是他当时说的第一句话。
"""


class ChatBusy(RuntimeError):
    """这段对话正在跑一轮。"""


StopVerdict = Literal["stopped", "idle", "stale"]
"""一次「停」的三种结局。**机器码，一个字都不上屏**（措辞在 `_STOP_WORDING`）。

| 取值 | 意思 |
|---|---|
| `stopped` | 信号送到了正在跑的那一轮 |
| `idle` | 这一刻本来就没在跑 —— **不是失败** |
| `stale` | 在跑，但**不是它想停的那一轮** —— 忽略掉，也不是失败 |
"""


class _Running:
    """**正在跑的那几轮**，进程内。键是 `(项目, 会话)`。

    存在的理由只有一个：`POST …/stop` 是**另一个请求**，它要够得着正在跑的那一轮手里
    那个 `Cancellation`。跨进程不需要——重启之后没有任何一轮在跑，而库里那段对话
    照旧读得回来（resume 靠的是「缺哪几个 `tool_result`」，不是一个 running 标志位）。

    **不落库**也是有意的：一个存在库里的 `status='RUNNING'` 会在进程崩掉之后永远
    卡在那儿，而清它需要一个没人会写的看门狗。

    ── 为什么每一轮还带一个标识（2026-08-12）────────────────────────────────

    `begin` 在已经有一轮活着时抛 `ChatBusy`，所以同一段对话不可能同时有两轮——
    大部分场景天然安全。**但这个序列会出事**：

        作者按停 → 请求在路上 → 上一轮自己跑完了 → 作者又发一句
        → 新一轮开始 → 停止请求到达 → **杀掉新的那一轮**

    作者看到的是「我刚发出去的那句话，它自己停了」，而他按的那一下是给上一轮的。
    所以 `stop` 要比对**它想停的是哪一轮**，对不上就忽略（Vercel AI SDK 文档里
    那条「track which stream is active，stale stop 要忽略」是同一件事）。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._live: dict[tuple[str, str], tuple[str, Cancellation]] = {}

    def begin(self, key: tuple[str, str], run_id: str = "") -> Cancellation:
        with self._lock:
            if key in self._live:
                raise ChatBusy("这段对话正在跑上一轮")
            signal = Cancellation()
            self._live[key] = (run_id, signal)
            return signal

    def end(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._live.pop(key, None)

    def stop(self, key: tuple[str, str], run_id: str = "") -> StopVerdict:
        """把信号交给它想停的那一轮。**对不上就一个字都不动。**

        `run_id` 两边**任一为空就不比对**：老客户端（和 `curl`）不报标识，那时的行为
        和 2026-08-12 之前一模一样。**新前端每一轮都报**，所以那条竞态在产品上是关着的。
        比对不上不是失败，也不是「没在跑」——它是第三档（见 `StopVerdict`）。
        """
        with self._lock:
            live = self._live.get(key)
        if live is None:
            return "idle"
        running_id, signal = live
        if run_id and running_id and run_id != running_id:
            return "stale"
        signal.stop()
        return "stopped"

    def running(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return key in self._live

    def clear(self) -> None:
        with self._lock:
            self._live.clear()


LIVE = _Running()
"""模块级单例（同 `api/autopilot.py::JOBS`）。测试用 `LIVE.clear()` 隔离。"""


# ══════════════════════════════════════════════════════════════════════════
# 出参：**对话的投影，不是对话的原文**
# ══════════════════════════════════════════════════════════════════════════


class ChatMessageView(BaseModel):
    """作者在屏幕上看得见的一条。

    **只有两种说话人。** 工具返回和「只叫工具没说话」的那几条不在这里——见模块
    docstring 第三条。少掉的那部分不是被藏起来了：它有一个数（`TurnReceipt.lookups`），
    而**零必须带着理由**这条规矩在这儿的形态是「查了几次说得出来，查到了什么不上屏」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    """在这段对话历史里的位置。前端拿它当 key —— **不要用它当业务标识**。"""

    speaker: Literal["author", "assistant"]
    text: str


class ChatSessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str = ""
    created_at: str
    updated_at: str
    message_count: int = Field(ge=0)
    """这段对话历史里有多少条（含不上屏的那些）。"""

    running: bool = False
    """这一刻正在跑一轮。**进程内事实**，重启后恒为 `false`。"""

    pending_lookups: int = Field(default=0, ge=0)
    """上一轮**断在半路**、还缺结果的那几步（`Conversation.pending_calls`）。

    不为零 = 进程死在了模型调用和派发之间。下一轮会先把它们补跑掉（ADR 0019 的
    resume：看尾巴、补跑缺的、继续），所以这儿不需要一个「恢复」按钮。
    """


class ChatDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ChatSessionView
    messages: tuple[ChatMessageView, ...] = ()


class ContextReceipt(BaseModel):
    """这一轮发给模型的那份上下文**裁掉了什么**（`agent.loop.Projection` 的回执）。

    静默截断读起来像「全给了」。这份回执存在的理由和 `/check` 的 `rules_run`、
    起草回执的 `memory` 是同一条：**零必须带着理由一起出现**（ARCHITECTURE §10 约束 8）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    off_chapter: int = 0
    """因为绑在**更后面**的章上而没进这一份的查询结果条数（ADR 0019 边界五）。"""

    stale_lookups: int = 0
    """助手手上那份正文**已经被作者改掉**、这一轮换成「重新读一次」的条数（边界三）。

    不为零 = 它上一轮读到的那一章和磁盘上那份对不上了。**不说的话作者永远不知道**
    助手手里曾经拿着一份旧稿——而那正是「哪一份正文是真的」有第二个答案的形态。
    """

    trimmed_results: int = 0
    dropped_lookups: int = 0
    dropped_reasoning: int = 0
    lost_lookups: int = 0
    full: bool = False
    """剪到只剩作者说过的话仍然装不下。**这一档不砍作者的话**，这一轮直接停。"""


class DraftCandidateView(BaseModel):
    """摆在界面上的一稿（ADR 0022）。**正文不在这儿**——要正文单取一次。

    ── 「推荐哪一版」由谁说 ──────────────────────────────────────────────

    **不是引擎**（ADR 0005：引擎不给散文打分），也不是这一层。`note` 是**写那一稿的
    那个模型自己**在同一次调用里交的三十个字；`landed` 是助手真的做过的一个动作
    ——「它把哪一版写进了书」就是它的推荐，而那是一个动作不是一句评价。

    一批都没落盘时**后端不替作者挑**（同 `AmbiguousName`：两个方向都很贵就摆出来，
    绝不挑）。前端照 `ordinal` 顺序摆，别自己算一个「最好的那版」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """取全文用它。**不上屏**——它是 `draft:01J…` 这种形状，作者认得的是「第 2 稿」。"""

    chapter: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    """这一章的第几稿。**屏幕上说的是这个数。**"""

    units: int = Field(ge=0)
    note: str = ""
    """写它的那个模型自己那句话。**空 = 它这次没说**，界面上就别硬编一句出来。"""

    preview: str = ""
    """开头那一段，定长（`agent.candidates.PREVIEW_UNITS`）。"""

    created_at: str = ""
    landed: bool = False
    """它进过书没有。**不是「被选中」**：作者可以在版本历史里把它退回去。"""

    stopped_reason: str = ""
    """**空 = 这一稿写完了**；非空 = 它被砍断了，这句话说明为什么（作者按了「停」）。

    **它必须上屏。** 一稿断在半句上而屏幕不说，作者会以为写作模型就写成了这样——
    而这块屏幕上另外两样（`preview` / 全文）都长得和一份写完的稿子一模一样。
    措辞的唯一出处是库里那一列（`agent/drafting.py::AUTHOR_STOPPED_NOTE`），
    **前端不许按这一位自己再造一句**。
    """


class ChapterDrafts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    drafts: tuple[DraftCandidateView, ...] = ()


class DraftCandidateDetail(DraftCandidateView):
    """一稿的全文（`GET …/drafts/{id}`）。**界面上摊开那一版读的就是它。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    """一稿正文，**不含章标题**（那一行是切章的锚，属于作者）。"""


class TurnReceipt(BaseModel):
    """跑完一轮的回执。**`TurnResult.maintainer_note` 不在这儿，一个字都不出去。**

    那是写给维护者的 `ProviderError` 原文（里面有端点地址和模型名），同
    `ExtractionRunError.message`——作者读不懂它，而且他能做的动作是去顶栏改设置，
    不是读一条英文诊断。作者看到的只有 `message`（`stop_wording()` 那一句）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ChatSessionView
    chapter: int = Field(ge=1)
    reason: StopReason
    message: str
    """说给作者的那一句。**措辞唯一出处是 `agent.loop.stop_wording()`**，前端不许再翻一遍。"""

    reply: str = ""
    messages: tuple[ChatMessageView, ...] = ()
    """这一轮新长出来的、上得了屏的那几条。"""

    steps: int = Field(default=0, ge=0)
    lookups: int = Field(default=0, ge=0)
    """这一轮查了几次（工具调用次数）。"""

    tokens_reported: int = Field(default=0, ge=0)
    calls_without_usage: int = Field(default=0, ge=0)
    """有几次调用没量准。**不为零时上面那个数是低估**，界面上不许把它当全部。"""

    context: ContextReceipt = ContextReceipt()

    asked: AuthorQuestion | None = None
    """它停下来问作者的那一句 + 几个可点的选项（ADR 0024）。**非空 ⇔ `reason` 是「问了作者」。**

    **它必须在回执上，不能只在事件流里**（`TurnResult.asked` 的 docstring 是同一条）：
    作者可能在这一轮结束**之后**才打开那段对话（换台机器、刷新页面、三个月后回来），
    那时事件早就没了，而这一轮唯一的收场是一个没人答的问题。

    **原样出去，一个字都不加**：问句和选项 100% 是模型自己的字（`agent/tools.py` 那条
    handler 里连一个数据来源都没有）。这一层要是替它补一句「你可以这样答」，
    ADR 0024 那两条断言就只剩一条了。
    """

    drafts: tuple[DraftCandidateView, ...] = ()
    """这一轮写出来的那几稿，**按生成顺序**（ADR 0022）。

    它从起草台那儿现拿（`ChapterDesk.produced`），**不是从工具返回的 JSON 里翻出来的**
    ——翻它就是第二处解析点，而这个仓库刚把「同一件事两处解析」清掉。

    `landed=true` 的那一稿已经在书里了：**这一轮的正文变了**，界面该重取一次那一章。
    （3.5 那条余债「回执上没有字段说哪一章被写了」由这个字段还上了：`chapter` 在每一稿上。）
    """


class TurnRefused(BaseModel):
    """长连接上那一帧「跑到一半被拒了」（`SSE_FAILED`）。

    **它只在一种情形下存在**：头都发出去了才撞上乐观并发闸（这段对话在别的窗口里
    刚往前走了一步）。不流式那条路上它是一个 409，而这儿改不成状态码了——
    但**那句中文还说得出来**，这一帧存在的全部理由就是别把它弄丢。

    `message` 可以是空的：认不出的拒绝**不编一句**（§10 约束 8），
    界面那时说的是它自己那句「这一轮没跑成，而系统没能说清是为什么」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = ""


class ChatStopped(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    stopped: bool
    """`false` = 这一刻它本来就没在跑（不是失败）。"""

    message: str


class ChatDeleted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    deleted: bool


class AuthorRuleView(BaseModel):
    """摆给作者看的一条规矩（ADR 0023 决策二的「摆出来」那一半）。

    **`heard` 不在这儿，这是有意的。** 它是「在几个来回里被记下过」，读起来像
    「你说过 N 遍」——而提炼是模型干的，它记下的东西作者不一定真说过（ADR 0023 自己
    把这条列在「代价」里）。屏幕上说不准的数不如不说；作者要决定的只有「留还是不留」，
    而那两个字段（`text` / `scope`）就够。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    """取消它时报这个数（`AuthorRule.seq`，历史下标）。**不上屏。**

    它和消息上那个 `seq` 是同一个坐标（`_visible` 也按历史下标编），所以「作者点了
    哪一条」在整条链上只有一种读法。
    """

    text: str
    """那句话，**作者自己的措辞**（模型提炼时被要求原样重述，`agent/tools.py`）。"""

    scope: str
    """它管到哪儿，**一句中文**。

    **枚举不出这一层**（`chapter_wide` 是个 bool，翻成中文要么在前端摆一张
    「码 → 中文」的表——那张表被删过一次，理由在 `frontend/src/api/correctionError.ts`
    顶上——要么在这儿翻一次）。同 `stopped_reason` / `stop_wording()`：措辞的唯一出处
    在后端，前端照抄。
    """


class ChatRules(BaseModel):
    """第 `chapter` 章此刻生效的那几条（`GET …/rules?chapter=N`）。

    **`expired` 不是装饰，它是零态的那句理由**（§10 约束 8）：一块空面板要说得出
    自己是哪一种空——「还没有规矩」和「定过、这会儿都不作数了」下一步动作不同，
    后者的下一句是「规矩只管你说它时那一章」，而这个仓库为「静默的零」栽过五次。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    """这份答案是按第几章算的。**原样回给界面**：作者可能在请求飞着的时候翻了页，
    那时屏幕上说的必须是这份数据真正的坐标，不是他此刻站的那一章。"""

    rules: tuple[AuthorRuleView, ...] = ()
    expired: int = Field(default=0, ge=0)
    """**已经不作数**的有几条（按身份数，见 `rules.expired_rule_count`）。

    它数的是**整段对话**里的，不只这一章——「他在第 2 章定过、现在在第 7 章」正是
    这个数存在的理由，而那时那几条不属于当前这一章。作者取消掉的不算（他知道它没了）。
    """


class RuleRevoked(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    seq: int = Field(ge=0)
    revoked: bool
    """恒为 `true`（撤不掉的那几种在上面就 4xx 了）。

    **界面不许拿它当「这条已经从清单上消失」的证据**：真正的证据是重取一次那份清单，
    而那正是「按下标撤」那个 bug 唯一会现形的地方。
    """


# ══════════════════════════════════════════════════════════════════════════
# 入参
# ══════════════════════════════════════════════════════════════════════════


class NewChat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    house_style: str = ""
    """作者的文风偏好。**进稳定前缀，所以它必须跨章不变**（ADR 0019 边界六）。

    它是自由文本，这一层看不出作者有没有把一条禁令写进去——判它要回答「这句话是不是
    把伏笔说破了」，那是语义判断（ADR 0005 在 v1 里禁止本仓库长出这种能力）。
    """


class TurnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    """作者此刻在写第几章。**必填，没有默认值**——见模块 docstring 第一条。"""

    said: str = ""
    """作者这一轮说的话。**留空 = 接着上次往下跑**（resume，或者上一轮撞了闸之后继续）。"""

    run_id: str = Field(default="", max_length=64)
    """**这一轮的标识，由发起的那个界面自己造。** 「停」拿它认出要停的是哪一轮。

    ── 为什么是客户端造的，不是后端发的 ────────────────────────────────────

    后端发不了：`POST …/turn` 是**跑完才回来**的（这一版 HTTP 不流式），而「停」必须
    在那之前就能按。所以标识只能由按下发送的那一边生成，跟着两个请求一起走。

    留空 = 不比对（`curl` / 老界面）。**它不进任何一行数据**，只活在进程内的 `LIVE` 里。
    """


class StopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default="", max_length=64)
    """**想停的是哪一轮**（跑那一轮时报的那个 `TurnBody.run_id`）。

    对不上就忽略——见 `_Running` 那段「作者按停 → 上一轮自己跑完 → 新一轮开始」的序列。
    留空 = 不比对（今天的行为）。
    """


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


def build_agent_model(config: ProviderConfig, plan: ResolvedCallPlan) -> ModelPort:
    """造这一轮的模型端口。**测试替换的就是这一个函数**（同 `deps` 里那几个注入点）。"""
    return ProviderModelPort(config, plan)


def _ledger(conn: Connection, project_id: str) -> LedgerFn:
    """把一份账单原料落成一行 `model_call`。**逐字段平移，不翻译。**

    `record_call` 自己开 `BEGIN IMMEDIATE` 但**不 commit**（调用方负责自己的业务表），
    所以这里补上提交。**失败不吞**：账记不上的那一次调用已经花过钱了，
    而一条静默失败的记账就是日志页第二次骗人。
    """

    def record(receipt: ModelCallReceipt) -> None:
        try:
            record_call(
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
                call_id_factory=lambda pid: new_id(EntityType.CALL, pid),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()

    return record


def _tool_context(
    proj: Any,
    store: GraphStore,
    conn: Connection,
    *,
    chapter: int,
    desk: ChapterDesk,
    capability: ProviderCapabilities,
    plan: ResolvedCallPlan,
    db_lock: AbstractContextManager[Any] | None = None,
) -> ToolContext:
    """这一轮里模型碰得到的**全部**东西（`agent/ports.py` 是那一页的规格）。

    `drafter` 2026-08-11 接上了（3.6 / ADR 0021），2026-08-12 拆成三个动作
    （ADR 0022：生成 / 落盘 / 读回）。**它是一个注入进来的对象，不是一个新字段**：
    写盘要 `GraphStore`（带写入面）、一条连接和候选表，而 `ToolContext` 上只有
    `StoryGraph`——「模型改不了作者的 canon」是类型保证不是纪律（边界一）。
    写入面握在 `agent/drafting.py` 那个起草台里，这个 dataclass 上一个字都没多。

    **窗口那两个数从 `capability` / `plan` 现取**，不再由调用方分别传进来：它们和
    起草那条路要用的 `config` / `capability` 是同一批东西，分两处传迟早有一处漏改。
    """
    return ToolContext(
        store=store,
        project_id=proj.id,
        root_path=proj.root_path,
        drafter=desk,
        # **和起草台是同一把锁**：并发窗口里碰这条连接的每一句都要排在同一道队里
        # （`agent/ports.py::ToolContext.db_lock` 记着为什么）。两把 = 各排各的 = 没排。
        db_lock=db_lock,
        summaries=SummaryStore(conn),
        events=SqliteEventStore(conn),
        working_chapter=chapter,
        max_context_tokens=capability.max_context_tokens,
        # **对话那一档的输出预算**（`AGENT_REPLY_LENGTH` 倒推的），用来算「一次工具返回
        # 最多给多少字」。起草那一次调用的预算是另一个数，由 `chapter_drafter` 自己算
        # ——两档长度不同，共用一个 plan 会让工具返回的天花板跟着起草的输出预算走。
        reserved_output_tokens=plan.request_token_budget,
    )


def _visible(messages: tuple[AgentMessage, ...], first_seq: int) -> tuple[ChatMessageView, ...]:
    """canonical → 屏幕上那几条。工具返回和「只叫工具没说话」的那些不出去。"""
    out: list[ChatMessageView] = []
    for offset, message in enumerate(messages):
        if message.role is Role.USER:
            speaker: Literal["author", "assistant"] = "author"
        elif message.role is Role.ASSISTANT and message.content.strip():
            speaker = "assistant"
        else:
            continue
        out.append(
            ChatMessageView(seq=first_seq + offset, speaker=speaker, text=message.content)
        )
    return tuple(out)


def _session_view(
    session: ChatSessionRow, conversation: Conversation, history_count: int, *, running: bool
) -> ChatSessionView:
    return ChatSessionView(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=history_count,
        running=running,
        pending_lookups=len(conversation.pending_calls),
    )


def _draft_view(candidate: DraftCandidate) -> DraftCandidateView:
    """候选 → 界面上那一条。**逐字段平移，不翻译**（同 `_ledger` 那条理由：
    翻译的地方就是能悄悄漏字段的地方）。"""
    return DraftCandidateView(
        id=candidate.id,
        chapter=candidate.chapter,
        ordinal=candidate.ordinal,
        units=candidate.units,
        note=candidate.note,
        preview=candidate.preview,
        created_at=candidate.created_at,
        landed=candidate.landed,
        stopped_reason=candidate.stopped_reason,
    )


def _rule_view(rule: AuthorRule) -> AuthorRuleView:
    """一条规矩 → 屏幕上那一行。**这儿只翻一件事：`chapter_wide` 那个 bool。**

    两句话都带着「什么时候它自己就没了」，因为那正是作者不问就不会知道的一半：
    ADR 0023 的安全方向是**拿不准就放掉**（留着 = 第 200 章写不出打戏，而作者不知道
    为什么），所以「它会自己过期」这件事必须和规矩本身摆在一起，不能只活在 ADR 里。
    """
    if rule.chapter_wide:
        return AuthorRuleView(
            seq=rule.seq,
            text=rule.text,
            scope=f"第 {rule.chapter} 章都按它来 —— 翻到下一章就自动放掉",
        )
    return AuthorRuleView(
        seq=rule.seq,
        text=rule.text,
        scope="只管眼下这一轮 —— 你再说一句话，它就自动放掉",
    )


def _load(conn: Connection, project_id: str, chat_id: str) -> StoredChat:
    stored = ChatStore(conn).load(project_id, chat_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    return stored


def _plan_or_422() -> tuple[ProviderConfig, ProviderCapabilities, ResolvedCallPlan]:
    """模型配好了没 + 这一档它撑不撑得起。**两个问题两句话**，别合并。"""
    reason = model_configuration_error()
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    config = agent_provider_config()
    try:
        capability, plan = agent_call_plan(config)
    except (CapabilityError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"这个模型跑不了写作助手：{exc} —— 去顶栏 ⚙「AI 设置」换一个模型或服务地址。"
            ),
        )
    return config, capability, plan


# ══════════════════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════════════════


@router.post("/api/projects/{project_id}/chats", response_model=ChatSessionView, status_code=201)
def create_chat(
    body: NewChat,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatSessionView:
    """开一段新的对话。**作者可以同时开好几段**，每一段各自 resume。"""
    store = ChatStore(conn)
    session = store.create(proj.id, title=body.title, house_style=body.house_style or None)
    stored = store.load(proj.id, session.id)
    assert stored is not None  # 刚建好
    return _session_view(session, stored.conversation, 0, running=False)


@router.get("/api/projects/{project_id}/chats", response_model=list[ChatSessionView])
def list_chats(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> list[ChatSessionView]:
    """侧栏那一列，最近说过话的在前。

    **不逐条读消息**：列表页要的是「有哪几段」，把每段的历史都拉出来只为了数一个数，
    在一段几百条的对话上就是几百行的白读。所以两个数各走一次聚合查询。

    **`pending_lookups` 必须真的算**，不能让它吃默认值 0：那个字段说的是「上一轮断在
    半路」，而**这一页是作者唯一一次看得见全部会话的地方**——它恒为 0 的话，断在半路
    的那一段和跑完了的那一段在侧栏上长得一模一样，而两者的下一步动作不同。
    一个只在某一个读端说真话的字段，比没有这个字段更坏。
    """
    store = ChatStore(conn)
    counts = store.history_counts(proj.id)
    pending = store.pending_counts(proj.id)
    return [
        ChatSessionView(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=counts.get(session.id, 0),
            running=LIVE.running((proj.id, session.id)),
            pending_lookups=pending.get(session.id, 0),
        )
        for session in store.list(proj.id, limit=limit)
    ]


@router.get("/api/projects/{project_id}/chats/{chat_id}", response_model=ChatDetail)
def read_chat(
    chat_id: ChatId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatDetail:
    """看一段对话。404 = 这一段不在（或不属于这本书），和 501 分得开。"""
    stored = _load(conn, proj.id, chat_id)
    return ChatDetail(
        session=_session_view(
            stored.session,
            stored.conversation,
            stored.history_count,
            running=LIVE.running((proj.id, chat_id)),
        ),
        messages=_visible(stored.conversation.messages, 0),
    )


@router.delete("/api/projects/{project_id}/chats/{chat_id}", response_model=ChatDeleted)
def delete_chat(
    chat_id: ChatId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatDeleted:
    """删掉一段对话。**这不会动到任何一行正文**——正文在磁盘上，这两张表里没有它。

    正在跑的那一段**先拒掉**：删了它，那一轮跑完想把产物追加回去时会撞上一个不存在的
    会话，作者看到的是一句「在别的窗口里刚往前走了一步」——**一句和他刚做的事完全对不上
    的话**。先说「它正在跑」是准的。
    """
    if LIVE.running((proj.id, chat_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_busy",
                "chat_id": chat_id,
                "message": "这段对话正在跑，先按「停」再删。",
            },
        )
    if not ChatStore(conn).delete(proj.id, chat_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    return ChatDeleted(chat_id=chat_id, deleted=True)


class _TurnRun:
    """一轮的全部装配。**构造 = 占位并落下作者那句话；`go()` = 花时间的那半截。**

    ── 为什么要拆成两半 ────────────────────────────────────────────────────

    因为流式那条路上，**第一个字节发出去之后就没有状态码了**。422（模型没配好）/
    409（这段对话正在跑）/ 404（这段对话不在）必须在 `StreamingResponse` 被构造之前
    抛完，否则作者拿到的是一个 200 的空流——一块**看起来正常的空屏幕**，
    正是这个仓库反复在修的那种失败形态。

    构造函数里的顺序（先占位、再读、再写）和拆开之前一模一样，理由也没变：
    顺序反过来会有一个实测过的真故障——第二个窗口在第一轮还在跑的时候先把作者那句
    新话追加进去，于是第一轮跑完想追加自己的产物时撞上乐观并发闸，
    **先按发送的那一个被判失败，后按的那一个反而通过**。
    """

    def __init__(
        self,
        *,
        chat_id: str,
        body: TurnBody,
        proj: Any,
        conn: Connection,
        store: GraphStore,
    ) -> None:
        self._config, self._capability, self._plan = _plan_or_422()
        self._chat_store = ChatStore(conn)
        self._key = (proj.id, chat_id)
        self._proj = proj
        self._conn = conn
        self._store = store
        self._chat_id = chat_id
        self._chapter = body.chapter
        self._desk: ChapterDesk | None = None
        try:
            self._signal = LIVE.begin(self._key, body.run_id)
        except ChatBusy:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "chat_busy",
                    "chat_id": chat_id,
                    "message": "这段对话正在跑上一轮，等它停下来，或者按「停」。",
                },
            )
        try:
            stored = _load(conn, proj.id, chat_id)
            self._conversation = stored.conversation
            self._history_count = stored.history_count
            said = body.said.strip()
            if said:
                self._conversation = self._conversation.with_author(said)
                # **作者那句话先落库**：这一轮后面任何一步炸掉（记账写不进去、库锁住），
                # 他说过的话都还在，下一轮接着往下走就是。
                self._history_count = _append(
                    self._chat_store,
                    proj.id,
                    chat_id,
                    self._history_count,
                    self._conversation.messages[self._history_count :],
                )
                if not stored.session.title.strip():
                    self._chat_store.rename(proj.id, chat_id, said[:TITLE_FROM_FIRST_SAID])
            elif not self._conversation.messages:
                raise HTTPException(
                    status_code=422,
                    detail="这段对话还没开始——先说一句你想让它做什么。",
                )

            # 这一轮开始时历史有多长。**出参那几条按它切**，不能按 `_history_count` 切——
            # 后者会被那个落库回调一路推着走，切出来的就只剩最后一两条。
            self._turn_start = self._history_count
        except BaseException:
            # 构造到一半炸了（404 / 422 / 库锁住）——**占的位要还回去**，
            # 否则这段对话在进程活着的余生里都是「正在跑上一轮」，而没有一轮在跑。
            LIVE.end(self._key)
            raise

    def _save(self, live: Conversation) -> None:
        """一轮跑到一半时把已经长出来的消息落库（`agent.loop.PersistFn`）。

        **走的是同一个 `append`**，只是不再等到最后才走一次：`chat_message` 上没有
        第二种写法，也不该有——「跑到哪一步」那样一列会造出第二份执行态。
        """
        self._history_count = _append(
            self._chat_store,
            self._proj.id,
            self._chat_id,
            self._history_count,
            live.messages[self._history_count :],
        )

    def go(self, on_event: EventFn | None = None) -> TurnReceipt:
        """真的跑。**`on_event=None` 这条路和 2026-08-12 之前逐字节相同**
        （`agent/loop.py::EventFn` 保证了这句话，两条路由的差别只有这一个参数）。

        起草台和 `ToolContext` **在这儿造，不在构造函数里**：起草台是三个收事件的地方
        之一，而它按「接没接 `on_event`」决定要不要在流式那次调用上挂一个逐片回调
        （`ChapterDesk._listening`）。在还不知道有没有人听的时候先造它，
        那个回调就永远挂着——于是「不传 = 逐字节不变」在这条路上当场变成一句假话。
        """
        keep: PersistFn = self._save
        # 这一轮碰库排的那道队。**一轮一把**（一次请求一条连接，`api/deps.py`），
        # 交给起草台和 `ToolContext` 的是同一把。
        db_lock = RLock()
        # **起草台在这儿造，不在 `_tool_context` 里造**：这一轮生成了哪几稿只有它
        # 知道（`produced`），而出参要把那几稿摆到界面上。让壳去翻工具返回的 JSON
        # 就是第二处解析点，而这个仓库刚把「同一件事两处解析」清掉。
        desk = chapter_drafter(
            store=self._store,
            conn=self._conn,
            project_id=self._proj.id,
            root=self._proj.root_path,
            config=self._config,
            capability=self._capability,
            events=SqliteEventStore(self._conn),
            summaries=SummaryStore(self._conn),
            db_lock=db_lock,
            # **和 `run_turn` 拿的是同一个信号对象。** 两个信号 = 按停只停住其中一半，
            # 而作者看到的是「按了停，那一稿还在写」——起草那一次调用是这一轮里最长的
            # 一段（几十秒），停不住它等于没停。
            cancel=self._signal,
            # **同一个 `on_event` 交给两处**（loop 和起草台）。给了不同的对象就等于
            # 只接了一半：作者会看到「它说在写，然后什么都没有，然后突然写完了」。
            on_event=on_event,
        )
        self._desk = desk
        try:
            result = run_turn(
                self._conversation,
                context=_tool_context(
                    self._proj,
                    self._store,
                    self._conn,
                    chapter=self._chapter,
                    desk=desk,
                    capability=self._capability,
                    plan=self._plan,
                    db_lock=db_lock,
                ),
                # **回话那一档没有接事件流，这是有意的，不是漏的**（ADR 0024 的红字）：
                # `AGENT_REPLY_LENGTH` 倒推的输出预算 4,024 远在流式阈值之下 ⇒
                # `plan.stream is False` ⇒ 那条线今天一个 `reply_delta` 都发不出来
                # （`tests/test_chat_boundary.py::test_todays_agent_call_is_not_streaming_…`
                # 钉着它）。接上去的代价是这个注入点的签名要改，而**十几处测试桩会为
                # 一条永远不响的流各带一个参数** —— 那读起来像产品有这个能力。
                # 那条断言变红的那天（谁把回话预算抬过阈值）就是接它的那天，
                # 而那一天要一起决定的是界面上那一档怎么画（今天的答案是「不画」）。
                model=build_agent_model(self._config, self._plan),
                ledger=_ledger(self._conn, self._proj.id),
                # **并发只在这一层放开**（见 `AGENT_PARALLEL_TOOLS`）：只有开连接的人
                # 知道这条连接跨不跨得了线程。
                limits=TurnLimits(parallel_tools=AGENT_PARALLEL_TOOLS),
                cancel=self._signal,
                persist=keep,
                on_event=on_event,
            )
            self._save(result.conversation)
        finally:
            LIVE.end(self._key)

        session = self._chat_store.get(self._proj.id, self._chat_id)
        assert session is not None  # 构造时刚读过它

        return TurnReceipt(
            session=_session_view(
                session, result.conversation, self._history_count, running=False
            ),
            chapter=self._chapter,
            reason=result.reason,
            message=result.said_to_author,
            reply=result.reply,
            messages=_visible(result.conversation.messages[self._turn_start :], self._turn_start),
            steps=result.steps,
            lookups=result.tool_calls,
            tokens_reported=result.tokens_reported,
            calls_without_usage=result.calls_without_usage,
            context=_context_receipt(result.projection),
            asked=result.asked,
            # **按「第几章的第几稿」排，不按它们跑完的先后。** 一批稿是同时跑的
            # （ADR 0022），谁先回来取决于服务商那一刻的排队——照那个顺序摆，作者每次
            # 刷新看到的次序都可能不一样，而他记的是「第 2 稿」。
            drafts=tuple(
                _draft_view(candidate)
                for candidate in sorted(desk.produced, key=lambda c: (c.chapter, c.ordinal))
            ),
        )


@router.post("/api/projects/{project_id}/chats/{chat_id}/turn", response_model=TurnReceipt)
def run_chat(
    chat_id: ChatId,
    body: TurnBody,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    # **收的是读写交集，不是只读的 `StoryGraph`**：起草落盘走 `importer.save_chapter`，
    # 而它要 `put_chapter`（ADR 0021）。写入面到此为止——它进的是起草那个闭包，
    # 不进 `ToolContext`（边界一，见 `_tool_context`）。
    store: GraphStore = Depends(get_store),
) -> TurnReceipt:
    """跑一轮，**跑完才回**（十一种停法）。

    工作台走的不是这一条，走的是下面那条长连接。这一条留着有两个用处，都不是「兼容」：

    1. 它**就是**「换回请求/响应」那条退路的全部内容（ADR 0024 的修复成本那节）——
       事件流扔掉之后剩下的正是这个函数，而它一直在被测试跑着，不是一段纸上的退路；
    2. 一个不会流式的调用方（`curl`、脚本）照旧问得出「这一轮怎么样了」。

    `said` 留空 = 接着上次往下跑。那就是 ADR 0019 的 resume：**看尾巴、补跑缺的、继续**
    （缺哪几个由 `Conversation.pending_calls` 回答，这里不写第二份）。

    **产物是边跑边落库的，不是跑完再整批落。** 「跑完再落」看起来更干净，但它让上面那句
    resume 变成一句空话：进程死在中途 ⇒ 这一轮一条都没进库 ⇒ 尾巴上没有那条带
    `tool_calls` 的 assistant ⇒ `pending_calls` 恒为空 ⇒ 没有任何东西可补，而那几次模型
    调用的钱**已经记在账上了**。所以 `run_turn` 收一个 `persist`（`agent/loop.py`），
    每长出一条就落一次。
    """
    return _TurnRun(chat_id=chat_id, body=body, proj=proj, conn=conn, store=store).go()


SSE_TURN = "turn"
SSE_RECEIPT = "receipt"
SSE_FAILED = "failed"
"""长连接上只有这三种帧。**封闭集合**——加第四种要同时改前端那个解码器。

| 帧 | 载荷 | 什么时候 |
|---|---|---|
| `turn` | 一条 `TurnEvent` | 每一步的边界 |
| `receipt` | 一份 `TurnReceipt` | 跑完了，**和不流式那条路由的出参是同一个东西** |
| `failed` | `{"message": …}` | 头都发出去了才被拒（唯一一档：库在别的窗口里被推过一步）|

**没有第四种「出错了」**：一条流断在半路而没有 `receipt`，前端说的是它自己那句
「这一轮没跑成，而系统没能说清是为什么」——那句话对**网断了**和**服务端炸了**同样准，
而给后者编一个具体理由就是 §10 约束 8 禁的那种话。
"""

_SSE_HEADERS = {
    # 中间任何一层都不许攒着（dev 那条 Vite 代理、将来的桌面壳）。攒起来的形态不是
    # 报错，是「事件全都在最后一秒一起到」——也就是回到今天这个黑箱，而且没人看得出来。
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


def _frame(event: str, payload: str) -> bytes:
    """一帧 SSE。**`payload` 必须是单行**——JSON 里真正的换行一律被转义成 `\\n`，
    所以这条前提由 `model_dump_json()` 自己守着，不需要在这儿再洗一遍。"""
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _refusal_message(exc: HTTPException) -> str:
    """一次「跑到一半被拒」里**说给作者听**的那句话。认不出就交白卷。

    `detail` 有两种形状（这一层自己写的 dict、和 FastAPI 的裸字符串），
    而**编一句出来比不说更贵**：这一档今天只有一种真实来源（`chat_conflict`），
    它的 dict 里有一句现成的中文。
    """
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message", "")
        return message if isinstance(message, str) else ""
    return detail if isinstance(detail, str) else ""


def _turn_frames(run: _TurnRun, lease: Iterator[Connection] | None = None) -> Iterator[bytes]:
    """把一轮的事件流变成一串帧。**这个函数是整条适配器**（ADR 0024 决策二）。

    `lease` 是**只属于这一轮**的那条连接的租约（`stream_chat` 里那段 docstring 写着
    为什么它不能是请求那条）。**由跑那一轮的线程还**，和位子同一时刻、同一个 `finally`。

    ── 为什么要另起一条线程 ────────────────────────────────────────────────

    `run_turn` 是阻塞的：它跑完之前一个字节都还不回来。生成器只有拿到控制权才能
    `yield`，所以「边跑边喊」在同一条线上做不出来——**那正是今天这个黑箱的形状**。
    所以一轮跑在自己的线程上，事件进队列，这个生成器从队列另一头往外发。
    连接本来就是 `check_same_thread=False` 开的，而这一轮的 SQL 今天也已经跨线程了
    （`AGENT_PARALLEL_TOOLS`）——换的是哪条线程，不是有几条。

    ── 三件 `agent/loop.py::EventFn` 交代给适配器的活 ───────────────────────

    1. **缓冲**：`on_event` 是同步的，慢的消费者会拖慢这一轮。队列把两边解开。
    2. **丢弃**：浏览器关掉之后这一轮**还在跑、还在花钱**（那是有意的），
       但它的事件没人要了——`gone` 一亮就地扔掉，不再往队列里堆。
    3. **不抛**：往队列里放东西不会失败，所以这一层天然满足「界面掉线不许拿走这一轮」。

    **占的位由跑那条线程还**（`_TurnRun.go` 的 `finally`），不由这个生成器还：
    作者关掉页面时生成器会被提前扔掉，而那一轮还在跑——位子归还的时机必须跟着那一轮，
    不跟着屏幕。

    ── 为什么这个函数**不是**生成器（2026-08-12 修）────────────────────────

    写成 `def … yield` 的话，函数体要等到**第一次 `next()`** 才开始跑，也就是说
    **那条线程要等有人来读这条流才起**。而这中间隔着整个 ASGI 层：作者按下发送、
    在第一个字节出去之前把标签页关掉，`StreamingResponse` 一次都不迭代它——
    于是 `_TurnRun.__init__` 已经占掉的位子（`LIVE.begin`）**在这个进程的余生里
    都还不回来**。实测那段对话从此：再说一句 → 409「正在跑上一轮」；按「停」→
    200「按你的意思停下了」（**一句假话**，什么都没在跑）；想删掉 → 409「先按停再删」。
    **界面请他做的那个动作恰好是唯一不管用的那个。**

    所以线程在**这个函数被调用时**就起（路由的同步函数体里），发帧那半截住在
    `drain()` 里。`_Running` 自己的 docstring 拒绝过同一个病的库表版本
    （「一个存在库里的 `status='RUNNING'` 会在进程崩掉之后永远卡在那儿」）——
    这条只是把它在进程内的那一份也堵上。
    """
    events: queue.Queue[tuple[str, str] | BaseException | None] = queue.Queue()
    gone = Event()

    def emit(event: TurnEvent) -> None:
        if not gone.is_set():
            events.put((SSE_TURN, event.model_dump_json()))

    def work() -> None:
        try:
            receipt = run.go(emit)
        except HTTPException as exc:
            # 头已经发出去了，改不成 409 了。**但那句中文还说得出来**（唯一一档：
            # 这段对话在别的窗口里刚往前走了一步）。
            events.put((SSE_FAILED, TurnRefused(message=_refusal_message(exc)).model_dump_json()))
        except BaseException as exc:  # noqa: BLE001 —— 见下：它会在生成器那边被重新抛出
            # **不吞。** 吞掉的话作者看到一条空流、维护者一行 traceback 都没有，
            # 而这个仓库对「静默降级」的立场从来只有一个。带回生成器那条线再抛，
            # uvicorn 才印得出它是在哪儿炸的。
            events.put(exc)
        else:
            events.put((SSE_RECEIPT, receipt.model_dump_json()))
        finally:
            # **这一轮自己的连接由这一行还**（见 `stream_chat`）：它必须排在
            # `run.go` 的每一句 SQL 之后，而这是唯一一个满足这句话的地方。
            if lease is not None:
                lease.close()
            events.put(None)

    # **这一行必须在生成器外面**（见上）：它跑起来，那一轮就一定会走到 `LIVE.end`，
    # 不管有没有人来读这条流。
    Thread(target=work, name="nh-chat-turn", daemon=True).start()

    def drain() -> Iterator[bytes]:
        try:
            while (item := events.get()) is not None:
                if isinstance(item, BaseException):
                    raise item
                yield _frame(*item)
        finally:
            gone.set()

    return drain()


@router.post("/api/projects/{project_id}/chats/{chat_id}/turn/events")
def stream_chat(
    chat_id: ChatId,
    body: TurnBody,
    proj: Any = Depends(load_project),
) -> StreamingResponse:
    """跑一轮，**边跑边说**（ADR 0024 决策一 / 三）。

    出参是一条 `text/event-stream`：中间是 `TurnEvent`，最后一帧是和上面那条路由
    **一模一样**的 `TurnReceipt`。所以订阅这条的界面不需要为「跑完之后」写第二套逻辑。

    **这条路由不换掉「停」**：按停仍然是 `POST …/stop`（另一个请求）。理由在模块
    docstring 的那张表下面——把它搬到这条流上，流断掉的那一刻作者就没有插手的地方了。

    404 / 409 / 422 仍然是真的状态码：它们全在 `_TurnRun` 的构造里抛完，
    **在第一个字节之前**。

    ── 为什么这一条**不收** `Depends(get_conn)`（2026-08-12）────────────────

    `api/deps.py::get_conn` 的纪律是「一请求一连接，**请求结束即关**」，而在这之前
    每条路由都在请求内跑完，所以那条纪律一直是安全的。**这条路由是第一个例外**：
    它故意让一段代码活得比请求长（「一个掉线的浏览器不该把作者已经付过钱的那一轮
    弄崩」）。两件事凑在一起就是——浏览器一走，FastAPI 拆依赖 `conn.close()`，
    而那一轮还在同一条 sqlite3 连接上落库、记账、收尾读一次会话。
    **2026-08-12 实测那不是一个异常，是 `Fatal Python error: Segmentation fault`**
    （两条栈停在 `agent/store.py::get` 和 `api/deps.py` 那句 `close()`）——
    也就是**关掉一个标签页可以把作者的整个工作台打死**。

    所以这一轮借的是**它自己的**一条连接：同一份策略（`get_conn` 本身，
    `check_same_thread=False`，这儿不写第二种开法），但**归还的时机跟着那一轮**
    （`_turn_frames` 里那条线程的 `finally`），不跟着屏幕。这跟「位子由那条线程还」
    是同一条纪律的第二半——活得比请求长的东西，它的每一样资源都得自己还。

    `proj` 仍然走请求那条连接：它是构造前的一次读，读完就是一个脱离连接的 Pydantic
    对象（`_TurnRun` 只用它的 `id` / `root_path`）。
    """
    # 只属于这一轮的一条连接。`next(...)` 之后它的归还责任就归 `_turn_frames`
    # 那条线程了——这一行到那个 `finally` 之间任何一个抛点都要自己收拾。
    lease = get_conn()
    conn = next(lease)
    try:
        run = _TurnRun(chat_id=chat_id, body=body, proj=proj, conn=conn, store=get_store(conn))
    except BaseException:
        # 404 / 409 / 422 全在这儿抛完（在第一个字节之前）。**那时那条线程还没起**，
        # 所以连接只能由这一行还。
        lease.close()
        raise
    return StreamingResponse(
        _turn_frames(run, lease), media_type="text/event-stream", headers=_SSE_HEADERS
    )


_STOP_WORDING: dict[StopVerdict, str] = {
    "stopped": stop_wording(StopReason.AUTHOR_STOPPED),
    "idle": "这段对话这会儿没在跑，不用停。",
    # **这一句不许说成失败**（同上一句）：作者按的那一下是对的，只是它想停的那一轮
    # 在这几百毫秒里自己跑完了。说「按钮没反应」会让他再按一次，而再按一次就会
    # 停掉他刚发出去的那一句——正是这道比对要防的事。
    "stale": "你按的是上一轮的「停」，那一轮已经自己跑完了。这会儿跑的是新的一轮，没有动它。",
}
"""三档结局各自那一句。**措辞只有这一份**，前端照抄 `message`。"""


@router.post("/api/projects/{project_id}/chats/{chat_id}/stop", response_model=ChatStopped)
def stop_chat(
    chat_id: ChatId,
    body: StopBody | None = None,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatStopped:
    """按下「停」。**它不等这一轮跑完**——信号交给正在跑的那一轮，跑一轮的那个请求
    会以「按你的意思停下了」收尾，已经查到的东西留着。

    `stopped=false` 不是失败，而且它有**两种**：那一刻本来就没在跑（跑完了、或者从来
    没开始），或者在跑的是**另一轮**（`run_id` 对不上，见 `_Running`）。
    两种都是 200，两种的措辞不一样。

    `body` 可以整个不给（`curl` / 老界面）：那时不比对，行为和 2026-08-12 之前一样。
    """
    if ChatStore(conn).get(proj.id, chat_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    verdict = LIVE.stop((proj.id, chat_id), (body.run_id if body else ""))
    return ChatStopped(
        chat_id=chat_id,
        stopped=verdict == "stopped",
        message=_STOP_WORDING[verdict],
    )


@router.get("/api/projects/{project_id}/chats/{chat_id}/rules", response_model=ChatRules)
def list_rules(
    chat_id: ChatId,
    chapter: Annotated[int, Query(ge=1)],
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatRules:
    """这一章此刻**生效着**的规矩（ADR 0023 决策二的「看得见」那一半）。

    **`chapter` 必填，和跑一轮那条同一个理由，但故障形态不同**：`live_rules` 在
    `chapter is None` 时返回空元组（拿不准就放掉，那一侧是对的），而这条路由的出参
    会直接变成一块「这一章还没有规矩」的空面板——**一句它不知道真假的话**。
    那一档比漏几条规矩更坏：它长得完全正常。

    两个数一次给全（`rules` + `expired`），**不给第二条路由**：分成两次取的话，
    界面上会出现「零条规矩」和「有几条过期了」不同步的一瞬间，而那一瞬间说的是假话。
    """
    stored = _load(conn, proj.id, chat_id)
    live = live_rules(stored.conversation, chapter)
    return ChatRules(
        chapter=chapter,
        rules=tuple(_rule_view(rule) for rule in live),
        # **下标集合由上面那份现成的答案凑出来**（`AuthorRule.seq` 按定义就是它），
        # 不再调一次 `surviving_rule_indices` —— 两次调用之间那份会话不会变，
        # 但「同一个问题在这一层问了两遍」正是判据开始漂的形状。
        expired=expired_rule_count(
            stored.conversation.messages, frozenset(rule.seq for rule in live)
        ),
    )


@router.delete(
    "/api/projects/{project_id}/chats/{chat_id}/rules/{seq}", response_model=RuleRevoked
)
def revoke_rule(
    chat_id: ChatId,
    seq: Annotated[int, Path(ge=0)],
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> RuleRevoked:
    """作者点掉一条规矩（ADR 0023 决策二的「能取消」那一半）。

    ── 它是 `DELETE`，但库里**一行都没少** ────────────────────────────────────

    canonical 只增不改（读回来重建出的 `Conversation` 必须和存进去之前逐字节相同），
    所以取消是历史上多出来的一条记录，指着被取消的那一条。动词仍然用 `DELETE`：
    对作者而言这就是「把这条划掉」，而 HTTP 的动词说的是他要的效果，不是库里的实现。

    ── 撤销**按身份**，这一层一个循环都不写 ──────────────────────────────────

    同一条规矩被记过两遍时，读端只摆最后那一条（`live_rules` 去重），作者点的也就是
    那一条；只把那个下标划掉的话，前面那一遍还在，而它此刻已经是章级的——
    **按钮按了、规矩还在，且没有任何东西会报错**。判据在 `rules._revoked_indices`
    （按「同一章的同一串字」撤掉每一份），这儿只负责把那条记录追加进去。

    ── 正在跑的那一轮**先拒掉**，而且这不是洁癖 ──────────────────────────────

    这一轮自己也在往同一段历史里追加（`_TurnRun._save` 每长出一条落一次），追加走的是
    乐观并发闸：中间插一条进去，**下一次落库当场 409，那一轮整个死掉**。
    作者点的是一条规矩上的 ×，而代价是他已经付过钱的那一轮——两件事之间没有任何
    看得出来的联系。所以这儿先说「它正在跑」，那句话是准的。
    """
    if LIVE.running((proj.id, chat_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_busy",
                "chat_id": chat_id,
                "message": "这段对话正在跑，跑完（或者按「停」）再取消这条规矩。",
            },
        )
    stored = _load(conn, proj.id, chat_id)
    try:
        record = revocation(stored.conversation.messages, seq)
    except ValueError as exc:
        # **那两句话是引擎写给作者的中文**（`rules.revocation` 的 docstring 写着为什么
        # 它们不是诊断），这一层原样转发，不翻第二遍。这一档只可能是界面拿着一份旧清单
        # 在点（另一个标签页刚取消过同一条、或者会话被换掉了）。
        raise HTTPException(
            status_code=422,
            detail={"error": "rule_not_found", "chat_id": chat_id, "message": str(exc)},
        )
    _append(ChatStore(conn), proj.id, chat_id, stored.history_count, (record,))
    return RuleRevoked(chat_id=chat_id, seq=seq, revoked=True)


@router.get("/api/projects/{project_id}/drafts", response_model=ChapterDrafts)
def list_drafts(
    chapter: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChapterDrafts:
    """助手写过的那几稿，最近的在前（ADR 0022）。

    **它不是版本历史。** 版本历史（`GET …/chapters/{n}/history`）里是**已经在书里**的
    那些；这儿是**还摆在桌上**的那些——没落盘的候选在磁盘上、在快照里都不存在，
    没有这条路由的话，作者关掉那一轮的回执就再也看不到它们了。

    `chapter` 给了就只看那一章：作者在第 12 章上问「刚才那三稿呢」，问的是那一章的三稿。
    """
    store = DraftCandidateStore(conn)
    return ChapterDrafts(
        drafts=tuple(
            _draft_view(candidate)
            for candidate in store.recent(proj.id, chapter=chapter, limit=limit)
        )
    )


@router.get(
    "/api/projects/{project_id}/drafts/{draft_id}", response_model=DraftCandidateDetail
)
def read_draft(
    draft_id: Annotated[str, Path(min_length=1)],
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> DraftCandidateDetail:
    """摊开某一稿的全文。**界面上「推荐那一版摊开」读的就是它**（ADR 0022 的入口形态）。

    404 = 这本书里没有这一稿（编号对不上，或者它落过盘、又老到被清理掉了——
    清理只清落过盘的，那些字在版本历史里还在）。
    """
    stored = DraftCandidateStore(conn).get(proj.id, draft_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "draft_not_found", "draft_id": draft_id},
        )
    return DraftCandidateDetail(
        **_draft_view(stored).model_dump(),
        # **正文在这儿，也只在这儿。** 列表那条路由一个字都不给——一次列出二十稿
        # 就是二十章正文，而作者要的只是「哪一版是哪一版」。
        text=stored.body,
    )


def _append(
    store: ChatStore,
    project_id: str,
    chat_id: str,
    base_count: int,
    messages: tuple[AgentMessage, ...],
) -> int:
    try:
        return store.append(project_id, chat_id, base_count=base_count, messages=messages)
    except ChatConcurrency as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_conflict",
                "chat_id": chat_id,
                "message": f"这段对话在别的窗口里刚往前走了一步，刷新一下再说。（{exc}）",
            },
        )


def _context_receipt(projection: Projection | None) -> ContextReceipt:
    if projection is None:
        return ContextReceipt()
    return ContextReceipt(
        off_chapter=projection.off_chapter,
        stale_lookups=projection.stale_manuscript,
        trimmed_results=projection.stubbed_results,
        dropped_lookups=projection.dropped_calls,
        dropped_reasoning=projection.dropped_reasoning,
        lost_lookups=projection.filled_shells,
        full=projection.over_budget,
    )


__all__ = ["LIVE", "build_agent_model", "router"]
