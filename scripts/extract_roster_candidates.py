#!/usr/bin/env python3
"""从一本小说里抽「疑似角色名」候选（M1 角色册的草稿，零语义 ADR 0005）。

强信号 = 显式说话人标签位置（`X道：「…」` / `X说：“…”`）：名字紧贴说话动词、
动词后接引号，这是全书写得最不可能是普通词汇的位置。

输出按出现次数降序的候选清单。**它是给维护者看的草稿，不是自动建册**——
确认、改名、标 usable_for_rules 全人工做。

用法：
    uv run python scripts/extract_roster_candidates.py path/to/novel.txt
        [--chapters 158] [--top 40] [--out roster_candidates.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from novel_harness.text.chapterize import chapterize

# 与 scripts/probe_speaker_tags.py 的动词表同源；这里要求动词后**紧接引号**，
# 把「不知道」「知道」这类普通词挡在标签位置之外。
SPEAKER_RE = re.compile(
    r"([\u4e00-\u9fff]{2,4}?)"
    r"(?:道|说道|问道|答道|冷笑道|笑道|开口道|沉声道|低声道|喝道|叹道)"
    r"\s*[：:]?\s*[「“\"]"
)

# 常见非名字（说话动词前面恰好凑成 2-4 字的普通词）。不是完备表——
# 候选清单本来就要人看，这张表只挡最响的噪声。
STOPWORDS = {
    "不知",
    "知道",
    "难道",
    "觉得",
    "显得",
    "报道",
    "知道",
    "听说",
    "心道",
    "暗道",
    "想道",
    "应道",
    "答道",
    "叫道",
}

# 常见非名字尾字：`贾环颔首道` 会被切成本名「贾环颔首」+「道」——机械过滤
# 以这些字结尾的候选（仍是草稿，人还要过一遍）。
SUFFIX_BLACKLIST = "首着淡声手头然笑的脸礼话骂问道说"


def extract(text: str, *, limit: int | None = None) -> Counter[str]:
    """按章节顺序扫描说话人标签位置，返回 称呼 → 出现次数。"""
    chapters = chapterize(text).chapters
    if limit is not None:
        chapters = chapters[:limit]
    counter: Counter[str] = Counter()
    for chapter in chapters:
        for match in SPEAKER_RE.finditer(chapter.body):
            name = match.group(1)
            if name in STOPWORDS:
                continue
            if name[-1] in SUFFIX_BLACKLIST:
                continue
            counter[name] += 1
    return counter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--chapters", type=int, default=None, help="只扫前 N 章")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None, help="候选 JSON 落盘路径")
    args = parser.parse_args()

    text = args.path.read_text(encoding="utf-8-sig")
    counter = extract(text, limit=args.chapters)
    rows = counter.most_common(args.top)
    for rank, (name, count) in enumerate(rows, 1):
        print(f"{rank:3d}  {name}  ×{count}")
    print(f"\n共 {len(counter)} 个不同称呼；前 {len(rows)} 个如上。")

    if args.out is not None:
        payload = {
            "source": str(args.path),
            "chapters_scanned": args.chapters or "all",
            "candidates": [{"surface": name, "count": count} for name, count in rows],
        }
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"候选清单已写：{args.out}")


if __name__ == "__main__":
    main()
