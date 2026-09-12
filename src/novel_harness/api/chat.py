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
| `POST …/say` | 一轮跑着的时候再说一句：进 `Mailbox`，loop 在下一步之前并进对话（2026-09-12） | 工作台 |

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

3. **对话的原文不是这条路由的出参。** 出去的是一份**投影**：作者说的话 + 助手说的话
   （+ 那几行「这一轮没跑成」，见下）。
   工具返回一条都不出去——它们是 `model_dump_json()` 出来的内部模型，里面躺着
   `NodeRef` 的裸 id（`secret:…:01J…`）。那种东西一旦被前端原样渲染就是屏幕上的
   研发术语（`frontend/src/test/screenGuard.ts` 的第三张网认的就是 `前缀:标识`），
   而**收窄的最强形态是根本没发出去**（同 `activity.py` 那次把 `params_json` 从
   SELECT 里删掉）。

── 一轮没跑成，那句话留在**对话里**（2026-08-13，迁移 012）──────────────────

作者说了两句，助手一个字都没有——那一轮在发出去之前就死了（他那台机器到端点的
TLS 全断）。屏幕上确实弹过一句提醒，可它活在浏览器的组件状态里：组件一卸载、
他再发一句，那句话就没了。作者的原话：「**没有必要消失**。」

所以这一档落盘：`ChatStore.note()` 往对话里追加一行，`section='notice'`。
**要不要留、留哪句话**在 `_notice_for`（判据 + 不留痕的那几档都写在它的 docstring 里）；
措辞一个字不加，原样是 `stop_wording()` 那一句。

**它为什么不是一条 SYSTEM 消息**：`Conversation.messages` 里的 SYSTEM 只有两个来源
（一条规矩 / 一条撤销记录），而 `rules.is_rule()` 是**纯结构判据**——塞一条别的用途的
SYSTEM 进去，它会被当成一条规矩，在作者切到下一章时静默消失。第三档 `section` 让
「不进 prompt」和「不是规矩」变成实现里够不着的事，而不是两条要记得写的过滤。

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
| ~~这一章现在哪几条生效 / 取消一条~~ | **2026-08-14 撤了**（ADR 0028）——规矩不上屏，见文件末尾那段 |

最后那一行是**这条按钮唯一会假绿的地方**：同一条规矩记过两遍时读端只摆最后一条，
作者点的也只能是那一条，**而前面那一遍已经是章级的**。只划掉那个下标 =
按钮按了、规矩还在、且没有任何东西会报错。引擎那一侧按**身份**（同一章的同一串字）
撤掉每一份，所以这儿收一个 `seq` 就够——**在路由层重写一遍那个循环就是第二份判据**。
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from threading import Event, Lock, RLock, Thread
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..advisory_review import review_track_on_demand
from ..agent.rules import surviving_rule_indices
from ..agent.loop import (
    write_rule_of,
    AgentMessage,
    Cancellation,
    Conversation,
    Mailbox,
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
    TurnResult,
    run_turn,
    stop_wording,
)
from ..agent.candidates import DraftCandidate, DraftCandidateStore
from ..agent.drafting import ChapterDesk, chapter_drafter
from ..agent.model import ProviderModelPort, agent_call_plan
from ..agent.ports import ToolContext, TrackVerdict
from ..agent.tools import AuthorQuestion
from ..agent.store import ChatConcurrency, ChatNotice, ChatSessionRow, ChatStore, StoredChat
from ..checks.service import RulesReader
from ..notices import NoticeReader
from ..db import Connection
from ..draft.length import DraftLanguage
from ..focus import frontier_chapter
from ..graph import StoryGraph
from ..draft.capabilities import CapabilityError, ProviderCapabilities, ResolvedCallPlan
from ..draft.provider import CompletionResult, ProviderConfig
from ..draft.rolling_summary import SummaryStore
from ..extract.call_audit import record_call
from ..graph.sqlite_events import SqliteEventStore
from ..graph.store import GraphStore
from ..ids import EntityType, new_id
from .deps import (
    agent_provider_config,
    get_advisory_reviewer,
    get_conn,
    get_store,
    load_project,
    model_configuration_error,
    resolve_route_capabilities,
)


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
        self._live: dict[tuple[str, str], tuple[str, Cancellation, Mailbox]] = {}

    def begin(self, key: tuple[str, str], run_id: str = "") -> Cancellation:
        with self._lock:
            if key in self._live:
                raise ChatBusy("这段对话正在跑上一轮")
            signal = Cancellation()
            self._live[key] = (run_id, signal, Mailbox())
            return signal

    def mailbox(self, key: tuple[str, str]) -> Mailbox | None:
        """这一轮的信箱（`begin` 时一起造的）。`None` = 没在跑。"""
        with self._lock:
            live = self._live.get(key)
        return None if live is None else live[2]

    def say(self, key: tuple[str, str], run_id: str, text: str) -> StopVerdict:
        """把作者中途那句话交给它想插进的那一轮。判据和 `stop` 一模一样（三档同名）：
        `stopped` 在这儿读作「排进去了」。**对不上一个字都不动**——也不落库，
        那句话由前端按新的一轮发出去。"""
        with self._lock:
            live = self._live.get(key)
        if live is None:
            return "idle"
        running_id, _signal, mailbox = live
        if run_id and running_id and run_id != running_id:
            return "stale"
        mailbox.put(text)
        return "stopped"

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
        running_id, signal, _mailbox = live
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
"""模块级单例。测试用 `LIVE.clear()` 隔离。

**后台那条路不是这个形态，别照抄**：保存触发的整理绝不在内存里 enqueue 模型调用，
它只写持久的 `chapter_refresh_attempt`（ADR 0029），由 dispatcher 转成结果——
进程一退就没了的注册表只够回答「这次对话还开着吗」这种问题，扛不住付费动作。
"""


