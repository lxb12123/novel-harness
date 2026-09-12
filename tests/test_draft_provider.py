"""统一模型出口的单测。**零网络请求**:调用路径注入假客户端,建客户端路径只断言组装参数。

两条核心不变式:

1. 连接/采样参数(model/base_url/temperature)全从 `ProviderConfig` 来。输出预算
   属于单独的 resolved call plan，不再是连接配置里的 4096 默认值。
2. **配置不自洽在构造时就红,不留到发请求。** 这一条是后加的:默认值曾经是一组自相矛盾的
   取值(`claude-opus-4-8` + 空 base_url + `temperature=0.7`),开箱即失败,而失败发生在
   第一次真发请求时 —— 对 kill-gate 是最坏的时机(跑到一半死,不是启动就红)。

`_build_client` 也在这儿被覆盖。它此前**一次都没被执行过**(所有测试都注入 `_fake_client`),
所以那段代码是「看起来有测试的没测试代码」。构造 OpenAI 客户端本身不发请求,断言得起来。
"""

from __future__ import annotations

import json
import sys
import types

import httpx
import pytest
from pydantic import ValidationError

from novel_harness.draft.capabilities import (
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
)
from novel_harness.draft.length import M2_LENGTH_SPEC, LengthSpec
from novel_harness import __version__
from novel_harness.draft.provider import (
    CLIENT_NAME,
    DEFAULT_TEMPERATURE,
    SAMPLING_STRICT_MODELS,
    SESSION_ID,
    CompletionResult,
    ProviderConfig,
    ProviderError,
    _build_client,
    _model_family,
    _wire_kwargs,
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


TEST_LENGTH = LengthSpec(language="zh", min_units=100, target_units=200, max_units=300)


def _plan(
    dialect: ReasoningDialect = ReasoningDialect.NONE,
    effort: ReasoningEffort = ReasoningEffort.OFF,
    *,
    base_url: str = LOCAL,
    model: str = "test-model",
    length: LengthSpec = TEST_LENGTH,
    request_token_budget: int | None = None,
    supports_stream_usage: bool | None = None,
) -> ResolvedCallPlan:
    reasoning = (
        frozenset({ReasoningEffort.OFF})
        if dialect is ReasoningDialect.NONE
        else frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH})
    )
    capability = ProviderCapabilities(
        base_url=base_url,
        model=model,
        source="operator-test",
        source_urls=("https://example.test/capability",),
        max_context_tokens=1_000_000,
        max_output_tokens=128_000,
        max_tokens_field=(
            "max_completion_tokens" if dialect is ReasoningDialect.OPENAI else "max_tokens"
        ),
        reasoning_levels=reasoning,
        reasoning_dialect=dialect,
        reasoning_shares_output=dialect is not ReasoningDialect.NONE,
        reserve_ratio_high=0.8 if dialect is not ReasoningDialect.NONE else None,
    )
    return plan_call(
        length,
        effort,
        capability,
        request_token_budget=request_token_budget,
    )


# ── 默认值是一对自洽的取值 ────────────────────────────────────────────────


def test_connection_config_has_no_default_provider_or_model() -> None:
    """ProviderConfig 不猜供应商,也不偷塞一个模型。"""
    assert DEFAULT_TEMPERATURE is None
    with pytest.raises(ValidationError):
        ProviderConfig()
    with pytest.raises(ValidationError):
        ProviderConfig(base_url=LOCAL)
    with pytest.raises(ValidationError):
        ProviderConfig(model="qwen2.5")


def test_explicit_endpoint_and_model_construct_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最小配置是显式 endpoint + model;输出预算不属于连接配置。"""
    for key in ("NH_LLM_TEMPERATURE", "NH_LLM_MAX_TOKENS", "NH_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("NH_LLM_MODEL", "anthropic/claude-opus-4.8")
    cfg = ProviderConfig.from_env()
    assert (cfg.model, cfg.temperature) == ("anthropic/claude-opus-4.8", None)
    assert "max_tokens" not in ProviderConfig.model_fields


def test_direct_connection_config_strips_transport_whitespace() -> None:
    cfg = ProviderConfig(model="  qwen2.5  ", base_url=f"  {LOCAL}/  ")
    assert cfg.model == "qwen2.5"
    assert cfg.base_url == LOCAL


# ── 不自洽的配置在构造时就报错 ────────────────────────────────────────────


def test_missing_base_url_is_rejected_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 base_url 曾经默默指向 OpenAI 官方(而那儿没有默认模型)。现在它是个构造期错误。"""
    for key in ("NH_LLM_MODEL", "NH_LLM_BASE_URL", "NH_LLM_TEMPERATURE", "NH_LLM_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NH_LLM_MODEL", "qwen2.5")
    with pytest.raises(ValidationError, match="NH_LLM_BASE_URL"):
        ProviderConfig.from_env()
    # 直接构造也一样,不能只在 from_env 那条路上拦。
    with pytest.raises(ValidationError, match="NH_LLM_BASE_URL"):
        ProviderConfig(model="qwen2.5", base_url="")


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
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-xxx")
    cfg = ProviderConfig.from_env()
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.api_key == "sk-xxx"


