"""**作者的规矩**在浏览器里看得见、点得掉（[ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。

引擎侧的四件事（存 / 数重复 / 按章号过期 / 撤销）在 `tests/test_author_rules.py` 里
被逐条钉过了。**这份文件量的是别的东西：那条退路的最后一厘米。**

ADR 0023 把「规矩提炼是模型的判断，它会判错」的退路押在一句话上——
**看得见 + 能取消，不是事前确认**。在这两条路由之前，`agent/rules.py` 在 `src/` 里
一个调用方都没有：规矩记得下、过得了期，而作者看不见它、也点不掉它，
于是那条退路只兑现了一半。这个仓库栽过五次同一种病（能力建完了，最后一厘米没接）。

量四件事，每一件都是「不做它这个按钮就是假的」的那一种：

1. **它真的从模型那条路上长出来**（不是测试自己塞进库里的一条）；
2. 🔴 **撤销按身份，不按下标** —— 同一条记过两遍时，只划掉那个下标的话，
   前面那一遍还在、而且已经是章级的：按钮按了、规矩还在，**且没有任何东西会报错**；
3. **界面报的那个 `seq` 和引擎认的是同一个坐标**（它不是会话表里那一列）；
4. **零带着理由** —— 「还没有规矩」和「定过、这会儿都不作数了」在出参上分得开。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.agent.rules as rules_mod
import novel_harness.api.chat as chat_mod
from novel_harness.agent.loop import AgentMessage, Role
from novel_harness.agent.rules import rule_message
from novel_harness.agent.store import ChatStore
from novel_harness.db import connect
from novel_harness.draft.provider import CompletionResult, ToolCall


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


def open_chat(client: TestClient, pid: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def seed(db: str, pid: str, chat_id: str, *messages: AgentMessage) -> None:
    """直接把一段历史落进去。

    **除了第一条断言，别的测试都不跑模型**：这份文件量的是那两条路由，而「一句话怎么
    变成一条规矩」已经在 `test_agent_tools.py` / `test_author_rules.py` 里各有一整节。
    """
    conn = connect(db)
    try:
        store = ChatStore(conn)
        stored = store.load(pid, chat_id)
        assert stored is not None
        store.append(pid, chat_id, base_count=stored.history_count, messages=messages)
    finally:
        conn.close()


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def rules_of(client: TestClient, pid: str, chat_id: str, chapter: int) -> dict[str, Any]:
    response = client.get(
        f"/api/projects/{pid}/chats/{chat_id}/rules", params={"chapter": chapter}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ══════════════════════════════════════════════════════════════════════════
# 一、这条线通不通：模型记下的那一条，浏览器里真的看得见
# ══════════════════════════════════════════════════════════════════════════


def test_a_rule_the_model_wrote_down_is_visible_to_the_author(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者说一句 → 模型叫一次「记下来」 → **它出现在那份清单上**。

    这是这份文件的第一条断言，理由同 `test_chat_api.py` 的第一条：这个仓库栽的从来
    不是「能力不对」，是**能力建完了、最后一厘米没接**。
    """
    pid = book["pid"]
    script = [
        CompletionResult(
            text="",
            model="deepseek-v4-flash",
            finish_reason="tool_calls",
            tool_calls=(
                ToolCall(
                    id="c1",
                    name="remember_rule",
                    arguments=json.dumps({"rule": "这一章别写打斗"}, ensure_ascii=False),
                ),
            ),
        ),
        CompletionResult(
            text="记下了，这一章我不写打斗。",
            model="deepseek-v4-flash",
            finish_reason="stop",
        ),
    ]
    calls: list[Any] = []

    def model(messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        calls.append(messages)
        return script[min(len(calls) - 1, len(script) - 1)]

    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: model)
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 3, "said": "这一章别写打斗。"},
    )
    assert turn.status_code == 200, turn.text

    listed = rules_of(client, pid, chat_id, 3)
    assert [entry["text"] for entry in listed["rules"]] == ["这一章别写打斗"]
    assert listed["chapter"] == 3
    assert listed["expired"] == 0

    # **切一章就没了**（ADR 0023 的安全方向：拿不准就放掉）。而那时它不是静默消失的：
    # 空清单带着一个数，界面据它说得出「定过、这会儿都不作数了」。
    next_chapter = rules_of(client, pid, chat_id, 4)
    assert next_chapter["rules"] == []
    assert next_chapter["expired"] == 1


