"""**书内索引的那张网**：章标题 / 摘要 / 正文是 3.1 那张网没罩过的三个面。

`tests/test_agent_tools.py` 罩的是**约束类**工具（`scene_constraints` / `character_state` /
`draft_chapter`）：它们谈论的东西本来就是秘密和未来，所以那张网从第一天就盯着它们。
书内索引（`agent/index.py`）新开了三个面，而这三个面**长得不像泄漏面**——章标题是作者
自己写的，摘要是机器压的，正文就是稿子本身。正因为长得无辜，它们才需要单独量一遍：

| 新面 | 它凭什么可疑 |
|---|---|
| 章标题（L0） | 目录会列出**作者还没写到那儿**的章，标题本身就是剧情（「第 200 章 弑师」） |
| 花名册（L0） | 它按设计就是要列秘密。**列到什么粒度才不算把未来递过去**，见下面那条判据 |
| 摘要（L2） | 它是一段**没有时态**的自然语言，而它住在会话里（ADR 0019 边界四同源） |
| 正文（L3） | 磁盘上可能已经有第 200 章 —— 一次 `chapter_text(200)` 就把它读进对话 |

── 这份文件为什么不并进 `test_agent_tools.py` ────────────────────────────

因为那张网**在这三个面上是空转的**，而空转的守卫比没有守卫更糟：

- 它给 `chapter_summaries` 接的是一个 `chapter_summary` 表**恒空**的库，
  于是 `summaries: []` —— 摘要那一面从来没被真的采样过；
- 它的 `ToolContext.events` 是 `None`，于是事件轴恒 `blind` —— L1 那一半也没采样过。

所以这里**先把料喂满再搜毒**：真的跑一遍 `RollingSummarizer` 落几段摘要、接一个
**故意带毒**的事件读端、让磁盘上真的存在作者还没写到的那几章。
`test_the_wiki_net_is_not_vacuously_clean` 把「料确实喂满了」钉成断言——
少了它，下面每一条都可能是在一个空字符串上搜子串。

── 判据：秘密列到什么粒度才不算泄漏 ──────────────────────────────────────

照既有闸门量，不新发明一把尺。`panel/constraints.py` 的 `ForbiddenEntity` 交出去的是
**显示名 + 首现章号**，一个字的内容都没有；`must_not_reveal` 交出去的是 `NodeRef`
（id/label/name）。所以：

> 花名册里一个秘密**只许出现这两样东西**（名字、首现章号），
> 而它们都是既有闸门**今天已经在交**的。多出来的任何一样都是新开的泄漏面。

别名不在这两样里，所以别名一个都不许出——秘密的非 canonical 别名就是它的内容 tell
（`玄血蛊`），人物的别名则可能本身就是一条认知边（ADR 0004：化名 = 别人不知道他是谁）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from novel_harness import project
from novel_harness.agent import (
    BookIndex,
    ChapterFullText,
    ChapterSummaries,
    CharacterChapters,
    ToolContext,
    dispatch,
    dispatch_all,
    tool_declarations,
)
from novel_harness.agent import tools as agent_tools
from novel_harness.agent.candidates import DraftCandidate
from novel_harness.agent.ports import (
    DraftAsk,
    DraftProduct,
    LandingReport,
    StoredDraft,
    ToolRefused,
)
from novel_harness.agent.tools import SceneConstraintsArgs, ToolSpec
from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.context import DraftContext
from novel_harness.draft.provider import CompletionResult, ToolCall
from novel_harness.draft.rolling_summary import (
    ChapterSummaryStatus,
    RollingSummarizer,
    SummaryMessage,
    SummaryRequest,
    SummaryStore,
)
from novel_harness.draft.summarize import build_summary_messages
from novel_harness.events import EventView, StoryEvent
from novel_harness.graph import (
    AliasSpec,
    ChapterSpec,
    EdgeSource,
    EdgeStatus,
    EvidenceStatus,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import CHAPTER_DIR, chapter_path
from novel_harness.panel.constraints import forbidden_entities

# ══════════════════════════════════════════════════════════════════════════
# 毒：作者写在图上的东西。**出现在任何一个模型看得见的面上都是泄漏。**
# ══════════════════════════════════════════════════════════════════════════

TWIST = "萧决其实是魔尊之子第200章揭晓"
"""`props.twist`，挂在一个**已经登场**的秘密上（`graph.models.NodeRef` 记的实测形态之一）。"""

SECRET_DESC = "血脉的真相是他母亲换了孩子"
"""`secret` 扩展表的 `description`。它不在 `props` 里，所以「不读 props」拦不住它。"""

PLOT_NOTE = "萧决在幽泉窟被顾清音所杀"
"""`props.plot_note`，挂在一个第 200 章才首现的**未来地点**上（实测形态之二）。"""

FUTURE_TWIST = "顾清音在第300章亲手弑师"
"""挂在一个**还没登场的秘密**上。这一档 3.1 的 fixture 里没有：那儿的秘密没有
`first_appears_chapter`，于是花名册那条「未来实体」分支在秘密上一次都没跑过。"""

TELL = "玄血蛊"
"""秘密的**非 canonical 别名 = 内容 tell**（`panel/constraints.secret_surfaces` 的论证）。
它进对话就是把检测器要找的那个词自己写进去，所以索引层一个别名都不许出。"""

EVENT_SUMMARY = "顾清音在藏书阁读到血脉的真相是他母亲换了孩子"
"""事件读端交给索引层的 `StoryEvent.summary`。L1 只该数章号，**不该把它转发出去**。"""

POISON: dict[str, str] = {
    "已登场秘密 props 上的 twist": TWIST,
    "secret 扩展表的 description": SECRET_DESC,
    "未来地点 props 上的 plot_note": PLOT_NOTE,
    "未登场秘密 props 上的 twist": FUTURE_TWIST,
    "秘密的内容 tell（非 canonical 别名）": TELL,
    "事件读端交进来的事件摘要": EVENT_SUMMARY,
}

# ── 反向断言的料：这些**必须**出现，收窄过头是同一个 bug 的另一面 ──────────
SECRET_NAME = "血脉秘密"
FUTURE_SECRET_NAME = "弑师之约"
FUTURE_PLACE = "幽泉窟"

WORKING_CHAPTER = 2
"""作者写到第 2 章。**磁盘上有第 3、4 章**（他往前写过一段又回头改），
所以「未来」这一档在下面每一层都是真的被走到的，不是一个空分支。"""

CHAPTERS: dict[int, tuple[str, str]] = {
    1: ("第一章 少年", "萧决独自走进了北荒的风雪里。"),
    2: ("第二章 藏书阁", "顾清音在藏书阁遇见萧决。"),
    3: ("第三章 渡口", "萧决与顾清音在渡口分别。"),
    # 第四章的**标题本身就是剧情**，而作者只写到第 2 章 —— 章标题这一面的全部风险在这一行。
    4: ("第四章 弑师", "顾清音在渡口拔出了剑。"),
}


# ══════════════════════════════════════════════════════════════════════════
# 一本带毒的书（走生产写路径建，不是手写数据）
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PoisonedBook:
    conn: Connection
    db_path: Path
    project_id: str
    store: SqliteStoryGraph
    root: Path
    secret_id: str

    def context(self, **overrides: Any) -> ToolContext:
        base: dict[str, Any] = {
            "store": self.store,
            "project_id": self.project_id,
            "root_path": str(self.root),
            "summaries": SummaryStore(self.conn),
            "events": PoisonedEvents(self.secret_id),
            "working_chapter": WORKING_CHAPTER,
        }
        base.update(overrides)
        return ToolContext(**base)


@dataclass
class PoisonedEvents:
    """一个**故意带毒**的事件读端：它交给索引层的 `EventView` 里装着事件摘要和被揭示的秘密。

    真的 `SqliteEventStore` 也会交这些（`EventView` 上就有 `event.summary` 和
    `revealed_facts`）—— 换句话说这不是一个假想的坏端口，它是**真端口的形状**。
    索引层只该从里面数章号；把 `view` 整份 `model_dump_json()` 出去是最省事的写法，
    而这个桩就是用来钉住那条最省事的路走不通。
    """

    secret_id: str
    asked: list[tuple[Sequence[str], int, InformationScope]] = field(default_factory=list)

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]:
        self.asked.append((list(character_ids), chapter, scope))
        cast = [
            NodeRef(id=node_id, label=NodeLabel.CHARACTER, name=f"人{index}")
            for index, node_id in enumerate(character_ids)
        ]
        return [
            EventView(
                event=StoryEvent(
                    id="evt_1",
                    project_id=project_id,
                    chapter_number=number,
                    summary=EVENT_SUMMARY,
                    information_scope=InformationScope.CANON,
                    status=EdgeStatus.ACTIVE,
                    source=EdgeSource.EXTRACTOR,
                    evidence_id="ev_1",
                    evidence_status=EvidenceStatus.NONE,
                ),
                participants=cast,
                knowers=cast,
                revealed_facts=[
                    NodeRef(id=self.secret_id, label=NodeLabel.SECRET, name=SECRET_NAME)
                ],
            )
            for number in (2, 4)
        ]


@pytest.fixture
def book(tmp_path: Path) -> Iterator[PoisonedBook]:
    """四章正文 + 两个秘密（一个已登场、一个第 300 章才首现）+ 一个未来地点。

    用文件库不用内存库：`RollingSummarizer` 自己开连接（它 own 每条连接来保证幂等），
    内存库里那是另一个空数据库 —— 而摘要那一面正是 3.1 那张网空转的地方。
    """
    root = tmp_path / "book"
    (root / CHAPTER_DIR).mkdir(parents=True)

    db_path = tmp_path / "book.db"
    conn = connect(db_path)
    migrate(conn)
    pid = project.create(conn, name="青云记-毒", root_path=str(root)).id
    store = SqliteStoryGraph(conn)

    for name in ("萧决", "顾清音"):
        store.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name=name))
    secret = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name=SECRET_NAME,
            props=NodeProps.model_validate({"twist": TWIST}),
            secret=SecretDetail(description=SECRET_DESC),
        )
    )
    # 内容 tell 挂成非 canonical 别名 —— 这是合成小册子里那条「唯一专名 tell」的形状。
    store.add_alias(AliasSpec(project_id=pid, node_id=secret.id, surface=TELL))
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name=FUTURE_SECRET_NAME,
            props=NodeProps.model_validate(
                {"first_appears_chapter": 300, "twist": FUTURE_TWIST}
            ),
            secret=SecretDetail(description=FUTURE_TWIST),
        )
    )
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.LOCATION,
            name=FUTURE_PLACE,
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
    yield PoisonedBook(
        conn=conn,
        db_path=db_path,
        project_id=pid,
        store=store,
        root=root,
        secret_id=secret.id,
    )
    conn.close()


def summarize(book: PoisonedBook, chapters: Sequence[int]) -> list[list[SummaryMessage]]:
    """走**真的** `RollingSummarizer` 落几段摘要，并把发出去的 prompt 原样留下来。

    返回的是「生成侧真的发了什么」——边界四要量的就是它，不是摘要落库之后长什么样。
    """
    sent: list[list[SummaryMessage]] = []

    def analyzer(request: SummaryRequest) -> CompletionResult:
        sent.append(list(request.messages))
        return CompletionResult(
            text=f"第 {request.chapter.number} 章：有人走了，有人留下。",
            model="stub",
            finish_reason="stop",
        )

    summarizer = RollingSummarizer(
        connection_factory=lambda: connect(book.db_path), analyzer=analyzer
    )
    for number in chapters:
        summarizer.ensure(book.project_id, number)
    return sent


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


def _ok(name: str, context: ToolContext, **arguments: Any) -> str:
    outcome = dispatch(_call(name, **arguments), context)
    assert outcome.ok, outcome.content
    return outcome.content


# ══════════════════════════════════════════════════════════════════════════
# 网本身
# ══════════════════════════════════════════════════════════════════════════


DRAFT_ID = "draft:01JTESTTESTTESTTESTTESTTEST"


def _candidate(chapter: int) -> DraftCandidate:
    return DraftCandidate(
        id=DRAFT_ID,
        chapter=chapter,
        ordinal=1,
        units=12,
        note="这一版更冷。",
        preview=f"（第 {chapter} 章草稿）风雪落在肩上。",
    )


def leaks(surface: str, blob: str) -> list[str]:
    """`blob` 里有没有毒 / 有没有 `props`。返回人读得懂的违规描述，空 = 干净。

    判据是**子串命中**：泄漏的形态不止一种（props 整份序列化、拼进一句好心的错误说明、
    被某个 docstring 抄进 schema 的 description），而它们的共同点只有一个——
    那几个字出现在了模型看得见的地方。
    """
    out = [f"{surface} 里出现了{what}" for what, text in POISON.items() if text in blob]
    if '"props"' in blob:
        out.append(f'{surface} 里出现了 "props" —— 索引层的出参只许有名字和纯量')
    return out


def surfaces_of(book: PoisonedBook) -> dict[str, str]:
    """模型看得见的**每一处字**，全部拿到手里。

    每一条工具 × 成功那条路 + 索引四层各自的失败那条路 + 工具声明 + 交给起草侧的那份约束。
    索引层的失败路径单列，是因为它们是**新写的中文句子**——「第 9 章还没写，那是第 200 章
    才发生的事」这种好心的解释就是一次泄漏，而它读起来完全不像。
    """
    captured: list[DraftContext] = []

    class Desk:
        """注入进来的起草台（ADR 0022 之后是三个动作）。三个动作各自都是一个
        模型看得见的面，所以三个都要填满——只填一个的话另外两块屏幕没被扫过。"""

        def write(self, ask: DraftAsk, ctx: DraftContext) -> DraftProduct:
            captured.append(ctx)
            return DraftProduct(candidate=_candidate(ask.chapter))

        def land(self, candidate_id: str) -> LandingReport:
            return LandingReport(
                chapter=WORKING_CHAPTER,
                landed=True,
                note=f"已经写进第 {WORKING_CHAPTER} 章了（章标题保持原样）。",
            )

        def recall(self, candidate_id: str) -> StoredDraft:
            return StoredDraft(
                **_candidate(WORKING_CHAPTER).model_dump(),
                body=f"（第 {WORKING_CHAPTER} 章草稿）风雪落在肩上。",
            )

    context = book.context(drafter=Desk())
    outcomes = dispatch_all(
        [
            # 索引四层，成功那条路。
            _call("book_index"),
            _call("character_chapters", characters=["萧决", "顾清音"]),
            _call("chapter_summaries", first_chapter=1, last_chapter=6),
            _call("chapter_text", chapter=1),
            # **未来那一档**：磁盘上有第 4 章，而作者写到第 2 章。
            _call("chapter_text", chapter=4),
            _call("book_index", from_chapter=3),
            # 3.1 那三个约束类工具也带上：索引层改过 `agent/` 的共用面。
            _call("scene_constraints", chapter=WORKING_CHAPTER),
            _call("character_state", chapter=WORKING_CHAPTER, character="萧决"),
            _call("draft_chapter", chapter=WORKING_CHAPTER, goal="写顾清音在藏书阁"),
            # ADR 0022 拆出来的另外两个动作，各自一个新的返回面。
            _call("save_draft", draft_id=DRAFT_ID),
            _call("read_draft", draft_id=DRAFT_ID),
            # ADR 0024 的问作者：出参直接摆到作者面前，所以它也是一个面。
            _call(
                "ask_author",
                question="这一场你想让顾清音看见那封信吗？",
                options=["让她看见", "先不给她"],
            ),
            # ADR 0023 的记规矩：出参会原样进对话历史，所以它也是一个面。
            _call("remember_rule", rule="这一章别写打斗"),
            # 失败那条路：拿秘密 / 未来地点 / 不存在的章去问每一层。
            #
            # **这里故意不拿内容 tell（`TELL`）去问。** 拒绝语会把称呼原样回显，而那个串是
            # 模型自己打进来的——把它算成「工具交出去的东西」会让这张网抓自己的尾巴。
            # 那条路单独由 `test_a_refusal_echoes_only_what_the_model_typed` 量。
            _call("character_chapters", characters=[SECRET_NAME]),
            _call("character_chapters", characters=[FUTURE_PLACE]),
            _call("chapter_text", chapter=200),
            _call("character_state", chapter=WORKING_CHAPTER, character=FUTURE_SECRET_NAME),
        ],
        context,
    )
    assert [outcome.ok for outcome in outcomes] == [True] * 13 + [False] * 4, (
        "采样计划自己漂了：成功/失败两条路的条数对不上，下面搜的可能是另一批面"
    )
    assert captured, "起草工具没把约束交给起草侧 —— 进 prompt 的那一面没被采到"

    out = {f"{o.name} 的返回（ok={o.ok}）": o.content for o in outcomes}
    out["发给模型的工具声明"] = json.dumps(tool_declarations(), ensure_ascii=False)
    out["交给起草侧的约束（进 prompt 的那一份）"] = captured[0].model_dump_json()
    return out


# ══════════════════════════════════════════════════════════════════════════
# 头条
# ══════════════════════════════════════════════════════════════════════════


def test_no_wiki_surface_ever_carries_secret_content(book: PoisonedBook) -> None:
    """**索引四层一个字的秘密都不许交出去**（ADR 0019 边界一，那条修复成本写着「不可回收」）。

    工具一旦把秘密交出去过，它已经在作者的持久化对话里了，改代码删不掉。
    """
    summarize(book, [1, 2, 3, 4])
    offenders = [
        line for surface, blob in surfaces_of(book).items() for line in leaks(surface, blob)
    ]
    assert not offenders, (
        "书内索引把秘密交出去了：\n  " + "\n  ".join(offenders) + "\n"
        "章标题 / 摘要 / 正文这三个面长得无辜，但它们经手的对象和约束类工具是同一批："
        "`NodeProps` 是 extra=\"allow\"，`SecretDetail.description` 不在 props 里，"
        "而 `EventView` 自带 `event.summary` 和 `revealed_facts`。\n"
        "这是不可回收的：对话是持久化的，交出去过的那段话改代码删不掉。"
    )


def test_the_wiki_net_is_not_vacuously_clean(book: PoisonedBook) -> None:
    """**空的面上搜毒永远是干净的。** 这条钉住上面那一条真的搜过东西。

    3.1 那张网在这三个面上恰好就是空转的（摘要表恒空、事件端口没接），
    所以这不是一条假想的自守卫——它是那张网今天真实的形状。
    """
    sent = summarize(book, [1, 2, 3, 4])
    assert sent, "一次总结都没生成 —— 摘要那一面又是空的"

    context = book.context()
    index = BookIndex.model_validate_json(_ok("book_index", context))
    assert index.roster and index.chapters, "L0 两半都得有料"
    assert any(entry.future for entry in index.chapters), "磁盘上得真的有作者还没写到的章"

    axis = CharacterChapters.model_validate_json(
        _ok("character_chapters", context, characters=["萧决", "顾清音"])
    )
    assert not axis.mentioned_together.blind and axis.mentioned_together.total > 0
    assert not axis.events_together.blind and axis.events_together.total > 0, (
        "事件轴瞎着 —— 那一半的返回是空的，搜毒搜了个寂寞"
    )

    digest = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", context, first_chapter=1, last_chapter=6)
    )
    assert digest.summaries, "摘要那一面是空的 —— 正是 3.1 那张网空转的那一处"

    text = ChapterFullText.model_validate_json(_ok("chapter_text", context, chapter=4))
    assert text.text.strip(), "正文那一面是空的"

    # 毒真的在图上（fixture 自己漏了的话，上面每一条都是在搜一个不存在的串）。
    node = book.store.resolve(book.project_id, [SECRET_NAME])[0].hits[0].node
    assert node.props.model_dump().get("twist") == TWIST
    assert TELL in {
        resolution.surface
        for resolution in book.store.resolve(book.project_id)
        if resolution.hits and resolution.hits[0].node.id == book.secret_id
    }, "内容 tell 那条别名没落进库 —— 「别名一个都不出」这条断言是空的"


def test_the_wiki_net_catches_a_leaky_layer(
    book: PoisonedBook, monkeypatch: pytest.MonkeyPatch
) -> None:
    """喂一个**故意泄漏**的假索引层进同一条派发路径，断言网抓得住它。

    走 `dispatch()` 而不是直接调 handler：网罩的是 `ToolOutcome.content`，
    而序列化正是泄漏真正发生的那一步。
    """
    monkeypatch.setitem(agent_tools.TOOLS, LEAKY_PROBE.name, LEAKY_PROBE)
    outcome = dispatch(_call(LEAKY_PROBE.name, chapter=WORKING_CHAPTER), book.context())
    assert outcome.ok, outcome.content

    caught = leaks("假索引层的返回", outcome.content)
    assert caught, "网看不见一个直接把 Node 交出去的层 —— 上面所有断言都是永远绿的"
    assert any("twist" in line for line in caught)
    assert any("props" in line for line in caught)


def test_the_wiki_net_does_not_cry_wolf(
    book: PoisonedBook, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个节点、同一条派发路径，只是收窄成了 `NodeRef` —— 必须绿，**且显示名还在**。

    误报会让人把守卫关掉，而关掉的守卫等于没有。
    """
    monkeypatch.setitem(agent_tools.TOOLS, CLEAN_PROBE.name, CLEAN_PROBE)
    outcome = dispatch(_call(CLEAN_PROBE.name, chapter=WORKING_CHAPTER), book.context())
    assert outcome.ok, outcome.content
    assert leaks("干净层的返回", outcome.content) == []
    assert SECRET_NAME in outcome.content


