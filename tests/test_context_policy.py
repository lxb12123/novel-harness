"""对抗性验证：**上下文的账、剪枝的类别、作者的规矩**（ADR 0023）。

这份文件不重复 `test_agent_loop_budget.py` / `test_agent_rules.py` 已经量过的东西。
它只问六个「反过来看会怎样」的问题，每一个都配一个**反向探针**——
一条断言如果在被推翻的实现上也照样绿，那它什么都没验。

| 节 | 问的是 | 探针 |
|---|---|---|
| 一 | 预算量的那个数，和真发出去那份 payload，是不是**两条独立的路**算的 | 四种「会少算」的假量法，每一种都必须冲出容差带 |
| 二 | 剪枝的类别判据是**结构**还是一张会漂的表 | 现场注册第十条工具，再拿一张按名字认的表当对照 |
| 三 | 极端预算下作者说的话一字不改，而且**是最后一个被动的** | 通用做法（按「新不新」剪）当对照，它会先吃掉作者的第一句 |
| 四 | 切了章的规矩**真的失效了**吗（fail-open，跟 `must_not_reveal` 相反） | 把过滤器换成 fail-closed，断言这张网当场红 |
| 五 | 章级规矩有没有混进稳定前缀（边界六） | 手工造一份「混进去了」的前缀，断言逐字节比对认得出来 |
| 六 | 数重复数的是「**作者**说了几遍」还是别的 | 语义分组当对照 + 一条同批重复的反例 |

── 为什么第一节要在**剪枝真的发生过**之后再量一次 ────────────────────────

`test_agent_loop_budget.py` 那条断言跑在 `budget_units=100_000` 上，那一份投影**一个字
都没剪**。而 ADR 0023 全部的重量在「省了多少」上，被判的恰恰是剪完之后那一份——
剪枝路径上多算/少算一次，那条断言看不见。

── 为什么第七节在这儿而不是在别处 ────────────────────────────────────────

`budget_units` 的**单位**在 2026-08-12 变了（对话 → 整份 payload），而喂它的那个默认值
（`ToolContext.return_units`）没变，它至今仍然是「**一次工具返回**的天花板」。两个数
从此不是同一种量，而没有任何一条断言在看它们的关系。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from novel_harness.agent import rules as rules_module
from novel_harness.agent.loop import (
    PAYLOAD_ENVELOPE_UNITS,
    AgentMessage,
    Cancellation,
    Conversation,
    ModelCallReceipt,
    Projection,
    Role,
    StopReason,
    _cost,
    _wire,
    payload_units,
    project,
    run_turn,
    start_conversation,
    tool_declaration_units,
)
from novel_harness.agent.model import agent_call_plan
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.rules import is_rule, rule_key, rule_message, surviving_rule_indices
from novel_harness.agent.tools import (
    TOOL_TABLE,
    TOOLS,
    ToolSpec,
    dispatch,
    tool_declarations,
)
from novel_harness.draft.length import DraftLanguage, count_units
from novel_harness.draft.provider import (
    CompletionResult,
    ProviderConfig,
    ToolCall,
    _wire_kwargs,
)


# ══════════════════════════════════════════════════════════════════════════
# 器材
# ══════════════════════════════════════════════════════════════════════════


class FakeStore:
    """`StoryGraph` 的最小替身：这份文件里没有一条断言碰得到图。"""

    def resolve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def a_context(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {"store": FakeStore(), "project_id": "project:policy"}
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


@dataclass
class Recorder:
    """记下**真正被交给运输层**的那两样东西，一轮里每一次调用各存一份。"""

    reply: CompletionResult = field(
        default_factory=lambda: CompletionResult(
            text="好的。", model="m", finish_reason="stop"
        )
    )
    messages: list[list[dict[str, Any]]] = field(default_factory=list)
    tools: list[list[dict[str, Any]]] = field(default_factory=list)

    def __call__(
        self, messages: Any, *, tools: Any, cancel: Cancellation
    ) -> CompletionResult:
        self.messages.append(list(messages))
        self.tools.append(list(tools))
        return self.reply


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def reasoned(text: str) -> AgentMessage:
    return AgentMessage(role=Role.ASSISTANT, content=text)


def asked(call_id: str, name: str, chapter: int) -> AgentMessage:
    return AgentMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=(
            ToolCall(id=call_id, name=name, arguments=json.dumps({"chapter": chapter})),
        ),
    )


def answered(call_id: str, content: str, chapter: int | None = None) -> AgentMessage:
    return AgentMessage(
        role=Role.TOOL, content=content, tool_call_id=call_id, chapter=chapter
    )


def a_session(*messages: AgentMessage, house_style: str | None = None) -> Conversation:
    return start_conversation(house_style).model_copy(
        update={"messages": tuple(messages)}
    )


AUTHOR_LINES = (
    "第 40 章我想让萧决在雨里等一个人，" + "别写成苦情戏，" * 30,
    "上一版太满了，" + "留白多一点，" * 30,
    "就照这个方向再来一版。",
)


def a_session_worth_pruning() -> Conversation:
    """一段有得剪的会话：三句作者的话 + 两段推理 + 两次工具往返 + 一条**章级**规矩。

    每一档都放得够长，好让预算扫描能一档一档把它们逼出来。规矩记在**两个不同的
    作者回合**里（所以它是章级的，整章有效）——否则它会因为过期而消失，
    而这几条断言要验的是「预算剪不动它」，不是「它有没有过期」。
    """
    return a_session(
        said(AUTHOR_LINES[0]),
        reasoned("先看看这一章不许说破什么。" + "想" * 300),
        asked("c1", "scene_constraints", 40),
        answered("c1", "禁说清单：" + "秘" * 900, chapter=40),
        rule_message("别写打斗", chapter=40),
        said(AUTHOR_LINES[1]),
        asked("c2", "chapter_text", 40),
        answered("c2", "正" * 900, chapter=40),
        rule_message("别写打斗", chapter=40),
        reasoned("这一段的节奏是这样的。" + "念" * 300),
        said(AUTHOR_LINES[2]),
        house_style="冷一点，少用形容词。",
    )


def floor_units(conversation: Conversation, tools: list[dict[str, Any]]) -> int:
    """这段会话「一条历史都不发」时那份 payload 有多大：工具声明 + 稳定前缀 + JSON 信封。

    **量出来的，不是抄来的**（同 `test_agent_loop.py::room`）：抄一个数下来，
    别人往 `AGENT_SYSTEM_PROMPT` 里加两行就会把这些断言弄红，而那两行和剪枝无关。
    """
    bare = conversation.model_copy(update={"messages": ()})
    return project(bare, None, budget_units=10**9, tools=tools).payload_units


def wire_units(projection: Projection, tools: list[dict[str, Any]]) -> int:
    """**真正会被 `**` 出去的那个 dict** 有多少字（`draft/provider.py::_wire_kwargs`）。

    这是这份文件里唯一一条「实际」路径：它走的是产品代码，不是把 `payload_units()`
    换个写法再算一遍——同一个函数量两遍是空转。
    """
    config = ProviderConfig(
        model="deepseek-v4-pro", base_url="https://api.deepseek.com", api_key="k"
    )
    _, plan = agent_call_plan(config)
    kwargs = _wire_kwargs(config, plan, projection.messages, tools=tools)
    return count_units(json.dumps(kwargs, ensure_ascii=False), DraftLanguage.ZH)


def author_lines(projection: Projection) -> list[str]:
    return [m["content"] for m in projection.messages if m["role"] == "user"]


# ══════════════════════════════════════════════════════════════════════════
# 一、账：两条独立的路，而且**剪完之后也要对得上**
# ══════════════════════════════════════════════════════════════════════════


def test_the_number_still_matches_the_wire_after_pruning_actually_fired() -> None:
    """**剪完之后**再量一次。ADR 0023 全部的重量在「省了多少」上，被判的就是这一份。

    上游那条断言跑在 `budget_units=100_000` 上，那一份一个字都没剪——剪枝路径上
    多算/少算一次它看不见，而剪枝路径正是这次改动新长出来的那一段。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    floor = floor_units(conversation, declarations)

    fired = 0
    for room in (2_400, 1_600, 900, 400, 100):
        projected = project(
            conversation, 40, budget_units=floor + room, tools=declarations
        )
        if (
            projected.stubbed_results
            or projected.dropped_calls
            or projected.dropped_reasoning
        ):
            fired += 1
        gap = wire_units(projected, declarations) - projected.payload_units
        assert 0 <= gap <= PAYLOAD_ENVELOPE_UNITS, (
            f"剩 {room} 字时量出来的和真发出去的差 {gap} 字 —— "
            "剪枝路径上的口径和最后报出去的口径不是同一个"
        )
    assert fired >= 4, "这几档预算居然一次都没剪 —— 那这条断言验的还是没剪的那一份"


