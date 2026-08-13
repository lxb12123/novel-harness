"""活动日志读端 —— ADR 0020 的「可查」那一半。

这份文件测的东西按重要性排：

1. **`jump` 落在真路由上。** 「跳去哪个模块改」由后端给，而给错了的表现是一个
   点了没反应的按钮——比没有按钮更糟。`test_every_jump_endpoint_resolves_to_a_real_route`
   把每条 jump 吐出来的路径拿去和 `app.routes` 对。
2. **日志是一个全新的泄漏面。** `decision_log.payload_json` 是开放 JSON，
   `NodeProps` 是 `extra="allow"`——一条把完整 Node 塞进 payload 的确认，会在展开详情里
   把 `props.twist` / `props.plot_note` 原样交出去。这里种了那两种实测形态。
3. **按 actor 分得开。** 自动升 CANON 之后 system 行会长得快得多，
   作者自己点过的那几十次会被淹没（ADR 0020 的原话）。
4. **零带着理由。** `cost` 没记账时是 `null` 不是 `0.0`，token 同理。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from test_api import PLOT_NOTE, TWIST, _seed_low_confidence_proposal, _seed_provisional_event

from novel_harness import activity, decisions, project
from novel_harness.api.deps import get_extraction_runner
from novel_harness.db import connect
from novel_harness.decisions import SYSTEM_ACTOR, DecisionKind, Verdict
from novel_harness.draft.provider import CompletionResult
from novel_harness.extract import RawChapterAnalysis, RawEvent, RawStateUpdate
from novel_harness.extract.auto_canon import promote_clean_facts
from novel_harness.extract.call_audit import record_call
from novel_harness.extract.proposals import confirm_provisional_events
from novel_harness.extract.runner import ExtractionRunner
from novel_harness.extract.service import ExtractionService
from novel_harness.graph import (
    AliasSpec,
    ChapterSpec,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.ids import EntityType, new_id

QUOTE = "萧决在青云城主府第一次听说了血脉秘密的真相。"


# ══════════════════════════════════════════════════════════════════════════
# 播种
# ══════════════════════════════════════════════════════════════════════════


def seed_run(
    book: dict[str, str],
    chapter: int = 1,
    *,
    status: str = "SUCCEEDED",
    proposals: int = 0,
    call_id: str | None = None,
) -> str:
    """一条 `extraction_run`。**直接写表**：跑一次真抽取要模型、要钱。

    时间戳写死是为了让契约 fixture 稳定（`test_frontend_contract.py` 也吃这个 helper）；
    `created_at` 仍走 SQL 的 DEFAULT，归并排序靠的是它。
    """
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        snapshot_id = SqliteStoryGraph(conn).chapter_snapshots(pid, chapter)[0].snapshot_id
        run_id = new_id(EntityType.EXTRACTION_RUN, pid)
        conn.execute(
            """
            INSERT INTO extraction_run (
                id, project_id, chapter_number, snapshot_id, status, errors_json,
                valid_event_count, discarded_event_count, proposal_count, model_call_id,
                schema_version, prompt_hash, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, 3, 1, ?, ?, 'm4.analysis.v1', ?, ?, ?)
            """,
            (
                run_id,
                pid,
                chapter,
                snapshot_id,
                status,
                # **真形态**：`ExtractionErrorCode` 的真值 + `runner.py` 真写下的那句
                # 英文诊断。这里原来种的是 `{"code":"boom","message":"模型没答话"}`
                # ——一个编出来的码配一句中文，于是「失败的抽取会不会把机器码摆给作者」
                # 这条路径在两个运行时的守卫下都是绿的，而真实形态是
                # `provider_failure：chapter analysis provider failed`。
                '[{"code":"provider_failure","message":"chapter analysis provider failed"}]'
                if status == "FAILED"
                else "[]",
                proposals,
                call_id,
                f"prompt:{chapter}",
                "2026-08-10T00:00:00.100Z",
                "2026-08-10T00:00:01.100Z",
            ),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def seed_call(
    book: dict[str, str],
    *,
    capability: str = "extractor",
    tokens_in: int | None = 1200,
    tokens_out: int | None = 400,
    cost: float | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> str:
    """一条 `model_call` 审计行。

    两个缓存参数**默认 None**，也就是「这条端点没报」那一档——它是今天绝大多数样本的
    真实形状。要验另外两档（报了 0 / 报了正数）的调用方显式传，
    `tests/test_cache_usage.py` 和契约夹具都这么做：**夹具里只躺着一种形状的样本，
    等于那条屏幕守卫扫的是一块永远长一个样的屏幕**（`_RUN_ERROR_LABEL` 那一节记着
    这个仓库上一次栽在这上面的现场）。

    `tokens_out` 2026-08-12 从写死的 `400` 变成入参：**「供应商一个用量数都没给」
    那一档在此之前根本 seed 不出来**（`tokens_in=None` 只造得出「给了一半」），
    而那一档正是流式起草打开之后 DeepSeek 的常态形状。
    """
    conn = connect(book["db"])
    try:
        call_id = new_id(EntityType.CALL, book["pid"])
        conn.execute(
            """
            INSERT INTO model_call (
                id, project_id, capability, model, params_json, prompt_hash,
                in_artifact, out_artifact, tokens_in, tokens_out, ms, cost,
                cache_read_tokens, cache_write_tokens
            ) VALUES (?, ?, ?, 'deepseek-v4', '{"finish_reason":"stop"}',
                      'ph', 'a', 'b', ?, ?, 900, ?, ?, ?)
            """,
            (
                call_id,
                book["pid"],
                capability,
                tokens_in,
                tokens_out,
                cost,
                cache_read_tokens,
                cache_write_tokens,
            ),
        )
        conn.commit()
        return call_id
    finally:
        conn.close()


def seed_summary(
    book: dict[str, str],
    chapter: int = 1,
    *,
    call_id: str,
    text: str = "萧决在青云城主府第一次听说了血脉秘密。",
) -> str:
    """一条**模型写的**滚动总结，挂在某次调用上（`model_call_id` 就是它的地址）。

    直接写表（同 `seed_run` / `seed_call`）：走真路径要一次模型调用、要钱。
    `source` / `status` 两列用 DDL 默认值，也就是 `RollingSummarizer.ensure` 落下的
    那一份形状（`model` / `ACTIVE`）——作者改过或撤回过的行是**另外追加的行**，
    `model_call_id` 为空，天生不会被这条 join 认走。
    """
    conn = connect(book["db"])
    try:
        summary_id = new_id(EntityType.SUMMARY, book["pid"])
        conn.execute(
            """
            INSERT INTO chapter_summary (
                id, project_id, chapter_number, summary,
                schema_version, prompt_hash, model_call_id
            ) VALUES (?, ?, ?, ?, 'chapter-summary-v1', ?, ?)
            """,
            (summary_id, book["pid"], chapter, text, f"prompt:summary:{chapter}", call_id),
        )
        conn.commit()
        return summary_id
    finally:
        conn.close()


@pytest.fixture
def seeded(book: dict[str, str]) -> dict[str, str]:
    """一条抽取运行 + 它的模型调用 + 一次 `actor='system'` 的自动生效。

    system 那条**走真路径**（`confirm_provisional_events(actor='system')`，也就是
    `extract/auto_canon.py` 调的那一个），不是手写一条 decision_log ——
    手写的话这份测试验的是我自己编的 payload 形状，而日志页要显示的是真实形状。
    """
    call_id = seed_call(book)
    run_id = seed_run(book, 1, call_id=call_id)
    event_id = _seed_provisional_event(book)
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        confirmation = confirm_provisional_events(
            conn,
            SqliteStoryGraph(conn),
            SqliteEventStore(conn),
            pid,
            [event_id],
            expected_canon_version=project.require_canon_version(conn, pid),
            actor=SYSTEM_ACTOR,
        )
        return {
            **book,
            "run_id": run_id,
            "call_id": call_id,
            "event_id": event_id,
            "system_decision_id": confirmation.decision_id,
        }
    finally:
        conn.close()


# ── 带毒的一本书：三种毒 + 一整条真链路 ────────────────────────────────────

SECRET_TEXT = "秘密正文：玄铁令沉在北荒的枯井里"
"""**第三种毒。**

