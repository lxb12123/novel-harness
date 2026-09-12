"""对抗性验证：**边界三（第二真相源）和账**，在会话真的落盘之后。

这份文件不重复 `test_chat_api.py` 的功能验收。它只问五个问题，每一个都是
「答错了不会有任何东西报错」的那一种：

1. **正文有没有第二份**，而磁盘那份被改了之后谁赢、有没有东西说这件事。
2. **持久化第一次让边界一变成真的不可回收** —— 毒不在**出参**里不算数，
   这里搜的是**已经落盘、删不掉的那张表的每一列**。
3. **账的完整性** —— 花掉的钱在不在账上，而且在不在 `GET /activity` 上。
   配一个「只在成功时记账」的假实现当自守卫。
4. **打断真的中断了一次进行中的调用**（适配器那一侧，不是 loop 那一侧）。
5. **会话删掉之后**，账还在吗、审计链动没动。

── 这里为什么另起一份 fixture ────────────────────────────────────────────

`test_api.py` 的 `book` 已经带毒（Secret 挂 `props.twist`、未来 Character 挂
`props.plot_note`），但它有两条路走不到：**`SecretDetail.description`**（扩展表那一行，
和 props 是两条不同的存法）和**挂在 Location 上的 `plot_note` 且这个 Location 真的被
某个人的 `character_state` 引用到**。`poisoned` 把这两条补上——不补的话「毒没出现」
这句话只覆盖了一半的毒。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
from novel_harness.agent.loop import (
    Cancellation,
    ModelCallReceipt,
    StopReason,
    run_turn,
    start_conversation,
)
from novel_harness.agent.model import AgentCancelled, ProviderModelPort
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.store import ChatStore
from novel_harness.db import connect
from novel_harness.declare import Ledger
from novel_harness.draft.capabilities import ReasoningEffort, plan_call, resolve_capabilities
from novel_harness.draft.length import DraftLanguage, LengthSpec
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.graph import NodeLabel, NodeProps, NodeSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from test_api import PLOT_NOTE, TWIST

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

SECRET_DESC = "血脉秘密的真相是萧决体内的玄血蛊"
"""`SecretDetail.description`。**和 `props.twist` 是两条不同的存法**，
一条走 `node.props`（`extra="allow"`），一条走秘密扩展表——搜一条不等于搜了两条。"""

LOC_NOTE = "青云城主府是萧决殒命之地"
"""挂在**真的会被引用到**的那个 Location 上（`character_state.location`）。
`NodeRef.of` 的 docstring 记的实测泄漏形态就是 `{"plot_note": …}` 挂在 Location 上。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def poisoned(book: dict[str, str]) -> dict[str, str]:
    """一本**带毒的书**：两种存法的秘密内容 + 一个被人物指着的带毒地点。"""
    conn = connect(book["db"])
    store = SqliteStoryGraph(conn)
    pid = book["pid"]
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.FACTION,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST, "plot_note": SECRET_DESC}),
        )
    )
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.LOCATION,
            name="青云城主府",
            props=NodeProps.model_validate({"plot_note": LOC_NOTE}),
        )
    )
    conn.commit()
    # 让 `character_state` 真的返回一个 `location`——否则那条收窄（Node → NodeRef）
    # 在这本书上根本没被走到，而「没走到」和「走到了没泄漏」是两件事。
    Ledger(store, conn, pid).declare_where(
        who="萧决", loc="青云城主府", quote="萧决在青云城主府第一次听说了血脉秘密的真相。"
    )
    conn.commit()
    conn.close()
    return book


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BYOK 指到一个登记过的端点。设置文件落在 tmp（绝不碰真实用户目录）。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


class Scripted:
    """按剧本一句一句回答。**剧本用完重复最后一条**（同 `test_chat_api.py`）。"""

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
            ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
    )


def use(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: model)


def open_chat(client: TestClient, pid: str, **body: Any) -> str:
    response = client.post(f"/api/projects/{pid}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def turn(client: TestClient, pid: str, chat_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/projects/{pid}/chats/{chat_id}/turn", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def every_column(db: str) -> str:
    """模式二**落盘的那几张表**的每一列，拼成一整块字。

    搜出参和搜这块字不是一回事：出参可以事后收窄，**这块字已经落盘了**——
    ADR 0019「若此决策错误」那一节把它写死了：「一旦某个工具把 `Node` 交出去过，
    那段秘密就已经在作者的持久化对话里了，**改代码不会把它删掉**」。

    `draft_candidate` 是 2026-08-12 加进来的**第三张**（ADR 0022 把候选稿从对话里挪了
    出去）。那份 ADR 结尾的原话：「如果候选表泄漏了秘密原文，**它是落盘的**——
    和对话历史一样不可回收……**直接查表的每一列**搜那几种毒。这跟在出参上搜不是一回事。」
    """
    conn = connect(db)
    rows = [dict(row) for row in conn.execute("SELECT * FROM chat_message").fetchall()]
    session_rows = [dict(row) for row in conn.execute("SELECT * FROM chat_session").fetchall()]
    drafts = [dict(row) for row in conn.execute("SELECT * FROM draft_candidate").fetchall()]
    conn.close()
    return json.dumps([rows, session_rows, drafts], ensure_ascii=False, default=str)


def bills(db: str) -> list[dict[str, Any]]:
    conn = connect(db)
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM model_call WHERE capability = 'agent' ORDER BY ts, id"
        ).fetchall()
    ]
    conn.close()
    return rows


