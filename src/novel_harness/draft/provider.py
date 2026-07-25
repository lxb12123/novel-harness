"""统一的模型出口 —— **OpenAI 兼容协议**(`base_url` + `api_key` + `model`),开源闭源同一套。

作者选的是「通用格式」:一份代码接得了本地开源(Ollama / vLLM / LM Studio)、闭源直连
(OpenAI / DeepSeek / Kimi / 智谱 GLM),以及中转/聚合(OpenRouter,能转 Claude 等)。
换模型 = 换三个环境变量(`NH_LLM_BASE_URL` / `NH_LLM_MODEL` / `NH_LLM_API_KEY`),不改代码。

── 为什么参数集中在一处(load-bearing) ──────────────────────────────────
M2 kill-gate 的三臂 X0/X1/X2 和产品起草**共用这一个 `complete()`**:它们只在「往 prompt 里
塞什么」上不同,`model` / `temperature` / `max_tokens` 全从同一个 `ProviderConfig` 来。若哪一处
自己另起一个调用、另设一套参数,gate 测的就不再是产品会发的东西(EVAL_PROTOCOL.md §2）。
所以调用参数只在这里定义一次。

**用 OpenAI 兼容而非 Anthropic 原生 SDK 是作者的显式决定**:通用性 > Claude 原生的
thinking / prompt-caching 小功能(起草 prose 用不上;Claude 仍可经 OpenRouter / 兼容端点接入）。
别「顺手」换回 anthropic 原生 SDK —— 那会把这一层锁死在单一闭源供应商上。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

DEFAULT_MODEL = "claude-opus-4-8"
"""缺省模型。空 base_url 时指向 OpenAI 官方(那儿没有这个名),所以实际用时应由
`NH_LLM_MODEL` + `NH_LLM_BASE_URL` 一起指定成匹配的一对(如 deepseek-chat + deepseek 的 v1,
或 claude-opus-4-8 + OpenRouter 的 v1)。"""


class ProviderError(RuntimeError):
    """模型调用失败(网络、鉴权、供应商 4xx/5xx)统一收敛成这一个,调用方不必认识 openai 的异常类型。"""


class ProviderConfig(BaseModel):
    """一次调用的全部旋钮。**frozen**:kill-gate 里冻结它,三臂逐字节共用同一份参数。"""

    model_config = ConfigDict(frozen=True)

    model: str = DEFAULT_MODEL
    base_url: str | None = None
    """None = OpenAI 官方端点;本地 Ollama 填 http://localhost:11434/v1;中转填 OpenRouter 等。"""
    api_key: str = ""
    """本地 Ollama 之类不校验 key,可随便填(如 "ollama");闭源填真实 key。"""
    temperature: float | None = 0.7
    """None = **不发** temperature 字段 —— 少数端点(如 Anthropic 的 OpenAI 兼容端点对某些模型)
    会拒非默认采样参数,那时设 None 让它走供应商默认。"""
    max_tokens: int = 4096
    timeout: float = 600.0

    @classmethod
    def from_env(cls) -> ProviderConfig:
        """从 NH_LLM_* 读配置。作者(非程序员)只需在启动前填这几个,不碰代码。"""
        base = os.environ.get("NH_LLM_BASE_URL", "").strip()
        temp_raw = os.environ.get("NH_LLM_TEMPERATURE")
        if temp_raw is None:
            temperature: float | None = 0.7
        elif temp_raw.strip().lower() in ("", "none", "omit"):
            temperature = None  # 显式让调用不带 temperature
        else:
            temperature = float(temp_raw)
        return cls(
            model=os.environ.get("NH_LLM_MODEL", DEFAULT_MODEL),
            base_url=base or None,
            api_key=os.environ.get("NH_LLM_API_KEY", ""),
            temperature=temperature,
            max_tokens=int(os.environ.get("NH_LLM_MAX_TOKENS", "4096")),
        )


class CompletionResult(BaseModel):
    """一次调用的结果。文本 + 溯源(哪个模型、为什么停、花了多少 token)。frozen。"""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _build_client(config: ProviderConfig) -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise ProviderError(
            "缺少 openai 客户端库(统一走 OpenAI 兼容协议)。请 `uv add openai` 后重试。"
        ) from exc
    kwargs: dict[str, Any] = {
        # 端点可能不校验 key(本地 Ollama),但 openai 客户端构造时要求非空 —— 给个占位。
        "api_key": config.api_key or "not-needed",
        "timeout": config.timeout,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OpenAI(**kwargs)


def complete(
    messages: Sequence[dict[str, Any]],
    *,
    config: ProviderConfig | None = None,
    client: Any = None,
) -> CompletionResult:
    """一次非流式补全。kill-gate 的 runner 走这条(三臂各自把 messages 换成 X0/X1/X2)。

    Args:
        messages: OpenAI 兼容的 `[{"role": "system"/"user"/"assistant", "content": ...}]`。
        config: None = 从 `NH_LLM_*` 环境变量读(`ProviderConfig.from_env`)。
        client: 注入一个鸭子类型的 OpenAI 客户端(测试用);None = 按 config 现建一个真的。

    Raises:
        ProviderError: 调用失败(网络/鉴权/供应商错误),统一收敛,不外泄 openai 的异常类型。
    """
    config = config or ProviderConfig.from_env()
    client = client or _build_client(config)

    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": list(messages),
        "max_tokens": config.max_tokens,
    }
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature

    try:
        resp = client.chat.completions.create(**kwargs)
    except ProviderError:
        raise
    except Exception as exc:  # 供应商/网络异常五花八门,统一收口成 ProviderError
        raise ProviderError(
            f"模型调用失败(model={config.model}, base_url={config.base_url}):{exc}"
        ) from exc

    choice = resp.choices[0]
    usage = getattr(resp, "usage", None)
    return CompletionResult(
        text=getattr(choice.message, "content", None) or "",
        model=getattr(resp, "model", config.model),
        finish_reason=getattr(choice, "finish_reason", None),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )
