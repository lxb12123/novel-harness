"""对抗性验证：**起草可中断**这一刀有没有在别处捅出洞。

这份文件不复述 `tests/test_draft_interrupt.py` 的断言，它只量六件
「答错了不会有任何东西报错」的事，每一件配一个自守卫（**把被测的那一条拆掉，
断言这张网当场红**——没有自守卫的绿等于没测）：

1. **M2 那条路一个字节没变。** `stream` 是 wire 上的一个字段，而三臂是冻结的考卷。
   逐个 case 对着**手写一遍的旧式子**比，并断言 `interruptible` 这个名字
   在 wire 上一次都不出现。
2. **未登记的端点没有被卷进流式。** 三态各造一次；**「未登记 = 支持」是最坏的一种**。
3. **真的停在中途。** 数上游被拉了几片，不是「最后返回了」——
   一条读到底才抛的流在返回值上和真停下来长得一模一样，而钱差一整章。
4. **半截那一稿：存下来 · 标注在 · 按 id 读回来标注也在 · 它没有落盘。**
5. **账**：被取消的那一次必须在账上，而**账本那两列照抄供应商报的（含空值）**，
   闸门的估算不许渗进去。
6. **过期的「停」**：把那个序列真的造出来（旧一轮跑完 → 新一轮开始 → 停止请求才到），
   断言新的那一轮没被杀掉。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.agent.drafting as drafting
import novel_harness.api.chat as chat_mod
from novel_harness import importer
from novel_harness.agent.candidates import DraftCandidateStore
from novel_harness.agent.drafting import AUTHOR_STOPPED_NOTE, chapter_drafter
from novel_harness.agent.loop import Cancellation, StopReason
from novel_harness.agent.model import _CancellableClient
from novel_harness.agent.ports import DraftAsk, ToolRefused
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import (
    CAPABILITY_REGISTRY,
    STREAM_THRESHOLD_TOKENS,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.context import unknown_cast_constraints
from novel_harness.draft.length import (
    DEFAULT_LENGTH_POLICY,
    M2_LENGTH_SPEC,
    DraftLanguage,
    LengthSpec,
)
from novel_harness.draft.provider import ProviderConfig, _wire_kwargs
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from test_chat_api import Scripted, open_chat, says, use, wants

GATE_ROUTE = ("https://api.deepseek.com", "deepseek-v4-flash")
HOMEBREW = ("https://localhost:11434/v1", "my-local-model")
"""作者自己搭的那个端点。**能力表里没有它** ⇒ `supports_streaming is None`。"""

LENGTH = DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)
MESSAGES = [{"role": "system", "content": "系统"}, {"role": "user", "content": "写第 40 章"}]


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 BYOK 指到一个**登记过**的端点（同 `test_chat_api.py` 那一份）。

    设置文件落在 `tmp_path`，**绝不碰真实用户目录**——这个仓库有过一次
    「开着的 dev server 把新迁移应用到作者真书上」的事故，方向是同一个。
    """
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", GATE_ROUTE[0])
    monkeypatch.setenv("NH_LLM_MODEL", GATE_ROUTE[1])
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


def _config(route: tuple[str, str]) -> ProviderConfig:
    return ProviderConfig(base_url=route[0], model=route[1], api_key="k", temperature=None)


