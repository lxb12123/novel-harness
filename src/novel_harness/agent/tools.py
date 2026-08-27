"""模式二（agent harness）的**工具表 —— 它就是权限边界**（ADR 0019 边界一）。

规格书是 [`docs/adr/0019-agent-loop-not-graph.md`](../../../docs/adr/0019-agent-loop-not-graph.md)，
这个文件是它六条边界里第一条和第二条的实现。

Claude Code 给模型一个通用的 `Read` 是安全的。**这里给一个通用的「读图谱节点」工具就是泄漏**：
agent 调一次就把 PLANNED 秘密的正文读进对话历史，而对话是持久化且会累积的，之后每一轮
起草它都还在上下文里。原则 11 / 铁律 5 破功，**而且是我们自己递过去的**。

所以这个文件的形状是被那一条决定的：

1. **一张表，一处闸。** 模型能做的事 = `TOOL_TABLE` 里的那几条，多一条都没有。
   发给模型的 function schema **由这张表生成**（`tool_declarations()`），不许在别处
   手写第二份——两份迟早漂，而漂的方向没人看得见。
2. **出参一律收窄成本模块自己的 Pydantic 类型。** 全库最不该被完整序列化的两批节点
   （秘密本身、`first_appears_chapter > 本章` 的未来实体）恰好就是约束工具要谈论的那两批，
   而 `NodeProps` 是 `extra="allow"`——作者写在秘密节点上的 `twist` 会原样穿过任何一次
   `model_dump_json()`。因此这里**只出 `NodeRef`（id/label/name）和纯量**，
   `Node` / `StateSnapshot` 一个都不出。

   （这儿原来还有一条关于认知矩阵的说明：它不出去的理由是**寿命**不是 props——
   工具结果永久留在对话里，而矩阵跟章号绑死。矩阵和 `knows_secret` 都随秘密下线
   走了，ADR 0039；那条「寿命」的论证仍然对**其它**跟章号绑死的出参成立，
   记在 ADR 0037 的补记里。）
3. **`json.loads` 在这一层做，不在运输层。** `draft/provider.py` 的 `ToolCall.arguments`
   是模型生成的原始字符串，运输层不解析、也不校验工具名（认得工具名就等于有第二份工具表）。
   「解析失败算什么」是编排层的判断，所以它是 `dispatch()` 的一个分支，返回一条
   模型读得懂的中文错误，而不是一个异常。

── 这一版有哪几条工具，以及**没有**哪一条 ──────────────────────────────

**约束与起草**（`tools.py` 自己实现）：

| 工具 | 它回答什么 | 它绝不返回什么 |
|---|---|---|
| `scene_constraints` | 第 N 章哪些实体还没登场 | PLANNED 边 |
| `character_state`   | 某人第 N 章在哪、什么状态、登场没有、死没死 | `Node`（它带着 props） |
| `draft_chapter`     | 起草第 N 章的一稿，**收进候选、不动书** | **一整章正文**（只给 id + 定长预览 + 自述） |
| `save_draft`        | 把某一稿写进它那一章（**不问作者**） | —— 见下面「落盘」那一节 |
| `read_draft`        | 按 id 把某一稿的全文拿回来 | —— 最贵的一条，只在要合并两版时调 |
| `ask_author`        | 停下来问作者一句，给他几个可点的选项 | —— 见下面「问作者」那一节 |
| `remember_rule`     | 把作者刚定下的一条规矩记下来 | —— 见下面「记规矩」那一节 |

**写前校准**（ADR 0033，2026-08-17）：

| 工具 | 它回答什么 | 它绝不返回什么 |
|---|---|---|
| `calibrate_scene`  | 按第 N 章时点核对预计人物、状态、关系、知识、事件和摘要（带来源） | 秘密内容、完整 PLANNED、任意 Node props、不对称知识事件 |
| `seal_scene_brief` | 把校准报告封存成不可变 `calibration_id`（Writer 简报） | 自由文本（入参只有 inspection id + 作者选择；目标由后端固定渲染） |

**书内索引**（`index.py`，四层，越往下越贵；那份 docstring 是它的规格）：

| 工具 | 层 | 它回答什么 |
|---|---|---|
| `book_index`         | L0 | 全书章标题 + 花名册（秘密只给**显示名**） |
| `character_chapters` | L1 | 某几个人**同时**出现在哪些章（正文命中 / 已确认事件，两条轴分开） |
| `chapter_summaries`  | L2 | 指定区间的滚动总结 + **哪几章有正文却没摘要** |
| `chapter_text`       | L3 | 一章正文（从磁盘读，ADR 0007） |

**索引的四条工具全部只读、且出参里没有一条路径走得到 `props`。** 它们新加的返回面同样
被 `tests/test_agent_tools.py` 的那张网罩着——加工具的那天先跑那张网，不是先跑功能测试。

── 落盘：**是一条工具了，但它收不到一个字的正文**（ADR 0022，2026-08-12）────────

ADR 0019 边界一原来的最后一条是「写正文必须作者确认，模型不能直接落盘」。
**那一条被 ADR 0021 推翻了**（起草完直接写磁盘，不弹框），而 ADR 0022 又把它的**机制**
拆成两个动作：生成（花钱、不动书）/ 落盘（不花钱、动书、**仍然不问作者**）。
于是表里第一次有了一条会改作者的书的工具（`save_draft`）。它守的东西换了个位置，
但一条都没松（`tests/test_agent_tools.py::test_no_tool_takes_a_paragraph_of_prose` 钉着这一节）：

- **`save_draft` 只收一个候选 id，收不到文本。** 所以**模型没有「只写不草」这个动作**：
  它不能拿一段自己编的（或者从别处抄来的）文本去盖某一章——能写进磁盘的只有刚刚由后端
  按那一章的约束生成出来的候选。这条以前靠「表里根本没有写工具」成立，现在靠**入参形状**
  成立，而后者是可断言的。
- **写入面仍然不在 `ToolContext` 上。** 写盘要 `importer.sync()`，而它收的是
  `GraphStore`（带写入面）；这个 dataclass 上只有 `StoryGraph`，`CanonWriter` 在类型层
  就不存在（边界一）。落盘发生在**注入进来的那个 `DraftDesk` 里**（`agent/drafting.py`
  握着 conn 和 store），这一层只把它的回执（`landed` / `note`）原样交给模型。
- **候选既不进正文也不进对话**：它在 `draft_candidate` 表里（ADR 0022 / 边界三）。
  `draft_chapter` 的返回里只有 id + 定长预览 + 那一稿的自述——一整章正文进对话的话，
  无状态的 wire 会把它**每一轮**重发一遍，直到会话结束。

**`secret_surfaces` / `resolve_cast` 也不在表里**（`tests/test_draft_boundary.py`
的 `WRITER_BANNED`）：前者是秘密的内容 tell（`玄血蛊`）——进对话就是把检测器要找的词
自己写进去；后者会给「第二份约束推导」开门，而约束集只许有一个入口
（`panel/constraints.py`）。

── 问作者：**模型决定什么时候问，作者决定答什么**（ADR 0024，2026-08-12）────────

`ask_author` 是表里第一条**不查东西、也不做东西**的工具：它把一句问话和几个可点的选项
交出去，然后这一轮就结束了。三条边界：

- **它不碰 `ToolContext`。** handler 里一个 `context.` 都没有，出参逐字段就是入参——
  **引擎往问句里加不了一个字**。这不是纪律，是这条工具的实现里根本没有数据来源。
  「问句里不许夹带秘密」（ADR 0024）在引擎这一侧只能做到这一步：**判断一句话是不是
  说破了秘密要回答「这句话是什么意思」，那是语义判断**（ADR 0005 禁止 v1 长出这种能力）。
  能做到的是「引擎不给它任何新料」，而模型手上本来就只有显示名。
- **入参形状就是「这是个问题」。** 至少两个选项、每个都短到能摆成一颗按钮
  （`ASK_OPTION_UNITS`）——**一段散文进不来**。一个问句混在普通回话里，作者会当成
  陈述句翻过去，而「我在等你」必须在结构上分得开。
- **什么时候该问写在 `description` 里，不写成代码里的判据。** 「不问就得猜、而猜错了
  作者看不出来」是语义判断，那是**模型**的活（ADR 0005 禁的是引擎）。

**它结束这一轮**：判据是出参的**类型**（`AuthorQuestion`），见 `ToolOutcome.asked`。

── 记规矩：**入参只有那句话，章号是引擎的**（ADR 0023 决策二，2026-08-12）────────

`remember_rule` 是表里第二条不查东西的工具。作者在对话里说的「别写打斗」「冷一点」
是**偏好不是事实**——它不进 canon（没有引语、没有证据、没有一条边安放得了它），
但它必须活过剪枝，所以它变成 canonical 历史里的一条记录（`rules.rule_message`）。
四条边界：

- **入参里没有章号，而且永远不许有。** 章号只能来自 `ToolContext.working_chapter`
  ——作者填不了（约束 10：表单里有章号输入框 = 邀请污染），模型也传不了（给它一个
  `chapter` 参数，它就能把一条规矩钉在第 9999 章上，而那正是「不许把有效期改成 9999」
  要防的）。**没有坐标就拒绝记录**：一条过不了期的规矩会跟着作者走到第 200 章，
  而他不知道它在。
- **它不写任何东西。** handler 只造一条消息交回去，**贴进 canonical 的是 loop**
  （`agent/loop.py::run_turn` 的 `finish()`）——同 `ToolContext` 上没有写入面那条纪律：
  这一层碰不到会话，也就不可能改写历史。
- **「说了几遍」是按字比的**（`rules.rule_key`：归一化之后**字节相等**），所以工具描述
  里写死了「同一条要用一模一样的措辞」。判它「是不是一个意思」是语义判断，ADR 0005 禁。
- **出参不回吐那句话**（`RememberRuleResult.rule` 是 `exclude` 的）：工具返回只按章号
  **往前**筛，回吐一次就等于把一条第 40 章的规矩以「引擎确认过的」的形态钉进第 200 章的
  prompt——那正是 ADR 0023 点名的那个故障。

**残余代价（接受，同 ADR 0019 边界二那一条）**：模型这次调用的参数
（`{"rule": "别写打斗"}`）躺在它自己那条 assistant 消息里，**它不按章号过期**——
那条规矩到第 200 章早就不生效了，而「我当时记过这么一句」还在历史里。改写模型说过的话
是另一种病，判它又需要读懂那句话（语义判断）。所以这一层的保证收窄成一句可断言的话：
**引擎自己说的每一句里，都没有一条过了期的规矩**；真正生效的那一条（SYSTEM 消息）
该没就没了。

── 撞空：**「查不到」和「说法不对」是两句话**（2026-08-13 实测）────────────

工具拒绝时说的那句话不只是礼貌，它**决定模型下一步烧不烧一步**。实测那一轮（722 章的
真书，给第 723 章起草）八步全花在查询上、一稿都没写出来，直接原因是引擎对两种完全不同
的情况说了同一句「换一个更具体的称呼」——而它们的正确下一步是相反的：

| 情况 | 判据（集合判断） | 该做什么 | 谁说这句话 |
|---|---|---|---|
| 花名册里没有 | `len(hits) == 0` | **别再试** | `index.UnknownCharacter` |
| 一个叫法指向好几个人 | `len(hits) > 1` | 换个更具体的称呼（重试是对的） | `index.resolve_one` |

两句话住在 `index.py`（`tools.py` 认得它、反过来是环），**全仓只有那一份**——它以前有
两份拷贝，两份说的都是同一句错话。

在这之上加了一件只有派发这一层做得到的事：**`TurnMemo` 记住这一轮撞空过的称呼**，
从第二次开始把清单附在拒绝后面（「你已经在花名册外面撞了 2 次」）。它是一个**只增不减
的字符串集合**——一个语义判断都没有——而 `agent/loop.py::TurnLimits.unknown_name_limit`
拿它的 `len()` 当第三道闸：上面那两个连续失败计数都会被一次成功清零，而实测那一轮的
节奏恰恰是「失败、失败、成功」。

**它只用来说话和计数，从不短路查询**（见 `TurnMemo` 的 docstring：作者会在这一轮中途
把人加进花名册，缓存一个「查不到」就是造一个看起来完全正常的错误答案）。

── 边界二在这里的落点：**没有一个工具收约束** ──────────────────────────

约束是逐章算的，而对话跨章累积（能隔三个月回来）。`ch40 的 must_not_reveal ⊇ ch90 的`
——陈旧的约束躺在 context 里，方向是 fail-open 的最坏那侧。解法不是清理历史，是让
**模型没有机会把约束当参数传进来**：`DraftAsk` 上没有约束字段，`SceneConstraintsArgs`
上也没有，两个模型都是 `extra="forbid"`，模型幻想出一个 `must_not_reveal` 参数会被
当场拒掉。约束由 `_scene_context()` 当场从 `scene_view(chapter)` 算，**全模块只此一处**。

── 入参里唯一的时间坐标是章号，而它是 AS OF 不是声明（约束 10）────────────

`chapter` 在这里的语义与面板那条 `chapter` 参数完全相同：**查询坐标**，不写进任何一行数据。
作者永远不填 `valid_from`——那个数只由证据决定（ADR 0006 / §5.9）。这条差别在工具表上
之所以还成立，是因为表里**一条写图谱的工具都没有**：没有写路径，就没有地方能把这个数
存成一条边的 `valid_from`。

── 还没接线的那几个缝（诚实说明）──────────────────────────────────────

`ToolContext` 上那几个 `None` 不是可选功能，是**故意留在外面的接线口**，完整清单和
每一个的退化方向在 `ports.py`。这里只说与本文件直接相关的两个：

- `root_path` 缺席时「谁在场」推不出来（ADR 0018：在场从正文数，不是作者填的），
  约束退化成**全禁**（`unknown_cast_constraints`）。方向是 fail-closed 的那一侧
  ——多禁一条的代价是少写一段，漏禁一条的代价是崩人设。
- `drafter` 缺席时 `draft_chapter` 返回一条明确的「没接线」而不是假装起草。
  它是注入而不是在这里再实现一遍：「章号 → 一稿正文」的实现在
  `draft/product_draft.py::draft_chapter()`，**HTTP 的 `/draft` 和这个工具调的是同一个函数**
  （2026-08-11 从 `api/app.py` 的路由体里提出来的，提之前那 120 行只存在于那儿）。
  在这里抄一份 = 第二条会漂的起草路径，而这个仓库刚把「同一份东西三处拷贝」的病清掉。

**注入的形状本身也在守边界二**：`drafter` 收的是 `(DraftAsk, DraftContext)` 两件东西，
而 `DraftAsk` 里没有约束字段、`DraftContext` 由后端算——起草侧拿不到模型给的约束，
不是靠纪律，是没有那个参数。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..draft.context import (
    DraftContext,
    ResolvedConstraints,
    unknown_cast_constraints,
)
from ..draft.length import DraftLanguage, count_units
from ..draft.provider import ToolCall
from ..extract.call_audit import ModelCallReceipt
from ..graph import NodeLabel, NodeRef
from ..graph.store import StoreError
from ..importer import chapter_path, read_chapter, text_digest
from ..mentioned import mentioned_cast
from ..panel.constraints import UnresolvedCast, scene_view
from ..panel.state import character_state as _state_at
from ..text import paragraphs as split_paragraphs
from ..calibration.calibrate import CalibrationInput, calibrate_scene
from ..calibration.models import (
    AuthorResolution,
    AuthorTurnRef,
    CalibrationReport,
    ReferencedFact,
    SceneProposal,
    TargetChapterSnapshot,
)
from ..calibration.seal import SealRefused, seal_scene_brief
from ..calibration.store import CalibrationNotFound, CalibrationRefused, CalibrationStore
from ..calibration.handoff import build_retcon_handoff
from .prompt_terms import message, translate_tool_declarations
from .index import (
    BookIndexArgs,
    ChapterFullText,
    ChapterSummariesArgs,
    ChapterTextArgs,
    CharacterChaptersArgs,
    UnknownCharacter,
    handle_book_index,
    handle_chapter_summaries,
    handle_chapter_text,
    handle_character_chapters,
    resolve_one,
)
from .ports import DraftAsk, DraftDesk, ToolContext, ToolRefused, TrackVerdict

# ══════════════════════════════════════════════════════════════════════════
# 入参：模型填的那几个格子。**每一个字段名和描述都会原样发给模型。**
# ══════════════════════════════════════════════════════════════════════════


class SceneConstraintsArgs(BaseModel):
    """查「第 N 章不许说破什么」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(
        ge=1,
        description="要查第几章（AS OF 第几章，纯查询坐标，不会写进任何数据）。",
    )


