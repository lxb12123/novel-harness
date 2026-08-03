"""M4 event-memory domain contracts."""

from __future__ import annotations

from typing import get_type_hints

import pytest
from pydantic import ValidationError

import novel_harness.events as event_contracts
from novel_harness.decisions import quote_hash
from novel_harness.events import (
    CharacterProfilePatch,
    EventCharacterRole,
    EventView,
    ProposalAuditSnapshot,
    ProposalCreate,
    ProposalRecord,
    ProposalResolutionMark,
    ProposalStatus,
    ProposalStore,
    ProvisionalEventSpec,
    StoryEvent,
)
from novel_harness.graph import (
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeRef,
)


def _provisional_event(**overrides: object) -> ProvisionalEventSpec:
    values: dict[str, object] = {
        "project_id": "project:01JZ0000000000000000000000",
        "summary": "顾清音在渡口交给萧决一枚玄铁令。",
        "evidence_id": "evidence:4956977b:01JZ0000000000000000000000",
        "participant_ids": ["character:4956977b:01JZ0000000000000000000001"],
        "knower_ids": ["character:4956977b:01JZ0000000000000000000001"],
        "revealed_fact_ids": ["secret:4956977b:01JZ0000000000000000000002"],
        "confidence": 0.91,
    }
    values.update(overrides)
    return ProvisionalEventSpec.model_validate(values)


def _proposal_create(**overrides: object) -> ProposalCreate:
    values: dict[str, object] = {
        "project_id": "project:01JZ0000000000000000000000",
        "kind": "entity_resolution",
        "summary": "顾姑娘可能指向顾清音。",
        "items": [{"surface": "顾姑娘", "confidence": 0.6}],
    }
    values.update(overrides)
    return ProposalCreate.model_validate(values)


def test_provisional_event_spec_is_frozen_and_forbids_scope() -> None:
    spec = _provisional_event()

    with pytest.raises(ValidationError, match="frozen"):
        spec.summary = "改写后的摘要"
    with pytest.raises(ValidationError, match="information_scope"):
        _provisional_event(information_scope="CANON")


def test_character_profile_patch_is_frozen_and_forbids_extra_fields() -> None:
    patch = CharacterProfilePatch(gender="女", main_character=True)

    with pytest.raises(ValidationError, match="frozen"):
        patch.gender = "男"
    with pytest.raises(ValidationError, match="chapter_number"):
        CharacterProfilePatch.model_validate({"chapter_number": 12})


def test_node_props_exposes_only_the_five_new_profile_contract_fields() -> None:
    props = NodeProps(gender="女", personality="谨慎", main_character=True)

    assert props.gender == "女"
    assert props.personality == "谨慎"
    assert props.background is None
    assert props.character_notes is None
    assert props.main_character is True


def test_story_event_and_event_view_are_immutable_pydantic_outputs() -> None:
    event = StoryEvent(
        id="event:4956977b:01JZ0000000000000000000003",
        project_id="project:01JZ0000000000000000000000",
        chapter_number=12,
        summary="顾清音在渡口交给萧决一枚玄铁令。",
        information_scope=InformationScope.PROVISIONAL,
        status=EdgeStatus.ACTIVE,
        confidence=0.91,
        source=EdgeSource.EXTRACTOR,
        evidence_id="evidence:4956977b:01JZ0000000000000000000000",
        evidence_status=EvidenceStatus.FRESH,
    )
    participant = NodeRef(id="character:one", label=NodeLabel.CHARACTER, name="顾清音")
    view = EventView(event=event, participants=[participant])

    assert view.event.chapter_number == 12
    assert view.participants == [participant]
    assert view.knowers == []
    assert view.revealed_facts == []
    assert {role.value for role in EventCharacterRole} == {"participant", "knower"}
    with pytest.raises(ValidationError, match="frozen"):
        event.summary = "改写"
    with pytest.raises(ValidationError, match="frozen"):
        view.event = event


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_provisional_event_confidence_is_bounded(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        _provisional_event(confidence=confidence)


def test_proposal_create_exposes_structured_items_and_derived_count() -> None:
    items = [{"surface": "顾姑娘", "confidence": 0.6}]

    proposal = _proposal_create(items=items)

    assert proposal.items == items
    assert proposal.item_count == len(items)
    dumped = proposal.model_dump(mode="json")
    assert dumped["items"] == items
    assert dumped["item_count"] == len(items)
    assert "items_json" not in dumped


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("items_json", '[{"surface": "顾姑娘"}]'),
        ("item_count", 99),
    ],
)
def test_proposal_create_rejects_storage_shaped_or_caller_derived_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=field):
        _proposal_create(**{field: value})


def test_proposal_create_requires_at_least_one_clustered_item() -> None:
    with pytest.raises(ValidationError, match="items"):
        _proposal_create(items=[])


def test_proposal_create_rejects_non_json_item_values() -> None:
    class ArbitraryValue:
        pass

    with pytest.raises(ValidationError, match="items"):
        _proposal_create(items=[{"surface": ArbitraryValue()}])


