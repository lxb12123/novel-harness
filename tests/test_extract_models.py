from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from novel_harness.extract.models import (
    RawChapterAnalysis,
    RawCharacterProfile,
    RawEvent,
    RawStateUpdate,
)


def _event(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "summary": "顾清音交出密信",
        "quote": "顾清音从袖中取出密信，轻轻放在案上。",
        "participants": ["顾清音"],
        "knowers": ["顾清音", "萧决"],
        "revealed_facts": ["顾清音持有密信"],
        "confidence": 0.95,
    }
    data.update(overrides)
    return data


def _state(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "kind": "location",
        "subject": "顾清音",
        "object": "临江客栈",
        "quote": "顾清音推门走进临江客栈，收起了纸伞。",
        "confidence": 0.91,
    }
    data.update(overrides)
    return data


def _analysis(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "events": [_event()],
        "state_updates": [],
        "character_profiles": [],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "model",
    [
        RawEvent.model_validate(_event()),
        RawCharacterProfile(surface="顾清音", confidence=0.8),
        RawStateUpdate.model_validate(_state()),
        RawChapterAnalysis.model_validate(_analysis()),
    ],
)
def test_raw_models_are_frozen_and_forbid_unknown_fields(model: object) -> None:
    assert model.__class__.model_config["frozen"] is True
    assert model.__class__.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError, match="frozen"):
        model.confidence = 0.1  # type: ignore[attr-defined]


def test_raw_collection_fields_are_deeply_immutable_tuples() -> None:
    analysis = RawChapterAnalysis.model_validate(
        _analysis(
            state_updates=[_state()],
            character_profiles=[{"surface": "顾清音", "confidence": 0.8}],
        )
    )

    assert isinstance(analysis.events, tuple)
    assert isinstance(analysis.state_updates, tuple)
    assert isinstance(analysis.character_profiles, tuple)
    assert isinstance(analysis.events[0].participants, tuple)
    assert isinstance(analysis.events[0].knowers, tuple)
    assert isinstance(analysis.events[0].revealed_facts, tuple)
    serialized = json.loads(analysis.model_dump_json())
    assert isinstance(serialized["events"], list)
    assert isinstance(serialized["state_updates"], list)
    assert isinstance(serialized["character_profiles"], list)
    assert isinstance(serialized["events"][0]["participants"], list)
    with pytest.raises(AttributeError):
        analysis.events.append(analysis.events[0])  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        analysis.events[0].participants[0] = "被篡改"  # type: ignore[index]


@pytest.mark.parametrize("missing", ["events", "state_updates", "character_profiles"])
def test_chapter_analysis_requires_every_top_level_collection(missing: str) -> None:
    payload = _analysis()
    payload.pop(missing)

    with pytest.raises(ValidationError, match=missing):
        RawChapterAnalysis.model_validate(payload)


def test_chapter_analysis_requires_at_least_one_event() -> None:
    with pytest.raises(ValidationError, match="events"):
        RawChapterAnalysis.model_validate(_analysis(events=[]))


@pytest.mark.parametrize("missing", ["participants", "knowers", "revealed_facts"])
def test_event_requires_every_surface_collection(missing: str) -> None:
    payload = _event()
    payload.pop(missing)

    with pytest.raises(ValidationError, match=missing):
        RawEvent.model_validate(payload)


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -math.inf])
def test_raw_models_reject_nonfinite_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate(_event(confidence=confidence))
    with pytest.raises(ValidationError):
        RawCharacterProfile(surface="顾清音", confidence=confidence)
    with pytest.raises(ValidationError):
        RawStateUpdate.model_validate(_state(confidence=confidence))


@pytest.mark.parametrize(
    ("payload", "forbidden_field"),
    [
        (_analysis(chapter=12), "chapter"),
        (_analysis(events=[_event(id="evt-1")]), "id"),
        (_analysis(events=[_event(scope="CANON")]), "scope"),
        (_analysis(events=[_event(status="ACTIVE")]), "status"),
        (
            _analysis(
                character_profiles=[{"surface": "顾清音", "confidence": 0.8, "node_id": "node-1"}]
            ),
            "node_id",
        ),
    ],
)
def test_chapter_analysis_rejects_business_fields(
    payload: dict[str, object], forbidden_field: str
) -> None:
    with pytest.raises(ValidationError, match=forbidden_field):
        RawChapterAnalysis.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _state(kind="location", object=None),
        _state(kind="location", dimension="修为"),
        _state(kind="location", value="客栈"),
        _state(kind="state", object=None, dimension=None, value="金丹"),
        _state(kind="state", object=None, dimension="修为", value=None),
        _state(kind="state", object="临江客栈", dimension="修为", value="金丹"),
        _state(kind="relationship", object=None, value="盟友"),
        _state(kind="relationship", object="萧决", value=None),
        _state(kind="relationship", object="萧决", dimension="关系", value="盟友"),
    ],
)
def test_state_update_rejects_invalid_field_combinations(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RawStateUpdate.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _state(kind="location"),
        _state(kind="state", object=None, dimension="修为", value="金丹"),
        _state(kind="relationship", object="萧决", value="盟友"),
    ],
)
def test_state_update_accepts_each_exact_shape(payload: dict[str, object]) -> None:
    RawStateUpdate.model_validate(payload)


def test_chapter_analysis_caps_events_and_state_updates() -> None:
    RawChapterAnalysis.model_validate(_analysis(events=[_event() for _ in range(12)]))
    RawChapterAnalysis.model_validate(_analysis(state_updates=[_state() for _ in range(24)]))

    with pytest.raises(ValidationError):
        RawChapterAnalysis.model_validate(_analysis(events=[_event() for _ in range(13)]))
    with pytest.raises(ValidationError):
        RawChapterAnalysis.model_validate(_analysis(state_updates=[_state() for _ in range(25)]))


def test_short_quotes_are_ratio_capped_per_chapter() -> None:
    """短引语（<10 字）按本章引语总数的 20% 控制，且至少放行 1 条。"""
    short = "卧槽，陨石！"
    # 12 条引语：20% = 2，2 条短引语合法。
    RawChapterAnalysis.model_validate(
        _analysis(events=[_event(quote=short)] * 2 + [_event() for _ in range(10)])
    )
    # 12 条引语 3 条短引语 → 超比例，整章拒收。
    with pytest.raises(ValidationError):
        RawChapterAnalysis.model_validate(
            _analysis(events=[_event(quote=short)] * 3 + [_event() for _ in range(9)])
        )
    # 单条引语章节：比例算出来是 0，也至少放行 1 条。
    RawChapterAnalysis.model_validate(_analysis(events=[_event(quote=short)]))
    # 事件 + 状态更新合并计数：7 条引语允许 1 条，2 条短引语 → 拒。
    with pytest.raises(ValidationError):
        RawChapterAnalysis.model_validate(
            _analysis(
                events=[_event(quote=short)] + [_event() for _ in range(5)],
                state_updates=[_state(quote=short)],
            )
        )


def test_raw_field_length_boundaries_are_enforced() -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate(_event(summary=""))
    with pytest.raises(ValidationError):
        RawEvent.model_validate(_event(quote="短引语"))
    # 4–9 字的短引语在字段层合法（频率在章级控制）。
    RawEvent.model_validate(_event(quote="卧槽，陨石！"))
    with pytest.raises(ValidationError):
        RawEvent.model_validate(_event(quote="引" * 121))
    with pytest.raises(ValidationError):
        RawCharacterProfile(surface="顾", confidence=0.5)
    with pytest.raises(ValidationError):
        RawStateUpdate.model_validate(_state(subject=""))
