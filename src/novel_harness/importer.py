r"""导入器 —— TXT → 磁盘上的 `chapters/NNNN.md` → 库（ADR 0007 / PLAN §5.9）。

**`chapter.path` / `chapter.number` / `text_sha256` 的唯一产地**，也就是全书每一个
`valid_from` 数字的最终来源。血统整条是：

    edge.valid_from_chapter ← evidence.chapter_number ← chapter.number
      ← ChapterSpec.number ← 本模块 ← chapterize 的 index ← 文本顺序

作者的输入从没进过这条链——他能做的只有「把书交给 import，然后在自己的编辑器里改
chapters/*.md」。**本模块的公开签名里没有一个位置能让他填章号**（约束 10 / §5.9）。

── 为什么 `import_book` 不收 conn，也不写 decision_log ─────────────────────

**这是刻意的。** 导入不是作者的一次确认，是从他自己的文件做的**确定性推导**：重放
= 重跑 `chapterize`，免费。`decision_log`（§5.7）记的是**不可重建**的东西——作者点过
的每一次「这条事实在这段原文里出现过」。往那张 append-only 的表里灌 300 条「我读了
一个文件」会把唯一不可重建的资产埋进噪声里，而它一条都删不掉（三个触发器封死了
INSERT/UPDATE/DELETE）。

── `sync` 只写 chapter / snapshot，不碰 evidence_status ────────────────────

作者改了第 88 章 → 锚在旧快照上的证据可能已经指不准了。把它们标成 STALE、重定位、
重校验是 **M4**，不是这里。这条线不靠自律：`UPDATE edge SET evidence_status` 只能写在
`graph/` 里（`tests/test_arch_guard.py`），而本模块连 `db` 都不 import。

审计指针指着的旧快照**永不删**，所以「不做 STALE」的代价是「面板可能对着一条锚在
旧正文上的证据放行」——诚实记在这里：M1 的 sync 让新写的正文可被定位，它不声称
旧证据仍然准。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from .decisions import quote_hash
from .graph import ChapterSpec, GraphStore, StoredChapter
from .text import Chapter, Chapterization, chapterize

CHAPTER_DIR: Final = "chapters"
"""相对 `project.root_path`。ADR 0007：这个目录里的 .md **就是稿子**，不是导出物。"""

_CHAPTER_FILE_RE: Final = re.compile(r"^(\d{4,})\.md$")
"""`0088.md` → number=88。**四位起，不封顶**：真书 1000 章之后是 `1000.md`，
而 `{index:04d}` 对它给的就是 `1000`——补零只是为了让 `ls` 的字典序等于章序。

不匹配的文件（`notes.md` / `大纲.md` / 编辑器的临时文件）不是错误，是作者的东西
（那个目录是他的工作区，不是我们的输出目录）。见 `SyncReport.ignored_files`。
"""


def chapter_path(index: int) -> str:
    """第 `index` 章的文件路径（相对 root，posix）。

    **补零的是 `index`——文本顺序——不是 `marker` 里印的那个中文数字。**
    `text/chapterize.py` 已经论证过为什么印出来的章号不是全序键（分卷重启：第二卷
    之后又来一次「第一章」）。拿它当文件名，两章会共用一个文件：`idx_chapter_path`
    的 UNIQUE 让第二章导入时当场炸（好），但在那之前 `explode` 已经把第一章的正文
    覆盖掉了（**灾难**）——而 watchdog 从此也不知道那个文件改了该更新哪一章。
    """
    return f"{CHAPTER_DIR}/{index:04d}.md"


class ChapterFile(BaseModel):
    """磁盘上的一个章节文件。**`chapters/NNNN.md` → (章号, 标题) 的唯一解读处。**

    标题取的是**首个非空行**，不是库里 `chapter.title` 那一列。两者平时同解，不同解的
    那一刻恰恰是要相信磁盘的那一刻：作者在自己的编辑器里改了标题还没 `sync`，库里那份
    就是旧的（ADR 0007：正文的真相源在磁盘上，DB 永远不是）。
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    title: str = ""
    path: str
    """相对 `root` 的 posix 路径，和 `chapter_path(number)` 同形。"""


