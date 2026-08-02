"""Deterministic one-continuation orchestration for drafted prose."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
import math
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    GetCoreSchemaHandler,
    field_serializer,
    field_validator,
)
from pydantic_core import core_schema

from .capabilities import ResolvedCallPlan
from .length import (
    DraftLanguage,
    LengthMeasurement,
    LengthSpec,
    LengthStatus,
    measure,
)
from .provider import CompletionResult, ProviderConfig, complete


def continuation_instruction(length: LengthSpec, cumulative_units: int) -> str:
    """固定模板续写指令：语言 + ``LengthSpec`` + 已写实测字数。

    2026-08-02 两连修（两次真实 run 都死在 K01/x0 的续写段）：
    1. 旧指令没有长度目标 → 续写 1,765 字、累计 3,325 → INVALID；
    2. 只给「总字数不得超过上限」不给已写数 → 模型不自己数数，续写 1,800 字、
       累计 3,598 → 仍 INVALID。
    现在把「已写 N 字、再写约 M 字、总字数区间」一次给足。``N`` 只来自确定性长度
    测量（修正案 5 裁定 2 允许续写只读长度计数），与 tell / 泄漏 / 文风 / 臂无关；
    模板恒定，指令原文随每条 attempt 落盘，可审计。
    """
    remaining_target = max(length.target_units - cumulative_units, 0)
    remaining_max = max(length.max_units - cumulative_units, 0)
    if length.language is DraftLanguage.ZH:
        return (
            "请直接接着上文续写，不要重新开始，不要概括上文，也不要评论这项请求。"
            f"目前已写 {cumulative_units} 字；请续写约 {remaining_target} 字，"
            f"使全文总字数落在 {length.min_units}–{length.max_units} 字之间，"
            f"续写段最多 {remaining_max} 字，不要超出。"
        )
    return (
        "Continue directly from the preceding text without restarting, recapping, "
        "or commenting on the request. "
        f"{cumulative_units} units are already written; continue for about "
        f"{remaining_target} more, keeping the complete draft within "
        f"{length.min_units}–{length.max_units} units and the continuation itself "
        f"under {remaining_max} units."
    )


CONTINUATION_CONTEXT_VERSION = "nh-continuation-context-v3"
CONTINUATION_PROMPT_OVERHEAD_TOKENS = 1_024


class _FrozenDict(Mapping[str, Any]):
    """Recursively immutable JSON object used for audit snapshots."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[tuple[str, Any], ...]) -> None:
        object.__setattr__(self, "_items", items)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("frozen JSON objects cannot be mutated")

    def __getitem__(self, key: str) -> Any:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return _thaw_json(self) == _thaw_json(other)

    def __repr__(self) -> str:
        return repr(_thaw_json(self))

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        del source_type, handler
        object_schema = core_schema.dict_schema(
            keys_schema=core_schema.str_schema(),
            values_schema=core_schema.any_schema(),
        )
        return core_schema.no_info_after_validator_function(
            _as_frozen_dict,
            object_schema,
            json_schema_input_schema=object_schema,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _thaw_json,
                return_schema=object_schema,
            ),
        )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, _FrozenDict):
        return value
    if isinstance(value, Mapping):
        items: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("message JSON object keys must be strings")
            items.append((key, _freeze_json(item)))
        return _FrozenDict(tuple(items))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("messages must contain only finite JSON values")


def _as_frozen_dict(value: Mapping[str, Any]) -> _FrozenDict:
    frozen = _freeze_json(value)
    if not isinstance(frozen, _FrozenDict):
        raise TypeError("each message must be a JSON object")
    return frozen


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _freeze_messages(messages: Sequence[Mapping[str, Any]]) -> tuple[_FrozenDict, ...]:
    frozen = tuple(_freeze_json(message) for message in messages)
    if not all(isinstance(message, _FrozenDict) for message in frozen):
        raise TypeError("each message must be a JSON object")
    return frozen


def _wire_messages(messages: Sequence[_FrozenDict]) -> tuple[dict[str, Any], ...]:
    return tuple(_thaw_json(message) for message in messages)


def continuation_prompt_reserve(plan: ResolvedCallPlan) -> int:
    """Worst-case first visible segment plus fixed continuation-message overhead."""
    return plan.request_token_budget + CONTINUATION_PROMPT_OVERHEAD_TOKENS


