"""架构守卫（PLAN §8 Day 4 点名的第三组 / §5.5）。

> 「所有时态过滤只在 `graph/queries.py` 实现一次。CI 检查：`graph/` 之外任何文件
>   `import sqlite3` 直接失败——**它连 import 都拦，而不是靠纪律**。」

为什么这条值得一个测试：闭开区间的五个条件（`valid_from <= ch` / `valid_to IS NULL OR
valid_to > ch` / `scope` / `status='ACTIVE'` / `evidence_status != 'STALE'`）写第二遍时，
漏掉的一定是最后一条，而漏掉它的产物是 STALE 的边继续开火 = 「依据没了还在质疑作者」
= 最伤的那类误报 = M3 的生死线。

── 守卫的判据是「谁在碰图表」，不是「谁 import 了 sqlite3」──────────────

§5.5 的原话说的是 import，但那条判据**拦不住它自己想拦的东西**，而且是实测过的：
`db.py` 是允许名单里唯一的成员，它同时导出 `Connection` 和 `connect()`；于是 `graph/`
之外的任何文件写一行 `from ..db import Connection, connect` 就能拿到活连接、裸写第二份
时态过滤、故意漏掉 `evidence_status != 'STALE'`，而 `import sqlite3` 一次都不用出现。
实测：那样一个文件放进 `checks/`，四条守卫全绿。
允许名单看起来只有 1 个成员，但它的**传递闭包** = 「所有 import db.py 的文件」= 全仓库。

更要命的是这个绕法已经是仓库里的既有惯用法：`decisions.py` 就是 `from .db import
Connection` 然后 `conn.execute(...)`。它本身完全正当（decision_log 不是图），但它给
下一个贡献者示范了一个复制到 `checks/` 就能拿到 `SELECT * FROM edge` 的模板。

所以这里有三道，判据各不相同：

1. `test_sqlite3_stays_inside_graph`  —— 天真情形（`import sqlite3`）
2. `test_graph_tables_stay_inside_graph` —— **谁在碰图表**（SQL 字面量）
3. `test_only_the_assembly_layer_opens_connections` —— **谁能拿到连接**（`connect` 的 import）

第 2 条是真正对着 §5.5 那句话的：它拦的是「写时态查询」。第 3 条把第 2 条封死——
拿不到连接就没法执行 SQL。

── 它拦不住什么（诚实说明）──────────────────────────────────────────────

- `graph/` 里的新文件照样能写第二份时态过滤。这道守卫是**边界守卫**不是**唯一性守卫**——
  唯一性靠 review 和 `queries.TEMPORAL_WHERE` 那个显眼的常量。
- `f"SELECT * FROM {table}"` 这种拼出来的表名扫不到。没有哪道 AST 守卫是完备的；
  §6 minor #9 说 Protocol 是「自我安慰」时说的就是这种事，它缓解不消除。
  但它拦住了**顺手写出来的那一种**，而那一种正是会真的发生的那一种。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

ALLOWED = frozenset({"db.py"})
"""**允许名单必须小且明确。** 目前只有一个成员。

`db.py` 在 `graph/` 之外却必须 import sqlite3——它是 `connect()` / `migrate()` 的家，
也就是那两条 PRAGMA（`foreign_keys=ON` / `journal_mode=WAL`）和 `user_version` 闸门
的唯一实现。守卫的正确边界是「谁有资格**写时态查询**」，不是「谁有资格开连接」：
db.py 开连接、graph/ 写查询，这条线两边都不模糊。

往这个集合里加名字之前先回答一个问题：那个文件是要开连接，还是要查图？**要查图就该
走 StoryGraph**，而不是加进这里——加进来的那一刻，这份守卫就变成了一张许可证。
"""

ALLOWED_DIRS = frozenset({"graph"})
"""`graph/` 整个目录（§5.5 的原话就是「graph/ 之外」）。

注意 `models.py` / `store.py` 自己在 docstring 里承诺不 import sqlite3——出参是 Pydantic
才换得掉实现。那条承诺由下面的 `test_models_and_store_stay_sqlite_free` 钉住，
不靠这份目录级许可。
"""

GRAPH_TABLE_OWNERS = frozenset({"db.py"})
"""除 `graph/` 外，还允许在 SQL 字面量里出现图表名的文件。