`TWIST` / `PLOT_NOTE` 挂在 `props` 上（`NodeProps` 的 `extra="allow"`）；这一条不挂那儿
——它是 `secret` 扩展行的 `description`，也就是 `POST /nodes` 的 `description` 落库的地方。
同一个 Secret 上作者写的东西有**两个**存放处，收窄网必须分别兜住。
"""

POISON_CHAPTER = 4
POISON_EVENT_QUOTE = "顾清音在北荒把玄铁令交给萧决。"
POISON_STATE_QUOTE = "萧决的修为终于突破到了金丹境界。"
POISON_TEXT = f"第四章 渡口\n\n{POISON_EVENT_QUOTE}\n{POISON_STATE_QUOTE}\n"

POISONS = (TWIST, PLOT_NOTE, SECRET_TEXT)


def _poison_analysis() -> RawChapterAnalysis:
    """一份**全干净**的抽取结果：三条事实全都会自动升 CANON（ADR 0020）。

    故意不放例外 bucket——要测的是「系统自己往书里写完之后，日志把什么交出来」，
    进了队列的那些还轮不到日志说话。
    """
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="顾清音交出玄铁令。",
                quote=POISON_EVENT_QUOTE,
                participants=("顾清音", "萧决"),
                knowers=("顾清音",),
                # 事件揭示的正是那个带毒的秘密：payload 里会出现它的**名字**。
                revealed_facts=("血脉秘密",),
                confidence=0.95,
            ),
        ),
        state_updates=(
            RawStateUpdate(
                kind="location",
                subject="顾清音",
                object="北荒",
                quote=POISON_EVENT_QUOTE,
                confidence=0.95,
            ),
            RawStateUpdate(
                kind="state",
                subject="萧决",
                dimension="修为",
                value="金丹",
                quote=POISON_STATE_QUOTE,
                confidence=0.96,
            ),
        ),
        character_profiles=(),
    )


@pytest.fixture
def poisoned(book: dict[str, str]) -> dict[str, str]:
    """把三种毒挂进书里，然后**跑真链路**：抽取落库 → 自动升 CANON → 留 system 日志。

    每一步都走生产代码（`ExtractionService.ingest` + `promote_clean_facts`），
    不手写 `decision_log`：手写的话这份测试验的是我自己编的 payload 形状，
    而日志页要显示的是真实形状。
    """
    conn = connect(book["db"])
    try:
        pid = book["pid"]
        graph = SqliteStoryGraph(conn)
        # 秘密上两种毒同时挂着：props.twist（extra="allow"）+ 扩展行的 description。
        graph.upsert_node(
            NodeSpec(
                project_id=pid,
                label=NodeLabel.SECRET,
                name="血脉秘密",
                props=NodeProps.model_validate({"twist": TWIST}),
                secret=SecretDetail(description=SECRET_TEXT),
            )
        )
        # 地点上挂 plot_note，且登场章在未来——`api/app.py::_narrow` 的两条判据都命中。
        graph.upsert_node(
            NodeSpec(
                project_id=pid,
                label=NodeLabel.LOCATION,
                name="北荒",
                props=NodeProps.model_validate(
                    {"first_appears_chapter": 200, "plot_note": PLOT_NOTE}
                ),
            )
        )
        graph.upsert_node(
            NodeSpec(project_id=pid, label=NodeLabel.CHARACTER, name="顾清音")
        )
        dimension = graph.upsert_node(
            NodeSpec(
                project_id=pid,
                label=NodeLabel.STATE_DIM,
                name="修为",
                props=NodeProps(dim_key="cultivation"),
            )
        )
        graph.add_alias(AliasSpec(project_id=pid, node_id=dimension.id, surface="修为"))
        graph.put_chapter(
            ChapterSpec(
                project_id=pid,
                number=POISON_CHAPTER,
                heading="第四章 渡口",
                path=f"chapters/{POISON_CHAPTER:04d}.md",
                text=POISON_TEXT,
            )
        )
        conn.commit()

        chapter = next(
            snapshot
            for snapshot in graph.current_snapshots(pid)
            if snapshot.number == POISON_CHAPTER
        )
        report = ExtractionService(
            conn=conn,
            graph=graph,
            event_store=SqliteEventStore(conn),
            proposal_store=SqliteProposalStore(conn),
        ).ingest(pid, chapter, _poison_analysis(), prompt_hash="prompt:poison")
        assert report.clean_event_ids and report.clean_edge_ids, (
            "这份 analysis 本该全是干净的——没有自动升就没有 system 日志行可查"
        )
        promotion = promote_clean_facts(conn, pid, report)
        assert promotion.failures == (), promotion.failures
        return {**book, "event_id": promotion.promoted_event_ids[0]}
    finally:
        conn.close()


def _every_activity_response(client: TestClient, pid: str) -> str:
    """日志读端**全部**出参拼成一个串：一页折叠行 + 每一条的展开详情 + 底栏那一格。

    拼起来搜是有意的：逐个端点写断言的写法，第一个忘了写的端点就是一次泄漏。
    """
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 200})
    assert page.status_code == 200, page.text
    texts = [page.text]
    for entry in page.json()["entries"]:
        detail = client.get(f"/api/projects/{pid}/activity/{entry['id']}")
        assert detail.status_code == 200, detail.text
        texts.append(detail.text)
    runs = client.get(f"/api/projects/{pid}/runs")
    assert runs.status_code == 200, runs.text
    texts.append(runs.text)
    return "\n".join(texts)


def _entries(client: TestClient, pid: str, **params: Any) -> list[dict[str, Any]]:
    r = client.get(f"/api/projects/{pid}/activity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def _by_id(entries: list[dict[str, Any]], entry_id: str) -> dict[str, Any]:
    hit = [e for e in entries if e["id"] == entry_id]
    assert len(hit) == 1, f"{entry_id} 不在这一页里：{[e['id'] for e in entries]}"
    return hit[0]


# ══════════════════════════════════════════════════════════════════════════
# 1. jump 必须落在真路由上
# ══════════════════════════════════════════════════════════════════════════


def _route_matchers() -> list[re.Pattern[str]]:
    from novel_harness.api.app import app

    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif (original := getattr(route, "original_router", None)) is not None:
            routes.extend(r for r in original.routes if isinstance(r, APIRoute))
    return [
        re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(r.path).replace("\\{", "{")) + "$")
        for r in routes
    ]


def _resolves(path: str) -> bool:
    return any(m.match(path) for m in _route_matchers())


def test_the_route_matcher_is_not_vacuous() -> None:
    """守卫的自守卫：匹配器要是把所有东西都放过，下面那条就永远绿。"""
    assert _resolves("/api/projects/p1/canon/knowledge")
    assert not _resolves("/api/projects/p1/canon/nonsense")
    assert not _resolves("/api/projects/p1/activity/x/y/z")


def test_every_jump_endpoint_resolves_to_a_real_route(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """**这条是本文件存在的第一个理由。**

    「跳去哪个模块改」由后端给（前端不许从标题反推），于是给错了没人会发现——
    作者点一下，什么都不发生。这里逐条把它送回路由表。
    """
    pid = seeded["pid"]
    entries = _entries(client, pid, limit=200)
    assert entries, "一条日志都没有，下面的断言全是空转"
    seen = 0
    for entry in entries:
        jump = entry["jump"]
        if jump is None:
            continue
        for endpoint in jump["endpoints"]:
            seen += 1
            assert _resolves(endpoint), f"{entry['id']} 指向一条不存在的路由：{endpoint}"
    assert seen, "没有一条 jump 带 endpoints —— 这一页证明不了任何事"


def test_a_chapter_jump_admits_it_cannot_be_edited(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """兜底坐标的 `endpoints` 必须是空的，**而且那是一个断言不是没填**。

    自动升上去的**边**就落在这一档：抽取只产 `LOCATED_AT` / `HAS_STATE` /
    `RELATED_TO`，而 `corrections.py` 只改 KNOWS↔BELIEVES 和事件名单。
    编一个按钮出来，ADR 0020「出现『作者改不回来』的形态」这条推翻条件就永远观测不到。
    """
    entries = _entries(client, seeded["pid"], limit=200)
    chapter_jumps = [
        e["jump"] for e in entries if e["jump"] and e["jump"]["target"] == "chapter"
    ]
    assert chapter_jumps, "这一页里没有兜底坐标 —— 换个种子，否则这条空转"
    for jump in chapter_jumps:
        assert jump["endpoints"] == []
        assert jump["chapter_number"] is not None


def test_the_knowledge_cell_jump_actually_changes_that_cell(
    client: TestClient, book: dict[str, str]
) -> None:
    """跳转坐标 + 它给的路由，合起来真能把那条事实改回去。

    这是「事后可查可改」两半接上的唯一证明：光有日志或光有 `/canon/…` 都不算。
    """
    pid = book["pid"]
    declared = client.post(
        f"/api/projects/{pid}/declare/knows",
        json={"who": "萧决", "secret": "血脉秘密", "quote": QUOTE},
    )
    assert declared.status_code == 200, declared.text
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "他以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert flipped.status_code == 200, flipped.text

    entry = _by_id(_entries(client, pid, limit=200), flipped.json()["decision_id"])
    jump = entry["jump"]
    assert jump["target"] == "knowledge_cell"
    assert jump["character_id"] == book["萧决"] and jump["secret_id"] == book["血脉秘密"]
    assert jump["endpoints"] == [f"/api/projects/{pid}/canon/knowledge"]

    # 照着它给的坐标 + 路由改回去 —— 这一步 200 才叫「跳得过去、改得掉」。
    back = client.post(
        jump["endpoints"][0],
        json={
            "character_id": jump["character_id"],
            "secret_id": jump["secret_id"],
            "to_type": "KNOWS",
            "expected_canon_version": flipped.json()["canon_version"],
        },
    )
    assert back.status_code == 200, back.text


def _knowledge_jump(client: TestClient, book: dict[str, str], who: str) -> dict[str, Any]:
    """声明一次「谁知道血脉秘密」，把那条日志行的 `jump` 取回来。"""
    pid = book["pid"]
    declared = client.post(
        f"/api/projects/{pid}/declare/knows",
        json={"who": who, "secret": "血脉秘密", "quote": QUOTE},
    )
    assert declared.status_code == 200, declared.text
    entries = _entries(client, pid, limit=200)
    hits = [e for e in entries if e["jump"] and e["jump"]["target"] == "knowledge_cell"]
    assert hits, "没有认知矩阵那一档的日志行 —— 这条测试在空转"
    return hits[0]["jump"]


def test_the_knowledge_cell_jump_carries_a_cast_coordinate(
    client: TestClient, book: dict[str, str]
) -> None:
    """**跳过去那一行必须真的在表上。**

    矩阵的行由本章正文推（ADR 0018），而声明的生效章由引语定（ADR 0006）——
    一句满是代词的声明会把坐标指向一章「他一次都没被点名」的正文，那一行不在表上，
    高亮和编辑入口一起落空，而屏幕上是一张看起来完全正常的表。

    坐标给的是**作者认得的称呼**（`?cast=` 收的一直是原文），不是 node_id：
    前端一个字都不解析，也不许拿屏幕上的人名自己去凑一个。
    """
    jump = _knowledge_jump(client, book, "萧决")
    assert jump["cast"] == ["萧决"]

    # 拿它去请求矩阵：那一行真的出现了，且那一格可改（`endpoints` 打得通）。
    matrix = client.get(
        f"/api/projects/{book['pid']}/chapters/{jump['chapter_number']}/matrix",
        params={"cast": "、".join(jump["cast"])},
    )
    assert matrix.status_code == 200, matrix.text
    body = matrix.json()
    assert [c["id"] for c in body["characters"]] == [book["萧决"]]
    assert body["unresolved_cast"] == []


def test_an_ambiguous_name_gets_no_cast_coordinate_at_all(
    client: TestClient, book: dict[str, str]
) -> None:
    """**歧义的时候什么都不给，绝不替作者挑一个**（ADR 0004 / §10 约束 8）。

    「师兄」在这本书里指向两个人。一个只有歧义称呼的人物，后端给不出一个能唯一指回
    他的坐标——那时 `cast` 是空的，界面退回今天的行为（矩阵那边照旧说「没有在这一章
    找到刚才那一格」）。**编一个坐标出来会把作者送到另一个人的那一行上。**
    """
    pid = book["pid"]
    # 先用**还不含歧义**的本名声明（声明本身就拒歧义，ADR 0004），再把这个称呼变歧义
    # ——这正是真实顺序：作者写着写着给另一个人也起了同一个称呼。
    jump = _knowledge_jump(client, book, "萧决")
    assert jump["cast"] == ["萧决"], "前提坏了：这一步本来该给得出坐标"

    muddled = client.post(
        f"/api/projects/{pid}/aliases", json={"of": "李管家", "surface": "萧决"}
    )
    assert muddled.status_code == 200, muddled.text
    # 自守卫：他**每一个**称呼现在都指向不止一个人（本名撞车 + 「师兄」本来就歧义），
    # 否则下面那条断言测的是「后端懒得给」而不是「后端不替作者挑」。
    for surface in ("萧决", "师兄"):
        hits = client.get(f"/api/projects/{pid}/resolve", params={"surface": surface}).json()
        assert len(hits["hits"]) > 1, f"「{surface}」不再有歧义 —— 换个样本"

    # 同一条日志行再读一次。**不重新声明**：声明本身就会被歧义拒掉（那是另一条纪律），
    # 而这里要验的是「已经躺在日志里的那一行，今天还给不给得出坐标」。
    again = [
        e["jump"]
        for e in _entries(client, pid, limit=200)
        if e["jump"] and e["jump"]["character_id"] == book["萧决"]
    ]
    assert again, "那条日志行不见了 —— 这条测试在空转"
    assert all(jump["cast"] == [] for jump in again), f"歧义时还是给了坐标：{again}"


def test_the_cast_coordinate_prefers_the_real_name_over_a_nickname(
    client: TestClient, book: dict[str, str]
) -> None:
    """本名优先，别名兜底。

    反过来会把**化名**摆到右栏那句「只看：…」上，而化名在这本书里可能正是
    「别人还不知道他是谁」的编码（ADR 0004 说别名差异是 canon 不是噪声）——
    拿它当筛选条件，作者读起来像换了一个人。
    """
    pid = book["pid"]
    added = client.post(
        f"/api/projects/{pid}/aliases", json={"of": "萧决", "surface": "青云城少主"}
    )
    assert added.status_code == 200, added.text
    jump = _knowledge_jump(client, book, "萧决")
    assert jump["cast"] == ["萧决"], "别名比本名长，按长度排会挑错那一个"


def test_only_the_knowledge_cell_target_gets_a_cast_coordinate(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """别的几档一个都不许有。

    `event_cast` 跳的是「已确认情节」那一格里的一份名单，不是矩阵的一行——给了 cast
    只会顺手把右栏别的几格一起按这一个人过滤掉。`proposal` / `chapter` 同理。
    """
    offenders = {
        e["id"]: e["jump"]["cast"]
        for e in _entries(client, seeded["pid"], limit=200)
        if e["jump"] and e["jump"]["target"] != "knowledge_cell" and e["jump"]["cast"]
    }
    assert not offenders, f"这几档不该带在场坐标：{offenders}"


def test_a_row_that_changed_several_events_does_not_read_as_unfixable(
    client: TestClient, book: dict[str, str]
) -> None:
    """**空 `endpoints` 在这一行不是「改不了」的意思，而 label 必须把这件事说出来。**

    一章通常抽出不止一条干净事件，而 `promote_clean_facts` 把它们**一次全升**——
    于是那一整章只留下一条日志行，`len(events) > 1` ⇒ 退到兜底坐标 ⇒ `endpoints: []`。
    可那几条事件其实**改得掉**（下面那个 200 就是证据）：`endpoints` 空只是因为
    「后端不替作者挑是哪一条」，不是因为没有路由。

    两种含义共用一个空元组这件事本身没法在这一层修掉（那要改出参形状，而日志页正照着
    今天这份契约在写）——但**一行写着「去第 4 章」的按钮会让作者以为那几条没救了**，
    而 ADR 0020 的整条退路就是「改得掉」。所以措辞里必须带上「有几条」。
    """
    pid = book["pid"]
    first = _seed_provisional_event(book)
    second = _seed_provisional_event(book)
    conn = connect(book["db"])
    try:
        confirmation = confirm_provisional_events(
            conn,
            SqliteStoryGraph(conn),
            SqliteEventStore(conn),
            pid,
            [first, second],
            expected_canon_version=project.require_canon_version(conn, pid),
            actor=SYSTEM_ACTOR,
        )
    finally:
        conn.close()

    entry = _by_id(_entries(client, pid, limit=200), confirmation.decision_id)
    jump = entry["jump"]
    assert jump["target"] == "chapter" and jump["endpoints"] == []
    assert "2 条事件" in jump["label"], f"这一行说不出它改了几条：{jump['label']}"

    # 反证：那两条真的改得掉——否则上面那句措辞才是骗人的那一个。
    detail = client.get(f"/api/projects/{pid}/activity/{confirmation.decision_id}").json()
    canon_ids = [event["event_id"] for event in detail["payload"]["events"]]
    assert len(canon_ids) == 2
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    fixed = client.post(
        f"/api/projects/{pid}/canon/events/{canon_ids[0]}/cast",
        json={"knower_ids": [book["萧决"]], "expected_canon_version": version},
    )
    assert fixed.status_code == 200, fixed.text


def test_the_event_cast_jump_on_a_system_row_really_edits_that_event(
    client: TestClient, poisoned: dict[str, str]
) -> None:
    """**ADR 0020 的整条赌注就压在这一条上。**

    系统自己把一条事件写进了 CANON，`knowers` 是推断不是文本事实——它是最可能错的
    那一维（ADR 0020「代价」第一条）。日志给的坐标必须真能把它改回来：
    `test_the_knowledge_cell_jump_actually_changes_that_cell` 验的是**作者亲手**那条路，
    而这一条验的是**没人点过的那条**，两者不是同一段代码。
    """
    pid = poisoned["pid"]
    rows = [
        e
        for e in _entries(client, pid, actor=SYSTEM_ACTOR, limit=200)
        if e["jump"] and e["jump"]["target"] == "event_cast"
    ]
    assert len(rows) == 1, f"自动升上去的事件没给出可编辑坐标：{[e['title'] for e in rows]}"
    jump = rows[0]["jump"]

    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    fixed = client.post(
        jump["endpoints"][0],
        json={"knower_ids": [poisoned["萧决"]], "expected_canon_version": version},
    )
    assert fixed.status_code == 200, fixed.text
    knowers = [node["name"] for node in fixed.json()["event"]["knowers"]]
    assert knowers == ["萧决"], "点过去了但名单没改成——那和点了没反应一样"


def test_a_pending_proposal_gets_a_jump_to_the_review_queue(
    client: TestClient, book: dict[str, str]
) -> None:
    """产出了待审提案的那次抽取，跳到那一条提案上（ADR 0020 的三个例外 bucket）。"""
    proposal_id, _event_id, _base = _seed_low_confidence_proposal(book)
    run_id = seed_run(book, 1, proposals=1)
    entry = _by_id(_entries(client, book["pid"], limit=200), run_id)
    assert entry["jump"]["target"] == "proposal"
    assert entry["jump"]["proposal_id"] == proposal_id
    assert entry["jump"]["endpoints"] == [
        f"/api/projects/{book['pid']}/proposals/{proposal_id}/accept",
        f"/api/projects/{book['pid']}/proposals/{proposal_id}/reject",
        f"/api/projects/{book['pid']}/proposals/{proposal_id}/edit",
    ]


def test_two_pending_proposals_do_not_get_picked_for_the_author(
    client: TestClient, book: dict[str, str]
) -> None:
    """两条待审时**不替作者挑第一条**（同 `AmbiguousName` 那条纪律）：只给章号。"""
    _seed_low_confidence_proposal(book)
    _seed_low_confidence_proposal(book)
    run_id = seed_run(book, 1, proposals=2)
    jump = _by_id(_entries(client, book["pid"], limit=200), run_id)["jump"]
    assert jump["target"] == "proposal"
    assert jump["proposal_id"] is None
    assert jump["endpoints"] == []
    assert "2 条待审" in jump["label"]


def test_a_rejected_proposal_never_points_at_the_canon_editor() -> None:
    """否决掉的事实没有升进 CANON，指向 `/canon/events/…` 就是一个必然 404 的按钮。"""
    rejected = decisions.Decision(
        id="decision:x:1",
        ts="2026-08-10T00:00:00.000Z",
        project_id="p",
        kind=DecisionKind.PROPOSAL_REVIEW,
        decision=Verdict.REJECT,
        payload={"events": [{"event_id": "event:x:1"}], "edges": [], "characters": []},
        chapter_number=3,
    )
    jump = activity._decision_jump(rejected)
    assert jump is not None and jump.target is activity.JumpTarget.CHAPTER


# ── 1.1 兜底那一档里躺过的两样东西（2026-08-13）─────────────────────────────
#
# **它们落进兜底都不是因为「今天没有更细的目标」**，而是目标后来才长出来、这张表
# 没跟着改。留在那儿，兜底那一档就从一句诚实话（ADR 0020 拿它当推翻条件的观测点）
# 变成一句骗人的话。


def _thin_analysis() -> str:
    """一份最小的分析（引语取自第 1 章正文，定位得到）。

    **够用**：下面那条测的是 run 有没有真的重新跑起来，不是抽出了什么
    （那在 `test_extraction_api.py`）。
    """
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="萧决听说了血脉秘密。",
                quote=QUOTE,
                participants=("萧决",),
                knowers=("萧决",),
                revealed_facts=(),
                confidence=0.95,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()


def test_a_failed_run_offers_a_real_retry_that_actually_reruns_it(
    client: TestClient, book: dict[str, str]
) -> None:
    """没跑成的那一条上必须有「再来一次」，而且那颗按钮真的能让它重跑。

    在这之前 `_run_jump` **一眼都不看 status**：成功和失败走同一条路径，于是屏幕上
    那句红字（「没能连上你配置的模型服务 · 失败」）只配着一颗「去第 N 章 →」——
    **重跑的能力后端一直都在**，只是日志上没有那颗按钮。

    整条链路走真代码（`POST …/extract` → 后台 → `extraction_run` 落 FAILED），
    只有模型那一步是桩：第一次抛（造出真的失败形态），第二次答一份空分析。
    """
    calls = {"n": 0}

    def analyze(_request: Any) -> CompletionResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("private transport detail")
        return CompletionResult(text=_thin_analysis(), model="api-test-model")

    runner = ExtractionRunner(lambda: connect(Path(book["db"])), analyze)
    client.app.dependency_overrides[get_extraction_runner] = lambda: runner
    try:
        pid = book["pid"]
        queued = client.post(f"/api/projects/{pid}/chapters/1/extract")
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["id"]
        assert client.get(f"/api/projects/{pid}/extractions/{run_id}").json()["status"] == "FAILED"

        entry = _by_id(_entries(client, pid, limit=200), run_id)
        jump = entry["jump"]
        assert entry["status"] == "failed"
        assert jump["target"] == "extraction_retry"
        assert jump["endpoints"] and _resolves(jump["endpoints"][0])
        # 措辞归后端，而且是作者的话（没有码、没有英文、没有裸 id）。
        assert "整理" in jump["label"] and "第 1 章" in jump["label"]

        # ── 探针：不带 `force` 就是一颗**点了没反应**的按钮 ────────────────────
        # 接口照样 202，可那一行照旧红着、模型一次都没再被调用。这正是那条路必须
        # 带参数的全部理由，也是「这张表只出路径」那条注释指着的东西。
        idle = client.post(jump["endpoints"][0])
        assert idle.status_code == 202 and idle.json()["status"] == "FAILED"
        assert calls["n"] == 1

        again = client.post(jump["endpoints"][0], params={"force": "true"})
        assert again.status_code == 202, again.text
        # 同一条 run 原地重置（不删行、不新建行），所以日志上那一行不会变成两行。
        assert again.json()["id"] == run_id
        assert calls["n"] == 2
        final = client.get(f"/api/projects/{pid}/extractions/{run_id}").json()
        assert final["status"] == "SUCCEEDED", final

        after = _by_id(_entries(client, pid, limit=200), run_id)
        assert after["status"] == "succeeded"
        # 跑成了之后那颗按钮跟着没了——`label` 和 `target` 是同一次判断的两个产物。
        assert after["jump"]["target"] != "extraction_retry"
    finally:
        client.app.dependency_overrides.pop(get_extraction_runner, None)


def test_a_summary_call_points_at_the_summary_it_wrote(
    client: TestClient, book: dict[str, str]
) -> None:
    """写总结那次调用跳的是**那一章的总结**，不是兜底的「去第 N 章」。

    右栏「章节总结」那一格（读 / 改 / 撤回 / 重新生成）是 2026-08-13 才长出来的，
    在那之前这一档除了章号真的没有更细的目标。目标有了，坐标就得跟着改。
    """
    pid = book["pid"]
    call_id = seed_call(book, capability="summarizer", tokens_in=None, tokens_out=None)
    seed_summary(book, 1, call_id=call_id)

    jump = _by_id(_entries(client, pid, limit=200), call_id)["jump"]
    assert jump["target"] == "summary" and jump["chapter_number"] == 1
    assert jump["endpoints"] == [f"/api/projects/{pid}/chapters/1/summary"]
    assert _resolves(jump["endpoints"][0])
    # 那条路由真的改得动这一章的总结——不是一个摆着好看的坐标。
    edited = client.patch(jump["endpoints"][0], json={"summary": "作者自己写的一段。"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["summary"] == "作者自己写的一段。"


def test_a_call_that_wrote_no_summary_still_falls_back_to_the_chapter(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """判据是「库里有没有一行总结指着它」，**不是 capability 那个字符串**。

    抽取那次调用照旧退到兜底坐标——按结构判，这一档不会因为哪天多一种能力名
    就凭空多出一颗跳去总结的按钮（那儿根本没有总结可看）。
    """
    jump = _by_id(_entries(client, seeded["pid"], limit=200), seeded["call_id"])["jump"]
    assert jump["target"] == "chapter" and jump["endpoints"] == []


# ══════════════════════════════════════════════════════════════════════════
# 2. 泄漏面
# ══════════════════════════════════════════════════════════════════════════


def test_narrow_payload_collapses_node_shaped_dicts_and_drops_props() -> None:
    """两条结构规则，递归执行。**比 `api/app.py::_narrow` 严**：那一个要一个「当前章」
    才判得出未来节点，而日志行没有当前章。"""
    payload = {
        "secret": {"id": "s", "label": "Secret", "name": "血脉秘密", "props": {"twist": TWIST}},
        "where": [
            {"id": "l", "label": "Location", "name": "北荒", "props": {"plot_note": PLOT_NOTE}}
        ],
        "props": {"plot_note": PLOT_NOTE},
        "keep": "这一条不该被动",
    }
    narrowed = activity.narrow_payload(payload)
    assert narrowed["secret"] == {"id": "s", "label": "Secret", "name": "血脉秘密"}
    assert narrowed["where"] == [{"id": "l", "label": "Location", "name": "北荒"}]
    assert "props" not in narrowed
    assert narrowed["keep"] == "这一条不该被动"
    assert TWIST not in str(narrowed) and PLOT_NOTE not in str(narrowed)


def test_detail_does_not_serialize_props_that_a_writer_smuggled_into_the_payload(
    client: TestClient, book: dict[str, str]
) -> None:
    """种一条把完整 Node 塞进 payload 的确认——展开详情一个字都不许交出去。

    这不是假想：`decision_log.payload` 收 `Mapping[str, Any]`，没有任何 schema 拦着
    某次改动把 `node.model_dump()` 整个写进去，而那份 dump 里带着 `props`。
    """
    pid = book["pid"]
    conn = connect(book["db"])
    try:
        smuggled = decisions.append(
            conn,
            project_id=pid,
            kind=DecisionKind.NODE_DECLARE,
            decision=Verdict.ACCEPT,
            subject_name="血脉秘密",
            payload={
                "node": {
                    "id": book["血脉秘密"],
                    "label": "Secret",
                    "name": "血脉秘密",
                    "props": {"twist": TWIST},
                },
                "future": {
                    "id": book["未来大能"],
                    "label": "Character",
                    "name": "未来大能",
                    "props": {"first_appears_chapter": 200, "plot_note": PLOT_NOTE},
                },
            },
        )
    finally:
        conn.close()
    r = client.get(f"/api/projects/{pid}/activity/{smuggled.id}")
    assert r.status_code == 200, r.text
    assert TWIST not in r.text and PLOT_NOTE not in r.text
    assert r.json()["payload"]["node"] == {
        "id": book["血脉秘密"],
        "label": "Secret",
        "name": "血脉秘密",
    }


def test_declaring_a_secret_does_not_put_its_description_in_the_log(
    client: TestClient, book: dict[str, str]
) -> None:
    """今天六个写入点一个都不带秘密正文——把这件事钉住，别哪天顺手加进 payload。"""
    pid = book["pid"]
    created = client.post(
        f"/api/projects/{pid}/nodes",
        json={"label": "Secret", "name": "玄铁令下落", "description": "其实在北荒的井里"},
    )
    assert created.status_code == 200, created.text
    entries = _entries(client, pid, limit=200)
    secret_rows = [e for e in entries if e["subtitle"].startswith("玄铁令下落")]
    assert secret_rows, f"没找到那条声明：{[e['subtitle'] for e in entries]}"
    r = client.get(f"/api/projects/{pid}/activity/{secret_rows[0]['id']}")
    assert r.status_code == 200, r.text
    assert "其实在北荒的井里" not in r.text


def test_a_poisoned_book_survives_the_whole_auto_canon_chain(
    client: TestClient, poisoned: dict[str, str]
) -> None:
    """**本节的头等断言。**三种毒 + 真链路（抽取落库 → 自动升 CANON → 读日志），
    整个读端的出参里一个字都不许出现。

    单测 `narrow_payload` 证明不了这件事：那验的是「我写的那份 payload 被收窄了」，
    而泄漏是「有一份我没想到的 payload 溜过去了」。所以这条从**真抽取**开始跑。
    """
    everything = _every_activity_response(client, poisoned["pid"])
    for poison in POISONS:
        assert poison not in everything, f"日志读端交出了：{poison}"


def test_the_poisoned_run_actually_wrote_something_to_look_at(
    client: TestClient, poisoned: dict[str, str]
) -> None:
    """上一条的自守卫：**空页面对任何毒都是干净的。**

    没有这一条，把 `read_activity` 改成 `return ActivityPage()` 也全绿——
    而那正是本仓库反复在修的那种「漂亮的空结果 + 200」。
    """
    entries = _entries(client, poisoned["pid"], limit=200)
    system_rows = [e for e in entries if e["actor"] == SYSTEM_ACTOR]
    assert len(system_rows) >= 2, "自动升 CANON 那两条 system 日志行没长出来"
    assert any(
        e["jump"] and e["jump"]["target"] == "event_cast" for e in system_rows
    ), "系统自己写进 CANON 的事件没有给出「去改名单」的坐标"


def test_narrowing_keeps_the_display_names_it_is_supposed_to_keep(
    client: TestClient, poisoned: dict[str, str]
) -> None:
    """**收窄过头同样是 bug。**

    `NodeRef` 的 docstring 把判据写死了：`label` 是 8 个枚举值之一、`name` 是面板本来
    就要渲染的东西，**两者都零内容风险**；有内容风险的是 `props` 和秘密正文。
    一条看不出说的是哪件事的日志，作者点不动——那和没有日志一样。
    """
    everything = _every_activity_response(client, poisoned["pid"])
    for name in ("血脉秘密", "北荒", "顾清音", "萧决"):
        assert name in everything, f"显示名被收窄掉了：{name}"


def _a_writer_that_logs_what_it_was_handed(project_id: str) -> dict[str, Any]:
    """一个**故意会漏的写入方**：把 `declare_node` 收到的入参原样记进日志。

    这不是稻草人。`NodeSpec` 正是作者的秘密**进入系统**的那个对象——
    `POST /nodes` 的 `description` 落在 `spec.secret.description` 上、
    作者手写的 `twist` 落在 `spec.props` 上。「日志记全一点，重放才对得上」
    这种改动写出来就长这样，而它一次带出两种毒。
    """
    spec = NodeSpec(
        project_id=project_id,
        label=NodeLabel.SECRET,
        name="血脉秘密",
        props=NodeProps.model_validate({"twist": TWIST}),
        secret=SecretDetail(description=SECRET_TEXT),
    )
    return {"spec": spec.model_dump(mode="json")}


def test_the_net_catches_a_writer_that_logs_the_declaration_it_received(
    client: TestClient, book: dict[str, str]
) -> None:
    """守卫的自守卫（漏的那一半）：种一个真会漏的写入方，网必须抓得住。

    **秘密的正文有两个存放处**：`props`（`extra="allow"` 的那条）和 `secret` 扩展行。
    只兜住前者的网，会把后者原样交出去——而后者才是作者真正打字打进去的那段。
    """
    pid = book["pid"]
    conn = connect(book["db"])
    try:
        leaked = decisions.append(
            conn,
            project_id=pid,
            kind=DecisionKind.SECRET_DECLARE,
            decision=Verdict.ACCEPT,
            subject_name="血脉秘密",
            payload=_a_writer_that_logs_what_it_was_handed(pid),
        )
    finally:
        conn.close()
    r = client.get(f"/api/projects/{pid}/activity/{leaked.id}")
    assert r.status_code == 200, r.text
    assert TWIST not in r.text, "props 上的毒漏了"
    assert SECRET_TEXT not in r.text, "secret 扩展行的毒漏了"
    # 反向：名字还在，作者看得出这条日志说的是哪个秘密。
    assert "血脉秘密" in r.text


def test_the_net_does_not_flatten_a_reference_that_was_already_narrow(
    client: TestClient, book: dict[str, str]
) -> None:
    """守卫的自守卫（不误报的那一半）：`corrections.py` 写的 `payload["secret"]`
    是一份**窄引用**（`{id,label,name}`），它必须原样留下。

    键名叫 `secret` 的东西一律清空的写法在这条上会红——那种网会把「改的是哪个秘密」
    一起收窄掉，而作者正是照着那个名字去找那一格的。
    """
    pid = book["pid"]
    client.post(
        f"/api/projects/{pid}/declare/knows",
        json={"who": "萧决", "secret": "血脉秘密", "quote": QUOTE},
    )
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "他以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert flipped.status_code == 200, flipped.text
    detail = client.get(f"/api/projects/{pid}/activity/{flipped.json()['decision_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["payload"]["secret"] == {
        "id": book["血脉秘密"],
        "label": "Secret",
        "name": "血脉秘密",
    }


def test_narrow_payload_empties_the_secret_extension_row() -> None:
    """单测那两条新规则：扩展行整份清空、窄引用一个字不动。"""
    detail = SecretDetail(description=SECRET_TEXT).model_dump(mode="json")
    narrow_ref = {"id": "s", "label": "Secret", "name": "血脉秘密"}

    assert activity.narrow_payload({"secret": detail}) == {"secret": {}}
    assert activity.narrow_payload({"secret": narrow_ref}) == {"secret": narrow_ref}
    # 顶层就是一份扩展行的写入方也有（`payload=spec.secret.model_dump()`）。
    assert activity.narrow_payload(detail) == {}


def test_the_call_audit_records_what_it_cost_not_what_it_said(
    client: TestClient, book: dict[str, str]
) -> None:
    """`model_call` 那一层：它记的是「花了多少」，**不许记「说了什么」**。

    起草的 prompt 里装着 `must_not_reveal` / `forbidden_entities`（铁律 5 转译过的那份），
    模型出参里装着正文——任何一样落进这张表，日志页就会把整份起草上下文摊出来。
    这里拿一份**带毒的 prompt + 带毒的出参**跑真的 `record_call`，再把那一行的每一列
    和它的日志详情一起搜。
    """
    pid = book["pid"]
    prompt = f"不许写出：{TWIST}".encode("utf-8")
    output = f"模型说了：{PLOT_NOTE}"
    conn = connect(book["db"])
    try:
        call_id = record_call(
            conn,
            project_id=pid,
            capability="writer",
            model="deepseek-v4",
            finish_reason="stop",
            schema_version="m4.analysis.v1",
            prompt_hash="ph",
            prompt_bytes=prompt,
            text=output,
            prompt_tokens=1200,
            completion_tokens=400,
            cache_read_tokens=960,
            cache_write_tokens=None,
            elapsed_ms=900,
            call_id_factory=lambda project_id: new_id(EntityType.CALL, project_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM model_call WHERE id = ?", (call_id,)).fetchone()
        stored = " ".join("" if value is None else str(value) for value in tuple(row))
    finally:
        conn.close()

    # 自守卫：毒真的进过入参和出参，否则下面两条搜的是空气。
    assert TWIST in prompt.decode("utf-8") and PLOT_NOTE in output
    # 落库这一侧：整行拼起来一个字都不许有。
    assert TWIST not in stored and PLOT_NOTE not in stored
    # 不漏的**理由**是内容寻址，不是「这次刚好没存」——把理由本身也钉住：
    # 入参和出参各留下一个 `artifact:sha256:…`，存的是指纹不是原文。
    assert stored.count("artifact:sha256:") == 2

    detail = client.get(f"/api/projects/{pid}/activity/{call_id}")
    assert detail.status_code == 200, detail.text
    assert TWIST not in detail.text and PLOT_NOTE not in detail.text


# ══════════════════════════════════════════════════════════════════════════
# 3. actor
# ══════════════════════════════════════════════════════════════════════════


def test_actor_filter_separates_the_author_from_the_system(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """ADR 0020 的原话：作者点过的那 30 次会被淹没在几千条 system 行里。"""
    pid = seeded["pid"]
    author = _entries(client, pid, actor="author", limit=200)
    system = _entries(client, pid, actor="system", limit=200)

    assert {e["actor"] for e in author} == {"author"}
    assert {e["actor"] for e in system} == {"system"}
    # 抽取运行和模型调用没有 actor 列——它们按定义是系统跑的，只能出现在 system 那一侧。
    assert {e["source"] for e in system} >= {"extraction", "model_call", "decision"}
    assert {e["source"] for e in author} == {"decision"}
    assert seeded["system_decision_id"] in {e["id"] for e in system}


def test_the_actor_tally_ignores_the_current_filter(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """计数跟着过滤一起变就什么都说明不了——它要回答的正是「我筛掉了多少」。"""
    pid = seeded["pid"]
    unfiltered = client.get(f"/api/projects/{pid}/activity").json()["actors"]
    filtered = client.get(f"/api/projects/{pid}/activity", params={"actor": "author"}).json()
    assert filtered["actors"] == unfiltered
    tally = {row["actor"]: row["count"] for row in unfiltered}
    assert tally["author"] > 0 and tally["system"] > 0
    # system 侧要算上另外两张表，否则这个数会小于时间线上真的能看见的行数。
    assert tally["system"] >= 3


# ══════════════════════════════════════════════════════════════════════════
# 4. 两层出参 / 零带着理由
# ══════════════════════════════════════════════════════════════════════════


def test_the_collapsed_row_answers_the_four_questions(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """折叠行 = 什么时候、什么东西、成没成、谁干的。**payload 不在这一层。**"""
    entry = _by_id(_entries(client, seeded["pid"], limit=200), seeded["run_id"])
    assert entry["ts"] and entry["title"] == "第 1 章抽取"
    assert entry["status"] == "succeeded" and entry["actor"] == "system"
    assert entry["subtitle"] == "有效事件 3 条 · 丢弃 1 条 · 待审提案 0 条"
    assert "payload" not in entry and "rows" not in entry


def test_the_expanded_detail_carries_what_ran_and_what_it_cost(
    client: TestClient, seeded: dict[str, str]
) -> None:
    pid = seeded["pid"]
    r = client.get(f"/api/projects/{pid}/activity/{seeded['run_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    labels = {row["label"]: row["value"] for row in body["rows"]}
    assert labels["章节"] == "第 1 章" and labels["有效事件"] == "3"
    assert labels["待审提案"] == "0" and labels["丢弃事件"] == "1"
    assert body["cost"]["call_id"] == seeded["call_id"]
    assert body["cost"]["tokens_in"] == 1200
    assert body["entry"]["id"] == seeded["run_id"]
    # **2026-08-11 起这张表上没有快照 id / schema 版本 / prompt 指纹了。**
    # 它们对小说作者是零信息量的内部编号，而这一页是他点进两个编辑入口的门厅。
    assert "抽取 schema" not in labels and "正文快照" not in labels
    assert "prompt 指纹" not in labels


def test_a_failed_run_shows_why(client: TestClient, book: dict[str, str]) -> None:
    """失败的原因说成人话 —— **码和那句英文诊断一个字都不跟出来**。

    码走 `activity._RUN_ERROR_LABEL`（措辞全在后端），`message` 是写给维护者的，
    库就在他手上。判据的完整性在 `tests/test_wording_guard.py`（枚举驱动）。
    """
    run_id = seed_run(book, 1, status="FAILED")
    entry = _by_id(_entries(client, book["pid"], limit=200), run_id)
    assert entry["status"] == "failed" and "没能连上你配置的模型服务" in entry["subtitle"]
    detail = client.get(f"/api/projects/{book['pid']}/activity/{run_id}").json()
    assert detail["errors"] == ["没能连上你配置的模型服务"]


def test_the_summary_call_says_what_it_actually_summarized(
    client: TestClient, book: dict[str, str]
) -> None:
    """展开一条「章节总结」，作者要看得见**买到了什么**。

    在这之前那一层只有能力 / 模型 / token / 耗时：花了钱这件事说得清清楚楚，
    花出来的东西一个字都没有。正文走**投影层**（`rows`，措辞和别的行同一种形状），
    不是把 `payload` 那个审计信封摊开——那是给机器重放用的。
    """
    pid = book["pid"]
    text = "萧决在青云城主府第一次听说了血脉秘密。"
    call_id = seed_call(book, capability="summarizer")
    seed_summary(book, 1, call_id=call_id, text=text)

    detail = client.get(f"/api/projects/{pid}/activity/{call_id}").json()
    labels = {row["label"]: row["value"] for row in detail["rows"]}
    assert labels["这次写出来的总结"] == text

    # 作者后来撤回了它，这一行照旧说得出**当时**买到的是什么：日志是账本，
    # 不是「现在算数的那一段」（那一段在右栏，跳过去就是）。
    assert client.delete(f"/api/projects/{pid}/chapters/1/summary").status_code == 200
    after = client.get(f"/api/projects/{pid}/activity/{call_id}").json()
    assert {row["label"]: row["value"] for row in after["rows"]}["这次写出来的总结"] == text


def test_a_call_that_wrote_no_summary_gets_no_empty_row_for_it(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """抽取那次调用**不多一行永远「未记录」**：那不是一个缺失的值，
    是这一档调用本来就没有这个字段（同 `_cache_text` 里「报了才说」的取舍）。"""
    detail = client.get(
        f"/api/projects/{seeded['pid']}/activity/{seeded['call_id']}"
    ).json()
    assert all(row["label"] != "这次写出来的总结" for row in detail["rows"])


def test_unrecorded_tokens_say_so_instead_of_rendering_zero(
    client: TestClient, book: dict[str, str]
) -> None:
    """§10 约束 8：静默的零和真的零不许长得一样。"""
    call_id = seed_call(book, tokens_in=None)
    entry = _by_id(_entries(client, book["pid"], limit=200), call_id)
    assert "入 未记录 /" in entry["subtitle"]
    detail = client.get(f"/api/projects/{book['pid']}/activity/{call_id}").json()
    assert detail["cost"]["tokens_in"] is None


def test_a_model_call_says_which_chapter_it_was_spent_on(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """`model_call` 没有章号列。不反查的话这一行是「一次调用，1200 token」——
    作者看得见花了钱、看不见花在哪儿。"""
    entry = _by_id(_entries(client, seeded["pid"], limit=200), seeded["call_id"])
    assert entry["chapter_number"] == 1
    assert entry["jump"]["target"] == "chapter" and entry["jump"]["chapter_number"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 5. /runs：不再是一条骗人的 501
# ══════════════════════════════════════════════════════════════════════════


def test_runs_is_a_real_endpoint_now(client: TestClient, seeded: dict[str, str]) -> None:
    r = client.get(f"/api/projects/{seeded['pid']}/runs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_count"] == 1
    assert [e["id"] for e in body["entries"]] == [seeded["run_id"]]
    assert body["totals"]["calls"] == 1 and body["totals"]["tokens_in"] == 1200


def test_cost_is_null_not_zero_when_nobody_recorded_a_price(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """`model_call.cost` 至今没有写入方（BYOK 之下引擎不知道单价）。
    一张写着 0 元的账单就是本仓库反复在修的那种「漂亮的空结果」。"""
    totals = client.get(f"/api/projects/{seeded['pid']}/runs").json()["totals"]
    assert totals["priced_calls"] == 0
    assert totals["cost"] is None


def test_a_priced_call_does_get_summed(client: TestClient, book: dict[str, str]) -> None:
    """真填了单价的话它要加得对——否则上面那条 None 可能只是求和写错了。"""
    seed_call(book, cost=0.25)
    seed_call(book, cost=0.75)
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["priced_calls"] == 2
    assert totals["cost"] == pytest.approx(1.0)


def test_a_call_the_vendor_never_metered_is_not_worth_zero_tokens(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这是这一刀修的那条病。** 供应商不报 usage ⇒ 那一行是 NULL ⇒ 汇总必须说
    「不知道」，不是「0」。

    它 2026-08-12 之前是 `COALESCE(SUM(tokens_in), 0)`，于是作者按 README 的默认配置
    （DeepSeek 两条路由都不是 `supports_stream_usage is True`）写书、真花着钱，
    底栏却写着「读入 0 token」。**同一条用量条上 `cost` 那一格早就做对了**，
    两种口径并排摆着。
    """
    seed_call(book, tokens_in=None, tokens_out=None)
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == 1
    assert totals["metered_calls"] == 0, "两个 token 数都是 NULL 的一行不算「报了」"
    assert totals["tokens_in"] is None and totals["tokens_out"] is None


