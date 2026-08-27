"""书内索引 —— **一份便宜的目录，模型自己往下钻**（ADR 0019 边界一的加法）。

不是「把全书摘要一股脑甩给模型」。那样既贵又不灵，而且它把「认出这一章和问题有没有
关系」这件事交给了引擎——引擎不许做那个判断（ADR 0005：只做集合判断）。这里的形状是
**目录 + 出处 + 往下钻**：每一条返回都带章号，取哪一条由模型自己决定。

四层，越往下越贵，**默认那一层必须便宜**：

| 层 | 工具 | 给什么 | 从哪来 |
|---|---|---|---|
| L0 | `book_index` | 全书章标题 + 花名册（含秘密的**显示名**） | 磁盘上的 `chapters/NNNN.md` + `store.resolve` |
| L1 | `character_chapters` | 某几个人**同时**出现在哪些章 | 正文 mention 扫描 + 已确认事件，**两条轴分开报** |
| L2 | `chapter_summaries` | 指定章号区间的滚动总结 | `SummaryIndex.coverage` + 磁盘对账（四态） |
| L3 | `chapter_text` | 一章正文 | 磁盘（ADR 0007） |

── L1 是这里最值钱的一层，而它一次向量都没用上（ADR 0002）─────────────────

「萧决第一次见顾清音是哪章」在这个系统里是**两个集合求交**，不是段落召回：ADR 0002
的原话是「答案不在任何一段原文里……它不存在于文本中，只存在于聚合里」。确定性、零猜测，
比 ANN 又快又准。**这正是铁律「只做集合判断」的正面形态**：能确定性回答的走集合，
集合答不了的才交给模型去读摘要（L2）和正文（L3）。

**两条轴回答的不是同一个问题，所以它们各占一份、各自报自己瞎没瞎：**

- **mention 轴**：这几个人的称呼**同时出现在**哪几章的正文里。它数的是字符串，
  和 R2 FUTURE_LEAK 用的是同一条 alternation（`text/mentions.py`），一个语义判断都没有。
  超集：回忆里的死人、被议论的第三方都会进来——**它不回答「谁在场」**（`mentioned.py`
  把这条讲透了，UI 上也不许写「在场」）。
- **事件轴**：这几个人**同时**是某一条已确认（CANON）事件的参与者或知情者。它比
  mention 轴准得多，但它依赖抽取跑过——**没跑过时它是空的，而空的和「没同框」长得一样**，
  所以每个人各自的事件总数也一并报出去（那个数为 0 = 这个人一条已确认事件都没有）。

── 三条纪律，违反任意一条这一层就开始骗人 ────────────────────────────────

1. **裁了什么必须说出来。** 每一处 top-N / 截断都物化成一个 `omitted` 计数 + 一句中文。
   静默截断读起来像「全覆盖了」，而模型会据此下结论。
2. **缺摘要不许静默，而「缺」有四种。** `coverage()` 只给得出三态（没写 / 写了没总结 /
   有），**它的「没写」是库的说法，而正文的真相源是磁盘**（ADR 0007）——作者刚在自己的
   编辑器里写完、还没 `sync` 的那一章在库里长得和「还没写到那儿」一模一样。所以这一层
   拿磁盘目录再对一遍账，把它拆成 `unindexed`（磁盘上有、库里还没有）和 `unwritten`
   （两边都没有）。**四个桶必须把问到的区间铺满**，`tests/test_story_wiki_honesty.py`
   的那条 partition 断言就是钉这个的。
   `coverage()` 还有一件事答不了：**它只问「有没有」不问「新不新」**
   （这条既有限制写在 `SummaryStore.coverage()` 自己的 docstring 里）。摘要过期比缺摘要更坏——缺是**瞎**，
   过期是**说错**，而模型手里只有那一段摘要。所以这一层拿磁盘 mtime 自己核一遍
   （`_rewritten_since`），核不了的时候用 `stale_checked` 说「我没查」。
3. **超过作者进度的条目只标不挡**（`ToolContext.working_chapter` 的 docstring 讲了为什么
   是标不是挡）。硬挡会把「因为知道第 200 章会怎样才回头改第 40 章」挡在门外，
   而 ADR 0019 边界二自己已经**接受**了推理被污染这个残余代价。
   **注意事件轴是个例外，而它是被迫的**：`events_for_characters` 是 AS OF 查询，
   查询坐标由后端算，超过它的事件在 SQL 层就没出来。挡不掉就报出来——那条轴的
   `searched_through` 说的正是「我只数到第几章」，它和 `omitted`（装不下所以没列）
   是两件事。

── 一处诚实的成本说明 ────────────────────────────────────────────────────

L1 的正文命中轴**每次都要把整本书扫一遍**（一次正则 alternation，722 章约 2MB）。
不缓存是有意的：磁盘上的稿子随时在变（作者就在旁边的编辑器里打字，ADR 0007），
一份缓存的命中表会在他保存的那一刻变成一个**看起来正常的错误答案**——而这一层
全部的价值就是「确定性、当场算、答案永远是当前的」。真的贵到不能忍时该做的是
建索引表并让它跟着 `chapter.text_sha256` 失效，不是在这里加一个 dict。

── 边界一在这里的落点 ────────────────────────────────────────────────────

**花名册只给节点的正式名（`node.name`），一个别名都不给。** 这不是省 token：
别名里装着认知边界——「魔尊」是不是顾清音，正是某条秘密本身（ADR 0004 说别名差异
「是 canon，不是噪声」）。而秘密的非 canonical 别名就是它的内容 tell（`玄血蛊`），
把它交出去等于把检测器要找的词写进对话。所以这一层**只出正式名和纯量，`props` 一个
字段都不碰**（`tests/test_agent_tools.py` 的 AST 守卫会拦住 `.props`）。

── 花名册是**声明**出来的，不是从正文里数出来的 ────────────────────────────

它的成员是「作者建过节点的那些东西」（ADR 0004），所以一本刚 import 进来的 722 章长篇
花名册就是空的，而正文里当然有人。`roster_total: 0` 不带一句话交出去只有一个读法——
「这本书没有人物、没有秘密」，而模型会据此认为这一章怎么写都不违背设定。
同理，花名册按 (类型, 名字) 排序再从头收，`Secret` 结构上永远是第一批被预算裁掉的：
**只报「另外 28 条没给」不够，要报的是「Secret 这一类一条都没给到」**，
并且给得出把它单独拉回来的路（`labels`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..draft.length import DraftLanguage, count_units
from ..graph import CANONICAL_ALIAS_LABELS, InformationScope, Node, NodeLabel, Resolution
from ..importer import ChapterFile, chapter_files, chapter_path
from ..panel.constraints import forbidden_entities
from ..text import paragraphs as split_paragraphs
from ..text.mentions import compile_alternation, find_mentions
from .ports import ToolContext, ToolRefused

_LANGUAGE: Final = DraftLanguage.ZH
"""预算的计数口径。**中文按非空白字符数**（`draft/length.py` 是全库唯一定义）。

