"""作者的规矩 —— [ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二。

六条硬的，逐节验：**默认只管当前这一章**（切章自动失效）/ **「整本书都这样」是升格进
稳定前缀，不是把有效期改成 9999** / **章级规矩不许进稳定前缀**（边界六）/
**取窄 + 数重复**（说第二次自动升到章级，而「说了几遍」是**集合判断**）/
**它真的从模型那儿进得来**（第六节）/ **作者取消得掉**（第七节）。

── 第六、七节为什么在这儿，而不是在工具那份文件里 ────────────────────────

**因为这个仓库的第四次「最后一厘米没接线」就发生在这条链上。** `rule_message()` /
`promoted()` 定义了、导出了、有测试，而 `src/` 里**一个调用方都没有**——也就是说
上面五节全绿的时候，生产里一条规矩都进不去。所以这两节量的不是某个函数，是
**整条链**：模型叫一次工具 → 引擎把章号绑上去 → 它进 canonical → 下一轮投影里看得见
→ 作者点一下就没了。链上任意一环断掉，这两节红。

── 第三节为什么单独拿出来写，而且带一个反向探针 ──────────────────────────

ADR 0023 把安全方向写死了，而**它和这个仓库其余地方的直觉是反的**：

| | 拿不准时 | 为什么 |
|---|---|---|
| `must_not_reveal` | **留着**（多禁 = fail-closed） | 说破了收不回来 |
| **作者的偏好** | **放掉**（早失效） | 留着 = 第 200 章写不出打戏，而**作者不知道为什么** |

「fail-closed 更安全」在这个仓库里几乎处处成立，所以**下一个人很可能顺手把这一条
「修」成 fail-closed**，而修完之后所有别的测试照旧全绿：多留一条偏好不会让任何断言红，
它只会在半年后的第 200 章上表现成「它就是写不出打戏」。

所以那一节做两件事：① 把两个方向摆在**同一份投影**里对照；
② 造一个 fail-closed 版本的规矩过滤（`_kept_if_fail_closed`）当探针，
断言当前实现和它给出**不同**的答案——没有这一条，第一件事可能只是碰巧成立。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from test_agent_loop import Ledger, ScriptedModel, a_context, say, wants

from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    Role,
    StopReason,
    TurnLimits,
    project,
    run_turn,
    start_conversation,
)
from novel_harness.agent.rules import (
    REPEAT_TO_WIDEN,
    RULE_MAX_UNITS,
    is_revocation,
    is_rule,
    live_rules,
    normalized_rule,
    promoted,
    revocation,
    rule_key,
    rule_message,
    surviving_rule_indices,
)
from novel_harness.agent.tools import RememberRuleArgs
from novel_harness.draft.provider import ToolCall


# ══════════════════════════════════════════════════════════════════════════
# 器材
# ══════════════════════════════════════════════════════════════════════════


def a_session(*messages: AgentMessage) -> Conversation:
    return start_conversation().model_copy(update={"messages": tuple(messages)})


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def rule(text: str, chapter: int) -> AgentMessage:
    return rule_message(text, chapter=chapter)


def _texts(projection: object) -> str:
    return str(getattr(projection, "messages"))


# ══════════════════════════════════════════════════════════════════════════
# 一、存法：它是 canonical 里的一条 SYSTEM 消息，章号只能来自引擎
# ══════════════════════════════════════════════════════════════════════════


def test_a_rule_is_one_more_message_on_the_path_that_already_exists() -> None:
    """规矩落在 `Conversation.messages` 里，**没开新的落盘面**（ADR 0022 那条不对称）。

    形状就是既有那三列（角色 / 正文 / 章号），所以会话持久化那一层一个字都不用改，
    而「秘密进了持久化的东西就收不回来」那条只需要继续罩住已经罩着的那一张表。
    """
    message = rule("别写打斗", chapter=40)
    assert message.role is Role.SYSTEM
    assert message.chapter == 40
    assert message.content == "别写打斗"
    assert is_rule(message)

    # 它进的是 `messages`，不是 `prefix`（见第二节）。
    conversation = a_session(said("写第 40 章"), message)
    assert conversation.messages[-1] is message


def test_the_chapter_on_a_rule_can_only_come_from_the_engine() -> None:
    """**约束 10 在这一层的形态**：没有坐标就不记，而不是猜一个。

    一条过不了期的规矩会跟着作者走到第 200 章，**而他不知道它在**——这正是 ADR 0023
    那张表把偏好判成「拿不准就放掉」的理由。所以宁可这一次记不上。
    """
    with pytest.raises(ValueError, match="第几章"):
        rule_message("别写打斗", chapter=None)
    with pytest.raises(ValueError):
        rule_message("别写打斗", chapter=0)


def test_a_rule_that_is_too_long_is_refused_not_quietly_trimmed() -> None:
    """上限是一道闸不是格式偏好：章级规矩**每一轮都要重发**（ADR 0023 的「代价」）。

    悄悄截断的话，作者说的和模型收到的是两句话，而没有任何东西会提这件事。
    """
    assert normalized_rule("  冷  一点 ") == "冷 一点"
    with pytest.raises(ValueError):
        normalized_rule("")
    with pytest.raises(ValueError, match=str(RULE_MAX_UNITS)):
        normalized_rule("冷" * (RULE_MAX_UNITS + 1))


def test_only_a_bare_system_message_counts_as_a_rule() -> None:
    """判据是**结构**（`messages` 里不带工具壳的 system 消息），不是一张表。"""
    assert not is_rule(said("别写打斗"))
    assert not is_rule(AgentMessage(role=Role.SYSTEM, content=""))
    assert not is_rule(
        AgentMessage(
            role=Role.SYSTEM,
            content="x",
            tool_calls=(ToolCall(id="t", name="book_index", arguments="{}"),),
        )
    )
    assert not is_rule(AgentMessage(role=Role.TOOL, content="x", tool_call_id="t"))


# ══════════════════════════════════════════════════════════════════════════
# 二、升格：「整本书都这样」进稳定前缀，**不是把有效期改成 9999**
# ══════════════════════════════════════════════════════════════════════════


def test_a_chapter_rule_cannot_be_put_into_the_stable_prefix() -> None:
    """边界六的判据只有一句：**换一章会不会变，会变就不许进。**

    这在这一层是**构造不出反例**，不是一句自觉——`Conversation` 的校验器拒收带章号的
    前缀消息。
    """
    with pytest.raises(ValueError):
        Conversation(prefix=(rule("别写打斗", chapter=40),))


def test_promoting_a_rule_puts_it_in_the_prefix_with_no_chapter_at_all() -> None:
    """升格 = 换个住处，**不是把 `valid_to` 写成一个很大的数**。

    一条 `[1, 9999)` 的规矩换一章不变，所以它本来就该住在稳定前缀里——住在那儿它才被
    缓存、才不用每轮重发，而且它在结构上**没法再带一个章号**。
    """
    conversation = promoted(start_conversation(), "整本书都克制一点")
    assert conversation.prefix[-1].content == "整本书都克制一点"
    assert conversation.prefix[-1].chapter is None
    assert conversation.messages == ()

    # 幂等：作者说了两遍「整本书都这样」是常态。
    again = promoted(conversation, "整本书都克制一点")
    assert len(again.prefix) == len(conversation.prefix)


def test_a_promoted_rule_survives_a_chapter_change_and_a_chapter_rule_does_not() -> None:
    """同一句话，两种住处，**换一章之后一个还在一个没了**——这就是升格的全部意义。"""
    chapter_scoped = a_session(said("写第 40 章"), rule("冷一点", chapter=40))
    book_wide = promoted(a_session(said("写第 40 章")), "冷一点")

    assert "冷一点" in _texts(project(chapter_scoped, 40, budget_units=100_000))
    assert "冷一点" not in _texts(project(chapter_scoped, 41, budget_units=100_000))

    assert "冷一点" in _texts(project(book_wide, 40, budget_units=100_000))
    assert "冷一点" in _texts(project(book_wide, 41, budget_units=100_000))


# ══════════════════════════════════════════════════════════════════════════
# 三、**安全方向跟 `must_not_reveal` 是反的**（这一节是本文件的重点）
# ══════════════════════════════════════════════════════════════════════════


def a_session_with_both() -> Conversation:
    """同一段历史里各放一样：一条绑第 40 章的**工具返回**，一条绑第 40 章的**规矩**。"""
    return a_session(
        said("先看看第 40 章"),
        AgentMessage(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(ToolCall(id="c1", name="scene_constraints", arguments="{}"),),
        ),
        AgentMessage(
            role=Role.TOOL,
            content="第 40 章的禁说清单：血脉秘密、身世",
            tool_call_id="c1",
            chapter=40,
        ),
        rule("别写打斗", chapter=40),
        rule("别写打斗", chapter=40),  # 说了两遍 ⇒ 章级，排除「只是因为它是尾巴」
    )


def test_the_two_directions_sit_side_by_side_in_one_projection() -> None:
    """**同一份历史、同一次投影，两条相反的规矩。**

    往前的那份禁说清单是**超集**（留着最多多禁一条，fail-closed）；
    而那条偏好换一章就该没（留着 = 一条隐形的规矩跟着作者走）。
    """
    conversation = a_session_with_both()

    at_forty = project(conversation, 40, budget_units=100_000)
    assert "禁说清单" in _texts(at_forty) and "别写打斗" in _texts(at_forty)

    later = project(conversation, 90, budget_units=100_000)
    assert "禁说清单" in _texts(later), (
        "第 40 章那份禁说清单是第 90 章那份的超集 —— 丢它就是 fail-open（ADR 0019 边界二）"
    )
    assert "别写打斗" not in _texts(later), (
        "作者在第 40 章说的偏好不该跟到第 90 章。留着的代价是他在第 200 章写不出打戏，"
        "而**他不知道为什么**（ADR 0023 决策二）。"
    )
    assert later.expired_rules == 1


def test_without_a_chapter_coordinate_the_constraints_stay_and_the_rules_go() -> None:
    """**同一个「不知道第几章」，两个相反的动作。**

    工具返回全留（不按任何一章筛 = 不替作者猜）；规矩全放（拿不准就放掉）。
    """
    blind = project(a_session_with_both(), None, budget_units=100_000)
    assert "禁说清单" in _texts(blind)
    assert "别写打斗" not in _texts(blind)
    assert blind.expired_rules == 1


def _kept_if_fail_closed(
    conversation: Conversation, chapter: int | None
) -> frozenset[int]:
    """**探针：把规矩改成 fail-closed 的那一版**（`<=`，从此不再过期）。

    这正是下一个人会顺手写出来的东西——「第 40 章定的规矩，第 90 章当然还算数」。
    它一个测试都不会弄红，所以只能在这儿把它写出来当对照。
    """
    return frozenset(
        index
        for index, message in enumerate(conversation.messages)
        if is_rule(message)
        and (chapter is None or (message.chapter or 0) <= chapter)
    )


def test_the_net_would_catch_a_fail_closed_rule_filter() -> None:
    """自守卫：当前实现和那个 fail-closed 版本必须给出**不同**的答案。

    没有这一条，上面两条「规矩没跟过来」可能只是因为这段会话里压根没有规矩。
    """
    conversation = a_session_with_both()
    for chapter in (90, None):
        ours = surviving_rule_indices(conversation.messages, chapter)
        theirs = _kept_if_fail_closed(conversation, chapter)
        assert ours == frozenset(), f"第 {chapter} 章：规矩没被放掉"
        assert theirs, f"第 {chapter} 章：探针自己就是空的 —— 那它证明不了任何事"


# ══════════════════════════════════════════════════════════════════════════
# 四、取窄 + 数重复：一次是偶然，两次是模式
# ══════════════════════════════════════════════════════════════════════════


def test_said_once_it_lives_until_the_author_speaks_again() -> None:
    """**取窄**（就这一批）。猜窄了的代价是作者顺口再说一次，猜宽了的代价是隐形。

    「这一批」的判据是**结构**：它后面还没有作者的话。
    """
    within_the_batch = a_session(said("写三版"), rule("别太煽情", chapter=40))
    assert "别太煽情" in _texts(project(within_the_batch, 40, budget_units=100_000))

    next_turn = within_the_batch.with_author("再写三版")
    projected = project(next_turn, 40, budget_units=100_000)
    assert "别太煽情" not in _texts(projected), "只说过一遍的规矩不该跨过作者的下一句话"
    assert projected.expired_rules == 1


def test_said_twice_it_covers_the_whole_chapter() -> None:
    """**说第二次自动升到章级**：一次是偶然，两次是模式（ADR 0023）。"""
    twice = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    ).with_author("再来一版")

    projected = project(twice, 40, budget_units=100_000)
    assert "别太煽情" in _texts(projected), "说了两遍还跨不过一句话，那它永远升不上去"
    assert projected.expired_rules == 0

    rules = live_rules(twice, 40)
    assert [(r.text, r.heard, r.chapter_wide) for r in rules] == [("别太煽情", 2, True)]
    assert (rules[0].valid_from, rules[0].valid_to) == (40, 41)


def test_saying_it_twice_puts_it_in_the_prompt_once() -> None:
    """同一条记了两遍，发出去只有一遍。**重复是计数用的，不是内容用的。**"""
    twice = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    )
    assert _texts(project(twice, 40, budget_units=100_000)).count("别太煽情") == 1


def test_counting_repeats_is_a_set_judgment_not_a_semantic_one() -> None:
    """**「说了几遍」是集合判断**（ADR 0005 一个字没破）。

    归一化只做三件确定性的事：NFKC、大小写、丢掉空白和一张写死的标点表。
    **意思相同但措辞不同的两句话不算同一条**——判它需要回答「这两句是不是一个意思」，
    而那是这个仓库在 v1 里不许长出来的能力。
    """
    assert rule_key("别写打斗。") == rule_key(" 别写打斗 ")
    assert rule_key("ABC") == rule_key("ａｂｃ")
    assert rule_key("别写打斗") != rule_key("不要写打戏"), (
        "这两句意思一样、字不一样 —— 认它们是同一条就等于在这一层做语义判断"
    )

    # 代价说清楚：模型换了个说法，计数从头开始 ⇒ 那条规矩少活一段章级。
    # 方向是**放掉**那一侧，和 ADR 0023 那张表一致。
    rephrased = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别写得太煽情", chapter=40),
    ).with_author("再来一版")
    assert _texts(project(rephrased, 40, budget_units=100_000)).count("煽情") == 0


def test_two_repeats_is_the_frozen_threshold() -> None:
    """阈值是一个数，不是一堆散落的判断。"""
    assert REPEAT_TO_WIDEN == 2


# ══════════════════════════════════════════════════════════════════════════
# 五、规矩不在剪枝那条链上
# ══════════════════════════════════════════════════════════════════════════


def test_a_rule_is_never_pruned_to_make_room() -> None:
    """ADR 0023 那张表：规矩「**升格成结构化的一条**」，小、跨轮有效、**丢了最气人**。

    所以它按章号过期，**不按预算剪**——三档剪枝一档都碰不到它，
    最后一档（作者说的话）本来就不存在。
    """
    conversation = a_session(
        said("写第 40 章"),
        rule("别写打斗", chapter=40),
        # **两遍要分在两个作者回合里**：升到章级数的是「作者说了几遍」，不是
        # 「引擎记了几条」（`rules._hearings`）。同一批里记两遍算一遍。
        said("再说一次，别写打斗"),
        rule("别写打斗", chapter=40),
        AgentMessage(
            role=Role.ASSISTANT,
            content="想" * 300,
            tool_calls=(ToolCall(id="t1", name="book_index", arguments="{}"),),
        ),
        AgentMessage(role=Role.TOOL, content="目" * 800, tool_call_id="t1"),
        said("接着写"),
    )
    for budget in range(6_000, 3_000, -50):
        projected = project(conversation, 40, budget_units=budget)
        assert "别写打斗" in _texts(projected), f"预算 {budget}：规矩被当成可剪的东西了"
        assert [m["content"] for m in projected.messages if m["role"] == "user"] == [
            "写第 40 章",
            "再说一次，别写打斗",
            "接着写",
        ]


# ══════════════════════════════════════════════════════════════════════════
# 六、接线：一条规矩**真的从模型那儿进得来**
# ══════════════════════════════════════════════════════════════════════════


WORKING = 40


def a_run(*script: Any, working_chapter: int | None = WORKING, **kwargs: Any) -> Any:
    """跑一轮真的 `run_turn`，返回 `(结果, 落库过的那几份历史)`。

    **走整条链，不直接调 `rule_message`**：这一节要证的正是「模型叫一次工具，那条规矩
    进得了 canonical」，而中间任意一环（工具表 / 出参类型 / loop 的收场）断掉时，
    直接调那个构造函数的测试全都照旧绿着。
    """
    persisted: list[Conversation] = []
    result = run_turn(
        kwargs.pop("conversation", None) or start_conversation().with_author("写第 40 章"),
        context=a_context(working_chapter=working_chapter),
        model=ScriptedModel(script=list(script)),
        ledger=Ledger(),
        persist=persisted.append,
        **kwargs,
    )
    return result, persisted


def remembers(text: str, **more: Any) -> Any:
    """模型这一步要求「把这条规矩记下来」。

    `ensure_ascii=False` 不是讲究：模型真的发出来的参数就是原样的中文，而这一节有一条
    断言量的正是「那句话在模型自己那半截里还在」——转义过的样本会让它假绿。
    """
    return wants(("remember_rule", json.dumps({"rule": text, **more}, ensure_ascii=False)))


def rules_in(conversation: Conversation) -> list[AgentMessage]:
    return [message for message in conversation.messages if is_rule(message)]


def _engine_said(projection: Any) -> str:
    """这一份 payload 里**引擎自己说的**那些字（system 消息 + 工具返回）拼成一条。

    **模型自己那几条不算**：它上一轮的推理和它填的工具参数是它自己的话，引擎既删不掉
    也不该删（改写模型说过的话是另一种病）。所以这套机制能给的保证收窄成一句可断言的：
    **引擎说的每一句里都没有一条过了期的规矩**（`tools.RememberRuleResult`）。
    """
    return "\n".join(
        str(message.get("content", ""))
        for message in projection.messages
        if message["role"] in ("system", "tool")
    )


def test_the_model_can_actually_put_a_rule_into_the_conversation() -> None:
    """**这一条是第六节存在的理由**：跑完一轮之后，canonical 里真的多了一条规矩。

    2026-08-12 之前这条链是断的：`rule_message()` 写好了、导出了、有测试，而 `src/`
    里一个调用方都没有——投影那一头的过期、数重复、去重全都在，**只是永远没有东西
    可过期**。
    """
    result, _ = a_run(remembers("别写打斗"), say("好，这一场我冷着写。"))

    assert result.reason is StopReason.DONE
    kept = rules_in(result.conversation)
    assert [(m.content, m.chapter, m.role) for m in kept] == [("别写打斗", 40, Role.SYSTEM)]

    # 下一轮它真的在 prompt 里，而换一章就没了（第三节那两个方向，这次走的是整条链）。
    assert "别写打斗" in _texts(project(result.conversation, 40, budget_units=100_000))
    assert surviving_rule_indices(result.conversation.messages, 90) == frozenset()
    assert "别写打斗" not in _engine_said(project(result.conversation, 90, budget_units=100_000))


def test_the_engine_never_repeats_an_expired_rule_back_at_the_model() -> None:
    """**工具返回不回吐那句话**（`tools.RememberRuleResult.rule` 是 `exclude` 的）。

    工具返回只按章号**往前**筛（`>`，那个方向是给 `must_not_reveal` 定的），所以回吐
    一次，一条第 40 章的规矩就以「引擎确认过的一条结构化规矩」的形态钉进了第 200 章的
    prompt——**正是 ADR 0023 点名的那个故障**，只是换了个地方发生。

    **剩下那半截是接受的**：模型自己那次调用的参数里有那句话，那是它自己的话
    （ADR 0019 边界二列成「接受」的残余代价）。这条断言把两者分开量。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"))
    returns = [m.content for m in result.conversation.messages if m.role is Role.TOOL]

    assert returns, "这一轮一条工具返回都没有 —— 下面几条在扫空字符串"
    assert "别写打斗" not in returns[0], "工具返回把那句规矩回吐了 —— 它不按章号过期"
    assert '"chapter":40' in returns[0], "返回连记在第几章都不说，模型就不知道它管多久"

    said_by_the_model = [
        call.arguments
        for m in result.conversation.messages
        for call in m.tool_calls
    ]
    assert any("别写打斗" in args for args in said_by_the_model), (
        "模型自己那半截也没了 —— 那说明这条测试搞错了对象（引擎不许改写模型说过的话）"
    )


