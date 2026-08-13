"""总结的倒排索引（T6）—— **每一段总结是一个可反查的记忆点**。

作者要的是「迅速找到相关章节的总结，然后引用、对比、调研」，且明说**不用 RAG**。
所以这里量的从头到尾只有一件事：**连线对不对**，而不是「像不像」。

这个文件钉的是四类会**不报错**的坏结局：

1. **索引跟不上总结。** 作者改了一段 / 撤回了一段 / 重新生成了一段，而反查还指着
   旧的那一段——屏幕上一切正常，答案是错的。这是本次设计里最贵的一条。
2. **索引跟不上花名册。** 作者刚建的人物在总结里永远搜不到（静默的假空，§10 约束 8）。
3. **秘密顺着芯片泄出去。** 这批命中里按定义就有 Secret，而 `Node.props` 装的正是内容。
4. **零和零分不开。** 「他没在任何总结里出现过」和「这个 id 根本不存在」下一步动作相反。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness.db import connect
from novel_harness.draft.rolling_summary import (
    retract_summary,
    save_author_summary,
)
from novel_harness.graph import AliasKind, AliasSpec, NodeLabel, NodeNotFound
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.summary_index import (
    INDEXED_LABELS,
    chapters_mentioning,
    ensure_index,
    mentions_in_chapter,
    roster_hash,
)

from test_api import TWIST

# 库里塞总结走 `save_author_summary`（作者档），不走 `RollingSummarizer`：后者要模型。
# **两者在库里完全同形**（迁移 013 的原话），索引这一层看不出区别。


def _seed(conn: Any, pid: str, chapter: int, text: str) -> None:
    save_author_summary(conn, project_id=pid, chapter_number=chapter, text=text)


@pytest.fixture
def wired(book: dict[str, str]) -> Any:
    """一条连着的连接 + store + pid。**用真库**（`book` fixture 那本，两章正文都在）。"""
    conn = connect(book["db"])
    store = SqliteStoryGraph(conn)
    try:
        yield conn, store, book["pid"], book
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 1. 连线本身
# ══════════════════════════════════════════════════════════════════════════


def test_a_summary_links_to_whoever_it_names(wired: Any) -> None:
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府听说了血脉秘密。")

    hits = mentions_in_chapter(conn, store, pid, 1)
    assert {hit.node.name for hit in hits} == {"萧决", "青云城主府", "血脉秘密"}
    assert [hit.node.name for hit in hits] == ["萧决", "青云城主府", "血脉秘密"], (
        "芯片按 NodeLabel 的声明顺序分组（人物 → 地点 → 秘密），组内按名字排。"
        "顺序不稳 = 同一份数据每次刷新长得不一样。"
    )


def test_it_reports_which_nickname_was_used_not_just_who(wired: Any) -> None:
    """「魔尊」和「萧决」在这一段里是**两件事**（ADR 0004：别名差异是 canon 不是噪声）。"""
    conn, store, pid, book = wired
    store.add_alias(
        AliasSpec(project_id=pid, node_id=book["萧决"], surface="魔尊", kind=AliasKind.TITLE)
    )
    conn.commit()
    _seed(conn, pid, 1, "那一夜魔尊出手了。")

    hits = mentions_in_chapter(conn, store, pid, 1)
    assert [(hit.node.name, hit.surfaces) for hit in hits] == [("萧决", ["魔尊"])]


def test_the_reverse_lookup_lists_chapters_in_order(wired: Any) -> None:
    """**这就是作者要的那件事**：点一个东西 → 还有哪几章的总结提到它，按章号排。"""
    conn, store, pid, book = wired
    _seed(conn, pid, 2, "萧决离开青云城主府。")
    _seed(conn, pid, 1, "萧决在青云城主府听说了血脉秘密。")

    found = chapters_mentioning(conn, store, pid, book["萧决"])
    assert found.node.name == "萧决"
    assert [row.chapter_number for row in found.chapters] == [1, 2]
    # 反查带回**原文**：作者点开是为了读它、比它、引它，不是为了知道它存在。
    assert found.chapters[0].summary.startswith("萧决在青云城主府")


def test_longest_surface_wins(wired: Any) -> None:
    """`顾清音` 不会被切成 `清音` —— 和 R2 共用 `compile_alternation` 的那条纪律。"""
    conn, store, pid, book = wired
    store.add_alias(
        AliasSpec(project_id=pid, node_id=book["李管家"], surface="管家", kind=AliasKind.TITLE)
    )
    conn.commit()
    _seed(conn, pid, 1, "李管家什么也没说。")

    hits = mentions_in_chapter(conn, store, pid, 1)
    assert [hit.surfaces for hit in hits] == [["李管家"]], "命中的必须是长的那个"


def test_ambiguous_surfaces_never_enter_the_index(wired: Any) -> None:
    """「师兄」同时指向 2 个人 ⇒ `rules_only` 把它挡在 alternation 之外。

    **不是这里语义过滤**，是它根本没进那条正则（同 `mentioned.py` 的注释）。
    """
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "师兄一言不发。")
    assert mentions_in_chapter(conn, store, pid, 1) == []


def test_engine_internal_labels_stay_out(wired: Any) -> None:
    """`StateDim` / `Chapter` 不进倒排表 —— 每段总结挂一个「修为（状态）」是纯噪声。"""
    assert NodeLabel.STATE_DIM not in INDEXED_LABELS
    assert NodeLabel.CHAPTER not in INDEXED_LABELS
    conn, store, pid, book = wired
    # 导入器给每一章建了一个 Chapter 节点（第 1 章的 name 是「血脉」）。
    _seed(conn, pid, 1, "血脉秘密在这一章被说破了。")
    assert [hit.node.label for hit in mentions_in_chapter(conn, store, pid, 1)] == [
        NodeLabel.SECRET
    ], "命中的该是「血脉秘密」那个 Secret，不是标题叫「血脉」的 Chapter 节点"


# ══════════════════════════════════════════════════════════════════════════
# 2. 索引什么时候重建 —— **本任务最重要的那条设计**
# ══════════════════════════════════════════════════════════════════════════


def test_editing_a_summary_moves_the_index_with_it(wired: Any) -> None:
    """作者改了一段字 ⇒ 反查跟着走。**旧的那一段不许还挂在索引上。**"""
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府。")
    assert [r.chapter_number for r in chapters_mentioning(conn, store, pid, book["萧决"]).chapters]

    _seed(conn, pid, 1, "李管家一个人守着院子。")

    assert chapters_mentioning(conn, store, pid, book["萧决"]).chapters == [], (
        "改完之后第 1 章不再提到萧决——而 `chapter_summary` 是 append-only，"
        "旧那一行还在库里。索引不跟着走的话，反查会指着一段作者已经改掉的字。"
    )
    assert [
        r.chapter_number
        for r in chapters_mentioning(conn, store, pid, book["李管家"]).chapters
    ] == [1]


def test_retracting_a_summary_takes_it_out_of_the_reverse_lookup(wired: Any) -> None:
    """撤回的语义定死为「这一章当作没总结」——起草不带它，**反查也不许还列着它**。"""
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府。")
    retract_summary(conn, project_id=pid, chapter_number=1)

    assert chapters_mentioning(conn, store, pid, book["萧决"]).chapters == []
    assert mentions_in_chapter(conn, store, pid, 1) == []


def test_a_retracted_chapter_comes_back_when_it_is_written_again(wired: Any) -> None:
    """撤回 → 再写一段 ⇒ 反查回来。「删了重来」那条退路在这一层也得成立。"""
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府。")
    retract_summary(conn, project_id=pid, chapter_number=1)
    _seed(conn, pid, 1, "萧决又回来了。")

    assert [
        r.chapter_number for r in chapters_mentioning(conn, store, pid, book["萧决"]).chapters
    ] == [1]


def test_a_new_alias_makes_old_summaries_searchable(wired: Any) -> None:
    """**花名册变了的那一档。**

    作者三个月前写的总结里写着「魔尊」，今天他才把这个别名建上。索引是那时候扫的，
    里面一个「魔尊」都没有——如果没有 `roster_hash` 这一层，这一段从此**永远搜不到**，
    而且不报错。那正是「必须记得去失效」的写法会留下的形态。
    """
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "那一夜魔尊出手了。")
    ensure_index(conn, store, pid)
    assert mentions_in_chapter(conn, store, pid, 1) == [], "此刻「魔尊」还不是任何人"

    before = roster_hash(store, pid)
    store.add_alias(
        AliasSpec(project_id=pid, node_id=book["萧决"], surface="魔尊", kind=AliasKind.TITLE)
    )
    conn.commit()
    assert roster_hash(store, pid) != before, "花名册的内容地址必须跟着别名一起变"

    assert [
        r.chapter_number for r in chapters_mentioning(conn, store, pid, book["萧决"]).chapters
    ] == [1], "加完别名，三个月前那一段总结当场搜得到——没有人需要记得去重建索引"


def test_a_surface_that_turns_ambiguous_drops_back_out(wired: Any) -> None:
    """**反方向也要成立。** 作者又建了一个人也被叫「魔尊」⇒ 这个称呼从此指向两个人
    ⇒ `rules_only` 把它挡在 alternation 之外 ⇒ 已经建好的那条命中必须消失。

    只做「加了就补上」不做「变了就撤掉」的索引会留着一条**指错人**的连线，
    而作者点下去看到的是一份看起来完全正常的章节名单。
    """
    conn, store, pid, book = wired
    store.add_alias(
        AliasSpec(project_id=pid, node_id=book["萧决"], surface="魔尊", kind=AliasKind.TITLE)
    )
    conn.commit()
    _seed(conn, pid, 1, "那一夜魔尊出手了。")
    assert mentions_in_chapter(conn, store, pid, 1)

    store.add_alias(
        AliasSpec(project_id=pid, node_id=book["李管家"], surface="魔尊", kind=AliasKind.TITLE)
    )
    conn.commit()
    assert mentions_in_chapter(conn, store, pid, 1) == []
    assert chapters_mentioning(conn, store, pid, book["萧决"]).chapters == []


def test_a_zero_hit_summary_is_not_rescanned_forever(wired: Any) -> None:
    """扫过了但一个人都没提到 ⇒ 库里是**一条 index 行 + 零条 mention 行**。

    两张表存在的理由就是这一条：合成一张的话，零命中和没扫过在库里长得一模一样，
    于是每一次读都把它重扫一遍。
    """
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "那一天风很大。")
    ensure_index(conn, store, pid)

    indexed = conn.execute(
        "SELECT COUNT(*) AS n FROM summary_index WHERE project_id = ?", (pid,)
    ).fetchone()["n"]
    hits = conn.execute(
        "SELECT COUNT(*) AS n FROM summary_mention WHERE project_id = ?", (pid,)
    ).fetchone()["n"]
    assert (indexed, hits) == (1, 0)


def test_a_second_read_writes_nothing(wired: Any) -> None:
    """没有一行要补时**一条写语句都不发**——换章、切页签每分钟发生几十次。"""
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府。")
    ensure_index(conn, store, pid)

    before = conn.total_changes
    ensure_index(conn, store, pid)
    ensure_index(conn, store, pid)
    assert conn.total_changes == before


def test_two_readers_that_computed_the_same_work_do_not_crash(wired: Any) -> None:
    """两个标签页同时打开这一格。**后到的那个不许 500。**

    `_ensure` 的两条 SELECT 在事务**外面**（读不该去抢写锁），所以真并发时两边会算出
    **同一份「要扫的」**，然后一前一后进事务。这里直接把那一份喂给 `_rebuild` 两次 ——
    这就是那个交错的全部内容，而它比起线程 + barrier 是确定性的。

    修之前后一次 `INSERT` 撞主键，在作者屏幕上是一次莫名其妙的失败：他什么都没做错，
    只是开了两个标签页。
    """
    from novel_harness.draft.rolling_summary import SummaryStore
    from novel_harness.summary_index import _rebuild, _roster

    conn, store, pid, book = wired
    _seed(conn, pid, 1, "萧决在青云城主府。")
    roster = _roster(store, pid)
    active = {row.id: row for row in SummaryStore(conn).active(pid)}
    scan = sorted(active)

    _rebuild(conn, pid, roster, active, drop=[], scan=scan)
    _rebuild(conn, pid, roster, active, drop=[], scan=scan)  # ← 修之前这一行 IntegrityError

    assert [
        r.chapter_number for r in chapters_mentioning(conn, store, pid, book["萧决"]).chapters
    ] == [1]
    # 重跑一遍不许把倒排行翻倍（先删后插，不是「插两次」）。
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM summary_mention WHERE project_id = ?", (pid,)
        ).fetchone()["n"]
        == 2
    )


def test_stale_rows_are_swept_not_left_behind(wired: Any) -> None:
    """作废的索引行**真的被删掉**，不是靠读的时候滤掉。

    留着它们只滤不删，`summary_mention` 会随作者每一次改总结单调增长，
    而这张表的全部价值就在于它小到可以整本重扫。
    """
    conn, store, pid, book = wired
    for text in ("萧决在青云城主府。", "李管家守着院子。", "北荒起风了。"):
        _seed(conn, pid, 1, text)
    ensure_index(conn, store, pid)

    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM summary_index WHERE project_id = ?", (pid,)
    ).fetchone()["n"]
    assert rows == 1, "第 1 章现在算数的只有一行，索引里也只该有一行"


# ══════════════════════════════════════════════════════════════════════════
# 3. 秘密 / 零 / 边界
# ══════════════════════════════════════════════════════════════════════════


def test_a_secret_chip_carries_the_label_never_the_content(wired: Any) -> None:
    """秘密**只出 `NodeRef`**（§10.5 第 3 条）。显示名是花名册本来就在渲染的东西。"""
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "血脉秘密被说破了。")

    hits = mentions_in_chapter(conn, store, pid, 1)
    assert [hit.node.name for hit in hits] == ["血脉秘密"]
    assert TWIST not in hits[0].model_dump_json()
    assert not hasattr(hits[0].node, "props")


def test_a_node_nobody_wrote_about_gets_an_empty_list_not_an_error(wired: Any) -> None:
    conn, store, pid, book = wired
    _seed(conn, pid, 1, "那一天风很大。")
    found = chapters_mentioning(conn, store, pid, book["萧决"])
    assert found.node.name == "萧决" and found.chapters == []


def test_an_unknown_node_id_is_not_an_empty_list(wired: Any) -> None:
    """§10 约束 8：静默的零和真的零不许长得一样。下一步动作完全不同。"""
    conn, store, pid, book = wired
    with pytest.raises(NodeNotFound):
        chapters_mentioning(conn, store, pid, "character:NOPE:0000")


def test_a_chapter_without_a_summary_is_empty(wired: Any) -> None:
    conn, store, pid, book = wired
    assert mentions_in_chapter(conn, store, pid, 2) == []


def test_an_empty_roster_does_not_blow_up(tmp_path: Path) -> None:
    """空花名册是合法状态（`compile_alternation` 的契约），不许 raise。"""
    from novel_harness import project as project_mod
    from novel_harness.db import migrate

    conn = connect(tmp_path / "empty.db")
    migrate(conn)
    pid = project_mod.create(conn, name="空", root_path=str(tmp_path)).id
    store = SqliteStoryGraph(conn)
    try:
        ensure_index(conn, store, pid)
        assert mentions_in_chapter(conn, store, pid, 1) == []
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 4. HTTP 两条
# ══════════════════════════════════════════════════════════════════════════


def _pid(book: dict[str, str]) -> str:
    return book["pid"]


def test_http_chapter_mentions(client: TestClient, book: dict[str, str]) -> None:
    conn = connect(book["db"])
    _seed(conn, book["pid"], 1, "萧决在青云城主府听说了血脉秘密。")
    conn.close()

    body = client.get(
        f"/api/projects/{_pid(book)}/chapters/1/summary/mentions"
    ).json()
    assert body["chapter"] == 1
    assert [m["node"]["name"] for m in body["mentions"]] == ["萧决", "青云城主府", "血脉秘密"]
    assert TWIST not in str(body), "秘密的 props 一个字都不许出接口"
    assert all("props" not in m["node"] for m in body["mentions"])


def test_http_reverse_lookup(client: TestClient, book: dict[str, str]) -> None:
    conn = connect(book["db"])
    _seed(conn, book["pid"], 1, "萧决在青云城主府。")
    _seed(conn, book["pid"], 2, "萧决走了。")
    conn.close()

    body = client.get(
        f"/api/projects/{_pid(book)}/nodes/{book['萧决']}/summary-mentions"
    ).json()
    assert body["node"]["name"] == "萧决"
    assert [row["chapter_number"] for row in body["chapters"]] == [1, 2]


def test_http_unknown_node_is_404(client: TestClient, book: dict[str, str]) -> None:
    response = client.get(
        f"/api/projects/{_pid(book)}/nodes/character:NOPE:0000/summary-mentions"
    )
    assert response.status_code == 404


def test_http_never_asks_the_author_for_a_chapter_number(
    client: TestClient, book: dict[str, str]
) -> None:
    """两条都是 GET，章号只在路径里当**查询坐标**（约束 10 允许的那一种）。

    这条断言在这儿是因为「反查」天然想长出一个「从第几章查到第几章」的输入框——
    而那正是约束 6 / 10 反复在拦的形状。今天没有，别加。
    """
    from novel_harness.api.app import app

    paths = [
        route.path  # type: ignore[attr-defined]
        for route in app.routes
        if "summary-mentions" in getattr(route, "path", "")
        or getattr(route, "path", "").endswith("/summary/mentions")
    ]
    assert sorted(paths) == [
        "/api/projects/{project_id}/chapters/{chapter}/summary/mentions",
        "/api/projects/{project_id}/nodes/{node_id}/summary-mentions",
    ]
    for route in app.routes:
        if getattr(route, "path", "") in paths:
            assert set(getattr(route, "methods", set())) == {"GET"}
