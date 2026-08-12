"""`mentioned_cast` —— 把「谁在场」从作者的输入变成引擎的推导。

这组测试守的是**方向**，不是准确率。它故意不断言「推得准」——推不准是设计内的
（「被提到」是「真在场」的超集）。它断言的是：**推不准的时候错在多算那一侧**，
因为 `must_not_reveal` 的判据是「在场的人里至少有一个还不知道」，多算 = 多禁 = 少写一段，
少算 = 少禁 = 崩人设。

顺带钉住三条会静默变错的机械性质：只收 Character、按首次出现去重、歧义称呼出局。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import AliasKind, NodeLabel, NodeProps, NodeSpec, SecretDetail
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.mentioned import mentioned_cast
from novel_harness.panel import scene_constraints
from novel_harness import project


@pytest.fixture
def store_pid(tmp_path: Path) -> tuple[SqliteStoryGraph, str]:
    """花名册：两个角色 + 一个地点 + 一个秘密 + 一个歧义称呼。"""
    conn = connect(tmp_path / "book.db")
    migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(tmp_path / "book")).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "顾清音")
    ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    ledger.declare_node(NodeLabel.LOCATION, "青云城主府")
    ledger.declare_alias(of="顾清音", surface="清音", kind=AliasKind.NICKNAME)
    # 「师兄」→ 2 个人：歧义，rules_only 会把它挡在 alternation 之外。
    ledger.declare_alias(of="萧决", surface="师兄", kind=AliasKind.TITLE)
    ledger.declare_alias(of="李管家", surface="师兄", kind=AliasKind.TITLE)

    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name="血脉秘密",
            props=NodeProps.model_validate({}),
            secret=SecretDetail(),
        )
    )
    conn.commit()
    return store, pid


def test_正文里提到谁就推出谁(store_pid: tuple[SqliteStoryGraph, str]) -> None:
    store, pid = store_pid
    paras = ["萧决推开门。", "顾清音没有回头。"]
    assert mentioned_cast(store, pid, paras) == ["萧决", "顾清音"]


def test_顺序是首次出现顺序_而不是花名册顺序(store_pid: tuple[SqliteStoryGraph, str]) -> None:
    """顺序即右栏矩阵的行序，所以它必须只由正文决定，同一份正文永远得到同一个列表。"""
    store, pid = store_pid
    assert mentioned_cast(store, pid, ["顾清音先到。", "萧决后到。"]) == ["顾清音", "萧决"]
    assert mentioned_cast(store, pid, ["萧决先到。", "顾清音后到。"]) == ["萧决", "顾清音"]


def test_同一个人反复出现只算一次(store_pid: tuple[SqliteStoryGraph, str]) -> None:
    store, pid = store_pid
    assert mentioned_cast(store, pid, ["萧决。萧决。", "萧决。"]) == ["萧决"]


def test_地点和秘密不进cast(store_pid: tuple[SqliteStoryGraph, str]) -> None:
    """否则认知矩阵会长出一行「血脉秘密知道血脉秘密吗」。"""
    store, pid = store_pid
    paras = ["萧决在青云城主府听说了血脉秘密。"]
    assert mentioned_cast(store, pid, paras) == ["萧决"]


def test_歧义称呼不进cast_而且这是安全的那一侧(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """「师兄」可能是两个人里的任何一个，系统不猜（ADR 0004）。

    少认一个人的后果是**少一行矩阵、多禁一条秘密**——fail-closed。
    猜错的后果是一条本该保密的秘密从 must_not_reveal 里消失。
    """
    store, pid = store_pid
    assert mentioned_cast(store, pid, ["师兄站在那里。"]) == []


def test_一个人的两个称呼都出现时都返回_下游按人去重(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """这里不去重是对的：`resolve_cast` 按 node_id 去重，顾清音仍然是矩阵里的一行。"""
    store, pid = store_pid
    surfaces = mentioned_cast(store, pid, ["顾清音走了。", "清音回头。"])
    assert surfaces == ["顾清音", "清音"]

    from novel_harness.panel import resolve_cast

    assert len(resolve_cast(store, pid, surfaces).ids) == 1


def test_空正文和空花名册都不炸(tmp_path: Path) -> None:
    conn = connect(tmp_path / "empty.db")
    migrate(conn)
    pid = project.create(conn, name="空书", root_path=str(tmp_path / "empty")).id
    store = SqliteStoryGraph(conn)
    assert mentioned_cast(store, pid, []) == []
    assert mentioned_cast(store, pid, ["谁也不认识的一句话。"]) == []


def test_推出来的cast落在多禁那一侧_这是整件事成立的理由(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """**这条是本文件的重点。**

    「被提到」⊇「真在场」。多出来的那些人（回忆里的死人、被议论的第三方）不会让
    must_not_reveal 变短——因为判据是「至少有一个还不知道」，多一个不知道的人只会
    让更多秘密进禁说清单。

    这里同时钉住退化端：推不出人 = 空 cast = 全禁，和作者什么都没填时一模一样。
    """
    store, pid = store_pid
    secrets = {n.name for n in _all_secrets(store, pid)}
    assert secrets == {"血脉秘密"}

    nobody = scene_constraints(store, pid, 10, [])
    mentioned = scene_constraints(store, pid, 10, mentioned_cast(store, pid, ["萧决推开门。"]))

    # 一个人都没有 → 全禁（fail-closed 的退化值）
    assert {n.name for n in nobody.must_not_reveal} == {"血脉秘密"}
    # 提到了萧决，而他还不知道这条秘密 → 照样禁
    assert {n.name for n in mentioned.must_not_reveal} == {"血脉秘密"}


def _all_secrets(store: SqliteStoryGraph, pid: str) -> list:
    seen = {}
    for resolution in store.resolve(pid, None):
        for hit in resolution.hits:
            if hit.node.label is NodeLabel.SECRET:
                seen[hit.node.id] = hit.node
    return list(seen.values())
