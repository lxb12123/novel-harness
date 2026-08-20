"""`ContinuityConflictHandoff` —— 交给并行通知系统的结构化冲突结果（§6.6）。

只有 `RETCON` 生成持久 handoff：`FOLLOW_OLD` 不产通知；`STORY_PROGRESSION` 只保留
当轮选择。通知侧消费它并负责持久化与展示，**不得重新调用 LLM 再判一次**
「是否冲突」——两套逻辑会给同一件事下出不同结论（ADR 0033）。

本文件只定义 producer 契约与纯构造；落库走 `CalibrationStore.push_handoff`
（`calibration_handoff_outbox`，迁移 017）。
"""

from __future__ import annotations

from .models import (
    ContinuityConflictHandoff,
    ProspectiveStructuralImpact,
    ReferencedFact,
    ReviewCandidate,
    SealedCalibration,
)


def build_retcon_handoff(
    *,
    sealed: SealedCalibration,
    proposal_item: str,
    inferred_tension: str,
    referenced_facts: tuple[ReferencedFact, ...],
    prospective_structural_impacts: tuple[ProspectiveStructuralImpact, ...] = (),
    review_candidates: tuple[ReviewCandidate, ...] = (),
    unknowns: tuple[str, ...] = (),
) -> ContinuityConflictHandoff:
    """构造一份 RETCON handoff。

    `inferred_tension` 是 Agent 带来源的语义张力，**明标 MACHINE_INFERENCE**：
    它不是确定性规则命中，通知侧不得把它渲染成硬结论。修改尚未发生时，
    影响只能叫 `prospective_structural_impacts`，不能叫「已发生的 STALE」。
    """
    return ContinuityConflictHandoff(
        calibration_id=sealed.calibration_id,
        project_id=sealed.project_id,
        chapter=sealed.chapter,
        author_turn_id=sealed.author_turn_id,
        proposal_item=proposal_item,
        inferred_tension=inferred_tension,
        referenced_facts=referenced_facts,
        author_choice="RETCON",
        prospective_structural_impacts=prospective_structural_impacts,
        review_candidates=review_candidates,
        unknowns=unknowns,
        source_watermark=sealed.source_watermark,
    )
