"""稿子回流那一层的**措辞** —— 「我在别的软件里改了稿」和「刚导进来一本书」。

这一层不干活（干活的是 `importer.sync` / `importer.import_book`），它只干一件事：
**把回执翻成作者读得懂的一两句话**。同 `api/extraction.py` 的 `ExtractionRunView`——
措辞归后端，前端一个字都不拼。

── 为什么这两句话必须在这儿，不在前端 ────────────────────────────────────────

`SyncReport` / `ImportReport` 是**给调用方看的结构**（哪几章新增、哪几章多了快照、
几个文件被忽略）。前端要把它读成一句话，就得在浏览器里写一段「零章 = 没稿子、
全没变 = 白跑一趟、有变 = 说哪几章」的判断——而那段判断一旦存在，它就是第二份
关于「同步意味着什么」的知识，且没有任何东西会在它和后端漂开的时候报错。

── `preamble_chars` 是全书唯一一个「整本都错了」的早期信号 ──────────────────

`ImportReport.preamble_chars` 数的是**第一个章标之前那堆字**。它异常地长，往往意味着
第一章的章标没被切章器认出来——那时全书的顺序位置集体少 1，而顺序位置就是
`chapter.number`，也就是每一条 `valid_from` 的最终来源（`importer` 模块头那条血统）。

**症状是沉默的**：切章成功、导入成功、面板画得出来、规则不报错，只是作者在第 24 章
记下的事被系统记成第 23 章。等他发现的时候，图里已经有几百条边指着错的章。
所以这条警告的价值不在「精确」，在**「在他往里记第一条事实之前说出来」**。
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ..importer import ImportReport, SyncReport
from ..onboarding import BootstrapResult
from ..project import Project

PREAMBLE_ALARM_CHARS: Final = 1000
"""章标之前多少个字算「不对劲」。

**这个数落在两类东西中间，两边都有余量：**

- 正常的卷首材料（书名 / 作者 / 简介 / 免责声明 / 第一个卷标题）合起来通常几百字以内；
- 一整章中文网文正文通常 2,000 字以上——第一章的章标没被认出来时，preamble 里
  躺着的就是这么一整章。

所以 1,000 两边都不贴边。**它宁可漏报也不许假报**：假报一次「你整本书的章号可能错了」
会让作者下次直接跳过这段警告，包括真出事的那一次；而漏报的代价由另一半兜着——
低于门槛但非零时照样有一行**中性**的说明（「章标之前有 N 个字不属于任何一章」），
只是不喊「整本书可能错一位」。

**它不是「一定错了」的判据**，因为同一个形状还有一个完全正常的解：书里真有一段
不属于任何一章的楔子 / 序 / 长简介。所以警告的措辞先说**看得见的事实**，再说两种解释。
"""


def _chapter_phrase(numbers: list[int]) -> str:
    """一串章号说成人话。**六个以内逐个念，再多就说区间**——
    300 章的书重新导一次会得到 300 个数字，摆出来等于什么都没说。"""
    if not numbers:
        return ""
    if len(numbers) <= 6:
        return "第 " + "、".join(str(n) for n in numbers) + " 章"
    return f"第 {min(numbers)}–{max(numbers)} 章里的 {len(numbers)} 章"


def _ignored_note(ignored: tuple[str, ...]) -> str:
    """`chapters/` 里那些不是章节文件的东西。**不是错误**：那个目录是作者的工作区。"""
    names = "、".join(ignored[:6])
    more = f" 等 {len(ignored)} 个" if len(ignored) > 6 else ""
    return f"这些文件不是章节，没有动它们：{names}{more}。"


class SyncOutcome(BaseModel):
    """`POST …/sync` 的出参：**作者在别的软件里改过的稿子，读回来了多少**。

    `SyncReport` 的四个字段照旧带着（前端要拿它决定摆几行），但**屏幕上那句话是
    `headline`**——它由这一层写，不由浏览器拼。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    added_chapters: tuple[int, ...] = ()
    """库里原本没有的章。日常回路里它非空 = 作者在外面新写了一章。"""

    updated_chapters: tuple[int, ...] = ()
    """正文变了、多了一条快照的章。**旧快照没删**（审计指针指着它）。"""

    unchanged_count: int = Field(default=0, ge=0)
    chapter_count: int = Field(default=0, ge=0)
    """这本书现在一共有几章在库里。零 = 稿子文件夹是空的，不是「没有变化」。"""

    ignored_files: tuple[str, ...] = ()

    headline: str
    """一句话说完这次干了什么。**空回执也有话说**——零永远带着一句理由（约束 8）。"""

    notes: tuple[str, ...] = ()
    """补充的几行（哪几章变了、哪些文件没动）。一行一句，前端原样摆。"""