英文书上它比真实 token 数偏大，也就是偏紧——保守的方向，宁可少给一条也不要发出去被拒。
"""


# ══════════════════════════════════════════════════════════════════════════
# 预算：**从模型窗口倒推，这里一个魔法数都没有**
# ══════════════════════════════════════════════════════════════════════════
#
# 天花板是 `ToolContext.return_units`（它直接复用 `memory_units_available()`，
# 倒推只写一次）。下面三个函数只负责「拿到一个额度之后怎么花」，而它们**都返回
# 被丢掉的条数**——因为本仓的规矩是「限制了覆盖就要把丢掉的说出来」。


def _cost(model: BaseModel) -> int:
    """一个出参条目进对话要花多少字。**量的是序列化后的样子**，不是它的文本字段。

    只量 `summary` / `title` 会系统性低估：JSON 的键名和引号也占 token，而条目多的时候
    那部分不小。量真正要贴回对话的那串字，预算才是它自称的那个东西。
    """
    return count_units(model.model_dump_json(), _LANGUAGE)


_Item = TypeVar("_Item", bound=BaseModel)


def _take_oldest(items: Sequence[_Item], budget: int) -> tuple[list[_Item], int]:
    """从头往后收，收到预算用完。返回 `(留下的, 丢掉的条数)`。

    **至少收一条**（`kept and ...` 的短路）：预算为 0 时返回空表比返回一条更糟——
    调用方分不清「没有」和「装不下」，而这一层存在的理由就是不许这两种长得一样。
    """
    kept: list[_Item] = []
    spent = 0
    for item in items:
        cost = _cost(item)
        if kept and spent + cost > budget:
            break
        spent += cost
        kept.append(item)
    return kept, len(items) - len(kept)


def _take_newest(items: Sequence[_Item], budget: int) -> tuple[list[_Item], int]:
    """从**最新**的一端往回收，返回仍按原（升序）顺序。返回 `(留下的, 丢掉的条数)`。

    砍最旧的那一端，理由和 `draft/product_context.py` 的 `_take_from_newest` 是同一条：
    更早的东西对「接下来写什么」贡献最小。**它不是语义判断**——引擎没有读那些字，
    它只是在没有别的依据时选了一条说得出口的规则，并且把丢掉的说出来（ADR 0005）。
    """
    kept: list[_Item] = []
    spent = 0
    for item in reversed(items):
        cost = _cost(item)
        if kept and spent + cost > budget:
            break
        spent += cost
        kept.append(item)
    kept.reverse()
    return kept, len(items) - len(kept)


def _int_cost(values: Sequence[int]) -> int:
    return sum(len(str(value)) + 1 for value in values)


def _take_both_ends(values: Sequence[int], budget: int) -> tuple[list[int], int]:
    """章号列表按预算裁：**留两端，丢中间**。返回 `(留下的, 丢掉的条数)`。

    和上面两个不同的收法，理由是这个列表要回答的两个问题分别住在两端：
    「第一次是哪章」在头上，「最近一次」在尾上。从任意一端砍都会静默地毁掉其中一个，
    而这一层还额外把 `first` / `last` / `total` 作为**永不被裁的纯量**报出去——
    列表可以缺，那三个数不许缺。
    """
    if _int_cost(values) <= budget:
        return list(values), 0
    head: list[int] = []
    tail: list[int] = []
    spent = 0
    left, right = 0, len(values) - 1
    while left <= right:
        take_head = len(head) <= len(tail)
        pick = values[left] if take_head else values[right]
        cost = len(str(pick)) + 1
        if (head or tail) and spent + cost > budget:
            break
        spent += cost
        if take_head:
            head.append(pick)
            left += 1
        else:
            tail.append(pick)
            right -= 1
    tail.reverse()
    return head + tail, len(values) - len(head) - len(tail)


# ══════════════════════════════════════════════════════════════════════════
# 「这是你还没写到那儿之后的」—— 标，不挡
# ══════════════════════════════════════════════════════════════════════════


def _future_note(context: ToolContext, future_count: int) -> str | None:
    """整份返回上的那一句。**每条上还另有一个 `future` 布尔**，两个都要。

    每条一个，是给模型的：它逐条推理，要能在用到某一条的那一刻知道这条来自未来。
    整份一句，是给作者的：他在聊天里扫一眼就知道这一轮模型看了未来——
    **而「作者看得见」正是 ADR 0019 边界二接受这个残余代价的前提之一。**
    """
    if context.working_chapter is None:
        return (
            "不知道作者当前写到第几章，所以这一份里没有标出哪些来自「他还没写到的地方」——"
            "别默认返回里的章号都是已经发生的事。"
        )
    if future_count <= 0:
        return None
    return (
        f"这一份里有 {future_count} 条来自第 {context.future_from_chapter} 章及之后："
        f"作者现在写到第 {context.working_chapter} 章，**那些是他还没写到那儿的东西**。"
        "看可以，但别当成此刻已经成立的事，更别把它写进当前这一章的正文。"
    )


def _catalog(context: ToolContext) -> list[ChapterFile]:
    """磁盘上的章目录。`root_path` 缺席 → 空表（**不是「这本书没有章」**，由调用方说清楚）。"""
    if context.root_path is None:
        return []
    return chapter_files(Path(context.root_path))


def _rewritten_since(context: ToolContext, chapter: int, created_at: str | None) -> bool:
    """磁盘上第 `chapter` 章在 `created_at` 之后被改过没有。**查不了一律 `False`。**

    「查不了」和「没改过」在这里靠 `ChapterSummaries.stale_checked` 分开——那个布尔一旦
    是 `False`，整份返回里的 `may_be_stale` 就都不作数。合并成一个字段就等于把
    「我没查」显示成「没过期」，而那是这份索引最不该犯的那种错。

    比的是**磁盘的 mtime**，不是库里的哈希：摘要是从库里那份快照生成的，但作者读到的、
    以及 `chapter_text` 交出去的都是磁盘上那份（ADR 0007）。所以要问的是「模型手里这段
    摘要还配不配得上作者眼前那一章」。mtime 会因为一次没改内容的保存而前移——**误报的
    方向是「去读一眼原文」，漏报的方向是「拿着描述已经不存在的正文的摘要往下写」**，
    这两者不对称，所以选前者。
    """
    if context.root_path is None or not created_at:
        return False
    file = Path(context.root_path) / chapter_path(chapter)
    if not file.is_file():
        return False
    try:
        made = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return file.stat().st_mtime > made


# ══════════════════════════════════════════════════════════════════════════
# L0 目录
# ══════════════════════════════════════════════════════════════════════════


class BookIndexArgs(BaseModel):
    """全书目录：章标题 + 花名册。**默认调用不带参数，这是最便宜的那一层。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_chapter: int = Field(
        default=1,
        ge=1,
        description=(
            "章标题从第几章开始列（默认第 1 章）。预算装不下整本时返回里会写清楚"
            "给到第几章为止，把这个参数设成下一章再调一次就能接着往下拿。"
        ),
    )
    labels: list[str] | None = Field(
        default=None,
        description=(
            "只列这几类花名册条目（Character / Location / Faction / Secret / "
            "Foreshadow / Object），默认全给。花名册太长被裁掉整整一类时，"
            "用它把那一类单独拉回来。"
        ),
    )


