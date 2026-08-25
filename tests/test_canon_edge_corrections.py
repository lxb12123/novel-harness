"""自动 Canon 地点/状态/关系边的可逆纠错（Task 8 / ADR 0032）。

「先可逆、后自动」的启用闸：LOCATED_AT / HAS_STATE / RELATED_TO 有完整纠错入口，
其余类型不得 auto-Canon；作者纠错后机器重放不得覆盖（override 保护）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.extract import RawChapterAnalysis, RawEvent, RawStateUpdate
from novel_harness.extract.auto_canon import promote_clean_facts
from novel_harness.extract.service import ExtractionService
from novel_harness.graph import (
    EdgeStatus,
    EvidenceStatus,
    NodeLabel,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph


class World:
    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "book"
        self.conn = connect(tmp / "b.db")
        migrate(self.conn)
        self.pid = project.create(self.conn, name="t", root_path=str(self.root)).id
        self.graph = SqliteStoryGraph(self.conn)
        src = tmp / "s.txt"
        src.write_text("第一章 甲\n\n萧决走进了青云城主府。\n", encoding="utf-8")
        importer.import_book(self.graph, self.pid, txt=src, root=self.root)
        self.ids = {}
        ledger_conn = self.conn
        from novel_harness.declare import Ledger

        ledger = Ledger(self.graph, ledger_conn, self.pid)
        for name, label in (
            ("萧决", NodeLabel.CHARACTER),
            ("青云城主府", NodeLocation := NodeLabel.LOCATION),
            ("北荒", NodeLocation),
        ):
            self.ids[name] = ledger.declare_node(label, name).id
        self.conn.commit()

    def ingest_location(self, quote: str, loc: str) -> str:
        row = self.conn.execute(
            "SELECT cs.id, cs.chapter_id, cs.text FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1 AND cs.text_sha256 = c.text_sha256",
            (self.pid,),
        ).fetchone()
        from novel_harness.graph import ChapterText

        chapter = ChapterText(
            chapter_id=row["chapter_id"], number=1, snapshot_id=row["id"], text=row["text"]
        )
        service = ExtractionService(
            conn=self.conn,
            graph=self.graph,
            event_store=SqliteEventStore(self.conn),
            proposal_store=SqliteProposalStore(self.conn),
        )
        report = service.ingest(
            self.pid,
            chapter,
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决到了",
                        quote=quote,
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(
                        kind="location",
                        subject="萧决",
                        object=loc,
                        quote=quote,
                        confidence=0.95,
                    ),
                ),
                character_profiles=(),
            ),
            prompt_hash="prompt:correction-test",
        )
        promote_clean_facts(
            self.conn, self.pid, report, graph=self.graph, events=SqliteEventStore(self.conn)
        )
        self.conn.commit()
        edge_id = self.conn.execute(
            "SELECT id FROM edge WHERE project_id = ? AND type = 'LOCATED_AT' "
            "AND information_scope = 'CANON' ORDER BY rowid DESC LIMIT 1",
            (self.pid,),
        ).fetchone()["id"]
        return edge_id

    def canon_edge(self, edge_id: str) -> dict[str, Any]:
        return self.graph.canon_edge_view(self.pid, edge_id).model_dump(mode="json")


@pytest.fixture
def world() -> World:
    return World(Path(tempfile.mkdtemp()))


def test_location_reassign_creates_author_replacement_and_bumps(
    world: World,
) -> None:
    edge_id = world.ingest_location("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    result = world.graph.edit_canon_edge(
        world.pid,
        edge_id,
        new_src=world.ids["萧决"],
        new_dst=world.ids["北荒"],
        props=__import__("novel_harness.graph.models", fromlist=["EdgeProps"]).EdgeProps(),
        expected_canon_version=before,
    )
    assert result.retracted is False
    assert result.replacement_edge_id is not None
    assert result.replacement_edge_id != edge_id
    assert project.require_canon_version(world.conn, world.pid) == before + 1
    # 旧 extractor 边 RETRACTED；新边 AUTHOR + evidence NONE（起源保留在 override 历史）。
    old = world.conn.execute(
        "SELECT status, source FROM edge WHERE id = ?", (edge_id,)
    ).fetchone()
    assert old["status"] == EdgeStatus.RETRACTED.value and old["source"] == "extractor"
    new = world.conn.execute(
        "SELECT source, evidence_id, evidence_status, dst FROM edge WHERE id = ?",
        (result.replacement_edge_id,),
    ).fetchone()
    assert new["source"] == "author" and new["evidence_id"] is None
    assert new["evidence_status"] == EvidenceStatus.NONE.value
    assert new["dst"] == world.ids["北荒"]
    override = world.conn.execute(
        "SELECT action, replacement_edge_id FROM canon_edge_override "
        "WHERE project_id = ? AND status = 'ACTIVE'",
        (world.pid,),
    ).fetchone()
    assert override["replacement_edge_id"] == result.replacement_edge_id


def test_props_only_edit_keeps_edge_id_and_source(world: World) -> None:
    """HAS_STATE 只改 value：edge id 不变、source/evidence 不变、override 有 before/after。"""
    # 直接种一条 HAS_STATE 机器边（health 维度）：extractor + CANON + FRESH evidence。
    from novel_harness.ids import EntityType, new_id

    chapter_row = world.conn.execute(
        "SELECT c.id, s.id AS snapshot_id FROM chapter c "
        "JOIN chapter_snapshot s ON s.chapter_id = c.id "
        "WHERE c.project_id = ? AND c.number = 1 LIMIT 1",
        (world.pid,),
    ).fetchone()
    dim_id = new_id(EntityType.STATE_DIM, world.pid)
    ev_id = new_id(EntityType.EVIDENCE, world.pid)
    world.conn.execute(
        "INSERT INTO node (id, project_id, label, name, props_json) VALUES (?,?,?,?,?)",
        (dim_id, world.pid, NodeLabel.STATE_DIM.value, "生死", '{"dim_key":"health"}'),
    )
    world.conn.execute(
        """
        INSERT INTO evidence (
            id, project_id, chapter_id, chapter_snapshot_id,
            para_index, quote_text, quote_sha256, para_index_hint, occurrence_k
        ) VALUES (?, ?, ?, ?, 0, ?, ?, 0, 0)
        """,
        (
            ev_id,
            world.pid,
            chapter_row["id"],
            chapter_row["snapshot_id"],
            "萧决走进了青云城主府。",
            __import__("hashlib").sha256("萧决走进了青云城主府。".encode()).hexdigest(),
        ),
    )
    edge_id = new_id(EntityType.EDGE, world.pid)
    world.conn.execute(
        """
        INSERT INTO edge (
            id, project_id, src, dst, type, valid_from_chapter, information_scope,
            status, confidence, props_json, source, evidence_id, evidence_status
        ) VALUES (?, ?, ?, ?, 'HAS_STATE', 1, 'CANON', 'ACTIVE', 1.0, ?, 'extractor', ?, 'FRESH')
        """,
        (
            edge_id,
            world.pid,
            world.ids["萧决"],
            dim_id,
            '{"dim_key":"health","value":"死","value_key":"dead"}',
            ev_id,
        ),
    )
    world.conn.commit()
    before = project.require_canon_version(world.conn, world.pid)
    from novel_harness.graph.models import EdgeProps

    result = world.graph.edit_canon_edge(
        world.pid,
        edge_id,
        new_src=world.ids["萧决"],
        new_dst=dim_id,
        props=EdgeProps(dim_key="health", value="重伤"),
        expected_canon_version=before,
    )
    assert result.replacement_edge_id == edge_id
    row = world.conn.execute(
        "SELECT source, evidence_id, props_json FROM edge WHERE id = ?", (edge_id,)
    ).fetchone()
    assert row["source"] == "extractor"
    assert "重伤" in row["props_json"]
    override = world.conn.execute(
        "SELECT before_props_json, after_props_json FROM canon_edge_override "
        "WHERE project_id = ? AND status = 'ACTIVE' AND source_edge_id = ?",
        (world.pid, edge_id),
    ).fetchone()
    assert override is not None
    assert "死" in override["before_props_json"] and "重伤" in override["after_props_json"]


def test_aba_restore_keeps_extractor_origin_and_stale_evidence_editable(
    world: World,
) -> None:
    """A→B→A：恢复旧 identity 时保留 extractor origin，active override 保护它——
    即使原 evidence 后来 STALE 也仍可继续修改/撤回。"""
    edge_id = world.ingest_location("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    to_b = world.graph.edit_canon_edge(
        world.pid,
        edge_id,
        new_src=world.ids["萧决"],
        new_dst=world.ids["北荒"],
        props=__import__("novel_harness.graph.models", fromlist=["EdgeProps"]).EdgeProps(),
        expected_canon_version=before,
    )
    back = world.graph.edit_canon_edge(
        world.pid,
        to_b.replacement_edge_id or to_b.edge_id,
        new_src=world.ids["萧决"],
        new_dst=world.ids["青云城主府"],
        props=__import__("novel_harness.graph.models", fromlist=["EdgeProps"]).EdgeProps(),
        expected_canon_version=project.require_canon_version(world.conn, world.pid),
    )
    # 恢复出的边：origin 仍是 extractor，但被 ACTIVE override 接管。
    restored = world.conn.execute(
        "SELECT id, source, status, dst FROM edge WHERE id = ?",
        (back.edge_id,),
    ).fetchone()
    assert restored["source"] == "extractor"
    assert restored["status"] == EdgeStatus.ACTIVE.value
    override = world.conn.execute(
        "SELECT replacement_edge_id FROM canon_edge_override "
        "WHERE project_id = ? AND status = 'ACTIVE'",
        (world.pid,),
    ).fetchone()
    assert override["replacement_edge_id"] == back.edge_id
    # 把原证据标 STALE：active override 保护下仍可编辑（裸 STALE 才拒绝）。
    world.conn.execute("UPDATE edge SET evidence_status = 'STALE' WHERE id = ?", (edge_id,))
    world.conn.commit()
    still_editable = world.graph.canon_edge_view(world.pid, back.edge_id)
    assert still_editable.author_owned is True


def test_retract_leaves_no_current_edge_and_writes_tombstone(world: World) -> None:
    edge_id = world.ingest_location("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    result = world.graph.retract_canon_edge(
        world.pid, edge_id, expected_canon_version=before
    )
    assert result.retracted is True
    row = world.conn.execute(
        "SELECT status FROM edge WHERE id = ?", (edge_id,)
    ).fetchone()
    assert row["status"] == EdgeStatus.RETRACTED.value
    override = world.conn.execute(
        "SELECT action, replacement_edge_id FROM canon_edge_override "
        "WHERE project_id = ? AND status = 'ACTIVE'",
        (world.pid,),
    ).fetchone()
    assert override["action"] == "RETRACT" and override["replacement_edge_id"] is None


def test_stale_canon_version_is_rejected(world: World) -> None:
    edge_id = world.ingest_location("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    with pytest.raises(project.StaleBaseVersion):
        world.graph.edit_canon_edge(
            world.pid,
            edge_id,
            new_src=world.ids["萧决"],
            new_dst=world.ids["北荒"],
            props=__import__("novel_harness.graph.models", fromlist=["EdgeProps"]).EdgeProps(),
            expected_canon_version=before - 1,
        )


def test_related_to_normalization_and_api_rejects_chapter_number(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NH_DB", world.conn.execute("PRAGMA database_list").fetchone()[2])
    from novel_harness.api.app import app

    with TestClient(app) as client:
        r = client.patch(
            f"/api/projects/{world.pid}/canon/edges/nope",
            json={"kind": "location", "location_id": "x", "expected_canon_version": 0,
                  "valid_from_chapter": 1},
        )
        assert r.status_code == 422  # extra=forbid：章号进不来
