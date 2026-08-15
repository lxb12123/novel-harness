"""会话的持久化 —— **一串 message，加上「哪几个 `tool_call` 还缺 `tool_result`」**。

规格书是 [`docs/adr/0019-agent-loop-not-graph.md`](../../../docs/adr/0019-agent-loop-not-graph.md)
的「为什么不是图编排」：线性 tool loop 的执行态就这些，resume = **看尾巴、补跑缺的、继续**。
所以这一层只有两件事——把 `Conversation` 原样写下去、原样读回来。

── 这一层唯一真正的正确性判据 ────────────────────────────────────────────

> 读回来重建出的 `Conversation`，必须和它存进去之前那个**逐字节相同**。

不相同 = resume 之后模型看到的是另一段历史，**而没有任何东西会报错**。这条判据比
「能存能读」严得多，它拒掉四种看起来很合理的省事做法：

1. **稳定前缀不许在读回时重新生成。** `AGENT_SYSTEM_PROMPT` 是一个会改的常量；
   照它重拼，改常量的那一刻全部旧会话的开头被静默换掉。所以 `prefix` 也逐行落盘
   （`section='prefix'`）。
2. **`chapter` 是可空整数，不是 0。** `None` = 这条不绑章号（作者的话、agent 的推理），
   `40` = 这条工具返回只对第 40 章成立。投影按它筛（边界五），两者混同 = 把「不筛」
   变成「按第 0 章筛」。
3. **`pruned` 要存。** 一条被剪成占位的工具返回读回来当成真返回，模型就以为工具
   真的回了那么一句「这条查询结果已经清掉了」。
4. **`revokes_seq` 要存，而且它是可空的。** 作者取消一条规矩 = 历史上多一条指着它的
   记录（ADR 0023「摆出来、能取消」的后半截）。丢了它，读回来那条撤销就成了一条
   内容为空的普通消息，而**被取消掉的规矩会活过来**——作者按过的那个按钮变成没按过，
   且没有任何东西会报错。
   **它的坐标是历史的下标**（`Conversation.messages` 的下标、界面上每条消息带的那个
   `seq`），**不是这张表的 `seq` 那一列**——那一列把稳定前缀也数在内，两者差一个前缀
   长度。所以这一列是**原样落盘**的，读写两头都不换算：换算一次就有两个坐标系，
   而它们指到的是两条不同的消息。

── 追加，不是覆盖 ────────────────────────────────────────────────────────

canonical 对话是**只增不改**的：`run_turn` 全程走 `Conversation.extended()`，投影里那些
剪枝只发生在 `project()` 的副本上。所以这里的写入面只有 `append()`，没有 `replace()`——
有一个「整段写回」的方法，就有一条把历史改写掉的路径，而它错的时候同样不会报错。

`append()` 收一个 `base_count`（调用方手上那份历史有多少条）。这不是防御性编程：
作者能开多个会话窗口，也能在两个标签页里对着**同一个**会话各跑一轮。没有这个闸，
两轮的消息会交织成一段谁也读不懂的历史；有了它，后到的那一次拿到
`ChatConcurrency`，而不是一段静默损坏的对话。

── 第三档 `notice`：**在对话里，但不在 `Conversation` 里**（迁移 012）─────────

一轮没跑成，那句话要留在对话里（作者：「有提醒文字，但是过一会文字消失了，
没有必要消失」）。它落在同一张表上、占同一串 `seq`——**顺序只有这一份真相**——
但 `load()` 按 `section` 分流，它进的是 `StoredChat.notices`，**不进 `Conversation`**。

这条分流是那一档全部正确性的来源，两件事因此在实现里**够不着**而不是纪律上不许：

1. 它进不了投影（`project()` 只看 `Conversation.messages`），于是不会被喂回给模型
   ——喂回去它就成了「模型以为自己说过的话」；
2. 它进不了 `rules.is_rule()` 的视野。那是个**纯结构判据**（SYSTEM + 没有工具壳 +
   内容非空），塞一条别的用途的 SYSTEM 消息进 `messages`，**它会被当成一条规矩，
   在作者切到下一章时静默消失**（`agent/rules.py` 模块 docstring 末尾那段警告）。

它也**不占历史下标**：`history_count` 只数 `history`，所以 `AuthorRule.seq` /
`revokes_seq` 那套坐标一个数都不动。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..draft.provider import ToolCall
from ..ids import EntityType, new_id
from .loop import AgentMessage, Conversation, Role, start_conversation

PREFIX_SECTION: Final = "prefix"
HISTORY_SECTION: Final = "history"
NOTICE_SECTION: Final = "notice"
"""**不属于 `Conversation` 的那一档**（迁移 012）。见文件头最后一节。"""


class ChatConcurrency(RuntimeError):
    """这段对话在别处刚被追加过。**不静默重试**（同 `expected_canon_version` 那条 409）。"""


class ChatSessionRow(BaseModel):
    """一段会话的元信息。**正文一个字都不在这儿**——那是 `chat_message` 的事。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    title: str = ""
    created_at: str
    updated_at: str


