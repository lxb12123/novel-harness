"""Frozen control-plane models and deterministic extraction-run serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..draft.provider import CompletionResult
from ..graph import ChapterText
from .prompt import AnalysisMessage, build_analysis_messages


class ExtractionRunnerError(RuntimeError):
    """Base class for control-plane extraction failures."""


class ExtractionChapterNotFound(ExtractionRunnerError):
    """No current immutable snapshot exists for the requested chapter."""


class ExtractionRunNotFound(ExtractionRunnerError):
    """The run ID does not identify an extraction run."""


class ExtractionRunStateError(ExtractionRunnerError):
    """A compare-and-set transition could not be completed safely."""


class ExtractionRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExtractionRunError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ExtractionRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    chapter_number: int = Field(ge=1)
    snapshot_id: str
    status: ExtractionRunStatus
    errors: tuple[ExtractionRunError, ...] = ()
    valid_event_count: int = Field(ge=0)
    discarded_event_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    model_call_id: str | None = None
    schema_version: str
    prompt_hash: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class ImmutableAnalysisMessage:
    """One deeply immutable member of the exact provider message sequence."""

    role: Literal["system", "user"]
    content: str

    def wire(self) -> AnalysisMessage:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """One prompt-bound analyzer request; derived fields cannot be supplied independently."""

    chapter: ChapterText
    messages: tuple[ImmutableAnalysisMessage, ...] = field(init=False)
    prompt_bytes: bytes = field(init=False, repr=False)
    prompt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        messages = tuple(
            ImmutableAnalysisMessage(role=message["role"], content=message["content"])
            for message in build_analysis_messages(self.chapter.text)
        )
        encoded = _encode_messages(messages)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "prompt_bytes", encoded)
        object.__setattr__(self, "prompt_hash", sha256(encoded).hexdigest())

    def wire_messages(self) -> list[AnalysisMessage]:
        """Return a fresh mutable wire copy without exposing the immutable audit source."""
        return [message.wire() for message in self.messages]


class AuditedCompletion(BaseModel):
    """Strict, UTF-8-safe copy of a provider result used for persistence and parsing."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    text: str
    model: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @field_validator("text", "model", "finish_reason")
    @classmethod
    def strict_utf8(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
        return value

    @field_validator("prompt_tokens", "completion_tokens", mode="before")
    @classmethod
    def non_negative_strict_int(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("token counts must be non-negative strict integers or None")
        return value

    @classmethod
    def from_result(cls, result: CompletionResult) -> AuditedCompletion:
        return cls.model_validate(
            {
                "text": result.text,
                "model": result.model,
                "finish_reason": result.finish_reason,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            },
            strict=True,
        )


RUN_COLUMNS: Final = """
id, project_id, chapter_number, snapshot_id, status, errors_json,
valid_event_count, discarded_event_count, proposal_count, model_call_id,
schema_version, prompt_hash, created_at, started_at, finished_at
"""


def prompt_bytes(text: str) -> bytes:
    messages = tuple(
        ImmutableAnalysisMessage(role=message["role"], content=message["content"])
        for message in build_analysis_messages(text)
    )
    return _encode_messages(messages)


def _encode_messages(messages: tuple[ImmutableAnalysisMessage, ...]) -> bytes:
    return json.dumps(
        [message.wire() for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def to_run(row: Any) -> ExtractionRun:
    if row is None:
        raise ExtractionRunStateError("expected extraction run row is missing")
    return ExtractionRun(
        id=row["id"],
        project_id=row["project_id"],
        chapter_number=row["chapter_number"],
        snapshot_id=row["snapshot_id"],
        status=row["status"],
        errors=tuple(
            ExtractionRunError.model_validate(item)
            for item in json.loads(row["errors_json"])
        ),
        valid_event_count=row["valid_event_count"],
        discarded_event_count=row["discarded_event_count"],
        proposal_count=row["proposal_count"],
        model_call_id=row["model_call_id"],
        schema_version=row["schema_version"],
        prompt_hash=row["prompt_hash"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