def test_half_a_measurement_still_counts_as_measured(
    client: TestClient, book: dict[str, str]
) -> None:
    """只给了一个数的那一行**算「报了」**，而少掉的那半仍然说「不知道」。

    判据写死在 SQL 里（`tokens_in IS NULL AND tokens_out IS NULL` 才算没报），
    所以 `metered_calls` 回答的是「有几次一个数都没给」，不是「有几次给全了」。
    这一档今天没有已知的生产者（两个数一起来自同一个 usage 对象），
    钉住它是因为**判据一旦反过来，屏幕上那句「另有 N 次没报」会数错**。
    """
    seed_call(book, tokens_in=1_000, tokens_out=None)
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == 1 and totals["metered_calls"] == 1
    assert totals["tokens_in"] == 1_000 and totals["tokens_out"] is None


def test_the_ones_that_did_report_still_get_summed(
    client: TestClient, book: dict[str, str]
) -> None:
    """**另一个方向的假话也不许说。** 90 次报了、10 次没报时，那 90 次的合计是真信息。

    整个改成「不知道」和把 NULL 折成 0 是同一种病的两头，所以出参是
    「已知的那些的合计 + 有几次没报」，两个数一起才是一句真话。
    """
    seed_call(book, tokens_in=1_000)
    seed_call(book, tokens_in=200)
    seed_call(book, tokens_in=None, tokens_out=None)
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == 3 and totals["metered_calls"] == 2
    # 报了的那两行 tokens_out 都是默认的 400。
    assert totals["tokens_in"] == 1_200 and totals["tokens_out"] == 800


