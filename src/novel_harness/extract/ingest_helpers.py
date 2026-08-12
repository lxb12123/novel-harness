"""事务性抽取落库服务共用的纯类型辅助。"""

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
    """传入的章节不是其 ID 所指的那份不可变快照。"""


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
    """一次事务性落库的深度不可变摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_event_count: int = Field(ge=0)
    discarded_event_count: int = Field(ge=0)
    valid_state_update_count: int = Field(ge=0)
    discarded: tuple[DiscardReason, ...] = ()
    event_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    proposal_count: int = Field(ge=0)

    clean_event_ids: tuple[str, ...] = ()
    """一条例外 bucket 都没进的事件 —— 自动升 CANON 的入口（1.2）。

    **是 `event_ids` 的子集，不是另一份来源。** 有默认值是为了老调用方；空元组的语义是
    「这次没有干净的」，与「这次没算」在这里刻意不区分：两者的安全动作都是不升。
    """

    clean_edge_ids: tuple[str, ...] = ()
    """同上，关系侧。"""


@dataclass(frozen=True, slots=True)
class PreparedStateUpdate:
    """一条已解析、已定位、但尚未写入任何东西的状态更新。"""

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
    """解析事件的名面列表，不让路人把整条事件拖死。

    未知名面直接丢弃：匿名配角与未声明事实在真书里是常态，M4_DESIGN 也规定
    路人不进图谱。歧义或错类名面仍整条拒收——那些是抽取器无法安全挑选的已知
    实体，产品从不替作者选择。两边都绝不猜测。
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
    """每个语义图键只保留最后一条已解析更新。"""

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
