"""认知边界矩阵（头牌）+ 约束转译。

§8 Day 5 点名的单测：**一个人物在 ch88 得知秘密 → ch87 UNKNOWN、ch88 KNOWS、
ch152 KNOWS。**

这里的 `FakeGraph` 是 StoryGraph Protocol 的一个**参考实现**，不是 mock：它按契约
写的那五个条件做时态过滤，所以这些测试同时是写给 `sqlite_store.py` 的一份可执行
规格——它必须让同样的断言过。（`sqlite_store` 由并行的 agent 写，本文件不依赖它。）

⚠️ **本文件的断言全部只打 Fake，一条都到不了生产的 SQL。** 上面那句「它必须让同样的
断言过」在很长一段时间里没有任何东西强制——包括 ch87/ch88/ch152 这条 README 第一行的
承诺，它验的是 Fake 的保真度，不是 `queries.knowledge_edges_at`。强制它的是
`tests/test_store_conformance.py`：同一份规格参数化跑 [fake, real] 两个后端，本文件的
`FakeGraph` 被它直接 import。**改这个 Fake 的时态/闭世界逻辑前先读那个文件**——
它漂了，那边 `[fake]` 与 `[real]` 会当场分叉。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

import pytest

from novel_harness.graph import (
    AliasHit,
    AliasKind,
    Edge,
    EdgeProps,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    EvidenceStatus,
    InformationScope,
    KnowledgeCell,
    KnowledgeMatrix,
    KnowledgeState,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    Resolution,
    StateSnapshot,
    StoryGraph,
    Subgraph,
    UpsertResult,
)
from novel_harness.panel import (
    UnresolvedCast,
    resolve_cast,
    scene_constraints,
)

PID = "project:demo:01J0"


def node(node_id: str, label: NodeLabel, name: str, **props: object) -> Node:
    return Node(id=node_id, project_id=PID, label=label, name=name, props=NodeProps(**props))


XIAO_JUE = node("character:demo:01J1", NodeLabel.CHARACTER, "萧决")
GU_QINGYIN = node("character:demo:01J2", NodeLabel.CHARACTER, "顾清音")
LI_GUANJIA = node("character:demo:01J3", NodeLabel.CHARACTER, "李管家")
BLOODLINE = node("secret:demo:01J4", NodeLabel.SECRET, "血脉秘密")
XUANTIE = node("secret:demo:01J5", NodeLabel.SECRET, "玄铁令下落")
YOUQUAN = node("location:demo:01J6", NodeLabel.LOCATION, "幽泉窟", first_appears_chapter=200)


def edge(
    src: str,
    dst: str,
    edge_type: EdgeType,
    valid_from: int,
    *,
    valid_to: int | None = None,
    scope: InformationScope = InformationScope.CANON,
    status: EdgeStatus = EdgeStatus.ACTIVE,
    evidence_status: EvidenceStatus = EvidenceStatus.NONE,
    **props: object,
) -> Edge:
    return Edge(
        id=f"edge:demo:{src}-{dst}-{edge_type}-{valid_from}",
        project_id=PID,
        src=src,
        dst=dst,
        type=edge_type,
        props=EdgeProps(**props),
        valid_from_chapter=valid_from,
        valid_to_chapter=valid_to,
        information_scope=scope,
        status=status,
        evidence_status=evidence_status,
    )


class FakeGraph:
    """按 store.py 的契约实现 `knowledge_matrix` / `resolve`。其余方法无消费者。"""

    def __init__(
        self,
        nodes: list[Node],
        edges: list[Edge],
        extra_aliases: dict[str, list[Node]] | None = None,
    ) -> None:
        self._nodes = {n.id: n for n in nodes}
        self._edges = edges
        # 每个节点一条 canonical 别名行（surface == node.name），外加测试塞进来的别名。
        # 一个 surface 映射到多个节点（「师兄」）是**跨行事实**，所以它长这样而不是
        # 在节点上挂一个字段——alias 表里存不进「歧义」，那只能在查询时算出来。
        self._aliases: dict[str, list[Node]] = {n.name: [n] for n in nodes}
        for surface, targets in (extra_aliases or {}).items():
            self._aliases.setdefault(surface, []).extend(targets)

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        del project_id
        if surfaces is None:
            # 花名册：按 surface 长度降序（mentions.py 的 alternation 要 leftmost-first）。
            ordered = [
                Resolution(surface=s, hits=[_hit(n) for n in targets])
                for s, targets in sorted(
                    self._aliases.items(), key=lambda kv: len(kv[0]), reverse=True
                )
            ]
        else:
            # 与入参**一一对应且同序**；解析不到的返回 hits=[]，**不许静默丢**——
            # 否则调用方分不清「没这个人」和「我没问过这个人」。
            ordered = [
                Resolution(surface=s, hits=[_hit(n) for n in self._aliases.get(s, [])])
                for s in surfaces
            ]
        if rules_only:
            return [r for r in ordered if r.usable_for_rules]
        return ordered

    def canon_version(self, project_id: str) -> int:
        del project_id
        return 0

    def knowledge_edges_at(
        self,
        project_id: str,
        character_ids: Sequence[str],
        secret_ids: Sequence[str],
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[Edge]:
        del project_id, character_ids, secret_ids, chapter, scope
        return []

    def state_at(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> StateSnapshot:
        raise NotImplementedError

    def knowledge_matrix(
        self,
        project_id: str,
        chapter: int,
        cast: Sequence[str],
        *,
        secrets: Sequence[str] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> KnowledgeMatrix:
        secret_ids = list(
            secrets
            if secrets is not None
            else [n.id for n in self._nodes.values() if n.label is NodeLabel.SECRET]
        )
        return KnowledgeMatrix(
            project_id=project_id,
            chapter=chapter,
            scope=scope,
            # 窄引用（NodeRef）：矩阵会被整份序列化进 D 分区，而 Secret 节点的 props 里
            # 装的就是秘密的内容。传 Node 会被 pydantic 当场拒——那是故意的。
            characters=[NodeRef.of(self._nodes[c]) for c in cast],
            secrets=[NodeRef.of(self._nodes[s]) for s in secret_ids],
            cells=[self._cell(cid, sid, chapter, scope) for cid in cast for sid in secret_ids],
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

    def _cell(
        self, cid: str, sid: str, chapter: int, scope: InformationScope
    ) -> KnowledgeCell:
        for want in (EdgeType.KNOWS, EdgeType.BELIEVES):
            for e in self._edges:
                if (e.src, e.dst, e.type) != (cid, sid, want):
                    continue
                # 五条件过滤，一个都不能少（store.py 的实现约束 1）。
                if not e.holds_at(chapter):
                    continue
                if e.information_scope is not scope or e.status is not EdgeStatus.ACTIVE:
                    continue
                if e.evidence_status is EvidenceStatus.STALE:
                    continue
                return KnowledgeCell(
                    character_id=cid,
                    secret_id=sid,
                    state=KnowledgeState(want.value),
                    since_chapter=e.valid_from_chapter,
                    believed_value=e.props.believed_value,
                    evidence_id=e.evidence_id,
                )
        # 闭世界：没有边 ⇒ 不知道。这一格必须被物化。
        return KnowledgeCell(character_id=cid, secret_id=sid, state=KnowledgeState.UNKNOWN)


def _hit(n: Node) -> AliasHit:
    return AliasHit(node=n, kind=AliasKind.CANONICAL, usable_for_rules=len(n.name) >= 2)


def build(edges: list[Edge], extra_aliases: dict[str, list[Node]] | None = None) -> FakeGraph:
    return FakeGraph(
        [XIAO_JUE, GU_QINGYIN, LI_GUANJIA, BLOODLINE, XUANTIE, YOUQUAN], edges, extra_aliases
    )


AMBIGUOUS_SHIXIONG: dict[str, list[Node]] = {"师兄": [XIAO_JUE, LI_GUANJIA]}
"""PLAN §3.1 点名的那个场景：「师兄」在一章里可能指 8 个人中的任何一个。"""


def test_fake_satisfies_protocol() -> None:
    assert isinstance(build([]), StoryGraph)


# ══════════════════════════════════════════════════════════════════════════
# §8 Day 5 点名的那条：ch87 UNKNOWN / ch88 KNOWS / ch152 KNOWS
# ══════════════════════════════════════════════════════════════════════════


def test_forbidden_entities_come_from_first_appears_chapter() -> None:
    """§3.2 面板上那行「幽泉窟(ch200 首现)」。"""
    store = build([])

    c = scene_constraints(store, PID, 152, [XIAO_JUE.name], secrets=[BLOODLINE.id])

    assert [(e.node.name, e.first_appears_chapter) for e in c.forbidden_entities] == [
        ("幽泉窟", 200)
    ]
    assert c.forbidden_entities[0].surfaces == ["幽泉窟"]


@pytest.mark.parametrize("chapter", [200, 201])
def test_entity_stops_being_forbidden_once_it_has_appeared(chapter: int) -> None:
    """首现章号 200 = 第 200 章他就登场了，那一章起不再是「未来实体」。"""
    store = build([])

    c = scene_constraints(store, PID, chapter, [XIAO_JUE.name], secrets=[BLOODLINE.id])

    assert c.forbidden_entities == []
def test_two_surfaces_for_one_person_is_one_row() -> None:
    """`cast=萧决,师兄` 且「师兄」唯一指向萧决：他是一个人、一行。

    不去重的话重复 id 会撞 store 的 `_reject_dups`，作者会收到一个他看不懂的错误。
    """
    store = build([], extra_aliases={"师兄": [XIAO_JUE]})

    resolved = resolve_cast(store, PID, ["萧决", "师兄"])

    assert resolved.ids == [XIAO_JUE.id]
    assert resolved.unresolved == []
    assert resolved.complete is True


def test_require_resolved_cast_is_the_draft_gate() -> None:
    """起草侧的那道断言：**歧义称呼要弹给作者，不是拿着退化约束去起草。**

    退化约束防得住泄漏，但它给 Writer 的是「全部秘密都不许提」——写出来作者也不要。
    panel/knowledge.py 说这个问题「要在 UI 上问作者」，在此之前没有东西把它传到 UI。
    """
    store = build([], extra_aliases=AMBIGUOUS_SHIXIONG)

    ambiguous = scene_constraints(store, PID, 152, ["师兄"], secrets=[BLOODLINE.id])
    with pytest.raises(UnresolvedCast, match="师兄"):
        ambiguous.require_resolved_cast()

    # 解析干净的那份不抛——否则这条守卫会把正常起草也拦了。
    scene_constraints(store, PID, 152, ["萧决"], secrets=[BLOODLINE.id]).require_resolved_cast()
