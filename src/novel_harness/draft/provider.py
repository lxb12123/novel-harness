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
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import (
    CallPlan,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    StructuredCallPlan,
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


class ToolCall(BaseModel):
    """模型要求调用一个工具。**运输层不解析 `arguments`,原样交出去。**

    参数是模型生成的 JSON 字符串,它可以是残缺的、可以带模型幻想出来的字段。
    在这里 `json.loads` 等于让运输层替调用方决定「解析失败算什么」——
    而那是编排层的判断(重试?回一条错给模型?终止?),不是发请求这一层的。
    同理不校验 `name` 在不在工具表里:**工具表是权限边界,那道闸在编排层**,
    运输层认得它就等于有第二份工具表,两份迟早漂。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """`tool_result` 要用它配对。resume 时「哪几个 tool_call 还缺 result」也靠它。"""

    name: str
    arguments: str = ""
    """**原始 JSON 字符串,未解析。** 流式下它是若干个 delta 拼起来的。"""


class CacheShape(StrEnum):
    """这次的 `usage` 是**按哪一家的写法**报缓存的。

    只是一个诊断标签(「我认出来的是这一种」),**下游不许拿它做分支** —— 归一化已经在
    `CacheUsage` 上做完了,再认第二遍就是第二张会漂的映射表。
    """

    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class CacheUsage(BaseModel):
    """这一次调用里,输入 token 有多少是**不用重新算**的(即前缀缓存命中)。

    ── 这两个数是拿来回答什么问题的 ────────────────────────────────────────
    **它不是一个功能,是一次测量。** 加完之后作者照常用工作台,数据自己就出来,
    而三种走势各指向一个完全不同的动作:

    * **命中率高** ⇒ 他的端点已经在自动缓存了,**什么都不用做**;
    * **恒为 0**(注意:是报了 0,不是没报) ⇒ 要么这条路由不支持,要么它要显式标记,
      那时才轮到去调查「怎么标」——在此之前调查等于凭空猜;
    * **忽高忽低** ⇒ **前缀被什么东西弄脏了,那是个 bug**(缓存按前缀逐字节匹配,
      稳定块前面混进一个逐次变的东西就会整段失效,ADR 0019 边界六换序过一次的正是这个)。
      它以前不可观测,现在看得见。

    ── 为什么「没报」必须是整个 `CompletionResult.cache is None` ──────────────
    端点根本不提这件事,和端点说「这次一次都没命中」,是两个不同的事实,而它们会导出
    两个相反的动作(去查怎么开启 / 去查前缀被谁弄脏了)。本仓已经在四个地方栽过把两者
    糊成 0 的跟头,所以这里:**认不出的形状 ⇒ `None`,不是 0**。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    shape: CacheShape
    read_tokens: int | None = None
    """这次输入里从缓存直接拿到的 token 数。`None` = 这一家的写法里没有这个数。"""

    written_tokens: int | None = None
    """这次**为了下次能命中**而写进缓存的 token 数(Anthropic 为它单独计费)。

    `None` = 这一家根本不报这件事(DeepSeek / OpenAI 都不报),**不是 0**。

    ⚠️ **DeepSeek 的 `prompt_cache_miss_tokens` 不是这个数,别往这儿映。** 那是「这次没命中
    的那部分输入」,它 = `prompt_tokens - prompt_cache_hit_tokens`,是个派生量;把它当成
    「写了多少」会让 DeepSeek 看起来每次都在写缓存,**恰好污染上面那条「恒为 0」的判读**。
    """


_CACHE_SHAPES: Final[tuple[tuple[CacheShape, tuple[str, ...], tuple[str, ...]], ...]] = (
    (CacheShape.DEEPSEEK, ("prompt_cache_hit_tokens",), ()),
    (CacheShape.ANTHROPIC, ("cache_read_input_tokens",), ("cache_creation_input_tokens",)),
    (CacheShape.OPENAI, ("prompt_tokens_details", "cached_tokens"), ()),
)
"""字段名是各家自己定的,所以这张小映射躲不掉 —— 但它**只在这儿有一份**。

三家的写法(实测/官方文档):

| 家 | 读了多少 | 写了多少 |
|---|---|---|
| DeepSeek | `usage.prompt_cache_hit_tokens` | 不报 |
| Anthropic | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` |
| OpenAI | `usage.prompt_tokens_details.cached_tokens` | 不报 |

**顺序即优先级**,第一个认出来的赢。中转(OpenRouter 之类)转发时会带上被转发那一家的
形状,所以判据必须是「响应里有哪个字段」,不是「我配的是哪个供应商」—— 后者一换中转就错。
"""


def _usage_count(value: Any) -> int | None:
    """一个 usage 数字,读得懂才算数。

    非整数 / 布尔 / 负数一律 `None`(**不是 0**):读不懂就是不知道。这一层永远不抛 ——
    一次已经生成完、已经花过钱的调用,不该因为 usage 里多了个怪值就变成 `ProviderError`。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage_at(usage: Any, path: tuple[str, ...]) -> int | None:
    """顺着字段路径取一个 usage 数字。

    全程 `getattr`,和隔壁读 `prompt_tokens` 的那两行**同一种读法** —— 不为缓存这两个数
    单开一条更宽的读法(比如同时试 `dict.get`),否则会造出「token 数没读到、缓存数读到了」
    的半截状态,而那种状态在日志页上看起来像一次真实的测量。
    """
    cursor: Any = usage
    for name in path:
        cursor = getattr(cursor, name, None)
        if cursor is None:
            return None
    return _usage_count(cursor)


_NO_STREAM_OPTIONS: set[tuple[str, str]] = set()
"""实测**拒绝**过 `stream_options` 的路由（进程内，不落盘）。

为什么不登记进能力表:**这一位靠一次失败就学得会**,而能力表要装的是学不会的那些
(上下文窗口、输出上限)。写进表 = 又多一个要人手维护、下个月过期的字段。

为什么不落盘:一次白费的往返只发生在**换端点之后的第一稿**,而换端点本来就要重启;
落盘换来的是一张要迁移、要清理、会和作者改设置这件事对不齐的表。
"""


def _rejected_stream_options(exc: Exception, kwargs: dict[str, Any]) -> bool:
    """这次失败是不是**因为**我们带了 `stream_options`。

    判据是「**错误里点了这个字段的名**」,不是「状态码是不是 400」——后者会把
    「模型名写错」「钥匙过期」这些也吞进重试里,于是一次真正的配置错误变成两次失败,
    而作者只看见后面那次的错误话术。
    """
    if "stream_options" not in kwargs:
        return False
    return "stream_options" in str(exc)


def _cache_usage(usage: Any) -> CacheUsage | None:
    """从一个 `usage` 上认出缓存命中量,认不出返回 `None`(见 `CacheUsage`)。

    「认出来了」的判据是**至少读到一个能用的数**:字段在但值读不懂,等于没读到,继续试
    下一家。三家都没读到 ⇒ `None` —— 那句话的意思是「这个端点没报」,不是「一次都没命中」。
    """
    if usage is None:
        return None
    for shape, read_path, write_path in _CACHE_SHAPES:
        read = _usage_at(usage, read_path)
        written = _usage_at(usage, write_path) if write_path else None
        if read is None and written is None:
            continue
        return CacheUsage(shape=shape, read_tokens=read, written_tokens=written)
    return None


class CompletionResult(BaseModel):
    """一次调用的结果。文本 + 溯源(哪个模型、为什么停、花了多少 token)。frozen。"""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    cache: CacheUsage | None = None
    """输入里有多少不用重新算(见 `CacheUsage`)。**`None` = 这个端点没报这件事。**

    读它是**纯读取侧**的:发送侧一个字节都没变。DeepSeek 的前缀缓存是全自动的
    (不需要在请求里标任何东西),所以「读到了」这件事本身不构成一次行为改变 ——
    `tests/test_draft_boundary.py` 和 M2 判分链靠 wire shape 逐字节稳定,而这一刀碰不到
    `_wire_kwargs*` 里的任何一行。
    """

    tool_calls: tuple[ToolCall, ...] = ()
    """模型这一轮要调的工具。**空 = 它要说话了**,那就是 agent loop 的退出条件。

    默认空所以 M2 判分链和产品起草一个字都不用改:它们不传 `tools`,
    端点也就不会返回 `tool_calls`。
    """


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


def _validate_call_plan(plan: CallPlan) -> CallPlan:
    """Strictly rebuild a plan that may have bypassed validation via ``model_copy``."""
    if isinstance(plan, ResolvedCallPlan):
        return ResolvedCallPlan.model_validate(
            plan.model_dump(mode="python", warnings=False), strict=True
        )
    if isinstance(plan, StructuredCallPlan):
        return StructuredCallPlan.model_validate(
            plan.model_dump(mode="python", warnings=False), strict=True
        )
    raise TypeError("plan must be a ResolvedCallPlan or StructuredCallPlan")


def _wire_kwargs_from_validated(
    config: ProviderConfig,
    plan: CallPlan,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> dict[str, Any]:
    """把中立 plan 序列化成某一个 OpenAI-compatible endpoint 的精确 wire shape。

    `tools` 为空(默认)时**一个字段都不加**——这是 M2 判分链和产品起草的路径,
    它们发出去的东西必须和长出工具调用之前一模一样(`EVAL_PROTOCOL.md` §2:
    gate 测的必须是产品会发的东西)。`test_provider_tools.py` 钉住了这一条。

    **不查「这个模型支不支持工具调用」。** 那和 `SAMPLING_STRICT_MODELS` 那张表
    面对的是同一类问题:从名字推不出来,任何中转都能转任何东西。不支持的端点会 400,
    统一收敛成 `ProviderError`——和这个文件对待未知模型的既有立场一致。
    """
    route = normalize_base_url(config.base_url), normalize_model(config.model)
    if route != (plan.base_url, plan.model):
        raise ProviderError(
            "ProviderConfig route does not match CallPlan route: "
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
    if (
        plan.stream
        and getattr(plan, "interruptible", False)
        and plan.capability.route not in _NO_STREAM_OPTIONS
    ):
        # **要用量的判据是「这一次要可中断」，不再是「这条路由登记过支持」**（2026-08-13）。
        # 原来看的是 `supports_stream_usage is True`，而注册表里只有 OpenAI 那四条是
        # `True` ⇒ 作者自己那条路一开可中断，每一稿的 token 数就退成「未记录」。
        #
        # ⚠️ **为什么不干脆无条件发**：那样 `nh gate` 那条路的 wire 上会多出这个字段，
        #    而它是**预注册的考卷**（EVAL_PROTOCOL §2：gate 测的必须是产品会发的东西，
        #    且逐字节稳定）。`interruptible` 恰好是「产品起草」和「M2 三臂」的分界——
        #    后者**永不设它**（`ResolvedCallPlan.interruptible` 的 docstring 写着），
        #    `StructuredCallPlan` 上压根没有这一位。于是这道闸天然只开在该开的那条路上。
        #    代价说清楚：**不可中断的长稿（预算过 16k 会流式）仍然拿不到用量**。
        #    那一档今天没有生产调用方；真长出来了，改的是这儿，不是回去登记能力。
        #
        # 三种结局，只有第三种要处理：
        #   ① 支持 → 拿到用量；
        #   ② 不认识这个字段 → 兼容端点普遍**忽略**未知字段 ⇒ 没有用量 ⇒ `_from_stream`
        #      落 `None` ⇒ 屏幕说「未记录」。**读取侧本来就 fail-safe，不用登记。**
        #   ③ 严格拒绝（400）→ 那就不是「少个数」，是**整次起草失败**。所以 `complete()`
        #      在那一档退一次、记进 `_NO_STREAM_OPTIONS`，这条路由以后不再带它。
        kwargs["stream_options"] = {"include_usage": True}
    if tools:
        kwargs["tools"] = list(tools)
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
    elif tool_choice is not None:
        # 没给工具却指定「必须调工具」= 一个必然失败的请求。在发出去之前拦住,
        # 别让它变成一条要从供应商 4xx 里反推的错误。
        raise ProviderError("tool_choice 需要同时提供 tools")

    effort = plan.reasoning_effective
    dialect = plan.reasoning_dialect
    if effort is ReasoningEffort.OFF:
        # "off" 是产品语义,不等于所有 provider 都能靠省略字段实现。
        # OpenAI GPT-5.6 省略后默认 medium;DeepSeek V4 省略后默认 high。
        if dialect is ReasoningDialect.OPENAI:
            kwargs["reasoning_effort"] = "none"
        elif dialect is ReasoningDialect.OPENROUTER:
            kwargs["extra_body"] = {"reasoning": {"effort": "none", "exclude": True}}
        elif dialect is ReasoningDialect.DEEPSEEK:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        # ⚠️ **`ANTHROPIC_COMPAT` 在这儿什么都不发 —— 而那是一个已知缺口,不是省略。**
        #    2026-08-13 由官方文档坐实(`ANTHROPIC_EFFORT_URL`),三句话连起来看:
        #      ① 「By default, Claude uses high effort」
        #      ② 「Setting effort to "high" produces exactly the same behavior as
        #         omitting the effort parameter entirely」
        #      ③ 「it is a hard limit on total output, thinking plus response text」
        #    ⇒ 我们的 OFF 不发字段 = 请求按 **high** 跑 = 思考照样发生,
        #      而思考的 token 和正文**共用** `max_tokens`。
        #    偏偏 `plan_call` 的预留分支写的是「effort 不是 OFF 才预留」,于是这一档
        #    **一个 token 都没预留** ⇒ 一次整章起草(可见预算 7,024)可能被思考吃掉一截
        #    ⇒ 稿子在 `finish_reason="length"` 上截断,看起来像模型不行。
        #    档位表上那五档(max/xhigh/high/medium/low)**没有 "none"**,所以 OFF 只能靠
        #    `thinking: {"type": "disabled"}` 表达 —— 而那个在 xhigh/max 上会 400,
        #    在「adaptive 常开」的型号上行为未知。**没有 Anthropic 钥匙就验不了,
        #    所以这一刀今天不发**:发错的下场是起草整个 400,比现在这个偏小的预算更坏。
        #    钉在 `tests/test_draft_provider.py::test_off_on_anthropic_is_a_known_gap`。
        return kwargs

    value = effort.value
    if dialect is ReasoningDialect.OPENAI:
        kwargs["reasoning_effort"] = value
    elif dialect is ReasoningDialect.OPENROUTER:
        kwargs["extra_body"] = {"reasoning": {"effort": value, "exclude": True}}
    elif dialect is ReasoningDialect.DEEPSEEK:
        kwargs["reasoning_effort"] = value
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    elif dialect is ReasoningDialect.ANTHROPIC_COMPAT:
        kwargs["extra_body"] = {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": value},
        }
    else:  # ProviderCapabilities 应已拦住;交通层仍不冒险发请求。
        raise ProviderError(f"reasoning={value} has no compatible wire dialect for {plan.model!r}")
    return kwargs


def _prepare_call(
    config: ProviderConfig,
    plan: CallPlan,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> tuple[CallPlan, dict[str, Any]]:
    """Return one strict plan and the wire shape derived from that same instance."""
    validated = _validate_call_plan(plan)
    return validated, _wire_kwargs_from_validated(config, validated, messages, tools, tool_choice)


def _wire_kwargs(
    config: ProviderConfig,
    plan: CallPlan,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> dict[str, Any]:
    """Keep the existing test/debug surface while enforcing transport validation."""
    return _prepare_call(config, plan, messages, tools, tool_choice)[1]


def _tool_calls_from_message(message: Any) -> tuple[ToolCall, ...]:
    """从一条非流式 message 上摘 `tool_calls`。

    全程 `getattr`:兼容层的响应对象形状各家不一(有的是 pydantic,有的是 dict-like,
    本地端点可能干脆没有这个字段)。缺字段返回空元组,**不抛**——
    「模型没要调工具」是最常见的正常情况。
    """
    raw = getattr(message, "tool_calls", None) or ()
    calls: list[ToolCall] = []
    for item in raw:
        fn = getattr(item, "function", None)
        name = getattr(fn, "name", None) or ""
        if not name:
            continue  # 没有函数名的条目无法派发,丢掉比造一个空名字安全
        calls.append(
            ToolCall(
                id=getattr(item, "id", None) or "",
                name=name,
                arguments=getattr(fn, "arguments", None) or "",
            )
        )
    return tuple(calls)


def _from_non_streaming(resp: Any, fallback_model: str) -> CompletionResult:
    choice = resp.choices[0]
    usage = getattr(resp, "usage", None)
    return CompletionResult(
        text=getattr(choice.message, "content", None) or "",
        model=getattr(resp, "model", fallback_model),
        finish_reason=getattr(choice, "finish_reason", None),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        cache=_cache_usage(usage),
        tool_calls=_tool_calls_from_message(choice.message),
    )


def _from_stream(chunks: Any, fallback_model: str) -> CompletionResult:
    visible: list[str] = []
    model = fallback_model
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # 缓存命中量和上面两个数走同一条路(都在 usage 上),所以**两条路都要读**——
    # 只补非流式那一条,长稿(>16k 预算,走流式)就会永远说「不知道」,
    # 而长稿恰恰是最该看缓存的那一档。
    cache: CacheUsage | None = None
    # 流式工具调用**按 index 累积**:`id` 和 `name` 通常只在第一个 delta 出现,
    # 而 `arguments` 是一串碎片(`{"cha` / `pter":` / ` 89}`)。
    # 用 dict 而不是 list:index 不保证从 0 连续,也不保证按序到达。
    partial: dict[int, dict[str, str]] = {}

    for chunk in chunks:
        chunk_model = getattr(chunk, "model", None)
        if chunk_model:
            model = chunk_model
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
            completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
            # 认出来了才覆盖:有的端点每个 chunk 都挂一个 usage,只有最后那个填满。
            # 用「认出的最后一次」而不是「最后一次」,免得一个空 usage 把已经读到的数抹掉。
            parsed = _cache_usage(usage)
            if parsed is not None:
                cache = parsed
        for choice in getattr(chunk, "choices", ()) or ():
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if isinstance(content, str):
                visible.append(content)
            for item in getattr(delta, "tool_calls", None) or ():
                slot = partial.setdefault(
                    getattr(item, "index", 0) or 0, {"id": "", "name": "", "arguments": ""}
                )
                call_id = getattr(item, "id", None)
                if call_id:
                    slot["id"] = call_id
                fn = getattr(item, "function", None)
                name = getattr(fn, "name", None)
                if name:
                    slot["name"] = name
                args = getattr(fn, "arguments", None)
                if isinstance(args, str):
                    slot["arguments"] += args  # 只能拼,不能覆盖——覆盖会丢掉前面的碎片
            stopped = getattr(choice, "finish_reason", None)
            if stopped is not None:
                finish_reason = stopped

    return CompletionResult(
        text="".join(visible),
        model=model,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache=cache,
        tool_calls=tuple(
            ToolCall(id=slot["id"], name=slot["name"], arguments=slot["arguments"])
            for _, slot in sorted(partial.items())  # 按 index 还原模型给的顺序
            if slot["name"]
        ),
    )


def complete(
    messages: Sequence[dict[str, Any]],
    *,
    config: ProviderConfig,
    plan: CallPlan,
    client: Any = None,
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> CompletionResult:
    """按已验证 plan 执行一次补全;大预算 stream 与非 stream 返回同一结果契约。

    Args:
        tools: OpenAI 兼容的工具声明。**不传(默认)时这个函数的行为一字不变**——
            M2 判分链和产品起草走的就是那条路。
        tool_choice: `"auto"` / `"none"` / `"required"`。**必须和 `tools` 一起给。**

    Notes:
        **这里不认识工具表,也不该认识。** 派发、权限、停止条件全在编排层;
        运输层只负责把声明发出去、把模型的请求原样带回来。
        工具表是权限边界(铁律 5),那道闸只许有一处。
    """
    validated_plan, kwargs = _prepare_call(config, plan, messages, tools, tool_choice)
    client = client or _build_client(config)

    try:
        response = client.chat.completions.create(**kwargs)
        if validated_plan.stream:
            return _from_stream(response, config.model)
        return _from_non_streaming(response, config.model)
    except ProviderError:
        raise
    except Exception as exc:  # 运输及流式迭代异常统一收口
        if _rejected_stream_options(exc, kwargs):
            # **只退这一个字段，不退流式**：作者要的是「能停下来」，用量是附带的。
            # 记住这条路由,下一次连试都不试——否则每一稿都白费一次失败的往返。
            _NO_STREAM_OPTIONS.add(validated_plan.capability.route)
            retry = {key: value for key, value in kwargs.items() if key != "stream_options"}
            try:
                response = client.chat.completions.create(**retry)
                return _from_stream(response, config.model)
            except Exception as retry_exc:
                raise ProviderError(
                    f"模型调用失败(model={config.model}, base_url={config.base_url})"
                    f":{retry_exc}"
                ) from retry_exc
        raise ProviderError(
            f"模型调用失败(model={config.model}, base_url={config.base_url}):{exc}"
        ) from exc
