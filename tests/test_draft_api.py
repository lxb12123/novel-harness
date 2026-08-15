"""`/draft` 端点（修正案 7，实验状态，2026-08-02）。

放行 ≠ 验证：响应必须带 ``experimental`` 标注；连接参数走 AI 设置页（BYOK）
优先、环境变量兜底；没配置 / 角色解析不了 / form 非法都必须 422。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import db, importer, project
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph

DRAFT_TEXT = "萧决道：「此剑无名。」"
ZH_LENGTH = {
    "language": "zh",
    "min_units": 2000,
    "target_units": 2500,
    "max_units": 3000,
}


TWIST = "玄血蛊"
"""秘密的**内容 tell**。它一个字符都不许进 prompt——`draft/context.py` 第三节：
tell 一旦进了 X1/X2，两臂 100% 命中自己写进去的词，Δ 翻负，预注册的裁决表读出
「KILL 起草线」，把一个本来对的项目砍掉，而全程没有东西会红。"""


BOOK_TXT = """第一章 起

萧决推开门，屋里没有点灯。

第二章 承

夜色沉下来，青云城主府的灯一盏盏亮起。
"""
"""两章正文——**滚动总结那几条断言要的是「有当前快照」**，没有它 `ensure` 只会一路
`SummaryChapterNotFound`，测试变成在验一个空集。"""


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    dbp = tmp_path / "book.db"
    conn = db.connect(dbp)
    db.migrate(conn)
    root = tmp_path / "书"
    pid = project.create(conn, name="测试书", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    # 一个**非人物**节点，名字同样进花名册、同样能被 `resolve_cast` 唯一解析。
    # 它是「在场角色」框里最容易被作者写进去的那类词，也是 PRODUCT 分支曾经的 500。
    ledger.declare_node(NodeLabel.LOCATION, "青云城主府")
    # 一个带 tell 的秘密：没有它，「续写全禁」和「tell 不外泄」两条断言都只是在验空集。
    from novel_harness.graph import NodeProps, SecretDetail
    from novel_harness.graph.models import NodeSpec

    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST}),
            secret=SecretDetail(),
        )
    )
    conn.commit()

    txt = tmp_path / "src.txt"
    txt.write_text(BOOK_TXT, encoding="utf-8")
    importer.import_book(store, pid, txt=txt, root=root)
    conn.commit()
    conn.close()
    return {"db": str(dbp), "pid": pid}


@pytest.fixture
def client(
    book: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("NH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NH_LLM_MODEL", raising=False)
    monkeypatch.delenv("NH_LLM_API_KEY", raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _url(book: dict[str, str]) -> str:
    return f"/api/projects/{book['pid']}/chapters/1/draft"


def _configure(client: TestClient) -> None:
    r = client.put(
        "/api/settings",
        json={
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
        },
    )
    assert r.status_code == 200, r.text


def _stub_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    def fake(
        messages,
        *,
        config=None,
        plan=None,
        client=None,
    ) -> CompletionResult:
        return CompletionResult(
            text=DRAFT_TEXT, model="fake", finish_reason="stop"
        )

    monkeypatch.setattr(generate_mod, "complete", fake)


def _capture_complete(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, str]]]:
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    observed: list[list[dict[str, str]]] = []

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        observed.append(messages)
        return CompletionResult(text="正文" * 1000, model="fake", finish_reason="stop")

    monkeypatch.setattr(generate_mod, "complete", fake)
    return observed


def test_draft_returns_experimental_draft(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "萧决看剑。",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "form": "X1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["experimental"] is True
    assert "实验状态" in body["note"]
    assert DRAFT_TEXT in body["text"]
    assert body["attempts"] == 2  # 桩文本不足下限 → 续写一次


def test_draft_without_connection_config_is_422(
    client: TestClient, book: dict[str, str]
) -> None:
    r = client.post(
        _url(book),
        json={"goal": "x", "cast": ["萧决"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    assert "AI 设置" in r.text


def test_draft_unresolvable_cast_is_422(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"goal": "x", "cast": ["不存在的人"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    assert "解析不了" in r.text


def test_draft_bad_form_is_422(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "form": "X9",
        },
    )
    assert r.status_code == 422
    assert "X0" in r.text
    assert "PRODUCT" in r.text


def test_default_product_draft_gets_confirmed_memory_preface(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    observed = _capture_complete(monkeypatch)

    response = client.post(
        _url(book),
        json={"goal": "萧决看剑。", "cast": ["萧决"], "length": ZH_LENGTH},
    )

    assert response.status_code == 200, response.text
    # `[文风][记忆][用户]`（ADR 0019 边界六）：文风跨章不变、排最前面，才有前缀缓存可言；
    # 记忆逐章变，插在它后面。原来的顺序把唯一稳定的那块夹在中间，缓存价值为零。
    assert [message["role"] for message in observed[0]] == ["system", "system", "user"]
    assert observed[0][1]["content"].startswith("已确认的故事记忆")
    assert "已确认的故事记忆" not in observed[0][0]["content"]


def test_explicit_kill_gate_form_keeps_the_prior_prompt_path(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    observed = _capture_complete(monkeypatch)

    response = client.post(
        _url(book),
        json={
            "goal": "萧决看剑。",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "form": "X0",
        },
    )

    assert response.status_code == 200, response.text
    rendered = "\n".join(message["content"] for message in observed[0])
    assert "已确认的故事记忆" not in rendered


def test_draft_custom_write_rule_is_accepted(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "write_rule": "文白夹杂，多用短句，对白简洁。",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("word", ["秘密", "不知道", "泄露", "剧透", "伏笔", "设定"])
def test_draft_write_rule_forbidden_hints_are_422(
    client: TestClient, book: dict[str, str], word: str
) -> None:
    _configure(client)
    r = client.post(
        _url(book),
        json={
            "goal": "x",
            "cast": ["萧决"],
            "length": ZH_LENGTH,
            "write_rule": f"写的时候{f'不要{word}'}任何情节。",
        },
    )
    assert r.status_code == 422
    assert "三臂共用" in r.text


# ══════════════════════════════════════════════════════════════════════════
# 行内续写（ADR 0015）—— 两种请求形状在 HTTP 边界上的差别
# ══════════════════════════════════════════════════════════════════════════

_SHORT = {"language": "zh", "min_units": 80, "target_units": 150, "max_units": 300}


def test_continuation_needs_neither_goal_nor_cast(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0015 D4：**作者边写边要提示，此刻「谁在场」还没有答案。**

    这条红了就说明续写又变回「先填在场人物才给用」——那正是这次要拆掉的门槛。
    """
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "mode": "continuation",
            "previous_tail": "萧决推开门，屋里没有点灯。",
            "length": _SHORT,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["experimental"] is True


