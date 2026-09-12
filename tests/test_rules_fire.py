"""**规则在一本从生产入口建起来的书上真的开火一次。**

⚠️ **2026-09-05：这份文件的主语换了第三次。** R2 `FUTURE_LEAK`（2026-08-27，ADR 0040）→
R3 `DEAD_SPEAKS`（2026-09-05，[ADR 0042](../docs/adr/0042-dead-speaks-cut.md)）→
**作者自己加的确定性规则**。系统规则一条不剩了，而这份测试要证的那件事一个字没变：
「作者按得到的那几个口子，真的能让检验那一步报出东西来。」

下面这段历史背景原样保留——它解释的是**为什么一条规则光有绿测试不算数**，
这个理由对今天这条作者规则同样成立（它的输入是作者敲进「检验规则」那一栏的字，
而那一栏 2026-09-05 之前在浏览器里根本不存在）。

── 这个文件为什么存在 ────────────────────────────────────────────────────

R2 `FUTURE_LEAK` 和 R3 `DEAD_SPEAKS` 在 2026-08-02 就写完了，`tests/test_checks.py` 里
两条规则各有一组绿着的断言。但那些断言喂的是 `FakeGraph`：`first_appears_chapter` 是
测试自己 `NodeProps(...)` 塞进去的，`value_key='dead'` 也是测试自己造的边。而生产上
（2026-08-13 之前）：

- `NodeProps.first_appears_chapter` —— 全仓只有 `tests/` 和 `synth/` 在写，
  `POST /nodes` 的请求体里没有它，`cli._declare_node` 不传 props；
- `EdgeProps.value_key` —— 零写入方，于是 `StateSnapshot.is_dead` **恒为 False**；
- `NodeLabel.STATE_DIM` —— 零创建路径（抽取只 `resolve_ids`，要求它**已存在**）。

**三样东西合起来的后果是：作者点「检查本章」，这些规则结构上永远不可能开火。**
右栏那句「本章尚未登场」永远显示「（无）」——不是书干净，是没有入口能填那个字段。
M3 那次「双边门槛已过」量的是 `synth/m3_replay.py` 的 `OverlayGraph`——它在**内存里**
补上 first_appears 和死亡边，一条都没穿过生产写路径（那个文件的 docstring 自己写着
「运行期在内存叠加」）。

所以本文件的判据只有一条：**每一个字都从 HTTP 进去**（作者今天真按得到的那些口子），
一个 `store.upsert_node` / `NodeProps(...)` 都不许出现在准备阶段。它红了 = 作者在
浏览器里加的规则又变回了摆设，而没有任何别的测试会告诉你这件事。

── 顺带钉住的两件事 ──────────────────────────────────────────────────────

1. **先跑一次「什么都没声明」的检查**，断言它是空的。没有这一步，下面「报出来了」
   证明不了任何东西——它可能一直在报。
2. **建议语里不许有机器码**（`first_appears_chapter` 这种）。那句话是印给小说作者看的，
   而在此之前它逐字写着一个 Python 标识符，且工作台里没有任何地方能做那件事。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_harness.checks import ALL_CHECKS
from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.graph import HEALTH_DIM_KEY, NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph

# ── 一本四章的小书 ────────────────────────────────────────────────────────
#
# 时间轴：顾清音和幽泉窟第 3 章才头一回露面，萧决第 2 章死。于是
#   ⚠️ 上面这条时间轴是给**已经砍掉的 R2/R3** 编的（ADR 0040 / 0042）。今天它只剩两个
#   用处：`declare/death` 那几条测试还在用「萧决第 2 章死」，而「幽泉窟」这四个字只
#   出现在 ch1 和 ch3——正好当作者那条 `forbidden_literal` 规则的靶子（ch2 里没有，
#   于是「命中」和「闭嘴」在同一本书上都测得到）。
#
# 三句引语（下面 declare 用的那三句）在全书里各只出现一次：`Ledger._one_candidate`
# 多于一处就拒，那是它的设计，不是这份测试的运气。
BOOK = (
    "第一章 山门\n"
    "\n"
    "萧决拾级而上，山门在雾里。\n"
    "顾清音道：「你来了。」\n"
    "幽泉窟的传闻，山下早就传遍了。\n"
    "\n"
    "第二章 落幕\n"
    "\n"
    "剑光落下，萧决再没有起来。\n"
    "\n"
    "第三章 新人\n"
    "\n"
    "顾清音第一次踏进这座山门。\n"
    "幽泉窟就在崖底张着口。\n"
    "\n"
    "第四章 回声\n"
    "\n"
    "萧决道：「我还在。」\n"
)

DEATH_QUOTE = "剑光落下，萧决再没有起来。"
GU_DEBUT_QUOTE = "顾清音第一次踏进这座山门。"

MACHINE_CODE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
"""snake_case —— 和 `tests/test_wording_guard.py` / `screenGuard.ts` 同一条判据（形状，不是词表）。"""


def _state_dims(db: str, pid: str) -> int:
    """库里有几个状态维度节点。**只能直接问库**——`StoryGraph` 没有「列出全部节点」，
    而那是故意的（`panel.constraints.forbidden_entities` 的 docstring）。
    `tests/` 不在架构守卫的扫描范围（它只扫 `src/`），同 `test_canon_writer._count`。
    """
    conn = connect(Path(db))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM node WHERE project_id = ? AND label = ?",
            (pid, NodeLabel.STATE_DIM.value),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    """只建库 + 导正文。**一个节点、一条边都不在这里造** —— 那是测试要证明的东西。"""
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="山门记", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    txt = tmp_path / "src.txt"
    txt.write_text(BOOK, encoding="utf-8")
    importer.import_book(store, pid, txt=txt, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid}


@pytest.fixture
def client(book: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", book["db"])
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


class Author:
    """作者今天在浏览器里按得到的那几个口子。**这个类里没有一句 SQL，也没有一个引擎类型。**"""

    def __init__(self, client: TestClient, pid: str) -> None:
        self._c = client
        self._pid = pid

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self._c.post(f"/api/projects/{self._pid}{path}", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def add(self, label: str, name: str, **extra: Any) -> Any:
        return self._post("/nodes", {"label": label, "name": name, **extra})

    def first_appearance(self, of: str, quote: str) -> Any:
        return self._post("/declare/first-appearance", {"of": of, "quote": quote})

    def died(self, who: str, quote: str) -> Any:
        return self._post("/declare/death", {"who": who, "quote": quote})

    def add_rule(self, *, literal: str, title: str) -> Any:
        """在「检验规则」那一栏里加一条——**今天规则只有这一个来源**（ADR 0042）。"""
        return self._post("/validation-rules", {"literal": literal, "title": title})

    def check(self, chapter: int) -> Any:
        return self._post(f"/chapters/{chapter}/check", {})

    def roster(self) -> Any:
        r = self._c.get(f"/api/projects/{self._pid}/roster")
        assert r.status_code == 200, r.text
        return r.json()

    def state(self, node_id: str, chapter: int) -> Any:
        r = self._c.get(
            f"/api/projects/{self._pid}/characters/{node_id}/state",
            params={"chapter": chapter},
        )
        assert r.status_code == 200, r.text
        return r.json()

    def panel_state(self, chapter: int) -> Any:
        """右栏「人物状态」那一格读的**那一条**。

        和上面 `state()` 不是同一条路由：这一条不收 node_id，在场由后端从
        **路径上那一章**的正文里数出来（`_effective_cast`，ADR 0018）。
        `chapter` 在两条上都是 AS OF —— 「看上一章」那个开关换的就是这个数，
        它不写进任何数据。
        """
        r = self._c.get(f"/api/projects/{self._pid}/chapters/{chapter}/state")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def author(client: TestClient, book: dict[str, str]) -> Author:
    a = Author(client, book["pid"])
    a.add("Character", "萧决")
    a.add("Character", "顾清音")
    a.add("Location", "幽泉窟")
    return a


def _issues(result: Any, rule: str) -> list[dict[str, Any]]:
    return [i for i in result["issues"] if i["rule"] == rule]


# ══════════════════════════════════════════════════════════════════════════
# 基线：什么都没声明的时候，这两条规则是哑的
# ══════════════════════════════════════════════════════════════════════════


def test_nothing_is_checked_before_the_author_writes_a_rule(author: Author) -> None:
    """**一条规则都没有时，这一步跑完了，但什么都没查。**

    没有这一条，下面那条「报出来了」证明不了任何事——它可能一直在报。

    这个零有两层，别混：`issues` 是空的**不代表书是干净的**（下面那条规则一加，
    同一段正文立刻报出来），`rules` 是空的才是「压根没查」。§10 约束 8 要的就是
    这两个零在出参里分得开——2026-09-05 系统规则清空之后（ADR 0042），
    一本没加过规则的书**常态**就长这样。
    """
    report = author.check(1)
    assert report["gate"] == "passed"
    assert report["issues"] == []
    assert report["rules"] == [], "没有规则 ≠ 没有问题，这一格得说得出「压根没查」"
    assert len(ALL_CHECKS) == 0, "系统规则又长回来了？那这条基线得跟着改"
    assert report["source_generation"] >= 1
    assert report["ruleset_epoch"] >= 1


# ══════════════════════════════════════════════════════════════════════════
# 作者自己加的规则 —— 加完就在真链路上开火
# ══════════════════════════════════════════════════════════════════════════


def test_the_authors_rule_fires_on_the_words_he_wrote(author: Author) -> None:
    """**本文件的第二条：作者加的规则第一次在生产写路径上开火。**

    每一个字都从 HTTP 进去（`POST …/validation-rules` → `POST …/chapters/{n}/check`），
    准备阶段一句 SQL 都没有——这正是这份文件存在的理由：一条规则「代码写完、测试绿着」
    和「作者在浏览器里真能让它开火」，是两件事。
    """
    author.add_rule(literal="幽泉窟", title="不许再提幽泉窟")

    report = author.check(1)

    assert [r["state"] for r in report["rules"]] == ["blocked"]
    assert report["gate"] == "blocked"
    assert len(report["issues"]) == 1
    issue = report["issues"][0]
    assert issue["rule"].startswith("custom:"), "命中要认得出是哪条规则"
    assert issue["chapter"] == 1
    assert issue["anchor"]["quote_text"] == "幽泉窟", "锚是作者写的那几个字，逐字"
    assert "幽泉窟" in issue["message"]


def test_the_authors_rule_is_silent_where_the_words_are_not(author: Author) -> None:
    """**没命中时闭嘴，但仍然算「查过了」。**

    ch2 那一章的正文里没有「幽泉窟」，所以 0 条命中——而 `rules` 里仍然有那一条、
    状态是 `clear`。零和真零分得开这件事，在作者规则上和当年在系统规则上是同一条纪律。
    """
    author.add_rule(literal="幽泉窟", title="不许再提幽泉窟")

    report = author.check(2)

    assert report["issues"] == []
    assert [r["state"] for r in report["rules"]] == ["clear"]
    assert report["gate"] == "passed"


def test_the_state_dim_is_created_by_the_engine_and_stays_out_of_the_roster(
    author: Author, book: dict[str, str]
) -> None:
    """生死这个维度由 `declare_dead` 自己建 —— 作者没有、也不该有建它的入口。

    **它必须留在角色册外面**：`resolve(pid, None)` 是 `mentions.py` 编 alternation 的料，
    一条 surface=「生死」的 canonical 行会让规则去正文里匹配每一个「生死」
    （`CANONICAL_ALIAS_LABELS` 的 docstring 说的就是这件事）。
    """
    author.died(who="萧决", quote=DEATH_QUOTE)

    assert not [n for n in author.roster() if n["label"] == NodeLabel.STATE_DIM.value], (
        "状态维度进角色册了 —— 下一步就是规则拿它去匹配正文"
    )
    assert _state_dims(book["db"], book["pid"]) == 1


def test_declaring_a_death_twice_does_not_build_a_second_dimension(
    author: Author, book: dict[str, str]
) -> None:
    """幂等：同一句引语再声明一次，维度不该多出第二个。

    多出来的后果不是「多一行」：两个 StateDim 共享 `dim_key` 时 supersede 认为那是两个
    维度，一条都不闭合，而 `is_dead` 的 `any()` 让 dead 永远压过 alive
    （`sqlite_store._check_one_value_per_dim` 的原话）。
    """
    author.died(who="萧决", quote=DEATH_QUOTE)
    author.died(who="萧决", quote=DEATH_QUOTE)

    assert _state_dims(book["db"], book["pid"]) == 1


def test_is_dead_reaches_the_panel_too(author: Author) -> None:
    """人物卡上那个「已故」也是同一条边喂出来的（R3 不是它唯一的消费者）。"""
    node_id = author.add("Character", "萧决")["id"]
    author.died(who="萧决", quote=DEATH_QUOTE)

    assert author.state(node_id, 1)["is_dead"] is False
    after = author.state(node_id, 4)
    assert after["is_dead"] is True
    assert [s["dim_key"] for s in after["states"]] == [HEALTH_DIM_KEY]


def test_the_route_the_right_column_actually_calls_carries_is_dead(author: Author) -> None:
    """右栏「人物状态」读的是 `/chapters/{n}/state`，**不是**上面那条按人查的路由。

    ── 为什么要单钉这一条 ────────────────────────────────────────────────
    `is_dead` 是一个 computed field，而它 2026-08-13 之前是个光秃秃的 `@property`
    ——`model_dump()` 从不输出 property，于是那个键在浏览器里恒为 `undefined`，
    `StateCards.tsx` 里那个「· 已亡」角标**一次都没画出来过**。
    `tsc` 和 vitest 谁都看不见：契约夹具是从真 app dump 的，真 app 就没发过这个键，
    两头一致地缺。

    上面那条断言量的是 `/characters/{id}/state`，而**作者的屏幕上是另一条**：
    它多走一层 `_narrow`（Secret / 未来节点收窄）。收窄那一层递归重建 dict，
    漏掉一个键不会有任何东西报错。

    ── 顺带钉住「看上一章」那个开关的全部机制 ────────────────────────────
    第 1 章问不出死人、第 4 章问得出——**同一条路由、同一份数据，只换路径上那个数**。
    这就是右栏那个开关做的事的全部，后端一个字都不用改。
    """
    author.died(who="萧决", quote=DEATH_QUOTE)

    def xiao(chapter: int) -> Any:
        cards = author.panel_state(chapter)
        found = [c for c in cards if c["node"]["name"] == "萧决"]
        assert found, f"第 {chapter} 章的人物状态里没有萧决：{[c['node']['name'] for c in cards]}"
        return found[0]

    assert "is_dead" in xiao(1), "那个键整个不在 —— 角标又画不出来了，而前端不会报错"
    assert xiao(1)["is_dead"] is False, "他第 1 章还活着（闭开区间 [2, ∞) 的可见产品行为）"
    assert xiao(4)["is_dead"] is True


# ══════════════════════════════════════════════════════════════════════════
# 话术：这两句话是印给小说作者看的
# ══════════════════════════════════════════════════════════════════════════


def test_the_hit_never_shows_the_author_a_machine_name(author: Author) -> None:
    """命中那句话是印给小说作者看的，**里面不许有机器码**。

    ⚠️ **2026-09-05 换了对象**：原来钉的是 R3 的建议语（它曾经逐字写着
    `first_appears_chapter`——既是研发术语，又是一条作者执行不了的指令）。R3 砍了
    （ADR 0042），而今天唯一会印给作者的那句话是自定义规则的 `message`
    （`checks/custom.py`），所以钉它。**自定义规则不产出 `suggested_action`**：
    作者自己写的规则，引擎没有立场替他出主意。

    判据是**形状**不是词表（同 `screenGuard.ts`）：明天换一个标识符照样咬得住。
    """
    author.add_rule(literal="幽泉窟", title="不许再提幽泉窟")

    hits = [(i["rule"], i["message"]) for i in author.check(1)["issues"]]
    assert hits, "命中一条都没取到，下面那条断言会空转成真"
    assert all(rule.startswith("custom:") for rule, _ in hits)
    offenders = [(rule, text) for rule, text in hits if MACHINE_CODE.search(text)]
    assert not offenders, f"给作者看的那句话里有机器码：{offenders}"
    assert all(i["suggested_action"] is None for i in author.check(1)["issues"])
