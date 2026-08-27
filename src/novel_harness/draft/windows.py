"""模型名 → 上下文窗口，从一份打包进来的公共快照里查。

## 它补的是能力表里唯一测不起的那个数

`max_context_tokens` 决定**上文给作者 800 字还是 40,000 字**（`assemble.product_tail_limit`），
而那个函数自己写着「只许把上文变长，不许变短——变短了没人会发现，只会觉得模型忽然变笨」。

它测不起：要知道窗口多大得真发一个百万 token 的请求看它拒不拒，而有的端点**不拒绝、
直接静默截断** —— 那时测出个假数，还付了钱。

手抄也不是办法（抄一次，下个季度就旧）。所以走这个生态**已经有的**共享真相源：
`BerriAI/litellm` 的公共表，Aider 直接用它。快照由 `scripts/refresh_model_windows.py`
裁出来，**只有 `max_input_tokens` 一列**（输出上限有意不取，理由在那个脚本里）。

## 三条边界

### 一、它排在我们自己那张表**后面**

顺序：`作者手填 → 本仓注册表 → 端点自己发布的能力 → 本快照 → unknown`
（第一档就是 `capabilities_from_author`，走 `resolve_capabilities` 的 `operator_override`）。
本仓注册表是照官方文档一条条查证过的，而公共表是社区提 PR 维护的——
**实测它在输出上限那一维会滞后一整代**（`deepseek-v4` 写着 8,192，官方是 384,000）。
上下文窗口那一维九条全中，所以只信这一列，且只在前面几档都没答案时用。

### 二、**只认精确的键，而「键」有两种拼法**

这是这个模块唯一真正的风险点：窗口是**模型**的属性，而我们手上是一条
`(base_url, model)`。作者跑一个自建的、名字碰巧一样但量化过的小模型，
照公共表填就会**高估**，症状是请求被拒。

**主机名是第一道闸，也是唯一那道**：不在 `_HOST_PROVIDERS` 里（自建、私有网关、
没见过的中转）**一个键都不查**，直接退回 `unknown`。认识的主机才查两个精确的键：

1. 用主机名补出来的限定名（`api.moonshot.cn` + `kimi-k3` ⇒ `moonshot/kimi-k3`）
   —— 公共表给国内那几家一律带前缀，不补这一层它们一条都命中不了；
2. 作者填的那个名字本身（有的家在公共表里就是裸键）。

⚠️ **第一版把顺序写反了：先查裸名、主机只用来补前缀。** 那样 `localhost` 上一个
随手起名叫 `deepseek-chat` 的量化小模型会直接命中 131,072 —— 正是这条路径要防的事。

**别为了「多认几个」加小写归一、前缀剥离或近似匹配** —— 那正好会让
「自建的同名小模型」命中一个大窗口，而那是这个模块唯一会造成真实伤害的路径。

### 三、猜错的方向是**可见**的

高估 ⇒ prompt 过长 ⇒ 端点当场拒 ⇒ 作者看得见错误，而且设置页那个手填框能压过它
（那一档优先级最高，2026-08-13 真的接上了：`capabilities_from_author`）。
这跟「上文悄悄塌成 800 字」不是一回事：后者没有任何一处会红。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from ..prompt_terms import message
from .capabilities import (
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    normalize_base_url,
    normalize_model,
)
from .length import DraftLanguage

SNAPSHOT_PATH: Final = Path(__file__).with_name("model_windows.json")
SNAPSHOT_SCHEMA: Final = "nh-model-windows-v2"
"""v2 起多带一张单价表。**版本一变，作者手上那份 v1 就整份作废**（`_read` 的
schema 判据），退回包里带的 v2 —— 半新半旧地混用两份表是唯一不能接受的结局。"""
SOURCE_URL: Final = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
SOURCE_LICENSE: Final = "MIT (BerriAI/litellm)"


class Price(BaseModel):
    """一个模型的单价，**美元 / 每 token**（公共表就是这个量纲，不换算）。"""

    model_config = ConfigDict(frozen=True)

    input: float
    output: float
    cache_read: float | None = None
    """命中缓存的那部分输入按这个价。**`None` = 公共表没写**，那时按原价算 ——
    方向是**偏高**，而偏高的账单不会让人少付钱，偏低会。"""


class Snapshot(BaseModel):
    """当前生效的那份公共数据。"""

    model_config = ConfigDict(frozen=True)

    windows: dict[str, int] = {}
    prices: dict[str, Price] = {}
    source: str = ""


def _clean_prices(raw: object) -> dict[str, Price]:
    """把快照里的单价读成 `Price`。**读不懂的整条丢掉**，不许留半份。

    半份价格（有输入价没输出价）会算出一个**偏低**的账单，而偏低的账单
    正是「屏幕上一句关于钱的假话」——比不显示坏。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Price] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        try:
            out[name] = Price.model_validate(entry)
        except ValueError:
            continue
    return out


