"""起草层的分层守卫 —— **墙的另一面 2026-08-24 拆了，这一面留着。**

── 它原来是什么 ────────────────────────────────────────────────────────

这个文件原来是「第 4 道 arch-guard」：起草层（`draft/`）与判分层（`eval/`）之间那堵墙
（`docs/EVAL_PROTOCOL.md` §2 / §3）。两面各一条主张：

1. **判分器不许自建禁忌集** —— 否则 gate 测的不是产品真会执行的那套约束；
2. **Writer 只拿标签，永不拿 tell** —— 否则注入约束的两臂会命中自己写进去的词，
   Δ 翻负，裁决表读出一个「把对的项目砍掉」的 KILL，而全程没有东西会红。

**秘密整套功能 2026-08-24 下线**（ADR 0039 / `docs/EVAL_PROTOCOL_RETIREMENT.md`）：
`eval/` 整个目录没了，tell 这个概念也没了。**上面那两条主张的对象都不存在了。**

── 留下的是什么，为什么留 ──────────────────────────────────────────────

三条**不依赖秘密**的分层纪律，它们说的是「起草层只收算好的东西，不自己去取原料」：

| 禁的 | 为什么 |
|---|---|
| `resolve_cast` | 约束集只有一个入口。自己拼一套 cast 解析 = 第二份约束推导 |
| `.props` | 节点属性里是**作者写的自由文本**。起草层碰它 = 绕开出参收窄，作者的东西直接穿过序列化 |
| `store.resolve(...)` | 同上：花名册解析只有一个来源 |
| `graph.queries` / `graph.sqlite_store` | 时态过滤只写一次。直接 import 进来 = 在它旁边开第二个入口 |

**这三条以前挂在 EVAL_PROTOCOL 上，现在挂在分层上。** 理由换了，判据一个字没动——
所以它们不是「留着备用」，是本来就有两个理由，只是当年只写了更响的那一个。

── 扫描器还有第二个用户 ────────────────────────────────────────────────

`tests/test_track_isolation.py` 从这里 import `banned_symbols`（「轨道不进 Writer 的
prompt」那道守卫）。**所以这个文件不能整删**，删了那道守卫会当场 ImportError。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

WRITER_DIRS = frozenset({"draft"})
"""起草层：把约束拼成 prompt、调模型。"""

WRITER_BANNED = frozenset({"resolve_cast"})
"""`draft/` 不许碰的符号。

**约束集只有一个入口**：`draft/context.py` 该走 `scene_constraints()` +
`SceneConstraints.require_resolved_cast()`（ARCHITECTURE 对 context.py 的定义就是
「薄封装」），而不是自己拼一套 cast 解析。放开它等于给「第二份约束推导」开门。

（`secret_surfaces` 曾经也在这儿——tell 的唯一来源。它随秘密下线一起没了。）
"""

PROPS = "props"
"""`Node.props` —— 起草层不许碰的那个字段。

它装的是**作者写的自由文本**（人物设定、备注）。起草层直接读它 = 绕开出参收窄，
作者的原话不经任何一层挑选就进 prompt。要人物资料该收算好的 `CharacterProfileView`
（`product_assemble._PROFILE_LABELS` 逐字段挑），不是整个 props 端过去。