def test_the_model_has_no_way_to_say_which_chapter_a_rule_belongs_to() -> None:
    """**约束 10 在这条链上的形态**：章号只能是引擎手里那个坐标。

    给模型一个 `chapter` 参数，它就能把一条规矩钉在第 9999 章上——而那正是 ADR 0023
    「不许把有效期改成 9999」要防的东西，只不过换成由模型来做。
    """
    assert "chapter" not in RememberRuleArgs.model_fields, "入参上长出了章号的格子"

    # 模型硬塞一个也进不来（`extra="forbid"`），而且这一轮**一条规矩都没记下**。
    result, _ = a_run(remembers("别写打斗", chapter=9999), say("好。"))
    assert rules_in(result.conversation) == []
    refused = [m for m in result.conversation.messages if m.role is Role.TOOL]
    assert refused and "参数不合法" in refused[0].content


def test_without_a_working_chapter_the_rule_is_refused_not_pinned_forever() -> None:
    """**没有坐标就不记**，而不是记成一条永不过期的。

    一条过不了期的规矩会跟着作者走到第 200 章，而他不知道它在——ADR 0023 那张表把
    偏好判成「拿不准就放掉」，正是为了这个形态。所以宁可这一次记不上。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"), working_chapter=None)

    assert rules_in(result.conversation) == []
    said_back = [m.content for m in result.conversation.messages if m.role is Role.TOOL]
    assert said_back and "第几章" in said_back[0], "拒绝了却没说为什么 —— 模型只会再试一次"


def test_the_rule_lands_no_matter_how_the_turn_ended() -> None:
    """十一种停法各有各的出口，**规矩不许只在其中几条上进得去**。

    这一条钉的是「落点只有一个」（`run_turn` 的 `finish()`）：逐个出口记得去贴的话
    迟早漏掉一种，而漏掉的形态是「他说了，系统答应了，下一轮它就忘了」——
    没有任何东西会报错，作者只会觉得它记性不好。
    """
    # ① 同一批里又记规矩又问作者：`ask_author` 当场收掉这一轮（ADR 0024）。
    asked, _ = a_run(
        wants(
            ("remember_rule", json.dumps({"rule": "别写打斗"}, ensure_ascii=False)),
            (
                "ask_author",
                json.dumps(
                    {"question": "这一场收在哪儿？", "options": ["雪停", "天亮"]},
                    ensure_ascii=False,
                ),
            ),
        )
    )
    assert asked.reason is StopReason.ASKED_AUTHOR
    assert [m.content for m in rules_in(asked.conversation)] == ["别写打斗"]

    # ② 它在原地打转：闸门在这一批的中间停下来，走的是 `settle()` 那条收尾路径。
    #    前三次是真的记下了（`repeat_limit`），第四次才被拦——三条记录、一条规矩。
    spinning, _ = a_run(
        wants(*[("remember_rule", json.dumps({"rule": "冷一点"}, ensure_ascii=False))] * 4),
        say("好。"),
    )
    assert spinning.reason is StopReason.REPEATED_CALL
    assert [m.content for m in rules_in(spinning.conversation)] == ["冷一点"] * 3
    live = live_rules(spinning.conversation, 40)
    assert [(r.text, r.heard) for r in live] == [("冷一点", 1)], (
        "同一批里记了三遍算作者说了三遍 —— 那它当场就升到章级，而他只开过一次口"
    )

    # ③ 步数到顶：模型永远不收手，规矩照样落得下来。
    endless, _ = a_run(remembers("别写打斗"), limits=TurnLimits(max_steps=2))
    assert endless.reason is StopReason.STEP_LIMIT
    assert [m.content for m in rules_in(endless.conversation)] == ["别写打斗"] * 2

    # ④ 作者按了停（信号在派发之前就亮着 ⇒ 这一批一个都不跑，也就没有规矩可记）。
    signal = Cancellation()
    signal.stop()
    stopped, _ = a_run(remembers("别写打斗"), say("好。"), cancel=signal)
    assert stopped.reason is StopReason.AUTHOR_STOPPED
    assert rules_in(stopped.conversation) == [], "一次没跑成的调用不该留下一条规矩"


def test_a_caller_that_only_listens_to_persist_still_gets_the_rule() -> None:
    """**落库跟着走一次。** 只认 `persist` 的调用方（`agent/loop.py::PersistFn`）不该
    看到一段少了那条规矩的历史——不然它下一轮从库里读回来的就是「他没说过」。
    """
    result, persisted = a_run(remembers("别写打斗"), say("好。"))
    assert persisted, "这一轮一次都没落库 —— 下面那条断言是空的"
    assert [m.content for m in rules_in(persisted[-1])] == ["别写打斗"]
    assert persisted[-1].model_dump_json() == result.conversation.model_dump_json()


def test_a_rule_never_lands_between_a_batch_and_its_results() -> None:
    """**wire 上那一批必须连着**（同 `PRUNED_RESULT`：一条带 `tool_calls` 的 assistant
    必须被同样多条 `tool` 消息接住）。

    规矩是一条 SYSTEM 消息，当场贴回去就夹在两条 `tool` 中间——那一份 payload 发出去
    是 400，而它发生在作者按下发送之后。所以 loop 把它挪到收场时才贴。
    """
    result, _ = a_run(
        wants(
            ("remember_rule", json.dumps({"rule": "别写打斗"}, ensure_ascii=False)),
            ("book_index", "{}"),
            ("remember_rule", json.dumps({"rule": "冷一点"}, ensure_ascii=False)),
        ),
        say("好。"),
    )
    messages = result.conversation.messages
    for index, message in enumerate(messages):
        if message.role is not Role.ASSISTANT or not message.tool_calls:
            continue
        answers = messages[index + 1 : index + 1 + len(message.tool_calls)]
        assert [m.role for m in answers] == [Role.TOOL] * len(message.tool_calls), (
            "有东西插进了一批工具返回中间 —— 那一份 payload 在 OpenAI 兼容的 wire 上是 400"
        )
    assert len(rules_in(result.conversation)) == 2, "两条规矩没都进来"


def test_the_wiring_keeps_the_fail_open_direction_and_a_fail_closed_one_would_not() -> None:
    """**第三节那个反向探针的接线版。**

    上面那一节量的是投影函数；这一条量的是**跑完一轮之后的那段真历史**——接线接错
    （比如 loop 顺手把规矩贴进 `prefix`，或者绑了一个错的章号）时，投影那一头照旧全绿，
    而这条规矩会永远不过期。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"))
    later = result.conversation

    ours = surviving_rule_indices(later.messages, 90)
    theirs = _kept_if_fail_closed(later, 90)
    assert ours == frozenset(), "第 90 章：这条链上的规矩没被放掉"
    assert theirs, "探针自己就是空的 —— 那它证明不了任何事"
    assert later.prefix == start_conversation().prefix, (
        "规矩被贴进了稳定前缀 —— 那儿的东西永不过期，也没有章号可过期（边界六）"
    )


