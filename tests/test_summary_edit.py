"""作者改得动滚动总结（迁移 013）：改 / 撤回 / 撤回之后重来。

滚动总结**真的在花作者的钱、真的在影响每一稿**（`draft/product_assemble.py` 把它们一行行
拼进起草 prompt），而 2026-08-13 之前他在整个工作台里看不见它、改不了它、删不掉它。
这份文件钉住补上之后的三件事，每一件都对应一种**不报错的**坏结局：

1. **改和撤回都不删行**（005 那条纪律）。删了行，作者就再也拿不回模型写过什么。
2. **撤回真的把这一章从起草那边摘掉**。只改界面不改读端 = 屏幕上说撤了、prompt 里还在。
3. **撤回过的章不会被后台自动买回来**。那是一次他没按过的付费调用，顺带抹掉他刚做的动作。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness.db import Connection, connect, migrate
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import (
    AUTHOR_SUMMARY_MAX_CHARS,
    RollingSummarizer,
    SummaryChapterNotFound,
    SummaryOrigin,
    SummaryRequest,
    SummaryState,
    SummaryStore,
    SummaryTextRejected,
    retract_summary,
    save_author_summary,
)
from novel_harness.graph import ChapterSpec
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.project import create as create_project


QUOTE = "顾清音在渡口把玄铁令交给萧决，随即动身前往北荒。"
MACHINE_TEXT = "顾清音在渡口把玄铁令交给萧决，随后独自前往北荒。"
AUTHOR_TEXT = "玄铁令易手那一场：顾清音交出去的时候没说为什么，萧决也没问。"


@dataclass(frozen=True)
class Seed:
    path: Path
    project_id: str

    def connection(self) -> Connection:
        return connect(self.path)


@pytest.fixture
def seed(tmp_path: Path) -> Seed:
    path = tmp_path / "summary-edit.db"
    conn = connect(path)
    migrate(conn)
    project_id = create_project(conn, name="青云记", root_path=".").id
    graph = SqliteStoryGraph(conn)
    for number in (1, 2, 3):
        graph.put_chapter(
            ChapterSpec(
                project_id=project_id,
                number=number,
                heading=f"第{number}章",
                path=f"chapters/{number:04d}.md",
                text=QUOTE + "\n",
            )
        )
    conn.close()
    return Seed(path=path, project_id=project_id)


class Summarizer:
    """每次给一段**不一样**的文字：拿它能看出「第二次是不是真的又付了一次钱」。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: SummaryRequest) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text=f"{MACHINE_TEXT}（第 {self.calls} 次）",
            model="summarizer-test-model",
            finish_reason="stop",
            prompt_tokens=88,
            completion_tokens=32,
        )


def _runner(seed: Seed, analyzer: Summarizer) -> RollingSummarizer:
    made = {"n": 0}

    def next_id(prefix: str) -> Any:
        def factory(_project_id: str) -> str:
            made["n"] += 1
            return f"{prefix}:{made['n']}"

        return factory

    return RollingSummarizer(
        seed.connection,
        analyzer,
        summary_id_factory=next_id("summary"),
        call_id_factory=next_id("call"),
    )


def _legacy_prompt_hash(chapter_text: str) -> str:
    """013 之前 `chapter_summary.prompt_hash` 里装的那个哈希，**在这儿独立算一遍**。

    从 `rolling_summary` import 那个内部函数就成了「拿实现验实现」：地址算法改了它
    跟着改，而那条断言的全部意义就是「别改」。
    """
    from hashlib import sha256
    import json

    from novel_harness.draft.summarize import build_summary_messages

    encoded = json.dumps(
        build_summary_messages(chapter_text),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _rows(conn: Connection, project_id: str, chapter: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT summary, source, status FROM chapter_summary"
            " WHERE project_id = ? AND chapter_number = ? ORDER BY rowid",
            (project_id, chapter),
        )
    ]


# ══════════════════════════════════════════════════════════════════════════
# 1. 改 —— 追加一行，模型那一行留着
# ══════════════════════════════════════════════════════════════════════════


