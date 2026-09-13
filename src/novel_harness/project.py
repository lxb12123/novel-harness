"""项目 —— `project` 表的唯一拥有者（PLAN §5.9 / ADR 0007）。

建库那条路（`POST /api/projects` / `api/launch.py` 首次建库）和 `scripts/seed_demo.py`
都从这里开局：一个项目行是全库其余一切的作用域
（`node.project_id` 的外键指着它，`ids.project_short()` 的短指纹派生自它的 id）。

本模块碰的不是图（`project` 不在 `tests/test_arch_guard.py` 的 `GRAPH_TABLES` 里）；
要读图走 `StoryGraph` 的五个方法。`from .db import Connection` 只做类型标注——
这跟 `decisions.py` 是同一个形状，而那个形状正是守卫 `test_only_the_assembly_layer_
opens_connections` 明文允许的那一个。**别把这个文件当模板复制到 `checks/`**：
规则收 `CheckContext`，收连接的规则不是纯函数。

── M4 唯一的版本写入是 compare-and-bump ──────────────────────────────────
M4 的第一个读者是 STALE_BASE_VERSION 冲突检测。因此这里只提供
一个严格 compare-and-bump：比较成功才 +1，不暴露 setter，也不替
业务层 commit。调用方必须把它放在写 CANON 和终态 proposal 的同一个
`BEGIN IMMEDIATE` 里，三者才能在失败时一起回滚。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .checks.catalog import CURRENT_RULESET_EPOCH, CURRENT_RULESET_HASH
from .db import Connection
from .ids import new_project_id


class Project(BaseModel):
    """一行已落库的项目。`frozen=True`：本模块没有 setter，类型层复读一遍。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    root_path: str
    canon_version: int = 0
    language: str = "zh"
    """`"zh"` 或 `"en"`。默认从正文自动判定（`text/language.py::detect_language()`），
    作者能通过 `PATCH …/language` 改——改过之后自动判定不会再覆盖它（`language_locked`
    那一列不出这个类型，纯服务端记账）。ADR 0012「书自己拥有它的库」：这一位挂在
    project 行上，不进 `~/.config/novel-harness/settings.json`——那份配置是每台机器
    一份的，而一个人可能同时有一本中文书和一本英文书。"""


class ProjectNotFound(LookupError):
    """A project-scoped operation named a project that does not exist."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"项目不存在：{project_id}")


class StaleBaseVersion(RuntimeError):
    """The caller reviewed an older canon base than the locked project row."""

    def __init__(self, project_id: str, *, expected: int, current: int) -> None:
        self.project_id = project_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"STALE_BASE_VERSION: project={project_id}, expected={expected}, current={current}"
        )


def create(conn: Connection, *, name: str, root_path: str) -> Project:
    """建一个项目并提交。`project` 表写入仍只属于本模块。

    id 走 `ids.new_project_id()`（两段不是三段）——`new_id(EntityType.PROJECT, ...)`
    会抛，项目无法以自身为作用域。

    没有 `canon_version` 参数：它由 SQL 的 DEFAULT 起于 0，之后只能由
    `compare_and_bump_canon_version()` 在同一笔 Canon 写事务中 CAS 递增。
    """
    already = conn.in_transaction
    if not already:
        conn.execute("BEGIN IMMEDIATE")
    try:
        created = insert(conn, name=name, root_path=root_path)
        if not already:
            conn.commit()
        return created
    except BaseException:
        if not already:
            conn.rollback()
        raise


def insert(conn: Connection, *, name: str, root_path: str, language: str | None = None) -> Project:
    """插入一个项目，但本函数自身绝不 BEGIN、commit 或 rollback。

    `db.connect()` 的 SQLite legacy transaction control 会在 INSERT 时隐式开启事务，
    所以调用方之后必须 commit 或 rollback；需要把项目行和其他写入原子组合时，应先
    开启外层事务。`project` 表仍只由本模块写，普通公开创建仍应使用会提交的 `create()`。

    **ruleset 基线那一行也在这儿写**（017：每个项目从出生那一刻起就有一行
    `validation_ruleset_state`，Task 5 的 attempt 冻结 ruleset 时才不会撞上缺行）。
    2026-09-13 之前它只在 `create()` 里写，而工作台的「新建 / 导入」走的是
    `onboarding.bootstrap_project()` → 本函数——于是从浏览器 / 桌面版建出来的每一本书
    都没有这一行：「分析本章」500、保存后的整理和 30 分钟扫描静默跳过（`autonomy_once`
    按项目吞异常），作者看到的只是按钮闪一下。基线跟 project 行同一笔事务，
    不给任何创建路径留「忘了写」的口子；既有的书由迁移 038 补行。

    Args:
        language: `None` = 交给 SQL 的 `DEFAULT 'zh'`（这本书还没有正文可判定，比如
            空白新书）。调用方已经能从正文推出语言时（`onboarding.bootstrap_project()`
            的 import 模式）应显式传入——这是**创建时机**的检测结果，不是覆盖：
            `language_locked` 仍然是 0，后续 sync 检测到不一样的语言时照样能改。
    """
    if not name:
        raise ValueError("书名不能为空：它是作者建好之后唯一认得出这本书的东西")
    if not root_path:
        raise ValueError("root_path 不能为空：正文在磁盘上（ADR 0007），没有它就没有 chapters/")
    if language is not None and language not in ("zh", "en"):
        raise ValueError(f"language 只能是 'zh' 或 'en'，收到 {language!r}")

    if language is None:
        row = conn.execute(
            """
            INSERT INTO project (id, name, root_path)
            VALUES (?, ?, ?)
            RETURNING id, name, root_path, canon_version, language
            """,
            (new_project_id(), name, root_path),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO project (id, name, root_path, language)
            VALUES (?, ?, ?, ?)
            RETURNING id, name, root_path, canon_version, language
            """,
            (new_project_id(), name, root_path, language),
        ).fetchone()
    # **拿的是 `CURRENT_RULESET_*`，不是历史值 `SYSTEM_RULESET_V1_HASH`**
    # （2026-08-27，删 R2 那一刀顺带发现的）：早先这里硬编码 `(1, SYSTEM_
    # RULESET_V1_HASH)`，因为在那之前 `SYSTEM_RULES` 从出生起就没变过，
    # 「epoch=1 时的历史值」和「当前值」恰好是同一个数，看不出区别。
    # 删 R2 让 031 迁移把既有项目的 epoch 推到 2，若新项目还硬编码 1，
    # 它从出生那一刻就落后于刚做完迁移的旧书——第一次校验就会被判成
    # 「ruleset 变了」。新书理应站在**当前** epoch 上，不是历史上第一次
    # 建库时的那个 epoch。
    conn.execute(
        """
        INSERT INTO validation_ruleset_state (project_id, epoch, ruleset_hash)
        VALUES (?, ?, ?)
        """,
        (row["id"], CURRENT_RULESET_EPOCH, CURRENT_RULESET_HASH),
    )
    return _row_to_project(row)


