"""kill-gate 统计原语的单测：多数投票 / 精确 McNemar / 配对比较 / Holm。

全是纯函数，取值可手算——这一层是裁决的地基，错一个符号就把 PASS 和 KILL 判反。
"""

from __future__ import annotations

import pytest

from novel_harness.eval import score
from novel_harness.eval.score import (
    GateInput,
    TrapRuns,
    Verdict,
    compare_arms,
    decide,
    holm,
    majority,
    mcnemar_exact_p,
)


def test_majority() -> None:
    assert majority([True, True, False]) is True
    assert majority([True, False, False]) is False
    assert majority([True, True, True, False, False]) is True
    assert majority([True, True, False, False, False]) is False


def test_majority_empty_raises() -> None:
    with pytest.raises(ValueError, match="空投票"):
        majority([])


def test_mcnemar_exact_known_values() -> None:
    assert mcnemar_exact_p(0, 0) == 1.0
    # 8 个判别对全指向一个方向：2 * C(8,0)/2^8 = 2/256
    assert mcnemar_exact_p(8, 0) == pytest.approx(2 / 256)
    # 双侧对称：把方向反过来 p 不变
    assert mcnemar_exact_p(0, 8) == pytest.approx(2 / 256)
    # 完全均分：2 × (>0.5 的尾) 被封顶到 1
    assert mcnemar_exact_p(5, 5) == 1.0


def test_mcnemar_negative_raises() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        mcnemar_exact_p(-1, 3)


def test_compare_arms_full_improvement() -> None:
    a = [True] * 8 + [False] * 2  # 基线泄漏 0.8
    b = [False] * 10  # 处理臂零泄漏
    cmp = compare_arms("X0 vs X1", a, b)
    assert cmp.leak_a == pytest.approx(0.8)
    assert cmp.leak_b == 0.0
    assert cmp.delta == pytest.approx(0.8)
    assert (cmp.b_only, cmp.c_only) == (8, 0)
    assert cmp.discordant == 8
    assert cmp.p_exact == pytest.approx(2 / 256)


def test_compare_arms_counts_both_directions() -> None:
    a = [True, True, False, False]
    b = [False, True, True, False]
    cmp = compare_arms("X0 vs X2", a, b)
    assert cmp.b_only == 1  # trap0: a 泄漏、b 不泄漏
    assert cmp.c_only == 1  # trap2: b 泄漏、a 不泄漏
    assert cmp.delta == pytest.approx(0.0)


def test_compare_arms_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="等长"):
        compare_arms("x", [True], [True, False])


def test_compare_arms_empty_raises() -> None:
    with pytest.raises(ValueError, match="空样本"):
        compare_arms("x", [], [])


def test_holm_two_hypotheses() -> None:
    adj = holm({"A1": 0.01, "A2": 0.04})
    assert adj["A1"] == pytest.approx(0.02)  # 最小的 × 2
    assert adj["A2"] == pytest.approx(0.04)  # 次小的 × 1


def test_holm_is_order_independent_and_monotone() -> None:
    adj = holm({"big": 0.04, "small": 0.01})
    assert adj["small"] == pytest.approx(0.02)
    assert adj["big"] == pytest.approx(0.04)
    assert adj["small"] <= adj["big"]  # 单调不减


# ══════════════════════════════════════════════════════════════════════════
# 预注册裁决表 decide()
# ══════════════════════════════════════════════════════════════════════════
#
# 这一段测的不是「代码跑不跑」，是**及格线有没有被动过**。四档裁决各一条，
# 外加一条把六个阈值常量逐字钉死——那条红了不是测试过时，是有人改了卷子。


def _trap(tid: str, x0: str, x1: str, x2: str, *, kind: str = "KNOWS", ref: bool = False):
    """用 "110" 这种字符串写重复结果，读起来像一张真的数据表。1 = 泄漏。"""
    b = lambda s: tuple(c == "1" for c in s)  # noqa: E731
    return TrapRuns(trap_id=tid, kind=kind, x0=b(x0), x1=b(x1), x2=b(x2), reference_leaked=ref)


def _uniform(n: int, x0: str, x1: str, x2: str, *, start: int = 0, **kw):
    return [_trap(f"t{i}", x0, x1, x2, **kw) for i in range(start, start + n)]