def _spec(target: int, maximum: int) -> LengthSpec:
    return DEFAULT_LENGTH_POLICY.validate_spec(
        LengthSpec(
            language=DraftLanguage.ZH,
            min_units=max(1, target // 2),
            target_units=target,
            max_units=maximum,
        )
    )


SPECS = {
    "tiny": _spec(200, 800),
    "agent": LENGTH,
    "m2": M2_LENGTH_SPEC,
    "huge": _spec(10_000, 20_000),
}
"""四档长度，**跨过 16k 那个阈值的两侧**——只测一侧的话，翻转了也看不出来。"""


# ══════════════════════════════════════════════════════════════════════════
# 一、M2 那条路：`stream` 的取值逐个 case 与改动前相同
# ══════════════════════════════════════════════════════════════════════════


def _frozen_stream(request_token_budget: int) -> bool:
    """**这一刀之前那条式子，在这儿手抄一遍**（`capabilities.py` 的旧 `plan_call`）。

    故意不 import 现在那个 `_streams`：拿被测代码去验被测代码，改错了两边一起错。
    """
    return request_token_budget > STREAM_THRESHOLD_TOKENS


def _cases() -> list[tuple[str, LengthSpec, ReasoningEffort, ProviderCapabilities, float | None]]:
    """注册表全部路由 × 全部 effort × 温度给/不给 × 跨阈值的四档预算。

    **拒掉的组合一并留在表里**（某些 effort 在某些路由上本来就发不出去）：
    `_wire` 会把「两棵树都拒」当成一个可比的结局，去掉它们等于让矩阵自己缩水。
    """
    out = []
    for route, capability in sorted(CAPABILITY_REGISTRY.items()):
        for effort in sorted(ReasoningEffort, key=lambda e: e.value):
            for temperature in (None, 0.7):
                for name, spec in sorted(SPECS.items()):
                    out.append(
                        (f"{route}|{effort.value}|{temperature}|{name}", spec, effort,
                         capability, temperature)
                    )
    return out


def _wire(spec: LengthSpec, effort: ReasoningEffort, capability: ProviderCapabilities,
          temperature: float | None, **kwargs: Any) -> tuple[dict[str, Any] | str, int]:
    """这一个 case 发出去的那一份（或者「它在发出去之前就被拒了」）+ 输出预算。"""
    try:
        config = ProviderConfig(
            base_url=capability.base_url, model=capability.model, api_key="k",
            temperature=temperature,
        )
        plan = plan_call(spec, effort, capability, **kwargs)
    except Exception as exc:  # noqa: BLE001 —— 拒绝本身也是一个要对拷的结局
        return f"refused:{type(exc).__name__}", -1
    return _wire_kwargs(config, plan, MESSAGES), plan.request_token_budget


def test_the_frozen_arms_still_stream_by_exactly_the_old_rule() -> None:
    """**不设 `interruptible` ⇒ `stream` 只由那条冻结的式子决定，逐个 case。**

    三臂和 gate 走的就是这一档。这条式子改错了，三臂发出去的东西就和
    改动前不可比——而那是一份已经跑过九轮、且协议先于结果冻结的考卷。
    """
    checked = 0
    for label, spec, effort, capability, temperature in _cases():
        wire, budget = _wire(spec, effort, capability, temperature)
        if isinstance(wire, str):
            continue
        assert wire["stream"] is _frozen_stream(budget), label
        checked += 1
    assert checked >= 60, f"这张网自己空了 —— 只量到 {checked} 个 case，绿的是空转"


def test_gate_route_is_untouched_and_the_new_bit_never_reaches_the_wire() -> None:
    """gate 那一条（`M2_LENGTH_SPEC` + `HIGH` + 冻结 profile 路由）逐字节量一遍，
    并且**「可中断」这个名字在 wire 上一次都不出现**——它是编排层的意图，不是协议字段。
    """
    capability = resolve_capabilities(*GATE_ROUTE)
    wire, budget = _wire(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability, None)
    assert isinstance(wire, dict)
    assert budget > STREAM_THRESHOLD_TOKENS, "M2 的输出预算本来就在阈值之上"
    assert wire["stream"] is True
    assert set(wire) == {
        "model", "messages", "max_tokens", "stream", "reasoning_effort", "extra_body",
    }, "wire 上多了或少了字段"
    blob = json.dumps(wire, ensure_ascii=False, sort_keys=True, default=str)
    assert "interruptible" not in blob

    # **每一条路由都验一遍这一句**，不是只验 gate 那一条。
    for _, spec, effort, cap, temperature in _cases():
        each, _ = _wire(spec, effort, cap, temperature)
        assert "interruptible" not in json.dumps(each, ensure_ascii=False, default=str)


def test_the_net_above_is_alive_because_turning_the_bit_on_changes_the_wire() -> None:
    """**自守卫**：把那一位翻成开启，上面那张网必须当场红。

    恒真的断言也能让 `test_the_frozen_arms_still_stream_by_exactly_the_old_rule` 变绿，
    所以这里反着量一次：同一个矩阵、只改这一位，**必须有 case 的 wire 变了**。
    """
    changed = 0
    for _, spec, effort, capability, temperature in _cases():
        plain, _ = _wire(spec, effort, capability, temperature)
        live, _ = _wire(spec, effort, capability, temperature, interruptible=True)
        if plain != live:
            changed += 1
            assert isinstance(plain, dict) and isinstance(live, dict)
            # **翻转那一位今天有两种可见后果**（2026-08-13 起）：
            # ① 本来不流式的变成流式（预算在阈值之下的那些）；
            # ② 本来就流式的多出 `stream_options`（要用量，见 `provider.py`）。
            # 只断言 ① 会让 ② 那一批悄悄从这张自守卫里溜掉。
            turned_on = plain["stream"] is False and live["stream"] is True
            asked_usage = "stream_options" not in plain and "stream_options" in live
            assert turned_on or asked_usage
    assert changed > 0, "翻转那一位一个 case 都没动 —— 上面那张网量的是空气"


# ══════════════════════════════════════════════════════════════════════════
# 二、未登记的端点没有被卷进流式
# ══════════════════════════════════════════════════════════════════════════


def _capability(streaming: bool | None) -> ProviderCapabilities:
    return ProviderCapabilities(
        base_url=HOMEBREW[0],
        model=f"probe-{streaming}",
        source="metadata:probe",
        source_urls=("https://example.test/probe",),
        max_context_tokens=200_000,
        max_output_tokens=64_000,
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
    )


def test_wanting_to_stop_is_enough_to_get_a_stream() -> None:
    """**2026-08-13：判据从「这条路由登记过支持流式」换成「这一次要不要能停」。**

    原来这条叫 `test_only_an_endpoint_that_says_yes_gets_streamed`，参数化 `None/False/True`
    三档，断言只有 `True` 会流式。**那个默认方向是错的**，而它的代价不是「保守」：
    作者接任何自定义端点，「停」按钮和「边写边看」一起哑掉，**且不报错**。

    换掉它的理由是协议本身 —— `stream` 是 OpenAI Chat Completions 的**基本功能**，
    自称兼容就得支持（对照过 Cursor：它根本不存这一位，Verify 发的就是一次流式请求）。
    所以 `supports_streaming` 连同那三档一起从能力表上删了。

    原来那条 docstring 担心的是「吐不出 chunk 而我们按流式读 ⇒ 起草每次失败」。
    **那一档今天由运输层兜**：真拒绝的端点会在第一次失败时被记住（`_NO_STREAM_OPTIONS`
    是同一套思路），而「学得会的东西不进能力表」正是这次改动的判据。
    """
    capability = _capability(None)  # 未登记的那一份
    plan = plan_call(LENGTH, ReasoningEffort.OFF, capability, interruptible=True)
    assert plan.stream is True
    assert plan.interruptible is True, "意图照记 —— 它说得出自己想要什么、拿到了什么"

    wire = _wire_kwargs(_config((capability.base_url, capability.model)), plan, MESSAGES)
    assert wire["stream"] is True
    # 可中断这一档要用量（丢账的正是它）；M2 那一档不要，见 `provider.py` 那段注释。
    assert wire["stream_options"] == {"include_usage": True}

    quiet = plan_call(LENGTH, ReasoningEffort.OFF, capability)
    assert quiet.stream is False, "不想停就别流 —— 判据只剩这一条"


def test_an_unregistered_endpoint_is_not_refused_either() -> None:
    """**也不许 fail-closed 拒掉它。** 拒的症状很难查：聊天好好的、只有起草每次失败。

    `resolve_capabilities` 对未登记的路由给的是真的那一份未知能力，不是构造出来的。
    """
    capability = resolve_capabilities(*HOMEBREW)
    assert capability.source == "unknown"
    plan = plan_call(LENGTH, ReasoningEffort.OFF, capability, interruptible=True)
    assert plan.stream is True, "未登记不再等于不流式"


# ══════════════════════════════════════════════════════════════════════════
# 三、真的停在中途 —— 数上游被拉了几片
# ══════════════════════════════════════════════════════════════════════════


def _chunk(text: str) -> Any:
    return SimpleNamespace(
        model=GATE_ROUTE[1],
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None),
                                finish_reason=None)],
    )