def test_the_two_ends_of_the_pipe_are_the_same_number_when_the_budget_bites() -> None:
    """`over_budget` 和 `payload_units` 只许有一个口径。

    两个口径的症状是**剪完了还是喊装不下**——一个停不下来的形态，而它在测试里
    长得像一次偶发的 `CONTEXT_FULL`。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    for budget in (1, 100, floor_units(conversation, declarations), 100_000):
        projected = project(conversation, 40, budget_units=budget, tools=declarations)
        assert projected.over_budget == (projected.payload_units > projected.budget_units)
        assert projected.payload_units == payload_units(projected.messages, declarations)


# ── 自守卫：四种「会少算」的假量法，一个都不许藏进容差里 ────────────────────


def _forgets_the_tools(projection: Projection, tools: list[dict[str, Any]]) -> int:
    """3.3 那一版：只量对话。"""
    return payload_units(projection.messages, [])


def _forgets_the_prefix(projection: Projection, tools: list[dict[str, Any]]) -> int:
    """只量 `messages`、漏掉稳定前缀——「前缀是缓存的，不用算」是一个很顺的错。"""
    prefix = len(start_conversation("冷一点，少用形容词。").prefix)
    return payload_units(projection.messages[prefix:], tools)


def _forgets_the_json(projection: Projection, tools: list[dict[str, Any]]) -> int:
    """只数正文的字，不数 JSON 的键名和引号（`index.py::_cost` 早就否掉过这个写法）。"""
    body = "".join(str(m.get("content", "")) for m in projection.messages)
    return count_units(body, DraftLanguage.ZH) + tool_declaration_units(tools)


def _forgets_one_message(projection: Projection, tools: list[dict[str, Any]]) -> int:
    """漏掉一整条消息——「最后那条还没进历史」「那条是占位不用算」都会长成这个形状。

    探针丢掉的是**最大的那一条**，理由要说清楚：这条带子挡得住的是「差多少字」，
    不是「漏了几条」。一条 200 字的消息掉了，256 字的容差里根本看不出来（这是容差
    这种判据本身的极限，不是实现的毛病）——所以探针必须丢一条大到有意义的。
    """
    biggest = max(range(len(projection.messages)), key=lambda i: _json_units_of(projection, i))
    kept = [m for i, m in enumerate(projection.messages) if i != biggest]
    return payload_units(kept, tools)


def _json_units_of(projection: Projection, index: int) -> int:
    return count_units(
        json.dumps(projection.messages[index], ensure_ascii=False), DraftLanguage.ZH
    )


@pytest.mark.parametrize(
    "broken",
    [_forgets_the_tools, _forgets_the_prefix, _forgets_the_json, _forgets_one_message],
    ids=["tools", "prefix", "json", "one-message"],
)
def test_every_way_of_under_counting_blows_the_band(broken: Any) -> None:
    """**自守卫**：造一个会少算的量法，断言它当场冲出容差带。

    没有这一节，那条 `gap <= PAYLOAD_ENVELOPE_UNITS` 可能只是因为带子宽到什么都装得下。
    容差 256 字不是一个小数——它比这个仓库里好几样真东西都大，所以「它抓得住什么」
    必须逐项写出来，而不是靠一句「留了一倍余量」。
    """
    declarations = tool_declarations()
    projected = project(
        a_session_worth_pruning(), 40, budget_units=100_000, tools=declarations
    )
    actual = wire_units(projected, declarations)
    honest = actual - projected.payload_units
    assert 0 <= honest <= PAYLOAD_ENVELOPE_UNITS

    gap = actual - broken(projected, declarations)
    assert gap > PAYLOAD_ENVELOPE_UNITS, (
        f"这个少算了的量法只差 {gap} 字，藏得进 {PAYLOAD_ENVELOPE_UNITS} 字的容差里 —— "
        "那说明容差挡不住它这一类的错"
    )


def test_run_turn_still_sends_exactly_what_it_measured_when_it_had_to_prune() -> None:
    """端到端：**剪过枝**的那一轮，回执上那个数仍然等于真发出去的那份。

    上游那条端到端断言用的是一段两句话的会话（没得剪）。量的和发的漂开只会在
    有东西被剪掉的时候发生，所以这一条必须跑在剪过的那一份上。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    recorder = Recorder()
    receipts: list[ModelCallReceipt] = []
    result = run_turn(
        conversation,
        context=a_context(working_chapter=40),
        model=recorder,
        ledger=receipts.append,
        budget_units=floor_units(conversation, declarations) + 900,
    )
    assert result.reason is StopReason.DONE
    assert result.projection is not None
    assert result.projection.stubbed_results >= 1, "这一轮没剪成，那它验不了「剪完还对不对」"
    assert result.projection.payload_units == payload_units(
        recorder.messages[0], recorder.tools[0]
    ), "量的和发的不是同一份"


