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
一头是库里的总结行，一头是图层的花名册，是一次应用层合成。

── 二、按相关性锚定，不按位置 ────────────────────────────────────────────

**不取「后面 N 章」**：区间是死的，猜小了漏、猜大了全浪费，而铺垫的影响范围本来
就不固定。按「共同提到了谁 / 哪个地方 / 哪个物件」取：

    一级  扫作者刚改的那段字 → 命中花名册里哪些东西      `summary_index.mentions_in_text`
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
from .summary_index import (
    ChapterSummaryMention,
    SummaryMention,
    chapters_after_mentioning,
    mentions_in_text,
)

__all__ = ["TRACK_CHAPTER_LIMIT", "Track", "build_track"]


TRACK_CHAPTER_LIMIT: Final = 8
"""一次最多带回几章的总结。**是名额，不是判据。**

候选池已经由相关性圈定（只有共同提到了什么的章才进来），这个数只回答「验证那一侧
一次读多少」。8 章 ≈ 960 字（模型档总结上限 120 字/章），是那一侧一次调用的合理料量。

没有它的下场：主角在 700 章里章章出现，一次续写就把 700 段总结拖出来——免费是免费，
但排在后面的那几百段既不会被读，也只是把回执撑成一屏无用的章号。
"""


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
    完全不同的原因（就在最前沿写 / 这段字没提到花名册上的东西 / 提到了但后面没人
    提过它们 / 这一次压根没算成），而它们在界面上会长成同一个「什么都没有」。"""

    anchors: list[SummaryMention] = Field(default_factory=list)
    """一级：作者刚改的那段字命中了花名册里的哪些东西。"""

    chapters: list[ChapterSummaryMention] = Field(default_factory=list)
    """二级：后面哪几章的总结跟它们相关，**最相关的在前**（共同提到得越多越靠前），
    至多 `TRACK_CHAPTER_LIMIT` 章。"""

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
) -> Track:
    """算一次轨道。**零模型调用、零花费**，两级都是查库 + 一条正则。

    Args:
        chapter: 作者正在改的那一章。
        text: **作者刚改的那一段字**。今天这条路上拿得到的最接近的东西是
            「这一次请求带上来的那段正文」（续写就是光标前后那两截）——
            引擎手上没有 diff，所以「刚改的」在这一层是个近似，而这个近似
            只会让锚定**多找**几个东西，不会让它漏。
        limit: 最多带回几章，见 `TRACK_CHAPTER_LIMIT`。

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
                "花名册上的任何东西，反查不到相关章节。"
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
    return Track(
        chapter=chapter,
        frontier=frontier,
        anchors=anchors,
        chapters=kept,
        # **`found` 和 `kept` 两个数都要报**：砍掉的那几章不是「不存在」，是「这一次没带」，
        # 而这两件事的下一步动作不同（前者去补总结，后者去调名额）。
        note=(
            f"这一段提到了 {len(anchors)} 样东西，后面 {len(found)} 章的总结跟它们相关"
            f"（这一次带回最相关的 {len(kept)} 章）。"
        ),
    )
