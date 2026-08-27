"""切章的回归测试。

前三条（`\\s` 吞行 / 全角空格 / 正文提及）钉死的是 PLAN §8 记录的**实测 bug**，
它们比这个模块本身老——**不许在没让它们红一次的前提下改 `CHAPTER_RE`**。

下半部分是脏数据（卷标题 / 番外 / 作者的话）与 index 全序的行为。
⚠️ **这里没有一本真实网文**（M0 剩余项）。所以全是手写 fixture，
**不许从这些测试推出任何覆盖率结论**——「章数 = 目录数」还没被验收过。
"""

from __future__ import annotations

import re
from pathlib import Path

from novel_harness.text.chapterize import (
    CHAPTER_RE,
    Chapterization,
    chapterize,
    chapters,
    drop_toc_duplicates,
)

# ══════════════════════════════════════════════════════════════════════════
# 正则本身：PLAN §8 记录的三个实测 bug
# ══════════════════════════════════════════════════════════════════════════


def test_does_not_swallow_next_line() -> None:
    """若用 [\\s　]* 作行内空白，\\s 含 \\n，会把正文首行吞成标题。"""
    text = "第一百零八章\n他说第三章很好看。\n"
    m = CHAPTER_RE.search(text)
    assert m is not None
    assert m.group(1) == "第一百零八章"
    assert m.group(2) == "", f"标题吞掉了下一行: {m.group(2)!r}"


def test_fullwidth_space_and_title() -> None:
    m = CHAPTER_RE.search("　第 12 章　初入青云\n")
    assert m is not None and m.group(2) == "初入青云"


def test_body_mention_is_not_a_chapter_mark() -> None:
    """正文里提到「第三章」不该被当成章节标记——它不在行首。"""
    assert CHAPTER_RE.search("他说第三章很好看。") is None


# ══════════════════════════════════════════════════════════════════════════
# 英文章标（国际化第一批 ①，2026-08-26）
# ══════════════════════════════════════════════════════════════════════════


def test_chapter_plus_arabic_digit() -> None:
    m = CHAPTER_RE.search("Chapter 1: The Beginning\n")
    assert m is not None
    assert m.group(1) == "Chapter 1"
    assert m.group(2) == ": The Beginning"


def test_chapter_plus_spelled_out_word_any_case() -> None:
    """`Chapter One` / `chapter one` 都认，覆盖 Title Case 和全小写——
    见模块 docstring「英文章标」一节第 2 条：不覆盖全大写拼词。"""
    assert CHAPTER_RE.search("Chapter One\n").group(1) == "Chapter One"
    assert CHAPTER_RE.search("chapter one\n").group(1) == "chapter one"
    assert CHAPTER_RE.search("Chapter Twenty-One\n").group(1) == "Chapter Twenty-One"
    assert CHAPTER_RE.search("Chapter Ninety-Nine\n").group(1) == "Chapter Ninety-Nine"


def test_chapter_plus_uppercase_roman_numeral() -> None:
    m = CHAPTER_RE.search("CHAPTER XII\n")
    assert m is not None and m.group(1) == "CHAPTER XII"
    # 小写罗马数字故意不认——见模块 docstring 第 4 条。
    assert CHAPTER_RE.search("Chapter xii\n") is None


def test_ch_dot_abbreviation() -> None:
    assert CHAPTER_RE.search("Ch. 12\n").group(1) == "Ch. 12"
    assert CHAPTER_RE.search("ch.12\n").group(1) == "ch.12"


def test_bare_number_period_is_not_a_chapter_mark() -> None:
    """裸数字加句点（`1.`）**不认**——见模块 docstring 第 5 条，两次真书实测都踩中它：

    - `tests/fixtures/demo_novel.txt` 前言里一句大白话的编号列表
      （`1. **不是 M0 Day 3 的真书验收。** ……`）曾被第一版实现（带标题也认）切成一章。
    - 收窄到「独占一行、不带标题」之后，*Moby-Dick*（Gutenberg #2701）又炸出
      完全不同的碰撞：`Extracts` 一节里 `……Phil. Trans. A.D.\\n1668.` 纯粹是硬折行，
      `1668.` 独占一行只是排版折到那儿，前面那行本身不是空行——「要求前后夹空行」
      这种更强的收窄也未必挡得住。两条路径互相独立，判定这个形状没有纯语法收窄
      能兜住，所以 `Chapter` / `Ch.` 两支照留，`1.` 这一支不做。
    """
    assert CHAPTER_RE.search("1.\n") is None
    assert CHAPTER_RE.search("1. The Arrival\n") is None
    assert CHAPTER_RE.search("2. Not a chapter, just a list item.\n") is None
    # Moby-Dick 那种硬折行碰撞：上一行不是空行，这一行只有一个年份 + 句点。
    assert CHAPTER_RE.search(
        "—Richard Strafford’s Letter from the Bermudas. Phil. Trans. A.D.\n1668.\n"
    ) is None


