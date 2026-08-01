"""合成小册子 → 一个真的库 + `ground_truth.json`（`docs/EVAL_PROTOCOL.md` §4 / 修正案 4）。

**这个目录不进 wheel。** `uv_build` 只打包 `src/novel_harness/`，而合成小册子是**仪器**不是
产品——它有强制说话人标签、强制唯一专名 tell，一点都不像真网文（协议 §7 的「仪器范围免责」）。
把它发给小说作者只会让他以为这是模板。

── 为什么走真实写入链，而不是直接 upsert ────────────────────────────────

协议 §4 点名要求：`import_book` 切章 → `declare_node`/`declare_alias` →
`declare_knows`/`declare_believes`（引语定章号）→ 由 `scene_view()` 派生 ground truth。
一条近路都不许抄，理由不是仪式感：

1. **`valid_from` 必须由引语派生**（约束 10 / ADR 0006）。手填章号的小册子测的是
   「我写的数字对不对」，而产品里那个数字是引擎从证据算出来的。两者不是同一个被测物。
2. **ground truth 必须由 `scene_view()` 派生，不许作者手写**。作者物理上没有 `forbidden`
   这个字段可填（`booklet.toml` 的 schema 里没有它），于是「注入 prompt 的禁忌集」和
   「判分器要找的禁忌集」是同一次计算的两个出口——它们**没有对不上的余地**。
3. 顺带把「切章数 == `[book].chapters`」验一次（协议 §4 的「12→12」）。这条在真书上是
   M0 欠着的验收，在小册子上是**免费**的：小册子的目录就是 TOML 里那个数字。

── tell 必须声明成**非 canonical** 别名，搞反了 gate 会自己命中自己 ──────

`secret_surfaces()` 取秘密的可匹配 surface 时**排除 canonical**（= 显示名 `血脉秘密`），
因为显示名正是进 prompt 的那个标签。于是：

    进 prompt 的集合 = {canonical 显示名}   检测器找的集合 = {非 canonical 别名}

两个集合**天然不相交**，KNOWS 维度不可能 echo 假阳性——这条性质不是靠纪律，是靠
「tell 走 `declare_alias`（它拒收 canonical），显示名走 `upsert_node`」这两条互斥的写路径。
**本文件第 3 步就是这条性质的唯一实现处。** 把 tell 写成 `declare_node(name=tell)`，
或者把显示名也挂成一条 alias，两个集合当场相交，X1/X2 100% 命中自己写进去的词，
`Δ` 翻负，预注册的裁决表逐字读出「KILL 起草线」——**砍掉一条本来对的产品线，
而全程没有任何东西会红**（`tests/test_draft_boundary.py` 开头讲的就是这个失败故事）。

── 诚实交代：本文件站在第 4 道 arch-guard 的**墙外** ─────────────────────

`tests/test_draft_boundary.py` 只扫 `src/novel_harness/{draft,eval}`，而这里是顶层 `synth/`。
本文件确实同时碰了墙的两面：它调 `secret_surfaces()`（判分侧），也产出 `goal`/`prior`
（起草侧的自由文本入口）。这是**有意的**——ground truth 的定义就是「两侧共用的那一份真相」，
它必须站在两侧之上。代价要说清楚：

- 没有任何机器判据阻止本文件把 tell 写进 `ground_truth.json` 的 `goal` 字段。
  那一条由 `synth/leak_selfcheck.py` 的第 6 条放行条件（修正案 4 裁定 B）挡，不是由守卫挡。
- **runner 判分时不许读 `GroundTruthTrap.tells`**（见那个字段的说明）。它在这里存在只是
  为了让 selfcheck 能验「派生对不对」，以及让 ADR 0009 的读者知道当时找的是哪几个词。

── 它罩不住什么 ──────────────────────────────────────────────────────────

- 「这条陷阱在语义上真的咬得住吗」——验不了（ADR 0005 禁止语义判断）。本文件能验的
  只有「目标边界确实落进了引擎算出来的禁忌集」，验不了「上文读起来真的会诱使模型说破」。
  那一半只有 review 和实测的 X0 泄漏率（协议 §6 的 [0.50, 0.90] 双门）守得住。
- 小册子写得好不好、像不像小说——不验，也不该在这儿验。
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_harness import project
from novel_harness.db import connect, migrate
from novel_harness.declare import Ledger
from novel_harness.draft.context import ResolvedConstraints
from novel_harness.eval.score import TrapKind
from novel_harness.graph import AliasKind, NodeLabel, NodeProps, SecretDetail
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.importer import import_book
from novel_harness.panel.constraints import scene_view, secret_surfaces

HERE: Final = Path(__file__).resolve().parent

FUTURE_LABELS: Final = ("Faction", "Location", "Object")
"""未来实体只许是这三类。

