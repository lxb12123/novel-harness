"""**一轮边跑边说话，而且停得下来问**（[ADR 0024](../docs/adr/0024-a-turn-is-a-conversation-not-a-black-box.md)）。

这份文件量四件事，第一件是它存在的理由：

1. **事件流是一个全新的输出面，而边界一不可回收**（ADR 0024「修复成本」点名最贵的那条）。
   一轮的返回一直是**投影过的**（工具查到了什么根本不上屏，只有一个「查了 3 次」的数），
   事件流会把中间过程摊开——那些中间过程里就有工具的原返回。**所以先有网再有管子**：
   喂一本带毒的书跑完整一轮，把这一轮发出的**每一条事件**拼起来搜毒。
2. **不传回调 = 今天的行为逐字节不变。** 判据不是「看起来一样」：两次跑之间
   发给模型的每一份 payload、每一份账单原料、每一次落库的历史、最后那份回执，
   **逐字节对拷**。
3. **「停下来问」真的把这一轮收掉了。** ADR 0024 说「在对话里，停下来问 = 这一轮说完了」
   ——那句话在 loop 里**不是自动成立的**（这个 for 之后还有下一次模型调用）。
   所以这里有一条**探针**：同样的剧本换一条普通工具，模型会接着往下跑。
   探针红了才说明上面那条断言在测一个真的机制。
4. **发不出去不许拿走这一轮。** 作者已经为它付过钱了。

── 措辞的判据借的是别人的尺 ──────────────────────────────────────────────

`tests/test_wording_guard.py::dev_shapes` 是全仓唯一那份形状网，这里**不抄第二份**。
事件分两半：引擎写的那半句（`said_to_author`）要过网，模型写的字（`text`）不过——
那是作者要读的正文，英文、代码、什么都可能有，用形状网去判它是把守卫指向了错的东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import novel_harness.agent.drafting as drafting
import novel_harness.agent.tools as agent_tools
from test_agent_drafting import (
    DRAFT,
    ENDPOINT,
    MODEL,
    FakeDrafting,
    _NoSummaries,
    _root,
    _write,
)
from test_agent_loop import Ledger, ScriptedModel, a_context, say, wants
from test_agent_tools import CHAPTER, SECRET_CONTENT, TWIST, World, conn, leaks, world
from test_wording_guard import dev_shapes

from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    EventFn,
    LedgerFn,
    PersistFn,
    Role,
    StopReason,
    TurnEvent,
    TurnEventKind,
    TurnLimits,
    run_turn,
    start_conversation,
    stop_wording,
)
from novel_harness.agent.model import ProviderModelPort
from novel_harness.agent.tools import (
    TOOL_NAMES,
    TOOL_TABLE,
    AskAuthorArgs,
    AuthorQuestion,
    SceneConstraintsArgs,
    ToolSpec,
    dispatch,
)
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import (
    ReasoningEffort,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.draft.provider import ProviderConfig, ToolCall
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

__all__ = ["conn", "world"]
"""两个 fixture 从 `test_agent_tools` 借来（同 `conftest.py` 那次 re-export）。

**借而不是另建一份**：那儿的带毒书已经把三种存法都摆齐了（`props.twist` /
`SecretDetail.description` / 挂在 Location 上的 `plot_note`），另写一份只会漏掉其中一种，
而漏掉的那一种正是下一次真的会漏出去的那一种。
"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Screen:
    """一块**会记账的屏幕**：这一轮往它上面推过什么。

    它同时是「界面」的替身和搜毒的采样点——所以它一条都不许漏收，
    也不许自己做任何过滤（过滤了就等于在测一个我们希望的事件流，不是真的那个）。
    """

    events: list[TurnEvent] = field(default_factory=list)
    explode: bool = False

    def __call__(self, event: TurnEvent) -> None:
        if self.explode:
            raise RuntimeError("界面掉线了")
        self.events.append(event)

    @property
    def kinds(self) -> list[TurnEventKind]:
        return [event.kind for event in self.events]

    def of(self, kind: TurnEventKind) -> list[TurnEvent]:
        return [event for event in self.events if event.kind is kind]

    @property
    def everything_pushed(self) -> str:
        """**这一轮推上屏的每一个字节**，拼成一条。搜毒搜的是它。

        逐条 `model_dump_json()` 而不是只拼 `said_to_author` + `text`：泄漏可以躺在
        任何一个字段上（一个多出来的 `detail`、一个原样带出去的工具名），而它们的
        共同点只有一个——**它被推给界面了**。
        """
        return "\n".join(event.model_dump_json() for event in self.events)


def a_turn(*script: Any, screen: Screen | None = None, **kwargs: Any) -> Any:
    """跑一轮，**默认接一块屏**。返回 `(结果, 模型, 账本, 屏)`。

    显式给 `on_event=` 时用它（那时第四个返回值是那块空屏，别读它）。
    """
    hooked = kwargs.pop("on_event", None)
    board = screen if screen is not None else Screen()
    model = ScriptedModel(script=list(script))
    ledger = Ledger()
    conversation = kwargs.pop("conversation", None) or start_conversation().with_author("写第 7 章")
    result = run_turn(
        conversation,
        context=kwargs.pop("context", None) or a_context(),
        model=model,
        ledger=ledger,
        on_event=hooked if hooked is not None else board,
        **kwargs,
    )
    return result, model, ledger, board


