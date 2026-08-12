"""约束 10 的守卫：**作者永不填章号，一次都不行**（ARCHITECTURE §10 / PLAN §5.9）。

> 问作者「这条关系从第几章开始有效」，他不记得（200 万字写了三年，他连主角哪章突破
> 金丹都要翻）。他会填 1 → 时态模型退化成当前值快照图 → 这个项目全部差异化的地基没了。
> **表单里有章号输入框 = 邀请污染。**

修法是：`valid_from` 只由证据决定。作者确认的是「这条事实在这段原文里出现过」——他看着
原文点，零记忆负担——系统自己反推出那是第几章。血统整条是：

    edge.valid_from_chapter ← evidence.chapter_number ← chapter.number
      ← ChapterSpec.number ← importer ← chapterize 的 index ← 文本顺序

**这条约束在今天之前一条自动化断言都没有**，它只活在几个 docstring 里。而它是那种
「加一个旗标就能让用户少被拒一次」的约束——那个下午一定会来，来的时候没有任何东西会
拦住它，因为填错章号的产物是一条 `valid_from` 错了的 CANON 边，**而它在面板上长得完全
正常**：没有任何一条规则、任何一个面板分区、任何一次 review 会发现它。

── 为什么是一个新文件，不塞进 `test_arch_guard.py` ────────────────────────

那份守卫治的是「时态过滤写第二遍」，判据是「谁在碰图表」。约束 10 是另一个问题、另一条
判据（「作者的输入能不能到达 valid_from」）。塞进去会把一份判据清晰的守卫变成杂物间，
而那个文件自己的 docstring 把代价写得很清楚：**误报会让人把守卫关掉，而关掉的守卫等于
没有守卫。** 两条判据混在一个文件里，关掉其中一条的最省事办法就是关掉整个文件。

── 它拦不住什么（诚实说明）──────────────────────────────────────────────

- 它扫的是**命令面**（typer 的参数名和类型）和 `declare.py` 的**一处 AST**。一个把章号
  藏在 `--quote "第88章:他终于明白…"` 里解析出来的实现，这里一条都扫不到。没有哪道
  守卫是完备的——它拦住的是**顺手写出来的那一种**，而那一种正是会真的发生的那一种。
- PLANNED 边（`nh declare foreshadow`）的 `valid_from` **确实是作者填的**（001_init.sql
  逐字写了「那不是回忆是决定」）。M1 没有那条命令，所以这里零 carve-out。加它的那天，
  这份守卫需要的不是一个例外——**例外会被拓宽**——而是一条把 PLANNED 的写路径与
  `Ledger` 彻底分开的判据。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer
from typer.main import get_command

from novel_harness.cli import app

DECLARE_PY = Path(__file__).resolve().parents[1] / "src" / "novel_harness" / "declare.py"

BANNED = re.compile(r"chapter|valid_from|valid_to|since|^ch$|^at$", re.IGNORECASE)
"""参数名里撞上它 = 一个章号输入框。

`^ch$` / `^at$` 用锚而不是裸词：`ch` 和 `at` 作为**整个参数名**是缩写的章号和 AS OF，
但作为子串它们在 `search` / `match` / `path` 里无处不在——不加锚这道守卫会对着
`nh import <path>` 开火，然后被关掉。
"""

DECLARATION_COMMANDS = ("declare", "init", "import", "sync", "locate")
"""**声明面**：作者往库里写东西的每一条命令，加上 `locate`（它是 declare 的预演，
一个能在这里挑章的旗标等于在 declare 那边挑）。

`panel` / `check` / `version` **故意不在这里**——见 `test_panel_and_check_still_take_chapter`。
"""


# ══════════════════════════════════════════════════════════════════════════
# 扫描器
# ══════════════════════════════════════════════════════════════════════════


INT_TYPE_NAMES = frozenset({"int", "integer"})
"""`ParamType.name` 对整数的两种叫法。

