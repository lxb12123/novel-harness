"""右栏那几栏，写作助手也读得到（2026-09-12）——**角色卡 / 事件 / 检验规则 / 通知**。

作者 2026-09-12 的原话：「我要求把这几个功能给他补进去，就是能读这里面的功能，
就是这上面按钮的那些功能……每个按钮功能里面的每个内容」。在这之前写作助手对右栏的
覆盖是：目录 / 摘要 / 正文 / 谁在场 / 一个人的处境（`index.py` + `tools.py`），而
**关系、事件、角色卡上的信息、检验规则、通知一条路都没有**——当时只有为起草设计的
写前校准顺带交出关系和事件，评价一章时模型不会去调它（那条链同日随 ADR 0047 砍了）。

四条工具、四栏，每条读的都是**面板自己那条读法**，不另开判据：

| 工具 | 右栏 | 读什么 | 从哪来 |
|---|---|---|---|
| `character_card` | 角色册 → 一个人 | 基本信息 / 别名 / 处境 / 关系 / 他经历过的事 | `EventIndex.profile` + `store.aliases_of` + `state_at()`（含 `relations` 投影）+ `events_for_one_character` |
| `chapter_events` | 事件 | 这一章已确认的情节（在场 / 知情） | `EventIndex.events_for_chapter`（CANON） |
| `validation_rules` | 检验规则 | 规则目录（停用的也列）+ 某一章最近一次检验 | `RulesIndex`（`checks.service.RulesReader`） |
| `notifications` | 通知 | OPEN 的通知 + 待确认的提案 | `NoticeIndex`（`notices.NoticeReader`） |

── 边界一在这里的落点（ADR 0019）──────────────────────────────────────────

出参只有 `NodeRef`（id / label / name）和纯量，`Node` 一个都不出、`props` 一个字段都不碰
（`tests/test_agent_tools.py` 的 AST 守卫罩着本模块）。角色卡上那几行字（性别 / 性格 /
背景 / 备注）走的是 `EventIndex.profile` 那个**收窄过的** `CharacterProfileView`——
它和进写作提示的是同一份（`draft/product_assemble.py::_profile_line`），所以这儿没有
打开任何写作模型看不到的东西；关系的值在 `graph/` 那边就解读好了
（`StateSnapshot.relations`），本层只读结果。

**别名从这一条起交给模型了**（角色卡上本来就画着它们）。`index.py` 当年「一个别名都
不给」的理由是别名里装着秘密的认知边界——秘密整套 2026-08-25 下线（ADR 0039）之后，
别名只剩 canonical / alias / nickname / title 四档，都是作者在角色卡上能看见能改的东西。
`book_index` 那一层照旧只出正式名：那是目录，不是卡。

── 三条纪律照抄 `index.py`────────────────────────────────────────────────────

1. **裁了什么必须说出来**：每一处按预算收的列表都带 `*_omitted`。
2. **空和瞎分开**：端口没接线时明说（`events_blind` / 拒绝），不静默给一个空表。
3. **超过作者进度的只标不挡**：每条带 `future`，整份带 `_future_note` 那一句。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..draft.length import DraftLanguage
from ..events import EventView
from ..events.models import ProposalRecord
from ..graph import AliasKind, InformationScope, NodeLabel
from ..graph.models import NodeRef
from ..importer import chapter_path, text_digest
from ..panel.state import character_state as _state_at
from ..system_notifications import SystemNotification
from .index import _cost, _future_note, _take_newest, _take_oldest, resolve_one
from .ports import ToolContext, ToolRefused
from .prompt_terms import message

# ══════════════════════════════════════════════════════════════════════════
# 角色卡
# ══════════════════════════════════════════════════════════════════════════


class CharacterCardArgs(BaseModel):
    """查「一个人的角色卡」：右栏「角色册」里点开一个人看到的那一页。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    character: str = Field(
        min_length=1,
        description="人物的称呼，用作者在正文里的那个叫法。有歧义的叫法会被拒绝。",
    )
    chapter: int = Field(
        ge=1,
        description="按第几章的时点看他的处境和关系（AS OF 第几章，纯查询坐标）。",
    )