def test_editing_appends_the_author_row_and_keeps_the_machine_one(seed: Seed) -> None:
    analyzer = Summarizer()
    _runner(seed, analyzer).ensure(seed.project_id, 1)

    conn = seed.connection()
    try:
        saved = save_author_summary(
            conn, project_id=seed.project_id, chapter_number=1, text=AUTHOR_TEXT
        )
        assert saved.summary == AUTHOR_TEXT
        assert saved.source is SummaryOrigin.AUTHOR

        rows = _rows(conn, seed.project_id, 1)
        assert len(rows) == 2, "作者改一段总结不该让模型写的那一行凭空消失（005 的调子）"
        assert rows[0]["source"] == "model" and rows[0]["status"] == "ACTIVE"
        assert rows[1]["source"] == "author"
        # 现在算数的是作者那一行。
        assert SummaryStore(conn).get(seed.project_id, 1).summary == AUTHOR_TEXT
    finally:
        conn.close()


def test_saving_the_same_text_twice_appends_nothing(seed: Seed) -> None:
    conn = seed.connection()
    try:
        first = save_author_summary(
            conn, project_id=seed.project_id, chapter_number=1, text=AUTHOR_TEXT
        )
        again = save_author_summary(
            conn, project_id=seed.project_id, chapter_number=1, text=f"  {AUTHOR_TEXT}  "
        )
        assert again == first
        assert len(_rows(conn, seed.project_id, 1)) == 1
    finally:
        conn.close()


def test_changing_back_to_an_earlier_wording_really_takes(seed: Seed) -> None:
    """**A → B → 改回 A**。

    只拿文字算内容地址的话，第三步会撞上第一步那一行的唯一键，而库里那一行是**旧的**
    ——最新一行仍然是 B，于是屏幕上作者刚做的那次修改什么都没发生，**而且不报错**。
    这是这次改动里最容易长出来的那种「看起来完全正常的假页面」。
    """
    conn = seed.connection()
    try:
        for text in ("甲版：他没问。", "乙版：他问了一句。", "甲版：他没问。"):
            save_author_summary(
                conn, project_id=seed.project_id, chapter_number=1, text=text
            )
        assert SummaryStore(conn).get(seed.project_id, 1).summary == "甲版：他没问。"
        assert len(_rows(conn, seed.project_id, 1)) == 3
    finally:
        conn.close()


def test_empty_and_overlong_text_are_refused_in_words_the_author_can_read(
    seed: Seed,
) -> None:
    conn = seed.connection()
    try:
        with pytest.raises(SummaryTextRejected) as empty:
            save_author_summary(
                conn, project_id=seed.project_id, chapter_number=1, text="   \n "
            )
        with pytest.raises(SummaryTextRejected) as long:
            save_author_summary(
                conn,
                project_id=seed.project_id,
                chapter_number=1,
                text="字" * (AUTHOR_SUMMARY_MAX_CHARS + 1),
            )
        for exc in (empty, long):
            said = str(exc.value)
            assert re.search(r"[一-鿿]", said), "拒绝作者的话必须是中文"
            assert not re.search(r"[a-z][a-z0-9]*_[a-z0-9]+", said), "别把字段名摆给作者"
        assert _rows(conn, seed.project_id, 1) == []
    finally:
        conn.close()


def test_a_chapter_with_no_text_cannot_be_summarized_by_hand_either(seed: Seed) -> None:
    """判据和 `ensure` 是同一条：没有当前正文快照 = 没得总结。"""
    conn = seed.connection()
    try:
        with pytest.raises(SummaryChapterNotFound):
            save_author_summary(
                conn, project_id=seed.project_id, chapter_number=99, text=AUTHOR_TEXT
            )
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 2. 撤回 —— 「这一章当作没总结」，而库里一行都不少
# ══════════════════════════════════════════════════════════════════════════


