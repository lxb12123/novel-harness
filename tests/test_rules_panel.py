"""对抗性验证：**规矩面板**那两条路由（[ADR 0023](../docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。

引擎那一层已经被 `tests/test_author_rules.py` 逐条钉过，路由那一层被
`tests/test_chat_rules_api.py` 钉过。**这份文件只量那两份都没量的四件事**，
每一件都是「不做它这个按钮就是假的」的那一种：

1. **撤销按身份，换一个样本形状** —— 那两份用的都是「作者说三遍」。真实里更常见的是
   **作者说两遍、模型记三遍**（同一批里记两次：`TurnLimits.repeat_limit` 允许，
   resume 补跑一条 `pending` 的调用也会），以及**同一批里记两遍**（作者只开过一次口）。
   两种形状都能让按下标撤的错实现现形，而现有样本一种都没覆盖。
2. **撤销之后它真的不再进 prompt —— 走完整条链。** 引擎那条断言拿的是内存里拼出来的
   `Conversation`；这儿走的是「浏览器点 × → DELETE → 落库 → 下一轮真的读回来投影」。
   中间多了 SQLite 那一段（`revokes_seq` 得存得住）和 `_TurnRun` 那一段。
3. **翻一章之后它在发出去那份里也没了**（fail-open，方向和 `must_not_reveal` 相反）。
4. 🔴 **「定过、这会儿都不作数了」那句话的理由** —— 出参**分不出**两种过期
   （翻页 / 作者又开口），而屏幕上写死了其中一种，且写死的**不是**默认那一种。

**探针一律换掉引擎里那个私有函数**（同 `test_author_rules.py`），量的是 HTTP 这一层
看得见的后果：路由照样 200、回执照样 `revoked: true`，唯独那条规矩还在。
"""

from __future__ import annotations

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
from novel_harness.draft.provider import CompletionResult

# 规矩本身用的字**故意和作者说的话不一样**：作者说过的话永远留在 prompt 里
# （剪枝的最后一档就是不碰它），拿同一串字去断言「它不在发出去那份里」是空转的。
RULE = "少写点武戏"


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


def said(text: str) -> AgentMessage:
    return AgentMessage(role=Role.USER, content=text)


