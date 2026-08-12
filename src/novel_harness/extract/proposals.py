"""Atomic M4 author review, passive confirmation, and post-commit audit recovery."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import Iterator

from .. import project
from ..db import Connection
from ..events import (
    EventCastStore,
    EventStore,
    EventStoreError,
    EventView,
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalRecord,
    ProposalResolutionMark,
    ProposalStore,
)
from ..graph import (
    EdgeStatus,
    Evidence,
    EvidenceStatus,
    GraphStore,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
)
from ..graph.review_store import (
    EdgeReviewStore,
    EdgeReviewValidationError,
    ReviewableEdge,
)
from ..graph.sqlite_proposals import SqliteProposalStore
from ..graph.sqlite_review import SqliteEdgeReviewStore
from .proposal_audit import build_audit_envelope, ensure_proposal_audit
from .proposal_models import (
    ConfirmationConflict,
    DecisionAuditError,
    EdgeConflictItem,
    NewCharacterItem,
    ProposalAction,
    ProposalActionError,
    ProposalResolution,
    ProposalReview,
    ProposalReviewError,
    ProposalShapeError,
    ProvisionalConfirmation,
    ValidatedProposal,
)
from .proposal_validation import (
    referenced_node_ids,
    validate_current_canon_facts,
    validate_hydrated_facts,
    validate_proposal_shape,
)

__all__ = [
    "ConfirmationConflict",
    "DecisionAuditError",
    "ProposalAction",
    "ProposalActionError",
    "ProposalResolution",
    "ProposalReview",
    "ProposalReviewError",
    "ProposalShapeError",
    "ProvisionalConfirmation",
    "confirm_provisional_edge",
    "confirm_provisional_edges",
    "confirm_provisional_event",
    "confirm_provisional_events",
    "hydrate_proposal_names",
    "recover_proposal_audit",
    "review_proposal",
]


def hydrate_proposal_names(
    review_store: EdgeReviewStore,
    project_id: str,
    proposals: Sequence[ProposalRecord],
) -> tuple[ProposalRecord, ...]:
    """给每条提案补上 `node_refs`：`items` 里的裸 id → 显示名。

    ── 为什么这件事必须在后端做 ─────────────────────────────────────────
    在这个函数存在之前，界面拿 `subject_id` / `target_id` 去**花名册**里查名字，
    查不到就把 id 截断了摆上屏（`n:ID22`）。**缝在两次查询的时间差**：花名册和提案
    队列在浏览器里是两条独立缓存，而后台抽取 / 自动升 CANON 会造出新节点——没有任何
    一条路径保证花名册那份在提案那份之后重取过。（花名册本身不挑 label：它走
    `resolve(pid, None)`，而每个节点建出来就带一条 canonical 别名。）

    出参自足 ⇒ 这一整类失败在结构上不存在，也不必再给一条「什么时候该重取花名册」的纪律。

    ── 认不出的 id 不进这份名单 ──────────────────────────────────────────
    `node_refs` 对不存在 / 跨项目的 id 会抛（写路径要的就是这个）。这里是**读端**：
    一条引用了幽灵 id 的提案不该把整页队列一起打不开，所以整批失败时逐个再试一遍，
    认不出的那个**直接不出现**——绝不编一个名字出来（§10 约束 8：静默的假名比空更贵）。
    """
    ids = tuple(
        dict.fromkeys(
            node_id
            for proposal in proposals
            for node_id in referenced_node_ids(proposal.items)
        )
    )
    if not ids:
        return tuple(proposals)
    by_id = {ref.id: ref for ref in _known_node_refs(review_store, project_id, ids)}
    return tuple(
        proposal.model_copy(
            update={
                "node_refs": tuple(
                    by_id[node_id]
                    for node_id in referenced_node_ids(proposal.items)
                    if node_id in by_id
                )
            }
        )
        for proposal in proposals
    )


def _known_node_refs(
    review_store: EdgeReviewStore,
    project_id: str,
    ids: Sequence[str],
) -> tuple[NodeRef, ...]:
    """认得出的那些。一次批量，失败了才逐个——**只有坏数据会走第二条路**。"""
    try:
        return review_store.node_refs(project_id, ids)
    except EdgeReviewValidationError:
        out: list[NodeRef] = []
        for node_id in ids:
            try:
                out.extend(review_store.node_refs(project_id, [node_id]))
            except EdgeReviewValidationError:
                continue
        return tuple(out)


@contextmanager
def _business_transaction(conn: Connection) -> Iterator[None]:
    if conn.in_transaction:
        raise RuntimeError("author review 必须在没有外层事务的连接上启动")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _hydrate_events(
    event_store: EventStore,
    evidence_store: EdgeReviewStore,
    project_id: str,
    event_ids: Sequence[str],
) -> tuple[tuple[EventView, ...], dict[str, Evidence]]:
    views: list[EventView] = []
    evidence: dict[str, Evidence] = {}
    for event_id in event_ids:
        view = event_store.event(project_id, event_id)
        if view is None:
            raise ProposalShapeError(
                f"event {event_id} 不存在、跨项目或当前不可审阅"
            )
        if (
            view.event.information_scope is not InformationScope.PROVISIONAL
            or view.event.status is not EdgeStatus.ACTIVE
            or view.event.evidence_status is not EvidenceStatus.FRESH
        ):
            raise ProposalShapeError(f"event {event_id} 必须是 ACTIVE/FRESH PROVISIONAL")
        views.append(view)
        evidence[event_id] = evidence_store.evidence(project_id, view.event.evidence_id)
    return tuple(views), evidence


def _hydrate_cluster(
    proposal: ProposalRecord,
    review: ProposalReview,
    event_store: EventStore,
    edge_store: EdgeReviewStore,
) -> tuple[
    ValidatedProposal,
    tuple[EventView, ...],
    dict[str, Evidence],
    tuple[ReviewableEdge, ...],
    dict[str, Evidence],
]:
    validated = validate_proposal_shape(proposal, review)
    events, event_evidence = _hydrate_events(
        event_store, edge_store, proposal.project_id, proposal.event_ids
    )
    try:
        edges = (
            edge_store.hydrate_provisional(proposal.project_id, proposal.edge_ids)
            if proposal.edge_ids
            else ()
        )
    except EdgeReviewValidationError as exc:
        raise ProposalShapeError(str(exc)) from exc
    edge_evidence = {
        item.edge.id: edge_store.evidence(
            proposal.project_id, item.edge.evidence_id or ""
        )
        for item in edges
    }
    validate_hydrated_facts(
        validated,
        events,
        event_evidence,
        edges,
        edge_evidence,
    )
    return validated, events, event_evidence, edges, edge_evidence


def _validate_current_canon(
    proposal: ProposalRecord,
    validated: ValidatedProposal,
    proposed_edges: Sequence[ReviewableEdge],
    edge_store: EdgeReviewStore,
) -> None:
    current_ids = tuple(
        item.current.edge_id
        for item in validated.edge_items
        if isinstance(item, EdgeConflictItem)
    )
    if not current_ids:
        return
    try:
        current_edges = edge_store.hydrate_current_canon(
            proposal.project_id,
            current_ids,
        )
    except EdgeReviewValidationError as exc:
        raise ProposalShapeError(f"current Canon 复核失败：{exc}") from exc
    validate_current_canon_facts(validated, proposed_edges, current_edges)


def _create_characters(
    graph: GraphStore,
    project_id: str,
    candidates: Sequence[NewCharacterItem],
) -> tuple[NodeRef, ...]:
    surfaces = [candidate.surface for candidate in candidates]
    if len(surfaces) != len(set(surfaces)):
        raise ProposalShapeError("new_character cluster 里 surface 不能重复")
    resolutions = graph.resolve(project_id, surfaces)
    if len(resolutions) != len(surfaces):
        raise ProposalShapeError("resolve 没有与 new_character surfaces 一一对应")
    if any(resolution.unique_node is not None or resolution.ambiguous for resolution in resolutions):
        raise ProposalShapeError("new_character 在锁内必须仍然 unknown，不允许合并或猜测")
    created: list[NodeRef] = []
    for candidate in candidates:
        profile = candidate.profile
        node = graph.upsert_node(
            NodeSpec(
                project_id=project_id,
                label=NodeLabel.CHARACTER,
                name=candidate.surface,
                props=NodeProps(
                    gender=profile.gender,
                    personality=profile.personality,
                    background=profile.background,
                    character_notes=profile.character_notes,
                ),
            )
        )
        created.append(NodeRef.of(node))
    return tuple(created)


def _clone_events(
    event_store: EventStore,
    event_ids: Sequence[str],
    *,
    edited_summary: str | None = None,
) -> tuple[EventView, ...]:
    out: list[EventView] = []
    try:
        for event_id in event_ids:
            out.append(
                event_store.clone_to_scope(
                    event_id,
                    InformationScope.CANON,
                    summary=edited_summary,
                )
            )
    except EventStoreError as exc:
        raise ProposalShapeError(str(exc)) from exc
    return tuple(out)


def _edit_cast(
    event_store: EventStore,
    project_id: str,
    events: tuple[EventView, ...],
    review: ProposalReview,
) -> tuple[EventView, ...]:
    """把 `edit` 里的名单改动落到**刚克隆出来的那条 CANON 事件**上。

    顺序是「先克隆再改」而不是「改完再克隆」：源事件是 PROVISIONAL，它不该被作者的
    审阅动作改写（`test_edit_is_event_only...` 断言的就是源事件一个字节没动）。
    """
    if review.edited_knower_ids is None and review.edited_participant_ids is None:
        return events
    if not isinstance(event_store, EventCastStore):
        raise ProposalActionError("这个事件仓储改不了名单：没有 edit_cast")
    (view,) = events  # validate_proposal_shape 已保证 edit 恰好 1 个 event
    try:
        edited = event_store.edit_cast(
            project_id,
            view.event.id,
            knower_ids=review.edited_knower_ids,
            participant_ids=review.edited_participant_ids,
        )
    except EventStoreError as exc:
        raise ProposalShapeError(str(exc)) from exc
    return (edited.event,)


def _audit_pairs(
    facts: Sequence,
    source_ids: Sequence[str],
    evidence_by_source: dict[str, Evidence],
):
    return tuple(
        (fact, evidence_by_source[source_id])
        for fact, source_id in zip(facts, source_ids, strict=True)
    )


def _resolution(
    proposal: ProposalRecord,
    *,
    status: str,
    canon_version: int,
    decision_id: str,
    events: tuple[EventView, ...],
    edges: tuple[ReviewableEdge, ...],
    characters: tuple[NodeRef, ...],
) -> ProposalResolution:
    return ProposalResolution(
        proposal_id=proposal.id,
        status=status,
        canon_version=canon_version,
        decision_id=decision_id,
        event=events[0] if len(events) == 1 else None,
        character=characters[0] if len(characters) == 1 else None,
        events=events,
        edges=edges,
        characters=characters,
    )


def review_proposal(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    review: ProposalReview,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProposalResolution:
    proposals = proposal_store or SqliteProposalStore(conn)
    edge_reviews = edge_review_store or SqliteEdgeReviewStore(conn, graph)
    with _business_transaction(conn):
        proposal = proposals.get_by_id(proposal_id)
        if proposal is None:
            raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
        if proposal.status.value != "PENDING":
            raise ProposalAlreadyResolved(
                f"proposal {proposal_id} 已是 {proposal.status.value}，不能再次处理"
            )
        current = project.require_canon_version(conn, proposal.project_id)
        if review.expected_canon_version != current:
            raise project.StaleBaseVersion(
                proposal.project_id,
                expected=review.expected_canon_version,
                current=current,
            )
        if proposal.base_canon_version != current:
            # 作者在当前版本上明确审阅：base 落后是「批量抽取后再审」的顺序产物，
            # 不是错误。把 base 推进到 current，随后 _validate_current_canon 仍会
            # 对当前 canon 重验全部事实（同 rebase_pending_cohort 的承诺）。
            proposal = proposals.rebase_to_current(proposal.id, current)
        validated, source_events, event_evidence, source_edges, edge_evidence = (
            _hydrate_cluster(proposal, review, events, edge_reviews)
        )
        _validate_current_canon(
            proposal,
            validated,
            source_edges,
            edge_reviews,
        )
        canon_events: tuple[EventView, ...] = ()
        canon_edges: tuple[ReviewableEdge, ...] = ()
        characters: tuple[NodeRef, ...] = ()
        if review.action in {ProposalAction.ACCEPT, ProposalAction.EDIT}:
            canon_events = _edit_cast(
                events,
                proposal.project_id,
                _clone_events(
                    events,
                    proposal.event_ids,
                    edited_summary=review.edited_summary,
                ),
                review,
            )
            if proposal.edge_ids:
                try:
                    canon_edges = edge_reviews.clone_to_canon(
                        proposal.project_id, proposal.edge_ids
                    )
                except EdgeReviewValidationError as exc:
                    raise ProposalShapeError(str(exc)) from exc
            if validated.characters:
                characters = _create_characters(
                    graph, proposal.project_id, validated.characters
                )
        status = {
            ProposalAction.ACCEPT: "ACCEPTED",
            ProposalAction.EDIT: "EDITED",
            ProposalAction.REJECT: "REJECTED",
            ProposalAction.BYSTANDER: "REJECTED",
        }[review.action]
        if review.action in {ProposalAction.ACCEPT, ProposalAction.EDIT}:
            canon_version = project.compare_and_bump_canon_version(
                conn, proposal.project_id, current
            )
        else:
            canon_version = current
        audit_events = canon_events or source_events
        audit_edges = canon_edges or source_edges
        envelope = build_audit_envelope(
            proposal_id=proposal.id,
            action=review.action,
            status=status,
            canon_version=canon_version,
            kind=proposal.kind,
            events=_audit_pairs(audit_events, proposal.event_ids, event_evidence),
            edges=_audit_pairs(audit_edges, proposal.edge_ids, edge_evidence),
            characters=tuple(
                (candidate, characters[index] if characters else None)
                for index, candidate in enumerate(validated.characters)
            ),
        )
        proposals.mark_resolved(
            proposal.id,
            ProposalResolutionMark(
                status=status,
                action=review.action.value,
                canon_version=canon_version,
                audit_envelope=envelope.snapshot(),
            ),
        )
        if review.action in {ProposalAction.ACCEPT, ProposalAction.EDIT}:
            proposals.rebase_pending_cohort(
                proposal.id,
                current,
                canon_version,
            )

    decision, _audited = ensure_proposal_audit(conn, proposals, proposal.id)
    return _resolution(
        proposal,
        status=status,
        canon_version=canon_version,
        decision_id=decision.id,
        events=canon_events,
        edges=canon_edges,
        characters=characters,
    )


from .proposal_confirm import (  # noqa: E402 — public facade after review implementation
    confirm_provisional_edge,
    confirm_provisional_edges,
    confirm_provisional_event,
    confirm_provisional_events,
)


def recover_proposal_audit(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProposalResolution:
    """Lazily load recovery so it can reuse the private, already-initialized review helpers."""
    from .proposal_recovery import recover_proposal_audit as recover

    return recover(
        conn,
        graph,
        events,
        proposal_id,
        proposal_store=proposal_store,
        edge_review_store=edge_review_store,
    )
