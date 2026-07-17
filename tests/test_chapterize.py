"""切章的回归测试。

前三条（`\\s` 吞行 / 全角空格 / 正文提及）钉死的是 PLAN §8 记录的**实测 bug**，
它们比这个模块本身老——**不许在没让它们红一次的前提下改 `CHAPTER_RE`**。

下半部分是脏数据（卷标题 / 番外 / 作者的话）与 index 全序的行为。
⚠️ **这里没有一本真实网文**（M0 剩余项）。所以全是手写 fixture，
**不许从这些测试推出任何覆盖率结论**——「章数 = 目录数」还没被验收过。
"""

from __future__ import annotations

from novel_harness.text.chapterize import CHAPTER_RE, Chapterization, chapterize, chapters

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
