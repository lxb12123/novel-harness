"""章节分析的纯校验与解析辅助。"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ..graph.models import NodeRef
from ..graph.store import StoryGraph

from .models import RawChapterAnalysis

__all__ = [
    "AnalysisFormatError",
    "ResolvedAnalysis",
    "ResolutionContractError",
    "SurfaceResolution",
    "parse_analysis",
    "resolve_surfaces",
]


class AnalysisFormatError(ValueError):
    """模型响应不是预期的那份分析 JSON。"""


class ResolutionContractError(ValueError):
    """StoryGraph.resolve 返回的数据违反了对齐契约。"""


def parse_analysis(text: str) -> RawChapterAnalysis:
    """校验一份模型响应；不做修复、不剥壳、不重试。"""
    try:
        return RawChapterAnalysis.model_validate_json(text)
    except ValidationError as exc:
        raise AnalysisFormatError("chapter analysis is not valid schema JSON") from exc


class SurfaceResolution(BaseModel):
    """一个名面的全部图谱候选，并把唯一性显式化。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    surface: str
    candidates: tuple[NodeRef, ...] = ()
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
    """一份分析的按序、去重名面解析结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolutions: tuple[SurfaceResolution, ...] = ()


def resolve_surfaces(
    graph: StoryGraph,
    project_id: str,
    surfaces: Sequence[str],
) -> ResolvedAnalysis:
    """解析名字；不创建节点、也不在歧义别名之间替作者选择。"""
    ordered_surfaces = list(dict.fromkeys(surfaces))
    graph_resolutions = graph.resolve(
        project_id,
        ordered_surfaces,
        rules_only=False,
    )
    expected_surfaces = tuple(ordered_surfaces)
    actual_surfaces = tuple(resolution.surface for resolution in graph_resolutions)
    if actual_surfaces != expected_surfaces:
        raise ResolutionContractError(
            "StoryGraph.resolve must return exactly one same-order result per surface: "
            f"expected {expected_surfaces!r}, got {actual_surfaces!r}"
        )

    resolved: list[SurfaceResolution] = []
    for surface, resolution in zip(ordered_surfaces, graph_resolutions, strict=True):
        candidates: list[NodeRef] = []
        candidate_ids: set[str] = set()
        for hit in resolution.hits:
            if hit.node.project_id != project_id:
                raise ResolutionContractError(
                    "StoryGraph.resolve returned a candidate from another project: "
                    f"surface={surface!r}, node={hit.node.id!r}"
                )
            if hit.node.id in candidate_ids:
                raise ResolutionContractError(
                    "StoryGraph.resolve returned a duplicate candidate node id: "
                    f"surface={surface!r}, node={hit.node.id!r}"
                )
            candidate_ids.add(hit.node.id)
            candidates.append(NodeRef.of(hit.node))
        candidate_tuple = tuple(candidates)
        resolved.append(
            SurfaceResolution(
                surface=surface,
                candidates=candidate_tuple,
                unique_id=candidate_tuple[0].id if len(candidate_tuple) == 1 else None,
            )
        )
    return ResolvedAnalysis(resolutions=tuple(resolved))
