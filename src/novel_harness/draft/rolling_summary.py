"""滚动总结：幂等的后台章节摘要生成、作者的改与撤回、确定性读取。

摘要不是作者确认的事实（不进 decision_log），进写作 prompt 时明确标注
「机器摘要，仅背景」。同一章同一 prompt 只付一次调用，由 DB 唯一键兜底。

**作者改得动它，而且改的痕迹留着**（迁移 013）：改 = 追加一行作者版，
撤回 = 追加一行标记撤回，模型写的那一行原样留在库里。
「这一章现在的总结」= 最新那一行；它 RETRACTED 就当这一章没有。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
from time import perf_counter
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..extract.call_audit import record_call
from ..extract.control import AuditedCompletion
from ..graph import ChapterText
from ..ids import EntityType, new_id
from .provider import CompletionResult
from .summarize import SUMMARY_VERSION, SummaryMessage, build_summary_messages


ROLLING_WINDOW: Final = 30
"""写第 X 章时最多带上的旧章节摘要数（只覆盖「近八章事件窗口」之前的章节）。"""

AUTHOR_VERSION: Final = "chapter-summary-author-v1"
"""作者自己写的那一行的 `schema_version`。

**不复用 `SUMMARY_VERSION`**：那一列回答的是「这段字是哪一版 prompt 产出的」，
而作者的字不是任何 prompt 产出的。混着装的话，换一版总结 prompt 会把作者手写的那些
一起判成「旧版本」。"""

RETRACTION_VERSION: Final = "chapter-summary-retraction-v1"
"""「撤回」那一行的 `schema_version`。同上：它也不是 prompt 产出的。"""

EMPTY_SUMMARY_HASH: Final = sha256(b"").hexdigest()
"""RETRACTED tombstone 的统一 `summary_sha256`（§4.3：sha256(空字节)）。

ACTIVE 行要求非空 summary 且 hash == 正文 hash；RETRACTED 行要求 summary 为 NULL
且 hash == 这个常量。这样撤回本身可追溯，而起草那一侧确定地当这一章没有总结
（**没有「用原文顶上」那条兜底**：它 2026-08-22 删了，一章原文 3,000–5,000 字、
一段总结 120 字，顶替一次就吃掉三十章的额度，量完全不可控）。"""

AUTHOR_SUMMARY_MAX_CHARS: Final = 1_000
"""作者手写一段总结的上限。

模型那边是 120 字（`SUMMARY_MAX_CHARS`，写死在 prompt 里），作者不受那条约束——
这是他的记忆，不是模型的作业。但**不能不设上限**：滚动总结整块有预算
（`MemoryBudget.rolling_summaries`），撑破了 `_take_from_newest` 会从最旧的开始丢，
于是「往这一章贴一整章正文」的代价是**更早那几章的总结静默地掉出 prompt**。"""


class SummaryOrigin(StrEnum):
    """这段字是谁写的。"""

    MODEL = "model"
    AUTHOR = "author"


class SummaryState(StrEnum):
    """这一行算不算数。

    `RETRACTED` 的含义和 `EdgeStatus.RETRACTED` 一致：**这一行从此不作数**，
    但它还在库里（005 那条纪律：作者改一条事实，不该有一行凭空消失）。
    """

    ACTIVE = "ACTIVE"
    RETRACTED = "RETRACTED"


class SummaryChapterNotFound(LookupError):
    """请求的章节没有当前不可变快照。"""


class SummaryGenerationError(RuntimeError):
    """总结器返回了空文本。"""


class SummaryTextRejected(ValueError):
    """作者交上来的那段字收不下（空的 / 太长）。`str(exc)` 是给作者看的那句话。"""


class SummaryEditConflict(RuntimeError):
    """PATCH 的 `expected_version_id` 与当前 head 不一致（作者/机器已先行改动）。

    HTTP 映射成 409：界面保留作者输入并重取当前事实，不能靠覆盖解决。
    """


class ChapterSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    chapter_id: str
    chapter_number: int = Field(ge=1)
    summary: str | None = None
    summary_sha256: str
    source_snapshot_id: str | None = None
    replaces_summary_id: str | None = None
    schema_version: str
    prompt_hash: str
    model_call_id: str | None = None
    created_at: str
    source: SummaryOrigin = SummaryOrigin.MODEL
    status: SummaryState = SummaryState.ACTIVE

    @property
    def is_retraction_tombstone(self) -> bool:
        """这一行是不是「撤回」tombstone（summary 为空、hash 是统一空字节 hash）。"""
        return self.status is SummaryState.RETRACTED


class ChapterSummaryStatus(BaseModel):
    """一章在滚动总结上的状态。**几种「没有」必须分得开**（ARCHITECTURE §10 约束 8）。

    ``has_text=False`` = 这一章还没有正文快照，压根没得总结；
    ``has_text=True`` 且 ``summary is None`` = 有正文、**没生成过**（要花钱，只由作者显式触发）；
    两者在界面上长成同一个「- 暂无」，作者就永远不知道自己少喂了什么给写作模型——
    而那正是 2026-08-06 盘点出来的病（`chapter_summary` 表恒空且没有任何东西提示）。

    ``retracted=True`` 是 2026-08-13 长出来的**第三种零**：有正文、生成过、
    **作者亲手撤回了**。它和「还没生成」在起草那一侧完全同义（两种都不进 prompt），
    可在屏幕上不是一回事——前者的下一步是「去生成一份」，后者的下一步是他自己刚做的。
    催他去补一件他刚撤掉的事，是这块面板最容易说出口的那句假话。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    has_text: bool
    summary: str | None = None
    created_at: str | None = None
    retracted: bool = False
    """最新那一行是不是「撤回」。`summary` 为 None 时才有意义。"""

    author_written: bool = False
    """现在这一段是不是作者自己写的（而不是模型写的）。

    **它不是装饰**：作者手写的那段不带「机器摘要，未经确认」的免责，而屏幕上
    分不出来的话，他会对着自己写的字读到一句「这是机器压缩的，别当事实」。"""

    version_id: str | None = None
    """head 指向的版本行 id（019）。第一次生成前为 NULL。"""

    summary_sha256: str | None = None
    replaces_version_id: str | None = None
    version_source: str | None = None
    """`model` / `author` / `legacy`（019 迁移后旧行可能是 legacy）。"""


