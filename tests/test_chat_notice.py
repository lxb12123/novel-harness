"""一轮没跑成，那句话留在**对话里**，不是留在界面状态里（迁移 012）。

── 2026-08-13 的真实现场，这份文件钉的就是它 ──────────────────────────────

作者打开工作台，说了两句话，助手一个字都没有：

    你：你好
    你：fff

库里查：`chat_message` 只有他那两条，`model_call` 一次都没增加——那一轮**在发出去
之前就死了**（他配的端点，那台机器到它的 TLS 全断）。屏幕上确实弹过一句提醒，
可它活在浏览器的组件状态里：组件一卸载、他再发一句，那句话就没了。
作者的原话：「有提醒文字，但是过一会文字消失了，**没有必要消失**。」

── 三条必须同时成立，缺一条这一刀就是坏的 ────────────────────────────────

1. **不进模型的上下文。** 这句话是说给作者听的，喂回去就成了「模型以为自己说过的话」，
   而且每一轮都要为它付一次钱。
2. **不被 `agent/rules.py::is_rule` 当成一条规矩。** 那是个纯结构判据（SYSTEM +
   没有工具壳 + 内容非空），往 `messages` 里塞一条别的用途的 SYSTEM 消息，
   它会被当成规矩，**在作者切到下一章时静默消失**。
3. **上屏，而且切走切回还在。**

前两条在这一刀里是**结构成立**的（`section` 的第三档，`ChatStore.load` 分流时就
拦住了），所以下面那几条断言不是「记得写了过滤」的复查，是「那条路真的不通」的探针。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
from novel_harness.agent.loop import StopReason, stop_wording
from novel_harness.agent.rules import is_rule, surviving_rule_indices
from novel_harness.agent.store import ChatStore
from novel_harness.db import connect, migrate, user_version
from novel_harness.draft.provider import ProviderError

from test_chat_api import Scripted, configured, open_chat, says, use, wants  # noqa: F401

__all__ = ["configured"]  # re-export：这一份用的是 `test_chat_api` 那套 BYOK 装配


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


class Unreachable:
    """端点连不上（2026-08-13 那台机器的 TLS 全断）。**它一个 token 都没花掉。**"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> Any:
        self.calls += 1
        raise ProviderError("TLSV1_ALERT_INTERNAL_ERROR: api.example.com")