class CharacterStateArgs(BaseModel):
    """查「某个人在第 N 章的处境」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(
        ge=1,
        description="要查第几章（AS OF 第几章，纯查询坐标）。",
    )
    character: str = Field(
        min_length=1,
        description="人物的称呼，用作者在正文里的那个叫法。有歧义的叫法会被拒绝。",
    )


class CheckTrackArgs(BaseModel):
    """拿这一章已经落盘的正文去跟**后面那些已经写完的章**对一遍（轨道阶段 3）。

    ── 为什么只收一个章号 ────────────────────────────────────────────────

    ADR 0019 边界二：**模型没有机会影响这个工具的任何一个实质入参。** 正文由后端从
    磁盘读当前快照，轨道由后端算——模型递不进来一段自己编的正文，也指不定要跟哪几章比。
    它能决定的只有「问不问、问哪一章」，那正是「循环归模型」该有的那点自由。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(
        ge=1,
        description="要核对第几章。用它**已经落盘的当前正文**，不是你手里这一稿。",
    )


# `DraftAsk` 在 `ports.py`——它是**注入契约的一半**（`DraftFn` 收的就是它），
# 和起草那个接线口放在一起才看得出「起草侧拿不到模型给的约束」是结构而不是纪律。


ASK_QUESTION_UNITS: Final = 60
"""问句最多多少字（`count_units` 口径）。**一句话，不是一段。**"""

ASK_OPTION_UNITS: Final = 24
"""一个选项最多多少字。**它要摆成一颗按钮**——摆不下的就不是选项，是散文。"""

ASK_MIN_OPTIONS: Final = 2
ASK_MAX_OPTIONS: Final = 5
"""几个选项。下限 2 是这条工具的形状本身（一个选项的「选择」不是选择）；
上限 5 是屏幕上一眼看得完的量——再多作者只会点第一个。"""


class AskAuthorArgs(BaseModel):
    """停下来问作者一句。**这不是一次查询，它没有章号**（见模块 docstring「问作者」）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(
        min_length=1,
        description=(
            "问他的那一句话，一句就够。用他自己的说法（人物名、场景），"
            "不要提工具名和编号。"
        ),
    )
    options: tuple[str, ...] = Field(
        min_length=ASK_MIN_OPTIONS,
        max_length=ASK_MAX_OPTIONS,
        description=(
            "几个他能直接点的答案，每个都短。**它们是几条不同的走法，不是同一条的复述。**"
            "他也可以不点、直接说别的。"
        ),
    )

    @model_validator(mode="after")
    def _short_enough_to_be_buttons(self) -> AskAuthorArgs:
        """长度在这儿拒，不在别处被悄悄截断。**报错里不回显他/它写了什么**
        （同 `_validation_message`：这一层是把任意字符串搬进持久化对话的通路）。"""
        if count_units(self.question, DraftLanguage.ZH) > ASK_QUESTION_UNITS:
            raise ValueError(
                f"问句太长了（上限 {ASK_QUESTION_UNITS} 字）。作者要一眼看完它才答得上来，"
                "把背景放到你正文里说，这里只留那一问。"
            )
        for option in self.options:
            if not option.strip():
                raise ValueError("有一个选项是空的：每一个都要是他点得下去的一句话。")
            if count_units(option, DraftLanguage.ZH) > ASK_OPTION_UNITS:
                raise ValueError(
                    f"有选项太长了（上限 {ASK_OPTION_UNITS} 字）。选项是一颗按钮，"
                    "不是一段解释——把解释放进问句前面那句正文里。"
                )
        return self


# ── `RememberRuleArgs` 的那几条约束，写在类外面（**docstring 要花钱**）──────────
#
# 入参模型的 docstring 会原样进 `model_json_schema()` 的 `description`，也就是
# **每一轮、每一次调用都重发一遍**（`tool_declarations()` 是稳定前缀的一部分，
# 但那是省缓存不是省钱；ADR 0023「前置」量的正是这块地板）。所以给维护者看的理由留在
# 这儿，类里只留说给模型听的那一句。
#
# **这里没有 `chapter`，也没有「管多久」**：两样都不是模型的。章号是引擎手里的坐标
# （约束 10），而「管这一批还是管整章」由「作者说了几遍」决定（`REPEAT_TO_WIDEN`，
# 一条集合判断）。给模型任何一个旋钮，它都能把一条随口的偏好变成常驻——而 ADR 0023
# 把那一侧点名成最贵的：**一条隐形的规矩跟着作者走，他不知道它在。**
#
# 字段叫 `rule` 而不是 `text`：`tests/test_agent_tools.py` 那张网拦的是「正文形状的入参」
# （模型能拿一段自己编的字去盖作者的书）。一条规矩不是正文，但**长得像正文的字段名一律
# 不许出现在工具入参上**，那条判据认的就是名字。
class RememberRuleArgs(BaseModel):
    """记下作者刚定的一条规矩：**那句话 + 它管到什么时候。没有章号。**

    `until` 是 2026-08-15 加的（迁移 016）。作者的原话：「你在写某一章的时候用户讲过的
    规则，然后**你规定的时效**什么的都可以记一下」。**「你规定的」三个字定死了它的形状**：
    时效得是模型明确交出来的一个字段，埋在 `rule` 那句话里就没有可以记下来的东西。

    **它仍然不是章号**（约束 10）：模型写的是一句人话（「男主走出这片沙地为止」），
    引擎一个字都不解析，也永远不会把它换算成一个数。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str = Field(
        min_length=1,
        description=(
            "他要的那一句，用他自己的说法，一句话。太长会被退回来让你压短。"
        ),
    )

    until: str = Field(
        min_length=1,
        description=(
            "**这条什么时候就不算数了**，用一句人话写清楚。"
            "比如「男主走出这片沙地为止」「这一场打完」「他和师父摊牌之前」"
            "「整本书都这样」。\n"
            "说不清就写「整本书都这样」——**别硬编一个条件**，编出来的条件下一轮是你自己在读。\n"
            "这句话你以后每一轮都会连着规矩一起看到，**它是你判断这条还作不作数的依据**；"
            "作者也会在那张表里看到它。"
        ),
    )


