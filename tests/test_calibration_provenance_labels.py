"""写作简报里的**出处**标签：八类出处各有各的档，且只表出处不表效力。

这条缝坏掉时代表什么：
- 某一类出处被塌进别人的档 —— 最贵的一种是作者亲手立的事实被印成「机器」，
  模型（今天）和作者（它上屏之后）都会照着这个假出处低估它；
- `UNKNOWN` 被印成「推演」—— 它的定义是「不得补写成事实」，印成推演就成了
  一条弱事实，而不是一个洞；
- 出处标签里混进了效力词（硬依据 / 参考）—— 那两个词是另一个字段的活，
  合成一个标签就会出现「机器推演 · 硬依据」这种读不通的组合。

**穷举**写在这儿是有意的：以后新增一类 `EpistemicKind` 而忘了给它定档，
这几条会直接红，而不是等到某天有人在 prompt 里看见一个错标签。
"""

from __future__ import annotations

import pytest

from novel_harness.calibration.models import (
    ContinuityFact,
    DirectiveKind,
    EpistemicKind,
    FactType,
    MachineDirective,
    SafeArg,
    SceneBrief,
)
from novel_harness.calibration.render import render_scene_brief

_FACT_TEXT = "苏挽 位于 断魂崖下"

# 从枚举名派生，不手抄名单：新增一类 AUTHOR_* / MACHINE_* 会自动进入这几条守卫。
_AUTHOR_KINDS = tuple(k for k in EpistemicKind if k.name.startswith("AUTHOR_"))
_MACHINE_KINDS = (
    EpistemicKind.EXTRACTED_CURRENT,
    EpistemicKind.OBSERVED_TEXT,
    EpistemicKind.MACHINE_SUMMARY,
    EpistemicKind.MACHINE_INFERENCE,
)


def _fact_line(kind: EpistemicKind, *, strength: str = "HARD") -> str:
    """走公开渲染口，不去戳私有映射表 —— 钉的是印出来的那行字。"""
    brief = SceneBrief(
        chapter=11,
        continuity_facts=(
            ContinuityFact(
                item_id="cf:0",
                report_item_id="r:0",
                basis=kind,
                fact_type=FactType.LOCATION,
                display_text=_FACT_TEXT,
                strength=strength,
            ),
        ),
    )
    hits = [line for line in render_scene_brief(brief).splitlines() if _FACT_TEXT in line]
    assert len(hits) == 1, hits
    return hits[0]


def _basis_label(kind: EpistemicKind) -> str:
    """从 `- 正文（出处 · 效力）` 里取出「出处」那半。"""
    inner = _fact_line(kind).split("（", 1)[1].rstrip("）")
    return inner.split(" · ", 1)[0]


@pytest.mark.parametrize("kind", list(EpistemicKind))
def test_every_epistemic_kind_has_its_own_label(kind: EpistemicKind) -> None:
    """穷举：每一类出处都渲染得出一个非空标签，没有哪一类掉进「默认档」。"""
    label = _basis_label(kind)
    assert label, kind


def test_author_sourced_kinds_never_render_as_machine() -> None:
    """作者来源的三类里没有一类会印成「机器」。

    这是本条的主症状：`AUTHOR_CANON_FACT`（作者经声明链立的事实）原来印成
    「机器推演」，模型据此把作者的话当成机器的猜测。
    """
    assert EpistemicKind.AUTHOR_INTENT in _AUTHOR_KINDS
    for kind in _AUTHOR_KINDS:
        label = _basis_label(kind)
        assert label.startswith("作者"), (kind, label)
        assert "机器" not in label, (kind, label)


def test_machine_sourced_kinds_never_claim_the_author() -> None:
    """反向：机器来源的四类不许冒充作者（标签越权比标低更贵）。"""
    for kind in _MACHINE_KINDS:
        label = _basis_label(kind)
        assert label.startswith("机器"), (kind, label)
        assert "作者" not in label, (kind, label)


def test_evidence_backed_machine_is_distinguishable_from_a_guess() -> None:
    """「从正文来的」和「真正的猜测」必须是两个标签，否则这一档白分。"""
    guess = _basis_label(EpistemicKind.MACHINE_INFERENCE)
    for kind in (
        EpistemicKind.EXTRACTED_CURRENT,
        EpistemicKind.OBSERVED_TEXT,
        EpistemicKind.MACHINE_SUMMARY,
    ):
        assert _basis_label(kind) != guess, kind


def test_author_confirmation_is_distinguishable_from_author_record() -> None:
    """作者当场点头的 vs 作者写下的：两回事，不能共用一个标签。"""
    assert _basis_label(EpistemicKind.AUTHOR_INTENT) != _basis_label(
        EpistemicKind.AUTHOR_CANON_FACT
    )


def test_unknown_is_a_hole_not_a_weak_guess() -> None:
    """`UNKNOWN` 的定义是「不得补写成事实」—— 印成「推演」会把洞说成弱事实。"""
    label = _basis_label(EpistemicKind.UNKNOWN)
    assert "推演" not in label, label
    assert "机器" not in label, label
    assert "作者" not in label, label
    others = {_basis_label(k) for k in EpistemicKind if k is not EpistemicKind.UNKNOWN}
    assert label not in others, label


def test_provenance_label_carries_no_strength_word() -> None:
    """出处那一半不许混进效力词 —— 效力由后一半（硬依据 / 参考）单独负责。"""
    for kind in EpistemicKind:
        label = _basis_label(kind)
        assert "依据" not in label, (kind, label)
        assert "参考" not in label, (kind, label)


def test_strength_and_provenance_vary_independently() -> None:
    """两个字段各管一半：换出处不动效力，换效力不动出处。

    并直接钉住病症原文「机器推演 · 硬依据」—— 机器猜的东西不会是硬依据。
    """
    hard = _fact_line(EpistemicKind.AUTHOR_CANON_FACT, strength="HARD")
    soft = _fact_line(EpistemicKind.AUTHOR_CANON_FACT, strength="SUGGESTION")
    assert "作者记录 · 硬依据" in hard, hard
    assert "作者记录 · 参考" in soft, soft
    assert "机器推演 · 硬依据" not in hard, hard

    machine_hard = _fact_line(EpistemicKind.MACHINE_INFERENCE, strength="HARD")
    assert "机器推演 · 硬依据" in machine_hard, machine_hard


def test_machine_directive_label_is_read_from_the_field() -> None:
    """【机器写作建议】那行的出处也来自 `basis`，不是第二份写死的「机器推演」。

    两处各写一份标签正是这条缝原本的病：改了一处忘了另一处，简报就自相矛盾。
    """
    brief = SceneBrief(
        chapter=11,
        machine_directives=(
            MachineDirective(
                item_id="mach:0",
                directive_kind=DirectiveKind.ADVANCE_CLUE,
                safe_args=(SafeArg(key="object", value="断魂崖"),),
                basis=EpistemicKind.MACHINE_SUMMARY,
            ),
        ),
    )
    rendered = render_scene_brief(brief)
    assert f"（{_basis_label(EpistemicKind.MACHINE_SUMMARY)}）" in rendered, rendered