def test_continuation_without_cast_forbids_every_secret(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """不知道谁在场 ⇒ 全禁（fail-closed）。**方向搞反就是泄漏。**"""
    _configure(client)
    seen = _capture_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"mode": "continuation", "previous_tail": "夜色沉下来。", "length": _SHORT},
    )
    assert r.status_code == 200, r.text

    body = "\n".join(m["content"] for m in seen[0])
    assert "未知" in body and "不得说破" in body
    assert "血脉秘密" in body  # 显示名进 prompt……
    assert TWIST not in body  # ……内容 tell 永远不进


def test_continuation_with_cast_is_less_restrictive(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """填了 cast ⇒ 收紧到精确约束。**它是奖励，不是门槛**（ADR 0015 D4）。"""
    _configure(client)
    seen = _capture_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
            "mode": "continuation",
            "cast": ["萧决"],
            "previous_tail": "夜色沉下来。",
            "length": _SHORT,
        },
    )
    assert r.status_code == 200, r.text

    body = "\n".join(m["content"] for m in seen[0])
    assert "【在场】\n萧决" in body
    assert "未知" not in body


def test_continuation_refuses_an_author_supplied_goal(
    client: TestClient, book: dict[str, str]
) -> None:
    """ADR 0015 D3：`goal` 是 ADR 0010 点名的泄漏入口，续写模式把它**关掉**。

    前端能传的东西作者就能改，所以这条闸必须在后端。
    """
    _configure(client)
    r = client.post(
        _url(book),
        json={
            "mode": "continuation",
            "goal": "写萧决发现血脉有异——他还不知道那是家族封印的反噬",
            "length": _SHORT,
        },
    )
    assert r.status_code == 422


def test_whole_chapter_drafting_still_demands_goal_and_cast(
    client: TestClient, book: dict[str, str]
) -> None:
    """**反面守卫**：加了续写形状之后，整章起草那条闸不许跟着松。"""
    _configure(client)
    for payload in (
        {"cast": ["萧决"], "length": ZH_LENGTH},  # 缺 goal
        {"goal": "萧决看剑。", "length": ZH_LENGTH},  # 缺 cast
    ):
        assert client.post(_url(book), json=payload).status_code == 422


