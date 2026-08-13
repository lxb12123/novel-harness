"""硬规则 —— 判分器与 Validator 的同一份实现（PLAN §9）。

| 规则 | 状态 |
|---|---|
| R1 认知边界 | 不在这里——它是面板不是规则（`panel/knowledge.py`） |
| **R2 FUTURE_LEAK** | **2026-08-02 落地**：`text/mentions.py` + `first_appears_chapter` |
| **R3 DEAD_SPEAKS** | **2026-08-02 落地**：说话人标签位置 × `is_dead` / `has_appeared()` |
| **R4 LOCATION_CONFLICT** | v1 最早的一条：不读正文，零 FP |
| ~~R5 ADDRESS_CONFLICT~~ | **已砍**（2026-08-02：真书样本 8.2% < 10%，[ADR 0014](../docs/adr/0014-r5-cut-by-quote-coverage.md)） |

M3 的生死线是「真书连续 20 章误报 < 1 条/章 **且** 合成小册子真阳性 ≥ 22/25」。
双边门槛的存在理由：**沉默的工具死得比吵闹的工具更快，只是死得更安静，而且指标
不会告诉你它死了**（§6 fatal #7）——所以「先只上 R4」在 M0 是对的，在 M3 不是。

⚠️ **「落地」曾经不等于「开得了火」，这条要写在最前面。** R2/R3 从 2026-08-02 起
每天绿着，而在 2026-08-13 之前它们在**生产上结构性地不可能报出任何东西**：
`first_appears_chapter` 没有写入方（`POST /nodes` 的请求体里没有它、`cli._declare_node`
不传 props）、`EdgeProps.value_key` 没有写入方（于是 `is_dead` 恒为 False）、
`StateDim` 没有创建路径。三条规则里作者只可能查出 R4 一种问题。
那次「双边门槛已过」量的是 `synth/m3_replay.py` 的 `OverlayGraph`——它在**内存里**
补上这些边界数据，一条都没穿过写路径。

补法是三条作者入口（`nh declare dead` / `nh declare appears` + 两条同名 HTTP +
`POST /nodes` 的 `first_appears_chapter`），钉住它的是
`tests/test_rules_fire.py`：**每一个字都从 HTTP 进去**，先断言「什么都没声明时两条规则
是哑的」，再断言它们各自报出一条。**规则本身一行没改**（除了那两句建议语的措辞）——
这一课是「一条规则写完了、测试绿着，和它在产品里能开火，是两件事」。
"""

from __future__ import annotations

from . import dead_speaks, future_leak, location_conflict
from .base import FIRE_SCOPE, Check, CheckContext, Issue, Scene

ALL_CHECKS: tuple[Check, ...] = (
    location_conflict.check,
    future_leak.check,
    dead_speaks.check,
)
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