class GetResultArgs(BaseModel):
    """`get_result` 的入参：**这一轮里的编号**（见投影末尾「已收起的结果」清单）。

    编号只活这一轮（同 `TurnMemo` 的生命周期），跨轮不承诺稳定——下一轮要内容就
    正常重查，那才是拿得到当前版本的路径。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(ge=1, description="「已收起的结果」清单里的编号。")
    kind: Literal["result", "block"] = "result"
    """取什么：`result` = 被剪掉的工具结果（默认）；`block` = 被压成摘要的对话块
    （「更早的对话」清单里的 #N）。两个编号空间各自独立，所以必须点名。"""
    max_units: int | None = Field(
        default=None,
        ge=1,
        description=(
            "最多取回多少个字（超出会截断并标注）。上下文很紧时用它买得起一部分；"
            "不传 = 整份取回。"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# 出参：**只有 NodeRef 和纯量**。`Node` / `props` 一个都不出去。
# ══════════════════════════════════════════════════════════════════════════


class ForbiddenName(BaseModel):
    """一个「第 K 章才首现」而当前 N < K 的实体：**只有名字和章号**。

    它比 `panel.constraints.ForbiddenEntity` 还窄一档——那个类型带着 `surfaces`
    （拿去和正文做正则匹配的全部别名，R2 的料）。规则要它，模型不要：多给一串别名
    只是把「这个东西还没登场」这件事说了 N 遍，而每一遍都是一次可以说漏嘴的机会。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    first_appears_chapter: int


class ConstraintsResult(BaseModel):
    """`scene_constraints` 的出参：**转译后的约束，不是原件**。

    `must_not_reveal` 是 `NodeRef`（id/label/name），**不是 `Node`**。这条不是洁癖：
    `NodeProps` 是 `extra="allow"`，作者写在秘密节点上的 `{"twist": "…第 200 章揭晓"}`
    会原样穿过 `model_dump_json()` 进对话——**保密清单自己泄密**，而且泄完就删不掉了
    （对话是持久化的）。完整论证在 `graph.models.NodeRef`。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int

    cast: list[str] = Field(default_factory=list)
    """本章正文里被提到的花名册称呼（ADR 0018：在场是数出来的，不是作者填的）。"""

    cast_derived: bool = False
    """`False` = 这一章的正文还读不到，在场是**退化值**（数不出人）。

    **零必须带着理由一起出现**（约束 8）：「这一章没人」和「我没数出这一场有谁」
    在清单上长得一模一样，而它们对作者是完全相反的两件事。
    """

    forbidden_entities: list[ForbiddenName] = Field(default_factory=list)


class StateFact(BaseModel):
    """一条 `HAS_STATE`：维度显示名 + 值 + 从第几章起。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    value: str | None = None
    since_chapter: int


class CharacterStateResult(BaseModel):
    """`character_state` 的出参。**`StateSnapshot` 在这里被收窄。**

    `StateSnapshot.node` 和 `.location` 都是完整的 `Node`——直接交出去，人物 props 里的
    `character_notes` 和地点 props 里实测过的 `{"plot_note": "萧决在此被顾清音所杀"}`
    就一起进对话了。所以两者都收成 `NodeRef`。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    character: NodeRef
    location: NodeRef | None = None
    states: list[StateFact] = Field(default_factory=list)
    is_dead: bool = False
    has_appeared: bool = True

class DraftResult(BaseModel):
    """`draft_chapter` 的出参：**认得出是哪一稿的那几样，不是那一稿本身**（ADR 0022）。

    ── 为什么这里没有 `text` ────────────────────────────────────────────────

    这份返回会原样变成一条 `tool` 消息进对话历史，而跟模型说话的接口是**无状态**的：
    每一轮把整个消息数组从头重发。一整章正文从生成那一刻起**每一轮都在被重发**，
    直到会话结束——而默认没有任何东西会去拿掉它。三稿就是 9,000 字 × 剩下的每一轮。

    所以正文落在候选表里（`agent/candidates.py`），这儿给的是 id、第几稿、字数、
    **定长预览**和**写它的那个模型自己那句自述**。要全文得再调一次 `read_draft`，
    而那一次是模型显式决定的（作者要合并两版的时候）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    draft_id: str
    """把这一稿写进书里（`save_draft`）或者读全文（`read_draft`）时报这个号。

    **别把它念给作者听**：他认得的是「第 2 稿」（`ordinal`），不是一串编号。
    """

    ordinal: int
    """这一章的第几稿。**跟作者讲话时用这个。**"""

    units: int
    preview: str
    """开头那一段，**定长**（`candidates.PREVIEW_UNITS`）。"""

    note: str = ""
    """**写这一稿的那个模型自己那句话**（「这一版更冷，删掉了那段回忆」）。

    不是引擎给的评价——引擎不给散文打分（ADR 0005）。空 = 它这次没说，这儿不替它编。
    """

    calls: tuple[ModelCallReceipt, ...] = Field(default=(), exclude=True)
    """这一稿花掉的那几笔。**`exclude=True`：它一个字都不进对话历史。**

    这不是省 token，是边界三：账单原料里躺着 `prompt_bytes`——**那是整份 prompt 的原文**，
    含约束段。把它序列化进 `tool_result` 等于把一份逐章变的约束钉进持久化的对话，
    正是边界二/六在防的东西，而且是我们自己递过去的。

    它由 `dispatch()` 从这儿取走放到 `ToolOutcome.calls` 上（**结构判断，不是一张
    「哪个工具花钱」的表**，同 `_asked_chapter`），再由 loop 交给 `ledger`。
    """


class SealSceneBriefArgs(BaseModel):
    """`seal_scene_brief` 的入参：**只有 inspection 编号和作者选择**。

    提案不重新提交：封存器从校准报告里取原始提案，重新校验全部可见性与水位。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    inspection_id: str = Field(
        min_length=1,
        description="calibrate_scene 返回的校准报告编号。",
    )
    author_choice: AuthorResolution | None = Field(
        default=None,
        description=(
            "作者看过类型化任务卡之后的三个选择之一。"
            "**没有就不传**（未确认的请求投影保持机器推演强度）。"
            "传了就必须是在作者回复之后的那一轮——系统会绑定他真正说过的那句话。"
        ),
    )
    retcon_fact_ids: tuple[str, ...] = Field(
        default=(),
        description=(
            "author_choice=RETCON_NON_SAFETY 时必须点名要推翻的旧事实"
            "（校准报告里的 item_id）。"
        ),
    )
    inferred_tension: str = Field(
        default="",
        max_length=200,
        description=(
            "RETCON 时你（Agent）对这条矛盾的带来源推演，一句话。"
            "它永远只是机器推演，不会变成确定性规则命中。"
        ),
    )


class SealResult(BaseModel):
    """`seal_scene_brief` 的出参：不可变编号 + 状态 + 渲染好的目标文字。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_id: str
    chapter: int
    status: str
    goal_spec: str
    """后端从类型项固定渲染的目标，不是 Agent 提交的散文。"""


class DraftIdArgs(BaseModel):
    """认一稿：**只有一个候选 id，没有别的**。

    `save_draft` 和 `read_draft` 共用它，而这个形状本身就是那道闸：
    **模型交不出一段正文**，它只能指着后端刚生成的某一稿说「这个」。
    合成一个「写正文（收 text）」的工具就是把 ADR 0019 边界一最硬的那半条拆掉——
    那时模型能拿任何一段字去盖作者的书。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str = Field(
        min_length=1,
        description="哪一稿（起草时返回的那个编号）。**不要把这个编号念给作者听。**",
    )


class LandingResult(BaseModel):
    """`save_draft` 的出参：**写没写成，以及为什么**。

    「我写进第 12 章了」和「你刚改过这一章，我没有覆盖它」是两句完全不同的话，
    而模型只能从这份返回里知道是哪一句（ADR 0021 的机制照旧，只是它现在是一个动作）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    landed: bool = False
    note: str = ""
    """写进哪儿了 / 为什么没写。**每一种结局都说得出口。**"""


class DraftFullText(BaseModel):
    """`read_draft` 的出参：一稿的**全文**。

    **表里最贵的一条返回**（一整章进对话，而且此后每一轮都跟着重发）。
    它存在的理由只有一个：作者说「把第一版的开头接第二版的结尾」时，模型手上得有那两段字。
    `draft_chapter` 的返回**不含**正文正是为了让这一次成为一个显式决定。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    chapter: int
    ordinal: int
    units: int
    note: str = ""
    landed: bool = False
    text: str = ""
    """一稿正文，**不含章标题**（那一行是切章的锚，属于作者）。"""

    stopped_reason: str = ""
    """**空 = 这一稿写完了**；非空 = 它被砍断了，这句话说明为什么。

    **这一位必须跟着正文一起回来，它不是元数据**（迁移 010 的注释写死了理由）：
    一段断在半句的正文，你读到它、若不知道那是被砍断的，
    **会把那个断口当成一种有意的写法去模仿**。
    「他缓缓抬起手，然后——」在小说里读起来像一个刻意的悬停。
    """


class AuthorQuestion(BaseModel):
    """模型停下来问作者的那一句 + 几个可点的选项（ADR 0024）。

    **它是一个类型，不是一个约定的字段名**——`ToolOutcome.asked` / `TurnResult.asked` /
    事件流三处都认这一个类型，所以「这一轮停在一个问题上」在结构上分得开，
    而不是靠界面去猜哪一段话是问句。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    options: tuple[str, ...] = ()


class AskAuthorResult(AuthorQuestion):
    """`ask_author` 的出参：**问句原样交回来** + 一句说给模型听的话。

    **出参逐字段等于入参**（`note` 除外）——引擎不往问句里加一个字，也没地方能加：
    这条 handler 里一个 `context.` 都没有。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    note: str = ""
    """说给**模型**听的那句：这一轮到此为止。**不是给作者的。**"""


class RememberedRule(BaseModel):
    """作者定下的一条规矩，**已经归一化、已经绑好章号**（ADR 0023 决策二）。

    **它是一个类型，不是一个约定的字段名**——`ToolOutcome.remembered` 认的是它
    （同 `AuthorQuestion`），所以加第二条会记规矩的工具那天它自动被认出来。

    `chapter` 只可能来自 `ToolContext.working_chapter`：这个类型构造不出一条没有章号的
    规矩，而**入参上根本没有那个格子**（`RememberRuleArgs`）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    chapter: int = Field(ge=1)
    until: str = ""
    """这条管到什么时候（模型自己写的一句人话，迁移 016）。**空串只可能来自老会话。**"""


class RememberRuleResult(RememberedRule):
    """`remember_rule` 的出参：**只说记在第几章**，不回吐那句话。

    ── 那一位为什么 `exclude`（这不是洁癖，是 ADR 0023 的安全方向）────────────

    出参会原样进对话历史（`ToolOutcome.content`），而工具返回只按章号**往前**筛
    （`>`，那个方向是给 `must_not_reveal` 定的）。回吐那句话的话，一条第 40 章的规矩会
    以「引擎确认过的一条结构化规矩」的形态留在第 200 章的 prompt 里——**正是 ADR 0023
    点名的那个故障**（第 200 章写不出打戏，而作者不知道为什么），只是换了个地方发生。

    模型不需要它：它上一句刚打进来的就是那句话。真正生效的那一条住在 canonical 的
    SYSTEM 消息里，**它会按章号过期**，那才是这套机制唯一的出口。

    **剩下的那半截说清楚**：模型自己那次调用的参数（`{"rule": …}`）躺在它自己的
    assistant 消息里，**引擎删不掉也不该删**（改写模型说过的话是另一种病）。
    那一档属于 ADR 0019 边界二自己列成「接受」的残余代价——是它自己的话，不是引擎的话。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str = Field(exclude=True)
    """记下的那句话。**`exclude=True` = 它不进 `content`，也就是不进对话历史**
    （同 `ToolOutcome.calls` 那条「账单原料不进对话」）。loop 从
    `ToolOutcome.remembered` 拿它——那条路不经过对话。"""

    until: str = Field(default="", exclude=True)
    """模型刚写下的那句时效。**同样 `exclude=True`，理由同上**：它下一轮会连着规矩
    一起回到 prompt 里（`rules.prompt_text`），在工具返回里再摆一遍是同一句话的第二份，
    而两份措辞一旦不一致，模型信哪一份没有答案。"""

    note: str = ""
    """说给**模型**听的那句：它管多久、同一条怎么才算同一条。**不是给作者的。**"""


class StoredResult(BaseModel):
    """`get_result` 的出参：这一轮第 `id` 条工具结果的原文。

    **它是一个类型，不是约定的字段名**——`ToolOutcome.stored` 认的是它（同
    `AuthorQuestion` / `RememberedRule`），loop 的取回预检（`_fit_stored_fetch`）靠它
    拿到大小、靠类型知道「这是一次取回」。

    **权限边界和原来的查询是同一条**：它回吐的内容本来就是那一次工具调用的出参、
    已经在 canonical 里了——`get_result` 不新开任何一条通往 `Node` / `props` /
    PLANNED 的路，只是把已经给过的东西按编号还回来。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(ge=1)
    content: str
    units: int
    """`content` 有多少字（`count_units` 口径）。取回预检拿它和「全剪后的剩余空间」比。"""


class ToolOutcome(BaseModel):
    """一次工具调用的结果。**`content` 就是要贴回对话里的那段字。**

    成功时它是出参模型的 `model_dump_json()`；失败时它是一句中文，说清楚为什么以及
    可以怎么改。两种情况都由这里生成——**运输层不认识工具，也不该认识**。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    name: str
    ok: bool
    content: str

    calls: tuple[ModelCallReceipt, ...] = ()
    """这次工具调用**自己花掉的**那几笔（今天只有 `draft_chapter` 会非空）。

    **它不进 `content`，也就是不进对话历史**——账单原料是给 `agent/loop.py` 的
    `ledger` 和成本闸看的，模型看见它只会浪费 token。这是「同一次调用两个消费者、
    两套规矩」的老形状（同 `ModelCallReceipt.completion_tokens` 那条）。

    为什么必须走出参而不是让起草侧自己记账：`ToolContext` 上没有 conn（边界一），
    起草侧自己记就得把 conn 塞回去；而且**它自己记的账 loop 看不见**，
    `TurnLimits.max_tokens` 那道闸就罩不住表里唯一一个花钱的工具。
    """

    chapter: int | None = None
    """模型在这次调用里点的那个章号。`None` = 这个工具压根不收章号，或者参数没过校验。

    **它是 agent loop 投影的过滤判据**（ADR 0019 边界五：绑在第 90 章的返回不许出现在
    第 40 章的投影里）。放在这儿而不是让 loop 自己去解析 `call.arguments`，是因为参数
    在这一层已经过了 `model_validate`——让 loop 再解析一遍就是第二处解析点，而这个仓库
    刚把「同一件事两处解析」清掉。

    取法是**结构判断**（这个入参模型上有没有一个叫 `chapter` 的整数字段），不是一张
    「哪个工具绑章号」的表：表会在加工具的那天漂，而结构不会。
    """

    asked: AuthorQuestion | None = None
    """模型这一次是**停下来问作者**（ADR 0024）。非空 ⇒ **这一轮到此为止**。

    ── 为什么判据是出参的类型，不是工具名，也不是 `ToolSpec` 上一个开关 ──────────

    「哪个工具会结束一轮」是一张会漂的表（同 `TurnLimits.max_calls_per_step` 那条
    「按工具名给闸门」）。而**一条工具的出参是不是一句问作者的话**是它自己的类型说了算
    （`isinstance(payload, AuthorQuestion)`）——加第二条会问的工具那天，它自动被认出来。

    **只在 `ok=True` 时非空**：参数不合法的那一次不是一次提问，模型该把参数改对重发，
    而不是让一次填错的调用替它结束这一轮。
    """

    remembered: RememberedRule | None = None
    """这一次记下了作者的一条规矩（ADR 0023 决策二）。非空 ⇒ **loop 要把它贴进 canonical**。

    ── 为什么它在这儿，而不是让工具自己去写 ────────────────────────────────

    `ToolContext` 上没有会话、没有连接（边界一），所以工具这一层**够不着** canonical——
    「模型改写了作者的历史」在类型上不可能，不是靠纪律。它只造出那条规矩，
    贴不贴、贴在哪儿由 `agent/loop.py` 决定（它贴在这一批工具返回**之后**，
    因为 wire 上那一批必须连着）。

    判据同 `asked`：**出参的类型**（`isinstance(payload, RememberedRule)`），不是工具名。
    **只在 `ok=True` 时非空**——没通过校验的那一次什么都没记下。
    """

    stored: StoredResult | None = None
    """这次调用是**按编号取回一条已收起的结果**（`get_result`）。非空 ⇒ loop 要
    做取回预检（装不装得下），并在装不下时把它换成拒绝。

    它不在 `content` 之外另走一条路：`content` 就是取回的那份原文，`stored` 只是让
    loop 知道「这是一次取回、原有多大」——同 `calls` 那条「同一次调用两个消费者」。
    判据同 `asked` / `remembered`：**出参的类型**，不是工具名。**只在 `ok=True` 时非空**。
    """


# 上下文（`ToolContext` / `DraftFn` / 两个只读端口）在 `ports.py`：
# **工具能碰到的东西集中在一页**，而那一页不许认识任何一个工具（否则和 `index.py` 成环）。


# ══════════════════════════════════════════════════════════════════════════
# 约束：**全模块唯一一处算它的地方**（边界二）
# ══════════════════════════════════════════════════════════════════════════


def _derived_cast(context: ToolContext, chapter: int) -> list[str]:
    """第 `chapter` 章正文里提到的花名册称呼（ADR 0018）。读不到正文就是空的。

    读盘那一半在这儿，数人那一半在 `_derived_cast_from_text`——**同一份实现**，
    这条只是替调用方把磁盘上那一章读出来。
    """
    if context.root_path is None:
        return []
    file = Path(context.root_path) / chapter_path(chapter)
    if not file.exists():
        return []
    return _derived_cast_from_text(
        context, chapter, file.read_text(encoding="utf-8-sig")
    )


def _derived_cast_from_text(
    context: ToolContext,
    chapter: int,
    text: str,
) -> list[str]:
    """正文里提到的花名册称呼，正文由调用方一次读取后传入（ADR 0033 §8.4）。

    走的是 `mentioned_cast` 那条既有实现（和 R2 FUTURE_LEAK 同一条正则 alternation，
    一个语义判断都没有）。**不在这里重新数一遍**：第二份实现会和第一份漂移。

    ── ⚠️ `expand_ambiguous=True` 的理由 2026-08-25 失效了，行为**没动** ──────

    2026-08-22 定这一档时的论证是：判据是「在场至少有一个人还不知道」，所以
    「师兄」指向 8 个人时 8 个全算，**多算只会多一批禁令**，方向仍在安全那一侧。

    **秘密下线之后（ADR 0039）没有那个判据了。** 这份 cast 今天的唯一去处是
    Writer prompt 里的【在场】那一块——8 个全算的产物不再是「多禁一批」，
    是**对模型说「这一场有这 8 个人」，而那句话是假的**。

    **行为一个字节没改，因为改它是维护者的裁定，不是这次下线顺手能做的事。**
    要动之前先回答：一个指向 8 个人的称呼，是该 8 个全报（模型可能把 7 个不在场的
    人写进去）、还是整个丢掉（真正在场的那个人从名单里消失）？两侧今天都不是
    「安全」那一侧——ADR 0018 §3 的单调性证明连同它的对象一起没了。
    """
    paras = split_paragraphs(text)
    return mentioned_cast(
        context.store, context.project_id, paras, expand_ambiguous=True
    )


def _scene_context_from_text(
    context: ToolContext,
    chapter: int,
    text: str,
) -> DraftContext:
    """从**已经读好的**目标章正文算约束（与 `_scene_context` 同一份实现）。

    ── 有正文 ⇒ cast 是**数**出来的 ⇒ 用；数不出人 ⇒ 只能**猜** ⇒ **不猜** ──────

    数不出人的那一章（最常见：刚开头的空章）退回「不知道在场是谁」，**不许拿上一章的
    名单顶上**（2026-08-23 撤销 M2-b，见 ADR 0037 补记）。当年那条论证是拿泄漏量测的
    （`synth/gate.db` 第 11 章实测 20 种组合里 15 种少禁，最坏一组从 5 条掉到 2 条），
    **那个量具随秘密下线没了**（ADR 0039）——但结论今天更直白：另一章的名单不是这一章的
    名单，把它发给模型就是说了一句假话。

    「还没开始写」= **cast 不存在**，不是「未知，需要补一个」。精确的那份留给
    保存之后的验证器——写完才数得出谁真的在场。
    """
    cast = _derived_cast_from_text(context, chapter, text)
    if cast:
        try:
            view = scene_view(context.store, context.project_id, chapter, cast)
            return ResolvedConstraints.of(view, cast)
        except UnresolvedCast:
            pass
    return unknown_cast_constraints(context.store, context.project_id, chapter)


def _scene_context(context: ToolContext, chapter: int) -> DraftContext:
    """第 `chapter` 章的约束。**模型没有机会影响这个函数的任何一个入参**（边界二）。

    出参的类型本身就是「这份约束退没退化」的答案（`draft/context.py`）：
    `ResolvedConstraints` = 数出来的人全都解析成了唯一角色；
    `UnknownCastConstraints` = 不知道这一场有谁，全禁。

    `UnresolvedCast` 在这里被接住而不是抛出去，理由是这条路径上它**不是**作者要回答的
    问题：cast 不是他填的，是引擎从正文里数的，所以能做的只有退回全禁那一侧——
    而那正是 `panel/constraints.py` 的「算不准就多禁」。作者要修的是别名表，
    不是这一次工具调用。

    **歧义已经不从这道口子掉下去了**（2026-08-22）：`_derived_cast_from_text` 把
    「师兄」展开成全部候选，展开出来的是各自唯一可解析的正式名。剩下能落到这一支的
    只有花名册本身病了的那几种（两个节点重名、节点没有 canonical 别名行），
    那时全禁仍然是唯一诚实的答案。**空 cast 那一档不归这儿**：这一章还没写出人来时
    走的是 `_scene_context_from_text` 里 `if cast:` 外面那条路——同样是全禁，
    但理由是「不存在」而不是「算不准」。
    """
    if context.root_path is None:
        return unknown_cast_constraints(context.store, context.project_id, chapter)
    file = Path(context.root_path) / chapter_path(chapter)
    if not file.exists():
        return unknown_cast_constraints(context.store, context.project_id, chapter)
    return _scene_context_from_text(
        context, chapter, file.read_text(encoding="utf-8-sig")
    )


def _target_snapshot(context: ToolContext, chapter: int) -> TargetChapterSnapshot:
    """一次读取目标章当前正文（磁盘）+ 哈希。**全链唯一的一次读。**"""
    if context.root_path is None:
        raise ToolRefused(message("no_manuscript_root", context.language))
    text = read_chapter(Path(context.root_path), chapter)
    if text is None:
        raise ToolRefused(
            message("chapter_not_yet_written", context.language, chapter=chapter)
        )
    return TargetChapterSnapshot(
        chapter=chapter,
        text=text,
        sha256=text_digest(text),
    )


def _require_author_turn(context: ToolContext) -> AuthorTurnRef:
    if context.author_turn is None:
        raise ToolRefused(message("no_author_turn", context.language))
    return context.author_turn


def _require_calibrations(context: ToolContext) -> CalibrationStore:
    if context.calibrations is None:
        raise ToolRefused(message("calibration_not_wired", context.language))
    return context.calibrations


# ══════════════════════════════════════════════════════════════════════════
# 三个处理函数
# ══════════════════════════════════════════════════════════════════════════


def _handle_scene_constraints(
    args: SceneConstraintsArgs, context: ToolContext
) -> ConstraintsResult:
    ctx = _scene_context(context, args.chapter)
    # 类型本身就是「这份约束退没退化」的答案（`draft/context.py`），所以这里问的是类型，
    # 不是某个字段——`UnknownCastConstraints` 上根本没有 `cast` 可读，退化态表示不出来。
    resolved = isinstance(ctx, ResolvedConstraints)
    return ConstraintsResult(
        chapter=args.chapter,
        cast=list(ctx.cast) if resolved else [],
        cast_derived=resolved,
        # 收窄发生在 `panel/constraints.py`（`NodeRef.of`），这里只是不要把它加回来。
        forbidden_entities=[
            ForbiddenName(
                name=entity.node.name,
                first_appears_chapter=entity.first_appears_chapter,
            )
            for entity in ctx.forbidden_entities
        ],
    )


def _handle_character_state(
    args: CharacterStateArgs, context: ToolContext
) -> CharacterStateResult:
    resolutions = context.store.resolve(context.project_id, [args.character])
    # **「查不到」和「说法不对」在这儿分成两句话**（`index.UnknownCharacter` 写着实测）：
    # 合成一句「换个说法再试」等于由引擎亲口鼓励它去烧下一步，而花名册里没有的名字
    # 换什么叫法都还是没有。判据是 `len(hits)`，一个集合判断。
    node = resolve_one(args.character, resolutions[0] if resolutions else None, context.language)
    if node.label is not NodeLabel.CHARACTER:
        # 集合判断，不是语义判断：秘密 / 地点 / 物件也在花名册里，拿它们去查「状态」
        # 会返回一份看起来正常、实际上没有意义的快照（秘密没有处境）。
        raise ToolRefused(
            message(
                "not_a_character_state",
                context.language,
                surface=args.character,
                label=node.label,
            )
        )

    snapshot = _state_at(context.store, context.project_id, node.id, args.chapter)
    return CharacterStateResult(
        chapter=args.chapter,
        character=NodeRef.of(snapshot.node),
        location=NodeRef.of(snapshot.location) if snapshot.location is not None else None,
        states=[
            StateFact(
                dimension=state.dim.name,
                value=state.value,
                since_chapter=state.since_chapter,
            )
            for state in snapshot.states
        ],
        is_dead=snapshot.is_dead,
        has_appeared=snapshot.has_appeared(),
    )


TRACK_NUDGE_HEADER: Final = "你正在改一章旧的："
"""「不是最新章」那句提醒的抬头（轨道阶段 3）。

**判断在系统这边，调用在模型那边**：系统只负责把「后面还有 N 章已经写完」说给它听，
去不去调那条工具是它自己的事——ADR 0019 的「循环归模型，不归代码」。
做成「写完必须验、验完必须改」的流水线就把那条 ADR 破了。

**这段话住在本模块而不是 `loop.py`**：它点了工具的名字，而 loop 那一侧有一条守卫
（`test_the_tool_table_is_what_gets_declared_and_the_loop_writes_no_second_copy`）
钉着「loop 自己不认识任何一个工具名」——认识一个就是第二份工具表的开头。
"""


def track_nudge(chapter: int | None, frontier: int | None) -> dict[str, str] | None:
    """不是最新章时，投影里多的那一句。**每轮重算，一个字都不落库。**

    ── 为什么它不能进对话历史 ────────────────────────────────────────────

    它绑着章号，而对话是**持久且累积的**（ADR 0019 边界六）：存进去之后作者写到
    第 200 章时，第 12 章那句「后面还有 8 章已经写完」还躺在历史里，而它已经是假话。
    稳定前缀那一侧的 validator 直接拒收绑章号的消息，理由是同一条。

    所以它跟着「已收起的结果」那条注记走同一条路：**投影时追加，用完就没了**。
    """
    if chapter is None or frontier is None or chapter >= frontier:
        return None
    return {
        "role": "system",
        "content": (
            f"{TRACK_NUDGE_HEADER}第 {chapter} 章后面还有 {frontier - chapter} 章"
            "已经写完了。动笔前先用 check_track 对一遍——它会告诉你第几句跟第几章抵触，"
            "**不会告诉你那几章写了什么**（那是这一章的读者还不该知道的）。"
            "查不查、改不改你自己定。"
        ),
    }


def _handle_check_track(args: CheckTrackArgs, context: ToolContext) -> TrackVerdict:
    """轨道核对。**这个工具有权看轨道，agent 没有**（ADR 0019 边界一的字面落点）。

    出参里只有 `TrackClash` 那三个数，轨道原文一个字都不过这条边——理由是 `track.py`
    模块头那一节：后面章节的总结里可能写着这一章的读者还不该知道的事，把它给写
    第 2 章的模型看，等于把伏笔亲手告诉它。
    """
    if context.track_check is None:
        raise ToolRefused(message("track_not_wired", context.language))
    return context.track_check(args.chapter)
def _desk(context: ToolContext) -> DraftDesk:
    """起草那一摊，或者一句「没接线」。**不假装写了一稿。**"""
    if context.drafter is None:
        raise ToolRefused(message("drafting_not_wired", context.language))
    return context.drafter


def _handle_draft_chapter(args: DraftAsk, context: ToolContext) -> DraftResult:
    desk = _desk(context)
    # **算约束要碰库，而这条工具是并发跑的**（`ToolSpec.concurrent`）——同一条连接被两条
    # 线程同时用是 `InterfaceError`，不是理论风险（`ToolContext.db_lock` 记着实测）。
    # 锁只罩这一小段；下面那次模型调用（几十秒）在锁外面，并发的收益全在那儿。
    with context.db_guard:
        calibrations = _require_calibrations(context)
        author_turn = _require_author_turn(context)
        # **一次读取目标章当前正文快照**（ADR 0033 §8.4）：安全 cast、水位校验、
        # Writer 当前章、候选 base_sha256 全用同一个对象，禁止各自重读磁盘。
        snapshot = _target_snapshot(context, args.chapter)
        try:
            sealed = calibrations.require_draftable(
                args.calibration_id,
                project_id=context.project_id,
                chapter=args.chapter,
                author_turn_id=author_turn.turn_id,
                author_request_sha256=author_turn.request_sha256,
                target_sha256=snapshot.sha256,
                canon_version=context.store.canon_version(context.project_id),
            )
        except (CalibrationNotFound, CalibrationRefused) as exc:
            raise ToolRefused(str(exc)) from exc
        ctx = _scene_context_from_text(context, args.chapter, snapshot.text)
    product = desk.write(
        args,
        ctx,
        goal=sealed.goal_spec,
        brief=sealed.scene_brief,
        snapshot=snapshot,
    )
    candidate = product.candidate
    return DraftResult(
        chapter=args.chapter,
        draft_id=candidate.id,
        ordinal=candidate.ordinal,
        units=candidate.units,
        preview=candidate.preview,
        note=candidate.note,
        calls=product.calls,
    )


def _handle_calibrate_scene(
    args: SceneProposal,
    context: ToolContext,
) -> CalibrationReport:
    """校准层：确定性证据 + 结构冲突 + 覆盖，**不做语义判断**。"""
    calibrations = _require_calibrations(context)
    author_turn = _require_author_turn(context)
    with context.db_guard:
        snapshot = _target_snapshot(context, args.chapter)
        input_ = CalibrationInput(
            store=context.store,
            project_id=context.project_id,
            author_turn=author_turn,
            target_snapshot=snapshot,
            canon_version=context.store.canon_version(context.project_id),
            root_path=context.root_path,
            summaries=context.summaries,
            events=context.events,
        )
        report = calibrate_scene(input_, args)
        return calibrations.save_inspection(report)


def _handle_seal_scene_brief(
    args: SealSceneBriefArgs,
    context: ToolContext,
) -> SealResult:
    """封存器：重新校验全部引用与水位，签发不可变 `calibration_id`。"""
    calibrations = _require_calibrations(context)
    author_turn = _require_author_turn(context)
    with context.db_guard:
        inspection = calibrations.get_inspection(context.project_id, args.inspection_id)
        if inspection is None:
            raise ToolRefused(
                message(
                    "no_such_inspection", context.language, inspection_id=args.inspection_id
                )
            )
        snapshot = _target_snapshot(context, inspection.chapter)
        canon_version = context.store.canon_version(context.project_id)
        confirmation = None
        if args.author_choice is not None:
            if author_turn.turn_id == inspection.author_turn_id:
                raise ToolRefused(message("author_not_confirmed_yet", context.language))
            confirmation = author_turn
        if inspection.proposal is None:
            raise ToolRefused(message("no_saved_proposal", context.language))
        try:
            sealed = seal_scene_brief(
                store=context.store,
                proposal=inspection.proposal,
                inspection=inspection,
                author_turn=author_turn,
                confirmation_turn=confirmation,
                author_choice=args.author_choice,
                canon_version=canon_version,
                target_sha256=snapshot.sha256,
                retcon_fact_ids=args.retcon_fact_ids,
                language=context.language,
            )
        except SealRefused as exc:
            raise ToolRefused(str(exc)) from exc
        sealed = calibrations.save_sealed(sealed)
        if args.author_choice is AuthorResolution.RETCON_NON_SAFETY:
            facts_by_id = {f.item_id: f for f in inspection.agent_safe_facts}
            handoff = build_retcon_handoff(
                sealed=sealed,
                proposal_item=(
                    f"{inspection.id}:{args.retcon_fact_ids[0]}"
                    if args.retcon_fact_ids
                    else inspection.id
                ),
                inferred_tension=args.inferred_tension,
                referenced_facts=tuple(
                    ReferencedFact(
                        fact_id=fact_id,
                        kind=facts_by_id[fact_id].kind,
                        fact_type=facts_by_id[fact_id].fact_type,
                        valid_from_chapter=facts_by_id[fact_id].valid_from_chapter,
                        valid_to_chapter=facts_by_id[fact_id].valid_to_chapter,
                    )
                    for fact_id in args.retcon_fact_ids
                    if fact_id in facts_by_id
                ),
            )
            calibrations.push_handoff(handoff)
        return SealResult(
            calibration_id=sealed.calibration_id,
            chapter=sealed.chapter,
            status=sealed.status.value,
            goal_spec=sealed.goal_spec,
        )


def _handle_save_draft(args: DraftIdArgs, context: ToolContext) -> LandingResult:
    report = _desk(context).land(args.draft_id)
    return LandingResult(chapter=report.chapter, landed=report.landed, note=report.note)


ASK_ACKNOWLEDGED: Final = (
    "这个问题已经摆到作者面前了，这一轮到此为止——他答完你会看见他那句回答，"
    "那时再接着往下走。别在同一轮里替他先假定一个答案。"
)
"""`ask_author` 交回给**模型**的那句话。**它不上屏**（给作者看的那一句在 `loop.py`）。

最后一句不是客套：模型在同一轮里既问又猜，作者就会同时收到一个问题和一份照猜写出来的稿子
——而那正是 ADR 0024 要这条工具去掉的东西。**结构上也堵住了**（这一轮当场结束），
这句话是给它的解释，免得它把「没写成」理解成一次失败去重试。
"""


def _handle_ask_author(args: AskAuthorArgs, context: ToolContext) -> AskAuthorResult:
    """把问句原样交回来。**这里一个 `context.` 都没有，所以引擎加不进一个字。**

    参数 `context` 收着不用是有意的：工具表的形状是 `(args, context)`，为这一条破例
    会让 `ToolSpec.handler` 的类型分叉。**它不被用到本身就是那条边界的执行者**。
    """
    return AskAuthorResult(
        question=args.question, options=args.options, note=ASK_ACKNOWLEDGED
    )


def _handle_read_draft(args: DraftIdArgs, context: ToolContext) -> DraftFullText:
    stored = _desk(context).recall(args.draft_id)
    return DraftFullText(
        draft_id=stored.id,
        chapter=stored.chapter,
        ordinal=stored.ordinal,
        units=stored.units,
        note=stored.note,
        landed=stored.landed,
        text=stored.body,
        # **一稿被砍断过这件事跟着正文一起回来。** 漏掉它的形态不是报错，
        # 是模型照着那个断口继续写（见 `DraftFullText.stopped_reason`）。
        stopped_reason=stored.stopped_reason,
    )


RULE_ACKNOWLEDGED: Final = (
    "记下了。**它不会自己过期**：以后每一轮你都会看到它，前面标着作者是写第几章时说的。"
    "每次读到它，自己判断那个情境还在不在——不在了就当它不存在，别硬套。"
    "他再说一遍你就再记一遍，**同一条得用一模一样的措辞**（是按字比的，"
    "换个说法就成了两条，两条都会跟着你走）。"
)
"""`remember_rule` 交回给**模型**的那句话。**它不上屏。**

两半都不是客套：

- **「不会自己过期」**是 2026-08-14 换掉的机制（[ADR 0028]）。从前它按章号作废，
  而章号从来不是有效期——「男主在这片沙地别杀人」跟第几章没关系。
- **「同一条得用一模一样的措辞」**：去重的判据是 `rules.rule_key` 归一化之后
  **字节相等**（ADR 0005：「这两句是不是一个意思」是语义判断，引擎不做）。
  换个说法就多一条，而条数是有上限的（`RULE_KEEP_MAX`）——挤掉的是最早那几条。
"""


def _handle_remember_rule(args: RememberRuleArgs, context: ToolContext) -> RememberRuleResult:
    """把作者刚定下的那条规矩造出来。**这一层不写它**（见 `ToolOutcome.remembered`）。

    **章号在这一行里被绑死**：它来自 `context.working_chapter`，模型碰不到、作者也填不了。
    `rule_message` 的三种拒绝（没有坐标 / 空 / 超长）全是 `ValueError`，`dispatch` 会把
    它们变成一条 `ok=False` 的返回贴回对话——那几句本来就是写给模型看的中文。
    """
    from .rules import rule_message  # 断环，同 `loop.project()`

    message = rule_message(args.rule, chapter=context.working_chapter, until=args.until)
    return RememberRuleResult(
        rule=message.content,
        until=message.rule_until,
        # `rule_message` 保证它不是 `None`（没有坐标那一支已经拒了）；真漏了的话
        # 这儿是一次 `ValidationError`（也是 `ValueError`）⇒ 同样变成一条拒绝，
        # **而不是一条章号为空的规矩**。
        chapter=message.chapter,
        note=RULE_ACKNOWLEDGED,
    )


def _handle_get_result(
    args: GetResultArgs,
    context: ToolContext,
    stored: dict[int, str] | None,
    blocks: dict[int, str] | None,
) -> StoredResult:
    """按编号把这一轮里已收起的工具结果 / 已压缩的对话块原文取回来。

    `stored` / `blocks` 是 loop 造、随轮即焚的只读表（同 `TurnMemo` 的位置）：它们不住在
    `ToolContext` 上，因为那是能力闸不是杂物抽屉，而这一轮的 canonical 在 loop 手里。
    `None` = 没有这一轮的表（CLI / 直接派发）⇒ fail-closed，不猜。
    """
    table = blocks if args.kind == "block" else stored
    if table is None:
        raise ToolRefused(message("no_collapsed_result_table", context.language))
    content = table.get(args.id)
    if content is None:
        raise ToolRefused(message("no_such_result_id", context.language, id=args.id))
    if args.max_units is not None and len(content) > args.max_units:
        content = content[: args.max_units] + message("truncated_marker", context.language)
    return StoredResult(
        id=args.id,
        content=content,
        units=count_units(content, DraftLanguage.ZH),
    )


def _get_result_without_store(args: GetResultArgs, context: ToolContext) -> BaseModel:
    """防御：正常链路永远走 `dispatch` 的 `get_result` 分支；这条只防绕过了那道闸的调用。"""
    raise ToolRefused(message("no_collapsed_result_table", context.language))


# ══════════════════════════════════════════════════════════════════════════
# 工具表本身
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ToolSpec:
    """表里的一行。`args` 同时是**校验器**和**发给模型的 schema 的来源**——
    两个身份一份定义，所以「声明说收 A、实现却读 B」在结构上不可能。"""

    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[Any, ToolContext], BaseModel]

    label: str = ""
    """**说给小说作者听的那半句**（「读一章正文」），事件流拿它拼「正在……」。

    ── 为什么它在这张表上，而不在事件那一层的一张对照表里 ────────────────────

    对照表会在加工具的那天漂，而漂掉的形态是**作者的屏幕上出现一个工具名**
    （`character_chapters`）——snake_case 摆在「用 WPS 不想碰命令行」的人脸上。
    写在这一行，加工具的人就在同一个地方被问到「这件事怎么跟他说」。

    **空 = 没写**，`tool_label()` 那时给一句不认字的通用说法，**绝不原样回吐工具名**
    （同 `stop_wording()` / 前端 `KIND_LABEL` 认不出 `DecisionKind` 时的兜底，
    见 `frontend/src/backendMessages.ts`）。空着不是一个可选项：
    `tests/test_agent_events.py` 会红。
    """

    concurrent: bool = False
    """这一条能不能和同一批里的别几条**同时跑**（`BatchRunner`）。**默认 False = fail-closed。**

    判据**不是「它读还是写」，是两条一起**：

    1. **跑一遍和跑三遍对世界的影响相同**（它不动书、不动 canon、不改任何共享状态）；
    2. **它慢**——慢到值得为它多担一份线程的心。

    今天只有 `draft_chapter` 两条都满足：ADR 0022 把落盘拆出去之后它没有副作用了，
    而它是表里唯一一个每次要跑几十秒的（一次真的模型调用）。索引那几层是纯读，
    但它们快，并发它们只是把线程安全的面积白白摊大。

    **`save_draft` 永远是 False**：它写作者的书。它在批里是一道**屏障**——
    前面那几条并发跑完了才轮到它，它跑完了后面的才开始（见 `BatchRunner`）。
    """


TOOL_TABLE: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="scene_constraints",
        description=(
            "查第 N 章哪些实体还没登场。"
            "返回的是实体的首现章号。"
        ),
        args=SceneConstraintsArgs,
        handler=_handle_scene_constraints,
        label="查这一章不许说破什么",
    ),
    ToolSpec(
        name="character_state",
        description=(
            "查某个人在第 N 章的处境：在哪、各状态维度的值、登场了没有、是不是已经死了。"
            "**只认花名册上的名字**（book_index 里那份）：不在上面的人查不到，"
            "而那不是你说法不对——换个叫法再查一次也还是查不到，只是白花一步。"
        ),
        args=CharacterStateArgs,
        handler=_handle_character_state,
        label="查一个人此刻的处境",
    ),
    ToolSpec(
        name="draft_chapter",
        description=(
            "起草第 N 章的一稿。**先 calibrate_scene + seal_scene_brief 拿到"
            " calibration_id，再调本工具**——起草目标只从那个不可变产物读取，"
            "你传不了、也不需要传任何目标文字。**这一步不动书**：稿子存在一边，返回里给你它的编号、"
            "字数、开头的一段，以及写它的那个模型自己说的一句话。"
            "方向清楚就写一稿、接着调 save_draft 存进去（不用问作者，他随时能退回去）；"
            "方向不清楚就一次要几稿，把它们的自述摆给作者挑——**同一批里的几稿会同时写**，"
            "不比一稿慢多少。"
            "**不要传约束**：不许说破什么由后端按这个章号当场重算，"
            "你上一轮看到的清单对这一章可能已经过期。"
        ),
        args=DraftAsk,
        handler=_handle_draft_chapter,
        label="写一稿",
        # 表里唯一一条又慢又没有副作用的工具 —— 见 `ToolSpec.concurrent`。
        concurrent=True,
    ),
    # ── 书内索引（L0 → L3，越往下越贵）。规格在 `index.py` 的模块 docstring ──────
    #
    # **追加在表尾，不插在前面。** `tool_declarations()` 按表序生成，而按边界六它是少数
    # 几个能进稳定前缀的东西之一——在中间插一条会把整段前缀的缓存作废。
    ToolSpec(
        name="book_index",
        description=(
            "全书目录：章标题一览 + 花名册（人物 / 地点 / 门派 / 物件的**显示名**）。"
            "**先调这个再往下钻**，它是最便宜的一层。"
        ),
        args=BookIndexArgs,
        handler=handle_book_index,
        label="翻这本书的目录",
    ),
    ToolSpec(
        name="character_chapters",
        description=(
            "某几个人出现在哪些章。给两个人就是求交集——「他第一次见她是哪章」问的就是这个，"
            "而这是一次确定性的集合运算，不是搜索。返回两条分开的轴："
            "正文里同时被提到的章、以及同一条已确认事件里同时在场/知情的章。"
            "**每条轴会自己说它瞎没瞎**（读不到正文 / 抽取没跑过），别把 0 当成「没发生过」。"
        ),
        args=CharacterChaptersArgs,
        handler=handle_character_chapters,
        label="找这几个人同时出现的章",
    ),
    ToolSpec(
        name="chapter_summaries",
        description=(
            "指定章号区间的章节摘要（每章一段，机器生成的背景，不是作者确认的事实）。"
            "区间由你给——先用 book_index / character_chapters 定位到大概哪一段，再拉这一段。"
            "返回会明说**哪几章有正文却没生成过摘要**（索引在那几章是瞎的），"
            "以及区间太长时哪一段没给。"
        ),
        args=ChapterSummariesArgs,
        handler=handle_chapter_summaries,
        label="读这几章的摘要",
    ),
    ToolSpec(
        name="chapter_text",
        description=(
            "读一整章的正文原文，**最贵的一层**，确定要看哪一章之后再调。"
            "正文从磁盘上的稿子读，也就是作者此刻看见的那一份。"
        ),
        args=ChapterTextArgs,
        handler=handle_chapter_text,
        label="读一章正文",
    ),
    # ── 起草那一摊的另外两半（ADR 0022）。**追加在表尾，尽管它俩是 `draft_chapter` 的
    # 同伙**：按边界六，声明是能进稳定前缀的东西之一，在中间插一条会把它后面整段的
    # 缓存作废——那不是错误，是白付一次全量 token，而且没有任何东西会提示。
    ToolSpec(
        name="save_draft",
        description=(
            "把某一稿写进它那一章，**不用问作者**——他随时能在版本历史里退回去。"
            "只收稿子的编号：你没法拿别的文本去盖一章。"
            "返回里会说清楚存没存进去：作者在这中间改过那一章、或者那一章还不存在"
            "（新的一章要他自己起标题），都不会覆盖，那时把稿子读给他听、让他决定。"
        ),
        args=DraftIdArgs,
        handler=_handle_save_draft,
        label="把稿子存进那一章",
    ),
    ToolSpec(
        name="read_draft",
        description=(
            "按编号把某一稿的全文读回来。**很贵**（一整章会一直留在你的上下文里），"
            "只在真的要动那些字的时候调——比如作者说「把第一稿的开头接第二稿的结尾」。"
            "只想知道是哪一版的话，起草时给过的那句自述和开头一段就够了。"
        ),
        args=DraftIdArgs,
        handler=_handle_read_draft,
        label="把那一稿的全文取回来",
    ),
    # ── 问作者（ADR 0024）。**追加在表尾**，理由同上面那两条。
    ToolSpec(
        name="ask_author",
        description=(
            "停下来问作者一句，给他几个能直接点的选项。**说完这一句你这一轮就结束了**，"
            "他答完你会看见他的回答——所以别在同一轮里又问又猜。\n"
            "**什么时候问**：只在「不问就得猜，而猜错了他看不出来」的时候。"
            "那种事只有一类——答案在他脑子里，书里查不到："
            "这一场他想不想让某个人知道那件事、这条线往哪个方向收、两个版本里他要哪一个。\n"
            "**什么时候不问**：书里查得到的（谁在哪、哪章说过什么、上一章怎么写的）自己去查；"
            "改一改就能重来的小事（语气、长短）自己定，写完他看得见。"
            "每一轮都问他一句，等于把想事情这件事退回给他。\n"
            "**叫这个工具的同一步里，先用一句话交代背景**（你查到了什么、卡在哪儿）："
            "那句话进对话记录，问句和选项另外摆成一张卡。"
        ),
        args=AskAuthorArgs,
        handler=_handle_ask_author,
        label="问你一句",
    ),
    # ── 记规矩（ADR 0023 决策二）。**追加在表尾**，理由同上面那几条。
    ToolSpec(
        name="remember_rule",
        description=(
            "作者提了一条**怎么写**的要求（「别写打斗」「冷一点」「男主在这片沙地不杀人」），"
            "把它记下来。记下的那条以后每一轮你都看得见。\n"
            "**把「什么时候不算数了」写进那句话里**，用他自己的说法。"
            "他说「男主在这片沙地别杀人」，就照这么记——你以后读到它，"
            "自己看男主还在不在那片沙地；不在了就当它不存在，不必守。"
            "系统**不会**替你按章号把它作废（那是 2026-08-14 撤掉的机制）："
            "一条规矩活多久，由你每次读到它时判断。\n"
            "**只记怎么写，不记书里发生了什么**：人物、关系、谁知道什么那些是书里的事实，"
            "有它们自己的地方，从这儿进去只会变成一句谁也查不到的话。\n"
            "**同一条要用一模一样的措辞**：换个说法就成了两条，两条都会跟着你走。\n"
            "**没有章号这个参数**：记在第几章由这本书此刻打开的地方决定，你传不进来，"
            "他也不填。他还没停在任何一章上时这一次会被退回来——那时先接着聊，别硬记。\n"
            "**别每句话都记**：他随口的一句评价不是规矩，记多了等于给他攒了一堆他不知道"
            "自己定过的规矩——而他看不见这张单子。"
        ),
        args=RememberRuleArgs,
        handler=_handle_remember_rule,
        label="记下你刚说的那条规矩",
    ),
    # ── 按编号取回这一轮被收起的结果（2026-08-15 设计，docs_dev 快照）。**追加在表尾**，
    # 理由同上面那几条：声明是稳定前缀的一部分，插在中间会把整段前缀的缓存作废。
    ToolSpec(
        name="get_result",
        description=(
            "把这一轮里**已收起**的某一条工具结果按编号取回来（编号和一行描述见上下文"
            "末尾的「已收起的结果」清单），或把**被压成摘要的对话块**按编号取回整块原文"
            "（编号见「更早的对话」清单，取块要传 kind=\"block\"）。只读，不重新执行任何查询。\n"
            "**取回的内容会重新占用上下文**：编号对应的内容之前被剪掉正是因为装不下，"
            "取回来之前系统会先量一次——装不下会拒绝并告诉你差多少，那时用 "
            "get_result(id, max_units=N) 只取一部分，或把要问的说得短一点，"
            "或重查一个更小的范围。\n"
            "**编号只活这一轮**：换了一轮就按正常方式重新查。"
        ),
        args=GetResultArgs,
        handler=_get_result_without_store,
        label="取回刚查过的那份内容",
    ),
    # ── 写前校准（ADR 0033）。**追加在表尾**，理由同上面那几条。
    ToolSpec(
        name="calibrate_scene",
        description=(
            "写前校准：把你对作者当前要求的结构化理解（预计人物 + 封闭指令码 + "
            "视角/风格码）拿去按第 N 章时点核对真实数据。返回一份详细报告："
            "已解析人物、带来源的状态/关系/知识/事件/摘要、确定性冲突、未知项和"
            "覆盖回执。\n"
            "**只做确定性核对，不做语义判断**：报告不会替你判断「作者这句话和旧事实"
            "冲不冲突」——那种张力是你的 `MACHINE_INFERENCE`，需要时用 ask_author 问。\n"
            "**这是起草的前置步骤**：先调它，再视情况 ask_author，再 seal_scene_brief，"
            "最后才 draft_chapter。"
        ),
        args=SceneProposal,
        handler=_handle_calibrate_scene,
        label="写前校准",
    ),
    ToolSpec(
        name="seal_scene_brief",
        description=(
            "把 calibrate_scene 的校准报告封存成不可变写作简报，拿到 calibration_id。"
            "封存时后端重新校验每一个事实引用、可见性和全部水位；"
            "返回的目标文字由后端从类型项固定渲染，你不需要手抄任何东西。\n"
            "**作者确认**：如果你想把这稿的要求标成「作者确认」，必须先把类型化任务卡"
            "摆给作者、等他下一句回复（这一轮结束），下一轮再带着他的选择来封存。"
            "没确认就封存也可以——那批指令会标成机器推演进 Writer。\n"
            "**推翻旧设定**：只有作者明确选了推翻非安全旧设定才传 "
            "author_choice=RETCON_NON_SAFETY + retcon_fact_ids。"
        ),
        args=SealSceneBriefArgs,
        handler=_handle_seal_scene_brief,
        label="封存写作简报",
    ),
    # ── 轨道核对（2026-08-23，轨道阶段 3）。**追加在表尾**，理由同上面那几条。
    #
    # 这一条是 ADR 0019 边界一「工具表就是权限边界」在这份文件里**字面成立**的地方：
    # **工具有权看轨道，agent 没有。** 出参锁死在 `TrackClash` 那三个数上
    # （第几句 / 跟第几章 / 冲突类型），多一个自由文本的 `reason` 就等于给轨道原文
    # 开了一条进出参的路，而那条路一开，「写的那个看不见验的那个看见的东西」当场破功。
    #
    # **做成工具而不是「写完必须验、验完必须改」的流水线**：ADR 0019 的
    # 「循环归模型，不归代码」保住了——验不验、改不改，它自己定。
    ToolSpec(
        name="check_track",
        description=(
            "拿第 N 章**已经落盘的正文**去跟后面那些已经写完的章对一遍，"
            "看有没有跟既有设定抵触。**只告警，不阻断**——改不改你自己定。\n"
            "**什么时候用**：你在改一章旧的（后面还有已经写完的章）。"
            "在最前沿写时它一步都不走，会直接告诉你「这儿是最前沿」。\n"
            "**它给你的是三个数**：第几句、跟第几章抵触、哪一类抵触。"
            "**不会告诉你后面那几章写了什么**——那是这一章的读者还不该知道的东西，"
            "拿到了你也不许写进正文。要细节请作者自己去翻那一章。\n"
            "空清单要连着那句话一起读：「没抵触」和「压根没核对」不是一回事。"
        ),
        args=CheckTrackArgs,
        handler=_handle_check_track,
        label="跟后面已经写完的章对一遍",
    ),
)
"""**模式二的权限边界。这张表以外的能力，模型一律没有。**

加一条之前先回答 ADR 0019 边界一那个问题：它的出参里有没有任何一条路径能走到
`Node` / `node.props` / PLANNED 边的内容？有就不许加——工具一旦把秘密交出去过，
那段话已经在作者的持久化对话里了，**改代码删不掉**。

`get_result` 的那一栏答案是「没有新路」：它回吐的是**已经**给过、已经躺在 canonical
里的工具返回（`dispatch` 的 `get_result` 分支拿 loop 的只读表，见 `_handle_get_result`），
不是新开一条通往图数据的路。
"""

