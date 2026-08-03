"""Deterministic bilingual length measurement for drafted prose."""

from __future__ import annotations

import os
import unicodedata
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


COUNTING_RULE_VERSION = "nh-length-v1"


class DraftLanguage(StrEnum):
    """Supported draft languages; callers must select one explicitly."""

    ZH = "zh"
    EN = "en"


class LengthStatus(StrEnum):
    UNDER = "under"
    WITHIN = "within"
    OVER = "over"


class LengthSpec(BaseModel):
    """Requested draft length, in the unit uniquely implied by its language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    language: DraftLanguage
    min_units: int = Field(ge=1)
    target_units: int = Field(ge=1)
    max_units: int = Field(ge=1)

    @model_validator(mode="after")
    def _has_ordered_bounds(self) -> Self:
        if not self.min_units <= self.target_units <= self.max_units:
            raise ValueError("min_units <= target_units <= max_units is required")
        return self

    @property
    def unit(self) -> Literal["characters", "words"]:
        return "characters" if self.language is DraftLanguage.ZH else "words"


class LengthMeasurement(BaseModel):
    """The actual measured length and its comparison with a ``LengthSpec``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    language: DraftLanguage
    unit: Literal["characters", "words"]
    actual_units: int = Field(ge=0)
    status: LengthStatus

    @model_validator(mode="after")
    def _has_language_unit_pair(self) -> Self:
        expected = "characters" if self.language is DraftLanguage.ZH else "words"
        if self.unit != expected:
            raise ValueError(f"{self.language.value} measurements use {expected}")
        return self


def _language(value: DraftLanguage | str) -> DraftLanguage:
    try:
        return DraftLanguage(value)
    except ValueError as exc:
        raise ValueError("unsupported draft language; expected 'zh' or 'en'") from exc


def _is_word_char(character: str) -> bool:
    return unicodedata.category(character)[:1] in {"L", "N"}


def _count_english_words(text: str) -> int:
    words = 0
    index = 0
    connectors = {"'", "’", "-"}
    text_length = len(text)

    while index < text_length:
        if not _is_word_char(text[index]):
            index += 1
            continue

        words += 1
        index += 1
        while index < text_length:
            if _is_word_char(text[index]):
                index += 1
                continue
            if (
                text[index] in connectors
                and index + 1 < text_length
                and _is_word_char(text[index - 1])
                and _is_word_char(text[index + 1])
            ):
                index += 1
                continue
            break
    return words


def count_units(text: str, language: DraftLanguage | str) -> int:
    """Count Chinese characters or English words using only the documented rules."""
    selected = _language(language)
    if selected is DraftLanguage.ZH:
        return sum(not character.isspace() for character in text)
    return _count_english_words(text)


def measure(text: str, spec: LengthSpec) -> LengthMeasurement:
    """Measure ``text`` using ``spec`` and classify it against its hard bounds."""
    actual_units = count_units(text, spec.language)
    if actual_units < spec.min_units:
        status = LengthStatus.UNDER
    elif actual_units > spec.max_units:
        status = LengthStatus.OVER
    else:
        status = LengthStatus.WITHIN
    return LengthMeasurement(
        language=spec.language,
        unit=spec.unit,
        actual_units=actual_units,
        status=status,
    )


