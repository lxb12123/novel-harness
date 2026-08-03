"""Repair the narrow post-commit gap between proposal state and decision audit."""

from __future__ import annotations

from ..db import Connection
from ..events import EventStore, ProposalStore
from ..graph import GraphStore
from ..graph.review_store import EdgeReviewStore
from ..graph.sqlite_proposals import SqliteProposalStore
from .proposal_audit import ensure_proposal_audit
from .proposal_models import ProposalResolution


def recover_proposal_audit(
    conn: Connection,
    graph: GraphStore,
    events: EventStore,
    proposal_id: str,
    *,
    proposal_store: ProposalStore | None = None,
    edge_review_store: EdgeReviewStore | None = None,
) -> ProposalResolution:
    """Attach the exact audit snapshot committed with a terminal proposal.

    ``graph``/``events`` remain in the public signature for compatibility, but recovery deliberately
    does not rehydrate mutable PROVISIONAL facts.  The proposal row is the durable outbox.
    """
    _ = graph, events, edge_review_store
    proposals = proposal_store or SqliteProposalStore(conn)
    decision, proposal = ensure_proposal_audit(conn, proposals, proposal_id)
    if proposal.resolved_canon_version is None:
        raise RuntimeError(f"proposal {proposal.id} 审计恢复后缺少 canon version")
    return ProposalResolution(
        proposal_id=proposal.id,
        status=proposal.status.value,
        canon_version=proposal.resolved_canon_version,
        decision_id=decision.id,
    )