class SlowStream:
    """一条**吐得很慢**的流：每被拉一片就记一笔，中途由 `stop_after` 亮信号。

    它自己**不抛任何东西**——停下来这件事必须由被测的那条链做出来。
    """

    def __init__(self, pieces: list[str], cancel: Cancellation, *, stop_after: int) -> None:
        self.pieces = pieces
        self.cancel = cancel
        self.stop_after = stop_after
        self.pulled = 0
        self.closed = 0

    def __iter__(self) -> Any:
        for index, text in enumerate(self.pieces):
            self.pulled += 1
            yield _chunk(text)
            if index == self.stop_after:
                self.cancel.stop()

    def close(self) -> None:
        self.closed += 1


class _Endpoint:
    def __init__(self, stream: SlowStream) -> None:
        self.stream = stream
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        assert kwargs["stream"] is True, "这条测试的前提是流式"
        return iter(self.stream)


def _drain(cancel: Cancellation | None, pieces: list[str], stop_after: int) -> SlowStream:
    """把一条流从头读到尾（或者读到被停），返回那条流本身好数它被拉了几片。"""
    from novel_harness.draft.provider import _from_stream

    stream = SlowStream(pieces, cancel or Cancellation(), stop_after=stop_after)
    endpoint = _Endpoint(stream)
    client: Any = _CancellableClient(endpoint, cancel) if cancel is not None else endpoint
    raw = client.chat.completions.create(stream=True)
    try:
        _from_stream(raw, GATE_ROUTE[1])
    except Exception:  # noqa: BLE001 —— 停下来那一档抛的就是被测的那个
        pass
    close = getattr(raw, "close", None)
    if callable(close):
        close()
    return stream


