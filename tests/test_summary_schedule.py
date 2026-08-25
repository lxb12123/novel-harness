"""全书总结自治调度（2026-08-18 文档 §2.2/§4/Step 2 + 2026-08-22 去阀门/拆覆写）。

钉住：四态判定（paired/missing/stale/retracted）全确定性查库；**要干活的章全部
进候选池**（权重只分配一轮的名额，不当准入门槛），唯一的例外是焦点章；「报了
已排覆写」必须真的排了总结那一支、**且那张单跑完之后库里真的换了一份**；整轮调度
不因单章失败而阻塞；「导入一本生成一半的书」→ 不点保存 → 等一轮调度 → 缺章被补上
（写进持久 attempt 表）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import seed

from novel_harness import importer, project
from novel_harness.api.app import _current_ruleset
from novel_harness.chapter_refresh import BRANCH_SUMMARY
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.summary_schedule import (
    QUEUED_OUTCOMES,
    scan_chapter_summary_state,
    schedule_alignment,
    weight_for_chapter,
)


def _wheel(tmp_path: Path, chapters: int) -> dict[str, str]:
    """建一本 `chapters` 章的书（每章正文）。
    第 1 章从磁盘进库；后续章靠 `importer.sync` 一起进。
    """
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="调度", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    Ledger(store, conn, pid).declare_node(NodeLabel.CHARACTER, "萧决")
    src = tmp_path / "s.txt"
    body = "\n\n".join(f"第{n}章 甲{n}\n\n萧决在第 {n} 章做了些事。\n" for n in range(1, chapters + 1))
    src.write_text(body, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


def _conn_and_ruleset(wheel: dict[str, str]):
    conn = connect(wheel["db"])
    epoch, ruhash = _current_ruleset(conn, wheel["pid"])
    return conn, epoch, ruhash


def _seed_summary(wheel: dict[str, str], chapter: int, *, stale: bool) -> None:
    """给第 `chapter` 章一条 ACTIVE 总结并切 head。

    `stale=True` 时先往库里塞一个「旧版」正文快照（text 与当前不同、text_sha256
    也不同）再把总结的 `source_snapshot_id` 指过去 → 指纹对不上 = 该覆写；
    `stale=False` 时指向当前快照 = 配对。
    """
    conn = connect(wheel["db"])
    ch = conn.execute(
        "SELECT id FROM chapter WHERE project_id=? AND number=?", (wheel["pid"], chapter)
    ).fetchone()["id"]
    from novel_harness import importer
    from novel_harness.ids import EntityType, new_id

    if stale:
        old_text = f"第 {chapter} 章的旧版本旧版本\n"
        source_snapshot = new_id(EntityType.SNAPSHOT, wheel["pid"])
        conn.execute(
            """
            INSERT INTO chapter_snapshot (id, chapter_id, text, text_sha256)
            VALUES (?, ?, ?, ?)
            """,
            (source_snapshot, ch, old_text, importer.text_digest(old_text)),
        )
    else:
        source_snapshot = conn.execute(
            "SELECT cs.id FROM chapter_snapshot cs "
            "JOIN chapter c ON c.id = cs.chapter_id "
            "WHERE cs.chapter_id = ? AND cs.text_sha256 = c.text_sha256 LIMIT 1",
            (ch,),
        ).fetchone()["id"]
    sid = new_id(EntityType.SUMMARY, wheel["pid"])
    conn.execute(
        """
        INSERT INTO chapter_summary (
            id, project_id, chapter_id, chapter_number, summary, summary_sha256,
            source_snapshot_id, replaces_summary_id, schema_version, prompt_hash,
            model_call_id, created_at, source, status
        ) VALUES (?, ?, ?, ?, '旧总结', nh_sha256_text('旧总结'), ?, NULL,
                  'chapter-summary-v1', ?, NULL,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'model', 'ACTIVE')
        """,
        (sid, wheel["pid"], ch, chapter, source_snapshot, f"prompt:{sid}"),
    )
    # 018 给每章都建了空的 head 行；这里把现有的空 head 指向新总结。
    conn.execute(
        "UPDATE chapter_summary_head SET current_summary_id = ? WHERE chapter_id = ?",
        (sid, ch),
    )
    conn.commit()
    conn.close()


def _attempt_masks(wheel: dict[str, str]) -> dict[int, int]:
    """每一章现有 coverage attempt 的 `missing_branch_mask` 的并集（章号 → mask）。"""
    conn = connect(wheel["db"])
    try:
        rows = conn.execute(
            """
            SELECT c.number AS number, a.missing_branch_mask AS mask
              FROM chapter_refresh_attempt a
              JOIN chapter_refresh_run r ON r.id = a.run_id
              JOIN chapter c ON c.id = r.chapter_id
             WHERE c.project_id = ?
            """,
            (wheel["pid"],),
        ).fetchall()
    finally:
        conn.close()
    masks: dict[int, int] = {}
    for row in rows:
        masks[int(row["number"])] = masks.get(int(row["number"]), 0) | int(row["mask"])
    return masks


def test_weight_peaks_in_window_and_decays_outside() -> None:
    assert weight_for_chapter(draft_chapter=12, chapter_number=11) == 1.0
    assert weight_for_chapter(draft_chapter=12, chapter_number=2) == 1.0  # Δ=10 == 窗口
    assert weight_for_chapter(draft_chapter=12, chapter_number=1) < 1.0
    assert weight_for_chapter(draft_chapter=12, chapter_number=1) > 0.0
    # 归零点 = WINDOW + CAP = 往回 60 章（模块文档说 50、代码说 60 的那处不一致，
    # 2026-08-22 裁定按代码的 60 统一；这两条断言就是那份文档的守卫）。
    assert weight_for_chapter(draft_chapter=60, chapter_number=1) > 0.0  # Δ=59
    assert weight_for_chapter(draft_chapter=61, chapter_number=1) == 0.0  # Δ=60
    # 正在写的这章 / 未来章排队尾（0.0）。**0.0 不再等于「不调度」**，
    # 「正写的章不碰」只由 focused_chapter 一条口子表达（见下面那条测试）。
    assert weight_for_chapter(draft_chapter=12, chapter_number=12) == 0.0
    assert weight_for_chapter(draft_chapter=1, chapter_number=2) == 0.0


def test_scan_classifies_missing_paired_and_stale_without_llm(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, 3)
    conn, _, _ = _conn_and_ruleset(wheel)
    try:
        states = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        by_chapter = {s.chapter_number: s for s in states}
        assert len(by_chapter) == 3
        # 新书没总结 → 全 missing；1/2 在近窗口内权重 1.0。
        assert by_chapter[1].state == "missing" and by_chapter[1].weight == 1.0
        assert by_chapter[2].state == "missing" and by_chapter[2].weight == 1.0
        # 第 3 章是「正在写」的那章 —— 权重 0（排队尾），但**照样在池子里要干活**。
        assert by_chapter[3].weight == 0.0
        assert by_chapter[3].needs_work is True
    finally:
        conn.close()

    # 造一条 stale（source_sha 故意不等于当前正文）。
    _seed_summary(wheel, 1, stale=True)
    conn = connect(wheel["db"])
    try:
        states = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        by_chapter = {s.chapter_number: s for s in states}
        assert by_chapter[1].state == "stale", "正文变过 = 该覆写（不是 paired）"
        assert by_chapter[1].needs_work is True
    finally:
        conn.close()

    # 配对的章（总结照的就是当前正文）总结那一维不用干活。
    _seed_summary(wheel, 2, stale=False)
    conn = connect(wheel["db"])
    try:
        by_chapter = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        }
        assert by_chapter[2].state == "paired"
        # ⚠️ **`paired` 不等于 `needs_work is False`**（2026-08-25）：抽取那一维还空着。
        # 两个维度分两格答，`needs_work` 才是合起来的答案。
        assert by_chapter[2].extraction == "missing"
        assert by_chapter[2].needs_work is True
    finally:
        conn.close()

    # 两维都齐了才算这一章没活干。
    seed.applied_extraction(wheel["db"], wheel["pid"], 2)
    conn = connect(wheel["db"])
    try:
        by_chapter = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        }
        assert by_chapter[2].extraction == "applied"
        assert by_chapter[2].needs_work is False
    finally:
        conn.close()


def test_a_retracted_summary_is_not_a_gap_for_the_scanner(tmp_path: Path) -> None:
    """作者撤回过的总结**不算缺** —— 否则他删一次，系统买回来一次（2026-08-21 踩过）。

    保存那条路由 `chapter_refresh._head_missing` 钉着；这里钉的是**扫描这条路**：
    去掉权重阀门之后全书每一章都会被扫到，撤回过的章要是被判成 `missing`，
    作者每撤一次，下一轮自治就替他买回来一次。
    """
    wheel = _wheel(tmp_path, 2)
    _seed_summary(wheel, 1, stale=False)

    from novel_harness.draft.rolling_summary import retract_summary

    conn = connect(wheel["db"])
    try:
        retract_summary(conn, project_id=wheel["pid"], chapter_number=1)
    finally:
        conn.close()

    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        by_chapter = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        }
        assert by_chapter[1].state == "retracted", "撤回过 ≠ 缺"
        # 「撤回不算缺」**只管总结那一维**：这一章的抽取跑过了，所以它今天真的没活干。
        # 不给它造 applied 的话它会因为抽取那一维被排上——那时下面 `decisions[1]` 会是
        # `queued_extraction`，而这条测试问的是「总结会不会被买回来」，两码事。
        seed.applied_extraction(wheel["db"], wheel["pid"], 1)
        by_chapter = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        }
        assert by_chapter[1].state == "retracted" and by_chapter[1].needs_work is False
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=3,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        assert decisions[1] == "retracted"
    finally:
        conn.close()
    # 一条总结分支的活都不许排到这一章头上。
    assert not _attempt_masks(wheel).get(1, 0) & BRANCH_SUMMARY, (
        "撤回过的章被排了总结分支 = 花作者没按过的钱抹掉他刚做的动作"
    )


def test_distance_never_removes_a_chapter_from_the_candidate_pool(
    tmp_path: Path,
) -> None:
    """任务 A 的守卫：**候选池不会因为离作者远而少人**（权重只分名额）。

    两种「远」都盖上：往回远超归零点（Δ ≥ 60 → 权重 0.000），以及作者跳回前面的
    章之后所有后续章都变成「未来章」（Δ ≤ 0 → 权重 0.000）。从前这两种都直接
    退出调度 —— 一本 158 章的书，作者看一眼第 2 章就有 156 章永远补不上。
    """
    wheel = _wheel(tmp_path, 5)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        # ① 往回远超归零点：全部权重 0，但一个都不许掉队，且**离作者近的排前面**。
        far = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=70)
        assert all(s.weight == 0.0 for s in far), "Δ≥60 全部归零（这正是从前的阀门）"
        assert all(s.needs_work for s in far)
        assert [s.chapter_number for s in far] == [5, 4, 3, 2, 1], (
            f"同权重要按离作者近的先补，实得 {[s.chapter_number for s in far]}"
        )
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=70,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        assert {n for n, d in decisions.items() if d in QUEUED_OUTCOMES} == {1, 2, 3, 4, 5}
    finally:
        conn.close()

    # ② 作者跳回第 2 章：3/4/5 成了「未来章」，权重 0，但仍在池子里，只排队尾。
    wheel2 = _wheel(tmp_path / "b2", 5)
    conn, epoch, ruhash = _conn_and_ruleset(wheel2)
    try:
        states = scan_chapter_summary_state(conn, wheel2["pid"], draft_chapter=2)
        by_chapter = {s.chapter_number: s for s in states}
        assert by_chapter[1].weight == 1.0
        assert all(by_chapter[n].weight == 0.0 for n in (3, 4, 5))
        # 队列全貌：权重 1.0 的第 1 章打头，其余按离原点的远近排队尾。第 2 章
        # （作者正开着的那一章）在**扫描层照样在队里**——豁免只发生在调度层，
        # 这样「不碰焦点章」就只有 `focused_chapter` 一处判据，不会又长出第二处。
        assert [s.chapter_number for s in states] == [1, 2, 3, 4, 5], (
            "权重 0 的那几章该排在队尾（离焦点近的先），而不是退出队列，"
            f"实得 {[s.chapter_number for s in states]}"
        )
        decisions = schedule_alignment(
            conn,
            wheel2["pid"],
            draft_chapter=2,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=2,
            limit=20,
        )
        assert decisions[2] == "focused", "唯一的例外：作者当前打开的那一章不动"
        assert all(decisions[n] == "queued" for n in (1, 3, 4, 5)), (
            f"离作者远只该排队尾，不该退出调度，实得 {decisions}"
        )
    finally:
        conn.close()


def _stub_runtime(db: str, *, limit: int):
    """真协调器 + 桩 adapter：跑得完整条 DAG，但不真调模型。

    ── ⚠️ 2026-08-25：这个桩的抽取那一半**从来没有工作过** ────────────────────

    它原来构造的是 `RawChapterAnalysis(events=(), ...)`，而那个模型的 `events` 是
    `min_length=1`——**构造时就抛**，而那句构造正好在 runner 的 `try` 里面，
    于是每一章的抽取都以 `provider_failure` 收场，`extraction_run` 全是 FAILED。

    上面那句「跑得完整条 DAG」因此是假的，**而没有任何东西会红**：
    在这一天之前调度器根本不问抽取那一维，所以没有一条断言看得见它。
    判据从「总结齐没齐」扩成「该做的都做了没有」的第一刻，它就自己露出来了
    （全书永远收敛不了，因为抽取永远缺）。

    修法是给它一条真的事件（同 `test_autonomy_runtime` 那个桩的形状）。
    """
    from novel_harness.api.background_runtime import BackgroundRuntime
    from novel_harness.draft.provider import CompletionResult
    from novel_harness.draft.rolling_summary import RollingSummarizer
    from novel_harness.extract import RawChapterAnalysis, RawEvent
    from novel_harness.extract.runner import ExtractionRunner

    def conn_factory():
        return connect(db)

    def extraction(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(
                    RawEvent(
                        summary="萧决做了些事。",
                        quote="萧决在第 1 章做了些事。",
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
            prompt_tokens=1,
            completion_tokens=1,
        )

    def summarize(_request):
        return CompletionResult(
            text="这一章萧决做了些事。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    return BackgroundRuntime(
        db_path=db,
        connection_factory=conn_factory,
        runner_factory=lambda: ExtractionRunner(conn_factory, extraction),
        summarizer_factory=lambda: RollingSummarizer(conn_factory, summarize),
        owner="test",
        poll_seconds=100,
        autonomy_seconds=3600.0,
        autonomy_limit=limit,
    )


def test_the_pool_drains_across_rounds_until_every_chapter_has_a_summary(
    tmp_path: Path,
) -> None:
    """任务 A 的验收：一本全书没总结的书接进来，**最终每一章都有总结**。

    一轮的名额（这里故意设成 2）小于池子，所以要靠「取不完下一轮接着取」才补得完。
    从前这件事在长书上做不到：权重归零的章根本不进池子，扫多少轮都不会被补。
    顺带钉住**先后**：第一轮的两个名额给离作者最近的两章（权重的唯一用途）。
    最后一轮必须一单都不下（幂等收敛，不反复花钱）。
    """
    wheel = _wheel(tmp_path, 5)
    runtime = _stub_runtime(wheel["db"], limit=2)

    # 第一轮：没有焦点 → 原点是前沿章 + 1 = 6，名额该落在离它最近的 5 / 4 上。
    assert runtime.autonomy_once() == 2
    assert set(_attempt_masks(wheel)) == {4, 5}, (
        f"名额没给离作者最近的两章，实得 {sorted(_attempt_masks(wheel))}"
    )
    runtime.pump_once()

    rounds = 0
    while rounds < 10:
        rounds += 1
        if runtime.autonomy_once() == 0:
            break
        runtime.pump_once()
    assert rounds < 10, "没在有限轮内收敛"

    conn = connect(wheel["db"])
    try:
        states = scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=6)
        assert {s.chapter_number: s.state for s in states} == {
            n: "paired" for n in (1, 2, 3, 4, 5)
        }, "补完之后每一章都该配对"
    finally:
        conn.close()
    assert runtime.autonomy_once() == 0, "补完再扫不许产生新单"


def test_reporting_an_overwrite_means_the_order_carries_the_summary_branch(
    tmp_path: Path,
) -> None:
    """任务 B 的守卫：**说排了覆写 = 下的单里真的带着总结那一项**。

    2026-08-22 实测的 bug 就是这两者对不上：扫描器判 `stale`、返回
    `queued_overwrite`，而真正下单那一步问的是「这一章**有没有**总结」——有啊，
    那份旧的还挂着 —— 于是一单没下。报什么和做什么之间当时没有任何东西对过。
    """
    wheel = _wheel(tmp_path, 3)
    _seed_summary(wheel, 1, stale=True)  # 正文改过、总结还挂在旧快照上
    _seed_summary(wheel, 2, stale=False)  # 配对：这一轮不该碰
    # 三章的抽取都造成「已跑过」：这条测的是**总结那一维**的报-做一致，
    # 不给的话第 2 章会因为抽取那一维被排上，`decisions[2]` 就不是 `paired` 了
    # ——那时它量的是另一件事（2026-08-25 判据扩宽）。
    for chapter in (1, 2, 3):
        seed.applied_extraction(wheel["db"], wheel["pid"], chapter)

    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=4,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        conn.commit()
    finally:
        conn.close()

    assert decisions[1] == "queued_overwrite", f"改过正文的章要排覆写，实得 {decisions}"
    assert decisions[2] == "paired"
    assert decisions[3] == "queued"

    masks = _attempt_masks(wheel)
    for number, outcome in decisions.items():
        if outcome in QUEUED_OUTCOMES:
            assert masks.get(number, 0) & BRANCH_SUMMARY, (
                f"第 {number} 章报了 {outcome}，但下的单里没有总结那一项"
                f"（mask={masks.get(number, 0):b}）—— 报了没做"
            )
    assert 2 not in masks or not masks[2] & BRANCH_SUMMARY, "配对的章不该被排总结"


def test_a_scan_after_the_gaps_are_filled_orders_nothing_new(tmp_path: Path) -> None:
    """幂等收敛：补完之后再扫，一单都不产生（不反复花钱）。

    「补完」= **两维都补完**（2026-08-25）：总结配对 **且** 抽取跑过。
    只造总结那一半的话这条会红，而那正是判据扩宽之后要的行为
    （`test_autonomy_runtime` 里有一条专门验它）。
    """
    wheel = _wheel(tmp_path, 3)
    for chapter in (1, 2, 3):
        _seed_summary(wheel, chapter, stale=False)
        seed.applied_extraction(wheel["db"], wheel["pid"], chapter)

    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=4,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        conn.commit()
    finally:
        conn.close()
    assert set(decisions.values()) == {"paired"}, f"全书配对不该再下单，实得 {decisions}"
    assert _attempt_masks(wheel) == {}, "没有活要干的时候一条 attempt 都不该写"


def test_a_round_never_claims_more_than_the_order_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """就算下单那一层将来又漏了总结分支，调度器也**不许**报 `queued_overwrite`。

    这是上一条守卫的另一半：那条钉「现在对得上」，这条钉「对不上的时候会说实话」。
    桩下的单只带验证那一支 —— 既没有总结也没有抽取，所以结论是 `no_branch_ordered`
    （2026-08-25 之前它叫 `no_summary_branch`；抽取进了单之后「没排总结」不再等价于
    「白排了」，词也跟着换）。
    """
    from novel_harness.chapter_refresh import BRANCH_VALIDATION, CoverageDecision

    wheel = _wheel(tmp_path, 2)
    _seed_summary(wheel, 1, stale=True)

    def only_validation(conn, **kwargs):
        return CoverageDecision(BRANCH_VALIDATION, "att-x", reused=False, processing="queued")

    monkeypatch.setattr(
        "novel_harness.summary_schedule.ensure_refresh_coverage", only_validation
    )
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=3,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
    finally:
        conn.close()
    assert decisions[1] == "no_branch_ordered", f"单里什么都没排就别说排了，实得 {decisions}"


def test_schedule_exempts_only_the_focused_chapter(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, 12)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        # 作者在第 12 章写：焦点=12 不该被调度；别的章（远近都算）该补。
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=12,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=12,
            limit=20,
        )
        assert decisions[12] == "focused", "焦点中的章不调度"
        assert decisions[11] == "queued", f"Δ=1 的章必调度，实得 {decisions[11]}"
        assert decisions[1] == "queued", f"最远那章也照排，实得 {decisions[1]}"
    finally:
        conn.close()


def test_half_imported_book_fills_missing_chapters_without_save(tmp_path: Path) -> None:
    """导入一本「生成一半的书」：缺章/不对齐都被调度填上，不点保存。"""
    wheel = _wheel(tmp_path, 5)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    try:
        decisions = schedule_alignment(
            conn,
            wheel["pid"],
            draft_chapter=5,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        queued = [n for n, d in decisions.items() if d in QUEUED_OUTCOMES]
        assert 1 in queued and 2 in queued and 3 in queued, (
            f"缺章 1..5 应全被调度，实得 {decisions}"
        )
        # 结果写进持久 attempt（系统记录）——不是内存 enqueue。
        count = conn.execute(
            """
            SELECT COUNT(*) FROM chapter_refresh_attempt a
            JOIN chapter_refresh_run r ON r.id = a.run_id
            JOIN chapter c ON c.id = r.chapter_id
            WHERE c.project_id = ?
            """,
            (wheel["pid"],),
        ).fetchone()[0]
        assert count >= 3, f"应有 >=3 条覆盖 attempt，实得 {count}"
    finally:
        conn.close()


def test_one_enqueue_failure_does_not_block_the_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6：单章入队失败不阻塞整轮（调度器把失败记成 enqueue_failed 继续往下）。"""
    wheel = _wheel(tmp_path, 4)
    conn, epoch, ruhash = _conn_and_ruleset(wheel)
    from novel_harness.summary_schedule import schedule_alignment as real

    calls = 0

    def flaky(conn, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("模拟入队失败")

    monkeypatch.setattr("novel_harness.summary_schedule.ensure_refresh_coverage", flaky)
    try:
        decisions = real(
            conn,
            wheel["pid"],
            draft_chapter=4,
            ruleset_epoch=epoch,
            ruleset_hash=ruhash,
            focused_chapter=None,
            limit=20,
        )
        failed = [n for n, d in decisions.items() if d == "enqueue_failed"]
        # 没有焦点 = 一章都不豁免：4 章全试过、全失败（第 4 章 Δ=0 权重 0，
        # 但权重不再是门槛，防抖只认 focused_chapter）。
        assert len(failed) == 4, f"1..4 全失败 = 全部记 enqueue_failed，实得 {decisions}"
        assert calls == 4, "失败章不阻塞后续章的入队尝试"
    finally:
        conn.close()


def test_a_stale_chapter_really_gets_a_new_summary_written_over_the_old_one(
    tmp_path: Path,
) -> None:
    """维护者 2026-08-22 裁定：覆写单**真去重买一份、自动覆盖旧的**，不是「只提示」。

    上一条钉的是「报了覆写 = 单里带着总结那一支」；这一条钉的是那张单**跑完之后
    库里真的换了一份**——中间还隔着 dispatcher 和 `ensure` 的幂等判据，它要是把
    「这一章已经有总结了」当成「不用再生成」，覆写就会在最后一米静默丢掉：扫描器
    报了、单也下了、总结照旧停在旧版。这条红了就是那种丢法。
    """
    from novel_harness.draft.rolling_summary import SummaryStore

    wheel = _wheel(tmp_path, 2)
    _seed_summary(wheel, 1, stale=True)
    runtime = _stub_runtime(wheel["db"], limit=20)

    rounds = 0
    while rounds < 5:
        rounds += 1
        if runtime.autonomy_once() == 0:
            break
        runtime.pump_once()

    conn = connect(wheel["db"])
    try:
        by_chapter = {
            s.chapter_number: s
            for s in scan_chapter_summary_state(conn, wheel["pid"], draft_chapter=3)
        }
        assert by_chapter[1].state == "paired", (
            f"改过正文的章跑完一轮之后仍然不对齐：{by_chapter[1].state}"
        )
        head = SummaryStore(conn).latest(wheel["pid"], 1)
        assert head is not None and head.summary != "旧总结", (
            "旧那一份还挂在 head 上 —— 单下了、活没干，覆写在最后一米丢了"
        )
    finally:
        conn.close()
    assert runtime.autonomy_once() == 0, "覆写完再扫不许再下单（不反复花钱）"
