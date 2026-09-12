"""把「待确认提案」现读现拼成 `SystemNotification` 形状（2026-08-31）。

背景：右栏原来的「待确认」tab 整个搬进了「通知」——作者原话「待确认直接改成
事件，然后将待确认搬到通知那边」。`proposal_set` 早就有自己的一整套真相
（`PENDING`/`ACCEPTED`/... 状态机、`currentness`、审计快照），**这个模块不重写
它，只在读的时候把它套进 `SystemNotification` 的壳**：

- 不写 `system_notification_outbox`/`system_notification` 表：提案接受/驳回/
  改一改之后，它自然从 `proposals.pending()` 里消失，下一次 GET 就看不到它了，
  不需要专门再去把某条通知标 RESOLVED——**少一次写就是少一处两边可能对不上**。
- `id` 直接用 proposal 自己的 id：`SystemNotification.id` 只要求在这一次
  响应里唯一，proposal id 和 `new_id(EntityType.SYSTEM_NOTIFICATION, ...)`
  本来就是不同的 id 命名空间（`ids.py`），不会撞。
- `jump` 留空：提案的 `items` 里带的是一句引语（`quote`），不是
  `(para_index, quote_text, occurrence_k)` 三元锚，前端补不出「去这一句」，
  只能退到 `chapter_number` 那条「去这一章」的路——`NotificationRow` 本来就有
  这条兜底，不用另外改它。
- `title_code`/`title_params` 只给一句摘要（跟原来卡片的抬头「关系冲突」/
  「需要确认的情节」一个量级）：完整的当前/提议对照、在场/知情、可信度、
  原文引用，前端拿 `subject_id` 去 `GET .../proposals`（项目全量版）里找同一条
  记录，用原来 `ProposalCard` 那套渲染——通知列表不是第二个地方重新拼一遍
  这些字段。
- **`new_character` 那一档不出现在这里**：ADR 0020 补记已经把它从「待确认」
  卡片列表里去掉了（只剩一行「这些不用再处理了」的说明），这次搬家干脆不把
  那条说明也带过来——它说的是一个已经退休的机制，继续找地方安放它不会再有
  新读者需要这句话。
"""

from __future__ import annotations

from .events.models import ProposalRecord
from .graph.sqlite_proposals import SqliteProposalStore
from .system_notifications import SystemNotification

_ACTIVE_KINDS = {"edge_conflict", "low_confidence_main"}

_KIND_MAP: dict[str, str] = {
    "edge_conflict": "proposal_conflict",
    "low_confidence_main": "proposal_low_confidence",
}


def _editable(proposal: ProposalRecord) -> bool:
    """跟 `ProposalReviewTab.tsx` 原来那条判据一字不差：只有一件事、不牵扯关系边。

    两边各判一次是因为前端要用它决定画不画「改一改」按钮，后端要用它决定
    要不要把 `"edit"` 放进 `actions`——**判据必须是同一条**，不然会出现按钮
    露出来但点了 422，或者按钮没画出来但其实能编辑，两种都是「界面说的和
    后端能做的对不上」。 """
    return len(proposal.event_ids) == 1 and len(proposal.edge_ids) == 0


def _actions(proposal: ProposalRecord) -> tuple[str, ...]:
    if proposal.kind == "low_confidence_main" and _editable(proposal):
        return ("accept", "reject", "edit")
    return ("accept", "reject")


def _title_code(proposal: ProposalRecord) -> str:
    return (
        "proposal_conflict_title"
        if proposal.kind == "edge_conflict"
        else "proposal_low_confidence_title"
    )


def pending_active_proposals(
    proposals: SqliteProposalStore, project_id: str
) -> list[ProposalRecord]:
    """项目全量 PENDING 里**还会画出来的那几档**（`_ACTIVE_KINDS`）。

    通知面板和写作助手的「通知」工具（`notices.NoticeReader`）读的是同一份筛法——
    `new_character` 那一档为什么不在，见模块 docstring 最后一条。
    """
    return [
        record for record in proposals.pending(project_id) if record.kind in _ACTIVE_KINDS
    ]


def pending_proposal_notifications(
    proposals: SqliteProposalStore, project_id: str
) -> list[SystemNotification]:
    """项目全量（不按章）的 PENDING 提案，现读现拼成通知形状。

    不按章：通知面板本来就是项目级的（`GET .../notifications` 不吃 `chapter`
    参数），提案原来锁死在「当前打开的那一章」里，翻章之后早先攒下的提案就
    没地方看得见——搬进通知顺手把这个洞也补了，不是range 扩大的副作用，是
    这次搬家应该带来的效果。`proposals.pending(project_id)` 不传 `chapter_number`
    就是查全项目，这条能力早就在（`graph/sqlite_proposals.py::pending`）。
    """
    out: list[SystemNotification] = []
    for record in pending_active_proposals(proposals, project_id):
        out.append(
            SystemNotification(
                id=record.id,
                project_id=project_id,
                kind=_KIND_MAP[record.kind],  # type: ignore[arg-type]
                status="OPEN",
                subject_type="proposal",
                subject_id=record.id,
                chapter_number=record.chapter_number,
                title_code=_title_code(record),
                title_params={},
                jump=None,
                actions=_actions(record),
                created_at=record.created_at,
            )
        )
    return out
