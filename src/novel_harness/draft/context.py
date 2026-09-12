"""为一个场景取出合法约束集 —— **把「cast 已解析」编码进类型**（ARCHITECTURE §10.5 第 2 条）。

`draft/` 的入口，薄到只有一个类型 + 一个类方法 + 一个便利函数。它存在的理由全在那个类型上。

── 一、把「必须记得调」变成「不调就拿不到那个类型」──────────────────────

`SceneConstraints.require_resolved_cast()` 是这一层最弱的一环，ARCHITECTURE §10.5 点名说过：
「M2 写 `draft/` 时若忘了调，fail-open 就回来了」，并指定 **M2 的第一个任务是把这个守卫变成
类型层强制**——「让 prompt 拼装只接受一个 `ResolvedConstraints` 类型，而它只能由
`require_resolved_cast()` 产出」。这个文件就是那一句话的实现。

于是 `assemble()` 的签名收 `ResolvedConstraints` 而不是 `SceneConstraints`，而
「这份约束是不是退化值」在下游**不必再问一次**——想问也没有字段可问：`unresolved_cast`
在这个类型上根本不存在，退化态在类型里表示不出来。**这就是「薄封装」的价值：它不是多一层
转发，它是把一条运行期纪律变成一个类型**（拿到对象 = 检查已经做过，且只可能做过一次）。

── ⚠️ 二、这两个类型的**理由 2026-08-25 换了**，别照着旧的读 ────────────

这一段原来写的是：空 cast 会让 `scene_constraints` 退化成「`must_not_reveal` = 全部
秘密」，而那是 fail-closed 的退化值，所以必须用两个类型把它和精确值分开。

**秘密下线之后（ADR 0039）那条论证整个不成立了**，`forbidden_entities` 又在
2026-08-31 删掉了（见下面第三节）——`SceneConstraints` 今天只剩 `unresolved_cast`，
而它本来就只由 cast 是否解析成功决定，不存在「按章号算、与 cast 无关」那种恒定输出。
`UnknownCastConstraints` **因此不再是一个安全类型**。

**但两个类型仍然留着**，理由换成了一条更朴素、也仍然真实的：

> 一份**你没有**的在场名单，不许拿空列表冒充着发给 Writer。

`assemble()` 拿到 `UnknownCastConstraints` 时**整个不发【在场】块**（2026-08-22 M1-a），
而不是发一个「在场：」后面空着的块。行内续写（ADR 0015 D4）真的不知道这一场有谁——
「谁在场」是被那一段写出来的结果，不是前提。这件事今天由类型保证，仍然比留给纪律好。

**改这一层之前先想清楚你在守什么**：守的不再是泄漏，是「别对模型说一句你不知道的话」。
这两条的严格程度不一样，取舍也不一样。

── 三、`forbidden_entities` 2026-08-31 删了 ───────────────────────────────

这两个类型原来还各带一个 `forbidden_entities: list[ForbiddenEntity]` 字段（未登场
实体的禁写清单），是 PLANNED 转译进 prompt 的落点。**维护者裁定这条价值不在了，
连同它在 Mode 2 两个工具（`scene_constraints`/`book_index`）和右栏面板里的读取
一起删**，见 [ADR 0041](../../../docs/adr/0041-forbidden-entities-cut.md)。
`PLANNED` 边本身仍然没有读路径（`QUERYABLE_SCOPES`），这条防线没有跟着松动——
删掉的只是一个曾经存在、现在被认定不再需要的转译出口。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..graph import NodeRef, StoryGraph
from ..panel.constraints import SceneView, UnresolvedCast, scene_view


class ResolvedConstraints(BaseModel):
    """一个场景的约束集，**且保证它不是 fail-closed 的退化值**。

    与 `SceneConstraints` 的差别只有一处，而那一处就是全部意义：**它没有 `unresolved_cast`**。
    这个类型的实例存在，就等价于「作者声明的每一个称呼都解析成了唯一角色，且至少有一个人」。

    **唯一被认可的构造路径是 `of()` / `resolve_constraints()`。** Python 拦不住有人裸调
    `ResolvedConstraints(...)`，但那么做的人是在**手写**一份没人检查过的在场名单——
    这跟绕过 `panel/constraints.py` 自己拼一份是同一类事，只有 review 拦得住。
    """

    model_config = ConfigDict(frozen=True)

    chapter: int

    cast: list[str] = Field(min_length=1)
    """作者写在场景块里的称呼原文，**全部解析成功**。顺序 = 他写的顺序。

    留原文而不是 node_id：进 prompt 的该是作者自己的叫法（「在场：萧决、顾清音」），
    node_id 是 ULID，对 Writer 和作者都不是信息。

    `min_length=1` 不是防御性编程，是模块 docstring 第二节那条「另一条退化路径」的落点：
    空 cast 会安静地穿过 `require_resolved_cast()`。
    """

    characters: list[NodeRef] = Field(default_factory=list)
    """`cast` 里解析出来的那几个节点（**窄引用，没有 props**）。

    起草侧要它把「作者在在场里写了个地点名」挡掉，而它拿不到 `resolve_cast`
    （第 4 道 arch-guard 的 `WRITER_BANNED`）——所以由 `panel` 那一层交出来。
    """

    @classmethod
    def of(
        cls,
        view: SceneView,
        cast: Sequence[str],
    ) -> ResolvedConstraints:
        """收窄一份**已经算好的** `SceneView`。歧义 / 空 cast 在这里弹给作者。

        `cast` 必须是从**本章**正文里数出来的。**没有「从别处借一份顶上」这条路**
        （2026-08-23 撤销 M2-b，见 ADR 0037 补记）：另一章的名单不是本章名单的超集，
        顶替的结果是对模型说了一句「这一场有这些人」，而那句话是假的。数不出人时调用方
        退回 `unknown_cast_constraints`（那时 `assemble` 干脆不提在场），不构造这个类型。

        Args:
            view: `panel.scene_view()` 的出参（约束 + 算它用的那份在场名单）。
            cast: 算它时用的**同一份**称呼原文。`SceneConstraints` 记不住解析成功的那些称呼
                （它只留 `unresolved_cast`），所以「作者到底声明了几个人」这个信息只能从
                这里进来。也正因如此这两个参数必须成对：
                `unresolved_cast` 为空 **且** `cast` 非空 ⟺ `ResolvedCast.complete`。
                不想操心配对就用 `resolve_constraints()`，它没有配错的余地。

        Raises:
            UnresolvedCast: 有称呼解析不出唯一角色，**或者作者根本没写 cast**。
        """
        constraints = view.constraints
        constraints.require_resolved_cast()
        if not list(cast):
            # 这一条 `require_resolved_cast()` 看不见（它只读 unresolved_cast）。
            raise UnresolvedCast(
                "unresolved_cast_no_cast_declared", chapter=constraints.chapter
            )
        return cls(
            chapter=constraints.chapter,
            cast=list(cast),
            characters=list(view.characters),
        )


class UnknownCastConstraints(BaseModel):
    """**不知道在场是谁**时的约束集 —— fail-closed 的退化值，而且它明说自己是。

    行内续写用它（[ADR 0015](../../../docs/adr/0015-inline-continuation-is-a-short-draft.md) D4）：
    作者边写边要提示，此刻「谁在场」还没有答案——**它是被这一段写出来的结果，不是前提**。
    无名配角（「掌柜的」「一个小厮」）更是永远进不了角色册。

    ── 为什么是第二个类型，而不是把 `ResolvedConstraints.cast` 的 `min_length=1` 放宽 ──

    **⚠️ 这条理由 2026-08-25 换过一次，见模块 docstring 第二节。** 今天的理由是：
    两个类型 ⇒ `assemble()` **知道自己拿的是哪一种**，于是不知道在场时**整个不发
    【在场】块**（2026-08-22 M1-a——「未知」两个字不带信息），而不是拿一份空 cast
    伪装成精确清单。一句你不知道的话，不发比发一个空壳好。

    **没有 `cast`**：不知道就是不知道，不许用空列表冒充「这一场没有人」。
    """

    model_config = ConfigDict(frozen=True)

    chapter: int


DraftContext = ResolvedConstraints | UnknownCastConstraints
"""`assemble()` 收的两种约束集。**类型本身就是「这份约束退化了没有」的答案。**"""


def unknown_cast_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
) -> UnknownCastConstraints:
    """算出「不知道谁在场」时的全禁约束（ADR 0015 D4）。

    **它故意不调 `require_resolved_cast()`。** 那道守卫的作用是拦住「起草侧拿到退化值
    却以为拿到了精确值」；这里的调用方**明确要的就是退化值**，而它拿到的类型也明说了
    这一点，所以守卫在这条路径上没有对象可守。
    """
    constraints = scene_view(store, project_id, chapter, ()).constraints
    return UnknownCastConstraints(chapter=constraints.chapter)


def resolve_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
) -> ResolvedConstraints:
    """算一次 + 收窄一次。**产品起草的默认入口**（`cast` 是称呼原文，同 `scene_view`）。

    它比 `of()` 少一个出错的方式：`constraints` 和 `cast` 没有配错的余地。
    """
    return ResolvedConstraints.of(scene_view(store, project_id, chapter, cast), cast)


# 这一章已经有正文时，写手拿它怎么办——**由助手按作者这一次的话定**（`DraftAsk.intent`），
# 不写死在提示词里（维护者 2026-09-12：「每次都说要重新写，这就是写死了……有些时候可能就是
# 让他去改这篇里面的语句」）：作者说「重写」是一回事，说「把第三段改一下」是另一回事，两种话
# 写手拿到的现有正文是同一份，差的只是那一段前面怎么交代它（`product_draft._TARGET_CHAPTER_ASK`）。
# docstring 会进工具 schema 给模型看（英文书要有译文，`prompt_terms._EN`），所以它只有一句。
class DraftIntent(StrEnum):
    """这一章已经有正文时拿它怎么办：整章重写（rewrite），还是在它的基础上修改（revise）。"""

    REWRITE = "rewrite"
    REVISE = "revise"


class TargetChapterSnapshot(BaseModel):
    """一次起草唯一的目标章快照。所有下游共用同一个对象：写手看的正文、候选的 `base_sha256`
    都从它来，不许各自重读磁盘（2026-09-12 从校准包搬来，那个包随 ADR 0047 删了）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    text: str
    sha256: str = Field(min_length=64, max_length=64)
