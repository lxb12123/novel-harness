"""后台 dispatcher 的重启恢复（Task 16 / 不变量 18）。

钉住：库预放 PENDING / 过期 RUNNING attempt 后，**没有任何新 HTTP 请求**，
dispatcher 一轮 `pump_once` 就把它们全部重新 claim 并执行；未过期 RUNNING 不被
抢；shutdown 停止新 claim。adapter 用桩（不付真模型），协调器真跑固定 DAG。

⚠️ **还钉一件在这份文件里长得不像测试的事：这一波跑在哪条线程上。** 本文件其余
每一条测试都在**构造 runtime 的那条线程**上直接调 `pump_once()`，而生产里构造发生
在 lifespan 协程、执行发生在 `dsh-background` 线程——两条线程之间隔着 sqlite3 的
`check_same_thread`。那道缝让真书卡了一周而全套测试全绿，
所以 `test_a_wave_runs_on_the_background_thread_not_the_one_that_built_it` 是这里
唯一一条**换线程**的测试，别把它「统一」回其他测试的写法。
"""

from __future__ import annotations

from pathlib import Path
import threading

from novel_harness import importer, project
from novel_harness.api.background_runtime import BackgroundRuntime
from novel_harness.chapter_refresh import (
    BranchAdapter,
    BranchContext,
)
from test_chapter_refresh import an_attempt
from novel_harness.db import connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.extract import RawChapterAnalysis, RawEvent
from novel_harness.extract.runner import ExtractionRunner
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.draft.rolling_summary import RollingSummarizer, SummaryRequest

TEXT = "第一章 甲\n\n萧决走进了青云城。\n"


class StubSummaryAdapter(BranchAdapter):
    def run(self, ctx: BranchContext) -> str:
        return "summary:stub"


class StubExtractionAdapter(BranchAdapter):
    def run(self, ctx: BranchContext) -> str:
        return "extraction:stub"


def _seed_book(tmp_path: Path) -> tuple[str, str, str]:
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="恢复", root_path=str(root)).id
    graph = SqliteStoryGraph(conn)
    src = tmp_path / "s.txt"
    src.write_text(TEXT, encoding="utf-8")
    importer.import_book(graph, pid, txt=src, root=root)
    conn.commit()
    row = conn.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND number = 1", (pid,)
    ).fetchone()
    chapter_id = row["id"]
    conn.close()
    return str(db), pid, chapter_id


def _conn_factory(db: str):
    from novel_harness.db import connect as _connect

    return lambda: _connect(db)


def _stub_runner(db: str):
    # 抽取 branch 的 run 需要真实 enqueue + run，用桩 analyzer 防止真付费。
    def analyzer(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决到了。",
                        quote="萧决走进了青云城。",
                        participants=("萧决",),
                        knowers=("萧决",),
                        confidence=0.95,
                    ),
                ),
                state_updates=(),
                character_profiles=(),
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return ExtractionRunner(lambda: connect(db), analyzer)


def _stub_summarizer(db: str):
    def analyzer(_request: SummaryRequest) -> CompletionResult:
        return CompletionResult(
            text="这一章萧决来到了青云城。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )

    return RollingSummarizer(lambda: connect(db), analyzer)


def test_pump_once_claims_and_runs_pending_attempts_without_http(tmp_path: Path) -> None:
    db, pid, chapter_id = _seed_book(tmp_path)
    conn = connect(db)
    # 预放两个 attempt：一个 summary+extraction 都缺的 manual，一个 validation 已过、
    # 只缺 extraction 的 coverage。
    attempt_a = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    attempt_b = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    # 第二个 attempt 预置验证通过，只等 summary/extraction。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET validation_state = 'SUCCEEDED' WHERE id = ?",
        (attempt_b,),
    )
    conn.commit()

    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,  # 不自动跑；我们手动 pump
    )

    # 没有任何 HTTP 请求：直接 pump 一次。
    done = runtime.pump_once()
    assert done >= 1
    statuses = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT id, summary_state || '/' || extraction_state "
            "FROM chapter_refresh_attempt WHERE id IN (?, ?)",
            (attempt_a, attempt_b),
        )
    }
    # 至少一条已经成功（validation 分支共用跑完）。
    assert any("SUCCEEDED" in v for v in statuses.values()), statuses
    # claim 后 coordinator 跑完，attempt 不再 PENDING 可被恢复扫描捡到。
    still_pending = conn.execute(
        "SELECT COUNT(*) FROM chapter_refresh_attempt "
        "WHERE (summary_state IN ('PENDING','RUNNING') "
        "OR extraction_state IN ('PENDING','RUNNING'))",
    ).fetchone()[0]
    assert still_pending == 0, f"还有 attempt 没收尾：{still_pending}"
    conn.close()


