"""行内续写（[ADR 0015](../docs/adr/0015-inline-continuation-is-a-short-draft.md)）。

三条不变式，坏掉的样子各不相同：

1. **不知道谁在场 ⇒ 全禁，而不是不给写。** 方向错了就是泄漏：漏禁一条的代价是崩人设，
   多禁一条的代价只是「这一段写得保守」。
2. **退化态是另一个类型，不是 `ResolvedConstraints` 的一个弱化实例。** 后者存在即证明
   「约束是算出来的」；一旦空 cast 能穿过去，`panel/constraints.py` 记的那次
   fail-open 病史立刻能重演，而且没有任何东西会红。
3. **`ResolvedConstraints` 那条路径逐字节不变。** kill-gate 与产品起草共用 `assemble()`，
   这次改动只许**加一个分支**，不许挪动原路径的任何一个字节（EVAL_PROTOCOL §2）。
"""

from __future__ import annotations

import pytest
from test_knowledge import (
    BLOODLINE,
    GU_QINGYIN,
    PID,
    XIAO_JUE,
    XUANTIE,
    build,
    edge,
)

from novel_harness.draft.assemble import (
    CONTINUATION_GOAL,
    UNKNOWN_CAST_LINE,
    PromptForm,
    assemble,
)
from novel_harness.draft.context import (
    ResolvedConstraints,
    UnknownCastConstraints,
    resolve_constraints,
    unknown_cast_constraints,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.graph import EdgeType

SHORT = LengthSpec(language=DraftLanguage.ZH, min_units=80, target_units=150, max_units=300)
"""续写档：一两段。`LengthSpec` 的下限是 `ge=1`，所以这是合法取值——ADR 0015 D1
说的「后端一个字都不用改」就是这一行的意思。"""


def _store():
    return build([edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 88)])


# ── 1. 不知道谁在场 ⇒ 全禁 ────────────────────────────────────────────────


def test_unknown_cast_forbids_every_secret() -> None:
    """空 cast 的答案是「全禁」，不是「无约束」，也不是抛异常。"""
    ctx = unknown_cast_constraints(_store(), PID, 152, secrets=[BLOODLINE.id, XUANTIE.id])

    assert isinstance(ctx, UnknownCastConstraints)
    assert sorted(ctx.secret_labels) == sorted(["血脉秘密", "玄铁令下落"])


def test_a_known_cast_is_strictly_less_restrictive_than_not_knowing() -> None:
    """对照：知道在场是谁 ⇒ 约束收紧。

    没有这一条，上面那条也可能是因为「这个函数恒返回全部秘密」而绿的——
    而「恒返回全部秘密」正好是退化值自己的形状，两者从出参上分不开。
    """
    store = _store()
    unknown = unknown_cast_constraints(store, PID, 152, secrets=[BLOODLINE.id])
    known = resolve_constraints(store, PID, 152, [XIAO_JUE.name], secrets=[BLOODLINE.id])

    assert unknown.secret_labels == ["血脉秘密"]  # 不知道谁在场 → 禁
    assert known.secret_labels == []  # 萧决第 88 章就知道了 → 不必禁


def test_forbidden_entities_stay_exact_because_they_never_depended_on_cast() -> None:
    """未来实体是按章号算的，与在场无关——退化态里这一项**不该**跟着退化。"""
    ctx = unknown_cast_constraints(_store(), PID, 152, secrets=[BLOODLINE.id])
    assert ctx.forbidden_names == ["幽泉窟"]


# ── 2. 退化态是另一个类型 ─────────────────────────────────────────────────


def test_the_degraded_type_cannot_masquerade_as_the_resolved_one() -> None:
    """两个类型不可互换，而且退化态**没有 cast 字段**——不许用空列表冒充「没有人在场」。"""
    ctx = unknown_cast_constraints(_store(), PID, 152, secrets=[BLOODLINE.id])

    assert not isinstance(ctx, ResolvedConstraints)
    assert not hasattr(ctx, "cast")
    assert not hasattr(ctx, "matrix")


def test_resolved_constraints_still_refuses_an_empty_cast() -> None:
    """**这条是上面那条的反面守卫**：加了退化型之后，原来那道闸不许跟着松。

    松了的形态是 `ResolvedConstraints(cast=[])` 能构造出来 —— 那一刻
    「拿到这个类型 = 约束是算出来的」这句话就不再为真，而下游全靠它。
    """
    with pytest.raises(Exception):
        ResolvedConstraints(chapter=1, cast=[], matrix=None)  # type: ignore[arg-type]


def test_the_unknown_cast_line_states_the_consequence_not_just_the_gap() -> None:
    """「在场：未知」单独出现时，模型最自然的反应是自己猜一个。约束得跟在同一句里。"""
    assert "未知" in UNKNOWN_CAST_LINE
    assert "不得说破" in UNKNOWN_CAST_LINE


# ── 3. 渲染：退化态进得了 prompt，且原路径逐字节不变 ──────────────────────


def test_the_degraded_context_renders_and_carries_the_ban() -> None:
    ctx = unknown_cast_constraints(_store(), PID, 152, secrets=[BLOODLINE.id])

    messages = assemble(ctx, form=PromptForm.X1, goal=CONTINUATION_GOAL, length=SHORT)
    body = "\n".join(m["content"] for m in messages)

    assert UNKNOWN_CAST_LINE in body
    assert "血脉秘密" in body  # 禁的是显示名……
    assert "玄血蛊" not in body  # ……永远不是内容 tell


def test_the_degraded_context_injects_no_knowledge_matrix() -> None:
    """没有 cast 就没有矩阵行。渲一个空矩阵等于说「查过了，没人知道任何事」——那是假的。"""
    ctx = unknown_cast_constraints(_store(), PID, 152, secrets=[BLOODLINE.id])
    for form in (PromptForm.X1, PromptForm.X2):
        assert "认知边界" not in assemble(
            ctx, form=form, goal=CONTINUATION_GOAL, length=SHORT
        )[-1]["content"]


def test_the_resolved_path_is_byte_identical_after_adding_the_branch() -> None:
    """**本次改动只许加一个分支。** 这条钉住 kill-gate 走的那条路径一个字节没动。

    坏掉的形态：有人为了让两条路径「长得一样」而顺手改了 `_base()` 里在场行的措辞——
    X0/X1/X2 三臂的 prompt 全变，而 `runs/*.jsonl` 里的历史结果再也不可比。
    """
    ctx = resolve_constraints(_store(), PID, 152, [GU_QINGYIN.name], secrets=[BLOODLINE.id])
    messages = assemble(ctx, form=PromptForm.X0, goal="试探", length=SHORT)

    assert messages[-1]["content"] == "【在场】\n顾清音\n\n【这一场要写】\n试探"


def test_the_continuation_goal_is_a_backend_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0015 D3：续写的提示语住在后端。它是常量，不是某个调用方传进来的默认值。"""
    assert CONTINUATION_GOAL.strip()
    assert "顺着上文" in CONTINUATION_GOAL
