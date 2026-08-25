"""每一段章节总结提到了花名册里的哪些东西 —— **记忆点之间的连线**。

作者的原话是「每一个总结就相当于一本书的一个记忆点，我想迅速找到需要的内容或相关章节的
总结，然后引用、对比、调研，再顺下去看全文」，紧跟着一句「**我不想用 RAG**」。

这两句话不冲突，它们说的是同一件事的两面：

    **他要的不是「找相似」，是「找相关」。**

找相似要向量、embedding、语义打分——那是被砍掉的 Qdrant（ADR 0001 / v1.1 的触发条件制），
而且它要回答「这两段像不像」，也就是 ADR 0005 铁律里那种「这句话是什么意思」的判断。
找相关只要**连线**：谁出现在哪、什么秘密在哪一章被提到、哪件事牵着哪些人。
而 PLAN 的技术裁决表在「全文检索」那一行早就定过这条路：

    > **不做。mention 索引即检索** ——「它给你『顾清音的全部出场按章排序』，
    > 这是作者 90% 的真实需求，而且是精确的不是 BM25 的。」（§6 S11）

这条路今天只跑在正文上（`mentioned.py` 的本章在场推导、`agent/index.py` 的人物出场轴），
没跑在总结上。本模块就是把它接到总结上，**一个语义判断都不加**：判据只有
「这个称呼在这段字里出现了没有」，用的是 `text/mentions.py` 那条正则 alternation
（长度降序、最长优先，`顾清音` 不会被切成 `清音`），和 R2 FUTURE_LEAK 同一份实现。

── 为什么在这里，不在 `draft/` 也不在 `text/` ────────────────────────────

同 `mentioned.py` 那条理由，位置也一样（顶层，和 `declare.py` / `importer.py` 同级）：

* `text/` 是纯字符串层，不认识 store 也不认识库；
* `draft/rolling_summary.py` 是**写侧**（生成 / 改 / 撤回），它只收一条 `Connection`；
  把索引塞进去要顺带给它一个 `StoryGraph`，而它今天一个都不需要；
* 本模块是一次**应用层合成**：一头是库里的总结行，一头是图层的花名册。

── 只收调用方给的 surface ────────────────────────────────────────────────

`store.resolve(pid, None, rules_only=True)`，和 R2/R3 一样。于是「清音」在它被标成
不可用时天然不命中、「师兄」指向 8 个人时天然出局——**不是这里语义过滤，是它们根本
没进 alternation**。`usable_for_rules` 那条判断归 ADR 0004，本模块不重新发明它。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from .db import Connection
from .draft.rolling_summary import ChapterSummary, SummaryOrigin, SummaryStore
from .graph import Node, NodeLabel, NodeNotFound, NodeRef, StoryGraph
from .text.mentions import compile_alternation, find_mentions

__all__ = [
    "ChapterSummaryMention",
    "INDEXED_LABELS",
    "NodeSummaryMentions",
    "ParagraphMention",
    "SummaryMention",
    "chapters_after_mentioning",
    "chapters_mentioning",
    "paragraphs_mentioning",
    "ensure_index",
    "mentions_in_chapter",
    "mentions_in_text",
    "roster_hash",
]


INDEXED_LABELS: Final = frozenset(
    {
        NodeLabel.CHARACTER,
        NodeLabel.LOCATION,
        NodeLabel.FACTION,
        NodeLabel.FORESHADOW,
        NodeLabel.OBJECT,
    }
)
"""进倒排表的 5 类节点。**判据是 `node.label`，仍然是集合判断**（同 `mentioned.py`）。

差集正是 `StateDim` 和 `Chapter`，理由和前端 `AUTHORED_LABELS` 那张表一字不差：
前者是引擎内部的状态维度（`修为` / `身份` / `健康` —— 这些词在总结里是常事，
每一段都会挂一个「修为（状态）」的芯片，纯噪声），后者由导入器生成，它的 name 是章标题。
两者都不是作者心里的「记忆点」，而**噪声芯片的代价和误报是同一种**：作者不再看它们。
"""

_LABEL_ORDER: Final = {label: i for i, label in enumerate(NodeLabel)}
"""芯片的分组顺序 = `NodeLabel` 的声明顺序（人物 → 地点 → 势力 → 伏笔 → …）。

