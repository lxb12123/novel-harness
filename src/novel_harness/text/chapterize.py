r"""切章 —— 全书全序键的产地（PLAN §8 Day 3）。

M0 的验收里有一条是「真书切出的章数 = 目录数」。这个文件是那条验收的被测物，
但它真正的产物不是「章数」而是 **`Chapter.index`——全书 1-based 顺序位置**，
也就是 `chapter.number`、也就是 `state_at` 的 `valid_from_chapter <= :ch` 里的那个 `:ch`。
切错一章，往后每一章的 index 全体偏移 1，`state_at` 从此答的是隔壁那一章的状态。

── 正则：用 PLAN §8 钉死的那一个，别用直觉写的那个 ────────────────────────

`[ \t　]` 而不是 `[\s　]`。**实测的 bug**：`\s` 含 `\n`，于是「第一百零八章」（无标题、
独占一行）会把正文首行整个吞成标题。全角空格 `　` 必须在字符类里——中文 TXT 里到处都是。
这条由 `tests/test_chapterize.py` 钉住。

正则是**唯一真相**：`scripts/probe_speaker_tags.py` 从这里 import，不留第二份副本。
一份正则抄两处 = 探针量出来的覆盖率和导入器实际切出来的章不是同一批章。

── index 的来源是文本顺序，不是标题里印的那个中文数字 ──────────────────────

**这是个刻意的设计，不是偷懒没做数字解析。** `marker` 里的「第一百零八章」只是**显示用**，
`index` 由 `enumerate` 给。理由是印出来的章号在真书里**不是全序的**：

- 分卷重启：「第二卷」之后又来一次「第一章」→ 两章同号
- 番外、加更章：号会重、会跳、会缺

而 `chapter` 表上有 `UNIQUE(project_id, number)`，`state_at` 要求 number 是全序键。
拿印出来的章号当 number，两章同号 ⇒ 全序断裂 ⇒ `state_at` 同时返回两条互斥边 ⇒
规则误报 ⇒ M3 生死线崩。ordinal 由构造保证唯一，永远撞不上那条 UNIQUE。

**所以 `marker` 不许被解析成数字。** 想知道「第几章」的人要的是 `index`。

── 脏数据：卷标题 / 番外 / 作者的话 ──────────────────────────────────────

PLAN §8 Day 3 为这三样预留了半天。本模块的处置是**一句话能说完的那种**：

> 只有行首的 `第N[章节回]` 是章界。别的一律不是，于是它们落进**上一章的 body**
> （或第一章之前的 `preamble`）。

`第一卷 风起青云` 不匹配（`卷` 不在 `[章节回]` 里）、`番外：…` 和 `作者的话：…` 不匹配
（没有 `第`）、`番外 第一章 少年萧决` 也不匹配（行首只容得下空白，容不下 `番外`）。
它们不会被误切成章，也不会被丢掉——落在 body 里，最坏结果是那一章多几行不是正文的字。
这个方向是选过的：**多切一章会让全书 index 集体偏移（灾难），body 里多几行只是脏（可容忍）**。

反过来，番外若自己占一行写成 `第一章`，它就**是**一章、拿一个 ordinal。本模块认的是
字面形状，不认「这一章是不是番外」——那是语义判断（ADR 0005 划在 v1 之外）。

⚠️ **这个模块没有在任何一本真实网文上跑过**（M0 剩余项，`docs/ARCHITECTURE.md` 里
记着「需要一本中文小说 TXT」）。下面的行为全部是手写 fixture 上的行为，**不是覆盖率断言**。
真书跑通之前，别把「章数 = 目录数」当成已验收。
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

CHAPTER_RE: Final = re.compile(
    r"^[ \t　]*(第[ \t　]*[0-9〇零一二三四五六七八九十百千两]+[ \t　]*[章节回])[ \t　]*(.*?)[ \t　]*$",
    re.M,
)
"""PLAN §8 Day 3 逐字抄来的那一个。**改它之前先让 `tests/test_chapterize.py` 红。**