def test_a_book_where_every_call_was_metered_says_so(
    client: TestClient, book: dict[str, str]
) -> None:
    """全报了那一档：`metered_calls == calls`，界面照此不说「另有几次没报」。"""
    seed_call(book, tokens_in=1_000)
    seed_call(book, tokens_in=200)
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == totals["metered_calls"] == 2
    assert totals["tokens_in"] == 1_200


def test_an_empty_book_owes_no_numbers_at_all(client: TestClient, book: dict[str, str]) -> None:
    """一次都没调过 ⇒ 四个可空的数全是 None，两个计数全是 0。

    **`calls == 0` 时说「读入 0 token」在字面上碰巧不假，但它和「问了没人答」
    长得一模一样**——两种情形指向的动作完全不同（去用一下 / 去查端点报不报 usage），
    所以这一档也走同一条口径，不给它开特例。
    """
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == 0 and totals["metered_calls"] == 0
    assert totals["tokens_in"] is None and totals["tokens_out"] is None
    assert totals["ms"] is None and totals["cost"] is None


def test_the_stopwatch_is_ours_so_a_real_call_always_carries_it(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ms` 为什么没有自己的计数：它是**我们自己掐的表**，不是供应商报的。

    走真 `record_call`（`elapsed_ms: int` 非空，全库唯一写入口）落一行**不报 usage**
    的账：token 那两个是 None，耗时照样是个数。**这就是那个不对称的证据**——
    哪天真长出一个不掐表的写入方，这条会红，那时才该给 `ms` 补一个计数。
    """
    import novel_harness.api.deps as deps_mod
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        deps_mod,
        "complete",
        lambda messages, *, config=None, plan=None, client=None: CompletionResult(
            text="萧决在青云城主府听说了血脉秘密。", model="deepseek-v4-flash", finish_reason="stop"
        ),
    )
    made = client.post(f"/api/projects/{book['pid']}/chapters/1/summary")
    assert made.status_code == 200, made.text
    totals = client.get(f"/api/projects/{book['pid']}/runs").json()["totals"]
    assert totals["calls"] == 1 and totals["metered_calls"] == 0
    assert totals["tokens_in"] is None, "桩没给 token 数，那就是 NULL"
    assert isinstance(totals["ms"], int), "耗时是我们自己掐的，不该跟着 token 一起消失"


_STALE_EMPTY = re.compile(r"`?model_call`?\s*(?:表)?\s*(?:今天是空的|空)")
_QUOTED = re.compile(r"「[^」]*」")

CLAIM_FILES = (
    "src/novel_harness/api/app.py",
    "docs/UI_ARCHITECTURE.md",
    "docs/ARCHITECTURE.md",
)


def _asserts_the_table_is_empty(line: str) -> bool:
    """这一行是在**断言**「model_call 是空的」，还是在引用这段病史？

    豁免精确到**这次命中本身**落在 `「…」` 里（照抄
    `test_doc_numbers._asserts_only_r4` 的判据）：整行有引号就放过的写法漏过一半，
    而**一条会漏的守卫比没有守卫更坏**——它让人以为这件事有人管着。
    """
    spans = [m.span() for m in _QUOTED.finditer(line)]
    return any(
        not any(lo < hit.start() and hit.end() <= hi for lo, hi in spans)
        for hit in _STALE_EMPTY.finditer(line)
    )


def test_nothing_still_claims_the_model_call_table_is_empty() -> None:
    """那句话在 M4 落地那天就过期了，而它躺在一条 501 的 docstring + 端点清单里。

    **这条不是防御性编程，是补一次真实的过期**：抽取和滚动总结从 M4 起就在
    `model_call` 上记账，而 `GET /runs` 一直挂着一条写着「表今天是空的」的 501。
    """
    root = Path(__file__).resolve().parents[1]
    offenders = [
        f"{rel}:{lineno}: {line.strip()[:80]}"
        for rel in CLAIM_FILES
        for lineno, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1)
        if _asserts_the_table_is_empty(line)
    ]
    assert not offenders, (
        "这些地方还在断言 model_call 是空的：\n  "
        + "\n  ".join(offenders)
        + "\n讲病史请把那句话包进「」——那读起来就是引用，不是断言。"
    )


def test_that_guard_can_tell_a_quote_from_a_claim() -> None:
    """守卫的自守卫：引用旧结论不许被咬，断言它必须被咬。"""
    assert _asserts_the_table_is_empty("| GET | `/runs` | —（`model_call` 空） | 501 |")
    assert not _asserts_the_table_is_empty("理由写着「`model_call` 表今天是空的」，那句已过期")


# ══════════════════════════════════════════════════════════════════════════
# 6. 分页 / 404
# ══════════════════════════════════════════════════════════════════════════


def test_the_cursor_walks_every_row_exactly_once(
    client: TestClient, seeded: dict[str, str]
) -> None:
    """跨三张表的归并分页：每一行恰好出现一次，一条不漏一条不重。"""
    pid = seeded["pid"]
    everything = [e["id"] for e in _entries(client, pid, limit=200)]
    assert len(everything) > 3, "行数太少，翻页翻不出问题"

    walked: list[str] = []
    cursor: str | None = None
    for _ in range(len(everything) + 5):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get(f"/api/projects/{pid}/activity", params=params).json()
        walked.extend(e["id"] for e in page["entries"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert cursor is None, "翻不到底 —— next_cursor 一直不为 None"
    assert walked == everything


def test_a_broken_cursor_is_422_not_a_silent_first_page(
    client: TestClient, book: dict[str, str]
) -> None:
    """静默回到第一页 = 作者只会看到「我怎么翻不动」，而没有任何东西报错。"""
    r = client.get(f"/api/projects/{book['pid']}/activity", params={"cursor": "乱写的"})
    assert r.status_code == 422, r.text


def test_detail_hides_unknown_and_cross_project_entries(
    client: TestClient, seeded: dict[str, str]
) -> None:
    pid = seeded["pid"]
    assert client.get(f"/api/projects/{pid}/activity/nonsense").status_code == 404
    assert client.get(f"/api/projects/{pid}/activity/decision:zzz:1").status_code == 404
    # 换一本书去要同一条：id 存在，但不属于那本书。
    other = client.post("/api/projects", json={"name": "另一本"}).json()["id"]
    assert client.get(f"/api/projects/{other}/activity/{seeded['run_id']}").status_code == 404


def test_an_empty_book_is_an_empty_page_not_an_error(client: TestClient) -> None:
    other = client.post("/api/projects", json={"name": "空书"}).json()["id"]
    body = client.get(f"/api/projects/{other}/activity").json()
    assert body == {"entries": [], "next_cursor": None, "actors": []}
