"""`CanonWriter` —— 建节点 / 别名 / 秘密 / 章节 / 快照 / 证据（M1 声明层的地基）。

**全部走真库**（`db.connect(IN_MEMORY)` + `db.migrate`），零 Fake：这一层要证的正是
「schema 的那些 CHECK 和复合外键与我们的编排合得上」，而 Fake 对那件事一无所知。

这个文件里最重要的一条是 `test_current_snapshots_follows_the_hash_not_the_clock`：
它是唯一一条在正常路径上**不会**失败的断言——`ORDER BY created_at DESC LIMIT 1` 和
`text_sha256` 等值判据在没人改稿时同解，于是那个 bug 只在作者把一章改回上一个版本时
才现形，而它的产物是一条锚在旧正文上的证据。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.decisions import quote_hash
from novel_harness.graph import (
    AliasKind,
    AliasSpec,
    ChapterSpec,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeNotFound,
    NodeProps,
    NodeSpec,
    QuoteMismatch,
    SecretDetail,
    StoreError,
    StoryGraph,
)
from novel_harness.graph import GraphStore as GraphStoreProto
from novel_harness.graph import queries
from novel_harness.graph import sqlite_store as sqlite_store_mod
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import new_project_id

CH1 = "第一章 血脉\n\n他终于明白母亲为何从不提起父亲。\n那半块玉佩他握了很久。\n"


@pytest.fixture
def conn() -> Iterator[Connection]:
    c = connect(IN_MEMORY)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def pid(conn: Connection) -> str:
    p = new_project_id()
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)", (p, "青云记", "."))
    conn.commit()
    return p


@pytest.fixture
def store(conn: Connection) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


def _count(conn: Connection, table: str, **where: object) -> int:
    clause = " AND ".join(f"{k} = ?" for k in where) or "1=1"
    # 表名是调用点的字面量、值一律走占位符。tests/ 不在架构守卫的扫描范围（它只扫 src/），
    # 而这里要证的正是「库里到底落了几行」——那件事只能直接问库。
    sql = f"SELECT COUNT(*) FROM {table} WHERE {clause}"
    return int(conn.execute(sql, tuple(where.values())).fetchone()[0])


def _character(pid: str, name: str, **kw: object) -> NodeSpec:
    return NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name=name, **kw)


def _secret(pid: str, name: str, **detail: object) -> NodeSpec:
    return NodeSpec(
        project_id=pid, label=NodeLabel.SECRET, name=name, secret=SecretDetail(**detail)
    )


def _chapter(pid: str, number: int = 1, text: str = CH1, **kw: object) -> ChapterSpec:
    kw.setdefault("heading", "第一章 血脉")
    kw.setdefault("title", "血脉")
    kw.setdefault("path", f"chapters/{number:04d}.md")
    return ChapterSpec(project_id=pid, number=number, text=text, **kw)


# ══════════════════════════════════════════════════════════════════════════
# Protocol
# ══════════════════════════════════════════════════════════════════════════


def test_store_satisfies_both_protocols(store: SqliteStoryGraph) -> None:
    # StoryGraph 的五个方法一个字没动 → 老消费者（checks/ 收 store: StoryGraph）不受影响；
    # GraphStore 是读写交集 → declare / importer 收它。同一个对象同时是两者。
    assert isinstance(store, StoryGraph)
    assert isinstance(store, GraphStoreProto)


# ══════════════════════════════════════════════════════════════════════════
# upsert_node
# ══════════════════════════════════════════════════════════════════════════


def test_upsert_node_is_idempotent(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    first = store.upsert_node(_character(pid, "萧决"))
    again = store.upsert_node(_character(pid, "萧决"))
    assert again.id == first.id
    assert _count(conn, "node", project_id=pid) == 1
    # 第二个「萧决」会让 resolve 返 2 个 hit → ambiguous → usable_for_rules 为假 →
    # 面板上整行消失，且没有一步会报错。幂等顺手关掉了那条路。
    assert _count(conn, "alias", project_id=pid) == 1


def test_upsert_node_updates_props_on_rerun(store: SqliteStoryGraph, pid: str) -> None:
    store.upsert_node(_character(pid, "顾清音"))
    again = store.upsert_node(
        _character(pid, "顾清音", props=NodeProps(first_appears_chapter=12))
    )
    assert again.props.first_appears_chapter == 12


def test_canonical_alias_surface_is_the_name(store: SqliteStoryGraph, pid: str) -> None:
    node = store.upsert_node(_character(pid, "萧决"))
    [res] = store.resolve(pid, ["萧决"])
    assert res.unique_node is not None
    assert res.unique_node.id == node.id
    assert res.hits[0].kind is AliasKind.CANONICAL
    assert res.usable_for_rules


def test_one_character_name_is_storable_but_never_fires(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """1 字名的人物真书里有。`CHECK (usable_for_rules = 0 OR length(surface) >= 2)` 会让
    `usable_for_rules=1` 的那条 INSERT 失败，**而它和节点在一个事务里** —— 不判这一下，
    「建一个叫『决』的人物」整个失败。schema 的立场是「短 surface 存得下，只是不许被
    规则拿去匹配正文」，不是「1 字名的人不许进这本书」。"""
    node = store.upsert_node(_character(pid, "决"))
    assert _count(conn, "node", id=node.id) == 1
    assert _count(conn, "alias", node_id=node.id, usable_for_rules=0) == 1
    [res] = store.resolve(pid, ["决"])
    assert res.unique_node is not None  # 找得到
    assert not res.usable_for_rules  # 但规则不许拿它开火


@pytest.mark.parametrize("label", [NodeLabel.STATE_DIM, NodeLabel.CHAPTER])
def test_labels_outside_the_roster_get_no_canonical_alias(
    conn: Connection, store: SqliteStoryGraph, pid: str, label: NodeLabel
) -> None:
    """`resolve(pid, None)` 是花名册，mentions.py 拿它编 alternation。
    「健康」进去 = 去正文里匹配每一个「健康」；300 条章标进去 = 花名册变成目录。"""
    if label is NodeLabel.CHAPTER:
        node_id = store.put_chapter(_chapter(pid)).id
    else:
        node_id = store.upsert_node(
            NodeSpec(
                project_id=pid,
                label=NodeLabel.STATE_DIM,
                name="健康",
                props=NodeProps(dim_key="health"),
            )
        ).id
    assert _count(conn, "alias", node_id=node_id) == 0
    roster = store.resolve(pid, None)
    assert node_id not in {h.node.id for r in roster for h in r.hits}


def test_chapter_nodes_can_only_be_born_with_their_row(pid: str) -> None:
    # 没有 chapter 行就没有 number，而 number 是 state_at 的全序键。
    with pytest.raises(ValidationError, match="put_chapter"):
        NodeSpec(project_id=pid, label=NodeLabel.CHAPTER, name="第一章 血脉")


def test_secret_row_and_label_live_and_die_together(pid: str) -> None:
    with pytest.raises(ValidationError, match="同生同死"):
        NodeSpec(project_id=pid, label=NodeLabel.SECRET, name="血脉秘密")
    with pytest.raises(ValidationError, match="同生同死"):
        _character(pid, "萧决", secret=SecretDetail())


def test_secret_node_becomes_a_column_in_the_matrix(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """一个没有 secret 行的 Secret 节点在 `queries.secret_ids`（`FROM secret`）里不成列——
    而 `secrets=None` 是面板的唯一路径。作者会看见「系统对这个秘密没意见」，
    实际是「系统不知道有这个秘密」。"""
    node = store.upsert_node(_secret(pid, "血脉秘密", description="他不是萧家的孩子"))
    assert queries.secret_ids(conn, pid) == [node.id]


def test_sub_of_lands(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    parent = store.upsert_node(_secret(pid, "血脉秘密"))
    child = store.upsert_node(_secret(pid, "生母是谁", sub_of=parent.id))
    row = conn.execute("SELECT sub_of FROM secret WHERE id = ?", (child.id,)).fetchone()
    assert row["sub_of"] == parent.id


def test_upsert_node_rejects_a_duplicate_name_it_did_not_create(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """幂等键是应用层的（`idx_node_name` 只是普通 INDEX）。绕开本方法塞进第二行时，
    正确动作是炸，不是「那就再建一个」。"""
    first = store.upsert_node(_character(pid, "萧决"))
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES (?, ?, 'Character', '萧决')",
        (f"character:x:{first.id[-26:]}", pid),
    )
    with pytest.raises(StoreError, match="幂等键撞出多行"):
        store.upsert_node(_character(pid, "萧决"))


# ══════════════════════════════════════════════════════════════════════════
# add_alias
# ══════════════════════════════════════════════════════════════════════════


def test_add_alias(store: SqliteStoryGraph, pid: str) -> None:
    node = store.upsert_node(_character(pid, "萧决"))
    stored = store.add_alias(
        AliasSpec(project_id=pid, node_id=node.id, surface="决哥", kind=AliasKind.NICKNAME)
    )
    assert stored.node_id == node.id
    assert stored.kind is AliasKind.NICKNAME
    [res] = store.resolve(pid, ["决哥"])
    assert res.unique_node is not None
    assert res.unique_node.id == node.id


def test_canonical_is_upsert_nodes_alone(pid: str) -> None:
    # 它的 surface 必须 == node.name，而 add_alias 够不到 node.name。从这里放进来撞的是
    # idx_alias_canonical，给调用方一个读不懂的 IntegrityError。
    with pytest.raises(ValidationError, match="canonical"):
        AliasSpec(project_id=pid, node_id="x", surface="萧决", kind=AliasKind.CANONICAL)


def test_short_alias_may_exist_but_never_fires(pid: str) -> None:
    with pytest.raises(ValidationError, match="usable_for_rules"):
        AliasSpec(project_id=pid, node_id="x", surface="音")
    assert not AliasSpec(
        project_id=pid, node_id="x", surface="音", usable_for_rules=False
    ).usable_for_rules


def test_add_alias_to_unknown_node_raises(store: SqliteStoryGraph, pid: str) -> None:
    with pytest.raises(NodeNotFound):
        store.add_alias(AliasSpec(project_id=pid, node_id="character:zz:NOPE", surface="决哥"))


# ══════════════════════════════════════════════════════════════════════════
# put_chapter
# ══════════════════════════════════════════════════════════════════════════


def test_put_chapter_creates_node_row_and_snapshot(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    ch = store.put_chapter(_chapter(pid))
    assert ch.created and ch.snapshot_created
    # 扩展表主键 = node.id，所以 PLANTED_IN 的 dst 是 Chapter 节点时 edge.dst 的外键才成立。
    assert _count(conn, "node", id=ch.id, label="Chapter") == 1
    assert _count(conn, "chapter", id=ch.id) == 1
    assert _count(conn, "chapter_snapshot", chapter_id=ch.id) == 1
    assert ch.text_sha256 == quote_hash(CH1)


def test_put_chapter_is_idempotent(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    first = store.put_chapter(_chapter(pid))
    again = store.put_chapter(_chapter(pid))
    assert again.id == first.id
    assert not again.created and not again.snapshot_created
    # 快照按 UNIQUE(chapter_id, text_sha256) 去重：它是证据的锚，不是版本历史。
    assert _count(conn, "chapter_snapshot", chapter_id=first.id) == 1


def test_edited_chapter_gets_a_new_snapshot_and_keeps_the_old_one(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """`nh sync` 的正身：作者在自己的编辑器里改了这一章。"""
    first = store.put_chapter(_chapter(pid))
    edited = store.put_chapter(_chapter(pid, text=CH1 + "他攥紧了拳头。\n"))
    assert edited.id == first.id
    assert not edited.created
    assert edited.snapshot_created
    assert edited.snapshot_id != first.snapshot_id
    assert _count(conn, "chapter_snapshot", chapter_id=first.id) == 2
    # 旧快照永不删：审计指针指着它，而那个指针的承诺是「永不失效」。
    assert _count(conn, "chapter_snapshot", id=first.snapshot_id) == 1
    row = conn.execute("SELECT text_sha256 FROM chapter WHERE id = ?", (first.id,)).fetchone()
    assert row["text_sha256"] == edited.text_sha256


def test_put_chapter_updates_the_heading(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    first = store.put_chapter(_chapter(pid))
    store.put_chapter(_chapter(pid, heading="第一章 血脉（修）", title="血脉（修）"))
    row = conn.execute("SELECT name FROM node WHERE id = ?", (first.id,)).fetchone()
    assert row["name"] == "第一章 血脉（修）"


def test_current_snapshots_follows_the_hash_not_the_clock(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """**这个文件里最重要的一条。**

    造两条快照，再把 `chapter.text_sha256` 指回**较早**那条（= 作者把一章改回上一个版本；
    快照按内容去重、不新建，所以「最新的那条」指向的是那份已经被改掉的正文）。
    `ORDER BY s.created_at DESC LIMIT 1` 会拿到较晚那条 —— 那是在「哪条快照是当前的」
    这件事上猜，而 chapter.text_sha256 已经把答案写在那儿了。
    """
    first = store.put_chapter(_chapter(pid))
    later = store.put_chapter(_chapter(pid, text=CH1 + "他攥紧了拳头。\n"))
    assert later.snapshot_id != first.snapshot_id

    conn.execute(
        "UPDATE chapter SET text_sha256 = ? WHERE id = ?", (quote_hash(CH1), first.id)
    )
    conn.commit()

    [current] = store.current_snapshots(pid)
    assert current.snapshot_id == first.snapshot_id, "当前快照的判据是哈希等值，不是 created_at"
    assert current.text == CH1
    assert current.number == 1


def test_current_snapshots_is_ordered_by_chapter_number(
    store: SqliteStoryGraph, pid: str
) -> None:
    store.put_chapter(_chapter(pid, number=3, text="丙\n"))
    store.put_chapter(_chapter(pid, number=1, text="甲\n"))
    store.put_chapter(_chapter(pid, number=2, text="乙\n"))
    assert [c.number for c in store.current_snapshots(pid)] == [1, 2, 3]


def test_chapter_spec_has_no_chapter_number_the_author_could_type(pid: str) -> None:
    """约束 10 在这一层的形态：`ChapterSpec` 里的 `number` 是 chapterize 的文本顺序
    index，而**没有** valid_from / since / at 之类的字段。"""
    assert "valid_from" not in ChapterSpec.model_fields
    with pytest.raises(ValidationError):
        ChapterSpec(project_id=pid, number=1, heading="第一章", path="p.md", text="x", valid_from=1)


# ══════════════════════════════════════════════════════════════════════════
# put_evidence —— ADR 0006 配套第 3 条
# ══════════════════════════════════════════════════════════════════════════


def test_hash_is_taken_from_the_snapshot_not_from_the_caller(
    store: SqliteStoryGraph, pid: str
) -> None:
    ch = store.put_chapter(_chapter(pid))
    quote = "他终于明白母亲为何从不提起父亲"
    ev = store.put_evidence(
        EvidenceSpec(
            project_id=pid, chapter_snapshot_id=ch.snapshot_id, para_index=2, quote_text=quote
        )
    )
    # 哈希取的是**从快照里切出来的那个子串**。M1 精确匹配下它与 spec.quote_text 逐字节
    # 相等，所以这两条断言今天分不出「对谁取的哈希」——分得出的是另外两条：
    # EvidenceSpec 根本没有 quote_sha256 字段（见下），且锚对不上时压根没有 evidence 行。
    # 那正是「把它变成不可能，而不是检测它」的意思。
    assert ev.audit.quote_text == quote
    assert ev.audit.quote_sha256 == quote_hash(ev.audit.quote_text)
    assert ev.chapter_number == 1  # evidence 表里没有这列，JOIN chapter 填的


def test_hash_follows_the_located_text_even_when_it_differs_from_the_input(
    store: SqliteStoryGraph, pid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上一条断言分不出「对谁取的哈希」，这一条分得出——用的是 M4 才会出现的那个条件。

    M1 只做精确匹配，于是 `sliced` 与 `spec.quote_text` 恒等，`quote_hash(sliced)` 和
    `quote_hash(spec.quote_text)` 在全部 542 个测试下同解：把 `sqlite_store` 里那一行改
    翻，一条测试都不会红。而 M4 的模糊定位落地那天（抽取器返回的 quote 有 10–30% 对不上
    原文，`ratio<0.9` 丢弃、命中的**切原文**）两者才第一次分叉——那时改翻的产物是一条
    `quote_sha256` 对不上自己 `quote_text` 的证据，`revalidate` 永远 relocate 不回来。

    所以这里把 `find_one` 换成一个「命中了但切出来的串跟输入不同」的桩，即 M4 的形状。
    它耦合了「put_evidence 经由 anchor.find_one 定位」这个实现细节——**这是故意的**：
    要钉的正是那一行取谁的哈希。
    """
    ch = store.put_chapter(_chapter(pid))
    located = "他终于明白母亲为何从不提起父亲"
    typed_by_extractor = "他终于明白母亲为什么从不提起父亲"  # 差一个「什」，模糊命中

    monkeypatch.setattr(sqlite_store_mod.anchor, "find_one", lambda para, quote, k: located)

    ev = store.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=ch.snapshot_id,
            para_index=2,
            quote_text=typed_by_extractor,
        )
    )
    assert ev.audit.quote_text == located
    assert ev.audit.quote_sha256 == quote_hash(located)
    assert ev.audit.quote_sha256 != quote_hash(typed_by_extractor)


