"""对抗性验证：**作者的规矩**（[ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。

`tests/test_agent_rules.py` 是那一刀自己写的网，量的是「我实现的东西成立吗」。
**这一份反过来问：它自称的那六件事，能不能被一个顺手写出来的实现骗过去。**
所以每一节的形状都一样：**先量那件事，再拿一个探针证明这个量法有牙**——
没有第二半的话，第一半可能只是碰巧成立，而它安静地失效之后没有任何东西会红。

六件事，按「错了有多贵」排：

1. **往返逐字节一致**（第一节）。`revokes_seq` 是这张表上唯一一列「意思全在数字上」的，
   丢了它 = 作者按过的取消变成没按过，而**逐字节对拷照样绿**（存 5 读 5，只是它现在
   指着另一条消息）。所以这一节穷举消息形状，判据两条一起，外加一个**真的把那一列
   读丢**的假实现当探针。
2. 🔴 **安全方向**（第二节）。ADR 0023 把偏好过期写成 **fail-open（放掉）**，
   **和 `must_not_reveal` 正好相反**。而「fail-closed 更安全」在这个仓库里几乎处处成立
   ——下一个人最可能顺手把它「修」成 fail-closed，修完之后所有别的测试照旧全绿，
   它只会在半年后的第 200 章上表现成「它就是写不出打戏」。
3. **撤销和「说第二遍」分得开**（第三节）。两者共用同一条通路的话，作者想取消，
   系统会听成加强（迁移 011 开头写死的那件事）。
4. **数重复是集合判断**（第四节）。「别写打斗」和「不要写打戏」判成同一条要回答
   「这两句是不是一个意思」——ADR 0005 禁止 v1 长出这种能力。
5. **章级规矩没混进稳定前缀**（第五节，边界六）。判据只有一句：换一章会不会变。
6. **章号真的进不来**（第六节，约束 10）。作者填不了、模型传不了、没有坐标就不记。

第七节是这一次**真找到的两处**，两条测试都是先红后绿的。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from test_agent_loop import Ledger, ScriptedModel, a_context, say, wants
from test_no_chapter_input import model_chapter_fields

from novel_harness import project as book
from novel_harness.agent import rules as rules_mod
from novel_harness.agent import store as store_mod
from novel_harness.agent.loop import (
    AgentMessage,
    Conversation,
    Projection,
    Role,
    project,
    run_turn,
    start_conversation,
)
from novel_harness.agent.rules import (
    expired_rule_count,
    is_rule,
    live_rules,
    normalized_rule,
    revocation,
    rule_key,
    rule_message,
)
from novel_harness.agent.store import ChatStore
from novel_harness.agent.tools import RULE_ACKNOWLEDGED, RememberRuleArgs, dispatch
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import ToolCall


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    c = connect(tmp_path / "chat.db")
    migrate(c)
    return c


@pytest.fixture
def pid(conn: Connection, tmp_path: Path) -> str:
    return book.create(conn, name="青云记", root_path=str(tmp_path / "book")).id

# ══════════════════════════════════════════════════════════════════════════
# 器材
# ══════════════════════════════════════════════════════════════════════════

ROOMY = 1_000_000
"""投影预算给到满：这份文件一条都不量剪枝，剪枝那一档有它自己的文件。"""

REMEMBER = '{"rule": "别写打斗"}'
"""模型叫这条工具时打进来的那串字。**里面没有章号，也塞不进去**（第六节）。"""


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def rule(text: str, chapter: int) -> AgentMessage:
    return rule_message(text, chapter=chapter)


def a_conversation(*messages: AgentMessage, write_rule: str | None = None) -> Conversation:
    return start_conversation(write_rule).model_copy(update={"messages": tuple(messages)})


def a_turn(*script: Any, chapter: int | None = 40, conversation: Conversation | None = None):
    """真跑一轮，返回长出来的那段 canonical。**这一节量的是整条链，不是某个函数。**"""
    live = conversation or start_conversation().with_author("这一章别写打斗。")
    model = ScriptedModel(script=list(script))
    return run_turn(live, context=a_context(working_chapter=chapter), model=model, ledger=Ledger())


ENGINE_ROLES = frozenset({"system", "tool"})
"""投影里**引擎自己说的**那两种角色。

