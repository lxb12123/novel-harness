"""测试用的播种助手 —— **直接走库，不经 HTTP。**

2026-08-14 之前，几十个测试是这样造世界的：

    client.post(f"/api/projects/{pid}/declare/knows", json={...})

那三条路由那天删了（作者裁决：「谁知道什么 / 谁以为什么 / 谁在哪儿」只走抽取那条路，
见 `declare.py` 的「边」那一节）。**但那些测试要的从来不是那条路由**——它们要的是
「让世界里存在这么一条边」，然后去测别的东西（跳转坐标、改正循环、活动记录的措辞…）。

**用一条自己不测的路由去播种，本来就是个错。** 它让「那条路由还在不在」和几十个
完全无关的断言绑在一起——这次删路由，48 个测试一起红，而其中没有一个关心 declare。

所以这里给的是同一件事的库级入口：`Ledger` 的那几个方法（它们**没有作者入口**，
但仍是 `scripts/seed_demo.py` 和这里的夹具词汇）。

用法和原来那行 `client.post` 一一对应：

    where(book["db"], pid, who="萧决", loc="北荒", quote=QUOTE)

（`knows` / `believes` 两个包装 2026-08-24 随秘密下线一起删了，ADR 0039。）

**开的是第二条连接**，和 TestClient 那条并存（库是 WAL，`book` 夹具本来就这么干）。
每次调用自开自关，免得夹具之间互相持着未提交的事务。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_harness.db import connect
from novel_harness.declare import Ledger
from novel_harness.graph.sqlite_store import SqliteStoryGraph

__all__ = ["applied_extraction", "ledger_for", "locate", "where"]


def ledger_for(db: str | Path, pid: str) -> tuple[Ledger, Any]:
    """(ledger, conn)。**调用方负责 close** —— 下面四个包装已经替你做了。"""
    conn = connect(Path(db))
    return Ledger(SqliteStoryGraph(conn), conn, pid), conn


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


def applied_extraction(db: str | Path, pid: str, chapter: int) -> None:
    """让第 `chapter` 章的抽取**看起来已经跑过**（`extraction_alignment` → `applied`）。

    ── 为什么这么多测试忽然需要它（2026-08-25）──────────────────────────────

    调度器的判据那天从「总结齐没齐」扩成「这一章该做的都做了没有」，抽取是第二维。
    于是任何一个「造几章总结 → 断言扫描收敛/不再下单」的测试，**只造总结那一半就不再
    收敛**——它们量的东西没变（总结那一维的调度），变的是「这本书还有没有别的活要干」。

    所以这个助手不是绕过守卫，是**把那些测试的前提补齐**：它们说的「这本书没活了」
    今天要两维都做完才成立。真正验新行为的那条断言在
    `test_autonomy_runtime.py::test_a_book_whose_summaries_are_all_paired_still_gets_extraction_ordered`。

    **走真的 `activate_extraction_application`，不手写那两条 INSERT**：`extraction_application`
    有 14 列、head 表走 intent CAS，手抄一份的下场是列名对不上或者 intent 序号造错
    （同 migration 027 那次「手抄漏了一列」的教训）。
    """
    from novel_harness.chapter_refresh import activate_extraction_application
    from novel_harness.ids import EntityType, new_id

    conn = connect(Path(db))
    try:
        row = conn.execute(
            "SELECT id, snapshot_generation FROM chapter WHERE project_id=? AND number=?",
            (pid, chapter),
        ).fetchone()
        if row is None:
            raise LookupError(f"第 {chapter} 章不在项目 {pid} 里")
        chapter_id, generation = str(row["id"]), int(row["snapshot_generation"] or 1)
        snapshot_id = str(
            conn.execute(
                "SELECT cs.id FROM chapter_snapshot cs "
                "JOIN chapter c ON c.id = cs.chapter_id "
                "WHERE cs.chapter_id = ? AND cs.text_sha256 = c.text_sha256 LIMIT 1",
                (chapter_id,),
            ).fetchone()["id"]
        )
        analysis_run_id = new_id(EntityType.EXTRACTION_RUN, pid)
        conn.execute(
            "INSERT INTO extraction_run (id, project_id, chapter_number, snapshot_id, "
            "status, errors_json, schema_version, prompt_hash, source_generation, "
            "required_ruleset_epoch, required_ruleset_hash) "
            "VALUES (?, ?, ?, ?, 'SUCCEEDED', '[]', 'm4.analysis.v1', ?, ?, 1, 'hash')",
            (
                analysis_run_id,
                pid,
                chapter,
                snapshot_id,
                analysis_run_id + "-prompt",
                generation,
            ),
        )
        activate_extraction_application(
            conn,
            project_id=pid,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=generation,
            analysis_run_id=analysis_run_id,
            ruleset_epoch=1,
            ruleset_hash="hash",
        )
        conn.commit()
    finally:
        conn.close()