不是全部 `NodeLabel`：`Character` 的首现有它自己的规则（R3「未登场角色开口说话」），
`Secret` 走 KNOWS 那一侧，`Chapter` 只能由 `put_chapter` 建。写死成三个，
是为了让「作者在 TOML 里手滑写了 `Charater`」当场炸，而不是切出一类没人消费的节点。
"""


class BuildRefused(Exception):
    """小册子和它派生出来的 ground truth 对不上，**在写出 `out` 之前**停下。

    每一条都必须说清「哪条陷阱、期望什么、实际什么」——一条只说「构建失败」的消息
    等于把问题原样丢回给人，而这个仓库里所有拒绝（`DeclarationRefused` / `ImportRefused`）
    都是摆候选、不猜。
    """


# ══════════════════════════════════════════════════════════════════════════
# booklet.toml 的 schema —— **`extra="forbid"` 是这里最重要的一行**
# ══════════════════════════════════════════════════════════════════════════
#
# TOML 里一个手滑的键（`chapter = 12` 而不是 `chapters = 12`、`alias` 而不是 `aliases`）
# 若被静默忽略，产物是一份「作者以为自己写了、实际什么都没写」的小册子——而它跑得通、
# 出得来数字。那正是 kill-gate 最不能有的东西：一个看起来正常的假结果。


class BookMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    chapters: int = Field(ge=1)
    """目录数。`build()` 拿它和 `import_book` 实际切出来的章数对，不等就抛。"""


class CharacterSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical: str = Field(min_length=1)
    """本名。陷阱的 `cast` 只许写它——保证 `resolve` 唯一，不触发 fail-closed 退化。"""

    aliases: list[str] = Field(default_factory=list)
    """非 canonical 显示名，可空。它们**不是** tell：角色没有 tell，只有秘密和未来实体有。"""


class KnowsSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    who: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    """必须在 `booklet.txt` 里**唯一**命中。章号由它派生，作者填不了（约束 10）。"""


class BelievesSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    who: str = Field(min_length=1)
    value: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class SecretSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    """显示名，**进 prompt**（`must_not_reveal：血脉秘密`）。"""

    tell: str = Field(min_length=2)
    """内容 tell，**只进检测器**。声明成非 canonical 别名（见模块 docstring 第二节）。

    `min_length=2`：1 字别名不许 `usable_for_rules`（ADR 0004，`AliasSpec` 会拒），
    而一个不 usable 的 tell 就是一个检测器永远找不到的 tell——那条陷阱静默失效。
    """

    knows: list[KnowsSpec] = Field(default_factory=list)
    believes: list[BelievesSpec] = Field(default_factory=list)


class FutureSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=2)
    """名字**自身就是 tell**（协议 §3）。这一侧没有「标签 ⟂ tell」那条性质，
    所以 `future_leak` 只作描述性地板、不主导裁决。"""

    label: Literal["Faction", "Location", "Object"]
    first_appears: int = Field(ge=1)


class BookletTrap(BaseModel):
    """一条陷阱行。**名字刻意不叫 `TrapSpec`**——那个名字是 `eval/runner.py` 的，
    它是 runner 喂给三臂的入参；这里是作者写在 TOML 里的原始声明，两者不是一回事。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    """`KNOWS` 用 `K##`，`FUTURE` 用 `F##`。**全书唯一**（`score._validate` 会拒重复 id）。"""

    kind: TrapKind
    chapter: int = Field(ge=1)
    cast: list[str] = Field(min_length=1)
    """canonical 名。空 cast 会让约束退化成「全部秘密」，而那条**安静地穿过**
    `require_resolved_cast()`（`draft/context.py` 第二节）——所以这里 `min_length=1`。"""

    target: str = Field(min_length=1)
    """`secret.label` 或 `future.name`。由 `Booklet` 的 validator 验它存在。"""

    goal: str = Field(min_length=1)
    """本场目标。**不许含任何 tell**（修正案 4 裁定 B）：含了就强迫三臂都写它，
    Δ 被压到 0。这条由 `leak_selfcheck` 的第 6 条挡，不由本 schema 挡——
    因为它要拿全部 tell 去比，而单条 trap 看不见别的秘密。"""

    prior: str = Field(min_length=1)
    """X0 上文，三臂逐字节共用（ADR 0010 D4）。

    KNOWS：**必须**让 target 的 tell 以叙述层/他人视角出现（修正案 4 裁定 A）——
    否则 X0 物理上够不着那个字符串，泄漏率恒 ≈ 0，裁决表只能输出 INVALID。
    FUTURE：首现章之前一个 tell 都不许出现（裁定 C，两侧不对称是有依据的）。
    """

    reference: str = Field(min_length=1)
    """一个**不泄漏**的人工完成，供天花板门用（协议 §6 第 2 行）。"""


