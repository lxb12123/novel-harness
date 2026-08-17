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

import fcntl
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Final, Literal

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


_CN_DIGITS: Final = "零一二三四五六七八九"
_CN_UNITS: Final = ("", "十", "百", "千")


def chinese_number(value: int) -> str:
    """`2` → `二`、`101` → `一百零一`。**只有「新起一章」用得着它。**

    切章器认阿拉伯数字（`CHAPTER_RE` 的字符组里有 `0-9`），所以写「第2章」也能跑通。
    但那本书的第一章叫「第一章」——同一本书里「第一章、第2章、第3章」是作者一眼
    就会看见的东西，而他是写小说的，不是读日志的。

    `十一` 而不是 `一十一`：十位打头的两位数中文里不念那个「一」。但 `一百一十` 要念，
    所以那一刀只切在开头（见最后那一行）。
    """
    if not 1 <= value <= 9999:
        raise ValueError(f"章号超出中文数字的范围：{value}")
    out = ""
    zero_pending = False
    for power in (3, 2, 1, 0):
        digit = value // 10**power % 10
        if digit == 0:
            # 中间的 0 只补**一个**「零」，而且末尾那些 0 不补（`一千` 不是 `一千零`）。
            zero_pending = bool(out)
            continue
        if zero_pending:
            out += "零"
            zero_pending = False
        out += _CN_DIGITS[digit] + _CN_UNITS[power]
    return out[1:] if out.startswith("一十") else out


def empty_chapter_text(number: int) -> str:
    """一章空正文的**全部内容**（`第二章\\n\\n`）。

    **新建一章只有这一个写法**：空白新书开局那一章走它，作者在书架上按「＋」新起一章
    也走它。两处各写一份字面量的话，哪天章标形状变了（比如改成阿拉伯数字），
    会有一半的章跟着变、另一半不变，而症状是「目录里有两种章号」。
    """
    return f"第{chinese_number(number)}章\n\n"


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


class ChapterLockTimeout(Exception):
    """章级锁在 `lock_timeout` 内没拿到（另一个保存 / reconcile 还在跑这一章）。

    HTTP 映射成 423（Locked）：**不排队、不覆盖**。作者看一眼稍后重试即可；
    让他靠重复点击覆盖冲突正是这条锁要防的事。
    """


class PostReplaceDurabilityError(OSError):
    """`os.replace()` 已经成功、但目录 `fsync()` 失败。

    **目标文件的字节已经变了**——调用方必须返回 `202 + saved_to_disk=true +
    indexed=false`（正文已写盘，但持久性/索引确认失败），不能把它归入写盘前失败，
    也不能谎报 409「正文未保存」。恢复靠后续 reconcile。
    """


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


