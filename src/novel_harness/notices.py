"""「通知」那一栏的**只读**端口（2026-09-12，给写作助手用）。

右栏的「通知」是两个来源合成的一条流（`api/notifications.py::_merged_open_notifications`）：
`system_notification` 表里的 OPEN 行，加上现读现拼的待确认提案。写作助手要读同一栏，
但它那一层（`agent/`）**没有连接、不许碰 `Node`**（ADR 0019 边界一），所以这儿把两个来源
各自包成一个方法交出去——**筛法和面板是同一份**（`list_open_notifications` /
`pending_active_proposals`），没有第二套「哪些算通知」的判据。

提案带着 `node_refs`（`hydrate_proposal_names`，id → 显示名）：写作助手要把
「贾环 在 荣国府 → 贾环 在 大理寺」这种对照说给作者听，而 `items` 里只有裸 id。
"""

from __future__ import annotations

from .db import Connection
from .events.models import ProposalRecord
from .extract.proposals import hydrate_proposal_names
from .graph import StoryGraph
from .graph.sqlite_review import SqliteEdgeReviewStore
from .graph.sqlite_proposals import SqliteProposalStore
from .proposal_notifications import pending_active_proposals
from .system_notifications import SystemNotification, list_open_notifications


class NoticeReader:
    """两个来源、两个方法，**没有写方法**：忽略 / 处理通知仍然只有面板那条路。"""

    def __init__(self, conn: Connection, store: StoryGraph) -> None:
        self._conn = conn
        self._store = store

    def open_notices(self, project_id: str) -> list[SystemNotification]:
        """`system_notification` 表里 OPEN 的那些，按时间升序（面板同一份读法）。"""
        return list_open_notifications(self._conn, project_id)

    def pending_proposals(self, project_id: str) -> list[ProposalRecord]:
        """待确认的提案（项目全量，不按章），`node_refs` 已经补好。"""
        records = pending_active_proposals(SqliteProposalStore(self._conn), project_id)
        review_store = SqliteEdgeReviewStore(self._conn, self._store)
        return list(hydrate_proposal_names(review_store, project_id, records))
