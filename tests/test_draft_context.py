"""`draft/context.py` —— 「已解析」这件事在类型层的守卫。

核心不变式，坏掉的样子各不相同：

1. **构造成功 ⇒ cast 无歧义。** 歧义要在构造时弹给作者，不能静默用 `scene_constraints`
   的 fail-closed 退化值（「全部秘密」）——那份退化值防得住泄漏，但拿它起草会让 Writer
   收到「什么都不许提」，拿它跑 kill-gate 会把「全禁」当基线、污染臂间比较。
2. **空 cast 也算歧义。** 这是 `SceneConstraints.require_resolved_cast()` **看不见**的那条
   退化路径（它只读 `unresolved_cast`），本文件用一条对照测试把那个洞钉出来。
3. **出参里没有 tell、没有 props。** 秘密进 prompt 的是显示名（`血脉秘密`），
   内容 tell（`玄血蛊`）和 `props.twist` 一个字符都不许出现。

复用 `test_knowledge.FakeGraph`（同 `test_store_conformance.py` 的做法）：它是 StoryGraph
契约的参考实现，`scene_constraints` 要的 `resolve` + `knowledge_matrix` 它都按契约实现了。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_knowledge import (
    AMBIGUOUS_SHIXIONG,
    BLOODLINE,
    GU_QINGYIN,
    LI_GUANJIA,
    PID,
    XIAO_JUE,
    XUANTIE,
    YOUQUAN,
    FakeGraph,
    build,
    edge,
    node,
)

from novel_harness.draft.context import ResolvedConstraints, resolve_constraints
from novel_harness.graph import EdgeType, NodeLabel
from novel_harness.panel import UnresolvedCast, scene_constraints
from novel_harness.panel.constraints import scene_view

TWIST = "萧决其实是魔尊之子，第 200 章揭晓"
"""挂在秘密节点上的 `props` 额外字段。`NodeProps` 是 `extra="allow"`，所以它真的存在，
真的会被 `model_dump_json()` 吐出来——除非出参已经收窄成 `NodeRef`。"""

TELL = "玄血蛊"
"""血脉秘密的**内容 tell**（EVAL_PROTOCOL §4 的第 1 条边界）。它是判分器那一侧的词：
进了 prompt，X1/X2 就会命中自己写进去的东西，Δ 翻负 → 裁决表读出假的「KILL」。"""


# ══════════════════════════════════════════════════════════════════════════
# ① 正常路径
# ══════════════════════════════════════════════════════════════════════════


def test_happy_path_narrows_a_scene() -> None:
    """幽泉窟第 200 章才首现 → 这一场不许出现；在场解析成了唯一一个人。"""
    store = build([])

    ctx = resolve_constraints(store, PID, 152, [GU_QINGYIN.name])

    assert ctx.chapter == 152
    assert ctx.cast == ["顾清音"]
    assert [r.name for r in ctx.characters] == ["顾清音"]
    assert ctx.forbidden_names == ["幽泉窟"]
def test_of_narrows_the_very_same_constraints() -> None:
    """`of()` 是**收窄**，不是重算。

    kill-gate 的 runner 要拿同一份 `SceneConstraints` 既判分（`score_against`）又起草，
    「禁忌集只有一个来源」必须在对象层成立（EVAL_PROTOCOL §3），不能靠两次查询碰巧一致。
    """
    store = build([])
    cast = [GU_QINGYIN.name, LI_GUANJIA.name]

    view = scene_view(store, PID, 152, cast)
    ctx = ResolvedConstraints.of(view, cast)

    sc = view.constraints
    assert ctx.forbidden_entities == sc.forbidden_entities
    assert ctx.chapter == sc.chapter
    # 在场那几个 ref 也是**同一份**，不是重算的：下游拿不到 `resolve_cast`
    # （第 4 道 arch-guard 的 WRITER_BANNED），所以这一份必须从这儿传下去。
    assert ctx.characters == view.characters


def test_the_degraded_state_is_unrepresentable() -> None:
    """类型里没有 `unresolved_cast` —— 下游**问不出**「这份约束是不是退化值」。

    这条不是形式主义：它是「薄封装」和「多一层转发」的分界线。字段还在的话，
    `assemble()` 就仍然得（记得）自己判一次，而 ARCHITECTURE §10.5 说的正是
    「必须记得调」这件事本身是最弱的一环。
    """
    assert "unresolved_cast" not in ResolvedConstraints.model_fields


# ══════════════════════════════════════════════════════════════════════════
# ② 歧义 → 抛，不是静默退化
# ══════════════════════════════════════════════════════════════════════════


def test_ambiguous_cast_raises_instead_of_degrading() -> None:
    """「师兄」映射到 2 个人（PLAN §3.1 点名的场景）：弹给作者问，不替他猜。

    同时钉住**面板侧不受影响**：`scene_constraints` 仍然返回一份退化约束（它得渲染，
    抛异常会让头牌整片黑掉，且作者修不了一个异常）。两侧的行为差异是有意的。
    """
    store = build(
        [edge(GU_QINGYIN.id, BLOODLINE.id, EdgeType.KNOWS, 10)], extra_aliases=AMBIGUOUS_SHIXIONG
    )
    cast = [GU_QINGYIN.name, "师兄"]

    degraded = scene_constraints(store, PID, 152, cast, secrets=[BLOODLINE.id])
    assert degraded.unresolved_cast == ["师兄"]

    with pytest.raises(UnresolvedCast, match="师兄"):
        resolve_constraints(store, PID, 152, cast, secrets=[BLOODLINE.id])


def test_unknown_cast_surface_raises_too() -> None:
    """「查无此人」和歧义走同一个出口：两者都是「我不知道这一场有谁」。"""
    store = build([edge(GU_QINGYIN.id, BLOODLINE.id, EdgeType.KNOWS, 10)])

    with pytest.raises(UnresolvedCast, match="查无此人"):
        resolve_constraints(store, PID, 152, [GU_QINGYIN.name, "查无此人"])


def test_empty_cast_raises_the_hole_require_resolved_cast_misses() -> None:
    """**这条是这个封装不只是转发的证据。**

    `Scene.cast` 的默认值是 `[]`（作者写了个没有 `cast=` 的场景块）→ `ResolvedCast.complete`
    为假 → `must_not_reveal` 退化成全部秘密。而 `require_resolved_cast()` 只读
    `unresolved_cast`，**它对这条路径完全无感**——下面第一段断言就是把那个洞钉出来。
    """
    store = build([edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88)])

    degraded_view = scene_view(store, PID, 152, [], secrets=[BLOODLINE.id, XUANTIE.id])
    degraded = degraded_view.constraints
    degraded.require_resolved_cast()  # ← 不抛。这就是那个洞。

    with pytest.raises(UnresolvedCast, match="cast"):
        ResolvedConstraints.of(degraded_view, [])
    with pytest.raises(UnresolvedCast, match="cast"):
        resolve_constraints(store, PID, 152, [], secrets=[BLOODLINE.id])


def test_direct_construction_still_rejects_an_empty_cast() -> None:
    """绕过 `of()` 裸构造也拿不到一份空 cast 的「已解析」约束。

    Python 拦不住裸构造，但至少不变式里可判定的那一半要跟着类型走，而不是只跟着构造函数走。
    """
    with pytest.raises(ValidationError):
        ResolvedConstraints(chapter=152, cast=[])


# ══════════════════════════════════════════════════════════════════════════
# ③ 出参里没有 tell、没有 props
# ══════════════════════════════════════════════════════════════════════════


def _store_with_a_tell() -> FakeGraph:
    """血脉秘密带一个 `props.twist` 和一个非 canonical 的内容 tell「玄血蛊」。"""
    bloodline = node(BLOODLINE.id, NodeLabel.SECRET, "血脉秘密", twist=TWIST)
    return FakeGraph(
        [XIAO_JUE, GU_QINGYIN, bloodline, YOUQUAN],
        [edge(XIAO_JUE.id, bloodline.id, EdgeType.KNOWS, 88)],
        extra_aliases={TELL: [bloodline]},
    )


def test_no_tell_and_no_props_reach_the_output() -> None:
    """进 prompt 的是标签（`血脉秘密`），不是 tell（`玄血蛊`），更不是 `props.twist`。

    tell 进了 X1/X2 的 prompt → 两臂 100% 命中自己写进去的词 → `Δ` 翻负 →
    预注册裁决表读出「KILL 起草线」：**把一个本来对的项目砍掉，而全程没有东西会红。**
    """
    store = _store_with_a_tell()

    ctx = resolve_constraints(store, PID, 152, [GU_QINGYIN.name])
    dumped = ctx.model_dump_json()

    assert [r.name for r in ctx.characters] == ["顾清音"]
    assert TWIST not in dumped
    # 同一份东西经 dict 出去也不许漏（prompt 拼装未必走 JSON）。
    assert TELL not in str(ctx.model_dump())
    assert TWIST not in str(ctx.model_dump())


def test_the_tell_really_is_in_the_graph() -> None:
    """上一条的非空证明：tell 和 twist **确实存在**于图里，是被收窄挡掉的，不是本来就没有。

    没有这一条，只要哪天 fixture 少写了一个别名，那条测试就会永远绿着通过。
    """
    store = _store_with_a_tell()

    hit = store.resolve(PID, [TELL])[0].unique_node
    assert hit is not None and hit.id == BLOODLINE.id
    assert TWIST in hit.model_dump_json()


def test_a_full_node_is_rejected_by_the_type() -> None:
    """`characters` 只收 `NodeRef`。塞一个完整的 `Node` 进来 pydantic 当场拒。

    ARCHITECTURE §10.5 第 3 条：`NodeProps` 是 `extra="allow"`，一个完整 `Node` 会把
    `props.twist` 顺着序列化进 prompt——**保密清单自己泄密**。
    """
    with pytest.raises(ValidationError):
        ResolvedConstraints(
            chapter=152,
            cast=[GU_QINGYIN.name],
            characters=[node(GU_QINGYIN.id, NodeLabel.CHARACTER, "顾清音", twist=TWIST)],
        )


def test_future_entity_names_are_not_the_same_promise() -> None:
    """诚实说明的那一半：未来实体的名字**自身就是 tell**，它必然进 prompt。

    `血枭盟` / `幽泉窟` 既是显示名也是检测词，所以 X1/X2 有 echo 风险——EVAL_PROTOCOL §3
    因此让 `future_leak` 只作描述性地板、**不主导裁决**。把 KNOWS 那侧「标签 ⟂ tell」的
    直觉搬过来用，就会读错 gate 的结论。
    """
    store = _store_with_a_tell()

    ctx = resolve_constraints(store, PID, 152, [GU_QINGYIN.name], secrets=[BLOODLINE.id])

    assert ctx.forbidden_names == [YOUQUAN.name]
    assert YOUQUAN.name in ctx.model_dump_json()  # 它进 prompt 是设计，不是泄漏
