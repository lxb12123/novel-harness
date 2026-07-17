"""场景块的解析与写回（PLAN §8 Day 5 下午）—— `Scene` 的**唯一生产者**。

```markdown
## 场景 3
<!-- nh: cast=萧决,顾清音,李管家 loc=青云城主府 goal=李管家试探萧决的身世 -->
```

在此之前 `checks.base.Scene` 全仓库只在测试里被构造过一次：R4 是一条正确的纯函数，
但没有任何东西喂得动它。这个文件是那条链路缺的那一环。

`Scene` **定义在 `checks/base.py`，不在这里**（那份 docstring 已经写明它是本模块与
`checks/` 的共享契约）。这里只 import，不另造——契约有两份定义就等于没有契约。

── 这一层不解析任何东西 ──────────────────────────────────────────────────

`cast=萧决,顾清音,师兄` 出去的时候还是这三个**字符串**，不是 node_id。解析归
`panel/constraints.resolve_cast()`，理由写在 `scene_constraints` 的 Notes 里：
「师兄」指向 8 个人时必须有人接住这个不确定性，而本模块连 store 都拿不到，
接不住。**所以它连试都不许试。** 本模块 import 的东西里没有 `graph`、没有 sqlite3、
没有 I/O——它是 `str` 进 `str`/`Scene` 出的纯函数。

── 不确定就闭嘴（原则 8）──────────────────────────────────────────────────

解析侧**永不抛异常，永不猜**。看不懂的东西一律当作没写：

| 输入 | 产出 | 为什么 |
|---|---|---|
| `## 场景 0` / `## 场景 -1` | 不产 Scene | `Scene.number` 是 `ge=1`；这不是场景块 |
| `## 场景 3xyz` | 不产 Scene | 数字后面必须是空白或行尾，否则不认 |
| `## 场景 3` 后面没有指令行 | 产 Scene，`cast=[]`，锚在标题行 | 面板要能把 cast 写进去，前提是这一场存在 |
| `<!-- nh: cast=A cast=B -->` | 该 key 当作没写 | 取第一个就是猜 |
| `<!-- nh: mood=紧张 -->` | 未知 key 忽略 | 但**写回时原样保留**——不认识不等于可以删 |
| 没有标题的孤儿指令行 | 忽略 | 它不属于任何一场，归不到 `Scene.number` |

**「当作没写」在这条链路上是安全方向**：cast 为空 → `resolve_cast` 的 `complete` 为假
→ `scene_constraints` 退化成全部秘密（fail-closed）。反过来「取第一个」会让一份
**看起来完整**的 cast 少一个人，而那正是 `constraints.py` 逐字论证过的泄漏路径。

── 写回侧相反：宁可拒绝，也不写坏 ────────────────────────────────────────

`write_scene_directive` 会 raise（`SceneWriteRefused` 的四个子类）。因为它的失败形态不是
「少报一条」，是**把作者的文件改错**——而正文是磁盘上作者自己的 Markdown（ADR 0007），
系统写坏了他要靠 git diff 才发现。所以写回：

1. **只动它自己拥有的那几个字符。** 缩进、行尾（`\\r\\n` / 无尾换行）、未知 key、
   key 顺序、`<!--` 内部的多余空格——全部逐字节保留。无改动 = 原样返回。
2. **写完自己再解析一遍验尸**（`_verify`）。作者传了个含逗号的名字（`cast=["萧,决"]`）
   会被切成两个人，这种转义漏洞不靠穷举堵，靠回读比对。

── 锚 ────────────────────────────────────────────────────────────────────

`Scene` 出去的定位信息只有 `(para_index, decl_text)`，R4 拿它拼 `TextAnchor`（k 默认 0）。
**本模块内部的那些 `int` 是行内 offset，它们一个都不许出现在出参里**（ADR 0006）：
它们只在 `write_scene_directive` 的一次调用内活着，用来做原地拼接。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ..checks.base import Scene

__all__ = [
    "KEEP",
    "AmbiguousScene",
    "MalformedDirective",
    "SceneNotFound",
    "SceneWriteRefused",
    "UnwritableValue",
    "parse_scenes",
    "write_scene_directive",
]

# ══════════════════════════════════════════════════════════════════════════
# 词法
# ══════════════════════════════════════════════════════════════════════════

_INLINE_SPACE: Final = " \t　"
"""行内空白：**半角空格 / Tab / 全角空格 U+3000，不含 `\\n`。**

