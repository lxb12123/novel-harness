"""Product prompt wrapper that adds confirmed event memory to the frozen M2 assembler."""

from __future__ import annotations

from collections.abc import Sequence

from ..events import CharacterProfileView, EventView
from .assemble import GATE_TAIL_CODE_POINTS, assemble
from .context import DraftContext, ResolvedConstraints
from .length import DraftLanguage, LengthSpec
from .product_context import ResolvedProductContext, RollingSummaryView
from .prompt_terms import PromptTerm, term


_PROFILE_LABEL_KEYS = (
    ("gender", PromptTerm.PROFILE_LABEL_GENDER),
    ("personality", PromptTerm.PROFILE_LABEL_PERSONALITY),
    ("background", PromptTerm.PROFILE_LABEL_BACKGROUND),
    ("character_notes", PromptTerm.PROFILE_LABEL_NOTES),
)


def _profile_line(profile: CharacterProfileView, language: DraftLanguage) -> str:
    detail_template = term(PromptTerm.PROFILE_DETAIL, language)
    details = [
        detail_template.format(label=term(label_key, language), value=value)
        for field, label_key in _PROFILE_LABEL_KEYS
        if (value := getattr(profile, field))
    ]
    suffix = (
        term(PromptTerm.PROFILE_DETAIL_SEPARATOR, language).join(details)
        if details
        else term(PromptTerm.PROFILE_NO_DETAILS, language)
    )
    return term(PromptTerm.PROFILE_LINE, language).format(
        name=profile.character.name, suffix=suffix
    )


def _event_line(view: EventView, language: DraftLanguage) -> str:
    separator = term(PromptTerm.LIST_SEPARATOR, language)
    participants = separator.join(participant.name for participant in view.participants)
    suffix = (
        term(PromptTerm.EVENT_PARTICIPANTS, language).format(participants=participants)
        if participants
        else ""
    )
    return term(PromptTerm.EVENT_LINE, language).format(
        chapter=view.event.chapter_number, summary=view.event.summary, suffix=suffix
    )


def _rolling_summary_lines(
    summaries: Sequence[RollingSummaryView],
    *,
    show_empty_placeholder: bool,
    language: DraftLanguage,
) -> list[str]:
    """滚动总结那一块的正文行。`show_empty_placeholder=False` = 一条都没有时整块不出现。

    整章起草传 `True`（那一段本来就有别的块，一个占位不多；而「零带着理由」是
    回执那一侧的事）；行内续写传 `False`——它整份 prompt 只有几百字，塞一个
    占位符等于凭空多一块什么都没说的东西。

    占位符文字（原来的字面量 `"- 暂无"`）2026-08-27 起从 `prompt_terms` 按语言取——
    这个参数不再是「调用方给什么占位符就渲染什么」，是「要不要渲染占位符」，
    真正的文字由这一层自己按 `language` 决定，调用方不用再自己拼一遍。
    """
    if not summaries and not show_empty_placeholder:
        return []
    heading = term(PromptTerm.ROLLING_SUMMARY_HEADING, language)
    if not summaries:
        return [heading, term(PromptTerm.NONE_YET, language)]
    chapter_template = term(PromptTerm.CHAPTER_LINE, language)
    return [
        heading,
        *(
            chapter_template.format(chapter=item.chapter_number, summary=item.summary)
            for item in summaries
        ),
    ]


def _following_text_block(text: str, limit: int, language: DraftLanguage) -> str:
    """【下文】那一块，超出额度**从后面截**——留住紧挨着光标的那一截。

    上文截的是末尾（`_base()` 的 `previous_tail[-limit:]`），下文截的是开头，
    两刀都朝着光标切：离光标越远的字，对「这一段接不接得上」越没用。

    标题和说明**必须在块首**（同滚动总结那条免责，历史原因见 ADR 0017 补记）：
    写在几千字之后，模型读到它的时候早就把下文当成「待写的段落」读完了。
    """
    kept = text.strip()[:limit] if limit > 0 else ""
    if not kept:
        return ""
    return "\n".join(
        [
            term(PromptTerm.FOLLOWING_TEXT, language),
            term(PromptTerm.FOLLOWING_TEXT_INSTRUCTION, language),
            kept,
        ]
    )


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


def render_product_memory(memory: ResolvedProductContext, language: DraftLanguage) -> str:
    """Render only author-facing names and accepted profile/event prose, never storage metadata.

    `language` 只换标题、分隔符、占位符这些字面量（`prompt_terms.py`，国际化第二批）；
    块序、预算、要不要出现，一律不受影响——**中文取值就是原来那几个字面量**，
    这一层加了参数不代表加了新的判断。

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
    """

    none_yet = term(PromptTerm.NONE_YET, language)
    lines = [
        term(PromptTerm.STORY_MEMORY_HEADING, language),
        term(PromptTerm.STORY_MEMORY_INTRO, language),
        "",
        *_rolling_summary_lines(
            memory.rolling_summaries, show_empty_placeholder=True, language=language
        ),
        "",
        term(PromptTerm.EARLIER_RELATED_EVENTS, language),
        *(
            (_event_line(view, language) for view in memory.background_events)
            if memory.background_events
            else (none_yet,)
        ),
        "",
        term(PromptTerm.RECENT_EVENTS, language),
        *(
            (_event_line(view, language) for view in memory.recent_events)
            if memory.recent_events
            else (none_yet,)
        ),
        "",
        term(PromptTerm.PRESENT_PROFILES, language),
        *(_profile_line(profile, language) for profile in memory.profiles),
    ]
    return "\n".join(lines)


