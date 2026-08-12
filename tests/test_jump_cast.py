"""日志页那个跳转坐标，会不会把「谁在场」的安全方向翻过来。

ADR 0018 的全部安全性押在一句话上：**推导是超集 = 多禁 = fail-closed**。
在这之前 `?cast=` 里的东西只有两个来源——作者亲手标的场景块，或者从正文推的。
活动日志的 `ActivityJump.cast` 是**第一次由系统自己往面板的在场里塞东西**，
所以它必须单独验一遍方向：

    must_not_reveal 的判据 = 「在场的人里**至少有一个**还不知道」

  · 在场**多**一个人 → 禁令**多**一条 → fail-closed，代价是少写一段
  · 在场**少**一个人 → 禁令**少**一条 → fail-open，代价是崩人设

而同一份在场喂着三条读端：`/matrix`（少一行 = 作者看不见那个人的认知状态）、
`/state`（少一张状态卡）、以及 **`/constraints`（少一个人 = 少一批禁令）**。
第三条才是要命的那条——它的产物是「一份看起来完全正常、只是说破了不该说破的东西的正文」。

── 这份文件测的是「坐标怎么落到面板上」，不是「坐标长什么样」──────────────
`jump.cast` 本身的形状（歧义时为空、本名优先、只有认知矩阵那一档有）在
`tests/test_activity.py` 里。这里只问一件事：**把它交给面板之后，禁令有没有变少。**
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from test_activity import seeded  # noqa: F401  ← 一条抽取 + 一次自动生效的那本书
from test_api import TWIST


QUOTE = "萧决在青云城主府第一次听说了血脉秘密的真相。"
QUOTE_STEWARD = "李管家什么也没说。"
"""两句都在第 1 章正文里（`test_api.BOOK`），所以 `valid_from` 都算成第 1 章。"""

OFFSTAGE = "顾清音"
"""这一章正文里**一次都没被点名**的人 —— 跳转坐标存在的全部理由就是他这一类。

