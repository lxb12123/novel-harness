"""M2 kill-gate 的统计 —— 精确 McNemar + Holm，纯函数、零重依赖（不引 scipy/numpy/statsmodels）。

分析单位是**陷阱**（trap），不是单次生成：每条陷阱多次重复取多数成二值，再在配对样本上做
精确 McNemar。为什么精确不用卡方近似：n≈15–25、判别对可能个位数，卡方的连续性近似在这个
量级不可信；精确检验用 `math.comb` 直接算二项尾概率，零依赖、`uvx` 一条命令装得上不受影响
（docs/EVAL_PROTOCOL.md §5）。

两层：**统计原语**（多数投票 / 精确 McNemar / 配对比较 / Holm）在上半部分，
**预注册裁决表** `decide()` 在下半部分——它只消费这些原语，不自己算统计。

`decide()` 是一个**纯函数**：喂进去每条陷阱每个臂每次重复的二值结果，吐出
PASS / KILL / INVALID / INCONCLUSIVE。它不读文件、不碰库、不调模型——因为裁决必须
**可复现**：ADR 0009 里的那个结论，任何人拿着同一份 `runs/*.jsonl` 都应该能重算出来。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

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


# ══════════════════════════════════════════════════════════════════════════
# 预注册裁决表（docs/EVAL_PROTOCOL.md §6 + EVAL_PROTOCOL_AMENDMENT_1.md）
# ══════════════════════════════════════════════════════════════════════════
#
# 下面这六个常量**就是及格线本身**。它们的值来自 2026-07-25 冻结的协议，
# 在第一次生成之前就定死了——**改它们等于改卷子**。
# `tests/test_eval_score.py::test_the_thresholds_are_the_preregistered_ones` 钉着它们，
# 那条测试红了不是「测试过时了」，是有人动了及格线。

FLOOR_MIN_X0_LEAK = 0.50
"""地板门：X0 的 KNOWS 泄漏率低于它 → INVALID（陷阱没咬住，不是约束有用）。**绝不 KILL。**"""

CEILING_MAX_X0_LEAK = 0.90
"""天花板门：X0 泄漏率高于它 → INVALID（任务本身不可满足，测不出注入的贡献）。"""

MIN_DELTA = 0.15
"""PASS 需要的绝对下降（15 个百分点）。协议 §7 的功效声明就是按这个效应量写的。"""

ALPHA = 0.05
"""显著性水平。A 家族走 Holm 校正后的 p，B 走原始 p（见 `_evaluate_form_pivot`）。"""

MIN_DISCORDANT = 8
"""McNemar 判别对下限。不足 → **INCONCLUSIVE，绝不 KILL**（§5）。

判别对少意味着**仪器没给出信息**，而不是「注入没用」。把「没信息」读成「没用」
再据此砍掉产品线，是这份协议从头到尾在防的那类错误里最贵的一个。
"""

FORM_PIVOT_MIN_DELTA = 0.15
"""决策 B：X2 要比 X1 **再**低这么多，才值得把生产默认翻成叙事化形态。"""

BASE_REPEATS = 3
"""§5：每条陷阱跑 3 次重复，取多数（≥2/3）成二值。**首轮必须是这个值。**"""

ESCALATED_REPEATS = 5
"""INCONCLUSIVE 的预案：一次性升到 5 次重复（泄漏 iff ≥3/5）。

