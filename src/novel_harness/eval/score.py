"""M2 kill-gate 的统计 —— 精确 McNemar + Holm，纯函数、零重依赖（不引 scipy/numpy/statsmodels）。

分析单位是**陷阱**（trap），不是单次生成：每条陷阱多次重复取多数成二值，再在配对样本上做
精确 McNemar。为什么精确不用卡方近似：n≈15–25、判别对可能个位数，卡方的连续性近似在这个
量级不可信；精确检验用 `math.comb` 直接算二项尾概率，零依赖、`uvx` 一条命令装得上不受影响
（docs/EVAL_PROTOCOL.md §5）。

这里只放**统计原语**（多数投票 / 精确 McNemar / 配对比较 / Holm）。完整裁决表（FLOOR/CEILING
门、KNOWS 主导、符号稳定性、MIN_DISCORDANT、gatekept B）落在后续的 `decide()`，它消费这些原语。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


def majority(votes: Sequence[bool]) -> bool:
    """多数投票。3 次里 ≥2、5 次里 ≥3 判 True。重复数取奇数，不会有平票。"""
    if not votes:
        raise ValueError("majority() 不接受空投票")
    return sum(1 for v in votes if v) * 2 > len(votes)


def mcnemar_exact_p(b: int, c: int) -> float:
    """配对样本的**精确** McNemar 双侧 p 值。`b`、`c` = 两个不一致格的计数。

    H0 下每个判别对是 50/50，检验统计量 ~ Binomial(b+c, 0.5)。双侧 = 2 × 较小尾，封顶 1。
    `b == c == 0`（无判别对）时无信息，返回 1.0。
    """
    if b < 0 or c < 0:
        raise ValueError(f"判别对计数不能为负：b={b}, c={c}")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


class ArmComparison(BaseModel):
    """一对臂（如 X0 vs X1）在陷阱级二值上的配对比较。"""

    model_config = ConfigDict(frozen=True)

    name: str
    n: int
    leak_a: float
    """基线臂（X0）的泄漏率。"""
    leak_b: float
    """处理臂（X1/X2）的泄漏率。"""
    delta: float
    """`leak_a − leak_b`，>0 = 处理臂降低了泄漏（注入有用的方向）。"""
    b_only: int
    """a 泄漏、b 不泄漏（处理臂相对基线的**改善**数）。"""
    c_only: int
    """b 泄漏、a 不泄漏（处理臂相对基线的**恶化**数）。"""
    p_exact: float = Field(ge=0.0, le=1.0)

    @property
    def discordant(self) -> int:
        """判别对总数。EVAL_PROTOCOL.md 的 MIN_DISCORDANT≥8 判据比它。"""
        return self.b_only + self.c_only


def compare_arms(name: str, a: Sequence[bool], b: Sequence[bool]) -> ArmComparison:
    """陷阱级配对比较。`a` 是基线臂（X0），`b` 是处理臂（X1/X2）；一一对应、同序、同长。"""
    if len(a) != len(b):
        raise ValueError(f"配对样本必须等长：len(a)={len(a)}, len(b)={len(b)}")
    n = len(a)
    if n == 0:
        raise ValueError("compare_arms() 不接受空样本")
    b_only = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    c_only = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    return ArmComparison(
        name=name,
        n=n,
        leak_a=sum(1 for x in a if x) / n,
        leak_b=sum(1 for y in b if y) / n,
        delta=(sum(1 for x in a if x) - sum(1 for y in b if y)) / n,
        b_only=b_only,
        c_only=c_only,
        p_exact=mcnemar_exact_p(b_only, c_only),
    )


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm–Bonferroni 校正。返回 name → 校正后 p（保持单调不减，封顶 1）。

    家族 = {X0 vs X1, X0 vs X2}（EVAL_PROTOCOL.md §5 的决策 A）。Holm 比 Bonferroni 更有
    功效且同样控制 FWER——只有 2 个假设、样本又小的这里，多留一分功效是值得的。
    """
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, p * (m - i)))
        adjusted[name] = running
    return adjusted
