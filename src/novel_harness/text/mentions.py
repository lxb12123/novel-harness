"""正文里的称呼命中 —— R2/R3/R5 与角色册验收的共同前置（PLAN §9）。

纯字符串函数：surface 列表 → 正则 alternation → 逐段命中。**不做任何语义判断**
（ADR 0005）。锚复用 `text/anchor.Located`：`(para_index, quote, occurrence_k)`
全库只有一份定义，这里不另造。

── 两条机械纪律 ──────────────────────────────────────────────────────────

1. **最长优先**：`compile_alternation` 按长度降序排。`顾清音` 排在 `清音` 前面，
   `顾清音` 不会「被切成」`顾` + `清音`；同一起点上 Python 的正则取 alternation
   里先列出的那支 = 最长匹配（PLAN §8 的 `test_mentions.py`）。
2. **只收调用方给的 surface**：本模块不判断「清音」该不该匹配——那归
   `usable_for_rules`（作者/UI 对 surface 本身的判定，ADR 0004）。R2/R3 必须传
   `store.resolve(rules_only=True)` 的结果（`graph/store.py` 的契约原文：
   「text/mentions.py 靠它拿全部 surface 去编译那条正则 alternation」）。
   于是 `琴声清音袅袅` 在 `清音` 不可用时天然不命中——不是这里语义过滤，
   是它根本没进 alternation。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .anchor import Located

__all__ = ["compile_alternation", "find_mentions"]


def compile_alternation(surfaces: Sequence[str]) -> re.Pattern[str]:
    """编译 alternation。**长度降序**，命中 = 子串，转义全部 surface。

    空列表/全空串 → 一个永不命中的模式（调用方拿它跑正文，得到空结果，
    不许 raise——空角色册是合法状态）。
    """
    ordered = sorted(
        (s for s in surfaces if s), key=lambda s: (-len(s), s)
    )
    if not ordered:
        return re.compile(r"(?!x)x")
    return re.compile("|".join(re.escape(s) for s in ordered))


def find_mentions(
    paragraphs: Sequence[str],
    pattern: re.Pattern[str],
) -> list[Located]:
    """逐段找命中。`occurrence_k` 是**这一段之内**的第 k 次（`anchor.Located` 的契约）。

    非重叠（`finditer` 语义）：`"萧决萧决"` 给 2 个命中，不是 3 个。
    """
    hits: list[Located] = []
    for para_index, para in enumerate(paragraphs):
        k = 0
        for match in pattern.finditer(para):
            hits.append(
                Located(
                    para_index=para_index,
                    occurrence_k=k,
                    matched_text=match.group(0),
                )
            )
            k += 1
    return hits