# ══════════════════════════════════════════════════════════════════════════
# 记忆层的最后一厘米（2026-08-06 盘点）
#
# 三个洞叠在一起，作者在浏览器里点「AI 起草」走不到 `build_product_context`：
# 记忆只在 `form=PRODUCT` 时装配，而前端硬编码发 `X1`；滚动总结又没有 HTTP 入口，
# 于是 `chapter_summary` 表恒空、【更早章节滚动总结】永远是「- 暂无」且**无人提示**。
# 下面这一组钉的就是补完之后的形状。
# ══════════════════════════════════════════════════════════════════════════


def _draft(client: TestClient, book: dict[str, str], **extra: object) -> dict:
    payload = {"goal": "萧决看剑。", "cast": ["萧决"], "length": ZH_LENGTH, **extra}
    response = client.post(_url(book), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_product_draft_says_what_it_actually_loaded(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """记忆层装了什么必须是**响应里的数字**，不是作者的猜测（§10 约束 8）。"""
    _configure(client)
    _stub_complete(monkeypatch)

    memory = _draft(client, book)["memory"]

    assert memory["assembled"] is True
    assert memory["profiles"] == 1  # 萧决的档案
    assert memory["rolling_summaries"] == 0
    assert memory["note"]


def test_kill_gate_arms_say_they_carry_no_memory(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """X0/X1/X2 不带记忆是**有意的**（三臂是考卷），所以回执要说出来而不是给个 0。"""
    _configure(client)
    _stub_complete(monkeypatch)

    memory = _draft(client, book, form="X1")["memory"]

    assert memory["assembled"] is False
    assert memory["profiles"] == 0
    assert memory["note"]


def test_continuation_reports_its_missing_memory_too(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    response = client.post(
        _url(book),
        json={"mode": "continuation", "previous_tail": "夜色沉下来。", "length": _SHORT},
    )
    assert response.status_code == 200, response.text
    assert response.json()["memory"]["assembled"] is False


def test_a_non_character_cast_surface_does_not_explode(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**回归钉。** 「青云城主府」是地点，但它同样在花名册里、同样唯一解析得出来。

    `resolve_cast` 不看 label，`require_resolved_cast()` 也不看，于是它一路穿到
    `build_product_context`，撞上「cast must contain only Character references」。
    **实测的失败形态不是 500 而是 422**（那句 `ValueError` 被全局 handler 接住，
    原样发给作者），这更坏一点：一句英文的引擎内部话冒充作者的输入错误，
    而他唯一做错的事是在「在场角色」里写了个地名。
    （ADR 0018 的推导路径不产生这种输入——`mentioned_cast` 只收 Character；
    危险的是起草抽屉里那个**作者手打**的框，以及任何直接调 API 的客户端。）
    """
    _configure(client)
    _stub_complete(monkeypatch)

    body = _draft(client, book, cast=["青云城主府"])

    assert body["memory"]["assembled"] is False
    assert "没有一个解析成人物" in body["memory"]["note"]


# ── 逐字上文的长度：产品档不许继承对照臂的预算（ADR 0019 边界五）────────────


_LONG_TAIL = "甲" * 6_000
"""比 X0 的 800 长得多的一段上文。真书里这就是「上一章的结尾」。"""


def _tail_units_in_prompt(messages: list[dict[str, str]]) -> int:
    body = "\n".join(m["content"] for m in messages)
    return body.count("甲")


def test_product_draft_gets_a_tail_far_longer_than_the_control_arm(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这条是「AI 写出来前言不搭后语」那半个病因的钉子。**

    800 是 X0 对照臂的定义（ARCHITECTURE §9），它存在是为了证明「给得少会崩」；
    产品继承它 = 产品拿对照组的预算跑，而作者只会看到文笔和情绪断掉。
    """
    _configure(client)
    seen = _capture_complete(monkeypatch)

    _draft(client, book, previous_tail=_LONG_TAIL)

    assert _tail_units_in_prompt(seen[0]) > 800


def test_a_named_kill_gate_arm_still_gets_exactly_800(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名 X0/X1/X2 = kill-gate 的臂，**预算必须原样**。这条红了就是考卷被改了。"""
    _configure(client)
    seen = _capture_complete(monkeypatch)

    for arm in ("X0", "X1", "X2"):
        seen.clear()
        _draft(client, book, form=arm, previous_tail=_LONG_TAIL)
        assert _tail_units_in_prompt(seen[0]) == 800


def test_mixed_cast_keeps_the_characters_and_drops_the_rest(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """过滤是**丢掉非人物**，不是整份记忆一起放弃——否则作者多写一个地名就白丢档案。"""
    _configure(client)
    _stub_complete(monkeypatch)

    memory = _draft(client, book, cast=["萧决", "青云城主府"])["memory"]

    assert memory["assembled"] is True
    assert memory["profiles"] == 1


# ── 滚动总结的 HTTP 入口 ────────────────────────────────────────────────────


def _stub_summarizer(monkeypatch: pytest.MonkeyPatch, text: str = "萧决进屋，没点灯。") -> None:
    """只把**模型**换成桩：库、幂等键、审计写入全部走真代码。"""
    import novel_harness.api.deps as deps_mod
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        deps_mod,
        "complete",
        lambda messages, *, config=None, plan=None, client=None: CompletionResult(
            text=text, model="fake", finish_reason="stop"
        ),
    )


def _summaries(client: TestClient, book: dict[str, str], chapter: int) -> dict:
    response = client.get(f"/api/projects/{book['pid']}/chapters/{chapter}/summaries")
    assert response.status_code == 200, response.text
    return response.json()


def test_summary_window_separates_unwritten_from_ungenerated(
    client: TestClient, book: dict[str, str]
) -> None:
    """**静默的零和真的零不许长得一样。** 第 1/2 章有正文没总结 → `missing`；

    第 3 章根本没写 → `has_text=false`，不该催作者去总结一个不存在的东西。
    """
    body = _summaries(client, book, 4)  # 窗口 = 本章之前的全部章

    assert (body["window_first"], body["window_last"]) == (1, 3)
    assert body["summarized"] == 0
    assert body["missing"] == [1, 2]
    assert [row["has_text"] for row in body["chapters"]] == [True, True, False]


def test_early_chapters_have_an_empty_window_not_a_complaint(
    client: TestClient, book: dict[str, str]
) -> None:
    """写**第 1 章**时滚动总结那一层本来就该是空的——它前面没有章。

    这一条和上一条合起来才是「零带着理由」：同样是 0 条总结，一个要催，一个不许催。

    （窗口曾经是「本章 − 8 − 1」，所以第 3 章也是空的。改成字数预算之后，
    「近期」有多深由事件字数决定、不再由章号写死，总结窗口也就不再从它倒推——
    见 `test_rolling_summaries_do_not_depend_on_the_event_window`。）
    """
    body = _summaries(client, book, 1)

    assert body["chapters"] == [] and body["missing"] == []
    assert body["window_last"] < body["window_first"]


def test_generating_a_summary_is_explicit_and_idempotent(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同章重复点不重复付费——前端因此可以「把缺的挨个补一遍」而不必自己记账。"""
    _configure(client)
    calls: list[int] = []
    import novel_harness.api.deps as deps_mod
    from novel_harness.draft.provider import CompletionResult

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        calls.append(1)
        return CompletionResult(text="萧决进屋，没点灯。", model="fake", finish_reason="stop")

    monkeypatch.setattr(deps_mod, "complete", fake)

    url = f"/api/projects/{book['pid']}/chapters/1/summary"
    first = client.post(url)
    assert first.status_code == 200, first.text
    assert first.json()["summary"] == "萧决进屋，没点灯。"
    assert client.post(url).json() == first.json()
    assert len(calls) == 1

    assert _summaries(client, book, 12)["missing"] == [2]


def test_generated_summary_reaches_the_writer_prompt(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这条是整件事的目的。** 生成 → 起草，那段字必须真的出现在写作 prompt 里；

    红了就说明链条又断在某一环（而每一环单看都会绿）。
    """
    _configure(client)
    _stub_summarizer(monkeypatch, text="萧决进屋，没点灯。")
    assert client.post(f"/api/projects/{book['pid']}/chapters/1/summary").status_code == 200

    seen = _capture_complete(monkeypatch)
    response = client.post(
        f"/api/projects/{book['pid']}/chapters/12/draft",
        json={"goal": "萧决看剑。", "cast": ["萧决"], "length": ZH_LENGTH},
    )
    assert response.status_code == 200, response.text

    assert response.json()["memory"]["rolling_summaries"] == 1
    assert "萧决进屋，没点灯。" in "\n".join(m["content"] for m in seen[0])
    assert response.json()["memory"]["unsummarized_chapters"] == [2]


def test_summarizing_a_chapter_without_text_is_404(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_summarizer(monkeypatch)
    response = client.post(f"/api/projects/{book['pid']}/chapters/9/summary")
    assert response.status_code == 404


def test_summarizing_without_connection_config_is_422(
    client: TestClient, book: dict[str, str]
) -> None:
    """没配钥匙的作者该看见「去顶栏 ⚙ 填」，不是 pydantic 的开发者输出。"""
    response = client.post(f"/api/projects/{book['pid']}/chapters/1/summary")
    assert response.status_code == 422
    assert "AI 设置" in response.text