# ══════════════════════════════════════════════════════════════════════════
# 反向断言：收窄过头是同一个 bug 的另一面
# ══════════════════════════════════════════════════════════════════════════


def test_the_wiki_still_shows_the_names_the_author_needs(book: PoisonedBook) -> None:
    """章标题、人物显示名、秘密显示名、摘要正文 —— **这四样必须在。**

    只剩一串 ULID 和一堆纯量的索引，模型钻不下去，作者也看不出系统在谈哪一章。
    过度收窄不会有任何断言变红，所以它必须在这里被单独钉一次。
    """
    summarize(book, [1, 2])
    context = book.context()

    index = BookIndex.model_validate_json(_ok("book_index", context))
    assert [entry.title for entry in index.chapters] == [
        heading for _, (heading, _) in sorted(CHAPTERS.items())
    ], "章标题必须逐字给 —— 目录没有标题就只是一串数字"
    names = {entry.name for entry in index.roster}
    assert {"萧决", "顾清音", SECRET_NAME, FUTURE_PLACE, FUTURE_SECRET_NAME} <= names

    digest = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", context, first_chapter=1, last_chapter=2)
    )
    assert all(entry.summary.strip() for entry in digest.summaries), "摘要正文必须逐字给"

    text = ChapterFullText.model_validate_json(_ok("chapter_text", context, chapter=1))
    assert CHAPTERS[1][1] in text.text, "正文必须逐字给（ADR 0007：磁盘那份就是稿子）"


