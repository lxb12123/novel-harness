"""保存之后异步验一遍 —— **只告警，不阻断**（M1-b + 轨道阶段 2）。

两问一条路，钉的是它们各自那件「坏了也不会有任何东西红」的事：

① **秘密有没有对不该知道的人说破。** 写之前算不准（谁在场是写出来的结果），写之后
   数得出来。这一问的价值全在**精确**，所以这份文件不只验「有没有落通知」，还验
   落的那条**点得过去**（锚是从我们自己切的句表上来的，不是模型给的引语）。

② **新写的跟后面已经写完的章抵不抵触。** 这一问的价值全在**它没把后面那一章的内容
   带回来**——把第 6 章的总结塞进给第 2 章的评语里，等于把伏笔从另一扇门递出去，
   而这个产品的一句话定义在那一刻就成了假的。所以下面有一整节专门验这件事，
   而且带对照组（模型那一侧**确实看见了**轨道，只是它一个字都出不来）。

**两问共用的那条纪律：永不阻断。** 挂错成 `validation_blocked` 的后果是作者改一个
老章就把整章的自动整理停掉，而那不会有任何东西报错（C 轨道那份
`tests/test_advisory_notifications.py` 用真跑一遍 DAG 钉着这一条；这里钉的是
**产出这条通知的那一侧从来只产 `text_advisory`**）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from novel_harness import importer, project
from novel_harness.advisory_review import (
    TrackClash,
    _TRACK_NOT_CHECKED,
    numbered_sentences,
    review_saved_chapter,
    review_track_on_demand,
)
from novel_harness.chapter_refresh import ChapterRefreshCoordinator
from novel_harness.db import Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import save_author_summary
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.system_notifications import (
    BLOCKING_KINDS,
    list_open_notifications,
    materialize_notification_outbox,
)
from novel_harness.text import find_one, paragraphs

CHAPTERS = 6
"""这本书写到第 6 章。作者跳回去改的是第 2 章，后面还有 4 章已经写完。"""

HERE = 2
"""作者正在改的那一章。"""

SECRET = "沈孤鸿之死"
KNOWER = "萧决"
OUTSIDER = "顾清音"

SLIP_LINE = "沈孤鸿是被自己人杀的。"
"""第 2 章里那一句。**顾清音就在场，而她到这一章还不知道这件事。**"""

SPOILER = "萧决终于知道自己身上养的是玄血蛊。"
"""第 6 章的总结。**第 2 章的读者还不该知道这件事**，而它必须一个字都不出现在
回给作者的那条评语里。"""

LATER_SETTING = "玄铁令只能在水底唤醒，白光是假的。"
"""第 5 章的总结。第 2 章写它「发出白光」，两边对不上——轨道那一问要抓的就是它。"""


def _chapter_text(number: int) -> str:
    if number == 1:
        # 第 1 章那句话在全书必须唯一：`declare_knows` 的章号由引语算出来
        # （ADR 0006，没人敲过章号），而 `Ledger` 见到多于一处的引语会当场拒。
        return "第1章 甲1\n\n萧决在灵前听完了那件事。\n"
    if number == HERE:
        return (
            f"第{number}章 夜谈\n"
            "\n"
            "萧决把玄铁令放在桌上，它在他掌心发出白光。\n"
            "顾清音抬起头看他。\n"
            f"他说：「{SLIP_LINE}」\n"
        )
    return f"第{number}章 甲{number}\n\n这一章写了些事情。\n"


BOOK = "\n\n".join(_chapter_text(n) for n in range(1, CHAPTERS + 1))

# ── 脚手架 ────────────────────────────────────────────────────────────────


class Reviewer:
    """会数数、会按问题分答案的核对器桩。**这份文件所有的钱都从它这儿过。**"""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.answers = answers or {}
        self.seen: list = []

    def __call__(self, request):
        self.seen.append(request)
        return CompletionResult(
            text=self.answers.get(request.kind, '{"findings":[]}'),
            model="stub-reviewer",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    def calls(self, kind: str) -> int:
        return sum(1 for r in self.seen if r.kind == kind)

    def prompt(self, kind: str) -> str:
        return "\n".join(
            m.content for r in self.seen if r.kind == kind for m in r.messages
        )


class Exploding:
    """模型没配好 / 端点炸了。**它一次都不许把栈甩到作者面前。**"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        raise RuntimeError("模型没配好：先去顶栏 ⚙ 填服务地址")


UPSTREAM_MARKER = "[echoed-by-the-endpoint]"
"""`EchoingFailure` 报错里那个只可能来自异常的标记。

后面章节的内容（`SPOILER`）验的是「泄漏发生了」，它验的是「异常文本本身一个字都
没进来」——两问的 prompt 里有什么各不相同，而这个标记两边都在。"""


