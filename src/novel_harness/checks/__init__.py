"""硬规则 —— 判分器与 Validator 的同一份实现（PLAN §9）。

| 规则 | 状态 |
|---|---|
| R1 认知边界 | 不在这里——它是面板不是规则（`panel/knowledge.py`） |
| **R2 FUTURE_LEAK** | **2026-08-02 落地**：`text/mentions.py` + `first_appears_chapter` |
| **R3 DEAD_SPEAKS** | **2026-08-02 落地**：说话人标签位置 × `is_dead` / `has_appeared()` |
| ~~R4 LOCATION_CONFLICT~~ | **已砍**（2026-08-14，[ADR 0027](../../../docs/adr/0027-scene-blocks-cut.md)：它的一侧输入只能由作者手写，真书上零覆盖） |
| ~~R5 ADDRESS_CONFLICT~~ | **已砍**（2026-08-02：真书样本 8.2% < 10%，[ADR 0014](../../../docs/adr/0014-r5-cut-by-quote-coverage.md)） |

**两条被砍的规则死于同一个判据，这不是巧合**：ADR 0005 的表按「输入从哪儿来」排规则，
而排在最上面那几条（不读正文、零 FP）之所以便宜，是因为**它们把成本转嫁给了作者**——
R4 的一侧输入是他要在正文里手写的 `<!-- nh: loc=… -->`，R5 的一侧是显式说话人标签。
真书上前者是 0%、后者是 8.2%。**「零误报」在一条永远跑不起来的规则上是免费的。**

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

补法是三条作者入口（`POST …/declare/death` / `POST …/declare/first-appearance` +
`POST /nodes` 的 `first_appears_chapter`；当时还各有一条同名子命令，随命令行面一起删了，
见 ADR 0034），钉住它的是
`tests/test_rules_fire.py`：**每一个字都从 HTTP 进去**，先断言「什么都没声明时两条规则
是哑的」，再断言它们各自报出一条。**规则本身一行没改**（除了那两句建议语的措辞）——
这一课是「一条规则写完了、测试绿着，和它在产品里能开火，是两件事」。

**2026-08-14：R3 那一半不再需要作者动手。** 抽取器长出了 `kind="death"`
（`extract/models.py` 那段论证：模型在**封闭枚举**里挑一个，`value_key` 仍由引擎
写死成常量，铁律 2 没破），端到端由 `tests/test_extractor_feeds_r3.py` 钉住——
判据同上，只断言两头：模型说「他死了」→ 后面的章 check 报出「死人说话」。
`POST …/declare/death` 留着当**改**的入口（模型漏了或判错时作者手上得有一条路）。

**R2 那一半不会跟进，这不是待办。** `first_appears_chapter` 的主要用法是
「这东西我打算第 200 章才让它出场」——那是**作者的计划**，物理上不在已写文本里
（ADR 0004：墙上那把枪是不是伏笔，取决于他第 200 章打不打算开枪）。
模型读不出没写下来的意图，所以这一格只能是作者填的。
"""

from __future__ import annotations

from . import dead_speaks, future_leak
from .base import Check, CheckContext, Issue

ALL_CHECKS: tuple[Check, ...] = (
    future_leak.check,
    dead_speaks.check,
)
"""按声明顺序跑。加一条规则 = 加一个文件 + 在这里加一项。"""


def run_checks(ctx: CheckContext, checks: tuple[Check, ...] = ALL_CHECKS) -> list[Issue]:
    """跑全部规则（`POST …/chapters/{n}/check` / `scripts/demo.sh` 的入口）。

    **不吞异常。** 一条规则炸了应当整个红，而不是让作者以为「这章没问题」——
    静默的零 issue 和真的零 issue 在面板上长得一模一样。
    """
    issues: list[Issue] = []
    for check in checks:
        issues.extend(check(ctx))
    return issues


__all__ = [
    "ALL_CHECKS",
    "Check",
    "CheckContext",
    "Issue",
    "run_checks",
]
