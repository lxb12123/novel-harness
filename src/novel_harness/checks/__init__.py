"""硬规则 —— 判分器与 Validator 的同一份实现（PLAN §9）。

`ALL_CHECKS` 现在只有一条。这不是进度落后，是 ADR 0005 的铁律划完之后剩下的：

| 规则 | 状态 |
|---|---|
| R1 认知边界 | 不在这里——它是面板不是规则（`panel/knowledge.py`） |
| R2 FUTURE_LEAK | TODO(M3)：要 `text/mentions.py` 的正则 alternation |
| R3 DEAD_SPEAKS | TODO(M3)：同上；判据是 `StateSnapshot.is_dead` / `has_appeared()` |
| **R4 LOCATION_CONFLICT** | **v1 现在就能做的唯一一条：不读正文，零 FP** |
| R5 ADDRESS_CONFLICT | TODO(M3)：生死取决于 Day 1 下午的覆盖率实测（≥10% 才进 v1） |

M3 的生死线是「真书连续 20 章误报 < 1 条/章 **且** 合成小册子真阳性 ≥ 22/25」。
双边门槛的存在理由：**沉默的工具死得比吵闹的工具更快，只是死得更安静，而且指标
不会告诉你它死了**（§6 fatal #7）——所以「先只上 R4」在 M0 是对的，在 M3 不是。
"""

from __future__ import annotations

from . import location_conflict
from .base import FIRE_SCOPE, Check, CheckContext, Issue, Scene

ALL_CHECKS: tuple[Check, ...] = (location_conflict.check,)
"""按声明顺序跑。加一条规则 = 加一个文件 + 在这里加一项。"""


def run_checks(ctx: CheckContext, checks: tuple[Check, ...] = ALL_CHECKS) -> list[Issue]:
    """跑全部规则（`nh check --chapter N` / `scripts/demo.sh` 的入口）。

    **不吞异常。** 一条规则炸了应当整个红，而不是让作者以为「这章没问题」——
    静默的零 issue 和真的零 issue 在面板上长得一模一样。
    """
    issues: list[Issue] = []
    for check in checks:
        issues.extend(check(ctx))
    return issues


__all__ = [
    "ALL_CHECKS",
    "FIRE_SCOPE",
    "Check",
    "CheckContext",
    "Issue",
    "Scene",
    "run_checks",
]
