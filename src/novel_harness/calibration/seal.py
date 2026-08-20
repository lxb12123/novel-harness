"""`seal_scene_brief` —— 重新校验 + 固定渲染 + 签发不可变 `calibration_id`。

规格书 §5.3 方案 C 的第三步：封存器**绝不接收需要它判断含义的 Writer-bound 字符串**。
作者意图只能来自绑定完整任务卡与指令参数的服务端确认记录；连续性事实只接
Writer-safe `item_id` 并由后端渲染；Agent 新增内容一律固定为 `AGENT_INFERRED`。
"""

from __future__ import annotations

from hashlib import sha256

from ..graph import NodeRef, StoryGraph
from ..ids import EntityType, new_id
from .models import (
    AuthorInstruction,
    AuthorInstructionRef,
    AuthorResolution,
    AuthorTurnRef,
    CalibrationReport,
    CalibrationStatus,
    ContinuityFact,
    DirectiveCandidate,
    DirectiveKind,
    DoNotAssume,
    EpistemicKind,
    EvidenceEnvelope,
    FactType,
    MachineDirective,
    ProjectedDirective,
    SafeArg,
    SceneBrief,
    SceneProposal,
    Sealability,
    SealedCalibration,
    WriterVisibility,
)
from .render import render_author_card, render_goal_spec


class SealRefused(ValueError):
    """封存被拒。理由要说给模型听（改参数重来），不是崩溃。"""


REQUEST_DIRECTIVE_KINDS = frozenset(
    {DirectiveKind.ENTER_LOCATION, DirectiveKind.SEARCH_FOR, DirectiveKind.DEFER_REVEAL}
)
"""「对作者请求的结构化理解」用的指令码。"""

MACHINE_DIRECTIVE_KINDS = frozenset(
    {DirectiveKind.TEST_CHARACTER, DirectiveKind.ADVANCE_CLUE}
)
"""「机器写作建议」用的指令码。"""

SAFETY_FACT_TYPES = frozenset({FactType.KNOWS, FactType.BELIEVES})
"""安全相关 RETCON 涉及的事实类型：Canon 纠错完成前不得封存或起草。"""


def _resolved_surface(
    store: StoryGraph,
    project_id: str,
    surface: str | None,
    *,
    what: str,
) -> NodeRef | None:
    if surface is None:
        return None
    resolutions = store.resolve(project_id, [surface])
    node = resolutions[0].unique_node if resolutions else None
    if node is None:
        raise SealRefused(
            f"封存校验失败：{what}「{surface}」解析不出唯一节点，"
            "不能作为安全参数进入 Writer 简报。"
        )
    return NodeRef.of(node)


def _safe_args(
    directive: DirectiveCandidate,
    *,
    resolved: dict[str, NodeRef],
) -> tuple[SafeArg, ...]:
    """一条指令的安全参数：NodeRef 显示名或封闭码，没有自由文本。"""
    out: list[SafeArg] = []
    for key, surface in (
        ("actor", directive.actor_surface),
        ("target", directive.target_surface),
        ("object", directive.object_surface),
        ("location", directive.location_surface),
    ):
        if surface is None:
            continue
        ref = resolved.get(surface)
        if ref is None:
            raise SealRefused(f"封存校验失败：指令参数「{surface}」没有解析到节点。")
        out.append(SafeArg(key=key, value=ref.name))
    return tuple(out)


def _reason_code_for(unknown: EvidenceEnvelope) -> str:
    if unknown.fact_type is FactType.FORESHADOW:
        return "NO_RELIABLE_PRODUCTION_READ"
    if unknown.freshness.value == "STALE":
        return "STALE_EVIDENCE"
    if unknown.completeness.value == "MISSING":
        return "CAPABILITY_MISSING"
    return "UNKNOWN"


