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
    """Render only author-facing names and accepted profile/event prose, never storage metadata.

    ── 块序：**最不会变的排最前**（ADR 0019 边界六，同一条判据往里再走一层）────

    边界六在**消息**这一层已经守住了（`[文风][记忆][用户]`，见 `assemble_product`），
    可记忆前言**内部**原来又犯了一遍同一个错，而且这一次代价大得多：

    | 块 | 跟着什么变 | 真书实测长度 |
    |---|---|---|
    | 【更早章节滚动总结】 | 只跟章号，且要撑破预算才动 | **5,518 字** |
    | 【更早的相关事件】/【近八章事件】 | 章号 + 在场 | 各十几字 |
    | 【在场人物资料】 | **每一场的在场名单** | 48–89 字 |

    原来的顺序把最后那一块（几十个字、每场都变）排在那 5,518 字**前面**。前缀缓存只认
    前缀，**前面变一个字节后面全废**——于是相邻两章的起草 prompt 共同前缀只剩 333 字
    （2026-08-13 在作者 722 章真书上实测第 718–722 章，全长约 6,800 字，**4.9%**），
    换成现在这个顺序是 5,881 字（**87%**）。同一轮里对话那一档命中 97.5%、起草那一档
    五次调用命中 0，差的就是这件事：对话是**追加式**的消息表（前面每一轮逐字节不动），
    起草每次从头拼，而拼的时候把最易变的排在了最前面。

    **内容一个字没动，动的只是块的先后。**

    ⚠️ **滚动总结的稳定性有条件**：`product_context._take_from_newest` 是「从新往旧收到
    预算用完」，所以总结一多到撑破预算，**掉的是最旧的那几条**——那时这一块的前缀会整体
    平移一次，缓存跟着废一次。那是预算的性质，不是块序的问题；块序只保证「没撑破的时候
    它是稳的」。

    ⚠️ 那句「未经作者确认」的免责跟着它自己那一块走，并且移到了**块首**：原来它在整段最
    末尾，离它要免责的那 5,518 字有五千多字远，模型读到它的时候早把总结当成事实读完了。
    """

    lines = [
        "已生效的故事记忆",
        "以下人物资料与事件是当前已生效的记忆（系统自动整理的部分只当线索，"
        "作者亲自确认过的才当既定事实）。",
        "",
        "【更早章节滚动总结】",
        "（滚动总结是机器压缩的背景，未经作者确认；只当线索，不当已确认事实。）",
        *(
            (
                f"- 第 {summary.chapter_number} 章：{summary.summary}"
                for summary in memory.rolling_summaries
            )
            if memory.rolling_summaries
            else ("- 暂无",)
        ),
        *(
            line
            for fallback in memory.raw_fallbacks
            for line in (
                f"【第 {fallback.chapter_number} 章原文片段（没有总结，这不是总结）】",
                fallback.text,
            )
        ),
        "",
        "【更早的相关事件】",
        *(
            (_event_line(view) for view in memory.background_events)
            if memory.background_events
            else ("- 暂无",)
        ),
        "",
        "【近八章事件】",
        *(
            (_event_line(view) for view in memory.recent_events)
            if memory.recent_events
            else ("- 暂无",)
        ),
        "",
        "【在场人物资料】",
        *(_profile_line(profile) for profile in memory.profiles),
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
    write_rule: str | None = None,
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
        write_rule=write_rule,
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
