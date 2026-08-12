"""候选稿的存取 —— **起草的产物落在这儿，不落在书里，也不落在对话里**（ADR 0022）。

规格书是 [`docs/adr/0022-drafting-is-a-proposal-not-a-write.md`](../../../docs/adr/0022-drafting-is-a-proposal-not-a-write.md)，
表在 `migrations/007_draft_candidate.sql`（那份文件头写着每一列为什么长这样）。

── 这一层只回答三个问题 ──────────────────────────────────────────────────

1. **收一稿**（`put`）—— 生成完就放进来，**不动书**。
2. **按 id 拿回一稿**（`get`）—— 只在作者要合并两版时才会被调用（ADR 0022 的「代价」）。
3. **它进书了没有**（`mark_landed`）—— 落盘之后回来记一笔，好让列表分得开。

── 为什么对话里只放 id + 定长预览 + 那一稿的自述 ──────────────────────────

跟模型说话的接口是**无状态**的：每一轮把整个消息数组从头重发，而工具返回也是消息。
三稿 ≈ 9,000 字从生成那一刻起**每一轮都在被重发**，直到有人主动拿掉——而默认没有任何
东西会拿掉它。界面上折叠、划掉、关标签页都不改变发出去的那份数组。

所以 `PREVIEW_UNITS` 是一个**写死的数**，不是一个「差不多够用」的默认值：
没有硬上限的话，一份 500 字预览 × 3 就把省下的吃回去一半（ADR 0022 的「代价」第四条）。
`tests/test_draft_candidates.py` 拿一稿三千字的正文钉住它。

── 线程安全：碰这条连接的每一句都要排队 ──────────────────────────────────

一批稿可以并发（起草没有副作用了，ADR 0022），于是 `put()` 会**从几个工作线程同时被调**，
而它们共用装配层那一条连接。**`check_same_thread=False` 不让连接变成线程安全的**：
`sqlite3` 的预备语句缓存是按连接的，两条线程同时执行同一句 SQL 会拿到同一个 statement，
当场 `InterfaceError: bad parameter or other API misuse`——实测过（4 线程 × 同一句
SELECT，几百次之内必炸），不是理论风险。

所以锁罩的是**这一层的每一次访问**，不只是写。而且它**必须和别处共用同一把**：
并发窗口里碰库的不止这张表（算约束、查记忆走的是同一条连接），两把锁 = 各排各的队 = 没排。
装配层负责把同一把交下来（`api/chat.py` → `ToolContext.db_lock` + `ChapterDesk`）。

loop 那条线程不需要参与排队：批执行器**等整个并发窗口跑完**才回到串行处理
（落库、记账、落盘全在那之后），所以工作线程和 loop 线程永远不会同时碰库。
**这条前提变了，这里就不够了。**
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..draft.length import DraftLanguage, count_units
from ..ids import EntityType, new_id

PREVIEW_UNITS: Final = 120
"""**预览有多长，写死在这儿。**

它的活儿只有一个：让模型（和作者）认出这是哪一稿——开头一段就够了。
往大了调之前先算一遍：这个数 × 一批的稿数 × **剩下每一轮**，那才是它真实的价格。
ADR 0022 点名了这条：「没有硬上限的话，一份 500 字预览 × 3 就把省下的吃回去一半」。

截的口径是 code point（`preview_of` 直接切片），而 `count_units` 中文数的是**非空白**
code point——所以按预算那把尺量，一份预览只会比这个数更少，不会更多。
"""

PREVIEW_ELLIPSIS: Final = "……"
"""截断的记号。**必须有**：一段被无声截断的预览读起来就是「他只写了这么多」。"""

KEEP_LANDED: Final = 20
"""清理策略：每本书**已经进过书**的候选，只留最近这么多份。