`assistant` 是模型自己的字、`user` 是作者自己的字，**两样都不是这一层担保得了的**
（改写它们是另一种病，判它们又要读懂那句话是什么意思 = 语义判断，ADR 0005 禁）。
所以这份文件对「规矩真的失效了」的判据收窄成一句可断言的话：
**引擎自己说的每一句里都没有一条过了期的规矩。**
"""


def engine_words(projection: Projection) -> str:
    """这一份投影里，引擎自己说的那几句拼起来。"""
    return json.dumps(
        [m for m in projection.messages if m["role"] in ENGINE_ROLES], ensure_ascii=False
    )


def leading_system_block(projection: Projection) -> str:
    """投影最前面那一串连着的 system 消息 —— **前缀缓存会拿它当钥匙的那一段。**

    判据不是「`Conversation.prefix` 相不相等」（那是构造期就拦住的另一件事），
    是**发出去的那份 payload 的开头**：一条章级规矩只要落在历史的最前面，这一段就变了，
    而边界六量的正是「换一章会不会变」。
    """
    out: list[dict[str, Any]] = []
    for message in projection.messages:
        if message["role"] != "system":
            break
        out.append(message)
    return json.dumps(out, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# 一、往返：加了 `revokes_seq` 之后，每一种形状都要原样回来
# ══════════════════════════════════════════════════════════════════════════

SHAPES: tuple[AgentMessage, ...] = (
    said("第 40 章那一场，别写打斗。"),
    AgentMessage(
        role=Role.ASSISTANT,
        content="",  # **只有工具调用，正文是空的**：`""` 是取值，不是「没填」
        tool_calls=(
            ToolCall(id="c-1", name="remember_rule", arguments=REMEMBER),
            # 参数里带引号和反斜杠，而且是一串没拼完的 JSON（流式下真会发生）
            ToolCall(id="c-2", name="chapter_text", arguments='{"q": "他说\\"别\\\\写\\"", "cha'),
        ),
    ),
    AgentMessage(
        role=Role.TOOL,
        content='{"chapter":40,"note":"记下了。"}',
        tool_call_id="c-1",
        chapter=40,
    ),
    # `chapter` 三态之二：最小的合法章号 + 已经被剪成占位
    AgentMessage(role=Role.TOOL, content="", tool_call_id="c-2", chapter=1, pruned=True),
    # `chapter` 三态之三：不绑章号
    AgentMessage(role=Role.ASSISTANT, content="好。"),
    rule("别写打斗，冷一点。", 40),
    # 撤销记录本身：**正文是空的**，全部意义在那个数上
    AgentMessage(role=Role.SYSTEM, revokes_seq=5),
    # `0` 和 `None` 是两件事（`0` = 撤销第 0 条，那是一条真的消息）
    AgentMessage(role=Role.SYSTEM, revokes_seq=0),
    said("行。😀 换\n行 制表\t符 空字\x00节 反斜杠\\ 引号\""),
)


def roundtrip(
    store: ChatStore, project_id: str, messages: Sequence[AgentMessage], *, write_rule: str | None
) -> Conversation:
    """存进去再读回来。**判据两条一起**：模型相等 **且** `model_dump_json()` 逐字节相等。

    第二条不是第一条的复述：一个被顺手 `strip()` 掉的空白、一个被 `int()` 成 `0` 的
    `None`，都能在第一条上蒙混过去。
    """
    session = store.create(project_id, write_rule=write_rule)
    if messages:
        store.append(project_id, session.id, base_count=0, messages=list(messages))
    got = store.load(project_id, session.id)
    assert got is not None
    wanted = got.conversation.model_copy(update={"messages": tuple(messages)})
    assert got.conversation == wanted
    assert got.conversation.model_dump_json() == wanted.model_dump_json()
    return got.conversation


def test_every_shape_a_message_can_take_comes_back_byte_for_byte(
    conn: Connection, pid: str
) -> None:
    """穷举形状，**每一种单独走一次，再全部一起走一次**。

    单独走是为了让失败可读：整串一起对拷时，报错只会说「这一大坨不一样」。
    两种前缀长度都走（有没有文风），因为**这张表的 `seq` 把前缀也数在内**——
    只有前缀长度变了，一次「把 `revokes_seq` 当成本表 seq」的换算才会露出来。
    """
    store = ChatStore(conn)
    for write_rule in (None, "写得冷一点，少用形容词。"):
        for shape in SHAPES:
            roundtrip(store, pid, [shape], write_rule=write_rule)
        roundtrip(store, pid, SHAPES, write_rule=write_rule)


def test_the_cancel_still_points_at_the_same_message_after_a_reload(
    conn: Connection, pid: str
) -> None:
    """**逐字节相等还不够，得问它一句话。**

    `revokes_seq` 的坐标是**历史的下标**，而表里那一列 `seq` 把稳定前缀也数在内。
    读写任意一头做一次换算，那个数就指着另一条消息了——而上面那条对拷**照样绿**
    （存进去 5、读回来还是 5）。症状：作者按过的取消失效，规矩活过来，没有任何东西报错。

    所以这里让前缀长度在两种取值之间动，两次都问同一句话：那条规矩还活着吗。
    """
    store = ChatStore(conn)
    history = [said("别写打斗。"), rule("别写打斗", 40), AgentMessage(role=Role.SYSTEM, revokes_seq=1)]
    for write_rule in (None, "冷一点"):
        back = roundtrip(store, pid, history, write_rule=write_rule)
        assert is_rule(back.messages[1]), "样本里那条规矩没了 —— 下面这句是空的"
        assert back.messages[2].revokes_seq == 1
        assert live_rules(back, 40) == (), (
            "读回来之后那条被取消的规矩又活了 —— 多半是 `revokes_seq` 被当成了本表的 seq"
        )


def test_a_read_that_drops_the_cancel_turns_this_net_red(
    conn: Connection, pid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**探针**：真的把那一列读丢，上面两条必须当场红。

    这不是「造一个对象再比一比」——那只证明两个对象不一样。这里换掉的是**真读端**
    （`store._to_message`），所以它证明的是**这张网**罩得住那种实现，而不是某次断言凑巧成立。
    """
    real = store_mod._to_message

    def lossy(row: Any) -> AgentMessage:
        return real(row).model_copy(update={"revokes_seq": None})

    monkeypatch.setattr(store_mod, "_to_message", lossy)
    store = ChatStore(conn)
    with pytest.raises(AssertionError):
        roundtrip(store, pid, [AgentMessage(role=Role.SYSTEM, revokes_seq=0)], write_rule=None)


