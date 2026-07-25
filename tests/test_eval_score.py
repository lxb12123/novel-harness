"""kill-gate 统计原语的单测：多数投票 / 精确 McNemar / 配对比较 / Holm。

全是纯函数，取值可手算——这一层是裁决的地基，错一个符号就把 PASS 和 KILL 判反。
"""

from __future__ import annotations

import pytest

from novel_harness.eval.score import compare_arms, holm, majority, mcnemar_exact_p


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
