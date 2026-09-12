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


# ── 语域守卫（2026-09-12）───────────────────────────────────────────────────
#
# 作者指着回执上那句「这段对话太长了，为了装得下，15 条早先查到的东西被收起来了
# （要用它会重新查一次）。你说过的话一句都没删。」问：「这么人机的那种话放到开源社区，
# 大家是怎么想我的？」——并要求「不能仅仅针对这句，我不希望再出现类似这种」。
# 那一轮把全仓作者可见的话（回执、停法、进度行、错误、空态、按钮）从「拟人聊天」改成
# 书面产品文案。这张表钉的是**那种口气的标记词**，不是词表式的全面审查：
#   · 口语时间/动作词（刷新一下 / 过一会儿 / 这会儿 / 看一眼 / 试试）；
#   · 聊天口气（算了 / 知道了 / 就是了 / 一句都没 / 装得下 / 说一声 / 跟它说……）；
#   · 拿「它」讲故事（它会 / 它还 / 它自己 / 它正在 / 它在等……）——助手在屏幕上叫
#     「写作助手」，不是「它」；
#   · 句末语气词（吧 / 呢 / 哦 / 啦 / 嘛）；
#   · 英文那半同一种口气（couldn't say why / in a moment / Just write / Got it……）。
# 扫描面比上面那张 `BANNED_COPY` 宽一档：`.ts` 也扫（`chat.ts` / `backendMessages.ts`
# 才是这类话最多的地方）。后端说给作者的那几张表由 `tests/test_wording_guard.py`
# 拿**同一张**表扫，别在那边再抄一份。
CHATTY_COPY: dict[str, str] = {
    r"刷新一下|过一会儿|这会儿|等一下|看一眼|试试|再点一次|再试一次": "colloquial time / retry phrasing",
    (
        r"(?<![计估])算了|知道了[。」\"]|就是了|一句都没|装得下|装不下|说一声|跟它说|拿不准|"
        r"等着你|往下走|断在半路|先停下来|今天不在了|没读出来|读不出来|而系统没能说清"
    ): "chat-register phrasing",
    r"它会|它还|它自己|它正在|它在等|它这一轮|它手上|它读到|它下一步|问了你一句": "narrating the assistant as 它",
    r"[吧呢哦啦嘛][。！」\"]": "sentence-final particle",
    (
        r"couldn['’]t say why|in a moment|Just write|Got it|tucked away|Not a single word|"
        r"it['’]ll |it['’]s waiting|[Tt]ake a look"
    ): "chatty English",
}


