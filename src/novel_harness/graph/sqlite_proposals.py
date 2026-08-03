"""SQLite implementation of the proposal-cluster repository contract."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json

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
from .sqlite_store import _transaction


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
        items_json = json.dumps(
            proposal.items,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
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
                   base_canon_version, schema_version, prompt_hash
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
        )

    def mark_resolved(
        self,
        proposal_id: str,
        resolution: ProposalResolutionMark,
    ) -> ProposalRecord:
        with _transaction(self._conn):
            row = self._conn.execute(
                "SELECT project_id, status FROM proposal_set WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(f"proposal 不存在：{proposal_id}")
            if row["status"] != "PENDING":
                raise ProposalAlreadyResolved(
                    f"proposal {proposal_id} 已是 {row['status']}，不能再次处理"
                )
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
            self._conn.execute(
                """
                UPDATE proposal_set
                SET status = ?,
                    resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    decision_log_id = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (resolution.status.value, resolution.decision_log_id, proposal_id),
            )
            record = self.get(str(row["project_id"]), proposal_id)
            if record is None:
                raise RuntimeError(f"resolved proposal 更新后不可读：{proposal_id}")
            return record
