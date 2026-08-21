"""纯结构化章节抽取契约。"""

from __future__ import annotations

from .analyze import (
    AnalysisFormatError,
    ResolvedAnalysis,
    ResolutionContractError,
    SurfaceResolution,
    parse_analysis,
    resolve_surfaces,
)
from .models import (
    ExtractedAlias,
    RawChapterAnalysis,
    RawCharacterProfile,
    RawEvent,
    RawStateUpdate,
)
from .locate import LocateOutcome, LocateResult, locate_quote
from .prompt import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisMessage,
    build_analysis_messages,
)

__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "ExtractedAlias",
    "AnalysisFormatError",
    "AnalysisMessage",
    "LocateOutcome",
    "LocateResult",
    "RawChapterAnalysis",
    "RawCharacterProfile",
    "RawEvent",
    "RawStateUpdate",
    "ResolvedAnalysis",
    "ResolutionContractError",
    "SurfaceResolution",
    "build_analysis_messages",
    "locate_quote",
    "parse_analysis",
    "resolve_surfaces",
]