def test_a_refusal_echoes_only_what_the_model_typed(book: PoisonedBook) -> None:
    """拿一个**内容 tell** 去问人物轴，拒绝语回显那个称呼 —— 但**不许多说一个字**。

    回显本身不是泄漏：那个串是模型自己打进来的（同 `character_state` 的既有拒绝语，
    `tests/test_agent_tools.py::test_a_refusal_says_why` 已经把那条形状钉住了）。
    真正的风险是拒绝语顺手多解释两句：「它是「血脉秘密」的别名，那条秘密第 300 章才揭晓」
    —— 一句好心的解释，而它读起来完全不像泄漏。所以这里量的是**除了回显之外还说了什么**。
    """
    outcome = dispatch(_call("character_chapters", characters=[TELL]), book.context())
    assert outcome.ok is False
    assert TELL in outcome.content, "连称呼都不回显的话，模型不知道是哪个词被拒了"

    rest = outcome.content.replace(TELL, "")
    assert not leaks("人物轴对内容 tell 的拒绝语（去掉回显之后）", rest)
    for extra in (SECRET_NAME, "300", "200"):
        assert extra not in rest, (
            f"拒绝语里多说了「{extra}」—— 它把一个 tell 连到了显示名 / 首现章号上，"
            "而模型本来只知道自己打的那个词"
        )