def test_the_thresholds_are_the_preregistered_ones() -> None:
    """**这条测试守的是「不能事后挪及格线」。**

    六个数字全部来自 2026-07-25 冻结的 EVAL_PROTOCOL.md §6/§5，在第一次生成之前就定死了。
    它红了只有两种可能：有人改了阈值（= 改卷子），或者协议出了新的修正案而这里没跟上。
    两种都必须停下来看，不许直接改数字让它变绿。
    """
    assert score.FLOOR_MIN_X0_LEAK == 0.50
    assert score.CEILING_MAX_X0_LEAK == 0.90
    assert score.MIN_DELTA == 0.15
    assert score.ALPHA == 0.05
    assert score.MIN_DISCORDANT == 8
    assert score.FORM_PIVOT_MIN_DELTA == 0.15
    assert score.ESCALATED_REPEATS == 5


def test_floor_gate_never_kills() -> None:
    """陷阱没咬住时，三臂都低、Δ≈0、p 不显著——**读起来和 KILL 一模一样**。

    地板门排在裁决表第一行就是为了把这两件事分开。这条测试钉的是那个顺序：
    顺序反了，一个陷阱造得太软的实验会砍掉一个本来对的项目。
    """
    d = decide(GateInput(traps=tuple(_uniform(20, "000", "000", "000"))))
    assert d.verdict is Verdict.INVALID
    assert "地板" in d.rule
    assert "绝不 KILL" in d.action


def test_ceiling_gate_fires_on_a_leaking_reference() -> None:
    """X0 泄漏率没超天花板，但有一条 `reference` 完成被判泄漏 → 仍然 INVALID。

    reference 是人手写的、保证不泄漏的完成。它被判泄漏意味着**检测器在开无差别火**，
    这时候任何臂间比较都不可信——哪怕 X0 的数字看着很正常。
    """
    # X0 泄漏率 15/20 = 0.75，稳稳落在地板与天花板之间——**这一条必须由 reference 触发**，
    # 否则它测的就是上一条已经测过的天花板率门。
    traps = _uniform(15, "111", "000", "000") + _uniform(4, "000", "000", "000", start=15)
    traps.append(_trap("t-ref", "000", "000", "000", ref=True))
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.INVALID
    assert "reference" in d.rule


def test_pass_requires_all_four_conditions() -> None:
    """PASS 的四个条件缺一不可：Δ≥0.15、Holm p<0.05、符号稳定、判别对≥8。"""
    # 15 条 KNOWS：12 条 X0 泄漏而 X1/X2 不泄漏（判别对 12），3 条三臂都不泄漏。
    traps = _uniform(12, "111", "000", "000") + _uniform(3, "000", "000", "000", start=12)
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.PASS
    a1 = next(c for c in d.comparisons if c.name.startswith("A1"))
    assert a1.delta == pytest.approx(12 / 15)
    assert a1.discordant == 12 >= score.MIN_DISCORDANT
    assert d.holm_p[a1.name] < score.ALPHA
    assert d.sign_stable[a1.name] is True


def test_thin_discordant_is_inconclusive_never_kill() -> None:
    """判别对不足 = **仪器没给出信息**，不是「注入没用」。§5 明文「绝不 KILL」。"""
    # 只有 5 条判别对（< 8）。另配 8 条两臂同泄漏 + 4 条两臂同干净，
    # 把 X0 泄漏率压到 13/17 ≈ 0.76 —— 必须避开地板和天花板，否则测的是别的门。
    traps = (
        _uniform(5, "111", "000", "000")
        + _uniform(8, "111", "111", "111", start=5)
        + _uniform(4, "000", "000", "000", start=13)
    )
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.INCONCLUSIVE
    assert "判别对不足" in d.rule
    assert "扩陷阱集" in d.action


def test_direction_right_but_small_is_inconclusive_at_three_repeats() -> None:
    """方向对、判别对够、但 Δ < 0.15 → 缓刑一轮（修正案 2 的分流）。

    这一档在原裁决表里会被第 6 行吃掉判成 KILL——那正是修正案 2 要修的东西。
    """
    # 20 条：2 条改善、10 条恶化…… 不行，要 Δ>0 且小。用 30 条陷阱做细粒度。
    traps = (
        _uniform(9, "111", "000", "000")  # 9 条改善
        + _uniform(6, "000", "111", "111", start=9)  # 6 条恶化 → Δ = (9-6)/30 = 0.10
        + _uniform(15, "111", "111", "111", start=15)  # 15 条两边都泄漏
    )
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.INCONCLUSIVE
    assert "方向对、仪器有效" in d.rule
    assert "Δ=0.10 < 0.15" in d.rule  # 理由必须指名实际落空的那一条
    assert "升到 5 次重复" in d.action


