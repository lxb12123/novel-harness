"""Strict models for untrusted chapter-analysis output."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "RawChapterAnalysis",
    "RawCharacterProfile",
    "RawEvent",
    "RawStateUpdate",
]


_UNTRUSTED_CONFIG = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class RawEvent(BaseModel):
    """One story beat proposed by the analysis model."""

    model_config = _UNTRUSTED_CONFIG

    summary: str = Field(min_length=1)
    quote: str = Field(min_length=10, max_length=120)
    participants: list[str] = Field(default_factory=list)
    knowers: list[str] = Field(default_factory=list)
    revealed_facts: list[str] = Field(default_factory=list)
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
    quote: str = Field(min_length=10, max_length=120)
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

    events: list[RawEvent] = Field(default_factory=list, max_length=12)
    state_updates: list[RawStateUpdate] = Field(default_factory=list, max_length=24)
    character_profiles: list[RawCharacterProfile] = Field(default_factory=list)