def chapter_files(root: Path) -> list[ChapterFile]:
    """`{root}/chapters/` 里的章节文件，按章号升序。目录不存在 → `[]`（不是错误）。

    **它是「这本书有哪些章」的唯一读法**，`chapter_path()` 写下去的东西由它读回来。
    在此之前 `api/app.py` 自己抄了一份文件名正则 + 一份「首个非空行就是标题」，
    而那份正则必须与 `{index:04d}.md` 保持同解——两份会漂，且漂掉的那天症状是
    「章列表少了 1000 章之后的那些」，没有任何测试会红。

    只读到首个非空行为止，不整份读进内存：全书 722 章一次列举要开 722 个文件，
    而调用方（目录、索引）要的只有标题。
    """
    directory = root / CHAPTER_DIR
    if not directory.is_dir():
        return []
    out: list[ChapterFile] = []
    for file in sorted(directory.iterdir()):
        if not file.is_file():
            continue
        matched = _CHAPTER_FILE_RE.match(file.name)
        if matched is None:
            # 不匹配的文件是作者的东西，不是错误（同 `SyncReport.ignored_files`）。
            continue
        title = ""
        with file.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if line.strip():
                    title = line.strip()
                    break
        out.append(
            ChapterFile(
                number=int(matched.group(1)),
                title=title,
                path=f"{CHAPTER_DIR}/{file.name}",
            )
        )
    # 按章号排，不按文件名字典序：两者今天同解（补零），1000 章之后不同解（同 `sync`）。
    return sorted(out, key=lambda entry: entry.number)


def chapter_text(heading: str, body: str) -> str:
    """一个章节文件的全部内容。

    **`heading` 原样，前面不许加 `# `。** 已对着 `CHAPTER_RE`（`^[ \t　]*(第…)`）核过：
    行首只容得下空白，`# 第一章 少年萧决` **匹配不上**。加了 `#` 就让 `sync` 和重新
    import 把每个文件都切出零章——导出的 chapters/ 再也读不回来，而它今天看起来很漂亮。
    """
    return f"{heading}\n\n{body}\n"


class ImportRefused(Exception):
    """`import_book` 在**写任何一个字节之前**拒绝了整本书。

    `conflicts` 是那些「已存在且内容不同」的文件（相对 root 的 posix 路径），
    零章的 TXT 则是空列表。
    """

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts: Final = conflicts or []


class SyncRefused(Exception):
    """某个 `chapters/NNNN.md` 不是一个章节文件（切出 0 章或 >1 章）。"""

    def __init__(self, message: str, path: str) -> None:
        super().__init__(message)
        self.path: Final = path


class ChapterMissing(Exception):
    """磁盘上没有这一章。**新建一章不走这条路**（见 `save_chapter`）。"""

    def __init__(self, message: str, chapter: int) -> None:
        super().__init__(message)
        self.chapter: Final = chapter


class ChapterChanged(Exception):
    """磁盘上那一章**已经不是**调用方依据的那一份了（ADR 0021 的那道乐观闸）。

    `expected` / `actual` 是两个 `text_sha256`，给调用方拼话用——**它们不上屏**
    （屏幕上那句话是「你刚改过这一章，所以没有覆盖它」）。
    """

    def __init__(self, message: str, chapter: int, *, expected: str, actual: str) -> None:
        super().__init__(message)
        self.chapter: Final = chapter
        self.expected: Final = expected
        self.actual: Final = actual


class SyncReport(BaseModel):
    """`sync` 干了什么。"""

    model_config = ConfigDict(frozen=True)

    added: list[StoredChapter] = Field(default_factory=list)
    """库里原本没有的章（`number` 是新的）。"""

    refreshed: list[StoredChapter] = Field(default_factory=list)
    """已有的章，正文变了 → 多了一条快照。旧快照还在（审计指针指着它）。"""

    unchanged_count: int = 0
    """正文与库里一字不差的章。日常回路里这是绝大多数，逐条列出来只会淹没上面两行。"""

    ignored_files: list[str] = Field(default_factory=list)
    """`chapters/` 下不叫 `NNNN.md` 的文件。**不是错误**：那是作者的工作区。"""


class ImportReport(BaseModel):
    """`import_book` 干了什么。"""

    model_config = ConfigDict(frozen=True)

    chapter_count: int
    preamble_chars: int
    """第一个章标之前那堆字的字数（strip 之后）。**不落任何一章**——书名 / 简介 /
    免责声明 / **第一个卷标题**都长在那儿。它异常地长，往往意味着第一章的章标
    没被认出来，而那时全书 index 集体少 1、每一条 `valid_from` 都错一章。
    """

    written: list[str]
    """这次新建的文件（相对 root）。重跑时应当为空——不空 = 章数或章序变了。"""

    unchanged: list[str]
    """已存在且内容一字不差的文件，跳过了。"""

    synced: SyncReport