`db.py` 里有 `001_init.sql` 的**读取**逻辑而非表名字面量，但迁移文件本身建的就是这些表，
所以留一个位置给它。**`decisions.py` 不在这里，也不需要在**：它只碰 `decision_log`，
那不是图——这正好说明这条判据比「谁 import 了 sqlite3」准。
"""

CONNECTION_OPENERS = frozenset(
    {"db.py", "cli.py", "__main__.py", "api/deps.py", "api/launch.py", "eval/gate.py"}
)
"""允许 `from .db import connect` 的文件：装配层。

`db.py` 是家；`cli.py` / `__main__.py` 是进程入口，它们的活就是「开库、组装、递给别人」。
`api/deps.py` 是 **FastAPI 壳的装配层**——同一份活（开库、组装 store、递给路由），只不过
入口是 HTTP 而不是命令行。`api/launch.py` 是**工作台启动器**（原 `nh serve` 的库函数版，
桌面壳的地基）：建库 + 起服务，所以它开连接。`eval/gate.py` 是 M2「防泄漏」考试的独立
入口（`python -m novel_harness.eval.gate`）：它要开库组装 store 才能跑考试，同属装配。
它们是装配面里仅有的开连接处；路由 `api/app.py` 收 `Depends(get_store)`，不碰连接，
正如 `checks/` 的规则收 `CheckContext`。加它们进来回答了守卫 docstring 那个问题：这些
文件是要**开连接**（装配），不是要**查图**（那仍然只走 StoryGraph）。

别的文件想要连接就该**收一个 StoryGraph**，而不是自己开一个——`checks/` 里的规则收
`CheckContext`，面板收 `store`，那是它们成为纯函数的原因（`checks/base.py`：
「规则不许从别处取数据——否则它就不是纯函数，也就不可复现」）。

`Connection`（只做类型标注）不受这条限制：`decisions.py` 就该那么写。
"""

GRAPH_TABLES = (
    "event_participant",
    "event_knower",
    "event_reveal",
    "proposal_event",
    "proposal_edge",
    "story_event",
    "edge_type",
    "edge",
    "node",
    "alias",
    "secret",
)
"""时态过滤碰得到的表。`edge_type` 排在 `edge` 前面：交替是 leftmost-first，
`edge` 会先匹配上 `edge_type` 的前 4 个字符——同 mentions.py 那条长度降序的理由。

`chapter` / `decision_log` / `model_call` 等**故意不在这里**：它们不是图，
碰它们不产生第二份时态过滤。守卫要拦的是那五个条件，不是「所有 SQL」。
"""

_TABLE_SQL_RE = re.compile(
    rf"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:{'|'.join(GRAPH_TABLES)})\b",
    re.IGNORECASE,
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """docstring 的 `ast.Constant` 的 id 集合。

    docstring 传不进 `conn.execute()`，所以扫它是纯误报——而**守卫误报会被人关掉，
    关掉的守卫等于没有**。本文件自己的模块 docstring 就引着 `SELECT * FROM edge`。
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def sqlite3_importers(source: str, filename: str = "<probe>") -> list[int]:
    """返回 import 了 sqlite3 的行号。

    用 AST 不用 grep：正则会把注释、docstring 和字符串里的「import sqlite3」一起算进去
    （比如本文件），而那些不是 import。
    """
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "sqlite3" or a.name.startswith("sqlite3.") for a in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "sqlite3" or mod.startswith("sqlite3."):
                lines.append(node.lineno)
    return sorted(lines)


