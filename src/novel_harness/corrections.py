"""作者事后改一条**已经生效（CANON）的事实** —— 「错了能看见、能改」的那个「能改」。

`declare.py` 是作者**新增**一条事实的入口，本模块是他**改正**一条已经在库里的事实的入口。
两者共用同一套机制（图的 supersede / RETRACTED、`decision_log`、canon 版本 CAS），
区别只有一个：这边的 `valid_from` 不是从引语算出来的，而是**从被改的那条事实上继承的**——
所以这边同样一个章号输入框都没有（约束 10 / ADR 0006）。

── 为什么必须先有它，自动生效才敢开 ──────────────────────────────────────

抽取直接进 CANON 之后，作者第一次看见那条事实时它已经生效了。此时他手上只有两种动作：
把整条事实丢掉（然后自己重新声明一遍），或者接受一条错的。**「改」这条路不通的时候，
「自动生效」就等于「系统自动改了作者的书，而他改不回来」。**

── 三件事本模块**故意不做** ───────────────────────────────────────────────

1. **不改 `valid_from`。** 「这条事实说错了」和「它从第几章开始成立」是两件事，
   后者只由证据决定。要改章号只能改引语，那是 `declare.py` 的活。
2. **不改事件的 `summary`。** 那是审阅队列里 `edit` 已经能干的事，且它走的是
   「PROVISIONAL 克隆成 CANON 时换一句话」——一条已生效事件的 summary 有它自己的
   问题（`story_event` 的 `UNIQUE(project_id, evidence_id, information_scope)` 让
   同一条证据在 CANON 层写不出第二个事件，所以那不是一次克隆，是一次原地改）。
   见模块末尾的「还没做的」。
3. **不删。** 改一条 KNOWS 成 BELIEVES 之后，那条 KNOWS 的行还在库里，只是
   `status = 'RETRACTED'`；名单里删掉一个人同理。**能查到改过什么，是这条退路的一半价值。**

── 和 `declare.py` 一样，日志写在事务之后 ─────────────────────────────────

`decisions.append()` 自己 commit，进不了外层事务；而写图失败（版本冲突、乱序、
名单里有个不存在的人）是**预期异常**。日志先写 = 每一次失败都在那张封死了
INSERT/UPDATE/DELETE 的表里留一条假的 EDIT，永远删不掉。
代价是微秒级崩溃窗口里丢一条编辑记录，那时事实本身已经改好了。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from . import decisions, project
from .db import Connection
from .decisions import DecisionKind, Verdict
from .events import EventCastEdit, EventCastStore, EventStoreError, EventView
from .graph import (
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeStatus,
    EdgeType,
    Evidence,
    GraphStore,
    InformationScope,
    NodeLabel,
    NodeRef,
)
from .graph.review_store import EdgeReviewStore, EdgeReviewValidationError
from .graph.sqlite_review import SqliteEdgeReviewStore

KNOWLEDGE_EDGE_TYPES: Final[frozenset[EdgeType]] = frozenset(
    {EdgeType.KNOWS, EdgeType.BELIEVES}
)
"""认知矩阵那一格上可能存在的两种边。**本模块只在这两者之间改**。

