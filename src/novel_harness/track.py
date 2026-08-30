"""轨道 —— 作者跳回去改旧章时，**后面那些已经写完的章里跟这一段相关的设定**。

作者写到第 158 章，跳回去改第 2 章。在这个模块存在之前，引擎**完全不知道第 3 章
之后存在**：拼上下文只取比当前章更早的，续写只给光标之前的，后台算权重时把章号
更大的一律当成「还没写」。于是改出来的东西可能跟第 64 章直接打架，而没有任何东西
会发现。

**轨道不是素材，是护栏**：不是拿来照着写的，是「写出来的东西不许跟它相抵触」。
这一条决定了它的全部形状，包括下面那条最要命的边界。

── ⚠️ 一、它永远不进 Writer 的 prompt。这是地基，不是优化 ──────────────────

后面章节的总结里，可能写着**这一章的读者还不该知道的事**
（第 64 章：「萧决终于知道自己身上养的是玄血蛊。」）。把它给写第 2 章的模型看，
等于把伏笔亲手告诉它——**那正是这个产品的核心主张被从背后拆掉的地方**
（CLAUDE.md 头号错误第 5 条的同一个失败，只是从另一扇门进来）。

**解法不是过滤，是分开：写的和验的不是同一个上下文。**

    Writer 的上下文：前文、下文、**前面**章节的总结     —— 干净的，从没见过轨道
    验证那一侧    ：轨道（可能带着不该出现的内容）+ Writer 刚写的那段

所以本模块**住在 `draft/` 外面**，而且 `draft/` 一行都不许 import 它——
`tests/test_track_isolation.py` 扫 AST 钉这一条，并另有一条在真链路上验
「第 64 章的总结一个字都没进第 2 章的 prompt」。位置的理由同 `summary_index.py`：
一头是库里的总结行，一头是图层的角色册，是一次应用层合成。

── 二、按相关性锚定，不按位置 ────────────────────────────────────────────

**不取「后面 N 章」**：区间是死的，猜小了漏、猜大了全浪费，而铺垫的影响范围本来
就不固定。按「共同提到了谁 / 哪个地方 / 哪个物件」取：

    一级  扫作者刚改的那段字 → 命中角色册里哪些东西      `summary_index.mentions_in_text`
    二级  反查倒排表 → 后面哪几章的总结也提到了它们      `summary_index.chapters_after_mentioning`

两级都是**纯查库 + 一条正则**，零模型调用、零花费，所以敢跑在「停手 400 毫秒」
那条预算里。倒排表六类都收（人物 / 地点 / 势力 / 秘密 / 伏笔 / **物件**），
于是「玄铁令第 64 章写着只能在水底唤醒，你第 2 章写它发白光」和人物那条线是
**同一次查询**捞回来的。

三级（下探到那几章原文的相关段落）和四级（用模型判抵不抵触）不在本模块：
前者待定，后者是验证那一侧的事，而它按定义要花钱。

── 三、消费者是谁 ────────────────────────────────────────────────────────

今天只有一个：`POST …/draft` 把它算出来放进响应的 `track` 一格（**和 `memory`
那一格正好相反**——`memory` 说的是「这一稿的 prompt 里装了什么」，`track` 说的是
「这一次查到了什么，而且它一个字都没进 prompt」）。异步验证（模式一）和那个
持有轨道的工具（模式二）各自是后面的事，它们收的是同一个 `Track`。
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from .db import Connection
from .focus import frontier_chapter
from .graph import StoryGraph
from .draft.length import count_units
from .summary_index import (
    ChapterSummaryMention,
    SummaryMention,
    chapters_after_mentioning,
    mentions_in_text,
    paragraphs_mentioning,
)
from .text import paragraphs as split_paragraphs

__all__ = [
    "TRACK_CHAPTER_LIMIT",
    "TRACK_EXCERPT_UNITS",
    "ChapterSnapshotText",
    "Track",
    "TrackExcerpt",
    "build_track",
    "current_chapter_text",
]


TRACK_CHAPTER_LIMIT: Final = 8
"""一次最多带回几章的总结。**是名额，不是判据。**