def graph_table_sql(source: str, filename: str = "<probe>") -> list[int]:
    """返回**碰了图表**的字符串字面量的行号（docstring 不算）。

    这才是 §5.5 真正想圈的东西：`SELECT * FROM edge WHERE valid_from_chapter <= ?`
    是第二份时态过滤，不管它是怎么拿到连接的。f-string 的常量段也扫得到
    （`f"SELECT {cols} FROM edge WHERE ..."` 里那个 `" FROM edge WHERE ..."`）。
    """
    tree = ast.parse(source, filename=filename)
    skip = _docstring_nodes(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        if isinstance(node.value, str) and _TABLE_SQL_RE.search(node.value):
            lines.append(node.lineno)
    return sorted(set(lines))


def db_connect_importers(source: str, filename: str = "<probe>") -> list[int]:
    """返回把 `connect` 从 db 模块里拿出来的行号。

    三种形状都算，因为三种都给得到活连接：
    `from .db import connect` / `from novel_harness.db import connect` /
    `from . import db`（然后 `db.connect(...)`）/ `import novel_harness.db`。
    **只 import `Connection`（类型标注）不算**——那正是 decisions.py 该有的样子。
    """
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in {"novel_harness.db"} for a in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            # `from .db import ...` / `from ..db import ...` / `from novel_harness.db import ...`
            if mod == "db" or mod.endswith(".db") or mod == "novel_harness.db":
                if names - {"Connection"}:
                    lines.append(node.lineno)
            # `from . import db` / `from .. import db`
            elif node.level > 0 and "db" in names:
                lines.append(node.lineno)
    return sorted(set(lines))


def _is_allowed(rel: Path, files: frozenset[str]) -> bool:
    return rel.as_posix() in files or (len(rel.parts) > 1 and rel.parts[0] in ALLOWED_DIRS)


def _scan(check, allowed: frozenset[str]) -> list[str]:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if _is_allowed(rel, allowed):
            continue
        for lineno in check(path.read_text(encoding="utf-8"), str(rel)):
            offenders.append(f"{rel}:{lineno}")
    return offenders


# ══════════════════════════════════════════════════════════════════════════
# 三道守卫
# ══════════════════════════════════════════════════════════════════════════


def test_sqlite3_stays_inside_graph() -> None:
    offenders = _scan(sqlite3_importers, ALLOWED)
    assert not offenders, (
        f"这些文件在 graph/ 之外 import 了 sqlite3：{offenders}。\n"
        "时态过滤只允许在 graph/queries.py 出现一次（PLAN §5.5）。要读图请走 "
        "StoryGraph 的五个方法；真的需要开连接就走 db.py。"
    )


def test_graph_tables_stay_inside_graph() -> None:
    """**这条才是对着 §5.5 那句话的。** 判据是「谁在碰图表」，不是「谁 import 了 sqlite3」。

    守卫的 docstring 一直写着「守卫的正确边界是『谁有资格写时态查询』，不是『谁有资格
    开连接』」——但代码曾经按后者实现，于是 `from ..db import Connection, connect` +
    `SELECT * FROM edge WHERE valid_from_chapter <= ?`（漏掉 STALE 那条）全绿通过。
    """
    offenders = _scan(graph_table_sql, GRAPH_TABLE_OWNERS)
    assert not offenders, (
        f"这些文件在 graph/ 之外对图表写了 SQL：{offenders}。\n"
        "时态过滤的五个条件只允许在 graph/queries.py 出现一次（PLAN §5.5）——"
        "写第二遍时漏掉的一定是 evidence_status != 'STALE'，而它的产物是 STALE 的边"
        "继续开火 = 最伤的那类误报 = M3 的生死线。要读图请走 StoryGraph 的五个方法。"
    )


def test_only_the_assembly_layer_opens_connections() -> None:
    """拿不到连接，上面那条就绕不过去。

    `from .db import Connection`（类型标注）不受限——decisions.py 就该那么写，
    它碰的是 decision_log，那不是图。
    """
    offenders = _scan(db_connect_importers, CONNECTION_OPENERS)
    assert not offenders, (
        f"这些文件从 db 里拿了 connect（或整个 db 模块）：{offenders}。\n"
        f"只有装配层（{sorted(CONNECTION_OPENERS)}）有资格开连接。别的地方要数据就"
        "**收一个 StoryGraph**——那是 checks/ 的规则能是纯函数的原因。\n"
        "只做类型标注的话 `from .db import Connection` 是允许的。"
    )


def test_models_and_store_stay_sqlite_free() -> None:
    # 契约层的两个文件在 docstring 里承诺过这件事。它是 StoryGraph 唯一的真实价值：
    # 出参是 Pydantic 就换得掉实现，出参是 sqlite3.Row 就换不掉。
    for name in ("models.py", "store.py"):
        src = (SRC / "graph" / name).read_text(encoding="utf-8")
        assert not sqlite3_importers(src, name), f"graph/{name} 不该 import sqlite3"


def test_allowlist_stays_small() -> None:
    # 不是洁癖：这份名单每长一个成员，「时态过滤只写一次」就多一个可以被绕开的地方。
    # 要加成员，先在 PR 里回答「它是要开连接还是要查图」。
    assert ALLOWED == frozenset({"db.py"})
    assert ALLOWED_DIRS == frozenset({"graph"})
    assert GRAPH_TABLE_OWNERS == frozenset({"db.py"})
    # api/deps.py 是 FastAPI 壳的装配层（唯一开连接处），api/launch.py 是启动器，
    # eval/gate.py 是 M2 考试入口，均与 cli.py 同性质。
    assert CONNECTION_OPENERS == frozenset(
        {
            "db.py",
            "cli.py",
            "__main__.py",
            "api/deps.py",
            "api/launch.py",
            "eval/gate.py",
        }
    )
    # 同理：遮蔽豁免名单长一个，就多一个「模块取不到」的地方。
    assert SHADOW_GRANDFATHERED == frozenset({"novel_harness.text.chapterize"})


def test_event_hyperedge_tables_are_inside_the_graph_sql_boundary() -> None:
    expected = {
        "story_event",
        "event_participant",
        "event_knower",
        "event_reveal",
        "proposal_event",
        "proposal_edge",
    }
    assert expected <= set(GRAPH_TABLES)
    for table in expected:
        assert graph_table_sql(f'sql = "SELECT * FROM {table}"') == [1]


def test_story_graph_method_set_remains_frozen() -> None:
    from novel_harness.graph.store import StoryGraph

    methods = {
        name
        for name, value in StoryGraph.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
    assert methods == {
        "resolve",
        "canon_version",
        "knowledge_edges_at",
        "state_at",
        "knowledge_matrix",
        "subgraph",
        "upsert_edge",
    }


def test_events_package_imports_in_a_cold_interpreter() -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", "import novel_harness.events"],
        cwd=SRC.parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


SHADOW_GRANDFATHERED = frozenset({"novel_harness.text.chapterize"})
"""下面那条守卫的既有例外。**只有一个成员，加第二个要在 PR 里回答「为什么不能改名」。**

`chapterize` 这个函数名比包早：`importer.py` / `cli.py` / `test_importer.py` 都在用
`from .text import chapterize`，改成模块优先要动三处调用方，而收益是零——
`text/__init__` 已经把 `CHAPTER_RE` / `normalize` / `Chapter` 全部再出口了，
`scripts/probe_speaker_tags.py` 走的又是 `from novel_harness.text.chapterize import ...`
（这个写法不吃包属性，走 `sys.modules` 的尾模块，不受遮蔽影响）。
**也就是说这一处遮蔽今天没有让任何东西取不到。** 新写的模块不许再进这份名单。
"""


def test_no_package_export_shadows_a_submodule() -> None:
    """包级导出不许和子模块**同名**——同名会把那个子模块彻底遮死。

    `eval/__init__.py` 里一句 `from .confound_lint import confound_lint` 就够：
    import 机制先把子模块挂成包属性，紧接着这条 from-import 用函数覆盖掉它，此后

        from novel_harness.eval import confound_lint      # → 函数
        import novel_harness.eval.confound_lint as mod    # → 也是函数（3.7+ 先走 getattr）
        mod.LEN_TOLERANCE                                 # → AttributeError

    模块从此**没有任何一种写法拿得到**。这个坑实际踩过一次（2026-07-30 加包级导出时，
    `tests/test_confound_lint.py` 当场红），`draft/assemble.py` 的 `assemble` 是同一个形状。
    规矩因此是：与子模块同名的可调用对象一律不上包级，要它就从子模块直接 import。
    """
    import importlib
    import types

    offenders: list[str] = []
    for init in sorted(SRC.rglob("__init__.py")):
        pkg_dir = init.parent
        dotted = "novel_harness" + "".join(f".{p}" for p in pkg_dir.relative_to(SRC).parts)
        pkg = importlib.import_module(dotted)
        for child in sorted(pkg_dir.glob("*.py")):
            if child.stem == "__init__":
                continue
            full = f"{dotted}.{child.stem}"
            if full in SHADOW_GRANDFATHERED:
                continue
            bound = getattr(pkg, child.stem, None)
            if bound is not None and not isinstance(bound, types.ModuleType):
                offenders.append(f"{full} 被 {type(bound).__name__} 遮住了")
    assert not offenders, (
        "包级导出遮住了同名子模块，那个子模块从此取不到：\n  "
        + "\n  ".join(offenders)
        + "\n改 __init__.py 把这个名字从导出里拿掉（子模块名优先），别改这条断言。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 守卫自己的守卫
# ══════════════════════════════════════════════════════════════════════════
#
# **一个永远绿的守卫比没有守卫更糟**，因为它还提供安全感。上一版的自守卫只验证了
# 「扫描器找得到 graph/ 里的 import」——它没验证「扫描器看得见绕法」，而绕法正是
# 实际发生的那一种。所以下面把那个真实的绕法作为 fixture 喂进扫描器。

NAIVE_PROBE = """
from __future__ import annotations
import sqlite3
def x(c: sqlite3.Connection) -> None: ...
"""

BYPASS_PROBE = '''
from __future__ import annotations
from ..db import Connection, connect

def bypass(conn: Connection, pid: str, ch: int) -> list:
    """零 import sqlite3，第二份时态过滤，还故意漏掉 evidence_status != 'STALE'。"""
    return conn.execute(
        "SELECT * FROM edge WHERE project_id=? AND valid_from_chapter<=?"
        " AND (valid_to_chapter IS NULL OR valid_to_chapter>?)", (pid, ch, ch),
    ).fetchall()

def open_my_own(path: str) -> Connection:
    return connect(path)
'''

TYPE_ONLY_PROBE = """
from __future__ import annotations
from .db import Connection

def read(conn: Connection) -> list:
    return conn.execute("SELECT * FROM decision_log WHERE project_id = ?", ("p",)).fetchall()
"""


def test_the_guard_can_actually_see_the_offenders() -> None:
    # 扫描器要是把 src 找错了地方（或者 AST 判空），上面那些会永远绿着通过。
    assert (SRC / "graph" / "queries.py").exists()
    assert sqlite3_importers((SRC / "graph" / "sqlite_store.py").read_text(encoding="utf-8"))
    assert sqlite3_importers((SRC / "graph" / "queries.py").read_text(encoding="utf-8"))
    assert graph_table_sql((SRC / "graph" / "queries.py").read_text(encoding="utf-8"))


def test_the_guard_can_see_the_naive_case() -> None:
    assert sqlite3_importers(NAIVE_PROBE) == [3]


def test_the_guard_can_see_the_bypass() -> None:
    """**这条是这个文件存在的理由。** 实测过：修之前这个 probe 放进 checks/ 是 4 passed。"""
    assert sqlite3_importers(BYPASS_PROBE) == []  # 绕法不 import sqlite3——这正是它绕得过的原因
    assert graph_table_sql(BYPASS_PROBE), "扫描器必须看见 SELECT * FROM edge"
    assert db_connect_importers(BYPASS_PROBE), "扫描器必须看见它把 connect 拿走了"


def test_the_guard_does_not_cry_wolf_on_decisions_py() -> None:
    """decisions.py 那种写法必须是绿的：它 `from .db import Connection` 且只碰
    decision_log。**误报会让人把守卫关掉**，而 decision_log 不是图。"""
    assert graph_table_sql(TYPE_ONLY_PROBE) == []
    assert db_connect_importers(TYPE_ONLY_PROBE) == []
    real = (SRC / "decisions.py").read_text(encoding="utf-8")
    assert graph_table_sql(real, "decisions.py") == []
    assert db_connect_importers(real, "decisions.py") == []


def test_the_guard_ignores_docstrings() -> None:
    # 本文件的模块 docstring 自己就引着 SELECT * FROM edge。docstring 传不进 execute()。
    doc_only = '"""见 queries.py：SELECT * FROM edge WHERE valid_from_chapter <= :ch。"""\n'
    assert graph_table_sql(doc_only) == []
    real_sql = 'x = "SELECT * FROM edge WHERE valid_from_chapter <= 1"\n'
    assert graph_table_sql(real_sql) == [1]