def user_snapshot_path() -> Path:
    """作者自己刷新出来的那一份。**和设置放同一个地方**（`settings.py` 的目录）。

    为什么不覆盖包里那份：装在 `site-packages` 里的东西是只读的，而且重装一次就没了。
    作者的数据该住在作者的目录里 —— 这跟钥匙、地址、模型放一起是同一条道理。
    """
    from ..settings import DEFAULT_PATH  # 局部导入：`draft/` 不该在模块层依赖装配层

    override = os.environ.get("NH_SETTINGS_PATH")
    home = Path(override).parent if override else DEFAULT_PATH.parent
    return home / "model_windows.json"


def _read(path: Path) -> Snapshot | None:
    """读一份快照。**读不出来返回 `None`**（不是空 dict —— 那两者要分得开）。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SNAPSHOT_SCHEMA:
        return None
    raw = payload.get("windows")
    if not isinstance(raw, dict):
        return None
    windows = {
        name: value
        for name, value in raw.items()
        if isinstance(name, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    }
    prices = _clean_prices(payload.get("prices"))
    source = payload.get("source_url")
    return Snapshot(windows=windows, prices=prices, source=source if isinstance(source, str) else "")


@lru_cache(maxsize=1)
def _snapshot() -> Snapshot:
    """当前生效的那份快照。**作者刷新出来的压过包里带的**。

    顺序的理由：包里那份是发版时冻的，作者点过「更新」就说明他明确要更新的那一份。
    两份都读不出来 ⇒ 空 ⇒ 退回今天的 `unknown`。**这一层永不抛**：
    一个数据文件不该有能力把起草弄挂。
    """
    return _read(user_snapshot_path()) or _read(SNAPSHOT_PATH) or Snapshot()


_HOST_PROVIDERS: Final[Mapping[str, str]] = {
    # 主机名 → 公共表里的 `litellm_provider`。**判据是主机名精确相等**（同 `discovery`
    # 那条：子串判据会让 `api.deepseek.com.evil.example.com` 命中）。
    # 这张表只该长在「作者真的会填这个地址」上，不是把上游列表抄一遍。
    "api.deepseek.com": "deepseek",
    "api.moonshot.cn": "moonshot",
    "api.moonshot.ai": "moonshot",
    "dashscope.aliyuncs.com": "dashscope",
    "dashscope-intl.aliyuncs.com": "dashscope",
    "api.mistral.ai": "mistral",
    "api.x.ai": "xai",
    "api.perplexity.ai": "perplexity",
    "openrouter.ai": "openrouter",
    "api.together.xyz": "together_ai",
    "api.fireworks.ai": "fireworks_ai",
    "api.deepinfra.com": "deepinfra",
    "api.novita.ai": "novita",
}


def _keys(base_url: str, model: str) -> tuple[str, ...]:
    """这条路由在公共表里可能的键。**主机名不认识就一个键都不给**（边界二）。

    ⚠️ **第一版这儿是「先查裸名，再查限定名」，那是个洞**：裸名那个键完全不看主机，
    于是 `localhost` 上一个随手起名叫 `deepseek-chat` 的量化小模型会命中公共表里
    131,072 的窗口 —— 正是这个模块唯一会造成真实伤害的那条路径。
    `test_a_lookalike_host_is_not_believed_either` 当场把它抓出来了。

    所以主机名是**两个键共同的闸**，不是只给限定名那个用的。
    """
    host = (urlsplit(base_url).hostname or "").lower()
    provider = _HOST_PROVIDERS.get(host)
    if provider is None:
        return ()
    name = model.strip()
    if name.startswith(f"{provider}/"):
        return (name,)
    return (f"{provider}/{name}", name)


def window_for(base_url: str, model: str) -> int | None:
    """这条路由在公共快照里的上下文窗口。**认不出返回 `None`。**"""
    snapshot = _snapshot()
    for key in _keys(base_url, model):
        found = snapshot.windows.get(key)
        if found is not None:
            return found
    return None


def price_for(base_url: str, model: str) -> Price | None:
    """这条路由的单价（美元/token）。**认不出返回 `None`。**

    走的是和窗口**完全同一道闸**（`_keys`：主机名不认识就一个键都不查）。
    对价格来说那道闸比对窗口还要紧：作者在自己机器上跑的模型**几乎不花钱**，
    照云端标价算出来的数字会离谱地高，而屏幕上那是一句关于他钱包的话。
    """
    snapshot = _snapshot()
    for key in _keys(base_url, model):
        found = snapshot.prices.get(key)
        if found is not None:
            return found
    return None


def capabilities_from_snapshot(base_url: str, model: str) -> ProviderCapabilities | None:
    """把快照里那一个数包成一份最小的能力，交给 `resolve_capabilities` 的 `metadata` 档。

    **除了上下文窗口，其余一律是最保守的那一档**：只有 OFF、方言 NONE、不共享输出预算。
    这不是「没写完」——快照只知道窗口这一件事，多声称一位就是编。
    """
    try:
        route = normalize_base_url(base_url), normalize_model(model)
    except CapabilityError:
        return None
    context = window_for(*route)
    if context is None:
        return None
    source_url = _snapshot().source
    if not source_url:
        return None  # 没有出处的能力不许存在（`_is_coherent` 也会拒）
    try:
        return ProviderCapabilities(
            base_url=route[0],
            model=route[1],
            source="snapshot:litellm-model-windows",
            source_urls=(source_url,),
            max_context_tokens=context,
            # **输出上限留空**：公共表那一列实测会滞后一整代（deepseek-v4 写 8,192，
            # 官方 384,000）。留空只是少一道预检，填错会把整章起草截成一小段。
            max_output_tokens=None,
            max_tokens_field="max_tokens",
            max_tokens_field_source="compat_default",
            reasoning_levels=frozenset({ReasoningEffort.OFF}),
            reasoning_dialect=ReasoningDialect.NONE,
            reasoning_shares_output=False,
            reserve_ratio_high=None,
        )
    except (ValueError, CapabilityError):  # pragma: no cover - 上面已把字段挑干净
        return None


AUTHOR_WINDOW_SOURCE: Final = "author:settings-context-window"
"""作者手填那一档的 `source`。**它不是 `unknown`**，而这不是随便起的名字：
`ProviderCapabilities._is_coherent` 规定「unknown 必须是那份规范的空壳」，
所以一旦填了窗口，这条路由在能力表眼里就**不再是未知的**（见下面函数的第三条）。"""


def capabilities_from_author(
    capability: ProviderCapabilities, context_window: int | None
) -> ProviderCapabilities | None:
    """把作者在设置页手填的窗口盖到已解析出的能力上。**没填返回 `None`。**

    这是边界三里那句「设置页那个手填框能压过它」的实现，也是这份解析顺序的**第一档**：

        作者手填 → 本仓注册表 → 端点自己发布的能力 → 本快照 → unknown

    它只可能被 `resolve_capabilities(..., operator_override=…)` 那个口子消费——
    **那一档本来就是为这件事留的，别为它另开第二条路径**（第二条路径意味着
    「谁压过谁」有两份答案，而这个数错了的症状是「模型忽然变笨」，没有任何一处会红）。

    三条边界：

    ### 一、除了窗口，**其余一位都不动**

    作者知道自己那台机器一次能吃多少，不代表他知道这个模型的推理方言、输出上限、
    或者思考要占多少预算。多改一位就是替他编。

    ### 二、输出上限跟着往下夹

    `_is_coherent` 不许 `max_output_tokens > max_context_tokens`。作者填的数比登记的
    输出上限还小时（比如给已登记的路由填 8,000），不夹就是一个 `ValidationError`，
    而作者会看到一句「模型没配好」——**他明明配好了，只是填了个小数**。
    夹的方向和 `discovery.py` 那条一样：**信小的那个**。

    ### 三、⚠️ 它顺带把这条路由从「未知」变成了「已知」

    这不是设计选择，是模型不变式逼出来的：带着窗口的 `unknown` 在 `_is_coherent` 那儿
    根本构造不出来。可见的后果是 `plan_structured_call` 那道
    「`source == "unknown"` 就拒」的闸对这条路由**不再拦**——也就是抽取和滚动总结
    在自建端点上从「一律 422」变成了「真的发出去试试」。方向是对的
    （那两条链本来就该在自建端点上能用），而且失败是**当场可见**的
    （JSON 解不开 = 报错，不是悄悄写坏），符合 ADR 0026「学得会的东西由运输层学」。
    **写在这儿是因为它是这个函数唯一一处「超出窗口本身」的影响**，
    `tests/test_author_context_window.py` 把它钉成了一条有意的行为。
    """
    if context_window is None or context_window < 1:
        return None
    output = capability.max_output_tokens
    if output is not None:
        output = min(output, context_window)
    values = capability.model_dump()
    values.update(
        source=AUTHOR_WINDOW_SOURCE,
        # **出处就是那条路由本身。** 这个数没有公开文档背书——背书它的是把那个端点
        # 跑起来的人。而 `_is_coherent` 要求非 unknown 的能力至少有一条 http(s) 出处，
        # 那时随手编一个文档地址塞进审计线索才是真的在骗人。
        source_urls=(capability.base_url,),
        max_context_tokens=context_window,
        max_output_tokens=output,
    )
    try:
        return ProviderCapabilities(**values)
    except (ValueError, CapabilityError):
        # 作者填的数把这份能力弄得自相矛盾 ⇒ 当作没填。**一个设置框不该有能力把起草弄挂。**
        return None


class RefreshReport(BaseModel):
    """点一次「更新」之后发生了什么。**没有这份回执，那颗按钮就是个不出声的按钮。**"""

    model_config = ConfigDict(frozen=True)

    fetched: str
    """这次拉取的日期（`YYYY-MM-DD`，由调用方给，见 `refresh`）。"""

    total: int
    """更新后一共认得多少个模型。"""

    added: int
    changed: int
    removed: int
    """和更新前那一份比，多了/变了/少了几个。**`changed` 才是最值得看的那个**——
    一个模型的窗口被上游改小了，作者的上文会跟着变短，而那件事没有别的观测点。"""

    path: str
    """写到哪儿去了（作者的配置目录，不是包里）。"""


def _rate(value: object) -> float | None:
    """一个单价，读得懂才算数。**0 是合法的**（免费档），负数和非数字不是。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value >= 0 else None


