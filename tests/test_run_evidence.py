"""M2 JSONL evidence inspection is deterministic, offline, and fail-closed."""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from novel_harness import db, project
from novel_harness.declare import Ledger
from novel_harness.draft.assemble import PromptForm, assemble
from novel_harness.draft.capabilities import (
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    ResolvedCallPlan,
    plan_call,
)
from novel_harness.draft.context import ResolvedConstraints
from novel_harness.draft.generate import continuation_instruction
from novel_harness.draft.length import (
    COUNTING_RULE_VERSION,
    M2_LENGTH_SPEC,
    LengthStatus,
    length_within_tolerance,
    measure,
)
from novel_harness.eval.confound_lint import LEN_TOLERANCE, confound_lint
from novel_harness.eval.evidence import EvidenceError, inspect_run
from novel_harness.eval.leak import LeakResult, score_against
from novel_harness.eval.runner import ARMS, TrapSpec
from novel_harness.eval.score import GateInput, TrapRuns, decide
from novel_harness.graph import (
    AliasKind,
    EdgeProps,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.panel.constraints import scene_view


PROTOCOL = "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5/6/7/8 + ADR 0010/0011"
LOCAL = "http://localhost:11434/v1"
MODEL = "fixture-high-model"
TELL = "玄血蛊"
FUTURE_TELL = "血枭盟"
CAST = ["苏挽", "萧决"]
CHAPTER = 5
CLEAN = "雨" * 2100


class Seeded(NamedTuple):
    store: SqliteStoryGraph
    project_id: str


def _seed(path: Path) -> Seeded:
    conn = db.connect(path)
    db.migrate(conn)
    project_id = project.create(conn, name="证据检查", root_path=str(path.parent)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, project_id)

    xiao = ledger.declare_node(NodeLabel.CHARACTER, "萧决").id
    ledger.declare_node(NodeLabel.CHARACTER, "苏挽")
    secret = ledger.declare_node(
        NodeLabel.SECRET,
        "血脉秘密",
        secret=SecretDetail(),
    ).id
    ledger.declare_alias(of="血脉秘密", surface=TELL, kind=AliasKind.ALIAS)
    store.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.FACTION,
            name=FUTURE_TELL,
            props=NodeProps.model_validate({"first_appears_chapter": 8}),
        )
    )
    store.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=xiao,
            dst=secret,
            type=EdgeType.KNOWS,
            props=EdgeProps(),
            valid_from_chapter=3,
            information_scope=InformationScope.CANON,
        )
    )
    conn.commit()
    return Seeded(store, project_id)


@pytest.fixture
def seeded(tmp_path: Path) -> Seeded:
    return _seed(tmp_path / "book.db")


def _trap(trap_id: str, kind: str) -> TrapSpec:
    return TrapSpec(
        id=trap_id,
        kind=kind,
        chapter=CHAPTER,
        cast=list(CAST),
        goal="苏挽来还伞，两人在檐下短暂交锋。",
        prior="上一章末尾，别人在她背后提过那件事，她没听见。",
        reference=CLEAN,
    )


TRAPS = (_trap("K01", "KNOWS"), _trap("F01", "FUTURE"))


def _plan() -> ResolvedCallPlan:
    capability = ProviderCapabilities(
        base_url=LOCAL,
        model=MODEL,
        source="operator-test",
        source_urls=("https://example.test/capability",),
        max_context_tokens=1_000_000,
        max_output_tokens=128_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
        reasoning_dialect=ReasoningDialect.OPENAI,
        reasoning_shares_output=False,
        supports_streaming=True,
        supports_stream_usage=False,
    )
    return plan_call(
        M2_LENGTH_SPEC,
        ReasoningEffort.HIGH,
        capability,
        prompt_token_budget=1_000,
    )


def _leaked(kind: str, result: LeakResult) -> bool:
    return result.knows_violation if kind == "KNOWS" else result.future_leak


def _known_names(ctx: ResolvedConstraints) -> list[str]:
    values = [
        *ctx.cast,
        *ctx.secret_labels,
        *ctx.forbidden_names,
        *(item.name for item in ctx.matrix.characters),
        *(item.name for item in ctx.matrix.secrets),
    ]
    return list(dict.fromkeys(values))