def test_decimal_number_is_not_a_chapter_mark() -> None:
    assert CHAPTER_RE.search("12.5\n") is None


def test_part_level_heading_is_not_a_chapter_boundary() -> None:
    """`PART TWO` 是卷/部级标题，跟中文的「卷」同一个方向——故意不认，
    本模块没有一个「PART」分支，不是漏写。"""
    assert CHAPTER_RE.search("PART TWO\n") is None
    assert CHAPTER_RE.search("Part Two\n") is None


def test_title_starting_with_roman_letters_does_not_get_eaten() -> None:
    """`Chapter Mild` 不许被切成 marker=`Chapter M` + title=`ild`——
    见模块 docstring 第 3 条：数字/罗马数字/拼词那一支后面不许紧跟字母。
    """
    assert CHAPTER_RE.search("Chapter Mild\n") is None
    assert CHAPTER_RE.search("Chapter civil\n") is None
    assert CHAPTER_RE.search("Chapter Onerous\n") is None  # "one" 吃掉前三个字母的那类


def test_plural_chapters_word_is_not_a_chapter_mark() -> None:
    assert CHAPTER_RE.search("Chapters 1-5: Summary\n") is None


def test_english_book_chapterizes_with_ordinal_index_and_opaque_marker() -> None:
    """跟中文同一条纪律：`marker` 是显示用的原文，`index` 只看文本出现顺序。

    故意混用三种写法（阿拉伯数字 / 拼词 / 罗马数字），证明三者共用同一条 index 序列，
    谁都不比谁「更懂」章号——marker 不解析成数字，这条纪律中英文各自独立成立。
    """
    text = (
        "Chapter 1\n\nThe morning was cold.\n"
        "Chapter Two\n\nShe left before dawn.\n"
        "CHAPTER III\n\nHe never saw her again.\n"
    )
    out = chapterize(text)
    assert [c.index for c in out.chapters] == [1, 2, 3]
    assert [c.marker for c in out.chapters] == ["Chapter 1", "Chapter Two", "CHAPTER III"]
    assert out.chapters[0].body == "The morning was cold."
    assert out.chapters[2].body == "He never saw her again."


# ══════════════════════════════════════════════════════════════════════════
# normalize：正则修不得，所以脏字符在进正则之前就得死
# ══════════════════════════════════════════════════════════════════════════


def test_crlf_does_not_fabricate_a_title() -> None:
    """`$` 只认 \\n 而 `.` 吃得下 \\r → 无标题的章会长出一个 '\\r' 标题。

    这是 `\\s` 吞行的同胞：受害者同样是 group(2)，同样只有一个字符宽。
    """
    assert CHAPTER_RE.search("第一章\r\n正文\r\n").group(2) == "\r", "前提变了：\\r 不再被吃进标题"

    ch = chapters("第一章\r\n正文在此\r\n")
    assert len(ch) == 1
    assert ch[0].title == ""
    assert ch[0].body == "正文在此"


def test_bom_does_not_eat_chapter_one() -> None:
    """BOM 让**第一章**匹配不上 → 整章落进 preamble → 往后每章 index 少 1。

    表现形式是「切出章数 = 目录数 - 1」：看着像差一点点，实则全书错位。
    """
    assert CHAPTER_RE.search("﻿第一章 起点\n正文\n") is None, "前提变了：BOM 不再挡住行首"

    out = chapterize("﻿第一章 起点\n正文\n第二章 再启\n正文二\n")
    assert [(c.index, c.title) for c in out.chapters] == [(1, "起点"), (2, "再启")]
    assert out.preamble == ""


# ══════════════════════════════════════════════════════════════════════════
# index：来自文本顺序，不来自标题里印的数字
# ══════════════════════════════════════════════════════════════════════════


