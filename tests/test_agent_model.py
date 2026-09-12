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

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    _CancellableClient,
    _sockets_under,
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
    assert result.said_to_author.startswith("已停止")
    # 打断的那一次**有账**（2026-09-12 起，回复走流式的那天一起补的漏账口）：
    # 请求发出去了，断开连接 ≠ 停止计费。**数一律留空**——供应商没报的数这一层不许编
    # （同 `generate.py::interrupted`），`finish_reason` 也不是一个我们自己编的
    # "cancelled"。第一片已经收到了，所以 `text` 是那半截。
    assert [r.model for r in receipts] == [MODEL]
    cut = receipts[0]
    assert (cut.prompt_tokens, cut.completion_tokens, cut.finish_reason) == (None, None, None)
    assert cut.text == "第0段第1段", "掐断前收到的那两片没进账上的正文"


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
    assert "无法连接写作模型" in result.said_to_author
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


def test_the_reply_call_is_interruptible_so_stop_lands_within_a_chunk() -> None:
    """**回复那一次调用要可中断**（作者 2026-09-12：「按停的话就是全部的工作都停下来」）。

    2026-09-12 之前这儿钉的是反面（`plan.stream is False`，「一处诚实交代」）：回复的
    输出预算在流式阈值之下，一次阻塞往返里没有位置插进去，「停」要等整份回复回来。
    改法不是抬预算——预算一个字没动（抬了 `stream` 的判据就变成「谁想要流式」）——
    是和起草那一档一样要 `interruptible`，`_streams` 的第二个理由成立。
    这条红了的那天要一起想的是：被掐断的调用还进不进账
    （`test_the_interruption_is_a_provider_error_so_the_loop_reads_it_as_the_author`）。
    """
    _, plan = agent_call_plan(a_config())
    assert plan.interruptible is True
    assert plan.stream is True
    assert plan.visible_token_budget == AGENT_REPLY_LENGTH.max_units * 2 + 1_024, "预算被抬了"


def test_the_bill_says_this_money_was_spent_by_the_writing_assistant() -> None:
    """账上那一列的取值是 `agent`，而日志页认得它。

    国际化第四批·笔二起这张表搬去了前端 `backendMessages.ts`
    的 `CAPABILITY_LABEL`，不再是 `activity._CAPABILITY_LABEL`。
    """
    from test_wording_guard import BACKEND_MESSAGES, _ts_const_object_entry

    source = BACKEND_MESSAGES.read_text(encoding="utf-8")
    assert _ts_const_object_entry(source, "CAPABILITY_LABEL", AGENT_CAPABILITY, "zh") == "写作助手"


# ══════════════════════════════════════════════════════════════════════════
# 四、信号亮了当场掐断连接（2026-09-12：Codex / Claude Code 那一路的 AbortController）
# ══════════════════════════════════════════════════════════════════════════
#
# 「每一片问一次」的盲区是两片之间：读的那条线卡在 `recv` 里，模型想得越久盲区越长。
# 这一节用一条**真的本机连接**量：对面吐了一片就卡住，另一条线按停，读的那条线多久醒。
# 实测（macOS）`close()` 叫不醒它，`socket.shutdown` 一毫秒就醒——`_cut` 走的是后者。
# 顺便钉住「今天摸得到 socket」：那条路走的是 httpx / httpcore 的私有属性，依赖升级
# 把它改了，这儿先红，而不是「停」悄悄退化成「下一片到了才停」。


class _StallingEndpoint(BaseHTTPRequestHandler):
    """一个 OpenAI 兼容的假端点：流式吐一片就卡 20 秒；非流式卡 20 秒才回。"""

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 —— http.server 的命名
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                first = {
                    "id": "x", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
                self.wfile.flush()
                time.sleep(20)
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                time.sleep(20)
                whole = {
                    "id": "x", "object": "chat.completion", "created": 0, "model": MODEL,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "late"},
                         "finish_reason": "stop"}
                    ],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(whole).encode())
        except OSError:
            pass  # 对面掐断了，正是这一节要的


@pytest.fixture
def stalling_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StallingEndpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()


def _real_openai_client(base_url: str) -> Any:
    """一个**不走代理**的真客户端：这台机器的环境里挂着 HTTP 代理，
    不关 `trust_env` 的话 127.0.0.1 会被送去代理、回一个 502。"""
    import httpx
    from openai import OpenAI

    return OpenAI(
        base_url=base_url, api_key="k", max_retries=0, http_client=httpx.Client(trust_env=False)
    )