**不按「在这段字里第几个出现」排**：那要在读的时候再摸一遍正文，而这一层的全部
主张就是「读的时候不摸文本，只查表」。组内按显示名排，于是同一份数据永远同一个顺序。
"""


# ══════════════════════════════════════════════════════════════════════════
# 出参（Pydantic，§10 约束 2）
# ══════════════════════════════════════════════════════════════════════════


class SummaryMention(BaseModel):
    """一段总结提到的一个东西。

    **`node` 是 `NodeRef` 不是 `Node`**（§10.5 第 3 条）：`NodeProps` 是 `extra="allow"` 的，
    而这批命中里按定义就有 Secret —— 一个 `props.twist="萧决其实是魔尊之子"` 会顺着
    `model_dump_json()` 直接进浏览器。秘密的**显示名**是花名册本来就在渲染的东西
    （`GET /roster` 一直这么给），秘密的**内容**一个字都不出这个模块。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node: NodeRef
    surfaces: list[str] = Field(min_length=1)
    """总结里真正出现的那几个称呼，按长度降序（同 alternation 的序）。

    **不是只报节点**：作者在这一段里写的是「魔尊」还是「萧决」是他自己的信息
    （ADR 0004：别名差异编码着关系阶段和认知边界，是 canon 不是噪声）。
    """


class ChapterSummaryMention(BaseModel):
    """反查的一行：**哪一章的总结也提到了它**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    summary: str = Field(min_length=1)
    """那一章现在算数的那一段总结，原文。作者点开是为了**读**它，不是为了知道它存在。"""

    surfaces: list[str] = Field(min_length=1)
    """在那一章里它是用哪几个称呼被提到的。"""

    author_written: bool
    """那一段是作者自己写的。模型写的那一段才带「未经确认，只当线索」的免责——
    这一条和 `ChapterSummaryStatus.author_written` 是同一个意思，别在界面上分成两套话。"""


class NodeSummaryMentions(BaseModel):
    """反查的全部答案：**它是谁** + 哪几章的总结提到过它。

    `node` 和 `chapters` 装在一起，是因为界面上那句「顾清音出现在这几章的总结里」
    需要前者，而调用方手上往往只有一个 id（从活动日志、从花名册点过来的时候，
    它没有那个名字）。分成两次查 = 让调用方自己拼一句话 = 第二份措辞。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node: NodeRef
    chapters: list[ChapterSummaryMention]


@dataclass(frozen=True, slots=True)
class _Roster:
    """一次扫描要用的花名册快照：可匹配的称呼 → 它指向谁，加上它的内容地址。"""

    owner: dict[str, str]
    """surface → node_id。`rules_only=True` 已经保证「恰好一个候选且未被标不可用」。"""

    hash: str
    """`owner` 的内容地址。**索引行的有效期就是这个哈希**（见模块级 `ensure_index`）。"""


@dataclass(frozen=True, slots=True)
class _IndexState:
    """`ensure_index` 跑完之后，这个项目的索引长什么样。"""

    roster: _Roster
    active: dict[str, ChapterSummary]
    """summary_id → 那一行。**「现在算数的是哪几行」只由 `SummaryStore` 说**，
    本模块不写第二份判据（迁移 014 的头注释讲了为什么这两张表不存章号）。"""


# ══════════════════════════════════════════════════════════════════════════
# 花名册的内容地址
# ══════════════════════════════════════════════════════════════════════════


def roster_hash(store: StoryGraph, project_id: str) -> str:
    """当前花名册的内容地址。**索引什么时候作废，全靠这一个值。**

    作者建一个人物、加一个别名、把某个称呼标成不可用、删掉一个人 —— 这个哈希就变，
    于是全书每一行索引都对不上，下一次读时整本重扫一次。

    **它换掉的是「写路径记得去通知索引」那条纪律**，而那条纪律在这个仓库有明确评价
    （ARCHITECTURE §10.5：「这是个『必须记得调』的守卫——它是本层最弱的一环」）。
    忘了通知不会报错，表现只是「那个新建的人物在总结里永远搜不到」——一条静默的假空，
    正是 §10 约束 8 反复在拦的那一类。
    """
    return _roster(store, project_id).hash