def test_the_roster_gives_a_secret_exactly_what_the_existing_gate_gives(
    book: PoisonedBook,
) -> None:
    """**判据不新发明**：花名册里的一个秘密，只许有 `forbidden_entities` 已经在交的那两样。

    既有闸门（`panel/constraints.py`）对一个还没登场的实体交的是**显示名 + 首现章号**，
    一个字的内容都没有。花名册按设计就是要列秘密，所以它必须照着这把尺量：
    名字和首现章号可以，**别名、描述、`props` 一律不行**。
    """
    context = book.context()
    index = BookIndex.model_validate_json(_ok("book_index", context))
    entry = next(item for item in index.roster if item.name == FUTURE_SECRET_NAME)

    gate = {
        item.node.name: item.first_appears_chapter
        for item in forbidden_entities(book.store, book.project_id, WORKING_CHAPTER)
    }
    assert gate.get(FUTURE_SECRET_NAME) == 300, "闸门自己就该拦住它，否则这条测试没有参照物"
    assert entry.first_appears_chapter == gate[FUTURE_SECRET_NAME], (
        "首现章号必须来自那个唯一闸门，不是这一层自己读 props"
    )

    # 交出去的字段就这么几个，且每一个都能在既有闸门上找到对应物。
    assert set(entry.model_dump()) == {"name", "label", "first_appears_chapter", "future"}
    blob = json.dumps(index.model_dump(), ensure_ascii=False)
    assert TELL not in blob, "别名进了目录 —— 秘密的非 canonical 别名就是它的内容 tell"


