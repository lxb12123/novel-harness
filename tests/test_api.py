"""FastAPI 壳 —— 错误映射（§1.3）、出参收窄（§1.2 陷阱）、declare 写路径闭环、R4 check。

**全部走真库 + 真 app**（`TestClient` + 一个建在 tmp 里的 SQLite 文件），零 mock。
壳的活是「把引擎函数包成 HTTP」，所以这里量的就是那层包装有没有把引擎的语义漏掉：

- 收窄：一个带 `props.twist` 的 Secret / 一个带 `props.plot_note` 的未来 Character，
  经过 resolve / subgraph / state **一个字都不许序列化出去**（`NodeRef` docstring 的实测
  泄漏形态）。这是本文件最重要的一组断言——它是那条产品核心主张在 HTTP 边界上的兑现。
- 错误映射：歧义**一律 409 + candidates，服务端绝不替作者挑**（同 DeclarationRefused）。
- 约束 10：declare 入参里没有章号，`valid_from` 由引语算出来——回执里那个数字是产物。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import (
    AliasKind,
    NodeLabel,
    NodeProps,
    NodeSpec,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# 泄漏物：这两个字符串是「作者写在节点 props 上、绝不该出接口」的东西（NodeRef docstring
# 的实测形态）。所有收窄断言都搜它们——出现即泄漏。
TWIST = "萧决是魔尊之子第200章揭晓"
PLOT_NOTE = "萧决在此被顾清音所杀"

# 两章正文。第 1 章一句唯一引语（declare 的料）；第 2 章一句重复引语（AmbiguousQuote 的料）。
BOOK = (
    "第一章 血脉\n"
    "\n"
    "萧决在青云城主府第一次听说了血脉秘密的真相。\n"
    "李管家什么也没说。\n"
    "\n"
    "第二章 试探\n"
    "\n"
    "他终于明白了。\n"
    "风起。他终于明白了。\n"
)

# 带场景块的一章，只写磁盘（check 读磁盘，不读 DB）。R4 拿它跑 location_conflict。
CH3_DISK = (
    "第三章 对峙\n"
    "\n"
    "## 场景 1\n"
    "<!-- nh: cast=萧决 loc=北荒 -->\n"
    "萧决独立北荒之巅。\n"
)


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    """建一个可控的库：花名册 + 一个泄漏 Secret + 一个未来 Character + 两章正文。

    Secret / 未来 Character 走 `store.upsert_node` 直接注入 props（`declare_node` 不收
    props，而收窄要测的正是这些 props 出不出得去）。别的节点走 `Ledger`（生产写路径）。
    """
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    ids: dict[str, str] = {}
    ids["萧决"] = ledger.declare_node(NodeLabel.CHARACTER, "萧决").id
    ids["李管家"] = ledger.declare_node(NodeLabel.CHARACTER, "李管家").id
    ids["青云城主府"] = ledger.declare_node(NodeLabel.LOCATION, "青云城主府").id
    ids["北荒"] = ledger.declare_node(NodeLabel.LOCATION, "北荒").id
    # 「师兄」→ 2 个人：AmbiguousName 的料（跨行事实，各行合法，查询时才算得出歧义）。
    ledger.declare_alias(of="萧决", surface="师兄", kind=AliasKind.TITLE)
    ledger.declare_alias(of="李管家", surface="师兄", kind=AliasKind.TITLE)

    # 泄漏 Secret：props.twist 挂在 extra="allow" 上。
    ids["血脉秘密"] = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.SECRET,
            name="血脉秘密",
            props=NodeProps.model_validate({"twist": TWIST}),
            secret=SecretDetail(),
        )
    ).id
    # 未来 Character：first_appears=200 + props.plot_note。
    ids["未来大能"] = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="未来大能",
            props=NodeProps.model_validate({"first_appears_chapter": 200, "plot_note": PLOT_NOTE}),
        )
    ).id
    conn.commit()

    # 两章进磁盘 + DB（locate/declare 搜 DB 快照，chapter_text 读磁盘）。
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK, encoding="utf-8")
    importer.import_book(store, pid, txt=txt, root=root)
    # 第三章只写磁盘，给 check 用。
    (root / "chapters" / "0003.md").write_text(CH3_DISK, encoding="utf-8")
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, **ids}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    # 必须 import 在设 env 之后不重要（app 在 import 时不连库），但 lifespan 在进 with 时
    # 跑 ensure_schema()，那时要 NH_DB 已就位。
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def _pid(book: dict[str, str]) -> str:
    return book["pid"]


# ══════════════════════════════════════════════════════════════════════════
# 出参收窄（§1.2 陷阱）—— 本文件的头等断言
# ══════════════════════════════════════════════════════════════════════════


def test_resolve_secret_does_not_leak_props(client: TestClient, book: dict[str, str]) -> None:
    r = client.get(f"/api/projects/{_pid(book)}/resolve", params={"surface": "血脉秘密"})
    assert r.status_code == 200
    assert TWIST not in r.text  # ← 核心：秘密的内容不出接口
    hit = r.json()["hits"][0]["node"]
    assert set(hit.keys()) == {"id", "label", "name"}  # 窄引用，没有 props


def test_subgraph_narrows_secret_node(client: TestClient, book: dict[str, str]) -> None:
    # 萧决 KNOWS 血脉秘密，所以子图里会有那个 Secret 节点——它必须被收窄。
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={
            "who": "萧决",
            "secret": "血脉秘密",
            "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
        },
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/projects/{_pid(book)}/subgraph",
        params={"center": book["萧决"], "chapter": 5, "hops": 2},
    )
    assert r.status_code == 200, r.text
    assert TWIST not in r.text
    secret = next(n for n in r.json()["nodes"] if n["id"] == book["血脉秘密"])
    assert "props" not in secret  # Secret 节点被收窄成 {id,label,name}


def test_future_character_narrowed_before_first_appears(
    client: TestClient, book: dict[str, str]
) -> None:
    # first_appears=200：第 5 章视角下它是「未来」，plot_note 不许出。
    r = client.get(
        f"/api/projects/{_pid(book)}/characters/{book['未来大能']}/state",
        params={"chapter": 5},
    )
    assert r.status_code == 200, r.text
    assert PLOT_NOTE not in r.text
    assert "props" not in r.json()["node"]


def test_future_character_full_after_first_appears(
    client: TestClient, book: dict[str, str]
) -> None:
    # 第 250 章视角下它已登场，不再是「未来」——props 正常序列化（收窄只针对未来/秘密）。
    r = client.get(
        f"/api/projects/{_pid(book)}/characters/{book['未来大能']}/state",
        params={"chapter": 250},
    )
    assert r.status_code == 200, r.text
    assert PLOT_NOTE in r.text  # 登场后不收窄，这是收窄「只收敏感节点」的反证


def test_matrix_never_leaks_secret_props(client: TestClient, book: dict[str, str]) -> None:
    # 矩阵出参本就是 NodeRef（引擎侧安全），这里钉住它不退化。
    r = client.get(
        f"/api/projects/{_pid(book)}/chapters/5/matrix",
        params={"cast": "萧决"},
    )
    assert r.status_code == 200, r.text
    assert TWIST not in r.text


# ══════════════════════════════════════════════════════════════════════════
# 错误映射（§1.3）
# ══════════════════════════════════════════════════════════════════════════


def test_project_not_found(client: TestClient) -> None:
    r = client.get("/api/projects/project:does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "project_not_found"


def test_unknown_name_404(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={"who": "查无此人", "secret": "血脉秘密", "quote": "随便"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "unknown_name"
    assert body["surface"] == "查无此人"


def test_ambiguous_name_409_with_candidates(client: TestClient, book: dict[str, str]) -> None:
    # 「师兄」→ 萧决 + 李管家。服务端绝不替作者挑——摆候选，409。
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={"who": "师兄", "secret": "血脉秘密", "quote": "随便"},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "ambiguous_name"
    names = {c["name"] for c in body["candidates"]}
    assert names == {"萧决", "李管家"}
    # 候选是窄引用，不含 props
    assert all(set(c.keys()) == {"id", "label", "name"} for c in body["candidates"])


def test_wrong_label_422(client: TestClient, book: dict[str, str]) -> None:
    # secret 位置收到一个 Location（青云城主府）。
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={"who": "萧决", "secret": "青云城主府", "quote": "随便"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "wrong_label"
    assert body["got"] == NodeLabel.LOCATION.value
    assert body["want"] == NodeLabel.SECRET.value


def test_quote_not_found_422(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={"who": "萧决", "secret": "血脉秘密", "quote": "这句话正文里根本没有"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "quote_not_found"


def test_ambiguous_quote_409_with_candidates(client: TestClient, book: dict[str, str]) -> None:
    # 「他终于明白了。」在第 2 章出现两次 → 系统不替作者挑，摆两个候选。
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={"who": "萧决", "secret": "血脉秘密", "quote": "他终于明白了。"},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "ambiguous_quote"
    assert len(body["candidates"]) == 2


def test_short_alias_rejected_422(client: TestClient, book: dict[str, str]) -> None:
    # 单字别名「决」+ 规则可用 → AliasSpec 的 validator 拒（ADR 0004）→ 剥壳后的人话。
    r = client.post(
        f"/api/projects/{_pid(book)}/aliases",
        json={"of": "萧决", "surface": "决", "usable_for_rules": True},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid"
    assert "决" in body["message"]  # 作者可读的那半句留住了


def test_sync_refused_on_two_headings_422(client: TestClient, book: dict[str, str]) -> None:
    # 把一章存成两个章标 → 切出 2 章 → SyncRefused → 422 带 path。
    r = client.put(
        f"/api/projects/{_pid(book)}/chapters/1/text",
        json={"markdown": "第一章 甲\n\n正文。\n\n第二章 乙\n\n正文。\n"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "sync_refused"
    assert body["path"] is not None


# ══════════════════════════════════════════════════════════════════════════
# declare 写路径闭环（约束 10：入参没有章号，valid_from 是产物）
# ══════════════════════════════════════════════════════════════════════════


def test_locate_returns_single_hit(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/locate",
        json={"quote": "萧决在青云城主府第一次听说了血脉秘密的真相。"},
    )
    assert r.status_code == 200
    cands = r.json()
    assert len(cands) == 1
    assert cands[0]["chapter_number"] == 1


def test_locate_zero_hits_is_empty_not_error(client: TestClient, book: dict[str, str]) -> None:
    # 0 命中不是错误，是合法答案「这句还不可用」——由前端渲染。
    r = client.post(
        f"/api/projects/{_pid(book)}/locate",
        json={"quote": "正文里没有的句子"},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_declare_knows_computes_valid_from(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/declare/knows",
        json={
            "who": "萧决",
            "secret": "血脉秘密",
            "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
        },
    )
    assert r.status_code == 200, r.text
    decl = r.json()
    # 章号是引语落在第 1 章的产物——请求体里从没有它。
    assert decl["edge"]["valid_from_chapter"] == 1
    assert decl["evidence"]["chapter_number"] == 1


def test_declare_where_auto_closes_previous(client: TestClient, book: dict[str, str]) -> None:
    # 引语都在第 1 章，两条 LOCATED_AT 同章 → 后一条撤回前一条（同章更正）。
    q = "萧决在青云城主府第一次听说了血脉秘密的真相。"
    first = client.post(
        f"/api/projects/{_pid(book)}/declare/where",
        json={"who": "萧决", "loc": "青云城主府", "quote": q},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/projects/{_pid(book)}/declare/where",
        json={"who": "萧决", "loc": "北荒", "quote": q},
    )
    assert second.status_code == 200, second.text
    # 招牌动作：旧位置被系统自动处理，回执如实报告（closed 或 retracted 至少一处）。
    decl = second.json()
    assert decl["closed"] or decl["retracted"]


def test_declare_node_secret_is_narrowed(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/nodes",
        json={"label": NodeLabel.SECRET.value, "name": "玄铁令下落", "description": "在北荒"},
    )
    assert r.status_code == 200, r.text
    node = r.json()
    assert set(node.keys()) == {"id", "label", "name"}  # Secret 收窄
    assert "在北荒" not in r.text  # description 不回吐


# ══════════════════════════════════════════════════════════════════════════
# R4 check —— 不返裸 list，静默的零和真的零分得开
# ══════════════════════════════════════════════════════════════════════════


def test_check_reports_rules_and_scene_count(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(f"/api/projects/{_pid(book)}/chapters/3/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_count"] == 1
    assert body["rules_run"]  # 「跑了几条规则」印出来，不静默
    assert isinstance(body["issues"], list)


def test_check_missing_chapter_404(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(f"/api/projects/{_pid(book)}/chapters/999/check")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "chapter_not_found"


# ══════════════════════════════════════════════════════════════════════════
# 只读面板仍然通（回归）
# ══════════════════════════════════════════════════════════════════════════


def test_roster_and_chapters(client: TestClient, book: dict[str, str]) -> None:
    roster = client.get(f"/api/projects/{_pid(book)}/roster")
    assert roster.status_code == 200
    assert TWIST not in roster.text  # 花名册也不漏秘密
    names = {n["name"] for n in roster.json()}
    assert {"萧决", "李管家", "青云城主府", "血脉秘密"} <= names

    chapters = client.get(f"/api/projects/{_pid(book)}/chapters")
    assert chapters.status_code == 200
    numbers = {c["number"] for c in chapters.json()}
    assert {1, 2, 3} <= numbers


def test_scenes_read(client: TestClient, book: dict[str, str]) -> None:
    # 第 3 章磁盘正文有 `## 场景 1` + cast=萧决 loc=北荒。
    r = client.get(f"/api/projects/{_pid(book)}/chapters/3/scenes")
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert len(scenes) == 1
    assert scenes[0]["number"] == 1
    assert scenes[0]["cast"] == ["萧决"]
    assert scenes[0]["loc"] == "北荒"


def test_scenes_write_updates_cast(client: TestClient, book: dict[str, str]) -> None:
    r = client.put(
        f"/api/projects/{_pid(book)}/chapters/3/scenes",
        json={"number": 1, "cast": ["萧决", "李管家"], "loc": "北荒", "goal": "对峙"},
    )
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert scenes[0]["cast"] == ["萧决", "李管家"]
    assert scenes[0]["goal"] == "对峙"
    # 再读一次磁盘确认落盘了（不是只在响应里）。
    again = client.get(f"/api/projects/{_pid(book)}/chapters/3/scenes")
    assert again.json()[0]["cast"] == ["萧决", "李管家"]


def test_scene_write_missing_number_404(client: TestClient, book: dict[str, str]) -> None:
    r = client.put(
        f"/api/projects/{_pid(book)}/chapters/3/scenes",
        json={"number": 99, "cast": ["萧决"]},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "scene_not_found"


def test_create_project_then_import(client: TestClient) -> None:
    # 非程序员的起步路径：建书 → 导入 TXT → 章节出现，全程不碰命令行。
    made = client.post("/api/projects", json={"name": "新书"})
    assert made.status_code == 200, made.text
    pid = made.json()["id"]

    imported = client.post(f"/api/projects/{pid}/import", json={"text": BOOK})
    assert imported.status_code == 200, imported.text
    assert imported.json()["chapter_count"] == 2  # BOOK 是两章

    chapters = client.get(f"/api/projects/{pid}/chapters")
    assert {c["number"] for c in chapters.json()} == {1, 2}


def test_create_project_empty_name_422(client: TestClient) -> None:
    r = client.post("/api/projects", json={"name": ""})
    assert r.status_code == 422
    assert r.json()["error"] == "bad_request"


def test_import_zero_chapters_refused_409(client: TestClient) -> None:
    # 零章不是「书是空的」，是「章标没认出来」——ImportRefused → 409，别当导入成功。
    made = client.post("/api/projects", json={"name": "无章标"})
    pid = made.json()["id"]
    r = client.post(f"/api/projects/{pid}/import", json={"text": "没有任何章标的一段散文。\n就这些。\n"})
    assert r.status_code == 409
    assert r.json()["error"] == "import_refused"


def test_openapi_schema_builds(client: TestClient) -> None:
    # 壳能生成 openapi（P0 前端要拿它跑 openapi-typescript）。
    r = client.get("/openapi.json")
    assert r.status_code == 200
    json.loads(r.text)
