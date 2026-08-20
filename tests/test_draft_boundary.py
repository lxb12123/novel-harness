"""第 4 道 arch-guard —— 起草层与判分层之间那堵墙（`docs/EVAL_PROTOCOL.md` §2 / §3）。

`eval/leak.py` 和 `panel/constraints.py` 的 docstring 曾经宣称「由第 4 道 arch-guard 钉死」，
而这个文件**当时并不存在**（`git log --all -- tests/test_draft_boundary.py` 是空的）。
那是这个仓库自己反复警告的那种坏法：**一个不存在的守卫比一个永远绿的守卫更糟，它还提供安全感。**
这份文件就是把那句话兑现。

── 这堵墙有两面，两面都成立 kill-gate 才有意义 ──────────────────────────

**一、判分器不许自建禁忌集（`eval/`）。** EVAL_PROTOCOL §3 原话：
「禁忌集只经 `panel.constraints`……`eval/leak.py` **不许**自己另走一趟 `store.resolve()`。」

坏掉的样子：判分器算出一份和产品闸门（`panel/constraints`）**不同**的禁忌集，于是 gate 测的
不是产品真会执行的那套约束。`checks/base.py` 那条「判分器 == Validator，同一份代码」名存实亡，
跑出来的 p 值不说明任何关于产品的事。

**二、Writer 只拿标签，永不拿 tell（`draft/`）。** EVAL_PROTOCOL §2 原话：
「prompt 里出现的是 `血脉秘密`，永远不是它的 tell `玄血蛊`，永远不碰 `Node.props`。于是
『检测器命中的 tell』与『prompt 里出现的标签』两个集合天然不相交——不会自己命中自己（echo-FP）。」

**第二条才是这道守卫存在的主要理由**，因为它坏起来不是「少了个功能」，是**得出相反的结论**：
`draft/assemble.py` 只要顺手调一次 `secret_surfaces()` 把 tell 一并写进 X1/X2 的 prompt，
那两臂就会 100% 命中自己写进去的词，`Δ = leak(X0) − leak(X1)` 翻成负数，裁决表照着读出
**「KILL 起草线」**——把一个本来对的项目砍掉，而全程没有任何东西会红、没有任何断言会失败。
预注册保证了「不能事后挪及格线」，但它保证不了「仪器没接反」。这道守卫管的是后者。

── 规则表（每一格都有一个独立的失败故事）────────────────────────────────

| 符号                    | `draft/` | `eval/` | 为什么 |
|-------------------------|----------|---------|--------|
| `secret_surfaces`       |    ❌    |   ✅    | tell 是判分器那一侧的东西。进了 prompt 就是 echo-FP（上面那条） |
| `knowledge_matrix`      |    ✅    |   ❌    | X1 要注入矩阵要点；但判分器自建「谁知道什么」= 判分器 ≠ Validator |
| `.props`                |    ❌    |   ❌    | tell 住在 `Node.props`。§2 的原话是「永远不碰」 |
| `store.resolve(...)`    |    ❌    |   ❌    | 约束集只有一个来源：`panel/constraints`。自己 resolve = 第二份禁忌集 |
| `graph.queries` 直接 import | ❌   |   ❌    | 时态过滤只在 `queries.py` 实现一次（第 2 道守卫的同一条道理） |

两侧**故意不对称**：`draft/` 能看认知矩阵但看不到 tell，`eval/` 能看 tell 但不能自己算矩阵。
不对称正是「两个集合天然不相交」的机械保证——对称地都放开或都禁掉，这条性质就没了。

── 它拦不住什么（诚实说明，同 `test_arch_guard.py` 的自述）────────────────

- `getattr(node, "props")`、`f = secret_surfaces; f(...)`、字符串拼出来的属性名——扫不到。
  没有哪道 AST 守卫是完备的；它拦住的是**顺手写出来的那一种**，而那一种正是会真的发生的那一种。
- **它拦不住「完整 PLANNED 进 Writer prompt」**（CLAUDE.md 头号错误第 5 条）。那要读懂 prompt
  文本的含义 = 语义判断 = ADR 0005 禁止的东西。那条只有 review 和 ADR 0010 守得住。
- **它只扫 `draft/` 和 `eval/` 两个目录。** runner 若写在别处（比如顶层 `gate.py` 或顶层 `synth/`），
  这堵墙就绕过去了。所以 `WRITER_DIRS` / `SCORER_DIRS` **必须跟着 runner 落在哪一起更新**——
  下面 `test_the_scanned_dirs_are_the_ones_that_matter` 会在目录消失时红，但它没法知道
  你又在第三个地方新建了一个 runner。

  **`agent/`（模式二的工具表）2026-08-10 就是那第三个地方**，它也在拼要进 prompt 的东西。
  它**没有**被折进 `WRITER_DIRS`，而是在 `tests/test_agent_tools.py` 里复用本文件的
  `banned_symbols` / `props_reads` 单独扫（**不抄第二份扫描器**）。分开的理由是
  `raw_resolve_calls`：`draft/` 里裸调 `store.resolve(...)` 是绕过闸门，
  而 `agent/` 合法地要把作者打字说的称呼解析成节点——折进来会假红，
  而一道会假红的守卫的下场是被人加豁免，然后豁免被拓宽。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

WRITER_DIRS = frozenset({"draft"})
"""起草层：把约束拼成 prompt、调模型。**它是「被判的那个」。**"""

SCORER_DIRS = frozenset({"eval"})
"""判分层：泄漏检测 + 统计。**它是「判分的那个」。**

