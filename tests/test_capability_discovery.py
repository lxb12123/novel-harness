"""对抗性验证：「去问端点自己」这条路，会不会把假的能力放进来。

这个模块是本仓**第一次让能力表以外的东西决定预算和流式**，所以判据不是「解析对了」，
而是**「它拒绝得够不够狠」**：一份读不懂的响应必须原样退回今天的 `unknown`，
绝不许降级成半份能力——半份能力比没有能力危险得多，因为它会通过 `plan_call`。

六条网：

1. **登记过的路由一次网都不发**（M2 判分链、直连 DeepSeek/OpenAI 都在这一档）
2. **数字取下确界**，而且是逐字段取——不是「挑一家全抄」
3. **烂响应一律退回 unknown**（少字段 / 类型不对 / 空上游 / 自相矛盾）
4. **推理那两条互锁规矩**没被绕过（方言 ↔ 非 OFF 档 ↔ shares_output）
5. **缓存真的省掉第二次请求**，且失败也进缓存（否则每起一稿等一次超时）
6. **别家一律不问**（判据是 host，不是「看起来像不像中转」）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from novel_harness.draft import discovery, windows
from novel_harness.draft.capabilities import (
    CAPABILITY_REGISTRY,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
)
from novel_harness.draft.discovery import discover, resolve_with_discovery
from novel_harness.draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage

OPENROUTER = "https://openrouter.ai/api/v1"
SLUG = "deepseek/deepseek-v4-flash"
ZH = DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)

#: 一份**按真响应裁剪**的载荷（2026-08-13 实测 19 家里挑 3 家，字段名一字未改）。
#: 三家故意各带一种极端：上下文最小 / 输出最小 / 参数集最窄。
REAL_SHAPE: dict[str, Any] = {
    "data": {
        "id": SLUG,
        "endpoints": [
            {
                "provider_name": "DeepSeek",
                "context_length": 1_048_576,
                "max_completion_tokens": 384_000,
                "supported_parameters": ["max_tokens", "temperature", "reasoning_effort"],
            },
            {
                "provider_name": "Cloudflare",
                "context_length": 384_000,
                "max_completion_tokens": 384_000,
                "supported_parameters": ["max_tokens", "temperature", "reasoning_effort"],
            },
            {
                "provider_name": "Venice",
                "context_length": 1_000_000,
                "max_completion_tokens": 32_768,
                "supported_parameters": ["max_tokens", "reasoning_effort", "top_k"],
            },
        ],
    }
}


def _fetch(payload: Any, *, seen: list[str] | None = None) -> discovery.Fetcher:
    def fetch(url: str) -> Any:
        if seen is not None:
            seen.append(url)
        if isinstance(payload, Exception):
            raise payload
        return payload

    return fetch


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    discovery.clear_cache()


# ══════════════════════════════════════════════════════════════════════════
# 1. 登记过的路由一次网都不发
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("route", sorted(CAPABILITY_REGISTRY))
def test_registered_routes_never_touch_the_wire(route: tuple[str, str]) -> None:
    """8 条精确路由全部短路。

    **这条不是性能测试，是隔离测试**：M2 判分链和 `nh gate` 走的就是这些路由，
    它们发出去的东西必须逐字节不变（EVAL_PROTOCOL §2）。只要这里漏一条，
    那条路的能力就可能被一个第三方 API 的返回值改写 —— 而那是「考卷被外部改动」。
    """
    seen: list[str] = []
    capability = resolve_with_discovery(*route, fetch=_fetch(REAL_SHAPE, seen=seen))
    assert seen == [], f"{route} 走了发现逻辑"
    assert capability is CAPABILITY_REGISTRY[route]


def test_an_unregistered_route_is_the_only_thing_that_asks() -> None:
    seen: list[str] = []
    resolve_with_discovery(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE, seen=seen))
    assert seen == [discovery.OPENROUTER_ENDPOINTS_URL.format(slug=SLUG)]


# ══════════════════════════════════════════════════════════════════════════
# 2. 数字取下确界 —— 逐字段取，不是挑一家全抄
# ══════════════════════════════════════════════════════════════════════════


def test_limits_are_the_floor_across_upstreams_field_by_field() -> None:
    """上下文取 Cloudflare 那家、输出取 Venice 那家 —— **两个数来自不同的上游**。

    这条测试真正防的是「挑一家最保守的全抄」那种实现：那样写的话，两个数会一起来自
    同一行，而真实情况是没有哪一家在所有维度上都最小。抄错的后果是**高估**
    （比如抄了 Cloudflare 的 384,000 输出上限），而路由器随时可能把这一次请求
    发给只扛得住 32,768 的那家。
    """
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE))
    assert capability is not None
    assert capability.max_context_tokens == 384_000  # Cloudflare
    assert capability.max_output_tokens == 32_768  # Venice
    assert capability.source == "endpoint:openrouter-models-endpoints"


def test_an_upstream_that_declares_no_ceiling_does_not_erase_the_others() -> None:
    """有的上游 `max_completion_tokens` 是 `null`。**「没说」不等于「没有上限」**，
    但也不该让整条路由退回「不知道」——那会把另外两家报出来的真数字一起扔掉。
    读得懂的那些里取最小。"""
    payload = {"data": {"endpoints": [dict(item) for item in REAL_SHAPE["data"]["endpoints"]]}}
    payload["data"]["endpoints"][2]["max_completion_tokens"] = None
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(payload))
    assert capability is not None
    assert capability.max_output_tokens == 384_000


def test_supported_parameters_are_intersected_not_unioned() -> None:
    """`top_k` 只有 Venice 支持 ⇒ 不算这条路由支持。**我们不锁上游，所以判据是「每一家都行」**。"""
    payload = {"data": {"endpoints": [dict(item) for item in REAL_SHAPE["data"]["endpoints"]]}}
    payload["data"]["endpoints"][0]["supported_parameters"] = ["max_tokens"]
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(payload))
    assert capability is not None
    # 有一家不支持 reasoning_effort ⇒ 整条路由退回只有 OFF 的那一档
    assert capability.reasoning_levels == frozenset({ReasoningEffort.OFF})
    assert capability.reasoning_dialect is ReasoningDialect.NONE
    assert capability.reasoning_shares_output is False


# ══════════════════════════════════════════════════════════════════════════
# 3. 烂响应一律退回 unknown —— 半份能力比没有能力危险
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="没有 data"),
        pytest.param({"data": {}}, id="没有 endpoints"),
        pytest.param({"data": {"endpoints": []}}, id="一个上游都没有"),
        pytest.param({"data": {"endpoints": "nope"}}, id="endpoints 不是列表"),
        pytest.param({"data": [1, 2]}, id="data 不是对象"),
        pytest.param("<html>502</html>", id="根本不是 JSON 对象"),
        pytest.param(TimeoutError("slow"), id="超时"),
        pytest.param(ValueError("bad json"), id="JSON 坏了"),
    ],
)
def test_a_response_we_cannot_read_falls_back_to_unknown(payload: Any) -> None:
    """**判据是「退回 unknown」，不是「不抛异常」。**

    最危险的失败不是崩，是造出一份「上下文不知道、但流式支持」的半份能力：
    它能通过 `plan_call`，于是作者的稿子按一个我们编的预算发出去。
    """
    assert discover(OPENROUTER, SLUG, fetch=_fetch(payload)) is None

    # **退到下一层，不是退到「我们编一个」。** 2026-08-13 起下面还垫着一层打包快照
    # （`draft/windows.py`），所以这里的判据不再是「一定是 unknown」，
    # 而是「**绝不冒充成端点亲口说的**」—— `endpoint:` 那个前缀是这条网真正要防的东西。
    capability = resolve_with_discovery(OPENROUTER, SLUG, fetch=_fetch(payload))
    assert not capability.source.startswith("endpoint:")
    assert capability.source in {"unknown", "snapshot:litellm-model-windows"}
    # 无论落到哪一层，**输出上限一律没有** —— 那一列谁都没资格替这条路由声称。
    assert capability.max_output_tokens is None


def test_self_contradictory_numbers_are_refused_whole() -> None:
    """输出上限大于上下文 —— 校验器会拒。这时**整份作废**，不许挑能用的字段留下。"""
    payload = {
        "data": {
            "endpoints": [
                {
                    "context_length": 8_192,
                    "max_completion_tokens": 100_000,
                    "supported_parameters": ["max_tokens"],
                }
            ]
        }
    }
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(payload))
    # 实现会把 output 夹到 context 以内 —— 那是「信小的那个」，不是编。
    assert capability is not None
    assert (capability.max_context_tokens, capability.max_output_tokens) == (8_192, 8_192)


# ══════════════════════════════════════════════════════════════════════════
# 4. 推理那两条互锁规矩 —— 也是这个模块最容易写错的地方
# ══════════════════════════════════════════════════════════════════════════


def test_reasoning_off_must_really_switch_thinking_off() -> None:
    """**这条是整个模块最贵的一条，理由写在 `discovery.py` 的 docstring 里。**

    `dialect=NONE` 时 OFF 档一个字段都不发，而实测这些模型**默认就在思考**
    ⇒ 思考的 token 照旧从输出预算里扣，而 `NONE` 又必须配 `shares_output=False`
    ⇒ 预算算少了 ⇒ **稿子被截断**。所以认出推理能力时方言必须是 OPENROUTER。
    """
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE))
    assert capability is not None
    assert capability.reasoning_dialect is ReasoningDialect.OPENROUTER
    assert capability.reasoning_shares_output is True

    from novel_harness.draft import provider as prov

    config = prov.ProviderConfig(base_url=OPENROUTER, model=SLUG, api_key="k")
    plan = plan_call(ZH, ReasoningEffort.OFF, capability, interruptible=True)
    wire = prov._wire_kwargs(config, plan, [{"role": "user", "content": "写第 89 章"}])
    assert wire["extra_body"]["reasoning"] == {"effort": "none", "exclude": True}, (
        "OFF 档没有真的把思考关掉 —— 预算会算少，稿子会被截断。"
    )


def test_a_reasoning_level_we_never_measured_is_refused_at_plan_time() -> None:
    """`reserve_ratio_high` 问不出来（只能实跑长稿测右尾），所以非 OFF 档必须在
    `plan_call` 上被拒。**理由要说的是「没实测过预算比例」，不是「不支持这个档」**
    —— 后者是假话，端点自己声明了支持。"""
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE))
    assert capability is not None
    assert ReasoningEffort.HIGH in capability.reasoning_levels
    assert capability.reserve_ratio_high is None
    with pytest.raises(Exception, match="no audited reserve ratio"):
        plan_call(ZH, ReasoningEffort.HIGH, capability)


def test_what_this_module_is_still_worth_after_the_streaming_bits_left_the_table() -> None:
    """**这条替掉了 `test_the_draft_path_gets_its_three_dead_things_back`。**

    那一条说的是「没有本模块 ⇒ `supports_streaming=None` ⇒ 可中断起草不流式 ⇒
    停按钮 / 边写边看 / 账 三样一起死」。**那三样里有两样已经不归这个模块管了**：
    2026-08-13 起 `supports_streaming` / `supports_stream_usage` 从能力表上删掉，
    流式按协议默认开、用量按「要不要可中断」发。

    **于是本模块的收益缩成了「那两个测不起的数字」**，比它落地那天小得多——
    写在这儿是因为一个模块的价值缩水了却没人回来改说明，正是本仓最常见的那种骗人文档。
    """
    from novel_harness.draft.assemble import product_tail_limit

    # 问不到时退到打包快照那一层（`draft/windows.py`，2026-08-13 起垫在下面）。
    # **本模块的独家价值因此又缩了一次**：快照给的是「这个模型名一般多大」，
    # 本模块给的是「这条路由此刻按上游逐条算下来多大」。
    fallback = resolve_with_discovery(OPENROUTER, SLUG, fetch=_fetch(TimeoutError()))
    assert fallback.source == "snapshot:litellm-model-windows"

    # **这一行不是样板，是「失败也进缓存」的直接后果**：同一条路由问第二次拿回的是
    # 缓存里的失败，换个 `fetch` 也叫不动它。第一版这条测试就红在这儿。
    discovery.clear_cache()
    found = resolve_with_discovery(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE))
    assert found.max_context_tokens == 384_000
    assert found.max_context_tokens < fallback.max_context_tokens, (
        "问到的那一份必须比快照更**保守** —— 它按 19 家上游取了下确界，"
        "而快照只知道『这个模型名一般多大』。反过来的话，取下确界那条就白算了。"
    )
    assert product_tail_limit(found.max_context_tokens, 7_024) > 800

    # 而流式这件事，两边现在**一样**——它已经不依赖这个模块了。
    assert plan_call(ZH, ReasoningEffort.OFF, fallback, interruptible=True).stream is True
    assert plan_call(ZH, ReasoningEffort.OFF, found, interruptible=True).stream is True


# ══════════════════════════════════════════════════════════════════════════
# 5. 缓存
# ══════════════════════════════════════════════════════════════════════════


def test_the_second_ask_does_not_go_out_again() -> None:
    seen: list[str] = []
    fetch = _fetch(REAL_SHAPE, seen=seen)
    for _ in range(3):
        assert discover(OPENROUTER, SLUG, fetch=fetch) is not None
    assert len(seen) == 1


def test_a_failure_is_cached_too() -> None:
    """**失败也进缓存**，否则一个打不通的端点会让作者每起一次草都干等一次超时。
    代价是「网络刚恢复」要等 TTL 或换一次模型 —— 换模型会换 key，缓存自然错开。"""
    seen: list[str] = []
    fetch = _fetch(TimeoutError(), seen=seen)
    assert discover(OPENROUTER, SLUG, fetch=fetch) is None
    assert discover(OPENROUTER, SLUG, fetch=fetch) is None
    assert len(seen) == 1


def test_the_cache_expires_on_its_own_clock() -> None:
    seen: list[str] = []
    fetch = _fetch(REAL_SHAPE, seen=seen)
    clock = [1_000.0]
    discover(OPENROUTER, SLUG, fetch=fetch, now=lambda: clock[0])
    clock[0] += discovery.CACHE_TTL_SECONDS + 1
    discover(OPENROUTER, SLUG, fetch=fetch, now=lambda: clock[0])
    assert len(seen) == 2


# ══════════════════════════════════════════════════════════════════════════
# 6. 别家一律不问 —— 判据是 host
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434/v1",
        "https://api.deepseek.com",
        "https://my-openrouter-mirror.example.com/api/v1",
        "https://openrouter.ai.evil.example.com/api/v1",
        "",
    ],
)
def test_other_hosts_are_never_asked(base_url: str) -> None:
    """**判据是 host 精确相等，不是「名字里有没有 openrouter」。**

    最后那个探针是重点：`openrouter.ai.evil.example.com` 用子串判据会命中，
    而那时我们会把一个陌生主机的返回值当成能力表 —— 它能改预算、能开流式。
    """
    seen: list[str] = []
    assert discover(base_url, SLUG, fetch=_fetch(REAL_SHAPE, seen=seen)) is None
    assert seen == []


def test_the_discovered_capability_is_a_pydantic_model_not_a_dict() -> None:
    """铁律 4 的同一条道理：出参是 Pydantic，别让一个 `dict` 混进能力表这条路。"""
    capability = discover(OPENROUTER, SLUG, fetch=_fetch(REAL_SHAPE))
    assert isinstance(capability, ProviderCapabilities)
    assert capability.route == (OPENROUTER, SLUG)


# ══════════════════════════════════════════════════════════════════════════
# 7. 装配层那两个入口 —— 这条路由真跑起来，上文到底有多长
# ══════════════════════════════════════════════════════════════════════════
#
# 上面每一条测的都是 `discover` / `resolve_with_discovery`（本模块自己的函数）。
# **但作者感觉得到的那个数不在这一层**：它在 `product_tail_limit`，而喂它的
# `max_context_tokens` 由**装配层**取——工作台那三条走 `api/deps.py`，
# 写作助手（模式二）走 `agent/model.py`，两处各自调 `resolve_with_discovery`。
#
# 2026-08-13 有人只调了 `resolve_capabilities`（**它按设计不查快照**）就报了一个
# 「上文被砍到 800」的 bug。那个函数确实答 unknown，而装配层那两个入口答 384,000。
# 这一节就是把「按生产路径问」和「按某一个中间函数问」的差别钉住：
# 判据落在**上文有多长**上，不是某个字段等于几。


def _product_tail_for(capability: ProviderCapabilities) -> int:
    """这份能力下，模式二起草那一次调用给得出多长的逐字上文（code point）。

    预留量**现算**（`plan_call` 走一遍起草那一档），不写一个 7,024 的字面量——
    那个数会随长度档漂，而漂掉之后这条断言仍然会绿，只是量的不再是同一件事。
    """
    from novel_harness.agent.drafting import AGENT_DRAFT_LENGTH, AGENT_DRAFT_REASONING
    from novel_harness.draft.assemble import product_tail_limit

    reserved = plan_call(
        AGENT_DRAFT_LENGTH, AGENT_DRAFT_REASONING, capability, interruptible=True
    ).request_token_budget
    return product_tail_limit(capability.max_context_tokens, reserved)


@pytest.fixture
def _packaged_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """在一个空的家目录下跑，且**两头都清那份快照缓存**。

    `windows._snapshot` 是模块级 `lru_cache`，而它读的是「作者的家目录」——
    在临时家目录下缓存出来的那一份会活到下一条测试里去（同 `discovery` 那条串味）。
    """
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    windows._snapshot.cache_clear()
    yield
    windows._snapshot.cache_clear()


@pytest.mark.parametrize("wire", ["端点答得上来", "网络不通"])
@pytest.mark.usefixtures("_packaged_snapshot")
def test_the_authors_real_route_gets_a_long_tail_through_both_assembly_entries(
    wire: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者 2026-08-13 真在用的那条路由，**两个装配入口都必须给出远大于 800 的上文**。

    ── 为什么「网络不通」那一档必须一起测 ────────────────────────────────────

    `discover()` 把**失败也进缓存**，而失败一律静默（这个模块不许抛）。所以
    「端点问不到 ⇒ 悄悄退回 800」是这条链上唯一不会有人发现的坏法——
    垫在下面的是打包快照（`draft/windows.py`），它认得这个 slug 的裸键。
    这一档要是断了，作者看到的只是「模型忽然变笨」。

    ── 为什么两个入口都点名 ──────────────────────────────────────────────

    它们是**两份各自调用 `resolve_with_discovery` 的代码**，不是一份：
    工作台的抽取/总结/`/draft` 走 `api/deps.py`，写作助手走 `agent/model.py`。
    只测一个，另一个哪天被改成裸的 `resolve_capabilities` 时这里照样绿。
    """
    # 覆盖 conftest 那道「不许真发请求」的网：这条测试要走完整的发现逻辑，
    # 而这两个装配入口都不收 `fetch=` 参数——唯一的注入点就是这个模块属性。
    payload: Any = REAL_SHAPE if wire == "端点答得上来" else TimeoutError()
    monkeypatch.setattr(discovery, "_http_get_json", _fetch(payload))
    discovery.clear_cache()

    from novel_harness.agent.model import agent_call_plan
    from novel_harness.api.deps import resolve_route_capabilities
    from novel_harness.draft.provider import ProviderConfig

    config = ProviderConfig(base_url=OPENROUTER, model=SLUG, api_key="k", temperature=None)

    shell = resolve_route_capabilities(config)
    agent_capability, _ = agent_call_plan(config)

    for label, capability in (("api/deps.py", shell), ("agent/model.py", agent_capability)):
        assert capability.source != "unknown", f"{label} 把这条路由判成了未知"
        assert _product_tail_for(capability) > 10_000, (
            f"{label} 给的逐字上文塌回了对照臂那一档（800）—— 症状是「模型忽然变笨」，"
            "屏幕上没有任何一处会红。"
        )
