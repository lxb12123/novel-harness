"""改过的章重新分析一遍 —— **旧那一版的事实要退休，不是跟新的并排活着**。

── 这条缝长什么样（2026-08-14 实测出来的）────────────────────────────────

作者在别的软件里把第 1 章从「萧决走进了青云城主府」改成「萧决走进了北荒雪原」，
后台整理按新快照重新分析一遍。修之前实测：

    story_event   2 → 4      一模一样两组，纯重复
    edge          同一个人在第 1 章同时 ACTIVE 在两个地点

**R4 会报一条正文里根本不存在的位置冲突**，而作者对着稿子完全看不懂系统在说什么。
这是「看起来完全正常的假页面」在图层的形态：每一条边单看都合法。

根因：`STALE` 这一档从 `001_init.sql` 起就写在 `TEMPORAL_WHERE` 里、
`location_conflict.py` 也照着它写了「STALE 立刻停火」，**而生产上一个写入方都没有**
（`importer.py` 模块头当年写的是「那是 M4」）。

── 判据 ──────────────────────────────────────────────────────────────────

只断言**面板看见什么**，不断言中间任何一层——那是作者唯一会看的东西，也是这条缝
真正的受害者。库里那几行状态另有一条断言，但它是**辅助**：面板对了而库里乱着，
下一次查询照样会翻车。

**外加两条**，少一条这份测试就会在错的地方绿：

1. **作者手动加的那条一个字都不许动。** 退休它等于系统吃掉了作者的决定——他会发现
   自己刚补的一条认知在改了个错别字之后消失了，而没有任何地方告诉他为什么。
2. **没改过的章一条都不许退休。** 幂等：`sync` 整本跑一遍时每一章都会调到它。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.extract import RawChapterAnalysis, RawEvent, RawStateUpdate
from novel_harness.extract.auto_canon import promote_clean_facts
from novel_harness.extract.service import ExtractionService
from novel_harness.graph import ChapterText, EdgeType, InformationScope, NodeLabel
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

BEFORE = "第一章 甲\n\n萧决走进了青云城主府。\n"
AFTER = "第一章 甲\n\n萧决走进了北荒雪原。\n"


class World:
    """一本一章的书 + 花名册。**`ingest` 模拟一次后台整理**（不调模型）。"""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "book"
        self.conn = connect(tmp / "b.db")
        migrate(self.conn)
        self.pid = project.create(self.conn, name="t", root_path=str(self.root)).id
        self.graph = SqliteStoryGraph(self.conn)
        src = tmp / "s.txt"
        src.write_text(BEFORE, encoding="utf-8")
        importer.import_book(self.graph, self.pid, txt=src, root=self.root)
        ledger = Ledger(self.graph, self.conn, self.pid)
        self.ids = {
            name: ledger.declare_node(label, name).id
            for label, name in (
                (NodeLabel.CHARACTER, "萧决"),
                (NodeLabel.LOCATION, "青云城主府"),
                (NodeLabel.LOCATION, "北荒雪原"),
            )
        }
        self.conn.commit()

    def ingest(self, quote: str, loc: str) -> None:
        row = self.conn.execute(
            "SELECT cs.id, cs.chapter_id, cs.text FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1 AND cs.text_sha256 = c.text_sha256",
            (self.pid,),
        ).fetchone()
        chapter = ChapterText(
            chapter_id=row["chapter_id"], number=1, snapshot_id=row["id"], text=row["text"]
        )
        service = ExtractionService(
            conn=self.conn,
            graph=self.graph,
            event_store=SqliteEventStore(self.conn),
            proposal_store=SqliteProposalStore(self.conn),
        )
        analysis = RawChapterAnalysis(
            events=(
                RawEvent(
                    summary="萧决到了",
                    quote=quote,
                    participants=("萧决",),
                    knowers=("萧决",),
                    revealed_facts=("到了",),
                    confidence=0.9,
                ),
            ),
            state_updates=(
                RawStateUpdate(
                    kind="location", subject="萧决", object=loc, quote=quote, confidence=0.9
                ),
            ),
            character_profiles=(),
        )
        report = service.ingest(self.pid, chapter, analysis, prompt_hash="p1")
        self.conn.commit()
        promote_clean_facts(
            self.conn, self.pid, report, graph=self.graph, events=SqliteEventStore(self.conn)
        )

    def rewrite(self, text: str) -> None:
        """作者在别的软件里改了这一章，然后系统把它读回来（= 第一层那一下）。"""
        (self.root / "chapters" / "0001.md").write_text(text, encoding="utf-8")
        importer.sync_chapter(self.graph, self.pid, self.root, 1)
        self.conn.commit()

    def places_on_screen(self) -> list[str]:
        """**面板在第 1 章看到的在场地点。** 走时态过滤，所以 STALE 的不在里面。"""
        state = self.graph.state_at(
            self.pid, self.ids["萧决"], 1, scope=InformationScope.CANON
        )
        back = {v: k for k, v in self.ids.items()}
        return [back.get(e.dst, e.dst) for e in state.edges if e.type is EdgeType.LOCATED_AT]

    def fresh_events(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM story_event WHERE project_id = ? AND evidence_status = 'FRESH'",
            (self.pid,),
        ).fetchone()[0]


@pytest.fixture
def world() -> World:
    return World(Path(tempfile.mkdtemp()))


def test_the_panel_shows_only_where_he_actually_is(world: World) -> None:
    """**这份文件的头等大事。** 改完重新分析之后，他只在一个地方。"""
    world.ingest("萧决走进了青云城主府。", "青云城主府")
    assert world.places_on_screen() == ["青云城主府"]

    world.rewrite(AFTER)
    world.ingest("萧决走进了北荒雪原。", "北荒雪原")

    assert world.places_on_screen() == ["北荒雪原"], (
        "旧那一版的位置还活着 —— R4 会报一条正文里根本不存在的冲突"
    )
    # 库里那一半：旧事件退休、新事件在。面板对了而库里乱着，下一次查询照样翻车。
    assert world.fresh_events() == 2, "重新分析之后新鲜事件不止新那一组 —— 旧的没退休"


def test_what_the_author_added_by_hand_is_never_retired(world: World) -> None:
    """**作者手动加的那条一个字都不许动。**

    退休它等于系统吃掉了作者的决定：他会发现自己刚补的一条在改了个错别字之后
    消失了，而没有任何地方告诉他为什么。判据是 `source`，不是「锚在哪一版」——
    作者那条锚在**同一个**旧快照上，按快照一刀切会把它一起带走。
    """
    ledger = Ledger(world.graph, world.conn, world.pid)
    ledger.declare_where(who="萧决", loc="青云城主府", quote="萧决走进了青云城主府。")
    world.conn.commit()

    world.rewrite(AFTER)

    mine = world.conn.execute(
        "SELECT evidence_status FROM edge WHERE project_id = ? AND source = 'author'",
        (world.pid,),
    ).fetchall()
    assert mine, "这条测试在空转 —— 作者那条边没建出来"
    assert all(r["evidence_status"] == "FRESH" for r in mine), "系统退休了作者自己加的事实"


def test_a_chapter_that_did_not_change_retires_nothing(world: World) -> None:
    """**幂等。** 整本 `sync` 会对每一章都调到它，没改过的一条都不许动。

    少了这条，一个「只要 sync 就全标 STALE」的实现照样能让上面那条绿——
    而它会在作者按一次保存之后把整本书的事实全部熄灭。
    """
    world.ingest("萧决走进了青云城主府。", "青云城主府")
    before = world.fresh_events()

    importer.sync(world.graph, world.pid, world.root)
    world.conn.commit()

    assert world.fresh_events() == before
    assert world.places_on_screen() == ["青云城主府"]
