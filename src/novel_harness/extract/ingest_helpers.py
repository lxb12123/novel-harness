"""Pure typed helpers shared by the transactional extraction ingestion service."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..graph import Edge, EdgeType, GraphStore, NodeLabel, StateSnapshot
from ..text.anchor import Located
from .analyze import SurfaceResolution, resolve_surfaces
from .locate import LocateOutcome, locate_quote
from .models import RawChapterAnalysis, RawStateUpdate

DiscardKind = Literal["event", "state_update", "character_profile"]


class ExtractionContextError(ValueError):
    """The supplied chapter is not the immutable snapshot named by its IDs."""


class DiscardOutcome(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    UNKNOWN_SURFACE = "UNKNOWN_SURFACE"
    AMBIGUOUS_SURFACE = "AMBIGUOUS_SURFACE"
    WRONG_LABEL = "WRONG_LABEL"


class DiscardReason(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DiscardKind
    index: int = Field(ge=0)
    outcome: DiscardOutcome
    detail: str


class ExtractionReport(BaseModel):
    """Deeply immutable summary of one transactional ingestion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_event_count: int = Field(ge=0)
    discarded_event_count: int = Field(ge=0)
    valid_state_update_count: int = Field(ge=0)
    discarded: tuple[DiscardReason, ...] = ()
    event_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    proposal_count: int = Field(ge=0)


def resolution_map(
    graph: GraphStore,
    project_id: str,
    analysis: RawChapterAnalysis,
) -> dict[str, SurfaceResolution]:
    surfaces: list[str] = []
    for event in analysis.events:
        surfaces.extend((*event.participants, *event.knowers, *event.revealed_facts))
    for state in analysis.state_updates:
        surfaces.append(state.subject)
        if state.object is not None:
            surfaces.append(state.object)
        if state.dimension is not None:
            surfaces.append(state.dimension)
    surfaces.extend(profile.surface for profile in analysis.character_profiles)
    resolved = resolve_surfaces(graph, project_id, surfaces)
    return {item.surface: item for item in resolved.resolutions}


def resolve_ids(
    kind: DiscardKind,
    index: int,
    surfaces: Sequence[str],
    expected: NodeLabel,
    resolutions: dict[str, SurfaceResolution],
) -> tuple[list[str], DiscardReason | None]:
    ids: list[str] = []
    for surface in surfaces:
        resolution = resolutions[surface]
        if (
            resolution.unknown
            or resolution.ambiguous
            or resolution.candidates[0].label is not expected
        ):
            return [], surface_reason(kind, index, surface, expected, resolution)
        ids.append(resolution.candidates[0].id)
    return ids, None


def surface_reason(
    kind: DiscardKind,
    index: int,
    surface: str,
    expected: NodeLabel,
    resolution: SurfaceResolution,
) -> DiscardReason:
    if resolution.unknown:
        outcome = DiscardOutcome.UNKNOWN_SURFACE
        detail = f"{surface!r} did not resolve"
    elif resolution.ambiguous:
        outcome = DiscardOutcome.AMBIGUOUS_SURFACE
        detail = f"{surface!r} resolved to multiple nodes"
    else:
        outcome = DiscardOutcome.WRONG_LABEL
        detail = (
            f"{surface!r} resolved as {resolution.candidates[0].label.value}, "
            f"expected {expected.value}"
        )
    return DiscardReason(kind=kind, index=index, outcome=outcome, detail=detail)


def locate_evidence(
    kind: Literal["event", "state_update"],
    index: int,
    paras: Sequence[str],
    quote: str,
) -> tuple[Located, None] | tuple[None, DiscardReason]:
    result = locate_quote(paras, quote, min_ratio=0.90)
    if result.outcome in {LocateOutcome.EXACT, LocateOutcome.FUZZY}:
        if result.located is None:
            raise RuntimeError("successful quote location omitted its source span")
        return result.located, None
    return None, DiscardReason(
        kind=kind,
        index=index,
        outcome=DiscardOutcome(result.outcome.value),
        detail=f"quote location failed with ratio={result.ratio:.6f}",
    )


def find_conflict(
    raw: RawStateUpdate,
    subject_id: str,
    target_id: str,
    state: StateSnapshot,
) -> dict[str, object] | None:
    current: Edge | None = None
    if raw.kind == "location":
        current = next(
            (edge for edge in state.edges if edge.type is EdgeType.LOCATED_AT),
            None,
        )
        if current is None or current.dst == target_id:
            return None
        value: str | None = None
    elif raw.kind == "state":
        current = next(
            (
                edge
                for edge in state.edges
                if edge.type is EdgeType.HAS_STATE and edge.dst == target_id
            ),
            None,
        )
        if current is None or current.props.value == raw.value:
            return None
        value = current.props.value
    else:
        current = next(
            (
                edge
                for edge in state.edges
                if edge.type is EdgeType.RELATED_TO
                and edge.peer_of(subject_id) == target_id
            ),
            None,
        )
        if current is None or current.props.value == raw.value:
            return None
        value = current.props.value
    return {
        "edge_id": current.id,
        "subject_id": subject_id,
        "target_id": (
            current.peer_of(subject_id)
            if current.type is EdgeType.RELATED_TO
            else current.dst
        ),
        "value": value,
    }
