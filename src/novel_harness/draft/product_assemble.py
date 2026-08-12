"""Product prompt wrapper that adds confirmed event memory to the frozen M2 assembler."""

from __future__ import annotations

from ..events import CharacterProfileView, EventView
from .assemble import GATE_TAIL_CODE_POINTS, PromptForm, assemble
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
    previous_tail_limit: int = GATE_TAIL_CODE_POINTS,
    house_style: str | None = None,
) -> list[dict[str, str]]:
    """Insert safe memory after the stable style block, forwarding assembler args unchanged.

    ── 顺序是 `[文风][记忆][用户]`，不是 `[记忆][文风][用户]`（ADR 0019 边界六）──────

    记忆前言**逐章变**（人物档案 + 近期事件 + 滚动总结），文风**跨章不变**。记忆排在前面
    时，唯一稳定的那块被夹在中间，**前缀缓存价值为零**——每一章都得重付一次文风段的输入
    token，而那一段每次逐字节相同。

    **这不是给 kill-gate 改考卷**：三臂（X0/X1/X2）走的是 `assemble()`，从来不经过本函数。
    实测证据在 `tests/test_product_assemble.py::test_the_gate_never_reaches_this_module`
    ——`eval/runner.py` 和 `eval/evidence.py` 直接 import `assemble`，`assemble_product`
    在整个 `src/` 里只有一个调用方（`api/app.py` 的 `/draft`，且只在 `PRODUCT` 那一支）。
    `assemble()` 本身一个字都没动。

    切的是「前导 system 段」而不是写死 `base[0]`：`assemble()` 今天只发一条 system 消息，
    但**把「它只有一条」写进这儿就是第二处依赖它的地方**，而那是三臂那侧的形状，不是这儿的。
    """

    # `previous_tail_limit` 只是**透传**：默认值仍是三臂那个冻结值，放大它的决定在调用点
    # （`api/app.py` 的 `/draft`），不在这儿——本模块加长上文等于替 kill-gate 改了考卷。
    base = assemble(
        ctx,
        form=form,
        goal=goal,
        length=length,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        house_style=house_style,
    )
    stable = 0
    while stable < len(base) and base[stable]["role"] == "system":
        stable += 1
    return [
        *base[:stable],
        {"role": "system", "content": render_product_memory(memory)},
        *base[stable:],
    ]


__all__ = ["assemble_product", "render_product_memory"]