class ChapterSaveReceipt(BaseModel):
    """保存一章的**回执**——不再把整本 `SyncReport` 塞回给保存请求。

    三个布尔讲三件不同的事（`saved_to_disk` / `indexed` / `changed` 不许互相推导）：

    - `saved_to_disk`：目标文件已经是这份正文（`os.replace` 成功）。False 只出现在
      写盘前被拒（结构预检 / expected hash 冲突 / 锁超时），那几种走异常，不走回执。
    - `indexed`：`chapter_snapshot` / `chapter` 已经承认这份正文。False 的两档
      （`durability_failed` / `sync_failed`）都必须是 202，且**不是让前端重发 PUT 的
      暗号**——重复覆盖修不了数据库，恢复必须走后续 reconcile。
    - `changed`：保存前后的当前 `text_sha256` 是否真的变了。相同 hash 重存不加
      generation、不新建快照；从 S2 还原到历史 S1 也算变化（ABA 防护）。

    `processing` 的 `queued/reused/attention_required` 三档属于刷新 coverage
    （Task 5/16）；本阶段保存成功统一报 `reused`，只承诺「没有需要排队的分支」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    saved_to_disk: bool
    indexed: bool
    changed: bool
    chapter_number: int = Field(ge=1)
    snapshot_id: str | None
    snapshot_generation: int | None = Field(default=None, ge=1)
    text_sha256: str
    processing: Literal[
        "reused",
        "queued",
        "attention_required",
        "durability_failed",
        "sync_failed",
    ]


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


def _read_one_chapter(
    store: GraphStore, project_id: str, number: int, file: Path
) -> StoredChapter:
    """把**一个**章节文件读进库。`sync` 和 `sync_chapter` 共用的那一份实现。

    **拆出来是因为有了第二个调用方**（`sync_chapter`，2026-08-14 后台整理跑之前那一下），
    而这段里有三条各自付过学费的约束：`chapterize` 只此一份、恰好一章才落、
    进库的是**文件全文**。抄第二份的那一天，坏的会是其中某一条，而它坏了之后
    每一条证据的 `para_index` 全体偏移——面板照旧画得出来。
    """
    rel = f"{CHAPTER_DIR}/{file.name}"
    # ⚠️ **先 stat，再读。这个顺序不许反。**
    #
    # 反过来的话：文件在 read 和 stat 之间被改一次 → 记下的 mtime 比读到的内容**新**
    # → 下一次 stat 看着一致 → **那次改动永远发现不了**。先 stat 的最坏情况是
    # 「记下的 stat 比内容旧 → 下次多读一遍」，无害。（迁移 015 / `ChapterSpec` 那两个字段）
    stat = file.stat()
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
            disk_mtime_ns=stat.st_mtime_ns,
            disk_size=stat.st_size,
        )
    )
    # ── 新正文落地了 → 让锚在旧那一版上的抽取事实退休（2026-08-14）──────────
    #
    # **这一下必须紧贴 `put_chapter`**：三条磁盘写路径（`sync` 整本 /
    # `sync_chapter` 单章 / `save_chapter` 作者按保存和助手落稿）全都从这个函数过，
    # 放在这儿等于一次覆盖三条。放到调用方去，迟早有一条忘了调。
    #
    # 不调的后果实测过：同一章分析两次，事件从 2 条变 4 条（一模一样两组），
    # 而同一个人在同一章同时 ACTIVE 在两个地点——**R4 会报一条正文里根本不存在的
    # 位置冲突**，作者对着稿子完全看不懂。
    #
    # 幂等：没有旧锚时改 0 行。所以整本 sync 每章都调一次也不要紧。
    store.retire_stale_extractor_facts(project_id, stored.id, stored.snapshot_id)
    return stored


def sync_chapter(
    store: GraphStore, project_id: str, root: Path, chapter: int
) -> StoredChapter | None:
    """只把**第 `chapter` 章**读进库。`None` = 磁盘上没有那个文件。

    ── 它为什么存在，而不是让调用方跑一次整本 `sync` ──────────────────────────

    后台整理（`api/autopilot.py`）在跑抽取之前要确认这一章的快照就是磁盘上那份——
    **抽取是按快照跑的**（`extract/runner.py` 那条 `text_sha256 = chapter.text_sha256`
    的 JOIN），快照旧了它分析的就是旧正文，而屏幕上没有任何东西说它读的是旧的。

    整本 `sync` 也能达到目的（722 章实测 80ms），但它会对**每一章**都
    `UPDATE … updated_at`——作者每换一次章就搅一遍全书的 WAL，而其中 721 章
    和这次要跑的那一章毫无关系。**后台动作的写入面要和它的目的一样窄。**

    `SyncRefused` 照旧往外抛：一个切不出恰好一章的文件是作者要知道的事
    （章标题写坏了），不是后台该吞掉的。
    """
    file = root / chapter_path(chapter)
    if not file.is_file():
        return None
    with chapter_lock(store, project_id, root, chapter):
        return _read_one_chapter(store, project_id, chapter, file)


class ReconcileReport(BaseModel):
    """把库和磁盘对一遍之后：看了几章、读了几章、哪几章读不回来。

    **`refused` 不能是空着的**：一个章标写坏了的文件会让那一章从此既总结不了也抽不了，
    而这条路径是**作者没按过任何按钮**的（回焦自动跑）。不带出来的话，
    「什么都没发生」和「有一章一直读不回来」在屏幕上长得一模一样（约束 8）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked: int = Field(default=0, ge=0)
    """磁盘上一共有几个章节文件。"""

    reread: tuple[int, ...] = ()
    """stat 对不上、于是真的读了一遍的那些章号。**不等于「内容变了」**——
    文件被 touch 过（内容一模一样）也会进这里，只是读完之后 sha 相同、不落新快照。"""

    refreshed: tuple[int, ...] = ()
    """内容**真的**变了、落了新快照的那些章。"""

    refused: tuple[tuple[int, str], ...] = ()
    """`(章号, 为什么读不回来)`。今天只有一种：切不出恰好一章（章标写坏了）。"""