`valid_from` 由引语定（ADR 0006），矩阵的行由本章正文推（ADR 0018）：作者用一句满是
代词的话声明某人的认知时，那一行根本不在表上。坐标要做的是把它加回去，**不是把别人拿掉**。
"""


# ══════════════════════════════════════════════════════════════════════════
# 取数
# ══════════════════════════════════════════════════════════════════════════


def _panel(client: TestClient, pid: str, what: str, chapter: int, **params: str) -> Any:
    r = client.get(f"/api/projects/{pid}/chapters/{chapter}/{what}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _rows(client: TestClient, pid: str, chapter: int, **params: str) -> list[str]:
    return [c["id"] for c in _panel(client, pid, "matrix", chapter, **params)["characters"]]


def _forbidden(client: TestClient, pid: str, chapter: int, **params: str) -> list[str]:
    body = _panel(client, pid, "constraints", chapter, **params)
    return [n["id"] for n in body["must_not_reveal"]]


def _state_ids(client: TestClient, pid: str, chapter: int, **params: str) -> list[str]:
    return [s["node"]["id"] for s in _panel(client, pid, "state", chapter, **params)]


def _coordinate(jump: dict[str, Any]) -> str:
    """坐标交给面板时的那个写法（收的一直是称呼原文，顿号分隔）。"""
    return "、".join(jump["cast"])


def _jump_of(client: TestClient, pid: str, character_id: str) -> dict[str, Any]:
    """日志里指向这个人那一格的坐标。**从真接口读**，不手写一个假的。"""
    r = client.get(f"/api/projects/{pid}/activity", params={"limit": "200"})
    assert r.status_code == 200, r.text
    hits = [
        e["jump"]
        for e in r.json()["entries"]
        if e["jump"]
        and e["jump"]["target"] == "knowledge_cell"
        and e["jump"]["character_id"] == character_id
    ]
    assert hits, "没有指向认知矩阵的日志行 —— 这条测试在空转"
    return hits[0]


def _both_know(client: TestClient, pid: str) -> None:
    """让这一章提到的**两个人都知道**血脉秘密。

    没有这一步，推导出来的在场（萧决 + 李管家）自己就产出一条禁令，
    「坐标把人挤掉了」和「本来就该禁」就分不开——下面几条断言会因为一个巧合而绿。
    """
    for who, quote in (("萧决", QUOTE), ("李管家", QUOTE_STEWARD)):
        r = client.post(
            f"/api/projects/{pid}/declare/knows",
            json={"who": who, "secret": "血脉秘密", "quote": quote},
        )
        assert r.status_code == 200, r.text


def _offstage_believer(client: TestClient, pid: str) -> tuple[str, dict[str, Any]]:
    """建一个没在第 1 章露过面的人，声明他**以为**血脉秘密是别的东西。

    「以为」和「不知道」在 `must_not_reveal` 的判据里是同一侧（`state is not KNOWS`），
    所以他一旦进了在场，那条秘密就必须被禁——**坐标只可能让禁令变多**，这条是它的检验。
    """
    made = client.post(
        f"/api/projects/{pid}/nodes", json={"label": "Character", "name": OFFSTAGE}
    )
    assert made.status_code == 200, made.text
    declared = client.post(
        f"/api/projects/{pid}/declare/believes",
        json={
            "who": OFFSTAGE,
            "secret": "血脉秘密",
            "believed_value": "她以为那只是个传闻",
            "quote": QUOTE,
        },
    )
    assert declared.status_code == 200, declared.text
    node_id = made.json()["id"]
    return node_id, _jump_of(client, pid, node_id)


# ══════════════════════════════════════════════════════════════════════════
# 1. 坐标只许加人 —— 三条读端各验一遍
# ══════════════════════════════════════════════════════════════════════════


def test_the_jump_coordinate_puts_a_row_back_without_taking_the_others_off(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这是整件事的形状**：坐标是「这一行也要在表上」，不是「只看这一行」。

    第 1 章正文里提到的是萧决和李管家，顾清音一次都没被点名。
    把她当坐标交给面板，正确的结果是**三行**——原来那两行一行不少。
    只剩一行的话，作者就看不见另外两个人的认知状态了，而他根本没要求过过滤。
    """
    pid = book["pid"]
    derived = _rows(client, pid, 1)
    assert derived == [book["萧决"], book["李管家"]], f"前提坏了：推导出来的是 {derived}"

    node_id, jump = _offstage_believer(client, pid)
    widened = _rows(client, pid, 1, include=_coordinate(jump))
    assert node_id in widened, "坐标没到面板上 —— 那一行仍然不在表里"
    assert set(derived) <= set(widened), f"推导出来的行被挤掉了：{derived} → {widened}"


