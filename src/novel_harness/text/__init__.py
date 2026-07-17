"""纯文本层：切章 / 分词 / 提及 / 锚点 / 场景块（PLAN §9 骨架）。

这一层**不认识数据库**，也不该认识：输入是一个 `str`，输出是 Pydantic。
`text/` 里出现 `import sqlite3` 或图表名的那一天，`tests/test_arch_guard.py` 会红。

| 模块 | 内容 |
|---|---|
| `chapterize` | 切章 —— `Chapter.index` 是全书全序键，`state_at` 的那个 `:ch` 的产地 |
| `scenes` | `## 场景 N` + `<!-- nh: ... -->` 的解析与写回 —— `Scene` 的唯一生产者 |

`CHAPTER_RE` 在这里再出口是**故意的**（`chapterize` 的 docstring：正则是唯一真相，
`scripts/probe_speaker_tags.py` 从那里 import，不留第二份副本）。
`normalize` 同理要出口：它是喂正则之前的必经步骤，藏起来只会让第二个调用方跳过它，
而跳过它的代价是 CRLF / BOM 那两个各一字符宽的 index 偏移 bug。

── ⚠️ M3 会在这里踩一个 import 环，别在那天才第一次读到这段 ────────────────

`scenes` → `..checks.base`（`Scene` 定义在消费者一侧，见那份 docstring）。方向是反的——
文本层在 checks 之下——但今天无环：`checks/` 只 import `graph/`。

`checks/base.py:126` 已经 TODO 了「R2/R3/R5 在 M3 import `text/mentions.py`」。**那一行落地
的当天，这个文件下面的 `from .scenes import ...` 就是环的另一半**：`checks/__init__` 跑到
一半 → import `text` → `text/__init__` 跑 `from .scenes` → `scenes` import 一个半成品的
`novel_harness.checks`。今天它侥幸活着，靠的是 `checks/__init__` 恰好先 import 了
`location_conflict`（它把 `.base` 拉了进来）——**一个 import 语句的顺序，不是一条约束。**

M3 真被绊倒时的正确修法**不是**把这里的再出口删掉（那只是把环藏进调用方），是把
`Scene` 挪进 `text/`、让 `checks/` import 它。那与 `Scene` docstring 里「定义在消费者
这一侧」的论证冲突，所以留给 M3 连同 `mentions.py` 一起决定，不在 Wire 阶段单方面动。
"""

from __future__ import annotations

from .chapterize import (
    CHAPTER_RE,
    Chapter,
    Chapterization,
    chapterize,
    chapters,
    normalize,
)
from .scenes import (
    KEEP,
    AmbiguousScene,
    MalformedDirective,
    SceneNotFound,
    SceneWriteRefused,
    UnwritableValue,
    parse_scenes,
    write_scene_directive,
)

__all__ = [
    "CHAPTER_RE",
    "KEEP",
    "AmbiguousScene",
    "Chapter",
    "Chapterization",
    "MalformedDirective",
    "SceneNotFound",
    "SceneWriteRefused",
    "UnwritableValue",
    "chapterize",
    "chapters",
    "normalize",
    "parse_scenes",
    "write_scene_directive",
]