class RosterEntry(BaseModel):
    """花名册里的一个东西：**只有正式名和类型**。别名一个都不给（见模块 docstring）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    label: str
    """`Character` / `Location` / `Faction` / `Secret` / `Foreshadow` / `Object`。"""

    first_appears_chapter: int | None = None
    """作者声明的首现章号，**且它晚于作者当前进度**（也就是「这东西还没登场」）。

    `None` 有两个意思，靠 `BookIndex.future_from_chapter` 区分：那个字段是 `None` 时
    表示这一轮压根没算首现（不知道作者写到第几章）；不是 `None` 时表示这东西已经登场了。
    数据来源是 `panel/constraints.forbidden_entities()`——**未来与秘密进 prompt 的唯一闸门**，
    这里不另开一条路。
    """

    future: bool = False
    """`True` = 作者还没写到它首现的那一章。**和 `ChapterEntry.future` 是同一个东西。**

    它不是上面那个字段的重复：`first_appears_chapter is None` 同时表示「已经登场」和
    「这一轮压根没算」，而模型是**逐条**推理的——要它每读一条花名册记录就回头去和
    `future_from_chapter` 对一次数，是把标记的责任推给了读的人。
    章目录那一半从第一天就有这个布尔，花名册这一半漏了它整整一轮。
    """


class ChapterEntry(BaseModel):
    """目录里的一行：章号 + 标题（磁盘上那一章的首个非空行）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    title: str = ""
    future: bool = False


class BookIndex(BaseModel):
    """`book_index` 的出参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roster: list[RosterEntry] = Field(default_factory=list)
    roster_total: int = 0
    """符合这次请求的花名册一共多少条（**不受预算影响**；`labels` 过滤后的总数）。"""

    roster_omitted: int = 0
    roster_labels_omitted: list[str] = Field(default_factory=list)
    """**一条都没给到**的那几类。

    「另外 28 条没给」不够：花名册按 (类型, 名字) 排序再从头收，于是 `Secret` 结构上
    永远是第一批掉出去的——而模型读到的是「这本书没有任何秘密」，然后据此认为这一章
    怎么写都不违背设定。**裁了什么要说的是「什么」，不只是「多少条」。**
    """

    chapters: list[ChapterEntry] = Field(default_factory=list)
    chapters_total: int = 0
    """磁盘上一共有多少章（**不受 `from_chapter` 和预算影响**）。"""

    chapters_through: int | None = None
    """标题给到第几章为止。`None` = 一条都没给。"""

    chapters_omitted: int = 0

    working_chapter: int | None = None
    future_from_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)
    """**裁了什么、瞎在哪儿、哪些是未来**——由上面那些纯量生成，不许手写第二份。"""


_ROSTER_LABELS: Final[frozenset[str]] = frozenset(
    str(label) for label in CANONICAL_ALIAS_LABELS
)
"""花名册里可能出现的全部类型。**取自 `CANONICAL_ALIAS_LABELS`，不另抄一份**——
花名册的成员判据就是「`upsert_node` 会不会给它建 canonical 别名」，那份集合一变这里跟着变。
"""


def _roster(context: ToolContext, labels: frozenset[str] | None) -> list[RosterEntry]:
    """花名册：全项目每个有 canonical 别名的节点一条，按 (类型, 名字) 排。

    走 `store.resolve(project_id)`（`surfaces=None` = 全项目花名册）。**按节点去重**，
    所以一个人的五个别名只出一条，出的还是他的正式名——别名不出现在返回里的任何位置。
    """
    future_first: dict[str, int] = {}
    if context.working_chapter is not None:
        future_first = {
            entity.node.id: entity.first_appears_chapter
            for entity in forbidden_entities(
                context.store, context.project_id, context.working_chapter
            )
        }

    seen: dict[str, RosterEntry] = {}
    for resolution in context.store.resolve(context.project_id):
        for hit in resolution.hits:
            node = hit.node
            if node.id in seen:
                continue
            if labels is not None and str(node.label) not in labels:
                continue
            seen[node.id] = RosterEntry(
                name=node.name,
                label=str(node.label),
                first_appears_chapter=future_first.get(node.id),
                # 判据就是「它在不在那个唯一闸门的输出里」——`forbidden_entities` 已经按
                # `first_appears_chapter > working_chapter` 筛过一遍，这里不再算第二次。
                future=node.id in future_first,
            )
    return sorted(seen.values(), key=lambda entry: (entry.label, entry.name))


def handle_book_index(args: BookIndexArgs, context: ToolContext) -> BookIndex:
    budget = context.return_units
    notes: list[str] = []

    wanted: frozenset[str] | None = None
    if args.labels is not None:
        unknown = sorted(set(args.labels) - _ROSTER_LABELS)
        if unknown:
            raise ToolRefused(
                f"labels 里有认不出来的类型：{'、'.join(unknown)}。"
                f"花名册只有这几类：{'、'.join(sorted(_ROSTER_LABELS))}。"
            )
        wanted = frozenset(args.labels)

    roster_all = _roster(context, wanted)
    roster, roster_omitted = _take_oldest(roster_all, budget)
    spent = sum(_cost(entry) for entry in roster)
    # 「哪一类一条都没给到」要从**这次请求的总体**里算，不是从给出去的那截里算。
    given_labels = {entry.label for entry in roster}
    roster_labels_omitted = sorted(
        {entry.label for entry in roster_all} - given_labels
    )
    if wanted is not None:
        notes.append(
            f"这一份的花名册只列了 {'、'.join(sorted(wanted))} 这几类："
            "别的类不在这一份里，**不代表书里没有**。"
        )
    if roster_omitted:
        notes.append(
            f"花名册太长：只给了按（类型, 名字）排序的前 {len(roster)} 条，"
            f"另外 {roster_omitted} 条这一轮没给。"
        )
    if roster_labels_omitted:
        notes.append(
            f"其中这几类**一条都没给到**：{'、'.join(roster_labels_omitted)}。"
            "要单独看某一类就再调一次，把 labels 设成比如 [\"Foreshadow\"]。"
        )
    if not roster_all:
        notes.append(
            (
                f"这本书里没有 {'、'.join(sorted(wanted))} 这几类的任何条目。"
                if wanted is not None
                else "花名册是空的：这本书还没有**声明**过任何人物 / 地点 / 门派 / 物件"
                "（也可能是这个 project_id 在库里查无此项）。"
            )
            + "**花名册是作者声明出来的，不是从正文里数出来的**——它空着不等于正文里没有"
            "这些人：正文里的角色只有被声明过才会进花名册，没进的这个引擎一无所知。"
        )

    catalog = _catalog(context)
    entries = [
        ChapterEntry(
            chapter=entry.number,
            title=entry.title,
            future=context.is_future(entry.number),
        )
        for entry in catalog
        if entry.number >= args.from_chapter
    ]
    chapters, chapters_omitted = _take_oldest(entries, max(0, budget - spent))

    if not catalog:
        notes.append(
            "读不到项目目录里的 chapters/NNNN.md，这一轮一条章标题都给不出来"
            "（正文的真相源是磁盘，不是数据库）——别把它读成「这本书还没有章」。"
            if context.root_path is None
            else "chapters/ 里没有 NNNN.md，这本书在磁盘上还一章都没有。"
        )
    elif args.from_chapter > catalog[-1].number:
        notes.append(
            f"from_chapter={args.from_chapter} 已经超过全书最后一章（第 {catalog[-1].number} 章）。"
        )
    if chapters_omitted:
        notes.append(
            f"章标题给到第 {chapters[-1].chapter} 章为止，后面还有 {chapters_omitted} 章没给："
            f"要接着往下拿就再调一次，把 from_chapter 设成 {chapters[-1].chapter + 1}。"
        )

    # **两半都要数。** 只数章目录的话，作者线性往下写（`chapters/` 里最远就是他写到的
    # 那一章）时未来条数恒为 0，于是整份返回上一句话都没有——而花名册里正躺着第 200 章
    # 的地点和第 300 章的秘密。ADR 0019 边界二把「推理被污染」列成**接受**的残余代价，
    # 它接受的前提之一是「作者看得见」，那一句话就是「看得见」的全部实现。
    future_note = _future_note(
        context,
        sum(1 for entry in chapters if entry.future) + sum(1 for entry in roster if entry.future),
    )
    if future_note:
        notes.append(future_note)

    return BookIndex(
        roster=roster,
        roster_total=len(roster_all),
        roster_omitted=roster_omitted,
        roster_labels_omitted=roster_labels_omitted,
        chapters=chapters,
        chapters_total=len(catalog),
        chapters_through=chapters[-1].chapter if chapters else None,
        chapters_omitted=chapters_omitted,
        working_chapter=context.working_chapter,
        future_from_chapter=context.future_from_chapter,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════
# L1 人物轴
# ══════════════════════════════════════════════════════════════════════════


class CharacterChaptersArgs(BaseModel):
    """某几个人出现在哪些章。**给多个人 = 求交集**（他们同时出现在哪几章）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    characters: list[str] = Field(
        min_length=1,
        description=(
            "人物的称呼，用作者在正文里的那个叫法。给两个就是问「这两个人同时出现在"
            "哪几章」——「萧决第一次见顾清音是哪章」问的就是这个。有歧义的叫法会被拒绝。"
        ),
    )


