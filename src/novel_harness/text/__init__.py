"""纯文本层：切章 / 分词 / 提及 / 锚点 / 场景块（PLAN §9 骨架）。

这一层**不认识数据库**，也不该认识：输入是一个 `str`，输出是 Pydantic。
`text/` 里出现 `import sqlite3` 或图表名的那一天，`tests/test_arch_guard.py` 会红。

| 模块 | 内容 |
|---|---|
| `anchor` | 「什么是一段」+「这段引语在哪」—— `para_index` 的唯一定义 |
| `chapterize` | 切章 —— `Chapter.index` 是全书全序键，`state_at` 的那个 `:ch` 的产地 |
| ~~`scenes`~~ | **2026-08-14 删了**（[ADR 0027](../../../docs/adr/0027-scene-blocks-cut.md)），连同它唯一的消费者 R4 |

`CHAPTER_RE` 在这里再出口是**故意的**（`chapterize` 的 docstring：正则是唯一真相，
`scripts/probe_speaker_tags.py` 从那里 import，不留第二份副本）。
`normalize` 同理要出口：它是喂正则之前的必经步骤，藏起来只会让第二个调用方跳过它，
而跳过它的代价是 CRLF / BOM 那两个各一字符宽的 index 偏移 bug。

── ⚠️ 这儿曾经有一个「M3 会踩到的 import 环」的警告，它跟着 `scenes` 一起没了 ────

那个环是 `text/scenes` → `..checks.base`（`Scene` 定义在消费者一侧），靠
`checks/__init__` 恰好先 import `location_conflict` 才没炸——**一个 import 语句的顺序，
不是一条约束**。2026-08-14 两头一起删掉之后，`text/` 重新变成一层纯下游：
它今天不 import `checks/` 里的任何东西，环连成立的条件都没有了。
**别再把任何 `checks/` 的类型 import 进这一层来。**
"""

from __future__ import annotations

from .anchor import (
    Located,
    find_all,
    find_one,
    paragraphs,
)
from .chapterize import (
    CHAPTER_RE,
    Chapter,
    Chapterization,
    chapterize,
    chapters,
    normalize,
)
__all__ = [
    "CHAPTER_RE",
    "Chapter",
    "Chapterization",
    "Located",
    "chapterize",
    "chapters",
    "find_all",
    "find_one",
    "normalize",
    "paragraphs",
]
