"""「这一次调用是为哪一章花的」——`model_call.chapter_number`（迁移 009）+ 反查兜底。

这份文件钉四件事：

1. **起草那条路真的记账了。** 在这一刀之前 `/draft` 一行 `model_call` 都不写，
   于是**作者花钱最多的动作在账上是零，而且看起来像全部**（`docs_dev` 的「已知限制」
   第一条）。它同时是唯一一条**没有业务表可反查**的路——章号只能由这一列答。
2. **旧行的章号不许消失。** 作者库里已经躺着上百行这一列是 `NULL` 的账。
   `_call_chapter` 直接改成只读这一列的话，它们的章号会当场消失**而且不报错**——
   本仓栽过五次的那种病（零和不知道糊成一个）的第六种长相。
3. **填不出来就留空。** 不许拿「作者此刻停在第几章」冒充「这一次为哪一章花的」：
   起草工具起的是 `DraftAsk.chapter` 那一章，两者可以不是同一个数。
4. **那个默认值别再扩散。** `record_call(chapter_number=...)` 是全库唯一写入口上
   唯一一个有默认值的「事实」参数，它是一条欠账（写作助手那一档还没接），
   不是一个可以照抄的形状。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_activity import seed_call, seed_run

from novel_harness import activity
from novel_harness.db import connect
from novel_harness.ids import EntityType, new_id

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

ZH_LENGTH = {"language": "zh", "min_units": 2_000, "target_units": 2_500, "max_units": 3_000}


# ══════════════════════════════════════════════════════════════════════════
# 一、起草那条路：账在，而且答得出为哪一章
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def configured(client: TestClient) -> None:
    reply = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
        },
    )
    assert reply.status_code == 200, reply.text


@pytest.fixture
def settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _calls(book: dict[str, str]) -> list[dict[str, Any]]:
    conn = connect(book["db"])
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, capability, chapter_number, tokens_in FROM model_call "
                "WHERE project_id = ? ORDER BY rowid",
                (book["pid"],),
            )
        ]
    finally:
        conn.close()


def _draft(client: TestClient, book: dict[str, str], chapter: int) -> Any:
    """打一次 `/draft`。**2026-08-26 起那条路只有行内续写**（整章那个入口零调用方，删了）。

    这个文件量的是「一次调用 = 一行账 = 一个章号」，**和是哪种模式无关**——
    换请求体不换性质。"""
    return client.post(
        f"/api/projects/{book['pid']}/chapters/{chapter}/draft",
        json={"previous_tail": "夜色沉下来，城主府的灯一盏盏亮起。", "length": ZH_LENGTH},
    )


def test_one_draft_is_one_line_on_the_bill_with_its_chapter(
    client: TestClient,
    book: dict[str, str],
    settings_path: None,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次起草 = 一行账 = 一个章号。**章号来自这一列，不是反查**：
    起草没有 `extraction_run` / `chapter_summary` 那样一张指回来的业务表。"""
    import novel_harness.draft.generate as generate
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        generate,
        "complete",
        lambda messages, *, config, plan, client=None: CompletionResult(
            text="字" * 2_400, model="fake", finish_reason="stop", prompt_tokens=1_111
        ),
    )

    assert _draft(client, book, 2).status_code == 200
    rows = _calls(book)
    assert [(row["capability"], row["chapter_number"]) for row in rows] == [("writer", 2)]
    # 供应商报了多少就记多少（账本只照抄）。
    assert rows[0]["tokens_in"] == 1_111

    detail = client.get(f"/api/projects/{book['pid']}/activity/{rows[0]['id']}")
    assert detail.status_code == 200, detail.text
    values = {row["label"]: row["value"] for row in detail.json()["rows"]}
    assert values["为哪一章"] == "第 2 章"