class ParagraphMention(BaseModel):
    """一段正文里命中了哪几样东西（轨道三级下探的料）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    para_index: int = Field(ge=0)
    """段号，**0-based**（全系统口径，ADR 0006）。要给作者看就在渲染那一层 +1。"""

    text: str = Field(min_length=1)
    surfaces: list[str] = Field(min_length=1)
    """这一段里那几样东西是用哪几个称呼被提到的。"""


def paragraphs_mentioning(
    store: StoryGraph,
    project_id: str,
    paragraphs: Sequence[str],
    node_ids: Iterable[str],
) -> list[ParagraphMention]:
    """这几段里，**哪几段提到了这几个节点**。库只碰一次，正则只编一次。

    ── 为什么它在这儿而不在 `track.py` ──────────────────────────────────

    花名册和那条 alternation 归本模块（`_roster` + `INDEXED_LABELS`）。调用方按段调
    `mentions_in_text` 也能算出同样的答案，但那是**每段重建一次花名册、重编一次正则**
    ——8 章 × 30 段 = 240 次，而这条路的整个卖点是「零模型调用、零花费」。

    ── 出参为什么带 `para_index` ────────────────────────────────────────

    三级要的是「那几段」，不是「那一章」。段号从 `find_mentions` 来，和 `Issue` 的锚
    是同一份实现（ADR 0006）——**这一层不自己数第二遍**。
    """
    wanted = frozenset(node_ids)
    if not wanted or not paragraphs:
        return []
    roster = _roster(store, project_id)
    if not roster.owner:
        return []
    # **只把要找的那几个节点的称呼编进 alternation**：整本花名册编进去会命中一堆跟这次
    # 无关的东西再在后面滤掉——那不只是白跑，还会让最长优先在别处生效
    # （`text/mentions.py` 那条机械纪律是按「进了 alternation 的那些」算的）。
    surfaces = [s for s, owner in roster.owner.items() if owner in wanted]
    if not surfaces:
        return []
    pattern = compile_alternation(_ordered(surfaces))
    by_para: dict[int, list[str]] = {}
    for hit in find_mentions(list(paragraphs), pattern):
        found = by_para.setdefault(hit.para_index, [])
        if hit.matched_text not in found:
            found.append(hit.matched_text)
    return [
        ParagraphMention(
            para_index=index, text=paragraphs[index], surfaces=_ordered(by_para[index])
        )
        for index in sorted(by_para)
    ]


def _roster(store: StoryGraph, project_id: str) -> _Roster:
    owner: dict[str, str] = {}
    for resolution in store.resolve(project_id, None, rules_only=True):
        if not resolution.hits:
            continue
        node = resolution.hits[0].node
        if node.label in INDEXED_LABELS:
            owner[resolution.surface] = node.id
    payload = json.dumps(
        sorted(owner.items()), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return _Roster(owner=owner, hash=sha256(payload).hexdigest())


def _nodes_by_id(store: StoryGraph, project_id: str) -> dict[str, Node]:
    """node_id → 节点。**不 `rules_only`**：反查要渲染的是「它是谁」，而一个名字太短、
    或者称呼有歧义的人物照样可以是命中的那个人（命中走的是别的 surface）。"""
    out: dict[str, Node] = {}
    for resolution in store.resolve(project_id, None):
        for hit in resolution.hits:
            out[hit.node.id] = hit.node
    return out


# ══════════════════════════════════════════════════════════════════════════
# 重建：**两个内容地址的相等判断，没有一个要记得调的钩子**
# ══════════════════════════════════════════════════════════════════════════


def ensure_index(conn: Connection, store: StoryGraph, project_id: str) -> None:
    """把这个项目的倒排表补到「跟当前的总结和当前的花名册都对得上」。

    ── 什么时候会真的干活 ────────────────────────────────────────────────

    索引是 `(总结的那一行, 花名册那一版)` 的纯函数，两个自变量都是内容寻址的：

    * **总结改 / 撤回 / 重新生成** —— `chapter_summary` 是 append-only（迁移 013），
      三个动作产出的都是**一个新 id 的新行**，旧行原样留着。所以「这一行的字变了」
      在这个库里不可能发生，变的只是「哪一行现在算数」。新行没有索引 ⇒ 这里补扫；
      旧行不再算数 ⇒ 这里把它的索引删掉。**三条写路径一个字都不用改，也没得忘。**
    * **花名册变** —— `roster_hash` 变 ⇒ 全书索引一个都对不上 ⇒ 整本重扫。

    ── 为什么敢在读路径上做这件事 ────────────────────────────────────────

    因为总结整本加起来是**几十万个字符**（模型档 120 字上限 / 作者档 1000 字上限
    × 章数），一次 alternation 全扫是毫秒级。正文那一侧（每章几千字）才是需要
    心疼的量，而这张表**不碰正文**。

    没有一行要补时**一条写语句都不发**（也就不去抢那把写锁）——换章、切页签这些
    每分钟发生几十次的读，代价是两条 SELECT。
    """
    _ensure(conn, store, project_id)


def _ensure(conn: Connection, store: StoryGraph, project_id: str) -> _IndexState:
    roster = _roster(store, project_id)
    active = {row.id: row for row in SummaryStore(conn).active(project_id)}
    known = {
        str(row["summary_id"]): str(row["roster_hash"])
        for row in conn.execute(
            "SELECT summary_id, roster_hash FROM summary_index WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    }
    keep = {sid for sid, digest in known.items() if digest == roster.hash and sid in active}
    drop = sorted(known.keys() - keep)
    scan = sorted(active.keys() - keep)
    if drop or scan:
        _rebuild(conn, project_id, roster, active, drop=drop, scan=scan)
    return _IndexState(roster=roster, active=active)


def _rebuild(
    conn: Connection,
    project_id: str,
    roster: _Roster,
    active: dict[str, ChapterSummary],
    *,
    drop: Sequence[str],
    scan: Sequence[str],
) -> None:
    """一个事务里做完「删作废的 + 扫新的」。中途炸掉 = 一行都没动。

    半个事务的后果不是「少几个芯片」而是**一份看起来正常的假页面**：
    索引说这一章提到了三个人，其中一个的那一行还指着作者半分钟前撤掉的那段总结。

    ── 要扫的那些也**先删一遍**，理由是并发 ────────────────────────────────

    `_ensure` 的两条 SELECT 在事务**外面**（不想让每一次读都去抢写锁）。于是两个标签页
    同时打开这一格时，两边都算出同一份 `scan`，后到的那个进事务时那些行已经在库里了——
    直接 INSERT 会撞主键，**在作者屏幕上就是一次莫名其妙的 500**。先删后插，重跑一次
    完全等价（同一段字 + 同一版花名册 ⇒ 同一批命中），代价是几百条走主键的 DELETE。
    """
    pattern = compile_alternation(list(roster.owner))
    conn.execute("BEGIN IMMEDIATE")
    try:
        for chunk in _chunks([*drop, *scan]):
            marks = ",".join("?" * len(chunk))
            # 两条 DELETE，不靠 `ON DELETE CASCADE` 收第二张表：级联要 `foreign_keys=ON`，
            # 而那是**连接级**的 PRAGMA（`db.connect()` 的注释自己写着「每条连接都要设」）。
            # 它今天确实开着；把「作废的索引行真的消失了」挂在一条别处设的 PRAGMA 上，
            # 关掉它的那一天这里不会报错，只会开始返回撤回过的章。
            conn.execute(
                f"DELETE FROM summary_mention WHERE summary_id IN ({marks})", tuple(chunk)
            )
            conn.execute(
                f"DELETE FROM summary_index WHERE summary_id IN ({marks})", tuple(chunk)
            )
        for summary_id in scan:
            conn.execute(
                "INSERT INTO summary_index (summary_id, project_id, roster_hash)"
                " VALUES (?, ?, ?)",
                (summary_id, project_id, roster.hash),
            )
            rows = [
                (summary_id, project_id, roster.owner[surface], surface)
                for surface in _hit_surfaces(active[summary_id].summary, pattern, roster)
            ]
            if rows:
                conn.executemany(
                    "INSERT INTO summary_mention (summary_id, project_id, node_id, surface)"
                    " VALUES (?, ?, ?, ?)",
                    rows,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _hit_surfaces(text: str, pattern: re.Pattern[str], roster: _Roster) -> list[str]:
    """这段字里命中的称呼，去重，按 alternation 的序（长度降序）。

    整段当**一段**喂给 `find_mentions`：那个函数的 `para_index` / `occurrence_k` 是
    给锚点用的（ADR 0006），而这里要的只是一个集合——**总结不是正文，它没有锚**
    （作者改一段总结不会让任何 `Issue` 的坐标漂掉）。
    """
    seen = {hit.matched_text for hit in find_mentions([text], pattern)}
    return [surface for surface in _ordered(roster.owner) if surface in seen]


def _ordered(owner: Iterable[str]) -> list[str]:
    return sorted(owner, key=lambda s: (-len(s), s))


def _chunks(items: Sequence[str], size: int = 400) -> Iterable[Sequence[str]]:
    """SQLite 的 `?` 上限（默认 999）：722 章一次全作废是真会发生的（改一次花名册）。"""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ══════════════════════════════════════════════════════════════════════════
# 读：两条，都是一次 SQL，都不调模型
# ══════════════════════════════════════════════════════════════════════════


def mentions_in_chapter(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    chapter_number: int,
) -> list[SummaryMention]:
    """第 N 章**现在算数**的那段总结提到了哪些东西。

    这一章没有总结（没写 / 没生成 / 撤回过）→ 空表。三种「没有」的区分在
    `ChapterSummaryStatus` 那边，**不在这儿再答一遍**：屏幕上那一格已经在说那句话了，
    这儿再说一遍就是第二份措辞。
    """
    state = _ensure(conn, store, project_id)
    target = next(
        (row for row in state.active.values() if row.chapter_number == chapter_number),
        None,
    )
    if target is None:
        return []
    rows = conn.execute(
        "SELECT node_id, surface FROM summary_mention WHERE summary_id = ?",
        (target.id,),
    ).fetchall()
    if not rows:
        return []
    nodes = _nodes_by_id(store, project_id)
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(str(row["node_id"]), []).append(str(row["surface"]))
    hits = [
        SummaryMention(node=NodeRef.of(nodes[node_id]), surfaces=_ordered(surfaces))
        for node_id, surfaces in grouped.items()
        # 节点在这一瞬间被删掉是可能的（另一个标签页），而它的下一步是重扫，不是报错。
        if node_id in nodes
    ]
    hits.sort(key=lambda hit: (_LABEL_ORDER[hit.node.label], hit.node.name))
    return hits


def mentions_in_text(
    store: StoryGraph,
    project_id: str,
    text: str,
) -> list[SummaryMention]:
    """**这段字**里出现了花名册的哪些东西。库一次都不碰，一个语义判断都没有。

    和 `mentions_in_chapter` 的差别只有输入：那个问的是「第 N 章那段总结提到了谁」，
    这个问的是「你手上这段字提到了谁」。**两者共用同一份 alternation 和同一批标签**
    （`_roster` + `INDEXED_LABELS`），所以它算出来的 node_id 必然对得上倒排表里的键——
    自己另起一份花名册的话，反查会稳定地少命中，而少命中是静默的。

    这是「轨道」的第一级（`track.py`）：作者刚改的那段字命中了哪些记忆点，
    再拿它们去反查后面哪几章的总结也提到过。

    和 `mentioned.py::mentioned_cast` 的分工：那个只收 Character、返回**称呼原文**，
    因为它的下游 `resolve_cast` 收的就是原文；这里六类都要（物件、地点、伏笔同样是
    「后面章节可能已经写死了的设定」），返回的是节点——反查表的键是 node_id。
    """
    roster = _roster(store, project_id)
    if not roster.owner or not text:
        return []
    pattern = compile_alternation(list(roster.owner))
    surfaces = _hit_surfaces(text, pattern, roster)
    if not surfaces:
        return []
    grouped: dict[str, list[str]] = {}
    for surface in surfaces:
        grouped.setdefault(roster.owner[surface], []).append(surface)
    nodes = _nodes_by_id(store, project_id)
    hits = [
        SummaryMention(node=NodeRef.of(nodes[node_id]), surfaces=_ordered(found))
        for node_id, found in grouped.items()
        # 同 `mentions_in_chapter`：另一个标签页刚把这个节点删掉是可能的。
        if node_id in nodes
    ]
    hits.sort(key=lambda hit: (_LABEL_ORDER[hit.node.label], hit.node.name))
    return hits


def chapters_after_mentioning(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    node_ids: Iterable[str],
    *,
    after_chapter: int,
) -> list[ChapterSummaryMention]:
    """**第 `after_chapter` 章之后**，哪几章的总结提到了这批东西里的任意一个。

    这是「轨道」的第二级（`track.py`）：一次 SQL，不调模型、不花钱。

    ── 为什么不是「`chapters_mentioning` 循环 N 次再过滤」──────────────────

    那个函数每调一次都要把整本花名册解析两遍（`_ensure` 一遍、`_nodes_by_id` 一遍）。
    一段正文命中十几个东西是常事，而这条路跑在**作者停手 400 毫秒**那条预算里。
    判据、索引、时态口径全部共用，差的只有「一次问一批」和「只要后面的章」。

    Args:
        after_chapter: 严格大于它的章才进来。**这就是「后面」的定义**——
            作者正在改的那一章自己不算（他手上那段字就是它，再给一遍是噪声），
            更早的章也不算（那是记忆层的活，走 `product_context`，而且那一侧
            允许进 Writer 的 prompt，这一侧不允许）。

    Returns:
        按章号升序。`surfaces` 是**这一批节点在那一章的总结里合起来用过的称呼**，
        所以它的长度可以拿来当「相关度」用（共同提到的越多越相关）——
        代价说清楚：一个人有三个别名时它数出来是 3 不是 1，排序会略偏向别名多的人。
        排序归调用方（`track.py`），这儿只保证序稳定。
    """
    state = _ensure(conn, store, project_id)
    wanted = sorted({node_id for node_id in node_ids})
    if not wanted:
        return []
    grouped: dict[str, list[str]] = {}
    for chunk in _chunks(wanted):
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT summary_id, surface FROM summary_mention"
            f" WHERE project_id = ? AND node_id IN ({marks})",
            (project_id, *chunk),
        ).fetchall()
        for row in rows:
            summary_id = str(row["summary_id"])
            # 同 `chapters_mentioning`：索引行可能指着一段**刚刚不算数了**的总结。
            item = state.active.get(summary_id)
            if item is None or item.chapter_number <= after_chapter:
                continue
            grouped.setdefault(summary_id, []).append(str(row["surface"]))
    out = [
        ChapterSummaryMention(
            chapter_number=state.active[summary_id].chapter_number,
            summary=state.active[summary_id].summary,
            surfaces=_ordered(set(surfaces)),
            author_written=state.active[summary_id].source is SummaryOrigin.AUTHOR,
        )
        for summary_id, surfaces in grouped.items()
    ]
    out.sort(key=lambda row: row.chapter_number)
    return out


def chapters_mentioning(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    node_id: str,
) -> NodeSummaryMentions:
    """**还有哪几章的总结提到它**，按章号升序。一次 SQL，不调模型、不花钱。

    这就是作者要的那件事：一段总结是一个记忆点，点开它上面的任意一个东西，
    书里所有提到过它的记忆点按顺序摊开——然后他自己决定要引用哪一段、对比哪两段、
    从哪一章顺下去看全文。

    Raises:
        NodeNotFound: `node_id` 不在本项目。**不返回空表**：「他没在任何总结里出现过」
            和「这个 id 根本不存在」是两件事，而它们的下一步动作完全不同
            （§10 约束 8：静默的零和真的零不许长得一样）。
    """
    state = _ensure(conn, store, project_id)
    nodes = _nodes_by_id(store, project_id)
    if node_id not in nodes:
        # 措辞和 `sqlite_store._require_node` 逐字同形——同一件事在作者眼里该是同一句话。
        raise NodeNotFound(f"要反查的节点 不在项目 {project_id} 里：{node_id}")
    rows = conn.execute(
        "SELECT summary_id, surface FROM summary_mention"
        " WHERE project_id = ? AND node_id = ?",
        (project_id, node_id),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        summary_id = str(row["summary_id"])
        # 交集：索引行可能指着一段**刚刚不算数了**的总结（另一条连接刚撤回它）。
        # `_ensure` 会在下一次把它删掉，但这一轮的答案不许把它算进去。
        if summary_id in state.active:
            grouped.setdefault(summary_id, []).append(str(row["surface"]))
    out = [
        ChapterSummaryMention(
            chapter_number=state.active[summary_id].chapter_number,
            summary=state.active[summary_id].summary,
            surfaces=_ordered(surfaces),
            author_written=state.active[summary_id].source is SummaryOrigin.AUTHOR,
        )
        for summary_id, surfaces in grouped.items()
    ]
    out.sort(key=lambda row: row.chapter_number)
    return NodeSummaryMentions(node=NodeRef.of(nodes[node_id]), chapters=out)
