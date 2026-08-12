"""`ModelPort` 的实现 —— **把作者按下的「停」带进一次正在进行的模型调用里**。

`loop.py` 的 `Cancellation` docstring 把要求写死了，这个文件是它的另一半：

> 适配器把信号带进**流式循环**：信号一亮就让迭代器抛出去，`provider.complete()` 会把它
> 收敛成 `ProviderError`，loop 看见 `stopped` 就知道那不是故障，是作者停的。

3.3 那一层说得出自己退化了（适配器不理信号 ⇒ 打断退化成「这一次调用跑完就停」），
**说不出适配器退没退化**——那条只能在这儿测，测在 `tests/test_agent_model.py`。

── 三件必须在这一层做对的事 ──────────────────────────────────────────────

1. **信号要进到迭代里，不是只在两次调用之间被看到。** 一次模型调用是一次阻塞往返，
   loop 自己在那儿等着；回调形式的打断只能在调用**之间**生效，那在长输出上等于没有打断。
   所以这里包的是**流本身**（`_watch`），每收一个 chunk 问一次信号。
2. **一个非 `ProviderError` 的异常都不许逃出去。** `run_turn` 有意不吞异常
   （「bug 不许被吞」），于是**适配器漏一个正常的网络故障出去，作者看到的就是崩溃**，
   而不是「联系不上写作模型，等一下再试」。`complete()` 自己会把它 try 里的一切收敛掉，
   但**客户端的构造在它外面**——那一处只能由这里兜。
3. **不传 `tools` 时的 wire shape 一个字节都不许动。** 这里从头到尾只调
   `draft.provider.complete()`，不自己拼 kwargs、不自己造客户端参数；M2 判分链走的是
   同一个函数的不传 `tools` 那条路（EVAL_PROTOCOL §2），它必须继续和长出工具调用之前
   逐字节相同。

── 一处诚实交代：**今天这条路多半不是流式的** ────────────────────────────

`stream` 不是这儿定的，是 `plan_call` 按**冻结的**阈值（`STREAM_THRESHOLD_TOKENS`，
16k）从 `request_token_budget` 推出来的。一次对话回复的输出预算远在阈值之下，所以
`plan.stream` 通常是 `False`，而非流式的一次 HTTP 往返**没有可以插进去的位置**——
打断在那种形态下确实退化成「这一次调用跑完就停」。

**不许为了让它变成流式去抬输出预算**：那样 `stream` 的判据就从「输出多大」变成了
「谁想要流式」，而抬上去之后对**能力表没登记的模型**（`supports_streaming is None`）
`plan_call` 会 fail-closed 直接拒——作者换个自建端点，写作助手整个不能用。
所以这里的实现按流式写、按流式测，真到了流式那一档就真的生效；到不了的那一档，
`loop` 的每步检查仍然在。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Final

from ..draft.capabilities import (
    ProviderCapabilities,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
    resolve_capabilities,
)
from ..draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage, LengthSpec
from ..draft.provider import CompletionResult, ProviderConfig, ProviderError, complete
from .loop import Cancellation

AGENT_REPLY_LENGTH: Final = DEFAULT_LENGTH_POLICY.validate_spec(
    LengthSpec(
        language=DraftLanguage.ZH,
        min_units=1,
        target_units=400,
        max_units=1_500,
    )
)
"""对话回复的长度档。**它在这儿只有一个用途：倒推输出预算**（`visible = max×2 + 1024`）。

`min_units=1` 是有意的：这不是起草，**一句「好的，第 40 章那两条我记住了」就是完整回答**，
而 `generate.py` 那套「不够长就续写一次」的逻辑这条路根本不走（`run_turn` 直接调
`complete`）。把它写成 2000 会让预算凭空大五倍，钱是作者的。

上限 1500 字是「一次说得完的解释」的量级。真要长文是起草工具的事，不是聊天回复。
"""

AGENT_REASONING: Final = ReasoningEffort.OFF
"""**`OFF` 是唯一对每个端点都成立的取值。**