同 `chapterize.py` 那个实测 bug（PLAN §8）：写成 `[\\s　]` 的话 `\\s` 含 `\\n`，
`## 场景 3` 的标题正则会把正文首行一起吞进去。这里逐字段扫单行，`\\n` 不该出现在
任何一个字符类里。
"""

_SCENE_HEADING_RE: Final = re.compile(
    rf"^[{_INLINE_SPACE}]*##(?!#)[{_INLINE_SPACE}]*场景"
    rf"[{_INLINE_SPACE}]*([0-9０-９]+)(?:[{_INLINE_SPACE}]+\S.*?)?[{_INLINE_SPACE}]*$"
)
"""`## 场景 3` / `　##　场景　３　初入青云`。

- `##(?!#)`：`### 场景 3` 是别的层级的标题，不是场景块。
- `[0-9０-９]`：作者的中文输入法会打出全角数字，`int("３")` 认得。
- 数字后面要么是行尾，要么是**空白 + 内容**（可选的小标题，忽略）。`## 场景 3x` 不认——
  认了就是在猜他想写什么。
"""

_DIRECTIVE_RE: Final = re.compile(
    rf"^[{_INLINE_SPACE}]*(<!--[{_INLINE_SPACE}]*nh[{_INLINE_SPACE}]*:(.*?)-->)[{_INLINE_SPACE}]*$"
)
"""`<!-- nh: ... -->`，**必须独占一行**（它是「声明行」）。

group(1) = `decl_text`（锚的 `quote_text`，必须是本行的真子串）；group(2) = 内容。
正文里顺手写的 `<!-- nh: ... -->` 不算——那种位置上它是个 HTML 注释，不是声明。
"""

_KEY_RE: Final = re.compile(rf"(?:\A|[{_INLINE_SPACE}])([A-Za-z_][A-Za-z0-9_-]*)=")
"""**任意** `key=` 都是一个分隔符，不只是 `cast` / `loc` / `goal`。

这条是未知 key 能被无损保留的原因：`cast=萧决 mood=紧张` 里 cast 的值到 `mood=` 为止。
若只认三个已知 key，cast 会吃成 `萧决 mood=紧张`，然后 `resolve` 查无此人——
一个未知 key 静默毁掉一整场的 cast。
"""

_CAST_SEP_RE: Final = re.compile(r"[,，、]")
"""半角逗号 / 全角逗号 / 顿号。三个都是中文作者会打出来的分隔符。

**不含空格**：`cast=李 管家` 里那个空格是名字的一部分，切了就查无此人。
"""

_CANONICAL_KEYS: Final = ("cast", "loc", "goal")
"""新建指令行 / 追加新 key 时的书写顺序。**已存在的 key 一律留在原位。**"""


class SceneWriteRefused(Exception):
    """写回拒绝执行。

    **拿它当「弹给作者」的信号，不是当 bug。** 与 `panel.constraints.UnresolvedCast`
    同一个性质：系统看不懂作者的文件时，唯一正确的动作是把它原样交还给他。
    """


class SceneNotFound(SceneWriteRefused):
    """要写的场景号在这一章里不存在。

    **不能静默返回原文**：面板上作者点了「把 cast 写进去」，什么都没发生比报错更糟——
    他会以为写进去了。
    """


class AmbiguousScene(SceneWriteRefused):
    """同一章里有两个同号的 `## 场景 N`。写哪一个都是猜。"""


class MalformedDirective(SceneWriteRefused):
    """要写的 key 在指令行里出现了两次。改哪一个都是猜。"""


class UnwritableValue(SceneWriteRefused):
    """写完回读，解析结果和请求的值对不上——值里有本格式表达不了的字符。

    典型：`cast=["萧,决"]`（名字里有逗号）会被切成两个人。**由 `_verify` 回读发现，
    不靠穷举非法字符**——穷举一定会漏，而漏掉的那个会安静地改坏作者的文件。
    """


class _Keep:
    """`write_scene_directive` 的「这个字段别动」哨兵。

    不能用 `None`：`loc=None` 有确定的含义（**删掉这个 key**）。两者共用 `None`
    的话，「只改 cast」就会顺手把作者的 loc 和 goal 抹掉。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "KEEP"


KEEP: Final = _Keep()


@dataclass(frozen=True, slots=True)
class _Field:
    """指令行里的一个 `key=value`。三个 offset 都是**行内**的，出不了本模块。"""

    key: str
    value: str
    sep_start: int
    """`key` 前面那个分隔空格的位置（删除时要连它一起删，否则留下双空格）。"""
    value_start: int
    end: int
    """`value` 末尾（已 rstrip 掉尾随空白）。"""


