"""Provider capability resolution and deterministic output-budget planning."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from novel_harness.draft.capabilities import (
    CAPABILITY_REGISTRY,
    CAPABILITY_REGISTRY_VERSION,
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec, M2_LENGTH_SPEC


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
        "supports_streaming": True,
        "supports_stream_usage": False,
    }
    values.update(updates)
    return ProviderCapabilities(**values)


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


def test_capability_model_enforces_cross_field_invariants() -> None:
    with pytest.raises(ValidationError, match="OFF"):
        _caps(reasoning_levels=frozenset({ReasoningEffort.HIGH}))
    with pytest.raises(ValidationError, match="NONE"):
        _caps(
            reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
            reasoning_dialect=ReasoningDialect.NONE,
        )
    with pytest.raises(ValidationError, match="stream usage"):
        _caps(supports_streaming=False, supports_stream_usage=True)
    with pytest.raises(ValidationError, match="reserve_ratio_high"):
        _caps(reasoning_shares_output=False, reserve_ratio_high=Decimal("0.8"))
    with pytest.raises(ValidationError, match="max_output_tokens"):
        _caps(max_context_tokens=1000, max_output_tokens=1001)
    with pytest.raises(ValidationError):
        _caps(unexpected=True)


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
    assert plan.visible_token_budget == 7_024
    assert plan.required_token_budget == 35_120
    assert plan.request_token_budget == 40_000
    assert plan.reasoning_effective is ReasoningEffort.HIGH
    assert plan.reasoning_dialect is ReasoningDialect.OPENROUTER
    assert plan.stream is True

    automatic = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)
    assert automatic.required_token_budget == 35_120
    assert automatic.request_token_budget == 40_000


def test_unknown_allows_off_but_rejects_requested_reasoning() -> None:
    caps = resolve_capabilities("http://localhost:11434/v1", "custom-model")
    off = plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, caps)
    assert off.request_token_budget == 7_024
    assert off.max_tokens_field == "max_tokens"
    with pytest.raises(CapabilityError, match="reasoning"):
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, caps)


def test_explicit_budget_and_model_limit_are_never_silently_clamped() -> None:
    with pytest.raises(CapabilityError, match="required.*7024"):
        plan_call(
            M2_LENGTH_SPEC,
            ReasoningEffort.OFF,
            _caps(),
            request_token_budget=7_023,
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
    for support in (False, None):
        with pytest.raises(CapabilityError, match="streaming"):
            plan_call(
                spec,
                ReasoningEffort.OFF,
                _caps(supports_streaming=support),
                request_token_budget=16_001,
            )


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
        ("https://api.deepseek.com", "deepseek-v4-pro"),
        ("https://api.anthropic.com/v1", "claude-opus-4-8"),
    ):
        capability = resolve_capabilities(*route)
        assert capability.reasoning_shares_output is True
        assert capability.reserve_ratio_high is None
        with pytest.raises(CapabilityError, match="audited reserve ratio"):
            plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)

    with pytest.raises(TypeError):
        CAPABILITY_REGISTRY[("https://example.test/v1", "x")] = _caps()  # type: ignore[index]


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
