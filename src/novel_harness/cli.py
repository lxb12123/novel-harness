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
   任何一个能让作者写 `valid_from` 的旗标。

3. **静默的零和真的零不许长得一样**（§10 约束 8 / `checks/__init__.py:29`）。
   一张零行的矩阵、一个没有秘密的项目、一个空库——在终端上全都长得像「一切正常」。
   本文件里每一处 `_die()` 都在拆这一类等号，它们不是防御性编程。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import typer

from . import __version__
from .checks import ALL_CHECKS, CheckContext, Issue, run_checks
from .db import connect
from .graph import InformationScope, KnowledgeCell, KnowledgeMatrix, KnowledgeState, StoreError
from .graph.sqlite_store import SqliteStoryGraph
from .panel import SceneConstraints, knowledge_matrix, resolve_cast, scene_constraints
from .text import chapterize, parse_scenes

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Novel Harness — 知道谁在第几章还不该知道什么。",
)

_CAST_SEP_RE = re.compile(r"[,，、]")
"""`--cast 萧决,顾清音、李管家` 的分隔符：半角逗号 / 全角逗号 / 顿号。

跟 `text/scenes.py` 认的是同一组，且理由相同（三个都是中文作者会打出来的），
但**不共用那个私有常量**：命令行和场景块是两个不同的输入面，作者在 shell 里敲的东西
还要过一层 shell 分词。两处同时改的日子来临时，这里该跟着改，不该是自动跟着变。
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


def _open_store(db: Path, project: str) -> SqliteStoryGraph:
    """开库 + 组装 + **确认这个项目真的有东西**。

    两道闸门，都在拆「静默的零 == 真的零」那个等号：

    1. **库文件不存在就死。** `connect()` 会把它**建出来**（还顺手 mkdir 父目录），
       于是一个敲错的路径会得到一个空库，然后面板理直气壮地画出一张「在场三个人对
       全部秘密一无所知」——闭世界推导下 UNKNOWN 是**断言**不是「查不到」。
       打错一个字母的代价不该是一个看起来完全正常的错误答案。
    2. **花名册为空就死。** 走 `resolve(project)`（`surfaces=None` = 全项目花名册）——
       它是 `StoryGraph` 五个方法里唯一能回答「这个项目里有东西吗」的那个，而
       「查一下 project 表」在这里是不许的（本文件不写 SQL）。空花名册 = 项目号打错了
       或者一条声明都没有，两者都不该画面板。
    """
    if not db.exists():
        _die(
            f"库不存在：{db}\n"
            "不替你建：connect() 建得出一个空库，而空库上的面板会画出一张「谁都不知道」的\n"
            "矩阵——闭世界推导下那是一个**断言**，不是一个空结果。一个敲错的路径不该长得\n"
            "像一个正确的答案。"
        )
    store = SqliteStoryGraph(connect(db))
    if not store.resolve(project):
        _die(
            f"项目 {project} 在 {db} 里没有任何花名册行。\n"
            "要么 project_id 打错了，要么这本书还一条声明都没有（那是 M1 的声明层，"
            "还不存在）。\n"
            "无论哪种，画出来的都会是一张零行矩阵——它长得像「没问题」，其实是「没数据」。"
        )
    return store


def _split_cast(raw: str) -> list[str]:
    """`--cast` 原文 → 称呼列表。**只切分，不解析**（§10.5 第 1 条）。"""
    return [name.strip() for name in _CAST_SEP_RE.split(raw) if name.strip()]


# ══════════════════════════════════════════════════════════════════════════
# 命令
# ══════════════════════════════════════════════════════════════════════════


@app.command()
def version() -> None:
    """打印版本。"""
    typer.echo(__version__)


@app.command("import")
def import_(
    path: Path = typer.Argument(..., help="小说 TXT"),
) -> None:
    """切章并**对账**：切出来几章、每章的标题行长什么样。

    ⚠️ **它现在还落不了库，而且这不是「没写完」，是一条不存在的写路径。** 详见下面
    那段 `_die`——把它做成一个安静地不落库的命令是这里最坏的选择。
    """
    if not path.exists():
        _die(f"文件不存在：{path}")

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
    _die(
        "\n落库没做，而且它不是「没写完」——图层没有写节点的路径。\n"
        "  `StoryGraph` 只有五个方法（resolve / state_at / knowledge_matrix / subgraph /\n"
        "  upsert_edge），一个都建不出 Chapter 节点；而 chapter 表的主键就是那个节点的 id，\n"
        "  所以落一章 = 先建一个节点。cli.py 自己往图表里插行是被\n"
        "  `tests/test_arch_guard.py` 明确拦掉的（它是装配层，不是图层），\n"
        "  而把 cli.py 加进 GRAPH_TABLE_OWNERS 就是把守卫变成许可证。\n"
        "  正确的下一步是给图层加一个建节点的方法（M1 的声明层要它，不止导入器要它），\n"
        "  不是在这里绕过守卫。\n"
        "  在那之前，上面那份对账是这个命令能诚实给出的全部东西。"
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

    `--file` 而不是「从库里取第 N 章的正文」：正文在磁盘上（ADR 0007），而把章号映射到
    路径要 `chapter.path`——那是导入器落的库，`nh import` 现在落不了（见它的输出）。
    所以这里让作者直接指稿子，这条链路今天就能通。
    """
    if not file.exists():
        _die(f"稿子不存在：{file}")
    store = _open_store(db, project)

    # 一份 list，两个消费者（parse_scenes 的 para_index 和 CheckContext.paragraphs），
    # 于是 para_index 在这个进程里**只有一个含义**——这正是 parse_scenes 那句
    # 「本函数不收裸文本，只收段落」要的东西。
    # ⚠️ 但「什么是一段」在本仓库还没有唯一定义：chapterize.py 提到的 text/anchor.py
    # 尚不存在。M3 它落地时，这一行必须换成调它，**且两个消费者要一起换**——只换一个，
    # R4 的 Issue 就会锚到隔壁段落，且是「偶尔差一两段」那种查两周的形态（ADR 0006 对
    # offset 的判词，一字不差地适用于 para_index 的两份定义）。
    paragraphs = file.read_text(encoding="utf-8-sig").splitlines()
    scenes = parse_scenes(paragraphs)
    if not scenes:
        _die(
            f"{file} 里一个场景块都没有（要的是 `## 场景 N` + 下一非空行的 "
            "`<!-- nh: cast=... loc=... -->`）。\n"
            "**没有场景块 = R4 无事可做 = 必然零 issue**，而那个零和「这一章没问题」\n"
            "在终端上一模一样。不让它冒充体检报告。"
        )

    ctx = CheckContext(
        store=store,
        project_id=project,
        chapter=chapter,
        scenes=tuple(scenes),
        paragraphs=paragraphs,
    )
    issues = run_checks(ctx)

    # 「跑了几条规则」必须印出来，且这是本命令唯一的成功输出。§10 约束 8 /
    # checks/__init__.py:29：静默的零和真的零不许长得一样。v1 的 ALL_CHECKS 只有 R4
    # （R2/R3/R5 在 M3），所以「无 issue」的真实含义是「R4 没意见」，不是「这章没问题」——
    # 沉默的工具死得比吵闹的工具更快，只是死得更安静。
    typer.echo(
        f"第 {chapter} 章：{len(scenes)} 个场景块，跑了 {len(ALL_CHECKS)} 条规则"
        f"（{', '.join(c.__module__.rsplit('.', 1)[-1] for c in ALL_CHECKS)}），"
        f"{len(issues)} 条 issue。"
    )
    if not issues:
        return
    for issue in issues:
        typer.echo("")
        typer.echo(_issue_text(issue))
    raise typer.Exit(1)


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


if __name__ == "__main__":
    app()