def test_both_pointers_coincide_at_write_time(store: SqliteStoryGraph, pid: str) -> None:
    ch = store.put_chapter(_chapter(pid))
    ev = store.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=ch.snapshot_id,
            para_index=3,
            quote_text="那半块玉佩",
        )
    )
    assert ev.audit.para_index == ev.relocate.para_index_hint == 3
    assert ev.relocate.chapter_id == ch.id
    assert ev.audit.chapter_snapshot_id == ch.snapshot_id
    assert ev.audit.quote_sha256 == ev.relocate.quote_sha256
    assert ev.anchor().para_index == 3


def test_occurrence_k_picks_the_right_one(store: SqliteStoryGraph, pid: str) -> None:
    ch = store.put_chapter(_chapter(pid, number=2, text="他走了。他走了。他走了。\n"))
    ev = store.put_evidence(
        EvidenceSpec(
            project_id=pid,
            chapter_snapshot_id=ch.snapshot_id,
            para_index=0,
            occurrence_k=2,
            quote_text="他走了",
        )
    )
    assert ev.relocate.occurrence_k == 2


@pytest.mark.parametrize(
    ("para_index", "occurrence_k", "quote", "why"),
    [
        (2, 0, "他终于明白母亲为何从不提起母亲", "那个位置上是别的字"),
        (3, 0, "他终于明白母亲为何从不提起父亲", "引语在别的段"),
        (99, 0, "他终于明白母亲为何从不提起父亲", "段号越界"),
        (2, 7, "他终于明白母亲为何从不提起父亲", "第 k 次不存在"),
    ],
)
def test_a_bad_anchor_leaves_no_evidence_row(
    conn: Connection,
    store: SqliteStoryGraph,
    pid: str,
    para_index: int,
    occurrence_k: int,
    quote: str,
    why: str,
) -> None:
    """四种失败是同一种：这个锚在这份快照上定位不到，于是没有子串可以取哈希。
    ADR 0006 那句「反过来做的话，锚从第一天起就是坏的」在这里是**不可能**，不是被检测。"""
    ch = store.put_chapter(_chapter(pid))
    with pytest.raises(QuoteMismatch):
        store.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=ch.snapshot_id,
                para_index=para_index,
                occurrence_k=occurrence_k,
                quote_text=quote,
            )
        )
    assert _count(conn, "evidence", project_id=pid) == 0, why


