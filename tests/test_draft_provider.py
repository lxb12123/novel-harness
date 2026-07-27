"""统一模型出口的单测。**零网络请求**:调用路径注入假客户端,建客户端路径只断言组装参数。

两条核心不变式:

1. 调用参数(model/temperature/max_tokens)全从 `ProviderConfig` 来,原样传到统一出口 ——
   这是 kill-gate 三臂与生产共用一份参数的基础。
2. **配置不自洽在构造时就红,不留到发请求。** 这一条是后加的:默认值曾经是一组自相矛盾的
   取值(`claude-opus-4-8` + 空 base_url + `temperature=0.7`),开箱即失败,而失败发生在
   第一次真发请求时 —— 对 kill-gate 是最坏的时机(跑到一半死,不是启动就红)。

`_build_client` 也在这儿被覆盖。它此前**一次都没被执行过**(所有测试都注入 `_fake_client`),
所以那段代码是「看起来有测试的没测试代码」。构造 OpenAI 客户端本身不发请求,断言得起来。
"""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import ValidationError

from novel_harness.draft.provider import (
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    SAMPLING_STRICT_MODELS,
    CompletionResult,
    ProviderConfig,
    ProviderError,
    _build_client,
    _model_family,
    complete,
)

LOCAL = "http://localhost:11434/v1"
"""测试里到处用的一个真实合法端点。base_url 现在是必填的,所以每次构造都要给一个。"""


def _fake_client(content: str = "草稿正文", *, raise_exc: Exception | None = None):
    calls: dict[str, dict] = {}

    def create(**kwargs):
        calls["kwargs"] = kwargs
        if raise_exc is not None:
            raise raise_exc
        return types.SimpleNamespace(
            model="served-by-fake",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content=content),
                    finish_reason="stop",
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, calls


# ── 默认值是一对自洽的取值 ────────────────────────────────────────────────


def test_the_two_defaults_are_a_matching_pair() -> None:
    """**这条是这个文件最重要的一条。**

    `DEFAULT_MODEL` 和 `DEFAULT_TEMPERATURE` 不是两个独立常量,是一对:默认模型在
    「拒绝非默认采样参数」的表里,所以默认温度只能是 None。谁把 temperature 改回 0.7、
    或把模型换成表里另一个而没动温度,这条就红 —— 而不是等到 kill-gate 跑起来吃 400。
    """
    assert DEFAULT_MODEL in SAMPLING_STRICT_MODELS
    assert DEFAULT_TEMPERATURE is None
    # 而且这一对真的构造得出来(只差一个端点)。
    cfg = ProviderConfig(base_url=LOCAL)
    assert cfg.model == DEFAULT_MODEL
    assert cfg.temperature is None