# ══════════════════════════════════════════════════════════════════════════
# 二、🔴 安全方向：拿不准就**放掉**，和 `must_not_reveal` 相反
# ══════════════════════════════════════════════════════════════════════════


def assert_the_rule_is_really_gone(conversation: Conversation, chapter: int | None) -> None:
    """「切了章，那条规矩真的失效了」——**三层同时量**。

    只量其中一层都骗得过去：投影那一层过了而读端没过，界面就会摆一条其实没生效的规矩；
    读端过了而投影没过，模型手上还留着它。所以三条一起：

    1. 判据本身（`surviving_rule_indices`）；
    2. 摆给作者的那一份（`live_rules`）；
    3. **真正发出去的那份 payload 里，引擎自己说的每一句**（`engine_words`）。

    第三条是这一节唯一担保得了的形态，理由见 `ENGINE_ROLES`：模型自己那次调用的参数
    （`{"rule": "别写打斗"}`）躺在它自己的 assistant 消息里，作者那句原话躺在他自己的
    user 消息里，**两样引擎都删不掉也不该删**。

    三条**都走模块属性**（`rules_mod.x`），不走这份文件顶上那几个 `from … import`：
    下面那个探针换掉的是模块上那个名字，而 `from` 进来的引用换不掉——那样探针会绿，
    而它绿的时候这一节什么都没证明。
    """
    assert rules_mod.surviving_rule_indices(conversation.messages, chapter) == frozenset()
    assert rules_mod.live_rules(conversation, chapter) == ()
    assert "别写打斗" not in engine_words(project(conversation, chapter, budget_units=ROOMY))


def test_a_preference_from_another_chapter_is_gone_not_merely_unlisted() -> None:
    """作者在第 40 章说的「别写打斗」，到第 200 章**不该还管着他**。

    ADR 0023：留着的代价是「第 200 章写不出打戏，而**作者不知道为什么**」——
    那正是这条 ADR 把偏好判成「拿不准就放掉」的全部理由。
    """
    result = a_turn(wants(("remember_rule", REMEMBER)), say("好，记下了。"), chapter=40)
    conversation = result.conversation

    # 先证明它在第 40 章上是真的生效着的 —— 不然下面那两条「没了」不值一分钱。
    assert [entry.text for entry in live_rules(conversation, 40)] == ["别写打斗"]
    assert "别写打斗" in engine_words(project(conversation, 40, budget_units=ROOMY))

    assert_the_rule_is_really_gone(conversation, 200)
    assert_the_rule_is_really_gone(conversation, 39)
    # **没有章号坐标时也放掉**，不是「留着更安全」：那是工具返回那一档的方向。
    assert_the_rule_is_really_gone(conversation, None)


