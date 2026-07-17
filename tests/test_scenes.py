"""场景块解析与写回（`text/scenes.py`）。

两组断言，方向相反，这是本模块的全部性格：

- **解析永不抛、永不猜**（原则 8）：看不懂 = 当作没写 = cast 为空 = fail-closed。
- **写回宁可抛，也不写坏**：它改的是作者磁盘上的文件（ADR 0007）。

外加一组无损往返：无改动 = 逐字节原样。它是「写回不许顺手规范化」那条约束的钉子。
"""

from __future__ import annotations

import pytest

from novel_harness.checks.base import Scene
from novel_harness.text.scenes import (
    AmbiguousScene,
    MalformedDirective,
    SceneNotFound,
    UnwritableValue,
    parse_scenes,
    write_scene_directive,
)

# ══════════════════════════════════════════════════════════════════════════
# 解析 —— 好路径
# ══════════════════════════════════════════════════════════════════════════

CANONICAL = [
    "## 场景 3",
    "<!-- nh: cast=萧决,顾清音,李管家 loc=青云城主府 goal=李管家试探萧决的身世 -->",
    "李管家推门进来的时候，萧决正在擦剑。",
]
"""PLAN §5「改 3」/ `Scene` docstring 里逐字抄下来的那一段。"""


def test_parses_the_canonical_block() -> None:
    (scene,) = parse_scenes(CANONICAL)
    assert scene == Scene(
        number=3,
        cast=["萧决", "顾清音", "李管家"],
        loc="青云城主府",
        goal="李管家试探萧决的身世",
        para_index=1,
        decl_text=CANONICAL[1],
    )


def test_produces_exactly_the_shared_contract_type() -> None:
    """R4 到今天为止只在测试里被喂过数据（tests/test_checks.py:185）。

    这条钉的是「本模块产出的就是 `checks.base.Scene`」——不是一个长得像它的东西。
    """
    (scene,) = parse_scenes(CANONICAL)
    assert type(scene) is Scene


def test_anchor_is_the_declaration_line_and_is_findable() -> None:
    """`decl_text` 必须是 `paragraphs[para_index]` 的真子串。

    否则 `(para_index, quote_text, k)` 这个锚定不到位——而 ADR 0006 换掉 offset
    换来的正是「锚自带校验」。
    """
    (scene,) = parse_scenes(CANONICAL)
    assert CANONICAL[scene.para_index].count(scene.decl_text) == 1


def test_cast_is_appellation_text_not_resolved() -> None:
    """`cast` 出去的时候还是作者写的字。「师兄」在这里就该原样留着——

    解析归 `panel.constraints.resolve_cast()`，因为「指向 8 个人」这个不确定性
    本模块接不住（它连 store 都没有）。
    """
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast=萧决,师兄 -->"])
    assert scene.cast == ["萧决", "师兄"]


def test_multiple_scenes_keep_source_order() -> None:
    scenes = parse_scenes(
        [
            "## 场景 1",
            "<!-- nh: cast=萧决 loc=北荒 -->",
            "风很大。",
            "## 场景 2",
            "<!-- nh: cast=顾清音 loc=青云城主府 -->",
        ]
    )
    assert [(s.number, s.loc, s.para_index) for s in scenes] == [
        (1, "北荒", 1),
        (2, "青云城主府", 4),
    ]


def test_accepts_fullwidth_and_optional_title() -> None:
    """作者的中文输入法会打出全角空格和全角数字；`## 场景 3 初入青云` 也是场景块。"""
    scenes = parse_scenes(["　##　场景　３　初入青云", "<!-- nh:　cast=萧决　loc=北荒　-->"])
    assert [(s.number, s.cast, s.loc) for s in scenes] == [(3, ["萧决"], "北荒")]


@pytest.mark.parametrize("sep", [",", "，", "、"])
def test_all_three_chinese_separators(sep: str) -> None:
    (scene,) = parse_scenes(["## 场景 1", f"<!-- nh: cast=萧决{sep}顾清音 -->"])
    assert scene.cast == ["萧决", "顾清音"]


def test_space_inside_an_appellation_survives() -> None:
    """`李 管家` 里那个空格是名字的一部分。切了就查无此人——而查无此人是静默的。"""
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast=李 管家, 萧决 loc=北荒 -->"])
    assert scene.cast == ["李 管家", "萧决"]


def test_partial_directive_leaves_the_rest_none() -> None:
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast=萧决 -->"])
    assert (scene.cast, scene.loc, scene.goal) == (["萧决"], None, None)


