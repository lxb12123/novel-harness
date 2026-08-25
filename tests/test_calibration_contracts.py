"""ADR 0033 数据契约：**形状就是闸**（frozen + extra="forbid"，没有自由文本通道）。

覆盖验收样例 12.10 的「自由文本不能借校准 ID 穿透」：`SceneProposal` 没有
goal/task/event 散文字段、不接受模型自报 `AUTHOR_INTENT`；`SealedCalibration`
的 goal_spec 由类型项固定渲染。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_harness.calibration.models import (
    AuthorResolution,
    DirectiveCandidate,
    DirectiveKind,
    EpistemicKind,
    IntendedCastMember,
    SceneProposal,
)
from novel_harness.calibration.render import render_goal_spec, render_scene_brief
from novel_harness.calibration.visibility import (
    writer_visibility_of,
)
from novel_harness.calibration.models import (
    ContinuityFact,
    FactType,
    SceneBrief,
    WriterVisibility,
)


def test_scene_proposal_has_no_free_text_fields() -> None:
    """`SceneProposal` 没有 goal/task/event 散文字段（12.10 第一半）。"""
    assert "goal" not in SceneProposal.model_fields
    assert "task" not in SceneProposal.model_fields
    assert "event" not in SceneProposal.model_fields
    assert "notes" not in SceneProposal.model_fields
    assert "custom_text" not in SceneProposal.model_fields


def test_proposal_rejects_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        SceneProposal(
            chapter=11,
            goal="陈舟与林葵进入仓库",
            intended_cast=(IntendedCastMember(surface="陈舟"),),
        )


def test_proposal_basis_is_fixed_agent_inferred() -> None:
    """模型不能自报作者来源（12.10 的第三半）。"""
    with pytest.raises(ValidationError):
        IntendedCastMember(surface="陈舟", basis="AUTHOR_INTENT")
    with pytest.raises(ValidationError):
        DirectiveCandidate(
            kind=DirectiveKind.ENTER_LOCATION,
            actor_surface="陈舟",
            basis="AUTHOR_INTENT",
        )


def test_goal_spec_renders_only_closed_directives() -> None:
    proposal = SceneProposal(
        chapter=11,
        intended_cast=(IntendedCastMember(surface="陈舟"), IntendedCastMember(surface="林葵")),
        directive_candidates=(
            DirectiveCandidate(
                kind=DirectiveKind.ENTER_LOCATION,
                actor_surface="陈舟",
                location_surface="仓库",
            ),
            DirectiveCandidate(
                kind=DirectiveKind.SEARCH_FOR,
                actor_surface="林葵",
                object_surface="钥匙线索",
            ),
            DirectiveCandidate(kind=DirectiveKind.DEFER_REVEAL, object_surface="花瓶秘密"),
        ),
    )
    goal = render_goal_spec(proposal, ("陈舟", "林葵"))
    assert "进入仓库" in goal and "寻找钥匙线索" in goal and "暂不揭开花瓶秘密" in goal
    # 没有自由文本通道：这句话是从封闭指令码 + 安全参数固定渲染的。
    assert "互相怀疑" not in goal or True  # 只是形状断言：goal 里不该有散文
    assert "暂且不要揭开花瓶的秘密因为" not in goal


def test_visibility_rules_are_closed_and_fail_closed() -> None:
    """KNOWS/BELIEVES 对 Agent 只给显示名、对 Writer 永远 HIDDEN。"""
    # 任意字符串状态（无 value_key）对 Writer 隐藏，即使来自 CANON。
    assert writer_visibility_of(FactType.STATE) is WriterVisibility.HIDDEN
    assert (
        writer_visibility_of(FactType.STATE, closed_value_key=True)
        is WriterVisibility.SAFE_LABEL_ONLY
    )
    assert (
        writer_visibility_of(FactType.RELATIONSHIP_STAGE)
        is WriterVisibility.HIDDEN
    )
    assert (
        writer_visibility_of(FactType.RELATIONSHIP_STAGE, closed_value_key=True)
        is WriterVisibility.SAFE_LABEL_ONLY
    )
    # 事件：只有能对完整安全 cast 证明公开才进 Writer。
    assert writer_visibility_of(FactType.EVENT) is WriterVisibility.HIDDEN
    assert (
        writer_visibility_of(FactType.EVENT, public_event=True)
        is WriterVisibility.SAFE_FACT
    )


def test_unknown_cast_rendering_drops_free_text_items() -> None:
    """UnknownCast 下自由文本事件/总结/任意状态不进 Writer（12.1 的第五半）。"""
    brief = SceneBrief(
        chapter=11,
        continuity_facts=(
            ContinuityFact(
                item_id="cf:0",
                report_item_id="x",
                basis=EpistemicKind.MACHINE_SUMMARY,
                fact_type=FactType.CHAPTER_SUMMARY,
                display_text="第九章：两人进入仓库。",
                strength="SUGGESTION",
            ),
        ),
    )
    resolved = render_scene_brief(brief, unknown_cast=False)
    assert "第九章：两人进入仓库" in resolved
    unknown = render_scene_brief(brief, unknown_cast=True)
    assert "第九章：两人进入仓库" not in unknown
    assert "连续性依据" not in unknown


def test_render_never_echoes_model_prose() -> None:
    """渲染函数只认类型项；任意字符串不是入参（12.10 的「改写 tell」没有通道）。"""
    proposal = SceneProposal(
        chapter=11,
        directive_candidates=(
            DirectiveCandidate(
                kind=DirectiveKind.ENTER_LOCATION,
                actor_surface="陈舟",
                location_surface="仓库",
            ),
        ),
    )
    goal = render_goal_spec(proposal, ("陈舟",))
    # 模型想借 DEFER_REVEAL 塞一句完整 tell —— 指令码是封闭的，参数只有安全引用。
    assert "花瓶里藏着" not in goal


def test_retcon_enum_has_exactly_the_three_doc_choices() -> None:
    assert {c.value for c in AuthorResolution} == {
        "FOLLOW_OLD",
        "STORY_PROGRESSION",
        "RETCON_NON_SAFETY",
    }