def sync_outcome(report: SyncReport) -> SyncOutcome:
    """`SyncReport` → 作者看得懂的那份。**唯一的转换点。**"""
    added = [c.number for c in report.added]
    updated = [c.number for c in report.refreshed]
    total = len(added) + len(updated) + report.unchanged_count
    ignored = tuple(report.ignored_files)

    notes: list[str] = []
    if total == 0:
        # 真的零 vs 静默的零（同 `nh sync` 那条 `_die` 的理由）：这两种都会让作者的
        # 下一步「记录这句」被拒，而他会去查引语——问题却在这里。
        headline = "这本书的稿子文件夹里一个章节都没有，没有读到任何正文。"
        notes.append("这不是「没有变化」，是「没找到稿子」——书的文件夹可能被移动或改名了。")
    elif not added and not updated:
        headline = f"读了一遍：稿子和这边记着的一样，没有变化（共 {total} 章）。"
    else:
        changed: list[str] = []
        if updated:
            changed.append(f"{len(updated)} 章有新内容")
        if added:
            changed.append(f"{len(added)} 章是新写的")
        headline = "读回来了：" + "、".join(changed) + f"（这本书现在共 {total} 章）。"
        if updated:
            notes.append(f"内容变了：{_chapter_phrase(updated)}。之前那一版还在「历史」里。")
        if added:
            notes.append(f"新读到：{_chapter_phrase(added)}。")
    if ignored:
        notes.append(_ignored_note(ignored))
    return SyncOutcome(
        added_chapters=tuple(added),
        updated_chapters=tuple(updated),
        unchanged_count=report.unchanged_count,
        chapter_count=total,
        ignored_files=ignored,
        headline=headline,
        notes=tuple(notes),
    )


class ImportSummary(BaseModel):
    """`POST /projects/bootstrap`（导入档）之后**摆给作者看的那份回执**。

    在这一层出现之前，整份 `import_report` 被前端丢掉了：屏幕上只有「导入成功」，
    而回执里躺着唯一一个**全书性**的信号（`preamble_chars`）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_count: int = Field(ge=0)
    written_count: int = Field(ge=0)
    """这次新建的章节文件数。重跑同一份 TXT 时应当为零。"""

    unchanged_count: int = Field(ge=0)
    """已经在文件夹里、内容一字不差、跳过了的。"""

    landed_count: int = Field(ge=0)
    """真的进了库、从此可以被「记录这句」定位到的章数。"""

    ignored_files: tuple[str, ...] = ()
    preamble_chars: int = Field(ge=0)

    headline: str
    lines: tuple[str, ...] = ()
    warning: str | None = None
    """`preamble_chars` 越过门槛时那一大段。`None` = 没到门槛，**不是「没检查」**。"""


def _preamble_warning(chars: int) -> str:
    return (
        f"⚠️ 第一章的标题之前还有 {chars} 个字，它们不属于任何一章。\n"
        "最常见的原因是**第一章的标题没有被认出来**（比如它写成「楔子」「序章」，"
        "或者标题那一行前面还有别的字）。真是这样的话，整本书的章号会集体差一章："
        "你在第 24 章记下的事，系统会记成第 23 章，而这件事在界面上看不出任何异常。\n"
        "先打开这份 TXT 看一眼开头：如果那段字确实是第一章的正文，"
        "把它的标题改成「第一章 ……」再重新导入一次（导入不会覆盖已经建好的书，"
        "请新建一本）。如果那本来就是简介或者楔子，它不属于任何一章是对的，可以不管。"
    )


def import_summary(report: ImportReport, *, name: str) -> ImportSummary:
    """`ImportReport` → 作者看得懂的那份。**唯一的转换点。**"""
    synced = report.synced
    landed = len(synced.added) + len(synced.refreshed) + synced.unchanged_count
    ignored = tuple(synced.ignored_files)

    lines = [f"切出了 {report.chapter_count} 章，其中 {landed} 章已经可以被引用。"]
    if report.written:
        lines.append(f"新建了 {len(report.written)} 个章节文件。")
    if report.unchanged:
        # 「已经在那儿、内容一样」不是「导入失败」，但作者会以为是——所以说出来。
        lines.append(f"有 {len(report.unchanged)} 章的文件本来就在，内容一样，没有动它们。")
    if ignored:
        lines.append(_ignored_note(ignored))
    alarm = report.preamble_chars >= PREAMBLE_ALARM_CHARS
    if report.preamble_chars and not alarm:
        # **低于门槛也说一句**，只是不喊「整本书可能错一位」：章标之前的字不属于任何
        # 一章，作者在那段里选中的句子永远定位不到，而那时他会去怀疑自己的操作。
        # 越过门槛时**不说这一句**——那件事下面 `warning` 里说得更全，
        # 同一件事说两遍会教作者跳过整段，包括他真正需要读的那半段。
        lines.append(
            f"第一章的标题之前有 {report.preamble_chars} 个字，它们不属于任何一章"
            "（书名、简介、卷标题一般长在那儿），在那段字里选句子记录不了。"
        )

    warning = _preamble_warning(report.preamble_chars) if alarm else None
    return ImportSummary(
        chapter_count=report.chapter_count,
        written_count=len(report.written),
        unchanged_count=len(report.unchanged),
        landed_count=landed,
        ignored_files=ignored,
        preamble_chars=report.preamble_chars,
        headline=f"《{name}》切成了 {report.chapter_count} 章，已经建好了。",
        lines=tuple(lines),
        warning=warning,
    )


class BootstrapView(BaseModel):
    """`POST /api/projects/bootstrap` 的出参 = 原来那份 + **一份读得懂的回执**。

    `import_report` 原样留着（它是结构化的真相，前端要摆几行数字得靠它），
    **屏幕上的每一句话来自 `summary`**。空白建书档 `summary` 是 `None`：
    那时没有任何东西被切、被写、被忽略，摆一份全零的回执只是噪声。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project: Project
    initial_chapter: int = Field(ge=1)
    import_report: ImportReport | None = None
    summary: ImportSummary | None = None


def bootstrap_view(result: BootstrapResult) -> BootstrapView:
    """`BootstrapResult` → 出参。**唯一的转换点。**"""
    report = result.import_report
    return BootstrapView(
        project=result.project,
        initial_chapter=result.initial_chapter,
        import_report=report,
        summary=None if report is None else import_summary(report, name=result.project.name),
    )