def test_unknown_key_does_not_eat_the_cast() -> None:
    """`cast=萧决 mood=紧张` 里 cast 到 `mood=` 为止。

    只认三个已知 key 的话 cast 会吃成「萧决 mood=紧张」→ resolve 查无此人 →
    一个未知 key 静默毁掉一整场。
    """
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast=萧决 mood=紧张 loc=北荒 -->"])
    assert (scene.cast, scene.loc) == (["萧决"], "北荒")


# ══════════════════════════════════════════════════════════════════════════
# 解析 —— 看不懂就闭嘴（原则 8）
# ══════════════════════════════════════════════════════════════════════════


def test_parse_never_raises_on_garbage() -> None:
    assert parse_scenes([]) == []
    assert parse_scenes(["就是一段正文。", "<!-- 作者的普通注释 -->", "## 第三章 北荒"]) == []


def test_heading_without_directive_still_yields_a_scene() -> None:
    """**这一条是写回存在的前提。**

    面板要能把 cast「写进去」，前提是这一场先存在。而空 cast 在下游是安全方向：
    `ResolvedCast.complete` 为假 → `scene_constraints` 退化成全部秘密（fail-closed）。
    """
    (scene,) = parse_scenes(["## 场景 3", "李管家推门进来。"])
    assert scene == Scene(number=3, cast=[], loc=None, goal=None, para_index=0, decl_text="## 场景 3")


@pytest.mark.parametrize(
    "heading",
    [
        "## 场景 0",  # Scene.number 是 ge=1，构造出来会抛——而解析侧不许抛
        "## 场景 3xyz",  # 数字后面必须是空白或行尾，否则不认
        "### 场景 3",  # 别的层级的标题
        "# 场景 3",
        "他在场景 3 里写错了。",  # 不在行首
        "## 场景",  # 没有号
    ],
)
def test_not_a_scene_heading(heading: str) -> None:
    assert parse_scenes([heading, "<!-- nh: cast=萧决 -->"]) == []


def test_duplicate_key_is_treated_as_unwritten() -> None:
    """取第一个就是猜，而猜错的产物是一份**看起来完整**、实则少一个人的 cast——

    正是 `constraints.py` 逐字论证过的那条泄漏路径。空 cast 反而是 fail-closed。
    """
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast=萧决 cast=顾清音 loc=北荒 -->"])
    assert (scene.cast, scene.loc) == ([], "北荒")


def test_orphan_directive_is_ignored() -> None:
    """没有标题就归不到 `Scene.number`。"""
    assert parse_scenes(["<!-- nh: cast=萧决 loc=北荒 -->", "正文。"]) == []


def test_directive_must_own_its_line() -> None:
    """正文里顺手写的那个位置上它是 HTML 注释，不是声明行。"""
    (scene,) = parse_scenes(["## 场景 1", "他说 <!-- nh: cast=萧决 --> 然后走了。"])
    assert scene.cast == []


def test_directive_after_prose_belongs_to_nobody() -> None:
    """隔着正文的指令行不认——认了就是把 B 场的 cast 安到 A 场头上。"""
    (scene,) = parse_scenes(["## 场景 1", "风很大。", "<!-- nh: cast=萧决 -->"])
    assert (scene.cast, scene.para_index) == ([], 0)


def test_empty_values_are_no_values() -> None:
    (scene,) = parse_scenes(["## 场景 1", "<!-- nh: cast= loc= goal= -->"])
    assert (scene.cast, scene.loc, scene.goal) == ([], None, None)


def test_duplicate_scene_numbers_are_not_renumbered() -> None:
    """那是作者的笔误。替他改掉他就永远看不见它。"""
    scenes = parse_scenes(["## 场景 3", "<!-- nh: cast=萧决 -->", "## 场景 3", "<!-- nh: cast=顾清音 -->"])
    assert [(s.number, s.cast) for s in scenes] == [(3, ["萧决"]), (3, ["顾清音"])]


def test_no_offsets_escape() -> None:
    """ADR 0006：出参只有 `(para_index, decl_text)`，行内 offset 一个都不许出来。"""
    (scene,) = parse_scenes(CANONICAL)
    assert set(Scene.model_fields) == {"number", "cast", "loc", "goal", "para_index", "decl_text"}
    assert "offset" not in scene.model_dump_json()


# ══════════════════════════════════════════════════════════════════════════
# 写回 —— 无损
# ══════════════════════════════════════════════════════════════════════════

FILE = (
    "# 第一百五十一章 试探\n"
    "\n"
    "## 场景 1\n"
    "<!-- nh: cast=萧决 loc=北荒 -->\n"
    "风很大。\n"
    "\n"
    "## 场景 3\n"
    "\n"
    "  <!--  nh:  cast=萧决,顾清音  mood=紧张  loc=青云城主府  -->\n"
    "李管家推门进来。\n"
)