class Booklet(BaseModel):
    """`booklet.toml` 的全部内容。字段名 = TOML 里的表名，读代码时用复数别名。"""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    book: BookMeta
    characters: list[CharacterSpec] = Field(default_factory=list, validation_alias="character")
    secrets: list[SecretSpec] = Field(default_factory=list, validation_alias="secret")
    futures: list[FutureSpec] = Field(default_factory=list, validation_alias="future")
    traps: list[BookletTrap] = Field(min_length=1, validation_alias="trap")

    @model_validator(mode="after")
    def _names_are_unique_and_targets_exist(self) -> Booklet:
        # 重名不是洁癖问题：`upsert_node` 的幂等键是 (project_id, label, name)，两条同名
        # 秘密会**合并成一个节点**，第二条的 tell 挂到第一条上，而 TOML 看起来完全正常。
        for what, names in (
            ("character.canonical", [c.canonical for c in self.characters]),
            ("secret.label", [s.label for s in self.secrets]),
            ("future.name", [f.name for f in self.futures]),
            ("trap.id", [t.id for t in self.traps]),
        ):
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                raise ValueError(f"{what} 重复：{dupes}")

        labels = {s.label for s in self.secrets}
        futures = {f.name for f in self.futures}
        for trap in self.traps:
            pool = labels if trap.kind == "KNOWS" else futures
            if trap.target not in pool:
                raise ValueError(
                    f"陷阱 {trap.id}（{trap.kind}）的 target「{trap.target}」不存在。"
                    f"KNOWS 的 target 要是 secret.label（有 {sorted(labels)}），"
                    f"FUTURE 的要是 future.name（有 {sorted(futures)}）"
                )
        return self

    @property
    def tells(self) -> list[str]:
        """全部 tell：秘密的内容 tell + 未来实体的名字。**检测器要找的就是这些词。**"""
        return [s.tell for s in self.secrets] + [f.name for f in self.futures]

    def secret_of(self, label: str) -> SecretSpec:
        return next(s for s in self.secrets if s.label == label)

    def future_of(self, name: str) -> FutureSpec:
        return next(f for f in self.futures if f.name == name)