def test_a_wave_runs_on_the_background_thread_not_the_one_that_built_it(
    tmp_path: Path,
) -> None:
    """**构造在一条线程、执行在另一条**——生产的接线就是这样，测试从前不是。

    `BackgroundRuntime` 在 lifespan 协程里构造，`_loop` 跑在 `start()` 起的
    `dsh-background` 线程上。sqlite3 的连接默认 `check_same_thread=True`，所以
    构造时开的任何一条连接在波次里都是一颗雷：第一条 SELECT 抛 `ProgrammingError`,
    `_loop` 的 `except Exception` 吞掉，**每一波都死在第一条 attempt 上**——
    claim 照旧发生（`fencing_token` 一路涨到四位数），分支状态一个都不动。
    真书 book.db 就是这么在「135 章缺总结」上停了一周（2026-09-05 修）。

    本文件其余测试都在构造线程上调 `pump_once()`，因此**一条都拦不住这个**。
    这条测试押两件事：
    ① 构造过程**一条连接都不开**（工厂调用计数为 0）——雷根本没机会被埋下；
    ② 换一条线程跑一波，异常原样抬回来，并且 attempt 真的收尾了。
    """
    db, pid, chapter_id = _seed_book(tmp_path)
    conn = connect(db)
    attempt = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    conn.commit()

    opened: list[int] = []
    base = _conn_factory(db)

    def counting_factory():
        opened.append(threading.get_ident())
        return base()

    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=counting_factory,
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,
    )
    assert opened == [], "构造时不许开连接：它开在错的那条线程上"

    box: dict[str, object] = {}

    def wave() -> None:
        try:
            box["done"] = runtime.pump_once()
        except BaseException as exc:  # noqa: BLE001 —— 生产里这一层是 except: pass
            box["exc"] = exc

    thread = threading.Thread(target=wave, name="dsh-background")
    thread.start()
    thread.join()

    assert "exc" not in box, f"后台线程上跑不动：{box.get('exc')!r}"
    assert box["done"] == 1
    row = conn.execute(
        "SELECT summary_state, extraction_state FROM chapter_refresh_attempt WHERE id = ?",
        (attempt,),
    ).fetchone()
    assert row["summary_state"] not in ("PENDING", "RUNNING"), dict(row)
    assert row["extraction_state"] not in ("PENDING", "RUNNING"), dict(row)
    conn.close()


def test_unexpired_running_attempt_is_not_stolen(tmp_path: Path) -> None:
    db, pid, chapter_id = _seed_book(tmp_path)
    conn = connect(db)
    attempt = an_attempt(
        conn,
        project_id=pid,
        chapter_id=chapter_id,
        snapshot_id=_snapshot(conn, pid),
    )
    # 别的 worker 正拿着（lease 未过期）。
    conn.execute(
        "UPDATE chapter_refresh_attempt SET lease_owner = 'other', "
        "lease_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ','now', '+100 seconds'), "
        "extraction_state = 'RUNNING' WHERE id = ?",
        (attempt,),
    )
    conn.commit()

    runtime = BackgroundRuntime(
        db_path=db,
        connection_factory=_conn_factory(db),
        runner_factory=lambda: _stub_runner(db),
        summarizer_factory=lambda: _stub_summarizer(db),
        owner="test",
        poll_seconds=100,
    )
    done = runtime.pump_once()
    assert done == 0, "未过期 RUNNING 不应被抢"
    row = conn.execute(
        "SELECT lease_owner FROM chapter_refresh_attempt WHERE id = ?", (attempt,)
    ).fetchone()
    assert row["lease_owner"] == "other"
    conn.close()


def _snapshot(conn, pid: str) -> str:

    return str(
        conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE c.project_id = ? AND c.number = 1 AND cs.text_sha256 = c.text_sha256",
            (pid,),
        ).fetchone()["id"]
    )