def test_the_rule_says_how_far_it_reaches_and_when_it_lets_go(
    client: TestClient, book: dict[str, str]
) -> None:
    """一行一条：**那句话 + 它管到哪儿**。两句话都得说出它什么时候自己就没了。

    「它会自己过期」是作者不问就不会知道的那一半，而 ADR 0023 的安全方向正是
    **拿不准就放掉**——不说的话，规矩消失在他眼里就是系统忘事。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(
        book["db"],
        pid,
        chat_id,
        said("这一章别写打斗。"),
        rule_message("这一章别写打斗", chapter=3),
        said("我说真的，别写打斗。"),
        rule_message("这一章别写打斗", chapter=3),
        rule_message("冷一点", chapter=3),
    )

    by_text = {entry["text"]: entry["scope"] for entry in rules_of(client, pid, chat_id, 3)["rules"]}
    assert set(by_text) == {"这一章别写打斗", "冷一点"}
    # 说过两遍的升到章级；只说过一遍的活到作者下一次开口。
    assert "第 3 章" in by_text["这一章别写打斗"] and "下一章" in by_text["这一章别写打斗"]
    assert "这一轮" in by_text["冷一点"]
    # **措辞里一个研发术语都没有**：这条路的尽头是小说作者的屏幕。
    for scope in by_text.values():
        assert "chapter" not in scope and "_" not in scope


# ══════════════════════════════════════════════════════════════════════════
# 二、🔴 撤销按身份，不按下标
# ══════════════════════════════════════════════════════════════════════════


def said_three_times_then_listed(
    client: TestClient, book: dict[str, str]
) -> tuple[str, dict[str, Any]]:
    """同一条规矩被记过三遍，读端只摆出最后那一条。**作者点得到的只有它。**

    **三遍不是两遍**：只记过两遍时，按下标撤掉最后一条会让计数掉回 1，剩下那一遍当场
    退成批级、而作者早就又开口了——于是它自己就消失了，假实现看起来是对的。
    三遍才让「按钮按了、规矩还在」这个形态真的出现（同 `test_author_rules.py` 那个样本）。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid, house_style="冷峻、克制")
    history: list[AgentMessage] = []
    for _ in range(3):
        history.append(said("我说真的，这一章别写打斗。"))
        history.append(rule_message("别写打斗", chapter=3))
    seed(book["db"], pid, chat_id, *history)
    listed = rules_of(client, pid, chat_id, 3)
    assert len(listed["rules"]) == 1, "样本没去重 —— 下面那条量不出东西"
    assert "第 3 章" in listed["rules"][0]["scope"], "样本没升到章级 —— 那正是这条要防的形态"
    return chat_id, listed


def test_cancelling_takes_every_copy_not_only_the_one_the_author_clicked(
    client: TestClient, book: dict[str, str]
) -> None:
    """**按钮按下去之后，重取一次那份清单，它真的不在了。**

    只划掉作者点的那个下标的话，前面那一遍还在，**而它此刻已经是章级的**——
    于是按钮按了、规矩还在，且没有任何东西会报错。这条断言走的正是界面走的那条路
    （点 → DELETE → 重取），因为那是唯一能发现这件事的地方。
    """
    pid = book["pid"]
    chat_id, listed = said_three_times_then_listed(client, book)

    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200, gone.text
    assert gone.json()["revoked"] is True

    after = rules_of(client, pid, chat_id, 3)
    assert after["rules"] == []
    # **作者自己取消掉的不算「过期」**：他知道它没了，是他按的。两者在屏幕上说的是
    # 两句不同的话，混在一起会让他去找一条他刚刚亲手取消掉的规矩。
    assert after["expired"] == 0


