"""decisions.py —— append-only（PLAN §5.7 / ADR 0003）。

这张表记的是全库唯一真正不可重建的资产。所以本文件测的不只是「append 能写进去」，
更重要的是那三条**没有**：没有修改入口、没有删除入口、没有把 quote_sha256 交给调用方。
"""

from __future__ import annotations

import hashlib
import inspect
import sqlite3
from pathlib import Path

import pytest

from novel_harness import decisions
from novel_harness.db import connect, migrate
from novel_harness.decisions import DecisionKind, Verdict, append, quote_hash, read
from novel_harness.ids import new_project_id


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "nh.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def project() -> str:
    # 注意：decision_log 没有指向 project 的外键，所以这里不需要真的建一行 project。
    # 那不是疏忽——见 test_append_needs_no_project_row。
    return new_project_id()


# ══════════════════════════════════════════════════════════════════════════
# API 面：这里测的是「不存在」，不是「存在」
# ══════════════════════════════════════════════════════════════════════════

_MUTATION_PREFIXES = ("update", "delete", "remove", "edit", "set_", "patch", "drop", "purge")


def test_module_exposes_no_mutation_entry_point() -> None:
    """**「只有 append() 没有 update()」是 §5.7 的原话，这条把它变成一条测试。**

    纪律会在某个赶时间的下午被绕过——比如某个 issue 说「作者点错了想改一下」，而
    改一条已经落库的确认，等于把这张表从「不可重建资产的保险」降级成「一张普通的表」。
    正确做法永远是**再 append 一条**（Verdict.EDIT 就是为这个而存在的）。
    """
    public = [
        name
        for name, obj in vars(decisions).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == decisions.__name__
    ]
    assert sorted(public) == ["append", "quote_hash", "read"]
    for name in public:
        assert not name.startswith(_MUTATION_PREFIXES), f"decisions.{name} 是个修改入口"


def test_append_has_no_quote_sha256_parameter() -> None:
    """没有这个参数是故意的（ADR 0006 配套第 3 条）。

    开着这个口子，迟早有调用方把**抽取器返回的那个字符串**的哈希传进来，而那个字符串
    有 10–30% 不是逐字原文——锚从写下去那一刻就是坏的，且不会有任何东西报错。
    """
    params = inspect.signature(append).parameters
    assert "quote_sha256" not in params
    # ts 同理：形状由 SQL 的 DEFAULT 统一（Python 的 isoformat 出的是 +00:00 不是 Z）。
    assert "ts" not in params


# ══════════════════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════════════════


def test_append_roundtrip(conn: sqlite3.Connection, project: str) -> None:
    d = append(
        conn,
        project_id=project,
        kind=DecisionKind.KNOWS_DECLARE,
        decision=Verdict.ACCEPT,
        subject_name="萧决",
        quote_text="他终于明白，师父早就知道了。",
        chapter_number=88,
        para_index=3,
        payload={"secret": "主角是魔尊转世", "state": "KNOWS"},
    )
    assert d.id.startswith("decision:")
    assert d.decision is Verdict.ACCEPT
    assert d.payload == {"secret": "主角是魔尊转世", "state": "KNOWS"}
    assert d.actor == "author"

    (back,) = read(conn, project)
    assert back == d


def test_quote_sha256_is_derived_from_quote_text(conn: sqlite3.Connection, project: str) -> None:
    quote = "他终于明白，师父早就知道了。"
    d = append(
        conn,
        project_id=project,
        kind="knows_declare",
        decision=Verdict.ACCEPT,
        quote_text=quote,
    )
    assert d.quote_sha256 == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    assert d.quote_sha256 == quote_hash(quote)


def test_quote_hash_does_not_normalize() -> None:
    """**不归一化是硬要求。**

    `evidence.quote_sha256` 算的是「在正文里定位成功后的原文子串」（ADR 0006）。
    这边只要 `.strip()` 或 NFC 一下，两边的哈希就永远对不上，而 decision_log 的
    全部价值就是重放时能对上号。
    """
    assert quote_hash(" 他死了 ") != quote_hash("他死了")
    # 必须写成转义：源文件本身会被编辑器/工具静默 NFC 归一化，直接贴 NFD 字面量的话
    # 这条测试会在某次「无关」的改动后突然变成 NFC == NFC 而恒真——一条假测试。
    assert quote_hash("\u00e9") != quote_hash("e\u0301")  # NFC vs NFD 的同一个 é


def test_quote_fields_live_and_die_together(conn: sqlite3.Connection, project: str) -> None:
    d = append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    assert d.quote_text is None
    assert d.quote_sha256 is None