class EchoingFailure:
    """端点炸了，**而且报错里把请求原样贴了回来**。

    这不是一个为了写测试想出来的形状：第三方网关的 4xx 常把请求片段回显进 body
    （`{"error":{"message":"...your input was: ..."}}`），内容过滤更是直接回显被
    标记的那一段原文。**报错文本不是我们写的，所以「它里面有没有后面章节的内容」
    不由我们决定**——我们唯一能决定的是它进不进那几个会往下走的字段。
    """

    def __init__(self) -> None:
        self.seen: list = []

    def __call__(self, request):
        self.seen.append(request)
        body = "\n".join(m.content for m in request.messages)
        raise RuntimeError(f"{UPSTREAM_MARKER} 400 —— 上游拒收，原样回显请求：{body}")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[dict]:
    db = tmp_path / "book.db"
    root = tmp_path / "book"
    conn = connect(db)
    migrate(conn)
    pid = project.create(conn, name="夜谈", root_path=str(root)).id
    store = SqliteStoryGraph(conn)
    src = tmp_path / "src.txt"
    src.write_text(BOOK, encoding="utf-8")
    importer.import_book(store, pid, txt=src, root=root)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, KNOWER)
    ledger.declare_node(NodeLabel.CHARACTER, OUTSIDER)
    ledger.declare_node(NodeLabel.OBJECT, "玄铁令")
    conn.commit()
    yield {"conn": conn, "db": str(db), "pid": pid, "store": store, "root": str(root)}
    conn.close()


def _summarize(world: dict, chapter: int, text: str) -> None:
    """给后面那几章一份作者自己写的总结。不调模型，一分钱不花。"""
    save_author_summary(
        world["conn"], project_id=world["pid"], chapter_number=chapter, text=text
    )
    world["conn"].commit()


@pytest.fixture
def written(world: dict) -> dict:
    """后面两章各有一段总结：一段跟第 2 章的设定对不上，一段是剧透。"""
    _summarize(world, 5, LATER_SETTING)
    _summarize(world, CHAPTERS, SPOILER)
    return world


def _review(world: dict, reviewer, **kwargs):
    return review_saved_chapter(
        world["conn"], world["store"], world["pid"], HERE, reviewer=reviewer, **kwargs
    )


def _track_only(world: dict, reviewer, **kwargs):
    """模式二自己叫的那一次：只问轨道，不问秘密，不落通知（轨道阶段 3）。

    形状照抄 `_review`，**入口不同**是这条路的全部差别——所以这儿也只换那一个名字，
    别在测试里替它多做一层包装。
    """
    return review_track_on_demand(
        world["conn"], world["store"], world["pid"], HERE, reviewer=reviewer, **kwargs
    )


def _notices(world: dict):
    materialize_notification_outbox(
        world["conn"], project_id=world["pid"], lease_owner="t"
    )
    return list_open_notifications(world["conn"], world["pid"])


def _found(sentence: int, **rest) -> str:
    return json.dumps({"findings": [{"sentence": sentence, **rest}]}, ensure_ascii=False)


def _sentence_number(world: dict, needle: str) -> int:
    """那一句在句表上的编号。**测试自己也不许硬编码它**——句表怎么切是实现，
    而这些断言问的是「模型指了那一句时会发生什么」。"""
    text = _current_text(world["conn"], world["pid"], HERE)
    for s in numbered_sentences(paragraphs(text)):
        if needle in s.text:
            return s.number
    raise AssertionError(f"句表里找不到「{needle}」")


def _current_text(conn: Connection, project_id: str, chapter: int) -> str:
    row = conn.execute(
        """
        SELECT chapter_snapshot.text AS text
          FROM chapter
          JOIN chapter_snapshot
            ON chapter_snapshot.chapter_id = chapter.id
           AND chapter_snapshot.text_sha256 = chapter.text_sha256
         WHERE chapter.project_id = ? AND chapter.number = ?
        """,
        (project_id, chapter),
    ).fetchone()
    assert row is not None
    return str(row["text"])


# ══════════════════════════════════════════════════════════════════════════
# 一、锚：模型挑的是**我们编的号**，不是它自己的引语
# ══════════════════════════════════════════════════════════════════════════


