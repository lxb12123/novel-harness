"""模式二的 **agent loop** —— 循环归模型，**停止条件归代码**（ADR 0019）。

规格书是 [`docs/adr/0019-agent-loop-not-graph.md`](../../../docs/adr/0019-agent-loop-not-graph.md)。
这个文件是它的执行态：**「一串 message + 哪几个 `tool_call` 还缺 `tool_result`」**，别的什么都没有。
没有节点、没有条件边、没有并行 fan-out——正因为没有，Checkpoint 那套机制在这儿 90% 是空转，
而 resume 只是「看尾巴、补跑缺的、继续」（`Conversation.pending_calls` + `run_turn` 开头那一段）。

── 这一层的分工，一句话 ────────────────────────────────────────────────

**模型决定叫哪个工具、叫几次、什么时候收手。** 代码只负责四件事：

1. 把工具声明发出去（`tool_declarations()`，由表生成，这儿不写第二份）；
2. 把 `tool_call` 交给 `dispatch`，把 `tool_result` 贴回去；
3. **在该停的时候停得下来**（十一种停法，每一种都说得出自己为什么停）；
4. **每一次模型调用都记一笔账**（`ledger`，见下）；
5. **每长出一条消息就交给调用方落一次库**（`persist`，见下）；
6. **在每一步的边界上往外喊一声**（`on_event`，ADR 0024 —— 见下）。

第 5 条不是「顺手也存一下」：**没有它，`pending_calls` 在产品里恒为空**。一轮的产物只在
返回之后整批落库的话，进程死在中途 = 这一轮一条都没进库 = 尾巴上根本没有那条带
`tool_calls` 的 assistant，于是「看尾巴、补跑缺的」这句 ADR 原文无事可补——而那几次模型
调用的钱**已经记在账上了**。

── `dispatch` 外面**没有** try/except，这是有意的 ──────────────────────

`tools.dispatch()` 的 docstring 写着理由：四种失败（工具名不在表里 / 参数不是合法 JSON /
参数不合 schema / 工具自己拒绝）**全部是 `ok=False` 的正常返回，不是异常**——抛出去只会让
这个文件变成一串 try/except，而漏掉其中一个的后果是整个会话死掉。所以这里对派发
（`tools.BatchRunner`，它是 `dispatch` 唯一的调用方）的调用是**裸调用**，
一个 `except` 都不许加：加了就等于把那个设计撤销掉。

同理，**这里不校验工具名**。那道闸只在 `dispatch` 里（运输层也不认识工具表，ADR 0019
边界一）。这个文件里唯一读 `ToolCall.name` 的地方是「无进展」的判据，它比的是**字节相同**，
不回答「这个名字合不合法」。

── 记账：账记在哪一层 ──────────────────────────────────────────────────

`extract/call_audit.py::record_call` 要一个 `Connection`，而 **`ToolContext` 上故意没有 conn**
（`ports.py`：没有写入面 ⇒「模型改了作者的 canon」在类型层就不可能）。为了记账把 conn 塞进
`ToolContext` 是把那条边界拆掉换一张发票，不干。

所以：**这一层生成账单原料（`ModelCallReceipt`），持久化留给持有 conn 的那一层**
（3.4 的 HTTP 壳）。`ledger` 是 `run_turn` 的**必填**参数，不是可选项——

> `/draft` 一行 `model_call` 都不写……作者花钱最多的动作在账上一行都没有，
> 于是日志页显示的是真实花销的一小部分，看起来却像全部。

agent loop 是第二个会大量花钱的地方。给 `ledger` 一个 `None` 默认值 = 日志页第二次骗人，
而且这一次是明知故犯。**记账失败也不吞**：账记不上的那一次调用已经花过钱了。

**工具花掉的钱走同一个入口**（`run_tool` → `bill`）：`draft_chapter` 每跑一次是一次真的
模型调用，起草侧把回执（`DraftProduct.calls`）交回来，这一层记账 + 计闸。3.3 那一版没有
这条线，于是 `max_tokens` 罩不住表里唯一一个不免费的工具——那正是上面引的那句话的形状，
只是换了个地方发生。

**诚实说明**：`ProviderError`（网络断了、供应商 4xx）那一路**没有账**——没有 `CompletionResult`
就没有 token 数，而这一层不许替它编一个（`model_call.cost` 那一列至今空着，正是同一条纪律：
BYOK 之下引擎不知道作者签的什么单价，宁可空着也不猜）。已经发出去、生成到一半被作者掐掉的
那些 token 因此不在账上——这是今天真实的漏账口，写在这儿是为了它别被当成不存在。

── 边跑边说话：**一条回调，不认识任何传输**（ADR 0024）──────────────────

第 6 条只有一个函数（`EventFn`），loop 在自己**已经知道**的那几个边界上叫它：
在查什么 / 查完了成没成 / 它这一步说了什么 / 它停下来问了作者 / 为什么停。
WebSocket、SSE、终端刷屏都是**适配器**的事——扔掉适配器、`on_event` 空着不叫，
就是 2026-08-12 之前那个「三分钟的黑箱」，逐字节相同。

**模型吐字那两条流不在这个文件里**：它们从 `_CancellableClient` 那层流过
（`agent/model.py` / `agent/drafting.py`），因为 loop 按定义不认识运输——
`ModelPort` 那个协议的存在就是为了不认识。装配层把**同一个** `on_event` 交给三处，
同 `cancel`（两个信号 = 按停只停住一半）。

**事件里没有工具查到了什么**（边界一）：一轮的返回一直是投影过的，事件流不许把它摊开。
判据在 `TurnEvent` 的类 docstring，网在 `tests/test_agent_events.py`。

── 投影：**按章号参数化，不按时间近**（边界五）────────────────────────

对话历史是 canonical，永不破坏；模型看到的是它的一次投影，签名是
`project(conversation, chapter, *, budget_units)`。「最近」只决定**取多少**，
「第几章」决定**取什么**。3.3 还没有持久化的长历史，所以今天看不出差别——但签名里没有
那个参数的话，3.4 会把它硬拧上去，而边界五**错的时候不会报错**（产出的是一段读起来
完全正常、只是说破了不该说破的东西的正文）。

剪枝顺序**写死**，判据是**可不可重建**（ADR 0023 决策一，它把 ADR 0019 边界五那句
「按位置」重述成了按类别）：老 `tool_result`（重查回来的**更新鲜**，丢掉是升级）→
老 `tool_call` → agent 中间推理（能重想）→ **最后才碰作者说的话**（**全场唯一不可重建的
东西**）。这一层的最后一档是**停下来**（`CONTEXT_FULL`），不是悄悄砍掉作者说的话。

**作者定下的规矩**（`rules.py`）在这条链之外：它不按预算剪，只按章号过期
（ADR 0023 那张表的第二行——「小、跨轮有效、丢了最气人」）。
**它进 canonical 的唯一入口也在这个文件里**：模型叫一次 `remember_rule`，
这一层在收场时把它贴成一条 SYSTEM 消息（`finish()`）——章号来自
`ToolContext.working_chapter`，作者和模型都碰不到它（约束 10）。

**「正文永不压缩」在这一层是白拿的**：这里只剪不压，一个字都不摘要
（摘要是 3.4 的事，而边界四管着它：总结不许含图谱事实）。剪掉的工具返回重查一次就有，
**而且查回来的是当前的**——这正是把 `tool_result` 排在剪枝第一位的理由。

── 预算量的是**真正发出去那份 payload**，工具声明也在里面（ADR 0023「前置」）──

`budget_units` 管的是 `{"messages": […], "tools": […]}` 这**整份**东西，不是只有对话。
以前只量对话，而工具声明有近 4,000 字——**约 23% 的系统性低估，方向偏松**，
于是「省了多少」全是估的。现在 `Projection.payload_units` 是量出来的那个数，
`tests/test_agent_loop_budget.py` 把它和 `draft.provider._wire_kwargs()` 真的要发出去的
那份 kwargs 对上，误差写死在 `PAYLOAD_ENVELOPE_UNITS` 里。

── 稳定前缀（边界六）：它是结构，不是纪律 ──────────────────────────────

`Conversation.prefix` 和 `Conversation.messages` 是**两个字段**，投影永远是 `prefix + …`，
而 `prefix` 的校验器拒收任何带章号的消息。于是「跨章不变的那块排在最前面」不是一句自觉，
是构造不出反例。能进前缀的只有文风和工具表声明（后者走 `tools=` 参数，本来就在最前面）。

**它拦不住什么（诚实交代）**：`write_rule` 是自由文本入口，作者把一条 `must_not_reveal`
用自然语言写进文风里，这一层看不见——判它要回答「这句话是不是把伏笔说破了」，
那是语义判断（ADR 0005 在 v1 里禁止本仓库长出这种能力）。同 `assemble()` 的 `goal`。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from time import perf_counter
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..draft.length import DraftLanguage, count_units
from ..draft.product_context import TOKENS_PER_UNIT
from ..draft.provider import CompletionResult, ProviderError, ToolCall
from ..extract.call_audit import ModelCallReceipt
from .ports import LedgerFn, ToolContext
from .tools import (
    TOOL_NAMES,
    AuthorQuestion,
    BatchRunner,
    ToolOutcome,
    outdated_manuscript,
    tool_declarations,
    tool_label,
)

_LANGUAGE: Final = DraftLanguage.ZH
"""预算的计数口径，和 `index.py` 同一个：中文按非空白字符数（`draft/length.py` 是唯一定义）。"""

AGENT_CAPABILITY: Final = "agent"
"""`model_call.capability` 这一列的取值。**加了它就要去 `activity.py` 补一行中文说法**
（`_CAPABILITY_LABEL`），否则日志页会把 `agent` 原样摆到小说作者脸上——那张表认不出的
是原样回吐的。"""

AGENT_SCHEMA_VERSION: Final = "m5.agent.v1"
"""这一轮对话的 prompt 结构版本，进 `model_call.params_json`。

**改了消息形状就要改这个数**：账上那一行是事后唯一能回答「当时发出去的是什么形状」的东西，
而 `in_artifact` 只存哈希、不存原文。"""


# ══════════════════════════════════════════════════════════════════════════
# canonical 对话 —— 永不破坏的那一份
# ══════════════════════════════════════════════════════════════════════════


class Role(StrEnum):
    """OpenAI 兼容的四个角色。**取值就是 wire 上那个字**，这一层不做第二次翻译。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(BaseModel):
    """canonical 历史里的一条。**它不是 wire 形状**——wire 形状由 `project()` 生成。

    两者分开是边界五的前提：canonical 上多带的那点元数据（这条绑第几章、这条是不是
    已经被剪成占位）正是投影要读的东西，而它们一个字都不该发给模型。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""

    chapter: int | None = None
    """**这条消息只对第几章成立。** `None` = 不绑章号。

    两种消息会绑，而**它们的取值来源和过滤方向都不一样**：

    | 谁 | 这个数是什么 | 投影怎么筛 |
    |---|---|---|
    | 工具返回 | 模型在那次调用里填的 `chapter`（`ToolOutcome.chapter`） | `> chapter` 丢（往后的清单更短 = fail-open） |
    | 作者的规矩（`rules.py`） | **引擎手里的 `working_chapter`**，模型和作者都碰不到 | `!= chapter` 丢（拿不准就放掉） |

    两个方向相反是 ADR 0023 写死的：说破一个秘密收不回来，写错一场戏改一次就好。

    作者说的话和 agent 的推理**永远不绑章号**——它们不是事实、是意图，
    也**永远不因为章号被丢掉**（剪枝顺序的最后一档就是作者的话）。
    """

    pruned: bool = False
    """这条工具返回已经被剪成一句占位。**不许把它当成「工具返回了这么一句」**。"""

    revokes_seq: int | None = None
    """这条消息**撤销的是第几条**（作者取消了一条自己定下的规矩，ADR 0023 / 迁移 011）。

    坐标是**同一段历史里的下标**（`messages` 的下标，也就是 `rules.AuthorRule.seq`），
    不是会话表里那一列 `seq`——那儿的编号把稳定前缀也数在内。

    ── 为什么撤销是一条新消息，而不是把那条改掉或删掉 ──────────────────────

    canonical **只增不改**：读回来重建出的 `Conversation` 必须和存进去之前逐字节相同，
    删一行就把这条不变量拆了，而它错的时候没有任何东西会报错。

    **也不能靠「再说一遍」表达撤销**：那条通路已经被「说第二遍 = 从批级升到章级」占用了
    （`rules.REPEAT_TO_WIDEN`），作者想取消，系统会听成加强。所以撤销必须有一个**结构上
    的槽**而不是一段字——这一位就是那个槽，整条消息的正文是空的。

    **这一层不校验它指得对不对**：`Conversation` 也用来装一段**尾巴**（会话列表数
    `pending` 时读的就是尾巴），那时它指的东西根本不在手上。判据在
    `rules._revoked_indices`，越界一律忽略。
    """


PRUNED_RESULT = "（这条查询结果已经从上下文里清掉了。需要就重新查一次——重新查到的还是当前的。）"
"""被剪掉的工具返回留下的占位。

