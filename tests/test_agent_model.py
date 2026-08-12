"""`ModelPort` 的适配器 —— **3.3 说得出自己退化了，说不出适配器退没退化。这儿才说得出。**

`agent/loop.py::Cancellation` 的 docstring 把要求写死了：

> 适配器把信号带进**流式循环**：信号一亮就让迭代器抛出去，`provider.complete()` 会把它
> 收敛成 `ProviderError`，loop 看见 `stopped` 就知道那不是故障，是作者停的。

「等这一轮跑完才停」在流式长输出上等于没有打断，所以这份文件的第一条是：
**信号亮起之后，上游的流不许再被拉一片。**

第二条同样硬，方向相反：**适配器抛非 `ProviderError` 的异常会直接穿透 `run_turn`**
（那是「bug 不许被吞」的代价）。所以一个正常的网络故障漏成崩溃就是这一层的错——
而 `complete()` 的 try 罩不住**客户端构造**那一段，那一段只能由适配器自己兜。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_harness.agent.loop import (
    AGENT_CAPABILITY,
    Cancellation,
    StopReason,
    run_turn,
    start_conversation,
)
from novel_harness.agent.model import (
    AGENT_REASONING,
    AGENT_REPLY_LENGTH,
    AgentCancelled,
    ProviderModelPort,
    agent_call_plan,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.draft.capabilities import (
    ReasoningEffort,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.draft.provider import ProviderConfig, ProviderError

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"


def a_config() -> ProviderConfig:
    return ProviderConfig(model=MODEL, base_url=BASE_URL, api_key="k", temperature=None)


def a_streaming_plan() -> Any:
    """一份 `stream=True` 的 plan。

    **`stream` 不是这儿挑的**，是 `plan_call` 按冻结阈值（16k）从输出预算推出来的——
    所以这里抬的是长度档，不是一个 `stream=True` 开关。真实的对话回复档
    （`AGENT_REPLY_LENGTH`）在阈值之下，那件事本身有一条测试钉着（见文件末尾）。
    """
    long_reply = LengthSpec(
        language=DraftLanguage.ZH, min_units=1, target_units=8_000, max_units=9_000
    )
    plan = plan_call(long_reply, ReasoningEffort.OFF, resolve_capabilities(BASE_URL, MODEL))
    assert plan.stream is True
    return plan


class _Stream:
    """一条会记账的流：**拉了几片、有没有被关掉**。"""

    def __init__(self, pieces: int, *, on_chunk: Any = None) -> None:
        self.pieces = pieces
        self.pulled = 0
        self.closed = False
        self._on_chunk = on_chunk

    def __iter__(self) -> Any:
        for index in range(self.pieces):
            self.pulled += 1
            if self._on_chunk is not None:
                self._on_chunk(index)
            yield SimpleNamespace(
                model=MODEL,
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=f"第{index}段", tool_calls=None),
                        finish_reason=None,
                    )
                ],
            )

    def close(self) -> None:
        self.closed = True


class _Client:
    """OpenAI 兼容客户端的替身。`create` 收到的 kwargs 原样留下来给断言看。"""

    def __init__(self, response: Any, *, boom: BaseException | None = None) -> None:
        self.response = response
        self.boom = boom
        self.kwargs: dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.boom is not None:
            raise self.boom
        return self.response


def a_non_stream_response(text: str = "写完了。") -> Any:
    return SimpleNamespace(
        model=MODEL,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None),
                finish_reason="stop",
            )
        ],
    )


class FakeStore:
    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context() -> ToolContext:
    return ToolContext(store=FakeStore(), project_id="project:x", working_chapter=7)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════
# 一、打断真的进到了流里
# ══════════════════════════════════════════════════════════════════════════


def test_pressing_stop_halfway_through_a_stream_stops_pulling_it() -> None:
    """**这是这份文件的主张。** 第 3 片之后按停，第 4 片不许再被拉出来。

    `pulled == 3` 而不是 `== 10`：差别就是「打断」和「等这一轮跑完」。
    """
    cancel = Cancellation()
    stream = _Stream(10, on_chunk=lambda i: cancel.stop() if i == 2 else None)
    port = ProviderModelPort(a_config(), a_streaming_plan(), client=_Client(stream))

    with pytest.raises(AgentCancelled):
        port([{"role": "user", "content": "写"}], tools=[], cancel=cancel)

    assert stream.pulled == 3, f"信号亮了之后还在拉流：拉了 {stream.pulled} 片"
    assert stream.closed, "打断之后没关掉上游 —— 服务端会继续生成，而钱按生成算"


def test_the_interruption_is_a_provider_error_so_the_loop_reads_it_as_the_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """打断抛出去的东西必须是 `ProviderError`，否则它会**穿透** `run_turn` 变成崩溃。

    然后 loop 那一侧的判据（先问信号再判故障）把它读成 `author_stopped`，
    而不是「联系不上写作模型」——**说错原因比不说更坏**：作者会去改设置。
    """
    assert issubclass(AgentCancelled, ProviderError)

    cancel = Cancellation()
    stream = _Stream(10, on_chunk=lambda i: cancel.stop() if i == 1 else None)
    port = ProviderModelPort(a_config(), a_streaming_plan(), client=_Client(stream))
    receipts: list[Any] = []
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=port,
        ledger=receipts.append,
        cancel=cancel,
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert result.said_to_author.startswith("按你的意思停下了")
    # 打断的那一次**没有账**：没有 `CompletionResult` 就没有 token 数，这一层不许编
    # （板子上「已知限制」里记着的那个漏账口，方向是偏低）。
    assert receipts == []


def test_a_signal_already_up_never_opens_a_connection() -> None:
    """作者在这一轮开始前就按了停：**一个字节都不该发出去。**"""
    cancel = Cancellation()
    cancel.stop()
    client = _Client(a_non_stream_response())
    port = ProviderModelPort(a_config(), a_streaming_plan(), client=client)
    with pytest.raises(AgentCancelled):
        port([{"role": "user", "content": "写"}], tools=[], cancel=cancel)
    assert client.kwargs == {}, "信号亮着还是把请求发出去了"


def test_a_non_streaming_call_that_already_came_back_is_not_thrown_away() -> None:
    """非流式那一档：响应已经回来了、钱已经花掉了，**这时抛掉等于把一次付过费的调用
    从账上抹掉**。loop 的下一次检查照样会停。"""
    cancel = Cancellation()
    long_reply = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=10, max_units=20)
    plan = plan_call(long_reply, ReasoningEffort.OFF, resolve_capabilities(BASE_URL, MODEL))
    assert plan.stream is False
    port = ProviderModelPort(a_config(), plan, client=_Client(a_non_stream_response()))
    result = port([{"role": "user", "content": "写"}], tools=[], cancel=cancel)
    cancel.stop()
    assert result.text == "写完了。"
    assert result.prompt_tokens == 10


# ══════════════════════════════════════════════════════════════════════════
# 二、适配器不许把正常故障漏成崩溃
# ══════════════════════════════════════════════════════════════════════════


def test_a_network_failure_arrives_as_a_provider_error_not_as_a_crash() -> None:
    """端点 500 / 连不上 / 鉴权失败 —— `complete()` 会收敛它，这里只是确认没有旁路。"""
    port = ProviderModelPort(
        a_config(), a_streaming_plan(), client=_Client(None, boom=OSError("连不上"))
    )
    with pytest.raises(ProviderError):
        port([{"role": "user", "content": "写"}], tools=[], cancel=Cancellation())


def test_a_client_that_cannot_even_be_built_is_still_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**`complete()` 的 try 罩不住客户端构造那一段**（它在函数外面），所以那一段只能
    由适配器自己兜。漏出去的形态是：作者点一下，界面上是一个栈。"""
    import novel_harness.draft.provider as provider_mod

    monkeypatch.setattr(
        provider_mod, "_build_client", lambda config: (_ for _ in ()).throw(TypeError("坏了"))
    )
    port = ProviderModelPort(a_config(), a_streaming_plan())
    with pytest.raises(ProviderError):
        port([{"role": "user", "content": "写"}], tools=[], cancel=Cancellation())


