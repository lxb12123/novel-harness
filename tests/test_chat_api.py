"""写作助手的 HTTP 壳 —— **`agent/` 这一层第一次真的被调用**。

这个仓库栽过四次「能力建好了、最后一厘米没接」（`docs_dev/2026-08-06-…`），
所以这份文件的第一条断言不是某个字段的形状，是**这条线通不通**：
浏览器发一句话 → 工具真的跑了 → 账真的落在日志页上 → 对话真的存下来了 → 重启还在。

量五件事，每一件都是「不做它就会静默错掉」的那一种：

1. **resume**：进程死在模型调用和派发之间之后，下一轮先把缺的补跑（ADR 0019）。
2. **记账**：验收不是「`model_call` 里有行」，是 **`GET /activity` 里看得见**。
3. **打断**：另一个请求按下的「停」够得着正在跑的那一轮。
4. **上屏的是投影不是原文**：工具返回里躺着 `NodeRef` 的裸 id，那种东西一旦被前端
   原样渲染就是屏幕上的研发术语（`frontend/src/test/screenGuard.ts` 的第三张网）。
5. **边界三**：这两张表里的东西没有一条路径能变成「第 N 章是什么」的答案。
"""

from __future__ import annotations

import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
from novel_harness.agent.loop import AgentMessage, ModelCallReceipt, Role, StopReason
from novel_harness.agent.store import ChatStore
from novel_harness.db import connect
from novel_harness.draft.provider import CompletionResult, ToolCall
from novel_harness.extract.call_audit import record_call

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"


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


class Scripted:
    """按剧本一句一句回答（同 `tests/test_agent_loop.py` 里那个），**剧本用完重复最后一条**。"""

    def __init__(self, *script: CompletionResult, before: Any = None) -> None:
        self.script = list(script)
        self.calls: list[Any] = []
        self.before = before

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls.append(list(messages))
        if self.before is not None:
            self.before(cancel)
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


def says(text: str, **kwargs: Any) -> CompletionResult:
    return CompletionResult(
        text=text, model="deepseek-v4-flash", finish_reason="stop", **kwargs
    )


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


def use(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: model)


def open_chat(client: TestClient, pid: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ══════════════════════════════════════════════════════════════════════════
# 一、这条线通不通
# ══════════════════════════════════════════════════════════════════════════


def test_one_turn_goes_all_the_way_through_and_stays_in_the_book(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一句话进去，工具真的跑了，对话真的落库了，**再读一次还在**。"""
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("scene_constraints", json.dumps({"chapter": 2}))),
            says("第 2 章这一场，血脉那条先别说破。"),
        ),
    )
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 2, "said": "第 2 章能说破血脉的事吗？"},
    )
    assert turn.status_code == 200, turn.text
    body = turn.json()
    assert body["reason"] == StopReason.DONE.value
    assert body["reply"].startswith("第 2 章")
    assert body["lookups"] == 1, "工具没真的跑 —— 这就是「最后一厘米没接」的形态"
    assert body["steps"] == 2

    detail = client.get(f"/api/projects/{pid}/chats/{chat_id}").json()
    assert [m["speaker"] for m in detail["messages"]] == ["author", "assistant"]
    assert detail["session"]["message_count"] == 4  # 作者 + 助手(带调用) + 工具返回 + 助手
    # 标题由作者第一句话来（侧栏上一排「新的对话」找不出三个月前那一段）。
    assert detail["session"]["title"].startswith("第 2 章能说破")


def test_the_first_turn_of_a_conversation_needs_the_author_to_say_something(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = book["pid"]
    use(monkeypatch, Scripted(says("嗯")))
    chat_id = open_chat(client, pid)
    empty = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2})
    assert empty.status_code == 422
    assert "先说一句" in empty.text


def test_the_chapter_is_required_because_none_means_do_not_filter(
    client: TestClient, book: dict[str, str], configured: None
) -> None:
    """**`working_chapter` 不是可选项。**

    `project()` 在 `chapter is None` 时不过滤——那不是安全默认值，是「没接线」默认值，
    而它错的方向是 fail-open 的那一侧（第 90 章的禁说清单是第 40 章那份的**子集**，
    留着它模型就以为只有两条不能说，ADR 0019 边界二/五）。所以壳里没有一个默认值
    能让这个参数缺席。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    missing = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json={"said": "写"})
    assert missing.status_code == 422
    zero = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 0, "said": "写"}
    )
    assert zero.status_code == 422


