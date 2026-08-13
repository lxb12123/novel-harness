"""运输层的工具调用支持 —— 模式二 agent loop 的唯一前置。

**零网络请求**,同 `test_draft_provider.py`:注入假客户端,只断言发出去和带回来的形状。

这个文件守三件事,按「坏掉的代价」排序:

1. **不传 `tools` 时发出去的东西一字不变。** M2 判分链和产品起草走的就是那条路,
   `EVAL_PROTOCOL.md` §2 要求 gate 测的必须是产品会发的东西。
2. **`arguments` 原样带回,不解析。** 运输层一 `json.loads`,「解析失败算什么」
   就被它替编排层决定了。
3. **流式按 index 累积。** `arguments` 是碎片,覆盖而不是拼接会静默丢参数——
   而丢掉的那半截参数会让工具带着一个看起来合法的残缺输入去跑。
"""

from __future__ import annotations

import types

import pytest

from novel_harness.draft.capabilities import (
    STREAM_THRESHOLD_TOKENS,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
)
from novel_harness.draft.length import LengthSpec
from novel_harness.draft.provider import (
    ProviderConfig,
    ProviderError,
    ToolCall,
    _wire_kwargs,
    complete,
)

LOCAL = "http://localhost:11434/v1"
TEST_LENGTH = LengthSpec(language="zh", min_units=100, target_units=200, max_units=300)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "chapter_constraints",
            "description": "这一章不能说破什么",
            "parameters": {
                "type": "object",
                "properties": {"chapter": {"type": "integer"}},
                "required": ["chapter"],
            },
        },
    }
]


def _plan(*, stream: bool = False, supports_stream_usage: bool | None = None) -> ResolvedCallPlan:
    """**`stream` 不是旋钮。** 它由输出预算导出(`stream == budget > STREAM_THRESHOLD_TOKENS`),
    `model_copy(update={"stream": True})` 会被 `_validate_call_plan` 的严格重建当场打回。
    所以这里靠给足预算把它逼成 True,不是直接设。
    """
    capability = ProviderCapabilities(
        base_url=LOCAL,
        model="test-model",
        source="operator-test",
        source_urls=("https://example.test/capability",),
        max_context_tokens=1_000_000,
        max_output_tokens=128_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        reserve_ratio_high=None,
    )
    return plan_call(
        TEST_LENGTH,
        ReasoningEffort.OFF,
        capability,
        request_token_budget=STREAM_THRESHOLD_TOKENS * 2 if stream else None,
    )


def _cfg() -> ProviderConfig:
    return ProviderConfig(base_url=LOCAL, model="test-model")


def _tool_call(index: int, call_id: str, name: str | None, arguments: str | None):
    return types.SimpleNamespace(
        index=index,
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


def _client(response):
    seen: dict[str, dict] = {}

    def create(**kwargs):
        seen["kwargs"] = kwargs
        return response

    return (
        types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
        ),
        seen,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. 不传 tools = 什么都没变
# ══════════════════════════════════════════════════════════════════════════


def test_wire_shape_is_byte_identical_without_tools() -> None:
    """**M2 判分链的那条路。** 长出工具调用之后,它发出去的东西必须一字未动。"""
    cfg, plan, messages = _cfg(), _plan(), [{"role": "user", "content": "写一段"}]
    assert _wire_kwargs(cfg, plan, messages) == _wire_kwargs(cfg, plan, messages, None, None)
    assert "tools" not in _wire_kwargs(cfg, plan, messages)
    assert "tool_choice" not in _wire_kwargs(cfg, plan, messages)


def test_empty_tool_list_is_the_same_as_none() -> None:
    """空列表不是「一个都不许调」,是「没有工具这回事」——发个空数组有的端点会 400。"""
    cfg, plan, messages = _cfg(), _plan(), [{"role": "user", "content": "写一段"}]
    assert "tools" not in _wire_kwargs(cfg, plan, messages, [])


def test_no_tool_calls_means_the_model_wants_to_talk() -> None:
    """空 `tool_calls` 就是 agent loop 的退出条件,所以它必须是默认值而不是 None。"""
    response = types.SimpleNamespace(
        model="served-by-fake",
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content="写好了。"), finish_reason="stop"
            )
        ],
        usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )
    client, _ = _client(response)
    result = complete([{"role": "user", "content": "写"}], config=_cfg(), plan=_plan(), client=client)
    assert result.tool_calls == ()
    assert result.text == "写好了。"


# ══════════════════════════════════════════════════════════════════════════
# 2. 传了 tools
# ══════════════════════════════════════════════════════════════════════════


def test_tools_and_tool_choice_reach_the_wire() -> None:
    kwargs = _wire_kwargs(_cfg(), _plan(), [{"role": "user", "content": "查"}], TOOLS, "auto")
    assert kwargs["tools"] == TOOLS
    assert kwargs["tool_choice"] == "auto"


def test_tool_choice_without_tools_is_refused_before_the_request() -> None:
    """一个必然失败的请求。在发出去之前拦住,别让它变成要从供应商 4xx 里反推的错误。"""
    with pytest.raises(ProviderError, match="tool_choice 需要同时提供 tools"):
        _wire_kwargs(_cfg(), _plan(), [{"role": "user", "content": "查"}], None, "required")


