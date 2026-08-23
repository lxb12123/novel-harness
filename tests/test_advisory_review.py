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
    SecretSlip,
    TrackClash,
    numbered_sentences,
    review_saved_chapter,
)
from novel_harness.db import Connection, connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.provider import CompletionResult
from novel_harness.draft.rolling_summary import save_author_summary
from novel_harness.graph import NodeLabel, SecretDetail
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
    ledger.declare_node(
        NodeLabel.SECRET, SECRET, secret=SecretDetail(description="他是被自己人杀的")
    )
    # 章号由引语算出来（ADR 0006）：这句话在第 1 章，所以萧决从第 1 章起就知道。
    ledger.declare_knows(who=KNOWER, secret=SECRET, quote="萧决在灵前听完了那件事。")
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
    reviewer = Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)})

    outcome = _review(written, reviewer)

    assert outcome.slips == (SecretSlip(sentence=number, character=OUTSIDER, secret=SECRET),)
    notice = next(n for n in _notices(written) if n.kind == "text_advisory")
    assert notice.jump is not None
    para = paragraphs(_current_text(written["conn"], written["pid"], HERE))
    assert (
        find_one(para[notice.jump.para_index], notice.jump.quote_text, notice.jump.occurrence_k)
        == f"他说：「{SLIP_LINE}」"
    )


# ══════════════════════════════════════════════════════════════════════════
# 二、M1-b：秘密有没有对不该知道的人说破
# ══════════════════════════════════════════════════════════════════════════


def test_only_what_he_does_not_know_is_put_in_front_of_the_model(written: dict) -> None:
    """摆给核对器的是「**还不知道**」那几格，知道的一格都不进去。

    对照组同在这一条里：萧决从第 1 章起就知道这件事（章号由引语算的），
    所以清单里只该有顾清音那一行——两行都出现的话，模型会去找一句根本不存在的问题。
    """
    reviewer = Reviewer()
    _review(written, reviewer)

    prompt = reviewer.prompt("secret")
    assert f"- {OUTSIDER}：还不知道「{SECRET}」" in prompt
    assert f"- {KNOWER}：还不知道「{SECRET}」" not in prompt
    assert SLIP_LINE in prompt, "正文没进去的话上面那条断言绿得毫无意义"


