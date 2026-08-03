"""Strict, offline reconstruction of preregistered M2 JSONL evidence.

The run file is evidence, not a cache.  This module therefore treats every recorded
count, score, ordering decision, and verdict input as an assertion to be recomputed.
It never resumes a run, calls a provider, or trusts a serialized leak result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..draft.assemble import PromptForm, assemble
from ..draft.capabilities import ReasoningEffort, ResolvedCallPlan
from ..draft.context import ResolvedConstraints
from ..draft.generate import continuation_instruction
from ..draft.length import (
    COUNTING_RULE_VERSION,
    M2_LENGTH_SPEC,
    LengthMeasurement,
    LengthStatus,
    length_within_tolerance,
    measure,
)
from ..graph import StoryGraph
from ..panel.constraints import scene_view
from .confound_lint import ConfoundReport, LEN_TOLERANCE, confound_lint
from .leak import LeakResult, score_against
from .runner import ARMS, TrapSpec
from .score import (
    BASE_REPEATS,
    ESCALATED_REPEATS,
    GateDecision,
    GateInput,
    TrapRuns,
    decide,
)


EXPECTED_PROTOCOL_VERSION = (
    "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5/6/7/8 + ADR 0010/0011"
)
CONTINUATION_POLICY = {"max_attempts": 2, "trigger": "under_min_only"}


class EvidenceError(ValueError):
    """The JSONL cannot support a scientific M2 verdict."""


class RunCounts(BaseModel):
    """Expected record counts after a complete structural inspection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    traps: int = Field(ge=1)
    references: int = Field(ge=1)
    cells: int = Field(ge=1)
    attempts: int = Field(ge=1)
    generations: int = Field(ge=1)
    confounds: int = Field(ge=1)


class RunLengthSummary(BaseModel):
    """Deterministically recomputed final-output lengths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_units: int = Field(ge=0)
    max_units: int = Field(ge=0)
    total_units: int = Field(ge=0)


class RunContinuationSummary(BaseModel):
    """Observed transport calls and cells that used the one allowed continuation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: int = Field(ge=0)
    transport_calls: int = Field(ge=1)
    maximum_attempts: int = Field(ge=1)