（它以前的理由是「tell 住在 props 里」。tell 没了，**这一条的理由变成了上面那个**——
判据一个字没动。）
"""


def _import_targets(node: ast.ImportFrom | ast.Import) -> set[str]:
    if isinstance(node, ast.ImportFrom):
        return {a.name for a in node.names}
    return {a.name for a in node.names}


def banned_symbols(source: str, banned: frozenset[str], filename: str = "<probe>") -> list[int]:
    """返回引用了 `banned` 里任一符号的行号。

    三种形状都算，因为三种都拿得到那个函数：
    `from ..panel.constraints import resolve_cast`（import 名）、
    `resolve_cast(...)`（裸名调用）、`constraints.resolve_cast(...)`（属性访问）。

    用 AST 不用 grep：模块 docstring 里常常白纸黑字写着被禁的名字（在解释自己为什么
    不调它们）——正则会把那段说明算成违规，而**守卫误报会被人关掉，关掉的守卫等于没有**。
    docstring 里的字不是属性访问，AST 天然看不见。
    """
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            if _import_targets(node) & banned:
                lines.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr in banned:
                lines.append(node.lineno)
        elif isinstance(node, ast.Name):
            if node.id in banned:
                lines.append(node.lineno)
    return sorted(set(lines))


def props_reads(source: str, filename: str = "<probe>") -> list[int]:
    """返回读了 `.props` 的行号。**只认属性访问**，不认同名的局部变量。

    `props = {...}` 是个普通的字典，它不是 `Node.props`；把它算进来是纯误报。
    """
    tree = ast.parse(source, filename=filename)
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == PROPS
    ]
    return sorted(set(lines))


def raw_resolve_calls(source: str, filename: str = "<probe>") -> list[int]:
    """返回 `X.resolve(<至少一个位置参数>)` 的行号 —— 也就是 `StoryGraph.resolve`。

    **判据是「有没有位置参数」，不是名字**，因为 `Path(...).resolve()` 是这个仓库里到处都在用的
    东西（本文件第一行就有一个），把它算成违规会让守卫在第一次 import pathlib 时就变成噪声。
    `StoryGraph.resolve(project_id, surfaces=...)` 至少带一个位置参数 `project_id`；
    `Path.resolve()` 一个位置参数都没有（只有 `strict=` 关键字）。

    代价说清楚：`store.resolve()` 这种零参写法扫不到——但它在 `StoryGraph` 上是 TypeError，
    写出来跑一次就红了，不是一个能静默存活的绕法。
    """
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "resolve" and node.args:
            lines.append(node.lineno)
    return sorted(set(lines))


def graph_internals_imports(source: str, filename: str = "<probe>") -> list[int]:
    """返回直接 import `graph.queries` / `graph.sqlite_store` 的行号。

    这一层只该收**算好的 Pydantic 对象**。绕过 `StoryGraph` 直接进 `queries` 就是在
    时态过滤旁边开第二个入口——第 2 道守卫（`test_graph_tables_stay_inside_graph`）拦的是
    「在 graph/ 外写图表 SQL」，拦不住「import 进来直接调 graph/ 里的私有查询」。
    """
    banned_tails = ("graph.queries", "graph.sqlite_store")
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if any(mod == t or mod.endswith("." + t) for t in banned_tails):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(a.name.endswith(t) for a in node.names for t in banned_tails):
                lines.append(node.lineno)
    return sorted(set(lines))


def _scan(dirs: frozenset[str], check) -> list[str]:
    offenders: list[str] = []
    for d in sorted(dirs):
        for path in sorted((SRC / d).rglob("*.py")):
            rel = path.relative_to(SRC)
            for lineno in check(path.read_text(encoding="utf-8"), str(rel)):
                offenders.append(f"{rel}:{lineno}")
    return offenders


# ══════════════════════════════════════════════════════════════════════════
# 起草层只收算好的东西，不自己去取原料
# ══════════════════════════════════════════════════════════════════════════


def test_the_writer_never_resolves_cast_on_its_own() -> None:
    """约束集只有一个入口。"""
    offenders = _scan(WRITER_DIRS, lambda s, f: banned_symbols(s, WRITER_BANNED, f))
    assert not offenders, (
        f"起草层自己拼了一套 cast 解析：{offenders}\n"
        f"`draft/` 不许引用 {sorted(WRITER_BANNED)}。\n"
        "要约束就收 `panel.constraints.scene_constraints()` 算好的那一份——"
        "自己解析出来的花名册和闸门算的那一份只要有一处不同，产品就在按两套约束写。"
    )


def test_the_writer_never_touches_node_props() -> None:
    """`Node.props` 里是作者写的自由文本，起草层不许整个端过去。"""
    offenders = _scan(WRITER_DIRS, props_reads)
    assert not offenders, (
        f"起草层读了 Node.props：{offenders}\n"
        "props 里是**作者写的自由文本**，直接读它等于绕开出参收窄。\n"
        "要人物资料就收算好的 `CharacterProfileView`（逐字段挑），别端整个 props。"
    )


def test_the_writer_never_resolves_on_its_own() -> None:
    """花名册解析只有一个来源。自己 `store.resolve()` 就是第二份。"""
    offenders = _scan(WRITER_DIRS, raw_resolve_calls)
    assert not offenders, (
        f"起草层自己调了 StoryGraph.resolve：{offenders}\n"
        "解析**只有一个来源**：`panel/constraints.py`。\n"
        "（`Path(...).resolve()` 不受影响：判据是「有没有位置参数」。）"
    )


def test_the_writer_never_reaches_into_graph_internals() -> None:
    offenders = _scan(WRITER_DIRS, graph_internals_imports)
    assert not offenders, (
        f"起草层直接 import 了 graph 的内部实现：{offenders}\n"
        "这一层只该收算好的 Pydantic 对象，读图走 StoryGraph 的那几个方法。"
    )


def test_the_banned_sets_stay_put() -> None:
    """不是洁癖：这几个集合每少一个成员，上面就少一条纪律被挡住。

    要删成员，先在 PR 里回答「删掉之后，第二份约束推导靠什么挡住」。

    （这条断言 2026-08-24 缩过一次：`WRITER_BANNED` 掉了 `secret_surfaces`，
    `SCORER_*` 整组没了 —— 那是秘密下线，不是有人放宽了纪律。）
    """
    assert WRITER_BANNED == frozenset({"resolve_cast"})
    assert PROPS == "props"
    assert WRITER_DIRS == frozenset({"draft"})


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# 「全绿」永远不足以证明扫描器有效——它也可能在扫一个空集合、或者根本没看见那种绕法。
# 下面用违规 fixture 证明它确实看得见每一种写法。

PROPS_PROBE = '''
from __future__ import annotations

def notes_of(node) -> str:
    return node.props["character_notes"]
'''

SELF_RESOLVE_PROBE = '''
from __future__ import annotations

def roster(store, pid: str, names: list[str]) -> set[str]:
    """第二份花名册：不 import 任何被禁的名字，只是自己走了一趟 resolve。"""
    return {r.node.name for r in store.resolve(pid, names)}
'''

CAST_PROBE = '''
from __future__ import annotations
from ..panel.constraints import resolve_cast

def who(store, pid, cast):
    return resolve_cast(store, pid, cast)
'''

ATTRIBUTE_PROBE = '''
from __future__ import annotations
from ..panel import constraints

def sneaky(store, pid, cast):
    """不 import 那个名字，改走属性访问——同样拿得到它。"""
    return constraints.resolve_cast(store, pid, cast)
'''

INTERNALS_PROBE = '''
from __future__ import annotations
from ..graph.queries import knows_at

def peek(conn, pid):
    return knows_at(conn, pid)
'''

PATHLIB_PROBE = '''
from __future__ import annotations
from pathlib import Path

HERE = Path(__file__).resolve().parent
def out(p: str) -> Path:
    return Path(p).resolve(strict=False)
'''

DOCSTRING_PROBE = '''
"""本模块不自己调 `store.resolve` / `resolve_cast`，也不碰 `node.props`。

