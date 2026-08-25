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
    EdgeStatus,
    EdgeType,
    NodeLabel,
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
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
    assert d.valid_from == 1
    assert d.edge.valid_from_chapter == 1
    assert d.evidence.chapter_number == 1


def test_a_quote_in_chapter_three_yields_chapter_three(led: Ledger) -> None:
    # 同一个 API、同样零章号入参，换一句引语就换一章：那个数确实是引语的函数。
    d = led.declare_where(who="萧决", loc="北荒", quote="北荒的风比刀还利")
    assert d.valid_from == 3


def test_declaration_valid_from_is_the_evidence_chapter(led: Ledger) -> None:
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
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
        "declare_dead",
        "declare_first_appearance",
        "declare_node",
        "declare_where",
        "locate",
    }
    # `declare_first_appearance` 是这条守卫最该盯住的那一个：它算出来的东西**就是一个章号**
    # （`first_appears_chapter`），而它照样只收 `of` + `quote`。上面那句 `params & banned`
    # 对它跑过一次，才是这条断言今天的意义。


# ══════════════════════════════════════════════════════════════════════════
# 日志：唯一不可重建的资产必须对得上号
# ══════════════════════════════════════════════════════════════════════════


def test_decision_quote_hash_matches_evidence_byte_for_byte(led: Ledger, conn: Connection, pid: str) -> None:
    """**这条是「传 ev.audit.quote_text，不是作者敲的那个串」的具身。**

    传错的话两边哈希永远不等，而没有任何东西会报错——直到某次 schema 变更要重放。
    """
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
    log = [x for x in decisions.read(conn, pid) if x.id == d.decision_id]
    assert len(log) == 1
    entry = log[0]
    assert entry.quote_sha256 == d.evidence.audit.quote_sha256
    assert entry.quote_text == d.evidence.audit.quote_text
    assert entry.chapter_number == d.evidence.chapter_number
    assert entry.para_index == d.evidence.audit.para_index


def test_decision_subject_is_a_name_not_an_id(led: Ledger, conn: Connection, pid: str) -> None:
    # §5.7 原文：「人名，不是 ID」。ID 随重抽全部作废，重放不回去的日志等于没有日志。
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
    entry = next(x for x in decisions.read(conn, pid) if x.id == d.decision_id)
    assert entry.subject_name == "萧决"
    assert entry.subject_name != d.edge.src
    assert entry.payload["object_name"] == "青云城"
    assert entry.payload["edge_type"] == "LOCATED_AT"
def test_declare_alias_logs_alias_merge(led: Ledger, conn: Connection, pid: str) -> None:
    stored = led.declare_alias(of="萧决", surface="决哥", kind=AliasKind.NICKNAME)
    assert stored.surface == "决哥"
    entry = decisions.read(conn, pid, kind="alias_merge")[-1]
    assert entry.subject_name == "萧决"
    assert entry.payload["surface"] == "决哥"
    assert entry.payload["typed_surface"] == "萧决"


# ══════════════════════════════════════════════════════════════════════════
# Canon 版本：作者声明与图事实必须同事务提交
# ══════════════════════════════════════════════════════════════════════════


def test_successful_node_alias_and_edge_declarations_bump_only_for_canon_changes(
    led: Ledger,
    conn: Connection,
    pid: str,
) -> None:
    version = project.require_canon_version(conn, pid)

    led.declare_node(NodeLabel.CHARACTER, "陆青禾")
    version += 1
    assert project.require_canon_version(conn, pid) == version

    # `declare_node` 和 `upsert_edge` 都是幂等入口；重放完全相同的事实不应制造新版本。
    led.declare_node(NodeLabel.CHARACTER, "陆青禾")
    assert project.require_canon_version(conn, pid) == version

    led.declare_alias(of="陆青禾", surface="陆医师")
    version += 1
    assert project.require_canon_version(conn, pid) == version

    first = led.declare_where(
        who="萧决",
        loc="青云城",
        quote="青云城的雨下了一夜",
    )
    version += 1
    assert project.require_canon_version(conn, pid) == version
    evidence_rows = conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE project_id = ?",
        (pid,),
    ).fetchone()[0]

    repeated = led.declare_where(
        who="萧决",
        loc="青云城",
        quote="青云城的雨下了一夜",
    )
    assert project.require_canon_version(conn, pid) == version
    assert repeated.edge.id == first.edge.id
    assert repeated.evidence.id == first.evidence.id
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE project_id = ?",
        (pid,),
    ).fetchone()[0] == evidence_rows


