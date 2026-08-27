"""语言判定 —— `project.language` 的唯一计算处（国际化第一批 ②）。

判据是集合判断，不是语义判断（ADR 0005）：数 CJK 表意字符占「非空白字符」的比例，
过阈值判中文，否则判英文。约束 6「不让作者填章号」的同一个方向、ADR 0018
「cast 是推出来的不是声明的」——能从正文算出来的东西就别做成表单。

**纯函数，不碰磁盘也不碰库**（同 `chapterize()` 的形状）：给一段正文样本，
吐一个判定或 `None`。样本从哪来、判定结果写不写回 `project` 表，是调用方
（`importer.language_sample()` + `project.apply_detected_language()`）的事。
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[一-鿿]")
"""CJK 统一表意文字主区块（U+4E00–U+9FFF）。故意不含扩展区、标点、全角符号——
本模块要的是「这段字是不是中文叙述」，多算标点不会让判断更准，只会让极端情况
（纯符号行）更难看懂结果。"""
_NON_SPACE_RE = re.compile(r"\S")

MIN_SAMPLE_CHARS = 50
"""样本太短就不判——短到这个地步（比如刚建好、只有一行空章标的新书），CJK 占比
本身就不可靠，硬判一个答案不如什么都不说（返回 `None`，调用方保留现状）。"""

CJK_RATIO_THRESHOLD = 0.15
"""CJK 字符占非空白字符的比例过这个数就判中文。**低阈值是有意的、不对称的**：
夹杂大量英文引文的中文小说，叙述本身仍然是中文，CJK 占比会远高于这个数；
反过来纯英文小说的 CJK 占比恒为 0。宁可放宽中文这边的门槛，也别让引文
把判断误伤到英文——这条不对称对着「作者在中文小说里插了不少英文对话/引文」
这个真实场景设计，见国际化第一批 ② 的任务说明。"""


def detect_language(text: str) -> str | None:
    """从一段正文样本判定 `"zh"` / `"en"`。

    样本非空白字符数 < `MIN_SAMPLE_CHARS` 时返回 `None`——调用方应保留当前值，
    不能拿一个还没见过正文的项目去覆盖已有的判定（或已有的作者手动设置）。
    """
    non_space = len(_NON_SPACE_RE.findall(text))
    if non_space < MIN_SAMPLE_CHARS:
        return None
    cjk = len(_CJK_RE.findall(text))
    return "zh" if cjk / non_space >= CJK_RATIO_THRESHOLD else "en"
