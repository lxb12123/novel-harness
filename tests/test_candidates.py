"""对抗性验证：**候选表这个新落盘面，以及三档界面靠的那份后端出参**（ADR 0022 复核）。

`tests/test_draft_candidates.py` 是造这一刀的人自己架的网。这一份从外面再打一遍，
**只站它没站到的位置**：

1. **那份网的毒扫描走的是「不知道这一场有谁」那条退化路**（`_ask()` 直接交
   `unknown_cast_constraints`），于是 `_with_memory` 整段一次都没跑过——而**记忆前言
   是这条链路上最大的一块字**（人物档案 + 近期事件 + 更早章节滚动总结）。这一份让 cast
   真的从正文里数得出来，把那一段拉进扫描面，再**逐列**搜表。
2. 候选交出去的口子有**三个**（`POST …/turn` 的回执 / `GET …/drafts` / `GET …/drafts/{id}`），
   那份网只搜了表。**表干净不等于出参干净**，反过来也不等于。
3. **「省了多少」至今没有一个数。** ADR 0022 全部的理由是「一整章 × 剩下的每一轮」，
   而没有任何东西量过剪完之后对话里到底剩多少字。硬上限写死在 `IN_CONVERSATION_UNITS`。
4. **闸拒了之后那一稿还在不在桌上。** `landed` 是 ADR 0022 新长出来的状态，它同时决定
   界面上的推荐位和清理策略——拒了却标成 landed，作者会以为它进了书，而且它会被清掉。
5. **屏障那一条今天的判据是「读那张表的声明」**（`ToolSpec.concurrent`），
   没有任何东西验过 `BatchRunner` 真的照它排队。
6. **闸落在并发窗口中间**那一档：账和 `tool_call` 的配对同时被考。

结论写在报告里，这儿只留会红的断言。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.api.chat as chat_mod
import novel_harness.draft.generate as generate
from novel_harness import importer, project
from novel_harness.agent.candidates import (
    PREVIEW_ELLIPSIS,
    PREVIEW_UNITS,
    DraftCandidateStore,
    preview_of,
)
from novel_harness.agent.drafting import (
    SELF_NOTE_MARK,
    SELF_NOTE_UNITS,
    chapter_drafter,
    split_self_note,
)
from novel_harness.agent.loop import (
    AgentMessage,
    Conversation,
    Role,
    TurnLimits,
    run_turn,
    start_conversation,
)
from novel_harness.agent.ports import ToolContext
from novel_harness.agent.tools import dispatch_all
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.graph import NodeLabel, NodeProps, NodeSpec, SecretDetail
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from calibration_seed import seed_calibration

ENDPOINT = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"

SECRET_DESC = "血脉的真相是他母亲当年换了孩子"
"""`SecretDetail.description` 那一路的毒。`book` 那个 fixture 里的秘密没有 description，
所以这一份自己补一条上去——**ADR 0022 结尾点名的三种毒，一种都不许少**。"""

IN_CONVERSATION_UNITS = 420
"""**一稿留在对话里最多多少字。** 写死的，不是「差不多」。

它 = 定长预览（`PREVIEW_UNITS` + 记号）+ 自述（`SELF_NOTE_UNITS` + 记号）+ JSON 的键名、
编号、字数、以及**那一章的禁说清单**。ADR 0022 的整个理由是「一整章 × 剩下的每一轮」，
所以这个数乘上一批的稿数、再乘上会话剩下的轮数，才是它真实的价格。

**它红的时候不许直接调大**：先问那多出来的字是谁的——正文漏出来了，还是有人往返回里
加了一段没有上限的东西（今天唯一没有上限的是禁说清单，见第三节最后一条）。
"""

BODY_UNITS = 2_400
"""假写手交回来那一稿有多长。**要过得了长度闸**（`AGENT_DRAFT_LENGTH` 是中文 2,000–3,000），
否则 ADR 0011 D3 的续写会再要一次调用，账上就变成两笔，这几节量的东西全跟着漂。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配：一本**毒齐了**的书 + 一个只替掉「那一次真的模型调用」的写手
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", ENDPOINT)
    monkeypatch.setenv("NH_LLM_MODEL", MODEL)
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