def test_the_receipt_says_a_preference_expired_instead_of_staying_silent() -> None:
    """裁了什么必须说出来（`Projection.expired_rules`）——静默失效读起来像「全给了」。"""
    conversation = a_turn(wants(("remember_rule", REMEMBER)), say("好。"), chapter=40).conversation
    assert project(conversation, 40, budget_units=ROOMY).expired_rules == 0
    assert project(conversation, 200, budget_units=ROOMY).expired_rules == 1


def _fail_closed(messages: Sequence[AgentMessage], chapter: int | None) -> frozenset[int]:
    """**下一个人最可能顺手写出来的那一版**：定下之后一直算数。

    它读起来完全正常（「作者说过的话当然还算数」），而且它和这个仓库其余地方的直觉一致
    ——`must_not_reveal` 那一档正是这么写的。区别只有一个字符：那儿是 `>`，这儿该是 `!=`。
    """
    return frozenset(
        index
        for index, message in enumerate(messages)
        if is_rule(message)
        and message.chapter is not None
        and (chapter is None or message.chapter <= chapter)
    )


def test_swapping_in_a_fail_closed_filter_turns_this_net_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**探针**：把判据换成 fail-closed，上面那一节量的事必须当场不成立。

    `project()` 是**函数内** import `surviving_rule_indices` 的（它和 `rules.py` 互相要用
    对方的东西），所以换掉模块上那个名字就够——三条判据一起换，一处漏网都没有。

    没有这一条，`assert_the_rule_is_really_gone` 可能只是因为**别的原因**碰巧成立
    （比如投影根本没把规矩发出去过），而那时它对 fail-open 这条方向一个字都没证明。
    """
    conversation = a_turn(wants(("remember_rule", REMEMBER)), say("好。"), chapter=40).conversation
    monkeypatch.setattr(rules_mod, "surviving_rule_indices", _fail_closed)
    with pytest.raises(AssertionError):
        assert_the_rule_is_really_gone(conversation, 200)

    # **换到底了没有**：上面那句只证明第一行断言有牙。这一句量的是 ADR 0023 真正在乎的
    # 那件事 —— 第 40 章定的规矩跟着模型去写第 200 章了。
    assert "别写打斗" in engine_words(project(conversation, 200, budget_units=ROOMY))

    # 读端那一层在这个探针下**炸**而不是答错，这里把它钉住（顺手量出来的一件事）：
    # `live_rules` 用查询章号去 `_hearings` 数出来的那张表里取计数，而一条属于别的章的
    # 规矩在那张表里没有条目。也就是说「留下来的那几条都属于这一章」是
    # `surviving_rule_indices` 的**前提**，而它今天只写在一句注释里、没有一道检查。
    # 换判据的人会先撞上这个 `KeyError` —— 响是响的，但它指向的地方跟原因不是一回事。
    with pytest.raises(KeyError):
        rules_mod.live_rules(conversation, 200)


# ══════════════════════════════════════════════════════════════════════════
# 三、撤销和「说第二遍」分得开
# ══════════════════════════════════════════════════════════════════════════


def a_cancelled_then_repeated_session() -> Conversation:
    """说一遍 → 撤销 → 再说一遍。**这三步共用一条通路的话，第三步会被读成第二遍。**"""
    first = [said("这一章别写打斗。"), rule("别写打斗", 40)]
    first.append(revocation(first, 1))
    again = [*first, said("算了，还是别写打斗。"), rule("别写打斗", 40)]
    return a_conversation(*again)


def test_cancelling_is_never_heard_as_saying_it_again() -> None:
    """取消完又说一遍是**新的一条**，`heard` 从 1 重新起 —— 不是第二遍、不升章级。

    照记录条数数的话它是第 2 遍，当场升到章级：作者刚明确表示不想要它，
    系统反手把它管到了整章。
    """
    conversation = a_cancelled_then_repeated_session()
    live = live_rules(conversation, 40)
    assert [entry.heard for entry in live] == [1]
    assert live[0].chapter_wide is False
    assert live[0].seq == 4, "摆给作者的该是最后那一条（去重之后只剩它）"


def test_the_cancel_does_not_eat_the_time_he_said_it_again() -> None:
    """撤销**只往回管**：它杀不掉排在它后面的那一次。

    反过来写（「取消 = 以后再也不许说这句」）的症状是：作者改主意再说一遍，
    系统装作没听见，而没有任何东西会报错。
    """
    conversation = a_cancelled_then_repeated_session()
    assert live_rules(conversation, 40) != ()
    assert "别写打斗" in engine_words(project(conversation, 40, budget_units=ROOMY))
    # 中间那一步真的取消掉过 —— 不然上面那句是废话。
    halfway = a_conversation(*conversation.messages[:3])
    assert live_rules(halfway, 40) == ()


def a_rule_said_three_times_then_cancelled() -> list[AgentMessage]:
    """说三遍（早就升到章级了）→ 作者点了读端摆出来的那一条。

    **三遍不是凑数**：两遍的话，划掉一遍之后计数就掉回 1，那条规矩会因为「批级 + 作者
    已经又开口了」自己死掉——于是一个按下标撤的错实现也能蒙对，探针量不出东西来。
    三遍时剩下的那两遍仍然够章级，错实现的症状才露出来。
    """
    history = [said("别写打斗。"), rule("别写打斗", 40)]
    for _ in range(2):
        history.append(said("我说真的，别写打斗。"))
        history.append(rule("别写打斗", 40))
    history.append(revocation(history, live_rules(a_conversation(*history), 40)[0].seq))
    return history


def test_cancelling_takes_every_copy_not_only_the_one_the_author_clicked() -> None:
    """**按身份撤，不按下标撤。**

    同一条说过好几遍时，读端只摆最后那一条，作者点的也只能是它。只划掉那个下标的话，
    前面那几遍还在，**而且它们此刻已经是章级的**——于是按钮按了、规矩还在，
    且没有任何东西会报错。
    """
    history = a_rule_said_three_times_then_cancelled()
    before = live_rules(a_conversation(*history[:-1]), 40)
    assert [entry.heard for entry in before] == [3]
    assert before[0].chapter_wide is True, "样本没升到章级 —— 下面那条量不出东西"

    assert live_rules(a_conversation(*history), 40) == ()
    assert "别写打斗" not in engine_words(
        project(a_conversation(*history), 40, budget_units=ROOMY)
    )


def _revoked_by_index_only(messages: Sequence[AgentMessage]) -> frozenset[int]:
    """**最省事的那一版**：作者点了哪个下标就划掉哪个下标。

    它读起来完全对（「他点的就是那一条」），而且**只有在同一条被记过不止一遍时才错**
    ——也就是只在真实使用里错。
    """
    return frozenset(
        message.revokes_seq
        for message in messages
        if message.revokes_seq is not None and 0 <= message.revokes_seq < len(messages)
    )


def test_a_cancel_that_only_crosses_out_one_index_turns_this_net_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**探针**：换成按下标撤，上面那条必须当场不成立 —— 而且**规矩还是章级的**。"""
    history = a_rule_said_three_times_then_cancelled()
    monkeypatch.setattr(rules_mod, "_revoked_indices", _revoked_by_index_only)
    still_there = rules_mod.live_rules(a_conversation(*history), 40)
    assert still_there != ()
    assert still_there[0].chapter_wide is True


