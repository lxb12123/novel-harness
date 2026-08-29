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

    def ingest_death(self, quote: str) -> str:
        """同 `ingest_location`，但种一条真实的抽取器 HAS_STATE(health) 边。

        走这条而不是手写 SQL/`EdgeProps`：生产四处写 HAS_STATE 边都不传 `dim_key`
        （2026-08-27 挖出的坑，见 `EdgeProps.dim_key` 的说明），手写夹具会悄悄放过
        「这份 props 是不是生产真的会写出来的形状」这件事。
        """
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
                        summary="萧决死了",
                        quote=quote,
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(kind="death", subject="萧决", quote=quote, confidence=0.95),
                ),
                character_profiles=(),
            ),
            prompt_hash="prompt:correction-test-death",
        )
        promote_clean_facts(
            self.conn, self.pid, report, graph=self.graph, events=SqliteEventStore(self.conn)
        )
        self.conn.commit()
        edge_id = self.conn.execute(
            "SELECT id FROM edge WHERE project_id = ? AND type = 'HAS_STATE' "
            "AND information_scope = 'CANON' ORDER BY rowid DESC LIMIT 1",
            (self.pid,),
        ).fetchone()["id"]
        return edge_id

    def ingest_state(self, quote: str, dimension: str, value: str) -> str:
        """同 `ingest_death`，但走**自由维度**分支（`kind="state"`，2026-08-27 裁定）：
        维度认不出就建、不配机器键。真实生产路径下 `EdgeProps.dim_key` 恒 `None`，
        `edge.dst` 才是这条事实「关于哪个维度」的唯一身份——手写会放过这一点。
        """
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
                        summary="萧决的境界变了",
                        quote=quote,
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(
                        kind="state",
                        subject="萧决",
                        dimension=dimension,
                        value=value,
                        quote=quote,
                        confidence=0.95,
                    ),
                ),
                character_profiles=(),
            ),
            prompt_hash="prompt:correction-test-state",
        )
        promote_clean_facts(
            self.conn, self.pid, report, graph=self.graph, events=SqliteEventStore(self.conn)
        )
        self.conn.commit()
        edge_id = self.conn.execute(
            "SELECT id FROM edge WHERE project_id = ? AND type = 'HAS_STATE' "
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


def test_canon_edge_view_survives_a_real_state_edge(world: World) -> None:
    """`GET .../canon/edges/{id}`（作者点开状态事实编辑器时走的那条）不许崩。

    2026-08-27 挖出的坑：`EdgeProps` 只声明了 `value`/`value_key`，`dim_key` 一直是
    `extra="allow"` 的隐式额外字段，而生产四处写 HAS_STATE 边（抽取器 `state`/`death`、
    `declare.py::declare_dead`）都不传它——`canon_edge_slot_key()` 那句
    `edge.props.dim_key or ""` 对任何一条真实边都是 `AttributeError`，也就是说**今天
    只要作者点开任何一条状态事实的编辑器就 500**。这条测试种一条最普通的真实死亡边
    （不手写 SQL、不手写 `EdgeProps`），只做「打开它」这一件事——它就是最小复现。
    """
    edge_id = world.ingest_death("萧决走进了青云城主府。")
    view = world.canon_edge(edge_id)
    assert view["props"]["dim_key"] is None
    assert view["slot_key"]


def test_props_only_edit_keeps_edge_id_and_source(world: World) -> None:
    """HAS_STATE 只改 value：edge id 不变、source/evidence 不变、override 有 before/after。

    边种在真实生产路径上（`world.ingest_death` → 抽取器 `kind="death"`），不再手写
    `props_json`——手写会放过「这份 props 是不是生产真的会写出来的形状」这件事：
    2026-08-27 挖出，生产四处写 HAS_STATE 边全部不传 `dim_key`，而这份夹具原来手写
    塞了它，绕开了真实形状，让「打开任何一条真实状态事实的编辑器就 500」这件事在这份
    测试绿着的情况下活了下来（见 `EdgeProps.dim_key` 的说明和新增的
    `test_canon_edge_view_survives_a_real_state_edge`）。
    """
    edge_id = world.ingest_death("萧决走进了青云城主府。")
    dim_id = world.conn.execute(
        "SELECT dst FROM edge WHERE id = ?", (edge_id,)
    ).fetchone()["dst"]
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


def test_state_dims_lists_health_keyed_and_free_dimensions_keyless(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/canon/state-dims` 给「维度」下拉框用（Task 8 补记）：health 带真机器键，
    自由维度不带——**这条边界不是这个端点新画的**，它照抄 `NodeProps.dim_key`
    （2026-08-27 裁定）的既有事实，这里只是把它变成一份 HTTP 可读的列表。
    """
    world.ingest_death("萧决走进了青云城主府。")
    world.ingest_state("萧决走进了青云城主府。", "武功境界", "炼气期")
    monkeypatch.setenv("NH_DB", world.conn.execute("PRAGMA database_list").fetchone()[2])
    from novel_harness.api.app import app

    with TestClient(app) as client:
        r = client.get(f"/api/projects/{world.pid}/canon/state-dims")
        assert r.status_code == 200, r.text
        dims = {d["name"]: d["dim_key"] for d in r.json()}
    assert dims.get("生死") == "health"
    assert "武功境界" in dims
    assert dims["武功境界"] is None


def test_editing_a_free_dimension_fact_retargets_dst_without_fabricating_dim_key(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作者打开一条「武功境界」的事实去编辑（真实场景：只是想改错别的值）。

    维度那一格重新选回同一个维度，保存后：**目标节点没变、`dim_key` 没被
    误写成任何字符串**——这正是这一批要堵的洞（此前维度下拉框硬编码只有
    health/location 两项，选中即会把 `dim_key` 误改成 "health"）。

    第二段验证反过来：真的切换到 health 节点，`dim_key` 应该照那个节点的
    真身份变成 "health"，证明这不是「永远焊死在 None」的另一种坏。
    """
    edge_id = world.ingest_state("萧决走进了青云城主府。", "武功境界", "炼气期")
    original_dst = world.conn.execute(
        "SELECT dst FROM edge WHERE id = ?", (edge_id,)
    ).fetchone()["dst"]
    from novel_harness.graph.queries import find_state_dim

    assert not find_state_dim(world.conn, world.pid, "health")  # 还没有任何 death 事件

    monkeypatch.setenv("NH_DB", world.conn.execute("PRAGMA database_list").fetchone()[2])
    from novel_harness.api.app import app

    with TestClient(app) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(f"{base}/canon/edges/{edge_id}").json()
        # 重新选回同一个维度（自由维度自己就是自己在 state-dims 列表里的那一条）。
        same_dim = client.patch(
            f"{base}/canon/edges/{edge_id}",
            json={
                "kind": "state",
                "dim_node_id": original_dst,
                "value": "炼气期",
                "expected_canon_version": current["canon_version"],
            },
        )
        assert same_dim.status_code == 200, same_dim.text
        view = same_dim.json()["view"]
        assert view["dst"] == original_dst
        assert view["props"]["dim_key"] is None

        # 现在真的种一条 death，让 health 维度节点存在，再把这条边切过去。
        world.ingest_death("萧决走进了青云城主府。")
        health_id = world.conn.execute(
            "SELECT dst FROM edge WHERE project_id = ? AND type = 'HAS_STATE' "
            "AND json_extract(props_json, '$.value_key') = 'dead'",
            (world.pid,),
        ).fetchone()["dst"]
        current = client.get(f"{base}/canon/edges/{edge_id}").json()
        switched = client.patch(
            f"{base}/canon/edges/{edge_id}",
            json={
                "kind": "state",
                "dim_node_id": health_id,
                "value": "炼气期",
                "expected_canon_version": current["canon_version"],
            },
        )
        assert switched.status_code == 200, switched.text
        view = switched.json()["view"]
        assert view["dst"] == health_id
        assert view["props"]["dim_key"] == "health"


def test_dim_node_id_must_be_a_real_state_dim_node(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dim_node_id` 不是随便一个 id 都能糊弄过去——它必须真的是个 StateDim 节点，
    否则 422。防的是前端一个笔误就把一条状态事实的 `dst` 指到一个人物节点上。
    """
    edge_id = world.ingest_state("萧决走进了青云城主府。", "武功境界", "炼气期")
    monkeypatch.setenv("NH_DB", world.conn.execute("PRAGMA database_list").fetchone()[2])
    from novel_harness.api.app import app

    with TestClient(app) as client:
        base = f"/api/projects/{world.pid}"
        current = client.get(f"{base}/canon/edges/{edge_id}").json()
        r = client.patch(
            f"{base}/canon/edges/{edge_id}",
            json={
                "kind": "state",
                "dim_node_id": world.ids["萧决"],  # 一个真实但不是 StateDim 的节点
                "value": "炼气期",
                "expected_canon_version": current["canon_version"],
            },
        )
        assert r.status_code == 422