def test_the_same_sentence_twice_in_one_paragraph_gets_two_different_anchors() -> None:
    """`occurrence_k` 是**非重叠**计数，不是「前面有几句一样的」。

    `他走了。走了。` 这一段上两种数法给出不同的 k，而错的那个数**不会报错**——
    它只会让作者点「去这一句」时落在隔壁半句上。所以句表问的是
    `text/anchor.occurrence_at`（全库唯一那份定义），不自己数。
    """
    para = "他走了。走了。"
    table = numbered_sentences([para])

    assert [(s.text, s.occurrence_k) for s in table] == [("他走了。", 0), ("走了。", 1)]
    # 逐条回代：拿锚去原文里取，取出来的必须还是那一句。
    for s in table:
        assert find_one(para, s.text, s.occurrence_k) == s.text


def test_a_closing_quote_does_not_become_a_sentence_of_its_own() -> None:
    """`他说：「走。」` 是一句，不是「走。」加一个孤零零的 `」`。

    切错的症状是静默的：作者点过去看见的是半个引号。
    """
    assert [s.text for s in numbered_sentences(["他说：「走。」他没回头。"])] == [
        "他说：「走。」",
        "他没回头。",
    ]


def test_the_notice_points_at_the_real_sentence(written: dict) -> None:
    """通知的 `jump` 拿回原文能**逐字取到那一句**。

    这是「能点过去」那句验收的机械形态：锚的三个分量全部来自我们切的句表，
    所以它不可能指向一句作者没写过的话（ADR 0006 那段病史的来源正是反过来做）。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    outcome = _review(written, reviewer)

    assert outcome.clashes == (TrackClash(sentence=number, chapter=5, conflict="setting"),)
    notice = next(n for n in _notices(written) if n.kind == "text_advisory")
    assert notice.jump is not None
    para = paragraphs(_current_text(written["conn"], written["pid"], HERE))
    assert (
        find_one(para[notice.jump.para_index], notice.jump.quote_text, notice.jump.occurrence_k)
        == f"他说：「{SLIP_LINE}」"
    )
def test_a_sentence_number_that_does_not_exist_is_dropped(written: dict) -> None:
    """指一个不存在的句号 → 丢掉。**绝不退回第 1 句**——退回去就是锚到别处。"""
    reviewer = Reviewer({"track": _found(9999, chapter=5, conflict="setting")})

    outcome = _review(written, reviewer)
    assert outcome.clashes == (), "模型指了一个不存在的句号，那条必须被机械复核挡掉"
    assert [n.kind for n in _notices(written)] == []


def test_the_notice_says_who_and_what_without_any_engine_words(written: dict) -> None:
    """`title_params` 说得出哪一句、哪一章——渲染成人话是前端的活（国际化第四批
    Phase B）。`conflict` 发的是封闭枚举值（`setting`），**这一位本身就是设计成
    这样发的**，不算"机器码上屏"：它从不原样显示，前端 `backendMessages.ts` 的
    `CONFLICT_LABEL` 查表翻成"设定对不上"才上屏，那条翻译的断言在
    `backendMessages.test.ts` 里。这儿只钉后端不许多发跟这条通知无关的内部词
    （`track`/`text_advisory`/`node` 这类和"这一句冲突在哪"毫无关系的标识符）。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    _review(written, reviewer)

    notice = next(n for n in _notices(written) if n.kind == "text_advisory")
    assert notice.title_code == "clash_title"
    params = notice.title_params
    assert params is not None
    assert params["sentence"] == number
    assert params["chapter"] == 5
    assert params["conflict"] == "setting"  # 封闭枚举，前端翻译，不是泄漏
    blob = json.dumps(params, ensure_ascii=False)
    for machine in ("track", "text_advisory", "node"):
        assert machine not in blob


# ══════════════════════════════════════════════════════════════════════════
# 三、阶段 2：跟后面已经写完的章抵不抵触
# ══════════════════════════════════════════════════════════════════════════


def test_a_clash_with_a_later_chapter_comes_back_as_three_numbers(written: dict) -> None:
    """出参就是「第几句 ↔ 第几章 + 冲突类型」，三样，没有第四样。"""
    number = _sentence_number(written, "白光")
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    outcome = _review(written, reviewer)

    assert outcome.clashes == (TrackClash(sentence=number, chapter=5, conflict="setting"),)
    notice = next(n for n in _notices(written) if n.kind == "text_advisory")
    assert notice.title_code == "clash_title"
    # "第几句 ↔ 第几章 + 冲突类型"，三样——`rest` 是第四位（还有几条），
    # 跟出参 `TrackClash` 的三个字段不是同一件事：那是模型判定的形状，
    # 这是通知要说的话的形状。`conflict` 发封闭枚举本身，翻成"设定对不上"
    # 是前端的活（`backendMessages.test.ts` 钉着那条翻译）。
    assert notice.title_params == {
        "sentence": number,
        "chapter": 5,
        "conflict": "setting",
        "rest": 0,
    }


