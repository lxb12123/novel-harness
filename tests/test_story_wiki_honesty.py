"""书内索引的**诚实性**对抗验证 —— 它少给东西的时候，会不会安静地骗模型。

`tests/test_agent_index.py` 已经钉了四层各自的正常形状；这一份专挑**它没挡住的那几种
「漂亮的空结果 + exit 0」**。判据只有一条，而且它比「返回对不对」更硬：

> **「书里没有」和「我没看」在返回里必须分得开。**

分不开的代价是确定的：模型把「没搜到」当成「不存在」，然后据此写正文——而作者看到的是
一段读起来完全正常、只是与前八十章矛盾的稿子。这个仓库为同一种病吃过四次亏
（`chapter_summary` 恒空而界面只写「- 暂无」／底栏花销系统性偏低／`endpoints: []` 对
改得掉的事说改不了／屏幕守卫的词表恰好漏掉真正上屏的那两个词），所以它单独占一份文件。

这里钉住的五条，每一条都是**上面那份测试当时看不见**的：

| # | 骗法 | 为什么原来的测试看不见 |
|---|---|---|
| 1 | L2 的「还没有正文」是从**库**里数的，而正文的真相源是磁盘 | fixture 里磁盘和库恰好同步 |
| 2 | 「有摘要」这一态吃掉了「摘要是**旧正文**的」 | fixture 里没人改过正文 |
| 3 | 事件轴的查询坐标是个**静默上限**（`omitted=0` 却裁掉了三分之二） | 那份 fixture 的假端口**忽略 `chapter` 参数**，比真库宽 |
| 4 | 花名册被裁光整整一类（秘密），而且**没有取回它的路** | 只断言了 `omitted > 0`，没问「omit 掉的是哪一类」 |
| 5 | 花名册为空时**一句话都不说** | 空花名册是「合法的空」，没有断言盯它 |

第 3 条顺带说明了一件事：**假实现比真实现宽的时候，测试是绿的而产品是错的。**
所以这份文件里的两个假端口都刻意做成「和真实现同解」，另有两个探针专门断言
「网抓得住一个撒谎的实现」——一个永远绿的守卫比没有守卫更糟。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from novel_harness import project
from novel_harness.agent import (
    BookIndex,
    ChapterSummaries,
    CharacterChapters,
    ToolContext,
    dispatch,
)
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import CompletionResult, ToolCall
from novel_harness.draft.rolling_summary import (
    ChapterSummaryStatus,
    RollingSummarizer,
    SummaryStore,
)
from novel_harness.events import EventView, StoryEvent
from novel_harness.graph import (
    ChapterSpec,
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    NodeLabel,
    NodeRef,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import CHAPTER_DIR, chapter_path

CHAPTERS: dict[int, tuple[str, str]] = {
    1: ("第一章 少年", "萧决独自走进了北荒的风雪里。"),
    2: ("第二章 藏书阁", "顾清音在藏书阁遇见萧决。"),
    3: ("第三章 渡口", "萧决与顾清音在渡口分别。"),
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

    def node_id(self, name: str) -> str:
        return self.store.resolve(self.project_id, [name])[0].hits[0].node.id

    def write_only_to_disk(self, number: int, heading: str, body: str) -> Path:
        """**只写磁盘，不进库。** 这是作者在自己的编辑器里写字时的常态（ADR 0007）。"""
        file = self.root / chapter_path(number)
        file.write_text(f"{heading}\n\n{body}\n", encoding="utf-8")
        return file


@pytest.fixture
def book(tmp_path: Path) -> Iterator[Book]:
    root = tmp_path / "book"
    (root / CHAPTER_DIR).mkdir(parents=True)
    db_path = tmp_path / "book.db"
    conn = connect(db_path)
    migrate(conn)
    pid = project.create(conn, name="青云记-honesty", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    for name in ("萧决", "顾清音"):
        store.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name=name))
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


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


def _ok(name: str, context: ToolContext, **arguments: Any) -> str:
    outcome = dispatch(_call(name, **arguments), context)
    assert outcome.ok, outcome.content
    return outcome.content


def _summarize(book: Book, chapters: Sequence[int]) -> None:
    summarizer = RollingSummarizer(
        connection_factory=lambda: connect(book.db_path),
        analyzer=lambda request: CompletionResult(
            text=f"第 {request.chapter.number} 章：{request.chapter.text.strip().splitlines()[-1]}",
            model="stub",
            finish_reason="stop",
        ),
    )
    for number in chapters:
        summarizer.ensure(book.project_id, number)


# ══════════════════════════════════════════════════════════════════════════
# 这份文件的网：**四个桶必须把问到的区间铺满**
# ══════════════════════════════════════════════════════════════════════════


def assert_covers_every_chapter_asked_for(result: ChapterSummaries) -> None:
    """区间里的每一章必须**恰好**落进一个桶里。

    这是 L2 唯一一条不靠读中文就能自动执行的诚实性判据：只要有一章既没进「有摘要」、
    也没进「有正文没摘要」/「磁盘上有库里没有」/「还没写」，它就是被**静默丢掉**的——
    而静默丢掉的那一章，模型会读成「那一章什么都没发生」。

    `for_range` 那条路（只返回有摘要的那些）之所以不许进端口，就是因为它必然破坏这条。
    """
    span = result.last_chapter - result.first_chapter + 1
    buckets = {
        "有摘要": result.summarized_total,
        "有正文没摘要": result.unsummarized_total,
        "磁盘上有、库里还没有": result.unindexed_total,
        "还没写": result.unwritten_total,
    }
    assert sum(buckets.values()) == span, (
        f"问的是第 {result.first_chapter}–{result.last_chapter} 章（{span} 章），"
        f"四个桶加起来只有 {sum(buckets.values())} 章：{buckets}。\n"
        "差掉的那几章被静默丢掉了——模型会把它读成「那几章什么都没发生」。"
    )


class SilentlyTruncatingSummaries:
    """**撒谎的端口**：只报有摘要的那些章，缺的那些一声不吭。

    这正是 `SummaryIndex` 故意不收 `for_range` 的理由（`ports.py` 写了那段论证）。
    它在这里的唯一用途是**证明上面那张网真的抓得住它**——一个永远绿的守卫比没有守卫更糟。
    """

    def __init__(self, real: SummaryStore) -> None:
        self._real = real

    def coverage(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> list[ChapterSummaryStatus]:
        return [
            row
            for row in self._real.coverage(project_id, first_chapter, last_chapter)
            if row.summary is not None
        ]


def test_the_partition_guard_catches_a_silently_truncating_port(book: Book) -> None:
    """自守卫：喂一个「只报有摘要的那些」的端口进去，断言网红。"""
    _summarize(book, [1])
    liar = book.context(summaries=SilentlyTruncatingSummaries(SummaryStore(book.conn)))
    lied = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", liar, first_chapter=1, last_chapter=3)
    )
    with pytest.raises(AssertionError, match="静默丢掉"):
        assert_covers_every_chapter_asked_for(lied)

    honest = ChapterSummaries.model_validate_json(
        _ok(
            "chapter_summaries",
            book.context(summaries=SummaryStore(book.conn)),
            first_chapter=1,
            last_chapter=3,
        )
    )
    assert_covers_every_chapter_asked_for(honest)


# ══════════════════════════════════════════════════════════════════════════
# 档 1：缺摘要 —— 三态之外还有第四态和第五态
# ══════════════════════════════════════════════════════════════════════════


def test_l2_does_not_call_a_chapter_unwritten_when_it_is_on_disk(book: Book) -> None:
    """**磁盘上有正文、库里还没有** ≠ 还没写到那儿（ADR 0007：真相源在磁盘）。

    作者就在旁边的编辑器里打字，新写的一章在 `sync` 之前只存在于 `chapters/NNNN.md`。
    这一档如果被并进「还没有正文」，模型就会跳过一整章**已经写好的正文**——而同一次
    对话里 `book_index` 列得出它的标题、`chapter_text` 读得出它的全文。**三个层互相打架，
    而最令人安心的那个说法赢了。**
    """
    book.write_only_to_disk(4, "第四章 未同步", "萧决在北荒住了三个月。")
    context = book.context(summaries=SummaryStore(book.conn))

    result = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", context, first_chapter=1, last_chapter=5)
    )
    assert_covers_every_chapter_asked_for(result)

    assert result.unindexed == [4] and result.unindexed_total == 1, (
        "第 4 章的正文就在磁盘上（同一次会话里 chapter_text 读得到），"
        "它不是「还没写」，是「库里还没有当前快照」。"
    )
    assert result.unwritten == [5], "只有磁盘上也没有的那一章才算还没写"
    assert any("还没写" in note and "别把它们" in note for note in result.notes)

    # 同一次会话里另外两层对第 4 章的说法必须和这一层一致。
    index = BookIndex.model_validate_json(_ok("book_index", context))
    assert 4 in [entry.chapter for entry in index.chapters]
    assert dispatch(_call("chapter_text", chapter=4), context).ok is True


def test_l2_without_the_manuscript_says_it_could_not_tell_the_two_apart(book: Book) -> None:
    """读不到磁盘时**分不出**「还没写」和「写了没同步」—— 那就说出来，别替它选一个。"""
    result = ChapterSummaries.model_validate_json(
        _ok(
            "chapter_summaries",
            book.context(root_path=None, summaries=SummaryStore(book.conn)),
            first_chapter=1,
            last_chapter=5,
        )
    )
    assert_covers_every_chapter_asked_for(result)
    assert result.unindexed_total == 0
    assert any("读不到" in note and "磁盘" in note for note in result.notes), (
        "root_path 缺席时「还没有正文」这一档里可能混着「写了但还没同步」的章，"
        "**这件事必须写在返回里**，否则它退化成了一个更自信的错误答案。"
    )


def test_l2_says_a_summary_may_be_older_than_the_chapter_it_summarizes(book: Book) -> None:
    """**「有摘要」吃掉了「摘要是旧正文的」。**

    实测形态：第 3 章整章重写（顾清音在新版里根本没出现），而 L2 仍旧原样交出那段
    「萧决与顾清音在渡口分别」，`summarized_total` 一动不动，一个字都不说。摘要是模型在
    这一层唯一的「这一章讲了什么」，所以它过期 = 索引在那一章给的是**错的**而不是缺的
    ——比缺更坏。

    上游知道这件事（`api/autopilot.py`：「`coverage()` 也只问「有没有」不问「新不新」」），
    但 `index.py` 自己的 docstring 反过来宣称「照抄它就不会撒谎」。
    """
    _summarize(book, [1, 2, 3])
    stale_file = book.write_only_to_disk(3, "第三章 渡口", "萧决独自离开，顾清音并未出现。")
    later = time.time() + 60
    os.utime(stale_file, (later, later))

    result = ChapterSummaries.model_validate_json(
        _ok(
            "chapter_summaries",
            book.context(summaries=SummaryStore(book.conn)),
            first_chapter=1,
            last_chapter=3,
        )
    )
    by_chapter = {entry.chapter: entry for entry in result.summaries}
    assert by_chapter[3].may_be_stale is True, (
        "第 3 章的正文在这段摘要生成之后被改过——返回里必须带得出这件事，"
        "否则模型会拿一段描述已经不存在的正文的摘要去写下一章。"
    )
    assert by_chapter[1].may_be_stale is False and by_chapter[2].may_be_stale is False
    assert result.stale_total == 1
    assert any("改过" in note or "过期" in note for note in result.notes)


def test_l2_says_when_it_could_not_check_staleness(book: Book) -> None:
    """读不到磁盘就查不了「正文有没有被改过」——**不许把「没查」显示成「没过期」。**"""
    _summarize(book, [1])
    result = ChapterSummaries.model_validate_json(
        _ok(
            "chapter_summaries",
            book.context(root_path=None, summaries=SummaryStore(book.conn)),
            first_chapter=1,
            last_chapter=1,
        )
    )
    assert result.stale_checked is False
    assert any("没" in note and ("改过" in note or "过期" in note) for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# 档 2：预算不够时裁的是什么，以及**裁完还拿不拿得回来**
# ══════════════════════════════════════════════════════════════════════════


def _crowd_the_roster(book: Book) -> None:
    """把花名册撑到装不下：40 个路人 + 5 条秘密 + 1 个地点。"""
    for i in range(40):
        book.store.upsert_node(
            NodeSpec(
                project_id=book.project_id,
                label=NodeLabel.CHARACTER,
                name=f"路人甲乙丙{i:03d}",
            )
        )
    for i in range(5):
        book.store.upsert_node(
            NodeSpec(
                project_id=book.project_id,
                label=NodeLabel.SECRET,
                name=f"某条秘密{i:03d}",
                secret=SecretDetail(description="内容不许出现在任何工具返回里"),
            )
        )
    book.store.upsert_node(
        NodeSpec(project_id=book.project_id, label=NodeLabel.LOCATION, name="幽泉窟")
    )
    book.conn.commit()


def test_l0_says_which_kinds_of_names_it_dropped_entirely(book: Book) -> None:
    """花名册按 (类型, 名字) 排序 + 从头收 ⇒ **`Secret` 永远是第一批被裁光的**。

    而 `book_index` 的工具描述自己承诺「人物 / 地点 / 门派 / 物件 / **秘密**的显示名」。
    只报一个 `roster_omitted=28` 是不够的：模型读到的是「这本书没有任何秘密」，
    而那正是它接下来会据以判断「这一章可以随便写」的东西。
    **裁了什么必须说出来 —— 是「什么」，不只是「多少条」。**
    """
    _crowd_the_roster(book)
    tight = book.context(max_context_tokens=8_000, reserved_output_tokens=0)
    result = BookIndex.model_validate_json(_ok("book_index", tight))

    assert result.roster_omitted > 0, "这个窗口本来就装不下，装下了说明 fixture 失效了"
    assert {entry.label for entry in result.roster} == {"Character"}, (
        "前提：排序 + 从头收让整批 Secret / Location 掉在了外面"
    )
    assert set(result.roster_labels_omitted) >= {"Secret", "Location"}, (
        "整整一类一条都没给到 —— 这件事必须物化成纯量，不能只留在 roster_omitted 那个总数里"
    )
    assert any("Secret" in note for note in result.notes)


def test_l0_roster_truncation_has_a_way_to_get_the_rest(book: Book) -> None:
    """**说得出怎么接着拿，而且那条路真的走得通**——章标题那一半已经做到了，花名册没有。

    `from_chapter` 只管章标题；被裁掉的 28 条花名册在这个会话里再也拿不到。
    这一层的标准是它自己定的（`test_l0_truncation_says_how_to_get_the_rest`），
    不能对章标题成立、对花名册就不成立。
    """
    _crowd_the_roster(book)
    tight = book.context(max_context_tokens=8_000, reserved_output_tokens=0)

    only_secrets = BookIndex.model_validate_json(
        _ok("book_index", tight, labels=["Secret"])
    )
    assert [entry.label for entry in only_secrets.roster] == ["Secret"] * 5
    assert only_secrets.roster_omitted == 0
    assert only_secrets.roster_labels_omitted == []
    # 过滤本身也是一次「少给」，它同样要说出来。
    assert any("Secret" in note and "别的" in note for note in only_secrets.notes)

    refused = dispatch(_call("book_index", labels=["主角"]), tight)
    assert refused.ok is False and "Character" in refused.content


def test_l2_budget_keeps_the_newest_and_says_so(book: Book) -> None:
    """预算口径的**自圆其说**这一条是过的，钉住免得被人顺手改成静默的。

    ADR 0019 边界五说「投影按章号参数化，不按时间近」，而检索类问题恰恰常指向很早的章。
    这一层的答复是：区间**由模型给**（章号参数化那一半成立），装不下时从最早的一端收，
    并且**说得出怎么把最早那段拿回来**（把区间往前挪）——而那条路真的走得通。
    """
    _summarize(book, [1, 2, 3])
    tight = book.context(
        summaries=SummaryStore(book.conn), max_context_tokens=1, reserved_output_tokens=0
    )
    wide = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", tight, first_chapter=1, last_chapter=3)
    )
    assert wide.omitted > 0 and wide.summaries[-1].chapter == 3
    assert any("最早的" in note for note in wide.notes)

    # 说出口的那条退路必须走得通：区间往前挪，被裁掉的那一段就拿得到。
    narrow = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", tight, first_chapter=1, last_chapter=1)
    )
    assert [entry.chapter for entry in narrow.summaries] == [1]


# ══════════════════════════════════════════════════════════════════════════
# 档 3：L1 的两条轴各自答的是不是它宣称的那个问题
# ══════════════════════════════════════════════════════════════════════════


def _event(book: Book, number: int, *names: str) -> EventView:
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
        participants=[
            NodeRef(id=book.node_id(name), label=NodeLabel.CHARACTER, name=name)
            for name in names
        ],
    )


class HorizonRespectingEvents:
    """**和真库同解**的假端口：`chapter` 是 AS OF 坐标，超过它的事件查不到。

    `graph/queries.py` 的 `TEMPORAL_WHERE` 第一行就是 `valid_from_chapter <= :ch`。
    `tests/test_agent_index.py` 里那个假端口**忽略了这个参数**——它比真库宽，于是
    「查询坐标是个静默上限」这件事在那份文件里根本显不出来。假实现比真实现宽的时候，
    **测试是绿的而产品是错的**。
    """

    def __init__(self, views: Sequence[EventView]) -> None:
        self.views = list(views)
        self.asked: list[int] = []

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self.asked.append(chapter)
        wanted = set(character_ids)
        return [
            view
            for view in self.views
            if view.event.chapter_number <= chapter
            and wanted & {ref.id for ref in view.participants}
        ]


def test_l1_event_axis_says_how_far_its_query_coordinate_reached(book: Book) -> None:
    """事件轴问的是「AS OF 第 N 章」，而 N 是后端自己算的 —— **那个 N 必须报出来。**

    读不到磁盘时 N 退化成作者的进度，于是「他已经写完但还没读到的那些章」里的事件
    **一条都查不到**，而返回里 `omitted=0`（在说「一条都没裁」）、`total=1`、
    `last=2`。三个数字合起来是一个自信的错误答案。

    它还和这一层自己写死的纪律直接冲突：「超过作者进度的条目**只标不挡**」——
    事件轴在这个配置下是挡，而且不说。
    """
    events = HorizonRespectingEvents(
        [
            _event(book, 2, "萧决", "顾清音"),
            _event(book, 5, "萧决", "顾清音"),
            _event(book, 9, "萧决", "顾清音"),
        ]
    )
    result = CharacterChapters.model_validate_json(
        _ok(
            "character_chapters",
            book.context(root_path=None, events=events, working_chapter=2),
            characters=["萧决", "顾清音"],
        )
    )
    axis = result.events_together
    assert events.asked == [2] and axis.chapters == [2], "前提：真库会把第 5 / 9 章滤掉"
    assert axis.searched_through == 2, (
        "这条轴只数到第 2 章为止 —— total / first / last / omitted 全都只在这个范围内成立，"
        "而返回里今天没有任何字段说得出这个范围。"
    )
    assert any("第 2 章" in note and "之后" in note for note in result.notes)


def test_l1_axis_coverage_is_reported_even_when_nothing_is_cut(book: Book) -> None:
    """磁盘读得到时查询坐标盖住全书 —— 那个数照样要报，否则模型没法比较两条轴。"""
    events = HorizonRespectingEvents([_event(book, 2, "萧决", "顾清音")])
    result = CharacterChapters.model_validate_json(
        _ok(
            "character_chapters",
            book.context(events=events),
            characters=["萧决", "顾清音"],
        )
    )
    assert result.events_together.searched_through == max(CHAPTERS)
    assert result.mentioned_together.searched_through == max(CHAPTERS)


def test_l1_two_axes_still_say_which_question_each_one_answered(book: Book) -> None:
    """这一条今天是**过的**，钉住免得被人合并成一条「出场章」。

    「这一章正文提到他」和「这一章有他的一条已确认事件」不是同一个问题：前者是超集
    （回忆里的死人也算），后者依赖抽取跑过。含混的出处比没有出处更坏——模型会拿它当
    「他在这章出现过」，去读全文，读不到就以为书错了。
    """
    result = CharacterChapters.model_validate_json(
        _ok(
            "character_chapters",
            book.context(events=HorizonRespectingEvents([])),
            characters=["萧决", "顾清音"],
        )
    )
    assert result.mentioned_together.source != result.events_together.source
    assert "不回答「谁在场」" in result.mentioned_together.source
    assert "抽取" in result.events_together.source
    assert result.events_together.blind is False and result.events_together.total == 0
    assert any("抽取还没跑过" in note for note in result.notes)


# ══════════════════════════════════════════════════════════════════════════
# 档 4：「未来」标记的边界值 —— 这一档是**过的**，钉住防回归
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("working_chapter", "expected_future"),
    [
        (None, set()),
        (1, {2, 3}),
        (2, {3}),
        (max(CHAPTERS), set()),
        (max(CHAPTERS) + 90, set()),
    ],
)
def test_future_marking_at_the_boundaries(
    book: Book, working_chapter: int | None, expected_future: set[int]
) -> None:
    """N=1 / N=最大 / N 越界 / 没有工作章号，四个边界都要么标对、要么说自己没标。"""
    result = BookIndex.model_validate_json(
        _ok("book_index", book.context(working_chapter=working_chapter))
    )
    assert {entry.chapter for entry in result.chapters if entry.future} == expected_future
    if working_chapter is None:
        assert result.future_from_chapter is None
        assert any("不知道作者当前写到第几章" in note for note in result.notes), (
            "没标而且不说 = 模型不知道自己在看未来"
        )
    else:
        assert result.future_from_chapter == working_chapter + 1


def test_future_is_marked_never_blocked(book: Book) -> None:
    """**一个字都不挡**（ADR 0019 边界二自己接受了推理被污染这个残余代价）。"""
    context = book.context(working_chapter=1)
    index = BookIndex.model_validate_json(_ok("book_index", context))
    assert index.chapters_total == len(CHAPTERS), "未来的章仍然要列出来"
    assert dispatch(_call("chapter_text", chapter=max(CHAPTERS)), context).ok is True


# ══════════════════════════════════════════════════════════════════════════
# 档 5：零转 —— 「书里没有」和「我没看」
# ══════════════════════════════════════════════════════════════════════════


def test_l0_empty_roster_says_why_it_is_empty(tmp_path: Path) -> None:
    """空花名册 = **作者还没声明过任何东西**，不等于「这本书里没有人」。

    花名册是声明出来的（ADR 0004），不是从正文里数出来的：一本刚 import 进来的 722 章
    长篇，花名册就是空的，而正文里当然有人。`roster_total: 0` 不带一句话交出去，
    模型只有一个读法——「这本书没有人物、没有秘密」，然后它会据此认为怎么写都不违背设定。

    这也是「项目不存在」那一档唯一的出口：`store.resolve()` 对一个查无此项目的 id
    返回的同样是空表，两者在返回里今天长得一模一样。
    """
    root = tmp_path / "fresh"
    (root / CHAPTER_DIR).mkdir(parents=True)
    conn = connect(tmp_path / "fresh.db")
    migrate(conn)
    pid = project.create(conn, name="刚导进来的书", root_path=str(root)).id
    (root / chapter_path(1)).write_text("第一章 少年\n\n萧决走进风雪。\n", encoding="utf-8")

    context = ToolContext(store=SqliteStoryGraph(conn), project_id=pid, root_path=str(root))
    result = BookIndex.model_validate_json(_ok("book_index", context))
    assert result.roster_total == 0 and result.chapters_total == 1
    assert any("花名册" in note and "声明" in note for note in result.notes), (
        "零必须带着理由一起出现（ARCHITECTURE §10 约束 8）"
    )
    conn.close()


def test_a_bogus_project_does_not_come_back_as_an_empty_book(book: Book) -> None:
    """项目 id 查无此项时，索引层**不许把它渲染成一本空书**。

    L1 今天是对的（解析不出人物就当场拒），L0 / L2 不是：L0 照样把磁盘上的章标题列出来，
    再配一份空花名册；L2 则把每一章都报成「还没有正文」。两者合起来是一份看起来完整、
    实际上关于另一个项目的索引。
    """
    bogus = ToolContext(
        store=book.store,
        project_id="project:does-not-exist",
        root_path=str(book.root),
        summaries=SummaryStore(book.conn),
    )
    index = BookIndex.model_validate_json(_ok("book_index", bogus))
    assert index.roster_total == 0
    assert any("花名册" in note for note in index.notes)

    summaries = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", bogus, first_chapter=1, last_chapter=3)
    )
    assert_covers_every_chapter_asked_for(summaries)
    assert summaries.unindexed_total == 3 and summaries.unwritten_total == 0, (
        "磁盘上这三章都在 —— 说它们「还没写」是把「我查的是别的项目」说成了「书里没有」"
    )


def test_l1_refuses_instead_of_returning_an_empty_axis(book: Book) -> None:
    """人物解析不出来时是**拒绝**，不是一份「他没出现在任何一章」的空轴。

    判据只问「拒没拒 + 说没说是哪个称呼」。**那句话本身别在这儿抄第二份**：
    它 2026-08-13 改过一次（「查不到」和「说法不对」分成了两句），措辞的网在
    `tests/test_agent_unknown_names.py`，这里再钉一遍就是第二个会漂的判据。
    """
    outcome = dispatch(_call("character_chapters", characters=["查无此人"]), book.context())
    assert outcome.ok is False and "查无此人" in outcome.content