def test_swapping_in_a_fail_closed_filter_turns_this_net_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**真的把判据改成 fail-closed，然后看这张网红不红。**

    上面那两条探针是拿一份平行实现做对照；这一条更硬一档：把 `rules` 里那个判据整个
    换掉（「第 40 章定的规矩，第 90 章当然还算数」——下一个人最可能顺手写出来的那一版），
    断言**这一节量的那件事当场不成立**。

    没有这一条，前面那些「换一章就没了」可能只是因为这段会话里压根没有活着的规矩，
    而**一个永远绿的守卫比没有守卫更糟，因为它还提供安全感**。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"))
    assert "别写打斗" not in _engine_said(
        project(result.conversation, 90, budget_units=100_000)
    )

    def fail_closed(messages: Any, chapter: int | None) -> frozenset[int]:
        """「定下之后一直算数」的那一版：只要坐标没往回走，就一条都不放。"""
        return frozenset(
            index
            for index, message in enumerate(messages)
            if is_rule(message) and chapter is not None and (message.chapter or 0) <= chapter
        )

    # `project()` 是在函数里 import 它的（断环），所以换掉模块上那个名字就够了。
    monkeypatch.setattr("novel_harness.agent.rules.surviving_rule_indices", fail_closed)
    leaked = project(result.conversation, 90, budget_units=100_000)
    assert "别写打斗" in _engine_said(leaked), (
        "换成 fail-closed 之后那条规矩照样没了 —— 那这一节量的不是这个机制"
    )


