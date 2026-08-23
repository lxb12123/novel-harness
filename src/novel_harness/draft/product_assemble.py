"""Product prompt wrapper that adds confirmed event memory to the frozen M2 assembler."""

from __future__ import annotations

from collections.abc import Sequence

from ..events import CharacterProfileView, EventView
from .assemble import GATE_TAIL_CODE_POINTS, PromptForm, assemble
from .context import DraftContext, ResolvedConstraints
from .length import LengthSpec
from .product_context import ResolvedProductContext, RollingSummaryView


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


ROLLING_SUMMARY_HEADING = "【更早章节滚动总结】"
"""滚动总结那一块的块首。整章起草和行内续写**渲染的是同一块**。"""

ROLLING_SUMMARY_DISCLAIMER = (
    "（滚动总结是机器压缩的背景，未经作者确认；只当线索，不当已确认事实。）"
)
"""跟着那一块走的免责，**必须在块首**：它原来在整段最末尾，离要免责的那五千字有
五千字远，模型读到它的时候早把总结当成事实读完了。"""


def _rolling_summary_lines(
    summaries: Sequence[RollingSummaryView], *, empty_line: str | None
) -> list[str]:
    """滚动总结那一块的正文行。`empty_line=None` = 一条都没有时整块不出现。

    整章起草传 `- 暂无`（那一段本来就有别的块，一个占位不多；而「零带着理由」是
    回执那一侧的事）；行内续写传 `None`——它整份 prompt 只有几百字，塞一个
    「- 暂无」等于凭空多一块什么都没说的东西。
    """
    if not summaries and empty_line is None:
        return []
    return [
        ROLLING_SUMMARY_HEADING,
        ROLLING_SUMMARY_DISCLAIMER,
        *(
            (f"- 第 {item.chapter_number} 章：{item.summary}" for item in summaries)
            if summaries
            else (empty_line,)
        ),
    ]


FOLLOWING_TEXT_HEADING = "【下文】"
"""光标**后面**那截已经写好的正文的块首。

`_base()` 发的【上文】是「接着往下写」的那一段；这一块是「已经写好、别重写」的那一段。
**两块必须在 prompt 里分得开**：不说清楚，模型会把下文也当成「要写的」，重写一遍。
"""

FOLLOWING_TEXT_INSTRUCTION = (
    "以下是这一章接下来已经写好的正文。不要重写它、不要改动它，"
    "你写的这一段要能自然接上它的开头。"
)
"""跟着那一块走的说明，**必须在块首**（同滚动总结那条免责）：写在几千字之后，
模型读到它的时候早就把下文当成「待写的段落」读完了。"""


def _following_text_block(text: str, limit: int) -> str:
    """【下文】那一块，超出额度**从后面截**——留住紧挨着光标的那一截。

    上文截的是末尾（`_base()` 的 `previous_tail[-limit:]`），下文截的是开头，
    两刀都朝着光标切：离光标越远的字，对「这一段接不接得上」越没用。
    """
    kept = text.strip()[:limit] if limit > 0 else ""
    if not kept:
        return ""
    return "\n".join([FOLLOWING_TEXT_HEADING, FOLLOWING_TEXT_INSTRUCTION, kept])


def _insert_memory(base: list[dict[str, str]], block: str) -> list[dict[str, str]]:
    """把记忆段插在**前导 system 段之后**（`[文风][记忆][用户]`，ADR 0019 边界六）。

    切的是「前导 system 段」而不是写死 `base[0]`：`assemble()` 今天只发一条 system
    消息，但**把「它只有一条」写进这儿就是第二处依赖它的地方**，而那是三臂那侧的
    形状，不是这儿的。
    """
    stable = 0
    while stable < len(base) and base[stable]["role"] == "system":
        stable += 1
    return [*base[:stable], {"role": "system", "content": block}, *base[stable:]]


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

    ⚠️ **滚动总结的稳定性有条件**：`product_context.select_rolling_summaries` 是「从新往旧
    收到预算用完」，所以总结一多到撑破预算，**掉的是最旧的那几条**——那时这一块的前缀会整体
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
        *_rolling_summary_lines(memory.rolling_summaries, empty_line="- 暂无"),
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
    return _insert_memory(base, render_product_memory(memory))


def assemble_continuation(
    ctx: DraftContext,
    rolling_summaries: Sequence[RollingSummaryView],
    *,
    form: PromptForm = PromptForm.X1,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    previous_tail_limit: int = GATE_TAIL_CODE_POINTS,
    following_text: str = "",
    write_rule: str | None = None,
) -> list[dict[str, str]]:
    """行内续写的装配：`assemble()` + **滚动总结那一格** + **光标后的同章正文**。

    ── 为什么不是 `assemble_product` 少传几样 ────────────────────────────────

    那个函数收的是 `ResolvedProductContext`，而它**要求 cast 非空**——续写的常态正是
    「不知道这一场有谁在」（ADR 0015 D4），构造不出来。而且它渲染的四块里有三块
    （档案 / 近期事件 / 更早事件）要查图、要算窗口边界，进不了续写那 400 毫秒的预算。
    所以这儿收的是**已经选好的那几条总结**，纯查库来的，别的一格都不碰。

    `ctx` 收的是 `DraftContext` 而不是 `ResolvedConstraints`：续写两种约束态都要走
    这条路，而这一层对它做的唯一一件事就是原样递给 `assemble()`。

    两格都空时**整块都不出现**，返回值与 `assemble()` 逐字节相同：续写的整份
    prompt 只有几百字，凭空多一块「- 暂无」既不带信息、又把前缀缓存的形状改了。

    ── 【下文】追加在用户消息**尾部**，和 X1/X2 的图谱段同一个形状 ────────────

    `assemble()` 是三臂共用的冻结渲染器（EVAL_PROTOCOL §2），它发的那条用户消息里
    只有【上文】【在场】【这一场要写】——**这一层不进去改它**，照 X1/X2 追加图谱段
    那一手在后面接一块。代价是【下文】排在【这一场要写】之后而不是紧挨着【上文】；
    换来的是那个文件一个字节都没动。

    Args:
        following_text: 光标**后面**那截同章正文（作者跳回去改旧章时，那是已经写好的
            几千字）。空串 = 整块不出现。**额度和上文共用 `previous_tail_limit`**：
            两截都朝着光标切，公式只有后端那一份（`assemble.product_tail_limit`），
            这儿不再引入第二个数。
    """
    base = assemble(
        ctx,
        form=form,
        goal=goal,
        length=length,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        write_rule=write_rule,
    )
    following = _following_text_block(following_text, previous_tail_limit)
    if following:
        # 只追加，不重排、不改写前面任何一个字节（同 `assemble()` 追加图谱段那一行）。
        base = [*base[:-1], {**base[-1], "content": base[-1]["content"] + "\n\n" + following}]
    lines = _rolling_summary_lines(rolling_summaries, empty_line=None)
    return base if not lines else _insert_memory(base, "\n".join(lines))


__all__ = ["assemble_continuation", "assemble_product", "render_product_memory"]
