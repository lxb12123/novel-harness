"""跑 gate 之前的自检 —— **全绿才放行**（`docs/EVAL_PROTOCOL.md` §4 + 修正案 4）。

七条放行条件，**全是精确的字符串或结构比较，零语义判断**（ADR 0005 铁律）。空列表 = 放行。

| # | 条件 | 出处 | 方向 |
|---|------|------|------|
| 1 | tell 两两不互为子串 | §3「tell 全局唯一、两两不互为子串」 | 出现即错 |
| 2 | 每条陷阱的 7 个源字段一致；禁忌集非空、含 target、`tells` 等于声明值 | §4 | 对不上即错 |
| 3 | 未来 tell 在首现章之前处处不出现（正文 + prior + goal） | §4 / 修正案 4 裁定 C | 出现即错 |
| 4 | echo guard：tell 与 secret label 互不为子串 | §2「两个集合天然不相交」 | 出现即错 |
| 5 | 切章数 == `[book].chapters` | §4「12→12」 | 不等即错 |
| 6 | 所有 `goal` 不含任何 tell | 修正案 4 裁定 B | 出现即错 |
| 7 | 每条 KNOWS 陷阱的 `prior` **必须含** target 的 tell | 修正案 4 裁定 A | **缺了才错** |

── 第 7 条为什么方向和别的相反 ──────────────────────────────────────────

修正案 4 的整份文件在论证一件事：`prior` 若不含 tell，**X0 物理上够不着那个字符串**
（tell 是为这个实验生造的专名，不在任何模型的输出分布里），KNOWS 泄漏率恒 ≈ 0，
撞裁决表第一行的地板门 → INVALID，而**重造多少次陷阱都不会好**。
一个输出集合是 `{INVALID}` 单元素的 kill-gate 不构成对任何主张的检验。

所以这一条是「这条陷阱物理上可能咬住」的**唯一机器判据**。

**它罩不住的那一半，必须在这里写清楚**：它只能验 tell 这个字符串在不在 `prior` 里，
**验不了「目标角色是不是已经知道它」**——而裁定 A 的原话是「必须让 tell 以叙述层或
第三方视角出现，但不许出现任何表明本条陷阱的目标角色已经知道它的内容」。
后半句要回答「这段话是谁的视角、他听见了没有」，那是语义判断，ADR 0005 在 v1 里禁止
本仓库长出这种能力。**那一半只有 review 守得住**，以及实测：`prior` 把话喂到嘴边会让
X0 撞天花板门（§6 第 2 行，KNOWS 泄漏率 > 0.90 → INVALID）。协议早就为「陷阱强度」
这个旋钮规定了合法区间 [0.50, 0.90]，本文件验的是「旋钮接上了没有」，不是「拧到几度」。

── 刻意**不**验的两件事 ─────────────────────────────────────────────────

- **`reference` 不含 tell**：协议 §6 第 2 行把它设成**运行期**天花板门（「任一 reference
  完成被判泄漏 → INVALID」），而那道门比这里严——它用的是**整场**的禁忌集，能抓到
  「reference 没写 target 的 tell、却写了同场另一条秘密的 tell」。在这里先用一个更弱的
  判据把它挡掉，等于让那道门永远不响，却给人一种它在守着的错觉。
- **第 3 条与第 6 条在 `goal` 上重叠**（同一处会报两次）。留着：它们引的是两条不同的裁定
  （C 与 B），且 B 严格更宽（对所有 tell、不看章号）。去掉任何一条，另一条看起来就像是
  多余的，而下一个人会顺手把它删掉。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from novel_harness.importer import chapter_text
from novel_harness.text import Chapterization, chapterize

from .build import HERE, Booklet, GroundTruth, load_booklet

# 前缀叫 C 不叫 R：这个仓库里 `R2` / `R4` 已经是**规则**的名字（checks/），
# 而这七条是**放行条件**，跑在规则之外、也早于任何一条规则。撞名会让报错读起来像 R4 红了。
C1 = "[1 tell 互为子串]"
C2 = "[2 禁忌集 ≠ 目标边界]"
C3 = "[3 未来 tell 提前出现]"
C4 = "[4 echo guard]"
C5 = "[5 切章数]"
C6 = "[6 goal 含 tell]"
C7 = "[7 KNOWS 的 prior 缺 tell]"


def selfcheck(*, booklet: Path, prose: Path, ground_truth: Path) -> list[str]:
    """七条放行条件。**返回空列表 = 放行**，非空的每一条都说清了哪条陷阱、哪个 tell、
    期望什么、实际什么——一条只说「自检失败」的消息等于把问题原样丢回给人。

    Args:
        booklet: `booklet.toml`。走的是 `build.load_booklet`，**同一份 schema**
            （两份解析迟早有一份会把手滑的键放过去）。
        prose: `booklet.txt`，未切章的全文。
        ground_truth: `build()` 派生出来的 `ground_truth.json`。

    Notes:
        它读的是**磁盘上那三个文件**，不读库——于是「ground_truth.json 是旧的」这件事
        它抓得到（第 2 条会逐项比 trap id，以及 kind / chapter / cast / target / goal / prior /
        reference 七个源字段），
        而一个从库里现算一遍的自检反而抓不到：那样它验的是库，不是那份要拿去跑的 json。
    """
    bk = load_booklet(booklet)
    gt = GroundTruth.model_validate_json(ground_truth.read_text(encoding="utf-8"))
    book = chapterize(prose.read_text(encoding="utf-8-sig"))

    return [
        *_tells_never_nest(bk),
        *_boundaries_match_the_target(bk, gt),
        *_future_tells_stay_offstage(bk, book),
        *_echo_guard(bk),
        *_chapter_count_agrees(bk, gt, book),
        *_goals_carry_no_tell(bk),
        *_knows_priors_carry_the_tell(bk),
    ]


def _owner_of(bk: Booklet, tell: str) -> str:
    """这个 tell 是谁的。只用在错误消息里——人要能一眼看出该去改 TOML 的哪一行。

    **返回全部持有者，不是第一个。** 第 1 条要报的恰恰是「同一个 tell 被声明了两次」，
    只报第一个等于把那条消息里最有用的一半（另一处在哪）吞掉。
    """
    owners = [f"secret「{s.label}」" for s in bk.secrets if s.tell == tell]
    owners += [f"future「{f.name}」" for f in bk.futures if f.name == tell]
    return " + ".join(owners)


# ── 1 ─────────────────────────────────────────────────────────────────────


def _tells_never_nest(bk: Booklet) -> list[str]:
    """协议 §3：「tell 全局唯一、两两不互为子串」。

    互为子串的两个 tell 会让 `anchor.find_all` 在一段草稿上**同时**命中两条陷阱的禁忌集
    （`玄血蛊` ⊂ `玄血蛊毒`），于是一次泄漏被算成两次，而两条陷阱是统计的**独立单位**。
    n 没变，判别对数虚高，McNemar 的 p 值直接不成立。

    「完全相同的两个 tell」那一支通常在 `build()` 就已经炸了（同一个 surface 挂在两个节点上
    → 有歧义 → `usable_for_rules` 为假 → 派生出空集 → `BuildRefused`）。这里仍然留着它，
    因为两条消息说的不是一回事：那边说「派生出空集」，读起来像引擎坏了；这边说「你写重了」。
    """
    out: list[str] = []
    tells = bk.tells
    for i, a in enumerate(tells):
        for b in tells[i + 1 :]:
            if a == b:
                out.append(
                    f"{C1} tell「{a}」被声明了两次（{_owner_of(bk, a)}）。"
                    "期望：全局唯一；实际：两处共用。"
                    "同一个词命中时分不清是哪条陷阱漏的，两条陷阱不再是独立的分析单位"
                )
            elif a in b or b in a:
                inner, outer = (a, b) if a in b else (b, a)
                out.append(
                    f"{C1} tell「{inner}」（{_owner_of(bk, inner)}）是 tell"
                    f"「{outer}」（{_owner_of(bk, outer)}）的子串。"
                    "期望：两两不互为子串；实际：前者包含在后者里。"
                    "一段草稿写了后者会被算成两条陷阱同时泄漏，判别对数虚高"
                )
    return out


# ── 2 ─────────────────────────────────────────────────────────────────────


def _boundaries_match_the_target(bk: Booklet, gt: GroundTruth) -> list[str]:
    """每条陷阱的源字段一致、禁忌集非空且含 target，`tells` **等于**声明的那一个。

    `ground_truth.json` 不只是禁忌集：runner 真正吃的 `cast` / `goal` / `prior` / `reference`
    也从这里来。只比 id/kind/chapter/target 会放过一种旧文件：TOML 已经重造了 prompt，
    JSON 仍让模型跑旧 prompt，而自检照样全绿。所以七个源字段全部逐项精确比较。

    第三项是这条里最硬的一格：`tells` 是 `build()` 从**引擎**（`secret_surfaces()` /
    `ForbiddenEntity.surfaces`）派生的，而期望值是作者写在 TOML 里的。两者不等，说明
    派生链某一环错了——最常见的是 tell 被写成了 canonical 显示名（`secret_surfaces()`
    排除 canonical，于是派生出空集），或者 tell 有歧义（`usable_for_rules` 为假）。
    **这一格直接守着「检测集合 ⟂ prompt 集合」那条机械保证**：若派生结果里混进了显示名，
    实际值会多出一个词，这里当场红。
    """
    out: list[str] = []
    by_id = {t.id: t for t in gt.traps}
    declared = {t.id for t in bk.traps}

    for extra in sorted(set(by_id) - declared):
        out.append(
            f"{C2} ground_truth.json 里有陷阱 {extra}，booklet.toml 里没有。"
            "这份 ground truth 是旧的——重跑 build 再自检"
        )

    for trap in bk.traps:
        g = by_id.get(trap.id)
        if g is None:
            out.append(
                f"{C2} 陷阱 {trap.id} 在 booklet.toml 里，ground_truth.json 里没有。"
                "这份 ground truth 是旧的——重跑 build 再自检"
            )
            continue
        source_fields = ("kind", "chapter", "cast", "target", "goal", "prior", "reference")
        for field in source_fields:
            declared_value = getattr(trap, field)
            derived_value = getattr(g, field)
            if declared_value != derived_value:
                out.append(
                    f"{C2} 陷阱 {trap.id} 的源字段 {field} 两处对不上："
                    f"booklet.toml={declared_value!r}，"
                    f"ground_truth.json={derived_value!r}。"
                    "这份 ground truth 是旧的——重跑 build 再自检"
                )

        if (g.kind, g.chapter, g.target) != (trap.kind, trap.chapter, trap.target):
            continue

        if trap.kind == "KNOWS":
            derived, what = g.must_not_reveal, "must_not_reveal"
            expected = {bk.secret_of(trap.target).tell}
        else:
            derived, what = g.forbidden, "forbidden"
            expected = {bk.future_of(trap.target).name}

        if not derived:
            out.append(
                f"{C2} 陷阱 {trap.id} 的 {what} 是空的。"
                f"期望：至少含「{trap.target}」；实际：[]。"
                "禁忌集为空 = 这一场没有任何东西不许说 = 这条陷阱不可能咬住"
            )
        elif trap.target not in derived:
            out.append(
                f"{C2} 陷阱 {trap.id} 的 {what} 不含它的 target。"
                f"期望：含「{trap.target}」；实际：{derived}"
            )

        if set(g.tells) != expected:
            out.append(
                f"{C2} 陷阱 {trap.id} 的 tells 和 booklet.toml 声明的对不上。"
                f"期望：{sorted(expected)}；实际：{sorted(g.tells)}。"
                "ground truth 派生错了——最常见的是 tell 被写成了 canonical 显示名"
                "（secret_surfaces() 排除 canonical，派生出空集），"
                "或者那个 surface 有歧义（usable_for_rules 为假）"
            )
    return out


# ── 3 ─────────────────────────────────────────────────────────────────────


def _future_tells_stay_offstage(bk: Booklet, book: Chapterization) -> list[str]:
    """未来实体的 tell 在首现章之前**处处**不出现：正文、`prior`、`goal`。

    修正案 4 裁定 C 一字未改这一条，并说清了为什么它和 KNOWS 那一侧不对称：
    未来实体的 tell **自身就是实体名**，X1/X2 的 prompt 必然点它，所以那一侧本来就有
    echo 风险——协议因此让 `future_leak` 只作描述性地板。在一个不主导裁决的维度上
    再给 X0 加一条泄漏渠道，只会让描述性数字更难读。

    前言（第一个章标之前那段）按「第 0 章」算：它比任何一章都早，落在那儿的未来实体名
    同样会让 X0 在它该够不着的时候够得着。
    """
    out: list[str] = []
    numbered = [(0, "前言", book.preamble)] + [
        (ch.index, f"第 {ch.index} 章", chapter_text(ch.raw_heading, ch.body))
        for ch in book.chapters
    ]
    for future in bk.futures:
        for index, where, text in numbered:
            if index < future.first_appears and future.name in text:
                out.append(
                    f"{C3} 未来实体「{future.name}」出现在{where}，"
                    f"而它声明的首现章是第 {future.first_appears} 章。"
                    f"期望：第 {future.first_appears} 章之前正文里一次都不出现；实际：出现了"
                )
        for trap in bk.traps:
            if trap.chapter >= future.first_appears:
                continue
            for field, text in (("prior", trap.prior), ("goal", trap.goal)):
                if future.name in text:
                    out.append(
                        f"{C3} 陷阱 {trap.id}（第 {trap.chapter} 章）的 {field} 里出现了"
                        f"未来实体「{future.name}」，而它的首现章是第 "
                        f"{future.first_appears} 章。"
                        "期望：不出现；实际：出现了。"
                        "（裁定 C：FUTURE 这一侧禁令照旧从严，不适用 KNOWS 的裁定 A）"
                    )
    return out


# ── 4 ─────────────────────────────────────────────────────────────────────


def _echo_guard(bk: Booklet) -> list[str]:
    """tell 与 secret **显示名**互不为子串（两个方向都查）。

    协议 §2 的原话：「prompt 里出现的是 `血脉秘密`，永远不是它的 tell `玄血蛊`。
    于是『检测器命中的 tell』与『prompt 里出现的标签』两个集合天然不相交——
    不会自己命中自己（echo-FP）。」

    `secret_surfaces()` 排除 canonical 保证了两个集合**不相等**，但保证不了**不相交**：
    显示名叫「玄血蛊之秘」而 tell 叫「玄血蛊」时，X1/X2 的 prompt 印出显示名，
    检测器在那句话里精确命中 tell —— 两臂 100% 自己命中自己，Δ 翻负，裁决表读出 KILL。
    **这一条是那个失败故事唯一的机器判据。**

    比较的对象只有 secret 的显示名：未来实体的名字**自身就是** tell，拿它和自己比
    必然相等，那不是发现问题，那是构造一条永远红的规则。
    """
    out: list[str] = []
    labels = [s.label for s in bk.secrets]
    for tell in bk.tells:
        for label in labels:
            if tell == label:
                out.append(
                    f"{C4} tell「{tell}」和 secret 显示名「{label}」是同一个词。"
                    "显示名进 prompt、tell 进检测器，两个集合当场相交 → X1/X2 命中自己"
                    "写进去的词 → Δ 翻负 → 裁决表读出 KILL（把对的项目砍掉）"
                )
            elif tell in label or label in tell:
                out.append(
                    f"{C4} tell「{tell}」（{_owner_of(bk, tell)}）与 secret 显示名"
                    f"「{label}」互为子串。期望：两者不相交；实际：其一包含另一个。"
                    "显示名会被印进 X1/X2 的 prompt，而检测器在 prompt 抄进草稿的那句话上"
                    "精确命中 tell —— 这就是 echo 假阳性"
                )
    return out


# ── 5 ─────────────────────────────────────────────────────────────────────


def _chapter_count_agrees(bk: Booklet, gt: GroundTruth, book: Chapterization) -> list[str]:
    """切章数 == `[book].chapters` == `ground_truth.chapters`。

    协议 §4 写的是「12→12」，那个 12 是**真小册子**的目录数；这里比的是 TOML 里的那个数，
    否则任何一份小号 fixture 都会被这条判死，而它验的其实是「切章器认得出这本书的章标」。

    `build()` 在导入之后已经验过一次同样的等式——这里再验一次不是重复：**那次验的是当时，
    这次验的是现在**。`booklet.txt` 在 build 之后被改过（加了一章、改了章标写法）
    是完全可能的，而那时 `ground_truth.json` 里每一条 `chapter` 都指着另一章。
    """
    cut = len(book.chapters)
    if cut != bk.book.chapters or gt.chapters != bk.book.chapters:
        return [
            f"{C5} 章数对不上：booklet.txt 现在切出 {cut} 章，"
            f"[book].chapters 写的是 {bk.book.chapters}，"
            f"ground_truth.json 记的是 {gt.chapters}。期望三者相等。"
            "（切出来少一章往往是第一章的章标没被认出来——那时全书 index 集体少 1，"
            "每一条 valid_from 都错一章，而面板会理直气壮地画出来）"
        ]
    return []


# ── 6 ─────────────────────────────────────────────────────────────────────


def _goals_carry_no_tell(bk: Booklet) -> list[str]:
    """所有 `goal` 不含任何 tell（修正案 4 裁定 B）。

    `goal` 含 tell 会**强迫三臂都写它**：X0 会写，X1/X2 也会写，Δ 被压到 0。
    方向和裁定 A 相反，但一样让实验失效——而且更隐蔽，因为三臂同时被污染，
    `confound_lint` 也抓不到（它比的是 X1 vs X2，不是 base）。
    ADR 0010「问题：守卫为什么在这一格失效」那一节点名要求把它落成这里的放行条件。
    """
    return [
        f"{C6} 陷阱 {trap.id} 的 goal 含 tell「{tell}」（{_owner_of(bk, tell)}）。"
        "期望：goal 里一个 tell 都没有；实际：有。"
        "goal 含 tell 会强迫三臂都写它，Δ 被压到 0，而 confound_lint 抓不到"
        f"（它比的是 X1 vs X2，不是 base）。goal 原文：{trap.goal}"
        for trap in bk.traps
        for tell in bk.tells
        if tell in trap.goal
    ]


# ── 7 ─────────────────────────────────────────────────────────────────────


def _knows_priors_carry_the_tell(bk: Booklet) -> list[str]:
    """**缺了才报错**：每条 KNOWS 陷阱的 `prior` 必须含它 target 的 tell（裁定 A）。

    方向和上面六条都相反，理由见模块 docstring。它罩不住「目标角色是不是已经知道」——
    那要语义判断（ADR 0005 禁止），只有 review 守得住。
    """
    out: list[str] = []
    for trap in bk.traps:
        if trap.kind != "KNOWS":
            continue
        tell = bk.secret_of(trap.target).tell
        if tell not in trap.prior:
            out.append(
                f"{C7} 陷阱 {trap.id} 的 prior 里没有 target「{trap.target}」的 tell"
                f"「{tell}」。期望：出现（叙述层或第三方视角）；实际：一次都没有。\n"
                "  X0 的 prompt 只有 house-style + prior + goal，而 tell 是为这个实验生造的"
                "专名——prior 不含它，X0 物理上够不着那个字符串，KNOWS 泄漏率恒 ≈ 0，"
                "撞地板门恒判 INVALID，重造多少次陷阱都不会好（修正案 4）。\n"
                f"  prior 原文：{trap.prior}"
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m synth.leak_selfcheck`（**不是** `python synth/leak_selfcheck.py`——
    它和 `build.py` 共用一份 schema，相对 import 需要包上下文）。放行返回 0，否则 1。"""
    parser = argparse.ArgumentParser(description="跑 gate 之前的小册子自检；全绿才放行")
    parser.add_argument("--booklet", type=Path, default=HERE / "booklet.toml")
    parser.add_argument("--prose", type=Path, default=HERE / "booklet.txt")
    parser.add_argument("--ground-truth", type=Path, default=HERE / "ground_truth.json")
    args = parser.parse_args(argv)

    problems = selfcheck(
        booklet=args.booklet, prose=args.prose, ground_truth=args.ground_truth
    )
    if not problems:
        print("自检全绿，放行。")
        return 0
    print(f"自检不通过，{len(problems)} 条：")
    for line in problems:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
