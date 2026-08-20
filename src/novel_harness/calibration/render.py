"""后端固定模板渲染 —— Writer 简报 / goal_spec / 目标章正文 / 作者任务卡。

所有展示文字由后端从 writer-safe 类型项固定渲染；**不接受 Agent 提供 `text`**。
作者聊天原文、Agent 的任务散文和事件散文都不直接转发。
"""

from __future__ import annotations

from hashlib import sha256

from ..draft.length import DraftLanguage, count_units
from .models import (
    BriefDirective,
    ContinuityFact,
    DirectiveCandidate,
    DirectiveKind,
    EpistemicKind,
    EventBeat,
    FactType,
    SceneBrief,
    SceneProposal,
)


_DIRECTIVE_TEXT: dict[DirectiveKind, str] = {
    DirectiveKind.ENTER_LOCATION: "进入{location}",
    DirectiveKind.SEARCH_FOR: "寻找{object}",
    DirectiveKind.TEST_CHARACTER: "试探{target}",
    DirectiveKind.DEFER_REVEAL: "本章暂不揭开{object}",
    DirectiveKind.ADVANCE_CLUE: "推进{object}线索",
}


def _directive_line(
    directive: DirectiveCandidate,
    *,
    cast_names: tuple[str, ...],
) -> str:
    """一条封闭指令 → 一句固定渲染的话。**没有自由文本通道。**"""
    template = _DIRECTIVE_TEXT.get(directive.kind)
    if template is None:
        return directive.kind.value
    args = {
        "location": directive.location_surface or "",
        "object": directive.object_surface or "",
        "target": directive.target_surface or "",
    }
    actor = directive.actor_surface or (cast_names[0] if cast_names else "")
    text = template.format(**args).strip()
    return f"{actor}{text}" if actor and not text.startswith("本章") else text


def render_goal_spec(proposal: SceneProposal, cast_names: tuple[str, ...]) -> str:
    """goal_spec：由结构化作者选择、封闭指令码与安全引用组成。

    普通自然语言请求的投影固定为 `MACHINE_INFERENCE`；确认后同一组指令进入
    `author_instructions`，但 goal_spec 的渲染函数只有一个。
    """
    lines = [_directive_line(d, cast_names=cast_names) for d in proposal.directive_candidates]
    return "；".join(line for line in lines if line)


def render_author_card(
    *,
    chapter: int,
    proposal: SceneProposal,
    cast_names: tuple[str, ...],
) -> tuple[str, str]:
    """固定渲染的类型化任务卡（给作者确认用）。返回 (卡片文字, card_hash)。"""
    lines = [
        f"【类型化任务卡】第 {chapter} 章",
        f"- 预计人物：{'、'.join(cast_names) or '（空）'}",
    ]
    if proposal.directive_candidates:
        lines.append("- 指令：")
        lines.extend(
            f"  - {_directive_line(d, cast_names=cast_names)}"
            for d in proposal.directive_candidates
        )
    else:
        lines.append("- 指令：（无）")
    codes = []
    for name, value in (
        ("语气", proposal.tone_code),
        ("节奏", proposal.pacing_code),
        ("结尾", proposal.ending_code),
    ):
        if value:
            codes.append(f"{name}={value}")
    if codes:
        lines.append(f"- 风格码：{'、'.join(codes)}")
    card = "\n".join(lines)
    return card, sha256(card.encode("utf-8")).hexdigest()


def render_evidence_text(
    *,
    fact_type: FactType,
    character: str | None = None,
    peer: str | None = None,
    location: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    chapter: int | None = None,
    since_chapter: int | None = None,
    secret: str | None = None,
    summary: str | None = None,
    participants: tuple[str, ...] = (),
) -> str:
    """一条证据的固定渲染文字（按 fact_type 模板，不接受 Agent 自填）。"""
    if fact_type is FactType.BODY_LIMITATION:
        return f"{character}：{value or '（有身体限制）'}{f'（{dimension}）' if dimension else ''}"
    if fact_type is FactType.STATE:
        return f"{character}：{dimension} = {value or '（未记录值）'}"
    if fact_type is FactType.LOCATION:
        return f"{character} 位于 {location or '（未知）'}"
    if fact_type is FactType.DEATH:
        return f"{character} 已亡" if value else f"{character} 在世"
    if fact_type is FactType.APPEARED:
        return f"{character} 已登场" if value else f"{character} 尚未登场"
    if fact_type is FactType.RELATIONSHIP_STAGE:
        return f"{character} ↔ {peer or '（未知）'}：{value or '（关系已记录）'}"
    if fact_type in (FactType.KNOWS, FactType.BELIEVES):
        verb = "知道" if fact_type is FactType.KNOWS else "相信"
        since = f"（自第 {since_chapter} 章）" if since_chapter else ""
        return f"{character} {verb}「{secret or '（某秘密）'}」{since}"
    if fact_type is FactType.EVENT:
        suffix = f"（涉及：{'、'.join(participants)}）" if participants else ""
        return f"第 {chapter} 章：{summary or '（无摘要）'}{suffix}"
    if fact_type is FactType.CHAPTER_SUMMARY:
        return f"第 {chapter} 章：{summary or '（无摘要）'}"
    if fact_type is FactType.PROFILE:
        return f"{character}：{value or '（无补充资料）'}"
    if fact_type is FactType.FORESHADOW:
        return "「伏笔是否已经回收」目前没有可靠生产数据，不能核验。"
    return value or ""


