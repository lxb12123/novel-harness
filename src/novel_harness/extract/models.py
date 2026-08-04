"""Strict models for untrusted chapter-analysis output."""

from __future__ import annotations

import math
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "RawChapterAnalysis",
    "RawCharacterProfile",
    "RawEvent",
    "RawStateUpdate",
]


_UNTRUSTED_CONFIG = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

SHORT_QUOTE_LIMIT: Final = 10
"""引语短于这个字符数算「短引语」。"""

SHORT_QUOTE_RATIO: Final = 0.30
"""短引语占本章引语总数的比例上限。"""

MIN_SHORT_QUOTES_PER_CHAPTER: Final = 1
"""比例算出来是 0 时至少放行 1 条：单事件章节不该因为唯一原话太短而整章失败。"""


class RawEvent(BaseModel):
    """One story beat proposed by the analysis model."""

    model_config = _UNTRUSTED_CONFIG

    summary: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=120)
    participants: tuple[str, ...]
    knowers: tuple[str, ...]
    revealed_facts: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class RawCharacterProfile(BaseModel):
    """Profile fields attributed to a character surface name."""

    model_config = _UNTRUSTED_CONFIG

    surface: str = Field(min_length=2)
    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    confidence: float = Field(ge=0, le=1)


class RawStateUpdate(BaseModel):
    """One proposed graph-state change, still expressed only in surface names."""

    model_config = _UNTRUSTED_CONFIG

    kind: Literal["location", "state", "relationship"]
    subject: str = Field(min_length=1)
    object: str | None = None
    dimension: str | None = None
    value: str | None = None
    quote: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_kind_shape(self) -> Self:
        if self.kind == "location":
            valid = self.object is not None and self.dimension is None and self.value is None
        elif self.kind == "state":
            valid = self.object is None and self.dimension is not None and self.value is not None
        else:
            valid = self.object is not None and self.dimension is None and self.value is not None
        if not valid:
            raise ValueError(f"invalid fields for {self.kind} state update")
        return self


class RawChapterAnalysis(BaseModel):
    """Complete, untrusted structured output for one chapter."""

    model_config = _UNTRUSTED_CONFIG

    events: tuple[RawEvent, ...] = Field(min_length=1, max_length=12)
    state_updates: tuple[RawStateUpdate, ...] = Field(max_length=24)
    character_profiles: tuple[RawCharacterProfile, ...]

    @model_validator(mode="after")
    def short_quote_frequency_capped(self) -> Self:
        items = (*self.events, *self.state_updates)
        short = sum(
            1
            for item in items
            if len(item.quote) < SHORT_QUOTE_LIMIT
        )
        allowed = max(
            MIN_SHORT_QUOTES_PER_CHAPTER,
            math.ceil(SHORT_QUOTE_RATIO * len(items)),
        )
        if short > allowed:
            raise ValueError(
                f"short quotes {short} exceed the per-chapter allowance {allowed} "
                f"({SHORT_QUOTE_RATIO:.0%} of {len(items)} quotes, min {MIN_SHORT_QUOTES_PER_CHAPTER})"
            )
        return self
