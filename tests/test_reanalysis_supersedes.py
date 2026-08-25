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
from novel_harness.graph import (
    ChapterSpec,
    ChapterText,
    EdgeType,
    InformationScope,
    NodeLabel,
)
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


def test_saving_new_text_retires_extractor_canon_and_bumps_canon_version_once(
    world: World,
) -> None:
    """保存路径（`commit_chapter_snapshot`）退休 Writer 可见 CANON 时，
    同一事务只 bump 一次 canon version；作者拿旧 expected 改事实必须 409。"""
    world.ingest("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    # 保存前那条 extractor 边是 CANON + FRESH（自动升 Canon，ADR 0020）。
    row = world.conn.execute(
        "SELECT information_scope, evidence_status FROM edge "
        "WHERE project_id = ? AND source = 'extractor' AND information_scope = 'CANON'",
        (world.pid,),
    ).fetchone()
    assert row["information_scope"] == "CANON" and row["evidence_status"] == "FRESH"

    # 作者保存新正文（走产品保存路径，不是 sync）。
    importer.save_chapter(
        world.graph,
        world.pid,
        world.root,
        1,
        AFTER,
        expected_sha256=importer.text_digest(BEFORE),
    )
    world.conn.commit()

    retired = world.conn.execute(
        "SELECT evidence_status FROM edge "
        "WHERE project_id = ? AND source = 'extractor' AND information_scope = 'CANON'",
        (world.pid,),
    ).fetchone()
    assert retired["evidence_status"] == "STALE"
    assert project.require_canon_version(world.conn, world.pid) == before + 1

    # 作者拿着旧 canon version 改事实 → CAS 失败（HTTP 层就是 409）。
    with pytest.raises(project.StaleBaseVersion):
        project.compare_and_bump_canon_version(world.conn, world.pid, before)


def test_same_hash_save_does_not_bump_canon_version(world: World) -> None:
    world.ingest("萧决走进了青云城主府。", "青云城主府")
    before = project.require_canon_version(world.conn, world.pid)
    importer.save_chapter(
        world.graph,
        world.pid,
        world.root,
        1,
        BEFORE,
        expected_sha256=importer.text_digest(BEFORE),
    )
    world.conn.commit()
    assert project.require_canon_version(world.conn, world.pid) == before


def test_aba_restore_gets_a_new_generation_and_a_fresh_run(seed_provider=None) -> None:
    """S1(g1)→S2(g2)→S1(g3)：第三次的 S1 必须拥有自己的 generation + run。

    021 / Task 9 的核心：g1 的晚到 run 虽然 snapshot/hash 与当前再次相同，
    也必须因 generation 不同而 SUPERSEDED；g3 的 enqueue 必须拿到一条以
    g3 basis 冻结的新 run（content-addressed 复用只发生在同 basis 内）。
    """
    from test_extract_runner import QUOTE as RUNNER_QUOTE  # noqa: F401  (API 形状探针)

    tmp = tempfile.mkdtemp()
    root = Path(tmp) / "book"
    conn = connect(Path(tmp) / "b.db")
    migrate(conn)
    pid = project.create(conn, name="t", root_path=str(root)).id
    graph = SqliteStoryGraph(conn)
    src = Path(tmp) / "s.txt"
    src.write_text(BEFORE, encoding="utf-8")
    importer.import_book(graph, pid, txt=src, root=root)
    conn.commit()

    from novel_harness.extract.runner import ExtractionRunner
    from novel_harness.draft.provider import CompletionResult

    calls: list[str] = []

    def analyzer(request):
        calls.append(request.chapter.snapshot_id)
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决到了青云城主府。",
                        quote="萧决走进了青云城主府。",
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(
                    RawStateUpdate(
                        kind="location",
                        subject="萧决",
                        object="青云城主府",
                        quote="萧决走进了青云城主府。",
                        confidence=0.95,
                    ),
                ),
                character_profiles=(),
            ).model_dump_json(),
            model="extractor-test-model",
            finish_reason="stop",
        )

    runner = ExtractionRunner(lambda: connect(Path(tmp) / "b.db"), analyzer)

    # g1：第一轮 S1，run 成功。
    g1_run = runner.enqueue(pid, 1)
    assert g1_run.source_generation == 1
    assert runner.run(g1_run.id).status.value == "SUCCEEDED"

    # g2：保存 S2（换正文）。
    importer.save_chapter(
        graph, pid, root, 1, AFTER, expected_sha256=importer.text_digest(BEFORE)
    )
    conn.commit()

    # g3：从 S2 还原历史 S1 —— 判为「变化」，generation 必须 +1。
    importer.save_chapter(
        graph, pid, root, 1, BEFORE, expected_sha256=importer.text_digest(AFTER)
    )
    conn.commit()
    current = graph.current_snapshots(pid)[0]
    assert current.text == BEFORE
    assert graph.current_chapter_generation(pid, 1) == 3, (
        "S1→S2→S1 的第三次 S1 必须拿到新 generation（ABA 防护）"
    )

    # g3 的 enqueue：同一内容，但 basis 不同 → 新 run（g3 basis）。
    g3_run = runner.enqueue(pid, 1)
    assert g3_run.id != g1_run.id
    assert g3_run.source_generation == 3

    # g1 的晚到 run 在 g3 已经 current 之后重新跑：因 generation 不同而 SUPERSEDED。
    g1_again = runner.run(g1_run.id)
    assert g1_again.status.value == "SUPERSEDED", (
        "g1 的晚到结果借 snapshot/hash 等值复活 = ABA"
    )

    # g3 自己的 run 正常成功。
    assert runner.run(g3_run.id).status.value == "SUCCEEDED"


