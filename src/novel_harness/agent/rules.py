"""作者在对话里定下的**规矩** —— 偏好，不是事实（[ADR 0023](../../../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。

「别写打斗」「冷一点」「太煽情了」这类话，**不进 canon**（它们不是从正文里抽出来的事实，
没有引语、没有证据、也没有一条边能安放它们），但它们必须**活过剪枝**——ADR 0023 那张表
把它们排在「作者说过的话」之后、约束返回之前，理由是「小、跨轮有效、**丢了最气人**」。

── 这一层只做四件事，第五件不是它的 ──────────────────────────────────────

1. **存**：把模型提炼出来的一条规矩变成 canonical 历史里的一条记录（`rule_message`）；
2. **数**：同一串字被记了几遍（`heard`）——**字节相等，不是意思相同**；
3. **按章号过期**：`surviving_rule_indices()`；
4. **撤销**：追加一条指着它的记录（`revocation()`），**不是把那一条删掉**。

**第五件「提炼」不在这儿，它是模型干的。** 「作者刚才那句话算不算一条规矩、它该被写成
哪一句」要回答「这句话是什么意思」——那是语义判断，ADR 0005 在 v1 里禁止本仓库长出这种
能力。引擎负责的是提炼**之后**的那四件事，而它们全部是集合判断。

── 存法：它是 canonical 历史里的一条 SYSTEM 消息，**没有新的落盘面** ────────

规矩落在 `Conversation.messages` 里，`role=SYSTEM`、`chapter=<坐标>`、`content=<那句话>`。
会话持久化那一层（`agent/store.py`）这三列本来就在逐字段落盘，所以**不需要新表、
不需要新列、不需要迁移**——而这不只是省事：

> 秘密一旦交出去就在**持久化**的东西里了（ADR 0019 边界一，不可回收）。
> 每多一个落盘面就多一处要单独去搜的地方，而「在出参上搜干净」不等于「在表里搜干净」。

规矩走的是那条**已经被搜过、已经有形状网罩着**的路，一个新面都没开。

**这个模块自己够不着那一层**：它不 import 存储、不写一句 SQL、也不提那两张表的名字
（`tests/test_chat_api.py` 和 `tests/test_chat_boundary.py` 各有一张按文件扫的网罩着
「谁够得着会话的读端」——**本文件第一版就是被它们咬住的**，咬的是一句 docstring 里的
表名。收窄的最强形态是根本没提到）。

**约定**：`Conversation.messages` 里的 SYSTEM 消息**只有两个来源**：一条规矩，或者一条
撤销记录（下一节）。`prefix` 里的 SYSTEM 是另一回事（跨章不变的身份和文风，边界六），
两者住在两个字段上，`Conversation` 的校验器让它们混不了。

── 撤销：**追加一条指着它的记录**，不是删（迁移 011）────────────────────────

ADR 0023 的退路押在两件事上：**看得见 + 能取消**。取消不能靠删——canonical **只增不改**
（读回来重建出的 `Conversation` 必须和存进去之前逐字节相同，删一行就把这条不变量拆了），
所以撤销是历史上多出来的一条记录，它身上唯一有意义的是那个结构槽（`AgentMessage.revokes_seq`）。

**也不能靠「再说一遍」表达撤销**：那条通路已经被「说第二遍 = 从批级升到章级」占用了
（下面的 `REPEAT_TO_WIDEN`），作者想取消，系统会听成加强。

两条判据必须一起写对，缺一条这个按钮就是假的：

- **按身份撤，不按下标撤。** 同一条规矩被记过两遍时，读端只摆出**最后**那一条
  （`live_rules` 去重），作者点的也就是那一条；只把那个下标划掉的话，前面那一遍还在，
  而它此刻已经是章级的（说过两遍）——于是**按钮按了，规矩还在**，且没有任何东西会报错。
- **只往回管。** 撤销杀掉的是它**之前**的同名规矩。作者取消完又说了一遍，那是新的一条，
  该重新开始活——不然「取消」就成了「以后再也不许说这句话」。

── 章号从哪儿来：**只能是引擎手里的那个坐标**（约束 10）────────────────────

`rule_message(..., chapter=…)` 的那个数只许来自 `ToolContext.working_chapter`：

- **不许来自作者**：表单里有章号输入框 = 邀请污染，这是约束 10 的原文；
- **不许来自模型**：给它一个 `chapter` 参数，它就能把一条规矩钉在第 9999 章上，
  而那正是下面「不许把有效期改成 9999」那一条要防的东西；
- **没有坐标就不记**（`chapter is None` ⇒ `ValueError`）。一条过不了期的规矩会**跟着作者
  走到第 200 章**，而他不知道它在——ADR 0023 那张表里「作者的偏好」拿不准要**放掉**，
  正是为了这个形态。

它是**查询坐标（AS OF）**，和 `nh panel --chapter` 同义，**不写进任何一行图数据**：
这个模块没有 `StoryGraph`、没有连接、没有一句 SQL。

── 安全方向：**跟 `must_not_reveal` 是反的**（ADR 0023 写死了）───────────────

| | 拿不准时 | 为什么 |
|---|---|---|
| `must_not_reveal` | **留着**（多禁 = fail-closed） | 说破了收不回来 |
| **作者的偏好** | **放掉**（早失效） | 留着 = 第 200 章写不出打戏，而**作者不知道为什么** |

具体到代码，这条差别只有一个字符：`agent/loop.py::project()` 筛工具返回用的是 `>`
（往前的章是超集，留着最多多禁一条），筛规矩用的是 `!=`（**换一章就没了**），
而 `chapter is None` 时工具返回**全留**、规矩**全放**。

**接线那一头也是这个方向**：模型叫一次「记下来」，而引擎手上没有章号坐标时
（`ToolContext.working_chapter is None`）**这次记录直接被拒**，不是退而求其次记成一条
永不过期的（`rule_message` 那句报错就是这条工具的拒绝理由）。

**这跟这个仓库其余地方的直觉相反**，下一个人很可能顺手把它「修」成 fail-closed，
所以它有专门的测试（`tests/test_agent_rules.py` 第三节，两条同框对照 + 一个反向探针）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..draft.length import DraftLanguage, count_units
from .loop import AgentMessage, Conversation, Role

RULE_MAX_UNITS = 120
"""一条规矩最多多少字（`count_units` 口径，同单章摘要那个上限）。

