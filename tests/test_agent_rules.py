"""作者的规矩 —— [ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二，
有效期那一半已被 [ADR 0028](../docs/adr/0028-rules-expire-by-situation.md) 推翻。

⚠️ **2026-08-14：这个文件原来的主张有一半被换掉了，那一半是「默认只管当前这一章」。**
从前引擎按章号让规矩过期（`!=`，翻一页就没），理由是「拿不准就放掉」；今天它**不过期**，
作不作数由读到它的模型按情境判（规矩前面带着「作者写第几章时说的；情境不在了就不必守」）。
换掉它的判据不在这一层——章号从来不是有效期，它只是引擎手上唯一能确定知道的数，
而作者说「男主在这片沙地别杀人」时，那条规矩跟第几章一点关系都没有。

今天逐节验的是：**它是既有那条路上多一条消息** / **章号只能来自引擎** /
**「整本书都这样」是升格进稳定前缀** / **不过期、只去重、只有条数上限** /
**它不在剪枝那条链上** / **它真的从模型那儿进得来**（第六节）/
**老会话里存着的撤销记录仍然算数**（第七节）。

── 第六节为什么在这儿，而不是在工具那份文件里 ────────────────────────────

**因为这个仓库的第四次「最后一厘米没接线」就发生在这条链上。** `rule_message()` /
`promoted()` 定义了、导出了、有测试，而 `src/` 里**一个调用方都没有**——也就是说
其余几节全绿的时候，生产里一条规矩都进不去。所以那一节量的不是某个函数，是
**整条链**：模型叫一次工具 → 引擎把章号绑上去 → 它进 canonical → 下一轮投影里看得见。

── 第三节为什么单独拿出来写，而且带一个反向探针 ──────────────────────────

**规矩和工具返回今天走的是两条完全不同的路，而它们住在同一份投影里**：工具返回按章号
往前筛（`>`，超集留着，fail-closed，因为说破了收不回来），规矩**一条都不筛**。
下一个人很可能顺手把规矩也塞进那条按章号的通路（「统一一下」），而修完之后别的测试
照旧全绿——它只会表现成「作者上一章交代的事，这一章模型不知道」。
所以那一节做两件事：① 把两条路摆在**同一份投影**里对照；
② 造一个「按章号过期」的旧实现当探针（`_kept_if_expired_by_chapter`），
断言当前实现和它给出**不同**的答案。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

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
    RULE_KEEP_MAX,
    RULE_MAX_UNITS,
    RULE_PROMPT_PREFIX,
    is_revocation,
    is_rule,
    normalized_rule,
    promoted,
    prompt_text,
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


def test_a_promoted_rule_carries_no_situation_and_a_recorded_one_does() -> None:
    """同一句话，两种住处，**升格那一份不带任何情境标记**——这就是升格今天的全部意义。

    ⚠️ **这条测试 2026-08-14 换了主张。** 从前它量的是「换一章之后一个还在一个没了」，
    而规矩不再按章号过期（ADR 0028），两份**都还在**。留下来的差别是别的：

    - 记下来的那条带着「作者写第 40 章时说的；情境不在了就不必守」——它是**可以过去的**；
    - 升格进稳定前缀那条一个标记都没有——它是「整本书都这样」，没有情境可言。

    这个差别正是「升格」这个动作在今天唯一的内容。它没了的话，`promoted()` 就退化成
    一个把同一句话换个地方存的函数。
    """
    chapter_scoped = a_session(said("写第 40 章"), rule("冷一点", chapter=40))
    book_wide = promoted(a_session(said("写第 40 章")), "冷一点")

    recorded = _texts(project(chapter_scoped, 41, budget_units=100_000))
    assert RULE_PROMPT_PREFIX.format(chapter=40) + "冷一点" in recorded

    promoted_text = _texts(project(book_wide, 41, budget_units=100_000))
    assert "冷一点" in promoted_text
    assert "情境" not in promoted_text, "升格那一份带上了情境标记 —— 它没有情境可言"


# ══════════════════════════════════════════════════════════════════════════
# 三、规矩和工具返回走两条路，而它们住在同一份投影里
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
    )


def test_the_two_paths_sit_side_by_side_in_one_projection() -> None:
    """**同一份历史、同一次投影，两条不同的处置。**

    往前的那份禁说清单是**超集**（留着最多多禁一条，fail-closed，说破了收不回来）；
    而那条偏好**一路跟着走**——作者在第 40 章说「别写打斗」，第 90 章他多半还是这个意思，
    而「还算不算数」是情境的事，由读到它的模型判（ADR 0028）。
    """
    conversation = a_session_with_both()

    at_forty = project(conversation, 40, budget_units=100_000)
    assert "禁说清单" in _texts(at_forty) and "别写打斗" in _texts(at_forty)

    later = project(conversation, 90, budget_units=100_000)
    assert "禁说清单" in _texts(later), (
        "第 40 章那份禁说清单是第 90 章那份的超集 —— 丢它就是 fail-open（ADR 0019 边界二）"
    )
    assert "别写打斗" in _texts(later), (
        "作者在第 40 章交代的事，第 90 章模型就不知道了 —— 那正是 ADR 0028 换掉的东西"
    )
    assert later.expired_rules == 0


def test_it_travels_with_the_chapter_the_author_said_it_in() -> None:
    """**规矩进 prompt 时带着两样东西**：他是写第几章时说的 + 一句「情境不在了就不必守」。

    这两样合起来**就是这次改动的全部机制**。少了前一半，模型判不了情境；
    少了后一半，它会把一条早就过去的交代当成硬约束——而那正是 ADR 0023 当初按章号
    过期是想防的东西（第 200 章写不出打戏，而作者不知道为什么）。
    """
    projected = project(a_session_with_both(), 90, budget_units=100_000)
    line = next(
        str(m["content"]) for m in projected.messages if "别写打斗" in str(m["content"])
    )

    assert line == RULE_PROMPT_PREFIX.format(chapter=40) + "别写打斗"
    assert "第 40 章" in line, "作者是写第几章时说的 —— 判情境要靠它"
    assert "情境" in line, "没有这半句，模型会把它当成一条无条件的硬约束"


def test_the_model_must_say_how_long_it_lasts() -> None:
    """**`until` 是必填**（迁移 016）。少了它这次调用连校验都过不去。

    可选的话它就是「模型高兴才写」——而那张表上多半会空着一整列，
    作者看到的是一份记了一半的记录。作者要的正是这一格（「**你规定的**时效」）。
    """
    assert "until" in RememberRuleArgs.model_fields
    assert RememberRuleArgs.model_fields["until"].is_required()

    with pytest.raises(ValidationError):
        RememberRuleArgs(rule="别写打斗")  # type: ignore[call-arg]


def test_the_expiry_the_model_wrote_comes_back_to_it_next_turn() -> None:
    """模型写下的那句时效，下一轮**原样回到它面前**。

    这是这一格存在的全部理由：它判「这条还作不作数」时，读的是**自己上次的判断**，
    而不是一条不知道谁定下的期限——后者会被当成硬约束（正是 ADR 0023 按章号过期
    要防的那个形态，换了个地方发生）。
    """
    message = rule_message("男主在这片沙地不杀人", chapter=722, until="男主走出这片沙地为止")
    assert message.rule_until == "男主走出这片沙地为止"

    line = prompt_text(message)
    assert "第 722 章" in line
    assert "男主走出这片沙地为止" in line
    assert "你当时判定" in line, "读起来得像它自己的判断，不像一条外来的规定"
    assert line.endswith("男主在这片沙地不杀人")


def test_a_rule_from_before_the_column_still_renders() -> None:
    """016 之前记下的规矩没有时效。**不给它编一个**，退回那句通用的。

    补写 = 替模型说一句它没说过的话，而那句话会以「它自己的判断」的形态回到它面前。
    """
    old = rule_message("别写打斗", chapter=40)
    assert old.rule_until == ""
    assert prompt_text(old) == RULE_PROMPT_PREFIX.format(chapter=40) + "别写打斗"


def test_the_expiry_is_never_parsed_into_anything() -> None:
    """引擎**一个字都不解析**它（ADR 0005）。「男主走出这片沙地为止」归不了类。

    这一条钉的是「没有第二种读法」：存进去、渲进 prompt、摆进表格，三处都是同一串字。
    """
    said = "男主走出这片沙地为止"
    message = rule_message("别杀人", chapter=7, until=said)
    assert message.rule_until == said
    assert said in prompt_text(message)


def test_the_stored_rule_is_still_the_authors_own_sentence() -> None:
    """那句前缀**只活在投影里**。canonical 只增不改，而措辞是会变的东西——
    存进去的话，改一次措辞就等于改写了作者当年说过的话。
    """
    conversation = a_session_with_both()
    stored = [m.content for m in conversation.messages if is_rule(m)]
    assert stored == ["别写打斗"]


def _kept_if_expired_by_chapter(
    conversation: Conversation, chapter: int | None
) -> frozenset[int]:
    """**探针：2026-08-14 之前那一版**（`!=`，换一章就没了，`None` 一条不留）。

    它是下一个人最可能顺手写回去的东西（「规矩当然只管那一章」/「和工具返回统一一下」），
    而写回去之后别的测试照旧全绿——它只会表现成「作者上一章交代的事，这一章模型不知道」。
    """
    if chapter is None:
        return frozenset()
    return frozenset(
        index
        for index, message in enumerate(conversation.messages)
        if is_rule(message) and message.chapter == chapter
    )


def test_the_net_would_catch_a_chapter_scoped_rule_filter() -> None:
    """自守卫：当前实现和那个「按章号过期」的旧版必须给出**不同**的答案。

    没有这一条，上面那条「规矩跟过来了」可能只是因为这段会话里压根没有规矩。
    """
    conversation = a_session_with_both()
    for chapter in (90, None):
        ours = surviving_rule_indices(conversation.messages)
        theirs = _kept_if_expired_by_chapter(conversation, chapter)
        assert ours, f"第 {chapter} 章：规矩没留下来"
        assert theirs == frozenset(), f"第 {chapter} 章：探针自己就留下了 —— 它证明不了任何事"


# ══════════════════════════════════════════════════════════════════════════
# 四、引擎对规矩只剩三件确定的事：去重 / 撤销 / 条数上限
# ══════════════════════════════════════════════════════════════════════════


def test_saying_it_twice_puts_it_in_the_prompt_once() -> None:
    """同一串字记了两遍，发出去只有一遍。**重复是浪费，不是加强。**"""
    twice = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    )
    assert _texts(project(twice, 40, budget_units=100_000)).count(
        RULE_PROMPT_PREFIX.format(chapter=40) + "别太煽情"
    ) == 1


def test_it_no_longer_matters_how_many_times_he_said_it() -> None:
    """**说一遍和说两遍今天完全等价。**

    从前说到第二遍才从「这一批」升到「这一章」（`REPEAT_TO_WIDEN`）。那一档是
    「按章号过期」的配套：一条只活一批的规矩太短，得有个办法让它长一点。
    有效期换成模型判之后，那两个档次连同它们中间那个阈值一起没了。
    """
    once = a_session(said("写三版"), rule("别太煽情", chapter=40)).with_author("再来一版")
    twice = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    ).with_author("再来一版")

    for conversation in (once, twice):
        projected = project(conversation, 40, budget_units=100_000)
        assert "别太煽情" in _texts(projected)
        assert projected.expired_rules == 0


def test_dedup_is_a_set_judgment_not_a_semantic_one() -> None:
    """**「是不是同一条」是集合判断**（ADR 0005 一个字没破）。

    归一化只做三件确定性的事：NFKC、大小写、丢掉空白和一张写死的标点表。
    **意思相同但措辞不同的两句话不算同一条**——判它需要回答「这两句是不是一个意思」，
    而那是这个仓库在 v1 里不许长出来的能力。
    """
    assert rule_key("别写打斗。") == rule_key(" 别写打斗 ")
    assert rule_key("ABC") == rule_key("ａｂｃ")
    assert rule_key("别写打斗") != rule_key("不要写打戏"), (
        "这两句意思一样、字不一样 —— 认它们是同一条就等于在这一层做语义判断"
    )

    # 代价说清楚：模型换了个说法，那就是两条，两条都跟着走（从前的代价是「少活一段」）。
    rephrased = a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别写得太煽情", chapter=40),
    )
    assert _texts(project(rephrased, 40, budget_units=100_000)).count("煽情") == 2


def test_only_the_most_recent_rules_are_carried() -> None:
    """**条数上限是成本的闸，不是有效期。** 挤掉的是**最早说的**那几条。

    没有这道闸，一本 722 章的书攒下来的每一句随口评价都会变成常驻 prompt，
    而作者是按 token 付钱的那个人。
    """
    many = a_session(
        *[rule(f"第{i}条规矩", chapter=40) for i in range(RULE_KEEP_MAX + 3)]
    )
    live = surviving_rule_indices(many.messages)
    assert len(live) == RULE_KEEP_MAX

    texts = _texts(project(many, 40, budget_units=1_000_000))
    assert "第0条规矩" not in texts, "挤掉的该是最早那几条"
    assert f"第{RULE_KEEP_MAX + 2}条规矩" in texts, "最近那条必须在"
    # 挤掉的要报出来（§10 约束 8：静默的裁剪读起来像「全给了」）。
    assert project(many, 40, budget_units=1_000_000).expired_rules == 3


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


RULE_ARGS = {"rule": "别写打斗", "until": "这一章写完为止"}
COLD_ARGS = {"rule": "冷一点", "until": "这一场结束"}
"""模型每记一条规矩都要同时交出「管到什么时候」（迁移 016）——**`until` 是必填**，
少了它这次调用连校验都过不去。测试里那句话写什么不重要，**有没有**才是被量的东西。"""

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
    args = {"rule": text, "until": "这一章写完为止", **more}
    return wants(("remember_rule", json.dumps(args, ensure_ascii=False)))


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

    # 下一轮它真的在 prompt 里，**换一章也还在**（第三节那两条路，这次走的是整条链）。
    assert "别写打斗" in _texts(project(result.conversation, 40, budget_units=100_000))
    assert surviving_rule_indices(result.conversation.messages)
    assert "别写打斗" in _engine_said(project(result.conversation, 90, budget_units=100_000))


def test_the_engine_never_repeats_an_expired_rule_back_at_the_model() -> None:
    """**工具返回不回吐那句话**（`tools.RememberRuleResult.rule` 是 `exclude` 的）。

    回吐一次，这条规矩就在 prompt 里有了**两份**：一份是走规矩那条路的（带着「作者写
    第几章时说的；情境不在了就不必守」，模型判得了它还作不作数），另一份是一条裸的
    工具返回——看起来像「引擎确认过的一条结构化事实」，而它一个情境标记都没有。
    两份并排摆着，模型该信哪一份？**这条测试要的就是「只有一份」。**

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
            ("remember_rule", json.dumps(RULE_ARGS, ensure_ascii=False)),
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
        wants(*[("remember_rule", json.dumps(COLD_ARGS, ensure_ascii=False))] * 4),
        say("好。"),
    )
    assert spinning.reason is StopReason.REPEATED_CALL
    assert [m.content for m in rules_in(spinning.conversation)] == ["冷一点"] * 3
    live = surviving_rule_indices(spinning.conversation.messages)
    assert len(live) == 1, "同一串字记了三遍，进 prompt 的该只有一条（去重）"

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
            ("remember_rule", json.dumps(RULE_ARGS, ensure_ascii=False)),
            ("book_index", "{}"),
            ("remember_rule", json.dumps(COLD_ARGS, ensure_ascii=False)),
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


