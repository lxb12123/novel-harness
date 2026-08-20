"""没登记的路由，去问端点自己 —— `resolve_capabilities` 那个 `metadata` 口子的第一个走法。

## 这一层解决什么

作者在设置页填什么是**他的自由**（`capabilities.py` 只认注册表里那 8 条精确路由）。
填了别的 ⇒ `resolve_capabilities` 返回 fail-closed 的 `unknown` ⇒ 三样东西一起哑掉：

| 字段 | `unknown` 的值 | 后果 |
|---|---|---|
| `max_context_tokens` | `None` | **逐字上文从 40,000 字塌回 800**，记忆层也倒推不出预算 |
| `max_output_tokens` | `None` | 少一道「发出去必被拒」的预检 |

⚠️ **2026-08-13 这张表短了一半**：原来头两行是 `supports_streaming` / `supports_stream_usage`
（「停」和「边写边看」在自选端点上一起哑掉）。**那两位已经从能力表里删掉了**——
`stream` 是 OpenAI 兼容协议的基本功能，不需要逐条登记；用量则是无条件要、端点不给就落
`None`。于是本模块的收益只剩上面这两个数字，**比它落地那天小得多**。

**注册表不许凭空登记没查证过的东西**，这条立场是对的、不动。但「没查证过」不等于
「查不到」——有的中转**自己把能力表发布成 API**。这个模块就是去读那份 API。

## 铁律：登记过的路由永远走注册表

`resolve_capabilities` 的顺序是 `operator override → 注册表 → metadata → unknown`，
所以**已登记的 8 条路由碰都不会碰这里**：M2 判分链、gate、作者直连 DeepSeek /
OpenAI 的那几条，wire shape 一个字节不变。这个模块只可能影响「本来就是 unknown」的那些。

## 只对 OpenRouter 成立，且是**有意**只做一家

判据是「这一家有没有把能力发布成可读的 API」，不是「这一家红不红」。今天只有
OpenRouter 满足（`/api/v1/models/{slug}/endpoints`，公开、免鉴权、按上游逐条列出）。
再多一家就在 `_DISCOVERERS` 上加一条，**别在这儿写第二套 HTTP**。

## 不锁模型（2026-08-13 的产品裁定）

作者选哪个 slug 是他的事，所以这里**按 slug 现问**，不预先登记某几个模型。
代价是一次网络往返，见下面的缓存。

## 同一个 slug 后面站着一堆上游，所以数字取**下确界**

OpenRouter 会在多家上游之间替你路由（`deepseek/deepseek-v4-flash` 实测 19 家）。
它们的上限差得离谱：

    输出上限   Venice 32,768 · DeepInfra 65,536 · 官方 DeepSeek 384,000
    缓存读价   官方 $0.0028/M · 第三方 $0.017–0.07/M（差 6–25 倍）

**我们不发锁上游的字段**（那是另一次改动），所以任何一次请求都可能落到最小的那家。
于是这里对上下文/输出取 `min`，对 `supported_parameters` 取**交集**——
「任何一家都扛得住」才算这条路由扛得住。这是唯一 fail-closed 的读法。

⚠️ **取 `min` 的代价要说清**：作者锁了官方、能用 384,000 的那一档，我们照样只给他
最小那家的数。那不是 bug，是「我们不知道这一次会落到谁头上」的诚实表达。
要拿回那个大数，得先让产品发得出锁上游的字段。

## 推理档位为什么只能这么写

`ProviderCapabilities._is_coherent` 有两条互锁的规矩：

1. `dialect=NONE` 不许声明任何非 OFF 档；
2. 非 NONE 的方言**必须**声明至少一个非 OFF 档。

而 OFF 在 `NONE` 方言下**一个字段都不发**——实测 `deepseek/deepseek-v4-flash` 过
OpenRouter **默认就在思考**（一次 raw 调用回来 `reasoning_tokens=8`）。
照 `NONE` 登记的话，「关了思考」是句空话：思考的 token 照旧从输出预算里扣，
而我们又声明了 `reasoning_shares_output=False` ⇒ 预算算少了 ⇒ **稿子被截断**。

所以方言必须是 `OPENROUTER`（OFF 会真的发 `{"reasoning": {"effort": "none",
"exclude": true}}`），那就得声明非 OFF 档。而 `reserve_ratio_high`（思考吃掉多少
输出预算）**问不出来**——它只能靠实跑长稿测右尾（`M2_ENDPOINT_PROFILE.md` 那种）。
于是：

    reasoning_shares_output = True    ← 保守方向：多留预算不会写坏，少留会截断
    reserve_ratio_high      = None    ← 没实测过，不许编

`plan_call` 因此在**非 OFF 档**上抛 `CapabilityError`（"shares output but has no
audited reserve ratio"）。这正是今天 `unknown` 的行为，只是**理由从「这个档不支持」
变成了「这个档的预算比例没实测过」**——后者说的是真话。

**受影响的只有 `/draft`**（它写死 `ReasoningEffort.HIGH`）。起草工具走 `OFF`，
聊天走 `OFF`，两条都活。

## 缓存与失败

- **进程内缓存 + TTL**：同一个 slug 在 TTL 内只问一次。作者一晚上写十章，网络往返一次。
- **任何失败都退回 `None`**（超时 / 404 / JSON 变形 / 字段缺失），调用方拿到的就是
  今天的 `unknown`。**这个模块不许抛**：一次问不到能力，不该让作者连稿都起不了。
- **不带钥匙**：那个端点是公开的。带上反而是把凭证发给一个不需要它的地方。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import quote, urlsplit

from .capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    normalize_base_url,
    normalize_model,
    resolve_capabilities,
)
from .windows import capabilities_from_snapshot

OPENROUTER_HOST: Final = "openrouter.ai"
OPENROUTER_ENDPOINTS_URL: Final = "https://openrouter.ai/api/v1/models/{slug}/endpoints"
"""按 slug 列出所有上游。公开、免鉴权（实测 2026-08-13）。"""

CACHE_TTL_SECONDS: Final = 3_600.0
FETCH_TIMEOUT_SECONDS: Final = 5.0

#: 一次 GET 的返回类型。测试注入假的这一层，**不许在测试里真发请求**。
Fetcher = Callable[[str], Any]

_cache: dict[tuple[str, str], tuple[float, ProviderCapabilities | None]] = {}


def _http_get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _positive(value: object) -> int | None:
    """一个上限数字，读得懂才算数。读不懂返回 `None`（**不是 0**，同 `_usage_count`）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _floor(values: Iterable[int | None]) -> int | None:
    """一堆上游报的上限里取下确界。**全都读不懂就是 `None`**——不知道，不是没有。"""
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _shared_parameters(endpoints: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """所有上游都支持的参数（交集）。**任何一家不支持就不算支持**——我们不锁上游。"""
    sets: list[set[str]] = []
    for endpoint in endpoints:
        raw = endpoint.get("supported_parameters")
        sets.append({item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set())
    if not sets:
        return frozenset()
    return frozenset(set.intersection(*sets))


def _openrouter_capabilities(
    base_url: str, model: str, fetch: Fetcher
) -> ProviderCapabilities | None:
    """把 `/models/{slug}/endpoints` 的真响应翻成一份 `ProviderCapabilities`。"""
    url = OPENROUTER_ENDPOINTS_URL.format(slug=quote(model, safe="/"))
    payload = fetch(url)
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    raw_endpoints = data.get("endpoints")
    if not isinstance(raw_endpoints, list):
        return None
    endpoints = [item for item in raw_endpoints if isinstance(item, Mapping)]
    if not endpoints:
        # 这个 slug 一个上游都没有 ⇒ 它今天发过去就是 404。别替它编一份能力。
        return None

    context = _floor(_positive(item.get("context_length")) for item in endpoints)
    output = _floor(_positive(item.get("max_completion_tokens")) for item in endpoints)
    if context is not None and output is not None:
        # 校验器不许 output > context；真出现这种数据时，**信小的那个**。
        output = min(output, context)

    shared = _shared_parameters(endpoints)
    reasons = shared & {"reasoning", "reasoning_effort"}
    if reasons:
        # 见模块 docstring：方言必须是 OPENROUTER（否则 OFF 关不掉思考），
        # 于是必须声明一个非 OFF 档；而它在 plan 时会被 reserve_ratio 那道闸拦下。
        levels = frozenset(ReasoningEffort)
        dialect = ReasoningDialect.OPENROUTER
        shares = True
    else:
        levels = frozenset({ReasoningEffort.OFF})
        dialect = ReasoningDialect.NONE
        shares = False

    try:
        return ProviderCapabilities(
            base_url=base_url,
            model=model,
            source="endpoint:openrouter-models-endpoints",
            source_urls=(url,),
            max_context_tokens=context,
            max_output_tokens=output,
            max_tokens_field="max_tokens",
            max_tokens_field_source="declared" if "max_tokens" in shared else "compat_default",
            reasoning_levels=levels,
            reasoning_dialect=dialect,
            reasoning_shares_output=shares,
            reserve_ratio_high=None,
        )
    except (ValueError, CapabilityError):
        # 校验器不收 ⇒ 这份数据自相矛盾 ⇒ 当作没问到。**绝不降级成半份能力**。
        return None


_DISCOVERERS: Final[Mapping[str, Callable[[str, str, Fetcher], ProviderCapabilities | None]]] = {
    OPENROUTER_HOST: _openrouter_capabilities,
}


def discover(
    base_url: str,
    model: str,
    *,
    fetch: Fetcher | None = None,
    now: Callable[[], float] = time.monotonic,
) -> ProviderCapabilities | None:
    """问一次这条路由的能力。**问不到返回 `None`，永不抛。**

    Args:
        fetch: 注入点。**测试必须传它**——这个函数默认会真的发 HTTP。
    """
    try:
        route = normalize_base_url(base_url), normalize_model(model)
    except CapabilityError:
        return None
    host = urlsplit(route[0]).hostname or ""
    discoverer = _DISCOVERERS.get(host.lower())
    if discoverer is None:
        return None

    cached = _cache.get(route)
    if cached is not None and now() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        found = discoverer(route[0], route[1], fetch or _http_get_json)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, KeyError):
        # **失败也进缓存**：一个打不通的端点不该让作者每起一次草都等 5 秒超时。
        found = None
    _cache[route] = (now(), found)
    return found


def resolve_with_discovery(
    base_url: str,
    model: str,
    *,
    fetch: Fetcher | None = None,
) -> ProviderCapabilities:
    """注册表优先；没登记的才去问端点自己。**这是装配层该调的那一个。**

    登记过的路由**一次网络都不发**——顺序在这儿就短路了，不是靠
    `resolve_capabilities` 内部的优先级。
    """
    try:
        route = normalize_base_url(base_url), normalize_model(model)
    except CapabilityError:
        # 连路由都不合法：交给 `resolve_capabilities` 去抛它自己那份错，
        # **别在这儿造第二种错误话术**。
        return resolve_capabilities(base_url, model)
    if route in CAPABILITY_REGISTRY:
        return resolve_capabilities(base_url, model)
    # **顺序是硬的**：端点自己发布的能力 > 打包的公共快照。前者是这条路由此刻的实况
    # （按上游逐条列），后者是一份社区维护、可能滞后一代的表（见 `windows.py` 边界一）。
    metadata = discover(*route, fetch=fetch) or capabilities_from_snapshot(*route)
    return resolve_capabilities(base_url, model, metadata=metadata)


def clear_cache() -> None:
    """丢掉进程内缓存。给测试和「作者刚在设置页换了模型」用。"""
    _cache.clear()


__all__ = [
    "CACHE_TTL_SECONDS",
    "OPENROUTER_ENDPOINTS_URL",
    "clear_cache",
    "discover",
    "resolve_with_discovery",
]
