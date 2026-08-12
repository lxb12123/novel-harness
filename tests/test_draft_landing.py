"""对抗性验证：起草接线与落盘 —— 这个项目**第一次让机器直接写作者的正文**。

写错的代价不是误报，是丢稿。[ADR 0021](../docs/adr/0021-agent-writes-drafts-without-asking.md)
自己写了那条不对称：

> 如果 sha 闸写错（该拒的没拒），**作者的字会被盖掉，而快照里只有盖之后那一份的记录**
> ——被盖掉的那一版如果从没被 `sync` 过就是真没了。

`tests/test_agent_drafting.py` 量的是这一刀**做到了什么**；这一份量的是它**在哪几种
时序下会失手**，每一节都配一个自守卫探针：把闸换成省事写法，断言这张网当场红。
"""

from __future__ import annotations

import json
import os
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
from novel_harness.draft.length import measure
from novel_harness.draft.product_draft import ChapterDraft, memory_receipt
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.extract.call_audit import ModelCallReceipt
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

ENDPOINT = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"

DRAFT = "风雪落在肩上，他终于抬起头。\n\n那一夜谁都没有再说话。"
"""模型交回来的一稿。**没有章标题** —— `draft/assemble.py` 一个字都没要求它写。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_running_turns() -> Any:
    chat_mod.LIVE.clear()
    yield
    chat_mod.LIVE.clear()


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", ENDPOINT)
    monkeypatch.setenv("NH_LLM_MODEL", MODEL)
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


def _root(conn: Connection, pid: str) -> Path:
    return Path(
        str(conn.execute("SELECT root_path FROM project WHERE id = ?", (pid,)).fetchone()[
            "root_path"
        ])
    )


def _file(conn: Connection, pid: str, chapter: int) -> Path:
    return _root(conn, pid) / importer.chapter_path(chapter)


def _on_disk(conn: Connection, pid: str, chapter: int) -> str:
    return importer.read_chapter(_root(conn, pid), chapter) or ""


def _snapshots(conn: Connection, pid: str, chapter: int) -> list[str]:
    return [s.text for s in SqliteStoryGraph(conn).chapter_snapshots(pid, chapter)]


def _receipt(text: str = DRAFT) -> ModelCallReceipt:
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


class _NoSummaries:
    def for_range(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first: int, last: int) -> list[Any]:
        return []


class Drafting:
    """替掉 `draft/product_draft.py::draft_chapter`（**那一次真的模型调用**）。

    `during` 在「模型正在生成」的那几十秒里跑 —— 作者就在旁边打字的那个窗口，
    只有在这个位置才构造得出来。
    """

    def __init__(self, text: str = DRAFT, during: Any = None) -> None:
        self.text = text
        self.during = during
        self.times = 0

    def __call__(self, ctx: Any, **kwargs: Any) -> ChapterDraft:
        self.times += 1
        if self.during is not None:
            self.during()
        return ChapterDraft(
            result=DraftResult(
                text=self.text,
                length=measure(self.text, drafting.AGENT_DRAFT_LENGTH),
                attempts=(),
                truncated=False,
            ),
            memory=memory_receipt("测试装的一稿。"),
            calls=(_receipt(self.text),),
        )


def _drafter(
    conn: Connection, pid: str, monkeypatch: pytest.MonkeyPatch, fake: Drafting
) -> Any:
    monkeypatch.setattr(drafting, "draft_chapter", fake)
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
    )


def _real_drafter(conn: Connection, pid: str) -> Any:
    """接了线的那个 `DraftFn`，**里面是真的 `product_draft.draft_chapter`**。

    上面那个 `_drafter` 换掉的是「那一次模型调用」，这个不换 —— 要量的是模型调用
    **中途**失败时那笔已经付掉的钱去了哪儿，桩掉整个 `draft_chapter` 就把它桩没了。
    """
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL, api_key="sk-test"),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
    )


def _context(conn: Connection, pid: str) -> Any:
    """走 `dispatch` 那条真路径要的上下文（**写入面不在它身上**，边界一）。"""
    from novel_harness.agent.ports import ToolContext

    return ToolContext(
        store=SqliteStoryGraph(conn),
        project_id=pid,
        root_path=str(_root(conn, pid)),
        drafter=_real_drafter(conn, pid),
        summaries=_NoSummaries(),
        events=SqliteEventStore(conn),
        working_chapter=1,
    )


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    return (
        DraftAsk(chapter=chapter, goal="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、sha 闸的时序穷举 —— 哪一档会盖掉作者的字
# ══════════════════════════════════════════════════════════════════════════

MINE = "第一章 血脉\n\n作者自己敲的这一句，从没同步过。\n"


def _timings(conn: Connection, pid: str) -> dict[str, Any]:
    """六种时序，每一种是「作者在什么时刻动了那一章」。

    返回 `{名字: (安排这次改动的函数, 改完磁盘上应该是什么, 该不该落盘)}`。
    **`None` 表示「作者没动」**，那一档的期望是落盘。
    """
    file = _file(conn, pid, 1)
    before = file.read_text(encoding="utf-8-sig")

    def write(text: str) -> Any:
        def do() -> None:
            file.write_text(text, encoding="utf-8")

        return do

    def same_second() -> None:
        """改内容，**但把修改时间原样按回去**。

        闸如果哪天被改成比 mtime（「文件比我读的时候新吗」），这一档会静默放行——
        而作者一秒钟内改两次、或者编辑器保留 mtime，都不是罕见事。
        """
        stat = file.stat()
        file.write_text(MINE, encoding="utf-8")
        os.utime(file, (stat.st_atime, stat.st_mtime))

    def whitespace() -> None:
        file.write_text(before + "\n", encoding="utf-8")

    def revert() -> None:
        file.write_text("第一章 血脉\n\n改了一下。\n", encoding="utf-8")
        file.write_text(before, encoding="utf-8")

    return {
        "起草中作者改了": (write(MINE), MINE, False),
        "同一秒内改的": (same_second, MINE, False),
        "只改了空白": (whitespace, before + "\n", False),
        "改回原样": (revert, before, True),
        "作者没动": (None, before, True),
    }


def test_the_gate_holds_in_every_timing_the_author_can_type_in(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**五种时序逐个过。** 作者在「模型正在生成」的那几十秒里做了什么，
    决定这一稿落不落得了盘 —— 而判据只有一条：**磁盘上那份还是不是我起草时依据的那份。**

    「同一秒内改的」那一档是这张表里唯一一条**探针性**的：它改了内容却没改 mtime，
    所以任何一个「比时间戳」的省事实现都会在那里静默放行。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    try:
        for name, (act, expected_disk, should_land) in _timings(conn, pid).items():
            _file(conn, pid, 1).write_text(
                "第一章 血脉\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家什么也没说。\n",
                encoding="utf-8",
            )
            product = _drafter(conn, pid, monkeypatch, Drafting(during=act))(*_ask(conn, pid, 1))

            assert product.saved is should_land, f"{name}：落盘与否判错了（{product.note}）"
            if should_land:
                assert DRAFT in _on_disk(conn, pid, 1), f"{name}：该落盘的没落"
            else:
                assert _on_disk(conn, pid, 1) == expected_disk, (
                    f"{name}：**作者的字被盖掉了** —— 这是 ADR 0021 那条不对称的坏那一侧"
                )
                assert "改过" in product.note, f"{name}：拒了却说不出为什么（{product.note}）"
            # 拒不拒都要交出正文和回执：那一次调用的钱已经花掉了。
            assert product.text == DRAFT and len(product.calls) == 1, name
    finally:
        conn.close()


def test_without_the_gate_every_one_of_those_timings_eats_the_authors_words(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的自守卫。** 把闸换成省事写法（「我刚才读的就是它」= 恒放行），
    断言上面每一条该拒的当场变成「作者的字没了」。

    没有这一条，`saved is False` 可能只是因为别的什么东西没跑起来 ——
    一个永远绿的守卫比没有守卫更糟。
    """
    real = importer.save_chapter

    def blind(*args: Any, **kwargs: Any) -> Any:
        kwargs["expected_sha256"] = None  # ← 省事写法：闸恒 True
        return real(*args, **kwargs)

    conn = connect(book["db"])
    pid = book["pid"]
    try:
        monkeypatch.setattr(drafting.importer, "save_chapter", blind)
        eaten = []
        for name, (act, expected_disk, should_land) in _timings(conn, pid).items():
            if should_land:
                continue
            _file(conn, pid, 1).write_text(
                "第一章 血脉\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家什么也没说。\n",
                encoding="utf-8",
            )
            _drafter(conn, pid, monkeypatch, Drafting(during=act))(*_ask(conn, pid, 1))
            if _on_disk(conn, pid, 1) != expected_disk:
                eaten.append(name)
        assert len(eaten) == 3, f"探针没咬住（被吃掉的只有 {eaten}）—— 上一条证明不了任何事"
    finally:
        conn.close()


