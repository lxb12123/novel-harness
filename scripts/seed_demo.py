"""给心跳铺一个库：README:20-31 那个世界，逐格照抄。用法 `seed_demo.py <db>`，stdout 出 project_id。

**这个文件是 M1 声明层的替身，不是它的雏形。** 作者声明秘密与认知的正路是 M1 的声明层，
而今天它不存在——`StoryGraph` 的五个方法一个都建不出节点，`nh import` 落不了库是同一个
原因（见 `cli.py::import_` 末尾那段）。所以这里直接写 SQL，理由和 `tests/test_cli.py::_seed`
一模一样：`tests/test_arch_guard.py` 只扫 `src/`，而生产侧根本没有那条写路径。

⚠️ **别把守卫扫不到这里读成「这里可以查图」。** 那道守卫拦的是「第二份时态过滤」，
而本文件一行读查询都没有：它只往 node / alias / secret / edge 里填作者本该亲手声明的东西，
读那一侧全部由 `nh panel` / `nh check` 走 StoryGraph 完成——**心跳要量的正是那条读路径，
在这里自己 SELECT 一次等于把被测物换成了自己。**
M1 的建节点方法落地那天，本文件应该缩成对它的一次调用。

── 那几个章号（88 / 103 / 120 / 150）─────────────────────────────────────

它们在真实流程里是**证据的产物**，不是谁填进去的：作者看着第 88 章的原文点确认，系统
自己写 `valid_from=88`（ADR 0006 / §10 约束 10）。本文件是「作者已经确认过了」这个状态的
替身，所以它有资格直接写这些数字——**而 `nh panel` 至今没有、也不许有一个填章号的旗标。**
这两件事不矛盾：一个是已确认状态的 fixture，一个是作者的输入面。
"""

from __future__ import annotations

import sys
from pathlib import Path

from novel_harness.db import Connection, connect, migrate
from novel_harness.ids import EntityType, new_id, new_project_id

# (label, name)。README 的框里出现的每一个人和每一条秘密，外加 R4 要的两个地点。
_NODES: list[tuple[str, str]] = [
    ("Character", "萧决"),
    ("Character", "顾清音"),
    ("Character", "李管家"),
    ("Secret", "血脉秘密"),
    ("Secret", "玄铁令下落"),
    ("Location", "青云城主府"),
    ("Location", "北荒"),
]


def _insert_node(conn: Connection, pid: str, label: str, name: str) -> str:
    node_id = new_id(EntityType.for_node_label(label), pid)
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES (?,?,?,?)",
        (node_id, pid, label, name),
    )
    _insert_alias(conn, pid, node_id, name, "canonical")
    return node_id


def _insert_alias(conn: Connection, pid: str, node_id: str, surface: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,?)",
        (new_id(EntityType.ALIAS, pid), pid, node_id, surface, kind),
    )


def seed(path: Path) -> str:
    """建库 + 填数据，返回 project_id。"""
    conn = connect(path)
    migrate(conn)

    pid = new_project_id()
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (pid, "青云记", "."))

    ids = {name: _insert_node(conn, pid, label, name) for label, name in _NODES}
    for name in ("血脉秘密", "玄铁令下落"):
        conn.execute("INSERT INTO secret (id, project_id) VALUES (?,?)", (ids[name], pid))

    # 「师兄」→ 2 个人。§3.1 点名的那个场景（一章里 8 个角色都叫「师兄」）的最小形态。
    # 歧义是**跨行**事实：两行各自完全合法，只有查询时才算得出来它指不到唯一的人。
    # 心跳靠它量 fail-closed 那一段还活着——没有这两行，那段断言测不到。
    for name in ("萧决", "李管家"):
        _insert_alias(conn, pid, ids[name], "师兄", "title")

    edges = [
        ("萧决", "血脉秘密", "KNOWS", 88, "{}"),
        ("萧决", "玄铁令下落", "KNOWS", 120, "{}"),
        ("李管家", "血脉秘密", "BELIEVES", 103, '{"believed_value":"已泄露"}'),
        ("萧决", "北荒", "LOCATED_AT", 150, "{}"),
    ]
    for src, dst, edge_type, valid_from, props in edges:
        conn.execute(
            "INSERT INTO edge (id, project_id, src, dst, type, props_json,"
            " valid_from_chapter, information_scope) VALUES (?,?,?,?,?,?,?,'CANON')",
            (new_id(EntityType.EDGE, pid), pid, ids[src], ids[dst], edge_type, props, valid_from),
        )

    conn.commit()
    conn.close()
    return pid


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：seed_demo.py <db 路径>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if path.exists():
        # 001_init.sql 里没有一个 IF NOT EXISTS，重跑会撞一堆 already exists；
        # 而往一个已有的库里再填一遍会让 project_id 翻倍。心跳自己在 tmp 里跑，
        # 撞上这条一定是有人拿它指了真库——那种时候删不如停。
        print(f"{path} 已存在，不覆盖。心跳请指一个临时路径。", file=sys.stderr)
        return 2

    pid = seed(path)
    # **stdout 只出 project_id**，别的话一律走 stderr：demo.sh 拿 $(...) 直接吃它。
    print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