# ══════════════════════════════════════════════════════════════════════════
# 二、resume：看尾巴、补跑缺的、继续
# ══════════════════════════════════════════════════════════════════════════


def test_a_turn_that_died_between_the_model_and_the_dispatch_is_picked_back_up(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这就是 ADR 0019 说的 resume**，而它不需要图：

    执行态只有「一串 message + 哪几个 `tool_call` 还缺 `tool_result`」。
    这里直接往库里种一段「模型要了两个工具、只回来一个」的历史（进程死在中间的形状），
    然后**不带新话**跑一轮：缺的那个必须被补跑，而作者一个字都不用重说。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    conn = connect(book["db"])
    ChatStore(conn).append(
        pid,
        chat_id,
        base_count=0,
        messages=[
            AgentMessage(role=Role.USER, content="第 2 章有谁在？"),
            AgentMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(
                    ToolCall(id="a", name="book_index", arguments="{}"),
                    ToolCall(
                        id="b", name="scene_constraints", arguments=json.dumps({"chapter": 2})
                    ),
                ),
            ),
            AgentMessage(role=Role.TOOL, content="（目录）", tool_call_id="a"),
        ],
    )
    conn.close()

    before = client.get(f"/api/projects/{pid}/chats/{chat_id}").json()
    assert before["session"]["pending_lookups"] == 1, "断在半路这件事读不出来 = resume 无从谈起"

    use(monkeypatch, Scripted(says("查完了，第 2 章那条先别说破。")))
    turn = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2})
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 1, "缺的那一步没被补跑"
    assert turn.json()["reason"] == StopReason.DONE.value

    after = client.get(f"/api/projects/{pid}/chats/{chat_id}").json()
    assert after["session"]["pending_lookups"] == 0


def test_more_than_one_conversation_can_be_open_at_once(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者明确要的：**多个会话窗口，每个各自 resume。** 两段互不串。"""
    pid = book["pid"]
    use(monkeypatch, Scripted(says("知道了")))
    first = open_chat(client, pid, title="第 2 章")
    second = open_chat(client, pid, title="人物线")
    client.post(f"/api/projects/{pid}/chats/{first}/turn", json={"chapter": 2, "said": "甲"})
    client.post(f"/api/projects/{pid}/chats/{second}/turn", json={"chapter": 3, "said": "乙"})

    listed = client.get(f"/api/projects/{pid}/chats").json()
    assert {row["title"] for row in listed} == {"第 2 章", "人物线"}
    assert all(row["message_count"] == 2 for row in listed)
    assert [m["text"] for m in client.get(f"/api/projects/{pid}/chats/{first}").json()["messages"]] == [
        "甲",
        "知道了",
    ]


def test_deleting_one_conversation_leaves_the_other_alone(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好")))
    keep = open_chat(client, pid, title="留着")
    drop = open_chat(client, pid, title="删掉")
    client.post(f"/api/projects/{pid}/chats/{keep}/turn", json={"chapter": 1, "said": "甲"})

    gone = client.delete(f"/api/projects/{pid}/chats/{drop}")
    assert gone.status_code == 200 and gone.json() == {"chat_id": drop, "deleted": True}
    assert client.get(f"/api/projects/{pid}/chats/{drop}").status_code == 404
    assert client.get(f"/api/projects/{pid}/chats/{keep}").status_code == 200
    assert client.delete(f"/api/projects/{pid}/chats/{drop}").status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# 三、记账：**验收是日志页看得见，不是表里有行**
# ══════════════════════════════════════════════════════════════════════════


def test_every_paid_round_shows_up_on_the_activity_page_in_chinese(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """板子上记着一个同款的洞：`/draft` 一行 `model_call` 都不写，于是日志页显示的是
    真实花销的一小部分、**看起来却像全部**。写作助手是第二个会大量花钱的地方，
    所以这条断言走到最后一格：`GET /activity` 里那一行写的是「写作助手」。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("book_index", "{}")),
            says("看完了", prompt_tokens=1_200, completion_tokens=90),
        ),
    )
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "看看目录"}
    )
    assert turn.status_code == 200, turn.text

    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 20}).json()
    ours = [
        e
        for e in page["entries"]
        if e["title_code"] == "call_entry_title" and e["title_params"] == {"capability": "agent"}
    ]
    assert len(ours) == 2, f"两次模型调用只记了 {len(ours)} 笔"
    assert ours[0]["subtitle_code"] == "call_subtitle"
    assert ours[0]["subtitle_params"]["model"] == "deepseek-v4-flash"
    assert ours[0]["subtitle_params"]["tokens_in"] == 1200
    assert ours[0]["subtitle_params"]["tokens_out"] == 90
    # 底栏那一格也得看得见（同一张表的另一个读端）。
    runs = client.get(f"/api/projects/{pid}/runs").json()
    assert runs["totals"]["calls"] >= 2


