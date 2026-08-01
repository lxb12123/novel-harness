"""统一的模型出口 —— **OpenAI 兼容协议**(`base_url` + `api_key` + `model`),开源闭源同一套。

作者选的是「通用格式」:一份代码接得了本地开源(Ollama / vLLM / LM Studio)、闭源直连
(OpenAI / DeepSeek / Kimi / 智谱 GLM),以及中转/聚合(OpenRouter,能转 Claude 等)。
换模型 = 换三个环境变量(`NH_LLM_BASE_URL` / `NH_LLM_MODEL` / `NH_LLM_API_KEY`),不改代码。

── 为什么参数集中在一处(load-bearing) ──────────────────────────────────
M2 kill-gate 的三臂 X0/X1/X2 和产品起草**共用这一个 `complete()`**:它们只在「往 prompt 里
塞什么」上不同;连接/采样来自同一个 `ProviderConfig`,输出/reasoning 来自同一个
已验证 `ResolvedCallPlan`。若哪一处
自己另起一个调用、另设一套参数,gate 测的就不再是产品会发的东西(EVAL_PROTOCOL.md §2）。
所以调用参数只在这里定义一次。

**用 OpenAI 兼容而非 Anthropic 原生 SDK 是作者的显式决定**:同一客户端可接
OpenAI / DeepSeek / Claude 兼容端点 / OpenRouter / 本地模型。这不等于放弃 thinking:
能力已确认的端点由适配层发它支持的 reasoning 字段,未知端点则失败关闭。

── 配置的自洽性在构造时检查,不留到发请求 ──────────────────────────────
`base_url` / `model` / `temperature` 三者不是独立旋钮,它们必须**互相匹配**。曾经的默认值是
一组自相矛盾的取值(`claude-opus-4-8` + 空 base_url 指向 OpenAI 官方 + `temperature=0.7`),
开箱即失败,而且失败发生在第一次真发请求的时候 —— 对 kill-gate 来说那是最坏的时机:
整轮跑到一半死掉,而不是启动时就红。所以 `ProviderConfig` 现在在**构造时**就拒绝不自洽的组合
(见 `_reject_self_contradictory_config`)。判据全是集合/前缀/主机名判断,不做语义判断。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import (
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    normalize_base_url,
    normalize_model,
)

DEFAULT_TEMPERATURE: float | None = None
"""缺省不发 temperature,使兼容层不猜模型的采样语义。"""

SAMPLING_STRICT_MODELS = frozenset(
    {
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
    }
)
"""**已核对**会对非默认采样参数返回 400 的模型(`docs/PLAN.md` 第 301 行那张表 + 同源文档)。

判据是集合成员,不是语义 —— 守 ADR 0005 的铁律。三点诚实说明:

① **这张表只拦已知的那几个。** 表外的模型一律放行,因为「这个模型收不收 temperature」
   没法从名字推出来。它是**已知错误配置的拦网**,不是完备性保证。
② **它可能误伤中转。** OpenRouter 之类的聚合端点转发这些模型时,往往会自己吞掉或翻译
   temperature,于是「claude-opus-4-8 + 0.7」在那儿其实跑得通。这里仍然硬拦,理由是
   PLAN.md 对这几个模型的结论本来就是「运行间方差是结构性的、无法消除」——
   项目已经放弃在它们上面用 temperature 了,拦住比放过更贴合既有决策。
   真要在中转上用 temperature,把 `NH_LLM_MODEL` 写成中转自己的别名(不落在这张表里)。
③ **`claude-opus-4-6` / `claude-sonnet-4-6` 不在表里**,它们是接受采样参数的 —— 别顺手加进来。
"""

_OPENAI_OFFICIAL_HOSTS = frozenset({"api.openai.com"})
"""OpenAI 官方端点的主机名。**唯一一个「端点 ↔ 模型」可判定的情形**在这儿用到。