@pytest.mark.parametrize(
    "text",
    [
        FILE,
        FILE.replace("\n", "\r\n"),  # CRLF
        FILE.rstrip("\n"),  # 没有尾换行
        "## 场景 3\n",  # 光秃秃的标题
    ],
)
def test_no_change_is_byte_identical(text: str) -> None:
    """**这条是「写回不许顺手规范化」的钉子。**

    三个字段都是 KEEP 时不许有任何字节变化——CRLF、尾换行的有无、`<!--` 里的双空格
    都得原样。系统规范化了它不拥有的东西，代价是每次面板点一下作者的 git diff 里
    就多出一堆噪声。
    """
    assert write_scene_directive(text, 3) is not None
    assert write_scene_directive(text, 3) == text


def test_writing_the_same_value_is_byte_identical() -> None:
    """面板重复点同一份 cast 不该在文件里留下痕迹。"""
    assert write_scene_directive(FILE, 1, cast=["萧决"], loc="北荒") == FILE


def test_write_preserves_unknown_keys_indent_and_spacing() -> None:
    """不认识 `mood=` 不等于可以删它。缩进和 `<!--` 里的空格同理。"""
    out = write_scene_directive(FILE, 3, cast=["萧决", "顾清音", "李管家"])
    assert "  <!--  nh:  cast=萧决,顾清音,李管家  mood=紧张  loc=青云城主府  -->\n" in out
    assert out.replace("cast=萧决,顾清音,李管家", "cast=萧决,顾清音") == FILE


def test_write_touches_only_the_named_scene() -> None:
    out = write_scene_directive(FILE, 3, loc="北荒")
    assert "<!-- nh: cast=萧决 loc=北荒 -->\n" in out  # 场景 1 原样
    assert out.count("## 场景") == 2


def test_write_preserves_crlf() -> None:
    crlf = FILE.replace("\n", "\r\n")
    out = write_scene_directive(crlf, 3, cast=["萧决"])
    assert "\n" not in out.replace("\r\n", "")
    assert out.count("\r\n") == crlf.count("\r\n")


# ══════════════════════════════════════════════════════════════════════════
# 写回 —— 改了之后解析得回来（往返闭合）
# ══════════════════════════════════════════════════════════════════════════


def _scene(text: str, number: int) -> Scene:
    (scene,) = [s for s in parse_scenes(text.splitlines()) if s.number == number]
    return scene


def test_panel_writes_a_cast_into_a_bare_heading() -> None:
    """面板点选 cast → 写进文件的主路径：这一场此前只有一行标题。"""
    text = "## 场景 3\n李管家推门进来。\n"
    out = write_scene_directive(text, 3, cast=["萧决", "顾清音"], loc="青云城主府")
    assert out == "## 场景 3\n<!-- nh: cast=萧决,顾清音 loc=青云城主府 -->\n李管家推门进来。\n"
    assert _scene(out, 3).cast == ["萧决", "顾清音"]


def test_insert_after_a_heading_with_no_trailing_newline() -> None:
    """标题是最后一行且没有尾换行——不补换行的话指令行会拼到标题屁股后面。"""
    out = write_scene_directive("正文。\n## 场景 3", 3, cast=["萧决"])
    assert out == "正文。\n## 场景 3\n<!-- nh: cast=萧决 -->"
    assert _scene(out, 3).cast == ["萧决"]


def test_insert_keeps_the_heading_indent() -> None:
    out = write_scene_directive("  ## 场景 3\n", 3, cast=["萧决"])
    assert out == "  ## 场景 3\n  <!-- nh: cast=萧决 -->\n"


def test_insert_nothing_when_everything_is_empty() -> None:
    """没东西可写就别在作者的文件里放一行空指令。"""
    assert write_scene_directive("## 场景 3\n", 3, cast=[], loc=None) == "## 场景 3\n"


def test_appends_a_new_key_in_canonical_order() -> None:
    out = write_scene_directive(FILE, 1, goal="萧决独自面对北荒")
    assert "<!-- nh: cast=萧决 loc=北荒 goal=萧决独自面对北荒 -->\n" in out
    assert _scene(out, 1).goal == "萧决独自面对北荒"


def test_append_into_an_empty_directive() -> None:
    out = write_scene_directive("## 场景 3\n<!-- nh: -->\n", 3, cast=["萧决"], loc="北荒")
    assert out == "## 场景 3\n<!-- nh: cast=萧决 loc=北荒 -->\n"


def test_none_deletes_the_key_and_leaves_no_double_space() -> None:
    out = write_scene_directive(FILE, 1, loc=None)
    assert "<!-- nh: cast=萧决 -->\n" in out
    assert _scene(out, 1).loc is None


