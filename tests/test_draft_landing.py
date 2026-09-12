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


def _body(desk: Any, product: Any) -> str:
    """那一稿的正文。**它不在生成回执上**（ADR 0022：正文不进对话），按 id 取回来。"""
    return desk.recall(product.candidate.id).body


def _context(conn: Connection, pid: str) -> Any:
    """走 `dispatch` 那条真路径要的上下文（**写入面不在它身上**，边界一）。"""
    from novel_harness.agent.ports import ToolContext
    
    store = SqliteStoryGraph(conn)
    return ToolContext(
        store=store,
        project_id=pid,
        root_path=str(_root(conn, pid)),
        drafter=_real_drafter(conn, pid),
        summaries=_NoSummaries(),
        events=SqliteEventStore(conn),
        working_chapter=1,
    )


def _ask(conn: Connection, pid: str, chapter: int) -> tuple[DraftAsk, Any]:
    return (
        DraftAsk(chapter=chapter, brief="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(conn), pid, chapter),
    )


def _write(desk: Any, conn: Connection, pid: str, chapter: int) -> Any:
    ask, ctx = _ask(conn, pid, chapter)
    return desk.write(ask, ctx)


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


# ══════════════════════════════════════════════════════════════════════════
# 二、丢稿的完整形态 —— 从没 `sync` 过的那一版，事后还找得回来吗
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# 三、章不存在那一档 —— 一个字节都不许落在磁盘上
# ══════════════════════════════════════════════════════════════════════════


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


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
    """先要三稿（一批，**同时跑**），再把三稿逐个存进去。

    ADR 0022 之后这是两个动作，而中间那几个编号得真的传得回来——所以这个假模型
    照真形态办：从上一批工具返回里把编号读出来。
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return _wants_draft([1, 2, 3])
        if self.calls == 2:
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


def _wants_draft(chapters: list[int]) -> CompletionResult:
    return CompletionResult(
        text="",
        model=MODEL,
        finish_reason="tool_calls",
        tool_calls=tuple(
            ToolCall(
                id=f"d{n}",
                name="draft_chapter",
                arguments=json.dumps({"chapter": chapter, "brief": f"写第 {chapter} 章的一场对峙"}),
            )
            for n, chapter in enumerate(chapters)
        ),
    )


class DraftThenTalk:
    """单章版：起草 → 说话收手。"""

    def __init__(self, final_text: str = "写好了。") -> None:
        self.final_text = final_text
        self.calls = 0

    def __call__(self, messages: Any, *, tools: Any, cancel: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return _wants_draft([1])
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
    # 进没进书由作者的保存决定（ADR 0048）：一轮跑完三稿都还在桌上。
    assert [(d["chapter"], d["landed"]) for d in turn.json()["drafts"]] == [
        (1, False),
        (2, False),
        (3, False),
    ]

    conn = connect(book["db"])
    billed = conn.execute(
        "SELECT COUNT(*) AS n FROM model_call WHERE project_id = ? AND capability = 'writer'",
        (pid,),
    ).fetchone()["n"]
    conn.close()
    assert billed == 3, f"三稿正文在账上只有 {billed} 行"

    titles = [(row["title_code"], tuple(sorted(row["title_params"].items()))) for row in _rows(client, pid)]
    writer_calls = ("call_entry_title", (("capability", "writer"),))
    assert titles.count(writer_calls) == 3, "日志页上数不出三次起草"
    # 没有一行「写入正文」：这一层不写盘，那一行等作者按保存时才有（ADR 0048）。
    assert not any(code == "decision_entry_title" and dict(params).get("kind") == "chapter_draft"
                   for code, params in titles), "起草这一层往日志页写了「写入正文」那一行"


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
                    {"chapter": 1, "brief": "写一场对峙"}
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
    是这一刀最容易走错的一步 —— 走错的症状是「按一下续写，那一章当场被换掉」，
    而作者只会以为编辑器抽风了。**共用**是关键：那一层今天仍然被模式二的起草工具
    调着，它那条路是要落盘的，所以「谁负责落盘」这条线必须留在调用方这一侧。

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

    # ── 2026-08-26：这个请求体从整章起草改成续写 ────────────────────────────
    #
    # `/draft` 上「起草一整章」那个入口删了（零调用方），这条路只剩行内续写。
    # **量的性质一个字没变**：不落盘、不留版本、账上恰好多一行。续写正是它最该
    # 成立的地方——那一段落进编辑器缓冲区，作者按了 Tab 才是磁盘上的字（D5）。
    #
    # ⚠️ **不能靠「不改也能绿」蒙混**：`DraftRequest` 不是 `extra="forbid"`，
    # 老请求体照旧 200，只是悄悄换成了续写。那样这条测试的名字会开始说假话，
    # 而它是这个文件里唯一一条量「这条路不动作者的书」的。
    reply = client.post(
        f"/api/projects/{pid}/chapters/1/draft",
        json={
            "previous_tail": "夜色沉下来，城主府的灯一盏盏亮起。",
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
        lambda config, plan: Recording(DraftThenTalk()),
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


