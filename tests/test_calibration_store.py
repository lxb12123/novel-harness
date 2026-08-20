"""非 Canon artifact 存储（迁移 017 / ADR 0033）：幂等、可过期、可重建、handoff outbox。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import project
from novel_harness.calibration.models import (
    AuthorTurnRef,
    CalibrationReport,
    CoverageReceipt,
    SceneBrief,
    SceneProposal,
    Sealability,
    SealedCalibration,
    SourceWatermark,
)
from novel_harness.calibration.store import (
    CalibrationNotFound,
    CalibrationRefused,
    CalibrationStore,
)
from novel_harness.db import Connection, connect, migrate
from novel_harness.ids import EntityType, new_id


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    c = connect(tmp_path / "cal.db")
    migrate(c)
    yield c
    c.close()


def _turn(pid: str) -> AuthorTurnRef:
    return AuthorTurnRef(
        turn_id=f"{pid}:turn",
        request_sha256="0" * 64,
    )


def _inspection(pid: str, *, chapter: int = 11, target_sha: str = "1" * 64) -> CalibrationReport:
    turn = _turn(pid)
    return CalibrationReport(
        id=new_id(EntityType.CALIBRATION, pid),
        project_id=pid,
        chapter=chapter,
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        proposal=SceneProposal(chapter=chapter),
        source_watermark=SourceWatermark(
            schema_version="test",
            author_turn_id=turn.turn_id,
            author_request_sha256=turn.request_sha256,
            canon_version=0,
            target_sha256=target_sha,
        ),
        sealability=Sealability.READY_TO_SEAL,
        coverage_receipt=CoverageReceipt(),
    )


def _sealed(pid: str, *, chapter: int = 11, target_sha: str = "1" * 64) -> SealedCalibration:
    turn = _turn(pid)
    inspection = _inspection(pid, chapter=chapter, target_sha=target_sha)
    return SealedCalibration(
        calibration_id=new_id(EntityType.CALIBRATION, pid),
        project_id=pid,
        chapter=chapter,
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        goal_spec="陈舟进入仓库",
        source_watermark=inspection.source_watermark,
        source_inspection_id=inspection.id,
        scene_brief=SceneBrief(chapter=chapter),
    )


def test_save_is_idempotent_by_content(conn: Connection) -> None:
    pid = project.create(conn, name="仓库", root_path="/tmp").id
    store = CalibrationStore(conn)
    first = store.save_inspection(_inspection(pid))
    second = store.save_inspection(_inspection(pid))
    assert first.id == second.id, "同一内容两次校准生成了两份 —— 内容寻址幂等破了"


def test_require_draftable_rejects_every_watermark_mismatch(conn: Connection) -> None:
    pid = project.create(conn, name="仓库", root_path="/tmp").id
    store = CalibrationStore(conn)
    store.save_sealed(_sealed(pid))
    turn = _turn(pid)
    ok = store.require_draftable(
        store.latest_sealed(pid, 11).calibration_id,
        project_id=pid,
        chapter=11,
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        target_sha256="1" * 64,
        canon_version=0,
    )
    assert ok.status.value == "READY_FOR_DRAFT"

    with pytest.raises(CalibrationNotFound):
        store.require_draftable(
            "calibration:nope",
            project_id=pid,
            chapter=11,
            author_turn_id=turn.turn_id,
            author_request_sha256=turn.request_sha256,
            target_sha256="1" * 64,
            canon_version=0,
        )
    with pytest.raises(CalibrationRefused, match="第 11 章"):
        store.require_draftable(
            store.latest_sealed(pid, 11).calibration_id,
            project_id=pid,
            chapter=12,
            author_turn_id=turn.turn_id,
            author_request_sha256=turn.request_sha256,
            target_sha256="1" * 64,
            canon_version=0,
        )
    with pytest.raises(CalibrationRefused, match="变了"):
        store.require_draftable(
            store.latest_sealed(pid, 11).calibration_id,
            project_id=pid,
            chapter=11,
            author_turn_id=turn.turn_id,
            author_request_sha256=turn.request_sha256,
            target_sha256="2" * 64,
            canon_version=0,
        )
    with pytest.raises(CalibrationRefused, match="canon version"):
        store.require_draftable(
            store.latest_sealed(pid, 11).calibration_id,
            project_id=pid,
            chapter=11,
            author_turn_id=turn.turn_id,
            author_request_sha256=turn.request_sha256,
            target_sha256="1" * 64,
            canon_version=3,
        )
    with pytest.raises(CalibrationRefused, match="又说了话"):
        store.require_draftable(
            store.latest_sealed(pid, 11).calibration_id,
            project_id=pid,
            chapter=11,
            author_turn_id="another-turn",
            author_request_sha256="1" * 64,
            target_sha256="1" * 64,
            canon_version=0,
        )


def test_stale_and_revoked_are_reversible_non_canon_lifecycle(conn: Connection) -> None:
    pid = project.create(conn, name="仓库", root_path="/tmp").id
    store = CalibrationStore(conn)
    sealed = store.save_sealed(_sealed(pid))
    assert store.mark_stale(pid, sealed.calibration_id)
    assert store.get_sealed(pid, sealed.calibration_id).status.value == "STALE"
    assert store.mark_revoked(pid, sealed.calibration_id)
    assert store.get_sealed(pid, sealed.calibration_id).status.value == "REVOKED"


def test_handoff_outbox_is_producer_side_persistence(conn: Connection) -> None:
    """RETCON 只落在 producer outbox；通知侧消费它，这里不判第二次。"""
    from novel_harness.calibration.handoff import build_retcon_handoff
    from novel_harness.calibration.models import ReferencedFact, EpistemicKind, FactType

    pid = project.create(conn, name="仓库", root_path="/tmp").id
    store = CalibrationStore(conn)
    sealed = store.save_sealed(_sealed(pid))
    handoff = build_retcon_handoff(
        sealed=sealed,
        proposal_item="proposal:0",
        inferred_tension="陈舟伤势与「健步如飞」表面冲突（机器推演）。",
        referenced_facts=(
            ReferencedFact(
                fact_id="11:1:BODY_LIMITATION",
                kind=EpistemicKind.AUTHOR_CANON_FACT,
                fact_type=FactType.BODY_LIMITATION,
                valid_from_chapter=10,
            ),
        ),
    )
    outbox_id = store.push_handoff(handoff)
    pending = store.pending_handoffs(pid)
    assert [item[0] for item in pending] == [outbox_id]
    assert pending[0][1].author_choice == "RETCON"
    assert store.mark_handoff_consumed(outbox_id)
    assert store.pending_handoffs(pid) == []