改的是「同一条证据被读成了哪一种」，所以两端、章号、证据全都不动——变的只有类型
（和 BELIEVES 那条独有的 `believed_value`）。
"""


# ══════════════════════════════════════════════════════════════════════════
# 拒绝
# ══════════════════════════════════════════════════════════════════════════


class CorrectionError(Exception):
    """作者发起的一次改正，系统拒绝执行。

    **这三个异常的 `str()` 会原样出现在小说作者的错误框里**（`api/review.py::
    _correction_error` 把它放进 `message`，工作台直接渲染那一句）。所以这里的每一句话
    都得是作者读得懂的中文：**不许出现 `KNOWS` / `BELIEVES` / `believed_value` /
    `(Character, Secret)` / 裸 id / 「面板 §3.2」这类写给维护者的东西。**

    这不是措辞洁癖，是措辞**源**的问题：前端一旦为了遮住这些词加一张
    「引擎的词 → 作者的词」的映射表，屏幕上的说法就和这里、和 CLI 不再是同一句话，
    而那张表永远只覆盖写它那天想得到的几个词。`tests/test_canon_edit_boundary.py::
    test_the_correction_layer_writes_no_engine_words` 从 AST 上钉住这条。

    ── 推论：**这一句只能由本模块写，不许转发别人的 `str(exc)`** ──────────────

    上面那道守卫扫的是「塞进构造器的字面量」。`CorrectionRefused(str(exc))` 里没有
    字面量，于是**借来的那句话在守卫眼里根本不存在**——而 `graph/sqlite_review.py` 和
    `graph/sqlite_events.py` 的异常是写给维护者的诊断：`边 edge:01J… 已不是 ACTIVE、
    未闭合、非 STALE 的 current Canon`、`只能改已生效（CANON）的事件，event:01J… 是
    PROVISIONAL`。它们全都整句穿到过作者的错误框里（2026-08-11 实测）。

    **两条编辑路径上最常撞的就是这几句**：ADR 0020 让干净的抽取结果自动升 CANON，
    作者摊开编辑器的这段时间里后台正好把那条事实换掉，是常态不是边角。
    所以按异常**类型**给出本模块自己的一句话，原异常靠 `raise … from exc` 留在日志里。
    `test_no_refusal_borrows_someone_elses_sentence` 从 AST 上钉住这条推论。
    """


class FactNotFound(CorrectionError):
    """要改的那条事实今天不在（没有、跨项目、或者已经不是 current CANON）。"""


class CorrectionRefused(CorrectionError):
    """事实在，但这次改正本身讲不通（改成它已经是的样子 / 什么都没改 / 名单里有个地点）。"""


# ══════════════════════════════════════════════════════════════════════════
# 出参 —— 一律窄引用
# ══════════════════════════════════════════════════════════════════════════


class KnowledgeCorrection(BaseModel):
    """「他知道 X」改成「他以为 X」之后的回执。

    **没有 `Edge`，也没有 `Node`。** `EdgeProps` / `NodeProps` 都是 `extra="allow"` 的，
    而这条边的 dst 按定义是一个 Secret——整份序列化出去就是秘密自己泄密
    （`graph.models.NodeRef` 的 docstring 有实测形态）。这里只出 id、窄引用、
    和面板本来就在渲染的那两个字段。
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    canon_version: int = Field(ge=0)
    decision_id: str

    character: NodeRef
    secret: NodeRef

    from_type: EdgeType
    to_type: EdgeType
    believed_value: str | None = None
    """仅 BELIEVES：他以为的那个版本。面板 §3.2 直接渲染它。"""

    since_chapter: int = Field(ge=1)
    """**继承自被改的那条边，不是作者填的**（约束 10）。血统一路回到那条证据。"""

    edge_id: str
    retracted_edge_id: str
    """被改掉的那条。它还在库里，只是 status=RETRACTED —— 查得到改过什么。"""

    closed_edge_ids: tuple[str, ...] = ()
    """写新边时被 supersede 顺手闭合的旧边（同类型、更早的那条）。"""


class EventCastCorrection(BaseModel):
    """改完一条已生效事件的在场/知情名单之后的回执。"""

    model_config = ConfigDict(frozen=True)

    project_id: str
    canon_version: int = Field(ge=0)
    decision_id: str

    event: EventView
    knowers_added: tuple[NodeRef, ...] = ()
    knowers_removed: tuple[NodeRef, ...] = ()
    participants_added: tuple[NodeRef, ...] = ()
    participants_removed: tuple[NodeRef, ...] = ()


# ══════════════════════════════════════════════════════════════════════════
# 内部
# ══════════════════════════════════════════════════════════════════════════


@contextmanager
def _business_transaction(conn: Connection) -> Iterator[None]:
    """同 `extract/proposals.py`：改 CANON 与版本 CAS 必须同成同败。"""
    if conn.in_transaction:
        raise RuntimeError("作者改正必须在没有外层事务的连接上启动")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _source_for(actor: str) -> EdgeSource:
    """谁改的 → 这条边的出处。**别写死 `AUTHOR`。**

    自动生效那条链（抽取直接进 CANON）如果哪天也来调这里，它写下的边不该冒充作者亲手
    确认过的东西——`EdgeSource` 的三个值里 `system` 就是给「推导 / 迁移 / 重放」留的。
    """
    return EdgeSource.AUTHOR if actor == decisions.DEFAULT_ACTOR else EdgeSource.SYSTEM


