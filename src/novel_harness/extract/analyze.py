"""章节分析的纯校验与解析辅助。"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ..draft.provider import unfenced

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
    """校验一份模型响应；**不做修复、不重试**。

    这两个「不」是有意的：改字段、补逗号、失败重试都是在替模型圆场，
    而圆场会把「这个模型/这份 prompt 产不出合规 JSON」这件事永久藏起来。

    ⚠️ **2026-09-06 起剥壳了**（`provider.unfenced`，全仓一份）。原来这里是三个「不」，
    而 `advisory_review._payload` 从一开始就剥——同一个模型、同一种毛病、两处不同的
    答案，而**严的那一侧在真书上失败了 23 次**。剥围栏和圆场是两回事：围栏是包装，
    里面那份 JSON 一个字节没变。

    这一层今天是**第二道防线**：第一道是 `response_format={"type": "json_object"}`
    （`StructuredCallPlan` 自动带上），端点认它的话围栏根本不会出现。

    ⚠️ **但拒绝的理由要留得下来**（2026-09-06）。这里原来只抛一句
    「chapter analysis is not valid schema JSON」，把 pydantic 那份**指到具体字段**
    的 `ValidationError` 整个扔了（`from exc` 只在栈上，落进 `extraction_run.errors_json`
    的是那句空话）。真书上 23 次 `analysis_format` 因此全都长得一模一样，
    看不出模型到底错在哪一格——和同日修掉的那三个 `except Exception` 是一个病。

    只带**前两条**错误、并砍到 400 字：这一列是给维护者看的诊断，不是全量转储；
    模型跑偏时 pydantic 能一口气报出几十条，全存进去只是把库撑大。
    """
    try:
        return RawChapterAnalysis.model_validate_json(unfenced(text))
    except ValidationError as exc:
        first = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:2]
        )
        raise AnalysisFormatError(
            f"chapter analysis is not valid schema JSON ({len(exc.errors())} errors) — {first}"[:400]
        ) from exc


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