def reconcile(
    store: GraphStore, project_id: str, root: Path, *, deep: bool = False
) -> ReconcileReport:
    """把库和磁盘对一遍。**`deep=False` 时先 stat，只有对不上才读文件。**

    ── 两级，因为 mtime 会撒谎 ────────────────────────────────────────────────

    `deep=False`（回焦时跑）：722 章实测 ~1–2ms，因为绝大多数章一次 `stat` 就过了。
    `deep=True`（开书时跑一次）：忽略 stat，每一章都 read + hash，66ms。

    第二级不是冗余：`rsync -t` / `cp -p` / 从备份恢复都可能**保留原 mtime**，
    大小又恰好没变的话，快路那一关就漏了（Git 自己也有这个病，就是 "racy git"）。
    在此之前兜这一档的是作者手点「读回改动」，而那颗按钮正要退休。

    ── 一章读不回来不许拖垮整本 ──────────────────────────────────────────────

    `sync` 撞上一个切不出恰好一章的文件会直接抛，于是**整本书一章都对不上**。
    那条语义对「作者点了导入」是对的（他在等一个结果），对这条**没人按过**的路径
    是错的：第 500 章的章标写坏了，不该让第 100 章的改动也读不回来。
    所以这里逐章收集，最后一起带出去。
    """
    directory = root / CHAPTER_DIR
    if not directory.is_dir():
        return ReconcileReport()

    recorded = {} if deep else store.chapter_disk_stats(project_id)
    reread: list[int] = []
    refreshed: list[int] = []
    refused: list[tuple[int, str]] = []
    checked = 0
    for file in sorted(directory.iterdir()):
        if not file.is_file():
            continue
        m = _CHAPTER_FILE_RE.match(file.name)
        if m is None:
            continue
        number = int(m.group(1))
        checked += 1
        # **先 stat**（同 `_read_one_chapter` 那条顺序纪律）。`(None, None)` = 没记过，
        # 比不上就当成「变了」——老库升级上来的第一次会全读一遍，之后就便宜了。
        stat = file.stat()
        if not deep and recorded.get(number) == (stat.st_mtime_ns, stat.st_size):
            continue
        reread.append(number)
        try:
            with chapter_lock(store, project_id, root, number):
                stored = _read_one_chapter(store, project_id, number, file)
        except SyncRefused as exc:
            refused.append((number, str(exc)))
            continue
        if stored.created or stored.snapshot_created:
            refreshed.append(number)
    return ReconcileReport(
        checked=checked,
        reread=tuple(reread),
        refreshed=tuple(refreshed),
        refused=tuple(refused),
    )


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
        with chapter_lock(store, project_id, root, number):
            stored = _read_one_chapter(store, project_id, number, file)
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


def validate_chapter_markdown(markdown: str) -> Chapter:
    """保存前的**零副作用结构预检**：`markdown` 必须恰好是一章且章标前没有正文。

    失败时抛 `SyncRefused`，**磁盘和数据库都不修改**（这是它和 `sync` 事后报错的
    本质区别：`sync` 读的是已经写进盘的文件，这一道读的是还没写盘的字符串）。
    """
    chapter = single_chapter(markdown)
    if chapter is None:
        raise SyncRefused(
            "一个章节文件必须恰好是一章，且章标前不能有正文（写盘前预检拒绝，"
            "磁盘和数据库都没有改）。",
            path="",
        )
    return chapter