# ══════════════════════════════════════════════════════════════════════════
# 「这是你还没写到那儿之后的」—— 标不挡，但**必须真的标到**
# ══════════════════════════════════════════════════════════════════════════
#
# ADR 0019 边界二把「模型的推理可能被污染」列成**接受**的残余代价，而它接受的前提之一是
# **作者在聊天里看得见**。所以整份返回上那一句不是装饰：它是那条 ADR 成立的条件。
# 少了它，工具就是在一个作者看不见的地方把第 300 章的东西递进了这一轮对话。


def test_the_roster_marks_the_entities_the_author_has_not_written_yet(
    book: PoisonedBook,
) -> None:
    """花名册里的未来实体**每条都要有自己的标记**，不能只靠读者拿两个数去减。

    章目录那一半有 `ChapterEntry.future`；花名册这一半必须同样有，否则
    「`first_appears_chapter is None`」同时表示「已经登场」和「这一轮压根没算」，
    而模型要做的恰恰是逐条推理。
    """
    index = BookIndex.model_validate_json(_ok("book_index", book.context()))
    marked = {entry.name for entry in index.roster if entry.future}
    assert marked == {FUTURE_PLACE, FUTURE_SECRET_NAME}, (
        "花名册里第 200 / 300 章才首现的那两个东西没被逐条标出来"
    )
    assert not any(entry.future for entry in index.roster if entry.name == "萧决")