def test_a_cancel_never_shows_up_in_the_prompt() -> None:
    """撤销记录一条都不发出去：它的意义全在结构槽上，正文是空的。

    模型该看到的结果是**那条规矩从来没被说过**，而不是一条空的 system 消息。
    """
    history = [said("别写打斗。"), rule("别写打斗", 40)]
    history.append(revocation(history, 1))
    wire = project(a_conversation(*history), 40, budget_units=ROOMY).messages
    assert [m["role"] for m in wire[len(start_conversation().prefix) :]] == ["user"]


def test_cancelling_the_same_rule_twice_is_not_an_error() -> None:
    """双击、两个标签页各点一次都是常态。第二下是一次无害的重复，不是一次报错。"""
    history = [said("别写打斗。"), rule("别写打斗", 40)]
    history.append(revocation(history, 1))
    history.append(revocation(history, 1))
    assert live_rules(a_conversation(*history), 40) == ()
    assert expired_rule_count(tuple(history), frozenset()) == 0, (
        "作者自己取消掉的不算「过期」——他知道它没了，是他按的"
    )


def test_a_cancel_that_points_at_nothing_is_refused_in_chinese() -> None:
    """两句都是说给**作者**听的中文：这条路的尽头是他手上那个按钮，不是一条诊断。"""
    history = [said("别写打斗。"), rule("别写打斗", 40)]
    with pytest.raises(ValueError, match="不在这段对话里"):
        revocation(history, 7)
    with pytest.raises(ValueError, match="不是你定下的规矩"):
        revocation(history, 0)


# ══════════════════════════════════════════════════════════════════════════
# 四、数重复是集合判断，不是语义判断（ADR 0005）
# ══════════════════════════════════════════════════════════════════════════


def two_ways_in_one_breath() -> Conversation:
    """一口气说了两种说法。**同一个作者回合**，所以「取窄」那一档不参与。"""
    return a_conversation(
        said("别写打斗。也不要写打戏。"),
        rule("别写打斗", 40),
        rule("不要写打戏", 40),
    )


