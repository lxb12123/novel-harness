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
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from ..graph import InformationScope, StoryGraph, TextAnchor

FIRE_SCOPE = InformationScope.CANON
"""规则唯一许可开火的层。**规则不许接受 scope 参数。**

PROVISIONAL 是「抽取的、未确认」，拿它报错等于用一条 Agent 猜出来的事实去质疑作者——
原则 5 的反面。CANON 是「作者确认过」，所以 R4 的全部断言都是「作者声明 vs 作者声明」。
"""


class Scene(BaseModel):
    """场景块的声明（PLAN §5「改 3」的那行 Markdown 注释）。

    ```markdown
    ## 场景 3
    <!-- nh: cast=萧决,顾清音,李管家 loc=青云城主府 goal=李管家试探萧决的身世 -->
    ```

    **`cast` / `loc` 是作者写的称呼原文，不是 node_id。** 规则自己去 `resolve`——
    因为「这个称呼解析不出唯一节点」正是规则必须闭嘴的那种情况，把解析藏在上游
    会让规则拿到一个已经被猜过一次的答案。

    这个类型是 `text/scenes.py`（解析与写回）和 `checks/` 的共享契约，定义在消费者
    这一侧，理由同 `TextAnchor`：**契约有两份定义就等于没有契约。**
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    """`## 场景 N` 里的 N。"""

    cast: list[str] = Field(default_factory=list)
    """在场角色的称呼，顺序 = 作者写的顺序。"""

    loc: str | None = None
    goal: str | None = None

    para_index: int = Field(ge=0)
    """声明行在本章段落里的 0-based 位置。R4 的 Issue 锚在这里——
    它不读正文，但它报的问题**有**一个精确的物理位置：作者写错的那行声明。"""

    decl_text: str
    """声明行原文（`<!-- nh: ... -->`），作为锚的 `quote_text`。"""


class Issue(BaseModel):
    """一条检查结果。**没有 offset，也永远不会有**（ADR 0006）。

    v1 的规则只报问题、给定位、给建议——**作者自己在自己的编辑器里改**（PLAN 改 13）。
    局部 Patch 推到 v1.1，理由是它会持续制造 offset 漂移，而 Evidence 锚定和
    revalidate 还没被真实压测过。
    """

    model_config = ConfigDict(frozen=True)

    rule: str
    """哪条规则产出的（R2 / R3 / R4 / R5）。用于「关掉某条规则」和归因误报。"""

    issue_type: str
    """**开放字符串枚举**（ADR 0005）：SQLite 改 CHECK 要重建整张表，而 issue_type
    只进 `validation_report.issues_json`，不参与任何过滤。

    v1 填规则自己的名字（`LOCATION_CONFLICT`）。ConStory-Bench 的 5 类 19 子类映射
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

    scenes: Sequence[Scene] = field(default_factory=tuple)
    """本章的场景块声明，按出现顺序。R1 / R4 只吃这个，**不读正文**。"""

    paragraphs: Sequence[str] | None = None
    """本章正文，按段落切开；`para_index` 是它的 0-based 下标。

    `None` = 调用方没提供正文，**读正文的规则必须直接返回 `[]`，不许报错**——
    面板路径（不读正文，2–5ms）和规则路径（debounce 2s）是两条链路（§6 serious #3）。

    R2 FUTURE_LEAK / R3 DEAD_SPEAKS 的唯一数据源（2026-08-02 已落地）。
    R5 ADDRESS_CONFLICT 不在 v1：它的生死等 Day 1 下午的说话人标签覆盖率实测
    （ADR 0005，≥10% 才进 v1）。
    """


Check = Callable[[CheckContext], list[Issue]]
"""贡献者入口的类型。一个文件一条规则，一个纯函数，零注册仪式。"""