def test_the_future_note_covers_the_roster_too(book: PoisonedBook) -> None:
    """**磁盘上没有未来章、但花名册里有未来实体**时，那一句话必须照样出现。

    这是这条缝最真实的形状：作者线性往下写，`chapters/` 里最远就是他写到的那一章，
    于是章目录那一半一条 `future` 都没有 —— 而花名册里躺着第 200 章的地点和第 300 章的
    秘密。整份返回上那一句由「章目录里有几条未来」算出来，于是它**恰好是空的**，
    `notes` 整个是 `[]`：作者在聊天里看不到任何提示，而 ADR 0019 边界二接受那份残余代价
    的前提正是「作者看得见」。
    """
    # 作者写到磁盘上的最后一章 —— 章目录里一条未来都没有。
    context = book.context(working_chapter=max(CHAPTERS))
    index = BookIndex.model_validate_json(_ok("book_index", context))
    assert not any(entry.future for entry in index.chapters), "这条测试的前提没成立"
    assert any(entry.future for entry in index.roster), "花名册里得真的有未来实体"

    assert any("还没写到" in note for note in index.notes), (
        "花名册里有第 200 / 300 章的东西，而整份返回上一句提示都没有：\n"
        f"  notes = {index.notes}\n"
        "ADR 0019 边界二把「推理被污染」列成**接受**的代价，前提之一是「作者看得见」。"
    )

    # 数也得对：说「有 N 条来自未来」而实际有 N+2 条，比不说更坏。
    counted = sum(entry.future for entry in index.chapters) + sum(
        entry.future for entry in index.roster
    )
    note = next(note for note in index.notes if "还没写到" in note)
    assert f"有 {counted} 条" in note, f"未来条数数漏了花名册那一半：{note}"