def trim(raw: object) -> tuple[dict[str, int], dict[str, Price]]:
    """把公共表裁成「模型名 → 窗口」和「模型名 → 单价」。**脚本和那颗按钮共用这一份。**

    两条过滤，理由都在 `scripts/refresh_model_windows.py` 的 docstring 里：
    只收 `mode == "chat"`（否则混进图片/嵌入/语音），只收读得懂的数。

    ⚠️ **窗口和单价是分开收的**：一个模型可能有窗口没价（自建/免费档），
    也可能有价没窗口。绑成一条会让「少一样就两样都丢」。

    ⚠️ **`output_cost_per_token` 是取的，而 `max_output_tokens` 不取** —— 看着矛盾，
    其实是两回事：**上限**那一列实测滞后一整代（deepseek-v4 写 8,192，官方 384,000），
    而**单价**那一列是这个表被维护得最勤的部分（它是 litellm 的主业）。
    """
    if not isinstance(raw, dict):
        return {}, {}
    windows: dict[str, int] = {}
    prices: dict[str, Price] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        if entry.get("mode") != "chat":
            continue
        value = entry.get("max_input_tokens")
        if not isinstance(value, bool) and isinstance(value, int) and value >= 1:
            windows[name] = value
        in_rate = _rate(entry.get("input_cost_per_token"))
        out_rate = _rate(entry.get("output_cost_per_token"))
        if in_rate is not None and out_rate is not None:
            prices[name] = Price(
                input=in_rate,
                output=out_rate,
                cache_read=_rate(entry.get("cache_read_input_token_cost")),
            )
    return windows, prices


