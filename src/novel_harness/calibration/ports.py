"""校准层收的**窄只读端口**（结构上由既有实现满足，不 import agent/）。

与 `agent/ports.py` 的 `SummaryIndex` / `EventIndex` 同判据、同形状；这里重新声明
是因为校准包**不 import agent/**（依赖方向是 agent → calibration）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..draft.rolling_summary import ChapterSummaryStatus, SummarySnapshotWatermark
from ..events import EventView
from ..graph import InformationScope


@runtime_checkable
class CalibrationSummarySource(Protocol):
    """滚动总结只读端口：coverage + 当前快照水位。"""

    def coverage(
        self,
        project_id: str,
        first_chapter: int,
        last_chapter: int,
    ) -> list[ChapterSummaryStatus]: ...

    def snapshot_watermark(
        self,
        project_id: str,
        chapter_number: int,
    ) -> SummarySnapshotWatermark | None: ...


@runtime_checkable
class CalibrationEventSource(Protocol):
    """已确认事件只读端口（收窄：没有写方法）。"""

    def events_for_characters(
        self,
        project_id: str,
        character_ids: Sequence[str],
        chapter: int,
        scope: InformationScope,
    ) -> list[EventView]: ...