class CardProfile(BaseModel):
    """角色卡上作者写的 / 抽取出来的那几行。**空就是空**：作者没填，这儿不编。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    main_character: bool | None = None


class CardState(BaseModel):
    """一条 `HAS_STATE`：维度显示名 + 值 + 从第几章起。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    value: str | None = None
    since_chapter: int


class CardRelation(BaseModel):
    """他和另一个人之间记录过的关系（`RELATED_TO`）。`value` 是作者/抽取器给这段
    关系写的字；空 = 只记了「有关系」，没写是什么关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    peer: NodeRef
    value: str | None = None
    since_chapter: int


class CardEvent(BaseModel):
    """他经历过的一件事。**一件事跟几个人相关，就在几个人的卡上各出现一次。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    summary: str
    participants: list[str] = Field(default_factory=list)
    knowers: list[str] = Field(default_factory=list)
    future: bool = False


class CharacterCard(BaseModel):
    """`character_card` 的出参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    character: NodeRef
    profile: CardProfile
    aliases: list[str] = Field(default_factory=list)
    """正式名之外的叫法（别名 / 绰号 / 称号），角色卡上那一排。"""

    location: NodeRef | None = None
    states: list[CardState] = Field(default_factory=list)
    """按「从第几章起」升序。预算装不下时留最近改过的那些（`states_omitted` 说丢了几条）。"""
    states_omitted: int = 0
    is_dead: bool = False
    """「死没死」照旧给：那是一条真的记在图上的事实（人物卡「状态」那一格也画它）。
    **「登场没」不给**——`first_appears_chapter` 零消费者、真书上一个都没设过（ADR 0042）。"""

    relations: list[CardRelation] = Field(default_factory=list)
    relations_omitted: int = 0

    events: list[CardEvent] = Field(default_factory=list)
    """按章号升序，**只有已确认（CANON）的**。预算装不下时留最近的那些。"""
    events_total: int = 0
    events_omitted: int = 0
    events_blind: bool = False
    """`True` = 事件那个端口没接线，`events` 为空**不代表他什么都没经历过**。"""

    working_chapter: int | None = None
    future_from_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)


def _names(refs: list[NodeRef]) -> list[str]:
    return [ref.name for ref in refs]


def _card_event(view: EventView, context: ToolContext) -> CardEvent:
    return CardEvent(
        chapter=view.event.chapter_number,
        summary=view.event.summary,
        participants=_names(list(view.participants)),
        knowers=_names(list(view.knowers)),
        future=context.is_future(view.event.chapter_number),
    )


def handle_character_card(args: CharacterCardArgs, context: ToolContext) -> CharacterCard:
    resolutions = context.store.resolve(context.project_id, [args.character])
    node = resolve_one(args.character, resolutions[0] if resolutions else None, context.language)
    if node.label is not NodeLabel.CHARACTER:
        # 集合判断：地点 / 维度也在角色册里，它们没有「卡」。
        raise ToolRefused(
            message(
                "not_a_character_card",
                context.language,
                surface=args.character,
                label=node.label,
            )
        )

    snapshot = _state_at(context.store, context.project_id, node.id, args.chapter)
    aliases = [
        alias.surface
        for alias in context.store.aliases_of(context.project_id, node.id)
        if alias.kind is not AliasKind.CANONICAL and alias.status == "ACTIVE"
    ]

    # 一张卡上四份清单分预算：状态和关系各最多四分之一，剩下的全给事件。
    # **真书上主角有 131 个状态维度、655 件事**（2026-09-12 实测）——不分的话事件会把
    # 整份预算吃光，状态一条都进不来；而卡的第一用途是「他是谁、此刻怎么样」，
    # 整条时间线归 chapter_events 按章去翻。三份都从**最近**那一端留（`_take_newest`）。
    budget = context.return_units
    states_all = sorted(
        (
            CardState(dimension=state.dim.name, value=state.value, since_chapter=state.since_chapter)
            for state in snapshot.states
        ),
        key=lambda state: state.since_chapter,
    )
    states, states_omitted = _take_newest(states_all, budget // 4)
    relations_all = sorted(
        (
            CardRelation(
                peer=NodeRef.of(relation.peer),
                value=relation.value,
                since_chapter=relation.since_chapter,
            )
            for relation in snapshot.relations
        ),
        key=lambda relation: relation.since_chapter,
    )
    relations, relations_omitted = _take_newest(relations_all, budget // 4)
    spent = sum(_cost(item) for item in [*states, *relations])

    events_blind = context.events is None
    events_all: list[CardEvent] = []
    if context.events is not None:
        events_all = [
            _card_event(view, context)
            for view in context.events.events_for_one_character(
                context.project_id, node.id, InformationScope.CANON
            )
        ]
    events, events_omitted = _take_newest(events_all, max(0, budget - spent))

    notes: list[str] = []
    if events_blind:
        notes.append(message("card_events_not_wired", context.language))
    if events_omitted:
        notes.append(
            message(
                "card_events_omitted",
                context.language,
                total=len(events_all),
                kept=len(events),
                omitted=events_omitted,
            )
        )
    if relations_omitted:
        notes.append(
            message(
                "card_relations_omitted",
                context.language,
                total=len(relations_all),
                omitted=relations_omitted,
            )
        )
    if states_omitted:
        notes.append(
            message(
                "card_states_omitted",
                context.language,
                total=len(states_all),
                omitted=states_omitted,
            )
        )
    future_note = _future_note(context, sum(1 for event in events if event.future))
    if future_note:
        notes.append(future_note)

    profile = context.events.profile(context.project_id, node.id) if context.events else None
    return CharacterCard(
        chapter=args.chapter,
        character=NodeRef.of(snapshot.node),
        profile=CardProfile(
            gender=profile.gender if profile else None,
            personality=profile.personality if profile else None,
            background=profile.background if profile else None,
            character_notes=profile.character_notes if profile else None,
            main_character=profile.main_character if profile else None,
        ),
        aliases=aliases,
        location=NodeRef.of(snapshot.location) if snapshot.location is not None else None,
        states=states,
        states_omitted=states_omitted,
        is_dead=snapshot.is_dead,
        relations=relations,
        relations_omitted=relations_omitted,
        events=events,
        events_total=len(events_all),
        events_omitted=events_omitted,
        events_blind=events_blind,
        working_chapter=context.working_chapter,
        future_from_chapter=context.future_from_chapter,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════
# 事件
# ══════════════════════════════════════════════════════════════════════════


class ChapterEventsArgs(BaseModel):
    """查「这几章的事件」：右栏「事件」那一栏——已经确认、写进书里的情节，按章列。
    **区间由你给**：面板上是到当前章为止的全部，一次问整本书装不下。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_chapter: int = Field(ge=1, description="区间下界（含）。")
    last_chapter: int = Field(ge=1, description="区间上界（含）。只看一章就两个都填它。")

    @model_validator(mode="after")
    def _range_is_ordered(self) -> ChapterEventsArgs:
        if self.last_chapter < self.first_chapter:
            raise ValueError("last_chapter 不能小于 first_chapter")
        return self