候选池已经由相关性圈定（只有共同提到了什么的章才进来），这个数只回答「验证那一侧
一次读多少」。8 章 ≈ 960 字（模型档总结上限 120 字/章），是那一侧一次调用的合理料量。

没有它的下场：主角在 700 章里章章出现，一次续写就把 700 段总结拖出来——免费是免费，
但排在后面的那几百段既不会被读，也只是把回执撑成一屏无用的章号。
"""


TRACK_EXCERPT_UNITS: Final = 1_000
"""三级下探一次最多带回多少字（`draft/length.py::count_units` 口径 = 非空白字符数）。

**它是闸不是配额**：常态下拦不到（一章里真提到那几样东西的段落通常只有两三段）。
它拦的是病态输入——一整卷被当成一章、或者主角在那一章里章章出现。

为什么是 1,000：二级八章总结 ≈ 960 字已经是验证那一侧一次调用的合理料量，
三级是**在总结说不清时补那几段**，不是换一种更贵的方式把那几章重读一遍。
"""


class ChapterSnapshotText(BaseModel):
    """某一章**当前**那一版快照连正文。

    判据是 `chapter_snapshot.text_sha256 == chapter.text_sha256` 的**精确等值**，
    不是「这一章最新的那条快照」——后者要在「哪个快照是当前的」这件事上猜，
    而 `chapter.text_sha256` 已经把答案写在那儿了（同 `store.current_snapshots`）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_id: str
    snapshot_id: str
    sha256: str
    """这段正文是哪一版。事后核对拿它记「这条告警照的是哪一版」。"""

    text: str


def current_chapter_text(
    conn: Connection, project_id: str, chapter_number: int
) -> ChapterSnapshotText | None:
    """第 N 章当前那一版正文。查不到 → `None`（还没进库，或者刚被删掉）。

    **这份读法全仓只有这一处**：`advisory_review` 也要它（事后核对拿真正文数句子），
    而它已经 import 本模块，本模块不 import 它——方向对，住在这儿是唯一不会长出
    第二份的位置。两份的下场是「当前快照」的判据在两处各写一遍，漂了之后的产物是
    一条锚在旧正文上的告警，**而那种错没有任何东西会红**。
    """
    row = conn.execute(
        """
        SELECT chapter.id AS chapter_id, chapter_snapshot.id AS snapshot_id,
               chapter_snapshot.text_sha256 AS sha256, chapter_snapshot.text AS text
          FROM chapter
          JOIN chapter_snapshot
            ON chapter_snapshot.chapter_id = chapter.id
           AND chapter_snapshot.text_sha256 = chapter.text_sha256
         WHERE chapter.project_id = ? AND chapter.number = ?
        """,
        (project_id, chapter_number),
    ).fetchone()
    if row is None:
        return None
    return ChapterSnapshotText(
        chapter_id=str(row["chapter_id"]),
        snapshot_id=str(row["snapshot_id"]),
        sha256=str(row["sha256"]),
        text=str(row["text"]),
    )