@pytest.fixture
def poisoned(book: dict[str, str]) -> dict[str, str]:
    """`book` 那本书 + 一条**带 description 的秘密**。

    `book` 里已经有 `props.twist`（秘密节点上）和 `props.plot_note`（第 200 章才首现的
    人物上），缺的只有 `SecretDetail.description` 那一路。补上之后三种毒齐了。
    """
    conn = connect(book["db"])
    try:
        store = SqliteStoryGraph(conn)
        store.upsert_node(
            NodeSpec(
                project_id=book["pid"],
                label=NodeLabel.SECRET,
                name="禁地之密",
                props=NodeProps.model_validate({}),
                secret=SecretDetail(description=SECRET_DESC),
            )
        )
        conn.commit()
    finally:
        conn.close()
    return book


def _poisons(book: dict[str, str]) -> dict[str, str]:
    """三种毒的原文。**从 `test_api` 借，不在这儿抄第二份**——抄一份的那天，
    那边改了毒的措辞、这边还在搜旧的，于是这张网静默变成永远绿的。"""
    from test_api import PLOT_NOTE, TWIST

    return {
        "Secret 节点 props 上的 twist": TWIST,
        "未来人物 props 上的 plot_note": PLOT_NOTE,
        "secret 扩展表的 description": SECRET_DESC,
    }


