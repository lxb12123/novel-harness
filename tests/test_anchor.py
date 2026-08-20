"""锚点的回归测试。

钉的是三件**改了不会有任何东西报错**的事：
1. `paragraphs()` 的语义（改成按空行分段 → 库里已有的证据锚集体偏移，测试却全绿）
2. `find_all` / `find_one` 对「第 k 次」的一致（不一致 = 证据锚到别处）
3. `matched_text` 是切出来的子串（ADR 0006 配套第 3 条的形状）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_harness.text.anchor import find_all, find_one, paragraphs

FIXTURE = Path(__file__).parent / "fixtures" / "demo_novel.txt"


# ══════════════════════════════════════════════════════════════════════════
# paragraphs：para_index 的定义
# ══════════════════════════════════════════════════════════════════════════


def test_paragraphs_is_splitlines_on_a_real_book() -> None:
    """今天的定义逐字节 = splitlines()。这条红了说明有人改了 `para_index` 的含义。"""
    text = FIXTURE.read_text(encoding="utf-8")
    assert paragraphs(text) == text.splitlines()


def test_paragraphs_keeps_blank_lines() -> None:
    """空行**占一个 index**——`api/app.py` 喂给 CheckContext 的那份也是这样数的。"""
    assert paragraphs("甲\n\n乙\n") == ["甲", "", "乙"]


# ══════════════════════════════════════════════════════════════════════════
# find_all
# ══════════════════════════════════════════════════════════════════════════


def test_three_hits_in_one_paragraph_get_k_0_1_2() -> None:
    hits = find_all(["他笑了，他笑了，他笑了。"], "他笑了")
    assert [h.occurrence_k for h in hits] == [0, 1, 2]
    assert {h.para_index for h in hits} == {0}


def test_hits_across_paragraphs_get_different_para_index() -> None:
    hits = find_all(["萧决点头。", "", "萧决转身。"], "萧决")
    assert [(h.para_index, h.occurrence_k) for h in hits] == [(0, 0), (2, 0)]


def test_no_hit_returns_empty() -> None:
    assert find_all(["萧决点头。"], "顾清音") == []


def test_matched_text_is_sliced_from_the_source() -> None:
    """M1 精确匹配下它等于 quote，但它必须是**切出来的**那一个（ADR 0006 配套第 3 条）。"""
    para = "你身上流的不是萧家的血。"
    (hit,) = find_all([para], "不是萧家的血")
    assert hit.matched_text == "不是萧家的血"
    assert hit.matched_text in para


def test_overlapping_matches_are_not_counted_twice() -> None:
    """非重叠，与 str.count() 同义：`aaa` 里的 `aa` 只有一次。"""
    hits = find_all(["aaa"], "aa")
    assert len(hits) == 1
    assert hits[0].occurrence_k == 0


def test_empty_quote_raises() -> None:
    with pytest.raises(ValueError):
        find_all(["随便什么"], "")


# ══════════════════════════════════════════════════════════════════════════
# find_one：必须和 find_all 说同一种「第 k 次」
# ══════════════════════════════════════════════════════════════════════════


def test_find_one_agrees_with_find_all_on_every_anchor() -> None:
    paras = paragraphs("他笑了，他笑了。\n\n他笑了三声，他笑了。\n")
    hits = find_all(paras, "他笑了")
    assert len(hits) == 4
    for h in hits:
        assert find_one(paras[h.para_index], "他笑了", h.occurrence_k) == h.matched_text


def test_find_one_k_out_of_range_returns_none() -> None:
    assert find_one("他笑了，他笑了。", "他笑了", 2) is None


def test_find_one_miss_returns_none() -> None:
    assert find_one("萧决点头。", "顾清音", 0) is None


def test_find_one_empty_quote_raises() -> None:
    with pytest.raises(ValueError):
        find_one("随便什么", "", 0)
