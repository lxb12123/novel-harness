"""text/mentions.py —— R2/R3/R5 的共同前置（PLAN §9）。

三条机械纪律各钉一条：
1. 最长优先：`顾清音` 排在 `清音`/`顾` 前面，不被切成更短的称呼。
2. 只匹配调用方给的 surface：`清音` 不可用时，`琴声清音袅袅` 天然不命中。
3. 锚复用 `anchor.Located`：para_index / occurrence_k 的语义和证据锚同源。
"""

from __future__ import annotations

from novel_harness.text.mentions import compile_alternation, find_mentions


def test_longest_surface_wins_at_the_same_start() -> None:
    pattern = compile_alternation(["清音", "顾清音", "顾"])
    hits = find_mentions(["顾清音道：「……」"], pattern)
    assert [h.matched_text for h in hits] == ["顾清音"]


def test_short_alias_outside_the_surface_list_does_not_match() -> None:
    # 「清音」没进 alternation（usable_for_rules=False 的典型情形），
    # 于是「琴声清音袅袅」不产生任何 mention——这不是语义过滤，是它根本不在名单里。
    pattern = compile_alternation(["顾清音"])
    assert find_mentions(["琴声清音袅袅"], pattern) == []


def test_matches_are_non_overlapping_and_anchored_per_paragraph() -> None:
    pattern = compile_alternation(["萧决", "苏挽"])
    paragraphs = ["萧决道：", "苏挽道：", "萧决萧决"]
    hits = find_mentions(paragraphs, pattern)
    assert [(h.para_index, h.occurrence_k, h.matched_text) for h in hits] == [
        (0, 0, "萧决"),
        (1, 0, "苏挽"),
        (2, 0, "萧决"),
        (2, 1, "萧决"),
    ]


def test_empty_surface_list_never_raises_and_never_matches() -> None:
    pattern = compile_alternation([])
    assert find_mentions(["任何正文"], pattern) == []


def test_surfaces_are_regex_escaped() -> None:
    pattern = compile_alternation(["A+B", "萧(决)"])
    assert [h.matched_text for h in find_mentions(["见 A+B 与萧(决)"], pattern)] == [
        "A+B",
        "萧(决)",
    ]
