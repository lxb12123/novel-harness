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

**秘密下线之后（ADR 0039）那条论证整个不成立了**：`SceneConstraints` 今天只剩
`forbidden_entities`，而它**按章号算、与 cast 无关**——空 cast 不再让任何东西退化。
`UnknownCastConstraints` **因此不再是一个安全类型**。

**但两个类型仍然留着**，理由换成了一条更朴素、也仍然真实的：

> 一份**你没有**的在场名单，不许拿空列表冒充着发给 Writer。

`assemble()` 拿到 `UnknownCastConstraints` 时**整个不发【在场】块**（2026-08-22 M1-a），
而不是发一个「在场：」后面空着的块。行内续写（ADR 0015 D4）真的不知道这一场有谁——
「谁在场」是被那一段写出来的结果，不是前提。这件事今天由类型保证，仍然比留给纪律好。

**改这一层之前先想清楚你在守什么**：守的不再是泄漏，是「别对模型说一句你不知道的话」。
这两条的严格程度不一样，取舍也不一样。

── 三、PLANNED 只转译不透传（约束 4）────────────────────────────────────

出参里没有一个字段装得下 PLANNED 边的内容：未来实体是 `NodeRef`（id/label/name，
**没有 props**）外加一个首现章号。这不是本文件的功劳，是 `panel/constraints.py`
和 `graph.models.NodeRef` 的——本文件的责任是**不要在收窄的路上把它加回来**。

**这一节没有跟着上一节一起失效**：它防的是「未来剧情泄漏」，而未来实体还在。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..graph import NodeRef, StoryGraph
from ..panel.constraints import (
    ForbiddenEntity,
    SceneView,
    UnresolvedCast,
    scene_view,
)


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

    forbidden_entities: list[ForbiddenEntity] = Field(default_factory=list)
    """首现章号在本章之后的实体，按首现章号升序。完整 PLANNED 到此为止，只剩名字和章号。"""

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
            forbidden_entities=list(constraints.forbidden_entities),
            characters=list(view.characters),
        )

    @property
    def forbidden_names(self) -> list[str]:
        """未来实体的名字（进 prompt 的禁写清单）。"""
        return [e.node.name for e in self.forbidden_entities]


class UnknownCastConstraints(BaseModel):
    """**不知道在场是谁**时的约束集 —— fail-closed 的退化值，而且它明说自己是。

    行内续写用它（[ADR 0015](../../../docs/adr/0015-inline-continuation-is-a-short-draft.md) D4）：
    作者边写边要提示，此刻「谁在场」还没有答案——**它是被这一段写出来的结果，不是前提**。
    无名配角（「掌柜的」「一个小厮」）更是永远进不了花名册。

    ── 为什么是第二个类型，而不是把 `ResolvedConstraints.cast` 的 `min_length=1` 放宽 ──

    **⚠️ 这条理由 2026-08-25 换过一次，见模块 docstring 第二节。** 今天的理由是：
    两个类型 ⇒ `assemble()` **知道自己拿的是哪一种**，于是不知道在场时**整个不发
    【在场】块**（2026-08-22 M1-a——「未知」两个字不带信息），而不是拿一份空 cast
    伪装成精确清单。一句你不知道的话，不发比发一个空壳好。

    **没有 `cast`**：不知道就是不知道，不许用空列表冒充「这一场没有人」。
    """

    model_config = ConfigDict(frozen=True)

    chapter: int

    forbidden_entities: list[ForbiddenEntity] = Field(default_factory=list)
    """首现章号在本章之后的实体。**这一项本来就与 cast 无关**（按章号算），所以退化态里它是精确的。"""

    @property
    def forbidden_names(self) -> list[str]:
        return [e.node.name for e in self.forbidden_entities]


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

    空 cast 喂给 `scene_view()` 得到的正是 `resolved.complete == False` 那一支。
    今天这一支和精确值算出来的 `forbidden_entities` 是一样的（它按章号算，与 cast 无关），
    **区别全在类型上**：拿到本类型的下游知道自己不知道在场是谁。
    """
    constraints = scene_view(store, project_id, chapter, ()).constraints
    return UnknownCastConstraints(
        chapter=constraints.chapter,
        forbidden_entities=list(constraints.forbidden_entities),
    )


def resolve_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
) -> ResolvedConstraints:
    """算一次 + 收窄一次。**产品起草的默认入口**（`cast` 是称呼原文，同 `scene_constraints`）。

    它比 `of()` 少一个出错的方式：`constraints` 和 `cast` 没有配错的余地。
    """
    return ResolvedConstraints.of(scene_view(store, project_id, chapter, cast), cast)