def test_a_route_that_crossed_out_one_index_would_still_look_fine(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**探针**：把身份判据换成下标判据，上面那条必须当场不成立。

    换掉的是引擎里那个私有函数（同 `test_author_rules.py` 的那个探针），而这里量的是
    **HTTP 这一层看得见的后果**：路由照样 200、回执照样 `revoked: true`、
    库里照样多了一条撤销记录——**唯独那条规矩还在清单上，而且还是章级的**。
    这就是「按钮按了、规矩还在，且没有任何东西会报错」的完整形态。
    """
    pid = book["pid"]
    chat_id, listed = said_three_times_then_listed(client, book)

    def by_index_only(messages: Any) -> frozenset[int]:
        return frozenset(
            message.revokes_seq
            for message in messages
            if message.revokes_seq is not None and 0 <= message.revokes_seq < len(messages)
        )

    monkeypatch.setattr(rules_mod, "_revoked_indices", by_index_only)
    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200 and gone.json()["revoked"] is True

    after = rules_of(client, pid, chat_id, 3)
    assert after["rules"] != [], "探针没生效 —— 上面那条断言不证明任何事"
    assert "第 3 章" in after["rules"][0]["scope"]


def test_the_seq_the_screen_reports_is_the_one_the_engine_means(
    client: TestClient, book: dict[str, str]
) -> None:
    """界面回传的那个数是**历史下标**，不是会话表里那一列 `seq`（两者差一个前缀长度）。

    拿库里那一列去撤销，撞上的是另一条消息：轻则 422「那一条不是你定下的规矩」，
    重则划掉一条别的。所以这条样本**故意带一段稳定前缀**（`house_style`），
    让两个坐标错开——不错开的话这条断言是空转的。
    """
    chat_id, listed = said_three_times_then_listed(client, book)
    reported = listed["rules"][0]["seq"]

    conn = connect(book["db"])
    try:
        rows = conn.execute(
            "SELECT seq, section, content FROM chat_message WHERE session_id = ? ORDER BY seq",
            (chat_id,),
        ).fetchall()
    finally:
        conn.close()
    prefix = [r for r in rows if str(r["section"]) == "prefix"]
    assert len(prefix) >= 2, "样本的前缀太短，两个坐标没错开 —— 这条量不出东西"

    history = [r for r in rows if str(r["section"]) == "history"]
    assert history[reported]["content"] == listed["rules"][0]["text"]
    # 同一个下标在库那一列上指的是别的东西（或者根本越界）。
    assert reported != int(history[reported]["seq"])


# ══════════════════════════════════════════════════════════════════════════
# 三、拒绝的那几档，每一档都说人话
# ══════════════════════════════════════════════════════════════════════════


def test_cancelling_something_that_is_not_a_rule_is_refused_in_chinese(
    client: TestClient, book: dict[str, str]
) -> None:
    """界面拿着一份旧清单在点（另一个标签页刚取消过同一条）。**那句话是给作者的。**"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, said("这一章别写打斗。"), rule_message("别写打斗", chapter=3))

    missed = client.delete(f"/api/projects/{pid}/chats/{chat_id}/rules/9")
    assert missed.status_code == 422, missed.text
    assert "不在这段对话里" in missed.json()["detail"]["message"]

    not_a_rule = client.delete(f"/api/projects/{pid}/chats/{chat_id}/rules/0")
    assert not_a_rule.status_code == 422, not_a_rule.text
    assert "不是你定下的规矩" in not_a_rule.json()["detail"]["message"]


def test_cancelling_the_same_rule_twice_is_not_an_error(
    client: TestClient, book: dict[str, str]
) -> None:
    """双击、两个标签页各点一次都是常态。第二下是一次无害的重复，不是一次报错。"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, said("这一章别写打斗。"), rule_message("别写打斗", chapter=3))

    for _ in range(2):
        again = client.delete(f"/api/projects/{pid}/chats/{chat_id}/rules/1")
        assert again.status_code == 200, again.text
    assert rules_of(client, pid, chat_id, 3)["rules"] == []


def test_cancelling_while_a_turn_is_running_does_not_kill_that_turn(
    client: TestClient, book: dict[str, str]
) -> None:
    """正在跑的时候点 × ：**先拒掉，说一句准的话。**

    放它过去的话，这条追加会把那一轮的乐观并发闸撞红（`_TurnRun._save` 每长出一条落
    一次），作者已经付过钱的那一轮当场死掉——而他做的事是在一条规矩上点了个 ×，
    两件事之间没有任何看得出来的联系。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, said("这一章别写打斗。"), rule_message("别写打斗", chapter=3))

    chat_mod.LIVE.begin((pid, chat_id))
    busy = client.delete(f"/api/projects/{pid}/chats/{chat_id}/rules/1")
    assert busy.status_code == 409, busy.text
    assert "正在跑" in busy.json()["detail"]["message"]
    # 拒掉不等于丢掉：那一轮停下来之后它还在，作者再点一次就是了。
    chat_mod.LIVE.end((pid, chat_id))
    assert rules_of(client, pid, chat_id, 3)["rules"] != []


def test_a_missing_chapter_is_refused_instead_of_answering_zero(
    client: TestClient, book: dict[str, str]
) -> None:
    """**章号必填。** `live_rules` 在没有坐标时返回空元组（那一侧是对的：拿不准就放掉），
    可这条路由的出参会因此变成一块「这一章还没有规矩」的空面板——**一句它不知道真假的
    话**，而且它长得完全正常。"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, said("这一章别写打斗。"), rule_message("别写打斗", chapter=3))

    assert client.get(f"/api/projects/{pid}/chats/{chat_id}/rules").status_code == 422


def test_a_chat_that_is_not_there_is_a_404_not_an_empty_list(
    client: TestClient, book: dict[str, str]
) -> None:
    """删掉的那段对话，规矩不该读成「这一章还没定过」。"""
    pid = book["pid"]
    missing = client.get(
        f"/api/projects/{pid}/chats/chat_session:nope/rules", params={"chapter": 1}
    )
    assert missing.status_code == 404, missing.text


# ══════════════════════════════════════════════════════════════════════════
# 四、零带着理由（§10 约束 8）
# ══════════════════════════════════════════════════════════════════════════


def test_an_empty_list_says_which_kind_of_empty_it_is(
    client: TestClient, book: dict[str, str]
) -> None:
    """两种空在出参上分得开：**从没定过** vs **定过、这会儿都不作数了**。

    分不开的后果是屏幕上一句它不知道真假的话。这个仓库为「静默的零」栽过五次，
    每一次的形态都一样：一块看起来完全正常的空面板。
    """
    pid = book["pid"]
    quiet = open_chat(client, pid)
    assert rules_of(client, pid, quiet, 3) == {"chapter": 3, "rules": [], "expired": 0}

    talked = open_chat(client, pid)
    seed(
        book["db"],
        pid,
        talked,
        said("这一章别写打斗。"),
        rule_message("别写打斗", chapter=3),
        rule_message("冷一点", chapter=3),
    )
    # 作者翻到第 7 章：那两条都不属于这一章了，而**它们仍然存在过**。
    moved_on = rules_of(client, pid, talked, 7)
    assert moved_on["rules"] == []
    assert moved_on["expired"] == 2