def test_the_same_data_at_five_repeats_becomes_kill() -> None:
    """**缓刑是一次性的。** 同一份形状升到 5 次重复后仍不过阈值 → KILL。

    §6 第 7 行的动作原文就写着「仍不过阈值→按 KILL」。不实现它，INCONCLUSIVE
    就是一个可以无限期续期的逃生舱，kill-gate 也就不再是 gate。
    """
    traps = (
        _uniform(9, "11111", "00000", "00000")
        + _uniform(6, "00000", "11111", "11111", start=9)
        + _uniform(15, "11111", "11111", "11111", start=15)
    )
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.KILL
    assert d.repeats == 5
    assert "升到 5 次重复后仍不过" in d.rule


def test_kill_when_direction_is_wrong_and_instrument_is_valid() -> None:
    """注入把事情弄得更糟、且仪器有效 → 这才是裁决表第 6 行说的 KILL。"""
    # 12 条两臂同泄漏（把 X0 抬到 12/22 ≈ 0.55，过地板）+ 10 条注入后反而泄漏（Δ<0，判别对 10）。
    traps = _uniform(12, "111", "111", "111") + _uniform(10, "000", "111", "111", start=12)
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.KILL
    assert "连方向都不对" in d.rule
    assert "/draft 冻在 501" in d.action


def test_form_pivot_is_gatekept_by_a_and_by_confound_lint() -> None:
    """决策 B 只在 A 通过后评，且 `confound_lint` 报警时整条摘掉（§6 第 3 行）。"""
    # A 通过（X2 全干净），且 X2 比 X1 再低 10/15 ≥ 0.15。
    traps = (
        _uniform(10, "111", "111", "000")  # X1 泄漏、X2 不泄漏
        + _uniform(3, "111", "000", "000", start=10)
        + _uniform(2, "000", "000", "000", start=13)
    )
    ok = decide(GateInput(traps=tuple(traps), confound_ok=True))
    assert ok.verdict is Verdict.PASS
    assert ok.form_pivot is True
    assert any(c.name.startswith("B") for c in ok.comparisons)
    assert "NH_DRAFT_FORM=X2" in ok.action

    # 同一份数据，confound_lint 报警 → 仍 PASS，但 B 根本不评。
    warned = decide(GateInput(traps=tuple(traps), confound_ok=False))
    assert warned.verdict is Verdict.PASS
    assert warned.form_pivot is False
    assert not any(c.name.startswith("B") for c in warned.comparisons)
    assert any("confound_lint 报警" in n for n in warned.notes)


def test_future_traps_never_drive_the_verdict() -> None:
    """FUTURE 的 tell 必然出现在 X1/X2 的 prompt 里（echo 风险），所以只作描述性地板。

    这条构造了一个「FUTURE 上注入臂看起来惨不忍睹」的极端：如果 FUTURE 参与裁决，
    它会把 PASS 拽成 KILL。裁决必须对它免疫。
    """
    knows = _uniform(12, "111", "000", "000") + _uniform(3, "000", "000", "000", start=12)
    future = _uniform(10, "000", "111", "111", start=100, kind="FUTURE")
    d = decide(GateInput(traps=tuple(knows + future)))
    assert d.verdict is Verdict.PASS
    assert d.n_knows == 15
    assert d.n_future == 10
    assert d.future_floor["x1"] == 1.0  # 注入臂在 FUTURE 上全泄漏……
    assert all(c.n == 15 for c in d.comparisons)  # ……而它一次都没进比较