def test_retracting_hides_the_chapter_everywhere_the_draft_reads(seed: Seed) -> None:
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    runner.ensure(seed.project_id, 1)
    runner.ensure(seed.project_id, 2)

    conn = seed.connection()
    try:
        retract_summary(conn, project_id=seed.project_id, chapter_number=1)
        store = SummaryStore(conn)

        # 起草那边：整章不出现（`for_range` 是进 prompt 的那一份）。
        assert [row.chapter_number for row in store.for_range(seed.project_id, 1, 3)] == [2]
        assert store.get(seed.project_id, 1) is None
        # 覆盖率那边：算作「缺」，但**带着一句「是你撤的」**——否则界面会去催他补一件
        # 他刚做完的事。
        first = store.coverage(seed.project_id, 1, 3)[0]
        assert first.has_text and first.summary is None and first.retracted

        rows = _rows(conn, seed.project_id, 1)
        assert len(rows) == 2 and rows[0]["status"] == "ACTIVE"
        assert rows[1]["status"] == "RETRACTED"
        assert rows[1]["summary"] == rows[0]["summary"], "撤回那一行要带着被撤掉的原文"
    finally:
        conn.close()


def test_retracting_twice_or_with_nothing_there_appends_nothing(seed: Seed) -> None:
    conn = seed.connection()
    try:
        assert retract_summary(conn, project_id=seed.project_id, chapter_number=1) is None
        assert _rows(conn, seed.project_id, 1) == []

        save_author_summary(
            conn, project_id=seed.project_id, chapter_number=1, text=AUTHOR_TEXT
        )
        retract_summary(conn, project_id=seed.project_id, chapter_number=1)
        retract_summary(conn, project_id=seed.project_id, chapter_number=1)
        assert len(_rows(conn, seed.project_id, 1)) == 2
    finally:
        conn.close()


def test_generating_again_after_a_retraction_really_pays_and_really_takes(
    seed: Seed,
) -> None:
    """撤回语义里写死的那条退路：**想重来就再点生成。**

    同一章正文 = 同一份 prompt = 同一个哈希，所以 013 之前的幂等判据（「这一章有没有
    一行的键等于这份 prompt」）会在这儿静默说谎：旧的那一行还在，于是「已经有了」，
    重新生成永远不发生，而作者点几次都出不去。
    """
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    runner.ensure(seed.project_id, 1)
    assert analyzer.calls == 1

    conn = seed.connection()
    try:
        retract_summary(conn, project_id=seed.project_id, chapter_number=1)
    finally:
        conn.close()

    again = runner.ensure(seed.project_id, 1)
    assert analyzer.calls == 2, "撤回之后再点生成必须真的重来一次"
    assert again.summary.endswith("（第 2 次）")
    assert again.status is SummaryState.ACTIVE

    # 而**再点一次不会再付一次**：幂等照旧成立，只是判据换成了「最新那一行」。
    runner.ensure(seed.project_id, 1)
    assert analyzer.calls == 2


def test_generating_again_after_an_author_edit_does_not_hand_back_his_own_words(
    seed: Seed,
) -> None:
    """作者改过之后按「重新生成」，拿回来的必须是模型新写的，不是他自己那段字。"""
    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    runner.ensure(seed.project_id, 1)

    conn = seed.connection()
    try:
        save_author_summary(
            conn, project_id=seed.project_id, chapter_number=1, text=AUTHOR_TEXT
        )
    finally:
        conn.close()

    regenerated = runner.ensure(seed.project_id, 1)
    assert analyzer.calls == 2
    assert regenerated.summary != AUTHOR_TEXT
    assert regenerated.source is SummaryOrigin.MODEL


def test_a_row_written_before_013_still_counts_as_generated(seed: Seed) -> None:
    """老库里那些行的 `prompt_hash` 是**原始哈希**（`sha256(消息序列)`）。

    认不出它 = 作者已有的每一章都被判成「没生成过」，然后重新付一遍钱。
    所以第一行的地址必须**逐字节**还是那个原始哈希（`_address` 的 revision 0 那一支），
    而不是「反正 ensure 自己写的自己认得」——那种自洽在换了库的那一刻就断了。
    """
    conn = seed.connection()
    try:
        text = conn.execute(
            "SELECT chapter_snapshot.text AS text FROM chapter"
            " JOIN chapter_snapshot ON chapter_snapshot.chapter_id = chapter.id"
            " WHERE chapter.project_id = ? AND chapter.number = 1",
            (seed.project_id,),
        ).fetchone()["text"]
    finally:
        conn.close()

    analyzer = Summarizer()
    runner = _runner(seed, analyzer)
    first = runner.ensure(seed.project_id, 1)
    assert first.prompt_hash == _legacy_prompt_hash(text)

    runner.ensure(seed.project_id, 1)
    assert analyzer.calls == 1


