"""对抗性验证 —— ADR 0019 的**边界五**（投影按章号）和**边界六**（稳定前缀）。

这两条单独拿出来验，理由是那份 ADR 自己的「若此决策错误」那一节写的：

> **边界五写错**（投影没带章号）：……**但它错的时候不会报错**：产出的是一段读起来
> 完全正常、只是说破了不该说破的东西的正文。
> **边界六写错**：把逐章变的东西缓存进了稳定前缀。**症状同边界五**，且因为缓存的
> 存在会更持久。

「不会报错」意味着**没有任何一条别的测试会因为它坏掉而红**。所以只能把那个场景构造出来。

── 这份文件和 `test_agent_loop.py` 的分工 ────────────────────────────────

那一份验的是「投影**会**按章号筛」；这一份验的是**筛得对不对**，以及**筛完发出去的东西
还合不合法**。两处刻意重叠了一个场景（第 90 章 → 第 40 章），但这儿的那一份是
**端到端**跑出来的：

> **投影函数的签名里有 `chapter` 参数 ≠ 它真的按 `chapter` 投影**，
> 同理**手工把 `chapter=90` 贴在消息上 ≠ 真实链路会把它贴成 90**。

所以这儿的会话不是手写 canonical，是让 `run_turn` 真的跑一轮、让 `dispatch` 真的去取
那个章号（`ToolOutcome.chapter`）、再让第二轮的投影去筛它。中间任何一环断了这条都会红。

── 自守卫 ──────────────────────────────────────────────────────────────

`test_the_net_would_catch_a_recency_projection` 造了一份**按最近 N 条切**的假投影
（ADR 0019 边界五点名的那种错法），断言本文件的判据抓得住它。没有这一条，下面所有
「泄漏不存在」的断言都可能只是因为网本身是破的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

import novel_harness.agent.tools as tools_module
from novel_harness.agent.loop import (
    AGENT_SYSTEM_PROMPT,
    AgentMessage,
    Cancellation,
    Conversation,
    Projection,
    Role,
    project,
    run_turn,
    start_conversation,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.loop import tool_declaration_units
from novel_harness.agent.tools import TOOL_TABLE, ToolSpec, tool_declarations
from novel_harness.draft.provider import CompletionResult, ToolCall


# ══════════════════════════════════════════════════════════════════════════
# 器材：一个按剧本回答的模型 + 一个「第 N 章不许说破什么」的假工具
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ScriptedModel:
    """按剧本一句一句回答；剧本用完就重复最后一条。**记下每一次真正发出去的 messages。**"""

    script: list[CompletionResult]
    calls: list[list[dict[str, Any]]] = field(default_factory=list)
    declared: list[list[str]] = field(default_factory=list)

    def __call__(
        self, messages: Any, *, tools: Any, cancel: Cancellation
    ) -> CompletionResult:
        self.calls.append(list(messages))
        self.declared.append([t["function"]["name"] for t in tools])
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]

    @property
    def last_prompt(self) -> str:
        return str(self.calls[-1])


def say(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", finish_reason="stop")


def asks(name: str, arguments: str, *, text: str = "", call_id: str = "c") -> CompletionResult:
    return CompletionResult(
        text=text,
        model="m",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )


class FakeList(BaseModel):
    """假的「第 N 章不许说破什么」。**形状照着真的 `ConstraintsResult` 的那两列长。**"""

    chapter: int
    must_not_reveal: list[str]


# `ch40 ⊇ ch90` —— ADR 0019 边界二写死的方向：到第 90 章更多人已经知道了，清单更短。
FORBIDDEN = {
    40: ["血脉秘密", "玄铁令下落", "换子"],
    90: ["血脉秘密"],
}


def payload(chapter: int) -> str:
    """那一章的清单**序列化之后逐字节**长什么样。

    判据必须是整份 payload，不能是某个秘密的名字：第 90 章那份是第 40 章那份的**子集**，
    按名字找的话「血脉秘密」两边都有，抓不出是哪一份留在了 context 里。
    """
    return FakeList(chapter=chapter, must_not_reveal=FORBIDDEN[chapter]).model_dump_json()


def _fake_constraints(args: Any, context: ToolContext) -> BaseModel:
    return FakeList(chapter=args.chapter, must_not_reveal=FORBIDDEN[args.chapter])


@pytest.fixture()
def constraint_tool() -> Any:
    """把 `scene_constraints` 的实现换成上面那个假的，**入参模型一个字不换**。

    换实现不换入参，是为了让 `dispatch` 那条「章号从已校验的入参上结构性地取」的路径
    照原样走一遍——这条测试要证的正是那一整条链，不是投影函数一个人。
    """
    original = tools_module.TOOLS["scene_constraints"]
    tools_module.TOOLS["scene_constraints"] = ToolSpec(
        name="scene_constraints",
        description=original.description,
        args=original.args,
        handler=_fake_constraints,
    )
    try:
        yield
    finally:
        tools_module.TOOLS["scene_constraints"] = original


class FakeStore:
    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(chapter: int | None) -> ToolContext:
    return ToolContext(
        store=FakeStore(),  # type: ignore[arg-type]
        project_id="project:projection",
        working_chapter=chapter,
    )


def a_ledger(receipt: Any) -> None:
    """这份文件不验记账（`test_agent_loop.py` 验），但 `ledger` 是必填参数。"""


def a_turn(
    conversation: Conversation,
    *,
    chapter: int | None,
    script: list[CompletionResult],
) -> tuple[Conversation, ScriptedModel]:
    model = ScriptedModel(script=script)
    result = run_turn(
        conversation, context=a_context(chapter), model=model, ledger=a_ledger
    )
    return result.conversation, model


def wandered_to_chapter_forty(cursor_follows_the_author: bool) -> ScriptedModel:
    """先聊第 90 章，再回头改第 40 章。**两轮都是真的跑出来的。**

    Args:
        cursor_follows_the_author: 第二轮作者的光标有没有跟着切到第 40 章。
            `False` 就是「他在聊天里说『回头改第 40 章』，但界面上没动」——
            而工具表允许模型为任意一章查约束、起草（`ports.working_chapter` 写着
            它「只标不挡」），所以这**不是异常，是设计允许的常态**。

    Returns:
        第二轮那个模型（`calls` 里是它每一步真正收到的 messages）。
    """
    conversation, _ = a_turn(
        start_conversation().with_author("第 90 章这里怎么收？"),
        chapter=90,
        script=[asks("scene_constraints", '{"chapter": 90}', call_id="c90"), say("这样收。")],
    )
    _, model = a_turn(
        conversation.with_author("先回去改第 40 章"),
        chapter=40 if cursor_follows_the_author else 90,
        script=[asks("scene_constraints", '{"chapter": 40}', call_id="c40"), say("好。")],
    )
    return model


# ══════════════════════════════════════════════════════════════════════════
# 一、边界五：ADR 自己点名的那个场景，端到端
# ══════════════════════════════════════════════════════════════════════════


def test_chapter_forty_never_sees_chapter_ninetys_shorter_list_end_to_end(
    constraint_tool: Any,
) -> None:
    """ADR 0019「若此决策错误」那一节逐字点名的那条测试。

    > 构造一个「先聊第 90 章再回头写第 40 章」的会话，
    > 断言第 40 章的投影里**不含第 90 章的禁说清单**。

    这一份是**端到端**的：章号不是手工贴上去的，是 `dispatch` 从已校验的入参里取的。
    链条上任何一环断掉（`_asked_chapter` 取不到、`_outcome_message` 没往下传、投影不筛）
    这一条都会红，而**别的什么都不会红**。
    """
    model = wandered_to_chapter_forty(cursor_follows_the_author=True)

    assert payload(90) not in model.last_prompt, (
        "第 90 章那份**更短**的清单还在第 40 章的 context 里 —— "
        "模型会以为「只有这一条不能说」，而这是 fail-open 的最坏那侧。"
    )
    assert payload(40) in model.last_prompt, "第 40 章自己那份必须在"


def recency_project(conversation: Conversation, chapter: int | None, *, keep: int) -> Projection:
    """**按「最近 N 条」切的假投影** —— ADR 0019 边界五点名的那种错法。

    > 通行的投影按「时间近」切……**这里不够。** 作者会从第 90 章回头改第 40 章——
    > 那时「最近」是错的坐标。

    它收 `chapter` 却**根本不用**，正是「签名里有参数 ≠ 真的按它投影」的最小样本。
    """
    tail = list(conversation.messages)[-keep:]
    answered = {m.tool_call_id for m in tail if m.role is Role.TOOL}
    tail = [
        m
        for m in tail
        if not (m.role is Role.ASSISTANT and m.tool_calls)
        or all(c.id in answered for c in m.tool_calls)
    ]
    return Projection(
        chapter=chapter,
        messages=[
            {"role": str(m.role), "content": m.content}
            for m in (*conversation.prefix, *tail)
        ],
    )


def test_the_net_would_catch_a_recency_projection(constraint_tool: Any) -> None:
    """**自守卫。** 上面那条「泄漏不存在」的断言，只有在网本身抓得住泄漏时才算数。

    这里拿同一段 canonical 喂给一个按最近 N 条切的投影，断言判据当场红。
    """
    conversation, _ = a_turn(
        start_conversation().with_author("第 90 章这里怎么收？"),
        chapter=90,
        script=[asks("scene_constraints", '{"chapter": 90}', call_id="c90"), say("这样收。")],
    )
    conversation, _ = a_turn(
        conversation.with_author("先回去改第 40 章"),
        chapter=40,
        script=[asks("scene_constraints", '{"chapter": 40}', call_id="c40"), say("好。")],
    )

    faked = str(recency_project(conversation, 40, keep=6).messages)
    assert payload(90) in faked, "探针没造出泄漏 —— 那么上面那条断言什么都没证明"

    honest = str(project(conversation, 40, budget_units=100_000).messages)
    assert payload(90) not in honest


def test_the_chapter_ninety_results_really_leave_the_context_not_just_the_draft(
    constraint_tool: Any,
) -> None:
    """边界五堵的是「**过期约束根本没离开过 context**」，不是「起草时没用上它」。

    边界二（起草工具只收章号）管的是后者；这一条量的是前者——第 90 章那批工具返回
    在第 40 章这一轮里，**从第一次模型调用起就不在**，而不是最后一次才被摘掉。
    """
    model = wandered_to_chapter_forty(cursor_follows_the_author=True)
    assert model.calls, "这一轮一次模型调用都没发生，下面的断言是空的"
    for step, sent in enumerate(model.calls):
        assert payload(90) not in str(sent), f"第 {step + 1} 次模型调用里还带着第 90 章的清单"


# ══════════════════════════════════════════════════════════════════════════
# 二、坐标错的时候，错在哪一侧（本次找到的真问题）
# ══════════════════════════════════════════════════════════════════════════


def test_a_lagging_cursor_must_not_delete_the_long_list_and_keep_the_short_one(
    constraint_tool: Any,
) -> None:
    """**这条曾经是红的。** 判据用 `!=` 时，坐标一错就亲手造出 ADR 点名的那个故障。

    场景：作者在聊天里说「回头改第 40 章」，但界面上的光标还在第 90 章
    （`working_chapter=90`）。模型照做，为第 40 章查了约束，拿到那份**更长**的清单。

    `!=` 的判据把「往前的章」和「往后的章」当成一回事，于是下一步的投影
    **把第 40 章那份长清单删掉、把第 90 章那份短清单留下** —— 比不筛还坏：
    不筛的时候两份都在，模型至少看得见长的那份。

    方向是 ADR 0019 边界二写死的：`ch40 的 must_not_reveal ⊇ ch90 的`。
    往后的章更短（fail-open，必须丢），往前的章是超集（fail-closed，留着）。
    """
    model = wandered_to_chapter_forty(cursor_follows_the_author=False)

    assert payload(40) in model.last_prompt, (
        "模型刚为第 40 章查到的那份**更长**的清单被投影删掉了，"
        "而第 90 章那份更短的还在 —— 这正是这条 ADR 说「错的时候不会报错」的那个故障。"
    )


def test_an_earlier_chapters_superset_is_kept_because_over_restricting_is_visible() -> None:
    """往前的章那些返回**留着**，这是一条有代价的裁定，写在这儿是为了它别被悄悄改掉。

    留着的代价：`character_state` 那类连续性事实会过期（人已经死了、地方已经换了），
    模型可能把一个死人写活。那是**作者一眼看得见**的错。
    丢掉的代价：少一条 `must_not_reveal`，产出一段读起来完全正常、只是说破了不该说破
    的东西的正文 —— **没有任何东西会报错**。这条 ADR 的全部重量都在后半句上。
    """
    conversation = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 90 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="p", name="scene_constraints", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content="早先那份", tool_call_id="p", chapter=40),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="f", name="scene_constraints", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content="更后面那份", tool_call_id="f", chapter=200),
            )
        }
    )
    projected = project(conversation, 90, budget_units=100_000)
    blob = str(projected.messages)
    assert "早先那份" in blob, "第 40 章那份是超集，丢它只会让模型少禁一条"
    assert "更后面那份" not in blob, "第 200 章那份更短，留它就是 fail-open"
    assert projected.off_chapter == 1


# ══════════════════════════════════════════════════════════════════════════
# 三、剪枝顺序：老 tool_result → 老 tool_call → 中间推理 → **最后才碰作者**
# ══════════════════════════════════════════════════════════════════════════


def a_session_worth_pruning() -> Conversation:
    """一段必须剪的会话：两轮工具 + 两段推理 + 三句作者的话。"""
    return start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="作者第一句：" + "写" * 60),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="推理甲：" + "想" * 60,
                    tool_calls=(ToolCall(id="t1", name="book_index", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content="老返回：" + "目" * 300, tool_call_id="t1"),
                AgentMessage(role=Role.USER, content="作者第二句：" + "再" * 60),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="推理乙：" + "念" * 60,
                    tool_calls=(ToolCall(id="t2", name="book_index", arguments="{}"),),
                ),
                AgentMessage(role=Role.TOOL, content="新返回：" + "录" * 300, tool_call_id="t2"),
                AgentMessage(role=Role.USER, content="作者第三句：" + "接" * 60),
            )
        }
    )


def room(units: int) -> int:
    """留给**对话**这么多字的那个 `budget_units`（同 `test_agent_loop.py::room`）。

    2026-08-12（ADR 0023「前置」）起 `budget_units` 管的是**整份 payload**：工具声明那
    近 4,000 字以前一个字都不在账上。它是一块剪枝动不了的地板，所以「把预算从宽扫到紧」
    这类测试要从地板之上开始扫——否则第一个取值就已经装不下，三档剪枝一次都不会被触发，
    而**测试仍然是绿的**（那正是这份文件最怕的那种绿）。
    """
    return units + tool_declaration_units()


def _authors_lines(projection: Projection) -> list[str]:
    return [m["content"] for m in projection.messages if m["role"] == "user"]


def test_the_author_is_the_last_thing_standing_at_every_budget() -> None:
    """**顺序错了的症状是「作者的要求被丢了而工具返回还在」——那是最气人的一种。**

    所以这条不挑一两个预算点，它从宽到紧扫一遍，在**每一个**预算上量三条不变式：

    1. 作者说的三句，一句都没少、一个字都没改；
    2. 动了中间推理的时候，工具返回**已经一条不剩**（不许越档）；
    3. 剪到只剩作者的话还装不下时，`over_budget` 立起来 —— 这一层的最后一档是
       **停下来**，不是悄悄把作者说过的话吃掉。
    """
    conversation = a_session_worth_pruning()
    expected = [m.content for m in conversation.messages if m.role is Role.USER]
    assert len(expected) == 3

    saw_reasoning_dropped = False
    saw_over_budget = False
    for budget in range(2_000, 20, -20):
        projected = project(conversation, None, budget_units=room(budget))

        assert _authors_lines(projected) == expected, f"预算 {budget}：作者的话被动了"

        if projected.dropped_reasoning:
            saw_reasoning_dropped = True
            assert not [m for m in projected.messages if m["role"] == "tool"], (
                f"预算 {budget}：中间推理已经被丢，工具返回却还在 —— 越档了。"
                "ADR 0019 的顺序是老 tool_result → 老 tool_call → 中间推理 → 作者。"
            )
        if projected.over_budget:
            saw_over_budget = True
            assert projected.dropped_reasoning >= 2, "还有推理可剪就不该喊装不下"

    assert saw_reasoning_dropped, "预算扫得不够紧，第三档一次都没被触发"
    assert saw_over_budget, "预算扫得不够紧，`over_budget` 一次都没被触发"


def test_the_oldest_goes_first_in_the_first_two_tiers() -> None:
    """「**老** `tool_result` → **老** `tool_call`」：同一档之内先动更早的那条。"""
    conversation = a_session_worth_pruning()

    stubbed_one = next(
        p
        for budget in range(2_000, 20, -1)
        if (p := project(conversation, None, budget_units=room(budget))).stubbed_results == 1
    )
    blob = str(stubbed_one.messages)
    assert "老返回" not in blob and "新返回" in blob, "第一档动的必须是更早的那条返回"

    dropped_one = next(
        p
        for budget in range(2_000, 20, -1)
        if (p := project(conversation, None, budget_units=room(budget))).dropped_calls == 1
    )
    calls = [c["id"] for m in dropped_one.messages for c in m.get("tool_calls", ())]
    assert calls == ["t2"], "第二档拿掉的必须是更早的那个调用"


def test_a_stub_that_would_make_the_prompt_bigger_is_not_a_stub() -> None:
    """**这条曾经是红的。** 第一档是「删内容留壳」，而壳有它自己的长度。

    返回比占位还短的时候（`{}`、一条 40 字的「没查到」），把它换成占位是**三重损失**：
    内容没了、prompt 反而更大、而且它把第二档往下压——本来剪两条调用够用，现在得剪四条。
    短返回不需要第一档救，第二档会把它连壳带调用一起拿掉，那才是它的档位。
    """
    messages: list[AgentMessage] = []
    for i in range(6):
        messages.append(
            AgentMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(ToolCall(id=f"s{i}", name="book_index", arguments="{}"),),
            )
        )
        messages.append(AgentMessage(role=Role.TOOL, content="{}", tool_call_id=f"s{i}"))
    messages.append(AgentMessage(role=Role.USER, content="写第 7 章"))
    conversation = start_conversation().model_copy(update={"messages": tuple(messages)})

    whole = project(conversation, None, budget_units=100_000)
    baseline = len(str(whole.messages))

    tight = project(conversation, None, budget_units=room(1_000))
    assert tight.stubbed_results == 0, "占位比原返回还长，第一档不该动它"
    assert len(str(tight.messages)) <= baseline, "「剪枝」把 prompt 剪大了"


def test_nothing_in_this_layer_compresses_the_manuscript() -> None:
    """**正文永不压缩。** 这一层只剪不压，剪掉的东西**重查一次就有，而且是当前的**。

    这儿量的是行为（`test_agent_loop.py` 那条量的是代码里没有摘要器）：
    被第一档动过的那条，留下的必须是那句「重新查一次」的占位，**不是一段浓缩**。
    """
    conversation = a_session_worth_pruning()
    stubbed = next(
        p
        for budget in range(2_000, 20, -1)
        if (p := project(conversation, None, budget_units=room(budget))).stubbed_results >= 1
    )
    survivors = [m["content"] for m in stubbed.messages if m["role"] == "tool"]
    for content in survivors:
        assert content.startswith("老返回") or content.startswith("新返回") or "重新查一次" in content


# ══════════════════════════════════════════════════════════════════════════
# 四、稳定前缀（边界六）：判据只有一条 —— 这个东西换一章会不会变
# ══════════════════════════════════════════════════════════════════════════


CHAPTER_BOUND_SHAPES = (
    re.compile(r"第\s*\d+\s*章"),
    re.compile(r"must_not_reveal|forbidden_entities|believed_value"),
    re.compile(r"不得写破|尚未登场|认知边界|认知矩阵|本场设定要点|【在场】"),
    re.compile(r"还不知道「|误以为「|知道「"),
)
"""**逐章变的东西长什么样。** 一条命中就说明有东西不该在前缀里。

