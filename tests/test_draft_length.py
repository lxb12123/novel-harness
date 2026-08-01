"""Pure bilingual draft-length rules and policy."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from novel_harness import draft
from novel_harness.draft.length import (
    COUNTING_RULE_VERSION,
    DEFAULT_LENGTH_POLICY,
    DraftLanguage,
    LengthMeasurement,
    LengthPolicy,
    LengthSpec,
    LengthStatus,
    count_units,
    measure,
)


def test_count_units_counts_every_non_whitespace_code_point_in_chinese() -> None:
    assert count_units("你 好，\nGPT-5！", DraftLanguage.ZH) == 9


def test_count_units_chinese_includes_punctuation_latin_digits_and_supplementary_chars() -> None:
    assert count_units("你 \t好，A9\n𠀀！", DraftLanguage.ZH) == 7


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("don't stop", 2),
        ("state-of-the-art GPT-5", 2),
        ("café 2026!", 2),
        ("'don't' -state- -GPT-5-", 3),
        ("東京 Ⅷ ٣٤ café", 4),
    ],
)
def test_count_units_english_uses_unicode_letter_number_words(
    text: str, expected: int
) -> None:
    assert count_units(text, DraftLanguage.EN) == expected


def test_count_units_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="unsupported draft language"):
        count_units("bonjour", "fr")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("minimum", "target", "maximum"),
    [(3, 2, 4), (2, 4, 3), (2, 3, 2)],
)
def test_length_spec_rejects_invalid_range_ordering(
    minimum: int, target: int, maximum: int
) -> None:
    with pytest.raises(ValidationError, match="min_units <= target_units <= max_units"):
        LengthSpec(
            language=DraftLanguage.ZH,
            min_units=minimum,
            target_units=target,
            max_units=maximum,
        )


def test_length_spec_derives_its_unit_and_rejects_caller_supplied_unit() -> None:
    spec = LengthSpec(language=DraftLanguage.EN, min_units=2, target_units=3, max_units=4)

    assert spec.unit == "words"
    with pytest.raises(ValidationError):
        LengthSpec(
            language=DraftLanguage.ZH,
            min_units=2,
            target_units=3,
            max_units=4,
            unit="words",  # type: ignore[call-arg]
        )


def test_measure_classifies_exact_boundaries() -> None:
    spec = LengthSpec(language=DraftLanguage.EN, min_units=2, target_units=3, max_units=4)

    assert measure("one", spec).status is LengthStatus.UNDER
    assert measure("one two", spec).status is LengthStatus.WITHIN
    assert measure("one two three four", spec).status is LengthStatus.WITHIN
    assert measure("one two three four five", spec).status is LengthStatus.OVER


def test_length_models_are_frozen() -> None:
    spec = LengthSpec(language=DraftLanguage.ZH, min_units=2, target_units=3, max_units=4)
    measurement = LengthMeasurement(
        language=DraftLanguage.ZH,
        unit="characters",
        actual_units=3,
        status=LengthStatus.WITHIN,
    )

    with pytest.raises(ValidationError):
        spec.min_units = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        measurement.actual_units = 0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DEFAULT_LENGTH_POLICY.zh_max_chars = 1  # type: ignore[misc]


def test_default_policy_has_exact_defaults_and_enforces_hard_maximums() -> None:
    policy = DEFAULT_LENGTH_POLICY

    assert policy.zh_default == LengthSpec(
        language=DraftLanguage.ZH, min_units=2000, target_units=2500, max_units=3000
    )
    assert policy.en_default == LengthSpec(
        language=DraftLanguage.EN, min_units=1200, target_units=1500, max_units=1800
    )
    assert policy.default_for(DraftLanguage.ZH) is policy.zh_default
    assert policy.default_for(DraftLanguage.EN) is policy.en_default

    at_limit = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=10000, max_units=20000)
    assert policy.validate_spec(at_limit) is at_limit
    too_long = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=10000, max_units=20001)
    with pytest.raises(ValueError, match="hard maximum"):
        policy.validate_spec(too_long)

    en_at_limit = LengthSpec(
        language=DraftLanguage.EN, min_units=1, target_units=6000, max_units=12000
    )
    assert policy.validate_spec(en_at_limit) is en_at_limit
    en_too_long = LengthSpec(
        language=DraftLanguage.EN, min_units=1, target_units=6000, max_units=12001
    )
    with pytest.raises(ValueError, match="hard maximum"):
        policy.validate_spec(en_too_long)


def test_policy_rejects_defaults_of_the_wrong_language_or_above_hard_maximum() -> None:
    english = LengthSpec(language=DraftLanguage.EN, min_units=1, target_units=2, max_units=3)
    chinese = LengthSpec(language=DraftLanguage.ZH, min_units=1, target_units=2, max_units=3)

    with pytest.raises(ValidationError, match="zh_default.language"):
        LengthPolicy(zh_default=english)
    with pytest.raises(ValidationError, match="en_default.language"):
        LengthPolicy(en_default=chinese)
    with pytest.raises(ValidationError, match="hard maximum"):
        LengthPolicy(
            zh_default=LengthSpec(
                language=DraftLanguage.ZH, min_units=1, target_units=2, max_units=20001
            )
        )


def test_policy_from_env_applies_all_supported_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_DRAFT_ZH_DEFAULT_MIN_CHARS", "20")
    monkeypatch.setenv("NH_DRAFT_ZH_DEFAULT_TARGET_CHARS", "25")
    monkeypatch.setenv("NH_DRAFT_ZH_DEFAULT_MAX_CHARS", "30")
    monkeypatch.setenv("NH_DRAFT_ZH_HARD_MAX_CHARS", "300")
    monkeypatch.setenv("NH_DRAFT_EN_DEFAULT_MIN_WORDS", "12")
    monkeypatch.setenv("NH_DRAFT_EN_DEFAULT_TARGET_WORDS", "15")
    monkeypatch.setenv("NH_DRAFT_EN_DEFAULT_MAX_WORDS", "18")
    monkeypatch.setenv("NH_DRAFT_EN_HARD_MAX_WORDS", "180")

    policy = LengthPolicy.from_env()

    assert policy.zh_default == LengthSpec(
        language=DraftLanguage.ZH, min_units=20, target_units=25, max_units=30
    )
    assert policy.en_default == LengthSpec(
        language=DraftLanguage.EN, min_units=12, target_units=15, max_units=18
    )
    assert policy.zh_max_chars == 300
    assert policy.en_max_words == 180


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("NH_DRAFT_ZH_DEFAULT_MIN_CHARS", "many", "must be an integer"),
        ("NH_DRAFT_EN_DEFAULT_MIN_WORDS", "2000", "min_units <= target_units <= max_units"),
        ("NH_DRAFT_ZH_HARD_MAX_CHARS", "2999", "hard maximum"),
    ],
)
def test_policy_from_env_fails_as_a_whole_for_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, match: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises((ValidationError, ValueError), match=match):
        LengthPolicy.from_env()


def test_m2_length_spec_never_reads_product_environment_on_first_import() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "NH_DRAFT_ZH_DEFAULT_MIN_CHARS": "20",
            "NH_DRAFT_ZH_DEFAULT_TARGET_CHARS": "25",
            "NH_DRAFT_ZH_DEFAULT_MAX_CHARS": "30",
            "NH_DRAFT_ZH_HARD_MAX_CHARS": "300",
        }
    )
    script = (
        "from novel_harness.draft.length import M2_LENGTH_SPEC as spec; "
        "print(spec.language.value, spec.min_units, spec.target_units, spec.max_units)"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.stdout.strip() == "zh 2000 2500 3000"
    assert COUNTING_RULE_VERSION == "nh-length-v1"


def test_package_export_does_not_shadow_length_submodule() -> None:
    length_module = importlib.import_module("novel_harness.draft.length")

    assert draft.length is length_module
    assert not callable(draft.length)
