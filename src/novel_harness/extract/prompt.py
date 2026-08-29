"""结构化章节分析的稳定 prompt 构造。"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisMessage",
    "build_analysis_messages",
]


ANALYSIS_SCHEMA_VERSION: Final = "chapter-analysis-v1"
ANALYSIS_PROMPT_VERSION: Final = "chapter-analysis-prompt-v7"
"""v7（2026-08-25）：拿掉 `revealed_facts`。秘密下线之后它无处可解（ADR 0039）。

⚠️ **改这段 prompt 的正文会改 `prompt_hash`，而 `prompt_hash` 是 `extraction_run`
的唯一键的一部分**——改一个字 = 全书每一章都得重新调一次模型。别为了措辞好看改它。

── 📌 下次真要改这段 prompt 时，**顺手把下面这条一起带上** ──────────────

**「状态维度的名字用正文的语言写。」**

2026-08-27 实测（作者真书三章，`state`/`location` 放开「认不出就建」之后）：模型新建
了 9 个维度，其中两个是**英文**——`mental state`、`true status`，而正文和其余七个
维度名都是中文。人物卡（`StateCards.tsx`）直接渲染维度名，于是作者会看到

    身体状况       重伤
    mental state   惊惧      ← 中英混排
    武功境界       三阶

这是模型的输出习惯，不是引擎的 bug——**一句话就能治，但那句话要付上面那个代价**
（作者那本 158 章的书重新买一整轮）。维护者 2026-08-27 裁定：**先放着，攒到下次
真有理由动 prompt 时捎上。**

**记在这儿而不是别处**：改 prompt 的人一定会读这个文件，而这条只有在那一刻才值得做。
"""

_SYSTEM_PROMPT: Final = f"""You extract structured facts from one supplied novel chapter.
Schema version: {ANALYSIS_SCHEMA_VERSION}. Prompt version: {ANALYSIS_PROMPT_VERSION}.

Return JSON only: one JSON object, with no Markdown fences and no prose before or after it.
Use exactly these top-level keys: events, state_updates, character_profiles.

Extract events at story-beat granularity. Return 1-12 events, never scene-sized summaries.

Every event is exactly this object:
{{"summary": string, "quote": string, "participants": [surface names],
  "knowers": [surface names], "confidence": number from 0 through 1}}

Every state update is exactly this object:
{{"kind": "location" or "state" or "relationship" or "death", "subject": surface name,
  "object": surface name or null, "dimension": string or null,
  "value": string or null, "quote": string, "confidence": number from 0 through 1}}
The "kind" field is required. Use exactly one of these four shapes:
- "kind": "location": subject + object; dimension and value must be null or omitted.
- "kind": "state": subject + dimension + value; object must be null or omitted.
- "kind": "relationship": subject + object + value; dimension must be null or omitted.
- "kind": "death": subject only; object, dimension and value must be null or omitted.
Use "death" when the chapter states that the subject dies, is killed, or is confirmed
dead. Use it for the death itself, not for someone merely fearing, predicting, or
falsely reporting a death, and not for a character who is only wounded or unconscious.
Do not express a death as a "state" update with a dimension of your own wording.
Return no more than 24 state updates.

Every event and state update must include a verbatim quote of no more than 120
characters copied from the supplied chapter. Never paraphrase a quote. There is no
minimum quote length: short verbatim utterances are fine. Quotes shorter than 10
characters may make up at most 30% of the chapter's quotes (at least one); otherwise
quote a longer verbatim sentence from the chapter that contains the utterance.

Use surface names from the chapter for participants, knowers, profile surfaces, subjects,
and objects. Never invent or return business IDs, chapter identifiers or numbers, scope,
status, evidence identifiers, or any other server-owned field.

A character profile is exactly this object:
{{"surface": string, "gender": string or null, "personality": string or null,
  "background": string or null, "character_notes": string or null,
  "confidence": number from 0 through 1}}
"""


class AnalysisMessage(TypedDict):
    role: Literal["system", "user"]
    content: str


def build_analysis_messages(chapter_text: str) -> list[AnalysisMessage]:
    """返回确定性的消息序列，章节原文逐字节作为文本包含在内。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": chapter_text},
    ]