def explode(book: Chapterization, root: Path) -> tuple[list[str], list[str]]:
    """把切好的章写成 `{root}/chapters/NNNN.md`。返回 `(written, unchanged)`。

    **两阶段：先把全部冲突检出来，再动手写**（同 `sqlite_store.upsert_edge` 的
    「先把乱序全部检出来」）。任一目标文件已存在且内容不同 → `ImportRefused`，
    **一个字节都不写**。事务回滚兜不住文件系统，而失败路径不该依赖回滚的正确性。
    **没有 `--force`。**

    这条规则做两件事：

    (a) **覆盖作者的稿子是这个项目最不能犯的错**，比任何一条误报都致命。误报他骂一句
        就过去了，覆盖掉的三千字要他重写。

    (b) 它顺手关掉了「中间插一章」那个洞：作者在 TXT 中间插入新的第 5 章 → 之后每章
        `index` 全体 +1 → 重跑 import 要把 `chapters/0088.md` 写成原来的第 87 章 →
        **内容不同 → 当场拒绝**。没有这条，`UNIQUE(project_id, number)` 不炸（数还是
        1..N+1）、`idx_chapter_path` 不炸（路径原样复用，只是内容换了），而图里每一条
        `valid_from=88` 从此指着另一章——**面板会理直气壮地画出来**。
    """
    targets = [
        (chapter_path(ch.index), chapter_text(ch.raw_heading, ch.body)) for ch in book.chapters
    ]

    conflicts: list[str] = []
    unchanged: list[str] = []
    todo: list[tuple[str, str]] = []
    for rel, text in targets:
        file = root / rel
        if not file.exists():
            todo.append((rel, text))
        elif file.read_text(encoding="utf-8-sig") == text:
            unchanged.append(rel)
        else:
            conflicts.append(rel)

    if conflicts:
        raise ImportRefused(
            f"拒绝导入：{len(conflicts)} 个章节文件已存在且内容不同，一个字节都没写。\n"
            + "".join(f"    {rel}\n" for rel in conflicts)
            + "  这些文件是你的稿子（ADR 0007），import 不覆盖它们，也没有 --force。\n"
            "  最常见的原因是你在 TXT 中间插了一章：之后每一章的顺序位置全体 +1，于是\n"
            "  chapters/0088.md 要被写成原来的第 87 章。放它过去的话，图里每一条\n"
            "  valid_from=88 从此都指着另一章，而面板会理直气壮地画出来。\n"
            "  M1 不支持中途插章。要么直接在 chapters/ 里改（然后 nh sync），\n"
            "  要么导进一个空目录。",
            conflicts=conflicts,
        )

    for rel, text in todo:
        file = root / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return [rel for rel, _ in todo], unchanged


