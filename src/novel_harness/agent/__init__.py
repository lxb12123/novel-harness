"""模式二（agent harness）的编排层 —— **工具表就是权限边界**（ADR 0019 边界一）。

几个文件，职责按「谁认识谁」切开（这个方向不许反过来，反过来就成环）：

| 文件 | 装什么 |
|---|---|
| `ports.py` | 工具能碰到的全部东西：`ToolContext` + 三个注入口 + 两个只读端口。**它不认识任何工具。** |
| `candidates.py` | 候选稿的存取（ADR 0022）：起草的产物落在这儿，**不落在书里，也不落在对话里**。 |
| `index.py` | 书内索引的四层（目录 / 人物轴 / 摘要 / 正文）。收 `ToolContext`，不碰工具表。 |
| `tools.py` | 工具表本身 + 派发 + 约束与起草那三条。**加工具只在这儿加。** |
| `rules.py` | **作者定下的规矩**（ADR 0023 决策二）：存一条 / 数重复 / 按章号过期 / 撤销。它不认识工具表，也不碰会话。 |
| `loop.py` | 循环归模型、停止条件归代码 + 按章号参数化的投影（边界五）+ **边跑边发的事件**（ADR 0024）。 |
| `store.py` | 会话表的读写。**判据只有一条：读回来的 `Conversation` 和存进去之前逐字节相同。** |
| `model.py` | `ModelPort` 的适配器：把作者按下的「停」带进流式循环。 |

认识 `db` / `draft.provider` 这两个外面的东西的只有 `store.py` / `model.py` /
`candidates.py`（还有把三者装起来的 `drafting.py`）——**这一层的可测性靠的就是那条线**
（工具和 loop 全是纯函数加注入口）。

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
from .candidates import DraftCandidate, DraftCandidateStore, StoredDraft
from .ports import (
    DraftAsk,
    DraftDesk,
    DraftFn,
    DraftProduct,
    EventIndex,
    LandingReport,
    SummaryIndex,
    ToolContext,
    ToolRefused,
)
from .store import ChatConcurrency, ChatNotice, ChatSessionRow, ChatStore, StoredChat
from .tools import (
    TOOL_NAMES,
    TOOL_TABLE,
    TOOLS,
    AskAuthorArgs,
    AskAuthorResult,
    AuthorQuestion,
    BatchRunner,
    CharacterStateArgs,
    CharacterStateResult,
    ConstraintsResult,
    DraftFullText,
    DraftIdArgs,
    DraftResult,
    LandingResult,
    RememberedRule,
    RememberRuleArgs,
    RememberRuleResult,
    SceneConstraintsArgs,
    StateFact,
    ToolOutcome,
    ToolSpec,
    dispatch,
    dispatch_all,
    tool_declarations,
    tool_label,
)
from .loop import EventFn, TurnEvent, TurnEventKind, safe_emitter

# **摆出来 + 能取消**（ADR 0023 决策二）：这两样是 HTTP 壳要的读端和写端。
# `rules` 排在 `loop` 后面是因为它 import 得着 `loop`（canonical 的形状住在那儿）。

__all__ = [
    "TOOLS",
    "TOOL_NAMES",
    "TOOL_TABLE",
    "AskAuthorArgs",
    "AskAuthorResult",
    "AuthorQuestion",
    "BatchRunner",
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
    "ChatNotice",
    "ChatSessionRow",
    "ChatStore",
    "CharacterChapters",
    "CharacterChaptersArgs",
    "CharacterCoverage",
    "CharacterStateArgs",
    "CharacterStateResult",
    "ConstraintsResult",
    "DraftAsk",
    "DraftCandidate",
    "DraftCandidateStore",
    "DraftDesk",
    "DraftFn",
    "DraftFullText",
    "DraftIdArgs",
    "DraftProduct",
    "DraftResult",
    "EventFn",
    "EventIndex",
    "LandingReport",
    "LandingResult",
    "RememberRuleArgs",
    "RememberRuleResult",
    "RememberedRule",
    "RosterEntry",
    "SceneConstraintsArgs",
    "StateFact",
    "StoredChat",
    "StoredDraft",
    "SummaryIndex",
    "ToolContext",
    "ToolOutcome",
    "ToolRefused",
    "ToolSpec",
    "TurnEvent",
    "TurnEventKind",
    "dispatch",
    "dispatch_all",
    "safe_emitter",
    "tool_declarations",
    "tool_label",
]
