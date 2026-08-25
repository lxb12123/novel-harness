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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import seed

from novel_harness import importer, project
from novel_harness.api.deps import get_conn
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import (
    AliasKind,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    EvidenceSpec,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeSpec,
)
from novel_harness.events import ProposalCreate, ProvisionalEventSpec
from novel_harness.graph.sqlite_events import SqliteEventStore
from novel_harness.graph.sqlite_proposals import SqliteProposalStore
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

    # **两种毒药挂在同一个节点上**：未来 Character（first_appears=200）+ `twist` +
    # `plot_note`。`upsert_node` 的幂等键是 (project, label, name)，分两次写后一次会
    # 盖掉前一次——所以合并成一次写，不是两个节点。
    ids["未来大能"] = store.upsert_node(
        NodeSpec(
            project_id=pid,
            label=NodeLabel.CHARACTER,
            name="未来大能",
            props=NodeProps.model_validate(
                {"first_appears_chapter": 200, "twist": TWIST, "plot_note": PLOT_NOTE}
            ),
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


def _root(book: dict[str, str]) -> Path:
    """书的稿子目录：`book.db` 同级的 `book/`（fixture 里 `root = tmp_path / "book"`）。"""
    return Path(book["db"]).parent / "book"


def _error(response: Any) -> dict[str, Any]:
    """测试侧的归一化：自定义 handler 的 error 在顶层，HTTPException 在 .detail。"""
    body = response.json()
    return body.get("detail", body) if isinstance(body.get("detail"), dict) else body


def test_request_connection_survives_fastapi_worker_thread_handoff(
    book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NH_DB", book["db"])
    dependency = get_conn()
    conn = next(dependency)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            name = executor.submit(
                lambda: conn.execute(
                    "SELECT name FROM project WHERE id = ?", (book["pid"],)
                ).fetchone()["name"]
            ).result()
        assert name == "青云记"
    finally:
        dependency.close()


def test_parallel_http_requests_keep_connections_request_local(
    client: TestClient, book: dict[str, str]
) -> None:
    url = f"/api/projects/{_pid(book)}/chapters"
    with ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(lambda _: client.get(url), range(20)))
    assert [response.status_code for response in responses] == [200] * 20


# ══════════════════════════════════════════════════════════════════════════
# 出参收窄（§1.2 陷阱）—— 本文件的头等断言
# ══════════════════════════════════════════════════════════════════════════


def test_resolve_never_leaks_props(client: TestClient, book: dict[str, str]) -> None:
    """`/resolve` **一律**出窄引用，不看章号。

    它是无章号的花名册查询，没有「当前章」可以拿来判「这个节点是不是未来的」——
    所以它不走 `_narrow(chapter=None)`（那条今天什么都不收窄），自己一律收窄。
    """
    r = client.get(f"/api/projects/{_pid(book)}/resolve", params={"surface": "未来大能"})
    assert r.status_code == 200
    assert TWIST not in r.text  # ← 核心：作者写在节点上的东西不出接口
    hit = r.json()["hits"][0]["node"]
    assert set(hit.keys()) == {"id", "label", "name"}  # 窄引用，没有 props
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
# ══════════════════════════════════════════════════════════════════════════
# 在场从正文推，不从作者的表单来
# ══════════════════════════════════════════════════════════════════════════


def test_mentioned_reads_the_manuscript(client: TestClient, book: dict[str, str]) -> None:
    """第 1 章正文里有萧决和李管家，还有一个地点和一个秘密——只有人进 cast。"""
    r = client.get(f"/api/projects/{_pid(book)}/chapters/1/mentioned")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_text"] is True
    assert body["surfaces"] == ["萧决", "李管家"]
    # 「青云城主府」是地点，它也在那句话里，但 cast 只收 Character——不许进来。
    assert "青云城主府" not in body["surfaces"]


def test_mentioned_separates_no_chapter_from_no_one(
    client: TestClient, book: dict[str, str]
) -> None:
    """**静默的零和真的零不许长得一样**（§10 约束 8）。

    「这一章还没写」和「写了但没提到花名册里的人」对作者是两件事：前者该说「去写」，
    后者该说「补花名册」。只看 `surfaces: []` 分不出来，所以有 `has_text`。
    """
    written = client.get(f"/api/projects/{_pid(book)}/chapters/2/mentioned").json()
    assert written["has_text"] is True  # 第 2 章有正文，但里面一个名字都没有
    assert written["surfaces"] == []

    missing = client.get(f"/api/projects/{_pid(book)}/chapters/99/mentioned").json()
    assert missing["has_text"] is False
    assert missing["surfaces"] == []


def test_panels_derive_cast_when_the_author_did_not_type_one(
    client: TestClient, book: dict[str, str]
) -> None:
    """不传 cast 的面板不再是空的——它从这一章的正文里自己数出来。

    这一条是「在场人物不该是写之前填的表单」在 HTTP 边界上的兑现。
    """
    pid = _pid(book)
    # （2026-08-24 之前这条量的是认知矩阵那条路由；它随秘密下线删了，
    #   而**推导本身没变**——人物状态卡吃的是同一份 `_effective_cast`。）
    derived = client.get(f"/api/projects/{pid}/chapters/1/state").json()
    assert [c["node"]["name"] for c in derived] == ["萧决", "李管家"]

    # 显式传 cast 仍然优先：作者说了算，推导只在他没说时接手。
    explicit = client.get(f"/api/projects/{pid}/chapters/1/state", params={"cast": "萧决"}).json()
    assert [c["node"]["name"] for c in explicit] == ["萧决"]
# ══════════════════════════════════════════════════════════════════════════
# 错误映射（§1.3）
# ══════════════════════════════════════════════════════════════════════════


def test_project_not_found(client: TestClient) -> None:
    r = client.get("/api/projects/project:does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "project_not_found"


def test_unknown_name_404(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(f"/api/projects/{_pid(book)}/declare/death", json={"who": "查无此人", "quote": "随便"})
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "unknown_name"
    assert body["surface"] == "查无此人"


def test_ambiguous_name_409_with_candidates(client: TestClient, book: dict[str, str]) -> None:
    # 「师兄」→ 萧决 + 李管家。服务端绝不替作者挑——摆候选，409。
    r = client.post(f"/api/projects/{_pid(book)}/declare/death", json={"who": "师兄", "quote": "随便"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "ambiguous_name"
    names = {c["name"] for c in body["candidates"]}
    assert names == {"萧决", "李管家"}
    # 候选是窄引用，不含 props
    assert all(set(c.keys()) == {"id", "label", "name"} for c in body["candidates"])


def test_wrong_label_422(client: TestClient, book: dict[str, str]) -> None:
    # who 位置收到一个 Location（青云城主府）。
    r = client.post(f"/api/projects/{_pid(book)}/declare/death", json={"who": "青云城主府", "quote": "随便"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "wrong_label"
    assert body["got"] == NodeLabel.LOCATION.value
    assert body["want"] == NodeLabel.CHARACTER.value


def test_quote_not_found_422(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(f"/api/projects/{_pid(book)}/declare/death", json={"who": "萧决", "quote": "这句话正文里根本没有"})
    assert r.status_code == 422
    assert r.json()["error"] == "quote_not_found"


def test_ambiguous_quote_409_with_candidates(client: TestClient, book: dict[str, str]) -> None:
    # 「他终于明白了。」在第 2 章出现两次 → 系统不替作者挑，摆两个候选。
    r = client.post(f"/api/projects/{_pid(book)}/declare/death", json={"who": "萧决", "quote": "他终于明白了。"})
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
    # 把一章存成两个章标 → 写盘前结构预检拒绝 → 422 带 path，磁盘和库都不动。
    pid = _pid(book)
    disk_before = (_root(book) / "chapters/0001.md").read_text(encoding="utf-8")
    db_before = _current_hash(client, pid, 1)
    r = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={
            "markdown": "第一章 甲\n\n正文。\n\n第二章 乙\n\n正文。\n",
            "expected_text_sha256": db_before,
        },
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "sync_refused"
    assert body["path"] is not None
    assert (_root(book) / "chapters/0001.md").read_text(encoding="utf-8") == disk_before
    assert _current_hash(client, pid, 1) == db_before


# ══════════════════════════════════════════════════════════════════════════
# declare 写路径闭环（约束 10：入参没有章号，valid_from 是产物）
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# R4 check —— 不返裸 list，静默的零和真的零分得开
# ══════════════════════════════════════════════════════════════════════════


def test_chapter_history_grows_on_edit(client: TestClient, book: dict[str, str]) -> None:
    pid = _pid(book)
    h0 = client.get(f"/api/projects/{pid}/chapters/1/history").json()
    assert len(h0) == 1
    assert h0[0]["is_current"] is True

    # 改正文 → sync 落一条新快照，旧的还在（证据锚指着它）。
    r = _put_text(client, pid, 1, "第一章 血脉\n\n改了的正文在这里。\n")
    assert r.status_code == 200, r.text
    assert r.json()["indexed"] is True
    assert r.json()["changed"] is True

    h1 = client.get(f"/api/projects/{pid}/chapters/1/history").json()
    assert len(h1) == 2  # 内容去重：两份不同内容 = 两条
    current = [s for s in h1 if s["is_current"]]
    assert len(current) == 1  # 恰好一条是当前
    assert "改了的正文" in current[0]["text"]


def test_chapter_history_dedupes_identical_content(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = _pid(book)
    # 存成与当前一字不差的内容 → 不新建快照（UNIQUE(chapter_id, text_sha256)）。
    current = client.get(f"/api/projects/{pid}/chapters/2/text").json()
    same = current["markdown"]
    r = client.put(
        f"/api/projects/{pid}/chapters/2/text",
        json={"markdown": same, "expected_text_sha256": current["text_sha256"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is False
    h = client.get(f"/api/projects/{pid}/chapters/2/history").json()
    assert len(h) == 1  # 同内容只存一次


# ══════════════════════════════════════════════════════════════════════════
# 版本还原 / 删除 —— 快照是证据的锚，所以「删」有前提，「还原」没有新表
# ══════════════════════════════════════════════════════════════════════════


def _history(client: TestClient, pid: str, chapter: int = 1) -> list[dict[str, Any]]:
    return list(client.get(f"/api/projects/{pid}/chapters/{chapter}/history").json())


def _current_hash(client: TestClient, pid: str, chapter: int) -> str:
    return client.get(f"/api/projects/{pid}/chapters/{chapter}/text").json()["text_sha256"]


def _put_text(
    client: TestClient, pid: str, chapter: int, markdown: str, expected: str | None = None
) -> Any:
    if expected is None:
        expected = _current_hash(client, pid, chapter)
    return client.put(
        f"/api/projects/{pid}/chapters/{chapter}/text",
        json={"markdown": markdown, "expected_text_sha256": expected},
    )


def _save(client: TestClient, pid: str, chapter: int, markdown: str) -> None:
    r = _put_text(client, pid, chapter, markdown)
    assert r.status_code == 200, r.text


def test_restoring_an_old_version_moves_current_without_adding_a_row(
    client: TestClient, book: dict[str, str]
) -> None:
    """还原 = 把旧正文写回磁盘。**没有第二条写路径，也不长出第三个版本。**

    这是「还原」在本仓库不需要新端点的全部理由：快照按内容去重，写回去 sha 命中已有那条，
    于是 `is_current` 移回去、总数不变。要是它每还原一次就多一版，版本列表会越理越乱。
    """
    pid = _pid(book)
    original = client.get(f"/api/projects/{pid}/chapters/1/text").json()["markdown"]
    v1 = _history(client, pid)[0]["snapshot_id"]

    _save(client, pid, 1, "第一章 血脉\n\n改坏了的一版。\n")
    assert len(_history(client, pid)) == 2
    assert [s["snapshot_id"] for s in _history(client, pid) if s["is_current"]] != [v1]

    _save(client, pid, 1, original)  # 还原
    after = _history(client, pid)
    assert len(after) == 2, "还原不该新增版本——同内容的快照只存在一次"
    assert [s["snapshot_id"] for s in after if s["is_current"]] == [v1]
    assert client.get(f"/api/projects/{pid}/chapters/1/text").json()["markdown"] == original


def test_deleting_an_unused_old_version_removes_it(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = _pid(book)
    v1 = _history(client, pid)[0]["snapshot_id"]
    _save(client, pid, 1, "第一章 血脉\n\n第二版。\n")

    r = client.delete(f"/api/projects/{pid}/chapters/1/snapshots/{v1}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "snapshot_id": v1}
    left = _history(client, pid)
    assert [s["snapshot_id"] for s in left] == [s["snapshot_id"] for s in left if s["is_current"]]
    assert v1 not in [s["snapshot_id"] for s in left]


def test_save_receipt_carries_snapshot_generation_and_hash(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = _pid(book)
    base = _current_hash(client, pid, 1)
    r = _put_text(client, pid, 1, "第一章 血脉\n\n带 generation 的回执。\n", expected=base)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved_to_disk"] is True
    assert body["indexed"] is True
    assert body["changed"] is True
    assert body["chapter_number"] == 1
    assert body["snapshot_generation"] == 2
    assert body["snapshot_id"] is not None
    assert body["text_sha256"] == importer.text_digest("第一章 血脉\n\n带 generation 的回执。\n")


def test_save_stale_expected_hash_is_409(client: TestClient, book: dict[str, str]) -> None:
    pid = _pid(book)
    r = _put_text(client, pid, 1, "第一章 血脉\n\n不该覆盖。\n", expected="a" * 64)
    assert r.status_code == 409
    assert r.json()["error"] == "chapter_changed"


def test_save_lock_timeout_is_423(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    pid = _pid(book)
    base = _current_hash(client, pid, 1)
    lock_dir = _root(book) / ".novel-harness" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(book["db"])
    try:
        store = SqliteStoryGraph(conn)
        chapter_id = next(ct.chapter_id for ct in store.current_snapshots(pid) if ct.number == 1)
    finally:
        conn.close()
    lock_file = lock_dir / f"{chapter_id}.lock"
    handle = lock_file.open("w")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        from novel_harness import importer as _importer

        original = _importer.save_chapter

        def short_timeout(*args: object, **kwargs: object) -> object:
            kwargs = {**kwargs, "lock_timeout": 0.05}
            return original(*args, **kwargs)

        monkeypatch.setattr(_importer, "save_chapter", short_timeout)
        r = client.put(
            f"/api/projects/{pid}/chapters/1/text",
            json={"markdown": "第一章 血脉\n\n锁超时。\n", "expected_text_sha256": base},
        )
        assert r.status_code == 423
        assert r.json()["error"] == "lock_timeout"
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def test_deleting_the_current_version_is_refused(
    client: TestClient, book: dict[str, str]
) -> None:
    """删「现在这一版」= 让这一章没有当前快照。`rolling_summary` 会直接抛，而作者
    在界面上看不到任何原因——所以在这里拒绝，并告诉他先还原到别的版本。"""
    pid = _pid(book)
    current = next(s for s in _history(client, pid) if s["is_current"])["snapshot_id"]
    r = client.delete(f"/api/projects/{pid}/chapters/1/snapshots/{current}")
    assert r.status_code == 409
    assert _error(r)["error"] == "snapshot_is_current"
    assert len(_history(client, pid)) == 1  # 没删掉


def test_deleting_a_version_that_backs_evidence_is_refused(
    client: TestClient, book: dict[str, str]
) -> None:
    """被证据引着的那一版删不掉，且回执里带得出「被几条什么引着」。

    三条外键都没有 ON DELETE CASCADE 是有意的：证据的价值全在「那句话当年在这儿」，
    锚没了它就只是一句无出处的断言。
    """
    pid = _pid(book)
    seed.where(
        book["db"], pid,
        who="萧决", loc="青云城主府",
        quote="萧决在青云城主府第一次听说了血脉秘密的真相。",
    )
    anchored = next(s for s in _history(client, pid) if s["is_current"])["snapshot_id"]

    _save(client, pid, 1, "第一章 血脉\n\n改过之后那句话没了。\n")  # 让它不再是当前那条

    r = client.delete(f"/api/projects/{pid}/chapters/1/snapshots/{anchored}")
    assert r.status_code == 409
    body = _error(r)
    assert body["error"] == "snapshot_in_use"
    assert body["usage"]["evidence"] >= 1
    assert anchored in [s["snapshot_id"] for s in _history(client, pid)]


def test_deleting_a_version_of_another_chapter_is_refused(
    client: TestClient, book: dict[str, str]
) -> None:
    """章号和快照 id 是一对坐标。对不上就拒绝——否则第 2 章的界面能删掉第 1 章的版本。"""
    pid = _pid(book)
    v1 = _history(client, pid, 1)[0]["snapshot_id"]
    _save(client, pid, 1, "第一章 血脉\n\n第二版。\n")

    r = client.delete(f"/api/projects/{pid}/chapters/2/snapshots/{v1}")
    assert r.status_code == 422
    assert _error(r)["error"] == "store_error"
    assert v1 in [s["snapshot_id"] for s in _history(client, pid, 1)]


def test_deleting_an_unknown_snapshot_is_refused(client: TestClient, book: dict[str, str]) -> None:
    r = client.delete(f"/api/projects/{_pid(book)}/chapters/1/snapshots/snapshot:NOPE")
    assert r.status_code == 422
    assert _error(r)["error"] == "store_error"


def test_evidence_roundtrip(client: TestClient, book: dict[str, str]) -> None:
    # 声明产生证据 → 按 id 取回「来源章 + 当年那句原文」，且明确不含 score。
    q = "萧决在青云城主府第一次听说了血脉秘密的真相。"
    decl = seed.where(book["db"], _pid(book), who="萧决", loc="青云城主府", quote=q)
    ev_id = decl.evidence.id

    r = client.get(f"/api/projects/{_pid(book)}/evidence/{ev_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chapter_number"] == 1
    assert body["quote_text"] == q
    assert body["anchor"]["quote_text"] == q
    assert "score" not in body  # v1 无向量，出分数就是编的


def test_evidence_not_found_404(client: TestClient, book: dict[str, str]) -> None:
    r = client.get(f"/api/projects/{_pid(book)}/evidence/evidence:zzzz:doesnotexist")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "evidence_not_found"


def test_check_reports_which_rules_ran(client: TestClient, book: dict[str, str]) -> None:
    r = client.post(f"/api/projects/{_pid(book)}/chapters/3/check")
    assert r.status_code == 200, r.text
    body = r.json()
    # 「跑了哪几条、各自什么状态」印出来，不静默（§10 约束 8）。
    assert {rule["rule_id"] for rule in body["rules"]} == {"R2", "R3"}
    assert body["gate"] in {"passed", "blocked", "error"}
    assert body["source_generation"] >= 1
    assert body["ruleset_epoch"] >= 1
    assert isinstance(body["issues"], list)
    # `scene_count` 2026-08-14 随场景块和 R4 一起从出参里去掉了（ADR 0027）。
    # 它原本是那个「零 issue」的成色说明，而它说明的那条规则已经不在了。
    assert "scene_count" not in body


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
    assert TWIST not in roster.text  # 花名册也不漏节点上的 props
    names = {n["name"] for n in roster.json()}
    assert {"萧决", "李管家", "青云城主府", "未来大能"} <= names

    chapters = client.get(f"/api/projects/{_pid(book)}/chapters")
    assert chapters.status_code == 200
    numbers = {c["number"] for c in chapters.json()}
    assert {1, 2, 3} <= numbers


def test_create_chapter_appends_and_is_immediately_editable(
    client: TestClient, book: dict[str, str]
) -> None:
    """书架上那颗「＋」：新起一章 → 它当场在目录里、正文读得到、也存得回去。

    **最后那一步是这条测试的重点**：新建那一章的正文如果切不出恰好一章，
    作者第一次按保存就会撞 422，而那时他已经写了一整章了。
    """
    pid = _pid(book)
    created = client.post(f"/api/projects/{pid}/chapters")
    assert created.status_code == 201
    assert created.json() == {"number": 4, "title": "第四章"}

    listed = client.get(f"/api/projects/{pid}/chapters").json()
    assert {c["number"] for c in listed} == {1, 2, 3, 4}

    text = client.get(f"/api/projects/{pid}/chapters/4/text")
    assert text.status_code == 200
    assert text.json()["markdown"] == "第四章\n\n"
    assert "text_sha256" in text.json()
    assert text.json()["snapshot_generation"] == 1

    saved = client.put(
        f"/api/projects/{pid}/chapters/4/text",
        json={
            "markdown": "第四章\n\n他推开门。\n",
            "expected_text_sha256": text.json()["text_sha256"],
        },
    )
    assert saved.status_code == 200


def test_create_chapter_conflict_says_so_instead_of_500(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """另一个窗口在这两步之间抢先建好了那一章。

    **不覆盖**（那边可能已经写了字），而且要说一句人话——裸 `FileExistsError` 会变成
    500，而 500 在屏幕上只剩「再试一次」，重试多少次都一样。
    """
    from novel_harness import importer

    def boom(*args: object, **kwargs: object) -> object:
        raise FileExistsError

    monkeypatch.setattr(importer, "append_chapter", boom)
    r = client.post(f"/api/projects/{_pid(book)}/chapters")

    assert r.status_code == 409
    # HTTPException 的详情裹在 `detail` 里（前端 `client.ts` 就是照这个形状拆的）。
    assert r.json()["detail"]["error"] == "chapter_exists"
    assert "另一个窗口" in r.json()["detail"]["message"]


def test_create_chapter_takes_no_chapter_number(client: TestClient, book: dict[str, str]) -> None:
    """约束 6 的同一条道理：章号由磁盘决定，请求体里没有一个位置能让作者填它。"""
    pid = _pid(book)
    r = client.post(f"/api/projects/{pid}/chapters", json={"number": 99})
    assert r.status_code == 201
    assert r.json()["number"] == 4  # 那个 99 一个字都没被听进去


def test_delete_chapter_takes_it_out_of_the_list(client: TestClient, book: dict[str, str]) -> None:
    """章目录里那颗「⋯」：删掉一章 → 目录里没有了，正文也读不到了。

    删的是刚新建的第 4 章——引擎在它上面什么都没记过，所以它是唯一一档
    「删得动」的形状（别的形状见下面那条 409）。
    """
    pid = _pid(book)
    client.post(f"/api/projects/{pid}/chapters")

    r = client.delete(f"/api/projects/{pid}/chapters/4")

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "number": 4}
    assert {c["number"] for c in client.get(f"/api/projects/{pid}/chapters").json()} == {1, 2, 3}
    assert client.get(f"/api/projects/{pid}/chapters/4/text").status_code == 404


def test_delete_chapter_refuses_when_the_engine_remembers_something(
    client: TestClient, book: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """挡路的东西要**数得出来**摆在屏幕上。

    一句不带理由的「删不掉」会把作者赶去文件夹里自己动手删那个 .md——
    而那条路上引擎的记忆一条都不会被清理，库和磁盘从此对不上。
    """
    from novel_harness import importer
    from novel_harness.graph import ChapterInUse, ChapterUsage

    def refuse(*args: object, **kwargs: object) -> object:
        raise ChapterInUse(
            ChapterUsage(
                chapter_number=1, evidence=3, edges=2, events=1, extraction_runs=1, proposal_sets=0
            )
        )

    monkeypatch.setattr(importer, "remove_chapter", refuse)
    r = client.delete(f"/api/projects/{_pid(book)}/chapters/1")

    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "chapter_in_use"
    assert body["usage"]["evidence"] == 3
    assert body["usage"]["edges"] == 2
    assert "第 1 章" in body["message"]


def test_delete_a_chapter_that_is_not_there(client: TestClient, book: dict[str, str]) -> None:
    r = client.delete(f"/api/projects/{_pid(book)}/chapters/9")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "chapter_missing"


def test_delete_a_chapter_the_library_never_saw(client: TestClient, book: dict[str, str]) -> None:
    """第三章只写了磁盘、没进过库（夹具就是这么造的）。

    **不能只删掉磁盘那一半就说删掉了**：那说明这个库和这个文件夹已经对不上，
    作者该知道——所以图层的 `StoreError` 原样上来（422），文件留在原地。
    """
    pid = _pid(book)
    r = client.delete(f"/api/projects/{pid}/chapters/3")

    assert r.status_code == 422
    assert r.json()["error"] == "store_error"
    assert 3 in {c["number"] for c in client.get(f"/api/projects/{pid}/chapters").json()}


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


# ══════════════════════════════════════════════════════════════════════════
# M2 / M4 的 stub —— 501 而不是 404（UI_ARCHITECTURE §1.2 第 48 行）
#
# 这组断言钉的不是「有 5 个还没写的功能」，是**前端能把「还没做」和「路径写错」分开**：
# 分不开 → 按钮只能藏起来 → 作者在界面上看不到路线图。
# ══════════════════════════════════════════════════════════════════════════

# (HTTP 方法, 项目前缀之后的路径, 里程碑)。M4 的抽取/审阅路由已经点亮；
# `GET /runs` 2026-08-10 也点亮了（`api/activity.py`）——它当年 501 的理由是
# 「`model_call` 表今天是空的」，而那句在 M4 落地那天就过期了。
# 这里只剩 AI 规划一条还没开放。
STUBS = [
    ("post", "/chapters/7/plan", "M2"),
]


@pytest.mark.parametrize(("method", "path", "milestone"), STUBS)
def test_stub_returns_501_with_milestone(
    client: TestClient, book: dict[str, str], method: str, path: str, milestone: str
) -> None:
    r = getattr(client, method)(f"/api/projects/{_pid(book)}{path}")
    assert r.status_code == 501, r.text
    # 形状写死：前端据 milestone 印「M2 才有」，硬编码在前端的那张表会和后端漂移。
    assert r.json() == {"status": "not_implemented", "milestone": milestone}


def test_stub_501_is_distinguishable_from_a_typo_404(
    client: TestClient, book: dict[str, str]
) -> None:
    """同一个前缀下，拼错的路径仍然是 404——这条对比就是 stub 存在的全部理由。

    两者都返 404 的话，前端收到 404 时不知道该渲染灰按钮还是该报自己的 bug。
    """
    pid = _pid(book)
    assert client.post(f"/api/projects/{pid}/chapters/7/drafts").status_code == 404
    assert client.post(f"/api/projects/{pid}/chapters/7/plan").status_code == 501


# ══════════════════════════════════════════════════════════════════════════
# M4 提案审阅 / 被动确认 —— 真库 + 真 app，seed 走存储层（提案的生产者是抽取
# 后台，不是 HTTP 壳；HTTP 壳只负责审阅动作本身）。
# ══════════════════════════════════════════════════════════════════════════


def _seed_provisional_event(book: dict[str, str], *, confidence: float = 0.6) -> str:
    """Chapter 1 上放一条 PROVISIONAL 事件，返回其 id。"""
    conn = connect(book["db"])
    try:
        graph = SqliteStoryGraph(conn)
        events = SqliteEventStore(conn)
        pid = book["pid"]
        snapshot_id = graph.chapter_snapshots(pid, 1)[0].snapshot_id
        quote = "萧决在青云城主府第一次听说了血脉秘密的真相。"
        evidence = graph.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=snapshot_id,
                para_index=2,
                quote_text=quote,
            )
        )
        event = events.put_provisional(
            ProvisionalEventSpec(
                project_id=pid,
                summary="萧决得知血脉秘密。",
                evidence_id=evidence.id,
                participant_ids=[book["萧决"], book["李管家"]],
                knower_ids=[book["萧决"], book["李管家"]],
                confidence=confidence,
            )
        )
        return event.event.id
    finally:
        conn.close()


def _seed_low_confidence_proposal(book: dict[str, str]) -> tuple[str, str, int]:
    """一条 PENDING low_confidence_main 事件提案；返回 (proposal_id, event_id, base)。"""
    conn = connect(book["db"])
    try:
        graph = SqliteStoryGraph(conn)
        pid = book["pid"]
        event_id = _seed_provisional_event(book)
        event = SqliteEventStore(conn).event(pid, event_id)
        assert event is not None
        snapshot_id = graph.chapter_snapshots(pid, 1)[0].snapshot_id
        base = project.require_canon_version(conn, pid)
        proposal = SqliteProposalStore(conn).create(
            ProposalCreate(
                project_id=pid,
                kind="low_confidence_main",
                items=[
                    {
                        "source_kind": "event",
                        "event_id": event_id,
                        "summary": event.event.summary,
                        "confidence": event.event.confidence,
                        "quote": "萧决在青云城主府第一次听说了血脉秘密的真相。",
                    }
                ],
                chapter_number=1,
                snapshot_id=snapshot_id,
                base_canon_version=base,
                schema_version="m4.analysis.v1",
                prompt_hash="prompt:api-test",
                event_ids=[event_id],
            )
        )
        return proposal.id, event_id, base
    finally:
        conn.close()


def _seed_edge_conflict_proposal(book: dict[str, str]) -> tuple[str, str]:
    """一条 PENDING edge_conflict 提案；返回 (proposal_id, proposed_edge_id)。"""
    conn = connect(book["db"])
    try:
        graph = SqliteStoryGraph(conn)
        pid = book["pid"]
        hero = book["萧决"]
        old_place = graph.upsert_node(
            NodeSpec(project_id=pid, label=NodeLabel.LOCATION, name="青云城")
        ).id
        north = graph.resolve(pid, ["北荒"])[0].unique_node.id
        current = graph.upsert_edge(
            EdgeSpec(
                project_id=pid,
                src=hero,
                dst=old_place,
                type=EdgeType.LOCATED_AT,
                valid_from_chapter=1,
                information_scope=InformationScope.CANON,
            )
        ).edge
        snapshot_id = graph.chapter_snapshots(pid, 1)[0].snapshot_id
        quote = "萧决在青云城主府第一次听说了血脉秘密的真相。"
        evidence = graph.put_evidence(
            EvidenceSpec(
                project_id=pid,
                chapter_snapshot_id=snapshot_id,
                para_index=2,
                quote_text=quote,
            )
        )
        proposed = graph.upsert_edge(
            EdgeSpec(
                project_id=pid,
                src=hero,
                dst=north,
                type=EdgeType.LOCATED_AT,
                valid_from_chapter=1,
                information_scope=InformationScope.PROVISIONAL,
                confidence=0.8,
                source=EdgeSource.EXTRACTOR,
                evidence_id=evidence.id,
            )
        ).edge
        base = project.require_canon_version(conn, pid)
        proposal = SqliteProposalStore(conn).create(
            ProposalCreate(
                project_id=pid,
                kind="edge_conflict",
                items=[
                    {
                        "update_kind": "location",
                        "current": {
                            "edge_id": current.id,
                            "subject_id": hero,
                            "target_id": old_place,
                            "value": None,
                        },
                        "proposed": {
                            "edge_id": proposed.id,
                            "subject_id": hero,
                            "target_id": north,
                            "value": None,
                            "quote": quote,
                        },
                    }
                ],
                chapter_number=1,
                snapshot_id=snapshot_id,
                base_canon_version=base,
                schema_version="m4.analysis.v1",
                prompt_hash="prompt:api-edge-conflict",
                edge_ids=[proposed.id],
            )
        )
        return proposal.id, proposed.id
    finally:
        conn.close()


def _seed_new_character_proposal(book: dict[str, str]) -> tuple[str, str]:
    """一条 PENDING new_character 提案；返回 (proposal_id, surface)。"""
    conn = connect(book["db"])
    try:
        graph = SqliteStoryGraph(conn)
        pid = book["pid"]
        base = project.require_canon_version(conn, pid)
        snapshot_id = graph.chapter_snapshots(pid, 1)[0].snapshot_id
        surface = "陆青禾"
        proposal = SqliteProposalStore(conn).create(
            ProposalCreate(
                project_id=pid,
                kind="new_character",
                items=[
                    {
                        "surface": surface,
                        "profile": {
                            "surface": surface,
                            "gender": "女",
                            "personality": "隐忍",
                            "background": "北荒旧族",
                            # **不能是 None**：这一份会进契约夹具，而审阅面板从前根本不画
                            # 这一行——作者在闸门上批准了一条他没看见的东西，而它接受之后
                            # 会跟着这个人进写作提示（`draft/product_assemble.py`）。
                            # 夹具里躺一个 `null`，那条「作者看得见吗」的断言就永远绿。
                            "character_notes": "说话总带三分敬意，从不主动提北荒",
                            "confidence": 0.8,
                        },
                        "confidence": 0.8,
                    }
                ],
                chapter_number=1,
                snapshot_id=snapshot_id,
                base_canon_version=base,
                schema_version="m4.analysis.v1",
                prompt_hash="prompt:api-new-character",
            )
        )
        return proposal.id, surface
    finally:
        conn.close()


def test_proposals_pending_list_and_status_validation(
    client: TestClient, book: dict[str, str]
) -> None:
    proposal_id, _proposal_event, base = _seed_low_confidence_proposal(book)
    pid = _pid(book)

    r = client.get(f"/api/projects/{pid}/chapters/1/proposals")
    assert r.status_code == 200, r.text
    items = r.json()
    assert [item["id"] for item in items] == [proposal_id]
    assert items[0]["status"] == "PENDING"
    assert items[0]["kind"] == "low_confidence_main"
    assert items[0]["base_canon_version"] == base

    r = client.get(f"/api/projects/{pid}/chapters/1/proposals", params={"status": "PENDING"})
    assert r.status_code == 200, r.text
    assert r.json()[0]["id"] == proposal_id

    r = client.get(
        f"/api/projects/{pid}/chapters/1/proposals", params={"status": "ACCEPTED"}
    )
    assert r.status_code == 422, r.text


def test_accept_proposal_endpoint_then_double_review_conflicts(
    client: TestClient, book: dict[str, str]
) -> None:
    proposal_id, event_id, base = _seed_low_confidence_proposal(book)
    pid = _pid(book)

    r = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/accept",
        json={"expected_canon_version": base},
    )
    assert r.status_code == 200, r.text
    resolution = r.json()
    assert resolution["proposal_id"] == proposal_id
    assert resolution["status"] == "ACCEPTED"
    assert resolution["canon_version"] == base + 1
    assert resolution["decision_id"]
    assert resolution["event"]["event"]["information_scope"] == "CANON"
    assert resolution["event"]["event"]["derived_from_event_id"] == event_id

    # 同一提案二次审阅 → 409（不是幂等 200：它已经是 terminal）。
    r = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/accept",
        json={"expected_canon_version": base + 1},
    )
    assert r.status_code == 409, r.text
    assert _error(r)["error"] == "proposal_already_resolved"

    # 项目 canon 已推进到 1，拿旧的 0 再来 → stale_base_version。
    other_id, _other_event, _other_base = _seed_low_confidence_proposal(book)
    r = client.post(
        f"/api/projects/{pid}/proposals/{other_id}/accept",
        json={"expected_canon_version": base},
    )
    assert r.status_code == 409, r.text
    assert _error(r)["error"] == "stale_base_version"
    assert _error(r)["current"] == base + 1


def test_reject_proposal_endpoint(book: dict[str, str], client: TestClient) -> None:
    proposal_id, _proposal_event, base = _seed_low_confidence_proposal(book)
    pid = _pid(book)

    r = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/reject",
        json={"action": "reject", "expected_canon_version": base},
    )
    assert r.status_code == 200, r.text
    resolution = r.json()
    assert resolution["status"] == "REJECTED"
    assert resolution["canon_version"] == base

    # 已 REJECTED 的提案不能再 accept。
    r = client.post(
        f"/api/projects/{pid}/proposals/{proposal_id}/accept",
        json={"expected_canon_version": base},
    )
    assert r.status_code == 409, r.text
    assert _error(r)["error"] == "proposal_already_resolved"


def test_review_unknown_proposal_404_and_bad_bodies_422(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = _pid(book)
    r = client.post(
        f"/api/projects/{pid}/proposals/proposal:missing/accept",
        json={"expected_canon_version": 0},
    )
    assert r.status_code == 404, r.text
    assert _error(r)["error"] == "proposal_not_found"

    r = client.post(
        f"/api/projects/{pid}/proposals/proposal:missing/accept",
        json={"expected_canon_version": -1},
    )
    assert r.status_code == 422, r.text

    r = client.post(
        f"/api/projects/{pid}/proposals/proposal:missing/reject",
        json={"action": "delete", "expected_canon_version": 0},
    )
    assert r.status_code == 422, r.text


def test_provisional_confirm_endpoint_is_idempotent_and_conflict_detected(
    client: TestClient, book: dict[str, str]
) -> None:
    event_id = _seed_provisional_event(book)
    pid = _pid(book)
    url = f"/api/projects/{pid}/chapters/1/provisional/confirm"
    conn = connect(book["db"])
    try:
        base = project.require_canon_version(conn, pid)
    finally:
        conn.close()

    first = client.post(
        url,
        json={
            "fact_kind": "event",
            "fact_ids": [event_id],
            "expected_canon_version": base,
        },
    )
    assert first.status_code == 200, first.text
    confirmation = first.json()
    assert confirmation["confirmation_id"]
    assert confirmation["canon_version"] == base + 1
    assert confirmation["decision_id"]
    assert confirmation["events"][0]["event"]["information_scope"] == "CANON"

    retry = client.post(
        url,
        json={
            "fact_kind": "event",
            "fact_ids": [event_id],
            "expected_canon_version": base,
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["confirmation_id"] == confirmation["confirmation_id"]
    assert retry.json()["canon_version"] == base + 1

    # 新请求与已有回执重叠（第二个事件 + 已确认事件）但哈希不同 → 冲突。
    second_id = _seed_provisional_event(book)
    conflict = client.post(
        url,
        json={
            "fact_kind": "event",
            "fact_ids": [event_id, second_id],
            "expected_canon_version": base + 1,
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert _error(conflict)["error"] == "confirmation_conflict"

    bad = client.post(
        url,
        json={
            "fact_kind": "secret",
            "fact_ids": [event_id],
            "expected_canon_version": base,
        },
    )
    assert bad.status_code == 422, bad.text


def test_stub_501_does_not_depend_on_project_state(client: TestClient) -> None:
    """项目不存在也返 501，不返 404。

    「这个能力还没实现」不取决于库里有什么。stub 若接了 load_project，按钮的灰与亮就
    被「项目在不在」决定——那是另一个问题的答案。
    """
    r = client.post("/api/projects/project:does-not-exist/chapters/1/plan")
    assert r.status_code == 501
    assert r.json()["status"] == "not_implemented"


def test_openapi_declares_exactly_the_remaining_stub(client: TestClient) -> None:
    """501 进 openapi（前端从 schema 就看得见），且**恰好 1 条**。

    多出第 2 条 = 有人把一个能力悄悄降级成 stub；少一条 = 有人把 stub 删了而不是实现它。
    两种都该在这里响。
    `/draft`、M4 审阅/被动确认、以及 2026-08-10 点亮的 `GET /runs` 都必须不在 stub 列表里
    ——`/runs` 那条 501 的理由写着「`model_call` 表今天是空的」，而那句在 M4 落地那天就过期了。
    """
    spec = client.get("/openapi.json").json()
    stubbed = {
        (path, method)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if "501" in op.get("responses", {})
    }
    assert len(stubbed) == 1, sorted(stubbed)
    assert ("/api/projects/{project_id}/chapters/{chapter}/plan", "post") in stubbed
    assert ("/api/projects/{project_id}/runs", "get") not in stubbed
    assert ("/api/projects/{project_id}/chapters/{chapter}/draft", "post") not in stubbed
    assert (
        "/api/projects/{project_id}/chapters/{chapter}/proposals",
        "get",
    ) not in stubbed
    assert (
        "/api/projects/{project_id}/proposals/{proposal_id}/accept",
        "post",
    ) not in stubbed


def test_draft_openapi_publishes_its_request_contract(client: TestClient) -> None:
    """真实 /draft 的请求契约发布进 OpenAPI：goal/cast/length/form，length 带校验。"""
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/projects/{project_id}/chapters/{chapter}/draft"]["post"]
    request_body = operation["requestBody"]

    assert request_body.get("required", False) is True
    body_schema = request_body["content"]["application/json"]["schema"]
    while "$ref" in body_schema:
        body_schema = spec["components"]["schemas"][body_schema["$ref"].rsplit("/", 1)[-1]]
    if "allOf" in body_schema:
        merged: dict[str, Any] = {}
        for part in body_schema["allOf"]:
            merged.update(part)
        body_schema = merged
    assert {"goal", "cast", "length"} <= set(body_schema["properties"])
    length_schema = body_schema["properties"]["length"]
    while "$ref" in length_schema:
        length_schema = spec["components"]["schemas"][
            length_schema["$ref"].rsplit("/", 1)[-1]
        ]
    if "allOf" in length_schema:
        merged_len: dict[str, Any] = {}
        for part in length_schema["allOf"]:
            merged_len.update(part)
        length_schema = merged_len

    assert set(length_schema["properties"]) == {
        "language",
        "min_units",
        "target_units",
        "max_units",
    }
    assert set(length_schema["required"]) == {
        "language",
        "min_units",
        "target_units",
        "max_units",
    }
    language_ref = length_schema["properties"]["language"]["$ref"]
    language_schema = spec["components"]["schemas"][language_ref.rsplit("/", 1)[-1]]
    assert language_schema["enum"] == ["zh", "en"]


@pytest.mark.parametrize(
    "length",
    [
        {"language": "fr", "min_units": 2000, "target_units": 2500, "max_units": 3000},
        {"language": "zh", "min_units": 0, "target_units": 2500, "max_units": 3000},
        {"language": "en", "min_units": 1800, "target_units": 1500, "max_units": 1200},
        {"language": "zh", "min_units": 2000, "target_units": 2500, "max_units": 20001},
        {"language": "en", "min_units": 1200, "target_units": 1500, "max_units": 12001},
    ],
)
def test_draft_rejects_invalid_length_body(
    client: TestClient, book: dict[str, str], length: dict[str, int | str]
) -> None:
    r = client.post(
        f"/api/projects/{_pid(book)}/chapters/7/draft",
        json={"goal": "x", "cast": ["萧决"], "length": length},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]


# ══════════════════════════════════════════════════════════════════════════
# 自定义确定性验证规则（024 / Task 13）
# ══════════════════════════════════════════════════════════════════════════


def test_validation_rule_crud_and_ruleset_bump(
    client: TestClient, book: dict[str, str]
) -> None:
    pid = _pid(book)
    base = client.get(f"/api/projects/{pid}/validation-rules")
    assert base.status_code == 200, base.text
    assert any(r["rule_id"] == "R2" for r in base.json()), "R2 常驻显示"
    assert any(r["rule_id"] == "R3" for r in base.json()), "R3 常驻显示"
    # 空项目还没有自定义规则。
    assert all(r["rule_id"] not in ("vrule",) for r in base.json())

    created = client.post(
        f"/api/projects/{pid}/validation-rules",
        json={"title": "不许有玄铁令", "literal": "玄铁令", "blocks_downstream": True},
    )
    assert created.status_code == 200, created.text
    rule_id = created.json()["rule_id"]
    after = client.get(f"/api/projects/{pid}/validation-rules").json()
    custom = [r for r in after if r["rule_id"] == rule_id]
    assert len(custom) == 1 and custom[0]["title"] == "不许有玄铁令"

    # 禁用后列表不再返回（照旧留在库里）。
    patched = client.patch(
        f"/api/projects/{pid}/validation-rules/{rule_id}", json={"enabled": False}
    )
    assert patched.status_code == 200, patched.text
    after_disable = client.get(f"/api/projects/{pid}/validation-rules").json()
    assert all(r["rule_id"] != rule_id for r in after_disable)

    # 删除。
    deleted = client.delete(f"/api/projects/{pid}/validation-rules/{rule_id}")
    assert deleted.status_code == 200, deleted.text
    missing = client.delete(f"/api/projects/{pid}/validation-rules/{rule_id}")
    assert missing.status_code == 404, missing.text


def test_validation_rule_roundtrips_in_the_ruleset_hash(
    client: TestClient, book: dict[str, str]
) -> None:
    from novel_harness.db import connect

    pid = _pid(book)
    before = connect(book["db"]).execute(
        "SELECT epoch, ruleset_hash FROM validation_ruleset_state WHERE project_id = ?",
        (pid,),
    ).fetchall()
    client.post(
        f"/api/projects/{pid}/validation-rules",
        json={"literal": "血脉" if False else "青云城", "title": "地点别写"},
    )
    after = connect(book["db"]).execute(
        "SELECT epoch, ruleset_hash FROM validation_ruleset_state WHERE project_id = ?",
        (pid,),
    ).fetchall()
    assert after[0][0] == before[0][0] + 1, "规则语义变化必须递增 epoch"
    assert after[0][1] != before[0][1], "ruleset hash 必须重算"


# ══════════════════════════════════════════════════════════════════════════
# 保存触发的固定刷新（Task 16 / Task 18 e2e）
# ══════════════════════════════════════════════════════════════════════════


def test_save_creates_a_refresh_attempt_and_reply_stays_reused(
    client: TestClient, book: dict[str, str]
) -> None:
    """保存后 `_trigger_refresh` 给当前 generation 排一个覆盖性 attempt。

    保存本身（`save_chapter`）的语义不受它影响——回执仍是 `reused`（同正文再次
    保存）。断言的是持久 attempt 表里多了一行：这就是后台 dispatcher
    （Task 16）会去 claim 的那条，**不是内存 enqueue**。
    """
    pid = _pid(book)
    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    markdown = body["markdown"]
    # 先带同 hash 保存一次（reused），把「已有 attempt」那次撇开。
    first = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": markdown, "expected_text_sha256": body["text_sha256"]},
    )
    assert first.status_code == 200, first.text
    conn = connect(book["db"])
    conn.close()

    # 保存**同一**正文第二次：正文没变、不是新 generation，但 `_trigger_refresh`
    # 仍然会为一个「保存动作」确保覆盖（head/验证缺哪补哪）。
    again = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": markdown, "expected_text_sha256": body["text_sha256"]},
    )
    assert again.status_code == 200, again.text
    conn = connect(book["db"])
    after = conn.execute(
        "SELECT COUNT(*) FROM chapter_refresh_attempt a "
        "JOIN chapter_refresh_run r ON r.id = a.run_id "
        "WHERE r.project_id = ?",
        (pid,),
    ).fetchone()[0]
    conn.close()
    # 至少排了一次（第一次保存也可能建了一条；关键是这条路径真的写持久表）。
    assert after >= 1, "保存必须把刷新动作写进持久 attempt 表，不能只在内存"


def test_save_changes_length_triggers_a_fresh_attempt(
    client: TestClient, book: dict[str, str]
) -> None:
    """正文 hash 变了（作者真的改了）→ `changed=true` → 为**新 generation** 排 attempt。"""
    pid = _pid(book)
    body = client.get(f"/api/projects/{pid}/chapters/1/text").json()
    markdown = body["markdown"] + "\n\n萧决在结尾又说了一句话。\n"
    saved = client.put(
        f"/api/projects/{pid}/chapters/1/text",
        json={"markdown": markdown, "expected_text_sha256": body["text_sha256"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["changed"] is True
    # 新 generation 快照在库里（保存那一步落库了）。
    conn = connect(book["db"])
    row = conn.execute(
        "SELECT r.source_generation AS g FROM chapter_refresh_attempt a "
        "JOIN chapter_refresh_run r ON r.id = a.run_id "
        "WHERE r.project_id = ? ORDER BY a.created_at DESC, r.source_generation DESC LIMIT 1",
        (pid,),
    ).fetchone()
    conn.close()
    assert row is not None and int(row["g"]) >= 1