**判据是 `.name` 而不是 `isinstance(param.type, click.types.IntParamType)`**，理由见
`walk` ——这里没有一个能拿来 isinstance 的 click。两个名字都收：typer 的 vendored click
叫 `int`，上游 click 叫 `integer`，而这份守卫不该押注于今天装的是哪一个。
"""


def _subcommands(cmd: Any) -> dict[str, Any]:
    """一个命令的子命令。Group 有 `.commands`，叶子命令没有。

    **鸭子类型，不 isinstance。** `typer` vendored 了它自己的 click
    （`typer._click.core.Command`），而 `click` 根本不在 pyproject 的依赖里——
    `isinstance(cmd, click.Group)` 对着一个碰巧装上的、**另一个** click 的类去比，
    结果恒为 False，于是遍历只走到根、三条守卫全部安静地全绿。

    **这不是假想的：本文件第一版就是那么写的，`test_the_guard_can_see_a_chapter_flag`
    当场把它逮住了。** 这段注释是那次的实测记录，别把它改回 isinstance。
    """
    commands = getattr(cmd, "commands", None)
    return commands if isinstance(commands, dict) else {}


def walk(cmd: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """递归命令树。`declare` 是一个 Group，它的子命令才是声明面本身。"""
    yield path, cmd
    for name, sub in _subcommands(cmd).items():
        yield from walk(sub, f"{path} {name}".strip())


def _opt_names(param: Any) -> list[str]:
    """一个参数的全部长选项名，归一成 python 标识符的形状（`--valid-from` → `valid_from`）。

    只扫 `param.name` 不够：typer 里参数名和长选项名是**两个东西**（`believed:
    str = Option(..., "--as")`），而作者看见的、也是那个下午会被加上去的，是后者。
    """
    return [
        opt.lstrip("-").replace("-", "_")
        for opt in [*param.opts, *param.secondary_opts]
        if opt.startswith("--")
    ]


def chapter_inputs(cmd: Any, path: str, *, ban_ints: bool) -> list[str]:
    """这条命令上的章号输入框。返回人读得懂的违规描述，空 = 干净。

    `ban_ints` 只对 `declare` 的子命令为真：**声明面上作者没有任何一个正当的数字要敲**，
    所以那里的 int 型参数不管叫什么名字都是可疑的（`--nth` / `--n` / `--k` 绕得过 BANNED）。
    `import` / `sync` 不设这一条：它们某天可能正当地长出 `--workers 4`，而一道会对
    `--workers` 开火的守卫就是一道会被关掉的守卫。
    """
    out: list[str] = []
    for param in cmd.params:
        names = [param.name or "", *_opt_names(param)]
        for name in names:
            if name and BANNED.search(name):
                out.append(f"nh {path} 的参数「{name}」撞了章号（§5.9 / 约束 10）")
                break
        if ban_ints and getattr(param.type, "name", "") in INT_TYPE_NAMES:
            out.append(f"nh {path} 的参数「{param.name}」是 int —— 声明面上作者不敲数字")
    return out


def scan(root: Any) -> list[str]:
    """整棵命令树里的全部章号输入框（只看声明面）。"""
    out: list[str] = []
    for path, cmd in walk(root):
        if _subcommands(cmd):
            continue  # Group 自己没有声明参数，它的子命令才是面
        head = path.split(" ")[0] if path else ""
        if head not in DECLARATION_COMMANDS:
            continue
        out += chapter_inputs(cmd, path, ban_ints=head == "declare")
    return out


def valid_from_assignments(source: str, filename: str = "<probe>") -> list[tuple[int, str, str]]:
    """`valid_from_chapter=` 这个**关键字实参**的每一次出现：`(行号, 值的 AST 类型, 值的源码)`。

    扫关键字实参而不是 grep：`EdgeSpec` 是 `extra="forbid"` 的 Pydantic 模型，
    给 `valid_from_chapter` 赋值的唯一形状就是一个关键字实参。docstring 和注释里
    引着它的地方（`declare.py` 有好几处）不是赋值，AST 天然不会把它们算进来。
    """
    tree = ast.parse(source, filename=filename)
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "valid_from_chapter":
            out.append((node.lineno, type(node.value).__name__, ast.unparse(node.value)))
    return sorted(out)


# ══════════════════════════════════════════════════════════════════════════
# 三道守卫
# ══════════════════════════════════════════════════════════════════════════


def test_declare_surface_has_no_chapter_input() -> None:
    """`nh declare *` / `init` / `import` / `sync` / `locate` 上没有一个章号输入框。

    §5.9：他不记得 → 他会填 1 → 时态模型退化成快照图 → 项目全部差异化的地基没了。
    **表单里有章号输入框 = 邀请污染。**
    """
    offenders = scan(get_command(app))
    assert not offenders, (
        "声明面上出现了章号输入框：\n  " + "\n  ".join(offenders) + "\n"
        "章号是「这句引语落在哪一章」的**产物**，不是作者的输入（PLAN §5.9 / 约束 10）。\n"
        "作者不记得第几章（200 万字写了三年），他会填 1，而填错的产物是一条 valid_from\n"
        "错了的 CANON 边——它在面板上长得完全正常，没有任何一条规则会发现它。\n"
        "要让作者指定一处，让他把**引语**加长到只匹配一处。"
    )


def test_panel_and_check_still_take_chapter() -> None:
    """**反向断言：`nh panel --chapter` / `nh check --chapter` 必须还在。**

    它们是查询参数（AS OF 第几章 / 这份稿子是第几章），不写进任何一行数据。

    这一条不是凑数：一道**把它们也拦掉**的守卫会逼着下一个人去关掉整个文件，而
    关掉的守卫等于没有守卫（`test_arch_guard.py` 的原话）。把合法的那一面也钉住，
    这份守卫才有资格一直开着。
    """
    commands = {path: cmd for path, cmd in walk(get_command(app))}
    for name in ("panel", "check"):
        opts = {opt for param in commands[name].params for opt in param.opts}
        assert "--chapter" in opts, f"nh {name} 必须有 --chapter：它是 AS OF，是查询不是声明"
        assert "-c" in opts, f"nh {name} 的 -c 是 README 和 demo.sh 里印着的，不许改"


def test_valid_from_is_assigned_exactly_once_in_declare() -> None:
    """`declare.py` 里 `valid_from_chapter=` **只许出现一次**，且值必须是 `ev.chapter_number`。

    第二次赋值一定意味着有第二条路给它填数——那正是「退化成快照图」的形态。而值必须是
    一个 `ast.Attribute`（从证据上取的属性）而不是常量或参数名：`valid_from_chapter=1`
    和 `valid_from_chapter=chapter` 都能编译、都能跑、都能在面板上画得很正常。

    与 `queries.TEMPORAL_WHERE` 只写一次是同一个手法：**把「只有一处」从一件 review
    事项变成一件 CI 事项。** review 会累，CI 不会。
    """
    found = valid_from_assignments(DECLARE_PY.read_text(encoding="utf-8"), "declare.py")

    assert len(found) == 1, (
        f"declare.py 里给 valid_from_chapter 赋值了 {len(found)} 次：{found}。\n"
        "它只许有一处（§5.9 / 约束 10）——第二处一定是第二条让章号进来的路。"
    )
    lineno, kind, source = found[0]
    assert kind == "Attribute", (
        f"declare.py:{lineno} 把 valid_from_chapter 赋成了 {kind}（`{source}`）。\n"
        "它只能是证据上的一个属性（`ev.chapter_number`）：常量和参数名都能跑、都能在\n"
        "面板上画得很正常，而它们的产物是一条时间错了的 CANON 边。"
    )
    assert source.endswith(".chapter_number"), (
        f"declare.py:{lineno} 的 valid_from_chapter = `{source}`，它必须取自证据的章号。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# **一个永远绿的守卫比没有守卫更糟**，因为它还提供安全感。上面三条全靠「扫描器真的
# 走到了那些命令」——`get_command(app)` 换个 typer 版本就可能换形状，而遍历为空的
# 那一天，三条断言会安静地全绿。

probe_app = typer.Typer()
probe_declare = typer.Typer()
probe_app.add_typer(probe_declare, name="declare")


@probe_declare.command("knows")
def _probe_knows(
    who: str = typer.Option(..., "--who"),
    chapter: int = typer.Option(..., "--chapter", "-c"),
) -> None:
    """一条**该被拦掉**的命令：它让作者直接敲章号。"""


@probe_declare.command("where")
def _probe_where(
    who: str = typer.Option(..., "--who"),
    nth: int = typer.Option(0, "--nth"),
) -> None:
    """另一种形态：名字绕过了 BANNED，但它仍是「让作者挑一个数」。"""


CONSTANT_PROBE = """
def declare_edge(ev, spec_cls):
    return spec_cls(src="a", dst="b", valid_from_chapter=88)