def asks(question: str, *options: str, text: str = "") -> Any:
    """模型这一步要求「问作者一句」。"""
    return wants(
        ("ask_author", json.dumps({"question": question, "options": list(options)})),
        text=text,
    )


ASKED = "这一场你想让萧决知道那件事吗？"
OPTIONS = ("让他知道", "先瞒着他")


# ══════════════════════════════════════════════════════════════════════════
# 一、不传回调 = 逐字节不变（ADR 0024「换回请求/响应」那条退路的前提）
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Transcript:
    """一轮里**穿过引擎边界**的所有东西。两次跑对拷的就是它。"""

    payloads: list[str]
    receipts: list[str]
    persisted: list[str]
    result: str

    @staticmethod
    def of(*, on_event: EventFn | None) -> Transcript:
        payloads: list[str] = []
        receipts: list[str] = []
        persisted: list[str] = []

        def ledger(receipt: Any) -> None:
            # ⚠️ **`elapsed_ms` 不进这次对拷。** 它是秒表读数（实测墙钟，
            # `agent/loop.py` 里 `int((perf_counter() - started) * 1000)`），不是钱。
            # 两遍跑本来就不可能花一样久：假模型瞬间返回，两遍通常都不到 1 毫秒、
            # 双双取整成 0 于是相等，但机器忙一下就会一边 0 一边 1——**逐字节对拷当场
            # 假红**，喊的还是「账变了」。三次全量跑里响过一次。
            # 一条会喊狼来了的守卫迟早被人关掉，而它守的那件事是真的要紧
            # （开着进度条会不会偷偷多花钱），所以剔掉尺子上这一格，别放宽判据。
            receipts.append(receipt.model_dump_json(exclude={"elapsed_ms"}))

        def persist(conversation: Any) -> None:
            persisted.append(conversation.model_dump_json())

        model = ScriptedModel(
            script=[
                wants(("book_index", "{}"), text="我先翻一下目录"),
                wants(("chapter_text", '{"chapter": 7}')),
                say("好了，这一场我这样写：……", prompt_tokens=1_200, completion_tokens=300),
            ]
        )
        result = run_turn(
            start_conversation("冷一点").with_author("写第 7 章"),
            context=a_context(working_chapter=7),
            model=model,
            ledger=ledger,
            persist=persist,
            on_event=on_event,
        )
        # **量的是真正发出去那份 payload**，不是「调了几次」：漂一个字节这里就红。
        payloads = [
            json.dumps(messages, ensure_ascii=False, sort_keys=True)
            for messages in model.calls
        ]
        return Transcript(
            payloads=payloads,
            receipts=receipts,
            persisted=persisted,
            result=result.model_dump_json(),
        )


def test_not_passing_the_callback_leaves_the_turn_byte_for_byte_identical() -> None:
    """**ADR 0024 那条退路的前提**：适配器扔掉、`on_event` 空着不叫 = 今天的行为。

    「看起来一样」不算。这里对拷四样穿过引擎边界的东西：发给模型的每一份 payload、
    每一份账单原料、每一次落库的历史、最后那份回执。**任何一样漂了都说明事件流
    改变了这一轮本身**，而那正是这条退路便宜与否的分界线。
    """
    quiet = Transcript.of(on_event=None)
    watched = Transcript.of(on_event=Screen())

    assert quiet.payloads == watched.payloads, "有人在听的时候发给模型的东西变了"
    assert quiet.receipts == watched.receipts, "账变了 —— 事件流不许影响钱"
    # 自守卫：**排除的只有秒表那一格**。钱那几格必须还在对拷里，否则这条断言就空了
    # ——「把假红的字段排掉」和「把判据放宽到什么都不查」只差一次顺手。
    for money in ("prompt_tokens", "completion_tokens", "cost"):
        assert all(money in r for r in quiet.receipts), f"{money} 被排出对拷了 —— 这条守卫空了"
    assert all("elapsed_ms" not in r for r in quiet.receipts), "秒表那一格又回来了"
    assert quiet.persisted == watched.persisted, "落库的历史变了 —— 事件流不许进 canonical"
    assert quiet.result == watched.result, "回执变了"
    assert len(quiet.payloads) == 3 and quiet.receipts, "剧本没跑起来，这条对拷是空的"


def test_the_new_hook_has_the_same_shape_as_the_two_that_were_already_there() -> None:
    """**照既有的两个接线口，不发明第三种形状**（`PersistFn` / `LedgerFn`）。

    三个都是 `Callable[[一件东西], None]`：一个普通函数就能接，没有协议、没有基类、
    没有要实现的方法。形状一致的价值不在整齐——它让「HTTP 壳那一层怎么接」这个问题
    对第三个接线口不用重新回答一遍。
    """
    from typing import get_args

    shapes = {
        "persist": get_args(PersistFn),
        "ledger": get_args(LedgerFn),
        "event": get_args(EventFn),
    }
    for name, (params, returns) in shapes.items():
        assert len(params) == 1, f"{name} 收的不是一件东西"
        assert returns in (None, type(None)), f"{name} 有返回值 —— 接线口不该有"


# ══════════════════════════════════════════════════════════════════════════
# 二、一轮说得出自己在干什么
# ══════════════════════════════════════════════════════════════════════════


