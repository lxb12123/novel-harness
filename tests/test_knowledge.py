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
    knowledge_matrix,
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


@pytest.mark.parametrize(
    ("chapter", "expected"),
    [
        (87, KnowledgeState.UNKNOWN),
        (88, KnowledgeState.KNOWS),
        (152, KnowledgeState.KNOWS),
    ],
)
def test_knows_since_chapter_88(chapter: int, expected: KnowledgeState) -> None:
    store = build([edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88)])

    matrix = knowledge_matrix(store, PID, chapter, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    cell = matrix.cell(XIAO_JUE.id, BLOODLINE.id)
    assert cell.state is expected
    # 「他从第 88 章起知道」——ch87 那格不许带 since_chapter，否则面板会渲染出
    # 「✗ 不知道 (ch88)」这种自相矛盾的东西。
    assert cell.since_chapter == (88 if expected is KnowledgeState.KNOWS else None)


def test_closed_world_unknown_cell_is_materialized() -> None:
    """闭世界：零 KNOWS 边 ⇒ 每一格都是 UNKNOWN，且**每一格都存在**。

    「没有这一格」和「他不知道」是两个意思。面板上少一格 = 作者以为系统没意见 = 说漏嘴。
    """
    store = build([])

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id, GU_QINGYIN.id])

    assert len(matrix.cells) == len(matrix.characters) * len(matrix.secrets)
    assert {c.state for c in matrix.cells} == {KnowledgeState.UNKNOWN}


def test_believes_carries_believed_value_not_a_guess() -> None:
    store = build(
        [
            edge(
                LI_GUANJIA.id,
                BLOODLINE.id,
                EdgeType.BELIEVES,
                103,
                believed_value="以为已泄露",
            )
        ]
    )

    cell = knowledge_matrix(store, PID, 152, [LI_GUANJIA.id]).cell(LI_GUANJIA.id, BLOODLINE.id)

    assert cell.state is KnowledgeState.BELIEVES
    assert cell.believed_value == "以为已泄露"
    assert cell.since_chapter == 103


def test_cast_order_is_the_panel_row_order() -> None:
    """行序 = 作者在场景块里写的 cast 顺序。面板逐格核对靠它对得上。"""
    store = build([])
    cast = [LI_GUANJIA.id, XIAO_JUE.id, GU_QINGYIN.id]

    matrix = knowledge_matrix(store, PID, 152, cast)

    assert [c.id for c in matrix.characters] == cast


def test_provisional_edge_does_not_leak_into_canon_matrix() -> None:
    """PROVISIONAL「永不断言为真」：默认那次查询里它必须完全不存在。"""
    provisional = InformationScope.PROVISIONAL
    store = build([edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88, scope=provisional)])

    canon = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])
    grey = knowledge_matrix(
        store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id], scope=provisional
    )

    assert canon.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN
    # 灰显是**第二次调用**，不是混在同一份结果里。
    assert grey.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.KNOWS


def test_stale_evidence_stops_firing() -> None:
    """ADR 0006：依据被作者改没了 ⇒ 立刻停火。这条边等于不存在。"""
    store = build(
        [
            edge(
                XIAO_JUE.id,
                BLOODLINE.id,
                EdgeType.KNOWS,
                88,
                evidence_status=EvidenceStatus.STALE,
            )
        ]
    )

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN


def test_retracted_edge_stops_firing() -> None:
    """同章更正撤回的边不算数（status 与时态正交）。"""
    store = build(
        [edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88, status=EdgeStatus.RETRACTED)]
    )

    matrix = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[BLOODLINE.id])

    assert matrix.cell(XIAO_JUE.id, BLOODLINE.id).state is KnowledgeState.UNKNOWN


@pytest.mark.parametrize("scope", [InformationScope.PLANNED, InformationScope.REJECTED])
def test_planned_and_rejected_are_unreadable(scope: InformationScope) -> None:
    """改 7：泄漏在**物理上**不可能，不是「大概率不会」。PLANNED 没有读路径。"""
    store = build([])

    with pytest.raises(ValueError, match="不可读"):
        knowledge_matrix(store, PID, 152, [XIAO_JUE.id], scope=scope)


# ══════════════════════════════════════════════════════════════════════════
# panel/constraints.py：PLANNED 进 prompt 的唯一闸门
# ══════════════════════════════════════════════════════════════════════════