runner（`runs/*.jsonl` 的产出者）已经落在这里，因此自动被本守卫罩住；若把它移到
顶层 `gate.py` 或顶层 `synth/` 就会绕过整堵墙。**`gate.py` 是今天真实存在的那个诱惑位**：
它是 gate 的入口、就在顶层、离 runner 只有一次 import（2026-08-20 删命令行面时它从
`eval/gate.py` 搬出来，正是因为边界守卫不许入口住在 `eval/` 里）。这不是风格问题：runner 是**同时**碰
两侧的唯一一段代码，它在墙外意味着墙的两面都可以被它一个人破掉。
"""

WRITER_BANNED = frozenset({"secret_surfaces", "resolve_cast"})
"""`draft/` 不许碰的符号。

`secret_surfaces` 是 tell 的唯一来源——见本文件开头那条「得出相反结论」的失败故事。

`resolve_cast` 一起禁掉，理由是**约束集只有一个入口**：`draft/context.py` 该走
`scene_constraints()` + `SceneConstraints.require_resolved_cast()`（ARCHITECTURE 对 context.py
的定义就是「薄封装」），而不是自己拼一套 cast 解析。放开它等于给「第二份约束推导」开门。
"""

SCORER_BANNED = frozenset({"knowledge_matrix"})
"""`eval/` 不许碰的符号。

判分器一旦自己算「谁在第几章知道什么」，它就有了一份独立于产品闸门的认知视图；
两份视图只要有一处不同，gate 测的就不是产品会执行的东西。判分器要的是**已经算好的**
`SceneConstraints`（`score_against` 就是这个形状），不是原料。
"""

PROPS = "props"
"""tell 住的地方。`Node.props` 是**两侧都不许碰**的唯一字段。

`draft/` 碰它 = tell 进 prompt = echo-FP；`eval/` 碰它 = 绕开 `secret_surfaces` 的
canonical 排除逻辑（那个排除正是为了不让检测器命中 prompt 自己写进去的显示名）。
两个方向都会把 gate 弄成自己命中自己。
"""


def _import_targets(node: ast.ImportFrom | ast.Import) -> set[str]:
    if isinstance(node, ast.ImportFrom):
        return {a.name for a in node.names}
    return {a.name for a in node.names}


def banned_symbols(source: str, banned: frozenset[str], filename: str = "<probe>") -> list[int]:
    """返回引用了 `banned` 里任一符号的行号。

    三种形状都算，因为三种都拿得到那个函数：
    `from ..panel.constraints import secret_surfaces`（import 名）、
    `secret_surfaces(...)`（裸名调用）、`constraints.secret_surfaces(...)`（属性访问）。

    用 AST 不用 grep：`leak.py` 的模块 docstring 里就白纸黑字写着 `store.resolve` 和
    `knowledge_matrix`（它在解释自己为什么不调它们）——正则会把那段说明算成违规，
    而**守卫误报会被人关掉，关掉的守卫等于没有**。docstring 里的字不是属性访问，AST 天然看不见。
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

    这两层只该收**算好的 Pydantic 对象**。绕过 `StoryGraph` 直接进 `queries` 就是在
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
# 墙的两面
# ══════════════════════════════════════════════════════════════════════════


def test_the_writer_never_touches_a_tell() -> None:
    """**这条是这个文件存在的主要理由。**

    `draft/` 拿到 tell 的那一刻，X1/X2 的 prompt 里就会出现检测器要找的那个词，
    两臂 100% 自己命中自己，Δ 翻负，裁决表读出 KILL——一个把对的项目砍掉的结论，
    而且没有任何测试会红。
    """
    offenders = _scan(WRITER_DIRS, lambda s, f: banned_symbols(s, WRITER_BANNED, f))
    assert not offenders, (
        f"起草层碰了判分层的东西：{offenders}\n"
        f"`draft/` 不许引用 {sorted(WRITER_BANNED)}（EVAL_PROTOCOL §2「只有标签进 prompt」）。\n"
        "prompt 里该出现的是秘密的**显示名**（血脉秘密），永远不是它的 tell（玄血蛊）。\n"
        "一旦 tell 进了 prompt，注入约束的那两臂会命中自己写进去的词 → Δ 翻负 → "
        "裁决表读出「KILL 起草线」。**那是一个把对的项目砍掉的错误结论，而且全程没有东西会红。**\n"
        "要约束就收 `panel.constraints.scene_constraints()` 的 SceneConstraints（只含标签）。"
    )