def test_a_turn_says_what_it_is_doing_while_it_is_doing_it() -> None:
    """ADR 0024 决策一：在查什么 / 查完了 / 它说了什么 / 为什么停。

    顺序也是断言的一部分：**「正在查」必须排在「查完了」前面**——反过来的话
    界面上那个转圈的图标会在结果已经到了之后才出现。
    """
    result, _, _, screen = a_turn(
        wants(("book_index", "{}"), text="我先翻一下目录"),
        say("找到了，这一场我这样写：……"),
    )
    assert result.reason is StopReason.DONE
    assert screen.kinds == [
        TurnEventKind.REPLY_TEXT,
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_FINISHED,
        TurnEventKind.REPLY_TEXT,
        TurnEventKind.TURN_STOPPED,
    ]
    started, finished = screen.of(TurnEventKind.TOOL_STARTED)[0], screen.of(
        TurnEventKind.TOOL_FINISHED
    )[0]
    assert started.tool == finished.tool == "book_index"
    assert finished.ok is True
    assert screen.of(TurnEventKind.TURN_STOPPED)[0].reason is StopReason.DONE


def test_the_thing_it_said_on_the_way_gets_out_too() -> None:
    """只叫工具的那一步它也会说一句（「我先翻一下目录」）。

    **那句话今天只活在对话历史里**，一轮跑完才随投影一起出去——事件流不喊它，
    界面上就是几十秒的空白配一个转圈的图标，而 ADR 0024 §2 要治的正是那个。
    """
    _, _, _, screen = a_turn(
        wants(("book_index", "{}"), text="我先翻一下目录"),
        say("好了"),
    )
    assert [e.text for e in screen.of(TurnEventKind.REPLY_TEXT)] == ["我先翻一下目录", "好了"]


def test_a_wide_batch_says_which_one_of_how_many() -> None:
    """一批好几件时**每一件都说得出自己是第几件**，否则界面上是一排一样的转圈。"""
    _, _, _, screen = a_turn(
        wants(
            ("book_index", "{}"),
            ("book_index", '{"from_chapter": 5}'),
            ("book_index", '{"from_chapter": 9}'),
        ),
        say("看完了"),
    )
    started = screen.of(TurnEventKind.TOOL_STARTED)
    assert [(e.index, e.total) for e in started] == [(1, 3), (2, 3), (3, 3)]
    assert "（2/3）" in started[1].said_to_author


def _slow_handler(args: SceneConstraintsArgs, context: Any) -> SceneConstraintsArgs:
    """一条慢的、没有副作用的工具 —— 也就是 `draft_chapter` 在批里的形状。"""
    import time

    time.sleep(0.02)
    return args


SLOW_PROBE = ToolSpec(
    name="_slow_probe",
    description="慢，而且没有副作用。",
    args=SceneConstraintsArgs,
    handler=_slow_handler,
    label="做一件慢事",
    concurrent=True,
)


def test_a_batch_that_runs_at_once_says_so_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """**一个并发窗口是一次跑完的**（ADR 0022 的一批三稿），事件也必须这么说。

    照 `take()` 的顺序在外面喊，喊出来的会是「第 1 件开始 → 第 1 件好了 → 第 2 件开始」
    ——一句**读起来完全正常的假话**：三件事其实是同时在跑、同时跑完的。
    所以那一声由执行器喊（`BatchRunner.on_start`），窗口里那几条一起喊。
    """
    monkeypatch.setitem(agent_tools.TOOLS, SLOW_PROBE.name, SLOW_PROBE)
    _, _, _, screen = a_turn(
        wants(*[(SLOW_PROBE.name, json.dumps({"chapter": n})) for n in (1, 2, 3)]),
        say("三件都做完了"),
        limits=TurnLimits(max_steps=3, parallel_tools=3),
    )
    kinds = [k for k in screen.kinds if k in (TurnEventKind.TOOL_STARTED, TurnEventKind.TOOL_FINISHED)]
    assert kinds == [TurnEventKind.TOOL_STARTED] * 3 + [TurnEventKind.TOOL_FINISHED] * 3, (
        "三件同时在跑，而事件说的是一件接一件 —— 那是一句读起来很正常的假话"
    )
    assert [e.index for e in screen.of(TurnEventKind.TOOL_STARTED)] == [1, 2, 3]


def test_the_steps_it_decided_not_to_run_do_not_pretend_to_have_run() -> None:
    """闸门在批中间停下来时，**没跑的那几条一声都不许喊**。

    喊了「正在查」却永远没有「查完了」，界面上就是一个永远转下去的图标；
    喊「查完了」更糟——那是一句关于没发生过的事的陈述。
    """
    _, _, _, screen = a_turn(
        wants(*[("book_index", json.dumps({"from_chapter": n})) for n in range(1, 9)]),
        limits=TurnLimits(max_steps=3, max_calls_per_step=6),
    )
    assert screen.of(TurnEventKind.TOOL_STARTED) == []
    assert screen.of(TurnEventKind.TOOL_FINISHED) == []
    assert screen.of(TurnEventKind.TURN_STOPPED)[0].reason is StopReason.BATCH_TOO_WIDE


