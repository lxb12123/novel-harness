"""起草可中断 —— **按「停」要在一稿写完之前真的停下来，而已经写出来的字要留下**。

在这一刀之前，打断走的是适配器包在**流**上的信号（`agent/model.py::_watch`），
而起草直接调 `complete()`、`plan.stream` 恒为 `False`（输出预算 7,024 远在 16k 阈值之下）
——于是那颗按钮对这一轮里最长的那一段（几十秒的一次整章起草）完全无效。

这份文件量四件「答错了不会有任何东西报错」的事：

1. **`stream` 是为「可中断」开的，不是靠抬输出预算绕开阈值。** 抬预算那条路会让
   **能力表没登记的端点**被 fail-closed 拒掉，症状是「聊天好好的、只有起草每次失败」。
2. **三臂那条路一个字节都没变。** 默认不设 `interruptible` ⇒ `stream` 仍然只由
   16k 阈值决定（wire 逐字节相同的证据在 `test_gate_wire_shape.py`，这儿钉的是判据本身）。
3. **半截那一稿留下来，而且带着「它没写完」一起留。** 那个标注不是元数据：
   一段断在半句的正文，模型下次读到它、若不知道那是被砍断的，
   **会把那个断口当成一种有意的写法去模仿**（迁移 010 的注释）。
4. **账不许因为「停」而消失。** 3.6 的教训是「起草中途失败 = 一次已付费调用从账上和
   成本闸上同时消失」，修法是 `ToolRefused.calls`——**取消走同一条路，不许另开**。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import novel_harness.agent.drafting as drafting
from novel_harness import importer
from novel_harness.agent.candidates import DraftCandidateStore
from novel_harness.agent.drafting import AUTHOR_STOPPED_NOTE, chapter_drafter
from novel_harness.agent.loop import Cancellation
from novel_harness.agent.model import AgentCancelled, _CancellableClient, _visible_text
from novel_harness.agent.ports import DraftAsk, ToolRefused
from novel_harness.agent.tools import DraftFullText
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import (
    STREAM_THRESHOLD_TOKENS,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.generate import CallInterrupted, generate_draft
from novel_harness.draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage
from novel_harness.draft.provider import ProviderConfig, _from_stream
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

ENDPOINT = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"
HOMEBREW = "https://localhost:11434/v1"
"""作者自建的那个端点。**能力表里没有它** ⇒ `supports_streaming is None`。"""

LENGTH = DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)


def _config(base_url: str = ENDPOINT, model: str = MODEL) -> ProviderConfig:
    return ProviderConfig(base_url=base_url, model=model, api_key="k", temperature=None)


# ══════════════════════════════════════════════════════════════════════════
# 一个假端点：**流是真的一片一片吐的**，因为要停的正是那个循环
# ══════════════════════════════════════════════════════════════════════════


def _chunk(text: str) -> Any:
    return SimpleNamespace(
        model=MODEL,
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, tool_calls=None),
                finish_reason=None,
            )
        ],
    )


class FakeEndpoint:
    """按次序回几条流。`stop_at` = 第几次调用的第几片之后作者按停。

    **它自己不抛任何东西**：停下来这件事必须由被测的那条链（`_watch` → `complete`）
    做出来，假端点只负责在对的时刻拨一下那个信号——不然测的就是这份假实现。
    """

    def __init__(
        self,
        pieces: list[list[str]],
        cancel: Cancellation | None = None,
        *,
        stop_at: tuple[int, int] | None = None,
    ) -> None:
        self.pieces = pieces
        self.cancel = cancel
        self.stop_at = stop_at
        self.calls = 0
        self.kwargs: list[dict[str, Any]] = []
        self.closed = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        index = self.calls
        self.calls += 1
        if not kwargs.get("stream"):
            text = "".join(self.pieces[index])
            return SimpleNamespace(
                model=MODEL,
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=text, tool_calls=None),
                        finish_reason="stop",
                    )
                ],
            )
        return _Stream(self, index)


class _Stream:
    def __init__(self, endpoint: FakeEndpoint, index: int) -> None:
        self._endpoint = endpoint
        self._index = index

    def __iter__(self) -> Any:
        endpoint = self._endpoint
        for position, text in enumerate(endpoint.pieces[self._index]):
            if endpoint.stop_at == (self._index, position) and endpoint.cancel is not None:
                # 作者在这一片到手的那一刻按下了停。
                endpoint.cancel.stop()
            yield _chunk(text)

    def close(self) -> None:
        self._endpoint.closed += 1


def _client(endpoint: FakeEndpoint, cancel: Cancellation) -> Any:
    """**真的那个取消包装**（`agent/model.py`），只是里面塞的是假端点。"""
    return _CancellableClient(endpoint, cancel)


# ══════════════════════════════════════════════════════════════════════════
# 一、`stream` 为「可中断」而开 —— 而不是靠抬预算绕过阈值
# ══════════════════════════════════════════════════════════════════════════


def test_streaming_opens_for_interruptibility_without_touching_the_budget() -> None:
    """**同一档长度，`interruptible` 只动 `stream` 这一位，预算一个 token 都不动。**

    抬预算是那条被明确禁掉的路：`stream` 的判据会从「输出多大」变成「谁想要流式」，
    而抬上去之后未登记的端点会被 fail-closed 拒掉。
    """
    capability = resolve_capabilities(ENDPOINT, MODEL)
    plain = plan_call(LENGTH, ReasoningEffort.OFF, capability)
    live = plan_call(LENGTH, ReasoningEffort.OFF, capability, interruptible=True)

    assert plain.stream is False, "起草那一档的预算本来就在阈值之下"
    assert plain.request_token_budget < STREAM_THRESHOLD_TOKENS
    assert live.stream is True
    # **除了 `stream` / `interruptible` 这两位，两份 plan 逐字段相同。**
    assert live.model_dump(exclude={"stream", "interruptible"}) == plain.model_dump(
        exclude={"stream", "interruptible"}
    )


def test_an_unregistered_endpoint_still_does_not_stream() -> None:
    """作者自建的端点（`supports_streaming is None`）：**不流式，但也不拒**。

    拒掉的形态很难查——**聊天好好的，只有起草每次失败**。所以这一档只是安静地退化成
    「这一稿写完才停」。
    """
    capability = resolve_capabilities(HOMEBREW, "my-local-model")
    assert capability.supports_streaming is None
    plan = plan_call(LENGTH, ReasoningEffort.OFF, capability, interruptible=True)
    assert plan.stream is False


def test_a_route_that_says_no_to_streaming_is_also_left_alone() -> None:
    """登记过、而且**明说不支持流式**的那一档同样不开（`False` 和 `None` 都不是 `True`）。"""
    capability = ProviderCapabilities(
        base_url=HOMEBREW,
        model="no-stream",
        source="registry:test-no-stream",
        source_urls=("https://example.test/docs",),
        max_context_tokens=200_000,
        max_output_tokens=64_000,
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        supports_streaming=False,
    )
    plan = plan_call(LENGTH, ReasoningEffort.OFF, capability, interruptible=True)
    assert plan.stream is False


def test_the_default_leaves_the_frozen_threshold_alone() -> None:
    """**不设它 = 今天那条式子一个字没改。** 三臂 / `nh gate` 走的就是这一档。"""
    capability = resolve_capabilities(ENDPOINT, MODEL)
    small = plan_call(LENGTH, ReasoningEffort.OFF, capability)
    big = plan_call(
        LENGTH, ReasoningEffort.OFF, capability, request_token_budget=STREAM_THRESHOLD_TOKENS + 1
    )
    assert (small.stream, big.stream) == (False, True)
    assert (small.interruptible, big.interruptible) == (False, False)


def test_a_hand_made_plan_cannot_claim_streaming_without_a_reason() -> None:
    """校验器仍然咬人：`stream=True` 必须**说得出是哪一个理由**（预算够大 / 要可中断）。

    这一条守的是那个绕法：`model_copy(update={"stream": True})` 跳过校验，而
    `provider._validate_call_plan` 会把 plan 严格重建一次——重建时这里要红。
    """
    dumped = plan_call(
        LENGTH, ReasoningEffort.OFF, resolve_capabilities(ENDPOINT, MODEL)
    ).model_dump()
    with pytest.raises(ValueError, match="threshold"):
        ResolvedCallPlan.model_validate(dumped | {"stream": True})
    # 反过来也要咬：说了要可中断、端点也支持，那 `stream` 就**必须**是真的。
    with pytest.raises(ValueError, match="threshold"):
        ResolvedCallPlan.model_validate(dumped | {"interruptible": True})


# ══════════════════════════════════════════════════════════════════════════
# 二、半截那一稿 —— **接得住，而且第一段不许跟着丢**
# ══════════════════════════════════════════════════════════════════════════


def _plan() -> Any:
    return plan_call(
        LENGTH, ReasoningEffort.OFF, resolve_capabilities(ENDPOINT, MODEL), interruptible=True
    )


def test_stopping_the_first_call_keeps_what_was_already_written() -> None:
    cancel = Cancellation()
    endpoint = FakeEndpoint([["风雪落在肩上，", "他终于抬起", "头。"]], cancel, stop_at=(0, 1))
    billed: list[Any] = []

    with pytest.raises(CallInterrupted) as caught:
        generate_draft(
            [{"role": "user", "content": "写一场对峙"}],
            length=LENGTH,
            config=_config(),
            plan=_plan(),
            client=_client(endpoint, cancel),
            on_attempt=billed.append,
        )

    assert caught.value.partial_text == "风雪落在肩上，他终于抬起"
    # **一次已经发出去的调用 = 一份回执**，哪怕它没跑完（钱可能已经花了）。
    assert len(billed) == 1
    assert billed[0].result.text == "风雪落在肩上，他终于抬起"
    # 供应商没报数 ⇒ 留空，**不许替它编一个**（账本只照抄）。
    assert billed[0].result.prompt_tokens is None
    assert billed[0].result.completion_tokens is None
    assert billed[0].result.finish_reason is None, "它没停在供应商说的任何一种理由上"
    assert endpoint.closed == 1, "那条 HTTP 连接该断掉"


def test_stopping_the_continuation_keeps_the_first_segment_too() -> None:
    """**停在续写那一次时，第一段是完整的、钱也付过了。**

    整章起草是一到两次调用（ADR 0011 D3）。只留半截、把第一段扔掉，是同一个错误的
    更贵版本——它扔掉的是一整次跑完的调用。
    """
    cancel = Cancellation()
    first = "太短了。"
    endpoint = FakeEndpoint([[first], ["接着写：", "风雪未停，"]], cancel, stop_at=(1, 1))
    billed: list[Any] = []

    with pytest.raises(CallInterrupted) as caught:
        generate_draft(
            [{"role": "user", "content": "写一场对峙"}],
            length=LENGTH,
            config=_config(),
            plan=_plan(),
            client=_client(endpoint, cancel),
            on_attempt=billed.append,
        )

    assert endpoint.calls == 2, "第一次答得太短 ⇒ 走了那一次续写"
    assert caught.value.partial_text == "太短了。接着写：风雪未停，"
    assert [attempt.number for attempt in billed] == [1, 2]
    assert billed[0].result.text == first


def test_a_stop_that_lands_before_the_request_goes_out_is_not_billed() -> None:
    """**信号在发出去之前就亮了 ⇒ 一分钱没花 ⇒ 账上不许多一行。**

    「不知道」和「0」要分得开，而「凭空多一次没发生过的调用」比两者都糟。
    """
    cancel = Cancellation()
    cancel.stop()
    endpoint = FakeEndpoint([["不该被发出去"]], cancel)
    billed: list[Any] = []

    with pytest.raises(CallInterrupted) as caught:
        generate_draft(
            [{"role": "user", "content": "写一场对峙"}],
            length=LENGTH,
            config=_config(),
            plan=_plan(),
            client=_client(endpoint, cancel),
            on_attempt=billed.append,
        )

    assert caught.value.sent is False
    assert billed == []
    assert endpoint.calls == 0


def test_the_adapter_reads_a_chunk_the_same_way_the_transport_does() -> None:
    """**这是那处必须跟着漂的重复的守卫。**

    `_visible_text`（适配器）和 `_from_stream`（运输层）各自读同一片 chunk：
    信号一亮流就抛出去，运输层那个累加器**连同它累到的字一起被丢掉**，所以适配器只能
    自己数一份。两边认的字段哪天分了家，这一条会红。
    """
    pieces = ["风雪", "落在", "肩上"]
    assert "".join(_visible_text(_chunk(text)) for text in pieces) == _from_stream(
        (_chunk(text) for text in pieces), MODEL
    ).text


# ══════════════════════════════════════════════════════════════════════════
# 三、端到端：候选表里那一行 + 它身上的标注 + 那笔账
# ══════════════════════════════════════════════════════════════════════════


def _root(conn: Connection, pid: str) -> Path:
    return Path(str(conn.execute(
        "SELECT root_path FROM project WHERE id = ?", (pid,)
    ).fetchone()["root_path"]))


class _NoSummaries:
    def for_range(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first: int, last: int) -> list[Any]:
        return []


def _desk(conn: Connection, pid: str, cancel: Cancellation | None, endpoint: Any) -> Any:
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=_config(),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        cancel=cancel,
    )


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    from novel_harness.draft.context import unknown_cast_constraints

    return (
        DraftAsk(chapter=chapter, goal="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def test_the_half_draft_is_kept_and_says_it_is_a_half_draft(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """按停 ⇒ **写出来的那部分收进候选表，带着「它没写完」**，而且账跟着一起交回去。

    留它的理由：那些 token 是**付过钱的信息**，扔掉 = 钱花了字没了。而 ADR 0022 之后
    起草本来就是「提议」，半截只是**短一点的提议**——它不会自动进书。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    cancel = Cancellation()
    endpoint = FakeEndpoint([["风雪落在肩上，", "他终于抬起", "头。"]], cancel, stop_at=(0, 1))
    monkeypatch.setattr(drafting, "cancellable_client", lambda config, signal: _client(
        endpoint, signal
    ))
    desk = _desk(conn, pid, cancel, endpoint)

    with pytest.raises(ToolRefused) as caught:
        desk.write(*_ask(conn, pid, 1))

    # ① 真的走了流式（不然这颗按钮对起草无效）
    assert endpoint.kwargs[0]["stream"] is True
    # ② 那一次已付费的调用**没有从账上消失**（3.6 的教训，走的是同一条路）
    assert [receipt.capability for receipt in caught.value.calls] == ["writer"]
    # ③ 半截那一稿在候选表里，**带着标注**
    stored = DraftCandidateStore(conn).recent(pid, chapter=1)
    assert len(stored) == 1
    assert stored[0].stopped_reason == AUTHOR_STOPPED_NOTE
    assert "风雪落在肩上" in DraftCandidateStore(conn).get(pid, stored[0].id).body
    # ④ 它也进了这一轮的产出（屏幕上作者看得见的就是这份）
    assert [c.id for c in desk.produced] == [stored[0].id]
    assert desk.produced[0].stopped_reason == AUTHOR_STOPPED_NOTE
    # ⑤ 说给模型听的那句话认得出「这一稿没写完」
    assert "没写完" in str(caught.value)
    # ⑥ **底稿哈希照记**：落盘那道闸对它和对一份写完的稿子是同一条判据。
    #    存 `None` 的那一版会让 `_land` 说出一句假话（「写这一稿的时候那一章还不存在」），
    #    而那一章一直都在。
    on_disk = importer.read_chapter(_root(conn, pid), 1)
    assert on_disk is not None
    assert DraftCandidateStore(conn).get(pid, stored[0].id).base_sha256 == importer.text_digest(
        on_disk
    )
    conn.close()


