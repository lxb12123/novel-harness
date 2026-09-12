"""`draft/context.py` —— 「已解析」这件事在类型层的守卫。

核心不变式，坏掉的样子各不相同：

1. **构造成功 ⇒ cast 无歧义。** 歧义要在构造时弹给作者，不能静默丢掉一个解析不出的人。
2. **空 cast 也算歧义。** 这是 `SceneConstraints.require_resolved_cast()` **看不见**的那条
   路径（它只读 `unresolved_cast`），本文件用一条对照测试把那个洞钉出来。
3. **出参里没有别名、没有 props。** 进 prompt 的是显示名，作者写在节点上的 `twist`
   和非 canonical 别名一个字符都不许出现。

⚠️ **1 和 2 的理由 2026-08-25 换过**（ADR 0039）：原来它们说的是「退化值 =
`must_not_reveal` 全部秘密」，那是一条 fail-closed 的安全性质。秘密下线之后
`SceneConstraints` 只剩 `unresolved_cast`（`forbidden_entities` 也在 2026-08-31 删了，
[ADR 0041](../../docs/adr/0041-forbidden-entities-cut.md)）——
**空 cast 今天不再让任何东西退化**。留下来的理由见 `draft/context.py` 第二节：
一份你没有的在场名单，不许拿空列表冒充着发给模型。

复用 `test_fake_graph.FakeGraph`（同 `test_store_conformance.py` 的做法）：它是 StoryGraph
契约的参考实现，`scene_view` 要的 `resolve` 它按契约实现了。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_fake_graph import (
    AMBIGUOUS_SHIXIONG,
    BEIHUANG,
    GU_QINGYIN,
    LI_GUANJIA,
    PID,
    QINGYUN,
    XIAO_JUE,
    YOUQUAN,
    FakeGraph,
    build,
    edge,
    node,
)

from novel_harness.draft.context import ResolvedConstraints, resolve_constraints
from novel_harness.graph import EdgeType, NodeLabel
from novel_harness.panel import UnresolvedCast
from novel_harness.panel.constraints import scene_view

TWIST = "萧决其实是魔尊之子，第 200 章揭晓"
"""挂在节点上的 `props` 额外字段。`NodeProps` 是 `extra="allow"`，所以它真的存在，
真的会被 `model_dump_json()` 吐出来——除非出参已经收窄成 `NodeRef`。"""

TELL = "玄血蛊"
"""一条**非 canonical 别名**。别名里装着作者的意图（ADR 0004：别名差异「是 canon，
不是噪声」），所以它跟 `props` 一样不许进 prompt——进 prompt 的只有显示名。"""


# ══════════════════════════════════════════════════════════════════════════
# ① 正常路径
# ══════════════════════════════════════════════════════════════════════════


def test_happy_path_narrows_a_scene() -> None:
    """在场解析成了唯一一个人。"""
    store = build([])

    ctx = resolve_constraints(store, PID, 152, [GU_QINGYIN.name])

    assert ctx.chapter == 152
    assert ctx.cast == ["顾清音"]
    assert [r.name for r in ctx.characters] == ["顾清音"]


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

    同时钉住**面板侧不受影响**：`scene_view` 仍然返回一份退化约束（它得渲染，
    抛异常会让头牌整片黑掉，且作者修不了一个异常）。两侧的行为差异是有意的。
    """
    store = build(
        [edge(GU_QINGYIN.id, QINGYUN.id, EdgeType.LOCATED_AT, 10)],
        extra_aliases=AMBIGUOUS_SHIXIONG,
    )
    cast = [GU_QINGYIN.name, "师兄"]

    degraded = scene_view(store, PID, 152, cast).constraints
    assert degraded.unresolved_cast == ["师兄"]

    with pytest.raises(UnresolvedCast) as exc:
        resolve_constraints(store, PID, 152, cast)
    assert exc.value.code == "unresolved_cast_ambiguous"
    assert "师兄" in exc.value.params["unresolved"]


