"""测试共用的校准种子：一份 READY_TO_SEAL inspection + 一份 READY_FOR_DRAFT sealed。

不是 fixture，是纯构造函数：`test_agent_tools` / `test_turn_events` 的整表跑需要
`draft_chapter` 一开始就有可用的 `calibration_id`，而这两个文件各自把 world 的
形状带进来，这里只收最少的字段。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from novel_harness import importer
from novel_harness.calibration.models import (
    AuthorTurnRef,
    CalibrationReport,
    CoverageReceipt,
    DirectiveCandidate,
    DirectiveKind,
    IntendedCastMember,
    SceneBrief,
    SceneProposal,
    Sealability,
    SealedCalibration,
    SourceWatermark,
)
from novel_harness.calibration.store import CalibrationStore

SEEDED_INSPECTION_ID = "inspection:test:seeded"
SEEDED_CALIBRATION_ID = "calibration:test:seeded"


def _turn(project_id: str) -> AuthorTurnRef:
    return AuthorTurnRef(
        turn_id=f"{project_id}:author:turn",
        request_sha256=sha256("author turn".encode("utf-8")).hexdigest(),
    )


def seed_calibration(
    *,
    conn: Any,
    project_id: str,
    store: Any,
    root: Any,
    chapter: int,
    goal: str = "写萧决独自走进北荒",
    proposal: SceneProposal | None = None,
) -> tuple[CalibrationStore, AuthorTurnRef]:
    """造一份和当前世界状态（章 sha / canon version / author turn）一致的校准产物。"""
    text = importer.read_chapter(root, chapter) or ""
    target_sha = importer.text_digest(text)
    canon_version = store.canon_version(project_id)
    turn = _turn(project_id)
    watermark = SourceWatermark(
        schema_version="test",
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        canon_version=canon_version,
        target_sha256=target_sha,
    )
    proposal = proposal or SceneProposal(
        chapter=chapter,
        intended_cast=(IntendedCastMember(surface="萧决"),),
        directive_candidates=(
            DirectiveCandidate(
                kind=DirectiveKind.ENTER_LOCATION,
                actor_surface="萧决",
                location_surface="北荒",
            ),
        ),
    )
    inspection = CalibrationReport(
        id=SEEDED_INSPECTION_ID,
        project_id=project_id,
        chapter=chapter,
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        proposal=proposal,
        source_watermark=watermark,
        sealability=Sealability.READY_TO_SEAL,
        coverage_receipt=CoverageReceipt(),
    )
    sealed = SealedCalibration(
        calibration_id=SEEDED_CALIBRATION_ID,
        project_id=project_id,
        chapter=chapter,
        author_turn_id=turn.turn_id,
        author_request_sha256=turn.request_sha256,
        goal_spec=goal,
        source_watermark=watermark,
        source_inspection_id=SEEDED_INSPECTION_ID,
        scene_brief=SceneBrief(chapter=chapter),
    )
    store = CalibrationStore(conn)
    if store.get_inspection(project_id, SEEDED_INSPECTION_ID) is None:
        store.save_inspection(inspection)
    if store.get_sealed(project_id, SEEDED_CALIBRATION_ID) is None:
        store.save_sealed(sealed)
    return store, turn