def test_the_save_transaction_hands_back_the_facts_that_lost_their_support(
    world: World,
) -> None:
    """那份「失去依据」的清单**算完不许扔**（2026-08-23）。

    退休这一下每次保存都在算，而且算得很精确：退了哪几条边、哪几条事件、其中几条
    是 Writer 可见的 CANON。**从前它只喂了一次 canon bump 就被丢在
    `commit_chapter_snapshot` 里**——于是这句话说不出来：

        「你刚改的这一段，原本支撑着 N 条已确认的事实，它们现在失去了依据。」

    它确定性、纯查库、不花一分钱，答的正是改老章最容易出事的那一问。现在它挂在
    token 上（ADR 0029 已经把 token 定成保存后所有自动任务的唯一输入）。
    **这条红了 = 那份清单又被扔了**，而不是「退休本身坏了」——退休对不对由这份
    文件里的另外几条钉着。
    """
    world.ingest("萧决走进了青云城主府。", "青云城主府")
    live = {
        table: {
            str(row["id"])
            for row in world.conn.execute(
                f"SELECT id FROM {table} WHERE project_id = ? "  # noqa: S608 —— 表名是字面量
                "AND source = 'extractor' AND evidence_status = 'FRESH'",
                (world.pid,),
            )
        }
        for table in ("edge", "story_event")
    }
    assert live["edge"] and live["story_event"], "这条测试在空转 —— 抽取出来的事实没建成"

    token = world.graph.commit_chapter_snapshot(
        ChapterSpec(
            project_id=world.pid,
            number=1,
            heading="第一章 甲",
            path=importer.chapter_path(1),
            text=AFTER,
        ),
        expected_text_sha256=importer.text_digest(BEFORE),
    )
    world.conn.commit()

    assert token.retirement is not None, "保存回来的 token 上没有那份清单 —— 又被扔了"
    assert set(token.retirement.retired_edge_ids) == live["edge"]
    assert set(token.retirement.retired_event_ids) == live["story_event"]
    assert token.retirement.effective_canon_changed, (
        "退掉的是 Writer 可见的 CANON，清单却说没碰到 —— 那句话会报 0 条"
    )

    # 同一份正文再存一次：清单是**空的**，不是 None。空账和「这条 token 不是从
    # 保存事务来的」是两件事，混起来的话「没有东西失去依据」会被当成「不知道」。
    again = world.graph.commit_chapter_snapshot(
        ChapterSpec(
            project_id=world.pid,
            number=1,
            heading="第一章 甲",
            path=importer.chapter_path(1),
            text=AFTER,
        ),
        expected_text_sha256=importer.text_digest(AFTER),
    )
    world.conn.commit()
    assert again.retirement is not None
    assert again.retirement.retired_edge_ids == ()
    assert again.retirement.retired_event_ids == ()