def _prompt_text(messages: Sequence[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages)


def _identity(trap: TrapSpec, arm: str, form: PromptForm, repeat: int) -> dict[str, object]:
    return {
        "trap_id": trap.id,
        "trap_kind": trap.kind,
        "chapter": trap.chapter,
        "cast": list(trap.cast),
        "arm": arm,
        "form": form.value,
        "repeat": repeat,
    }


def _attempt(
    trap: TrapSpec,
    arm: str,
    form: PromptForm,
    repeat: int,
    *,
    number: int,
    messages: list[dict[str, str]],
    output: str,
    cumulative: str,
    needs_continuation: bool,
) -> dict[str, object]:
    return {
        "kind": "generation_attempt",
        **_identity(trap, arm, form, repeat),
        "attempt": number,
        "messages": messages,
        "output": output,
        "segment_length": measure(output, M2_LENGTH_SPEC).model_dump(mode="json"),
        "cumulative_length": measure(cumulative, M2_LENGTH_SPEC).model_dump(mode="json"),
        "model": MODEL,
        "finish_reason": "stop",
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "needs_continuation": needs_continuation,
    }


def _final(
    seeded: Seeded,
    trap: TrapSpec,
    arm: str,
    form: PromptForm,
    repeat: int,
    *,
    output: str,
    attempts: int,
) -> tuple[dict[str, object], bool]:
    constraints = scene_view(
        seeded.store,
        seeded.project_id,
        trap.chapter,
        trap.cast,
    ).constraints
    leak = score_against(seeded.store, seeded.project_id, constraints, output)
    leaked = _leaked(trap.kind, leak)
    measurement = measure(output, M2_LENGTH_SPEC)
    return (
        {
            "kind": "generation",
            **_identity(trap, arm, form, repeat),
            "output": output,
            "length": measurement.model_dump(mode="json"),
            "length_tolerated": (
                measurement.status is not LengthStatus.WITHIN
                and length_within_tolerance(
                    M2_LENGTH_SPEC, measurement.actual_units
                )
            ),
            "attempt_count": attempts,
            "leak": leak.model_dump(mode="json"),
            "leaked": leaked,
        },
        leaked,
    )


def _write_valid_run(path: Path, seeded: Seeded) -> tuple[list[dict[str, object]], GateInput]:
    plan = _plan()
    records: list[dict[str, object]] = [
        {
            "kind": "header",
            "protocol": PROTOCOL,
            "project_id": seeded.project_id,
            "repeats": 3,
            "n_traps": len(TRAPS),
            "arms": [arm for arm, _ in ARMS],
            "config": {
                "base_url": LOCAL,
                "model": MODEL,
                "temperature": None,
                "timeout": 600.0,
                "api_key_set": True,
            },
            "length_profile": M2_LENGTH_SPEC.model_dump(mode="json"),
            "counting_rule": COUNTING_RULE_VERSION,
            "continuation": {"max_attempts": 2, "trigger": "under_min_only"},
            "call_plan": plan.model_dump(mode="json"),
            "confound_len_tolerance": LEN_TOLERANCE,
        }
    ]
    trap_runs: list[TrapRuns] = []
    for trap in TRAPS:
        view = scene_view(seeded.store, seeded.project_id, trap.chapter, trap.cast)
        ctx = ResolvedConstraints.of(view, trap.cast)
        reference_leak = score_against(
            seeded.store,
            seeded.project_id,
            view.constraints,
            trap.reference,
        )
        records.append(
            {
                "kind": "reference",
                "trap_id": trap.id,
                "trap_kind": trap.kind,
                "chapter": trap.chapter,
                "text": trap.reference,
                "leak": reference_leak.model_dump(mode="json"),
                "leaked": _leaked(trap.kind, reference_leak),
            }
        )

        votes: dict[str, list[bool]] = {"x0": [], "x1": [], "x2": []}
        prompts: dict[str, list[dict[str, str]]] = {}
        for arm, form in ARMS:
            messages = assemble(
                ctx,
                form=form,
                goal=trap.goal,
                length=M2_LENGTH_SPEC,
                previous_tail=trap.prior,
            )
            prompts[arm] = messages
            for repeat in range(3):
                tell = TELL if trap.kind == "KNOWS" else FUTURE_TELL
                complete_output = (
                    tell + "雨" * (2100 - len(tell)) if arm == "x0" else CLEAN
                )
                if trap.id == "K01" and arm == "x0" and repeat == 0:
                    first = complete_output[:1000]
                    second = complete_output[1000:]
                    records.append(
                        _attempt(
                            trap,
                            arm,
                            form,
                            repeat,
                            number=1,
                            messages=messages,
                            output=first,
                            cumulative=first,
                            needs_continuation=True,
                        )
                    )
                    continuation_messages = [
                        *messages,
                        {"role": "assistant", "content": first},
                        {
                            "role": "user",
                            "content": continuation_instruction(
                                M2_LENGTH_SPEC,
                                measure(first, M2_LENGTH_SPEC).actual_units,
                            ),
                        },
                    ]
                    records.append(
                        _attempt(
                            trap,
                            arm,
                            form,
                            repeat,
                            number=2,
                            messages=continuation_messages,
                            output=second,
                            cumulative=first + second,
                            needs_continuation=False,
                        )
                    )
                    attempt_count = 2
                else:
                    records.append(
                        _attempt(
                            trap,
                            arm,
                            form,
                            repeat,
                            number=1,
                            messages=messages,
                            output=complete_output,
                            cumulative=complete_output,
                            needs_continuation=False,
                        )
                    )
                    attempt_count = 1

                final, leaked = _final(
                    seeded,
                    trap,
                    arm,
                    form,
                    repeat,
                    output=complete_output,
                    attempts=attempt_count,
                )
                records.append(final)
                votes[arm].append(leaked)

        report = confound_lint(
            _prompt_text(prompts["x1"]),
            _prompt_text(prompts["x2"]),
            known_names=_known_names(ctx),
        )
        records.append(
            {"kind": "confound", "trap_id": trap.id, "report": report.model_dump(mode="json")}
        )
        trap_runs.append(
            TrapRuns(
                trap_id=trap.id,
                kind=trap.kind,
                x0=tuple(votes["x0"]),
                x1=tuple(votes["x1"]),
                x2=tuple(votes["x2"]),
                reference_leaked=_leaked(trap.kind, reference_leak),
            )
        )

    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return records, GateInput(
        traps=tuple(trap_runs),
        confound_ok=all(record["report"]["ok"] for record in records if record["kind"] == "confound"),
    )


def _rewrite(path: Path, records: Sequence[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _find_record(
    records: Sequence[dict[str, object]],
    kind: str,
    **identity: object,
) -> tuple[int, dict[str, Any]]:
    for index, raw in enumerate(records):
        record = raw
        if record.get("kind") == kind and all(record.get(key) == value for key, value in identity.items()):
            return index, record
    raise AssertionError(f"fixture record not found: {kind} {identity}")


def _inspect(path: Path, seeded: Seeded) -> object:
    return inspect_run(path, store=seeded.store, project_id=seeded.project_id, traps=TRAPS)


def test_valid_run_reconstructs_gate_input_decision_and_summaries(
    seeded: Seeded,
    tmp_path: Path,
) -> None:
    path = tmp_path / "valid.jsonl"
    _, expected_input = _write_valid_run(path, seeded)

    inspected = inspect_run(
        path,
        store=seeded.store,
        project_id=seeded.project_id,
        traps=TRAPS,
    )

    assert inspected.gate_input == expected_input
    assert inspected.decision == decide(expected_input)
    assert inspected.counts.model_dump() == {
        "traps": 2,
        "references": 2,
        "cells": 18,
        "attempts": 19,
        "generations": 18,
        "confounds": 2,
    }
    assert inspected.lengths.min_units == 2100
    assert inspected.lengths.max_units == 2100
    assert inspected.lengths.total_units == 18 * 2100
    assert inspected.continuations.cells == 1
    assert inspected.continuations.transport_calls == 19
    assert inspected.continuations.maximum_attempts == 2


@pytest.mark.parametrize("damage", ["duplicate", "missing", "out_of_order"])
def test_rejects_duplicate_missing_and_out_of_order_cells(
    seeded: Seeded,
    tmp_path: Path,
    damage: str,
) -> None:
    path = tmp_path / f"{damage}.jsonl"
    records, _ = _write_valid_run(path, seeded)
    attempt_index, attempt = _find_record(
        records,
        "generation_attempt",
        trap_id="K01",
        arm="x0",
        repeat=1,
        attempt=1,
    )
    final_index, final = _find_record(
        records,
        "generation",
        trap_id="K01",
        arm="x0",
        repeat=1,
    )
    if damage == "duplicate":
        records[final_index + 1 : final_index + 1] = [deepcopy(attempt), deepcopy(final)]
    elif damage == "missing":
        del records[final_index]
    else:
        records[attempt_index]["repeat"] = 2
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="out-of-order|mismatched|missing"):
        _inspect(path, seeded)


@pytest.mark.parametrize("attempt_number", [0, 3])
def test_rejects_attempt_numbers_outside_one_or_two(
    seeded: Seeded,
    tmp_path: Path,
    attempt_number: int,
) -> None:
    path = tmp_path / "attempt.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, attempt = _find_record(
        records,
        "generation_attempt",
        trap_id="K01",
        arm="x0",
        repeat=1,
        attempt=1,
    )
    attempt["attempt"] = attempt_number
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="attempt"):
        _inspect(path, seeded)