def render_scene_brief(brief: SceneBrief, *, unknown_cast: bool = False) -> str:
    """Writer 简报的固定渲染。**独立「本稿执行计划」分区，不拼进 write rule。**

    `unknown_cast=True` 时按 UnknownCast 白名单再收窄一次：自由文本事件、章节总结
    与任意字符串状态默认排除（fail-closed，与 seal 时的 `writer_visibility` 同向）。
    """
    lines: list[str] = ["【本稿执行计划】"]

    cast = "、".join(ref.name for ref in brief.intended_cast) if brief.intended_cast else "（未知）"
    lines.append(f"- 预计人物：{cast}")

    if brief.author_instructions:
        lines.append("【作者确认的本稿要求】")
        lines.extend(f"- {_render_directive(item)}" for item in brief.author_instructions)
    if brief.projected_request_directives:
        lines.append("【Agent 对作者请求的结构化理解（未确认时为机器推演）】")
        lines.extend(
            f"- {_render_directive(item)}（{_basis_label(item.basis)}）"
            for item in brief.projected_request_directives
        )

    facts = [
        fact
        for fact in brief.continuity_facts
        if not _is_unknown_cast_dropped(fact, unknown_cast=unknown_cast)
    ]
    if facts:
        lines.append("【连续性依据】")
        lines.extend(
            f"- {fact.display_text}（{_strength_label(fact)}）"
            for fact in facts
        )

    machine = list(brief.machine_directives)
    if machine:
        lines.append("【机器写作建议】")
        lines.extend(f"- {_render_directive(item)}（机器推演）" for item in machine)

    beats = [
        item for item in brief.event_beats if not _is_unknown_cast_dropped(item, unknown_cast=unknown_cast)
    ]
    if beats:
        lines.append("【事件节拍】")
        lines.extend(f"- {_render_directive(item)}（{_basis_label(item.basis)}）" for item in beats)

    if brief.do_not_assume:
        lines.append("【未知，不得擅自断言】")
        lines.extend(f"- {_reason_text(item.reason_code)}" for item in brief.do_not_assume)

    if brief.tone_code or brief.pacing_code or brief.ending_code:
        codes = []
        for name, value in (
            ("语气", brief.tone_code),
            ("节奏", brief.pacing_code),
            ("结尾", brief.ending_code),
        ):
            if value:
                codes.append(f"{name}={value}")
        lines.append(f"【风格码】{'、'.join(codes)}")

    return "\n".join(lines)


def _is_unknown_cast_dropped(
    item: ContinuityFact | EventBeat,
    *,
    unknown_cast: bool,
) -> bool:
    if not unknown_cast:
        return False
    if isinstance(item, EventBeat):
        # 事件只有在能机械证明对全体可能人物公开时才进 Writer；UnknownCast 下
        # 预计人物只是实际人物的子集，不能只证明预计人物都是 knower。
        return True
    return item.fact_type in {
        FactType.EVENT,
        FactType.CHAPTER_SUMMARY,
        FactType.PROFILE,
        FactType.STATE,
        FactType.RELATIONSHIP_STAGE,
        FactType.KNOWS,
        FactType.BELIEVES,
    }


def _render_directive(item: BriefDirective) -> str:
    args = {arg.key: str(arg.value) for arg in item.safe_args}
    template = _DIRECTIVE_TEXT.get(item.directive_kind)
    if template is None:
        return item.directive_kind.value
    text = template.format(**args).strip()
    return text or item.directive_kind.value


def _basis_label(basis: EpistemicKind) -> str:
    if basis is EpistemicKind.AUTHOR_INTENT:
        return "作者确认"
    return "机器推演"


def _strength_label(fact: ContinuityFact) -> str:
    if fact.strength == "HARD":
        return f"{_basis_label(fact.basis)} · 硬依据"
    return f"{_basis_label(fact.basis)} · 参考"


def render_target_chapter(text: str, *, max_units: int) -> tuple[str, bool]:
    """目标章当前正文分区：**确定性截断并给覆盖回执**，不静默只取章首。"""
    if max_units <= 0:
        return "", True
    units = count_units(text, DraftLanguage.ZH)
    if units <= max_units:
        return text, False
    # 取头部（自然阅读顺序）+ 覆盖回执；专有 revision/patch 能力存在前，
    # 整章替代候选仍叫「整章替代候选」。
    kept: list[str] = []
    spent = 0
    for para in text.split("\n"):
        cost = count_units(para, DraftLanguage.ZH)
        if kept and spent + cost > max_units:
            break
        spent += cost
        kept.append(para)
    return "\n".join(kept), True


_REASON_TEXT: dict[str, str] = {
    "NO_RELIABLE_PRODUCTION_READ": "目前没有可靠生产数据，不能核验。",
    "CAPABILITY_MISSING": "当前能力不存在，不得擅自断言。",
    "STALE_EVIDENCE": "证据已失效，不得当作当前事实。",
    "TRUNCATED": "查询被截断，其余部分未核验。",
    "UNKNOWN": "缺数据或无法回答，不得补写成事实。",
}


def _reason_text(code: str) -> str:
    return _REASON_TEXT.get(code, f"未知项（{code}）")
