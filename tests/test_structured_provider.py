"""Structured-output calls share the audited provider path without fake prose lengths."""

from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

import novel_harness.draft.capabilities as capability_module
from novel_harness.draft.capabilities import (
    BUDGET_FORMULA_VERSION,
    CapabilityError,
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
    resolve_capabilities,
)
from novel_harness.draft.length import M2_LENGTH_SPEC
from novel_harness.draft.provider import ProviderConfig, _wire_kwargs, complete


LOCAL = "http://localhost:11434/v1"


def _caps(**updates: object) -> ProviderCapabilities:
    values: dict[str, object] = {
        "base_url": LOCAL,
        "model": "structured-test-model",
        "source": "operator-test",
        "source_urls": ("https://example.test/model",),
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


def _plan(
    *,
    visible_token_budget: int = 4_096,
    reasoning: ReasoningEffort = ReasoningEffort.OFF,
    capability: ProviderCapabilities | None = None,
    prompt_token_budget: int = 0,
    request_token_budget: int | None = None,
):
    return capability_module.plan_structured_call(
        visible_token_budget,
        reasoning,
        capability or _caps(),
        prompt_token_budget=prompt_token_budget,
        request_token_budget=request_token_budget,
    )


def test_structured_plan_is_caller_budgeted_without_a_length_spec() -> None:
    assert hasattr(capability_module, "StructuredCallPlan")
    plan = _plan(visible_token_budget=4_096, prompt_token_budget=512)

    assert plan.visible_token_budget == 4_096
    assert plan.required_token_budget == 4_096
    assert plan.request_token_budget == 4_096
    assert plan.prompt_token_budget == 512
    assert plan.stream is False
    assert "length" not in type(plan).model_fields
    assert plan.model_config["frozen"] is True
    assert plan.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError, match="frozen"):
        plan.request_token_budget = 10  # type: ignore[misc]


def test_structured_high_reasoning_uses_the_audited_shared_reserve_and_rounding() -> None:
    capability = resolve_capabilities("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    plan = _plan(
        visible_token_budget=7_224,
        reasoning=ReasoningEffort.HIGH,
        capability=capability,
    )

    assert plan.required_token_budget == 36_120
    assert plan.request_token_budget == 40_000
    assert plan.stream is True


def test_structured_explicit_request_is_never_clamped_or_downgraded() -> None:
    capability = resolve_capabilities("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    with pytest.raises(CapabilityError, match="below required budget 36120"):
        _plan(
            visible_token_budget=7_224,
            reasoning=ReasoningEffort.HIGH,
            capability=capability,
            request_token_budget=36_119,
        )
    with pytest.raises(CapabilityError, match="model max output 8000"):
        _plan(
            visible_token_budget=8_001,
            capability=_caps(max_output_tokens=8_000),
        )
    with pytest.raises(CapabilityError, match="reasoning=high.*not supported"):
        _plan(reasoning=ReasoningEffort.HIGH)


def test_structured_context_boundary_is_exact() -> None:
    capability = _caps(max_context_tokens=10_000, max_output_tokens=8_000)
    exact = _plan(
        visible_token_budget=8_000,
        capability=capability,
        prompt_token_budget=2_000,
    )
    assert exact.prompt_token_budget + exact.request_token_budget == 10_000

    with pytest.raises(CapabilityError, match="context window 10000"):
        _plan(
            visible_token_budget=8_000,
            capability=capability,
            prompt_token_budget=2_001,
        )


def test_a_big_structured_call_streams_without_asking_the_table_first() -> None:
    """**2026-08-13 起「没登记就不许流式」那道拒绝没了**（`_streams` 的 docstring 写了为什么）。

    这条原来叫 `test_structured_rejects_required_streaming_without_known_support`，
    参数化跑 `supports_streaming` 的 `False` / `None` 两档，断言 `plan_structured_call`
    当场 `CapabilityError`。那两档连同字段本身一起删了：**`stream` 是 OpenAI 兼容协议的
    基本功能**，不是要逐条登记的扩展。

    留下这条正面断言，钉住换过来的行为：预算过阈值就流式，**不再问表**。
    """
    plan = _plan(visible_token_budget=16_001)
    assert plan.stream is True


def test_structured_unknown_capability_fails_closed_even_with_reasoning_off() -> None:
    unknown = resolve_capabilities(LOCAL, "unknown-model")
    assert unknown.source == "unknown"
    with pytest.raises(CapabilityError, match="unknown capability"):
        _plan(capability=unknown)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visible_token_budget", True),
        ("visible_token_budget", 0),
        ("prompt_token_budget", True),
        ("prompt_token_budget", -1),
        ("request_token_budget", True),
        ("request_token_budget", 0),
    ],
)
def test_structured_planner_rejects_bool_and_invalid_integer_budgets(
    field: str, value: object
) -> None:
    kwargs = {field: value}
    with pytest.raises(CapabilityError, match=field):
        _plan(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base_url", "https://other.test/v1", "route"),
        ("max_tokens_field", "max_completion_tokens", "token field"),
        ("reasoning_dialect", ReasoningDialect.OPENAI, "reasoning dialect"),
        ("reasoning_effective", ReasoningEffort.HIGH, "silently downgraded"),
        ("required_token_budget", 4_097, "required token budget"),
        ("stream", True, "stream.*threshold"),
    ],
)
def test_structured_plan_revalidates_tampered_payloads(
    field: str, value: object, message: str
) -> None:
    payload = _plan().model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        capability_module.StructuredCallPlan.model_validate(payload)