def sync(store: GraphStore, project_id: str, root: Path) -> SyncReport:
    """把 `{root}/chapters/*.md` 的**现状**读进库。**日常回路。**

    ADR 0007 说这些 .md 就是稿子：作者随时在 VSCode 里改它、在里面写新的第 301 章。
    没有 `sync`，`nh import` 就是一次性播种，而「我刚写完的那句话」永远定位不到——
    `declare` 搜的是快照，快照只有这里落得下。它是 M1 回路的一半，不是 M4 的活。

    `number` 来自**文件名**（不是文件内容里印的章号，也不是这次扫到第几个文件）：
    文件名是 `explode` 写下去的顺序位置，而正文里那行章标只是显示用。

    每个文件过的是**那一个** `chapterize()`——不写第二份章标解析。恰好 1 章才落；
    0 章（首行不是章标）或 >1 章（一个文件里塞了两章）→ `SyncRefused`。
    """
    directory = root / CHAPTER_DIR
    if not directory.is_dir():
        return SyncReport()

    numbered: list[tuple[int, Path]] = []
    ignored: list[str] = []
    for file in sorted(directory.iterdir()):
        if not file.is_file():
            continue
        m = _CHAPTER_FILE_RE.match(file.name)
        if m is None:
            ignored.append(f"{CHAPTER_DIR}/{file.name}")
        else:
            numbered.append((int(m.group(1)), file))

    added: list[StoredChapter] = []
    refreshed: list[StoredChapter] = []
    unchanged_count = 0
    # 按 number 升序，不按文件名字典序：两者今天同解（补零），1000 章之后不同解，
    # 而落章顺序决定了 UNIQUE(project_id, number) 撞车时先炸的是哪一章。
    for number, file in sorted(numbered, key=lambda pair: pair[0]):
        rel = f"{CHAPTER_DIR}/{file.name}"
        text = file.read_text(encoding="utf-8-sig")
        book = chapterize(text)
        if len(book.chapters) != 1:
            raise SyncRefused(
                f"{rel} 切出了 {len(book.chapters)} 章，一个章节文件必须**恰好**是一章。\n"
                "  0 章 = 首行不是章标（认的是行首的「第N章/节/回」，前面容不下 `# `）；\n"
                "  >1 章 = 一个文件里塞了两章，而 number 来自文件名，第二章会被静默吞掉。",
                path=rel,
            )
        chapter = book.chapters[0]
        stored = store.put_chapter(
            ChapterSpec(
                project_id=project_id,
                number=number,
                heading=chapter.raw_heading,
                title=chapter.title,
                path=rel,
                # 写进库的是**文件全文**，不是 chapter.body：写进盘的字节、快照存的字节、
                # anchor.paragraphs 分段的字节必须是同一份。错开一个字节（比如这里省掉
                # 标题行）→ 每一条证据的 para_index 全体偏移 → 锚全坏。
                text=text,
            )
        )
        if stored.created:
            added.append(stored)
        elif stored.snapshot_created:
            refreshed.append(stored)
        else:
            unchanged_count += 1
    return SyncReport(
        added=added,
        refreshed=refreshed,
        unchanged_count=unchanged_count,
        ignored_files=ignored,
    )


def read_chapter(root: Path, chapter: int) -> str | None:
    """磁盘上第 `chapter` 章的正文。`None` = 那个文件不存在。

    **`utf-8-sig`**：和 `sync`、`explode`、`api/app.py` 的读法同解。差一个 BOM，
    `text_digest()` 就会给出两个不同的哈希，而那道乐观闸会在作者什么都没做的时候拦下来。
    """
    file = root / chapter_path(chapter)
    if not file.exists():
        return None
    return file.read_text(encoding="utf-8-sig")


def single_chapter(text: str) -> Chapter | None:
    """`text` 恰好是**一个章节文件**时返回切出来的那一章，否则 `None`。

    **切章只有 `chapterize()` 这一份实现**（同 `sync` 的那句话）——调用方不许自己写
    第二份「首行是不是章标」的判断，两份漂掉的那天，一边说这份稿子能存、另一边把
    整本书的章号切歪。

    **比 `sync` 严一格**：它还要求章标之前一个字都没有。`sync` 落库的是**文件全文**，
    preamble 丢不掉；而这个函数的调用方要拿 `body` 去**重拼**一份文件
    （`chapter_text(raw_heading, body)`），preamble 会在那次重拼里被静默丢掉——
    那是作者写在章标前面的字，不该由一次自动落盘吃掉。
    """
    book = chapterize(text)
    if len(book.chapters) != 1 or book.preamble.strip():
        return None
    return book.chapters[0]


def text_digest(text: str) -> str:
    """一段章节正文的 `text_sha256`。**和 `chapter_snapshot` 那一列同解。**

    直接借 `decisions.quote_hash`（sha256 / UTF-8 原始字节 / 不做任何归一化），
    理由写在它自己的 docstring 里：**别在别处再实现一遍**——两份实现里只要有一份
    哪天加了 `.strip()`，快照去重和这道乐观闸就在那一刻各说各话。
    """
    return quote_hash(text)