`resolve_capabilities` 对没登记的模型给 `reasoning_levels={OFF}`，所以 `HIGH`（`/draft`
用的那档）在作者自建的端点上会当场 `CapabilityError`——写作助手整个用不了。
抽取和滚动总结（`api/deps.py` 那两处）也是 `OFF`，这里跟它们一致。
"""


class AgentCancelled(ProviderError):
    """作者在一次**进行中**的调用里按了停。

    继承 `ProviderError` 是为了穿过 `complete()` 的 `except ProviderError: raise`
    原样出来（不被重新包成一句「模型调用失败」）。而 `run_turn` 在 `except ProviderError`
    里**先问信号再判故障**，所以它落在 `AUTHOR_STOPPED` 而不是 `MODEL_UNREACHABLE`。
    """


def _watch(chunks: Any, cancel: Cancellation) -> Iterator[Any]:
    """把一条流包成「每一片都先问一次信号」的流。

    `finally` 里关掉上游：作者按了停，那条 HTTP 连接就该断掉，而不是留着让服务端
    继续把整份输出生成完（**钱是按生成的 token 算的，不是按收到的**）。
    """
    try:
        for chunk in chunks:
            if cancel.stopped:
                raise AgentCancelled("作者中止了这一次调用")
            yield chunk
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()


class _Completions:
    def __init__(self, real: Any, cancel: Cancellation) -> None:
        self._real = real
        self._cancel = cancel

    def create(self, **kwargs: Any) -> Any:
        if self._cancel.stopped:
            raise AgentCancelled("作者中止了这一次调用")
        response = self._real.chat.completions.create(**kwargs)
        if not kwargs.get("stream"):
            # 非流式：整份响应已经回来了，钱已经花掉。**这里不抛**——抛掉等于把一次
            # 已经付过费的调用从账上抹掉，而 loop 下一次检查信号照样会停。
            return response
        return _watch(response, self._cancel)


class _Chat:
    def __init__(self, real: Any, cancel: Cancellation) -> None:
        self.completions = _Completions(real, cancel)


class _CancellableClient:
    """OpenAI 兼容客户端的一层薄壳：**只包 `chat.completions.create` 的返回值。**

    包客户端而不是改 `provider.py`，理由有两条且都是硬的：`draft/provider.py` 是 M2
    判分链的运输层（冻结），而且**取消是编排层的概念**——运输层认得它，就等于运输层
    知道有个「作者」和一个「停」按钮，那和「运输层不认识工具表」是同一条边界。
    """

    def __init__(self, real: Any, cancel: Cancellation) -> None:
        self.chat = _Chat(real, cancel)


def _real_client(config: ProviderConfig) -> Any:
    """造一个真的客户端。

    借的是 `draft.provider._build_client`（私有名）。**这是有意的，不是偷懒**：那儿握着
    两条不能抄第二遍的语义——空 key 换占位符（本地端点合法地不校验 key），以及
    「永远显式传值，不让进程环境里别人家的 `OPENAI_API_KEY` 悄悄参与进来」。
    在这里重写一遍 `OpenAI(...)` 就是那两条语义的第二份拷贝，而它们漂掉的形态是
    「作者的 key 没用上，用的是环境里另一个人的」。

    构造不发网络请求，但**它会抛**（openai 库没装、base_url 不合法）——而这一处在
    `complete()` 的 try 之外，所以收敛在这儿。
    """
    from ..draft.provider import _build_client

    try:
        return _build_client(config)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：漏出去 = 作者看到崩溃
        raise ProviderError(f"模型客户端建不起来（base_url={config.base_url}）：{exc}") from exc


class ProviderModelPort:
    """`loop.ModelPort` 的实现。**`tools` 和 `cancel` 由 loop 给，这里不替换。**"""

    def __init__(
        self,
        config: ProviderConfig,
        plan: ResolvedCallPlan,
        *,
        client: Any = None,
    ) -> None:
        self._config = config
        self._plan = plan
        self._client = client

    def __call__(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]],
        cancel: Cancellation,
    ) -> CompletionResult:
        client = self._client if self._client is not None else _real_client(self._config)
        return complete(
            messages,
            config=self._config,
            plan=self._plan,
            client=_CancellableClient(client, cancel),
            tools=tools,
        )


def agent_call_plan(config: ProviderConfig) -> tuple[ProviderCapabilities, ResolvedCallPlan]:
    """这一轮对话按哪份能力证据、多大预算发。

    Raises:
        draft.capabilities.CapabilityError: 这个端点/模型撑不起这一档（调用方映成 422，
            并告诉作者去顶栏「AI 设置」看一眼）。
    """
    capability = resolve_capabilities(config.base_url, config.model)
    return capability, plan_call(AGENT_REPLY_LENGTH, AGENT_REASONING, capability)


__all__ = [
    "AGENT_REASONING",
    "AGENT_REPLY_LENGTH",
    "AgentCancelled",
    "ProviderModelPort",
    "agent_call_plan",
]