def estimate_cost(
    base_url: str,
    model: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cache_read_tokens: int | None,
) -> float | None:
    """这一次调用**大概**花了多少美元。**算不出来返回 `None`，绝不返回 0。**

    ── 这是估算，不是账单 ──────────────────────────────────────────────────

    单价来自公共表的**标价**。作者可能有折扣、走中转、或者用的是免费额度，
    所以这个数**只配写成「约 ¥x」**，屏幕上必须带那个「约」字。
    真账单只有供应商自己给得出（OpenRouter 的响应里就带 `cost`，今天还没接）。

    ── 缓存那一段单独算，否则这个项目的账会系统性偏高 ──────────────────────

    一本 723 章的书每轮前缀几乎不变，命中率能到 98%。把命中的那部分按原价算，
    算出来的数能比真实高一个数量级 —— 而这个产品的成本故事整个押在前缀缓存上。

        cost = (输入 − 命中) × 输入价 + 命中 × 缓存价 + 输出 × 输出价

    公共表没写缓存价时按原价算（**偏高**）：偏高的账单不会让人少付钱，偏低会。

    ── 少一个数就整笔不算 ──────────────────────────────────────────────────

    供应商没报 token 数时（`None`），这里**不拿估算的 token 去凑**。
    `activity.py` 那条规矩是「账本只照抄」，而一笔用估算 token 乘出来的钱，
    在屏幕上和一笔真钱长得一模一样。
    """
    if prompt_tokens is None or completion_tokens is None:
        return None
    price = price_for(base_url, model)
    if price is None:
        return None
    cached = cache_read_tokens or 0
    fresh = max(0, prompt_tokens - cached)
    cache_rate = price.cache_read if price.cache_read is not None else price.input
    return fresh * price.input + cached * cache_rate + completion_tokens * price.output


