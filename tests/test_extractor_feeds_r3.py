"""**抽取器一个人就能让 R3 开火**——作者一个字都不用敲。

── 这个文件为什么存在 ────────────────────────────────────────────────────

`tests/test_rules_fire.py` 证明的是「作者按得到的那几个口子能让 R2/R3 开火」，
它每一个字都走 `POST /declare/death` / `POST /declare/first-appearance`。

那是 2026-08-13 补的洞。但它补出来的是一条**手工**路径，而 2026-08-14 作者定了规则：

> 右边那栏里边的东西由 llm 在相应的时期去进行无感生成……你现在很多东西都是需要靠
> 用户手动抽取之类的，这个太烂了。

于是这一条问的是另一个问题：**换成模型来说「他死了」，R3 还开得了火吗。**

判据必须是端到端的，理由和 `checks/__init__.py` 开头那段警告一模一样——
R2/R3 在 2026-08-02 到 08-13 之间每天绿着、生产上结构性哑火，而当时**每一层单看
都是对的**：规则对、`is_dead` 对、`state_at` 对，断的是「没有任何东西写 `value_key`」。
所以这里不断言中间任何一层，只断言两头：

    模型吐出 kind="death"   →   第 4 章 check 报出「死人说话」

── 三条一起钉住，少一条这份测试就会在错的地方绿 ──────────────────────────

1. **先跑一次「模型什么都没说」的检查，断言它是哑的。** 没有这一步，下面那条
   「报出来了」证明不了任何东西——它可能一直在报。
2. **`value_key` 是引擎写的常量，不是模型那段字。** 模型在 `kind` 这个封闭枚举里挑
   一个，`HealthValue.DEAD` 由 `service.py` 填死——这是铁律 2 在这条路上的落点，
   `_the_engine_never_reads_the_models_wording` 钉它。
3. **`StateDim` 由引擎自己建。** 抽取那条路只会 `resolve_ids`（要求节点**已存在**），
   而生死维度不在花名册里（`CANONICAL_ALIAS_LABELS` 有意排除它）。不自己建的话
   这条 death 会被当成「称呼解析不到」静默丢掉——**丢掉的形态和成功的形态在
   check 的出参上长得一模一样**，都是「本章没问题」。
"""

from __future__ import annotations

from collections.abc import Iterator
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
    ChapterText,
    EdgeType,
    HealthValue,
    InformationScope,
    NodeLabel,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# 萧决第 2 章死，第 4 章还挂着对话标签 —— R3 的教科书形态。
# 第 1 章那句「萧决拾级而上」**不该报**：他那时还活着（闭开区间 [2, ∞)）。
BOOK = (
    "第一章 山门\n"
    "\n"
    "萧决拾级而上，山门在雾里。\n"
    "\n"
    "第二章 落幕\n"
    "\n"
    "剑光落下，萧决再没有起来。\n"
    "\n"
    "第三章 新人\n"
    "\n"
    "山门里换了新的守夜人。\n"
    "\n"
    "第四章 回声\n"
    "\n"
    "萧决道：「我还在。」\n"
)

DEATH_QUOTE = "剑光落下，萧决再没有起来。"


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    """只建库 + 导正文。**一个节点、一条边都不在这里造。**"""
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="山门记", root_path=str(root)).id
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK, encoding="utf-8")
    importer.import_book(SqliteStoryGraph(conn), pid, txt=txt, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _analysis() -> RawChapterAnalysis:
    """模型看完第 2 章之后**吐出来的那份 JSON**。

    `events` 至少要一条（`RawChapterAnalysis` 的 `min_length=1`），所以这里给一条
    和死亡同源的。**`death` 那条只有 subject + quote**：object / dimension / value
    三样都是引擎自己的常量，给模型填的机会就是给它编一个别的键的机会。
    """
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="萧决在剑光下倒地不起。",
                quote=DEATH_QUOTE,
                participants=("萧决",),
                knowers=("萧决",),
                confidence=0.95,
            ),
        ),
        state_updates=(
            RawStateUpdate(kind="death", subject="萧决", quote=DEATH_QUOTE, confidence=0.95),
        ),
        character_profiles=(),
    )