@dataclass(frozen=True, slots=True)
class _Block:
    """一个场景块：一行标题 + 可选的一行指令。index 是入参序列的下标。"""

    number: int
    heading: int
    directive: int | None
    decl_text: str
    body_start: int
    body_end: int


def _tokenize(line: str, start: int, end: int) -> list[_Field]:
    fields: list[_Field] = []
    matches = list(_KEY_RE.finditer(line, start, end))
    for i, m in enumerate(matches):
        value_start = m.end()
        value_end = matches[i + 1].start() if i + 1 < len(matches) else end
        value = line[value_start:value_end].rstrip(_INLINE_SPACE)
        fields.append(
            _Field(
                key=m.group(1),
                value=value,
                sep_start=m.start(),
                value_start=value_start,
                end=value_start + len(value),
            )
        )
    return fields


def _by_key(fields: Sequence[_Field]) -> dict[str, list[_Field]]:
    out: dict[str, list[_Field]] = {}
    for field in fields:
        out.setdefault(field.key, []).append(field)
    return out


def _unique_value(by_key: dict[str, list[_Field]], key: str) -> str | None:
    # 重复 key = 该 key 当作没写（见模块 docstring 的表）。取第一个就是猜。
    found = by_key.get(key, ())
    if len(found) != 1:
        return None
    return found[0].value or None


def _split_cast(value: str | None) -> list[str]:
    if value is None:
        return []
    return _clean(_CAST_SEP_RE.split(value))


def _clean(items: Sequence[str]) -> list[str]:
    # 不去重：顺序即面板行序，去重归 `ResolvedCast`（它按 node_id 去重，比按字面量准）。
    return [s for s in (item.strip(_INLINE_SPACE) for item in items) if s]


# ══════════════════════════════════════════════════════════════════════════
# 扫描 —— 解析与写回共用的唯一一份
# ══════════════════════════════════════════════════════════════════════════


def _scan(lines: Sequence[str]) -> list[_Block]:
    """找出全部场景块。

    `lines` 是「一行一项、不含行尾换行」的序列。解析侧喂的是 `paragraphs`（下标即
    `para_index`），写回侧喂的是去掉行尾的物理行——**两个下标空间不同，但词法只有这一份。**
    """
    blocks: list[_Block] = []
    for index, line in enumerate(lines):
        heading = _SCENE_HEADING_RE.match(line)
        if heading is None:
            continue
        number = int(heading.group(1))
        if number < 1:
            # `Scene.number` 是 ge=1。构造出来会抛，而解析侧不许抛（原则 8）。
            continue
        directive = _find_directive(lines, index + 1)
        if directive is None:
            blocks.append(
                _Block(
                    number=number,
                    heading=index,
                    directive=None,
                    decl_text=line.strip(),
                    body_start=0,
                    body_end=0,
                )
            )
            continue
        d_index, match = directive
        blocks.append(
            _Block(
                number=number,
                heading=index,
                directive=d_index,
                decl_text=match.group(1),
                body_start=match.start(2),
                body_end=match.end(2),
            )
        )
    return blocks