def render(windows: dict[str, int], prices: dict[str, Price], *, fetched: str) -> str:
    """裁好的那两张表写成快照文件的内容。**脚本和按钮写出的字节完全一样。**"""
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "fetched": fetched,
        "source_url": SOURCE_URL,
        "source_license": SOURCE_LICENSE,
        "note": (
            "裁了两列：max_input_tokens（上下文窗口）和单价。**输出上限有意不取** —— "
            "实测 deepseek-v4 那一档公共表写的是 8,192，而官方文档是 384,000（差 47 倍）。"
            "单价那一列不同：它是这个表被维护得最勤的部分。"
        ),
        "windows": dict(sorted(windows.items())),
        "prices": {
            name: price.model_dump(exclude_none=True)
            for name, price in sorted(prices.items())
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def refresh(
    raw: object, *, fetched: str, language: DraftLanguage = DraftLanguage.ZH
) -> RefreshReport:
    """把一份刚下下来的公共表落成作者自己那份快照，并说出变了什么。

    Raises:
        ValueError: 裁完一个模型都不剩。**这时绝不覆盖旧的那份**——
            一次拉到半截的响应不该把作者手上能用的数据换成空的。
    """
    windows, prices = trim(raw)
    if not windows:
        raise ValueError(message("model_windows_refresh_empty", language))

    before = (_read(user_snapshot_path()) or _read(SNAPSHOT_PATH) or Snapshot()).windows
    target = user_snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(windows, prices, fetched=fetched), encoding="utf-8")
    _snapshot.cache_clear()

    return RefreshReport(
        fetched=fetched,
        total=len(windows),
        added=len(windows.keys() - before.keys()),
        changed=sum(
            1 for name, value in windows.items() if name in before and before[name] != value
        ),
        removed=len(before.keys() - windows.keys()),
        path=str(target),
    )


__all__ = [
    "AUTHOR_WINDOW_SOURCE",
    "SNAPSHOT_PATH",
    "SOURCE_URL",
    "Price",
    "RefreshReport",
    "capabilities_from_author",
    "capabilities_from_snapshot",
    "estimate_cost",
    "price_for",
    "refresh",
    "render",
    "trim",
    "user_snapshot_path",
    "window_for",
]
