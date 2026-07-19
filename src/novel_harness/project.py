"""项目 —— `project` 表的唯一拥有者（PLAN §5.9 / ADR 0007）。

`nh init` 和 `scripts/seed_demo.py` 都从这里开局：一个项目行是全库其余一切的作用域
（`node.project_id` 的外键指着它，`ids.project_short()` 的短指纹派生自它的 id）。

本模块碰的不是图（`project` 不在 `tests/test_arch_guard.py` 的 `GRAPH_TABLES` 里）；
要读图走 `StoryGraph` 的五个方法。`from .db import Connection` 只做类型标注——
这跟 `decisions.py` 是同一个形状，而那个形状正是守卫 `test_only_the_assembly_layer_
opens_connections` 明文允许的那一个。**别把这个文件当模板复制到 `checks/`**：
规则收 `CheckContext`，收连接的规则不是纯函数。

── 没有 bump_canon_version，也没有任何 setter ────────────────────────────
这不是遗漏。`canon_version` 今天**零读者**（全仓库只有 SQL 的 DEFAULT 和
`graph/models.py` 的默认值 0 在提它），第一个读者是 M4 的 STALE_BASE_VERSION 冲突检测。
按 ADR 0005 的增长规则：没有消费者就不长出来——一个没人读的计数器只会在它第一次
被读的那天暴露出「这三年它一直是 0」，而那时候已经没人记得该在哪些写路径上 +1 了。
到 M4 真的要它时，连同它的读者一起加。
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


def create(conn: Connection, *, name: str, root_path: str) -> Project:
    """建一个项目。**本模块唯一的写入口。**

    id 走 `ids.new_project_id()`（两段不是三段）——`new_id(EntityType.PROJECT, ...)`
    会抛，项目无法以自身为作用域。

    没有 `canon_version` 参数：它由 SQL 的 DEFAULT 起于 0，且此后无人写它（见模块 docstring）。
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
    conn.commit()
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


def _row_to_project(row: Any) -> Project:
    # sqlite3.Row 到此为止——出参是 Pydantic 才换得掉实现（同 graph/models.py 的规矩）。
    return Project(
        id=row["id"],
        name=row["name"],
        root_path=row["root_path"],
        canon_version=row["canon_version"],
    )
