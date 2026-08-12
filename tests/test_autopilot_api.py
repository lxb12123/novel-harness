"""换章即后台（`POST/GET …/chapters/{n}/autopilot`）。

这一层唯一的新东西是**没人在看的时候跑**，所以这份测试的重心不在「跑得通」，而在
「跑不通的时候说不说得出来」：模型没配好 / 章没正文 / 抽取炸了 / 后台总结炸了，
四种都必须在出参里分得开，且都不许长成同一个「什么都没有」（ARCHITECTURE §10 约束 8）。

**触发点是换章不是保存**，所以这里也钉住「不重复付费」：同一章连打两次，
模型调用次数不变。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from novel_harness import db, importer, project
from novel_harness.api.autopilot import JOBS, router
from novel_harness.draft.provider import CompletionResult, ProviderError
from novel_harness.extract.models import RawChapterAnalysis, RawEvent
from novel_harness.graph.sqlite_store import SqliteStoryGraph

BOOK_TXT = """第一章 起

萧决推开门，屋里没有点灯。

第二章 承

夜色沉下来，青云城主府的灯一盏盏亮起。
"""

SUMMARY_TEXT = "萧决进屋，没点灯。"


def _analysis_json() -> str:
    return RawChapterAnalysis(
        events=(
            RawEvent(
                summary="萧决推开门。",
                quote="萧决推开门，屋里没有点灯。",
                participants=(),
                knowers=(),
                revealed_facts=(),
                confidence=0.9,
            ),
        ),
        state_updates=(),
        character_profiles=(),
    ).model_dump_json()


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    dbp = tmp_path / "book.db"
    conn = db.connect(dbp)
    db.migrate(conn)
    root = tmp_path / "书"
    pid = project.create(conn, name="测试书", root_path=str(root)).id
    other = project.create(conn, name="别册", root_path=str(tmp_path / "别册")).id
    store = SqliteStoryGraph(conn)
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK_TXT, encoding="utf-8")
    importer.import_book(store, pid, txt=txt, root=root)
    conn.commit()
    conn.close()
    return {"db": str(dbp), "pid": pid, "other": other}


@pytest.fixture
def client(
    book: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """**独立 app，只挂 autopilot 那一个 router。**

    不往 `api/app.py` 那个全局单例上 `include_router`：那会在导入顺序里偷偷改掉
    `tests/test_doc_numbers.py` 数出来的路由条数——一份测试污染另一份。
    """
    monkeypatch.setenv("NH_DB", book["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    JOBS.clear()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c
    JOBS.clear()


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 BYOK 三件套配好（冻结档，`M2_ENDPOINT_PROFILE.md`）——**只配置，不发请求**。"""
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