ADR 0022 承认候选会堆积，同时钉了一句「**不许在作者还可能回头看的时候清**」。
所以判据不是「旧」，是「**它已经有归宿了**」——落过盘的那一稿正文在磁盘上、在
`chapter_snapshot` 里（内容寻址），版本抽屉里退得回去；这一行删掉不丢任何东西。
**没落过盘的一行都不删**，多久都不删：那些才是作者可能回头挑的。
"""


def preview_of(body: str) -> str:
    """一稿的定长预览。**唯一实现**（模型看见的和界面看见的必须是同一段字）。"""
    head = body.strip()
    if len(head) <= PREVIEW_UNITS:
        return head
    return head[:PREVIEW_UNITS] + PREVIEW_ELLIPSIS


class DraftCandidate(BaseModel):
    """一稿的**摘要行**：认得出是哪一稿，但不带正文。

    这是进对话、进列表页的那一份。**正文不在这儿**——要正文得按 id 单取一次
    （`DraftCandidateStore.get`），而那一次是模型显式决定的（ADR 0022：只在作者要
    合并两版时才会被调用），不是每一轮白送的。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """**不上屏。** 它是 `draft:01J…` 这种形状，摆到小说作者脸上就是屏幕守卫要咬的那一种
    （`frontend/src/test/screenGuard.ts` 的第三张网认的就是 `前缀:标识`）。
    作者认得的说法是 `ordinal`（「第 2 稿」）。"""

    chapter: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    """这一章的第几稿。**作者和模型讲话时用它**，不用 id。"""

    units: int = Field(ge=0)
    note: str = ""
    """写它的那个模型自己那句话。**空 = 它这次没说**，这一层不替它编（ADR 0005）。"""

    preview: str = ""
    created_at: str = ""
    landed: bool = False
    """它进过书没有。**不是「被选中」**：作者可以在版本历史里把它退回去。"""

    stopped_reason: str = ""
    """**空 = 它写完了**；非空 = 没写完，这句话说明为什么（`migrations/010`）。

    ── 它不是元数据，是语义的一部分 ──────────────────────────────────────────

    对作者，显然。**真正要命的是对模型那一半**：一段断在半句的正文，模型下次读到它、
    若不知道那是被砍断的，**会把那个断口当成一种有意的写法去模仿**。
    「他缓缓抬起手，然后——」在代码里一眼就是坏的，在小说里它读起来像一个刻意的悬停。
    所以**它跟着这一行的每一个读端走**：摘要行（列表 / 对话回执）、全文（`read_draft`）、
    界面上那张卡，一个都不许漏。

    ── 为什么存的是一句中文，不是一个枚举码 ────────────────────────────────

    它同时要给**模型**和**作者**看，而这两个读者读的是同一种话。存机器码就要在两个出口
    各写一次映射，而那是两份会漂的措辞；更糟的是漏掉一处的形态——
    屏幕上一个 `author_stopped`（屏幕守卫罩不住小写裸枚举值，那个洞是写在判据文件里的）。
    库里那一列**不参与任何过滤分支**（迁移 010 那句「开放字符串，不 CHECK」），
    所以它没有第二个身份要守。
    """


