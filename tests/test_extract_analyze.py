from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novel_harness.extract.analyze import (
    AnalysisFormatError,
    ResolvedAnalysis,
    ResolutionContractError,
    SurfaceResolution,
    resolve_surfaces,
    parse_analysis,
)
from novel_harness.extract.models import RawChapterAnalysis
from novel_harness.extract.prompt import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    build_analysis_messages,
)
from novel_harness.graph import AliasHit, AliasKind, Node, NodeLabel, NodeRef, Resolution


def _hit(
    node_id: str,
    name: str,
    *,
    usable: bool = True,
    project_id: str = "project-1",
) -> AliasHit:
    return AliasHit(
        node=Node(
            id=node_id,
            project_id=project_id,
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


class FixedResolutionGraph:
    def __init__(self, resolutions: list[Resolution]) -> None:
        self.resolutions = resolutions

    def resolve(
        self,
        project_id: str,
        surfaces: list[str] | tuple[str, ...] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        return self.resolutions


def _valid_json() -> str:
    return json.dumps(
        {
            "events": [
                {
                    "summary": "顾清音交出密信",
                    "quote": "顾清音从袖中取出密信，轻轻放在案上。",
                    "participants": ["顾清音"],
                    "knowers": ["顾清音", "萧决"],
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
    assert "120" in system
    assert "30%" in system
    assert "minimum quote length" in system
    assert "location" in system and "object" in system
    assert "state" in system and "dimension" in system and "value" in system
    assert "relationship" in system
    # 2026-08-04 真模型首跑回归：state update 的判别字段和 confidence 必须点名，
    # 否则模型会自创 "shape" 或漏掉 kind，整章被严格解析拒收。
    assert '"kind"' in system
    assert '"kind": "location"' in system
    assert "confidence" in system
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
    assert isinstance(result.resolutions, tuple)
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
    assert result.resolutions[2].candidates == ()
    assert result.resolutions[2].ambiguous is False
    assert result.resolutions[2].unknown is True
    assert graph.calls == [("project-1", ("顾姑娘", "师兄", "陌生人"), False)]

    assert isinstance(result.resolutions[1].candidates, tuple)
    with pytest.raises(AttributeError):
        result.resolutions.append(result.resolutions[0])  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        result.resolutions[1].candidates.append(  # type: ignore[attr-defined]
            result.resolutions[1].candidates[0]
        )


def test_resolve_surfaces_uses_unique_unusable_alias_without_rules_filtering() -> None:
    graph = FakeStoryGraph({"小顾": [_hit("character-1", "顾清音", usable=False)]})

    result = resolve_surfaces(graph, "project-1", ["小顾"])  # type: ignore[arg-type]

    assert result.resolutions[0].unique_id == "character-1"
    assert result.resolutions[0].candidates[0].model_dump() == {
        "id": "character-1",
        "label": NodeLabel.CHARACTER,
        "name": "顾清音",
    }


@pytest.mark.parametrize(
    ("candidates", "unique_id"),
    [
        ((), "character-1"),
        ((NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),), None),
        ((NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),), "wrong"),
        (
            (
                NodeRef(id="character-1", label=NodeLabel.CHARACTER, name="顾清音"),
                NodeRef(id="character-2", label=NodeLabel.CHARACTER, name="萧决"),
            ),
            "character-1",
        ),
    ],
)
def test_surface_resolution_rejects_illegal_unique_id_states(
    candidates: tuple[NodeRef, ...], unique_id: str | None
) -> None:
    with pytest.raises(ValidationError, match="unique_id"):
        SurfaceResolution(
            surface="称呼",
            candidates=candidates,
            unique_id=unique_id,
        )


@pytest.mark.parametrize(
    "returned_surfaces",
    [
        ("顾姑娘",),
        ("顾姑娘", "师兄", "额外"),
        ("顾姑娘", "顾姑娘"),
        ("师兄", "顾姑娘"),
    ],
)
def test_resolve_surfaces_rejects_misaligned_graph_results(
    returned_surfaces: tuple[str, ...],
) -> None:
    graph = FixedResolutionGraph(
        [Resolution(surface=surface, hits=[]) for surface in returned_surfaces]
    )

    with pytest.raises(ResolutionContractError, match="StoryGraph.resolve"):
        resolve_surfaces(
            graph,  # type: ignore[arg-type]
            "project-1",
            ["顾姑娘", "师兄", "顾姑娘"],
        )


def test_resolve_surfaces_rejects_foreign_project_candidates() -> None:
    graph = FakeStoryGraph({"顾姑娘": [_hit("character-1", "顾清音", project_id="project-2")]})

    with pytest.raises(ResolutionContractError, match="project"):
        resolve_surfaces(graph, "project-1", ["顾姑娘"])  # type: ignore[arg-type]


def test_resolve_surfaces_rejects_duplicate_candidate_node_ids() -> None:
    duplicate = _hit("character-1", "顾清音")
    graph = FakeStoryGraph({"顾姑娘": [duplicate, duplicate]})

    with pytest.raises(ResolutionContractError, match="duplicate"):
        resolve_surfaces(graph, "project-1", ["顾姑娘"])  # type: ignore[arg-type]