def test_a_failed_lookup_says_it_failed_and_not_why() -> None:
    """成没成要说，**为什么没成不上屏**——那是工具返回，边界一那道网罩着它。

    「它看得见为什么」这半句是真的：失败原文贴回对话给模型看（`dispatch` 的四种失败
    都是 `ok=False` 的正常返回），只是不推给界面。
    """
    _, _, _, screen = a_turn(
        wants(("character_state", '{"chapter": 7, "character": "查无此人"}')),
        say("换个说法"),
    )
    finished = screen.of(TurnEventKind.TOOL_FINISHED)[0]
    assert finished.ok is False
    assert "查无此人" not in finished.model_dump_json(), "工具返回的原文进了事件"


def test_every_event_can_actually_be_rendered() -> None:
    """一条渲染不出来的事件 = 界面上一个转圈的图标，也就是 ADR 0024 要治的那个东西。

    三条：① 要么有引擎的话、要么有模型的字（这一条在类型上就构造不出反例，
    这儿量的是它没被绕过去）；② 引擎那半句是**说给小说作者听的中文**，过形状网；
    ③ 机器码字段只可能取自封闭集合。
    """
    result, _, _, screen = a_turn(
        wants(("book_index", "{}"), ("没有这个工具", "{}"), text="我查一下"),
        say("好了"),
    )
    assert result.reason is StopReason.DONE
    offenders: dict[str, list[str]] = {}
    for event in screen.events:
        assert event.said_to_author or event.text, f"{event.kind} 这条渲染不出来"
        if found := dev_shapes(event.said_to_author):
            offenders[str(event.kind)] = found
        assert event.tool in TOOL_NAMES or event.tool == ""
    assert not offenders, f"推给界面的话里有研发术语：{offenders}"
    assert len(screen.events) > 4, "样本太薄，上面几条没量到什么"


def test_the_kind_code_itself_would_be_caught_if_it_ever_reached_the_screen() -> None:
    """**守卫的自守卫**（同 `StopReason` 那一条）：措辞表干净不代表码不会漏上屏，
    所以码本身要长成会被咬住的形状。"""
    escaped = [str(kind.value) for kind in TurnEventKind if not dev_shapes(kind)]
    assert not escaped, (
        f"这些事件码一旦被原样摆上屏，形状守卫看不见：{escaped}\n"
        "取值要保持 snake_case —— 那正是 `screenGuard.ts` 第一张网认的形状。"
    )


def test_every_tool_says_how_to_call_it_in_the_authors_words() -> None:
    """**加一条工具就要给它一句中文**（`ToolSpec.label`）。

    空着的后果不是崩：`tool_label()` 会给一句通用说法，于是屏幕上永远是「正在查一样东西」
    ——一个不会报错、只是越来越没用的界面。所以这条断言在表上，不在事件上。
    """
    missing = [spec.name for spec in TOOL_TABLE if not spec.label.strip()]
    assert not missing, f"这几条工具没有说给作者听的说法：{missing}"
    for spec in TOOL_TABLE:
        assert not dev_shapes(spec.label), f"{spec.name} 那句说法里有研发术语"
        assert any("一" <= ch <= "鿿" for ch in spec.label)


def test_a_tool_name_the_model_invented_never_reaches_the_screen() -> None:
    """**工具名是模型打进来的字。**

    它会幻想工具名（`dispatch` 的第一种失败就是这个），而那个字符串完全由它决定——
    原样推给界面等于给了它一条把任意一段话摆到作者屏幕上的路。所以事件上的 `tool`
    只可能是表里的名字，认不出的一律空，那句话走通用说法。
    """
    invented = f"读一下{TWIST}"
    _, _, _, screen = a_turn(
        wants((invented, "{}")),
        say("换个说法"),
    )
    pushed = screen.everything_pushed
    assert invented not in pushed and TWIST not in pushed
    assert [e.tool for e in screen.of(TurnEventKind.TOOL_FINISHED)] == [""]
    assert agent_tools.UNNAMED_TOOL_LABEL in screen.of(TurnEventKind.TOOL_STARTED)[0].said_to_author


# ══════════════════════════════════════════════════════════════════════════
# 三、边界一在事件流上 —— **先有网再有管子**
# ══════════════════════════════════════════════════════════════════════════
#
# ADR 0024「修复成本」：「一条事件一旦把秘密推到了屏幕上（而对话是持久化的），
# 改代码删不掉它。所以那张网必须先于管子存在。」


class _LeakyResult(SceneConstraintsArgs):
    """一个**故意泄漏**的出参：它把秘密的 twist 原样装在返回里。

    不是假想的坏写法——`tests/test_agent_tools.py` 里那个探针证明过工具返回**能**带毒
    （直接返回 `Node` 是最省事的写法）。这里要问的是下一个问题：
    **带毒的工具返回会不会顺着事件流上屏。**
    """

    detail: str = ""


def _leaky_handler(args: SceneConstraintsArgs, context: Any) -> _LeakyResult:
    return _LeakyResult(chapter=args.chapter, detail=TWIST)


LEAKY_PROBE = ToolSpec(
    name="_leaky_probe",
    description="故意把秘密装进返回里。",
    args=SceneConstraintsArgs,
    handler=_leaky_handler,
    label="做一件带毒的事",
)