def test_the_list_reaches_the_run_row_instead_of_dying_in_the_receipt(
    world: World,
) -> None:
    """那份清单要一路走到**库里那三列**，不是只挂在 token 上（2026-08-23）。

    上一条钉的是「算完没被扔」；这一条钉的是**它真的到了终点**。
    `chapter_refresh_run` 的三列 JSON 是 018 迁移就留好的位子，注释写着
    「Task 3 起由 `commit_chapter_snapshot` 写」——而实测到 2026-08-23 为止
    **一直全空**：账算出来了、挂在 token 上了，就是没人往下递。

    中间那一段是 `importer.save_chapter` → 回执 → `api.app._trigger_refresh`
    → `ensure_refresh_coverage` → `create_run`。**这条红了 = 那一段又断了**，
    而不是「退休本身坏了」（那个由这份文件里的另外几条钉着）。

    顺带钉住那份账**不上线**：回执就是 `PUT …/text` 的出参，而这份清单带的是
    内部图 ID，作者的浏览器永远不该看见。
    """
    import json

    from novel_harness.api.app import _trigger_refresh
    from novel_harness.chapter_refresh import find_run

    world.ingest("萧决走进了青云城主府。", "青云城主府")
    live = {
        table: {
            str(row["id"])
            for row in world.conn.execute(
                f"SELECT id FROM {table} WHERE project_id = ? "  # noqa: S608 —— 表名是字面量
                "AND source = 'extractor' AND evidence_status = 'FRESH'",
                (world.pid,),
            )
        }
        for table in ("edge", "story_event")
    }
    assert live["edge"] and live["story_event"], "这条测试在空转 —— 抽取出来的事实没建成"

    receipt = importer.save_chapter(
        world.graph,
        world.pid,
        world.root,
        1,
        AFTER,
        expected_sha256=importer.text_digest(BEFORE),
    )
    world.conn.commit()
    assert receipt.retirement is not None, "回执把 token 上那份账丢了"

    # 出参里不许有它：多一个会序列化的字段就是改前端契约。
    assert "retirement" not in receipt.model_dump()

    _trigger_refresh(world.conn, world.graph, world.pid, 1, receipt)
    world.conn.commit()

    chapter_id = next(
        ct.chapter_id for ct in world.graph.current_snapshots(world.pid) if ct.number == 1
    )
    generation = world.graph.current_chapter_generation(world.pid, 1) or 1
    run = find_run(world.conn, world.pid, chapter_id, generation)
    assert run is not None, "保存之后连 run 行都没有 —— 断在更前面"

    # `find_run` 只取窄投影，那三列不在里面 —— 直接问那一行。
    stored = world.conn.execute(
        "SELECT retired_edge_ids_json, retired_event_ids_json, "
        "retired_knower_event_ids_json FROM chapter_refresh_run WHERE id = ?",
        (run["id"],),
    ).fetchone()
    assert set(json.loads(stored["retired_edge_ids_json"])) == live["edge"], (
        "run 行上那三列还是空的 —— 清单又死在回执里了"
    )
    assert set(json.loads(stored["retired_event_ids_json"])) == live["story_event"]
    assert json.loads(stored["retired_knower_event_ids_json"]) == list(
        receipt.retirement.retired_knower_event_ids
    )