`group(1)` = 章标（`第一百零八章`），`group(2)` = 标题（可为空）。
"""

_BOM: Final = "﻿"


def normalize(text: str) -> str:
    """喂给 `CHAPTER_RE` 之前必须先过这里。**两条都是实测出来的，且都只有一个字符宽。**

    1. **CRLF**：`re.M` 的 `$` 只认 `\\n`，而 `.` 吃得下 `\\r`。于是 Windows TXT 里
       `第一章\\r\\n` 的 `group(2)` 是 `'\\r'` 而不是 `''`——一个无标题的章凭空长出标题。
       这跟 `\\s` 吞行是同一类 bug 的同一个受害者（`group(2)`），只是换了个字符。
    2. **BOM**：`^[ \t　]*` 后面接的是 `第`，而 `\\ufeff` 既不是行首空白也不是 `第`。
       于是**当第一个章标就是文件首行时**（无前言的 TXT，常见形态）它匹配不上，
       整章落进 preamble，往后每一章 index 少 1。表现形式恰好是「切出来的章数 =
       目录数 - 1」——看着像差一点点，实则每一章都错位。
       （有前言的书里 BOM 挨着的是书名行，这条不发火——**实测过才这么写**：
       先前这里断言的是「全书第一章匹配不上」，在带前言的 fixture 上是假的。）

    顺带一提 `utf-8-sig`：探针按 `utf-8` 解码，BOM 会**原样留在字符串里**且不报错，
    所以这两条在真书上是一起发生的，不是二选一。

    正则本身是 PLAN 钉死的，修不得（也不该修：正则不是清洗器）。所以清洗在这里。
    """
    return text.lstrip(_BOM).replace("\r\n", "\n").replace("\r", "\n")


class Chapter(BaseModel):
    """切出来的一章。**这是纯文本层的出参，跟库里的 `chapter` 表不是一回事**——
    `path` / `text_sha256` / `id` 由导入器（装配层）填，本模块不知道库的存在。
    """

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    """全书 1-based 顺序位置 → `chapter.number`。**由文本顺序决定，不由 `marker` 决定**
    （见模块 docstring）。作者永不填它，标题里印的数字也不决定它。"""

    title: str = ""
    """`group(2)`。无标题的章（真书里很常见）是 `''`，不是 None——`chapter.title`
    是 `NOT NULL DEFAULT ''`，两边同一个形状。"""

    marker: str
    """`group(1)`，如 `第一百零八章`。**显示用。不许解析成数字**（见模块 docstring）。"""

    raw_heading: str
    """整行标题行的原样（去掉行内首尾空白）。给导入器跟目录对账时打印用——
    「章数 != 目录数」时人要看的是这个，不是 index。"""

    body: str
    """标题行之后、下一个章标之前的全部文本。首尾空行已去掉。"""


class Chapterization(BaseModel):
    """`chapterize()` 的出参。

    `preamble` 单独出参而不是丢掉：真书的第一个章标之前有书名、简介、免责声明、
    以及**第一个卷标题**。丢掉它就是静默丢数据；把它塞进第 1 章的 body 则是撒谎。
    """

    model_config = ConfigDict(frozen=True)

    preamble: str = ""
    """第一个章标之前的东西。没有章标时**全文在这里**，`chapters` 为空。"""

    chapters: list[Chapter] = Field(default_factory=list)


def chapterize(text: str) -> Chapterization:
    """把一整本书切成章。纯函数，不碰磁盘也不碰库。

    没找到任何章标时返回 `chapters=[]` + `preamble=全文`——**不是**「整本书算一章」。
    调用方必须自己决定怎么办（对导入器来说这是个要报错的情形：一本书零章）。
    """
    text = normalize(text)
    marks = list(CHAPTER_RE.finditer(text))
    if not marks:
        return Chapterization(preamble=text)

    chapters_: list[Chapter] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chapters_.append(
            Chapter(
                index=i + 1,
                title=m.group(2),
                marker=m.group(1),
                raw_heading=m.group(0).strip(),
                # body 掐头去尾只去空行：留着的话 text/anchor.py 分段时会多出一个空段，
                # 而 para_index 是证据锚的第一个分量（ADR 0006）——偏 1 就全偏。
                body=text[m.end() : end].strip("\n"),
            )
        )
    return Chapterization(preamble=text[: marks[0].start()], chapters=chapters_)


def chapters(text: str) -> list[Chapter]:
    """只要章的便捷入口。**会静默丢掉 preamble**——导入器请用 `chapterize()`。"""
    return chapterize(text).chapters
