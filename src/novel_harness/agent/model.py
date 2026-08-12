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

── 一处诚实交代：**对话那一档今天多半不是流式的** ─────────────────────────

`stream` 不是这儿定的，是 `plan_call` 推出来的。一次**对话回复**的输出预算
（`AGENT_REPLY_LENGTH` 倒推的 4,024）远在 16k 阈值之下，所以那条路上 `plan.stream`
通常是 `False`，而非流式的一次 HTTP 往返**没有可以插进去的位置**——
打断在那种形态下确实退化成「这一次调用跑完就停」。

**不许为了让它变成流式去抬输出预算**：那样 `stream` 的判据就从「输出多大」变成了
「谁想要流式」，而抬上去之后对**能力表没登记的模型**（`supports_streaming is None`）
`plan_call` 会 fail-closed 直接拒——作者换个自建端点，写作助手整个不能用。

**起草那一档 2026-08-12 起走的是另一条**：`plan_call(..., interruptible=True)`
（`ResolvedCallPlan.interruptible`）—— 显式说出「这次要能中途停」，能力表照旧说了算
（未登记 ⇒ 仍然不流式 ⇒ 仍然退化成「这一稿写完才停」）。抬预算那条路一步没走。

**这条落差直接决定了 ADR 0024 的「边写边看」今天落在哪一档**（2026-08-12 加）：
那份 ADR 说的是「那些片本来就在我们手里，只是没往外递」——**在手里的是起草那一档的片**。
对话回复走非流式的一次往返，里面没有「写到一半」这个时刻可以插进去，所以
`REPLY_DELTA` 在产品上今天基本不响，响的是 `DRAFT_DELTA`。而那正是 ADR 0024 §2
举的那个例子要的东西（「读到第三行发现语气不对 ⇒ 第 5 秒按停」——他读的是**稿子**）。
`tests/test_chat_boundary.py::test_todays_agent_call_is_not_streaming_…` 钉着这条落差，
**别为了让回复也流式去抬输出预算**（上一段写着那样会让没登记的端点整个用不了）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Final

from ..draft.capabilities import (
    ProviderCapabilities,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
    resolve_capabilities,
)
from ..draft.generate import CallInterrupted
from ..draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage, LengthSpec
from ..draft.provider import CompletionResult, ProviderConfig, ProviderError, complete
from .loop import Cancellation, EventFn, TurnEvent, safe_emitter

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


class AgentCancelled(CallInterrupted):
    """作者在一次**进行中**的调用里按了停。

    继承链是 `CallInterrupted` → `ProviderError`，两截各管一件事：

    * `ProviderError` 让它穿过 `complete()` 的 `except ProviderError: raise` 原样出来
      （不被重新包成一句「模型调用失败」）。`run_turn` 在 `except ProviderError` 里
      **先问信号再判故障**，所以它落在 `AUTHOR_STOPPED` 而不是 `MODEL_UNREACHABLE`。
    * `CallInterrupted`（`draft/generate.py`）让**起草那一层认得出它**，从而把已经
      到手的半截正文接住。对话那条路一个字都没变：`run_turn` 只看 `ProviderError`。
    """


def _visible_text(chunk: Any) -> str:
    """从一片 chunk 上取可见正文。**这是第二个读它的人**，第一个是
    `draft/provider.py::_from_stream`。

    ── 为什么必须在这儿再读一遍 ──────────────────────────────────────────────

    信号一亮，这条流就抛出去了，于是 `_from_stream` 那个累加器**连同它累到的字一起
    被丢掉**（异常从它中间穿过去）。而那些字是**付过钱的信息**。要么让运输层认得
    「取消」（那是 M2 冻结的那个文件，且取消是编排层的概念），要么在这儿自己数一份——
    选后者。

    **代价是一处必须跟着漂的重复**：那边认 `choices[].delta.content`，这边也只能认它。
    `tests/test_agent_model.py` 拿同一片 chunk 同时喂给两边，钉住它们读出同一段字。
    """
    out: list[str] = []
    for choice in getattr(chunk, "choices", ()) or ():
        content = getattr(getattr(choice, "delta", None), "content", None)
        if isinstance(content, str):
            out.append(content)
    return "".join(out)


TextSink = Callable[[str], None]
"""流上每收到一片可见正文就叫一次。**它不是 `EventFn`**，理由在 `_watch` 的 docstring。"""


def _watch(chunks: Any, cancel: Cancellation, on_text: TextSink | None = None) -> Iterator[Any]:
    """把一条流包成「每一片都先问一次信号」的流，**顺手把那一片字递出去**（ADR 0024）。

    `finally` 里关掉上游：作者按了停，那条 HTTP 连接就该断掉，而不是留着让服务端
    继续把整份输出生成完。**但别把这句话说成省钱**——断开连接 ≠ 停止生成 ≠ 停止计费，
    服务端会不会跟着停由供应商决定，这一层管不着（`ChapterDesk.write` 的 docstring
    把这条诚实话摆给了上面那层）。

    停下来时抛的那一份**带着已经收到的正文**（`partial_text`），理由见 `_visible_text`。

    ── `on_text` 为什么是 `Callable[[str], None]` 而不是 `EventFn` ──────────────

    **这一层不知道自己在为谁数字。** 同一个包装同时给两条路用：对话回复（`REPLY_DELTA`）
    和起草（`DRAFT_DELTA`，带章号和流号）。让它认得事件类型，就等于让它认得
    「这是第几章的第几稿」——那是上面那层的事。所以它只管把字递上去，
    **由持有者决定这一片叫什么**（`ProviderModelPort` / `ChapterDesk`）。

    **发不出去不许拿走这一次调用**：一个掉线的界面不该让作者已经付过钱的那一段字连同
    这次调用一起没掉。包安全的那一层在 `loop.safe_emitter`，这里再兜一次是因为
    **这一处是在 `provider.complete()` 的 try 之外**（同 `_real_client` 那条理由）。
    """
    seen: list[str] = []
    try:
        for chunk in chunks:
            # **先收下这一片，再问信号。** 顺序反过来会把已经到手的那一片扔掉——
            # 它已经生成、已经付过钱了，而这一层存在的全部理由就是别让那些字白花。
            # 抛的位置一点没变（仍然是同一次迭代，仍然在 `yield` 之前）。
            visible = _visible_text(chunk)
            seen.append(visible)
            if on_text is not None and visible:
                try:
                    on_text(visible)
                except Exception:  # noqa: BLE001 —— 见 docstring 最后一段
                    pass
            if cancel.stopped:
                raise AgentCancelled("作者中止了这一次调用", partial_text="".join(seen))
            yield chunk
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()