def test_a_person_or_a_secret_the_model_invented_is_dropped(written: dict) -> None:
    """模型编一个名字出来 → 当场丢掉，**不落通知**。

    这是「结构化出参」唯一的价值兑现处：`character` / `secret` 必须在我们给出去的
    那份清单里逐字找得到。判据是集合成员，不是相似度。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"secret": _found(number, character="李管家", secret=SECRET)})

    outcome = _review(written, reviewer)

    assert outcome.slips == ()
    assert [n.kind for n in _notices(written)] == []


def test_a_sentence_number_that_does_not_exist_is_dropped(written: dict) -> None:
    """指一个不存在的句号 → 丢掉。**绝不退回第 1 句**——退回去就是锚到别处。"""
    reviewer = Reviewer({"secret": _found(9999, character=OUTSIDER, secret=SECRET)})

    outcome = _review(written, reviewer)

    assert outcome.slips == ()
    assert [n.kind for n in _notices(written)] == []


def test_the_notice_says_who_and_what_without_any_engine_words(written: dict) -> None:
    """标题是给小说作者看的：说得出哪一句、哪个人、哪条秘密，**不说机器码**。"""
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)})

    _review(written, reviewer)

    title = next(n for n in _notices(written) if n.kind == "text_advisory").title
    assert f"第 {number} 句" in title
    assert OUTSIDER in title and SECRET in title
    for machine in ("secret", "text_advisory", "KNOWS", "UNKNOWN", "node"):
        assert machine not in title


# ══════════════════════════════════════════════════════════════════════════
# 三、阶段 2：跟后面已经写完的章抵不抵触
# ══════════════════════════════════════════════════════════════════════════


def test_a_clash_with_a_later_chapter_comes_back_as_three_numbers(written: dict) -> None:
    """出参就是「第几句 ↔ 第几章 + 冲突类型」，三样，没有第四样。"""
    number = _sentence_number(written, "白光")
    reviewer = Reviewer({"track": _found(number, chapter=5, conflict="setting")})

    outcome = _review(written, reviewer)

    assert outcome.clashes == (TrackClash(sentence=number, chapter=5, conflict="setting"),)
    title = next(n for n in _notices(written) if n.kind == "text_advisory").title
    assert f"第 {number} 句 ↔ 第 5 章" in title
    assert "设定对不上" in title
    assert "setting" not in title, "枚举值是机器码，不上作者的屏"


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
    for leaked in (LATER_SETTING, SPOILER, "玄血蛊"):
        assert leaked not in verdict, f"轨道原文进了评语：{leaked}"
        assert leaked not in notice.title, f"轨道原文进了通知：{leaked}"
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
            "secret": _found(number, character=OUTSIDER, secret=SECRET),
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
    first = (reviewer.calls("secret"), reviewer.calls("track"))
    outcome = _review(written, reviewer)

    assert first == (1, 1)
    assert (reviewer.calls("secret"), reviewer.calls("track")) == first
    assert "没有再花钱" in outcome.notes["secret"]
    assert "没有再花钱" in outcome.notes["track"]


def test_a_model_that_is_not_configured_leaves_the_author_alone(written: dict) -> None:
    """模型不可用时：**不抛、不落通知、但说得出为什么**（§10 约束 8）。

    「这一章没问题」和「压根没问」在右栏上长成同一个「什么都没有」，
    而这两件事的下一步动作完全相反。
    """
    exploding = Exploding()

    outcome = _review(written, exploding)

    assert outcome.slips == () and outcome.clashes == () and outcome.notices == ()
    assert [n.kind for n in _notices(written)] == []
    assert "模型没配好" in outcome.notes["secret"]
    assert "模型没配好" in outcome.notes["track"]


def test_garbage_instead_of_json_is_not_an_exception(written: dict) -> None:
    """核对器回了一段散文 → 这一次核对不算数，**不是一个 500**。"""
    reviewer = Reviewer({"secret": "我觉得这一章写得挺好的。"})

    outcome = _review(written, reviewer)

    assert outcome.slips == ()
    assert "没能核对" in outcome.notes["secret"]


def test_a_second_scan_of_the_same_text_does_not_wipe_its_own_warning(
    written: dict,
) -> None:
    """同一份正文再扫一遍，**刚报的那条还在**。

    「作者改掉了那一句 ⇒ 收走旧告警」这条清理规则最容易写坏的方向就是它：按「这一章」
    收，一次幂等重扫就把自己刚报的问题抹掉了，而右栏上什么都不会说。所以判据是
    **来源正文的 hash**，不是章。
    """
    number = _sentence_number(written, SLIP_LINE)
    reviewer = Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)})
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
    _review(written, Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)}))
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
    reviewer = Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)})
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
    reviewer = Reviewer({"secret": _found(number, character=OUTSIDER, secret=SECRET)})
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

    assert reviewer.calls("secret") == 1, "切走之后该核对一次"
    assert reviewer.calls("track") == 1


def test_a_blocked_chapter_is_not_reviewed(
    client, written: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸门说停 → **一次核对都不发**。

    一章已经判定阻断的正文不值得再为它花一次模型调用（它的总结和抽取也没跑）。
    这里把协调器的结论换成 `blocked` 而不是去造一条真的 R2 命中：**被验的是接线
    那一支**——「闸门放行了才核对」这句话在 `_run_one` 里只有一行，而它错了不会
    有任何东西报错，只会多花钱。
    """
    pid = written["pid"]
    reviewer = Reviewer()
    runtime = _runtime(written, reviewer)
    monkeypatch.setattr(
        runtime._coordination, "run", lambda *a, **k: {"validation": "blocked"}
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