def open_chat(client: TestClient, pid: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def seed(db: str, pid: str, chat_id: str, *messages: AgentMessage) -> None:
    conn = connect(db)
    try:
        store = ChatStore(conn)
        stored = store.load(pid, chat_id)
        assert stored is not None
        store.append(pid, chat_id, base_count=stored.history_count, messages=messages)
    finally:
        conn.close()


def rules_of(client: TestClient, pid: str, chat_id: str, chapter: int) -> dict[str, Any]:
    response = client.get(
        f"/api/projects/{pid}/chats/{chat_id}/rules", params={"chapter": chapter}
    )
    assert response.status_code == 200, response.text
    return response.json()


def by_index_only(messages: Any) -> frozenset[int]:
    """**最省事的那一版**：作者点了哪个下标就划掉哪个下标。

    它读起来完全对（「他点的就是那一条」），只在同一条被记过不止一遍时才错
    ——也就是只在真实使用里错，而且**错的时候没有任何东西会报错**。
    """
    return frozenset(
        message.revokes_seq
        for message in messages
        if message.revokes_seq is not None and 0 <= message.revokes_seq < len(messages)
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、🔴 撤销按身份 —— 两种现有样本都没覆盖的形状
# ══════════════════════════════════════════════════════════════════════════


def said_twice_written_down_three_times() -> list[AgentMessage]:
    """**作者说两遍，模型记三遍**（第二批里记了两次）。规矩因此是章级的。

    这不是编出来的形状：`TurnLimits.repeat_limit` 允许同一个调用在一轮里出现三次，
    而 resume 补跑一条 `pending` 的调用就会把同一条规矩再记一遍
    （`rules.REPEAT_TO_WIDEN` 的 docstring 自己写着这条）。

    **它和「作者说三遍」那个样本量的不是一回事**：`heard` 数的是作者回合，这儿是 2；
    按下标划掉最后那一条之后，剩下的两条仍然横跨两个回合 ⇒ **还是章级的**，
    于是「按钮按了、规矩还在」原样发生。
    """
    return [
        said("这一章别写打斗。"),
        rule_message(RULE, chapter=3),
        said("我说真的，别写打斗。"),
        rule_message(RULE, chapter=3),
        rule_message(RULE, chapter=3),
    ]


def written_down_twice_in_one_breath() -> list[AgentMessage]:
    """**最小的那个形状**：作者只开过一次口，模型在同一批里记了两遍。

    规矩是**批级**的（`heard` = 1），而它照样能让按下标撤的错实现现形：
    划掉最后那一条之后，前面那一遍还排在作者最后一次开口的**后面**，一条都没死。

    留着它是因为「章级 = 这个 bug 的前提」是个很自然的误读——
    真正的前提只有一条：**同一条被记过不止一遍**。
    """
    return [
        said("这三版太煽情了。"),
        rule_message(RULE, chapter=3),
        rule_message(RULE, chapter=3),
    ]


SHAPES = [
    pytest.param(said_twice_written_down_three_times, True, id="说两遍记三遍-章级"),
    pytest.param(written_down_twice_in_one_breath, False, id="一口气记两遍-批级"),
]


def one_rule_on_the_list(
    client: TestClient, book: dict[str, str], history: list[AgentMessage]
) -> tuple[str, dict[str, Any]]:
    """摆到作者面前：读端去重之后只剩一条，**他点得到的只有它**。"""
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, *history)
    listed = rules_of(client, pid, chat_id, 3)
    assert len(listed["rules"]) == 1, "样本没去重 —— 下面那条量不出东西"
    return chat_id, listed


@pytest.mark.parametrize(("shape", "chapter_wide"), SHAPES)
def test_one_click_takes_every_copy_of_it(
    client: TestClient,
    book: dict[str, str],
    shape: Any,
    chapter_wide: bool,
) -> None:
    """点一次 × ，**每一份都没了**（重取回来它真的不在了）。

    重取是唯一的判据：回执上那个 `revoked: true` 恒为真，撤不掉的那几种在它之前就
    4xx 了——**拿它当证据的界面看不见这个 bug**。
    """
    pid = book["pid"]
    chat_id, listed = one_rule_on_the_list(client, book, shape())
    assert ("第 3 章" in listed["rules"][0]["scope"]) is chapter_wide, "样本的作用域跑偏了"

    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200, gone.text

    after = rules_of(client, pid, chat_id, 3)
    assert after["rules"] == []
    # 作者自己取消掉的**不算过期**：他知道它没了，是他按的。混进去的话，屏幕会请他
    # 去找一条他刚刚亲手取消掉的规矩。
    assert after["expired"] == 0


@pytest.mark.parametrize(("shape", "chapter_wide"), SHAPES)
def test_a_route_that_crossed_out_one_index_would_still_answer_200(
    client: TestClient,
    book: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    shape: Any,
    chapter_wide: bool,
) -> None:
    """**探针**：换成按下标撤，上面那条必须当场不成立。

    这就是那种坏法的完整形态——路由 200、回执 `revoked: true`、库里真多了一条撤销
    记录，**唯独规矩还在清单上，而且作用域一点没变**。屏幕上什么都不会红。
    """
    pid = book["pid"]
    chat_id, listed = one_rule_on_the_list(client, book, shape())

    monkeypatch.setattr(rules_mod, "_revoked_indices", by_index_only)
    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200 and gone.json()["revoked"] is True

    after = rules_of(client, pid, chat_id, 3)
    assert after["rules"] != [], "探针没生效 —— 上面那条断言不证明任何事"
    assert ("第 3 章" in after["rules"][0]["scope"]) is chapter_wide


# ══════════════════════════════════════════════════════════════════════════
# 二、🔴 撤销之后它真的不再进 prompt —— 走完整条链
# ══════════════════════════════════════════════════════════════════════════


def a_turn_that_only_talks(sent: list[Any]) -> Any:
    """一轮：什么工具都不叫，说一句话收手。**它唯一的作用是把发出去那份留下来。**"""

    def model(messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        sent.append(messages)
        return CompletionResult(
            text="好。", model="deepseek-v4-flash", finish_reason="stop"
        )

    return model


def rule_reached_the_model(payload: Any) -> bool:
    """那条规矩在**发出去那份**里吗。

    只看 SYSTEM 那一档：作者说过的话永不被剪掉，拿整份 payload 去搜会把「作者自己
    提过一嘴」也算成命中。
    """
    return any(
        message.get("role") == "system" and RULE in (message.get("content") or "")
        for message in payload
    )


def run_a_turn(
    client: TestClient, pid: str, chat_id: str, chapter: int, monkeypatch: Any
) -> list[Any]:
    sent: list[Any] = []
    monkeypatch.setattr(
        chat_mod, "build_agent_model", lambda config, plan, m=a_turn_that_only_talks(sent): m
    )
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": chapter, "said": "接着说说这一章怎么开头。"},
    )
    assert turn.status_code == 200, turn.text
    assert sent, "这一轮根本没调模型 —— 下面那条量不出东西"
    return sent[-1]


def test_after_the_author_clicks_it_away_the_engine_stops_sending_it(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """点 × 之后**下一轮真的不再把它发出去**（不是只从清单上消失）。

    读端和投影是同源的（都走 `surviving_rule_indices`），但这条链上还有两段引擎测试
    够不着的：`revokes_seq` 得在 SQLite 里存得住、`_TurnRun` 得把整段历史读回来
    （读回来的是一截尾巴的话，那个下标就指到别的消息上了）。

    **两头都断言**：撤销之前它在里头（不然下面那句是废话），撤销之后它不在。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, *said_twice_written_down_three_times())

    before = run_a_turn(client, pid, chat_id, 3, monkeypatch)
    assert rule_reached_the_model(before), "撤销之前它就没发出去 —— 这条量不出东西"

    listed = rules_of(client, pid, chat_id, 3)
    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200, gone.text

    after = run_a_turn(client, pid, chat_id, 3, monkeypatch)
    assert not rule_reached_the_model(after)
    # 撤销记录本身也不出去：它的意义全在结构槽上，正文是空的——发一条空的 system
    # 消息只是白花钱，而模型该看到的结果是「那条规矩从来没被说过」。
    assert not any(
        message.get("role") == "system" and not (message.get("content") or "").strip()
        for message in after
    )


def test_a_cancel_that_never_reached_the_prompt_would_turn_this_net_red(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**探针**：撤销只划掉那个下标时，那条规矩**照样发给模型**。

    这一档比清单上那一档更贵：作者已经明确说过不要它了，而它每一轮都在花他的钱
    继续管着他的稿子，**且他刚刚亲手确认过它不在清单上**。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, *said_twice_written_down_three_times())

    listed = rules_of(client, pid, chat_id, 3)
    gone = client.delete(
        f"/api/projects/{pid}/chats/{chat_id}/rules/{listed['rules'][0]['seq']}"
    )
    assert gone.status_code == 200, gone.text

    monkeypatch.setattr(rules_mod, "_revoked_indices", by_index_only)
    after = run_a_turn(client, pid, chat_id, 3, monkeypatch)
    assert rule_reached_the_model(after), "探针没生效 —— 上面那条断言不证明任何事"


# ══════════════════════════════════════════════════════════════════════════
# 三、翻一章：清单上没了，**发出去那份里也没了**（fail-open）
# ══════════════════════════════════════════════════════════════════════════


def test_turning_the_page_takes_it_out_of_the_prompt_not_just_off_the_list(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作者翻到下一章：面板上没了，**下一轮也不再把它发出去**。

    这一条的方向和 `must_not_reveal` 是**反**的（ADR 0023 写死了）：拿不准就放掉。
    只把它从清单上摘掉、还继续发给模型的话，症状是**第 200 章写不出打戏，而作者不知道
    为什么**——他看得见的那份清单上一条规矩都没有。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(book["db"], pid, chat_id, *said_twice_written_down_three_times())

    here = run_a_turn(client, pid, chat_id, 3, monkeypatch)
    assert rule_reached_the_model(here), "在它自己那一章都没发出去 —— 这条量不出东西"

    assert rules_of(client, pid, chat_id, 4)["rules"] == []
    there = run_a_turn(client, pid, chat_id, 4, monkeypatch)
    assert not rule_reached_the_model(there)


# ══════════════════════════════════════════════════════════════════════════
# 四、🔴 「定过、都不作数了」那句话的**理由**
# ══════════════════════════════════════════════════════════════════════════


def test_the_expired_count_cannot_say_why_they_stopped_counting(
    client: TestClient, book: dict[str, str]
) -> None:
    """两种过期在出参上**一个字节都不差**，所以屏幕上任何一句「因为你 X」都是猜的。

    ── 两条路，一份出参 ──────────────────────────────────────────────────

    | 怎么没的 | 作者做了什么 |
    |---|---|
    | 翻页 | 他从第 2 章翻到了第 3 章 |
    | **说了下一句话** | 他**一直待在第 3 章**，只是又开口了（批级 = 取窄的默认档） |

    第二种是**默认那一档**（ADR 0023 决策二「取窄」：说一遍的规矩活到他下一次开口），
    也就是作者最常撞见的那一种。而屏幕今天只说得出第一种。
    """
    pid = book["pid"]

    turned_the_page = open_chat(client, pid)
    seed(
        book["db"],
        pid,
        turned_the_page,
        said("这一章别太煽情。"),
        rule_message("别太煽情", chapter=2),
    )

    just_kept_talking = open_chat(client, pid)
    seed(
        book["db"],
        pid,
        just_kept_talking,
        said("这三版太煽情了。"),
        rule_message("别太煽情", chapter=3),
        said("行，那接着写。"),
    )

    # 一个翻了页、一个一步没挪，**出参完全一样**。
    assert rules_of(client, pid, turned_the_page, 3) == {
        "chapter": 3,
        "rules": [],
        "expired": 1,
    }
    assert rules_of(client, pid, just_kept_talking, 3) == rules_of(
        client, pid, turned_the_page, 3
    )


def test_the_common_way_a_rule_lets_go_is_not_turning_the_page(
    client: TestClient, book: dict[str, str]
) -> None:
    """**默认那一档根本不涉及翻页** —— 屏幕上那句理由说的却只有翻页。

    这一条钉的是「界面那句话该覆盖什么」的事实依据：一条只说过一遍的规矩（取窄，
    ADR 0023 的默认）在**同一章里**、作者只是接着说了下一句，就已经不作数了。
    界面把这一档解释成「往后翻就自动放掉」，作者会得出「只要我不翻页它就还在」——
    一个**恰好反着**的心智模型，而下一次他就会跳过一件他以为已经交代过的事。

    后端在活着的规矩上是说得清的（批级那句 `scope` 写着「你再说一句话，它就自动
    放掉」），所以两句话对不上的地方只在**零态**。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    seed(
        book["db"],
        pid,
        chat_id,
        said("这三版太煽情了。"),
        rule_message("别太煽情", chapter=3),
    )

    # 还没说下一句：它在，而且后端自己把「怎么放掉」写在那一行上。
    live = rules_of(client, pid, chat_id, 3)["rules"]
    assert len(live) == 1
    assert "再说一句话" in live[0]["scope"]
    assert "下一章" not in live[0]["scope"]

    # 作者接着说了一句 —— 一页没翻，规矩没了。
    seed(book["db"], pid, chat_id, said("行，那接着写。"))
    moved_on = rules_of(client, pid, chat_id, 3)
    assert moved_on == {"chapter": 3, "rules": [], "expired": 1}