def two_ways_across_two_turns() -> Conversation:
    """两个回合各说一种说法 —— **一个「懂意思」的判据会在这儿凑够两遍。**"""
    return a_conversation(
        said("别写打斗。"),
        rule("别写打斗", 40),
        said("不要写打戏。"),
        rule("不要写打戏", 40),
    )


def assert_they_are_two_rules(conversation: Conversation) -> None:
    """「别写打斗」和「不要写打戏」是**两条**，各说过一遍，一条都没升到章级。

    判它们是同一条要回答「这两句是不是一个意思」——那是语义判断，ADR 0005 在 v1 里
    禁止本仓库长出这种能力。代价是明说的：模型换个说法计数就从头开始，那条规矩少活
    一段章级——方向是「放掉」那一侧，和 ADR 0023 那张表一致。
    """
    live = rules_mod.live_rules(conversation, 40)
    assert sorted(entry.text for entry in live) == ["不要写打戏", "别写打斗"]
    assert [entry.heard for entry in live] == [1, 1]
    assert not any(entry.chapter_wide for entry in live)


def assert_nothing_got_widened(conversation: Conversation) -> None:
    """换个说法**凑不出**「说了两遍」——升到章级的判据是同一串字。"""
    live = rules_mod.live_rules(conversation, 40)
    assert live != ()
    assert [entry.heard for entry in live] == [1]
    assert not any(entry.chapter_wide for entry in live)


def test_two_ways_of_saying_the_same_thing_are_two_rules() -> None:
    assert rule_key("别写打斗") != rule_key("不要写打戏")
    assert_they_are_two_rules(two_ways_in_one_breath())
    assert_nothing_got_widened(two_ways_across_two_turns())


def test_only_what_a_set_judgment_can_see_is_folded_together() -> None:
    """归一化只做确定性的三件事：NFKC / `casefold` / 丢掉空白和一张写死的标点表。

    **它们全都不需要知道那句话是什么意思。**
    """
    assert rule_key("冷一点") == rule_key("冷一点。") == rule_key(" 冷 一 点 ")
    assert rule_key("ｃｏｌｄ") == rule_key("COLD") == rule_key("cold")
    # 一张写死的表，不是 `unicodedata.category().startswith("P")`：后者随 Unicode 版本变。
    assert rule_key("冷一点") != rule_key("冷两点")


def test_a_semantic_key_would_be_caught_by_this_net(monkeypatch: pytest.MonkeyPatch) -> None:
    """**探针**：换一个「懂意思」的判据进去，上面那一节必须当场红。

    这里那一版只是把「打斗 / 打戏」映射到同一个词——一个真会有人写的「小优化」
    （「这两句明明是一回事」）。它一旦成立，`REPEAT_TO_WIDEN` 就会被换个说法凑够，
    而作者只说过一次。
    """

    def semantic(text: str) -> str:
        return rule_key(text).replace("不要", "别").replace("打戏", "打斗")

    monkeypatch.setattr(rules_mod, "rule_key", semantic)
    with pytest.raises(AssertionError):
        assert_they_are_two_rules(two_ways_in_one_breath())
    with pytest.raises(AssertionError):
        # 更贵的那一半：两个回合各说一种说法，被凑成了「说了两遍」⇒ 整章有效，
        # 而作者每种说法只说过一次。
        assert_nothing_got_widened(two_ways_across_two_turns())


# ══════════════════════════════════════════════════════════════════════════
# 五、章级规矩没混进稳定前缀（边界六）
# ══════════════════════════════════════════════════════════════════════════


def test_the_leading_system_block_is_byte_identical_at_chapter_40_and_90() -> None:
    """同一段会话按第 40 / 90 章各投一次，**前导 system 块逐字节相同**。

    边界六的判据只有一句：换一章会不会变。这一节量的不是 `Conversation.prefix` 那个字段
    （那是构造期就拦住的另一件事），是**真正发出去那份 payload 的开头**——
    前缀缓存拿它当钥匙，而一条被缓存住的逐章规矩就是一条钉死在 context 里的过期偏好。
    """
    conversation = a_turn(
        wants(("remember_rule", REMEMBER)),
        say("好。"),
        chapter=40,
        conversation=start_conversation("写得冷一点。").with_author("这一章别写打斗。"),
    ).conversation

    at_40 = project(conversation, 40, budget_units=ROOMY)
    at_90 = project(conversation, 90, budget_units=ROOMY)
    assert leading_system_block(at_40) == leading_system_block(at_90)
    # 那条规矩确实存在，只是它排在历史里而不是排在前面 —— 不然上面那句是废话。
    assert "别写打斗" in engine_words(at_40)
    assert conversation.prefix == start_conversation("写得冷一点。").prefix


