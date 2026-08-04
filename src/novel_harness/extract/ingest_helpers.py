"""Pure typed helpers shared by the transactional extraction ingestion service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
    SUPERSEDED_IN_ANALYSIS = "SUPERSEDED_IN_ANALYSIS"


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


@dataclass(frozen=True, slots=True)
class PreparedStateUpdate:
    """A fully resolved and located state update that has not written anything yet."""

    index: int
    raw: RawStateUpdate
    subject_id: str
    target_id: str
    located: Located
    graph_key: tuple[str, ...]


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


def resolve_event_surfaces(
    kind: DiscardKind,
    index: int,
    surfaces: Sequence[str],
    expected: NodeLabel,
    resolutions: dict[str, SurfaceResolution],
) -> tuple[list[str], list[str], DiscardReason | None]:
    """Resolve an event's surface list without letting bystanders kill the event.

    Unknown surfaces are dropped: anonymous supporting characters and undeclared
    facts are normal in real novels, and M4_DESIGN says bystanders never enter the
    graph.  Ambiguous or wrong-label surfaces still discard the whole item — those
    are known entities the extractor cannot safely pick, and the product never
    chooses for the author.  Never guesses either way.
    """
    ids: list[str] = []
    dropped: list[str] = []
    for surface in surfaces:
        resolution = resolutions[surface]
        if resolution.unknown:
            dropped.append(surface)
            continue
        if resolution.ambiguous or resolution.candidates[0].label is not expected:
            return [], dropped, surface_reason(kind, index, surface, expected, resolution)
        ids.append(resolution.candidates[0].id)
    return ids, dropped, None


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


def prepare_state_update(
    index: int,
    raw: RawStateUpdate,
    paras: Sequence[str],
    resolutions: dict[str, SurfaceResolution],
) -> tuple[PreparedStateUpdate, None] | tuple[None, DiscardReason]:
    subjects, reason = resolve_ids(
        "state_update", index, (raw.subject,), NodeLabel.CHARACTER, resolutions
    )
    if reason is not None:
        return None, reason
    target_surface = raw.dimension if raw.kind == "state" else raw.object
    expected = (
        NodeLabel.STATE_DIM
        if raw.kind == "state"
        else NodeLabel.LOCATION
        if raw.kind == "location"
        else NodeLabel.CHARACTER
    )
    targets, reason = resolve_ids(
        "state_update", index, (target_surface or "",), expected, resolutions
    )
    if reason is not None:
        return None, reason
    located, reason = locate_evidence("state_update", index, paras, raw.quote)
    if reason is not None:
        return None, reason
    subject_id, target_id = subjects[0], targets[0]
    edge_type = {
        "location": EdgeType.LOCATED_AT,
        "state": EdgeType.HAS_STATE,
        "relationship": EdgeType.RELATED_TO,
    }[raw.kind]
    if raw.kind == "location":
        graph_key = (edge_type.value, subject_id)
    elif raw.kind == "state":
        graph_key = (edge_type.value, subject_id, target_id)
    else:
        graph_key = (edge_type.value, *sorted((subject_id, target_id)))
    return PreparedStateUpdate(
        index=index,
        raw=raw,
        subject_id=subject_id,
        target_id=target_id,
        located=located,
        graph_key=graph_key,
    ), None


def keep_last_state_updates(
    prepared: Sequence[PreparedStateUpdate],
) -> tuple[list[PreparedStateUpdate], list[DiscardReason]]:
    """Keep only the final resolved update for each semantic graph key."""

    final_index = {item.graph_key: item.index for item in prepared}
    retained: list[PreparedStateUpdate] = []
    discarded: list[DiscardReason] = []
    for item in prepared:
        winner = final_index[item.graph_key]
        if item.index == winner:
            retained.append(item)
            continue
        discarded.append(
            DiscardReason(
                kind="state_update",
                index=item.index,
                outcome=DiscardOutcome.SUPERSEDED_IN_ANALYSIS,
                detail=f"superseded by state_update[{winner}] for {item.graph_key!r}",
            )
        )
    return retained, discarded


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