@pytest.mark.parametrize(
    "invalid_number",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_proposal_create_rejects_nested_non_finite_numbers(invalid_number: float) -> None:
    with pytest.raises(ValidationError, match="strict UTF-8 JSON"):
        _proposal_create(items=[{"nested": {"numbers": [1, invalid_number]}}])


def test_proposal_create_rejects_lone_surrogate_strings() -> None:
    with pytest.raises(ValidationError, match="strict UTF-8 JSON"):
        _proposal_create(items=[{"surface": "顾\ud800姑娘"}])


def test_proposal_create_accepts_multilingual_structured_json() -> None:
    items = [
        {
            "人物": "顾清音",
            "称呼": ["顾姑娘", "Gu Qingyin", "グー・チンイン", "🙂"],
            "meta": {"章节": 10, "已确认": False, "备注": None},
        }
    ]

    proposal = _proposal_create(items=items)

    assert proposal.items == items


@pytest.mark.parametrize("status", list(ProposalStatus))
def test_proposal_record_exposes_structured_items_for_every_lifecycle_status(
    status: ProposalStatus,
) -> None:
    items = [{"surface": "顾姑娘", "confidence": 0.6}]

    record = ProposalRecord(
        id="proposal:4956977b:01JZ0000000000000000000004",
        created_at="2026-08-03T12:00:00Z",
        status=status,
        **_proposal_create(items=items).model_dump(exclude={"item_count"}),
    )

    assert record.status is status
    assert record.items == items
    assert record.item_count == len(items)
    assert record.model_dump(mode="json")["item_count"] == len(items)


@pytest.mark.parametrize("status", ["ACCEPTED", "REJECTED", "EDITED"])
def test_proposal_resolution_mark_accepts_only_terminal_statuses(status: str) -> None:
    action = {"ACCEPTED": "accept", "REJECTED": "reject", "EDITED": "edit"}[status]
    canon_version = 1 if status in {"ACCEPTED", "EDITED"} else 0
    mark = ProposalResolutionMark(
        status=status,
        action=action,
        canon_version=canon_version,
        audit_envelope=ProposalAuditSnapshot(
            payload={
                "proposal_id": "proposal:test",
                "action": action,
                "status": status,
                "canon_version": canon_version,
                "kind": "test",
            }
        ),
    )

    assert mark.status.value == status
    assert isinstance(mark.status, event_contracts.ProposalResolutionStatus)


@pytest.mark.parametrize(
    ("quote_text", "quote_sha256"),
    [
        ("abc", None),
        (None, "0" * 64),
        ("abc", "0" * 64),
    ],
)
def test_proposal_audit_snapshot_requires_exact_quote_hash(
    quote_text: str | None,
    quote_sha256: str | None,
) -> None:
    with pytest.raises(ValidationError, match="quote_sha256"):
        ProposalAuditSnapshot(
            payload={"proposal_id": "proposal:test"},
            quote_text=quote_text,
            quote_sha256=quote_sha256,
        )

    valid = ProposalAuditSnapshot(
        payload={"proposal_id": "proposal:test"},
        quote_text="abc",
        quote_sha256=quote_hash("abc"),
    )
    assert valid.quote_sha256 == quote_hash("abc")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payload", {"number": float("nan")}),
        ("payload", {"number": float("inf")}),
        ("payload", {"number": float("-inf")}),
        ("payload", {"number": 2**63}),
        ("payload", {"text": "bad\ud800text"}),
        ("subject_name", "bad\ud800name"),
    ],
    ids=["nan", "positive-infinity", "negative-infinity", "int64", "payload-utf8", "subject-utf8"],
)
def test_proposal_audit_snapshot_uses_sqlites_strict_json_domain(
    field: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {"payload": {"proposal_id": "proposal:test"}}
    kwargs[field] = value

    with pytest.raises(ValidationError, match="finite int64 strict UTF-8 JSON"):
        ProposalAuditSnapshot(**kwargs)


def test_proposal_resolution_mark_rejects_pending_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        ProposalResolutionMark(
            status=ProposalStatus.PENDING,
            action="reject",
            canon_version=0,
            audit_envelope=ProposalAuditSnapshot(payload={}),
        )


def test_proposal_resolution_mark_rejects_action_status_mismatch() -> None:
    with pytest.raises(ValidationError, match="不一致"):
        ProposalResolutionMark(
            status="ACCEPTED",
            action="reject",
            canon_version=0,
            audit_envelope=ProposalAuditSnapshot(payload={}),
        )


def test_proposal_store_protocol_uses_concrete_proposal_contracts() -> None:
    assert get_type_hints(ProposalStore.create) == {
        "proposal": ProposalCreate,
        "return": ProposalRecord,
    }
    assert get_type_hints(ProposalStore.pending) == {
        "project_id": str,
        "chapter_number": int | None,
        "return": list[ProposalRecord],
    }
    assert get_type_hints(ProposalStore.get) == {
        "project_id": str,
        "proposal_id": str,
        "return": ProposalRecord | None,
    }
    assert get_type_hints(ProposalStore.mark_resolved) == {
        "proposal_id": str,
        "resolution": ProposalResolutionMark,
        "return": ProposalRecord,
    }
