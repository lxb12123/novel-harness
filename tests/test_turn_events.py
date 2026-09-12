"""事件流是一个**全新的输出面**，而边界一不可回收
（[ADR 0024](../docs/adr/0024-a-turn-is-a-conversation-not-a-black-box.md)「修复成本」）。

这份文件是对 `tests/test_agent_events.py` 的**对抗性复核**，不是它的第二份拷贝。
两者的差别只有一条，而那一条就是这份文件存在的理由：

> **先证明料喂满了，再搜。**

上一轮的泄漏网被证明过是空转的（3.2 那次：`ToolContext.events` 是 `None`、
库里 `chapter_summary` 恒空 ⇒ 在一个空字符串上搜子串）。所以这里的每一条搜毒断言
**前面都压着一条「这一轮真的有毒可漏」的断言**：走完工具表里**每一条**工具、
每一条都 `ok=True`、而且那几份返回拼起来**真的**命中毒清单。命中不了就先红在那儿——
一个永远绿的守卫比没有守卫更糟，因为它还提供安全感。

── 毒分两种，别混 ────────────────────────────────────────────────────────

| | 从哪儿来 | 事件流里该不该有 |
|---|---|---|
| `SECRET_CONTENT`（`props.twist` / `SecretDetail.description` / `plot_note`） | 作者写在图上的 | **一个字都不许** |
| `工具返回里的字`（摘要正文 / 章正文 / 一稿的自述） | 工具查回来的 | **一个字都不许**（ADR 0024：事件流不许把中间过程摊开） |

第二行比第一行宽，而且它才是这一刀真正新开的口子：今天一轮的返回是**投影过的**
（`api/chat.py::_visible` 把工具返回整条挡在屏幕外，只给一个「查了几次」的数），
**而事件是绕过那道闸的第二条路**。所以这里量的不是「有没有 twist」，是
**「工具查到的任何一段字有没有顺着事件上屏」**。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import novel_harness.agent.drafting as drafting
import novel_harness.agent.tools as agent_tools
from test_agent_drafting import ENDPOINT, MODEL, FakeDrafting, _NoSummaries, _root, _write
from test_agent_loop import Ledger, ScriptedModel, a_context, say, wants
from test_agent_tools import (
    CHAPTER,
    KNOWS_QUOTE,
    SECRET_CONTENT,
    TWIST,
    WHERE_QUOTE,
    World,
    conn,
    world,
)
from test_wording_guard import dev_shapes

from novel_harness.agent.candidates import DraftCandidate
from novel_harness.agent.drafting import chapter_drafter
from novel_harness.agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    Role,
    StopReason,
    TurnEvent,
    TurnEventKind,
    TurnLimits,
    run_turn,
    start_conversation,
)
from novel_harness.agent.ports import (
    DraftAsk,
    DraftProduct,
    StoredDraft,
    ToolContext,
)
from novel_harness.agent.tools import TOOL_NAMES, AuthorQuestion
from novel_harness.api.chat import _visible
from novel_harness.checks.service import RulesReader
from novel_harness.db import Connection, connect
from novel_harness.draft.capabilities import resolve_capabilities
from novel_harness.draft.context import DraftContext
from novel_harness.draft.product_draft import DraftRefused
from novel_harness.draft.provider import ProviderConfig, ProviderError, ToolCall
from novel_harness.draft.rolling_summary import SUMMARY_VERSION, SummaryStore
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.notices import NoticeReader

__all__ = ["conn", "world"]
"""两个 fixture 从 `test_agent_tools` 借来（同 `conftest.py` 那次 re-export）。"""

REPO_ROOT = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
# 采样点：一块会记账的屏
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Screen:
    """这一轮往界面上推过的每一条。**它自己一条都不许过滤**——过滤了就等于在测一个
    我们希望的事件流，不是真的那一个。"""

    events: list[TurnEvent] = field(default_factory=list)
    explode_from: int | None = None
    """从第几条开始炸（`0` = 第一条就炸，`None` = 不炸）。"""

    def __call__(self, event: TurnEvent) -> None:
        seen = len(self.events)
        self.events.append(event)
        if self.explode_from is not None and seen >= self.explode_from:
            raise RuntimeError("界面掉线了")

    def of(self, kind: TurnEventKind) -> list[TurnEvent]:
        return [event for event in self.events if event.kind is kind]

    @property
    def pushed(self) -> str:
        """**这一轮推上屏的每一个字节。** 搜毒搜的是它。

        逐条 `model_dump_json()`：泄漏可以躺在任何一个字段上（一个多出来的 `detail`、
        一个原样带出去的工具名），而它们的共同点只有一个——它被推给界面了。
        """
        return "\n".join(event.model_dump_json() for event in self.events)


# ══════════════════════════════════════════════════════════════════════════
# 带毒的一本书：**每一条工具都真的答得出话来**
# ══════════════════════════════════════════════════════════════════════════

SUMMARY_TELL = "第七章摘要说破了顾清音的来历"
"""喂进 `chapter_summary` 表的那句话。

**它不是 `SECRET_CONTENT` 那种毒**——它是一段真的会被 `chapter_summaries` 返回给模型的字。
拿它当探针是因为 3.2 那次的空转正是从这儿开始的：`chapter_summary` 恒空 ⇒
那条工具返回里一个字都没有 ⇒ 在空字符串上搜子串永远绿。
"""

DRAFT_TELL = "这一稿把幽泉窟那段挪到了开头"
"""起草台三个动作（写 / 存 / 读回）的返回里各埋一句。它们是**注入进来的**端口的出参，
也就是这张表上最不受引擎控制的那三份返回。"""

DRAFT_BODY_TELL = "风雪落在肩上，他没有回头。"

TOOL_RETURN_TELLS = (SUMMARY_TELL, DRAFT_TELL, DRAFT_BODY_TELL, KNOWS_QUOTE)
"""**工具查回来的字**。事件流里一条都不许出现（ADR 0024：不许把中间过程摊开）。"""

DRAFT_ID = "draft:01JEVENTEVENTEVENTEVENTEVE"


class PoisonedDesk:
    """注入进来的那个起草台，**三个动作的返回里各埋一句**（ADR 0022 之后它们是三个面）。

    只填一个的话，另外两块屏在这张网里从来没被扫过。
    """

    def __init__(self) -> None:
        self.seen: list[DraftContext] = []

    def _candidate(self, chapter: int) -> DraftCandidate:
        return DraftCandidate(
            id=DRAFT_ID,
            chapter=chapter,
            ordinal=1,
            units=12,
            note=DRAFT_TELL,
            preview=DRAFT_BODY_TELL,
            created_at="2026-08-12T00:00:00.000Z",
        )

    def write(self, ask: DraftAsk, ctx: DraftContext, **kwargs: Any) -> DraftProduct:
        self.seen.append(ctx)
        return DraftProduct(candidate=self._candidate(ask.chapter))

    def revise(self, ask: Any, ctx: DraftContext, **kwargs: Any) -> DraftProduct:
        """改一段（ADR 0049）：返回和 `write` 同一个形状，同一句毒。"""
        self.seen.append(ctx)
        return DraftProduct(candidate=self._candidate(ask.chapter))

    def recall(self, candidate_id: str) -> StoredDraft:
        return StoredDraft(**self._candidate(CHAPTER).model_dump(), body=DRAFT_BODY_TELL)


def _feed_a_summary(conn: Connection, project_id: str) -> None:
    """往 `chapter_summary` 里塞一条真的摘要。

    **没有它，`chapter_summaries` 的返回里一个字都没有**，而这份文件的头条会在
    一个空字符串上搜子串——正是 3.2 那次空转的形态。
    """
    from hashlib import sha256

    summary_id = f"summary:{project_id}:{CHAPTER}"
    row = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, CHAPTER),
    ).fetchone()
    assert row is not None
    conn.execute(
        """
        INSERT OR IGNORE INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            schema_version, prompt_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (summary_id, project_id, row["id"], CHAPTER, SUMMARY_TELL,
         sha256(SUMMARY_TELL.encode("utf-8")).hexdigest(), SUMMARY_VERSION, "hash-for-the-net"),
    )
    conn.execute(
        """
        UPDATE chapter_summary_head SET current_summary_id = ?
         WHERE chapter_id = ?
        """,
        (summary_id, row["id"]),
    )
    conn.commit()


