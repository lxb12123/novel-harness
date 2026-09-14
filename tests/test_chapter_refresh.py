"""固定章节刷新协调器（Task 5）—— 固定 DAG、幂等 coverage、lease/fencing、真并行。

本文件**只用 stub adapter**：summary/extraction 的 head CAS 是 Task 6/9 的活，
这里证明的是协调器本身——分支并行、final gate 不把初次 PASSED 当最终闸门、
lease 抢领与旧 token 失效、coverage 幂等不重复付费。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.chapter_refresh import (
    BRANCH_EXTRACTION,
    BRANCH_SUMMARY,
    BRANCH_VALIDATION,
    MAX_AUTO_RETRIES,
    _head_missing,
    BranchContext,
    ChapterRefreshCoordinator,
    CoverageDecision,
    activate_extraction_application,
    claim_attempt,
    ensure_refresh_coverage,
    heartbeat,
    release_attempt,
)
from novel_harness.db import Connection, connect, migrate
from novel_harness.graph import ChapterSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph


class Stub:
    """可编程的 BranchAdapter：记调用、可阻塞在 barrier、可抛异常。"""

    def __init__(
        self,
        barrier: threading.Barrier | None = None,
        note: str = "done",
        fail: Exception | None = None,
    ) -> None:
        self.barrier = barrier
        self.note = note
        self.fail = fail
        self.calls: list[BranchContext] = []

    def run(self, ctx: BranchContext) -> str:
        self.calls.append(ctx)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if self.fail is not None:
            raise self.fail
        return self.note


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    c = connect(tmp_path / "refresh.db")
    migrate(c)
    yield c
    c.close()


def _seed_chapter(conn: Connection, tmp_path: Path) -> tuple[str, str]:
    """一本一章的书，返回 (project_id, chapter_id)。"""
    root = tmp_path / "book"
    pid = project.create(conn, name="刷新测试", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    text = "第一章 甲\n\n萧决走进来了。\n"
    src = root / "s.txt"
    root.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    current = next(ct for ct in store.current_snapshots(pid) if ct.number == 1)
    return pid, current.chapter_id


def _snapshot_id(conn: Connection, pid: str) -> str:
    return next(
        ct.snapshot_id for ct in SqliteStoryGraph(conn).current_snapshots(pid) if ct.number == 1
    )


def an_attempt(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    mask: int = BRANCH_VALIDATION | BRANCH_SUMMARY | BRANCH_EXTRACTION,
    generation: int = 1,
) -> str:
    """建一张待跑的单，**给要「手里先有一张单」的那些测试建场用**。

    ── 它从前走 `create_manual_attempt`（2026-08-25 删了）──────────────────

    那条是「作者点重新整理」的产方，而手动入口整条下线之后它零生产调用。
    这些测试要的从来不是「manual 那一种单」，是「一张单」——所以改走
    `ensure_refresh_coverage`，也就是**今天生产上唯一还在建单的那条路**。
    建场跟着生产走，测试才测得到真实形状。

    `missing_check` 直接给死 mask：从前 `create_manual_attempt` 收
    `missing_branch_mask=`，这儿是同一件事的注入口。**coverage 的唯一键带着 mask**
    （`idx_refresh_attempt_coverage`），所以同一个 run 上 mask 不同就是两张单——
    「两张单竞争同一个 head」那类场景照旧构造得出来。
    """
    decision = ensure_refresh_coverage(
        conn,
        project_id=project_id,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=generation,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=lambda *_: mask,
    )
    assert decision.attempt_id is not None, "没建出单来 —— 建场就已经不成立了"
    return decision.attempt_id


def _coordinator(conn: Connection) -> ChapterRefreshCoordinator:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])

    def factory() -> Connection:
        return connect(path)

    return ChapterRefreshCoordinator(
        conn,
        connection_factory=factory,
        store_factory=lambda c: SqliteStoryGraph(c),
    )


def test_validation_blocked_stops_downstream_with_zero_summary_calls(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """验证阻断 → 总结/抽取调用数为 0，旧分支保持未启动。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    # 直接把验证状态预置成 BLOCKED，让 run 跳过验证、也跳过下游。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET validation_state = 'BLOCKED' WHERE id = ?",
        (attempt_id,),
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1", ttl_seconds=60)
    assert token is not None

    summary = Stub()
    extraction = Stub()
    outcome = _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
    )
    assert summary.calls == [] and extraction.calls == []
    assert outcome["validation"] == "blocked"