def test_empty_cast_deletes_the_key() -> None:
    """`[]` 和 `None` 解析回来都是「没写」。留一个空 `cast=` 只是一块看不出意图的垃圾。"""
    out = write_scene_directive(FILE, 1, cast=[])
    assert "<!-- nh: loc=北荒 -->\n" in out


def test_delete_the_only_key() -> None:
    out = write_scene_directive("## 场景 3\n<!-- nh: cast=萧决 -->\n", 3, cast=None)
    assert out == "## 场景 3\n<!-- nh: -->\n"


def test_write_all_three_at_once() -> None:
    out = write_scene_directive(FILE, 1, cast=["顾清音"], loc="青云城主府", goal="她在等他")
    scene = _scene(out, 1)
    assert (scene.cast, scene.loc, scene.goal) == (["顾清音"], "青云城主府", "她在等他")


def test_written_cast_is_stripped() -> None:
    out = write_scene_directive(FILE, 1, cast=[" 萧决 ", "", "　顾清音"])
    assert _scene(out, 1).cast == ["萧决", "顾清音"]


def test_write_then_parse_roundtrips_a_space_bearing_appellation() -> None:
    out = write_scene_directive(FILE, 1, cast=["李 管家"])
    assert _scene(out, 1).cast == ["李 管家"]


# ══════════════════════════════════════════════════════════════════════════
# 写回 —— 宁可拒绝，也不写坏
# ══════════════════════════════════════════════════════════════════════════


def test_missing_scene_raises_instead_of_doing_nothing() -> None:
    """静默返回原文比报错更糟：作者点了一下，会以为写进去了。"""
    with pytest.raises(SceneNotFound, match="场景 9"):
        write_scene_directive(FILE, 9, cast=["萧决"])


def test_missing_scene_raises_even_with_nothing_to_write() -> None:
    with pytest.raises(SceneNotFound):
        write_scene_directive(FILE, 9)


def test_duplicate_scene_number_raises() -> None:
    text = "## 场景 3\n<!-- nh: cast=萧决 -->\n## 场景 3\n<!-- nh: cast=顾清音 -->\n"
    with pytest.raises(AmbiguousScene):
        write_scene_directive(text, 3, cast=["李管家"])


def test_duplicate_key_raises_when_written() -> None:
    text = "## 场景 3\n<!-- nh: cast=萧决 cast=顾清音 -->\n"
    with pytest.raises(MalformedDirective, match="cast"):
        write_scene_directive(text, 3, cast=["李管家"])


def test_duplicate_key_does_not_block_an_unrelated_key() -> None:
    """`cast=` 有两份不影响写 `loc=`——拒绝要拒绝得有理由，不是一见脏就整片罢工。"""
    text = "## 场景 3\n<!-- nh: cast=萧决 cast=顾清音 -->\n"
    out = write_scene_directive(text, 3, loc="北荒")
    assert out == "## 场景 3\n<!-- nh: cast=萧决 cast=顾清音 loc=北荒 -->\n"


@pytest.mark.parametrize(
    "bad",
    ["萧,决", "萧，决", "萧、决"],
)
def test_separator_inside_an_appellation_is_refused(bad: str) -> None:
    """名字里有逗号会被切成两个人。**这个洞由回读比对发现，不靠穷举非法字符。**"""
    with pytest.raises(UnwritableValue):
        write_scene_directive(FILE, 1, cast=[bad])


@pytest.mark.parametrize("bad", ["青云\n城", "青云 loc=北荒", "青云 goal=x"])
def test_unwritable_loc_is_refused(bad: str) -> None:
    """换行会把指令行劈成两半；`loc=青云 loc=北荒` 会变成重复 key。"""
    with pytest.raises(UnwritableValue):
        write_scene_directive(FILE, 1, loc=bad)


def test_arrow_inside_a_value_actually_survives() -> None:
    """`loc=青云 --> 城` 写得进去也读得回来——`_DIRECTIVE_RE` 的 `.*?` 会回溯到最后一个
    `-->`。**所以不许把它列进黑名单。**

    这条钉的是「拒绝要有理由」：`_verify` 是回读比对，不是一张猜出来的非法字符表。
    一张表既会漏（漏的那个改坏文件），也会误禁（误禁的那个作者改不了——他的地名
    合法，只是系统以为不合法）。
    """
    out = write_scene_directive(FILE, 1, loc="青云 --> 城")
    assert _scene(out, 1).loc == "青云 --> 城"


def test_refusal_leaves_the_original_untouched() -> None:
    """抛异常时调用方拿到的是异常，不是一份改坏的全文——写回是纯函数，文件由调用方落盘。"""
    with pytest.raises(UnwritableValue):
        write_scene_directive(FILE, 1, cast=["萧,决"])
    assert _scene(FILE, 1).cast == ["萧决"]