def get(conn: Connection, project_id: str) -> Project | None:
    """读一个项目；不存在返回 None。"""
    row = conn.execute(
        "SELECT id, name, root_path, canon_version, language FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_project(row)


def list_all(conn: Connection) -> list[Project]:
    """列出全部项目（按 name）。装配层/API 壳用它挑当前书；`project` 不是图表，读它无需走 StoryGraph。"""
    rows = conn.execute(
        "SELECT id, name, root_path, canon_version, language FROM project ORDER BY name"
    ).fetchall()
    return [_row_to_project(r) for r in rows]


def apply_detected_language(conn: Connection, project_id: str, language: str | None) -> None:
    """自动检测的结果。**只在作者没手动定过的时候才写**——SQL 的
    `WHERE language_locked = 0` 让这条判断原子成立，不需要先读再判断再写。

    `language is None`（样本太短，见 `text/language.py::detect_language()`）时
    整个函数是空操作：没有信号就别覆盖已有的值，哪怕那个值只是还没判定过的地板。
    本函数自成一次完整操作，调用方不需要再 commit。
    """
    if language is None:
        return
    if language not in ("zh", "en"):
        raise ValueError(f"language 只能是 'zh' 或 'en'，收到 {language!r}")
    conn.execute(
        "UPDATE project SET language = ? WHERE id = ? AND language_locked = 0",
        (language, project_id),
    )
    conn.commit()


def override_language(conn: Connection, project_id: str, language: str) -> Project:
    """作者手动改语言（`PATCH …/language` 唯一的调用方）。

    **之后自动检测永不再覆盖它**：同一笔把 `language_locked` 置 1。这是「右栏 LLM
    生成、作者可见可改」那条口径在这一位上的落点——机器猜的，人改了就听人的。
    """
    if language not in ("zh", "en"):
        raise ValueError(f"language 只能是 'zh' 或 'en'，收到 {language!r}")
    row = conn.execute(
        """
        UPDATE project SET language = ?, language_locked = 1
        WHERE id = ?
        RETURNING id, name, root_path, canon_version, language
        """,
        (language, project_id),
    ).fetchone()
    if row is None:
        raise ProjectNotFound(project_id)
    conn.commit()
    return _row_to_project(row)


def require_canon_version(conn: Connection, project_id: str) -> int:
    """Return the current canon version or fail loudly for an unknown project."""
    row = conn.execute(
        "SELECT canon_version FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ProjectNotFound(project_id)
    return int(row[0])


def compare_and_bump_canon_version(
    conn: Connection,
    project_id: str,
    expected: int,
) -> int:
    """Atomically bump exactly one version, without beginning or committing a transaction."""
    if type(expected) is not int:
        raise TypeError("expected canon_version 必须是严格 int（bool 也不接受）")
    if expected < 0:
        raise ValueError("expected canon_version 不能为负数")
    row = conn.execute(
        """
        UPDATE project
        SET canon_version = canon_version + 1
        WHERE id = ? AND canon_version = ?
        RETURNING canon_version
        """,
        (project_id, expected),
    ).fetchone()
    if row is not None:
        return int(row[0])
    current = require_canon_version(conn, project_id)
    raise StaleBaseVersion(project_id, expected=expected, current=current)


def _row_to_project(row: Any) -> Project:
    # sqlite3.Row 到此为止——出参是 Pydantic 才换得掉实现（同 graph/models.py 的规矩）。
    return Project(
        id=row["id"],
        name=row["name"],
        root_path=row["root_path"],
        canon_version=row["canon_version"],
        language=row["language"],
    )