class TrackExcerpt(BaseModel):
    """三级：那一章原文里**真正提到了缺口那几样东西**的一段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    para_index: int = Field(ge=0)
    """段号 **0-based**（ADR 0006 全系统口径）。上屏要 +1，那是渲染层的事。"""

    text: str = Field(min_length=1)
    surfaces: list[str] = Field(min_length=1)
    """这一段命中了缺口里的哪几个称呼。"""


class Track(BaseModel):
    """一次锚定的全部答案。**这个对象永远不进 Writer 的 prompt**（见模块头）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    """作者正在改的那一章。"""

    frontier: int = Field(ge=0)
    """全书最大章号（`focus.frontier_chapter`）。**0 = 这本书还没有章，或者这一次
    问不出来**——两者的区别在 `note` 里说，不在这个数里编码。"""

    note: str
    """**零带着理由一起出现**（ARCHITECTURE §10 约束 8）：`chapters` 为空有四种
    完全不同的原因（就在最前沿写 / 这段字没提到角色册上的东西 / 提到了但后面没人
    提过它们 / 这一次压根没算成），而它们在界面上会长成同一个「什么都没有」。"""

    anchors: list[SummaryMention] = Field(default_factory=list)
    """一级：作者刚改的那段字命中了角色册里的哪些东西。"""

    chapters: list[ChapterSummaryMention] = Field(default_factory=list)
    """二级：后面哪几章的总结跟它们相关，**最相关的在前**（共同提到得越多越靠前），
    至多 `TRACK_CHAPTER_LIMIT` 章。"""

    excerpts: list[TrackExcerpt] = Field(default_factory=list)
    """三级：总结说不清的那几章，原文里的相关段落。**空是常态**——
    二级的总结把锚点都覆盖到了就不下探（判据见 `_dig`）。"""

    @property
    def at_frontier(self) -> bool:
        """这一章后面还有没有已经写完的章。**问不出来时也答 `True`。**

        答 `True` 意味着「照旧，什么都不变」，那是这条路上唯一安全的默认
        （§10 约束 7：系统不确定时的默认动作是闭嘴）。答错成 `False` 的代价是
        凭空多几次免费查询，答错成 `True` 的代价是回到「引擎以为自己在最前沿」——
        而后者正是这个模块要修的病，所以两个方向不对称，只有前者可以接受。
        """
        return self.chapter >= self.frontier


def build_track(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    *,
    chapter: int,
    text: str,
    limit: int = TRACK_CHAPTER_LIMIT,
    excerpt_units: int = TRACK_EXCERPT_UNITS,
) -> Track:
    """算一次轨道。**零模型调用、零花费**，两级都是查库 + 一条正则。

    Args:
        chapter: 作者正在改的那一章。
        text: **作者刚改的那一段字**。今天这条路上拿得到的最接近的东西是
            「这一次请求带上来的那段正文」（续写就是光标前后那两截）——
            引擎手上没有 diff，所以「刚改的」在这一层是个近似，而这个近似
            只会让锚定**多找**几个东西，不会让它漏。
        limit: 最多带回几章，见 `TRACK_CHAPTER_LIMIT`。
        excerpt_units: 三级下探最多带回多少字，见 `TRACK_EXCERPT_UNITS`。
            传 `0` = 只做到二级（模式一 2026-08-22 的行为，留着当退路）。

    Returns:
        一份 `Track`。**在最前沿写就是空的**（只花一条 `MAX(number)` 查询），
        这是「是最新章时行为一字不变」的落点。
    """
    frontier = frontier_chapter(conn, project_id)
    if chapter >= frontier:
        return Track(
            chapter=chapter,
            frontier=frontier,
            # 「就是最后一章」和「比最后一章还靠后（在写新的一章）」是同一件事的两种形状，
            # 措辞得同时对得上两种——写死「这一章就是最后一章」在后一种里是假话。
            note=(
                f"这一章后面没有已经写完的章（全书 {frontier} 章），轨道是空的。"
                if frontier
                else "这本书还没有章，轨道无从算起。"
            ),
        )

    anchors = mentions_in_text(store, project_id, text)
    if not anchors:
        return Track(
            chapter=chapter,
            frontier=frontier,
            note=(
                f"这一段后面还有 {frontier - chapter} 章已经写完，但这段字里没有出现"
                "角色册上的任何东西，反查不到相关章节。"
            ),
        )

    found = chapters_after_mentioning(
        conn,
        store,
        project_id,
        [anchor.node.id for anchor in anchors],
        after_chapter=chapter,
    )
    if not found:
        names = "、".join(anchor.node.name for anchor in anchors)
        return Track(
            chapter=chapter,
            frontier=frontier,
            anchors=anchors,
            note=(
                f"这一段提到了 {names}，但后面 {frontier - chapter} 章的总结里"
                "没有一段提到它们。"
            ),
        )
    # 相关度 = 共同提到了几个称呼；并列时离得近的先（都在这一章后面，所以就是章号升序）。
    # **排序在这儿，不在查询那一层**：那一层保证的是「序稳定」，怎么算相关是这一层的事。
    ranked = sorted(found, key=lambda row: (-len(row.surfaces), row.chapter_number))
    kept = ranked[: max(0, limit)]
    excerpts = _dig(
        conn, store, project_id, anchors=anchors, chapters=kept, budget=max(0, excerpt_units)
    )
    return Track(
        chapter=chapter,
        frontier=frontier,
        anchors=anchors,
        chapters=kept,
        excerpts=excerpts,
        # **`found` 和 `kept` 两个数都要报**：砍掉的那几章不是「不存在」，是「这一次没带」，
        # 而这两件事的下一步动作不同（前者去补总结，后者去调名额）。
        note=(
            f"这一段提到了 {len(anchors)} 样东西，后面 {len(found)} 章的总结跟它们相关"
            f"（这一次带回最相关的 {len(kept)} 章）。"
            + (
                f"其中有几章的总结没提到全部锚点，下探回了 {len(excerpts)} 段原文。"
                if excerpts
                else ""
            )
        ),
    )