def test_timestamp_shape_matches_the_rest_of_the_db(
    conn: sqlite3.Connection, project: str
) -> None:
    """全库统一 ISO 8601 / UTC / 毫秒 / **'Z'**。

    Python 的 `datetime.now(UTC).isoformat()` 出的是 `+00:00`，与 'Z' 混存会把同秒记录的
    字典序排错——而 read() 正是按 ts 排的，重放顺序错了就会「先声明 KNOWS 再合并别名」。
    所以 ts 由 SQL 的 DEFAULT 写，Python 侧一次都不写。
    """
    d = append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    assert d.ts.endswith("Z")
    assert "+" not in d.ts
    assert len(d.ts) == len("2026-07-16T00:00:00.000Z")


def test_append_needs_no_project_row(conn: sqlite3.Connection, project: str) -> None:
    """`decision_log` 一个外键都没有，连 project_id 都没有——所以这里没建 project 行也能写。

    它必须能比它记录的一切活得更久。这条测试看起来像在测「约束太松」，实际测的是
    §5.7 那个刻意的缺省：指向 project 的外键会让「删掉项目」顺手删掉唯一不可重建的资产。
    """
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    assert len(read(conn, project)) == 1


def test_kind_is_an_open_string(conn: sqlite3.Connection, project: str) -> None:
    # kind 不参与任何过滤分支，写错了只是日志难看；SQLite 改 CHECK 要重建整张表。
    # DecisionKind 是**约定**不是强制——加一类确认不必动 schema。
    d = append(conn, project_id=project, kind="scene_cast_declare", decision=Verdict.EDIT)
    assert d.kind == "scene_cast_declare"


def test_append_rejects_empty_project_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="project_id"):
        append(conn, project_id="", kind="alias_merge", decision=Verdict.ACCEPT)


def test_append_rejects_empty_kind(conn: sqlite3.Connection, project: str) -> None:
    # 重放时靠 kind 分派到对应的重建逻辑。空 kind 的记录重放不回去 = 等于丢了。
    with pytest.raises(ValueError, match="kind"):
        append(conn, project_id=project, kind="", decision=Verdict.ACCEPT)


def test_append_rejects_unknown_verdict(conn: sqlite3.Connection, project: str) -> None:
    bad: Verdict = "maybe"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        append(conn, project_id=project, kind="alias_merge", decision=bad)


def test_payload_keeps_chinese_readable(conn: sqlite3.Connection, project: str) -> None:
    # ensure_ascii=False：这张表是要被人读、被将来的迁移脚本读的，\u xxxx 逃逸是给机器看的。
    append(
        conn,
        project_id=project,
        kind="secret_declare",
        decision=Verdict.ACCEPT,
        payload={"name": "主角是魔尊转世"},
    )
    raw = conn.execute("SELECT payload_json FROM decision_log").fetchone()[0]
    assert "主角是魔尊转世" in raw


# ══════════════════════════════════════════════════════════════════════════
# 读回：顺序就是重放顺序
# ══════════════════════════════════════════════════════════════════════════


def test_read_is_in_replay_order(conn: sqlite3.Connection, project: str) -> None:
    """顺序错了，「先合并别名再声明 KNOWS」会重放成「先声明 KNOWS 再合并别名」——
    后者指向一个还不存在的节点。

    ts 只有毫秒精度，一个循环里的 20 条大概率同毫秒——所以这条同时也在测
    「同 ts 时靠 ULID 断平」这件事（ADR 0003 选 ULID 而非 UUIDv4 的理由之一）。
    """
    for i in range(20):
        append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT,
               payload={"i": i})
    got = [d.payload["i"] for d in read(conn, project)]
    assert got == list(range(20))


def test_read_filters_by_project(conn: sqlite3.Connection, project: str) -> None:
    other = new_project_id()
    append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    append(conn, project_id=other, kind="alias_merge", decision=Verdict.ACCEPT)
    assert len(read(conn, project)) == 1
    assert len(read(conn, other)) == 1


def test_read_filters_by_kind_and_limit(conn: sqlite3.Connection, project: str) -> None:
    for kind in ("alias_merge", "alias_merge", "secret_declare"):
        append(conn, project_id=project, kind=kind, decision=Verdict.ACCEPT)
    assert len(read(conn, project, kind="alias_merge")) == 2
    assert len(read(conn, project, kind="secret_declare")) == 1
    assert len(read(conn, project, limit=1)) == 1
    assert read(conn, project, kind="不存在的 kind") == []


def test_decision_is_frozen(conn: sqlite3.Connection, project: str) -> None:
    # 类型层复读一遍这张表的语义：读回来的东西也改不动。
    d = append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    with pytest.raises(Exception):
        d.decision = Verdict.REJECT  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# 引擎层：API 面挡不住的那条路
# ══════════════════════════════════════════════════════════════════════════