def _find_directive(lines: Sequence[str], start: int) -> tuple[int, re.Match[str]] | None:
    """标题之后**第一个非空行**若是指令行就是它，否则这一场没有指令行。

    允许中间隔空行（写回侧喂物理行，作者在标题和指令之间空一行是常态；解析侧喂
    paragraphs，那里根本没有空行）。**不允许隔正文**：三段正文之后的那个指令行属于
    下一场，认了就是把 A 场的 cast 安到 B 场头上。
    """
    for index in range(start, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        match = _DIRECTIVE_RE.match(line)
        return (index, match) if match else None
    return None


def _scene_of(lines: Sequence[str], block: _Block) -> Scene:
    if block.directive is None:
        # 没有指令行时锚在标题行：R4 报出来的位置是作者能看见、能改的那一行。
        return Scene(number=block.number, para_index=block.heading, decl_text=block.decl_text)
    line = lines[block.directive]
    by_key = _by_key(_tokenize(line, block.body_start, block.body_end))
    return Scene(
        number=block.number,
        cast=_split_cast(_unique_value(by_key, "cast")),
        loc=_unique_value(by_key, "loc"),
        goal=_unique_value(by_key, "goal"),
        para_index=block.directive,
        decl_text=block.decl_text,
    )


# ══════════════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════════════


def parse_scenes(paragraphs: Sequence[str]) -> list[Scene]:
    """把一章的段落解析成场景块声明。**永不抛异常。**

    Args:
        paragraphs: 本章正文，按段落切开——即 `CheckContext.paragraphs` 那个序列。
            `Scene.para_index` 是它的 0-based 下标。

    Returns:
        按出现顺序。**同号的场景不合并、不重编号**：`## 场景 3` 出现两次就产两个
        `number=3` 的 Scene，那是作者的笔误，替他改掉他就永远看不见它。

    Notes:
        **本函数不收裸文本，只收段落。** 收裸文本就得在这里再定义一次「什么是一段」，
        而那是 `chapterize.py` 的事——`para_index` 的两份定义会让 R4 的 Issue 锚
        指向别的段落，且是那种「偶尔差一两段」的形态（同 ADR 0006 对 offset 的判词）。
        写回侧不受这条约束：它按物理行工作，行号一个都不出参。
    """
    return [_scene_of(paragraphs, block) for block in _scan(paragraphs)]


# ══════════════════════════════════════════════════════════════════════════
# 写回
# ══════════════════════════════════════════════════════════════════════════


def write_scene_directive(
    text: str,
    number: int,
    *,
    cast: Sequence[str] | None | _Keep = KEEP,
    loc: str | None | _Keep = KEEP,
    goal: str | None | _Keep = KEEP,
) -> str:
    """把面板上点选的 cast / loc / goal 写进这一章的场景块。

    Args:
        text: 章节文件的**全文**（不是段落序列——要保住换行才谈得上无损）。
        number: `## 场景 N` 的 N。
        cast: 称呼原文的列表。`KEEP` = 不动；`None` 或 `[]` = 删掉 `cast=`。
        loc: `KEEP` = 不动；`None` 或 `""` = 删掉 `loc=`。
        goal: 同 `loc`。

    Returns:
        新全文。**三个都是 `KEEP` 时返回的是 `text` 本身逐字节的重建**——
        `splitlines(keepends=True)` + `"".join()` 对任何字符串都是恒等。

    Raises:
        SceneNotFound: 没有这个场景号。
        AmbiguousScene: 有两个这个场景号。
        MalformedDirective: 要写的 key 在指令行里有两份。
        UnwritableValue: 值里有本格式表达不了的字符（回读比对发现）。

    Notes:
        **它只动它拥有的那几个字符。** 缩进 / 行尾（`\\r\\n`、无尾换行）/ 未知 key /
        已有 key 的顺序 / `<!--` 里的多余空格全部原样保留——正文是作者的文件
        （ADR 0007「稿子活在磁盘上」），他用 VSCode 写、用 git 管。系统顺手规范化了
        它不拥有的东西，代价是每次面板点一下就在他的 diff 里多出一堆噪声。
    """
    intent = _intent(cast, loc, goal)
    lines = text.splitlines(keepends=True)
    views = [line.rstrip("\r\n") for line in lines]
    block = _pick(_scan(views), number)

    if not intent:
        return "".join(lines)

    literals = {key: _literal(value) for key, value in intent.items()}
    before = _scene_of(views, block)
    if block.directive is None:
        new_lines = _insert_directive(lines, views, block, literals)
    else:
        new_lines = _rewrite_directive(lines, views, block, literals)
    if new_lines is None:
        return "".join(lines)

    result = "".join(new_lines)
    _verify(result, number, before, intent)
    return result


def _intent(
    cast: Sequence[str] | None | _Keep,
    loc: str | None | _Keep,
    goal: str | None | _Keep,
) -> dict[str, list[str] | str | None]:
    """把三个入参归一成 `{key: 作者要的值}`，`None` = 删。`KEEP` 的 key 不进来。

    **归一化在这里做一次，`_verify` 拿的就是这份。** 拿字面量再切一次回来当期望值
    是同义反复：`cast=["萧,决"]` 序列化成 `萧,决`、切回来是 `["萧","决"]`，
    两边都错得一模一样，回读比对就永远绿——它验的是「格式自洽」，不是「作者要的」。

    空值（`[]` / `""`）归一成删除而不是 `cast=`：两者解析回来都是「没写」，
    留一个空 key 只是在文件里放一块看不出意图的垃圾。
    """
    out: dict[str, list[str] | str | None] = {}
    if not isinstance(cast, _Keep):
        out["cast"] = _clean(cast or ())
    if not isinstance(loc, _Keep):
        out["loc"] = (loc or "").strip(_INLINE_SPACE) or None
    if not isinstance(goal, _Keep):
        out["goal"] = (goal or "").strip(_INLINE_SPACE) or None
    return out


def _literal(value: list[str] | str | None) -> str | None:
    if isinstance(value, list):
        return ",".join(value) or None
    return value or None


def _pick(blocks: Sequence[_Block], number: int) -> _Block:
    found = [b for b in blocks if b.number == number]
    if not found:
        raise SceneNotFound(
            f"这一章里没有「## 场景 {number}」。"
            f"现有的场景号：{sorted({b.number for b in blocks}) or '一个都没有'}"
        )
    if len(found) > 1:
        raise AmbiguousScene(
            f"这一章里有 {len(found)} 个「## 场景 {number}」（第 "
            f"{[b.heading + 1 for b in found]} 行）。写哪一个都是猜——请先改掉重号。"
        )
    return found[0]


def _rewrite_directive(
    lines: Sequence[str],
    views: Sequence[str],
    block: _Block,
    literals: dict[str, str | None],
) -> list[str] | None:
    assert block.directive is not None
    line = views[block.directive]
    by_key = _by_key(_tokenize(line, block.body_start, block.body_end))

    edits: list[tuple[int, int, str]] = []
    appended: list[str] = []
    for key in _CANONICAL_KEYS:
        if key not in literals:
            continue
        value = literals[key]
        found = by_key.get(key, ())
        if len(found) > 1:
            raise MalformedDirective(
                f"场景 {block.number} 的指令行里有 {len(found)} 个「{key}=」"
                f"（第 {block.directive + 1} 行）。改哪一个都是猜——请先删掉多余的那个。"
            )
        if found:
            field = found[0]
            if value is None:
                edits.append((field.sep_start, field.end, ""))
            elif value != field.value:
                edits.append((field.value_start, field.end, value))
        elif value is not None:
            appended.append(f"{key}={value}")

    if appended:
        # 插在内容末尾（`-->` 前的空白之前），这样 `<!-- nh: -->` 补出来仍是 `<!-- nh: x=1 -->`。
        pos = block.body_start + len(line[block.body_start : block.body_end].rstrip(_INLINE_SPACE))
        edits.append((pos, pos, "".join(f" {item}" for item in appended)))
    if not edits:
        return None

    new_lines = list(lines)
    terminator = lines[block.directive][len(line) :]
    new_lines[block.directive] = _apply(line, edits) + terminator
    return new_lines


def _apply(line: str, edits: Sequence[tuple[int, int, str]]) -> str:
    # 从右往左：左边的替换会平移右边的 offset，反过来做就得一路补偏移量。
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        line = line[:start] + replacement + line[end:]
    return line


def _insert_directive(
    lines: Sequence[str],
    views: Sequence[str],
    block: _Block,
    literals: dict[str, str | None],
) -> list[str] | None:
    items = [f"{key}={literals[key]}" for key in _CANONICAL_KEYS if literals.get(key) is not None]
    if not items:
        return None

    view = views[block.heading]
    heading = lines[block.heading]
    terminator = heading[len(view) :]
    new_lines = list(lines)
    if not terminator:
        # 标题是最后一行且没有尾换行：不补的话指令行会拼到标题屁股后面。
        new_lines[block.heading] = heading + "\n"
    indent = view[: len(view) - len(view.lstrip(_INLINE_SPACE))]
    new_lines.insert(block.heading + 1, f"{indent}<!-- nh: {' '.join(items)} -->{terminator}")
    return new_lines


def _verify(
    result: str, number: int, before: Scene, intent: dict[str, list[str] | str | None]
) -> None:
    """写完回读，比对**作者要的值**（不是比对我自己刚写下去的字面量）。

    转义漏洞靠这里发现，不靠穷举非法字符：穷举一定会漏（`,` `，` `、` `-->` `x=` `\\n`…），
    而漏掉的那一个的表现形态是「作者的 cast 少了一个人 / 多了一个人」——正是
    `constraints.py` 论证过的那条泄漏路径，只不过起点从 resolve 挪到了文件里。

    **`before` 是为没被写的那两个 key 准备的**：写 cast 不许顺手改掉 loc。
    """
    views = result.splitlines()
    found = [b for b in _scan(views) if b.number == number]
    after = _scene_of(views, found[0]) if len(found) == 1 else None

    expected_cast = intent.get("cast", before.cast)
    expected_loc = intent.get("loc", before.loc)
    expected_goal = intent.get("goal", before.goal)

    if (
        after is None
        or after.cast != expected_cast
        or after.loc != expected_loc
        or after.goal != expected_goal
    ):
        raise UnwritableValue(
            f"写回场景 {number} 之后回读对不上：期望 "
            f"cast={expected_cast} loc={expected_loc!r} goal={expected_goal!r}，"
            f"实得 {after if after is None else (after.cast, after.loc, after.goal)}。"
            "值里大概有分隔符（, ， 、）或 `-->`——这些字符本格式表达不了，请换个写法。"
        )
