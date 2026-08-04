"""SQLite implementation of the proposal-cluster repository contract."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..db import Connection
from ..events.models import (
    ProposalCreate,
    ProposalRecord,
    ProposalResolutionMark,
)
from ..events.store import (
    ProposalAlreadyResolved,
    ProposalNotFound,
    ProposalValidationError,
)
from ..ids import EntityType, new_id
from ..json_contract import strict_json_dumps
from .sqlite_store import _transaction


@dataclass(frozen=True)
class ConfirmationReceipt:
    """Durable outbox row for an explicit passive confirmation batch.

    ``event_ids`` / ``edge_ids`` are the PROVISIONAL source facts in original request
    order (link rowid order); the CANON projections are recorded inside the audit
    envelope payload.
    """

    id: str
    project_id: str
    fact_kind: str
    request_hash: str
    base_canon_version: int
    resolved_canon_version: int
    decision_log_id: str | None
    event_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    envelope: dict[str, Any]
    items: list[Any]


def _default_proposal_id(project_id: str) -> str:
    return new_id(EntityType.PROPOSAL, project_id)


@contextmanager
def _read_snapshot(conn: Connection) -> Iterator[None]:
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


class SqliteProposalStore:
    def __init__(
        self,
        conn: Connection,
        *,
        proposal_id_factory: Callable[[str], str] = _default_proposal_id,
    ) -> None:
        self._conn = conn
        self._new_proposal_id = proposal_id_factory

    def create(self, proposal: ProposalCreate) -> ProposalRecord:
        proposal_id = self._new_proposal_id(proposal.project_id)
        try:
            items_json = json.dumps(
                proposal.items,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            items_json.encode("utf-8")
        except (UnicodeEncodeError, ValueError) as exc:
            raise ProposalValidationError(
                f"proposal items 必须是 strict UTF-8 JSON：{exc}"
            ) from exc
        with _transaction(self._conn):
            self._validate_context(proposal)
            self._validate_links(proposal)
            self._conn.execute(
                """
                INSERT INTO proposal_set (
                    id, project_id, kind, summary, item_count, items_json, confidence,
                    chapter_number, snapshot_id, base_canon_version, schema_version, prompt_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    proposal.project_id,
                    proposal.kind,
                    proposal.summary,
                    proposal.item_count,
                    items_json,
                    proposal.confidence,
                    proposal.chapter_number,
                    proposal.snapshot_id,
                    proposal.base_canon_version,
                    proposal.schema_version,
                    proposal.prompt_hash,
                ),
            )
            for event_id in sorted(set(proposal.event_ids)):
                self._conn.execute(
                    "INSERT INTO proposal_event (proposal_id, project_id, event_id) "
                    "VALUES (?, ?, ?)",
                    (proposal_id, proposal.project_id, event_id),
                )
            for edge_id in sorted(set(proposal.edge_ids)):
                self._conn.execute(
                    "INSERT INTO proposal_edge (proposal_id, project_id, edge_id) VALUES (?, ?, ?)",
                    (proposal_id, proposal.project_id, edge_id),
                )
            created = self.get(proposal.project_id, proposal_id)
            if created is None:
                raise RuntimeError(f"proposal 插入后不可读：{proposal_id}")
            return created

    def _validate_context(self, proposal: ProposalCreate) -> None:
        project = self._conn.execute(
            "SELECT canon_version FROM project WHERE id = ?",
            (proposal.project_id,),
        ).fetchone()
        if project is None:
            raise ProposalValidationError(f"项目不存在：{proposal.project_id}")
        if int(project[0]) != proposal.base_canon_version:
            raise ProposalValidationError(
                f"base_canon_version={proposal.base_canon_version} 与项目当前版本 "
                f"{project[0]} 不一致"
            )
        if proposal.snapshot_id is not None:
            snapshot = self._conn.execute(
                """
                SELECT 1 FROM chapter_snapshot AS snapshot
                JOIN chapter ON chapter.id = snapshot.chapter_id
                WHERE snapshot.id = ? AND chapter.project_id = ?
                  AND (? IS NULL OR chapter.number = ?)
                """,
                (
                    proposal.snapshot_id,
                    proposal.project_id,
                    proposal.chapter_number,
                    proposal.chapter_number,
                ),
            ).fetchone()
            if snapshot is None:
                raise ProposalValidationError("snapshot 必须属于 proposal 的 project/chapter")
        elif proposal.chapter_number is not None:
            chapter = self._conn.execute(
                "SELECT 1 FROM chapter WHERE project_id = ? AND number = ?",
                (proposal.project_id, proposal.chapter_number),
            ).fetchone()
            if chapter is None:
                raise ProposalValidationError(
                    f"chapter_number={proposal.chapter_number} 不在项目 {proposal.project_id} 里"
                )

    def _validate_links(self, proposal: ProposalCreate) -> None:
        for kind, table, ids in (
            ("event", "story_event", proposal.event_ids),
            ("edge", "edge", proposal.edge_ids),
        ):
            wanted = set(ids)
            if not wanted:
                continue
            markers = ", ".join("?" for _ in wanted)
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE project_id = ? "
                f"AND information_scope = 'PROVISIONAL' AND id IN ({markers})",
                (proposal.project_id, *sorted(wanted)),
            ).fetchall()
            found = {str(row[0]) for row in rows}
            invalid = sorted(wanted - found)
            if invalid:
                raise ProposalValidationError(
                    f"proposal {kind} links 必须属于项目 {proposal.project_id} 且为 "
                    f"PROVISIONAL；无效引用：{invalid}"
                )

    def pending(
        self,
        project_id: str,
        chapter_number: int | None = None,
    ) -> list[ProposalRecord]:
        with _read_snapshot(self._conn):
            clause = "" if chapter_number is None else " AND chapter_number = ?"
            params: tuple[object, ...] = (
                (project_id,) if chapter_number is None else (project_id, chapter_number)
            )
            rows = self._conn.execute(
                "SELECT id FROM proposal_set WHERE project_id = ? AND status = 'PENDING'"
                f"{clause} ORDER BY created_at, id",
                params,
            ).fetchall()
            records: list[ProposalRecord] = []
            for row in rows:
                record = self.get(project_id, str(row[0]))
                if record is None:
                    raise RuntimeError(f"pending proposal 插入后不可读：{row[0]}")
                records.append(record)
            return records

    def get(self, project_id: str, proposal_id: str) -> ProposalRecord | None:
        row = self._conn.execute(
            """
            SELECT id, project_id, kind, summary, items_json, confidence, status,
                   created_at, resolved_at, decision_log_id, chapter_number, snapshot_id,
                   base_canon_version, schema_version, prompt_hash, resolution_action,
                   resolved_canon_version, audit_envelope_json
            FROM proposal_set WHERE project_id = ? AND id = ?
            """,
            (project_id, proposal_id),
        ).fetchone()
        if row is None:
            return None
        event_ids = [
            str(link[0])
            for link in self._conn.execute(
                "SELECT event_id FROM proposal_event WHERE proposal_id = ? ORDER BY event_id",
                (proposal_id,),
            ).fetchall()
        ]
        edge_ids = [
            str(link[0])
            for link in self._conn.execute(
                "SELECT edge_id FROM proposal_edge WHERE proposal_id = ? ORDER BY edge_id",
                (proposal_id,),
            ).fetchall()
        ]
        return ProposalRecord(
            id=row["id"],
            project_id=row["project_id"],
            kind=row["kind"],
            summary=row["summary"],
            items=json.loads(row["items_json"]),
            confidence=row["confidence"],
            chapter_number=row["chapter_number"],
            snapshot_id=row["snapshot_id"],
            base_canon_version=row["base_canon_version"],
            schema_version=row["schema_version"],
            prompt_hash=row["prompt_hash"],
            event_ids=event_ids,
            edge_ids=edge_ids,
            status=row["status"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            decision_log_id=row["decision_log_id"],
            resolution_action=row["resolution_action"],
            resolved_canon_version=row["resolved_canon_version"],
            audit_envelope=(
                None
                if row["audit_envelope_json"] is None
                else json.loads(row["audit_envelope_json"])
            ),
        )

    def get_by_id(self, proposal_id: str) -> ProposalRecord | None:
        row = self._conn.execute(
            "SELECT project_id FROM proposal_set WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        return None if row is None else self.get(str(row[0]), proposal_id)

    def mark_resolved(
        self,
        proposal_id: str,
        resolution: ProposalResolutionMark,
    ) -> ProposalRecord:
        with _transaction(self._conn):
            row = self._conn.execute(
                "SELECT project_id, kind, status, base_canon_version "
                "FROM proposal_set WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
            if row["status"] != "PENDING":
                raise ProposalAlreadyResolved(
                    f"proposal {proposal_id} 已是 {row['status']}，不能再次处理"
                )
            expected_version = int(row["base_canon_version"]) + (
                1 if resolution.action.value in {"accept", "edit"} else 0
            )
            payload = resolution.audit_envelope.payload
            expected_payload = {
                "proposal_id": proposal_id,
                "action": resolution.action.value,
                "status": resolution.status.value,
                "canon_version": resolution.canon_version,
                "kind": str(row["kind"]),
            }
            if resolution.canon_version != expected_version or any(
                payload.get(key) != value for key, value in expected_payload.items()
            ):
                raise ProposalValidationError(
                    f"proposal {proposal_id} resolution/audit metadata 不一致"
                )
            if resolution.action.value == "bystander" and row["kind"] != "new_character":
                raise ProposalValidationError("bystander 只允许 new_character proposal")
            if resolution.decision_log_id is not None:
                decision = self._conn.execute(
                    "SELECT project_id FROM decision_log WHERE id = ?",
                    (resolution.decision_log_id,),
                ).fetchone()
                if decision is None or decision["project_id"] != row["project_id"]:
                    raise ProposalValidationError(
                        f"decision_log {resolution.decision_log_id} 不存在或不属于项目 "
                        f"{row['project_id']}"
                    )
            try:
                envelope_json = strict_json_dumps(
                    resolution.audit_envelope.model_dump(mode="json")
                )
            except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
                raise ProposalValidationError(
                    f"proposal {proposal_id} audit envelope 不是 strict JSON"
                ) from exc
            try:
                self._conn.execute(
                    """
                    UPDATE proposal_set
                    SET status = ?,
                        resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                        resolution_action = ?,
                        resolved_canon_version = ?,
                        audit_envelope_json = ?,
                        decision_log_id = ?
                    WHERE id = ? AND status = 'PENDING'
                    """,
                    (
                        resolution.status.value,
                        resolution.action.value,
                        resolution.canon_version,
                        envelope_json,
                        resolution.decision_log_id,
                        proposal_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProposalValidationError(
                    f"proposal {proposal_id} resolution/audit metadata 被数据库拒绝"
                ) from exc
            record = self.get(str(row["project_id"]), proposal_id)
            if record is None:
                raise RuntimeError(f"resolved proposal 更新后不可读：{proposal_id}")
            return record

    def rebase_pending_cohort(
        self,
        resolved_proposal_id: str,
        from_canon_version: int,
        to_canon_version: int,
    ) -> int:
        """Advance only pending siblings emitted by the exact same extraction run.

        The selected proposal must already prove a real one-version Canon mutation.  Rebased
        siblings still undergo full fact/current-Canon validation when they are reviewed later.
        """
        if from_canon_version < 0 or to_canon_version != from_canon_version + 1:
            raise ProposalValidationError("cohort rebase 必须对应一次 one-version Canon bump")
        with _transaction(self._conn):
            row = self._conn.execute(
                """
                SELECT proposal_set.project_id, proposal_set.status,
                       proposal_set.resolution_action,
                       proposal_set.base_canon_version,
                       proposal_set.resolved_canon_version,
                       proposal_set.chapter_number, proposal_set.snapshot_id,
                       proposal_set.schema_version, proposal_set.prompt_hash,
                       project.canon_version AS project_canon_version
                FROM proposal_set
                JOIN project ON project.id = proposal_set.project_id
                WHERE proposal_set.id = ?
                """,
                (resolved_proposal_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(f"proposal 不存在：{resolved_proposal_id}")
            expected_status = {
                "accept": "ACCEPTED",
                "edit": "EDITED",
            }.get(row["resolution_action"])
            if (
                expected_status is None
                or row["status"] != expected_status
                or int(row["base_canon_version"]) != from_canon_version
                or row["resolved_canon_version"] != to_canon_version
                or row["project_canon_version"] != to_canon_version
            ):
                raise ProposalValidationError(
                    "cohort rebase 需要已提交且匹配 watermark 的 terminal Canon bump"
                )
            identity = (
                row["chapter_number"],
                row["snapshot_id"],
                row["schema_version"],
                row["prompt_hash"],
            )
            if any(value is None for value in identity):
                return 0
            updated = self._conn.execute(
                """
                UPDATE proposal_set
                SET base_canon_version = ?
                WHERE id <> ?
                  AND project_id = ?
                  AND status = 'PENDING'
                  AND base_canon_version = ?
                  AND chapter_number = ?
                  AND snapshot_id = ?
                  AND schema_version = ?
                  AND prompt_hash = ?
                """,
                (
                    to_canon_version,
                    resolved_proposal_id,
                    row["project_id"],
                    from_canon_version,
                    *identity,
                ),
            )
            return updated.rowcount

    def rebase_to_current(
        self,
        proposal_id: str,
        to_canon_version: int,
    ) -> ProposalRecord:
        """把一条 PENDING 提案的 base 推进到当前 canon 版本。

        批量抽取后跨章审阅时，作者在「当前版本」上明确确认提案：base 落后不是
        错误，而是批次顺序造成的。base 推进后，审阅仍会对当前 canon 重验事实
        （同 rebase_pending_cohort 的承诺）。
        """
        if to_canon_version < 0:
            raise ProposalValidationError("rebase 目标版本不能为负")
        with _transaction(self._conn):
            row = self._conn.execute(
                "SELECT project_id, status, base_canon_version FROM proposal_set WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
            if row["status"] != "PENDING":
                raise ProposalAlreadyResolved(
                    f"proposal {proposal_id} 已是 {row['status']}，不能 rebase"
                )
            changed = self._conn.execute(
                """
                UPDATE proposal_set SET base_canon_version = ?
                WHERE id = ? AND status = 'PENDING' AND base_canon_version <> ?
                """,
                (to_canon_version, proposal_id, to_canon_version),
            )
            if changed.rowcount > 1:
                raise RuntimeError("proposal rebase updated more than one row")
            record = self.get(str(row["project_id"]), proposal_id)
            if record is None:
                raise RuntimeError(f"rebase 后提案不可读：{proposal_id}")
            return record

    def attach_decision(self, proposal_id: str, decision_id: str) -> ProposalRecord:
        """Attach audit only after terminal business state has committed.

        Retrying the same attachment is idempotent.  A different decision id is never allowed to
        overwrite history, and the decision must belong to the proposal's project.
        """
        if not decision_id:
            raise ProposalValidationError("decision_id 不能为空")
        with _transaction(self._conn):
            row = self._conn.execute(
                """
                SELECT project_id, status, decision_log_id
                FROM proposal_set WHERE id = ?
                """,
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
            if row["status"] == "PENDING":
                raise ProposalValidationError(
                    f"proposal {proposal_id} 仍是 PENDING，只能给 terminal proposal 附审计"
                )
            decision = self._conn.execute(
                """
                SELECT project_id, kind, subject_name, quote_text, quote_sha256,
                       chapter_number, para_index, payload_json, decision
                FROM decision_log WHERE id = ?
                """,
                (decision_id,),
            ).fetchone()
            if decision is None or decision["project_id"] != row["project_id"]:
                raise ProposalValidationError(
                    f"decision_log {decision_id} 不存在或不属于 proposal 项目 "
                    f"{row['project_id']}"
                )
            record = self.get(str(row["project_id"]), proposal_id)
            if (
                record is None
                or record.audit_envelope is None
                or record.resolution_action is None
            ):
                raise ProposalValidationError(
                    f"proposal {proposal_id} 没有完整 durable audit metadata"
                )
            audit = record.audit_envelope
            expected_verdict = {
                "accept": "accept",
                "edit": "edit",
                "reject": "reject",
                "bystander": "reject",
            }[record.resolution_action.value]
            actual = {
                "payload": json.loads(decision["payload_json"]),
                "subject_name": decision["subject_name"],
                "quote_text": decision["quote_text"],
                "quote_sha256": decision["quote_sha256"],
                "chapter_number": decision["chapter_number"],
                "para_index": decision["para_index"],
            }
            if (
                decision["kind"] != "proposal_review"
                or decision["decision"] != expected_verdict
                or actual != audit.model_dump(mode="json")
            ):
                raise ProposalValidationError(
                    f"decision_log {decision_id} 不是 proposal {proposal_id} 的匹配审计"
                )
            attached = row["decision_log_id"]
            if attached == decision_id:
                record = self.get(str(row["project_id"]), proposal_id)
                if record is None:
                    raise RuntimeError(f"proposal 附审计后不可读：{proposal_id}")
                return record
            if attached is not None:
                raise ProposalValidationError(
                    f"proposal {proposal_id} 已附审计 {attached}，不能换成 {decision_id}"
                )
            try:
                updated = self._conn.execute(
                    """
                    UPDATE proposal_set SET decision_log_id = ?
                    WHERE id = ? AND status <> 'PENDING' AND decision_log_id IS NULL
                    """,
                    (decision_id, proposal_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ProposalValidationError(
                    f"proposal {proposal_id} 审计附加被数据库拒绝"
                ) from exc
            if updated.rowcount != 1:
                raise ProposalValidationError(
                    f"proposal {proposal_id} 审计附加 CAS 失败"
                )
            record = self.get(str(row["project_id"]), proposal_id)
            if record is None:
                raise RuntimeError(f"proposal 附审计后不可读：{proposal_id}")
            return record

    def unaudited(self, project_id: str) -> list[ProposalRecord]:
        """List terminal proposals whose post-commit decision attachment is missing."""
        with _read_snapshot(self._conn):
            rows = self._conn.execute(
                """
                SELECT id FROM proposal_set
                WHERE project_id = ? AND status <> 'PENDING' AND decision_log_id IS NULL
                ORDER BY resolved_at, id
                """,
                (project_id,),
            ).fetchall()
            records: list[ProposalRecord] = []
            for row in rows:
                record = self.get(project_id, str(row[0]))
                if record is None:
                    raise RuntimeError(f"unaudited proposal 不可读：{row[0]}")
                records.append(record)
            return records

    # ══════════════════════════════════════════════════════════════════════
    # Passive confirmation receipts (durable outbox rows, kind='provisional_confirm')
    # ══════════════════════════════════════════════════════════════════════

    def _read_receipt(self, receipt_id: str) -> ConfirmationReceipt | None:
        row = self._conn.execute(
            """
            SELECT id, project_id, kind, confirmation_fact_kind, confirmation_request_hash,
                   base_canon_version, resolved_canon_version, decision_log_id,
                   audit_envelope_json, items_json
            FROM proposal_set WHERE id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None or row["kind"] != "provisional_confirm":
            return None
        event_ids = [
            str(link[0])
            for link in self._conn.execute(
                "SELECT event_id FROM proposal_event WHERE proposal_id = ? ORDER BY rowid",
                (receipt_id,),
            ).fetchall()
        ]
        edge_ids = [
            str(link[0])
            for link in self._conn.execute(
                "SELECT edge_id FROM proposal_edge WHERE proposal_id = ? ORDER BY rowid",
                (receipt_id,),
            ).fetchall()
        ]
        return ConfirmationReceipt(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            fact_kind=str(row["confirmation_fact_kind"]),
            request_hash=str(row["confirmation_request_hash"]),
            base_canon_version=int(row["base_canon_version"]),
            resolved_canon_version=int(row["resolved_canon_version"]),
            decision_log_id=(
                None if row["decision_log_id"] is None else str(row["decision_log_id"])
            ),
            event_ids=tuple(event_ids),
            edge_ids=tuple(edge_ids),
            envelope=json.loads(row["audit_envelope_json"]),
            items=json.loads(row["items_json"]),
        )

    def confirmation_receipt(
        self,
        project_id: str,
        *,
        fact_kind: str,
        request_hash: str,
    ) -> ConfirmationReceipt | None:
        """Idempotency lookup for an exact confirmation request."""
        if fact_kind not in {"event", "edge"}:
            raise ProposalValidationError(f"confirmation fact_kind 不合法：{fact_kind}")
        with _read_snapshot(self._conn):
            row = self._conn.execute(
                """
                SELECT id FROM proposal_set
                WHERE project_id = ? AND kind = 'provisional_confirm'
                  AND confirmation_fact_kind = ? AND confirmation_request_hash = ?
                """,
                (project_id, fact_kind, request_hash),
            ).fetchone()
            if row is None:
                return None
            return self._read_receipt(str(row["id"]))

    def confirmation_conflicts(
        self,
        project_id: str,
        *,
        fact_kind: str,
        fact_ids: Sequence[str],
    ) -> tuple[str, ...]:
        """Which requested PROVISIONAL facts already belong to a durable receipt."""
        if fact_kind not in {"event", "edge"}:
            raise ProposalValidationError(f"confirmation fact_kind 不合法：{fact_kind}")
        wanted = tuple(sorted(set(fact_ids)))
        if not wanted:
            return ()
        markers = ", ".join("?" for _ in wanted)
        with _read_snapshot(self._conn):
            if fact_kind == "event":
                rows = self._conn.execute(
                    f"""
                    SELECT link.event_id
                    FROM proposal_event AS link
                    JOIN proposal_set AS owner ON owner.id = link.proposal_id
                    WHERE owner.project_id = ? AND owner.kind = 'provisional_confirm'
                      AND link.event_id IN ({markers})
                    """,
                    (project_id, *wanted),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"""
                    SELECT link.edge_id
                    FROM proposal_edge AS link
                    JOIN proposal_set AS owner ON owner.id = link.proposal_id
                    WHERE owner.project_id = ? AND owner.kind = 'provisional_confirm'
                      AND link.edge_id IN ({markers})
                    """,
                    (project_id, *wanted),
                ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def create_confirmation_receipt(
        self,
        *,
        receipt_id: str,
        project_id: str,
        fact_kind: str,
        request_hash: str,
        base_canon_version: int,
        resolved_canon_version: int,
        items: list[Any],
        event_ids: Sequence[str],
        edge_ids: Sequence[str],
        envelope: dict[str, Any],
        summary: str,
        chapter_number: int | None,
    ) -> ConfirmationReceipt:
        """Insert the terminal outbox row plus PROVISIONAL fact links in one transaction."""
        if fact_kind not in {"event", "edge"}:
            raise ProposalValidationError(f"confirmation fact_kind 不合法：{fact_kind}")
        if resolved_canon_version != base_canon_version + 1:
            raise ProposalValidationError(
                "confirmation receipt 必须对应一次 one-version Canon bump"
            )
        try:
            items_json = strict_json_dumps(items)
            envelope_json = strict_json_dumps(envelope)
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise ProposalValidationError(
                f"confirmation receipt 不是 strict JSON：{exc}"
            ) from exc
        with _transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO proposal_set (
                    id, project_id, kind, summary, item_count, items_json, confidence,
                    status, chapter_number, snapshot_id, base_canon_version,
                    schema_version, prompt_hash, resolution_action, resolved_canon_version,
                    audit_envelope_json, confirmation_fact_kind, confirmation_request_hash
                ) VALUES (
                    ?, ?, 'provisional_confirm', ?, ?, ?, NULL, 'ACCEPTED', ?, NULL,
                    ?, NULL, NULL, 'accept', ?, ?, ?, ?
                )
                """,
                (
                    receipt_id,
                    project_id,
                    summary,
                    len(items),
                    items_json,
                    chapter_number,
                    base_canon_version,
                    resolved_canon_version,
                    envelope_json,
                    fact_kind,
                    request_hash,
                ),
            )
            for event_id in event_ids:
                self._conn.execute(
                    """
                    INSERT INTO proposal_event (proposal_id, project_id, event_id)
                    VALUES (?, ?, ?)
                    """,
                    (receipt_id, project_id, event_id),
                )
            for edge_id in edge_ids:
                self._conn.execute(
                    """
                    INSERT INTO proposal_edge (proposal_id, project_id, edge_id)
                    VALUES (?, ?, ?)
                    """,
                    (receipt_id, project_id, edge_id),
                )
            created = self._read_receipt(receipt_id)
            if created is None:
                raise RuntimeError(f"confirmation receipt 插入后不可读：{receipt_id}")
            return created