class ChapterAxis(BaseModel):
    """一条确定性的「在哪些章」轴。`first` / `last` / `total` **永不被预算裁掉**。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    """这条轴是怎么数出来的。两条轴回答的不是同一个问题，所以它在返回里明说。"""

    chapters: list[int] = Field(default_factory=list)
    total: int = 0
    first: int | None = None
    last: int | None = None
    omitted: int = 0
    """被预算裁掉的章数（裁的是**中间那段**，两端留着）。"""

    searched_through: int | None = None
    """这条轴**数到第几章为止**。`None` = 它瞎着，一章都没数。

    `omitted` 说的是「装不下所以没列」，它**不包括**「压根没查到那儿」——两者混在一起时
    `omitted=0` 会被读成「一条都没裁」，而实际上后半本书根本没进过这个查询。
    事件轴尤其要它：`events_for_characters` 是 AS OF 查询（`valid_from_chapter <= :ch`），
    那个 `:ch` 由后端算，读不到磁盘时它退化成作者的进度。
    """

    blind: bool = False
    """`True` = 这条轴今天查不了。**它和「一章都没命中」是两件事**，不许长得一样。"""

    reason: str = ""


class CharacterCoverage(BaseModel):
    """单个人在两条轴上各自的总数。**这两个数是用来拆「没同框」和「查不到」的。**

    `None` = 那条轴瞎着，这个人的数根本没数出来。**它和 0 不许长得一样**——
    交集为空时读者要能分清「他们没同框」和「我压根没数」，而这正是这一层存在的理由。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    mention_chapters: int | None = None
    event_chapters: int | None = None


