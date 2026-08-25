"""`calibrate_scene` —— 模式二写前的**确定性**校准（Task 3 / ADR 0033）。

只做身份、时态、集合、覆盖和证据校准，**不做语义判断**（ADR 0005）：
「作者这句话是否与旧事实冲突」「人物为什么这么做」是 Agent 的
`MACHINE_INFERENCE` 张力，不进 `deterministic_conflicts`。

本模块对 graph / event / summary / manuscript / Canon / RememberedRule **只读**。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ..events import EventView
from ..graph import (
    Edge,
    EdgeSource,
    EdgeType,
    HEALTH_DIM_KEY,
    HealthValue,
    InformationScope,
    NodeLabel,
    NodeRef,
    StoryGraph,
)
from ..graph.store import StoreError
from ..ids import EntityType, new_id
from ..importer import chapter_path, read_chapter, text_digest
from .freshness import summary_freshness
from .models import (
    AgentVisibility,
    AuthorTurnRef,
    CalibrationReport,
    Completeness,
    CoverageReceipt,
    DeterministicConflict,
    EpistemicKind,
    EvidenceEnvelope,
    FactType,
    Freshness,
    IntendedCastMember,
    NormalizedCastMember,
    SceneProposal,
    Sealability,
    SourceWatermark,
    TargetChapterSnapshot,
    WriterVisibility,
)
from .ports import CalibrationEventSource, CalibrationSummarySource
from .render import render_evidence_text
from .visibility import agent_visibility_of, writer_visibility_of

CALIBRATION_SCHEMA_VERSION: str = "calibration.v1"
"""校准产物的 schema 版本。改了装配形状就改这个数（进 `source_watermark`）。"""

EVENT_CAP: int = 40
"""校准报告里事件的上限。超出标 `truncated`，不静默截断。"""


@dataclass(frozen=True)
class CalibrationInput:
    """校准层的全部输入。**连接不进这里**（`agent/ports.py` 的同一层纪律）。"""

    store: StoryGraph
    project_id: str
    author_turn: AuthorTurnRef
    target_snapshot: TargetChapterSnapshot
    canon_version: int
    root_path: str | None = None
    summaries: CalibrationSummarySource | None = None
    events: CalibrationEventSource | None = None
    schema_version: str = CALIBRATION_SCHEMA_VERSION


def _item_id(chapter: int, fact_type: FactType, index: int) -> str:
    """证据 item 的确定性 ID：同一输入两次校准产出同一组 ID（内容寻址幂等）。"""
    return f"{chapter}:{index}:{fact_type.value}"


def _kind_for_source(source: EdgeSource | str | None) -> EpistemicKind:
    if source == EdgeSource.AUTHOR:
        return EpistemicKind.AUTHOR_CANON_FACT
    return EpistemicKind.EXTRACTED_CURRENT


def _resolve_cast(
    store: StoryGraph,
    project_id: str,
    members: Sequence[IntendedCastMember],
) -> tuple[list[NormalizedCastMember], list[DeterministicConflict]]:
    """预计人物解析。查无此人 / 一名多解 / 不是人物 → 确定性冲突。"""
    if not members:
        return [], []
    resolutions = store.resolve(project_id, [m.surface for m in members])
    normalized: list[NormalizedCastMember] = []
    conflicts: list[DeterministicConflict] = []
    seen: set[str] = set()
    for member, resolution in zip(members, resolutions, strict=True):
        node = resolution.unique_node
        if node is None:
            conflicts.append(
                DeterministicConflict(
                    code="UNRESOLVED_NAME",
                    message=(
                        f"「{member.surface}」在花名册里查无此人或一名多解，"
                        "不同选择会改变本章人物任务。"
                    ),
                )
            )
            continue
        if node.label is not NodeLabel.CHARACTER:
            conflicts.append(
                DeterministicConflict(
                    code="UNRESOLVED_NAME",
                    message=f"「{member.surface}」不是人物（它是 {node.label}），不能进预计人物。",
                )
            )
            continue
        if node.id not in seen:
            seen.add(node.id)
            normalized.append(
                NormalizedCastMember(
                    surface=member.surface,
                    node=NodeRef.of(node),
                    basis="AGENT_INFERRED",
                )
            )
    return normalized, conflicts


def _match_state_edge(edges: Sequence[Edge], *, dim_id: str, since_chapter: int) -> Edge | None:
    for edge in edges:
        if (
            edge.type is EdgeType.HAS_STATE
            and edge.dst == dim_id
            and edge.valid_from_chapter == since_chapter
        ):
            return edge
    return None


def _dead_edge(edges: Sequence[Edge]) -> Edge | None:
    for edge in edges:
        if edge.type is EdgeType.HAS_STATE and edge.props.value_key == HealthValue.DEAD:
            return edge
    return None


def _disk_sha256(root_path: str | None, chapter: int) -> str | None:
    if root_path is None:
        return None
    path = Path(root_path) / chapter_path(chapter)
    if not path.exists():
        return None
    return text_digest(read_chapter(Path(root_path), chapter) or "")


def _kind_from_event(view: EventView) -> EpistemicKind:
    if view.event.source is EdgeSource.AUTHOR:
        return EpistemicKind.AUTHOR_CANON_FACT
    return EpistemicKind.EXTRACTED_CURRENT


def calibrate_scene(
    input_: CalibrationInput,
    proposal: SceneProposal,
) -> CalibrationReport:
    """按第 N 章时点校准 Agent 的 `SceneProposal`，产出 Agent-safe 报告。

    Raises:
        ValueError: proposal 的章号与目标快照章号不一致（装配错误，不是模型问题）。
    """
    if proposal.chapter != input_.target_snapshot.chapter:
        raise ValueError(
            f"proposal 章号 {proposal.chapter} 与目标快照章号 "
            f"{input_.target_snapshot.chapter} 不一致"
        )
    store = input_.store
    pid = input_.project_id
    chapter = proposal.chapter

    conflicts: list[DeterministicConflict] = []
    warnings: list[str] = []
    facts: list[EvidenceEnvelope] = []
    unknowns: list[EvidenceEnvelope] = []

    def next_index() -> int:
        """facts 与 unknowns 共用一个单调下标，保证 item_id 全报告唯一。"""
        return len(facts) + len(unknowns)

    # ── 1. 预计人物解析（只用于检索/写作意图，绝不写回 Canon）────────────
    normalized, cast_conflicts = _resolve_cast(store, pid, proposal.intended_cast)
    conflicts.extend(cast_conflicts)

    # ── 2. 每人一条状态/地点/登场/关系快照（来源字段原样保留）──────────────
    for member in normalized:
        try:
            snapshot = store.state_at(pid, member.node.id, chapter, scope=InformationScope.CANON)
        except StoreError as exc:
            conflicts.append(
                DeterministicConflict(
                    code="IMPOSSIBLE_STATE",
                    message=f"{member.surface} 的状态出现机械上不可能同时成立的值：{exc}",
                    chapter=chapter,
                )
            )
            continue

        if snapshot.location is not None:
            edge = next(
                (
                    e
                    for e in snapshot.edges
                    if e.type is EdgeType.LOCATED_AT and e.dst == snapshot.location.id
                ),
                None,
            )
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, FactType.LOCATION, next_index()),
                    kind=_kind_for_source(edge.source if edge else EdgeSource.EXTRACTOR),
                    fact_type=FactType.LOCATION,
                    display_text=render_evidence_text(
                        fact_type=FactType.LOCATION,
                        character=member.node.name,
                        location=snapshot.location.name,
                    ),
                    chapter=chapter,
                    valid_from_chapter=edge.valid_from_chapter if edge else None,
                    valid_to_chapter=edge.valid_to_chapter if edge else None,
                    source=edge.source.value if edge else None,
                    confidence=edge.confidence if edge else None,
                    evidence_id=edge.evidence_id if edge else None,
                    evidence_status=edge.evidence_status.value if edge else None,
                    freshness=Freshness.CURRENT,
                    agent_visibility=agent_visibility_of(FactType.LOCATION),
                    writer_visibility=writer_visibility_of(FactType.LOCATION),
                )
            )

        for state in snapshot.states:
            edge = _match_state_edge(
                snapshot.edges, dim_id=state.dim.id, since_chapter=state.since_chapter
            )
            fact_type = (
                FactType.BODY_LIMITATION if state.dim_key == HEALTH_DIM_KEY else FactType.STATE
            )
            closed = bool(state.value_key)
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, fact_type, next_index()),
                    kind=_kind_for_source(edge.source if edge else EdgeSource.EXTRACTOR),
                    fact_type=fact_type,
                    display_text=render_evidence_text(
                        fact_type=fact_type,
                        character=member.node.name,
                        dimension=state.dim.name,
                        value=state.value_key or state.value or None,
                        since_chapter=state.since_chapter,
                    ),
                    chapter=chapter,
                    valid_from_chapter=state.since_chapter,
                    valid_to_chapter=edge.valid_to_chapter if edge else None,
                    source=edge.source.value if edge else None,
                    confidence=edge.confidence if edge else None,
                    evidence_id=state.evidence_id or (edge.evidence_id if edge else None),
                    evidence_status=edge.evidence_status.value if edge else None,
                    freshness=Freshness.CURRENT,
                    agent_visibility=agent_visibility_of(fact_type),
                    writer_visibility=writer_visibility_of(fact_type, closed_value_key=closed),
                )
            )

        dead = _dead_edge(snapshot.edges)
        if dead is not None:
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, FactType.DEATH, next_index()),
                    kind=_kind_for_source(dead.source),
                    fact_type=FactType.DEATH,
                    display_text=render_evidence_text(
                        fact_type=FactType.DEATH, character=member.node.name, value="dead"
                    ),
                    chapter=chapter,
                    valid_from_chapter=dead.valid_from_chapter,
                    valid_to_chapter=dead.valid_to_chapter,
                    source=dead.source.value,
                    confidence=dead.confidence,
                    evidence_id=dead.evidence_id,
                    evidence_status=dead.evidence_status.value,
                    freshness=Freshness.CURRENT,
                    agent_visibility=agent_visibility_of(FactType.DEATH),
                    writer_visibility=writer_visibility_of(FactType.DEATH),
                )
            )

        facts.append(
            EvidenceEnvelope(
                item_id=_item_id(chapter, FactType.APPEARED, next_index()),
                kind=(
                    EpistemicKind.AUTHOR_CANON_FACT
                    if snapshot.node.props.first_appears_chapter is not None
                    else EpistemicKind.UNKNOWN
                ),
                fact_type=FactType.APPEARED,
                display_text=render_evidence_text(
                    fact_type=FactType.APPEARED,
                    character=member.node.name,
                    value="appeared" if snapshot.has_appeared() else None,
                ),
                chapter=chapter,
                freshness=Freshness.CURRENT,
                agent_visibility=agent_visibility_of(FactType.APPEARED),
                writer_visibility=writer_visibility_of(FactType.APPEARED),
            )
        )

        for edge in snapshot.edges:
            if edge.type is not EdgeType.RELATED_TO:
                continue
            try:
                peer = store.state_at(pid, edge.peer_of(member.node.id), chapter).node
            except StoreError:
                warnings.append(f"{member.surface} 有一条关系指向查不到的人（{edge.id}）。")
                continue
            fact_type = FactType.RELATIONSHIP_STAGE
            value = edge.props.value_key or edge.props.value
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, fact_type, next_index()),
                    kind=_kind_for_source(edge.source),
                    fact_type=fact_type,
                    display_text=render_evidence_text(
                        fact_type=fact_type,
                        character=member.node.name,
                        peer=peer.name,
                        value=value or None,
                    ),
                    chapter=chapter,
                    valid_from_chapter=edge.valid_from_chapter,
                    valid_to_chapter=edge.valid_to_chapter,
                    source=edge.source.value,
                    confidence=edge.confidence,
                    evidence_id=edge.evidence_id,
                    evidence_status=edge.evidence_status.value,
                    freshness=Freshness.CURRENT,
                    agent_visibility=agent_visibility_of(fact_type),
                    writer_visibility=writer_visibility_of(
                        fact_type, closed_value_key=bool(edge.props.value_key)
                    ),
                )
            )

    # ── 4. 已确认事件（只列参与者，不列 knower/revealed 的不对称面）────────
    truncated_events = False
    if input_.events is not None and normalized:
        views = input_.events.events_for_characters(
            pid,
            [m.node.id for m in normalized],
            chapter,
            InformationScope.CANON,
        )
        views = sorted(views, key=lambda v: (v.event.chapter_number, v.event.id))
        if len(views) > EVENT_CAP:
            truncated_events = True
            views = views[:EVENT_CAP]
        cast_ids = {m.node.id for m in normalized}
        for view in views:
            participant_ids = {p.id for p in view.participants}
            knower_ids = {k.id for k in view.knowers}
            public = bool(participant_ids & cast_ids) and cast_ids <= knower_ids
            fact_type = FactType.EVENT
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, fact_type, next_index()),
                    kind=_kind_from_event(view),
                    fact_type=fact_type,
                    display_text=render_evidence_text(
                        fact_type=fact_type,
                        chapter=view.event.chapter_number,
                        summary=view.event.summary,
                        participants=tuple(p.name for p in view.participants),
                    ),
                    chapter=view.event.chapter_number,
                    valid_from_chapter=view.event.chapter_number,
                    source=view.event.source.value,
                    confidence=view.event.confidence,
                    evidence_id=view.event.evidence_id,
                    evidence_status=view.event.evidence_status.value,
                    freshness=Freshness.CURRENT,
                    agent_visibility=agent_visibility_of(fact_type),
                    writer_visibility=writer_visibility_of(fact_type, public_event=public),
                )
            )

    # ── 5. 章节摘要 + 新鲜度（唯一读路径，STALE 不进 Writer）───────────────
    missing_summaries: list[int] = []
    supporting: list[tuple[int, str]] = []
    summary_ids: list[str] = []
    if input_.summaries is not None:
        coverage = input_.summaries.coverage(pid, 1, chapter)
        for status in coverage:
            if status.summary is None:
                if status.has_text and not status.retracted:
                    missing_summaries.append(status.chapter_number)
                continue
            watermark = input_.summaries.snapshot_watermark(pid, status.chapter_number)
            disk_sha = _disk_sha256(input_.root_path, status.chapter_number)
            if disk_sha is not None:
                supporting.append((status.chapter_number, disk_sha))
            freshness = summary_freshness(
                summary_created_at=status.created_at or "",
                watermark_text_sha256=watermark.text_sha256 if watermark else None,
                watermark_snapshot_created_at=watermark.snapshot_created_at if watermark else None,
                disk_sha256=disk_sha,
            )
            summary_ids.append(f"{status.chapter_number}:{sha256(status.summary.encode()).hexdigest()[:16]}")
            if freshness is Freshness.STALE:
                warnings.append(
                    f"第 {status.chapter_number} 章摘要已过期（不对应当前正文），"
                    "不会进 Writer。"
                )
            fact_type = FactType.CHAPTER_SUMMARY
            facts.append(
                EvidenceEnvelope(
                    item_id=_item_id(chapter, fact_type, next_index()),
                    kind=(
                        EpistemicKind.AUTHOR_BACKGROUND
                        if status.author_written
                        else EpistemicKind.MACHINE_SUMMARY
                    ),
                    fact_type=fact_type,
                    display_text=render_evidence_text(
                        fact_type=fact_type,
                        chapter=status.chapter_number,
                        summary=status.summary,
                    ),
                    chapter=status.chapter_number,
                    freshness=freshness,
                    completeness=Completeness.COMPLETE,
                    agent_visibility=agent_visibility_of(fact_type),
                    writer_visibility=writer_visibility_of(
                        fact_type, fresh=freshness is Freshness.CURRENT
                    ),
                )
            )

    # ── 6. 伏笔：生产读口未完成前固定返回能力缺失，不造空实现 ─────────────
    unknowns.append(
        EvidenceEnvelope(
            item_id=_item_id(chapter, FactType.FORESHADOW, next_index()),
            kind=EpistemicKind.UNKNOWN,
            fact_type=FactType.FORESHADOW,
            display_text=render_evidence_text(fact_type=FactType.FORESHADOW),
            chapter=chapter,
            completeness=Completeness.MISSING,
            freshness=Freshness.UNVERIFIED,
            blind_reasons=("伏笔生产读口未完成，当前不能核验伏笔是否已回收。",),
            agent_visibility=AgentVisibility.SAFE_LABEL_ONLY,
            writer_visibility=WriterVisibility.HIDDEN,
        )
    )

    queried = tuple(range(1, max(2, chapter)))
    receipt = CoverageReceipt(
        queried_chapters=queried,
        missing_summaries=tuple(missing_summaries),
        truncated=truncated_events,
        blind_reasons=tuple(
            reason for unknown in unknowns for reason in unknown.blind_reasons
        ),
    )

    inspection_id = new_id(EntityType.CALIBRATION, pid)
    watermark = SourceWatermark(
        schema_version=input_.schema_version,
        author_turn_id=input_.author_turn.turn_id,
        author_request_sha256=input_.author_turn.request_sha256,
        canon_version=input_.canon_version,
        target_sha256=input_.target_snapshot.sha256,
        supporting_chapter_hashes=tuple(supporting),
        summary_ids=tuple(summary_ids),
    )
    return CalibrationReport(
        id=inspection_id,
        project_id=pid,
        chapter=chapter,
        author_turn_id=input_.author_turn.turn_id,
        author_request_sha256=input_.author_turn.request_sha256,
        proposal=proposal,
        source_watermark=watermark,
        sealability=(
            Sealability.NEEDS_AUTHOR if conflicts else Sealability.READY_TO_SEAL
        ),
        normalized_cast=tuple(normalized),
        agent_safe_facts=tuple(facts),
        writer_safe_fact_ids=tuple(
            item.item_id for item in facts if item.writer_visibility is not WriterVisibility.HIDDEN
        ),
        deterministic_conflicts=tuple(conflicts),
        warnings=tuple(warnings),
        unknowns=tuple(unknowns),
        coverage_receipt=receipt,
    )
