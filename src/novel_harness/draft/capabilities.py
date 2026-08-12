"""Exact-route model capabilities and deterministic output-budget planning.

The compatible protocol is intentionally generic; capability evidence is not.  Every
known entry is bound to one normalized ``(base_url, model)`` pair, and nearby names never
inherit reasoning or capacity from one another.
"""

from __future__ import annotations

import os
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Literal, Mapping, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .length import LengthSpec


CAPABILITY_REGISTRY_VERSION = "nh-capabilities-v1"
REGISTRY_VERSION = CAPABILITY_REGISTRY_VERSION
ADAPTER_VERSION = "nh-provider-v1"
BUDGET_FORMULA_VERSION = "nh-visible-budget-v1"
TOKEN_PLAN_VERSION = BUDGET_FORMULA_VERSION
STREAM_THRESHOLD_TOKENS = 16_000
STREAM_THRESHOLD = STREAM_THRESHOLD_TOKENS

OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
OPENAI_CHAT_URL = (
    "https://developers.openai.com/api/reference/resources/chat/"
    "subresources/completions/methods/create"
)
DEEPSEEK_MODELS_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
DEEPSEEK_THINKING_URL = "https://api-docs.deepseek.com/guides/thinking_mode"
ANTHROPIC_OPUS_URL = "https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8"
ANTHROPIC_COMPAT_URL = "https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk"
ANTHROPIC_THINKING_URL = (
    "https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models"
)
OPENROUTER_MODEL_URL = "https://openrouter.ai/anthropic/claude-opus-4.8"
OPENROUTER_REASONING_URL = "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens"


class CapabilityError(ValueError):
    """The selected call cannot be justified by its frozen capability evidence."""