def _ref_payload(refs: Sequence[NodeRef]) -> list[dict[str, str]]:
    """写进 `decision_log.payload` 的节点形状：**恰好 `NodeRef` 的三个字段。**

    名字在里面是有意的（§5.7：人名，不是 ID —— ID 随重抽全部作废，重放不回去的日志
    等于没有日志）；`props` 不在里面也是有意的，理由同 `KnowledgeCorrection`。
    """
    return [{"id": ref.id, "label": ref.label.value, "name": ref.name} for ref in refs]


def _reviews(
    conn: Connection,
    graph: GraphStore,
    edge_review_store: EdgeReviewStore | None,
) -> EdgeReviewStore:
    return edge_review_store or SqliteEdgeReviewStore(conn, graph)


def _require_version(conn: Connection, project_id: str, expected: int) -> int:
    current = project.require_canon_version(conn, project_id)
    if expected != current:
        raise project.StaleBaseVersion(project_id, expected=expected, current=current)
    return current


def _evidence_of(
    reviews: EdgeReviewStore, project_id: str, evidence_id: str | None
) -> Evidence | None:
    if not evidence_id:
        return None
    try:
        return reviews.evidence(project_id, evidence_id)
    except EdgeReviewValidationError:
        # 依据查不到不该让「改回来」这条退路整个失效——它只是让这条日志少一个文本锚。
        return None