def test_the_stop_really_stops_pulling_instead_of_reading_to_the_end() -> None:
    """**它没有把整个流读完。**

    一条「读到底再抛」的实现在返回值上和真停下来长得一模一样（都是异常、都有半截正文），
    差别只在**上游还在不在吐**——而那正是这颗按钮唯一能省下的东西。
    """
    pieces = [f"第{i}片。" for i in range(50)]
    cancel = Cancellation()
    stream = _drain(cancel, pieces, stop_after=2)

    # 第 3 片到手之后按停 ⇒ 最多再被拉一片（那一片是判断信号的那次迭代拉的）。
    assert stream.pulled <= 4, f"停下来之后还在拉：拉了 {stream.pulled} 片"
    assert stream.pulled >= 3, "第 3 片之前就停了 —— 那是另一种错"


def test_without_the_wrapper_the_whole_stream_is_drained() -> None:
    """**自守卫**：同一条流、同一个信号，只是不套那层取消包装 ⇒ **50 片全被拉走**。

    没有这一条，上面那个 `pulled <= 4` 可能只是因为假流本来就短。
    """
    pieces = [f"第{i}片。" for i in range(50)]
    stream = _drain(None, pieces, stop_after=2)
    assert stream.pulled == 50


# ══════════════════════════════════════════════════════════════════════════
# 四、半截那一稿 —— 存下来 · 标注在 · 读回来还在 · **没有落盘**
# ══════════════════════════════════════════════════════════════════════════


