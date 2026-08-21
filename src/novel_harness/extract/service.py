"""Transactional ingestion for validated, still-untrusted chapter analysis."""

from __future__ import annotations

from collections.abc import Sequence

from .. import project as project_mod
from ..db import Connection
from ..events import EventStore, ProposalCreate, ProposalStore, ProvisionalEventSpec
from ..graph import (
    DEAD_VALUE_TEXT,
    HEALTH_DIM_KEY,
    HEALTH_DIM_NAME,
    ChapterText,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EvidenceSpec,
    GraphStore,
    HealthValue,
    InformationScope,
    NodeLabel,
)
from ..text.anchor import Located, paragraphs
from .analyze import SurfaceResolution
from .ingest_helpers import (
    EDGE_TYPE_BY_KIND,
    DiscardOutcome,
    DiscardReason,
    ExtractionContextError,
    ExtractionReport,
    PreparedStateUpdate,
    find_conflict,
    keep_last_state_updates,
    locate_evidence,
    prepare_state_update,
    resolution_map,
    resolve_event_surfaces,
    surface_reason,
)
from .models import RawChapterAnalysis, RawEvent
from .prompt import ANALYSIS_SCHEMA_VERSION

__all__ = [
    "DiscardOutcome",
    "DiscardReason",
    "ExtractionContextError",
    "ExtractionReport",
    "ExtractionService",
]


