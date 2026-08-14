"""闭环：**错了看得见 → 跳得过去 → 改得掉 → 这次改动自己也留了痕**。

ADR 0020 拿「事后可查可改」换掉了「事前批准」。那条退路是**三段**，任何一段断掉换回来的
就只是「系统自动改你的书」。`test_activity.py` 已经分段验过其中两段（jump 落在真路由上、
那条路由真的 200），**这份文件验的是它们连起来**：从一条 `actor='system'` 的自动升日志
出发，照它给的坐标改一次，再回到同一份清单——那次改动必须以 `actor='author'` 出现在
里面，否则「作者改过什么」就只有他自己记得。

另外两条钉在这儿，它们都是「按 id / 按章号猜」会踩的坑：

- **id 陷阱。** `AutoPromotion.promoted_event_ids` 是 **PROVISIONAL** 的 id，改得掉的是
  它克隆出来的那条 CANON 事件（**id 不同**）。拿前者打 `/canon/events/{id}/cast` 必然
  404 —— 一个点了没反应的按钮。日志给的坐标必须是后者，而且必须能在
  `?scope=CANON` 那一页里找得到（工作台就是按这个 id 认哪一条要展开的）。
- **认知格的坐标不保证落得下。** 矩阵的行由本章正文推（ADR 0018），而 `valid_from`
  只由引语决定（ADR 0006）——引语里没写名字的那一章，跳过去那一行根本不在表上。
  引擎今天不打算补（补它要在 `jump` 里多给一个坐标），**所以工作台必须把这件事说出来**，
  见 `frontend/src/components/ActivityLog.loop.test.tsx` 里对应的那一条。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import seed

from test_activity import (  # noqa: F401  ← poisoned / book / client 是 fixture，靠名字注入
    POISON_CHAPTER,
    _by_id,
    _entries,
    poisoned,
)

from novel_harness.activity import ActivityJump, JumpTarget
from novel_harness.api.activity import _endpoints
from novel_harness.decisions import SYSTEM_ACTOR

# 第二章的正文是「他终于明白了。/ 风起。他终于明白了。」——**一个人名都没有**。
# 用它当引语声明认知是最常见的中文网文写法（满篇代词），而它正好制造出
# 「这一章的矩阵里没有他那一行」。
NAMELESS_QUOTE = "风起。他终于明白了。"

# 第一章那句写了名字的（人 + 地点 + 秘密全在里面）。声明三种边都能用它当引语，
# 于是「落不落得下」的差别只可能来自章号推导本身，不是引语选得不一样。
NAMED_QUOTE = "萧决在青云城主府第一次听说了血脉秘密的真相。"


def _tally(client: TestClient, pid: str, actor: str) -> int:
    """全量计数里 `actor` 那一格。**不受过滤影响**（`activity()` 的契约）。"""
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": 1}).json()
    return next((row["count"] for row in page["actors"] if row["actor"] == actor), 0)


def _system_event_row(client: TestClient, pid: str) -> dict[str, Any]:
    """系统自己升上去的那条事件行。**它是 ADR 0020 押的赌注本身**：
    `knowers` 是推断不是文本事实，没人点过，最可能错。"""
    rows = [
        entry
        for entry in _entries(client, pid, actor=SYSTEM_ACTOR, limit=200)
        if entry["jump"] and entry["jump"]["target"] == JumpTarget.EVENT_CAST.value
    ]
    assert len(rows) == 1, f"自动升上去的事件没给出可编辑坐标：{[r['title'] for r in rows]}"
    return rows[0]


# ══════════════════════════════════════════════════════════════════════════
# 1. 三段连起来
# ══════════════════════════════════════════════════════════════════════════


def test_a_system_row_can_be_walked_back_and_the_walk_back_shows_up_as_the_author(
    client: TestClient, poisoned: dict[str, str]  # noqa: F811 ← 见文件头的 import 注释
) -> None:
    """**这一条是整份文件存在的理由。**

    分段绿不等于闭环绿：日志给的坐标能 200（`test_activity.py` 验过）、改正层能写库
    （`test_corrections.py` 验过），但「改完之后作者回到日志页看得见自己改过什么」
    是第三段，此前没有任何东西验它。而 ADR 0020 承诺的正是三段一起
    ——少了第三段，作者第二天就分不清一条事实是系统写的还是他自己改过的。
    """
    pid = poisoned["pid"]
    row = _system_event_row(client, pid)
    jump = row["jump"]
    assert row["actor"] == SYSTEM_ACTOR, "这一行不是系统写的，下面验的就不是那条赌注"

    before_author = _tally(client, pid, "author")
    before_system = _tally(client, pid, SYSTEM_ACTOR)

    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    fixed = client.post(
        jump["endpoints"][0],
        json={"knower_ids": [poisoned["萧决"]], "expected_canon_version": version},
    )
    assert fixed.status_code == 200, fixed.text
    assert [n["name"] for n in fixed.json()["event"]["knowers"]] == ["萧决"]

    # ── 第三段：这次改动自己也进了同一份清单，且署的是作者 ──────────────
    entry = _by_id(_entries(client, pid, limit=200), fixed.json()["decision_id"])
    assert entry["actor"] == "author", (
        "作者亲手改的那一次署名不是 author —— 日志页的 actor 过滤就分不出人和机器了"
    )
    assert _tally(client, pid, "author") == before_author + 1
    # 只增不改：系统那一行还在。「查得到改过什么」是这条退路的一半价值
    # （`corrections.py` 模块头「三件事本模块故意不做」第 3 条）。
    assert _tally(client, pid, SYSTEM_ACTOR) == before_system
    assert _by_id(_entries(client, pid, actor=SYSTEM_ACTOR, limit=200), row["id"])

    # 而且这条新行自己也**跳得回去**：闭环不是一次性的，作者可以改第二次。
    assert entry["jump"]["target"] == JumpTarget.EVENT_CAST.value
    assert entry["jump"]["event_id"] == jump["event_id"]
    again = client.post(
        entry["jump"]["endpoints"][0],
        json={
            "knower_ids": [poisoned["萧决"], poisoned["李管家"]],
            "expected_canon_version": fixed.json()["canon_version"],
        },
    )
    assert again.status_code == 200, again.text


def test_only_the_author_row_is_new_when_only_the_author_acted(
    client: TestClient, poisoned: dict[str, str]  # noqa: F811 ← 见文件头的 import 注释
) -> None:
    """**自守卫（不误报那一半）**：没动手的时候，上面那条断言不许自己变绿。

    `_tally(author) == before + 1` 只有在「不动手就不涨」的前提下才说明问题。
    这里只读不写，两个计数都必须一动不动。
    """
    pid = poisoned["pid"]
    before = (_tally(client, pid, "author"), _tally(client, pid, SYSTEM_ACTOR))
    _system_event_row(client, pid)  # 纯读
    client.get(f"/api/projects/{pid}/activity", params={"limit": 200})
    assert (_tally(client, pid, "author"), _tally(client, pid, SYSTEM_ACTOR)) == before


# ══════════════════════════════════════════════════════════════════════════
# 2. id 陷阱
# ══════════════════════════════════════════════════════════════════════════


def test_the_jump_hands_back_the_canon_clone_not_the_promoted_provisional(
    client: TestClient, poisoned: dict[str, str]  # noqa: F811 ← 见文件头的 import 注释
) -> None:
    """升上去的 id 和改得掉的 id **不是同一个**，而它们长得一模一样（都是 `event:…`）。

    `promote_clean_facts` 返回的是被升的那批 **PROVISIONAL** id；真正进 CANON 的是
    `clone_to_scope` 造出来的新行。任何一处照着 `promoted_event_ids` 拼编辑入口的代码
    都会产出一个必然 404 的按钮，而 404 在界面上会被读成「这条情节已经没了」。
    """
    pid = poisoned["pid"]
    promoted = poisoned["event_id"]  # ← PROVISIONAL 的那一个
    jump = _system_event_row(client, pid)["jump"]

    assert jump["event_id"] != promoted, "日志给的是被升的那条 PROVISIONAL —— 点了必然 404"

    # 它必须在 `?scope=CANON` 那一页里找得到：工作台就是按这个 id 认「展开哪一条」的
    #（`CanonEventCast` 比的是 `view.event.id === focusEventId`）。找不到 = 跳过去落空。
    canon = client.get(
        f"/api/projects/{pid}/chapters/{POISON_CHAPTER}/events", params={"scope": "CANON"}
    )
    assert canon.status_code == 200, canon.text
    ids = [view["event"]["id"] for view in canon.json()]
    assert jump["event_id"] in ids, f"跳转坐标不在本章的已确认情节里：{ids}"
    assert promoted not in ids


def test_the_id_trap_is_real_so_the_check_above_is_not_decoration(
    client: TestClient, poisoned: dict[str, str]  # noqa: F811 ← 见文件头的 import 注释
) -> None:
    """**自守卫（会漏的那一半）**：把假实现真的造出来，看上面那张网抓不抓得住。

    假实现 = 「用 `promoted_event_ids` 拼编辑入口」，也就是审计发现过的那个形态。
    它算出来的路径**指向一条真存在的路由**（所以
    `test_every_jump_endpoint_resolves_to_a_real_route` 不会红），只是那个 id 是
    PROVISIONAL 的 —— 于是它必然 404。真坐标那一条必须 200，否则这条探针两边都验不出。
    """
    pid = poisoned["pid"]
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    body = {"knower_ids": [poisoned["萧决"]], "expected_canon_version": version}

    leaky = _endpoints(
        pid,
        ActivityJump(
            target=JumpTarget.EVENT_CAST,
            label="去改这条事件的知情 / 在场名单",
            chapter_number=POISON_CHAPTER,
            event_id=poisoned["event_id"],  # ← 假实现：拿了被升的那条 PROVISIONAL
        ),
    )
    assert leaky and client.post(leaky[0], json=body).status_code == 404

    real = _system_event_row(client, pid)["jump"]["endpoints"]
    assert client.post(real[0], json=body).status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# 3. 认知格的坐标落不落得下
# ══════════════════════════════════════════════════════════════════════════


def test_a_knowledge_cell_jump_can_point_at_a_chapter_whose_matrix_has_no_such_row(
    client: TestClient, book: dict[str, str]
) -> None:
    """**引擎不保证那一格在跳过去的那张表上，而这不是 bug，是两条 ADR 交叉出来的形状。**

    `valid_from` 只由引语决定（ADR 0006），矩阵的行由本章正文推（ADR 0018）。作者用一句
    没写名字的话声明认知（满篇代词，中文网文的常态），这两条就对不上：跳转坐标指向第 2 章
    的某一格，而第 2 章的矩阵里**一行都没有**。

    坐标本身没错（`/canon/knowledge` 照样改得掉，下面那个 200 就是证据），所以修的位置
    不在这儿——**工作台必须把「跳过来了但这张表上没有」说出来**，否则作者点完只看见一张
    空表（§10 约束 8：静默的零和真的零不许长得一样）。那一条钉在
    `frontend/src/components/ActivityLog.loop.test.tsx`。
    """
    pid = book["pid"]
    seed.knows(book["db"], pid, who="萧决", secret="血脉秘密", quote=NAMELESS_QUOTE)

    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert flipped.status_code == 200, flipped.text

    jump = _by_id(_entries(client, pid, limit=200), flipped.json()["decision_id"])["jump"]
    assert jump["target"] == JumpTarget.KNOWLEDGE_CELL.value
    # 章号继承自那条引语所在的章（作者一个字都没填过它）。
    assert jump["chapter_number"] == 2

    # 工作台跳过去时 `cast` 是空的（`jumpFromActivity` 清掉它），后端于是按本章正文推。
    matrix = client.get(f"/api/projects/{pid}/chapters/{jump['chapter_number']}/matrix")
    assert matrix.status_code == 200, matrix.text
    cells = {(c["character_id"], c["secret_id"]) for c in matrix.json()["cells"]}
    assert (jump["character_id"], jump["secret_id"]) not in cells, (
        "第 2 章的正文里出现了人名 —— 换一句更干净的引语，否则这条测试是空转"
    )

    # 但那条事实**改得掉**：落不下的是坐标在那张表上的位置，不是编辑能力。
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


def test_a_named_quote_does_land_on_the_matrix(
    client: TestClient, book: dict[str, str]
) -> None:
    """**自守卫（不误报那一半）**：引语里写了名字的那一章，坐标是落得下的。

    没有这一条，上面那句「落不下」可能只是因为矩阵读端整个坏了——那就是另一个 bug，
    而不是本文件要说的那件事。
    """
    pid = book["pid"]
    seed.knows(book["db"], pid, who="萧决", secret="血脉秘密", quote=NAMED_QUOTE)

    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert flipped.status_code == 200, flipped.text

    jump = _by_id(_entries(client, pid, limit=200), flipped.json()["decision_id"])["jump"]
    assert jump["chapter_number"] == 1
    matrix = client.get(f"/api/projects/{pid}/chapters/1/matrix").json()
    cells = {(c["character_id"], c["secret_id"]) for c in matrix["cells"]}
    assert (jump["character_id"], jump["secret_id"]) in cells


# ══════════════════════════════════════════════════════════════════════════
# 4. `expected_canon_version` 的来源必须跟着作者看到的那份数据走
# ══════════════════════════════════════════════════════════════════════════


def test_the_matrix_carries_the_version_that_its_own_editor_needs(
    client: TestClient, book: dict[str, str]
) -> None:
    """矩阵那一格的编辑器从 `matrix.version.canon_version` 取版本，所以这个数必须是真的。

    它一度恒为 0（图层建 `KnowledgeMatrix` 时吃 `GraphVersion()` 默认值），照那个数发请求
    **每一次都会 409**，而那个 409 说的是「别处刚改过」——一个骗人的错误比一个错误更贵。
    """
    pid = book["pid"]
    seed.knows(book["db"], pid, who="萧决", secret="血脉秘密", quote=NAMELESS_QUOTE)
    truth = client.get(f"/api/projects/{pid}").json()["canon_version"]
    assert truth > 0, "这本书还没升过版本，下面比什么都一样 —— 换个种子"
    seen = client.get(f"/api/projects/{pid}/chapters/1/matrix").json()["version"]
    assert seen["canon_version"] == truth

    # 用这张表自己带的那个数发一次改正：必须不是 409。
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "以为那只是个传闻",
            "expected_canon_version": seen["canon_version"],
        },
    )
    assert flipped.status_code == 200, flipped.text
    # 改完那张表跟着涨一格：作者接着改第二次，用的还是这张表上的数。
    assert client.get(f"/api/projects/{pid}/chapters/1/matrix").json()["version"][
        "canon_version"
    ] == flipped.json()["canon_version"]


def test_the_event_cast_editor_has_no_such_carrier(
    client: TestClient, poisoned: dict[str, str]  # noqa: F811 ← 见文件头的 import 注释
) -> None:
    """**名单编辑器那一侧没有同样的载体**——版本只在 `/api/projects` 上，这是前端那条
    「撞了 409 之后要重取哪个读端」的全部依据。

    这不是缺陷（`/events` 出参里塞一个项目版本才是乱放东西），但它是一条**前端必须知道
    的事实**：名单撞 409 之后重取 `/events` 一个字都不会变，得去重取 `/api/projects`。
    对应的前端断言在 `ActivityLog.loop.test.tsx`。
    """
    pid = poisoned["pid"]
    events = client.get(
        f"/api/projects/{pid}/chapters/{POISON_CHAPTER}/events", params={"scope": "CANON"}
    )
    assert events.status_code == 200, events.text
    assert events.json(), "这一章没有已确认情节，下面这句就是空转"
    for view in events.json():
        assert "canon_version" not in view
        assert "version" not in view
    assert "canon_version" in client.get(f"/api/projects/{pid}").json()


# ══════════════════════════════════════════════════════════════════════════
# 5. 空 `endpoints` 是一个**断言**，不许把改得掉的东西说成改不掉
# ══════════════════════════════════════════════════════════════════════════
#
# `api/activity._endpoints` 的空元组在本仓有一个写死的含义：「今天没有任何路由能改
# 这个东西」。ADR 0020 把这句话当成自己的推翻条件之一（「出现『作者改不回来』的形态」），
# 日志页因此照着它挂一句「从这里点不到具体的某一处」。
#
# **所以这个 bucket 只要多收一条其实改得掉的事实，那句话就变成假话，观测点也跟着脏了**
# ——而且脏在会让人误以为退路不存在的那一侧。


def _declare_row(client: TestClient, pid: str, decision_id: str) -> dict[str, Any]:
    return _by_id(_entries(client, pid, limit=200), decision_id)


def test_the_authors_own_knowledge_declaration_is_not_filed_as_unfixable(
    client: TestClient, book: dict[str, str]
) -> None:
    """作者亲手声明的那条「知道」，日志里必须指得回那一格。

    这是同一条事实的两行并排摆着的问题（真 dump 里就是这个形态）：

        作者 · 更正认知类型  萧决 对「血脉秘密」：知道 → 以为   → endpoints 有一条
        作者 · 声明认知      萧决 知道 血脉秘密                → endpoints 空

    **两行说的是同一格。** 后者的空元组按本仓的定义念出来是「今天没有任何路由能改它」，
    而 `/canon/knowledge` 一打就通（下面那个 200 就是证据）。

    这不是文案问题：`declare_knows` / `declare_believes` 是作者**自己**往图里放认知的
    主路径（ADR 0004 声明优于抽取），也是他最可能把「知道」和「以为」写反的地方。
    日志在那一行告诉他没救了，等于把 ADR 0020 押的退路在最常用的入口上关掉。
    """
    pid = book["pid"]
    declared = seed.knows(book["db"], pid, who="萧决", secret="血脉秘密", quote=NAMED_QUOTE)

    jump = _declare_row(client, pid, declared.decision_id)["jump"]
    assert jump["target"] == JumpTarget.KNOWLEDGE_CELL.value, (
        "声明认知那一行被归进了「改不了」那一档 —— 而它改得掉"
    )
    # 坐标是两个真 id，不是从「萧决 知道 血脉秘密」那句字面反解出来的。
    assert (jump["character_id"], jump["secret_id"]) == (book["萧决"], book["血脉秘密"])
    # 章号仍然只由那句引语决定，作者一个字都没填过它（ADR 0006 / 约束 10）。
    assert jump["chapter_number"] == 1

    # 「改得掉」得当场证明，不是看 endpoints 长度。
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    fixed = client.post(
        jump["endpoints"][0],
        json={
            "character_id": jump["character_id"],
            "secret_id": jump["secret_id"],
            "to_type": "BELIEVES",
            "believed_value": "以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert fixed.status_code == 200, fixed.text


def test_a_belief_declaration_lands_on_the_same_cell(
    client: TestClient, book: dict[str, str]
) -> None:
    """`declare_believes` 走的是同一个 `DecisionKind`，别只顾着 KNOWS 那一半。

    两条声明共用 `_log_edge`，所以「只对 KNOWS 补坐标」这种半吊子实现在这条上会红。
    """
    pid = book["pid"]
    declared = seed.believes(
        book["db"], pid,
        who="萧决", secret="血脉秘密",
        believed_value="以为那只是个传闻", quote=NAMED_QUOTE,
    )
    jump = _declare_row(client, pid, declared.decision_id)["jump"]
    assert jump["target"] == JumpTarget.KNOWLEDGE_CELL.value
    assert (jump["character_id"], jump["secret_id"]) == (book["萧决"], book["血脉秘密"])

    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    back = client.post(
        jump["endpoints"][0],
        json={
            "character_id": jump["character_id"],
            "secret_id": jump["secret_id"],
            "to_type": "KNOWS",
            "expected_canon_version": version,
        },
    )
    assert back.status_code == 200, back.text


def test_a_place_declaration_still_admits_it_cannot_be_edited(
    client: TestClient, book: dict[str, str]
) -> None:
    """**自守卫（不误报那一半）**：位置声明必须**留在**空 endpoints 那一档。

    `corrections.py` 只改「知道 ↔ 以为」和事件名单——`LOCATED_AT` 今天真的没有编辑入口。
    上面那条修法要是写成「所有声明行都给一个认知格坐标」，这条会红，而那种实现产出的
    正是本仓反复禁止的东西：一个点了必然被拒的按钮，外加把 ADR 0020 的观测点关掉。
    """
    pid = book["pid"]
    declared = seed.where(book["db"], pid, who="萧决", loc="青云城主府", quote=NAMED_QUOTE)
    jump = _declare_row(client, pid, declared.decision_id)["jump"]
    assert jump["target"] == JumpTarget.CHAPTER.value
    assert jump["endpoints"] == []
    assert jump["character_id"] is None and jump["secret_id"] is None


def test_pointing_a_place_declaration_at_the_cell_editor_really_would_be_a_dead_button(
    client: TestClient, book: dict[str, str]
) -> None:
    """**自守卫（会漏的那一半）**：把上面那条禁令对应的假实现造出来，看它是不是真的坏。

    没有这一条，`test_a_place_declaration_still_admits_it_cannot_be_edited` 可能只是
    一句品味声明。这里真的按「所有声明行都给认知格坐标」拼一次路径 —— 它**指向一条
    真存在的路由**（所以 `test_every_jump_endpoint_resolves_to_a_real_route` 不会红），
    但打过去被拒：那正是「点了没反应的按钮」在 HTTP 上的样子。
    """
    pid = book["pid"]
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    leaky = _endpoints(
        pid,
        ActivityJump(
            target=JumpTarget.KNOWLEDGE_CELL,
            label="去认知矩阵改这一格",
            chapter_number=1,
            character_id=book["萧决"],
            secret_id=book["青云城主府"],  # ← 假实现：一个地点被当成了秘密
        ),
    )
    assert leaky, "假实现连路径都拼不出来，这条探针证明不了任何事"
    refused = client.post(
        leaky[0],
        json={
            "character_id": book["萧决"],
            "secret_id": book["青云城主府"],
            "to_type": "BELIEVES",
            "believed_value": "以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert refused.status_code == 422, refused.text


# ══════════════════════════════════════════════════════════════════════════
# 6. **补**一条（2026-08-14）：空格子 → 补上 → 表上变了 → 日志里指得回来
# ══════════════════════════════════════════════════════════════════════════
#
# 上面五节验的都是「改」。产品规则的后一半是「**增**」——抽取写得出 `event_knower`，
# 而作者想手工补一条时此前**没有路**（空格子 404）。这一节是那条路的闭环，判据和
# 第 1 节逐字相同：不是「后端函数写得对」，是「作者补完之后，回到日志页看得见、
# 而且点得回那一格」。
#
# **章号那件事在这里必须一起验**：这条路由的生效章在路径上（矩阵本来就是 AS OF 那一章
# 渲染的），请求体里一个章号键都没有。`test_canon_edit_boundary.py` 从源码和 schema
# 两侧钉这条，这里钉的是**结果**——那个数真的落成了 `since_chapter`。

BLANK_CELL_CHAPTER = 1
"""补在第 1 章：那一章的正文里 `李管家` 和 `血脉秘密` 都被点到了，所以坐标落得下
（第 2 章一个人名都没有，见第 3 节）。"""


def _add_blank(
    client: TestClient, book: dict[str, str], **body: Any
) -> Any:
    """在「李管家 对 血脉秘密」那一格空白上补一条。**章号只出现在路径里。**"""
    pid = book["pid"]
    return client.post(
        f"/api/projects/{pid}/chapters/{BLANK_CELL_CHAPTER}/canon/knowledge",
        json={
            "character_id": book["李管家"],
            "secret_id": book["血脉秘密"],
            "expected_canon_version": client.get(f"/api/projects/{pid}").json()["canon_version"],
            **body,
        },
    )


def _cell(client: TestClient, book: dict[str, str], chapter: int) -> dict[str, Any]:
    """那一格在第 `chapter` 章的矩阵上长什么样。"""
    matrix = client.get(f"/api/projects/{book['pid']}/chapters/{chapter}/matrix")
    assert matrix.status_code == 200, matrix.text
    found = [
        c
        for c in matrix.json()["cells"]
        if (c["character_id"], c["secret_id"]) == (book["李管家"], book["血脉秘密"])
    ]
    assert len(found) == 1, f"第 {chapter} 章的表上没有这一格：{matrix.json()['cells']}"
    return found[0]


def test_an_empty_cell_can_be_filled_in_and_the_fill_in_points_back_at_itself(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这一条是第 6 节存在的理由。** 四段一次走完，中间任何一段断掉这条路就是半条。

    ① 那一格现在是空的 → ② 补一条 → ③ 同一张表上它变了 → ④ 日志里有一行署着作者，
    而且它的坐标**点得回这一格**（当场用它改一次，不是看 endpoints 的长度）。
    """
    pid = book["pid"]

    # ① 空的。**这一句不是装饰**：不先证明它是空的，下面三段可能只是在描述一条早就
    #    存在的边（那样这个测试对「补」这件事一个字都没验）。
    assert _cell(client, book, BLANK_CELL_CHAPTER)["state"] == "UNKNOWN"

    # ② 补一条。
    added = _add_blank(client, book, type="BELIEVES", believed_value="以为那是老爷编出来的")
    assert added.status_code == 200, added.text
    receipt = added.json()
    # 生效章 = 路径里那一段（作者正看着的那一章），不是他敲的数。
    assert receipt["since_chapter"] == BLANK_CELL_CHAPTER
    assert receipt["character"]["name"] == "李管家" and receipt["secret"]["name"] == "血脉秘密"

    # ③ 同一张表上那一格变了。
    now = _cell(client, book, BLANK_CELL_CHAPTER)
    assert now["state"] == "BELIEVES"
    assert now["believed_value"] == "以为那是老爷编出来的"
    assert now["since_chapter"] == BLANK_CELL_CHAPTER
    # **没有依据，而且这件事必须在出参上看得出来**：这条边背后没有引语（作者手工补的），
    # 伪造一条证据出来会被 M4 的 relocate/revalidate 当真，而它指着一句他没写过的话。
    assert now["evidence_id"] is None

    # ④ 日志里那一行：署作者，坐标是两个真 id，且**点得回这一格**。
    entry = _by_id(_entries(client, pid, limit=200), receipt["decision_id"])
    assert entry["actor"] == "author"
    jump = entry["jump"]
    assert jump["target"] == JumpTarget.KNOWLEDGE_CELL.value, (
        "补上的那一行被归进了「改不了」那一档 —— 而它当场就改得掉"
    )
    assert (jump["character_id"], jump["secret_id"]) == (book["李管家"], book["血脉秘密"])
    assert jump["chapter_number"] == BLANK_CELL_CHAPTER
    back = client.post(
        jump["endpoints"][0],
        json={
            "character_id": jump["character_id"],
            "secret_id": jump["secret_id"],
            "to_type": "KNOWS",
            "expected_canon_version": receipt["canon_version"],
        },
    )
    assert back.status_code == 200, back.text
    assert _cell(client, book, BLANK_CELL_CHAPTER)["state"] == "KNOWS"