def test_a_jump_coordinate_never_shrinks_must_not_reveal(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这条是这份文件存在的理由。**

    同一份在场还喂着 `/constraints`。场景：两个被提到的人都已经知道血脉秘密
    ⇒ 推导下这一章没有禁令；坐标指向的顾清音**以为**是别的 ⇒ 她一进在场就得禁。

    所以正确的结果是禁令**变多**。变少（或者干脆没变化，那说明坐标压根没到）都不行。
    """
    pid = book["pid"]
    _both_know(client, pid)
    _, jump = _offstage_believer(client, pid)
    chapter = jump["chapter_number"]

    baseline = _forbidden(client, pid, chapter)
    assert baseline == [], f"前提坏了：两个人都知道，本来不该有禁令，却有 {baseline}"

    landed = _forbidden(client, pid, chapter, include=_coordinate(jump))
    assert set(baseline) <= set(landed), f"跳一下就少了几条禁令：{baseline} → {landed}"
    assert landed == [book["血脉秘密"]], (
        "坐标没有把那个人算进在场 —— 他还不知道真相，这一条必须被禁"
    )


def test_a_jump_coordinate_never_shrinks_the_state_cards(
    client: TestClient, book: dict[str, str]
) -> None:
    """`/state` 吃的是同一份在场。少一个人 = 少一张状态卡，作者以为那个人这一章没状态。"""
    pid = book["pid"]
    node_id, jump = _offstage_believer(client, pid)
    chapter = jump["chapter_number"]

    baseline = _state_ids(client, pid, chapter)
    landed = _state_ids(client, pid, chapter, include=_coordinate(jump))
    assert node_id in landed, "坐标没到状态卡这一格"
    assert set(baseline) <= set(landed), f"状态卡少了：{baseline} → {landed}"


# ══════════════════════════════════════════════════════════════════════════
# 2. 自守卫：把坐标做成「过滤」的那个实现，这张网抓不抓得住
# ══════════════════════════════════════════════════════════════════════════


def test_the_net_catches_a_coordinate_that_filters_instead_of_adding(
    client: TestClient, book: dict[str, str]
) -> None:
    """**探针**：同一份数据，把坐标按「只看这个人」交上去，禁令必须当场少一条。

    `?cast=` 就是那个收窄口——它是作者亲手标的场景块用的，那种收窄是他自己要的。
    这条断言证明上面那几条不是空转：这份库真的分得出「加人」和「换人」，
    所以「坐标落到面板上之后禁令没少」是一句有内容的话。
    """
    pid = book["pid"]
    _both_know(client, pid)
    # 李管家改成「以为」——于是推导下的在场里有一个人不知道真相，禁令有一条。
    version = client.get(f"/api/projects/{pid}").json()["canon_version"]
    flipped = client.post(
        f"/api/projects/{pid}/canon/knowledge",
        json={
            "character_id": book["李管家"],
            "secret_id": book["血脉秘密"],
            "to_type": "BELIEVES",
            "believed_value": "他以为那只是个传闻",
            "expected_canon_version": version,
        },
    )
    assert flipped.status_code == 200, flipped.text

    derived = _forbidden(client, pid, 1)
    assert book["血脉秘密"] in derived, "前提坏了：推导下本来就该禁这一条"

    filtered = _forbidden(client, pid, 1, cast="萧决")
    assert book["血脉秘密"] not in filtered, (
        "探针失效：把在场收窄成一个知情者之后禁令居然没少 —— "
        "那说明这份库测不出 fail-open，上面几条断言也就证明不了什么"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. 退化那一档不许被坐标撬开
# ══════════════════════════════════════════════════════════════════════════


def test_a_coordinate_cannot_break_open_a_chapter_where_nobody_was_named(
    client: TestClient, book: dict[str, str]
) -> None:
    """一个人都没数出来 = 「不知道谁在场」= 全禁（`ResolvedCast.complete` 那条）。

    这一档最容易被一句「把坐标加进去」撬开：加进去之后在场从空变成一个人，
    于是 `complete` 成立，全禁塌成「只按他一个人算」。**那是 fail-open 的完整形态**
    ——而且恰好发生在系统对这一章一无所知的时候。

    第 2 章正文全是代词（`test_api.BOOK`），一个花名册称呼都没有。
    """
    pid = book["pid"]
    _both_know(client, pid)
    assert _rows(client, pid, 2) == [], "前提坏了：第 2 章居然数出人来了"

    locked = _forbidden(client, pid, 2)
    assert book["血脉秘密"] in locked, "前提坏了：不知道谁在场的时候本来就该全禁"
    # 探针：这一档真的撬得开（换成收窄口就塌了），所以下面那条断言不是空转。
    assert book["血脉秘密"] not in _forbidden(client, pid, 2, cast="萧决")

    still_locked = _forbidden(client, pid, 2, include="萧决")
    assert book["血脉秘密"] in still_locked, (
        "坐标把全禁撬开了：第 2 章一个人都没被点名，加一个知情者进去就不禁了"
    )


def test_an_ambiguous_coordinate_locks_down_instead_of_silently_falling_back(
    client: TestClient, book: dict[str, str]
) -> None:
    """坐标撞上歧义时**不许静默退回推导**——静默退回是最坏的一种。

    后端给坐标之前就滤掉了歧义称呼（`tests/test_activity.py` 钉着那条），
    但那是**日志页那一刻**的判断：作者可以在跳过去之前给另一个人也起同一个称呼。
    真到了那一步，面板要么把这件事说出来、要么全禁，不能装作坐标不存在。

    「师兄」在这本书里指向两个人。
    """
    pid = book["pid"]
    _both_know(client, pid)
    assert _forbidden(client, pid, 1) == [], "前提坏了：两个人都知道，本来不该有禁令"

    body = _panel(client, pid, "constraints", 1, include="师兄")
    assert body["unresolved_cast"] == ["师兄"], "歧义称呼被静默吞掉了"
    assert [n["id"] for n in body["must_not_reveal"]] == [book["血脉秘密"]], (
        "在场没数全却还敢说哪条秘密是安全的"
    )

    matrix = _panel(client, pid, "matrix", 1, include="师兄")
    assert matrix["unresolved_cast"] == ["师兄"], "矩阵那边也得说出来"


# ══════════════════════════════════════════════════════════════════════════
# 4. 坐标里装的是称呼，不是秘密正文
# ══════════════════════════════════════════════════════════════════════════


def test_the_coordinate_carries_a_display_name_and_no_secret_text(
    client: TestClient, book: dict[str, str]
) -> None:
    """**两头都要验**：秘密正文一个字不许在坐标里，显示名一个字不许少。

    过度收窄一样是 bug——作者认不出这条日志说的是哪个秘密，「可查」就没兑现。
    """
    pid = book["pid"]
    declared = client.post(
        f"/api/projects/{pid}/declare/knows",
        json={"who": "萧决", "secret": "血脉秘密", "quote": QUOTE},
    )
    assert declared.status_code == 200, declared.text
    page = client.get(f"/api/projects/{pid}/activity", params={"limit": "200"})
    assert page.status_code == 200, page.text
    assert TWIST not in page.text, "秘密正文出现在日志页上"

    jump = _jump_of(client, pid, book["萧决"])
    # 坐标里只有人物称呼：秘密的名字（哪怕只是显示名）进了在场，就会长出一行
    # 「血脉秘密知道血脉秘密吗」（`mentioned.py` 那条「只收 Character」）。
    assert jump["cast"] == ["萧决"], f"坐标里混进了别的东西：{jump['cast']}"

    # 反面：显示名必须看得见 —— 它在那条日志行自己身上。
    row = [
        e
        for e in page.json()["entries"]
        if e["jump"] and e["jump"]["character_id"] == book["萧决"]
    ][0]
    assert "血脉秘密" in row["subtitle"], "连改的是哪个秘密都说不出来了"


def test_the_event_cast_row_leaves_the_panel_on_the_derived_cast(
    client: TestClient, seeded: dict[str, str]  # noqa: F811
) -> None:
    """`event_cast` 那一档跳的是一份名单，不是矩阵的一行——它不许带坐标。

    带了的后果不是「多一行」而是「右栏别的几格一起按这一个人算」，
    而 `/constraints` 就在那几格里。
    """
    pid = seeded["pid"]
    entries = client.get(f"/api/projects/{pid}/activity", params={"limit": "200"}).json()
    offenders = {
        e["id"]: e["jump"]["cast"]
        for e in entries["entries"]
        if e["jump"] and e["jump"]["target"] != "knowledge_cell" and e["jump"]["cast"]
    }
    assert not offenders, f"这几档不该带在场坐标：{offenders}"
    # 空坐标交上去 = 什么都没变（推导那一份原样）。
    assert _forbidden(client, pid, 1, include="") == _forbidden(client, pid, 1)
