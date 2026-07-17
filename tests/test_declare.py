"""声明层 —— 「作者敲一句引语，系统算出章号」的全链条（PLAN §5.9 / 约束 10）。

**全部走真库**（`db.connect(IN_MEMORY)` + `db.migrate` + `SqliteStoryGraph`），零 Fake。
正文用 `store.put_chapter` 直接铺——不依赖 `importer.py`。

这个文件里最重要的两条，都是「不会失败的失败」：

- `test_decision_quote_hash_matches_evidence_byte_for_byte`：传错 quote_text 时两边哈希
  永远不等，而**没有任何东西会报错**——直到某次 schema 变更要重放。
- `test_supersede_conflict_leaves_no_fake_accept_in_the_log`：日志先写的话，每一次拒绝
  都会在那张封死了 UPDATE/DELETE 的表里留一条假的 accept，删不掉。

而全文最该注意的是：**没有一个测试传过章号**。断言里的 1 / 3 全是系统算出来的。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from novel_harness import decisions, project
from novel_harness.db import IN_MEMORY, Connection, connect, migrate
from novel_harness.declare import (
    AmbiguousName,
    AmbiguousQuote,
    Declaration,
    Ledger,
    QuoteNotFound,
    WrongLabel,
)
from novel_harness.graph import (
    AliasKind,
    EdgeType,
    KnowledgeState,
    NodeLabel,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.text import anchor

# 3 章。萧决在第 1 章得知血脉秘密、第 1 章在青云城、第 3 章到北荒。
# 「他终于明白」在 ch1 和 ch3 各出现一次 —— 那是歧义引语的料。
CH1 = (
    "第一章 血脉\n"
    "\n"
    "他终于明白母亲为何从不提起父亲。\n"
    "你身上流的不是萧家的血。\n"
    "青云城的雨下了一夜。\n"
)
CH2 = "第二章 雨停\n\n李管家撑着伞站在阶下。\n他什么也没说。\n"
CH3 = (
    "第三章 北荒\n"
    "\n"
    "他终于明白这条路没有回头。\n"
    "北荒的风比刀还利。\n"
)


@pytest.fixture
def conn() -> Iterator[Connection]:
    c = connect(IN_MEMORY)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def pid(conn: Connection) -> str:
    return project.create(conn, name="青云记", root_path=".").id


@pytest.fixture
def store(conn: Connection) -> SqliteStoryGraph:
    return SqliteStoryGraph(conn)


@pytest.fixture
def led(store: SqliteStoryGraph, conn: Connection, pid: str) -> Ledger:
    from novel_harness.graph import ChapterSpec

    for number, text in ((1, CH1), (2, CH2), (3, CH3)):
        store.put_chapter(
            ChapterSpec(
                project_id=pid,
                number=number,
                heading=text.splitlines()[0],
                path=f"chapters/{number:04d}.md",
                text=text,
            )
        )
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.CHARACTER, "李管家")
    ledger.declare_node(NodeLabel.LOCATION, "青云城")
    ledger.declare_node(NodeLabel.LOCATION, "北荒")
    ledger.declare_node(NodeLabel.SECRET, "血脉秘密", secret=SecretDetail(description="他不是萧家的种"))
    return ledger


def _counts(conn: Connection, pid: str) -> tuple[int, int, int]:
    """(evidence 行数, edge 行数, decision_log 行数)。三张表零新增是「拒绝 = 什么都没发生」的判据。"""
    ev = conn.execute("SELECT count(*) FROM evidence WHERE project_id = ?", (pid,)).fetchone()[0]
    ed = conn.execute("SELECT count(*) FROM edge WHERE project_id = ?", (pid,)).fetchone()[0]
    return ev, ed, len(decisions.read(conn, pid))


# ══════════════════════════════════════════════════════════════════════════
# 头牌：引语 → 章号
# ══════════════════════════════════════════════════════════════════════════


def test_valid_from_comes_from_the_quote_not_from_the_author(led: Ledger) -> None:
    # 作者敲的只有这一句话。下面那个 1 是系统算出来的 —— 本测试从头到尾没有章号入参。
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert d.valid_from == 1
    assert d.edge.valid_from_chapter == 1
    assert d.evidence.chapter_number == 1


def test_a_quote_in_chapter_three_yields_chapter_three(led: Ledger) -> None:
    # 同一个 API、同样零章号入参，换一句引语就换一章：那个数确实是引语的函数。
    d = led.declare_where(who="萧决", loc="北荒", quote="北荒的风比刀还利")
    assert d.valid_from == 3


def test_declaration_valid_from_is_the_evidence_chapter(led: Ledger) -> None:
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert d.valid_from == d.evidence.chapter_number


def test_ledger_has_no_chapter_shaped_parameter_anywhere(led: Ledger) -> None:
    """约束 10 在签名层的具身：**没有一个旗标能让作者填章号。**"""
    banned = {"chapter", "valid_from", "valid_to", "since", "at", "ch", "chapter_number"}
    scanned: list[str] = []
    for name, method in inspect.getmembers(Ledger, inspect.isfunction):
        if name.startswith("_"):
            continue
        scanned.append(name)
        params = set(inspect.signature(method).parameters) - {"self"}
        assert not params & banned, f"Ledger.{name} 收了一个章号形状的参数：{params & banned}"
    # 一个永远绿的守卫比没有守卫更糟：扫描器要是一个方法都没看见，上面那条恒真。
    assert set(scanned) == {
        "declare_alias",
        "declare_believes",
        "declare_knows",
        "declare_node",
        "declare_where",
        "locate",
    }


# ══════════════════════════════════════════════════════════════════════════
# 日志：唯一不可重建的资产必须对得上号
# ══════════════════════════════════════════════════════════════════════════


def test_decision_quote_hash_matches_evidence_byte_for_byte(led: Ledger, conn: Connection, pid: str) -> None:
    """**这条是「传 ev.audit.quote_text，不是作者敲的那个串」的具身。**

    传错的话两边哈希永远不等，而没有任何东西会报错——直到某次 schema 变更要重放。
    """
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    log = [x for x in decisions.read(conn, pid) if x.id == d.decision_id]
    assert len(log) == 1
    entry = log[0]
    assert entry.quote_sha256 == d.evidence.audit.quote_sha256
    assert entry.quote_text == d.evidence.audit.quote_text
    assert entry.chapter_number == d.evidence.chapter_number
    assert entry.para_index == d.evidence.audit.para_index


def test_decision_subject_is_a_name_not_an_id(led: Ledger, conn: Connection, pid: str) -> None:
    # §5.7 原文：「人名，不是 ID」。ID 随重抽全部作废，重放不回去的日志等于没有日志。
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    entry = next(x for x in decisions.read(conn, pid) if x.id == d.decision_id)
    assert entry.subject_name == "萧决"
    assert entry.subject_name != d.edge.src
    assert entry.payload["object_name"] == "血脉秘密"
    assert entry.payload["edge_type"] == "KNOWS"


def test_declare_node_logs_secret_declare_for_secrets(led: Ledger, conn: Connection, pid: str) -> None:
    kinds = {d.kind for d in decisions.read(conn, pid)}
    assert "secret_declare" in kinds
    assert "node_declare" in kinds
    secret_entries = decisions.read(conn, pid, kind="secret_declare")
    assert [e.subject_name for e in secret_entries] == ["血脉秘密"]
    assert secret_entries[0].quote_text is None  # 节点不是时态的：没有引语，也没有章号


def test_declare_alias_logs_alias_merge(led: Ledger, conn: Connection, pid: str) -> None:
    stored = led.declare_alias(of="萧决", surface="决哥", kind=AliasKind.NICKNAME)
    assert stored.surface == "决哥"
    entry = decisions.read(conn, pid, kind="alias_merge")[-1]
    assert entry.subject_name == "萧决"
    assert entry.payload["surface"] == "决哥"
    assert entry.payload["typed_surface"] == "萧决"


# ══════════════════════════════════════════════════════════════════════════
# 拒绝：代价必须是「什么都没发生」
# ══════════════════════════════════════════════════════════════════════════


def test_quote_not_found_writes_nothing(led: Ledger, conn: Connection, pid: str) -> None:
    before = _counts(conn, pid)
    with pytest.raises(QuoteNotFound):
        led.declare_knows(who="萧决", secret="血脉秘密", quote="这句话全书里没有")
    assert _counts(conn, pid) == before


def test_ambiguous_quote_shows_candidates_and_writes_nothing(
    led: Ledger, conn: Connection, pid: str
) -> None:
    before = _counts(conn, pid)
    with pytest.raises(AmbiguousQuote) as exc:
        led.declare_knows(who="萧决", secret="血脉秘密", quote="他终于明白")
    assert len(exc.value.candidates) == 2
    assert [c.chapter_number for c in exc.value.candidates] == [1, 3]
    # 摆的候选必须带上下文，否则作者不知道该往哪边加长引语。
    assert "母亲" in exc.value.candidates[0].context
    assert "回头" in exc.value.candidates[1].context
    assert _counts(conn, pid) == before


def test_ambiguous_name_shows_candidates_and_writes_nothing(
    led: Ledger, conn: Connection, pid: str
) -> None:
    led.declare_alias(of="萧决", surface="师兄", kind=AliasKind.TITLE)
    led.declare_alias(of="李管家", surface="师兄", kind=AliasKind.TITLE)
    before = _counts(conn, pid)
    with pytest.raises(AmbiguousName) as exc:
        led.declare_knows(who="师兄", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert exc.value.surface == "师兄"
    assert sorted(c.name for c in exc.value.candidates) == ["李管家", "萧决"]
    assert _counts(conn, pid) == before


def test_unknown_name_writes_nothing(led: Ledger, conn: Connection, pid: str) -> None:
    from novel_harness.declare import UnknownName

    before = _counts(conn, pid)
    with pytest.raises(UnknownName):
        led.declare_knows(who="没有这个人", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert _counts(conn, pid) == before


def test_knows_refuses_a_character_in_the_secret_slot(led: Ledger, conn: Connection, pid: str) -> None:
    """「知道李管家」会建出一条 dst 是人的 KNOWS 边——而它在认知矩阵里根本不成列。"""
    before = _counts(conn, pid)
    with pytest.raises(WrongLabel) as exc:
        led.declare_knows(who="萧决", secret="李管家", quote="你身上流的不是萧家的血")
    assert exc.value.got is NodeLabel.CHARACTER
    assert exc.value.want is NodeLabel.SECRET
    assert _counts(conn, pid) == before


def test_where_refuses_a_secret_in_the_location_slot(led: Ledger) -> None:
    with pytest.raises(WrongLabel):
        led.declare_where(who="萧决", loc="血脉秘密", quote="北荒的风比刀还利")


def test_supersede_conflict_leaves_no_fake_accept_in_the_log(
    led: Ledger, conn: Connection, pid: str
) -> None:
    """**「日志最后」那个裁决的具身，别删。**

    先声明第 3 章的位置，再声明第 1 章的位置 = 乱序 → `SupersedeConflict`（预期异常，
    不是崩溃）。日志先写的话，这次拒绝会在那张封死了 UPDATE/DELETE 的表里留一条
    永远删不掉的假 accept。
    """
    from novel_harness.graph import SupersedeConflict

    led.declare_where(who="萧决", loc="北荒", quote="北荒的风比刀还利")  # ch3
    before = _counts(conn, pid)
    with pytest.raises(SupersedeConflict):
        led.declare_where(who="萧决", loc="青云城", quote="青云城的雨下了一夜")  # ch1，乱序
    after = _counts(conn, pid)
    assert after == before, "拒绝的代价必须是「什么都没发生」：证据、边、日志三张表零新增"
    assert not [d for d in decisions.read(conn, pid) if d.payload.get("object_name") == "青云城"]


# ══════════════════════════════════════════════════════════════════════════
# 时态：新边闭合旧边
# ══════════════════════════════════════════════════════════════════════════


def test_second_location_closes_the_first(led: Ledger) -> None:
    first = led.declare_where(who="萧决", loc="青云城", quote="青云城的雨下了一夜")
    assert first.valid_from == 1
    second = led.declare_where(who="萧决", loc="北荒", quote="北荒的风比刀还利")
    assert second.valid_from == 3
    assert len(second.closed) == 1
    assert second.closed[0].id == first.edge.id
    assert second.closed[0].valid_to_chapter == 3  # 闭开区间：[1,3) —— ch2 在青云城，ch3 不在


def test_state_at_agrees_with_the_declared_intervals(led: Ledger, store: SqliteStoryGraph, pid: str) -> None:
    xiao = led.declare_node(NodeLabel.CHARACTER, "萧决")
    led.declare_where(who="萧决", loc="青云城", quote="青云城的雨下了一夜")
    led.declare_where(who="萧决", loc="北荒", quote="北荒的风比刀还利")
    assert store.state_at(pid, xiao.id, 2).location is not None
    assert store.state_at(pid, xiao.id, 2).location.name == "青云城"
    assert store.state_at(pid, xiao.id, 3).location.name == "北荒"


def test_believes_stores_the_believed_value(led: Ledger) -> None:
    d = led.declare_believes(
        who="李管家",
        secret="血脉秘密",
        believed_value="以为萧决是萧家的种",
        quote="李管家撑着伞站在阶下",
    )
    assert d.edge.type is EdgeType.BELIEVES
    assert d.edge.props.believed_value == "以为萧决是萧家的种"
    assert d.valid_from == 2


def test_declared_secret_shows_up_in_the_default_matrix_columns(
    led: Ledger, store: SqliteStoryGraph, pid: str
) -> None:
    """`declare_node(SECRET)` 落了 secret 行，所以它在默认列序（走 `secret` 表）里成列。"""
    xiao = led.declare_node(NodeLabel.CHARACTER, "萧决")
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    m = store.knowledge_matrix(pid, 1, [xiao.id])
    assert [s.name for s in m.secrets] == ["血脉秘密"]
    cell = m.cell(xiao.id, d.edge.dst)
    assert cell.state is KnowledgeState.KNOWS
    assert cell.since_chapter == 1
    assert store.knowledge_matrix(pid, 1, [xiao.id]).cell(xiao.id, d.edge.dst).evidence_id


# ══════════════════════════════════════════════════════════════════════════
# 证据：双指针 + locate
# ══════════════════════════════════════════════════════════════════════════


def test_evidence_pointers_coincide_on_write(led: Ledger) -> None:
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert d.evidence.audit.para_index == d.evidence.relocate.para_index_hint
    assert d.evidence.audit.quote_sha256 == d.evidence.relocate.quote_sha256
    assert d.edge.evidence_id == d.evidence.id


def test_locate_is_read_only_and_finds_every_hit(led: Ledger, conn: Connection, pid: str) -> None:
    before = _counts(conn, pid)
    cands = led.locate("他终于明白")
    assert [(c.chapter_number, c.para_index, c.occurrence_k) for c in cands] == [(1, 2, 0), (3, 2, 0)]
    assert all(c.matched_text == "他终于明白" for c in cands)
    assert _counts(conn, pid) == before


def test_context_split_agrees_with_anchor_on_the_k_th_occurrence() -> None:
    """`_context` 用 `str.split` 数「第 k 次」，`anchor` 用 `_occurrences`。**两者必须同解。**

    不同解的产物是拒绝消息里那段上下文指向另一处命中——作者照着它加长引语，然后又被拒。
    "aaa"/"aa" 是那条非重叠语义的判据。
    """
    from novel_harness.declare import _context

    for para, quote in (("aaa", "aa"), ("abab", "ab"), ("他明白他明白他明白", "他明白")):
        assert len(para.split(quote)) - 1 == len(anchor.find_all([para], quote))
        for hit in anchor.find_all([para], quote):
            assert quote in _context(para, quote, hit.occurrence_k)


def test_declaration_is_frozen(led: Ledger) -> None:
    d = led.declare_knows(who="萧决", secret="血脉秘密", quote="你身上流的不是萧家的血")
    assert isinstance(d, Declaration)
    with pytest.raises(Exception):  # noqa: B017 —— 要钉的是「改不动」，不是 pydantic 的错误分类
        d.decision_id = "x"  # type: ignore[misc]