def _turn(client: TestClient, pid: str, chat_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _on_screen(client: TestClient, pid: str, chat_id: str) -> list[tuple[str, str]]:
    """重新打开这段对话时，屏幕上从上到下是哪几句。"""
    detail = client.get(f"/api/projects/{pid}/chats/{chat_id}")
    assert detail.status_code == 200, detail.text
    return [(m["speaker"], m["text"]) for m in detail.json()["messages"]]


UNREACHABLE = stop_wording(StopReason.MODEL_UNREACHABLE)


# ══════════════════════════════════════════════════════════════════════════
# 一、那一轮死在发出去之前 —— 作者第二天回来还看得见
# ══════════════════════════════════════════════════════════════════════════


def test_a_turn_that_died_before_it_left_the_machine_stays_in_the_conversation(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """现场复刻：说两句、两轮都死在发出去之前，**每一轮各留一行**，而且各在各的位置。

    这条断言的形状就是作者画的那张：

        你：你好
        系统：<这一轮为什么没跑成>
        你：fff
        系统：<这一轮为什么没跑成>

    **两行不许堆在末尾**：堆起来读成「最后这一轮失败了两次」，而真相是两轮各失败一次。
    """
    pid = book["pid"]
    model = Unreachable()
    use(monkeypatch, model)
    chat_id = open_chat(client, pid)

    first = _turn(client, pid, chat_id, chapter=1, said="你好")
    assert first["reason"] == StopReason.MODEL_UNREACHABLE.value
    _turn(client, pid, chat_id, chapter=1, said="fff")
    assert model.calls == 2

    assert _on_screen(client, pid, chat_id) == [
        ("author", "你好"),
        ("system", UNREACHABLE),
        ("author", "fff"),
        ("system", UNREACHABLE),
    ]


def test_the_line_says_what_the_backend_says_and_not_one_word_more(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """措辞的**唯一出处在后端**（`stop_wording()`），这一层一个字都不加。

    前端也不做码 → 中文的映射（那张表被删过一次，理由在
    `frontend/src/correctionError.ts` 顶上），所以这句话必须一路原样走到屏幕上。
    """
    pid = book["pid"]
    use(monkeypatch, Unreachable())
    chat_id = open_chat(client, pid)
    receipt = _turn(client, pid, chat_id, chapter=1, said="你好")

    # 回执上那句话和留在对话里的那一行**是同一串字**，不是两处各写一份。
    assert receipt["message"] == UNREACHABLE
    assert [m for m in receipt["messages"] if m["speaker"] == "system"] == [
        {"seq": 1, "speaker": "system", "text": UNREACHABLE}
    ]
    # **维护者那份英文诊断一个字都不许出去**（`TurnResult.maintainer_note`）。
    assert "TLSV1" not in json.dumps(receipt, ensure_ascii=False)
    assert "TLSV1" not in json.dumps(_on_screen(client, pid, chat_id), ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# 二、判据一：它不进模型的上下文
# ══════════════════════════════════════════════════════════════════════════


def test_the_line_is_never_sent_back_to_the_model(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下一轮真的发出去的那份 wire 里**没有这句话**。

    喂回去有两个代价，而且第二个更贵：作者每一轮都为它付一次钱（它每轮都要重发），
    以及**模型会以为自己说过这句话**——于是它可能接着道歉、接着解释一次它并没有
    经历过的失败。
    """
    pid = book["pid"]
    use(monkeypatch, Unreachable())
    chat_id = open_chat(client, pid)
    _turn(client, pid, chat_id, chapter=1, said="你好")

    good = Scripted(says("这次通了。"))
    use(monkeypatch, good)
    _turn(client, pid, chat_id, chapter=1, said="再试一次")

    wire = json.dumps(good.calls[0], ensure_ascii=False)
    assert "你好" in wire, "探针：作者说过的话本来就该在这份 wire 里"
    assert UNREACHABLE not in wire, (
        "那行提示被喂回给模型了 —— 它会以为自己说过这句话，而作者每一轮都在为它付钱"
    )


def test_the_line_is_not_in_the_conversation_that_comes_back_out_of_the_database(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """结构判据：读回来的 `Conversation` 里**根本没有它**，所以上一条不靠任何过滤。

    这才是选「`section` 第三档」而不是「消息里带一个机器码」的全部理由——
    后者要在投影和 `is_rule` 两处各写一条跳过，而漏掉哪一处都不报错。
    """
    pid = book["pid"]
    use(monkeypatch, Unreachable())
    chat_id = open_chat(client, pid)
    _turn(client, pid, chat_id, chapter=1, said="你好")

    conn = connect(book["db"])
    try:
        stored = ChatStore(conn).load(pid, chat_id)
        rows = [
            (str(r["section"]), str(r["content"]))
            for r in conn.execute(
                "SELECT section, content FROM chat_message"
                " WHERE session_id = ? ORDER BY seq",
                (chat_id,),
            )
        ]
    finally:
        conn.close()

    assert stored is not None
    # 库里真有这一行（不然下面那几条断言测的是「压根没写」）。
    assert ("notice", UNREACHABLE) in rows
    assert all(UNREACHABLE not in m.content for m in stored.conversation.messages)
    assert all(UNREACHABLE not in m.content for m in stored.conversation.prefix)
    assert [n.text for n in stored.notices] == [UNREACHABLE]
    # 它不占历史下标：撤销规矩、乐观并发闸用的都是这个数。
    assert stored.history_count == len(stored.conversation.messages) == 1


# ══════════════════════════════════════════════════════════════════════════
# 三、判据二：它不是一条规矩
# ══════════════════════════════════════════════════════════════════════════


def test_the_line_is_never_mistaken_for_a_rule_the_author_laid_down(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_rule` 是**纯结构判据**，它认不出「这条其实不是规矩」。

    所以判据不能是「记得别让它长成一条规矩」，得是「它根本到不了 `is_rule` 面前」。
    被当成规矩的后果不报错也不留痕：它会跟着进每一轮的 prompt，然后**在作者切到
    下一章时静默消失**。
    """
    pid = book["pid"]
    use(monkeypatch, Unreachable())
    chat_id = open_chat(client, pid)
    _turn(client, pid, chat_id, chapter=1, said="你好")

    conn = connect(book["db"])
    try:
        stored = ChatStore(conn).load(pid, chat_id)
    finally:
        conn.close()
    assert stored is not None
    assert not any(is_rule(m) for m in stored.conversation.messages)
    assert surviving_rule_indices(stored.conversation.messages) == frozenset()
    # ⚠️ 这儿原来还有一遍端到端：`GET …/rules` 那条路由。**2026-08-14 两条路由都撤了**
    # （ADR 0028——规矩不上屏），所以「作者面板上也没有它」这半句无处可验了。


def test_a_notice_does_not_disturb_a_rule_that_was_already_recorded(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先记一条规矩、再跑砸一轮 —— 那条规矩还在，而且还是同一条。

    ── 这条测试 2026-08-14 换了主张 ──────────────────────────────────────

    原来它量的是 `AuthorRule.seq`（历史下标）在提示插进来之后一个数都不动，
    理由是作者点「×」时报的就是它。**那颗「×」连同两条路由一起撤了**（[ADR 0028]：
    规矩不上屏，有效期由模型按情境判），于是那个坐标不再有对外的消费者。

    **换而不是删**：提示是这一层唯一会往历史中间插东西的机制，而规矩的去重和取消
    都按历史下标算（`_revoked_indices` / `surviving_rule_indices`）。插进来的一行
    要是把规矩挤出投影，模型下一轮就看不见作者刚交代的事——而不会有任何东西报错。
    """
    pid = book["pid"]
    remembered = json.dumps({"rule": "别写打斗", "until": "这一章写完"})
    use(monkeypatch, Scripted(wants(("remember_rule", remembered)), says("好")))
    chat_id = open_chat(client, pid)
    _turn(client, pid, chat_id, chapter=1, said="打斗少一点")

    def rules_now() -> list[str]:
        conn = connect(book["db"])
        try:
            stored = ChatStore(conn).load(pid, chat_id)
        finally:
            conn.close()
        assert stored is not None
        messages = stored.conversation.messages
        return [messages[i].content for i in sorted(surviving_rule_indices(messages))]

    before = rules_now()
    assert before == ["别写打斗"], "探针：这一轮真的记下了一条规矩"

    # **接着往下跑**（`said` 留空 = resume）：这一轮会砸，砸完往历史里插一行提示。
    use(monkeypatch, Unreachable())
    _turn(client, pid, chat_id, chapter=1, said="")

    assert rules_now() == before


# ══════════════════════════════════════════════════════════════════════════
# 四、哪些失败**不**留痕，以及为什么
# ══════════════════════════════════════════════════════════════════════════


def test_a_turn_that_actually_said_something_leaves_no_line(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跑成了的那一轮**一行都不留**。

    判据是结构（这一轮在屏幕上留下了什么），不是一张「哪几种停法算失败」的表——
    `done` 那一档因此自动不留痕，没人需要在哪儿点它的名。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("第 1 章可以说破。")))
    chat_id = open_chat(client, pid)
    receipt = _turn(client, pid, chat_id, chapter=1, said="第 1 章能说破吗？")

    assert receipt["reason"] == StopReason.DONE.value
    assert all(m["speaker"] != "system" for m in receipt["messages"])
    assert _on_screen(client, pid, chat_id) == [
        ("author", "第 1 章能说破吗？"),
        ("assistant", "第 1 章可以说破。"),
    ]


def test_pressing_stop_is_not_a_failure_and_leaves_no_line(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**作者按了停不是失败。**

    往历史里写一行「这一轮没跑成」等于把他自己做的一个决定说成一次故障，
    而那一档 ADR 0024 已经有它自己的说法（`turn_stopped` + 回执那句
    「按你的意思停下了」）。

    剧本是「一句话没说、只要了个工具，然后按停」——**它在屏幕上留下的东西和那次
    真故障一模一样（什么都没有）**，所以这一条真的在验那半条判据，不是碰巧绿的。
    """
    pid = book["pid"]

    def stop_it(cancel: Any) -> None:
        cancel.stop()

    use(monkeypatch, Scripted(wants(("book_index", "{}")), before=stop_it))
    chat_id = open_chat(client, pid)
    receipt = _turn(client, pid, chat_id, chapter=1, said="写点什么")

    assert receipt["reason"] == StopReason.AUTHOR_STOPPED.value
    assert all(m["speaker"] != "assistant" for m in receipt["messages"]), (
        "探针：这一轮在屏幕上什么都没留下 —— 和那次真故障长得一样"
    )
    assert all(m["speaker"] != "system" for m in receipt["messages"])
    assert all(speaker != "system" for speaker, _ in _on_screen(client, pid, chat_id))


def test_a_turn_that_never_started_leaves_nothing_behind(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「这段对话正在跑上一轮」那个 409 **一个字都不留**。

    它连作者那句话都没进历史（`_TurnRun.__init__` 在追加之前就抛了），所以写一行
    进去就是凭空造出一轮没发生过的对话：屏幕上会有「这一轮没跑成」，而它上面
    没有任何人说过话。作者的字被还回了输入框，那才是他要的东西。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("嗯")))
    chat_id = open_chat(client, pid)
    _turn(client, pid, chat_id, chapter=1, said="第一句")

    # 假装这段对话正在跑（进程内事实，同 `_Running`）。
    chat_mod.LIVE.begin((pid, chat_id))
    busy = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 1, "said": "第二句"}
    )
    chat_mod.LIVE.end((pid, chat_id))
    assert busy.status_code == 409, busy.text

    screen = _on_screen(client, pid, chat_id)
    assert all(speaker != "system" for speaker, _ in screen)
    assert ("author", "第二句") not in screen


def test_a_model_that_is_not_configured_leaves_nothing_behind(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """模型没配好那个 422 同理：**作者那句话根本没落库**，历史里没有可依附的东西。

    这一档他能做的动作是去顶栏 ⚙ 改设置，而他打的字还在输入框里 —— 屏幕上多一行
    「这一轮没跑成」而它上面一句话都没有，只会让他以为自己发出去过。
    """
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("NH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NH_LLM_MODEL", raising=False)
    monkeypatch.delenv("NH_LLM_API_KEY", raising=False)
    pid = book["pid"]
    chat_id = open_chat(client, pid)

    refused = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 1, "said": "你好"}
    )
    assert refused.status_code == 422, refused.text
    assert _on_screen(client, pid, chat_id) == []


# ══════════════════════════════════════════════════════════════════════════
# 五、迁移 012 跑在作者的真书上
# ══════════════════════════════════════════════════════════════════════════


def _at_version_11(path: Path) -> Any:
    """一个停在第 11 版、里面已经有一段三条消息的对话的库（同 `test_call_chapter_and_backup`）。"""
    from importlib.resources import files

    conn = connect(path)
    root = files("novel_harness") / "migrations"
    for entry in sorted(e.name for e in root.iterdir() if e.name.endswith(".sql")):
        if int(entry[:3]) > 11:
            continue
        conn.executescript((root / entry).read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES ('project:old', '青云记', '/x')"
    )
    conn.execute(
        "INSERT INTO chat_session (id, project_id, title) VALUES ('chat:old', 'project:old', '旧的')"
    )
    for seq, (section, role, content, revokes) in enumerate(
        [
            ("prefix", "system", "你是一位写作搭档。", None),
            ("history", "user", "打斗少一点", None),
            ("history", "system", "别写打斗", None),
        ]
    ):
        conn.execute(
            "INSERT INTO chat_message (id, session_id, seq, section, role, content, chapter,"
            " revokes_seq) VALUES (?, 'chat:old', ?, ?, ?, ?, ?, ?)",
            (f"chat_message:old{seq}", seq, section, role, content, 1 if seq == 2 else None,
             revokes),
        )
    conn.commit()
    assert user_version(conn) == 11
    return conn


def test_migration_012_runs_on_a_book_that_already_has_three_messages(
    tmp_path: Path,
) -> None:
    """重建整张表**一行不增一行不减**，而且第三档当场可用。

    这条迁移会跑在作者的真书上（`db.py::migrate` 已经先 `VACUUM INTO` 备份了一份）。
    重建 SQLite 表最贵的错法是漏一列——那不报错，只是那一列的数据从此不见了，
    所以这儿逐列比对，`revokes_seq`（011 加的那一列）尤其要在。
    """
    conn = _at_version_11(tmp_path / "book.db")
    before = [dict(r) for r in conn.execute("SELECT * FROM chat_message ORDER BY seq")]

    assert migrate(conn) == 39
    after = [dict(r) for r in conn.execute("SELECT * FROM chat_message ORDER BY seq")]
    # **只比 011 那时就有的那几列。** 012 之后的迁移还会往这张表上加列（016 的
    # `rule_until` 就是一个），而这条测试量的是「012 重建整张表时有没有漏掉一列」——
    # 拿全列去比，任何一次后续 `ADD COLUMN` 都会让它红，而那一次根本没碰这条迁移。
    kept = [{key: row[key] for key in before[0]} for row in after]
    assert kept == before, "重建之后有列对不上 —— 那一列的数据已经没了，而且不报错"

    # 索引跟着 DROP 一起没了，必须重建过（少了它，列表页那两条聚合查询会全表扫）。
    indexes = [
        str(r["name"])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'chat_message'"
        )
    ]
    assert "idx_chat_message" in indexes
    # 外键还在（删一段对话要连消息一起走）。
    assert [
        str(r["table"]) for r in conn.execute("PRAGMA foreign_key_list(chat_message)")
    ] == ["chat_session"]

    conn.execute(
        "INSERT INTO chat_message (id, session_id, seq, section, role, content)"
        " VALUES ('chat_message:note', 'chat:old', 3, 'notice', 'system', '这一轮没跑成')"
    )
    conn.commit()
    assert ChatStore(conn).load("project:old", "chat:old") is not None
    conn.close()


def test_the_third_section_is_the_only_one_that_was_added(tmp_path: Path) -> None:
    """**自守卫**：`section` 仍然是一个封闭集合，不是「随便写什么都行」。

    重建整张表最省事的写法是顺手把 CHECK 删掉，而那之后「拼错一个 section」
    不会报错，只会让那几行消息在读回来时**静默消失**（`load` 三个分支都不认它）。
    """
    conn = _at_version_11(tmp_path / "book.db")
    migrate(conn)
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chat_message (id, session_id, seq, section, role, content)"
            " VALUES ('chat_message:x', 'chat:old', 9, 'histroy', 'user', '拼错了')"
        )
    conn.close()