**这是一道闸不是格式偏好。** 章级规矩进不了稳定前缀（边界六），所以它**每一轮都要重发**
（ADR 0023 自己把这条列在「代价」里）。没有上限的话，一条被提炼得很热心的「规矩」就是
一段免费的常驻 prompt，而作者是按 token 付钱的那个人。
"""

REPEAT_TO_WIDEN = 2
"""**作者**说到第几遍自动从「就这一批」升到「这一章都这样」（ADR 0023：一次是偶然，两次是模式）。

**「说了几遍」是集合判断**：`rule_key()` 归一化之后**字节相等**才算同一条。
「这两句话是不是一个意思」是语义判断，ADR 0005 禁止——所以提炼那一步（模型干的）
必须对同一条规矩**重述成一模一样的措辞**，工具描述里要写死这句话。

**数的是作者开口的次数，不是引擎记了几条**（`_hearings`）。ADR 把这条机制写成一个
来回：取窄（就这一批）⇒「猜窄了的代价是**作者再说一次**（顺口，他本来就要评价下一批）」
⇒ 升到章级。照记录条数数的话，模型在**同一批**里把同一条规矩记两遍就直接升到章级，
而那一批里作者只说过一次——方向正是 ADR 点名最贵的那一侧：「猜宽了的代价是**一条隐形的
规矩跟着他走，他不知道它在**」。它够得着：`TurnLimits.repeat_limit` 允许同一个调用在
一轮里出现三次，而 resume 补跑一条 `pending` 的调用就会把同一条规矩再记一遍。

**代价说清楚**：模型换了个说法，计数就从头开始，那条规矩多活一轮批级、少活一段章级。
方向是**放掉**那一侧，和上面那张表一致。
"""

_DROPPED_PUNCTUATION = frozenset(
    "。，、；：！？…—～·「」『』（）《》〈〉【】“”‘’"
    ".,;:!?()[]{}<>\"'`-_~/\\|@#$%^&*+="
)
"""归一化时丢掉的标点。**一张写死的字符表，不是「标点的定义」**——