def test_the_scorer_never_builds_its_own_view() -> None:
    """EVAL_PROTOCOL §3 点名要的那一条：判分器不许另立一份禁忌集。"""
    offenders = _scan(SCORER_DIRS, lambda s, f: banned_symbols(s, SCORER_BANNED, f))
    assert not offenders, (
        f"判分层自己算了认知视图：{offenders}\n"
        f"`eval/` 不许引用 {sorted(SCORER_BANNED)}（EVAL_PROTOCOL §3「禁忌集只经 panel.constraints」）。\n"
        "判分器一旦有了独立于产品闸门的「谁知道什么」，gate 测的就不是产品会执行的那套约束——"
        "`checks/base.py` 的「判分器 == Validator，同一份代码」名存实亡，p 值不说明任何事。\n"
        "判分器要的是**已经算好的** SceneConstraints（`score_against` 就是这个形状），不是原料。"
    )


def test_neither_side_touches_node_props() -> None:
    """tell 住在 `Node.props`。EVAL_PROTOCOL §2 的原话是「永远不碰 `Node.props`」。"""
    offenders = _scan(WRITER_DIRS | SCORER_DIRS, props_reads)
    assert not offenders, (
        f"这些文件读了 Node.props：{offenders}\n"
        "tell 住在 props 里，两侧都不许碰（EVAL_PROTOCOL §2）。\n"
        "`draft/` 碰它 = tell 进 prompt = echo-FP；`eval/` 碰它 = 绕开 `secret_surfaces` 的 "
        "canonical 排除（那个排除正是为了不让检测器命中 prompt 自己写进去的显示名）。\n"
        "两个方向都会把 gate 弄成自己命中自己。"
    )


def test_neither_side_resolves_on_its_own() -> None:
    """约束集只有一个来源。自己 `store.resolve()` 就是第二份禁忌集。"""
    offenders = _scan(WRITER_DIRS | SCORER_DIRS, raw_resolve_calls)
    assert not offenders, (
        f"这些文件自己调了 StoryGraph.resolve：{offenders}\n"
        "约束集**只有一个来源**：`panel/constraints.py`。自己 resolve 出来的花名册和闸门算的"
        "那一份只要有一处不同，gate 就在测一个产品不会执行的东西（EVAL_PROTOCOL §3）。\n"
        "（`Path(...).resolve()` 不受影响：判据是「有没有位置参数」。）"
    )


def test_neither_side_reaches_into_graph_internals() -> None:
    offenders = _scan(WRITER_DIRS | SCORER_DIRS, graph_internals_imports)
    assert not offenders, (
        f"这些文件直接 import 了 graph 的内部实现：{offenders}\n"
        "这两层只该收算好的 Pydantic 对象，读图走 StoryGraph 的五个方法。"
    )


def test_the_banned_sets_stay_put() -> None:
    """不是洁癖：这几个集合每少一个成员，上面就少一个失败故事被挡住。

    要删成员，先在 PR 里回答「删掉之后，本文件开头那条『Δ 翻负读出 KILL』的路径靠什么挡住」。
    两侧的**不对称**是刻意的——对称地放开或禁掉，「两个集合天然不相交」这条性质就没了。
    """
    assert WRITER_BANNED == frozenset({"secret_surfaces", "resolve_cast"})
    assert SCORER_BANNED == frozenset({"knowledge_matrix"})
    assert PROPS == "props"
    assert WRITER_DIRS == frozenset({"draft"})
    assert SCORER_DIRS == frozenset({"eval"})


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# 这道守卫最初落地时扫的两个目录几乎是空的；今天 `assemble.py`、`confound_lint.py`、runner
# 都已进入扫描面。无论目录大小，「五条全绿」仍不足以证明扫描器有效，所以继续用下面的
# 违规 fixture 证明它确实看得见每一种绕法。

ECHO_PROBE = '''
from __future__ import annotations
from ..panel.constraints import scene_constraints, secret_surfaces

def assemble(store, pid, chapter, cast) -> str:
    """把 tell 一并写进 prompt —— 看起来像「让模型知道得更全」，实际是把仪器接反了。"""
    c = scene_constraints(store, pid, chapter, cast)
    tells = secret_surfaces(store, pid, c.must_not_reveal)
    return "不要提到：" + "、".join(t for v in tells.values() for t in v)
'''

