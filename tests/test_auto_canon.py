"""自动升 CANON（进度板 1.2）：干净的直接生效，例外照旧进队列，每一步留 `actor='system'`。

这条路是 2026-08-10 那次裁决的后半段：「**无需留人工可以留记录**」。前半段（作者事后改得掉）
是 `corrections.py`，已在 1.1 落地——**顺序不能倒**，否则会有一段时间是「系统自动改你的书，
而你改不回来」。

本文件钉五件事，外加一道自守卫：
  ① 没进 bucket 的事实自己变 CANON；
  ② 那条 `decision_log` 的 actor 是 `system`；
  ③ 进了 bucket 的仍是 PROVISIONAL 且仍在队列里；
  ④ 重跑不多升一次、也不多写一行日志；
  ⑤ 作者亲手确认那条路写出来的仍然是 `author`（别把 actor 全局翻了）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from novel_harness import decisions, project
from novel_harness.db import Connection, connect, migrate
from novel_harness.decisions import DEFAULT_ACTOR, SYSTEM_ACTOR, DecisionKind
from novel_harness.draft.provider import CompletionResult
from novel_harness.extract import (
    RawChapterAnalysis,
    RawEvent,
    RawStateUpdate,
)
from novel_harness.extract.auto_canon import promote_clean_facts
from novel_harness.extract.ingest_helpers import ExtractionReport
from novel_harness.extract.proposals import confirm_provisional_events
from novel_harness.extract.runner import ExtractionRunner, ExtractionRunStatus
from novel_harness.extract.service import ExtractionService
from novel_harness.graph import (
    AliasSpec,
    ChapterSpec,
    ChapterText,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


CLEAN_EVENT_QUOTE = "顾清音在渡口把玄铁令交给萧决。"
LOW_EVENT_QUOTE = "萧决独自在夜色里想起了顾清音说过的那句话。"
CLEAN_STATE_QUOTE = "顾清音的修为终于突破到了金丹境界。"
CONFLICT_LOCATION_QUOTE = "萧决看见顾清音仍然停留在渡口。"

CHAPTER_TEXT = "\n".join(
    (
        CLEAN_EVENT_QUOTE,
        LOW_EVENT_QUOTE,
        CLEAN_STATE_QUOTE,
        CONFLICT_LOCATION_QUOTE,
    )
) + "\n"

CLEAN_EVENT_SUMMARY = "顾清音交出玄铁令。"
LOW_EVENT_SUMMARY = "萧决独自回想那句话。"


@dataclass(frozen=True)
class Seed:
    project_id: str
    graph: SqliteStoryGraph
    chapter: ChapterText
    hero_id: str
    sidekick_id: str
    harbor_id: str
    mountain_id: str
    dimension_id: str
    secret_id: str


@pytest.fixture
def conn(tmp_path) -> Iterator[Connection]:
    connection = connect(tmp_path / "auto-canon.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def seed(conn: Connection) -> Seed:
    project_id = create_project(conn, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(conn)
    hero = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(gender="女", main_character=True),
        )
    )
    sidekick = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.CHARACTER, name="萧决")
    )
    harbor = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="渡口")
    )
    mountain = graph.upsert_node(
        NodeSpec(project_id=project_id, label=NodeLabel.LOCATION, name="北荒")
    )
    dimension = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.STATE_DIM,
            name="修为",
            props=NodeProps(dim_key="cultivation"),
        )
    )
    graph.add_alias(
        AliasSpec(project_id=project_id, node_id=dimension.id, surface="修为")
    )
    secret = graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.SECRET,
            name="玄铁令来历",
            secret=SecretDetail(),
        )
    )
    graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=7,
            heading="第七章 渡口",
            path="chapters/0007.md",
            text=CHAPTER_TEXT,
        )
    )
    # 冲突源：作者的 Canon 里她在北荒，而本章抽出「仍停留在渡口」。
    graph.upsert_edge(
        EdgeSpec(
            project_id=project_id,
            src=hero.id,
            dst=mountain.id,
            type=EdgeType.LOCATED_AT,
            valid_from_chapter=1,
            information_scope=InformationScope.CANON,
        )
    )
    return Seed(
        project_id=project_id,
        graph=graph,
        chapter=graph.current_snapshots(project_id)[0],
        hero_id=hero.id,
        sidekick_id=sidekick.id,
        harbor_id=harbor.id,
        mountain_id=mountain.id,
        dimension_id=dimension.id,
        secret_id=secret.id,
    )


def _mixed_analysis() -> RawChapterAnalysis:
    """一次抽取里同时有干净的和进了 bucket 的。"""
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary=CLEAN_EVENT_SUMMARY,
                quote=CLEAN_EVENT_QUOTE,
                participants=("顾清音", "萧决"),
                knowers=("顾清音",),
                revealed_facts=("玄铁令来历",),
                confidence=0.91,
            ),
            RawEvent(
                summary=LOW_EVENT_SUMMARY,
                quote=LOW_EVENT_QUOTE,
                participants=("萧决", "顾清音"),
                knowers=("萧决",),
                revealed_facts=(),
                confidence=0.42,
            ),
        ),
        state_updates=(
            RawStateUpdate(
                kind="state",
                subject="顾清音",
                dimension="修为",
                value="金丹",
                quote=CLEAN_STATE_QUOTE,
                confidence=0.96,
            ),
            RawStateUpdate(
                kind="location",
                subject="顾清音",
                object="渡口",
                quote=CONFLICT_LOCATION_QUOTE,
                confidence=0.94,
            ),
        ),
        character_profiles=(),
    )


def _ingest(conn: Connection, seed: Seed, *, prompt_hash: str) -> ExtractionReport:
    return ExtractionService(
        conn=conn,
        graph=seed.graph,
        event_store=SqliteEventStore(conn),
        proposal_store=SqliteProposalStore(conn),
    ).ingest(seed.project_id, seed.chapter, _mixed_analysis(), prompt_hash=prompt_hash)


# ══════════════════════════════════════════════════════════════════════════
# 观察面：三个只读探针。**测试和自守卫共用它们**，否则自守卫验的是另一套断言。
# ══════════════════════════════════════════════════════════════════════════


def canon_event_summaries(conn: Connection, seed: Seed) -> set[str]:
    return {
        view.event.summary
        for view in SqliteEventStore(conn).events_for_chapter(
            seed.project_id, seed.chapter.number, InformationScope.CANON
        )
    }


def canon_state(conn: Connection, seed: Seed) -> tuple[str | None, list[str]]:
    """(她此刻在哪, Canon 上的状态值)。走 `state_at`，时态过滤只有一份实现。"""
    del conn
    snapshot = seed.graph.state_at(
        seed.project_id,
        seed.hero_id,
        seed.chapter.number,
        scope=InformationScope.CANON,
    )
    location = None if snapshot.location is None else snapshot.location.name
    return location, sorted(state.value or "" for state in snapshot.states)


def review_decisions(conn: Connection, seed: Seed) -> list[decisions.Decision]:
    return decisions.read(
        conn, seed.project_id, kind=DecisionKind.PROPOSAL_REVIEW
    )


def assert_only_the_clean_facts_went_canon(conn: Connection, seed: Seed) -> None:
    """① 干净的进了 Canon；③ 进 bucket 的一条都没进。"""
    assert canon_event_summaries(conn, seed) == {CLEAN_EVENT_SUMMARY}, (
        "低置信度那条事件不许自动升——它是作者唯一还愿意被打扰的三个地方之一"
    )
    assert canon_state(conn, seed) == ("北荒", ["金丹"]), (
        "与当前 Canon 冲突的位置更新不许自动生效；干净的状态更新必须生效"
    )


def assert_bucketed_facts_are_still_pending(
    conn: Connection, seed: Seed, report: ExtractionReport
) -> None:
    """③ 的另一半：它们仍是 PROVISIONAL，且仍挂在 PENDING 提案上。"""
    events = SqliteEventStore(conn)
    bucketed_event_ids = [
        event_id for event_id in report.event_ids if event_id not in report.clean_event_ids
    ]
    assert bucketed_event_ids, "这份 analysis 本来就该有一条进 bucket 的事件"
    for event_id in bucketed_event_ids:
        view = events.event(seed.project_id, event_id)
        assert view is not None
        assert view.event.information_scope is InformationScope.PROVISIONAL

    pending = SqliteProposalStore(conn).pending(seed.project_id)
    assert {proposal.kind for proposal in pending} == {
        "low_confidence_main",
        "edge_conflict",
    }
    queued_events = {event_id for proposal in pending for event_id in proposal.event_ids}
    queued_edges = {edge_id for proposal in pending for edge_id in proposal.edge_ids}
    assert queued_events == set(bucketed_event_ids)
    assert not queued_edges & set(report.clean_edge_ids)


def assert_every_review_decision_is_the_system(
    conn: Connection, seed: Seed, *, expected: int
) -> None:
    """② 日志里那些行的 actor 是 `system`，不是 `author`。"""
    logged = review_decisions(conn, seed)
    assert len(logged) == expected
    assert [decision.actor for decision in logged] == [SYSTEM_ACTOR] * expected
    assert all(
        decision.payload["actor"] == SYSTEM_ACTOR for decision in logged
    ), "actor 必须同时落在日志行和信封 payload 里——恢复路径只读得到后者"


# ══════════════════════════════════════════════════════════════════════════
# 守卫
# ══════════════════════════════════════════════════════════════════════════


def test_clean_facts_promote_themselves_and_bucketed_ones_stay_in_the_queue(
    conn: Connection, seed: Seed
) -> None:
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")

    assert len(report.event_ids) == 2 and len(report.edge_ids) == 2
    assert len(report.clean_event_ids) == 1 and len(report.clean_edge_ids) == 1
    assert canon_event_summaries(conn, seed) == set(), "落库那一刻还没有任何东西是 CANON"

    before = project.require_canon_version(conn, seed.project_id)
    result = promote_clean_facts(conn, seed.project_id, report)

    assert result.failures == ()
    assert result.promoted_event_ids == report.clean_event_ids
    assert result.promoted_edge_ids == report.clean_edge_ids
    assert result.canon_version == before + 2  # 事件一次 + 关系一次
    assert_only_the_clean_facts_went_canon(conn, seed)
    assert_bucketed_facts_are_still_pending(conn, seed, report)
    assert_every_review_decision_is_the_system(conn, seed, expected=2)


def test_promoting_twice_neither_promotes_twice_nor_logs_twice(
    conn: Connection, seed: Seed
) -> None:
    """④ 幂等。靠 `confirm_provisional_*` 的 `request_hash` 去重，不靠调用方记得别调两次。"""
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")
    first = promote_clean_facts(conn, seed.project_id, report)

    second = promote_clean_facts(conn, seed.project_id, report)

    assert second.failures == ()
    assert second.canon_version == first.canon_version, "第二次不许再 bump 一次 Canon 版本"
    assert second.decision_ids == first.decision_ids, "第二次归属的是同一批日志行"
    assert_only_the_clean_facts_went_canon(conn, seed)
    assert_every_review_decision_is_the_system(conn, seed, expected=2)


def test_the_author_path_still_logs_as_the_author(conn: Connection, seed: Seed) -> None:
    """⑤ 别把 actor 全局翻了：作者亲手确认写出来的仍然是 `author`。"""
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")

    confirmation = confirm_provisional_events(
        conn,
        seed.graph,
        SqliteEventStore(conn),
        seed.project_id,
        report.clean_event_ids,
        expected_canon_version=project.require_canon_version(conn, seed.project_id),
    )

    logged = review_decisions(conn, seed)
    assert [decision.id for decision in logged] == [confirmation.decision_id]
    assert logged[0].actor == DEFAULT_ACTOR
    assert logged[0].payload["actor"] == DEFAULT_ACTOR


def test_a_failed_promotion_leaves_the_facts_provisional_and_never_raises(
    conn: Connection, seed: Seed
) -> None:
    """fail-safe：升不上去的事实留在 PROVISIONAL，那是安全的一侧。"""
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")
    before = project.require_canon_version(conn, seed.project_id)
    broken = report.model_copy(
        update={"clean_event_ids": ("event:nope:nope", *report.clean_event_ids)}
    )

    result = promote_clean_facts(conn, seed.project_id, broken)

    assert result.promoted_event_ids == ()
    assert [failure.fact_kind for failure in result.failures] == ["event"]
    assert canon_event_summaries(conn, seed) == set()
    # 关系那一侧是独立的一组：事件组炸了不该把它也拖下水。
    assert result.promoted_edge_ids == report.clean_edge_ids
    assert result.canon_version == before + 1


def test_promotion_refuses_to_run_inside_an_outer_transaction(
    conn: Connection, seed: Seed
) -> None:
    """`proposal_confirm._transaction` 要求无外层事务——这里提前响，不留给它去发现。"""
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="无外层事务"):
            promote_clean_facts(conn, seed.project_id, report)
    finally:
        conn.rollback()


def test_a_successful_extraction_run_promotes_without_touching_its_status(
    tmp_path,
) -> None:
    """接线：`ExtractionRunner.run()` 跑完一轮，干净事件自己就 CANON 了。

    **run 的状态不受自动升影响**——正文已经抽完、模型钱已经花了，把 run 标红只会让作者
    再花一次（`auto_canon` 模块 docstring 的第 2 条）。
    """
    path = tmp_path / "auto-canon-runner.db"
    setup = connect(path)
    migrate(setup)
    project_id = create_project(setup, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(setup)
    graph.upsert_node(
        NodeSpec(
            project_id=project_id,
            label=NodeLabel.CHARACTER,
            name="顾清音",
            props=NodeProps(main_character=True),
        )
    )
    graph.put_chapter(
        ChapterSpec(
            project_id=project_id,
            number=3,
            heading="第三章 渡口",
            path="chapters/0003.md",
            text=CLEAN_EVENT_QUOTE + "\n",
        )
    )
    setup.close()

    analysis_json = RawChapterAnalysis(
        events=(
            RawEvent(
                summary=CLEAN_EVENT_SUMMARY,
                quote=CLEAN_EVENT_QUOTE,
                participants=("顾清音",),
                knowers=("顾清音",),
                revealed_facts=(),
                confidence=0.93,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()
    runner = ExtractionRunner(
        lambda: connect(path),
        lambda _request: CompletionResult(
            text=analysis_json,
            model="extractor-test-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        ),
    )
    run = runner.run(runner.enqueue(project_id, 3).id)

    assert run.status is ExtractionRunStatus.SUCCEEDED
    check = connect(path)
    try:
        canon = SqliteEventStore(check).events_for_chapter(
            project_id, 3, InformationScope.CANON
        )
        assert [view.event.summary for view in canon] == [CLEAN_EVENT_SUMMARY]
        logged = decisions.read(check, project_id, kind=DecisionKind.PROPOSAL_REVIEW)
        assert [decision.actor for decision in logged] == [SYSTEM_ACTOR]
    finally:
        check.close()


# ══════════════════════════════════════════════════════════════════════════
# 守卫的自守卫
# ══════════════════════════════════════════════════════════════════════════
#
# **一个永远绿的守卫比没有守卫更糟**，因为它还提供安全感。上面三条观察面全靠
# 「`events_for_chapter` / `state_at` / `decisions.read` 真的看得见变化」——它们
# 换了形状（或者 fixture 恰好没造出该进 bucket 的东西）的那天，断言会安静地全绿。
#
# 所以这里喂两个**真会漏的**假实现进同一批断言，要求它们红。


def _promote_everything(conn: Connection, seed: Seed, report: ExtractionReport):
    """坏实现一：不分干净与否，全升。这正是 1.2 最容易写成的那一种。"""
    return promote_clean_facts(
        conn,
        seed.project_id,
        report.model_copy(
            update={
                "clean_event_ids": report.event_ids,
                "clean_edge_ids": report.edge_ids,
            }
        ),
    )


def _promote_as_the_author(conn: Connection, seed: Seed, report: ExtractionReport):
    """坏实现二：升对了，但日志写成作者点的——日志页从此分不清谁改的。"""
    return confirm_provisional_events(
        conn,
        seed.graph,
        SqliteEventStore(conn),
        seed.project_id,
        report.clean_event_ids,
        expected_canon_version=project.require_canon_version(conn, seed.project_id),
    )


def test_the_guard_can_see_a_promotion_that_swallows_the_queue(
    conn: Connection, seed: Seed
) -> None:
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")

    leaked = _promote_everything(conn, seed, report)

    # 先确认这个坏实现**真的漏了**：它要是自己失败了，下面那条 raises 会因为
    # 「什么都没升」而通过——那就又是一条永远绿的守卫。
    assert leaked.failures == ()
    assert LOW_EVENT_SUMMARY in canon_event_summaries(conn, seed)
    with pytest.raises(AssertionError):
        assert_only_the_clean_facts_went_canon(conn, seed)


def test_the_guard_can_see_a_promotion_logged_as_the_author(
    conn: Connection, seed: Seed
) -> None:
    report = _ingest(conn, seed, prompt_hash="prompt:mixed")

    _promote_as_the_author(conn, seed, report)

    # 同上：先确认它真的升上去并且真的写了一行日志，红才是红在 actor 上。
    assert canon_event_summaries(conn, seed) == {CLEAN_EVENT_SUMMARY}
    assert len(review_decisions(conn, seed)) == 1
    with pytest.raises(AssertionError):
        assert_every_review_decision_is_the_system(conn, seed, expected=1)
