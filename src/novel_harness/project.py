"""项目 —— `project` 表的唯一拥有者（PLAN §5.9 / ADR 0007）。

`nh init` 和 `scripts/seed_demo.py` 都从这里开局：一个项目行是全库其余一切的作用域
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

from .db import Connection
from .ids import new_project_id


class Project(BaseModel):
    """一行已落库的项目。`frozen=True`：本模块没有 setter，类型层复读一遍。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    root_path: str
    canon_version: int = 0


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
    made = insert(conn, name=name, root_path=root_path)
    conn.commit()
    return made


def insert(conn: Connection, *, name: str, root_path: str) -> Project:
    """插入一个项目，但把事务的提交或回滚留给调用方。

    `project` 表仍只由本模块写；需要把项目行和其他写入放进同一事务的调用方应使用
    这个原语，普通公开创建仍应使用会提交的 `create()`。
    """
    if not name:
        raise ValueError("name 不能为空：它是作者在 nh init 之后唯一认得出这个库的东西")
    if not root_path:
        raise ValueError("root_path 不能为空：正文在磁盘上（ADR 0007），没有它就没有 chapters/")

    row = conn.execute(
        """
        INSERT INTO project (id, name, root_path)
        VALUES (?, ?, ?)
        RETURNING id, name, root_path, canon_version
        """,
        (new_project_id(), name, root_path),
    ).fetchone()
    return _row_to_project(row)


def get(conn: Connection, project_id: str) -> Project | None:
    """读一个项目；不存在返回 None。"""
    row = conn.execute(
        "SELECT id, name, root_path, canon_version FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_project(row)


def list_all(conn: Connection) -> list[Project]:
    """列出全部项目（按 name）。装配层/API 壳用它挑当前书；`project` 不是图表，读它无需走 StoryGraph。"""
    rows = conn.execute(
        "SELECT id, name, root_path, canon_version FROM project ORDER BY name"
    ).fetchall()
    return [_row_to_project(r) for r in rows]


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
    )
