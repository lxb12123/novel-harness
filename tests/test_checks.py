"""R4 LOCATION_CONFLICT + 规则契约。

**这些测试里一半是「不报」的测试。** 那不是凑数：M3 的生死线是「误报 < 1 条/章」，
而 R4 的零 FP 全部来自四个闭嘴条件。每个闭嘴条件都有一条测试，因为删掉其中任何一个
都不会让「会报的那条」变红——它只会让真书上多出几条误报，在第 11 周才被发现。

⚠️ **本文件的 R4 只打下面那个 `FakeGraph`，到不了生产的 `state_at`。** 那个 Fake 手写了
一遍五条件过滤（见它的 docstring），所以「时态正确」「STALE 停火」在这里验的是 Fake 的
保真度。真库上的同一组断言在 `tests/test_store_conformance.py`——它 import 本文件的
`FakeGraph`，把同一份规格参数化跑 [fake, real]。**改这个 Fake 的过滤逻辑前先读那个文件。**
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

import pytest

from novel_harness.checks import ALL_CHECKS, CheckContext, Issue, Scene, run_checks
from novel_harness.checks.location_conflict import check
from novel_harness.graph import (
    AliasHit,
    AliasKind,
    Edge,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    InformationScope,
    KnowledgeMatrix,
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

PID = "project:demo:01J0"


def node(node_id: str, label: NodeLabel, name: str) -> Node:
    return Node(id=node_id, project_id=PID, label=label, name=name, props=NodeProps())


XIAO_JUE = node("character:demo:01J1", NodeLabel.CHARACTER, "萧决")
GU_QINGYIN = node("character:demo:01J2", NodeLabel.CHARACTER, "顾清音")
QINGYUN = node("location:demo:01J3", NodeLabel.LOCATION, "青云城主府")
BEIHUANG = node("location:demo:01J4", NodeLabel.LOCATION, "北荒")
BEIHUANG_2 = node("location:demo:01J5", NodeLabel.LOCATION, "北荒")
"""同名的第二个地点——「北荒」这个 surface 于是有歧义。"""


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
        assert surfaces is not None, "R4 只按 surface 查，不要花名册"
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
        states: list[StateValue] = []
        return StateSnapshot(
            node=self._nodes[node_id],
            chapter=chapter,
            scope=scope,
            edges=edges,
            location=self._nodes[locations[0].dst] if locations else None,
            states=states,
        )

    def knowledge_matrix(
        self,
        project_id: str,
        chapter: int,
        cast: Sequence[str],
        *,
        secrets: Sequence[str] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> KnowledgeMatrix:
        raise NotImplementedError

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
    loc: str | None = "北荒",
    cast: list[str] | None = None,
    aliases: dict[str, list[Node]] | None = None,
) -> CheckContext:
    scene = Scene(
        number=3,
        cast=cast if cast is not None else ["萧决"],
        loc=loc,
        goal="李管家试探萧决的身世",
        para_index=4,
        decl_text=f"<!-- nh: cast=萧决 loc={loc} -->",
    )
    return CheckContext(
        store=FakeGraph(aliases or ALIASES, edges),
        project_id=PID,
        chapter=chapter,
        scenes=[scene],
    )


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeGraph({}, []), StoryGraph)


# ══════════════════════════════════════════════════════════════════════════
# 会报的那条
# ══════════════════════════════════════════════════════════════════════════


def test_declared_loc_conflicts_with_graph() -> None:
    """作者声明 vs 作者声明：场景写着北荒，图上他在青云城主府。"""
    issues = check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)]))

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule == "R4"
    assert issue.issue_type == "LOCATION_CONFLICT"
    assert issue.chapter == 151
    assert "青云城主府" in issue.message and "萧决" in issue.message
    assert issue.suggested_action is not None and "青云城主府" in issue.suggested_action


def test_issue_anchor_is_the_declaration_line() -> None:
    """R4 不读正文，但它报的问题有精确位置：作者写错的那行声明。"""
    issues = check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)]))

    anchor = issues[0].anchor
    assert anchor.para_index == 4
    assert anchor.quote_text == "<!-- nh: cast=萧决 loc=北荒 -->"
    assert anchor.occurrence_k == 0


def test_one_issue_per_conflicting_character() -> None:
    issues = check(
        ctx(
            [
                located_at(XIAO_JUE.id, QINGYUN.id, 10),
                located_at(GU_QINGYIN.id, QINGYUN.id, 10),
            ],
            cast=["萧决", "顾清音"],
        )
    )

    assert len(issues) == 2


# ══════════════════════════════════════════════════════════════════════════
# 时态：闭开区间 [valid_from, valid_to)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("chapter", "expected"),
    [(150, 1), (151, 0), (152, 0)],
)
def test_conflict_disappears_the_chapter_he_arrives(chapter: int, expected: int) -> None:
    """他在第 151 章到北荒：ch150 声明 loc=北荒 是冲突，ch151 起不是。

    区间的上闭下开在这里是可见的产品行为，不是内部细节。
    """
    edges = [
        located_at(XIAO_JUE.id, QINGYUN.id, 10, valid_to=151),
        located_at(XIAO_JUE.id, BEIHUANG.id, 151),
    ]

    assert len(check(ctx(edges, chapter=chapter))) == expected


# ══════════════════════════════════════════════════════════════════════════
# 四个闭嘴条件 —— 零 FP 的全部来源
# ══════════════════════════════════════════════════════════════════════════


def test_silent_when_no_location_edge_at_all() -> None:
    """闭世界：图上没声明过他在哪 ≠ 他不在这。"""
    assert check(ctx([])) == []


def test_silent_when_declared_loc_is_ambiguous() -> None:
    """「北荒」映射到两个节点 → 不知道作者指哪个 → 闭嘴。"""
    aliases = {**ALIASES, "北荒": [BEIHUANG, BEIHUANG_2]}

    assert check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)], aliases=aliases)) == []


def test_silent_when_declared_loc_is_unknown() -> None:
    """作者写了个图上没有的地方（还没建节点）→ 那不是位置冲突。"""
    assert check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)], loc="幽泉窟")) == []


def test_silent_when_declared_loc_is_not_a_location() -> None:
    """`loc=萧决`（打错了）→ 闭嘴。拿人物节点去比所在地会报出一条无意义的红字。"""
    assert check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)], loc="萧决")) == []


def test_silent_when_cast_surface_is_ambiguous() -> None:
    """「师兄」一章里可能是 8 个人 → 跳过这个人，不猜。"""
    aliases = {**ALIASES, "师兄": [XIAO_JUE, GU_QINGYIN]}
    edges = [located_at(XIAO_JUE.id, QINGYUN.id, 10), located_at(GU_QINGYIN.id, QINGYUN.id, 10)]

    assert check(ctx(edges, cast=["师兄"], aliases=aliases)) == []


def test_short_but_unambiguous_loc_still_fires() -> None:
    """1 字地名不是闭嘴条件。

    ADR 0004 的短别名约束防的是「短别名去匹配正文」（「音」「决」是灾难），而 R4
    **不匹配正文**——它读的是作者亲手敲在 `loc=` 后面的字。所以这里用
    `Resolution.unique_node` 而不是 `usable_for_rules`。
    """
    yuan = node("location:demo:01J6", NodeLabel.LOCATION, "渊")
    aliases = {**ALIASES, "渊": [yuan]}

    issues = check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)], loc="渊", aliases=aliases))

    assert len(issues) == 1


def test_scene_without_loc_is_skipped() -> None:
    assert check(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)], loc=None)) == []


# ══════════════════════════════════════════════════════════════════════════
# 只在 CANON 层开火
# ══════════════════════════════════════════════════════════════════════════


def test_provisional_edge_never_fires() -> None:
    """§5.4：PROVISIONAL 是抽取器猜的、未确认的。**永不开火。**

    拿它报错 = 用 Agent 的猜测去质疑作者 = 原则 5 的反面。
    """
    edges = [located_at(XIAO_JUE.id, QINGYUN.id, 10, scope=InformationScope.PROVISIONAL)]

    assert check(ctx(edges)) == []


def test_stale_evidence_stops_firing() -> None:
    """ADR 0006：依据被作者改没了 ⇒ 立刻停火。

    「依据没了还在质疑作者」是最伤的那种误报——这就是 STALE 90% 的价值。
    """
    edges = [located_at(XIAO_JUE.id, QINGYUN.id, 10, evidence_status=EvidenceStatus.STALE)]

    assert check(ctx(edges)) == []


def test_retracted_edge_never_fires() -> None:
    edges = [located_at(XIAO_JUE.id, QINGYUN.id, 10, status=EdgeStatus.RETRACTED)]

    assert check(ctx(edges)) == []


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


def test_check_does_not_read_the_manuscript() -> None:
    """R4 的零 FP 前提：`paragraphs=None` 也照跑。

    面板/规则是两条链路（§6 serious #3），R4 属于不读正文那条。
    """
    context = ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)])
    assert context.paragraphs is None

    assert len(check(context)) == 1


def test_run_checks_runs_the_registry() -> None:
    assert ALL_CHECKS == (check,)
    assert len(run_checks(ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)]))) == 1


def test_check_is_a_pure_function_of_ctx() -> None:
    """同一个 ctx 跑两次结果相同——判分器（eval）和 Validator（写作时）是同一份代码，
    它必须可复现，否则 kill-gate 量的是噪声。"""
    context = ctx([located_at(XIAO_JUE.id, QINGYUN.id, 10)])

    assert check(context) == check(context)
