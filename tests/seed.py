"""测试用的播种助手 —— **直接走库，不经 HTTP。**

2026-08-14 之前，几十个测试是这样造世界的：

    client.post(f"/api/projects/{pid}/declare/knows", json={...})

那三条路由那天删了（作者裁决：「谁知道什么 / 谁以为什么 / 谁在哪儿」只走抽取那条路，
见 `declare.py` 的「边」那一节）。**但那些测试要的从来不是那条路由**——它们要的是
「让世界里存在这么一条边」，然后去测别的东西（跳转坐标、改正循环、活动记录的措辞…）。

**用一条自己不测的路由去播种，本来就是个错。** 它让「那条路由还在不在」和几十个
完全无关的断言绑在一起——这次删路由，48 个测试一起红，而其中没有一个关心 declare。

所以这里给的是同一件事的库级入口：`Ledger` 的那三个方法（它们**没有作者入口**，
但仍是 `synth/build.py`、`scripts/seed_demo.py` 和这里的夹具词汇）。

用法和原来那行 `client.post` 一一对应：

    knows(book["db"], pid, who="萧决", secret="血脉秘密", quote=QUOTE)

**开的是第二条连接**，和 TestClient 那条并存（库是 WAL，`book` 夹具本来就这么干）。
每次调用自开自关，免得夹具之间互相持着未提交的事务。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_harness.db import connect
from novel_harness.declare import Ledger
from novel_harness.graph.sqlite_store import SqliteStoryGraph

__all__ = ["believes", "knows", "ledger_for", "locate", "where"]


def ledger_for(db: str | Path, pid: str) -> tuple[Ledger, Any]:
    """(ledger, conn)。**调用方负责 close** —— 下面四个包装已经替你做了。"""
    conn = connect(Path(db))
    return Ledger(SqliteStoryGraph(conn), conn, pid), conn


def knows(db: str | Path, pid: str, *, who: str, secret: str, quote: str) -> Any:
    ledger, conn = ledger_for(db, pid)
    try:
        result = ledger.declare_knows(who=who, secret=secret, quote=quote)
        conn.commit()
        return result
    finally:
        conn.close()


def believes(
    db: str | Path, pid: str, *, who: str, secret: str, believed_value: str, quote: str
) -> Any:
    ledger, conn = ledger_for(db, pid)
    try:
        result = ledger.declare_believes(
            who=who, secret=secret, believed_value=believed_value, quote=quote
        )
        conn.commit()
        return result
    finally:
        conn.close()


def where(db: str | Path, pid: str, *, who: str, loc: str, quote: str) -> Any:
    ledger, conn = ledger_for(db, pid)
    try:
        result = ledger.declare_where(who=who, loc=loc, quote=quote)
        conn.commit()
        return result
    finally:
        conn.close()


def locate(db: str | Path, pid: str, quote: str) -> list[Any]:
    """这句引语在当前正文里的全部命中。`POST …/locate` 那条路由的库级替身。"""
    ledger, conn = ledger_for(db, pid)
    try:
        return ledger.locate(quote)
    finally:
        conn.close()