def _stub_model(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """只把**模型**换成桩：库、幂等键、审计写入、后台调度全部走真代码。

    返回一份调用流水 —— 「不重复付费」这条断言靠数它。
    """
    import novel_harness.api.deps as deps_mod

    calls: list[str] = []

    def summary(request: Any) -> CompletionResult:
        calls.append("summary")
        return CompletionResult(text=SUMMARY_TEXT, model="fake", finish_reason="stop")

    def extraction(request: Any) -> CompletionResult:
        calls.append("extraction")
        return CompletionResult(text=_analysis_json(), model="fake", finish_reason="stop")

    monkeypatch.setattr(deps_mod, "_analyze_summary", summary)
    monkeypatch.setattr(deps_mod, "_analyze_extraction", extraction)
    return calls


def _post(client: TestClient, book: dict[str, str], chapter: int) -> dict[str, Any]:
    response = client.post(f"/api/projects/{book['pid']}/chapters/{chapter}/autopilot")
    assert response.status_code == 202, response.text
    return response.json()


def _get(client: TestClient, book: dict[str, str], chapter: int) -> dict[str, Any]:
    response = client.get(f"/api/projects/{book['pid']}/chapters/{chapter}/autopilot")
    assert response.status_code == 200, response.text
    return response.json()


# ── 正路 ────────────────────────────────────────────────────────────────────


def test_leaving_a_written_chapter_queues_both_jobs(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    calls = _stub_model(monkeypatch)

    body = _post(client, book, 1)

    assert body["chapter"] == 1
    assert body["summary"] == "queued"
    assert body["extraction"] == "queued"
    assert body["extraction_run_id"].startswith("extraction_run:")
    assert body["errors"] == []
    # TestClient 在响应返回前把 BackgroundTasks 跑完，所以此刻两件事都已落地。
    assert sorted(calls) == ["extraction", "summary"]

    status = _get(client, book, 1)
    assert status["summary_ready"] is True
    assert status["extraction_ready"] is True
    assert status["running"] is False
    assert (status["summary_state"], status["extraction_state"]) == ("ready", "ready")
    assert status["errors"] == []


def test_second_pass_over_the_same_chapter_pays_nothing(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """作者来回切章是常态。**幂等不是加分项，是这条链能不能开的前提。**"""
    _configure(monkeypatch)
    calls = _stub_model(monkeypatch)

    _post(client, book, 1)
    assert len(calls) == 2

    again = _post(client, book, 1)

    assert again["summary"] == "skipped"
    assert again["extraction"] == "skipped"
    assert len(calls) == 2, "换回来一次就又付了一次费"


def test_the_summary_really_lands_in_the_store(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """后台跑完 ≠ 写进去了。这条把「跑了」和「有结果」分开验。"""
    _configure(monkeypatch)
    _stub_model(monkeypatch)
    _post(client, book, 2)

    from novel_harness.draft.rolling_summary import SummaryStore

    conn = db.connect(Path(book["db"]))
    try:
        stored = SummaryStore(conn).get(book["pid"], 2)
    finally:
        conn.close()
    assert stored is not None and stored.summary == SUMMARY_TEXT


# ── 四种「什么都没发生」，每一种都得说得出为什么 ──────────────────────────────


def test_a_chapter_without_text_says_no_text_not_missing(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 9 章根本没写。**「还没写」和「写了但没总结」是两件事**，合并显示 = 催作者去

    总结一个不存在的东西（同 `/summaries` 那条既有纪律）。
    """
    _configure(monkeypatch)
    calls = _stub_model(monkeypatch)

    body = _post(client, book, 9)

    assert (body["summary"], body["extraction"]) == ("no_text", "no_text")
    assert body["extraction_run_id"] is None
    assert calls == []

    status = _get(client, book, 9)
    assert (status["summary_state"], status["extraction_state"]) == ("no_text", "no_text")
    assert status["summary_ready"] is False and status["extraction_ready"] is False


def test_an_unconfigured_model_is_reported_not_a_4xx_and_not_silence(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配钥匙时**照旧返 202**：这是换章时无人值守打的端点，弹 4xx = 每换一章骂一次。

    但也**不许静默**——两个字段都说 `unconfigured`，`errors` 里给的是作者照着能做的话。
    """
    calls = _stub_model(monkeypatch)

    body = _post(client, book, 1)

    assert (body["summary"], body["extraction"]) == ("unconfigured", "unconfigured")
    assert body["extraction_run_id"] is None
    assert calls == [], "没配模型却已经在调它了"
    assert [e["stage"] for e in body["errors"]] == ["model"]
    assert "AI 设置" in body["errors"][0]["message"]

    status = _get(client, book, 1)
    assert (status["summary_state"], status["extraction_state"]) == (
        "unconfigured",
        "unconfigured",
    )
    assert "AI 设置" in status["errors"][-1]["message"]


def test_a_failed_background_summary_is_visible_in_the_status(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chapter_summary` 只记成功，所以后台炸掉在库里一个字节都没有——

    没有进程内留痕的话，GET 会说「还没生成」，作者永远不知道自己少喂了什么给写作模型。
    """
    _configure(monkeypatch)
    import novel_harness.api.deps as deps_mod

    def boom(request: Any) -> CompletionResult:
        raise ProviderError("connection refused")

    monkeypatch.setattr(deps_mod, "_analyze_summary", boom)
    monkeypatch.setattr(
        deps_mod,
        "_analyze_extraction",
        lambda request: CompletionResult(text=_analysis_json(), model="fake"),
    )

    assert _post(client, book, 1)["summary"] == "queued"

    status = _get(client, book, 1)
    assert status["summary_ready"] is False
    assert status["summary_state"] == "failed"
    failure = [e for e in status["errors"] if e["stage"] == "summary"]
    assert failure and failure[0]["code"] == "provider_failure"
    assert "connection refused" in failure[0]["message"]


def test_a_summary_that_returns_empty_text_is_a_failure_not_a_blank(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    import novel_harness.api.deps as deps_mod

    monkeypatch.setattr(
        deps_mod,
        "_analyze_summary",
        lambda request: CompletionResult(text="   ", model="fake"),
    )
    monkeypatch.setattr(
        deps_mod,
        "_analyze_extraction",
        lambda request: CompletionResult(text=_analysis_json(), model="fake"),
    )

    _post(client, book, 1)

    status = _get(client, book, 1)
    assert status["summary_state"] == "failed"
    assert [e["code"] for e in status["errors"] if e["stage"] == "summary"] == ["empty_summary"]


def test_a_failed_extraction_is_reported_and_never_auto_retried(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动链路**不自动重试**：重试要再付一次钱，那得作者点（`?force=true` 在

    `/extract` 上）。所以第二次换章回来只会照实说「上次失败了」。
    """
    _configure(monkeypatch)
    import novel_harness.api.deps as deps_mod

    extraction_calls: list[int] = []

    def boom(request: Any) -> CompletionResult:
        extraction_calls.append(1)
        raise ProviderError("502 from provider")

    monkeypatch.setattr(deps_mod, "_analyze_extraction", boom)
    monkeypatch.setattr(
        deps_mod,
        "_analyze_summary",
        lambda request: CompletionResult(text=SUMMARY_TEXT, model="fake"),
    )

    first = _post(client, book, 1)
    assert first["extraction"] == "queued"
    assert len(extraction_calls) == 1

    status = _get(client, book, 1)
    assert status["extraction_ready"] is False
    assert status["extraction_state"] == "failed"
    assert [e["stage"] for e in status["errors"]] == ["extraction"]

    again = _post(client, book, 1)
    assert again["extraction"] == "failed"
    assert len(extraction_calls) == 1, "自动链路把作者的钱花在了一个已知会失败的调用上"
    assert [e["code"] for e in again["errors"]] == ["provider_failure"]
    # 精确追查的句柄仍在：run id 指得到 `/extractions/{run_id}` 那条真记录。
    assert again["extraction_run_id"] == first["extraction_run_id"]


# ── 状态端点自己不许有副作用 ────────────────────────────────────────────────


def test_status_does_not_enqueue_anything_or_spend_money(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """查一下不该变成做一件事：GET 若顺手 `enqueue`，一次轮询就会建出一条永远 PENDING

    的 run，而那条 run 会让 `running` 恒为真。
    """
    _configure(monkeypatch)
    calls = _stub_model(monkeypatch)

    status = _get(client, book, 1)

    assert (status["summary_state"], status["extraction_state"]) == ("missing", "missing")
    assert status["running"] is False
    assert calls == []

    # 没有留下任何 run：接着 POST 拿到的仍是一条新排的 PENDING。
    assert _post(client, book, 1)["extraction"] == "queued"


# ── 边界 ────────────────────────────────────────────────────────────────────


def test_unknown_project_is_404(client: TestClient) -> None:
    for call in (
        client.post("/api/projects/project:missing/chapters/1/autopilot"),
        client.get("/api/projects/project:missing/chapters/1/autopilot"),
    ):
        assert call.status_code == 404, call.text
        assert call.json()["detail"]["error"] == "project_not_found"


def test_chapter_zero_is_refused_by_the_path_gate(
    client: TestClient, book: dict[str, str]
) -> None:
    assert (
        client.post(f"/api/projects/{book['pid']}/chapters/0/autopilot").status_code == 422
    )
    assert client.get(f"/api/projects/{book['pid']}/chapters/0/autopilot").status_code == 422


def test_jobs_are_tracked_per_project(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """留痕的键是 (project_id, chapter)。同一台服务上开着两本书，第 1 章不是同一章。"""
    _configure(monkeypatch)
    _stub_model(monkeypatch)
    _post(client, book, 1)

    other = client.get(f"/api/projects/{book['other']}/chapters/1/autopilot")
    assert other.status_code == 200, other.text
    assert other.json()["summary_state"] == "no_text"