def _production_sources() -> list[Path]:
    """作者会看到的那些文件：`.ts` + `.tsx`，测试、夹具、测试脚手架不算。"""
    out: list[Path] = []
    for path in sorted(COMPONENTS.rglob("*.ts*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        if "__fixtures__" in path.parts or "test" in path.parts:
            continue
        out.append(path)
    return out


def _without_comments(source: str) -> str:
    """Remove TS/TSX comments while preserving quoted strings and JSX text."""

    return _scan(source)[0]


def _scan(source: str) -> tuple[str, str | None]:
    """Same walk as `_without_comments`, but also returns the quote state at EOF.

    **This state machine doesn't know JSX from a string literal.** It tracks one
    active quote character at a time and closes on the next matching one — which
    is exactly right for `"..."`, `'...'`, and `` `...` `` in real code, but a bare
    apostrophe in plain JSX text (`<>it's fine</>`, not inside any quotes) reads to
    it as *opening* a `'` string. One unpaired apostrophe like that — "it's" with
    no later apostrophe to close it — leaves `quote` set for the rest of the file,
    and every `//`/`/* */` comment after that point is silently **not** stripped:
    it's treated as string content instead. `test_production_tsx_has_no_internal_or_fixture_copy`
    only catches this if a banned word happens to sit in one of those swallowed
    comments — it did, once (`SummaryTab.tsx`, an `ADR 0004` reference in a comment
    that stopped being recognized as a comment three paragraphs of English prose
    earlier). A quieter file with the same defect would pass that test by luck
    while still hiding whatever comes after it from every pattern in `BANNED_COPY`.
    **A clean end-of-file quote state is the real invariant**; this exposes it.
    """

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
    return "".join(out), quote


_SVG_PATH_DATA = re.compile(r'\bd="[^"]*"')

_QUOTED = re.compile(r'"([^"\\\n]*)"')
_PATH_ONLY = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9\s,.\-eE]+$")


def _without_path_data(source: str) -> str:
    """去掉 SVG 的几何。**它是几何，不是文案**，而这道守卫查的是文案。

    真出现过的误报（2026-08-13）：齿轮图标的路径以 `M10.48` 起笔，被
    `\\bM\\d+\\b`（内部里程碑 M0–M4）当场抓成「界面泄漏了里程碑编号」。

    收窄那条里程碑正则也能让它绿，但**那是把守卫削薄去迁就一个不是文案的东西**——
    真正该做的是别让它看几何。路径数据里藏不住泄漏：它一个字都不会渲染成文字。

    ⚠️ **2026-09-07：几何不一定住在 `d="…"` 里。** 翅膀图标（`WingIcon`）的四条路径
    存成一个字符串数组再 `map` 出来，于是那一版只认属性的过滤看不见它们，
    `"M330.5 613 C…"` 又一次被当成里程碑 `M330` 抓住——**同一个误报，第二个藏身处**。
    所以这儿改成两道：属性一道，**整串都是路径语法的字符串字面量**一道。
    判据故意收得很紧（首字符是 `M`/`m`、其余只有路径命令字母和数字标点、且够长），
    任何一句真文案——中文也好、带别的标点的英文也好——都过不了它。
    """
    source = _SVG_PATH_DATA.sub('d=""', source)

    def blank(match: re.Match[str]) -> str:
        body = match.group(1)
        if body[:1] in {"M", "m"} and len(body) > 8 and _PATH_ONLY.match(body):
            return '""'
        return match.group(0)

    return _QUOTED.sub(blank, source)


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


def test_the_comment_stripper_never_ends_the_file_still_inside_a_quote() -> None:
    """`_scan()` 是逐字符的状态机，不是真解析器——它靠"下一个同类引号来了就收口"
    认引号，不认 JSX 语义。**一个裸在 JSX 文本里、不在任何引号内的英文撇号**
    （`<>it's fine</>`，不是 `"it's fine"`）会被它当成"开了一个 `'` 字符串"，
    而如果这句话里没有第二个撇号去闭合它，`quote` 就一路带到文件结尾——
    从那一撇往后，**每一条 `//`/`/* */` 注释都不再被识别成注释**，被当成字符串
    内容原样保留。

    **这不是假设**：国际化第四批·前端文案批次里真的踩过一次——`SummaryTab.tsx`
    一句英文（"...chapter's own text..."）的撇号没有闭合，三段话之后一条提著
    `ADR 0004` 的注释就这样漏了出来，被
    `test_production_tsx_has_no_internal_or_fixture_copy` 抓到。**抓到是运气**：
    那条测试只在漏出来的注释恰好撞上 `BANNED_COPY` 里的某个词时才会红——
    同一个缺陷落在一段没有敏感词的注释上，会安静地让那条测试对着之后**整个文件**
    失明，而没有任何红色提醒这件事发生了。

    真正的不变量不是"没漏敏感词"，是"扫到文件末尾时引号状态该合上"——这条测试钉的
    是这件事本身，不等一个敏感词凑巧掉进坑里才发现坑在那儿。

    **修法**：英文散文里的撇号（`it's` / `chapter's` / `you'll`）改用印刷体右单引号
    `'`（U+2019）而不是直引号 `'`——它不是这三个引号字符里的任何一个，这台状态机
    根本不会把它当成引号，顺带也是英文排版本该用的那个字符。真的需要一对单引号
    做字符串分隔符（比如 `.join('", "')`）时，直引号 `'` 照旧用，那种用法从来
    不会落在"裸 JSX 文本"里，不受这条影响。
    """
    offenders: list[str] = []
    for path in sorted(COMPONENTS.rglob("*.tsx")):
        if path.name.endswith(".test.tsx"):
            continue
        _, quote = _scan(path.read_text(encoding="utf-8"))
        if quote:
            offenders.append(f"{path.relative_to(ROOT)}: 扫到文件末尾时还困在 {quote!r} 里")

    assert not offenders, (
        "注释剥离器的引号状态在文件末尾没有归零，说明中途有个没闭合的引号——"
        "从那一点往后，这个文件里的注释可能没有被真的剥掉，"
        "BANNED_COPY 那条守卫可能在这些文件上失明：\n" + "\n".join(offenders)
    )


def test_production_copy_is_written_not_chatty() -> None:
    """作者可见的话是**产品文案**，不是聊天（CLAUDE.md「界面上的字：书面语，不是聊天」）。"""
    offenders: list[str] = []
    for path in _production_sources():
        source = _without_path_data(_without_comments(path.read_text(encoding="utf-8")))
        for pattern, label in CHATTY_COPY.items():
            for match in re.finditer(pattern, source):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {label}: {match.group(0)!r}")

    assert not offenders, (
        "界面文案带着聊天口气（书面语、不拟人、不用口语时间词；见 CLAUDE.md「界面上的字」）：\n"
        + "\n".join(offenders)
    )

