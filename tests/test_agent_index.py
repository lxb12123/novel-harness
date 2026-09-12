"""书内索引四层（`agent/index.py`）—— **它每一次少给东西，都必须自己说出来。**

泄漏那一面由 `tests/test_agent_tools.py` 那张网罩（那条最贵、不可回收，所以它先于功能）。
这份文件盯的是另一类错误，症状不是「说漏嘴」而是**「安静地骗人」**：

| 骗法 | 长什么样 | 这里怎么钉 |
|---|---|---|
| 静默截断 | 「共 722 章」的目录只给了 300 条，读起来像全给了 | `*_omitted` + 一句中文，且**说得出怎么接着拿** |
| 缺摘要不报 | 只返回有摘要的那些 ⇒ 模型以为那几章什么都没发生 | 三态（没写 / 写了没总结 / 有）全物化 |
| 轴瞎了不报 | 抽取没跑过 ⇒ 事件轴恒空 ⇒ 读起来像「他们没同框」 | `blind` + `reason`，且每人各自的总数也报 |
| 未来不标 | 第 200 章的东西混在返回里，模型当成已经发生 | 每条一个 `future` + 整份一句话 |

**四种都是「漂亮的空结果 + exit 0」**（ARCHITECTURE §10 约束 8 那一类），
没有一种会让任何别的测试变红——所以它们只能在这里被钉住。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from novel_harness import project
from novel_harness.agent import (
    BookIndex,
    ChapterFullText,
    ChapterSummaries,
    CharacterChapters,
    EventIndex,
    SummaryIndex,
    ToolContext,
    dispatch,
)
from novel_harness.agent.ports import NoticeIndex, RulesIndex
from novel_harness.agent import index as agent_index
from novel_harness.agent import ports as agent_ports
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.product_context import DEFAULT_MEMORY_BUDGET, memory_units_available
from novel_harness.draft.provider import CompletionResult, ToolCall
from novel_harness.draft.rolling_summary import RollingSummarizer, SummaryStore
from novel_harness.events import EventView, StoryEvent
from novel_harness.graph import (
    ChapterSpec,
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import CHAPTER_DIR, chapter_path

TWIST = "萧决其实是魔尊之子第200章揭晓"
SECRET_DESC = "血脉的真相是他母亲换了孩子"
PLOT_NOTE = "萧决在幽泉窟被顾清音所杀"

# 五章正文。**谁在哪一章被提到是这份 fixture 的全部信息量**：
# 萧决 1/2/3/4、顾清音 2/3、「凌」（一字名，规则不可用）5。
CHAPTERS: dict[int, tuple[str, str]] = {
    1: ("第一章 少年", "萧决独自走进了北荒的风雪里。"),
    2: ("第二章 藏书阁", "顾清音在藏书阁遇见萧决。"),
    3: ("第三章 渡口", "萧决与顾清音在渡口分别。"),
    4: ("第四章 北荒", "萧决在北荒住了三个月。"),
    5: ("第五章 无人", "凌在城门口等了一夜。"),
}


@dataclass(frozen=True)
class Book:
    conn: Connection
    db_path: Path
    project_id: str
    store: SqliteStoryGraph
    root: Path

    def context(self, **overrides: Any) -> ToolContext:
        base: dict[str, Any] = {
            "store": self.store,
            "project_id": self.project_id,
            "root_path": str(self.root),
        }
        base.update(overrides)
        return ToolContext(**base)


@pytest.fixture
def book(tmp_path: Path) -> Iterator[Book]:
    """一本五章的小书 + 一个带 twist 的秘密 + 一个第 200 章才首现的地点。

    用文件库不用内存库：`RollingSummarizer` 自己开连接（它 own 每条连接来保证幂等），
    内存库里那是另一个空数据库。
    """
    root = tmp_path / "book"
    (root / CHAPTER_DIR).mkdir(parents=True)

    db_path = tmp_path / "book.db"
    conn = connect(db_path)
    migrate(conn)
    pid = project.create(conn, name="青云记-index", root_path=str(root)).id
    store = SqliteStoryGraph(conn)

    for name in ("萧决", "顾清音"):
        store.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name=name))
    # 一字名：canonical 别名的 `usable_for_rules` 是 `len(name) >= 2`，所以它**进不了**
    # mentions 的 alternation。这是 L1 那条「静默的零」的活体样本，不是凑数。
    store.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name="凌"))
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
            name="幽泉窟",
            props=NodeProps.model_validate(
                {"first_appears_chapter": 200, "plot_note": PLOT_NOTE}
            ),
        )
    )

    for number, (heading, body) in CHAPTERS.items():
        text = f"{heading}\n\n{body}\n"
        (root / chapter_path(number)).write_text(text, encoding="utf-8")
        store.put_chapter(
            ChapterSpec(
                project_id=pid,
                number=number,
                heading=heading,
                path=chapter_path(number),
                text=text,
            )
        )
    conn.commit()
    yield Book(conn=conn, db_path=db_path, project_id=pid, store=store, root=root)
    conn.close()


def _summarize(book: Book, chapters: Sequence[int]) -> None:
    """走真的 `RollingSummarizer` 落几条摘要（模型换成一个常量桩）。"""
    summarizer = RollingSummarizer(
        connection_factory=lambda: connect(book.db_path),
        analyzer=lambda request: CompletionResult(
            text=f"第 {request.chapter.number} 章的机器摘要。",
            model="stub",
            finish_reason="stop",
        ),
    )
    for number in chapters:
        summarizer.ensure(book.project_id, number)


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


def _ok(name: str, context: ToolContext, **arguments: Any) -> str:
    outcome = dispatch(_call(name, **arguments), context)
    assert outcome.ok, outcome.content
    return outcome.content


# ══════════════════════════════════════════════════════════════════════════
# 预算：**从模型窗口倒推，这一层里一个魔法数都不许有**
# ══════════════════════════════════════════════════════════════════════════


def test_the_budget_is_derived_from_the_window_not_written_down(book: Book) -> None:
    """`return_units` 就是 `memory_units_available()`，**倒推只写一次**。

    「近八章 / 12 条 / 30 章」那三个魔法数刚被换掉一次，理由是绝对量不随模型缩放：
    1M 窗口和 32k 窗口拿同一个数字。这里不许把那个病换个地方再犯一次。
    """
    unknown = book.context()
    assert unknown.return_units == DEFAULT_MEMORY_BUDGET.total, (
        "能力表没登记这个模型时要回落到既有兜底，**不许猜一个大窗口**"
    )

    small = book.context(max_context_tokens=32_000, reserved_output_tokens=4_000)
    big = book.context(max_context_tokens=1_000_000, reserved_output_tokens=8_000)
    assert small.return_units == memory_units_available(32_000, 4_000)
    assert big.return_units == memory_units_available(1_000_000, 8_000)
    assert small.return_units < big.return_units, "换个模型它得跟着缩放，否则就还是个魔法数"


MAGIC = 100
"""大于它的整数**字面量**在索引层里一律可疑。