def test_the_annotation_comes_back_with_the_text_when_the_model_reads_it(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**按 id 读回时标注必须跟着一起回来**（迁移 010 写死的那条）。

    漏掉它不会有任何东西报错：模型拿到一段断在半句的正文，**会把那个断口当成一种
    有意的写法去模仿**——「他缓缓抬起手，然后——」在小说里读起来像一个刻意的悬停。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    cancel = Cancellation()
    endpoint = FakeEndpoint([["他缓缓抬起手，", "然后——", "尾巴"]], cancel, stop_at=(0, 1))
    monkeypatch.setattr(drafting, "cancellable_client", lambda config, signal: _client(
        endpoint, signal
    ))
    desk = _desk(conn, pid, cancel, endpoint)
    with pytest.raises(ToolRefused):
        desk.write(*_ask(conn, pid, 1))
    candidate_id = desk.produced[0].id

    recalled = desk.recall(candidate_id)
    assert recalled.stopped_reason == AUTHOR_STOPPED_NOTE

    # 工具那一层的出参也带着它——**模型读到的就是这一份**。
    outgoing = DraftFullText(
        draft_id=recalled.id,
        chapter=recalled.chapter,
        ordinal=recalled.ordinal,
        units=recalled.units,
        note=recalled.note,
        landed=recalled.landed,
        text=recalled.body,
        stopped_reason=recalled.stopped_reason,
    )
    assert AUTHOR_STOPPED_NOTE in outgoing.model_dump_json()
    conn.close()


def test_a_finished_draft_carries_no_annotation(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫**：没被停过的那一稿这一位是空的。

    恒非空的标注等于把「它没写完」贴在每一稿上，作者会当噪声划掉——
    然后真正断掉的那一稿也就没人看得见了。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    cancel = Cancellation()
    endpoint = FakeEndpoint([["写完了。" * 700]], cancel)
    monkeypatch.setattr(drafting, "cancellable_client", lambda config, signal: _client(
        endpoint, signal
    ))
    desk = _desk(conn, pid, cancel, endpoint)

    product = desk.write(*_ask(conn, pid, 1))

    assert product.candidate.stopped_reason == ""
    assert desk.recall(product.candidate.id).stopped_reason == ""
    conn.close()


def test_nothing_written_before_the_stop_leaves_no_candidate_but_still_leaves_a_bill(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个字都没收到 ⇒ **没有「短一点的提议」可留**，但那一次调用照旧记账。

    收一份空白进表只会占一个编号，让模型以为手上有东西（同空稿那一档）；
    而不记账就是 3.6 那个洞原样搬回来——**已经发出去的请求，服务端那边可能已经在生成了。**
    """
    conn = connect(book["db"])
    pid = book["pid"]
    cancel = Cancellation()
    # 第一片就是空的（端点开了个头就被掐了），**但请求已经发出去了**。
    endpoint = FakeEndpoint([[""]], cancel, stop_at=(0, 0))
    monkeypatch.setattr(drafting, "cancellable_client", lambda config, signal: _client(
        endpoint, signal
    ))
    desk = _desk(conn, pid, cancel, endpoint)

    with pytest.raises(ToolRefused) as caught:
        desk.write(*_ask(conn, pid, 1))

    assert DraftCandidateStore(conn).recent(pid, chapter=1) == []
    assert desk.produced == []
    assert len(caught.value.calls) == 1, "请求已经发出去了，账不许跟着消失"
    assert caught.value.calls[0].text == "", "一个字都没收到 —— 账上照抄这件事，不许编"
    conn.close()


def test_without_a_signal_drafting_behaves_exactly_as_before(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**没接停止信号的那一档一个字节都没变**：不流式、不包客户端。

    CLI 和测试里的那些形态走的就是这一条，而 `/draft` 那条 HTTP 路由也一样。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    endpoint = FakeEndpoint([["写完了。" * 700]])
    built: list[Any] = []
    monkeypatch.setattr(drafting, "cancellable_client", lambda *a, **k: built.append(a) or None)
    # 没有 cancel ⇒ `client=None` ⇒ `complete()` 自己去造客户端。这里把那一步换掉，
    # 免得测试真的去 import openai 建一个客户端。
    monkeypatch.setattr("novel_harness.draft.provider._build_client", lambda config: endpoint)
    desk = _desk(conn, pid, None, endpoint)

    product = desk.write(*_ask(conn, pid, 1))

    assert built == [], "没信号就不该有那层包装"
    assert endpoint.kwargs[0]["stream"] is False
    assert product.candidate.stopped_reason == ""
    conn.close()


def test_the_cancellation_reaches_the_desk_and_the_loop_as_one_object() -> None:
    """**起草台和 loop 拿的必须是同一个信号对象。**

    两个信号 = 按停只停住其中一半，而作者看到的是「按了停，那一稿还在写」——
    起草那一次调用是这一轮里最长的一段。
    """
    import inspect

    import novel_harness.api.chat as chat_mod

    source = inspect.getsource(chat_mod.run_chat)
    assert "cancel=signal" in source, "起草台没拿到这一轮的信号"
    assert source.count("cancel=signal") == 2, "loop 和起草台必须各拿一次，且是同一个 `signal`"


def test_the_stopped_annotation_carries_no_markdown() -> None:
    """那句标注**原样落到屏幕上**，而那块屏幕不渲染 markdown。

    `stop_wording(CONTEXT_FULL)` 里那一对字面量 `**` 就是这么变成两颗星号摆在作者脸上的。
    这一句更硬：它进的是**库**，改措辞救不回已经写下去的那些行。
    """
    assert "*" not in AUTHOR_STOPPED_NOTE
    assert "`" not in AUTHOR_STOPPED_NOTE


def test_the_agent_cancellation_is_still_a_provider_error() -> None:
    """继承链的自守卫：对话那条路只认 `ProviderError`，它一个字都不该改。"""
    from novel_harness.draft.provider import ProviderError

    stopped = AgentCancelled("停")
    assert isinstance(stopped, CallInterrupted)
    assert isinstance(stopped, ProviderError)