# ══════════════════════════════════════════════════════════════════════════
# 七、撤销：**追加一条指着它的记录**，不是删
# ══════════════════════════════════════════════════════════════════════════


def a_rule_said_twice() -> Conversation:
    """同一条规矩说过两遍 ⇒ 它已经是章级的（第四节）。**撤销要面对的正是这一档。**"""
    return a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    ).with_author("再来一版")


def cancelled(conversation: Conversation, seq: int) -> Conversation:
    """作者点了那条规矩上的「取消」。"""
    return conversation.extended(revocation(conversation.messages, seq))


def test_taking_a_rule_back_is_one_more_record_not_a_deletion() -> None:
    """canonical **只增不改**（3.4 的核心不变量）。删一行就把「读回来逐字节相同」拆了。"""
    before = a_rule_said_twice()
    after = cancelled(before, live_rules(before, 40)[0].seq)

    assert after.messages[: len(before.messages)] == before.messages, "历史被改过了"
    assert len(after.messages) == len(before.messages) + 1
    taken_back = after.messages[-1]
    assert is_revocation(taken_back) and not is_rule(taken_back)
    assert taken_back.content == "", (
        "撤销带了正文 —— 它和「再说一遍」就又在同一条通路上了（迁移 011 的注释写死了这条）"
    )


