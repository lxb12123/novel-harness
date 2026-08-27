"""合成小册子 → 一个真的库。**这份仪器现在只服务 M3 那张卷子。**

── 2026-08-24：它掉了一半 ────────────────────────────────────────────────

这个文件原来同时干两件事：造库，以及派生 M2「防泄漏」考试的 `ground_truth.json`
（陷阱 × 禁忌集 × tell）。**秘密整套功能下线之后，后一半的题目不存在了**
（ADR 0039 / `docs/EVAL_PROTOCOL_RETIREMENT.md`）。

**留下的是造库那一半**，因为 `synth/m3_replay.py` 要在它造出来的 `gate.db` 上跑
R3 的误报门槛（`docs/M3_GATE_PROTOCOL.md`），而那张卷子**零秘密**——
M3_GATE_PROTOCOL.md 自己写着「KNOWS 类知识越界不在本门槛内」。

删掉的：`SecretSpec` / `KnowsSpec` / `BelievesSpec` / `BookletTrap` /
`GroundTruthTrap` / `GroundTruth` / `_derive` / tell 别名那一步 / `--out` 这个参数。

⚠️ **2026-08-27：`FutureSpec`（`[[future]]`）也删了。** R2 FUTURE_LEAK 产品下线
（ADR 0040），M3 门槛只剩 R3（修正案 1），而 `FutureSpec` 存在的唯一理由是给 R2
一份**真写进图里**（不靠 overlay）的「非角色节点 + 未来首现章」——R3 不读非角色
节点的 first_appears_chapter，这份能力从此没有消费者。

⚠️ **同日，`EntitySpec`（`[[entity]]`）也删了。** 删 `FutureSpec` 那次留了一句
「删不删是另一次判断」——现在做了那次判断：判据和 `FutureSpec` 相同，**M3 那 10
道 R3 题用不用得到它**。5 个 Foreshadow 节点（原 M2 KNOWS 的替身，ADR 0039）没有
一个出现在 `m3_ground_truth.json` 现存的 10 道题里——它们的名字/别名仍然留在
`booklet.txt` 的正文里（那是历史遗留的故事文本，不动），但作为**图节点**的声明
没有任何消费者。删本地 `gate.db`、用新 `booklet.toml` 重 build、跑
`m3_replay.py` 全绿（证据同 `FutureSpec` 那次，见对应提交）。`NON_CHARACTER_LABELS`
随它一起删——那个常量除了在 `EntitySpec` 自己的 docstring 里被提一句，从没被
任何代码真正读过。

── 它为什么不在 `src/` 里 ────────────────────────────────────────────────

**合成小册子是仪器不是产品。** 它不进 wheel、不装进 .venv，所以吃它的测试要自己
补 `sys.path`。这不是坏味道，是那条决定的直接后果。

── `extra="forbid"` 是这里最重要的一行 ───────────────────────────────────

TOML 里一个手滑的键（`chapter = 12` 而不是 `chapters = 12`、`alias` 而不是 `aliases`）
若被静默忽略，产物是一份「作者以为自己写了、实际什么都没写」的小册子——而它跑得通、
出得来数字。**一个看起来正常的假结果**正是量具最不能有的东西。
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_harness import project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import import_book

HERE: Final = Path(__file__).resolve().parent


class BuildRefused(Exception):
    """小册子和落库结果对不上，**在库被当成合格考场之前**停下。

    每一条都必须说清「哪儿、期望什么、实际什么」——一条只说「构建失败」的消息等于把
    问题原样丢回给人，而这个仓库里所有拒绝（`DeclarationRefused` / `ImportRefused`）
    都是摆候选、不猜。
    """


# ══════════════════════════════════════════════════════════════════════════
# booklet.toml 的 schema
# ══════════════════════════════════════════════════════════════════════════


class BookMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    chapters: int = Field(ge=1)
    """目录数。`build()` 拿它和 `import_book` 实际切出来的章数对，不等就抛。"""


class CharacterSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical: str = Field(min_length=1)
    """本名。保证 `resolve` 唯一，不触发 fail-closed 退化。"""

    aliases: list[str] = Field(default_factory=list)
    """非 canonical 显示名，可空。"""


class Booklet(BaseModel):
    """`booklet.toml` 的全部内容。字段名 = TOML 里的表名，读代码时用复数别名。"""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    book: BookMeta
    characters: list[CharacterSpec] = Field(default_factory=list, validation_alias="character")

    @model_validator(mode="after")
    def _names_are_unique(self) -> Booklet:
        # 重名不是洁癖问题：`upsert_node` 的幂等键是 (project_id, label, name)，两条同名
        # 声明会**合并成一个节点**，而 TOML 看起来完全正常。
        names = [c.canonical for c in self.characters]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"character.canonical 重复：{dupes}")
        return self


def load_booklet(path: Path) -> Booklet:
    """读 + 校验 `booklet.toml`。**全仓只有这一个 loader。**

    两份解析 = 两份 schema，而其中一份迟早会把手滑的键放过去。这跟 `graph/queries.py`
    的时态过滤只实现一次是同一条道理。
    """
    return Booklet.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


class BuildResult(BaseModel):
    """`build()` 干了什么。数字都是**落库之后**数出来的，不是从 TOML 数的。"""

    model_config = ConfigDict(frozen=True)

    project_id: str
    chapters: int
    characters: int


# ══════════════════════════════════════════════════════════════════════════
# build
# ══════════════════════════════════════════════════════════════════════════


def build(*, booklet: Path, prose: Path, db: Path) -> BuildResult:
    """造一份 M3 门槛用的库。

    Args:
        booklet: `booklet.toml`。
        prose: `booklet.txt`，未切章的全文。
        db: 要建的库。**必须不存在**（见下）。`chapters/` 落在它的同级目录。

    Raises:
        BuildRefused: 库已存在，或切章数对不上。
        DeclarationRefused: 声明被拒（`declare.py` 会摆候选出来）。

    Notes:
        **为什么库必须不存在。** `project.create` 每次都建一个新项目，而 `migrate` 是
        幂等的——往同一个库跑第二次不会报错，只会多出一个项目，第一个项目的数据还躺在
        库里。那不是错误，是**两份看起来都对的真相**，而 `m3_replay` 那道「说得出考哪个」
        的门正是为这件事存在的。
    """
    bk = load_booklet(booklet)

    if db.exists():
        raise BuildRefused(
            f"{db} 已存在，不覆盖。\n"
            "  往同一个库再跑一次不会报错，只会多出一个项目——库里于是有两份看起来都对的\n"
            "  真相。请指一个新路径。（上一次 build 中途拒绝时留下的半成品库也算。\n"
            "   本函数不替你删任何文件——在这个仓库里「自动删掉一个已存在的文件」是不许\n"
            "   有的动作，自己 rm 它。）"
        )

    root = db.parent
    conn = connect(db)
    try:
        migrate(conn)
        pid = project.create(conn, name=bk.book.title, root_path=str(root)).id
        store = SqliteStoryGraph(conn)

        report = import_book(store, pid, txt=prose, root=root)
        if report.chapter_count != bk.book.chapters:
            raise BuildRefused(
                f"切章数对不上：{prose} 切出 {report.chapter_count} 章，"
                f"[book].chapters 写的是 {bk.book.chapters}。\n"
                f"  （第一个章标之前有 {report.preamble_chars} 个字落进了 preamble——"
                "异常地长往往意味着第一章的章标没被认出来，那时全书 index 集体少 1，\n"
                "  每一条 valid_from 都错一章。）"
            )

        ledger = Ledger(store, conn, pid)

        for character in bk.characters:
            ledger.declare_node(
                NodeLabel.CHARACTER, character.canonical, aliases=character.aliases
            )
    finally:
        conn.close()

    return BuildResult(
        project_id=pid,
        chapters=report.chapter_count,
        characters=len(bk.characters),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m synth.build`。造完拿 `python -m synth.m3_replay` 跑 M3 门槛。"""
    parser = argparse.ArgumentParser(description="合成小册子 → M3 门槛用的库")
    parser.add_argument("--booklet", type=Path, default=HERE / "booklet.toml")
    parser.add_argument("--prose", type=Path, default=HERE / "booklet.txt")
    parser.add_argument("--db", type=Path, default=HERE / "gate.db")
    args = parser.parse_args(argv)

    result = build(booklet=args.booklet, prose=args.prose, db=args.db)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