# ══════════════════════════════════════════════════════════════════════════
# 出参：**对话的投影，不是对话的原文**
# ══════════════════════════════════════════════════════════════════════════


class ChatMessageView(BaseModel):
    """作者在屏幕上看得见的一条。

    **三种说话人。** 工具返回和「只叫工具没说话」的那几条不在这里——见模块
    docstring 第三条。少掉的那部分不是被藏起来了：它有一个数（`TurnReceipt.lookups`），
    而**零必须带着理由**这条规矩在这儿的形态是「查了几次说得出来，查到了什么不上屏」。

    第三种 `system` 是「这一轮没跑成」那一行（`ChatStore.note`，迁移 012）。
    **它不是对话的一部分**：库里它在第三档 `section` 上，读回来进的是
    `StoredChat.notices` 而不是 `Conversation`——所以它进不了 prompt、也不会被
    `rules.is_rule()` 当成一条规矩。这一层只负责把它摆回作者看得见的位置。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    """在这段对话历史里的位置。前端拿它当 key —— **不要用它当业务标识**。

    ⚠️ **`system` 那一档不占历史下标**（它不在历史里），所以它这个数说的是
    「它前面有几条历史消息」——**和紧跟其后那一条撞号是正常的**。
    前端不许拿它当身份，也不许拿它去调任何一条按 `seq` 定位的路由
    （那个历史下标坐标只对 `author` / `assistant` 成立）。
    """

    speaker: Literal["author", "assistant", "system"]
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

    compressed_blocks: int = 0
    """这一份投影里有几块**更早的对话被压成了摘要**（docs_dev 快照第五节）。

    压缩只发生在「装不下」那一档：把最旧块换成摘要，canonical 和界面原文一字不动。
    **裁了什么必须说出来**——作者有权知道模型读到的是摘要，而原文可以按编号取回。
    """


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

    brief: str = ""
    """助手给写手的「这一稿要做什么、要守什么」（ADR 0047）。**作者看得见它喂了什么**：
    桌上那张卡能展开看。空 = 旧机制写的稿（迁移 037 之前），界面上那一格不画。"""

    materials: tuple[str, ...] = ()
    """助手挑出来补给写手的资料，每条一段（同上）。"""


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
    """说给作者的那一句。**措辞唯一出处是 `agent.loop.stop_wording()`**，前端不许再翻一遍。

    **这一轮在对话里留了一行的时候，界面不许把这句话再画一遍**：那时它已经是
    `messages` 里那条 `system`（下面），两处一起画就是同一句话在同一块屏幕上出现两次。
    判据是**结构**——`messages` 里有没有 `speaker == "system"`——不是拿两串字去比。
    """

    reply: str = ""
    messages: tuple[ChatMessageView, ...] = ()
    """这一轮新长出来的、上得了屏的那几条。

    末尾可能有一条 `speaker == "system"`：这一轮什么都没跑出来，而那句「为什么」
    已经**落进库里**了（迁移 012 / `_notice_for`）。它不是这份回执生成的一句话，
    是重新打开这段对话时照样读得到的那一行。
    """

    steps: int = Field(default=0, ge=0)
    lookups: int = Field(default=0, ge=0)
    """这一轮查了几次（工具调用次数）。"""

    unanswered: int = Field(default=0, ge=0)
    """作者中途说的、这一轮没来得及答的那几句（`agent.loop.Mailbox`）。它们已经在对话里。"""

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


class ChatSaid(BaseModel):
    """`POST …/say` 的回执：这句话**排进正在跑的那一轮了没有**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    queued: bool
    """`false` = 没排进去（那一刻没在跑 / 在跑的是另一轮）。**不是失败**，也**没有落库**：
    前端拿着这句话按新的一轮发出去就是。"""

    message: str