def _run_extraction(db: str, pid: str) -> None:
    """把那份分析按**生产顺序**落库：ingest（一个事务）→ 自动升 CANON（另一个事务）。

    两次事务不是偷懒：`proposal_confirm._transaction` 明写「必须在无外层事务的连接上
    启动」，而 `ingest()` 全程跑在自己的事务里（`auto_canon.py` 的 docstring）。
    """
    conn = connect(Path(db))
    try:
        graph = SqliteStoryGraph(conn)
        row = conn.execute(
            "SELECT cs.id, cs.chapter_id, cs.text FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 2",
            (pid,),
        ).fetchone()
        chapter = ChapterText(
            chapter_id=row["chapter_id"], number=2, snapshot_id=row["id"], text=row["text"]
        )
        service = ExtractionService(
            conn=conn,
            graph=graph,
            event_store=SqliteEventStore(conn),
            proposal_store=SqliteProposalStore(conn),
        )
        report = service.ingest(pid, chapter, _analysis(), prompt_hash="test-death")
        conn.commit()
        promote_clean_facts(conn, pid, report, graph=graph, events=SqliteEventStore(conn))
    finally:
        conn.close()


def _issues(client: TestClient, pid: str, chapter: int) -> list[dict[str, Any]]:
    r = client.post(f"/api/projects/{pid}/chapters/{chapter}/check", json={})
    assert r.status_code == 200, r.text
    return r.json()["issues"]


def _add_character(client: TestClient, pid: str, name: str) -> None:
    """花名册里得有他 —— 抽取的第二道闸是「称呼解析到唯一一个人」。

    **这一步今天仍然是手工的，而且是对的**：`new_character` 提案（模型发现了一个
    花名册里没有的人）走的是另一条路，那条路的终点也是这里。
    """
    r = client.post(f"/api/projects/{pid}/nodes", json={"label": NodeLabel.CHARACTER.value, "name": name})
    assert r.status_code == 200, r.text


def test_without_the_model_saying_anything_r3_is_mute(
    client: TestClient, book: dict[str, str]
) -> None:
    """**基线。** 光有花名册和正文，第 4 章那句「萧决道：」不该报。

    没有这一条，下面那条「报出来了」证明不了任何东西。
    """
    _add_character(client, book["pid"], "萧决")
    assert _issues(client, book["pid"], 4) == []


def test_the_model_saying_he_died_is_enough_to_make_r3_fire(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这份文件的头等大事**：作者一个字都没敲，check 报出了「死人说话」。"""
    _add_character(client, book["pid"], "萧决")
    _run_extraction(book["db"], book["pid"])

    issues = _issues(client, book["pid"], 4)
    assert [i["rule"] for i in issues] == ["R3"], issues
    assert issues[0]["issue_type"] == "DEAD_SPEAKS"
    # 锚是三元组，永远不是 offset（ADR 0006）。R3 锚在**称呼**上（说话动词只是
    # 它开火的位置判据，不进锚）——工作台点这条 issue 时按 quote 重寻的就是这个串。
    assert issues[0]["chapter"] == 4
    assert issues[0]["anchor"]["quote_text"] == "萧决"
    assert set(issues[0]["anchor"]) >= {"para_index", "quote_text", "occurrence_k"}

    # 第 1 章他还活着 —— 闭开区间 [2, ∞) 的可见产品行为。**少了这一条，一个把
    # 时态过滤整个丢掉的实现照样全绿**：它对每一章都答「死了」。
    assert _issues(client, book["pid"], 1) == []


def test_the_engine_never_reads_the_models_wording(client: TestClient, book: dict[str, str]) -> None:
    """落进库里的是**机器键**，不是模型写的那段字。

    R3 的判据是 `EdgeProps.value_key == HealthValue.DEAD`，而「死 / 陨落 / 坐化 /
    兵解」怎么写都不该影响它（ADR 0005 铁律 2）。模型在 `kind` 这个**封闭枚举**里挑
    一个，键由引擎填死——语义判断留在模型那一侧，引擎这一侧仍然只做集合判断。
    """
    _add_character(client, book["pid"], "萧决")
    _run_extraction(book["db"], book["pid"])

    conn = connect(Path(book["db"]))
    try:
        graph = SqliteStoryGraph(conn)
        hero = graph.resolve(book["pid"], ["萧决"])[0].unique_node
        assert hero is not None
        state = graph.state_at(book["pid"], hero.id, 4, scope=InformationScope.CANON)
        health = [e for e in state.edges if e.type is EdgeType.HAS_STATE]
        assert len(health) == 1, health
        assert health[0].props.value_key == HealthValue.DEAD
        assert state.is_dead

        # **生死维度是引擎自己建的。** 抽取那条路只会 `resolve_ids`（要求节点已存在），
        # 而它不在花名册里——不自己建，这条 death 会被当成「解析不到」静默丢掉。
        dims = conn.execute(
            "SELECT COUNT(*) FROM node WHERE project_id = ? AND label = ?",
            (book["pid"], NodeLabel.STATE_DIM.value),
        ).fetchone()[0]
        assert dims == 1
    finally:
        conn.close()
