"""会话表 —— **这一层唯一真正的正确性判据只有一条**：

> 从库里读回来重建出的 `Conversation`，必须和它存进去之前那个**逐字节相同**。

不相同 = resume 之后模型看到的是另一段历史，**而没有任何东西会报错**：产出的是一段
读起来完全正常、只是基于一段被悄悄改写过的历史的回答。所以这份文件的第一节是一次
往返，样本里塞满了每一种「顺手写的实现会丢掉」的东西：`tool_calls` 的三个字段、
配对用的 `tool_call_id`、绑章号与不绑章号、空内容、被剪成占位的那一档、多字节中文、
表情、以及一个 NUL。

第二节量 resume：ADR 0019 说执行态就是「一串 message + 哪几个 `tool_call` 还缺
`tool_result`」，所以**存回去再读回来之后，`pending_calls` 必须还认得出同样那几个**。

第三节量并发：作者能开多个会话窗口，也能在两个标签页里对着同一段会话各跑一轮。
没有闸的话两轮的消息会交织成一段谁也读不懂的历史，**而那同样不会报错**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_harness import project
from novel_harness.agent.loop import (
    AGENT_SYSTEM_PROMPT,
    AgentMessage,
    Conversation,
    Role,
    start_conversation,
)
from novel_harness.agent.rules import is_revocation, is_rule, live_rules
from novel_harness.agent.store import ChatConcurrency, ChatStore
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import ToolCall


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    c = connect(tmp_path / "chat.db")
    migrate(c)
    return c


@pytest.fixture
def pid(conn: Connection, tmp_path: Path) -> str:
    return project.create(conn, name="青云记", root_path=str(tmp_path / "book")).id


NASTY = [
    AgentMessage(role=Role.USER, content="第 40 章那一场，谁能说破血脉的事？"),
    AgentMessage(
        role=Role.ASSISTANT,
        content="我先查一下。",
        tool_calls=(
            ToolCall(id="call-1", name="scene_constraints", arguments='{"chapter": 40}'),
            # **参数不是合法 JSON 也要原样存回来**：流式下它是一串 delta 拼起来的，
            # 截断真的会发生，而这一层不解析（那道闸只在 `tools.dispatch`）。
            ToolCall(id="call-2", name="character_state", arguments='{"chapter": 40, "chara'),
        ),
    ),
    AgentMessage(
        role=Role.TOOL,
        content='{"chapter": 40, "must_not_reveal": [{"name": "血脉秘密"}]}',
        tool_call_id="call-1",
        chapter=40,
    ),
    AgentMessage(
        role=Role.TOOL,
        content="（这条查询结果已经从上下文里清掉了。）",
        tool_call_id="call-2",
        chapter=40,
        pruned=True,
    ),
    # 空内容 + 不绑章号：`""` 和 `None` 都是有意义的取值，不是「没填」。
    AgentMessage(role=Role.ASSISTANT, content=""),
    # 作者定下的一条规矩（ADR 0023 决策二）+ 他随后按的那个「取消」。
    # **撤销那条的正文是空的**，它的全部意义在 `revokes_seq` 上——丢了那一列，
    # 读回来它就是一条普通的空消息，而**被取消掉的规矩会活过来**。
    AgentMessage(role=Role.SYSTEM, content="别写打斗", chapter=40),
    AgentMessage(role=Role.SYSTEM, revokes_seq=5),
    AgentMessage(role=Role.USER, content="行，那就照这个写。😀 换\n行 制表\t符 空字\x00节"),
]


def test_a_conversation_comes_back_byte_for_byte(conn: Connection, pid: str) -> None:
    """**这一层的全部重量在这一条上。**

    判据是两条一起：模型相等（每个字段都对上），以及序列化之后**逐字节**相等
    （一个被顺手 `strip()` 掉的空白、一个被 `int()` 成 0 的 `None` 都躲不过第二条）。
    """
    store = ChatStore(conn)
    session = store.create(pid, title="第 40 章", write_rule="写得冷一点，少用形容词。")
    original = store.load(pid, session.id)
    assert original is not None
    wanted = original.conversation.model_copy(update={"messages": tuple(NASTY)})

    store.append(pid, session.id, base_count=0, messages=NASTY)
    got = store.load(pid, session.id)
    assert got is not None

    assert got.conversation == wanted
    assert got.conversation.model_dump_json() == wanted.model_dump_json()
    assert got.history_count == len(NASTY)


def test_a_rule_the_author_took_back_does_not_come_back_alive(
    conn: Connection, pid: str
) -> None:
    """**逐字节相等还不够，得问它一句话。**

    上一条量的是「每个字段都读回来了」；这一条量的是那几列**读回来还是同一个意思**。
    `revokes_seq` 是这里唯一一列「意思全在数字上」的：它指的是**历史的下标**
    （`Conversation.messages` 的下标），而这张表自己的 `seq` 把稳定前缀也数在内——
    读写任意一头做一次换算，那个数就指到别的消息上了，而**逐字节对拷照样绿**
    （存进去 5、读回来还是 5，只是它现在指着另一条消息）。

    症状：作者按过的那个「取消」失效，那条规矩活过来，而没有任何东西会报错。
    """
    store = ChatStore(conn)
    session = store.create(pid, write_rule="写得冷一点")  # ← 前缀两行，历史下标从这儿错开
    store.append(pid, session.id, base_count=0, messages=NASTY)
    got = store.load(pid, session.id)
    assert got is not None

    revoked = got.conversation.messages[6]
    assert is_revocation(revoked) and revoked.revokes_seq == 5
    assert is_rule(got.conversation.messages[5]), "样本里那条规矩没了 —— 下面这条是空的"
    assert live_rules(got.conversation, 40) == (), (
        "读回来之后那条被取消的规矩又活了 —— 多半是 `revokes_seq` 被当成了本表的 seq"
    )


def test_the_stable_prefix_is_stored_not_regenerated(conn: Connection, pid: str) -> None:
    """**前缀也逐行落盘。**

    照 `AGENT_SYSTEM_PROMPT` 在读回时重拼是最省事的写法，而它错的形态是：改一次那个
    常量，全部旧会话的开头被静默换掉——同一段对话，模型看到的是另一份身份说明。
    这里把库里的那一行改掉，然后断言读回来的是**库里那一份**，不是代码里那一份。
    """
    store = ChatStore(conn)
    session = store.create(pid, write_rule="冷一点")
    assert store.load(pid, session.id).conversation.prefix[0].content == AGENT_SYSTEM_PROMPT

    conn.execute(
        "UPDATE chat_message SET content = ? WHERE session_id = ? AND section = 'prefix'"
        " AND seq = 0",
        ("（这是当年那一版的说明。）", session.id),
    )
    conn.commit()
    reloaded = store.load(pid, session.id)
    assert reloaded is not None
    assert reloaded.conversation.prefix[0].content == "（这是当年那一版的说明。）"
    # 文风那一条仍然在前缀里，且仍然是第二条 —— 顺序也是历史的一部分。
    assert reloaded.conversation.prefix[1].content == "冷一点"


def test_a_prefix_that_would_pin_a_chapter_cannot_be_written_back(
    conn: Connection, pid: str
) -> None:
    """读回来的前缀照样过 `Conversation` 的校验器（边界六）。

    **这条不是重复 3.3 的测试**：那儿量的是「构造不出来」，这儿量的是「存进去的东西
    读回来也构造不出来」——一个直接写库的迁移、一次手工修数据都可能造出那种行。
    这一层的正确动作是**炸**，不是把一条钉死的过期禁令交给模型。
    """
    store = ChatStore(conn)
    session = store.create(pid)
    conn.execute(
        "UPDATE chat_message SET chapter = 40 WHERE session_id = ? AND section = 'prefix'",
        (session.id,),
    )
    conn.commit()
    with pytest.raises(ValueError, match="稳定前缀"):
        store.load(pid, session.id)


def test_resume_still_knows_which_lookups_never_came_back(conn: Connection, pid: str) -> None:
    """**执行态存不住 = resume 补跑不了**（ADR 0019「为什么不是图编排」）。

    造一个「模型要了两个工具、只回来一个」的历史——那正是进程死在模型调用和派发之间
    的形状——存回去、读回来，`pending_calls` 必须还是同样那一个。
    """
    store = ChatStore(conn)
    session = store.create(pid)
    half = [
        AgentMessage(role=Role.USER, content="查一下"),
        AgentMessage(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(
                ToolCall(id="a", name="book_index", arguments="{}"),
                ToolCall(id="b", name="chapter_text", arguments='{"chapter": 3}'),
            ),
        ),
        AgentMessage(role=Role.TOOL, content="目录…", tool_call_id="a"),
    ]
    store.append(pid, session.id, base_count=0, messages=half)

    got = store.load(pid, session.id)
    assert got is not None
    pending = got.conversation.pending_calls
    assert [c.id for c in pending] == ["b"]
    assert pending[0].name == "chapter_text"
    assert pending[0].arguments == '{"chapter": 3}'


def test_two_windows_cannot_interleave_one_conversation(conn: Connection, pid: str) -> None:
    """两个标签页各跑一轮：**后到的那一次拿到拒绝，不是一段静默损坏的历史。**"""
    store = ChatStore(conn)
    session = store.create(pid)
    store.append(pid, session.id, base_count=0, messages=[AgentMessage(role=Role.USER, content="一")])

    with pytest.raises(ChatConcurrency):
        store.append(
            pid, session.id, base_count=0, messages=[AgentMessage(role=Role.USER, content="二")]
        )

    got = store.load(pid, session.id)
    assert got is not None
    assert [m.content for m in got.conversation.messages] == ["一"]


def test_appending_keeps_the_order_across_batches(conn: Connection, pid: str) -> None:
    """一轮一批地追加，**顺序是全局的**。

    顺序不能靠时间戳：同一批里几条落在同一毫秒是常态，而顺序错了的对话在
    OpenAI 兼容的 wire 上直接是 400（带 `tool_calls` 的 assistant 必须紧跟着被
    同样多条 `tool` 消息接住）。
    """
    store = ChatStore(conn)
    session = store.create(pid)
    count = 0
    for round_index in range(5):
        batch = [
            AgentMessage(role=Role.USER, content=f"第{round_index}问"),
            AgentMessage(role=Role.ASSISTANT, content=f"第{round_index}答"),
        ]
        count = store.append(pid, session.id, base_count=count, messages=batch)
    got = store.load(pid, session.id)
    assert got is not None
    assert [m.content for m in got.conversation.messages] == [
        text for i in range(5) for text in (f"第{i}问", f"第{i}答")
    ]
    assert got.history_count == 10


def test_sessions_are_per_project_and_never_leak_across_books(
    conn: Connection, pid: str, tmp_path: Path
) -> None:
    """一个库里可以有好几本书（书架）。**另一本书的会话读不到、删不掉。**"""
    other = project.create(conn, name="别的书", root_path=str(tmp_path / "other")).id
    store = ChatStore(conn)
    mine = store.create(pid, title="我的")
    assert store.get(other, mine.id) is None
    assert store.load(other, mine.id) is None
    assert store.delete(other, mine.id) is False
    assert [s.id for s in store.list(other)] == []
    assert [s.id for s in store.list(pid)] == [mine.id]


def test_deleting_a_session_takes_its_messages_and_nothing_else(
    conn: Connection, pid: str
) -> None:
    """删一段对话 = 删这一段。**正文一个字节都不在这两张表里**（ADR 0007 / 边界三）。"""
    store = ChatStore(conn)
    kept = store.create(pid, title="留着")
    doomed = store.create(pid, title="删掉")
    store.append(pid, kept.id, base_count=0, messages=[AgentMessage(role=Role.USER, content="留")])
    store.append(pid, doomed.id, base_count=0, messages=[AgentMessage(role=Role.USER, content="删")])

    assert store.delete(pid, doomed.id) is True
    assert store.get(pid, doomed.id) is None
    assert conn.execute(
        "SELECT COUNT(*) FROM chat_message WHERE session_id = ?", (doomed.id,)
    ).fetchone()[0] == 0
    assert store.load(pid, kept.id).history_count == 1


def test_the_list_puts_the_one_just_spoken_to_on_top(conn: Connection, pid: str) -> None:
    """按「最近说过话」排，不按「什么时候开的」——作者回到三个月前那一段接着聊，
    它就该回到最上面。"""
    store = ChatStore(conn)
    first = store.create(pid, title="老的")
    second = store.create(pid, title="新的")
    assert [s.title for s in store.list(pid)] == ["新的", "老的"]

    conn.execute(
        "UPDATE chat_session SET updated_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (second.id,),
    )
    conn.commit()
    store.append(pid, first.id, base_count=0, messages=[AgentMessage(role=Role.USER, content="喂")])
    assert [s.title for s in store.list(pid)] == ["老的", "新的"]


def test_an_empty_append_is_not_a_write(conn: Connection, pid: str) -> None:
    """一轮什么都没长出来（第一步就撞了闸）时不写库，也不假装历史变长了。"""
    store = ChatStore(conn)
    session = store.create(pid)
    before = store.get(pid, session.id)
    assert store.append(pid, session.id, base_count=0, messages=[]) == 0
    assert store.get(pid, session.id) == before


def test_the_round_trip_survives_a_conversation_with_no_house_style(
    conn: Connection, pid: str
) -> None:
    """没填文风的那一档：前缀只有一条，读回来还是一条。"""
    store = ChatStore(conn)
    session = store.create(pid)
    got = store.load(pid, session.id)
    assert got is not None
    assert got.conversation == start_conversation()
    assert got.conversation == Conversation(prefix=(got.conversation.prefix[0],))