class ChatDeleted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    deleted: bool


class RecordedRule(BaseModel):
    """作者交代过的一条规矩，**摆进那张表里的一行**（[ADR 0028] + 迁移 016）。

    ⚠️ **它不是 2026-08-14 撤掉的那个东西，别把两者当成一回事。**

    | | 撤掉的那个（`GET …/chats/{cid}/rules`） | 这一个 |
    |---|---|---|
    | 回答的问题 | **这一章此刻哪几条生效** | **我到底跟它交代过什么** |
    | 形态 | 对话头上一颗常驻按钮 + 一块能点掉的面板 | 一张回头翻的表 |
    | 有没有取消 | 有（那是它存在的理由） | **没有，一颗按钮都没有** |
    | 生不生效 | 引擎按章号算给作者看 | **不说** —— 那是模型每一轮按情境判的事 |

    作者 2026-08-15 的原话：「你在写某一章的时候用户讲过的规则，然后你规定的时效
    什么的都可以记一下」。**「记一下」和「摆出来让他管」是两件事**，他 8-14 否掉的是后者。

    **这里没有「它还作不作数」这一列，而且不许加。** 那个答案只有读到规矩的那个模型
    知道（ADR 0028），引擎给不出来——给一个出来就是编，而编出来的那一列看起来完全正常。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    """他说这话时在写第几章。**时间轴就是它**——对一个写了 722 章的人来说，
    「第几章」比「几月几号」有用得多，而且它已经存着（`ToolContext.working_chapter`，
    约束 10：不是他填的）。"""

    text: str
    """那句话，作者自己的措辞（模型被要求原样重述）。"""

    until: str = ""
    """模型当时判定它管到什么时候。**空 = 迁移 016 之前记下的**，那时还没有这一格；
    界面照实说「没记下」，**不许替它编一个**。"""

    chat_id: str
    chat_title: str = ""
    """哪一段对话里说的 —— 表上那一行的出处，作者点回去能读到上下文。"""


class RecordedRules(BaseModel):
    """全书的规矩表（`GET /api/projects/{pid}/rules`）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[RecordedRule, ...] = ()
    """按章号从大到小（他最近在写的那一章排最前），同章按说出来的先后。"""

    scanned_chats: int = Field(default=0, ge=0)
    """这一次翻了几段对话。**零条规矩时它就是那个零的成色**（§10 约束 8）：
    「一段对话都没有」和「说过话但一条规矩都没记下」在屏幕上是两句不同的话，
    而后者今天是**默认**那一档——`remember_rule` 在真书上一次都没开过火。"""


class NewChat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    write_rule: str = ""
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


