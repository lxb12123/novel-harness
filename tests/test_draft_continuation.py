"""行内续写（[ADR 0015](../docs/adr/0015-inline-continuation-is-a-short-draft.md)）。

三条不变式，坏掉的样子各不相同：

1. **不知道谁在场 ⇒ 全禁，而不是不给写。** 方向错了就是泄漏：漏禁一条的代价是崩人设，
   多禁一条的代价只是「这一段写得保守」。
2. **退化态是另一个类型，不是 `ResolvedConstraints` 的一个弱化实例。** 后者存在即证明
   「约束是算出来的」；一旦空 cast 能穿过去，`panel/constraints.py` 记的那次
   fail-open 病史立刻能重演，而且没有任何东西会红。
3. **`ResolvedConstraints` 那条路径逐字节不变。** kill-gate 与产品起草共用 `assemble()`，
   退化态那一支怎么改都不许挪动原路径的任何一个字节（EVAL_PROTOCOL §2）——
   2026-08-22 的 M1-a 把退化态的「【在场】未知」整块删掉，走的也是这条纪律。
"""

from __future__ import annotations

import pytest
from test_fake_graph import (
    QINGYUN,
    GU_QINGYIN,
    PID,
    XIAO_JUE,
    build,
    edge,
)

from novel_harness.draft.assemble import (
    CONTINUATION_GOAL,
    PromptForm,
    assemble,
)
from novel_harness.draft.context import (
    ResolvedConstraints,
    resolve_constraints,
    unknown_cast_constraints,
)
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.graph import EdgeType

SHORT = LengthSpec(language=DraftLanguage.ZH, min_units=80, target_units=150, max_units=300)
"""续写档：一两段。`LengthSpec` 的下限是 `ge=1`，所以这是合法取值——ADR 0015 D1
说的「后端一个字都不用改」就是这一行的意思。"""


def _store():
    return build([edge(XIAO_JUE.id, QINGYUN.id, EdgeType.LOCATED_AT, 88)])
def test_forbidden_entities_stay_exact_because_they_never_depended_on_cast() -> None:
    """未来实体是按章号算的，与在场无关——退化态里这一项**不该**跟着退化。"""
    ctx = unknown_cast_constraints(_store(), PID, 152)
    assert ctx.forbidden_names == ["幽泉窟"]


# ── 2. 退化态是另一个类型 ─────────────────────────────────────────────────


def test_the_degraded_type_cannot_masquerade_as_the_resolved_one() -> None:
    """两个类型不可互换，而且退化态**没有 cast 字段**——不许用空列表冒充「没有人在场」。"""
    ctx = unknown_cast_constraints(_store(), PID, 152)

    assert not isinstance(ctx, ResolvedConstraints)
    assert not hasattr(ctx, "cast")


def test_resolved_constraints_still_refuses_an_empty_cast() -> None:
    """**这条是上面那条的反面守卫**：加了退化型之后，原来那道闸不许跟着松。

    松了的形态是 `ResolvedConstraints(cast=[])` 能构造出来 —— 那一刻
    「拿到这个类型 = 约束是算出来的」这句话就不再为真，而下游全靠它。
    """
    with pytest.raises(Exception):
        ResolvedConstraints(chapter=1, cast=[])


# ── 2.5 「【在场】未知」那一块已经删了（M1-a，2026-08-22）───────────────────


def test_the_degraded_prompt_says_nothing_about_who_is_present() -> None:
    """退化态的 prompt 里**没有**「【在场】未知」那一块。

    它坏掉时代表：有人把那句话加了回来。「未知」两个字不带任何信息，而模型仍然不知道
    这一场有谁。要把这一块变准就得知道在场是谁，而续写的那一刻它还没被写出来——
    那份精度只有保存之后的验证侧算得准（`docs_dev` 2026-08-22 M1-a）。
    """
    ctx = unknown_cast_constraints(_store(), PID, 152)

    body = "\n".join(
        m["content"]
        for m in assemble(ctx, form=PromptForm.X1, goal=CONTINUATION_GOAL, length=SHORT)
    )

    assert "【在场】" not in body
    assert "未知" not in body


def test_what_is_left_of_the_degraded_prompt_all_still_carries_information() -> None:
    """**删过头也要红。** 剩下的每一块都还在，而且每一块都带着信息。

    它坏掉时代表：M1-a 连**不依赖在场**的那一块也一起删了——尚未登场的实体
    （只看章号算，退化态里仍然精确）。那样退化态的 prompt 就只剩一句空提示。
    """
    ctx = unknown_cast_constraints(_store(), PID, 152)

    body = assemble(ctx, form=PromptForm.X1, goal=CONTINUATION_GOAL, length=SHORT)[-1][
        "content"
    ]

    assert "尚未登场、这一场不得出现：幽泉窟（第 200 章首现）" in body
    assert CONTINUATION_GOAL in body
def test_the_resolved_path_is_byte_identical() -> None:
    """kill-gate 走的那条路径一个字节没动 —— 加分支那次没动，M1-a 删分支这次也没动。

    坏掉的形态：有人为了让两条路径「长得一样」而顺手改了 `_base()` 里在场行的措辞——
    X0/X1/X2 三臂的 prompt 全变，而 `runs/*.jsonl` 里的历史结果再也不可比。
    """
    ctx = resolve_constraints(_store(), PID, 152, [GU_QINGYIN.name])
    messages = assemble(ctx, form=PromptForm.X0, goal="试探", length=SHORT)

    assert messages[-1]["content"] == "【在场】\n顾清音\n\n【这一场要写】\n试探"


def test_the_continuation_goal_is_a_backend_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0015 D3：续写的提示语住在后端。它是常量，不是某个调用方传进来的默认值。"""
    assert CONTINUATION_GOAL.strip()
    assert "顺着上文" in CONTINUATION_GOAL