def save_chapter(
    store: GraphStore,
    project_id: str,
    root: Path,
    chapter: int,
    markdown: str,
    *,
    expected_sha256: str | None = None,
) -> SyncReport:
    """把一章正文写进磁盘，再 `sync` 落快照。**磁盘先、DB 跟**（ADR 0007）。

    保存这条路只有这一个实现：作者按 Ctrl-S 走它（`PUT …/chapters/{n}/text`），
    agent 起草完落盘也走它（ADR 0021）。**一个功能不留两个入口**，也不给
    「哪份正文是真的」第二个答案。

    Args:
        expected_sha256: 调用方**依据的那一份**正文的哈希。给了就先比对磁盘当前值，
            不一致 → `ChapterChanged`，**一个字节都不写**。

            `None` = 不比对，也就是作者自己按保存那一档：他改的就是他眼前那份，
            没有第三方能在中间插一脚。**agent 那条路必须给**——它从起草到落盘之间隔着
            一次几十秒的模型调用，而作者就在旁边打字。

    Raises:
        ChapterMissing: 那一章的文件不存在。**这条 404 不许为 agent 放开**（ADR 0021）：
            新建一章要起章标题，而标题是切章的锚（`chapterize` 认「首个非空行」），
            起错了整本书的章号会漂；`chapter_snapshot.chapter_id` 也没有落点。
        ChapterChanged: 磁盘上那份已经比 `expected_sha256` 新。
        SyncRefused: 写进去的东西切不出恰好一章（正文**已经**落盘，作者要修章标题再存一次）。
    """
    file = root / chapter_path(chapter)
    if not file.exists():
        raise ChapterMissing(f"第 {chapter} 章在磁盘上不存在", chapter)
    if expected_sha256 is not None:
        # **读了立刻比、比完立刻写**：中间不夹任何一次模型调用或事务。窗口关不死
        # （没有文件锁，作者的编辑器随时可能在这两行之间落盘），但它从「几十秒」
        # 缩到「几毫秒」，而剩下那点窗口和作者的两个编辑器互相覆盖是同一种东西。
        actual = text_digest(file.read_text(encoding="utf-8-sig"))
        if actual != expected_sha256:
            raise ChapterChanged(
                f"第 {chapter} 章在磁盘上已经变了",
                chapter,
                expected=expected_sha256,
                actual=actual,
            )
    file.write_text(markdown, encoding="utf-8")
    return sync(store, project_id, root)


def import_book(store: GraphStore, project_id: str, *, txt: Path, root: Path) -> ImportReport:
    """**一次性播种**：切章 → 写盘 → `sync`。日常回路请用 `sync`。

    不收 `conn`、不写 `decision_log`——导入是确定性推导，不是作者的确认（见模块 docstring）。

    Raises:
        ImportRefused: 零章的 TXT，或有目标文件已存在且内容不同（见 `explode`）。
        SyncRefused: 写出去的文件读回来不是一章（`chapter_text` 坏了才可能）。
    """
    # utf-8-sig 而不是 utf-8：`utf-8` 解 utf-8-sig 的文件**会成功**并把 BOM 原样留在串里，
    # 而 `^[ \t　]*` 后面接的是 `第`——BOM 既不是行首空白也不是「第」。
    # （chapterize.normalize 会兜住它；这里多一道是因为「把磁盘上的字节变成干净的 str」
    #  本来就是读文件这一侧的责任。）
    return import_text(
        store,
        project_id,
        text=txt.read_text(encoding="utf-8-sig"),
        source=str(txt),
        root=root,
    )


def prepare_text(text: str, *, source: str) -> Chapterization:
    """切分内存中的书稿；零章在任何磁盘或数据库写入前拒绝。"""
    book = chapterize(text.removeprefix("\ufeff"))
    if not book.chapters:
        raise ImportRefused(
            f"{source} 里一个章标都没切出来（认的是行首的「第N章/节/回」）。\n"
            "  零章不是「这本书是空的」，是「切章器没认出这本书的章标写法」——\n"
            "  别把它当成导入成功：落库零章的产物是一个永远定位不到任何引语的项目。"
        )
    return book


def import_prepared(
    store: GraphStore,
    project_id: str,
    *,
    book: Chapterization,
    root: Path,
) -> ImportReport:
    """把已经验证并切好的内存书稿写盘，再将其同步进库。"""
    written, unchanged = explode(book, root)
    return ImportReport(
        chapter_count=len(book.chapters),
        preamble_chars=len(book.preamble.strip()),
        written=written,
        unchanged=unchanged,
        synced=sync(store, project_id, root),
    )


def import_text(
    store: GraphStore,
    project_id: str,
    *,
    text: str,
    source: str,
    root: Path,
) -> ImportReport:
    """导入内存中的 TXT，且不创建临时文件。"""
    return import_prepared(
        store,
        project_id,
        book=prepare_text(text, source=source),
        root=root,
    )