@pytest.mark.parametrize("mutation", ["node", "alias", "edge"])
def test_canon_version_failure_rolls_back_the_ledger_graph_mutation(
    led: Ledger,
    store: SqliteStoryGraph,
    conn: Connection,
    pid: str,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    before_version = project.require_canon_version(conn, pid)
    before_counts = _counts(conn, pid)

    def fail_cas(*_args, **_kwargs):
        raise RuntimeError("CAS injection")

    monkeypatch.setattr(project, "compare_and_bump_canon_version", fail_cas)
    with pytest.raises(RuntimeError, match="CAS injection"):
        if mutation == "node":
            led.declare_node(NodeLabel.CHARACTER, "陆青禾")
        elif mutation == "alias":
            led.declare_alias(of="萧决", surface="决少")
        else:
            led.declare_where(
                who="萧决",
                loc="青云城",
                quote="青云城的雨下了一夜",
            )

    assert project.require_canon_version(conn, pid) == before_version
    assert _counts(conn, pid) == before_counts
    if mutation == "node":
        assert store.resolve(pid, ["陆青禾"])[0].hits == []
    elif mutation == "alias":
        assert store.resolve(pid, ["决少"])[0].hits == []


def test_replaying_a_retracted_edge_is_a_storage_and_version_noop(
    led: Ledger,
    conn: Connection,
    pid: str,
) -> None:
    first = led.declare_where(
        who="萧决",
        loc="青云城",
        quote="青云城的雨下了一夜",
    )
    replacement = led.declare_where(
        who="萧决",
        loc="北荒",
        quote="你身上流的不是萧家的血",
    )
    assert [edge.id for edge in replacement.retracted] == [first.edge.id]
    version = project.require_canon_version(conn, pid)
    evidence_rows = conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE project_id = ?",
        (pid,),
    ).fetchone()[0]

    repeated = led.declare_where(
        who="萧决",
        loc="青云城",
        quote="青云城的雨下了一夜",
    )

    assert repeated.edge.id == first.edge.id
    assert repeated.edge.status is EdgeStatus.RETRACTED
    assert repeated.evidence.id == first.evidence.id
    assert project.require_canon_version(conn, pid) == version
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE project_id = ?",
        (pid,),
    ).fetchone()[0] == evidence_rows


def test_ledger_rejects_an_outer_transaction_before_any_canon_write(
    led: Ledger,
    store: SqliteStoryGraph,
    conn: Connection,
    pid: str,
) -> None:
    version = project.require_canon_version(conn, pid)
    conn.execute("BEGIN IMMEDIATE")

    with pytest.raises(RuntimeError, match="外层事务"):
        led.declare_node(NodeLabel.CHARACTER, "陆青禾")
    # 模拟调用方吞掉异常并提交自己的事务；Ledger 仍不得留下半笔事实。
    conn.commit()

    assert project.require_canon_version(conn, pid) == version
    assert store.resolve(pid, ["陆青禾"])[0].hits == []


# ══════════════════════════════════════════════════════════════════════════
# 拒绝：代价必须是「什么都没发生」
# ══════════════════════════════════════════════════════════════════════════


def test_quote_not_found_writes_nothing(led: Ledger, conn: Connection, pid: str) -> None:
    before = _counts(conn, pid)
    with pytest.raises(QuoteNotFound):
        led.declare_where(who="萧决", loc="青云城", quote="这句话全书里没有")
    assert _counts(conn, pid) == before


def test_ambiguous_quote_shows_candidates_and_writes_nothing(
    led: Ledger, conn: Connection, pid: str
) -> None:
    before = _counts(conn, pid)
    with pytest.raises(AmbiguousQuote) as exc:
        led.declare_where(who="萧决", loc="青云城", quote="他终于明白")
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
        led.declare_where(who="师兄", loc="青云城", quote="你身上流的不是萧家的血")
    assert exc.value.surface == "师兄"
    assert sorted(c.name for c in exc.value.candidates) == ["李管家", "萧决"]
    assert _counts(conn, pid) == before


def test_unknown_name_writes_nothing(led: Ledger, conn: Connection, pid: str) -> None:
    from novel_harness.declare import UnknownName

    before = _counts(conn, pid)
    with pytest.raises(UnknownName):
        led.declare_where(who="没有这个人", loc="青云城", quote="你身上流的不是萧家的血")
    assert _counts(conn, pid) == before
def test_where_refuses_a_character_in_the_location_slot(led: Ledger) -> None:
    with pytest.raises(WrongLabel):
        led.declare_where(who="萧决", loc="李管家", quote="北荒的风比刀还利")


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
# ══════════════════════════════════════════════════════════════════════════
# 证据：双指针 + locate
# ══════════════════════════════════════════════════════════════════════════