def test_engine_rejects_update_and_delete(conn: sqlite3.Connection, project: str) -> None:
    """API 层没有入口是第一道；触发器是第二道。**两道都要有**：

    第一道挡的是「decisions.py 长出一个 update()」，第二道挡的是「有人拿着 conn
    直接写 SQL」——而 db.connect() 把 conn 交给了每一个模块。
    """
    d = append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    with pytest.raises(sqlite3.IntegrityError, match="只增不改"):
        conn.execute("UPDATE decision_log SET decision = 'reject' WHERE id = ?", (d.id,))
    with pytest.raises(sqlite3.IntegrityError, match="只增不删"):
        conn.execute("DELETE FROM decision_log WHERE id = ?", (d.id,))
    assert read(conn, project) == [d]


def test_engine_rejects_insert_or_replace(conn: sqlite3.Connection, project: str) -> None:
    """**REPLACE 这一路曾经是敞开的，而上面那条测试对此全绿。**

    两个触发器曾经只有 BEFORE UPDATE / BEFORE DELETE，而 SQLite 默认
    `PRAGMA recursive_triggers = 0`（本库实测就是 0）——此时 `INSERT OR REPLACE` 内部的
    隐式 DELETE **不触发 BEFORE DELETE 触发器**。于是 UPDATE 和 DELETE 被拦得死死的，
    REPLACE 大摇大摆走过去，把一条已落库的确认就地改写：**无痕、无报错、行数不变**。

    这一条打的正好是全仓最重的那句自我论证——decisions.py 的 docstring 写着「纪律会在
    某个赶时间的下午被绕过，所以同一条约束在三层上都成立……2. 引擎层」，而引擎层实际
    只覆盖了三个写动词里的两个。§5.7 说这张表是「全库唯一真正不可重建的资产」。

    今天仓库里 `INSERT OR REPLACE` 的命中数是 0，所以这不是一个正在发火的 bug，
    是**一层被宣称存在、实际不存在的防御**。它的危害是时间差：写下那一行的那个下午，
    没有任何东西会报错。
    """
    d = append(
        conn, project_id=project, kind="knows_declare", decision=Verdict.ACCEPT,
        subject_name="萧决", quote_text="萧决终于知道了自己的身世。", chapter_number=88,
    )
    with pytest.raises(sqlite3.IntegrityError, match="只增不改"):
        conn.execute(
            "INSERT OR REPLACE INTO decision_log"
            " (id, project_id, kind, subject_name, payload_json, decision, actor)"
            " VALUES (?, ?, 'knows_declare', '李管家', '{}', 'reject', 'attacker')",
            (d.id, project),
        )
    conn.rollback()
    assert read(conn, project) == [d], "作者点的 accept/萧决 必须原样还在"


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "INSERT OR IGNORE", "INSERT"])
def test_no_write_verb_can_overwrite_an_existing_id(
    conn: sqlite3.Connection, project: str, verb: str
) -> None:
    """BEFORE INSERT 那道守卫对三个动词一视同仁，**且不依赖任何连接级 PRAGMA**。

    `PRAGMA recursive_triggers = ON` 也能让 REPLACE 触发 BEFORE DELETE，但它是**连接级**
    的——靠每条连接都记得设它，本身就是一条纪律，而纪律正是这张表不打算依赖的东西。
    """
    d = append(conn, project_id=project, kind="alias_merge", decision=Verdict.ACCEPT)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"{verb} INTO decision_log (id, project_id, kind, payload_json, decision)"
            " VALUES (?, ?, 'alias_merge', '{}', 'reject')",
            (d.id, project),
        )
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 1
    assert read(conn, project)[0].decision is Verdict.ACCEPT


def test_append_still_works_after_the_insert_guard(
    conn: sqlite3.Connection, project: str
) -> None:
    """反面：那道 BEFORE INSERT 守卫**不许拦住正常的 append**。

    它的 WHERE 只在 id 已存在时成立，而 id 是 ULID——这条测试钉的是「守卫没有把
    整张表变成只读」，那是一个改坏了会让全部功能静默失效的方向。
    """
    written = [
        append(conn, project_id=project, kind="secret_declare", decision=Verdict.ACCEPT)
        for _ in range(5)
    ]
    assert [d.id for d in read(conn, project)] == [d.id for d in written]


def test_correction_is_a_new_append_not_an_update(conn: sqlite3.Connection, project: str) -> None:
    """作者点错了怎么办：**再 append 一条**。`Verdict.EDIT` 就是为这个而存在的。

    历史是资产——「他先接受了、后来改主意了」比「他接受了」信息量更大，尤其是在
    v1→v2 重放的时候（那时你要知道的正是他最后的意思，而不是他第一次的手滑）。
    """
    first = append(
        conn, project_id=project, kind="knows_declare", decision=Verdict.ACCEPT,
        subject_name="萧决", payload={"state": "KNOWS"},
    )
    second = append(
        conn, project_id=project, kind="knows_declare", decision=Verdict.EDIT,
        subject_name="萧决", payload={"state": "BELIEVES", "supersedes": first.id},
    )
    history = read(conn, project)
    assert [d.id for d in history] == [first.id, second.id]
    assert history[-1].payload["supersedes"] == first.id