def seal_scene_brief(
    *,
    store: StoryGraph,
    proposal: SceneProposal,
    inspection: CalibrationReport,
    author_turn: AuthorTurnRef,
    confirmation_turn: AuthorTurnRef | None,
    author_choice: AuthorResolution | None,
    canon_version: int,
    target_sha256: str,
    retcon_fact_ids: tuple[str, ...] = (),
) -> SealedCalibration:
    """把一份 inspection 封存成 READY_FOR_DRAFT 的不可变产物。

    Raises:
        SealRefused: 任何一项校验不过（状态 / 项目 / 章号 / turn / 水位 /
            未解析 surface / 安全相关 RETCON / 不可见的引用）。
    """
    if inspection.sealability is not Sealability.READY_TO_SEAL:
        raise SealRefused(
            f"这份校准报告还不能封存（{inspection.sealability.value}）："
            "先解决确定性冲突，或让作者回答需要他决定的问题。"
        )
    if proposal.chapter != inspection.chapter:
        raise SealRefused(
            f"提案章号 {proposal.chapter} 与校准报告章号 {inspection.chapter} 不一致。"
        )
    if confirmation_turn is None and (
        author_turn.turn_id != inspection.author_turn_id
        or author_turn.request_sha256 != inspection.author_request_sha256
    ):
        raise SealRefused("当前作者消息与这份校准不是同一轮，旧产物已过期，请重新校准。")
    watermark = inspection.source_watermark
    if watermark.canon_version != canon_version:
        raise SealRefused(
            f"图谱水位变了（校准时的 {watermark.canon_version} → 现在的 {canon_version}），"
            "请重新校准。"
        )
    if watermark.target_sha256 != target_sha256:
        raise SealRefused("目标章正文在这份校准之后变了，请重新校准。")

    # ── 解析全部 surface（seal 时重新验证，不许漏过一个）───────────────────
    resolved: dict[str, NodeRef] = {}
    for directive in proposal.directive_candidates:
        for surface in (
            directive.actor_surface,
            directive.target_surface,
            directive.object_surface,
            directive.location_surface,
        ):
            if surface and surface not in resolved:
                resolved[surface] = _resolved_surface(
                    store, inspection.project_id, surface, what="指令参数"
                )
    viewpoint_ref = (
        _resolved_surface(
            store, inspection.project_id, proposal.viewpoint_surface, what="视角人物"
        )
        if proposal.viewpoint_surface
        else None
    )

    cast_names = tuple(member.node.name for member in inspection.normalized_cast)

    # ── SceneBrief ────────────────────────────────────────────────────────
    facts_by_id = {item.item_id: item for item in inspection.agent_safe_facts}

    # RETCON：非安全旧事实可以作为 AUTHOR_INTENT 进入本稿，同时产 handoff；
    # 安全相关 RETCON（KNOWS/BELIEVES/秘密）必须走作者侧 Canon 纠错并重新校准。
    if author_choice is AuthorResolution.RETCON_NON_SAFETY:
        if not retcon_fact_ids:
            raise SealRefused(
                "选择「推翻旧设定」时必须点名要推翻的旧事实（校准报告里的 item_id）。"
            )
        for fact_id in retcon_fact_ids:
            envelope = facts_by_id.get(fact_id)
            if envelope is None:
                raise SealRefused(f"封存校验失败：要推翻的事实 {fact_id} 不在校准报告里。")
            if envelope.fact_type in SAFETY_FACT_TYPES:
                raise SealRefused(
                    f"事实 {fact_id} 涉及知情/秘密（{envelope.fact_type.value}），"
                    "属于安全相关 RETCON：必须先走作者侧 Canon 声明/纠错链并重新校准，"
                    "不能借 SceneBrief 绕过旧 Canon 的后端约束。"
                )

    continuity: list[ContinuityFact] = []
    for index, item_id in enumerate(inspection.writer_safe_fact_ids):
        envelope = facts_by_id.get(item_id)
        if envelope is None or envelope.writer_visibility is WriterVisibility.HIDDEN:
            # seal 时重新验证可见性：报告里引用到的必须是 writer-safe 项。
            raise SealRefused(f"封存校验失败：报告项 {item_id} 不可见或不存在。")
        continuity.append(
            ContinuityFact(
                item_id=f"cf:{index}",
                report_item_id=item_id,
                basis=envelope.kind,
                fact_type=envelope.fact_type,
                display_text=envelope.display_text,
                strength="HARD" if envelope.kind is EpistemicKind.AUTHOR_CANON_FACT else "SUGGESTION",
            )
        )

    projected: list[ProjectedDirective] = []
    machine: list[MachineDirective] = []
    for index, directive in enumerate(proposal.directive_candidates):
        args = _safe_args(directive, resolved=resolved)
        if directive.kind in REQUEST_DIRECTIVE_KINDS:
            projected.append(
                ProjectedDirective(
                    item_id=f"proj:{index}",
                    directive_kind=directive.kind,
                    safe_args=args,
                    basis=EpistemicKind.MACHINE_INFERENCE,
                    author_turn_ref=author_turn,
                )
            )
        elif directive.kind in MACHINE_DIRECTIVE_KINDS:
            machine.append(
                MachineDirective(
                    item_id=f"mach:{index}",
                    directive_kind=directive.kind,
                    safe_args=args,
                    basis=EpistemicKind.MACHINE_INFERENCE,
                )
            )
        else:
            raise SealRefused(f"指令码 {directive.kind.value} 不在封闭指令集里。")

    author_instructions: list[AuthorInstruction] = []
    card_hash = ""
    if author_choice is not None:
        if confirmation_turn is None or confirmation_turn.turn_id == inspection.author_turn_id:
            raise SealRefused(
                "作者确认必须在封存之前产生一条新的作者消息（看过任务卡之后回答）。"
                "仅有指向模型文字的 answer ID 不算作者确认。"
            )
        card, card_hash = render_author_card(
            chapter=proposal.chapter,
            proposal=proposal,
            cast_names=cast_names,
        )
        union_hash = sha256(
            proposal.model_dump_json().encode("utf-8")
        ).hexdigest()
        safe_args = tuple(
            arg
            for directive in proposal.directive_candidates
            for arg in _safe_args(directive, resolved=resolved)
        )
        ref = AuthorInstructionRef(
            turn_id=confirmation_turn.turn_id,
            request_sha256=confirmation_turn.request_sha256,
            card_hash=card_hash,
            directive_union_hash=union_hash,
            safe_args=safe_args,
            confirmation_id=confirmation_turn.turn_id,
        )
        author_instructions = [
            AuthorInstruction(
                item_id=f"auth:{index}",
                directive_kind=directive.kind,
                safe_args=_safe_args(directive, resolved=resolved),
                basis=EpistemicKind.AUTHOR_INTENT,
                author_instruction_ref=ref,
            )
            for index, directive in enumerate(proposal.directive_candidates)
        ]

    do_not_assume = [
        DoNotAssume(
            item_id=f"dna:{index}",
            reason_code=_reason_code_for(unknown),
            source_refs=(unknown.item_id,),
        )
        for index, unknown in enumerate(inspection.unknowns)
    ]

    brief = SceneBrief(
        chapter=proposal.chapter,
        intended_cast=tuple(member.node for member in inspection.normalized_cast),
        author_instructions=tuple(author_instructions),
        projected_request_directives=tuple(projected),
        continuity_facts=tuple(continuity),
        machine_directives=tuple(machine),
        event_beats=(),
        do_not_assume=tuple(do_not_assume),
        viewpoint_ref=viewpoint_ref,
        tone_code=proposal.tone_code,
        pacing_code=proposal.pacing_code,
        ending_code=proposal.ending_code,
    )

    sealed_turn = confirmation_turn if confirmation_turn is not None else author_turn
    return SealedCalibration(
        calibration_id=new_id(EntityType.CALIBRATION, inspection.project_id),
        project_id=inspection.project_id,
        chapter=proposal.chapter,
        author_turn_id=sealed_turn.turn_id,
        author_request_sha256=sealed_turn.request_sha256,
        goal_spec=render_goal_spec(proposal, cast_names),
        author_resolution=author_choice,
        source_watermark=watermark,
        source_inspection_id=inspection.id,
        scene_brief=brief,
        status=CalibrationStatus.READY_FOR_DRAFT,
    )