def _in_a_thread(fn: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def run() -> None:
        started = time.perf_counter()
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 —— 测的就是抛什么
            out["error"] = exc
        out["elapsed"] = time.perf_counter() - started

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    out["thread"] = thread
    return out


def test_stop_cuts_the_socket_so_a_read_blocked_between_chunks_wakes_at_once(
    stalling_endpoint: str,
) -> None:
    cancel = Cancellation()
    real = _real_openai_client(stalling_endpoint)
    client = _CancellableClient(real, cancel)
    stream = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": "写"}], stream=True, max_tokens=5
    )
    got: list[str] = []

    def read_it() -> None:
        for chunk in stream:
            got.append(chunk.choices[0].delta.content)

    reader = _in_a_thread(read_it)
    # 第一片到手、第二片永远不来——这就是「模型在想」的那个形态。
    deadline = time.time() + 5
    while not got and time.time() < deadline:
        time.sleep(0.01)
    assert got == ["first"]
    # 摸的是连接池（`_watch` 里挂的钩子摸的是 openai `Stream` 那一头，两条路殊途同归）。
    assert _sockets_under(real), "顺着 httpx 摸不到 socket 了 —— 依赖升级改了私有属性？"

    pressed = time.perf_counter()
    cancel.stop()
    reader["thread"].join(timeout=5)
    assert not reader["thread"].is_alive(), "按了停，读的那条线还卡在 recv 里"
    woke_after = time.perf_counter() - pressed
    assert woke_after < 2, f"醒得太慢：{woke_after:.2f}s（对面要 20 秒才吐下一片）"
    error = reader.get("error")
    assert isinstance(error, AgentCancelled), f"抛的不是作者停：{error!r}"
    assert error.partial_text == "first", "掐断前到手的那一片丢了"
    assert error.sent is True and error.model == MODEL


def test_stop_cuts_a_non_streaming_call_that_is_still_waiting(stalling_endpoint: str) -> None:
    """非流式那一档（块摘要走的）：整份响应还没回来时按停，也当场醒，而且**记账**
    （请求发出去了，`sent=True`）。"""
    cancel = Cancellation()
    real = _real_openai_client(stalling_endpoint)
    client = _CancellableClient(real, cancel)
    waiting = _in_a_thread(
        lambda: client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "压缩"}], max_tokens=5
        )
    )
    deadline = time.time() + 5
    while not _sockets_under(real) and time.time() < deadline:
        time.sleep(0.01)
    assert _sockets_under(real), "顺着客户端摸不到连接池里的 socket 了"

    pressed = time.perf_counter()
    cancel.stop()
    waiting["thread"].join(timeout=5)
    assert not waiting["thread"].is_alive(), "按了停，非流式那一次还在等整份响应"
    assert time.perf_counter() - pressed < 2
    error = waiting.get("error")
    assert isinstance(error, AgentCancelled), f"抛的不是作者停：{error!r}"
    assert (error.sent, error.partial_text, error.model) == (True, "", MODEL)


def test_stop_hooks_fire_once_each_and_a_late_hook_fires_at_once() -> None:
    """`Cancellation.on_stop` 的三条：亮了就叫；亮了之后才挂的当场叫；摘掉的不叫；
    一个钩子抛了别的照叫、信号照亮。"""
    cancel = Cancellation()
    called: list[str] = []
    off_a = cancel.on_stop(lambda: called.append("a"))
    cancel.on_stop(lambda: (_ for _ in ()).throw(RuntimeError("连接早断了")))
    cancel.on_stop(lambda: called.append("b"))
    off_a()
    cancel.stop()
    assert cancel.stopped
    assert called == ["b"]
    cancel.on_stop(lambda: called.append("late"))
    assert called == ["b", "late"]


def test_a_cut_signalled_by_a_clean_eof_is_still_the_author_stopping() -> None:
    """掐断在读的那条线上可能长成「流正常结束」（对面 EOF）——信号亮着就不是正常结束，
    半截不许当完整的收：半截的 `tool_calls` 会被派发。"""
    cancel = Cancellation()

    class _EndsQuietly:
        """两片之后信号亮起、然后流「正常」结束（模拟 shutdown 之后读到 EOF）。"""

        closed = False

        def __iter__(self) -> Any:
            for index in range(2):
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
            cancel.stop()

        def close(self) -> None:
            self.closed = True

    stream = _EndsQuietly()
    port = ProviderModelPort(a_config(), a_streaming_plan(), client=_Client(stream))
    with pytest.raises(AgentCancelled) as caught:
        port([{"role": "user", "content": "写"}], tools=[], cancel=cancel)
    assert caught.value.partial_text == "第0段第1段"
    assert stream.closed