`unicodedata.category(ch).startswith("P")` 看起来更聪明，但它会把作者可能真的用来区分
两条规矩的东西也吃掉，而且它随 Unicode 版本变。这里要的只是「句末多打了一个句号不算
另一条规矩」，那用一张表就够，而且它读得出来、改得动。
"""


def rule_key(text: str) -> str:
    """两条规矩**是不是同一串字**的判据。**它不回答「是不是一个意思」。**

    归一化只做三件确定性的事：NFKC（全角半角、兼容字符）、`casefold`（拉丁字母大小写）、
    丢掉空白和 `_DROPPED_PUNCTUATION` 里那几个标点。剩下的逐字符比。
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and ch not in _DROPPED_PUNCTUATION
    )


def normalized_rule(text: str) -> str:
    """一条规矩存进去之前的样子。**超长和空白在这儿被拒，不在投影里被悄悄截断。**

    **「归一化之后一个字都不剩」也在这儿被拒**（`rule_key` 是空串，比如「。。。」「……」）。
    那种规矩记得下、**却一次都活不了**：下面三条判据（数重复 / 留存 / 身份比对）全都走
    `rule_key`，而空串在它们那儿一律跳过。于是工具回一句「记下了」，投影里它从来没出现过
    ——正是 `agent/loop.py::run_turn` 的 `finish()` 点名的那个形态：
    **「他说了，系统答应了，下一轮它就忘了」**。拒在入口是唯一能让这句话不发生的地方。

    Raises:
        ValueError: 空的、只剩标点、或者超过 `RULE_MAX_UNITS`。三条报错都是**说给模型
            听的中文**（它会经 `ToolRefused` 贴回对话让模型自己改），不是给维护者的诊断。
    """
    line = " ".join(text.split())
    if not line:
        raise ValueError("这条规矩是空的：作者到底要什么，一句话说清楚再记。")
    if not rule_key(line):
        raise ValueError(
            "这条规矩里一个字都没有，只剩标点——记下来它也一次都生效不了。"
            "用作者自己的说法把那句话写出来。"
        )
    if count_units(line, DraftLanguage.ZH) > RULE_MAX_UNITS:
        raise ValueError(
            f"这条规矩太长了（上限 {RULE_MAX_UNITS} 字）。规矩是每一轮都要重发的东西，"
            "把它压成一句话——细节留在对话里说。"
        )
    return line


def is_rule(message: AgentMessage) -> bool:
    """这条 canonical 消息是不是一条规矩。

    判据是**结构**（`messages` 里的 SYSTEM 消息，不带任何工具调用的壳，**也不撤销任何
    东西**），不是一张「哪几条是规矩」的表——表会在加东西的那天漂（同 `ToolOutcome.chapter`
    取法的那条先例）。

    最后那一条排掉的是撤销记录：它和规矩住在同一个角色上，认错了它就会被当成一条内容
    为空的规矩去数重复。
    """
    return (
        message.role is Role.SYSTEM
        and not message.tool_calls
        and not message.tool_call_id
        and message.revokes_seq is None
        and bool(message.content.strip())
    )


def is_revocation(message: AgentMessage) -> bool:
    """这条 canonical 消息是不是一条**撤销记录**（作者取消了某条规矩）。

    它的全部意义在那个结构槽上，**正文是空的**——撤销要是也走文本，它和「再说一遍」
    就在同一条通路上分不开了（见模块 docstring）。所以它**不进 prompt**：
    `project()` 把它整条丢掉，模型看到的就是那条规矩从来没被说过。
    """
    return message.role is Role.SYSTEM and message.revokes_seq is not None


def revocation(messages: Sequence[AgentMessage], seq: int) -> AgentMessage:
    """作者取消第 `seq` 条规矩。**追加一条指着它的记录**，不是把那一条删掉。

    Args:
        messages: 这一段会话的 canonical 历史（**整段**，不是尾巴）。
        seq: 要取消的那条规矩在 `messages` 里的下标，也就是 `AuthorRule.seq` ——
            读端摆给作者的就是它，**界面回传的也只能是它**。

    **重复取消不报错**：作者双击、两个标签页各点一次都是常态，多出来的那条是一次无害的
    重复（撤销按身份生效，第二条杀的是同一批）。

    Raises:
        ValueError: 那个下标不在这段历史里，或者它指的不是一条规矩。**两句都是说给
            作者听的中文**——这条路的尽头是他手上那个按钮，不是一条诊断。
    """
    if not 0 <= seq < len(messages):
        raise ValueError("找不到要取消的那条规矩——它不在这段对话里。")
    if not is_rule(messages[seq]):
        raise ValueError("那一条不是你定下的规矩，取消不了它。")
    return AgentMessage(role=Role.SYSTEM, revokes_seq=seq)