def test_the_receipt_is_a_move_not_a_translation() -> None:
    """`ModelCallReceipt` 的字段是**照着 `record_call` 的签名长的**，所以壳里那一段
    必须是一次平移。翻译的地方就是能悄悄漏字段的地方，而漏掉的那个字段会让日志页
    少算一笔钱。

    判据是字段名对参数名，**不是读源码里那几行**——那样一次改名就把守卫骗过去了。
    """
    params = set(inspect.signature(record_call).parameters)
    missing = [name for name in ModelCallReceipt.model_fields if name not in params]
    assert not missing, f"账单原料上这几个字段在 `record_call` 里没有对应的入参：{missing}"


def test_a_turn_that_never_reached_the_model_writes_no_bill(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**没花的钱不许出现在账上。** 端点连不上时没有 `CompletionResult`，
    也就没有 token 数——这一层不许替它编一个（同 `cost` 那一列有意留空）。"""
    from novel_harness.draft.provider import ProviderError

    pid = book["pid"]

    def dead(messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        raise ProviderError("connection refused: https://api.deepseek.com")

    use(monkeypatch, dead)
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "写点什么"}
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["reason"] == StopReason.MODEL_UNREACHABLE.value
    assert "联系不上写作模型" in turn.json()["message"]
    # **维护者那条诊断一个字都不出去**（同 `ExtractionRunError.message`）：
    # 里面有端点地址和模型名，而作者能做的动作是去顶栏改设置。
    assert "connection refused" not in turn.text
    assert "api.deepseek.com" not in turn.text

    page = client.get(f"/api/projects/{pid}/activity").json()
    assert not [
        e
        for e in page["entries"]
        if e["title_code"] == "call_entry_title" and e["title_params"] == {"capability": "agent"}
    ]


# ══════════════════════════════════════════════════════════════════════════
# 四、停
# ══════════════════════════════════════════════════════════════════════════


def test_the_stop_button_reaches_a_turn_that_is_already_running(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「停」是**另一个请求**，它得够得着正在跑的那一轮手里那个信号。

    这儿把模型卡在第一次调用里，从主线程按停，再放行——那一轮必须以
    「按你的意思停下了」收尾，而且**那几个工具一个都不许再跑**（按了停之后不干活）。

    **模型这一次要的是工具，不是说话**，因为那才是「停」真正拦得住的形态：
    模型如果只是说了一句就收手，这一轮本来就结束了，那时报 `done` 是准确的
    （停没有让任何事情少发生），报「按你的意思停下了」反而是替它认领了一件没做的事。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            lambda: client.post(f"{url}/turn", json={"chapter": 2, "said": "写一段"})
        )
        assert entered.wait(timeout=5), "这一轮没跑起来"
        stopped = client.post(f"{url}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["stopped"] is True
        assert stopped.json()["message"] == "按你的意思停下了。已经查到的东西留着。"
        release.set()
        turn = running.result(timeout=10)

    assert turn.status_code == 200, turn.text
    assert turn.json()["reason"] == StopReason.AUTHOR_STOPPED.value
    assert turn.json()["lookups"] == 0, "按了停之后还在干活"
    # 已经查到的东西留着，而且那一轮的账照记（钱已经花掉了，停不会退回来）。
    page = client.get(f"/api/projects/{pid}/activity").json()
    assert [
        e
        for e in page["entries"]
        if e["title_code"] == "call_entry_title" and e["title_params"] == {"capability": "agent"}
    ]


def test_stopping_a_conversation_that_is_not_running_is_not_a_failure(
    client: TestClient, book: dict[str, str], configured: None
) -> None:
    """`stopped=false` 不是错——那一刻它本来就没在跑。**别让界面把它说成失败。**"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    idle = client.post(f"/api/projects/{pid}/chats/{chat_id}/stop")
    assert idle.status_code == 200
    assert idle.json()["stopped"] is False
    assert "没在跑" in idle.json()["message"]
    assert client.post(f"/api/projects/{pid}/chats/chat_session:nope/stop").status_code == 404


def test_a_stop_meant_for_the_previous_turn_does_not_kill_the_new_one(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**过期的「停」要被忽略。**

    `_Running.begin` 保证同一段对话不会同时有两轮，所以大部分场景天然安全。
    **但这个序列会出事**：

        作者按停 → 请求在路上 → 上一轮自己跑完了 → 作者又发一句
        → 新一轮开始 → 停止请求到达 → **杀掉新的那一轮**

    作者看到的是「我刚发出去的那句话，它自己停了」。修法是让「停」报出它想停的是
    哪一轮，比对不上就不动——而**这一档不许说成失败**（同上一条：他按的那一下是对的）。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            lambda: client.post(f"{url}/turn", json={"chapter": 2, "said": "新的一轮", "run_id": "b"})
        )
        assert entered.wait(timeout=5), "这一轮没跑起来"
        # 作者按的是**上一轮**那颗停（`run_id="a"`），而这会儿跑的是 `"b"`。
        stale = client.post(f"{url}/stop", json={"run_id": "a"})
        assert stale.status_code == 200, stale.text
        assert stale.json()["stopped"] is False
        assert "上一轮" in stale.json()["message"]
        assert "失败" not in stale.json()["message"]
        release.set()
        turn = running.result(timeout=10)

    # **新的那一轮一点没被动过**：它照旧跑完、照旧叫了那个工具。
    assert turn.status_code == 200, turn.text
    assert turn.json()["reason"] == StopReason.DONE.value
    assert turn.json()["lookups"] == 1


def test_a_stop_that_names_the_running_turn_still_lands(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**报对了标识的那一下照旧生效**（上一条的自守卫）。

    没有它，上面那句 `stopped is False` 可能只是因为比对把所有「停」都挡掉了——
    一颗永远不生效的停止按钮同样能让那条断言变绿。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            lambda: client.post(f"{url}/turn", json={"chapter": 2, "said": "这一轮", "run_id": "b"})
        )
        assert entered.wait(timeout=5), "这一轮没跑起来"
        stopped = client.post(f"{url}/stop", json={"run_id": "b"})
        assert stopped.json()["stopped"] is True
        release.set()
        turn = running.result(timeout=10)

    assert turn.json()["reason"] == StopReason.AUTHOR_STOPPED.value


def test_a_client_that_reports_no_turn_id_keeps_the_old_behaviour(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`curl` 和老界面不报标识 ⇒ **不比对**，行为和 2026-08-12 之前一模一样。

    这不是留后门：报不出标识的那一边**本来就没有**「上一轮」和「这一轮」的概念
    （它一次只按一下），而让 `POST /stop` 变成必须带 body 会把一条既有的路当场打断。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            lambda: client.post(f"{url}/turn", json={"chapter": 2, "said": "写一段", "run_id": "b"})
        )
        assert entered.wait(timeout=5), "这一轮没跑起来"
        assert client.post(f"{url}/stop").json()["stopped"] is True
        release.set()
        turn = running.result(timeout=10)

    assert turn.json()["reason"] == StopReason.AUTHOR_STOPPED.value


def test_deleting_a_conversation_that_is_running_says_so_instead_of_confusing_him(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删掉正在跑的那一段：**先说「它正在跑」，而不是让那一轮跑完之后吐一句
    「在别的窗口里刚往前走了一步」**——后者和作者刚做的事完全对不上。"""
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(lambda: client.post(f"{url}/turn", json={"chapter": 2, "said": "甲"}))
        assert entered.wait(timeout=5)
        refused = client.delete(url)
        release.set()
        assert running.result(timeout=10).status_code == 200

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error"] == "chat_busy"
    assert detail["params"]["action"] == "delete"
    assert client.delete(url).status_code == 200


def test_a_second_turn_on_a_running_conversation_is_refused_not_interleaved(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个标签页对着同一段会话各按一次发送：**后到的拿到拒绝**。

    不拦的话两轮的消息会交织成一段谁也读不懂的历史，**而那不会报错**。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def blocking(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(says("好"), before=blocking))
    chat_id = open_chat(client, pid)
    url = f"/api/projects/{pid}/chats/{chat_id}/turn"

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(lambda: client.post(url, json={"chapter": 2, "said": "甲"}))
        assert entered.wait(timeout=5)
        second = client.post(url, json={"chapter": 2, "said": "乙"})
        release.set()
        first = running.result(timeout=10)

    assert second.status_code == 409, second.text
    assert second.json()["detail"]["error"] == "chat_busy"
    # **先按发送的那一个必须是通过的那一个。** 这条曾经红过：占位放在追加之后，
    # 于是第二个窗口先把「乙」写进历史，第一轮跑完想追加自己的产物时撞上乐观并发闸——
    # 后按的那个反而赢了，而先按的那个作者什么都没做错。
    assert first.status_code == 200, first.text
    assert [m["text"] for m in client.get(f"/api/projects/{pid}/chats/{chat_id}").json()["messages"]] == [
        "甲",
        "好",
    ]


# ══════════════════════════════════════════════════════════════════════════
# 五、上屏的是投影，不是原文
# ══════════════════════════════════════════════════════════════════════════


def test_no_tool_payload_ever_reaches_the_browser(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具返回是内部模型的 `model_dump_json()`，里面躺着 `NodeRef` 的**裸 id**
    （`secret:…:01J…`）。它一旦被前端原样渲染就是屏幕上的研发术语——而**收窄的最强
    形态是根本没发出去**（同 `activity.py` 那次把 `params_json` 从 SELECT 里删掉）。

    少掉的那部分不是被藏起来：查了几次有一个数（`lookups`），只是查到了什么不上屏。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("book_index", "{}"), ("scene_constraints", json.dumps({"chapter": 2}))),
            says("看完了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "查一下"}
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 2

    entity_id = re.compile(
        r"(?:character|location|faction|secret|foreshadow|object|statedim|chapter|edge|event)"
        r":[0-9a-f]{8}:[0-9A-HJKMNP-TV-Z]{26}"
    )
    for text in (turn.text, client.get(f"/api/projects/{pid}/chats/{chat_id}").text):
        # 会话自己的 id 当然在出参里（前端要拿它寻址）；**图上那些实体的 id 不许在**。
        assert not entity_id.search(text), "工具返回里的裸 id 上屏了"
        assert "book_index" not in text and "scene_constraints" not in text
        assert "must_not_reveal" not in text
        assert "血脉秘密" not in text


def test_the_context_receipt_says_what_it_trimmed(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """静默截断读起来像「全给了」。**零必须带着理由一起出现**（§10 约束 8）。"""
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好")))
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "问一句"}
    )
    context = turn.json()["context"]
    assert set(context) == {
        "off_chapter",
        "stale_lookups",
        "trimmed_results",
        "dropped_lookups",
        "dropped_reasoning",
        "lost_lookups",
        "full",
        "compressed_blocks",
    }
    assert context["full"] is False


# ══════════════════════════════════════════════════════════════════════════
# 六、边界三：这两张表答不了「第 N 章是什么」
# ══════════════════════════════════════════════════════════════════════════


def test_only_the_chat_layer_ever_touches_the_chat_tables() -> None:
    """**边界三可机器验证的那一半。**

    ADR 0019 边界三怕的是「哪一份正文是真的」有第二个答案。真正决定这件事的不是
    「对话里有没有出现过正文」——`chapter_text` 工具按定义就要把一章正文给模型看，
    而那条返回必须原样存下来，否则 resume 之后模型看到的是另一段历史。
    决定它的是**有没有第二条读路径**：只要没有任何代码拿 `chat_message` 回答
    「第 N 章是什么」，磁盘就仍然是唯一答案（ADR 0007）。

    所以判据是「谁在碰这两张表」（同 `test_arch_guard.py` 的 `GRAPH_TABLES` 那条），
    白名单只有两个文件：写它的那一层和数它的那个壳。
    """
    # `ids.py` 只登记这两个 ID 类型（`EntityType` 的规矩是「每个值对应一张有主键的表」），
    # 它一行 SQL 都没有。
    allowed = {"agent/store.py", "api/chat.py", "ids.py"}
    offenders = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if re.search(r"\bchat_(?:message|session)\b", path.read_text(encoding="utf-8"))
        and path.relative_to(SRC).as_posix() not in allowed
    )
    assert not offenders, (
        f"这些文件在碰会话表：{offenders}\n"
        "正文的真相源在磁盘上（ADR 0007）；会话表只是「说过什么」的记录，"
        "任何一条从它读正文的路径都是「哪份正文是真的」的第二个答案。"
    )


def test_reading_a_chapter_in_a_conversation_does_not_add_a_version(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """助手读了一整章正文之后：**版本抽屉一个字没变，磁盘上那份也没变。**

    这条钉的是那个最容易顺手做的错事——把模型看过/写过的正文塞进 `chapter_snapshot`
    「省得再存一遍」。那一刻它就出现在作者的版本列表里，「哪一份是真的」当场有了
    第二个答案，而且是他在界面上看得见的那一个。
    """
    pid = book["pid"]
    before = client.get(f"/api/projects/{pid}/chapters/1/history").json()
    disk = client.get(f"/api/projects/{pid}/chapters/1/text").json()["markdown"]

    use(
        monkeypatch,
        Scripted(wants(("chapter_text", json.dumps({"chapter": 1}))), says("读完了。")),
    )
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 1, "said": "读一下第 1 章"}
    )
    assert turn.status_code == 200 and turn.json()["lookups"] == 1

    assert client.get(f"/api/projects/{pid}/chapters/1/history").json() == before
    assert client.get(f"/api/projects/{pid}/chapters/1/text").json()["markdown"] == disk
    # 而那一章的正文**确实**进了对话历史（模型看过它），这是 resume 的代价，也是它的前提。
    conn = connect(book["db"])
    stored = ChatStore(conn).load(pid, chat_id)
    conn.close()
    assert stored is not None
    assert any("萧决在青云城主府" in m.content for m in stored.conversation.messages)


# ══════════════════════════════════════════════════════════════════════════
# 七、没配模型
# ══════════════════════════════════════════════════════════════════════════


def test_an_unconfigured_model_is_one_sentence_the_author_can_act_on(
    client: TestClient, book: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配钥匙的作者点一下，拿到的必须是「去顶栏 ⚙ 填三个框」，不是一段英文。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("NH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NH_LLM_MODEL", raising=False)
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 2, "said": "写"}
    )
    assert turn.status_code == 422
    assert "AI 设置" in turn.json()["detail"]
    # **开会话不要模型**：作者可以先开一段、再去配（开一段不花钱）。
    assert client.post(f"/api/projects/{pid}/chats", json={}).status_code == 201