class LengthPolicy(BaseModel):
    """Configurable length defaults together with non-negotiable upper bounds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    zh_default: LengthSpec = Field(
        default_factory=lambda: LengthSpec(
            language=DraftLanguage.ZH, min_units=2000, target_units=2500, max_units=3000
        )
    )
    en_default: LengthSpec = Field(
        default_factory=lambda: LengthSpec(
            language=DraftLanguage.EN, min_units=1200, target_units=1500, max_units=1800
        )
    )
    zh_max_chars: int = Field(default=20000, ge=1)
    en_max_words: int = Field(default=12000, ge=1)

    @model_validator(mode="after")
    def _has_valid_defaults(self) -> Self:
        if self.zh_default.language is not DraftLanguage.ZH:
            raise ValueError("zh_default.language must be DraftLanguage.ZH")
        if self.en_default.language is not DraftLanguage.EN:
            raise ValueError("en_default.language must be DraftLanguage.EN")
        if self.zh_default.max_units > self.zh_max_chars:
            raise ValueError("zh_default exceeds the zh hard maximum")
        if self.en_default.max_units > self.en_max_words:
            raise ValueError("en_default exceeds the en hard maximum")
        return self

    def default_for(self, language: DraftLanguage | str) -> LengthSpec:
        selected = _language(language)
        return self.zh_default if selected is DraftLanguage.ZH else self.en_default

    def validate_spec(self, spec: LengthSpec) -> LengthSpec:
        hard_maximum = self.zh_max_chars if spec.language is DraftLanguage.ZH else self.en_max_words
        if spec.max_units > hard_maximum:
            raise ValueError(
                f"{spec.language.value} length spec exceeds hard maximum of {hard_maximum} {spec.unit}"
            )
        return spec

    @classmethod
    def from_env(cls) -> Self:
        """Build one complete policy from the eight supported environment variables."""
        defaults = cls()
        zh_minimum = _read_env_int("NH_DRAFT_ZH_DEFAULT_MIN_CHARS", defaults.zh_default.min_units)
        zh_target = _read_env_int("NH_DRAFT_ZH_DEFAULT_TARGET_CHARS", defaults.zh_default.target_units)
        zh_maximum = _read_env_int("NH_DRAFT_ZH_DEFAULT_MAX_CHARS", defaults.zh_default.max_units)
        zh_hard_maximum = _read_env_int("NH_DRAFT_ZH_HARD_MAX_CHARS", defaults.zh_max_chars)
        en_minimum = _read_env_int("NH_DRAFT_EN_DEFAULT_MIN_WORDS", defaults.en_default.min_units)
        en_target = _read_env_int("NH_DRAFT_EN_DEFAULT_TARGET_WORDS", defaults.en_default.target_units)
        en_maximum = _read_env_int("NH_DRAFT_EN_DEFAULT_MAX_WORDS", defaults.en_default.max_units)
        en_hard_maximum = _read_env_int("NH_DRAFT_EN_HARD_MAX_WORDS", defaults.en_max_words)
        return cls(
            zh_default=LengthSpec(
                language=DraftLanguage.ZH,
                min_units=zh_minimum,
                target_units=zh_target,
                max_units=zh_maximum,
            ),
            en_default=LengthSpec(
                language=DraftLanguage.EN,
                min_units=en_minimum,
                target_units=en_target,
                max_units=en_maximum,
            ),
            zh_max_chars=zh_hard_maximum,
            en_max_words=en_hard_maximum,
        )


def _read_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


DEFAULT_LENGTH_POLICY = LengthPolicy()
M2_LENGTH_SPEC = LengthSpec(
    language=DraftLanguage.ZH,
    min_units=2000,
    target_units=2500,
    max_units=3100,  # 修正案 8 起：宽容带 [1800, 3410]，带外才 INVALID
)

LENGTH_TOLERANCE = 0.10
"""修正案 8（2026-08-03）：长度按权重比例看待。

长度是仪器卫生，不是考试的核心问题（核心 = 图谱约束对泄漏率的影响）。
±10% 内的小偏差**记录不判死**（M2：1,800–3,410），只有带外才 INVALID。
取代修正案 6 的固定 ≤100 宽容（并补了下限侧）。
"""


def length_within_tolerance(spec: LengthSpec, actual_units: int) -> bool:
    """修正案 8 的宽容带判定：``[min×0.9, max×1.1]`` 闭区间。"""
    floor = int(spec.min_units * (1 - LENGTH_TOLERANCE))
    ceiling = int(spec.max_units * (1 + LENGTH_TOLERANCE))
    return floor <= actual_units <= ceiling


__all__ = [
    "COUNTING_RULE_VERSION",
    "DEFAULT_LENGTH_POLICY",
    "LENGTH_TOLERANCE",
    "M2_LENGTH_SPEC",
    "DraftLanguage",
    "LengthMeasurement",
    "LengthPolicy",
    "LengthSpec",
    "LengthStatus",
    "count_units",
    "length_within_tolerance",
    "measure",
]
