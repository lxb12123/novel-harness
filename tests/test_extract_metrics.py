"""Three-chapter M4 acceptance metrics are deterministic and review-aware."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from novel_harness import project
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.events import (
    ProposalAuditSnapshot,
    ProposalCreate,
    ProposalResolutionMark,
)
from novel_harness.graph import ChapterSpec
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph


@pytest.fixture
def conn() -> Iterator[Connection]:
    connection = connect(IN_MEMORY)
    migrate(connection)
    yield connection
    connection.close()


def _seed_chapters(conn: Connection, project_id: str, chapters: range) -> dict[int, str]:
    graph = SqliteStoryGraph(conn)
    return {
        chapter: graph.put_chapter(
            ChapterSpec(
                project_id=project_id,
                number=chapter,
                heading=f"第{chapter}章",
                path=f"chapters/{chapter:04d}.md",
                text=f"第{chapter}章正文。\n",
            )
        ).snapshot_id
        for chapter in chapters
    }


def _run(
    conn: Connection,
    project_id: str,
    chapter: int,
    snapshot_id: str,
    *,
    status: str,
    valid: int = 0,
    discarded: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO extraction_run (
            id, project_id, chapter_number, snapshot_id, status,
            valid_event_count, discarded_event_count, schema_version, prompt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'chapter-analysis-v1', ?)
        """,
        (
            f"extraction_run:{project_id}:{chapter}",
            project_id,
            chapter,
            snapshot_id,
            status,
            valid,
            discarded,
            f"prompt:{project_id}:{chapter}",
        ),
    )


def _proposal(
    store: SqliteProposalStore,
    project_id: str,
    chapter: int,
    snapshot_id: str,
    *,
    kind: str,
    item_count: int,
    status: str | None,
) -> None:
    created = store.create(
        ProposalCreate(
            project_id=project_id,
            chapter_number=chapter,
            snapshot_id=snapshot_id,
            kind=kind,
            items=[{"item": index} for index in range(item_count)],
        )
    )
    if status is not None:
        action = {
            "ACCEPTED": "accept",
            "EDITED": "edit",
            "REJECTED": "reject",
        }[status]
        canon_version = created.base_canon_version + (
            1 if action in {"accept", "edit"} else 0
        )
        store.mark_resolved(
            created.id,
            ProposalResolutionMark(
                status=status,
                action=action,
                canon_version=canon_version,
                audit_envelope=ProposalAuditSnapshot(
                    payload={
                        "proposal_id": created.id,
                        "action": action,
                        "status": status,
                        "canon_version": canon_version,
                        "kind": created.kind,
                        "events": [],
                        "edges": [],
                        "characters": [],
                    }
                ),
            ),
        )


def test_metrics_for_three_chapters_compute_the_locked_acceptance_gate(
    conn: Connection,
) -> None:
    from novel_harness.extract.metrics import metrics_for_range

    project_id = project.create(conn, name="三章验收", root_path=".").id
    snapshots = _seed_chapters(conn, project_id, range(1, 5))
    _run(conn, project_id, 1, snapshots[1], status="SUCCEEDED", valid=4, discarded=1)
    _run(conn, project_id, 2, snapshots[2], status="FAILED")
    _run(conn, project_id, 3, snapshots[3], status="SUCCEEDED", valid=3, discarded=2)
    # Outside the selected range and therefore invisible.
    _run(conn, project_id, 4, snapshots[4], status="SUCCEEDED", valid=99, discarded=99)

    generated = iter(
        [
            "proposal:conflict-one",
            "proposal:conflict-two",
            "proposal:bystander",
            "proposal:pending",
            "proposal:outside",
        ]
    )
    proposals = SqliteProposalStore(conn, proposal_id_factory=lambda _pid: next(generated))
    _proposal(
        proposals,
        project_id,
        1,
        snapshots[1],
        kind="edge_conflict",
        item_count=2,
        status="ACCEPTED",
    )
    _proposal(
        proposals,
        project_id,
        2,
        snapshots[2],
        kind="edge_conflict",
        item_count=1,
        status="ACCEPTED",
    )
    # A bystander decision is persisted as REJECTED and must stay out of the numerator.
    _proposal(
        proposals,
        project_id,
        3,
        snapshots[3],
        kind="new_character",
        item_count=1,
        status="REJECTED",
    )
    _proposal(
        proposals,
        project_id,
        2,
        snapshots[2],
        kind="low_confidence_main",
        item_count=1,
        status=None,
    )
    _proposal(
        proposals,
        project_id,
        4,
        snapshots[4],
        kind="edge_conflict",
        item_count=8,
        status="ACCEPTED",
    )

    metrics = metrics_for_range(conn, project_id, 1, 3)

    assert (metrics.total_runs, metrics.succeeded_runs, metrics.failed_runs) == (3, 2, 1)
    assert (metrics.valid_event_count, metrics.discarded_event_count) == (7, 3)
    assert (metrics.pending_proposals, metrics.resolved_proposals) == (1, 3)
    assert (metrics.accepted_reviews, metrics.edited_reviews, metrics.rejected_reviews) == (
        2,
        0,
        1,
    )
    assert metrics.review_acceptance_rate == pytest.approx(2 / 3)
    assert metrics.review_acceptance_rate > 0.60
    assert [(item.chapter_number, item.conflict_count) for item in metrics.conflicts] == [
        (1, 2),
        (2, 1),
        (3, 0),
    ]
    assert metrics.max_conflicts_per_chapter == 2
    assert metrics.max_conflicts_per_chapter <= 2


