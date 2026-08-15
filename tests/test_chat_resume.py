"""**resume 真的接得上吗** —— 对 ADR 0019 那条「不上 LangGraph」论证的对抗性验证。

那条论证只有一句：「线性 loop 的执行态足够简单，resume = 看尾巴、补跑缺的、继续」。
**它只有在 resume 真的对的时候才成立**，而 resume 错了的症状是**静默的**：模型看到的
是另一段历史，产出的正文读起来完全正常。所以这份文件不测「能存能读」，它测五件事：

1. **往返一致** —— 穷举消息形状，判据是模型相等 **且** 逐字节相等。配一条自守卫探针：
   造一个会丢字段的假序列化，断言这一节的判据**当场红**。
2. **崩在批中间** —— 一批三个工具、跑完第一个进程死掉。重开之后 `pending_calls`
   必须是剩下那两个，补跑的必须**只是那两个**，补跑完 wire 上 `asked == answered`。
3. **崩了 + 作者又说了一句** —— 那个 `tool_call` 再也不会被补跑（`pending_calls`
   扫到作者发言就停），所以它必须在**投影里**就地配壳，否则发出去是 400。
4. **多会话** —— 两个窗口、同一本书、同一章。一个会话的 `pending` 不许被另一个补跑；
   会话删掉之后它的账**必须还在**（钱已经付了）。
5. **重放的代价** —— ADR 说「T1–T5 只读或纯函数，重放免费」。这一节去核实那句话对
   **今天的工具表**还成不成立，并把「不成立的那一个」钉成断言而不是一句知道。

── 第 2 节找到的那个洞（已修）────────────────────────────────────────────

`api/chat.py` 原本只在**两个**时刻写库：作者说完那一句、以及 `run_turn` 返回之后。
于是进程死在一轮中间时**这一轮一条都没进库**——尾巴上根本没有那条带 `tool_calls` 的
assistant，`Conversation.pending_calls` 恒为空，`resume` 无事可补。ADR 0019 的第三问
（「中途失败必须继续 —— 必须，维护者当天定的」）在产品路径上够不到，而那几次模型调用
的钱**已经记在账上了**（`ledger` 当场 commit）。修法是 `run_turn` 收一个 `persist`
回调，每长出一条落一次；连带补上「补跑也要过批宽度闸」——否则一次崩溃就是绕过成本闸的办法。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.agent.store as store_mod
import novel_harness.agent.tools as tools_mod
import novel_harness.api.chat as chat_mod
from novel_harness import project
from novel_harness.agent.loop import (
    LOST_RESULT,
    UNRUN_CALL,
    AgentMessage,
    Conversation,
    Role,
    StopReason,
    TurnLimits,
    run_turn,
)
from novel_harness.agent.candidates import DraftCandidate
from novel_harness.agent.ports import DraftAsk, DraftProduct, ModelCallReceipt, ToolContext
from novel_harness.agent.store import ChatStore
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.draft.rolling_summary import SummaryStore
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import chapter_path


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    c = connect(tmp_path / "chat.db")
    migrate(c)
    return c


@pytest.fixture
def pid(conn: Connection, tmp_path: Path) -> str:
    return project.create(conn, name="青云记", root_path=str(tmp_path / "book")).id


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 BYOK 指到一个**登记过**的端点。设置文件落在 tmp（绝不碰真实用户目录）。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


class ProcessDied(RuntimeError):
    """进程在这儿死掉了。**它不是任何一层接得住的异常**——`dispatch` 只把四种工具失败
    做成 `ok=False` 的正常返回，别的一律穿过去，这正是模拟「断电」要的形状。"""


def says(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="deepseek-v4-flash", finish_reason="stop")


def wants(*calls: tuple[str, str], text: str = "") -> CompletionResult:
    return CompletionResult(
        text=text,
        model="deepseek-v4-flash",
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
    )


class Scripted:
    """按剧本一句一句回答，**剧本用完重复最后一条**（同 `tests/test_chat_api.py`）。

    `sent` 留着每一次真的发出去的那份 wire —— 第 2、3 节的判据全在它上面。
    """

    def __init__(self, *script: CompletionResult) -> None:
        self.script = list(script)
        self.sent: list[list[dict[str, Any]]] = []

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.sent.append([dict(m) for m in messages])
        return self.script[min(len(self.sent) - 1, len(self.script) - 1)]


def use(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: model)


def open_chat(client: TestClient, pid_: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid_}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def history(db: str, chat_id: str) -> list[tuple[str, str, str]]:
    """**从一条新连接读库**：这一节量的是「进程重开之后库里有什么」，
    而不是「刚才那个请求手上那份内存对象长什么样」。"""
    c = connect(db)
    try:
        rows = c.execute(
            "SELECT role, content, tool_calls_json FROM chat_message"
            " WHERE session_id = ? AND section = 'history' ORDER BY seq",
            (chat_id,),
        ).fetchall()
        return [(str(r["role"]), str(r["content"]), str(r["tool_calls_json"])) for r in rows]
    finally:
        c.close()


def dangling(wire: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """这份 wire 上**谁问了没人答、谁答了没人问**。

    两边都要量：少一条 `tool` 消息是 400，多一条没人认领的也是 400，
    而两种都会在作者按下发送之后才发生（"联系不上写作模型"，一个指向别处的错误说法）。
    """
    asked = [call["id"] for m in wire for call in m.get("tool_calls", ())]
    answered = [m["tool_call_id"] for m in wire if m["role"] == "tool"]
    return (
        [i for i in asked if i not in set(answered)],
        [i for i in answered if i not in set(asked)],
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、往返一致：**穷举形状**，判据是逐字节
# ══════════════════════════════════════════════════════════════════════════


EXHAUSTIVE: tuple[AgentMessage, ...] = (
    AgentMessage(role=Role.USER, content="第 40 章那一场，谁能说破血脉的事？"),
    # 只有 `tool_calls`、没有正文：模型直接动手不废话是常态。
    AgentMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=(ToolCall(id="c-empty", name="book_index", arguments="{}"),),
    ),
    AgentMessage(role=Role.TOOL, content="{}", tool_call_id="c-empty"),
    # **一轮多个 `tool_call`**，且参数里塞满了「顺手写的序列化会改掉」的东西：
    # 引号、反斜杠、真换行、制表符、已经转义过一次的 \\n、以及一段截断的 JSON。
    AgentMessage(
        role=Role.ASSISTANT,
        content="我一次查几样。",
        tool_calls=(
            ToolCall(
                id="c-quote",
                name="character_state",
                arguments=json.dumps({"chapter": 40, "character": '他说“别去”\\北荒'}),
            ),
            ToolCall(
                id="c-raw",
                name="character_state",
                arguments='{"chapter": 40, "character": "行\n列\t尾"}',
            ),
            ToolCall(id="c-esc", name="book_index", arguments='{"note": "a\\\\nb\\"c"}'),
            # 流式下 `arguments` 是一串 delta 拼起来的，**截断真的会发生**。
            ToolCall(id="c-cut", name="scene_constraints", arguments='{"chapter": 4'),
            # 端点一个参数都不给的那一档（Ollama / llama.cpp 常见）。
            ToolCall(id="c-none", name="book_index", arguments=""),
        ),
    ),
    # `chapter` 三态：不绑 / 绑第 1 章 / 绑一个很后面的章。**`None` 不是 0。**
    AgentMessage(role=Role.TOOL, content="不绑章号的返回", tool_call_id="c-quote"),
    AgentMessage(role=Role.TOOL, content="第一章", tool_call_id="c-raw", chapter=1),
    AgentMessage(role=Role.TOOL, content="很后面", tool_call_id="c-esc", chapter=999_999),
    # 被剪成占位的那一档：读回来当成真返回 = 模型以为工具真回了这么一句。
    AgentMessage(
        role=Role.TOOL, content="（已清掉）", tool_call_id="c-cut", chapter=40, pruned=True
    ),
    # 空内容 / 只有空白的内容：`strip()` 一下就没了，而它俩不是同一件事。
    AgentMessage(role=Role.TOOL, content="", tool_call_id="c-none"),
    AgentMessage(role=Role.ASSISTANT, content="   \n\t "),
    # 内容本身就是一段合法 JSON：拿它去 `json.loads` 的实现在这儿现形。
    AgentMessage(role=Role.TOOL, content='{"must_not_reveal": []}', tool_call_id="c-json"),
    # 多字节、emoji、组合字符、零宽、NUL、CRLF、首尾空白。
    AgentMessage(
        role=Role.USER,
        content="  行了😀👨‍👩‍👧 组合é 零宽​ 空字\x00节 回车\r\n 结尾空白  ",
    ),
    # 超长内容：`chapter_text` 按定义就要把一整章交给模型看（那是 resume 的代价，
    # 也是它的前提），所以这一档不是极端值，是日常值。
    AgentMessage(role=Role.TOOL, content="正文" * 60_000, tool_call_id="c-long", chapter=7),
    AgentMessage(role=Role.ASSISTANT, content="写完了。"),
)


def _round_trip(store: ChatStore, pid_: str, session_id: str) -> Conversation:
    store.append(pid_, session_id, base_count=0, messages=EXHAUSTIVE)
    got = store.load(pid_, session_id)
    assert got is not None
    return got.conversation


def _assert_identical(got: Conversation, wanted: Conversation) -> None:
    """**两条一起断言。** 第一条抓字段错位，第二条抓被顺手 `strip()` 掉的空白、
    被 `int()` 成 0 的 `None`、以及任何一个在读回时被「重新生成」出来的值。"""
    assert got == wanted
    assert got.model_dump_json() == wanted.model_dump_json()


def test_every_message_shape_comes_back_byte_for_byte(conn: Connection, pid: str) -> None:
    """**这一层唯一真正的正确性判据。** 不相同 = resume 之后模型看到的是另一段历史，
    而没有任何东西会报错。"""
    store = ChatStore(conn)
    session = store.create(pid, title="第 40 章", write_rule="写得冷一点。")
    before = store.load(pid, session.id)
    assert before is not None
    wanted = before.conversation.model_copy(update={"messages": EXHAUSTIVE})

    _assert_identical(_round_trip(store, pid, session.id), wanted)


def test_the_round_trip_check_catches_a_serializer_that_drops_a_field(
    conn: Connection, pid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫。** 上面那条判据自己也可能是假的——一份只比对「能读回来几条」的断言
    看起来一样绿。这里把 `chapter` 从读回路径上敲掉（投影按它筛，丢了它就是边界五失效），
    断言上面那条判据**当场红**。

    敲的是读回而不是写入，因为写入侧有 `NOT NULL` / `CHECK` 兜着，而读回侧一个字都没有。
    """
    real = store_mod._to_message
    monkeypatch.setattr(
        store_mod, "_to_message", lambda row: real(row).model_copy(update={"chapter": None})
    )
    store = ChatStore(conn)
    session = store.create(pid)
    before = store.load(pid, session.id)
    assert before is not None
    wanted = before.conversation.model_copy(update={"messages": EXHAUSTIVE})

    with pytest.raises(AssertionError):
        _assert_identical(_round_trip(store, pid, session.id), wanted)