def test_a_chapter_number_outside_the_track_is_dropped(written: dict) -> None:
    """说「跟第 99 章抵触」而第 99 章不在这一次的轨道里 → 丢掉。"""
    number = _sentence_number(written, "白光")
    reviewer = Reviewer({"track": _found(number, chapter=99, conflict="setting")})

    assert _review(written, reviewer).clashes == ()
    assert [n.kind for n in _notices(written)] == []


def test_writing_at_the_frontier_asks_the_model_nothing(written: dict) -> None:
    """在最前沿写 = 后面没有已经写完的章 = 这一问一次调用都不发。"""
    reviewer = Reviewer()

    outcome = review_saved_chapter(
        written["conn"], written["store"], written["pid"], CHAPTERS, reviewer=reviewer
    )

    assert reviewer.calls("track") == 0
    assert "后面没有已经写完的章" in outcome.notes["track"]


# ══════════════════════════════════════════════════════════════════════════
# 四、信息隔离：轨道进得了核对器，**出不来**
# ══════════════════════════════════════════════════════════════════════════


def test_the_later_chapters_never_come_back_out_in_the_verdict(written: dict) -> None:
    """**这一条是整件事的地基。**

    第 6 章的总结写着「萧决终于知道自己身上养的是玄血蛊」——那是第 2 章的读者还不该
    知道的事。核对那一侧看得见它（下面对照组证明），但它**一个字都不许**出现在
    回给作者的评语和通知里。判据是数据驱动的：拿这一次轨道里的**每一段总结**去搜。
    """
    number = _sentence_number(written, "白光")
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    outcome = _review(written, reviewer)
    notice = next(n for n in _notices(written) if n.kind == "text_advisory")

    # 对照组：模型那一侧**确实**看见了轨道。少了它，下面几条在「轨道根本没算出来」
    # 的实现上也会全绿。
    prompt = reviewer.prompt("track")
    assert LATER_SETTING in prompt and SPOILER in prompt

    verdict = json.dumps(
        [c.model_dump() for c in outcome.clashes], ensure_ascii=False
    )
    title_blob = json.dumps(notice.title_params, ensure_ascii=False)
    for leaked in (LATER_SETTING, SPOILER, "玄血蛊"):
        assert leaked not in verdict, f"轨道原文进了评语：{leaked}"
        assert leaked not in title_blob, f"轨道原文进了通知：{leaked}"
        assert notice.jump is not None and leaked not in notice.jump.quote_text


def test_the_verdict_has_nowhere_to_put_a_reason() -> None:
    """**形状限死。** `TrackClash` 只有三个字段，多一个 `reason: str` 就是给轨道原文
    开了一条回流的路——而那条路一开，上面那条守卫就只剩运气在守。
    """
    assert set(TrackClash.model_fields) == {"sentence", "chapter", "conflict"}
    with pytest.raises(Exception):
        TrackClash(sentence=1, chapter=5, conflict="setting", reason="第 5 章写着……")


