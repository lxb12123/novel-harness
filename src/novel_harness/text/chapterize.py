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

── 英文章标：国际化第一批 ①（2026-08-26）加的 ─────────────────────────────

同一条正则里追加两个英文分支，跟中文那支共用 `group(1)`=marker / `group(2)`=title、
共用「marker 不解析成数字、index 只看文本顺序」这条纪律（上一节）。认得：
`Chapter 1` / `Chapter One` / `CHAPTER XII` / `Ch. 12`。`PART TWO` 这类
**卷/部级**标题故意不认，跟中文的「卷」同一个方向——本模块没有一个「PART」分支，
不是漏写。

三条设计取舍，任何一条改动前先想一遍为什么改：

1. **大小写不敏感靠逐字母字符类 `[Cc][Hh][Aa]...`，不靠 `re.IGNORECASE` 或
   Python 专有的 `(?i:...)` 局部标志。** 两个都不能用，且理由不同：全局标志会
   波及中文那半（本模块的正则里数字字符类没有大小写，波及了也测不出来，但下一个
   加中文以外分支的人未必这么幸运，别开这个先例）；`(?i:...)` 是 Python `re` 的
   扩展语法，`frontend/src/chapterTitle.ts` 里那份**逐字节**副本是标准 JS 正则，
   没有局部标志这回事——写了 JS 那边编译不出同一个模式，两份就没法再保持同步。
2. **拼出来的数字词（`one`..`ninety-nine`）只有首字母大小写不敏感**（`[Oo]ne`，
   不是逐字母的 `[Oo][Nn][Ee]`）。覆盖 Title Case（`One`）和全小写（`one`），
   覆盖不到全大写拼词（`ONE`）——这是故意收窄的：真书里全大写的章标配的是数字
   或罗马数字（`CHAPTER 1` / `CHAPTER XII`），没见过全大写拼词章号；真要出现，
   退化成整行落进上一章 body，跟卷标题同一个下场，不是新增一类误切。不含
   `hundred` 及以上——拼出「Chapter One Hundred」的书远少于直接用数字/罗马数字的。
3. **数字/罗马数字/拼词那一支后面跟一个 `(?![A-Za-z])`**（不许紧跟字母）。
   没有这一条，`Chapter Mild` 会被切成 marker=`Chapter M`（M 是合法罗马数字）+
   title=`ild`——罗马数字字符集只有 7 个字母，`mild`/`civil`/`onerous` 这类
   常见英文词全用得上；拼词分支同理（`Onerous` 会被 `one` 吃掉前三个字母）。
   这条守卫**只挡「数字后面立刻接字母」**，不挡 `Chapter 1: The Beginning`
   这种数字后面接标点或空白的正常标题。
4. **罗马数字只认大写 `[IVXLCDM]+`，不认小写。** 放开小写等于把上一条的守卫
   削弱一半——大写单词在正常英文叙事里本来就罕见，这个限制换来的是
   「误切概率趋近于 0」，代价是不认 `chapter xii` 这种全小写罗马数字章标
   （目前没见过这种真书用法）。
5. **裸数字章标（`1.`）——想过，最后没做，两次真书实测都指向同一个方向。**
   第一版实现（`1.` 后面允许跟标题，跟 `Chapter N` 同一个宽松度）在
   `tests/fixtures/demo_novel.txt` 上就翻了车：前言里一句大白话的编号列表
   `1. **不是 M0 Day 3 的真书验收。** ……` 被切成了一章。收窄到「只认独占一行、
   不带标题」那一种（`(?=[ \t　]*$)`）看似顶住了这一条，但拿 *Moby-Dick*
   （Gutenberg #2701）一试，**又炸出一种完全不同的碰撞**：`Extracts` 那节引文
   `—Richard Strafford's Letter from the Bermudas. Phil. Trans. A.D.\n1668.`
   纯粹是硬折行——`1668.` 独占一行只是排版折到那儿，跟编号列表毫无关系，
   却照样满足「独占一行、不带标题」。**这不是同一个漏洞的两个症状，是两条
   独立的碰撞路径**：一条来自内容结构（列表），一条来自排版换行——后者
   连「要求空行前后夹住」这种更强的收窄都未必挡得住（`1668.` 前面那行本身
   就不是空行，是上一句折过来的）。裸数字加句点在英文平摊文本里出现的
   密度太高，两次不同角度的真书验证都命中，判定这个形状**没有纯语法收窄能
   兜住**（ADR 0005 不许上语义判断），所以 `Chapter` / `Ch.` 两支照留（强前缀，
   正常文本里不会意外出现），**`1.` 这一支干脆不做**。真要支持它，需要更强的
   信号（比如整本书统一的编号风格是不是自洽），那是状态判断，不是单行正则
   能答的问题，留给以后有专门设计时再做。

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