def test_index_is_ordinal_not_the_printed_numeral() -> None:
    """分卷重启会让印出来的章号重复。拿它当 `chapter.number` = 撞 UNIQUE / 全序断裂。

    这条是 `index` 存在的全部理由，也是 `marker` 不许被解析成数字的全部理由。
    """
    text = (
        "第一卷 风起\n"
        "第一章 甲\n正文甲\n"
        "第二章 乙\n正文乙\n"
        "第二卷 云涌\n"
        "第一章 丙\n正文丙\n"  # 印出来又是「第一章」
    )
    out = chapterize(text)
    assert [c.index for c in out.chapters] == [1, 2, 3]
    assert [c.marker for c in out.chapters] == ["第一章", "第二章", "第一章"]
    assert len({c.index for c in out.chapters}) == 3, "index 必须由构造保证唯一"


def test_index_ignores_gaps_and_repeats_in_the_markers() -> None:
    """章号跳号 / 缺号（真书的加更、删章）不影响 index 的连续性。"""
    out = chapterize("第一章 甲\n正\n第五章 乙\n正\n第三章 丙\n正\n")
    assert [c.index for c in out.chapters] == [1, 2, 3]


# ══════════════════════════════════════════════════════════════════════════
# 脏数据：卷标题 / 番外 / 作者的话（PLAN §8 Day 3 为它们预留了半天）
# ══════════════════════════════════════════════════════════════════════════


def test_volume_heading_is_not_a_chapter_boundary() -> None:
    """`卷` 不在 `[章节回]` 里 → 卷标题不切章，落进上一章 body。

    方向是选过的：多切一章会让全书 index 集体偏移（灾难），
    body 里多一行只是脏（可容忍）。
    """
    out = chapterize("第一章 甲\n正文甲\n第二卷 云涌\n第二章 乙\n正文乙\n")
    assert [c.index for c in out.chapters] == [1, 2]
    assert "第二卷 云涌" in out.chapters[0].body


def test_fanwai_and_author_note_are_not_chapter_boundaries() -> None:
    """番外 / 作者的话没有 `第`，不匹配 → 同样落进上一章 body，不被丢掉。"""
    out = chapterize("第一章 甲\n正文甲\n作者的话：今天加更\n番外：萧决的过去\n那一年……\n")
    assert len(out.chapters) == 1
    body = out.chapters[0].body
    assert "作者的话：今天加更" in body
    assert "番外：萧决的过去" in body


def test_fanwai_prefixed_heading_does_not_split() -> None:
    """`番外 第一章 少年萧决`：`第` 前面有非空白前缀 → `^[ \\t　]*第` 不匹配 → 不切章。

    **实测过才敢这么写**（先前这里断言它会切成两章，红了）。行首只容得下空白，
    容不下 `番外`。所以这个形态的番外整段落进上一章 body，跟卷标题同一个下场。
    """
    assert CHAPTER_RE.search("番外 第一章 少年萧决") is None

    out = chapterize("第一章 甲\n正文甲\n番外 第一章 少年萧决\n那一年……\n")
    assert [c.index for c in out.chapters] == [1]
    assert "番外 第一章 少年萧决" in out.chapters[0].body


def test_fanwai_on_its_own_heading_line_does_split() -> None:
    """诚实说明另一半：番外若**自己占一行**写成 `第一章`，它就是一章，切得开、拿 ordinal。

    这不是 bug 也不是能力——本模块认的是字面形状，不认「这一章是不是番外」，
    那是语义判断（ADR 0005 划在 v1 之外）。真书上这两种形态都存在。
    """
    out = chapterize("第一章 甲\n正文甲\n番外\n第一章 少年萧决\n那一年……\n")
    assert [c.index for c in out.chapters] == [1, 2]
    assert out.chapters[1].title == "少年萧决"


# ══════════════════════════════════════════════════════════════════════════
# 出参形状
# ══════════════════════════════════════════════════════════════════════════


def test_preamble_is_returned_not_dropped() -> None:
    """第一个章标之前的书名 / 简介 / 首个卷标题既不许丢，也不许塞进第 1 章 body。"""
    out = chapterize("《青云志》\n简介：一个故事。\n第一卷 风起\n第一章 甲\n正文甲\n")
    assert out.preamble == "《青云志》\n简介：一个故事。\n第一卷 风起\n"
    assert out.chapters[0].body == "正文甲"


def test_no_marks_means_zero_chapters_not_one_big_chapter() -> None:
    """没有章标 = 零章 + 全文进 preamble。**不是**「整本书算一章」——
    那会让导入器把一坨没切开的东西当成第 1 章写进库。"""
    out = chapterize("这是一段没有任何章节标记的文本。\n")
    assert out.chapters == []
    assert out.preamble == "这是一段没有任何章节标记的文本。\n"