**留占位而不是整条删掉**，是因为 OpenAI 兼容的 wire 上一条带 `tool_calls` 的 assistant 消息
必须被同样多条 `tool` 消息接住，少一条就是 400。ADR 0019 把剪枝的头两步写成
「老 `tool_result` → 老 `tool_call`」，而**在这条协议上它们不可能是两次独立的删除**——
第一步删内容留壳（省掉的是绝大部分字），第二步才把壳和它的 `tool_call` 成对拿掉。
"""

STALE_MANUSCRIPT = (
    "（这一章的正文在你读过它之后被作者改过了——上面那份是旧的，已经从上下文里清掉。"
    "要用就重新读一次，读回来的是他此刻看见的那一份。）"
)
"""被作者改掉的那一份正文，在投影里留下的占位（判据见 `tools.outdated_manuscript`）。

**这不是省 token，是边界三。** 会话表里那份正文必须存下来（不存 resume 就看到另一段
历史），但它一旦和磁盘对不上，「第 N 章是什么」就有了两个答案，而**发出去的是过期那个**。
ADR 0007 的铁律是正文的真相源在磁盘上，DB 永远不是——所以这一层的动作是：
canonical 一个字不改，**投影里把它换成一句「重新读一次」**。

换而不是删，理由同 `PRUNED_RESULT`：壳必须接住那个 `tool_call`，少一条就是 400。
"""

UNRUN_CALL = "（这一步没有执行：这一轮已经停下来了。）"
"""停在一批工具中间时，剩下那几个 `tool_call` 的占位。理由同上：**壳必须配齐**。

它和 `Conversation.pending_calls`（真的缺 result）是两件事：那一档表示进程在模型调用和
派发之间死掉了，resume 要补跑；这一档表示「这一轮明确决定不跑了」。
"""

_SYNTHETIC_CALL_ID = "nh-call-{0}"
"""端点没给 `id` 时补上的那个。**这一层必须补，因为 `id` 是它的执行态主键。**

`draft/provider.py` 有意不替模型编数据（`id=getattr(item, "id", None) or ""`），
而本地 OpenAI 兼容端点（Ollama / llama.cpp / 部分中转）**常常一个 id 都不给**，
中转也常常每一轮都从 `call_1` 重新数起。撞在一起时这一层的两件事会静默答错：

- `pending_calls` 靠 id 数「还缺哪几个」⇒ 缺的那个被当成答过了 ⇒ resume 一个都不补
  ⇒ 下一次 wire 少一条 `tool` 消息 ⇒ 400 ⇒ 这段对话**永远发不出去**，
  而作者看到的说法是「联系不上写作模型」（一个指向别处的错误说法）；
- 投影按 id 成对摘 `tool_call` 和它的返回（边界五）⇒ 一个连坐一片 ⇒
  刚为**当前这一章**查回来的东西也没了 ⇒ 模型立刻再查一次。

补 id 不是「运输层的活儿漏到这儿」：**「哪几个 `tool_call` 还缺 `tool_result`」是这一层
自己的执行态**（ADR 0019「为什么不是图编排」），让它可寻址是它自己的责任。
"""

LOST_RESULT = "（这一步没跑完就断了，结果没留下。需要就重新查一次。）"
"""canonical 里**永远补不上**的那种缺口的占位。

`Conversation.pending_calls` 只认尾巴上那一批（它扫到 `USER` 就停），所以「进程死在派发
中间 → 作者回来又说了一句」之后，那个 `tool_call` 就被那句话挡在了 resume 的视野之外，
**再也没有人会去补它**。而 OpenAI 兼容的 wire 上一条没人接住的 `tool_call` 就是 400。

补跑不是选项：结果只能追加到队尾，也就是排在作者那句新话**后面**，那个顺序照样是 400。
所以这里的动作是**在投影里就地配壳**——投影是发出去之前的最后一道关口，它的出参必须
是一份能发的 wire，而不是一份「canonical 长什么样就发什么样」的转写。
"""


class Conversation(BaseModel):
    """一次会话的 canonical 状态。**执行态就这些**（ADR 0019「为什么不是图编排」）。

    `prefix` / `messages` 分成两个字段是边界六的实现：投影永远是 `prefix + 投影(messages)`，
    而 `prefix` 的校验器拒收带章号的消息——**「唯一稳定的那块被夹在中间」这个错误在这儿
    构造不出来**，不是靠记得把它排在前面。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: tuple[AgentMessage, ...] = ()
    """稳定前缀。**只许放跨章不变的东西**（文风、身份）。判据只有一条：换一章会不会变。"""

    messages: tuple[AgentMessage, ...] = ()

    @model_validator(mode="after")
    def _prefix_holds_nothing_that_moves(self) -> Conversation:
        for message in self.prefix:
            if message.chapter is not None:
                raise ValueError(
                    "稳定前缀里出现了绑章号的消息——它逐章变，缓存它就是把一条过期的禁令"
                    "钉死在 context 里（ADR 0019 边界六）。"
                )
            if message.role is not Role.SYSTEM:
                raise ValueError("稳定前缀只放 system 消息：别的角色都会随对话变。")
            if message.revokes_seq is not None:
                # 撤销的坐标是**历史**的下标（`AgentMessage.revokes_seq`），前缀里放一条
                # 就是拿一个坐标系去指另一个坐标系——它指到的永远是别的消息。
                raise ValueError("稳定前缀里放不了撤销记录：它指的是历史里的一条。")
        return self

    @property
    def pending_calls(self) -> tuple[ToolCall, ...]:
        """**还缺 `tool_result` 的那几个 `tool_call`** —— 线性 loop 的全部「未完成」状态。

        非空只有一个来法：进程在模型调用和派发之间死掉了。正常停止一律给每个未执行的
        调用补一条 `UNRUN_CALL`，所以从界面上停下来的会话在这儿是空的。
        """
        answered = {m.tool_call_id for m in self.messages if m.role is Role.TOOL}
        for message in reversed(self.messages):
            if message.role is Role.ASSISTANT and message.tool_calls:
                return tuple(call for call in message.tool_calls if call.id not in answered)
            if message.role is Role.USER:
                break
        return ()

    def extended(self, *added: AgentMessage) -> Conversation:
        return self.model_copy(update={"messages": (*self.messages, *added)})

    def with_author(self, said: str) -> Conversation:
        """作者说了一句。**它永远不绑章号**（见 `AgentMessage.chapter`）。"""
        text = said.strip()
        if not text:
            raise ValueError("作者这一轮什么都没说：空话进不了对话历史。")
        return self.extended(AgentMessage(role=Role.USER, content=text))


AGENT_SYSTEM_PROMPT = """你是一位中文长篇小说作者的写作搭档，坐在他的稿子旁边。

- 你手上的工具是**这本书自己的索引**：先用便宜的那几层定位（目录 / 人物同框章 / 摘要区间），
  确定了再去读整章正文。
- **不许说破的东西是逐章算的。** 你上一轮查到的清单对另一章可能已经过期了——要为哪一章
  写东西，就为哪一章重新查一次。起草工具只收章号，约束由后端当场重算，你传不进去。
- 工具返回里带「还没查」「瞎着」「裁掉了多少条」的话，一律照它说的理解：0 不等于没有。
- **起草和存进书是两步。** 方向清楚就写一稿、接着存进去，不用问他；方向不清楚就一次写
  几稿、先别存，把每一稿的自述摆给他挑。稿子的编号是给工具用的，跟他说话时说「第几稿」。
- 说话对着作者，用中文，不要把工具名和参数念给他听。"""
"""稳定前缀的正文。**跨章不变**，所以它能进前缀（边界六那张表的第一行）。

里面**一条 `must_not_reveal` 都没有，也永远不许有**：一份被缓存住的禁说清单就是一条被钉死
在 context 里的过期约束，而它过期的方向是 fail-open 的最坏那侧（到第 90 章更多人已经知道，
清单更短）。
"""


def start_conversation(write_rule: str | None = None) -> Conversation:
    """开一段新会话。`write_rule` 是作者定下的**一条一直挂着的要求**，跨章不变才配进前缀。

    ⚠️ **它比名字大。** 2026-08-13 之前这一位叫 `house_style`（文风），
    可它是个自由文本入口：真的文风、格式规则（「每段用叠词开头」）、
    「主角不许叫小名」……什么都塞得进去。**名字比东西窄，用的人会低估它**，
    于是没人想到往里放别的，也没人意识到它有多大（下面那段「它拦不住什么」
    说的就是它能有多大）。
    """
    prefix = [AgentMessage(role=Role.SYSTEM, content=AGENT_SYSTEM_PROMPT)]
    if write_rule and write_rule.strip():
        prefix.append(AgentMessage(role=Role.SYSTEM, content=write_rule.strip()))
    return Conversation(prefix=tuple(prefix))


def write_rule_of(conversation: Conversation) -> str:
    """这段对话的那条一直挂着的要求。**没有就是空串。**

    判据是「前缀里第一条 SYSTEM 消息**之后**的那条」——前缀由
    `start_conversation` 造，第一条永远是引擎自己的系统提示词。
    **这条判据必须和那个函数长在一起**：分开写两处，改一处就会静默错位，
    而错位的症状是把引擎的系统提示词当成作者的要求送去起草。
    """
    extra = [m for m in conversation.prefix if m.role is Role.SYSTEM][1:]
    return extra[0].content.strip() if extra else ""


# ══════════════════════════════════════════════════════════════════════════
# 投影：`project(canonical, chapter) -> messages`（边界五）
# ══════════════════════════════════════════════════════════════════════════