TOOLS: Final[dict[str, ToolSpec]] = {spec.name: spec for spec in TOOL_TABLE}

TOOL_NAMES: Final[frozenset[str]] = frozenset(TOOLS)

UNNAMED_TOOL_LABEL: Final = "查一样东西"
"""表里认不出的那个名字，跟作者怎么说。**认不出的绝不原样回吐**（同 `stop_wording()`）。

这一条不是防御性编程，它堵的是一个真的通路：**工具名是模型打进来的字**
（`dispatch` 的第一种失败就是「模型幻想出来的名字」）。原样摆上屏 = 屏幕上出现一串
snake_case，往坏里说 = 模型编的任意一段话直接进了作者的界面。
"""


def tool_label(name: str) -> str:
    """这次调用跟**小说作者**怎么说。**措辞的唯一出处**，事件那一层不许再翻一遍。"""
    spec = TOOLS.get(name)
    return spec.label if spec is not None and spec.label else UNNAMED_TOOL_LABEL


def tool_declarations(language: DraftLanguage = DraftLanguage.ZH) -> list[dict[str, Any]]:
    """OpenAI 兼容的 function schema，**由 `TOOL_TABLE` 生成**。

    直接喂 `draft.provider.complete(..., tools=...)`。手写第二份的诱惑在于「schema 里
    想多写两句提示」——那两句会和 `ToolSpec.description` 各自演化，而**模型只看得见
    发出去的那一份**，于是表变成了一份没人执行的文档。要改提示就改表。

    这份声明是**跨章不变**的，因此它是 ADR 0019 边界六里少数几个能进稳定前缀的东西之一
    （约束不能进——它逐章变，缓存它就是把一条过期的禁令钉死在 context 里）。

    ⚠️ **它不是「稳定前缀」本身**（国际化第三批踩出来的区分）：开会话那一步只把
    `AGENT_SYSTEM_PROMPT` + `write_rule` 落库、读回时不重算（会话存储那一层的
    建会话方法）；这份声明**每一轮都从当前 `TOOL_TABLE` 现算**（`loop.py` 里三处
    调用点都是这样），从不落库。
    所以它没有「老会话冻住了」这重保护——`language` 变了，下一轮就是新的一套。
    实际影响为零：中文书的输出逐字节不变（`TOOL_TABLE` 一个字都没改，`translate_
    tool_declarations` 对 ZH 直接原样返回），会变的只有英文书，而它昨天才刚支持。

    `language` 只换 `translate_tool_declarations()` 那一层的措辞，`TOOL_TABLE`
    本身（name / args / handler）跟语言无关，一个字都不读这个参数。
    """
    declarations = [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.args.model_json_schema(),
            },
        }
        for spec in TOOL_TABLE
    ]
    return translate_tool_declarations(declarations, language)