def test_body_has_no_leading_blank_line() -> None:
    """body 首尾的空行必须掉：留着的话 anchor.py 分段会多出一个空段，
    而 para_index 是证据锚的第一个分量（ADR 0006）——偏 1 就全偏。"""
    out = chapterize("第一章 甲\n\n第一段。\n\n第二段。\n\n\n第二章 乙\n正文乙\n")
    assert out.chapters[0].body == "第一段。\n\n第二段。"


def test_headless_chapter_title_is_empty_string_not_none() -> None:
    # chapter.title 是 NOT NULL DEFAULT ''，两边同一个形状。
    (c,) = chapters("第一百零八章\n他说第三章很好看。\n")
    assert c.title == ""
    assert c.raw_heading == "第一百零八章"
    assert c.body == "他说第三章很好看。"


def test_raw_heading_keeps_the_line_as_printed() -> None:
    """导入器跟目录对账、报「章数 != 目录数」时，人要看的是这一行。"""
    (c,) = chapters("　第 12 章　初入青云\n正文\n")
    assert c.raw_heading == "第 12 章　初入青云"
    assert c.marker == "第 12 章"
    assert c.title == "初入青云"


def test_outputs_are_pydantic() -> None:
    out = chapterize("第一章 甲\n正文甲\n")
    assert isinstance(out, Chapterization)
    assert out.model_dump()["chapters"][0]["index"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 第二份副本：浏览器里那一份（**唯一一份**，且必须逐字节相同）
# ══════════════════════════════════════════════════════════════════════════

FRONTEND_COPY = Path(__file__).resolve().parents[1] / "frontend" / "src" / "chapterTitle.ts"


def test_frontend_marker_regex_is_the_same_one() -> None:
    """浏览器里那份章标正则 == 这儿这份，**逐字节**。

    本模块的注释写着「正则是唯一真相，不留第二份副本」，而 `chapterTitle.ts` 里还是有一份。
    它为什么必须存在：工作台那行标题双击可以改名，而**章号那一段不进输入框**
    （作者的原话：「双击之后就带章节名，左边的章号原地不动」）——
    要按住章标，就得当场知道它到哪儿结束，而「当场」指的是**作者此刻正在改的那一行**：
    还没保存，后端没见过它，问不到。

    所以按那条规矩付代价：这条测试就是钉那条缝的，同
    `test_serve.py::test_vite_outdir_and_dist_agree`（Vite 的 `outDir` 和 FastAPI 的 `_DIST`
    也是两个必须同时改的字面量）。**改这边不改那边，这条先红。**

    唯一允许的差别是前端多括了一组「中间那截空白」（`([ \\t　]*)`，改名时原样带回去），
    摊平之后必须一字不差。JS 那份**不带标志**（`/…/;` 后面什么都没有，本测试的正则要求了
    这一点）：它一次只看一行，而这边要在整份正文上找章界，所以那边有 `re.M`、这边没有。
    """
    source = FRONTEND_COPY.read_text(encoding="utf-8")
    found = re.search(r"const MARKER =\s*/(\S.*)/;", source)
    assert found, f"{FRONTEND_COPY.name} 里找不到 `const MARKER = /…/;`——它是这条缝的另一半"

    flattened = found.group(1).replace(r"([ \t　]*)", r"[ \t　]*")
    assert flattened == CHAPTER_RE.pattern, (
        "前端那份章标正则和 `CHAPTER_RE` 漂开了：\n"
        f"  前端（摊平后）：{flattened}\n"
        f"  这儿：          {CHAPTER_RE.pattern}\n"
        "两份必须同时改。漂了之后症状看得见但很绕：这边多认一个章标 → 存盘时切不出一章 → 422；"
        "少认一个 → 改名框里出现整行（含章号），而作者会以为章号也能改。"
    )


# ══════════════════════════════════════════════════════════════════════════
# drop_toc_duplicates：目录页双计（2026-08-27，维护者拍板的判据）
# ══════════════════════════════════════════════════════════════════════════
#
# 判据：一章正文是空的，而且全书还有另一章 (marker, title) 跟它一模一样、正文
# 非空 ⇒ 目录里那一行，丢掉。四种情形各钉一条——**目录在结尾**和**分卷重启**
# 两条尤其要有：它们是这个判据比「行距 ≤1 且连续 ≥3」那一版强的地方（分卷重启
# 那一版会误伤，这一版不会；目录在结尾那一版没验过，这一版验了）。


def test_toc_at_the_start_is_dropped() -> None:
    """目录页排在最前面：两行标题先各自空跑一遍，正文才真正开始。"""
    text = (
        "第一章 山门\n"
        "第二章 落幕\n"
        "\n"
        "第一章 山门\n"
        "\n"
        "萧决拾级而上。\n"
        "\n"
        "第二章 落幕\n"
        "\n"
        "剑光落下。\n"
    )
    book = chapterize(text)
    assert len(book.chapters) == 4, "先确认切章本身切出了 4 个命中，不然这条测试测不到东西"

    filtered, skipped = drop_toc_duplicates(book)

    assert [c.index for c in filtered.chapters] == [1, 2], "剩下的两章必须重新连续编号"
    assert [(c.marker, c.title, c.body) for c in filtered.chapters] == [
        ("第一章", "山门", "萧决拾级而上。"),
        ("第二章", "落幕", "剑光落下。"),
    ]
    assert [(s.position, s.raw_heading) for s in skipped] == [
        (1, "第一章 山门"),
        (2, "第二章 落幕"),
    ], "撤销要用的 (原始位置, 标题行原样) 必须精确"


def test_toc_at_the_end_is_dropped() -> None:
    """目录页排在末尾（有些排版把目录放书末）：这是行距版判据没验过的一种。"""
    text = (
        "第一章 山门\n"
        "\n"
        "萧决拾级而上。\n"
        "\n"
        "第二章 落幕\n"
        "\n"
        "剑光落下。\n"
        "\n"
        "第一章 山门\n"
        "第二章 落幕\n"
    )
    book = chapterize(text)
    assert len(book.chapters) == 4

    filtered, skipped = drop_toc_duplicates(book)

    assert [c.index for c in filtered.chapters] == [1, 2]
    assert [(c.marker, c.title, c.body) for c in filtered.chapters] == [
        ("第一章", "山门", "萧决拾级而上。"),
        ("第二章", "落幕", "剑光落下。"),
    ]
    assert [s.position for s in skipped] == [3, 4]


def test_empty_placeholder_chapter_without_a_duplicate_is_kept() -> None:
    """作者留的空占位章（`append_chapter` 造的「第 N 章」还没写字）不许被当成目录丢掉。

    判据的「而且」那一半救了它：正文虽空，但全书找不到第二个同名同标题的非空章
    ——这本书还没写到那儿，`(marker, title)` 在别处压根没有匹配。
    """
    text = (
        "第一章 山门\n"
        "\n"
        "萧决拾级而上。\n"
        "\n"
        "第二章 待写\n"
        "\n"
        "第三章 落幕\n"
        "\n"
        "剑光落下。\n"
    )
    book = chapterize(text)
    assert len(book.chapters) == 3

    filtered, skipped = drop_toc_duplicates(book)

    assert skipped == []
    assert [c.index for c in filtered.chapters] == [1, 2, 3]
    assert filtered.chapters[1].body == "", "空占位章的正文本来就该是空的——这条不该被填"


def test_volume_restart_duplicate_headings_are_both_kept() -> None:
    """分卷重启：两个真「第一章」标题完全一样，但**两个都有正文**——都不许丢。

    这是判据「而且」那一半的另一面：`(marker, title)` 相同只是**必要条件**，
    「有一边正文是空的」才会触发丢弃。行距版判据在这种输入上会退化成两个相邻的短
    间隔，容易连续两次判成目录；这一版因为两章都非空，从一开始就不会进入候选。
    """
    text = "第一章\n\n主角出发了。\n\n第一章\n\n时间来到十年后。\n"
    book = chapterize(text)
    assert len(book.chapters) == 2
    assert (book.chapters[0].marker, book.chapters[0].title) == (
        book.chapters[1].marker,
        book.chapters[1].title,
    ), "先确认这两章的 (marker, title) 真的完全相同，不然这条测试测不到分卷重启"

    filtered, skipped = drop_toc_duplicates(book)

    assert skipped == []
    assert [c.index for c in filtered.chapters] == [1, 2]
    assert [c.body for c in filtered.chapters] == ["主角出发了。", "时间来到十年后。"]


def test_a_book_with_no_toc_collision_is_untouched() -> None:
    """没有目录碰撞的书（今天绝大多数真书）：一章都不会被这层碰到。"""
    text = "第一章 山门\n\n萧决拾级而上。\n\n第二章 落幕\n\n剑光落下。\n"
    book = chapterize(text)

    filtered, skipped = drop_toc_duplicates(book)

    assert skipped == []
    assert filtered == book