"""

PARAM_PROBE = """
def declare_edge(ev, chapter, spec_cls):
    return spec_cls(src="a", dst="b", valid_from_chapter=chapter)
"""

TWICE_PROBE = """
def declare_edge(ev, spec_cls):
    if ev.planned:
        return spec_cls(valid_from_chapter=ev.planned_chapter)
    return spec_cls(valid_from_chapter=ev.chapter_number)
"""

DOCSTRING_PROBE = '''
def declare_edge(ev, spec_cls):
    """按 §5.9，这里写 valid_from_chapter=88 是不行的。"""
    return spec_cls(valid_from_chapter=ev.chapter_number)
'''


def test_the_guard_can_see_a_chapter_flag() -> None:
    """喂一个带 `--chapter` 的 declare 子命令进扫描器，断言它红。"""
    offenders = scan(get_command(probe_app))

    assert any("chapter" in o for o in offenders), (
        f"扫描器没看见 --chapter：{offenders}。上面三条守卫因此是永远绿的。"
    )
    assert any("nth" in o and "int" in o for o in offenders), (
        f"扫描器没看见 declare 上的 int 型参数：{offenders}。"
        "「--nth 2」是 --chapter 换了个名字：它同样是让作者挑一个数。"
    )


def test_the_guard_actually_walks_the_real_app() -> None:
    """扫描器要是把 app 找错了地方（或者遍历为空），三条守卫会永远绿着通过。"""
    paths = {path for path, _ in walk(get_command(app))}

    assert {"declare knows", "declare believes", "declare where", "declare alias"} <= paths
    assert {"init", "import", "sync", "locate", "panel", "check"} <= paths
    # 声明面上真的有参数可扫——一条零参数的命令当然「没有章号输入框」。
    commands = {path: cmd for path, cmd in walk(get_command(app))}
    assert len(commands["declare knows"].params) >= 4


def test_the_ast_guard_can_see_a_hand_typed_chapter() -> None:
    """三种真实的坏形态各喂一次。都能编译、都能跑、都能在面板上画得很正常。"""
    assert valid_from_assignments(CONSTANT_PROBE) == [(3, "Constant", "88")]
    assert valid_from_assignments(PARAM_PROBE) == [(3, "Name", "chapter")]

    twice = valid_from_assignments(TWICE_PROBE)
    assert len(twice) == 2, "两条路给 valid_from 填数，扫描器必须都看见"

    # docstring 里引着它的地方不是赋值。误报会让人把守卫关掉，而 declare.py 的
    # docstring 到处在讲 valid_from。
    assert valid_from_assignments(DOCSTRING_PROBE) == [(4, "Attribute", "ev.chapter_number")]


# ══════════════════════════════════════════════════════════════════════════
# 第四道：**改**一条已生效事实的入参里也没有章号
# ══════════════════════════════════════════════════════════════════════════
#
# 前三道盯的是「新增」那条路（`nh declare *` / `declare.py`）。作者推翻「事前逐条确认」
# 之后多出来一条路：抽取直接生效，作者事后**改**（`corrections.py` + `/canon/…`）。
# 那条路上「改一条 CANON 事实」离「顺手也让他改一下这条事实从第几章开始成立」只有一个
# 字段的距离，而那个字段一旦有了，作者填的就是他不记得的那个数（§5.9）。
#
# 判据换了（这里没有 typer 命令可扫）：扫 **Pydantic 入参模型的字段名** 和
# **改正层函数的参数名**。出参不扫——`KnowledgeCorrection.since_chapter` 是一个**产物**，
# 它必须在（面板要渲染「✓ 知道 (ch88)」），扫它等于要求这个产品别告诉作者章号。


def model_chapter_fields(model: Any) -> list[str]:
    """一个 Pydantic 模型上撞了章号的字段名。"""
    return [name for name in model.model_fields if BANNED.search(name)]


def callable_chapter_params(func: Any) -> list[str]:
    import inspect

    return [name for name in inspect.signature(func).parameters if BANNED.search(name)]


def test_fact_edit_request_schemas_have_no_chapter_field() -> None:
    """三个改正入参模型（两条 `/canon/…` + 提案 `edit`）里一个章号字段都没有。"""
    from novel_harness.api.review import (
        EventCastEditRequest,
        KnowledgeEditRequest,
        ProposalEditRequest,
    )
    from novel_harness.extract.proposal_models import ProposalReview

    offenders = {
        model.__name__: model_chapter_fields(model)
        for model in (
            KnowledgeEditRequest,
            EventCastEditRequest,
            ProposalEditRequest,
            ProposalReview,
        )
        if model_chapter_fields(model)
    }
    assert not offenders, (
        f"改正入参里出现了章号字段：{offenders}。\n"
        "改一条事实**说错了**和改它**从第几章开始成立**是两件事：后者只由证据决定，\n"
        "新事实的 valid_from 只能从被改的那条上继承（约束 10 / ADR 0006）。"
    )


def test_the_correction_layer_takes_no_chapter_argument() -> None:
    from novel_harness.corrections import correct_event_cast, correct_knowledge
    from novel_harness.graph.sqlite_events import SqliteEventStore

    offenders = {
        func.__qualname__: callable_chapter_params(func)
        for func in (correct_knowledge, correct_event_cast, SqliteEventStore.edit_cast)
        if callable_chapter_params(func)
    }
    assert not offenders, f"改正层长出了章号参数：{offenders}"


def test_the_schema_guard_can_see_a_chapter_field() -> None:
    """**守卫的自守卫**：喂一个真带章号的模型/函数进去，它必须红。

    没有这一条，上面两条在 `model_fields` 换形状（pydantic 升级）的那天会安静地全绿。
    """
    from pydantic import BaseModel

    class _Probe(BaseModel):
        character_id: str
        since_chapter: int

    def _probe(who: str, valid_from: int) -> None: ...

    assert model_chapter_fields(_Probe) == ["since_chapter"]
    assert callable_chapter_params(_probe) == ["valid_from"]

    class _Clean(BaseModel):
        character_id: str
        believed_value: str

    assert model_chapter_fields(_Clean) == [], "干净的模型不许被咬——假红会让人关掉守卫"