def _anchor(evidence: Evidence | None) -> dict[str, Any]:
    if evidence is None:
        return {"quote_text": None, "chapter_number": None, "para_index": None}
    return {
        "quote_text": evidence.audit.quote_text,
        "chapter_number": evidence.chapter_number,
        "para_index": evidence.audit.para_index,
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. KNOWS ↔ BELIEVES
# ══════════════════════════════════════════════════════════════════════════


def correct_knowledge(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    *,
    character_id: str,
    secret_id: str,
    to_type: EdgeType,
    believed_value: str | None = None,
    expected_canon_version: int,
    actor: str = decisions.DEFAULT_ACTOR,
    edge_review_store: EdgeReviewStore | None = None,
) -> KnowledgeCorrection:
    """「他知道 X」改成「他以为 X（内容是 Y）」，或者反过来。

    这是网文里最高频的一种抽错，也是认知矩阵最怕的一种：KNOWS 在矩阵上压着 BELIEVES，
    所以一条抽错的 KNOWS 会让面板对作者说「这个人已经知道了」，而他其实信着一个错版本。

    ── 机制：撤回 + 写新的，不是原地改类型 ────────────────────────────────

    旧边走 `RETRACTED`（不是写 `valid_to`）：作者改的是**同一条证据的读法**，
    「他知道」这条事实从来没成立过；写成 `[10,143)` 则是编了一段假的历史区间，
    而那段历史在人物卡上长得完全正常。新边走 `upsert_edge`（supersede 的唯一收敛点），
    `valid_from` 原样继承——**入参里没有章号，这条函数也没有第二条路让它进来。**

    「改回来」（KNOWS→BELIEVES→KNOWS）走 `restore_canon`：那一次撞的是被撤回的那条边的
    幂等键，而 `upsert_edge` 会把它当重跑、`status` 一个字节不动——两条边同时停在
    RETRACTED，**这一格凭空消失，且没有任何一步会报错**。

    Args:
        to_type: `KNOWS` 或 `BELIEVES`，且必须**不等于**今天那条边的类型。
            改 BELIEVES 的内容（换一个 `believed_value`）不走这里，见模块末尾。
        believed_value: `to_type is BELIEVES` 时必填、否则必须为空。
        actor: 写进 `decision_log.actor`，并决定新边的 `source`（见 `_source_for`）。

    Raises:
        FactNotFound: 这一格上今天没有 current CANON 的 KNOWS/BELIEVES 边。
        CorrectionRefused: 已经是 `to_type` 了 / `believed_value` 形状不对 /
            两端不是 Character 和 Secret。
        project.StaleBaseVersion: 作者看的是旧版本（有人在他之前改过）。
    """
    if to_type not in KNOWLEDGE_EDGE_TYPES:
        raise CorrectionRefused("这一格只有「知道」和「以为」两种，改不成别的")
    if to_type is EdgeType.BELIEVES and not (believed_value or "").strip():
        raise CorrectionRefused(
            "改成「以为」要写一句他以为的版本 —— 那句话就是这一格上会显示的东西，"
            "空着的话，这一格只会显示一片空白"
        )
    if to_type is EdgeType.KNOWS and believed_value is not None:
        raise CorrectionRefused("改成「知道」时不用写内容：他知道的就是真的那一版")

    reviews = _reviews(conn, graph, edge_review_store)
    with _business_transaction(conn):
        current_version = _require_version(conn, project_id, expected_canon_version)
        try:
            character, secret = reviews.node_refs(project_id, [character_id, secret_id])
        except EdgeReviewValidationError as exc:
            # 借不得那句话：`节点不存在或跨项目：character:01J…`（`sqlite_review.py`）。
            raise FactNotFound(
                "这一格上的人物或秘密在这本书里找不到了 —— 刷新一下看看现在有哪些。"
            ) from exc
        if character.label is not NodeLabel.CHARACTER or secret.label is not NodeLabel.SECRET:
            raise CorrectionRefused(
                f"这一格只能是一个人物对一个秘密 —— 「{character.name}」和"
                f"「{secret.name}」不是这样的一对"
            )

        held = reviews.current_knowledge(project_id, character_id, secret_id)
        wrong = [item for item in held if item.edge.type is not to_type]
        if not held:
            raise FactNotFound(
                f"「{character.name}」对「{secret.name}」现在是「不知道」 —— 这一格上没有可改的"
                "事实。要新添一条，请去那句原文上声明（章号由那句话定，不用你填）"
            )
        if not wrong:
            raise CorrectionRefused(
                f"「{character.name}」对「{secret.name}」现在就是你要改成的那一种，不用再改一次"
            )
        source_edge = wrong[0].edge
        evidence = _evidence_of(reviews, project_id, source_edge.evidence_id)

        try:
            reviews.retract_canon(project_id, [source_edge.id])
        except EdgeReviewValidationError as exc:
            # **这是两条编辑路径上最常撞的一句**，别把它当边角：干净的抽取结果会自动
            # 生效（ADR 0020），作者摊开这一格的这段时间里后台正好把这条事实换掉了。
            # 借来的那句原文是 `边 edge:01J… 已不是 ACTIVE、未闭合、非 STALE 的
            # current Canon` —— 一个小说作者读到它只会以为自己把书弄坏了。
            raise CorrectionRefused(
                "这一格刚刚在别处变过，你看到的还是变之前的样子 —— "
                "先看一眼它现在是什么，再决定这一处要不要改"
            ) from exc

        props = EdgeProps(believed_value=believed_value)
        spec = EdgeSpec(
            project_id=project_id,
            src=character_id,
            dst=secret_id,
            type=to_type,
            props=props,
            # ★ 继承，不是输入：这个数一路回到 `evidence.chapter_number`（约束 10）。
            valid_from_chapter=source_edge.valid_from_chapter,
            information_scope=InformationScope.CANON,
            confidence=source_edge.confidence,
            source=_source_for(actor),
            evidence_id=source_edge.evidence_id,
        )
        existing = graph.find_edge_by_identity(spec)
        closed: tuple[str, ...] = ()
        if existing is None or existing.status is EdgeStatus.ACTIVE:
            result = graph.upsert_edge(spec)
            new_edge_id = result.edge.id
            closed = tuple(edge.id for edge in result.closed)
        elif existing.valid_to_chapter is None:
            # 这一条正是「改回来」：那条边此刻是 RETRACTED，upsert 会把它当重跑。
            new_edge_id = reviews.restore_canon(project_id, existing.id, props=props).edge.id
        else:
            raise CorrectionRefused(
                f"「{character.name}」对「{secret.name}」这一格上有一段更早的记录已经结束、"
                "又被撤回过，直接改回来会和它撞在同一段时间上。这一处请改引语重新声明"
            )
        canon_version = project.compare_and_bump_canon_version(
            conn, project_id, current_version
        )

    decision = decisions.append(
        conn,
        project_id=project_id,
        kind=DecisionKind.KNOWLEDGE_EDIT,
        decision=Verdict.EDIT,
        # 人名，不是 ID（§5.7）。
        subject_name=character.name,
        payload={
            "target": "knowledge",
            "character": _ref_payload([character])[0],
            "secret": _ref_payload([secret])[0],
            # ★ 「改成了什么」在这两行里。只记「改过了」的日志重放不回去。
            "from": {
                "edge_id": source_edge.id,
                "edge_type": source_edge.type.value,
                "believed_value": source_edge.props.believed_value,
            },
            "to": {
                "edge_id": new_edge_id,
                "edge_type": to_type.value,
                "believed_value": believed_value,
            },
            "scope": InformationScope.CANON.value,
            "valid_from_chapter": source_edge.valid_from_chapter,
            "evidence_id": source_edge.evidence_id,
            "retracted_edge_ids": [source_edge.id],
            "closed_edge_ids": list(closed),
            "canon_version": canon_version,
        },
        **_anchor(evidence),
        actor=actor,
    )
    return KnowledgeCorrection(
        project_id=project_id,
        canon_version=canon_version,
        decision_id=decision.id,
        character=character,
        secret=secret,
        from_type=source_edge.type,
        to_type=to_type,
        believed_value=believed_value,
        since_chapter=source_edge.valid_from_chapter,
        edge_id=new_edge_id,
        retracted_edge_id=source_edge.id,
        closed_edge_ids=closed,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2 + 3. 事件的 knowers / participants
# ══════════════════════════════════════════════════════════════════════════


def correct_event_cast(
    conn: Connection,
    graph: GraphStore,
    events: EventCastStore,
    project_id: str,
    event_id: str,
    *,
    knower_ids: Sequence[str] | None = None,
    participant_ids: Sequence[str] | None = None,
    expected_canon_version: int,
    actor: str = decisions.DEFAULT_ACTOR,
    edge_review_store: EdgeReviewStore | None = None,
) -> EventCastCorrection:
    """改一条已生效事件的**知情名单 / 在场名单**。

    `knowers` 是抽取里唯一靠推断得来的那一维（谁在场是文本里写着的，谁**因此知道了**
    是猜的），所以它也是最需要改的一维。

    入参是**绝对集合**：`knower_ids=[…]` 的意思是「改完之后知情的是这些人」，不是
    「再加这些人」。`None` = 这一维不动。于是重发一次同样的请求是空操作，而不是
    把同一个人加两遍——那是审阅队列 UI 上真会发生的事（作者点两下）。

    机制见 `SqliteEventStore.edit_cast`：删一个人 = 那一行 status 改成 RETRACTED，
    行留着；加一个人先看有没有那一行（改回来，而不是插第二行撞主键）。
    **没有章号入参**：新 knower 行的 `valid_from_chapter` 只能是事件自己的章号。

    Raises:
        FactNotFound: 事件不在、跨项目、或不是一条还成立的 CANON 事件。
        CorrectionRefused: 名单里有个不是 Character 的东西，或者这次什么都没改。
    """
    if knower_ids is None and participant_ids is None:
        raise CorrectionRefused("没说要改哪一份名单 —— 知道这件事的人、在场的人，至少得动一份")

    reviews = _reviews(conn, graph, edge_review_store)
    with _business_transaction(conn):
        current_version = _require_version(conn, project_id, expected_canon_version)
        try:
            edit: EventCastEdit = events.edit_cast(
                project_id,
                event_id,
                knower_ids=knower_ids,
                participant_ids=participant_ids,
            )
        except EventStoreError as exc:
            raise _event_failure(exc) from exc
        if not edit.changed:
            # 空操作不该在那张只增不改的表里留一条「编辑」——它会把重放变成一串噪音。
            raise CorrectionRefused("这次没有要改的东西 —— 名单和现在的一模一样")
        evidence = _evidence_of(reviews, project_id, edit.event.event.evidence_id)
        canon_version = project.compare_and_bump_canon_version(
            conn, project_id, current_version
        )

    view = edit.event
    subject = view.participants[0].name if view.participants else None
    decision = decisions.append(
        conn,
        project_id=project_id,
        kind=DecisionKind.EVENT_EDIT,
        decision=Verdict.EDIT,
        subject_name=subject,
        payload={
            "target": "event_cast",
            "event_id": view.event.id,
            "chapter_number": view.event.chapter_number,
            "summary": view.event.summary,
            # ★ 「改成了什么」：diff 说改了谁，after 说改完是什么样。缺后者的日志
            #   在中间夹进第二次编辑之后就重放不回去了。
            "knowers": {
                "added": _ref_payload(edit.knowers_added),
                "removed": _ref_payload(edit.knowers_removed),
                "after": _ref_payload(view.knowers),
            },
            "participants": {
                "added": _ref_payload(edit.participants_added),
                "removed": _ref_payload(edit.participants_removed),
                "after": _ref_payload(view.participants),
            },
            "evidence_id": view.event.evidence_id,
            "canon_version": canon_version,
        },
        **_anchor(evidence),
        actor=actor,
    )
    return EventCastCorrection(
        project_id=project_id,
        canon_version=canon_version,
        decision_id=decision.id,
        event=view,
        knowers_added=edit.knowers_added,
        knowers_removed=edit.knowers_removed,
        participants_added=edit.participants_added,
        participants_removed=edit.participants_removed,
    )


def _event_failure(exc: EventStoreError) -> CorrectionError:
    """事件仓储的失败翻成本模块的两类，**并且换一句作者读得懂的话**。

    「事件不在 / 不是 CANON / 已撤回」是 `FactNotFound`（作者点的那条今天不在了）；
    「名单里有个地点」是 `CorrectionRefused`（事实在，是这次请求讲不通）。

    ── 为什么不是 `str(exc)`（它原先就是那么写的）────────────────────────────

    `graph/sqlite_events.py` 的那几句是写给维护者的诊断，而这里的返回值会**整句**
    进 `message`、被工作台原样渲染。实测穿到过作者脸上的三句：

        event 不存在或跨项目：event:01J…
        只能改已生效（CANON）的事件，event:01J… 是 PROVISIONAL——…走审阅队列的 edit
        名单里只能是 Character，secret:01J… 是 Secret

    原异常不丢：调用处是 `raise _event_failure(exc) from exc`，维护者要的那句话在
    traceback 里。**分类靠类型，不靠转发字符串**——转发的那一刻，措辞源就从本模块
    搬到了仓储层，而仓储层没有任何理由用作者的话说事。
    """
    from .events import EventCastError, EventNotFound, EventScopeError

    if isinstance(exc, EventCastError):
        # 名单两维只收人物。前端的候选人里有一半来自这条情节现有的名单（不按花名册
        # 过滤，否则名单里那个花名册没有的人会在界面上凭空消失），所以这条到得了。
        return CorrectionRefused("这两份名单里只能放人物 —— 勾上的有一个不是人物")
    if isinstance(exc, EventNotFound):
        return FactNotFound(
            "这条情节现在不在了 —— 它可能刚被撤回或者改掉了。"
            "刷新一下看看这一章现在有哪些情节。"
        )
    if isinstance(exc, EventScopeError):
        # 只有一种到得了：那条情节还没确认（编辑器只列已确认的，但日志里的旧坐标
        # 和另一个标签页的操作都能把一个还没确认的 id 送到这儿）。
        return FactNotFound("这条情节还没确认 —— 先在上面确认它，之后才能改它的名单。")
    return FactNotFound(
        "这条情节现在改不了 —— 它的原文依据变了，或者它已经被撤回。"
        "等这一章重新整理过再来改。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 还没做的（写在这儿免得下一个人以为是遗漏）
# ══════════════════════════════════════════════════════════════════════════
#
# - **改一条已生效事件的 `summary`。** 审阅队列的 `edit` 能改（那是 PROVISIONAL 克隆成
#   CANON 时换一句话），但对一条**已经是 CANON** 的事件它做不到：`story_event` 有
#   `UNIQUE(project_id, evidence_id, information_scope)`，同一条证据在 CANON 层写不出
#   第二个事件，所以「写新的 + 撤回旧的」在这里物理上不成立。要它得先决定：是把那个
#   UNIQUE 放开（那会让「一条引语一个事件」这条不变式没了），还是承认 summary 是
#   一条可原地改的显示字段。**这是个 schema 决定，不该在一个下午顺手做掉。**
# - **改一条已生效边的 `believed_value`**（不换类型）。它撞的是同一个幂等键，
#   `upsert_edge` 会当重跑只更 props —— 那正是「原地改」，而它到底算不算一次
#   supersede 值得单独裁一次。
# - **改 `participants` 之外的事件维度**（`revealed_facts`）。没有消费者要它
#   （ADR 0005 的增长规则），加了就是一个没人读的写路径。