def a_poisoned_turn(world: World, monkeypatch: pytest.MonkeyPatch) -> Screen:
    """真书 + 真工具 + 一条**返回真的带毒**的工具 + 一个**名字带毒**的幻想工具，跑完一轮。"""
    monkeypatch.setitem(agent_tools.TOOLS, LEAKY_PROBE.name, LEAKY_PROBE)
    model = ScriptedModel(
        script=[
            wants(
                ("scene_constraints", json.dumps({"chapter": CHAPTER})),
                ("character_state", json.dumps({"chapter": CHAPTER, "character": "萧决"})),
                ("chapter_text", json.dumps({"chapter": CHAPTER})),
                (LEAKY_PROBE.name, json.dumps({"chapter": CHAPTER})),
                (f"查一下{TWIST}", "{}"),
                text="我先查一下这一章不能说破什么。",
            ),
            # 显示名**必须**能穿过去（反向断言）：它就是作者要看的东西。
            say("查完了：这一章不能说破血脉秘密，所以那一段我绕开写。"),
        ]
    )
    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=world.context(working_chapter=CHAPTER),
        model=model,
        ledger=Ledger(),
        on_event=screen,
        limits=TurnLimits(max_steps=4, tool_failure_limit=5),
    )
    assert result.reason is StopReason.DONE, result.said_to_author
    assert len(screen.of(TurnEventKind.TOOL_FINISHED)) == 5, "采样计划自己漂了"
    return screen


