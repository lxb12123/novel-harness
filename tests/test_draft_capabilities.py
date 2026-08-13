"""Provider capability resolution and deterministic output-budget planning."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

import pytest
from pydantic import ValidationError

import novel_harness.draft.capabilities as capability_module
from novel_harness.draft.capabilities import (
    CAPABILITY_REGISTRY,
    CAPABILITY_REGISTRY_VERSION,
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec, M2_LENGTH_SPEC


_DIRECT_LENGTH_SPEC = LengthSpec(
    language=DraftLanguage.ZH, min_units=1, target_units=1, max_units=1
)


def _caps(**updates: object) -> ProviderCapabilities:
    values: dict[str, object] = {
        "base_url": "http://localhost:11434/v1",
        "model": "test-model",
        "source": "operator-test",
        "source_urls": ("https://example.test/model",),
        "registry_version": "operator-test-v1",
        "max_context_tokens": 100_000,
        "max_output_tokens": 80_000,
        "max_tokens_field": "max_tokens",
        "max_tokens_field_source": "declared",
        "reasoning_levels": frozenset({ReasoningEffort.OFF}),
        "reasoning_dialect": ReasoningDialect.NONE,
        "reasoning_shares_output": False,
        "reserve_ratio_high": None,
    }
    values.update(updates)
    return ProviderCapabilities(**values)


def _direct_plan(
    capability: ProviderCapabilities, **updates: object
) -> ResolvedCallPlan:
    values: dict[str, object] = {
        "base_url": capability.base_url,
        "model": capability.model,
        "length": _DIRECT_LENGTH_SPEC,
        "prompt_token_budget": 0,
        "visible_token_budget": 1_026,
        "required_token_budget": 1_026,
        "request_token_budget": 1_026,
        "max_tokens_field": capability.max_tokens_field,
        "reasoning_requested": ReasoningEffort.OFF,
        "reasoning_effective": ReasoningEffort.OFF,
        "reasoning_dialect": capability.reasoning_dialect,
        "stream": False,
        "capability": capability,
    }
    values.update(updates)
    return ResolvedCallPlan(**values)


def test_exact_registry_contains_all_supported_routes() -> None:
    openai = resolve_capabilities("https://API.OPENAI.COM:443/v1/", "gpt-5.6")
    assert openai.base_url == "https://api.openai.com/v1"
    assert openai.max_output_tokens == 128_000
    assert openai.max_tokens_field == "max_completion_tokens"
    assert openai.reasoning_dialect is ReasoningDialect.OPENAI

    deepseek = resolve_capabilities("https://api.deepseek.com", "deepseek-v4-pro")
    assert deepseek.max_context_tokens == 1_000_000
    assert deepseek.max_output_tokens == 384_000
    assert deepseek.max_tokens_field == "max_tokens"
    assert ReasoningEffort.HIGH in deepseek.reasoning_levels

    anthropic = resolve_capabilities("https://api.anthropic.com/v1/", "claude-opus-4-8")
    assert anthropic.reasoning_dialect is ReasoningDialect.ANTHROPIC_COMPAT

    openrouter = resolve_capabilities(
        "https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8"
    )
    assert openrouter.reasoning_dialect is ReasoningDialect.OPENROUTER

    for capability in (openai, deepseek, anthropic, openrouter):
        assert capability.source_urls
        assert capability.registry_version == CAPABILITY_REGISTRY_VERSION
        assert capability.model_dump(mode="json")["source_urls"]


def test_the_two_streaming_bits_stay_out_of_the_table() -> None:
    """**2026-08-13 删掉的那两位，别加回来。**

    这条原来叫 `test_registry_only_claims_stream_usage_with_recorded_support`：
    它钉的是「只有 OpenAI 那四条敢声称 `supports_stream_usage`，别家一律 `None`」。
    那个形状本身就是病灶 —— 作者自己那条路一开可中断，每一稿的 token 数就退成「未记录」，
    而「停」和「边写边看」在任何自选端点上一起哑掉。

    换掉它的判据是**协议**，不是登记：

    * `stream` 是 OpenAI Chat Completions 的**基本功能**，自称兼容就得支持
      （对照过 Cursor：它根本不存这一位，Verify 按钮发的就是一次流式请求）；
    * `stream_options` **无条件发**，端点不认就忽略 ⇒ 没有用量 ⇒ 读取侧落 `None`，
      本来就 fail-safe；真严格拒绝（400）的那一档由 `provider.py` 退一次并记住。

    **要加回来之前先回答一个问题**：这一位是「学得会的」还是「学不会的」？
    学得会的（一次失败就知道）不进表 —— 表要装的是 `max_context_tokens` 那种
    **测不起**的东西。
    """
    fields = set(ProviderCapabilities.model_fields)
    assert "supports_streaming" not in fields
    assert "supports_stream_usage" not in fields

    for route in (
        ("https://api.openai.com/v1", "gpt-5.6"),
        ("https://api.deepseek.com", "deepseek-v4-pro"),
        ("https://api.anthropic.com/v1", "claude-opus-4-8"),
        ("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8"),
    ):
        assert resolve_capabilities(*route).source != "unknown"


@pytest.mark.parametrize(
    "source_url",
    [
        "https://example.test/model?token=must-not-leak",
        "https://example.test/model#signed-secret",
    ],
)
def test_capability_source_urls_reject_query_and_fragment_secrets(
    source_url: str,
) -> None:
    with pytest.raises(ValidationError, match="query or fragment"):
        _caps(source_urls=(source_url,))


def test_resolution_is_exact_after_narrow_url_normalization() -> None:
    known = resolve_capabilities(
        "  HTTPS://OPENROUTER.AI:443/api/v1/  ", "  anthropic/claude-opus-4.8  "
    )
    assert known.source != "unknown"

    wrong_path = resolve_capabilities(
        "https://openrouter.ai/other/v1", "anthropic/claude-opus-4.8"
    )
    wrong_case = resolve_capabilities(
        "https://openrouter.ai/api/v1", "Anthropic/claude-opus-4.8"
    )
    missing_prefix = resolve_capabilities(
        "https://openrouter.ai/api/v1", "claude-opus-4-8"
    )
    unsupported_deepseek_alias = resolve_capabilities(
        "https://api.deepseek.com/v1", "deepseek-v4-pro"
    )
    assert {
        wrong_path.source,
        wrong_case.source,
        missing_prefix.source,
        unsupported_deepseek_alias.source,
    } == {"unknown"}
    assert wrong_path.base_url.endswith("/other/v1")


def test_override_registry_metadata_unknown_precedence_and_binding() -> None:
    route = ("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    override = _caps(
        base_url=route[0],
        model=route[1],
        source="operator-override",
        max_output_tokens=99_999,
    )
    metadata = _caps(
        base_url=route[0], model=route[1], source="endpoint-metadata", max_output_tokens=88_888
    )
    assert resolve_capabilities(*route, operator_override=override, metadata=metadata) is override
    assert resolve_capabilities(*route, metadata=metadata).source != "endpoint-metadata"

    unregistered = ("https://models.example/v1", "exact-model")
    exact_metadata = _caps(base_url=unregistered[0], model=unregistered[1], source="metadata")
    assert resolve_capabilities(*unregistered, metadata=exact_metadata) is exact_metadata
    unknown = resolve_capabilities(*unregistered)
    assert unknown.source == "unknown"
    assert unknown.max_tokens_field == "max_tokens"
    assert unknown.max_tokens_field_source == "compat_default"

    mismatched = _caps(base_url="https://other.example/v1", model=route[1])
    with pytest.raises(CapabilityError, match="does not match"):
        resolve_capabilities(*route, operator_override=mismatched)
    with pytest.raises(CapabilityError, match="does not match"):
        resolve_capabilities(*route, metadata=mismatched)


@pytest.mark.parametrize(
    "updates",
    [
        pytest.param(
            {"source_urls": ("https://example.test/model",)}, id="source-urls"
        ),
        pytest.param({"max_context_tokens": 100_000}, id="context-limit"),
        pytest.param({"max_output_tokens": 80_000}, id="output-limit"),
        pytest.param(
            {"max_tokens_field": "max_completion_tokens"}, id="token-field"
        ),
        pytest.param({"max_tokens_field_source": "declared"}, id="token-source"),
        pytest.param(
            {"reasoning_levels": frozenset(ReasoningEffort)}, id="reasoning-levels"
        ),
        pytest.param(
            {"reasoning_dialect": ReasoningDialect.OPENAI}, id="reasoning-dialect"
        ),
        pytest.param({"reasoning_shares_output": True}, id="shared-output"),
        pytest.param({"reserve_ratio_high": 0.8}, id="reserve-ratio"),
    ],
)
def test_unknown_source_requires_canonical_fail_closed_capabilities(
    updates: dict[str, object],
) -> None:
    values = resolve_capabilities(
        "http://localhost:11434/v1", "custom-model"
    ).model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match="unknown.*canonical"):
        ProviderCapabilities(**values)


def test_capability_model_enforces_cross_field_invariants() -> None:
    with pytest.raises(ValidationError, match="OFF"):
        _caps(reasoning_levels=frozenset({ReasoningEffort.HIGH}))
    with pytest.raises(ValidationError, match="NONE"):
        _caps(
            reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
            reasoning_dialect=ReasoningDialect.NONE,
        )
    with pytest.raises(ValidationError, match="reserve_ratio_high"):
        _caps(reasoning_shares_output=False, reserve_ratio_high=Decimal("0.8"))
    with pytest.raises(ValidationError, match="max_output_tokens"):
        _caps(max_context_tokens=1000, max_output_tokens=1001)
    with pytest.raises(ValidationError):
        _caps(unexpected=True)


def test_direct_call_plan_rejects_effort_not_supported_by_capability() -> None:
    with pytest.raises(ValidationError, match="reasoning.*supported"):
        _direct_plan(
            _caps(),
            reasoning_requested=ReasoningEffort.HIGH,
            reasoning_effective=ReasoningEffort.HIGH,
        )


def test_plan_call_freezes_the_length_spec_used_for_budgeting() -> None:
    plan = plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, _caps())
    assert plan.length == M2_LENGTH_SPEC


def test_direct_call_plan_rejects_unknown_budget_formula_version() -> None:
    with pytest.raises(ValidationError, match="budget formula version"):
        _direct_plan(_caps(), budget_formula_version="forged-formula-v0")


def test_direct_call_plan_rejects_visible_budget_not_derived_from_length() -> None:
    with pytest.raises(ValidationError, match="visible token budget"):
        _direct_plan(
            _caps(),
            visible_token_budget=1_027,
            required_token_budget=1_027,
            request_token_budget=1_027,
        )


def test_direct_call_plan_rejects_inflated_required_budget_without_shared_reasoning() -> None:
    with pytest.raises(ValidationError, match="required token budget"):
        _direct_plan(
            _caps(),
            required_token_budget=1_027,
            request_token_budget=1_027,
        )


def test_direct_call_plan_enforces_exact_shared_high_reserve_math() -> None:
    caps = resolve_capabilities(
        "https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8"
    )
    shared_values: dict[str, object] = {
        "length": M2_LENGTH_SPEC,
        "visible_token_budget": 7_224,
        "request_token_budget": 40_000,
        "reasoning_requested": ReasoningEffort.HIGH,
        "reasoning_effective": ReasoningEffort.HIGH,
        "stream": True,
    }
    exact = _direct_plan(caps, required_token_budget=36_120, **shared_values)
    assert exact.required_token_budget == 36_120

    with pytest.raises(ValidationError, match="required token budget"):
        _direct_plan(caps, required_token_budget=36_121, **shared_values)


def test_direct_call_plan_rejects_shared_reasoning_without_audited_ratio() -> None:
    caps = _caps(
        reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
        reasoning_dialect=ReasoningDialect.OPENAI,
        reasoning_shares_output=True,
        reserve_ratio_high=None,
    )
    with pytest.raises(ValidationError, match="audited reserve ratio"):
        _direct_plan(
            caps,
            reasoning_requested=ReasoningEffort.HIGH,
            reasoning_effective=ReasoningEffort.HIGH,
        )


def test_direct_call_plan_rejects_request_above_output_limit() -> None:
    with pytest.raises(ValidationError, match="model max output"):
        _direct_plan(
            _caps(max_output_tokens=2_000),
            request_token_budget=2_001,
        )


def test_direct_call_plan_rejects_prompt_plus_request_above_context_limit() -> None:
    with pytest.raises(ValidationError, match="context window"):
        _direct_plan(
            _caps(max_context_tokens=3_000, max_output_tokens=2_000),
            prompt_token_budget=1_975,
        )


def test_a_big_direct_plan_streams_without_asking_the_table_first() -> None:
    """**2026-08-13：「没登记就不许流式」那道校验删了**（`_streams` 的 docstring 写了为什么）。

    原来这条参数化跑 `supports_streaming` 的 `False` / `None` 两档，断言 `ResolvedCallPlan`
    在校验器上炸。那个字段本身已经不在模型上了：**`stream` 是 OpenAI 兼容协议的基本功能**。
    留下正面断言，钉住换过来的行为。
    """
    plan = _direct_plan(_caps(), request_token_budget=16_001, stream=True)
    assert plan.stream is True


def test_shared_reasoning_without_a_documented_ratio_is_recordable_but_not_plannable() -> None:
    caps = _caps(
        reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
        reasoning_dialect=ReasoningDialect.OPENAI,
        reasoning_shares_output=True,
        reserve_ratio_high=None,
    )
    with pytest.raises(CapabilityError, match="audited reserve ratio"):
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)


def test_openrouter_opus_high_uses_exact_shared_reserve_math() -> None:
    caps = resolve_capabilities("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    plan = plan_call(
        M2_LENGTH_SPEC,
        ReasoningEffort.HIGH,
        caps,
        request_token_budget=40_000,
        prompt_token_budget=2_000,
    )
    assert plan.visible_token_budget == 7_224
    assert plan.required_token_budget == 36_120
    assert plan.request_token_budget == 40_000
    assert plan.reasoning_effective is ReasoningEffort.HIGH
    assert plan.reasoning_dialect is ReasoningDialect.OPENROUTER
    assert plan.stream is True

    automatic = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)
    assert automatic.required_token_budget == 36_120
    assert automatic.request_token_budget == 40_000


def test_unknown_allows_off_but_rejects_requested_reasoning() -> None:
    caps = resolve_capabilities("http://localhost:11434/v1", "custom-model")
    off = plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, caps)
    assert off.request_token_budget == 7_224
    assert off.max_tokens_field == "max_tokens"
    with pytest.raises(CapabilityError, match="reasoning"):
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)


def test_explicit_budget_and_model_limit_are_never_silently_clamped() -> None:
    with pytest.raises(CapabilityError, match="required.*7224"):
        plan_call(
            M2_LENGTH_SPEC,
            ReasoningEffort.OFF,
            _caps(),
            request_token_budget=7_223,
        )
    with pytest.raises(CapabilityError, match="4096"):
        plan_call(
            M2_LENGTH_SPEC,
            ReasoningEffort.OFF,
            _caps(max_output_tokens=4_096),
        )


def test_streaming_threshold_is_strictly_greater_than_16000() -> None:
    spec = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=1, max_units=7_488)
    at_threshold = plan_call(
        spec, ReasoningEffort.OFF, _caps(), request_token_budget=16_000
    )
    assert at_threshold.visible_token_budget == 16_000
    assert at_threshold.stream is False

    above = plan_call(spec, ReasoningEffort.OFF, _caps(), request_token_budget=16_001)
    assert above.stream is True
    # **2026-08-13 起大预算不再需要「登记过支持流式」**：那道 fail-closed 拒绝删了。
    assert plan_call(
        spec, ReasoningEffort.OFF, _caps(), request_token_budget=16_001
    ).stream is True


def test_prompt_plus_request_budget_obeys_context_boundary() -> None:
    spec = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=1, max_units=7_488)
    caps = _caps(max_context_tokens=20_000, max_output_tokens=20_000)
    exact = plan_call(
        spec,
        ReasoningEffort.OFF,
        caps,
        request_token_budget=16_000,
        prompt_token_budget=4_000,
    )
    assert exact.prompt_token_budget + exact.request_token_budget == 20_000
    with pytest.raises(CapabilityError, match="context"):
        plan_call(
            spec,
            ReasoningEffort.OFF,
            caps,
            request_token_budget=16_000,
            prompt_token_budget=4_001,
        )


def test_registry_is_immutable_and_does_not_fabricate_unpublished_reserve_ratios() -> None:
    assert isinstance(CAPABILITY_REGISTRY, MappingProxyType)
    assert CAPABILITY_REGISTRY
    for route, capability in CAPABILITY_REGISTRY.items():
        assert route == capability.route
        assert capability.source_urls
        assert capability.max_tokens_field_source == "declared"

    for route in (
        ("https://api.openai.com/v1", "gpt-5.6"),
        ("https://api.anthropic.com/v1", "claude-opus-4-8"),
    ):
        capability = resolve_capabilities(*route)
        assert capability.reasoning_shares_output is True
        assert capability.reserve_ratio_high is None
        with pytest.raises(CapabilityError, match="audited reserve ratio"):
            plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)

    with pytest.raises(TypeError):
        CAPABILITY_REGISTRY[("https://example.test/v1", "x")] = _caps()  # type: ignore[index]


def test_deepseek_v4_high_uses_the_audited_shared_reserve_math() -> None:
    """2026-08-02 冻结：DeepSeek V4 的 thinking 与正文共享输出预算，按 0.95 预留。

    visible = ceil(3100*2) + 1024 = 7224；
    required = ceil(7224 / (1-0.95)) = 144480；
    request 向上取整到 150000 > 16000，必须走 streaming。
    证据见 docs/M2_ENDPOINT_PROFILE.md。
    """
    capability = resolve_capabilities("https://api.deepseek.com", "deepseek-v4-flash")
    assert capability.reserve_ratio_high == 0.95
    plan = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)
    assert plan.visible_token_budget == 7_224
    assert plan.required_token_budget == 144_480
    assert plan.request_token_budget == 150_000
    assert plan.stream is True
    assert plan.reasoning_dialect is ReasoningDialect.DEEPSEEK


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://user@example.test/v1",
        "https://example.test/v1?route=x",
        "https://example.test/v1#fragment",
        "ftp://example.test/v1",
    ],
)
def test_unsafe_registry_urls_are_rejected(unsafe: str) -> None:
    with pytest.raises(CapabilityError):
        resolve_capabilities(unsafe, "model")


def test_runtime_options_default_to_frozen_fail_closed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "NH_LLM_REASONING_EFFORT",
        "NH_LLM_MAX_CONTEXT_TOKENS",
        "NH_LLM_MAX_OUTPUT_TOKENS",
        "NH_LLM_DIALECT",
    ):
        monkeypatch.delenv(name, raising=False)

    runtime_type = capability_module.ProviderRuntimeOptions
    options = runtime_type.from_env()
    assert options.reasoning_effort is ReasoningEffort.OFF
    assert options.max_context_tokens is None
    assert options.max_output_tokens is None
    assert options.reasoning_dialect is None
    assert "ProviderRuntimeOptions" in capability_module.__all__
    with pytest.raises(ValidationError, match="frozen"):
        options.reasoning_effort = ReasoningEffort.HIGH


def test_runtime_options_parse_only_the_documented_environment_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NH_LLM_REASONING_EFFORT", "high")
    monkeypatch.setenv("NH_LLM_MAX_CONTEXT_TOKENS", "1000000")
    monkeypatch.setenv("NH_LLM_MAX_OUTPUT_TOKENS", "128000")
    monkeypatch.setenv("NH_LLM_DIALECT", "openrouter")

    options = capability_module.ProviderRuntimeOptions.from_env()
    assert options.reasoning_effort is ReasoningEffort.HIGH
    assert options.max_context_tokens == 1_000_000
    assert options.max_output_tokens == 128_000
    assert options.reasoning_dialect is ReasoningDialect.OPENROUTER


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NH_LLM_MAX_CONTEXT_TOKENS", "0"),
        ("NH_LLM_MAX_CONTEXT_TOKENS", "-1"),
        ("NH_LLM_MAX_CONTEXT_TOKENS", "1.0"),
        ("NH_LLM_MAX_OUTPUT_TOKENS", "not-an-integer"),
        ("NH_LLM_MAX_OUTPUT_TOKENS", ""),
    ],
)
def test_runtime_options_reject_non_positive_or_non_integer_limits(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        capability_module.ProviderRuntimeOptions.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NH_LLM_REASONING_EFFORT", "ultra"),
        ("NH_LLM_DIALECT", "claude"),
    ],
)
def test_runtime_options_reject_unknown_enums(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        capability_module.ProviderRuntimeOptions.from_env()


def test_runtime_options_reject_output_limit_above_context_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NH_LLM_MAX_CONTEXT_TOKENS", "64000")
    monkeypatch.setenv("NH_LLM_MAX_OUTPUT_TOKENS", "128000")
    with pytest.raises(ValidationError, match="max_output_tokens.*max_context_tokens"):
        capability_module.ProviderRuntimeOptions.from_env()


def test_runtime_options_do_not_upgrade_unknown_endpoint_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NH_LLM_REASONING_EFFORT", "high")
    monkeypatch.setenv("NH_LLM_MAX_CONTEXT_TOKENS", "1000000")
    monkeypatch.setenv("NH_LLM_MAX_OUTPUT_TOKENS", "128000")
    monkeypatch.setenv("NH_LLM_DIALECT", "openai")
    options = capability_module.ProviderRuntimeOptions.from_env()
    unknown = resolve_capabilities("http://localhost:11434/v1", "custom-model")

    assert options.reasoning_effort is ReasoningEffort.HIGH
    assert unknown.reasoning_levels == frozenset({ReasoningEffort.OFF})
    assert unknown.reasoning_dialect is ReasoningDialect.NONE
    with pytest.raises(CapabilityError, match="reasoning"):
        plan_call(M2_LENGTH_SPEC, options.reasoning_effort, unknown)