class SayBody(BaseModel):
    """作者在一轮**跑着的时候**又说了一句（2026-09-12，`agent.loop.Mailbox`）。"""

    model_config = ConfigDict(extra="forbid")

    said: str = Field(min_length=1, max_length=20_000)
    run_id: str = Field(default="", max_length=64)
    """**想插进哪一轮**。对不上就不排（同 `StopBody.run_id`）：一次迟到的「再说一句」
    不该插进作者刚发起的下一轮——那一轮的第一句话已经是他自己发的了。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


def build_agent_model(config: ProviderConfig, plan: ResolvedCallPlan) -> ModelPort:
    """造这一轮的模型端口。**测试替换的就是这一个函数**（同 `deps` 里那几个注入点）。"""
    return ProviderModelPort(config, plan)


def _summarize_conversation_block(
    text: str, config: ProviderConfig, capability: ProviderCapabilities, *, cancel: Cancellation
) -> CompletionResult:
    """把最旧一段对话压成一句摘要（docs_dev 快照第五节）。**一次真的模型调用。**

    只读、确定性 prompt、按块原文做幂等键；loop 拿到 `CompletionResult` 自己记账
    （`_compress_oldest_block` 的 `bill`），这一层不碰账本——同 `build_agent_model`
    的位置。

    `cancel`：**和这一轮别的调用同一个信号**（2026-09-12）。它是这一轮里唯一一次非流式的
    调用，以前作者按停对它无效——要等整份摘要回来。现在走 `cancellable_client`，信号一亮
    连接当场掐断（`agent/model.py::_cut`），loop 收到 `CallInterrupted` 按「作者停的」收场。
    """
    from ..agent.model import cancellable_client
    from ..draft.block_summary import BLOCK_SUMMARY_LENGTH, block_summary_messages
    from ..draft.capabilities import ReasoningEffort, plan_call
    from ..draft.length import DraftLanguage, count_units
    from ..draft.provider import complete
    from ..draft.product_context import TOKENS_PER_UNIT

    plan = plan_call(
        BLOCK_SUMMARY_LENGTH,
        ReasoningEffort.OFF,
        capability,
        prompt_token_budget=count_units(text, DraftLanguage.ZH) * TOKENS_PER_UNIT,
    )
    return complete(
        block_summary_messages(text),
        config=config,
        plan=plan,
        client=cancellable_client(config, cancel),
    )


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
    chat_id: str,
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
        language=DraftLanguage(proj.language),
        root_path=proj.root_path,
        drafter=desk,
        # **和起草台是同一把锁**：并发窗口里碰这条连接的每一句都要排在同一道队里
        # （`agent/ports.py::ToolContext.db_lock` 记着为什么）。两把 = 各排各的 = 没排。
        db_lock=db_lock,
        summaries=SummaryStore(conn),
        events=SqliteEventStore(conn),
        # 右栏那两栏的只读端口（2026-09-12，`agent/panels.py`）：检验规则 / 通知。
        # 角色卡和事件走的是上面 `events` 那个端口（它 2026-09-12 多了三个读方法）。
        rules=RulesReader(conn),
        notices=NoticeReader(conn, store),
        # 轨道核对（轨道阶段 3）。**轨道握在这个闭包里，不在 `ToolContext` 上**——
        # 同 `drafter` 那条：模型碰得到的是一个已经判完的结论（三个数），
        # 不是 `Track` 本身。核对模型没配时闭包返回一句「没接线」，工具照实说。
        track_check=_a_track_check(conn, store, proj.id),
        # 一条 `MAX(number)`。**和调度原点、轨道问的是同一个问题，判断只许有这一份**
        # （`focus.frontier_chapter` 的 docstring 点名了这条）。
        frontier_chapter=frontier_chapter(conn, proj.id),
        working_chapter=chapter,
        max_context_tokens=capability.max_context_tokens,
        # **对话那一档的输出预算**（`AGENT_REPLY_LENGTH` 倒推的），用来算「一次工具返回
        # 最多给多少字」。起草那一次调用的预算是另一个数，由 `chapter_drafter` 自己算
        # ——两档长度不同，共用一个 plan 会让工具返回的天花板跟着起草的输出预算走。
        reserved_output_tokens=plan.request_token_budget,
    )


def _a_track_check(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
) -> Callable[[int], TrackVerdict]:
    """把「轨道核对」包成一个只出结论的闭包（ADR 0019 边界一的落点）。

    **轨道原文一个字都不过这条边**：`review_track_on_demand` 手里有 `Track`，
    交出来的是 `TrackClash` 那三个数。`agent/` 那一侧连 `build_track` 都 import 不到
    （`tests/test_track_isolation.py` 钉着那份白名单）。

    **核对模型没配就说没配**，不静默返回一份空清单——「没抵触」和「压根没核对」
    在模型眼里长成同一个空清单，而这两件事的下一步动作完全相反（§10 约束 8）。
    """

    def check(chapter: int) -> TrackVerdict:
        try:
            reviewer = get_advisory_reviewer()
        except Exception:  # noqa: BLE001 —— 没配 / 配错都只是「这一问没跑」，不是 500
            return TrackVerdict(
                chapter=chapter,
                note="这套工作台没配核对模型，跟后面章节抵不抵触这一问没跑。",
            )
        outcome = review_track_on_demand(
            conn, store, project_id, chapter, reviewer=reviewer
        )
        return TrackVerdict(
            chapter=chapter,
            clashes=outcome.clashes,
            note=outcome.notes.get("track", ""),
        )

    return check


def _visible(
    messages: tuple[AgentMessage, ...],
    first_seq: int,
    notices: Sequence[ChatNotice] = (),
) -> tuple[ChatMessageView, ...]:
    """canonical（+ 那几行提示）→ 屏幕上那几条。工具返回和「只叫工具没说话」的不出去。

    **提示要插回它自己的位置，不能一律摆到最后。** 作者说一句、跑砸了、又说一句、
    又跑砸了——两行提示各属于一轮，堆在末尾读起来就是「最后这一轮失败了两次」。
    位置由 `ChatNotice.after_history` 给（它前面有几条历史），而那个数是按库里
    那一串 `seq` 数出来的，**顺序仍然只有一份真相**。

    `first_seq` 之前的那几行不出去：回执切的是**这一轮**新长出来的那一段，
    把上一轮的提示带进去就是同一句话在屏幕上出现两遍。
    """
    out: list[ChatMessageView] = []
    waiting = [n for n in notices if n.after_history >= first_seq]
    cursor = 0

    def drain(upto: int) -> None:
        nonlocal cursor
        while cursor < len(waiting) and waiting[cursor].after_history <= upto:
            note = waiting[cursor]
            out.append(
                ChatMessageView(seq=note.after_history, speaker="system", text=note.text)
            )
            cursor += 1

    for offset, message in enumerate(messages):
        # 「它前面有 N 条历史」= 它排在第 N 条**之前**，所以先把它放下去。
        drain(first_seq + offset)
        if message.role is Role.USER:
            speaker: Literal["author", "assistant", "system"] = "author"
        elif message.role is Role.ASSISTANT and message.content.strip():
            speaker = "assistant"
        else:
            continue
        out.append(
            ChatMessageView(seq=first_seq + offset, speaker=speaker, text=message.content)
        )
    drain(first_seq + len(messages))
    return tuple(out)


def _notice_for(
    result: TurnResult, shown: Sequence[ChatMessageView], drafts: Sequence[DraftCandidate]
) -> str:
    """这一轮要不要在对话里留一行，留哪句话。**空串 = 不留。**

    ── 判据（一句话说得出口）────────────────────────────────────────────────

    > **一轮跑完，屏幕上除了作者自己那句话什么都没有，而这不是他自己叫停的。**

    2026-08-13 的真实现场就是这个形状：作者说了两句，助手一个字都没有（端点 TLS 断了，
    那一轮在发出去之前就死了）。屏幕上确实弹过一句提醒，但它活在组件状态里——
    组件一卸载、他再发一句，那句话就没了。**留痕就是把这一句从界面状态挪进对话。**

    ── 两半都是结构判据，不是一张「哪几种停法算失败」的表 ────────────────────

    那种表会在下一种停法长出来的那天漂（同 `rules.is_rule` 用结构判据、
    `ToolOutcome.chapter` 取法那两条先例）。这儿的两半是：

    - **「什么都没留下」走的是 `shown`**，也就是**同一个 `_visible` 的产物**——
      屏幕上有没有东西，只许有一处答案。稿子（ADR 0022）和它问的那一句
      （ADR 0024）不在 `shown` 里但都是「留下了东西」，所以各查一次。
      `DONE` 因此自动不留痕（那一档 `reply` 必非空，见 `run_turn` 的收场），
      不需要在这儿点它的名。
    - **「不是他自己叫停的」只排掉 `AUTHOR_STOPPED`**：作者按了停不是失败
      （ADR 0024 有 `turn_stopped`，回执那句话是「按你的意思停下了」）。
      把它写成一行「这一轮没跑成」是**把他做的一个决定说成一次故障**。

    ── 不在这个函数里的那几档失败，以及为什么它们不留痕 ──────────────────────

    | 失败 | 为什么不留 |
    |---|---|
    | 422（`_plan_or_422`：模型没配好） | 它抛在 `_TurnRun.__init__` 的第一行，**作者那句话根本没进历史**。写一行进去就是凭空造出一轮没发生过的对话：屏幕上会有「这一轮没跑成」，而它上面没有任何人说过话。那一档作者的字被还回了输入框，那才是他要的东西。 |
    | 409（这段对话正在跑上一轮） | 同上，这一轮**根本没开始**；而且它说的是「你手速太快了」，把它写进历史等于给作者的书里记一笔他自己的手误。 |
    | 流跑到一半撞上乐观并发闸 | 要写这一行就得再追加一次，而追加正是刚刚失败的那个动作。 |
    | 认不出的崩溃 | **后端说不出一句给作者的话**（`maintainer_note` 是英文诊断，一个字都不出去）。编一句 = §10 约束 8 禁的那种话，所以这儿交白卷，界面说它自己那句「没能说清是为什么」。 |

    Args:
        result: 这一轮的 `TurnResult`（`agent.loop`）。
        shown: 这一轮新长出来、上得了屏的那几条（`_visible` 的产物）。
        drafts: 这一轮写出来的那几稿（`ChapterDesk.produced`）。
    """
    if result.reason is StopReason.AUTHOR_STOPPED:
        return ""
    if drafts or result.asked is not None:
        return ""
    if any(view.speaker == "assistant" for view in shown):
        return ""
    # **措辞原样搬**：`stop_wording()` 是唯一那一份，这一层一个字都不加
    # （加了就是第二份措辞源，而两份一定会漂）。
    return result.said_to_author


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
        brief=candidate.brief,
        materials=candidate.materials,
    )


def _load(conn: Connection, project_id: str, chat_id: str) -> StoredChat:
    stored = ChatStore(conn).load(project_id, chat_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found"},
        )
    return stored


def _plan_or_422() -> tuple[ProviderConfig, ProviderCapabilities, ResolvedCallPlan]:
    """模型配好了没 + 这一档它撑不撑得起。**两个问题两句话**，别合并。"""
    reason = model_configuration_error()
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    config = agent_provider_config()
    try:
        # **能力由装配层解析，不让 `agent/` 自己去查**：只有这一层够得着作者在设置页
        # 手填的上下文窗口（`agent/` 不读设置）。自己解析的话，他填的那个数管得到
        # 起草抽屉/抽取/总结，唯独管不到写作助手 —— 一半听一半不听，而且不报错。
        capability, plan = agent_call_plan(config, resolve_route_capabilities(config))
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
    session = store.create(
        proj.id,
        title=body.title,
        write_rule=body.write_rule or None,
        language=DraftLanguage(proj.language),
    )
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
    """看一段对话。404 = 这一段不在（或不属于这本书），和 501 分得开。

    **那几行「这一轮没跑成」也在这儿**（迁移 012）。它们是这条路由存在感最强的一次
    兑现：作者切走再切回、换台机器、三个月后翻回来，看到的仍然是「那一轮没跑成」——
    而在这之前那句话只活在浏览器的组件状态里，他再发一句就没了。
    """
    stored = _load(conn, proj.id, chat_id)
    return ChatDetail(
        session=_session_view(
            stored.session,
            stored.conversation,
            stored.history_count,
            running=LIVE.running((proj.id, chat_id)),
        ),
        messages=_visible(stored.conversation.messages, 0, stored.notices),
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
            detail={"error": "chat_busy", "params": {"action": "delete"}},
        )
    if not ChatStore(conn).delete(proj.id, chat_id):
        raise HTTPException(status_code=404, detail={"error": "chat_not_found"})
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
                detail={"error": "chat_busy", "params": {"action": "send"}},
            )
        # 作者中途说的话从这儿进来（`POST …/say` 往里放，loop 在步的边界取）。
        self._mailbox = LIVE.mailbox(self._key)
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
            # **作者那条一直挂着的要求要再送一遍。** 它已经在对话前缀里了，
            # 但起草是另一次调用，那份 prompt 从零拼、和对话一个字都不共享 ——
            # 不送就是「助手听见了、写手没听见」，而且不报错
            # （2026-08-13 在真书上实测：五稿二十五段一段都没照做）。
            write_rule=write_rule_of(self._conversation),
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
                    chat_id=self._chat_id,
                    chapter=self._chapter,
                    desk=desk,
                    capability=self._capability,
                    plan=self._plan,
                    db_lock=db_lock,
                ),
                # **回话那一档没有接事件流，这是有意的，不是漏的**（ADR 0024 的红字）。
                # 2026-09-12 起那条线在 wire 上**是**流式的了（`agent_call_plan` 要了
                # `interruptible`，为的是「停」落在下一片之内），所以 `reply_delta` 真的
                # 有片可发——但这个注入点的签名（`build_agent_model(config, plan)`）是
                # 十几处测试桩共用的，接 `on_event` 要一起改它们；而且那一天要一起决定
                # 的是界面上那一档怎么画（`frontend/src/chat.ts::applyTurnEvent` 今天
                # 对 `reply_delta` 的答案是「不画」）。两件事一起做，别只做一半。
                model=build_agent_model(self._config, self._plan),
                ledger=_ledger(self._conn, self._proj.id),
                # **装不下时先压最旧一段对话**（docs_dev 快照第五节）：作者的话是唯一
                # 不可剪的累积，压缩是它唯一的出口。`None` 会退回 CONTEXT_FULL 停，
                # 但产品档要接——否则长对话永远硬停。
                block_summarizer=lambda text: _summarize_conversation_block(
                    text, self._config, self._capability, cancel=self._signal
                ),
                # **并发只在这一层放开**（见 `AGENT_PARALLEL_TOOLS`）：只有开连接的人
                # 知道这条连接跨不跨得了线程。
                limits=TurnLimits(parallel_tools=AGENT_PARALLEL_TOOLS),
                cancel=self._signal,
                persist=keep,
                on_event=on_event,
                mailbox=self._mailbox,
                # 按停之后再问他一句（作者 2026-09-12）——产品这条路开着；
                # 见 `run_turn` 那条参数的 docstring。
                debrief_on_stop=True,
            )
            self._save(result.conversation)

            shown = _visible(result.conversation.messages[self._turn_start :], self._turn_start)
            # ── 这一轮什么都没留下的话，把「为什么」留在**对话里**（迁移 012）──────
            # 判据和措辞都在 `_notice_for`。
            #
            # **它必须排在 `LIVE.end` 之前**（也就是留在这个 `try` 里）：位子一还回去，
            # 作者下一句话就能开跑、就能往历史里追加，而那一行落在哪儿是按「它前面有
            # 几条历史」定的——晚一步，这句「这一轮没跑成」就挂到了他刚发出去的**下一句
            # 话后面**，读起来像新那一轮失败了。
            notice = _notice_for(result, shown, desk.produced)
            if notice:
                landed = self._chat_store.note(self._proj.id, self._chat_id, notice)
                shown = (
                    *shown,
                    ChatMessageView(
                        seq=landed.after_history, speaker="system", text=landed.text
                    ),
                )
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
            messages=shown,
            steps=result.steps,
            lookups=result.tool_calls,
            unanswered=result.unanswered,
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
    "idle": "当前没有正在进行的一轮，无需停止。",
    # **这一句不许说成失败**（同上一句）：作者按的那一下是对的，只是它想停的那一轮
    # 在这几百毫秒里自己跑完了。说「按钮没反应」会让他再按一次，而再按一次就会
    # 停掉他刚发出去的那一句——正是这道比对要防的事。
    "stale": "上一轮已自行结束，这次「停」未作用于当前进行的新一轮。",
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
            detail={"error": "chat_not_found"},
        )
    verdict = LIVE.stop((proj.id, chat_id), (body.run_id if body else ""))
    return ChatStopped(
        chat_id=chat_id,
        stopped=verdict == "stopped",
        message=_STOP_WORDING[verdict],
    )


_SAY_WORDING: dict[StopVerdict, str] = {
    "stopped": "已加入本轮，下一步读取。",
    "idle": "当前没有正在进行的一轮，消息未排入；请直接发送。",
    "stale": "当前进行的是新的一轮，消息未排入；请直接发送。",
}
"""三档结局各自那一句（同 `_STOP_WORDING`）。**措辞只有这一份**，前端照抄 `message`。"""


@router.post("/api/projects/{project_id}/chats/{chat_id}/say", response_model=ChatSaid)
def say_mid_turn(
    chat_id: ChatId,
    body: SayBody,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatSaid:
    """一轮跑着的时候再说一句（2026-09-12）。**它不等、不打断、不落库。**

    作者的原话：「像 codex 那样，新的消息可以直接发出去，模型可以读，并且不会耽误正在
    做的」。机制在 `agent.loop.Mailbox`：这句话放进正在跑的那一轮的信箱，loop 在下一次
    模型调用之前把它按正常的作者消息追加进对话（那时才落库、才喊 `author_said`），
    正在跑的那一步一个字都不受影响。**这条路由自己一个字都不写库**——对话的写入只有
    loop 那一条线，两条线各写一次就会撞上乐观并发闸（`_TurnRun` 那段实测故障）。

    `queued=false` 不是失败：那一刻没在跑（跑完了）或在跑的是另一轮——那句话没有
    排进去也没有落库，前端拿着它按新的一轮发出去就是。
    """
    if ChatStore(conn).get(proj.id, chat_id) is None:
        raise HTTPException(status_code=404, detail={"error": "chat_not_found"})
    verdict = LIVE.say((proj.id, chat_id), body.run_id, body.said)
    return ChatSaid(chat_id=chat_id, queued=verdict == "stopped", message=_SAY_WORDING[verdict])


# ⚠️ **`GET`/`DELETE …/chats/{cid}/rules[/{seq}]` 两条路由 2026-08-14 删了**
# （[ADR 0028](../../../docs/adr/0028-rules-expire-by-situation.md)）。
#
# 它们是 ADR 0023「看得见 + 能取消」那一半的 HTTP 面，服务的是写作助手顶上那颗
# 「这一章的规矩」按钮——而那颗按钮同日撤了。作者的原话：
#
#   「这个原本就不需要展示给用户看，并且这个规则是有时效性的……
#     这个要由 llm 智能判断，而不是显示出来给用户选择。」
#
# **零调用方的端点不许留着。** 这个仓库为它栽过一次（ARCHITECTURE「工作台的已知洞」
# 第 4 条：滚动总结的两条端点「有了」却没人调，于是它一直在花作者的钱、影响每一稿，
# 而他看不见改不了删不掉）。删掉是那条教训的正着用法。
#
# `AuthorRuleView` / `ChatRules` / `RuleRevoked` 三个出参模型、`_rule_view()`
# 和 `rules.revocation()` 一起没了。**引擎侧一个字没动**：规矩照旧被记下、照旧进 prompt，
# 只是有效期从「按章号算」变成「模型按情境判」。
#
# **2026-08-15 长出来的是另一条**（`GET /api/projects/{pid}/rules`，就在下面）：
# 它回答的不是「这一章哪几条生效」，是「我到底跟它交代过什么」——一张回头翻的表，
# 没有取消按钮，也不说哪条还作不作数。两者的差别写在 `RecordedRule` 的 docstring 里。


@router.get("/api/projects/{project_id}/rules", response_model=RecordedRules)
def recorded_rules(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> RecordedRules:
    """作者交代过的每一条规矩，**按他说话时在写第几章排**。

    ── 为什么逐段把对话读出来，而不是一条 SQL 扫过去 ──────────────────────────

    「这条被撤销过没有」的坐标是**历史下标**（`AgentMessage.revokes_seq`），而下标只有
    在一整段历史手上才算得出来（`rules._revoked_indices`）。用 SQL 现拼一遍那个判据 =
    第二份实现，而它错的形态是**作者当年明确取消过的规矩又出现在这张表上**。

    代价是 O(段数) 次 `load()`。**今天这个代价接近零**：真书上 7 个作者回合、一段对话。
    哪天它真的贵起来，正确的修法是给 `ChatStore` 加一条只取候选行的读端，
    **不是**在这一层重写那条判据。

    ⚠️ **它和 2026-08-14 撤掉的那两条不是一回事**，差别在 `RecordedRule` 的 docstring 里。
    """
    store = ChatStore(conn)
    rows: list[RecordedRule] = []
    sessions = store.list(proj.id, limit=limit)
    for session in sessions:
        stored = store.load(proj.id, session.id)
        if stored is None:  # 两次查询之间被删掉了；这一段就当不存在
            continue
        messages = stored.conversation.messages
        for index in sorted(surviving_rule_indices(messages)):
            message = messages[index]
            if message.chapter is None:  # 没有坐标的老规矩，表上摆不出行
                continue
            rows.append(
                RecordedRule(
                    chapter=message.chapter,
                    text=message.content,
                    until=message.rule_until,
                    chat_id=session.id,
                    chat_title=session.title,
                )
            )
    # 他最近在写的那一章排最前；同一章里按说出来的先后（`rows` 已经是那个序）。
    rows.sort(key=lambda r: -r.chapter)
    return RecordedRules(rules=tuple(rows), scanned_chats=len(sessions))


# 「桌上那几稿」的列表路由（`GET …/drafts?chapter=`）2026-09-12 随并排页和「本章 N 稿」入口
# 一起撤了（作者：「这块就不要了」）：稿子流进左边的编辑器，右边每稿一行；前端从此只按编号
# 单取一稿（下面那条）。
@router.get(
    "/api/projects/{project_id}/drafts/{draft_id}", response_model=DraftCandidateDetail
)
def read_draft(
    draft_id: Annotated[str, Path(min_length=1)],
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> DraftCandidateDetail:
    """一稿的全文。**作者按「放入编辑器」读的就是它**（ADR 0048）。

    404 = 这本书里没有这一稿（编号对不上，或者它落过盘、又老到被清理掉了——
    清理只清落过盘的，那些字在版本历史里还在）。
    """
    stored = DraftCandidateStore(conn).get(proj.id, draft_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "draft_not_found"},
        )
    return DraftCandidateDetail(
        **_draft_view(stored).model_dump(),
        # **正文在这儿，也只在这儿。** 回执里那几行一个字都不给——作者要的是
        # 「哪一版是哪一版」，字在他按「放入编辑器」之后进左边的编辑器。
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
    except ChatConcurrency:
        # 曾经把 `str(exc)` 拼进 `message` 末尾——那句本身是安全的（只有两个计数），
        # 但已经在外层这句话里重复说了一遍同一件事，不必带两份。
        raise HTTPException(status_code=409, detail={"error": "chat_conflict"})


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
        compressed_blocks=projection.compressed_blocks,
    )


__all__ = ["LIVE", "build_agent_model", "router"]
