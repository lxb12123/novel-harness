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
ANALYSIS_PROMPT_VERSION: Final = "chapter-analysis-prompt-v6"

_SYSTEM_PROMPT: Final = f"""You extract structured facts from one supplied novel chapter.
Schema version: {ANALYSIS_SCHEMA_VERSION}. Prompt version: {ANALYSIS_PROMPT_VERSION}.

Return JSON only: one JSON object, with no Markdown fences and no prose before or after it.
Use exactly these top-level keys: events, state_updates, character_profiles.

Extract events at story-beat granularity. Return 1-12 events, never scene-sized summaries.

Every event is exactly this object:
{{"summary": string, "quote": string, "participants": [surface names],
  "knowers": [surface names], "revealed_facts": [short factual statements],
  "confidence": number from 0 through 1}}

Every state update is exactly this object:
{{"kind": "location" or "state" or "relationship", "subject": surface name,
  "object": surface name or null, "dimension": string or null,
  "value": string or null, "quote": string, "confidence": number from 0 through 1}}
The "kind" field is required. Use exactly one of these three shapes:
- "kind": "location": subject + object; dimension and value must be null or omitted.
- "kind": "state": subject + dimension + value; object must be null or omitted.
- "kind": "relationship": subject + object + value; dimension must be null or omitted.
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
