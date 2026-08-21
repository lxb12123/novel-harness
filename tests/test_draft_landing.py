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
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import novel_harness.agent.drafting as drafting
import novel_harness.api.chat as chat_mod
from novel_harness import importer
from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.ports import DraftAsk, ToolRefused
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
from calibration_seed import seed_calibration

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

    def snapshot_watermark(self, project_id: str, chapter_number: int) -> Any:
        return None


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


def _land(desk: Any, conn: Connection, pid: str, chapter: int) -> tuple[Any, Any]:
    """写一稿，然后把它存进那一章。返回 `(生成回执, 落盘回执)`。

    **ADR 0022 之后这是两个动作**：生成花钱不动书，落盘动书不花钱。这一份量的全是
    第二个动作那几道闸，但每一条都得先有一稿——所以两步都在这儿走完。
    """
    product = _write(desk, conn, pid, chapter)
    return product, desk.land(product.candidate.id)


def _body(desk: Any, product: Any) -> str:
    """那一稿的正文。**它不在生成回执上**（ADR 0022：正文不进对话），按 id 取回来。"""
    return desk.recall(product.candidate.id).body


def _context(conn: Connection, pid: str) -> Any:
    """走 `dispatch` 那条真路径要的上下文（**写入面不在它身上**，边界一）。"""
    from novel_harness.agent.ports import ToolContext
    from novel_harness.calibration.store import CalibrationStore

    store = SqliteStoryGraph(conn)
    _, author_turn = seed_calibration(
        conn=conn,
        project_id=pid,
        store=store,
        root=_root(conn, pid),
        chapter=1,
    )
    return ToolContext(
        store=store,
        project_id=pid,
        root_path=str(_root(conn, pid)),
        drafter=_real_drafter(conn, pid),
        summaries=_NoSummaries(),
        events=SqliteEventStore(conn),
        calibrations=CalibrationStore(conn),
        author_turn=author_turn,
        working_chapter=1,
    )


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    return (
        DraftAsk(chapter=chapter, calibration_id="test:unused"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _write(desk: Any, conn: Connection, pid: str, chapter: int) -> Any:
    ask, ctx = _ask(conn, pid, chapter)
    return desk.write(ask, ctx, goal="写一场对峙")


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
            desk = _drafter(conn, pid, monkeypatch, Drafting(during=act))
            product, report = _land(desk, conn, pid, 1)

            assert report.landed is should_land, f"{name}：落盘与否判错了（{report.note}）"
            if should_land:
                assert DRAFT in _on_disk(conn, pid, 1), f"{name}：该落盘的没落"
            else:
                assert _on_disk(conn, pid, 1) == expected_disk, (
                    f"{name}：**作者的字被盖掉了** —— 这是 ADR 0021 那条不对称的坏那一侧"
                )
                assert "改过" in report.note, f"{name}：拒了却说不出为什么（{report.note}）"
            # 拒不拒，稿子都还在、回执都还在：那一次调用的钱已经花掉了。
            assert _body(desk, product) == DRAFT and len(product.calls) == 1, name
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
            _land(_drafter(conn, pid, monkeypatch, Drafting(during=act)), conn, pid, 1)
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
        desk = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", sync_then_author_types)
        _, report = _land(desk, conn, pid, 1)

        assert report.landed is False, "在 `sync` 和写盘之间那几毫秒里改的字被盖掉了"
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
                assert _land(other, conn, pid, 1)[1].landed is True
            finally:
                monkeypatch.setattr(drafting, "draft_chapter", ours)

        ours = Drafting(during=another_turn_lands_first)
        mine = _drafter(conn, pid, monkeypatch, ours)
        _, report = _land(mine, conn, pid, 1)

        assert report.landed is False, "后落盘的那一稿盖掉了先落盘的那一稿，而它拿的是过期底稿"
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
        assert _land(_drafter(conn, pid, monkeypatch, Drafting()), conn, pid, 1)[1].landed is True
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


def test_the_paragraph_survives_even_without_the_pre_landing_sync(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的自守卫，换了个方向。** 去掉落盘前那一次 `sync()`（只去掉那一次，
    `save_chapter` 自己那次照跑），断言作者那一段**在版本抽屉里仍然找得回来**：
    Task 2 的 `save_chapter` 写盘前发现 DB 落后于磁盘时会先 reconcile 收编。
    也就是说那条退路不再依赖「落盘前记得跑一次 sync」——将来删掉那一次也不会
    让作者的字消失（这条测试会在退路真的断掉时红）。
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
        desk = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", skip_the_first_sync)
        assert _land(desk, conn, pid, 1)[1].landed is True
    finally:
        conn.close()

    texts = [row["text"] for row in client.get(f"/api/projects/{pid}/chapters/1/history").json()]
    assert MINE in texts, "save_chapter 的 stale-DB reconcile 没有把作者那一段收进版本历史"


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

        desk = _drafter(conn, pid, monkeypatch, Drafting())
        product, report = _land(desk, conn, pid, 4)

        assert report.landed is False
        assert _body(desk, product) == DRAFT, "没落点不等于把这一稿扔掉"
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
        notes = [_land(_drafter(conn, pid, monkeypatch, Drafting()), conn, pid, 4)[1].note]
        # 空稿那一档 ADR 0022 之后在**生成**那一步就被拒，而那句话同样会被模型转述给
        # 作者，所以它也得过这张网。
        with pytest.raises(ToolRefused) as empty:
            _write(_drafter(conn, pid, monkeypatch, Drafting(text="  \n ")), conn, pid, 1)
        notes.append(str(empty.value))
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


class DraftThenSave:
    """先校准、封存，再要三稿（一批，**同时跑**），再把三稿逐个存进去。

    ADR 0022 之后这是两个动作，而中间那几个编号得真的传得回来——所以这个假模型
    照真形态办：从上一批工具返回里把编号读出来。
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return _wants_calibrate(1, 2, 3)
        if self.calls == 2:
            results = _tool_results(messages)
            ids = [r["id"] for r in results if "id" in r]
            return _wants_seal(ids)
        if self.calls == 3:
            results = _tool_results(messages)
            pairs = [
                (r["chapter"], r["calibration_id"])
                for r in results
                if "calibration_id" in r
            ]
            return _wants_draft(pairs)
        if self.calls == 4:
            results = _tool_results(messages)
            draft_ids = [r["draft_id"] for r in results if "draft_id" in r]
            return CompletionResult(
                text="",
                model=MODEL,
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ToolCall(
                        id=f"s{n}",
                        name="save_draft",
                        arguments=json.dumps({"draft_id": did}),
                    )
                    for n, did in enumerate(draft_ids)
                ),
            )
        return CompletionResult(text="三章都写好了。", model=MODEL, finish_reason="stop")


def _tool_results(messages: list[Any]) -> list[dict[str, Any]]:
    return [
        json.loads(m["content"])
        for m in messages
        if m.get("role") == "tool" and str(m.get("content", "")).strip()
    ]


def _wants_calibrate(*chapters: int) -> CompletionResult:
    return CompletionResult(
        text="",
        model=MODEL,
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(
                id=f"c{n}",
                name="calibrate_scene",
                arguments=json.dumps({"chapter": n}),
            )
            for n in chapters
        ),
    )


def _wants_seal(inspection_ids: list[str]) -> CompletionResult:
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
            for n, iid in enumerate(inspection_ids)
        ),
    )


def _wants_draft(pairs: list[tuple[int, str]]) -> CompletionResult:
    return CompletionResult(
        text="",
        model=MODEL,
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(
                id=f"d{n}",
                name="draft_chapter",
                arguments=json.dumps({"chapter": chapter, "calibration_id": cid}),
            )
            for n, (chapter, cid) in enumerate(pairs)
        ),
    )


class CalibrateSealDraft:
    """单章版：校准 → 封存 → 起草 → 说话收手（`Scripted` 是静态的，解析不了编号）。"""

    def __init__(self, final_text: str = "写好了。") -> None:
        self.final_text = final_text
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return _wants_calibrate(1)
        if self.calls == 2:
            results = _tool_results(messages)
            return _wants_seal([r["id"] for r in results if "id" in r])
        if self.calls == 3:
            results = _tool_results(messages)
            pairs = [
                (r["chapter"], r["calibration_id"])
                for r in results
                if "calibration_id" in r
            ]
            return _wants_draft(pairs)
        return CompletionResult(
            text=self.final_text,
            model=MODEL,
            finish_reason="stop",
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
    # **三稿是同时写的**（ADR 0022）：这个栅栏要三条线程都到齐才放行，所以串行跑
    # 一定超时 —— 一条不靠计时的并发证据。超时 5 秒是给 CI 的余量，不是节奏。
    together = threading.Barrier(3, timeout=5)
    monkeypatch.setattr(drafting, "draft_chapter", Drafting(during=together.wait))
    pid = book["pid"]
    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: DraftThenSave(),
    )

    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    # **作者的光标在第 3 章**，而不是第 1 章：投影按章号筛（ADR 0019 边界五，判据是
    # `> chapter`），坐标停在第 1 章的话第 2、3 章那两条工具返回下一步就不在上下文里了
    # ——而 ADR 0022 之后**编号是拿回那一稿的唯一把手**，看不见编号就存不进去。
    # 这不是这条测试在绕路，是那两条边界叠在一起的真实形状（报告里记了这一条）。
    turn = client.post(
        f"/api/projects/{pid}/chats/{chat_id}/turn",
        json={"chapter": 3, "said": "把前三章各重写一稿"},
    )
    assert turn.status_code == 200, turn.text
    assert together.n_waiting == 0 and not together.broken, (
        "三稿没有同时在写 —— 栅栏没凑齐（ADR 0022 拆开落盘就是为了这个）"
    )
    assert [(d["chapter"], d["landed"]) for d in turn.json()["drafts"]] == [
        (1, True),
        (2, True),
        (3, True),
    ]

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

        product, report = _land(
            _drafter(conn, pid, monkeypatch, Drafting(during=author_types)), conn, pid, 1
        )
        assert report.landed is False
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
                arguments=json.dumps(
                    {"chapter": 1, "calibration_id": "calibration:test:seeded"}
                ),
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


def test_the_browser_draft_route_writes_no_chapter_but_bills_the_call(
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

    ── 2026-08-12：第三条断言**反过来了** ────────────────────────────────
    这条测试原来量三样「一样都不许动」：磁盘、版本历史、**日志页**。前两样是
    「不落盘」的定义，第三样不是——它把「不动作者的书」和「不记账」混成了一件事，
    于是这条路上**作者花钱最多的动作在账上是零，而且看起来像全部**
    （`docs_dev` 的「已知限制」第一条）。**账那几行本来就该在**，所以这里现在量的是：
    磁盘和版本历史一个字节不动，日志页**恰好多出这一次调用**。
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

    after = _rows(client, pid)
    assert len(after) == before_rows + 1, "`/draft` 花掉的那一次调用又没记账"
    billed = after[0]
    assert billed["source"] == "model_call"
    # 起草没有一张可反查的业务表，所以「为哪一章」只能由这一列答（迁移 009）。
    assert billed["chapter_number"] == 1


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
    """**起草留在对话里的那一份会过期，而 `outdated_manuscript` 认不出它。**

    `chapter_text` 的返回（`ChapterFullText`）过期时，投影会把它换成一句
    「重新读一次」（`agent/loop.py::STALE_MANUSCRIPT`）—— 理由是边界三：
    「第 N 章是什么」不许有两个答案，而**发出去的是过期那个**。

    **2026-08-12（ADR 0022）这个洞缩掉了大半**：`draft_chapter` 的返回里不再有整章
    正文，只剩 id + 定长预览 + 自述。**但它没被补上**——那段预览同样会在作者改完之后
    变旧，判据同样认不出它（`DraftResult` 过不了 `ChapterFullText` 的 `extra="forbid"`）。
    差别只在代价：从「一整章过期正文每一轮重发」缩到「一段开头」。

    这一条**钉的是今天的形状，不是想要的形状**：修的时候它会红，而红的时候要一起
    想清楚的是「同一段字，一个通路擦、一个通路不擦」这件事本身。
    """
    monkeypatch.setattr(drafting, "draft_chapter", Drafting())
    pid = book["pid"]
    seen: list[Any] = []

    class Recording(Scripted):
        def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
            self.calls += 1
            seen.append([dict(m) for m in messages])
            result = self.script[min(self.calls - 1, len(self.script) - 1)]
            if callable(result):
                return result(messages, tools=tools, cancel=cancel)
            return result

    monkeypatch.setattr(
        chat_mod,
        "build_agent_model",
        lambda config, plan: Recording(CalibrateSealDraft()),
    )
    chat_id = client.post(f"/api/projects/{pid}/chats", json={}).json()["id"]
    assert _turn(client, pid, chat_id, 1, "第 1 章重写一稿")["reason"] == "done"

    # 作者读完那一稿，自己把它改了（走的是他按保存那条路）。
    mine = "第一章 血脉\n\n作者读完之后自己重写的那一段。\n"
    base = client.get(f"/api/projects/{pid}/chapters/1/text").json()["text_sha256"]
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": mine, "expected_text_sha256": base},
    )
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
        "起草留下的那段预览被从投影里擦掉了 —— 这一条该改成断言「擦得对」了"
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
    base = client.get(f"/api/projects/{pid}/chapters/1/text").json()["text_sha256"]
    client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": "第一章 血脉\n\n再改一次。\n", "expected_text_sha256": base},
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
        desk = _drafter(conn, pid, monkeypatch, Drafting())
        monkeypatch.setattr(drafting.importer, "sync", fail_the_sync_that_follows_the_write)
        _, report = _land(desk, conn, pid, 1)

        assert report.landed is True, "磁盘是真相源（ADR 0007），写成了就不许说没写"
        assert DRAFT in _on_disk(conn, pid, 1), "前提没成立：那一稿根本没写进磁盘"
    finally:
        conn.close()

    landed = [row for row in _rows(client, pid) if row["title"] == "系统 · 写进正文"]
    assert len(landed) == 1, (
        "作者的第 1 章被改掉了，而日志页上没有这一行 —— "
        "「不挡，但每步留痕」那笔交易只履行了「不挡」那一半"
    )
