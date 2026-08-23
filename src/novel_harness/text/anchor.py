"""锚点 —— 「什么是一段」和「这段引语在哪」的**唯一定义**（ADR 0006）。

`para_index` 是证据锚的第一个分量，而它今天在本仓库有**两个潜在产地**：
`api/app.py` 里喂给 `CheckContext.paragraphs` 的那一份，和 `evidence.para_index`。
两份定义 = R4 的 Issue 锚到隔壁段落，且是「偶尔差一两段」那种查两周的形态——
ADR 0006 对 offset 的判词一字不差地适用。**这个模块存在的理由就是把那个数收敛成一个。**

这一层不认识数据库（见 `text/__init__.py`），也不认识哈希：`quote_sha256` 的两个消费者
（`evidence.quote_sha256` / `chapter.text_sha256`）都在 `graph/sqlite_store.py`，
它自己 import `decisions.quote_hash`。本模块 import `decisions` 就等于 import 了 `db`。

── 只做精确匹配。这是裁决，不是遗漏 ──────────────────────────────────────

ADR 0006 配套第 2 条的 `difflib` + `ratio < 0.9 丢弃`，是给「LLM 抽取器返回的 quote 有
10–30% 对不上原文」准备的。M1 的 quote 只有一个来源：**作者从自己稿子里复制粘贴**。
给一个不存在的消费者写阈值参数 = 给它一个哪天忘了它是干什么的机会（ADR 0005 增长规则）。
更实际的：MIN_RATIO 调低一格 = 锚到隔壁那句 = `valid_from` 错一章，而它在面板上长得完全正常。

M4 抽取器落地时，模糊定位是**加法**：`Located.matched_text` 的形状现在就已经是对的
（它是切出来的子串，不是传进来的 quote），那天只需要让 `find_all` 多一条模糊分支。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Located", "find_all", "find_one", "occurrence_at", "paragraphs"]


class Located(BaseModel):
    """一次命中。三个分量恰好是 ADR 0006 的锚：`(para_index, quote_text, occurrence_k)`。
    **没有 offset，将来也不许有。**
    """

    model_config = ConfigDict(frozen=True)

    para_index: int = Field(ge=0)
    """0-based，`paragraphs()` 返回的列表下标。"""

    occurrence_k: int = Field(ge=0)
    """0-based，**在这一段之内**的第 k 次出现（不是全章第 k 次）。"""

    matched_text: str = Field(min_length=1)
    """**从正文里切出来的那个子串，不是调用方传进来的 quote。**
    M1 精确匹配下两者逐字节相等，所以这条今天看着是废话；M4 的模糊路径上它们不等，
    而 `quote_sha256` 必须对这一个取（ADR 0006 配套第 3 条：反过来做的话锚从第一天起就是坏的）。
    调用方今天就该拿这个字段，不该拿自己那份 quote——形状现在对了，M4 才是加法。
    """


def paragraphs(text: str) -> list[str]:
    """一章正文 → 段落列表。**`para_index` 全库唯一的定义。**

    今天的实现逐字节等于 `text.splitlines()`。这个函数存在的理由不是它做了什么，
    是它让那个下标只有一处定义：`api/app.py` 喂进 `CheckContext.paragraphs` 的那一份和
    `evidence.para_index` 必须永远是同一个含义。

    **别顺手改成按空行分段。** 那会静默改变 `para_index` 的含义——R4 的现有测试全绿着，
    库里已有的证据锚会集体偏移，而没有任何东西会报错。真要改，先让两个消费者一起改。
    """
    return text.splitlines()


def _occurrences(para: str, quote: str) -> Iterator[int]:
    """`quote` 在 `para` 里逐次出现的起点。**「第 k 次」全库只在这里定义一次。**

    **非重叠**（命中后从 `idx + len(quote)` 继续），与 `str.count()` 语义一致：
    `_occurrences("aaa", "aa")` 只给 1 个。`find_all` / `find_one` 对「第 k 次」意见
    不一致的产物是一条锚到别处的证据，所以它们共用这一个生成器，不许各写一遍。
    """
    pos = 0
    while (idx := para.find(quote, pos)) != -1:
        yield idx
        pos = idx + len(quote)


def _require_quote(quote: str) -> None:
    # 空串的「出现次数」是无限，`str.find` 每次都命中且 pos 不前进 → 死循环。
    if not quote:
        raise ValueError("quote 不能为空串：空串的「第 k 次出现」没有定义")


def find_all(paras: Sequence[str], quote: str) -> list[Located]:
    """在全部段落里精确定位 `quote`，按 (para_index, occurrence_k) 顺序返回全部命中。

    返回空列表 = 找不到。**返回多条时调用方不许替作者挑一条**（§5.9：挑错的产物是
    一条 `valid_from` 错了的 CANON 边，而它在面板上长得完全正常）。
    """
    _require_quote(quote)
    return [
        Located(para_index=i, occurrence_k=k, matched_text=para[idx : idx + len(quote)])
        for i, para in enumerate(paras)
        for k, idx in enumerate(_occurrences(para, quote))
    ]


def occurrence_at(para: str, quote: str, start: int) -> int | None:
    """`para[start:]` 上那一次命中是**第几次**出现（`Located.occurrence_k` 的口径）。

    调用方手上已经知道位置、只缺那个 k 时用它——**不许自己数一遍**。「第 k 次」是
    非重叠计数（`_occurrences`），自己数出来的那个 k 在
    `para="他走了。走了。", quote="走了。"` 上就已经和这里不一致，而症状是
    一条锚到隔壁半句的证据，在面板上长得完全正常。

    Returns:
        `start` 不是一次出现的起点（非重叠计数下被前一次吃掉了）时返回 `None`。
        调用方该把这一条丢掉，**不要退回 0** —— 退回 0 就是锚到别处。
    """
    _require_quote(quote)
    for k, idx in enumerate(_occurrences(para, quote)):
        if idx == start:
            return k
        if idx > start:
            break
    return None

def find_one(para: str, quote: str, occurrence_k: int) -> str | None:
    """单段内取第 k 次出现，返回**切出来的子串**；k 越界返回 `None`。

    `None` 而不是抛：调用方（`put_evidence`）拿它当「快照对不上锚」的判据，
    para_index 越界与 k 越界在那里是同一种失败（`QuoteMismatch`）。
    """
    _require_quote(quote)
    for k, idx in enumerate(_occurrences(para, quote)):
        if k == occurrence_k:
            return para[idx : idx + len(quote)]
    return None