class ChatNotice(BaseModel):
    """一轮没跑成，留在对话里的那一行。**它不是对话的一部分**（见文件头第三档）。

    **不是 `AgentMessage`，这是有意的**：它要是长成一条消息，谁哪天顺手把它拼进
    `Conversation.messages` 就完事了——而那正是要防的两件事（进 prompt / 被当成规矩）。
    两个类型互相塞不进去，那两条判据就不需要任何人记得写。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    """说给作者的那一句。**唯一出处是 `agent/loop.py::stop_wording()`**，这一层只搬运。"""

    after_history: int = Field(ge=0)
    """它前面有几条历史消息 —— 屏幕上它就画在那两句话中间。

    **不是存下来的一列，是读的时候按 `seq` 数出来的。** 存一列就有两份顺序真相
    （那一列和 `seq`），而它们对不上的时候没有任何东西会报错：屏幕上那句
    「这一轮没跑成」会挂到另一句话底下，读起来像是**别的那一轮**失败了。
    """


class StoredChat(BaseModel):
    """从库里读回来的一整段会话。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ChatSessionRow
    conversation: Conversation
    history_count: int = Field(ge=0)
    """`conversation.messages` 的条数。**追加时要原样交回来**（见 `append`）。

    **它不含 `notices`**：那一档不占历史下标，所以追加那道乐观并发闸、
    `AuthorRule.seq`、`revokes_seq` 全都不因为多了一行提示而移位。
    """

    notices: tuple[ChatNotice, ...] = ()
    """这段对话里那几行「这一轮没跑成」。**按它们在对话里的位置排。**"""


def _dump_calls(calls: Sequence[ToolCall]) -> str:
    # `arguments` 原样进一个 JSON 字符串值：它可以是残缺的 JSON、可以带换行，
    # 这一层不解析（那道闸只在 `tools.dispatch`）。
    return json.dumps(
        [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
        ensure_ascii=False,
    )


def _load_calls(raw: str) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(id=str(item["id"]), name=str(item["name"]), arguments=str(item["arguments"]))
        for item in json.loads(raw)
    )


def _to_message(row: Any) -> AgentMessage:
    chapter = row["chapter"]
    revokes = row["revokes_seq"]
    return AgentMessage(
        role=Role(str(row["role"])),
        content=str(row["content"]),
        tool_calls=_load_calls(str(row["tool_calls_json"])),
        tool_call_id=str(row["tool_call_id"]),
        chapter=None if chapter is None else int(chapter),
        pruned=bool(row["pruned"]),
        # 016 之前存下的行没有这一列的值（`DEFAULT ''` 兜着），所以这儿也吃 None。
        rule_until=str(row["rule_until"] or ""),
        # 同 `chapter`：`None` 和 `0` 是两件事（`0` 是「撤销第 0 条」，那是一条真的消息）。
        revokes_seq=None if revokes is None else int(revokes),
    )


