"""候选稿那张表 —— **这个仓库第五个「写进去就删不掉」的面**（ADR 0022）。

那份 ADR 结尾把这一条单独拎出来了，而且给了它一条判据：

> **但有一条不对称**：如果候选表泄漏了秘密原文，**它是落盘的**——和对话历史一样
> 不可回收（ADR 0019 边界一）。所以候选表必须先有那张网：喂一本带 `props.twist` /
> `plot_note` / `SecretDetail.description` 的书跑完整链路，**直接查表的每一列**搜那几种毒。
> **这跟在出参上搜不是一回事。**

所以第一节是那张网，而且它是**逐列**搜的（`SELECT *`），不是搜出参。

其余四节量的是这次拆分自己引入的那几件「答错了不会有任何东西报错」的事：

2. **预览是定长的**（没有硬上限的话，一份 500 字预览 × 3 就把省下的吃回去一半）；
3. **自述是写它的那个模型给的**，引擎不替它编一句（ADR 0005 一个字没破）；
4. **一批稿真的是同时跑的**（判据是一个栅栏，不是一个秒表）；
5. **清理只清有归宿的那些**（ADR 0022：不许在作者还可能回头看的时候清）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import novel_harness.agent.drafting as drafting
from novel_harness import importer, project
from novel_harness.agent.candidates import (
    KEEP_LANDED,
    PREVIEW_UNITS,
    DraftCandidateStore,
    preview_of,
)
from novel_harness.agent.drafting import SELF_NOTE_MARK, chapter_drafter, split_self_note
from novel_harness.agent.ports import DraftAsk, ToolContext
from novel_harness.agent.tools import dispatch_all
from novel_harness.db import Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.context import unknown_cast_constraints
from novel_harness.draft.provider import CompletionResult, ProviderConfig, ToolCall
from novel_harness.graph import (
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph

ENDPOINT = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"

# 四种毒：作者写在节点上的东西。**出现在任何一个落盘的面上都是不可回收的泄漏。**
TWIST = "萧决其实是魔尊之子第200章揭晓"
SECRET_DESC = "血脉的真相是他母亲换了孩子"
PLOT_NOTE = "萧决在幽泉窟被顾清音所杀"
POISON = {
    "Secret 节点 props 上的 twist": TWIST,
    "secret 扩展表的 description": SECRET_DESC,
    "未来地点 props 上的 plot_note": PLOT_NOTE,
}

CHAPTER_ONE = "第一章 血脉\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家什么也没说。\n"


@pytest.fixture
def poisoned(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """一本**带毒**的书：秘密带 twist、秘密扩展表带 description、未来地点带 plot_note。

    走生产写路径建（`Ledger` / `upsert_node`），不是手写数据——收窄要防的正是
    `NodeProps` 那个 `extra="allow"`。
    """
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db, check_same_thread=False)
    migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST}),
            secret=SecretDetail(description=SECRET_DESC),
        )
    )
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.LOCATION,
            name="幽泉窟",
            props=NodeProps.model_validate(
                {"first_appears_chapter": 200, "plot_note": PLOT_NOTE}
            ),
        )
    )
    conn.commit()
    (root / "chapters").mkdir(parents=True)
    (root / "chapters" / "0001.md").write_text(CHAPTER_ONE, encoding="utf-8")
    importer.sync(store, pid, root)
    conn.commit()
    yield {"conn": conn, "pid": pid, "root": root, "db": str(db)}
    conn.close()


class FakeWriter:
    """替掉**那一次真的模型调用**（`draft/generate.py::complete`）。

    换在这一层而不是换掉整个 `draft_chapter`：约束装配、记忆前言、长度策略全走真代码
    ——第一节要搜的正是「那条真链路有没有把毒带进表里」。
    """

    def __init__(self, text: str, *, during: Any = None) -> None:
        self.text = text
        self.during = during
        self.prompts: list[Any] = []

    def __call__(
        self, messages: Any, *, config: Any, plan: Any, client: Any = None
    ) -> CompletionResult:
        self.prompts.append(messages)
        if self.during is not None:
            self.during()
        return CompletionResult(
            text=self.text,
            model=MODEL,
            finish_reason="stop",
            prompt_tokens=1_200,
            completion_tokens=2_400,
        )


def _desk(poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch, writer: FakeWriter) -> Any:
    import novel_harness.draft.generate as generate

    monkeypatch.setattr(generate, "complete", writer)
    return chapter_drafter(
        store=SqliteStoryGraph(poisoned["conn"]),
        conn=poisoned["conn"],
        project_id=poisoned["pid"],
        root=poisoned["root"],
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL, api_key="sk-test"),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(poisoned["conn"]),
        summaries=_NoSummaries(),
    )


class _NoSummaries:
    def for_range(self, project_id: str, first: int, last: int) -> list[Any]:
        return []

    def coverage(self, project_id: str, first: int, last: int) -> list[Any]:
        return []


def _ask(poisoned: dict[str, Any], chapter: int) -> tuple[DraftAsk, Any]:
    return (
        DraftAsk(chapter=chapter, goal="写一场对峙"),
        unknown_cast_constraints(SqliteStoryGraph(poisoned["conn"]), poisoned["pid"], chapter),
    )


def every_column(conn: Connection) -> str:
    """`draft_candidate` 的**每一列**拼成一整块字。

    搜出参和搜这块字不是一回事：出参可以事后收窄，**这块字已经落盘了**。
    """
    rows = [dict(row) for row in conn.execute("SELECT * FROM draft_candidate").fetchall()]
    assert rows, "表里一行都没有 —— 下面那些「没搜到」是在一个空字符串上搜的"
    return json.dumps(rows, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 一、毒在不在**落盘的**那张表里（ADR 0022 点名要先有的那一条）
# ══════════════════════════════════════════════════════════════════════════


def test_no_poison_survives_into_the_candidate_table(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**跑完整链路，然后逐列搜毒。**

    候选表是这个仓库第五个「写进去就删不掉」的面。它今天干净不是因为没人往里写毒，
    是因为**它只装模型自己产出的东西和纯量**：没有 prompt、没有约束清单、
    没有 `params_json`。哪天有人为了「查起来方便」往里加一列上下文，这一条会红。
    """
    writer = FakeWriter(f"{SELF_NOTE_MARK} 这一版更冷。\n\n风雪落在肩上。")
    desk = _desk(poisoned, monkeypatch, writer)
    product = desk.write(*_ask(poisoned, 1))
    desk.land(product.candidate.id)

    stored = every_column(poisoned["conn"])
    offenders = [what for what, poison in POISON.items() if poison in stored]
    assert not offenders, (
        f"候选表里出现了：{offenders}\n"
        "它是落盘的 —— 和对话历史一样不可回收（ADR 0019 边界一 / ADR 0022 结尾）。"
    )
    assert '"props"' not in stored, "表里出现了 props —— 那是整份节点被序列化进来了"

    # 反证：这一条不是在一个「什么都没跑到」的库上搜的。约束真的算过（秘密**显示名**
    # 出现在发出去的那份 prompt 里，那是它本来就该在的地方），而候选表里没有它的内容。
    prompt = json.dumps(writer.prompts[0], ensure_ascii=False)
    assert "血脉秘密" in prompt, "这一稿根本没带约束跑 —— 上面那几条「没搜到」是空的"
    assert TWIST not in prompt