def test_evidence_spec_cannot_carry_a_hash_or_a_second_chapter_id(pid: str) -> None:
    for field in ("quote_sha256", "chapter_id", "para_index_hint"):
        with pytest.raises(ValidationError):
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id="snapshot:x:Y",
                para_index=0,
                quote_text="x",
                **{field: "x"},
            )


def test_unknown_snapshot_raises(store: SqliteStoryGraph, pid: str) -> None:
    with pytest.raises(StoreError, match="快照不存在"):
        store.put_evidence(
            EvidenceSpec(
                project_id=pid, chapter_snapshot_id="snapshot:zz:NOPE", para_index=0, quote_text="x"
            )
        )


def test_cross_project_snapshot_raises(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    """evidence 的两个指针分别外键到 chapter_snapshot 和 chapter，两条都不带 project_id，
    所以 schema 拦不住这一条——它只能在这里拦。"""
    other = new_project_id()
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)", (other, "别的书", "."))
    ch = store.put_chapter(_chapter(other))
    with pytest.raises(StoreError, match="属于项目"):
        store.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=ch.snapshot_id,
                para_index=2,
                quote_text="他终于明白母亲为何从不提起父亲",
            )
        )


# ══════════════════════════════════════════════════════════════════════════
# transaction —— 「证据 + 边」必须原子
# ══════════════════════════════════════════════════════════════════════════