def test_the_loop_turns_that_into_one_sentence_the_author_can_act_on() -> None:
    """端到端：故障 → `model_unreachable` → 一句中文，**而不是一个穿透出来的异常**。"""
    port = ProviderModelPort(
        a_config(), a_streaming_plan(), client=_Client(None, boom=OSError("连不上"))
    )
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=port,
        ledger=lambda receipt: None,
    )
    assert result.reason is StopReason.MODEL_UNREACHABLE
    assert "联系不上写作模型" in result.said_to_author
    # 维护者那条诊断（带端点地址和模型名）在 `maintainer_note` 里，**不在给作者的那句话里**。
    assert BASE_URL not in result.said_to_author
    assert BASE_URL in result.maintainer_note


# ══════════════════════════════════════════════════════════════════════════
# 三、这一档发出去的东西
# ══════════════════════════════════════════════════════════════════════════


def test_the_tools_the_loop_hands_over_are_the_tools_that_go_on_the_wire() -> None:
    """工具声明由 loop 给（表就是权限边界），适配器**原样转交，不加不减不改**。"""
    declarations = [
        {"type": "function", "function": {"name": "book_index", "parameters": {}}},
    ]
    client = _Client(a_non_stream_response())
    long_reply = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=10, max_units=20)
    plan = plan_call(long_reply, ReasoningEffort.OFF, resolve_capabilities(BASE_URL, MODEL))
    port = ProviderModelPort(a_config(), plan, client=client)
    port([{"role": "user", "content": "查"}], tools=declarations, cancel=Cancellation())
    assert client.kwargs["tools"] == declarations
    # **不指定 `tool_choice`**：指定「必须调工具」会让「它想说话了」这个正常结局
    # 变成一次失败的请求，而那正是 loop 的退出条件。
    assert "tool_choice" not in client.kwargs