def table_counts(db: str) -> dict[str, int]:
    """全库每张表各有多少行。"""
    conn = connect(db)
    names = [
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    counts = {
        name: int(conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"])
        for name in names
    }
    conn.close()
    return counts


_CONVERSATION_TABLES = frozenset({"chat_message", "chat_session", "model_call", "artifact"})
"""跑一轮**允许**长行的那几张：对话本身 + 那笔账（`artifact` 是账的内容寻址落点）。"""


def grew_beyond_the_conversation(
    before: dict[str, int], after: dict[str, int]
) -> list[str]:
    return sorted(
        name
        for name in after
        if after[name] != before.get(name) and name not in _CONVERSATION_TABLES
    )


def on_the_activity_page(client: TestClient, pid: str) -> list[dict[str, Any]]:
    """日志页上那几行。**验收不是「表里有行」，是作者看得见。**"""
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 100}).json()
    return [
        entry
        for entry in page["entries"]
        if entry["title_code"] == "call_entry_title" and entry["title_params"] == {"capability": "agent"}
    ]


# ══════════════════════════════════════════════════════════════════════════
# 一、正文有几份，以及磁盘那份被改了之后谁赢
# ══════════════════════════════════════════════════════════════════════════


def test_the_manuscript_the_assistant_read_is_a_third_copy_and_only_two_of_them_are_answers(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数一遍：同一段正文，磁盘 1 份、`chapter_snapshot` 1 份、`chat_message` 1 份。

    **三份不等于三个真相源。** 判据是「有几条读路径回答『第 N 章是什么』」：
    磁盘那份是答案（ADR 0007），`chapter_snapshot` 是冻结的证据锚（版本抽屉），
    而 `chat_message` 里那一份**是助手读过它的记录**——它必须存下来，否则 resume 之后
    模型看到的是另一段历史。这条测试钉的是「第三份存在」这个事实本身，
    下一条才去问「它过期了会怎样」。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(wants(("chapter_text", json.dumps({"chapter": 1}))), says("读完了。")),
    )
    chat_id = open_chat(client, pid)
    assert turn(client, pid, chat_id, chapter=1, said="读一下第 1 章")["lookups"] == 1

    sentence = "萧决在青云城主府第一次听说了血脉秘密的真相。"
    root = Path(client.get(f"/api/projects/{pid}").json()["root_path"])
    on_disk = [p for p in (root / "chapters").glob("*.md") if sentence in p.read_text("utf-8")]
    assert len(on_disk) == 1, f"磁盘上有 {len(on_disk)} 份"

    conn = connect(book["db"])
    snapshots = conn.execute(
        "SELECT COUNT(*) AS n FROM chapter_snapshot WHERE text LIKE ?", (f"%{sentence}%",)
    ).fetchone()["n"]
    in_chat = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_message WHERE content LIKE ?", (f"%{sentence}%",)
    ).fetchone()["n"]
    conn.close()
    assert snapshots == 1, f"版本抽屉里有 {snapshots} 份 —— 会话不许往那儿写"
    assert in_chat == 1, "对话里没有那一章正文 —— 那 resume 之后模型看到的就是另一段历史"


def test_the_author_edits_the_chapter_and_the_conversation_stops_serving_the_old_one(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**磁盘那份被作者改了、会话表那份没变的时候，谁赢。**

    这是边界三真正的形态。ADR 0007 说正文的真相源在磁盘上，可**模型下一轮读的是
    context**——而 context 里躺着它上一轮读到的那份快照。两份不一致的时候：

    - 对**作者**：磁盘赢（编辑器、版本抽屉、`GET …/text` 全都是磁盘那份）；
    - 对**模型**：会话表那份赢，因为它是被发出去的那一份。

    于是「第 1 章李管家说话了吗」有两个答案，而模型给的是过期的那个——
    **一段读起来完全正常、只是说错了的话**，没有任何东西会报错。

    这个仓库自己在别处已经把这条论证写死了（`agent/index.py` 说 L1 不缓存的理由）：

    > 一份缓存的命中表会在他保存的那一刻变成一个**看起来正常的错误答案**……
    > 而这一层全部的价值就是「确定性、当场算、答案永远是当前的」。

    对话表就是这样一份缓存，**而且它是持久化的**。所以判据不是「旧正文在不在库里」
    （它必须在，canonical 只增不改），是「**发出去的那一份**里还有没有它」。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(wants(("chapter_text", json.dumps({"chapter": 1}))), says("读完了。")),
    )
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="读一下第 1 章")

    # 作者在编辑器里改了第 1 章（走产品那条既有路由：先磁盘、再 sync）。
    rewritten = (
        "第一章 血脉\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家改口了：其实我知道。\n"
    )
    base = client.get(f"/api/projects/{pid}/chapters/1/text").json()["text_sha256"]
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": rewritten, "expected_text_sha256": base},
    )
    assert saved.status_code == 200, saved.text

    second = Scripted(says("嗯。"))
    use(monkeypatch, second)
    receipt = turn(client, pid, chat_id, chapter=1, said="第 1 章李管家说话了吗？")

    sent = json.dumps(second.calls[0], ensure_ascii=False)
    assert "李管家什么也没说" not in sent, (
        "发出去的那一份里还躺着**作者已经改掉**的正文，而且没有一个字说它是旧的 —— "
        "「第 1 章是什么」当场有了第二个答案，给出来的还是过期的那个（ADR 0019 边界三）"
    )
    assert receipt["context"]["stale_lookups"] == 1, "换掉了却不说，就是静默截断的另一种形态"

    # canonical **不许**被改写：旧的那一份仍然原样躺在库里（只增不改）。
    conn = connect(book["db"])
    still = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_message WHERE content LIKE ?", ("%李管家什么也没说%",)
    ).fetchone()["n"]
    conn.close()
    assert still == 1, "投影去改 canonical 了 —— 那是另一条把历史改写掉的路径"


