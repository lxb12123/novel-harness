"""反混淆闸门的单测（EVAL_PROTOCOL §2 / `eval/confound_lint.py`）。

这道闸门的产出只驱动一个开关（`GateInput.confound_ok` → 禁不禁用 FORM-PIVOT），
所以测试分成三类，缺一类都不够：

1. **判据本身对不对**（专名差异指得出名字、字数边界两侧各一条）；
2. **判据是不是对称的**——把 X1、X2 对调裁决必须不变。不对称的判据意味着结论由
   「哪一列写在前面」决定，那正是 `score.decide()` 平票 bug 的同一个病；
3. **它罩不住什么**。下面 `# 盲区` 那一段里的测试**断言的是「它看不见」**，不是缺陷记录：
   把盲区写成会红的测试，才不会有人某天把绿灯读成「反混淆铁律成立」。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from novel_harness.eval import confound_lint as mod
from novel_harness.eval.confound_lint import ConfoundReport, confound_lint, visible_len

# 小册子里的名字（`synth/booklet.toml` 的角色 / 秘密标签 / 未来实体）。
# 注意进 prompt 的是**标签** `血脉秘密`，不是它的 tell `玄血蛊`——tell 在这一侧根本不存在。
NAMES = ["萧决", "苏挽", "血脉秘密", "血枭盟"]

FILLER = "字"
"""填充字符。**不许是任何已知专名的子串，也不许含任何已知专名**，否则长度测试会顺带
改变专名集合，一条测试同时动两个变量就说明不了任何事。"""


def _text(names: Sequence[str], length: int) -> str:
    """拼一段恰好 `length` 个非空白字符、且含 `names` 里每一个名字的文本。"""
    head = "".join(names)
    if len(head) > length:
        raise ValueError(f"{length} 个字装不下这些名字（需要 {len(head)}）")
    return head + FILLER * (length - len(head))


# ══════════════════════════════════════════════════════════════════════════
# 预注册的阈值
# ══════════════════════════════════════════════════════════════════════════


def test_the_tolerance_is_the_preregistered_one() -> None:
    """**这条守的是「不能事后挪及格线」**，同 `test_eval_score.py` 里那条。

    15% 来自 2026-07-25 冻结的 EVAL_PROTOCOL.md §2，在第一次生成之前就定死了。
    它红了只有两种可能：有人改了阈值（= 改卷子），或者协议出了新修正案而这里没跟上。
    两种都必须停下来看，不许直接改数字让它变绿。
    """
    assert mod.LEN_TOLERANCE == 0.15


# ══════════════════════════════════════════════════════════════════════════
# 专名：集合判断
# ══════════════════════════════════════════════════════════════════════════


def test_identical_arms_pass() -> None:
    text = _text(NAMES, 200)
    r = confound_lint(text, text, known_names=NAMES)
    assert r.ok
    assert r.reasons == []
    assert (r.only_in_x1, r.only_in_x2) == ([], [])
    assert r.len_ratio == 1.0


def test_an_extra_name_in_x1_is_named() -> None:
    """报警必须**说得出是谁**——§6 第 3 行的动作是「重渲染对齐后重跑 B」，
    而一条没有名字的报警没法照着改。
    """
    x1 = _text(NAMES, 200)
    x2 = _text(["萧决", "苏挽", "血脉秘密"], 200)
    r = confound_lint(x1, x2, known_names=NAMES)
    assert not r.ok
    assert r.only_in_x1 == ["血枭盟"]
    assert r.only_in_x2 == []
    assert len(r.reasons) == 1
    assert "血枭盟" in r.reasons[0]


def test_an_extra_name_in_x2_is_named() -> None:
    x1 = _text(["萧决", "苏挽", "血脉秘密"], 200)
    x2 = _text(NAMES, 200)
    r = confound_lint(x1, x2, known_names=NAMES)
    assert not r.ok
    assert r.only_in_x1 == []
    assert r.only_in_x2 == ["血枭盟"]


def test_the_verdict_does_not_depend_on_argument_order() -> None:
    """**这条钉的是「±15% 取哪个分母」那个判断**（模块 docstring 第三节）。

    以 X1 为基准的读法（`|a−b|/x1`）在这里会给出两个不同的裁决：120 比 100 多 20%（报警），
    100 比 120 少 16.7%（也报警）——但换一对数字就会分家，比如 100 vs 117 是 17% / 14.5%。
    裁决由「哪一列写在前面」决定，正是修正案 3 那份见证数据里的病：同一份数字把 X1、X2
    两列对调，结论就翻面。所以分母只能是 `min` 或 `max`，本模块取更严的 `min`。
    """
    x1 = _text(NAMES, 100)
    x2 = _text(["萧决", "苏挽"], 117)
    forward = confound_lint(x1, x2, known_names=NAMES)
    backward = confound_lint(x2, x1, known_names=NAMES)
    assert forward.ok is backward.ok
    assert forward.len_ratio == backward.len_ratio
    assert forward.only_in_x1 == backward.only_in_x2
    assert forward.only_in_x2 == backward.only_in_x1
    assert forward.reasons != backward.reasons  # 只有措辞里的 X1/X2 换了位置


# ══════════════════════════════════════════════════════════════════════════
# 字数：口径与边界
# ══════════════════════════════════════════════════════════════════════════


def test_visible_len_ignores_layout() -> None:
    """字数口径 = 非空白字符数，含全角空格与各种换行（模块 docstring 第二节）。"""
    assert visible_len("") == 0
    assert visible_len(" \n\t　") == 0
    assert visible_len("萧决\n苏挽") == 4
    assert visible_len("萧决、苏挽") == 5  # 标点算数


def test_layout_difference_alone_does_not_alarm() -> None:
    """**这条是「为什么不数空白」的活证据。**

    同样的内容，X1 排成清单（每条一行）、X2 连成散文。按 `len(s)` 数，清单多出 6 个换行
    = 多 6/38 ≈ 16% → 报警；而那正是协议允许存在的**唯一**那个差异，报它就是纯假警报。
    一道天天误报的守卫会被人关掉，关掉的守卫等于没有。
    """
    items = ["萧决 KNOWS 血脉秘密", "苏挽 UNKNOWN 血脉秘密", "血枭盟 首现 第8章"]
    x1 = "\n".join(f"- {it}" for it in items)
    x2 = "；".join(items) + "。"
    assert len(x1) != len(x2)
    r = confound_lint(x1, x2, known_names=NAMES)
    assert r.ok, r.reasons
    assert r.len_x1 == visible_len(x1)


def test_length_just_inside_tolerance_passes() -> None:
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 114), known_names=NAMES)
    assert r.ok, r.reasons
    assert r.len_ratio == pytest.approx(1.14)


def test_length_just_outside_tolerance_alarms() -> None:
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 116), known_names=NAMES)
    assert not r.ok
    assert (r.len_x1, r.len_x2) == (100, 116)
    assert len(r.reasons) == 1
    assert "字数比" in r.reasons[0]


def test_the_boundary_is_inclusive() -> None:
    """`max/min == 1.15` 判 ok。含不含等号是个必须写下来的决定，不能靠读代码猜。"""
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 115), known_names=NAMES)
    assert r.ok, r.reasons
    assert r.len_ratio == pytest.approx(1.15)


def test_the_stricter_of_the_two_symmetric_readings_is_used() -> None:
    """100 vs 117：按 `|a−b|/max` 读是 14.5%（放行），按 `|a−b|/min` 读是 17%（报警）。

    本模块取后者。理由是代价不对称：假警报只丢一轮次要决策 B（§6 自带「重渲染对齐后
    重跑 B」的补救），假放行会让一份被污染的 B 被当真、生产默认因为错误的理由翻成 X2。
    这条测试就是那个取舍的落点——它绿着，说明我们站在「宁可报警」那一边。
    """
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 117), known_names=NAMES)
    assert abs(117 - 100) / 117 < mod.LEN_TOLERANCE  # 宽读法会放行
    assert not r.ok  # 我们不


# ══════════════════════════════════════════════════════════════════════════
# 空的东西：空集相等不是一致性证据
# ══════════════════════════════════════════════════════════════════════════


def test_both_arms_empty_is_not_ok() -> None:
    """两条子检查逐字全过（等长、专名集合相等），而它们过得毫无信息。

    这里报 ok 就是「一张漂亮的空表 + exit 0」——`demo.sh` 自己警告的那种绿。
    """
    r = confound_lint("", "", known_names=NAMES)
    assert not r.ok
    assert r.len_ratio == 1.0  # 都是 0，「等长」是真的；空臂那条报警由另一条负责
    assert len(r.reasons) == 2
    assert any("两臂都" in why for why in r.reasons)


def test_one_empty_arm_is_not_ok_and_ratio_is_inf() -> None:
    r = confound_lint(_text(NAMES, 200), "", known_names=NAMES)
    assert not r.ok
    assert r.len_ratio == float("inf")
    assert r.only_in_x1 == sorted(NAMES)
    assert any("X2" in why and "空" in why for why in r.reasons)


def test_neither_arm_mentions_a_known_name_is_not_ok() -> None:
    """**这一条是本模块自己做的判断，不在协议里，所以理由必须写下来。**

    两边都不含任何已知专名 → 集合确实相等，但那是两个空集相等。这道闸门唯一的输出是
    一个「关掉就更保守」的开关（关掉 = 不评 FORM-PIVOT = 生产默认不变），
    **没有信息时它应该报那个保守值**。

    往哪边错：调用方漏传 / 传错 `known_names` 会当场收到报警，而不是静默全绿。有意的。
    """
    blank = FILLER * 100
    r = confound_lint(blank, blank, known_names=NAMES)
    assert not r.ok
    assert len(r.reasons) == 1
    assert "不含任何已知专名" in r.reasons[0]


def test_empty_known_names_is_not_ok() -> None:
    """名单为空 = 闸门什么都没看。同上一条，报保守值。"""
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 100), known_names=[])
    assert not r.ok
    assert "0 个" in r.reasons[0]


def test_blank_names_in_the_whitelist_are_dropped() -> None:
    """`"" in text` 恒真。留着空名字会让每一臂都「命中」一个不存在的名字，
    专名集合永远相等——闸门当场变成恒绿。同 `eval/leak.py::_hits` 里那个 `if t`。
    """
    x1 = _text(["萧决"], 100)
    x2 = _text(["萧决", "苏挽"], 100)
    r = confound_lint(x1, x2, known_names=["", "   ", "\n", "萧决", "苏挽"])
    assert not r.ok
    assert r.only_in_x2 == ["苏挽"]
    assert "" not in r.only_in_x1 + r.only_in_x2


# ══════════════════════════════════════════════════════════════════════════
# 报告对象的不变式
# ══════════════════════════════════════════════════════════════════════════


def test_ok_is_exactly_reasons_empty() -> None:
    """`ok` 不是第三个独立判断，它就是 `not reasons`。两者能分家的那一刻，
    报告会说「不 ok」却说不出为什么，或者说得出理由却放行。
    """
    cases = [
        (_text(NAMES, 200), _text(NAMES, 200)),
        (_text(NAMES, 100), _text(NAMES, 400)),
        (_text(NAMES, 200), _text(["萧决"], 200)),
        ("", ""),
        (FILLER * 50, FILLER * 50),
    ]
    for x1, x2 in cases:
        r = confound_lint(x1, x2, known_names=NAMES)
        assert r.ok is (r.reasons == []), (x1[:8], x2[:8], r.reasons)


def test_all_failing_checks_are_reported_not_just_the_first() -> None:
    """不短路。落空时要说得出**哪几条**落空了——这段话会原样进 ADR 0009，
    而一份说错了自己失败原因的记录，比没有记录更糟（同 `score.py::_pass_checks`）。
    """
    r = confound_lint(_text(NAMES, 100), _text(["萧决"], 400), known_names=NAMES)
    assert not r.ok
    assert len(r.reasons) == 2
    assert any("专名集合不同" in why for why in r.reasons)
    assert any("字数比" in why for why in r.reasons)


def test_report_is_frozen() -> None:
    r = confound_lint(_text(NAMES, 100), _text(NAMES, 100), known_names=NAMES)
    with pytest.raises(ValidationError):
        r.ok = True  # type: ignore[misc]


def test_report_is_a_pydantic_model() -> None:
    """出参不许是 dict / Row（CLAUDE.md 头号错误第 4 条）。"""
    assert isinstance(
        confound_lint("萧决", "萧决", known_names=NAMES),
        ConfoundReport,
    )


# ══════════════════════════════════════════════════════════════════════════
# 盲区 —— 下面每一条断言的都是「它看不见」
# ══════════════════════════════════════════════════════════════════════════
#
# 把盲区写成会红的测试，才不会有人某天把这道闸门的绿灯读成「反混淆铁律成立」。
# 这些格子由 ADR 0010 D4（三臂共用同一个 `_base`）+ review 守，不由本模块守。


def test_a_behaviour_instruction_slips_through() -> None:
    """**这是本模块最大的一个盲区，而且它正好是协议 §2 要修的那件事。**

    PLAN §5.7 的内建混淆就长这样：X2 比 X1 多一条行为指令。它不含任何已知专名、
    长度增量远小于 15% → 四条子检查逐条全过、报 ok。
    守住它的不是本模块，是 ADR 0010 D4「三臂共用同一个 `_base`，X1/X2 只在它之后
    追加图谱段」+ review。这条测试绿着，说明那一格**只**靠纪律守着。
    """
    x1 = _text(NAMES, 200)
    x2 = x1 + "注意：不要写出上面提到的任何内容。"
    r = confound_lint(x1, x2, known_names=NAMES)
    assert r.ok, r.reasons


def test_repetition_is_invisible() -> None:
    """集合不是多重集：X1 提一次 `萧决`、X2 提十次，专名集合相同。

    长度那一维**可能**兜住一点，但那是碰巧不是设计——这里两臂等长，它一点都没兜住。
    """
    x1 = _text(NAMES, 100)
    x2 = "萧决" * 10 + "苏挽血脉秘密血枭盟" + FILLER * (100 - 20 - 9)
    assert visible_len(x2) == 100
    r = confound_lint(x1, x2, known_names=NAMES)
    assert r.ok, r.reasons


def test_names_outside_the_whitelist_are_invisible() -> None:
    """白名单之外的名字对它**不存在**。调用方给的名单就是这道闸门的全部视野。"""
    x1 = _text(["萧决"], 100)
    x2 = "萧决" + "顾清音" + FILLER * 95
    r = confound_lint(x1, x2, known_names=["萧决"])
    assert r.ok, r.reasons


def test_nested_names_over_report() -> None:
    """互为子串的名字会连坐：X1 只多写了 `血枭盟` 一处，报出来是两个名字。

    方向上只会**多**报警、不会漏报警，所以不修——修它要判「哪个名字才是这里真正出现的
    那一个」，那是语义判断（ADR 0005）。
    """
    known = ["萧决", "血枭", "血枭盟"]
    x1 = _text(["萧决", "血枭盟"], 100)
    x2 = _text(["萧决"], 100)
    r = confound_lint(x1, x2, known_names=known)
    assert not r.ok
    assert r.only_in_x1 == ["血枭", "血枭盟"]


def test_it_never_looks_at_the_shared_base() -> None:
    """它比的是 X1 vs X2，**不是 base**。有人把 tell 拼进 `goal`，三臂同时被污染，
    本模块一个字看不见（ADR 0010 已经点名说过这一格）。
    那一格归 `synth/leak_selfcheck.py`（修正案 4 裁定 B：`goal` 一律不许含 tell）。
    """
    poisoned_base = "本场目标：写萧决服下玄血蛊之后的反应。"  # tell 在 base 里
    x1 = poisoned_base + _text(NAMES, 100)
    x2 = poisoned_base + _text(NAMES, 100)
    r = confound_lint(x1, x2, known_names=NAMES)
    assert r.ok, r.reasons