def test_summary_and_extraction_run_in_parallel(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """两个 stub 同时进入 barrier：调用顺序证明不了并行，barrier 通过才证明。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None

    barrier = threading.Barrier(2)
    summary = Stub(barrier=barrier, note="summary-ok")
    extraction = Stub(barrier=barrier, note="extraction-ok")
    outcome = _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
        alias_adapter=Stub(note="unchanged"),
    )
    assert outcome["summary_state"] == "summary-ok"
    assert outcome["extraction_state"] == "extraction-ok"
    assert len(summary.calls) == len(extraction.calls) == 1
    row = conn.execute(
        "SELECT summary_state, extraction_state, final_gate_state, final_validation_report_id "
        "FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row["summary_state"] == "SUCCEEDED"
    assert row["extraction_state"] == "SUCCEEDED"
    assert row["final_gate_state"] == "PASSED"
    assert row["final_validation_report_id"] is not None


def test_alias_not_started_blocks_the_final_gate(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """初次 PASSED 但 alias_phase=NOT_STARTED：summary 候选 READY 也不能切 head
    （final gate 保持 PENDING）；alias 阶段确认 UNCHANGED 后才 PASSED。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None

    coord = _coordinator(conn)
    outcome = coord.run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=Stub(note="candidate-ready"),
        extraction_adapter=Stub(),
        # 不给 alias adapter：NOT_STARTED 保持非终态
    )
    assert outcome["final_gate"] == "PENDING"
    row = conn.execute(
        "SELECT final_gate_state, alias_phase FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row["final_gate_state"] == "PENDING"
    assert row["alias_phase"] == "NOT_STARTED"

    # 第二次运行：alias 阶段确认未变化 → final gate PASSED + final report 存在。
    release_attempt(conn, attempt_id, owner="w1", token=token)
    token2 = claim_attempt(conn, attempt_id, owner="w2")
    assert token2 is not None
    outcome2 = coord.run(
        attempt_id,
        owner="w2",
        token=token2,
        summary_adapter=Stub(note="re-run"),
        extraction_adapter=Stub(),
        alias_adapter=Stub(note="unchanged"),
    )
    assert outcome2["final_gate"] is not None
    row2 = conn.execute(
        "SELECT final_gate_state, final_validation_report_id FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row2["final_gate_state"] == "PASSED"
    assert row2["final_validation_report_id"] is not None


def test_heartbeat_keeps_lease_and_stale_token_cas_fails(
    conn: Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """heartbeat 正常 → 第二 worker 抢不到；心跳停了 → 新 claim 拿到更大的 token，
    旧 worker 的状态写 CAS 失败。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    conn.commit()
    now = [time.time()]
    monkeypatch.setattr("novel_harness.chapter_refresh.time.time", lambda: now[0])
    ttl = 60.0

    token1 = claim_attempt(conn, attempt_id, owner="w1", ttl_seconds=ttl, now=now[0])
    assert token1 == 1
    # heartbeat 续租 → 第二 worker 在过期前抢不到。
    now[0] += 10
    assert heartbeat(conn, attempt_id, owner="w1", token=token1, ttl_seconds=ttl) is True
    assert claim_attempt(conn, attempt_id, owner="w2", ttl_seconds=ttl, now=now[0]) is None

    # 心跳停了（TTL 过期）→ w2 抢到，fencing token 单调递增。
    now[0] += 60
    token2 = claim_attempt(conn, attempt_id, owner="w2", ttl_seconds=ttl, now=now[0])
    assert token2 == 2

    # 旧 worker 恢复：owner/token 都不匹配 → 状态写失败。
    from novel_harness import chapter_refresh

    ok = chapter_refresh._branch_update(
        conn, attempt_id, owner="w1", token=1, column="summary_state", state="SUCCEEDED"
    )
    assert ok is False
    row = conn.execute(
        "SELECT summary_state FROM chapter_refresh_attempt WHERE id = ?", (attempt_id,)
    ).fetchone()
    assert row["summary_state"] == "PENDING"


def _validation_reports(conn: Connection, project_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM validation_report WHERE project_id = ?", (project_id,)
        ).fetchone()[0]
    )


@pytest.mark.parametrize("ordered", [1, 2, 3, 4, 5, 6, 7])
def test_the_coordinator_runs_exactly_the_branches_the_order_carries(
    conn: Connection, tmp_path: Path, ordered: int
) -> None:
    """**排了几支 = 跑了几支**（2026-08-23）。单上没有的那一支一次都不许跑。

    从前 `run` 一眼都不看 `missing_branch_mask`，只要那一列还是 `PENDING` 就跑。
    实测的后果：焦点防抖在下单那一层把总结位剥掉（mask=0b101），执行层照样调了
    一次总结模型——**作者正改着的那一章，改一下午存三十次就买三十次**，而
    「覆写单真去重买一份」那条裁定的成本论证靠的恰恰是这条防抖。

    它和 `test_reporting_an_overwrite_means_the_order_carries_the_summary_branch`
    是同一个病的两面：那条钉「报的 = 排的」，这条钉「排的 = 做的」。两条都在，
    报什么、排什么、做什么之间才第一次有人对过。
    """
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    if not ordered & BRANCH_VALIDATION:
        # 单上没有验证位的含义是「这一版正文已经验过了」。先真验一遍，
        # 否则协调器答不出「放不放行」，会（正确地）连下游一起停掉。
        warmup = an_attempt(
            conn,
            project_id=pid,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            mask=BRANCH_VALIDATION,
        )
        conn.commit()
        warm_token = claim_attempt(conn, warmup, owner="w0")
        assert warm_token is not None
        _coordinator(conn).run(
            warmup,
            owner="w0",
            token=warm_token,
            summary_adapter=Stub(),
            extraction_adapter=Stub(),
        )

    reports_before = _validation_reports(conn, pid)
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        mask=ordered,
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None
    summary = Stub(note="summary-ok")
    extraction = Stub(note="extraction-ok")
    _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=extraction,
    )

    ran = 0
    if _validation_reports(conn, pid) > reports_before:
        ran |= BRANCH_VALIDATION
    if summary.calls:
        ran |= BRANCH_SUMMARY
    if extraction.calls:
        ran |= BRANCH_EXTRACTION
    assert ran == ordered, (
        f"单上排了 0b{ordered:03b}，实际跑了 0b{ran:03b} —— 「排了什么」和"
        "「做了什么」对不上（多跑 = 花没排过的钱，少跑 = 报了没做）"
    )
    # 没排的那几支要落终态：留在 PENDING 的话这条 attempt 永远算「还有活」，
    # 每一波 pump 都把它重新 claim 一次，却什么都不做。
    row = conn.execute(
        "SELECT validation_state, summary_state, extraction_state "
        "FROM chapter_refresh_attempt WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    for column, branch in (
        ("validation_state", BRANCH_VALIDATION),
        ("summary_state", BRANCH_SUMMARY),
        ("extraction_state", BRANCH_EXTRACTION),
    ):
        if not ordered & branch:
            assert row[column] != "PENDING", f"{column} 没排也没落终态：{row[column]}"


def test_a_branch_left_off_the_order_does_not_slip_past_a_blocked_report(
    conn: Connection, tmp_path: Path
) -> None:
    """复用验证报告时复用的是**结论**，不只是「跑过了」。

    单上没有验证位 = 这一版正文已经验过。要是只当成「跳过验证」，一张只缺总结的
    单就能绕过闸门，把一章**已经判定阻断**的正文拿去总结和抽取——而闸门存在的
    全部理由就是不让那件事发生。
    """
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    # 造一份 blocked 报告（同一 snapshot/generation/epoch），模拟前一张单验出问题。
    conn.execute(
        """
        INSERT INTO validation_report (
            id, project_id, chapter_id, chapter_number, chapter_snapshot_id,
            source_generation, ruleset_epoch, ruleset_hash, phase, gate, issues_json
        ) VALUES ('report:blocked', ?, ?, 1, ?, 1, 1, 'x', 'initial', 'blocked', '[]')
        """,
        (pid, chapter_id, snapshot_id),
    )
    attempt_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        mask=BRANCH_SUMMARY,
    )
    conn.commit()
    token = claim_attempt(conn, attempt_id, owner="w1")
    assert token is not None
    summary = Stub()
    outcome = _coordinator(conn).run(
        attempt_id,
        owner="w1",
        token=token,
        summary_adapter=summary,
        extraction_adapter=Stub(),
    )
    assert outcome["validation"] == "blocked"
    assert summary.calls == [], "已判定阻断的正文被拿去总结了 —— 闸门被绕过去了"


def test_coverage_is_idempotent_and_another_mask_is_another_order(
    conn: Connection,
    tmp_path: Path,
) -> None:
    """同 hash 保存：只为缺失分支建 coverage attempt，第二次复用同一 attempt；
    FAILED → 再下一张，**最多 `MAX_AUTO_RETRIES` 张**，够数才 attention_required（2026-09-14）。"""
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = next(
        ct.snapshot_id for ct in SqliteStoryGraph(conn).current_snapshots(pid) if ct.number == 1
    )

    def missing_summary_and_extraction(c: Connection, p: str, ch: str, run: str) -> int:
        return BRANCH_SUMMARY | BRANCH_EXTRACTION

    first = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=missing_summary_and_extraction,
    )
    assert first.processing == "queued"
    assert first.missing_branch_mask == BRANCH_SUMMARY | BRANCH_EXTRACTION
    assert first.attempt_id is not None

    second = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=missing_summary_and_extraction,
    )
    assert second.attempt_id == first.attempt_id
    assert second.processing == "queued"

    # 覆盖完整 → reused，不建 attempt。
    full = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
        missing_check=lambda c, p, ch, run: 0,
    )
    assert full.processing == "reused" and full.reused is True

    # 同 basis FAILED 了 → **再下一张**（2026-09-14 之前是永远 attention_required：真书上
    # 思考吃空预算那 31 章修好之后一张单都不会再下）。新的一行、新的 trigger_key，
    # 旧的原样留着；最多再下 MAX_AUTO_RETRIES 张，之后才是 attention_required。
    def fail(attempt_id: str) -> None:
        # 像真的跑完了那样：验证过了、抽取过了、总结那一支炸了。**三支都终态**——
        # 还有一支 PENDING/RUNNING 的单是「还在跑」，问它只会幂等复用，不会再下一张。
        conn.execute(
            "UPDATE chapter_refresh_attempt SET validation_state = 'SUCCEEDED', "
            "extraction_state = 'SUCCEEDED', summary_state = 'FAILED' WHERE id = ?",
            (attempt_id,),
        )
        conn.commit()

    def order() -> CoverageDecision:
        return ensure_refresh_coverage(
            conn,
            project_id=pid,
            chapter_id=chapter_id,
            snapshot_id=snapshot_id,
            generation=1,
            ruleset_epoch=1,
            ruleset_hash="x",
            missing_check=missing_summary_and_extraction,
        )

    fail(first.attempt_id)
    retries: list[str] = []
    for n in range(1, MAX_AUTO_RETRIES + 1):
        retry = order()
        assert retry.processing == "queued" and retry.reused is False, n
        assert retry.attempt_id is not None and retry.attempt_id != first.attempt_id
        assert retry.attempt_id not in retries
        retries.append(retry.attempt_id)
        # 还没跑完的那张再问一次是复用（幂等），不是又一张。
        again = order()
        assert again.attempt_id == retry.attempt_id and again.reused is True
        fail(retry.attempt_id)
    assert len(retries) == MAX_AUTO_RETRIES
    # 够数了：不再付。
    exhausted = order()
    assert exhausted.processing == "attention_required"
    assert exhausted.attempt_id == retries[-1]
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT trigger_key FROM chapter_refresh_attempt WHERE trigger_kind = 'coverage' "
            "AND missing_branch_mask = ? ORDER BY created_at, id",
            (BRANCH_SUMMARY | BRANCH_EXTRACTION,),
        )
    ]
    assert keys == ["coverage:6", "coverage:6#1", "coverage:6#2", "coverage:6#3"]

    # BLOCKED（规则拦下的）**不重试**：正文和规则集不变，再跑一遍还是拦。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET summary_state = 'SUCCEEDED', "
        "validation_state = 'BLOCKED' WHERE id = ?",
        (retries[-1],),
    )
    conn.commit()
    assert order().processing == "attention_required"

    # **另一个 mask 是另一张单**：coverage 的唯一键是 (run, epoch, kind, mask)，
    # 所以「缺的东西不一样」建得出第二张单，而「同一批缺口」建不出。
    # （从前这儿钉的是「显式重新整理 → 新 manual intent」，那条路 2026-08-25
    # 随手动入口一起删了；剩下的这条不变式是那几个「手里先有两张单」的测试
    # 今天还能建场的原因。）
    other_mask = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    assert other_mask != first.attempt_id


def test_extraction_application_head_cas_keeps_only_the_latest_intent_current(
    conn: Connection, tmp_path: Path
) -> None:
    """021 / Task 9：同一 generation 的多个合法 attempt 竞争同一个 application head。

    各自持有合法 fencing token，但只有 `intent_seq` 最新者能成为 CURRENT；
    DB 的 partial unique（每个 refresh run 至多一条 CURRENT）再兜一道底——
    两个 attempt 不能各自建 head 绕过竞争（不变量 22）。
    """
    pid, chapter_id = _seed_chapter(conn, tmp_path)
    snapshot_id = _snapshot_id(conn, pid)
    run_id = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
    )
    # `an_attempt` 返回的是 attempt id，不是 run id —— 取 run。
    row = conn.execute(
        "SELECT run_id FROM chapter_refresh_attempt WHERE id = ?", (run_id,)
    ).fetchone()
    refresh_run_id = row["run_id"]

    # analysis_run_id 是真实存在的 extraction_run（021 的 FK 不许悬空引用）。
    def seed_run(run_id: str) -> None:
        # prompt_hash 不同：同一 generation 的 save/manual/ruleset attempt 可以
        # 有不同的 content-addressed run，但不能各自建 head 绕过竞争（不变量 22）。
        conn.execute(
            "INSERT INTO extraction_run (id, project_id, chapter_number, snapshot_id, "
            "status, errors_json, schema_version, prompt_hash, source_generation, "
            "required_ruleset_epoch, required_ruleset_hash) "
            "VALUES (?, ?, 1, ?, 'SUCCEEDED', '[]', 'm4.analysis.v1', ?, 1, 1, 'x')",
            (run_id, pid, snapshot_id, run_id + "-prompt"),
        )

    seed_run("run:analysis-a")
    app_a, status_a = activate_extraction_application(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        analysis_run_id="run:analysis-a",
        ruleset_epoch=1,
        ruleset_hash="x",
    )
    assert status_a == "CURRENT"
    assert conn.execute(
        "SELECT intent_seq FROM extraction_application_head WHERE refresh_run_id = ?",
        (refresh_run_id,),
    ).fetchone()["intent_seq"] == 1

    # 第二次 intent 前进 → 它取代 a 成为 CURRENT（+1），a 退休留审计。
    seed_run("run:analysis-b")
    app_b, status_b = activate_extraction_application(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=1,
        analysis_run_id="run:analysis-b",
        ruleset_epoch=1,
        ruleset_hash="x",
    )
    assert status_b == "CURRENT"
    # partial unique：整个 refresh run 只有一条 CURRENT —— 是最新的 b。
    current = conn.execute(
        "SELECT id FROM extraction_application WHERE refresh_run_id = ? AND status = 'CURRENT'",
        (refresh_run_id,),
    ).fetchone()
    assert current["id"] == app_b
    # 旧的 a：SUPERSEDED（审计保留，不删）。
    a_row = conn.execute(
        "SELECT status FROM extraction_application WHERE id = ?", (app_a,)
    ).fetchone()
    assert a_row["status"] == "SUPERSEDED"
    # head intent 单调前进，且只指向最新那条。
    head = conn.execute(
        "SELECT intent_seq, current_application_id FROM extraction_application_head "
        "WHERE refresh_run_id = ?",
        (refresh_run_id,),
    ).fetchone()
    assert head["intent_seq"] == 2
    assert head["current_application_id"] == app_b


def test_a_retracted_summary_is_not_bought_back_by_the_next_save(
    conn: Connection, tmp_path: Path
) -> None:
    """作者撤回一章的总结之后，**自动派活不许再替他买一份回来**。

    这条是钱和信任两件事：他按了「撤回」，下一次保存就冒出来一份新的，等于
    **花了他没按过的钱去抹掉他刚做的动作**。ARCHITECTURE 把这条写死过
    （「`get()` / `latest()` 的差别是钱……用 `get()` 的话作者撤掉的那一章会在他保存后
    下一秒被自动买回来」），而 2026-08-20 合并保存闭环任务时实测发现代码和那句话相反。

    **它原本只由 `api/autopilot.py` 实现着**（拿 `latest()` + `retracted` 标志跳过），
    那个模块随换章 autopilot 一起删掉之后，这半条纪律就没有任何东西守着了——
    所以补这条测试，而不是只改那一行 SQL。

    界面那一侧不受影响：`SummaryStore.coverage()` 照旧把撤回过的章显示成「没有总结」
    （他要看得见自己撤了），想要新的一份他自己点「重新生成」（走 manual intent）。
    """
    from novel_harness.draft.rolling_summary import retract_summary, save_author_summary

    pid, chapter_id = _seed_chapter(conn, tmp_path)

    # ① 从没总结过 → 该自动补。
    assert _head_missing(conn, pid, chapter_id) is True

    # ② 有一份生效的总结 → 不缺。
    save_author_summary(conn, project_id=pid, chapter_number=1, text="作者亲手写的摘要")
    conn.commit()
    assert _head_missing(conn, pid, chapter_id) is False

    # ③ 作者撤回它 → **仍然不算缺**（关键的那一档）。
    retract_summary(conn, project_id=pid, chapter_number=1)
    conn.commit()
    assert _head_missing(conn, pid, chapter_id) is False, (
        "撤回过的章被判成「缺总结」——下一次保存就会自动付费买回来，"
        "把作者刚做的动作抹掉。判据只能问「有没有 head」，不能问「head 是不是 ACTIVE」。"
    )


def _rewrite_chapter_one(conn: Connection, pid: str, text: str) -> tuple[str, int]:
    """把第 1 章的正文换成 `text`（走产品保存那条路），返回新的 `(快照, generation)`。"""
    store = SqliteStoryGraph(conn)
    token = store.commit_chapter_snapshot(
        ChapterSpec(
            project_id=pid,
            number=1,
            heading="第一章 甲",
            path=importer.chapter_path(1),
            text=text,
        ),
        expected_text_sha256=store.current_chapter_hash(pid, 1),
    )
    conn.commit()
    return token.source_snapshot_id, token.source_generation


def test_the_coverage_order_carries_the_summary_branch_when_the_text_moved_on(
    conn: Connection, tmp_path: Path
) -> None:
    """正文改过、旧总结还挂着 → coverage 单里**必须**带总结那一支（2026-08-22 任务 B）。

    这里是那个 bug 的出生地：`missing_branch_mask` 只答得了「缺不缺」，而一份挂着的
    旧总结在那份清单里永远答「不缺」。于是上游扫描器判 `stale`、报「已排覆写」，
    真正下的单里一项总结都没有。

    **这条红了代表「缺不缺」和「旧不旧」又被并回了同一个问题**，症状是覆写永远不
    发生：作者在别的软件里改完一章，系统嘴上说排了，这一章的总结却永远停在旧版，
    写下一章时被喂进模型的仍然是过时的剧情。
    """
    from novel_harness.draft.rolling_summary import save_author_summary

    pid, chapter_id = _seed_chapter(conn, tmp_path)
    store = SqliteStoryGraph(conn)
    before = next(ct for ct in store.current_snapshots(pid) if ct.number == 1)
    save_author_summary(conn, project_id=pid, chapter_number=1, text="照着这一版正文写的摘要")
    conn.commit()

    paired = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=before.snapshot_id,
        generation=1,
        ruleset_epoch=1,
        ruleset_hash="x",
    )
    assert not paired.missing_branch_mask & BRANCH_SUMMARY, (
        "总结照的就是当前正文 = 既不缺也不旧，不该再买一份"
    )

    snapshot_id, generation = _rewrite_chapter_one(
        conn, pid, "第一章 甲\n\n萧决改了主意，这一段和原来完全不同。\n"
    )
    stale = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=generation,
        ruleset_epoch=1,
        ruleset_hash="x",
    )
    assert stale.missing_branch_mask & BRANCH_SUMMARY, (
        "正文换了版本、总结停在旧版，单里却没有总结那一项 —— 这正是「报了覆写、"
        f"实际一单没下」的形状（mask={stale.missing_branch_mask:b}）"
    )


def test_a_retracted_chapter_is_not_bought_back_after_the_text_changes(
    conn: Connection, tmp_path: Path
) -> None:
    """撤回过的章，**正文后来又改了也不许自动买回来**（2026-08-21 那个坑的第二种走法）。

    「旧不旧」这一问加进来之后，撤回的 tombstone 一旦被当成「一份照旧正文写的总结」，
    作者只要在撤回之后再动一次正文，系统就替他买回来一份——正是他刚按掉的东西，
    而且这次连保存都不必：30 分钟扫描自己就会去买。

    这条红了代表「旧不旧」那一问漏掉了「只看 ACTIVE 那一份」的那一半。
    """
    from novel_harness.draft.rolling_summary import retract_summary, save_author_summary

    pid, chapter_id = _seed_chapter(conn, tmp_path)
    save_author_summary(conn, project_id=pid, chapter_number=1, text="他后来不想要的那份摘要")
    retract_summary(conn, project_id=pid, chapter_number=1)
    conn.commit()

    snapshot_id, generation = _rewrite_chapter_one(
        conn, pid, "第一章 甲\n\n撤回之后他又把这一章重写了一遍。\n"
    )
    after = ensure_refresh_coverage(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=snapshot_id,
        generation=generation,
        ruleset_epoch=1,
        ruleset_hash="x",
    )
    assert not after.missing_branch_mask & BRANCH_SUMMARY, (
        "撤回过的章在正文改动后被排了总结分支 —— 花作者没按过的钱，"
        f"抹掉他刚做的动作（mask={after.missing_branch_mask:b}）"
    )