def test_cancelling_takes_every_copy_not_only_the_one_the_author_clicked() -> None:
    """**这一条是第七节的重点。**

    同一条规矩记过两遍时，读端只摆出最后那一条，作者点的也只能是那一条。只把那个下标
    划掉的话，前面那一遍还在——而它此刻已经是章级的（说过两遍），于是**按钮按了、
    规矩还在**，且没有任何东西会报错。
    """
    before = a_rule_said_twice()
    seq = live_rules(before, 40)[0].seq
    after = cancelled(before, seq)

    assert live_rules(after, 40) == ()
    projected = project(after, 40, budget_units=100_000)
    assert "别太煽情" not in _texts(projected)

    # **自守卫**：另一份拷贝真的还在历史里。顺手写出来的那一版撤销（「把作者点的那个
    # 下标从 live 里去掉」）会把它留下，而它此刻是章级的 —— 按钮按了，规矩还在。
    other_copies = {
        index
        for index, message in enumerate(after.messages)
        if is_rule(message) and message.chapter == 40 and index != seq
    }
    assert other_copies, "这段历史里只有一份拷贝 —— 那上面那条断言证明不了「按身份撤」"
    assert not (other_copies & surviving_rule_indices(after.messages, 40))


def test_a_cancelled_rule_is_not_reported_as_expired() -> None:
    """**作者自己取消掉的不算过期。** 他知道它没了（是他按的），而撤销记录永远留在历史里
    ——算进去的话回执上那个数会永远挂着一条他已经处理完的规矩，而他会去找它。
    """
    after = cancelled(a_rule_said_twice(), live_rules(a_rule_said_twice(), 40)[0].seq)
    assert project(after, 40, budget_units=100_000).expired_rules == 0


