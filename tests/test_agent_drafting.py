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
from novel_harness.agent.ports import DraftAsk, LandingReport, ToolRefused
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
        DraftAsk(chapter=chapter, calibration_id="test:unused"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _write(desk: Any, conn: Connection, pid: str, chapter: int) -> Any:
    """`desk.write` 的测试桩：goal 是这一层直传的（模式二里它只来自封存产物）。"""
    ask, ctx = _ask(conn, pid, chapter)
    return desk.write(ask, ctx, goal="写一场对峙")


def _write_and_land(desk: Any, conn: Connection, pid: str, chapter: int) -> LandingReport:
    """写一稿，然后把它存进那一章。

    **ADR 0022 之后这是两个动作**，而这份文件量的是第二个动作那五条闸
    （sha 闸 / 只对已存在的章 / 先 sync / 留章标题 + 验「恰好一章」/ 空稿闸）。
    第一个动作在 `tests/test_draft_candidates.py`。
    """
    product = _write(desk, conn, pid, chapter)
    return desk.land(product.candidate.id)


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
    desk = _drafter(conn, pid, monkeypatch, fake)
    product = _write(desk, conn, pid, 1)
    report = desk.land(product.candidate.id)

    assert report.landed is False
    assert _on_disk(conn, pid, 1) == AUTHORS_OWN_WORDS, "作者刚写的那句话被盖掉了"
    # **说得出为什么**：模型据此决定下一句跟作者说什么，而这句话最终会上屏。
    assert "改过" in report.note and "第 1 章" in report.note
    # 拒了不等于把这一稿扔掉（ADR：稿子还在，由作者决定），**账也不许跟着丢**
    # ——那一次调用的钱已经花掉了。
    assert desk.recall(product.candidate.id).body == DRAFT
    assert [r.capability for r in product.calls] == ["writer"]
    conn.close()


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


def test_a_chapter_nobody_touched_lands(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸只拦「更晚的改动」，不拦正常那一次。**磁盘先、DB 跟。**"""
    conn = connect(book["db"])
    pid = book["pid"]
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())

    report = _write_and_land(desk, conn, pid, 1)

    assert report.landed is True
    on_disk = _on_disk(conn, pid, 1)
    assert DRAFT in on_disk
    assert on_disk in _snapshots(conn, pid, 1), "磁盘写了、`sync` 没跟上 —— 版本历史里没有它"
    assert "第 1 章" in report.note and "版本历史" in report.note
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
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())

    product = _write(desk, conn, pid, 8)
    report = desk.land(product.candidate.id)

    assert report.landed is False
    assert desk.recall(product.candidate.id).body == DRAFT, "没落点不等于把这一稿扔掉"
    assert "还不存在" in report.note and "章标题" in report.note
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
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())

    assert _write_and_land(desk, conn, pid, 1).landed is True

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
    desk = _drafter(conn, pid, monkeypatch, fake)

    assert _write_and_land(desk, conn, pid, 1).landed is True

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
    desk = _drafter(conn, pid, monkeypatch, fake)

    product = _write(desk, conn, pid, 1)
    report = desk.land(product.candidate.id)

    assert report.landed is False
    assert _on_disk(conn, pid, 1) == before, "切不成一章的东西被写进去了"
    assert "切不成恰好一章" in report.note
    assert desk.recall(product.candidate.id).body == fake.text, "拒了也要把这一稿留着"
    conn.close()


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


def test_landing_still_refuses_to_blank_a_chapter(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**空稿闸在落盘那一侧也留着**（3.6 挖出来的那三条之一，一条都不许在重构里丢掉）。

    空的一稿接上章标题**照样切得出恰好一章**，所以上一条那道形状闸拦不住它，
    而拦不住的后果是作者的一整章被一份空白盖掉。生成那一侧已经拒过一次，
    这儿是第二道：`_land` 收的那份 `body` 不是它自己写的，它只保证自己不清空一章。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    before = _on_disk(conn, pid, 1)

    landed, note = drafting._land(
        SqliteStoryGraph(conn),
        conn,
        project_id=pid,
        root=_root(conn, pid),
        chapter=1,
        base_sha=importer.text_digest(before),
        body="   \n\n  ",
        language=DraftLanguage.ZH,
    )

    assert landed is False and "空的" in note
    assert _on_disk(conn, pid, 1) == before, "作者的一章被一份空白盖掉了"
    conn.close()


def test_landing_notes_are_english_for_an_english_book(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_land()` 的 `language` 参数（2026-08-27 补的国际化第三批遗漏）真的接到了输出上——
    不只是表里有英文模板，是**这一条路真的选中它**，同一份判据、同一句空稿闸。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    before = _on_disk(conn, pid, 1)

    landed, note = drafting._land(
        SqliteStoryGraph(conn),
        conn,
        project_id=pid,
        root=_root(conn, pid),
        chapter=1,
        base_sha=importer.text_digest(before),
        body="   \n\n  ",
        language=DraftLanguage.EN,
    )

    assert landed is False
    assert "empty" in note and "空的" not in note
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

    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    assert _write_and_land(desk, conn, pid, 1).landed is True

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
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    assert _write_and_land(desk, conn, pid, 1).landed is True
    conn.close()

    landed = [
        row
        for row in _rows(client, pid)
        if row["title_code"] == "decision_entry_title"
        and row["title_params"] == {"actor": "system", "kind": "chapter_draft"}
    ]
    assert len(landed) == 1, "落盘在日志页上没有一行 —— 「事后可查」在这条路上是空话"
    (row,) = landed
    assert row["actor"] == "system"
    assert row["chapter_number"] == 1
    assert row["subtitle_code"] == "decision_subtitle_chapter_draft"
    assert row["subtitle_params"]["chapter"] == 1
    assert row["jump"] is not None and row["jump"]["chapter_number"] == 1

    by_author = [r for r in _rows(client, pid, actor="author") if r["id"] == row["id"]]
    assert by_author == [], "系统落的盘混进了「作者改的」那一堆里"


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
    _write_and_land(desk, conn, pid, 1)
    conn.close()

    (row,) = [
        r
        for r in _rows(client, pid)
        if r["title_code"] == "decision_entry_title"
        and r["title_params"] == {"actor": "system", "kind": "chapter_draft"}
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
    desk = _drafter(conn, pid, monkeypatch, FakeDrafting())
    product = _write(desk, conn, pid, 1)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("日志表写不进去")

    monkeypatch.setattr(drafting.decisions, "append", boom)
    with pytest.raises(RuntimeError):
        desk.land(product.candidate.id)
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
    assert _write_and_land(desk, conn, pid, 1).landed is True
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
    """**最后一厘米**：`POST …/turn` → 工具 → 起草 → **存进去** → 磁盘。

    这个仓库栽过四次「能力建好了、最后一厘米没接」，而 3.4 交付时
    `ToolContext.drafter` 还是 `None`。这一条量的就是那根线通没通。

    ADR 0022 之后它多了一节：**那根线现在是两截**（`draft_chapter` → `save_draft`），
    而中间那个编号得真的传得回来。假模型照真形态办——从上一条工具返回里读编号，
    读不出来就传一个瞎编的，那样这条测试会以「稿子不在」收场而不是静默变绿。
    """
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    pid = book["pid"]

    class Scripted:
        """先校准、封存，再要一稿，把**那一稿**存进去，最后说话收手。"""

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
                            id="c0",
                            name="calibrate_scene",
                            arguments=json.dumps({"chapter": 1}),
                        ),
                    ),
                )
            if self.calls == 2:
                calibrated = [m for m in messages if m.get("role") == "tool"][-1]
                inspection_id = json.loads(calibrated["content"]).get("id", "inspection:不存在")
                return CompletionResult(
                    text="",
                    model=MODEL,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="seal_scene_brief",
                            arguments=json.dumps({"inspection_id": inspection_id}),
                        ),
                    ),
                )
            if self.calls == 3:
                sealed = [m for m in messages if m.get("role") == "tool"][-1]
                calibration_id = json.loads(sealed["content"]).get(
                    "calibration_id", "calibration:不存在"
                )
                return CompletionResult(
                    text="",
                    model=MODEL,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="c2",
                            name="draft_chapter",
                            arguments=json.dumps(
                                {"chapter": 1, "calibration_id": calibration_id}
                            ),
                        ),
                    ),
                )
            if self.calls == 4:
                drafted = [m for m in messages if m.get("role") == "tool"][-1]
                draft_id = json.loads(drafted["content"]).get("draft_id", "draft:不存在")
                return CompletionResult(
                    text="",
                    model=MODEL,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="c3",
                            name="save_draft",
                            arguments=json.dumps({"draft_id": draft_id}),
                        ),
                    ),
                )
            return CompletionResult(
                text="写好了，已经存进第 1 章。", model=MODEL, finish_reason="stop"
            )

    monkeypatch.setattr(chat_mod, "build_agent_model", lambda config, plan: Scripted())

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "第 1 章重写一稿"},
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["lookups"] == 4

    # 出参上那几稿：界面靠它知道「这一轮写了什么、哪一版进了书」（ADR 0022）。
    drafts = turn.json()["drafts"]
    assert [(d["chapter"], d["ordinal"], d["landed"]) for d in drafts] == [(1, 1, True)]
    assert "text" not in drafts[0], "回执里带了一整章正文 —— 预览存在的意义就没了"

    conn = connect(book["db"])
    assert DRAFT in _on_disk(conn, pid, 1), "浏览器点完，磁盘上那一章一个字都没变"
    conn.close()

    titles = [(row["title_code"], tuple(sorted(row["title_params"].items()))) for row in _rows(client, pid)]
    assert ("decision_entry_title", (("actor", "system"), ("kind", "chapter_draft"))) in titles, (
        "落盘那一行没上日志页"
    )
    assert ("call_entry_title", (("capability", "writer"),)) in titles, (
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