class ReasoningEffort(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasoningDialect(StrEnum):
    NONE = "none"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"
    ANTHROPIC_COMPAT = "anthropic_compat"


class ServerContextManagement(StrEnum):
    """这条路由的服务端**替我们清上下文**吗，清了会不会说（ADR 0023 决策一那一节）。

    ── 为什么是三档而不是一个布尔 ────────────────────────────────────────

    本层的承诺是「**裁了什么必须说出来**」（`agent.loop.Projection` 会报）。厂商在服务端
    清掉一段而不告诉我们，那句承诺就有一段是空的——**而空在哪儿我们连说都说不出来**。
    所以「会不会说」不是第二个能力位，它就是这一位本身的取值。

    | 取值 | 含义 |
    |---|---|
    | `NONE` | 端点不做服务端上下文管理 |
    | `OPAQUE` | 做，但响应里带不回「清了什么」——**有它也不许开** |
    | `REPORTED` | 做，且响应里带得回「清了什么」——只有这一档能外包 |

    字段本身还有第四种状态：`None`（**没登记**）。同 `supports_streaming`，
    未登记一律 fail-closed 走自己的剪枝。
    """

    NONE = "none"
    OPAQUE = "opaque"
    REPORTED = "reported"


def _optional_positive_env_integer(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{name} must be a positive base-10 integer")
    return int(raw)


def _reasoning_effort_from_env() -> ReasoningEffort:
    name = "NH_LLM_REASONING_EFFORT"
    raw = os.environ.get(name, ReasoningEffort.OFF.value)
    try:
        return ReasoningEffort(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be one of off, low, medium, high") from exc


def _reasoning_dialect_from_env() -> ReasoningDialect | None:
    name = "NH_LLM_DIALECT"
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return ReasoningDialect(raw)
    except ValueError as exc:
        choices = ", ".join(dialect.value for dialect in ReasoningDialect)
        raise ValueError(f"{name} must be one of {choices}") from exc


class ProviderRuntimeOptions(BaseModel):
    """Environment-selected runtime policy kept separate from connection config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning_effort: ReasoningEffort = ReasoningEffort.OFF
    max_context_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    reasoning_dialect: ReasoningDialect | None = None

    @model_validator(mode="after")
    def _limits_are_coherent(self) -> Self:
        if (
            self.max_context_tokens is not None
            and self.max_output_tokens is not None
            and self.max_output_tokens > self.max_context_tokens
        ):
            raise ValueError("max_output_tokens cannot exceed max_context_tokens")
        return self

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            reasoning_effort=_reasoning_effort_from_env(),
            max_context_tokens=_optional_positive_env_integer("NH_LLM_MAX_CONTEXT_TOKENS"),
            max_output_tokens=_optional_positive_env_integer("NH_LLM_MAX_OUTPUT_TOKENS"),
            reasoning_dialect=_reasoning_dialect_from_env(),
        )


def normalize_base_url(value: str) -> str:
    """Normalize harmless URL spelling without adding, removing, or inferring a path."""
    raw = value.strip()
    if "\\" in raw or any(character.isspace() for character in raw):
        raise CapabilityError("base_url contains unsafe whitespace or backslashes")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise CapabilityError("base_url has an invalid port") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise CapabilityError("base_url must be an absolute http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise CapabilityError("base_url must not contain credentials")
    if parts.query or parts.fragment:
        raise CapabilityError("base_url must not contain a query or fragment")

    host = parts.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    is_default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = rendered_host if port is None or is_default_port else f"{rendered_host}:{port}"
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), "", ""))


def normalize_model(value: str) -> str:
    """Strip transport whitespace while keeping case, prefix, dots, and dashes exact."""
    model = value.strip()
    if not model:
        raise CapabilityError("model must not be blank")
    return model


class ProviderCapabilities(BaseModel):
    """Frozen facts for one exact compatible endpoint/model route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    model: str
    source: str
    source_urls: tuple[str, ...] = ()
    registry_version: str = CAPABILITY_REGISTRY_VERSION
    adapter_version: str = ADAPTER_VERSION
    max_context_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    max_tokens_field_source: Literal["declared", "compat_default"] = "declared"
    reasoning_levels: frozenset[ReasoningEffort]
    reasoning_dialect: ReasoningDialect
    reasoning_shares_output: bool
    reserve_ratio_high: float | None = Field(default=None, gt=0, lt=1)
    supports_streaming: bool | None = None
    supports_stream_usage: bool | None = None
    server_context_management: ServerContextManagement | None = None
    """服务端替我们清上下文吗（见 `ServerContextManagement`）。`None` = **这条路由没登记过这件事**。

    **今天全库没有一个消费者，这是有意的**，理由写在 `server_context_management_usable()`
    的 docstring 里：在 chat.completions 这个**无状态**协议上，「厂商那一层只许开在我们
    自己已经放弃的那部分上」这个 ADR 0023 给的口子是**空的**——我们放弃的那些根本不在
    发出去的数组里。所以这一位先立着、按证据登记，等到真有一个能带回「清了什么」的
    有状态端点时它已经在表上了，而不是那天现拍一个。
    """

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("base_url must be a string")
        return normalize_base_url(value)

    @field_validator("model", mode="before")
    @classmethod
    def _normalize_model(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("model must be a string")
        return normalize_model(value)

    @field_validator("source", "registry_version", "adapter_version")
    @classmethod
    def _nonblank_audit_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audit labels must not be blank")
        return value.strip()

    @field_validator("source_urls")
    @classmethod
    def _public_unique_source_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("source_urls must not contain duplicates")
        for value in values:
            parts = urlsplit(value)
            if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
                raise ValueError("source_urls must contain absolute http(s) URLs")
            if parts.username is not None or parts.password is not None:
                raise ValueError("source_urls must not contain credentials")
            if parts.query or parts.fragment:
                raise ValueError("source_urls must not contain a query or fragment")
        return values

    @model_validator(mode="after")
    def _is_coherent(self) -> Self:
        if self.source == "unknown":
            canonical_unknown = (
                not self.source_urls
                and self.max_context_tokens is None
                and self.max_output_tokens is None
                and self.max_tokens_field == "max_tokens"
                and self.max_tokens_field_source == "compat_default"
                and self.reasoning_levels == frozenset({ReasoningEffort.OFF})
                and self.reasoning_dialect is ReasoningDialect.NONE
                and not self.reasoning_shares_output
                and self.reserve_ratio_high is None
                and self.supports_streaming is None
                and self.supports_stream_usage is None
                and self.server_context_management is None
            )
            if not canonical_unknown:
                raise ValueError("unknown capabilities must use the canonical fail-closed shape")
        elif not self.source_urls:
            raise ValueError("known capabilities require at least one source URL")
        if (
            self.max_context_tokens is not None
            and self.max_output_tokens is not None
            and self.max_output_tokens > self.max_context_tokens
        ):
            raise ValueError("max_output_tokens cannot exceed max_context_tokens")
        if ReasoningEffort.OFF not in self.reasoning_levels:
            raise ValueError("reasoning_levels must include OFF")

        non_off = self.reasoning_levels - {ReasoningEffort.OFF}
        if self.reasoning_dialect is ReasoningDialect.NONE and non_off:
            raise ValueError("reasoning dialect NONE cannot advertise non-off reasoning")
        if self.reasoning_dialect is not ReasoningDialect.NONE and not non_off:
            raise ValueError("a reasoning dialect must advertise a non-off effort")
        if not non_off and self.reasoning_shares_output:
            raise ValueError("off-only capabilities cannot share output with reasoning")
        if self.reserve_ratio_high is not None and (
            ReasoningEffort.HIGH not in self.reasoning_levels or not self.reasoning_shares_output
        ):
            raise ValueError("reserve_ratio_high requires high reasoning in a shared output pool")
        if self.supports_stream_usage is True and self.supports_streaming is not True:
            raise ValueError("stream usage support requires streaming support")
        return self

    @property
    def route(self) -> tuple[str, str]:
        return self.base_url, self.model


class ResolvedCallPlan(BaseModel):
    """One secret-free plan resolved and validated before a transport call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    model: str
    length: LengthSpec
    prompt_token_budget: int = Field(ge=0)
    visible_token_budget: int = Field(ge=1)
    required_token_budget: int = Field(ge=1)
    request_token_budget: int = Field(ge=1)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"]
    reasoning_requested: ReasoningEffort
    reasoning_effective: ReasoningEffort
    reasoning_dialect: ReasoningDialect
    stream: bool
    budget_formula_version: str = BUDGET_FORMULA_VERSION
    capability: ProviderCapabilities

    @model_validator(mode="after")
    def _matches_capability(self) -> Self:
        if self.budget_formula_version != BUDGET_FORMULA_VERSION:
            raise ValueError("call plan budget formula version is not supported")
        if (self.base_url, self.model) != self.capability.route:
            raise ValueError("call plan route must match its capability")
        if self.max_tokens_field != self.capability.max_tokens_field:
            raise ValueError("call plan token field must match its capability")
        if self.reasoning_dialect is not self.capability.reasoning_dialect:
            raise ValueError("call plan reasoning dialect must match its capability")
        if self.reasoning_requested not in self.capability.reasoning_levels:
            raise ValueError("call plan reasoning is not supported by its capability")
        if self.reasoning_effective is not self.reasoning_requested:
            raise ValueError("reasoning may not be silently downgraded")
        expected_visible = self.length.max_units * 2 + 1_024
        if self.visible_token_budget != expected_visible:
            raise ValueError(
                f"visible token budget must equal {expected_visible} for the frozen length"
            )
        expected_required = expected_visible
        if (
            self.reasoning_requested is not ReasoningEffort.OFF
            and self.capability.reasoning_shares_output
        ):
            if (
                self.reasoning_requested is not ReasoningEffort.HIGH
                or self.capability.reserve_ratio_high is None
            ):
                raise ValueError(
                    "shared reasoning requires an audited reserve ratio for its effort"
                )
            ratio = Fraction(str(self.capability.reserve_ratio_high))
            expected_required = _ceil_fraction(Fraction(expected_visible, 1) / (1 - ratio))
        if self.required_token_budget != expected_required:
            raise ValueError(
                f"required token budget must equal {expected_required} for the frozen plan"
            )
        if not self.visible_token_budget <= self.required_token_budget <= self.request_token_budget:
            raise ValueError("visible <= required <= request token budgets is required")
        if (
            self.capability.max_output_tokens is not None
            and self.request_token_budget > self.capability.max_output_tokens
        ):
            raise ValueError(
                f"request token budget {self.request_token_budget} exceeds model max output "
                f"{self.capability.max_output_tokens}"
            )
        if (
            self.capability.max_context_tokens is not None
            and self.prompt_token_budget + self.request_token_budget
            > self.capability.max_context_tokens
        ):
            raise ValueError(
                f"prompt ({self.prompt_token_budget}) + request "
                f"({self.request_token_budget}) exceeds context window "
                f"{self.capability.max_context_tokens}"
            )
        if self.stream != (self.request_token_budget > STREAM_THRESHOLD_TOKENS):
            raise ValueError("stream must follow the versioned token threshold")
        if self.stream and self.capability.supports_streaming is not True:
            state = "unknown" if self.capability.supports_streaming is None else "false"
            raise ValueError(f"streaming is required, but capability support is {state}")
        return self


class StructuredCallPlan(BaseModel):
    """One validated provider plan for caller-budgeted structured output.

    Unlike :class:`ResolvedCallPlan`, this plan has no prose ``LengthSpec``.  The
    caller declares the maximum visible JSON budget directly, while the same
    capability evidence still determines reasoning reserve, request size, and
    streaming.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    model: str
    prompt_token_budget: int = Field(ge=0)
    visible_token_budget: int = Field(ge=1)
    required_token_budget: int = Field(ge=1)
    request_token_budget: int = Field(ge=1)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"]
    reasoning_requested: ReasoningEffort
    reasoning_effective: ReasoningEffort
    reasoning_dialect: ReasoningDialect
    stream: bool = Field(strict=True)
    budget_formula_version: str = BUDGET_FORMULA_VERSION
    capability: ProviderCapabilities

    @field_validator(
        "prompt_token_budget",
        "visible_token_budget",
        "required_token_budget",
        "request_token_budget",
        mode="before",
    )
    @classmethod
    def _budgets_are_strict_integers(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("token budgets must be integers, not bools or coerced values")
        return value

    @model_validator(mode="after")
    def _matches_capability(self) -> Self:
        if self.budget_formula_version != BUDGET_FORMULA_VERSION:
            raise ValueError("call plan budget formula version is not supported")
        if self.capability.source == "unknown":
            raise ValueError("structured calls require known capability evidence")
        if (self.base_url, self.model) != self.capability.route:
            raise ValueError("call plan route must match its capability")
        if self.max_tokens_field != self.capability.max_tokens_field:
            raise ValueError("call plan token field must match its capability")
        if self.reasoning_dialect is not self.capability.reasoning_dialect:
            raise ValueError("call plan reasoning dialect must match its capability")
        if self.reasoning_requested not in self.capability.reasoning_levels:
            raise ValueError("call plan reasoning is not supported by its capability")
        if self.reasoning_effective is not self.reasoning_requested:
            raise ValueError("reasoning may not be silently downgraded")

        expected_required = self.visible_token_budget
        if (
            self.reasoning_requested is not ReasoningEffort.OFF
            and self.capability.reasoning_shares_output
        ):
            if (
                self.reasoning_requested is not ReasoningEffort.HIGH
                or self.capability.reserve_ratio_high is None
            ):
                raise ValueError(
                    "shared reasoning requires an audited reserve ratio for its effort"
                )
            ratio = Fraction(str(self.capability.reserve_ratio_high))
            expected_required = _ceil_fraction(Fraction(self.visible_token_budget, 1) / (1 - ratio))
        if self.required_token_budget != expected_required:
            raise ValueError(
                f"required token budget must equal {expected_required} for the frozen plan"
            )
        if not self.visible_token_budget <= self.required_token_budget <= self.request_token_budget:
            raise ValueError("visible <= required <= request token budgets is required")
        if (
            self.capability.max_output_tokens is not None
            and self.request_token_budget > self.capability.max_output_tokens
        ):
            raise ValueError(
                f"request token budget {self.request_token_budget} exceeds model max output "
                f"{self.capability.max_output_tokens}"
            )
        if (
            self.capability.max_context_tokens is not None
            and self.prompt_token_budget + self.request_token_budget
            > self.capability.max_context_tokens
        ):
            raise ValueError(
                f"prompt ({self.prompt_token_budget}) + request "
                f"({self.request_token_budget}) exceeds context window "
                f"{self.capability.max_context_tokens}"
            )
        if self.stream != (self.request_token_budget > STREAM_THRESHOLD_TOKENS):
            raise ValueError("stream must follow the versioned token threshold")
        if self.stream and self.capability.supports_streaming is not True:
            state = "unknown" if self.capability.supports_streaming is None else "false"
            raise ValueError(f"streaming is required, but capability support is {state}")
        return self


type CallPlan = ResolvedCallPlan | StructuredCallPlan


def _known_capability(
    base_url: str,
    model: str,
    *,
    source: str,
    source_urls: tuple[str, ...],
    max_context_tokens: int,
    max_output_tokens: int,
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"],
    reasoning_levels: frozenset[ReasoningEffort],
    reasoning_dialect: ReasoningDialect,
    reserve_ratio_high: float | None = None,
    supports_stream_usage: bool | None = None,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        base_url=base_url,
        model=model,
        source=source,
        source_urls=source_urls,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        max_tokens_field=max_tokens_field,
        max_tokens_field_source="declared",
        reasoning_levels=reasoning_levels,
        reasoning_dialect=reasoning_dialect,
        reasoning_shares_output=True,
        reserve_ratio_high=reserve_ratio_high,
        supports_streaming=True,
        supports_stream_usage=supports_stream_usage,
    )


_ALL_EFFORTS = frozenset(ReasoningEffort)
_DEEPSEEK_PRO_EFFORTS = frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH})
_DEEPSEEK_FLASH_EFFORTS = frozenset(
    {ReasoningEffort.OFF, ReasoningEffort.LOW, ReasoningEffort.HIGH}
)


def _build_registry() -> Mapping[tuple[str, str], ProviderCapabilities]:
    entries: list[ProviderCapabilities] = []
    for model in ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        entries.append(
            _known_capability(
                "https://api.openai.com/v1",
                model,
                source="registry:openai-gpt-5.6-chat-completions",
                source_urls=(OPENAI_MODELS_URL, OPENAI_CHAT_URL),
                max_context_tokens=1_050_000,
                max_output_tokens=128_000,
                max_tokens_field="max_completion_tokens",
                reasoning_levels=_ALL_EFFORTS,
                reasoning_dialect=ReasoningDialect.OPENAI,
                supports_stream_usage=True,
            )
        )
    for model, levels in (
        ("deepseek-v4-pro", _DEEPSEEK_PRO_EFFORTS),
        ("deepseek-v4-flash", _DEEPSEEK_FLASH_EFFORTS),
    ):
        entries.append(
            _known_capability(
                "https://api.deepseek.com",
                model,
                source="registry:deepseek-v4-chat-completions",
                source_urls=(DEEPSEEK_MODELS_URL, DEEPSEEK_THINKING_URL),
                max_context_tokens=1_000_000,
                max_output_tokens=384_000,
                max_tokens_field="max_tokens",
                reasoning_levels=levels,
                reasoning_dialect=ReasoningDialect.DEEPSEEK,
                # 审计过的共享输出预留：DeepSeek V4 thinking 的 reasoning_content 与
                # content 共用输出预算且无官方占比。2026-08-02 实测右尾：单个 cell
                # thinking+content 达 40,000 tokens（K02/x2/r0 finish_reason=length），
                # 0.8 不够；按 0.95 预留（request 150,000），证据见
                # docs/M2_ENDPOINT_PROFILE.md（profile 不含 key）。
                reserve_ratio_high=0.95,
            )
        )
    entries.append(
        _known_capability(
            "https://api.anthropic.com/v1",
            "claude-opus-4-8",
            source="registry:anthropic-openai-compat-opus-4.8",
            source_urls=(ANTHROPIC_OPUS_URL, ANTHROPIC_COMPAT_URL, ANTHROPIC_THINKING_URL),
            max_context_tokens=1_000_000,
            max_output_tokens=128_000,
            max_tokens_field="max_tokens",
            reasoning_levels=_ALL_EFFORTS,
            reasoning_dialect=ReasoningDialect.ANTHROPIC_COMPAT,
        )
    )
    entries.append(
        _known_capability(
            "https://openrouter.ai/api/v1",
            "anthropic/claude-opus-4.8",
            source="registry:openrouter-opus-4.8",
            source_urls=(OPENROUTER_MODEL_URL, OPENROUTER_REASONING_URL),
            max_context_tokens=1_000_000,
            max_output_tokens=128_000,
            max_tokens_field="max_tokens",
            reasoning_levels=_ALL_EFFORTS,
            reasoning_dialect=ReasoningDialect.OPENROUTER,
            reserve_ratio_high=0.8,
        )
    )
    registry = {entry.route: entry for entry in entries}
    if len(registry) != len(entries):  # pragma: no cover - import-time invariant
        raise RuntimeError("duplicate exact route in capability registry")
    return MappingProxyType(registry)


CAPABILITY_REGISTRY = _build_registry()


def resolve_capabilities(
    base_url: str,
    model: str,
    *,
    operator_override: ProviderCapabilities | None = None,
    metadata: ProviderCapabilities | None = None,
) -> ProviderCapabilities:
    """Resolve operator override -> exact registry -> metadata -> explicit unknown."""
    route = normalize_base_url(base_url), normalize_model(model)
    if operator_override is not None and operator_override.route != route:
        raise CapabilityError("operator override route does not match the requested route")
    if metadata is not None and metadata.route != route:
        raise CapabilityError("endpoint metadata route does not match the requested route")
    if operator_override is not None:
        return operator_override
    registered = CAPABILITY_REGISTRY.get(route)
    if registered is not None:
        return registered
    if metadata is not None:
        return metadata
    return ProviderCapabilities(
        base_url=route[0],
        model=route[1],
        source="unknown",
        source_urls=(),
        max_context_tokens=None,
        max_output_tokens=None,
        max_tokens_field="max_tokens",
        max_tokens_field_source="compat_default",
        reasoning_levels=frozenset({ReasoningEffort.OFF}),
        reasoning_dialect=ReasoningDialect.NONE,
        reasoning_shares_output=False,
        reserve_ratio_high=None,
        supports_streaming=None,
        supports_stream_usage=None,
        server_context_management=None,
    )


def server_context_management_usable(capability: ProviderCapabilities) -> bool:
    """能不能把「清旧上下文」外包给这条路由的服务端（ADR 0023 决策一）。

    **只有 `REPORTED` 一档为真**，其余（`OPAQUE` / `NONE` / 未登记）一律自己做。
    这是那张能力表的既有立场（`supports_streaming is None` ⇒ 不许流式）换了个对象，
    不是新机制。

    ── 为什么它今天没有调用方，而这**不是**「接线还没做」──────────────────

    ADR 0023 给厂商那一层留的口子写得很死：

    > **厂商那一层只许开在「我们自己已经放弃」的那部分上**，或者确认它的响应能带回
    > 「清了什么」。

    在 `chat.completions` 这个**无状态**协议上，前半句那个口子是**空集**：每一轮把整个
    消息数组从头重发（ADR 0022 §3 就是靠这条事实决定候选稿不进对话的），所以
    「我们已经放弃的那些」压根不在发出去的 payload 里——服务端没有东西可清。
    真正非空的形态要求端点自己存着这段会话（Responses / 有状态那一类），而那不是本仓
    `draft/provider.py` 钉死的那个协议。

    后半句（`REPORTED`）本身也不够：**服务端不知道第几章**。它把一条绑第 90 章的工具
    返回压成散文之后，那段散文对第 40 章的投影就是一条读起来完全正常的过期事实——
    ADR 0019 边界四点名的那个故障，只是这次发生在我们的筛子够不着的地方。所以真要开，
    先决条件不只是「它说得出清了什么」，还得是**它清掉的那些本来就不该按章号筛**。

    结论：这一位按证据登记、判据只写一次，**开关留给那一天**。
    """
    return capability.server_context_management is ServerContextManagement.REPORTED


def _positive_integer(value: int, name: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        adjective = "non-negative" if allow_zero else "positive"
        raise CapabilityError(f"{name} must be a {adjective} integer")
    return value


def _ceil_fraction(value: Fraction) -> int:
    return (value.numerator + value.denominator - 1) // value.denominator


def plan_call(
    length: LengthSpec,
    reasoning: ReasoningEffort | str,
    capability: ProviderCapabilities,
    *,
    prompt_token_budget: int = 0,
    request_token_budget: int | None = None,
) -> ResolvedCallPlan:
    """Resolve a capacity plan without probing, downgrading, or clamping."""
    try:
        effort = ReasoningEffort(reasoning)
    except ValueError as exc:
        raise CapabilityError(f"unsupported reasoning effort: {reasoning!r}") from exc
    if effort not in capability.reasoning_levels:
        raise CapabilityError(
            f"reasoning={effort.value} is not supported for exact route {capability.route!r}"
        )
    prompt = _positive_integer(prompt_token_budget, "prompt_token_budget", allow_zero=True)

    visible = length.max_units * 2 + 1_024
    required = visible
    if effort is not ReasoningEffort.OFF and capability.reasoning_shares_output:
        if effort is not ReasoningEffort.HIGH or capability.reserve_ratio_high is None:
            raise CapabilityError(
                f"reasoning={effort.value} shares output but has no audited reserve ratio"
            )
        ratio = Fraction(str(capability.reserve_ratio_high))
        required = _ceil_fraction(Fraction(visible, 1) / (1 - ratio))

    if capability.max_output_tokens is not None and required > capability.max_output_tokens:
        raise CapabilityError(
            f"required token budget {required} exceeds model max output "
            f"{capability.max_output_tokens}; refusing to clamp"
        )

    if request_token_budget is None:
        request = (
            ((required + 9_999) // 10_000) * 10_000
            if effort is ReasoningEffort.HIGH and capability.reasoning_shares_output
            else required
        )
    else:
        request = _positive_integer(request_token_budget, "request_token_budget")
        if request < required:
            raise CapabilityError(
                f"request token budget {request} is below required budget {required}"
            )
    if capability.max_output_tokens is not None and request > capability.max_output_tokens:
        raise CapabilityError(
            f"request token budget {request} exceeds model max output "
            f"{capability.max_output_tokens}; refusing to clamp"
        )
    if (
        capability.max_context_tokens is not None
        and prompt + request > capability.max_context_tokens
    ):
        raise CapabilityError(
            f"prompt ({prompt}) + request ({request}) exceeds context window "
            f"{capability.max_context_tokens}"
        )

    stream = request > STREAM_THRESHOLD_TOKENS
    if stream and capability.supports_streaming is not True:
        state = "unknown" if capability.supports_streaming is None else "false"
        raise CapabilityError(
            f"request budget {request} requires streaming, but support is {state}"
        )

    return ResolvedCallPlan(
        base_url=capability.base_url,
        model=capability.model,
        length=length,
        prompt_token_budget=prompt,
        visible_token_budget=visible,
        required_token_budget=required,
        request_token_budget=request,
        max_tokens_field=capability.max_tokens_field,
        reasoning_requested=effort,
        reasoning_effective=effort,
        reasoning_dialect=capability.reasoning_dialect,
        stream=stream,
        capability=capability,
    )


def plan_structured_call(
    visible_token_budget: int,
    reasoning: ReasoningEffort | str,
    capability: ProviderCapabilities,
    *,
    prompt_token_budget: int = 0,
    request_token_budget: int | None = None,
) -> StructuredCallPlan:
    """Resolve a structured-output plan without inventing a prose length."""
    if capability.source == "unknown":
        raise CapabilityError(
            f"unknown capability for exact route {capability.route!r}; refusing structured call"
        )
    try:
        effort = ReasoningEffort(reasoning)
    except (TypeError, ValueError) as exc:
        raise CapabilityError(f"unsupported reasoning effort: {reasoning!r}") from exc
    if effort not in capability.reasoning_levels:
        raise CapabilityError(
            f"reasoning={effort.value} is not supported for exact route {capability.route!r}"
        )

    visible = _positive_integer(visible_token_budget, "visible_token_budget")
    prompt = _positive_integer(prompt_token_budget, "prompt_token_budget", allow_zero=True)
    required = visible
    if effort is not ReasoningEffort.OFF and capability.reasoning_shares_output:
        if effort is not ReasoningEffort.HIGH or capability.reserve_ratio_high is None:
            raise CapabilityError(
                f"reasoning={effort.value} shares output but has no audited reserve ratio"
            )
        ratio = Fraction(str(capability.reserve_ratio_high))
        required = _ceil_fraction(Fraction(visible, 1) / (1 - ratio))

    if capability.max_output_tokens is not None and required > capability.max_output_tokens:
        raise CapabilityError(
            f"required token budget {required} exceeds model max output "
            f"{capability.max_output_tokens}; refusing to clamp"
        )

    if request_token_budget is None:
        request = (
            ((required + 9_999) // 10_000) * 10_000
            if effort is ReasoningEffort.HIGH and capability.reasoning_shares_output
            else required
        )
    else:
        request = _positive_integer(request_token_budget, "request_token_budget")
        if request < required:
            raise CapabilityError(
                f"request token budget {request} is below required budget {required}"
            )
    if capability.max_output_tokens is not None and request > capability.max_output_tokens:
        raise CapabilityError(
            f"request token budget {request} exceeds model max output "
            f"{capability.max_output_tokens}; refusing to clamp"
        )
    if (
        capability.max_context_tokens is not None
        and prompt + request > capability.max_context_tokens
    ):
        raise CapabilityError(
            f"prompt ({prompt}) + request ({request}) exceeds context window "
            f"{capability.max_context_tokens}"
        )

    stream = request > STREAM_THRESHOLD_TOKENS
    if stream and capability.supports_streaming is not True:
        state = "unknown" if capability.supports_streaming is None else "false"
        raise CapabilityError(
            f"request budget {request} requires streaming, but support is {state}"
        )

    return StructuredCallPlan(
        base_url=capability.base_url,
        model=capability.model,
        prompt_token_budget=prompt,
        visible_token_budget=visible,
        required_token_budget=required,
        request_token_budget=request,
        max_tokens_field=capability.max_tokens_field,
        reasoning_requested=effort,
        reasoning_effective=effort,
        reasoning_dialect=capability.reasoning_dialect,
        stream=stream,
        capability=capability,
    )


__all__ = [
    "ADAPTER_VERSION",
    "BUDGET_FORMULA_VERSION",
    "CAPABILITY_REGISTRY",
    "CAPABILITY_REGISTRY_VERSION",
    "REGISTRY_VERSION",
    "STREAM_THRESHOLD",
    "STREAM_THRESHOLD_TOKENS",
    "TOKEN_PLAN_VERSION",
    "CapabilityError",
    "CallPlan",
    "ProviderCapabilities",
    "ProviderRuntimeOptions",
    "ReasoningDialect",
    "ReasoningEffort",
    "ResolvedCallPlan",
    "ServerContextManagement",
    "StructuredCallPlan",
    "normalize_base_url",
    "normalize_model",
    "plan_call",
    "plan_structured_call",
    "resolve_capabilities",
    "server_context_management_usable",
]