def test_no_event_this_turn_pushed_a_single_word_of_the_secret(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这一条是本节存在的理由**（ADR 0024 点名不可回收的那一条）。

    一轮的返回是投影过的：工具查到了什么根本不上屏，只有一个「查了几次」的数。
    事件流把中间过程摊开了——所以这里把这一轮推给界面的**每一条**拼起来搜毒。
    """
    screen = a_poisoned_turn(world, monkeypatch)
    offenders = leaks("这一轮推给界面的事件", screen.everything_pushed)
    assert not offenders, (
        "事件流把秘密推到屏幕上了：\n  " + "\n  ".join(offenders) + "\n"
        "一轮的返回一直是投影过的（工具查到了什么不上屏），而事件流会把中间过程摊开。\n"
        "这是**不可回收的**：对话是持久化的，推上屏过的那段话改代码删不掉（ADR 0024）。"
    )


def test_that_net_is_not_searching_an_empty_string(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫两条。** 一个永远绿的守卫比没有守卫更糟，因为它还提供安全感。

    ① 这一轮里毒**真的存在**（带毒工具的返回里有 twist，它进了对话历史）；
    ② 那段字**如果**被推给界面，网抓得住它。
    """
    monkeypatch.setitem(agent_tools.TOOLS, LEAKY_PROBE.name, LEAKY_PROBE)
    outcome = dispatch(
        ToolCall(id="p", name=LEAKY_PROBE.name, arguments=json.dumps({"chapter": CHAPTER})),
        world.context(),
    )
    assert outcome.ok and TWIST in outcome.content, "探针自己就不带毒 —— 上面那条是空的"

    poisoned_screen = Screen()
    poisoned_screen(TurnEvent.reply_text(outcome.content))
    caught = leaks("一条把工具返回推上屏的事件", poisoned_screen.everything_pushed)
    assert caught, "网看不见一条装着工具返回的事件 —— 上面那条断言是永远绿的"
    assert len(SECRET_CONTENT) >= 3, "上游那份毒清单缩水了"


def test_the_author_still_sees_which_constraint_it_is_talking_about(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**反向断言：收窄过头是同一个 bug 的另一面。**

    事件流里只剩「查完了」而作者看不出它在说哪一条约束，这块屏幕就白开了。
    显示名（`血脉秘密`）是既有闸门今天已经在交的东西（`NodeRef.name`），它必须穿得过去
    ——**它和秘密的内容 tell 是两件事**，这一条和上面那条一起才定义了那道线在哪儿。
    """
    screen = a_poisoned_turn(world, monkeypatch)
    pushed = screen.everything_pushed
    assert "血脉秘密" in pushed, "显示名被收掉了 —— 作者看不出它在绕开哪一条"
    assert f"第 {CHAPTER} 章" in pushed, "查的是哪一章也该说得出来"
    said = " ".join(e.said_to_author for e in screen.events)
    assert "正在" in said and "已查询" in said


# ══════════════════════════════════════════════════════════════════════════
# 四、问作者：**模型决定什么时候问，作者决定答什么**
# ══════════════════════════════════════════════════════════════════════════


def test_asking_the_author_ends_the_turn_right_there() -> None:
    """**ADR 0024 的收场**：在对话里，停下来问就等于这一轮说完了。

    剧本里问句后面**还跟着一个起草**。它一次都不许跑——不然作者会同时收到一个问题
    和一份照猜写出来的稿子，而那正是这条工具要去掉的东西（**钱也已经花掉了**）。
    """
    result, model, _, screen = a_turn(
        wants(
            ("ask_author", json.dumps({"question": ASKED, "options": list(OPTIONS)})),
            ("draft_chapter", json.dumps({"chapter": 7, "goal": "照我猜的写"})),
            text="有件事我拿不准。",
        ),
        say("这一句永远到不了"),
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    assert result.asked == AuthorQuestion(question=ASKED, options=OPTIONS)
    assert len(model.calls) == 1, "问完了还接着叫模型 —— 这一轮没收住"
    assert result.tool_calls == 1, "问完了还去跑那次起草 —— 作者的钱花在一个猜出来的稿子上"

    asked = screen.of(TurnEventKind.ASKED_AUTHOR)
    assert len(asked) == 1 and asked[0].asked is not None
    assert asked[0].asked.options == OPTIONS
    assert screen.of(TurnEventKind.TURN_STOPPED)[0].reason is StopReason.ASKED_AUTHOR


def test_without_that_stop_it_would_have_gone_on_guessing() -> None:
    """**探针**：上面那条只有在「问完就停」是一个真机制时才算数。

    同一个剧本，把问句换成一条普通工具——模型接着往下跑，那次起草真的发生。
    所以上面那条量到的不是「剧本恰好只有一步」。
    """
    result, model, _, _ = a_turn(
        wants(
            ("book_index", "{}"),
            ("draft_chapter", json.dumps({"chapter": 7, "goal": "照我猜的写"})),
            text="有件事我拿不准。",
        ),
        say("我接着往下写了"),
    )
    assert result.reason is StopReason.DONE
    assert len(model.calls) == 2, "换成普通工具之后这一轮就不该停在第一步"
    assert result.tool_calls == 2


def test_the_answer_is_just_the_next_thing_the_author_says() -> None:
    """**「不需要 interrupt/resume 机制」是可验证的**（ADR 0024）。

    停在一个问题上的会话，执行态和别的停法一模一样：一串 message，**一个缺的
    `tool_result` 都没有**。作者答一句就是 `with_author(...)`，下一轮照常跑——
    挂起/恢复那套机制一行都不用写。
    """
    result, _, _, _ = a_turn(asks(ASKED, *OPTIONS))
    assert result.conversation.pending_calls == (), "问一句不该在执行态里留下一个缺口"
    # 模型在下一轮看得见：它自己那次调用 + 一条「已经摆给他了」+ 作者的回答。
    tail = result.conversation.messages[-2:]
    assert tail[0].role is Role.ASSISTANT and tail[0].tool_calls
    assert tail[1].role is Role.TOOL and agent_tools.ASK_ACKNOWLEDGED in tail[1].content

    answered = result.conversation.with_author("先瞒着他")
    again, model, _, _ = a_turn(say("好，那我这样写"), conversation=answered)
    assert again.reason is StopReason.DONE
    assert model.calls[0][-1] == {"role": "user", "content": "先瞒着他"}


def test_a_question_that_came_back_from_a_crash_ends_the_turn_too() -> None:
    """进程死在「模型要求问一句」和「派发」之间，那个问题会以 `pending` 的身份回来。

    补跑它照样收掉这一轮。**少了这一支**，这一轮会带着一个没人答的问题接着往下跑
    ——而 resume 那条路是 ADR 0019 敢不上图编排的全部理由，它不该有第二套规矩。
    """
    half_done = start_conversation().with_author("写第 7 章").extended(
        AgentMessage(
            role=Role.ASSISTANT,
            content="有件事我拿不准。",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name="ask_author",
                    arguments=json.dumps({"question": ASKED, "options": list(OPTIONS)}),
                ),
            ),
        )
    )
    assert len(half_done.pending_calls) == 1, "样本没造出「缺一个结果」的局面"

    result, model, _, screen = a_turn(say("这一句永远到不了"), conversation=half_done)
    assert result.reason is StopReason.ASKED_AUTHOR
    assert result.asked is not None and result.asked.question == ASKED
    assert model.calls == [], "补跑出来的一次提问之后不该再叫模型"
    assert screen.of(TurnEventKind.ASKED_AUTHOR)


def test_a_question_shaped_like_a_paragraph_is_refused_not_shown() -> None:
    """**「这是个问题、我在等你」必须在结构上分得开**（ADR 0024）。

    一个问句混在普通回话里，作者会当成陈述句翻过去。所以出参形状是「几个可点的选项」，
    而形状不对的那一次是一次**普通的工具失败**——模型看得见为什么，自己改。
    """
    only_one = a_turn(
        wants(("ask_author", json.dumps({"question": ASKED, "options": ["随便"]}))),
        say("好，我改一下"),
    )
    result, model, _, screen = only_one
    assert result.reason is StopReason.DONE, "参数填错的一次不是一次提问，不该收掉这一轮"
    assert result.asked is None
    assert len(model.calls) == 2
    assert screen.of(TurnEventKind.ASKED_AUTHOR) == []
    assert screen.of(TurnEventKind.TOOL_FINISHED)[0].ok is False


def test_the_options_have_to_fit_on_a_button() -> None:
    """一段散文当选项 = 屏幕上一堵墙，而作者要的是点一下。"""
    with pytest.raises(ValueError):
        AskAuthorArgs(question=ASKED, options=("好" * 200, "算了"))
    with pytest.raises(ValueError):
        AskAuthorArgs(question="问" * 200, options=OPTIONS)
    with pytest.raises(ValueError):
        AskAuthorArgs(question=ASKED, options=("   ", "算了"))
    fine = AskAuthorArgs(question=ASKED, options=OPTIONS)
    assert fine.options == OPTIONS


def test_the_engine_does_not_put_one_word_into_the_question() -> None:
    """**「问句里不许夹带秘密」在引擎这一侧只能做到这一步**（ADR 0005）。

    「这句话是不是说破了秘密」要回答「这句话是什么意思」——那是语义判断，v1 不做。
    引擎能保证的是**它自己一个字都没加**：那条 handler 里没有任何数据来源，
    出参逐字段就是入参。这一条按**源码**量，因为它是一条关于「没有什么」的断言。
    """
    args = AskAuthorArgs(question=ASKED, options=OPTIONS)
    result = agent_tools._handle_ask_author(args, None)  # type: ignore[arg-type]
    assert (result.question, result.options) == (ASKED, OPTIONS)

    source = Path(agent_tools.__file__).read_text(encoding="utf-8")
    whole = source[source.index("def _handle_ask_author") : source.index("def _handle_read_draft")]
    # **只扫代码，不扫 docstring**：那段说明里就写着 `context.` 三个字，
    # 拿它去扫等于让一句解释自己触发断言（同 `dev_shapes` 那条「只扫真的会上屏的字符串」）。
    body = whole.rsplit('"""', 1)[-1]
    assert "return AskAuthorResult" in body, "切歪了 —— 下面那条在扫一个空字符串"
    assert "context." not in body, (
        "问作者那条 handler 碰了 `ToolContext` —— 它一旦有数据来源，"
        "「引擎往问句里加不了一个字」这句话就不再是结构保证了"
    )


def test_the_question_is_still_there_after_the_turn_is_over() -> None:
    """事件是**跑的过程**，而作者可能是在这一轮结束之后才打开那段对话
    （换台机器、刷新页面、三个月后回来）。那时说得出「它问了你什么」的只有回执和历史。"""
    result, _, _, _ = a_turn(asks(ASKED, *OPTIONS))
    assert result.asked is not None
    assert result.said_to_author == stop_wording(StopReason.ASKED_AUTHOR)
    assert not dev_shapes(result.said_to_author)


# ══════════════════════════════════════════════════════════════════════════
# 五、界面掉线不许拿走这一轮
# ══════════════════════════════════════════════════════════════════════════


def test_a_dead_screen_does_not_take_down_a_turn_the_author_already_paid_for() -> None:
    """**发不出去要吞，而记账和落库不吞。**

    差别是后果：账记不上 = 钱花了没记上（继续跑只会让他付第二次），
    发不出去 = 一个界面少看见一行字。而「弄崩」在这儿的形态是真的崩——
    `run_turn` 外面没有 try/except（那是有意的），异常会一路穿到 HTTP 壳。
    """
    dead = Screen(explode=True)
    result, model, ledger, _ = a_turn(
        wants(("book_index", "{}"), text="我查一下"),
        say("好了"),
        screen=dead,
    )
    assert result.reason is StopReason.DONE
    assert result.reply == "好了"
    assert len(ledger.receipts) == len(model.calls) == 2, "账没受影响"
    assert dead.events == [], "样本没炸 —— 这条测试是空的"


def test_a_screen_that_dies_halfway_keeps_the_rest_of_the_turn() -> None:
    """**第一声就炸掉的那一档**上面量过了，这一条量「跑到一半才掉线」。

    它是真实形态（浏览器标签页被关掉），而且它更容易漏：吞在错的层里的话，
    前半截事件已经发出去了，作者看到的是一轮跑到一半突然消失。
    """
    calls: list[TurnEvent] = []

    def flaky(event: TurnEvent) -> None:
        calls.append(event)
        if len(calls) >= 2:
            raise RuntimeError("界面掉线了")

    result, _, _, _ = a_turn(
        wants(("book_index", "{}"), text="我查一下"),
        say("好了"),
        screen=None,
        on_event=flaky,
    )
    assert result.reason is StopReason.DONE
    assert len(calls) > 2, "掉线之后就不叫它了 —— 那是把界面的故障变成了引擎的状态"


# ══════════════════════════════════════════════════════════════════════════
# 六、模型吐字那一段：**那些片本来就在我们手里**
# ══════════════════════════════════════════════════════════════════════════


class _Stream:
    """一条会记账的流（同 `tests/test_agent_model.py` 那个形状）。"""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces
        self.closed = False

    def __iter__(self) -> Any:
        for piece in self.pieces:
            yield SimpleNamespace(
                model=MODEL,
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=piece, tool_calls=None),
                        finish_reason=None,
                    )
                ],
            )

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def a_streaming_plan() -> Any:
    """一份 `stream=True` 的 plan。**`stream` 不是这儿挑的**，是 `plan_call` 按冻结阈值
    从输出预算推出来的（同 `tests/test_agent_model.py`）。"""
    long_reply = LengthSpec(
        language=DraftLanguage.ZH, min_units=1, target_units=8_000, max_units=9_000
    )
    plan = plan_call(
        long_reply, ReasoningEffort.OFF, resolve_capabilities(ENDPOINT, MODEL)
    )
    assert plan.stream is True
    return plan


