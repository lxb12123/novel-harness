"""Transactional ingestion for validated, still-untrusted chapter analysis."""

from __future__ import annotations

from collections.abc import Sequence

from .. import project as project_mod
from ..db import Connection
from ..events import (
    CharacterProfilePatch,
    EventStore,
    ProposalCreate,
    ProposalStore,
    ProvisionalEventSpec,
)
from ..graph import queries
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
    NodeSpec,
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
from .models import RawChapterAnalysis, RawCharacterProfile, RawEvent
from .prompt import ANALYSIS_SCHEMA_VERSION

__all__ = [
    "DiscardOutcome",
    "DiscardReason",
    "ExtractionContextError",
    "ExtractionReport",
    "ExtractionService",
    "profile_information_units",
]

def profile_information_units(profile: RawCharacterProfile) -> int:
    """这一章里模型给这个人写的画像**有多长**（裁定：分数 = 各章信息量的累计）。

    判据是**四个自由文本字段去掉首尾空白之后的字符数之和**，`surface`（名字）和
    `confidence`（数）不算——名字长不代表这个人重要。

    ── ⚠️ 它算的是「模型写了多少」，不是「这个人有多重要」──────────────────

    两者相关但不相等，而**这一批只拿它排序，不拿它做任何判断**（ADR 0020 的第二份
    补记）。真书实测（2026-08-25，3 章）：

        只看第 1 章    探春 16  ←→  袭人 14、麝月 12    分不开，探春会被误判
        叠加之后       探春 42  ←→  袭人 14、麝月 12    差 3 倍，分得清

    **叠加是必需的不是优化**：单章判据一定会误判重要配角。

    ── 一个已知的偏差，照实记 ──────────────────────────────────────────

    没有画像的人这一章记 0，哪怕他在十件事里都在场。**事件不计入分数**——
    裁定的原文是「模型写的画像有多长」，而事件那一侧要不要算、算多少，
    是定阈值那一步要重新回答的问题，不是这一批顺手能定的。
    """
    return sum(
        len(text.strip())
        for text in (
            profile.gender,
            profile.personality,
            profile.background,
            profile.character_notes,
        )
        if text
    )