@pytest.mark.parametrize(
    ("record_kind", "field"),
    [
        ("generation_attempt", "segment_length"),
        ("generation_attempt", "cumulative_length"),
        ("generation", "length"),
    ],
)
def test_rejects_mismatched_stored_lengths(
    seeded: Seeded,
    tmp_path: Path,
    record_kind: str,
    field: str,
) -> None:
    path = tmp_path / f"bad-{field}.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, record = _find_record(
        records,
        record_kind,
        trap_id="K01",
        arm="x0",
        repeat=1,
        **({"attempt": 1} if record_kind == "generation_attempt" else {}),
    )
    measurement = record[field]
    assert isinstance(measurement, dict)
    measurement["actual_units"] += 1
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="length|measurement|count"):
        _inspect(path, seeded)


def test_rejects_a_final_that_is_not_the_exact_attempt_concatenation(
    seeded: Seeded,
    tmp_path: Path,
) -> None:
    path = tmp_path / "concat.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, final = _find_record(
        records,
        "generation",
        trap_id="K01",
        arm="x0",
        repeat=0,
    )
    final["output"] = str(final["output"]) + "伪"
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="concatenation"):
        _inspect(path, seeded)


def test_rejects_an_altered_attempt_count(seeded: Seeded, tmp_path: Path) -> None:
    path = tmp_path / "attempt-count.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, final = _find_record(
        records,
        "generation",
        trap_id="K01",
        arm="x0",
        repeat=1,
    )
    final["attempt_count"] = 2
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="attempt"):
        _inspect(path, seeded)