def test_sign_stability_is_about_direction_not_self_consistency() -> None:
    """**「三次同号」= 与被主张的方向同号，不是「三次彼此同号」。**（修正案 3）

    这两种读法差别正好落在这条过滤最该拦住的地方。下面这份 n=19 的样本里，
    三次单次重复的 Δ **全是负的**——每一次单独看，注入都让泄漏更糟——
    而多数投票把它磨成了 +0.42。按「彼此同号」读，它报「符号稳定」并拿到 PASS，
    也就是让一条无效的产品线上线。
    """
    traps = (
        [_trap(f"i{k}", p, q, q) for k, (p, q) in enumerate(
            zip(["110", "110", "110", "110", "101", "101", "011", "011"],
                ["100", "100", "100", "010", "010", "010", "001", "001"], strict=True))]
        + [_trap("j0", "101", "111", "111"), _trap("j1", "011", "111", "111")]
        + [_trap(f"k{k}", "000", p, p) for k, p in enumerate(
            ["100", "100", "100", "010", "010", "010", "001", "001", "001"])]
    )
    per = [sum(t.x0[i] for t in traps) - sum(t.x1[i] for t in traps) for i in range(3)]
    assert all(dlt < 0 for dlt in per), f"前提：三次单次重复的 Δ 必须全为负，实测 {per}"
    assert score.sign_stable(traps, "x0", "x1") is False
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is not Verdict.PASS
    assert d.pass_checks["A1:X0-vs-X1"]["sign_stable"] is False
    # 而聚合 Δ 确实是正的且过阈值——也就是说，拦住它的**只有**这条过滤。
    assert d.pass_checks["A1:X0-vs-X1"]["delta"] is True


def test_sign_stable_rejects_a_zero_delta_repeat() -> None:
    """`Δ == 0` 的那一次算不稳定（零没有符号）。刻意从严，docstring 里说了理由。"""
    assert score.sign_stable([_trap(f"z{i}", "101", "101", "101") for i in range(15)],
                             "x0", "x1") is False


def test_sign_stable_needs_at_least_three_repeats() -> None:
    """repeats<3 时这条过滤退化成恒真——那是四个 PASS 条件里最难的一个白送。"""
    with pytest.raises(ValueError, match="至少需要 3 次重复"):
        score.sign_stable([_trap("a", "1", "0", "0")], "x0", "x1")


def test_decide_refuses_malformed_runs() -> None:
    """形状不对的 run 只能重跑，不能硬判——静默凑合出来的裁决没人能复算。"""
    with pytest.raises(ValueError, match="空陷阱集"):
        decide(GateInput(traps=()))
    with pytest.raises(ValueError, match="相同的重复次数"):
        decide(GateInput(traps=(_trap("a", "111", "000", "000"), _trap("b", "11", "00", "00"))))
    with pytest.raises(ValueError, match="协议只定义"):
        decide(GateInput(traps=tuple(_uniform(3, "11", "00", "00"))))
    # repeats=1 会让符号稳定性过滤退化成恒真——四个 PASS 条件里最难的那个白送。
    with pytest.raises(ValueError, match="协议只定义"):
        decide(GateInput(traps=tuple(_uniform(3, "1", "0", "0"))))
    with pytest.raises(ValueError, match="id 必须唯一"):
        decide(GateInput(traps=(_trap("a", "111", "000", "000"), _trap("a", "111", "000", "000"))))
    with pytest.raises(ValueError, match="没有 KNOWS 陷阱"):
        decide(GateInput(traps=tuple(_uniform(3, "111", "000", "000", kind="FUTURE"))))


# ══════════════════════════════════════════════════════════════════════════
# 裁决级守卫（对抗性验证补的那一批）
# ══════════════════════════════════════════════════════════════════════════
#
# 起因：一轮变异测试证明，四个 PASS 条件里**有三个可以整行删掉而全部测试仍然全绿**，
# 阈值常量也只被字面量断言钉住（改「使用点」不改常量，一条都不红）。
# 也就是说上面那些测试证明的是「代码能跑」，不是「及格线真的在生效」。
# 下面这批每一条都对着一个已实测存活的变异。


def _mix(*groups: tuple[int, str, str, str]):
    """按 `(条数, x0, x1, x2)` 造陷阱集，id 自动唯一。"""
    out, i = [], 0
    for count, a, b, c in groups:
        for _ in range(count):
            out.append(_trap(f"t{i}", a, b, c))
            i += 1
    return tuple(out)