class CharacterChapters(BaseModel):
    """`character_chapters` 的出参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    characters: list[CharacterCoverage] = Field(default_factory=list)
    mentioned_together: ChapterAxis
    events_together: ChapterAxis
    working_chapter: int | None = None
    future_from_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)


_MENTION_SOURCE: Final = (
    "正文里数出来的：这几个称呼同时在同一章出现过。它是超集——回忆里的死人、"
    "被议论的第三方也算，所以它不回答「谁在场」。"
)
_EVENT_SOURCE: Final = (
    "图上数出来的：同一条已确认（CANON）事件里这几个人同时是参与者或知情者。"
    "比正文命中准，但它依赖抽取跑过。"
)


# ══════════════════════════════════════════════════════════════════════════
# 称呼解析：**「查不到」和「说法不对」是两句话**（2026-08-13 实测）
# ══════════════════════════════════════════════════════════════════════════
#
# 这一段不属于索引的四层，它在这儿只有一个理由：`tools.py` 认得 `index.py`、反过来不行
# （反过来是环），而这句话必须只有一份。它以前有两份（`tools._handle_character_state`
# 和下面的 `_resolve_characters`），两份说的都是同一句错话。


class UnknownCharacter(ToolRefused):
    """这个称呼**花名册里根本没有**（`len(hits) == 0`）。

    ── 它为什么必须和「歧义」分成两个类型，而不是两条分支 ──────────────────

    2026-08-13 在作者 722 章的真书上实测：模型给第 723 章起草，连着查了两个大结局才
    出现的新角色（花名册里当然没有）。两次都拿到同一句

        「…解析不出唯一一个人（查无此人，或者这个叫法同时指向好几个人）。
          换一个更具体的称呼，或者先在人物卡上把别名理清楚。」

    ——**而这两种情况的正确下一步是相反的**：

    | 情况 | 判据（一个集合判断） | 该做什么 |
    |---|---|---|
    | 花名册里没有 | `len(hits) == 0` | **别再试**：换什么叫法都查不到 |
    | 一个叫法指向好几个人 | `len(hits) > 1` | 换个更具体的称呼（**重试是对的**） |

    合成一句「换个说法再试」= 在第一种情况下**由引擎亲口鼓励它再烧一步**。那一轮八步
    全花在查询上，一稿都没写出来，而作者看到的是几分钟的空白。**那句话本身就是那个 bug。**

    它是一个**类型**而不是一句话里的关键词，因为 `dispatch` 要把这一种数出来
    （`tools.TurnMemo`）。判「这条拒绝是不是『查不到』」如果靠在错误文本里找三个字，
    那就是拿字符串当协议，改一次措辞它就静默失效。
    """

    def __init__(self, surface: str) -> None:
        super().__init__(
            f"「{surface}」这个名字，这本书的花名册里没有。**别换个说法再查一次**——"
            "花名册是一份定死的名单（调 book_index 能看全），不在名单上的人，"
            "换什么叫法都查不到，再查一次只是白花一步。"
            "他要是这一场你新写的人，就当新人物直接往下写；"
            "要是作者写过他而系统还不认得，那得作者去人物卡上补，这一轮里等不到。"
        )
        self.surface = surface
        """模型填进来的那个称呼。**它只用来计数和复述，不参与任何查询。**"""


_AMBIGUOUS_SAMPLE: Final = 5
"""歧义时最多摆几个候选名字出来。「师兄」可以指向八个人，八个名字排开就成了一段散文，
而模型要的只是「这个叫法有歧义，挑一个具体的」——摆头几个够它挑了。

