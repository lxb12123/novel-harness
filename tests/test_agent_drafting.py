"""起草落盘 —— [ADR 0021](../docs/adr/0021-agent-writes-drafts-without-asking.md) 的那道闸。

那份 ADR 自己点名要求先有一条测试，而且说清了为什么是这一条：

> **如果 sha 闸写错（该拒的没拒），作者的字会被盖掉，而快照里只有盖之后那一份的记录**
> ——被盖掉的那一版如果从没被 `sync` 过就是真没了。所以那道闸必须先有测试：
> 构造「agent 拿着旧底稿、作者刚改过」的局面，断言它**不写**、且说得出为什么。

这份文件的第一节就是那一条，**外加它的自守卫**：一条「不带闸的同一次保存」当探针，
断言那种局面下作者的字**真的会被盖掉**——否则第一条断言证明不了任何事
（一个永远绿的守卫比没有守卫更糟，它还提供安全感）。

其余四节量的是这一刀的其他四件「答错了不会有任何东西报错」的事：

2. **章不存在那一档**：ADR 的范围限制。交出文本、说清楚、**一个文件都不许建**。
3. **章标题**：它是切章的锚。落盘不许换掉它，也不许写出一份切不成一章的文件。
4. **退路是真的**：被盖掉那一版必须进得了版本历史，哪怕作者从没同步过它。
5. **账和留痕**：那一次起草的钱进 `model_call`、落盘那一下进 `decision_log`，
   两样都要在 `GET /activity` 上看得见，而且 `actor` 分得开。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.agent.drafting as drafting
import novel_harness.api.chat as chat_mod
from novel_harness import importer
from novel_harness.agent.candidates import DraftCandidateStore
from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.ports import DraftAsk, ToolRefused
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.context import unknown_cast_constraints
from novel_harness.draft.generate import DraftResult
from novel_harness.draft.length import DraftLanguage, LengthStatus, measure
from novel_harness.draft.product_draft import ChapterDraft, memory_receipt
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ProviderError, ToolCall
from novel_harness.extract.call_audit import ModelCallReceipt
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

ENDPOINT = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"

DRAFT = "风雪落在肩上，他终于抬起头。\n\n那一夜谁都没有再说话。"
"""模型交回来的一稿。**没有章标题**——真实形态就是这样，`draft/assemble.py` 的提示词
里一个字都没有要求它写标题。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", ENDPOINT)
    monkeypatch.setenv("NH_LLM_MODEL", MODEL)
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


def _root(conn: Connection, pid: str) -> Path:
    return Path(str(conn.execute(
        "SELECT root_path FROM project WHERE id = ?", (pid,)
    ).fetchone()["root_path"]))


def _receipt(text: str) -> ModelCallReceipt:
    """一份**真形状**的账单原料（`draft/product_draft.py::_receipt` 产出的那种）。"""
    return ModelCallReceipt(
        capability="writer",
        schema_version="m5.draft.v1",
        model=MODEL,
        finish_reason="stop",
        prompt_hash="ph",
        prompt_bytes=b'[{"role":"system"}]',
        text=text,
        prompt_tokens=1_200,
        completion_tokens=2_400,
    )


def _chapter_draft(text: str) -> ChapterDraft:
    """`draft/product_draft.py::draft_chapter` 的出参，装一稿指定的正文。

    **走真的模型（`DraftResult` / `measure`）而不是一个 `SimpleNamespace`**：
    这一层读的是 `drafted.result.text` 和 `drafted.calls`，假出参的字段名写错了
    只会 `AttributeError`，而形状写松了就没人验得到。
    """
    return ChapterDraft(
        result=DraftResult(
            text=text,
            length=measure(text, drafting.AGENT_DRAFT_LENGTH),
            attempts=(),
            truncated=False,
        ),
        memory=memory_receipt("测试装的一稿。"),
        calls=(_receipt(text),),
    )