class Writer:
    """替掉 `draft/generate.py::complete` —— **只替那一次真的模型调用**。

    换在这一层是这一份和 `test_draft_landing.py` 的**唯一实质差别**：那边替掉的是
    `product_draft.draft_chapter` 整个函数，于是约束装配、记忆前言（人物档案 + 近期
    事件 + 滚动总结）、长度策略**一行都没跑**。而记忆前言正是这条链路上最大的一块字，
    也是最可能把 `props` 带出来的一段。
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[Any] = []

    def __call__(
        self, messages: Any, *, config: Any, plan: Any, client: Any = None
    ) -> CompletionResult:
        self.prompts.append(messages)
        return CompletionResult(
            text=self.text,
            model=MODEL,
            finish_reason="stop",
            prompt_tokens=1_200,
            completion_tokens=2_400,
        )


def _prose(tag: str, units: int = BODY_UNITS) -> str:
    """一稿够长的正文（**每一稿的字不一样**，好在对话里认得出是谁漏出来的）。"""
    sentence = f"风雪落在{tag}的肩上，他终于抬起头。"
    return (sentence * (units // len(sentence) + 1))[:units]


def _writer(monkeypatch: pytest.MonkeyPatch, text: str) -> Writer:
    writer = Writer(text)
    monkeypatch.setattr(generate, "complete", writer)
    return writer


def _root(conn: Connection, book: dict[str, str]) -> Path:
    """这本书的稿子在哪儿。**`book` 那个 fixture 不返回它**，从 `project` 那一行读。"""
    row = conn.execute(
        "SELECT root_path FROM project WHERE id = ?", (book["pid"],)
    ).fetchone()
    return Path(str(row["root_path"]))


def _desk(conn: Connection, book: dict[str, str], **kw: Any) -> Any:
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=book["pid"],
        root=_root(conn, book),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL, api_key="sk-test"),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        **kw,
    )


class _NoSummaries:
    def for_range(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def snapshot_watermark(self, project_id: str, chapter_number: int) -> Any:
        return None


@pytest.fixture
def desk_conn(poisoned: dict[str, str]) -> Iterator[Connection]:
    conn = connect(poisoned["db"], check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def _context(
    conn: Connection,
    book: dict[str, str],
    desk: Any,
    *,
    seed_chapter: int = 1,
    **kw: Any,
) -> ToolContext:
    kw.setdefault("working_chapter", 1)
    from novel_harness.calibration.store import CalibrationStore

    store = SqliteStoryGraph(conn)
    _, author_turn = seed_calibration(
        conn=conn,
        project_id=book["pid"],
        store=store,
        root=_root(conn, book),
        chapter=seed_chapter,
    )
    return ToolContext(
        store=store,
        project_id=book["pid"],
        root_path=str(_root(conn, book)),
        drafter=desk,
        events=SqliteEventStore(conn),
        calibrations=CalibrationStore(conn),
        author_turn=author_turn,
        **kw,
    )


def _draft(
    chapter: int,
    call_id: str = "c0",
    calibration_id: str = "calibration:test:seeded",
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="draft_chapter",
        arguments=json.dumps({"chapter": chapter, "calibration_id": calibration_id}),
    )


def every_column(conn: Connection) -> str:
    """`draft_candidate` 的**每一列**拼成一块字。**在出参上搜干净不算数。**"""
    rows = [dict(row) for row in conn.execute("SELECT * FROM draft_candidate").fetchall()]
    assert rows, "表里一行都没有 —— 下面那些「没搜到」是在空字符串上搜的"
    return json.dumps(rows, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 一、毒：**这一次连记忆前言那一段一起扫**
# ══════════════════════════════════════════════════════════════════════════


def test_no_poison_reaches_the_table_on_the_path_that_assembles_memory(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """cast 真的从正文里数出来 ⇒ 记忆前言真的装配 ⇒ 然后**逐列**搜表。

    这是 ADR 0022 结尾点名要先有的那张网，**站在它该站的位置上**：既有那一份走的是
    「不知道这一场有谁」那条退化路，而那条路上 `_with_memory` 直接 return，
    人物档案 / 近期事件 / 滚动总结一段都没进过 prompt。
    """
    writer = _writer(monkeypatch, _prose("甲"))
    desk = _desk(desk_conn, poisoned)
    (outcome,) = dispatch_all([_draft(1)], _context(desk_conn, poisoned, desk))
    assert outcome.ok, outcome.content

    prompt = json.dumps(writer.prompts[0], ensure_ascii=False)
    # **自守卫：这一次真的走了记忆前言那条路。** 少了这一句，下面所有「没搜到」
    # 都可能只是因为那一段根本没跑（既有那份网就正好停在这儿）。
    assert "已确认的故事记忆" in prompt and "【在场人物资料】" in prompt, (
        "这一稿是从退化路上跑出来的 —— 记忆前言那一段没进扫描面"
    )

    stored = every_column(desk_conn)
    leaked = {
        where: poison for where, poison in _poisons(poisoned).items() if poison in stored
    }
    assert not leaked, (
        f"候选表里躺着：{sorted(leaked)}。**它是落盘的**，和对话历史一样不可回收"
        "（ADR 0019 边界一 / ADR 0022 结尾）。"
    )
    assert '"props"' not in stored, "整份节点被序列化进表了"

    # **反向断言：显示名必须在。** 一张什么都搜不到的网和一条什么都没查的链路，
    # 在上面那几个 `not in` 面前长得一模一样。
    assert "血脉秘密" in json.loads(outcome.content)["must_not_reveal"]
    assert "血脉秘密" in prompt


def test_the_same_scan_over_the_three_places_a_candidate_is_handed_out(
    client: TestClient,
    poisoned: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**表干净不等于交出去的东西干净。** 三个口子一起搜：
    `POST …/turn` 的回执、`GET …/drafts`、`GET …/drafts/{id}`（那一条带全文）。

    这三条是作者的浏览器真的会收到的字节，而候选表那张网一个都没覆盖。
    """
    _writer(monkeypatch, _prose("乙"))
    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: DraftOnce())
    pid = poisoned["pid"]

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "第 1 章写一稿"},
    )
    assert turn.status_code == 200, turn.text
    drafts = turn.json()["drafts"]
    assert len(drafts) == 1, drafts

    listing = client.get(f"/api/projects/{pid}/drafts", params={"chapter": 1})
    assert listing.status_code == 200, listing.text
    detail = client.get(f"/api/projects/{pid}/drafts/{drafts[0]['id']}")
    assert detail.status_code == 200, detail.text

    surfaces = {
        "一轮的回执": turn.text,
        "桌上那几稿的列表": listing.text,
        "摊开那一版": detail.text,
    }
    for where, blob in surfaces.items():
        leaked = [name for name, poison in _poisons(poisoned).items() if poison in blob]
        assert not leaked, f"「{where}」这条出参里躺着：{leaked}"

    # 反向断言：这几条出参真的装着那一稿（不是三个空壳上搜出来的干净）。
    assert detail.json()["text"].startswith("风雪落在乙的肩上")
    assert listing.json()["drafts"][0]["ordinal"] == 1


