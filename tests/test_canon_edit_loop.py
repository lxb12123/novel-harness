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


from test_activity import (  # noqa: F401  ← poisoned / book / client 是 fixture，靠名字注入
    POISON_CHAPTER,
    _by_id,
    _entries,
    poisoned,
)

from novel_harness.activity import JumpTarget
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