# ══════════════════════════════════════════════════════════════════════════
# 一轮之内的短记性：**只记「花名册里没有的那几个称呼」**
# ══════════════════════════════════════════════════════════════════════════


class TurnMemo:
    """这一轮里，模型拿哪几个称呼撞过空（`index.UnknownCharacter`）。

    ── 它是什么，以及它**不是**什么 ────────────────────────────────────────

    它是一个**只增不减的字符串集合**，一轮一个，跑完就扔。判据是「这个称呼在这一轮里
    解析失败过」——一个集合判断，一个语义判断都没有（铁律 2 / ADR 0005）。

    它**不在 `ToolContext` 上**，是显式传进 `dispatch` 的。这不是嫌麻烦：`ToolContext`
    上的每一个字段都是一道能力闸（ADR 0019 边界一），而这个东西不给模型任何新能力——
    它记的全是模型自己刚打进来的字。挂上去会让那一页从「模型能碰到什么」变成
    「这一轮的杂物抽屉」，而那一页的价值全在于它短。

    ── 为什么它不缓存「查到了什么」，也不短路第二次查询 ──────────────────────

    「同一个称呼在一轮里被查第二次时，直接把上次的答案还给它」听起来能省一步，
    **但它一步都省不下来**：一步 = 一次模型调用 + 它这一次要的那批工具
    （`TurnLimits.max_steps` 数的是模型调用）。第二次查询之所以发生，是因为模型又发了
    一次请求——那次调用的钱在工具跑起来**之前**就付掉了。缓存省下的只有一次几毫秒的
    库往返，而它要付的代价这个仓库已经写死过一次（`index.py` 说 L1 为什么不缓存）：

    > 一份缓存的命中表会在他保存的那一刻变成一个**看起来正常的错误答案**。

    作者在另一个窗口里把那个新角色加进人物卡，是这一轮里真会发生的事。那时被短路掉的
    第二次查询会拿一句「花名册里没有」去盖住一个已经有了的人，**而它和一次正常的返回
    长得一模一样**。所以这里的规矩是**记，但不挡**：查询照跑，只有在它**又一次**真的
    失败之后，才把这一轮的失败清单附在回话后面。方向说死——宁可多查一次（多几毫秒），
    绝不挡掉一次本来能成功的查询。

    ── 为什么有锁 ────────────────────────────────────────────────────────

    `dispatch` 会跑在 `BatchRunner` 的工作线程上（ADR 0022）。今天没有一条会撞空的工具
    是 `concurrent=True` 的，所以实测碰不到——但「今天恰好碰不到」不是一条能留给下一个人
    的保证，而漏掉的形态是一个丢了几条记录的集合，**它不会报错**。
    """

    __slots__ = ("_lock", "_unknown")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # `dict` 当有序集合用：摆给模型看的时候顺序就是它自己试过的顺序，
        # 而「我按什么顺序试的」是它读得懂的东西，`set` 那种随机序不是。
        self._unknown: dict[str, None] = {}

    def note_unknown(self, surface: str) -> tuple[str, ...]:
        """记下一个撞空的称呼，返回**这一轮到此为止**撞空过的全部（含这一个，按出现序）。"""
        with self._lock:
            self._unknown[surface] = None
            return tuple(self._unknown)

    @property
    def unknown_names(self) -> tuple[str, ...]:
        """这一轮撞空过的那几个称呼。**`len()` 就是 loop 那道闸的判据。**"""
        with self._lock:
            return tuple(self._unknown)