class StoredDraft(DraftCandidate):
    """一稿的全文。**只有按 id 单取才拿得到。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str = ""
    """一稿正文，**不含章标题**（那一行是切章的锚，属于作者）。"""

    base_sha256: str | None = Field(default=None, exclude=True)
    """起草那一刻磁盘上那一章的哈希 —— **落盘时那道乐观闸比对的就是它**（ADR 0021）。

    `exclude=True`：它一个字都不进对话、不进出参。它是机器码（一段 sha256），
    对模型没有用、对作者是噪声，而**收窄的最强形态是根本没发出去**。
    """


def _row_kwargs(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "chapter": int(row["chapter_number"]),
        "ordinal": int(row["ordinal"]),
        "units": int(row["units"]),
        "note": str(row["note"]),
        "created_at": str(row["created_at"]),
        "landed": row["landed_at"] is not None,
        # **库里的 `NULL` 就是「它写完了」**（迁移 010 那一行注释），迁移之前的旧行
        # 也落在这一档上——那时还没有任何东西能把一稿砍断，所以那句话对它们是真的。
        "stopped_reason": "" if row["stopped_reason"] is None else str(row["stopped_reason"]),
    }


class DraftCandidateStore:
    """`draft_candidate` 的读写。**收一条连接，自己不开连接**（同会话表那一层）。

    **它不进 `ToolContext`**：那个 dataclass 上没有 conn 是边界一的一部分。
    工具够得到候选是因为装配层把一个握着它的闭包注入了进来（`agent/drafting.py`），
    而那个闭包只交出 Pydantic 出参。
    """

    def __init__(
        self, conn: Connection, *, lock: AbstractContextManager[Any] | None = None
    ) -> None:
        self._conn = conn
        self._lock: AbstractContextManager[Any] = lock or threading.RLock()
        """碰这条连接要排的那道队（见模块 docstring）。

        **装配层要把同一把锁交给这一层和起草那一层**（`api/chat.py`）：并发窗口里
        碰库的不止这张表——算约束、查记忆走的是同一条连接，而两条线程同时用一条
        SQLite 连接是 `InterfaceError`（`agent/ports.py::ToolContext.db_lock` 记着实测）。
        两把锁 = 各排各的队 = 没排。不给就自己造一把，那是「这一层单独用」的形态。"""

    # ── 写 ────────────────────────────────────────────────────────────────

    def put(
        self,
        project_id: str,
        *,
        chapter: int,
        body: str,
        note: str = "",
        base_sha256: str | None = None,
        stopped_reason: str = "",
    ) -> DraftCandidate:
        """收一稿。**不动书、不动对话**，只多这一行。

        `ordinal` 在同一个事务里取 `MAX+1`，所以并发的三稿拿到的是 1/2/3，不会撞号。

        Args:
            stopped_reason: 这一稿**没写完**时说明为什么（见 `DraftCandidate.stopped_reason`）。
                默认空 = 它写完了。**半截的一稿照旧收**：按停那一刻已经生成的 token 是
                付过钱的信息，扔掉 = 钱花了字没了；而 ADR 0022 之后一稿本来就只是「提议」，
                半截只是**短一点的提议**，它不会自动进书。
        """
        candidate_id = new_id(EntityType.DRAFT_CANDIDATE, project_id)
        units = count_units(body, DraftLanguage.ZH)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) AS top FROM draft_candidate"
                    " WHERE project_id = ? AND chapter_number = ?",
                    (project_id, chapter),
                ).fetchone()
                ordinal = int(row["top"]) + 1
                self._conn.execute(
                    "INSERT INTO draft_candidate"
                    " (id, project_id, chapter_number, ordinal, body, note, units, base_sha256,"
                    "  stopped_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate_id,
                        project_id,
                        chapter,
                        ordinal,
                        body,
                        note,
                        units,
                        base_sha256,
                        # **空串写成 `NULL`**：库里那一列的两态是「NULL = 写完了」，
                        # 一行空串会变成第三态，而它和 NULL 长得一模一样、意思却要靠
                        # 读端去猜（`_row_kwargs` 只认 NULL）。
                        stopped_reason or None,
                    ),
                )
                # `created_at` 是列默认值（库里那只钟），**读回来而不是在这儿再算一次**：
                # 两只钟迟早对不上，而列表页正是按它排序的。
                written = self._conn.execute(
                    "SELECT created_at FROM draft_candidate WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()
        return DraftCandidate(
            id=candidate_id,
            chapter=chapter,
            ordinal=ordinal,
            units=units,
            note=note,
            preview=preview_of(body),
            created_at=str(written["created_at"]),
            landed=False,
            stopped_reason=stopped_reason,
        )

    def mark_landed(self, project_id: str, candidate_id: str) -> None:
        """记下「这一稿进书了」。

        **落盘成功之后才调**，而且它失败不许被吞——同 `_log_landing` 那条：
        书被改了而记录没跟上，是把 ADR 0021 那笔交易赖掉一半。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE draft_candidate"
                    " SET landed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                    " WHERE project_id = ? AND id = ?",
                    (project_id, candidate_id),
                )
                self._prune_landed(project_id)
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()

    def _prune_landed(self, project_id: str) -> None:
        """清理：**只清已经有归宿的那些**（见 `KEEP_LANDED`）。没落过盘的一行都不动。"""
        self._conn.execute(
            "DELETE FROM draft_candidate WHERE project_id = ? AND landed_at IS NOT NULL"
            " AND id NOT IN ("
            "   SELECT id FROM draft_candidate WHERE project_id = ? AND landed_at IS NOT NULL"
            "   ORDER BY landed_at DESC, id DESC LIMIT ?"
            ")",
            (project_id, project_id, KEEP_LANDED),
        )

    # ── 读 ────────────────────────────────────────────────────────────────

    def get(self, project_id: str, candidate_id: str) -> StoredDraft | None:
        """按 id 取一稿的**全文**。`None` = 这本书里没有这一稿。"""
        with self._lock:
            row = self._read_one(project_id, candidate_id)
        if row is None:
            return None
        body = str(row["body"])
        return StoredDraft(
            **_row_kwargs(row),
            preview=preview_of(body),
            body=body,
            base_sha256=None if row["base_sha256"] is None else str(row["base_sha256"]),
        )

    def _read_one(self, project_id: str, candidate_id: str) -> Any:
        return self._conn.execute(
            "SELECT id, chapter_number, ordinal, body, note, units, base_sha256,"
            "       created_at, landed_at, stopped_reason"
            " FROM draft_candidate WHERE project_id = ? AND id = ?",
            (project_id, candidate_id),
        ).fetchone()

    def recent(
        self, project_id: str, *, chapter: int | None = None, limit: int = 20
    ) -> list[DraftCandidate]:
        """最近这几稿的摘要行（**不带正文**）。`chapter` 给了就只看那一章。"""
        sql = (
            "SELECT id, chapter_number, ordinal, body, note, units, created_at, landed_at,"
            "       stopped_reason"
            " FROM draft_candidate WHERE project_id = ?"
        )
        params: list[Any] = [project_id]
        if chapter is not None:
            sql += " AND chapter_number = ?"
            params.append(chapter)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [
            DraftCandidate(**_row_kwargs(row), preview=preview_of(str(row["body"])))
            for row in rows
        ]


__all__ = [
    "KEEP_LANDED",
    "PREVIEW_ELLIPSIS",
    "PREVIEW_UNITS",
    "DraftCandidate",
    "DraftCandidateStore",
    "StoredDraft",
    "preview_of",
]
