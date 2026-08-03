"""Frozen control-plane models and deterministic extraction-run serialization."""

from __future__ import annotations

from enum import StrEnum
import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from .prompt import build_analysis_messages


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


RUN_COLUMNS: Final = """
id, project_id, chapter_number, snapshot_id, status, errors_json,
valid_event_count, discarded_event_count, proposal_count, model_call_id,
schema_version, prompt_hash, created_at, started_at, finished_at
"""


def prompt_bytes(text: str) -> bytes:
    return json.dumps(
        build_analysis_messages(text),
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
