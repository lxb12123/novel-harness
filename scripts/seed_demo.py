"""给心跳铺一个库：README:20-31 那个世界，逐格照抄。用法 `seed_demo.py <db>`，stdout 出 project_id。

**这个文件是 M1 声明层的替身，不是它的雏形。**

节点 / 别名 / 秘密那一半现在**真的走声明层**（`project.create` + `Ledger.declare_node` /
`declare_alias`）：M1 落地之后生产侧有这条路径了，这里再手写第二份写路径就等于把被测物
换成了自己。裸 SQL 从 5 条降到 0 条。

**证据 → valid_from 那一半，它仍然是替身。** 下面那四个章号字面量绕过了
「引语 → 章号 → valid_from」那条链——本文件**没有一本书可以指**（`declare_knows` 要求
引语在正文里真的存在，而这里没有第 88 章的正文）。那条链由 `scripts/demo.sh` 的
**第二条泳道**每天量一遍：它 `nh init` → `nh import` 一本 3 章的 fixture → `nh declare
knows --quote …` → 断言 stdout 上的 `valid_from = ch3`。两条泳道各证一件事，谁都不替代谁。

⚠️ **别把守卫扫不到这里读成「这里可以查图」。** 那道守卫拦的是「第二份时态过滤」，
而本文件一行读查询都没有：它只往图里填作者本该亲手声明的东西，读那一侧全部由
`nh panel` / `nh check` 走 StoryGraph 完成——**心跳要量的正是那条读路径，
在这里自己写一句 SQL 查一遍等于把被测物换成了自己。**

── 那几个章号（88 / 103 / 120 / 150）─────────────────────────────────────

它们在真实流程里是**证据的产物**，不是谁填进去的：作者看着第 88 章的原文点确认，系统
自己写 `valid_from=88`（ADR 0006 / §10 约束 10）。本文件是「作者已经确认过了」这个状态的
替身，所以它有资格直接写这些数字——约束 10 管的是**作者的输入面**，不是 store 的写接口
（`EdgeSpec.valid_from_chapter` 一直是公开的、`ge=1` 的字段）。**而 `nh panel` 至今没有、
也不许有一个填章号的旗标。** 这两件事不矛盾：一个是已确认状态的 fixture，一个是作者的输入面。
"""

from __future__ import annotations

import sys
from pathlib import Path

from novel_harness import project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import (
    AliasKind,
    EdgeProps,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# (label, name, secret)。README 的框里出现的每一个人和每一条秘密，外加 R4 要的两个地点。
# 两条秘密带着 `SecretDetail()`：`NodeSpec` 的 validator 要求 label 与 secret 同生同死
# （没有 secret 行的 Secret 节点在认知矩阵的默认列序里根本不成列）。
_NODES: list[tuple[NodeLabel, str, SecretDetail | None]] = [
    (NodeLabel.CHARACTER, "萧决", None),
    (NodeLabel.CHARACTER, "顾清音", None),
    (NodeLabel.CHARACTER, "李管家", None),
    (NodeLabel.SECRET, "血脉秘密", SecretDetail()),
    (NodeLabel.SECRET, "玄铁令下落", SecretDetail()),
    (NodeLabel.LOCATION, "青云城主府", None),
    (NodeLabel.LOCATION, "北荒", None),
]

_EDGES: list[tuple[str, str, EdgeType, int, EdgeProps]] = [
    ("萧决", "血脉秘密", EdgeType.KNOWS, 88, EdgeProps()),
    ("萧决", "玄铁令下落", EdgeType.KNOWS, 120, EdgeProps()),
    ("李管家", "血脉秘密", EdgeType.BELIEVES, 103, EdgeProps(believed_value="已泄露")),
    ("萧决", "北荒", EdgeType.LOCATED_AT, 150, EdgeProps()),
]


def seed(path: Path) -> str:
    """建库 + 填数据，返回 project_id。"""
    conn = connect(path)
    migrate(conn)

    pid = project.create(conn, name="青云记", root_path=".").id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    # canonical 别名由 `upsert_node` 自动建，且比原来那两行手写 SQL 对：1 字名会撞
    # `CHECK (usable_for_rules = 0 OR length(surface) >= 2)`，而 upsert_node 会替它算。
    ids = {
        name: ledger.declare_node(label, name, secret=secret).id
        for label, name, secret in _NODES
    }

    # 「师兄」→ 2 个人。§3.1 点名的那个场景（一章里 8 个角色都叫「师兄」）的最小形态。
    # 歧义是**跨行**事实：两行各自完全合法，只有查询时才算得出来它指不到唯一的人。
    # 心跳靠它量 fail-closed 那一段还活着——没有这两行，那段断言测不到。
    for name in ("萧决", "李管家"):
        ledger.declare_alias(of=name, surface="师兄", kind=AliasKind.TITLE)

    for src, dst, edge_type, valid_from, props in _EDGES:
        store.upsert_edge(
            EdgeSpec(
                project_id=pid,
                src=ids[src],
                dst=ids[dst],
                type=edge_type,
                props=props,
                valid_from_chapter=valid_from,
                information_scope=InformationScope.CANON,
            )
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