def test_saying_it_again_after_cancelling_starts_over() -> None:
    """**撤销只往回管。** 取消完又说一遍是新的一条，该重新开始活——不然「取消」就成了
    「以后再也不许说这句话」，而作者按那个按钮的意思从来不是这个。
    """
    after = cancelled(a_rule_said_twice(), live_rules(a_rule_said_twice(), 40)[0].seq)
    again = after.with_author("还是收着点写").extended(rule("别太煽情", chapter=40))

    live = live_rules(again, 40)
    assert [(r.text, r.heard, r.chapter_wide) for r in live] == [("别太煽情", 1, False)], (
        "被撤销过的那两遍还在计数里 —— 它会当场跳回章级，而作者刚说过不想要它"
    )
    assert "别太煽情" in _texts(project(again, 40, budget_units=100_000))


def test_a_revocation_never_reaches_the_model() -> None:
    """撤销记录的意义全在结构槽上，正文是空的。**发一条空的 system 消息只是白花钱**，
    而模型该看到的结果是「那条规矩从来没被说过」。
    """
    after = cancelled(a_rule_said_twice(), live_rules(a_rule_said_twice(), 40)[0].seq)
    projected = project(after, 40, budget_units=100_000)
    system_texts = [m["content"] for m in projected.messages if m["role"] == "system"]
    assert "" not in system_texts, "一条空的 system 消息进了 prompt"
    assert all(text.strip() for text in system_texts)


