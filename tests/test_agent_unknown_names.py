"""**角色册里没有的那个名字**：一轮的步数是怎么被它烧光的（2026-08-13 实测）。

2026-08-13 在作者 722 章的真书上跑模式二，让它给第 723 章写一稿。它是这么把一轮花掉的：

    book_index → scene_constraints → chapter_summaries → chapter_text
    → character_state 姜召（成）
    → character_state 姜源初（**查不到**）
    → character_state 小归终（**查不到**）
    → character_state 阮芸芸（成）
    → 步数用完 → 这一轮停下 → **一稿都没写**

作者看到的是：等了几分钟，什么都没有。

── 根因不是「模型笨」，是引擎那句话 ────────────────────────────────────────

「姜源初」「小归终」是大结局才出现的新角色，角色册里没有。而引擎当时对
**「角色册里没有」和「这个叫法指向好几个人」说的是同一句话**：

    「…解析不出唯一一个人（查无此人，或者这个叫法同时指向好几个人）。
      换一个更具体的称呼，或者先在人物卡上把别名理清楚。」

两种情况的正确下一步是**相反**的（前者别再试，后者重试才对），而这句话在两种情况下都
在劝它再试一次——**引擎亲口鼓励模型去烧下一步**。所以那句话本身就是这个 bug 的一部分。

── 这份文件钉三样东西 ──────────────────────────────────────────────────

1. **实测的那个形态不许再发生**：连着几个查不到的名字，步数不该被耗光
   （§一，带一条把闸关掉就复现原 bug 的自守卫——**一张抓不住原 bug 的网等于没有网**）。
2. **两种情况是两句话**，而且给的是相反的建议（§二）。
3. **失败方向**：记性只用来说话，不用来挡查询——撞过空之后查得到的人还查得到，
   作者中途把人加进角色册之后同一个名字立刻查得到（§三）。

判据一律是**集合判断**（`len(hits)` / 「这一轮撞空过的不同称呼有几个」），
一个语义判断都没有（铁律 2 / ADR 0005）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from test_wording_guard import dev_shapes

from novel_harness import project
from novel_harness.agent.index import UnknownCharacter
from novel_harness.agent.loop import (
    Cancellation,
    Conversation,
    ModelCallReceipt,
    StopReason,
    TurnLimits,
    TurnResult,
    run_turn,
    start_conversation,
    stop_wording,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import TurnMemo, dispatch
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.provider import CompletionResult, ToolCall
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# 角色册上有的两个人 + 一个共用称呼的第三个人（歧义那一支要用它）。
ON_THE_ROSTER = ("姜召", "阮芸芸", "顾清音")
SHARED_SURFACE = "小师妹"
"""「阮芸芸」和「顾清音」都被这么叫过 ⇒ `len(hits) == 2` ⇒ 歧义。"""

# 大结局才出现的新角色：**角色册里一个都没有**，实测那一轮撞的就是它们。
NOT_IN_THE_BOOK = ("姜源初", "小归终", "白其粟")


@dataclass(frozen=True)
class Book:
    conn: Connection
    project_id: str
    store: SqliteStoryGraph
    ledger: Ledger

    def context(self, **overrides: Any) -> ToolContext:
        base: dict[str, Any] = {
            "store": self.store,
            "project_id": self.project_id,
            "working_chapter": 723,
        }
        base.update(overrides)
        return ToolContext(**base)


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def book(conn: Connection, tmp_path: Path) -> Book:
    """一本**真书的最小形状**：角色册上有几个人，而作者刚写到的那几个新角色不在上面。

    走的是生产写路径（`declare.Ledger`），所以 `store.resolve` 的行为是真的——
    这份文件全部判据都建立在「`hits` 空 / `hits` 多」这一个集合判断上，
    拿一个替身桩去造那两种形状等于自己给自己写判据。
    """
    pid = project.create(conn, name="姜召传", root_path=str(tmp_path)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    for name in ON_THE_ROSTER:
        ledger.declare_node(NodeLabel.CHARACTER, name)
    for name in ("阮芸芸", "顾清音"):
        ledger.declare_alias(of=name, surface=SHARED_SURFACE)
    conn.commit()
    return Book(conn=conn, project_id=pid, store=store, ledger=ledger)


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(arguments))


def _asked_about(who: str) -> ToolCall:
    return _call("character_state", chapter=723, character=who)


# ══════════════════════════════════════════════════════════════════════════
# 器材：按剧本回答的模型（同 `test_agent_loop_runaway.py`，那儿有完整说明）
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ScriptedModel:
    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any,
        cancel: Cancellation,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


def _looks_up(who: str) -> CompletionResult:
    """模型这一步只做一件事：查一个人。**一步一个**，实测那一轮就是这个节奏。"""
    return CompletionResult(
        text=f"我先看看{who}。",
        model="deepseek-v4",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(id=f"call-{who}", name="character_state",
                             arguments=json.dumps({"chapter": 723, "character": who})),),
    )


def _says(text: str) -> CompletionResult:
    """模型收手，说一句话。**不叫工具 = 这一轮说完了。**"""
    return CompletionResult(text=text, model="deepseek-v4", finish_reason="stop")


def _lookups(*names: str) -> list[CompletionResult]:
    return [_looks_up(who) for who in names]


THE_REAL_TRACE = (
    # 实测那一轮的节奏：**成功和失败是交替的**，而这正是旧的两个连续计数罩不住它的原因
    # （它们都在一次 `ok=True` 上归零，而这条轨迹里最长的连续失败只有 2）。
    # 后三步是「换个说法再查一次」——那正是旧措辞在教它做的事。
    "姜召", "姜源初", "小归终", "阮芸芸", "白其粟", "顾清音", "姜源初", SHARED_SURFACE,
)


def _run(
    book: Book, script: list[CompletionResult], **kwargs: Any
) -> tuple[TurnResult, ScriptedModel]:
    model = ScriptedModel(script=script)
    conversation: Conversation = kwargs.pop("conversation", None) or (
        start_conversation().with_author("给第 723 章写一稿")
    )
    receipts: list[ModelCallReceipt] = []
    result = run_turn(
        conversation,
        context=book.context(),
        model=model,
        ledger=receipts.append,
        **kwargs,
    )
    return result, model


# ══════════════════════════════════════════════════════════════════════════
# 一、实测的那个形态：**连着几个查不到的名字，步数不该被耗光**
# ══════════════════════════════════════════════════════════════════════════


def test_a_run_of_names_the_book_does_not_have_does_not_eat_the_whole_turn(book: Book) -> None:
    """把 2026-08-13 那一轮原样重放一遍：它必须**在步数用完之前**停下来。

    停下来不等于写出了一稿——**代码这一侧做不到让模型去写**（循环归模型）。它能做到的是
    ① 别让这一轮把作者的钱和步数烧到底；② 停的时候说得出一句作者接得上的话。
    真正把模型拉回去写稿的是 §二那两句话，而那一半没有确定性的判据。
    """
    result, model = _run(book, _lookups(*THE_REAL_TRACE), limits=TurnLimits(max_steps=8))

    assert result.reason is StopReason.TOOL_STUCK
    assert result.steps < 8, "步数被耗光了 —— 这正是实测那一轮的形态"
    assert result.steps == 5, "第三个查不到的名字出现在第 5 步，闸该在那一步响"
    assert len(model.calls) == result.steps

    # 停下来那句话是说给「用 WPS 不想碰命令行」的小说作者听的。
    assert result.said_to_author == stop_wording(result.reason)
    assert not dev_shapes(result.said_to_author), result.said_to_author
    # 停在一批中间也不许留下悬空的 `tool_call`（wire 上它必须被接住，少一条就是 400）。
    assert result.conversation.pending_calls == ()


def test_switching_the_gate_off_reproduces_the_original_bug(book: Book) -> None:
    """**自守卫**：把这道闸放开，上面那条实测轨迹必须原样烧到步数上限。

    没有这一条的话，上面那条断言可能只是碰巧绿（比如被别的闸拦住了），
    而这个仓库栽过的第五次正是「假实现比真实现宽，于是测试绿而产品错」。
    """
    result, model = _run(
        book,
        _lookups(*THE_REAL_TRACE),
        limits=TurnLimits(max_steps=8, unknown_name_limit=99),
    )
    assert result.reason is StopReason.STEP_LIMIT, (
        f"闸关掉了却不是死于步数（是 {result.reason}）—— 那上面那条在测别的东西"
    )
    assert result.steps == 8 == len(model.calls), "原 bug 的形态就是「八步全花在查询上」"


def test_a_success_in_between_does_not_buy_another_three_misses(book: Book) -> None:
    """**旧的两个计数会被一次成功清零，而这是最自然的节奏。**

    `tool_failure_limit` 数的是「连着失败几次」（按工具名一份、不分工具名一份），两个都在
    一次 `ok=True` 上归零。模型挨个查人时，查得到的和查不到的天然交替——于是
    「失败、失败、成功」可以无限重复，闸门一次都不响。

    这一条把那个节奏拉长：**成功穿插得再密，撞空过的不同名字仍然只增不减。**
    """
    interleaved = _lookups("姜源初", "姜召", "小归终", "阮芸芸", "白其粟", "顾清音")
    result, _ = _run(
        book,
        interleaved,
        # 连续计数按老口径**一次都不会响**（最长连续失败是 1 次）。
        limits=TurnLimits(max_steps=10, tool_failure_limit=3),
    )
    assert result.reason is StopReason.TOOL_STUCK
    assert result.steps == 5, "第三个查不到的名字在第 5 步 —— 中间那两次成功不该把账清零"


def test_the_short_memory_does_not_follow_the_author_into_the_next_turn(book: Book) -> None:
    """**记性是一轮一个。** 上一轮撞满三次不该让下一轮的第一次查询就撞在闸上。

    方向：这道闸只结束**这一轮**。作者答一句「那几个是新人物，直接写」，下一轮必须是
    一张白纸——否则一次探索会把整段会话废掉，而那比原来的浪费更贵。
    """
    first, _ = _run(book, _lookups(*THE_REAL_TRACE), limits=TurnLimits(max_steps=8))
    assert first.reason is StopReason.TOOL_STUCK

    second, model = _run(
        book,
        [_looks_up("姜源初"), _says("行，那我按现在知道的写。")],
        conversation=first.conversation.with_author("那几个是新人物，别查了，直接写"),
        limits=TurnLimits(max_steps=3, unknown_name_limit=3),
    )
    assert second.reason is StopReason.DONE, (
        f"上一轮的账跟到了下一轮（停在 {second.reason}）—— 一次探索把整段会话废掉了"
    )
    assert len(model.calls) == 2, "下一轮的第一次查询就被拦住了"


# ══════════════════════════════════════════════════════════════════════════
# 二、两种情况是两句话，而且建议是相反的
# ══════════════════════════════════════════════════════════════════════════


def test_not_in_the_book_and_ambiguous_give_opposite_advice(book: Book) -> None:
    """**这一条是根因。** 合成一句「换个说法再试」= 引擎亲口让模型去烧下一步。"""
    missing = dispatch(_asked_about("姜源初"), book.context())
    ambiguous = dispatch(_asked_about(SHARED_SURFACE), book.context())

    assert missing.ok is False and ambiguous.ok is False
    assert missing.content != ambiguous.content, (
        "两种情况又变回同一句话了 —— 正确的下一步是相反的，说成一句就必然有一半在骗它"
    )

    # 角色册里没有：**别再试**，而且说得出「再试也不会变」是为什么（名单是定死的）。
    assert "姜源初" in missing.content
    assert "别换个说法再查" in missing.content
    assert "角色册" in missing.content

    # 一个叫法指向好几个人：**重试是对的**，而且把候选摆出来让它挑。
    assert SHARED_SURFACE in ambiguous.content
    assert "再查一次" in ambiguous.content
    assert "阮芸芸" in ambiguous.content and "顾清音" in ambiguous.content


def test_the_sentence_that_started_this_is_gone_from_both_lookup_tools(book: Book) -> None:
    """那句话有**两份拷贝**（`tools.py` 一份、`index.py` 一份），说的都是同一句错话。

    现在两条工具走同一处实现，所以这条断言顺带钉住「只有一份」：两句话要逐字相同。
    """
    by_state = dispatch(_asked_about("小归终"), book.context())
    by_chapters = dispatch(
        _call("character_chapters", characters=["小归终"]), book.context()
    )
    assert by_state.content == by_chapters.content, "同一件事又有两种说法了"
    for outcome in (by_state, by_chapters):
        assert "换一个更具体的称呼" not in outcome.content, (
            "旧那句话回来了 —— 它在「这本书里没有这个人」的时候是在鼓励模型再烧一步"
        )


def test_the_engine_starts_counting_out_loud_from_the_second_miss(book: Book) -> None:
    """第一次撞空只说「这本书没有他」；**第二次开始才把这一轮撞空过的都摆出来**。

    第一次不说，是因为那时它还不知道这本书的角色册有多严，多说无益；
    第二次开始说，是因为「你已经在这上面花掉两步了」是它自己算不出来的数
    （它看得见历史，但不会去数），而这一层能给的最有用的东西就是这个数。
    """
    memo = TurnMemo()
    first = dispatch(_asked_about("姜源初"), book.context(), memo)
    assert "撞上" not in first.content, "第一次就开始数数 —— 那时它还没有可数的东西"

    second = dispatch(_asked_about("小归终"), book.context(), memo)
    assert "撞上 2 个角色册外的名字" in second.content
    assert "姜源初" in second.content and "小归终" in second.content, (
        "只报了个数不报是哪几个 —— 模型看不出自己在同一类名字上打转"
    )
    assert memo.unknown_names == ("姜源初", "小归终")


def test_without_a_memo_the_refusal_is_byte_for_byte_the_plain_one(book: Book) -> None:
    """`memo=None`（CLI、测试、任何没有「一轮」概念的调用方）**行为不变**。

    记性是编排层的东西，不是工具的能力（`TurnMemo` 的 docstring 写着为什么它不在
    `ToolContext` 上）。没人记的时候这一层不该长出第二种行为。
    """
    without = dispatch(_asked_about("姜源初"), book.context())
    with_empty = dispatch(_asked_about("姜源初"), book.context(), TurnMemo())
    assert without.content == with_empty.content


# ══════════════════════════════════════════════════════════════════════════
# 三、失败方向：**记，但不挡**
# ══════════════════════════════════════════════════════════════════════════


def test_a_miss_never_blocks_a_lookup_that_would_have_worked(book: Book) -> None:
    """宁可多让它查一次，也不能把一次本来能成功的查询挡掉。"""
    memo = TurnMemo()
    for who in NOT_IN_THE_BOOK:
        assert dispatch(_asked_about(who), book.context(), memo).ok is False
    for who in ON_THE_ROSTER:
        assert dispatch(_asked_about(who), book.context(), memo).ok is True, (
            f"撞过三次空之后，角色册上的「{who}」也查不动了 —— 这道闸挡到正事上了"
        )


def test_the_same_name_is_asked_again_instead_of_answered_from_memory(book: Book) -> None:
    """**这一轮里作者真的会去把人加进角色册**，那时上一次的「查不到」就是一个陈旧答案。

    「同一个称呼查第二次时直接把上次的答案还给它」一步都省不下来（一步 = 一次模型调用，
    那笔钱在工具跑起来之前就付掉了），却会造出这个仓库已经拒绝过一次的东西
    （`index.py` 说 L1 为什么不缓存）：**一个看起来完全正常的错误答案。**
    """
    memo = TurnMemo()
    assert dispatch(_asked_about("小归终"), book.context(), memo).ok is False

    # 作者在另一个窗口里把他建了出来。
    book.ledger.declare_node(NodeLabel.CHARACTER, "小归终")
    book.conn.commit()

    assert dispatch(_asked_about("小归终"), book.context(), memo).ok is True, (
        "上一次的「查不到」被当成答案还回去了 —— 而作者刚刚已经把他加进来了"
    )


def test_the_refusal_type_is_what_gets_counted_not_a_keyword_in_the_sentence(
    book: Book,
) -> None:
    """「这条拒绝是不是『角色册里没有』」的判据是**异常的类型**，不是在文本里找字。

    拿字符串当协议的话，改一次措辞这道闸就静默失效——而它失效的形态是作者又一次
    等了几分钟什么都没有，没有任何东西会报错。
    """
    from novel_harness.agent import index as index_module
    from novel_harness.agent.ports import ToolRefused

    with pytest.raises(UnknownCharacter) as missing:
        index_module.resolve_one("姜源初", None)
    assert missing.value.surface == "姜源初"
    assert isinstance(missing.value, ToolRefused), (
        "它必须仍然是一次普通的拒绝：`dispatch` 的四种失败都是 `ok=False` 的正常返回"
    )

    # 歧义那一支**不是** `UnknownCharacter`：它不该被这道闸数进去，重试是对的。
    resolutions = book.store.resolve(book.project_id, [SHARED_SURFACE])
    with pytest.raises(ToolRefused) as ambiguous:
        index_module.resolve_one(SHARED_SURFACE, resolutions[0])
    assert not isinstance(ambiguous.value, UnknownCharacter), (
        "歧义被当成「角色册里没有」数进闸里 —— 那会把一条本来该重试的路也停掉"
    )
