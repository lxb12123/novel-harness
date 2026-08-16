"""对话块压缩（设计：docs_dev 快照第五节）。作者的话是 CONTEXT_FULL 唯一来源；
这条链让 loop 在装不下时把最旧的对话块压成摘要，原文留在 canonical，模型视野里
换成「更早的对话」摘要块，可按编号取回整块原文。

与 `test_agent_stored_results.py` 的分工：那边是**工具结果**的剪掉 + 取回（已实现）；
这份是**作者的话**的压缩 + 取回（本节实现）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_harness.agent.blocks import (
    BLOCK_SIZE,
    block_number_of,
    block_text,
    is_block_summary,
    marker_block_number,
    oldest_uncompressed_number,
    summary_message,
)
from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    Role,
    StopReason,
    payload_units,
    project,
    run_turn,
    start_conversation,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import ToolCall, dispatch, tool_declarations
from novel_harness.draft.provider import CompletionResult


# ══════════════════════════════════════════════════════════════════════════
# 器材（同 test_agent_stored_results 的形状）
# ══════════════════════════════════════════════════════════════════════════


class FakeStore:
    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {"store": FakeStore(), "project_id": "project:blocks"}
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


@dataclass
class ScriptedModel:
    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self, messages: Any, *, tools: Any, cancel: Cancellation
    ) -> CompletionResult:
        self.calls.append(list(messages))
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


def say(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", finish_reason="stop")


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def answered(call_id: str, content: str) -> AgentMessage:
    return AgentMessage(role=Role.TOOL, content=content, tool_call_id=call_id)


def reasoned(text: str) -> AgentMessage:
    return AgentMessage(role=Role.ASSISTANT, content=text)


def a_session(*messages: AgentMessage) -> Conversation:
    return start_conversation().model_copy(update={"messages": tuple(messages)})


def noop_ledger(receipt: Any) -> None:
    pass


def floor_units(conversation: Conversation) -> int:
    bare = conversation.model_copy(update={"messages": ()})
    return project(bare, None, budget_units=10**9, tools=tool_declarations()).payload_units


def author_line(index: int, fill: str = "字") -> str:
    return f"作者第 {index} 句。" + fill * 120


def a_long_session(blocks: int = 2) -> Conversation:
    """`blocks` 个满块：每个块 `BLOCK_SIZE` 句作者的话，中间夹一轮助手回答。"""
    messages: list[AgentMessage] = []
    for block in range(blocks):
        for i in range(BLOCK_SIZE):
            messages.append(said(author_line(block * BLOCK_SIZE + i + 1)))
            messages.append(reasoned(f"（助手回应第 {block * BLOCK_SIZE + i + 1} 句）"))
    return a_session(*messages)


class RecordingSummarizer:
    """假的块摘要器：记录被调用了几次，每次都返回同一句话。"""

    def __init__(self, summary: str = "作者要求别写打斗。") -> None:
        self.summary = summary
        self.calls: list[str] = []

    def __call__(self, text: str) -> CompletionResult:
        self.calls.append(text)
        return CompletionResult(text=self.summary, model="m", finish_reason="stop")


# ══════════════════════════════════════════════════════════════════════════
# 一、分块与标记
# ══════════════════════════════════════════════════════════════════════════


def test_blocks_are_author_message_groups_of_block_size() -> None:
    conversation = a_long_session(blocks=2)
    # 第 0 句作者的话在第 0 个块（显示 #1）；第 BLOCK_SIZE 句进第 1 个块（显示 #2）。
    user_indices = [i for i, m in enumerate(conversation.messages) if m.role is Role.USER]
    assert block_number_of(conversation, user_indices[0]) == 1
    assert block_number_of(conversation, user_indices[BLOCK_SIZE]) == 2
    assert block_number_of(conversation, user_indices[-1]) == 2


def test_block_text_contains_the_blocks_messages_but_not_other_blocks() -> None:
    conversation = a_long_session(blocks=2)
    text = block_text(conversation, 1)
    assert "作者第 1 句。" in text
    assert "作者第 " + str(BLOCK_SIZE) + " 句。" in text
    assert "作者第 " + str(BLOCK_SIZE + 1) + " 句。" not in text


def test_the_summary_marker_is_a_structural_system_message() -> None:
    marker = summary_message(3, "作者要求别写打斗。")
    assert marker.role is Role.SYSTEM
    assert is_block_summary(marker)
    assert marker_block_number(marker) == 3
    assert not is_block_summary(AgentMessage(role=Role.SYSTEM, content="别的东西"))


def test_oldest_uncompressed_skips_marked_blocks() -> None:
    conversation = a_long_session(blocks=2)
    assert oldest_uncompressed_number(conversation) == 1
    marked = conversation.extended(summary_message(1, "压过了。"))
    assert oldest_uncompressed_number(marked) == 2
    fully_marked = marked.extended(summary_message(2, "也压过了。"))
    assert oldest_uncompressed_number(fully_marked) is None


# ══════════════════════════════════════════════════════════════════════════
# 二、投影：压缩块换成摘要块，近期原文保留
# ══════════════════════════════════════════════════════════════════════════


def test_project_replaces_a_compressed_block_with_a_summary_block() -> None:
    conversation = a_long_session(blocks=2).extended(summary_message(1, "作者要求别写打斗。"))
    projection = project(
        conversation, None, budget_units=10**9, tools=tool_declarations()
    )

    blob = str(projection.messages)
    assert "【更早的对话】" in blob
    assert "#1 · 作者要求别写打斗。" in blob
    # 块 1 的原话没了，块 2 的原话还在
    assert "作者第 1 句。" not in blob
    assert "作者第 " + str(BLOCK_SIZE + 1) + " 句。" in blob
    # 标记消息本身不上投影（它是元数据）
    assert "已压缩对话块" not in blob


def test_compressed_blocks_keep_their_rule_messages_alive() -> None:
    """规矩不随块压缩消失：它在块里是 SYSTEM 消息，块成员只算作者/助手/工具。"""
    rule = AgentMessage(role=Role.SYSTEM, content="别写打斗")
    conversation = a_session(
        said("第一句"), rule, said("第二句")
    ).extended(summary_message(1, "压过了。"))
    projection = project(
        conversation, None, budget_units=10**9, tools=tool_declarations()
    )
    assert "别写打斗" in str(projection.messages)


def test_compression_cost_is_included_in_the_payload_exactly() -> None:
    conversation = a_long_session(blocks=2).extended(summary_message(1, "作者要求别写打斗。"))
    declarations = tool_declarations()
    projection = project(conversation, None, budget_units=10**9, tools=declarations)
    assert projection.payload_units == payload_units(projection.messages, declarations)


# ══════════════════════════════════════════════════════════════════════════
# 三、run_turn：装不下时压缩最旧块，而不是直接停
# ══════════════════════════════════════════════════════════════════════════


def test_run_turn_compresses_the_oldest_block_instead_of_stopping() -> None:
    conversation = a_long_session(blocks=2)
    floor = floor_units(conversation)
    summarizer = RecordingSummarizer()
    model = ScriptedModel(script=[say("好，接着说。")])

    result = run_turn(
        conversation,
        context=a_context(),
        model=model,
        ledger=noop_ledger,
        budget_units=floor + 1_500,
        block_summarizer=summarizer,
    )

    assert result.reason is not StopReason.CONTEXT_FULL
    assert len(summarizer.calls) >= 1, "装不下时应该真的去压缩"
    first = str(model.calls[0])
    assert "【更早的对话】" in first, "压缩后的摘要块没进模型视野"
    assert "作者第 1 句。" not in first, "最旧块的原话还在投影里"
    assert "作者第 " + str(BLOCK_SIZE + 1) + " 句。" in first, "近期原话不该被压"


def test_run_turn_without_a_summarizer_stays_context_full() -> None:
    conversation = a_long_session(blocks=2)
    floor = floor_units(conversation)
    result = run_turn(
        conversation,
        context=a_context(),
        model=ScriptedModel(script=[say("好。")]),
        ledger=noop_ledger,
        budget_units=floor + 800,
    )
    assert result.reason is StopReason.CONTEXT_FULL


def test_each_block_is_summarized_at_most_once() -> None:
    conversation = a_long_session(blocks=2)
    floor = floor_units(conversation)
    summarizer = RecordingSummarizer()
    model = ScriptedModel(script=[say("好。")])

    result = run_turn(
        conversation,
        context=a_context(),
        model=model,
        ledger=noop_ledger,
        budget_units=floor + 300,
        block_summarizer=summarizer,
    )

    assert result.reason is not StopReason.CONTEXT_FULL
    assert len(summarizer.calls) == 2, "两个块各压一次，不该重复压同一个块"


# ══════════════════════════════════════════════════════════════════════════
# 四、取回：按编号取回一整块原文
# ══════════════════════════════════════════════════════════════════════════


def test_get_result_fetches_a_whole_block_with_kind_block() -> None:
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 1, "kind": "block"}'),
        a_context(),
        blocks={1: "作者原话……"},
    )
    assert outcome.ok
    assert outcome.content == "作者原话……"


def test_get_result_default_kind_still_fetches_tool_results() -> None:
    outcome = dispatch(
        ToolCall(id="g1", name="get_result", arguments='{"id": 2}'),
        a_context(),
        stored={1: "工具结果一", 2: "工具结果二"},
    )
    assert outcome.ok
    assert outcome.content == "工具结果二"