def test_removed_global_max_tokens_is_rejected_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4096 旧阀门不能悄悄污染新调用;请求预算必须走 call plan。"""
    with pytest.raises(ValidationError, match="max_tokens"):
        ProviderConfig(model="qwen2.5", base_url=LOCAL, max_tokens=4096)

    monkeypatch.setenv("NH_LLM_MODEL", "qwen2.5")
    monkeypatch.setenv("NH_LLM_BASE_URL", LOCAL)
    monkeypatch.setenv("NH_LLM_MAX_TOKENS", "4096")
    with pytest.raises(ValueError, match="NH_LLM_MAX_TOKENS.*call plan"):
        ProviderConfig.from_env()


def test_temperature_can_be_omitted_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_LLM_BASE_URL", LOCAL)
    monkeypatch.setenv("NH_LLM_MODEL", "qwen2.5")  # 表外模型:证明 None 不是被守卫逼出来的
    monkeypatch.setenv("NH_LLM_TEMPERATURE", "none")
    assert ProviderConfig.from_env().temperature is None


def test_api_key_is_excluded_from_repr_dump_and_validation_errors() -> None:
    secret = "sk-不许出现"
    cfg = ProviderConfig(model="test-model", base_url=LOCAL, api_key=secret)
    assert secret not in repr(cfg)
    assert "api_key" not in cfg.model_dump()

    with pytest.raises(ValidationError) as caught:
        ProviderConfig(
            model="claude-opus-4-8",
            base_url="https://api.openai.com/v1",
            api_key=secret,
        )
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        (ReasoningDialect.OPENAI, {"reasoning_effort": "high"}),
        (
            ReasoningDialect.OPENROUTER,
            {"extra_body": {"reasoning": {"effort": "high", "exclude": True}}},
        ),
        (
            ReasoningDialect.DEEPSEEK,
            {
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
            },
        ),
        (
            ReasoningDialect.ANTHROPIC_COMPAT,
            {
                "extra_body": {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "high"},
                }
            },
        ),
    ],
)
def test_high_reasoning_maps_to_each_compatible_wire_dialect(
    dialect: ReasoningDialect,
    expected: dict[str, object],
) -> None:
    plan = _plan(
        dialect,
        ReasoningEffort.HIGH,
        length=M2_LENGTH_SPEC,
        request_token_budget=40_000,
    )
    config = ProviderConfig(model=plan.model, base_url=plan.base_url)
    kwargs = _wire_kwargs(config, plan, [{"role": "user", "content": "x"}])

    assert kwargs[plan.max_tokens_field] == 40_000
    assert kwargs["stream"] is True
    # **2026-08-13：判据从「这条路由登记过 stream usage」换成「这一次要可中断」。**
    # 这条 plan 不可中断（M2 三臂那一档），所以四种方言一律不带这个字段。
    assert "stream_options" not in kwargs
    for key, value in expected.items():
        assert kwargs[key] == value


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        (ReasoningDialect.NONE, {}),
        (ReasoningDialect.OPENAI, {"reasoning_effort": "none"}),
        (
            ReasoningDialect.OPENROUTER,
            {"extra_body": {"reasoning": {"effort": "none", "exclude": True}}},
        ),
        (
            ReasoningDialect.DEEPSEEK,
            {"extra_body": {"thinking": {"type": "disabled"}}},
        ),
        (ReasoningDialect.ANTHROPIC_COMPAT, {}),
    ],
)
def test_off_reasoning_is_effectively_off_for_each_dialect(
    dialect: ReasoningDialect,
    expected: dict[str, object],
) -> None:
    plan = _plan(dialect)
    config = ProviderConfig(model=plan.model, base_url=plan.base_url)
    kwargs = _wire_kwargs(config, plan, [{"role": "user", "content": "x"}])

    assert kwargs[plan.max_tokens_field] == plan.request_token_budget
    assert kwargs["stream"] is False
    reasoning_keys = {"reasoning_effort", "extra_body"}
    assert {key: kwargs[key] for key in reasoning_keys & kwargs.keys()} == expected


def test_wire_rejects_a_plan_for_a_different_route() -> None:
    plan = _plan()
    with pytest.raises(ProviderError, match="CallPlan route"):
        _wire_kwargs(
            ProviderConfig(model="other-model", base_url=LOCAL),
            plan,
            [{"role": "user", "content": "x"}],
        )


# ── complete():参数冻结、结果映射、错误收口 ──────────────────────────────


def test_complete_freezes_params_from_config() -> None:
    client, calls = _fake_client("这一场,他终究没有提起那件事。")
    cfg = ProviderConfig(base_url=LOCAL, model="qwen2.5", temperature=0.3)
    plan = _plan(model="qwen2.5")
    res = complete(
        [{"role": "user", "content": "写一段"}],
        config=cfg,
        plan=plan,
        client=client,
    )

    assert isinstance(res, CompletionResult)
    assert res.text.startswith("这一场")
    assert res.finish_reason == "stop"
    assert res.completion_tokens == 7
    assert res.model == "served-by-fake"
    # 参数原样传到统一出口 —— kill-gate 三臂共用这一份
    assert calls["kwargs"]["model"] == "qwen2.5"
    assert calls["kwargs"]["temperature"] == 0.3
    assert calls["kwargs"]["max_tokens"] == plan.request_token_budget


def test_complete_omits_temperature_when_none() -> None:
    client, calls = _fake_client()
    cfg = ProviderConfig(base_url=LOCAL, model="qwen2.5", temperature=None)
    complete(
        [{"role": "user", "content": "x"}],
        config=cfg,
        plan=_plan(model="qwen2.5"),
        client=client,
    )
    assert "temperature" not in calls["kwargs"]


def test_complete_requires_a_resolved_call_plan() -> None:
    client, _ = _fake_client()
    with pytest.raises(TypeError, match="plan"):
        complete(  # type: ignore[call-arg]
            [{"role": "user", "content": "x"}],
            config=ProviderConfig(base_url=LOCAL, model="qwen2.5"),
            client=client,
        )


def test_complete_wraps_transport_errors() -> None:
    client, _ = _fake_client(raise_exc=RuntimeError("connection refused"))
    with pytest.raises(ProviderError, match="模型调用失败"):
        complete(
            [{"role": "user", "content": "x"}],
            config=ProviderConfig(base_url=LOCAL, model="qwen2.5"),
            plan=_plan(model="qwen2.5"),
            client=client,
        )


def test_streaming_ignores_reasoning_and_aggregates_visible_text_and_usage() -> None:
    plan = _plan(
        ReasoningDialect.OPENROUTER,
        ReasoningEffort.HIGH,
        length=M2_LENGTH_SPEC,
        request_token_budget=40_000,
    )
    chunks = iter(
        [
            types.SimpleNamespace(
                model="stream-model",
                choices=[
                    types.SimpleNamespace(
                        delta=types.SimpleNamespace(
                            content=None,
                            reasoning="不得进入正文",
                            reasoning_content="也不得进入",
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            types.SimpleNamespace(
                model="stream-model",
                choices=[
                    types.SimpleNamespace(
                        delta=types.SimpleNamespace(content="可见"),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            types.SimpleNamespace(
                model="stream-model",
                choices=[
                    types.SimpleNamespace(
                        delta=types.SimpleNamespace(content="正文"),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
            types.SimpleNamespace(
                model="stream-model",
                choices=[],
                usage=types.SimpleNamespace(prompt_tokens=101, completion_tokens=202),
            ),
        ]
    )
    calls: dict[str, object] = {}

    def create(**kwargs: object) -> object:
        calls.update(kwargs)
        return chunks

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    result = complete(
        [{"role": "user", "content": "x"}],
        config=ProviderConfig(model=plan.model, base_url=plan.base_url),
        plan=plan,
        client=client,
    )

    assert result == CompletionResult(
        text="可见正文",
        model="stream-model",
        finish_reason="stop",
        prompt_tokens=101,
        completion_tokens=202,
    )
    assert calls["stream"] is True


def test_openai_sdk_serializes_extra_body_into_the_actual_json_request() -> None:
    from openai import OpenAI

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
                },
            },
        )

    sdk_client = OpenAI(
        api_key="not-needed",
        base_url="https://example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    plan = _plan(
        ReasoningDialect.ANTHROPIC_COMPAT,
        ReasoningEffort.HIGH,
        base_url="https://example.test/v1",
    )
    result = complete(
        [{"role": "user", "content": "x"}],
        config=ProviderConfig(model=plan.model, base_url=plan.base_url),
        plan=plan,
        client=sdk_client,
    )

    assert result.text == "ok"
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["output_config"] == {"effort": "high"}


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


def test_build_client_identifies_itself_and_carries_a_session_id() -> None:
    """每条路都带 `User-Agent` 和 `x-opencode-session`,**不按端点分档**。

    2026-09-08 的真书事故:opencode 的 Go 端点开始强制要 `x-opencode-session`,
    少了就是 `400 MissingSessionID`——一次都过不去,31 章总结批量失败。
    它家文档同时要求客户端报自己的名字而不是 SDK 的默认 UA,所以两个头一起补。
    """
    headers = {
        key.lower(): value
        for key, value in _build_client(
            ProviderConfig(base_url=LOCAL, model="qwen2.5")
        ).default_headers.items()
    }
    assert headers["user-agent"] == f"{CLIENT_NAME}/{__version__}"
    assert headers["x-opencode-session"] == SESSION_ID
    # 发给本地 Ollama 的也带 —— 判主机名的分支会在作者架一层中转的那天静默失效,
    # 而 HTTP 的规矩本来就是认不出的头一律忽略。
    assert LOCAL.startswith("http://localhost")


def test_session_id_is_stable_across_clients_in_one_process() -> None:
    """一次进程一个,**进程内恒定**:端点要它是为了路由亲和与 prompt 缓存,
    每次换一个等于每次换一台上游机器,而缓存收益归零这件事在账上看不出来。"""
    first = _build_client(ProviderConfig(base_url=LOCAL, model="qwen2.5"))
    second = _build_client(ProviderConfig(base_url="https://api.deepseek.com/v1", model="x"))
    assert (
        first.default_headers["x-opencode-session"]
        == second.default_headers["x-opencode-session"]
        == SESSION_ID
    )


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


def test_off_on_anthropic_is_a_known_gap() -> None:
    """**这条钉的是一个「今天还没修」，不是一个「今天正确」。**

    Anthropic 官方 effort 文档（`ANTHROPIC_EFFORT_URL`）三句话连起来：
    默认就是 `high` ／ 显式写 `high` 和干脆不写**完全等价** ／ `max_tokens` 是
    「思考 + 正文」的硬上限。所以我们在 OFF 档不发字段 = 请求按 **high** 跑 =
    思考照样发生、照样从 `max_tokens` 里扣，而 `plan_call` 的预留分支
    （`effort is not OFF and reasoning_shares_output`）**这一档根本不进** ⇒ 零预留。

    后果不是报错，是**一次整章起草可能在 `finish_reason="length"` 上被截断**，
    而作者看到的是「这助手写着写着没了」。

    ── 为什么不顺手修掉 ──────────────────────────────────────────────────
    档位表上五档（max/xhigh/high/medium/low）**没有 "none"**，OFF 只能靠
    `thinking: {"type": "disabled"}` 表达；而官方明写它在 xhigh/max 上返回 400，
    在「adaptive 常开」的型号（`claude-fable-5`）上行为没写。
    **没有 Anthropic 钥匙就验不了**，而发错的下场是起草整个 400 —— 比现在这个
    偏小的预算更坏。所以今天只把它钉住、说清楚。

    ── 这条什么时候该删 ──────────────────────────────────────────────────
    有人拿真钥匙验过并补上 OFF 的编码之后，这条会红。**那时删掉它**，
    并把 `provider.py` 那段注释一起换成实测结论。
    """
    from novel_harness.draft.capabilities import CAPABILITY_REGISTRY
    from novel_harness.draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage

    length = DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)
    route = ("https://api.anthropic.com/v1", "claude-opus-5")
    capability = CAPABILITY_REGISTRY[route]
    assert capability.reasoning_dialect is ReasoningDialect.ANTHROPIC_COMPAT
    assert capability.reasoning_shares_output is True

    plan = plan_call(length, ReasoningEffort.OFF, capability)
    wire = _wire_kwargs(
        ProviderConfig(base_url=route[0], model=route[1], api_key="k"),
        plan,
        [{"role": "user", "content": "写第 89 章"}],
    )
    assert "extra_body" not in wire and "reasoning_effort" not in wire, (
        "OFF 在 Anthropic 上开始发东西了 —— 若是有人验过并补上了编码，"
        "删掉这条测试，并把 provider.py 里那段注释换成实测结论。"
    )
    # 零预留的那一半：required 等于 visible，一个 token 都没给思考留。
    assert plan.required_token_budget == plan.visible_token_budget, (
        "预留逻辑变了 —— 这条测试描述的缺口可能已经不成立，重新读 plan_call。"
    )