判据故意粗，因为它要拦的是顺手写出来的那一种：`budget = 8000`。用 AST 不用正则——
`ADR 0019` / `2026-08-10` 在 docstring 里满地都是，而**守卫误报会被人关掉**。
"""


def test_no_layer_hides_a_magic_budget_number() -> None:
    """索引层和注入层里不许出现「一看就是预算」的整数字面量。

    「近八章 / 12 条 / 30 章」那三个魔法数刚被换掉一次（换成从模型窗口倒推），
    这里不许把同一个病换个地方再犯。真要放一个大常量进来，先回答它为什么不能从
    `memory_units_available()` 倒推出来。
    """
    import ast

    offenders: list[str] = []
    for module in (agent_index, agent_ports):
        path = Path(module.__file__ or "")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int)
                and not isinstance(node.value, bool)
                and node.value > MAGIC
            ):
                offenders.append(f"{path.name}:{node.lineno} → {node.value}")
    assert not offenders, (
        "索引层里出现了写死的大数字：\n  " + "\n  ".join(offenders) + "\n"
        "预算必须从 `capability.max_context_tokens` 倒推（`memory_units_available`），"
        "那儿已经握着「占几分之几」和「成本闸」这两个自由参数。"
    )


def test_that_magic_number_guard_can_see_one() -> None:
    """**一个永远绿的守卫比没有守卫更糟。** 喂一个假的进去，断言它红。"""
    import ast

    probe = ast.parse('"""ADR 0019 说了什么。"""\nBUDGET = 8000\n')
    found = [
        node.value
        for node in ast.walk(probe)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and node.value > MAGIC
    ]
    assert found == [8000], "扫描器要么看不见 8000，要么把 docstring 里的 0019 也算进来了"


# ══════════════════════════════════════════════════════════════════════════
# L0 目录
# ══════════════════════════════════════════════════════════════════════════


def test_l0_is_a_table_of_contents_with_names_and_no_aliases(book: Book) -> None:
    """目录 = 章标题（磁盘上的首个非空行）+ 角色册（**只有正式名**）。"""
    result = BookIndex.model_validate_json(_ok("book_index", book.context()))

    assert [(entry.chapter, entry.title) for entry in result.chapters] == [
        (number, heading) for number, (heading, _) in sorted(CHAPTERS.items())
    ]
    assert result.chapters_total == len(CHAPTERS)
    assert result.chapters_omitted == 0

    by_label = {(entry.label, entry.name) for entry in result.roster}
    assert ("Character", "萧决") in by_label
    assert ("Faction", "血脉秘密") in by_label, "非人物条目的**显示名**也要给"
    assert ("Location", "幽泉窟") in by_label
    assert result.roster_total == len(result.roster)


def test_l0_says_it_cannot_read_the_manuscript_instead_of_showing_an_empty_book(
    book: Book,
) -> None:
    """`root_path` 缺席时章标题为空——**而空必须带着理由**（约束 8）。

    「这本书还没有章」和「我读不到磁盘」在返回里长得一模一样，对作者却是完全相反的两件事。
    """
    result = BookIndex.model_validate_json(_ok("book_index", book.context(root_path=None)))
    assert result.chapters == [] and result.chapters_total == 0
    assert any("读不到" in note for note in result.notes)
    assert result.roster, "读不到正文不该影响角色册 —— 它来自图，不是磁盘"


def test_l0_truncation_says_how_to_get_the_rest(book: Book) -> None:
    """裁了就要说裁了多少，**而且说得出怎么接着拿**（`from_chapter`）。"""
    tight = book.context(max_context_tokens=1, reserved_output_tokens=0)
    first = BookIndex.model_validate_json(_ok("book_index", tight))
    assert first.chapters_omitted > 0
    assert first.chapters_through is not None
    assert any("from_chapter" in note for note in first.notes)
    assert first.chapters_total == len(CHAPTERS), "总数不许跟着被裁 —— 它是判断裁了多少的唯一依据"

    # 说得出的那条路要真的走得通。
    following = BookIndex.model_validate_json(
        _ok("book_index", tight, from_chapter=first.chapters_through + 1)
    )
    assert following.chapters[0].chapter == first.chapters_through + 1


def test_l0_marks_what_the_author_has_not_written_yet(book: Book) -> None:
    """超过作者进度的条目**标出来，不挡**（ADR 0019 边界二的残余代价是被接受的）。

    **2026-08-31**：这条原来还钉着角色册那一半的 `first_appears_chapter`/`future`
    标记——随 `forbidden_entities` 一起删了（ADR 0041），`RosterEntry` 上不再有
    这两个字段。章目录这一半的标记和它算法本来就不同，没有受影响。
    """
    result = BookIndex.model_validate_json(
        _ok("book_index", book.context(working_chapter=3))
    )
    assert [entry.chapter for entry in result.chapters if entry.future] == [4, 5], (
        "第 4/5 章在作者进度之后 —— 它们照给，但必须带标记"
    )
    assert result.future_from_chapter == 4
    assert any("还没写到" in note for note in result.notes)


def test_l0_does_not_pretend_to_know_where_the_author_is(book: Book) -> None:
    """不知道作者写到第几章时**不标，并且说自己没标**。猜一个数比不标更坏。"""
    result = BookIndex.model_validate_json(_ok("book_index", book.context()))
    assert result.working_chapter is None and result.future_from_chapter is None
    assert not any(entry.future for entry in result.chapters)
    assert any("不知道作者当前写到第几章" in note for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# L1 人物轴 —— 集合求交，不是检索（ADR 0002）
# ══════════════════════════════════════════════════════════════════════════


def test_l1_answers_when_they_first_met_by_intersecting_sets(book: Book) -> None:
    """「萧决第一次见顾清音是哪章」= 两个集合求交，**零猜测、零 token**。"""
    result = CharacterChapters.model_validate_json(
        _ok("character_chapters", book.context(), characters=["萧决", "顾清音"])
    )
    axis = result.mentioned_together
    assert axis.blind is False
    assert axis.chapters == [2, 3] and axis.first == 2 and axis.last == 3
    assert axis.total == 2

    counts = {entry.name: entry.mention_chapters for entry in result.characters}
    assert counts == {"萧决": 4, "顾清音": 2}, "每个人各自的总数也要给 —— 它是拆零的依据"


def test_l1_says_when_an_axis_is_blind_instead_of_returning_zero(book: Book) -> None:
    """**瞎了和「没命中」不许长得一样。** 三种瞎法，三句话。"""
    no_disk = CharacterChapters.model_validate_json(
        _ok("character_chapters", book.context(root_path=None), characters=["萧决"])
    )
    assert no_disk.mentioned_together.blind is True
    assert no_disk.mentioned_together.chapters == []
    assert "读不到" in no_disk.mentioned_together.reason
    assert no_disk.characters[0].mention_chapters is None, (
        "轴瞎着时每个人的数必须是 None —— 0 会被读成「他一章都没出现过」"
    )

    # 一字名在正文里出现了 5 次，可它一个「可用于匹配」的称呼都没有 ⇒ 恒为 0。
    unmatchable = CharacterChapters.model_validate_json(
        _ok("character_chapters", book.context(), characters=["凌"])
    )
    assert unmatchable.mentioned_together.blind is True
    assert "凌" in unmatchable.mentioned_together.reason
    assert any("不是「没出现过」" in note for note in unmatchable.notes)

    # 事件端口没接线。
    assert no_disk.events_together.blind is True
    assert "还没接" in no_disk.events_together.reason


def test_l1_refuses_an_ambiguous_or_non_character_name(book: Book) -> None:
    outcome = dispatch(
        _call("character_chapters", characters=["查无此人"]), book.context()
    )
    assert outcome.ok is False and "查无此人" in outcome.content

    not_a_character = dispatch(
        _call("character_chapters", characters=["血脉秘密"]), book.context()
    )
    assert not_a_character.ok is False and "不是人物" in not_a_character.content


class FakeEvents:
    """一条已确认事件的读端。**只有 `events_for_characters`**，写方法一个都没有。"""

    def __init__(self, views: Sequence[EventView]) -> None:
        self.views = list(views)
        self.calls: list[tuple[tuple[str, ...], int, InformationScope]] = []

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self.calls.append((tuple(character_ids), chapter, scope))
        wanted = set(character_ids)
        return [
            view
            for view in self.views
            if wanted
            & ({ref.id for ref in view.participants} | {ref.id for ref in view.knowers})
        ]


def _event(book: Book, number: int, *names: str) -> EventView:
    refs = [
        NodeRef.of(book.store.resolve(book.project_id, [name])[0].hits[0].node)
        for name in names
    ]
    return EventView(
        event=StoryEvent(
            id=f"event-{number}",
            project_id=book.project_id,
            chapter_number=number,
            summary=f"第 {number} 章的一件事",
            information_scope=InformationScope.CANON,
            status=EdgeStatus.ACTIVE,
            source=EdgeSource.EXTRACTOR,
            evidence_id="evidence-1",
            evidence_status=EvidenceStatus.FRESH,
        ),
        participants=refs,
    )


def test_l1_event_axis_is_a_set_intersection_too(book: Book) -> None:
    """事件轴要求这几个人**同时**在同一条事件里，不是「谁沾边算谁」。"""
    events = FakeEvents(
        [
            _event(book, 2, "萧决", "顾清音"),
            _event(book, 3, "萧决"),
            _event(book, 4, "萧决", "顾清音"),
        ]
    )
    result = CharacterChapters.model_validate_json(
        _ok(
            "character_chapters",
            book.context(events=events),
            characters=["萧决", "顾清音"],
        )
    )
    assert result.events_together.chapters == [2, 4]
    assert result.events_together.first == 2
    counts = {entry.name: entry.event_chapters for entry in result.characters}
    assert counts == {"萧决": 3, "顾清音": 2}

    # 查询坐标盖住全书（磁盘上最后一章），且它是 AS OF —— 不写进任何一行数据。
    (ids, horizon, scope) = events.calls[0]
    assert horizon == max(CHAPTERS) and scope is InformationScope.CANON
    assert set(ids) == {
        book.store.resolve(book.project_id, [name])[0].hits[0].node.id
        for name in ("萧决", "顾清音")
    }


def test_l1_empty_event_axis_admits_extraction_may_never_have_run(book: Book) -> None:
    """事件轴的 0 有两个意思，工具分不出来 —— **那就说出来**，别让模型替它选一个。"""
    result = CharacterChapters.model_validate_json(
        _ok(
            "character_chapters",
            book.context(events=FakeEvents([])),
            characters=["萧决", "顾清音"],
        )
    )
    assert result.events_together.blind is False and result.events_together.total == 0
    assert any("抽取还没跑过" in note for note in result.notes)


def test_l1_two_axes_answer_different_questions_and_say_so(book: Book) -> None:
    """两条轴的 `source` 必须不同、且各自说清自己数的是什么。

    合并成一条会毁掉这一层：正文命中是超集（回忆里的死人也算），事件是已确认的，
    读者若以为它们是同一个数，两种错误方向的偏差就被抹平了。
    """
    result = CharacterChapters.model_validate_json(
        _ok("character_chapters", book.context(events=FakeEvents([])), characters=["萧决"])
    )
    assert result.mentioned_together.source != result.events_together.source
    assert "不回答「谁在场」" in result.mentioned_together.source
    assert "抽取" in result.events_together.source


def test_l1_truncation_keeps_both_ends_and_never_lies_about_the_scalars(book: Book) -> None:
    """章号列表裁的是**中间**：「第一次」在头上，「最近一次」在尾上，砍任一端都毁掉一个。"""
    tight = book.context(max_context_tokens=1, reserved_output_tokens=0)
    result = CharacterChapters.model_validate_json(
        _ok("character_chapters", tight, characters=["萧决"])
    )
    axis = result.mentioned_together
    assert axis.total == 4 and axis.first == 1 and axis.last == 4, "纯量永不被裁"
    assert axis.omitted > 0 and len(axis.chapters) < axis.total
    assert axis.chapters[0] == 1
    assert any("中间" in note for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# L2 摘要 —— 三态，缺的那两态比有的那态更要紧
# ══════════════════════════════════════════════════════════════════════════


def test_l2_reports_all_three_states(book: Book) -> None:
    """有摘要 / 有正文没摘要 / 没正文。**只报第一种 = 告诉模型那几章什么都没发生。**"""
    _summarize(book, [1, 2])
    context = book.context(summaries=SummaryStore(book.conn))
    result = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", context, first_chapter=1, last_chapter=7)
    )

    assert [entry.chapter for entry in result.summaries] == [1, 2]
    assert result.summarized_total == 2
    assert result.unsummarized == [3, 4, 5] and result.unsummarized_total == 3
    assert result.unwritten == [6, 7] and result.unwritten_total == 2
    assert any("索引在那几章是瞎的" in note for note in result.notes)
    assert any("还没有正文" in note for note in result.notes)


def test_l2_refuses_clearly_when_the_port_is_not_wired(book: Book) -> None:
    outcome = dispatch(
        _call("chapter_summaries", first_chapter=1, last_chapter=3), book.context()
    )
    assert outcome.ok is False and "还没接" in outcome.content


def test_l2_rejects_a_backwards_range(book: Book) -> None:
    outcome = dispatch(
        _call("chapter_summaries", first_chapter=5, last_chapter=2),
        book.context(summaries=SummaryStore(book.conn)),
    )
    assert outcome.ok is False and "参数不合法" in outcome.content


def test_l2_range_is_bounded_by_the_budget_and_says_which_end_it_dropped(
    book: Book,
) -> None:
    """「一次别把全书拉下来」的上界就是预算本身 —— 但**丢的是哪一段必须说出来**。"""
    _summarize(book, [1, 2, 3, 4, 5])
    tight = book.context(
        summaries=SummaryStore(book.conn), max_context_tokens=1, reserved_output_tokens=0
    )
    result = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", tight, first_chapter=1, last_chapter=5)
    )
    assert result.summarized_total == 5
    assert result.omitted > 0 and len(result.summaries) < 5
    assert result.summaries[-1].chapter == 5, "留的是最近的那些（同起草侧的既有做法）"
    assert any("最早的" in note for note in result.notes)


def test_l2_spends_the_budget_on_the_gaps_before_the_summaries(book: Book) -> None:
    """预算不够时**先保「缺什么」**：它便宜一个量级，而它是这一层唯一的诚实性保证。

    这个窗口刚好装得下五个章号（每个两三个字），装不下五段摘要——顺序反过来的话，
    返回里会只剩摘要，而「那三章我是瞎的」这句话被最贵的东西挤掉了。
    """
    _summarize(book, [4, 5])
    tight = book.context(
        summaries=SummaryStore(book.conn), max_context_tokens=120, reserved_output_tokens=0
    )
    result = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", tight, first_chapter=1, last_chapter=5)
    )
    assert result.unsummarized == [1, 2, 3], "缺哪几章不许被摘要挤掉"
    assert result.unsummarized_omitted == 0
    assert result.omitted > 0, "被裁掉的应该是摘要那一侧"


def test_l2_marks_summaries_from_chapters_the_author_has_not_reached(book: Book) -> None:
    _summarize(book, [1, 5])
    result = ChapterSummaries.model_validate_json(
        _ok(
            "chapter_summaries",
            book.context(summaries=SummaryStore(book.conn), working_chapter=3),
            first_chapter=1,
            last_chapter=5,
        )
    )
    assert [(entry.chapter, entry.future) for entry in result.summaries] == [
        (1, False),
        (5, True),
    ]
    assert any("还没写到" in note for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# L3 全文 —— 磁盘是真相源（ADR 0007）
# ══════════════════════════════════════════════════════════════════════════


def test_l3_reads_the_manuscript_off_disk(book: Book) -> None:
    result = ChapterFullText.model_validate_json(
        _ok("chapter_text", book.context(), chapter=2)
    )
    assert CHAPTERS[2][1] in result.text
    assert result.truncated is False and result.units == result.units_given


def test_l3_refuses_instead_of_returning_an_empty_chapter(book: Book) -> None:
    """两种读不到，两句话 —— **绝不返回一个空字符串**（那读起来像「这一章是空的」）。"""
    missing = dispatch(_call("chapter_text", chapter=99), book.context())
    assert missing.ok is False and "没有正文" in missing.content

    no_root = dispatch(_call("chapter_text", chapter=1), book.context(root_path=None))
    assert no_root.ok is False and "磁盘" in no_root.content


def test_l3_truncation_reports_the_real_length(book: Book) -> None:
    """截了就说，**而且报的是全章的真长度**——只报给出去那部分等于说「这章就这么长」。"""
    # 兜底预算（DEFAULT_MEMORY_BUDGET.total）比这本书的一章大得多，所以给一个小窗口。
    tight = book.context(max_context_tokens=60, reserved_output_tokens=0)
    result = ChapterFullText.model_validate_json(_ok("chapter_text", tight, chapter=1))

    assert result.truncated is True
    assert result.units_given == tight.return_units
    assert result.units > result.units_given
    assert result.text and CHAPTERS[1][0].split()[0] in result.text, "留的是开头"
    assert any("截掉了" in note for note in result.notes)


def test_l3_marks_a_future_chapter(book: Book) -> None:
    result = ChapterFullText.model_validate_json(
        _ok("chapter_text", book.context(working_chapter=2), chapter=5)
    )
    assert result.future is True
    assert any("还没写到" in note for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# 端口：注入的是**窄的只读口**，不是连接
# ══════════════════════════════════════════════════════════════════════════


def test_the_real_stores_satisfy_the_narrow_read_only_ports(book: Book) -> None:
    """`SummaryStore` / `SqliteEventStore` 结构上就满足端口 —— **不许再实现第二个**。"""
    from novel_harness.graph.sqlite_events import SqliteEventStore

    assert isinstance(SummaryStore(book.conn), SummaryIndex)
    assert isinstance(SqliteEventStore(book.conn), EventIndex)


def test_the_context_still_has_no_connection_and_no_writer(book: Book) -> None:
    """摘要住在 SQLite 里，但**读它的代价不是把 `Connection` 塞进 `ToolContext`**。

    一条活连接进了那个 dataclass，「模型改不了作者的 canon」当场从类型保证退回纪律。
    """
    context = book.context(summaries=SummaryStore(book.conn))
    assert not hasattr(context, "conn")
    assert not hasattr(context, "writer")
    # 端口本身必须是窄的：实现可以更宽（`SummaryStore` 还有 `for_range` / `get`），
    # 但**协议上多一个方法就等于多一条模型够得着的路**。
    assert set(SummaryIndex.__protocol_attrs__) == {"coverage", "snapshot_watermark"}
    # 2026-09-12 多了三条**读**路（角色卡 / 事件那两栏，`agent/panels.py`）：
    # `EventStore` 上那三个写方法（`put_provisional` / `clone_to_scope` /
    # `update_profile`）一个都没跟过来——这条断言就是钉这件事的。
    assert set(EventIndex.__protocol_attrs__) == {
        "events_for_characters",
        "events_for_chapter",
        "events_for_one_character",
        "profile",
    }
    assert set(RulesIndex.__protocol_attrs__) == {"catalog", "latest_report"}
    assert set(NoticeIndex.__protocol_attrs__) == {"open_notices", "pending_proposals"}
