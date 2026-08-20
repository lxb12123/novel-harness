"""稿子回流那一层的措辞：**同步回执**和**导入回执**。

两件事在这儿被钉住：

1. `POST …/sync` 不再只吐一份结构（`SyncReport`），它带着一句给作者的话。
   那条路由从 M1.5 起就在后端，**浏览器里零调用方**——而它是「正文看得见」和
   「这句话记得下」之间那半条回路（正文直接扫磁盘，`locate` 搜库里的快照）。

2. `preamble_chars` 越过门槛时**明说全书章号可能错一位**。这是全书唯一一个
   「整本都错了」的早期信号，而它此前被前端整份丢在地上：`Setup.tsx` 只读了
   `project.id` 和 `initial_chapter`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import seed

from novel_harness.api.manuscript import (
    PREAMBLE_ALARM_CHARS,
    import_summary,
    sync_outcome,
)
from novel_harness.importer import ImportReport, SyncReport

# 判据借浏览器那侧的形状网（同一份判据，两个运行时）。
from test_wording_guard import dev_shapes


def _report(preamble: int, *, chapters: int = 300) -> ImportReport:
    return ImportReport(
        chapter_count=chapters,
        preamble_chars=preamble,
        written=[f"chapters/{i:04d}.md" for i in range(1, chapters + 1)],
        unchanged=[],
        synced=SyncReport(unchanged_count=chapters),
    )


# ══════════════════════════════════════════════════════════════════════════
# 导入回执：那个全书性的信号
# ══════════════════════════════════════════════════════════════════════════


def test_a_long_preamble_says_the_whole_book_may_be_off_by_one() -> None:
    """**这句话必须出现**：不是「有点长」，是「你在第 24 章记的事会被记成第 23 章」。

    症状是沉默的——切章成功、面板画得出来、规则不报错。等他发现的时候，图里已经有
    几百条边指着错的章，而 `valid_from` 是**不可重建**的（它由证据定，证据锚在快照上）。
    """
    warning = import_summary(_report(3200), name="青云记").warning
    assert warning is not None
    assert "整本书的章号会集体差一章" in warning
    # 三件事都要说到：看得见的事实 / 它意味着什么 / 下一步。
    assert "3200 个字" in warning
    assert "第一章的标题没有被认出来" in warning
    assert "再重新导入一次" in warning


def test_a_normal_front_matter_does_not_cry_wolf() -> None:
    """书名 + 作者 + 简介 = 几百字，**那是正常的**。

    假报一次「你整本书的章号可能错了」会让作者下次直接跳过这段，包括真出事的那一次。
    """
    assert import_summary(_report(180), name="青云记").warning is None
    # 但也不静默：低于门槛照样有一行中性的说明（约束 8 —— 零带着一句理由）。
    lines = import_summary(_report(180), name="青云记").lines
    assert any("不属于任何一章" in line for line in lines)


def test_the_neutral_line_is_not_repeated_inside_the_warning() -> None:
    """越过门槛之后**同一件事只说一遍**——说两遍会教作者跳过整段。"""
    summary = import_summary(_report(3200), name="青云记")
    assert not [line for line in summary.lines if "不属于任何一章" in line]


@pytest.mark.parametrize("chars", [PREAMBLE_ALARM_CHARS - 1, PREAMBLE_ALARM_CHARS])
def test_the_threshold_is_a_single_constant(chars: int) -> None:
    """门槛只有一个数，且它就是 `PREAMBLE_ALARM_CHARS`（改它，行为跟着改）。"""
    fired = import_summary(_report(chars), name="青云记").warning is not None
    assert fired == (chars >= PREAMBLE_ALARM_CHARS)


def test_zero_preamble_says_nothing_about_it() -> None:
    summary = import_summary(_report(0), name="青云记")
    assert summary.warning is None
    assert not [line for line in summary.lines if "章的标题之前" in line]


def test_the_receipt_counts_what_the_author_asked_about() -> None:
    """切了多少章 / 写了哪些 / 哪些没动 / 哪些文件被忽略 —— 四样都在。"""
    report = ImportReport(
        chapter_count=3,
        preamble_chars=0,
        written=["chapters/0001.md"],
        unchanged=["chapters/0002.md", "chapters/0003.md"],
        synced=SyncReport(unchanged_count=3, ignored_files=["chapters/大纲.md"]),
    )
    summary = import_summary(report, name="青云记")
    assert summary.chapter_count == 3
    assert summary.written_count == 1
    assert summary.unchanged_count == 2
    assert summary.landed_count == 3
    assert summary.ignored_files == ("chapters/大纲.md",)
    joined = "\n".join(summary.lines)
    assert "3 章" in joined and "1 个章节文件" in joined and "2 章" in joined
    assert "大纲.md" in joined


# ══════════════════════════════════════════════════════════════════════════
# 同步回执：零也带着一句理由
# ══════════════════════════════════════════════════════════════════════════


def test_no_manuscript_at_all_is_not_the_same_sentence_as_no_change() -> None:
    """**「一个章节都没有」和「读了一遍没有变化」的下一步动作正好相反。**

    合成一句「同步完成」的话，作者的下一步 declare 会被拒，而他会去查引语——
    问题却在这里（同 sync 那条空态的理由）。
    """
    empty = sync_outcome(SyncReport())
    quiet = sync_outcome(SyncReport(unchanged_count=12))
    assert empty.headline != quiet.headline
    assert "没找到稿子" in "\n".join(empty.notes)
    assert "没有变化" in quiet.headline and "12" in quiet.headline


def test_a_real_change_names_the_chapters(client: TestClient, book: dict[str, str]) -> None:
    """作者在别的软件里改了一章、新写了一章：回执要说得出**是哪几章**。

    走真 HTTP + 真磁盘：`sync` 是全书 `valid_from` 血统上的一环，
    在内存里凑一份 `SyncReport` 验不到「这条路由真的读得到磁盘」。
    """
    base = f"/api/projects/{book['pid']}"
    root = Path(client.get(base).json()["root_path"])
    second = root / "chapters" / "0002.md"
    second.write_text(second.read_text(encoding="utf-8-sig") + "\n他在灯下改了这一段。\n", "utf-8")
    (root / "chapters" / "大纲.md").write_text("三卷的走向。\n", encoding="utf-8")

    body: dict[str, Any] = client.post(f"{base}/sync").json()
    # 第 3 章那个文件只在磁盘上（`book` 夹具建的），所以这一下把它落进库 = 新读到。
    assert body["updated_chapters"] == [2]
    assert body["added_chapters"] == [3]
    assert "第 2 章" in "\n".join(body["notes"])
    assert "chapters/大纲.md" in body["ignored_files"]
    assert "大纲.md" in "\n".join(body["notes"])

    # 再点一次：什么都没变，而那是**另一句话**。
    again = client.post(f"{base}/sync").json()
    assert again["added_chapters"] == [] and again["updated_chapters"] == []
    assert "没有变化" in again["headline"]


def test_sync_lets_the_author_declare_what_he_just_wrote(
    client: TestClient, book: dict[str, str]
) -> None:
    """**这是这颗按钮存在的全部理由。**

    在外面往第 2 章里加一句话 → 立刻拿它去 locate：拒（库里那一版还没有它）→
    点一下 sync → 同一句话定位得到。中间作者一个字都没改。
    """
    base = f"/api/projects/{book['pid']}"
    root = Path(client.get(base).json()["root_path"])
    second = root / "chapters" / "0002.md"
    line = "他在灯下把那封信烧了。"
    second.write_text(second.read_text(encoding="utf-8-sig") + f"\n{line}\n", encoding="utf-8")

    # `POST …/locate` 2026-08-14 删了（手工声明那条路整条退场），但**这颗按钮的理由
    # 一个字没变**：库里那份快照旧了，后台整理就照着旧正文跑。这里改用库级 `locate`
    # 直接问那份快照——问的还是同一件事：「系统手上那一版有没有这句话」。
    assert seed.locate(book["db"], book["pid"], line) == []
    assert client.post(f"{base}/sync").status_code == 200
    hits = seed.locate(book["db"], book["pid"], line)
    assert [h.chapter_number for h in hits] == [2]


def test_sync_costs_nothing(client: TestClient, book: dict[str, str]) -> None:
    """**这颗按钮不许替作者按下一次他没按过的付费调用。**

    判据是 `model_call` 一行都没多——起草 / 抽取 / 总结全都会往那张表里写。
    """
    base = f"/api/projects/{book['pid']}"
    before = client.get(f"{base}/runs").json()["totals"]
    assert client.post(f"{base}/sync").status_code == 200
    assert client.get(f"{base}/runs").json()["totals"] == before


def test_every_sentence_on_this_path_is_the_authors_language() -> None:
    """两份回执里的每一句话都过一遍那张形状网（含第五张：命令行）。"""
    texts = [
        *import_summary(_report(3200), name="青云记").lines,
        import_summary(_report(3200), name="青云记").headline,
        import_summary(_report(3200), name="青云记").warning or "",
        *sync_outcome(SyncReport()).notes,
        sync_outcome(SyncReport()).headline,
        sync_outcome(SyncReport(unchanged_count=3)).headline,
    ]
    dirty = {text: dev_shapes(text) for text in texts if dev_shapes(text)}
    assert not dirty, f"回执里有研发术语：{dirty}"
