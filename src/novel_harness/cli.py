"""nh — Novel Harness 命令行（PLAN §8 Day 5 下午 / §9）。

**这是装配层，不是业务层。** 它的活只有四件：开库（`db.connect`）、组装
（`SqliteStoryGraph`）、把作者敲的字递给能力层、把 Pydantic 出参画成终端上的框。
`tests/test_arch_guard.py:78` 把本文件列进 `CONNECTION_OPENERS` 就是为了这个——
**它是一张开连接的许可证，不是一张写 SQL 的许可证。** 要数据走 `StoryGraph` 的五个方法。

── 三条在这个文件里反复出现的约束 ────────────────────────────────────────

1. **`--cast` 收的是作者写的称呼原文，不是 node_id**（ARCHITECTURE §10.5 第 1 条）。
   本文件**一次都不解析它**，原样递给 `panel.resolve_cast` / `panel.scene_constraints`——
   那个契约存在的理由就是「调用方没有机会把一个人弄丢」。CLI 正是那种会顺手写
   `[r.unique_node.id for r in ... if r.unique_node]` 的调用方。

2. **`--chapter N` 是查询参数，不是声明。** 「把第 152 章的面板画给我看」里的 152 不进
   任何一行数据，`valid_from` 仍然只由证据决定（§10 约束 10）。所以这里没有、也不许有
   任何一个能让作者写 `valid_from` 的旗标——`nh declare` 的七条子命令里连一个 int 型
   参数都没有，而 `tests/test_no_chapter_input.py` 把这条从「读代码看得出来」变成
   一条 CI 断言。它拦的不是笔误，是那个「加个 --chapter 让作者自己挑不就完了」的下午。

3. **静默的零和真的零不许长得一样**（§10 约束 8 / `checks/__init__.py:29`）。
   一张零行的矩阵、一个没有秘密的项目、一个空库——在终端上全都长得像「一切正常」。
   本文件里每一处 `_die()` 都在拆这一类等号，它们不是防御性编程。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer
from pydantic import ValidationError

from . import __version__
from .checks import ALL_CHECKS, CheckContext, Issue, run_checks
from .db import Connection, connect, migrate
from .declare import (
    AmbiguousName,
    AmbiguousQuote,
    Declaration,
    DeclarationRefused,
    Ledger,
    QuoteCandidate,
    QuoteNotFound,
    UnknownName,
    WrongLabel,
)
from .graph import (
    HEALTH_DIM_KEY,
    HEALTH_DIM_NAME,
    AliasKind,
    InformationScope,
    KnowledgeCell,
    KnowledgeMatrix,
    KnowledgeState,
    NodeLabel,
    NodeRef,
    SecretDetail,
    StoreError,
)
from .graph.sqlite_store import SqliteStoryGraph
from .importer import ImportRefused, SyncRefused, import_book
from .importer import sync as sync_chapters
from .panel import (
    SceneConstraints,
    UnresolvedCast,
    knowledge_matrix,
    resolve_cast,
    scene_constraints,
)
from .project import Project
from .project import create as create_project
from .project import get as get_project
from .text import chapterize
from .text import paragraphs as split_paragraphs

if TYPE_CHECKING:  # `nh gate` 的裁决类型。**运行期不 import**——见 `gate()` 里那段
    from .eval.score import GateDecision  # 「不为一个永远不跑的实验装置买单」的理由。

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Novel Harness — 知道谁在第几章还不该知道什么。",
)

declare_app = typer.Typer(
    no_args_is_help=True,
    help=(
        "声明：告诉系统「这条事实在这段原文里出现过」。"
        "章号由引语算出来——这里没有任何一个旗标能让你填它。"
    ),
)
app.add_typer(declare_app, name="declare")

_CAST_SEP_RE = re.compile(r"[,，、]")
"""`--cast 萧决,顾清音、李管家` 的分隔符：半角逗号 / 全角逗号 / 顿号。

三个都是中文作者会打得出来的分隔符。**这里曾经和 `text/scenes.py` 各有一份**，
而那一份 2026-08-14 随场景块一起删了（ADR 0027），于是它现在是全库唯一的一份。
**同样不含空格**——`--cast "李 管家"` 里那个空格是名字的一部分。
"""


def _die(message: str) -> NoReturn:
    """**响亮地死。** 退出码非 0，消息进 stderr。

    `demo.sh` 是 `set -euo pipefail` 的（PLAN §8：心跳是一个每天可见的布尔值），
    所以退出码必须是那个布尔值本身。往 stdout 打一行「没找到数据」然后 exit 0，
    在心跳脚本眼里和「面板画出来了」完全一样。
    """
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


# ══════════════════════════════════════════════════════════════════════════
# 终端渲染：中文是双宽的，`len()` 在这里是错的
# ══════════════════════════════════════════════════════════════════════════


def _width(text: str) -> int:
    """字符串在等宽终端里占几列。

    `len("萧决") == 2` 但它占 4 列——拿 `len()` 去对齐，表格会歪掉，而**歪掉的表格
    正是这个产品唯一的产出物**（§3.2 那张框图就是可交付物本身）。
    `✓ ✗ ⚠` 是 East_Asian_Width=Ambiguous，绝大多数终端按 1 列画，所以只把 W/F 算 2。
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, columns: int) -> str:
    return text + " " * max(0, columns - _width(text))