class _Completions:
    def __init__(self, real: Any, cancel: Cancellation, on_text: TextSink | None = None) -> None:
        self._real = real
        self._cancel = cancel
        self._on_text = on_text

    def create(self, **kwargs: Any) -> Any:
        if self._cancel.stopped:
            # **一个字节都还没发出去** ⇒ `sent=False` ⇒ 上面那层不许为它记一行账
            # （见 `CallInterrupted.sent`：记了就是账上凭空多一次没发生过的调用）。
            raise AgentCancelled("作者中止了这一次调用", sent=False)
        response = self._real.chat.completions.create(**kwargs)
        if not kwargs.get("stream"):
            # 非流式：整份响应已经回来了，钱已经花掉。**这里不抛**——抛掉等于把一次
            # 已经付过费的调用从账上抹掉，而 loop 下一次检查信号照样会停。
            #
            # **这一档也没有片可以往外递**：一次阻塞往返里没有「写到一半」这个时刻。
            # 对话那一档今天走的就是这条路（见模块 docstring 那段诚实交代）——
            # 也就是说边写边看今天只在**起草**那一档是真的。
            return response
        return _watch(response, self._cancel, self._on_text)


class _Chat:
    def __init__(self, real: Any, cancel: Cancellation, on_text: TextSink | None = None) -> None:
        self.completions = _Completions(real, cancel, on_text)


class _CancellableClient:
    """OpenAI 兼容客户端的一层薄壳：**只包 `chat.completions.create` 的返回值。**

    包客户端而不是改 `provider.py`，理由有两条且都是硬的：`draft/provider.py` 是 M2
    判分链的运输层（冻结），而且**取消是编排层的概念**——运输层认得它，就等于运输层
    知道有个「作者」和一个「停」按钮，那和「运输层不认识工具表」是同一条边界。

    **「边写边看」走的是同一条缝**，理由一模一样：那些片本来就从这儿过（ADR 0024
    「缺的是最后一厘米」），而 `provider.py` 的 wire 一个字节都不许动（M2 判分链）。
    """

    def __init__(self, real: Any, cancel: Cancellation, on_text: TextSink | None = None) -> None:
        self.chat = _Chat(real, cancel, on_text)


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


def cancellable_client(
    config: ProviderConfig, cancel: Cancellation, on_text: TextSink | None = None
) -> Any:
    """一个**把作者的「停」包在里面**的 OpenAI 兼容客户端。

    **对话和起草共用这一个包装**（`ChapterDesk` 拿它去起草那一次调用）。
    在别处再写一遍 `_CancellableClient(_real_client(...))` 就是第二个取消机制的开头，
    而我们已经选定了「信号对象 + 协作轮询」那一种（`loop.Cancellation`）。

    `on_text`：每收到一片可见正文叫一次（ADR 0024）。`None` = 不递，行为逐字节同以前。
    """
    return _CancellableClient(_real_client(config), cancel, on_text)


class ProviderModelPort:
    """`loop.ModelPort` 的实现。**`tools` 和 `cancel` 由 loop 给，这里不替换。**

    `on_event` 是**这一轮**的事件接线口（ADR 0024）。它在构造时进来，不在 `__call__` 里
    ——`ModelPort` 那个协议一个参数都没多，所以**别的 `ModelPort` 实现（测试里那些剧本
    模型）一个字都不用改**，而「不传 = 逐字节不变」在这条路上是构造出来的，不是测出来的。

    它和 `cancel` 是同一种东西：**一轮一个，装配层要把同一个交给 `run_turn`、这个端口和
    起草台**（`chapter_drafter` 那段 docstring 写着两个信号会怎么坏）。
    """

    def __init__(
        self,
        config: ProviderConfig,
        plan: ResolvedCallPlan,
        *,
        client: Any = None,
        on_event: EventFn | None = None,
    ) -> None:
        self._config = config
        self._plan = plan
        self._client = client
        self._emit = safe_emitter(on_event)
        self._streaming = on_event is not None

    def __call__(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]],
        cancel: Cancellation,
    ) -> CompletionResult:
        client = self._client if self._client is not None else _real_client(self._config)
        # **没人听的时候连闭包都不造**：`on_text=None` 走的是 `_watch` 里那条老路径，
        # 一个 `if` 都不多。有人听的时候这一片字才被复制一份递上去。
        on_text = (
            (lambda piece: self._emit(TurnEvent.reply_delta(piece)))
            if self._streaming
            else None
        )
        return complete(
            messages,
            config=self._config,
            plan=self._plan,
            client=_CancellableClient(client, cancel, on_text),
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
    "TextSink",
    "agent_call_plan",
    "cancellable_client",
]