# ══════════════════════════════════════════════════════════════════════════
# 认不出的人物：**直接建，不问**（2026-08-25，ADR 0020 补记）
# ══════════════════════════════════════════════════════════════════════════
#
# 这儿原来有一个 `new_character_summary()`：给待确认队列上那一行写一句「新人物
# 「贾环」还没登记：荣国府庶子」。它今天没有对象了——不再有 `new_character` 提案。
#
# **它为什么被删而不是留着**：那个函数是 2026-08-15 加的补丁，治的是「22 条提案
# 长得一模一样、作者点不动」。真正的病在上游——**人物认不出来，事件就整条丢**，
# 而作者除了逐条确认 22 个提案之外没有别的办法解开它。真书实测：三章抽出 35 件事，
# 引擎一件没留；22 个提案从 8 月 15 日 PENDING 到 8 月 25 日。
# 治上游之后，那句话没有地方可写，因为没有那一步了。

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
            # ── 认不出就建，不要问（2026-08-25，ADR 0020 补记）────────────────
            created = self._create_unknown_characters(project_id, analysis, resolutions)
            if created:
                resolutions = self._resolutions_after_identity(project_id, analysis)
            created_locations = self._create_unknown_locations(
                project_id, analysis, resolutions
            )
            if created_locations:
                resolutions = self._resolutions_after_identity(project_id, analysis)
            paras = paragraphs(chapter.text)
            discarded: list[DiscardReason] = []
            event_ids: list[str] = []
            edge_ids: list[str] = []
            # `new_character` 那一档 2026-08-25 删了：认不出的人物现在直接建
            # （见 `_create_unknown_characters`），不再攒成提案问作者。
            buckets: dict[str, list[dict[str, object]]] = {
                "edge_conflict": [],
                "low_confidence_main": [],
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
                if (
                    resolution.unique_id is not None
                    and resolution.candidates[0].label is NodeLabel.CHARACTER
                ):
                    # **每一个解析得出的人物都记分，不管他是不是这一次新建的**
                    # （裁定的第一条：已在角色册 → 不问，内容并进去，**分数继续累加**）。
                    queries.record_character_information(
                        self._conn,
                        project_id,
                        resolution.unique_id,
                        chapter.number,
                        profile_information_units(profile),
                    )
                if resolution.unknown:
                    # 上面 `_create_unknown_characters` 已经把每个画像 surface 建成人物了，
                    # 所以走到这儿只可能是**建失败**（名字空白 / 建的时候撞上别的东西）。
                    # 丢弃并记账，不再攒提案。
                    discarded.append(
                        surface_reason(
                            "character_profile",
                            index,
                            profile.surface,
                            NodeLabel.CHARACTER,
                            resolution,
                        )
                    )
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
            }
            for kind in ("edge_conflict", "low_confidence_main"):
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
        # `death` 的对面是引擎自己的 health 维度，不是角色册里的一个称呼——`prepare`
        # 那边留了空，在这儿现取（幂等）。同 `Ledger.declare_dead`：**这个维度由引擎建，
        # 作者和模型都没有入口去建它**（`AUTHORED_LABELS` 里没有 `StateDim`，`dim_key`
        # 是引擎写死的常量）。
        if raw.kind == "death":
            target_id = self._graph.ensure_state_dim(
                project_id, HEALTH_DIM_KEY, HEALTH_DIM_NAME
            ).id
        elif raw.kind == "state":
            # 这里不一样：维度是模型自由写的文本，认不出就建，不配机器键
            # （2026-08-27 裁定）。`upsert_node` 按 (project, label, name) 天然
            # find-or-create，不挂别名——`STATE_DIM` 不在 `CANONICAL_ALIAS_LABELS`
            # 里，进了角色册会让 mentions.py 的 alternation 拿维度名（「情绪」「境界」
            # 这类高频词）去正文里做字面匹配，把 mentions 冲垮。`prepare_state_update`
            # 已经保证 `raw.dimension` 非空。
            target_id = self._graph.upsert_node(
                NodeSpec(
                    project_id=project_id,
                    label=NodeLabel.STATE_DIM,
                    name=(raw.dimension or "").strip(),
                )
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
        # ── 只有「机器要推翻作者亲手改过的那一格」才做提案卡（2026-09-06 裁定）──
        #
        # 从前是「和当前 Canon 不一样就做卡」，于是真书上 100 张卡 / 298 条，
        # 每条都要读两行再判一次——**那个队列在真书上是不可用的**（作者原话：
        # 「这类问题导致的冲突不需要放通知这边，直接更新抽取到那个角色卡的状态中，
        # 然后用户要是觉得不满他自己可以改这个内容的」）。
        #
        # 机器推翻机器 → 直接升 CANON，人物卡上跟着变，**旧值不会消失**（那一格
        # 2026-09-06 起印的是全部历史，`state_history` / `location_history`）。
        # 机器推翻作者 → 才值得问他，用的还是这张卡。
        #
        # 判据是「这条当前边由作者接管着没有」——查一行 `canon_edge_override`，
        # **不是判断两句话意思冲不冲突**（那是语义判断，ADR 0005 的铁律禁的）。
        if current is not None and queries.active_canon_override_for_edge(
            self._conn, project_id, str(current["edge_id"])
        ) is not None:
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

    def _create_unknown_characters(
        self,
        project_id: str,
        analysis: RawChapterAnalysis,
        resolutions: dict[str, SurfaceResolution],
    ) -> list[str]:
        """**认不出的人物，直接建。**（2026-08-25 裁定，[ADR 0020](../../../docs/adr/0020-extraction-lands-canon-directly.md) 补记）

        ── 这一步在治什么 ────────────────────────────────────────────────

        从前：认不出的人物 → 攒进 `new_character` 提案等作者确认；而一条事件只要
        **一个参与者都解析不出来就整条丢**（`_ingest_event` 那句 `if not participants`）。
        两条规矩在空角色册上互锁：**没人 ⇒ 事件全丢 ⇒ 角色册还是没人**。

        真书实测（`book.db`，158 章）：三章抽取，模型抽出 35 件事，**引擎一件没留**，
        同时提了 22 个新人物提案，从 2026-08-15 PENDING 到 2026-08-25 一条没被确认。

        ── 只建**人物位**上的称呼，不是所有认不出的字 ────────────────────

        `resolution_map` 收的 surface 来自四处：事件的 participants / knowers、
        state_update 的 subject / object / dimension、画像的 surface。
        **只有第一组和最后一组是人物位。** `state.object` 可能是地点、`state.dimension`
        是状态维度（「健康」）——把它们也建成人物，角色册里会长出「健康」这个角色。

        ── 不拿 `usable_for_rules=False` 当「未确认」的替身 ──────────────

        建出来的人物和作者亲手建的**一模一样**（canonical 别名照 `upsert_node` 的规矩
        走，`usable_for_rules = len(name) >= 2`）。拿那个字段当「这个是机器猜的」的标记，
        等于在 ADR 0004 定死的一个语义上加载第二种意思，而下一个人只会读到第一种。

        ── ⚠️ 它会认错，而认错的代价必须有出口 ──────────────────────────

        真书那 22 个名字里「**袭人**」是真会误命中的：这本书里满篇「寒气袭人」「香气袭人」。
        今天它伤不到规则（R3 只看对白标签位；R2 要首现章而那个字段没有浏览器入口），
        **它伤的是「这一章提到了谁」和喂给模型的上下文**——袭人的档案会被塞进一场
        她不在的戏。所以**角色册的删除入口是这一条的配套，不是可选项**
        （`DELETE …/nodes/{id}`，同一批改动）。自动建 + 不能删 = 单向阀。

        Returns:
            这一次真建出来的人物 node id。非空时调用方**必须**重建一次 resolution_map。
        """
        wanted: list[str] = []
        for event in analysis.events:
            wanted.extend(event.participants)
            wanted.extend(event.knowers)
        wanted.extend(profile.surface for profile in analysis.character_profiles)

        # ⚠️ **模型自己说是别名的那些 surface，一个都不许建。**
        #
        # `analysis.aliases` 里的每一条都是「这是**某个已有人物**的另一种叫法」
        # （「凤辣子」→ 王熙凤）。`resolve_analysis_identity` 已经处理过它们：
        # 够格的落成别名，不够格（置信度低 / 引语对不上）的留着等作者裁。
        # **不够格 ≠ 这是个新人**——把它建成人物，同一个王熙凤在角色册里就有两个身子，
        # 而那正是 `aliases.py` 那一整套机制存在的理由。
        proposed_aliases = {alias.surface for alias in analysis.aliases}

        profiles = {p.surface: p for p in analysis.character_profiles}
        created: list[str] = []
        seen: set[str] = set()
        for surface in wanted:
            name = surface.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            resolution = resolutions.get(surface)
            # **只建 unknown 的**：歧义（`ambiguous`）照旧整条拒收——那是两个**已经存在**
            # 的人，建第三个只会让歧义更重（ADR 0004：产品从不替作者挑）。
            if resolution is None or not resolution.unknown:
                continue
            if name in proposed_aliases or surface in proposed_aliases:
                continue
            node = self._graph.upsert_node(
                NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name=name)
            )
            profile = profiles.get(surface)
            if profile is not None:
                # 画像跟着一起落：它本来就是这一次抽取的产物，攒着不写等于让作者
                # 在下一章再被问一次同一个人。
                self._events.update_profile(
                    project_id,
                    node.id,
                    CharacterProfilePatch(
                        gender=profile.gender,
                        personality=profile.personality,
                        background=profile.background,
                        character_notes=profile.character_notes,
                    ),
                )
            created.append(node.id)
        return created

    def _create_unknown_locations(
        self,
        project_id: str,
        analysis: RawChapterAnalysis,
        resolutions: dict[str, SurfaceResolution],
    ) -> list[str]:
        """**认不出的地点，直接建。** `_create_unknown_characters` 的姊妹条
        （2026-08-27 裁定，同一批：状态维度那边的「认不出就建」落地时一并做的）。

        只建 `state_update` 里 `kind == "location"` 的 `object`——不是所有认不出的字。
        `Location` 本来就在 `CANONICAL_ALIAS_LABELS` 里，`upsert_node` 建的时候自动挂
        canonical 别名，所以走完这一步、重建一次 resolution_map，`resolve_ids` 那条老路
        天然就通了（同人物那条，不需要另开一条解析路径——这点和维度不一样）。

        地点没有「别名」概念（`analysis.aliases` 是 Task 12 identity-first 给人物用的
        称呼对应），所以不需要 `_create_unknown_characters` 那份别名排除逻辑；歧义
        （两个已存在的地点撞同一个称呼）仍旧整条拒收，产品从不替作者挑（ADR 0004）。

        Returns:
            这一次真建出来的地点 node id。非空时调用方**必须**重建一次 resolution_map。
        """
        wanted = [
            state.object
            for state in analysis.state_updates
            if state.kind == "location" and state.object is not None
        ]
        created: list[str] = []
        seen: set[str] = set()
        for surface in wanted:
            name = surface.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            resolution = resolutions.get(surface)
            if resolution is None or not resolution.unknown:
                continue
            node = self._graph.upsert_node(
                NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name=name)
            )
            created.append(node.id)
        return created

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