def render_standing_rules(rules: Sequence[str], language: DraftLanguage) -> str:
    """作者自己定下的那几条规矩，渲染成一段。**空的时候调用方根本不插这一段。**

    今天只有一种规矩：`forbidden_literal`（作者在「检验规则」那一栏里写的一段字）。
    它同时进两条路——**写之前进这段 prompt，写完之后由同一条规则去查**
    （`checks/custom.py`），两边读的是同一行数据，不是两份措辞。

    ⚠️ **和 `write_rule` 不是一回事，别合并**：`write_rule` 是作者挂在**这一段对话**上的
    文风（`agent/store.py::start_conversation`），有范围、会随对话结束失效；这几条是
    **一直有效的**，跟对话无关（维护者原话：「这跟 session 选中的那个规则不一样，
    这个是作者定义的永久性规则」）。
    """
    heading = term(PromptTerm.STANDING_RULES_HEADING, language)
    line = term(PromptTerm.STANDING_RULE_FORBIDDEN, language)
    return "\n".join([heading, *(line.format(literal=rule) for rule in rules)])


def insert_standing_rules(
    base: list[dict[str, str]], rules: Sequence[str], language: DraftLanguage
) -> list[dict[str, str]]:
    """把「作者定下的规矩」插在稳定段之后。空的时候原样返回，**不插一个空块**。

    单独抽出来是因为**退化那一支也要它**：不知道这一场有谁在的时候记忆前言装不出来
    （`product_draft._with_memory`），可作者的规矩跟在场名单没有半点关系——
    那时候把它一起丢掉，等于「记忆查不到 ⇒ 顺便也不守规矩了」。
    """
    if not rules:
        return base
    return _insert_memory(base, render_standing_rules(rules, language))


def assemble_product(
    ctx: ResolvedConstraints,
    memory: ResolvedProductContext,
    *,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    previous_tail_limit: int = GATE_TAIL_CODE_POINTS,
    write_rule: str | None = None,
    standing_rules: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Insert safe memory after the stable style block, forwarding assembler args unchanged.

    ── 顺序是 `[文风][记忆][用户]`，不是 `[记忆][文风][用户]`（ADR 0019 边界六）──────

    记忆前言**逐章变**（人物档案 + 近期事件 + 滚动总结），文风**跨章不变**。记忆排在前面
    时，唯一稳定的那块被夹在中间，**前缀缓存价值为零**——每一章都得重付一次文风段的输入
    token，而那一段每次逐字节相同。

    （2026-08-25 之前这里还有一句「这不是给 kill-gate 改考卷：三臂走的是 `assemble()`，
    从来不经过本函数」。三臂随 M2 一起删了，那句话没有对象了——**但本函数仍然只是
    `assemble()` 外面的一层**，`assemble()` 一个字节都不该被这一层改到。）
    """

    # `previous_tail_limit` 只是**透传**：默认值仍是那个短的地板值，放大它的决定在调用点
    # （`api/app.py` 的 `/draft`），不在这儿。
    base = assemble(
        ctx,
        goal=goal,
        length=length,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        write_rule=write_rule,
    )
    # **规矩排在记忆前面**，理由就是上面那条块序判据（ADR 0019 边界六）：它是这份
    # prompt 里**最不会变的东西之一**（作者哪天改了才变，比文风还稳），而记忆逐章变。
    # 排在记忆后面 = 每章的记忆一变，它后面的前缀全废。
    base = insert_standing_rules(base, standing_rules, length.language)
    return _insert_memory(base, render_product_memory(memory, length.language))


def assemble_continuation(
    ctx: DraftContext,
    rolling_summaries: Sequence[RollingSummaryView],
    *,
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
        goal=goal,
        length=length,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        write_rule=write_rule,
    )
    following = _following_text_block(following_text, previous_tail_limit, length.language)
    if following:
        # 只追加，不重排、不改写前面任何一个字节（同 `assemble()` 追加图谱段那一行）。
        base = [*base[:-1], {**base[-1], "content": base[-1]["content"] + "\n\n" + following}]
    lines = _rolling_summary_lines(
        rolling_summaries, show_empty_placeholder=False, language=length.language
    )
    return base if not lines else _insert_memory(base, "\n".join(lines))


__all__ = ["assemble_continuation", "assemble_product", "render_product_memory"]
