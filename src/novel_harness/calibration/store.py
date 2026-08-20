"""非 Canon 校准产物的 SQLite 存储（迁移 017 / ADR 0033）。

**它只碰 `calibration_artifact` 与 `calibration_handoff_outbox` 两张表**，不碰图、
不碰正文、不碰会话。连接由装配层注入（同 `decisions.py` 的形状），不 import sqlite3。

不变量：

- 按 `(project_id, kind, content_sha256)` 幂等：同一内容只留一行，返回既有行；
- 可过期、可撤销、可重建；**永不晋升为事实**；
- `require_draftable` 逐项校验项目 / 章号 / 作者 turn / 水位 / 状态。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Final

from ..db import Connection
from ..ids import EntityType, new_id
from .models import (
    CalibrationReport,
    CalibrationStatus,
    ContinuityConflictHandoff,
    Sealability,
    SealedCalibration,
)


class CalibrationStoreError(RuntimeError):
    """存储层的底座。"""


class CalibrationNotFound(CalibrationStoreError):
    """按 ID 找不到校准产物。"""


class CalibrationRefused(CalibrationStoreError):
    """校准产物存在但不可起草（状态/项目/章号/turn/水位不匹配）。"""


def _content_hash(payload_json: str) -> str:
    return sha256(payload_json.encode("utf-8")).hexdigest()


class CalibrationStore:
    """`calibration_artifact` + `calibration_handoff_outbox` 的读写口。

    这是**非 Canon 写端口**：它能写的东西只有这两张表，模型改不了 canon。
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── inspection（CalibrationReport）───────────────────────────────────

    def save_inspection(self, report: CalibrationReport) -> CalibrationReport:
        """按内容幂等保存 inspection。返回库里那一份（可能是既有行）。"""
        payload = report.model_dump_json(exclude={"id"})
        content_hash = _content_hash(payload)
        existing = self._by_content(report.project_id, "inspection", content_hash)
        if existing is not None:
            return self._load_inspection(existing)
        self._insert(
            kind="inspection",
            project_id=report.project_id,
            chapter=report.chapter,
            author_turn_id=report.author_turn_id,
            author_request_sha256=report.author_request_sha256,
            status=(
                CalibrationStatus.NEEDS_AUTHOR
                if report.sealability.value == "NEEDS_AUTHOR"
                else CalibrationStatus.OPEN
            ),
            payload_json=report.model_dump_json(),
            content_sha256=content_hash,
            source_watermark_json=report.source_watermark.model_dump_json(),
            id=report.id,
        )
        return report

    def get_inspection(
        self, project_id: str, inspection_id: str
    ) -> CalibrationReport | None:
        row = self._get_row(project_id, inspection_id, "inspection")
        return None if row is None else self._load_inspection(row)

    # ── sealed（SealedCalibration）───────────────────────────────────────

    def save_sealed(self, sealed: SealedCalibration) -> SealedCalibration:
        payload = sealed.model_dump_json(exclude={"calibration_id"})
        content_hash = _content_hash(payload)
        existing = self._by_content(sealed.project_id, "sealed", content_hash)
        if existing is not None:
            return self._load_sealed(existing)
        self._insert(
            kind="sealed",
            project_id=sealed.project_id,
            chapter=sealed.chapter,
            author_turn_id=sealed.author_turn_id,
            author_request_sha256=sealed.author_request_sha256,
            status=sealed.status,
            payload_json=sealed.model_dump_json(),
            content_sha256=content_hash,
            source_watermark_json=sealed.source_watermark.model_dump_json(),
            id=sealed.calibration_id,
        )
        return sealed

    def get_sealed(
        self, project_id: str, calibration_id: str
    ) -> SealedCalibration | None:
        row = self._get_row(project_id, calibration_id, "sealed")
        return None if row is None else self._load_sealed(row)

    def require_draftable(
        self,
        calibration_id: str,
        *,
        project_id: str,
        chapter: int,
        author_turn_id: str,
        author_request_sha256: str,
        target_sha256: str,
        canon_version: int,
    ) -> SealedCalibration:
        """`draft_chapter` 的唯一取物口。逐项校验，任何不匹配都拒绝并要求重新校准。"""
        sealed = self.get_sealed(project_id, calibration_id)
        if sealed is None:
            raise CalibrationNotFound(
                f"没有这个校准产物（{calibration_id}）。先用 calibrate_scene 校准、"
                "再用 seal_scene_brief 封存，然后拿它给的编号来起草。"
            )
        if sealed.status is not CalibrationStatus.READY_FOR_DRAFT:
            raise CalibrationRefused(
                f"校准产物 {calibration_id} 状态是 {sealed.status.value}，不能起草。"
                "重新校准并封存。"
            )
        if sealed.project_id != project_id:
            raise CalibrationRefused("校准产物不属于这个项目。")
        if sealed.chapter != chapter:
            raise CalibrationRefused(
                f"校准产物是第 {sealed.chapter} 章的，不能拿去起草第 {chapter} 章。"
            )
        if (
            sealed.author_turn_id != author_turn_id
            or sealed.author_request_sha256 != author_request_sha256
        ):
            raise CalibrationRefused(
                "作者在这份校准之后又说了话（或这份校准不属于当前消息），"
                "旧产物已过期，请重新校准。"
            )
        watermark = sealed.source_watermark
        if watermark.target_sha256 != target_sha256:
            raise CalibrationRefused(
                "目标章正文在这份校准之后变了。请重新校准（旧产物已过期）。"
            )
        if watermark.canon_version != canon_version:
            raise CalibrationRefused(
                "图谱在这份校准之后变了（canon version 不匹配）。请重新校准。"
            )
        return sealed

    def mark_stale(self, project_id: str, artifact_id: str) -> bool:
        """水位变化后的过期标记。只改状态，不删行（可重建）。"""
        cur = self._conn.execute(
            "UPDATE calibration_artifact SET status = ?, updated_at = "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE project_id = ? AND id = ? AND status != 'REVOKED'",
            (CalibrationStatus.STALE.value, project_id, artifact_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_revoked(self, project_id: str, artifact_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE calibration_artifact SET status = ?, updated_at = "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE project_id = ? AND id = ?",
            (CalibrationStatus.REVOKED.value, project_id, artifact_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def latest_sealed(self, project_id: str, chapter: int) -> SealedCalibration | None:
        row = self._conn.execute(
            "SELECT id FROM calibration_artifact"
            " WHERE project_id = ? AND chapter = ? AND kind = 'sealed'"
            "   AND status = 'READY_FOR_DRAFT'"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id, chapter),
        ).fetchone()
        if row is None:
            return None
        return self.get_sealed(project_id, str(row["id"]))

    # ── handoff outbox（producer 侧）─────────────────────────────────────

    def push_handoff(self, handoff: ContinuityConflictHandoff) -> str:
        """RETCON 的持久化 handoff。通知任务消费它；这里不判第二次「是否冲突」。"""
        outbox_id = new_id(EntityType.HANDOFF, handoff.project_id)
        self._conn.execute(
            "INSERT INTO calibration_handoff_outbox"
            " (id, project_id, chapter, calibration_id, author_turn_id, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                outbox_id,
                handoff.project_id,
                handoff.chapter,
                handoff.calibration_id,
                handoff.author_turn_id,
                handoff.model_dump_json(),
            ),
        )
        self._conn.commit()
        return outbox_id

    def pending_handoffs(
        self, project_id: str, *, limit: int = 100
    ) -> list[tuple[str, ContinuityConflictHandoff]]:
        rows = self._conn.execute(
            "SELECT id, payload_json FROM calibration_handoff_outbox"
            " WHERE project_id = ? AND consumed = 0"
            " ORDER BY created_at, id LIMIT ?",
            (project_id, limit),
        ).fetchall()
        out: list[tuple[str, ContinuityConflictHandoff]] = []
        for row in rows:
            handoff = ContinuityConflictHandoff.model_validate_json(str(row["payload_json"]))
            out.append((str(row["id"]), handoff))
        return out

    def mark_handoff_consumed(self, outbox_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE calibration_handoff_outbox SET consumed = 1 WHERE id = ? AND consumed = 0",
            (outbox_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _insert(
        self,
        *,
        id: str,
        kind: str,
        project_id: str,
        chapter: int,
        author_turn_id: str,
        author_request_sha256: str,
        status: CalibrationStatus,
        payload_json: str,
        content_sha256: str,
        source_watermark_json: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO calibration_artifact"
            " (id, kind, project_id, chapter, author_turn_id, author_request_sha256,"
            "  status, payload_json, content_sha256, source_watermark_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                id,
                kind,
                project_id,
                chapter,
                author_turn_id,
                author_request_sha256,
                status.value,
                payload_json,
                content_sha256,
                source_watermark_json,
            ),
        )
        self._conn.commit()

    def _by_content(
        self, project_id: str, kind: str, content_hash: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_artifact"
            " WHERE project_id = ? AND kind = ? AND content_sha256 = ?"
            " ORDER BY created_at, id LIMIT 1",
            (project_id, kind, content_hash),
        ).fetchone()
        return None if row is None else dict(row)

    def _get_row(
        self, project_id: str, artifact_id: str, kind: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_artifact"
            " WHERE project_id = ? AND id = ? AND kind = ?",
            (project_id, artifact_id, kind),
        ).fetchone()
        return None if row is None else dict(row)

    def _load_inspection(self, row: dict[str, Any]) -> CalibrationReport:
        report = CalibrationReport.model_validate_json(str(row["payload_json"]))
        status_col = str(row["status"])
        sealability = (
            Sealability.READY_TO_SEAL
            if status_col == CalibrationStatus.OPEN.value
            else Sealability(status_col)
        )
        return report.model_copy(update={"sealability": sealability})

    def _load_sealed(self, row: dict[str, Any]) -> SealedCalibration:
        sealed = SealedCalibration.model_validate_json(str(row["payload_json"]))
        return sealed.model_copy(
            update={"status": CalibrationStatus(str(row["status"]))}
        )


__all__: Final = [
    "CalibrationNotFound",
    "CalibrationRefused",
    "CalibrationStore",
    "CalibrationStoreError",
]
