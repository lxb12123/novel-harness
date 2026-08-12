"""作者的规矩 —— [ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二。

四条硬的，逐节验：**默认只管当前这一章**（切章自动失效）/ **「整本书都这样」是升格进
稳定前缀，不是把有效期改成 9999** / **章级规矩不许进稳定前缀**（边界六）/
**取窄 + 数重复**（说第二次自动升到章级，而「说了几遍」是**集合判断**）。

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

import pytest

from novel_harness.agent.loop import (
    AgentMessage,
    Conversation,
    Role,
    project,
    start_conversation,
)
from novel_harness.agent.rules import (
    REPEAT_TO_WIDEN,
    RULE_MAX_UNITS,
    is_rule,
    live_rules,
    normalized_rule,
    promoted,
    rule_key,
    rule_message,
    surviving_rule_indices,
)
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
