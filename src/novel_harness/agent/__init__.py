"""模式二（agent harness）的编排层 —— **工具表就是权限边界**（ADR 0019 边界一）。

三个文件，职责按「谁认识谁」切开（这个方向不许反过来，反过来就成环）：

| 文件 | 装什么 |
|---|---|
| `ports.py` | 工具能碰到的全部东西：`ToolContext` + 三个注入口 + 两个只读端口。**它不认识任何工具。** |
| `index.py` | 书内索引的四层（目录 / 人物轴 / 摘要 / 正文）。收 `ToolContext`，不碰工具表。 |
| `tools.py` | 工具表本身 + 派发 + 约束与起草那三条。**加工具只在这儿加。** |
| `loop.py` | 循环归模型、停止条件归代码 + 按章号参数化的投影（边界五）。 |
| `store.py` | 会话表的读写。**判据只有一条：读回来的 `Conversation` 和存进去之前逐字节相同。** |
| `model.py` | `ModelPort` 的适配器：把作者按下的「停」带进流式循环。 |

只有 `store.py` / `model.py` 认识 `db` / `draft.provider` 这两个外面的东西，别的三个不认识——
**这一层的可测性靠的就是那条线**（工具和 loop 全是纯函数加注入口）。

浏览器那一侧（对话面板）是 3.5，今天还没有；HTTP 壳在 `api/chat.py`。

顺序是有意的，ADR 0019「若此决策错误，修复成本」那一节写死了理由：**换编排形状便宜，
边界一写错最贵而且不可回收**——工具一旦把 `Node` 交出去过，那段秘密已经在作者的持久化
对话里了，改代码删不掉。所以先有网（`tests/test_agent_tools.py`）再有工具。
"""

from __future__ import annotations

from .index import (
    BookIndex,
    BookIndexArgs,
    ChapterAxis,
    ChapterEntry,
    ChapterFullText,
    ChapterSummaries,
    ChapterSummariesArgs,
    ChapterSummaryEntry,
    ChapterTextArgs,
    CharacterChapters,
    CharacterChaptersArgs,
    CharacterCoverage,
    RosterEntry,
)
from .ports import (
    DraftAsk,
    DraftFn,
    EventIndex,
    SummaryIndex,
    ToolContext,
    ToolRefused,
)
from .store import ChatConcurrency, ChatSessionRow, ChatStore, StoredChat
from .tools import (
    TOOL_NAMES,
    TOOL_TABLE,
    TOOLS,
    CharacterStateArgs,
    CharacterStateResult,
    ConstraintsResult,
    DraftResult,
    ForbiddenName,
    SceneConstraintsArgs,
    StateFact,
    ToolOutcome,
    ToolSpec,
    dispatch,
    dispatch_all,
    tool_declarations,
)

__all__ = [
    "TOOLS",
    "TOOL_NAMES",
    "TOOL_TABLE",
    "BookIndex",
    "BookIndexArgs",
    "ChapterAxis",
    "ChapterEntry",
    "ChapterFullText",
    "ChapterSummaries",
    "ChapterSummariesArgs",
    "ChapterSummaryEntry",
    "ChapterTextArgs",
    "ChatConcurrency",
    "ChatSessionRow",
    "ChatStore",
    "CharacterChapters",
    "CharacterChaptersArgs",
    "CharacterCoverage",
    "CharacterStateArgs",
    "CharacterStateResult",
    "ConstraintsResult",
    "DraftAsk",
    "DraftFn",
    "DraftResult",
    "EventIndex",
    "ForbiddenName",
    "RosterEntry",
    "SceneConstraintsArgs",
    "StateFact",
    "StoredChat",
    "SummaryIndex",
    "ToolContext",
    "ToolOutcome",
    "ToolRefused",
    "ToolSpec",
    "dispatch",
    "dispatch_all",
    "tool_declarations",
]
