"""一轮内的编号取回（设计：docs_dev/2026-08-15-写作助手一轮内工具结果编号取回设计.md）。

剪枝把老工具结果换壳之后，模型不能再看见原文；这条链让它可以按编号取回：
`project()` 在投影末尾渲染「已收起的结果」清单，`get_result(id)` 从这一轮内存里
按编号取回原文，且取回前先做一次确定性预检——把可再生的全剪掉也装不下时，
在派发这一步就拒绝，而不是让内容白回来一趟（钱花了、从没被读过）。

与 `test_context_policy.py` 的分工：那边验的是「预算、剪枝、REPEATED_CALL」的既有
行为，这份验的是**新长出来的取回链**，包括它的两条边界（一轮即焚 / 装不下就拒）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    Role,
    StopReason,
    project,
    run_turn,
    start_conversation,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import ToolCall, dispatch, tool_declarations
from novel_harness.draft.provider import CompletionResult


# ══════════════════════════════════════════════════════════════════════════
# 器材（同 test_context_policy / test_agent_loop_projection 的形状）
# ══════════════════════════════════════════════════════════════════════════


class FakeStore:
    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {"store": FakeStore(), "project_id": "project:stored"}
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


@dataclass
class ScriptedModel:
    """按剧本回答；剧本用完就重复最后一条。记下每一次真正发出去的 messages。"""

    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self, messages: Any, *, tools: Any, cancel: Cancellation
    ) -> CompletionResult:
        self.calls.append(list(messages))
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


def say(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", finish_reason="stop")


def asks(name: str, arguments: str, *, call_id: str = "c") -> CompletionResult:
    return CompletionResult(
        text="",
        model="m",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def asked(call_id: str, name: str, chapter: int) -> AgentMessage:
    return AgentMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=(
            ToolCall(id=call_id, name=name, arguments=json.dumps({"chapter": chapter})),
        ),
    )


def answered(call_id: str, content: str, chapter: int | None = None) -> AgentMessage:
    return AgentMessage(
        role=Role.TOOL, content=content, tool_call_id=call_id, chapter=chapter
    )


def a_session(*messages: AgentMessage) -> Conversation:
    return start_conversation().model_copy(update={"messages": tuple(messages)})


def noop_ledger(receipt: Any) -> None:
    pass


def floor_units(conversation: Conversation) -> int:
    """这段会话「一条历史都不发」时那份 payload 有多大（工具声明 + 前缀 + 信封）。"""
    bare = conversation.model_copy(update={"messages": ()})
    return project(bare, None, budget_units=10**9, tools=tool_declarations()).payload_units


# ══════════════════════════════════════════════════════════════════════════
# 一、投影末尾的「已收起的结果」清单
# ══════════════════════════════════════════════════════════════════════════


def test_pruned_results_are_listed_in_a_registry_block_at_the_end() -> None:
    """两条老工具结果被剪成壳时，投影末尾出现清单：编号 + 工具名 + 字数。"""
    declarations = tool_declarations()
    conversation = a_session(
        said("先查目录再读正文"),
        asked("c1", "book_index", 40),
        answered("c1", "章目录：" + "目" * 900),
        asked("c2", "chapter_text", 40),
        answered("c2", "风雪落在肩上。" + "正" * 2_000, chapter=40),
    )
    floor = floor_units(conversation)

    projection = project(conversation, 40, budget_units=floor + 800, tools=declarations)

    assert projection.stubbed_results == 2
    last = projection.messages[-1]
    assert last["role"] == "system"
    assert "已收起的结果" in last["content"]
    assert "#1 book_index" in last["content"]
    assert "#2 chapter_text" in last["content"]
    assert "字" in last["content"]


def test_no_registry_block_when_nothing_was_pruned() -> None:
    """预算充足、一条都没剪时，末尾不出现清单（那只是噪音）。"""
    conversation = a_session(
        said("读一下"),
        asked("c1", "chapter_text", 40),
        answered("c1", "风雪落在肩上。", chapter=40),
    )
    projection = project(
        conversation, 40, budget_units=10**9, tools=tool_declarations()
    )
    assert all(
        not (
            message.get("role") == "system"
            and "已收起的结果" in message.get("content", "")
        )
        for message in projection.messages
    )


# ══════════════════════════════════════════════════════════════════════════
# 二、get_result：按编号取回 / 拒绝
# ══════════════════════════════════════════════════════════════════════════


def test_get_result_returns_the_stored_content_by_number() -> None:
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 2}'),
        a_context(),
        stored={1: "目录…", 2: "风雪落在肩上。"},
    )
    assert outcome.ok
    assert outcome.content == "风雪落在肩上。"
    assert outcome.stored is not None
    assert outcome.stored.id == 2


def test_get_result_refuses_an_unknown_number() -> None:
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 99}'),
        a_context(),
        stored={1: "只有一条"},
    )
    assert not outcome.ok
    assert "99" in outcome.content


def test_get_result_without_a_turn_registry_is_refused() -> None:
    """没有这一轮的结果表（CLI / 测试直接派发）时 fail-closed，不猜。"""
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 1}'),
        a_context(),
    )
    assert not outcome.ok
    assert "没有可取" in outcome.content


def test_get_result_max_units_truncates_with_a_marker() -> None:
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 1, "max_units": 3}'),
        a_context(),
        stored={1: "一二三四五"},
    )
    assert outcome.ok
    assert outcome.content == "一二三（已截断）"


# ══════════════════════════════════════════════════════════════════════════
# 三、端到端：取回链 + 取回预检
# ══════════════════════════════════════════════════════════════════════════


def test_a_fetch_that_fits_reaches_the_model_end_to_end() -> None:
    """模型要 #1，第二次调用真的看见那份内容（一轮内按号取回）。

    预算紧到第一条结果在第一份投影里就被剪成壳——所以第二次调用里那份内容
    只能来自 `get_result` 的取回，不是「原文还躺在上下文里」的假绿。
    """
    content = "风" * 2_000
    conversation = a_session(
        said("读一下第40章"),
        asked("c1", "chapter_text", 40),
        answered("c1", content, chapter=40),
    )
    floor = floor_units(conversation)
    model = ScriptedModel(
        script=[asks("get_result", '{"id": 1}', call_id="g1"), say("好。")]
    )
    run_turn(
        conversation,
        context=a_context(working_chapter=40),
        model=model,
        ledger=noop_ledger,
        budget_units=floor + 2_500,
    )
    assert content in str(model.calls[1]), (
        "取回的那份内容没有出现在模型的下一次调用里 —— 取回链断了"
    )


def test_a_fetch_that_cannot_fit_is_refused_before_the_model_sees_it() -> None:
    """临界场景：把可再生的全剪掉也装不下 #1 时，模型看到的是拒绝，不是那份内容。"""
    big = "正" * 20_000
    conversation = a_session(
        said("读一下"),
        asked("c1", "chapter_text", 40),
        answered("c1", big, chapter=40),
    )
    floor = floor_units(conversation)
    model = ScriptedModel(
        script=[asks("get_result", '{"id": 1}', call_id="g1"), say("好。")]
    )

    run_turn(
        conversation,
        context=a_context(working_chapter=40),
        model=model,
        ledger=noop_ledger,
        budget_units=floor + 2_000,
    )

    second = str(model.calls[1])
    assert "腾不出这么多位置" in second, "预检没有拒绝装不下的取回 —— 内容会白回来一趟"
    assert big not in second, "装不下的那份内容还是进了模型视野"


def test_fetching_by_id_does_not_collide_with_the_original_querys_signature() -> None:
    """反复按编号取回不算「反复查同一个查询」——那道闸按工具名+参数字节判。

    原文被预算剪掉，内容只能来自取回；三次同编号取回（≤ repeat_limit）都放行，
    第四步模型正常收尾，而不是被 REPEATED_CALL 拦下来。
    """
    content = "章" * 800
    conversation = a_session(
        said("查一下目录"),
        asked("c1", "book_index", 40),
        answered("c1", content),
    )
    floor = floor_units(conversation)
    model = ScriptedModel(
        script=[
            asks("get_result", '{"id": 1}', call_id="g1"),
            asks("get_result", '{"id": 1}', call_id="g2"),
            asks("get_result", '{"id": 1}', call_id="g3"),
            say("好了。"),
        ]
    )
    result = run_turn(
        conversation,
        context=a_context(working_chapter=40),
        model=model,
        ledger=noop_ledger,
        budget_units=floor + 2_500,
    )
    assert result.reason is not StopReason.REPEATED_CALL
    assert content in str(model.calls[3]), "第三次取回的内容没有出现在第四次调用里"
