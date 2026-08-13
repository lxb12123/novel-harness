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

顺序：`operator override → 本仓注册表 → 端点自己发布的能力 → 本快照 → unknown`。
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
（那一档优先级最高）。这跟「上文悄悄塌成 800 字」不是一回事：后者没有任何一处会红。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from .capabilities import (
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    normalize_base_url,
    normalize_model,
)

SNAPSHOT_PATH: Final = Path(__file__).with_name("model_windows.json")
SNAPSHOT_SCHEMA: Final = "nh-model-windows-v1"


@lru_cache(maxsize=1)
def _snapshot() -> tuple[dict[str, int], str]:
    """读那份快照。**读不出来就是空的**——这一层永不抛。

    快照坏掉/缺失的后果只是「退回今天的 unknown」，而让它抛等于一个数据文件
    能把整个起草弄挂。
    """
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, ""
    if not isinstance(payload, dict) or payload.get("schema") != SNAPSHOT_SCHEMA:
        return {}, ""
    raw = payload.get("windows")
    if not isinstance(raw, dict):
        return {}, ""
    windows = {
        name: value
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool)
        and value > 0
    }
    source = payload.get("source_url")
    return windows, source if isinstance(source, str) else ""


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
    windows, _ = _snapshot()
    for key in _keys(base_url, model):
        found = windows.get(key)
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
    _, source_url = _snapshot()
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


__all__ = ["SNAPSHOT_PATH", "capabilities_from_snapshot", "window_for"]