@pytest.mark.parametrize("record_kind", ["reference", "generation"])
def test_rejects_altered_recorded_leak_results(
    seeded: Seeded,
    tmp_path: Path,
    record_kind: str,
) -> None:
    path = tmp_path / f"leak-{record_kind}.jsonl"
    records, _ = _write_valid_run(path, seeded)
    identity = {"trap_id": "K01"}
    if record_kind == "generation":
        identity.update({"arm": "x1", "repeat": 1})
    _, record = _find_record(records, record_kind, **identity)
    leak = record["leak"]
    assert isinstance(leak, dict)
    leak["knows_violation"] = not leak["knows_violation"]
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="leak result was altered"):
        _inspect(path, seeded)


def test_rejects_an_altered_target_leaked_flag(seeded: Seeded, tmp_path: Path) -> None:
    path = tmp_path / "leaked.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, final = _find_record(
        records,
        "generation",
        trap_id="F01",
        arm="x0",
        repeat=1,
    )
    final["leaked"] = not final["leaked"]
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="leaked flag was altered"):
        _inspect(path, seeded)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "sk-test"),
        ("Authorization", "Bearer sk-test"),
        ("bearer_token", "sk-test"),
        ("client_secret", "secret"),
    ],
)
def test_rejects_recursive_secret_shaped_keys(
    seeded: Seeded,
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    path = tmp_path / "secret.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, attempt = _find_record(
        records,
        "generation_attempt",
        trap_id="K01",
        arm="x1",
        repeat=1,
        attempt=1,
    )
    messages = attempt["messages"]
    assert isinstance(messages, list)
    messages[0]["metadata"] = {"nested": {key: value}}
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="secret-shaped"):
        _inspect(path, seeded)