def _wire(message: AgentMessage) -> dict[str, Any]:
    """canonical → OpenAI 兼容的一条。**元数据一个字都不发出去。**"""
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    out: dict[str, Any] = {"role": str(message.role), "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    return out


def _json_units(payload: Any) -> int:
    """序列化之后有多少字。**量的是序列化后的样子**（同 `index.py::_cost`）：
    JSON 的键名和引号也占 token，条目多的时候那部分不小。"""
    return count_units(json.dumps(payload, ensure_ascii=False), _LANGUAGE)


def _cost(message: AgentMessage) -> int:
    """这条消息进 prompt 要花多少字。"""
    return _json_units(_wire(message))


def payload_units(
    messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
) -> int:
    """这一次**真正发出去的那份 payload** 有多少字（`count_units` 口径）。

    ── 为什么要有这一个函数（ADR 0023「前置：先把账算对」）────────────────

    > 预算量出来的数，和**真正发出去那份 payload 的实际大小**，误差必须在一个写死的
    > 范围内。

    以前的预算只量 `messages`，而每一次调用还要带上整张工具表的声明（近 4,000 字，
    **相对默认预算约 23%**）。那是一个方向**偏松**的系统性低估：以为发了 17,000 字，
    实际发了 21,000。在这之上算「剪枝省了多少」，省下来的每一个数都是估的。

    **它不是全部的 payload**：真正的 wire 上还有一层信封（`model` / `max_tokens` /
    `stream` / reasoning 那几个字段，见 `draft/provider.py::_wire_kwargs`）。那一层
    由 `ProviderConfig` + `CallPlan` 决定，而这一层**不认识它们**（`ModelPort` 的
    存在就是为了不认识）。所以口径是「量得到的那两块」，差额由
    `PAYLOAD_ENVELOPE_UNITS` 兜住，并且是**单向**的：信封只会让实际更大，不会更小。
    """
    return _json_units({"messages": list(messages), "tools": list(tools)})


PAYLOAD_ENVELOPE_UNITS: Final = 256
"""`payload_units()` 量不到的那一层信封，最多多少字。**这是那条断言的容差，写死的。**

实测（`tests/test_agent_loop_budget.py` 逐条跑）：能力表里那五条路由 + 一个没登记的
本地端点，信封在 **53–129 字**之间（最大的是 Anthropic 兼容那档的
`extra_body.thinking` / `output_config`）。256 是留了一倍余量的整数。

**它红的时候不许直接把数字调大**：信封变大只有两种来法——换了个名字特别长的模型
（无害），或者 `_wire_kwargs` 开始往请求里塞新东西（**那才是要看的**，因为那意味着
每一次调用都在多付一笔没人记账的钱）。
"""

_EMPTY_PAYLOAD_UNITS: Final = payload_units((), ())
"""两个空数组时的信封（`{"messages": [], "tools": []}`）。**算出来的，不是抄的。**"""


def _array_units(costs: Sequence[int]) -> int:
    """一个 JSON 数组里，元素本身 + 分隔它们的那些逗号。**方括号已经算在信封里。**"""
    return sum(costs) + max(0, len(costs) - 1)


def _measured_units(message_costs: Sequence[int], tool_costs: Sequence[int]) -> int:
    """`payload_units()` 的增量版：逐条的字数已经算好时用它。

    两者**必须逐字节同解**（`tests/test_agent_loop_budget.py` 有一条对拷断言钉着）——
    剪枝的每一步用这一个、最后报出去的用另一个的话，就会出现「剪完了但还是喊装不下」
    那种停不下来的形态。
    """
    return _EMPTY_PAYLOAD_UNITS + _array_units(message_costs) + _array_units(tool_costs)


def tool_declaration_units(tools: Sequence[dict[str, Any]] | None = None) -> int:
    """整张工具表的声明在一次 payload 里占多少字。**剪枝一个字都动不了它，它是地板。**

    有名字的地板才管得住：以前它不在账上，于是「预算 17,000」实际发出去 21,000
    （ADR 0023「前置」）。今天它进 `Projection.tool_units`，于是「这一轮的上下文里
    有多少是雷打不动的固定开销」是一个报得出来的数，不是一个要去读源码才知道的事。
    """
    declarations = tool_declarations() if tools is None else list(tools)
    return _array_units([_json_units(declaration) for declaration in declarations])


def _drop_calls(messages: list[AgentMessage], call_ids: set[str]) -> list[AgentMessage]:
    """把这几个 `tool_call` 连同它们的返回一起摘掉，**并保证壳配得齐**。

    一条 assistant 消息的 `tool_calls` 被摘空、又没有正文时整条丢掉（留一条空 assistant
    只是多一行噪声）；还有正文时留下正文——那是「agent 中间推理」，它排在剪枝的第三档，
    不该被第一、二档顺手带走。
    """
    out: list[AgentMessage] = []
    for message in messages:
        if message.role is Role.TOOL and message.tool_call_id in call_ids:
            continue
        if message.role is Role.ASSISTANT and message.tool_calls:
            kept = tuple(call for call in message.tool_calls if call.id not in call_ids)
            if len(kept) != len(message.tool_calls):
                if not kept and not message.content.strip():
                    continue
                message = message.model_copy(update={"tool_calls": kept})
        out.append(message)
    return out


def _fill_lost_shells(messages: list[AgentMessage]) -> tuple[list[AgentMessage], int]:
    """给 canonical 里没人接住的 `tool_call` 就地配一个壳（见 `LOST_RESULT`）。

    **这不是重复 `_drop_calls` 的工作**：那一个管的是「这一次投影自己丢掉的」，这一个管的是
    「canonical 里本来就缺的」。后者 `pending_calls` 补不上（它扫到 `USER` 就停），而两者
    漏掉任何一个的症状是同一个——发出去 400，而且是在作者按下发送之后才发生。

    壳标成 `pruned`：它本来就不是一次工具返回，第一档不该再去「剪」它。
    """
    answered = {m.tool_call_id for m in messages if m.role is Role.TOOL}
    out: list[AgentMessage] = []
    filled = 0
    for message in messages:
        out.append(message)
        if message.role is not Role.ASSISTANT:
            continue
        for call in message.tool_calls:
            if call.id in answered:
                continue
            out.append(
                AgentMessage(
                    role=Role.TOOL, content=LOST_RESULT, tool_call_id=call.id, pruned=True
                )
            )
            filled += 1
    return out, filled


class Projection(BaseModel):
    """一次投影的结果 + **它裁了什么**。

    ADR 只写了 `project(canonical, chapter) -> messages`，这儿多带一份回执，理由和
    `index.py` 那三条纪律的第一条同源：**裁了什么必须说出来**。静默截断读起来像
    「全给了」，而下游（3.4 的会话面板）会据此告诉作者一句错话。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int | None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    """**本仓少见的非 Pydantic 出参**，理由同 `assemble()`：它要原样进
    `provider.complete(messages=...)` 的线上格式，包一层只会在调用点再拆一次。"""

    off_chapter: int = 0
    """因为绑在**更后面**的章上而被丢掉的工具返回条数（边界五真正在防的那一档）。"""

    stale_manuscript: int = 0
    """因为**作者已经把那一章改了**而被换成占位的正文条数（见 `STALE_MANUSCRIPT`）。

    不为零 = 这一轮里模型手上的正文快照和磁盘对不上，而磁盘那份才是答案（ADR 0007）。
    它要报出来的理由和 `off_chapter` 同一条：**裁了什么必须说出来**。
    """

    filled_shells: int = 0
    """canonical 里没人接住、这一次就地配了壳的 `tool_call` 条数（见 `LOST_RESULT`）。
    不为零 = 有一次派发断在半路上，而作者的下一句话把它挡在了 resume 的视野之外。"""

    expired_rules: int = 0
    """因为**切到了另一章**（或者根本没有章号坐标）而失效的作者规矩条数（ADR 0023 决策二）。

    不为零 = 有几条偏好这一轮不再生效了。**方向和上面那几个字段是反的**：那几个是
    「这条还在，只是我们没发」，这一条是「它到期了」——而偏好到期是**设计**，不是损失。
    """

    stubbed_results: int = 0
    dropped_calls: int = 0
    dropped_reasoning: int = 0
    over_budget: bool = False
    """剪到只剩作者说的话，仍然装不下。**这一档不砍作者的话，由调用方停下来。**"""

    budget_units: int = 0
    """这一次的预算（整份 payload 的上限）。"""

    tool_units: int = 0
    """其中工具声明占掉的那部分。**它是一块固定的地板**——剪枝一个字都动不了它，
    而它以前根本不在账上（ADR 0023「前置」点名的那 23%）。"""

    payload_units: int = 0
    """这一份**真正发出去**的 payload 有多少字（含工具声明，见模块级同名函数）。

    `over_budget` 就是 `payload_units > budget_units`，**没有第二个口径**。
    """


def project(
    conversation: Conversation,
    chapter: int | None,
    *,
    budget_units: int,
    stale_calls: frozenset[str] = frozenset(),
    tools: Sequence[dict[str, Any]] | None = None,
) -> Projection:
    """canonical → 模型这一次看得见的那份（ADR 0019 边界五）。

    Args:
        chapter: **第几章视角**。`None` = 不知道作者在写第几章 ⇒ 工具返回不按章号过滤，
            而作者的规矩**一条都不留**（见下面那条诚实说明和「方向是反的」那一节）。
            它决定「取什么」。
        budget_units: **整份 payload** 最多多少字——含工具声明，见 `payload_units()`。
            它决定「取多少」。
        stale_calls: 手里那份正文**已经和磁盘对不上**的那几个 `tool_call` 的 id
            （判据在 `tools.outdated_manuscript`，由 `run_turn` 算好交进来——
            这个函数是纯的，不碰磁盘）。它决定「哪一份不许再发出去」。
        tools: 这一次要一起发出去的工具声明。`None` = 整张表（`tool_declarations()`），
            那**就是**产品行为——工具表是权限边界，loop 从不发半张表。
            `run_turn` 显式传它自己那一份，好让量的和发的是同一个对象。

    ── 四段，顺序不能反 ──────────────────────────────────────────────

    **零、把不生效的规矩和撤销记录拿掉**（`expired_rules`，ADR 0023 决策二）：作者定下的
    规矩默认**只管当前这一章**，切章自动失效；他取消掉的那些也在这一步没的。
    **它必须排在所有删减之前**：撤销的坐标是 canonical 的下标
    （`AgentMessage.revokes_seq`），下一档会从中间摘掉消息，摘完那个下标就指到别的
    消息上了。**判据是 `!=`，而且方向和下一档是反的**：

    | | 拿不准时 | 为什么 |
    |---|---|---|
    | `must_not_reveal`（下一档） | **留着**（`>` 只丢往后的） | 说破了收不回来 |
    | **作者的偏好**（这一档） | **放掉**（`!=`，换一章就没了） | 留着 = 第 200 章写不出打戏，而**作者不知道为什么** |

    所以 `chapter is None` 时这一档**清空**，而下一档**全留**——同一个「不知道第几章」，
    两个相反的动作，因为两边猜错的代价不对称。判据全在 `rules.surviving_rule_indices`，
    这儿不写第二份。

    **一、按章号取**（`off_chapter`）：绑在**更后面**的章上的工具返回不进这一份。
    作者会从第 90 章回头改第 40 章，那时「最近」是错的坐标——对话的近端讲的是第 90 章，
    而第 40 章的 `must_not_reveal` 是第 90 章那份的**超集**。让那份更短的清单留在
    context 里，模型就会以为「只有这两条不能说」。

    **判据是 `>` 不是 `!=`，因为这个泄漏是有方向的。** ADR 0019 边界二把方向写死了：

    ```
    ch40 的 must_not_reveal ⊇ ch90 的     ← 到第 90 章，更多人已经知道了
    ```

    往后的章清单**更短**（fail-open 的最坏那侧，必须丢）；往前的章清单是**超集**
    （fail-closed，留着最多是多禁一条）。`!=` 把这两侧当成一回事，于是**坐标一旦不是
    模型正在写的那一章，它就反过来把正确的那份长清单删掉、把过期的短清单留下**——
    亲手造出这条 ADR 点名的那个故障。而坐标今天来自 `ToolContext.working_chapter`
    （作者的光标），模型却可以为任意一章查约束、起草（`ports.py` 写着它「只标不挡」），
    两者不一致是**设计允许**的常态，不是异常。`>` 让坐标错的时候错在安全那一侧。

    代价说清楚：**往前的章那些返回会留着**，其中 `character_state` 这类连续性事实会
    过期（人已经死了、地方已经换了）。那是「话说错了」，作者一眼看得见；而丢掉一条
    `must_not_reveal` 是「稿子崩了」，没有任何东西会报错。这条 ADR 的全部重量都在
    「它错的时候不会报错」上，所以换法是往看得见的那一侧换。

    **二、把过期的正文换掉**（`stale_manuscript`）：作者在编辑器里改过的那一章，
    会话里那份快照就不再是「第 N 章是什么」的答案了——而它是**被发出去的那一份**，
    于是模型会拿一段读起来完全正常的旧正文回答他（ADR 0019 边界三 / ADR 0007）。
    canonical 一个字不动，只在这一份投影里换成 `STALE_MANUSCRIPT`。

    **三、按预算剪**（顺序写死）：老 `tool_result` → 老 `tool_call` → agent 中间推理
    → 最后才碰作者说的话。这一层的最后一档是**不碰**：剪完仍然装不下就把 `over_budget`
    立起来，让调用方停下来说人话，而不是悄悄把作者说过的话吃掉。

    ── 三条诚实说明 ────────────────────────────────────────────────

    - **`chapter is None` 时不过滤。** 那意味着这一层没有可用的「第 N 章视角」，
      按任何一章去筛都是替作者猜。它不是安全的默认值，它是**没接线**的默认值——
      所以 `working_chapter` 必须真的接上（`ToolContext`），空着的时候索引层已经在
      每一份返回里明说自己没标（`index._future_note`）。
    - **只筛工具返回，不筛 agent 的推理。** 模型上一轮基于第 90 章的认知说过的话仍然
      留在历史里。ADR 0019 边界二自己把这条列成**接受**的残余代价：「推理被污染，
      但写出来的正文受的是当前章的约束」——因为起草工具只收章号。判它需要读懂那句话
      是什么意思，而那是语义判断（ADR 0005）。
    - **不绑章号的工具返回照旧留着。** 索引那四层里有三层没有 `chapter` 参数
      （目录 / 人物同框轴 / 摘要区间），它们不带禁说清单，标的是 `future` 而不是禁令。
      这一档跟着上面那条残余代价一起被接受。
    """
    # **函数内 import 是为了断开一条环**：`rules.py` 要用这个文件里的 `AgentMessage` /
    # `Conversation` / `Role`（canonical 的形状住在这儿），所以它在模块级 import 本文件；
    # 本文件反过来在模块级 import 它就是一个真的循环。同 `agent/model.py` 里那处
    # `from ..draft.provider import _build_client`——这个包已经有这个先例。
    from .rules import expired_rule_count, is_revocation, is_rule, surviving_rule_indices

    kept = list(conversation.messages)

    # 第零档排在**最前面**（在 ADR 那张表里它属于「取什么」，和下面按章号取同一档）。
    # **顺序不是风格问题**：撤销的坐标是 canonical 的下标（`AgentMessage.revokes_seq`），
    # 而下面那一档会从中间摘掉消息——摘完再来解释那个下标，它指的就是别的消息了。
    # 整条判据在 `rules.py`，这儿只负责把不生效的那几条拿掉并报个数。
    live_rules = surviving_rule_indices(kept, chapter)
    expired_rules = 0
    if any(is_rule(message) for message in kept):
        # **报的是「有几条规矩不再生效」，不是「丢了几条消息」**：同一条被记了两遍、
        # 这儿只留最后一遍是**去重**，不是过期（见 `rules.expired_rule_count`）。
        expired_rules = expired_rule_count(kept, live_rules)
    kept = [
        message
        for index, message in enumerate(kept)
        # 撤销记录一条都不发出去：它的意义全在结构槽上，正文是空的，而模型该看到的
        # 结果是「那条规矩从来没被说过」——发一条空的 system 消息只是白花钱。
        if not is_revocation(message) and (not is_rule(message) or index in live_rules)
    ]

    off_chapter = 0
    if chapter is not None:
        stale = {
            message.tool_call_id
            for message in kept
            if message.role is Role.TOOL
            and message.chapter is not None
            and message.chapter > chapter
        }
        if stale:
            off_chapter = len(stale)
            kept = _drop_calls(kept, stale)

    # 过期的正文排在预算之前：占位比整章正文短得多，先换掉，第一、二档才知道自己
    # 到底还差多少字（反过来的话预算会照着一份已经不该发出去的正文去剪别的东西）。
    stale_manuscript = 0
    if stale_calls:
        for index, message in enumerate(kept):
            if message.role is not Role.TOOL or message.pruned:
                continue
            if message.tool_call_id not in stale_calls:
                continue
            kept[index] = message.model_copy(
                update={"content": STALE_MANUSCRIPT, "pruned": True}
            )
            stale_manuscript += 1

    # 配壳排在预算之前：壳也是要发出去的字，让它跟别的消息一起被算、一起被剪。
    kept, filled = _fill_lost_shells(kept)

    declarations = tool_declarations() if tools is None else list(tools)
    tool_costs = [_json_units(declaration) for declaration in declarations]
    tool_side = _array_units(tool_costs)  # == `tool_declaration_units(declarations)`
    prefix_costs = [_cost(message) for message in conversation.prefix]
    stubbed = dropped_calls = dropped_reasoning = 0

    def sent() -> int:
        """**这一刻发出去的话，那份 payload 有多大。** 剪枝的每一步问的都是这一个数。

        以前这儿问的是「对话有多少字」，而工具声明也要发出去——那近 4,000 字一直不在
        账上（ADR 0023「前置」）。现在预算和实际是同一个口径，所以
        `over_budget` 和 `payload_units` 不可能各说各的。
        """
        return _measured_units(
            [*prefix_costs, *(_cost(message) for message in kept)], tool_costs
        )

    # 第一档：老 `tool_result` —— 删内容留壳。**壳不能删**，wire 上它必须接住那个 tool_call。
    if sent() > budget_units:
        for index, message in enumerate(kept):
            if sent() <= budget_units:
                break
            if message.role is not Role.TOOL or message.pruned:
                continue
            stub = message.model_copy(update={"content": PRUNED_RESULT, "pruned": True})
            # **占位比原返回还长的时候不换。** 换了是三重损失：内容没了、prompt 反而更大、
            # 而且它把第二档往下压（本来剪两条调用够用，现在得剪四条）。
            # 短返回不需要第一档救——第二档会把它连壳带调用一起拿掉，那才是它的档位。
            if _cost(stub) >= _cost(message):
                continue
            kept[index] = stub
            stubbed += 1

    # 第二档：老 `tool_call` —— 壳和它的调用**成对**拿掉。
    if sent() > budget_units:
        for message in list(kept):
            if sent() <= budget_units:
                break
            if message.role is not Role.ASSISTANT or not message.tool_calls:
                continue
            ids = {call.id for call in message.tool_calls}
            kept = _drop_calls(kept, ids)
            dropped_calls += len(ids)

    # 第三档：agent 的中间推理（有正文、没有工具调用的 assistant 消息）。
    if sent() > budget_units:
        for message in list(kept):
            if sent() <= budget_units:
                break
            if message.role is not Role.ASSISTANT or message.tool_calls:
                continue
            kept.remove(message)
            dropped_reasoning += 1

    # 第四档不存在：**作者说的话不砍**。**规矩也不在这条链上**——它按章号过期，不按预算剪
    # （ADR 0023 那张表：小、跨轮有效、丢了最气人）。
    return Projection(
        chapter=chapter,
        messages=[_wire(m) for m in (*conversation.prefix, *kept)],
        off_chapter=off_chapter,
        stale_manuscript=stale_manuscript,
        filled_shells=filled,
        expired_rules=expired_rules,
        stubbed_results=stubbed,
        dropped_calls=dropped_calls,
        dropped_reasoning=dropped_reasoning,
        over_budget=sent() > budget_units,
        budget_units=budget_units,
        tool_units=tool_side,
        payload_units=sent(),
    )


# ══════════════════════════════════════════════════════════════════════════
# 停止条件
# ══════════════════════════════════════════════════════════════════════════


class StopReason(StrEnum):
    """这一轮为什么停。**每一种都要说得出自己是为什么停的。**

    取值是 snake_case，**故意的**：它是机器码，一旦有人把它原样摆上屏，
    `frontend/src/test/screenGuard.ts` 和 `tests/test_wording_guard.py` 的形状判据会当场咬住它。
    给作者看的那句话在 `stop_wording()`，那才是唯一的措辞出处。
    """

    DONE = "done"
    ASKED_AUTHOR = "asked_author"
    STEP_LIMIT = "step_limit"
    COST_LIMIT = "cost_limit"
    BATCH_TOO_WIDE = "batch_too_wide"
    AUTHOR_STOPPED = "author_stopped"
    REPEATED_CALL = "repeated_call"
    NO_OUTPUT = "no_output"
    TOOL_STUCK = "tool_stuck"
    CONTEXT_FULL = "context_full"
    MODEL_UNREACHABLE = "model_unreachable"


_STOP_WORDING: Final[dict[StopReason, str]] = {
    StopReason.DONE: "说完了。",
    StopReason.ASKED_AUTHOR: "它有件事拿不准，问了你一句，正等着你答。",
    StopReason.STEP_LIMIT: (
        "这一轮它来回查了太多次，先停下来了。上面查到的东西还在，"
        "你可以看一眼，再告诉它接下来往哪儿走。"
    ),
    StopReason.COST_LIMIT: (
        "这一轮用掉的额度到顶了，先停下来，免得一直烧下去。要接着往下就再说一句。"
    ),
    StopReason.BATCH_TOO_WIDE: (
        "它一口气要做的事太多了，先拦下来。挑一件最要紧的告诉它，或者把范围说小一点。"
    ),
    StopReason.AUTHOR_STOPPED: "按你的意思停下了。已经查到的东西留着。",
    StopReason.REPEATED_CALL: (
        "它在反复查同一件事，问不出新东西了，先停下来。换个说法，"
        "或者直接告诉它你想要的是什么。"
    ),
    StopReason.NO_OUTPUT: "它这一次什么都没说，也没去查任何东西。再说一遍试试。",
    StopReason.TOOL_STUCK: (
        "同一件事连着查了几次都没查成，先停下来。多半是称呼对不上，"
        "或者那一章还没有正文。"
    ),
    StopReason.CONTEXT_FULL: (
        "这段对话说得太长，装不下了。开一段新的对话，或者把要问的说得短一点——"
        "**你说过的话一句都没被删掉**。"
    ),
    StopReason.MODEL_UNREACHABLE: (
        "联系不上写作模型，这一轮没跑成。等一下再试；一直这样的话去顶栏「AI 设置」看一眼。"
    ),
}


def stop_wording(reason: StopReason) -> str:
    """说给**小说作者**听的那一句。**措辞的唯一出处**，前端不许再翻一遍。

    认不出的不原样回吐（同 `activity._kind_label`）：`StopReason` 是封闭枚举，认不出
    只可能是这张表漏了行，而漏掉的那一行会以 `tool_stuck` 的形态出现在作者的屏幕上。
    """
    return _STOP_WORDING.get(reason, "这一轮先停下来了。")


# ══════════════════════════════════════════════════════════════════════════
# 事件流：一轮不是黑箱（ADR 0024）
# ══════════════════════════════════════════════════════════════════════════


class TurnEventKind(StrEnum):
    """这一轮跑到哪儿了。**取值是机器码**，理由同 `StopReason`：snake_case 一旦被原样
    摆上屏，`frontend/src/test/screenGuard.ts` 和 `tests/test_wording_guard.py` 的形状
    判据会当场咬住它。给作者看的那句话在 `TurnEvent.said_to_author`。
    """

    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    REPLY_TEXT = "reply_text"
    REPLY_DELTA = "reply_delta"
    DRAFT_STARTED = "draft_started"
    DRAFT_DELTA = "draft_delta"
    DRAFT_KEPT = "draft_kept"
    DRAFT_FAILED = "draft_failed"
    ASKED_AUTHOR = "asked_author"
    TURN_STOPPED = "turn_stopped"


class TurnEvent(BaseModel):
    """一轮跑到一半时往外喊的一声（ADR 0024 决策一）。

    ── 它是给谁看的：**只有界面，最终是作者** ────────────────────────────────

    **模型看不见它**——模型只看得见 `project()` 出来的那份 message 数组，事件一条都不在
    里面。所以这一层的措辞标准和 `stop_wording()` 完全一样，而不是「给日志看的」：
    每一条要么带一句**中文的、说给小说作者听的话**（`said_to_author`），要么带一段
    **模型自己写的字**（`text`）。两样都空的事件构造不出来（见校验器）。

    ── 措辞在后端，不在界面（先例：`stop_wording()`）────────────────────────

    界面拿到 `kind` 之后自己翻一遍中文 = 措辞有了第二个出处，而两份迟早漂。所以：

    | 字段 | 谁写的 | 上屏吗 |
    |---|---|---|
    | `said_to_author` | **引擎**（这个类 + `ToolSpec.label` + `stop_wording()`） | 上 |
    | `text` | **模型**（回话的字 / 稿子的字） | 上 |
    | `kind` / `tool` | 机器码 | **一个字都不许上** |

    ── 这里**没有**工具查到了什么（边界一，不可回收）──────────────────────

    ADR 0024 自己把这一条列成最贵的代价：一轮的返回是**投影过的**（工具查到了什么根本
    不上屏，只有一个「查了 3 次」的数），而事件流会把中间过程摊开。所以这个类上
    **没有一个字段装得下 `ToolOutcome.content`**：查完了那一声只有「成没成」和「第几章」。

    同理 `tool` 只可能是工具表里的名字，认不出的一律空——**工具名是模型打进来的字**
    （它会幻想工具名），原样带出去等于给了它一条把任意字符串推上作者屏幕的路。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TurnEventKind

    said_to_author: str = ""
    """说给**小说作者**听的那一句。**措辞的唯一出处就是这个类**，界面不许再翻一遍。"""

    text: str = ""
    """**模型自己写的字**，原样。回话的一片 / 一稿的一片。引擎一个字都不加。"""

    tool: str = ""
    """这一次动的是表里哪一条（机器码）。**认不出的是空的**，见类 docstring。"""

    ok: bool | None = None
    """`tool_finished` 专用：这一次成没成。**为什么没成不在这儿**（那是工具返回）。"""

    chapter: int | None = None
    index: int = 0
    total: int = 0
    """这一批里的第几件 / 一共几件。`total <= 1` = 这一批就一件，界面不用说这半句。"""

    stream: int = 0
    """**同一条字流的片归到一起。** 一批三稿是同时在写的（ADR 0022），三条流的片会交错
    着到达；同一章的三稿连 `chapter` 都一样，没有这个数就没法把它们分开摆。
    `0` = 这一轮只有一条流（回话）。**它不上屏**，它是界面分组用的钥匙。
    """

    ordinal: int = 0
    units: int = 0
    """第几稿 / 多少字（`draft_kept`）。**跟作者说话时说「第几稿」**，不说稿子的编号。"""

    reason: StopReason | None = None
    asked: AuthorQuestion | None = None
    """它停下来问作者的那一句 + 几个可点的选项（ADR 0024）。"""

    @model_validator(mode="after")
    def _says_something(self) -> TurnEvent:
        if not self.said_to_author and not self.text:
            raise ValueError(
                "这条事件既没有说给作者听的话，也没有模型写的字——界面拿它没法渲染，"
                "而一条渲染不出来的事件只会变成一个转圈的图标（ADR 0024 要治的正是那个）。"
            )
        return self

    # ── 构造口。**措辞全在这几个方法里**，别在别处拼第二句 ────────────────────

    @classmethod
    def tool_started(cls, name: str, *, index: int = 0, total: int = 0) -> TurnEvent:
        label = tool_label(name)
        where = f"（这一批 {total} 件里的第 {index} 件）" if total > 1 else ""
        return cls(
            kind=TurnEventKind.TOOL_STARTED,
            said_to_author=f"正在{label}{where}。",
            tool=name if name in TOOL_NAMES else "",
            index=index,
            total=total,
        )

    @classmethod
    def tool_finished(cls, outcome: ToolOutcome, *, index: int = 0, total: int = 0) -> TurnEvent:
        """**只收成没成和第几章**：`outcome.content` 一个字都不进来（见类 docstring）。

        **章号是在这一声里第一次说得出口的**：开跑那一声只有工具名——参数还是一串没解析过
        的字符串，而解析点只许有一个（`dispatch`，`ToolOutcome.chapter` 的 docstring 写着
        为什么 loop 不许自己再解析一遍）。所以 ADR 0024 举例的那句「正在查第 40 章的约束」
        在开跑那一刻**说不出来**，能说的是「查完了第 40 章的约束」。
        """
        label = tool_label(outcome.name)
        which = "" if outcome.chapter is None else f"（第 {outcome.chapter} 章）"
        said = (
            f"{label}{which}，好了。"
            if outcome.ok
            else f"{label}{which}，这一次没成——它看得见为什么，会自己换个法子。"
        )
        return cls(
            kind=TurnEventKind.TOOL_FINISHED,
            said_to_author=said,
            tool=outcome.name if outcome.name in TOOL_NAMES else "",
            ok=outcome.ok,
            chapter=outcome.chapter,
            index=index,
            total=total,
        )

    @classmethod
    def reply_text(cls, text: str) -> TurnEvent:
        """这一步它说完的那一整段。**流式那一档它和 `reply_delta` 说的是同一段字**
        （界面二选一渲染）——非流式的时候只有这一条，而对话这一档今天多半非流式
        （`agent/model.py` 那段诚实交代）。"""
        return cls(kind=TurnEventKind.REPLY_TEXT, text=text)

    @classmethod
    def reply_delta(cls, text: str) -> TurnEvent:
        return cls(kind=TurnEventKind.REPLY_DELTA, text=text)

    @classmethod
    def draft_started(cls, chapter: int, *, stream: int = 0) -> TurnEvent:
        return cls(
            kind=TurnEventKind.DRAFT_STARTED,
            said_to_author=f"正在写第 {chapter} 章的一稿。",
            chapter=chapter,
            stream=stream,
        )

    @classmethod
    def draft_delta(cls, chapter: int, text: str, *, stream: int = 0) -> TurnEvent:
        return cls(
            kind=TurnEventKind.DRAFT_DELTA, text=text, chapter=chapter, stream=stream
        )

    @classmethod
    def draft_kept(
        cls,
        chapter: int,
        *,
        ordinal: int,
        units: int,
        stream: int = 0,
        stopped: bool = False,
    ) -> TurnEvent:
        """**半截的那一稿不许说成写好了。** 一段断在半句的正文，作者若不知道它是被砍断的，
        会把那个断口当成一种有意的写法（同 `DraftFullText.stopped_reason`）。"""
        said = (
            f"第 {chapter} 章的第 {ordinal} 稿停在这儿了，{units} 字，没写完。"
            if stopped
            else f"第 {chapter} 章的第 {ordinal} 稿写好了，{units} 字。"
        )
        return cls(
            kind=TurnEventKind.DRAFT_KEPT,
            said_to_author=said,
            chapter=chapter,
            ordinal=ordinal,
            units=units,
            stream=stream,
        )

    @classmethod
    def draft_failed(cls, chapter: int, *, stream: int = 0) -> TurnEvent:
        """**开了口的那条流的另一个结局。** 判据和措辞都在 `drafting.ChapterDesk`
        那边（`_open_streams` / `_close_stream`），这儿只提供句式。

        「正在写第 N 章的一稿」是在真的去调模型**之前**喊的，而那次调用有四条失败的路。
        少了这一声，界面上按 `stream` 分的那一格就是一个**永远转下去的图标**——
        ADR 0024 要治的正是那个。上层那条 `tool_finished(ok=False)` 补不上：
        它身上 `stream` 是 0，一批三稿同框时说不出死的是哪一条。

        **这儿没有「为什么没成」**：原因原文里有端点地址和模型名，那是写给维护者的
        （同 `TurnResult.maintainer_note`），而模型看得见它、会自己换个法子。
        """
        return cls(
            kind=TurnEventKind.DRAFT_FAILED,
            said_to_author=f"第 {chapter} 章那一稿没写成，这一条先停在这儿了。",
            chapter=chapter,
            stream=stream,
        )

    @classmethod
    def asked_author(cls, question: AuthorQuestion) -> TurnEvent:
        """它停下来问了一句。**问句本身是模型的字，所以它在 `asked` 里原样带着**，
        引擎那半句只说「有人在等你」。"""
        return cls(
            kind=TurnEventKind.ASKED_AUTHOR,
            said_to_author="它有件事拿不准，问了你一句，正等着你答。",
            asked=question,
        )

    @classmethod
    def stopped(cls, reason: StopReason) -> TurnEvent:
        """**措辞走 `stop_wording()`，这儿不写第二份**——同一种停法在回执上和事件流上
        说两句不一样的话，作者会以为发生了两件事。"""
        return cls(
            kind=TurnEventKind.TURN_STOPPED,
            said_to_author=stop_wording(reason),
            reason=reason,
        )


EventFn = Callable[[TurnEvent], None]
"""**一轮边跑边往外喊**的接线口（ADR 0024 决策一）。形状同 `PersistFn` / `LedgerFn`。

`None` = 不喊。**不传这个参数，这一轮的行为和 2026-08-12 之前逐字节相同**
（`tests/test_agent_events.py` 有一条把两次跑的模型入参、账单原料、落库的历史和
最终回执逐字节对拷的断言钉着它）。

── 引擎不认识传输（ADR 0024 决策二）────────────────────────────────────

这里没有 WebSocket、没有 SSE、没有队列，只有一个函数。要把它接到长连接上是**适配器**
的活，而适配器扔掉之后 `on_event` 空着不叫就是今天的行为——**这条纪律让回退很便宜**，
那也正是它值得守的理由。

── 三件调用方必须知道的事 ──────────────────────────────────────────────

1. **它是同步的，跑在调用它的那条线上。** 所以同一条线上的事件严格有序，
   而**慢的消费者会拖慢这一轮**——引擎不替它缓冲。要缓冲、要丢弃、要合并，
   在适配器里做（那儿才知道对面是一个掉了线的浏览器还是一个终端）。
2. **它可能被别的线程叫。** 一批稿是同时在写的（ADR 0022），那几条流的片从工作线程
   上来。所以实现必须线程安全，而且**跨流的先后顺序不作数**——同一条流内有序，
   靠 `TurnEvent.stream` 分组。
3. **它抛异常会被吞掉。** 见下。

── 为什么发不出去要吞，而记账和落库不吞 ────────────────────────────────

| | 坏掉的后果 | 所以 |
|---|---|---|
| `ledger` / `persist` | 钱花了没记上 / 历史丢了 | **响**（继续跑只会让作者付第二次钱） |
| `on_event` | 一个界面少看见一行字 | **吞** |

作者已经为这一轮付过钱了。一个掉线的浏览器不该把它弄崩——而「弄崩」的形态是
`run_turn` 外面没有 try/except（那是有意的），异常会一路穿到 HTTP 壳变成一次崩溃。
这跟 `_stale_manuscript_calls` 那处吞是同一条理由：**那是让屏幕更好看的东西，不是一道闸。**
"""


def safe_emitter(on_event: EventFn | None) -> Callable[[TurnEvent], None]:
    """把接线口包成「发不出去就算了」的那一个。**三个发事件的地方共用这一份**
    （`run_turn` / `agent/model.py` / `agent/drafting.py`）——各写各的 try/except，
    迟早有一处漏掉，而漏掉的那一处会在浏览器关掉的那一刻让作者的一轮崩掉。
    """
    if on_event is None:
        return _no_events

    def emit(event: TurnEvent) -> None:
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 —— 见 `EventFn`：界面掉线不许拿走这一轮
            return

    return emit


def _no_events(event: TurnEvent) -> None:
    """没人听的时候。**一条分支都不留在热路径上**：不传回调时这个函数什么都不做，
    而「行为逐字节不变」的断言量的就是这条路。"""


@dataclass(frozen=True)
class TurnLimits:
    """**代码这一侧的全部权力。** 循环归模型，但停不下来的循环归代码。"""

    max_steps: int = 8
    """一轮最多几步。一步 = 一次模型调用 + 它这一次请求的那批工具。"""

    max_tokens: int = 300_000
    """一轮最多烧多少 token（入 + 出，累计）。

    **口径只能是 token，不能是钱**：`model_call.cost` 那一列至今没有写入方，BYOK 之下
    引擎不知道作者签的什么单价——那是有意的空，不是漏的。

    这是**成本闸不是能力闸**，同 `MEMORY_UNITS_CEILING`：正常一轮撞不到它（8 步 × 一个
    几万字的 prompt 也就十几万），撞到说明模型陷在什么地方了。要放开就调这一个数。
    """

    max_calls_per_step: int = 6
    """**一步之内最多派发几个工具。** 它拦的是「次数」，不是「钱」——两半都要有。

    形态（3.3 实测）：模型一次发 50 个 `tool_call`，`max_steps` 数的是**模型调用**、
    这一批只算一步；`max_tokens` 那时在批派发**之前**查一次、批内不再查，于是 50 次
    派发全跑完。

    闸放在 loop 而不是放在起草那条路上，理由和「连续失败要数两个数」同源：

    - **起草是注入的**（`ToolContext.drafter`），它自己的闸这一层看不见也报不出来——
      作者会拿到一个 `done`，而钱已经花掉了；
    - **按工具名给闸门 = 一张会漂的表。** 今天只有一个不免费的工具，加第二个的那天
      没有任何东西会提醒谁去补它的闸。批宽度不认名字，所以它罩得住还没写出来的工具。

    **钱那一半在 3.6 补上了**（`run_tool` → `bill`）：起草的回执（`DraftProduct.calls`）
    进 `charged`，于是**每派发完一个就查一次 `max_tokens`**。这条必须有，因为次数闸
    在这儿是宽的：六个 `draft_chapter` 是六稿正文，次数上完全合法。

    默认 6：一次把索引那几层一起查了是正常行为；六个以上是「它想一口气做完一整章的活」，
    那时停下来问作者比替他花钱对。
    """

    parallel_tools: int = 1
    """**一批之内最多几个工具同时跑**（`tools.BatchRunner`）。`1` = 一条一条跑。

    ADR 0022 把落盘从起草里拆出去之后，`draft_chapter` 没有副作用了——一批三稿可以
    同时写，作者盯着转圈的两三分钟压回一分钟。哪几条能并发由 `ToolSpec.concurrent`
    说了算（默认 False，`save_draft` 永远是 False）。

    **默认是 1，而且这个默认不是保守，是正确的**：能不能跨线程用那条 SQLite 连接，
    **只有开它的那一层知道**（`api/deps.py::get_conn` 传的是 `check_same_thread=False`；
    CLI 和测试里那些是默认的 `True`，跨线程碰一下就当场 `ProgrammingError`）。
    所以放开它的动作在装配层（`api/chat.py`），不在这儿。
    """

    repeat_limit: int = 3
    """同一个 `(工具名, 参数)` 在一轮里出现几次算「在原地打转」。"""

    tool_failure_limit: int = 3
    """连续 `ok=False` 几次算「卡住了」。

    **一次失败不算**：那正是 `dispatch` 把四种失败做成正常返回的理由——贴回去让模型
    自己改（换个称呼、把参数重发一遍）。连着三次改不对才是卡住。

    **数两个数，缺一个就有一条绕过去的路**（两个都用这一个阈值）：

    - **按工具名数**：`character_state` 连着三次解析不出人，中间穿插几次成功的
      `book_index` 也不该把它的账清零——那个工具确实卡着。
    - **不分工具名数**：这一条堵的是**模型幻想工具名**。每个瞎编的名字都是一个新的
      key，只按名字数的话「三个不同的错名字」永远是三条 1 次的记录，**闸门一次都不响**。
      而那正是没有工具调用经验的模型最典型的翻车方式。
    """


class Cancellation:
    """作者按下的那个「停」。**线程安全**，因为按下它的是另一条线。

    ── 为什么它是一个对象而不是一个回调 ────────────────────────────────

    要求是「**在一次模型调用进行到一半时真的停下来**」。一次模型调用是一次阻塞的 HTTP
    往返（流式下是一个还在吐字的迭代器），loop 自己在那儿等着——回调形式的打断只能在
    两次调用之间被看到，那就成了「等这一轮跑完才发现该停了」。

    所以它是一个**取消信号**，同时交给两个人看：

    1. **loop** 在每一次模型调用前后、每一次派发之前查它（`stopped`）；
    2. **`ModelPort` 的实现**（3.4 那个适配器）把它带进流式循环里：
       信号一亮就让迭代器抛出去，`provider.complete()` 会把它收敛成 `ProviderError`，
       loop 看见 `stopped` 就知道那不是故障，是作者停的。

    **适配器不理它也不会坏**：那样打断退化成「这一次调用跑完就停」，而不是不停。
    这一层说得出自己退化了，但说不出**适配器**退没退化——那条要在 3.4 那边测。
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def stop(self) -> None:
        self._event.set()

    @property
    def stopped(self) -> bool:
        return self._event.is_set()


# ══════════════════════════════════════════════════════════════════════════
# 记账与模型端口
# ══════════════════════════════════════════════════════════════════════════


# `ModelCallReceipt` / `LedgerFn` 住在 `extract/call_audit.py`（`record_call` 的隔壁）
# 和 `ports.py`。**2026-08-11 从这个文件搬走**，理由是起草侧（`draft/product_draft.py`）
# 也要产出同一种原料，而 `draft/` 不许 import `agent/`（方向反了就成环）。
# 搬走之后这里仍然 re-export，所以 `from ..agent.loop import ModelCallReceipt` 照旧成立。


PersistFn = Callable[[Conversation], None]
"""**一轮跑到一半时把已经长出来的消息落库**的接线口。持有 conn 的那一层实现它。

── 没有它，`pending_calls` 在产品里永远是空的 ──────────────────────────────

ADR 0019 之所以敢不上 LangGraph，全部理由是「线性 loop 的执行态足够简单，
resume = 看尾巴、补跑缺的、继续」。而**「看尾巴」要求尾巴真的在库里**：
一轮只在返回之后整批写库的话，进程死在中途 = 这一轮一条都没落盘 ⇒
`Conversation.pending_calls` 恒为空 ⇒ resume 无事可补，作者重开之后看到的是
「我说了一句话，然后什么都没发生」，而那几次模型调用的钱**已经在账上了**
（`ledger` 是当场 commit 的）。实测形态：批派发进行中时库里只有作者那一条。

**这不是 LangGraph 那种 checkpoint blob**（那份文件头明确拒绝了「跑到哪一步」这样一列）：
它落的还是同一串 message，只是不再等到最后才落。执行态仍然只有一份，仍然是
「一串 message + 哪几个 `tool_call` 还缺 `tool_result`」。

`None` = 不落盘（纯内存跑一轮，测试和 CLI 用）。**产品那条路上必须传**。
"""


class ModelPort(Protocol):
    """一次模型调用。**loop 不认识 `ProviderConfig` / `CallPlan`，那是适配器闭包里的事。**

    `tools` 由 loop 给（表就是权限边界，它不该由调用方替换）；`cancel` 也由 loop 给
    （见 `Cancellation`）。适配器负责把这两样接到 `draft.provider.complete()` 上。
    """

    def __call__(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]],
        cancel: Cancellation,
    ) -> CompletionResult: ...


def _estimate_tokens(text: str) -> int:
    """没有 usage 时按字数倒推的 token 数。**只给闸门用，绝不进账。**

    换算走 `TOKENS_PER_UNIT`（全库唯一那一处字→token 的定义，中文往贵了算）。
    对 JSON 那种一半是 ASCII 的东西它偏大，也就是偏紧——**方向是对的**：
    一个「对方不报数就失效」的成本闸等于没有闸，而早停的代价只是作者再说一句话。

    **「不报数」要按最宽的那个意思理解**：不报、只报一半、报一个不可能的零（入方向的
    prompt token 不可能是 0，这一层从来不发空 prompt）。少认一种，闸门就多一条被供应商
    静默关掉的路——而供应商是谁由作者的 `NH_LLM_BASE_URL` 决定，这一层管不着。
    """
    return count_units(text, _LANGUAGE) * TOKENS_PER_UNIT


class TurnResult(BaseModel):
    """一轮的结果。**`conversation` 是新的 canonical**，调用方拿它去存（3.4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation: Conversation
    reason: StopReason
    said_to_author: str
    """给作者看的那一句（`stop_wording()`）。**这一层的产品语言只有这一个出口。**"""

    reply: str = ""
    """模型最后说的正文。停在半路时可能是空的。"""

    steps: int = 0
    tool_calls: int = 0

    tokens_reported: int = 0
    """供应商真的报出来的 token 之和（入 + 出）。**账上那些数的和。**"""

    calls_without_usage: int = 0
    """有几次调用**没量准**。它不为零时上面那个数是**低估**，界面上不许把它当全部。

    「没量准」不等于「一个数都没报」，三种都算：一个数都没报 / **只报了一半**（中转在
    流式下常见：入有数、出没有）/ **入方向报了个 0**（本地端点给一份恒 0 的 usage）。
    照「两个都是 `None` 才算」去判的话，后两种会被记成量准了的——于是界面上那个数是
    低估、却看起来像全部，而这正是底栏花销汇总那个洞的形状。
    """

    tokens_charged: int = 0
    """闸门实际用的口径：量准了的用报的，**没量准的取「报的」和「估的」里更大的那个**。
    **它不进账**（见 `_estimate_tokens`）。"""

    asked: AuthorQuestion | None = None
    """它停下来问作者的那一句 + 几个可点的选项（ADR 0024）。

    **非空 ⇔ `reason is StopReason.ASKED_AUTHOR`。** 这一轮的收场只是「说完了」的一种：
    在对话里，停下来问就等于这一轮说完了——作者答一句，下一轮接着跑，
    挂起/恢复那套机制一行都不用写（ADR 0024「为什么不需要 interrupt/resume」）。

    **它必须在这儿，不能只在事件流里。** 事件是「跑的过程」，而作者可能是在这一轮
    结束之后才打开那段对话（换台机器、刷新页面、三个月后回来）——那时唯一还说得出
    「它当时问了你什么」的是这个字段和会话历史。
    """

    projection: Projection | None = None
    """最后一次投影的回执（裁了什么）。`None` = 一次都没投影成（第一步就停了）。"""

    maintainer_note: str = ""
    """写给**维护者**的诊断，`ProviderError` 的原文（里面有端点地址和模型名）。

    **不许上屏。** 同 `ExtractionRunError.message`——那一条有
    `tests/test_wording_guard.py::test_the_maintainers_english_diagnosis_never_reaches_the_screen`
    钉着，这一条的同款断言在 `tests/test_agent_loop.py`。
    """


def _prompt_digest(
    messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
) -> tuple[bytes, str]:
    """账上那一行的 `in_artifact` / `prompt_hash`。

    **工具声明一起进哈希**：同一段对话配不同的工具表是两次不同的调用，
    而事后能回答「当时发出去的是什么」的只有这个哈希（原文不落库）。
    """
    encoded = json.dumps(
        {"messages": list(messages), "tools": list(tools)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded, sha256(encoded).hexdigest()


def _outcome_message(outcome: ToolOutcome) -> AgentMessage:
    return AgentMessage(
        role=Role.TOOL,
        content=outcome.content,
        tool_call_id=outcome.call_id,
        chapter=outcome.chapter,
    )


def _unrun(calls: Sequence[ToolCall]) -> list[AgentMessage]:
    """给没跑的那几个调用配壳（见 `UNRUN_CALL`）。"""
    return [
        AgentMessage(role=Role.TOOL, content=UNRUN_CALL, tool_call_id=call.id)
        for call in calls
    ]


def _stale_manuscript_calls(
    conversation: Conversation, context: ToolContext
) -> frozenset[str]:
    """这段历史里，**手上那份正文已经和磁盘对不上**的那几个 `tool_call`（边界三）。

    一轮之内只算一次：这一轮**新查回来的**正文按定义就是当前的，而作者在另一个窗口里
    改稿这件事，下一轮开头会重新算。

    **算它坏掉不许把这一轮带走。** 它是一个「让上下文更准」的东西，不是一道闸——
    磁盘读不了、编码坏了、文件在读的中途被换掉，作者拿到的应该还是他要的那次对话，
    而不是一句「联系不上写作模型」（那还会把他支去改设置，一个指向别处的说法）。
    这跟 `dispatch` 外面不许有 try/except **不矛盾**：那儿吞掉的会是工具的失败
    （模型要看见它、要据此改），这儿吞掉的是一次纯优化的失手。
    """
    stale: set[str] = set()
    for message in conversation.messages:
        if message.role is not Role.TOOL or message.pruned or not message.tool_call_id:
            continue
        try:
            if outdated_manuscript(message.content, context):
                stale.add(message.tool_call_id)
        except Exception:  # noqa: BLE001 —— 见 docstring：优化失手不许拿走这一轮
            continue
    return frozenset(stale)


def _taken_ids(conversation: Conversation) -> set[str]:
    """这段对话里已经用掉的 `tool_call` id。空串不算 —— 它是「端点没给」，不是一个身份。"""
    used = {call.id for message in conversation.messages for call in message.tool_calls}
    used |= {message.tool_call_id for message in conversation.messages}
    used.discard("")
    return used


def _addressable(calls: Sequence[ToolCall], taken: set[str]) -> tuple[ToolCall, ...]:
    """给这一批调用每人一个**在这段对话里唯一**的 id（见 `_SYNTHETIC_CALL_ID`）。

    只补两种：**没有 id 的**，和**和已经用过的撞了的**。端点给了一个自己的唯一 id 时
    原样留着——那样账上、界面上、供应商日志里说的是同一件事。

    `taken` **就地更新**，所以同一轮里后面那几批也躲得开前面用掉的号。
    """
    out: list[ToolCall] = []
    counter = 0
    for call in calls:
        call_id = call.id
        if not call_id or call_id in taken:
            counter += 1
            while (call_id := _SYNTHETIC_CALL_ID.format(len(taken) + counter)) in taken:
                counter += 1
        taken.add(call_id)
        out.append(call if call_id == call.id else call.model_copy(update={"id": call_id}))
    return tuple(out)


def run_turn(
    conversation: Conversation,
    *,
    context: ToolContext,
    model: ModelPort,
    ledger: LedgerFn,
    limits: TurnLimits = TurnLimits(),
    cancel: Cancellation | None = None,
    budget_units: int | None = None,
    persist: PersistFn | None = None,
    on_event: EventFn | None = None,
) -> TurnResult:
    """跑一轮：模型说话、叫工具，直到它收手或者代码把它停下来。

    Args:
        conversation: canonical 历史。**它自己不动**——返回的是一份新的。
            末尾还缺 `tool_result` 的那几个 `tool_call` 会先被补跑（resume，
            见 `Conversation.pending_calls`）。
        context: 工具能碰到的全部东西。**`working_chapter` 就住在这儿**，
            而它是这一轮投影的坐标——见下面「作者在写第几章」那一节。
        model: 模型端口（适配器闭包里握着 config / plan）。
        ledger: **必填**。每一次拿到结果的模型调用记一笔。
        budget_units: 这一轮每次投影最多多少字，**口径是整份 payload**（含工具声明，
            见 `payload_units()`）。默认是「剪枝碰不到的那一块」**加上**
            `context.return_units`——**加，不是从里面扣**：`return_units` 至今的定义是
            「一次工具返回的天花板」（`ports.ToolContext.return_units`），它量的是对话
            那一侧；而工具声明 + 稳定前缀是剪枝一个字都动不了的固定开销。从对话的额度
            里扣掉它们，对话能记住的东西就凭空少一截，而**加一条工具会再少一截**——
            那正是 3.6 加完 `save_draft` / `read_draft` 之后发生的事（地板从约 3,900 字
            长到 5,171 字），且没有任何东西显示这件事。最贵的那一档症状是：一条按
            `return_units` 截好的返回在**下一次**投影里被剪成占位，而工具返回只有在下
            一次模型调用里才会被模型看见——于是那次调用等于没发生（钱花了、那段字进了
            持久化的对话却从没被读过），模型再查一次，三次之后撞上 `REPEATED_CALL`，
            作者收到的说法是「它在反复查同一件事」（一个指向别处的解释）。
            这条关系钉在 `tests/test_context_policy.py` 第七节。
            这仍然是今天真实的紧，不是一个安全余量，调用方要更宽就显式传。
        persist: 跑到一半就把已经长出来的消息落库（见 `PersistFn`）。
            **不传 = 这一轮的执行态只活在进程内**，进程死了 resume 无事可补。
        on_event: 边跑边往外喊（见 `EventFn`，ADR 0024）。**不传 = 一声不喊，
            而且这一轮的行为逐字节不变**——`run_turn` 只在自己已经知道的那几个
            边界上多叫一个函数，一条判断都不因为有没有人听而改变。
            **模型吐字那两条流不在这儿**：它们发生在 `ModelPort` 和起草台内部
            （见 `agent/model.py` / `agent/drafting.py`），装配层把同一个 `on_event`
            也交给它们——同 `cancel`，两个接线口给了不同的对象就等于只接了一半。

    Returns:
        `TurnResult`。**十一种停法各有各的判据**，措辞一律走 `stop_wording()`。

    ── 「作者在写第几章」怎么进来的，以及它一轮之内变不变 ──────────────────

    它住在 `ToolContext.working_chapter`（3.2 留的接线口），loop 是它的第一个**持有者**。

    **一轮之内它不变**，因为 `ToolContext` 是 frozen dataclass——这不是纪律，是类型。
    中途变的话，同一轮里前半截的工具返回和后半截绑的是两个章号，而投影按章号筛，
    筛出来的东西就没有一个自洽的读法了。

    **跨轮它可以变**：作者切到另一章 = 3.4 用新的 `working_chapter` 造一个新的
    `ToolContext` 再调一次 `run_turn`。canonical 历史一个字都不动，变的只是投影——
    这正是边界五说的「对话历史是 canonical，模型看到的是它的一次投影」。

    **它只标不挡**（`ports.ToolContext.working_chapter` 的 docstring 写了理由）：
    硬挡会把「因为知道第 200 章会怎样才回头改第 40 章」这个正常修稿动作挡在门外。
    """
    if not conversation.messages:
        raise ValueError("这段对话里作者一句话都还没说，没有可跑的一轮。")

    signal = cancel or Cancellation()
    emit = safe_emitter(on_event)
    declarations = tool_declarations()
    chapter = context.working_chapter
    # 默认预算 = **剪枝碰不到的那一块** + `context.return_units`。两个数不是同一种量：
    # `return_units` 量的是对话那一侧（它的定义至今是「一次工具返回最多给多少字」），
    # 而 `budget_units` 从 2026-08-12 起管的是整份 payload。地板从对话的额度里扣掉，
    # 就等于让「一次满额的返回」在下一轮自动失效（见上面 Args 那一段）。
    #
    # **地板是量出来的，不是抄的**：把历史清空再投影一次，得到的就是「工具声明 +
    # 稳定前缀 + JSON 信封」。抄一个数下来的话，别人往 `AGENT_SYSTEM_PROMPT` 里加两行
    # 就会静默把对话的额度切掉一块，而没有任何东西会提这件事。
    if budget_units is None:
        bare = conversation.model_copy(update={"messages": ()})
        floor = project(bare, None, budget_units=0, tools=declarations).payload_units
        budget = floor + context.return_units
    else:
        budget = budget_units
    stale_calls = _stale_manuscript_calls(conversation, context)

    live = conversation
    remembered: list[AgentMessage] = []
    steps = 0
    tool_calls = 0
    reported = 0
    charged = 0
    unmetered = 0
    last_projection: Projection | None = None
    seen_signatures: dict[tuple[str, str], int] = {}
    failure_streak: dict[str, int] = {}
    failures_in_a_row = 0

    def save() -> None:
        """把已经长出来的那几条交给调用方落库（见 `PersistFn`）。

        **不包 try/except**：落不了库和记不上账是同一档事——继续往下跑只会让作者
        为一段最后存不下来的历史付第二次钱。
        """
        if persist is not None:
            persist(live)

    def bill(receipt: ModelCallReceipt) -> None:
        """一次模型调用：**记一笔账 + 计一次闸**。两件事必须同时发生，所以只有这一个入口。

        **它也收工具花掉的那几笔**（`draft_chapter` 每次是一次真的模型调用）。3.3 那一版
        只有对话自己那次调用走这儿，于是「一步之内派发几个工具」是唯一罩得住起草的闸——
        而那道闸只管次数不管钱：六个 `draft_chapter` 是六稿正文，次数上完全合法。

        「报了 usage」的判据是**两半都在、而且入方向不是 0**，不是「不是 None」。
        少这两档的话闸门能被供应商关掉，而且是静默地关掉：
        ① 中转在流式下常给半份（入有数、出没有）；
        ② 本地端点（llama.cpp 等）给一份恒 0 的 usage。
        照 0 累加的话额度永远到不了顶，剩下的只有步数闸——而那是另一个闸，拦的是别的东西。
        **一个能被对方报低到失效的成本闸等于没有闸**，所以不可用时退回估算（偏紧）。
        """
        nonlocal reported, charged, unmetered
        ledger(receipt)
        spent = (receipt.prompt_tokens or 0) + (receipt.completion_tokens or 0)
        reported += spent
        if (
            receipt.prompt_tokens is not None
            and receipt.completion_tokens is not None
            and receipt.prompt_tokens > 0
        ):
            charged += spent
            return
        # 账上仍然照抄供应商报的（含 `None`、含 0）——闸门用估算，**账本不许编数**。
        unmetered += 1
        charged += max(
            spent,
            _estimate_tokens(receipt.prompt_bytes.decode("utf-8", errors="replace"))
            + _estimate_tokens(receipt.text),
        )

    def remember(outcome: ToolOutcome) -> None:
        """这次调用记下的那条规矩**排进队**（ADR 0023 决策二）。**不当场贴进 canonical。**

        贴在这儿它就夹在同一批的两条 `tool` 消息中间，而 wire 上那一批必须是连着的
        （同 `PRUNED_RESULT`：一条带 `tool_calls` 的 assistant 必须被同样多条 `tool` 消息
        接住）。**挪到批尾不改变任何判据**：规矩的两条判据（作者说了几遍 / 这一批过去了
        没有）数的都是**相对于作者那句话**的位置，而这一批里没有作者的话。

        章号来自 `ToolContext.working_chapter`，在工具那一侧就已经绑好了
        （`tools._handle_remember_rule`）——**这儿不重新取一次**，取两次就有两处会漂。
        `rule_message` 在这儿只负责造那条消息：它的三种拒绝（没坐标 / 空 / 超长）
        在工具那一侧已经全部发生过，走到这儿的都是过了闸的。
        """
        if outcome.remembered is None:
            return
        from .rules import rule_message  # 断环，同 `project()`

        remembered.append(
            rule_message(outcome.remembered.rule, chapter=outcome.remembered.chapter)
        )

    def bill_outcome(outcome: ToolOutcome) -> ToolOutcome:
        """一次工具调用的钱（`DraftProduct.calls` → `bill`）。**每条结果只经过它一次。**

        它和派发**分开**是因为 ADR 0022 之后派发可能发生在别的线程上（`BatchRunner`），
        而记账必须留在 loop 这条线上：`ledger` 写的是同一条 SQLite 连接，
        两个线程各开一个显式事务在同一条连接上是嵌不起来的。
        """
        for receipt in outcome.calls:
            bill(receipt)
        return outcome

    def run_tool(call: ToolCall, *, index: int = 0, total: int = 0) -> ToolOutcome:
        """派发一次工具，**并把它花掉的钱记上**。

        外面照旧**没有** try/except（模块 docstring 第三节）：这里加的是记账，
        不是异常处理。**补跑（resume）走这一条**，它永远是串行的——重放一次
        `draft_chapter` 是一次真的模型调用，并发补跑只会让它更快地花钱。

        **它也走 `BatchRunner`，宽度是 1。** 派发点只许有一个（同 `dispatch` 那道闸）：
        这一层直接调 `dispatch` 的话，补跑和批派发就是两条路，而「有副作用的工具怎么排队」
        这类规矩迟早只在其中一条上生效。**「正在查什么」那一声也归它喊**，理由同上。
        """
        runner = BatchRunner(
            (call,),
            context,
            on_start=lambda _index: emit(
                TurnEvent.tool_started(call.name, index=index, total=total)
            ),
        )
        outcome = bill_outcome(runner.take(0))
        emit(TurnEvent.tool_finished(outcome, index=index, total=total))
        return outcome

    def finish(
        reason: StopReason,
        *,
        reply: str = "",
        note: str = "",
        asked: AuthorQuestion | None = None,
    ) -> TurnResult:
        """收场。**「为什么停」那一声在这儿喊，一条出口都绕不过去**——
        `finish` 是这个函数唯一的 return 形状，所以「停了却没人说为什么」构造不出来。

        **作者定下的规矩也在这儿落进 canonical**，理由是同一条：十一种停法各有各的出口，
        逐个记得去贴的话迟早漏掉一种，而漏掉的形态是「他说了，系统答应了，下一轮它就忘了」。
        `save()` 跟着走一次——不然那几条只活在返回值里，而调用方可能只认 `persist`。
        """
        nonlocal live
        if remembered:
            live = live.extended(*remembered)
            remembered.clear()
            save()
        emit(TurnEvent.stopped(reason))
        return TurnResult(
            conversation=live,
            reason=reason,
            said_to_author=stop_wording(reason),
            reply=reply,
            steps=steps,
            tool_calls=tool_calls,
            tokens_reported=reported,
            calls_without_usage=unmetered,
            tokens_charged=charged,
            asked=asked,
            projection=last_projection,
            maintainer_note=note,
        )

    # ── resume：进程死在模型调用和派发之间时，先把缺的 result 补上 ──────────
    # 这就是 ADR 0019 说的「看尾巴、补跑缺的、继续」。T1–T5 只读或纯函数，重放免费。
    #
    # **但补跑之前先问信号。** ADR 那句「重放免费」对表里的一个工具不成立：`draft_chapter`
    # 重放一次是一次真的模型调用，花的是作者的钱（2026-08-11 起它至少记得上账了——
    # `run_tool` 把回执交给 `bill`——但记上账不等于没花）。
    # 判据不是「哪个工具贵」——那是一张会在加工具的那天漂的表——是**信号亮着就不动手**。
    # 停下来时给剩下那几个补壳（同 `UNRUN_CALL`：这一档表示「这一轮明确决定不跑了」），
    # 否则 `pending_calls` 那句「从界面上停下来的会话在这儿是空的」当场变成假话。
    pending = live.pending_calls
    # **补跑也要过批宽度这道闸。** 闸原本只拦「还没派发的那一批」，而落库之后
    # 「模型要了 50 个工具」这件事本身会被持久化——进程死在闸之前，剩下那 50 个
    # 就以 `pending` 的身份回来，走的是一条没有闸的路。一次崩溃不该成为绕过成本闸的办法。
    if len(pending) > limits.max_calls_per_step:
        live = live.extended(*_unrun(pending))
        save()
        return finish(StopReason.BATCH_TOO_WIDE)
    for position, call in enumerate(pending):
        if signal.stopped:
            live = live.extended(*_unrun(pending[position:]))
            save()
            return finish(StopReason.AUTHOR_STOPPED)
        outcome = run_tool(call, index=position + 1, total=len(pending))
        live = live.extended(_outcome_message(outcome))
        remember(outcome)
        tool_calls += 1
        # **每补跑一个存一次**，而不是补完整批再存：否则死在补跑中间的下一次 resume
        # 又从整批的第一个开始，而「重放免费」对表里的 `draft_chapter` 不成立。
        save()
        if outcome.asked is not None:
            # 补跑出来的一次提问和现跑的一模一样地结束这一轮（ADR 0024）。**不能少这一支**：
            # 进程死在「模型要求问一句」和「派发」之间是真会发生的一档，那时问题会以
            # `pending` 的身份回来——少了它，这一轮会带着一个没人答的问题接着往下跑，
            # 而那正是这条工具要去掉的东西。
            live = live.extended(*_unrun(pending[position + 1 :]))
            save()
            emit(TurnEvent.asked_author(outcome.asked))
            return finish(StopReason.ASKED_AUTHOR, asked=outcome.asked)
        if charged >= limits.max_tokens:
            # 补跑本身能烧完整轮额度（一个 `draft_chapter` 就是一稿正文）。
            # 剩下那几个配壳停下来，**不是接着补**——不然这一轮在跑第一次模型调用
            # 之前就已经超了。
            live = live.extended(*_unrun(pending[position + 1 :]))
            save()
            return finish(StopReason.COST_LIMIT)

    taken_ids = _taken_ids(live)

    while steps < limits.max_steps:
        if signal.stopped:
            return finish(StopReason.AUTHOR_STOPPED)

        # `tools=declarations` 传的是**下面那一行真的会发出去的同一个对象**：量的和发的
        # 分成两次构造，就又有一处能漂（ADR 0023「前置：先把账算对」）。
        last_projection = project(
            live,
            chapter,
            budget_units=budget,
            stale_calls=stale_calls,
            tools=declarations,
        )
        if last_projection.over_budget:
            return finish(StopReason.CONTEXT_FULL)

        started = perf_counter()
        try:
            result = model(last_projection.messages, tools=declarations, cancel=signal)
        except ProviderError as exc:
            # 作者掐掉一次流式调用时，适配器让迭代器抛出去，`complete()` 会把它收敛成
            # `ProviderError`——**那不是故障**，所以先问信号再判故障。
            if signal.stopped:
                return finish(StopReason.AUTHOR_STOPPED)
            return finish(StopReason.MODEL_UNREACHABLE, note=str(exc))
        elapsed_ms = max(0, int((perf_counter() - started) * 1_000))
        steps += 1

        # ── 记账。**在任何一条停止分支之前**：钱已经花掉了，停下来不会把它退回来 ──
        prompt_bytes, prompt_hash = _prompt_digest(last_projection.messages, declarations)
        bill(
            ModelCallReceipt(
                capability=AGENT_CAPABILITY,
                schema_version=AGENT_SCHEMA_VERSION,
                model=result.model,
                finish_reason=result.finish_reason,
                prompt_hash=prompt_hash,
                prompt_bytes=prompt_bytes,
                text=result.text,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                # **钱照抄，不在这儿算**（`provider._priced` 已经填好，理由见
                # `ModelCallReceipt.cost`：单价要按 base_url 查，而这一层只有 model）。
                cost=result.cost,
                # 缓存命中量只读取、不参与任何闸门：`charged` 那一侧算的是花掉的总量，
                # 而命中只让它更便宜、不让它更少。**账照抄，闸不动。**
                cache_read_tokens=None if result.cache is None else result.cache.read_tokens,
                cache_write_tokens=None if result.cache is None else result.cache.written_tokens,
                elapsed_ms=elapsed_ms,
            )
        )

        calls = _addressable(result.tool_calls, taken_ids)
        live = live.extended(
            AgentMessage(
                role=Role.ASSISTANT,
                content=result.text,
                tool_calls=calls,
            )
        )
        # **这一次落库是 resume 的全部前提**，排在任何一条停止分支之前，理由同记账：
        # 钱已经花掉了。这条消息不落盘 = 进程死在派发中间时 `pending_calls` 是空的
        # （尾巴上根本没有那条 assistant），而作者重开之后看到的是「什么都没发生」。
        save()

        # 它这一步说的那段话。**排在停止分支之前**：只叫工具时它也会说一句
        # （「我先查一下第 40 章」），而那句话今天只活在对话历史里——事件流不喊它，
        # 界面上就是一段几十秒的空白配一个转圈的图标。
        if result.text.strip():
            emit(TurnEvent.reply_text(result.text))

        if not calls:
            # 既不叫工具也不说话 = 这一步什么都没发生。**再来一次是拿作者的钱赌**，
            # 所以这儿停，并且说的是「再说一遍试试」——重试的决定权在作者手上。
            if not result.text.strip():
                return finish(StopReason.NO_OUTPUT)
            return finish(StopReason.DONE, reply=result.text)

        if charged >= limits.max_tokens:
            live = live.extended(*_unrun(calls))
            return finish(StopReason.COST_LIMIT, reply=result.text)

        # 额度闸排在前面：那笔钱**已经**花掉了，说出来的理由该是真实原因。
        # 这一条拦的是**还没花出去**的那一批（见 `TurnLimits.max_calls_per_step`）：
        # 一个都不派发，全部配 `UNRUN_CALL` 的壳——「这一轮明确决定不跑了」。
        if len(calls) > limits.max_calls_per_step:
            live = live.extended(*_unrun(calls))
            return finish(StopReason.BATCH_TOO_WIDE, reply=result.text)

        # **这一批的执行器**（ADR 0022）：没有副作用的那几条同时跑，其余按序、单独跑。
        # 它不改这儿的任何一条规矩——出来的结果与 `calls` 同序，闸门照旧逐条查。
        batch = BatchRunner(
            calls,
            context,
            workers=limits.parallel_tools,
            # **一个并发窗口是一次跑完的**，所以「正在查什么」那一声由执行器喊：
            # 在这儿按 `take()` 的顺序喊，并发那一档喊出来的是一句读起来很正常的假话
            # （见 `BatchRunner.__init__`）。
            on_start=lambda index: emit(
                TurnEvent.tool_started(calls[index].name, index=index + 1, total=len(calls))
            ),
        )

        def settle(position: int) -> list[AgentMessage]:
            """这一批**剩下那几条**的收尾：已经跑过的如实收走，没跑的配壳。

            并发窗口是一次跑完的，所以闸门在批中间停下来时，后面那几条**可能已经跑完了**
            ——`draft_chapter` 甚至已经花过钱。给它们配一个「这一轮明确决定不跑了」的壳，
            就是一次凭空消失的花销加一句骗人的话。所以这儿把它们记上账、如实贴回去。
            """
            nonlocal tool_calls
            done = dict(batch.leftovers())
            out: list[AgentMessage] = []
            for index in range(position, len(calls)):
                already = done.get(index)
                if already is None:
                    # 没跑的那几条**不喊「查完了」**：它们连「正在查」都没喊过
                    # （`on_start` 只在窗口真的开跑时才响）。为什么停由停止那一声说。
                    out.append(_unrun([calls[index]])[0])
                    continue
                bill_outcome(already)
                remember(already)
                tool_calls += 1
                emit(TurnEvent.tool_finished(already, index=index + 1, total=len(calls)))
                out.append(_outcome_message(already))
            return out

        for position, call in enumerate(calls):
            if signal.stopped:
                live = live.extended(*settle(position))
                return finish(StopReason.AUTHOR_STOPPED, reply=result.text)

            # 「无进展」之一：同一个工具、**同样的参数**。判据是 `(name, arguments)`
            # **字节相同**——不做 JSON 归一化，因为归一化要在这儿把参数再解析一遍，
            # 而这个仓库刚把「同一件事两处解析」清掉（那道闸只在 `dispatch` 里）。
            # 漏判的代价只是多走一步，步数闸接得住；这一条一个语义判断都没有。
            signature = (call.name, call.arguments)
            seen_signatures[signature] = seen_signatures.get(signature, 0) + 1
            if seen_signatures[signature] > limits.repeat_limit:
                live = live.extended(*settle(position))
                return finish(StopReason.REPEATED_CALL, reply=result.text)

            # **裸调用，外面没有 try/except**（模块 docstring 第三节）。
            outcome = bill_outcome(batch.take(position))
            remember(outcome)
            tool_calls += 1
            emit(TurnEvent.tool_finished(outcome, index=position + 1, total=len(calls)))
            live = live.extended(_outcome_message(outcome))
            # **逐个存**：一批三个、跑完第一个就死掉时，下一次回来该补的是剩下那两个，
            # 不是整批重来。整批重来在只读工具上只是浪费，在 `draft_chapter` 上是真花钱。
            # （并发窗口里那几条这时**已经跑完了**，落库仍然逐条——库里那串消息的形状
            # 不该因为它们是同时跑的就变。）
            save()

            if charged >= limits.max_tokens:
                # **批宽度那道闸只管次数不管钱**：六个 `draft_chapter` 是六稿正文，
                # 次数上完全合法。所以钱这一半在这儿收——每派发完一个查一次，
                # 而不是等下一次模型调用之前才查（那时这一批已经全跑完了）。
                #
                # **并发之下它变松了一档，说清楚**：同一个窗口里的那几条是一起跑掉的，
                # 所以这道闸拦得住「下一个窗口」，拦不住「这一个窗口的后半截」。
                # 上限是 `max_calls_per_step`（6）稿，而不是无限——`settle` 会把
                # 已经跑掉的那几条如实记上账，闸门看得见它们，只是没能拦在前面。
                live = live.extended(*settle(position + 1))
                save()
                return finish(StopReason.COST_LIMIT, reply=result.text)

            if outcome.asked is not None:
                # ── **停下来问 = 这一轮说完了**（ADR 0024）────────────────────────
                #
                # ADR 那句「模型不再叫工具，loop 自己就停」**在这一层不是自动成立的**：
                # 这个 for 之后还有下一次模型调用，所以模型完全可以问一句、然后在同一轮
                # 里接着 `draft_chapter` ——作者会同时收到一个问题和一份照猜写出来的稿子，
                # 而那正是这条工具要去掉的东西（**钱也已经花掉了**）。
                #
                # 所以收场是**代码的事**，和别的那几种停法一样（「循环归模型，停止条件归代码」）。
                # 判据是 `outcome.asked`（出参的类型），不是工具名——见 `ToolOutcome.asked`。
                # 剩下那几条走 `settle`：已经跑掉的如实收走，没跑的配壳。
                live = live.extended(*settle(position + 1))
                save()
                emit(TurnEvent.asked_author(outcome.asked))
                return finish(
                    StopReason.ASKED_AUTHOR, reply=result.text, asked=outcome.asked
                )

            if outcome.ok:
                failure_streak.pop(call.name, None)
                failures_in_a_row = 0
                continue
            failure_streak[call.name] = failure_streak.get(call.name, 0) + 1
            failures_in_a_row += 1
            if (
                failure_streak[call.name] >= limits.tool_failure_limit
                or failures_in_a_row >= limits.tool_failure_limit
            ):
                live = live.extended(*settle(position + 1))
                return finish(StopReason.TOOL_STUCK, reply=result.text)

    return finish(StopReason.STEP_LIMIT)


__all__ = [
    "AGENT_CAPABILITY",
    "AGENT_SCHEMA_VERSION",
    "AGENT_SYSTEM_PROMPT",
    "AgentMessage",
    "Cancellation",
    "Conversation",
    "EventFn",
    "LedgerFn",
    "ModelCallReceipt",
    "ModelPort",
    "PAYLOAD_ENVELOPE_UNITS",
    "PersistFn",
    "Projection",
    "STALE_MANUSCRIPT",
    "Role",
    "StopReason",
    "TurnEvent",
    "TurnEventKind",
    "TurnLimits",
    "TurnResult",
    "payload_units",
    "project",
    "run_turn",
    "safe_emitter",
    "start_conversation",
    "stop_wording",
    "tool_declaration_units",
]