def _already_missed(names: Sequence[str], language: DraftLanguage) -> str:
    """撞空过的那几个摆给模型看。**这是一句集合的复述，不是一句评价。**

    第一次撞空不说这句（`len < 2`）：那一次它还不知道这本书的花名册有多严，
    `UnknownCharacter` 那句话已经把该说的说完了。**第二次开始才说**——「你在这上面
    已经花掉两步了」是一个它自己算不出来的事实（它看得见历史，但不会去数），
    而这一层能给的最有用的东西就是这个数。
    """
    if len(names) < 2:
        return ""
    # 说的是**几个名字**不是几次：同一个名字查两遍在集合里只算一个，而这句话要是说
    # 「撞了 2 次」就成了一句可以被人指出来是错的话——这一层的每一句都得经得起对账。
    separator = "、" if language is DraftLanguage.ZH else ", "
    return message(
        "already_missed", language, count=len(names), names=separator.join(names)
    )


# ══════════════════════════════════════════════════════════════════════════
# 派发：**`json.loads` 在这里，闸也在这里**
# ══════════════════════════════════════════════════════════════════════════


def _refused(
    call: ToolCall,
    message: str,
    chapter: int | None = None,
    calls: tuple[ModelCallReceipt, ...] = (),
) -> ToolOutcome:
    """一条 `ok=False` 的返回。**`calls` 默认空，但不许写死成空**——见 `ToolRefused.calls`：
    起草那一档可以在「已经花过一次钱」之后才拒。"""
    return ToolOutcome(
        call_id=call.id, name=call.name, ok=False, content=message, chapter=chapter, calls=calls
    )