PROPS_PROBE = '''
from __future__ import annotations

def tell_of(node) -> str:
    return node.props["tell"]
'''

SELF_RESOLVE_PROBE = '''
from __future__ import annotations

def forbidden(store, pid: str, names: list[str]) -> set[str]:
    """第二份禁忌集：不 import 任何被禁的名字，只是自己走了一趟 resolve。"""
    return {r.node.name for r in store.resolve(pid, names)}
'''

MATRIX_PROBE = '''
from __future__ import annotations
from ..panel.knowledge import knowledge_matrix

def who_knows(store, pid, chapter, cast):
    return knowledge_matrix(store, pid, chapter, cast)
'''

ATTRIBUTE_PROBE = '''
from __future__ import annotations
from ..panel import constraints

def sneaky(store, pid, secrets):
    """不 import 那个名字，改走属性访问——同样拿得到 tell。"""
    return constraints.secret_surfaces(store, pid, secrets)
'''

PATHLIB_PROBE = '''
from __future__ import annotations
from pathlib import Path

HERE = Path(__file__).resolve().parent
def out(p: str) -> Path:
    return Path(p).resolve(strict=False)
'''

DOCSTRING_PROBE = '''
"""本模块不自己调 `store.resolve` / `knowledge_matrix`，也不碰 `node.props`。

见 EVAL_PROTOCOL §3：禁忌集只经 panel.constraints，`secret_surfaces` 是唯一入口。
"""
from __future__ import annotations
'''


def test_the_scanned_dirs_are_the_ones_that_matter() -> None:
    """扫描器要是把路径找错了（或者目录被搬走），上面五条会永远绿着通过。"""
    for d in WRITER_DIRS | SCORER_DIRS:
        assert (SRC / d).is_dir(), f"{d}/ 不在了——这道守卫正在扫一个空集合"
    assert (SRC / "draft" / "provider.py").exists()
    assert (SRC / "eval" / "leak.py").exists()


def test_the_guard_can_see_the_echo_bypass() -> None:
    """**这条是这个文件存在的理由。** tell 进 prompt = 两臂自己命中自己 = 假的 KILL。"""
    assert banned_symbols(ECHO_PROBE, WRITER_BANNED), "扫描器必须看见 draft/ 拿走了 secret_surfaces"
    # 同一段代码放在 eval/ 是**合法**的——leak.py 就是这么写的。不对称必须体现在扫描器里。
    assert banned_symbols(ECHO_PROBE, SCORER_BANNED) == []


def test_the_guard_can_see_the_attribute_form() -> None:
    """`from ..panel import constraints` + `constraints.secret_surfaces(...)` 绕不过去。"""
    assert banned_symbols(ATTRIBUTE_PROBE, WRITER_BANNED)


def test_the_guard_can_see_props_and_self_resolve_and_matrix() -> None:
    # 行号写死（不只是「非空」）：扫描器要是把行号算错，报错信息就指不到人，
    # 而一道指不到人的守卫在第一次红的时候会被当成噪声关掉。
    assert props_reads(PROPS_PROBE) == [5]
    assert raw_resolve_calls(SELF_RESOLVE_PROBE) == [6]
    assert banned_symbols(MATRIX_PROBE, SCORER_BANNED), "扫描器必须看见 eval/ 自己算了矩阵"
    # 反向也要成立：矩阵在 draft/ 是合法的（X1 就是要注入矩阵要点）。
    assert banned_symbols(MATRIX_PROBE, WRITER_BANNED) == []


def test_the_guard_does_not_cry_wolf() -> None:
    """误报会让人把守卫关掉，而关掉的守卫等于没有。三种合法写法必须是绿的。"""
    # ① pathlib 的 .resolve()：本仓库到处都在用，包括本文件第一行。
    assert raw_resolve_calls(PATHLIB_PROBE) == []
    # ② docstring 里解释「我不调这些」的那段字——leak.py 的模块 docstring 就是这样。
    assert banned_symbols(DOCSTRING_PROBE, WRITER_BANNED | SCORER_BANNED) == []
    assert props_reads(DOCSTRING_PROBE) == []
    assert raw_resolve_calls(DOCSTRING_PROBE) == []
    # ③ 真文件：leak.py 合法地用 secret_surfaces，且它的 docstring 里写满了被禁的名字。
    leak = (SRC / "eval" / "leak.py").read_text(encoding="utf-8")
    assert banned_symbols(leak, SCORER_BANNED, "eval/leak.py") == []
    assert props_reads(leak, "eval/leak.py") == []
    assert raw_resolve_calls(leak, "eval/leak.py") == []
    # 而同一份文件若被当成 draft/ 来扫，必须红——这证明不对称是真的在生效，不是巧合。
    assert banned_symbols(leak, WRITER_BANNED, "eval/leak.py")