def test_the_scan_would_see_a_candidate_that_carried_the_secret(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上面两条的自守卫。** 让写手把秘密原文写进正文里（模型真的可能这么干），
    断言同一条判据当场红——包括**表**和**三个出参口子共用的那份判据**。

    一个 `return []` 的假实现对任何毒都干净，那不算通过。
    """
    from test_api import TWIST

    _writer(monkeypatch, f"{SELF_NOTE_MARK}：更冷。\n\n{TWIST}。" + _prose("丙"))
    desk = _desk(desk_conn, poisoned)
    (outcome,) = dispatch_all([_draft(1)], _context(desk_conn, poisoned, desk))
    assert outcome.ok, outcome.content

    stored = every_column(desk_conn)
    assert [w for w, poison in _poisons(poisoned).items() if poison in stored], (
        "网看不见一行带毒的候选 —— 上面那两条断言是永远绿的"
    )
    # **这一档不是引擎的错**：正文是模型写的，而引擎既没给它看过 twist（上一条已经
    # 断言 prompt 里没有），也不该改作者/模型的散文。这条探针钉的是「网有牙」。
    # 它顺带说明了那张网**唯一的假阳来源**：模型自己碰巧写出了同一句话。
    assert TWIST in json.loads(outcome.content)["preview"]


# ══════════════════════════════════════════════════════════════════════════
# 二、省下来的那一块，**第一次有一个数**
# ══════════════════════════════════════════════════════════════════════════


def _tool_blob(conversation: Conversation) -> str:
    return "\n".join(m.content for m in conversation.messages if m.role is Role.TOOL)


def test_three_drafts_leave_less_than_one_page_in_the_conversation(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**三稿 ≈ 7,200 字的散文，对话里必须只剩一页纸。**

    ADR 0022 的全部理由是「无状态的 wire 每一轮重发整个消息数组」，所以省下来的量
    要有一个数：这儿量的是**canonical 里那几条工具返回的实际字数**，上限写死在
    `IN_CONVERSATION_UNITS`。

    顺带钉住**正文一个片段都没漏**：预览之后那一段（第 200–400 字）在对话里搜不到。
    """
    body = _prose("丁")
    _writer(monkeypatch, body)
    desk = _desk(desk_conn, poisoned)
    context = _context(desk_conn, poisoned, desk)
    calls = [_draft(1, f"c{n}") for n in range(3)]
    outcomes = dispatch_all(calls, context)
    assert [o.ok for o in outcomes] == [True, True, True], [o.content for o in outcomes]

    conversation = start_conversation().with_author("写三个版本").extended(
        *[
            AgentMessage(role=Role.TOOL, content=o.content, tool_call_id=o.call_id)
            for o in outcomes
        ]
    )
    blob = _tool_blob(conversation)

    assert len(blob) <= 3 * IN_CONVERSATION_UNITS, (
        f"三稿在对话里占了 {len(blob)} 字，上限是 {3 * IN_CONVERSATION_UNITS}。"
        "先问多出来的字是谁的，再决定要不要动那个上限。"
    )
    # 三份加起来还不到那三稿散文的**八分之一** —— 这就是 ADR 0022 买到的东西。
    assert len(blob) * 8 < 3 * len(body)
    assert body[PREVIEW_UNITS + 80 : PREVIEW_UNITS + 200] not in blob, (
        "预览之后那一段正文出现在对话里 —— 硬上限被绕过去了"
    )


@pytest.mark.parametrize(
    ("what", "text"),
    [
        ("一整章不带一个换行", "风" * 4_000),
        ("自述那一行写了五百字", f"{SELF_NOTE_MARK}：{'长' * 500}\n\n" + "风" * 3_000),
        ("开头全是空白", " \n\t" * 200 + "风" * 3_000),
        ("记号出现了很多次（续写把它重复了）", (f"{SELF_NOTE_MARK}：短。\n风" * 300)),
        ("整篇就是一行超长的自述", f"{SELF_NOTE_MARK}{'长' * 4_000}"),
    ],
)
def test_nothing_the_writer_can_do_makes_the_preview_longer(what: str, text: str) -> None:
    """**定长是定长。** 预览和自述都每一轮重发，所以两个都要有硬上限，
    而上限不许被「模型写了个奇怪的形状」绕过去。

    这五种形状里有三种是真会发生的（一整章不换行、续写把记号重复一遍、
    模型把自述写成了一整段），另外两种是边界。
    """
    body, note = split_self_note(text)
    preview = preview_of(body)
    assert len(preview) <= PREVIEW_UNITS + len(PREVIEW_ELLIPSIS), f"{what}：预览 {len(preview)} 字"
    assert len(note) <= SELF_NOTE_UNITS + len(PREVIEW_ELLIPSIS), f"{what}：自述 {len(note)} 字"
    assert SELF_NOTE_MARK not in body, f"{what}：记号跟着正文走了"


def test_the_only_thing_in_a_draft_result_without_a_ceiling_is_the_forbidden_list(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**预览有硬上限，同一份返回里的禁说清单没有。**

    `draft_chapter` 的返回里除了定长的那两样，还带着**这一章的全部秘密显示名**。
    它是有用的（模型据此写），但它随书里的秘密条数线性长，而且**一批三稿就是三份、
    此后每一轮重发**。这一条把今天的形状钉住：一本 40 条秘密的书，一份「不含正文」的
    返回涨到多少字。

    它红 = 有人给这条返回又加了一样没有上限的东西，或者书大了之后这个数已经不能忽略。
    那时该做的是给它一个上限（同 `PREVIEW_UNITS`），不是把这个数调大。
    """
    conn = desk_conn
    store = SqliteStoryGraph(conn)
    for n in range(40):
        store.upsert_node(
            NodeSpec(
                project_id=poisoned["pid"],
                label=NodeLabel.SECRET,
                name=f"第{n}号秘密",
                props=NodeProps.model_validate({}),
                secret=SecretDetail(),
            )
        )
    conn.commit()

    _writer(monkeypatch, _prose("戊"))
    desk = _desk(conn, poisoned)
    (outcome,) = dispatch_all([_draft(1)], _context(conn, poisoned, desk))
    assert outcome.ok, outcome.content
    payload = json.loads(outcome.content)
    assert len(payload["must_not_reveal"]) >= 40, "这本书的秘密没进禁说清单，下面那个数不作数"
    assert len(outcome.content) <= 700, (
        f"一份不含正文的起草返回涨到了 {len(outcome.content)} 字。"
        "定长的是预览和自述，禁说清单没有上限 —— 它一批三份、每一轮重发。"
    )


# ── 顺带：候选按书隔离（这两条新路由收的是一个**模型报的编号**）─────────────


def test_a_draft_never_leaves_the_book_it_was_written_for(
    client: TestClient,
    poisoned: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**候选按书隔离**：另一本书的地址上取不到这一稿，也列不出它。

    这条路由是候选交出去的两个新口子之一，而它收的是一个**模型报的编号**
    （`draft:…` 从对话里来）。按 id 直取的路由不带书这一维的话，一段没进书的正文
    会在另一本书的界面上摊开——而这个仓库的每一条读路径都是按书隔离的
    （`load_project` + `project_id` 参数），新长出来的这一条不许是例外。
    """
    _writer(monkeypatch, _prose("癸"))
    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: DraftOnce())
    pid = poisoned["pid"]
    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "第 1 章写一稿"},
    )
    assert turn.status_code == 200, turn.text
    draft_id = turn.json()["drafts"][0]["id"]

    conn = connect(poisoned["db"])
    try:
        other = project.create(conn, name="另一本书", root_path=str(_root(conn, poisoned))).id
        conn.commit()
    finally:
        conn.close()

    assert client.get(f"/api/projects/{other}/drafts/{draft_id}").status_code == 404
    assert client.get(f"/api/projects/{other}/drafts").json()["drafts"] == []
    # 反向断言：同一个编号在**它自己那本书**上是取得到的（上面那两条不是 404 到别处去了）。
    assert client.get(f"/api/projects/{pid}/drafts/{draft_id}").status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# 三、闸拒了之后，那一稿**还在桌上**（ADR 0022 新长出来的状态）