def test_the_wiring_records_the_chapter_and_keeps_it_out_of_the_prefix() -> None:
    """**第三节那两条断言的接线版。**

    上面那一节量的是投影函数；这一条量的是**跑完一轮之后的那段真历史**——接线接错
    （比如 loop 顺手把规矩贴进 `prefix`，或者章号没绑上去）时，投影那一头照旧全绿，
    而屏幕上没有任何东西会红。

    **两件事都得对**：章号绑上了（不然模型判不了情境），且它没进稳定前缀
    （那儿的东西是「整本书都这样」，一条随口的交代不该住在那儿，边界六）。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"))
    later = result.conversation

    recorded = [m for m in later.messages if is_rule(m)]
    assert [(m.content, m.chapter) for m in recorded] == [("别写打斗", 40)]
    assert surviving_rule_indices(later.messages), "这条链上的规矩没留下来"
    assert later.prefix == start_conversation().prefix, (
        "规矩被贴进了稳定前缀 —— 那儿的东西没有章号，也就没有情境可判（边界六）"
    )


def test_swapping_in_the_old_chapter_filter_turns_this_net_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**真的把判据换回「按章号过期」，然后看这张网红不红。**

    上面那两条探针是拿一份平行实现做对照；这一条更硬一档：把 `rules` 里那个判据整个
    换成 2026-08-14 之前那一版（下一个人最可能顺手写回去的那个），断言**这一节量的
    那件事当场不成立**。

    没有这一条，前面那些「换一章它还在」可能只是因为这段会话里压根没有规矩，
    而**一个永远绿的守卫比没有守卫更糟，因为它还提供安全感**。
    """
    result, _ = a_run(remembers("别写打斗"), say("好。"))
    assert "别写打斗" in _engine_said(
        project(result.conversation, 90, budget_units=100_000)
    )

    def expired_by_chapter(messages: Any) -> frozenset[int]:
        """「规矩只管作者说它那一章」的那一版。这里写死成第 40 章之外一条不留。"""
        return frozenset(
            index
            for index, message in enumerate(messages)
            if is_rule(message) and message.chapter == 90
        )

    # `project()` 是在函数里 import 它的（断环），所以换掉模块上那个名字就够了。
    monkeypatch.setattr("novel_harness.agent.rules.surviving_rule_indices", expired_by_chapter)
    lost = project(result.conversation, 90, budget_units=100_000)
    assert "别写打斗" not in _engine_said(lost), (
        "换回按章号过期之后那条规矩照样在 —— 那这一节量的不是这个机制"
    )