def test_defaults_plus_an_endpoint_construct_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """只填一个 NH_LLM_BASE_URL,其余全走默认 —— 这是作者最小配置的样子,必须是通的。"""
    for key in ("NH_LLM_MODEL", "NH_LLM_TEMPERATURE", "NH_LLM_MAX_TOKENS", "NH_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    cfg = ProviderConfig.from_env()
    assert (cfg.model, cfg.temperature) == (DEFAULT_MODEL, None)
    assert cfg.max_tokens == 4096


# ── 不自洽的配置在构造时就报错 ────────────────────────────────────────────


def test_missing_base_url_is_rejected_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 base_url 曾经默默指向 OpenAI 官方(而那儿没有默认模型)。现在它是个构造期错误。"""
    for key in ("NH_LLM_MODEL", "NH_LLM_BASE_URL", "NH_LLM_TEMPERATURE", "NH_LLM_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError, match="NH_LLM_BASE_URL"):
        ProviderConfig.from_env()
    # 直接构造也一样,不能只在 from_env 那条路上拦。
    with pytest.raises(ValidationError, match="NH_LLM_BASE_URL"):
        ProviderConfig()


def test_strict_model_with_a_temperature_is_rejected_at_construction() -> None:
    """默认模型 + temperature=0.7 —— 就是原来那对必 400 的默认值。构造时就得红。"""
    with pytest.raises(ValidationError, match="拒绝非默认 temperature"):
        ProviderConfig(base_url=LOCAL, model="claude-opus-4-8", temperature=0.7)


def test_the_vendor_prefix_does_not_bypass_the_sampling_check() -> None:
    """中转会加供应商前缀。不剥掉它,换个中转就能悄悄绕过守卫 —— 绕得过去的守卫等于没有。"""
    assert _model_family("anthropic/claude-opus-4-8") == "claude-opus-4-8"
    with pytest.raises(ValidationError, match="拒绝非默认 temperature"):
        ProviderConfig(base_url=LOCAL, model="anthropic/claude-opus-4-8", temperature=0.2)


def test_openai_official_endpoint_with_a_claude_model_is_rejected() -> None:
    """唯一一种可判定的「端点 ↔ 模型」错配:官方 OpenAI 端点上不存在 claude-*。"""
    with pytest.raises(ValidationError, match="OpenAI 官方端点"):
        ProviderConfig(
            base_url="https://api.openai.com/v1",
            model="claude-sonnet-4-6",  # 采样参数合法,所以红的必然是端点那条
        )


def test_the_guard_does_not_cry_wolf() -> None:
    """误报会让人把守卫拆掉。三种合法配置必须构造得出来。"""
    # ① 表外的模型可以带 temperature。
    assert ProviderConfig(base_url=LOCAL, model="qwen2.5", temperature=0.7).temperature == 0.7
    # ② 4-6 系接受采样参数,**别顺手把它们加进 SAMPLING_STRICT_MODELS**。
    assert "claude-opus-4-6" not in SAMPLING_STRICT_MODELS
    ProviderConfig(base_url=LOCAL, model="claude-opus-4-6", temperature=0.7)
    # ③ 官方 OpenAI 端点配 OpenAI 自己的模型,当然是对的。
    ProviderConfig(base_url="https://api.openai.com/v1", model="gpt-4o-mini", temperature=0.7)


def test_env_temperature_conflicting_with_env_model_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量那条路也要拦 —— 作者填的是环境变量,不是构造函数。"""
    monkeypatch.setenv("NH_LLM_BASE_URL", LOCAL)
    monkeypatch.setenv("NH_LLM_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("NH_LLM_TEMPERATURE", "0.7")
    with pytest.raises(ValidationError, match="拒绝非默认 temperature"):
        ProviderConfig.from_env()


# ── 配置来自环境变量,作者不碰代码 ─────────────────────────────────────────


def test_config_from_env_reads_the_three_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-xxx")
    cfg = ProviderConfig.from_env()
    assert cfg.model == "deepseek-chat"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.api_key == "sk-xxx"


def test_temperature_can_be_omitted_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_LLM_BASE_URL", LOCAL)
    monkeypatch.setenv("NH_LLM_MODEL", "qwen2.5")  # 表外模型:证明 None 不是被守卫逼出来的
    monkeypatch.setenv("NH_LLM_TEMPERATURE", "none")
    assert ProviderConfig.from_env().temperature is None


# ── complete():参数冻结、结果映射、错误收口 ──────────────────────────────


def test_complete_freezes_params_from_config() -> None:
    client, calls = _fake_client("这一场,他终究没有提起那件事。")
    cfg = ProviderConfig(base_url=LOCAL, model="qwen2.5", temperature=0.3, max_tokens=1024)
    res = complete([{"role": "user", "content": "写一段"}], config=cfg, client=client)

    assert isinstance(res, CompletionResult)
    assert res.text.startswith("这一场")
    assert res.finish_reason == "stop"
    assert res.completion_tokens == 7
    assert res.model == "served-by-fake"
    # 参数原样传到统一出口 —— kill-gate 三臂共用这一份
    assert calls["kwargs"]["model"] == "qwen2.5"
    assert calls["kwargs"]["temperature"] == 0.3
    assert calls["kwargs"]["max_tokens"] == 1024


def test_complete_omits_temperature_when_none() -> None:
    client, calls = _fake_client()
    cfg = ProviderConfig(base_url=LOCAL, temperature=None)
    complete([{"role": "user", "content": "x"}], config=cfg, client=client)
    assert "temperature" not in calls["kwargs"]


def test_complete_wraps_transport_errors() -> None:
    client, _ = _fake_client(raise_exc=RuntimeError("connection refused"))
    with pytest.raises(ProviderError, match="模型调用失败"):
        complete(
            [{"role": "user", "content": "x"}],
            config=ProviderConfig(base_url=LOCAL),
            client=client,
        )


# ── _build_client():真的建一个客户端,但**不发任何请求** ──────────────────
#
# openai 的 OpenAI(...) 构造器只是存字段,不握手、不发请求。所以这几条能在离线环境跑。


def test_build_client_carries_the_config_onto_the_client() -> None:
    """config 的三个字段必须原样落到客户端上;落错了只有真发请求时才看得见。"""
    cfg = ProviderConfig(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-real",
        timeout=12.5,
    )
    client = _build_client(cfg)
    # base_url 是 httpx.URL,且会被补上尾斜杠 —— 用 str() 比,别比对象。
    assert str(client.base_url).rstrip("/") == "https://api.deepseek.com/v1"
    assert client.api_key == "sk-real"
    assert client.timeout == 12.5


def test_build_client_substitutes_a_placeholder_key_for_keyless_endpoints() -> None:
    """本地端点不校验 key,但 openai 客户端**构造时**就拒绝空 key —— 占位符是 load-bearing 的。"""
    client = _build_client(ProviderConfig(base_url=LOCAL, model="qwen2.5", api_key=""))
    assert client.api_key == "not-needed"
    assert str(client.base_url).rstrip("/") == LOCAL

    # 证明占位符不是装饰:去掉它,openai 自己就会在构造时炸。
    from openai import OpenAI

    with pytest.raises(Exception, match="api_key"):
        OpenAI(api_key="", base_url=LOCAL)


def test_build_client_converts_a_missing_openai_into_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖缺失也必须收敛成 ProviderError —— 调用方只认识这一个异常类型。

    `sys.modules[...] = None` 让 `from openai import OpenAI` 抛 **ImportError**
    (不是 ModuleNotFoundError)。这正是把 except 从 ModuleNotFoundError 放宽到
    ImportError 的理由:装了一半 / C 扩展坏掉走的也是这一条。
    """
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ProviderError, match="缺少 openai"):
        _build_client(ProviderConfig(base_url=LOCAL, model="qwen2.5"))