def _preflight_continuation_context(plan: ResolvedCallPlan) -> None:
    maximum = plan.capability.max_context_tokens
    if maximum is None:
        return
    required = (
        plan.prompt_token_budget
        + continuation_prompt_reserve(plan)
        + plan.request_token_budget
    )
    if required > maximum:
        raise ValueError(
            "continuation branch exceeds the known context window: "
            f"requires {required} tokens, model maximum is {maximum}"
        )


def validate_generation_plan(
    *,
    length: LengthSpec,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
) -> None:
    """Validate every deterministic generation branch without a provider call."""
    if plan.length != length:
        raise ValueError("length spec does not match the resolved call plan")
    if (plan.base_url, plan.model) != (config.base_url, config.model):
        raise ValueError("resolved call plan route does not match provider config route")
    _preflight_continuation_context(plan)


class DraftAttempt(BaseModel):
    """One provider call together with the exact request and segment measurement."""

    model_config = ConfigDict(frozen=True)

    number: int
    messages: tuple[_FrozenDict, ...]
    result: CompletionResult
    measurement: LengthMeasurement

    @field_validator("messages", mode="before")
    @classmethod
    def _freeze_message_snapshot(cls, value: Any) -> tuple[_FrozenDict, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise TypeError("messages must be a sequence of JSON objects")
        return _freeze_messages(value)

    @field_serializer("messages")
    def _serialize_messages(
        self, value: tuple[_FrozenDict, ...]
    ) -> list[dict[str, Any]]:
        return [_thaw_json(message) for message in value]


class DraftResult(BaseModel):
    """The complete paid output and every provider attempt used to produce it."""

    model_config = ConfigDict(frozen=True)

    text: str
    length: LengthMeasurement
    attempts: tuple[DraftAttempt, ...]
    truncated: bool

    @property
    def prompt_tokens(self) -> int | None:
        """Sum reported prompt usage while preserving wholly unavailable usage as null."""
        reported = (
            attempt.result.prompt_tokens
            for attempt in self.attempts
            if attempt.result.prompt_tokens is not None
        )
        values = tuple(reported)
        return sum(values) if values else None

    @property
    def completion_tokens(self) -> int | None:
        """Sum reported completion usage while preserving wholly unavailable usage as null."""
        reported = (
            attempt.result.completion_tokens
            for attempt in self.attempts
            if attempt.result.completion_tokens is not None
        )
        values = tuple(reported)
        return sum(values) if values else None


def generate_draft(
    messages: Sequence[dict[str, Any]],
    *,
    length: LengthSpec,
    config: ProviderConfig,
    plan: ResolvedCallPlan,
    client: Any = None,
    on_attempt: Callable[[DraftAttempt], None] | None = None,
) -> DraftResult:
    """Generate once and perform exactly one length-only continuation when under minimum."""
    validate_generation_plan(length=length, config=config, plan=plan)

    initial_messages = _freeze_messages(messages)
    initial_result = complete(
        _wire_messages(initial_messages),
        config=config,
        plan=plan,
        client=client,
    )
    initial_measurement = measure(initial_result.text, length)
    initial_attempt = DraftAttempt(
        number=1,
        messages=initial_messages,
        result=initial_result,
        measurement=initial_measurement,
    )
    if on_attempt is not None:
        on_attempt(initial_attempt)
    attempts = [initial_attempt]
    final_text = initial_result.text

    if initial_measurement.status is LengthStatus.UNDER:
        continuation_messages = initial_messages + _freeze_messages(
            (
                {"role": "assistant", "content": initial_result.text},
                {
                    "role": "user",
                    "content": continuation_instruction(
                        length, initial_measurement.actual_units
                    ),
                },
            )
        )
        continuation_result = complete(
            _wire_messages(continuation_messages),
            config=config,
            plan=plan,
            client=client,
        )
        continuation_attempt = DraftAttempt(
            number=2,
            messages=continuation_messages,
            result=continuation_result,
            measurement=measure(continuation_result.text, length),
        )
        if on_attempt is not None:
            on_attempt(continuation_attempt)
        attempts.append(continuation_attempt)
        final_text += continuation_result.text

    frozen_attempts = tuple(attempts)
    return DraftResult(
        text=final_text,
        length=measure(final_text, length),
        attempts=frozen_attempts,
        truncated=frozen_attempts[-1].result.finish_reason == "length",
    )


__all__ = [
    "CONTINUATION_CONTEXT_VERSION",
    "CONTINUATION_PROMPT_OVERHEAD_TOKENS",
    "continuation_instruction",
    "DraftAttempt",
    "DraftResult",
    "continuation_prompt_reserve",
    "generate_draft",
    "validate_generation_plan",
]