def test_must_not_reveal_covers_secrets_someone_does_not_know() -> None:
    """§3.2 的那个场景：萧决全知道，顾清音全不知道，李管家持错误认知。"""
    store = build(
        [
            edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88),
            edge(XIAO_JUE.id, XUANTIE.id, EdgeType.KNOWS, 120),
            edge(LI_GUANJIA.id, BLOODLINE.id, EdgeType.BELIEVES, 103, believed_value="以为已泄露"),
        ]
    )

    c = scene_constraints(
        store,
        PID,
        152,
        [XIAO_JUE.name, GU_QINGYIN.name, LI_GUANJIA.name],
        secrets=[BLOODLINE.id, XUANTIE.id],
    )

    assert [n.name for n in c.must_not_reveal] == ["血脉秘密", "玄铁令下落"]
    assert c.unresolved_cast == []


def test_secret_known_to_everyone_present_is_not_a_constraint() -> None:
    """全场都知道的秘密不必藏——否则 must_not_reveal 会退化成「全部秘密」，
    而一个永远说「什么都别说」的约束等于没有约束。"""
    store = build(
        [
            edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88),
            edge(GU_QINGYIN.id, BLOODLINE.id, EdgeType.KNOWS, 90),
        ]
    )

    c = scene_constraints(store, PID, 152, [XIAO_JUE.name, GU_QINGYIN.name], secrets=[BLOODLINE.id])

    assert c.must_not_reveal == []


def test_believes_still_counts_as_must_not_reveal() -> None:
    """李管家「以为已泄露」——把真相说破同样是崩人设。BELIEVES ≠ KNOWS。"""
    store = build(
        [edge(LI_GUANJIA.id, BLOODLINE.id, EdgeType.BELIEVES, 103, believed_value="以为已泄露")]
    )

    c = scene_constraints(store, PID, 152, [LI_GUANJIA.name], secrets=[BLOODLINE.id])

    assert [n.name for n in c.must_not_reveal] == ["血脉秘密"]


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


def test_constraints_never_carry_future_plot_content() -> None:
    """闸门的形状本身：约束只说「不许说什么」，不说「未来发生了什么」。

    这条断言看着像洁癖，它不是——改 7 要求把硬约束下沉为 filter。一旦
    ForbiddenEntity 上出现一个装着 PLANNED 边内容的字段，某个下午就会有人把它
    拼进 prompt，而「未来剧情泄漏率结构上恒为 0」这句话当场作废。
    """
    from novel_harness.panel import ForbiddenEntity, SceneConstraints

    assert set(ForbiddenEntity.model_fields) == {"node", "first_appears_chapter", "surfaces"}
    assert set(SceneConstraints.model_fields) == {
        "chapter",
        "unresolved_cast",
        "must_not_reveal",
        "forbidden_entities",
    }
    # 形状之外还要钉住类型：`node: Node` 会把 NodeProps 的 extras 整个带出闸门，
    # 而这条测试正是「结构上恒为 0」那个断言的守卫。见 test_gate_never_serializes_node_props。
    assert ForbiddenEntity.model_fields["node"].annotation is NodeRef


# ══════════════════════════════════════════════════════════════════════════
# 闸门的两个 fail-open 入口（这一节的每一条在修之前都是红的）
# ══════════════════════════════════════════════════════════════════════════


def test_gate_never_serializes_node_props() -> None:
    """**「结构上恒为 0」的那条断言。**

    `NodeProps` 是 `extra="allow"`，所以作者写在秘密节点上的 `twist`、写在未来地点上的
    `plot_note` 会挂在 `Node.props.model_extra` 里。闸门的出参只要还带着完整的 `Node`，
    `model_dump_json()`（API 出参 / prompt 拼装必然这么干）就把它们全吐进
    第 152 章的 Writer prompt——而 forbidden_entities 的节点按定义就是关于未来的。

    没有这条测试，下一个人把 `node: NodeRef` 改回 `node: Node` 是绿的。
    """
    bloodline = node(
        "secret:demo:leak1", NodeLabel.SECRET, "血脉秘密", twist="萧决其实是魔尊之子，第 200 章揭晓"
    )
    youquan = node(
        "location:demo:leak2",
        NodeLabel.LOCATION,
        "幽泉窟",
        first_appears_chapter=200,
        plot_note="萧决在此被顾清音所杀",
    )
    store = FakeGraph([XIAO_JUE, bloodline, youquan], [])

    c = scene_constraints(store, PID, 152, [XIAO_JUE.name], secrets=[bloodline.id])
    blob = c.model_dump_json()
    matrix_blob = knowledge_matrix(store, PID, 152, [XIAO_JUE.id], secrets=[bloodline.id])

    # 名字要在（面板 §3.2 就印着「must_not_reveal：血脉秘密」），内容不许在。
    assert "血脉秘密" in blob and "幽泉窟" in blob
    assert "萧决其实是魔尊之子" not in blob
    assert "萧决在此被顾清音所杀" not in blob
    # 同一刀补在矩阵上：D 分区拼 prompt 时会序列化整个矩阵。
    assert "萧决其实是魔尊之子" not in matrix_blob.model_dump_json()