def test_the_gate_still_holds_in_the_millisecond_between_the_snapshot_and_the_write(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**最后那道缝**：落盘前先跑一次 `sync()`（把作者那一版收进版本历史），
    而作者可以在那一次 `sync` 和真正写盘之间的**那几毫秒**里再敲一次。

    闸如果是「拿 `sync` 之前读到的那份比」，这一档就静默放行了 —— 而
    `save_chapter` 是**读了立刻比、比完立刻写**，所以它应当照拒。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    real_sync = importer.sync

    def sync_then_author_types(*args: Any, **kwargs: Any) -> Any:
        report = real_sync(*args, **kwargs)
        _file(conn, pid, 1).write_text(MINE, encoding="utf-8")
        monkeypatch.setattr(drafting.importer, "sync", real_sync)  # 只插一次队
        return report

    try:
        draft = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", sync_then_author_types)
        product = draft(*_ask(conn, pid, 1))

        assert product.saved is False, "在 `sync` 和写盘之间那几毫秒里改的字被盖掉了"
        assert _on_disk(conn, pid, 1) == MINE
    finally:
        conn.close()


def test_a_second_landing_cannot_use_a_stale_base(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**两次落盘之间**：另一轮（另一个标签页 / 同一批里的下一个工具调用）先落了盘，
    手里这一份的底稿就已经过期了。

    这一档没有作者参与，全是系统自己造出来的并发 —— 而它和「作者刚改过」在磁盘上
    长得一模一样，所以必须由同一道闸接住。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    try:
        theirs = Drafting(text="另一轮抢先写的那一稿。")
        other = _drafter(conn, pid, monkeypatch, theirs)

        def another_turn_lands_first() -> None:
            # 两轮各自的「那一次模型调用」是**两个**桩，换着装（不换就是无限递归）。
            monkeypatch.setattr(drafting, "draft_chapter", theirs)
            try:
                assert other(*_ask(conn, pid, 1)).saved is True
            finally:
                monkeypatch.setattr(drafting, "draft_chapter", ours)

        ours = Drafting(during=another_turn_lands_first)
        mine = _drafter(conn, pid, monkeypatch, ours)
        product = mine(*_ask(conn, pid, 1))

        assert product.saved is False, "后落盘的那一稿盖掉了先落盘的那一稿，而它拿的是过期底稿"
        assert "另一轮抢先写的那一稿。" in _on_disk(conn, pid, 1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 二、丢稿的完整形态 —— 从没 `sync` 过的那一版，事后还找得回来吗
# ══════════════════════════════════════════════════════════════════════════


def test_a_paragraph_the_author_never_synced_is_still_in_the_version_history(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR 0021 全部的退路押在这一条上。**

    形态（真书上最常见的那一种）：作者在自己的编辑器里改 `chapters/*.md`
    （ADR 0007 说那就是稿子），从不按工作台的保存。于是 agent 读到的就是那份最新的，
    **sha 闸放行**，他那一版被盖掉 —— 而它从没进过 `chapter_snapshot`。

    所以量的是**作者真的够得着的那个入口**（`GET …/history`，版本抽屉读的就是它），
    不是库里那张表。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    try:
        _file(conn, pid, 1).write_text(MINE, encoding="utf-8")
        assert MINE not in _snapshots(conn, pid, 1), "前提没成立"
        assert _drafter(conn, pid, monkeypatch, Drafting())(*_ask(conn, pid, 1)).saved is True
    finally:
        conn.close()

    history = client.get(f"/api/projects/{pid}/chapters/1/history")
    assert history.status_code == 200, history.text
    texts = [row["text"] for row in history.json()]
    assert MINE in texts, (
        "从没同步过的那一版在版本抽屉里找不回来 —— ADR 0021 的退路在这种形态下是空话"
    )
    assert not (_root(connect(book["db"]), pid) / importer.chapter_path(1)).read_text(
        encoding="utf-8-sig"
    ).count("从没同步过"), "磁盘上当然没有了（它就是被盖掉的那一版）"


def test_without_the_pre_landing_sync_that_paragraph_is_really_gone(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的自守卫。** 去掉落盘前那一次 `sync()`（只去掉那一次，`save_chapter`
    自己那次照跑），断言作者那一段**在磁盘上、在版本抽屉里、在任何地方都没有了**。

    这是这一整摊里唯一一种「改代码也找不回来」的故障，所以它必须被真的演一遍。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    real_sync = importer.sync
    skipped = {"once": False}

    def skip_the_first_sync(*args: Any, **kwargs: Any) -> Any:
        if not skipped["once"]:
            skipped["once"] = True
            return importer.SyncReport()
        return real_sync(*args, **kwargs)

    try:
        _file(conn, pid, 1).write_text(MINE, encoding="utf-8")
        draft = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", skip_the_first_sync)
        assert draft(*_ask(conn, pid, 1)).saved is True
    finally:
        conn.close()

    texts = [row["text"] for row in client.get(f"/api/projects/{pid}/chapters/1/history").json()]
    assert MINE not in texts, "探针没咬住 —— 上一条证明不了那一次 `sync` 在干活"


# ══════════════════════════════════════════════════════════════════════════
# 三、章不存在那一档 —— 一个字节都不许落在磁盘上
# ══════════════════════════════════════════════════════════════════════════


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_drafting_a_chapter_that_does_not_exist_leaves_the_disk_byte_identical(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一本 3 章的书起第 4 章的草：**整个 `root` 逐字节不变。**

    不是「那个文件不存在」——是**一个文件都没多、没少、没改**。偷偷 `touch` 一个空文件
    会让切章数多一章，而切章数是 M0 的验收判据（章号一漂，图里每一条 `valid_from`
    从此指着另一章，而面板会理直气壮地画出来）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    root = _root(conn, pid)
    try:
        before = _tree(root)
        chapters_before = len(importer.chapter_files(root))

        product = _drafter(conn, pid, monkeypatch, Drafting())(*_ask(conn, pid, 4))

        assert product.saved is False
        assert product.text == DRAFT, "没落点不等于把这一稿扔掉"
        assert _tree(root) == before, "磁盘上有东西变了 —— 章不存在那一档不许写任何字节"
        assert len(importer.chapter_files(root)) == chapters_before
        assert SqliteStoryGraph(conn).chapter_snapshots(pid, 4) == []
    finally:
        conn.close()


def test_what_it_says_about_a_chapter_that_does_not_exist_is_readable_by_a_novelist(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """那句话最终会被模型转述给作者，所以它过一遍**全仓唯一那份形状判据**。

    判据是 `tests/test_wording_guard.py::dev_shapes`（和 `screenGuard.ts` 同一套网），
    **不在这儿抄第二份**。顺带钉住它说的是「作者要自己起章标题」而不是一句
    「保存失败」——后者会让模型下一句跟作者说「我存不进去，你重试一下」。
    """
    from test_wording_guard import dev_shapes

    conn = connect(book["db"])
    pid = book["pid"]
    try:
        notes = [
            _drafter(conn, pid, monkeypatch, Drafting())(*_ask(conn, pid, 4)).note,
            _drafter(conn, pid, monkeypatch, Drafting(text="  \n "))(*_ask(conn, pid, 1)).note,
        ]
    finally:
        conn.close()

    offenders = {note: dev_shapes(note) for note in notes if dev_shapes(note)}
    assert not offenders, f"落盘的回话里有研发术语：{offenders}"
    assert "章标题" in notes[0]


# ══════════════════════════════════════════════════════════════════════════
# 四、记账 —— 一次真的模型调用 = 账上一行 = 日志页一行
# ══════════════════════════════════════════════════════════════════════════


class Scripted:
    def __init__(self, *script: CompletionResult) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        return self.script[min(self.calls - 1, len(self.script) - 1)]


def _wants(*chapters: int) -> CompletionResult:
    return CompletionResult(
        text="",
        model=MODEL,
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(
                id=f"c{n}",
                name="draft_chapter",
                arguments=json.dumps({"chapter": n, "goal": "写一场对峙"}),
            )
            for n in chapters
        ),
    )


def _rows(client: TestClient, pid: str, **params: Any) -> list[dict[str, Any]]:
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 200, **params})
    assert page.status_code == 200, page.text
    return list(page.json()["entries"])


def test_three_drafts_in_one_turn_are_three_lines_on_the_bill(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**N 次起草 = `model_call` N 行 = 日志页 N 行。**

    这条钉的是那个洞的反面：`/draft` 那条路至今一行账都不写，于是底栏显示的是真实
    花销的一小部分、看起来却像全部。起草接进 loop 的 `ledger` 之后，这条路上的钱
    必须逐笔看得见 —— 而「逐笔」的判据是**次数对得上**，不是「有就行」。
    """
    monkeypatch.setattr(drafting, "draft_chapter", Drafting())
    pid = book["pid"]
    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Scripted(_wants(1, 2, 3), CompletionResult(
            text="三章都写好了。", model=MODEL, finish_reason="stop"
        )),
    )

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 1, "said": "把前三章各重写一稿"},
    )
    assert turn.status_code == 200, turn.text

    conn = connect(book["db"])
    billed = conn.execute(
        "SELECT COUNT(*) AS n FROM model_call WHERE project_id = ? AND capability = 'writer'",
        (pid,),
    ).fetchone()["n"]
    conn.close()
    assert billed == 3, f"三稿正文在账上只有 {billed} 行"

    titles = [row["title"] for row in _rows(client, pid)]
    assert titles.count("模型调用 · 起草") == 3, "日志页上数不出三次起草"
    assert titles.count("系统 · 写进正文") == 3, "落盘那三行没上日志页"


def test_a_draft_the_gate_refused_is_still_on_the_bill(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**闸拒了不等于没花钱。** 被拒的那一稿照样是一次真的模型调用。

    账上少这一行的后果不是「少算一点」：它恰好是**作者最想看见的那一次**
    （「我付了钱，而它说没写进去」）。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    try:

        def author_types() -> None:
            _file(conn, pid, 1).write_text(MINE, encoding="utf-8")

        product = _drafter(conn, pid, monkeypatch, Drafting(during=author_types))(
            *_ask(conn, pid, 1)
        )
        assert product.saved is False
        assert [r.capability for r in product.calls] == ["writer"]
    finally:
        conn.close()


def test_a_draft_that_dies_halfway_still_reports_what_it_already_spent(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**中途失败那一档。** ADR 0011 D3 的续写是**第二次**真的模型调用：
    第一次答上来了（钱花了），续写那次断线 ⇒ 这一稿没成。

    `chapter_drafter` 自己的返回值说明写着那条不变式：

    > **拿到正文之后它不再为「落不落得了盘」抛异常**：无论写没写成都带着回执
    > （`calls`）返回，否则那笔账会连同 `ToolOutcome.calls` 一起消失，
    > 而成本闸和日志页同时失明。

    「拿到正文之后」把这一档漏在了外面：第一次调用的钱已经付了，而它一路抛到
    `dispatch`。量到最后一站 —— **`ToolOutcome.calls`**，因为那才是 `agent/loop.py`
    的 `bill()`（记账 + 计闸只有这一个入口）唯一看得见的地方。
    """
    import novel_harness.draft.generate as generate
    from novel_harness.agent.tools import dispatch
    from novel_harness.draft.provider import ProviderError

    conn = connect(book["db"])
    pid = book["pid"]
    sent: list[Any] = []

    def flaky(messages: Any, *, config: Any, plan: Any, client: Any = None) -> CompletionResult:
        sent.append(messages)
        if len(sent) == 1:
            # 远远不到 2,000 字 ⇒ 触发那一次续写（ADR 0011 D3）。
            return CompletionResult(
                text="太短了。",
                model=MODEL,
                finish_reason="stop",
                prompt_tokens=1_200,
                completion_tokens=8,
            )
        raise ProviderError("续写这一次断线了")

    try:
        monkeypatch.setattr(generate, "complete", flaky)
        outcome = dispatch(
            ToolCall(
                id="c0",
                name="draft_chapter",
                arguments=json.dumps({"chapter": 1, "goal": "写一场对峙"}),
            ),
            _context(conn, pid),
        )
    finally:
        conn.close()

    assert len(sent) == 2, "前提没成立：续写那一次根本没发出去"
    assert outcome.ok is False and "没写成" in outcome.content
    assert [(r.capability, r.prompt_tokens, r.completion_tokens) for r in outcome.calls] == [
        ("writer", 1_200, 8)
    ], (
        "第一次调用的钱在账上和成本闸上同时消失了 —— "
        "而 `chapter_drafter` 自己的文档说「无论写没写成都带着回执返回」"
    )


def test_a_refusal_that_cost_nothing_still_reports_nothing(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的自守卫。** 「拒绝也带回执」不许退化成「拒绝一律编一笔账」。

    发出去**之前**就被拒的那几档（参数不合法 / 称呼解析不了 / 模型撑不起整章起草）
    一分钱没花，`ToolOutcome.calls` 必须是空的 —— 记一笔没发生的花销，
    和漏记一笔发生过的，在日志页上是同一种病的两个方向。
    """
    from novel_harness.agent.tools import dispatch

    conn = connect(book["db"])
    pid = book["pid"]
    try:
        context = _context(conn, pid)
        outcomes = [
            dispatch(ToolCall(id="a", name="draft_chapter", arguments="{"), context),
            dispatch(
                ToolCall(id="b", name="draft_chapter", arguments=json.dumps({"chapter": 1})),
                context,
            ),
            dispatch(
                ToolCall(
                    id="c",
                    name="character_state",
                    arguments=json.dumps({"character": "师兄", "chapter": 1}),
                ),
                context,
            ),
        ]
    finally:
        conn.close()

    assert [o.ok for o in outcomes] == [False, False, False]
    assert [o.calls for o in outcomes] == [(), (), ()], "给没花过钱的拒绝编了一笔账"


# ══════════════════════════════════════════════════════════════════════════
# 五、浏览器那条起草路（`/draft`）**不落盘** —— 落盘是 agent 那条路的事
# ══════════════════════════════════════════════════════════════════════════


def test_the_browser_draft_route_still_writes_nothing(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0021 只推翻了「**agent** 写正文要先问」，没有给 `/draft` 加一次作者没按过的保存。

    这条路上作者眼前就是编辑器（他自己决定要不要把这一稿放进去），而行内续写
    （ADR 0015）按定义就不该落盘。**把落盘塞进共用的那一层**（`product_draft.py`）
    是这一刀最容易走错的一步 —— 走错的症状是「点一下 AI 起草，那一章当场被换掉」，
    而作者只会以为编辑器抽风了。

    所以这里量三样：磁盘、版本历史、日志页，**一样都不许动**。
    """
    import novel_harness.draft.generate as generate

    monkeypatch.setattr(
        generate,
        "complete",
        lambda messages, *, config, plan, client=None: CompletionResult(
            text="字" * 2_400, model=MODEL, finish_reason="stop"
        ),
    )
    pid = book["pid"]
    conn = connect(book["db"])
    root = _root(conn, pid)
    before_disk = _tree(root)
    before_snapshots = _snapshots(conn, pid, 1)
    conn.close()
    before_rows = len(_rows(client, pid))

    reply = client.post(
        f"/api/projects/{pid}/chapters/1/draft",
        json={
            "mode": "chapter",
            "goal": "两人在城主府对峙",
            "cast": ["萧决", "李管家"],
            "length": {
                "language": "zh",
                "min_units": 2_000,
                "target_units": 2_500,
                "max_units": 3_000,
            },
        },
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["text"]

    conn = connect(book["db"])
    try:
        assert _tree(root) == before_disk, "`/draft` 把一稿写进了作者的磁盘"
        assert _snapshots(conn, pid, 1) == before_snapshots, "`/draft` 多落了一条版本"
    finally:
        conn.close()
    assert len(_rows(client, pid)) == before_rows, "`/draft` 在日志页上多了一行"


# ══════════════════════════════════════════════════════════════════════════
# 六、边界三：接线之后，对话里多了**第二份正文**，而过期判据还只认第一份
# ══════════════════════════════════════════════════════════════════════════


def _turn(
    client: TestClient, pid: str, chat_id: str, chapter: int, said: str
) -> dict[str, Any]:
    reply = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn", json={"chapter": chapter, "said": said}
    )
    assert reply.status_code == 200, reply.text
    return dict(reply.json())


def test_the_copy_of_the_manuscript_a_draft_leaves_in_the_conversation_is_not_checked(
    client: TestClient,
    book: dict[str, str],
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**接线那天对话里多了第二份正文，而 `outdated_manuscript` 只认第一份。**

    `chapter_text` 的返回（`ChapterFullText`）过期时，投影会把它换成一句
    「重新读一次」（`agent/loop.py::STALE_MANUSCRIPT`）—— 理由是边界三：
    「第 N 章是什么」不许有两个答案，而**发出去的是过期那个**。

    `draft_chapter` 的返回里也躺着一整章正文（连同一句「已经写进第 N 章了」），
    它同样进 `chat_message`、同样会在作者改完那一章之后过期。**判据认不出它**
    （`ChapterFullText` 是 `extra="forbid"`，`DraftResult` 验不过），
    于是它原样发出去。

    这一条**钉的是今天的形状，不是想要的形状**（同
    `test_the_paid_tool_is_not_wired_into_the_product_path_yet` 的写法）：
    修的时候它会红，而红的时候要一起想清楚的是「同一段正文，一个通路擦、
    一个通路不擦」这件事本身。
    """
    monkeypatch.setattr(drafting, "draft_chapter", Drafting())
    pid = book["pid"]
    seen: list[Any] = []

    class Recording(Scripted):
        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            seen.append([dict(m) for m in messages])
            return super().__call__(messages, tools=tools, cancel=cancel)

    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Recording(
            _wants(1),
            CompletionResult(text="写好了。", model=MODEL, finish_reason="stop"),
        ),
    )
    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    assert _turn(client, pid, chat_id, 1, "第 1 章重写一稿")["reason"] == "done"

    # 作者读完那一稿，自己把它改了（走的是他按保存那条路）。
    mine = "第一章 血脉\n\n作者读完之后自己重写的那一段。\n"
    saved = client.put(f"/api/projects/{pid}/chapters/1/text", json={"markdown": mine})
    assert saved.status_code == 200, saved.text

    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Recording(
            CompletionResult(text="好的。", model=MODEL, finish_reason="stop")
        ),
    )
    seen.clear()
    _turn(client, pid, chat_id, 1, "第 1 章现在写的是什么？")

    # 工具返回是一段 JSON，正文里的换行在里面是转义的 —— 所以拿一句不含换行的原文比。
    projected = "\n".join(str(message.get("content", "")) for message in seen[-1])
    assert "风雪落在肩上，他终于抬起头。" in projected, (
        "起草那一份正文被从投影里擦掉了 —— 这一条该改成断言「擦得对」了"
    )
    assert "作者读完之后自己重写的那一段" not in projected, (
        "投影里出现了磁盘上的新版本 —— 那这条已经不是今天的形状了"
    )

    # ── 对照：**同一段正文经另一个工具进来，就会被擦掉** ──────────────────
    # 没有这一半，上面那三行只是在描述「这个仓库没做过期检查」；有了它，
    # 上面那三行说的是「做了，但只对一条通路做」。
    other = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Recording(
            CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(id="r0", name="chapter_text", arguments=json.dumps({"chapter": 1})),
                ),
            ),
            CompletionResult(text="读到了。", model=MODEL, finish_reason="stop"),
        ),
    )
    _turn(client, pid, other, 1, "读一下第 1 章")
    client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": "第一章 血脉\n\n再改一次。\n"},
    )
    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Recording(
            CompletionResult(text="好的。", model=MODEL, finish_reason="stop")
        ),
    )
    seen.clear()
    _turn(client, pid, other, 1, "它现在写的是什么？")

    control = "\n".join(str(message.get("content", "")) for message in seen[-1])
    assert "作者读完之后自己重写的那一段" not in control, (
        "`chapter_text` 那份过期正文没被擦掉 —— 那么上面那半条就不是「只对一条通路做」"
    )
    assert "已经从上下文里清掉" in control


# ══════════════════════════════════════════════════════════════════════════
# 七、写了但没留痕 —— ADR 0021 那笔交易的另一半
# ══════════════════════════════════════════════════════════════════════════


def test_a_landing_that_happened_leaves_its_line_even_when_the_snapshot_did_not(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**磁盘写成了、快照没跟上** —— 那一行日志照样得有。

    `save_chapter` 是「先写盘、再 `sync`」。落盘之后那次 `sync` 撞上别的章节文件被改坏
    （几毫秒的窗口，但它是这条路上唯一一种「写成了却走异常出口」的形态），
    今天这一支**直接返回，跳过 `decisions.append`**。

    结果正是这一整摊要防的那件事：**作者的书被改了，日志页上没有这一行。**
    ADR 0021 拿「不挡，但每步留痕」换掉了「事前问一句」，`_land` 自己的文档也写着
    「一次写了作者的正文却没留下痕迹的落盘正好把那笔交易的另一半赖掉了」——
    而它说的是留痕**失败**要吵，这一支是留痕**根本没发生**，静悄悄的。
    """
    conn = connect(book["db"])
    pid = book["pid"]
    real_sync = importer.sync
    times = {"n": 0}

    def fail_the_sync_that_follows_the_write(*args: Any, **kwargs: Any) -> Any:
        times["n"] += 1
        if times["n"] == 1:
            return real_sync(*args, **kwargs)  # 落盘前那次（把作者当前那版收进历史）
        raise importer.SyncRefused("别的章这几毫秒里被改坏了", path="chapters/0002.md")

    try:
        draft = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", fail_the_sync_that_follows_the_write)
        product = draft(*_ask(conn, pid, 1))

        assert product.saved is True, "磁盘是真相源（ADR 0007），写成了就不许说没写"
        assert DRAFT in _on_disk(conn, pid, 1), "前提没成立：那一稿根本没写进磁盘"
    finally:
        conn.close()

    landed = [row for row in _rows(client, pid) if row["title"] == "系统 · 写进正文"]
    assert len(landed) == 1, (
        "作者的第 1 章被改掉了，而日志页上没有这一行 —— "
        "「不挡，但每步留痕」那笔交易只履行了「不挡」那一半"
    )