def test_the_pieces_that_were_already_in_our_hands_now_get_out() -> None:
    """ADR 0024 §4：「剩下的不是『实现流式』，是把已经在手里的流接出去。」

    这条量的是那一段线：`_CancellableClient` 上的每一片 → 一条事件。
    **`provider.py` 一个字节都没动**（M2 判分链的运输层）——包的仍然是客户端。
    """
    screen = Screen()
    port = ProviderModelPort(
        ProviderConfig(model=MODEL, base_url=ENDPOINT, api_key="k", temperature=None),
        a_streaming_plan(),
        client=_Client(_Stream(["风雪落在", "肩上。"])),
        on_event=screen,
    )
    result = port([{"role": "user", "content": "写"}], tools=[], cancel=Cancellation())

    assert [e.text for e in screen.of(TurnEventKind.REPLY_DELTA)] == ["风雪落在", "肩上。"]
    assert result.text == "风雪落在肩上。", "递出去的那一份和累加出来的那一份对不上"


def test_nobody_listening_means_the_stream_is_untouched() -> None:
    """**不接事件 = 那条流上一个多余的动作都没有**（同第一节那条逐字节对拷）。"""
    port = ProviderModelPort(
        ProviderConfig(model=MODEL, base_url=ENDPOINT, api_key="k", temperature=None),
        a_streaming_plan(),
        client=_Client(_Stream(["风雪落在", "肩上。"])),
    )
    result = port([{"role": "user", "content": "写"}], tools=[], cancel=Cancellation())
    assert result.text == "风雪落在肩上。"


