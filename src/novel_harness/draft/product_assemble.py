"""Product prompt wrapper that adds confirmed event memory to the frozen M2 assembler."""

from __future__ import annotations

from ..events import CharacterProfileView, EventView
from .assemble import PromptForm, assemble
from .context import ResolvedConstraints
from .length import LengthSpec
from .product_context import ResolvedProductContext


_PROFILE_LABELS = (
    ("gender", "性别"),
    ("personality", "性格"),
    ("background", "背景"),
    ("character_notes", "备注"),
)


def _profile_line(profile: CharacterProfileView) -> str:
    details = [
        f"{label}：{value}"
        for field, label in _PROFILE_LABELS
        if (value := getattr(profile, field))
    ]
    suffix = "；".join(details) if details else "暂无补充资料"
    return f"- {profile.character.name}：{suffix}"


def _event_line(view: EventView) -> str:
    participants = "、".join(participant.name for participant in view.participants)
    suffix = f"（涉及：{participants}）" if participants else ""
    return f"- 第 {view.event.chapter_number} 章：{view.event.summary}{suffix}"


def render_product_memory(memory: ResolvedProductContext) -> str:
    """Render only author-facing names and accepted profile/event prose, never storage metadata."""

    lines = [
        "已确认的故事记忆",
        "以下人物资料与事件均已由作者确认；只把它们当作当前写作的既有事实。",
        "",
        "【在场人物资料】",
        *(_profile_line(profile) for profile in memory.profiles),
        "",
        "【近八章事件】",
        *(
            (_event_line(view) for view in memory.recent_events)
            if memory.recent_events
            else ("- 暂无",)
        ),
        "",
        "【更早的相关事件】",
        *(
            (_event_line(view) for view in memory.background_events)
            if memory.background_events
            else ("- 暂无",)
        ),
        "",
        "【更早章节滚动总结】",
        *(
            (
                f"- 第 {summary.chapter_number} 章：{summary.summary}"
                for summary in memory.rolling_summaries
            )
            if memory.rolling_summaries
            else ("- 暂无",)
        ),
        "（滚动总结是机器压缩的背景，未经作者确认；只当线索，不当已确认事实。）",
    ]
    return "\n".join(lines)


def assemble_product(
    ctx: ResolvedConstraints,
    memory: ResolvedProductContext,
    *,
    form: PromptForm = PromptForm.X1,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    house_style: str | None = None,
) -> list[dict[str, str]]:
    """Prepend safe memory while forwarding the prior assembler arguments unchanged."""

    base = assemble(
        ctx,
        form=form,
        goal=goal,
        length=length,
        previous_tail=previous_tail,
        house_style=house_style,
    )
    return [
        {"role": "system", "content": render_product_memory(memory)},
        *base,
    ]


__all__ = ["assemble_product", "render_product_memory"]