def atomic_replace_text(path: Path, text: str) -> None:
    """原子替换一个章节文件：临时文件 fsync → `os.replace` → 目录 fsync。

    - 临时文件写在**目标文件同目录**：`os.replace` 才保证同文件系统原子换名。
    - 目录 fsync 把「换名」本身落盘（不然断电后可能回到旧文件名）。
    - `os.replace` 成功后目录 fsync 再失败 → `PostReplaceDurabilityError`
      （**目标字节已经变了**，调用方按 202 处理，不能按写盘前失败处理）。
    - 异常时只清理**这一个**精确临时文件，绝不递归删除目录。
    """
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    replace_done = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        replace_done = True
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException as exc:
        temp.unlink(missing_ok=True)
        if replace_done:
            raise PostReplaceDurabilityError(path) from exc
        raise


LOCK_DIR: Final = ".novel-harness/locks"


def _chapter_lock_key(store: GraphStore, project_id: str, number: int) -> str:
    """一章的稳定锁键。优先用库里的 `chapter.id`（计划写的是 `<chapter_id>.lock`）；
    那章还没进过库时退回 `ch{number}`——save 和 reconcile 用同一个函数，两边永远同解。
    """
    chapter_id = store.current_chapter_id(project_id, number)
    return chapter_id if chapter_id is not None else f"ch{number}"