def test_a_tie_in_delta_does_not_hide_the_arm_that_passes() -> None:
    """**这条对着一个 critical。** Δ1 == Δ2 时，两条臂都是 §6 说的「该臂」。

    此前实现是 `best = max((a1, a2), key=...)`，依赖 Python 的「平票返回第一个」，
    于是平票时永远只评 A1，四条件全过的 A2 被遮住，一个 §6 第 4 行逐字命中的实验
    掉进第 6 行判 **KILL**。模拟显示平票不是角落情形（n=15/3 次重复下约占 26.5%）。
    """
    traps = _mix((8, "111", "000", "000"), (3, "111", "000", "111"),
                 (3, "000", "111", "000"), (6, "111", "111", "111"))
    d = decide(GateInput(traps=tuple(traps)))
    a1, a2 = d.comparisons[0], d.comparisons[1]
    assert a1.delta == a2.delta, "前提：这份数据必须是 Δ 平票"
    assert len(d.eligible_arms) == 2, "平票时两条臂都该进 eligible"
    assert d.verdict is Verdict.PASS
    assert d.pass_checks[a2.name] == {k: True for k in d.pass_checks[a2.name]}
    assert not all(d.pass_checks[a1.name].values()), "前提：只有 A2 四条件全过"


def test_the_verdict_does_not_depend_on_which_column_is_x1() -> None:
    """裁决由「哪一列写在前面」决定就不是裁决。同一份数字对调 X1/X2 两列，结果必须一样。"""
    a = _mix((11, "11111", "11000", "00000"), (2, "11111", "11111", "11111"),
             (2, "00000", "00000", "00000"))
    b = tuple(t.model_copy(update={"x1": t.x2, "x2": t.x1}) for t in a)
    assert decide(GateInput(traps=a)).verdict is decide(GateInput(traps=b)).verdict


@pytest.mark.parametrize(
    ("broken", "traps", "keyword"),
    [
        # 判别对不足会先命中 ⑥ 的 thin 分支（§5「绝不 KILL」优先于一切），那是对的——
        # 所以这一档验的是 pass_checks 里只有 discordant 为假，且理由说的是判别对。
        ("discordant", _mix((7, "111", "000", "000"), (9, "111", "111", "111"),
                            (4, "000", "000", "000")), "判别对不足"),
        # 两臂判别对都要 ≥8，否则又落进 thin 分支测不到 Holm。A2 的 p 必须更大，
        # Holm 才会把 ×2 的乘子给 A1（raw 0.0391 → Holm 0.0781，跨过 0.05）。
        ("holm_p", _mix((5, "111", "000", "000"), (3, "111", "000", "111"),
                        (1, "000", "111", "000"), (4, "000", "000", "111"),
                        (8, "111", "111", "111")), "Holm p"),
        ("sign_stable", _mix((8, "111", "000", "000"), (20, "000", "100", "100"),
                             (20, "111", "111", "111")), "符号不稳"),
        ("delta", _mix((8, "111", "000", "000"), (25, "111", "111", "111"),
                       (27, "000", "000", "000")), "Δ=0.13 < 0.15"),
    ],
)
def test_each_pass_condition_alone_can_block_pass(broken, traps, keyword) -> None:
    """**四个条件各自都必须能单独拦住 PASS。**

    每份数据只破坏一个条件、其余三个满足——此前那条同名测试只喂了一份四条全满足的
    数据，于是删掉其中任意三个条件它都照样绿。名不副实的测试比没有测试更糟。
    """
    d = decide(GateInput(traps=traps))
    arm = d.eligible_arms[0]
    assert d.pass_checks[arm][broken] is False, f"前提：{broken} 必须是唯一落空的那个"
    assert sum(1 for ok in d.pass_checks[arm].values() if not ok) == 1
    assert d.verdict is not Verdict.PASS
    assert keyword in d.rule, f"理由必须指名 {broken}，实际：{d.rule}"


