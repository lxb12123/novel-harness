"""抽取别名 identity-first（Task 12 / ADR 0031）。

钉住：明确别名自动入档 + 证据、同人既有别名复用、歧义进提案不自动、低置信
不自动、身份未决事实不 clean。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.extract import (
    ExtractedAlias,
    RawChapterAnalysis,
    RawEvent,
)
from novel_harness.extract.service import ExtractionService
from novel_harness.graph import ChapterText, NodeLabel
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

QUOTE = "凤辣子站在堂前，一眼就认出了王熙凤。"
TEXT = "第一章 甲\n\n" + QUOTE + "\n"


class World:
    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "book"
        self.conn = connect(tmp / "b.db")
        migrate(self.conn)
        self.pid = project.create(self.conn, name="抽取别名", root_path=str(self.root)).id
        self.graph = SqliteStoryGraph(self.conn)
        ledger = Ledger(self.graph, self.conn, self.pid)
        self.hero = ledger.declare_node(NodeLabel.CHARACTER, "王熙凤").id
        self.other = ledger.declare_node(NodeLabel.CHARACTER, "贾蓉").id
        src = tmp / "s.txt"
        src.write_text(TEXT, encoding="utf-8")
        importer.import_book(self.graph, self.pid, txt=src, root=self.root)
        self.conn.commit()

    def add_alias(self, *, to: str, surface: str) -> None:
        from novel_harness.ids import EntityType, new_id

        self.conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind, usable_for_rules) "
            "VALUES (?,?,?,?, 'alias', 1)",
            (new_id(EntityType.ALIAS, self.pid), self.pid, to, surface),
        )
        self.conn.commit()

    def chapter(self) -> ChapterText:
        row = self.conn.execute(
            "SELECT cs.id, cs.chapter_id, cs.text FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1 AND cs.text_sha256 = c.text_sha256",
            (self.pid,),
        ).fetchone()
        return ChapterText(
            chapter_id=row["chapter_id"], number=1, snapshot_id=row["id"], text=row["text"]
        )

    def ingest(self, aliases=(), *, confidence: float = 0.95):
        service = ExtractionService(
            conn=self.conn,
            graph=self.graph,
            event_store=SqliteEventStore(self.conn),
            proposal_store=SqliteProposalStore(self.conn),
        )
        report = service.ingest(
            self.pid,
            self.chapter(),
            RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="王熙凤认出自己被人认出。",
                        quote=QUOTE,
                        participants=("凤辣子", "王熙凤"),
                        knowers=("凤辣子",),
                        confidence=confidence,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
                aliases=aliases,
            ),
            prompt_hash="p1",
        )
        self.conn.commit()
        return report


@pytest.fixture
def world() -> World:
    return World(Path(tempfile.mkdtemp()))


def test_clear_alias_is_registered_automatically_with_evidence(world: World) -> None:
    report = world.ingest(
        aliases=(
            ExtractedAlias(
                surface="凤辣子", character_surface="王熙凤", quote=QUOTE, confidence=0.95
            ),
        )
    )
    row = world.conn.execute(
        "SELECT id, node_id, source, status FROM alias WHERE surface = '凤辣子'",
    ).fetchone()
    assert row is not None
    assert row["source"] == "extractor" and row["status"] == "ACTIVE"
    assert row["node_id"] == world.hero
    # 证据已挂上（FRESH + 绑定快照）。
    ev = world.conn.execute(
        "SELECT evidence_id, status FROM alias_evidence WHERE alias_id = ?", (row["id"],)
    ).fetchone()
    assert ev is not None and ev["status"] == "FRESH"
    # 事件现在能解析（凤辣子 = 王熙凤），不再是 new_character bucket。
    assert report.clean_event_ids


def test_ambiguous_alias_goes_to_proposal_not_auto(world: World) -> None:
    world.add_alias(to=world.hero, surface="凤哥")
    world.add_alias(to=world.other, surface="凤哥")
    world.ingest(
        aliases=(
            ExtractedAlias(
                surface="凤哥", character_surface="王熙凤", quote=QUOTE, confidence=0.95
            ),
        )
    )
    assert (
        world.conn.execute(
            "SELECT COUNT(*) FROM proposal_set WHERE kind = 'alias_resolution'"
        ).fetchone()[0]
        >= 1
    ), "歧义称呼应进 alias_resolution 提案，不自动选择"
    assert (
        world.conn.execute(
            "SELECT COUNT(*) FROM alias WHERE surface = '凤哥' AND source = 'extractor'"
        ).fetchone()[0]
        == 0
    ), "歧义 alias 不许自动落库"


def test_existing_alias_is_reused_not_recreated(world: World) -> None:
    world.add_alias(to=world.hero, surface="凤辣子")
    before = len(
        world.conn.execute("SELECT id FROM alias WHERE surface = '凤辣子'").fetchall()
    )
    report = world.ingest(
        aliases=(
            ExtractedAlias(
                surface="凤辣子", character_surface="王熙凤", quote=QUOTE, confidence=0.95
            ),
        )
    )
    after = len(
        world.conn.execute("SELECT id FROM alias WHERE surface = '凤辣子'").fetchall()
    )
    assert after == before, "既有别名不该被重复登记"
    assert report.clean_event_ids


def test_below_confidence_alias_is_blocked(world: World) -> None:
    world.ingest(
        aliases=(
            ExtractedAlias(
                surface="凤辣子", character_surface="王熙凤", quote=QUOTE, confidence=0.5
            ),
        )
    )
    assert (
        world.conn.execute(
            "SELECT COUNT(*) FROM alias WHERE surface = '凤辣子'"
        ).fetchone()[0]
        == 0
    ), "低置信别名不许自动落库"


def test_identity_unresolved_facts_stay_quarantined(world: World) -> None:
    """身份未决（歧义称呼）时，含它的整条事件保持隔离，不判 clean。

    事件 participants 里直接出现的是歧义 surface「凤哥」（不是本名）：它解析不到
    唯一人物 → 整条事件进 low_confidence bucket 隔离，不自动升 Canon。
    """
    world.add_alias(to=world.hero, surface="凤哥")
    world.add_alias(to=world.other, surface="凤哥")
    service = ExtractionService(
        conn=world.conn,
        graph=world.graph,
        event_store=SqliteEventStore(world.conn),
        proposal_store=SqliteProposalStore(world.conn),
    )
    report = service.ingest(
        world.pid,
        world.chapter(),
        RawChapterAnalysis(
            events=(
                RawEvent(
                    summary="凤哥与他人对峙。",
                    quote=QUOTE,
                    participants=("凤哥",),
                    knowers=("凤哥",),
                    confidence=0.95,
                ),
            ),
            state_updates=(),
            character_profiles=(),
            aliases=(
                ExtractedAlias(
                    surface="凤哥", character_surface="王熙凤", quote=QUOTE, confidence=0.95
                ),
            ),
        ),
        prompt_hash="p1",
    )
    world.conn.commit()
    assert report.clean_event_ids == (), "身份未决的整条相关事实保持隔离（不变量 12）"
