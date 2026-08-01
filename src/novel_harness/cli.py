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
import os
import re
import socket
import threading
import time
import unicodedata
import webbrowser
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
    UnknownName,
    WrongLabel,
)
from .graph import (
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
from .text import chapterize, parse_scenes
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
    match exc:
        case AmbiguousQuote():
            return (
                "\n  （没有 --pick，也没有 --chapter 让你直接指定第几章。那个旗标就是章号\n"
                "    输入框换了个变量名——挑错的产物是一条 valid_from 错了的 CANON 边，\n"
                "    而它在面板上长得完全正常：没有任何一条规则、任何一个面板分区、\n"
                "    任何一次 review 会发现它。）"
            )
        case AmbiguousName():
            return (
                "\n  系统不替你挑——挑错的产物是一条本该保密的秘密从 must_not_reveal 里消失，\n"
                "  而那一格在面板上长得跟「他确实不知道」一模一样。\n"
                "  用具体的名字，或者先 nh declare alias 把这个称呼指定给一个人。"
            )
        case UnknownName():
            return "\n  先声明它：nh declare character / place / secret。"
        case _:
            # `QuoteNotFound` / `WrongLabel` 及将来的新拒绝类型：异常自己的消息已经把
            # 「怎么办」说完了（QuoteNotFound 连「先跑 nh sync」都说了）。**不硬凑一条
            # 尾巴**——把同一句话说两遍会教作者跳过整段，包括他真正需要读的那半句。
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


def _bind(host: str, wanted: int) -> socket.socket:
    """绑住端口，把**绑好的** socket 交给 uvicorn（`Server.run(sockets=[...])`）。

    为什么不是「先探测一个空闲端口，再让 uvicorn 自己去绑」：探测得 bind 完再 close，
    而 close 到 uvicorn bind 之间有一个窗口，端口可能被别人抢走——那时 uvicorn 报
    「地址已被占用」，可我们已经把那个端口印在终端上、甚至已经拿它开了浏览器。
    直接把绑好的 socket 递过去，「我们知道端口号」和「端口是我们的」就成了同一件事。

    `wanted` 被占（多半是上一个 `nh serve` 还开着）→ 让内核挑一个空闲的，不报错退出：
    作者要的是「打开工作台」，不是「学习什么是端口占用」。
    """
    last: OSError | None = None
    for candidate in (wanted, 0):
        sock = socket.socket()  # AF_INET：v1 只支持 IPv4 字面量，IPv6 地址会在下面报错退出
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, candidate))
        except OSError as exc:  # 被占 / 地址不可用 / <1024 无权限
            sock.close()
            last = exc
            continue
        return sock
    _die(f"✗ 绑不上 {host}：{last}")