def _box(title: str, lines: Sequence[str]) -> str:
    """把几行字画进 §3.2 / README 的那个框里。"""
    inner = max([_width(line) for line in lines] + [_width(title) + 1])
    out = ["┌─ " + title + " " + "─" * (inner - _width(title) - 1) + "┐"]
    out += ["│ " + _pad(line, inner) + " │" for line in lines]
    out.append("└" + "─" * (inner + 2) + "┘")
    return "\n".join(out)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """按列宽对齐的表格，行 0 是表头，表头下面有一条分隔线（README 里画着）。"""
    widths = [max(_width(row[i]) for row in [header, *rows]) for i in range(len(header))]
    out = ["  ".join(_pad(cell, w) for cell, w in zip(header, widths, strict=True)).rstrip()]
    out.append("─" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        out.append("  ".join(_pad(cell, w) for cell, w in zip(row, widths, strict=True)).rstrip())
    return out


def _cell_text(cell: KnowledgeCell) -> str:
    """一格。**三个状态必须长得完全不一样**——这张表的全部价值就在这一个函数里。

    `since_chapter` 一定要印出来（README 的框里印着 `✓ 知道 (ch88)`）：它是作者认出
    「哦对，我是在第 88 章让他知道的」的那个钩子，而**那个数字作者从来没有输入过**
    （ADR 0006：他是看着第 88 章的原文点的确认，章号由证据决定）。
    """
    match cell.state:
        case KnowledgeState.KNOWS:
            return f"✓ 知道 (ch{cell.since_chapter})"
        case KnowledgeState.BELIEVES:
            return f"⚠ 错误认知 (ch{cell.since_chapter})"
        case _:
            return "✗ 不知道"


def _believes_lines(matrix: KnowledgeMatrix) -> list[str]:
    """BELIEVES 的详情。

    README 的框把「(ch103 起：以为已泄露)」画成格子下面的续行；这里改成表格下面的一段。
    **信息一个字没少，位置换了**——续行要在双宽对齐里再嵌一层，会把 `_table` 变成一个
    没人敢改的东西，而 `believed_value`（他以为的那个版本）是这一行的全部意义所在。
    """
    names = {ref.id: ref.name for ref in [*matrix.characters, *matrix.secrets]}
    out: list[str] = []
    for cell in matrix.cells:
        if cell.state is not KnowledgeState.BELIEVES:
            continue
        value = cell.believed_value or "（作者没写他以为的是什么）"
        out.append(
            f"  {names[cell.character_id]} × {names[cell.secret_id]}："
            f"ch{cell.since_chapter} 起以为「{value}」"
        )
    return out


def _constraint_lines(constraints: SceneConstraints) -> list[str]:
    """`must_not_reveal` / `forbidden_entities`（README 的框里它们在矩阵下面）。

    `must_not_reveal` **为空时也要印一行**「（无）」：空行和「这一场没有秘密要瞒」
    在终端上没有区别，而后者是一个断言。
    """
    secrets = " · ".join(ref.name for ref in constraints.must_not_reveal) or "（无）"
    out = [f"本场景 must_not_reveal：{secrets}"]
    if constraints.forbidden_entities:
        entities = " · ".join(
            f"{e.node.name}(ch{e.first_appears_chapter} 首现)" for e in constraints.forbidden_entities
        )
        out.append(f"本场景 forbidden_entities：{entities}")
    return out


def _unresolved_lines(unresolved: Sequence[str]) -> list[str]:
    """**解析不出唯一角色的称呼必须画出来，不许静默丢。**

    `panel/constraints.py:91` 的原话：不把 `unresolved` 传下去「面板会安静地少一行」。
    传下去了还不画，是同一个 bug 挪了 20 行——作者声明了 3 个人、看见 2 行，
    然后以为系统对第 3 个人没意见。
    """
    if not unresolved:
        return []
    return [
        "",
        f"⚠ 这些称呼解析不出唯一角色，面板没有替他们算：{'、'.join(unresolved)}",
        "  「师兄」在一章里可能指 8 个人，系统猜错的产物是一条本该保密的秘密从",
        "  must_not_reveal 里消失。请在别名表里指定他们是谁，或者把称呼写具体。",
        "  （此刻 must_not_reveal 是退化值 = 全部秘密，fail-closed，不是算出来的答案。）",
    ]


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


def _connect_existing(db: Path) -> Connection:
    """开一个**已经存在**的库。不存在就死，不替你建。

    `connect()` 会把它建出来（还顺手 mkdir 父目录），于是一个敲错的路径会得到一个空库，
    然后面板理直气壮地画出一张「在场三个人对全部秘密一无所知」——闭世界推导下 UNKNOWN 是
    **断言**不是「查不到」。打错一个字母的代价不该是一个看起来完全正常的错误答案。

    唯一有资格建库的是 `nh init`：那条命令里「这个文件不存在」正是作者的意图。
    """
    if not db.exists():
        _die(
            f"库不存在：{db}\n"
            "不替你建：connect() 建得出一个空库，而空库上的面板会画出一张「谁都不知道」的\n"
            "矩阵——闭世界推导下那是一个**断言**，不是一个空结果。一个敲错的路径不该长得\n"
            "像一个正确的答案。\n"
            "新开一本书走 nh init。"
        )
    return connect(db)


def _open_store(db: Path, project: str) -> SqliteStoryGraph:
    """**读路径**的装配（`nh panel` / `nh check`）：开库 + 组装 + 确认这个项目真的有东西。

    两道闸门，都在拆「静默的零 == 真的零」那个等号。第二道是**花名册为空就死**：
    走 `resolve(project)`（`surfaces=None` = 全项目花名册）——它是 `StoryGraph` 五个方法里
    唯一能回答「这个项目里有东西吗」的那个。空花名册 = 项目号打错了或者一条声明都没有，
    两者都不该画面板。

    **写路径不能用这道闸门，用 `_open_project`**：`nh init` 之后、第一条 `nh declare` 之前，
    空花名册是正确且必然的状态。拿读路径的闸门去关写路径，等于让作者永远迈不出第一步。
    """
    store = SqliteStoryGraph(_connect_existing(db))
    if not store.resolve(project):
        _die(
            f"项目 {project} 在 {db} 里没有任何花名册行。\n"
            "要么 project_id 打错了，要么这本书还一条声明都没有"
            "（先 nh import 落章，再 nh declare character / secret）。\n"
            "无论哪种，画出来的都会是一张零行矩阵——它长得像「没问题」，其实是「没数据」。"
        )
    return store


def _open_project(db: Path, project: str) -> tuple[SqliteStoryGraph, Connection, Project]:
    """**写路径**的装配（`nh import` / `nh sync` / `nh locate` / `nh declare *`）。

    闸门是「`project` 表里有没有这一行」，不是花名册——理由见 `_open_store`。这道闸门
    今天才可能存在：`project.get()` 是 M1 才有的（在那之前 `_open_store` 的 docstring
    只能拿花名册当项目存在性的替身）。它比花名册准：**它分得开「项目号打错了」和
    「这本书还没开始写」**，而那两件事的正确处置完全相反。

    同时返回 store 和 conn，因为 `Ledger` 要两个：图和 `decision_log` 是两条被刻意做成
    不同生命周期的日志（见 `declare.py`）。**一条连接**——两条会让 `store.transaction()`
    罩不住它该罩的东西。
    """
    conn = _connect_existing(db)
    proj = get_project(conn, project)
    if proj is None:
        _die(
            f"项目 {project} 不在 {db} 里。\n"
            "要么 project_id 打错了，要么这个库是别本书的。nh init 会吐出一个新的。\n"
            "不替你建：一个凭空建出来的项目，它的 root_path 只能是猜的，而 chapters/ 找错\n"
            "地方的产物是一本永远定位不到任何引语的书。"
        )
    return SqliteStoryGraph(conn), conn, proj


def _split_cast(raw: str) -> list[str]:
    """`--cast` 原文 → 称呼列表。**只切分，不解析**（§10.5 第 1 条）。"""
    return [name.strip() for name in _CAST_SEP_RE.split(raw) if name.strip()]


# ══════════════════════════════════════════════════════════════════════════
# 声明的渲染：拒绝与回执
# ══════════════════════════════════════════════════════════════════════════


def _die_refused(exc: DeclarationRefused) -> NoReturn:
    """一次被拒绝的声明。**exit 1**，消息进 stderr。

    异常自己的消息已经把「拒绝的理由 + 候选」说完了（`declare.py` 那五个类）。这里只加
    一条**属于命令行这一层**的尾巴：作者刚敲完一条命令，他此刻的下一个念头是「那给我
    一个旗标让我自己挑」——回答那个念头的地方是这里，不是那个不知道自己被谁调用的异常。

    这不违反约束 8：约束 8 治的是**系统主动推队列给作者**；这里是作者按了按钮而系统
    不肯替他猜，方向相反。**但绝不许挑一个。**
    """
    _die(f"✗ 拒绝：{exc}{_refusal_tail(exc)}")


def _refusal_tail(exc: DeclarationRefused) -> str:
    """**终端里那半句「怎么办」。**

    2026-08-13 起这里多了两支（`AmbiguousQuote` 的 `nh locate`、`QuoteNotFound` 的
    `nh sync`），而它们不是新话——是**从 `declare.py` 搬过来的**。搬的理由：那些消息
    经 `api/app.py` 原样进浏览器，而产品的最终用户不碰命令行，「先跑 nh sync」对他是
    一句死路。引擎那一层现在只说产品无关的半句（「让系统重新读一遍稿子」），
    终端这半句归这里，浏览器那半句归抽屉上那颗按钮。

    所以下面那条 `case _` 的注释也跟着改了：它原本的理由是「异常自己已经说完了」，
    而那个前提在搬走之后不再成立。
    """
    match exc:
        case AmbiguousQuote():
            return (
                "\n  （没有 --pick，也没有 --chapter 让你直接指定第几章。那个旗标就是章号\n"
                "    输入框换了个变量名——挑错的产物是一条 valid_from 错了的 CANON 边，\n"
                "    而它在面板上长得完全正常：没有任何一条规则、任何一个面板分区、\n"
                "    任何一次 review 会发现它。）\n"
                "  改引语之前先用 nh locate 试，比重敲一整条 declare 便宜。"
            )
        case AmbiguousName():
            return (
                "\n  系统不替你挑——挑错的产物是一条本该保密的秘密从 must_not_reveal 里消失，\n"
                "  而那一格在面板上长得跟「他确实不知道」一模一样。\n"
                "  用具体的名字，或者先 nh declare alias 把这个称呼指定给一个人。"
            )
        case UnknownName():
            return "\n  先声明它：nh declare character / place / secret。"
        case QuoteNotFound():
            # 「让系统重新读一遍稿子」在终端里的说法。**它只在这一层说得出口**——
            # 同一句话在浏览器里是抽屉上那颗按钮。
            return "\n  「重新读一遍稿子」在终端里是：nh sync（把 chapters/*.md 的现状读进库）。"
        case _:
            # `WrongLabel` 及将来的新拒绝类型：那些异常自己的消息已经把「怎么办」说完了，
            # 而且那半句不带产品形态（不像 sync / locate 那样在两个壳里长不同的样子）。
            # **不硬凑一条尾巴**——把同一句话说两遍会教作者跳过整段，包括他真正需要读的那半句。
            return ""


def _reason(exc: Exception) -> str:
    """把一个异常压成作者读得下去的一句话。

    pydantic 的 `ValidationError` 的 `str` 是 4 行给开发者看的东西（`[type=value_error,
    input_value={'project_id': 'project:0...}]` 加一条 errors.pydantic.dev 的链接），
    而它包着的 `Value error, ...` 那半句，正是 `AliasSpec` / `NodeSpec` 的 validator
    专门写给作者的话（「别名「音」只有 1 个字…要留着它就传 usable_for_rules=False」）。
    原样打出来，作者会跳过整段——**包括那半句**，也就是唯一告诉他该怎么办的那半句。
    """
    if isinstance(exc, ValidationError):
        return "；".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


def _node_names(store: SqliteStoryGraph, project_id: str) -> dict[str, str]:
    """node_id → name，从全项目花名册建。

    supersede 闭合掉的那条旧边的 dst 是作者这次**没有敲过**的节点（他上一次在哪儿），
    所以「自动闭合：萧决 LOCATED_AT **青云城主府**」里的名字只能从库里来。走
    `resolve(project)` 而不是加一个查询：它是 `StoryGraph` 五个方法里唯一能给出
    「这个项目里有哪些节点」的那个，而本文件不写 SQL。

    Chapter 节点不在花名册里（`CANONICAL_ALIAS_LABELS` 排除了它），这里也不需要它们。
    """
    out: dict[str, str] = {}
    for resolution in store.resolve(project_id):
        for hit in resolution.hits:
            out[hit.node.id] = hit.node.name
    return out


def _declaration_lines(decl: Declaration, names: dict[str, str]) -> list[str]:
    """一次成功的声明的回执。

    **最后那两行不是装饰。** 「这个数字你没有输入过」是这个项目全部差异化的地基
    （§5.9 / 约束 10）在终端上唯一露头的地方：作者看见 ch88，他会以为是自己填的，
    然后下一次他会想去改它——除非这里当场告诉他那是算出来的。
    """
    edge = decl.edge
    anchor = decl.evidence.anchor()
    src = names.get(edge.src, edge.src)
    dst = names.get(edge.dst, edge.dst)
    out = [
        f"✓ {src} {edge.type.value} {dst}    valid_from = ch{decl.valid_from}",
        f"  依据：第 {decl.evidence.chapter_number} 章 · 第 {anchor.para_index} 段 · "
        f"第 {anchor.occurrence_k} 次",
        f"        「{anchor.quote_text}」",
        f"  ↑ {decl.valid_from} 是**算出来的**：这句话落在第 {decl.valid_from} 章。"
        "你没有输入过这个数字，",
        "    也没有任何一个旗标能让你输入它（PLAN §5.9 / 约束 10）。",
        f"  已记入 decision_log：{decl.decision_id}",
    ]
    out += _supersede_lines(decl, names)
    return out


def _supersede_lines(decl: Declaration, names: dict[str, str]) -> list[str]:
    """自动闭合 / 撤回。**这是这个产品的招牌动作，不印出来等于没做。**

    作者敲的是「他到了北荒」，系统顺手把「他在青云城主府」那条边闭合到 `[88, 150)`。
    那个 150 同样是算出来的（= 新边的 valid_from）。不印的话，作者永远不知道系统替他
    维护了一条时间线——而他不知道的功能等于不存在的功能。
    """
    out: list[str] = []
    for old in decl.closed:
        src = names.get(old.src, old.src)
        dst = names.get(old.dst, old.dst)
        out.append(
            f"  ↳ 自动闭合：{src} {old.type.value} {dst} "
            f"[{old.valid_from_chapter}, {old.valid_to_chapter})"
        )
        out.append(
            f"    （{old.type.value} 的 exclusivity 是 single_per_src：一个人同时只能在"
            "一个地方。"
        )
        out.append(f"      {old.valid_to_chapter} 这个数字同样是算出来的。）")
    for old in decl.retracted:
        src = names.get(old.src, old.src)
        dst = names.get(old.dst, old.dst)
        out.append(
            f"  ↳ 已撤回：{src} {old.type.value} {dst}（同章更正——这条事实从未成立过，"
            "不是「后来变了」）"
        )
    return out


def _candidate_lines(candidates: Sequence[QuoteCandidate]) -> list[str]:
    return [
        f"    第 {c.chapter_number:>3} 章 · 第 {c.para_index:>3} 段 · 第 {c.occurrence_k} 次"
        f"   {c.context}"
        for c in candidates
    ]


# ══════════════════════════════════════════════════════════════════════════
# 命令
# ══════════════════════════════════════════════════════════════════════════


@app.command()
def version() -> None:
    """打印版本。"""
    typer.echo(__version__)


@app.command()
def serve(
    db: Path = typer.Option(..., "--db", help="SQLite 库（不存在就建一个空书架）"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址。默认只听本机"),
    port: int = typer.Option(8756, "--port", help="想要的端口；被占了自动换一个空闲的"),
    books_dir: Path | None = typer.Option(
        None, "--books-dir", help="新书稿子目录的基址。默认 <库同级>/books"
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="起好后自动开浏览器"),
) -> None:
    """起浏览器工作台：建库 + 起服务 + 开浏览器（薄壳；逻辑已搬进 api/launch.py，本命令面待删）。"""
    from .api.launch import LaunchError, launch

    try:
        launch(db, host=host, port=port, books_dir=books_dir, open_browser=open_browser)
    except LaunchError as exc:
        _die(f"✗ {exc}")


@app.command()
def init(
    name: str = typer.Option(..., "--name", help="书名"),
    root: Path = typer.Option(..., "--root", help="稿子目录（chapters/*.md 会落在这儿）"),
    db: Path = typer.Option(..., "--db", help="SQLite 库（不存在就建）"),
) -> None:
    """新开一本书。**stdout 只出 project_id 一行**，别的话一律 stderr。

    那一行是给 `PID=$(nh init ...)` 吃的（同 `scripts/seed_demo.py` 的 main）：一个往
    stdout 打欢迎语的 init 会让每一个用它的脚本拿到一个带着「✓ 建好了」的 project_id。

    `root` 存的是 `resolve()` 之后的**绝对路径**，于是**这个库不可搬家**：把 db 和稿子
    一起挪到另一台机器上，`nh sync` 会去找一个不存在的目录。v1 是单机的（ADR 0007：
    作者用 VSCode 写磁盘上的 md），这条限制写在这里而不是假装它可搬——存相对路径的话
    「相对谁」的答案是 cwd，而 cwd 是作者敲命令时碰巧在哪儿。
    """
    resolved_root = root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    conn = connect(db)
    migrate(conn)
    proj = create_project(conn, name=name, root_path=str(resolved_root))

    typer.echo(proj.id)  # ← stdout 的全部内容。下面每一行都是 err=True。
    typer.secho(
        f"✓ 《{name}》建好了\n"
        f"  库：{db.resolve()}\n"
        f"  稿子：{resolved_root}（章节文件会落在 {resolved_root / 'chapters'}）\n"
        f"  下一步：nh import <book.txt> --db {db} -p {proj.id}\n"
        "  （或者直接往 chapters/ 里写 0001.md 然后 nh sync。那个目录就是稿子，不是导出物。）",
        fg=typer.colors.GREEN,
        err=True,
    )


@app.command("import")
def import_(
    path: Path = typer.Argument(..., help="小说 TXT"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """**一次性播种**：切章 → 写成 `{root}/chapters/NNNN.md` → 落库。日常回路用 `nh sync`。

    先**对账**（切出来几章、每章的标题行长什么样）再落库：章数对不上目录时，作者要看的
    是那份对账，而它必须在库被写脏之前印出来。

    这里没有 `--chapter`、没有 `--valid-from`：章号是 `chapterize` 的文本顺序，作者的输入
    进不到那条链上（§5.9 / 约束 10）。也没有 `--force`：覆盖作者的稿子是这个项目最不能犯
    的错（见 `importer.explode`）。
    """
    if not path.exists():
        _die(f"文件不存在：{path}")
    store, _conn, proj = _open_project(db, project)

    # 这一份切章**只为了下面那张对账表**；落库的那一份由 `importer.import_book` 自己切
    # （它是 `chapter.number` 的产地）。同一份字节切两次是确定性的，浪费的是毫秒；
    # 而把 book 递进去省掉这一次的代价是 `import_book` 从「给它一个 TXT」变成「给它一个
    # 你已经切好的东西」——那时切歪的责任就从导入器挪到了每一个调用方身上。
    #
    # utf-8-sig 而不是 utf-8：`utf-8` 解 utf-8-sig 的文件**会成功**并把 BOM 原样留在
    # 串里（chapterize.normalize 的实测记录）。normalize() 会兜住它，这里多一道是因为
    # 装配层本来就该负责「把磁盘上的字节变成干净的 str」，而不是让下游每个人都记得兜。
    book = chapterize(path.read_text(encoding="utf-8-sig"))

    if not book.chapters:
        _die(
            f"{path} 里一个章标都没切出来（认的是行首的「第N章/节/回」）。\n"
            "零章不是「这本书是空的」，是「切章器没认出这本书的章标写法」——"
            "别把它当成导入成功。"
        )

    typer.echo(f"{path}：切出 {len(book.chapters)} 章")
    if book.preamble.strip():
        # preamble 要报数：真书的第一个章标前面有书名/简介/免责声明，而**第一个卷标题
        # 也在这里**。它异常地长，往往意味着第一章的章标没被认出来。
        typer.echo(f"  卷首（第一个章标之前）：{len(book.preamble.strip())} 字，未计入任何一章")
    for chapter in book.chapters:
        title = chapter.title or "（无标题）"
        typer.echo(f"  {chapter.index:>4}  {chapter.marker}  {title}")

    typer.echo("")
    typer.secho(
        "↑ 上面这个 index 是**文本顺序**，不是标题里印的那个数字（分卷重启会让印号重复）。\n"
        "  拿它跟目录对账：数一样、且顺序一样，切章才算对。",
        fg=typer.colors.CYAN,
        err=True,
    )

    root = Path(proj.root_path)
    try:
        report = import_book(store, project, txt=path, root=root)
    except ImportRefused as exc:
        # 一个字节都没写（`explode` 是两阶段的）。冲突文件逐条列出来——「拒绝导入」
        # 而不说是哪几个文件，作者唯一的下一步就是删掉整个目录重来。
        _die(f"✗ {exc}")
    except SyncRefused as exc:
        _die(f"✗ {exc}\n  出问题的文件：{exc.path}")

    synced = report.synced
    in_db = len(synced.added) + len(synced.refreshed) + synced.unchanged_count
    typer.echo(
        f"✓ 落库：新建 {len(report.written)} 个章节文件，"
        f"复用 {len(report.unchanged)} 个（内容一字不差，跳过），库里现在 {in_db} 章。\n"
        f"  稿子在：{root / 'chapters'}"
    )
    if synced.ignored_files:
        # 不是错误：那个目录是作者的工作区（`notes.md` / `大纲.md` / 编辑器的临时文件）。
        # 但要报数——一个被静默忽略的 `0004.txt`（后缀打错）长得就像「这一章导进去了」。
        typer.echo(f"  忽略了 {len(synced.ignored_files)} 个不叫 NNNN.md 的文件：")
        for rel in synced.ignored_files:
            typer.echo(f"    {rel}")
    typer.secho(
        "  chapters/*.md 从现在起就是稿子（ADR 0007）：直接用你的编辑器改它，\n"
        "  改完跑 nh sync。别再改那份 TXT——import 不覆盖已存在且内容不同的文件。",
        fg=typer.colors.CYAN,
        err=True,
    )


@app.command()
def sync(
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """把 `{root}/chapters/*.md` 的**现状**读进库。**日常回路。**

    没有它，`nh import` 就是一次性播种，而「我刚写完的第 301 章里那句话」永远定位不到
    ——`nh declare` 搜的是库里的快照，快照只有这里落得下。

    ⚠️ **它不做 STALE / relocate / revalidate（那是 M4）**：改过第 88 章之后，锚在旧快照上
    的证据可能已经指不准了，而这条命令不声称它们仍然准。它只让新写的正文可被定位。
    """
    store, _conn, proj = _open_project(db, project)
    root = Path(proj.root_path)
    try:
        report = sync_chapters(store, project, root)
    except SyncRefused as exc:
        _die(f"✗ {exc}\n  出问题的文件：{exc.path}")

    total = len(report.added) + len(report.refreshed) + report.unchanged_count
    if total == 0:
        # 真的零 vs 静默的零：`{root}/chapters/` 不存在、或者里面一个 NNNN.md 都没有，
        # 两者的自然产物都是「✓ 同步完成」+ exit 0，而作者的下一步 declare 会因为
        # 「引语找不到」被拒——他会去查引语，而问题在这里。
        _die(
            f"{root / 'chapters'} 里一个 NNNN.md 都没有，库里一章都没落。\n"
            "  这不是「没有变化」，是「没有稿子」——认的是四位起补零的纯数字文件名"
            "（0001.md），\n"
            "  数字来自章的顺序位置，不是正文里印的那个章号。\n"
            f"  先跑 nh import <book.txt> --db {db} -p {project}，或者自己往那个目录里写 0001.md。"
        )

    typer.echo(
        f"✓ 同步完成：新增 {len(report.added)} 章，"
        f"更新 {len(report.refreshed)} 章，{report.unchanged_count} 章没变（库里共 {total} 章）。"
    )
    for stored in report.added:
        typer.echo(f"  + 第 {stored.number} 章  {stored.title or '（无标题）'}  {stored.path}")
    for stored in report.refreshed:
        # 「多了一条快照」而不是「改了那一章」：旧快照永不删（审计指针指着它，ADR 0006）。
        typer.echo(
            f"  ~ 第 {stored.number} 章  {stored.title or '（无标题）'}  {stored.path}"
            "  → 新快照（旧的还在，旧证据的引语仍指得回原文）"
        )
    if report.ignored_files:
        typer.echo(f"  忽略了 {len(report.ignored_files)} 个不叫 NNNN.md 的文件：")
        for rel in report.ignored_files:
            typer.echo(f"    {rel}")


@app.command()
def locate(
    quote: str = typer.Option(..., "--quote", help="从你的正文里**复制**的一句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """这句引语在当前正文里的全部命中。**只读预览**：库里一个字节都不会变。

    它存在的理由是 `nh declare` 的那条拒绝：「这句话在 3 处都能定位到，系统不替你挑」。
    作者复制短句时会被拒好几次，而每次重试都要重敲整条 declare 命令——这条让他先便宜地
    试出一句只匹配一处的引语。

    **零命中非 0 退出**：这条命令的问题是「我这句引语能用吗」，而零命中的答案是「不能」。
    印一行「命中 0 处」然后 exit 0，跟印「命中 1 处」在 `set -e` 的脚本眼里一模一样。
    """
    store, conn, _proj = _open_project(db, project)
    candidates = Ledger(store, conn, project).locate(quote)

    if not candidates:
        # **借 `QuoteNotFound` 的消息，不再抄一份。** 这三行以前在这儿有第二份拷贝，
        # 而 2026-08-13 改「别对作者说命令行」时只改得动 `declare.py` 那一份——
        # 两份措辞漂开的那一刻，同一个问题在 `nh locate` 和 `nh declare` 下会得到
        # 两个不同的建议，而没有任何东西会红。
        miss = QuoteNotFound(quote)
        _die(f"{miss}{_refusal_tail(miss)}")

    typer.echo(f"「{quote}」：命中 {len(candidates)} 处")
    for line in _candidate_lines(candidates):
        typer.echo(line)
    if len(candidates) > 1:
        typer.secho(
            "  ↑ 多于一处 → nh declare 会拒绝，且不会替你挑。\n"
            "    把引语加长到只匹配一处（前后各多复制半句通常就够）。",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command()
def panel(
    chapter: int = typer.Option(..., "--chapter", "-c", help="看第几章的面板（查询，不是声明）"),
    cast: str = typer.Option("", "--cast", help="逗号分隔的在场角色**称呼原文**"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """打印某章某场景的认知边界矩阵（**头牌**，PLAN §8「本周 done_when」就是这条命令）。

    `--chapter` 是「AS OF 第几章」，是查询参数。**它不写进任何一行数据**——`valid_from`
    只由证据决定（§10 约束 10），所以这里没有任何一个旗标能让作者填章号。
    """
    store = _open_store(db, project)

    names = _split_cast(cast)
    if not names:
        _die(
            "--cast 是空的。\n"
            "空 cast 画出来是一张零行的表，而零行和「在场的人都没问题」在终端上一模一样。\n"
            "（`ResolvedCast.complete` 对空 cast 返回 False 也正是这个理由：`any()` over\n"
            "empty 是 False，于是「一个人都没有」和「全员都知道」会产出同样的零约束。）\n"
            "例：nh panel --chapter 152 --cast 萧决,顾清音,李管家 --db book.db -p <pid>"
        )

    # 原文进，原文进，原文进。这里不 resolve，`resolve_cast` 自己 resolve——
    # 面板行序 = 作者写的顺序，而解析不出来的那些有一个自己的去处（§10.5 第 1 条）。
    resolved = resolve_cast(store, project, names)
    try:
        matrix = knowledge_matrix(
            store,
            project,
            chapter,
            resolved.ids,
            scope=InformationScope.CANON,
            unresolved=resolved.unresolved,
        )
        constraints = scene_constraints(store, project, chapter, names)
    except (ValueError, StoreError) as exc:
        _die(str(exc))

    if not matrix.secrets:
        _die(
            f"项目 {project} 里一条秘密都没声明，矩阵是 {len(matrix.characters)} 行 × 0 列。\n"
            "**这不是「他们什么都不知道」**——那是一个断言，而这里是「没有东西可断言」。\n"
            "秘密由作者声明（ADR 0004：秘密是作者的意图，不是文本特征，抽取器只能猜）。"
        )

    if not resolved.ids:
        _die(
            f"--cast 里的 {len(names)} 个称呼**一个都没解析出唯一角色**："
            f"{'、'.join(resolved.unresolved)}\n"
            "画不出行来。要么名字打错了，要么这些称呼有歧义（「师兄」→ 8 个人），"
            "要么他们还没进花名册。"
        )

    lines = _table(
        ["在场角色", *(ref.name for ref in matrix.secrets)],
        [
            [ref.name, *(_cell_text(matrix.cell(ref.id, s.id)) for s in matrix.secrets)]
            for ref in matrix.characters
        ],
    )
    believes = _believes_lines(matrix)
    if believes:
        lines += ["", *believes]
    lines += ["", *_constraint_lines(constraints)]
    lines += _unresolved_lines(matrix.unresolved_cast)

    typer.echo(_box(f"认知边界 · 第 {chapter} 章", lines))


@app.command()
def check(
    chapter: int = typer.Option(..., "--chapter", "-c", help="这份稿子是第几章（查询参数）"),
    file: Path = typer.Option(..., "--file", "-f", help="本章正文 Markdown"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """跑一致性规则，打印 issue + 证据锚。

    `--file` 而不是「从库里取第 N 章的正文」：正文在磁盘上，而 `chapters/*.md` **就是稿子**
    （ADR 0007），库里那份是快照。作者刚在编辑器里敲完的那一段还没进库（要跑 `nh sync`），
    而他要体检的正是那一段——从库里取会让这条命令永远晚一步。

    `--chapter` 是「这份稿子是第几章」，是查询参数：它决定拿哪一章的图去比对，不写进
    任何一行数据（§10 约束 10）。
    """
    if not file.exists():
        _die(f"稿子不存在：{file}")
    store = _open_store(db, project)

    # 走 anchor.paragraphs() 而不是裸 splitlines()：那个函数存在的全部理由就是让
    # 「什么是一段」只有一处定义（它的 docstring 点名的两个消费者就是这儿和证据锚）。
    # paragraphs() 一改（比如改成按空行分段）而别处没跟着改，Issue 就会锚到隔壁段落，
    # 且是「偶尔差一两段」那种查两周的形态
    # （ADR 0006 判 offset 的那段话，一字不差地适用于 para_index 的两份定义）。
    paragraphs = split_paragraphs(file.read_text(encoding="utf-8-sig"))

    ctx = CheckContext(
        store=store,
        project_id=project,
        chapter=chapter,
        paragraphs=paragraphs,
    )
    issues = run_checks(ctx)

    # 「跑了几条规则」必须印出来，且这是本命令唯一的成功输出。§10 约束 8 /
    # checks/__init__.py 那张表：静默的零和真的零不许长得一样。「无 issue」的真实含义是
    # 「跑过的这几条没意见」，不是「这章没问题」——沉默的工具死得比吵闹的工具更快，
    # 只是死得更安静。**所以这里印的是 `len(ALL_CHECKS)` 和规则名，不是写死的数字。**
    #
    # 2026-08-14 这一行少了「N 个场景块」那一段，紧跟着那句「这一章没有场景块，所以
    # 某条没东西可查」也一起没了（ADR 0027）。**那句话是这次砍掉的东西留下的最后一个
    # 形态**：一条在真书上永远跑不起来的规则，每检查一次就要为自己的缺席解释一次。
    typer.echo(
        f"第 {chapter} 章：跑了 {len(ALL_CHECKS)} 条规则"
        f"（{', '.join(c.__module__.rsplit('.', 1)[-1] for c in ALL_CHECKS)}），"
        f"{len(issues)} 条 issue。"
    )
    if not issues:
        return
    for issue in issues:
        typer.echo("")
        typer.echo(_issue_text(issue))
    raise typer.Exit(1)


@app.command()
def draft(
    goal: str = typer.Option(..., "--goal", "-g", help="这一场要写什么"),
    cast: str = typer.Option(..., "--cast", help="在场角色称呼，逗号分隔"),
    chapter: int = typer.Option(..., "--chapter", "-c", help="第几章（查询参数，不写进数据）"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="可选：上文/prior（最多 800 code points）"
    ),
    length: str = typer.Option(
        "2000-2500-3000", "--length", help="min-target-max（中文），如 2000-2500-3000"
    ),
    arm: str = typer.Option(
        "X1", "--arm", help="X0（零图谱）/ X1（事实清单）/ X2（叙事提示）"
    ),
    style: str = typer.Option(
        "", "--style", help="自定义文风（可选；留空 = 默认文风）"
    ),
) -> None:
    """实验通道：跑一次 AI 起草（**不走 kill-gate、不写证据**）。

    给维护者亲手看模型行为用的（调 prompt / 长度 / 温度时几秒一轮）。
    **它不是开闸**：公开 `/draft` 仍 501，kill-gate 协议原封不动，
    M2 的正式裁决仍走 `nh gate`；这条命令的输出不做任何判分。
    """
    from .draft.assemble import WRITE_RULE_FORBIDDEN_HINTS, PromptForm, assemble
    from .draft.capabilities import (
        CapabilityError,
        ReasoningEffort,
        plan_call,
        resolve_capabilities,
    )
    from .draft.context import ResolvedConstraints
    from .draft.generate import generate_draft
    from .draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage, LengthSpec
    from .draft.provider import ProviderConfig, ProviderError
    from .panel.constraints import UnresolvedCast, scene_view

    try:
        parts = [int(p) for p in length.split("-")]
        if len(parts) != 3:
            raise ValueError("格式是 min-target-max，如 2000-2500-3000")
        spec = DEFAULT_LENGTH_POLICY.validate_spec(
            LengthSpec(
                language=DraftLanguage.ZH,
                min_units=parts[0],
                target_units=parts[1],
                max_units=parts[2],
            )
        )
    except (ValidationError, ValueError) as exc:
        _die(f"✗ 长度档不合法：{_reason(exc)}")

    try:
        form = PromptForm[arm.strip().upper()]
    except KeyError:
        _die(f"✗ arm 只能是 X0 / X1 / X2，收到 {arm!r}")

    if style.strip():
        hits = [w for w in WRITE_RULE_FORBIDDEN_HINTS if w in style]
        if hits:
            _die(
                f"✗ 自定义文风里不能出现这些词：{' / '.join(hits)}"
                "——文风三臂共用，写进去等于给对照组也上了约束。"
            )

    prior = ""
    if file is not None:
        if not file.exists():
            _die(f"✗ 上文文件不存在：{file}")
        prior = file.read_text(encoding="utf-8-sig")

    surfaces = [c.strip() for c in cast.split(",") if c.strip()]
    store = _open_store(db, project)
    try:
        view = scene_view(store, project, chapter, surfaces)
        ctx = ResolvedConstraints.of(view, surfaces)
        messages = assemble(
            ctx,
            form=form,
            goal=goal,
            length=spec,
            previous_tail=prior,
            write_rule=style.strip() or None,
        )
    except UnresolvedCast as exc:
        _die(f"✗ 在场角色解析不了：{_reason(exc)}")

    try:
        config = ProviderConfig.from_env()
        capability = resolve_capabilities(config.base_url, config.model)
        plan = plan_call(spec, ReasoningEffort.HIGH, capability)
    except (ValidationError, ValueError, CapabilityError) as exc:
        _die(
            f"✗ 模型没配好：{_reason(exc)}\n"
            "    export NH_LLM_BASE_URL=https://api.deepseek.com\n"
            "    export NH_LLM_MODEL=deepseek-v4-flash\n"
            "    export NH_LLM_API_KEY=...   # 只从环境注入\n"
            "    export NH_LLM_TEMPERATURE=0.3"
        )

    try:
        result = generate_draft(
            messages, length=spec, config=config, plan=plan
        )
    except ProviderError as exc:
        _die(f"✗ 模型调用失败：{exc}")

    typer.echo(result.text)
    last = result.attempts[-1].result
    typer.echo(
        f"\n── {result.length.actual_units} {result.length.unit}"
        f"（{result.length.status.value}）· finish={last.finish_reason}"
        f" · attempts={len(result.attempts)} · tokens={result.completion_tokens}"
    )


@app.command("summarize")
def summarize(
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
    first: int = typer.Option(..., "--from", help="起始章号（含）"),
    last: int = typer.Option(..., "--to", help="结束章号（含）"),
) -> None:
    """后台补章节滚动总结（**会调模型、会花钱**，作者不敲这条）。

    为写作 LLM 的「更早章节」背景层生成机器摘要；同章幂等，重复跑不会重复付费。
    """
    from .draft.capabilities import (
        CapabilityError,
        ReasoningEffort,
        plan_call,
        resolve_capabilities,
    )
    from .draft.provider import ProviderConfig, complete
    from .draft.rolling_summary import (
        RollingSummarizer,
        SummaryChapterNotFound,
        SummaryGenerationError,
    )
    # 长度档和 prompt 同住 `draft/summarize.py`：HTTP 的 `POST /summary` 读的是同一个常量。
    from .draft.summarize import SUMMARY_LENGTH as summary_length

    if first < 1 or last < first:
        _die("✗ 章号区间不合法：--from 和 --to 必须是 1 ≤ from ≤ to")
    try:
        config = ProviderConfig.from_env()
        capability = resolve_capabilities(config.base_url, config.model)
    except (ValidationError, ValueError, CapabilityError) as exc:
        _die(
            f"✗ 模型没配好：{_reason(exc)}\n"
            "    export NH_LLM_BASE_URL=https://api.deepseek.com\n"
            "    export NH_LLM_MODEL=deepseek-v4-flash\n"
            "    export NH_LLM_API_KEY=...   # 只从环境注入"
        )

    def analyzer(request) -> object:
        plan = plan_call(
            summary_length,
            ReasoningEffort.OFF,
            capability,
            prompt_token_budget=len(request.prompt_bytes),
        )
        return complete(request.messages, config=config, plan=plan)

    runner = RollingSummarizer(lambda: _connect_existing(db), analyzer)
    for chapter in range(first, last + 1):
        try:
            summary = runner.ensure(project, chapter)
            preview = summary.summary if len(summary.summary) <= 40 else summary.summary[:40] + "…"
            typer.echo(f"ch{chapter}: {preview}")
        except SummaryChapterNotFound:
            typer.echo(f"ch{chapter}: 跳过（无当前快照）")
        except SummaryGenerationError as exc:
            typer.echo(f"ch{chapter}: 失败：{exc}")


@app.command()
def gate(
    db: Path = typer.Option(..., "--db", help="SQLite 库（合成小册子那本）"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
    ground_truth: Path = typer.Option(
        ..., "--ground-truth", help="synth/build.py 生成的 ground_truth.json"
    ),
    repeats: int = typer.Option(3, "--repeats", help="每条陷阱每臂跑几次。**只接受 3 或 5**"),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="jsonl 落盘路径。默认 runs/<时间戳>.jsonl；已有文件拒绝覆盖",
    ),
) -> None:
    """跑一轮「防泄漏」考试，然后出裁决。

    这场考试干的事：拿 `synth/build.py` 造出来的那本带陷阱的假书（里面谁该知道秘密、
    谁不该知道，都是提前埋好的），让引擎去续写，看它会不会"说漏嘴"——把不该知道的
    秘密写进不该出现的章节。跑完按 `docs/EVAL_PROTOCOL.md` §6（+ 九份修正案）里
    **提前定死、考完不许改**的判分规矩，出 PASS / KILL / INCONCLUSIVE。

    维护者要记住的几点：
    - **会真调模型、真花钱**（一轮约 225 个 final cell，成功轮因长度续写还会翻到两倍）。
      它只服务这场考试，小说作者一辈子不碰，别手贱连着跑。
    - 规矩在 `docs/EVAL_PROTOCOL.md`，**跑之前就冻结**。本命令不解释结果、不挑分支，
      只把 `score.decide()` 判出来的结论打印出来。
    - 退出码：PASS / KILL / INCONCLUSIVE 都是一次**有效**实验的正常结束，退出码 0
      （"KILL" 也是合法结论，别把"结果不合我意"当成"命令出错"）；只有 INVALID 才是
      仪器坏了——陷阱造错 / 判分链断了，这轮等于白跑，没产出任何证据，退出码非 0。
    """
    # 只有这条命令会用到起草层和判分层（`draft/assemble` + 整个 `eval/`）。放在函数体里，
    # 同 `nh serve` 对 uvicorn 的处理：`nh declare` 一天敲几十次，不该为一个它永远不跑的
    # 实验装置买单。
    from .draft.provider import ProviderConfig, ProviderError
    from .eval.runner import PROTOCOL_VERSION, load_traps, run_gate, stamped_path
    from .eval.score import Verdict, decide

    if "修正案 1/2/3/4/5/6/7/8" not in PROTOCOL_VERSION:
        _die(
            "✗ nh gate 已暂停：当前 runner 的 PROTOCOL_VERSION 不含修正案 1/2/3/4/5/6/7/8。\n"
            "  旧协议跑出来的 JSONL 不能用于 ADR 0009，还白花钱；"
            "等 PROTOCOL_VERSION 原子升级后再放行。"
        )

    if not ground_truth.exists():
        _die(
            f"ground truth 不存在：{ground_truth}\n"
            "它是 synth/build.py 的生成物（不入库），先造小册子再跑 gate。"
        )
    store = _open_store(db, project)

    try:
        data = json.loads(ground_truth.read_text(encoding="utf-8"))
        stated = data.get("project_id")
        if stated and stated != project:
            # 拿 A 书的 ground truth 去跑 B 书的库，`scene_view` 多半照样算得出约束
            # （另一本书里也有秘密），只是算的不是这些陷阱瞄的那些——一整轮的数字会
            # 看起来很正常，而它们不说明任何事。
            _die(
                f"ground truth 是给项目 {stated} 造的，而 --project 是 {project}。\n"
                "对不上就不跑：陷阱瞄的那些边界在另一个项目里不存在，算出来的约束是别的东西，\n"
                "而那一轮的数字看起来会完全正常。"
            )
        traps = load_traps(data)
    except (ValueError, OSError) as exc:
        _die(f"✗ 读不了 {ground_truth}：{_reason(exc)}")

    try:
        config = ProviderConfig.from_env()
    except (ValidationError, ValueError) as exc:
        _die(
            f"✗ 模型没配好：{_reason(exc)}\n"
            "  gate 要真的调模型。三个环境变量：\n"
            "    export NH_LLM_BASE_URL=https://api.deepseek.com    # DeepSeek V4\n"
            "    export NH_LLM_MODEL=deepseek-v4-flash              # flash / pro\n"
            "    export NH_LLM_API_KEY=...                          # 只从环境注入，不进 profile/JSONL\n"
            "  base_url 和 model 必须是**匹配的一对**——端点上没有这个模型名，发出去就是 404。"
        )

    try:
        from .draft.capabilities import (
            CapabilityError,
            ReasoningEffort,
            plan_call,
            resolve_capabilities,
        )
        from .draft.length import M2_LENGTH_SPEC

        capability = resolve_capabilities(config.base_url, config.model)
        plan = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)
    except CapabilityError as exc:
        _die(
            f"✗ M2 的能力计划配不起来：{_reason(exc)}\n"
            "  M2 固定请求 high reasoning，而这对 route 没有审计过的能力声明"
            "（或 high 不受支持）。\n"
            "  已冻结的 profile：deepseek-v4-flash @ https://api.deepseek.com"
            "（docs/M2_ENDPOINT_PROFILE.md，2026-08-02，不含 key）。\n"
            "  换模型就换 NH_LLM_BASE_URL / NH_LLM_MODEL 为一对**已登记**的 route；"
            "未知 route 预检失败，不静默降级。"
        )

    out_path = out if out is not None else stamped_path(Path("runs"))
    try:
        gate_input = run_gate(
            store,
            project,
            traps,
            config=config,
            plan=plan,
            repeats=repeats,
            out_path=out_path,
        )
        decision = decide(gate_input)
    except ProviderError as exc:
        _die(f"✗ 模型调用失败，这一轮没跑完：{exc}\n  已经烧掉的那些生成在 {out_path} 里。")
    except (ValueError, StoreError, UnresolvedCast) as exc:
        _die(f"✗ {_reason(exc)}")

    # 「跑了几条陷阱、几次生成」是这条命令的**成功输出本身**，不是装饰（同 `nh check` 的
    # 「跑了几条规则」）。一份零陷阱的 run 会打印一张漂亮的裁决表 + exit 0，而
    # `decide()` 那边的空集断言只在陷阱集为空时才拦得住——数字印出来，人一眼看得见。
    generations = len(gate_input.traps) * len(("x0", "x1", "x2")) * repeats
    lines_on_disk = sum(1 for _ in out_path.open(encoding="utf-8"))
    typer.echo(
        f"✓ 跑完：{len(gate_input.traps)} 条陷阱 × 3 臂 × {repeats} 次 = {generations} 次生成"
        f"（另 {len(gate_input.traps)} 次 reference 判分）。\n"
        f"  落盘 {lines_on_disk} 行：{out_path}\n"
        f"  每一次生成的**完整 prompt** 都在里面——「tell 漏进 prompt」这个最贵的错误，"
        "唯一的发现办法是人去读它（ADR 0010）。"
    )
    typer.echo(_box(f"kill-gate 裁决 · {decision.verdict.value}", _gate_lines(decision, repeats)))
    if decision.verdict is Verdict.INVALID:
        raise typer.Exit(1)


def _gate_lines(decision: GateDecision, repeats: int) -> list[str]:
    """裁决的全部依据，**逐条印出来**。

    ADR 0009 直接抄这几行，所以这里不许只印一个 verdict：一份说不出自己怎么来的裁决，
    读者没法复算，而「任何人拿同一份 runs/*.jsonl 都能重算出同一个结论」正是
    `decide()` 被写成纯函数的理由。
    """
    out = [
        f"命中规则：{decision.rule}",
        f"动作：{decision.action}",
        "",
        f"n(KNOWS)={decision.n_knows}  n(FUTURE)={decision.n_future}  重复={repeats}",
        f"X0 的 KNOWS 泄漏率：{decision.x0_knows_leak:.2f}"
        "（地板 0.50 / 天花板 0.90，落在区间外判 INVALID）",
        "",
    ]
    out += _table(
        ["比较", "n", "leak(a)", "leak(b)", "Δ", "b_only", "c_only", "p", "Holm p", "符号稳定"],
        [
            [
                c.name,
                str(c.n),
                f"{c.leak_a:.2f}",
                f"{c.leak_b:.2f}",
                f"{c.delta:+.2f}",
                str(c.b_only),
                str(c.c_only),
                f"{c.p_exact:.4f}",
                f"{decision.holm_p.get(c.name, float('nan')):.4f}",
                "✓" if decision.sign_stable.get(c.name) else "✗",
            ]
            for c in decision.comparisons
        ],
    )
    if decision.eligible_arms:
        out += ["", f"「该臂」（取到 max Δ）：{'、'.join(decision.eligible_arms)}"]
    for arm, checks in decision.pass_checks.items():
        flags = "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
        out.append(f"  {arm}：{flags}")
    if decision.future_floor:
        floor = "  ".join(f"{k}={v:.2f}" for k, v in decision.future_floor.items())
        # FUTURE 只作**描述性地板**（§3 / 修正案 4 裁定 C 的 echo 探针），不参与裁决。
        # 不写这句话，下一个读者会拿它当第二个 kill-gate。
        out += ["", f"FUTURE 泄漏率（描述性，不参与裁决）：{floor}"]
    if decision.form_pivot:
        out += ["", "FORM 重要：X2 显著优于 X1 → 生产默认翻成 NH_DRAFT_FORM=X2，重跑确认。"]
    for note in decision.notes:
        out.append(f"⚠ {note}")
    return out


def _issue_text(issue: Issue) -> str:
    """一条 issue + 它的锚。**锚是三元组，永远不是 offset**（ADR 0006）。"""
    anchor = issue.anchor
    out = [
        f"[{issue.rule}] {issue.issue_type}  第 {issue.chapter} 章 · "
        f"第 {anchor.para_index} 段 · 第 {anchor.occurrence_k} 次",
        f"  {issue.message}",
        f"  证据：{anchor.quote_text}",
    ]
    if issue.suggested_action:
        # 建议由规则确定性产出（PLAN 改 13）——在一个已知答案的地方引入 LLM 是净损失。
        out.append(f"  建议：{issue.suggested_action}")
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════
# nh declare —— 作者的声明入口
# ══════════════════════════════════════════════════════════════════════════
#
# **这九条子命令里没有一个 `--chapter` / `--valid-from` / `--since` / `--at`，
# 一个 int 型参数都没有。** 作者敲的只有一句引语；章号是「这句引语落在哪一章」的产物
# （§5.9 / 约束 10）。`tests/test_no_chapter_input.py` 把这条钉成 CI 断言——它拦的不是
# 笔误，是那个「加个 --chapter 让作者自己挑不就完了」的下午。
#
# `--who` / `--of` / `--secret` / `--loc` 收的**全是作者写的称呼原文，本文件一次都不解析**
# （§10.5 第 1 条，同 `--cast`）：解析在 `Ledger` 里走 `store.resolve()`，于是 CLI
# **没有机会**把歧义的「师兄」偷偷解析成第一个候选——它根本拿不到候选。


class AliasKindOption(StrEnum):
    """`--kind` 的合法值。**没有 canonical**，这是本地枚举而不是直接用 `AliasKind` 的
    全部理由：canonical 是 `upsert_node` 的独占物（节点的 name 就是它），从命令行递一个
    进去撞的是 `idx_alias_canonical` 给的那条读不懂的 IntegrityError。
    """

    ALIAS = "alias"
    NICKNAME = "nickname"
    TITLE = "title"


def _ledger(db: Path, project: str) -> tuple[Ledger, SqliteStoryGraph]:
    store, conn, _proj = _open_project(db, project)
    return Ledger(store, conn, project), store


def _echo_declaration(decl: Declaration, store: SqliteStoryGraph, project: str) -> None:
    for line in _declaration_lines(decl, _node_names(store, project)):
        typer.echo(line)


@declare_app.command("character")
def declare_character(
    name: str = typer.Argument(..., help="本名（显示用的那个名字）"),
    alias: list[str] = typer.Option([], "--alias", help="别名，可重复"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """声明一个人物。幂等（再声明一次 = 更新属性，不会建出第二个）。

    别名**永不合并实体**（ADR 0004）：「顾姑娘 / 清音 / 魔尊」的差异编码的正是关系阶段
    和认知边界，是 canon 不是噪声。
    """
    _declare_node(NodeLabel.CHARACTER, name, aliases=alias, db=db, project=project)


@declare_app.command("place")
def declare_place(
    name: str = typer.Argument(..., help="地点名"),
    alias: list[str] = typer.Option([], "--alias", help="别名，可重复"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """声明一个地点。`nh declare where` 的 `--loc` 认的就是它。"""
    _declare_node(NodeLabel.LOCATION, name, aliases=alias, db=db, project=project)


@declare_app.command("secret")
def declare_secret(
    name: str = typer.Argument(..., help="秘密的称呼（面板上那一列的表头）"),
    description: str = typer.Option("", "--description", help="这个秘密到底是什么"),
    sub_of: str = typer.Option("", "--sub-of", help="父秘密的**称呼**（拆子事实用）"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """声明一个秘密。**秘密是作者的意图，不是文本特征**（ADR 0004：抽取器只能猜）。

    `--sub-of` 把它挂成另一个秘密的子事实。子事实是 ADR 0005 删掉 `PARTIALLY_KNOWS`
    之后「部分知道」的唯一表达法：秘密拆子事实、每子事实一条 KNOWS。表达力相同，
    零新增边类型。
    """
    ledger, store = _ledger(db, project)
    try:
        parent_id = _resolve_parent_secret(store, project, sub_of) if sub_of else None
        node = ledger.declare_node(
            NodeLabel.SECRET,
            name,
            secret=SecretDetail(description=description, sub_of=parent_id),
        )
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    typer.echo(f"✓ 秘密「{node.name}」已声明（{node.id}）")
    if parent_id is not None:
        typer.echo(f"  ↳ 是「{sub_of}」的子事实")
    typer.secho(
        "  它现在是认知矩阵的一列，默认全员 UNKNOWN——**闭世界推导下那是一个断言**，\n"
        "  不是「查不到」。谁知道它由 nh declare knows 说，而章号由引语算。",
        fg=typer.colors.CYAN,
        err=True,
    )


def _resolve_parent_secret(store: SqliteStoryGraph, project: str, surface: str) -> str:
    """`--sub-of` 的称呼 → 父秘密的 node_id。

    ⚠️ **这是本文件唯一一处解析称呼，它是一个已知的破例。** `Ledger.declare_node` 收的
    `SecretDetail.sub_of` 是一个 node_id（扩展表的外键），而 `Ledger` 今天没有一个收
    「父秘密的称呼」的入口——于是解析只能发生在这里，也就是 §10.5 第 1 条点名说
    「会顺手挑第一个候选」的那一层。

    所以这里**逐字复刻 `Ledger._resolve_one` 的拒绝形态**（三个异常类直接从 `declare.py`
    import，不自己造），绝不挑第一个。正确的修法是给 `Ledger.declare_node` 加一个收称呼
    的 `sub_of` 参数、把这个函数删掉——那是 `declare.py` 的改动，不在本包的归属里。
    在那之前，这段代码的存在本身就是那条 TODO。
    """
    resolution = store.resolve(project, [surface])[0]
    node = resolution.unique_node
    if node is None:
        if not resolution.hits:
            raise UnknownName(surface)
        raise AmbiguousName(surface, [NodeRef.of(hit.node) for hit in resolution.hits])
    if node.label is not NodeLabel.SECRET:
        raise WrongLabel(surface, node.label, NodeLabel.SECRET)
    return node.id


def _declare_node(
    label: NodeLabel,
    name: str,
    *,
    aliases: Sequence[str],
    db: Path,
    project: str,
) -> None:
    ledger, _store = _ledger(db, project)
    try:
        node = ledger.declare_node(label, name, aliases=aliases)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    typer.echo(f"✓ {label.value}「{node.name}」已声明（{node.id}）")
    if aliases:
        typer.echo(f"  别名：{'、'.join(aliases)}")


@declare_app.command("alias")
def declare_alias(
    of: str = typer.Option(..., "--of", help="已有节点的**称呼**（本名或任一别名）"),
    surface: str = typer.Option(..., "--surface", help="要加的称呼"),
    kind: AliasKindOption = typer.Option(AliasKindOption.ALIAS, "--kind", help="别名的种类"),
    not_for_rules: bool = typer.Option(
        False, "--not-for-rules", help="规则不许拿这个称呼去正文里匹配"
    ),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """给一个已有的节点加一个称呼。

    单字别名（「音」「决」）必须带 `--not-for-rules`：拿一个字去 200 万字里做子串匹配是
    ADR 0004 点名的灾难——它会在「音信全无」「决心」上开火，而那是每章几十条误报。
    """
    ledger, _store = _ledger(db, project)
    try:
        stored = ledger.declare_alias(
            of=of,
            surface=surface,
            kind=AliasKind(kind.value),
            usable_for_rules=not not_for_rules,
        )
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(
            f"✗ 拒绝：{_reason(exc)}\n"
            "  单字别名要规则可用是不行的（ADR 0004）：一个字在 200 万字里到处都是。\n"
            "  加 --not-for-rules 就能声明它——它进花名册、面板认得它，只是规则不拿它去匹配正文。"
        )
    typer.echo(f"✓ 「{stored.surface}」（{stored.kind.value}）→ 「{of}」")
    if not stored.usable_for_rules:
        # 不印的话，`--not-for-rules` 和忘了加它长得一模一样，而两者的规则行为完全相反。
        typer.echo("  规则不会拿这个称呼去正文里匹配（--not-for-rules）。")


# ⚠️ **`nh declare knows / believes / where` 2026-08-14 删了**（同 HTTP 那三条）。
# 作者裁决：「谁知道什么 / 谁以为什么 / 谁在哪儿」只走抽取那条路。
#
# **下面这两条不是同一组。**
#
# `appears` 是 **R2 在生产上唯一的写入方，而且只能是**：`first_appears_chapter`
# 主要用法是「这东西我打算第 200 章才让它出场」——那是作者的计划，物理上不在已写
# 文本里（ADR 0004），模型读不出没写下来的意图。
#
# `dead` 2026-08-14 起**不再唯一**（抽取器有 `kind="death"` 了），但它留着当「改」
# 的入口：模型漏了或判错时，作者手上得有一条路。
#
# 删掉它们等于让 `nh check` 在规则哑火的情况下继续说「没问题」——
# `checks/__init__.py` 开头那段警告记的就是那十一天。

@declare_app.command("dead")
def declare_dead(
    who: str = typer.Option(..., "--who", help="谁（称呼原文）"),
    quote: str = typer.Option(..., "--quote", help="从正文里**复制**的、他死了的那句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """「他在这段原文里死了」。这一条之后，`nh check` 才查得出「死人还在说话」。

    引擎存的是一个机器键，不是你写的那个词——所以「陨落 / 坐化 / 兵解」怎么写都行，
    规则一个字都不去猜（它只认那个键）。生死这个维度由引擎自己建，你不用管它。
    """
    ledger, store = _ledger(db, project)
    try:
        decl = ledger.declare_dead(who=who, quote=quote)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    # 生死维度**不在花名册里**（`CANONICAL_ALIAS_LABELS` 排除 StateDim），所以
    # `_node_names` 认不出这条边的 dst，回执会把一个裸 node id 摆在作者脸上。
    # 这里按 dim_key 现问一次拿它此刻的显示名（幂等，那个节点上一行刚建过），
    # 而不是印 `HEALTH_DIM_NAME` ——作者改过名的话那就是一个假名字。
    dim = store.ensure_state_dim(project, HEALTH_DIM_KEY, HEALTH_DIM_NAME)
    for line in _declaration_lines(decl, {**_node_names(store, project), dim.id: dim.name}):
        typer.echo(line)
    typer.secho(
        f"  从第 {decl.valid_from} 章起，他再有对话标签，nh check 会报出来。",
        fg=typer.colors.CYAN,
        err=True,
    )


@declare_app.command("appears")
def declare_appears(
    of: str = typer.Option(..., "--of", help="谁 / 什么（称呼原文）"),
    quote: str = typer.Option(..., "--quote", help="从正文里**复制**的、他头一回露面的那句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """「他在这段原文里头一回露面」。这一条之后，`nh check` 才查得出提前出场。

    **这句引语在哪一章，他就是在哪一章登场**——你没有输入过那个数字，也没有一个旗标
    能让你输入它。往前的章节里再出现这个名字，就是一次提前出场。
    """
    ledger, _store = _ledger(db, project)
    try:
        first = ledger.declare_first_appearance(of=of, quote=quote)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    typer.echo(f"✓ 「{first.node.name}」的首次登场记在第 {first.chapter} 章")
    if first.previous_chapter is not None and first.previous_chapter != first.chapter:
        # 不印的话，改掉一个旧答案和第一次声明长得一模一样，而旧的那个在库里没有第二份。
        typer.echo(f"  （原来记的是第 {first.previous_chapter} 章，这次改掉了）")
    typer.secho(
        "  这个数是从你给的那句原文算出来的，你没有输入过它。",
        fg=typer.colors.CYAN,
        err=True,
    )


if __name__ == "__main__":
    app()
