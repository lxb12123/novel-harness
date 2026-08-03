"""Pure validation and resolution helpers for chapter analysis."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..graph.models import NodeRef
from ..graph.store import StoryGraph

from .models import RawChapterAnalysis

__all__ = [
    "AnalysisFormatError",
    "ResolvedAnalysis",
    "SurfaceResolution",
    "parse_analysis",
    "resolve_surfaces",
]


class AnalysisFormatError(ValueError):
    """The model response was not exactly the expected analysis JSON."""


def parse_analysis(text: str) -> RawChapterAnalysis:
    """Validate one model response without repair, stripping, or retry."""
    try:
        return RawChapterAnalysis.model_validate_json(text)
    except ValidationError as exc:
        raise AnalysisFormatError("chapter analysis is not valid schema JSON") from exc


class SurfaceResolution(BaseModel):
    """All graph candidates for one surface, with uniqueness made explicit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    surface: str
    candidates: list[NodeRef] = Field(default_factory=list)
    unique_id: str | None = None

    @model_validator(mode="after")
    def unique_id_must_be_unambiguous(self) -> SurfaceResolution:
        expected = self.candidates[0].id if len(self.candidates) == 1 else None
        if self.unique_id != expected:
            raise ValueError("unique_id must identify the sole candidate")
        return self

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def unknown(self) -> bool:
        return not self.candidates


class ResolvedAnalysis(BaseModel):
    """Ordered, de-duplicated surface resolutions for one analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolutions: list[SurfaceResolution] = Field(default_factory=list)


def resolve_surfaces(
    graph: StoryGraph,
    project_id: str,
    surfaces: Sequence[str],
) -> ResolvedAnalysis:
    """Resolve names without creating nodes or choosing among ambiguous aliases."""
    ordered_surfaces = list(dict.fromkeys(surfaces))
    graph_resolutions = graph.resolve(
        project_id,
        ordered_surfaces,
        rules_only=False,
    )
    by_surface = {resolution.surface: resolution for resolution in graph_resolutions}

    resolved: list[SurfaceResolution] = []
    for surface in ordered_surfaces:
        resolution = by_surface.get(surface)
        candidates = (
            [NodeRef.of(hit.node) for hit in resolution.hits] if resolution is not None else []
        )
        resolved.append(
            SurfaceResolution(
                surface=surface,
                candidates=candidates,
                unique_id=candidates[0].id if len(candidates) == 1 else None,
            )
        )
    return ResolvedAnalysis(resolutions=resolved)
