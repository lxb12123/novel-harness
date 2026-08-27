"""轨道 —— 改旧章时的后向锚定（阶段 1：接线，全免费）。

作者写到第 20 章，跳回去改第 12 章。在这批代码之前，引擎**完全不知道第 13 章之后
存在**：拼上下文只取更早的，续写只给光标之前的，后台算权重时把章号更大的一律当成
「还没写」。于是改出来的东西可能跟第 18 章直接打架，而没有任何东西会发现。

**索引在跑、反查函数在、定位能力在——缺的是一根线。** 这一批钉的就是那根线：

1. **「不是最新章」的判断真的在写作那条路上做了**，而且是最新章时什么都不变；
2. **一级（这段字命中了谁）+ 二级（后面哪几章的总结提到了它们）真的被调到了**，
   零模型调用、零花费；
3. **按相关性取，不按位置取**——离得近但没共同提到任何东西的章不进来，
   离得远但共同提到了三样的章排在最前；
4. **续写改老章时 prompt 里有【下文】，且明说了「别重写」。**

信息隔离那一条（轨道永不进 Writer 的 prompt）在 `tests/test_track_isolation.py`，
**分开放是因为它是守卫不是行为**：这份文件描述这套东西做什么，那份文件挡住一次
会把产品核心主张从背后拆掉的改动。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel_harness import importer, project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.rolling_summary import save_author_summary
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.track import TRACK_CHAPTER_LIMIT, build_track

CHAPTERS = 20
"""这本书有多少章。作者跳回去改的是第 12 章，后面还有 8 章已经写完。"""

HERE = 12
"""作者正在改的那一章。"""

SHORT = {"language": "zh", "min_units": 80, "target_units": 150, "max_units": 300}
"""续写档：一两段（同 `tests/test_continuation_memory.py` 那一份）。"""

EDIT = "萧决把玄铁令举起来，它在他掌心发出白光。"
"""作者刚在第 12 章敲下的那一段。它同时提到一个人物和一件物件——
**六类同一次查询捞回来**这条性质，没有物件在里面就验不到。"""


# ── 脚手架 ────────────────────────────────────────────────────────────────


def _book(tmp_path: Path) -> dict[str, str]:
    """一本 `CHAPTERS` 章的书 + 花名册里一个人物、一件物件。

    **正文本身不提这两个名字**：这批断言全都建立在「总结里提到了什么」上，
    正文里再撒一遍只会让某条断言在错误的理由下变绿。
    """
    db = tmp_path / "b.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="轨道", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "萧决")
    ledger.declare_node(NodeLabel.OBJECT, "玄铁令")
    src = tmp_path / "s.txt"
    src.write_text(
        "\n\n".join(f"第{n}章 甲{n}\n\n这一章写了些事情。\n" for n in range(1, CHAPTERS + 1)),
        encoding="utf-8",
    )
    importer.import_book(store, pid, txt=src, root=root)
    conn.commit()
    conn.close()
    return {"db": str(db), "pid": pid, "root": str(root)}


def _summarize(book: dict[str, str], chapter: int, text: str) -> None:
    """给这一章一份**照当前正文写的**总结。不调模型，一分钱不花。"""
    conn = connect(book["db"])
    try:
        save_author_summary(
            conn, project_id=book["pid"], chapter_number=chapter, text=text
        )
    finally:
        conn.close()


def _track(book: dict[str, str], *, chapter: int = HERE, text: str = EDIT, **kwargs):
    conn = connect(book["db"])
    try:
        return build_track(
            conn, SqliteStoryGraph(conn), book["pid"], chapter=chapter, text=text, **kwargs
        )
    finally:
        conn.close()


@pytest.fixture
def book(tmp_path: Path) -> dict[str, str]:
    return _book(tmp_path)


@pytest.fixture
def written(book: dict[str, str]) -> dict[str, str]:
    """一本已经写到第 20 章、总结零零散散有几段的书。"""
    _summarize(book, 5, "萧决在城主府住下，玄铁令收在袖子里。")  # 更早的章
    _summarize(book, 15, "玄铁令只能在水底唤醒，白光是假的。")  # 后面，提到物件
    _summarize(book, 16, "城里下了一场雨，街上没有人。")  # 后面，什么都没提
    _summarize(book, 18, "萧决终于知道自己身上养的是玄血蛊。")  # 后面，提到人物
    return book


# ══════════════════════════════════════════════════════════════════════════
# 一、触发：只在「不是最新章」时启用
# ══════════════════════════════════════════════════════════════════════════


def test_writing_at_the_frontier_costs_nothing_and_finds_nothing(
    written: dict[str, str],
) -> None:
    """在最前沿写 = 照旧，什么都不变。**这一条是「不许乱花力气」那一半。**

    第 20 章后面没有已经写完的正文，所以连花名册都不必解析——两级锚定一级都不跑。
    """
    track = _track(written, chapter=CHAPTERS)

    assert track.at_frontier is True
    assert track.anchors == []
    assert track.chapters == []
    assert "后面没有已经写完的章" in track.note


def test_a_chapter_that_does_not_exist_yet_is_still_the_frontier(
    written: dict[str, str],
) -> None:
    """写第 21 章（还没建）也算最前沿——判据是「后面有没有已经写完的章」，
    不是「这一章在不在库里」。"""
    assert _track(written, chapter=CHAPTERS + 1).at_frontier is True


def test_jumping_back_leaves_the_frontier_behind(written: dict[str, str]) -> None:
    """改第 12 章 = 后面还有 8 章已经写完。**这就是今天引擎完全看不见的那 8 章。**"""
    track = _track(written)

    assert track.at_frontier is False
    assert track.frontier == CHAPTERS
    assert track.chapter == HERE


# ══════════════════════════════════════════════════════════════════════════
# 二、锚定：按相关性，不按位置
# ══════════════════════════════════════════════════════════════════════════


def test_the_first_level_hits_both_the_person_and_the_object(
    written: dict[str, str],
) -> None:
    """一级：扫作者刚改的那段字，命中花名册里哪些东西。**六类都收。**

    「玄铁令第 15 章写着只能在水底唤醒，你第 12 章写它发白光」这种，
    和人物那条线必须是同一次查询捞回来的——分成两套的那一天，物件那一半会先烂掉。
    """
    assert [a.node.name for a in _track(written).anchors] == ["萧决", "玄铁令"]


def test_the_second_level_brings_back_only_the_chapters_that_share_something(
    written: dict[str, str],
) -> None:
    """二级：后面哪几章的总结也提到了它们。

    **第 16 章离得最近，但它一个字都没共同提到，所以不进来**——这一条就是
    「不取后面 N 章」：区间是死的，猜小了漏、猜大了全是噪声。
    """
    got = {row.chapter_number for row in _track(written).chapters}

    assert got == {15, 18}
    assert 16 not in got, "离得近但没共同提到任何东西的章不该进来"


def test_earlier_chapters_never_come_back_no_matter_how_related(
    written: dict[str, str],
) -> None:
    """第 5 章的总结把两样都提到了，但它在**前面**。

    前面那些章归记忆层（`product_context`），而那一侧**允许**进 Writer 的 prompt；
    这一侧不允许。两边混在一起的那一天，混的不是数据是权限。
    """
    assert 5 not in {row.chapter_number for row in _track(written).chapters}


def test_the_summary_text_comes_back_with_it(written: dict[str, str]) -> None:
    """二级要的是总结**原文**，不是「第 15 章跟你有关」这么一句。

    验证那一侧要拿它去判抵不抵触，只给章号等于让它再查一遍。
    """
    fifteen = next(r for r in _track(written).chapters if r.chapter_number == 15)
    assert fifteen.summary == "玄铁令只能在水底唤醒，白光是假的。"
    assert fifteen.author_written is True


def test_the_most_related_chapter_comes_first_even_when_it_is_the_farthest(
    written: dict[str, str],
) -> None:
    """相关度 = 共同提到了几样，**不是离得多近**。

    第 20 章两样都提到了、离得最远，它必须排在只提到一样的第 15 / 18 章前面。
    没有这一条，`TRACK_CHAPTER_LIMIT` 一砍就把最该看的那一章砍掉了。
    """
    _summarize(written, 20, "萧决在水底唤醒了玄铁令。")

    assert [r.chapter_number for r in _track(written).chapters] == [20, 15, 18]


def test_the_limit_is_a_quota_that_keeps_the_top_of_the_list(
    written: dict[str, str],
) -> None:
    """名额砍的是**队尾**，不是随便几章。

    而且被砍掉的那几章要在 `note` 里数出来：「后面 3 章相关，这一次带回 1 章」和
    「后面根本没人提过它们」是两件事，下一步动作完全不同（调名额 vs 去补总结）。
    """
    _summarize(written, 20, "萧决在水底唤醒了玄铁令。")
    one = _track(written, limit=1)

    assert [r.chapter_number for r in one.chapters] == [20]
    assert "后面 3 章的总结跟它们相关" in one.note
    assert "带回最相关的 1 章" in one.note
    assert _track(written, limit=0).chapters == []


def test_the_default_quota_is_the_documented_one(written: dict[str, str]) -> None:
    """默认名额就是那个常量，不是某处又抄了一个数。"""
    assert len(_track(written, limit=TRACK_CHAPTER_LIMIT).chapters) == len(
        _track(written).chapters
    )


# ══════════════════════════════════════════════════════════════════════════
# 三、空的四种原因，说得出是哪一种（§10 约束 8）
# ══════════════════════════════════════════════════════════════════════════


def test_nothing_on_the_roster_in_this_edit_says_so(written: dict[str, str]) -> None:
    """这段字里没有花名册上的东西 —— 和「后面没人提过它们」是两件事。"""
    track = _track(written, text="他把窗子推开，风灌进来。")

    assert track.anchors == []
    assert track.chapters == []
    assert "没有出现" in track.note


def test_anchors_with_no_later_mention_keep_the_anchors_in_the_answer(
    book: dict[str, str],
) -> None:
    """认出了东西、但后面一段总结都没提过它们。

    **`anchors` 仍然要交出去**：它证明一级跑过了，而「一级没认出东西」和
    「二级没查到」的下一步动作完全不同（前者该去看花名册，后者该去补总结）。
    """
    track = _track(book)  # 一段总结都还没有

    assert [a.node.name for a in track.anchors] == ["萧决", "玄铁令"]
    assert track.chapters == []
    assert "没有一段提到它们" in track.note


def test_a_book_with_no_chapters_at_all_says_that_instead(tmp_path: Path) -> None:
    """空书：`frontier` 是 0，而 0 不许被读成「在最前沿」以外的任何东西。"""
    db = tmp_path / "empty.db"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="空", root_path=str(tmp_path / "b")).id
    conn.commit()
    try:
        track = build_track(conn, SqliteStoryGraph(conn), pid, chapter=1, text=EDIT)
    finally:
        conn.close()

    assert track.frontier == 0
    assert track.at_frontier is True
    assert "还没有章" in track.note


# ══════════════════════════════════════════════════════════════════════════
# 四、【下文】：光标后面那截已经写好的正文
# ══════════════════════════════════════════════════════════════════════════


def _continuation_args() -> dict[str, object]:
    from novel_harness.draft.assemble import continuation_goal
    from novel_harness.draft.length import DraftLanguage, LengthSpec

    return {
        "goal": continuation_goal(DraftLanguage.ZH),
        "length": LengthSpec(
            language=DraftLanguage.ZH, min_units=80, target_units=150, max_units=300
        ),
    }


def _ctx(book: dict[str, str]):
    from novel_harness.draft.context import unknown_cast_constraints

    conn = connect(book["db"])
    try:
        return unknown_cast_constraints(SqliteStoryGraph(conn), book["pid"], HERE)
    finally:
        conn.close()


def test_the_following_text_block_tells_the_model_not_to_rewrite_it(
    book: dict[str, str],
) -> None:
    """**不说清楚，模型会把下文也当成「要写的」，重写一遍。**

    所以这一块必须自带那句话，而且必须在块首——写在几千字后面，模型读到它的时候
    早就把下文当成待写的段落读完了（同滚动总结那条免责的病历）。
    """
    from novel_harness.draft.product_assemble import assemble_continuation

    messages = assemble_continuation(
        _ctx(book), (), following_text="他没有回头。门在身后合上。", **_continuation_args()
    )
    prompt = "\n".join(m["content"] for m in messages)

    assert "【下文】" in prompt
    assert "不要重写" in prompt
    assert "他没有回头。门在身后合上。" in prompt
    # 【上文】那一块是冻结的 `assemble._base()` 发的；这一块是在它**外面**追加的。
    assert prompt.index("【下文】") > prompt.index("【这一场要写】")


def test_no_following_text_leaves_the_prompt_byte_identical(book: dict[str, str]) -> None:
    """没有下文（在章末往下写，常态）→ 整块不出现，和 `assemble()` 逐字节相同。

    续写的整份 prompt 只有几百字，凭空多一块什么都没说的东西既不带信息，
    又把前缀缓存的形状改了（ADR 0019 边界六）。
    """
    from novel_harness.draft.assemble import assemble
    from novel_harness.draft.product_assemble import assemble_continuation

    ctx = _ctx(book)
    args = _continuation_args()

    assert assemble_continuation(ctx, (), following_text="", **args) == assemble(ctx, **args)
    assert assemble_continuation(ctx, (), following_text="   \n ", **args) == assemble(
        ctx, **args
    )


def test_the_following_text_is_cut_from_the_far_end_not_the_near_one(
    book: dict[str, str],
) -> None:
    """超出额度时留住**紧挨着光标**的那一截。

    上文截末尾、下文截开头，两刀都朝着光标切：离光标越远的字，对「这一段接不接得上」
    越没用。切错方向的症状是「模型接的是三千字之后那一段」，没有任何东西会红。
    """
    from novel_harness.draft.product_assemble import assemble_continuation

    messages = assemble_continuation(
        _ctx(book),
        (),
        following_text="近" * 10 + "远" * 10,
        previous_tail_limit=10,
        **_continuation_args(),
    )
    prompt = "\n".join(m["content"] for m in messages)

    assert "近" * 10 in prompt
    assert "远" not in prompt


# ══════════════════════════════════════════════════════════════════════════
# 五、真链路：写作那条路上真的调到了（今天一次都不调）
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(
    written: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NH_DB", written["db"])
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    # 环境变量是连接参数的兜底档；跑测试的人 shell 里恰好有一个，窗口就跟着他的模型变。
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from novel_harness.api.app import app

    with TestClient(app) as c:
        reply = c.put(
            "/api/settings",
            json={
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "api_key": "sk-test",
                "context_window": 128_000,
            },
        )
        assert reply.status_code == 200, reply.text
        yield c


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, str]]]:
    """把模型换成桩，**把发出去的 messages 留下来**——隔离那一条断言的对象就是它。"""
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    observed: list[list[dict[str, str]]] = []

    def fake(messages, *, config=None, plan=None, client=None) -> CompletionResult:
        observed.append(messages)
        return CompletionResult(text="正文" * 75, model="fake", finish_reason="stop")

    monkeypatch.setattr(generate_mod, "complete", fake)
    return observed


def _continue(
    client: TestClient,
    written: dict[str, str],
    chapter: int,
    *,
    following: str = "",
) -> dict:
    reply = client.post(
        f"/api/projects/{written['pid']}/chapters/{chapter}/draft",
        json={
                        "previous_tail": EDIT,
            "following_text": following,
            "length": SHORT,
        },
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_the_writing_path_now_actually_asks_for_the_track(
    client: TestClient, written: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**今天这条路一次都不调它。** 这一条红了就说明那根线又断了。"""
    _capture(monkeypatch)
    track = _continue(client, written, HERE)["track"]

    assert track["frontier"] == CHAPTERS
    assert [a["node"]["name"] for a in track["anchors"]] == ["萧决", "玄铁令"]
    assert [row["chapter_number"] for row in track["chapters"]] == [15, 18]


