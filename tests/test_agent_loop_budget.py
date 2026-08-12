"""**先把账算对** —— ADR 0023 的「前置」那一节，本文件是它唯一的执行者。

> `agent/loop.py` 的预算**只量对话、不量工具声明**……**约 18% 的系统性低估，方向偏松**。
> **在省之前先量准**——否则本 ADR 所有「省了多少」都是估的。
> 且要有一条断言：**预算量出来的数，和真正发出去那份 payload 的实际大小，
> 误差必须在一个写死的范围内。**

那条断言就是 `test_the_budget_and_the_real_payload_agree_within_a_frozen_band`。
它是这次全部「省了多少」的地基：**没有它，剪枝报出来的每一个数都是一个没有量纲的数。**

── 「真正发出去那份 payload」在这个仓库里是可指的 ────────────────────────

不是一个抽象概念：`draft/provider.py::complete()` 把 `_wire_kwargs()` 出来的那个 dict
原样 `**` 给 `client.chat.completions.create`。**那个 dict 就是 payload**，
所以这份测试量的是它，不是量一份「差不多的东西」。

**两侧口径故意不完全相同**，而差额是单向的：`payload_units()` 只量 `messages` + `tools`
（这一层认得的全部），wire 上还多一层信封（`model` / `max_tokens` / `stream` /
reasoning 那几个字段），由 `ProviderConfig` + `CallPlan` 决定，而 loop **有意不认识它们**
（`ModelPort` 存在的理由）。信封只会让实际**更大**，所以断言是
`0 <= 实际 − 量出来的 <= PAYLOAD_ENVELOPE_UNITS`——一个上界加一个方向。

方向那一半和上界一样重要：**预算量出来的数如果比实际大**，剪枝就会在没必要的时候动手；
比实际小（今天这个洞的形状）则是**偏松**——以为发了 17,000，实际发了 21,000。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from novel_harness.agent.loop import (
    PAYLOAD_ENVELOPE_UNITS,
    AgentMessage,
    Cancellation,
    Conversation,
    ModelCallReceipt,
    Projection,
    Role,
    StopReason,
    payload_units,
    project,
    run_turn,
    start_conversation,
    tool_declaration_units,
)
from novel_harness.agent.loop import _measured_units  # 增量口径 —— 见对拷那一条
from novel_harness.agent.model import agent_call_plan
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import tool_declarations
from novel_harness.draft.length import DraftLanguage, count_units
from novel_harness.draft.product_context import DEFAULT_MEMORY_BUDGET
from novel_harness.draft.provider import (
    CompletionResult,
    ProviderConfig,
    ToolCall,
    _wire_kwargs,
)


# ══════════════════════════════════════════════════════════════════════════
# 器材
# ══════════════════════════════════════════════════════════════════════════

ROUTES = (
    ("https://api.openai.com/v1", "gpt-5.6"),
    ("https://api.deepseek.com", "deepseek-v4-pro"),
    ("https://api.deepseek.com", "deepseek-v4-flash"),
    ("https://api.anthropic.com/v1", "claude-opus-4-8"),
    ("https://openrouter.ai/api/v1", "anthropic/claude-opus-4.8"),
    # **没登记的那一档也要量**：作者自建端点走的是它，而它的信封最短
    # （没有 reasoning 方言），所以它是那条带子的下沿。
    ("http://localhost:11434/v1", "qwen3-32b"),
)


def a_realistic_session() -> Conversation:
    """一段真实形状的会话：作者两句 + 一次工具往返 + 一段推理。"""
    return start_conversation("冷一点，少用形容词。").model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="第 40 章想让萧决在雨里等一个人。"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="先看看这一章不许说破什么。",
                    tool_calls=(
                        ToolCall(
                            id="c1", name="scene_constraints", arguments='{"chapter": 40}'
                        ),
                    ),
                ),
                AgentMessage(
                    role=Role.TOOL,
                    content='{"chapter": 40, "cast": ["萧决"], "must_not_reveal": []}',
                    tool_call_id="c1",
                    chapter=40,
                ),
                AgentMessage(role=Role.USER, content="那就写吧，" + "别太煽情，" * 40),
            )
        }
    )


def real_payload_units(
    projection: Projection, base_url: str, model: str, tools: list[dict[str, Any]]
) -> int:
    """**真正会被 `**` 出去的那个 dict** 有多少字（`draft/provider.py::_wire_kwargs`）。"""
    config = ProviderConfig(model=model, base_url=base_url, api_key="k")
    _, plan = agent_call_plan(config)
    kwargs = _wire_kwargs(config, plan, projection.messages, tools=tools)
    return count_units(json.dumps(kwargs, ensure_ascii=False), DraftLanguage.ZH)


# ══════════════════════════════════════════════════════════════════════════
# 一、那条断言本身
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("base_url,model", ROUTES)
def test_the_budget_and_the_real_payload_agree_within_a_frozen_band(
    base_url: str, model: str
) -> None:
    """**ADR 0023「前置」那条断言。** 六条真实路由各量一次。

    上界写死在 `PAYLOAD_ENVELOPE_UNITS`；下界是 0，也就是**量出来的数永远不许比实际大**。
    """
    declarations = tool_declarations()
    projected = project(
        a_realistic_session(), 40, budget_units=100_000, tools=declarations
    )
    actual = real_payload_units(projected, base_url, model, declarations)
    gap = actual - projected.payload_units

    assert gap >= 0, (
        f"{model}：量出来的比实际还大（差 {-gap} 字）。预算比实际大 = 在没必要的时候剪枝，"
        "而剪掉的东西作者是看得见的（回执上那几个数）。"
    )
    assert gap <= PAYLOAD_ENVELOPE_UNITS, (
        f"{model}：信封 {gap} 字，超过写死的 {PAYLOAD_ENVELOPE_UNITS}。\n"
        "**别直接把那个数字调大**：信封变大只有两种来法——模型名特别长（无害），"
        "或者 `_wire_kwargs` 开始往请求里塞新东西（那才是要看的：每一次调用都在多付"
        "一笔没人记账的钱）。"
    )


def test_the_two_ways_of_measuring_the_payload_are_one_number() -> None:
    """一次性量（`payload_units`）和增量量（`_measured_units`）必须**逐字节同解**。

    剪枝的每一步走增量、最后报出去的走一次性的话，会出现「剪完了却还是喊装不下」
    那种停不下来的形态——而它在测试里长得像一次偶发的 `CONTEXT_FULL`。
    """
    from novel_harness.agent.loop import _cost, _json_units, _wire

    declarations = tool_declarations()
    tool_costs = [_json_units(d) for d in declarations]
    conversation = a_realistic_session()

    for take in range(len(conversation.messages) + 1):
        chosen = list(conversation.prefix) + list(conversation.messages[:take])
        for tools in ([], declarations):
            costs = tool_costs if tools else []
            assert _measured_units([_cost(m) for m in chosen], costs) == payload_units(
                [_wire(m) for m in chosen], tools
            )
    assert _measured_units([], []) == payload_units([], [])


# ══════════════════════════════════════════════════════════════════════════
# 二、自守卫：**这张网抓不抓得住被推翻的那个量法**
# ══════════════════════════════════════════════════════════════════════════


def test_the_band_would_catch_a_budget_that_forgets_the_tool_declarations() -> None:
    """3.3 那一版的量法（只量对话）**必须**冲出这条带子。

    没有这一条，上面那条断言可能只是因为带子宽到什么都装得下——而这个仓库的规矩是
    「一道假绿的守卫等于没有守卫」。
    """
    declarations = tool_declarations()
    projected = project(
        a_realistic_session(), 40, budget_units=100_000, tools=declarations
    )
    conversation_only = payload_units(projected.messages, [])  # ← 被推翻的那个实现
    actual = real_payload_units(projected, *ROUTES[0], declarations)

    assert actual - conversation_only > PAYLOAD_ENVELOPE_UNITS, (
        "只量对话的那个旧口径居然还落在容差里 —— 那说明容差太宽，这条断言什么都没验。"
    )


def test_the_floor_is_far_bigger_than_the_tolerance() -> None:
    """工具声明这块**地板**必须远大于容差，否则「忘了算它」能藏在容差里。

    **判据是两个数的比，不是一个抄下来的字数**：加工具的那天地板自己变大，
    这条只会更成立；真要让它红，得是工具表缩到只剩一条描述——那时该重新想的是别的事。
    """
    floor = tool_declaration_units()
    assert floor > PAYLOAD_ENVELOPE_UNITS * 4, (
        f"工具声明只有 {floor} 字，而容差是 {PAYLOAD_ENVELOPE_UNITS} —— "
        "地板小到能藏进容差里的时候，上面那条自守卫就失效了。"
    )
    # 相对默认预算（能力表没登记这个模型时的兜底）的占比。ADR 说「约 18%」，
    # **这里不钉一个会漂的百分比**，只钉「它大到不可忽略」这件事本身。
    assert floor * 10 > DEFAULT_MEMORY_BUDGET.total, (
        "地板占默认预算不到一成的话，ADR 0023「前置」那一节的前提就不成立了 —— "
        "那时该核对的是它，不是这条断言。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 三、账变准之后，行为跟着变的那两处
# ══════════════════════════════════════════════════════════════════════════


def test_the_receipt_says_how_much_of_the_budget_is_the_floor() -> None:
    """回执要说得出「这一份有多大、预算多少、其中多少是雷打不动的」。

    同 `index.py` 那三条纪律的第一条：**裁了什么必须说出来**。一个只报「剪了 3 条」
    而不报「一共多大」的回执，读起来永远像「省了很多」。
    """
    projected = project(a_realistic_session(), 40, budget_units=100_000)
    assert projected.budget_units == 100_000
    assert projected.tool_units == tool_declaration_units()
    assert projected.payload_units > projected.tool_units
    assert not projected.over_budget


def test_a_budget_that_cannot_even_hold_the_tool_table_says_so() -> None:
    """预算连工具声明都装不下时**如实喊装不下**，而不是剪光对话之后假装成功。

    这一档以前不可能发生（声明不在账上），所以它是这次改动**新长出来的**一种真实形态：
    作者把预算调得很小，或者换了一个窗口很小的模型。那时该看见的是
    `CONTEXT_FULL`（「这段对话说得太长，装不下了」），不是一次把作者的话原样发出去的调用。
    """
    projected = project(a_realistic_session(), 40, budget_units=tool_declaration_units())
    assert projected.over_budget, "工具声明自己就吃掉全部预算，这一份不该被判成装得下"
    authors = [m for m in projected.messages if m["role"] == "user"]
    assert len(authors) == 2, "装不下也**不许**动作者说过的话 —— 那是最后一档，而它不存在"


# ══════════════════════════════════════════════════════════════════════════
# 四、端到端：量的和发的是**同一个对象**
# ══════════════════════════════════════════════════════════════════════════


class _Recorder:
    """记下真正被交给运输层的那两样东西。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []

    def __call__(
        self, messages: Any, *, tools: Any, cancel: Cancellation
    ) -> CompletionResult:
        self.messages = list(messages)
        self.tools = list(tools)
        return CompletionResult(text="好的。", model="m", finish_reason="stop")


class _FakeStore:
    """`StoryGraph` 的最小替身（同 `test_agent_loop.py`）：这条断言碰不到图。"""

    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def test_run_turn_measures_the_object_it_actually_sends() -> None:
    """`run_turn` 传给 `project()` 的工具声明，就是它下一行发出去的那一份。

    两次分别构造的话就又有一处能漂，而漂掉的症状是「回执上的数和账单对不上」——
    没有任何东西会报错。
    """
    recorder = _Recorder()
    receipts: list[ModelCallReceipt] = []
    result = run_turn(
        start_conversation().with_author("写第 40 章"),
        context=ToolContext(store=_FakeStore(), project_id="p"),  # type: ignore[arg-type]
        model=recorder,
        ledger=receipts.append,
    )
    assert result.reason is StopReason.DONE
    assert result.projection is not None
    assert result.projection.payload_units == payload_units(
        recorder.messages, recorder.tools
    ), "量的和发的不是同一份"
