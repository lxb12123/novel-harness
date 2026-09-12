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

_SHORT = {"language": "zh", "min_units": 80, "target_units": 150, "max_units": 300}
"""行内续写那一档的长度。"""


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
    # 一个**非人物**节点，名字同样进角色册、同样能被 `resolve_cast` 唯一解析。
    # 它是「在场角色」框里最容易被作者写进去的那类词，也是 PRODUCT 分支曾经的 500。
    ledger.declare_node(NodeLabel.LOCATION, "青云城主府")
    # 一个带 tell 的秘密：没有它，「续写全禁」和「tell 不外泄」两条断言都只是在验空集。
    from novel_harness.graph import NodeProps
    from novel_harness.graph.models import NodeSpec

    store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.FACTION,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST}),
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
        json={"cast": ["萧决"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    # 国际化第四批 Phase B：这句话不再由后端算，`error` 是码，前端拿它去
    # `backendMessages.ts` 渲染整句（含"去顶栏 AI 设置"那句指引）。
    assert r.json()["detail"]["error"] == "model_not_configured"


def test_draft_unresolvable_cast_is_422(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"cast": ["不存在的人"], "length": ZH_LENGTH},
    )
    assert r.status_code == 422
    # 国际化第四批 Phase B：`UnresolvedCast` 转发的是它自己的 code/params，不再是
    # 拼好的句子——`api/app.py` 那两处 except 块直接透传，不再包"在场角色解析
    # 不了："这层前缀。
    detail = r.json()["detail"]
    assert detail["error"] == "unresolved_cast_ambiguous"
    assert "不存在的人" in detail["params"]["unresolved"]


def test_the_draft_body_no_longer_takes_a_form(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**三臂删了**（2026-08-25），`form` 那一格从请求体上拿掉了。

    这条量的是「拿掉之后老前端不会炸」：`DraftRequest` 不是 `extra="forbid"`，
    所以还在发 `form` 的旧客户端只是那个键被忽略，起草照常成功。
    """
    _configure(client)
    _stub_complete(monkeypatch)
    from novel_harness.api.app import DraftRequest

    assert "form" not in DraftRequest.model_fields

    r = client.post(
        _url(book),
        json={"cast": ["萧决"], "length": ZH_LENGTH, "form": "X9"},
    )
    assert r.status_code == 200, r.text


def test_default_product_draft_gets_confirmed_memory_preface(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(client)
    observed = _capture_complete(monkeypatch)

    # 2026-08-26：从 `POST /draft` 改成直调（见 `_draft` 的 docstring）。
    # **这三段顺序是全仓唯一一处在钉它的地方**，所以入口没了它也不能跟着没。
    _draft(client, book)

    # `[文风][记忆][用户]`（ADR 0019 边界六）：文风跨章不变、排最前面，才有前缀缓存可言；
    # 记忆逐章变，插在它后面。原来的顺序把唯一稳定的那块夹在中间，缓存价值为零。
    assert [message["role"] for message in observed[0]] == ["system", "system", "user"]
    assert observed[0][1]["content"].startswith("已生效的故事记忆")
    assert "已生效的故事记忆" not in observed[0][0]["content"]


# ⚠️ **`write_rule` 那两条 2026-08-26 搬去 `tests/test_product_assemble.py` 了。**
#
# 它们从前打的是 `POST /draft`，而 `write_rule` 那天从这个请求体上删了：**只有整章那一支
# 读它**，而整章的 HTTP 入口零调用方。作者的文风今天挂在**对话**上
# （`agent/store.py::start_conversation`），由 `agent/drafting.py` 递进
# `ChapterDraftRequest`。
#
# **搬不是删。** `WRITE_RULE_FORBIDDEN_HINTS` 那张禁词网活着，起草工具每写一章都过一次；
# 而它的拒绝**只在 `product_draft.check_request()` 里**（`assemble()` 自己是有意宽松的，
# 见那个常量的 docstring）。搬走之前，那两条是这张网全仓**唯一**的覆盖
# ——`check_request` 一处直接测试都没有。


def test_a_write_rule_in_the_body_is_refused_not_quietly_dropped(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """留在 HTTP 这一层的只剩这一条：**这个请求体不收 write_rule，而且说出来。**

    静默忽略它 = 作者以为模型按他的文风写了（同 `goal` 那一条）。
    """
    _configure(client)
    observed = _capture_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"previous_tail": "夜色沉下来。", "length": _SHORT, "write_rule": "多用短句。"},
    )
    assert r.status_code == 422, r.text
    assert "不收 write_rule" in r.text
    assert observed == [], "被拒的请求还是花了一次模型调用"


# ══════════════════════════════════════════════════════════════════════════
# 行内续写（ADR 0015）—— 两种请求形状在 HTTP 边界上的差别
# ══════════════════════════════════════════════════════════════════════════



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
                        "previous_tail": "萧决推开门，屋里没有点灯。",
            "length": _SHORT,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["experimental"] is True
def test_continuation_with_cast_is_less_restrictive(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """填了 cast ⇒ 收紧到精确约束。**它是奖励，不是门槛**（ADR 0015 D4）。"""
    _configure(client)
    seen = _capture_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={
                        "cast": ["萧决"],
            "previous_tail": "夜色沉下来。",
            "length": _SHORT,
        },
    )
    assert r.status_code == 200, r.text

    body = "\n".join(m["content"] for m in seen[0])
    assert "【在场】\n萧决" in body
    assert "未知" not in body


def test_an_author_supplied_goal_is_refused_not_quietly_dropped(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0015 D3：`goal` 是 ADR 0010 点名的泄漏入口，这条路上**没有这一位**。

    ── 两层，缺一层这条纪律就只剩一半 ────────────────────────────────────

    ① **形状上没有** —— `goal` 不在 `model_fields` 里。这比一条校验器硬：
       校验器可以被下一个人放宽，字段不在就得先把它加回来。
    ② **发过来是拒收，不是忽略** —— 这一条是 2026-08-26 补的，而**它才是难的那半**。

    只做 ① 的话，还在发 `goal` 的旧客户端会拿到 200 + 一段续写，那句话被**静默丢掉**，
    而他以为模型读过它了。这个仓库对这件事有明文纪律（本文件到处、以及被删掉的那个
    `_check_mode_shape` 里原话就是「静默丢掉才是坏的」）。

    ⚠️ **这不推翻 2026-08-25「不加 `extra="forbid"`」那条裁定**：那条讲的是
    **空操作键**（忽略 `form` 之后起草照常成功），`form` 今天照旧被忽略，
    下面那条 `..._no_longer_takes_a_form` 还绿着。拒的只有「会换掉产品 / 会被当成
    读过了」的那两位。
    """
    _configure(client)
    from novel_harness.api.app import DraftRequest

    assert "goal" not in DraftRequest.model_fields  # ①

    tell = "写萧决发现血脉有异——他还不知道那是家族封印的反噬"
    observed = _capture_complete(monkeypatch)
    r = client.post(_url(book), json={"goal": tell, "previous_tail": "夜色沉下来。", "length": _SHORT})

    assert r.status_code == 422, r.text  # ②
    assert "不收 goal" in r.text
    assert observed == [], "被拒的请求还是花了一次模型调用"


def test_a_stale_chapter_shaped_body_is_refused_instead_of_silently_becoming_a_continuation(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**整章起草那个入口真的没了，而且它的消失是响亮的。**

    2026-08-26 之前这条路由是两种模式、`mode` 缺省 `"chapter"`，所以这个请求体
    （有 goal、有 cast、要 2,500 字）会起一整章。

    ── 为什么不能让它「忽略 mode，照续写办」 ──────────────────────────────

    那样是 200 + 一两百字：**发一份写整章的请求，拿回来一段续写，没有任何提示。**
    `DraftRequest` 没有 `extra="forbid"`（2026-08-25 的裁定，为的是别让还在发 `form`
    的旧客户端炸掉），所以这件事**不会自己变成报错**——得有人明写。

    那条裁定罩不住这一种：它讲的是**空操作键**（忽略 `form` 之后调用方拿到的还是他要的
    东西），而忽略 `mode="chapter"` 会**换掉产品**。所以这儿只拒会换产品的那一位。
    """
    _configure(client)
    observed = _capture_complete(monkeypatch)

    r = client.post(
        _url(book),
        json={"mode": "chapter", "goal": "萧决看剑。", "cast": ["萧决"], "length": ZH_LENGTH},
    )

    assert r.status_code == 422, r.text
    # 报的话要说清**入口去哪了**，不是「参数不合法」——收到这个 422 的是个程序，
    # 而它下一步要知道往哪走。
    assert "不再起草一整章" in r.text and "行内续写" in r.text
    assert observed == [], "被拒的请求还是花了一次模型调用"


def test_a_stale_frontend_still_sending_mode_continuation_keeps_working(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**旧前端发的那一份必须照旧能用。**

    2026-08-26 之前 `useContinuation` 发的是 `{mode: "continuation", previous_tail, …}`。
    上面那条把 `mode="chapter"` 拒了，**很容易顺手把整个 `mode` 都拒掉**——那就正好
    炸掉 2026-08-25 那条裁定要保护的那种客户端（一个还没更新的前端 bundle）。

    `mode="continuation"` 说的是真话，只是多余。收下。
    """
    _configure(client)
    _stub_complete(monkeypatch)
    r = client.post(
        _url(book),
        json={"mode": "continuation", "previous_tail": "夜色沉下来。", "length": _SHORT},
    )
    assert r.status_code == 200, r.text


# ══════════════════════════════════════════════════════════════════════════
# 记忆层的最后一厘米（2026-08-06 盘点）
#
# 三个洞叠在一起，作者在浏览器里点「AI 起草」走不到 `build_product_context`：
# 记忆只在 `form=PRODUCT` 时装配，而前端硬编码发 `X1`；滚动总结又没有 HTTP 入口，
# 于是 `chapter_summary` 表恒空、【更早章节滚动总结】永远是「- 暂无」且**无人提示**。
# 下面这一组钉的就是补完之后的形状。
#
# （那三个洞里的第一个 2026-08-25 从根上没了：`form` 整个删了，**起草只有一条路**，
#  它必然装配记忆。留着这段注释是因为另外两个洞的修法还挂在这一组测试上。）
# ══════════════════════════════════════════════════════════════════════════


def _draft(client: TestClient, book: dict[str, str], **extra: object) -> dict:
    """起一整章 —— **直调 `product_draft.draft_chapter()`，不走 HTTP。**

    ── 2026-08-26：这个 helper 从「POST /draft」改成了直调 ────────────────────

    `/draft` 上「起草一整章」那个入口删了（它 2026-08-14 起零调用方；
    模式二的 `draft_chapter` 工具本来就是**进程内直调**，不发 HTTP）。
    **被删的是入口，不是实现**——下面这些性质（记忆前言的三段顺序、回执里的数字、
    在场里混进地名时怎么退化）全都是 `draft_chapter()` 的性质，今天由模式二那条路
    在跑，一条都没死。

    **所以这些测试不能删，只能换个进法。** 这儿组装的四样（config / capability /
    plan / ctx）和 `agent/drafting.py` 那条真路是同一套；`generate.complete` 的桩
    照旧生效，因为直调走的是同一段代码。

    ⚠️ 唯独 `plan` 这一位跟着改了：从前它由路由用 `ReasoningEffort.HIGH` 算，
    现在用 `AGENT_DRAFT_REASONING`（= `OFF`）——**那才是模式二真正在用的那一档**，
    见那个常量的 docstring。
    """
    from novel_harness.agent.drafting import AGENT_DRAFT_REASONING
    from novel_harness.api.app import _draft_provider_config
    from novel_harness.api.deps import resolve_route_capabilities
    from novel_harness.draft.capabilities import plan_call
    from novel_harness.draft.context import ResolvedConstraints
    from novel_harness.draft.product_draft import ChapterDraftRequest, draft_chapter
    from novel_harness.draft.length import LengthSpec
    from novel_harness.draft.rolling_summary import SummaryStore
    from novel_harness.graph.sqlite_events import SqliteEventStore
    from novel_harness.panel.constraints import scene_view

    cast = list(extra.pop("cast", ["萧决"]))  # type: ignore[arg-type]
    chapter = int(extra.pop("chapter", 1))  # type: ignore[arg-type]
    length = LengthSpec.model_validate(extra.pop("length", ZH_LENGTH))
    config = _draft_provider_config()
    capability = resolve_route_capabilities(config)
    plan = plan_call(length, AGENT_DRAFT_REASONING, capability)

    conn = db.connect(book["db"])
    try:
        store = SqliteStoryGraph(conn)
        ctx = ResolvedConstraints.of(scene_view(store, book["pid"], chapter, cast), cast)
        drafted = draft_chapter(
            ctx,
            request=ChapterDraftRequest(
                goal=str(extra.pop("goal", "萧决看剑。")),
                length=length,
                **extra,  # type: ignore[arg-type]
            ),
            project_id=book["pid"],
            config=config,
            capability=capability,
            plan=plan,
            events=SqliteEventStore(conn),
            summaries=SummaryStore(conn),
        )
    finally:
        conn.close()
    return {"memory": drafted.memory, "text": drafted.result.text}


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


def test_continuation_reports_its_empty_memory_too(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """这本书一条总结都没有 ⇒ 续写那一格是空的，而**空要带着理由**（§10 约束 8）。

    2026-08-22 之前这条零的理由是「续写整个不带记忆」；现在它带滚动总结那一格
    （`tests/test_continuation_memory.py` 钉着），所以同样一个 `False` 背后换了个
    原因——回执里那句话必须跟着换，否则它就是一句过期的解释。
    """
    _configure(client)
    _stub_complete(monkeypatch)
    response = client.post(
        _url(book),
        json={"previous_tail": "夜色沉下来。", "length": _SHORT},
    )
    assert response.status_code == 200, response.text
    memory = response.json()["memory"]
    assert memory["assembled"] is False
    assert memory["rolling_summaries"] == 0
    assert "总结" in memory["note"]


def test_a_non_character_cast_surface_does_not_explode(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**回归钉。** 「青云城主府」是地点，但它同样在角色册里、同样唯一解析得出来。

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


def test_there_is_no_path_that_still_gets_the_800_floor(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**起草只有一条路，它按窗口取量。**

    2026-08-25 之前点名 X0/X1/X2 会走一条拿 800 字地板的分支（那是 kill-gate 的臂）。
    三臂删了之后那条分支不存在——这条量的就是「它真的没了」：同一份长上文，
    HTTP 那条路进 prompt 的逐字上文**必须多于 800**，否则就是产品在拿对照组的预算跑，
    而作者只会看到「AI 写出来的东西前言不搭后语」。
    """
    _configure(client)
    seen = _capture_complete(monkeypatch)

    _draft(client, book, previous_tail=_LONG_TAIL)
    assert _tail_units_in_prompt(seen[0]) > 800


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


def _generate(book: dict[str, str], chapter: int) -> None:
    """给这一章生成一份滚动总结。

    直接调生产上那个执行体——同一个 `ensure`、同一份幂等、同一条审计，只是少了
    HTTP 那一层。`POST …/summary` 2026-08-25 删过、2026-09-05 加回来了，
    **但这个帮手不改回去**：它的调用点全是「先造一份总结出来，再测别的东西」，
    借 HTTP 只是多一层跟被测对象无关的失败面。那条路由自己的行为由
    `test_summary_edit.py::test_the_author_can_buy_a_retracted_summary_back_by_hand` 钉。
    """
    import novel_harness.api.deps as deps_mod

    deps_mod.build_summarizer().ensure(book["pid"], chapter)


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


def test_generating_the_same_chapter_twice_only_pays_once(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一份正文只买一次。

    **这条从前叫「显式且幂等」，2026-08-25 只剩后半句**：手动那颗按钮删了，总结的
    触发只剩「保存之后」和「每 30 分钟扫描」两个自动的。于是「显式」不再是一件事，
    **而「幂等」比从前更要紧**——两个自动触发都不问作者，一份正文被扫到两次是常态
    （保存一次、半小时后又扫到），它俩共用的正是这一个执行体。
    """
    _configure(client)
    calls: list[int] = []
    import novel_harness.api.deps as deps_mod
    from novel_harness.draft.provider import CompletionResult

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        calls.append(1)
        return CompletionResult(text="萧决进屋，没点灯。", model="fake", finish_reason="stop")

    monkeypatch.setattr(deps_mod, "complete", fake)

    _generate(book, 1)
    first = client.get(f"/api/projects/{book['pid']}/chapters/1/summary").json()
    assert first["summary"] == "萧决进屋，没点灯。"
    _generate(book, 1)
    assert client.get(f"/api/projects/{book['pid']}/chapters/1/summary").json() == first
    assert len(calls) == 1, "同一份正文买了第二次 —— 两个自动触发的成本论证靠的就是这条"

    assert _summaries(client, book, 12)["missing"] == [2]


def test_generated_summary_reaches_the_writer_prompt(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**这条是整件事的目的。** 生成 → 起草，那段字必须真的出现在写作 prompt 里；

    红了就说明链条又断在某一环（而每一环单看都会绿）。
    """
    _configure(client)
    _stub_summarizer(monkeypatch, text="萧决进屋，没点灯。")
    _generate(book, 1)

    seen = _capture_complete(monkeypatch)
    # 2026-08-26：从 HTTP 改成直调（见 `_draft`）。**这一步不是形式**：
    # 不改的话它会静默地变成量「续写带不带总结」——那条链另有人钉
    # （`test_continuation_memory.py`），而这一条的主语是**整章起草**。
    memory = _draft(client, book, chapter=12)["memory"]

    assert memory["rolling_summaries"] == 1
    assert "萧决进屋，没点灯。" in "\n".join(m["content"] for m in seen[0])
    assert memory["unsummarized_chapters"] == [2]


def test_summarizing_a_chapter_without_text_refuses_instead_of_inventing_one(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有正文的章**拒绝**，不拿一段空白去换一次模型调用。

    从前这条钉的是那条手动路由的 404；路由 2026-08-25 删了，但**规矩没变**，
    只是唯一还会撞上它的人从作者变成了后台（保存触发 / 30 分钟扫描）。
    所以它改钉执行体本身——那才是这条规矩真正住的地方。
    """
    from novel_harness.draft.rolling_summary import SummaryChapterNotFound

    _configure(client)
    _stub_summarizer(monkeypatch)
    with pytest.raises(SummaryChapterNotFound):
        _generate(book, 9)