def _open_when_ready(url: str, host: str, port: int, timeout: float = 15.0) -> None:
    """等服务真的开始 accept 了再开浏览器。

    绑好但还没 listen 的端口会**拒绝**连接，所以这里轮询到连得上为止：立刻开浏览器
    多半只换来一张「无法访问此网站」，而服务其实半秒后就起来了——作者会以为它坏了。
    等超时都没起来就什么都不做：终端上的那行报错才是他该看的，再弹一个空白页只是添乱。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


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
    """起浏览器工作台：**一条命令** = 建库 + 起服务 + 开浏览器。

    在这之前起工作台是三步：手敲一句 `python -c "... migrate ..."` 建库、手敲 uvicorn
    并记得带上 `NH_DB`、再自己往地址栏里输端口。这条命令是 ADR 0007 那句分发叙事
    （「一条命令，不装 Docker」）的兑现，也是桌面壳的地基——**桌面版 = 这条命令 + 一个
    窗口**，不是另一套代码。

    **默认只听 127.0.0.1。** 这里没有任何认证（27 条路由全部裸奔），而库里是作者未发表
    的稿子和情节——`--host 0.0.0.0` 等于把它们摊在局域网上。要那么干的人得自己敲出来。
    """
    resolved_db = db.resolve()

    # 库不存在就建一个空的。`api/deps.py` 的 `_db_path()` **拒绝**连一个不存在的路径
    # （怕 sqlite 悄悄建出空库，给作者一张「看起来正常、全 UNKNOWN」的假矩阵），所以
    # 这一步必须由装配层做掉——cli.py 在 test_arch_guard 的 CONNECTION_OPENERS 里就是为这个。
    # 建出来的是**空书架不是空书**：工作台首屏会是「开始一本书」。
    conn = connect(resolved_db)
    try:
        migrate(conn)
    finally:
        conn.close()

    os.environ["NH_DB"] = str(resolved_db)
    if books_dir is not None:
        os.environ["NH_BOOKS_DIR"] = str(books_dir.resolve())

    # uvicorn（+uvloop/httptools）与 api.app（+FastAPI）加起来约 240ms 的导入开销。
    # 放在函数体里，好让 `nh declare` 这种一天敲几十次的命令不为一个它永远不跑的服务器买单。
    import uvicorn

    from .api.app import webui_built

    sock = _bind(host, port)
    actual = sock.getsockname()[1]
    # 0.0.0.0 是「所有网卡」，不是一个能访问的地址——别把它印进地址栏。
    browse_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{browse_host}:{actual}"

    typer.echo(url)  # ← stdout 的全部内容（同 `nh init` 的纪律：一行机器可读的东西）
    typer.secho(
        f"✓ 工作台起来了：{url}\n  库：{resolved_db}\n  停：Ctrl-C",
        fg=typer.colors.GREEN,
        err=True,
    )
    if not webui_built():
        typer.secho(
            "⚠ 前端没构建 —— 现在服务的是 api/static 的只读原型（页面能开，但功能少一半）。\n"
            "  构建一次：cd frontend && npm install && npm run build",
            fg=typer.colors.YELLOW,
            err=True,
        )

    if open_browser:
        threading.Thread(
            target=_open_when_ready, args=(url, browse_host, actual), daemon=True
        ).start()

    config = uvicorn.Config(
        "novel_harness.api.app:app", host=host, port=actual, log_level="warning"
    )
    uvicorn.Server(config).run(sockets=[sock])


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
        _die(
            f"这句话在当前正文里一处都找不到：「{quote}」\n"
            "  M1 只做逐字精确匹配（标点、空格、全半角都算）——从稿子里复制粘贴，别手打。\n"
            "  也可能是这一章还没进库：先跑 nh sync。"
        )

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

    # 一份 list，两个消费者（parse_scenes 的 para_index 和 CheckContext.paragraphs），
    # 于是 para_index 在这个进程里**只有一个含义**——这正是 parse_scenes 那句
    # 「本函数不收裸文本，只收段落」要的东西。
    # 走 anchor.paragraphs() 而不是裸 splitlines()：那个函数存在的全部理由就是让
    # 「什么是一段」只有一处定义（它的 docstring 点名的两个消费者就是这儿和证据锚）。
    # 这两者今天逐字节同解，所以这一行换过来时行为零变化——**换的是「改一处就全改」这个性质**：
    # 从前 api/app.py 走它、这儿不走，paragraphs() 一改（比如改成按空行分段）就会静默分叉，
    # R4 的 Issue 锚到隔壁段落，且是「偶尔差一两段」那种查两周的形态
    # （ADR 0006 判 offset 的那段话，一字不差地适用于 para_index 的两份定义）。
    paragraphs = split_paragraphs(file.read_text(encoding="utf-8-sig"))
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
    """跑一轮 M2 kill-gate：三臂 × N 次 → 落盘 → 打印预注册裁决表的结论。

    **这条命令会真的调模型、真的花钱**（25 条陷阱 × 3 臂 × 3 次 = 225 次生成）。
    它是仪器不是产品：小说作者永远不需要敲它，敲它的是维护者。

    裁决表冻在 `docs/EVAL_PROTOCOL.md` §6（+ 四份修正案），**跑之前就定死了**。
    这条命令不解释结果、不挑分支——它只把 `score.decide()` 的出参印出来。

    **INVALID 退出码非 0**：那一档的含义是「仪器坏了，重造陷阱重跑」，这一轮没有
    产出任何关于命题的证据。PASS / KILL / INCONCLUSIVE 都是一次有效实验的合法结论，
    退出码 0——把 KILL 判成「命令失败」等于说「结论不合我意就是出错」。
    """
    # 只有这条命令会用到起草层和判分层（`draft/assemble` + 整个 `eval/`）。放在函数体里，
    # 同 `nh serve` 对 uvicorn 的处理：`nh declare` 一天敲几十次，不该为一个它永远不跑的
    # 实验装置买单。
    from .draft.provider import ProviderConfig, ProviderError
    from .eval.runner import load_traps, run_gate, stamped_path
    from .eval.score import Verdict, decide

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
    except ValidationError as exc:
        _die(
            f"✗ 模型没配好：{_reason(exc)}\n"
            "  gate 要真的调模型。三个环境变量：\n"
            "    export NH_LLM_BASE_URL=http://localhost:11434/v1   # 本地 Ollama\n"
            "    export NH_LLM_MODEL=deepseek-chat                  # 端点上真有的模型名\n"
            "    export NH_LLM_API_KEY=...                          # 本地端点可以随便填\n"
            "  base_url 和 model 必须是**匹配的一对**——端点上没有这个模型名，发出去就是 404。"
        )

    out_path = out if out is not None else stamped_path(Path("runs"))
    try:
        gate_input = run_gate(
            store,
            project,
            traps,
            config=config,
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
# **这七条子命令里没有一个 `--chapter` / `--valid-from` / `--since` / `--at`，
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


@declare_app.command("knows")
def declare_knows(
    who: str = typer.Option(..., "--who", help="谁（称呼原文）"),
    secret: str = typer.Option(..., "--secret", help="哪个秘密（称呼原文）"),
    quote: str = typer.Option(..., "--quote", help="从正文里**复制**的、他知道了的那句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """「他在这段原文里知道了这个秘密」。

    **章号由这句引语算出来，你没有输入过它，也没有任何一个旗标能让你输入它**
    （§5.9 / 约束 10）。你确认的是「这条事实在这段原文里出现过」——看着原文点，
    零记忆负担；「那是第几章」是系统的活。
    """
    ledger, store = _ledger(db, project)
    try:
        decl = ledger.declare_knows(who=who, secret=secret, quote=quote)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    _echo_declaration(decl, store, project)


@declare_app.command("believes")
def declare_believes(
    who: str = typer.Option(..., "--who", help="谁（称呼原文）"),
    secret: str = typer.Option(..., "--secret", help="哪个秘密（称呼原文）"),
    believed: str = typer.Option(..., "--as", help="他**以为**的那个版本"),
    quote: str = typer.Option(..., "--quote", help="从正文里**复制**的那句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """「他以为的是另一个版本」——错误认知，面板上那个 ⚠。

    `--as` 是他以为的内容，面板直接渲染它（「ch103 起以为『已泄露』」）。只画一个 ⚠
    而不说他以为的是什么，等于没说。
    """
    ledger, store = _ledger(db, project)
    try:
        decl = ledger.declare_believes(who=who, secret=secret, believed_value=believed, quote=quote)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    _echo_declaration(decl, store, project)
    typer.echo(f"  他以为的是：「{believed}」")


@declare_app.command("where")
def declare_where(
    who: str = typer.Option(..., "--who", help="谁（称呼原文）"),
    loc: str = typer.Option(..., "--loc", help="哪儿（称呼原文）"),
    quote: str = typer.Option(..., "--quote", help="从正文里**复制**的、他到了那儿的那句话"),
    db: Path = typer.Option(..., "--db", help="SQLite 库"),
    project: str = typer.Option(..., "--project", "-p", help="project_id"),
) -> None:
    """「他在这段原文里到了这个地方」。

    `LOCATED_AT` 的 exclusivity 是 `single_per_src`（一个人同时只能在一个地方），所以这
    一条会**自动闭合他上一个位置**——闭到哪一章同样是算出来的（= 这条新边的 valid_from）。
    那是这个产品的招牌动作，回执里印着。
    """
    ledger, store = _ledger(db, project)
    try:
        decl = ledger.declare_where(who=who, loc=loc, quote=quote)
    except DeclarationRefused as exc:
        _die_refused(exc)
    except (ValueError, StoreError) as exc:
        _die(f"✗ 拒绝：{_reason(exc)}")
    _echo_declaration(decl, store, project)


if __name__ == "__main__":
    app()