class _Pieces:
    """按次序回几条流；`stop_at=(第几次调用, 第几片)` 那一刻作者按停。"""

    def __init__(self, script: list[list[str]], cancel: Cancellation,
                 *, stop_at: tuple[int, int] | None) -> None:
        self.script = script
        self.cancel = cancel
        self.stop_at = stop_at
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        assert kwargs["stream"] is True
        return self._yield(index)

    def _yield(self, index: int) -> Any:
        for position, text in enumerate(self.script[index]):
            if self.stop_at == (index, position):
                self.cancel.stop()
            yield _chunk(text)


class _NoSummaries:
    def for_range(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def snapshot_watermark(self, project_id: str, chapter_number: int) -> Any:
        return None


def _root(conn: Connection, pid: str) -> Path:
    row = conn.execute("SELECT root_path FROM project WHERE id = ?", (pid,)).fetchone()
    return Path(str(row["root_path"]))


def _desk(conn: Connection, pid: str, cancel: Cancellation) -> Any:
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=_config(GATE_ROUTE),
        capability=resolve_capabilities(*GATE_ROUTE),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        cancel=cancel,
    )


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    return (
        DraftAsk(chapter=chapter, calibration_id="test:unused"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _write(desk: Any, conn: Connection, pid: str, chapter: int) -> Any:
    ask, ctx = _ask(conn, pid, chapter)
    return desk.write(ask, ctx, goal="写一场对峙")


def _stop_a_draft(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch, script: list[list[str]],
    stop_at: tuple[int, int],
) -> tuple[Connection, str, Any, ToolRefused, _Pieces]:
    conn = connect(book["db"])
    pid = book["pid"]
    cancel = Cancellation()
    endpoint = _Pieces(script, cancel, stop_at=stop_at)
    monkeypatch.setattr(
        drafting, "cancellable_client",
        lambda config, signal, on_text=None: _CancellableClient(endpoint, signal, on_text),
    )
    desk = _desk(conn, pid, cancel)
    with pytest.raises(ToolRefused) as caught:
        _write(desk, conn, pid, 1)
    return conn, pid, desk, caught.value, endpoint


def _chapter_bytes(conn: Connection, pid: str, chapter: int) -> str | None:
    return importer.read_chapter(_root(conn, pid), chapter)


def _snapshots(conn: Connection, pid: str, chapter: int) -> list[str]:
    return [s.text for s in SqliteStoryGraph(conn).chapter_snapshots(pid, chapter)]


@pytest.mark.parametrize(
    ("script", "stop_at", "keeps"),
    [
        # ① 停在**第一次**调用中途
        ([["雪落在肩上，", "他抬起", "头。"]], (0, 1), "雪落在肩上，他抬起"),
        # ② 停在**续写**那一次中途（第一次答太短 ⇒ ADR 0011 D3 的第二次调用）
        ([["太短。"], ["接着写：", "风雪未停，"]], (1, 1), "太短。接着写：风雪未停，"),
    ],
    ids=["first_call", "continuation"],
)
def test_a_half_draft_is_kept_labelled_readable_and_never_lands(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch,
    script: list[list[str]], stop_at: tuple[int, int], keeps: str,
) -> None:
    """两档分别造：**存下来 · 标注在 · 按 id 读回来标注也在 · 它没有落盘。**

    最后那一条是这条测试真正的价值：半截的一稿**留着**是对的（那些 token 付过钱了），
    而「留着」和「进书」之间只隔着 ADR 0022 那一刀。它自己溜进那一章的形态是
    ——作者的那一章被一段断在半句的字盖掉，而他什么都没按。
    """
    conn, pid, desk, refused, endpoint = _stop_a_draft(book, monkeypatch, script, stop_at)
    try:
        stored = DraftCandidateStore(conn).recent(pid, chapter=1)
        assert len(stored) == 1, "半截那一稿没被收下来"
        assert stored[0].stopped_reason == AUTHOR_STOPPED_NOTE, "列表里那一行没带标注"

        full = DraftCandidateStore(conn).get(pid, stored[0].id)
        assert full is not None
        assert full.body == keeps, "留下来的正文不是到停下来那一刻收到的那些字"
        # **按 id 读回来时标注也在**（迁移 010 写死的那条）。
        assert full.stopped_reason == AUTHOR_STOPPED_NOTE

        # ── 它没有落盘 ────────────────────────────────────────────────────
        # （逐字节的「那一章一点没动」在下面 `test_the_disk_is_untouched…` 里量，
        #   这儿量的是更直接的一句：**那些字没出现在书里**。）
        assert full.landed is False
        assert keeps not in (_chapter_bytes(conn, pid, 1) or ""), "半截的字进了那一章"
        assert not any(keeps in text for text in _snapshots(conn, pid, 1))
        assert refused.calls, "已经发出去的那次调用不许从账上消失"
    finally:
        conn.close()


def test_the_annotation_is_what_the_net_above_is_actually_watching(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**自守卫：把标注去掉，上面那张网必须抓得住。**

    漏掉它不会有任何东西报错，而后果是模型下次读到这一稿、**把那个断口当成一种
    有意的写法去模仿**——「他缓缓抬起手，然后——」在小说里读起来像一个刻意的悬停。
    """
    monkeypatch.setattr(drafting, "AUTHOR_STOPPED_NOTE", "")
    conn, pid, desk, _refused, _endpoint = _stop_a_draft(
        book, monkeypatch, [["雪落在肩上，", "他抬起", "头。"]], (0, 1)
    )
    try:
        stored = DraftCandidateStore(conn).recent(pid, chapter=1)
        assert len(stored) == 1, "自守卫本身失效了：连稿子都没留下"
        # 标注被拆掉之后，那一稿和一份写完的稿子长得**一模一样**——这正是要防的形态。
        assert stored[0].stopped_reason == ""
        assert DraftCandidateStore(conn).get(pid, stored[0].id).stopped_reason == ""
    finally:
        conn.close()


def test_the_disk_is_untouched_because_landing_is_a_separate_action(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**这一章在磁盘上逐字节没动。** 先量下来再停，不靠「大概没变」。"""
    conn0 = connect(book["db"])
    before = _chapter_bytes(conn0, book["pid"], 1)
    before_snapshots = _snapshots(conn0, book["pid"], 1)
    conn0.close()
    assert before is not None, "第 1 章本来就该在磁盘上"

    conn, pid, _desk, _refused, _endpoint = _stop_a_draft(
        book, monkeypatch, [["雪落在肩上，", "他抬起", "头。"]], (0, 1)
    )
    try:
        assert _chapter_bytes(conn, pid, 1) == before
        assert _snapshots(conn, pid, 1) == before_snapshots
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 五、账 —— 被取消的那次在账上，而账本那两列照抄供应商报的
# ══════════════════════════════════════════════════════════════════════════


def _draft_asks(chapter: int) -> Any:
    return wants(("draft_chapter", json.dumps({"chapter": chapter, "goal": "写一场对峙"})))


class _CalibrateSealDraft:
    """校准 → 封存 → 起草（第 1 章）。`Scripted` 是静态的，编号只能现读。"""

    def __init__(self, after: Any) -> None:
        self.after = after
        self.calls = 0

    def _tool_results(self, messages: Any) -> list[dict[str, Any]]:
        return [
            json.loads(m["content"])
            for m in messages
            if m.get("role") == "tool" and str(m.get("content", "")).strip()
        ]

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return wants(("calibrate_scene", json.dumps({"chapter": 1})))
        if self.calls == 2:
            results = self._tool_results(messages)
            ids = [r["id"] for r in results if "id" in r]
            return wants(
                ("seal_scene_brief", json.dumps({"inspection_id": ids[0]}))
            )
        if self.calls == 3:
            results = self._tool_results(messages)
            pairs = [
                (r["chapter"], r["calibration_id"])
                for r in results
                if "calibration_id" in r
            ]
            return wants(
                (
                    "draft_chapter",
                    json.dumps(
                        {"chapter": pairs[0][0], "calibration_id": pairs[0][1]}
                    ),
                )
            )
        return self.after


def _bills(conn: Connection, pid: str) -> list[Any]:
    return conn.execute(
        "SELECT capability, tokens_in, tokens_out, ms FROM model_call"
        " WHERE project_id = ? AND capability = 'writer'",
        (pid,),
    ).fetchall()


def test_the_cancelled_call_is_on_the_bill_and_the_two_columns_stay_empty(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**钱已经花了 ⇒ 那一次必须在账上**；而**账本照抄供应商报的（这一档是空）**。

    两个方向各错一次都很贵：
    * 漏记 = 3.6 那个洞原样搬回来（一次已付费调用从账上和成本闸上同时消失）；
    * 拿闸门那个估算填上去 = 账本编数，而「不知道」和「0」从此分不开。
    """
    pid = book["pid"]
    cancel_box: list[Cancellation] = []
    endpoint_box: list[_Pieces] = []

    def fake_client(config: Any, signal: Cancellation, on_text: Any = None) -> Any:
        cancel_box.append(signal)
        endpoint = _Pieces([["雪落在肩上，", "他抬起", "头。"]], signal, stop_at=(0, 1))
        endpoint_box.append(endpoint)
        return _CancellableClient(endpoint, signal)

    monkeypatch.setattr(drafting, "cancellable_client", fake_client)
    use(monkeypatch, _CalibrateSealDraft(says("那一稿没写完。")))
    chat_id = open_chat(client, pid)

    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "写第 1 章", "run_id": "r1"},
    )
    assert turn.status_code == 200, turn.text
    assert endpoint_box and endpoint_box[0].calls == 1, "起草那一次根本没发出去"

    conn = connect(book["db"])
    try:
        billed = _bills(conn, pid)
        assert len(billed) == 1, "被取消的那一次不在账上"
        # **照抄：供应商没报 ⇒ 库里是 NULL，不是 0。** 写 0 的那一版会让底栏说出
        # 「入 0 / 出 0 token」，而那是一句它不知道真假的话。
        assert billed[0]["tokens_in"] is None
        assert billed[0]["tokens_out"] is None
    finally:
        conn.close()

    # 闸门那一侧另算：它走估算，而**估算只进闸门**（`calls_without_usage` 是那个口径的
    # 计数器）。两个消费者两套规矩，混起来就是账本编数。
    receipt = turn.json()
    assert receipt["calls_without_usage"] >= 1
    assert receipt["tokens_reported"] == 0, "没人报数 ⇒ 报出来的总和就是 0"
    # 半截那一稿仍然摆到了作者面前（不然屏幕上少一稿而没有任何东西报错）。
    assert [d["stopped_reason"] for d in receipt["drafts"]] == [AUTHOR_STOPPED_NOTE]
    # **屏幕上那句话是「按你的意思停下了」，不是「说完了」。** 起草被砍断之后 loop
    # 不许接着往下跑：那颗按钮按下去、模型却还在花钱说话，作者读到的是「按钮没反应」。
    assert receipt["reason"] == StopReason.AUTHOR_STOPPED.value


def test_a_stop_before_the_request_leaves_no_bill_at_all(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**自守卫（反方向）**：信号在发出去之前就亮了 ⇒ 一分钱没花 ⇒ 账上不许多一行。

    没有这一条，上面那句「必须在账上」可以靠「凡是取消都记一笔」变绿，
    而那是账上凭空多一次没发生过的调用。
    """
    pid = book["pid"]

    def fake_client(config: Any, signal: Cancellation, on_text: Any = None) -> Any:
        signal.stop()  # 作者在这一次发出去之前就按了停
        return _CancellableClient(_Pieces([["不该发出去"]], signal, stop_at=None), signal)

    monkeypatch.setattr(drafting, "cancellable_client", fake_client)
    use(monkeypatch, Scripted(_draft_asks(1), says("停了。")))
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "写第 1 章", "run_id": "r1"},
    )
    assert turn.status_code == 200, turn.text

    conn = connect(book["db"])
    try:
        assert _bills(conn, pid) == []
        assert DraftCandidateStore(conn).recent(pid, chapter=1) == []
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 六、过期的「停」—— 把那个序列真的造出来
# ══════════════════════════════════════════════════════════════════════════


def _run_turn(client: TestClient, pid: str, chat_id: str, **body: Any) -> Any:
    return client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json=body)


def _stale_sequence(
    client: TestClient, pid: str, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any]:
    """真的把那个序列跑一遍：

        第一轮（`run_id=a`）跑完 → 第二轮（`run_id=b`）开始 → 上一轮那颗「停」才到达。

    返回 `(停止请求的响应, 第二轮的响应)`。
    """
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        # 第一轮**不许**卡住：它必须先跑完，这才是「过期」的定义。
        if entered.is_set():
            return
        entered.set()
        release.wait(timeout=5)

    chat_id = open_chat(client, pid)
    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好")))
    first = _run_turn(client, pid, chat_id, chapter=2, said="第一轮", run_id="a")
    assert first.status_code == 200, first.text
    assert first.json()["reason"] == StopReason.DONE.value, "第一轮本来就该跑完"

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=blocking))
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            lambda: _run_turn(client, pid, chat_id, chapter=2, said="第二轮", run_id="b")
        )
        assert entered.wait(timeout=5), "第二轮没跑起来"
        stale = client.post(f"/api/projects/{pid}/chats/{chat_id}/stop", json={"run_id": "a"})
        release.set()
        second = running.result(timeout=10)
    return stale, second