def _billed_calls(payload: BaseModel) -> tuple[ModelCallReceipt, ...]:
    """这次工具调用自己花掉的那几笔。**结构判断，不是一张表**（同 `_asked_chapter`）。

    出参上有一个叫 `calls` 的 `ModelCallReceipt` 元组就取走它，没有就是没花钱。
    一张「哪个工具花钱」的表会在加工具的那天漂，而漂掉的症状是**一笔账凭空消失**、
    成本闸同时失明——两个都不会有任何东西报错。
    """
    value = getattr(payload, "calls", ())
    if isinstance(value, tuple) and all(isinstance(item, ModelCallReceipt) for item in value):
        return value
    return ()


def _asked_chapter(args: BaseModel) -> int | None:
    """这次调用点的是第几章。**结构判断，不是一张表**（见 `ToolOutcome.chapter`）。

    `bool` 是 `int` 的子类，所以显式排掉——一个叫 `chapter` 的布尔字段会被当成第 1 章，
    而那是一个看起来完全正常的错误答案。
    """
    value = getattr(args, "chapter", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _answered_chapter(payload: BaseModel) -> int | None:
    """出参自己说的那个章号。**只在入参里没有章号时才问它**（同一条结构判断的另一半）。

    ADR 0022 之后表里多了两条**入参里只有一个稿子编号**的工具（`save_draft` /
    `read_draft`），而 `read_draft` 的返回里躺着一整章正文。只看入参的话它们的
    `chapter` 恒为 `None` ⇒ 投影一条都不筛 ⇒ 一稿第 200 章的正文会跟着模型回头写第 40 章
    （边界五那条「等着发生的跨章泄漏」）。所以出参上有一个叫 `chapter` 的整数就取它。

    **不许反过来盖掉入参那个数**：模型点的是哪一章由它自己说了算，出参跟入参不一致的
    那天（今天没有这样的工具）该红的是测试，不是让投影按另一个坐标去筛。
    """
    return _asked_chapter(payload)


def _asked_question(payload: BaseModel) -> AuthorQuestion | None:
    """这次调用是不是**停下来问作者**（ADR 0024）。**判据是类型，不是字段名。**

    和 `_billed_calls` 那条鸭子判断有意不同：一个恰好有 `question` 字段的出参（比如
    以后某个「他问过什么」的查询）不该被当成一次提问——那会让这一轮凭空结束，
    而作者看到的是「它问了你一句」却没有问题。类型判断没有这个歧义，
    而且加第二条会问的工具那天它自动被认出来（`AuthorQuestion` 的子类）。

    **收窄成 `AuthorQuestion` 再交出去**：`AskAuthorResult` 上还有一句给模型的话
    （`note`），那句不该跟着问题摆到作者面前。
    """
    if not isinstance(payload, AuthorQuestion):
        return None
    return AuthorQuestion(question=payload.question, options=payload.options)


def _remembered_rule(payload: BaseModel) -> RememberedRule | None:
    """这次调用有没有记下一条规矩（ADR 0023 决策二）。**判据是类型**，同 `_asked_question`。

    **收窄成 `RememberedRule` 再交出去**：`RememberRuleResult` 上还有一句给模型的话
    （`note`），loop 拿它去造 canonical 里那条消息时不该把那句也带上。
    """
    if not isinstance(payload, RememberedRule):
        return None
    return RememberedRule(rule=payload.rule, chapter=payload.chapter, until=payload.until)


def _validation_message(exc: ValidationError, language: DraftLanguage) -> str:
    """把 pydantic 的报错压成一句模型读得懂的话。**只带字段名和原因，不回显入参。**

    回显入参在别处是好心（看得见自己填错了什么），在这里是一条把任意字符串搬进
    对话历史的通路——而对话历史正是这个模块全部小心翼翼在保护的地方。

    `err['msg']` 是 pydantic 自己生成的（英文），两侧语言都原样带过——不翻译它，
    翻译了反而在中文书里造出一句中英夹杂的话。这一层只翻自己写的那半：字段定位
    的兜底文案和"参数不合法"那句前缀。
    """
    whole = message("validation_whole_request", language)
    parts = [
        f"{'.'.join(str(x) for x in err['loc']) or whole}: {err['msg']}"
        for err in exc.errors()
    ]
    separator = "；" if language is DraftLanguage.ZH else "; "
    return message("validation_failed_prefix", language) + separator.join(parts)


def dispatch(
    call: ToolCall,
    context: ToolContext,
    memo: TurnMemo | None = None,
    stored: dict[int, str] | None = None,
    blocks: dict[int, str] | None = None,
) -> ToolOutcome:
    """执行模型请求的一次工具调用。**这是工具表这道闸唯一的执行点。**

    四种失败各有各的出口，而**四种都是 `ok=False` 的正常返回，不是异常**：
    编排层要把它们贴回对话让模型自己改，抛出去只会让 agent loop 变成一串
    try/except（而漏掉其中一个的后果是整个会话死掉）。

    1. **工具名不在表里** —— 包括模型幻想出来的名字。运输层故意不校验这个
       （校验就等于那儿有第二份工具表），所以这一处是唯一的拦截点。
    2. **参数不是合法 JSON** —— 流式下 `arguments` 是一串 delta 拼起来的，
       截断是真实会发生的事。
    3. **参数不合 schema** —— 含「模型自作主张多传了一个 `must_not_reveal`」
       （`extra="forbid"` 当场拒，边界二）。
    4. **工具自己拒绝** —— 花名册里没这个名字、称呼有歧义、能力没接线。
       前两种在这一层是**两条不同的出口**（见模块 docstring「撞空」那一节）：
       它们的正确下一步相反，合成一句就必然有一半在骗模型。

    Args:
        call: `draft.provider` 原样带回来的那个请求，`arguments` 还是字符串。
        memo: 这一轮的短记性（`TurnMemo`）。`None` = 没人记 ⇒ 每一次撞空都只说
            `UnknownCharacter` 自己那句话，**行为和以前逐字节相同**（测试和 CLI 走这条）。
        stored: 这一轮按编号的只读结果表（`get_result` 用，loop 造、随轮即焚）。
            `None` = 没有这一轮的表 ⇒ `get_result` fail-closed，其它工具不受影响。
        blocks: 这一轮已压缩对话块的编号 → 原文（`get_result(kind="block")` 用）。
            同上，随轮即焚；`None` ⇒ 块取回 fail-closed。
    """
    spec = TOOLS.get(call.name)
    if spec is None:
        list_separator = "、" if context.language is DraftLanguage.ZH else ", "
        return _refused(
            call,
            message(
                "unknown_tool_name",
                context.language,
                name=call.name,
                names=list_separator.join(sorted(TOOL_NAMES)),
            ),
        )

    try:
        raw = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        return _refused(
            call, message("bad_json_arguments", context.language, msg=exc.msg)
        )
    if not isinstance(raw, dict):
        return _refused(call, message("arguments_not_an_object", context.language))

    try:
        args = spec.args.model_validate(raw)
    except ValidationError as exc:
        return _refused(call, _validation_message(exc, context.language))

    # 章号在校验之后才作数：没过校验的参数里那个数是模型随手写的，拿它去绑投影
    # 等于让一次失败的调用替 loop 决定「这条属于第几章」。
    chapter = _asked_chapter(args)

    try:
        if call.name == "get_result":
            # 唯一一个需要「这一轮才有的只读结果表」的工具：表由 run_turn 造、随轮即焚，
            # 不可能住进 ToolContext（那是能力闸，不是杂物抽屉——同 TurnMemo）。
            payload = _handle_get_result(args, context, stored, blocks)
        else:
            payload = spec.handler(args, context)
    except UnknownCharacter as exc:
        # **这一支必须排在 `ToolRefused` 前面**：`UnknownCharacter` 是它的子类，写反了
        # 这一整段永远不执行——而且不会有任何东西报错，只是那句话又变回原来那句
        # 「换个说法再试」（正是 2026-08-13 那个 bug 的形状）。
        #
        # 记在这儿而不是记在 handler 里，是因为**「这一轮」这个范围只有派发这一层知道**：
        # handler 是纯函数，`ToolContext` 上没有轮次（也不该有，见 `TurnMemo`）。
        reply_text = str(exc)
        if memo is not None:
            reply_text += _already_missed(memo.note_unknown(exc.surface), context.language)
        return _refused(call, reply_text, chapter)
    except ToolRefused as exc:
        # **拒绝也可能是花过钱的**（`ToolRefused.calls`）：起草的第一次调用答上来了、
        # 续写那次断线，这一档拒得对，但那笔钱得跟着回执一起交出去。
        return _refused(call, str(exc), chapter, exc.calls)
    except (UnresolvedCast, StoreError, ValueError) as exc:
        # 这三种都是**在发出去之前**判出来的（称呼解析不了 / 图层拒绝 / 参数不合法），
        # 一分钱没花，所以这一支没有回执可交。
        return _refused(call, str(exc), chapter)

    if isinstance(payload, StoredResult):
        # `get_result` 的原文不是一段要 `model_dump_json` 的包裹：原样进对话历史。
        # **判据是类型不是工具名**（同 `asked` / `remembered` 那一行）。
        return ToolOutcome(
            call_id=call.id,
            name=call.name,
            ok=True,
            content=payload.content,
            stored=payload,
        )

    return ToolOutcome(
        call_id=call.id,
        name=call.name,
        ok=True,
        # 出参一律经 Pydantic 序列化：`dict` / `sqlite3.Row` 越不过这一行（铁律 4）。
        content=payload.model_dump_json(),
        chapter=chapter if chapter is not None else _answered_chapter(payload),
        calls=_billed_calls(payload),
        # **只有成功这一条路带得出提问**（见 `ToolOutcome.asked`）：一次参数填错的
        # `ask_author` 不是一次提问，模型该把它改对重发。
        asked=_asked_question(payload),
        # 同上：没通过校验的那一次什么都没记下（`remember_rule` 的三种拒绝走的是
        # 上面那个 `ValueError` 分支，一条规矩都造不出来）。
        remembered=_remembered_rule(payload),
    )


def outdated_manuscript(content: str, context: ToolContext) -> bool:
    """这条已经躺在对话里的工具返回，装的是不是一份**和磁盘对不上的正文**。

    ── 它堵的是边界三真正的那个形态 ──────────────────────────────────────

    ADR 0019 边界三只说了「正文的中间产物不许写进会话表当第二个答案」，而
    `chapter_text` 按定义就要把一整章正文给模型看，那条返回也必须原样存下来
    （否则 resume 之后模型看到的是另一段历史）。**于是第二份正文一定存在**，
    问题不在它存不存在，在**它过期之后还发不发得出去**：

    - 对作者：磁盘那份赢（编辑器、版本抽屉、`GET …/text` 全都读磁盘，ADR 0007）；
    - 对模型：会话里那份赢，因为它是**被发出去的那一份**。

    两份不一致的时候「第 N 章是什么」就有了两个答案，而模型给的是过期那个——
    一段读起来完全正常、只是说错了的话，**没有任何东西会报错**。这个仓库在
    `index.py` 里已经把同一条论证写死过一次（L1 不缓存的理由）：

    > 一份缓存的命中表会在他保存的那一刻变成一个**看起来正常的错误答案**……
    > 而这一层全部的价值就是「确定性、当场算、答案永远是当前的」。

    对话就是这样一份缓存，**而且它是持久化的**。所以投影在发出去之前把过期的那份
    换成一句「重新读一次」——ADR 0019 把 `tool_result` 排在剪枝第一位，用的正是
    这条理由：**丢掉的工具返回重查一次就有，而且查回来的是当前的。**

    ── 三条必须这么写的细节 ──────────────────────────────────────────────

    1. **只认正文那一种返回。** 判据是 `ChapterFullText`（`extra="forbid"`）验不验得过，
       不是一张「哪个工具的返回带正文」的表——表会在加工具那天漂。约束/摘要那几条
       返回也会过期，但它们的当前值不在磁盘上，拿磁盘去判它们是用一个答不了的问题
       删有用的东西。
    2. **比的是重跑一次同一个处理函数的出参**，不是自己再读一遍文件：截断长度、
       未来标记、注记全都跟着 `context` 走，自己拼一份就是第二处会漂的实现。
    3. **读不到磁盘时一律 `False`。** 「我看不见」和「它变了」是两件事，
       按后者办等于每一轮都把读过的正文清空一次，而那是拿作者的钱买一个没发生的问题。
    """
    try:
        payload = ChapterFullText.model_validate_json(content)
    except ValidationError:
        return False
    if context.root_path is None:
        return False
    try:
        fresh = handle_chapter_text(ChapterTextArgs(chapter=payload.chapter), context)
    except ToolRefused:
        # 那一章从磁盘上没了（作者删了文件、或者章号被重排）。手里这份**更加**不是当前的。
        return True
    return fresh.text != payload.text


class BatchRunner:
    """模型一轮要的那一批工具的执行器：**没有副作用的那几条同时跑，其余按序、单独跑。**

    ── 为什么会有它（ADR 0022）────────────────────────────────────────────

    起草拆成两个动作之后，`draft_chapter` 没有副作用了——**一批三稿可以同时写**，
    作者盯着转圈的两三分钟压回一分钟。旧机制下不敢并发：三稿都要往同一个文件写。

    ── 三条不许省的规矩 ──────────────────────────────────────────────────

    1. **顺序不变。** 出来的结果与 `calls` 同序，`tool_result` 靠 `call_id` 认领，
       而「哪几个 `tool_call` 还缺 result」是线性 loop 的全部执行态（ADR 0019）。
    2. **有副作用的那几条是屏障。** `save_draft` 写作者的书：它前面那批并发的跑完了
       才轮到它，它跑完了后面的才开始。判据是 `ToolSpec.concurrent`（**默认 False**），
       不是一张「哪个工具危险」的表——表会在加工具的那天漂，而漂的方向是往开着的那侧。
    3. **一个窗口一次跑完，跑完了才回到调用方。** 这不是省事：loop 拿到第一条结果之后
       就要落库、记账（那都是同一条 SQLite 连接上的**写事务**），而两个显式事务在同一条
       连接上嵌不起来。等整窗跑完，工作线程和 loop 线程就永远不会同时碰库。

    ── 并发默认是**关的**，只有装配层能开 ────────────────────────────────

    `workers=1` 就是老的串行行为。放开它的判据不在这一层：**只有开连接的那一层知道
    这条连接是不是 `check_same_thread=False`**（`api/deps.py::get_conn` 是；CLI 和
    测试里那些默认不是，跨线程用会当场 `ProgrammingError`）。所以 `run_turn` 的默认
    `TurnLimits.parallel_tools` 是 1，由 `api/chat.py` 显式放开。
    """

    def __init__(
        self,
        calls: Sequence[ToolCall],
        context: ToolContext,
        *,
        workers: int = 1,
        on_start: Callable[[int], None] | None = None,
        memo: TurnMemo | None = None,
        stored: dict[int, str] | None = None,
        blocks: dict[int, str] | None = None,
    ) -> None:
        """`on_start(下标)`：**这一条真的开跑了**（ADR 0024 的事件流要说「正在查什么」）。

        ── 为什么这一声必须由这一层喊 ────────────────────────────────────────

        调用方（`agent/loop.py`）是一条一条 `take()` 的，但**一个并发窗口是一次跑完的**。
        在 `take(i)` 之前自己喊一声，并发那一档喊出来的顺序就是
        「第 1 稿开始 → 第 1 稿好了 → 第 2 稿开始」——而实际上三稿是同时在写、同时写完的。
        那是一句**读起来完全正常的假话**，而这个仓库最贵的错误正是那一种。

        窗口的边界是这一层的私有逻辑（`_concurrent`），在外面重算一份就是第二处会漂的
        实现。所以这一声在 `_run_window` 里喊，一个窗口里那几条一起喊。

        它跑在**调用方那条线**上（提交给线程池之前），所以不需要线程安全。
        """
        self._calls = list(calls)
        self._context = context
        self._workers = max(1, workers)
        self._on_start = on_start
        # 这一轮的短记性，**由调用方（loop）造并且跨批共用**：一轮之内模型可以分好几步
        # 各撞一个空，每批各造一个的话那个数永远是 1，闸门一次都不响。
        self._memo = memo
        # 这一轮按编号的只读结果表（`get_result` 用）。**同样由 loop 造、跨批共用**：
        # 每批重造一份的话，模型在同一轮里分几步取回，编号会各数各的。
        self._stored = stored
        # 已压缩对话块 → 原文（`get_result(kind="block")` 用）。同上，跨批共用。
        self._blocks = blocks
        self._done: dict[int, ToolOutcome] = {}

    def __len__(self) -> int:
        return len(self._calls)

    def take(self, index: int) -> ToolOutcome:
        """第 `index` 条的结果。**同一个窗口里那几条会在第一次取的时候一起跑掉。**

        取走即移出（`leftovers()` 只剩没被取走的），所以一条结果**不会被记两遍账**。
        """
        if index not in self._done:
            self._run_window(index)
        return self._done.pop(index)

    def leftovers(self) -> list[tuple[int, ToolOutcome]]:
        """**已经真的跑过、但调用方还没取走**的那几条（按下标升序）。

        闸门（额度到顶、反复调同一个、作者按停）会让 loop 在批的中间停下来，而并发窗口
        里那几条**那时已经跑完了**——`draft_chapter` 甚至已经花过钱。把它们当成「没跑」
        配一个壳，就是一次凭空消失的花销加一句骗人的话。所以它们在这儿等着被如实收走。
        """
        return sorted(self._done.items())

    def _run_window(self, start: int) -> None:
        end = start + 1
        if self._workers > 1 and self._concurrent(start):
            while end < len(self._calls) and self._concurrent(end):
                end += 1
        if self._on_start is not None:
            for index in range(start, end):
                self._on_start(index)
        if end - start == 1:
            # **裸调用，外面没有 try/except**（`dispatch` 的四种失败都是正常返回）。
            self._done[start] = dispatch(
                self._calls[start],
                self._context,
                self._memo,
                self._stored,
                self._blocks,
            )
            return
        failure: BaseException | None = None
        with ThreadPoolExecutor(
            max_workers=min(self._workers, end - start), thread_name_prefix="nh-tool"
        ) as pool:
            futures = {
                index: pool.submit(
                    dispatch,
                    self._calls[index],
                    self._context,
                    self._memo,
                    self._stored,
                    self._blocks,
                )
                for index in range(start, end)
            }
        for index, future in futures.items():
            try:
                self._done[index] = future.result()
            except BaseException as exc:  # noqa: BLE001 —— 见下
                # `dispatch` 抛异常 = 一个真的 bug（四种失败它自己都收成了 `ok=False`）。
                # **先把跑成了的那几条收进来再抛**：它们里可能有一笔已经花掉的钱，
                # 而这一层不许让它连回执一起消失。抛出去是对的——bug 不许被吞。
                failure = failure or exc
        if failure is not None:
            raise failure

    def _concurrent(self, index: int) -> bool:
        spec = TOOLS.get(self._calls[index].name)
        # 认不出的工具名一律按「不能并发」算：它走的是 `dispatch` 里那条拒绝分支，
        # 便宜得很，而 fail-closed 在这儿的代价是零。
        return spec is not None and spec.concurrent


def dispatch_all(
    calls: Sequence[ToolCall],
    context: ToolContext,
    *,
    workers: int = 1,
    memo: TurnMemo | None = None,
    stored: dict[int, str] | None = None,
    blocks: dict[int, str] | None = None,
) -> list[ToolOutcome]:
    """模型一轮发了好几个 `tool_call` 时的便利函数，**与 `calls` 同序**。

    `workers=1`（默认）= 一条一条跑。放开它之前先读 `BatchRunner` 那三条规矩。
    """
    runner = BatchRunner(
        calls, context, workers=workers, memo=memo, stored=stored, blocks=blocks
    )
    return [runner.take(index) for index in range(len(calls))]