def rule_message(text: str, *, chapter: int | None) -> AgentMessage:
    """把模型提炼出来的一条规矩变成 canonical 里的一条记录。

    Args:
        text: 规矩本身（会经 `normalized_rule`）。
        chapter: **只能是 `ToolContext.working_chapter`**，见模块 docstring。

    Raises:
        ValueError: 没有章号坐标，或者章号不合法，或者规矩空/超长。
    """
    if chapter is None:
        raise ValueError(
            "现在不知道作者在写第几章，这条规矩记不了——记了它就永远过不了期，"
            "而作者不会知道它还在。等他把光标放到某一章上再说。"
        )
    if chapter < 1:
        raise ValueError("章号至少是 1。")
    return AgentMessage(role=Role.SYSTEM, content=normalized_rule(text), chapter=chapter)


class AuthorRule(BaseModel):
    """一条**现在生效**的规矩（读端用；投影自己走 `surviving_rule_indices`）。

    这是 ADR 0023「摆出来、能取消」的**摆出来**那一半；取消那一半是 `revocation()`，
    它收的就是下面那个 `seq`。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    chapter: int = Field(ge=1)
    """它绑在第几章上。**规矩的全部时间状态就这一个数。**"""

    heard: int = Field(ge=1)
    """这一章里**作者**说过它几遍（`rule_key` 相等，按作者回合数，见 `_hearings`）。

    `>= REPEAT_TO_WIDEN` = 已经升到章级。**不是「引擎记了几条」**：同一批里记两遍
    算一遍，那一批里作者只开过一次口。
    """

    seq: int = Field(ge=0)
    """它在 canonical 历史（`Conversation.messages`）里的下标。**取消时按它定位。**

    **它不是会话表里那一列 `seq`**：那儿的编号把稳定前缀也数在内，两者差一个前缀长度。
    这个数和界面上每条消息带的那个是同一个坐标（`api/chat.py::_visible` 也按历史下标编），
    所以「作者点了哪一条」在整条链上只有一种读法。
    """

    @property
    def chapter_wide(self) -> bool:
        """章级（整章有效） vs 批级（活到作者下一次开口）。"""
        return self.heard >= REPEAT_TO_WIDEN

    @property
    def valid_from(self) -> int:
        """闭开区间的左端。**它是 `chapter` 的别名，不是第二个存起来的数。**

        这里刻意不存一对端点：存两个数就有两处能不一致，而这个仓库把「同一个事实两份
        拷贝」当成头号病。**这条区间也不是一条图上的边**——这个模块没有 `StoryGraph`、
        没有连接、一句 SQL 都没有，`[valid_from, valid_to)` 那份唯一实现仍然只在
        `graph/queries.py`（铁律 3）。
        """
        return self.chapter

    @property
    def valid_to(self) -> int:
        """闭开区间的右端：**下一章**。切章就是失效，不是「有效期长一点」。"""
        return self.chapter + 1


def _last_author_index(messages: Sequence[AgentMessage]) -> int:
    """作者最后一次开口在哪儿。没开过口返回 `-1`。"""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is Role.USER:
            return index
    return -1


def _identity(message: AgentMessage) -> tuple[str, int | None]:
    """一条规矩的身份：**同一章的同一串字**算同一条。"""
    return rule_key(message.content), message.chapter


def _revoked_indices(messages: Sequence[AgentMessage]) -> frozenset[int]:
    """被作者取消掉的那几条规矩在 `messages` 里的下标（见模块 docstring「撤销」）。

    两条判据都在这儿，**缺一条那个按钮就是假的**：

    - **按身份**（同一章的同一串字）——记过两遍的规矩，读端只摆出最后那一条，
      只划掉那个下标的话前面那一遍还在，而它此刻已经是章级的；
    - **只往回**（`target < position`）——取消完又说一遍是新的一条，该重新开始活。

    **下标越界的一律忽略**，不炸：这个函数也会被拿去看一段**尾巴**（会话列表数
    `pending` 时读的就是尾巴），那时 `revokes_seq` 指向的东西根本不在手上。
    """
    dead: set[int] = set()
    for position, message in enumerate(messages):
        target = message.revokes_seq
        if target is None or not 0 <= target < position:
            continue
        aimed = messages[target]
        if not is_rule(aimed):
            continue
        identity = _identity(aimed)
        dead.update(
            index
            for index in range(position)
            if is_rule(messages[index]) and _identity(messages[index]) == identity
        )
    return frozenset(dead)


def _hearings(
    messages: Sequence[AgentMessage], chapter: int, revoked: frozenset[int]
) -> dict[str, int]:
    """这一章里每一条规矩**被作者说过几遍**（`rule_key` 相等算同一条）。

    **判据是结构：它被记在第几个作者回合里。** 一个「作者回合」= 两句作者的话之间的
    那一段，也就是模型在他说完之后干的那一批活。同一批里记两遍算**一遍**——
    那一批里作者只开过一次口，见 `REPEAT_TO_WIDEN`。

    **被撤销的那几条不计数**：不然作者取消完再说一遍，计数从 3 起跳、当场就是章级，
    而他刚刚明确表示过不想要它。

    这仍然是集合判断：数的是回合的**下标集合**有多大，一个「这两次是不是同一个意思」
    都没问。
    """
    turns: dict[str, set[int]] = {}
    turn = 0
    for index, message in enumerate(messages):
        if message.role is Role.USER:
            turn += 1
            continue
        if index not in revoked and is_rule(message) and message.chapter == chapter:
            key = rule_key(message.content)
            if key:
                turns.setdefault(key, set()).add(turn)
    return {key: len(seen) for key, seen in turns.items()}


def surviving_rule_indices(
    messages: Sequence[AgentMessage], chapter: int | None
) -> frozenset[int]:
    """这一份投影里**留得下来**的规矩，返回它们在 `messages` 里的下标。

    ── 三条判据，顺序无关 ──────────────────────────────────────────────

    1. **切章就失效**（`!=`，不是 `>`）。ADR 0023：默认只管当前这一章。
       `chapter is None`（不知道作者在写第几章）⇒ **一条都不留**。
       这两处都是「拿不准就放掉」，和工具返回那一档正好相反。
    2. **取窄**：作者只说过一遍的规矩活到**他下一次开口**为止（结构判据：它后面还没有
       USER 消息）。猜窄了的代价是作者顺口再说一次；猜宽了的代价是**一条隐形的规矩
       跟着他走**。
    3. **数重复**：同一章里同一串字被**作者**说到第 `REPEAT_TO_WIDEN` 遍，整章有效
       （`_hearings`：数的是作者回合，不是记录条数）。
    4. **作者取消掉的不留**（`_revoked_indices`）。

    **入参的 `messages` 必须是完整的那一段历史**：这里的下标就是 `revokes_seq` 的坐标，
    拿一段剪过的、或者一截尾巴进来，撤销会指到别的消息上。`project()` 因此把规矩这一档
    排在任何删减**之前**。

    **同一条只留最后那一次**：记了两遍就在 prompt 里出现两遍，是纯浪费。
    """
    if chapter is None:
        return frozenset()

    revoked = _revoked_indices(messages)
    heard = _hearings(messages, chapter, revoked)
    last_author = _last_author_index(messages)
    keep: dict[str, int] = {}
    for index, message in enumerate(messages):
        if index in revoked or not is_rule(message) or message.chapter != chapter:
            continue
        key = rule_key(message.content)
        if not key:
            continue
        if heard[key] < REPEAT_TO_WIDEN and index < last_author:
            continue  # 批级，而作者已经又开口了 —— 这一批过去了
        keep[key] = index
    return frozenset(keep.values())


def expired_rule_count(
    messages: Sequence[AgentMessage], live: frozenset[int]
) -> int:
    """**不再生效**的规矩有几条（回执上那个数）。

    **按身份数，不按记录数。** 同一条被记了两遍、投影里只留最后一遍——那是**去重**，
    不是过期，不该被算成「这一轮少给了模型一条规矩」。两者混在一个数里的话，
    「说了两遍」这个正常动作会让回执一直显示「有一条失效了」，而作者会去找那条不存在的
    规矩。

    **作者自己取消掉的也不算过期**：他知道它没了（是他按的），而且撤销记录永远留在历史
    里——算进来的话那个数就永远挂着一条他已经处理完的规矩。

    **归一化之后一个字不剩的也不算**（`rule_key` 是空串）：它**永远不可能出现在
    `surviving` 里**，算进来它就在回执上挂到会话结束，而作者会去找一条不存在的规矩。
    今天这种规矩进不来（`normalized_rule` 拒了），这一句是为**它进得来的那些天里存下的
    老会话**留的——canonical 只增不改，那几条会一直躺在历史里。
    """
    revoked = _revoked_indices(messages)
    everything = {
        _identity(message)
        for index, message in enumerate(messages)
        if is_rule(message) and index not in revoked and rule_key(message.content)
    }
    surviving = {_identity(messages[index]) for index in live}
    return len(everything - surviving)


def live_rules(conversation: Conversation, chapter: int | None) -> tuple[AuthorRule, ...]:
    """第 `chapter` 章此刻生效的那几条规矩，**按它们被说出来的顺序**。

    这是「摆出来、能取消」那条理由链（ADR 0020 → 0022 → 0023）的读端：不问作者，
    但他看得见、改得掉。**投影不走这个函数**（它只要下标），两边判据同源
    （`surviving_rule_indices`），不写第二份。
    """
    live = surviving_rule_indices(conversation.messages, chapter)
    if not live:
        return ()
    # 计数走 `_hearings`（作者回合），**不在这儿写第二份**：读端显示的「说过几遍」
    # 和投影用来判章级的必须是同一个数，否则清单上写着「说过 2 遍」而它其实没升级。
    counts = _hearings(
        conversation.messages, chapter, _revoked_indices(conversation.messages)
    )
    return tuple(
        AuthorRule(
            text=conversation.messages[index].content,
            chapter=chapter,  # `index in live` 已经保证了它等于消息上那个数
            heard=counts[rule_key(conversation.messages[index].content)],
            seq=index,
        )
        for index in sorted(live)
    )


def promoted(conversation: Conversation, text: str) -> Conversation:
    """把一条规矩**升格**成跨章不变的文风（ADR 0023：「整本书都这样」）。

    ── 升格**不是**把有效期改成 9999 ────────────────────────────────────

    那样做出来的东西是一条「一直没到期的章级规矩」，而边界六的判据只有一句：
    **换一章会不会变**。一条 `[1, 9999)` 的规矩换一章不变，所以它本来就该住在稳定前缀里
    ——住在那儿它才被缓存、才不用每轮重发，而且它在结构上就没法再带一个章号
    （`Conversation` 的校验器拒收带章号的前缀消息，**这不是纪律，是构造不出反例**）。

    幂等：同一条规矩升第二次不多出一条（作者说了两遍「整本书都这样」是常态）。

    **判据是 `rule_key`，和数重复那儿同一个**，不是逐字符比原文。两条路的代价差着一个
    数量级：章级那条记重了只是多一章的浪费，而**住进稳定前缀的东西永不过期、每一轮都
    重发到会话结束**。用原文比的话「冷一点」和「冷一点。」就是两条常驻规矩。

    Raises:
        ValueError: 规矩空/超长（同 `normalized_rule`）。
    """
    line = normalized_rule(text)
    key = rule_key(line)
    if any(rule_key(message.content) == key for message in conversation.prefix):
        return conversation
    return conversation.model_copy(
        update={
            "prefix": (*conversation.prefix, AgentMessage(role=Role.SYSTEM, content=line))
        }
    )


__all__ = [
    "REPEAT_TO_WIDEN",
    "RULE_MAX_UNITS",
    "AuthorRule",
    "expired_rule_count",
    "is_revocation",
    "is_rule",
    "live_rules",
    "normalized_rule",
    "promoted",
    "revocation",
    "rule_key",
    "rule_message",
    "surviving_rule_indices",
]