class ExtractionService:
    """Turn pure analysis into evidence-backed provisional memory on one connection."""

    def __init__(
        self,
        *,
        conn: Connection,
        graph: GraphStore,
        event_store: EventStore,
        proposal_store: ProposalStore,
    ) -> None:
        self._conn = conn
        self._graph = graph
        self._events = event_store
        self._proposals = proposal_store
        self._profile_main: dict[str, bool] = {}

    def ingest(
        self,
        project_id: str,
        chapter: ChapterText,
        analysis: RawChapterAnalysis,
        *,
        prompt_hash: str,
    ) -> ExtractionReport:
        if not prompt_hash:
            raise ValueError("prompt_hash must not be empty")
        # 服务可复用；跨 run 的档案审阅必须立即可见。
        self._profile_main.clear()
        with self._graph.transaction():
            canon_version = self._validate_context(project_id, chapter)
            # ── identity-first（Task 12）：先落地模型提议的称呼，再解析事件 ──
            # 不先做这一下，「凤辣子」会被 resolve 成 unknown → 整条事件被丢 / 误进
            # new_character bucket，同一个已出场的人物被登记两遍。
            from .aliases import resolve_analysis_identity

            generation_row = self._conn.execute(
                "SELECT snapshot_generation FROM chapter "
                "WHERE project_id = ? AND id = ?",
                (project_id, chapter.chapter_id),
            ).fetchone()
            source_generation = (
                int(generation_row["snapshot_generation"])
                if generation_row is not None
                else None
            )
            identity = resolve_analysis_identity(
                self._conn,
                self._graph,
                project_id,
                analysis,
                chapter_id=chapter.chapter_id,
                chapter_snapshot_id=chapter.snapshot_id,
                source_generation=source_generation,
                extraction_application_id=None,
            )
            if identity.ambiguous_candidates:
                # 歧义称呼 → 进入 alias_resolution 聚类提案（§4.5），不替作者挑。
                self._propose_identity_ambiguities(
                    project_id, chapter, identity.ambiguous_candidates
                )
            # 重建 resolution_map：自动 alias 已落库，新称呼现在能解析回人了。
            resolutions = self._resolutions_after_identity(project_id, analysis)
            paras = paragraphs(chapter.text)
            discarded: list[DiscardReason] = []
            event_ids: list[str] = []
            edge_ids: list[str] = []
            buckets: dict[str, list[dict[str, object]]] = {
                "edge_conflict": [],
                "low_confidence_main": [],
                "new_character": [],
            }
            event_links: dict[str, list[str]] = {kind: [] for kind in buckets}
            edge_links: dict[str, list[str]] = {kind: [] for kind in buckets}
            confidences: dict[str, list[float]] = {kind: [] for kind in buckets}

            for index, raw in enumerate(analysis.events):
                reason = self._ingest_event(
                    project_id,
                    chapter,
                    paras,
                    resolutions,
                    raw,
                    index,
                    event_ids,
                    buckets,
                    event_links,
                    confidences,
                )
                if reason is not None:
                    discarded.append(reason)

            prepared_states: list[PreparedStateUpdate] = []
            state_discards: list[DiscardReason] = []
            for index, raw in enumerate(analysis.state_updates):
                prepared, reason = prepare_state_update(
                    index, raw, paras, resolutions
                )
                if reason is not None:
                    state_discards.append(reason)
                elif prepared is not None:
                    prepared_states.append(prepared)
            retained_states, superseded = keep_last_state_updates(prepared_states)
            discarded.extend(sorted((*state_discards, *superseded), key=lambda item: item.index))
            for prepared in retained_states:
                self._write_state(
                    project_id, chapter, prepared, edge_ids,
                    buckets, edge_links, confidences,
                )

            for index, profile in enumerate(analysis.character_profiles):
                resolution = resolutions[profile.surface]
                if resolution.unknown:
                    buckets["new_character"].append(
                        {
                            "surface": profile.surface,
                            "profile": profile.model_dump(mode="json"),
                            "confidence": profile.confidence,
                        }
                    )
                    confidences["new_character"].append(profile.confidence)
                elif resolution.ambiguous:
                    discarded.append(
                        surface_reason(
                            "character_profile",
                            index,
                            profile.surface,
                            NodeLabel.CHARACTER,
                            resolution,
                        )
                    )
                elif resolution.candidates[0].label is not NodeLabel.CHARACTER:
                    discarded.append(
                        surface_reason(
                            "character_profile",
                            index,
                            profile.surface,
                            NodeLabel.CHARACTER,
                            resolution,
                        )
                    )
                # 已知档案保持只读，直到作者显式审阅。

            proposal_ids: list[str] = []
            summaries = {
                "edge_conflict": "抽取状态与当前 Canon 冲突。",
                "low_confidence_main": "主要人物相关抽取置信度低于 0.70。",
                "new_character": "抽取发现尚未登记的人物画像。",
            }
            for kind in ("edge_conflict", "low_confidence_main", "new_character"):
                items = buckets[kind]
                if not items:
                    continue
                if kind == "new_character":
                    # 每个未知人物独立成一条提案：作者才能逐人「接受为角色 / 标为路人」，
                    # 而不是整组一起处理。
                    for item in items:
                        proposal = self._proposals.create(
                            ProposalCreate(
                                project_id=project_id,
                                kind=kind,
                                summary=summaries[kind],
                                items=[item],
                                confidence=item["confidence"],
                                chapter_number=chapter.number,
                                snapshot_id=chapter.snapshot_id,
                                base_canon_version=canon_version,
                                schema_version=ANALYSIS_SCHEMA_VERSION,
                                prompt_hash=prompt_hash,
                            )
                        )
                        proposal_ids.append(proposal.id)
                    continue
                proposal = self._proposals.create(
                    ProposalCreate(
                        project_id=project_id,
                        kind=kind,
                        summary=summaries[kind],
                        items=items,
                        confidence=min(confidences[kind]) if confidences[kind] else None,
                        chapter_number=chapter.number,
                        snapshot_id=chapter.snapshot_id,
                        base_canon_version=canon_version,
                        schema_version=ANALYSIS_SCHEMA_VERSION,
                        prompt_hash=prompt_hash,
                        event_ids=sorted(set(event_links[kind])),
                        edge_ids=sorted(set(edge_links[kind])),
                    )
                )
                proposal_ids.append(proposal.id)

            # 干净集合 = 这次落库的 − 进了任何一个例外 bucket 的。**只能这么减**：
            # 反过来（「置信度够高就算干净」）会在下一个 bucket 加进来的那天静默漏掉它。
            bucketed_events = {item for ids in event_links.values() for item in ids}
            bucketed_edges = {item for ids in edge_links.values() for item in ids}
            discarded_events = sum(reason.kind == "event" for reason in discarded)
            return ExtractionReport(
                valid_event_count=len(event_ids),
                discarded_event_count=discarded_events,
                valid_state_update_count=len(retained_states),
                discarded=tuple(discarded),
                event_ids=tuple(event_ids),
                edge_ids=tuple(edge_ids),
                proposal_ids=tuple(proposal_ids),
                proposal_count=len(proposal_ids),
                # `dict.fromkeys` 去重：两条 raw 落在同一条引语上时会拿回同一个 id，
                # 而 `confirm_provisional_*` 对重复 id 是整批拒收——那会让一次本可以
                # 部分成功的自动升变成什么都不升。
                clean_event_ids=tuple(
                    dict.fromkeys(
                        item for item in event_ids if item not in bucketed_events
                    )
                ),
                clean_edge_ids=tuple(
                    dict.fromkeys(
                        item for item in edge_ids if item not in bucketed_edges
                    )
                ),
            )

    def _validate_context(self, project_id: str, chapter: ChapterText) -> int:
        row = self._conn.execute(
            """
            SELECT chapter.id AS chapter_id, chapter.number, snapshot.text,
                   chapter.snapshot_generation, project.canon_version
            FROM chapter_snapshot AS snapshot
            JOIN chapter AS chapter ON chapter.id = snapshot.chapter_id
            JOIN project AS project ON project.id = chapter.project_id
            WHERE snapshot.id = ? AND chapter.project_id = ?
            """,
            (chapter.snapshot_id, project_id),
        ).fetchone()
        if (
            row is None
            or str(row["chapter_id"]) != chapter.chapter_id
            or int(row["number"]) != chapter.number
            or str(row["text"]) != chapter.text
        ):
            raise ExtractionContextError(
                "chapter must exactly match its immutable project snapshot"
            )
        return int(row["canon_version"])

    def _ingest_event(
        self,
        project_id: str,
        chapter: ChapterText,
        paras: Sequence[str],
        resolutions: dict[str, SurfaceResolution],
        raw: RawEvent,
        index: int,
        event_ids: list[str],
        buckets: dict[str, list[dict[str, object]]],
        event_links: dict[str, list[str]],
        confidences: dict[str, list[float]],
    ) -> DiscardReason | None:
        participants, _dropped_participants, reason = resolve_event_surfaces(
            "event", index, raw.participants, NodeLabel.CHARACTER, resolutions
        )
        if reason is not None:
            return reason
        if not participants:
            return DiscardReason(
                kind="event",
                index=index,
                outcome=DiscardOutcome.UNKNOWN_SURFACE,
                detail="event has no resolvable participants",
            )
        knowers, _dropped_knowers, reason = resolve_event_surfaces(
            "event", index, raw.knowers, NodeLabel.CHARACTER, resolutions
        )
        if reason is not None:
            return reason
        facts, _dropped_facts, reason = resolve_event_surfaces(
            "event", index, raw.revealed_facts, NodeLabel.SECRET, resolutions
        )
        if reason is not None:
            return reason
        located, reason = locate_evidence("event", index, paras, raw.quote)
        if reason is not None:
            return reason
        evidence = self._put_evidence(project_id, chapter, located)
        view = self._events.put_provisional(
            ProvisionalEventSpec(
                project_id=project_id,
                summary=raw.summary,
                evidence_id=evidence.id,
                participant_ids=participants,
                knower_ids=knowers,
                revealed_fact_ids=facts,
                confidence=raw.confidence,
            )
        )
        # Task 7：新事件的机器摘要基线版本（绑定 evidence 快照，不调模型）。
        from ..events.summaries import create_event_summary_baseline

        create_event_summary_baseline(
            self._conn,
            project_id=project_id,
            event_id=view.event.id,
            summary=raw.summary,
            source_snapshot_id=evidence.audit.chapter_snapshot_id,
            evidence_sha256=evidence.audit.quote_sha256,
        )
        event_ids.append(view.event.id)
        if raw.confidence < 0.70 and self._any_main(
            project_id, (*participants, *knowers)
        ):
            buckets["low_confidence_main"].append(
                {
                    "source_kind": "event",
                    "event_id": view.event.id,
                    "summary": raw.summary,
                    "confidence": raw.confidence,
                    "quote": located.matched_text,
                }
            )
            event_links["low_confidence_main"].append(view.event.id)
            confidences["low_confidence_main"].append(raw.confidence)
        return None

    def _write_state(
        self,
        project_id: str,
        chapter: ChapterText,
        prepared: PreparedStateUpdate,
        edge_ids: list[str],
        buckets: dict[str, list[dict[str, object]]],
        edge_links: dict[str, list[str]],
        confidences: dict[str, list[float]],
    ) -> None:
        raw = prepared.raw
        subject_id, target_id = prepared.subject_id, prepared.target_id
        # `death` 的对面是引擎自己的 health 维度，不是花名册里的一个称呼——`prepare`
        # 那边留了空，在这儿现取（幂等）。同 `Ledger.declare_dead`：**维度由引擎建，
        # 作者和模型都没有入口去建它**（`AUTHORED_LABELS` 里没有 `StateDim`）。
        if raw.kind == "death":
            target_id = self._graph.ensure_state_dim(
                project_id, HEALTH_DIM_KEY, HEALTH_DIM_NAME
            ).id
        located = prepared.located
        canon = self._graph.state_at(
            project_id, subject_id, chapter.number, scope=InformationScope.CANON
        )
        current = find_conflict(raw, subject_id, target_id, canon)
        evidence = self._put_evidence(project_id, chapter, located)
        # **`value_key` 由引擎写死，不从模型那段文字里认**——R3 的判据是这个键，
        # 而「死 / 陨落 / 坐化 / 兵解」怎么写都不该影响它（ADR 0005 的铁律，
        # `declare.py::declare_dead` 有完整论证）。`value` 那段中文只给人看。
        props = (
            EdgeProps(value=DEAD_VALUE_TEXT, value_key=HealthValue.DEAD)
            if raw.kind == "death"
            else EdgeProps(value=raw.value)
        )
        result = self._graph.upsert_edge(
            EdgeSpec(
                project_id=project_id,
                src=subject_id,
                dst=target_id,
                type=EDGE_TYPE_BY_KIND[raw.kind],
                props=props,
                valid_from_chapter=chapter.number,
                information_scope=InformationScope.PROVISIONAL,
                confidence=raw.confidence,
                source=EdgeSource.EXTRACTOR,
                evidence_id=evidence.id,
            )
        )
        edge_ids.append(result.edge.id)
        proposed = {
            "edge_id": result.edge.id,
            "subject_id": subject_id,
            "target_id": target_id,
            "value": raw.value,
            "quote": located.matched_text,
        }
        if current is not None:
            buckets["edge_conflict"].append(
                {
                    "update_kind": raw.kind,
                    "current": current,
                    "proposed": proposed,
                }
            )
            edge_links["edge_conflict"].append(result.edge.id)
            confidences["edge_conflict"].append(raw.confidence)
        main_candidates = (
            (subject_id, target_id)
            if raw.kind == "relationship"
            else (subject_id,)
        )
        if raw.confidence < 0.70 and self._any_main(project_id, main_candidates):
            buckets["low_confidence_main"].append(
                {
                    "source_kind": "state_update",
                    "update_kind": raw.kind,
                    "confidence": raw.confidence,
                    "proposed": proposed,
                }
            )
            edge_links["low_confidence_main"].append(result.edge.id)
            confidences["low_confidence_main"].append(raw.confidence)

    def _put_evidence(
        self, project_id: str, chapter: ChapterText, located: Located
    ):
        return self._graph.put_evidence(
            EvidenceSpec(
                project_id=project_id,
                chapter_snapshot_id=chapter.snapshot_id,
                para_index=located.para_index,
                occurrence_k=located.occurrence_k,
                quote_text=located.matched_text,
            )
        )

    def _any_main(self, project_id: str, character_ids: Sequence[str]) -> bool:
        for character_id in dict.fromkeys(character_ids):
            if character_id not in self._profile_main:
                profile = self._events.profile(project_id, character_id)
                self._profile_main[character_id] = profile.main_character is True
            if self._profile_main[character_id]:
                return True
        return False

    def _resolutions_after_identity(
        self, project_id: str, analysis: RawChapterAnalysis
    ) -> dict[str, SurfaceResolution]:
        """identity 落地后重建 resolution_map：新 alias 现在能解析回人了。"""
        return resolution_map(self._graph, project_id, analysis)

    def _propose_identity_ambiguities(
        self,
        project_id: str,
        chapter: ChapterText,
        ambiguous: Sequence[tuple[str, Sequence[object]]],
    ) -> None:
        """歧义称呼 → `alias_resolution` 聚类提案（§4.5），不替作者挑。

        提案保留受影响 raw surface——作者确认归属后走确定性重放，不能重新付费
        调模型（Task 12 / §4.5）。
        """
        for surface, people in ambiguous:
            item = {
                "surface": surface,
                "candidates": [
                    {
                        "id": getattr(p, "id", None),
                        "name": getattr(p, "name", None),
                        "label": getattr(p, "label", None),
                    }
                    for p in people
                ],
            }
            self._proposals.create(
                ProposalCreate(
                    project_id=project_id,
                    kind="alias_resolution",
                    summary=f"「{surface}」可能指这几个人，系统不替你挑。",
                    items=[item],
                    chapter_number=chapter.number,
                    snapshot_id=chapter.snapshot_id,
                    base_canon_version=project_mod.require_canon_version(
                        self._conn, project_id
                    ),
                    schema_version=ANALYSIS_SCHEMA_VERSION,
                    prompt_hash="identity-first",
                )
            )