# ══════════════════════════════════════════════════════════════════════════


def test_a_refused_landing_leaves_the_draft_on_the_desk(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**拒了就不许标 landed。**

    `landed` 在 ADR 0022 之后同时是三件事：界面上的推荐位（唯一判据）、
    清理策略的判据（落过盘的才清）、以及作者眼里「它进书了没有」。
    拒了却标上 = 作者以为书变了（其实没变）、界面替他挑了一个没进书的版本、
    而且那一稿会被 `_prune_landed` 当成「已经有归宿」清掉——**而它根本没有归宿**。

    造的局面是 ADR 0021 那道 sha 闸：起草之后作者自己改了那一章。
    """
    _writer(monkeypatch, _prose("己"))
    desk = _desk(desk_conn, poisoned)
    (outcome,) = dispatch_all([_draft(1)], _context(desk_conn, poisoned, desk))
    draft_id = json.loads(outcome.content)["draft_id"]

    root = _root(desk_conn, poisoned)
    before = importer.read_chapter(root, 1) or ""
    head = importer.single_chapter(before)
    assert head is not None
    (root / importer.chapter_path(1)).write_text(
        importer.chapter_text(head.raw_heading, "作者刚刚自己写的一段。"), encoding="utf-8"
    )

    (landed,) = dispatch_all(
        [ToolCall(id="s0", name="save_draft", arguments=json.dumps({"draft_id": draft_id}))],
        _context(desk_conn, poisoned, desk),
    )
    report = json.loads(landed.content)
    assert report["landed"] is False, report
    assert "作者" in report["note"]

    store = DraftCandidateStore(desk_conn)
    stored = store.get(poisoned["pid"], draft_id)
    assert stored is not None and stored.landed is False, (
        "被闸拒掉的那一稿被标成「已经写进书里」了 —— 推荐位、清理策略、"
        "以及作者眼里的「书变了没有」三件事一起错"
    )
    assert draft_id in {c.id for c in store.recent(poisoned["pid"])}
    # 磁盘上仍然是作者刚写的那一段（一个字节都没被盖掉）。
    assert "作者刚刚自己写的一段" in (importer.read_chapter(root, 1) or "")


def test_the_desk_receipt_marks_only_the_one_that_actually_landed(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """三稿里存了一稿 ⇒ 回执上**只有那一稿** `landed=true`。

    这是界面上「推荐位」的唯一来源（`frontend/src/drafts.ts::openByDefault`）。
    多标一个，作者会看见两版摊开、并以为书里有两版；少标一个，那一章的正文
    已经变了而界面上没有任何字说这件事（ADR 0021 欠他的「看得见」那一半）。
    """
    _writer(monkeypatch, _prose("庚"))
    desk = _desk(desk_conn, poisoned)
    context = _context(desk_conn, poisoned, desk)
    outcomes = dispatch_all([_draft(1, f"c{n}") for n in range(3)], context)
    ids = [json.loads(o.content)["draft_id"] for o in outcomes]

    (saved,) = dispatch_all(
        [ToolCall(id="s0", name="save_draft", arguments=json.dumps({"draft_id": ids[1]}))],
        context,
    )
    assert json.loads(saved.content)["landed"] is True, saved.content
    assert [c.landed for c in desk.produced] == [False, True, False], (
        f"回执上的推荐位标错了：{[(c.ordinal, c.landed) for c in desk.produced]}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 四、屏障：**验的是 `BatchRunner` 真的排队，不是那张表上写着什么**
# ══════════════════════════════════════════════════════════════════════════


def test_the_write_tool_really_waits_for_the_concurrent_window(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`save_draft` 在一批里是**屏障**：前面那几条并发的跑完了才轮到它，而且它跑在
    调用方那条线上。

    既有那条断言看的是 `ToolSpec.concurrent` 这张表（「名单没变」），
    **没有任何东西验过执行器照这张名单排队**。而它排错的后果不是慢：
    落盘那一段（`importer.sync` + `save_chapter` + `decisions.append`）根本没进
    `db_lock`，它靠的正是「屏障成立所以那一刻只有一条线程」这个前提。

    判据是一条时间线，不是秒表：两稿卡在同一个栅栏上（串行必超时），
    而落盘那一笔必须排在**两稿都出来之后**、且在**主线程**上。
    """
    timeline: list[tuple[str, str]] = []
    together = threading.Barrier(2, timeout=5)
    armed = False

    class Barriered(Writer):
        def __call__(self, messages: Any, *, config: Any, plan: Any, **kw: Any):
            timeline.append(("起草进", threading.current_thread().name))
            if armed:
                together.wait()
            timeline.append(("起草出", threading.current_thread().name))
            return super().__call__(messages, config=config, plan=plan, **kw)

    writer = Barriered(_prose("辛"))
    monkeypatch.setattr(generate, "complete", writer)

    lock = threading.RLock()
    desk = _desk(desk_conn, poisoned, db_lock=lock)
    context = _context(desk_conn, poisoned, desk, db_lock=lock)

    (seed,) = dispatch_all([_draft(1, "seed")], context)
    seed_id = json.loads(seed.content)["draft_id"]

    real_land = desk.land

    def traced(candidate_id: str) -> Any:
        timeline.append(("落盘", threading.current_thread().name))
        return real_land(candidate_id)

    monkeypatch.setattr(desk, "land", traced)

    armed = True
    together.reset()
    timeline.clear()
    outcomes = dispatch_all(
        [
            _draft(1, "c0"),
            _draft(1, "c1"),
            ToolCall(id="c2", name="save_draft", arguments=json.dumps({"draft_id": seed_id})),
        ],
        context,
        workers=3,
    )
    assert [o.ok for o in outcomes] == [True, True, True], [o.content for o in outcomes]
    assert not together.broken, "两稿没有同时在跑 —— 栅栏没凑齐"

    steps = [step for step, _ in timeline]
    assert steps.index("落盘") == len(steps) - 1, f"落盘没排在最后：{timeline}"
    landing_thread = next(name for step, name in timeline if step == "落盘")
    assert landing_thread == threading.current_thread().name, (
        f"落盘跑在工作线程「{landing_thread}」上 —— 它那一段没有任何锁罩着"
    )
    drafting_threads = {name for step, name in timeline if step == "起草进"}
    assert len(drafting_threads) == 2, f"两稿跑在同一条线上：{timeline}"


# ══════════════════════════════════════════════════════════════════════════
# 五、闸落在并发窗口的**中间**：账不许少一笔，配对不许错一位
# ══════════════════════════════════════════════════════════════════════════


class DraftOnce:
    """一轮：要一稿，然后收手。"""

    def __init__(
        self,
        chapters: tuple[int, ...] = (1,),
    ) -> None:
        self.chapters = chapters
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ToolCall(
                        id=f"cal{n}",
                        name="calibrate_scene",
                        arguments=json.dumps(_calibrate_args(chapter, n)),
                    )
                    for n, chapter in enumerate(self.chapters)
                ),
            )
        if self.calls == 2:
            results = [
                json.loads(m["content"])
                for m in messages
                if m.get("role") == "tool" and str(m.get("content", "")).strip()
            ]
            ids = [r["id"] for r in results if "id" in r]
            return CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ToolCall(
                        id=f"seal{n}",
                        name="seal_scene_brief",
                        arguments=json.dumps({"inspection_id": iid}),
                    )
                    for n, iid in enumerate(ids)
                ),
            )
        if self.calls == 3:
            results = [
                json.loads(m["content"])
                for m in messages
                if m.get("role") == "tool" and str(m.get("content", "")).strip()
            ]
            pairs = [
                (r["chapter"], r["calibration_id"])
                for r in results
                if "calibration_id" in r
            ]
            return CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=tuple(
                    _draft(pairs[0][0], f"c{n}", pairs[0][1])
                    for n in range(len(self.chapters))
                ),
            )
        return CompletionResult(text="写好了，你看看。", model=MODEL, finish_reason="stop")