def test_metrics_are_project_and_range_isolated(conn: Connection) -> None:
    from novel_harness.extract.metrics import metrics_for_range

    selected = project.create(conn, name="选中项目", root_path=".").id
    other = project.create(conn, name="另一项目", root_path=".").id
    selected_snapshots = _seed_chapters(conn, selected, range(1, 2))
    other_snapshots = _seed_chapters(conn, other, range(1, 2))
    _run(conn, other, 1, other_snapshots[1], status="SUCCEEDED", valid=50)
    generated = iter(["proposal:other-project", "proposal:selected-pending"])
    other_proposals = SqliteProposalStore(
        conn,
        proposal_id_factory=lambda _pid: next(generated),
    )
    _proposal(
        other_proposals,
        other,
        1,
        other_snapshots[1],
        kind="edge_conflict",
        item_count=9,
        status="ACCEPTED",
    )
    _proposal(
        other_proposals,
        selected,
        1,
        selected_snapshots[1],
        kind="low_confidence_main",
        item_count=1,
        status=None,
    )
    conn.commit()

    metrics = metrics_for_range(conn, selected, 1, 1)

    assert metrics.total_runs == 0
    assert metrics.valid_event_count == 0
    assert (metrics.pending_proposals, metrics.resolved_proposals) == (1, 0)
    assert metrics.conflicts[0].conflict_count == 0
    assert metrics.review_acceptance_rate is None
    # Caller-owned transaction remains caller-owned.
    conn.execute("BEGIN IMMEDIATE")
    assert metrics_for_range(conn, selected, 1, 1).total_runs == 0
    assert conn.in_transaction
    conn.rollback()


def test_metrics_count_all_run_states_and_every_terminal_review_status(
    conn: Connection,
) -> None:
    from novel_harness.extract.metrics import metrics_for_range

    project_id = project.create(conn, name="状态口径", root_path=".").id
    snapshots = _seed_chapters(conn, project_id, range(1, 5))
    for chapter, status in enumerate(
        ("PENDING", "RUNNING", "SUCCEEDED", "FAILED"), start=1
    ):
        _run(conn, project_id, chapter, snapshots[chapter], status=status)
    generated = iter(
        ["proposal:pending", "proposal:accepted", "proposal:edited", "proposal:rejected"]
    )
    proposals = SqliteProposalStore(conn, proposal_id_factory=lambda _pid: next(generated))
    for chapter, status in enumerate((None, "ACCEPTED", "EDITED", "REJECTED"), start=1):
        _proposal(
            proposals,
            project_id,
            chapter,
            snapshots[chapter],
            kind="low_confidence_main",
            item_count=1,
            status=status,
        )

    metrics = metrics_for_range(conn, project_id, 1, 4)

    assert (metrics.total_runs, metrics.succeeded_runs, metrics.failed_runs) == (4, 1, 1)
    assert (metrics.pending_proposals, metrics.resolved_proposals) == (1, 3)
    assert (metrics.accepted_reviews, metrics.edited_reviews, metrics.rejected_reviews) == (
        1,
        1,
        1,
    )
    assert metrics.review_acceptance_rate == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    ("first", "last", "error", "match"),
    [
        (0, 1, ValueError, "at least 1"),
        (2, 1, ValueError, "first_chapter"),
        (True, 1, TypeError, "integer"),
        (1, 10_001, ValueError, "at most 10000"),
        (2**63, 2**63, ValueError, "SQLite"),
    ],
)
def test_metrics_reject_invalid_ranges(
    conn: Connection,
    first: object,
    last: object,
    error: type[Exception],
    match: str,
) -> None:
    from novel_harness.extract.metrics import metrics_for_range

    with pytest.raises(error, match=match):
        metrics_for_range(conn, "project:any", first, last)  # type: ignore[arg-type]


def test_metrics_reject_an_unknown_project(conn: Connection) -> None:
    from novel_harness.extract.metrics import MetricsProjectNotFound, metrics_for_range

    with pytest.raises(MetricsProjectNotFound, match="project:missing"):
        metrics_for_range(conn, "project:missing", 1, 3)