# ══════════════════════════════════════════════════════════════════════════
# 七、起草那一档：**在写第几稿**
# ══════════════════════════════════════════════════════════════════════════


def a_desk(
    conn: Connection,
    pid: str,
    monkeypatch: pytest.MonkeyPatch,
    screen: Screen,
    *,
    cancel: Cancellation | None = None,
) -> Any:
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        cancel=cancel,
        on_event=screen,
    )


def test_a_draft_says_which_chapter_it_is_writing_and_which_draft_it_became(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """「在写第几稿」这个数**只有起草台知道**（它是候选表现算的）。

    上面那层要拿到它就得去解析工具返回的 JSON，而那是第二处解析点——这个仓库刚把
    「同一件事两处解析」清掉。所以这一声在这儿喊。
    """
    conn = connect(book["db"])
    screen = Screen()
    desk = a_desk(conn, book["pid"], monkeypatch, screen)

    first = _write(desk, conn, book["pid"], 1)
    second = _write(desk, conn, book["pid"], 1)

    assert screen.kinds == [
        TurnEventKind.DRAFT_STARTED,
        TurnEventKind.DRAFT_KEPT,
        TurnEventKind.DRAFT_STARTED,
        TurnEventKind.DRAFT_KEPT,
    ]
    kept = screen.of(TurnEventKind.DRAFT_KEPT)
    assert [e.ordinal for e in kept] == [first.candidate.ordinal, second.candidate.ordinal] == [1, 2]
    assert all(e.chapter == 1 and e.units == len(DRAFT.replace("\n", "")) for e in kept)
    assert "第 2 稿" in kept[1].said_to_author and "第 1 章" in kept[1].said_to_author
    # **同一章的两稿连章号都一样**：没有流号就没法把同时到达的两条流分开摆。
    assert [e.stream for e in screen.of(TurnEventKind.DRAFT_STARTED)] == [1, 2]
    conn.close()


def test_the_pieces_of_a_draft_carry_the_chapter_and_the_stream_they_belong_to(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**一批三稿是同时在飞的**（ADR 0022），三条流的片会交错着到达。

    这条量的是起草台交给客户端的那个接线口：它认得自己是第几章的第几条流。
    捕获的是 `cancellable_client` 的第三个参数——那正是「多递一个『这一片叫什么』的
    闭包」那一段线，忘了接的症状是「开始写」和「写好了」之间什么都没有。
    """
    conn = connect(book["db"])
    screen = Screen()
    sinks: list[Any] = []

    def spy(config: Any, cancel: Any, on_text: Any = None) -> Any:
        sinks.append(on_text)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=None)))

    monkeypatch.setattr(drafting, "cancellable_client", spy)
    desk = a_desk(conn, book["pid"], monkeypatch, screen, cancel=Cancellation())
    _write(desk, conn, book["pid"], 1)

    assert sinks and sinks[0] is not None, "起草那一次调用没带上「这一片叫什么」"
    sinks[0]("风雪落在")
    delta = screen.of(TurnEventKind.DRAFT_DELTA)
    assert [e.text for e in delta] == ["风雪落在"]
    assert delta[0].chapter == 1 and delta[0].stream == 1
    conn.close()


def test_without_a_stop_signal_there_is_no_live_text_and_that_is_not_a_bug(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**能看见它写字，就一定能按停它**——两件事在产品上是捆着的。

    `interruptible` 决定流式（`plan_call`），而它今天等于「接没接停止信号」。
    没接信号的那一档（CLI）只看得见「开始写」和「写好了」，中间没有字。
    钉住它是为了下一个人别把这当成一个 bug 去「修」成默认流式——那条路
    `agent/model.py` 写着为什么不能走（没登记的端点会整个用不了）。
    """
    conn = connect(book["db"])
    screen = Screen()
    desk = a_desk(conn, book["pid"], monkeypatch, screen, cancel=None)
    _write(desk, conn, book["pid"], 1)

    assert screen.of(TurnEventKind.DRAFT_DELTA) == []
    assert screen.of(TurnEventKind.DRAFT_STARTED) and screen.of(TurnEventKind.DRAFT_KEPT)
    conn.close()
