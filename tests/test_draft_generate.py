"""One-continuation orchestration for bilingual draft generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from novel_harness.draft.capabilities import (
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
)
from novel_harness.draft.generate import (
    DraftAttempt,
    DraftResult,
    continuation_instruction,
    generate_draft,
    validate_generation_plan,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec, LengthStatus, M2_LENGTH_SPEC
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ProviderError


@pytest.fixture
def config() -> ProviderConfig:
    return ProviderConfig(
        base_url="http://localhost:11434/v1",
        model="test-model",
        api_key="not-needed",
    )


def _plan(length: LengthSpec) -> ResolvedCallPlan:
    capability = ProviderCapabilities(
        base_url="http://localhost:11434/v1",
        model="test-model",
        source="operator-test",
        source_urls=("https://example.test/model-card",),
        max_context_tokens=32_000,
        max_output_tokens=8_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        supports_streaming=True,
        supports_stream_usage=False,
    )
    return plan_call(length, ReasoningEffort.OFF, capability)


class ScriptedComplete:
    """Record the real generator boundary while returning deterministic provider results."""

    def __init__(self, *results: CompletionResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        config: ProviderConfig,
        plan: ResolvedCallPlan,
        client: Any = None,
    ) -> CompletionResult:
        self.calls.append(
            {
                "messages": tuple(dict(message) for message in messages),
                "config": config,
                "plan": plan,
                "client": client,
            }
        )
        return self._results.pop(0)


def _install(
    monkeypatch: pytest.MonkeyPatch, *results: CompletionResult
) -> ScriptedComplete:
    from novel_harness.draft import generate as generate_module

    scripted = ScriptedComplete(*results)
    monkeypatch.setattr(generate_module, "complete", scripted)
    return scripted


def test_within_range_initial_response_stops_after_one_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    plan = _plan(length)
    messages = [{"role": "user", "content": "写一个场景。"}]
    client = object()
    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲乙丙", model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        messages,
        length=length,
        config=config,
        plan=plan,
        client=client,
    )

    assert result.text == "甲乙丙"
    assert result.length.actual_units == 3
    assert result.length.status is LengthStatus.WITHIN
    assert len(result.attempts) == 1
    assert result.attempts[0].number == 1
    assert result.attempts[0].messages == tuple(messages)
    assert result.attempts[0].measurement == result.length
    assert result.truncated is False
    assert scripted.calls == [
        {
            "messages": tuple(messages),
            "config": config,
            "plan": plan,
            "client": client,
        }
    ]


def test_mismatched_length_plan_is_rejected_before_any_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    planned_length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    requested_length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=4,
        target_units=5,
        max_units=6,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲乙丙丁", model="test-model", finish_reason="stop"),
    )

    with pytest.raises(ValueError, match="length.*plan"):
        generate_draft(
            [{"role": "user", "content": "写。"}],
            length=requested_length,
            config=config,
            plan=_plan(planned_length),
        )

    assert scripted.calls == []


def test_generation_plan_route_must_match_config_before_any_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    wrong_config = config.model_copy(update={"model": "another-model"})
    scripted = _install(
        monkeypatch,
        CompletionResult(text="不应调用", model="test-model"),
    )

    with pytest.raises(ValueError, match="route.*config"):
        validate_generation_plan(
            length=length,
            config=wrong_config,
            plan=_plan(length),
        )

    with pytest.raises(ValueError, match="route.*config"):
        generate_draft(
            [{"role": "user", "content": "写。"}],
            length=length,
            config=wrong_config,
            plan=_plan(length),
        )
    assert scripted.calls == []


def test_attempt_messages_are_recursively_immutable_and_json_serializable(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    original: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "写。"}],
            "metadata": {"tags": ["frozen"]},
        }
    ]
    _install(
        monkeypatch,
        CompletionResult(text="甲", model="test-model", finish_reason="stop"),
        CompletionResult(text="乙丙", model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        original,
        length=length,
        config=config,
        plan=_plan(length),
    )
    original[0]["content"][0]["text"] = "调用方篡改"
    original[0]["metadata"]["tags"].append("调用方篡改")

    for attempt in result.attempts:
        assert attempt.messages[0]["content"][0]["text"] == "写。"
        assert tuple(attempt.messages[0]["metadata"]["tags"]) == ("frozen",)
        with pytest.raises(TypeError):
            attempt.messages[0]["content"][0]["text"] = "证据篡改"

    dumped = result.model_dump(mode="json")
    assert dumped["attempts"][0]["messages"][0]["content"] == [
        {"type": "text", "text": "写。"}
    ]


def test_attempt_models_publish_json_schema() -> None:
    attempt_schema = DraftAttempt.model_json_schema()
    assert attempt_schema["properties"]["messages"]["type"] == "array"
    assert attempt_schema["properties"]["messages"]["items"]["type"] == "object"

    result_schema = DraftResult.model_json_schema()
    assert "DraftAttempt" in result_schema["$defs"]


def test_transport_cannot_mutate_the_audit_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    from novel_harness.draft import generate as generate_module

    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )

    def mutating_complete(
        messages: Sequence[dict[str, Any]],
        *,
        config: ProviderConfig,
        plan: ResolvedCallPlan,
        client: Any = None,
    ) -> CompletionResult:
        messages[0]["content"][0]["text"] = "transport mutation"
        return CompletionResult(text="甲乙丙", model=config.model)

    monkeypatch.setattr(generate_module, "complete", mutating_complete)
    result = generate_draft(
        [{"role": "user", "content": [{"type": "text", "text": "写。"}]}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert result.attempts[0].messages[0]["content"][0]["text"] == "写。"


def test_attempt_observer_receives_the_first_attempt_before_continuation_failure(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    from novel_harness.draft import generate as generate_module

    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    observed: list[DraftAttempt] = []
    calls = 0

    def failing_second_complete(
        messages: Sequence[dict[str, Any]],
        *,
        config: ProviderConfig,
        plan: ResolvedCallPlan,
        client: Any = None,
    ) -> CompletionResult:
        nonlocal calls
        del messages, config, plan, client
        calls += 1
        if calls == 1:
            return CompletionResult(text="甲", model="test-model")
        assert [attempt.number for attempt in observed] == [1]
        raise ProviderError("continuation transport failed")

    monkeypatch.setattr(generate_module, "complete", failing_second_complete)

    with pytest.raises(ProviderError, match="continuation transport failed"):
        generate_draft(
            [{"role": "user", "content": "写。"}],
            length=length,
            config=config,
            plan=_plan(length),
            on_attempt=observed.append,
        )

    assert calls == 2
    assert [attempt.number for attempt in observed] == [1]
    assert observed[0].result.text == "甲"


def test_attempt_observer_failure_prevents_a_continuation_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲", model="test-model"),
        CompletionResult(text="不应调用", model="test-model"),
    )

    def persistence_failure(attempt: DraftAttempt) -> None:
        assert attempt.number == 1
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        generate_draft(
            [{"role": "user", "content": "写。"}],
            length=length,
            config=config,
            plan=_plan(length),
            on_attempt=persistence_failure,
        )

    assert len(scripted.calls) == 1


def test_continuation_capacity_is_checked_before_the_first_paid_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    capability = ProviderCapabilities(
        base_url=config.base_url,
        model=config.model,
        source="operator-test",
        source_urls=("https://example.test/model-card",),
        # Initial prompt(100) + request(1034) fits. The old unit-based reserve also
        # fit (2164), but a zero-unit response may still consume all 1034 tokens;
        # adding the fixed continuation overhead and second request does not fit.
        max_context_tokens=2_500,
        max_output_tokens=1_400,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        supports_streaming=True,
        supports_stream_usage=False,
    )
    plan = plan_call(
        length,
        ReasoningEffort.OFF,
        capability,
        prompt_token_budget=100,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(text="不应调用", model="test-model"),
    )

    with pytest.raises(ValueError, match="continuation.*context"):
        generate_draft(
            [{"role": "user", "content": "写。"}],
            length=length,
            config=config,
            plan=plan,
        )
    assert scripted.calls == []


@pytest.mark.parametrize(
    ("length", "first", "second", "expected_text"),
    [
        (
            LengthSpec(
                language=DraftLanguage.ZH,
                min_units=3,
                target_units=4,
                max_units=5,
            ),
            "甲",
            "乙丙",
            "甲乙丙",
        ),
        (
            LengthSpec(
                language=DraftLanguage.EN,
                min_units=3,
                target_units=4,
                max_units=5,
            ),
            "One",
            " two three",
            "One two three",
        ),
    ],
)
def test_under_length_uses_one_fixed_language_specific_continuation(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
    length: LengthSpec,
    first: str,
    second: str,
    expected_text: str,
) -> None:
    plan = _plan(length)
    original = (
        {"role": "system", "content": "Write prose."},
        {"role": "user", "content": "Continue the scene."},
    )
    client = object()
    scripted = _install(
        monkeypatch,
        CompletionResult(text=first, model="test-model", finish_reason="stop"),
        CompletionResult(text=second, model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        original,
        length=length,
        config=config,
        plan=plan,
        client=client,
    )

    expected_continuation = original + (
        {"role": "assistant", "content": first},
        {"role": "user", "content": continuation_instruction(length)},
    )
    assert len(scripted.calls) == 2
    assert scripted.calls[0]["messages"] == original
    assert scripted.calls[1]["messages"] == expected_continuation
    assert scripted.calls[0]["config"] is config
    assert scripted.calls[1]["config"] is config
    assert scripted.calls[0]["plan"] is plan
    assert scripted.calls[1]["plan"] is plan
    assert scripted.calls[0]["client"] is client
    assert scripted.calls[1]["client"] is client
    assert tuple(attempt.messages for attempt in result.attempts) == (
        original,
        expected_continuation,
    )
    assert result.text == expected_text
    assert result.length.status is LengthStatus.WITHIN
    assert result.truncated is False


def test_continuation_instruction_embeds_the_frozen_length_band() -> None:
    """2026-08-02 修：续写指令必须带长度档与硬上限，否则第二次调用会盲目续写、
    累计总长冲破 max_units（第一份真实 run 因此 INVALID）。"""
    zh = continuation_instruction(M2_LENGTH_SPEC)
    assert "2000–3000" in zh
    assert "目标约 2500" in zh
    assert "不得超过 3000" in zh
    assert "不要重新开始" in zh
    # 同一份 spec → 恒定文本；M2 整轮 225 个 cell 用的都是这一份。
    assert continuation_instruction(M2_LENGTH_SPEC) == zh

    en = continuation_instruction(
        LengthSpec(
            language=DraftLanguage.EN,
            min_units=1200,
            target_units=1500,
            max_units=1800,
        )
    )
    assert "1200" in en and "1800" in en
    assert "must not exceed 1800" in en


def test_final_under_length_never_makes_a_third_call(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲", model="test-model", finish_reason="stop"),
        CompletionResult(text="乙", model="test-model", finish_reason="stop"),
        CompletionResult(text="不应调用", model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert result.text == "甲乙"
    assert result.length.actual_units == 2
    assert result.length.status is LengthStatus.UNDER
    assert len(result.attempts) == 2
    assert len(scripted.calls) == 2


def test_over_length_initial_response_is_retained_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    full_paid_text = "甲乙丙丁戊己"
    scripted = _install(
        monkeypatch,
        CompletionResult(text=full_paid_text, model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert result.text == full_paid_text
    assert result.length.actual_units == 6
    assert result.length.status is LengthStatus.OVER
    assert len(result.attempts) == 1
    assert len(scripted.calls) == 1


def test_tell_like_content_does_not_trigger_a_retry_when_length_is_valid(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.EN,
        min_units=4,
        target_units=6,
        max_units=10,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(
            text="I know the hidden answer.",
            model="test-model",
            finish_reason="stop",
        ),
    )

    result = generate_draft(
        [{"role": "user", "content": "Write."}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert result.length.status is LengthStatus.WITHIN
    assert len(result.attempts) == 1
    assert len(scripted.calls) == 1


def test_truncated_uses_only_the_last_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲", model="test-model", finish_reason="length"),
        CompletionResult(text="乙丙", model="test-model", finish_reason="stop"),
    )

    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert len(scripted.calls) == 2
    assert result.truncated is False

    scripted = _install(
        monkeypatch,
        CompletionResult(text="甲", model="test-model", finish_reason="stop"),
        CompletionResult(text="乙丙", model="test-model", finish_reason="length"),
    )
    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )
    assert len(scripted.calls) == 2
    assert result.truncated is True


def test_usage_totals_sum_only_values_the_provider_reported(
    monkeypatch: pytest.MonkeyPatch,
    config: ProviderConfig,
) -> None:
    length = LengthSpec(
        language=DraftLanguage.ZH,
        min_units=3,
        target_units=4,
        max_units=5,
    )
    _install(
        monkeypatch,
        CompletionResult(
            text="甲",
            model="test-model",
            prompt_tokens=None,
            completion_tokens=4,
        ),
        CompletionResult(
            text="乙丙",
            model="test-model",
            prompt_tokens=3,
            completion_tokens=None,
        ),
    )

    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )

    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4

    scripted = _install(
        monkeypatch,
        CompletionResult(
            text="甲乙丙",
            model="test-model",
            prompt_tokens=None,
            completion_tokens=None,
        ),
    )
    result = generate_draft(
        [{"role": "user", "content": "写。"}],
        length=length,
        config=config,
        plan=_plan(length),
    )
    assert len(scripted.calls) == 1
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
