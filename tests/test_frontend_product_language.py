"""Guard the production workbench against leaking prototype and evaluation language."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
COMPONENTS = ROOT / "frontend" / "src"

BANNED_COPY: dict[str, str] = {
    r"\bR[1-4]\b": "evaluation rule number",
    r"\bM\d+\b": "internal milestone",
    r"ADR\s*0*\d+": "architecture record number",
    r"valid_from\s*=": "database field",
    r"must_not_reveal（": "API field",
    r"kill-gate|修正案|实验状态|退化值|fail-closed|decision_log": "engineering language",
    r"X[0-2]\s*·": "experiment arm",
    r"低置信|跑第.{0,20}抽取|抽取：|被动事件|时态区间|场景序": "analysis-pipeline language",
    r"萧决|顾清音|李管家|苏挽|北荒|血脉秘密": "fixture novel content",
    r"用这句声明|快照按内容去重|未声明|状态维": "implementation-shaped Chinese copy",
}


def _without_comments(source: str) -> str:
    """Remove TS/TSX comments while preserving quoted strings and JSX text."""

    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if quote:
            out.append(char)
            if char == "\\" and i + 1 < len(source):
                i += 1
                out.append(source[i])
            elif char == quote:
                quote = None
            i += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "/" and nxt == "/":
            i += 2
            while i < len(source) and source[i] != "\n":
                i += 1
            out.append("\n")
            i += 1
            continue
        if char == "/" and nxt == "*":
            i += 2
            while i + 1 < len(source) and source[i : i + 2] != "*/":
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


_SVG_PATH_DATA = re.compile(r'\bd="[^"]*"')


def _without_path_data(source: str) -> str:
    """去掉 SVG 的 `d="…"`。**它是几何，不是文案**，而这道守卫查的是文案。

    真出现过的误报（2026-08-13）：齿轮图标的路径以 `M10.48` 起笔，被
    `\\bM\\d+\\b`（内部里程碑 M0–M4）当场抓成「界面泄漏了里程碑编号」。

    收窄那条里程碑正则也能让它绿，但**那是把守卫削薄去迁就一个不是文案的东西**——
    真正该做的是别让它看几何。路径数据里藏不住泄漏：它一个字都不会渲染成文字。
    """
    return _SVG_PATH_DATA.sub('d=""', source)


def test_production_tsx_has_no_internal_or_fixture_copy() -> None:
    offenders: list[str] = []
    for path in sorted(COMPONENTS.rglob("*.tsx")):
        if path.name.endswith(".test.tsx"):
            continue
        source = _without_path_data(_without_comments(path.read_text(encoding="utf-8")))
        for pattern, label in BANNED_COPY.items():
            if match := re.search(pattern, source):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {label}: {match.group(0)!r}")

    assert not offenders, "用户界面泄漏了内部或演示语言：\n" + "\n".join(offenders)
