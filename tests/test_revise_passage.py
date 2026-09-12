"""改一段（ADR 0049）：助手说改哪儿、怎么改，写手只写那一段，后端拼回整章。

两层各一节：`draft/passage.py` 是纯函数（找那一段、拼回去），`ChapterDesk.revise` 是
接线（拒什么、喊什么、写手看见什么、桌上多了什么、磁盘没动）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from novel_harness import importer
from novel_harness.agent.candidates import DraftCandidateStore
from novel_harness.agent.loop import TurnEvent, TurnEventKind
from novel_harness.agent.tools import dispatch_all
from novel_harness.db import Connection
from novel_harness.draft.passage import (
    PassageAmbiguous,
    PassageEdit,
    PassageNotFound,
    PassageOutOfOrder,
    PassagesOverlap,
    locate_all,
    locate_span,
    splice,
)
from novel_harness.draft.provider import ToolCall
from test_api import book
from test_candidates import (
    Writer,
    _context,
    _desk,
    _root,
    _writer,
    configured,
    desk_conn,
    poisoned,
)

# 同一本毒书、同一个假写手；fixture 靠 import 进来（同 `conftest.py` 的写法）。
__all__ = ["Writer", "book", "configured", "desk_conn", "poisoned"]

# ══════════════════════════════════════════════════════════════════════════
# 一、纯函数：找那一段、拼回去
# ══════════════════════════════════════════════════════════════════════════

TEXT = "雨歇了。\n\n贾环站在废墟之中，久久没有动弹。他抬起头。\n\n“走吧。”他转身。\n"


def test_a_quote_is_found_only_when_it_occurs_exactly_once() -> None:
    span = locate_span(TEXT, "久久没有动弹。")
    assert (span.text, TEXT[span.start : span.end]) == ("久久没有动弹。", "久久没有动弹。")
    with pytest.raises(PassageNotFound):
        locate_span(TEXT, "从未出现过的一句")
    with pytest.raises(PassageAmbiguous) as caught:
        locate_span(TEXT + "他抬起头。\n", "他抬起头。")
    assert caught.value.count == 2
    # 引语两头的空白不算：助手从工具返回里抄的时候常带一个换行。
    assert locate_span(TEXT, "  雨歇了。\n").text == "雨歇了。"


def test_until_extends_the_span_to_the_end_of_that_sentence_and_must_come_after() -> None:
    span = locate_span(TEXT, "贾环站在废墟之中", until="他抬起头。")
    assert span.text == "贾环站在废墟之中，久久没有动弹。他抬起头。"
    with pytest.raises(PassageOutOfOrder):
        locate_span(TEXT, "他抬起头。", until="雨歇了。")


def test_edits_in_one_call_must_not_overlap() -> None:
    a = PassageEdit(quote="贾环站在废墟之中", until="他抬起头。", brief="x")
    b = PassageEdit(quote="久久没有动弹。", brief="y")
    with pytest.raises(PassagesOverlap):
        locate_all(TEXT, [a, b])
    c = PassageEdit(quote="“走吧。”他转身。", brief="z")
    assert [s.text for s in locate_all(TEXT, [c, a])] == ["“走吧。”他转身。", a.quote + "，久久没有动弹。他抬起头。"]


def test_splice_replaces_inserts_and_deletes_and_leaves_everything_else_byte_for_byte() -> None:
    edits = [
        PassageEdit(quote="久久没有动弹。", brief="x"),
        PassageEdit(quote="“走吧。”他转身。", kind="insert_after", brief="y"),
        PassageEdit(quote="雨歇了。", kind="delete"),
    ]
    spans = locate_all(TEXT, edits)
    out = splice(TEXT, edits, spans, ["许久没有动弹。\n", "天边一线鱼肚白。", ""])
    assert out == "贾环站在废墟之中，许久没有动弹。他抬起头。\n\n“走吧。”他转身。\n\n天边一线鱼肚白。\n"
    # 段落之间本来只隔一个换行的章，新段也只隔一个换行。
    tight = "一段。\n二段。\n三段。\n"
    e = [PassageEdit(quote="一段。", kind="insert_after", brief="x"), PassageEdit(quote="二段。", kind="delete")]
    assert splice(tight, e, locate_all(tight, e), ["新的。", ""]) == "一段。\n新的。\n三段。\n"
    # 句中删一句：两头直接接上，不动换行。
    mid = [PassageEdit(quote="他抬起头。", kind="delete")]
    assert splice(TEXT, mid, locate_all(TEXT, mid), [""]) == TEXT.replace("他抬起头。", "")


# ══════════════════════════════════════════════════════════════════════════
# 二、接线：`revise_passage` 这条工具
# ══════════════════════════════════════════════════════════════════════════

OLD_LINE = "李管家什么也没说。"
NEW_LINE = "李管家沉默了很久，什么也没说。"


def _revise(chapter: int, edits: list[dict[str, Any]], call_id: str = "r0") -> ToolCall:
    return ToolCall(
        id=call_id,
        name="revise_passage",
        arguments=json.dumps({"chapter": chapter, "edits": edits}, ensure_ascii=False),
    )


def _events_of(desk_conn: Connection, poisoned: dict[str, str], writer_text: str, monkeypatch: pytest.MonkeyPatch):
    """一个把事件都攒起来的桌子。"""
    heard: list[TurnEvent] = []
    writer = _writer(monkeypatch, writer_text)
    desk = _desk(desk_conn, poisoned, on_event=heard.append)
    return desk, writer, heard


def test_one_edit_writes_only_that_passage_and_lands_on_the_desk_as_one_draft(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    desk, writer, heard = _events_of(desk_conn, poisoned, f"〖自述〗改软了。\n\n{NEW_LINE}", monkeypatch)
    root = _root(desk_conn, poisoned)
    before = importer.read_chapter(root, 1) or ""
    assert OLD_LINE in before  # 探针

    outcome = dispatch_all(
        [_revise(1, [{"quote": OLD_LINE, "brief": "改软一点，让他的沉默有分量。"}])],
        _context(desk_conn, poisoned, desk),
    )[0]
    assert outcome.ok, outcome.content
    report = json.loads(outcome.content)

    # 写手：看整章，被要求只写那一段；它的那一次是唯一一次调用。
    assert len(writer.prompts) == 1
    blob = "\n".join(m["content"] for m in writer.prompts[0])
    assert "【目标章当前正文】" in blob and "【要改的一段】" in blob and OLD_LINE in blob
    assert "只写出替换那一段的新文字" in blob
    assert "整章重写" not in blob
    assert blob.index("【要改的一段】") < blob.index("改软一点")  # 任务在最后

    # 桌上多了一稿：整章、只有那一行变了；要求记的是改哪儿 + 怎么改；自述是写手那句。
    candidate = DraftCandidateStore(desk_conn).recent(poisoned["pid"])[0]
    assert candidate.id == report["draft_id"]
    body = desk.recall(candidate.id).body
    # 候选存的是正文（不含章标那一行）：整章只有那一行变了。
    assert before.startswith("第一章 血脉\n\n")
    assert body == before.removeprefix("第一章 血脉\n\n").replace(OLD_LINE, NEW_LINE)
    assert candidate.note == "改软了。"
    assert candidate.brief == f"「{OLD_LINE}」改写：改软一点，让他的沉默有分量。"
    assert report["note"] == "改软了。"
    # 磁盘没动。
    assert (importer.read_chapter(root, 1) or "") == before

    # 事件：一条流，「正在修改」开口（`revising`），整章一片送到，「第几稿完成」收尾。
    kinds = [e.kind for e in heard]
    assert kinds == [TurnEventKind.DRAFT_STARTED, TurnEventKind.DRAFT_DELTA, TurnEventKind.DRAFT_KEPT]
    started, delta, kept = heard
    assert started.said_to_author == "正在修改第 1 章。" and started.revising is True
    assert delta.text == body and delta.stream == started.stream == kept.stream
    assert kept.draft_id == candidate.id and kept.ordinal == candidate.ordinal


def test_several_edits_in_one_call_are_one_draft_and_a_delete_costs_nothing(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    desk, writer, _ = _events_of(desk_conn, poisoned, "〖自述〗加了一段。\n\n他把那句话咽了回去。", monkeypatch)
    before = importer.read_chapter(_root(desk_conn, poisoned), 1) or ""
    outcome = dispatch_all(
        [
            _revise(
                1,
                [
                    {"quote": "萧决在青云城主府第一次听说了血脉秘密的真相。", "kind": "insert_after", "brief": "加一句他的反应"},
                    {"quote": OLD_LINE, "kind": "delete"},
                ],
            )
        ],
        _context(desk_conn, poisoned, desk),
    )[0]
    assert outcome.ok, outcome.content
    assert len(writer.prompts) == 1, "删那一处不用写手"
    body = desk.recall(json.loads(outcome.content)["draft_id"]).body
    assert OLD_LINE not in body
    assert body == "萧决在青云城主府第一次听说了血脉秘密的真相。\n他把那句话咽了回去。\n"
    assert before.startswith("第一章 血脉\n\n萧决")  # 探针：章标切掉了，正文从这儿起
    assert len(DraftCandidateStore(desk_conn).recent(poisoned["pid"])) == 1


@pytest.mark.parametrize(
    ("chapter", "edits", "said"),
    [
        (7, [{"quote": "x", "brief": "y"}], "还没有正文"),
        (1, [{"quote": "这一句不在正文里", "brief": "y"}], "找不到"),
        (2, [{"quote": "他终于明白了。", "brief": "y"}], "出现了 2 次"),
        (1, [{"quote": OLD_LINE, "until": "萧决在青云城主府", "brief": "y"}], "前面"),
        (
            1,
            [
                {"quote": "萧决在青云城主府", "until": OLD_LINE, "brief": "y"},
                {"quote": "李管家", "brief": "z"},
            ],
            "互相压着",
        ),
        (1, [{"quote": OLD_LINE}], "没说怎么改"),
    ],
)
def test_bad_edits_are_refused_before_any_money_is_spent(
    desk_conn: Connection,
    poisoned: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    chapter: int,
    edits: list[dict[str, Any]],
    said: str,
) -> None:
    desk, writer, heard = _events_of(desk_conn, poisoned, NEW_LINE, monkeypatch)
    outcome = dispatch_all([_revise(chapter, edits)], _context(desk_conn, poisoned, desk))[0]
    assert not outcome.ok
    assert said in outcome.content, outcome.content
    assert writer.prompts == [], "拒了就不该花钱"
    assert heard == [], "拒在开口之前：没有一条会转圈的流"
    assert DraftCandidateStore(desk_conn).recent(poisoned["pid"]) == []


def test_an_empty_answer_from_the_writer_is_refused_with_the_bill_attached(
    desk_conn: Connection, poisoned: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    desk, writer, heard = _events_of(desk_conn, poisoned, "〖自述〗说了句话\n", monkeypatch)
    outcome = dispatch_all(
        [_revise(1, [{"quote": OLD_LINE, "brief": "改软一点"}])], _context(desk_conn, poisoned, desk)
    )[0]
    assert not outcome.ok and "交回来的是空的" in outcome.content
    assert len(outcome.calls) == 1, "钱花了就得在账上"
    assert [e.kind for e in heard] == [TurnEventKind.DRAFT_STARTED, TurnEventKind.DRAFT_FAILED]
    assert DraftCandidateStore(desk_conn).recent(poisoned["pid"]) == []
