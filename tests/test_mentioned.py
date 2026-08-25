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


def test_歧义称呼默认不进cast(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """「师兄」可能是两个人里的任何一个，默认这一档系统不猜（ADR 0004）。

    ── 这里原来写着「而这是安全的那一侧」，**那句话只对了一半**（2026-08-22 查清）──

    丢掉歧义称呼**只在 cast 因此变空时**才是 fail-closed（空 cast ⇒ 全禁）。
    这一场**还有别人在**的时候，丢掉的那一个人就是
    `panel/constraints.py` 记的那个真 bug 的同一种形态（「李管家静默地从 cast 里消失，
    血脉秘密从 must_not_reveal 里消失」）——方向是 **fail-open**。
    下面那条 `test_歧义候选全算在场时_禁令只多不少` 就是那个反例。

    默认值仍然是 `False`（面板那条路不动），要展开的调用方自己传。
    """
    store, pid = store_pid
    assert mentioned_cast(store, pid, ["师兄站在那里。"]) == []


def test_歧义候选全算在场_展开的是正式名(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """`expand_ambiguous=True`：「师兄」→ 两个候选**都**进来。

    进来的是各自的**正式名**，不是「师兄」两个字——原样交出去下游 `resolve_cast`
    只会把它判成 unresolved，也就是又回到全禁。
    """
    store, pid = store_pid
    assert set(mentioned_cast(store, pid, ["师兄站在那里。"], expand_ambiguous=True)) == {
        "萧决",
        "李管家",
    }


def test_展开出来的候选下游解析得动_不会又掉回全禁(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """展开的意义全在这一条上：**下游必须认得出这几个人**。

    `resolve_cast` 把解析不出唯一节点的称呼放进 `unresolved`，而 `unresolved` 非空
    ⇒ `must_not_reveal` 退化成全部秘密。展开成正式名之后这一支不再发生，
    禁说清单是按这两个人算出来的。

    「候选多 ⇒ 禁令只多不少」那个方向由
    `tests/test_agent_tools.py::test_more_candidates_only_ever_means_more_bans_never_fewer`
    钉（那儿有一个人**已经知道**那条秘密，才摆得出「不展开就少禁一条」的反例）。
    """
    from novel_harness.panel import resolve_cast

    store, pid = store_pid
    wide = mentioned_cast(store, pid, ["师兄站在那里。"], expand_ambiguous=True)
    resolved = resolve_cast(store, pid, wide)
    assert resolved.unresolved == []
    assert len(resolved.ids) == 2
    assert resolved.complete is True


def test_不可用的别名不会被展开进来(tmp_path: Path) -> None:
    """展开只放开「歧义」那一条，**不放开短别名 / 作者标了不可用的那一条**。

    `Resolution.usable_for_rules` 把 ADR 0004 的两条合并成了一个布尔值。一个字的「决」
    在正文里会疯狂误命中，把它放回来是往 cast 里灌噪声，不是补一个人——
    所以判据是「让它不可用的唯一原因是不是歧义」。
    """
    conn = connect(tmp_path / "alias.db")
    migrate(conn)
    pid = project.create(conn, name="别名书", root_path=str(tmp_path / "alias")).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    # 一个字：`AliasSpec` 强制 usable_for_rules=False（短别名不许匹配）。
    ledger.declare_alias(of="萧决", surface="决", kind=AliasKind.NICKNAME, usable_for_rules=False)
    # 歧义**且**其中一条被标了不可用 —— 不可用的原因不只歧义那一个，所以整条不展开。
    ledger.declare_alias(of="萧决", surface="那位", kind=AliasKind.TITLE)
    ledger.declare_alias(
        of="李管家", surface="那位", kind=AliasKind.TITLE, usable_for_rules=False
    )
    conn.commit()

    assert mentioned_cast(store, pid, ["决。"], expand_ambiguous=True) == []
    assert mentioned_cast(store, pid, ["那位来了。"], expand_ambiguous=True) == []


def test_展开只编译一条_alternation_最长优先没被破掉(tmp_path: Path) -> None:
    """歧义那批和不歧义那批必须进**同一条** alternation。

    分两遍扫的话，「师兄」会在「小师兄」里面也命中一次——多算仍然安全，但
    `text/mentions.py` 的最长优先是这一层唯一的机械纪律，破一处就没了。
    """
    conn = connect(tmp_path / "longest.db")
    migrate(conn)
    pid = project.create(conn, name="最长优先", root_path=str(tmp_path / "longest")).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    ledger.declare_node(NodeLabel.CHARACTER, "小师兄")
    # 「师兄」歧义（2 个人），「小师兄」是一个人的正式名且更长。
    ledger.declare_alias(of="萧决", surface="师兄", kind=AliasKind.TITLE)
    ledger.declare_alias(of="李管家", surface="师兄", kind=AliasKind.TITLE)
    conn.commit()

    assert mentioned_cast(store, pid, ["小师兄来了。"], expand_ambiguous=True) == ["小师兄"], (
        "「师兄」在「小师兄」里面又命中了一次 —— 两条 alternation 被分开扫了"
    )



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


def test_推出来的cast是被提到的那些人_不是真在场(
    store_pid: tuple[SqliteStoryGraph, str],
) -> None:
    """「被提到」⊇「真在场」：回忆里的死人、被议论的第三方都会进来。

    ⚠️ **这条今天只记录行为，不再证明它落在安全那一侧**（2026-08-24，ADR 0039）。

    它原来叫 `test_推出来的cast落在多禁那一侧_这是整件事成立的理由`，钉的是
    ADR 0018 正确性证明唯一的数学理由：判据是「在场至少有一个人还不知道 ⇒ 就禁」，
    所以多算一个人只会让更多秘密进禁说清单 —— **单调，因此 fail-closed**。

    **秘密下线之后禁说清单没了，那条论证跟着塌了。** 今天多算一个人的后果是
    「多一份人物档案进 prompt」——可能是噪声，不再明显是安全的那一侧。
    **「推导是超集所以安全」这句话需要一个新理由，或者被重新裁定。**
    """
    store, pid = store_pid

    mentioned = mentioned_cast(store, pid, ["萧决推开门。"])
    assert mentioned == ["萧决"], "正文里提到谁就是谁，不多不少"
    assert mentioned_cast(store, pid, ["风雪落了一夜。"]) == [], "一个人都没提到就是空"


def _all_secrets(store: SqliteStoryGraph, pid: str) -> list:
    seen = {}
    for resolution in store.resolve(pid, None):
        for hit in resolution.hits:
            if hit.node.label is NodeLabel.SECRET:
                seen[hit.node.id] = hit.node
    return list(seen.values())