# ══════════════════════════════════════════════════════════════════════════
# 区间是模型给的，而它今天没有上界
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class RecordingSummaries:
    """只记「被问了多大一段区间」的摘要读端。**它不返回任何东西**——量的是入口那一侧。"""

    spans: list[tuple[int, int]] = field(default_factory=list)

    def coverage(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> list[ChapterSummaryStatus]:
        self.spans.append((first_chapter, last_chapter))
        return []


def test_a_model_chosen_range_cannot_ask_for_more_than_the_book_can_hold(
    book: PoisonedBook,
) -> None:
    """`last_chapter` 是**模型填的**，而 `coverage()` 会为区间里每一章物化一行。

    实测：`last_chapter=2_000_000` 会在任何预算生效之前吃掉约 1.1 GB —— 而它还
    **返回成功**（出参只有 17 KB，因为预算是在那之后才裁的）。所以「预算就是那个上界」
    这句话对**返回**成立，对**代价**不成立：模型写一个 `99999999` 就能把作者的进程打死，
    而那是它想说「把全书摘要都给我」时最自然的写法。

    上界不许是一个拍出来的数：它取「预算装得下多少个章号」和「这本书在磁盘上最远到第几章」
    的大者 —— 两条各自成立的下界，取大者所以哪一条都不会单独把这一层弄瞎。
    """
    recorder = RecordingSummaries()
    context = book.context(summaries=recorder)
    payload = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", context, first_chapter=1, last_chapter=2_000_000)
    )

    first, last = recorder.spans[-1]
    ceiling = max(context.return_units, max(CHAPTERS))
    assert last - first + 1 <= ceiling, (
        f"模型要了第 {first}–{last} 章，而这一层原样问了下去（{last - first + 1} 章）。\n"
        "`coverage()` 每一章物化一个 `ChapterSummaryStatus`：2,000,000 章实测 ≈1.1 GB，"
        "而预算是在这之后才裁的，所以它拦不住这一步。"
    )
    assert payload.last_chapter == last, (
        "出参上的区间还写着模型要的那一段，而实际只查了一小段 —— "
        "「第 1–2000000 章里有 3 章没摘要」是一句谎话"
    )
    assert any("接着" in note or "再问" in note for note in payload.notes), (
        "夹了区间就要说出来，而且要说得出怎么接着拿下一段（同 L0 的 `from_chapter`）"
    )


def test_a_normal_range_is_not_clamped(book: PoisonedBook) -> None:
    """**误伤检查**：正常的区间一个字都不许被夹掉。

    没有这一条，上面那个上界可以被写成 1 而全绿 —— 那时索引层的 L2 等于没有。
    """
    summarize(book, [1, 2, 3, 4])
    recorder = RecordingSummaries()
    _ok("chapter_summaries", book.context(summaries=recorder), first_chapter=1, last_chapter=6)
    assert recorder.spans[-1] == (1, 6)

    payload = ChapterSummaries.model_validate_json(
        _ok("chapter_summaries", book.context(), first_chapter=1, last_chapter=6)
    )
    assert payload.first_chapter == 1 and payload.last_chapter == 6
    assert [entry.chapter for entry in payload.summaries] == [1, 2, 3, 4]