# ══════════════════════════════════════════════════════════════════════════
# 3. HTTP 那四条 —— 出参一个形状，撤回不被后台买回来
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """只把模型换成桩，库 / 幂等键 / 审计写入全走真代码（同契约测试那一轮）。"""
    import novel_harness.api.deps as deps_mod

    monkeypatch.setattr(
        deps_mod,
        "complete",
        lambda messages, *, config=None, plan=None, client=None: CompletionResult(
            text="萧决在青云城主府听说了血脉秘密。",
            model="deepseek-v4-flash",
            finish_reason="stop",
        ),
    )


def test_the_four_routes_all_answer_with_the_same_shape(
    client: TestClient, book: dict[str, str], stub_model: None
) -> None:
    base = f"/api/projects/{book['pid']}/chapters/1/summary"

    empty = client.get(base)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "chapter_number": 1,
        "has_text": True,
        "summary": None,
        "created_at": None,
        "retracted": False,
        "author_written": False,
    }

    generated = client.post(base)
    assert generated.status_code == 200, generated.text
    assert set(generated.json()) == set(empty.json()), "四条路由的出参形状必须一样"
    assert generated.json()["summary"] and generated.json()["author_written"] is False

    edited = client.patch(base, json={"summary": AUTHOR_TEXT})
    assert edited.status_code == 200, edited.text
    assert edited.json()["summary"] == AUTHOR_TEXT
    assert edited.json()["author_written"] is True
    # 做完之后重新读一遍必须逐字节相同 —— 否则「改完屏幕上显示的」和「刷新之后显示的」
    # 有机会不一样，而那种不一样没有任何东西会报错。
    assert client.get(base).json() == edited.json()

    retracted = client.delete(base)
    assert retracted.status_code == 200, retracted.text
    assert retracted.json()["summary"] is None
    assert retracted.json()["retracted"] is True
    assert client.get(base).json() == retracted.json()


def test_editing_a_chapter_that_has_no_text_is_a_404_not_a_500(
    client: TestClient, book: dict[str, str]
) -> None:
    base = f"/api/projects/{book['pid']}/chapters/9/summary"
    missing = client.patch(base, json={"summary": AUTHOR_TEXT})
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["error"] == "chapter_not_found"
    # 撤回一个不存在的东西**不是错**：这个动作没有失败的形态。
    assert client.delete(base).status_code == 200


def test_an_overlong_edit_is_refused_in_the_authors_language(
    client: TestClient, book: dict[str, str]
) -> None:
    base = f"/api/projects/{book['pid']}/chapters/1/summary"
    refused = client.patch(base, json={"summary": "字" * (AUTHOR_SUMMARY_MAX_CHARS + 1)})
    assert refused.status_code == 422, refused.text
    said = refused.json()["detail"]
    assert re.search(r"[一-鿿]", said) and "_" not in said


def test_background_tidying_never_buys_back_a_retracted_summary(
    client: TestClient, book: dict[str, str], stub_model: None
) -> None:
    """作者撤掉一份总结、切走一章 —— 后台**不许**替他重新买一份回来。

    判据在 `SummaryStore.latest()`：用 `get()` 的话撤回过的章在后台眼里就是「还没生成」，
    于是那是一次他没按过的付费调用，顺带把他刚做的动作抹掉。
    """
    base = f"/api/projects/{book['pid']}/chapters/1"
    assert client.post(f"{base}/summary").status_code == 200
    assert client.delete(f"{base}/summary").status_code == 200

    dispatched = client.post(f"{base}/autopilot")
    assert dispatched.status_code == 202, dispatched.text
    assert dispatched.json()["summary"] == "retracted"
    assert client.get(f"{base}/autopilot").json()["summary_state"] == "retracted"
    # 后台一个字都没写回去。
    assert client.get(f"{base}/summary").json()["retracted"] is True
