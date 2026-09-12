"""改一段（ADR 0049）：**找到那一段、把新文字拼回去**——这一层只做这两件纯事。

助手要改哪儿，用的是**那一段的原文**（引语），不是字符位置（ADR 0006：锚是引语，
禁 offset——位置只在这一次拼接的瞬间算出来用一下，不出这个模块、不存）。找不到 / 找到多处
一律拒，**不替它挑**（§5.9：挑错的产物是改错了地方，而它在屏幕上长得完全正常）。

三种改法（`PassageEdit.kind`）：

| kind | 写手写什么 | 拼回去 |
|---|---|---|
| `replace` | 替换那一段的新文字 | 原处换掉 |
| `insert_after` | 接在那一段后面的新文字 | 那一段所在行的末尾之后，另起一段 |
| `delete` | 不写（不花钱） | 拿掉；整行删掉的把多出来的空行收掉 |

`quote` 定起点，`until` 定终点（可省：那一段就是 `quote` 本身）。两个都得在正文里**恰好出现
一次**，`until` 得在 `quote` 后面。一次几处改动同时定位、互不重叠，全定位完才动手拼。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PassageKind = Literal["replace", "insert_after", "delete"]


class PassageEdit(BaseModel):
    """一处改动：改哪儿（原文引语）、怎么改、对写手说什么。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quote: str = Field(
        min_length=1,
        description=(
            "要改的那一段的原文，从正文里原样引，一个字都别改：一句话或一段的开头。"
            "必须在本章正文里恰好出现一次——找不到或出现多处都会被拒，那时引长一点。"
        ),
    )
    until: str | None = Field(
        default=None,
        description=(
            "要改的范围到哪儿为止：范围末尾那一句的原文（原样引）。不给 = 只改 quote 那一段本身。"
            "给了就是从 quote 开头到 until 结尾这一整块。"
        ),
    )
    kind: PassageKind = Field(
        default="replace",
        description=(
            "replace = 把这一段换成写手写的新文字；insert_after = 这一段不动，在它后面加一段"
            "写手写的新文字；delete = 把这一段拿掉（不用写手，brief 可以不给）。"
        ),
    )
    brief: str = Field(
        default="",
        description=(
            "这一处怎么改、要守什么——用你自己的话对着写手说（改软一点 / 换成他的视角 / "
            "把那句台词去掉……）。replace 和 insert_after 必须给；delete 不用。"
        ),
    )


@dataclass(frozen=True)
class Span:
    """正文里的一块 `[start, end)`。**只活在这一次拼接里**，不出模块、不存。"""

    start: int
    end: int
    text: str


class PassageNotFound(ValueError):
    """`quote` / `until` 在正文里找不到。"""

    def __init__(self, quote: str) -> None:
        super().__init__(quote)
        self.quote = quote


class PassageAmbiguous(ValueError):
    """`quote` / `until` 在正文里出现了不止一次。"""

    def __init__(self, quote: str, count: int) -> None:
        super().__init__(quote)
        self.quote = quote
        self.count = count


class PassageOutOfOrder(ValueError):
    """`until` 在 `quote` 前面（或和它重叠）。"""

    def __init__(self, quote: str, until: str) -> None:
        super().__init__(quote)
        self.quote = quote
        self.until = until


class PassagesOverlap(ValueError):
    """一次交来的几处改动互相压着。"""

    def __init__(self, first: str, second: str) -> None:
        super().__init__(first)
        self.first = first
        self.second = second


def _find_once(text: str, quote: str) -> int:
    quote = quote.strip()
    if not quote:
        raise PassageNotFound(quote)
    count = text.count(quote)
    if count == 0:
        raise PassageNotFound(quote)
    if count > 1:
        raise PassageAmbiguous(quote, count)
    return text.index(quote)


def locate_span(text: str, quote: str, until: str | None = None) -> Span:
    """在正文里找到要改的那一块。**恰好一次才算找到**，否则抛（调用方翻成给助手的话）。"""
    start = _find_once(text, quote)
    end = start + len(quote.strip())
    if until is not None and until.strip():
        tail = _find_once(text, until)
        tail_end = tail + len(until.strip())
        if tail < end:
            raise PassageOutOfOrder(quote, until)
        end = tail_end
    return Span(start=start, end=end, text=text[start:end])


def locate_all(text: str, edits: list[PassageEdit] | tuple[PassageEdit, ...]) -> list[Span]:
    """每一处都定位到了、而且互不重叠，才返回；任何一处有问题整批都不动。"""
    spans = [locate_span(text, edit.quote, edit.until) for edit in edits]
    ordered = sorted(range(len(spans)), key=lambda i: spans[i].start)
    for a, b in zip(ordered, ordered[1:], strict=False):
        if spans[b].start < spans[a].end:
            raise PassagesOverlap(edits[a].quote, edits[b].quote)
    return spans


def _paragraph_separator(text: str) -> str:
    """这一章段与段之间是空一行还是直接换行——照它原来的样子插新段。"""
    return "\n\n" if "\n\n" in text else "\n"


def splice(
    text: str,
    edits: list[PassageEdit] | tuple[PassageEdit, ...],
    spans: list[Span],
    replacements: list[str],
) -> str:
    """把每一处的新文字拼回正文。**从后往前**拼，前面的位置才不会因为后面的改动漂掉。
    正文原来以换行收尾的，拼完仍以换行收尾（删掉最后一段不该顺手把文件末尾的换行吃掉）。"""
    if not (len(edits) == len(spans) == len(replacements)):
        raise ValueError("edits / spans / replacements 三份长度对不上")
    out = text
    order = sorted(range(len(spans)), key=lambda i: spans[i].start, reverse=True)
    sep = _paragraph_separator(text)
    for i in order:
        edit, span, new = edits[i], spans[i], replacements[i].strip()
        if edit.kind == "replace":
            out = out[: span.start] + new + out[span.end :]
        elif edit.kind == "insert_after":
            # 接在那一段**所在行**的末尾之后，另起一段——引语是句子中间一截时也不把一段拦腰切开。
            line_end = out.find("\n", span.end)
            at = len(out) if line_end < 0 else line_end
            out = out[:at] + sep + new + out[at:]
        else:
            left, right = out[: span.start], out[span.end :]
            whole_lines = (left == "" or left.endswith("\n")) and (right == "" or right.startswith("\n"))
            if whole_lines:
                # 整行拿掉：把它两头的换行收成一个段间隔，别留一个空洞。
                left, right = left.rstrip("\n"), right.lstrip("\n")
                out = left + (sep if left and right else "") + right
            else:
                out = left + right
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out