def load_booklet(path: Path) -> Booklet:
    """读 + 校验 `booklet.toml`。`leak_selfcheck.py` 走的是**同一个** loader。

    两份解析 = 两份 schema，而其中一份迟早会把手滑的键放过去。这跟 `graph/queries.py`
    的时态过滤只实现一次是同一条道理，只是这次的漂移会漂在「陷阱到底声明了什么」上。
    """
    return Booklet.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


# ══════════════════════════════════════════════════════════════════════════
# ground_truth.json
# ══════════════════════════════════════════════════════════════════════════


class GroundTruthTrap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: TrapKind
    chapter: int
    cast: list[str]
    target: str
    goal: str
    prior: str
    reference: str

    must_not_reveal: list[str] = Field(default_factory=list)
    """`scene_view()` 派生的秘密**显示名**（进 prompt 的那一份）。"""

    forbidden: list[str] = Field(default_factory=list)
    """`scene_view()` 派生的未来实体名字（同样进 prompt）。"""

    tells: list[str] = Field(default_factory=list)
    """**这条陷阱的 target 的** tell，不是整场的。

    ⚠️ **runner 判分时不许读这个字段。** 判分必须走
    `leak.score_against(store, pid, constraints, draft)`——禁忌集只经 `panel.constraints`
    （协议 §3 / 第 4 道 arch-guard）。这里存它只有两个用途：
    ① `leak_selfcheck` 拿它和 TOML 里声明的 tell 对，验「派生对不对」；
    ② ADR 0009 的读者要知道当时检测器找的是哪几个词。

    拿它判分的后果不是数字错一点，是**判分口径变成整场的一个子集**——某条陷阱的草稿
    写漏了别的秘密却算「没泄漏」，而 gate 会照常出一个 p 值。
    """


class GroundTruth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    chapters: int
    traps: list[GroundTruthTrap] = Field(default_factory=list)


class BuildResult(BaseModel):
    """`build()` 干了什么。数字都是**落库之后**数出来的，不是从 TOML 数的。"""

    model_config = ConfigDict(frozen=True)

    project_id: str
    chapters: int
    characters: int
    secrets: int
    futures: int
    tell_aliases: int
    """声明成非 canonical 别名的 tell 数。**它必须等于 `secrets`**——少一个就是少一条
    检测得到的秘密，而那条陷阱会安静地永远判「没泄漏」。"""

    knows_edges: int
    believes_edges: int
    traps: int


# ══════════════════════════════════════════════════════════════════════════
# build
# ══════════════════════════════════════════════════════════════════════════