def test_the_round_trip_check_catches_a_serializer_that_strips_whitespace(
    conn: Connection, pid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二支探针：只有**逐字节**那一条抓得住它。

    `content.strip()` 是最典型的「顺手」——模型相等那一条对首尾空白照样会红，
    所以这里敲的是一条只有空白的消息之外的东西：把内容原样保留、只去掉首尾。
    """
    real = store_mod._to_message
    monkeypatch.setattr(
        store_mod,
        "_to_message",
        lambda row: (lambda m: m.model_copy(update={"content": m.content.strip()}))(real(row)),
    )
    store = ChatStore(conn)
    session = store.create(pid)
    before = store.load(pid, session.id)
    assert before is not None
    wanted = before.conversation.model_copy(update={"messages": EXHAUSTIVE})

    with pytest.raises(AssertionError):
        _assert_identical(_round_trip(store, pid, session.id), wanted)


def test_a_chapter_that_is_not_a_chapter_is_refused_by_the_table_not_stored_as_zero(
    conn: Connection, pid: str
) -> None:
    """`chapter` 的三个取值里 `None` 和「第 0 章」是两件事，而库替这条纪律站岗。

    投影按 `chapter > 视角` 筛（边界五）：一个静默变成 0 的 `None` 会让那条工具返回
    **永远留在每一次投影里**，包括第 1 章的——那正是 fail-open 的那一侧。
    """
    import sqlite3

    store = ChatStore(conn)
    session = store.create(pid)
    for bad in (0, -3):
        with pytest.raises(sqlite3.IntegrityError, match="chapter"):
            store.append(
                pid,
                session.id,
                base_count=0,
                messages=[AgentMessage(role=Role.TOOL, content="x", tool_call_id="a", chapter=bad)],
            )


# ══════════════════════════════════════════════════════════════════════════
# 二、崩在批中间：**这一节找到了那个洞**
# ══════════════════════════════════════════════════════════════════════════


def test_a_turn_that_dies_mid_batch_leaves_exactly_the_calls_that_never_ran(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一批三个工具，跑完第一个进程死掉。**重开之后库里必须看得见那条 assistant。**

    这是 ADR 0019 全部执行态的落点：尾巴上那条带 `tool_calls` 的 assistant + 已经回来的
    那几条 `tool`。它不在库里的话 `pending_calls` 恒为空，「看尾巴、补跑缺的」就是一句
    空话——**而那几次模型调用的钱已经在账上了**。

    修之前这条是红的：库里只有作者那一句。
    """
    pid_ = book["pid"]
    real_dispatch = tools_mod.dispatch
    done = 0

    # 第三个参数是这一轮的短记性（`tools.TurnMemo`）。**替身要原样收下并转交**：
    # 吞掉它的话「这一轮撞空过几个名字」在补跑那条路上就丢了。
    def dying_dispatch(call: Any, context: Any, memo: Any = None, stored: Any = None) -> Any:
        nonlocal done
        if done >= 1:
            raise ProcessDied("断电")
        done += 1
        return real_dispatch(call, context, memo, stored)

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", dying_dispatch)
    use(
        monkeypatch,
        Scripted(
            wants(
                ("book_index", "{}"),
                ("scene_constraints", json.dumps({"chapter": 2})),
                ("character_state", json.dumps({"chapter": 2, "character": "萧决"})),
            )
        ),
    )
    chat_id = open_chat(client, pid_)
    with pytest.raises(ProcessDied):
        client.post(
            f"/api/projects/{pid_}/chats/{chat_id}/turn",
            json={"chapter": 2, "said": "第 2 章有谁在？"},
        )

    rows = history(book["db"], chat_id)
    assert [role for role, _, _ in rows] == ["user", "assistant", "tool"], (
        "崩在批中间之后库里只剩作者那一句 —— 尾巴不在库里，resume 就无从谈起"
    )

    chat_mod.LIVE.clear()  # 进程重开
    detail = client.get(f"/api/projects/{pid_}/chats/{chat_id}").json()
    assert detail["session"]["pending_lookups"] == 2, "缺的应该正好是没跑完的那两个"


def test_only_the_calls_that_never_ran_are_replayed(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**补跑的是那两个，不是整批重来。** 整批重来在只读工具上只是浪费，
    在会花钱的那一个（第五节）上是真掏钱。

    顺带量最后一件事：补跑完发出去的那份 wire 上 `asked == answered`——少一条是 400，
    而作者看到的说法会是「联系不上写作模型」（一个指向别处的错误说法）。
    """
    pid_ = book["pid"]
    real_dispatch = tools_mod.dispatch
    ran: list[str] = []

    def dying_dispatch(call: Any, context: Any, memo: Any = None, stored: Any = None) -> Any:
        if len(ran) >= 1:
            raise ProcessDied("断电")
        ran.append(call.name)
        return real_dispatch(call, context, memo, stored)

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", dying_dispatch)
    use(
        monkeypatch,
        Scripted(
            wants(
                ("book_index", "{}"),
                ("scene_constraints", json.dumps({"chapter": 2})),
                ("character_state", json.dumps({"chapter": 2, "character": "萧决"})),
            )
        ),
    )
    chat_id = open_chat(client, pid_)
    with pytest.raises(ProcessDied):
        client.post(
            f"/api/projects/{pid_}/chats/{chat_id}/turn",
            json={"chapter": 2, "said": "第 2 章有谁在？"},
        )
    assert ran == ["book_index"]

    # 进程重开，作者一个字都不重说（`said` 留空 = 接着上次往下跑）。
    chat_mod.LIVE.clear()
    monkeypatch.setattr("novel_harness.agent.tools.dispatch", real_dispatch)
    resumed = Scripted(says("查完了，第 2 章那条先别说破。"))
    use(monkeypatch, resumed)
    turn = client.post(f"/api/projects/{pid_}/chats/{chat_id}/turn", json={"chapter": 2})
    assert turn.status_code == 200, turn.text
    body = turn.json()
    assert body["lookups"] == 2, "补跑的条数不对：整批重来是 3，一个不补是 0"
    assert body["reason"] == StopReason.DONE.value

    unanswered, orphan = dangling(resumed.sent[0])
    assert (unanswered, orphan) == ([], []), "补跑完的 wire 配不齐 —— 发出去就是 400"
    assert client.get(f"/api/projects/{pid_}/chats/{chat_id}").json()["session"][
        "pending_lookups"
    ] == 0


def test_the_turn_that_dies_is_the_one_the_shell_actually_asks_to_keep(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫：`api/chat.py` 真的把落库接口传下去了。**

    把 `persist=` 去掉，上面两条会静默退回「一轮整批落库」——库里少的那几行不会报错，
    只会让 resume 什么都补不上。这里直接对着 `run_turn` 的实参断言。
    """
    pid_ = book["pid"]
    seen: list[Any] = []
    real_run_turn = chat_mod.run_turn

    def spy(conversation: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("persist"))
        return real_run_turn(conversation, **kwargs)

    monkeypatch.setattr(chat_mod, "run_turn", spy)
    use(monkeypatch, Scripted(says("好")))
    chat_id = open_chat(client, pid_)
    client.post(f"/api/projects/{pid_}/chats/{chat_id}/turn", json={"chapter": 1, "said": "喂"})
    assert seen and seen[0] is not None, (
        "`run_turn` 没收到落库回调 —— 这一轮的执行态只活在进程内，崩了就没了"
    )


def test_a_crash_cannot_be_used_to_walk_past_the_batch_gate(
    conn: Connection, pid: str, tmp_path: Path
) -> None:
    """**批宽度闸原本只拦「还没派发的那一批」。**

    落库之后「模型一口气要了 20 个工具」这件事本身也会被持久化，于是剩下那 20 个
    以 `pending` 的身份回来，走的是一条没有闸的路——**一次崩溃成了绕过成本闸的办法**。
    修之前这条是红的（20 个全跑）。
    """
    context = ToolContext(
        store=SqliteStoryGraph(conn),
        project_id=pid,
        root_path=str(tmp_path / "book"),
        working_chapter=2,
    )
    wide = tuple(ToolCall(id=f"w{i}", name="book_index", arguments="{}") for i in range(20))
    live = Conversation(
        messages=(
            AgentMessage(role=Role.USER, content="查"),
            AgentMessage(role=Role.ASSISTANT, content="", tool_calls=wide),
        )
    )
    assert len(live.pending_calls) == 20

    result = run_turn(
        live,
        context=context,
        model=lambda messages, *, tools, cancel: says("好了"),
        ledger=lambda receipt: None,
        limits=TurnLimits(max_calls_per_step=6),
    )
    assert result.reason is StopReason.BATCH_TOO_WIDE
    assert result.tool_calls == 0, "补跑那一批绕过了批宽度闸"
    # 壳必须配齐：悬空的 `tool_call` 下一次就是 400。
    assert [m.content for m in result.conversation.messages[2:]] == [UNRUN_CALL] * 20


# ══════════════════════════════════════════════════════════════════════════
# 三、崩了 + 作者又说了一句
# ══════════════════════════════════════════════════════════════════════════


def test_a_lost_lookup_behind_a_new_sentence_still_leaves_a_wire_that_can_be_sent(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者回来之后没有按「继续」，而是又说了一句。

    `pending_calls` 扫到作者发言就停（它只认尾巴上那一批），所以那个 `tool_call`
    **再也不会有人补跑**。补跑也不是选项：结果只能追加到队尾，也就是排在作者那句新话
    **后面**，那个顺序照样是 400。所以它必须在**投影里**就地配壳——投影是发出去之前的
    最后一道关口。这条路径在持久化之后变长了（跨了一次进程），这里重验它。
    """
    pid_ = book["pid"]
    real_dispatch = tools_mod.dispatch

    def dying_dispatch(call: Any, context: Any, memo: Any = None, stored: Any = None) -> Any:
        raise ProcessDied("断电")

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", dying_dispatch)
    use(monkeypatch, Scripted(wants(("book_index", "{}"), ("chapter_text", '{"chapter": 1}'))))
    chat_id = open_chat(client, pid_)
    with pytest.raises(ProcessDied):
        client.post(
            f"/api/projects/{pid_}/chats/{chat_id}/turn", json={"chapter": 2, "said": "查一下"}
        )
    chat_mod.LIVE.clear()
    assert client.get(f"/api/projects/{pid_}/chats/{chat_id}").json()["session"][
        "pending_lookups"
    ] == 2

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", real_dispatch)
    spoken = Scripted(says("行，那我直接说。"))
    use(monkeypatch, spoken)
    turn = client.post(
        f"/api/projects/{pid_}/chats/{chat_id}/turn",
        json={"chapter": 2, "said": "算了，别查了，直接说。"},
    )
    assert turn.status_code == 200, turn.text

    wire = spoken.sent[0]
    unanswered, orphan = dangling(wire)
    assert (unanswered, orphan) == ([], []), "作者那句新话把两个 `tool_call` 挡成了悬空的"
    assert [m["content"] for m in wire if m["role"] == "tool"] == [LOST_RESULT] * 2
    # **壳只活在投影里**，canonical 一个字都没改（它是永不破坏的那一份）。
    assert turn.json()["context"]["lost_lookups"] == 2
    assert [role for role, _, _ in history(book["db"], chat_id)].count("tool") == 0
    # 作者那句话必须排在最后一条，而不是被两个壳挤到中间去。
    assert wire[-1] == {"role": "user", "content": "算了，别查了，直接说。"}


def test_the_shell_keeps_working_on_every_later_turn_not_just_the_next_one(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """那条悬空的 `tool_call` **永远留在 canonical 里**（只增不改）。

    所以配壳不是「下一轮补一次」的一次性动作：第三轮、第十轮发出去的 wire 一样要配齐。
    一个只在紧接着那一轮生效的修法看起来一样绿。
    """
    pid_ = book["pid"]
    conn = connect(book["db"])
    chat_id = open_chat(client, pid_)
    ChatStore(conn).append(
        pid_,
        chat_id,
        base_count=0,
        messages=[
            AgentMessage(role=Role.USER, content="查一下"),
            AgentMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(ToolCall(id="ghost", name="book_index", arguments="{}"),),
            ),
            AgentMessage(role=Role.USER, content="算了"),
        ],
    )
    conn.close()

    for round_index in range(3):
        model = Scripted(says(f"第 {round_index} 次"))
        use(monkeypatch, model)
        turn = client.post(
            f"/api/projects/{pid_}/chats/{chat_id}/turn",
            json={"chapter": 2, "said": f"再说一句 {round_index}"},
        )
        assert turn.status_code == 200, turn.text
        assert dangling(model.sent[0]) == ([], []), f"第 {round_index} 轮的 wire 配不齐"


# ══════════════════════════════════════════════════════════════════════════
# 四、多会话：同一本书、同一章、两个窗口
# ══════════════════════════════════════════════════════════════════════════


def test_two_windows_on_the_same_chapter_do_not_resume_each_other(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两段会话各自崩在半路，**补跑的必须各是各的**。

    `pending_calls` 是 `Conversation` 上的一个查询，而 `Conversation` 是按 `session_id`
    读回来的——串了的话症状是「甲窗口的查询结果出现在乙窗口的历史里」，而那读起来完全正常。
    """
    pid_ = book["pid"]
    real_dispatch = tools_mod.dispatch

    def die(call: Any, context: Any, memo: Any = None, stored: Any = None) -> Any:
        raise ProcessDied("断电")

    left = open_chat(client, pid_, title="甲")
    right = open_chat(client, pid_, title="乙")

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", die)
    use(monkeypatch, Scripted(wants(("book_index", "{}"))))
    with pytest.raises(ProcessDied):
        client.post(f"/api/projects/{pid_}/chats/{left}/turn", json={"chapter": 2, "said": "甲问"})
    chat_mod.LIVE.clear()
    use(
        monkeypatch,
        Scripted(wants(("scene_constraints", '{"chapter": 2}'), ("book_index", "{}"))),
    )
    with pytest.raises(ProcessDied):
        client.post(f"/api/projects/{pid_}/chats/{right}/turn", json={"chapter": 2, "said": "乙问"})
    chat_mod.LIVE.clear()

    listed = {row["title"]: row["pending_lookups"] for row in client.get(
        f"/api/projects/{pid_}/chats"
    ).json()}
    assert listed == {"甲": 1, "乙": 2}

    monkeypatch.setattr("novel_harness.agent.tools.dispatch", real_dispatch)
    use(monkeypatch, Scripted(says("甲答")))
    first = client.post(f"/api/projects/{pid_}/chats/{left}/turn", json={"chapter": 2})
    assert first.json()["lookups"] == 1, "甲补跑的条数被乙那一段污染了"
    assert client.get(f"/api/projects/{pid_}/chats").json()  # 乙还没跑
    right_view = client.get(f"/api/projects/{pid_}/chats/{right}").json()
    assert right_view["session"]["pending_lookups"] == 2, "乙的 pending 被甲那一轮补掉了"
    assert [m["text"] for m in client.get(f"/api/projects/{pid_}/chats/{left}").json()[
        "messages"
    ]] == ["甲问", "甲答"]


def test_deleting_a_conversation_keeps_the_bill(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**钱已经付了。** 删掉一段对话删的是对话，不是账——同 `decision_log` 的理由：
    一条已经发生过的、花过钱的动作不因为它的载体被删掉就没发生过。

    验收不是「`model_call` 里还有行」，是**日志页上还看得见**（`GET /activity`）。
    """
    pid_ = book["pid"]
    use(monkeypatch, Scripted(says("好")))
    chat_id = open_chat(client, pid_)
    client.post(f"/api/projects/{pid_}/chats/{chat_id}/turn", json={"chapter": 1, "said": "喂"})
    def billed() -> list[dict[str, Any]]:
        page = client.get(f"/api/projects/{pid_}/activity").json()
        return [row for row in page["entries"] if row["title"] == "模型调用 · 写作助手"]

    before = billed()
    assert before, "这一轮根本没记账 —— 后面那条断言会假绿"

    assert client.delete(f"/api/projects/{pid_}/chats/{chat_id}").json()["deleted"] is True
    assert billed() == before, "删掉对话把作者付过的账一起删了"
    assert client.get(f"/api/projects/{pid_}/chats/{chat_id}").status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# 五、重放的代价：「T1–T5 重放免费」对**今天的**工具表还成不成立
# ══════════════════════════════════════════════════════════════════════════


def _draft_pending(pid_: str, conn: Connection, root: Path, drafter: Any) -> ToolContext:
    return ToolContext(
        store=SqliteStoryGraph(conn),
        project_id=pid_,
        root_path=str(root),
        drafter=drafter,
        working_chapter=2,
    )


def test_replaying_a_lookup_is_free_but_replaying_a_draft_is_not(
    conn: Connection, pid: str, tmp_path: Path
) -> None:
    """ADR 0019 写着「T1–T5 只读或纯函数，**重放免费**」。**那句话对起草那一个工具不成立。**

    `draft_chapter` 每跑一次是一次真的模型调用，花的是作者的钱。补跑不问「哪个工具贵」
    （那是一张会在加工具那天漂的表），所以这里把代价钉成一个数：
    **补跑一个 `draft_chapter` = 一次起草**。

    **2026-08-11（3.6）改了后半句**：起草的回执现在跟着 `DraftProduct.calls` 回到
    `run_turn`，由 `bill()` 记账 + 计闸。所以这一条现在同时钉两件事——补跑真的又起了
    一次草（`drafted == [2]`），而且**那一次花的钱真的落在了这一轮的账上**。
    以前它走起草侧自己的账，loop 的成本闸看不见它。

    这条不是在要求改行为（信号亮着就不动手那一档已经在 loop 里了），它是在把
    「重放免费」这句 ADR 原文的**适用范围**钉住：加第八个会花钱的工具时它会红。
    """
    drafted: list[int] = []
    billed: list[ModelCallReceipt] = []

    class Desk:
        """注入的那个起草台（ADR 0022 之后是三个动作）。**这一条只用得到第一个**：
        补跑重放的是 `draft_chapter`，而它现在**不落盘**——花的钱一分没少。"""

        def write(self, ask: DraftAsk, context: Any) -> DraftProduct:
            drafted.append(ask.chapter)
            return DraftProduct(
                candidate=DraftCandidate(
                    id="draft:01JTESTTESTTESTTESTTESTTEST",
                    chapter=ask.chapter,
                    ordinal=1,
                    units=2_400,
                    note="一稿。",
                    preview="一稿正文……",
                ),
                calls=(
                    ModelCallReceipt(
                        capability="writer",
                        schema_version="m5.draft.v1",
                        model="deepseek-v4-flash",
                        prompt_hash="ph",
                        prompt_bytes=b"{}",
                        text="一稿正文……",
                        prompt_tokens=900,
                        completion_tokens=2_600,
                    ),
                ),
            )

        def land(self, candidate_id: str) -> Any:  # pragma: no cover - 这一条不落盘
            raise AssertionError("补跑不该自己去落盘")

        def recall(self, candidate_id: str) -> Any:  # pragma: no cover - 这一条不读回
            raise AssertionError("补跑不该自己去读回")

    drafter = Desk()

    live = Conversation(
        messages=(
            AgentMessage(role=Role.USER, content="写一稿"),
            AgentMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(
                    ToolCall(id="free", name="book_index", arguments="{}"),
                    ToolCall(
                        id="paid",
                        name="draft_chapter",
                        arguments=json.dumps({"chapter": 2, "goal": "两人对峙"}),
                    ),
                ),
            ),
        )
    )
    result = run_turn(
        live,
        context=_draft_pending(pid, conn, tmp_path / "book", drafter),
        model=lambda messages, *, tools, cancel: says("写好了"),
        ledger=billed.append,
    )
    assert result.tool_calls == 2
    assert drafted == [2], "补跑没有重跑 `draft_chapter`（或者跑了不止一次）"
    # 只读那一层一次调用都没花钱；起草那一次花了，**而且它进了这一轮的账**。
    assert [r.capability for r in billed] == ["writer", "agent"], (
        "起草那一次没进 `ledger` —— 补跑一个断在半路的起草是真花钱，账上却看不见它"
    )
    assert result.tokens_reported == 900 + 2_600, (
        "工具花掉的 token 没进这一轮的汇总 —— 界面上那个数会低估，看起来却像全部"
    )


def test_the_paid_tool_is_wired_and_a_replay_really_costs_again(
    book: dict[str, str], tmp_path: Path
) -> None:
    """**上一条量的代价，2026-08-11 起在产品里是真的**（3.6 / ADR 0021）。

    这条以前断言的是 `drafter is None`（「那个工具还没接线，所以代价是零」），
    它自带一句「接线那天会红，而红的时候要一起想清楚补跑会再起一次草」。
    接线了，所以它翻过来：**产品路径上真的有一个会花钱的工具**，
    而上一条钉住的「补跑 = 再花一次钱」从此不是一个假设。

    这里同时钉住第二件事：`_tool_context` 交出去的仍然只是一个**注入进来的端口**——
    写入面握在那个起草台里，`ToolContext` 上没有 conn、没有 `CanonWriter`（边界一）。
    """
    conn = connect(book["db"])
    try:
        proj = type("P", (), {"id": book["pid"], "root_path": str(tmp_path)})
        capability = resolve_capabilities("https://api.deepseek.com", "deepseek-v4-flash")
        desk = chat_mod.chapter_drafter(
            store=SqliteStoryGraph(conn),
            conn=conn,
            project_id=book["pid"],
            root=str(tmp_path),
            config=ProviderConfig(base_url="https://api.deepseek.com", model="deepseek-v4-flash"),
            capability=capability,
            events=SqliteEventStore(conn),
            summaries=SummaryStore(conn),
        )
        context = chat_mod._tool_context(
            proj,
            SqliteStoryGraph(conn),
            conn,
            chapter=1,
            desk=desk,
            capability=capability,
            plan=_plan(),
        )
        assert context.drafter is desk and hasattr(context.drafter, "write"), (
            "`draft_chapter` 又没接线了 —— 那条产品路径上的起草工具会回一句「没接线」"
        )
        assert not hasattr(context, "conn") and not hasattr(context, "writer"), (
            "写入面爬回了 `ToolContext` —— 边界一那条类型保证是靠这个 dataclass 上没有它"
        )
    finally:
        conn.close()


def _plan() -> Any:
    """对话那一档的调用计划（`agent_call_plan` 的第二个产物）。"""
    from novel_harness.agent.model import agent_call_plan

    _, plan = agent_call_plan(
        ProviderConfig(base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    )
    return plan


def test_replaying_the_read_only_tools_changes_nothing_in_the_book(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「重放免费」的另一半：**重放也不许改书。**

    免费只说了钱。这一条量的是副作用——补跑的那几个只读工具跑完之后，磁盘上的正文
    和版本抽屉必须一个字节都没变（ADR 0007 / 边界三）。
    """
    pid_ = book["pid"]
    conn = connect(book["db"])
    root = str(
        conn.execute("SELECT root_path FROM project WHERE id = ?", (pid_,)).fetchone()["root_path"]
    )
    chapter_one = Path(root) / chapter_path(1)
    before_text = chapter_one.read_text(encoding="utf-8")
    before_versions = client.get(f"/api/projects/{pid_}/chapters/1/history").json()

    chat_id = open_chat(client, pid_)
    ChatStore(conn).append(
        pid_,
        chat_id,
        base_count=0,
        messages=[
            AgentMessage(role=Role.USER, content="把第 1 章读一遍"),
            AgentMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(
                    ToolCall(id="t1", name="chapter_text", arguments='{"chapter": 1}'),
                    ToolCall(id="t2", name="book_index", arguments="{}"),
                ),
            ),
        ],
    )
    conn.close()

    use(monkeypatch, Scripted(says("读完了。")))
    turn = client.post(f"/api/projects/{pid_}/chats/{chat_id}/turn", json={"chapter": 1})
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 2

    assert chapter_one.read_text(encoding="utf-8") == before_text
    assert client.get(f"/api/projects/{pid_}/chapters/1/history").json() == before_versions