**这是一句话的长度上限，不是候选集的上限**（2026-08-22）：`mentioned.py` 的
`expand_ambiguous` 把八个候选**全部**算进 cast，那儿一个都不许截——判据是「在场至少有
一个人还不知道」，截掉第 6 个就是少算一个人，方向从 fail-closed 翻成 fail-open。
两处的「候选」是同一批人，但一处是说给模型听的措辞、另一处是禁令的依据。"""


def resolve_one(surface: str, resolution: Resolution | None) -> Node:
    """一个称呼 → 唯一那个节点。**解析不出就拒，绝不替作者猜一个。**

    `resolution=None` = 图层对这个称呼一行都没返回，和 `hits` 为空是同一件事
    （花名册里没有）。**两者不许分成两句话**：对模型来说它们的下一步完全相同，
    而多一种说法只会多一种它要去理解的东西。

    **这里不判 label**：「不是人物」的下一句话每个工具不一样（`character_state` 说
    「没有处境可查」，`character_chapters` 说「这儿只查人物的出场轴」），
    合并成一句就得说一句对两边都不够准的话。共用的只有上面那两种，它们的措辞与工具无关。
    """
    if resolution is None or not resolution.hits:
        raise UnknownCharacter(surface)
    node = resolution.unique_node
    if node is None:
        names = [hit.node.name for hit in resolution.hits[:_AMBIGUOUS_SAMPLE]]
        more = len(resolution.hits) - len(names)
        raise ToolRefused(
            f"「{surface}」这个叫法同时指向 {len(resolution.hits)} 个人"
            f"（{'、'.join(names)}{f'…… 等 {more} 个' if more else ''}）。"
            "**这一种换个说法是有用的**：挑其中一个的名字再查一次，或者用一个更具体的称呼。"
        )
    return node


def _resolve_characters(context: ToolContext, names: Sequence[str]) -> dict[str, str]:
    """称呼 → `{node_id: 正式名}`。解析不出唯一人物就拒，**绝不替作者猜一个**。

    **一次 `resolve` 收全部名字**（不是一个一个查）：这一层每多一次库往返都是作者在等，
    而 `resolve_one` 是纯函数，拿现成的那条 `Resolution` 判就行。
    """
    out: dict[str, str] = {}
    for resolution in context.store.resolve(context.project_id, list(names)):
        node = resolve_one(resolution.surface, resolution)
        if node.label is not NodeLabel.CHARACTER:
            raise ToolRefused(
                f"「{resolution.surface}」不是人物（它是 {node.label}）。这个工具只查人物的"
                "出场轴；地点不在这里问。"
            )
        out[node.id] = node.name
    return out


def _mention_axis(
    context: ToolContext,
    targets: dict[str, str],
    catalog: Sequence[ChapterFile],
    budget: int,
) -> tuple[ChapterAxis, dict[str, int | None]]:
    """正文 mention 轴 + 每个人各自命中了多少章（瞎着时是 `None`，不是 0）。

    用的是 `text/mentions.py` 那条 alternation（长度降序、最长优先），和 R2 FUTURE_LEAK
    同一份实现——**这里不重新数一遍**。只收 `rules_only=True` 的 surface，所以歧义的
    「师兄」天然出局（不是这里语义过滤，是它根本没进 alternation）。

    **别把 `mentioned.py` 的 `expand_ambiguous` 搬到这儿来**（2026-08-22）：那一侧算的是
    **禁令**，多算一个人只是多禁一条；这一侧是一条**查询**的答案（「这几个人在哪几章
    同时出现」），模型会把它当事实读——把八个候选全填进来是在回答里编造八条出场记录。
    同一个展开在一侧是 fail-closed，在另一侧是造假。
    """
    # 瞎着的时候留 `None`：0 会被读成「他一章都没出现」。
    per_character: dict[str, int | None] = dict.fromkeys(targets.values())
    if context.root_path is None or not catalog:
        return (
            ChapterAxis(
                source=_MENTION_SOURCE,
                blind=True,
                reason=(
                    "读不到磁盘上的正文，一个字都没扫（正文的真相源是磁盘）。"
                    if context.root_path is None
                    else "chapters/ 里一个章节文件都没有。"
                ),
            ),
            per_character,
        )

    owner: dict[str, str] = {}
    for resolution in context.store.resolve(context.project_id, None, rules_only=True):
        if resolution.hits and resolution.hits[0].node.id in targets:
            owner[resolution.surface] = resolution.hits[0].node.id
    matchable = set(owner.values())
    unmatchable = [name for node_id, name in targets.items() if node_id not in matchable]
    if unmatchable:
        # **静默的零在这里最危险**：一个称呼都不可用时命中数恒为 0，读起来像「他从没出现过」。
        return (
            ChapterAxis(
                source=_MENTION_SOURCE,
                blind=True,
                reason=(
                    f"这些人没有一个「可拿去匹配正文」的称呼：{'、'.join(unmatchable)}"
                    "（名字太短，或者它同时指向好几个人）。这条轴对他们恒为 0，"
                    "**那不是「没出现过」**。"
                ),
            ),
            per_character,
        )

    counted = dict.fromkeys(targets.values(), 0)
    pattern = compile_alternation(list(owner))
    root = Path(context.root_path)
    hits: list[int] = []
    for entry in catalog:
        file = root / entry.path
        if not file.is_file():
            continue
        paragraphs = split_paragraphs(file.read_text(encoding="utf-8-sig"))
        found = {owner[located.matched_text] for located in find_mentions(paragraphs, pattern)}
        for node_id in found:
            counted[targets[node_id]] += 1
        if targets.keys() <= found:
            hits.append(entry.number)
    return _axis(_MENTION_SOURCE, hits, budget, catalog[-1].number), dict(counted)


def _event_axis(
    context: ToolContext,
    targets: dict[str, str],
    catalog: Sequence[ChapterFile],
    budget: int,
) -> tuple[ChapterAxis, dict[str, int | None]]:
    """已确认事件轴 + 每个人各自有多少章带事件（瞎着时是 `None`，不是 0）。"""
    per_character: dict[str, int | None] = dict.fromkeys(targets.values())
    if context.events is None:
        return (
            ChapterAxis(
                source=_EVENT_SOURCE,
                blind=True,
                reason="已确认事件的读端还没接到这个会话上，这条轴这一轮查不了。",
            ),
            per_character,
        )

    # 查询坐标要盖住全书。**它是 AS OF 不是声明**（约束 10）：这个数不写进任何一行数据，
    # 这个模块也没有写路径。取磁盘上最后一章和作者进度里的大者——两个都不知道就没得查。
    horizon = max([entry.number for entry in catalog] + [context.working_chapter or 0])
    if horizon < 1:
        return (
            ChapterAxis(
                source=_EVENT_SOURCE,
                blind=True,
                reason=(
                    "既读不到正文、也不知道作者写到第几章，事件查询没有可用的查询坐标"
                    "（它按章号做时态过滤，得先知道要查到第几章）。"
                ),
            ),
            per_character,
        )

    views = context.events.events_for_characters(
        context.project_id,
        sorted(targets),
        horizon,
        InformationScope.CANON,
    )
    chapters: set[int] = set()
    per_node: dict[str, set[int]] = {node_id: set() for node_id in targets}
    for view in views:
        if view.event.information_scope is not InformationScope.CANON:
            continue
        involved = {ref.id for ref in view.participants} | {ref.id for ref in view.knowers}
        for node_id in involved & targets.keys():
            per_node[node_id].add(view.event.chapter_number)
        if targets.keys() <= involved:
            chapters.add(view.event.chapter_number)
    for node_id, seen in per_node.items():
        per_character[targets[node_id]] = len(seen)
    return _axis(_EVENT_SOURCE, sorted(chapters), budget, horizon), per_character


def _axis(
    source: str, hits: Sequence[int], budget: int, searched_through: int | None
) -> ChapterAxis:
    kept, omitted = _take_both_ends(hits, budget)
    return ChapterAxis(
        source=source,
        chapters=kept,
        total=len(hits),
        first=hits[0] if hits else None,
        last=hits[-1] if hits else None,
        omitted=omitted,
        searched_through=searched_through,
    )


def handle_character_chapters(
    args: CharacterChaptersArgs, context: ToolContext
) -> CharacterChapters:
    targets = _resolve_characters(context, args.characters)
    catalog = _catalog(context)
    # 两条轴各拿一半：它们是同一份返回里同时出现的两段，这是这一层唯一一次真正的「切」。
    budget = max(0, context.return_units // 2)

    mentioned, mention_counts = _mention_axis(context, targets, catalog, budget)
    events, event_counts = _event_axis(context, targets, catalog, budget)

    notes: list[str] = []
    if mentioned.blind:
        notes.append("正文命中轴瞎着：" + mentioned.reason)
    if events.blind:
        notes.append("已确认事件轴瞎着：" + events.reason)
    if not events.blind and not catalog and events.searched_through is not None:
        # 磁盘读得到时查询坐标就是全书最后一章，那句话是废话；读不到时它退化成作者的进度，
        # 于是「他已经写完、只是这一轮读不到」的那些章里的事件**一条都查不到**——
        # 而 `omitted` 在那种情况下仍然是 0，三个数字合起来是一个自信的错误答案。
        notes.append(
            f"已确认事件轴只查到第 {events.searched_through} 章为止"
            "（读不到磁盘上的稿子，查询坐标只能取作者当前的进度）："
            f"第 {events.searched_through + 1} 章及之后的已确认事件不在这条轴里，"
            "它的 total / first / last 也只在这个范围内成立。"
        )
    if not events.blind and events.total == 0 and all(
        count == 0 for count in event_counts.values()
    ):
        notes.append(
            "这几个人一条已确认事件都没有：可能是抽取还没跑过（那样整本书的事件轴都是空的），"
            "也可能确实没有。事件轴的 0 不等于正文里没写过——看正文命中轴。"
        )
    for axis, label in ((mentioned, "正文命中轴"), (events, "已确认事件轴")):
        if axis.omitted:
            notes.append(
                f"{label}命中 {axis.total} 章，预算只装得下 {len(axis.chapters)} 个章号，"
                f"**中间 {axis.omitted} 个被省掉了**（两端留着；first/last/total 是准的）。"
            )
    future_count = sum(
        1
        for axis in (mentioned, events)
        for chapter in axis.chapters
        if context.is_future(chapter)
    )
    future_note = _future_note(context, future_count)
    if future_note:
        notes.append(future_note)

    return CharacterChapters(
        characters=[
            CharacterCoverage(
                name=name,
                mention_chapters=mention_counts.get(name),
                event_chapters=event_counts.get(name),
            )
            for name in targets.values()
        ],
        mentioned_together=mentioned,
        events_together=events,
        working_chapter=context.working_chapter,
        future_from_chapter=context.future_from_chapter,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════
# L2 摘要
# ══════════════════════════════════════════════════════════════════════════


class ChapterSummariesArgs(BaseModel):
    """指定章号区间的滚动总结。**区间由你给**——你从 L0/L1 已经知道大概在哪儿了。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_chapter: int = Field(ge=1, description="区间下界（含）。")
    last_chapter: int = Field(ge=1, description="区间上界（含）。")

    @model_validator(mode="after")
    def _range_is_ordered(self) -> ChapterSummariesArgs:
        if self.last_chapter < self.first_chapter:
            raise ValueError("last_chapter 不能小于 first_chapter")
        return self


