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
from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.ports import DraftAsk
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


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    """一次起草请求 + 后端算出来的那份约束（**模型碰不到它**，边界二）。"""
    return (
        DraftAsk(chapter=chapter, goal="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _on_disk(conn: Connection, pid: str, chapter: int) -> str:
    return importer.read_chapter(_root(conn, pid), chapter) or ""


def _snapshots(conn: Connection, pid: str, chapter: int) -> list[str]:
    return [
        snapshot.text
        for snapshot in SqliteStoryGraph(conn).chapter_snapshots(pid, chapter)
    ]


# ══════════════════════════════════════════════════════════════════════════
# 一、那道闸 —— ADR 0021 点名要先有的那一条
# ══════════════════════════════════════════════════════════════════════════


AUTHORS_OWN_WORDS = "第一章 血脉\n\n作者在这几十秒里自己写的那一句，从没同步过。\n"


def test_the_gate_refuses_to_overwrite_a_chapter_the_author_touched_later(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**agent 拿着旧底稿、作者刚改过 ⇒ 一个字节都不写，而且说得出为什么。**

    这是 ADR 0021 结尾那条不对称的落点：闸该拒没拒 = 作者的字被盖掉，
    而被盖那一版**如果从没 `sync` 过就是真没了**。局面由 `during` 构造——
    它在「模型正在生成」的那几十秒里落盘，也就是真实世界里作者敲键盘的那个窗口。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    file = _root(conn, pid) / importer.chapter_path(1)

    def author_types() -> None:
        file.write_text(AUTHORS_OWN_WORDS, encoding="utf-8")

    fake = FakeDrafting(during=author_types)
    draft = _drafter(conn, pid, monkeypatch, fake)
    ask, ctx = _ask(conn, pid, 1)

    product = draft(ask, ctx)

    assert product.saved is False
    assert _on_disk(conn, pid, 1) == AUTHORS_OWN_WORDS, "作者刚写的那句话被盖掉了"
    # **说得出为什么**：模型据此决定下一句跟作者说什么，而这句话最终会上屏。
    assert "改过" in product.note and "第 1 章" in product.note
    # 拒了不等于把这一稿扔掉（ADR：交出文本，由作者决定），**账也不许跟着丢**
    # ——那一次调用的钱已经花掉了。
    assert product.text == DRAFT
    assert [r.capability for r in product.calls] == ["writer"]
    conn.close()


def test_without_the_gate_the_authors_words_really_would_be_gone(
    book: dict[str, str]
) -> None:
    """**上一条的自守卫。** 同一个局面、同一条保存路径，只是不带闸——断言作者的字
    **真的会被盖掉**，而且旧那一版在版本历史里也找不回来（它从没同步过）。

    没有这一条，上面那句 `saved is False` 可能只是因为别的什么东西没跑起来。
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
    assert all(AUTHORS_OWN_WORDS not in text for text in _snapshots(conn, pid, 1)), (
        "从没同步过的那一版本来就找不回来 —— 这正是那道闸必须先有测试的原因"
    )
    conn.close()


def test_a_chapter_nobody_touched_lands(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸只拦「更晚的改动」，不拦正常那一次。**磁盘先、DB 跟。**"""
    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())

    product = draft(*_ask(conn, pid, 1))

    assert product.saved is True
    on_disk = _on_disk(conn, pid, 1)
    assert DRAFT in on_disk
    assert on_disk in _snapshots(conn, pid, 1), "磁盘写了、`sync` 没跟上 —— 版本历史里没有它"
    assert "第 1 章" in product.note and "版本历史" in product.note
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 二、章不存在那一档 —— ADR 0021 的范围限制
# ══════════════════════════════════════════════════════════════════════════


def test_a_chapter_that_does_not_exist_yet_is_handed_back_not_created(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """给一本 3 章的书起第 8 章的草：**交出文本 + 说清楚，一个文件都不建。**

    `PUT /chapters/{n}/text` 文件不存在时是 404，而**这条 404 不许为 agent 放开**：
    新开一章要起章标题，而标题是切章的锚（切错了整本书章号会漂）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())

    product = draft(*_ask(conn, pid, 8))

    assert product.saved is False
    assert product.text == DRAFT, "没落点不等于把这一稿扔掉"
    assert "还不存在" in product.note and "章标题" in product.note
    assert not (_root(conn, pid) / importer.chapter_path(8)).exists(), (
        "agent 自己建了一章 —— 章标题是切章的锚，它不是模型的活（ADR 0021）"
    )
    assert SqliteStoryGraph(conn).chapter_snapshots(pid, 8) == []
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 三、章标题 —— 落盘不许动它，也不许写出一份切不成一章的文件
# ══════════════════════════════════════════════════════════════════════════


def test_the_authors_chapter_heading_survives_the_draft(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一稿正文按定义**不带章标题**，而章节文件必须有一行。

    落盘时把作者原来那一行接回去。写不接的话 `sync` 会当场 `SyncRefused`——
    而 `save_chapter` 是**先写盘再 sync**，那时正文已经盖上去了、标题已经没了，
    整本书从那一刻起切不成原来的章数。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())

    assert draft(*_ask(conn, pid, 1)).saved is True

    on_disk = _on_disk(conn, pid, 1)
    assert on_disk.startswith("第一章 血脉"), f"章标题没了：{on_disk[:20]!r}"
    assert importer.single_chapter(on_disk) is not None, "写出去的文件切不成恰好一章"


def test_a_heading_the_model_invented_is_dropped_not_stacked(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型自己写了一行章标题时，**用作者那一行**。

    两行叠起来的文件切出两章 → `sync` 炸 → 而正文已经在磁盘上了。
    换标题也不是模型的活：那一行是切章的依据（同上面那条 404 的理由）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    fake = FakeDrafting(text=f"第一章 换个名字\n\n{DRAFT}")
    draft = _drafter(conn, pid, monkeypatch, fake)

    assert draft(*_ask(conn, pid, 1)).saved is True

    on_disk = _on_disk(conn, pid, 1)
    assert on_disk.startswith("第一章 血脉")
    assert "换个名字" not in on_disk
    assert importer.single_chapter(on_disk) is not None
    conn.close()


def test_a_draft_that_is_two_chapters_is_refused_before_anything_is_written(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一稿里写了两章 ⇒ **写之前就拒**，不是等 `sync` 事后报错。

    `save_chapter` 先写盘再 sync：事后报错那一档里，作者的第 1 章已经被一份切不成
    一章的东西盖住了，他得自己去修章标题才存得回来。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    before = _on_disk(conn, pid, 1)
    fake = FakeDrafting(text=f"第一章 甲\n\n{DRAFT}\n\n第二章 乙\n\n再来一段。")
    draft = _drafter(conn, pid, monkeypatch, fake)

    product = draft(*_ask(conn, pid, 1))

    assert product.saved is False
    assert _on_disk(conn, pid, 1) == before, "切不成一章的东西被写进去了"
    assert "切不成恰好一章" in product.note
    assert product.text == fake.text, "拒了也要把这一稿交出去"
    conn.close()


def test_an_empty_draft_never_blanks_a_chapter(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**空的一稿接上章标题照样切得出恰好一章**，所以上一条那道形状闸拦不住它。

    拦不住的后果是作者的一整章被一份空白盖掉，而「模型什么都没说」是真会发生的一档
    （loop 自己就有一种停法叫 `NO_OUTPUT`）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    before = _on_disk(conn, pid, 1)
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting(text="   \n\n  "))

    product = draft(*_ask(conn, pid, 1))

    assert product.saved is False
    assert _on_disk(conn, pid, 1) == before, "作者的一章被一份空白盖掉了"
    assert "空的" in product.note
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 四、退路是真的 —— 被盖掉那一版进得了版本历史
# ══════════════════════════════════════════════════════════════════════════


def test_the_version_the_draft_replaces_is_snapshotted_first(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR 0021 承诺的退路是「版本历史里退得回去」，这一条让那句话是真的。**

    形态：作者在自己的编辑器里改完第 1 章、**还没同步**（没按保存、没跑 `sync`），
    然后让 agent 起一稿。sha 闸这时是**放行**的（agent 依据的就是磁盘上那份最新的），
    于是他那一版会被盖掉——而它从没进过 `chapter_snapshot`，**盖掉就是真没了**。

    所以落盘之前先跑一次 `sync`：把磁盘上现在那一版收进版本历史，再写。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    unsynced = "第一章 血脉\n\n作者自己改的这一版，还没同步过。\n"
    (_root(conn, pid) / importer.chapter_path(1)).write_text(unsynced, encoding="utf-8")
    assert unsynced not in _snapshots(conn, pid, 1), "前提没成立：这一版已经在快照里了"

    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())
    assert draft(*_ask(conn, pid, 1)).saved is True

    assert unsynced in _snapshots(conn, pid, 1), (
        "被盖掉那一版没进版本历史 —— ADR 0021 的退路在这种形态下是一句空话"
    )
    assert DRAFT in _on_disk(conn, pid, 1)
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 五、账和留痕 —— 两样都要在日志页上看得见
# ══════════════════════════════════════════════════════════════════════════


def _rows(client: TestClient, pid: str, **params: Any) -> list[dict[str, Any]]:
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 100, **params})
    assert page.status_code == 200, page.text
    return list(page.json()["entries"])


def test_landing_a_draft_leaves_a_line_the_author_can_read(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘那一下在 `GET /activity` 上有一行，`actor` 说得出是谁干的。

    这是 ADR 0021 那三样退路的第三样（另外两样是内容寻址快照和版本抽屉）。
    **它必须能被 `actor` 过滤掉**：自动写进来的行会长得比作者自己点的快得多，
    而作者要找的往往是自己那几次。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())
    assert draft(*_ask(conn, pid, 1)).saved is True
    conn.close()

    landed = [row for row in _rows(client, pid) if row["title"] == "系统 · 写进正文"]
    assert len(landed) == 1, "落盘在日志页上没有一行 —— 「事后可查」在这条路上是空话"
    (row,) = landed
    assert row["actor"] == "system"
    assert row["chapter_number"] == 1
    assert "第 1 章" in row["subtitle"], row["subtitle"]
    assert row["jump"] is not None and row["jump"]["chapter_number"] == 1

    by_author = [r for r in _rows(client, pid, actor="author") if r["id"] == row["id"]]
    assert by_author == [], "系统落的盘混进了「作者改的」那一堆里"


def test_the_line_says_nothing_the_author_cannot_read(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """那一行上屏的每个字都过一遍**全仓唯一那份形状判据**。

    判据是 `tests/test_wording_guard.py::dev_shapes`（和 `screenGuard.ts` 同一套网），
    **不在这儿抄第二份**。落盘那一行里天生躺着两样容易漏出去的东西：
    payload 里的 `text_sha256`，和 `chapter_draft` 这个 kind 本身。
    """
    from test_wording_guard import dev_shapes

    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())
    draft(*_ask(conn, pid, 1))
    conn.close()

    (row,) = [r for r in _rows(client, pid) if r["title"] == "系统 · 写进正文"]
    detail = client.get(f"/api/projects/{pid}/activity/{row['id']}").json()
    screen = {
        "title": row["title"],
        "subtitle": row["subtitle"],
        "jump": (row["jump"] or {}).get("label", ""),
        **{f"rows/{r['label']}": f"{r['label']}：{r['value']}" for r in detail["rows"]},
    }
    offenders = {where: dev_shapes(text) for where, text in screen.items() if dev_shapes(text)}
    assert not offenders, f"落盘那一行把研发术语摆到了作者脸上：{offenders}"


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
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())

    product = draft(*_ask(conn, pid, 1))

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
    from novel_harness.agent.ports import ToolRefused

    conn = connect(book["db"])
    pid = book["pid"]

    def boom(ctx: Any, **kwargs: Any) -> ChapterDraft:
        raise ProviderError("连不上")

    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())
    monkeypatch.setattr(drafting, "draft_chapter", boom)

    with pytest.raises(ToolRefused) as caught:
        draft(*_ask(conn, pid, 1))
    assert "没写成" in str(caught.value)
    conn.close()


def test_a_landing_that_cannot_be_logged_is_loud(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**留痕失败不吞**（同 `api/chat.py::_ledger` 那条「记账失败也不吞」）。

    ADR 0021 拿「不挡，但每步留痕」换掉了「事前问一句」。一次**写了作者的正文却没留下
    痕迹**的落盘正好把那笔交易的另一半赖掉了，而它静默失败的形态是
    「作者的书被改了，日志页上没有这一行」——那正是这一整摊要防的东西。

    响一声很吵（这一轮会以一次错误收场），但吵在对的方向。这条断言在于把「吵」钉住：
    有人哪天顺手给它包一个 `except Exception: pass`，这里就红。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(conn, pid, monkeypatch, FakeDrafting())

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("日志表写不进去")

    monkeypatch.setattr(drafting.decisions, "append", boom)
    with pytest.raises(RuntimeError):
        draft(*_ask(conn, pid, 1))
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
    from novel_harness.agent.ports import ToolRefused
    from novel_harness.draft.capabilities import ReasoningEffort

    conn = connect(book["db"])
    pid = book["pid"]
    draft = _drafter(
        conn, pid, monkeypatch, FakeDrafting(),
        endpoint="https://my-own-box.local/v1", model="my-llama",
    )
    monkeypatch.setattr(drafting, "AGENT_DRAFT_REASONING", ReasoningEffort.HIGH)

    with pytest.raises(ToolRefused) as caught:
        draft(*_ask(conn, pid, 1))
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
    draft = _drafter(
        conn, pid, monkeypatch, FakeDrafting(),
        endpoint="https://my-own-box.local/v1", model="my-llama",
    )
    assert draft(*_ask(conn, pid, 1)).saved is True
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 七、产品路径：浏览器发一句话 → 磁盘上那一章真的变了
# ══════════════════════════════════════════════════════════════════════════


def test_one_turn_from_the_browser_really_changes_the_chapter_on_disk(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**最后一厘米**：`POST …/turn` → 工具 → 起草 → 磁盘。

    这个仓库栽过四次「能力建好了、最后一厘米没接」，而 3.4 交付时
    `ToolContext.drafter` 还是 `None`。这一条量的就是那根线通没通。
    """
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    pid = book["pid"]

    class Scripted:
        def __init__(self, *script: CompletionResult) -> None:
            self.script = list(script)
            self.calls = 0

        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            return self.script[min(self.calls - 1, len(self.script) - 1)]

    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Scripted(
            CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(
                        id="c0",
                        name="draft_chapter",
                        arguments=json.dumps({"chapter": 1, "goal": "写一场对峙"}),
                    ),
                ),
            ),
            CompletionResult(text="写好了，已经存进第 1 章。", model=MODEL, finish_reason="stop"),
        ),
    )

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "第 1 章重写一稿"},
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 1

    conn = connect(book["db"])
    assert DRAFT in _on_disk(conn, pid, 1), "浏览器点完，磁盘上那一章一个字都没变"
    conn.close()

    titles = [row["title"] for row in _rows(client, pid)]
    assert "系统 · 写进正文" in titles, "落盘那一行没上日志页"
    assert "模型调用 · 起草" in titles, (
        "起草那一次调用没进账 —— 它走的是 loop 的 `ledger`（`DraftProduct.calls`），"
        "断了的话底栏那个花销数会低估，看起来却像全部"
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