def test_transaction_rolls_back_evidence(conn: Connection, store: SqliteStoryGraph, pid: str) -> None:
    ch = store.put_chapter(_chapter(pid))
    with pytest.raises(RuntimeError), store.transaction():
        store.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=ch.snapshot_id,
                para_index=2,
                quote_text="他终于明白母亲为何从不提起父亲",
            )
        )
        raise RuntimeError("声明层在这里改了主意")
    assert _count(conn, "evidence", project_id=pid) == 0


def test_evidence_and_edge_are_born_together(
    conn: Connection, store: SqliteStoryGraph, pid: str
) -> None:
    """声明层要的就是这个形状：`transaction()` 在 CanonWriter 上、`upsert_edge` 在
    StoryGraph 上，而它们必须罩在同一个事务里——所以 `GraphStore` 是一个交集，
    `SqliteStoryGraph` 是一个对象一条连接。"""
    who = store.upsert_node(_character(pid, "萧决"))
    secret = store.upsert_node(_secret(pid, "血脉秘密"))
    ch = store.put_chapter(_chapter(pid))

    with store.transaction():
        ev = store.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=ch.snapshot_id,
                para_index=2,
                quote_text="他终于明白母亲为何从不提起父亲",
            )
        )
        result = store.upsert_edge(
            EdgeSpec(
                project_id=pid,
                src=who.id,
                dst=secret.id,
                type=EdgeType.KNOWS,
                # ★ 作者路径上 valid_from 唯一的赋值：它来自证据，不来自任何一个入参。
                valid_from_chapter=ev.chapter_number,
                information_scope=InformationScope.CANON,
                evidence_id=ev.id,
            )
        )

    assert result.edge.valid_from_chapter == 1
    assert _count(conn, "evidence", id=ev.id) == 1
    assert _count(conn, "edge", id=result.edge.id) == 1
    matrix = store.knowledge_matrix(pid, 1, [who.id])
    assert matrix.cell(who.id, secret.id).state.value == "KNOWS"
    assert matrix.cell(who.id, secret.id).since_chapter == 1