def _calibrate_args(chapter: int, index: int) -> dict[str, Any]:
    """同章多稿要多份**签名不同**的校准入参（否则会在校准步先撞重复闸）。"""
    variants = [
        {"chapter": chapter, "viewpoint_surface": "萧决"},
        {"chapter": chapter, "viewpoint_surface": "李管家"},
        {"chapter": chapter, "intended_cast": [{"surface": "萧决"}]},
        {"chapter": chapter, "intended_cast": [{"surface": "李管家"}]},
    ]
    return variants[index % len(variants)]


def test_a_gate_inside_a_concurrent_window_loses_neither_a_bill_nor_a_pairing(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一批四稿**同时跑掉了**，闸在第一条之后才落下来。

    两件事必须同时成立，而它们是相反方向的错：

    1. **账一笔都不许少**（钱已经花了；`settle` 把跑掉的那几条如实记上）；
    2. **`tool_call` 和 `tool_result` 一一对上、且顺序不变**——线性 loop 的全部执行态
       就是「哪几个 `tool_call` 还缺 result」（ADR 0019），错一位，resume 会去补一个
       已经跑过的、而真正缺的那个永远补不上。

    今天没有任何东西同时考这两件事：既有那条并发的账单测试走的是「一切顺利」那条路。
    """
    _writer(monkeypatch, _prose("壬"))
    lock = threading.RLock()
    desk = _desk(desk_conn, poisoned, db_lock=lock)
    context = _context(desk_conn, poisoned, desk, db_lock=lock)

    billed: list[Any] = []
    result = run_turn(
        start_conversation().with_author("第 1 章写四稿"),
        context=context,
        model=DraftOnce((1, 1, 1, 1)),
        ledger=billed.append,
        # **闸选「在原地打转」而不是额度**：额度那道闸在批**开始之前**还查一次
        # （`charged >= max_tokens` 排在 `BatchRunner` 前面），所以它拦得住整批、
        # 落不到窗口中间。`repeat_limit=1` 落得到：四条一模一样的调用，
        # 第一条跑完（那一刻**四条都已经跑掉了**），第二条撞闸。
        limits=TurnLimits(parallel_tools=4, repeat_limit=1),
    )

    assert len(desk.produced) == 4, f"四稿没有都跑掉：{len(desk.produced)}"
    assert len(billed) == 7, (
        f"账上只有 {len(billed)} 笔：校准 + 封存 + 起草三步的 agent 调用 + 四稿。"
        "并发窗口里那几条已经花过钱了，配「没跑」的壳就是一次凭空消失的花销。"
    )

    calls = [
        call
        for message in result.conversation.messages
        if message.tool_calls
        for call in message.tool_calls
    ]
    results = [m for m in result.conversation.messages if m.role is Role.TOOL]
    assert [c.id for c in calls] == [m.tool_call_id for m in results], (
        "`tool_call` 和 `tool_result` 错位了 —— resume 会去补一个已经跑过的"
    )
    assert not result.conversation.pending_calls, "还有 tool_call 没被接住"
    draft_results = [
        m for m in results if "ordinal" in json.loads(m.content)
    ]
    ordinals = [json.loads(m.content)["ordinal"] for m in draft_results]
    assert sorted(ordinals) == [1, 2, 3, 4], f"并发插进去的稿子撞号了：{ordinals}"


# ══════════════════════════════════════════════════════════════════════════
# 六、ADR 0019 边界五 × ADR 0022 的交叉洞（**这一节描述现状，不是批准它**）
# ══════════════════════════════════════════════════════════════════════════


class DraftThenLook:
    """校准（为还没写到的章），然后**把下一步拿到的上下文原样存下来**再收手。

    这就是模型的处境：它下一步能不能封存/起草，取决于那个编号还在不在它眼前。
    """

    def __init__(self, chapter: int) -> None:
        self.chapter = chapter
        self.calls = 0
        self.seen: list[dict[str, Any]] = []

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(
                        id="cal0",
                        name="calibrate_scene",
                        arguments=json.dumps({"chapter": self.chapter}),
                    ),
                ),
            )
        self.seen = list(messages)
        return CompletionResult(text="写好了。", model=MODEL, finish_reason="stop")


def test_a_draft_for_a_chapter_the_author_has_not_reached_loses_its_handle(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**为「作者还没写到的章」校准，那个编号下一步就不在模型眼前了。**

    两条边界叠在一起的结果，两条各自都是对的：

    - 边界五（ADR 0019）：绑在**更后面**的章上的工具返回不进这一次投影
      （判据 `> working_chapter`，方向是 fail-closed 那一侧——过期清单越往后越短）；
    - ADR 0033：校准结果绑章号，**那个编号是封存/起草的唯一把手**
      （`seal_scene_brief` / `draft_chapter` 都只收它）。

    于是「给第 12 章起一稿」在作者的光标停在第 3 章时，校准结果在下一步就被丢掉：
    模型既封存不了、也起草不了——校准这一步不花模型的钱，损失从「一笔起草费」
    提前到「这一轮白走」。

    **这条断言在描述现状，不是在批准它。** 修它要动 `project()`（那是另一个人的地盘），
    所以这儿只把今天的形状钉住：它被修好的那天这条会红，那时该做的是删掉这条测试。
    """
    _writer(monkeypatch, _prose("子"))
    desk = _desk(desk_conn, poisoned)
    # 作者的光标停在第 1 章，而模型给第 3 章起稿（第 3 章磁盘上是有的）。
    context = _context(desk_conn, poisoned, desk, seed_chapter=3, working_chapter=1)
    model = DraftThenLook(chapter=3)
    result = run_turn(
        start_conversation().with_author("给第 3 章也起一稿"),
        context=context,
        model=model,
        ledger=lambda receipt: None,
    )

    assert len(desk.produced) == 0, (
        "校准结果没被投影丢掉 —— 这个洞已经在校准这一环被补上了，这条测试该删"
    )
    assert model.calls == 2, "校准之后没有「下一步」可看"

    seen = json.dumps(model.seen, ensure_ascii=False)
    assert "inspection" not in seen, (
        "校准结果还在下一步的投影里 —— 这个洞被补上了，把这条测试删掉"
    )
    assert result.projection is not None and result.projection.off_chapter == 1