class ChapterSummaryEntry(BaseModel):
    """一章的机器摘要。**它不是作者确认的事实**，只是背景。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    summary: str
    future: bool = False

    may_be_stale: bool = False
    """磁盘上那一章在这段摘要生成**之后**被改过。**只在 `stale_checked` 为真时作数。**

    这一档比「缺摘要」更坏：缺摘要是索引在那一章**瞎**，而它是索引在那一章**说错**——
    模型手里唯一的「这一章讲了什么」描述的是一段已经不存在的正文。上游知道这件事
    （`SummaryStore.coverage()` 的 docstring：「它只问「有没有」不问「新不新」」），
    界面上作者对着正文看得见，而在这里模型只看得见摘要，所以这一层必须自己标。
    """


class ChapterSummaries(BaseModel):
    """`chapter_summaries` 的出参。**缺的那几态比有的那态更要紧。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_chapter: int
    last_chapter: int

    summaries: list[ChapterSummaryEntry] = Field(default_factory=list)
    summarized_total: int = 0
    omitted: int = 0
    """区间里有摘要、但预算装不下而没给的章数（丢的是**最早**的那些）。"""

    unsummarized: list[int] = Field(default_factory=list)
    """**有正文、但没生成过摘要**——索引在这几章是瞎的。作者点一下就能补。"""

    unsummarized_total: int = 0
    unsummarized_omitted: int = 0

    unindexed: list[int] = Field(default_factory=list)
    """**磁盘上有 `chapters/NNNN.md`，库里却没有当前快照**的章。

    这一档是 ADR 0007 的直接后果，而且它是**写作过程中的常态**：作者就在自己的编辑器里
    打字，新写的一章在 `sync` 之前只存在于磁盘上。把它并进 `unwritten` 会让模型跳过一整章
    **已经写好的正文**——而同一次会话里 `book_index` 列得出它的标题、`chapter_text` 读得出
    它的全文。三层互相打架，赢的还是最令人安心的那个说法。
    """

    unindexed_total: int = 0
    unindexed_omitted: int = 0

    unwritten: list[int] = Field(default_factory=list)
    """既没有正文快照、磁盘上也没有那个文件的章。和上面两条是三件事，别合并看。"""

    unwritten_total: int = 0
    unwritten_omitted: int = 0

    stale_checked: bool = False
    """这一轮有没有去核对「摘要是不是比正文旧」（读不到磁盘就核不了）。

    `False` 时 `may_be_stale` 全是 `False`，但那是**没查**不是**没过期**——
    合并成一个字段就等于把前者显示成后者。
    """

    stale_total: int = 0

    working_chapter: int | None = None
    future_from_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)


