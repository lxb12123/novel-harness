"""Deterministic exact-first location of untrusted evidence quotes."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import chain
from numbers import Real

from pydantic import BaseModel, ConfigDict, Field

from ..text.anchor import Located, find_all

__all__ = ["LocateOutcome", "LocateResult", "locate_quote"]


class LocateOutcome(StrEnum):
    EXACT = "EXACT"
    FUZZY = "FUZZY"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


class LocateResult(BaseModel):
    """A location or the explicit reason that no safe location was selected."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    outcome: LocateOutcome
    located: Located | None = None
    ratio: float = Field(ge=0, le=1)


@dataclass(frozen=True, slots=True)
class _Candidate:
    para_index: int
    start: int
    end: int
    text: str


_SENTENCE_END = re.compile(r"[。！？!?；;.]+")


def _trimmed_span(para: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and para[start].isspace():
        start += 1
    while end > start and para[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _sentence_spans(para: str) -> Iterator[tuple[int, int]]:
    start = 0
    for delimiter in _SENTENCE_END.finditer(para):
        span = _trimmed_span(para, start, delimiter.end())
        if span is not None:
            yield span
        start = delimiter.end()
    span = _trimmed_span(para, start, len(para))
    if span is not None:
        yield span


def _occurrences_by_start(para: str, text: str) -> dict[int, int]:
    """Mirror anchor.find_one's non-overlapping occurrence numbering."""
    result: dict[int, int] = {}
    pos = 0
    occurrence_k = 0
    while (start := para.find(text, pos)) != -1:
        result[start] = occurrence_k
        occurrence_k += 1
        pos = start + len(text)
    return result


def _fuzzy_candidates(paras: Sequence[str], quote_length: int) -> Iterator[_Candidate]:
    seen_locations: set[tuple[int, int, int]] = set()
    for para_index, para in enumerate(paras):
        spans: Iterator[tuple[int, int]] = _sentence_spans(para)
        windows = (
            ((start, start + quote_length) for start in range(len(para) - quote_length + 1))
            if len(para) >= quote_length
            else iter(())
        )
        for start, end in chain(spans, windows):
            location = (para_index, start, end)
            if location in seen_locations:
                continue
            seen_locations.add(location)
            text = para[start:end]
            if not text:
                continue
            yield _Candidate(
                para_index=para_index,
                start=start,
                end=end,
                text=text,
            )


def _validate_inputs(quote: str, min_ratio: float) -> None:
    if not quote:
        raise ValueError("quote must not be empty")
    if isinstance(min_ratio, bool) or not isinstance(min_ratio, Real):
        raise TypeError("min_ratio must be a finite number from 0 through 1")
    if not math.isfinite(float(min_ratio)) or not 0 <= min_ratio <= 1:
        raise ValueError("min_ratio must be a finite number from 0 through 1")


def locate_quote(paragraphs: Sequence[str], quote: str, min_ratio: float = 0.90) -> LocateResult:
    """Locate a quote exactly, then by a unique deterministic source candidate."""
    _validate_inputs(quote, min_ratio)

    exact = find_all(paragraphs, quote)
    if len(exact) == 1:
        return LocateResult(
            outcome=LocateOutcome.EXACT,
            located=exact[0],
            ratio=1.0,
        )
    if len(exact) > 1:
        return LocateResult(outcome=LocateOutcome.AMBIGUOUS, ratio=1.0)

    ratio_cache: dict[str, float] = {}
    candidates_by_ratio: dict[float, list[_Candidate]] = {}
    for candidate in _fuzzy_candidates(paragraphs, len(quote)):
        ratio = ratio_cache.get(candidate.text)
        if ratio is None:
            ratio = SequenceMatcher(None, quote, candidate.text).ratio()
            ratio_cache[candidate.text] = ratio
        candidates_by_ratio.setdefault(ratio, []).append(candidate)

    if not candidates_by_ratio:
        return LocateResult(outcome=LocateOutcome.NOT_FOUND, ratio=0.0)

    for best_ratio in sorted(candidates_by_ratio, reverse=True):
        best = candidates_by_ratio[best_ratio]
        if best_ratio < min_ratio:
            return LocateResult(
                outcome=LocateOutcome.BELOW_THRESHOLD,
                ratio=best_ratio,
            )
        if len(best) > 1:
            return LocateResult(outcome=LocateOutcome.AMBIGUOUS, ratio=best_ratio)

        candidate = best[0]
        occurrence_k = _occurrences_by_start(paragraphs[candidate.para_index], candidate.text).get(
            candidate.start
        )
        if occurrence_k is None:
            continue
        return LocateResult(
            outcome=LocateOutcome.FUZZY,
            located=Located(
                para_index=candidate.para_index,
                occurrence_k=occurrence_k,
                matched_text=candidate.text,
            ),
            ratio=best_ratio,
        )

    return LocateResult(outcome=LocateOutcome.NOT_FOUND, ratio=0.0)