⚠️ **中文那半没有在任何一本真实网文上跑过**（M0 剩余项，`docs/ARCHITECTURE.md` 里
记着「需要一本中文小说 TXT」）。中文的行为全部是手写 fixture 上的行为，**不是覆盖率断言**，
真书跑通之前，别把「章数 = 目录数」当成中文那半已验收。

英文那两个分支 2026-08-26 用两本 Project Gutenberg 公版书验证过一次（章数 / 目录数 /
有没有误切成章，数字和过程在那笔提交的 commit message 里；两本书本身没有进 `tests/
fixtures/`——770KB/1.2MB 的整本文学作品不值得常驻仓库，命中的两个真实碰撞已经收窄成
`tests/test_chapterize.py` 里的手写 fixture 用例，长期回归靠那些）。*Pride and
Prejudice*（61 章，零误差）干净通过；*Moby-Dick*（135 章）暴露了一个**已知、
跨语言的架构缺口**——
书里内嵌的 `CONTENTS` 目录页用了和正文里一模一样的 `CHAPTER N. Title` 格式，
于是每一章被数了两次（目录一次、正文一次）。这不是英文分支的 bug，换成中文小说
只要「目录页」抄的是和正文相同的「第N章」格式，同样的坑就在那儿——本仓库还没有
遇到过这种中文 TXT，所以这条缝目前只在英文这边被踩到。**这个模块目前不区分
「目录页」和「正文标题」**，修它需要一种新的、跨越单行的结构判据（比如「连续命中
之间几乎没有正文」），那是下一个任务，不是这一批的范围。
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

_EN_ONES: Final = r"[Oo]ne|[Tt]wo|[Tt]hree|[Ff]our|[Ff]ive|[Ss]ix|[Ss]even|[Ee]ight|[Nn]ine"
_EN_TEENS: Final = (
    r"[Tt]en|[Ee]leven|[Tt]welve|[Tt]hirteen|[Ff]ourteen|"
    r"[Ff]ifteen|[Ss]ixteen|[Ss]eventeen|[Ee]ighteen|[Nn]ineteen"
)
_EN_TENS: Final = r"[Tt]wenty|[Tt]hirty|[Ff]orty|[Ff]ifty|[Ss]ixty|[Ss]eventy|[Ee]ighty|[Nn]inety"
_EN_NUMBER_WORD: Final = (
    rf"(?:(?:{_EN_TENS})(?:[ \t　-](?:{_EN_ONES}))?|{_EN_TEENS}|{_EN_ONES})"
)
"""英文章标里拼出来的数字：`one`..`ninety-nine`。只搭到 99——见模块 docstring
「英文章标」一节第 2 条。"""

_EN_CHAPTER_NUMBER: Final = (
    rf"(?:[0-9]+|[IVXLCDM]+|{_EN_NUMBER_WORD})(?![A-Za-z])"
)
"""`Chapter` 后面那个数字：阿拉伯数字 / 大写罗马数字 / 拼出来的数字词，
三选一，选完不许紧跟字母——见模块 docstring「英文章标」一节第 3 条。"""

CHAPTER_RE: Final = re.compile(
    r"^[ \t　]*("
    r"第[ \t　]*[0-9〇零一二三四五六七八九十百千两]+[ \t　]*[章节回]"
    rf"|[Cc][Hh][Aa][Pp][Tt][Ee][Rr][ \t　]*{_EN_CHAPTER_NUMBER}"
    rf"|[Cc][Hh]\.[ \t　]*[0-9]+(?![A-Za-z])"
    r")[ \t　]*(.*?)[ \t　]*$",
    re.M,
)
"""中文分支是 PLAN §8 Day 3 逐字抄来的那一个，英文两支是国际化第一批 ① 加的
（模块 docstring「英文章标」一节）。**改它之前先让 `tests/test_chapterize.py` 红。**

`group(1)` = 章标（`第一百零八章` / `Chapter One` / `CHAPTER XII` / `Ch. 12`），
`group(2)` = 标题（可为空）。**`frontend/src/chapterTitle.ts` 里有逐字节副本**
（多括了一组「marker 和 title 之间那截空白」），改这条必须同笔改那边，
`tests/test_chapterize.py::test_frontend_marker_regex_is_the_same_one` 钉着这条缝。
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