# ══════════════════════════════════════════════════════════════════════════
# 七、老会话里那些撤销记录**仍然算数**
#
# ⚠️ **`revocation()`（写入方）2026-08-14 删了**（[ADR 0028]）：它服务的是「这一章的
# 规矩」那块面板上的 ×，而那块面板和它背后的两条路由一起撤了——规矩不上屏。
#
# **读的那一半必须留着，而且必须有测试。** canonical 只增不改：作者当年按过的那些取消
# 还躺在真实的库里，不认它们就等于把他明确说过不要的规矩又放回 prompt。
# 所以这一节从「作者点得掉」变成「点过的仍然算数」——构造那条记录直接写结构槽，
# 因为今天没有别的路能造出它。
# ══════════════════════════════════════════════════════════════════════════


def a_rule_said_twice() -> Conversation:
    """同一条规矩说过两遍（记了两条）。**按身份撤销要面对的正是这一档。**"""
    return a_session(
        said("写三版"),
        rule("别太煽情", chapter=40),
        said("再写三版"),
        rule("别太煽情", chapter=40),
    ).with_author("再来一版")


def took_back(conversation: Conversation, seq: int) -> Conversation:
    """当年作者点了那条规矩上的「取消」，库里留下的就是这一条。

    **直接写结构槽**：`revocation()` 已经没了，而这一节要验的正是「那些留在库里的
    记录今天还认不认」。
    """
    return conversation.extended(AgentMessage(role=Role.SYSTEM, revokes_seq=seq))