def test_ambiguous_cast_fails_closed_not_open() -> None:
    """**原则 11 的那条 fatal。**

    作者写 `cast=顾清音,师兄`，而「师兄」映射到 2 个人（PLAN §3.1 点名的场景）。
    李管家不知道血脉秘密——他要是被静默丢掉，`any(state != KNOWS)` 就只剩顾清音，
    而她知道，于是血脉秘密从 must_not_reveal 里消失，Writer prompt 拿到「无需保密」。
    **错误方向必须指向误报（多禁一条 = 少写一段），不是指向泄漏（= 崩人设）。**
    """
    store = build(
        [edge(GU_QINGYIN.id, BLOODLINE.id, EdgeType.KNOWS, 10)], extra_aliases=AMBIGUOUS_SHIXIONG
    )

    c = scene_constraints(store, PID, 152, ["顾清音", "师兄"], secrets=[BLOODLINE.id])

    assert c.unresolved_cast == ["师兄"]
    assert [n.name for n in c.must_not_reveal] == ["血脉秘密"]
    # 而这正是「师兄 = 李管家」时的正确答案——退化值在这一场恰好等于真答案。
    correct = scene_constraints(
        store, PID, 152, ["顾清音", "李管家"], secrets=[BLOODLINE.id]
    )
    assert [n.name for n in correct.must_not_reveal] == ["血脉秘密"]
    assert correct.unresolved_cast == []


def test_unknown_cast_surface_also_fails_closed() -> None:
    """歧义和「查无此人」走同一个出口：两者都是「我不知道这一场有谁」。"""
    store = build([edge(GU_QINGYIN.id, BLOODLINE.id, EdgeType.KNOWS, 10)])

    c = scene_constraints(store, PID, 152, ["顾清音", "查无此人"], secrets=[BLOODLINE.id])

    assert c.unresolved_cast == ["查无此人"]
    assert [n.name for n in c.must_not_reveal] == ["血脉秘密"]


def test_empty_cast_forbids_everything() -> None:
    """同族的第二个入口：`Scene.cast` 的默认值就是 `[]`。

    作者写一个没有 `cast=` 的场景块 → `any()` over empty = False → 零约束。
    **「一个人都没有」和「全员都知道」在出参上曾经完全不可区分。**
    """
    store = build([edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88)])

    empty = scene_constraints(store, PID, 152, [], secrets=[BLOODLINE.id, XUANTIE.id])
    everyone_knows = scene_constraints(store, PID, 152, [XIAO_JUE.name], secrets=[BLOODLINE.id])

    assert [n.name for n in empty.must_not_reveal] == ["血脉秘密", "玄铁令下落"]
    # 对照：真的「全员都知道」时才是零约束，两者不再同形。
    assert everyone_knows.must_not_reveal == []


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


def test_matrix_carries_unresolved_cast_to_the_panel() -> None:
    """面板少一行不许是静默的：作者声明了 3 个人、画出 2 行，他会以为系统对第 3 个
    人没意见。`_check_complete` 拦不到整行缺失——它只校验已知行 × 已知列。"""
    store = build([], extra_aliases=AMBIGUOUS_SHIXIONG)
    resolved = resolve_cast(store, PID, ["顾清音", "师兄"])

    matrix = knowledge_matrix(
        store, PID, 152, resolved.ids, secrets=[BLOODLINE.id], unresolved=resolved.unresolved
    )

    assert matrix.unresolved_cast == ["师兄"]
    assert "师兄" in matrix.model_dump_json()
