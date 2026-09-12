"""agent loop **失控的时候会怎样** —— 对抗性验证，不是功能验收。

`tests/test_agent_loop.py` 量的是「十一种停法各自走得通」。这一份量的是**它们被绕过去的那些走法**：
供应商把 usage 报成 0、本地端点不给 `tool_call` 的 id、作者按了停但这一轮手上还攥着活儿。

── 为什么这一层要单独挨一遍打 ────────────────────────────────────────────

一个停不下来的 loop 花的是**作者自己的钱**（BYOK：引擎不知道他签的什么单价，
`model_call.cost` 那一列至今空着）。而这个仓库对「静默地做错事」已经栽过五次
（`demo.sh` 断了四天没人发现 / `chapter_summary` 恒空而界面只写「- 暂无」/
底栏花销汇总系统性偏低 / `endpoints: []` 对改得掉的事说改不了 /
**假实现比真实现宽，于是测试绿而产品错**）。所以这份文件的每一条都长成同一个形状：

> **造一个真的会触发它的局面，断言它真的停了、说得出为什么停，而且那个「为什么」不是编的。**

── 三条判据的出处，别在这儿抄第二份 ──────────────────────────────────────

- 研发术语的形状网：`tests/test_wording_guard.py::dev_shapes`（全仓唯一那一份）。
- 工具名的唯一校验点：`agent/tools.py::dispatch`（**loop 里不许有第二份**，ADR 0019 边界一）。
- 停下来那句话的唯一措辞出处：`agent/loop.py::stop_wording`。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from test_wording_guard import dev_shapes

from novel_harness.agent import tools as tools_module
from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    ModelCallReceipt,
    Role,
    StopReason,
    TurnLimits,
    TurnResult,
    run_turn,
    start_conversation,
    stop_wording,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.draft.provider import CompletionResult, ProviderError, ToolCall

# ══════════════════════════════════════════════════════════════════════════
# 器材：一个按剧本回答的模型、一个会数数的账本、一个不碰库的成功工具
# ══════════════════════════════════════════════════════════════════════════


class FakeStore:
    """`StoryGraph` 的最小替身。这份文件里没有一条断言碰得到图。"""

    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {"store": FakeStore(), "project_id": "project:runaway"}
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


@dataclass
class ScriptedModel:
    """按剧本一句一句回答。**剧本用完就一直重复最后一条** —— 那正是「模型不肯收手」。"""

    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)
    saw_cancel: list[Cancellation] = field(default_factory=list)
    on_call: Callable[[Cancellation], None] | None = None

    def __call__(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]],
        cancel: Cancellation,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        self.saw_cancel.append(cancel)
        if self.on_call is not None:
            self.on_call(cancel)
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


@dataclass
class Ledger:
    receipts: list[ModelCallReceipt] = field(default_factory=list)

    def __call__(self, receipt: ModelCallReceipt) -> None:
        self.receipts.append(receipt)


def say(text: str = "写完了。", **kwargs: Any) -> CompletionResult:
    return CompletionResult(text=text, model="deepseek-v4", finish_reason="stop", **kwargs)


def wants(*calls: tuple[str, str], text: str = "", **kwargs: Any) -> CompletionResult:
    """模型要调工具。**id 由这儿给**，测「端点不给 id」的那几条另有专门的构造。"""
    return CompletionResult(
        text=text,
        model="deepseek-v4",
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
        **kwargs,
    )


class _Echo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool = True


@pytest.fixture
def a_tool_that_always_works() -> Any:
    """把 `book_index` 的实现换成一个不碰库、必成功、会数数的桩。

    **换的是实现不是声明**：`tool_declarations()` 仍然发真表出去，
    所以「loop 把真表原样发出去」那条守卫不会被这份 fixture 弄假。
    """
    ran: list[str] = []

    def handler(args: Any, context: ToolContext) -> BaseModel:
        ran.append(args.model_dump_json())
        return _Echo()

    original = tools_module.TOOLS["book_index"]
    tools_module.TOOLS["book_index"] = tools_module.ToolSpec(
        name="book_index",
        description=original.description,
        args=tools_module.BookIndexArgs,
        handler=handler,
    )
    try:
        yield ran
    finally:
        tools_module.TOOLS["book_index"] = original


def a_turn(
    *script: CompletionResult, **kwargs: Any
) -> tuple[TurnResult, ScriptedModel, Ledger]:
    model = kwargs.pop("model", None) or ScriptedModel(script=list(script))
    ledger = Ledger()
    conversation = kwargs.pop("conversation", None) or start_conversation().with_author(
        "写第 7 章"
    )
    context = kwargs.pop("context", None) or a_context()
    result = run_turn(conversation, context=context, model=model, ledger=ledger, **kwargs)
    return result, model, ledger


def a_sane_stop(result: TurnResult, model: ScriptedModel, ceiling: int) -> None:
    """每一种停法都要过的三关：**真的停了 / 说得出为什么 / 那句话不是说给维护者听的**。"""
    assert len(model.calls) <= ceiling, (
        f"停下来之后还在调模型：调了 {len(model.calls)} 次，上限是 {ceiling}"
    )
    # `<=` 而不是 `==`：抛了 `ProviderError` 的那一次**不计步、也不记账**（没有
    # `CompletionResult` 就没有 token 数，这一层不许编）。发出去的那半次仍然花了钱，
    # 那是板子上「已知限制」里记着的漏账口，不是这条断言要拦的东西。
    assert result.steps <= len(model.calls)
    assert result.said_to_author == stop_wording(result.reason)
    assert result.said_to_author.strip(), f"{result.reason} 停了却说不出为什么"
    assert not dev_shapes(result.said_to_author), (
        f"{result.reason} 那句话里有研发术语：{dev_shapes(result.said_to_author)}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、五种停法：造一个真的会触发它的局面
# ══════════════════════════════════════════════════════════════════════════


def test_a_model_that_never_puts_the_tools_down_is_capped_by_steps(
    a_tool_that_always_works: list[str],
) -> None:
    """**步数**：工具每次都成功、参数每次都不同 —— 打转和卡住两个闸都不响，只剩步数。"""
    script = [wants(("book_index", json.dumps({"from_chapter": n}))) for n in range(1, 40)]
    result, model, ledger = a_turn(*script, limits=TurnLimits(max_steps=5))
    assert result.reason is StopReason.STEP_LIMIT
    a_sane_stop(result, model, ceiling=5)
    assert len(a_tool_that_always_works) == 5, "一步一次工具，多跑一次就是多花一次钱"
    assert len(ledger.receipts) == 5


def test_the_cost_gate_stops_a_turn_the_step_gate_would_have_let_run(
    a_tool_that_always_works: list[str],
) -> None:
    """**花费**：步数还早得很，额度先到顶。两个闸各管各的，不许互相顶替。"""
    script = [
        wants(("book_index", json.dumps({"from_chapter": n})), prompt_tokens=4_000,
              completion_tokens=1_000)
        for n in range(1, 40)
    ]
    result, model, _ = a_turn(*script, limits=TurnLimits(max_steps=30, max_tokens=12_000))
    assert result.reason is StopReason.COST_LIMIT
    a_sane_stop(result, model, ceiling=30)
    assert result.steps == 3, "5,000/次 × 3 次才越过 12,000"
    assert result.tokens_charged >= 12_000


def test_pressing_stop_ends_the_turn_at_the_next_thing_the_code_controls() -> None:
    """**打断**：信号亮着的时候，代码手上的每一个决策点都要看见它。"""
    cancel = Cancellation()
    cancel.stop()
    result, model, ledger = a_turn(say("不该到这儿"), cancel=cancel)
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert model.calls == [], "按了停就不许再发出去一次调用"
    assert ledger.receipts == []
    a_sane_stop(result, model, ceiling=0)


def test_going_round_in_circles_stops_even_when_every_call_succeeds(
    a_tool_that_always_works: list[str],
) -> None:
    """**无进展**：工具次次成功，但问的是同一件事 —— 成功不等于有进展。"""
    result, model, _ = a_turn(
        wants(("book_index", "{}")),
        limits=TurnLimits(max_steps=50, repeat_limit=3),
    )
    assert result.reason is StopReason.REPEATED_CALL
    a_sane_stop(result, model, ceiling=4)


def test_a_tool_that_keeps_refusing_stops_the_turn() -> None:
    """**反复失败**：`dispatch` 把失败做成正常返回，所以「连着失败」得由这一层数。"""
    result, model, _ = a_turn(
        wants(("character_state", '{"chapter": 1, "character": "查无此人"}')),
        limits=TurnLimits(max_steps=50, tool_failure_limit=3),
    )
    assert result.reason is StopReason.TOOL_STUCK
    a_sane_stop(result, model, ceiling=3)


def test_no_runaway_shape_gets_past_the_ceiling(
    a_tool_that_always_works: list[str],
) -> None:
    """**兜底**：不管模型怎么发疯，一轮里的模型调用次数都有上界。

    这一条是上面五条的合取：任何一条闸门被绕过去，这儿会以「调了比上限还多次」的形态红。
    """
    shapes: dict[str, list[CompletionResult]] = {
        "永远叫同一个工具": [wants(("book_index", "{}"))],
        "永远换参数": [wants(("book_index", json.dumps({"from_chapter": n})))
                       for n in range(1, 99)],
        "永远瞎编工具名": [wants((f"工具{n}", "{}")) for n in range(1, 99)],
        "永远一次发五个": [wants(*[("book_index", json.dumps({"from_chapter": n * 10 + k}))
                                  for k in range(5)]) for n in range(1, 99)],
        "永远只说话不收手": [wants(("book_index", "{}"), text="我再想想")],
        "什么都不说": [say("")],
    }
    for name, script in shapes.items():
        result, model, ledger = a_turn(*script, limits=TurnLimits(max_steps=6))
        assert result.steps == len(model.calls), f"「{name}」步数和真实调用次数对不上"
        assert len(model.calls) <= 6, f"「{name}」这种发疯方式没有上界"
        assert len(ledger.receipts) == len(model.calls), f"「{name}」漏账了"
        assert result.reason is not StopReason.DONE, f"「{name}」不该算作说完了"
        a_sane_stop(result, model, ceiling=6)


# ══════════════════════════════════════════════════════════════════════════
# 二、打断的粒度：能不能中断一次**进行中**的模型调用
# ══════════════════════════════════════════════════════════════════════════


def test_the_cancel_signal_really_reaches_the_model_call() -> None:
    """**「等这一轮跑完才停」在流式长输出上等于没有打断。**

    所以判据不是「两轮之间查了没有」，而是**信号本身有没有交到那次调用手上**：
    适配器（3.4）拿着它进流式循环，一亮就让迭代器抛出去。
    """
    cancel = Cancellation()
    _, model, _ = a_turn(say("好"), cancel=cancel)
    assert model.saw_cancel == [cancel], "取消信号没有进到模型调用里 —— 那就只剩两轮之间的检查"


def test_an_abort_in_the_middle_of_a_call_is_not_reported_as_a_breakdown() -> None:
    """流式被掐断和「联系不上模型」在异常类型上**是同一个东西**，判据只能是信号。"""

    def press_stop_then_abort(signal: Cancellation) -> None:
        signal.stop()
        raise ProviderError("模型调用失败(model=deepseek-v4, base_url=https://api.deepseek.com)")

    cancel = Cancellation()
    result, model, _ = a_turn(
        model=ScriptedModel(script=[say()], on_call=press_stop_then_abort), cancel=cancel
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.maintainer_note == ""
    a_sane_stop(result, model, ceiling=1)


def test_an_adapter_that_ignores_the_signal_degrades_to_one_more_call_not_to_never() -> None:
    """适配器不理取消信号时的**退化方向**：多花一次调用，而不是停不下来。"""
    cancel = Cancellation()
    result, model, ledger = a_turn(
        wants(("book_index", "{}")),
        model=ScriptedModel(
            script=[wants(("book_index", "{}"))], on_call=lambda signal: signal.stop()
        ),
        cancel=cancel,
        limits=TurnLimits(max_steps=50),
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert len(model.calls) == 1, "退化是「这一次跑完就停」，不是「跑到步数上限」"
    assert result.tool_calls == 0
    assert len(ledger.receipts) == 1, "那一次已经花过钱了"


def test_pressing_stop_does_not_buy_one_more_tool_run_on_the_way_out(
    a_tool_that_always_works: list[str],
) -> None:
    """**按了停之后不许再干活。**

    resume（「看尾巴、补跑缺的」）在 ADR 0019 里的免费论证是「T1–T5 只读或纯函数，重放免费」。
    **但表里有一个不免费的**：`draft_chapter` 重放一次是**一次真的模型调用**，那是作者的钱，
    而且它不走这一层的 `ledger`（起草侧自己记）。所以补跑之前先问信号 —— 判据不是
    「哪个工具贵」（那是一张会漂的表），是**信号亮着就不动手**。
    """
    crashed = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 7 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=(
                        ToolCall(id="orphan", name="book_index", arguments="{}"),
                    ),
                ),
            )
        }
    )
    cancel = Cancellation()
    cancel.stop()
    result, model, _ = a_turn(say("不该到这儿"), conversation=crashed, cancel=cancel)
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert a_tool_that_always_works == [], "作者已经按了停，补跑还是把活儿干了一遍"
    assert model.calls == []
    # 停下来的会话里不许留下悬空的 `tool_call`：wire 上它必须被一条 `tool` 消息接住
    # （`pending_calls` 的 docstring：从界面上停下来的会话在那儿是空的）。
    assert result.conversation.pending_calls == ()


# ══════════════════════════════════════════════════════════════════════════
# 三、记账：跑了几次就该有几笔，而且那几个数不许是编的
# ══════════════════════════════════════════════════════════════════════════


def _every_call_is_billed(
    runner: Callable[..., TurnResult],
    script: list[CompletionResult],
    **kwargs: Any,
) -> None:
    """**这份文件的网**：模型调了几次，账上就该有几笔，且每一笔说得出自己量的是哪一次。

    它被 `test_the_billing_net_catches_a_ledger_that_only_writes_on_success` 拿一个
    假实现验过 —— 一张抓不住假实现的网等于没有网。
    """
    model = ScriptedModel(script=script)
    ledger = Ledger()
    runner(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        **kwargs,
    )
    assert len(ledger.receipts) == len(model.calls) > 0, "漏账"
    assert len({r.prompt_hash for r in ledger.receipts}) == len(ledger.receipts), (
        "几笔账共用同一个 prompt 哈希 —— 事后分不出它们量的是哪一次"
    )


def test_n_rounds_leave_exactly_n_rows_with_numbers_that_are_not_made_up(
    a_tool_that_always_works: list[str],
) -> None:
    """跑 N 轮 ⇒ 账上**恰好** N 行，且 token 数不是 0/None 冒充的。"""
    script = [
        wants(("book_index", json.dumps({"from_chapter": n})),
              prompt_tokens=900 + n, completion_tokens=60 + n)
        for n in range(1, 4)
    ] + [say("好了", prompt_tokens=1_100, completion_tokens=200)]
    result, model, ledger = a_turn(*script, limits=TurnLimits(max_steps=10))

    assert result.reason is StopReason.DONE
    assert len(model.calls) == 4 and len(ledger.receipts) == 4
    assert all(r.prompt_tokens and r.completion_tokens for r in ledger.receipts), (
        "账上出现了 0 或 None —— 那正是「日志页看起来像全部」的来法"
    )
    assert result.tokens_reported == sum(
        (r.prompt_tokens or 0) + (r.completion_tokens or 0) for r in ledger.receipts
    )
    assert result.calls_without_usage == 0


def test_the_rounds_already_paid_for_stay_on_the_bill_when_the_author_interrupts(
    a_tool_that_always_works: list[str],
) -> None:
    """**钱已经付了，不记 = 日志页少算。** 中途被打断不是把前面几轮抹掉的理由。"""
    paid = [
        wants(("book_index", json.dumps({"from_chapter": n})),
              prompt_tokens=1_000, completion_tokens=100)
        for n in (1, 2)
    ]

    def abort_the_third(signal: Cancellation) -> None:
        if len(model.calls) < 3:
            return
        signal.stop()
        raise ProviderError("stream aborted by caller")

    model = ScriptedModel(script=[*paid, say("到不了")], on_call=abort_the_third)
    ledger = Ledger()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=model,
        ledger=ledger,
        cancel=Cancellation(),
        limits=TurnLimits(max_steps=10),
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert len(model.calls) == 3
    assert len(ledger.receipts) == 2, "被打断的那一次没有 usage 可记，前两次照记"
    assert result.tokens_reported == 2_200


def test_a_provider_that_reports_half_its_usage_does_not_get_called_fully_measured() -> None:
    """**只报一半的 usage 不许冒充完整。**

    中转在流式下常见的形态是「入方向有数、出方向没有」。照「两个都是 None 才算没报」去判，
    这一次会被记成量准了的（`calls_without_usage == 0`），于是界面上那个数是低估、
    却看起来像全部 —— 底栏花销汇总那个洞就是这么来的。
    """
    for half in (
        say("好", prompt_tokens=1_200, completion_tokens=None),
        say("好", prompt_tokens=None, completion_tokens=300),
    ):
        result, _, ledger = a_turn(half)
        assert result.calls_without_usage == 1, (
            "只报了一半也是没量准 —— 不说出来的话上面那个数就在骗人"
        )
        # 账本照抄供应商报的（含 None），这一层不许替它补一个估算值。
        assert ledger.receipts[0].prompt_tokens == half.prompt_tokens
        assert ledger.receipts[0].completion_tokens == half.completion_tokens


def test_a_provider_that_reports_zero_cannot_switch_the_cost_gate_off(
    a_tool_that_always_works: list[str],
) -> None:
    """**一个能被供应商报低到失效的成本闸等于没有闸。**

    本地端点（llama.cpp / 若干中转）在流式下会给一份 `usage` 但里面全是 0。
    照 0 累加的话额度永远到不了顶，作者眼看着它一步一步烧到步数上限 ——
    而步数上限是**另一个**闸，它拦的是别的东西。
    """
    script = [
        wants(("book_index", json.dumps({"from_chapter": n})),
              prompt_tokens=0, completion_tokens=0)
        for n in range(1, 99)
    ]
    result, model, _ = a_turn(*script, limits=TurnLimits(max_steps=40, max_tokens=5_000))
    assert result.reason is StopReason.COST_LIMIT, (
        f"供应商报 0 就把成本闸关掉了：跑满 {len(model.calls)} 步才停，停的理由是 {result.reason}"
    )
    assert result.calls_without_usage == result.steps
    assert result.tokens_charged >= 5_000


def test_the_billing_net_catches_a_ledger_that_only_writes_on_success(
    a_tool_that_always_works: list[str],
) -> None:
    """**自守卫**：拿一个「只在模型说完时记账」的假实现去撞上面那张网，它必须红。

    这个仓库栽过的第五次就是**假实现比真实现宽，于是测试绿而产品错**。
    """

    def only_bills_on_success(conversation: Conversation, **kwargs: Any) -> TurnResult:
        ledger = kwargs.pop("ledger")
        seen: list[ModelCallReceipt] = []
        result = run_turn(conversation, ledger=seen.append, **kwargs)
        if result.reason is StopReason.DONE:
            for receipt in seen:
                ledger(receipt)
        return result

    runaway = [wants(("book_index", "{}"))]
    with pytest.raises(AssertionError, match="漏账"):
        _every_call_is_billed(
            only_bills_on_success, runaway, limits=TurnLimits(max_steps=3)
        )
    # 反向自守卫：真实现要过得去，否则上面那条是在描述一个假故障。
    _every_call_is_billed(run_turn, runaway, limits=TurnLimits(max_steps=3))


# ══════════════════════════════════════════════════════════════════════════
# 四、异常怎么走：不吞、不静默重试
# ══════════════════════════════════════════════════════════════════════════


def test_a_dead_endpoint_is_said_out_loud_and_not_silently_retried() -> None:
    """**静默重试最坏**：作者付了两次钱而只看见一次。"""
    attempts: list[int] = []

    def always_down(signal: Cancellation) -> None:
        attempts.append(1)
        raise ProviderError("failed to connect to https://api.deepseek.com/v1 for deepseek-v4")

    result, model, ledger = a_turn(
        model=ScriptedModel(script=[say()], on_call=always_down),
        limits=TurnLimits(max_steps=8),
    )
    assert result.reason is StopReason.MODEL_UNREACHABLE
    assert len(attempts) == 1, "端点断了就说出来，不许自己再试一次"
    assert ledger.receipts == [], "没有 CompletionResult 就没有 token 数，这一层不许编一个"
    assert dev_shapes(result.maintainer_note) and not dev_shapes(result.said_to_author)
    a_sane_stop(result, model, ceiling=1)


def test_the_loop_never_wraps_dispatch_in_a_try_except() -> None:
    """`dispatch` 的四种失败是 `ok=False` 的**正常返回**，套 try/except 就等于把那个设计撤销。

    判据是**语法结构**：`dispatch(` 在不在某个 `try` 体里。行为判据在
    `test_agent_loop.py::test_dispatch_is_called_bare_because_it_never_raises`，
    两者量的不是一件事 —— 行为那条在有人加了 `except Exception: pass` 之后仍然是绿的。
    """
    import ast
    from pathlib import Path

    import novel_harness.agent.loop as loop_module

    tree = ast.parse(Path(loop_module.__file__).read_text(encoding="utf-8"))
    guarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "dispatch"
            ):
                guarded.append(node.lineno)
    assert not guarded, (
        f"loop.py:{guarded} 把 dispatch 包进了 try —— 它的 docstring 写了为什么不许：\n"
        "抛出去只会让这个文件变成一串 try/except，而漏掉其中一个的后果是整个会话死掉。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 五、一轮多个 `tool_call`：配对、以及「缺一个 result」要表示得出来
# ══════════════════════════════════════════════════════════════════════════


def test_three_calls_in_one_round_come_back_paired_and_in_order(
    a_tool_that_always_works: list[str],
) -> None:
    """`dispatch_all` 与 `calls` 同序，而 `tool_result` 靠 `call_id` 认领 —— 两者不许错位。"""
    result, model, _ = a_turn(
        wants(
            ("book_index", '{"from_chapter": 1}'),
            ("book_index", '{"from_chapter": 2}'),
            ("book_index", '{"from_chapter": 3}'),
        ),
        say("看完了"),
        limits=TurnLimits(max_steps=4),
    )
    assert result.reason is StopReason.DONE
    assert result.tool_calls == 3
    assert [json.loads(seen)["from_chapter"] for seen in a_tool_that_always_works] == [
        1,
        2,
        3,
    ], "三个调用的执行顺序和模型给的顺序不一致"

    sent = model.calls[-1]
    asked = [call["id"] for m in sent for call in m.get("tool_calls", ())]
    answered = [m["tool_call_id"] for m in sent if m["role"] == "tool"]
    assert asked == answered, f"配对错位了：发出去 {asked}，接住的是 {answered}"


def test_stopping_halfway_through_a_batch_leaves_no_dangling_call(
    a_tool_that_always_works: list[str],
) -> None:
    """停在一批工具中间时 **wire 必须仍然合法**：每个 `tool_call` 都要被一条 `tool` 消息接住。

    三条停法都会停在一批的中间：打转、卡住、额度到顶。
    """
    batch = [("book_index", "{}")] * 4
    cases = {
        "打转": (TurnLimits(max_steps=3, repeat_limit=1), StopReason.REPEATED_CALL),
        "额度": (TurnLimits(max_steps=3, max_tokens=1), StopReason.COST_LIMIT),
    }
    for name, (limits, expected) in cases.items():
        result, _, _ = a_turn(wants(*batch), limits=limits)
        assert result.reason is expected, name
        assert result.conversation.pending_calls == (), f"「{name}」留下了悬空的调用"
        tail = result.conversation.messages
        asked = [c.id for m in tail for c in m.tool_calls]
        answered = [m.tool_call_id for m in tail if m.role is Role.TOOL]
        assert sorted(asked) == sorted(answered), f"「{name}」的壳没配齐"

    stuck, _, _ = a_turn(
        wants(*[("查无此工具", "{}")] * 4),
        limits=TurnLimits(max_steps=3, tool_failure_limit=2),
    )
    assert stuck.reason is StopReason.TOOL_STUCK
    assert stuck.conversation.pending_calls == ()


def test_a_batch_wider_than_the_gate_spends_nothing(
    a_tool_that_always_works: list[str],
) -> None:
    """**一步之内派发多少次工具曾经没有上限，而表里恰好有一个不免费的工具。**

    上一版这条断言量的是「50 个 `tool_call` 全跑完」，并在 docstring 里写着那不是批准。
    三个数放在一起就是那个洞：

    - `max_steps` 数的是**模型调用**，一步之内的工具调用不在它的口径里；
    - `max_tokens` 一步只查一次，**在这一批派发之前**，批内不再查；
    - 表里只有一个工具花钱，其余是只读或纯函数（ADR 0019：「重放免费」），
      **`draft_chapter` 不是** —— 它每次是一次真的模型调用，走的是起草侧自己的账。

    3.4 把闸放在了 loop（`TurnLimits.max_calls_per_step`），不是放在起草那条路上：
    起草是**注入**的，它自己带的闸这一层看不见也报不出来——作者会拿到一个「说完了」，
    而钱已经花掉了；而且按工具名给闸门是一张会在加工具那天漂的表。

    所以这里量的是**一个都不跑**：拦住的那一批不许有半批已经付过钱。
    """
    wide = 50
    result, model, _ = a_turn(
        wants(*[("book_index", json.dumps({"from_chapter": n})) for n in range(1, wide + 1)]),
        say("看完了"),
        limits=TurnLimits(max_steps=4, max_tokens=10**9),
    )
    assert result.reason is StopReason.BATCH_TOO_WIDE
    assert result.tool_calls == 0, "拦下来的那一批里有已经跑掉的"
    assert a_tool_that_always_works == []
    assert len(model.calls) == 1
    # 壳必须配齐：拦下来不等于把那几个 `tool_call` 变成悬空的（悬空 = 下一次 wire 400）。
    assert result.conversation.pending_calls == ()
    a_sane_stop(result, model, ceiling=4)


def test_a_batch_the_gate_allows_still_fans_out(
    a_tool_that_always_works: list[str],
) -> None:
    """闸不是「一步只准查一个」：**把索引那几层一次查了是正常行为**，不该被拦。"""
    limits = TurnLimits(max_steps=4, max_tokens=10**9)
    result, model, _ = a_turn(
        wants(
            *[
                ("book_index", json.dumps({"from_chapter": n}))
                for n in range(1, limits.max_calls_per_step + 1)
            ]
        ),
        say("看完了"),
        limits=limits,
    )
    assert result.reason is StopReason.DONE
    assert result.tool_calls == limits.max_calls_per_step
    assert len(model.calls) == 2, "步数闸只数模型调用 —— 这一批工具它一次都没数"


def test_a_batch_of_paid_tools_stops_on_money_not_only_on_count(
    a_tool_that_always_works: list[str],
) -> None:
    """**批宽度那道闸只管次数不管钱**，而 3.6 之后表里那个不免费的工具真的接线了。

    六个 `draft_chapter` 在次数上完全合法（`max_calls_per_step = 6`），
    而每一个都是一稿正文。所以钱这一半必须在**批内**收：每派发完一个查一次
    `max_tokens`，而不是等下一次模型调用之前才查——那时这一批已经全跑完了。

    量三件事：
    ① 真的在中途停了（不是六个全跑完）；
    ② 剩下那几个配了壳（悬空的 `tool_call` = 下一次 wire 400）；
    ③ 起草花掉的钱进了这一轮的 `ledger` 和汇总——不进的话上面那道闸拿什么判。
    """
    spent = ModelCallReceipt(
        capability="writer",
        schema_version="m5.draft.v1",
        model="deepseek-v4",
        prompt_hash="ph",
        prompt_bytes=b"{}",
        text="一稿正文……",
        prompt_tokens=4_000,
        completion_tokens=6_000,
    )
    drafted: list[int] = []

    def handler(args: Any, context: ToolContext) -> BaseModel:
        # **换的是实现不是声明**（同 `a_tool_that_always_works`）：这一条量的是 loop
        # 怎么对待「工具自己花了钱」，约束怎么算是 `tests/test_agent_drafting.py` 的事。
        drafted.append(args.chapter)
        # **出参里没有正文**（ADR 0022）：一整章进对话会被无状态的 wire 每一轮重发一遍。
        # 这儿给的是它的摘要行——id / 第几稿 / 字数 / 预览 / 自述。
        return tools_module.DraftResult(
            chapter=args.chapter,
            draft_id=f"draft:0000000000000000000000000{len(drafted)}",
            ordinal=len(drafted),
            units=2_400,
            preview="一稿正文……",
            note="这一版更冷。",
            calls=(spent,),
        )

    original = tools_module.TOOLS["draft_chapter"]
    tools_module.TOOLS["draft_chapter"] = tools_module.ToolSpec(
        name="draft_chapter",
        description=original.description,
        args=original.args,
        handler=handler,
    )
    try:
        result, model, ledger = a_turn(
            wants(
                *[
                    ("draft_chapter", json.dumps({"chapter": 7, "brief": "写第 7 章"}))
                    for n in range(1, 7)
                ],
                # 对话那一次也报 usage —— 不报的话闸门用估算，这条断言的算术就不确定了。
                prompt_tokens=1_000,
                completion_tokens=0,
            ),
            say("写完了"),
            # 1,000（对话那次）+ 一稿一万 —— 额度只够三稿。
            limits=TurnLimits(max_steps=4, max_tokens=25_000),
        )
    finally:
        tools_module.TOOLS["draft_chapter"] = original

    assert result.reason is StopReason.COST_LIMIT
    assert len(drafted) == 3, f"批内没查额度（跑了 {len(drafted)} 稿）"
    assert result.conversation.pending_calls == (), "剩下那几个没配壳 —— 下一次 wire 是 400"
    assert [r.capability for r in ledger.receipts].count("writer") == 3, (
        "起草花掉的钱没进 `ledger` —— 那道闸判的就是这几笔"
    )
    assert result.tokens_reported >= 30_000
    a_sane_stop(result, model, ceiling=4)


def test_money_already_spent_in_a_parallel_window_never_disappears() -> None:
    """**并发窗口里那几条是一起跑掉的，所以闸在批中间停下来时它们已经花过钱了。**

    ADR 0022 让一批稿同时跑（起草没有副作用了）。代价是那道额度闸从「每派发完一个查
    一次」松成「每个**窗口**查一次」——而松了之后有一个必须守住的东西：
    **已经跑掉的那几条要如实记上账、如实贴回对话**。给它们配一个「这一轮没跑」的壳，
    就是一次凭空消失的花销加一句骗人的话，而这一条正是这个仓库栽过的那种病
    （账上的数偏低，界面上却自称是全部）。
    """
    spent = ModelCallReceipt(
        capability="writer",
        schema_version="m5.draft.v1",
        model="deepseek-v4",
        prompt_hash="ph",
        prompt_bytes=b"{}",
        text="一稿正文……",
        prompt_tokens=4_000,
        completion_tokens=6_000,
    )
    drafted: list[int] = []

    def handler(args: Any, context: ToolContext) -> BaseModel:
        drafted.append(args.chapter)
        return tools_module.DraftResult(
            chapter=args.chapter,
            draft_id=f"draft:0000000000000000000000000{len(drafted)}",
            ordinal=len(drafted),
            units=2_400,
            preview="一稿正文……",
            calls=(spent,),
        )

    original = tools_module.TOOLS["draft_chapter"]
    tools_module.TOOLS["draft_chapter"] = tools_module.ToolSpec(
        name="draft_chapter",
        description=original.description,
        args=original.args,
        handler=handler,
        concurrent=True,
    )
    try:
        result, model, ledger = a_turn(
            wants(
                *[
                    ("draft_chapter", json.dumps({"chapter": 7, "brief": "写第 7 章"}))
                    for n in range(1, 4)
                ],
                prompt_tokens=1_000,
                completion_tokens=0,
            ),
            say("写完了"),
            # 额度只够一稿 —— 但三稿是**同时**跑的，闸拦不住这一窗。
            limits=TurnLimits(max_steps=4, max_tokens=11_000, parallel_tools=3),
        )
    finally:
        tools_module.TOOLS["draft_chapter"] = original

    assert result.reason is StopReason.COST_LIMIT
    assert len(drafted) == 3, "并发窗口不是一次跑完的 —— 那 loop 那条线程会和它们抢库"
    assert [r.capability for r in ledger.receipts].count("writer") == 3, (
        "已经跑掉的那几稿有钱没上账 —— 闸松一档可以，账漏一笔不行"
    )
    assert result.conversation.pending_calls == (), "剩下那几个没配壳 —— 下一次 wire 是 400"
    from novel_harness.agent.loop import UNRUN_CALL

    assert not [m for m in result.conversation.messages if m.content == UNRUN_CALL], (
        "跑过的那几条被当成「没跑」贴回去了 —— 那是一句骗人的话"
    )
    assert result.tool_calls == 3


def test_a_crash_between_the_model_and_the_dispatch_is_representable(
    a_tool_that_always_works: list[str],
) -> None:
    """**「哪几个 `tool_call` 还缺 `tool_result`」是 3.4 resume 的全部依据。**

    一批三个、跑完第一个就死掉 —— 剩下两个必须数得出来，而且补跑之后一个都不剩。
    """
    half_run = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 7 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=tuple(
                        ToolCall(id=f"c{k}", name="book_index", arguments=f'{{"from_chapter": {k}}}')
                        for k in (1, 2, 3)
                    ),
                ),
                AgentMessage(role=Role.TOOL, content='{"ok":true}', tool_call_id="c1"),
            )
        }
    )
    assert [c.id for c in half_run.pending_calls] == ["c2", "c3"]

    result, _, _ = a_turn(say("接着说"), conversation=half_run)
    assert result.conversation.pending_calls == ()
    assert len(a_tool_that_always_works) == 2, "补跑的是缺的那两个，不是整批重来"


def test_a_provider_that_gives_no_call_id_does_not_kill_the_conversation(
    a_tool_that_always_works: list[str],
) -> None:
    """**本地端点常常一个 `id` 都不给。**（`provider.py` 明说它不替模型编数据：
    `id=getattr(item, "id", None) or ""`，所以空 id 是会真的走到这一层的形状。）

    而这一层拿 `id` 当执行态的主键：`pending_calls` 靠它数「还缺哪几个」，投影靠它
    成对摘掉 `tool_call` 和它的返回。两个空 id 撞在一起时，这两件事都会**静默地**答错：

    - 缺的那个被当成已经答过 ⇒ resume 一个都不补 ⇒ 下一次 wire 少一条 `tool` 消息 ⇒ 400；
    - 按章号筛的时候一个连坐一片 ⇒ 刚为**当前这一章**查回来的东西也没了。
    """
    blank = CompletionResult(
        text="",
        model="deepseek-v4",
        finish_reason="tool_calls",
        tool_calls=(
            ToolCall(id="", name="book_index", arguments='{"from_chapter": 1}'),
            ToolCall(id="", name="book_index", arguments='{"from_chapter": 2}'),
        ),
    )
    result, model, _ = a_turn(blank, say("好了"), limits=TurnLimits(max_steps=4))

    tail = result.conversation.messages
    ids = [c.id for m in tail for c in m.tool_calls]
    assert all(ids) and len(set(ids)) == len(ids), (
        f"两个调用共用同一个身份：{ids} —— 「还缺哪几个」从此答不对"
    )
    answered = [m.tool_call_id for m in tail if m.role is Role.TOOL]
    assert sorted(ids) == sorted(answered)

    sent = model.calls[-1]
    wire_asked = [c["id"] for m in sent for c in m.get("tool_calls", ())]
    wire_answered = [m["tool_call_id"] for m in sent if m["role"] == "tool"]
    assert sorted(wire_asked) == sorted(wire_answered), "发出去的那一份自己就配不齐"


def test_reused_call_ids_do_not_hide_a_missing_result(
    a_tool_that_always_works: list[str],
) -> None:
    """有的端点每一轮都从 `call_1` 重新数起。**上一轮那条返回会把这一轮缺的那个盖掉。**

    盖掉的后果不是少一条上下文，是这段对话**永远发不出去**（wire 少一条 `tool` 消息 = 400），
    而它表现出来的样子是「联系不上写作模型」—— 一个指向别处的错误说法。
    """
    result, _, _ = a_turn(
        wants(("book_index", '{"from_chapter": 1}')),
        wants(("book_index", '{"from_chapter": 2}')),
        say("好了"),
        limits=TurnLimits(max_steps=5),
    )
    ids = [c.id for m in result.conversation.messages for c in m.tool_calls]
    assert len(set(ids)) == len(ids), f"两轮共用同一串 id：{ids}"


class _Page(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


@pytest.fixture
def a_chapter_reader() -> Any:
    """把 `chapter_text` 换成一个不碰磁盘的桩：返回「第 N 章的正文」。

    它**收章号**，所以 `dispatch` 会把 `ToolOutcome.chapter` 填上 —— 那正是投影的过滤判据。
    """
    original = tools_module.TOOLS["chapter_text"]
    tools_module.TOOLS["chapter_text"] = tools_module.ToolSpec(
        name="chapter_text",
        description=original.description,
        args=tools_module.ChapterTextArgs,
        handler=lambda args, context: _Page(text=f"第 {args.chapter} 章的正文"),
    )
    try:
        yield
    finally:
        tools_module.TOOLS["chapter_text"] = original


def test_one_chapters_result_is_not_dragged_out_by_another_ones(
    a_chapter_reader: None,
) -> None:
    """按章号筛（边界五）是**按调用**摘的：丢掉一条返回时它的 `tool_call` 要跟着走。

    两个调用共用同一个 id（端点不给 id 时就是这样）时它会**连坐**：为第 90 章查的那条
    被筛掉，顺手把刚为**当前这一章**查回来的也带走了。方向是 fail-closed 所以不泄漏 ——
    但模型看不见自己刚查过的东西，只会立刻再查一次，钱烧在原地，而作者看到的是它在发呆。
    """
    both = CompletionResult(
        text="",
        model="deepseek-v4",
        finish_reason="tool_calls",
        tool_calls=(
            ToolCall(id="", name="chapter_text", arguments='{"chapter": 90}'),
            ToolCall(id="", name="chapter_text", arguments='{"chapter": 40}'),
        ),
    )
    result, model, _ = a_turn(
        both,
        say("看完了"),
        context=a_context(working_chapter=40),
        limits=TurnLimits(max_steps=4),
    )
    assert result.reason is StopReason.DONE
    sent = str(model.calls[-1])
    assert "第 90 章的正文" not in sent, "第 40 章的投影里出现了第 90 章的东西（边界五）"
    assert "第 40 章的正文" in sent, "为当前这一章刚查回来的东西被连坐筛掉了"
    assert result.projection is not None and result.projection.off_chapter == 1
