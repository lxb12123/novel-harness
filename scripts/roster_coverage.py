#!/usr/bin/env python3
"""M1 角色册 90% 提及度量（docs/M1_ROSTER_METRIC.md）。

用法：
    uv run python scripts/roster_coverage.py \\
        --db book.db --project <pid> --text book.txt --gold gold.json

两条硬纪律：
1. **gold 与正文逐字核对**：任一 surface 的标注次数和正文实际次数对不上 → 拒绝出数
   （exit 2）——一份对不上的 gold 是假数据，拿它算出来的覆盖率也是假的。
2. **覆盖率按实例算**：能被 `resolve(rules_only=True)` 精确覆盖的实例 / 全部实例
   ≥ 90% → exit 0，否则 exit 1。没覆盖的称呼清单打印出来 = 作者该补的别名。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from novel_harness import db
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.text.chapterize import chapterize
from novel_harness.text.mentions import compile_alternation, find_mentions

MIN_COVERAGE = 0.90


@dataclass(frozen=True)
class ChapterCoverage:
    chapter: int
    total: int
    covered: int
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CoverageResult:
    total: int
    covered: int
    chapters: tuple[ChapterCoverage, ...]

    @property
    def ratio(self) -> float:
        return self.covered / self.total if self.total else 1.0

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.ratio >= MIN_COVERAGE


def _count_occurrences(text: str, surface: str) -> int:
    pattern = compile_alternation([surface])
    return len(find_mentions([text], pattern))


def measure(
    *,
    db_path: Path,
    project_id: str,
    text: Path,
    gold: Path,
) -> CoverageResult:
    """核对 gold → 算覆盖率。gold 与正文对不上就 raise（拒绝出数）。"""
    data = json.loads(gold.read_text(encoding="utf-8"))
    if data.get("project_id") != project_id:
        raise ValueError("gold 的 project_id 与 --project 不一致")

    cz = chapterize(text.read_text(encoding="utf-8"))
    body_by_chapter = {index: c.body for index, c in enumerate(cz.chapters, start=1)}
    store = SqliteStoryGraph(db.connect(db_path))

    for entry in data["chapters"]:
        chapter = int(entry["chapter"])
        body = body_by_chapter.get(chapter)
        if body is None:
            raise ValueError(f"gold 标了第 {chapter} 章，正文里没有这一章")
        for surface, count in entry["mentions"].items():
            actual = _count_occurrences(body, surface)
            if actual != count:
                raise ValueError(
                    f"第 {chapter} 章「{surface}」gold 标 {count} 次，"
                    f"正文实际 {actual} 次——gold 和正文对不上，拒绝出数"
                )

    rows: list[ChapterCoverage] = []
    total = covered = 0
    for entry in data["chapters"]:
        chapter = int(entry["chapter"])
        surfaces = [(s, int(c)) for s, c in entry["mentions"].items()]
        if not surfaces:
            continue
        # 不带 rules_only：契约保证与入参一一对应且同序，解析不到的也返回 hits=[]
        # （rules_only=True 会把它们整个滤掉，zip 就会悄悄错位）。
        resolutions = store.resolve(project_id, [s for s, _ in surfaces])
        covered_here = 0
        missing: list[str] = []
        for (surface, count), resolution in zip(surfaces, resolutions):
            total += count
            if resolution.usable_for_rules:
                covered_here += count
            else:
                missing.append(surface)
        covered += covered_here
        rows.append(
            ChapterCoverage(
                chapter=chapter,
                total=sum(c for _, c in surfaces),
                covered=covered_here,
                missing=tuple(missing),
            )
        )
    return CoverageResult(total=total, covered=covered, chapters=tuple(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite 库（角色册所在）")
    parser.add_argument("--project", required=True, help="project_id")
    parser.add_argument("--text", required=True, help="全书 TXT（切章用 chapterize）")
    parser.add_argument("--gold", required=True, help="gold 标注 JSON")
    args = parser.parse_args()

    try:
        result = measure(
            db_path=Path(args.db),
            project_id=args.project,
            text=Path(args.text),
            gold=Path(args.gold),
        )
    except ValueError as exc:
        print(f"✗ 拒绝出数：{exc}", file=sys.stderr)
        raise SystemExit(2)

    for row in result.chapters:
        mark = "✓" if row.total and row.covered / row.total >= MIN_COVERAGE else "✗"
        print(
            f"{mark} 第 {row.chapter} 章：{row.covered}/{row.total} "
            f"({row.covered / row.total:.0%})"
        )
        for surface in row.missing:
            print(f"   未覆盖：{surface} —— 角色册里没有，或指向多人")
    print(
        f"全书：{result.covered}/{result.total} = {result.ratio:.0%}"
        f"（门槛 ≥{MIN_COVERAGE:.0%}）"
    )
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