def _a_track_check() -> Any:
    """一个不花钱的轨道核对：**答得出话，而且那句话带着理由**（§10 约束 8）。"""
    from novel_harness.agent.ports import TrackVerdict

    def check(chapter: int) -> TrackVerdict:
        return TrackVerdict(chapter=chapter, note="这一章就在最前沿，后面没有已经写完的章。")

    return check


def a_wired_context(world: World, **overrides: Any) -> ToolContext:
    """**每一个接线口都接上**的那份上下文。

    三个 `None` 是接线口不是可选功能（`ports.py`）：空着的时候那几条工具明确回一句
    「没接线」——`ok=False`、返回里一个字的书都没有。**在那种上下文上搜毒是空转。**
    """
    _feed_a_summary(world.conn, world.project_id)
    base: dict[str, Any] = {
        "drafter": PoisonedDesk(),
        "summaries": SummaryStore(world.conn),
        "events": SqliteEventStore(world.conn),
        "working_chapter": CHAPTER,
        # 轨道核对（2026-08-23）。这儿给的是一个**假的核对模型**，因为真核对要花钱；
        # 这张网测的是「这条工具答不答得上话、作者看不看得见它在干什么」，
        # 而轨道那一问本身有 `tests/test_advisory_review.py` 单独钉着。
        "track_check": _a_track_check(),
        # 右栏那两栏的只读端口（2026-09-12）。**真的读端**，不是桩：这张网要的是
        # 「每一条工具都答得上话」，一句「没接线」等于那一格没被考到。
        "rules": RulesReader(world.conn),
        "notices": NoticeReader(world.conn, world.store),
    }
    base.update(overrides)
    return world.context(**base)


MAX_CALLS_PER_STEP = 6
"""一步之内最多发几个（`TurnLimits.max_calls_per_step` 的默认值，这儿显式写一遍是为了
下面那个切点算得出来）。"""

EVERY_TOOL = (
    ("scene_constraints", {"chapter": CHAPTER}),
    ("character_state", {"chapter": CHAPTER, "character": "萧决"}),
    ("book_index", {}),
    ("character_chapters", {"characters": ["萧决", "顾清音"]}),
    ("chapter_summaries", {"first_chapter": 1, "last_chapter": CHAPTER}),
    ("chapter_text", {"chapter": CHAPTER}),
    ("draft_chapter", {"chapter": CHAPTER, "brief": "写一场雪，收在他没抬头。"}),
    ("read_draft", {"draft_id": DRAFT_ID}),
    ("remember_rule", {"rule": "这一章别写打斗", "until": "这一章写完为止"}),
    # `get_result` 要放在**第二批**：stored 表在批创建时从 live 数，第一批都还没跑
    # 的话它手里是空的，取 1 号会拒绝（`ok=False`）——这一节要的是每条都 ok=True。
    ("get_result", {"id": 1}),
    ("check_track", {"chapter": CHAPTER}),
    # 右栏那四栏（2026-09-12，`agent/panels.py`）。
    ("character_card", {"chapter": CHAPTER, "character": "萧决"}),
    ("chapter_events", {"first_chapter": 1, "last_chapter": CHAPTER}),
    ("validation_rules", {"chapter": CHAPTER}),
    ("notifications", {}),
    # 改一段（ADR 0049）：引的是这一章正文里真有的那一句。
    ("revise_passage", {"chapter": CHAPTER, "edits": [{"quote": WHERE_QUOTE, "brief": "改软一点"}]}),
    ("ask_author", {"question": "这一场你想让萧决知道那件事吗？",
                    "options": ["让他知道", "先瞒着他"]}),
)
"""**表里每一条，一条不落。** `ask_author` 排在最后是必须的：它会当场收掉这一轮
（ADR 0024），排在中间的话它后面那几条一个都跑不到。

**这里不写「几条」**：数量随表长，而它有一条自己的断言（下面那句 `called == TOOL_NAMES`）
——写死一个数只会在加工具的那天多红一处，而那一处红的是数字不是事实。"""


@dataclass
class WholeTable:
    """走完整张表的那一轮：屏 + 每一条工具的原返回。"""

    screen: Screen
    result: Any
    outcomes: dict[str, str]

    @property
    def tool_returns(self) -> str:
        """**工具真的查回来的那些字**，拼成一条。它是「料喂满了没有」的度量尺。"""
        return "\n".join(self.outcomes.values())