def test_rejects_a_bearer_credential_even_under_an_innocent_key(
    seeded: Seeded,
    tmp_path: Path,
) -> None:
    path = tmp_path / "bearer.jsonl"
    records, _ = _write_valid_run(path, seeded)
    records[0]["note"] = "Bearer sk-not-allowed"
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="Bearer credential"):
        _inspect(path, seeded)


def test_api_key_set_is_only_allowed_as_a_boolean(seeded: Seeded, tmp_path: Path) -> None:
    path = tmp_path / "api-key-set.jsonl"
    records, _ = _write_valid_run(path, seeded)
    config = records[0]["config"]
    assert isinstance(config, dict)
    config["api_key_set"] = "yes"
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="api_key_set must be a boolean"):
        _inspect(path, seeded)


@pytest.mark.parametrize(
    "damage",
    ["protocol", "length_profile", "counting_rule", "continuation", "call_plan"],
)
def test_rejects_protocol_and_header_mismatches(
    seeded: Seeded,
    tmp_path: Path,
    damage: str,
) -> None:
    path = tmp_path / f"header-{damage}.jsonl"
    records, _ = _write_valid_run(path, seeded)
    header = records[0]
    if damage == "protocol":
        header["protocol"] = "older protocol"
    elif damage == "length_profile":
        profile = header["length_profile"]
        assert isinstance(profile, dict)
        profile["min_units"] = 1999
    elif damage == "counting_rule":
        header["counting_rule"] = "another-counter"
    elif damage == "continuation":
        header["continuation"] = {"max_attempts": 3, "trigger": "under_min_only"}
    else:
        call_plan = header["call_plan"]
        assert isinstance(call_plan, dict)
        call_plan["reasoning_effective"] = "off"
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="protocol|profile|counting|continuation|call_plan"):
        _inspect(path, seeded)


def test_rejects_duplicate_headers(seeded: Seeded, tmp_path: Path) -> None:
    path = tmp_path / "headers.jsonl"
    records, _ = _write_valid_run(path, seeded)
    records.insert(1, deepcopy(records[0]))
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="exactly one header"):
        _inspect(path, seeded)


def test_rejects_a_terminal_length_invalid_without_returning_a_verdict(
    seeded: Seeded,
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.jsonl"
    records, _ = _write_valid_run(path, seeded)
    final_index, final = _find_record(
        records,
        "generation",
        trap_id="K01",
        arm="x0",
        repeat=1,
    )
    records[final_index] = {
        "kind": "length_invalid",
        **{key: final[key] for key in ("trap_id", "trap_kind", "chapter", "cast", "arm", "form", "repeat")},
        "output": final["output"],
        "length": final["length"],
        "attempt_count": final["attempt_count"],
        "truncated": False,
        "reasons": ["under_min"],
    }
    del records[final_index + 1 :]
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="length_invalid.*scientific verdict"):
        _inspect(path, seeded)


def test_rejects_a_length_limited_final_recorded_as_valid(
    seeded: Seeded,
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, attempt = _find_record(
        records,
        "generation_attempt",
        trap_id="K01",
        arm="x0",
        repeat=1,
        attempt=1,
    )
    attempt["finish_reason"] = "length"
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="length-limited"):
        _inspect(path, seeded)


def test_rejects_an_altered_confound_report(seeded: Seeded, tmp_path: Path) -> None:
    path = tmp_path / "confound.jsonl"
    records, _ = _write_valid_run(path, seeded)
    _, confound = _find_record(records, "confound", trap_id="K01")
    report = confound["report"]
    assert isinstance(report, dict)
    report["ok"] = not report["ok"]
    _rewrite(path, records)

    with pytest.raises(EvidenceError, match="confound report was altered"):
        _inspect(path, seeded)