def test_a_chapter_that_has_not_changed_is_not_re_read_out_from_under_the_model(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向：**没被改过的正文不许被顺手清掉。**

    上一条那个换法只对「已经不是当前」的那一份成立。要是它把每一份读过的正文都清掉，
    模型每一轮都得重读一次整章——那是拿作者的钱买一个没有发生的问题。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(wants(("chapter_text", json.dumps({"chapter": 1}))), says("读完了。")),
    )
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="读一下第 1 章")

    second = Scripted(says("嗯。"))
    use(monkeypatch, second)
    receipt = turn(client, pid, chat_id, chapter=1, said="接着说")
    sent = json.dumps(second.calls[0], ensure_ascii=False)
    assert "李管家什么也没说" in sent, "没改过的正文被清掉了 —— 模型下一轮要重读一次整章"
    assert receipt["context"]["stale_lookups"] == 0


def test_the_net_would_catch_a_projection_that_kept_serving_the_old_manuscript(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫。** 上面那条断言只有在判据真的抓得住「还在发旧正文」时才算数。

    这里把 loop 的过期判据换成一个恒 `False` 的假实现（「反正没人会改正文」那种
    最自然的省事写法），断言同一条判据当场红。
    """
    import novel_harness.agent.loop as loop_mod

    pid = book["pid"]
    monkeypatch.setattr(loop_mod, "outdated_manuscript", lambda content, context: False)
    use(
        monkeypatch,
        Scripted(wants(("chapter_text", json.dumps({"chapter": 1}))), says("读完了。")),
    )
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="读一下第 1 章")
    base = client.get(f"/api/projects/{pid}/chapters/1/text").json()["text_sha256"]
    client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={
            "markdown": "第一章 血脉\n\n萧决在青云城主府听说了真相。\n李管家改口了。\n",
            "expected_text_sha256": base,
        },
    )
    second = Scripted(says("嗯。"))
    use(monkeypatch, second)
    receipt = turn(client, pid, chat_id, chapter=1, said="第 1 章李管家说话了吗？")
    assert "李管家什么也没说" in json.dumps(second.calls[0], ensure_ascii=False)
    assert receipt["context"]["stale_lookups"] == 0


def test_nothing_outside_the_chat_layer_can_read_a_chapter_out_of_the_conversation() -> None:
    """边界三可机器验证的那一半，**比 `test_chat_api.py` 那条严一档**。

    那一条扫的是「谁写了 `chat_message` / `chat_session` 这两个字面量」。它漏掉一整类
    绕法：`ChatStore` 是一个类，`from ..agent.store import ChatStore` 之后
    `.load(...).conversation` 里就有整章正文，而那个文件里一次表名都不出现——
    **和 `test_arch_guard.py` 钉的那个绕法是同一个形状**（「一个文件写
    `from ..db import connect` 就能裸写第二份时态过滤，而 `sqlite3` 一次都不出现」）。

    所以这里数的是**谁拿得到那个读端**：表名字面量 ∪ `ChatStore` ∪ `agent.store`。
    """
    allowed = {"agent/store.py", "agent/__init__.py", "api/chat.py", "ids.py"}
    pattern = ("chat_message", "chat_session", "ChatStore", "agent.store", "agent import store")
    offenders = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if any(token in path.read_text(encoding="utf-8") for token in pattern)
        and path.relative_to(SRC).as_posix() not in allowed
    )
    assert not offenders, (
        f"这些文件够得着会话的读端：{offenders}\n"
        "正文的真相源在磁盘上（ADR 0007）。会话表里躺着助手读过的整章正文，"
        "多一条读它的路径就是「哪一份正文是真的」的第二个答案。"
    )


def test_that_net_would_catch_a_reader_that_never_names_the_tables(tmp_path: Path) -> None:
    """**自守卫**：上面那张网必须抓得住一个一次表名都不写的读路径。"""
    sneaky = tmp_path / "export.py"
    sneaky.write_text(
        "from ..agent.store import ChatStore\n"
        "def chapter_text(conn, pid, chat_id):\n"
        "    return ChatStore(conn).load(pid, chat_id)\n",
        encoding="utf-8",
    )
    body = sneaky.read_text(encoding="utf-8")
    assert not any(token in body for token in ("chat_message", "chat_session")), (
        "样本本身写了表名 —— 那就没在测「不写表名的绕法」"
    )
    pattern = ("chat_message", "chat_session", "ChatStore", "agent.store", "agent import store")
    assert any(token in body for token in pattern), "网抓不住不写表名的那一类读路径"


# ══════════════════════════════════════════════════════════════════════════
# 二、毒在不在**落盘的**那张表里（边界一，持久化之后不可回收）
# ══════════════════════════════════════════════════════════════════════════


def test_no_poison_survives_into_the_row_that_can_never_be_taken_back(
    client: TestClient, poisoned: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**表里那几个查询工具全跑一遍**，然后逐列搜四种毒。

    ADR 0019 边界一的修复成本那一条写着「最贵，而且不可回收」——这一层正是
    「持久化」这三个字第一次成真的地方，所以判据从「出参里没有」升成
    「**已经写进库、删不掉的那几行里没有**」。
    """
    pid = poisoned["pid"]
    use(
        monkeypatch,
        Scripted(
            # 一批最多 6 个（`TurnLimits.max_calls_per_step`），所以分两步走完这几条。
            wants(
                ("book_index", "{}"),
                ("scene_constraints", json.dumps({"chapter": 1})),
                ("character_state", json.dumps({"chapter": 1, "character": "萧决"})),
                ("chapter_text", json.dumps({"chapter": 1})),
                ("chapter_summaries", json.dumps({"first_chapter": 1, "last_chapter": 3})),
                ("character_chapters", json.dumps({"characters": ["萧决", "李管家"]})),
            ),
            wants(
                ("draft_chapter", json.dumps({"chapter": 2, "goal": "写一场对峙"})),
                # 模型幻想出一个约束参数（边界二）+ 拿秘密当人查：两条失败路径的
                # 错误话术也要落盘，它们同样是「删不掉的那几行」。
                ("scene_constraints", json.dumps({"chapter": 200, "twist": TWIST})),
                ("character_state", json.dumps({"chapter": 1, "character": "血脉秘密"})),
            ),
            says("看完了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    receipt = turn(client, pid, chat_id, chapter=1, said="帮我把第 1 章的底细摸一遍")
    assert receipt["lookups"] == 9, "那几条工具没全跑到，这条断言就没覆盖到它想覆盖的面"

    stored = every_column(poisoned["db"])
    for label, poison in (
        ("Secret 的 props.twist", TWIST),
        ("SecretDetail.description", SECRET_DESC),
        ("未来 Character 的 props.plot_note", PLOT_NOTE),
        ("Location 的 props.plot_note", LOC_NOTE),
    ):
        assert poison not in stored, (
            f"{label} 落进了 `chat_message` —— 它已经在作者的持久化对话里了，"
            "改代码删不掉（ADR 0019 边界一「不可回收」）"
        )
    # 秘密的**显示名**在里面是对的（那是面板本来就要渲染的东西），
    # 拿它当反证：上面那四条「没搜到」不是因为工具压根没跑到秘密上。
    assert "血脉秘密" in stored


def test_a_refusal_never_launders_text_into_the_conversation(
    client: TestClient, poisoned: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参数不合法时的那句话**只带字段名，不回显值**。

    回显入参在别处是好心，在这里是一条把任意字符串搬进**持久化**对话的通路。
    """
    pid = poisoned["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("scene_constraints", json.dumps({"chapter": 1, "私货": SECRET_DESC}))),
            says("好。"),
        ),
    )
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="查一下")
    stored = every_column(poisoned["db"])
    assert "私货" in stored, "连字段名都没落盘 —— 那这条断言测的不是回显"
    assert SECRET_DESC not in stored, "拒绝的话里回显了入参值 —— 那是一条搬字符串的通路"


# ══════════════════════════════════════════════════════════════════════════
# 三、账：已经花掉的必须在账上
# ══════════════════════════════════════════════════════════════════════════


def test_every_model_call_in_every_shape_of_turn_lands_on_the_activity_page(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N 次模型调用 = `model_call` 里 N 行 = 日志页上 N 行。**三种收场各验一次。**

    板子上已经记着一个同款的洞：`/draft` 一行 `model_call` 都不写，于是日志页显示的是
    真实花销的一小部分、**看起来却像全部**。写作助手是第二个会大量花钱的地方。
    """
    pid = book["pid"]
    chat_id = open_chat(client, pid)
    expected = 0

    # ① 正常跑完：一次工具 + 一次收尾 = 两次调用。
    for index in range(3):
        use(
            monkeypatch,
            Scripted(
                wants(("book_index", "{}")),
                says("看完了", prompt_tokens=10, completion_tokens=2),
            ),
        )
        assert turn(client, pid, chat_id, chapter=1, said=f"第 {index} 问")["steps"] == 2
        expected += 2
        assert len(bills(book["db"])) == expected

    # ② 中途被作者停：**停在第一次调用之后**，那笔钱已经花了。停下来之后它还会
    # 问作者一句（`run_turn` 的 debrief，2026-09-12）——那也是一次调用、一笔账。
    use(
        monkeypatch,
        Scripted(
            wants(("book_index", "{}")),
            says("好"),
            before=lambda cancel: cancel.stop(),
        ),
    )
    stopped = turn(client, pid, chat_id, chapter=1, said="再查一次")
    assert stopped["reason"] == StopReason.AUTHOR_STOPPED.value
    assert stopped["lookups"] == 0, "按了停之后还在派发工具"
    expected += 2
    assert len(bills(book["db"])) == expected, "停下来的那一轮把已经付过费的那次调用漏了"

    # ③ provider 挂掉：**没花的钱不许出现在账上**（没有 `CompletionResult` 就没有 token）。
    from novel_harness.draft.provider import ProviderError

    def dead(messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        raise ProviderError("connection refused: https://api.deepseek.com")

    use(monkeypatch, dead)
    assert turn(client, pid, chat_id, chapter=1, said="再来")["reason"] == (
        StopReason.MODEL_UNREACHABLE.value
    )
    assert len(bills(book["db"])) == expected

    # ④ resume 补跑：工具重放**不花钱**，它后面那次收尾调用花钱。
    use(monkeypatch, Scripted(says("查完了", prompt_tokens=5, completion_tokens=1)))
    resumed = turn(client, pid, chat_id, chapter=1)
    assert resumed["reason"] == StopReason.DONE.value
    expected += 1
    assert len(bills(book["db"])) == expected

    # 最后一格：**日志页上真的看得见**。
    #
    # `capability="agent"` 会原样出现在 `title_params` 里——国际化第四批·笔二起
    # 那不是泄漏，是设计：结构化的封闭枚举值，前端拿去查 `CAPABILITY_LABEL` 才翻成
    # 「写作助手」。旧断言「`json.dumps(entries)` 里不许出现 agent」钉的是「后端把
    # 机器码直接摆上屏」，那条判据现在要往下移一层——查的是 `subtitle_params`
    # 结构对不对，不是整份响应里有没有这个词。
    entries = on_the_activity_page(client, pid)
    assert len(entries) == expected, f"表里 {expected} 行，日志页只有 {len(entries)} 行"
    assert all(entry["subtitle_code"] == "call_subtitle" for entry in entries)
    assert all(entry["subtitle_params"]["model"] == "deepseek-v4-flash" for entry in entries)
    # 底栏那一格是同一张表的另一个读端。
    assert client.get(f"/api/projects/{pid}/runs").json()["totals"]["calls"] == expected


def test_the_net_would_catch_a_ledger_that_only_bills_the_successful_calls(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫。** 上面那条只有在网抓得住漏账时才算数。

    这里换一个「只在这一轮善终时记账」的假实现——那是最自然的省事写法
    （在 `run_turn` 返回之后统一记一笔），断言判据当场红：
    被作者停掉的那一轮花了钱，而账上一行都没有。
    """
    pid = book["pid"]
    real = chat_mod._ledger

    def only_when_it_ends_well(conn: Any, project_id: str) -> Any:
        record = real(conn, project_id)

        def fake(receipt: ModelCallReceipt) -> None:
            if receipt.finish_reason == "stop":
                record(receipt)

        return fake

    monkeypatch.setattr(chat_mod, "_ledger", only_when_it_ends_well)
    use(
        monkeypatch,
        Scripted(
            wants(("book_index", "{}")), says("好"), before=lambda cancel: cancel.stop()
        ),
    )
    chat_id = open_chat(client, pid)
    stopped = turn(client, pid, chat_id, chapter=1, said="查一次")
    assert stopped["reason"] == StopReason.AUTHOR_STOPPED.value
    # 真账上是两行（叫工具那一次 + 停下来之后问的那一句）；假实现只记「善终」的，
    # 于是只剩问的那一句（它 `finish_reason == "stop"`），叫工具那一次漏了。
    assert len(bills(book["db"])) == 1, "假实现没漏账 —— 那上面那条断言证明不了任何事"
    assert len(on_the_activity_page(client, pid)) == 1


def test_a_call_the_provider_never_measured_is_not_reported_as_zero(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**账上的零和「没报」是两件事。** 供应商没报 token 时，日志页写的必须是
    「未记录」，不是 0——否则底栏那个和看起来像全部，而它是低估。
    """
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好。")))  # 一个 usage 都不报
    chat_id = open_chat(client, pid)
    receipt = turn(client, pid, chat_id, chapter=1, said="说句话")
    assert receipt["calls_without_usage"] == 1
    assert receipt["tokens_reported"] == 0

    # 「未记录」不是后端拼的中文了（国际化第四批·笔二起）——`tokens_in`/`tokens_out`
    # 是 `None` 就是「没报」，前端的 `call_subtitle`/`value_optional_number` 模板才把
    # `None` 渲成「未记录」。这里钉的是 `None` 有没有原样送到参数里，不是一句中文。
    entry = on_the_activity_page(client, pid)[0]
    assert entry["subtitle_code"] == "call_subtitle"
    assert entry["subtitle_params"]["tokens_in"] is None, (
        f"没报的 token 被写成了一个数：{entry['subtitle_params']}"
    )
    assert entry["subtitle_params"]["tokens_out"] is None
    detail = client.get(f"/api/projects/{pid}/activity/{entry['id']}").json()
    tokens_in_row = next(r for r in detail["rows"] if r["label_code"] == "detail_label_tokens_in")
    assert tokens_in_row["value_params"] == {"n": None}


# ══════════════════════════════════════════════════════════════════════════
# 四、打断真的中断了一次进行中的调用
# ══════════════════════════════════════════════════════════════════════════


class _SlowStream:
    """一条吐得很慢的假流：**拉了几片、有没有被关掉**，都留着给断言看。"""

    def __init__(self, pieces: int, on_chunk: Any = None) -> None:
        self.pieces = pieces
        self.pulled = 0
        self.closed = False
        self._on_chunk = on_chunk

    def __iter__(self) -> Any:
        for index in range(self.pieces):
            self.pulled += 1
            if self._on_chunk is not None:
                self._on_chunk(index)
            yield SimpleNamespace(
                model="deepseek-v4-flash",
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=f"第{index}段", tool_calls=None),
                        finish_reason=None,
                    )
                ],
            )

    def close(self) -> None:
        self.closed = True


def _streaming_plan() -> Any:
    """一份 `stream=True` 的 plan。**`stream` 不是这儿挑的**，是 `plan_call` 按冻结阈值
    从输出预算推出来的——所以这里抬的是长度档，不是一个开关。"""
    plan = plan_call(
        LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=8_000, max_units=9_000),
        ReasoningEffort.OFF,
        resolve_capabilities("https://api.deepseek.com", "deepseek-v4-flash"),
    )
    assert plan.stream is True
    return plan


class _ClientOf:
    def __init__(self, response: Any) -> None:
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )


def test_pressing_stop_halfway_does_not_read_the_rest_of_the_stream() -> None:
    """**造一个吐得很慢的端点，中途亮信号，断言它没有把整个流读完。**

    3.3 那一层说得出自己退化了（适配器不理信号 ⇒ 打断变成「这一次调用跑完就停」），
    **说不出适配器退没退化**——这条只能在这儿测。
    """
    cancel = Cancellation()
    stream = _SlowStream(200, on_chunk=lambda i: cancel.stop() if i == 4 else None)
    port = ProviderModelPort(
        ProviderConfig(model="deepseek-v4-flash", base_url="https://api.deepseek.com", api_key="k"),
        _streaming_plan(),
        client=_ClientOf(stream),
    )
    with pytest.raises(AgentCancelled):
        port([{"role": "user", "content": "写"}], tools=[], cancel=cancel)
    assert stream.pulled == 5, f"信号亮了之后还在拉：拉了 {stream.pulled} 片 / 一共 200 片"
    assert stream.closed, "没关掉上游 —— 服务端会继续生成，而钱按生成算不按接收算"


def test_the_agent_call_streams_so_that_stop_lands_within_a_chunk() -> None:
    """上面那条量的打断粒度**在生产里到得了**（2026-09-12 起）。

    此前这儿钉的是反面：对话回复的输出预算够不到流式阈值 ⇒ `plan.stream is False` ⇒
    打断退化成「这一次调用跑完就停」，作者按了停要等十几秒到一分钟。改的不是预算
    （抬预算会让 `stream` 的判据变成「谁想要流式」），是 `agent_call_plan` 要了
    `interruptible`——和起草那一档同一条路。

    连带的账已经补上：被掐掉的那次调用**进账**（token 留空），`test_agent_model.py::
    test_the_interruption_is_a_provider_error_so_the_loop_reads_it_as_the_author` 钉着。
    """
    from novel_harness.agent.model import agent_call_plan

    _, plan = agent_call_plan(
        ProviderConfig(model="deepseek-v4-flash", base_url="https://api.deepseek.com", api_key="k")
    )
    assert plan.interruptible is True
    assert plan.stream is True


def test_the_stop_button_from_another_request_reaches_a_turn_in_flight(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：「停」是另一个请求，而它够得着正在跑的那一轮。

    这里比 `test_chat_api.py` 那条多问一件事：**停下来之后，已经花掉的那一笔还在账上**。
    """
    pid = book["pid"]
    entered, release = Event(), Event()

    def block(cancel: Any) -> None:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=5)

    use(monkeypatch, Scripted(wants(("book_index", "{}")), says("好"), before=block))
    chat_id = open_chat(client, pid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            client.post,
            f"/api/projects/{pid}/chats/{chat_id}/turn",
            json={"chapter": 1, "said": "查一次"},
        )
        assert entered.wait(timeout=5)
        pressed = client.post(f"/api/projects/{pid}/chats/{chat_id}/stop")
        assert pressed.status_code == 200 and pressed.json()["stopped"] is True
        release.set()
        done = running.result(timeout=10)
    assert done.json()["reason"] == StopReason.AUTHOR_STOPPED.value
    # 两行：被停掉的那一次调用 + 停下来之后问作者的那一句（debrief）。
    assert len(bills(book["db"])) == 2, "被停掉的那一轮把已经付过费的那次调用漏了"
    assert len(on_the_activity_page(client, pid)) == 2


# ══════════════════════════════════════════════════════════════════════════
# 五、会话删掉之后
# ══════════════════════════════════════════════════════════════════════════


def test_deleting_a_conversation_takes_the_messages_and_leaves_the_bill(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**钱已经付了，账不许跟着对话一起消失。**

    删对话是作者的整理动作；账是花销记录。让 `model_call` 挂在会话上，
    作者清理侧栏的那一刻底栏的花销汇总就凭空变小了——**而那是不可查的少算**。
    审计链（`decision_log`）同理：它记的是 canon 改动，和对话一行都不相干。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(("book_index", "{}")), says("看完了", prompt_tokens=7, completion_tokens=3)
        ),
    )
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="看看目录")

    conn = connect(book["db"])
    decisions_before = conn.execute("SELECT COUNT(*) AS n FROM decision_log").fetchone()["n"]
    messages_before = conn.execute("SELECT COUNT(*) AS n FROM chat_message").fetchone()["n"]
    conn.close()
    assert messages_before > 0 and decisions_before > 0

    deleted = client.delete(f"/api/projects/{pid}/chats/{chat_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True

    assert len(bills(book["db"])) == 2, "删对话把账一起删了"
    assert len(on_the_activity_page(client, pid)) == 2
    assert client.get(f"/api/projects/{pid}/runs").json()["totals"]["calls"] == 2

    conn = connect(book["db"])
    assert conn.execute("SELECT COUNT(*) AS n FROM chat_message").fetchone()["n"] == 0, (
        "消息没跟着会话走 —— 那是一堆够不着的孤儿行"
    )
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM decision_log").fetchone()["n"]
        == decisions_before
    ), "删一段对话动到了审计链"
    conn.close()

    # 磁盘上的正文一个字节没动（会话表里那份从来不是答案）。
    assert client.get(f"/api/projects/{pid}/chapters/1/text").status_code == 200


def test_a_deleted_conversation_cannot_be_resumed_but_the_book_is_untouched(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删掉之后再跑一轮 = 404，**不是一段凭空重建出来的空对话**。"""
    pid = book["pid"]
    use(monkeypatch, Scripted(says("好。")))
    chat_id = open_chat(client, pid)
    turn(client, pid, chat_id, chapter=1, said="说句话")
    client.delete(f"/api/projects/{pid}/chats/{chat_id}")

    again = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": 1, "said": "还在吗"}
    )
    assert again.status_code == 404
    assert client.get(f"/api/projects/{pid}/chats").json() == []


# ══════════════════════════════════════════════════════════════════════════
# 六、往返：库里那一份读回来还是同一段历史
# ══════════════════════════════════════════════════════════════════════════


def test_a_chapter_full_of_things_sqlite_hates_survives_the_round_trip(
    book: dict[str, str]
) -> None:
    """正文里真的会出现的那几种字符（多字节、emoji、NUL、残缺 JSON 的参数）
    存进去读回来必须**逐字节相同**——不同 = resume 之后模型看到的是另一段历史，
    而没有任何东西会报错。

    这条和 `test_chat_store.py` 那 11 条不重复：那儿量的是构造出来的样本，
    这儿量的是**一份真的 `chapter_text` 出参**（正文原样进了 `content`）。
    """
    from novel_harness.agent.loop import AgentMessage, Role

    conn = connect(book["db"])
    store = ChatStore(conn)
    session = store.create(book["pid"], title="往返")
    payload = json.dumps(
        {
            "chapter": 1,
            "text": "第一章\n\n他说：「走。」\x00🗡️\n",
            "units": 12,
            "units_given": 12,
            "truncated": False,
            "future": False,
            "working_chapter": 1,
            "notes": [],
        },
        ensure_ascii=False,
    )
    messages = [
        AgentMessage(role=Role.USER, content="读第 1 章"),
        AgentMessage(
            role=Role.ASSISTANT,
            tool_calls=(ToolCall(id="x", name="chapter_text", arguments='{"chapter": 1'),),
        ),
        AgentMessage(role=Role.TOOL, content=payload, tool_call_id="x", chapter=1),
    ]
    store.append(book["pid"], session.id, base_count=0, messages=messages)
    loaded = store.load(book["pid"], session.id)
    conn.close()
    assert loaded is not None
    assert loaded.conversation.messages == tuple(messages)
    assert (
        loaded.conversation.model_dump_json()
        == start_conversation()
        .model_copy(update={"messages": tuple(messages)})
        .model_dump_json()
    )


def test_the_conversation_table_has_no_column_a_chapter_could_be_anchored_to(
    book: dict[str, str]
) -> None:
    """结构判据：这两张表里**没有一列指向 `chapter`**。

    有外键就有 join，有 join 就有一条从对话回答「第 N 章是什么」的路径——
    而那正是边界三要防的东西。`chapter` 那一列是个**裸整数**（投影的过滤判据），
    它连不到任何一行数据上，所以它答不了任何问题。
    """
    conn = connect(book["db"])
    keys = {
        table: [dict(row) for row in conn.execute(f"PRAGMA foreign_key_list({table})")]
        for table in ("chat_session", "chat_message")
    }
    columns = {
        row["name"]: row["type"]
        for row in conn.execute("PRAGMA table_info(chat_message)")
    }
    conn.close()
    assert [k["table"] for k in keys["chat_session"]] == ["project"]
    assert [k["table"] for k in keys["chat_message"]] == ["chat_session"]
    assert columns["chapter"] == "INTEGER"


def test_running_a_turn_writes_nothing_but_the_conversation_and_the_bill(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跑一轮之后**哪几张表变了**。

    这条是「第二真相源」的整体判据：会话只许长在 `chat_*` 和 `model_call` 上。
    多出任何一张（`chapter_snapshot` / `chapter` / `node` / `edge` / `event`…）
    就说明这一轮在别处也写了一份，而那一份会被别人读。
    """
    pid = book["pid"]
    use(
        monkeypatch,
        Scripted(
            wants(
                ("chapter_text", json.dumps({"chapter": 1})),
                ("scene_constraints", json.dumps({"chapter": 1})),
            ),
            says("看完了。"),
        ),
    )
    chat_id = open_chat(client, pid)
    before = table_counts(book["db"])
    turn(client, pid, chat_id, chapter=1, said="读一下第 1 章")
    after = table_counts(book["db"])
    assert grew_beyond_the_conversation(before, after) == [], (
        f"跑一轮还写了这些表：{grew_beyond_the_conversation(before, after)}"
    )

    # **自守卫**：上面那条只有在它认得出「多写了一张表」时才算数。这里手写一次
    # 「把没被接受的草稿存进版本抽屉」（边界三点名的那个错事），断言判据当场红。
    conn = connect(book["db"])
    chapter_id = conn.execute("SELECT id FROM chapter LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256)"
        " VALUES ('snapshot:fake', ?, '一稿', 'deadbeef')",
        (chapter_id,),
    )
    conn.commit()
    conn.close()
    assert grew_beyond_the_conversation(before, table_counts(book["db"])) == [
        "chapter_snapshot"
    ], "判据认不出多写的那张表 —— 那上面那条断言证明不了任何事"


# ══════════════════════════════════════════════════════════════════════════
# 七、过期判据本身
# ══════════════════════════════════════════════════════════════════════════


def test_only_a_real_manuscript_payload_is_ever_judged_stale(book: dict[str, str]) -> None:
    """过期判据**只认正文那一种返回**，不认别的。

    判的是「这一份和磁盘上那份一不一样」，而磁盘只对正文成立（ADR 0007）。
    拿它去判约束/摘要那几条返回，就是用一个答不了的问题去删有用的东西。
    """
    from novel_harness.agent.tools import outdated_manuscript

    conn = connect(book["db"])
    store = SqliteStoryGraph(conn)
    pid = book["pid"]
    root = str(Path(book["db"]).parent / "book")
    context = ToolContext(store=store, project_id=pid, root_path=root, working_chapter=1)

    assert outdated_manuscript("一句中文，不是 JSON", context) is False
    assert outdated_manuscript('{"chapter":1,"cast":[],"cast_derived":true}', context) is False
    assert outdated_manuscript("", context) is False

    fresh = json.dumps(
        {
            "chapter": 1,
            "text": (Path(root) / "chapters" / "0001.md").read_text(encoding="utf-8-sig"),
            "units": 1,
            "units_given": 1,
            "truncated": False,
            "future": False,
            "working_chapter": 1,
            "notes": [],
        },
        ensure_ascii=False,
    )
    assert outdated_manuscript(fresh, context) is False
    stale = json.loads(fresh)
    stale["text"] = "第一章 血脉\n\n完全不是这一章。\n"
    assert outdated_manuscript(json.dumps(stale, ensure_ascii=False), context) is True
    gone = json.loads(fresh)
    gone["chapter"] = 900
    assert outdated_manuscript(json.dumps(gone, ensure_ascii=False), context) is True
    conn.close()


def test_a_turn_that_cannot_reach_the_disk_does_not_call_everything_stale(
    book: dict[str, str]
) -> None:
    """`root_path` 缺席时读不到磁盘。**那时不许把所有正文都判成过期**——
    「我看不见」和「它变了」是两件事，而按后者办等于每一轮都把读过的正文清空。
    """
    from novel_harness.agent.tools import outdated_manuscript

    conn = connect(book["db"])
    context = ToolContext(store=SqliteStoryGraph(conn), project_id=book["pid"], working_chapter=1)
    payload = json.dumps(
        {
            "chapter": 1,
            "text": "第一章 血脉\n",
            "units": 1,
            "units_given": 1,
            "truncated": False,
            "future": False,
            "working_chapter": 1,
            "notes": [],
        },
        ensure_ascii=False,
    )
    assert outdated_manuscript(payload, context) is False
    conn.close()


def test_the_stale_check_never_takes_a_turn_down_with_it(book: dict[str, str]) -> None:
    """判据坏掉的时候**这一轮照跑**。

    它是一个「让上下文更准」的东西，不是一道闸——为它抛一个异常出去，
    作者拿到的是「联系不上写作模型」或者一个栈，而他只是想聊天。
    """
    import novel_harness.agent.loop as loop_mod

    conn = connect(book["db"])
    context = ToolContext(
        store=SqliteStoryGraph(conn),
        project_id=book["pid"],
        root_path=str(Path(book["db"]).parent / "book"),
        working_chapter=1,
    )

    def boom(content: str, ctx: Any) -> bool:
        raise RuntimeError("判据自己坏了")

    original = loop_mod.outdated_manuscript
    loop_mod.outdated_manuscript = boom  # type: ignore[assignment]
    try:
        result = run_turn(
            start_conversation().with_author("说句话"),
            context=context,
            model=lambda messages, *, tools, cancel: says("好。"),
            ledger=lambda receipt: None,
            cancel=Cancellation(),
        )
    finally:
        loop_mod.outdated_manuscript = original  # type: ignore[assignment]
        conn.close()
    assert result.reason is StopReason.DONE