class ChapterEvent(BaseModel):
    """一件已确认的事：第几章、一句概要、在场的人、知道这件事的人。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    summary: str
    participants: list[str] = Field(default_factory=list)
    knowers: list[str] = Field(default_factory=list)
    future: bool = False


class ChapterEvents(BaseModel):
    """`chapter_events` 的出参。**没有事的章不在 `events` 里出现**，而「没有」有两种
    （没整理过 / 整理了没留下），这一层分不出来——`notes` 里说清。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_chapter: int
    last_chapter: int
    events: list[ChapterEvent] = Field(default_factory=list)
    """按 (章号, 事件) 升序。预算装不下时留**最后**那些章的（`omitted` 说丢了几件）。"""
    events_total: int = 0
    events_omitted: int = 0
    chapters_with_events: list[int] = Field(default_factory=list)
    """区间里哪几章有已确认的事（**永不被裁**）：列表可以缺，这个不许缺。"""
    working_chapter: int | None = None
    future_from_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)


def handle_chapter_events(args: ChapterEventsArgs, context: ToolContext) -> ChapterEvents:
    if context.events is None:
        # 空和瞎分开：端口没接线就拒，不交一份看起来像「这几章没事发生」的空表。
        raise ToolRefused(message("events_not_wired", context.language))
    # 面板那条读法是 AS OF（到第 N 章为止的全部），这儿按区间切一刀——
    # 时态过滤仍然只有 `graph/queries.py` 那一份，这里只是在它的结果上按章号挑。
    views = context.events.events_for_chapter(
        context.project_id, args.last_chapter, InformationScope.CANON
    )
    events_all = [
        ChapterEvent(
            chapter=view.event.chapter_number,
            summary=view.event.summary,
            participants=_names(list(view.participants)),
            knowers=_names(list(view.knowers)),
            future=context.is_future(view.event.chapter_number),
        )
        for view in views
        if args.first_chapter <= view.event.chapter_number <= args.last_chapter
    ]
    events, omitted = _take_newest(events_all, context.return_units)
    notes: list[str] = []
    if not events_all:
        notes.append(
            message(
                "chapters_have_no_confirmed_events",
                context.language,
                first_chapter=args.first_chapter,
                last_chapter=args.last_chapter,
            )
        )
    if omitted:
        notes.append(
            message(
                "chapter_events_omitted",
                context.language,
                total=len(events_all),
                kept=len(events),
                omitted=omitted,
            )
        )
    future_note = _future_note(context, sum(1 for event in events if event.future))
    if future_note:
        notes.append(future_note)
    return ChapterEvents(
        first_chapter=args.first_chapter,
        last_chapter=args.last_chapter,
        events=events,
        events_total=len(events_all),
        events_omitted=omitted,
        chapters_with_events=sorted({event.chapter for event in events_all}),
        working_chapter=context.working_chapter,
        future_from_chapter=context.future_from_chapter,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════
# 检验规则
# ══════════════════════════════════════════════════════════════════════════


class ValidationRulesArgs(BaseModel):
    """查「检验规则」：右栏那一栏——作者给这本书定的规则，以及某一章最近一次检验的结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int | None = Field(
        default=None,
        ge=1,
        description="顺带看这一章最近一次检验的结果；不传就只列规则。",
    )


class RuleView(BaseModel):
    """一条规则。`literal` = 正文里不许出现的那段字（`forbidden_literal` 模板）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    enabled: bool
    built_in: bool
    literal: str | None = None


class IssueView(BaseModel):
    """最近一次检验命中的一处：第几段、那一句、规则怎么说。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraph: int
    quote: str
    said: str


class LastCheck(BaseModel):
    """某一章最近一次检验。`current` = 验的是不是作者眼前这一版正文（判据是正文哈希，
    同右栏那颗闪电）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    checked_at: str
    passed: bool
    current: bool | None = None
    issues: list[IssueView] = Field(default_factory=list)
    issues_omitted: int = 0


class ValidationRules(BaseModel):
    """`validation_rules` 的出参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: list[RuleView] = Field(default_factory=list)
    rules_omitted: int = 0
    last_check: LastCheck | None = None
    notes: list[str] = Field(default_factory=list)


def handle_validation_rules(args: ValidationRulesArgs, context: ToolContext) -> ValidationRules:
    if context.rules is None:
        raise ToolRefused(message("rules_not_wired", context.language))
    rules_all = [
        RuleView(
            title=entry.title,
            enabled=entry.enabled,
            built_in=entry.template == "system",
            literal=(
                str(entry.config.get("literal"))
                if entry.template == "forbidden_literal" and entry.config.get("literal")
                else None
            ),
        )
        for entry in context.rules.catalog(context.project_id)
    ]
    budget = context.return_units
    rules, rules_omitted = _take_oldest(rules_all, budget // 2)
    spent = sum(_cost(item) for item in rules)

    notes: list[str] = []
    if not rules_all:
        notes.append(message("no_rules_yet", context.language))
    if rules_omitted:
        notes.append(
            message("rules_omitted", context.language, total=len(rules_all), omitted=rules_omitted)
        )

    last_check: LastCheck | None = None
    if args.chapter is not None:
        digest = context.rules.latest_report(context.project_id, args.chapter)
        if digest is None:
            notes.append(message("chapter_never_checked", context.language, chapter=args.chapter))
        else:
            issues_all = [
                IssueView(
                    paragraph=issue.anchor.para_index,
                    quote=issue.anchor.quote_text,
                    said=issue.message,
                )
                for issue in digest.issues
            ]
            issues, issues_omitted = _take_oldest(issues_all, max(0, budget - spent))
            current = _checked_current_text(context, args.chapter, digest.text_sha256)
            last_check = LastCheck(
                chapter=args.chapter,
                checked_at=digest.created_at,
                passed=digest.gate == "passed",
                current=current,
                issues=issues,
                issues_omitted=issues_omitted,
            )
            if current is False:
                notes.append(
                    message("check_is_stale", context.language, chapter=args.chapter)
                )
            if issues_omitted:
                notes.append(
                    message(
                        "check_issues_omitted",
                        context.language,
                        total=len(issues_all),
                        omitted=issues_omitted,
                    )
                )
    return ValidationRules(
        rules=rules, rules_omitted=rules_omitted, last_check=last_check, notes=notes
    )


def _checked_current_text(context: ToolContext, chapter: int, checked_sha: str | None) -> bool | None:
    """那次检验验的是不是磁盘上此刻这一版。**答不了就 `None`**（不猜）。"""
    if checked_sha is None or context.root_path is None:
        return None
    file = Path(context.root_path) / chapter_path(chapter)
    if not file.is_file():
        return None
    return text_digest(file.read_text(encoding="utf-8-sig")) == checked_sha


# ══════════════════════════════════════════════════════════════════════════
# 通知
# ══════════════════════════════════════════════════════════════════════════


class NotificationsArgs(BaseModel):
    """查「通知」：右栏那一栏——系统留给作者的提醒，以及等他确认的提案。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int | None = Field(
        default=None,
        ge=1,
        description="只看和这一章有关的；不传就看全书的（按章数出来一张表，再给最近的那些）。",
    )


class NoticeView(BaseModel):
    """一条通知。`said` 是这一类通知在面板上的抬头；`facts` 是它带的原始事实
    （第几段、谁被移出了、丢了几件事……），**引擎不替它们编成句子**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    said: str
    chapter: int | None = None
    when: str
    facts: dict[str, Any] = Field(default_factory=dict)


class PendingProposal(BaseModel):
    """一条等作者确认的提案。`lines` 是「当前 → 提议」的对照，或者那条待确认情节的概要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    said: str
    chapter: int | None = None
    when: str
    lines: list[str] = Field(default_factory=list)


class ChapterNoticeCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int | None
    notices: int = 0
    pending: int = 0


class Notifications(BaseModel):
    """`notifications` 的出参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int | None = None
    by_chapter: list[ChapterNoticeCount] = Field(default_factory=list)
    """全书按章数出来的那张表（**永不被裁**）：哪几章有多少条，一眼看得到。"""
    notices: list[NoticeView] = Field(default_factory=list)
    notices_total: int = 0
    notices_omitted: int = 0
    pending: list[PendingProposal] = Field(default_factory=list)
    pending_total: int = 0
    pending_omitted: int = 0
    notes: list[str] = Field(default_factory=list)


_NOTICE_KIND_KEYS: Final[dict[str, str]] = {
    "summary_mismatch": "notice_kind_summary_mismatch",
    "background_failure": "notice_kind_background_failure",
    "validation_blocked": "notice_kind_validation_blocked",
    "text_advisory": "notice_kind_text_advisory",
    "extraction_yielded_nothing": "notice_kind_extraction_yielded_nothing",
    "import_toc_skipped": "notice_kind_import_toc_skipped",
    "event_cast_changed": "notice_kind_event_cast_changed",
    "proposal_conflict": "notice_kind_proposal_conflict",
    "proposal_low_confidence": "notice_kind_proposal_low_confidence",
}
"""通知的类型 → 抬头那一句的键。**类型码本身不上屏**（同前端 `KIND_TITLE` 那张表）。"""


def _notice_said(kind: str, language: DraftLanguage) -> str:
    key = _NOTICE_KIND_KEYS.get(kind)
    return message(key if key else "notice_kind_unknown", language)


def _notice_view(item: SystemNotification, language: DraftLanguage) -> NoticeView:
    return NoticeView(
        said=_notice_said(item.kind, language),
        chapter=item.chapter_number,
        when=item.created_at,
        facts=dict(item.title_params or {}),
    )


def _fact_line(
    subject: str, update_kind: str, target: str, value: str | None, language: DraftLanguage
) -> str:
    """一行「当前」或「提议」的事实。**整句模板**，措辞和面板同一套（`SystemNotifications.tsx::factLine`）。"""
    if update_kind == "location":
        return message("fact_line_location", language, subject=subject, target=target)
    if update_kind == "relationship":
        key = "fact_line_relationship" if value else "fact_line_relationship_bare"
        return message(key, language, subject=subject, target=target, value=value or "")
    key = "fact_line_state" if value else "fact_line_state_bare"
    return message(key, language, subject=subject, target=target, value=value or "")


def _proposal_lines(record: ProposalRecord, language: DraftLanguage) -> list[str]:
    """`items` 是开放 JSON，认得的两种形状各渲染一种，认不出的一条**跳过**（不编）。"""
    names = {ref.id: ref.name for ref in record.node_refs}
    unknown = message("unknown_name_placeholder", language)

    def name_of(node_id: Any) -> str:
        return names.get(str(node_id), unknown)

    lines: list[str] = []
    for raw in record.items:
        if not isinstance(raw, dict):
            continue
        if "current" in raw and "proposed" in raw:
            current, proposed = raw.get("current"), raw.get("proposed")
            if not isinstance(current, dict) or not isinstance(proposed, dict):
                continue
            kind = str(raw.get("update_kind", ""))
            lines.append(
                message(
                    "proposal_conflict_pair",
                    language,
                    current=_fact_line(
                        name_of(current.get("subject_id")),
                        kind,
                        name_of(current.get("target_id")),
                        current.get("value"),
                        language,
                    ),
                    proposed=_fact_line(
                        name_of(proposed.get("subject_id")),
                        kind,
                        name_of(proposed.get("target_id")),
                        proposed.get("value"),
                        language,
                    ),
                    quote=str(proposed.get("quote") or ""),
                )
            )
        elif raw.get("source_kind") == "event":
            lines.append(
                message(
                    "proposal_event_line",
                    language,
                    summary=str(raw.get("summary") or ""),
                    quote=str(raw.get("quote") or ""),
                )
            )
    return lines


def _pending_view(record: ProposalRecord, language: DraftLanguage) -> PendingProposal:
    kind = "proposal_conflict" if record.kind == "edge_conflict" else "proposal_low_confidence"
    return PendingProposal(
        said=_notice_said(kind, language),
        chapter=record.chapter_number,
        when=record.created_at,
        lines=_proposal_lines(record, language),
    )


def handle_notifications(args: NotificationsArgs, context: ToolContext) -> Notifications:
    if context.notices is None:
        raise ToolRefused(message("notices_not_wired", context.language))
    language = context.language
    open_all = context.notices.open_notices(context.project_id)
    pending_all = context.notices.pending_proposals(context.project_id)

    # 按章那张表**永不被裁**：列表可以缺，「哪几章有事」这个数不许缺。
    counts: dict[int | None, list[int]] = {}
    for item in open_all:
        counts.setdefault(item.chapter_number, [0, 0])[0] += 1
    for record in pending_all:
        counts.setdefault(record.chapter_number, [0, 0])[1] += 1
    by_chapter = [
        ChapterNoticeCount(chapter=chapter, notices=n, pending=p)
        for chapter, (n, p) in sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    ]

    if args.chapter is not None:
        open_all = [item for item in open_all if item.chapter_number == args.chapter]
        pending_all = [record for record in pending_all if record.chapter_number == args.chapter]

    notice_views = [_notice_view(item, language) for item in open_all]
    pending_views = [_pending_view(record, language) for record in pending_all]
    budget = context.return_units - sum(_cost(row) for row in by_chapter)
    budget = max(0, budget)
    notices, notices_omitted = _take_newest(notice_views, budget // 2)
    spent = sum(_cost(item) for item in notices)
    pending, pending_omitted = _take_newest(pending_views, max(0, budget - spent))

    notes: list[str] = []
    if not open_all and not pending_all:
        notes.append(
            message("no_open_notices_for_chapter", language, chapter=args.chapter)
            if args.chapter is not None
            else message("no_open_notices", language)
        )
    if notices_omitted:
        notes.append(
            message(
                "notices_omitted", language, total=len(notice_views), omitted=notices_omitted
            )
        )
    if pending_omitted:
        notes.append(
            message(
                "pending_omitted", language, total=len(pending_views), omitted=pending_omitted
            )
        )
    return Notifications(
        chapter=args.chapter,
        by_chapter=by_chapter,
        notices=notices,
        notices_total=len(notice_views),
        notices_omitted=notices_omitted,
        pending=pending,
        pending_total=len(pending_views),
        pending_omitted=pending_omitted,
        notes=notes,
    )
