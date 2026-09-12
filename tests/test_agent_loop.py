"""agent loop：**循环归模型，停止条件归代码**（ADR 0019）。

这份文件量三件事，一件比一件贵：

1. **十一种停法都停得下来，而且每一种都说得出自己为什么停** —— 且那句话是说给
   小说作者听的，不是说给维护者听的（判据借 `tests/test_wording_guard.py` 的形状网，
   全仓只有那一份）。
2. **每一次模型调用都记一笔账** —— 板子上已经记着一个同样的洞（`/draft` 一行
   `model_call` 都不写，于是日志页显示的是真实花销的一小部分，看起来却像全部）。
   agent loop 是第二个会大量花钱的地方，所以这里有一条断言：**能不记账的路径一条都没有**。
3. **投影按章号参数化**（边界五）—— ADR 自己点名要的那个场景：
   「先聊第 90 章再回头写第 40 章，断言第 40 章的投影里不含第 90 章的禁说清单」。
   这一条**错的时候不会报错**（产出的是一段读起来完全正常、只是说破了不该说破的正文），
   所以它必须有测试。

── 这里为什么不接真库、真模型 ────────────────────────────────────────────

边界一那张网在 `tests/test_agent_tools.py`（真库、真秘密、四个面），这儿不重复它。
本文件的被测对象是**停与不停、记账与不记账、投影取什么**，它们都不需要真数据——
需要的是一个能按剧本回答的模型和一个会数数的账本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from test_wording_guard import dev_shapes

from novel_harness.agent.loop import (
    AGENT_CAPABILITY,
    AGENT_SYSTEM_PROMPT,
    AgentMessage,
    Cancellation,
    Conversation,
    Mailbox,
    ModelCallReceipt,
    Projection,
    Role,
    StopReason,
    TurnEvent,
    TurnEventKind,
    TurnLimits,
    project,
    run_turn,
    start_conversation,
    stop_wording,
)
from novel_harness.agent.ports import ToolContext, ToolRefused
from novel_harness.agent.tools import ToolSpec, dispatch
from novel_harness.draft.provider import CompletionResult, ProviderError, ToolCall


# ══════════════════════════════════════════════════════════════════════════
# 剧本模型 + 会数数的账本
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ScriptedModel:
    """按剧本一句一句回答。**剧本用完就一直重复最后一条**——那正是「模型不肯收手」。"""

    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    on_call: Any = None

    def __call__(
        self,
        messages: Any,
        *,
        tools: Any,
        cancel: Cancellation,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        self.tool_names = [t["function"]["name"] for t in tools]
        if self.on_call is not None:
            self.on_call(cancel)
        index = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[index]


@dataclass
class Ledger:
    """账本：`record_call()` 那一层的替身。**只数数，不落库**（这一层没有 conn）。"""

    receipts: list[ModelCallReceipt] = field(default_factory=list)
    explode: bool = False

    def __call__(self, receipt: ModelCallReceipt) -> None:
        if self.explode:
            raise RuntimeError("账本坏了")
        self.receipts.append(receipt)


def say(text: str = "写完了。", **kwargs: Any) -> CompletionResult:
    return CompletionResult(text=text, model="deepseek-v4", finish_reason="stop", **kwargs)


def wants(*calls: tuple[str, str], text: str = "") -> CompletionResult:
    return CompletionResult(
        text=text,
        model="deepseek-v4",
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
    )


class FakeStore:
    """`StoryGraph` 的最小替身：这份文件里没有一个断言碰得到图。"""

    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {"store": FakeStore(), "project_id": "project:loop"}
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


def a_turn(*script: CompletionResult, **kwargs: Any):
    """跑一轮，返回 `(结果, 模型, 账本)`。"""
    model = ScriptedModel(script=list(script))
    ledger = Ledger()
    conversation = kwargs.pop("conversation", None) or start_conversation().with_author("写第 7 章")
    context = kwargs.pop("context", None) or a_context()
    result = run_turn(
        conversation,
        context=context,
        model=model,
        ledger=ledger,
        **kwargs,
    )
    return result, model, ledger


# ══════════════════════════════════════════════════════════════════════════
# 一、十一种停法
# ══════════════════════════════════════════════════════════════════════════


def test_the_model_decides_when_to_stop_talking() -> None:
    """正常出口：模型不再叫工具就是它说完了。**这一条是「循环归模型」的全部内容。**"""
    result, model, _ = a_turn(say("这一场我这样写：……"))
    assert result.reason is StopReason.DONE
    assert result.reply == "这一场我这样写：……"
    assert result.steps == 1
    assert len(model.calls) == 1


def test_a_model_that_never_finishes_hits_the_step_ceiling() -> None:
    """剧本只有一条「再查一次」，模型永远不收手 —— 代码要接得住。"""
    result, model, ledger = a_turn(
        wants(("book_index", "{}")),
        limits=TurnLimits(max_steps=3),
    )
    assert result.reason is StopReason.STEP_LIMIT
    assert result.steps == 3
    assert len(model.calls) == 3
    # **步数上限不是「少记几笔账」的借口**：三次调用三笔。
    assert len(ledger.receipts) == 3


def test_the_cost_gate_counts_tokens_because_nobody_knows_the_price() -> None:
    """花费上限只能按 token —— `model_call.cost` 那一列至今没有写入方（BYOK）。"""
    result, _, ledger = a_turn(
        wants(("book_index", "{}"), text="再查一次"),
        limits=TurnLimits(max_steps=10, max_tokens=100),
    )
    assert result.reason is StopReason.COST_LIMIT
    assert result.tokens_reported == 0 and result.calls_without_usage == 1
    assert result.tokens_charged >= 100, "供应商不报 usage 时闸门不许失效"
    assert len(ledger.receipts) == 1
    # 没报的那次**账上仍然是 None**：闸门用估算，账本不许编数。
    assert ledger.receipts[0].prompt_tokens is None


def test_reported_usage_goes_on_the_bill_and_into_the_gate() -> None:
    result, _, ledger = a_turn(
        wants(("book_index", "{}"), text="查"),
        say("好了"),
        # **这个数要比「两次调用的估算」宽。** 没有 usage 时闸门按字数倒推，而那份估算
        # 里**含工具声明**——表里每多一条工具，这儿就更容易假红一次（ADR 0022 加了两条
        # 之后 10,000 当场不够）。这条测的是「报了 usage 就照报的算」，不是闸门本身。
        limits=TurnLimits(max_steps=4, max_tokens=60_000),
    )
    assert result.reason is StopReason.DONE
    assert result.calls_without_usage == 2

    result, _, ledger = a_turn(
        say("好了", prompt_tokens=1200, completion_tokens=300),
    )
    assert result.tokens_reported == 1500
    assert result.tokens_charged == 1500
    assert result.calls_without_usage == 0
    assert ledger.receipts[0].prompt_tokens == 1200


def test_the_author_can_stop_it_in_the_middle_of_a_model_call() -> None:
    """**不是「等这一轮跑完才发现该停了」。**

    真实形态：作者在浏览器上点「停」，那是另一条线；信号一亮，适配器让流式迭代器抛出去，
    `provider.complete()` 把它收敛成 `ProviderError`——loop 看见信号就知道那不是故障。
    """
    cancel = Cancellation()

    def press_stop_mid_call(signal: Cancellation) -> None:
        signal.stop()
        raise ProviderError("stream aborted by caller")

    model = ScriptedModel(script=[say("永远到不了这儿")], on_call=press_stop_mid_call)
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
        cancel=cancel,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.maintainer_note == "", "作者停的不是故障，别把它记成一次报错"


def test_a_provider_failure_is_not_the_same_as_the_author_stopping() -> None:
    """同一个异常类型，两种含义。**判据是信号亮没亮，不是异常长什么样。**"""

    def blow_up(signal: Cancellation) -> None:
        raise ProviderError("模型调用失败(model=deepseek-v4, base_url=https://api.deepseek.com)")

    model = ScriptedModel(script=[say()], on_call=blow_up)
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
    )
    assert result.reason is StopReason.MODEL_UNREACHABLE
    assert "base_url" in result.maintainer_note, "维护者那份诊断要留着"


def test_the_stop_signal_is_seen_between_tool_calls_too() -> None:
    """适配器不理取消信号时的退化：**这一次调用跑完就停**，而不是不停。"""
    cancel = Cancellation()
    model = ScriptedModel(
        script=[wants(("book_index", "{}"), ("book_index", '{"from_chapter": 2}'))],
        on_call=lambda signal: signal.stop(),
    )
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
        cancel=cancel,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.tool_calls == 0, "停下来之后不许再花时间去跑工具"
    assert result.conversation.pending_calls == (), (
        "停在一批工具中间时每个没跑的调用都要配一个壳 —— "
        "wire 上一条带 tool_calls 的 assistant 消息必须被同样多条 tool 消息接住"
    )


def test_going_round_in_circles_is_a_stop_not_a_feature() -> None:
    """无进展之一：同一个工具、**同样的参数**。判据是字节相同，一个语义判断都没有。"""
    result, _, _ = a_turn(
        wants(("book_index", "{}"), text="再看一眼"),
        limits=TurnLimits(max_steps=10, repeat_limit=2),
    )
    assert result.reason is StopReason.REPEATED_CALL


def test_the_same_tool_with_different_arguments_is_progress() -> None:
    """**反向断言**：换了参数就不是打转，不许误判（误判会让这个闸被关掉）。"""
    result, _, _ = a_turn(
        wants(("book_index", "{}")),
        wants(("book_index", '{"from_chapter": 5}')),
        wants(("book_index", '{"from_chapter": 9}')),
        say("找到了"),
        limits=TurnLimits(max_steps=6, repeat_limit=2),
    )
    assert result.reason is StopReason.DONE


def test_saying_nothing_and_doing_nothing_is_a_stop() -> None:
    """无进展之二：既不叫工具也不出正文。**再来一次是拿作者的钱赌**，所以停。"""
    result, model, ledger = a_turn(say(""))
    assert result.reason is StopReason.NO_OUTPUT
    assert len(model.calls) == 1, "什么都没说的一次不许自动重试"
    assert len(ledger.receipts) == 1, "它照样花了钱，照样要记账"


def test_one_tool_failure_goes_back_to_the_model_but_three_stop_the_turn() -> None:
    """**一次失败必须贴回去**——那正是 `dispatch` 把四种失败做成正常返回的理由。"""
    one_bad_then_fine = ScriptedModel(
        script=[
            wants(("没有这个工具", "{}")),
            say("好，那我换个说法"),
        ]
    )
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=one_bad_then_fine,
        ledger=Ledger(),
    )
    assert result.reason is StopReason.DONE
    assert len(one_bad_then_fine.calls) == 2
    fed_back = one_bad_then_fine.calls[-1][-1]
    assert fed_back["role"] == "tool" and "没有名为" in fed_back["content"]

    stuck, _, _ = a_turn(
        wants(("没有这个工具", "{}")),
        limits=TurnLimits(max_steps=10, tool_failure_limit=3),
    )
    assert stuck.reason is StopReason.TOOL_STUCK


def test_a_success_in_between_clears_the_failure_streak() -> None:
    """**连续**才算卡住：同一个工具中间成功过一次，计数归零。"""
    bad = '{"labels": ["根本不是一类东西"]}'
    result, _, _ = a_turn(
        wants(("book_index", bad), ("book_index", "{}"), ("book_index", bad)),
        say("行了"),
        limits=TurnLimits(max_steps=4, tool_failure_limit=2),
    )
    assert result.reason is StopReason.DONE


def test_three_different_hallucinated_tool_names_are_also_stuck() -> None:
    """**只按工具名数会漏掉这一种，而它是最典型的翻车方式。**

    每个瞎编的名字都是一个新的 key，只按名字数的话「三个不同的错名字」永远是三条
    1 次的记录——闸门一次都不响，作者眼看着它一步一步烧到步数上限。
    """
    result, _, _ = a_turn(
        wants(("查一下人物", "{}"), ("读正文", "{}"), ("看看设定", "{}")),
        limits=TurnLimits(max_steps=5, tool_failure_limit=3),
    )
    assert result.reason is StopReason.TOOL_STUCK


def test_a_conversation_that_cannot_be_pruned_any_further_stops_instead_of_eating_the_author(
) -> None:
    """剪枝顺序的最后一档是**不碰**：作者说过的话一句都不删，停下来说人话。"""
    conversation = start_conversation().with_author("写第 7 章，" + "把这一段改得再冷一点，" * 200)
    result, model, _ = a_turn(say("好"), conversation=conversation, budget_units=50)
    assert result.reason is StopReason.CONTEXT_FULL
    assert model.calls == [], "装不下就不该把它发出去"
    assert result.conversation.messages[-1].role is Role.USER


# ══════════════════════════════════════════════════════════════════════════
# 二、措辞：说给作者的那句话
# ══════════════════════════════════════════════════════════════════════════


def test_every_stop_reason_can_say_why_it_stopped_in_the_authors_language() -> None:
    """**判据借 `tests/test_wording_guard.py` 的形状网**（全仓只有那一份，别在这儿抄第二份）。

    这一层的读者是「用 WPS 不想碰命令行」的小说作者。`step_limit` 摆到他脸上和
    `provider_failure：chapter analysis provider failed` 是同一种错。
    """
    offenders: dict[str, list[str]] = {}
    for reason in StopReason:
        text = stop_wording(reason)
        assert text and re.search(r"[一-鿿]", text), f"{reason} 没有一句中文的说法"
        if found := dev_shapes(text):
            offenders[str(reason)] = found
    assert not offenders, f"停下来那句话里有研发术语：{offenders}"

    # 封闭枚举认不出只可能是表漏了行，而漏掉的那一行不该由作者来读。
    assert not dev_shapes(stop_wording("some_brand_new_reason"))  # type: ignore[arg-type]


def test_the_reason_code_itself_would_be_caught_if_it_ever_reached_the_screen() -> None:
    """**守卫的自守卫**：措辞表干净不代表码不会漏上屏，所以码本身要长成会被咬住的形状。"""
    escaped = [str(r.value) for r in StopReason if r is not StopReason.DONE and not dev_shapes(r)]
    assert not escaped, (
        f"这些停止码一旦被原样摆上屏，形状守卫看不见：{escaped}\n"
        "取值要保持 snake_case —— 那正是 `screenGuard.ts` 第一张网认的形状。"
    )


def test_the_maintainers_diagnosis_never_rides_along_with_the_authors_sentence() -> None:
    """`maintainer_note` 里有端点地址和模型名。**它和给作者的那句话是两个字段。**

    同 `ExtractionRunError.message`（`test_wording_guard.py` 那条钉着它不上屏）。
    """
    model = ScriptedModel(
        script=[say()],
        on_call=lambda _s: (_ for _ in ()).throw(
            ProviderError("failed to connect to https://api.deepseek.com/v1 for deepseek-v4")
        ),
    )
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
    )
    assert dev_shapes(result.maintainer_note), "样本不带研发术语的话下面那条是空转的"
    assert not dev_shapes(result.said_to_author)


# ══════════════════════════════════════════════════════════════════════════
# 三、记账
# ══════════════════════════════════════════════════════════════════════════


def test_there_is_no_path_that_calls_the_model_without_writing_a_receipt() -> None:
    """**这一条是这份文件最硬的一条。**

    板子上记着：`/draft` 一行 `model_call` 都不写，于是日志页显示的是真实花销的一小部分，
    看起来却像全部。agent loop 是第二个会大量花钱的地方——每一种停法都要验一遍
    「模型调了几次，账上就有几笔」。
    """
    cases: dict[str, tuple[tuple[CompletionResult, ...], dict[str, Any]]] = {
        "done": ((say("好"),), {}),
        "step_limit": ((wants(("book_index", "{}")),), {"limits": TurnLimits(max_steps=2)}),
        "no_output": ((say(""),), {}),
        "cost_limit": (
            (wants(("book_index", "{}")),),
            {"limits": TurnLimits(max_steps=5, max_tokens=1)},
        ),
        "tool_stuck": (
            (wants(("没有这个工具", "{}")),),
            {"limits": TurnLimits(max_steps=5, tool_failure_limit=1)},
        ),
        "repeated_call": (
            (wants(("book_index", "{}")),),
            {"limits": TurnLimits(max_steps=5, repeat_limit=1)},
        ),
    }
    for name, (script, kwargs) in cases.items():
        result, model, ledger = a_turn(*script, **kwargs)
        assert len(ledger.receipts) == len(model.calls) > 0, f"{name} 这条路径漏账了"
        assert result.steps == len(ledger.receipts)
        assert all(r.capability == AGENT_CAPABILITY for r in ledger.receipts)
        assert all(r.prompt_hash and r.prompt_bytes for r in ledger.receipts)


def test_the_capability_has_a_chinese_name_on_the_log_page() -> None:
    """`model_call.capability` 认不出的是**原样回吐**的 —— 新长出一种花钱的动作就要补一行。

    国际化第四批·笔二起，`CAPABILITY_LABEL` 搬去了前端 `backendMessages.ts`
    （后端不再知道该说哪种界面语言），不再调用一个已经不存在的
    `activity._capability_label`。
    """
    from test_wording_guard import BACKEND_MESSAGES, _ts_const_object_entry, _ts_const_object_keys

    source = BACKEND_MESSAGES.read_text(encoding="utf-8")
    assert AGENT_CAPABILITY in _ts_const_object_keys(source, "CAPABILITY_LABEL"), (
        f"{AGENT_CAPABILITY!r} 不在 CAPABILITY_LABEL 里 —— 日志页会原样显示这个英文字符串"
    )
    label = _ts_const_object_entry(source, "CAPABILITY_LABEL", AGENT_CAPABILITY, "zh")
    assert re.search(r"[一-鿿]", label) and not dev_shapes(label)


def test_a_broken_ledger_is_loud() -> None:
    """账记不上不许吞：那一次调用**已经花过钱了**，静默吞掉正是 `/draft` 那个洞的形状。"""
    with pytest.raises(RuntimeError):
        run_turn(
            start_conversation().with_author("写第 7 章"),
            context=a_context(),
            model=ScriptedModel(script=[say()]),
            ledger=Ledger(explode=True),
        )


def test_the_receipt_carries_the_tool_table_into_the_hash() -> None:
    """同一段对话配不同的工具表是两次不同的调用 —— 事后能回答这个的只有那个哈希。"""
    _, _, ledger = a_turn(say("好"))
    receipt = ledger.receipts[0]
    assert b"scene_constraints" in receipt.prompt_bytes
    assert len(receipt.prompt_hash) == 64


# ══════════════════════════════════════════════════════════════════════════
# 四、投影按章号（边界五）—— ADR 自己点名要的那个测试
# ══════════════════════════════════════════════════════════════════════════

CH90_LIST = '{"chapter": 90, "must_not_reveal": ["血脉秘密"]}'
CH40_LIST = '{"chapter": 40, "must_not_reveal": ["血脉秘密", "玄铁令下落", "换子"]}'


def a_session_that_wandered() -> Conversation:
    """先聊第 90 章，再回头写第 40 章。**ADR 0019「若此决策错误」那一节点名的场景。**"""
    return start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="第 90 章这里怎么收？"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="我查一下这一章不能说破什么。",
                    tool_calls=(ToolCall(id="c90", name="scene_constraints", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content=CH90_LIST, tool_call_id="c90", chapter=90),
                AgentMessage(role=Role.USER, content="先回去改第 40 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="好，我重新查第 40 章。",
                    tool_calls=(ToolCall(id="c40", name="scene_constraints", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content=CH40_LIST, tool_call_id="c40", chapter=40),
            )
        }
    )


def test_chapter_forty_never_sees_chapter_ninetys_shorter_forbidden_list() -> None:
    """**边界五的那条断言。**

    `ch40 的 must_not_reveal ⊇ ch90 的`——到第 90 章更多人已经知道了，清单更短。
    那份更短的清单留在 context 里，模型就会以为「只有这两条不能说」，
    而这个方向是 fail-open 的最坏那侧。
    """
    projected = project(a_session_that_wandered(), 40, budget_units=100_000)
    blob = str(projected.messages)
    assert CH40_LIST in blob
    assert CH90_LIST not in blob
    assert projected.off_chapter == 1

    # **反过来不成立，这个泄漏是有方向的。** 回到第 90 章时第 40 章那份**留着**：
    # 它是超集（`ch40 ⊇ ch90`），留着最多让模型多禁一条，作者看得见；丢掉它才是
    # fail-open。这条判据从 `!=` 收成 `>` 的完整论证 + 那个曾经的红
    # 在 `tests/test_agent_loop_projection.py` 的第二节。
    back = project(a_session_that_wandered(), 90, budget_units=100_000)
    assert CH90_LIST in str(back.messages) and CH40_LIST in str(back.messages)
    assert back.off_chapter == 0


def test_dropping_a_tool_result_drops_its_call_so_the_wire_stays_valid() -> None:
    """**一条带 `tool_calls` 的 assistant 消息必须被同样多条 `tool` 消息接住**，
    少一条就是 400。所以按章号丢返回时，那个 `tool_call` 得跟着走。"""
    projected = project(a_session_that_wandered(), 40, budget_units=100_000)
    asked = {
        call["id"]
        for message in projected.messages
        for call in message.get("tool_calls", ())
    }
    answered = {
        message["tool_call_id"] for message in projected.messages if message["role"] == "tool"
    }
    assert asked == answered
    assert "c90" not in asked


def test_the_authors_words_survive_the_chapter_filter() -> None:
    """作者说的话和 agent 的推理**不因为章号被丢掉**：它们是意图，不是绑章号的事实。

    ADR 0019 边界二把「模型的推理被陈旧认知污染」列成**接受**的残余代价——
    保的是起草 prompt（按章号参数化的投影），不是聊天。这条断言在描述那个裁定，
    不是在放松它。
    """
    projected = project(a_session_that_wandered(), 40, budget_units=100_000)
    blob = str(projected.messages)
    assert "第 90 章这里怎么收？" in blob
    assert "我查一下这一章不能说破什么。" in blob


def test_a_chapter_free_projection_filters_nothing_and_that_is_the_unwired_default() -> None:
    """`working_chapter` 没接上时按任何一章去筛都是替作者猜 —— 所以不筛，**并且说得出来**。"""
    projected = project(a_session_that_wandered(), None, budget_units=100_000)
    assert projected.off_chapter == 0
    assert CH90_LIST in str(projected.messages) and CH40_LIST in str(projected.messages)


def test_the_working_chapter_is_where_the_projection_coordinate_comes_from() -> None:
    """**`working_chapter` 的第一个持有者就是 loop**（3.2 留的接线口，那时哪儿都不来）。"""
    model = ScriptedModel(script=[say("好")])
    run_turn(
        a_session_that_wandered(),
        context=a_context(working_chapter=40),
        model=model,
        ledger=Ledger(),
    )
    sent = str(model.calls[0])
    assert CH40_LIST in sent and CH90_LIST not in sent


def test_the_working_chapter_cannot_change_inside_one_turn() -> None:
    """一轮之内不变**是类型不是纪律**：`ToolContext` 是 frozen dataclass。

    中途变的话，同一轮里前后半截的工具返回绑的是两个章号，而投影按章号筛——
    筛出来的东西就没有一个自洽的读法了。跨轮要换章：造一个新的 `ToolContext` 再跑一轮。
    """
    import dataclasses

    context = a_context(working_chapter=40)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.working_chapter = 90  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# 五、剪枝顺序写死（边界五后半）
# ══════════════════════════════════════════════════════════════════════════


def a_long_session() -> Conversation:
    return start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 7 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="我先看看目录。",
                    tool_calls=(ToolCall(id="a", name="book_index", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content="目" * 400, tool_call_id="a"),
                AgentMessage(role=Role.ASSISTANT, content="想" * 200),
                AgentMessage(role=Role.USER, content="接着写"),
            )
        }
    )


def _roles(projection: Projection) -> list[str]:
    return [message["role"] for message in projection.messages]


def room(conversation: Conversation, units: int) -> int:
    """留给**历史**这么多字的那个 `budget_units`。

    2026-08-12（ADR 0023「前置」）起 `budget_units` 管的是**整份 payload**——工具声明
    那几千字以前一个字都不在账上（相对默认预算约 23% 的系统性低估，方向偏松）。

    **地板是量出来的，不是抄来的**：把同一段会话的历史清空再投影一次，得到的就是
    「工具声明 + 稳定前缀 + JSON 信封」那一块。这样写的理由是它在这份文件里已经栽过一次：
    第一版只减了工具声明，于是**别人往 `AGENT_SYSTEM_PROMPT` 里加两行**（3.6 的候选稿
    那两句）就把这几条断言弄红了——而那两行和剪枝顺序一点关系都没有。
    """
    bare = conversation.model_copy(update={"messages": ()})
    return project(bare, None, budget_units=10**9).payload_units + units


def test_the_prune_order_is_frozen_tool_results_first() -> None:
    """老 `tool_result` → 老 `tool_call` → agent 中间推理 → **最后才碰作者说的话**。

    第一档在这个项目里零损失且更优：**丢掉的工具返回重查一次就有，
    而且查回来的是当前的。**
    """
    whole = project(a_long_session(), None, budget_units=100_000)
    assert (whole.stubbed_results, whole.dropped_calls, whole.dropped_reasoning) == (0, 0, 0)

    tight = project(a_long_session(), None, budget_units=room(a_long_session(), 700))
    assert tight.stubbed_results == 1 and tight.dropped_calls == 0
    assert "重新查一次" in str(tight.messages)
    assert tight.dropped_reasoning == 0
    assert "tool" in _roles(tight), "第一档删的是内容、留的是壳（wire 上它必须接住那个调用）"

    tighter = project(a_long_session(), None, budget_units=room(a_long_session(), 500))
    assert tighter.dropped_calls == 1
    assert "tool" not in _roles(tighter), "壳和它的调用是成对拿掉的"
    assert "想" * 200 in str(tighter.messages), "推理排在第三档，不许被前两档顺手带走"

    tightest = project(a_long_session(), None, budget_units=room(a_long_session(), 200))
    # 两条：agent 的那段推理，加上第二档剪完剩下的那句「我先看看目录。」——
    # 调用被拿掉之后它就是一条纯粹的中间推理，排在同一档。
    assert tightest.dropped_reasoning == 2
    # 末尾那条 system 是「已收起的结果」清单（2026-08-15 设计）：第一档剪过就会留一行。
    assert _roles(tightest) == ["system", "user", "user", "system"]
    assert not tightest.over_budget


def test_the_stable_prefix_is_never_pruned_and_never_carries_a_chapter() -> None:
    """稳定前缀（边界六）在这一层是**构造不出反例**，不是一句自觉。"""
    assert _roles(project(a_long_session(), None, budget_units=1))[0] == "system"

    with pytest.raises(ValueError):
        Conversation(prefix=(AgentMessage(role=Role.SYSTEM, content="x", chapter=40),))
    with pytest.raises(ValueError):
        Conversation(prefix=(AgentMessage(role=Role.USER, content="x"),))


def test_the_stable_prefix_holds_no_forbidden_list() -> None:
    """一份被缓存住的禁说清单 = 一条被钉死在 context 里的过期约束（边界六）。"""
    prefix = "\n".join(m.content for m in start_conversation("我的文风").prefix)
    assert "我的文风" in prefix and AGENT_SYSTEM_PROMPT in prefix
    for word in ("must_not_reveal", "不得说破", "禁说", "第 40 章", "血脉秘密"):
        assert word not in prefix


def loop_code() -> str:
    """`loop.py` 里**真的会执行的那些字**：注释和 docstring 全部剥掉。

    **必须剥**（同 `test_wording_guard._without_comments` 的理由）：下面两条守卫扫的是
    「代码里写了什么」，而解释这两条守卫的 docstring 本身就带着它们要拦的那些词
    （「`book_index` 也不该把它的账清零」）——不剥，一句解释就能让守卫红，
    而假红的下场是被人把守卫删掉。
    """
    import ast
    from pathlib import Path

    import novel_harness.agent.loop as loop_module

    tree = ast.parse(Path(loop_module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                body.pop(0)
        # 属性上的「裸字符串文档」（`x: int = 1` 后面那种）在 AST 里是独立的 Expr，
        # 一并剥掉——否则模块层那些字段说明会漏进来。
        node.body = [  # type: ignore[attr-defined]
            stmt
            for stmt in body
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            )
        ]
    return ast.unparse(tree)


def test_the_code_scanner_really_strips_the_prose() -> None:
    """**守卫的自守卫**：剥错了的话下面两条要么永远绿、要么永远红。"""
    code = loop_code()
    assert "def run_turn" in code and "StopReason.DONE" in code
    # 这两句只在 docstring 里出现过。**别拿「ADR 0019」当探针**：它同时躺在一条真的
    # `raise ValueError(...)` 里，而报错的正文是代码不是文档——那样的探针在描述一个假故障。
    assert "循环归模型" not in code, "docstring 没剥干净 —— 下面两条会对着文档开火"
    assert "空转" not in code


def test_the_only_summarisation_in_this_layer_is_conversation_block_compression() -> None:
    """loop 里只允许一种压缩：对话块压缩（docs_dev 快照第五节，作者的话那一档）。

    这条原来的守卫是「只剪不压，一个字都不摘要」——2026-08-15 设计改了：装不下时把
    最旧一段作者的话压成摘要（canonical 一字不动、可按编号取回）。**边界四（总结不许
    含图谱事实）管的是章节滚动总结**——那一位喂起草 prompt，图谱事实是引擎塞进去的；
    块压缩总结的是**作者自己说过的话**，不是引擎注入的事实，错了原文还在界面里可对质。
    所以这儿仍然禁止的只有章节总结那条链（`SummaryIndex` / `build_summary_messages`）：
    它不在这层。
    """
    code = loop_code()
    for banned in ("SummaryIndex", "build_summary_messages", "RollingSummarizer"):
        assert banned not in code, f"loop 里长出了章节总结：{banned} —— 那条链不在这层"


# ══════════════════════════════════════════════════════════════════════════
# 六、resume：执行态就是「一串 message + 哪几个 tool_call 还缺 tool_result」
# ══════════════════════════════════════════════════════════════════════════


def test_resume_is_look_at_the_tail_and_rerun_what_is_missing() -> None:
    """LangGraph 的 Checkpoint 贵在图有东西要快照。**线性 loop 没有这些。**

    T1–T5 只读或纯函数，重放免费——所以补跑缺的那几个就是 resume 的全部。
    """
    crashed = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 7 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="orphan", name="book_index", arguments="{}"),),
                ),
            )
        }
    )
    assert [c.id for c in crashed.pending_calls] == ["orphan"]

    result, model, _ = a_turn(say("接着说"), conversation=crashed)
    assert result.reason is StopReason.DONE
    assert result.conversation.pending_calls == ()
    first_sent = model.calls[0]
    assert first_sent[-1]["role"] == "tool" and first_sent[-1]["tool_call_id"] == "orphan"


def test_a_turn_needs_something_to_run_on() -> None:
    with pytest.raises(ValueError):
        run_turn(
            start_conversation(),
            context=a_context(),
            model=ScriptedModel(script=[say()]),
            ledger=Ledger(),
        )
    with pytest.raises(ValueError):
        start_conversation().with_author("   ")


# ══════════════════════════════════════════════════════════════════════════
# 七、章号从哪儿来：`dispatch` 已经校验过参数，loop 不再解析第二遍
# ══════════════════════════════════════════════════════════════════════════


def test_the_outcome_carries_the_chapter_the_model_asked_about() -> None:
    """投影的过滤判据来自 `ToolOutcome.chapter`，而它由**已校验的入参**结构性地取出。

    让 loop 自己去 `json.loads(call.arguments)` 就是第二处解析点，而这个仓库刚把
    「同一件事两处解析」清掉。
    """
    context = a_context()
    hit = dispatch(ToolCall(id="x", name="chapter_text", arguments='{"chapter": 88}'), context)
    assert hit.ok is False and hit.chapter == 88, "被拒的调用也绑章号：拒绝理由里就写着章号"

    free = dispatch(ToolCall(id="y", name="book_index", arguments="{}"), context)
    assert free.chapter is None, "不收章号的工具不许被绑上一个"

    broken = dispatch(ToolCall(id="z", name="chapter_text", arguments="{"), context)
    assert broken.chapter is None, "没过校验的参数里那个数是模型随手写的"


def test_the_chapter_is_read_by_shape_not_by_a_second_tool_table() -> None:
    """判据是「入参模型上有没有一个叫 `chapter` 的整数字段」，**不是一张表**。

    表会在加工具的那天漂；结构不会。这条断言直接量那个取法，包括 `bool` 的坑
    （`bool` 是 `int` 的子类，一个叫 `chapter` 的布尔会被当成第 1 章）。
    """
    from pydantic import BaseModel

    from novel_harness.agent.tools import _asked_chapter

    class Whatever(BaseModel):
        chapter: bool = True

    assert _asked_chapter(Whatever()) is None

    tools_with_chapter = {
        spec.name
        for spec in __import__(
            "novel_harness.agent.tools", fromlist=["TOOL_TABLE"]
        ).TOOL_TABLE
        if "chapter" in spec.args.model_fields
        and spec.args.model_fields["chapter"].annotation is int
    }
    assert tools_with_chapter == {
        "scene_constraints",
        "character_state",
        "draft_chapter",
        "chapter_text",
        # 一格认知边界（2026-08-22）：它按章号绑投影**正是它最需要的那道闸**——
        # 一格认知答案越到后面越可能过期，而过期的方向是 fail-open。
        # 轨道核对（2026-08-23）：同理按章号绑投影——「第 N 章跟后面抵不抵触」
        # 的答案在正文改过之后就不作数了。
        "check_track",
        # 右栏那四栏里只有角色卡带必填章号（2026-09-12）：按第 N 章看处境和关系。
        # 事件那条收的是区间（同 `chapter_summaries`，不绑章号）；检验规则 / 通知的
        # 章号是可选的（`int | None`），传了同样绑投影——`_asked_chapter` 量的是
        # 运行时那个值，不是标注。
        "character_card",
    }


def test_the_tool_table_is_what_gets_declared_and_the_loop_writes_no_second_copy() -> None:
    """**工具表就是权限边界。** loop 把表原样发出去，它自己不认识任何一个工具名。"""
    from novel_harness.agent.tools import TOOL_NAMES

    _, model, _ = a_turn(say("好"))
    assert set(model.tool_names) == TOOL_NAMES

    code = loop_code()
    for name in TOOL_NAMES:
        assert name not in code, (
            f"loop 里出现了工具名 {name!r} —— 那就是第二份工具表，两份迟早漂。\n"
            "工具名的唯一校验点在 `dispatch`（运输层也不认识它，ADR 0019 边界一）。"
        )


def test_dispatch_is_called_bare_because_it_never_raises() -> None:
    """**`dispatch` 外面不许套 try/except**（它的 docstring 明写了理由）。

    四种失败都是 `ok=False` 的正常返回；抛出去只会让 loop 变成一串 try/except，
    而漏掉其中一个的后果是整个会话死掉。这里量的是**行为**：喂四种失败进去，
    一次异常都不许出来。
    """
    context = a_context()
    calls = [
        ToolCall(id="1", name="根本没有这个工具", arguments="{}"),
        ToolCall(id="2", name="book_index", arguments="{截断的"),
        ToolCall(id="3", name="scene_constraints", arguments='{"must_not_reveal": ["x"]}'),
        ToolCall(id="4", name="character_state", arguments='{"chapter": 1, "character": "无此人"}'),
    ]
    outcomes = [dispatch(call, context) for call in calls]
    assert [o.ok for o in outcomes] == [False, False, False, False]
    assert all(o.content for o in outcomes)


def test_a_tool_that_raises_something_unexpected_still_kills_the_turn() -> None:
    """**诚实交代这道闸的边界**：`dispatch` 只收敛它认识的那四类。

    工具实现里冒出一个 `KeyError`，loop 不接——那不是「模型该改的问法」，是 bug，
    而吞掉 bug 换来的是一次看起来正常的空回答。
    """
    import novel_harness.agent.tools as tools_module

    def explode(args: Any, context: ToolContext) -> Any:
        raise KeyError("库里少了一列")

    broken = ToolSpec(name="book_index", description="", args=tools_module.BookIndexArgs,
                      handler=explode)
    original = tools_module.TOOLS["book_index"]
    tools_module.TOOLS["book_index"] = broken
    try:
        with pytest.raises(KeyError):
            run_turn(
                start_conversation().with_author("写第 7 章"),
                context=a_context(),
                model=ScriptedModel(script=[wants(("book_index", "{}"))]),
                ledger=Ledger(),
            )
    finally:
        tools_module.TOOLS["book_index"] = original

    # 自守卫：`ToolRefused` 那一类**是**被收敛的，两者别混。
    def refuse(args: Any, context: ToolContext) -> Any:
        raise ToolRefused("换个说法")

    tools_module.TOOLS["book_index"] = ToolSpec(
        name="book_index", description="", args=tools_module.BookIndexArgs, handler=refuse
    )
    try:
        outcome = dispatch(ToolCall(id="k", name="book_index", arguments="{}"), a_context())
        assert outcome.ok is False and outcome.content == "换个说法"
    finally:
        tools_module.TOOLS["book_index"] = original


# ══════════════════════════════════════════════════════════════════════════
# 「你正在改一章旧的」那句提醒（轨道阶段 3）
#
# **判断在系统这边，调用在模型那边**：系统只把处境说给它听，去不去查它自己定。
# ══════════════════════════════════════════════════════════════════════════


def _nudges(projected: object) -> list[str]:
    from novel_harness.agent.tools import TRACK_NUDGE_HEADER

    return [
        m["content"]
        for m in projected.messages  # type: ignore[attr-defined]
        if TRACK_NUDGE_HEADER in str(m.get("content", ""))
    ]


def test_being_at_the_frontier_changes_the_projection_by_not_one_byte() -> None:
    """在最前沿写 = **一个字节都不变**。

    这是这整条路的第一条纪律（`track.py` 模块头）：作者在书的最前端写作时，
    引擎的行为必须和这套东西不存在时完全一样。它红了代表那条纪律破了——
    症状是每一次正常写作都多花几十个字去说一句不成立的话。
    """
    here = project(a_session_that_wandered(), 90, budget_units=100_000)
    same = project(a_session_that_wandered(), 90, budget_units=100_000, frontier=90)
    ahead = project(a_session_that_wandered(), 90, budget_units=100_000, frontier=80)
    assert here.messages == same.messages == ahead.messages
    assert _nudges(same) == []


def test_editing_an_old_chapter_puts_the_situation_in_front_of_the_model() -> None:
    """不是最新章 ⇒ 投影里多一句，**说得出后面还剩几章**。

    它红了代表模型再也不知道自己在改旧章——而它不知道就不会去查，那条工具就等于没接
    （轨道那份计划原话：「**不能指望模型自觉去调**」）。
    """
    projected = project(a_session_that_wandered(), 12, budget_units=100_000, frontier=20)
    said = _nudges(projected)
    assert len(said) == 1, "提醒要么没出现，要么出现了不止一次"
    assert "第 12 章" in said[0] and "8 章" in said[0], "说不出后面还剩几章 = 一句没有信息的话"


def test_the_nudge_never_names_what_the_track_holds() -> None:
    """提醒里**不许出现后面那几章的任何内容**——它只说「有几章」和「去查」。

    这是整条路的地基（`track.py` 模块头第一节）：把后面章节的内容给写第 2 章的模型看，
    等于把伏笔亲手告诉它。**这一句是系统写的、每轮重建，所以它是这条边上最容易
    被顺手加料的地方**——加一句「第 64 章他就知道了」就当场破功。
    """
    projected = project(a_session_that_wandered(), 12, budget_units=100_000, frontier=20)
    said = _nudges(projected)[0]
    assert "不会告诉你那几章写了什么" in said


def test_the_nudge_is_projection_only_and_never_lands_in_history() -> None:
    """提醒**只活在这一次投影里**，不进对话历史。

    它绑着章号，而对话是持久且累积的（ADR 0019 边界六）：存进去之后作者写到第 200 章，
    第 12 章那句「后面还有 8 章」还躺在历史里，**而它已经是假话**。
    """
    from novel_harness.agent.tools import TRACK_NUDGE_HEADER

    session = a_session_that_wandered()
    project(session, 12, budget_units=100_000, frontier=20)
    assert all(
        TRACK_NUDGE_HEADER not in m.content for m in (*session.prefix, *session.messages)
    ), "提醒落进了 canonical 历史 —— 它会在后面每一章里继续说那句已经过期的话"


# ══════════════════════════════════════════════════════════════════════════
# 作者中途说话（2026-09-12，`Mailbox`）：排队 + 在步的边界并入，不打断
# ══════════════════════════════════════════════════════════════════════════
#
# 作者的原话：「像 codex 那样，新的消息可以直接发出去，模型可以读，并且不会耽误正在
# 做的」。**这不是并发**：loop 只在两个地方看信箱——每一次模型调用之前，以及模型
# 「说完了」的那一刻。下面四条各钉一件事：进来的位置 / 说完了不算完 / 一句不丢 /
# 不传信箱逐字节不变。


def _mailbox_turn(*script: CompletionResult, mailbox: Mailbox, **kwargs: Any):
    events: list[TurnEvent] = []
    result, model, ledger = a_turn(*script, mailbox=mailbox, on_event=events.append, **kwargs)
    return result, model, events


def test_a_mid_turn_message_is_read_at_the_next_model_call_not_before() -> None:
    """信箱里的话**在下一次模型调用之前**进对话：第一步看不见它，第二步看得见。"""
    mailbox = Mailbox()
    model_calls: list[int] = []

    def on_call(_cancel: Cancellation) -> None:
        # 第一次模型调用**正在进行**时作者说了一句——它不打断这一次。
        model_calls.append(1)
        if len(model_calls) == 1:
            mailbox.put("顺便看看第 2 章")

    model = ScriptedModel(
        script=[wants(("book_index", "{}"), text="我先翻一下目录。"), say("翻完了，第 2 章也看了。")],
        on_call=on_call,
    )
    events: list[TurnEvent] = []
    result = run_turn(
        start_conversation().with_author("这章讲什么"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
        mailbox=mailbox,
        on_event=events.append,
    )
    assert result.reason is StopReason.DONE
    # 第一次调用发出去的消息里没有那句话，第二次有——而且排在第一步的工具返回之后。
    first, second = model.calls
    assert not any(m.get("content") == "顺便看看第 2 章" for m in first)
    roles = [(m.get("role"), m.get("content")) for m in second]
    at = roles.index(("user", "顺便看看第 2 章"))
    assert roles[at - 1][0] == "tool", "它排在上一步的工具返回后面，也就是模型真的读到它的位置"
    # canonical 里它是一条正常的作者消息（不绑章号），而且喊了一声 `author_said`。
    landed = [m for m in result.conversation.messages if m.role is Role.USER]
    assert [m.content for m in landed] == ["这章讲什么", "顺便看看第 2 章"]
    assert all(m.chapter is None for m in landed)
    said = [e for e in events if e.kind is TurnEventKind.AUTHOR_SAID]
    assert [e.text for e in said] == ["顺便看看第 2 章"]
    assert result.unanswered == 0, "模型在那之后回过话了"


def test_done_with_a_queued_message_keeps_going_instead_of_finishing() -> None:
    """模型「说完了」而信箱里有话：这一轮不结束，接着跑（`Mailbox` 第二条规矩）。"""
    mailbox = Mailbox()
    seen = 0

    def on_call(_cancel: Cancellation) -> None:
        nonlocal seen
        seen += 1
        if seen == 1:
            mailbox.put("再补一句")

    model = ScriptedModel(script=[say("第一句答完了。"), say("补的那句也答了。")], on_call=on_call)
    result = run_turn(
        start_conversation().with_author("问一句"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
        mailbox=mailbox,
    )
    assert result.reason is StopReason.DONE
    assert result.steps == 2, "没有信箱的话第一步就 DONE 了"
    assert result.reply == "补的那句也答了。"
    contents = [m.content for m in result.conversation.messages]
    assert contents == ["问一句", "第一句答完了。", "再补一句", "补的那句也答了。"]
    assert result.unanswered == 0


def test_nothing_the_author_said_is_lost_when_the_turn_stops_early() -> None:
    """按了停 / 到了闸：信箱里剩的话照样进对话、落库，回执数出 `unanswered`（第一条规矩）。"""
    mailbox = Mailbox()
    cancel = Cancellation()
    saved: list[Conversation] = []

    def on_call(signal: Cancellation) -> None:
        mailbox.put("这句它没来得及看")
        signal.stop()  # 模型调用进行到一半时作者按了停

    model = ScriptedModel(script=[wants(("book_index", "{}"))], on_call=on_call)
    events: list[TurnEvent] = []
    result = run_turn(
        start_conversation().with_author("问一句"),
        context=a_context(),
        model=model,
        ledger=Ledger(),
        cancel=cancel,
        mailbox=mailbox,
        persist=saved.append,
        on_event=events.append,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert [m.content for m in result.conversation.messages if m.role is Role.USER] == [
        "问一句",
        "这句它没来得及看",
    ]
    assert result.unanswered == 1
    assert saved and saved[-1].messages[-1].content == "这句它没来得及看", "收场那一下落了库"
    assert [e.text for e in events if e.kind is TurnEventKind.AUTHOR_SAID] == ["这句它没来得及看"]
    assert mailbox.drain() == [], "信箱清空了，没有第二份"


def test_without_a_mailbox_the_turn_is_byte_for_byte_what_it_was() -> None:
    """不传信箱 = 没有中途说话这回事：路径、事件、结果一个字都不变。"""
    plain, _, _ = a_turn(wants(("book_index", "{}"), text="先看目录。"), say("看完了。"))
    boxed, _, _ = a_turn(
        wants(("book_index", "{}"), text="先看目录。"), say("看完了。"), mailbox=Mailbox()
    )
    assert plain.model_dump() == boxed.model_dump()
    assert plain.unanswered == 0


# ══════════════════════════════════════════════════════════════════════════
# 十二、停下来之后再问一句（作者 2026-09-12：「这个停的动作也要让那个 agent 知道，
# 然后让他问他为什么要停」）
# ══════════════════════════════════════════════════════════════════════════


def _stopped_between_steps(*script: CompletionResult, **kwargs: Any):
    """作者在模型要了一个工具、还没派发的那一刻按停。返回 `(结果, 模型, 账本, 事件)`。"""
    cancel = Cancellation()
    events: list[TurnEvent] = []
    presses = {"n": 0}

    def press_once(signal: Cancellation) -> None:
        presses["n"] += 1
        if presses["n"] == 1:
            signal.stop()

    model = ScriptedModel(script=list(script), on_call=press_once)
    ledger = Ledger()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        cancel=cancel,
        on_event=events.append,
        **kwargs,
    )
    return result, model, ledger, events


def test_after_the_stop_it_asks_the_author_one_question_with_no_tools() -> None:
    """停下来之后**多一次调用**：没有工具、末尾一句「作者按停了，问他」的提示，
    模型答的那一句作为普通的 assistant 消息进历史、上屏、进 `reply`，也记一笔账。"""
    result, model, ledger, events = _stopped_between_steps(
        wants(("book_index", "{}")),
        say("我停在翻目录之前了。是方向不对，还是想先改别的？"),
        debrief_on_stop=True,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert len(model.calls) == 2
    # 问的那一次：一个工具都不带，最后一条是给模型的提示（user 角色），说的是那一刻它在哪儿。
    assert model.tool_names == []
    nudge = model.calls[1][-1]
    assert nudge["role"] == "user"
    assert "按下了「停」" in nudge["content"] and "刚做完一步" in nudge["content"]
    # 答的那一句：进历史（assistant）、进回执、上屏。**提示本身不进历史。**
    last = result.conversation.messages[-1]
    assert (last.role, last.content) == (Role.ASSISTANT, "我停在翻目录之前了。是方向不对，还是想先改别的？")
    assert result.reply == last.content
    assert all("按下了「停」" not in m.content for m in result.conversation.messages)
    said = [e.text for e in events if e.kind is TurnEventKind.REPLY_TEXT]
    assert said == [last.content]
    # 两笔账：叫工具那一次 + 问的那一次；两步。
    assert len(ledger.receipts) == 2
    assert result.steps == 2
    # 没跑的那个工具照旧配壳——「知道自己被停了」靠的是历史里这几样，不是靠一句系统提示。
    assert result.conversation.pending_calls == ()


def test_the_debrief_is_off_by_default_so_stop_is_just_stop() -> None:
    """不传 `debrief_on_stop` = 停就是停：一次调用都不多，行为和以前逐字节相同。"""
    result, model, ledger, events = _stopped_between_steps(
        wants(("book_index", "{}")), say("永远到不了这儿")
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert len(model.calls) == 1
    assert len(ledger.receipts) == 1
    assert [e.kind for e in events if e.kind is TurnEventKind.REPLY_TEXT] == []


def test_tool_calls_in_the_debrief_answer_are_dropped_not_queued() -> None:
    """问的那一次说了「别叫工具」，模型硬要叫也**一条不接**：接了就是一批没人跑的调用
    挂在历史末尾，下一轮 resume 会去补跑——而作者刚说的是「停」。"""
    result, _, _, _ = _stopped_between_steps(
        wants(("book_index", "{}")),
        wants(("book_index", '{"from_chapter": 3}'), text="我再翻一下？"),
        debrief_on_stop=True,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.conversation.pending_calls == ()
    last = result.conversation.messages[-1]
    assert (last.role, last.tool_calls, last.content) == (Role.ASSISTANT, (), "我再翻一下？")


def test_pressing_stop_again_during_the_debrief_silences_it() -> None:
    """他又按了一次：那一句也不要了——信号在问的那一次里重新亮起，适配器把流掐断，
    这一轮照样是「按你的意思停下了」，只是没有那一句；掐断的那一次照样记账。"""
    from novel_harness.draft.generate import CallInterrupted

    cancel = Cancellation()
    calls: list[list[dict[str, Any]]] = []

    def model(messages: Any, *, tools: Any, cancel: Cancellation) -> CompletionResult:
        calls.append(list(messages))
        if len(calls) == 1:
            cancel.stop()
            return wants(("book_index", "{}"))
        # 问的那一次：信号已经被 loop 放下了（不然发都发不出去）；作者这时又按了一下。
        assert not cancel.stopped, "问那一句之前信号没放下，适配器会在发出去之前就掐掉它"
        cancel.stop()
        raise CallInterrupted("作者中止了这一次调用", partial_text="我停", model="deepseek-v4")

    ledger = Ledger()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        cancel=cancel,
        debrief_on_stop=True,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.reply == ""
    assert result.conversation.messages[-1].role is Role.TOOL  # 末尾是那个没跑的壳，没有半句话
    assert [r.text for r in ledger.receipts] == ["", "我停"]
    assert ledger.receipts[1].completion_tokens is None


def test_a_reply_cut_mid_sentence_keeps_the_half_sentence_and_is_billed() -> None:
    """停落在回复流的中间：说到一半的那几句是它真说过的——进历史、上屏；那一次调用
    记账但数留空；问的那一句说的是「话说到一半」。"""
    from novel_harness.draft.generate import CallInterrupted

    cancel = Cancellation()
    calls: list[list[dict[str, Any]]] = []
    events: list[TurnEvent] = []

    def model(messages: Any, *, tools: Any, cancel: Cancellation) -> CompletionResult:
        calls.append(list(messages))
        if len(calls) == 1:
            cancel.stop()
            raise CallInterrupted("作者中止了这一次调用", partial_text="好的，我先", model="deepseek-v4")
        return say("我说到一半停了。要换个方向吗？")

    ledger = Ledger()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        cancel=cancel,
        on_event=events.append,
        debrief_on_stop=True,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    roles = [(m.role, m.content) for m in result.conversation.messages[-2:]]
    assert roles == [
        (Role.ASSISTANT, "好的，我先"),
        (Role.ASSISTANT, "我说到一半停了。要换个方向吗？"),
    ]
    assert "话说到一半" in calls[1][-1]["content"]
    assert [e.text for e in events if e.kind is TurnEventKind.REPLY_TEXT] == [
        "好的，我先",
        "我说到一半停了。要换个方向吗？",
    ]
    cut = ledger.receipts[0]
    assert (cut.text, cut.model, cut.prompt_tokens, cut.finish_reason) == (
        "好的，我先",
        "deepseek-v4",
        None,
        None,
    )
    assert len(ledger.receipts) == 2


def test_a_cancelled_call_that_was_never_sent_is_not_billed() -> None:
    """信号在发之前就亮了（`sent=False`）：一分钱没花，**不许为它记一行账**。"""
    from novel_harness.draft.generate import CallInterrupted

    cancel = Cancellation()

    def model(messages: Any, *, tools: Any, cancel: Cancellation) -> CompletionResult:
        cancel.stop()
        raise CallInterrupted("作者中止了这一次调用", sent=False, model="deepseek-v4")

    ledger = Ledger()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        cancel=cancel,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert ledger.receipts == []