def handle_chapter_summaries(
    args: ChapterSummariesArgs, context: ToolContext
) -> ChapterSummaries:
    if context.summaries is None:
        raise ToolRefused(
            "章节摘要的读端还没接到这个会话上（工具表已经有它，实现还在 HTTP 路由里）。"
            "这一轮请改用 chapter_text 直接读正文，或者让作者从界面上看。"
        )
    budget = context.return_units
    catalog = _catalog(context)
    # **区间上界是模型填的，而 `coverage()` 会为区间里的每一章物化一行。**
    # `last_chapter=2_000_000` 实测吃掉约 1.1 GB 之后**成功返回**（出参只有 17 KB，
    # 因为预算是在这之后才裁的）——所以「预算就是那个上界」对**返回**成立，对**代价**
    # 不成立。而 `last_chapter=99999999` 正是模型想说「把全书摘要都给我」时最自然的写法。
    #
    # 上界不许是一个拍出来的数，它取两条各自成立的下界的**大者**：
    # ① 预算装得下多少个章号（一个章号至少占一个字，所以比这更长的区间**本来就报不全**）；
    # ② 这本书在磁盘上最远到第几章（预算为 0 的极端配置下不许把这一层弄瞎）。
    last_chapter = min(
        args.last_chapter,
        args.first_chapter + max(budget, catalog[-1].number if catalog else 0, 1) - 1,
    )
    rows = context.summaries.coverage(context.project_id, args.first_chapter, last_chapter)

    # **先花在「缺什么」上，再花在「有什么」上。** 缺的那三份是这一层会不会骗人的判据，
    # 而它们只是章号（一条几个字符），比摘要便宜一个量级——让它们排在后面被裁掉，
    # 等于用最便宜的东西换掉了唯一的诚实性保证。
    #
    # **`has_text` 是库的说法，正文的真相源是磁盘（ADR 0007）。** 两者不同解的那一刻恰恰是
    # 最常见的一刻：作者刚在自己的编辑器里写完一章，还没 sync。所以「库里没有」要先和
    # 磁盘对一遍，再决定它是「还没写」还是「写了还没同步进来」。
    # 复用上面那一次目录扫描：`chapter_files()` 要把每个章节文件都打开一次读标题，
    # 722 章扫两遍是白扫一遍。
    on_disk = {entry.number for entry in catalog}
    unsummarized_all = [row.chapter_number for row in rows if row.has_text and row.summary is None]
    missing = [row.chapter_number for row in rows if not row.has_text]
    unindexed_all = [number for number in missing if number in on_disk]
    unwritten_all = [number for number in missing if number not in on_disk]

    unsummarized, unsummarized_omitted = _take_both_ends(unsummarized_all, budget)
    spent = _int_cost(unsummarized)
    unindexed, unindexed_omitted = _take_both_ends(unindexed_all, max(0, budget - spent))
    spent += _int_cost(unindexed)
    unwritten, unwritten_omitted = _take_both_ends(unwritten_all, max(0, budget - spent))
    spent += _int_cost(unwritten)

    stale_checked = context.root_path is not None
    have = [
        ChapterSummaryEntry(
            chapter=row.chapter_number,
            summary=row.summary,
            future=context.is_future(row.chapter_number),
            may_be_stale=_rewritten_since(context, row.chapter_number, row.created_at),
        )
        for row in rows
        if row.summary is not None
    ]
    kept, omitted = _take_newest(have, max(0, budget - spent))
    stale_total = sum(1 for entry in have if entry.may_be_stale)

    notes: list[str] = []
    if last_chapter < args.last_chapter:
        notes.append(
            f"你问的是第 {args.first_chapter}–{args.last_chapter} 章，这一轮**只查到第 "
            f"{last_chapter} 章**（一次问得再长也报不全，而每一章都要真的去查一遍）："
            f"要接着往下拿就再问一次，把 first_chapter 设成 {last_chapter + 1}。"
        )
    if unsummarized_all:
        notes.append(
            f"第 {args.first_chapter}–{last_chapter} 章里有 {len(unsummarized_all)} 章"
            "**有正文但没生成过摘要**——索引在那几章是瞎的，那不是「那几章什么都没发生」。"
            "要补就请作者在界面上生成（会调模型、会花钱，所以引擎不替他按）。"
        )
    if unindexed_all:
        notes.append(
            f"另有 {len(unindexed_all)} 章**磁盘上有正文、库里还没有当前快照**"
            "（作者刚在自己的编辑器里写的，还没同步进来）：chapter_text 现在就读得到它们，"
            "只是同步之前引擎生成不了摘要。**别把它们读成「还没写」。**"
        )
    if unwritten_all:
        notes.append(
            f"另有 {len(unwritten_all)} 章在这个区间里**还没有正文**：库里没有当前快照，"
            "磁盘上也没有那个文件（还没写到那儿）。"
            if stale_checked
            else f"另有 {len(unwritten_all)} 章库里没有当前正文快照。**这一轮读不到磁盘上的"
            "稿子，所以分不出「还没写到那儿」和「写了但还没同步进来」**——别当成前者。"
        )
    if omitted:
        notes.append(
            f"区间里有 {len(have)} 章带摘要，预算只装得下 {len(kept)} 章，"
            f"**最早的 {omitted} 章没给**（要它们就把区间往前挪一段再问一次）。"
        )
    for count, label in (
        (unsummarized_omitted, "没摘要的章号"),
        (unindexed_omitted, "磁盘上有、库里没有的章号"),
        (unwritten_omitted, "没正文的章号"),
    ):
        if count:
            notes.append(f"{label}列表也裁了：中间 {count} 个没列出来，总数是准的。")
    if stale_total:
        notes.append(
            f"这一份里有 {stale_total} 段摘要比它那一章的正文旧：那几章在摘要生成之后又被"
            "**改过**，摘要说的可能已经不是磁盘上现在那一章了。要拿它下结论就先用 "
            "chapter_text 读一眼原文。"
        )
    elif have and not stale_checked:
        notes.append(
            "读不到磁盘上的稿子，所以这一轮**没检查**摘要是不是比正文旧——"
            "别把「没标」读成「没过期」。"
        )

    future_note = _future_note(context, sum(1 for entry in kept if entry.future))
    if future_note:
        notes.append(future_note)

    return ChapterSummaries(
        first_chapter=args.first_chapter,
        # **报的是真的查过的那一段，不是模型要的那一段。** 回显请求会让下面每一个
        # `*_total` 变成一句谎话（「第 1–2000000 章里有 3 章没摘要」）。
        last_chapter=last_chapter,
        summaries=kept,
        summarized_total=len(have),
        omitted=omitted,
        unsummarized=unsummarized,
        unsummarized_total=len(unsummarized_all),
        unsummarized_omitted=unsummarized_omitted,
        unindexed=unindexed,
        unindexed_total=len(unindexed_all),
        unindexed_omitted=unindexed_omitted,
        unwritten=unwritten,
        unwritten_total=len(unwritten_all),
        unwritten_omitted=unwritten_omitted,
        stale_checked=stale_checked,
        stale_total=stale_total,
        working_chapter=context.working_chapter,
        future_from_chapter=context.future_from_chapter,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════
# L3 全文
# ══════════════════════════════════════════════════════════════════════════


class ChapterTextArgs(BaseModel):
    """一章正文。**最贵的那一层**，钻到这儿之前先用上面三层定位。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1, description="读第几章的正文。")


class ChapterFullText(BaseModel):
    """`chapter_text` 的出参。正文**从磁盘读**（ADR 0007：DB 永远不是正文的真相源）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int
    text: str
    units: int
    """全章有多少字（`count_units` 口径）。**截断前的真值。**"""

    units_given: int
    truncated: bool
    future: bool = False
    working_chapter: int | None = None
    notes: list[str] = Field(default_factory=list)


def _truncate_units(text: str, budget: int) -> tuple[str, bool]:
    """按字数截断，**从头留**。返回 `(留下的, 截没截)`。"""
    if count_units(text, _LANGUAGE) <= budget:
        return text, False
    kept: list[str] = []
    used = 0
    for character in text:
        if not character.isspace():
            if used >= budget:
                break
            used += 1
        kept.append(character)
    return "".join(kept), True


def handle_chapter_text(args: ChapterTextArgs, context: ToolContext) -> ChapterFullText:
    if context.root_path is None:
        raise ToolRefused(
            "读不到项目目录，正文取不出来 —— 正文的真相源是磁盘上的 chapters/NNNN.md，"
            "数据库里那份只是派生索引（ADR 0007）。"
        )
    relative = chapter_path(args.chapter)
    file = Path(context.root_path) / relative
    if not file.is_file():
        raise ToolRefused(
            f"第 {args.chapter} 章在磁盘上没有正文（{relative} 不存在）——"
            "作者还没写到那儿，或者那一章不在这个项目里。"
        )
    text = file.read_text(encoding="utf-8-sig")
    given, truncated = _truncate_units(text, context.return_units)

    notes: list[str] = []
    if truncated:
        notes.append(
            f"这一章一共 {count_units(text, _LANGUAGE)} 字，预算只装得下 "
            f"{count_units(given, _LANGUAGE)} 字，**只给了开头那一段，后面截掉了**。"
        )
    future_note = _future_note(context, 1 if context.is_future(args.chapter) else 0)
    if future_note:
        notes.append(future_note)

    return ChapterFullText(
        chapter=args.chapter,
        text=given,
        units=count_units(text, _LANGUAGE),
        units_given=count_units(given, _LANGUAGE),
        truncated=truncated,
        future=context.is_future(args.chapter),
        working_chapter=context.working_chapter,
        notes=notes,
    )


__all__ = [
    "BookIndex",
    "BookIndexArgs",
    "ChapterAxis",
    "ChapterEntry",
    "ChapterFullText",
    "ChapterSummaries",
    "ChapterSummariesArgs",
    "ChapterSummaryEntry",
    "ChapterTextArgs",
    "CharacterChapters",
    "CharacterChaptersArgs",
    "CharacterCoverage",
    "RosterEntry",
    "handle_book_index",
    "handle_chapter_summaries",
    "handle_chapter_text",
    "handle_character_chapters",
]