**它同时是「缓刑已经用掉了」的标记。** §6 第 7 行的动作写着「仍不过阈值→按 KILL」，
所以在 5 次重复这一轮上，「方向对但没过阈值」不再返回 INCONCLUSIVE 而是 KILL——
否则 INCONCLUSIVE 就成了一个可以无限期续期的逃生舱，而 kill-gate 也就不再是 gate。
"""


class Verdict(StrEnum):
    """四种裁决。**没有第五种**——「看起来不错」不是裁决（约束 7：不确定就闭嘴）。"""

    PASS = "PASS"
    """注入有效。进 M3，把 per-kind 数字 + n 写进 README。"""
    KILL = "KILL"
    """注入无效且仪器有效。砍掉 AI 起草线，退回「作者的记忆外挂」。"""
    INVALID = "INVALID"
    """仪器坏了（地板/天花板）。重造陷阱重跑。**这一档绝不能滑成 KILL。**"""
    INCONCLUSIVE = "INCONCLUSIVE"
    """默认档。方向可能对但没过阈值，或判别对不够。升到 5 次重复再来。"""


TrapKind = Literal["KNOWS", "FUTURE"]


class TrapRuns(BaseModel):
    """一条陷阱在三个臂上的全部重复结果。

    `leaked` 里存的是**这条陷阱所针对的那一类**泄漏（`kind='KNOWS'` 存
    `LeakResult.knows_violation`，`kind='FUTURE'` 存 `future_leak`）——协议 §4 的分层
    说的就是每条陷阱各自瞄准一类。**具体数字以修正案 1 为准**：§4 冻结正文写的是
    「约 15 条 KNOWS / 约 8 条 FUTURE」，而 15+8 ≠ §5 的 n=25，
    `EVAL_PROTOCOL_AMENDMENT_1.md` 裁定为 **KNOWS 15 / FUTURE 10**。
    """

    model_config = ConfigDict(frozen=True)

    trap_id: str
    kind: TrapKind
    x0: tuple[bool, ...]
    x1: tuple[bool, ...]
    x2: tuple[bool, ...]
    reference_leaked: bool = False
    """这条陷阱的 `reference`（一个不泄漏的人工完成）被判成泄漏了吗。

    只要有**任何一条**为真，就说明检测器在一个本该干净的完成上开了火 = 天花板门触发
    = 这一轮的结果不可信（§6 第 2 行）。
    """


class GateInput(BaseModel):
    """跑完一轮 kill-gate 的全部输入。runner 从 `runs/*.jsonl` 归约成这个形状。"""

    model_config = ConfigDict(frozen=True)

    traps: tuple[TrapRuns, ...]
    confound_ok: bool = True
    """`confound_lint(x1, x2)` 通过了吗（两臂专名集合相同、字数 ±15% 内）。

    False → 本轮**禁用 FORM-PIVOT**，但 A 仍可评（§6 第 3 行）。它不是一个裁决，
    是一个把决策 B 摘掉的开关：X1 与 X2 之间有第二处差异时，B 的显著性无法归因到
    「清单 vs 散文」，硬读就会重犯 PLAN §5.7 那个内建混淆。
    """


class GateDecision(BaseModel):
    """裁决 + 它是怎么来的。**每一个字段都是为了让 ADR 0009 能被别人重算。**"""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    rule: str
    """命中了裁决表的哪一行（逐字引 §6）。"""
    action: str
    """那一行规定的动作。同样逐字来自 §6——动作也是预注册的一部分。"""
    form_pivot: bool = False
    """仅在 PASS 时可能为真：X2 显著优于 X1，生产默认翻成 `NH_DRAFT_FORM=X2`。"""

    n_knows: int
    n_future: int
    repeats: int
    x0_knows_leak: float
    """地板/天花板门比的就是它。"""
    comparisons: tuple[ArmComparison, ...]
    """A1（X0 vs X1）、A2（X0 vs X2），PASS 时再加 B（X1 vs X2）。"""
    holm_p: dict[str, float]
    """A 家族的 Holm 校正 p。**B 不在这个家族里**（固定序 gatekeeping，见 §5）。"""
    sign_stable: dict[str, bool]
    """每个 A 比较的符号稳定性：按「仅单次重复」重算 Δ，每次必须与陷阱级 Δ 同号。"""
    pass_checks: dict[str, dict[str, bool]] = Field(default_factory=dict)
    """臂名 → §6 第 4 行四个条件各自的真假（`delta`/`holm_p`/`sign_stable`/`discordant`）。

    **ADR 0009 直接引用这个字段**，别再从 rule 字符串里往外抠。地板/天花板那两档
    返回时它是空的——那时候还没算到第 4 行。
    """
    eligible_arms: tuple[str, ...] = ()
    """取到 `max(Δ1, Δ2)` 的臂。**平票时有两条**——那正是 §6「该臂」的歧义所在。"""
    future_floor: dict[str, float]
    """FUTURE 陷阱的三臂泄漏率。**描述性，不参与裁决**（§3：echo 风险）。"""
    notes: tuple[str, ...] = ()
    """判到一半发现的、需要写进 ADR 0009 的旁证。"""


def _pass_checks(
    c: ArmComparison, adjusted: dict[str, float], stability: dict[str, bool]
) -> dict[str, bool]:
    """§6 第 4 行的四个条件，**分开留下来**。

    分开而不是 `and` 成一个布尔，是因为落空时要能说出**哪一条**落空了：
    这句话会原样进 ADR 0009，而一份说错了自己失败原因的裁决记录，
    比没有记录更糟——它会让下一轮去改一个根本没问题的地方。
    """
    return {
        "delta": c.delta >= MIN_DELTA,
        "holm_p": adjusted[c.name] < ALPHA,
        "sign_stable": stability[c.name],
        "discordant": c.discordant >= MIN_DISCORDANT,
    }


def _explain(c: ArmComparison, checks: dict[str, bool], adjusted: dict[str, float]) -> str:
    """把落空的条件逐条写成人话。全过时返回空串（调用方不会走到）。"""
    detail = {
        "delta": f"Δ={c.delta:.2f} < {MIN_DELTA}",
        "holm_p": f"Holm p={adjusted[c.name]:.4f} ≥ {ALPHA}",
        "sign_stable": "符号不稳（有单次重复与陷阱级 Δ 反向或为零）",
        "discordant": f"判别对 {c.discordant} < {MIN_DISCORDANT}",
    }
    return "、".join(detail[k] for k, ok in checks.items() if not ok)


def _rates(traps: Sequence[TrapRuns], arm: str) -> list[bool]:
    """把一条陷阱的多次重复归约成一个二值（多数投票，§5）。"""
    return [majority(getattr(t, arm)) for t in traps]


def sign_stable(traps: Sequence[TrapRuns], arm_a: str, arm_b: str) -> bool:
    """符号稳定性过滤（§5 + 修正案 3）：**按「仅单次重复」重算 Δ，每一次都必须与陷阱级 Δ 同号。**

    为什么需要它：多数投票会把噪声磨平，于是一个纯粹由方差造出来的 ≥15pt 也可能
    在归约后看起来很稳。逐次重算是把那层磨平掉的噪声重新暴露出来——只要有一次
    方向反了，那个 15pt 就该判成方差而不是效应。

    ⚠️ **「同号」= 与被主张的方向同号，不是「三次彼此同号」。** 这两种读法不等价，
    而且差别正好落在这条过滤最该拦住的地方：实测构造得出一份 n=19 的样本，
    三次单次重复的 Δ **全是负的**（每一次注入都让泄漏更糟），聚合后却是 +0.42——
    按「彼此同号」读，它报「符号稳定」并拿到 PASS，也就是让一条无效的产品线上线。
    §5 原文「三次必须同号」按字面支持前一种读法，所以这条收紧**必须**是预注册的一部分，
    落在 `docs/EVAL_PROTOCOL_AMENDMENT_3.md`（写于第一个 `runs/*.jsonl` 之前）。

    `Δ == 0` 的那一次也算**不稳定**（零没有符号）。刻意从严：被这条守着的是
    「注入有效」这个会让产品上线的结论。
    """
    a_runs = [getattr(t, arm_a) for t in traps]
    b_runs = [getattr(t, arm_b) for t in traps]
    repeats = len(a_runs[0])
    if repeats < 3:
        raise ValueError(f"符号稳定性至少需要 3 次重复才有意义（§5），实测 {repeats}")
    # 陷阱级 Δ 的符号 = 被主张的方向。逐次重复必须每一次都指着同一边。
    claimed = sum(1 for r in a_runs if majority(r)) - sum(1 for r in b_runs if majority(r))
    if claimed == 0:
        return False
    want = 1 if claimed > 0 else -1
    for i in range(repeats):
        delta = sum(1 for r in a_runs if r[i]) - sum(1 for r in b_runs if r[i])
        if delta == 0 or (1 if delta > 0 else -1) != want:
            return False
    return True


def _validate(gi: GateInput) -> tuple[list[TrapRuns], list[TrapRuns], int]:
    """结构性前提。**不满足就抛，不静默凑合**——一份形状不对的 run 只能重跑，不能硬判。"""
    if not gi.traps:
        raise ValueError("decide() 不接受空陷阱集")
    lengths = {len(t.x0) for t in gi.traps} | {len(t.x1) for t in gi.traps}
    lengths |= {len(t.x2) for t in gi.traps}
    if len(lengths) != 1:
        raise ValueError(f"所有陷阱的所有臂必须有相同的重复次数，实测有 {sorted(lengths)}")
    repeats = lengths.pop()
    if repeats not in (BASE_REPEATS, ESCALATED_REPEATS):
        # 白名单而不是「奇数且 >0」：repeats=1 会让符号稳定性过滤**退化成恒真**
        # （只有一次重复，它必然与自己同号），于是四个 PASS 条件里最难的那个白送。
        # 协议只定义了两个值：§5 的 3 次、修正案 2 的升级后 5 次。别的都是 runner 出错。
        raise ValueError(
            f"协议只定义 {BASE_REPEATS} 次重复（§5）与升级后的 {ESCALATED_REPEATS} 次"
            f"（修正案 2），实测 {repeats} —— 这是 runner 的 bug，不能硬判"
        )
    ids = [t.trap_id for t in gi.traps]
    if len(set(ids)) != len(ids):
        raise ValueError("陷阱 id 必须唯一——重复的 id 会让同一条陷阱被算两次")
    knows = [t for t in gi.traps if t.kind == "KNOWS"]
    future = [t for t in gi.traps if t.kind == "FUTURE"]
    if not knows:
        raise ValueError("没有 KNOWS 陷阱——裁决由 KNOWS 维度主导，没有它就没有裁决可下")
    return knows, future, repeats


def _future_floor(future: Sequence[TrapRuns]) -> dict[str, float]:
    if not future:
        return {}
    n = len(future)
    return {
        arm: sum(1 for hit in _rates(future, arm) if hit) / n for arm in ("x0", "x1", "x2")
    }


def decide(gi: GateInput) -> GateDecision:
    """预注册裁决表（§6），**按序判**。第一条命中的规则就是裁决，不再往下看。

    ── 为什么「按序」这件事本身是预注册的一部分 ──────────────────────────

    地板门排在最前面，是因为它和 KILL 的表现形式长得一模一样：陷阱没咬住时，
    三个臂的泄漏率都很低、Δ 接近 0、p 不显著——**读起来就像「注入没用」**。
    先判地板，就把「仪器没测到东西」和「东西不存在」分开了。顺序反过来，
    一个陷阱造得太软的实验会直接砍掉一个本来对的项目。

    ── 口径（EVAL_PROTOCOL_AMENDMENT_1.md 修正 1）────────────────────────

    驱动裁决的三次 McNemar 跑在 **KNOWS 子集**上（协议 §5「裁决由 KNOWS 维度主导」）；
    `MIN_DISCORDANT ≥ 8` 也是对这个子集说的。FUTURE 陷阱照常跑、照常记，
    但只进 `future_floor` 那个描述性字段——它们的 tell 必然出现在 X1/X2 的 prompt 里，
    有 echo 风险，拿来主导裁决会系统性地冤枉注入臂。
    """
    knows, future, repeats = _validate(gi)
    n_knows, n_future = len(knows), len(future)
    x0 = _rates(knows, "x0")
    x0_leak = sum(1 for hit in x0 if hit) / n_knows
    a1 = compare_arms("A1:X0-vs-X1", x0, _rates(knows, "x1"))
    a2 = compare_arms("A2:X0-vs-X2", x0, _rates(knows, "x2"))
    adjusted = holm({a1.name: a1.p_exact, a2.name: a2.p_exact})
    stability = {
        a1.name: sign_stable(knows, "x0", "x1"),
        a2.name: sign_stable(knows, "x0", "x2"),
    }
    floor = _future_floor(future)
    notes: list[str] = []
    if not future:
        notes.append("本轮没有 FUTURE 陷阱：描述性地板缺席，ADR 0009 里要写明。")
    if not gi.confound_ok:
        notes.append("confound_lint 报警：本轮 FORM-PIVOT 已禁用，B 不评（§6 第 3 行）。")

    # 地板/天花板那两档在算到第 4 行之前就返回了，那时 `pass_checks` 本来就该是空的
    # （「还没走到那一步」和「四条都为假」是两件不同的事，别用默认值把它们混成一件）。
    late: dict[str, object] = {}

    def out(verdict: Verdict, rule: str, action: str, **kw) -> GateDecision:
        return GateDecision(
            verdict=verdict,
            rule=rule,
            action=action,
            **late,
            n_knows=n_knows,
            n_future=n_future,
            repeats=repeats,
            x0_knows_leak=x0_leak,
            holm_p=adjusted,
            sign_stable=stability,
            future_floor=floor,
            notes=tuple(notes),
            comparisons=kw.pop("comparisons", (a1, a2)),
            **kw,
        )

    # ① 地板：陷阱没咬住。**绝不 KILL。**
    if x0_leak < FLOOR_MIN_X0_LEAK:
        return out(
            Verdict.INVALID,
            f"X0 的 KNOWS 泄漏率 {x0_leak:.2f} < {FLOOR_MIN_X0_LEAK}（地板：陷阱没咬住）",
            "重造陷阱重跑。绝不 KILL——低泄漏基线说明仪器没测到东西，不说明注入没用。",
        )

    # ② 天花板 / 不可满足。
    leaky_refs = tuple(t.trap_id for t in gi.traps if t.reference_leaked)
    if x0_leak > CEILING_MAX_X0_LEAK or leaky_refs:
        why = (
            f"X0 KNOWS {x0_leak:.2f} > {CEILING_MAX_X0_LEAK}"
            if x0_leak > CEILING_MAX_X0_LEAK
            else f"reference 完成被判泄漏：{list(leaky_refs)}"
        )
        return out(Verdict.INVALID, f"{why}（天花板/不可满足）", "重造。")

    # ③ confound_lint 不是裁决，是把决策 B 摘掉的开关——已在上面记进 notes。

    # ④ PASS。**「该臂」= 取到 max(Δ) 的那条——平票时两条都是**（修正案 3 的裁定）。
    #
    # 这里曾经写成 `best = max((a1, a2), key=...)` 然后只判 best。那依赖 Python 的
    # 「平票返回第一个」，于是 Δ1 == Δ2 时永远只评 A1，**四条件全过的 A2 被遮住**，
    # 一个 §6 第 4 行逐字命中的实验会掉进第 6 行判 KILL。实测平票不是角落情形：
    # n=15 / 3 次重复的模拟里占 26.5%，而且同一份数字只把 X1、X2 两列对调，
    # 裁决就从 KILL 翻成 PASS——裁决由「哪一列写在前面」决定，那不是裁决。
    mx = max(a1.delta, a2.delta)
    eligible = tuple(c for c in (a1, a2) if c.delta == mx)
    checks = {c.name: _pass_checks(c, adjusted, stability) for c in (a1, a2)}
    winner = next((c for c in eligible if all(checks[c.name].values())), None)
    late["pass_checks"] = checks
    late["eligible_arms"] = tuple(c.name for c in eligible)

    if winner is not None:
        comparisons: tuple[ArmComparison, ...] = (a1, a2)
        pivot = False
        # ⑤ FORM 重要：仅当 A 通过才评 B（固定序 gatekeeping），且 confound_lint 必须通过。
        if gi.confound_ok:
            b = compare_arms("B:X1-vs-X2", _rates(knows, "x1"), _rates(knows, "x2"))
            comparisons = (a1, a2, b)
            # B 用**原始** p，不进 Holm 家族：固定序 gatekeeping 的整个意义就是
            # 「只有前一关过了才评下一关」，那已经控制了 FWER，再校正一次是重复惩罚。
            pivot = b.delta >= FORM_PIVOT_MIN_DELTA and b.p_exact < ALPHA
        return out(
            Verdict.PASS,
            f"{winner.name}：Δ={winner.delta:.2f} ≥ {MIN_DELTA}，Holm p="
            f"{adjusted[winner.name]:.4f} < {ALPHA}，符号稳定，判别对 {winner.discordant} ≥ "
            f"{MIN_DISCORDANT}"
            + ("（Δ 平票，两条臂都是「该臂」）" if len(eligible) > 1 else ""),
            (
                "进 M3；把 per-kind 数字 + n 写进 README。"
                + (" 并把生产默认翻成 NH_DRAFT_FORM=X2，重跑确认。" if pivot else "")
            ),
            form_pivot=pivot,
            comparisons=comparisons,
        )

    # 没过。**落空原因必须如实报出来**——这句话会原样进 ADR 0009，
    # 而它此前是硬编码成「Δ 未过阈值」的，于是 Δ=0.80 时会写出「Δ 最大 0.80 < 0.15」
    # 这种当场自证为假的话。逐条列出实际为 False 的那几项。
    best = eligible[0]
    why_failed = _explain(best, checks[best.name], adjusted)

    # ⑥ / ⑦ —— 分流口径见 EVAL_PROTOCOL_AMENDMENT_2.md（§6 第 6、7 行原文互相重叠）。
    #
    # 判别对不足永远走 INCONCLUSIVE（§5 明文「绝不 KILL」）：判别对少 = 仪器没给出信息，
    # 而不是「注入没用」。把「没信息」读成「没用」再据此砍产品线，是这里最贵的一种错。
    thin = min(a1.discordant, a2.discordant)
    if thin < MIN_DISCORDANT:
        return out(
            Verdict.INCONCLUSIVE,
            f"判别对不足（最少 {thin} < {MIN_DISCORDANT}）——仪器没给出信息，不是注入没用",
            # **不提「升重复次数」**：判别对不足意味着仪器测不到可分辨的东西，加重复不会变好
            # （修正案 2 专门为这一档写了理由）。而且写了会引诱 runner 去升到 5 次，
            # 白白烧掉本模块自己定义的「缓刑已用掉」标记，让下一轮直接落进 KILL。
            "扩陷阱集重跑（重复次数保持 3 次）。",
        )

    # 仪器有效。这时才谈「注入到底有没有用」。
    if mx > 0 and repeats < ESCALATED_REPEATS:
        # 方向对但没过阈值（§6 第 7 行）。**这不是逃生舱**：它的动作规定了升到 5 次重复，
        # 而升级后仍不过阈值就按 KILL 判——见下面那条分支。一次性缓刑，不是无限期。
        return out(
            Verdict.INCONCLUSIVE,
            f"{best.name} 未过：{why_failed}（Δ 最大 {mx:.2f}），方向对、仪器有效",
            f"预案一次性升到 {ESCALATED_REPEATS} 次重复（泄漏 iff ≥3/5）；仍不过阈值 → 按 KILL。",
        )

    why = (
        f"升到 {repeats} 次重复后仍不过（{why_failed}）"
        if repeats >= ESCALATED_REPEATS
        else f"两臂都不过（{why_failed}），且连方向都不对（Δ 最大 {mx:.2f} ≤ 0）"
    )
    return out(
        Verdict.KILL,
        f"{why}，且地板/天花板/判别对均合格（最少判别对 {thin} ≥ {MIN_DISCORDANT}）",
        "砍掉 AI 起草线，退回「作者的记忆外挂」；/draft 冻在 501；写 ADR 0009。",
    )