@pytest.mark.parametrize(
    ("what", "traps", "expected"),
    [
        # 地板是 `< 0.50`、天花板是 `> 0.90`——边界值本身必须通过，不是 INVALID。
        ("x0_leak 恰好 0.50", _mix((10, "111", "000", "000"), (10, "000", "000", "000")),
         Verdict.PASS),
        ("x0_leak 恰好 0.90", _mix((18, "111", "000", "000"), (2, "000", "000", "000")),
         Verdict.PASS),
        # 判别对恰好 8 是 `>=`，该过。
        ("判别对恰好 8", _mix((8, "111", "000", "000"), (9, "111", "111", "111"),
                              (3, "000", "000", "000")), Verdict.PASS),
        # 落在 (0.30, 0.50) 之间：必须仍是地板 INVALID。没有这一格，把地板阈值
        # 悄悄从 0.50 改成 0.30 一条测试都不会红——而那会把「仪器坏了」读成「有效但没结论」。
        ("x0_leak 0.40 仍在地板下", _mix((8, "111", "000", "000"),
                                         (12, "000", "000", "000")), Verdict.INVALID),
        # Δ 恰好 0：修正案 2 分流表第 3 行「max(Δ)≤0 → KILL」，不许滑进 INCONCLUSIVE。
        ("Δ 恰好 0", _mix((6, "111", "000", "000"), (6, "000", "111", "111"),
                          (10, "111", "111", "111")), Verdict.KILL),
    ],
)
def test_gate_boundaries(what, traps, expected) -> None:
    """不等号方向。把 `<` 写成 `<=` 这类变异全部实测存活过——边界值就是它们的照妖镜。"""
    assert decide(GateInput(traps=traps)).verdict is expected, what


def test_thin_takes_the_minimum_discordant_not_the_maximum() -> None:
    """判别对取**两臂里更少的那个**：一条臂没信息，就不能拿另一条臂的信息去 KILL。

    此前那条 INCONCLUSIVE 测试让 x1 与 x2 同形，于是 min 与 max 恒等，
    `min → max` 这个变异天然测不出来。
    """
    traps = _mix((10, "000", "111", "000"), (4, "000", "000", "111"),
                 (20, "111", "111", "111"))
    d = decide(GateInput(traps=traps))
    a1, a2 = d.comparisons[0], d.comparisons[1]
    assert (a1.discordant, a2.discordant) == (10, 4), "前提：两臂判别对必须不同"
    assert d.verdict is Verdict.INCONCLUSIVE
    assert "最少 4" in d.rule
    assert "扩陷阱集" in d.action
    # 修正案 2：判别对不足这一档**不许**建议升重复次数——加重复不会让仪器变灵敏，
    # 而且会白白烧掉「缓刑已用掉」的标记，让下一轮直接落进 KILL。
    assert "5 次重复" not in d.action


@pytest.mark.parametrize(
    ("what", "traps"),
    [
        ("只满足 Δ_B，p_B 不显著", _mix((3, "111", "111", "000"), (9, "111", "000", "000"),
                                        (5, "111", "111", "111"), (3, "000", "000", "000"))),
        ("只满足 p_B，Δ_B 不够", _mix((8, "111", "111", "000"), (10, "111", "000", "000"),
                                      (25, "111", "111", "111"), (17, "000", "000", "000"))),
    ],
)
def test_form_pivot_needs_both_conditions(what, traps) -> None:
    """FORM-PIVOT 的两个条件（Δ_B ≥ 15pt **且** p_B 显著）删掉任一个都实测存活过。"""
    d = decide(GateInput(traps=traps))
    assert d.verdict is Verdict.PASS, f"前提：A 必须先通过（{what}）"
    assert d.form_pivot is False, what
    assert "NH_DRAFT_FORM=X2" not in d.action


def test_the_floor_gate_wins_when_both_invalid_gates_fire() -> None:
    """两门同时命中时必须报地板——`decide()` 的 docstring 用整段论证「按序」是预注册的一部分。"""
    traps = list(_mix((3, "111", "000", "000"), (16, "000", "000", "000")))
    traps.append(_trap("t-ref", "000", "000", "000", ref=True))
    d = decide(GateInput(traps=tuple(traps)))
    assert d.verdict is Verdict.INVALID
    assert "地板" in d.rule and "绝不 KILL" in d.action


def test_holm_monotonicity_is_actually_exercised() -> None:
    """Holm 的 running-max 钳制：{0.03, 0.04} → 两者都是 0.06。

    此前那条单调性测试用 {0.01, 0.04}（0.01×2 = 0.02 < 0.04），钳制从来没被触发，
    于是「去掉 running max」这个变异实测存活——而它会放行一个不该 PASS 的臂。
    """
    assert holm({"A1": 0.03, "A2": 0.04}) == {"A1": 0.06, "A2": 0.06}