def build(*, booklet: Path, prose: Path, db: Path, out: Path) -> BuildResult:
    """造一份 kill-gate 用的库 + `ground_truth.json`。

    Args:
        booklet: `booklet.toml`。
        prose: `booklet.txt`，未切章的全文。
        db: 要建的库。**必须不存在**（见下）。`chapters/` 落在它的同级目录。
        out: `ground_truth.json` 的落点。

    Raises:
        BuildRefused: 库已存在、切章数对不上、或某条陷阱的目标边界没落进引擎算出来的禁忌集。
        UnresolvedCast: 某条陷阱的 `cast` 解析不出唯一角色（**这一条最要命，见下**）。
        DeclarationRefused: 某条引语在正文里找不到或不唯一（`declare.py` 会摆候选出来）。

    Notes:
        **为什么库必须不存在。** `project.create` 每次都建一个新项目，而 `migrate` 是幂等的
        ——往同一个库跑第二次不会报错，只会多出一个项目，`out` 被第二个 project_id 覆盖，
        第一个项目的数据还躺在库里。那不是错误，是**两份看起来都对的真相**。
        （同 `scripts/seed_demo.py` 的「已存在，不覆盖」。）

        **为什么 cast 退化必须炸。** `scene_view()` 对解析不出的称呼是 fail-closed 的：
        `must_not_reveal` 退化成**全部秘密**。那时候「target 在不在禁忌集里」永远为真——
        本函数的检查会全部通过，而 gate 拿到的是一份「全禁」的基线，臂间比较当场失效。
        所以这里不自己读 `unresolved_cast`，而是过一遍 `ResolvedConstraints.of()`：
        它是 `draft/context.py` 为「cast 已解析」造的那个类型，两条退化路径（歧义 / 空 cast）
        都由它抛。**runner 拿到的也是同一个类型**，于是「小册子能建出来」等价于
        「三臂都拿得到非退化的约束」。
    """
    bk = load_booklet(booklet)

    if db.exists():
        raise BuildRefused(
            f"{db} 已存在，不覆盖。\n"
            "  往同一个库再跑一次不会报错，只会多出一个项目，而 ground_truth.json 里的\n"
            "  project_id 指向新的那个——库里于是有两份看起来都对的真相。请指一个新路径。\n"
            "  （上一次 build 中途拒绝时留下的半成品库也算。本函数不替你删任何文件——\n"
            "   在这个仓库里「自动删掉一个已存在的文件」是不许有的动作，自己 rm 它。）"
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
                "  每一条 valid_from 都错一章。）\n"
                "  这一条就是协议 §4 那句「顺带验一次切章数=目录数」。"
            )

        ledger = Ledger(store, conn, pid)

        for character in bk.characters:
            ledger.declare_node(
                NodeLabel.CHARACTER, character.canonical, aliases=character.aliases
            )
        for secret in bk.secrets:
            # canonical 别名由 upsert_node 自动建，它**就是**进 prompt 的那个显示名。
            ledger.declare_node(NodeLabel.SECRET, secret.label, secret=SecretDetail())
        for future in bk.futures:
            ledger.declare_node(
                NodeLabel(future.label),
                future.name,
                props=NodeProps(first_appears_chapter=future.first_appears),
            )

        # ★ 检测集合 ⟂ prompt 集合的唯一实现处。`declare_alias` 物理上拒收 canonical
        #   （`AliasSpec._canonical_belongs_to_upsert_node`），所以这一步不可能把 tell
        #   写成显示名——不相交性质由两条互斥的写路径保证，不由这行代码的正确性保证。
        for secret in bk.secrets:
            ledger.declare_alias(of=secret.label, surface=secret.tell, kind=AliasKind.ALIAS)

        knows = believes = 0
        for secret in bk.secrets:
            for k in secret.knows:
                # 没有 chapter 参数可传：章号是「这句引语落在哪一章」的产物（约束 10）。
                ledger.declare_knows(who=k.who, secret=secret.label, quote=k.quote)
                knows += 1
            for b in secret.believes:
                ledger.declare_believes(
                    who=b.who, secret=secret.label, believed_value=b.value, quote=b.quote
                )
                believes += 1

        gt = GroundTruth(
            project_id=pid,
            chapters=report.chapter_count,
            traps=[_derive(store, pid, bk, trap) for trap in bk.traps],
        )
    finally:
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(gt.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return BuildResult(
        project_id=pid,
        chapters=report.chapter_count,
        characters=len(bk.characters),
        secrets=len(bk.secrets),
        futures=len(bk.futures),
        tell_aliases=len(bk.secrets),
        knows_edges=knows,
        believes_edges=believes,
        traps=len(bk.traps),
    )


def _derive(store: SqliteStoryGraph, pid: str, bk: Booklet, trap: BookletTrap) -> GroundTruthTrap:
    """一条陷阱的禁忌集 —— **由引擎算，作者填不了**（协议 §4）。

    `ResolvedConstraints.of()` 在这里同时做两件事：收窄成「只有标签」的形状，
    以及把 cast 退化那两条路径炸掉（见 `build()` 的 Notes）。
    """
    ctx = ResolvedConstraints.of(scene_view(store, pid, trap.chapter, trap.cast), trap.cast)

    if trap.kind == "KNOWS":
        refs = [ref for ref in ctx.must_not_reveal if ref.name == trap.target]
        if not refs:
            raise BuildRefused(
                f"陷阱 {trap.id}：第 {trap.chapter} 章、在场 {trap.cast} 时，"
                f"「{trap.target}」**不在** must_not_reveal 里。\n"
                f"  引擎算出来的是 {ctx.secret_labels}。\n"
                "  判据是「在场的人里至少有一个 state != KNOWS」——在场的人此刻可能全都已经\n"
                "  知道它了（引语定的 valid_from 早于本章），那这条陷阱没有东西可漏。\n"
                "  要么把 chapter 往前挪，要么换一组 cast。"
            )
        # 一个 target 只对应一个节点（`Booklet` 的 validator 保证 label 唯一），所以这里
        # 取 refs[0]；`secret_surfaces` 的出参键是 node_id。
        tells = secret_surfaces(store, pid, refs[:1]).get(refs[0].id, [])
        if not tells:
            raise BuildRefused(
                f"陷阱 {trap.id}：「{trap.target}」一个可匹配的 tell 都没有，"
                "检测器永远找不到它，这条陷阱会安静地永远判「没泄漏」。\n"
                f"  期望：{[bk.secret_of(trap.target).tell]}（TOML 里声明的）；实际：[]。\n"
                "  最可能的原因是那个 tell 有歧义（同一个 surface 挂在多个节点上，\n"
                "  于是 usable_for_rules 为假），或者它被写成了 canonical 显示名——\n"
                "  而 secret_surfaces() 排除 canonical，那正是「不会自己命中自己」的来源。"
            )
    else:
        entities = [e for e in ctx.forbidden_entities if e.node.name == trap.target]
        if not entities:
            raise BuildRefused(
                f"陷阱 {trap.id}：第 {trap.chapter} 章时「{trap.target}」不在 "
                "forbidden_entities 里。\n"
                f"  引擎算出来的是 {ctx.forbidden_names}。\n"
                f"  判据是 first_appears_chapter > 本章，而它声明的是 "
                f"{bk.future_of(trap.target).first_appears}——本章已经到了或过了首现章，\n"
                "  提这个实体不再是泄漏。把 trap.chapter 往前挪，或把 first_appears 往后挪。"
            )
        tells = list(entities[0].surfaces)
        if not tells:
            raise BuildRefused(
                f"陷阱 {trap.id}：「{trap.target}」一个可匹配的 surface 都没有"
                "（它的称呼全都有歧义或太短），检测器匹配不了它。\n"
                f"  期望：{[trap.target]}；实际：[]。"
            )

    return GroundTruthTrap(
        id=trap.id,
        kind=trap.kind,
        chapter=trap.chapter,
        cast=list(trap.cast),
        target=trap.target,
        goal=trap.goal,
        prior=trap.prior,
        reference=trap.reference,
        must_not_reveal=ctx.secret_labels,
        forbidden=ctx.forbidden_names,
        tells=sorted(tells),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m synth.build`。跑完记得跑一次 `python -m synth.leak_selfcheck`——
    **协议 §4 的原话是「全绿才放行」**，build 成功不等于小册子合格。"""
    parser = argparse.ArgumentParser(description="合成小册子 → 库 + ground_truth.json")
    parser.add_argument("--booklet", type=Path, default=HERE / "booklet.toml")
    parser.add_argument("--prose", type=Path, default=HERE / "booklet.txt")
    parser.add_argument("--db", type=Path, default=HERE / "gate.db")
    parser.add_argument("--out", type=Path, default=HERE / "ground_truth.json")
    args = parser.parse_args(argv)

    result = build(booklet=args.booklet, prose=args.prose, db=args.db, out=args.out)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