class ChatStore:
    """`chat_session` / `chat_message` 的读写。**收一条连接，自己不开连接。**"""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── 会话 ──────────────────────────────────────────────────────────────

    def create(
        self, project_id: str, *, title: str = "", write_rule: str | None = None
    ) -> ChatSessionRow:
        """开一段新会话，并把稳定前缀落盘。

        `write_rule` 是作者的文风偏好。**它进前缀，所以它必须跨章不变**（边界六）——
        `start_conversation` 造出来的 `Conversation` 上那条校验器是这件事的执行者，
        这里只是把它的产物原样写下去。
        """
        session_id = new_id(EntityType.CHAT_SESSION, project_id)
        conversation = start_conversation(write_rule)

        def run() -> None:
            self._conn.execute(
                "INSERT INTO chat_session (id, project_id, title) VALUES (?, ?, ?)",
                (session_id, project_id, title.strip()),
            )
            self._insert(session_id, project_id, 0, PREFIX_SECTION, conversation.prefix)

        self._write(run)
        row = self.get(project_id, session_id)
        if row is None:  # pragma: no cover - 刚写进去就读不到只可能是库坏了
            raise RuntimeError("刚建好的会话读不回来")
        return row

    def get(self, project_id: str, session_id: str) -> ChatSessionRow | None:
        row = self._conn.execute(
            "SELECT id, project_id, title, created_at, updated_at FROM chat_session"
            " WHERE project_id = ? AND id = ?",
            (project_id, session_id),
        ).fetchone()
        return None if row is None else ChatSessionRow(**dict(row))

    def list(self, project_id: str, *, limit: int = 50) -> list[ChatSessionRow]:
        """最近说过话的在前。**按 `updated_at` 不按 `created_at`**：作者回到三个月前
        开的那段会话接着聊，它就该回到列表最上面。"""
        rows = self._conn.execute(
            "SELECT id, project_id, title, created_at, updated_at FROM chat_session"
            " WHERE project_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [ChatSessionRow(**dict(row)) for row in rows]

    def history_counts(self, project_id: str) -> dict[str, int]:
        """每段会话各有多少条历史。**列表页要的是一个数，不是几百条消息。**

        放在这儿而不是放在 HTTP 壳里，是因为这两张表的 SQL 只许有一处——
        `'history'` 这个字面量在壳里再写一遍，改 `section` 取值的那天会漏掉那一处，
        而漏掉的症状是列表上每段对话都显示 0 条。
        """
        rows = self._conn.execute(
            "SELECT s.id AS id, COUNT(m.id) AS n FROM chat_session s"
            " LEFT JOIN chat_message m ON m.session_id = s.id AND m.section = ?"
            " WHERE s.project_id = ? GROUP BY s.id",
            (HISTORY_SECTION, project_id),
        ).fetchall()
        return {str(row["id"]): int(row["n"]) for row in rows}

    def pending_counts(self, project_id: str) -> dict[str, int]:
        """每段会话还缺几个 `tool_result`（`Conversation.pending_calls` 的批量版）。

        ── 为什么不是一句 SQL ────────────────────────────────────────────────

        「哪几个 `tool_call` 还缺 `tool_result`」是**线性 loop 的全部执行态**，它的定义
        只许有一处（`Conversation.pending_calls`）。用 SQL 再数一遍就是第二份执行态，
        而两份对不上的时候没有任何东西会报错——那正是 006 那份迁移开头拒绝加一列
        「跑到哪一步」的同一条理由。

        ── 为什么这样读不算「把每段对话都拉出来」 ────────────────────────────

        `pending_calls` 扫到 `USER` 就停，所以**只有最后一句作者发言之后的那几行**能
        影响答案。这里就只取那几行（`seq >` 那一句的 `seq`，走 `idx_chat_message`），
        列表页因此仍然不为了数一个数去读几百条历史。
        """
        rows = self._conn.execute(
            "SELECT m.session_id AS sid, m.role AS role, m.content AS content,"
            "       m.tool_calls_json AS tool_calls_json, m.tool_call_id AS tool_call_id,"
            "       m.chapter AS chapter, m.pruned AS pruned, m.revokes_seq AS revokes_seq,"
            "       m.rule_until AS rule_until"
            " FROM chat_message m"
            " JOIN chat_session s ON s.id = m.session_id"
            " WHERE s.project_id = ? AND m.section = ?"
            "   AND m.seq > COALESCE((SELECT MAX(u.seq) FROM chat_message u"
            "                         WHERE u.session_id = m.session_id AND u.section = ?"
            "                           AND u.role = ?), -1)"
            " ORDER BY m.session_id, m.seq",
            (project_id, HISTORY_SECTION, HISTORY_SECTION, str(Role.USER)),
        ).fetchall()
        tails: dict[str, list[AgentMessage]] = {}
        for row in rows:
            tails.setdefault(str(row["sid"]), []).append(_to_message(row))
        return {
            sid: len(Conversation(messages=tuple(tail)).pending_calls)
            for sid, tail in tails.items()
        }

    def rename(self, project_id: str, session_id: str, title: str) -> bool:
        changed = 0

        def run() -> None:
            nonlocal changed
            changed = self._conn.execute(
                "UPDATE chat_session SET title = ? WHERE project_id = ? AND id = ?",
                (title.strip(), project_id, session_id),
            ).rowcount

        self._write(run)
        return changed == 1

    def delete(self, project_id: str, session_id: str) -> bool:
        """删一段会话。消息随 `ON DELETE CASCADE` 一起走。

        **这不会动到任何一行正文**：这两张表里没有正文（ADR 0007 / 边界三）。
        """
        changed = 0

        def run() -> None:
            nonlocal changed
            changed = self._conn.execute(
                "DELETE FROM chat_session WHERE project_id = ? AND id = ?",
                (project_id, session_id),
            ).rowcount

        self._write(run)
        return changed == 1

    # ── 消息 ──────────────────────────────────────────────────────────────

    def load(self, project_id: str, session_id: str) -> StoredChat | None:
        """读回整段会话。**出参就是 `run_turn` 要的那个 `Conversation`。**

        **一趟扫完，按 `section` 分流。** 提示那一档（`notice`）在这儿和对话分开，
        而这一次分流就是它「不进 prompt、不被当成规矩」的全部实现——下游谁都不用
        再写一条过滤（见文件头第三档）。它排在哪儿由 `seq` 的顺序数出来，不另存一列。
        """
        session = self.get(project_id, session_id)
        if session is None:
            return None
        rows = self._conn.execute(
            "SELECT section, role, content, tool_calls_json, tool_call_id, chapter, pruned,"
            "       revokes_seq, rule_until"
            " FROM chat_message WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        prefix: list[AgentMessage] = []
        history: list[AgentMessage] = []
        notices: list[ChatNotice] = []
        for row in rows:
            section = str(row["section"])
            if section == PREFIX_SECTION:
                prefix.append(_to_message(row))
            elif section == HISTORY_SECTION:
                history.append(_to_message(row))
            elif section == NOTICE_SECTION:
                notices.append(
                    ChatNotice(text=str(row["content"]), after_history=len(history))
                )
        return StoredChat(
            session=session,
            conversation=Conversation(prefix=tuple(prefix), messages=tuple(history)),
            history_count=len(history),
            notices=tuple(notices),
        )

    def note(self, project_id: str, session_id: str, text: str) -> ChatNotice:
        """把一句「这一轮没跑成」追加到对话里，返回它落在了哪儿。

        **它不走 `append`**，两条理由都不是洁癖：

        - `append` 那道乐观并发闸问的是「你手上那份历史有多少条」，而这一行**不占
          历史下标**——拿它去过那道闸，闸会因为一行提示而判失败；
        - `append` 收的是 `AgentMessage`，而提示**故意不是**那个类型（见 `ChatNotice`）。

        `after_history` 在**同一个事务里**数出来，所以它和这一行真正的落点一致：
        写完之后再数一次的话，中间插进来的另一轮会让屏幕上那句话挂错位置。

        Args:
            text: 说给作者的那一句。唯一出处是 `agent/loop.py::stop_wording()`。

        Raises:
            ValueError: 空话。**一行没有内容的提示比不提示更坏**——屏幕上多出一格
                空白，而作者要找的是「刚才那一轮怎么了」。
        """
        line = text.strip()
        if not line:
            raise ValueError("这一行提示是空的：说不出这一轮为什么没跑成就别写它。")
        landed = ChatNotice(text=line, after_history=0)

        def run() -> None:
            nonlocal landed
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS last FROM chat_message WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            counted = self._conn.execute(
                "SELECT COUNT(*) AS n FROM chat_message WHERE session_id = ? AND section = ?",
                (session_id, HISTORY_SECTION),
            ).fetchone()
            self._conn.execute(
                "INSERT INTO chat_message (id, session_id, seq, section, role, content)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id(EntityType.CHAT_MESSAGE, project_id),
                    session_id,
                    int(row["last"]) + 1,
                    NOTICE_SECTION,
                    str(Role.SYSTEM),
                    line,
                ),
            )
            # 侧栏按 `updated_at` 排。**这一行也得把它推上去**：一段刚刚跑砸了的对话
            # 沉在列表底下，作者下次打开工作台会以为什么都没发生过。
            self._conn.execute(
                "UPDATE chat_session"
                " SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                " WHERE project_id = ? AND id = ?",
                (project_id, session_id),
            )
            landed = ChatNotice(text=line, after_history=int(counted["n"]))

        self._write(run)
        return landed

    def append(
        self,
        project_id: str,
        session_id: str,
        *,
        base_count: int,
        messages: Sequence[AgentMessage],
    ) -> int:
        """把新长出来的那几条追加进去，返回追加后的历史条数。

        Raises:
            ChatConcurrency: 库里的历史条数不等于 `base_count`——别人刚追加过。
        """
        if not messages:
            return base_count
        new_count = base_count

        def run() -> None:
            nonlocal new_count
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(MAX(seq), -1) AS last FROM chat_message"
                " WHERE session_id = ? AND section = ?",
                (session_id, HISTORY_SECTION),
            ).fetchone()
            if int(row["n"]) != base_count:
                raise ChatConcurrency(
                    f"这段对话在别处已经往前走了（库里 {int(row['n'])} 条，"
                    f"这一次基于 {base_count} 条）"
                )
            top = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS last FROM chat_message WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            self._insert(session_id, project_id, int(top["last"]) + 1, HISTORY_SECTION, messages)
            self._conn.execute(
                "UPDATE chat_session"
                " SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                " WHERE project_id = ? AND id = ?",
                (project_id, session_id),
            )
            new_count = base_count + len(messages)

        self._write(run)
        return new_count

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _insert(
        self,
        session_id: str,
        project_id: str,
        first_seq: int,
        section: str,
        messages: Sequence[AgentMessage],
    ) -> None:
        self._conn.executemany(
            "INSERT INTO chat_message"
            " (id, session_id, seq, section, role, content, tool_calls_json,"
            "  tool_call_id, chapter, pruned, revokes_seq, rule_until)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    new_id(EntityType.CHAT_MESSAGE, project_id),
                    session_id,
                    first_seq + offset,
                    section,
                    str(message.role),
                    message.content,
                    _dump_calls(message.tool_calls),
                    message.tool_call_id,
                    message.chapter,
                    int(message.pruned),
                    # **原样写下去，不换算**：它是历史的下标，不是上面那个 `seq`（见文件头）。
                    message.revokes_seq,
                    message.rule_until,
                )
                for offset, message in enumerate(messages)
            ],
        )

    def _write(self, body: Callable[[], None]) -> None:
        """一次写事务。**`BEGIN IMMEDIATE`**：追加的乐观检查是「先读计数再写」，
        裸 BEGIN（deferred）下那次读拿的是旧快照，两个进程会同时通过检查。"""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            body()
        except BaseException:
            self._conn.rollback()
            raise
        self._conn.commit()


__all__ = [
    "ChatConcurrency",
    "ChatNotice",
    "ChatSessionRow",
    "ChatStore",
    "StoredChat",
]