def test_unknown_cast_surface_raises_too() -> None:
    """「查无此人」和歧义走同一个出口：两者都是「我不知道这一场有谁」。"""
    store = build([edge(GU_QINGYIN.id, QINGYUN.id, EdgeType.LOCATED_AT, 10)])

    with pytest.raises(UnresolvedCast) as exc:
        resolve_constraints(store, PID, 152, [GU_QINGYIN.name, "查无此人"])
    assert exc.value.code == "unresolved_cast_ambiguous"
    assert "查无此人" in exc.value.params["unresolved"]


def test_empty_cast_raises_the_hole_require_resolved_cast_misses() -> None:
    """**这条是这个封装不只是转发的证据。**

    `Scene.cast` 的默认值是 `[]`（作者写了个没有 `cast=` 的场景块）→ `ResolvedCast.complete`
    为假。而 `require_resolved_cast()` 只读 `unresolved_cast`，**它对这条路径完全无感**——
    下面第一段断言就是把那个洞钉出来。
    """
    store = build([edge(XIAO_JUE.id, BEIHUANG.id, EdgeType.LOCATED_AT, 88)])

    degraded_view = scene_view(store, PID, 152, [])
    degraded = degraded_view.constraints
    degraded.require_resolved_cast()  # ← 不抛。这就是那个洞。

    with pytest.raises(UnresolvedCast, match="cast"):
        ResolvedConstraints.of(degraded_view, [])
    with pytest.raises(UnresolvedCast, match="cast"):
        resolve_constraints(store, PID, 152, [])


def test_direct_construction_still_rejects_an_empty_cast() -> None:
    """绕过 `of()` 裸构造也拿不到一份空 cast 的「已解析」约束。

    Python 拦不住裸构造，但至少不变式里可判定的那一半要跟着类型走，而不是只跟着构造函数走。
    """
    with pytest.raises(ValidationError):
        ResolvedConstraints(chapter=152, cast=[])


# ══════════════════════════════════════════════════════════════════════════
# ③ 出参里没有别名、没有 props
# ══════════════════════════════════════════════════════════════════════════


def _store_with_a_tell() -> FakeGraph:
    """青云城主府带一个 `props.twist` 和一个非 canonical 别名「玄血蛊」。"""
    poisoned = node(QINGYUN.id, NodeLabel.LOCATION, "青云城主府", twist=TWIST)
    return FakeGraph(
        [XIAO_JUE, GU_QINGYIN, poisoned, YOUQUAN],
        [edge(XIAO_JUE.id, poisoned.id, EdgeType.LOCATED_AT, 88)],
        extra_aliases={TELL: [poisoned]},
    )


def test_no_alias_and_no_props_reach_the_output() -> None:
    """进 prompt 的是显示名，不是别名（`玄血蛊`），更不是 `props.twist`。"""
    store = _store_with_a_tell()

    ctx = resolve_constraints(store, PID, 152, [GU_QINGYIN.name])
    dumped = ctx.model_dump_json()

    assert [r.name for r in ctx.characters] == ["顾清音"]
    assert TWIST not in dumped
    # 同一份东西经 dict 出去也不许漏（prompt 拼装未必走 JSON）。
    assert TELL not in str(ctx.model_dump())
    assert TWIST not in str(ctx.model_dump())


def test_the_tell_really_is_in_the_graph() -> None:
    """上一条的非空证明：别名和 twist **确实存在**于图里，是被收窄挡掉的，不是本来就没有。

    没有这一条，只要哪天 fixture 少写了一个别名，那条测试就会永远绿着通过。
    """
    store = _store_with_a_tell()

    hit = store.resolve(PID, [TELL])[0].unique_node
    assert hit is not None and hit.id == QINGYUN.id
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


# ⚠️ **2026-08-31：`test_future_entity_names_are_not_the_same_promise` 删了**——
# 它测的是「未来实体名字必然进 prompt，这是设计不是泄漏」，而 `forbidden_names`/
# `forbidden_entities` 整个不存在了，这条设计也就不再成立（见 ADR 0041）。