def test_the_frontier_chapter_gets_an_empty_track_and_an_untouched_prompt(
    client: TestClient, written: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**是最新章时行为一字不变**：轨道是空的，而且下文送上来也不进 prompt。

    最新章的常态是往末尾写，光标后面没有字。要在最新章中间插写时也给，
    那是一次单独的产品决定，不藏在这一刀里。
    """
    seen = _capture(monkeypatch)
    body = _continue(client, written, CHAPTERS, following="后面这一段已经写好了。")
    with_following = "\n".join(m["content"] for m in seen[0])
    seen.clear()
    _continue(client, written, CHAPTERS)
    without = "\n".join(m["content"] for m in seen[0])

    assert body["track"]["chapters"] == []
    assert "【下文】" not in with_following
    assert "后面这一段已经写好了。" not in with_following
    # **逐字节**，不是「没看见那几个字」：多一个空块也算行为变了（前缀缓存的形状）。
    assert with_following == without


def test_editing_an_old_chapter_puts_the_following_text_into_the_prompt(
    client: TestClient, written: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """改旧章 + 光标后面还有正文 → 模型这一次看得见它，并被告知别重写。"""
    seen = _capture(monkeypatch)
    _continue(client, written, HERE, following="他没有回头。门在身后合上。")
    prompt = "\n".join(m["content"] for m in seen[0])

    assert "【下文】" in prompt
    assert "不要重写" in prompt
    assert "他没有回头。门在身后合上。" in prompt


def test_the_track_costs_no_model_call(
    client: TestClient, written: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """两级锚定**零模型调用**：这一次续写仍然只发一次调用。

    它坏起来的样子不是报错，是作者的账单多出一笔他没按过的钱。
    """
    seen = _capture(monkeypatch)
    _continue(client, written, HERE)

    assert len(seen) == 1


# ⚠️ **`test_a_whole_chapter_draft_refuses_a_following_text` 2026-08-26 删了。**
#
# 它量的是「整章起草收到 `following_text` 要 422，别静默丢掉」。那条闸活在
# `DraftRequest._check_mode_shape` 里，而**整个「起草一整章」的 HTTP 入口那天删了**
# （零调用方；模式二的起草工具走进程内直调）——`/draft` 今天只有续写一种形状，
# 而续写**本来就该收**这一位。**没有入口就没有那种误用**，留着这条测试等于给一个
# 不存在的分支立守卫。
#
# 「静默丢掉才是坏的」那条纪律没跟着走：它在这个文件里由下面几条继续钉着
# （`following_text` 只在不是最前沿时才渲染，而不是悄悄不用）。


# ══════════════════════════════════════════════════════════════════════════
# 三级下探（ADR 0038 阶段 4，2026-08-23 维护者裁定要做）
#
# 判据是**集合**不是语义：这一章的总结提到的锚点 ⊊ 作者刚改那段字里的锚点
# ⇒ 那份总结可证明地对我们正关心的某几样东西只字未提 ⇒ 下探它的原文相关段落。
# ══════════════════════════════════════════════════════════════════════════


def _rewrite(book: dict[str, str], chapter: int, text: str) -> None:
    """把某一章的正文换掉（走产品保存那条路，`current_chapter_text` 才认得出它）。"""
    from novel_harness.graph.models import ChapterSpec

    conn = connect(book["db"])
    try:
        store = SqliteStoryGraph(conn)
        store.commit_chapter_snapshot(
            ChapterSpec(
                project_id=book["pid"],
                number=chapter,
                heading=f"第{chapter}章 甲{chapter}",
                path=importer.chapter_path(chapter),
                text=text,
            ),
            expected_text_sha256=store.current_chapter_hash(book["pid"], chapter),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_summary_that_covers_every_anchor_is_never_dug_into(book: dict[str, str]) -> None:
    """总结**已经**提到了全部锚点 ⇒ 一段原文都不下探。

    下探只是把同一件事用 5 倍的字再读一遍，而验证那一侧的料量有上限，多带的会把
    该带的挤掉。它红了代表判据从「差集非空」滑成了「总是下探」。
    """
    _summarize(book, 15, "萧决把玄铁令收好了。")  # 两个锚点都提到
    _rewrite(book, 15, "萧决在水底站着。\n\n玄铁令亮了。\n")
    track = _track(book)
    assert [row.chapter_number for row in track.chapters] == [15]
    assert track.excerpts == [], "总结已经覆盖了全部锚点，不该下探"


def test_a_summary_that_misses_an_anchor_gets_dug_into_but_only_those_paragraphs(
    book: dict[str, str],
) -> None:
    """总结只提到一半 ⇒ 下探，**而且只取命中缺口那几样东西的段落**。

    第 15 章的总结只说了玄铁令，没说萧决；作者刚改的那段字两样都提了。
    缺口 = {萧决}，下探回来的必须是提到萧决的那几段，**不是整章**。

    它红了代表要么下探没发生（那一章的原文永远看不到），要么取成了整章——
    后者的代价同「原文兜底」那条：一章 3,000–5,000 字会把二级那八章总结全挤掉。
    """
    _summarize(book, 15, "玄铁令只能在水底唤醒。")  # 只提物件，没提萧决
    _rewrite(
        book, 15, "萧决走到水边。\n\n那天风很大，什么都没发生。\n\n萧决把它按进水里。\n"
    )
    track = _track(book)

    assert [e.chapter_number for e in track.excerpts] == [15, 15]
    # **不钉字面段号**：存进库的正文带着标题行，段号是 `text.paragraphs` 的口径，
    # 不是我在这条测试里数的那个。钉的是「顺序稳定、0-based、只取命中的那几段」。
    numbers = [e.para_index for e in track.excerpts]
    assert numbers == sorted(numbers) and numbers[0] >= 0
    assert len(set(numbers)) == len(numbers), "同一段被带回了两次"
    assert all("萧决" in e.text for e in track.excerpts)
    assert "什么都没发生" not in "".join(e.text for e in track.excerpts), (
        "取回了没命中缺口的段落 —— 那是把整章搬过来，不是下探"
    )
    assert "下探" in track.note, "带回了原文却不在回执里说 —— 零和非零都要带着理由"


def test_the_dig_stops_on_a_chapter_boundary_not_in_the_middle_of_one(
    book: dict[str, str],
) -> None:
    """预算用完**停在章的边界上**，不是段的边界。

    半章证据在下游和整章长得一模一样，而「我看全了」和「我看了一半」的下一步不同。
    少一章是可数的，半章是不可数的。
    """
    _summarize(book, 15, "玄铁令在这一章出现过。")
    _summarize(book, 16, "玄铁令在这一章也出现过。")
    _rewrite(book, 15, "萧决说了很长很长很长很长很长的一段话。\n\n萧决又说了一句。\n")
    _rewrite(book, 16, "萧决在这里。\n")

    tight = _track(book, excerpt_units=12)
    chapters = {e.chapter_number for e in tight.excerpts}
    assert 15 not in chapters, "第 15 章装不下却带回了它的一部分 —— 那就是半章证据"
    assert chapters <= {16}


def test_zero_budget_means_level_two_only(book: dict[str, str]) -> None:
    """`excerpt_units=0` = 只做到二级（模式一 2026-08-22 的行为，留着当退路）。"""
    _summarize(book, 15, "玄铁令只能在水底唤醒。")
    _rewrite(book, 15, "萧决走到水边。\n")
    assert _track(book, excerpt_units=0).excerpts == []
    assert _track(book).excerpts != [], "默认档就该下探 —— 不然这条路等于没接"