判据是「**带上了具体的那一章**」，不是「谈到了章号这回事」：`AGENT_SYSTEM_PROMPT` 里那句
「不许说破的东西是逐章算的」**跨章逐字节相同**，它是一条讲规矩的话，不是一份清单；
而 `第 90 章的认知边界` / `must_not_reveal: [...]` / `- 萧决：还不知道「血脉秘密」`
换一章就变。头一版的网把前者也咬了——**假红的下场是被人把守卫删掉**，所以收窄到
「具体章号 + 序列化字段名 + `assemble.py` 那几个渲染模板的原文」。

这是关键词网不是语义检查（ADR 0005）：抓得住顺手写进去的那一种，抓不住换个说法的那一种。
`write_rule` 那个自由文本入口本来就守不住（`loop.py` 模块 docstring 末尾自己交代了），
这张网守的是**代码**哪天往前缀里塞了一块逐章算出来的东西。
"""


def _leading_system_blocks(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """wire 最前面那一串 system 消息 —— 前缀缓存真正能覆盖的就是它们。"""
    head: list[dict[str, Any]] = []
    for message in sent:
        if message["role"] != "system":
            break
        head.append(message)
    return head


def test_the_head_of_the_wire_is_byte_identical_across_chapters(constraint_tool: Any) -> None:
    """**边界六的判据只有一条：这个东西换一章会不会变。**

    所以量法就是把同一段 canonical 按两个章号各投一次，比最前面那几块的**字节**。
    差一个字节 = 前缀缓存价值为零，且差的那部分就是被钉死在 context 里的逐章事实。
    """
    forty = wandered_to_chapter_forty(cursor_follows_the_author=True).calls[0]
    ninety = wandered_to_chapter_forty(cursor_follows_the_author=False).calls[0]

    head_forty = _leading_system_blocks(forty)
    head_ninety = _leading_system_blocks(ninety)
    assert head_forty, "wire 前面一块 system 都没有 —— 那这条测试什么都没量"
    assert head_forty == head_ninety, "换一章之后前缀变了 —— 那它就不是稳定前缀"


def test_no_chapter_bound_block_sits_in_the_stable_prefix(constraint_tool: Any) -> None:
    """逐个查前缀里那几块，**有没有任何一块是逐章变的**（`must_not_reveal` / 认知矩阵 /
    人物处境 / 带章号的东西）。

    一份被当作稳定前缀缓存起来的 `must_not_reveal`，就是一条被钉死在 context 里的过期
    约束，而它过期的方向是 fail-open 的最坏那侧。
    """
    sent = wandered_to_chapter_forty(cursor_follows_the_author=True).calls[-1]
    head = _leading_system_blocks(sent)

    for block in head:
        for shape in CHAPTER_BOUND_SHAPES:
            assert not shape.search(block["content"]), (
                f"稳定前缀里有一块逐章变的东西（命中 {shape.pattern!r}）：\n{block['content'][:200]}"
            )

    # 自守卫：这张网确实抓得住那种东西 —— 否则上面那个循环只是在数空气。
    assert any(shape.search(payload(40)) for shape in CHAPTER_BOUND_SHAPES)
    assert any(shape.search("截至第 90 章的认知边界：") for shape in CHAPTER_BOUND_SHAPES)


def test_the_prefix_is_where_it_is_by_construction_not_by_convention() -> None:
    """「唯一稳定的那块被夹在中间」这个错，在这一层**构造不出来**。

    `prefix` / `messages` 是两个字段，投影永远是 `prefix + 投影(messages)`，
    而校验器拒收带章号的消息和非 system 角色。
    """
    with pytest.raises(ValueError):
        Conversation(prefix=(AgentMessage(role=Role.SYSTEM, content="x", chapter=40),))
    with pytest.raises(ValueError):
        Conversation(prefix=(AgentMessage(role=Role.USER, content="x"),))

    tiny = project(a_session_worth_pruning(), None, budget_units=1)
    assert tiny.messages[0]["content"] == AGENT_SYSTEM_PROMPT, "前缀不许被剪掉"


def test_the_loop_declares_the_table_in_table_order_and_appends_nothing(
    constraint_tool: Any,
) -> None:
    """**声明的追加序**（3.2 已钉 `test_the_declaration_prefix_is_append_only`）：
    loop 有没有绕过它自己重排一份？

    声明和消息一样进前缀缓存的键，插一条在中间 = 作废整段缓存。所以这里比的是**顺序**，
    不是集合 —— 集合相等的重排一样会把缓存打掉。
    """
    model = wandered_to_chapter_forty(cursor_follows_the_author=True)
    in_table_order = [spec.name for spec in TOOL_TABLE]

    for declared in model.declared:
        assert declared == in_table_order, "loop 重排了工具声明"
    assert [d["function"]["name"] for d in tool_declarations()] == in_table_order


# ══════════════════════════════════════════════════════════════════════════
# 五、投影发出去的必须是一份**能发**的 wire
# ══════════════════════════════════════════════════════════════════════════


def _wire_is_valid(projection: Projection) -> bool:
    asked = {c["id"] for m in projection.messages for c in m.get("tool_calls", ())}
    answered = {m["tool_call_id"] for m in projection.messages if m["role"] == "tool"}
    return asked == answered


def test_a_call_that_nobody_ever_answered_still_gets_a_shell() -> None:
    """**这条曾经是红的**，而它红的时候的症状是供应商回 400 —— 在作者按下发送之后。

    `Conversation.pending_calls` 只认尾巴上那一批（它扫到 `USER` 就停）。于是
    「进程死在派发中间 → 作者回来又说了一句」之后，那个 `tool_call` 被那句话挡在了
    resume 的视野之外，**再也没有人会去补它**；而 OpenAI 兼容的 wire 上一条没人接住的
    `tool_call` 就是 400。

    补跑不是选项：结果只能追加到队尾，也就是排在作者那句新话后面，那个顺序照样是 400。
    所以动作是在投影里就地配壳 —— 投影是发出去之前的最后一道关口。
    """
    crashed = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 7 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="我查一下。",
                    tool_calls=(ToolCall(id="orphan", name="book_index", arguments="{}"),),
                ),
            )
        }
    )
    assert [c.id for c in crashed.pending_calls] == ["orphan"], "崩在派发前，resume 看得见它"

    came_back = crashed.with_author("还在吗？")
    assert came_back.pending_calls == (), "作者又说了一句之后，resume 就再也看不见它了"

    projected = project(came_back, None, budget_units=100_000)
    assert _wire_is_valid(projected), "没人接住的 tool_call 原样发出去 = 400"
    assert projected.filled_shells == 1, "配了壳就要说出来（同「裁了什么必须说出来」）"


def test_the_wire_stays_valid_through_every_stage_of_pruning() -> None:
    """按章号丢、按预算剪、就地配壳 —— **三段各自都得让壳配齐**，漏一段症状一样。"""
    conversation = start_conversation().model_copy(
        update={
            "messages": (
                AgentMessage(role=Role.USER, content="写第 40 章"),
                AgentMessage(
                    role=Role.ASSISTANT,
                    content="并发查两章。",
                    tool_calls=(
                        ToolCall(id="past", name="scene_constraints", arguments="{}"),
                        ToolCall(id="future", name="scene_constraints", arguments="{}"),
                        ToolCall(id="lost", name="book_index", arguments="{}"),
                    ),
                ),
                AgentMessage(role=Role.TOOL, content="早" * 200, tool_call_id="past", chapter=10),
                AgentMessage(
                    role=Role.TOOL, content="晚" * 200, tool_call_id="future", chapter=90
                ),
                AgentMessage(role=Role.USER, content="接着写"),
            )
        }
    )
    for budget in range(3_000, 20, -20):
        projected = project(conversation, 40, budget_units=room(budget))
        assert _wire_is_valid(projected), f"预算 {budget}：wire 上有对不上的调用"