def test_structured_plan_rejects_a_coerced_stream_flag() -> None:
    payload = _plan(visible_token_budget=16_001).model_dump(mode="python")
    payload["stream"] = 1

    with pytest.raises(ValidationError, match="valid boolean"):
        capability_module.StructuredCallPlan.model_validate(payload)


def test_provider_wire_shape_is_identical_for_equal_prose_and_structured_plans() -> None:
    capability = resolve_capabilities("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8")
    prose = plan_call(
        M2_LENGTH_SPEC,
        ReasoningEffort.HIGH,
        capability,
        prompt_token_budget=123,
        request_token_budget=40_000,
    )
    structured = _plan(
        visible_token_budget=prose.visible_token_budget,
        reasoning=ReasoningEffort.HIGH,
        capability=capability,
        prompt_token_budget=123,
        request_token_budget=40_000,
    )
    config = ProviderConfig(base_url=capability.base_url, model=capability.model)
    messages = [{"role": "user", "content": "JSON only"}]

    assert _wire_kwargs(config, prose, messages) == _wire_kwargs(config, structured, messages)


def test_provider_rejects_a_structured_plan_tampered_after_construction() -> None:
    plan = _plan().model_copy(update={"max_tokens_field": "max_completion_tokens"})
    config = ProviderConfig(base_url=plan.base_url, model=plan.model)

    with pytest.raises(ValidationError, match="token field"):
        _wire_kwargs(config, plan, [{"role": "user", "content": "JSON only"}])


def test_complete_rejects_a_tampered_resolved_plan_before_calling_the_client() -> None:
    calls: list[dict[str, object]] = []

    def create(**kwargs: object) -> object:
        calls.append(kwargs)
        return types.SimpleNamespace(
            model="must-not-be-called",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="unsafe"),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    valid = plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, _caps())
    tampered = valid.model_copy(update={"request_token_budget": 1})

    with pytest.raises(ValidationError, match="visible.*required.*request"):
        complete(
            [{"role": "user", "content": "must not leave the process"}],
            config=ProviderConfig(base_url=valid.base_url, model=valid.model),
            plan=tampered,
            client=client,
        )
    assert calls == []


@pytest.mark.parametrize("plan_kind", ["resolved", "structured"])
@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("stream", "false"),
        ("stream", 0),
        ("request_token_budget", "7224"),
    ],
)
def test_complete_strictly_rejects_coercible_plan_tampering_before_client_call(
    plan_kind: str, field: str, tampered_value: object
) -> None:
    calls: list[dict[str, object]] = []

    def create(**kwargs: object) -> object:
        calls.append(kwargs)
        return types.SimpleNamespace(
            model="must-not-be-called",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="unsafe"),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    valid = (
        plan_call(M2_LENGTH_SPEC, ReasoningEffort.OFF, _caps())
        if plan_kind == "resolved"
        else _plan()
    )
    tampered = valid.model_copy(update={field: tampered_value})

    with pytest.raises(ValidationError, match="valid (boolean|integer)|must be integers"):
        complete(
            [{"role": "user", "content": "must not leave the process"}],
            config=ProviderConfig(base_url=valid.base_url, model=valid.model),
            plan=tampered,
            client=client,
        )
    assert calls == []


def test_complete_accepts_a_structured_plan() -> None:
    calls: dict[str, object] = {}

    def create(**kwargs: object) -> object:
        calls.update(kwargs)
        return types.SimpleNamespace(
            model="structured-test-model",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content='{"events":[]}'),
                    finish_reason="stop",
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    plan = _plan(visible_token_budget=2_048)
    result = complete(
        [{"role": "user", "content": "JSON only"}],
        config=ProviderConfig(base_url=plan.base_url, model=plan.model),
        plan=plan,
        client=client,
    )

    assert result.text == '{"events":[]}'
    assert calls[plan.max_tokens_field] == 2_048


def test_existing_resolved_call_plan_serialization_is_unchanged() -> None:
    capability = resolve_capabilities("https://api.openai.com/v1", "gpt-5.6")
    plan = plan_call(
        M2_LENGTH_SPEC,
        ReasoningEffort.OFF,
        capability,
        prompt_token_budget=123,
    )

    assert list(plan.model_dump(mode="json")) == [
        "base_url",
        "model",
        "length",
        "prompt_token_budget",
        "visible_token_budget",
        "required_token_budget",
        "request_token_budget",
        "max_tokens_field",
        "reasoning_requested",
        "reasoning_effective",
        "reasoning_dialect",
        "stream",
        # 2026-08-12 多出来的一位（`ResolvedCallPlan.interruptible`）。
        # **它进的是 `runs/*.jsonl` 里那份 plan 记录，不是 wire**——`_wire_kwargs` 里
        # 一个字段都没多，三臂发出去的东西逐字节不变（`_streams` 的第二个理由默认关着）。
        # 记录里多一位「这一次要不要能中途停」是**多一条审计事实**，不是少一条；
        # 旧记录读回来时它取默认 `False`，也就是当时的真实情况。
        "interruptible",
        "budget_formula_version",
        "capability",
    ]
    assert plan.model_dump(mode="json", exclude={"capability"}) == {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6",
        "length": {
            "language": "zh",
            "min_units": 2_000,
            "target_units": 2_500,
            "max_units": 3_100,
        },
        "prompt_token_budget": 123,
        "visible_token_budget": 7_224,
        "required_token_budget": 7_224,
        "request_token_budget": 7_224,
        "max_tokens_field": "max_completion_tokens",
        "reasoning_requested": "off",
        "reasoning_effective": "off",
        "reasoning_dialect": "openai",
        "stream": False,
        "interruptible": False,
        "budget_formula_version": BUDGET_FORMULA_VERSION,
    }