def test_filling_in_a_cell_that_already_has_one_is_a_conflict_not_a_silent_edit(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这一格已经有内容时 409，且绝不悄悄兼做「改」。**

    作者摊开这一格的这段时间里后台正好把这条事实抽出来，是 ADR 0020 的常态而不是边角。
    那时「补」这条路要么静默改掉他没看过的那一条（最坏），要么老实说一句
    「这一格现在有内容了，先看一眼」。这里钉的是后者，**并且钉住那条边一个字节都没动**。
    """
    first = _add_blank(client, book, type="KNOWS")
    assert first.status_code == 200, first.text

    again = _add_blank(client, book, type="BELIEVES", believed_value="以为那是假的")
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["error"] == "fact_already_exists"
    # 和「你手上的版本旧了」分得开：作者该做的事不一样（那边是重取，这边是先去看）。
    assert again.json()["detail"]["error"] != "stale_base_version"

    # **那条边一个字节都没动**：兼做「改」的实现在这儿会把它换成 BELIEVES。
    now = _cell(client, book, BLANK_CELL_CHAPTER)
    assert now["state"] == "KNOWS" and now["believed_value"] is None


def test_the_cell_that_only_exists_later_is_a_conflict_too(
    client: TestClient, book: dict[str, str]
) -> None:
    """**判据是「这一格现在有没有事实」，不是「这一章看得见没有」。**

    先在第 2 章补一条，再回到第 1 章看同一格——第 1 章的表上它仍然是空的（AS OF），
    而在那儿补一条的产物是乱序插入。按「本章看不看得见」判的实现会放它进去，
    然后撞在图层那句写给维护者的诊断上（带裸 id 和 `valid_from=`）。
    """
    pid = book["pid"]
    later = client.post(
        f"/api/projects/{pid}/chapters/2/canon/knowledge",
        json={
            "character_id": book["李管家"],
            "secret_id": book["血脉秘密"],
            "type": "KNOWS",
            "expected_canon_version": client.get(f"/api/projects/{pid}").json()["canon_version"],
        },
    )
    assert later.status_code == 200, later.text
    # 第 1 章的表上这一格照旧是空的（这正是那条陷阱的形状）。
    assert _cell(client, book, 1)["state"] == "UNKNOWN"

    earlier = _add_blank(client, book, type="KNOWS")
    assert earlier.status_code == 409, earlier.text
    assert earlier.json()["detail"]["error"] == "fact_already_exists"


def test_the_add_route_refuses_the_shapes_the_edit_route_refuses(
    client: TestClient, book: dict[str, str]
) -> None:
    """形状那三条规矩两条路共用一份（`corrections._knowledge_shape`）。

    **不是洁癖**：作者在同一块屏幕上按下的是同一个「以为」，两份措辞漂开的那天，
    同一个错误在「补」和「改」上说的不是一句话。
    """
    empty = _add_blank(client, book, type="BELIEVES", believed_value="  ")
    assert empty.status_code == 422, empty.text
    extra = _add_blank(client, book, type="KNOWS", believed_value="多余的一句")
    assert extra.status_code == 422, extra.text
    # 那一格仍然是空的：被拒的请求一个字节都不许落库。
    assert _cell(client, book, BLANK_CELL_CHAPTER)["state"] == "UNKNOWN"

    # 两端不是「一个人物对一个秘密」同样拒（照抄「改」那条的判据）。
    pid = book["pid"]
    wrong = client.post(
        f"/api/projects/{pid}/chapters/1/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["青云城主府"],  # ← 一个地点
            "type": "KNOWS",
            "expected_canon_version": client.get(f"/api/projects/{pid}").json()["canon_version"],
        },
    )
    assert wrong.status_code == 422, wrong.text


def test_the_add_route_takes_the_version_the_matrix_carries(
    client: TestClient, book: dict[str, str]
) -> None:
    """版本跟着**作者看到的那张表**走（同第 4 节，另一条路由）。

    矩阵那一格的补录器和编辑器读的是同一个 `matrix.version.canon_version`；
    从别处另取一次就是第二个会漂的源。
    """
    pid = book["pid"]
    seen = client.get(f"/api/projects/{pid}/chapters/1/matrix").json()["version"]["canon_version"]
    added = client.post(
        f"/api/projects/{pid}/chapters/1/canon/knowledge",
        json={
            "character_id": book["李管家"],
            "secret_id": book["血脉秘密"],
            "type": "KNOWS",
            "expected_canon_version": seen,
        },
    )
    assert added.status_code == 200, added.text

    stale = client.post(
        f"/api/projects/{pid}/chapters/1/canon/knowledge",
        json={
            "character_id": book["萧决"],
            "secret_id": book["血脉秘密"],
            "type": "KNOWS",
            "expected_canon_version": 0,  # 作者手上那份是很久以前的
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["error"] == "stale_base_version"