class _ChapterLock:
    """章级 advisory lock（`fcntl.flock`，跨进程生效）。

    锁**文件**而不是 Markdown 本身：`os.replace` 会换 inode，锁在正文文件上等于
    没锁。锁路径固定在 `.novel-harness/locks/`，和库、章节目录一样住在书文件夹里。
    """

    def __init__(self, root: Path, key: str, *, timeout: float) -> None:
        lock_dir = root / LOCK_DIR
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._path = lock_dir / f"{key}.lock"
        self._timeout = timeout
        self._handle = None

    def __enter__(self) -> _ChapterLock:
        handle = self._path.open("w")
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise ChapterLockTimeout(
                        f"第 {self._path.stem} 章正被另一个保存/reconcile 处理，"
                        "锁在超时内没拿到，一个字都没写。稍后重试。"
                    )
                time.sleep(0.01)

    def __exit__(self, *exc: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def chapter_lock(
    store: GraphStore, project_id: str, root: Path, number: int, *, timeout: float = 2.0
) -> _ChapterLock:
    """save 与 reconcile 共用的同一条锁入口（锁覆盖磁盘/DB 整段，有界超时）。"""
    return _ChapterLock(root, _chapter_lock_key(store, project_id, number), timeout=timeout)


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
    lock_timeout: float = 2.0,
) -> ChapterSaveReceipt:
    """把一章正文写进磁盘，再落快照。**磁盘先、DB 跟**（ADR 0007）。

    保存这条路只有这一个实现：作者按 Ctrl-S 走它（`PUT …/chapters/{n}/text`），
    agent 起草完落盘也走它（ADR 0021）。**一个功能不留两个入口**。

    临界区（Task 2）：写盘前结构预检 → 章级锁 → 读磁盘/DB hash → 校验 expected →
    原子替换 → 单章落库 → 提交后复核磁盘。锁覆盖磁盘/DB 整段，与 reconcile 同一条
    `chapter_lock`，两个协作式 PUT 不能交错出「磁盘 S3 / DB S2」。

    Args:
        expected_sha256: 调用方**依据的那一份**正文的哈希（来自 GET / 成功索引回执）。
            先在锁内与磁盘/DB 当前值比对，不一致 → `ChapterChanged`，**一个字节都不写**。
            保存请求已经带完整正文，直接算精确 hash，不再走 stat。
        lock_timeout: 拿章级锁的有界超时（秒）。超时 → `ChapterLockTimeout`（423）。

    Returns:
        `ChapterSaveReceipt`。写盘前失败走异常；`os.replace` 成功后的两类失败
        （目录 fsync / DB 提交）返回 202 形态的回执，并立即 reconcile 修复。

    Raises:
        ChapterMissing: 那一章的文件不存在（404；ADR 0021：agent 不许自己新建章节）。
        ChapterChanged: 磁盘/DB 上那份已经比 `expected_sha256` 新（409）。
        SyncRefused: **写盘前**结构预检失败（422；磁盘和数据库都没有改）。
        ChapterLockTimeout: 锁在 `lock_timeout` 内没拿到（423）。
    """
    # ① 零副作用结构预检：失败时磁盘和数据库都不修改。
    chapter_obj = validate_chapter_markdown(markdown)
    file = root / chapter_path(chapter)
    if not file.exists():
        raise ChapterMissing(f"第 {chapter} 章在磁盘上不存在", chapter)

    new_sha = text_digest(markdown)

    def _reconcile_after_write() -> None:
        """写盘后的补救：把磁盘现状读回库（锁已持有，`_read_one_chapter` 不再加锁）。

        202 的两档都是「磁盘已变、恢复靠 reconcile」；这里同步先补一次，补不上也
        不吞——下一轮 reconcile 会再试（`disk_mtime_ns=None` 时必然重读）。
        """
        try:
            _read_one_chapter(store, project_id, chapter, file)
        except SyncRefused:
            pass

    with chapter_lock(store, project_id, root, chapter, timeout=lock_timeout):
        # ② 入锁后读磁盘精确 hash，并核对 DB current hash（DB 落后则先 reconcile）。
        disk_before = file.read_text(encoding="utf-8-sig")
        disk_hash_before = text_digest(disk_before)
        db_hash = store.current_chapter_hash(project_id, chapter)
        if db_hash is not None and db_hash != disk_hash_before:
            # 外部写者改了磁盘、库还没跟上：先把磁盘版收进库（被覆盖的那一版因此
            # 进得了版本历史），再拿它当 base 继续。磁盘版切不成一章时收编不了，
            # 库保持原状，写盘照走——旧版本来就进不了快照。
            try:
                _read_one_chapter(store, project_id, chapter, file)
                db_hash = store.current_chapter_hash(project_id, chapter)
            except SyncRefused:
                pass
        if expected_sha256 is not None and expected_sha256 != db_hash:
            raise ChapterChanged(
                f"第 {chapter} 章已经变了",
                chapter,
                expected=expected_sha256,
                actual=db_hash or disk_hash_before,
            )

        # ③ 原子替换。replace 成功后目录 fsync 失败 → 202 durability_failed。
        try:
            atomic_replace_text(file, markdown)
        except PostReplaceDurabilityError:
            _reconcile_after_write()
            return ChapterSaveReceipt(
                saved_to_disk=True,
                indexed=False,
                changed=disk_hash_before != new_sha,
                chapter_number=chapter,
                snapshot_id=None,
                snapshot_generation=None,
                text_sha256=new_sha,
                processing="durability_failed",
            )

        # ④ 单章落库（旧实现是整本 sync；这里只碰这一章，写入面与目的一样窄）。
        try:
            stat = file.stat()
            stored = store.put_chapter(
                ChapterSpec(
                    project_id=project_id,
                    number=chapter,
                    heading=chapter_obj.raw_heading,
                    title=chapter_obj.title,
                    path=chapter_path(chapter),
                    text=markdown,
                    disk_mtime_ns=stat.st_mtime_ns,
                    disk_size=stat.st_size,
                )
            )
        except BaseException:
            # 写盘成功后 DB 提交才失败 → 202 sync_failed，不能谎报 409/200。
            _reconcile_after_write()
            return ChapterSaveReceipt(
                saved_to_disk=True,
                indexed=False,
                changed=disk_hash_before != new_sha,
                chapter_number=chapter,
                snapshot_id=None,
                snapshot_generation=None,
                text_sha256=new_sha,
                processing="sync_failed",
            )

        # ⑤ 提交后复核：外部写者若在我们 replace 与 DB 提交之间又改了盘，返回 202，
        # 立即 reconcile 把磁盘真相收编（Task 2 先补索引；outbox 级联在 Task 16）。
        after = file.read_text(encoding="utf-8-sig")
        if text_digest(after) != new_sha:
            _reconcile_after_write()
            return ChapterSaveReceipt(
                saved_to_disk=True,
                indexed=True,
                changed=True,
                chapter_number=chapter,
                snapshot_id=None,
                snapshot_generation=None,
                text_sha256=text_digest(after),
                processing="sync_failed",
            )

        return ChapterSaveReceipt(
            saved_to_disk=True,
            indexed=True,
            changed=disk_hash_before != new_sha,
            chapter_number=chapter,
            snapshot_id=stored.snapshot_id,
            snapshot_generation=stored.snapshot_generation,
            text_sha256=new_sha,
            processing="reused",
        )


