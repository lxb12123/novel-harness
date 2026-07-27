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

from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_MODEL = "claude-opus-4-8"
"""缺省模型。来自 `docs/PLAN.md` §5.2 的显式选型(起草和抽取默认 opus-4-8)。

**改它必须同时看 `DEFAULT_TEMPERATURE`** —— 它在 `SAMPLING_STRICT_MODELS` 里,
换成一个不在那张表里的模型时,默认 temperature 才有可能重新变成一个数字。
"""

DEFAULT_TEMPERATURE: float | None = None
"""缺省采样温度。**`None` 不是「随便」,是被 `DEFAULT_MODEL` 逼出来的唯一合法取值。**

`claude-opus-4-8` 拒绝非默认 `temperature`/`top_p`/`top_k`(400,`docs/PLAN.md` 第 301 行的
已核对硬约束)。所以「默认模型 + 默认温度」这一对里,温度只能是 None(= 不发这个字段)。
这两个常量**必须一起看**:任何一个单独改动都会让默认配置重新变回自相矛盾的一对。
`test_draft_provider.py::test_the_two_defaults_are_a_matching_pair` 就是钉这条缝的。
"""

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
    """一次调用的全部旋钮。**frozen**:kill-gate 里冻结它,三臂逐字节共用同一份参数。

    Raises:
        pydantic.ValidationError: 构造时配置不自洽(缺 base_url、模型与温度打架、
            官方 OpenAI 端点配 Claude 模型)。**这是有意的**:错误配置要在启动时红,
            不能留到 kill-gate 跑到一半才 400。
    """

    model_config = ConfigDict(frozen=True)

    model: str = DEFAULT_MODEL
    base_url: str = ""
    """端点,**必填,没有缺省值**。本地 Ollama 填 http://localhost:11434/v1;中转填 OpenRouter 等。

    **这里曾经有个缺省语义「空 = OpenAI 官方」,它就是那个 bug 的来源** —— 默认模型是
    `claude-opus-4-8`,而 OpenAI 官方端点上没有这个名字,于是开箱即 404。
    「猜一个供应商」没有对的猜法,所以现在不猜:空值在构造时报错,并告诉作者去填
    `NH_LLM_BASE_URL`。要打 OpenAI 官方就显式写 https://api.openai.com/v1 。
    """
    api_key: str = ""
    """本地 Ollama 之类不校验 key,可随便填(如 "ollama");闭源填真实 key。

    **故意不在构造时校验非空**:本地端点合法地不需要 key,而「哪些端点需要 key」不可判定。
    它是凭证缺失(运行时 401),不是配置自相矛盾 —— 两类问题别混在一个检查里。
    """
    temperature: float | None = DEFAULT_TEMPERATURE
    """None = **不发** temperature 字段,走供应商默认。见 `DEFAULT_TEMPERATURE` 与
    `SAMPLING_STRICT_MODELS`:对表里的模型,None 是唯一合法取值。"""
    max_tokens: int = 4096
    timeout: float = 600.0

    @model_validator(mode="after")
    def _reject_self_contradictory_config(self) -> ProviderConfig:
        """三条集合判断。**全部只回答「这两个取值能不能同时成立」,不回答「这是什么意思」。**"""
        if not self.model.strip():
            raise ValueError("NH_LLM_MODEL 是空的:必须指定模型名(如 deepseek-chat)。")

        if not self.base_url.strip():
            raise ValueError(
                "NH_LLM_BASE_URL 是空的:必须显式指定模型端点,本层不替你猜供应商。\n"
                "例:本地 Ollama = http://localhost:11434/v1;"
                "DeepSeek = https://api.deepseek.com/v1;OpenAI 官方 = https://api.openai.com/v1 。\n"
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
        temp_raw = os.environ.get("NH_LLM_TEMPERATURE")
        if temp_raw is None:
            temperature: float | None = DEFAULT_TEMPERATURE
        elif temp_raw.strip().lower() in ("", "none", "omit"):
            temperature = None  # 显式让调用不带 temperature
        else:
            temperature = float(temp_raw)
        return cls(
            model=os.environ.get("NH_LLM_MODEL", DEFAULT_MODEL),
            base_url=os.environ.get("NH_LLM_BASE_URL", "").strip(),
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
        pydantic.ValidationError: config 为 None 且环境变量里的配置不自洽(见 ProviderConfig)。
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
