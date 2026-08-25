"""作者事后改一条**已经生效（CANON）的事实** —— 「错了能看见、能改」的那个「能改」。

`declare.py` 是作者**带着一句引语**新增一条事实的入口，本模块是他面对**已经在库里**
那一格时的入口。两者共用同一套机制（图的 supersede / RETRACTED、`decision_log`、
canon 版本 CAS），区别只有一个：这边的 `valid_from` 不是从引语算出来的——
所以这边同样一个章号输入框都没有（约束 10 / ADR 0006）。

本模块今天只有一种写：**改事件的在场 / 知情名单**（`correct_event_cast`，`valid_from`
从被改的那条事实上继承）。

原来还有两种——`correct_knowledge`（改一条已生效的 KNOWS↔BELIEVES）和 `add_knowledge`
（认知矩阵那一格空白上手工添一条，**没有证据**，`valid_from` = 作者正在看的那一章）。
**两个都随秘密下线一起没了**（ADR 0039）。「补」那一路是这个模块里唯一一条不带证据
的写，它消失之后**本模块的每一次写都有一条上游事实作为出处**——哪天要把「补」这个
形态加回来（对别的什么东西），先去 git 历史里读它当年那段 docstring：那条例外是有
代价的，不是顺手。

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
3. **不删。** 名单里删掉一个人之后，那条记录还在库里，只是 `status = 'RETRACTED'`。
   **能查到改过什么，是这条退路的一半价值。**

── 和 `declare.py` 一样，日志写在事务之后 ─────────────────────────────────

`decisions.append()` 自己 commit，进不了外层事务；而写图失败（版本冲突、乱序、
名单里有个不存在的人）是**预期异常**。日志先写 = 每一次失败都在那张封死了
INSERT/UPDATE/DELETE 的表里留一条假的 EDIT，永远删不掉。
代价是微秒级崩溃窗口里丢一条编辑记录，那时事实本身已经改好了。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import decisions, project
from .db import Connection
from .decisions import DecisionKind, Verdict
from .events import EventCastEdit, EventCastStore, EventStoreError, EventView
from .graph import (
    EdgeSource,
    Evidence,
    GraphStore,
    NodeRef,
)
from .graph.review_store import EdgeReviewStore, EdgeReviewValidationError
from .graph.sqlite_review import SqliteEdgeReviewStore

# ══════════════════════════════════════════════════════════════════════════
# 拒绝
# ══════════════════════════════════════════════════════════════════════════


class CorrectionError(Exception):
    """作者发起的一次改正，系统拒绝执行。

    **这三个异常的 `str()` 会原样出现在小说作者的错误框里**（`api/review.py::
    _correction_error` 把它放进 `message`，工作台直接渲染那一句）。所以这里的每一句话
    都得是作者读得懂的中文：**不许出现 `LOCATED_AT` / `information_scope` / 裸 id /
    「面板 §3.2」这类写给维护者的东西。**

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
    等于没有日志）；`props` 不在里面也是有意的（`NodeProps` 是 `extra="allow"`，
    作者写的 `plot_note` 会跟着整份进那张不可变的表）。
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
    # 021 / Task 9：作者改过名单的整套 incidence 视为作者覆盖，机器重放不得再碰
    # （`cast_owner` 是「当前解释由谁接管」，`story_event.source` 仍是 extractor 起源）。
    # SQL 只住 graph 层（arch guard），这里经 queries 调用。
    from .graph import queries

    queries.mark_canon_event_cast_author(
        conn, view.event.id, project_id, decision.id
    )
    conn.commit()
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
        名单里只能是 Character，loc:01J… 是 Location

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