def test_evidence_pointers_coincide_on_write(led: Ledger) -> None:
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
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
    d = led.declare_where(who="萧决", loc="青云城", quote="你身上流的不是萧家的血")
    assert isinstance(d, Declaration)
    with pytest.raises(Exception):  # noqa: B017 —— 要钉的是「改不动」，不是 pydantic 的错误分类
        d.decision_id = "x"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# 首现章 / 生死 —— R2 与 R3 的作者入口（2026-08-13）
# ══════════════════════════════════════════════════════════════════════════


def test_first_appearance_chapter_comes_from_the_quote(led: Ledger) -> None:
    """**首现章也是算出来的。** 签名里只有称呼和引语，同 `declare_knows`。

    在这条方法之前，`node.props.first_appears_chapter` 生产上零写入方，
    R2 FUTURE_LEAK 和 R3 的「未登场」那一半因此结构上永远不可能开火
    （行为侧的证明在 `tests/test_rules_fire.py`，这里量的是章号的血统）。
    """
    first = led.declare_first_appearance(of="北荒", quote="北荒的风比刀还利")

    assert first.chapter == 3
    assert first.previous_chapter is None
    assert first.node.name == "北荒"


def test_declaring_a_first_appearance_again_reports_what_it_overwrote(led: Ledger) -> None:
    """它**会覆盖**上一次的答案，而覆盖掉的那个数在库里没有第二份（节点不是时态的）。"""
    led.declare_first_appearance(of="北荒", quote="北荒的风比刀还利")
    again = led.declare_first_appearance(of="北荒", quote="青云城的雨下了一夜。")

    assert (again.chapter, again.previous_chapter) == (1, 3)


def test_declare_dead_writes_the_machine_key_not_the_chinese(led: Ledger) -> None:
    """R3 的判据是 `value_key`，**不是** `value` 里那个字。

    这条断言就是 ADR 0005 铁律在写入侧的具身：`value_key` 由引擎写死，所以 R3 永远
    不必去回答「陨落 / 坐化 / 兵解 是不是死了」——那是「这句话是什么意思」。
    """
    from novel_harness.graph import HealthValue

    decl = led.declare_dead(who="萧决", quote="青云城的雨下了一夜。")

    assert decl.edge.type is EdgeType.HAS_STATE
    assert decl.edge.props.value_key == HealthValue.DEAD
    assert decl.valid_from == 1  # 引语落在第 1 章，没有人输过这个数


def test_declare_dead_builds_the_dimension_it_needs(
    led: Ledger, store: SqliteStoryGraph, pid: str
) -> None:
    """状态维度由引擎自己建（作者没有、也不该有建它的入口），且**按 dim_key 认身份**。

    第二次声明必须落在同一个维度上：两个 StateDim 共享 `dim_key` 时 supersede 认为
    那是两个维度，一条都不闭合，而 `is_dead` 的 `any()` 让 dead 永远压过 alive。
    """
    from novel_harness.graph import HEALTH_DIM_KEY

    first = led.declare_dead(who="萧决", quote="青云城的雨下了一夜。")
    second = led.declare_dead(who="李管家", quote="李管家撑着伞站在阶下。")

    assert first.edge.dst == second.edge.dst
    snapshot = store.state_at(pid, first.edge.src, 3)
    assert snapshot.is_dead
    assert [s.dim_key for s in snapshot.states] == [HEALTH_DIM_KEY]


def test_declaring_a_node_again_does_not_wipe_what_someone_else_wrote(led: Ledger) -> None:
    """`declare_node` 的 `props` 是 patch 不是替换。

    **这是一次静默的数据丢失**：`upsert_node` 撞上幂等键时整列覆盖 `props_json`，
    于是「再声明一次萧决，顺便标个首现章」会把抽取写进去的人物档案抹掉，
    而没有任何一步会报错。
    """
    from novel_harness.graph import NodeProps

    led.declare_node(NodeLabel.CHARACTER, "萧决", props=NodeProps(gender="男"))
    node = led.declare_node(
        NodeLabel.CHARACTER, "萧决", props=NodeProps(first_appears_chapter=7)
    )

    assert node.props.first_appears_chapter == 7
    assert node.props.gender == "男", "第二次声明把第一次写的东西抹掉了"
    # 反面：什么都不给 = 一个字段都不动（而不是「清空」）。
    assert led.declare_node(NodeLabel.CHARACTER, "萧决").props.gender == "男"
