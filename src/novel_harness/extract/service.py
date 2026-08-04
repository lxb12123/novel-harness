"""Transactional ingestion for validated, still-untrusted chapter analysis."""

from __future__ import annotations

from collections.abc import Sequence

from ..db import Connection
from ..events import EventStore, ProposalCreate, ProposalStore, ProvisionalEventSpec
from ..graph import (
    ChapterText,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    GraphStore,
    InformationScope,
    NodeLabel,
)
from ..text.anchor import Located, paragraphs
from .analyze import SurfaceResolution
from .ingest_helpers import (
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
        # The service may be reused; profile review between runs must be visible.
        self._profile_main.clear()
        with self._graph.transaction():
            canon_version = self._validate_context(project_id, chapter)
            resolutions = resolution_map(self._graph, project_id, analysis)
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
                # Known profiles remain read-only until explicit author review.

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
            )

    def _validate_context(self, project_id: str, chapter: ChapterText) -> int:
        row = self._conn.execute(
            """
            SELECT chapter.id AS chapter_id, chapter.number, snapshot.text,
                   project.canon_version
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
        located = prepared.located
        canon = self._graph.state_at(
            project_id, subject_id, chapter.number, scope=InformationScope.CANON
        )
        current = find_conflict(raw, subject_id, target_id, canon)
        evidence = self._put_evidence(project_id, chapter, located)
        edge_type = {
            "location": EdgeType.LOCATED_AT,
            "state": EdgeType.HAS_STATE,
            "relationship": EdgeType.RELATED_TO,
        }[raw.kind]
        result = self._graph.upsert_edge(
            EdgeSpec(
                project_id=project_id,
                src=subject_id,
                dst=target_id,
                type=edge_type,
                props=EdgeProps(value=raw.value),
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
