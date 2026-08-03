"""Stable prompt construction for structured chapter analysis."""

from __future__ import annotations

from typing import Final, Literal, TypedDict

__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisMessage",
    "build_analysis_messages",
]


ANALYSIS_SCHEMA_VERSION: Final = "chapter-analysis-v1"
ANALYSIS_PROMPT_VERSION: Final = "chapter-analysis-prompt-v1"

_SYSTEM_PROMPT: Final = f"""You extract structured facts from one supplied novel chapter.
Schema version: {ANALYSIS_SCHEMA_VERSION}. Prompt version: {ANALYSIS_PROMPT_VERSION}.

Return JSON only: one JSON object, with no Markdown fences and no prose before or after it.
Use exactly these top-level keys: events, state_updates, character_profiles.

Extract events at story-beat granularity. Return 1-12 events, never scene-sized summaries.
Every event and state update must include a verbatim 10-120 character quote copied from
the supplied chapter. Never paraphrase a quote.

Each state update must use exactly one of these shapes:
- location: subject + object; dimension and value must be null or omitted.
- state: subject + dimension + value; object must be null or omitted.
- relationship: subject + object + value; dimension must be null or omitted.
Return no more than 24 state updates.

Use surface names from the chapter for participants, knowers, profile surfaces, subjects,
and objects. Never invent or return business IDs, chapter identifiers or numbers, scope,
status, evidence identifiers, or any other server-owned field.

An event has summary, quote, participants, knowers, revealed_facts, confidence.
A character profile has surface, gender, personality, background, character_notes,
confidence. Confidence is a finite number from 0 through 1.
"""


class AnalysisMessage(TypedDict):
    role: Literal["system", "user"]
    content: str


def build_analysis_messages(chapter_text: str) -> list[AnalysisMessage]:
    """Return deterministic messages containing the chapter byte-for-byte as text."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": chapter_text},
    ]