def test_the_gate_would_notice_a_candidate_row_that_carried_context(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**上一条的自守卫。** 往表里塞一份带毒的「上下文」（最自然的那种坏写法：
    「把 prompt 也存下来，查起来方便」），断言同一条判据当场红。

    没有它，上面那条可能只是因为链路根本没写进表。
    """
    store = DraftCandidateStore(poisoned["conn"])
    store.put(
        poisoned["pid"],
        chapter=1,
        body=f"风雪落在肩上。（当时的 prompt：{TWIST}）",
        note="这一版更冷。",
    )
    stored = every_column(poisoned["conn"])
    assert [what for what, poison in POISON.items() if poison in stored], (
        "网看不见一行带毒的候选 —— 上面那条断言是永远绿的"
    )


# ══════════════════════════════════════════════════════════════════════════
# 二、预览是定长的（ADR 0022 的「代价」第四条）
# ══════════════════════════════════════════════════════════════════════════


def test_the_preview_is_a_hard_number_not_the_whole_chapter(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一稿三千字，**进对话的只有开头那一段**。

    ADR 0022 点名了这条：「预览长度必须是一个写死的数并有测试。没有硬上限的话，
    一份 500 字预览 × 3 就把省下的吃回去一半」。而**每一轮都会重发一次**，
    所以它的价格是「这个数 × 稿数 × 剩下的每一轮」。
    """
    whole_chapter = "风雪落在肩上。" * 500
    desk = _desk(poisoned, monkeypatch, FakeWriter(whole_chapter))
    product = desk.write(*_ask(poisoned, 1))

    preview = product.candidate.preview
    assert len(preview) <= PREVIEW_UNITS + 2, f"预览有 {len(preview)} 字，硬上限不管用"
    assert preview.startswith("风雪落在肩上。")
    assert preview.endswith("……"), "截断没有记号 —— 读起来像「他只写了这么多」"
    # 全文还在，只是要单取一次（`read_draft`）。
    assert desk.recall(product.candidate.id).body == whole_chapter


def test_the_tool_result_never_carries_the_chapter(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**贴回对话的那段字里没有整章正文**（ADR 0022 的整个理由）。

    无状态的 wire 每一轮把整个消息数组从头重发，所以一整章正文进了对话就是
    **每一轮都在付它的钱**，直到会话结束——而默认没有任何东西会去拿掉它。
    """
    whole_chapter = "风雪落在肩上。" * 500
    desk = _desk(poisoned, monkeypatch, FakeWriter(whole_chapter))
    context = ToolContext(
        store=SqliteStoryGraph(poisoned["conn"]),
        project_id=poisoned["pid"],
        root_path=str(poisoned["root"]),
        drafter=desk,
        working_chapter=1,
    )
    (outcome,) = dispatch_all(
        [
            ToolCall(
                id="c0",
                name="draft_chapter",
                arguments=json.dumps({"chapter": 1, "goal": "写一场对峙"}),
            )
        ],
        context,
    )
    assert outcome.ok, outcome.content
    assert whole_chapter not in outcome.content
    assert len(outcome.content) < 600, f"贴回对话的那段字有 {len(outcome.content)} 字"

    # 而 `read_draft` 是**显式**要全文的那一次，它当然给。
    (full,) = dispatch_all(
        [
            ToolCall(
                id="c1",
                name="read_draft",
                arguments=json.dumps({"draft_id": json.loads(outcome.content)["draft_id"]}),
            )
        ],
        context,
    )
    assert full.ok and whole_chapter in json.loads(full.content)["text"]


def test_a_draft_result_is_bound_to_its_chapter_even_when_the_call_has_no_chapter(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**投影按章号筛，而 `save_draft` / `read_draft` 的入参里没有章号**（只有编号）。

    只看入参的话它们的 `chapter` 恒为 `None` ⇒ 一条都不筛 ⇒ 一稿第 200 章的正文会跟着
    模型回头写第 40 章（ADR 0019 边界五点名的那个「等着发生的跨章泄漏」）。
    所以出参上有一个叫 `chapter` 的整数就取它——同一条结构判断的另一半，
    **不是一张「哪个工具绑章号」的表**（表会在加工具的那天漂）。
    """
    desk = _desk(poisoned, monkeypatch, FakeWriter("风雪落在肩上。"))
    context = ToolContext(
        store=SqliteStoryGraph(poisoned["conn"]),
        project_id=poisoned["pid"],
        root_path=str(poisoned["root"]),
        drafter=desk,
        working_chapter=1,
    )
    (drafted,) = dispatch_all(
        [
            ToolCall(
                id="c0",
                name="draft_chapter",
                arguments=json.dumps({"chapter": 1, "goal": "写一场对峙"}),
            )
        ],
        context,
    )
    draft_id = json.loads(drafted.content)["draft_id"]
    outcomes = dispatch_all(
        [
            ToolCall(id="c1", name="read_draft", arguments=json.dumps({"draft_id": draft_id})),
            ToolCall(id="c2", name="save_draft", arguments=json.dumps({"draft_id": draft_id})),
        ],
        context,
    )
    assert [o.chapter for o in outcomes] == [1, 1], (
        "候选那两条返回没绑章号 —— 投影筛不到它们，一稿别章的正文会跟着走"
    )


def test_preview_of_is_the_only_implementation() -> None:
    """预览只有一份实现：模型看见的和界面看见的必须是同一段字。"""
    assert preview_of("短的") == "短的"
    long_one = "字" * (PREVIEW_UNITS + 50)
    assert preview_of(long_one) == "字" * PREVIEW_UNITS + "……"


# ══════════════════════════════════════════════════════════════════════════
# 三、自述由写它的那个模型给（引擎不给散文打分）
# ══════════════════════════════════════════════════════════════════════════


def test_the_note_comes_from_the_writer_in_the_same_call(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**同一次调用里白送的三十个字**（ADR 0022）。

    对话里那个模型没看过正文——它要说「我推荐第二版，因为更冷」就得先把 9,000 字
    读进上下文，也就是把省下来的钱原样花回去。所以让写手自己交一句。

    两件事一起钉：那句话真的被送出去要了（prompt 里有那个记号），
    而**它不进正文**（存进书里的那一章不许出现那一行）。
    """
    writer = FakeWriter(f"{SELF_NOTE_MARK}：这一版更冷，删掉了那段回忆。\n\n风雪落在肩上。")
    desk = _desk(poisoned, monkeypatch, writer)
    product = desk.write(*_ask(poisoned, 1))

    assert product.candidate.note == "这一版更冷，删掉了那段回忆。"
    assert SELF_NOTE_MARK in json.dumps(writer.prompts[0], ensure_ascii=False), (
        "没要过自述，那句话是从哪儿来的？"
    )
    assert desk.land(product.candidate.id).landed is True
    on_disk = importer.read_chapter(poisoned["root"], 1) or ""
    assert SELF_NOTE_MARK not in on_disk, "自述那一行进了作者的书"
    assert "风雪落在肩上。" in on_disk


def test_a_writer_that_ignores_the_ask_gets_no_invented_note(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**模型没写自述就是没有**，这一层不替它编一句（ADR 0005：引擎不给散文打分）。

    编出来的那一句会被作者当成模型的判断去挑版本——那是这个仓库最不该长出来的能力。
    """
    desk = _desk(poisoned, monkeypatch, FakeWriter("风雪落在肩上。\n\n那一夜谁都没说话。"))
    product = desk.write(*_ask(poisoned, 1))

    assert product.candidate.note == ""
    assert desk.recall(product.candidate.id).body.startswith("风雪落在肩上。"), (
        "认不出自述时**一个字都不许动正文**"
    )


def test_the_continuation_cannot_smuggle_a_second_note_into_the_book() -> None:
    """**续写那一次会把记号再写一遍**（ADR 0011 D3：答太短就接着写，而那一次的提示里
    还带着同一句「先写一行自述」）。两段是直接拼起来的，所以第二处多半**不在行首**。

    照「只切第一行」处理的话，那半句会跟着正文进作者的书。实测形态就是下面这一段。
    """
    body, note = split_self_note(
        f"{SELF_NOTE_MARK}：更冷。\n\n风雪落在肩上。{SELF_NOTE_MARK}：接着写。\n\n他抬起头。"
    )
    assert SELF_NOTE_MARK not in body
    assert body == "风雪落在肩上。\n\n他抬起头。"
    assert note == "更冷。", "自述取第一处，后面那几处是续写重复的"


def test_a_note_that_runs_long_is_cut_not_refused() -> None:
    """自述也每一轮重发，所以它也有硬上限。写长了截断——那是它的散文，不是参数。"""
    body, note = split_self_note(f"{SELF_NOTE_MARK}{'长' * 200}\n\n正文。")
    assert len(note) <= drafting.SELF_NOTE_UNITS + 2
    assert body == "正文。"


# ══════════════════════════════════════════════════════════════════════════
# 四、一批稿是同时跑的（判据是栅栏，不是秒表）
# ══════════════════════════════════════════════════════════════════════════


def test_three_drafts_in_one_batch_really_run_at_the_same_time(
    poisoned: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**ADR 0022 的第四个好处**：起草没有副作用了，三稿同时跑。

    判据是一个三方栅栏——串行跑的话第一稿会卡在那儿等永远不会来的另外两稿，
    五秒后 `BrokenBarrierError`。**不用秒表**：秒表在慢机器上会假红，而假红的守卫
    会被人关掉。

    顺带钉住这条并发**在库那一侧是安全的**：三条线程共用一条 SQLite 连接，
    而 `check_same_thread=False` 并不让连接变成线程安全的（实测：同一句 SQL
    两条线程同时跑，几百次之内必 `InterfaceError`）。碰库的每一段都在
    `ToolContext.db_lock` 那道队里，慢的那一段（模型调用）在队外面。
    """
    together = threading.Barrier(3, timeout=5)
    desk = _desk(poisoned, monkeypatch, FakeWriter("风雪落在肩上。", during=together.wait))
    lock = threading.RLock()
    context = ToolContext(
        store=SqliteStoryGraph(poisoned["conn"]),
        project_id=poisoned["pid"],
        root_path=str(poisoned["root"]),
        drafter=desk,
        working_chapter=1,
        db_lock=lock,
    )
    # 起草台和 `ToolContext` 必须共用同一把锁，否则各排各的队 = 没排。
    desk._db_lock = lock  # noqa: SLF001 —— 装配层做的事，这儿手动装一次
    desk._candidates = DraftCandidateStore(poisoned["conn"], lock=lock)  # noqa: SLF001

    calls = [
        ToolCall(
            id=f"c{n}",
            name="draft_chapter",
            arguments=json.dumps({"chapter": 1, "goal": f"第 {n} 稿"}),
        )
        for n in range(3)
    ]
    outcomes = dispatch_all(calls, context, workers=3)

    assert [o.ok for o in outcomes] == [True, True, True], [o.content for o in outcomes]
    assert not together.broken, "三稿没有同时在跑 —— 栅栏没凑齐"
    # **编号不许撞**：三条线程同时往同一章插稿，`ordinal` 在同一个事务里取 MAX+1。
    ordinals = sorted(json.loads(o.content)["ordinal"] for o in outcomes)
    assert ordinals == [1, 2, 3], f"并发插进去的稿子撞号了：{ordinals}"


def test_a_tool_with_side_effects_is_never_run_concurrently() -> None:
    """**并发的判据是一张表上的声明，而它默认是关的**（fail-closed）。

    `save_draft` 写作者的书：它在一批里是**屏障**——前面那批并发的跑完了才轮到它。
    这条断言看的是那张表本身，因为「哪几条能并发」一旦变成一份口头约定，
    加工具的那天没有任何东西会提醒谁去想这件事。
    """
    from novel_harness.agent.tools import TOOL_TABLE

    concurrent = {spec.name for spec in TOOL_TABLE if spec.concurrent}
    assert concurrent == {"draft_chapter"}, (
        f"能并发的工具变了：{sorted(concurrent)}。加之前先回答两个问题——"
        "它跑三遍和跑一遍对世界的影响一样吗？它慢到值得为它多担一份线程的心吗？"
    )
    assert "save_draft" not in concurrent, "落盘并发 = 三条线程同时写作者的同一章"


# ══════════════════════════════════════════════════════════════════════════
# 五、清理：只清有归宿的那些
# ══════════════════════════════════════════════════════════════════════════


def test_cleanup_never_touches_a_draft_the_author_might_still_pick(
    poisoned: dict[str, Any],
) -> None:
    """ADR 0022：**不许在作者还可能回头看的时候清。**

    所以判据不是「旧」，是「**它已经有归宿了**」——落过盘的那一稿在磁盘上、在版本
    历史里，删掉这一行不丢任何东西；没落过盘的那些才是他可能回头挑的，一行都不删。
    """
    store = DraftCandidateStore(poisoned["conn"])
    pid = poisoned["pid"]
    never_landed = [
        store.put(pid, chapter=1, body=f"没进书的第 {n} 稿").id for n in range(5)
    ]
    landed = [store.put(pid, chapter=2, body=f"进过书的第 {n} 稿").id for n in range(KEEP_LANDED + 6)]
    for candidate_id in landed:
        store.mark_landed(pid, candidate_id)

    alive = {c.id for c in store.recent(pid, limit=200)}
    assert set(never_landed) <= alive, "没落过盘的候选被清掉了 —— 那正是作者要挑的那几稿"
    kept_landed = [cid for cid in landed if cid in alive]
    assert len(kept_landed) == KEEP_LANDED, f"落过盘的留了 {len(kept_landed)} 份"
    assert kept_landed == landed[-KEEP_LANDED:], "留下的不是最近那几份"