def test_the_continuation_attempt_is_a_second_line_on_the_same_chapter(
    client: TestClient,
    book: dict[str, str],
    settings_path: None,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**一份回执 = 一次调用**（ADR 0011 D3：不够长会续写一次）。两次都是同一章的钱。"""
    import novel_harness.draft.generate as generate
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        generate,
        "complete",
        lambda messages, *, config, plan, client=None: CompletionResult(
            text="字" * 20, model="fake", finish_reason="stop"
        ),
    )

    assert _draft(client, book, 1).status_code == 200
    assert [(row["capability"], row["chapter_number"]) for row in _calls(book)] == [
        ("writer", 1),
        ("writer", 1),
    ]


def test_the_first_attempt_is_billed_even_when_the_second_one_dies(
    client: TestClient,
    book: dict[str, str],
    settings_path: None,
    configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**钱先付出去，账后记** —— 所以账要一次一落，不能攒到整稿拼好。

    「第一次答上来了、续写那次断线」是一档真会发生的失败（ADR 0011 D3 的第二次调用）。
    攒到最后记的话，异常一抛 `ChapterDraft.calls` 就没了，而那次调用**已经花过钱**。
    这条测的是 `on_call` 那条线真的通着，不是「成功路径顺手记一下」。
    """
    import novel_harness.draft.generate as generate
    from novel_harness.draft.provider import CompletionResult, ProviderError

    seen = {"n": 0}

    def flaky(messages, *, config, plan, client=None):
        seen["n"] += 1
        if seen["n"] == 1:
            return CompletionResult(text="字" * 20, model="fake", finish_reason="length")
        raise ProviderError("端点断了")

    monkeypatch.setattr(generate, "complete", flaky)

    reply = _draft(client, book, 2)
    assert reply.status_code == 502, reply.text
    assert [(row["capability"], row["chapter_number"]) for row in _calls(book)] == [("writer", 2)]


# ══════════════════════════════════════════════════════════════════════════
# 二、旧行：这一列是 NULL，章号只有反查拿得到
# ══════════════════════════════════════════════════════════════════════════


def test_rows_written_before_this_column_existed_still_know_their_chapter(
    book: dict[str, str], client: TestClient
) -> None:
    """作者库里已有的那上百行账。**它们的 `chapter_number` 是 NULL**（`ADD COLUMN`
    没有 DEFAULT），章号只能从消费它的那张业务表反查——所以反查必须留着当兜底。

    这条测试的价值全在这儿：直接改成只读新列的话，它**不报错**，只是那些行的
    「为哪一章」一夜之间全变成「未记录」，而没有任何东西会红。
    """
    extractor_call = seed_call(book, capability="extractor")
    seed_run(book, 2, call_id=extractor_call)

    summarizer_call = seed_call(book, capability="summarizer")
    conn = connect(book["db"])
    try:
        chapter_id = conn.execute(
            "SELECT id FROM chapter WHERE project_id = ? AND number = 1",
            (book["pid"],),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO chapter_summary (
                id, project_id, chapter_id, chapter_number, summary, summary_sha256,
                schema_version, prompt_hash, model_call_id
            ) VALUES (?, ?, ?, 1, '第一章讲了什么', ?, 'nh.summary.v1', 'ph', ?)
            """,
            (
                new_id(EntityType.SUMMARY, book["pid"]),
                book["pid"],
                chapter_id,
                sha256("第一章讲了什么".encode("utf-8")).hexdigest(),
                summarizer_call,
            ),
        )
        conn.commit()
        assert [
            row["chapter_number"]
            for row in conn.execute(
                "SELECT chapter_number FROM model_call WHERE id IN (?, ?)",
                (extractor_call, summarizer_call),
            )
        ] == [None, None], "这两行本该是旧形状（新列为空），不然这条测的是别的东西"
    finally:
        conn.close()

    for call_id, chapter in ((extractor_call, 2), (summarizer_call, 1)):
        detail = client.get(f"/api/projects/{book['pid']}/activity/{call_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        values = {row["label"]: row["value"] for row in body["rows"]}
        assert values["为哪一章"] == f"第 {chapter} 章"
        assert body["entry"]["chapter_number"] == chapter
        assert body["entry"]["jump"]["chapter_number"] == chapter


def test_a_call_that_belongs_to_no_chapter_stays_unrecorded(
    book: dict[str, str], client: TestClient
) -> None:
    """两条路都答不上来 ⇒ **「未记录」，不是第 0 章、也不是随便挑一章**。

    §10 约束 8 的老规矩：零和空要带着理由。这一档在真库里就是写作助手那种
    「跟作者聊了两句、没为哪一章花钱」的调用。
    """
    orphan = seed_call(book, capability="agent")
    detail = client.get(f"/api/projects/{book['pid']}/activity/{orphan}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert {row["label"]: row["value"] for row in body["rows"]}["为哪一章"] == "未记录"
    assert body["entry"]["chapter_number"] is None
    assert body["entry"]["jump"] is None


def test_the_new_column_wins_over_the_reverse_lookup(book: dict[str, str]) -> None:
    """两条路都答得出时，以账自己那一列为准。

    新行两边一定相等（同一个 `chapter` 变量写进去的），所以这里量的不是「哪个对」，
    是**顺序**：反查是兜底，不是主路径——将来退掉反查时，先退的是这一半。
    """
    call_id = seed_call(book, capability="extractor")
    conn = connect(book["db"])
    try:
        conn.execute("UPDATE model_call SET chapter_number = 7 WHERE id = ?", (call_id,))
        conn.commit()
        seed_run(book, 2, call_id=call_id)
        detail = activity.read_entry(conn, book["pid"], call_id)
        assert detail is not None
        assert {row.label: row.value for row in detail.rows}["为哪一章"] == "第 7 章"
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 三、那个默认值只许缩，不许长
# ══════════════════════════════════════════════════════════════════════════

SILENT_LEDGERS_TODAY: frozenset[str] = frozenset({"api/chat.py"})
"""今天**没有**把章号说出来的记账点。**这是一份欠账清单，不是一份豁免清单。**

`api/chat.py::_ledger`（写作助手那一档）在这一刀里属于另一个 agent 的文件，
所以它暂时留在这儿。填它的时候要注意：那一层只知道**作者此刻停在第几章**，
而起草工具花的钱属于 `DraftAsk.chapter` 那一章——两者可以不同，
照会话那个数填等于编一个（`009_call_chapter.sql` 的最后一段）。
"""


def _ledger_call_sites() -> list[tuple[str, int, bool]]:
    """`src/` 里每一处 `record_call(` / `record_receipt(`：`(相对路径, 行号, 说没说章号)`。"""
    found: list[tuple[str, int, bool]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in {"record_call", "record_receipt"}:
                continue
            said = any(kw.arg == "chapter_number" for kw in node.keywords)
            found.append((str(path.relative_to(SRC)), node.lineno, said))
    return found


def test_no_new_silent_ledger() -> None:
    """新长出来的记账点必须把章号说出来（哪怕说的是「不知道」：显式 `None`）。

    ── 为什么是「子集」而不是「相等」 ────────────────────────────────────
    相等的话，另一个 agent **把欠账补上**的那一刻这条会红——一条在别人做对事情时
    亮红灯的守卫会被人删掉。子集只在**新增**一个不说话的记账点时红，那正是要拦的。
    """
    sites = _ledger_call_sites()
    assert sites, "一处 `record_call` 都没扫到 —— 这条守卫在空转"
    silent = {path for path, _line, said in sites if not said}
    extra = silent - SILENT_LEDGERS_TODAY
    assert not extra, (
        f"这些地方记了一笔账却没说是为哪一章：{sorted(extra)}。"
        "章号填不出来也要显式传 `chapter_number=None` —— 默认值是一条欠账，不是形状。"
    )


def test_the_guard_can_actually_see_a_silent_ledger(tmp_path: Path) -> None:
    """守卫的自守卫：拿一段**故意不说章号**的源码喂给同一个判据，它必须认出来。

    没有这一条的话，`_ledger_call_sites` 哪天因为 AST 形状变了而一个都扫不到，
    上面那条会安静地全绿——这个仓库栽过这种空转（`FakeEvents` 比真库宽那次）。
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "record_call(conn, project_id=pid, capability='writer')\n"
        "record_call(conn, project_id=pid, chapter_number=None)\n",
        encoding="utf-8",
    )
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    said = [
        any(kw.arg == "chapter_number" for kw in node.keywords)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "record_call"
    ]
    assert said == [False, True]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """这份文件里没有一条测试该真的连出去。桩没打上就当场炸，别静默走真路径。"""
    import novel_harness.draft.provider as provider

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("测试要发真请求了 —— 有一处桩没打上")

    monkeypatch.setattr(provider, "_build_client", refuse)
    yield
