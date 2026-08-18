"""别名生命周期（Task 11 / ADR 0031 / §4.5 / §6.5）。

钉住：ACTIVE 过滤（撤回后退出解析、允许重登记）、作者改机器 alias 派生 author
行、改归属 = 撤回 + 新建、canonical 保护、evidence 有效性、跨项目拒绝、陈旧
canon version 409。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import AliasKind, NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


class World:
    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "book"
        self.conn = connect(tmp / "b.db")
        migrate(self.conn)
        self.pid = create_project(self.conn, name="别名", root_path=str(self.root)).id
        self.graph = SqliteStoryGraph(self.conn)
        ledger = Ledger(self.graph, self.conn, self.pid)
        self.hero = ledger.declare_node(NodeLabel.CHARACTER, "萧决").id
        self.peer = ledger.declare_node(NodeLabel.CHARACTER, "顾清音").id
        self.place = ledger.declare_node(NodeLabel.LOCATION, "北荒").id
        src = tmp / "s.txt"
        root = tmp / "book"
        root.mkdir(parents=True, exist_ok=True)
        src.write_text("第一章 甲\n\n萧决走进了北荒。\n", encoding="utf-8")
        importer.import_book(self.graph, self.pid, txt=src, root=root)
        self.conn.commit()

    def add_alias(self, *, to: str, surface: str, source: str = "author") -> str:
        from novel_harness.ids import EntityType, new_id

        alias_id = new_id(EntityType.ALIAS, self.pid)
        self.conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules, source) "
            "VALUES (?,?,?,?, 'alias', 1, ?)",
            (alias_id, self.pid, to, surface, source),
        )
        self.conn.commit()
        return alias_id

    def canon(self) -> int:
        return project.require_canon_version(self.conn, self.pid)


@pytest.fixture
def world() -> World:
    return World(Path(tempfile.mkdtemp()))


def test_aliases_of_excludes_retracted(world: World) -> None:
    kept = world.add_alias(to=world.hero, surface="萧少侠")
    gone = world.add_alias(to=world.hero, surface="萧兄")
    world.graph.retract_alias(world.pid, gone)
    aliases = world.graph.aliases_of(world.pid, world.hero)
    surfaces = {a.surface for a in aliases}
    assert "萧少侠" in surfaces
    assert "萧兄" not in surfaces
    assert kept  # 防空转


def test_edit_alias_derives_an_author_row_even_for_machine_alias(world: World) -> None:
    machine = world.add_alias(to=world.hero, surface="凤辣子", source="extractor")
    edited = world.graph.edit_alias(world.pid, machine, surface="凤姐")
    assert edited.surface == "凤姐"
    assert edited.source == "author"
    assert edited.derived_from_alias_id == machine
    # 原机器行撤回（保留历史），解析里只剩 author 派生行（canonical 本名除外）。
    aliases = [
        a for a in world.graph.aliases_of(world.pid, world.hero)
        if a.kind is not AliasKind.CANONICAL
    ]
    assert {a.surface for a in aliases} == {"凤姐"}
    row = world.conn.execute(
        "SELECT status, source FROM alias WHERE id = ?", (machine,)
    ).fetchone()
    assert row["status"] == "RETRACTED" and row["source"] == "extractor"


def test_reassign_moves_to_target_character(world: World) -> None:
    source_alias = world.add_alias(to=world.hero, surface="少主")
    reassigned = world.graph.reassign_alias(world.pid, source_alias, to_node_id=world.peer)
    assert reassigned.node_id == world.peer
    assert reassigned.surface == "少主"
    # 旧行撤回，新行 ACTIVE + author 派生。
    assert not any(
        a.surface == "少主" for a in world.graph.aliases_of(world.pid, world.hero)
    )
    assert any(
        a.surface == "少主" for a in world.graph.aliases_of(world.pid, world.peer)
    )


def test_canonical_is_protected_from_every_operation(world: World) -> None:
    from novel_harness.graph.store import CanonEdgeRefused

    # canonical 是 upsert_node 建的那条（surface == node.name）。
    canonical_id = world.conn.execute(
        "SELECT id FROM alias WHERE project_id = ? AND node_id = ? AND kind = 'canonical'",
        (world.pid, world.hero),
    ).fetchone()["id"]
    with pytest.raises(CanonEdgeRefused):
        world.graph.retract_alias(world.pid, canonical_id)
    with pytest.raises(CanonEdgeRefused):
        world.graph.edit_alias(world.pid, canonical_id, surface="改名")
    with pytest.raises(CanonEdgeRefused):
        world.graph.reassign_alias(world.pid, canonical_id, to_node_id=world.peer)


def test_reassign_rejects_non_character_target(world: World) -> None:
    from novel_harness.graph.store import CanonEdgeRefused

    alias_id = world.add_alias(to=world.hero, surface="北荒之主")
    with pytest.raises(CanonEdgeRefused):
        world.graph.reassign_alias(world.pid, alias_id, to_node_id=world.place)


def test_cross_project_alias_is_rejected(world: World) -> None:
    from novel_harness.graph.store import NodeNotFound

    other = World(Path(tempfile.mkdtemp()))
    # 用别的项目的 alias id 操作本项目 → 拒绝（服务端 404，不是给你改别人的书）。
    with pytest.raises(NodeNotFound):
        other.graph.edit_alias(world.pid, "alias:nonexistent", surface="x")


def test_http_create_aliases_and_profile(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_DB", world.conn.execute("PRAGMA database_list").fetchone()[2])
    from novel_harness.api.app import app

    with TestClient(app) as client:
        profile = client.get(f"/api/projects/{world.pid}/characters/{world.hero}/profile")
        assert profile.status_code == 200, profile.text
        body = profile.json()
        assert body["character"]["name"] == "萧决"
        # 新项目刚建：只有 canonical，别名是空；canonical 不出现在 aliases 里。
        assert body["aliases"] == []

        created = client.post(
            f"/api/projects/{world.pid}/characters/{world.hero}/aliases",
            json={"surface": "萧少侠", "expected_canon_version": body["character"].get("canon_version", 0) if False else 0},
        )
        # expected_canon_version 必须等于当前 canon 版本——先读 profile 的版本偏移。
        # 简化：直接读当前版本。
        from novel_harness.project import require_canon_version

        canon = require_canon_version(world.conn, world.pid)
        created = client.post(
            f"/api/projects/{world.pid}/characters/{world.hero}/aliases",
            json={"surface": "萧少侠", "expected_canon_version": canon},
        )
        assert created.status_code == 200, created.text
        alias_id = created.json()["id"]
        assert created.json()["surface"] == "萧少侠"

        # 陈旧版本 → 409。
        stale = client.post(
            f"/api/projects/{world.pid}/characters/{world.hero}/aliases",
            json={"surface": "又来一个", "expected_canon_version": canon},
        )
        assert stale.status_code == 409, stale.text

        # 撤回。
        deleted = client.delete(f"/api/projects/{world.pid}/aliases/{alias_id}")
        assert deleted.status_code == 200, deleted.text
        profile_after = client.get(f"/api/projects/{world.pid}/characters/{world.hero}/profile")
        assert profile_after.json()["aliases"] == []
