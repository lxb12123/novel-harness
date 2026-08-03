from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novel_harness.extract.analyze import (
    AnalysisFormatError,
    ResolvedAnalysis,
    resolve_surfaces,
    parse_analysis,
)
from novel_harness.extract.models import RawChapterAnalysis
from novel_harness.extract.prompt import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    build_analysis_messages,
)
from novel_harness.graph import AliasHit, AliasKind, Node, NodeLabel, Resolution


def _hit(node_id: str, name: str, *, usable: bool = True) -> AliasHit:
    return AliasHit(
        node=Node(
            id=node_id,
            project_id="project-1",
            label=NodeLabel.CHARACTER,
            name=name,
        ),
        kind=AliasKind.ALIAS,
        usable_for_rules=usable,
    )


class FakeStoryGraph:
    def __init__(self, by_surface: dict[str, list[AliasHit]]) -> None:
        self.by_surface = by_surface
        self.calls: list[tuple[str, tuple[str, ...], bool]] = []

    def resolve(
        self,
        project_id: str,
        surfaces: list[str] | tuple[str, ...] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        assert surfaces is not None
        self.calls.append((project_id, tuple(surfaces), rules_only))
        return [
            Resolution(surface=surface, hits=self.by_surface.get(surface, []))
            for surface in surfaces
        ]


def _valid_json() -> str:
    return json.dumps(
        {
            "events": [
                {
                    "summary": "顾清音交出密信",
                    "quote": "顾清音从袖中取出密信，轻轻放在案上。",
                    "participants": ["顾清音"],
                    "knowers": ["顾清音", "萧决"],
                    "revealed_facts": ["顾清音持有密信"],
                    "confidence": 0.95,
                }
            ],
            "state_updates": [],
            "character_profiles": [],
        },
        ensure_ascii=False,
    )


def test_parse_analysis_validates_json_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    original = RawChapterAnalysis.model_validate_json
    calls: list[str] = []

    def tracked(cls: type[RawChapterAnalysis], text: str) -> RawChapterAnalysis:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(RawChapterAnalysis, "model_validate_json", classmethod(tracked))

    parsed = parse_analysis(_valid_json())

    assert parsed.events[0].summary == "顾清音交出密信"
    assert calls == [_valid_json()]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "```json\n" + _valid_json() + "\n```",
        _valid_json() + "\nanalysis complete",
        '{"events": [}',
        '{"events": [], "chapter": 12}',
    ],
)
def test_parse_analysis_never_repairs_or_strips_invalid_output(text: str) -> None:
    with pytest.raises(AnalysisFormatError) as exc_info:
        parse_analysis(text)

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_analysis_prompt_is_versioned_deterministic_and_includes_exact_text() -> None:
    chapter = "第一段。\n\n第二段含有 {JSON-looking} 原文。"

    first = build_analysis_messages(chapter)
    second = build_analysis_messages(chapter)

    assert ANALYSIS_SCHEMA_VERSION
    assert ANALYSIS_PROMPT_VERSION
    assert first == second
    assert first[-1] == {"role": "user", "content": chapter}


def test_analysis_prompt_pins_safety_and_shape_rules() -> None:
    system = build_analysis_messages("正文")[0]["content"]

    assert "JSON only" in system
    assert "story-beat" in system
    assert "1-12" in system
    assert "verbatim" in system
    assert "10-120" in system
    assert "location" in system and "object" in system
    assert "state" in system and "dimension" in system and "value" in system
    assert "relationship" in system
    for forbidden in ("IDs", "chapter", "scope", "status"):
        assert forbidden in system
    assert "surface names" in system


def test_resolve_surfaces_preserves_order_candidates_and_unknowns() -> None:
    unique = _hit("character-1", "顾清音")
    ambiguous = [_hit("character-2", "萧决"), _hit("character-3", "林渡")]
    graph = FakeStoryGraph({"顾姑娘": [unique], "师兄": ambiguous})

    result = resolve_surfaces(
        graph,  # type: ignore[arg-type]
        "project-1",
        ["顾姑娘", "师兄", "陌生人", "顾姑娘", "师兄"],
    )

    assert isinstance(result, ResolvedAnalysis)
    assert [item.surface for item in result.resolutions] == [
        "顾姑娘",
        "师兄",
        "陌生人",
    ]
    assert result.resolutions[0].unique_id == "character-1"
    assert [candidate.id for candidate in result.resolutions[0].candidates] == ["character-1"]
    assert result.resolutions[0].ambiguous is False
    assert result.resolutions[0].unknown is False

    assert result.resolutions[1].unique_id is None
    assert [candidate.id for candidate in result.resolutions[1].candidates] == [
        "character-2",
        "character-3",
    ]
    assert result.resolutions[1].ambiguous is True
    assert result.resolutions[1].unknown is False

    assert result.resolutions[2].unique_id is None
    assert result.resolutions[2].candidates == []
    assert result.resolutions[2].ambiguous is False
    assert result.resolutions[2].unknown is True
    assert graph.calls == [("project-1", ("顾姑娘", "师兄", "陌生人"), False)]


def test_resolve_surfaces_uses_unique_unusable_alias_without_rules_filtering() -> None:
    graph = FakeStoryGraph({"小顾": [_hit("character-1", "顾清音", usable=False)]})

    result = resolve_surfaces(graph, "project-1", ["小顾"])  # type: ignore[arg-type]

    assert result.resolutions[0].unique_id == "character-1"
    assert result.resolutions[0].candidates[0].model_dump() == {
        "id": "character-1",
        "label": NodeLabel.CHARACTER,
        "name": "顾清音",
    }
