"""规则的贡献者入口：`check(ctx: CheckContext) -> list[Issue]`（PLAN §11 / §9）。

**规则是纯函数。** 这不是风格偏好，它是社区贡献的入口设计：贡献者要读懂的东西只有
这个文件里的两个类型。判分器（eval）和 Validator（写作时）跑的是同一份代码——
§9 的骨架把 `checks/` 标成「★ 判分器 == Validator，同一份代码」，因为一旦分叉，
kill-gate 量出来的就不是产品的行为。

── 三条铁律，写新规则前先读 ──────────────────────────────────────────────

1. **不做语义判断**（ADR 0005）：任何需要回答「这句话是什么意思」的规则不进 v1。
2. **锚是 `TextAnchor` 三元组，永不 offset**（ADR 0006 Day 2 定死）。`TextAnchor`
   从 `graph.models` import，**不在这里另造一个三元组**——那个契约有两份实现就等于没有。
3. **只在 CANON 层开火**（PLAN §5.4）。所以 `CheckContext` 里**没有** scope 字段：
   PROVISIONAL「永不开火」不是靠每条规则自觉传对参数，是靠这里根本没有那个旋钮。
   （同 §5.4 对 PLANNED 的处置：把约束下沉成 filter，让违反在物理上不可能。）
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from ..graph import InformationScope, StoryGraph, TextAnchor

FIRE_SCOPE = InformationScope.CANON
"""规则唯一许可开火的层。**规则不许接受 scope 参数。**

PROVISIONAL 是「抽取的、未确认」，拿它报错等于用一条 Agent 猜出来的事实去质疑作者——
原则 5 的反面。

⚠️ **2026-08-14 起它没有显式调用方**：唯一那个（R4 的 `state_at(..., scope=FIRE_SCOPE)`）
随 R4 一起砍了（[ADR 0027](../../../docs/adr/0027-scene-blocks-cut.md)），而 R3 走的是
`state_at` 的默认值——它**恰好**也是 `CANON`。留着这个常量是因为那个「恰好」是这条
铁律今天唯一的实现：默认值哪天改了，这儿才有一个地方能把话说清楚。
"""


# ⚠️ **`Scene` 和 `CheckContext.scenes` 2026-08-14 删了**（[ADR 0027](../../../docs/adr/0027-scene-blocks-cut.md)）。
# 场景块是作者要在正文里手写的 `## 场景 N` + `<!-- nh: cast=… loc=… -->`，唯一的消费者
# 是 R4——而两样一起砍掉的判据是**真书上零覆盖**：导进来的稿子里一个场景块都不会有，
# 于是 R4 结构性地永远跑不了，而屏幕上还挂着一句催作者去补的话。
#
# **别把它当成「以后要恢复的东西」再加回来。** 场景块想要的那些信息（这一章分了几场、
# 每场谁在、在哪儿）都不是「作者写之前先填」的东西，是**写出来的结果**——ADR 0018 已经
# 就「在场是谁」下过一次同样的判断，`mentioned.py` 是那次的产物。真要有「每场在哪儿」，
# 出处是抽取器（它已经在写 `LOCATED_AT` 了），不是一套要作者去学的标记语法。


class Issue(BaseModel):
    """一条检查结果。**没有 offset，也永远不会有**（ADR 0006）。

    v1 的规则只报问题、给定位、给建议——**作者自己在自己的编辑器里改**（PLAN 改 13）。
    局部 Patch 推到 v1.1，理由是它会持续制造 offset 漂移，而 Evidence 锚定和
    revalidate 还没被真实压测过。
    """

    model_config = ConfigDict(frozen=True)

    rule: str
    """哪条规则产出的（今天只剩 R2 / R3）。用于「关掉某条规则」和归因误报。"""

    issue_type: str
    """**开放字符串枚举**（ADR 0005）：SQLite 改 CHECK 要重建整张表，而 issue_type
    只进 `validation_report.issues_json`，不参与任何过滤。

    v1 填规则自己的名字（`FUTURE_LEAK`）。ConStory-Bench 的 5 类 19 子类映射
    等 `validation_report` 真的开始写行时再补——v1 一行不写，现在映射是在无数据的
    情况下猜分类法。
    """

    chapter: int
    """全书顺序位置（`chapter.number`），不是正文里印的章号。"""

    anchor: TextAnchor
    message: str

    suggested_action: str | None = None
    """**由规则确定性产出**（PLAN 改 13）：「师兄」→「萧决」的答案图上就写着，
    在一个已知答案的地方引入 LLM 是净损失。"""


@dataclass(frozen=True, slots=True)
class CheckContext:
    """规则的全部输入。**规则不许从别处取数据**——否则它就不是纯函数，也就不可复现。

    不是 Pydantic：`store` 是一个 Protocol 对象，让 Pydantic 去校验它没有任何收益。
    """

    store: StoryGraph
    project_id: str

    chapter: int
    """全书顺序位置（1-based），由导入器分配。不是正文里印的章号——分卷重启和番外
    会让印号重复，而时态过滤 `valid_from_chapter <= :ch` 要求全序键。"""

    paragraphs: Sequence[str] | None = None
    """本章正文，按段落切开；`para_index` 是它的 0-based 下标。

    `None` = 调用方没提供正文，**读正文的规则必须直接返回 `[]`，不许报错**——
    面板路径（不读正文，2–5ms）和规则路径（debounce 2s）是两条链路（§6 serious #3）。

    R2 FUTURE_LEAK / R3 DEAD_SPEAKS 的唯一数据源（2026-08-02 已落地）。
    R5 ADDRESS_CONFLICT 不在 v1：2026-08-02 已按 ADR 0005 判据砍掉
    （真书样本显式标签覆盖率 8.2% < 10%，[ADR 0014]）。
    """


Check = Callable[[CheckContext], list[Issue]]
"""贡献者入口的类型。一个文件一条规则，一个纯函数，零注册仪式。"""