# ══════════════════════════════════════════════════════════════════════════
# 边界四：总结的**生成侧** prompt 不许含图谱事实
# ══════════════════════════════════════════════════════════════════════════
#
# 索引层把摘要读进了对话，于是摘要第一次成为「模型看得见的字」。它读得干净不够——
# 写进去的时候要是就带着图谱事实，读出来当然带着，而那时错的地方在 `draft/`，
# 不在 `agent/`。这一节量的是写那一侧。


def test_the_summary_prompt_is_the_chapter_text_and_nothing_else(book: PoisonedBook) -> None:
    """总结的 prompt = 一个跨章不变的 system + **本章正文逐字**，没有第三样东西。

    边界四的表说得很死：总结禁止含 `must_not_reveal` 的内容、谁知道 / 不知道什么、
    任何绑定章号的状态、秘密名、未登场实体名。今天这条链上一个 `scene_view` 都没有，
    但「顺手把认知矩阵拼进 system prompt 让摘要更准」是一个很自然的下午——
    那一刻这条断言必须红，因为压进散文之后「此时」是第几章就再也答不出来了。
    """
    sent = summarize(book, [1, 2, 3, 4])
    assert len(sent) == len(CHAPTERS), "总结一次都没真的发出去，这条测试是空的"

    for messages, number in zip(sent, sorted(CHAPTERS), strict=True):
        heading, body = CHAPTERS[number]
        chapter_text = f"{heading}\n\n{body}\n"
        assert messages == build_summary_messages(chapter_text), (
            f"第 {number} 章的总结 prompt 不再是「常量 system + 本章正文」了"
        )
        assert [message["role"] for message in messages] == ["system", "user"]

    blob = json.dumps(sent, ensure_ascii=False)
    assert not leaks("总结的生成侧 prompt", blob), "总结 prompt 里出现了图谱上的东西"
    for forbidden in (SECRET_NAME, FUTURE_SECRET_NAME, FUTURE_PLACE):
        assert forbidden not in blob, (
            f"总结 prompt 里出现了「{forbidden}」——秘密名 / 未登场实体名是边界四点名禁止的。"
            "摘要是一段没有时态的散文，它进了会话就再也说不清「此时」是第几章。"
        )


def test_that_summary_prompt_guard_can_see_an_injected_fact() -> None:
    """**自守卫**：上面那条全靠 `build_summary_messages` 真的只吃正文。

    喂一份「顺手把约束拼进去了」的假 prompt 进同一个判据，断言它红。
    """
    poisoned: list[SummaryMessage] = [
        {"role": "system", "content": f"本章不许说破：{SECRET_NAME}。"},
        {"role": "user", "content": "第一章 少年\n\n萧决独自走进了北荒的风雪里。\n"},
    ]
    blob = json.dumps([poisoned], ensure_ascii=False)
    assert SECRET_NAME in blob, "扫描器看不见被拼进去的秘密名"
    assert poisoned != build_summary_messages(poisoned[1]["content"])


# ══════════════════════════════════════════════════════════════════════════
# 探针（本仓惯例：一个永远绿的守卫比没有守卫更糟）
# ══════════════════════════════════════════════════════════════════════════


class _LeakyResult(BaseModel):
    """一个**故意泄漏**的索引出参：它把整个 `Node` 交出去。

    这不是假想的坏写法，它是最自然的那一种：`_roster()` 手上已经有 `node` 了，
    直接把它塞进出参比先摘一个 `NodeRef` 少一行。
    """

    model_config = ConfigDict(frozen=True)

    entries: list[Node]


class _CleanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: list[NodeRef]


def _the_secret(context: ToolContext) -> Node:
    hits = context.store.resolve(context.project_id, [SECRET_NAME])[0].hits
    if not hits:
        raise ToolRefused("探针自己找不到那个秘密")
    return hits[0].node


def _leaky_handler(args: SceneConstraintsArgs, context: ToolContext) -> _LeakyResult:
    return _LeakyResult(entries=[_the_secret(context)])


def _clean_handler(args: SceneConstraintsArgs, context: ToolContext) -> _CleanResult:
    return _CleanResult(entries=[NodeRef.of(_the_secret(context))])


LEAKY_PROBE = ToolSpec(
    name="_leaky_wiki_probe",
    description="故意把整个 Secret 节点当成一条花名册记录交出去。",
    args=SceneConstraintsArgs,
    handler=_leaky_handler,
)

CLEAN_PROBE = ToolSpec(
    name="_clean_wiki_probe",
    description="同一个节点，收窄成 NodeRef。",
    args=SceneConstraintsArgs,
    handler=_clean_handler,
)
