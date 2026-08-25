"""Strict models for untrusted chapter-analysis output."""

from __future__ import annotations

import math
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "RawChapterAnalysis",
    "RawCharacterProfile",
    "RawEvent",
    "RawStateUpdate",
]


_UNTRUSTED_CONFIG = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

SHORT_QUOTE_LIMIT: Final = 10
"""引语短于这个字符数算「短引语」。"""

SHORT_QUOTE_RATIO: Final = 0.30
"""短引语占本章引语总数的比例上限。"""

MIN_SHORT_QUOTES_PER_CHAPTER: Final = 1
"""比例算出来是 0 时至少放行 1 条：单事件章节不该因为唯一原话太短而整章失败。"""


class RawEvent(BaseModel):
    """One story beat proposed by the analysis model."""

    model_config = _UNTRUSTED_CONFIG

    summary: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=120)
    participants: tuple[str, ...]
    knowers: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class ExtractedAlias(BaseModel):
    """模型提议的一条称呼对应（§4.5 / Task 12）。

    `surface` = 正文里出现的称呼；`character_surface` = 它指的那个人物的**已登记
    称呼**（本名或既有别名）。**禁止仅凭字符串相似断言同人**——目标人物称呼必须
    逐字出现在模型输出的这段里，引擎仍按它去 `resolve` 成确定 person。

    `quote` 必须逐字包含目标称呼和 alias surface（§4.5 的自动条件之一）。
    """

    model_config = _UNTRUSTED_CONFIG

    surface: str = Field(min_length=1)
    character_surface: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)


class RawCharacterProfile(BaseModel):
    """Profile fields attributed to a character surface name."""

    model_config = _UNTRUSTED_CONFIG

    surface: str = Field(min_length=2)
    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    confidence: float = Field(ge=0, le=1)


class RawStateUpdate(BaseModel):
    """One proposed graph-state change, still expressed only in surface names.

    ── `death` 为什么是自己一档，而不是 `state` + dimension="生死" ──────────────

    因为**引擎不许去读模型写的那个词**。R3 `DEAD_SPEAKS` 的判据是
    `EdgeProps.value_key == HealthValue.DEAD` 这个**机器键**，不是 `value` 那段中文
    （`declare.py` 那段论证：死 / 陨落 / 坐化 / 兵解 —— 认那段字就是「这句话是什么
    意思」，撞 ADR 0005 的铁律）。

    走 `state` 的话，`dimension` 是模型自由写的一串字，引擎要么去猜「这个维度是不是
    生死」（语义判断），要么把 `value_key` 留空（那就是 2026-08-13 之前那十一天：
    R3 每天绿着、生产上结构性哑火）。

    **`kind` 是一个封闭枚举，模型只是在里面挑一个** —— 挑完之后 `value_key` 由引擎
    写死成常量，一个字都不从模型的文字里认。语义判断留在模型那一侧，引擎这一侧
    仍然只做集合判断。这和 `location` 那一档是同一个手法。

    `death` 不带 object / dimension / value：那三样都是引擎自己填的常量
    （health 维度 + `HealthValue.DEAD` + `DEAD_VALUE_TEXT`），**给模型填的机会
    就是给它编一个别的键的机会**。
    """

    model_config = _UNTRUSTED_CONFIG

    kind: Literal["location", "state", "relationship", "death"]
    subject: str = Field(min_length=1)
    object: str | None = None
    dimension: str | None = None
    value: str | None = None
    quote: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_kind_shape(self) -> Self:
        if self.kind == "location":
            valid = self.object is not None and self.dimension is None and self.value is None
        elif self.kind == "state":
            valid = self.object is None and self.dimension is not None and self.value is not None
        elif self.kind == "death":
            valid = self.object is None and self.dimension is None and self.value is None
        else:
            valid = self.object is not None and self.dimension is None and self.value is not None
        if not valid:
            raise ValueError(f"invalid fields for {self.kind} state update")
        return self


class RawChapterAnalysis(BaseModel):
    """Complete, untrusted structured output for one chapter."""

    model_config = _UNTRUSTED_CONFIG

    events: tuple[RawEvent, ...] = Field(min_length=1, max_length=12)
    state_updates: tuple[RawStateUpdate, ...] = Field(max_length=24)
    character_profiles: tuple[RawCharacterProfile, ...]
    aliases: tuple[ExtractedAlias, ...] = ()
    """Task 12：模型提议的称呼对应，**先于** profile/event/state 解析（identity-first）。

    默认空元组 = 旧 prompt 的响应对 `aliases` 字段直接 `extra="forbid"` 拒绝——
    升级 prompt 时模型必须显式给（哪怕给空数组）。"""

    @model_validator(mode="after")
    def short_quote_frequency_capped(self) -> Self:
        items = (*self.events, *self.state_updates)
        short = sum(
            1
            for item in items
            if len(item.quote) < SHORT_QUOTE_LIMIT
        )
        allowed = max(
            MIN_SHORT_QUOTES_PER_CHAPTER,
            math.ceil(SHORT_QUOTE_RATIO * len(items)),
        )
        if short > allowed:
            raise ValueError(
                f"short quotes {short} exceed the per-chapter allowance {allowed} "
                f"({SHORT_QUOTE_RATIO:.0%} of {len(items)} quotes, min {MIN_SHORT_QUOTES_PER_CHAPTER})"
            )
        return self
