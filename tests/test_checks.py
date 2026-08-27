"""R3 + 规则契约。

**这些测试里一半是「不报」的测试。** 那不是凑数：M3 的生死线是「误报 < 1 条/章」，
而每一条「闭嘴条件」删掉之后都**不会让任何一条会报的测试变红**——它只会让真书上多出
几条误报，在第 11 周才被发现。

⚠️ **本文件只打下面那个 `FakeGraph`，到不了生产的 `state_at`。** 那个 Fake 手写了
一遍五条件过滤（见它的 docstring），所以「时态正确」「STALE 停火」在这里验的是 Fake 的
保真度。真库上的同一组断言在 `tests/test_store_conformance.py`——它 import 本文件的
`FakeGraph`，把同一份规格参数化跑 [fake, real]。**改这个 Fake 的过滤逻辑前先读那个文件。**

⚠️ **2026-08-14：R4 LOCATION_CONFLICT 那一整节（约 200 行）随规则一起删了**
（[ADR 0027](../docs/adr/0027-scene-blocks-cut.md)）。它是这个文件原本的主角，
四个闭嘴条件各有一条测试——**而那些测试从头到尾都是绿的，规则本身也没有 bug**。
砍掉它的判据不在这一层：它的一侧输入（场景块 `<!-- nh: loc=… -->`）只能由作者手写，
真书上零覆盖，于是这条零误报的规则在产品里**一次都没开过火**。
留一句在这儿是因为「测试全绿」这件事在那 200 行上曾经读起来像「这条规则很健康」。

⚠️ **2026-08-27：R2 FUTURE_LEAK 那一节（`test_r2_*` 六条 + 它的 `SYSTEM_RULES`/
`ALL_CHECKS` 断言）也删了**（[ADR 0040](../docs/adr/0040-future-leak-cut.md)）。
同上一条同一个判据：`first_appears_chapter` 从来没有输入路径，R2 在产品里
一次都没开过火。跟 R4 不一样的是它的输入连「作者理论上可以手写」都没有——
没有输入框，物理上敲不进去。`has_state()` 那个 helper 留着，R3 的死亡场景测试
还在用它。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

import pytest


from novel_harness.checks import ALL_CHECKS, CheckContext, Issue, run_checks
from novel_harness.checks.catalog import (
    CURRENT_RULESET_EPOCH,
    CURRENT_RULESET_HASH,
    RuleAvailability,
    RuleSpec,
    SYSTEM_RULES,
    ruleset_hash,
    ruleset_semantic_json,
)
from novel_harness.checks import catalog as checks_catalog
from novel_harness.checks.dead_speaks import check as dead_speaks_check
from novel_harness.checks.service import (
    RulesetStateMissing,
    SnapshotValidationReport,
    current_ruleset,
    validate_snapshot,
)
from novel_harness.db import IN_MEMORY, connect, migrate
from novel_harness import importer, project
from novel_harness.graph import (
    AliasHit,
    AliasKind,
    Edge,
    EdgeProps,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    HealthValue,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    Resolution,
    StateSnapshot,
    StateValue,
    StoryGraph,
    Subgraph,
    UpsertResult,
)
from novel_harness.graph import ChapterSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.text import paragraphs as split_paragraphs
from novel_harness.text.chapterize import chapterize

PID = "project:demo:01J0"


def node(
    node_id: str,
    label: NodeLabel,
    name: str,
    *,
    first_appears: int | None = None,
    dim_key: str | None = None,
) -> Node:
    return Node(
        id=node_id,
        project_id=PID,
        label=label,
        name=name,
        props=NodeProps(first_appears_chapter=first_appears, dim_key=dim_key),
    )


XIAO_JUE = node("character:demo:01J1", NodeLabel.CHARACTER, "萧决")
GU_QINGYIN = node("character:demo:01J2", NodeLabel.CHARACTER, "顾清音", first_appears=200)
QINGYUN = node("location:demo:01J3", NodeLabel.LOCATION, "青云城主府")
BEIHUANG = node("location:demo:01J4", NodeLabel.LOCATION, "北荒")
BEIHUANG_2 = node("location:demo:01J5", NodeLabel.LOCATION, "北荒")
"""同名的第二个地点——「北荒」这个 surface 于是有歧义。"""

HEALTH_DIM = node("state:demo:01J7", NodeLabel.STATE_DIM, "健康", dim_key="health")


def located_at(
    src: str,
    dst: str,
    valid_from: int,
    *,
    valid_to: int | None = None,
    scope: InformationScope = InformationScope.CANON,
    status: EdgeStatus = EdgeStatus.ACTIVE,
    evidence_status: EvidenceStatus = EvidenceStatus.NONE,
) -> Edge:
    return Edge(
        id=f"edge:demo:{src}-{dst}-{valid_from}",
        project_id=PID,
        src=src,
        dst=dst,
        type=EdgeType.LOCATED_AT,
        valid_from_chapter=valid_from,
        valid_to_chapter=valid_to,
        information_scope=scope,
        status=status,
        evidence_status=evidence_status,
    )


class FakeGraph:
    """按 store.py 的契约实现 `resolve` / `state_at`。

    `state_at` 里那五个过滤条件是照契约抄的（store.py 的实现约束 1）——它们是
    这份 fake 存在的意义：R4 的「时态正确」和「STALE 停火」都得穿过它们才成立。
    """

    def __init__(self, aliases: dict[str, list[Node]], edges: list[Edge]) -> None:
        self._aliases = aliases
        self._edges = edges
        self._nodes = {n.id: n for n in (x for hits in aliases.values() for x in hits)}

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        del project_id
        if surfaces is None:
            # store.py 契约：None = 全项目花名册，按 surface 长度降序，
            # 直接可喂 alternation（text/mentions.py 靠它）。
            surfaces = sorted(self._aliases, key=lambda s: (-len(s), s))
        out = []
        for surface in surfaces:
            hits = [
                AliasHit(node=n, kind=AliasKind.CANONICAL, usable_for_rules=len(surface) >= 2)
                for n in self._aliases.get(surface, [])
            ]
            resolution = Resolution(surface=surface, hits=hits)
            if rules_only and not resolution.usable_for_rules:
                resolution = Resolution(surface=surface, hits=[])
            # 解析不到的 surface 也要返回 hits=[]，不许静默丢（契约）。
            out.append(resolution)
        return out

    def canon_version(self, project_id: str) -> int:
        del project_id
        return 0

    def state_at(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> StateSnapshot:
        del project_id
        edges = [
            e
            for e in self._edges
            if e.src == node_id
            and e.holds_at(chapter)
            and e.information_scope is scope
            and e.status is EdgeStatus.ACTIVE
            and e.evidence_status is not EvidenceStatus.STALE
        ]
        locations = [e for e in edges if e.type is EdgeType.LOCATED_AT]
        if len(locations) > 1:
            # exclusivity=single_per_src 保证至多一条。两条 = supersede 漏了，让它炸。
            raise AssertionError(f"{node_id} 在第 {chapter} 章有 {len(locations)} 条 LOCATED_AT")
        states: list[StateValue] = [
            StateValue(
                dim=self._nodes[e.dst],
                dim_key=self._nodes[e.dst].props.dim_key,
                value=e.props.value,
                value_key=e.props.value_key,
                since_chapter=e.valid_from_chapter,
                evidence_id=None,
            )
            for e in edges
            if e.type is EdgeType.HAS_STATE
        ]
        return StateSnapshot(
            node=self._nodes[node_id],
            chapter=chapter,
            scope=scope,
            edges=edges,
            location=self._nodes[locations[0].dst] if locations else None,
            states=states,
        )

    def subgraph(
        self,
        project_id: str,
        center: str,
        chapter: int,
        *,
        hops: int = 1,
        edge_types: Collection[EdgeType] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> Subgraph:
        raise NotImplementedError

    def upsert_edge(self, spec: EdgeSpec) -> UpsertResult:
        raise NotImplementedError


ALIASES: dict[str, list[Node]] = {
    "萧决": [XIAO_JUE],
    "顾清音": [GU_QINGYIN],
    "青云城主府": [QINGYUN],
    "北荒": [BEIHUANG],
}


def ctx(
    edges: list[Edge],
    *,
    chapter: int = 151,
    aliases: dict[str, list[Node]] | None = None,
    paragraphs: Sequence[str] | None = None,
) -> CheckContext:
    """2026-08-14：`loc` / `cast` 两个参数随场景块一起没了（ADR 0027）——
    今天两条规则的输入只有「图 + 正文」。"""
    return CheckContext(
        store=FakeGraph(aliases or ALIASES, edges),
        project_id=PID,
        chapter=chapter,
        paragraphs=paragraphs,
    )


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeGraph({}, []), StoryGraph)


# ══════════════════════════════════════════════════════════════════════════
# 规则契约本身
# ══════════════════════════════════════════════════════════════════════════


def test_issue_has_no_offset_and_never_will() -> None:
    """ADR 0006 Day 2 定死：Issue 一律用 (para_index, quote_text, occurrence_k)。

    offset 是三重错位的温床（坐标系 / UTF-16 vs code point / 快照漂移），它会以
    「偶尔位置差一两个字」的形态出现，被误当成小 bug 调两周。这条断言让那个字段
    加不进来。
    """
    assert set(Issue.model_fields) == {
        "rule",
        "issue_type",
        "chapter",
        "anchor",
        "message",
        "suggested_action",
    }
    assert set(Issue.model_fields["anchor"].annotation.model_fields) == {
        "para_index",
        "quote_text",
        "occurrence_k",
    }


def test_no_rule_raises_without_the_manuscript() -> None:
    """`paragraphs=None` 时**每一条规则都必须安静地返回 `[]`，不许抛**。

    面板/规则是两条链路（§6 serious #3）：面板不读正文（2–5ms），规则读。
    今天两条规则都在读正文那一侧，所以这一条量的是「没正文时它们闭嘴」——
    ⚠️ **2026-08-14 之前它量的是相反的一件事**（R4 不读正文也照样开火），
    而 R4 是那时唯一不读正文的规则。这条断言换了含义，不是换了写法。
    """
    context = ctx([])
    assert context.paragraphs is None

    for check in ALL_CHECKS:
        assert check(context) == []


def test_run_checks_runs_the_registry() -> None:
    assert set(ALL_CHECKS) == {dead_speaks_check}
    context = ctx([], paragraphs=["顾清音道：「……」"])
    assert len(run_checks(context)) == 1


def test_checks_are_pure_functions_of_ctx() -> None:
    """同一个 ctx 跑两次结果相同——判分器（eval）和 Validator（写作时）是同一份代码，
    它必须可复现，否则 kill-gate 量的是噪声。"""
    context = ctx([], paragraphs=["顾清音道：「……」"])

    for check in ALL_CHECKS:
        assert check(context) == check(context)


def has_state(
    src: str,
    dst: str,
    value_key: str,
    valid_from: int,
    *,
    valid_to: int | None = None,
) -> Edge:
    return Edge(
        id=f"edge:demo:{src}-{dst}-{valid_from}",
        project_id=PID,
        src=src,
        dst=dst,
        type=EdgeType.HAS_STATE,
        valid_from_chapter=valid_from,
        valid_to_chapter=valid_to,
        information_scope=InformationScope.CANON,
        status=EdgeStatus.ACTIVE,
        evidence_status=EvidenceStatus.NONE,
        props=EdgeProps(value_key=value_key),
    )


# ══════════════════════════════════════════════════════════════════════════
# R3 DEAD_SPEAKS（2026-08-02）
# ══════════════════════════════════════════════════════════════════════════


def test_r3_fires_when_a_dead_character_speaks() -> None:
    edges = [has_state(XIAO_JUE.id, HEALTH_DIM.id, HealthValue.DEAD, 89)]
    issues = dead_speaks_check(
        ctx(edges, aliases={**ALIASES, "健康": [HEALTH_DIM]}, paragraphs=["萧决道：「……」"])
    )
    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule == "R3"
    assert issue.issue_type == "DEAD_SPEAKS"
    assert "死" in issue.message and "萧决" in issue.message
    assert issue.anchor.quote_text == "萧决"


def test_r3_fires_when_a_not_yet_appeared_character_speaks() -> None:
    issues = dead_speaks_check(ctx([], paragraphs=["顾清音道：「……」"]))
    assert len(issues) == 1
    assert "200" in issues[0].message and "登场" in issues[0].message


def test_r3_silent_for_living_appeared_character() -> None:
    assert dead_speaks_check(ctx([], paragraphs=["萧决道：「……」"])) == []


def test_r3_silent_before_the_death_chapter() -> None:
    edges = [has_state(XIAO_JUE.id, HEALTH_DIM.id, HealthValue.DEAD, 89)]
    assert (
        dead_speaks_check(
            ctx(
                edges,
                chapter=50,
                aliases={**ALIASES, "健康": [HEALTH_DIM]},
                paragraphs=["萧决道：「……」"],
            )
        )
        == []
    )


def test_r3_silent_when_the_name_is_not_a_speaker_tag() -> None:
    """「萧决当年……」是别人提到死者，不在标签位置——ADR 0005 的 R3 注释原样测试。"""
    assert dead_speaks_check(ctx([], paragraphs=["萧决当年……"])) == []


def test_r3_silent_without_paragraphs() -> None:
    assert dead_speaks_check(ctx([])) == []


def test_r3_longest_surface_wins_before_the_verb() -> None:
    aliases = {"顾清音": [GU_QINGYIN], "清音": [GU_QINGYIN]}
    issues = dead_speaks_check(ctx([], aliases=aliases, paragraphs=["顾清音道：「……」"]))
    assert len(issues) == 1
    assert issues[0].anchor.quote_text == "顾清音"


# ══════════════════════════════════════════════════════════════════════════
# 规则目录 —— 稳定语义字段 / 排序 / 冻结 hash（Task 3）
# ══════════════════════════════════════════════════════════════════════════


def test_catalog_lists_exactly_r3_with_stable_semantics() -> None:
    assert [spec.rule_id for spec in SYSTEM_RULES] == ["R3"]
    assert all(spec.enabled and spec.blocks_downstream for spec in SYSTEM_RULES)
    assert all(spec.schema_version == "v1" for spec in SYSTEM_RULES)
    assert {spec.template for spec in SYSTEM_RULES} == {"system"}


def test_ruleset_hash_is_stable_and_order_independent() -> None:
    first = ruleset_semantic_json(SYSTEM_RULES)
    # 同一份目录怎么排都算同一个 JSON（排序由函数负责，不靠调用方传序）。
    assert ruleset_semantic_json(tuple(reversed(SYSTEM_RULES))) == first
    assert ruleset_hash(SYSTEM_RULES) == CURRENT_RULESET_HASH
    assert ruleset_hash() == CURRENT_RULESET_HASH


def test_title_and_description_do_not_enter_the_hash() -> None:
    """只改 UI 文案不能让所有机器任务失效（§4.2）。"""
    from dataclasses import replace

    renamed = tuple(
        replace(spec, title="换个标题", description="换个说明") for spec in SYSTEM_RULES
    )
    assert ruleset_hash(renamed) == CURRENT_RULESET_HASH


# ══════════════════════════════════════════════════════════════════════════
# 快照绑定的验证服务（Task 4）：报告绑定 / availability / error / ruleset 缺行
# ══════════════════════════════════════════════════════════════════════════


def _validation_token(pid: str, store: SqliteStoryGraph, text: str):
    chapter = chapterize(text).chapters[0]
    spec = ChapterSpec(
        project_id=pid,
        number=1,
        heading=chapter.raw_heading,
        title=chapter.title,
        path="chapters/0001.md",
        text=text,
    )
    return store.commit_chapter_snapshot(
        spec, expected_text_sha256=importer.text_digest(text)
    )


def test_service_report_binds_snapshot_ruleset_and_persists() -> None:
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    token = _validation_token(pid, store, "第一章 甲\n\n萧决走进来了。\n")
    epoch, ruleset_hash = current_ruleset(conn, pid)
    assert (epoch, ruleset_hash) == (CURRENT_RULESET_EPOCH, CURRENT_RULESET_HASH)

    report = validate_snapshot(
        conn, store, token, ruleset_epoch=epoch, ruleset_hash=ruleset_hash,
        paragraphs=split_paragraphs(token.text),
    )
    assert isinstance(report, SnapshotValidationReport)
    assert report.source_snapshot_id == token.source_snapshot_id
    assert report.source_generation == token.source_generation
    assert report.text_sha256 == token.text_sha256
    assert report.phase == "initial"
    assert report.gate == "passed"
    assert [r.rule_id for r in report.rules] == ["R3"]
    assert all(r.state == "clear" for r in report.rules)

    row = conn.execute(
        "SELECT chapter_snapshot_id, source_generation, phase, text_sha256, "
        "ruleset_epoch, ruleset_hash, gate, rules_json FROM validation_report WHERE id = ?",
        (report.id,),
    ).fetchone()
    assert row["chapter_snapshot_id"] == token.source_snapshot_id
    assert row["source_generation"] == token.source_generation
    assert row["ruleset_epoch"] == CURRENT_RULESET_EPOCH
    assert row["ruleset_hash"] == CURRENT_RULESET_HASH
    assert row["gate"] == "passed"
    conn.close()


def test_service_marks_technical_unavailable_without_blocking() -> None:
    """正文输入没装入 = unavailable（不阻断，gate 仍 passed）；不是「0 条问题」。"""
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    token = _validation_token(pid, store, "第一章 甲\n\n萧决走进来了。\n")
    epoch, ruleset_hash = current_ruleset(conn, pid)
    report = validate_snapshot(
        conn, store, token, ruleset_epoch=epoch, ruleset_hash=ruleset_hash, paragraphs=None,
    )
    assert all(r.state == "unavailable" for r in report.rules)
    assert report.gate == "passed"
    conn.close()


def test_service_rule_exception_becomes_error_and_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    token = _validation_token(pid, store, "第一章 甲\n\n萧决走进来了。\n")
    epoch, ruleset_hash = current_ruleset(conn, pid)

    def boom(ctx: CheckContext) -> list[Issue]:
        raise RuntimeError("注入：规则崩溃")

    broken = RuleSpec(
        rule_id="TEST",
        title="测试规则",
        description="注入崩溃",
        blocks_downstream=True,
        availability=lambda ctx: RuleAvailability.AVAILABLE,
        check=boom,
    )
    monkeypatch.setattr(checks_catalog, "SYSTEM_RULES", (broken,))
    report = validate_snapshot(
        conn, store, token, ruleset_epoch=epoch, ruleset_hash=ruleset_hash,
        paragraphs=split_paragraphs(token.text),
    )
    assert report.gate == "error"
    assert any(r.state == "error" for r in report.rules)
    assert report.rules[0].rule_id == "TEST"
    conn.close()


def test_service_missing_ruleset_row_is_an_error_not_a_temp_epoch() -> None:
    conn = connect(IN_MEMORY)
    migrate(conn)
    store = SqliteStoryGraph(conn)
    pid = project.create(conn, name="t", root_path=".").id
    _validation_token(pid, store, "第一章 甲\n\n萧决走进来了。\n")
    conn.execute("DELETE FROM validation_ruleset_state WHERE project_id = ?", (pid,))
    conn.commit()

    with pytest.raises(RulesetStateMissing):
        current_ruleset(conn, pid)
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 自定义确定性规则（024 / Task 13）
# ══════════════════════════════════════════════════════════════════════════


def test_forbidden_literal_finds_every_occurrence() -> None:
    from novel_harness.checks.custom import forbidden_literal_check

    check = forbidden_literal_check("玄铁令", rule_id="vrule:1")
    issues = check(ctx([], paragraphs=["萧决把玄铁令收进袖中，又把玄铁令放回桌上。"]))
    assert len(issues) == 2
    assert all(i.rule == "custom:vrule:1" for i in issues)
    assert issues[0].anchor.quote_text == "玄铁令"


def test_forbidden_literal_silent_when_absent() -> None:
    from novel_harness.checks.custom import forbidden_literal_check

    issues = forbidden_literal_check("玄铁令")(ctx([], paragraphs=["风起，雪落。"], chapter=1))
    assert issues == []


def test_custom_rule_spec_is_a_directory_rule() -> None:
    from novel_harness.checks.custom import custom_rule_spec

    spec = custom_rule_spec(
        rule_id="vrule:1", title="不许有玄铁令", literal="玄铁令", blocks_downstream=True
    )
    assert spec.template == "forbidden_literal"
    assert spec.check is not None
    found = spec.check(ctx([], paragraphs=["玄铁令出现了。"]))
    assert len(found) == 1