def test_the_reply_budget_is_a_conversation_not_a_chapter() -> None:
    """对话回复的档**不是起草的档**。

    起草是 2000–3000 字（`DEFAULT_LENGTH_POLICY.zh_default`）；一句「好的，那两条我
    记住了」也是完整回答。照起草的档发，预算凭空大五倍，而钱是作者的。
    """
    assert AGENT_REPLY_LENGTH.min_units == 1
    assert AGENT_REPLY_LENGTH.max_units < 2_000


def test_reasoning_is_off_because_that_is_the_only_level_every_endpoint_has() -> None:
    """**能力表没登记的模型只有 `off`。**

    `/draft` 用 `high`，所以它在作者自建的端点上会当场 `CapabilityError`。写作助手
    不能是那样——作者换个本地端点，它就整个不能用了。
    """
    assert AGENT_REASONING is ReasoningEffort.OFF
    unknown = resolve_capabilities("http://localhost:11434/v1", "some-local-model")
    assert unknown.reasoning_levels == frozenset({ReasoningEffort.OFF})
    config = ProviderConfig(
        model="some-local-model", base_url="http://localhost:11434/v1", api_key="x"
    )
    _, plan = agent_call_plan(config)  # 不抛 —— 未登记的端点也跑得起来
    assert plan.reasoning_effective is ReasoningEffort.OFF


def test_todays_reply_budget_does_not_reach_the_streaming_threshold() -> None:
    """**一处诚实交代，钉成断言。**

    `stream` 由 `plan_call` 按冻结阈值从输出预算推出来，而对话回复的预算在阈值之下——
    所以**今天这条路多半不是流式的**，上面第一节量的那个打断粒度在生产里到不了，
    降级成「这一次调用跑完就停」。

    这条测试的用处是：哪天有人把回复预算抬过阈值（或者把阈值调下来），它会红，
    而那时该被重新想一遍的是「未登记端点会不会因为 `supports_streaming is None`
    被 fail-closed 拒掉」——那才是抬预算真正的代价。
    """
    _, plan = agent_call_plan(a_config())
    assert plan.stream is False


def test_the_bill_says_this_money_was_spent_by_the_writing_assistant() -> None:
    """账上那一列的取值是 `agent`，而日志页认得它（`activity._CAPABILITY_LABEL`）。"""
    from novel_harness.activity import _CAPABILITY_LABEL

    assert _CAPABILITY_LABEL[AGENT_CAPABILITY] == "写作助手"