def _dig(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    *,
    anchors: list[SummaryMention],
    chapters: list[ChapterSummaryMention],
    budget: int,
) -> list[TrackExcerpt]:
    """三级：**总结说不清的那几章**，下探到原文里的相关段落（ADR 0038 阶段 4）。

    ── 「说不清」是一次集合判断，不是一次语义判断 ──────────────────────────

    这一条 2026-08-22 定不下来，理由是「现在定死规则就是拍脑袋」。**2026-08-23 维护者
    裁定要做**，而能不拍脑袋的判据只有一条形状：

        这一章的**总结**提到的锚点  ⊊  作者刚改那段字里的锚点

    差集非空 = 那一章的总结**可证明地**对我们正关心的某几样东西只字未提，而它偏偏
    因为提到了别的锚点才进的候选池。这是 ADR 0005 那条铁律的正面用法：只问「提没提到」，
    不问「说清楚了没有」——后者是语义，本仓 v1 一律不做。

    差集为空 ⇒ **不下探**。那时总结已经覆盖了全部锚点，下探只是把同一件事用 5 倍的字
    再读一遍，而验证那一侧的料量有上限，多带的会把该带的挤掉。

    ── 为什么只取那几段，不取整章 ────────────────────────────────────────

    取的是**命中缺口那几样东西的段落**，判据同上，仍是集合。整章带回来会让二级那八章
    总结一个都装不下——同 `product_context` 删掉「原文兜底」那条的理由：用原文顶替总结
    = 一章吃掉几十章的额度，量完全不可控。

    ── 预算用完就停，而且停在**章**的边界上 ──────────────────────────────

    停在段边界会让某一章只带回半份证据（前三段有、后两段被截），而「这一章我看全了」
    和「这一章我看了一半」在下游长得一模一样。**宁可整章不带**——少一章是可数的，
    半章是不可数的。
    """
    if budget <= 0 or not chapters:
        return []
    anchor_ids = {anchor.node.id for anchor in anchors}
    out: list[TrackExcerpt] = []
    spent = 0
    for row in chapters:
        covered = {hit.node.id for hit in mentions_in_text(store, project_id, row.summary)}
        gaps = anchor_ids - covered
        if not gaps:
            continue
        snapshot = current_chapter_text(conn, project_id, row.chapter_number)
        if snapshot is None:
            # 有总结没正文：反查读的是总结表，两张表可以不同步（正文刚被删、或者这一章
            # 还没同步进来）。这不是错，是「这一章没法下探」。
            continue
        found = paragraphs_mentioning(
            store, project_id, split_paragraphs(snapshot.text), gaps
        )
        if not found:
            continue
        cost = sum(count_units(hit.text, "zh") for hit in found)
        if spent + cost > budget:
            break  # 停在**章**的边界上（见 docstring 最后一节）
        spent += cost
        out.extend(
            TrackExcerpt(
                chapter_number=row.chapter_number,
                para_index=hit.para_index,
                text=hit.text,
                surfaces=hit.surfaces,
            )
            for hit in found
        )
    return out
