from __future__ import annotations

import hashlib
import math

import pytest

import novel_harness.extract.locate as locate_module
from novel_harness.extract.locate import LocateOutcome, locate_quote
from novel_harness.text.anchor import find_one


def test_exact_match_has_precedence_over_fuzzy_candidates() -> None:
    quote = "abcdefghijklmnopqrst"
    result = locate_quote(["abcdeXghijklmnoYqrst", quote], quote)

    assert result.outcome is LocateOutcome.EXACT
    assert result.ratio == 1.0
    assert result.located is not None
    assert result.located.para_index == 1
    assert result.located.matched_text == quote


def test_repeated_exact_match_is_ambiguous_and_never_chosen() -> None:
    quote = "这一句原文足够长，可以作为精确证据引语。"
    result = locate_quote([quote, "前缀" + quote], quote)

    assert result.outcome is LocateOutcome.AMBIGUOUS
    assert result.ratio == 1.0
    assert result.located is None


def test_unique_fuzzy_match_at_threshold_is_accepted_inclusively() -> None:
    source = "abcdefghijklmnopqrst"
    quote = "abcdeXghijklmnoYqrst"

    result = locate_quote([source], quote)

    assert result.outcome is LocateOutcome.FUZZY
    assert result.ratio == pytest.approx(0.90)
    assert result.located is not None
    assert result.located.matched_text == source


def test_same_length_window_finds_quote_inside_a_long_sentence() -> None:
    source = "abcdefghijklmnopqrst"
    quote = "abcdeXghijklmnoYqrst"
    paragraph = "无关开场文字" * 4 + source + "无关收尾文字" * 4

    result = locate_quote([paragraph], quote)

    assert result.outcome is LocateOutcome.FUZZY
    assert result.located is not None
    assert result.located.matched_text == source


def test_best_match_just_below_threshold_is_preserved_as_discard_reason() -> None:
    source = "a" * 199
    quote = "a" * 179 + "b" * 20

    result = locate_quote([source], quote)

    assert result.outcome is LocateOutcome.BELOW_THRESHOLD
    assert 0.899 < result.ratio < 0.90
    assert result.located is None


def test_equal_best_fuzzy_locations_are_ambiguous() -> None:
    source = "abcdefghijklmnopqrst"
    quote = "abcdeXghijklmnoYqrst"

    result = locate_quote([source, source], quote)

    assert result.outcome is LocateOutcome.AMBIGUOUS
    assert result.ratio == pytest.approx(0.90)
    assert result.located is None


def test_equal_best_locations_below_threshold_report_threshold_rejection() -> None:
    source = "a" * 199
    quote = "a" * 179 + "b" * 20

    result = locate_quote([source, source], quote)

    assert result.outcome is LocateOutcome.BELOW_THRESHOLD
    assert 0.899 < result.ratio < 0.90
    assert result.located is None


def test_sentence_span_handles_chinese_punctuation_and_an_insertion() -> None:
    source_sentence = "顾清音抬眸望向窗外，细雨声淹没了她未出口的话。"
    quote = "顾清音抬眸望向窗外，雨声淹没了她未出口的话。"
    paragraph = "风急。" + source_sentence + "灯灭。"

    result = locate_quote([paragraph], quote)

    assert result.outcome is LocateOutcome.FUZZY
    assert result.ratio > 0.90
    assert result.located is not None
    assert result.located.matched_text == source_sentence


def test_fuzzy_location_is_a_verifiable_source_substring_and_hash_input() -> None:
    source = "abcdefghijklmnopqrst"
    quote = "abcdeXghijklmnoYqrst"
    paragraphs = ["前一段", source, "后一段"]

    result = locate_quote(paragraphs, quote)

    assert result.located is not None
    located = result.located
    assert located.matched_text in paragraphs[located.para_index]
    assert (
        find_one(
            paragraphs[located.para_index],
            located.matched_text,
            located.occurrence_k,
        )
        == located.matched_text
    )
    assert (
        hashlib.sha256(located.matched_text.encode()).digest()
        == hashlib.sha256(source.encode()).digest()
    )
    assert (
        hashlib.sha256(located.matched_text.encode()).digest()
        != hashlib.sha256(quote.encode()).digest()
    )


def test_no_source_candidates_is_not_found() -> None:
    result = locate_quote(["", ""], "不存在但长度足够的候选引语")

    assert result.outcome is LocateOutcome.NOT_FOUND
    assert result.ratio == 0.0
    assert result.located is None


@pytest.mark.parametrize("quote", [""])
def test_empty_quote_is_rejected(quote: str) -> None:
    with pytest.raises(ValueError, match="quote"):
        locate_quote(["正文"], quote)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, math.nan, math.inf, -math.inf, True, "0.9"])
def test_invalid_threshold_is_rejected_loudly(threshold: object) -> None:
    with pytest.raises((TypeError, ValueError), match="min_ratio"):
        locate_quote(["正文"], "足够长的一段待定位引语", min_ratio=threshold)  # type: ignore[arg-type]


def test_moderate_chapter_location_is_deterministic() -> None:
    paragraphs = [f"第{i:03d}段。" + "山雨欲来风满楼，故人提剑下扬州。" * 12 for i in range(40)]
    quote = "山雨欲来风满楼，旧人提剑下扬州。"

    first = locate_quote(paragraphs, quote)
    second = locate_quote(paragraphs, quote)

    assert first == second


def test_repeated_candidate_text_is_scored_once_without_anchor_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "abcdefghijklmnopqrst"
    quote = "abcdeXghijklmnoYqrst"
    sequence_matcher_calls = 0
    occurrence_calls = 0
    real_sequence_matcher = locate_module.SequenceMatcher
    real_occurrences = locate_module._occurrences_by_start

    def counted_sequence_matcher(*args: object, **kwargs: object):
        nonlocal sequence_matcher_calls
        sequence_matcher_calls += 1
        return real_sequence_matcher(*args, **kwargs)

    def counted_occurrences(para: str, text: str) -> dict[int, int]:
        nonlocal occurrence_calls
        occurrence_calls += 1
        return real_occurrences(para, text)

    monkeypatch.setattr(locate_module, "SequenceMatcher", counted_sequence_matcher)
    monkeypatch.setattr(locate_module, "_occurrences_by_start", counted_occurrences)

    result = locate_quote([source] * 10_000, quote)

    assert result.outcome is LocateOutcome.AMBIGUOUS
    assert sequence_matcher_calls == 1
    assert occurrence_calls == 0