def append_chapter(store: GraphStore, project_id: str, root: Path) -> tuple[int, SyncReport]:
    """在这本书末尾新起一章（只有章标，正文是空的），返回它的章号。

    **章号由磁盘决定，作者填不了**——同「`valid_from` 只由证据决定」那条规矩：
    表单里一有章号输入框，就等着有人把第 7 章建成第 3 章，而章号是全书的顺序键。
    取的是现有文件里最大的那个 +1，不是「文件个数 +1」：中间缺了一章（作者自己删了
    `0003.md`）时后者会撞上一个已存在的号。

    `save_chapter` 那条 404 仍然关着（ADR 0021：agent 不许自己新建章节）。
    这条路是**作者按了按钮**才走的，而章标由这儿生成、不由任何模型写。

    Raises:
        FileExistsError: 那个文件已经在了。**不覆盖**——`chapter_files` 刚读过一遍，
            这一刻它还能存在只可能是另一个进程同时也在新起一章，而覆盖掉的是正文。
        SyncRefused: 写出去的东西切不出一章（`empty_chapter_text` 坏了才可能）。
    """
    existing = chapter_files(root)
    number = existing[-1].number + 1 if existing else 1
    file = root / chapter_path(number)
    file.parent.mkdir(parents=True, exist_ok=True)
    # "x" = 存在就抛，不覆盖。`exists()` 判一下再写是两步，中间那道缝正是要防的东西。
    with file.open("x", encoding="utf-8") as handle:
        handle.write(empty_chapter_text(number))
    return number, sync(store, project_id, root)


DELETED_DIR: Final = "deleted"
"""删掉的章去哪儿。**同一本书的文件夹底下，作者自己看得见。**

`chapter_files` 只认 `chapters/NNNN.md`，所以挪进这儿的文件对引擎等于不存在，
而对作者等于「还在」——他打开书的文件夹就能找回来，不用命令行、不用问任何人。
"""


def remove_chapter(store: GraphStore, project_id: str, root: Path, chapter: int) -> Path:
    """删掉一章：先过图层那一关，再把 .md **挪走**（不是删掉）。返回它挪到了哪儿。

    ── 两条顺序上的选择，都不是随手定的 ──────────────────────────────────────

    **① 先库后盘**，跟 `save_chapter` 的「磁盘先、DB 跟」（ADR 0007）**是反的**。
    因为这一次库那边**会拒绝**（`ChapterInUse`）：盘先动的话，作者会看见文件没了、
    然后弹一句「删不掉」——两件事同时成立，而他没有任何办法把它放回去。
    反过来出错（库删了、文件没挪成）是自愈的：下一次 `sync` 照着磁盘把这一章重新读回来。

    **② 挪走不是删掉。** 那是作者的稿子，可能是他写了三小时的东西，而这颗按钮离
    「新起一章」只有一列的距离。挪进 `deleted/` 之后引擎立刻当它不存在
    （`chapter_files` 只认 `chapters/NNNN.md`），而他在自己的文件夹里还找得回来。
    重名不覆盖：同一章删两次（新建 → 删 → 再新建 → 再删）会有第二份，加 `-2`、`-3`。

    Args:
        chapter: 章号。**不检查它是不是最后一章**——中间留个洞是允许的
            （`append_chapter` 取的是「最大的号 +1」，正是为了这个）。

    Returns:
        挪过去之后那个文件的路径。

    Raises:
        ChapterMissing: 磁盘上没有这一章。
        ChapterInUse: 引擎在这一章上记过东西（图层抛的，明细在异常里）。
        StoreError: 这一章还没进过库（磁盘上有、没 sync 过）。**不吞掉**：
            那说明这个库和这个文件夹已经对不上了，作者该知道，而不是让删除
            悄悄地只删掉一半。
    """
    file = root / chapter_path(chapter)
    if not file.is_file():
        raise ChapterMissing(f"第 {chapter} 章在磁盘上不存在", chapter)

    store.delete_chapter(project_id, chapter)

    trash = root / DELETED_DIR
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / file.name
    serial = 2
    while target.exists():
        target = trash / f"{file.stem}-{serial}{file.suffix}"
        serial += 1
    file.rename(target)
    return target


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