class SummarySnapshotWatermark(BaseModel):
    """一章的当前快照水位：给摘要新鲜度判据用（`calibration/freshness.py`）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    text_sha256: str
    snapshot_created_at: str


class SummaryStore:
    """chapter_summary 的只读仓储。

    **「这一章现在的总结」= 最新那一行**（rowid 最大），而不是「有没有行」。
    迁移 013 之后同一章可以有好几行（模型写的 → 作者改的 → 撤回的），
    只看「存不存在」的判据会把作者撤掉的那一份重新喂进 prompt。
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, project_id: str, chapter_number: int) -> ChapterSummary | None:
        """这一章**现在算数**的那一条。撤回过 = None（当这一章没总结）。"""
        row = self.latest(project_id, chapter_number)
        return None if row is None or row.status is SummaryState.RETRACTED else row

    def latest(self, project_id: str, chapter_number: int) -> ChapterSummary | None:
        """最新那一行，**撤回的也算**。

        和 `get()` 的差别只在一处，而那一处是钱：**判「这一章要不要自动派活」的那一层
        必须看得见撤回**。用 `get()` 的话，作者亲手撤掉的那一章会在他下一次保存时被
        自动重新生成——**一次他没按过的付费调用，顺带把他刚做的动作抹掉**。

        今天那个判断在 `chapter_refresh._head_missing`（它直接问 head 在不在，等价于
        「`latest()` 有没有值」），由
        `test_a_retracted_summary_is_not_bought_back_by_the_next_save` 钉住。
        2026-08-20 之前实现这条的是 `api/autopilot.py`（换章派活那条路），它随换章
        autopilot 一起删了——**纪律留着，实现换了地方**。
        """
        row = self._conn.execute(
            """
            SELECT s.id, s.project_id, c.id AS chapter_id, c.number AS chapter_number,
                   s.summary, s.summary_sha256, s.source_snapshot_id,
                   s.replaces_summary_id, s.schema_version, s.prompt_hash,
                   s.model_call_id, s.created_at, s.source, s.status
            FROM chapter c
            JOIN chapter_summary_head h ON h.chapter_id = c.id
            JOIN chapter_summary s ON s.id = h.current_summary_id
            WHERE c.project_id = ? AND c.number = ?
            """,
            (project_id, chapter_number),
        ).fetchone()
        return None if row is None else _row_to_summary(row)

    def for_range(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummary]:
        """闭区间内每章**现在算数**的那一条摘要，按章号升序。撤回过的章不出现。

        这是进起草 prompt 的那一份（`draft/product_context.py`）——所以撤回在这儿
        兑现成「这一章当作没总结」。
        """
        return [
            row
            for row in self._latest_per_chapter(project_id, first_chapter, last_chapter)
            if row.status is SummaryState.ACTIVE
        ]

    def active(self, project_id: str) -> list[ChapterSummary]:
        """全书**现在算数**的那些摘要，按章号升序。撤回过的章不在里面。

        和 `for_range` 是同一条判据、同一份 SQL，只是不给区间——倒排索引
        （`summary_index.py`）要的正是「哪几行现在算数」，而它不该自己去猜书有多长。

        **上界不能拍一个常数。** 章号是全书顺序位置，没有上限；写死一个
        `10**9` 之类的哨兵在这里能跑，但下一个人会照抄它去别的地方。
        """
        row = self._conn.execute(
            "SELECT MAX(chapter_number) AS last FROM chapter_summary WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        last = row["last"]
        return [] if last is None else self.for_range(project_id, 1, int(last))

    def _latest_per_chapter(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummary]:
        """闭区间内每章最新的一行（**撤回的也在里面**），按章号升序。

        `for_range` 和 `coverage` 共用它。**两份各写一遍 SQL 是这个仓库反复修的病**：
        判据一旦漂开，起草带进去的那几章和界面上说「有」的那几章就不是同一批。
        """
        rows = self._conn.execute(
            """
            SELECT s.id, s.project_id, c.id AS chapter_id, c.number AS chapter_number,
                   s.summary, s.summary_sha256, s.source_snapshot_id,
                   s.replaces_summary_id, s.schema_version, s.prompt_hash,
                   s.model_call_id, s.created_at, s.source, s.status
            FROM chapter c
            JOIN chapter_summary_head h ON h.chapter_id = c.id
            JOIN chapter_summary s ON s.id = h.current_summary_id
            WHERE c.project_id = ? AND c.number BETWEEN ? AND ?
            ORDER BY c.number
            """,
            (project_id, first_chapter, last_chapter),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def coverage(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummaryStatus]:
        """闭区间内**每一章**的总结状态，按章号升序。

        和 ``for_range`` 的差别就是这个模块存在的理由：``for_range`` 只返回有的那些，
        「缺哪几章」得靠调用方自己拿区间去减——而没人会记得减。这里把缺的那些也物化出来，
        且分得开「没写」和「写了没总结」（见 ``ChapterSummaryStatus``）。

        ``last_chapter < first_chapter`` 返回空表而不是报错：写第 3 章时滚动总结窗口
        本来就是空的（近八章走事件记忆），那是**正常态**，不是作者输错了。

        ── ⚠️ 它只问「有没有」，不问「新不新」────────────────────────────────
        改过正文的章，只要还有一条生效的总结就判**有**——即使那段字描述的是一版
        已经不存在的正文。这是既有行为，不是疏漏：判据换成「新不新」等于给每一次
        正文改动都挂上一次付费重生成。**摘要过期比缺摘要更坏**（缺是瞎，过期是
        说错，而模型手里只有那一段字），所以要用这个答案的上层得自己核：
        `agent/index.py` 拿磁盘 mtime 再对一遍（`_rewritten_since`），核不了时用
        `stale_checked` 说「我没查」。要改这条判据，连那一层一起改。
        （这段话 2026-08-20 之前住在 `api/autopilot.py` 的模块头里，随那个模块删掉时
        搬到这儿——它描述的是**本函数**的限制，本来就该住在这儿。）
        """
        if first_chapter < 1:
            raise ValueError("章号区间的下界至少是 1")
        if last_chapter < first_chapter:
            return []
        rows = self._conn.execute(
            """
            SELECT chapter.number AS number
            FROM chapter
            JOIN chapter_snapshot
              ON chapter_snapshot.chapter_id = chapter.id
             AND chapter_snapshot.text_sha256 = chapter.text_sha256
            WHERE chapter.project_id = ? AND chapter.number BETWEEN ? AND ?
            """,
            (project_id, first_chapter, last_chapter),
        ).fetchall()
        # 判据和 `RollingSummarizer._chapter` 是同一条（当前快照存在才总结得了），
        # 否则界面会请作者去生成一份引擎当场会拒的东西。
        with_text = {int(row["number"]) for row in rows}
        latest = {
            row.chapter_number: row
            for row in self._latest_per_chapter(project_id, first_chapter, last_chapter)
        }
        out: list[ChapterSummaryStatus] = []
        for number in range(first_chapter, last_chapter + 1):
            row = latest.get(number)
            # 撤回过的那一章：`summary` 是 None（起草那边也确实拿不到它），
            # 但**多带一个 `retracted`**，好让界面说得出「是你撤掉的」而不是「还没生成」。
            live = row is not None and row.status is SummaryState.ACTIVE
            out.append(
                ChapterSummaryStatus(
                    chapter_number=number,
                    has_text=number in with_text,
                    summary=row.summary if live else None,
                    created_at=row.created_at if live else None,
                    retracted=row is not None and row.status is SummaryState.RETRACTED,
                    author_written=live and row.source is SummaryOrigin.AUTHOR,
                    version_id=row.id if row is not None else None,
                    summary_sha256=row.summary_sha256 if live else None,
                    replaces_version_id=row.replaces_summary_id if live else None,
                    version_source=(
                        str(row.source.value) if row is not None else None
                    ),
                )
            )
        return out

    def snapshot_watermark(
        self,
        project_id: str,
        chapter_number: int,
    ) -> SummarySnapshotWatermark | None:
        """当前快照水位：`chapter.text_sha256` 指向的那行 `chapter_snapshot`。

        `None` = 没有当前快照行（正文从未同步过）⇒ 新鲜度只能判 `UNVERIFIED`。
        判据和 `coverage` 的 `with_text` 是同一条（当前快照存在才总结得了），
        不许在别处写第二份。
        """
        row = self._conn.execute(
            """
            SELECT snapshot.text_sha256 AS sha, snapshot.created_at AS created
            FROM chapter_snapshot AS snapshot
            JOIN chapter ON chapter.id = snapshot.chapter_id
            WHERE chapter.project_id = ?
              AND chapter.number = ?
              AND snapshot.text_sha256 = chapter.text_sha256
            """,
            (project_id, chapter_number),
        ).fetchone()
        if row is None:
            return None
        return SummarySnapshotWatermark(
            chapter_number=chapter_number,
            text_sha256=str(row["sha"]),
            snapshot_created_at=str(row["created"]),
        )


def _row_to_summary(row: Any) -> ChapterSummary:
    return ChapterSummary(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        chapter_id=str(row["chapter_id"]),
        chapter_number=int(row["chapter_number"]),
        summary=None if row["summary"] is None else str(row["summary"]),
        summary_sha256=str(row["summary_sha256"]),
        source_snapshot_id=(
            None if row["source_snapshot_id"] is None else str(row["source_snapshot_id"])
        ),
        replaces_summary_id=(
            None if row["replaces_summary_id"] is None else str(row["replaces_summary_id"])
        ),
        schema_version=str(row["schema_version"]),
        prompt_hash=str(row["prompt_hash"]),
        model_call_id=(
            None if row["model_call_id"] is None else str(row["model_call_id"])
        ),
        created_at=str(row["created_at"]),
        source=SummaryOrigin(str(row["source"])),
        status=SummaryState(str(row["status"])),
    )


def _default_summary_id(project_id: str) -> str:
    return new_id(EntityType.SUMMARY, project_id)


def _default_call_id(project_id: str) -> str:
    return new_id(EntityType.CALL, project_id)


# ══════════════════════════════════════════════════════════════════════════
# 内容地址：`prompt_hash` 那一列装的是什么（迁移 013 之后）
# ══════════════════════════════════════════════════════════════════════════


def _address(payload_hash: str, revision: int) -> str:
    """这一行在唯一键上的地址。**位置也是内容的一部分。**

    `revision` = 插进去之前这一章已经有几行。`revision == 0` 时**原样返回**，
    因为 004 到 013 之间写下的那些行就是这么存的（`sha256(消息序列)`）——
    改一个字节，作者已有的库里每一章都会被判成「没生成过」，然后重新付一遍钱。

    ── 为什么不能只哈希内容 ──────────────────────────────────────────────
    唯一键是 `(project, chapter, schema_version, prompt_hash)`。只哈希内容的话：

    * 作者「改成 A → 改成 B → 改回 A」，第三步撞上第一步那一行的键。而库里那一行是
      **旧的**，最新一行仍然是 B —— 屏幕上他刚做的那次修改什么都没发生，且不报错。
    * 「撤回之后再点重新生成」：同一章正文 = 同一份 prompt = 同一个哈希，同样撞键。
      而那正是撤回语义里写死的那条退路（「想重来就再点生成」）。

    两种都是**看起来完全正常的假页面**，也是这个仓库反复在修的那一类。
    """
    if revision <= 0:
        return payload_hash
    return sha256(f"{payload_hash}|rev{revision}".encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _revision(conn: Connection, project_id: str, chapter_number: int) -> int:
    """这一章现在有几行（模型的 + 作者的 + 撤回的，全算）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chapter_summary"
        " WHERE project_id = ? AND chapter_number = ?",
        (project_id, chapter_number),
    ).fetchone()
    return int(row["n"])


def _chapter_identity(
    conn: Connection, project_id: str, chapter_number: int
) -> tuple[str, str] | None:
    """当前正文的 `(chapter_id, snapshot_id)`；没进库 → None。

    判据与 `RollingSummarizer._chapter` 是同一条（当前快照存在才总结得了）。
    """
    row = conn.execute(
        """
        SELECT c.id AS chapter_id, s.id AS snapshot_id
        FROM chapter c
        JOIN chapter_snapshot s
          ON s.chapter_id = c.id
         AND s.text_sha256 = c.text_sha256
        WHERE c.project_id = ? AND c.number = ?
        """,
        (project_id, chapter_number),
    ).fetchone()
    return (str(row["chapter_id"]), str(row["snapshot_id"])) if row is not None else None


def _snapshot_text_hash(conn: Connection, project_id: str, chapter_number: int) -> str:
    """当前快照的 `text_sha256`（§4.4：滚动总结的 `source_sha256` 用它，不发明第三种）。"""
    row = conn.execute(
        "SELECT text_sha256 FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, chapter_number),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"chapter {chapter_number} 没有当前快照，无法核对")
    return str(row["text_sha256"])


def _chapter_generation(conn: Connection, project_id: str, chapter_number: int) -> int:
    row = conn.execute(
        "SELECT snapshot_generation FROM chapter WHERE project_id = ? AND number = ?",
        (project_id, chapter_number),
    ).fetchone()
    return int(row["snapshot_generation"])


def _switch_head(
    conn: Connection,
    chapter_id: str,
    new_summary_id: str,
    *,
    expected_head: str | None,
) -> bool:
    """head CAS：只有 current 仍等于 expected 才切换（作者编辑先赢）。

    返回 False = CAS 失败（并发作者/机器已切走 head），调用方必须回滚。
    """
    row = conn.execute(
        """
        UPDATE chapter_summary_head
           SET current_summary_id = :new_id,
               machine_intent_seq = machine_intent_seq + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE chapter_id = :cid
           AND current_summary_id IS :expected
        RETURNING chapter_id
        """,
        {"new_id": new_summary_id, "cid": chapter_id, "expected": expected_head},
    ).fetchone()
    return row is not None


def _has_current_text(conn: Connection, project_id: str, chapter_number: int) -> bool:
    """这一章有没有**当前**快照。判据和 `RollingSummarizer._chapter` 是同一条。"""
    row = conn.execute(
        """
        SELECT 1 AS ok
        FROM chapter
        JOIN chapter_snapshot
          ON chapter_snapshot.chapter_id = chapter.id
         AND chapter_snapshot.text_sha256 = chapter.text_sha256
        WHERE chapter.project_id = ? AND chapter.number = ?
        """,
        (project_id, chapter_number),
    ).fetchone()
    return row is not None


def _insert_row(
    conn: Connection,
    *,
    row_id: str,
    project_id: str,
    chapter_id: str,
    chapter_number: int,
    summary: str | None,
    summary_sha256: str,
    schema_version: str,
    prompt_hash: str,
    source: SummaryOrigin,
    status: SummaryState,
    model_call_id: str | None = None,
    source_snapshot_id: str | None = None,
    replaces_summary_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            schema_version, prompt_hash, model_call_id, source, status,
            source_snapshot_id, replaces_summary_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            project_id,
            chapter_id,
            chapter_number,
            summary,
            summary_sha256,
            schema_version,
            prompt_hash,
            model_call_id,
            str(source),
            str(status),
            source_snapshot_id,
            replaces_summary_id,
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# 作者这一侧：改 / 撤回。**两个都不花钱，两个都不删行。**
# ══════════════════════════════════════════════════════════════════════════


def save_author_summary(
    conn: Connection,
    *,
    project_id: str,
    chapter_number: int,
    text: str,
    expected_version_id: str | None = None,
    expect_head: bool = False,
    summary_id_factory: Callable[[str], str] = _default_summary_id,
) -> ChapterSummary:
    """把这一章的总结换成作者自己写的这一段。**追加一行，模型那一行留着。**

    幂等的判据是「最新那一行说的就是这段字」——不是哈希撞上了。差别在
    「改成 A → 改成 B → 改回 A」那一档：按哈希判会认成重复而什么都不做，
    可最新一行仍然是 B，屏幕上作者刚做的事没发生（见 `_address`）。

    这一章还没有总结时也收：作者手写一份比让他先付一次钱再改要合理，
    而写下来的东西和生成出来再改的完全同形。**唯一的前置是这一章得有正文**
    ——没正文的章连「总结什么」都答不上来，和 `ensure` 同一条判据。
    """
    body = text.strip()
    if not body:
        raise SummaryTextRejected("这一段是空的。要清掉这一章的总结，用「撤回」。")
    if len(body) > AUTHOR_SUMMARY_MAX_CHARS:
        raise SummaryTextRejected(
            f"这一段太长了（{len(body)} 字，最多 {AUTHOR_SUMMARY_MAX_CHARS} 字）。"
            "这里是给写作模型看的背景，写得太长会把更早那几章的总结挤出去。"
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _has_current_text(conn, project_id, chapter_number):
            raise SummaryChapterNotFound(
                f"chapter {chapter_number} has no current snapshot in project {project_id}"
            )
        identity = _chapter_identity(conn, project_id, chapter_number)
        assert identity is not None
        chapter_id, snapshot_id = identity
        store = SummaryStore(conn)
        current = store.latest(project_id, chapter_number)
        if expect_head:
            actual_head = current.id if current is not None else None
            if expected_version_id != actual_head:
                raise SummaryEditConflict(
                    f"expected summary head {expected_version_id!r}, "
                    f"current head {actual_head!r}"
                )
        if (
            current is not None
            and current.status is SummaryState.ACTIVE
            and current.summary == body
        ):
            conn.commit()
            return current
        expected_head = current.id if current is not None else None
        revision = _revision(conn, project_id, chapter_number)
        new_id_value = summary_id_factory(project_id)
        _insert_row(
            conn,
            row_id=new_id_value,
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            summary=body,
            summary_sha256=_text_hash(body),
            schema_version=AUTHOR_VERSION,
            prompt_hash=_address(_text_hash(body), revision),
            source=SummaryOrigin.AUTHOR,
            status=SummaryState.ACTIVE,
            source_snapshot_id=snapshot_id,
            replaces_summary_id=expected_head,
        )
        if not _switch_head(
            conn, chapter_id, new_id_value, expected_head=expected_head
        ):
            raise RuntimeError("author summary CAS failed: head moved concurrently")
        # 022 / Task 10：作者版总结立即生效，也要按当前正文核对（同一事务）。
        from ..summary_reconciliation import enqueue_reconciliation_outbox

        enqueue_reconciliation_outbox(
            conn,
            project_id=project_id,
            subject_type="chapter_summary",
            subject_id=chapter_id,
            chapter_number=chapter_number,
            checked_against_snapshot_id=snapshot_id,
            source_generation=_chapter_generation(conn, project_id, chapter_number),
            source_sha256=_snapshot_text_hash(conn, project_id, chapter_number),
            summary_sha256=_text_hash(body),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    saved = SummaryStore(conn).get(project_id, chapter_number)
    if saved is None:  # pragma: no cover - 刚提交的那一行读不回来只能是库坏了
        raise RuntimeError("author summary insert is unreadable")
    return saved


def retract_summary(
    conn: Connection,
    *,
    project_id: str,
    chapter_number: int,
    summary_id_factory: Callable[[str], str] = _default_summary_id,
) -> ChapterSummary | None:
    """撤回这一章的总结：**追加一行标记撤回**，被撤的那一行原样留着。

    语义定死为「这一章当作没总结」：起草时不带它、`coverage()` 里算作缺、
    想重来就再点生成。所以「删了重来」顺带覆盖，不用做两套。

    返回撤回那一行；本来就没有总结（或已经撤回过）时返回 `None` / 原样返回，
    **一行都不追加**——重复点不该在库里堆出一串意义相同的记录。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = SummaryStore(conn).latest(project_id, chapter_number)
        if current is None or current.status is SummaryState.RETRACTED:
            conn.commit()
            return current
        identity = _chapter_identity(conn, project_id, chapter_number)
        assert identity is not None
        chapter_id, snapshot_id = identity
        expected_head = current.id
        revision = _revision(conn, project_id, chapter_number)
        new_id_value = summary_id_factory(project_id)
        _insert_row(
            conn,
            row_id=new_id_value,
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            # 撤回 tombstone：正文为空、hash 是统一空字节 hash（§4.3）。
            summary=None,
            summary_sha256=EMPTY_SUMMARY_HASH,
            schema_version=RETRACTION_VERSION,
            prompt_hash=_address(current.prompt_hash, revision),
            source=SummaryOrigin.AUTHOR,
            status=SummaryState.RETRACTED,
            source_snapshot_id=snapshot_id,
            replaces_summary_id=expected_head,
        )
        if not _switch_head(
            conn, chapter_id, new_id_value, expected_head=expected_head
        ):
            raise RuntimeError("retraction CAS failed: head moved concurrently")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return SummaryStore(conn).latest(project_id, chapter_number)


def _messages_bytes(messages: list[SummaryMessage]) -> bytes:
    return json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """一次绑定 prompt 的总结请求；派生字段不能独立提供。"""

    chapter: ChapterText
    messages: tuple[SummaryMessage, ...] = field(init=False)
    prompt_bytes: bytes = field(init=False, repr=False)
    prompt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        messages = tuple(build_summary_messages(self.chapter.text))
        encoded = _messages_bytes(list(messages))
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "prompt_bytes", encoded)
        object.__setattr__(self, "prompt_hash", sha256(encoded).hexdigest())


class RollingSummarizer:
    """own 每条连接，靠 DB 唯一键保证「同一章同一 prompt 至多付一次」。"""

    def __init__(
        self,
        connection_factory: Callable[[], Connection],
        analyzer: Callable[[SummaryRequest], CompletionResult],
        *,
        summary_id_factory: Callable[[str], str] = _default_summary_id,
        call_id_factory: Callable[[str], str] = _default_call_id,
    ) -> None:
        self._connections = connection_factory
        self._analyzer = analyzer
        self._new_summary_id = summary_id_factory
        self._new_call_id = call_id_factory

    def ensure(self, project_id: str, chapter_number: int) -> ChapterSummary:
        """幂等：已有摘要直接返回；否则付费生成并落库。

        ── 幂等的判据是「**head 指向的那一行**是这份 prompt 产出的」────────────
        013 之前它是「这一章有没有一行的键等于这份 prompt」。作者能改、能撤回之后，
        那个判据会在两处静默说谎：撤回过的章（旧的那一行还在，于是「已经有了」，
        重新生成永远不发生）、作者改过的章（同上，他点重新生成拿回自己的字）。
        所以改成看 head 的**内容地址 + 状态**。

        探针认两个地址：`prompt_hash` 本身（013 之前写下的那些行）和
        `_address(prompt_hash, revision - 1)`（这一章后来又长过行的情况）。
        少认前一个，作者已有的库里每一章都会被判成没生成过，然后重新付一遍钱。

        018 之后机器结果走 job → result 审计 → CAS 切 head：CAS 失败只把
        job/result 标 SUPERSEDED，不产生一条假装生效过的 ACTIVE 版本。
        """
        conn = self._connections()
        try:
            chapter = self._chapter(conn, project_id, chapter_number)
            request = SummaryRequest(chapter)
            conn.execute("BEGIN IMMEDIATE")
            identity = _chapter_identity(conn, project_id, chapter_number)
            assert identity is not None
            chapter_id, snapshot_id = identity
            generation = _chapter_generation(conn, project_id, chapter_number)
            revision = _revision(conn, project_id, chapter_number)
            known = {request.prompt_hash, _address(request.prompt_hash, revision - 1)}
            current = SummaryStore(conn).latest(project_id, chapter_number)
            if (
                current is not None
                and current.status is SummaryState.ACTIVE
                and current.schema_version == SUMMARY_VERSION
                and current.prompt_hash in known
            ):
                conn.commit()
                return current
            expected_head = current.id if current is not None else None
            trigger_key = f"manual:{request.prompt_hash}"
            job_id = self._new_summary_id(project_id)
            conn.execute(
                """
                INSERT INTO summary_generation_job (
                    id, project_id, target_type, chapter_id, event_id,
                    source_snapshot_id, source_generation, source_sha256,
                    refresh_attempt_id, required_ruleset_epoch, required_ruleset_hash,
                    expected_head_version_id, required_machine_intent_seq,
                    trigger_key, trigger_source, status
                ) VALUES (?, ?, 'CHAPTER', ?, NULL, ?, ?, ?, NULL, NULL, NULL,
                          ?, NULL, ?, 'save', 'RUNNING')
                """,
                (
                    job_id,
                    project_id,
                    chapter_id,
                    snapshot_id,
                    generation,
                    request.prompt_hash,
                    expected_head,
                    trigger_key,
                ),
            )
            conn.commit()

            started = perf_counter()
            completion = self._analyzer(request)
            if not isinstance(completion, CompletionResult):
                raise TypeError("summarizer must return CompletionResult")
            audited = AuditedCompletion.from_result(completion)
            summary = audited.text.strip()
            if not summary:
                raise SummaryGenerationError("summarizer returned empty text")
            elapsed_ms = max(0, int((perf_counter() - started) * 1_000))

            try:
                call_id = record_call(
                    conn,
                    project_id=project_id,
                    capability="summarizer",
                    model=audited.model,
                    finish_reason=audited.finish_reason,
                    schema_version=SUMMARY_VERSION,
                    # **这儿装的是原始哈希，不是上面那个带 revision 的地址。**
                    # `model_call.prompt_hash` 回答的是「这段 prompt 跑过没有」，
                    # 而那个问题只由内容答得了（`ids.artifact_id` 同一条纪律）。
                    prompt_hash=request.prompt_hash,
                    prompt_bytes=request.prompt_bytes,
                    text=audited.text,
                    prompt_tokens=audited.prompt_tokens,
                    completion_tokens=audited.completion_tokens,
                    cache_read_tokens=audited.cache_read_tokens,
                    cache_write_tokens=audited.cache_write_tokens,
                    cost=audited.cost,
                    elapsed_ms=elapsed_ms,
                    # 总结的是这一章，账也记在这一章上。反查（`chapter_summary`）照旧
                    # 兜着旧行，两条路都留着——见 `009_call_chapter.sql`。
                    chapter_number=chapter_number,
                    call_id_factory=self._new_call_id,
                )
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                result_id = self._new_summary_id(project_id)
                conn.execute(
                    """
                    INSERT INTO summary_generation_result (
                        id, job_id, summary, summary_sha256, model_call_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result_id,
                        job_id,
                        summary,
                        _text_hash(summary),
                        call_id,
                    ),
                )
                version_id = self._new_summary_id(project_id)
                _insert_row(
                    conn,
                    row_id=version_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter_number=chapter_number,
                    summary=summary,
                    summary_sha256=_text_hash(summary),
                    schema_version=SUMMARY_VERSION,
                    prompt_hash=_address(request.prompt_hash, revision),
                    source=SummaryOrigin.MODEL,
                    status=SummaryState.ACTIVE,
                    model_call_id=call_id,
                    source_snapshot_id=snapshot_id,
                    replaces_summary_id=expected_head,
                )
                if not _switch_head(
                    conn, chapter_id, version_id, expected_head=expected_head
                ):
                    # 版本/result 一起回滚；job 的 SUPERSEDED 标记要**活下来**，
                    # 所以在回滚之后用新事务单独写（不能靠同一事务——它已经回滚了）。
                    conn.rollback()
                    conn.execute(
                        "UPDATE summary_generation_job SET status = 'SUPERSEDED' WHERE id = ?",
                        (job_id,),
                    )
                    conn.commit()
                    existing = self._read(conn, project_id, chapter_number)
                    if existing is not None:
                        return existing
                    raise RuntimeError("summary CAS failed and no existing head")
                conn.execute(
                    "UPDATE summary_generation_job SET status = 'SUCCEEDED' WHERE id = ?",
                    (job_id,),
                )
                # 022 / Task 10：head 切换与「要核对这条新总结」同一事务（不变量 9）。
                # 进程在这之后立即退出，重启后 dispatcher 仍能补核对。
                # `source_sha256` 口径只有一份（§4.4）：滚动总结用
                # `chapter_snapshot.text_sha256`，不用总结自己的 hash。
                from ..summary_reconciliation import enqueue_reconciliation_outbox

                enqueue_reconciliation_outbox(
                    conn,
                    project_id=project_id,
                    subject_type="chapter_summary",
                    subject_id=chapter_id,
                    chapter_number=chapter_number,
                    checked_against_snapshot_id=snapshot_id,
                    source_generation=_chapter_generation(conn, project_id, chapter_number),
                    source_sha256=_snapshot_text_hash(conn, project_id, chapter_number),
                    summary_sha256=_text_hash(summary),
                )
                conn.commit()
                created = self._read(conn, project_id, chapter_number)
                if created is None:
                    raise RuntimeError("summary insert is unreadable")
                return created
            except Exception:
                conn.rollback()
                existing = self._read(conn, project_id, chapter_number)
                if existing is not None:
                    # 并发另一路已写入：唯一键竞态的赢家，幂等返回。
                    return existing
                raise
        finally:
            conn.close()

    def _chapter(
        self,
        conn: Connection,
        project_id: str,
        chapter_number: int,
    ) -> ChapterText:
        row = conn.execute(
            """
            SELECT chapter_snapshot.chapter_id, chapter.number,
                   chapter_snapshot.id, chapter_snapshot.text
            FROM chapter
            JOIN chapter_snapshot
              ON chapter_snapshot.chapter_id = chapter.id
             AND chapter_snapshot.text_sha256 = chapter.text_sha256
            WHERE chapter.project_id = ? AND chapter.number = ?
            """,
            (project_id, chapter_number),
        ).fetchone()
        if row is None:
            raise SummaryChapterNotFound(
                f"chapter {chapter_number} has no current snapshot in project {project_id}"
            )
        return ChapterText(
            chapter_id=str(row["chapter_id"]),
            number=int(row["number"]),
            snapshot_id=str(row["id"]),
            text=str(row["text"]),
        )

    def _read(
        self,
        conn: Connection,
        project_id: str,
        chapter_number: int,
    ) -> ChapterSummary | None:
        return SummaryStore(conn).get(project_id, chapter_number)


__all__ = [
    "AUTHOR_SUMMARY_MAX_CHARS",
    "AUTHOR_VERSION",
    "ChapterSummary",
    "ChapterSummaryStatus",
    "RETRACTION_VERSION",
    "ROLLING_WINDOW",
    "RollingSummarizer",
    "SummaryChapterNotFound",
    "SummaryEditConflict",
    "SummaryGenerationError",
    "SummaryOrigin",
    "SummaryRequest",
    "SummaryState",
    "SummaryStore",
    "SummaryTextRejected",
    "retract_summary",
    "save_author_summary",
]