一般来说「这个端点供不供这个模型」不可判定(任何中转都能转任何东西),所以那件事是使用者的
责任。但有一种组合是确定错的:官方 OpenAI 端点上不存在 `claude-*`。那正是这个文件原来的 bug。
"""


class ProviderError(RuntimeError):
    """模型调用失败(网络、鉴权、供应商 4xx/5xx)统一收敛成这一个,调用方不必认识 openai 的异常类型。"""


def _model_family(model: str) -> str:
    """把模型名归一到「表里那个名字」。

    中转会给模型加供应商前缀(OpenRouter 写作 `anthropic/claude-opus-4-8`)。不剥掉这层前缀,
    换个中转就能悄悄绕过 `SAMPLING_STRICT_MODELS` —— 一道绕得过去的守卫等于没有。
    """
    return model.strip().lower().rsplit("/", 1)[-1]


class ProviderConfig(BaseModel):
    """连接与采样参数。**frozen**:kill-gate 三臂逐字节共用同一份配置。

    Raises:
        pydantic.ValidationError: 构造时配置不自洽(缺 base_url、模型与温度打架、
            官方 OpenAI 端点配 Claude 模型)。**这是有意的**:错误配置要在启动时红,
            不能留到 kill-gate 跑到一半才 400。
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    model: str
    base_url: str
    """端点,**必填,没有缺省值**。本地 Ollama 填 http://localhost:11434/v1;中转填 OpenRouter 等。

    **这里曾经有个缺省语义「空 = OpenAI 官方」,它就是那个 bug 的来源** —— 默认模型是
    `claude-opus-4-8`,而 OpenAI 官方端点上没有这个名字,于是开箱即 404。
    「猜一个供应商」没有对的猜法,所以现在不猜:空值在构造时报错,并告诉作者去填
    `NH_LLM_BASE_URL`。要打 OpenAI 官方就显式写 https://api.openai.com/v1 。
    """
    api_key: str = Field(default="", exclude=True, repr=False)
    """本地 Ollama 之类不校验 key,可随便填(如 "ollama");闭源填真实 key。

    **故意不在构造时校验非空**:本地端点合法地不需要 key,而「哪些端点需要 key」不可判定。
    它是凭证缺失(运行时 401),不是配置自相矛盾 —— 两类问题别混在一个检查里。
    """
    temperature: float | None = DEFAULT_TEMPERATURE
    """None = **不发** temperature 字段,走供应商默认。见 `DEFAULT_TEMPERATURE` 与
    `SAMPLING_STRICT_MODELS`:对表里的模型,None 是唯一合法取值。"""
    timeout: float = 600.0

    @field_validator("model")
    @classmethod
    def _normalize_config_model(cls, value: str) -> str:
        return normalize_model(value) if value.strip() else value

    @field_validator("base_url")
    @classmethod
    def _normalize_config_base_url(cls, value: str) -> str:
        return normalize_base_url(value) if value.strip() else value

    @model_validator(mode="after")
    def _reject_self_contradictory_config(self) -> ProviderConfig:
        """三条集合判断。**全部只回答「这两个取值能不能同时成立」,不回答「这是什么意思」。**"""
        if not self.model.strip():
            raise ValueError("NH_LLM_MODEL 是空的:必须指定模型名(如 deepseek-chat)。")

        if not self.base_url.strip():
            raise ValueError(
                "NH_LLM_BASE_URL 是空的:必须显式指定模型端点,本层不替你猜供应商。\n"
                "例:本地 Ollama = http://localhost:11434/v1;"
                "DeepSeek = https://api.deepseek.com;OpenAI 官方 = https://api.openai.com/v1 。\n"
                f"它必须和 NH_LLM_MODEL(当前 {self.model!r})是**匹配的一对** —— "
                "端点上没有这个模型名,发出去就是 404。"
            )

        family = _model_family(self.model)

        if family in SAMPLING_STRICT_MODELS and self.temperature is not None:
            raise ValueError(
                f"模型 {self.model!r} 拒绝非默认 temperature/top_p/top_k(400),"
                f"但 temperature 被设成了 {self.temperature!r}。\n"
                "这一对不可能同时成立。二选一:把 NH_LLM_TEMPERATURE 设成 none(不发这个字段),"
                "或换一个接受采样参数的模型(如 claude-sonnet-4-6 / deepseek-chat)。"
            )

        host = (urlsplit(self.base_url.strip()).hostname or "").lower()
        if host in _OPENAI_OFFICIAL_HOSTS and family.startswith("claude-"):
            raise ValueError(
                f"base_url 指向 OpenAI 官方端点({host}),但 NH_LLM_MODEL 是 {self.model!r} —— "
                "那儿没有这个模型名。\n"
                "要用 Claude 就把 NH_LLM_BASE_URL 换成能转它的端点(如 OpenRouter 的 v1);"
                "要用 OpenAI 官方就把 NH_LLM_MODEL 换成它自己的模型名。"
            )

        return self

    @classmethod
    def from_env(cls) -> ProviderConfig:
        """从 NH_LLM_* 读配置。作者(非程序员)只需在启动前填这几个,不碰代码。

        缺失/矛盾的组合在这里就抛 `ValidationError`(带中文修复指引),不会带着一份坏配置往下走。
        """
        if "NH_LLM_MAX_TOKENS" in os.environ:
            raise ValueError(
                "NH_LLM_MAX_TOKENS 已移除:输出预算必须来自已验证的 resolved call plan,"
                "不再使用全局 4096 阀门。"
            )

        temp_raw = os.environ.get("NH_LLM_TEMPERATURE")
        if temp_raw is None:
            temperature: float | None = DEFAULT_TEMPERATURE
        elif temp_raw.strip().lower() in ("", "none", "omit"):
            temperature = None  # 显式让调用不带 temperature
        else:
            temperature = float(temp_raw)
        return cls(
            model=os.environ.get("NH_LLM_MODEL", "").strip(),
            base_url=os.environ.get("NH_LLM_BASE_URL", "").strip(),
            api_key=os.environ.get("NH_LLM_API_KEY", ""),
            temperature=temperature,
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
    """按 config 组装一个真的 OpenAI 兼容客户端。**构造不发网络请求**,所以它可以被单测覆盖。"""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - openai 是声明依赖,正常装包不会走到
        # 抓 ImportError 而非 ModuleNotFoundError:装了一半、C 扩展坏掉、被 sys.modules 打桩
        # 都只抛前者。这个分支要么永远不发生,要么发生时必须收敛成 ProviderError。
        raise ProviderError(
            "缺少 openai 客户端库(统一走 OpenAI 兼容协议)。请 `uv sync` 后重试。"
        ) from exc
    return OpenAI(
        # 端点可能不校验 key(本地 Ollama),但 openai 客户端**构造时**就要求非空
        # (空串直接抛 OpenAIError),所以这里给占位符。同时它保证不去读 OPENAI_API_KEY:
        # 我们永远显式传值,不让进程环境里的别人家的 key 悄悄参与进来。
        api_key=config.api_key or "not-needed",
        base_url=config.base_url,  # ProviderConfig 已保证非空
        timeout=config.timeout,
    )


def _wire_kwargs(
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    messages: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """把中立 plan 序列化成某一个 OpenAI-compatible endpoint 的精确 wire shape。"""
    route = normalize_base_url(config.base_url), normalize_model(config.model)
    if route != (plan.base_url, plan.model):
        raise ProviderError(
            "ProviderConfig route does not match ResolvedCallPlan route: "
            f"config={route!r}, plan={(plan.base_url, plan.model)!r}"
        )

    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": list(messages),
        plan.max_tokens_field: plan.request_token_budget,
        "stream": plan.stream,
    }
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if plan.stream and plan.capability.supports_stream_usage is True:
        kwargs["stream_options"] = {"include_usage": True}

    effort = plan.reasoning_effective
    dialect = plan.reasoning_dialect
    if effort is ReasoningEffort.OFF:
        # "off" 是产品语义,不等于所有 provider 都能靠省略字段实现。
        # OpenAI GPT-5.6 省略后默认 medium;DeepSeek V4 省略后默认 high。
        if dialect is ReasoningDialect.OPENAI:
            kwargs["reasoning_effort"] = "none"
        elif dialect is ReasoningDialect.OPENROUTER:
            kwargs["extra_body"] = {
                "reasoning": {"effort": "none", "exclude": True}
            }
        elif dialect is ReasoningDialect.DEEPSEEK:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return kwargs

    value = effort.value
    if dialect is ReasoningDialect.OPENAI:
        kwargs["reasoning_effort"] = value
    elif dialect is ReasoningDialect.OPENROUTER:
        kwargs["extra_body"] = {
            "reasoning": {"effort": value, "exclude": True}
        }
    elif dialect is ReasoningDialect.DEEPSEEK:
        kwargs["reasoning_effort"] = value
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    elif dialect is ReasoningDialect.ANTHROPIC_COMPAT:
        kwargs["extra_body"] = {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": value},
        }
    else:  # ProviderCapabilities 应已拦住;交通层仍不冒险发请求。
        raise ProviderError(
            f"reasoning={value} has no compatible wire dialect for {plan.model!r}"
        )
    return kwargs


def _from_non_streaming(resp: Any, fallback_model: str) -> CompletionResult:
    choice = resp.choices[0]
    usage = getattr(resp, "usage", None)
    return CompletionResult(
        text=getattr(choice.message, "content", None) or "",
        model=getattr(resp, "model", fallback_model),
        finish_reason=getattr(choice, "finish_reason", None),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )


def _from_stream(chunks: Any, fallback_model: str) -> CompletionResult:
    visible: list[str] = []
    model = fallback_model
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    for chunk in chunks:
        chunk_model = getattr(chunk, "model", None)
        if chunk_model:
            model = chunk_model
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
            completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
        for choice in getattr(chunk, "choices", ()) or ():
            content = getattr(getattr(choice, "delta", None), "content", None)
            if isinstance(content, str):
                visible.append(content)
            stopped = getattr(choice, "finish_reason", None)
            if stopped is not None:
                finish_reason = stopped

    return CompletionResult(
        text="".join(visible),
        model=model,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def complete(
    messages: Sequence[dict[str, Any]],
    *,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    client: Any = None,
) -> CompletionResult:
    """按已验证 plan 执行一次补全;大预算 stream 与非 stream 返回同一结果契约。"""
    kwargs = _wire_kwargs(config, plan, messages)
    client = client or _build_client(config)

    try:
        response = client.chat.completions.create(**kwargs)
        if plan.stream:
            return _from_stream(response, config.model)
        return _from_non_streaming(response, config.model)
    except ProviderError:
        raise
    except Exception as exc:  # 运输及流式迭代异常统一收口
        raise ProviderError(
            f"模型调用失败(model={config.model}, base_url={config.base_url}):{exc}"
        ) from exc