def test_a_chapter_bound_rule_cannot_be_constructed_into_the_prefix() -> None:
    """**这不是纪律，是构造不出反例**：校验器拒收带章号的前缀消息，也拒收撤销记录。

    撤销那一条的理由不同：它的坐标是**历史的下标**，放进前缀就是拿一个坐标系去指另一个。
    """
    with pytest.raises(ValueError, match="稳定前缀"):
        Conversation(prefix=(rule("别写打斗", 40),))
    with pytest.raises(ValueError, match="稳定前缀"):
        Conversation(prefix=(AgentMessage(role=Role.SYSTEM, revokes_seq=0),))


def test_the_leading_block_really_does_move_when_a_rule_sits_in_front() -> None:
    """**探针**：把规矩挪到历史最前面，前导块当场就变了。

    也就是说上面那条断言**不是恒真的**，它量的是一件真会变的事。今天规矩到不了那个位置
    （`run_turn` 要求作者先开口，规矩在 `finish()` 落到队尾），**而这一点没有一条校验器
    钉着**——它是接线的结果，不是类型的结果。
    """
    sneaked = a_conversation(rule("别写打斗", 40))
    at_40 = project(sneaked, 40, budget_units=ROOMY)
    at_90 = project(sneaked, 90, budget_units=ROOMY)
    assert leading_system_block(at_40) != leading_system_block(at_90)


# ══════════════════════════════════════════════════════════════════════════
# 六、章号真的进不来（约束 10）
# ══════════════════════════════════════════════════════════════════════════


def test_the_model_has_no_grammar_for_saying_which_chapter_a_rule_belongs_to() -> None:
    """入参上没有那个格子，**发给模型的 schema 里也没有**。

    判据借的是 `tests/test_no_chapter_input.py` 那把尺（`model_chapter_fields`），
    不另写第二份——那份守卫是约束 10 在这个仓库里唯一的自动化形态。
    """
    assert model_chapter_fields(RememberRuleArgs) == []
    assert "chapter" not in json.dumps(RememberRuleArgs.model_json_schema(), ensure_ascii=False)


def test_a_model_that_tries_to_pin_a_chapter_is_refused_and_nothing_is_remembered() -> None:
    """模型硬塞一个章号 ⇒ `extra="forbid"` 当场拒，**那一轮一条规矩都没记下**。

    给它这个旋钮，它就能把一条随口的偏好钉在第 9999 章上——正是「不许把有效期改成
    9999」要防的东西。
    """
    outcome = dispatch(
        ToolCall(id="x", name="remember_rule", arguments='{"rule": "别写打斗", "chapter": 9999}'),
        a_context(working_chapter=40),
    )
    assert outcome.ok is False
    assert outcome.remembered is None
    assert "9999" not in outcome.content, "拒绝理由里不许回显入参"


def test_without_a_working_chapter_the_rule_is_refused_not_pinned_forever() -> None:
    """没有坐标就**不记**，不是退而求其次记一条永不过期的。

    一条过不了期的规矩会跟着作者走到第 200 章，而他不知道它在——ADR 0023 那张表把
    偏好判成「拿不准就放掉」，正是为了这个形态。
    """
    result = a_turn(wants(("remember_rule", REMEMBER)), say("好。"), chapter=None)
    assert not any(is_rule(message) for message in result.conversation.messages)
    refusal = next(m for m in result.conversation.messages if m.role is Role.TOOL)
    assert "第几章" in refusal.content
    assert RULE_ACKNOWLEDGED not in refusal.content


def test_the_chapter_on_the_rule_is_the_one_the_engine_held_not_the_one_the_model_asked_for(
) -> None:
    """同一轮里模型为第 90 章查了东西，规矩仍然绑在**引擎手里那个坐标**上。

    两者不一致是**设计允许**的常态（`working_chapter` 只标不挡，作者会从第 90 章回头改
    第 40 章）。所以「章号从哪儿来」这条链上只许有一个源头，不是「模型这一轮在谈哪一章」。
    """
    result = a_turn(
        wants(("chapter_text", '{"chapter": 90}'), ("remember_rule", REMEMBER)),
        say("好。"),
        chapter=40,
    )
    landed = [message for message in result.conversation.messages if is_rule(message)]
    assert [message.chapter for message in landed] == [40]
    # 模型确实点着第 90 章 —— 不然上面那句不成立。
    assert 90 in {m.chapter for m in result.conversation.messages if m.role is Role.TOOL}


