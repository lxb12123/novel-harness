"""通知带着定位（M1-c）+ 第四种「只告警不阻断」的通知（M1-d）。

这份文件钉两件在真实运行链路上的事，两件各自都曾经**坏了也不会有任何东西红**：

1. **锚要跟着通知走。** 规则产出的 `Issue` 本来就带精确的锚
   `(para_index, quote_text, occurrence_k)`（ADR 0006，禁止 offset），但那个锚
   以前停在 `validation_report` 里，通知只说一句「第 N 章的正文检查发现需要留意
   的地方」——作者点不过去。这里逐字比对**库里那份报告的第一条 Issue 的锚**和
   **通知的 `jump`**：不相等就红。

2. **新类型不许阻断。** `validation_blocked` 那句「新正文不会再自动生成总结与
   情节」是真的（停下游的是 `chapter_refresh` 的闸门）。事后语义核对按 ADR 0030
   只许告警——挂错档的后果是**作者改一个老章就把那一章的自动整理停掉，而那不会有
   任何东西报错**。所以这里不是断言一个常量，是**真跑一遍固定 DAG**，看两支
   adapter 到底被调没被调。

   为了让「照常跑」这句话不是空的，同一份夹具上还跑一遍**真的阻断**那条路
   （同一本书、同一章、同一套 adapter）——两条对照都在这个文件里，其中一条
   变绿变得太容易时另一条会拆穿它。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.chapter_refresh import (
    BranchContext,
    ChapterRefreshCoordinator,
    claim_attempt,
)
from test_chapter_refresh import an_attempt
from novel_harness.db import Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel, TextAnchor
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.system_notifications import (
    BLOCKING_KINDS,
    background_failure_dedupe_key,
    enqueue_text_advisory,
    enqueue_validation_blocked,
    list_open_notifications,
    materialize_notification_outbox,
)

# ⚠️ 2026-08-27：这份夹具原来用的是「幽泉窟第 2 章才头一回露面 → 第 1 章那句提及
# 是 R2 FUTURE_LEAK」。R2 砍了（ADR 0040），换成 R3 DEAD_SPEAKS 的另一半：
# 顾清音第 2 章才头一回露面 → 第 1 章那句「顾清音道」是还没登场就说话。
# 引语在全书里只出现一次（`Ledger._one_candidate` 多于一处就拒）。
BOOK = (
    "第一章 山门\n"
    "\n"
    "萧决拾级而上，山门在雾里。\n"
    "顾清音道：「你来了。」\n"
    "\n"
    "第二章 崖底\n"
    "\n"
    "顾清音第一次踏进这座山门。\n"
)
GU_DEBUT_QUOTE = "顾清音第一次踏进这座山门。"


class Stub:
    """记调用的 BranchAdapter。**这份文件全部的判据就是它有没有被调到。**"""

    def __init__(self, note: str = "done") -> None:
        self.note = note
        self.calls: list[BranchContext] = []

    def run(self, ctx: BranchContext) -> str:
        self.calls.append(ctx)
        return self.note


@pytest.fixture
def world(tmp_path: Path) -> Iterator[dict[str, object]]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="山门记", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK, encoding="utf-8")
    importer.import_book(store, pid, txt=txt, root=root)
    conn.commit()
    chapter_id = next(ct.chapter_id for ct in store.current_snapshots(pid) if ct.number == 1)
    snapshot_id = next(ct.snapshot_id for ct in store.current_snapshots(pid) if ct.number == 1)
    yield {
        "conn": conn,
        "db": str(db),
        "pid": pid,
        "store": store,
        "chapter_id": chapter_id,
        "snapshot_id": snapshot_id,
    }
    conn.close()


def _declare_late_debut(world: dict[str, object]) -> None:
    """作者声明「顾清音头一回露面在第 2 章」——章号是系统从引语算的，没人敲过。"""
    ledger = Ledger(world["store"], world["conn"], str(world["pid"]))
    ledger.declare_node(NodeLabel.CHARACTER, "顾清音")
    ledger.declare_first_appearance(of="顾清音", quote=GU_DEBUT_QUOTE)
    world["conn"].commit()


def _coordinator(world: dict[str, object]) -> ChapterRefreshCoordinator:
    path = str(world["db"])
    return ChapterRefreshCoordinator(
        world["conn"],
        connection_factory=lambda: connect(Path(path)),
        store_factory=lambda c: SqliteStoryGraph(c),
    )


def _run_refresh(world: dict[str, object], trigger: str) -> tuple[dict[str, str], Stub, Stub]:
    attempt_id = an_attempt(
        world["conn"],
        project_id=str(world["pid"]),
        chapter_id=str(world["chapter_id"]),
        snapshot_id=str(world["snapshot_id"]),
    )
    world["conn"].commit()
    token = claim_attempt(world["conn"], attempt_id, owner="w1")
    assert token is not None
    summary, extraction = Stub("summary-ok"), Stub("extraction-ok")
    outcome = _coordinator(world).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
        # 别名那一支给个「没变」，好让 final gate 走到终态——不给的话闸门恒 PENDING，
        # 「照常跑」这句话就只验到一半（同 `test_chapter_refresh` 里那条并行测试）。
        alias_adapter=Stub("unchanged"),
    )
    materialize_notification_outbox(
        world["conn"], project_id=str(world["pid"]), lease_owner="t"
    )
    return outcome, summary, extraction


def _report_row(conn: Connection, project_id: str) -> dict:
    row = conn.execute(
        "SELECT id, gate, issues_json FROM validation_report "
        "WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    assert row is not None, "一条验证报告都没落——下面的比对全是空的"
    return dict(row)


# ══════════════════════════════════════════════════════════════════════════
# M1-c：锚跟着通知走
# ══════════════════════════════════════════════════════════════════════════


def test_the_blocked_notification_carries_the_issue_anchor(world: dict[str, object]) -> None:
    """通知的 `jump` **逐字段等于**报告里第一条 Issue 的锚。

    比的是库里那份报告，不是测试自己算的期望值——期望值写错了两边会一起错。
    """
    _declare_late_debut(world)
    outcome, summary, extraction = _run_refresh(world, "manual:blocked")
    assert outcome["validation"] == "blocked"

    report = _report_row(world["conn"], str(world["pid"]))
    issues = json.loads(report["issues_json"])
    assert issues, "R3 没开火 —— 这条测试后面比的东西全都不存在"
    first = issues[0]["anchor"]

    notices = list_open_notifications(world["conn"], str(world["pid"]))
    assert len(notices) == 1
    notice = notices[0]
    assert notice.kind == "validation_blocked"
    assert notice.jump is not None, "锚没跟着通知走 —— 作者点不过去"
    assert notice.jump.para_index == first["para_index"]
    assert notice.jump.quote_text == first["quote_text"]
    assert notice.jump.occurrence_k == first["occurrence_k"]
    # 下游确实停了：这条对照让下面那条「不阻断」的断言不是白绿的。
    assert summary.calls == [] and extraction.calls == []


def test_the_blocked_notification_says_which_rule_and_which_paragraph(
    world: dict[str, object],
) -> None:
    """`title_params` 要说得出哪一段、哪条规则、哪一句 —— 而且**规则名不许是编号**。

    段号按作者的数法从 1 起（`TextAnchor` 内部 0-based，只在通知层换算一次）。

    国际化第四批 Phase B 之后，整句怎么拼（含"新正文不会再自动生成总结与情节"
    那句固定的副作用说明）是前端 `backendMessages.ts` 的模板函数的事，不再是
    后端拼出来的字符串——那半条断言搬到了 `frontend/src/backendMessages.test.ts`。
    这儿只钉后端发出去的原始事实是不是对的。
    """
    _declare_late_debut(world)
    _run_refresh(world, "manual:title")
    notice = list_open_notifications(world["conn"], str(world["pid"]))[0]

    report = _report_row(world["conn"], str(world["pid"]))
    first = json.loads(report["issues_json"])[0]
    assert notice.title_code == "validation_blocked_title"
    params = notice.title_params
    assert params is not None
    assert params["paragraph"] == first["anchor"]["para_index"] + 1
    assert params["rule_title"] == "人物开口时机", "说不出哪条规则"
    assert params["issue_message"] == first["message"], "说不出哪一句"
    assert "R2" not in params["rule_title"] and "R3" not in params["rule_title"]


def test_a_blocked_report_without_issues_refuses_to_file_an_unclickable_notice(
    world: dict[str, object],
) -> None:
    """gate=blocked 却零 issue = 报告和闸门对不上。宁可炸，不落一条点不动的通知。"""
    from novel_harness.checks.service import SnapshotValidationReport

    empty = SnapshotValidationReport(
        id="report:x",
        project_id=str(world["pid"]),
        chapter_id=str(world["chapter_id"]),
        chapter_number=1,
        source_snapshot_id=str(world["snapshot_id"]),
        source_generation=1,
        refresh_attempt_id=None,
        phase="initial",
        text_sha256="0" * 64,
        ruleset_epoch=1,
        ruleset_hash="x",
        gate="blocked",
        rules=(),
        issues=(),
    )
    with pytest.raises(ValueError):
        enqueue_validation_blocked(world["conn"], report=empty, attempt_id="attempt:x")


# ══════════════════════════════════════════════════════════════════════════
# M1-d：第四种通知，只告警不阻断
# ══════════════════════════════════════════════════════════════════════════


def test_a_text_advisory_does_not_stop_the_chapter_from_being_organized(
    world: dict[str, object],
) -> None:
    """**这条最值钱。** 落一条 `text_advisory` 之后，那一章的总结与抽取照常跑。

    挂错成 `validation_blocked` 的后果是作者改一个老章就把整章的自动整理停掉，
    而那**不会有任何东西报错**——只会表现成「总结怎么一直不更新」。
    上面那条阻断的对照跑的是同一本书、同一章、同一套 adapter。
    """
    conn = world["conn"]
    conn.execute("BEGIN IMMEDIATE")
    enqueue_text_advisory(
        conn,
        project_id=str(world["pid"]),
        chapter_id=str(world["chapter_id"]),
        chapter_number=1,
        title_code="test_notice",
        title_params=None,
        dedupe_key=background_failure_dedupe_key(
            kind="text_advisory",
            subject_type="chapter",
            subject_id=str(world["chapter_id"]),
            operation="secret_spoken",
            source_snapshot_id=str(world["snapshot_id"]),
            job_id="job:1",
        ),
        jump=TextAnchor(para_index=1, quote_text="顾清音道", occurrence_k=0),
    )
    conn.commit()

    outcome, summary, extraction = _run_refresh(world, "manual:advisory")

    assert outcome["validation"] == "passed"
    assert len(summary.calls) == 1, "只告警的通知把总结那一支停掉了"
    assert len(extraction.calls) == 1, "只告警的通知把抽取那一支停掉了"
    row = conn.execute(
        "SELECT summary_state, extraction_state, final_gate_state "
        "FROM chapter_refresh_attempt ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    assert row["summary_state"] == "SUCCEEDED"
    assert row["extraction_state"] == "SUCCEEDED"
    assert row["final_gate_state"] == "PASSED"

    notices = list_open_notifications(conn, str(world["pid"]))
    assert [n.kind for n in notices] == ["text_advisory"]
    assert notices[0].jump is not None and notices[0].jump.quote_text == "顾清音道"


def test_the_new_kind_is_on_the_non_blocking_side(world: dict[str, object]) -> None:
    """`BLOCKING_KINDS` 是「这一档落下时下游真的停了」的唯一落笔处。

    上面那条行为测试证明的是今天；这一条挡的是明天有人把新档加进阻断那一侧却
    不去改闸门——那时通知就在撒谎。
    """
    assert "validation_blocked" in BLOCKING_KINDS
    assert "text_advisory" not in BLOCKING_KINDS


def test_a_text_advisory_without_a_quote_is_refused(world: dict[str, object]) -> None:
    """说不出「在哪一句」的告警作者点不过去，那正是这一层要补的洞——空锚不许落库。"""
    with pytest.raises(ValueError):
        enqueue_text_advisory(
            world["conn"],
            project_id=str(world["pid"]),
            chapter_id=str(world["chapter_id"]),
            chapter_number=1,
            title_code="test_notice",
            title_params=None,
            dedupe_key="k",
            jump=TextAnchor(para_index=0, quote_text="   ", occurrence_k=0),
        )