class FakeDrafting:
    """替掉 `draft/product_draft.py::draft_chapter`（**那一次真的模型调用**）。

    `during` 在「模型正在生成」的那几十秒里跑——第一节要的那个局面
    （作者就在旁边打字）只能在这个位置构造出来。
    """

    def __init__(self, text: str = DRAFT, during: Any = None) -> None:
        self.text = text
        self.during = during
        self.asked: list[Any] = []

    def __call__(self, ctx: Any, **kwargs: Any) -> ChapterDraft:
        self.asked.append(kwargs["request"])
        if self.during is not None:
            self.during()
        return _chapter_draft(self.text)


def _drafter(
    conn: Connection,
    pid: str,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeDrafting,
    *,
    endpoint: str = ENDPOINT,
    model: str = MODEL,
) -> Any:
    """起草台（`agent.ports.DraftDesk`）：**生成 / 落盘 / 读回是三个动作**（ADR 0022）。"""
    monkeypatch.setattr(drafting, "draft_chapter", fake)
    store = SqliteStoryGraph(conn)
    return chapter_drafter(
        store=store,
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=ProviderConfig(base_url=endpoint, model=model),
        capability=resolve_capabilities(endpoint, model),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
    )


class _NoSummaries:
    """滚动总结的只读端口，空的。**这一层不碰它**（记忆前言在 `product_draft` 那一层）。"""

    def for_range(self, project_id: str, first_chapter: int, last_chapter: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first_chapter: int, last_chapter: int) -> list[Any]:
        return []

    def snapshot_watermark(self, project_id: str, chapter_number: int) -> Any:
        return None


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    """一次起草请求 + 后端算出来的那份约束（**模型碰不到它**，边界二）。"""
    return (
        DraftAsk(chapter=chapter, brief="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _write(desk: Any, conn: Connection, pid: str, chapter: int) -> Any:
    """`desk.write` 的测试桩：goal 是这一层直传的（模式二里它只来自封存产物）。"""
    ask, ctx = _ask(conn, pid, chapter)
    return desk.write(ask, ctx)


def _on_disk(conn: Connection, pid: str, chapter: int) -> str:
    return importer.read_chapter(_root(conn, pid), chapter) or ""


def _snapshots(conn: Connection, pid: str, chapter: int) -> list[str]:
    return [
        snapshot.text
        for snapshot in SqliteStoryGraph(conn).chapter_snapshots(pid, chapter)
    ]


# ══════════════════════════════════════════════════════════════════════════
# 一、保存那条路自己会把没同步的那一版收进版本历史（ADR 0021 的退路，今天归作者按的保存管）
# ══════════════════════════════════════════════════════════════════════════


AUTHORS_OWN_WORDS = "第一章 血脉\n\n作者在这几十秒里自己写的那一句，从没同步过。\n"


def test_save_chapter_itself_snapshots_an_unsynced_disk_version_before_overwriting(
    book: dict[str, str],
) -> None:
    """**上一条的自守卫，换了个方向。** 同一个局面、同一条保存路径，只是不带闸——
    作者的字**会被盖掉**（磁盘上），但旧那一版在版本历史里**找得回来**：
    Task 2 的 `save_chapter` 写盘前发现 DB 落后于磁盘时会先 reconcile，
    把作者没同步过的那一版收进库再覆盖（ADR 0021 的退路搬进了保存路径本身）。

    没有这一条，将来有人把那段 reconcile 删掉时，上面那条 `landed is False`
    的测试照样绿——而作者的字会在没人知道的地方消失。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    root = _root(conn, pid)
    (root / importer.chapter_path(1)).write_text(AUTHORS_OWN_WORDS, encoding="utf-8")

    importer.save_chapter(
        SqliteStoryGraph(conn), pid, root, 1, importer.chapter_text("第一章 血脉", DRAFT),
        expected_sha256=None,  # ← 闸关着，也就是作者自己按保存那一档
    )

    assert DRAFT in _on_disk(conn, pid, 1)
    assert AUTHORS_OWN_WORDS not in _on_disk(conn, pid, 1)
    assert any(AUTHORS_OWN_WORDS in text for text in _snapshots(conn, pid, 1)), (
        "save_chapter 的 stale-DB reconcile 没有把作者没同步过的那一版收进版本历史"
    )
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 二、空稿不成候选
# ══════════════════════════════════════════════════════════════════════════


def test_an_empty_draft_never_becomes_a_candidate(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**「模型什么都没说」是真会发生的一档**（loop 自己就有一种停法叫 `NO_OUTPUT`）。

    ADR 0022 之后这一档在**生成**那一步就被拒：一稿空白既落不了盘，也不该占一个编号
    让模型以为手上有东西。**但钱已经花掉了**，所以回执必须跟着拒绝一起交出去
    （`ToolRefused.calls`），否则那笔账和成本闸同时失明。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    before = _on_disk(conn, pid, 1)
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting(text="   \n\n  "))

    with pytest.raises(ToolRefused) as caught:
        _write(desk, conn, pid, 1)

    assert "空的" in str(caught.value)
    assert [r.capability for r in caught.value.calls] == ["writer"], (
        "空稿把那次已经付过钱的调用连回执一起吞了"
    )
    assert _on_disk(conn, pid, 1) == before, "作者的一章被一份空白盖掉了"
    assert DraftCandidateStore(conn).recent(pid) == [], "一稿空白占了一个编号"
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 三、账和留痕 —— 两样都要在日志页上看得见
# ══════════════════════════════════════════════════════════════════════════


def _rows(client: TestClient, pid: str, **params: Any) -> list[dict[str, Any]]:
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 100, **params})
    assert page.status_code == 200, page.text
    return list(page.json()["entries"])


def _author_saves(client: TestClient, pid: str, chapter: int, draft_id: str, body: str) -> None:
    """作者按「保存」把一稿写进那一章（ADR 0048：写盘只走他自己这条路，带上 `draft_id`）。"""
    current = client.get(f"/api/projects/{pid}/chapters/{chapter}/text").json()
    head = importer.single_chapter(current["markdown"])
    assert head is not None
    saved = client.put(
        f"/api/projects/{pid}/chapters/{chapter}/text",
        json={
            "markdown": importer.chapter_text(head.raw_heading, body),
            "expected_text_sha256": current["text_sha256"],
            "draft_id": draft_id,
        },
    )
    assert saved.status_code == 200, saved.text


def test_saving_a_draft_leaves_a_line_the_author_can_read(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者把一稿保存进书那一下在 `GET /activity` 上有一行，`actor` 说得出是谁干的。

    这是 ADR 0021 那三样退路的第三样（另外两样是内容寻址快照和版本抽屉）。
    2026-09-12 起（ADR 0048）写盘是**作者自己按的保存**（`PUT …/text` 带 `draft_id`），
    所以那一行的 actor 是作者——它出现在「作者改的」那一堆里，不再是系统那一堆。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    product = _write(desk, conn, pid, 1)
    conn.close()
    _author_saves(client, pid, 1, product.candidate.id, DRAFT)

    saved = [
        row
        for row in _rows(client, pid)
        if row["title_code"] == "decision_entry_title"
        and row["title_params"] == {"actor": "author", "kind": "chapter_draft"}
    ]
    assert len(saved) == 1, "保存那一稿在日志页上没有一行 —— 「事后可查」在这条路上是空话"
    (row,) = saved
    assert row["actor"] == "author"
    assert row["chapter_number"] == 1
    assert row["subtitle_code"] == "decision_subtitle_chapter_draft"
    assert row["subtitle_params"]["chapter"] == 1
    assert row["jump"] is not None and row["jump"]["chapter_number"] == 1

    by_author = [r for r in _rows(client, pid, actor="author") if r["id"] == row["id"]]
    assert len(by_author) == 1, "作者自己按的保存没进「作者改的」那一堆"
    # 候选表上记了「进书了」——清理策略只清进过书的那些。
    conn = connect(book["db"])
    stored = DraftCandidateStore(conn).get(pid, product.candidate.id)
    conn.close()
    assert stored is not None and stored.landed is True


def test_the_line_never_forwards_the_fingerprint_that_lives_only_in_the_payload(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘那一行天生躺着一样容易漏出去的东西：payload 里的 `text_sha256`。

    国际化第四批·笔二起，题目/副标题/跳转/展开详情都是码 + 参数，渲染发生在前端——
    `kind="chapter_draft"` 这类封闭枚举值本来就会作为参数出现，那是安全的结构化
    数据（前端拿去查 `KIND_LABEL`），不是泄漏，套一遍 `dev_shapes` 会对着它误报。
    真正要防的是 `text_sha256`：`ActivityDetail.payload` 允许带它（版本抽屉靠它对号），
    但 `activity.py` 只往 `subtitle_params` 里塞 `chapter`/`units`，一个字节都不该
    带着这个指纹漏进 title/subtitle/jump/rows 那几处**真的会渲染**的参数。
    渲染整句干不干净（`kind`/`capability` 这类枚举翻出来是不是中文）归
    `DevTerms.guard.test.tsx` 管（同 Phase B 给 `SystemNotifications` 补的那道）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    product = _write(desk, conn, pid, 1)
    conn.close()
    _author_saves(client, pid, 1, product.candidate.id, DRAFT)

    (row,) = [
        r
        for r in _rows(client, pid)
        if r["title_code"] == "decision_entry_title"
        and r["title_params"] == {"actor": "author", "kind": "chapter_draft"}
    ]
    detail = client.get(f"/api/projects/{pid}/activity/{row['id']}").json()
    fingerprint = (detail.get("payload") or {}).get("text_sha256")
    assert fingerprint, "前提没成立：这一行的 payload 里根本没有指纹可查"

    rendered = json.dumps(
        [
            row["title_params"],
            row["subtitle_params"],
            (row["jump"] or {}).get("label_params", {}),
            [r["value_params"] for r in detail["rows"]],
        ]
    )
    assert fingerprint not in rendered, "落盘那一行把只该待在 payload 里的指纹渲染出去了"


def test_the_draft_call_lands_on_the_bill(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """起草那一次调用的回执**由这一层交回去**，不在这一层自己记账。

    `ToolContext` 上没有 conn（边界一），所以起草侧自己记账要么把 conn 塞回去，
    要么记在一个 loop 看不见的地方——而后者正是 `/draft` 那个已知洞的形状：
    日志页显示的是真实花销的一小部分，看起来却像全部。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())

    product = _write(desk, conn, pid, 1)

    assert [(r.capability, r.prompt_tokens, r.completion_tokens) for r in product.calls] == [
        ("writer", 1_200, 2_400)
    ]
    billed = conn.execute(
        "SELECT COUNT(*) AS n FROM model_call WHERE project_id = ? AND capability = 'writer'",
        (pid,),
    ).fetchone()["n"]
    assert billed == 0, (
        "起草侧自己把账记了 —— 那笔钱 loop 看不见，`max_tokens` 那道闸就罩不住它"
    )
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 六、坏掉的那几条路：一次也不许把异常漏给 `run_turn`
# ══════════════════════════════════════════════════════════════════════════


def test_a_provider_failure_becomes_a_refusal_not_a_crash(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_turn` 外面**没有** try/except（那是有意的：bug 不许被吞）。

    所以一次正常的网络故障从这儿漏出去，作者看到的是一次崩溃，而不是
    「这一稿没写成，再试一次」。
    """
    conn = connect(book["db"])
    pid = book["pid"]

    def boom(ctx: Any, **kwargs: Any) -> ChapterDraft:
        raise ProviderError("连不上")

    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    monkeypatch.setattr(drafting, "draft_chapter", boom)

    with pytest.raises(ToolRefused) as caught:
        _write(desk, conn, pid, 1)
    assert "没写成" in str(caught.value)
    conn.close()


def test_a_model_too_small_for_a_whole_chapter_only_breaks_the_draft_tool(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """撑不起整章起草的模型**只让起草这一个工具失败**，不让整段对话 422。

    `plan_call` 因此在每次调用里算，不在工厂里算：算在工厂里的话，
    `POST …/turn` 整个 422，而聊天本来是能用的——作者只会看到「写作助手用不了」。

    这里用的是 reasoning 那一档（`resolve_capabilities` 对没登记的模型只给 `OFF`）：
    起草若照 `/draft` 那样请求 `HIGH`，作者换个自建端点就是每次起草必失败。
    """
    from novel_harness.draft.capabilities import ReasoningEffort

    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(
        conn, pid, monkeypatch, FakeDrafting(),
        endpoint="https://my-own-box.local/v1", model="my-llama",
    )
    monkeypatch.setattr(drafting, "AGENT_DRAFT_REASONING", ReasoningEffort.HIGH)

    with pytest.raises(ToolRefused) as caught:
        _write(desk, conn, pid, 1)
    assert "AI 设置" in str(caught.value)
    conn.close()


def test_the_shipped_reasoning_level_works_on_an_unregistered_endpoint(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的另一半**：产品实际发的那一档（`OFF`）在自建端点上跑得通。

    只有上一条的话，「换成 `HIGH` 会坏」这句话对今天的取值没有任何约束力。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(
        conn, pid, monkeypatch, FakeDrafting(),
        endpoint="https://my-own-box.local/v1", model="my-llama",
    )
    assert DRAFT.startswith(_write(desk, conn, pid, 1).candidate.preview[:8])
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 四、产品路径：浏览器发一句话 → 稿子在桌上、磁盘没动；作者按保存 → 磁盘变了、候选记上「进书了」
# ══════════════════════════════════════════════════════════════════════════


def test_one_turn_from_the_browser_leaves_the_disk_alone_until_the_author_saves(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**最后一厘米**：`POST …/turn` → 工具 → 起草 → 稿子在桌上（磁盘一个字没动）→
    作者按「保存」（带 `draft_id`）→ 磁盘变了、候选表记上进书了、日志页上是他那一行。

    这个仓库栽过四次「能力建好了、最后一厘米没接」。ADR 0021 / 0022 时代最后一厘米是
    「磁盘变了没有」；2026-09-12 起（ADR 0048）写盘是作者自己按的保存，所以这一条量两半：
    一轮跑完磁盘**必须**没变（模型手上没有落盘这个动作），保存之后**必须**变了。
    """
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    pid = book["pid"]

    class Scripted:
        """要一稿，然后说话收手。"""

        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text="",
                    model=MODEL,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="c2",
                            name="draft_chapter",
                            arguments=json.dumps({"chapter": 1, "brief": "第 1 章重写一稿"}),
                        ),
                    ),
                )
            drafted = [m for m in messages if m.get("role") == "tool"][-1]
            assert "landed" not in json.loads(drafted["content"]), drafted["content"]
            return CompletionResult(text="写好了一稿。", model=MODEL, finish_reason="stop")

    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: Scripted())

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "第 1 章重写一稿"},
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 1

    # 出参上那几稿：界面靠它知道「这一轮写了什么」；进没进书由作者的保存决定（ADR 0048）。
    drafts = turn.json()["drafts"]
    assert [(d["chapter"], d["ordinal"], d["landed"]) for d in drafts] == [(1, 1, False)]
    assert "text" not in drafts[0], "回执里带了一整章正文 —— 预览存在的意义就没了"

    conn = connect(book["db"])
    assert DRAFT not in _on_disk(conn, pid, 1), "一轮跑完磁盘就变了 —— 模型手上不该有落盘这个动作"
    conn.close()
    titles = [(row["title_code"], tuple(sorted(row["title_params"].items()))) for row in _rows(client, pid)]
    assert ("call_entry_title", (("capability", "writer"),)) in titles, (
        "起草那一次调用没进账 —— 它走的是 loop 的 `ledger`（`DraftProduct.calls`），"
        "断了的话底栏那个花销数会低估，看起来却像全部"
    )
    assert ("decision_entry_title", (("actor", "author"), ("kind", "chapter_draft"))) not in titles

    # 作者按保存：磁盘变了、候选记上进书了、日志页上是**他**那一行。
    _author_saves(client, pid, 1, drafts[0]["id"], DRAFT)
    conn = connect(book["db"])
    assert DRAFT in _on_disk(conn, pid, 1), "作者按了保存，磁盘上那一章一个字都没变"
    stored = DraftCandidateStore(conn).get(pid, drafts[0]["id"])
    conn.close()
    assert stored is not None and stored.landed is True
    titles = [(row["title_code"], tuple(sorted(row["title_params"].items()))) for row in _rows(client, pid)]
    assert ("decision_entry_title", (("actor", "author"), ("kind", "chapter_draft"))) in titles, (
        "作者保存那一稿在日志页上没有一行"
    )


def test_the_length_the_agent_asks_for_is_the_documented_product_default() -> None:
    """agent 起草用哪一档长度：**ADR 0011 D1 那张表里的产品默认档**，不是新发明的一档。

    三件事一起钉住：
    ① 是 `LengthPolicy` 的产品默认（中文 2,000 / 2,500 / 3,000），不是抄下来的三个数；
    ② **不是** `M2_LENGTH_SPEC`（那一档是考卷的定义，冻结在 EVAL_PROTOCOL，
       上限 3,100）——产品继承它就是拿考卷的参数跑产品；
    ③ `DraftAsk` 上**没有** `length` 字段：长度是作者的意愿（ADR 0013），
       不是模型可以主张的东西。
    """
    from novel_harness.draft.length import DEFAULT_LENGTH_POLICY, M2_LENGTH_SPEC

    assert drafting.AGENT_DRAFT_LENGTH == DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)
    assert drafting.AGENT_DRAFT_LENGTH != M2_LENGTH_SPEC
    assert "length" not in DraftAsk.model_fields, (
        "`DraftAsk` 上长出了 length —— 模型就能在一次工具调用里替作者决定这一章写多长"
    )
    # 自守卫：产品默认档真的落在这一档里，而不是两个都空。
    assert measure("字" * 2_500, drafting.AGENT_DRAFT_LENGTH).status is LengthStatus.WITHIN


def test_the_authors_standing_rules_ride_along_into_the_draft_request(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**作者在「检验规则」那一栏里写的字，写之前就到了模型手上**（2026-09-05）。

    维护者要的那个插槽：「用户在这边写了规则，不管是模式一模式二，特别是在模式二的
    情况下，他自己写内容的时候都要读一下这个规则。」

    ⚠️ **这跟 `write_rule` 不是一回事**：那条是挂在**这段对话**上的文风，会随对话失效；
    这几条是一直挂着的（`validation_rule` 表，也就是保存后那一轮验证要跑的同一份数据）。
    所以这条断言的价值是**「同一行数据两头都用得上」**——写之前提醒、写完之后查。

    这里直接写那张表，因为写它的路由（`POST …/validation-rules`）在 API 层，
    而这一层测的是起草台自己会不会去读；两头之间那条缝由
    `tests/test_rules_fire.py` 从 HTTP 打进去钉住。
    """
    import json as _json

    conn = connect(book["db"])
    pid = book["pid"]
    conn.execute(
        """
        INSERT INTO validation_rule (
            id, project_id, title, template, enabled, blocks_downstream, config_json
        ) VALUES ('vrule:t1', ?, '不许出现「玄铁令」', 'forbidden_literal', 1, 1, ?)
        """,
        (pid, _json.dumps({"literal": "玄铁令"}, ensure_ascii=False)),
    )
    # 停用的那一条**不该进 prompt**：`load_custom_rules` 只读 enabled=1，
    # 这一行同时钉住「停用 = 两条路一起不生效」。
    conn.execute(
        """
        INSERT INTO validation_rule (
            id, project_id, title, template, enabled, blocks_downstream, config_json
        ) VALUES ('vrule:t2', ?, '停用的那条', 'forbidden_literal', 0, 1, ?)
        """,
        (pid, _json.dumps({"literal": "上元节"}, ensure_ascii=False)),
    )
    conn.commit()

    fake = FakeDrafting()
    desk = _drafter(conn, pid, monkeypatch, fake)
    _write(desk, conn, pid, 1)

    assert fake.asked, "起草请求一次都没发出去"
    assert fake.asked[0].standing_rules == ("玄铁令",)
    conn.close()