def test_cancelling_something_that_is_not_a_rule_is_refused_in_the_authors_words() -> None:
    """这条路的尽头是作者手上那个按钮，所以两句拒绝都是说给他听的中文。"""
    conversation = a_rule_said_twice()
    with pytest.raises(ValueError, match="不在这段对话里"):
        revocation(conversation.messages, len(conversation.messages))
    with pytest.raises(ValueError, match="不是你定下的规矩"):
        revocation(conversation.messages, 0)  # 那是作者说的话


def test_cancelling_twice_is_not_an_error() -> None:
    """作者双击、两个标签页各点一次都是常态。第二条杀的是同一批，是一次无害的重复。"""
    once = cancelled(a_rule_said_twice(), live_rules(a_rule_said_twice(), 40)[0].seq)
    twice = once.extended(revocation(once.messages, 1))
    assert live_rules(twice, 40) == ()


def test_a_revocation_that_points_at_nothing_is_ignored_not_a_crash() -> None:
    """**越界一律忽略。** 这份判据也会被拿去看一段**尾巴**（会话列表数还缺几个结果时
    读的就是尾巴），那时 `revokes_seq` 指的东西根本不在手上——炸掉的话，一段正常的
    会话列表会因为其中一条对话里有人按过取消而整个打不开。
    """
    tail = Conversation(
        messages=(
            AgentMessage(role=Role.SYSTEM, revokes_seq=99),
            AgentMessage(role=Role.USER, content="接着写"),
        )
    )
    assert surviving_rule_indices(tail.messages, 40) == frozenset()
    assert project(tail, 40, budget_units=100_000).messages[-1]["content"] == "接着写"


def test_the_stable_prefix_cannot_hold_a_revocation() -> None:
    """前缀里放一条撤销 = 拿一个坐标系去指另一个坐标系（前缀的下标 ≠ 历史的下标），
    **它指到的永远是别的消息**。所以这一档是构造不出来，不是记得别写。
    """
    with pytest.raises(ValueError, match="撤销"):
        Conversation(prefix=(AgentMessage(role=Role.SYSTEM, revokes_seq=0),))