约束集只经 panel.constraints，这一层只收算好的对象。
"""
from __future__ import annotations
'''


def test_the_scanned_dir_is_the_one_that_matters() -> None:
    """扫描器要是把路径找错了（或者目录被搬走），上面几条会永远绿着通过。"""
    for d in WRITER_DIRS:
        assert (SRC / d).is_dir(), f"{d}/ 不在了——这道守卫正在扫一个空集合"
    assert (SRC / "draft" / "provider.py").exists()


def test_the_guard_can_see_the_import_form() -> None:
    assert banned_symbols(CAST_PROBE, WRITER_BANNED), "扫描器必须看见 draft/ 拿走了 resolve_cast"


def test_the_guard_can_see_the_attribute_form() -> None:
    """`from ..panel import constraints` + `constraints.resolve_cast(...)` 绕不过去。"""
    assert banned_symbols(ATTRIBUTE_PROBE, WRITER_BANNED)


def test_the_guard_can_see_props_and_self_resolve_and_internals() -> None:
    # 行号写死（不只是「非空」）：扫描器要是把行号算错，报错信息就指不到人，
    # 而一道指不到人的守卫在第一次红的时候会被当成噪声关掉。
    assert props_reads(PROPS_PROBE) == [5]
    assert raw_resolve_calls(SELF_RESOLVE_PROBE) == [6]
    assert graph_internals_imports(INTERNALS_PROBE) == [3]


def test_the_guard_does_not_cry_wolf() -> None:
    """误报会让人把守卫关掉，而关掉的守卫等于没有。三种合法写法必须是绿的。"""
    # ① pathlib 的 .resolve()：本仓库到处都在用，包括本文件第一行。
    assert raw_resolve_calls(PATHLIB_PROBE) == []
    # ② docstring 里解释「我不调这些」的那段字。
    assert banned_symbols(DOCSTRING_PROBE, WRITER_BANNED) == []
    assert props_reads(DOCSTRING_PROBE) == []
    assert raw_resolve_calls(DOCSTRING_PROBE) == []
    # ③ 真文件：`panel/constraints.py` 定义并大量谈论这些名字，而它不在扫描面里。
    #    它被当成 draft/ 来扫时必须红——这证明「扫描面」这一格是真的在生效。
    constraints = (SRC / "panel" / "constraints.py").read_text(encoding="utf-8")
    assert banned_symbols(constraints, WRITER_BANNED, "panel/constraints.py")