def _last_rule(conversation: Conversation) -> int:
    """读端当年摆给作者的就是最后那一条（去重只留最后一次），他点的也只能是它。"""
    return max(surviving_rule_indices(conversation.messages))


def test_taking_a_rule_back_is_one_more_record_not_a_deletion() -> None:
    """canonical **只增不改**（3.4 的核心不变量）。删一行就把「读回来逐字节相同」拆了。"""
    before = a_rule_said_twice()
    after = took_back(before, _last_rule(before))

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
    划掉的话，前面那一遍还在——于是**按钮按了、规矩还在**，且没有任何东西会报错。
    """
    before = a_rule_said_twice()
    seq = _last_rule(before)
    after = took_back(before, seq)

    assert surviving_rule_indices(after.messages) == frozenset()
    assert "别太煽情" not in _texts(project(after, 40, budget_units=100_000))

    # **自守卫**：另一份拷贝真的还在历史里。顺手写出来的那一版撤销（「把作者点的那个
    # 下标从 live 里去掉」）会把它留下 —— 按钮按了，规矩还在。
    other_copies = {
        index
        for index, message in enumerate(after.messages)
        if is_rule(message) and index != seq
    }
    assert other_copies, "这段历史里只有一份拷贝 —— 那上面那条断言证明不了「按身份撤」"
    assert not (other_copies & surviving_rule_indices(after.messages))


def test_a_cancelled_rule_is_not_reported_as_expired() -> None:
    """**作者自己取消掉的不算「这一轮没带上」。** 他知道它没了（是他按的），而撤销记录
    永远留在历史里——算进去的话回执上那个数会永远挂着一条他已经处理完的规矩。
    """
    before = a_rule_said_twice()
    after = took_back(before, _last_rule(before))
    assert project(after, 40, budget_units=100_000).expired_rules == 0


def test_saying_it_again_after_cancelling_starts_over() -> None:
    """**撤销只往回管。** 取消完又说一遍是新的一条，该重新开始活——不然「取消」就成了
    「以后再也不许说这句话」，而他按那个按钮的意思从来不是这个。
    """
    before = a_rule_said_twice()
    after = took_back(before, _last_rule(before))
    again = after.with_author("还是收着点写").extended(rule("别太煽情", chapter=40))

    assert "别太煽情" in _texts(project(again, 40, budget_units=100_000))


def test_a_revocation_never_reaches_the_model() -> None:
    """撤销记录的意义全在结构槽上，正文是空的。**发一条空的 system 消息只是白花钱**，
    而模型该看到的结果是「那条规矩从来没被说过」。
    """
    before = a_rule_said_twice()
    after = took_back(before, _last_rule(before))
    projected = project(after, 40, budget_units=100_000)
    system_texts = [m["content"] for m in projected.messages if m["role"] == "system"]
    assert "" not in system_texts, "一条空的 system 消息进了 prompt"
    assert all(str(text).strip() for text in system_texts)


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
    assert surviving_rule_indices(tail.messages) == frozenset()
    assert project(tail, 40, budget_units=100_000).messages[-1]["content"] == "接着写"


def test_the_stable_prefix_cannot_hold_a_revocation() -> None:
    """前缀里放一条撤销 = 拿一个坐标系去指另一个坐标系（前缀的下标 ≠ 历史的下标），
    **它指到的永远是别的消息**。所以这一档是构造不出来，不是记得别写。
    """
    with pytest.raises(ValueError, match="撤销"):
        Conversation(prefix=(AgentMessage(role=Role.SYSTEM, revokes_seq=0),))