def run_the_whole_table(world: World, *, screen: Screen | None = None) -> WholeTable:
    """一次 `run_turn` 里把表里每一条工具都叫一遍，**每一条都要 `ok=True`**。

    一次全发完会撞 `max_calls_per_step`（默认 6），所以分两步——这也更接近真形态。
    **切点按批宽度算，不写死**：加一条工具就该自动落到第二批里，而不是让这一行悄悄
    发出一批超宽的调用（那时这一轮会停在 `BATCH_TOO_WIDE` 上，一条工具都跑不到）。
    """
    board = screen if screen is not None else Screen()
    batches = [
        EVERY_TOOL[i : i + MAX_CALLS_PER_STEP]
        for i in range(0, len(EVERY_TOOL), MAX_CALLS_PER_STEP)
    ]
    model = ScriptedModel(
        script=[
            wants(
                *[(name, json.dumps(args)) for name, args in batch],
                text=(
                    "我先把这一章的底摸清楚。"
                    if i == 0
                    else "接着写一稿，写完有件事想问你。"
                ),
            )
            for i, batch in enumerate(batches)
        ]
        + [
            say("这一句到不了 —— 上一步那个问句会把这一轮收掉。"),
        ],
    )
    context = a_wired_context(world)
    result = run_turn(
        start_conversation().with_author(f"写第 {CHAPTER} 章"),
        context=context,
        model=model,
        ledger=Ledger(),
        on_event=board,
        limits=TurnLimits(
            max_steps=len(batches) + 1,
            max_calls_per_step=MAX_CALLS_PER_STEP,
        ),
    )
    # **按 `call_id` 认领，不按下标对齐**：位置对齐在有壳（`UNRUN_CALL`）的那一轮会
    # 整体错位，而错位的症状是「某条工具的返回里没有那句话」——读起来像一个泄漏结论。
    named = {
        call.id: call.name
        for message in result.conversation.messages
        for call in message.tool_calls
    }
    return WholeTable(
        screen=board,
        result=result,
        outcomes={
            named[message.tool_call_id]: message.content
            for message in result.conversation.messages
            if message.role is Role.TOOL and message.tool_call_id in named
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 一、料真的喂满了 —— **这一节在的理由是 3.2 那次空转**
# ══════════════════════════════════════════════════════════════════════════


def test_the_run_that_the_net_searches_actually_exercised_every_tool(world: World) -> None:
    """**先证明料喂满了，再搜。**

    三件事一起量：① 表里每一条工具都被叫到了；② 每一条都 `ok=True`（`ok=False` 多半
    是「没接线」，那种返回里一个字的书都没有）；③ 这一轮真的发出了事件。

    这一条红 = 下面那些搜毒断言全都在空集上跑，**它们的绿一文不值**。
    """
    ran = run_the_whole_table(world)

    called = {event.tool for event in ran.screen.of(TurnEventKind.TOOL_FINISHED)}
    assert called == TOOL_NAMES, f"没走到的工具：{sorted(TOOL_NAMES - called)}"
    failed = [
        event.tool for event in ran.screen.of(TurnEventKind.TOOL_FINISHED) if not event.ok
    ]
    assert not failed, f"这几条没答上话（多半是没接线，那种返回是空的）：{failed}"
    assert len(ran.screen.events) > 20, "这一轮几乎没发事件，搜的是一个空串"
    assert ran.result.reason is StopReason.ASKED_AUTHOR
    assert set(ran.outcomes) == TOOL_NAMES, "有工具的返回没被采到"
    # **`ok=True` 还不够**：索引层的那两条轴会一边 ok 一边说自己「瞎着」，而瞎着的轴
    # 返回里一个章号都没有——3.2 那次的空转正是这个形状（`events` 是 `None`）。
    assert "瞎着" not in ran.outcomes["character_chapters"], (
        "人物轴瞎着 —— 那条返回里没有内容，在它上面搜毒是空转"
    )
    assert "没接线" not in ran.tool_returns, "有工具这一轮只回了一句「没接线」"


def test_the_tool_returns_this_turn_really_do_carry_the_things_we_search_for(
    world: World,
) -> None:
    """**网的自守卫：毒真的在管子里。**

    这一轮工具查回来的那些字里，`SUMMARY_TELL` / `DRAFT_TELL` / 章正文都必须真的在。
    差一样都说明那条工具其实答的是「没接线」，而下面那条「事件里没有它」就是废话。
    """
    ran = run_the_whole_table(world)
    blob = ran.tool_returns
    missing = [tell for tell in TOOL_RETURN_TELLS if tell not in blob]
    assert not missing, (
        f"这几句没进工具返回：{missing}\n"
        "也就是说对应那条工具这一轮什么都没查到 —— 在它上面搜毒是空转（3.2 那次的形态）。"
    )
    # 约束那条也要真的有内容：`must_not_reveal` 空掉的话，网罩住的是一个空集合。
    assert "血脉秘密" in blob, "这一章的禁说清单是空的 —— 边界一那道网这一轮没有被考到"


# ══════════════════════════════════════════════════════════════════════════
# 二、边界一在事件流上 —— **先有网再有管子**
# ══════════════════════════════════════════════════════════════════════════


def test_not_one_event_this_turn_carried_a_single_word_of_what_the_tools_found(
    world: World,
) -> None:
    """**这一条是本文件存在的理由**（ADR 0024 点名「最贵而且不可回收」的那一条）。

    一轮的返回一直是投影过的（工具查到了什么根本不上屏，只有一个「查了几次」的数），
    而事件流会把中间过程摊开。所以这里把这一轮推给界面的**每一条**拼起来，
    同时搜两种东西：作者写在图上的秘密，和工具查回来的任何一段字。
    """
    ran = run_the_whole_table(world)
    pushed = ran.screen.pushed

    offenders = [f"秘密：{what}" for what, text in SECRET_CONTENT.items() if text in pushed]
    offenders += [f"工具返回：{tell}" for tell in TOOL_RETURN_TELLS if tell in pushed]
    if '"props"' in pushed:
        offenders.append('出现了 "props" 这个键')
    assert not offenders, (
        "事件流把中间过程推到屏幕上了：\n  " + "\n  ".join(offenders) + "\n"
        "这是**不可回收的**：对话是持久化的，推上屏过的那段话改代码删不掉（ADR 0024）。"
    )


def _engine_vocabulary() -> set[str]:
    """**引擎会说的每一个字**，由那几个构造口自己生成 —— 不手抄第二份。

    三个来源，和 `TurnEvent` 类 docstring 那张表逐行对上：`ToolSpec.label`（每条工具
    那半句）、`TurnEvent` 的九个 classmethod（句式）、`stop_wording()`（停法）。
    参数一律喂中性占位，所以它是「句式里的字」而不是「这一轮恰好说了什么」。
    """
    samples = [
        TurnEvent.tool_started("book_index", index=1, total=3),
        TurnEvent.tool_started("book_index"),
        TurnEvent.tool_finished(
            agent_tools.ToolOutcome(
                call_id="x", name="book_index", ok=True, content="", chapter=1
            )
        ),
        TurnEvent.tool_finished(
            agent_tools.ToolOutcome(call_id="x", name="book_index", ok=False, content="")
        ),
        TurnEvent.draft_started(1),
        TurnEvent.draft_kept(1, ordinal=1, units=1),
        TurnEvent.draft_kept(1, ordinal=1, units=1, stopped=True),
        TurnEvent.draft_failed(1),
        TurnEvent.asked_author(AuthorQuestion(question="x")),
        *[TurnEvent.stopped(reason) for reason in StopReason],
    ]
    vocabulary = {ch for event in samples for ch in event.said_to_author}
    vocabulary |= {ch for spec in agent_tools.TOOL_TABLE for ch in spec.label}
    return vocabulary | set(agent_tools.UNNAMED_TOOL_LABEL)


def test_the_engines_half_of_every_event_is_built_from_a_closed_vocabulary(
    world: World,
) -> None:
    """**换一把尺再量一次**：上面那条按「有没有这几个字」判，这一条按「能不能拼得出来」判。

    子串搜毒只抓得住**我们想到要搜的那几句**。这一条反过来问：引擎那半句里的每一个
    汉字，是不是都来自那三个登记过的出处？剩下任何一个都意味着有一段没登记的字
    进了这个面——**而下一次它可能来自图上**，那时子串网还没听说过它。
    """
    ran = run_the_whole_table(world)
    known = _engine_vocabulary()
    strays = {
        str(event.kind): {ch for ch in event.said_to_author if "一" <= ch <= "鿿"} - known
        for event in ran.screen.events
    }
    strays = {kind: left for kind, left in strays.items() if left}
    assert not strays, (
        f"引擎那半句里出现了没登记过的字：{strays}\n"
        "措辞只许来自 `ToolSpec.label` / `TurnEvent` 那几个 classmethod / `stop_wording()`。"
    )


def test_that_vocabulary_check_is_not_vacuous(world: World) -> None:
    """**自守卫**：上面那条的词表要是把整本书都圈进去了，它就永远绿。

    这里往一条事件的那半句里塞一个图上的字，断言它当场被挑出来。
    """
    smuggled = TurnEvent.tool_started("book_index").model_copy(
        update={"said_to_author": f"正在{TWIST}。"}
    )
    left = {ch for ch in smuggled.said_to_author if "一" <= ch <= "鿿"} - _engine_vocabulary()
    assert left, "词表把没登记过的字也认了 —— 上面那条是永远绿的"


def test_the_author_can_still_tell_what_it_was_doing(world: World) -> None:
    """**反向断言：收窄过头是同一个 bug 的另一面。**

    事件流里只剩一串「查完了」而作者看不出它在干什么，这块屏幕就白开了。
    每一条工具的那半句中文、章号、作者自己说的话、模型说的那两段，都必须穿得过去。
    """
    ran = run_the_whole_table(world)
    said = " ".join(event.said_to_author for event in ran.screen.events)
    for spec in agent_tools.TOOL_TABLE:
        assert spec.label in said, f"作者看不出它在「{spec.label}」"
    assert f"第 {CHAPTER} 章" in said, "查的是哪一章也该说得出来"

    spoken = [event.text for event in ran.screen.of(TurnEventKind.REPLY_TEXT)]
    assert spoken and spoken[0] == "我先把这一章的底摸清楚。", (
        "模型在路上说的那句开场没出去 —— 界面上就是几十秒的空白配一个转圈的图标"
    )
    assert all("接着写一稿" in text for text in spoken[1:]), (
        "中途那几句没出去 —— 界面上就是几十秒的空白配一个转圈的图标"
    )
    asked = ran.screen.of(TurnEventKind.ASKED_AUTHOR)
    assert asked and asked[0].asked is not None
    assert asked[0].asked.question == EVERY_TOOL[-1][1]["question"]


# ══════════════════════════════════════════════════════════════════════════
# 三、事件流 vs **上屏的那一份** —— 它会不会把 `_visible` 那道闸绕过去
# ══════════════════════════════════════════════════════════════════════════


def test_the_event_stream_shows_nothing_that_the_http_receipt_would_have_hidden(
    world: World,
) -> None:
    """今天 `api/chat.py::_visible` 把工具返回整条挡在屏幕外，只给一个「查了几次」的数。

    **事件是绕过那道闸的第二条路。** 所以这里拿同一轮的两份东西对：
    `_visible` 挑出来的那几条（今天上屏的全部），和事件流里模型写的每一段字。
    后者必须是前者的**子集**——事件不许比那道闸放行得更多。

    **一条说清楚的例外**：起草那条流的片（`draft_delta`）不在这个子集里——它是作者
    亲口要的那一稿，而且 `GET …/drafts/{id}` 本来就交得出全文，所以它不是一个新开的
    信息面。这一轮里没有它（起草台是注入的假的），所以下面那条量的是别的那几条路。
    """
    ran = run_the_whole_table(world)
    assert not ran.screen.of(TurnEventKind.DRAFT_DELTA), "例外那一档混进来了，判据要重写"
    on_screen_today = {view.text for view in _visible(ran.result.conversation.messages, 0)}
    assert on_screen_today, "这一轮 `_visible` 一条都没挑出来，下面那条是空的"

    for event in ran.screen.events:
        if not event.text:
            continue
        assert any(event.text in text for text in on_screen_today), (
            f"事件流推了一段 `_visible` 不会推的字：{event.text[:40]!r}\n"
            "工具返回今天被那道闸整条挡着，而事件是第二条路 —— 它不许比闸放行得更多。"
        )


def test_a_tool_return_full_of_poison_still_only_shows_up_as_a_number(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这一刀最可能翻车的地方**：一条工具返回里**真的**装着秘密，事件里有没有。

    探针是一条故意泄漏的工具（直接把 `twist` 装进出参，那是最省事的写法）。
    它证明的是「网罩得住的不是一个恰好干净的返回」——返回真的脏，而事件仍然只有
    「成没成 / 第几章」。
    """
    leaky = agent_tools.ToolSpec(
        name="_leaky_probe",
        description="故意把秘密装进返回里。",
        args=agent_tools.SceneConstraintsArgs,
        handler=lambda args, ctx: agent_tools.DraftResult(
            chapter=args.chapter,
            draft_id=DRAFT_ID,
            ordinal=1,
            units=12,
            preview="风雪落在肩上。",
            note=TWIST,
        ),
        label="做一件带毒的事",
    )
    monkeypatch.setitem(agent_tools.TOOLS, leaky.name, leaky)

    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_wired_context(world),
        model=ScriptedModel(
            script=[
                wants((leaky.name, json.dumps({"chapter": CHAPTER}))),
                say("好了"),
            ]
        ),
        ledger=Ledger(),
        on_event=screen,
    )
    poisoned = [
        message.content
        for message in result.conversation.messages
        if message.role is Role.TOOL
    ]
    assert poisoned and TWIST in poisoned[0], "探针自己就不带毒 —— 下面那条是空的"
    assert TWIST not in screen.pushed, "带毒的工具返回顺着事件流上屏了"
    finished = screen.of(TurnEventKind.TOOL_FINISHED)
    assert [event.ok for event in finished] == [True]
    assert finished[0].chapter == CHAPTER


def test_the_maintainers_english_diagnosis_never_reaches_the_event_stream() -> None:
    """`MODEL_UNREACHABLE` 那一支手里有一段**写给维护者**的原文（端点地址 + 模型名）。

    回执上它躺在 `maintainer_note` 里、明写着不许上屏。**事件流是第二个出口**，
    而那一句里有 `base_url` —— 作者读不懂它，而且它会把他支去改一个没坏的设置。
    """
    def dies(messages: Any, *, tools: Any, cancel: Any) -> Any:
        raise ProviderError("HTTP 500 from https://api.example.invalid/v1 (model=deepseek-v4)")

    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_context(),
        model=dies,
        ledger=Ledger(),
        on_event=screen,
    )
    assert result.reason is StopReason.MODEL_UNREACHABLE
    assert "api.example.invalid" in result.maintainer_note, "样本没造出那段诊断"
    assert "api.example.invalid" not in screen.pushed
    assert not dev_shapes(screen.of(TurnEventKind.TURN_STOPPED)[0].said_to_author)


# ══════════════════════════════════════════════════════════════════════════
# 四、不传回调 = 逐字节不变（**自己比，不信报告**）
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Transcript:
    """一轮里**穿过引擎边界**的四样东西。两次跑对拷的就是它。"""

    payloads: list[str]
    receipts: list[str]
    persisted: list[str]
    result: str

    def __eq__(self, other: object) -> bool:  # pragma: no cover - 由下面那条断言驱动
        assert isinstance(other, Transcript)
        return (
            self.payloads == other.payloads
            and self.receipts == other.receipts
            and self.persisted == other.persisted
            and self.result == other.result
        )


def a_transcript(world: World, *, on_event: Any, meddle: bool = False) -> Transcript:
    """跑同一段会话、同一批桩，把穿过边界的四样东西全录下来。

    `meddle=True` 时那块屏**顺手改一下这一轮**（每收到一条事件就动一次剧本）。
    那是「事件流改变了这一轮本身」唯一真实的形态——回调是同步的、跑在 loop 那条线上，
    所以它有能力反过来影响下一次模型调用。**它是这条对拷的自守卫**：
    上面那条断言若换成一个总是相等的比较，这一条会立刻绿给你看。
    """
    receipts: list[str] = []
    persisted: list[str] = []
    model = ScriptedModel(
        script=[
            wants(("book_index", "{}"), ("chapter_text", json.dumps({"chapter": CHAPTER})),
                  text="我先翻一下目录"),
            wants(("book_index", json.dumps({"from_chapter": 3})), text="再看一眼后面"),
            say("好了，这一场我这样写：……", prompt_tokens=1_200, completion_tokens=300),
        ]
    )

    def screen(event: TurnEvent) -> None:
        if on_event is not None:
            on_event(event)
        if meddle:
            # **第二步的回答被改掉 ⇒ 第三步的 payload 跟着变**。挑第二步是因为四条通道
            # 要一起红：只改最后一步的话，payload 那一条不会动（它已经发出去了）。
            model.script[1] = wants(
                ("book_index", json.dumps({"from_chapter": 99})), text="被事件改过的一步"
            )

    result = run_turn(
        start_conversation("冷一点").with_author(f"写第 {CHAPTER} 章"),
        context=a_wired_context(world),
        model=model,
        ledger=lambda receipt: receipts.append(receipt.model_dump_json()),
        persist=lambda conversation: persisted.append(conversation.model_dump_json()),
        on_event=None if on_event is None and not meddle else screen,
    )
    return Transcript(
        payloads=[json.dumps(call, ensure_ascii=False, sort_keys=True) for call in model.calls],
        receipts=receipts,
        persisted=persisted,
        result=result.model_dump_json(),
    )


def test_nobody_listening_leaves_the_turn_byte_for_byte_identical(world: World) -> None:
    """**ADR 0024 那条退路的前提**：适配器扔掉、`on_event` 空着不叫 = 今天的行为。

    「看起来一样」不算。这里逐字节对拷四样穿过引擎边界的东西：发给模型的每一份 payload、
    每一份账单原料、每一次落库的历史、最后那份回执。
    """
    quiet = a_transcript(world, on_event=None)
    watched = a_transcript(world, on_event=Screen())

    assert quiet.payloads == watched.payloads, "有人在听的时候发给模型的东西变了"
    assert quiet.receipts == watched.receipts, "账变了 —— 事件流不许影响钱"
    assert quiet.persisted == watched.persisted, "落库的历史变了 —— 事件流不许进 canonical"
    assert quiet.result == watched.result, "回执变了"
    assert len(quiet.payloads) == 3 and len(quiet.receipts) == 3 and quiet.persisted, (
        "剧本没跑起来，这条对拷是空的"
    )


def test_that_comparison_would_go_red_the_moment_the_event_path_touched_anything(
    world: World,
) -> None:
    """**上一条的自守卫。** 一个永远相等的比较是这个仓库最贵的那种错误。

    这里让发事件那条路顺手改一下这一轮（同步回调有这个能力），断言四条通道**全部**
    当场红——少红一条就说明那一条通道是摆设。
    """
    quiet = a_transcript(world, on_event=None)
    meddled = a_transcript(world, on_event=Screen(), meddle=True)

    assert quiet.payloads != meddled.payloads, "payload 那条通道是摆设"
    assert quiet.receipts != meddled.receipts, "账那条通道是摆设"
    assert quiet.persisted != meddled.persisted, "落库那条通道是摆设"
    assert quiet.result != meddled.result, "回执那条通道是摆设"


# ══════════════════════════════════════════════════════════════════════════
# 五、问作者
# ══════════════════════════════════════════════════════════════════════════

ASKED = "这一场你想让萧决知道那件事吗？"
OPTIONS = ("让他知道", "先瞒着他")


def _asks(question: str, *options: str, then: tuple[str, dict[str, Any]] | None = None) -> Any:
    calls = [("ask_author", json.dumps({"question": question, "options": list(options)}))]
    if then is not None:
        calls.append((then[0], json.dumps(then[1])))
    return wants(*calls, text="有件事我拿不准。")


def test_the_engine_adds_nothing_to_the_question_and_keeps_its_note_off_the_screen(
    world: World,
) -> None:
    """两条边界，一条比一条容易漏。

    ① **引擎一个字都不加**：事件上那个问句和模型打进来的那一份逐字段相同。
    ② **给模型的那句话不上屏**：`ASK_ACKNOWLEDGED` 是说给模型听的
    （「这一轮到此为止，别替他先假定一个答案」），它进对话历史、**不进事件**。
       漏掉它的形态不是崩，是作者的屏幕上多出一句在对他说话的、其实不是对他说的话。
    """
    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_wired_context(world),
        model=ScriptedModel(script=[_asks(ASKED, *OPTIONS), say("到不了")]),
        ledger=Ledger(),
        on_event=screen,
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    assert result.asked == AuthorQuestion(question=ASKED, options=OPTIONS)

    asked = screen.of(TurnEventKind.ASKED_AUTHOR)
    assert len(asked) == 1 and asked[0].asked == AuthorQuestion(question=ASKED, options=OPTIONS)
    assert agent_tools.ASK_ACKNOWLEDGED in "".join(
        message.content for message in result.conversation.messages if message.role is Role.TOOL
    ), "那句话根本没进对话 —— 下面那条是空的"
    assert agent_tools.ASK_ACKNOWLEDGED not in screen.pushed, (
        "说给模型听的那句话上屏了"
    )


def test_asking_does_not_pick_up_anything_from_the_book(world: World) -> None:
    """问句这条路上引擎能保证的只有一条：**它自己没有数据来源。**

    「这句话有没有说破秘密」要回答「这句话是什么意思」，那是语义判断（ADR 0005 禁引擎做）。
    所以这里量的是**别的**：同一段会话前面刚查过带毒的那几条工具，问句这一声里
    一个字都没有从那儿带过来。
    """
    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_wired_context(world),
        model=ScriptedModel(
            script=[
                wants(
                    ("scene_constraints", json.dumps({"chapter": CHAPTER})),
                    ("chapter_text", json.dumps({"chapter": CHAPTER})),
                ),
                _asks(ASKED, *OPTIONS),
                say("到不了"),
            ]
        ),
        ledger=Ledger(),
        on_event=screen,
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    event = screen.of(TurnEventKind.ASKED_AUTHOR)[0]
    blob = event.model_dump_json()
    assert not [text for text in SECRET_CONTENT.values() if text in blob]
    assert KNOWS_QUOTE not in blob, "刚查回来的那一章正文进了问句这一声"
    assert not dev_shapes(event.said_to_author)


@pytest.mark.parametrize(
    "tail",
    [
        ("draft_chapter", {"chapter": CHAPTER, "goal": "照我猜的写"}),
        ("read_draft", {"draft_id": DRAFT_ID}),
        ("chapter_text", {"chapter": CHAPTER}),
    ],
)
def test_whatever_it_lined_up_behind_the_question_never_runs(
    world: World, tail: tuple[str, dict[str, Any]]
) -> None:
    """**ADR 0024 说「问 = 这一轮说完了」，这里去验它是不是真的。**

    三条尾巴各是一种代价：花钱的（起草）、动书的（落盘）、只是白跑的（读正文）。
    一条都不许跑掉——作者同时收到一个问题和一份照猜写出来的稿子，正是这条工具
    要去掉的东西。
    """
    screen = Screen()
    context = a_wired_context(world)
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=context,
        model=ScriptedModel(script=[_asks(ASKED, *OPTIONS, then=tail), say("到不了")]),
        ledger=Ledger(),
        on_event=screen,
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    assert result.tool_calls == 1, f"「{tail[0]}」在问句后面还是跑掉了"
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_FINISHED)] == ["ask_author"]
    assert context.drafter.seen == [], "起草侧被叫过 —— 钱已经花掉了"  # type: ignore[union-attr]
    # **没跑的那几条一声都不许喊**：喊了「正在查」却永远没有「查完了」，
    # 界面上就是一个永远转下去的图标。
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_STARTED)] == ["ask_author"]


def test_a_question_wins_even_when_the_batch_was_allowed_to_run_at_once(
    world: World,
) -> None:
    """**并发放开着的时候它照样收得住。**

    `draft_chapter` 是表里唯一 `concurrent=True` 的一条，而闸是按**批的位置**收的
    （`settle(position + 1)`），不是按工具名。这里放开 `parallel_tools`，把问句夹在
    两条起草中间：前面那一条已经花过钱（如实收走），**问句后面那两条一步都不许动**。

    量的是 `settle()` 那条路：已经跑掉的如实记账收走，没跑的配壳——
    少配一个壳，wire 上就少一条 `tool` 消息，那是一次 400。
    """
    screen = Screen()
    context = a_wired_context(world)
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=context,
        model=ScriptedModel(
            script=[
                wants(
                    (
                        "draft_chapter",
                        json.dumps({"chapter": CHAPTER, "brief": "写一场雪"}),
                    ),
                    ("ask_author", json.dumps({"question": ASKED, "options": list(OPTIONS)})),
                    (
                        "draft_chapter",
                        json.dumps({"chapter": CHAPTER, "brief": "写一场雪"}),
                    ),
                    ("read_draft", json.dumps({"draft_id": DRAFT_ID})),
                ),
                say("到不了"),
            ]
        ),
        ledger=Ledger(),
        on_event=screen,
        limits=TurnLimits(max_steps=3, parallel_tools=3),
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_FINISHED)] == [
        "draft_chapter",
        "ask_author",
    ], "问句后面那几条跑掉了"
    assert len(context.drafter.seen) == 1  # type: ignore[union-attr]
    unrun = [
        message
        for message in result.conversation.messages
        if message.role is Role.TOOL and "这一轮已经停下来了" in message.content
    ]
    assert len(unrun) == 2, "没跑的那两条没有配齐壳 —— wire 上少一条 tool 消息就是 400"


def test_a_question_that_came_back_from_a_crash_still_stops_what_was_behind_it(
    world: World,
) -> None:
    """进程死在「模型要求问一句」和「派发」之间，那个问题会以 `pending` 的身份回来。

    **补跑那条路是另一份代码**（`run_tool`，不是批派发那一支），所以它要单独量：
    问句后面那条起草在补跑里照样一步都不许动。少了这一支，一次崩溃就成了绕过
    这条工具的办法——而 resume 是 ADR 0019 敢不上图编排的全部理由，它不该有第二套规矩。
    """
    half_done: Conversation = (
        start_conversation()
        .with_author("写第 7 章")
        .extended(
            AgentMessage(
                role=Role.ASSISTANT,
                content="有件事我拿不准。",
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="ask_author",
                        arguments=json.dumps({"question": ASKED, "options": list(OPTIONS)}),
                    ),
                    ToolCall(
                        id="c2",
                        name="draft_chapter",
                        arguments=json.dumps({"chapter": CHAPTER, "goal": "照我猜的写"}),
                    ),
                ),
            )
        )
    )
    assert len(half_done.pending_calls) == 2, "样本没造出「缺两个结果」的局面"

    screen = Screen()
    context = a_wired_context(world)
    model = ScriptedModel(script=[say("这一句到不了")])
    result = run_turn(
        half_done, context=context, model=model, ledger=Ledger(), on_event=screen
    )
    assert result.reason is StopReason.ASKED_AUTHOR
    assert model.calls == [], "补跑出来的一次提问之后不该再叫模型"
    assert context.drafter.seen == [], "补跑把问句后面那一稿写掉了"  # type: ignore[union-attr]
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_STARTED)] == ["ask_author"]


def test_a_turn_the_author_stopped_says_only_what_really_happened(world: World) -> None:
    """按停之后事件流仍然只许陈述**真的发生过**的事。

    第一条工具跑完之后按停：那一条要如实喊「查完了」（它真的查了），
    第二条**一声都不许喊**（喊了「正在查」却永远没有下文 = 又一个转圈的图标）。
    """
    signal = Cancellation()
    screen = Screen()
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_wired_context(world),
        model=ScriptedModel(
            script=[
                wants(
                    ("book_index", "{}"),
                    ("chapter_text", json.dumps({"chapter": CHAPTER})),
                ),
                say("到不了"),
            ],
            on_call=lambda _cancel: None,
        ),
        ledger=Ledger(),
        cancel=signal,
        on_event=lambda event: (
            screen(event),
            signal.stop() if event.kind is TurnEventKind.TOOL_FINISHED else None,
        )[0],
    )
    assert result.reason is StopReason.AUTHOR_STOPPED
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_STARTED)] == ["book_index"]
    assert [event.tool for event in screen.of(TurnEventKind.TOOL_FINISHED)] == ["book_index"]
    assert screen.of(TurnEventKind.TURN_STOPPED)[0].reason is StopReason.AUTHOR_STOPPED


def test_the_next_turn_needs_no_resume_machinery(world: World) -> None:
    """**「不需要 interrupt/resume」是可验证的**（ADR 0024）。

    停在一个问题上的会话，执行态和别的停法一模一样：一串 message，一个缺的
    `tool_result` 都没有。作者答一句就是 `with_author(...)`，下一轮照跑。
    """
    context = a_wired_context(world)
    stopped = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=context,
        model=ScriptedModel(script=[_asks(ASKED, *OPTIONS), say("到不了")]),
        ledger=Ledger(),
    )
    assert stopped.conversation.pending_calls == ()

    model = ScriptedModel(script=[say("好，那我这样写")])
    again = run_turn(
        stopped.conversation.with_author("先瞒着他"),
        context=context,
        model=model,
        ledger=Ledger(),
    )
    assert again.reason is StopReason.DONE
    assert model.calls[0][-1] == {"role": "user", "content": "先瞒着他"}


# ══════════════════════════════════════════════════════════════════════════
# 六、界面炸了不许拿走这一轮（作者已经付过钱了）
# ══════════════════════════════════════════════════════════════════════════


def test_building_an_event_can_never_be_what_kills_a_turn() -> None:
    """**安全网罩的是「发」，不是「造」。**

    `emit(TurnEvent.tool_started(...))` 里那个事件是在 `emit` 之前**先造出来**的，
    所以 `safe_emitter` 的 try/except 罩不到它；而它还**在没人听的时候照样被造**
    （`_no_events` 收得到一个已经构造好的对象）。也就是说：一个构造得出 `ValidationError`
    的构造口，会把一轮**根本没有界面在听**的对话弄崩。

    今天构造不出来（`said_to_author` 一律有话，认不出的名字走通用说法），这一条把它钉住：
    拿最难看的入参喂每一个构造口，一个都不许抛，而且喊出来的仍然是给作者听的中文。
    """
    nasty = ["", "   ", "\n", "没有这个工具", TWIST, "a" * 500]
    built: list[TurnEvent] = []
    for name in nasty:
        outcome = agent_tools.ToolOutcome(call_id="c", name=name, ok=False, content="")
        built += [TurnEvent.tool_started(name), TurnEvent.tool_finished(outcome)]
        assert TurnEvent.tool_finished(outcome).tool in TOOL_NAMES | {""}
    for chapter in (1, 999_999):
        built += [
            TurnEvent.draft_started(chapter),
            TurnEvent.draft_failed(chapter, stream=3),
            TurnEvent.draft_kept(chapter, ordinal=0, units=0),
        ]
    built += [TurnEvent.stopped(reason) for reason in StopReason]
    for event in built:
        assert event.said_to_author, f"{event.kind} 这一声没有说给作者的话"
        assert not dev_shapes(event.said_to_author), f"{event.kind} 那半句里有研发术语"
    assert TWIST not in "".join(event.said_to_author for event in built)


@pytest.mark.parametrize("dies_at", [0, 1, 3])
def test_a_screen_that_dies_never_takes_the_turn_with_it(world: World, dies_at: int) -> None:
    """三个时刻各炸一次：第一声、跑到一半、批的中间。

    吞在错的层里的话，前半截事件已经发出去了，而作者看到的是一轮跑到一半突然消失。
    **账和落库不受影响**：那两样坏掉要响（钱花了没记上），事件坏掉要吞。
    """
    dead = Screen(explode_from=dies_at)
    receipts: list[Any] = []
    persisted: list[Any] = []
    result = run_turn(
        start_conversation().with_author("写第 7 章"),
        context=a_wired_context(world),
        model=ScriptedModel(
            script=[
                wants(("book_index", "{}"), ("chapter_text", json.dumps({"chapter": CHAPTER}))),
                say("好了"),
            ]
        ),
        ledger=receipts.append,
        persist=persisted.append,
        on_event=dead,
    )
    assert result.reason is StopReason.DONE
    assert result.reply == "好了"
    assert len(receipts) == 2 and persisted, "账或者历史被一块掉线的屏带走了"
    assert len(dead.events) > dies_at, "样本没炸 —— 这条测试是空的"


def test_a_dead_screen_does_not_take_down_the_drafting_desk(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """起草台是**另一条**发事件的路（它有自己的 `safe_emitter`）。

    一个 try/except 漏在这儿，症状是作者的一稿因为浏览器关掉了而没写成——
    而那一稿的钱已经花了。
    """
    conn = connect(book["db"])
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    dead = Screen(explode_from=0)
    desk = chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=book["pid"],
        root=_root(conn, book["pid"]),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        on_event=dead,
    )
    product = _write(desk, conn, book["pid"], 1)
    assert product.candidate.ordinal == 1, "屏幕掉线把这一稿弄没了"
    assert len(dead.events) >= 1
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 七、一条流开了就得有人给它收尾
# ══════════════════════════════════════════════════════════════════════════


_ENDINGS = (TurnEventKind.DRAFT_KEPT, TurnEventKind.DRAFT_FAILED)


def _a_desk(conn: Connection, pid: str, screen: Screen) -> Any:
    return chapter_drafter(
        store=SqliteStoryGraph(conn),
        conn=conn,
        project_id=pid,
        root=_root(conn, pid),
        config=ProviderConfig(base_url=ENDPOINT, model=MODEL),
        capability=resolve_capabilities(ENDPOINT, MODEL),
        events=SqliteEventStore(conn),
        summaries=_NoSummaries(),
        on_event=screen,
    )


def _blows_up(exc: Exception) -> Any:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return boom


@pytest.mark.parametrize(
    ("how", "boom"),
    [
        ("联系不上模型", _blows_up(ProviderError("端点 503"))),
        ("发出去之前就被拒", _blows_up(DraftRefused("装不下"))),
        # 写手把一份空的交回来 —— 真会发生的一档（`ChapterDesk.write` 自己写着它）。
        ("写手交回来是空的", FakeDrafting(text="   ")),
    ],
)
def test_a_draft_stream_that_dies_still_gets_an_ending(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch, how: str, boom: Any
) -> None:
    """**一条开了口的流必须说得出自己怎么结束的。**

    「正在写第 N 章的一稿」是在真的去调模型**之前**喊的（得先拿到流号）。那次调用
    失败的路不止一条，而它们全都 `raise ToolRefused` ——少一声结局，界面上按
    `stream` 分的那一格就是一个**永远转下去的图标**，也就是 ADR 0024 要治的那个东西。

    **上层那条 `tool_finished(ok=False)` 补不上**：它身上 `stream` 是 0，
    一批三稿同框（ADR 0022）时它说不出死的是哪一条。

    三种失败各跑一遍，因为这一条要钉的不是某一个 `except`，是**出口的完备性**。
    """
    conn = connect(book["db"])
    monkeypatch.setattr(drafting, "draft_chapter", boom)
    screen = Screen()
    with pytest.raises(agent_tools.ToolRefused):
        _write(_a_desk(conn, book["pid"], screen), conn, book["pid"], 1)

    opened = {event.stream for event in screen.of(TurnEventKind.DRAFT_STARTED)}
    assert opened, f"样本（{how}）没造出「流开了」这件事"
    closed = {event.stream for event in screen.events if event.kind in _ENDINGS}
    assert opened <= closed, (
        f"（{how}）这几条流开了就再也没有下文：{sorted(opened - closed)}\n"
        "界面按 `stream` 分组，所以那一格是一个永远转下去的图标 —— "
        "而 `tool_finished` 补不上（它身上 `stream` 是 0，三稿同框时说不出死的是哪一条）。"
    )
    ending = [event for event in screen.events if event.kind in _ENDINGS][0]
    assert ending.kind is TurnEventKind.DRAFT_FAILED
    assert not dev_shapes(ending.said_to_author)
    assert "503" not in ending.said_to_author and "端点" not in ending.said_to_author, (
        "失败的原因原文上屏了 —— 那里面有端点地址，是写给维护者的"
    )
    conn.close()


def test_a_draft_that_made_it_says_so_exactly_once(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**自守卫**：上面那条要是靠「每条流都补一声没写成」来满足，它也会绿。

    写成了的那一档只许有 `draft_kept` 一个结局——多喊一声「没写成」，作者会以为
    那一稿丢了，而它其实好端端躺在候选表里。
    """
    conn = connect(book["db"])
    monkeypatch.setattr(drafting, "draft_chapter", FakeDrafting())
    screen = Screen()
    desk = _a_desk(conn, book["pid"], screen)
    _write(desk, conn, book["pid"], 1)
    _write(desk, conn, book["pid"], 1)

    endings = [event.kind for event in screen.events if event.kind in _ENDINGS]
    assert endings == [TurnEventKind.DRAFT_KEPT] * 2, f"写成了的那两稿收错了尾：{endings}"
    assert len({event.stream for event in screen.of(TurnEventKind.DRAFT_STARTED)}) == 2
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 八、数字只许有一份，而它是数出来的
# ══════════════════════════════════════════════════════════════════════════


_CHINESE_NUMERALS = "零一二三四五六七八九十"


def _chinese(number: int) -> str:
    """把 11 以内的数写成中文（这份文件只需要这一档）。"""
    if number <= 10:
        return _CHINESE_NUMERALS[number]
    return "十" + _CHINESE_NUMERALS[number - 10]


def test_the_prose_count_of_stop_reasons_is_the_one_the_code_has() -> None:
    """**「N 种停法」是一句会漂的断言，而它现在躺在七个文件里。**

    这个仓库刚把「620 个 pytest 在三处、27 条路由在七处」清掉，判据是
    `tests/test_doc_numbers.py`——但它只扫 markdown 里那几个数，扫不到源码 docstring
    里的这一句。而这一句 2026-08-12 之前就已经漂了（写着「九种」而 `StopReason`
    有十个），加上 `asked_author` 之后是十一个。

    **拷贝不是在写下的那一刻骗人，是在下一次改代码的时候。**
    """
    real = _chinese(len(list(StopReason)))
    scanned = [
        path
        for folder in ("src", "tests")
        for path in (REPO_ROOT / folder).rglob("*.py")
    ] + [REPO_ROOT / "docs" / "ARCHITECTURE.md"]
    stale: list[str] = []
    for path in scanned:
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # **两种不是总数的写法要放过去，否则这道守卫会误报，而误报的守卫会被关掉。**
            # ①「同一种 / 每一种 / 一种停法叫 `NO_OUTPUT`」说的是**一条**，不是有几条；
            # ② 以 `#` 开头的分节标题说的是**这一节测了几条**（「五种停法：造一个真的
            #    会触发它的局面」），那本来就该是一个更小的数。
            if text.lstrip().startswith("#"):
                continue
            for said in re.findall(r"([零一二三四五六七八九十]+)种停法", text):
                if said != "一" and said != real:
                    stale.append(f"{path.relative_to(REPO_ROOT)}:{line} 说「{said}种」")
    assert not stale, (
        f"`StopReason` 有 {len(list(StopReason))} 个，这几处说的是别的数：\n  "
        + "\n  ".join(stale)
        + "\n改的是那句话不是代码 —— 那个数是数出来的。"
    )


def test_that_count_guard_can_see_a_stale_number() -> None:
    """**守卫的自守卫。** 正则认不出作者的写法时，上面那条会静默变成空转。

    探针那个错值是**拼出来的**，不是写死在这一行的：写死的话上面那条会扫到它自己，
    于是这份文件永远红——同 `test_doc_numbers.py` 里那些 probe 不许写死数字的理由。
    """
    stale = "九" + "种停法"
    assert re.findall(r"([零一二三四五六七八九十]+)种停法", f"停得下来（{stale}）") == ["九"], (
        "正则认不出「N 种停法」这个写法 —— 上面那条永远绿"
    )
    assert _chinese(len(list(StopReason))) != "九", "真值回到九了，换一个错值"
    # 放过去的那两种也要真的被放过去，否则上面那条会误报，而误报的守卫会被关掉。
    assert re.findall(r"([零一二三四五六七八九十]+)种停法", "同一种停法") == ["一"]