class RunInspection(BaseModel):
    """Fully reconstructed run data suitable for ADR 0009."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: str
    project_id: str
    repeats: int
    call_plan: ResolvedCallPlan
    counts: RunCounts
    lengths: RunLengthSummary
    continuations: RunContinuationSummary
    gate_input: GateInput
    decision: GateDecision


class _LineRecord(NamedTuple):
    line: int
    value: dict[str, Any]


class _DuplicateJsonKey(ValueError):
    pass


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {value}")


_KEY_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_BEARER_VALUE = re.compile(r"^\s*bearer\s+\S+", re.IGNORECASE)


def _scan_for_secrets(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):  # JSON itself guarantees this; retain an explicit guard.
                raise EvidenceError(f"{path}: JSON object key is not a string")
            normalized = _KEY_PUNCTUATION.sub("", key.lower())
            child_path = f"{path}.{key}"
            if normalized == "apikeyset":
                if not isinstance(child, bool):
                    raise EvidenceError(f"{child_path}: api_key_set must be a boolean")
            elif (
                "apikey" in normalized
                or "authorization" in normalized
                or normalized in {"bearer", "bearertoken", "clientsecret", "password"}
            ):
                raise EvidenceError(f"{child_path}: forbidden secret-shaped key")
            _scan_for_secrets(child, path=child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_secrets(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _BEARER_VALUE.match(value):
        raise EvidenceError(f"{path}: serialized Bearer credential is forbidden")


def _load_records(path: Path) -> list[_LineRecord]:
    records: list[_LineRecord] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    raise EvidenceError(f"line {line_number}: blank JSONL lines are forbidden")
                try:
                    value = json.loads(
                        raw,
                        object_pairs_hook=_duplicate_safe_object,
                        parse_constant=_reject_json_constant,
                    )
                except (json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
                    raise EvidenceError(f"line {line_number}: invalid JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise EvidenceError(f"line {line_number}: each JSONL row must be an object")
                _scan_for_secrets(value, path=f"line {line_number}")
                records.append(_LineRecord(line_number, value))
    except (OSError, UnicodeError) as exc:
        raise EvidenceError(f"cannot read evidence file {path}: {exc}") from exc
    if not records:
        raise EvidenceError("evidence file is empty")
    return records


def _kind(record: _LineRecord) -> str:
    kind = record.value.get("kind")
    if not isinstance(kind, str):
        raise EvidenceError(f"line {record.line}: record kind must be a string")
    return kind


def _mapping(record: _LineRecord, key: str) -> Mapping[str, Any]:
    value = record.value.get(key)
    if not isinstance(value, Mapping):
        raise EvidenceError(f"line {record.line}: {key} must be an object")
    return value


def _exact_integer(record: _LineRecord, key: str, *, minimum: int = 0) -> int:
    value = record.value.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvidenceError(
            f"line {record.line}: {key} must be an integer greater than or equal to {minimum}"
        )
    return value


def _text(record: _LineRecord, key: str) -> str:
    value = record.value.get(key)
    if not isinstance(value, str):
        raise EvidenceError(f"line {record.line}: {key} must be a string")
    return value


def _boolean(record: _LineRecord, key: str) -> bool:
    value = record.value.get(key)
    if not isinstance(value, bool):
        raise EvidenceError(f"line {record.line}: {key} must be a boolean")
    return value


def _measurement(record: _LineRecord, key: str) -> LengthMeasurement:
    try:
        return LengthMeasurement.model_validate(_mapping(record, key))
    except ValidationError as exc:
        raise EvidenceError(f"line {record.line}: invalid {key}: {exc}") from exc


def _leak_result(record: _LineRecord) -> LeakResult:
    try:
        return LeakResult.model_validate(_mapping(record, "leak"))
    except ValidationError as exc:
        raise EvidenceError(f"line {record.line}: invalid leak result: {exc}") from exc


def _target_leaked(kind: str, result: LeakResult) -> bool:
    return result.knows_violation if kind == "KNOWS" else result.future_leak


def _known_names(ctx: ResolvedConstraints) -> list[str]:
    ordered = [
        *ctx.cast,
        *ctx.secret_labels,
        *ctx.forbidden_names,
        *(item.name for item in ctx.matrix.characters),
        *(item.name for item in ctx.matrix.secrets),
    ]
    return list(dict.fromkeys(ordered))


def _prompt_text(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(str(message.get("content", "")) for message in messages)


def _messages(record: _LineRecord) -> list[dict[str, Any]]:
    value = record.value.get("messages")
    if not isinstance(value, list) or not value:
        raise EvidenceError(f"line {record.line}: messages must be a non-empty list")
    if not all(isinstance(item, dict) for item in value):
        raise EvidenceError(f"line {record.line}: every message must be an object")
    return value


def _usage(record: _LineRecord, key: str) -> int | None:
    value = record.value.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"line {record.line}: {key} must be null or a non-negative integer")
    return value


def _validate_identity(
    record: _LineRecord,
    trap: TrapSpec,
    arm: str,
    form: PromptForm,
    repeat: int,
) -> None:
    expected: dict[str, Any] = {
        "trap_id": trap.id,
        "trap_kind": trap.kind,
        "chapter": trap.chapter,
        "cast": list(trap.cast),
        "arm": arm,
        "form": form.value,
        "repeat": repeat,
    }
    for key, expected_value in expected.items():
        if record.value.get(key) != expected_value:
            raise EvidenceError(
                f"line {record.line}: out-of-order or mismatched {key}; "
                f"expected {expected_value!r}, got {record.value.get(key)!r}"
            )


def _validate_reference_identity(record: _LineRecord, trap: TrapSpec) -> None:
    expected = {
        "trap_id": trap.id,
        "trap_kind": trap.kind,
        "chapter": trap.chapter,
    }
    for key, expected_value in expected.items():
        if record.value.get(key) != expected_value:
            raise EvidenceError(
                f"line {record.line}: out-of-order or mismatched reference {key}; "
                f"expected {expected_value!r}, got {record.value.get(key)!r}"
            )


def _expect(records: Sequence[_LineRecord], cursor: int, expected: str) -> _LineRecord:
    if cursor >= len(records):
        raise EvidenceError(f"missing {expected} record at end of JSONL")
    record = records[cursor]
    actual = _kind(record)
    if actual == "length_invalid":
        raise EvidenceError(
            f"line {record.line}: terminal length_invalid run cannot produce a scientific verdict"
        )
    if actual != expected:
        raise EvidenceError(
            f"line {record.line}: out-of-order record; expected {expected}, got {actual}"
        )
    return record


def _validate_header(
    record: _LineRecord,
    *,
    project_id: str,
    traps: Sequence[TrapSpec],
) -> tuple[int, ResolvedCallPlan]:
    if _kind(record) != "header":
        raise EvidenceError(f"line {record.line}: the first record must be the unique header")
    header = record.value
    if header.get("protocol") != EXPECTED_PROTOCOL_VERSION:
        raise EvidenceError(
            f"line {record.line}: protocol/header mismatch; expected "
            f"{EXPECTED_PROTOCOL_VERSION!r}, got {header.get('protocol')!r}"
        )
    if header.get("project_id") != project_id:
        raise EvidenceError(
            f"line {record.line}: project_id mismatch; expected {project_id!r}, "
            f"got {header.get('project_id')!r}"
        )
    repeats = _exact_integer(record, "repeats", minimum=1)
    if repeats not in (BASE_REPEATS, ESCALATED_REPEATS):
        raise EvidenceError(
            f"line {record.line}: repeats must be {BASE_REPEATS} or {ESCALATED_REPEATS}"
        )
    if _exact_integer(record, "n_traps", minimum=1) != len(traps):
        raise EvidenceError(
            f"line {record.line}: n_traps does not match the complete supplied trap set"
        )
    if header.get("arms") != [name for name, _ in ARMS]:
        raise EvidenceError(f"line {record.line}: arms must be x0, x1, x2 in frozen order")
    try:
        length = M2_LENGTH_SPEC.model_validate(_mapping(record, "length_profile"))
    except ValidationError as exc:
        raise EvidenceError(f"line {record.line}: invalid length_profile: {exc}") from exc
    if length != M2_LENGTH_SPEC:
        raise EvidenceError(f"line {record.line}: length_profile is not the frozen M2 profile")
    if header.get("counting_rule") != COUNTING_RULE_VERSION:
        raise EvidenceError(f"line {record.line}: counting_rule mismatch")
    if header.get("continuation") != CONTINUATION_POLICY:
        raise EvidenceError(f"line {record.line}: continuation policy mismatch")
    tolerance = header.get("confound_len_tolerance")
    if isinstance(tolerance, bool) or tolerance != LEN_TOLERANCE:
        raise EvidenceError(f"line {record.line}: confound length tolerance mismatch")

    try:
        plan = ResolvedCallPlan.model_validate(_mapping(record, "call_plan"))
    except ValidationError as exc:
        raise EvidenceError(f"line {record.line}: invalid call_plan: {exc}") from exc
    if plan.length != M2_LENGTH_SPEC:
        raise EvidenceError(f"line {record.line}: call_plan length does not match length_profile")
    if (
        plan.reasoning_requested is not ReasoningEffort.HIGH
        or plan.reasoning_effective is not ReasoningEffort.HIGH
    ):
        raise EvidenceError(f"line {record.line}: M2 call_plan must request and resolve high reasoning")

    config = _mapping(record, "config")
    if not isinstance(config.get("api_key_set"), bool):
        raise EvidenceError(f"line {record.line}: config.api_key_set must be a boolean")
    if config.get("model") != plan.model or config.get("base_url") != plan.base_url:
        raise EvidenceError(f"line {record.line}: config route does not match call_plan route")
    return repeats, plan


def _validate_attempt(
    record: _LineRecord,
    *,
    trap: TrapSpec,
    arm: str,
    form: PromptForm,
    repeat: int,
    attempt_number: int,
    expected_messages: list[dict[str, Any]],
    prior_output: str,
) -> tuple[str, bool, str | None]:
    _validate_identity(record, trap, arm, form, repeat)
    if _exact_integer(record, "attempt", minimum=1) != attempt_number:
        raise EvidenceError(
            f"line {record.line}: attempt numbering must be consecutive 1–2; "
            f"expected {attempt_number}"
        )
    messages = _messages(record)
    if messages != expected_messages:
        raise EvidenceError(f"line {record.line}: stored messages do not match the frozen request")
    output = _text(record, "output")
    expected_segment = measure(output, M2_LENGTH_SPEC)
    if _measurement(record, "segment_length") != expected_segment:
        raise EvidenceError(f"line {record.line}: stored segment measurement/count is incorrect")
    cumulative = prior_output + output
    expected_cumulative = measure(cumulative, M2_LENGTH_SPEC)
    if _measurement(record, "cumulative_length") != expected_cumulative:
        raise EvidenceError(f"line {record.line}: stored cumulative length/count is incorrect")
    _text(record, "model")
    finish_reason = record.value.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise EvidenceError(f"line {record.line}: finish_reason must be a string or null")
    _usage(record, "prompt_tokens")
    _usage(record, "completion_tokens")
    needs_continuation = _boolean(record, "needs_continuation")
    expected_need = attempt_number == 1 and expected_segment.status is LengthStatus.UNDER
    if needs_continuation is not expected_need:
        raise EvidenceError(
            f"line {record.line}: needs_continuation disagrees with under-min-only policy"
        )
    return output, needs_continuation, finish_reason


def inspect_run(
    path: Path,
    *,
    store: StoryGraph,
    project_id: str,
    traps: Sequence[TrapSpec],
) -> RunInspection:
    """Parse, recompute, and reconstruct one complete M2 run without network access.

    A partial run ending in ``length_invalid`` is valid audit evidence of an invalid
    instrument, but it is not a complete scientific sample.  This function rejects it
    explicitly instead of manufacturing a ``GateDecision`` from partial cells.
    """
    trap_list = list(traps)
    if not trap_list:
        raise EvidenceError("the supplied trap set is empty")
    trap_ids = [trap.id for trap in trap_list]
    if len(trap_ids) != len(set(trap_ids)):
        raise EvidenceError("the supplied trap IDs are not unique")

    records = _load_records(path)
    header_count = sum(_kind(record) == "header" for record in records)
    if header_count != 1:
        raise EvidenceError(f"JSONL must contain exactly one header; found {header_count}")
    repeats, call_plan = _validate_header(
        records[0],
        project_id=project_id,
        traps=trap_list,
    )
    for record in records[1:]:
        if _kind(record) == "length_invalid":
            raise EvidenceError(
                f"line {record.line}: terminal length_invalid run cannot produce a scientific verdict"
            )

    cursor = 1
    trap_runs: list[TrapRuns] = []
    confound_flags: list[bool] = []
    final_lengths: list[int] = []
    attempt_total = 0
    continuation_cells = 0

    for trap in trap_list:
        try:
            view = scene_view(store, project_id, trap.chapter, trap.cast)
            ctx = ResolvedConstraints.of(view, trap.cast)
        except Exception as exc:
            raise EvidenceError(f"cannot rebuild canonical scene_view for trap {trap.id}: {exc}") from exc

        reference = _expect(records, cursor, "reference")
        cursor += 1
        _validate_reference_identity(reference, trap)
        if _text(reference, "text") != trap.reference:
            raise EvidenceError(f"line {reference.line}: stored reference text does not match trap")
        recomputed_reference = score_against(
            store,
            project_id,
            view.constraints,
            trap.reference,
        )
        if _leak_result(reference) != recomputed_reference:
            raise EvidenceError(f"line {reference.line}: recorded reference leak result was altered")
        reference_leaked = _target_leaked(trap.kind, recomputed_reference)
        if _boolean(reference, "leaked") is not reference_leaked:
            raise EvidenceError(f"line {reference.line}: recorded reference leaked flag was altered")

        arm_votes: dict[str, list[bool]] = {"x0": [], "x1": [], "x2": []}
        prompts: dict[str, list[dict[str, Any]]] = {}
        for arm, form in ARMS:
            expected_initial: list[dict[str, Any]] = assemble(
                ctx,
                form=form,
                goal=trap.goal,
                length=M2_LENGTH_SPEC,
                previous_tail=trap.prior,
            )
            prompts[arm] = expected_initial
            for repeat in range(repeats):
                first = _expect(records, cursor, "generation_attempt")
                cursor += 1
                first_output, needs_continuation, last_finish_reason = _validate_attempt(
                    first,
                    trap=trap,
                    arm=arm,
                    form=form,
                    repeat=repeat,
                    attempt_number=1,
                    expected_messages=expected_initial,
                    prior_output="",
                )
                attempt_total += 1
                outputs = [first_output]

                if needs_continuation:
                    second = _expect(records, cursor, "generation_attempt")
                    cursor += 1
                    expected_second = [
                        *expected_initial,
                        {"role": "assistant", "content": first_output},
                        {
                            "role": "user",
                            "content": continuation_instruction(
                                M2_LENGTH_SPEC,
                                measure(first_output, M2_LENGTH_SPEC).actual_units,
                            ),
                        },
                    ]
                    second_output, second_need, last_finish_reason = _validate_attempt(
                        second,
                        trap=trap,
                        arm=arm,
                        form=form,
                        repeat=repeat,
                        attempt_number=2,
                        expected_messages=expected_second,
                        prior_output=first_output,
                    )
                    if second_need:
                        raise EvidenceError(
                            f"line {second.line}: a second attempt may never request a third call"
                        )
                    outputs.append(second_output)
                    attempt_total += 1
                    continuation_cells += 1

                final = _expect(records, cursor, "generation")
                cursor += 1
                _validate_identity(final, trap, arm, form, repeat)
                final_output = "".join(outputs)
                if _text(final, "output") != final_output:
                    raise EvidenceError(
                        f"line {final.line}: final output is not the exact attempt concatenation"
                    )
                final_measurement = measure(final_output, M2_LENGTH_SPEC)
                if _measurement(final, "length") != final_measurement:
                    raise EvidenceError(f"line {final.line}: recorded final length/count is incorrect")
                if not length_within_tolerance(
                    M2_LENGTH_SPEC, final_measurement.actual_units
                ):
                    raise EvidenceError(
                        f"line {final.line}: out-of-range final was recorded as a generation"
                    )
                expected_tolerated = (
                    final_measurement.status is not LengthStatus.WITHIN
                    and length_within_tolerance(
                        M2_LENGTH_SPEC, final_measurement.actual_units
                    )
                )
                if _boolean(final, "length_tolerated") is not expected_tolerated:
                    raise EvidenceError(
                        f"line {final.line}: length_tolerated flag disagrees with the "
                        "recomputed measurement"
                    )
                if last_finish_reason == "length":
                    raise EvidenceError(
                        f"line {final.line}: length-limited final was recorded as a generation"
                    )
                if _exact_integer(final, "attempt_count", minimum=1) != len(outputs):
                    raise EvidenceError(f"line {final.line}: final attempts count is incorrect")
                recomputed_leak = score_against(
                    store,
                    project_id,
                    view.constraints,
                    final_output,
                )
                if _leak_result(final) != recomputed_leak:
                    raise EvidenceError(f"line {final.line}: recorded final leak result was altered")
                leaked = _target_leaked(trap.kind, recomputed_leak)
                if _boolean(final, "leaked") is not leaked:
                    raise EvidenceError(f"line {final.line}: recorded final leaked flag was altered")
                arm_votes[arm].append(leaked)
                final_lengths.append(final_measurement.actual_units)

        confound = _expect(records, cursor, "confound")
        cursor += 1
        if confound.value.get("trap_id") != trap.id:
            raise EvidenceError(
                f"line {confound.line}: out-of-order confound; expected trap {trap.id!r}"
            )
        recomputed_report = confound_lint(
            _prompt_text(prompts["x1"]),
            _prompt_text(prompts["x2"]),
            known_names=_known_names(ctx),
        )
        try:
            recorded_report = ConfoundReport.model_validate(_mapping(confound, "report"))
        except ValidationError as exc:
            raise EvidenceError(f"line {confound.line}: invalid confound report: {exc}") from exc
        if recorded_report != recomputed_report:
            raise EvidenceError(f"line {confound.line}: recorded confound report was altered")
        confound_flags.append(recomputed_report.ok)
        trap_runs.append(
            TrapRuns(
                trap_id=trap.id,
                kind=trap.kind,
                x0=tuple(arm_votes["x0"]),
                x1=tuple(arm_votes["x1"]),
                x2=tuple(arm_votes["x2"]),
                reference_leaked=reference_leaked,
            )
        )

    if cursor != len(records):
        extra = records[cursor]
        raise EvidenceError(
            f"line {extra.line}: unexpected extra/duplicate/out-of-order {_kind(extra)} record"
        )

    gate_input = GateInput(
        traps=tuple(trap_runs),
        confound_ok=all(confound_flags),
    )
    decision = decide(gate_input)
    cell_count = len(trap_list) * len(ARMS) * repeats
    return RunInspection(
        protocol=EXPECTED_PROTOCOL_VERSION,
        project_id=project_id,
        repeats=repeats,
        call_plan=call_plan,
        counts=RunCounts(
            traps=len(trap_list),
            references=len(trap_list),
            cells=cell_count,
            attempts=attempt_total,
            generations=cell_count,
            confounds=len(trap_list),
        ),
        lengths=RunLengthSummary(
            min_units=min(final_lengths),
            max_units=max(final_lengths),
            total_units=sum(final_lengths),
        ),
        continuations=RunContinuationSummary(
            cells=continuation_cells,
            transport_calls=attempt_total,
            maximum_attempts=CONTINUATION_POLICY["max_attempts"],
        ),
        gate_input=gate_input,
        decision=decision,
    )


__all__ = [
    "CONTINUATION_POLICY",
    "EXPECTED_PROTOCOL_VERSION",
    "EvidenceError",
    "RunContinuationSummary",
    "RunCounts",
    "RunInspection",
    "RunLengthSummary",
    "inspect_run",
]
