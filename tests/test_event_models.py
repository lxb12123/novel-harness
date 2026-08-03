"""M4 event-memory domain contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_harness.events import (
    CharacterProfilePatch,
    EventCharacterRole,
    EventView,
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