# ══════════════════════════════════════════════════════════════════════════
# 七、这一次找到的：一条**归一化之后什么都不剩**的规矩
# ══════════════════════════════════════════════════════════════════════════


def test_a_rule_made_only_of_punctuation_is_refused_instead_of_acknowledged() -> None:
    """「。。。」进得来，而它**永远不可能生效** —— 那是一句答应了又忘掉的话。

    `normalized_rule` 只拒空白和超长，而重复计数、留存判据、身份比对全都走 `rule_key`
    （归一化 + 丢标点）。一串纯标点在 `rule_key` 下是空的，于是它：
    记得下（工具回一句「记下了」）、**一次都活不了**（`surviving_rule_indices` 直接跳过）。

    `finish()` 自己把这种形态点了名：「他说了，系统答应了，下一轮它就忘了」。
    """
    for junk in ("。。。", "……", "...", "???", "——"):
        assert rule_key(junk) == "", f"样本选错了：{junk!r} 归一化之后还有字"
        with pytest.raises(ValueError, match="一个字都没有"):
            rule_message(junk, chapter=40)

    outcome = dispatch(
        ToolCall(id="x", name="remember_rule", arguments='{"rule": "……"}'),
        a_context(working_chapter=40),
    )
    assert outcome.ok is False
    assert outcome.remembered is None
    assert RULE_ACKNOWLEDGED not in outcome.content


def test_a_rule_the_engine_can_never_honour_does_not_haunt_the_receipt() -> None:
    """老会话里已经躺着这么一条时（这条修复之前记下的），**回执不许永远挂着它**。

    `Projection.expired_rules` 的口径是「有几条偏好这一轮**不再生效**了」。一条从来没
    生效过、也永远不会生效的规矩每一轮都被算进去，作者看到的就是一句永远擦不掉的
    「有一条失效了」，而他会去找一条不存在的规矩——`expired_rule_count` 的 docstring
    自己把这个形态写成了它要防的东西（那儿说的是「说了两遍」那一档）。
    """
    haunting = a_conversation(said("嗯。"), AgentMessage(role=Role.SYSTEM, content="。。。", chapter=40))
    assert live_rules(haunting, 40) == ()
    assert project(haunting, 40, budget_units=ROOMY).expired_rules == 0
    assert project(haunting, 200, budget_units=ROOMY).expired_rules == 0


def test_a_rule_with_real_words_is_still_taken() -> None:
    """**假红守卫**：上面那条闸不许咬到正常的规矩——被误伤过的守卫会被人关掉。"""
    assert normalized_rule("别写打斗。") == "别写打斗。"
    assert normalized_rule("  冷 一点  ") == "冷 一点"
    assert normalized_rule("keep it cold") == "keep it cold"
    assert normalized_rule("第 3 幕慢一点") == "第 3 幕慢一点"
    assert rule_message("😀 少用形容词", chapter=1).content == "😀 少用形容词"


def test_the_write_rule_reaches_the_drafting_call_not_just_the_conversation() -> None:
    """**这一条钉的是 2026-08-13 在作者真书上实测出来的那个断口。**

    作者定的那条一直挂着的要求进了**对话**前缀（`start_conversation`），
    可**起草是另一次调用** —— `draft_chapter` 派出去的 prompt 由 `draft/assemble.py`
    从零拼，和对话一个字都不共享。于是：**助手听见了，写手没听见**，而且不报错。

    实测（722 章的真书，要求「每段以叠词开头」）：

        接线前   5 稿   叠词开头 0/6 段 × 5     ← 一段都没照做
        接线后   7 稿   6/6 · 6/6 · 4/6 · 4/6 …

    判据分两半，缺一半这条就是空转：
    ① `write_rule_of` 从会话前缀里挑得出那条（**跳过引擎自己的系统提示词**）；
    ② 挑出来的那个值真的进了 `ChapterDraftRequest`。
    """
    from novel_harness.agent.loop import start_conversation, write_rule_of

    rule = "每一段都以叠词开头。"
    conversation = start_conversation(rule)
    assert write_rule_of(conversation) == rule

    # **不许把引擎的系统提示词当成作者的要求送出去** —— 那是这个判据唯一会错的方向。
    assert write_rule_of(start_conversation()) == ""
    assert write_rule_of(start_conversation("   ")) == ""

    # 前缀第一条永远是引擎自己的，作者那条排第二 —— 判据依赖这个顺序。
    assert len(conversation.prefix) == 2
    assert conversation.prefix[1].content == rule
