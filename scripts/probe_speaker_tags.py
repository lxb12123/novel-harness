#!/usr/bin/env python3
"""R5（称呼冲突规则）的生死探针 —— ADR 0005。

问题：ADDRESS_CONFLICT 隐含要求「说话人归属 + 受话人识别」，这是未解的 NLP 问题。
退路是只在**显式说话人标签**（`萧决道：「……」`）内开火。但这条退路只有在
显式标签覆盖了足够多的对白时才成立。

本脚本量的就是这个覆盖率。判据（ADR 0005）：
    >= 10%  → R5 进 v1，只在显式标签 + 受话人在场且唯一时开火
    <  10%  → 当场砍掉 R5，M3 的验收标准相应改写

用法：
    uv run python scripts/probe_speaker_tags.py path/to/novel.txt
    uv run python scripts/probe_speaker_tags.py path/to/novel.txt --chapters 3
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from novel_harness.text.chapterize import chapterize

# 显式说话人标签：名字 + 说话动词 + 引号
SPEAKER_RE = re.compile(
    r"(?P<name>[一-龥]{2,4})"
    r"(?:道|说道|问道|答道|冷笑道|笑道|开口道|沉声道|低声道|喝道|叹道)"
    r"\s*[：:]?\s*[「“\"]"
)

# 引语：分子的分母。中文小说的对白引号
QUOTE_RE = re.compile(r"[「“][^」”]{1,400}[」”]")


def sample(text: str, limit: int) -> list[tuple[str, str]]:
    """取前 `limit` 章的 (标题, 正文)。

    切章走 `text/chapterize.py`，**这里不留第二份 CHAPTER_RE**：探针量的覆盖率必须
    出自导入器实际切出来的那批章，否则这个数字填进 ADR 0005 时量的是另一个东西。
    """
    out = chapterize(text)
    if not out.chapters:
        return [("(全文，未识别到章节标记)", out.preamble)]
    return [(c.raw_heading, c.body) for c in out.chapters[:limit]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("novel", type=Path)
    ap.add_argument("--chapters", type=int, default=3, help="取前 N 章（ADR 0005 说 3 章）")
    args = ap.parse_args()

    raw = args.novel.read_bytes()
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            print(f"编码：{enc}")
            break
        except UnicodeDecodeError:
            continue
    else:
        print("无法解码，试试手动转成 UTF-8", file=sys.stderr)
        return 1

    chs = sample(text, args.chapters)
    print(f"取样：{len(chs)} 章\n")

    tot_q = tot_tagged = 0
    for title, body in chs:
        quotes = QUOTE_RE.findall(body)
        # 一个引语算「带标签」当且仅当它前面 12 个字内有显式说话人标签
        tagged = 0
        for m in QUOTE_RE.finditer(body):
            window = body[max(0, m.start() - 12) : m.start() + 1]
            if SPEAKER_RE.search(window):
                tagged += 1
        tot_q += len(quotes)
        tot_tagged += tagged
        pct = 100 * tagged / len(quotes) if quotes else 0.0
        print(f"  {title[:24]:26s} 引语 {len(quotes):4d}  带显式说话人标签 {tagged:4d}  ({pct:5.1f}%)")

    print()
    if not tot_q:
        print("没找到任何引语——检查引号样式或章节切分。")
        return 1

    cov = 100 * tot_tagged / tot_q
    print(f"合计：{tot_tagged} / {tot_q} = **{cov:.1f}%**")
    print()
    if cov >= 10:
        print("判定：>= 10% → **R5 进 v1**，但只在显式标签 + 受话人在场且唯一时开火。")
    else:
        print("判定：< 10% → **当场砍掉 R5**，M3 验收标准相应改写。")
    print("→ 把这个数字填进 docs/adr/0005-set-judgment-only.md 的「实测结果」一节。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