# ══════════════════════════════════════════════════════════════════════════
# 二、剪枝的类别判据是**结构**，不是一张会漂的表
# ══════════════════════════════════════════════════════════════════════════


class MoodArgs(BaseModel):
    """第十条工具的入参。**它有一个叫 `chapter` 的整数字段，别的什么都没有。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1, description="要查第几章（AS OF 第几章，纯查询坐标）。")


class MoodResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    mood: str


def _handle_mood(args: MoodArgs, context: ToolContext) -> MoodResult:
    return MoodResult(chapter=args.chapter, mood="阴" * 900)


@pytest.fixture
def a_tenth_tool(monkeypatch: pytest.MonkeyPatch) -> ToolSpec:
    """**现场往表里加一条工具**，一行剪枝代码都不改。

    这就是「表会漂」的直接检验：判据如果是一张按名字写死的清单，新工具的返回会当场
    掉进「认不出 ⇒ 不筛 / 不剪」那一档，而没有任何东西会报错。
    """
    spec = ToolSpec(
        name="chapter_mood",
        description="查第 N 章的情绪基调（本文件现场注册的第十条工具）。",
        args=MoodArgs,
        handler=_handle_mood,  # type: ignore[arg-type]
    )
    monkeypatch.setattr("novel_harness.agent.tools.TOOL_TABLE", (*TOOL_TABLE, spec))
    monkeypatch.setattr(
        "novel_harness.agent.tools.TOOLS", {**TOOLS, spec.name: spec}
    )
    return spec


def _mood_turn(chapter: int) -> tuple[AgentMessage, AgentMessage]:
    call = ToolCall(
        id="m1", name="chapter_mood", arguments=json.dumps({"chapter": chapter})
    )
    outcome = dispatch(call, a_context())
    assert outcome.ok, outcome.content
    return (
        AgentMessage(role=Role.ASSISTANT, content="", tool_calls=(call,)),
        AgentMessage(
            role=Role.TOOL,
            content=outcome.content,
            tool_call_id=outcome.call_id,
            chapter=outcome.chapter,
        ),
    )


def test_a_brand_new_tool_lands_in_the_right_class_with_no_code_change(
    a_tenth_tool: ToolSpec,
) -> None:
    """第十条工具的返回，按**结构**归类：绑得上章号、进第一档、壳和调用成对拿掉。"""
    declarations = tool_declarations()
    assert any(d["function"]["name"] == "chapter_mood" for d in declarations), (
        "声明由表生成 —— 加了一条它就该在里面"
    )

    asked_at_90, got_at_90 = _mood_turn(90)
    assert got_at_90.chapter == 90, "章号是从入参的结构上取的，不是从一张工具名表上查的"
    assert not is_rule(got_at_90) and not is_rule(asked_at_90)

    # 一、按章号取：绑第 90 章的返回不许出现在第 40 章的投影里（边界五）。
    later = a_session(said("先看看第 90 章"), asked_at_90, got_at_90)
    at_forty = project(later, 40, budget_units=100_000, tools=declarations)
    assert at_forty.off_chapter == 1
    assert "阴" not in str(at_forty.messages)

    # 三、按预算剪：第一档删内容留壳，第二档壳和调用成对拿掉。
    asked_at_40, got_at_40 = _mood_turn(40)
    here = a_session(said("看第 40 章"), asked_at_40, got_at_40, said("接着写"))
    floor = floor_units(here, declarations)
    stubbed = project(here, 40, budget_units=floor + 400, tools=declarations)
    assert stubbed.stubbed_results == 1 and stubbed.dropped_calls == 0
    assert "tool" in [m["role"] for m in stubbed.messages], "壳不能删，wire 上它要接住调用"

    dropped = project(here, 40, budget_units=floor + 120, tools=declarations)
    assert dropped.dropped_calls == 1
    assert "tool" not in [m["role"] for m in dropped.messages]
    assert author_lines(dropped) == ["看第 40 章", "接着写"]


_A_TABLE_THAT_WILL_DRIFT = frozenset(
    {"scene_constraints", "character_state", "draft_chapter", "chapter_text"}
)


def _chapter_by_name_table(message: AgentMessage, calls: dict[str, str]) -> int | None:
    """**探针：按工具名认「这条绑第几章」**——加工具的那天它就漂了。"""
    name = calls.get(message.tool_call_id, "")
    return message.chapter if name in _A_TABLE_THAT_WILL_DRIFT else None


def test_the_name_table_version_would_have_leaked_the_new_tools_return(
    a_tenth_tool: ToolSpec,
) -> None:
    """自守卫：那张按名字认的表，在第十条工具上给出**不同**的答案。

    不同在哪一侧要说清楚：它把「绑第 90 章」认成「不绑章号」⇒ 投影一条都不筛 ⇒
    第 90 章的返回跟着模型回头写第 40 章。**那正是边界五说的「一个等着发生的跨章泄漏」**，
    而它不会报错。
    """
    asked_at_90, got_at_90 = _mood_turn(90)
    names = {"m1": "chapter_mood"}

    assert got_at_90.chapter == 90
    assert _chapter_by_name_table(got_at_90, names) is None, (
        "探针自己就认得出新工具的话，它证明不了「表会漂」这件事"
    )

    later = a_session(said("先看看第 90 章"), asked_at_90, got_at_90)
    ours = project(later, 40, budget_units=100_000)
    assert ours.off_chapter == 1 and "阴" not in str(ours.messages)

    # 同一段历史，把章号按那张表重标一遍——第 90 章那份就留在第 40 章的投影里了。
    by_table = later.model_copy(
        update={
            "messages": tuple(
                message.model_copy(
                    update={"chapter": _chapter_by_name_table(message, names)}
                )
                if message.role is Role.TOOL
                else message
                for message in later.messages
            )
        }
    )
    theirs = project(by_table, 40, budget_units=100_000)
    assert theirs.off_chapter == 0 and "阴" in str(theirs.messages)


# ══════════════════════════════════════════════════════════════════════════
# 三、作者说的话：一字不改，而且**是最后一个被动的**
# ══════════════════════════════════════════════════════════════════════════


def test_the_authors_words_survive_every_budget_all_the_way_down_to_zero() -> None:
    """把预算一路压到 0：作者那几句**逐字节相同、顺序不变、一句不少**。

    第四档不存在——剪到只剩作者说的话仍然装不下时，这一层的动作是把 `over_budget`
    立起来让调用方停下来说人话，不是悄悄把他说过的话吃掉。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    floor = floor_units(conversation, declarations)

    for budget in [floor + 3_000, floor + 800, floor + 200, floor, floor // 2, 1, 0]:
        projected = project(
            conversation, 40, budget_units=budget, tools=declarations
        )
        assert author_lines(projected) == list(AUTHOR_LINES), (
            f"预算 {budget}：作者说过的话被动了 —— 那是全场唯一不可重建的东西"
        )

    starved = project(conversation, 40, budget_units=0, tools=declarations)
    assert starved.over_budget, "装不下就要如实喊装不下，不许剪光了假装成功"
    assert [m["role"] for m in starved.messages] == [
        "system",  # 稳定前缀：身份
        "system",  # 稳定前缀：文风
        "user",
        "user",
        "system",  # 作者的规矩 —— 它不在剪枝链上（ADR 0023 那张表第二行）
        "user",
    ]
    assert "别写打斗" in str(starved.messages), "规矩按章号过期，不按预算剪"


def test_the_author_is_the_last_thing_standing_not_the_first_thing_cut() -> None:
    """**顺序**也要验，不只是「有没有被删」。

    判据写成蕴含式而不是三个写死的数：后一档动手 ⇒ 前一档已经把能剪的都剪完了。
    这样它罩得住「有人把顺序倒过来」，也罩得住「有人在中间插一档」。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    floor = floor_units(conversation, declarations)
    stubbable = sum(
        1 for message in conversation.messages if message.role is Role.TOOL
    )
    callers = sum(
        1 for message in conversation.messages if message.tool_calls
    )

    seen = set()
    for room in range(3_000, -1, -25):
        projected = project(
            conversation, 40, budget_units=floor + room, tools=declarations
        )
        wire_roles = [m["role"] for m in projected.messages]
        if projected.dropped_calls:
            assert projected.stubbed_results == stubbable, (
                "第二档动手时第一档必须已经剪无可剪 —— 删内容留壳比整条拿掉便宜"
            )
        if projected.dropped_reasoning:
            assert projected.dropped_calls == callers
            assert "tool" not in wire_roles
        seen.add(
            (
                bool(projected.stubbed_results),
                bool(projected.dropped_calls),
                bool(projected.dropped_reasoning),
            )
        )
    assert (True, False, False) in seen, "第一档从来没单独出现过 —— 这个扫描没扫到它"
    assert (True, True, False) in seen, "第二档从来没在第三档之前单独出现过"
    assert (True, True, True) in seen, "第三档从来没被逼出来过"


def _pruned_by_recency(conversation: Conversation, budget: int) -> list[AgentMessage]:
    """**探针：通用做法——按「新不新」剪**（ADR 0023 决策一开头点名的那个错坐标）。

    它不问「这东西丢了能不能重新拿回来」，只从头往后扔，直到装得下。
    """
    kept = list(conversation.messages)
    spent = (
        tool_declaration_units()
        + sum(_cost(message) for message in conversation.prefix)
        + sum(_cost(message) for message in kept)
    )
    while kept and spent > budget:
        spent -= _cost(kept[0])
        kept.pop(0)
    return kept


def test_a_recency_pruner_would_have_eaten_the_authors_first_sentence() -> None:
    """自守卫：按「新不新」剪的那一版，**先吃掉的就是作者的第一句**。

    没有这一条，上面那两条可能只是因为这段会话里作者的话本来就排在最后。
    """
    conversation = a_session_worth_pruning()
    declarations = tool_declarations()
    budget = floor_units(conversation, declarations) + 800

    theirs = _pruned_by_recency(conversation, budget)
    assert [m.content for m in theirs if m.role is Role.USER] != list(AUTHOR_LINES), (
        "探针自己都没丢掉作者的话 —— 那它证明不了「按新不新剪是错坐标」"
    )
    ours = project(conversation, 40, budget_units=budget, tools=declarations)
    assert author_lines(ours) == list(AUTHOR_LINES)


# ══════════════════════════════════════════════════════════════════════════
# 四、规矩是 fail-open：切了章就**真的**不发了
# ══════════════════════════════════════════════════════════════════════════


def a_session_with_a_rule() -> Conversation:
    """第 40 章定过一条规矩（作者说过两遍 ⇒ 已经是章级），然后他又说了一句。

    **作者那几句里一个字都不许和规矩的措辞重合**：规矩过期之后作者的话照旧原样发出去
    （那是全场唯一不可重建的东西），措辞撞上的话「规矩还在不在」就判不出来了——
    这份文件第一版就是这么写的，于是那条断言恒红。
    """
    return a_session(
        said("第 40 章清淡一点，能不动手就别动手"),
        rule_message("别写打斗", chapter=40),
        said("我再说一次，这一章就是要静"),
        rule_message("别写打斗", chapter=40),
        said("好，接着写"),
    )


def _rule_reached_the_wire(recorder: Recorder) -> bool:
    """那条规矩**真的被发出去过**吗。判的是运输层收到的那份，不是投影的回执。"""
    return any(
        "别写打斗" in str(message.get("content", ""))
        for call in recorder.messages
        for message in call
    )


def test_switching_chapters_really_stops_sending_the_rule_over_the_wire() -> None:
    """**判在运输层的入口上**，不判 `Projection.messages`。

    回执和真发出去的那份是两个东西，而 ADR 0023 那条 fail-open 承诺的是后者：
    「留着 = 第 200 章写不出打戏，而**作者不知道为什么**」说的是模型收到了什么。
    """
    conversation = a_session_with_a_rule()

    here = Recorder()
    run_turn(
        conversation,
        context=a_context(working_chapter=40),
        model=here,
        ledger=lambda receipt: None,
    )
    assert _rule_reached_the_wire(here), "同一章里它还该在 —— 不然这条断言验的是别的东西"

    later = Recorder()
    result = run_turn(
        conversation,
        context=a_context(working_chapter=90),
        model=later,
        ledger=lambda receipt: None,
    )
    assert not _rule_reached_the_wire(later), (
        "第 40 章定的偏好跟到了第 90 章。留着的代价是他在第 200 章写不出打戏，"
        "**而他不知道为什么**（ADR 0023 决策二，方向跟 `must_not_reveal` 相反）"
    )
    assert result.projection is not None and result.projection.expired_rules == 1


def test_the_rule_goes_when_there_is_no_chapter_coordinate_at_all() -> None:
    """没有坐标（`working_chapter is None`）时规矩**全放**——同一个「不知道」，
    工具返回全留、规矩全放，因为两边猜错的代价不对称。"""
    blind = Recorder()
    run_turn(
        a_session_with_a_rule(),
        context=a_context(),
        model=blind,
        ledger=lambda receipt: None,
    )
    assert not _rule_reached_the_wire(blind)


def _fail_closed_indices(
    messages: Any, chapter: int | None
) -> frozenset[int]:
    """**探针：把它「修」成 fail-closed**（第 40 章定的规矩第 90 章当然还算数）。

    这是下一个人最可能顺手写出来的那一版，而它一条别的测试都不会弄红。
    """
    return frozenset(
        index for index, message in enumerate(messages) if is_rule(message)
    )


def test_the_net_catches_a_fail_closed_rule_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自守卫：把过滤器换成 fail-closed，**上面那张网必须当场红**。

    `project()` 是在函数体里 `from .rules import …` 的（为了断一条环），所以换掉模块上
    那个名字就等于换掉了实现——这条自守卫因此判的是真的实现，不是一个平行宇宙。
    """
    monkeypatch.setattr(rules_module, "surviving_rule_indices", _fail_closed_indices)

    later = Recorder()
    run_turn(
        a_session_with_a_rule(),
        context=a_context(working_chapter=90),
        model=later,
        ledger=lambda receipt: None,
    )
    assert _rule_reached_the_wire(later), (
        "换成 fail-closed 之后这张网居然还是绿的 —— 那它一开始就没在验这件事"
    )


# ══════════════════════════════════════════════════════════════════════════
# 五、边界六：前导 system 块**逐字节相同**
# ══════════════════════════════════════════════════════════════════════════


def leading_system_block(projection: Projection) -> list[dict[str, Any]]:
    """投影最前面那一串 system 消息 —— 「稳定前缀」在 wire 上的样子。"""
    block: list[dict[str, Any]] = []
    for message in projection.messages:
        if message["role"] != "system":
            break
        block.append(message)
    return block


def test_the_leading_system_block_is_byte_identical_across_chapters() -> None:
    """同一段会话按第 40 / 90 章各投一次，**前导 system 块一个字节都不许差**。

    差一个字节 = 有逐章变的东西混进了本该跨章不变的那一块（边界六），而症状同边界五：
    产出的是一段读起来完全正常、只是说错了的正文，且因为缓存的存在会更持久。
    """
    conversation = a_session_with_a_rule()
    at_forty = project(conversation, 40, budget_units=100_000)
    at_ninety = project(conversation, 90, budget_units=100_000)

    block = leading_system_block(at_forty)
    assert block == leading_system_block(at_ninety)
    assert block == [_wire(message) for message in conversation.prefix]
    assert all("别写打斗" not in str(message["content"]) for message in block), (
        "章级规矩混进了前导 system 块 —— 它每一章都不一样，缓存它就是把它钉死"
    )

    # 章级规矩确实在这一份里，只是**排在作者第一句话之后**，不在前缀那一块。
    assert "别写打斗" in str(at_forty.messages)


def test_that_comparison_would_notice_a_rule_that_sneaked_into_the_prefix() -> None:
    """自守卫：手工造一份「混进去了」的前缀，断言上面那个比对认得出来。

    真的往 `Conversation.prefix` 里塞一条带章号的消息是**构造不出来的**
    （校验器拒收），所以探针造的是它在 wire 上的样子——那才是模型看得见的东西。
    """
    conversation = a_session_with_a_rule()
    honest = leading_system_block(project(conversation, 40, budget_units=100_000))
    leaked = [*honest, _wire(rule_message("别写打斗", chapter=40))]
    assert leaked != honest
    assert any("别写打斗" in str(message["content"]) for message in leaked)

    with pytest.raises(ValueError):
        Conversation(prefix=(rule_message("别写打斗", chapter=40),))


def test_promoting_the_same_rule_twice_never_grows_the_prefix() -> None:
    """升格的幂等判据必须和数重复用**同一个** `rule_key`，不是逐字符比原文。

    两条路的代价差着一个数量级：章级那条记重了只是多一章的浪费，而**稳定前缀里的
    东西永不过期、每一轮都重发到会话结束**。用原文比的话，作者（或者模型）说
    「冷一点」和「冷一点。」就是两条常驻规矩，而这个仓库刚刚才把
    「同一个事实两份拷贝」当成头号病。
    """
    assert rule_key("冷一点") == rule_key("冷一点。") == rule_key(" 冷一点 ")

    conversation = start_conversation()
    base = len(conversation.prefix)
    for spelling in ("冷一点", "冷一点。", " 冷一点 ", "冷一点，"):
        conversation = rules_module.promoted(conversation, spelling)
    assert len(conversation.prefix) == base + 1, (
        "同一条规矩换个标点就在稳定前缀里多住一条 —— 那是一份每轮都重发、永不过期的拷贝"
    )
    assert conversation.prefix[-1].content == "冷一点"


# ══════════════════════════════════════════════════════════════════════════
# 六、数重复：数的是「**作者**说了几遍」，而且是逐字符比
# ══════════════════════════════════════════════════════════════════════════


def _same_meaning(left: str, right: str) -> bool:
    """**探针：一个「懂意思」的分组器**（这儿用最粗的版本：共享一个词就算同一条）。

    真实现会是一次模型调用或者一份同义词表，形状不重要——重要的是它回答的是
    「这两句是不是一个意思」，而 ADR 0005 在 v1 里禁止本仓库长出这种能力。
    """
    return bool(set(left) & set(right) & set("煽情打斗"))


def test_counting_repeats_never_asks_whether_two_sentences_mean_the_same() -> None:
    """同一条的判据是**归一化之后逐字符相等**，不是「意思一不一样」。"""
    assert rule_key("别写打斗。") == rule_key(" 别写打斗 ") == rule_key("别写打斗")
    assert rule_key("别写打斗") != rule_key("不要写打戏")

    assert _same_meaning("别写打斗", "不要写打戏"), "探针自己就分不出来的话，它证明不了什么"
    rephrased = a_session(
        said("写三版"),
        rule_message("别太煽情", chapter=40),
        said("再写三版"),
        rule_message("别写得太煽情", chapter=40),
    ).with_author("再来一版")
    assert surviving_rule_indices(rephrased.messages, 40) == frozenset(), (
        "换个说法就该重新数 —— 认它们是同一条等于在这一层做语义判断（ADR 0005）"
    )


def test_widening_takes_two_author_turns_not_two_records() -> None:
    """**「说第二次」说的是作者说第二次**，不是引擎记了第二条（ADR 0023 决策二）。

    ADR 把这条机制写死成一个来回：**取窄**（就这一批）⇒ 猜窄了「作者再说一次（顺口，
    他本来就要评价下一批）」⇒ 升到章级。所以那个 2 数的是**作者开口的次数**。

    照「记了几条」数的话，模型在**同一批**里把同一条规矩记两遍就直接升到章级——
    而那一批里作者只说过一次。方向正是 ADR 点名最贵的那一侧：
    「猜宽了的代价是**一条隐形的规矩跟着他走，他不知道它在**」。
    它够得着，不是理论风险：`TurnLimits.repeat_limit` 允许同一个调用在一轮里出现三次，
    而 resume 补跑一条 `pending` 的调用就会把同一条规矩再记一遍。
    """
    one_turn_two_records = a_session(
        said("写三版，别太煽情"),
        rule_message("别太煽情", chapter=40),
        rule_message("别太煽情", chapter=40),  # 同一批里记了两遍，作者只说过一次
    ).with_author("再写三版")
    assert surviving_rule_indices(one_turn_two_records.messages, 40) == frozenset(), (
        "同一批里记两遍就升成章级了 —— 那个 2 数的是引擎写了几条，不是作者说了几遍"
    )

    two_turns = a_session(
        said("写三版，别太煽情"),
        rule_message("别太煽情", chapter=40),
        said("还是太煽情了"),
        rule_message("别太煽情", chapter=40),
    ).with_author("再写三版")
    assert surviving_rule_indices(two_turns.messages, 40), (
        "作者真说了两遍反而没升上去 —— 那这条机制永远升不上去"
    )


# ══════════════════════════════════════════════════════════════════════════
# 七、预算换了单位，喂它的那个数没换
# ══════════════════════════════════════════════════════════════════════════


def a_session_that_just_read_a_chapter(body: str) -> Conversation:
    """作者刚让它读了一整章，返回是索引层按 `return_units` 截出来的那一段。

    那个截断不是意外，是 `index.py::handle_chapter_text` 的正常行为：它会明说
    「这一章一共 N 字，预算只装得下 M 字」。所以「一条大得撑满额度的返回」是产品里
    真会出现的形状，不是一个构造出来的极端。
    """
    return a_session(
        said("把第 40 章读给我看看"),
        asked("t1", "chapter_text", 40),
        answered("t1", body, chapter=40),
    )


def a_history_filling_return(context: ToolContext) -> str:
    """一条**正好把对话那一侧的额度用满**的返回正文。

    大小是**算出来的**：把同一段会话的返回清空投一次，剩下的就是「地板 + 那几条壳」，
    额度减掉它就是这条返回还能有多长。写死一个数的话，别人改一句提示词就会把这条
    断言弄红，而那和预算的口径一点关系都没有。
    """
    declarations = tool_declarations()
    skeleton = a_session_that_just_read_a_chapter("")
    spent = project(skeleton, 40, budget_units=10**9, tools=declarations).payload_units
    floor = floor_units(skeleton, declarations)
    return "文" * (context.return_units - (spent - floor))


def test_a_return_that_fills_the_conversations_room_still_reaches_the_model() -> None:
    """一条**把对话额度用满**的工具返回，必须撑得到下一次模型调用。

    ── 为什么这是一条断言而不是一个调参 ──────────────────────────────────

    工具返回只有在**下一次**模型调用里才会被模型看见。它在那一次投影里被剪掉 =
    这一次工具调用的钱白花了、那段字进了持久化的对话却从没被读过，而模型看到的是
    「重新查一次」⇒ 它再查一次 ⇒ 三次之后 `REPEATED_CALL`，作者收到的说法是
    **「它在反复查同一件事」**——一个指向别处的解释。

    ── 两个数为什么会撞上 ────────────────────────────────────────────────

    `budget_units` 的单位在 2026-08-12 变了（对话 → **整份 payload**），而
    `run_turn` 的默认值还是 `ToolContext.return_units`，那个数至今的定义是
    「**一次工具返回**最多给多少字」，量的是对话那一侧。同一个数当两种量用，于是
    工具声明 + 稳定前缀这块**剪枝一个字都动不了**的固定开销是从对话的额度里扣掉的
    ——而它会随着工具表变长（3.6 加两条工具，地板从约 3,900 字长到 5,171 字）。
    """
    context = a_context(working_chapter=40)
    body = a_history_filling_return(context)
    recorder = Recorder()
    run_turn(
        a_session_that_just_read_a_chapter(body),
        context=context,
        model=recorder,
        ledger=lambda receipt: None,
    )
    assert "文" * 50 in str(recorder.messages[0]), (
        "刚读回来的那一章在下一次调用里就被剪掉了 —— 这次工具调用等于没发生，"
        "而模型会再查一次，直到撞上 `REPEATED_CALL`"
    )


def test_the_default_budget_is_the_conversations_room_plus_the_floor() -> None:
    """默认预算 = **那块剪不动的地板 + 对话的额度**，不是「对话的额度里再扣掉地板」。

    判据写成关系不写成一个数：加一条工具，地板自己变大，这一条跟着变——
    不用有人记得回来改，也就没有「加工具的那天所有会话静默变短一截」这件事。
    """
    context = a_context(working_chapter=40)
    conversation = a_session_that_just_read_a_chapter(a_history_filling_return(context))

    result = run_turn(
        conversation,
        context=context,
        model=Recorder(),
        ledger=lambda receipt: None,
    )
    assert result.projection is not None
    assert result.projection.budget_units >= (
        context.return_units + result.projection.tool_units
    ), "地板从对话的额度里扣，就等于每加一条工具都让所有会话的记性短一截"

    # 自守卫：把预算按**被推翻的那个口径**（整份 payload 只给 `return_units`）喂进去，
    # 那条返回当场没了——说明上面那条断言真的在挡这件事，而不是碰巧绿。
    starved = project(conversation, 40, budget_units=context.return_units)
    assert starved.stubbed_results == 1
