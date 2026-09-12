"""右栏那四栏的读工具（`agent/panels.py`，2026-09-12）—— **读得到、说得清、不漏。**

三件事各一节：

1. **读得到**：右栏每一格上作者看得见的内容，写作助手从同一条读法拿到同样的东西
   （角色卡的基本信息 / 别名 / 处境 / 关系 / 经历过的事；这一章已确认的事件；
   规则目录 + 最近一次检验；通知 + 待确认的提案）。
2. **说得清**：`index.py` 那三条纪律——裁了要说、空和瞎分开、未来只标不挡。
3. **不漏**：`Node.props` 里作者写的自由文本（`twist` / `plot_note`）一个字都不出，
   `"props"` 这个键不出——这是 ADR 0019 边界一在这四条上的落点。**事件摘要和别名从
   这四条起是有意交出去的**（`panels.py` 顶上写着为什么），所以这里反向断言它们在。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from novel_harness import project
from novel_harness.agent import ToolContext, dispatch
from novel_harness.agent.panels import (
    CharacterCard,
    ChapterEvents,
    Notifications,
    ValidationRules,
)
from novel_harness.checks.service import RulesReader
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.draft.length import DraftLanguage
from novel_harness.draft.provider import ToolCall
from novel_harness.events import CharacterProfilePatch, ProposalCreate, ProvisionalEventSpec
from novel_harness.graph import (
    AliasKind,
    AliasSpec,
    ChapterSpec,
    EdgeProps,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import CHAPTER_DIR, chapter_path
from novel_harness.notices import NoticeReader
from novel_harness.system_notifications import (
    enqueue_notification,
    materialize_notification_outbox,
)

TWIST = "萧决其实是魔尊之子第200章揭晓"
PLOT_NOTE = "萧决在幽泉窟被顾清音所杀"
WORKING_CHAPTER = 2
CHAPTERS = {
    1: ("第一章 少年", "萧决独自走进了北荒的风雪里。"),
    2: ("第二章 藏书阁", "顾清音在藏书阁遇见萧决。"),
    3: ("第三章 渡口", "萧决与顾清音在渡口分别。"),
}


@dataclass
class Book:
    conn: Connection
    project_id: str
    store: SqliteStoryGraph
    events: SqliteEventStore
    root: Path
    xiao: str
    gu: str
    event_ids: dict[int, str]

    def context(self, **overrides: Any) -> ToolContext:
        base: dict[str, Any] = {
            "store": self.store,
            "project_id": self.project_id,
            "root_path": str(self.root),
            "events": self.events,
            "rules": RulesReader(self.conn),
            "notices": NoticeReader(self.conn, self.store),
            "working_chapter": WORKING_CHAPTER,
        }
        base.update(overrides)
        return ToolContext(**base)


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


def _ok(name: str, context: ToolContext, **arguments: Any) -> str:
    outcome = dispatch(_call(name, **arguments), context)
    assert outcome.ok, outcome.content
    return outcome.content


def _refused(name: str, context: ToolContext, **arguments: Any) -> str:
    outcome = dispatch(_call(name, **arguments), context)
    assert not outcome.ok, outcome.content
    return outcome.content


@pytest.fixture
def book(tmp_path: Path) -> Iterator[Book]:
    """三章正文、两个人物、一段关系、一个带毒的地点、两件事（一件已确认、一件没有）。"""
    root = tmp_path / "book"
    (root / CHAPTER_DIR).mkdir(parents=True)
    conn = connect(IN_MEMORY)
    migrate(conn)
    pid = project.create(conn, name="青云记-panels", root_path=str(root)).id
    store = SqliteStoryGraph(conn)

    xiao = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="萧决",
            # 毒挂在**人物自己的 props** 上：角色卡读的就是这个节点，收窄漏一格就是这儿漏。
            props=NodeProps.model_validate({"twist": TWIST}),
        )
    )
    gu = store.upsert_node(NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name="顾清音"))
    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.LOCATION,
            name="幽泉窟",
            props=NodeProps.model_validate({"first_appears_chapter": 200, "plot_note": PLOT_NOTE}),
        )
    )
    store.add_alias(
        AliasSpec(project_id=pid, node_id=xiao.id, surface="师兄", kind=AliasKind.NICKNAME)
    )
    events = SqliteEventStore(conn)
    events.update_profile(
        pid,
        xiao.id,
        CharacterProfilePatch(
            gender="男", personality="沉默", background="北荒来的少年", character_notes="第 200 章翻身"
        ),
    )

    evidence: dict[int, str] = {}
    for number, (heading, body) in CHAPTERS.items():
        text = f"{heading}\n\n{body}\n"
        (root / chapter_path(number)).write_text(text, encoding="utf-8")
        chapter = store.put_chapter(
            ChapterSpec(
                project_id=pid, number=number, heading=heading, path=chapter_path(number), text=text
            )
        )
        evidence[number] = store.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=chapter.snapshot_id,
                para_index=2,  # 标题 / 空行 / 正文（`text.anchor.paragraphs` 按行切）
                quote_text=body,
            )
        ).id

    store.upsert_edge(
        EdgeSpec(
            project_id=pid,
            src=xiao.id,
            dst=gu.id,
            type=EdgeType.RELATED_TO,
            valid_from_chapter=2,
            information_scope=InformationScope.CANON,
            props=EdgeProps(value="师兄妹"),
        )
    )

    # 第 2 章一件已确认的事，第 3 章一件**还没确认**的事（PROVISIONAL）。
    event_ids: dict[int, str] = {}
    confirmed = events.put_provisional(
        ProvisionalEventSpec(
            project_id=pid,
            summary="顾清音在藏书阁遇见萧决。",
            evidence_id=evidence[2],
            participant_ids=[xiao.id, gu.id],
            knower_ids=[xiao.id, gu.id],
            confidence=0.9,
        )
    )
    events.clone_to_scope(confirmed.event.id, InformationScope.CANON)
    event_ids[2] = confirmed.event.id
    pending = events.put_provisional(
        ProvisionalEventSpec(
            project_id=pid,
            summary="萧决与顾清音在渡口分别。",
            evidence_id=evidence[3],
            participant_ids=[xiao.id, gu.id],
            knower_ids=[xiao.id],
            confidence=0.4,
        )
    )
    event_ids[3] = pending.event.id
    conn.commit()
    yield Book(
        conn=conn,
        project_id=pid,
        store=store,
        events=events,
        root=root,
        xiao=xiao.id,
        gu=gu.id,
        event_ids=event_ids,
    )
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 一、角色卡
# ══════════════════════════════════════════════════════════════════════════


def test_the_card_carries_what_the_roster_panel_shows(book: Book) -> None:
    card = CharacterCard.model_validate_json(
        _ok("character_card", book.context(), character="萧决", chapter=2)
    )
    assert card.character.name == "萧决"
    # 基本信息：和 `GET …/characters/{id}/profile` 是同一份收窄视图。
    assert (card.profile.gender, card.profile.personality) == ("男", "沉默")
    assert card.profile.background == "北荒来的少年"
    assert card.profile.character_notes == "第 200 章翻身"
    # 别名：正式名不在里面（它在 `character.name`），绰号在。
    assert card.aliases == ["师兄"]
    # 关系：对端已经是人名，值是作者写的那两个字。
    assert [(r.peer.name, r.value, r.since_chapter) for r in card.relations] == [
        ("顾清音", "师兄妹", 2)
    ]
    # 经历过的事：**只有已确认的**（第 3 章那件还没确认）。
    assert [(e.chapter, e.summary) for e in card.events] == [(2, "顾清音在藏书阁遇见萧决。")]
    assert card.events[0].participants == ["萧决", "顾清音"]
    assert card.events_total == 1 and card.events_omitted == 0 and not card.events_blind


def test_the_card_is_seen_from_the_other_side_too(book: Book) -> None:
    """无向边从哪一侧看，对端都是**另一个人**（`StateSnapshot.relations` 替消费侧算好）。"""
    card = CharacterCard.model_validate_json(
        _ok("character_card", book.context(), character="顾清音", chapter=2)
    )
    assert [(r.peer.name, r.value) for r in card.relations] == [("萧决", "师兄妹")]


def test_the_card_never_carries_props(book: Book) -> None:
    """**边界一**：人物 props 上的 `twist`、未来地点 props 上的 `plot_note` 一个字都不出。"""
    for name in ("萧决", "顾清音"):
        blob = _ok("character_card", book.context(), character=name, chapter=3)
        assert TWIST not in blob
        assert PLOT_NOTE not in blob
        assert '"props"' not in blob


def test_the_card_refuses_what_is_not_a_character(book: Book) -> None:
    said = _refused("character_card", book.context(), character="幽泉窟", chapter=2)
    assert "不是人物" in said and "幽泉窟" in said
    unknown = _refused("character_card", book.context(), character="不存在的人", chapter=2)
    assert "角色册" in unknown


def test_the_card_marks_the_future_and_says_when_it_left_events_out(book: Book) -> None:
    """第 3 章那件事一旦确认，它就在**作者还没写到的地方**——只标不挡。"""
    book.events.clone_to_scope(book.event_ids[3], InformationScope.CANON)
    book.conn.commit()
    card = CharacterCard.model_validate_json(
        _ok("character_card", book.context(working_chapter=2), character="萧决", chapter=2)
    )
    assert [(e.chapter, e.future) for e in card.events] == [(2, False), (3, True)]
    assert card.future_from_chapter == 3
    assert any("还没写到" in note for note in card.notes)

    # 预算小到装不下两件事：留最近的那件，并且说丢了几件。
    tight = book.context(max_context_tokens=1, reserved_output_tokens=0)
    card = CharacterCard.model_validate_json(
        _ok("character_card", tight, character="萧决", chapter=2)
    )
    assert card.events_total == 2
    assert card.events_omitted == 1
    assert [e.chapter for e in card.events] == [3], "留最近的那一端"
    assert any("没给" in note for note in card.notes)


def test_the_card_says_when_the_events_panel_is_not_wired(book: Book) -> None:
    card = CharacterCard.model_validate_json(
        _ok("character_card", book.context(events=None), character="萧决", chapter=2)
    )
    assert card.events_blind is True and card.events == []
    assert any("没接" in note for note in card.notes)
    # 基本信息走的也是那个端口，没接线时不编：空就是空。
    assert card.profile.gender is None


# ══════════════════════════════════════════════════════════════════════════
# 二、事件
# ══════════════════════════════════════════════════════════════════════════


def test_chapter_events_lists_only_confirmed_events_by_chapter(book: Book) -> None:
    result = ChapterEvents.model_validate_json(
        _ok("chapter_events", book.context(), first_chapter=1, last_chapter=3)
    )
    # 第 2 章那件已确认；第 3 章那件还没确认，不在。
    assert [(e.chapter, e.summary, e.future) for e in result.events] == [
        (2, "顾清音在藏书阁遇见萧决。", False)
    ]
    assert result.events[0].participants == ["萧决", "顾清音"]
    assert result.events[0].knowers == ["萧决", "顾清音"]
    assert result.chapters_with_events == [2]
    assert result.notes == []

    # 只看一章：两个界都填它。
    only_two = ChapterEvents.model_validate_json(
        _ok("chapter_events", book.context(), first_chapter=2, last_chapter=2)
    )
    assert [e.chapter for e in only_two.events] == [2]

    # 一件都没有的区间：清单是空的，**而 notes 说清了这不等于「没事发生」**。
    empty = ChapterEvents.model_validate_json(
        _ok("chapter_events", book.context(), first_chapter=3, last_chapter=3)
    )
    assert empty.events == [] and empty.events_total == 0
    assert any("没整理过" in note for note in empty.notes)


def test_chapter_events_marks_the_future_and_keeps_the_latest_when_tight(book: Book) -> None:
    book.events.clone_to_scope(book.event_ids[3], InformationScope.CANON)
    book.conn.commit()
    result = ChapterEvents.model_validate_json(
        _ok("chapter_events", book.context(working_chapter=2), first_chapter=1, last_chapter=3)
    )
    assert [(e.chapter, e.future) for e in result.events] == [(2, False), (3, True)]
    assert any("还没写到" in note for note in result.notes)

    tight = book.context(max_context_tokens=1, reserved_output_tokens=0)
    result = ChapterEvents.model_validate_json(
        _ok("chapter_events", tight, first_chapter=1, last_chapter=3)
    )
    assert result.events_total == 2 and result.events_omitted == 1
    assert [e.chapter for e in result.events] == [3], "留最后那些章的"
    assert result.chapters_with_events == [2, 3], "哪几章有事这张表永不被裁"


def test_chapter_events_refuses_instead_of_pretending_when_not_wired(book: Book) -> None:
    said = _refused("chapter_events", book.context(events=None), first_chapter=2, last_chapter=2)
    assert "没接" in said


# ══════════════════════════════════════════════════════════════════════════
# 三、检验规则
# ══════════════════════════════════════════════════════════════════════════


def _add_rule(book: Book, rule_id: str, literal: str, *, enabled: bool = True) -> None:
    book.conn.execute(
        "INSERT INTO validation_rule (id, project_id, title, template, enabled, config_json) "
        "VALUES (?, ?, ?, 'forbidden_literal', ?, ?)",
        (rule_id, book.project_id, literal, int(enabled), json.dumps({"literal": literal})),
    )
    book.conn.commit()


def test_validation_rules_lists_the_catalog_including_disabled_ones(book: Book) -> None:
    _add_rule(book, "rule:1", "突然")
    _add_rule(book, "rule:2", "忽然", enabled=False)
    result = ValidationRules.model_validate_json(_ok("validation_rules", book.context()))
    assert [(r.title, r.literal, r.enabled, r.built_in) for r in result.rules] == [
        ("突然", "突然", True, False),
        ("忽然", "忽然", False, False),
    ]
    assert result.last_check is None
    assert result.notes == []


def test_validation_rules_says_when_there_are_none_and_when_a_chapter_was_never_checked(
    book: Book,
) -> None:
    result = ValidationRules.model_validate_json(
        _ok("validation_rules", book.context(), chapter=2)
    )
    assert result.rules == []
    assert any("还没" in note and "规则" in note for note in result.notes)
    assert any("第 2 章还没有检验过" in note for note in result.notes)


def test_validation_rules_reports_the_latest_check_and_whether_it_is_current(book: Book) -> None:
    from novel_harness.checks.base import Issue
    from novel_harness.graph import TextAnchor
    from novel_harness.importer import text_digest

    text = (book.root / chapter_path(2)).read_text(encoding="utf-8")
    issue = Issue(
        rule="rule:突然",
        issue_type="FORBIDDEN_LITERAL",
        chapter=2,
        anchor=TextAnchor(para_index=2, quote_text="顾清音在藏书阁遇见萧决。", occurrence_k=0),
        message="出现了「突然」。",
    )
    book.conn.execute(
        "INSERT INTO validation_report (id, project_id, chapter_number, issue_count, issues_json, "
        "gate, text_sha256, created_at) VALUES (?, ?, 2, 1, ?, 'blocked', ?, ?)",
        ("report:1", book.project_id, json.dumps([issue.model_dump(mode="json")]),
         text_digest(text), "2026-09-12T00:00:00.000Z"),
    )
    book.conn.commit()

    result = ValidationRules.model_validate_json(
        _ok("validation_rules", book.context(), chapter=2)
    )
    assert result.last_check is not None
    assert result.last_check.passed is False
    assert result.last_check.current is True, "验的就是磁盘上此刻这一版"
    assert [(i.paragraph, i.said) for i in result.last_check.issues] == [(2, "出现了「突然」。")]

    # 作者又改了正文：那次检验对的是旧的一版，要说出来。
    (book.root / chapter_path(2)).write_text(text + "又添了一句。\n", encoding="utf-8")
    result = ValidationRules.model_validate_json(
        _ok("validation_rules", book.context(), chapter=2)
    )
    assert result.last_check is not None and result.last_check.current is False
    assert any("更早的一版" in note for note in result.notes)


def test_validation_rules_refuses_when_not_wired(book: Book) -> None:
    assert "没接" in _refused("validation_rules", book.context(rules=None))


# ══════════════════════════════════════════════════════════════════════════
# 四、通知
# ══════════════════════════════════════════════════════════════════════════


def _a_notice(book: Book, *, chapter: int, kind: str = "background_failure") -> None:
    enqueue_notification(
        book.conn,
        project_id=book.project_id,
        kind=kind,  # type: ignore[arg-type]
        subject_type="chapter",
        subject_id=f"chapter:{chapter}",
        chapter_number=chapter,
        title_code="test_notice",
        title_params={"operation": "summary"},
        dedupe_key=f"{kind}:{chapter}",
    )
    book.conn.commit()
    materialize_notification_outbox(book.conn, project_id=book.project_id, lease_owner="t")


def _a_conflict_proposal(book: Book, *, chapter: int) -> None:
    location = book.store.upsert_node(
        NodeSpec(project_id=book.project_id, label=NodeLabel.LOCATION, name="藏书阁")
    )
    SqliteProposalStore(book.conn).create(
        ProposalCreate(
            project_id=book.project_id,
            kind="edge_conflict",
            summary="抽取状态与当前 Canon 冲突。",
            chapter_number=chapter,
            confidence=0.98,
            items=[
                {
                    "update_kind": "location",
                    "current": {
                        "edge_id": "edge:old",
                        "subject_id": book.xiao,
                        "target_id": location.id,
                        "value": None,
                    },
                    "proposed": {
                        "edge_id": "edge:new",
                        "subject_id": book.xiao,
                        "target_id": location.id,
                        "value": None,
                        "quote": "萧决走进了藏书阁。",
                    },
                }
            ],
        )
    )
    book.conn.commit()


def test_notifications_carry_both_sources_with_names_not_ids(book: Book) -> None:
    _a_notice(book, chapter=1)
    _a_conflict_proposal(book, chapter=2)
    result = Notifications.model_validate_json(_ok("notifications", book.context()))

    assert [(row.chapter, row.notices, row.pending) for row in result.by_chapter] == [
        (1, 1, 0),
        (2, 0, 1),
    ]
    assert [n.said for n in result.notices] == ["后台有一件事没办成"]
    assert result.notices[0].chapter == 1
    assert result.notices[0].facts == {"operation": "summary"}
    assert [p.said for p in result.pending] == ["有一处设定跟抽出来的内容对不上"]
    line = result.pending[0].lines[0]
    assert "萧决 在 藏书阁" in line and "萧决走进了藏书阁。" in line
    # id 一个都不出：对照那几行说的是人名和地名。
    assert book.xiao not in json.dumps(result.pending[0].model_dump(), ensure_ascii=False)
    assert result.notes == []


def test_notifications_filter_by_chapter_and_say_when_a_chapter_is_quiet(book: Book) -> None:
    _a_notice(book, chapter=1)
    _a_conflict_proposal(book, chapter=2)
    only_two = Notifications.model_validate_json(
        _ok("notifications", book.context(), chapter=2)
    )
    assert only_two.notices == [] and len(only_two.pending) == 1
    # 按章那张表**永不被裁**，也不按章过滤：它是「全书哪几章有事」的那张表。
    assert [row.chapter for row in only_two.by_chapter] == [1, 2]

    quiet = Notifications.model_validate_json(_ok("notifications", book.context(), chapter=3))
    assert quiet.notices == [] and quiet.pending == []
    assert any("第 3 章" in note for note in quiet.notes)


def test_notifications_say_when_the_panel_is_empty_or_not_wired(book: Book) -> None:
    empty = Notifications.model_validate_json(_ok("notifications", book.context()))
    assert empty.by_chapter == [] and any("空" in note for note in empty.notes)
    assert "没接" in _refused("notifications", book.context(notices=None))


# ══════════════════════════════════════════════════════════════════════════
# 五、英文书：这四条的 notes / 拒绝都走双语表
# ══════════════════════════════════════════════════════════════════════════


def test_an_english_book_gets_english_notes_and_refusals(book: Book) -> None:
    import re

    cjk = re.compile("[一-鿿]")
    context = book.context(language=DraftLanguage.EN, events=None)
    card = CharacterCard.model_validate_json(
        _ok("character_card", context, character="萧决", chapter=2)
    )
    # 书的内容本身是中文（人名 / 别名），notes 那几句不该是。
    assert card.notes and all(cjk.search(note) is None for note in card.notes)
    assert cjk.search(_refused("chapter_events", context, first_chapter=2, last_chapter=2)) is None
    # 拒绝语会回显模型自己打进来的那个称呼（书的内容），句子本身得是英文。
    said = _refused("character_card", context, character="幽泉窟", chapter=2)
    assert "is not a character" in said
    assert cjk.search(said.replace("幽泉窟", "")) is None