def test_a_stop_that_arrives_after_the_next_turn_started_does_not_kill_it(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**新的那一轮没被杀掉。**

    作者按的那一下是给上一轮的；不比对的话他看到的是「我刚发出去的那句话，它自己停了」。
    这一档也**不许说成失败**——说成失败他会再按一次，而再按一次正好停掉新的那一轮。
    """
    stale, second = _stale_sequence(client, book["pid"], monkeypatch)

    assert stale.status_code == 200, stale.text
    assert stale.json()["stopped"] is False
    assert second.status_code == 200, second.text
    assert second.json()["reason"] == StopReason.DONE.value, "新的那一轮被那颗停杀掉了"
    assert second.json()["lookups"] == 1, "工具没跑 —— 它在中途就被停了"


def test_the_comparison_is_what_saves_the_new_turn(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**自守卫：把比对拆掉，上面那条当场红。**

    这里把 `_Running.stop` 换成 2026-08-12 之前那一版（不看是哪一轮，见谁停谁），
    序列一个字不改 —— 新的那一轮必须死在 `AUTHOR_STOPPED` 上。
    """
    original = chat_mod._Running.stop

    def blind(self: Any, key: tuple[str, str], run_id: str = "") -> Any:
        return original(self, key, "")

    monkeypatch.setattr(chat_mod._Running, "stop", blind)
    stale, second = _stale_sequence(client, book["pid"], monkeypatch)

    assert stale.json()["stopped"] is True, "自守卫本身没生效"
    assert second.json()["reason"] == StopReason.AUTHOR_STOPPED.value, (
        "不比对的那一版居然也没杀掉新的一轮 —— 那说明这条序列没造对"
    )