def test_arguments_come_back_unparsed() -> None:
    """**运输层不 `json.loads`。** 参数是模型生成的,可以是残缺的、可以带幻想出来的字段;
    「解析失败算什么」是编排层的判断(重试?回一条错给模型?终止?),不是这一层的。
    """
    response = types.SimpleNamespace(
        model="served-by-fake",
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=None,
                    tool_calls=[_tool_call(0, "call_1", "chapter_constraints", '{"chapter": 89}')],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=types.SimpleNamespace(prompt_tokens=30, completion_tokens=9),
    )
    client, _ = _client(response)
    result = complete([{"role": "user", "content": "查"}], config=_cfg(), plan=_plan(), client=client)

    assert result.tool_calls == (
        ToolCall(id="call_1", name="chapter_constraints", arguments='{"chapter": 89}'),
    )
    assert result.finish_reason == "tool_calls"
    assert result.text == ""  # content 是 None 时收敛成空串,不是 None


def test_a_call_without_a_function_name_is_dropped() -> None:
    """没有函数名就没法派发。丢掉比造一个空名字安全——空名字会一路走到派发表才炸。"""
    response = types.SimpleNamespace(
        model="served-by-fake",
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=None,
                    tool_calls=[
                        _tool_call(0, "call_1", None, "{}"),
                        _tool_call(1, "call_2", "chapter_constraints", "{}"),
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=None,
    )
    client, _ = _client(response)
    result = complete([{"role": "user", "content": "查"}], config=_cfg(), plan=_plan(), client=client)
    assert [c.name for c in result.tool_calls] == ["chapter_constraints"]


def test_a_response_object_without_the_field_does_not_raise() -> None:
    """本地端点(Ollama / LM Studio)的响应对象可能根本没有 `tool_calls` 这个属性。
    「模型没要调工具」是最常见的正常情况,不该走异常路径。
    """
    response = types.SimpleNamespace(
        model="served-by-fake",
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="好"), finish_reason="stop")],
        usage=None,
    )
    client, _ = _client(response)
    assert complete([{"role": "u", "content": "x"}], config=_cfg(), plan=_plan(), client=client).tool_calls == ()


# ══════════════════════════════════════════════════════════════════════════
# 3. 流式:按 index 累积
# ══════════════════════════════════════════════════════════════════════════


def _chunk(*tool_calls, content=None, finish_reason=None, model="served-by-fake", usage=None):
    return types.SimpleNamespace(
        model=model,
        usage=usage,
        choices=[
            types.SimpleNamespace(
                delta=types.SimpleNamespace(content=content, tool_calls=list(tool_calls) or None),
                finish_reason=finish_reason,
            )
        ],
    )


def test_streamed_arguments_are_concatenated_not_overwritten() -> None:
    """**覆盖而不是拼接会静默丢参数**,而丢掉半截的参数长得像一个合法输入。"""
    chunks = [
        _chunk(_tool_call(0, "call_1", "chapter_constraints", '{"cha')),
        _chunk(_tool_call(0, "", None, 'pter": ')),
        _chunk(_tool_call(0, "", None, "89}"), finish_reason="tool_calls"),
    ]
    client, _ = _client(iter(chunks))
    result = complete(
        [{"role": "user", "content": "查"}], config=_cfg(), plan=_plan(stream=True), client=client
    )
    assert result.tool_calls == (
        ToolCall(id="call_1", name="chapter_constraints", arguments='{"chapter": 89}'),
    )


def test_streamed_calls_keep_index_order_even_when_chunks_interleave() -> None:
    """并行工具调用的 delta 会交错到达,index 也不保证从 0 连续。"""
    chunks = [
        _chunk(_tool_call(1, "call_b", "run_checks", "{")),
        _chunk(_tool_call(0, "call_a", "read_chapter", '{"n":')),
        _chunk(_tool_call(1, "", None, "}")),
        _chunk(_tool_call(0, "", None, "40}"), finish_reason="tool_calls"),
    ]
    client, _ = _client(iter(chunks))
    result = complete(
        [{"role": "user", "content": "查"}], config=_cfg(), plan=_plan(stream=True), client=client
    )
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("call_a", "read_chapter", '{"n":40}'),
        ("call_b", "run_checks", "{}"),
    ]


def test_streamed_text_and_tool_calls_coexist() -> None:
    """模型可以先说一句再调工具。两样都要带回来,别让工具调用吃掉正文。"""
    chunks = [
        _chunk(content="我先看一下第 89 章。"),
        _chunk(_tool_call(0, "call_1", "read_chapter", '{"n": 89}'), finish_reason="tool_calls"),
    ]
    client, _ = _client(iter(chunks))
    result = complete(
        [{"role": "user", "content": "查"}], config=_cfg(), plan=_plan(stream=True), client=client
    )
    assert result.text == "我先看一下第 89 章。"
    assert result.tool_calls[0].name == "read_chapter"