def test_a_chatty_model_loses_its_reason_but_keeps_its_finding(written: dict) -> None:
    """模型多写一句理由：**理由落不了地，那条抵触照样报出来。**

    整份 `extra="forbid"` 校验会把一条真的抵触连着那句理由一起打掉（作者什么都收不到）；
    逐条只取三个键则两头都对——这一条钉的就是那个取舍。
    """
    number = _sentence_number(written, "白光")
    reviewer = Reviewer(
        {
            "track": json.dumps(
                {
                    "findings": [
                        {
                            "sentence": number,
                            "chapter": 5,
                            "conflict": "setting",
                            "reason": SPOILER,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )

    outcome = _review(written, reviewer)

    assert outcome.clashes == (TrackClash(sentence=number, chapter=5, conflict="setting"),)
    assert SPOILER not in json.dumps(
        [c.model_dump() for c in outcome.clashes], ensure_ascii=False
    )


# ── 四之二：**跑砸的那一次**也出不来 ─────────────────────────────────────
#
# 上面几条守的是「跑成了，评语里没有轨道」，而它们全靠 `TrackClash` 的形状。
# `notes` 旁边就是一格自由文本，形状救不了它——原来那儿拼着 `{exc}`，
# 于是「端点回显请求」这一种再普通不过的 4xx 就把后面章节的正文段落原样送进
# 持久化的对话历史。**这一节守的是那半边。**


def test_an_exploded_endpoint_cannot_smuggle_a_later_chapter_out(written: dict) -> None:
    """**报错里带着第 6 章，回执里一个字都不许有。**

    模式二的模型自己调 `check_track`，答案当场回给它并**留在对话历史里**（工具返回值
    是持久的）。所以这一格自由文本的下游不是右栏，是「写第 2 章的模型此后每一轮都
    看得见的东西」——这个产品的一句话定义就在那儿。
    """
    endpoint = EchoingFailure()

    outcome = _track_only(written, endpoint)

    # 对照组：那一次请求里**确实**装着第 6 章的剧透和第 5 章的设定，所以下面搜不到
    # 不是因为轨道压根没算出来。（阶段 4 之后装进去的还包括那几章的正文段落。）
    echoed = "\n".join(m.content for r in endpoint.seen for m in r.messages)
    assert SPOILER in echoed and LATER_SETTING in echoed

    receipt = outcome.model_dump_json()
    for leaked in (SPOILER, LATER_SETTING, "玄血蛊", UPSTREAM_MARKER):
        assert leaked not in receipt, f"异常原文进了回执：{leaked}"
    assert outcome.notes["track"] == _TRACK_NOT_CHECKED
    assert outcome.clashes == () and outcome.notices == ()


def test_the_tool_return_value_carries_no_word_of_the_exception(
    written: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**走真的那条边**：`_a_track_check` → `TrackVerdict` → 工具返回值。

    上一条钉的是源头那一格，这一条钉的是它到底流去了哪儿——`TrackVerdict.note` 是
    模式二的模型真正读到的那串字，而 `TrackClash` 那三个数装不下东西这件事在这条
    路上帮不上忙。两条都要：只有源头那条时，谁把 `note` 换成别的来源都不会红。
    """
    from novel_harness.api import chat

    endpoint = EchoingFailure()
    monkeypatch.setattr(chat, "get_advisory_reviewer", lambda: endpoint)

    verdict = chat._a_track_check(written["conn"], written["store"], written["pid"])(HERE)

    assert endpoint.seen, "核对压根没发生的话，下面搜不到什么都不能说明"
    dumped = verdict.model_dump_json()
    for leaked in (SPOILER, LATER_SETTING, "玄血蛊", UPSTREAM_MARKER):
        assert leaked not in dumped, f"异常原文进了工具返回值（此后每一轮都在）：{leaked}"
    assert verdict.note == _TRACK_NOT_CHECKED, "没跑成也要说得出没跑成（§10 约束 8）"
def test_the_on_demand_check_answers_only_the_track_question(written: dict) -> None:
    """答得出轨道那一问，**但不落通知**。

    落一条通知 = 作者的右栏冒出一件他没做过的事，而右栏那一格的语义是「你该看一眼」，
    不是「模型问过什么」。
    （这儿原来还断言「不问秘密那一问」——秘密整套 2026-08-24 下线了，那半句没了对象。）
    """
    number = _sentence_number(written, "白光")
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    outcome = _track_only(written, reviewer)

    assert outcome.clashes == (TrackClash(sentence=number, chapter=5, conflict="setting"),)
    assert outcome.notices == ()
    assert [n.kind for n in _notices(written)] == []
    # 顺带：这一路也不许把轨道带出来（上面那条对照组已证明模型确实看得见它）。
    assert SPOILER not in outcome.model_dump_json()


def test_the_on_demand_check_does_not_pay_twice_for_the_same_text(written: dict) -> None:
    """连问两次不连付两次钱——幂等判据和保存后那一遍**共用同一份**（内容地址）。

    模式二每动一次笔都可能叫它一次，所以「重复触发」在这条路上是常态。
    """
    reviewer = Reviewer()

    first = _track_only(written, reviewer)
    second = _track_only(written, reviewer)

    assert reviewer.calls("track") == 1
    assert "没发现抵触" in first.notes["track"]
    assert "没有再花钱" in second.notes["track"], "第二次必须说得出它没跑，不是没发现"


# ══════════════════════════════════════════════════════════════════════════
# 五、不阻断 / 不花冤枉钱 / 不炸
# ══════════════════════════════════════════════════════════════════════════


def test_this_side_can_only_produce_the_non_blocking_kind(written: dict) -> None:
    """产出这一侧从来只产 `text_advisory`。

    挂错成 `validation_blocked` 的后果是作者改一个老章就把整章的自动整理停掉，
    而那**不会有任何东西报错**——只会表现成「总结怎么一直不更新」。
    （「挂对了档就真的不停」由 `tests/test_advisory_notifications.py` 真跑一遍 DAG 钉着。）
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer(
        {
            "track": _found(number, chapter=5, conflict="timeline"),
        }
    )

    _review(written, reviewer)

    kinds = {n.kind for n in _notices(written)}
    assert kinds == {"text_advisory"}
    assert not kinds & BLOCKING_KINDS


def test_the_same_text_is_never_paid_for_twice(written: dict) -> None:
    """同一份正文再核对一遍，**一分钱都不再花**（幂等判据是 prompt 的内容哈希）。

    这条路每保存一次都可能被触发一次，所以「重复触发」是常态而不是异常。
    """
    reviewer = Reviewer()

    _review(written, reviewer)
    first = (reviewer.calls("track"), reviewer.calls("track"))
    outcome = _review(written, reviewer)

    assert first == (1, 1)
    assert (reviewer.calls("track"), reviewer.calls("track")) == first
    assert "没有再花钱" in outcome.notes["track"]
    assert "没有再花钱" in outcome.notes["track"]


def test_a_model_that_is_not_configured_leaves_the_author_alone(written: dict) -> None:
    """模型不可用时：**不抛、不落通知、但说得出这一问没跑成**（§10 约束 8）。

    「这一章没问题」和「压根没问」在右栏上长成同一个「什么都没有」，
    而这两件事的下一步动作完全相反。

    **说得出的只有「没跑成」，不包括「为什么」**——那句为什么是端点回的原话，
    而它会一路走到持久化的对话历史里（上面「四之二」那一节）。
    """
    exploding = Exploding()

    outcome = _review(written, exploding)

    assert outcome.clashes == () and outcome.notices == ()
    assert [n.kind for n in _notices(written)] == []
    assert outcome.notes["track"] == _TRACK_NOT_CHECKED
    # 跑成了和没跑成必须是两句不同的话，否则上面那两条断言只是在验一个空壳。
    assert "没跑成" in _TRACK_NOT_CHECKED


def test_garbage_instead_of_json_is_not_an_exception(written: dict) -> None:
    """核对器回了一段散文 → 这一次核对不算数，**不是一个 500**。"""
    reviewer = Reviewer({"track": "我觉得这一章写得挺好的。"})

    outcome = _review(written, reviewer)
    assert outcome.clashes == (), "回了一段散文时不许当成一条抵触"


def test_a_second_scan_of_the_same_text_does_not_wipe_its_own_warning(
    written: dict,
) -> None:
    """同一份正文再扫一遍，**刚报的那条还在**。

    「作者改掉了那一句 ⇒ 收走旧告警」这条清理规则最容易写坏的方向就是它：按「这一章」
    收，一次幂等重扫就把自己刚报的问题抹掉了，而右栏上什么都不会说。所以判据是
    **来源正文的 hash**，不是章。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})
    _review(written, reviewer)
    assert len(_notices(written)) == 1

    _review(written, reviewer)

    assert len(_notices(written)) == 1, "幂等重扫把自己刚报的那条抹掉了"


def test_a_model_outage_does_not_quietly_clear_yesterdays_warning(written: dict) -> None:
    """模型不可用那一次的承诺是「**什么都没发生**」——包括不动旧账。

    正文变了、可这一次一问都没答上来，那就说不出旧那条还成不成立。悄悄把它标成
    「已解决」是发生了一件事，而且是作者最看不见的那一种。
    """
    number = _sentence_number(written, SLIP_LINE)
    _review(written, Reviewer({"track": _found(number, chapter=5, conflict="setting")}))
    assert len(_notices(written)) == 1

    root = Path(written["root"])
    file = next(p for p in sorted(root.rglob("*.md")) if p.stem.startswith(f"{HERE:04d}"))
    importer.save_chapter(
        written["store"],
        written["pid"],
        root,
        HERE,
        file.read_text(encoding="utf-8") + "\n屋里静了很久。\n",
    )
    written["conn"].commit()
    _review(written, Exploding())

    assert [n.kind for n in _notices(written)] == ["text_advisory"]


def test_a_fixed_sentence_stops_warning(written: dict) -> None:
    """作者把那一句改掉之后，**旧的那条告警自己消失**。

    不解决的话，右栏会一直挂着一条锚指向一句已经不存在的话的通知——作者点过去落空，
    而它永远不会自己走。**只动 OPEN**：他自己按过的「忽略」是终态。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})
    _review(written, reviewer)
    assert len(_notices(written)) == 1

    # 改掉那一句 → 新快照 → 重新核对，这一次什么都没发现。
    root = Path(written["root"])
    file = next(p for p in sorted(root.rglob("*.md")) if p.stem.startswith(f"{HERE:04d}"))
    importer.save_chapter(
        written["store"],
        written["pid"],
        root,
        HERE,
        file.read_text(encoding="utf-8").replace(SLIP_LINE, "他什么都没说。"),
    )
    written["conn"].commit()
    _review(written, Reviewer())

    assert [n.kind for n in _notices(written)] == []


# ══════════════════════════════════════════════════════════════════════════
# 六、接线：什么时候真的会跑，跑完作者看不看得见
# ══════════════════════════════════════════════════════════════════════════


def _runtime(world: dict, reviewer):
    """真后台运行时 + 桩 adapter（同 `tests/test_focus_debounce.py` 那一份的形状）。"""
    from novel_harness.api.background_runtime import BackgroundRuntime
    from novel_harness.draft.rolling_summary import RollingSummarizer
    from novel_harness.extract import RawChapterAnalysis
    from novel_harness.extract.runner import ExtractionRunner

    def conn_factory():
        return connect(Path(world["db"]))

    def extraction(_request):
        return CompletionResult(
            text=RawChapterAnalysis(
                events=(), state_updates=(), character_profiles=()
            ).model_dump_json(),
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    def summarize(_request):
        return CompletionResult(
            text="这一章他们说了些话。",
            model="stub-model",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    return BackgroundRuntime(
        db_path=world["db"],
        connection_factory=conn_factory,
        runner_factory=lambda: ExtractionRunner(conn_factory, extraction),
        summarizer_factory=lambda: RollingSummarizer(conn_factory, summarize),
        reviewer_factory=lambda: reviewer,
        owner="test",
        poll_seconds=100,
        autonomy_seconds=3600.0,
    )


@pytest.fixture
def client(written: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("NH_DB", written["db"])
    monkeypatch.setenv("NH_BACKGROUND_RUNTIME", "0")
    from novel_harness.api.app import app

    with TestClient(app) as c:
        yield c


def test_the_author_sees_the_warning_without_anyone_calling_materialize(
    client, written: dict
) -> None:
    """**整条线**：保存 → 切走 → 后台核对 → 右栏真的多一条能点过去的通知。

    这一条同时补上一个此前没人管的洞：`enqueue_*` 只写通知 outbox（和业务状态同事务，
    不变量 29），而在这批改动之前**生产上没有任何东西把它搬进 `system_notification`**
    ——包括阻断那一条。所以这里**故意不调 `materialize_notification_outbox`**：
    通知必须靠调度那一侧自己搬过来，作者才看得见。
    """
    pid = written["pid"]
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})
    runtime = _runtime(written, reviewer)

    body = client.get(f"/api/projects/{pid}/chapters/{HERE}/text").json()
    client.post(f"/api/projects/{pid}/focus", json={"chapter": CHAPTERS})
    saved = client.put(
        f"/api/projects/{pid}/chapters/{HERE}/text",
        json={
            "markdown": body["markdown"] + "\n屋里静了很久。\n",
            "expected_text_sha256": body["text_sha256"],
        },
    )
    assert saved.status_code == 200, saved.text
    runtime.pump_once()

    notices = [
        n for n in list_open_notifications(written["conn"], pid) if n.kind == "text_advisory"
    ]
    assert len(notices) == 1, "右栏什么都没多——通知躺在 outbox 里没人搬"
    assert notices[0].jump is not None and SLIP_LINE in notices[0].jump.quote_text
    assert notices[0].chapter_number == HERE


def test_the_review_waits_until_the_author_leaves_that_chapter(
    client, written: dict
) -> None:
    """**它是第二个会花钱的后台动作，所以它守同一条防抖。**

    作者正盯着第 2 章改一下午 → 一次都不核对；切走 → 才核对。不问焦点的话，
    「改一下午存三十次」就变成三十次核对——那正是总结那一支刚修好的病，
    换个模块又长一遍。
    """
    pid = written["pid"]
    reviewer = Reviewer()
    runtime = _runtime(written, reviewer)

    client.post(f"/api/projects/{pid}/focus", json={"chapter": HERE})
    body = client.get(f"/api/projects/{pid}/chapters/{HERE}/text").json()
    text, sha = body["markdown"], body["text_sha256"]
    for i in range(5):
        text = text + f"\n他又改了第 {i} 遍。\n"
        saved = client.put(
            f"/api/projects/{pid}/chapters/{HERE}/text",
            json={"markdown": text, "expected_text_sha256": sha},
        )
        assert saved.status_code == 200, saved.text
        sha = saved.json()["text_sha256"]
        runtime.pump_once()
    assert len(reviewer.seen) == 0, (
        f"作者正改着这一章，5 次保存核对了 {len(reviewer.seen)} 次"
    )

    client.post(f"/api/projects/{pid}/focus", json={"chapter": CHAPTERS})
    runtime.autonomy_once()
    runtime.pump_once()

    assert reviewer.calls("track") == 1, "切走之后该核对一次"
    assert reviewer.calls("track") == 1


def test_a_blocked_chapter_is_not_reviewed(
    client, written: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸门说停 → **一次核对都不发**。

    一章已经判定阻断的正文不值得再为它花一次模型调用（它的总结和抽取也没跑）。
    这里把协调器的结论换成 `blocked` 而不是去造一条真的 R2 命中：**被验的是接线
    那一支**——「闸门放行了才核对」这句话在 `_run_one` 里只有一行，而它错了不会
    有任何东西报错，只会多花钱。

    打的是**类**上的 `run`，不是某个实例的：协调器 2026-09-05 起是每条 attempt
    现开现关的（它必须用执行线程自己的连接，见 `background_runtime._run_one`），
    实例在这里拿不到。
    """
    pid = written["pid"]
    reviewer = Reviewer()
    runtime = _runtime(written, reviewer)
    monkeypatch.setattr(
        ChapterRefreshCoordinator, "run", lambda *a, **k: {"validation": "blocked"}
    )

    body = client.get(f"/api/projects/{pid}/chapters/{HERE}/text").json()
    client.post(f"/api/projects/{pid}/focus", json={"chapter": CHAPTERS})
    client.put(
        f"/api/projects/{pid}/chapters/{HERE}/text",
        json={
            "markdown": body["markdown"] + "\n他站了起来。\n",
            "expected_text_sha256": body["text_sha256"],
        },
    )
    runtime.pump_once()

    assert reviewer.seen == []


def test_backfilling_an_imported_book_does_not_buy_a_single_review(
    client, written: dict
) -> None:
    """**接一本旧书进来，核对一次都不跑。**

    补总结那一轮会为每一章下一张单；顺手在那儿核对一遍等于**把接书的成本翻一倍**
    （每章从「总结 + 抽取」两次调用变成四次）。这一批交付的是「保存之后验一遍」，
    「扫全书找矛盾」是另一个决定——它有它自己的成本和一次几十条通知的噪声。
    判据是「这一版正文是不是作者存出来的」（`AttemptTarget.authored`），
    而这一条就是那个判据唯一的行为证据。
    """
    reviewer = Reviewer()
    runtime = _runtime(written, reviewer)

    # 全书都还是导进来的那一版：补总结的单一张不少，核对一次不发。
    assert runtime.autonomy_once() > 0, "一张单都没下的话这条测试什么都没验到"
    runtime.pump_once()

    assert reviewer.seen == []



# ══════════════════════════════════════════════════════════════════════════
# 三级下探回来的那几段，**必须真的进这一份 prompt**（ADR 0038 阶段 4）
# ══════════════════════════════════════════════════════════════════════════


def test_the_dug_out_paragraphs_actually_reach_the_reviewer() -> None:
    """不进 = 「算完扔掉」，而那正是这一整批工作反复在修的病。

    三级只在**总结可证明地没提到某个锚点**时才下探，所以这几段是那一章里唯一能回答
    「你写的这句跟它抵不抵触」的证据——二级那份总结对这几样东西只字未提。
    它红了代表那条链断在最后一米：钱花了（下探是免费的，但核对那一次不是），
    证据没进去，模型照旧只看得见一份不提这件事的总结。
    """
    from novel_harness.advisory_review import _track_request, numbered_sentences
    from novel_harness.track import Track, TrackExcerpt

    track = Track(
        chapter=12,
        frontier=20,
        note="x",
        excerpts=[
            TrackExcerpt(
                chapter_number=15, para_index=2, text="萧决把它按进水里。", surfaces=["萧决"]
            )
        ],
    )
    body = _track_request(12, numbered_sentences(["他举起了它。"]), track).messages[1].content

    assert "萧决把它按进水里。" in body, "下探回来的原文没进 prompt —— 又是算完扔掉"
    assert "第 15 章第 3 段" in body, "段号没 +1 —— 说给模型听的那一行还是引擎的 0-based 门牌"


def test_no_excerpt_block_when_nothing_was_dug_out() -> None:
    """没下探就**整块不出**，不留一个空标题。

    空标题会让模型以为「那几章的原文我看过了，里面没东西」——而真相是压根没去看。
    """
    from novel_harness.advisory_review import _track_request, numbered_sentences
    from novel_harness.track import Track

    track = Track(chapter=12, frontier=20, note="x")
    body = _track_request(12, numbered_sentences(["他举起了它。"]), track).messages[1].content
    assert "原文片段" not in body
