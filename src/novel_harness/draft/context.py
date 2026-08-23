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

── 二、它比 `require_resolved_cast()` 多堵一个洞：空 cast ────────────────

`scene_constraints` 退化成「全部秘密」有**两条**触发路径（`ResolvedCast.complete` 是
`not unresolved and bool(ids)`）：① 有称呼解析不出唯一节点（「师兄」→ 8 个人）；
② 作者根本没写 `cast=`——`Scene.cast` 的默认值就是 `[]`。
而 `require_resolved_cast()` **只看 ①**（它只读 `unresolved_cast`）。第 ② 条能安静地穿过
那道断言：`SceneConstraints` 出参上「一个人都没有」和「这一场的人全都知道」不可区分，
两者的 `unresolved_cast` 都是空。拿着它去起草，Writer 收到的是「全部秘密都不许提」；
拿着它去跑 kill-gate，等于给注入臂换了一份更严的卷子，臂间比较当场失效
（同 `eval/leak.py::score_draft` 那句「把『全禁』当基线」）。

**两条路径这里都堵**：① 由 `require_resolved_cast()` 抛，② 由 `cast` 的 `min_length=1` 挡，
两者对作者是同一个信号，所以出口也是同一个异常（`UnresolvedCast`）。

── 三、只有标签，永不 tell（EVAL_PROTOCOL §2 / 第 4 道 arch-guard）───────

进 prompt 的是秘密的**显示名**（`血脉秘密`），永远不是它的内容 tell（`玄血蛊`）。
tell 只经 `panel.constraints` 的那个助手取，而 `tests/test_draft_boundary.py` 禁止 `draft/`
引用它——理由不是洁癖：tell 一旦进了 X1/X2 的 prompt，两臂 100% 命中自己写进去的词，
`Δ` 翻负，预注册的裁决表读出「KILL 起草线」，**把一个本来对的项目砍掉，而全程没有东西会红**。

**但这条不对称，且必须说清楚**：`forbidden_entities` 的名字**自身就是 tell**
（`血枭盟` 既是显示名也是检测词，EVAL_PROTOCOL §3），X1/X2 必然点它的名。协议因此让
`future_leak` 只作**描述性地板、不主导裁决**。别把 KNOWS 那一侧「标签 ⟂ tell」的直觉
搬到 FUTURE 这一侧来用。

── 四、PLANNED 只转译不透传（约束 4）────────────────────────────────────

出参里没有一个字段装得下 PLANNED 边的内容：秘密和未来实体都是 `NodeRef`（id/label/name，
**没有 props**），未来实体额外只带一个首现章号。这不是本文件的功劳，是 `panel/constraints.py`
和 `graph.models.NodeRef` 的——本文件的责任是**不要在收窄的路上把它加回来**。

── 五、认知矩阵也绑在这个类型上（反混淆铁律的类型化）────────────────────

EVAL_PROTOCOL §2 的 X1 要注入「认知矩阵要点」，且**反混淆铁律**要求 X1/X2 从**同一个**
`knowledge_matrix` 对象渲染，除「清单 vs 散文」外不许有第二处差异。

矩阵本来就是 `must_not_reveal` 的中间结果，只是 `scene_constraints()` 算完就丢了。
现在 `panel.scene_view()` 把它一并交出来，`ResolvedConstraints` 带着它——于是
`assemble(ctx, form=X1)` 与 `assemble(ctx, form=X2)` 拿的**必然**是同一份，
铁律从「runner 记得只算一次」变成类型保证。这跟第一节用类型编码「cast 已解析」是同一招：
**能由类型保证的事，不要留给纪律。**

（`draft/` 也确实自己算不了矩阵：它要 node_id，而 `resolve_cast` 在第 4 道 arch-guard 的
`WRITER_BANNED` 里。所以这条路线同时消掉了「第二份会漂移的真相」这个隐患。）
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..graph import KnowledgeMatrix, NodeRef, StoryGraph
from ..panel.constraints import (
    ForbiddenEntity,
    SceneView,
    UnresolvedCast,
    scene_view,
)


class ResolvedConstraints(BaseModel):
    """一个场景的约束集，**且保证它不是 fail-closed 的退化值**。

    与 `SceneConstraints` 的差别只有一处，而那一处就是全部意义：**它没有 `unresolved_cast`**。
    这个类型的实例存在，就等价于「作者声明的每一个称呼都解析成了唯一角色，且至少有一个人」，
    也就等价于「`must_not_reveal` 是算出来的答案，不是『全部秘密』这个退化值」。

    **唯一被认可的构造路径是 `of()` / `resolve_constraints()`。** Python 拦不住有人裸调
    `ResolvedConstraints(...)`，但那么做的人是在**手写**一份没人检查过的约束——这跟绕过
    `panel/constraints.py` 自己拼一套禁忌集是同一类事，只有 review 拦得住。
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

    must_not_reveal: list[NodeRef] = Field(default_factory=list)
    """在场的人里至少有一个还不知道（或持错误认知）的秘密。**窄引用，没有 props。**

    传一个完整的 `Node` 进来会被 pydantic 当场拒——那是故意的（ARCHITECTURE §10.5 第 3 条）：
    `NodeProps` 是 `extra="allow"`，秘密节点上的 `twist` 会顺着序列化进 prompt，
    **保密清单自己泄密**。
    """

    forbidden_entities: list[ForbiddenEntity] = Field(default_factory=list)
    """首现章号在本章之后的实体，按首现章号升序。完整 PLANNED 到此为止，只剩名字和章号。"""

    matrix: KnowledgeMatrix
    """**算 `must_not_reveal` 用的就是这一份**（`panel.scene_view()` 交出来的）。

    它在这里而不是当 `assemble()` 的参数，为的是 EVAL_PROTOCOL §2 的**反混淆铁律**：
    X1 与 X2 必须从**同一个** `knowledge_matrix` 对象渲染，除「清单 vs 散文」外
    不许有第二处差异。矩阵绑在这个 frozen 对象上，`assemble(ctx, form=X1)` 与
    `assemble(ctx, form=X2)` 拿的**必然**是同一份——铁律从 runner 纪律变成类型保证，
    和这个类型用 `cast` 编码「已解析」是同一招。

    副产物：`draft/` 不必自己算矩阵（它也算不了——矩阵要 node_id，而 `resolve_cast`
    在第 4 道 arch-guard 的 `WRITER_BANNED` 里），于是不存在第二份会漂移的真相。
    """

    @classmethod
    def of(cls, view: SceneView, cast: Sequence[str]) -> ResolvedConstraints:
        """收窄一份**已经算好的** `SceneView`。歧义 / 空 cast 在这里弹给作者。

        Args:
            view: `panel.scene_view()` 的出参（约束 + 算它用的那份矩阵）。
            cast: 算它时用的**同一份**称呼原文。`SceneConstraints` 记不住解析成功的那些称呼
                （它只留 `unresolved_cast`），所以「作者到底声明了几个人」这个信息只能从
                这里进来。也正因如此这两个参数必须成对：
                `unresolved_cast` 为空 **且** `cast` 非空 ⟺ `ResolvedCast.complete`
                ⟺ `must_not_reveal` 是算出来的而不是退化值。
                不想操心配对就用 `resolve_constraints()`，它没有配错的余地。

        Raises:
            UnresolvedCast: 有称呼解析不出唯一角色，**或者作者根本没写 cast**。
        """
        constraints = view.constraints
        constraints.require_resolved_cast()
        if not list(cast):
            # 这一条 `require_resolved_cast()` 看不见（它只读 unresolved_cast），
            # 但它导致的退化和歧义那条一模一样：must_not_reveal = 全部秘密。
            raise UnresolvedCast(
                f"第 {constraints.chapter} 章的这一场没有声明在场角色（`cast=`）。"
                "没有在场的人就没有「谁还不知道什么」，约束会退化成「全部秘密都不许提」——"
                "那既不是这一场的答案，也让 Writer 写不出东西。请在场景块里写明这一场有谁"
            )
        return cls(
            chapter=constraints.chapter,
            cast=list(cast),
            must_not_reveal=list(constraints.must_not_reveal),
            forbidden_entities=list(constraints.forbidden_entities),
            matrix=view.matrix,
        )

    @property
    def secret_labels(self) -> list[str]:
        """进 prompt 的秘密**显示名**（`血脉秘密`），**永远不是内容 tell（`玄血蛊`）**。

        两个集合天然不相交（tell 那一侧排除 canonical 别名），这是 KNOWS 维度不会自己命中
        自己的机械保证。**这个 property 存在就是为了让「拼 prompt」这件事没有第二种写法**——
        没有它，某天会有人为了「让模型知道得更全」去找一个更具体的字符串。
        """
        return [s.name for s in self.must_not_reveal]

    @property
    def forbidden_names(self) -> list[str]:
        """未来实体的名字。**这一侧没有上面那条不相交性质**（模块 docstring 第三节）。

        `血枭盟` 自身即 tell，X1/X2 必然点它的名 → echo 风险 → 协议让 `future_leak`
        只作描述性地板、不主导裁决。
        """
        return [e.node.name for e in self.forbidden_entities]


class UnknownCastConstraints(BaseModel):
    """**不知道在场是谁**时的约束集 —— fail-closed 的退化值，而且它明说自己是。

    行内续写用它（[ADR 0015](../../../docs/adr/0015-inline-continuation-is-a-short-draft.md) D4）：
    作者边写边要提示，此刻「谁在场」还没有答案——**它是被这一段写出来的结果，不是前提**。
    无名配角（「掌柜的」「一个小厮」）更是永远进不了花名册。

    ── 为什么是第二个类型，而不是把 `ResolvedConstraints.cast` 的 `min_length=1` 放宽 ──

    那个类型存在的**全部意义**就是「`must_not_reveal` 是算出来的答案，不是退化值」。
    一旦空 cast 能穿过去，`panel/constraints.py` 记的那次病史（人静默消失 → 秘密从清单里
    消失 → Writer 收到「无需保密」）立刻能重演，**而且没有任何东西会红**。

    两个类型 ⇒ `assemble()` **知道自己拿的是哪一种**：退化态**整个不发【在场】块**
    （2026-08-22 M1-a——「未知」两个字不带信息，而它后半句和禁写清单是同一句话），
    而不是拿一份空 cast 伪装成精确清单；也 ⇒ **kill-gate 永远拿不到本类型**
    （它走 `resolve_constraints()`），臂间比较不会被「更严的卷子」污染
    （模块 docstring 第二节点名的那种失效）。

    **没有 `cast`**：不知道就是不知道，不许用空列表冒充「这一场没有人」。
    **没有 `matrix`**：矩阵的行就是 cast，没有 cast 就没有行——给一个空矩阵只会让
    下游以为「查过了，确实没人知道任何事」。
    """

    model_config = ConfigDict(frozen=True)

    chapter: int

    must_not_reveal: list[NodeRef] = Field(default_factory=list)
    """**全书尚未被所有人知道的秘密**（`scene_constraints` 空 cast 时的退化值）。窄引用，无 props。"""

    forbidden_entities: list[ForbiddenEntity] = Field(default_factory=list)
    """首现章号在本章之后的实体。**这一项本来就与 cast 无关**（按章号算），所以退化态里它是精确的。"""

    @property
    def secret_labels(self) -> list[str]:
        """同 `ResolvedConstraints.secret_labels`：显示名，**永不是内容 tell**。"""
        return [s.name for s in self.must_not_reveal]

    @property
    def forbidden_names(self) -> list[str]:
        return [e.node.name for e in self.forbidden_entities]


DraftContext = ResolvedConstraints | UnknownCastConstraints
"""`assemble()` 收的两种约束集。**类型本身就是「这份约束退化了没有」的答案。**"""


def unknown_cast_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    *,
    secrets: Sequence[str] | None = None,
) -> UnknownCastConstraints:
    """算出「不知道谁在场」时的全禁约束（ADR 0015 D4）。

    **它故意不调 `require_resolved_cast()`。** 那道守卫的作用是拦住「起草侧拿到退化值
    却以为拿到了精确值」；这里的调用方**明确要的就是退化值**，而它拿到的类型也明说了
    这一点，所以守卫在这条路径上没有对象可守。

    空 cast 喂给 `scene_view()` 得到的正是 `resolved.complete == False` 那一支：
    `must_not_reveal` = 全部秘密（`panel/constraints.py`：「算不准就多禁」）。
    """
    view = scene_view(store, project_id, chapter, (), secrets=secrets)
    constraints = view.constraints
    return UnknownCastConstraints(
        chapter=constraints.chapter,
        must_not_reveal=list(constraints.must_not_reveal),
        forbidden_entities=list(constraints.forbidden_entities),
    )


def resolve_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
    *,
    secrets: Sequence[str] | None = None,
) -> ResolvedConstraints:
    """算一次 + 收窄一次。**产品起草的默认入口**（`cast` 是称呼原文，同 `scene_constraints`）。

    它比 `of()` 少一个出错的方式：`constraints` 和 `cast` 没有配错的余地。

    Notes:
        **kill-gate 的 runner 不该走这条。** 它要拿**同一份** `SceneConstraints` 既喂
        `eval.leak.score_against`（判分）又喂 prompt（起草），所以它该自己
        `scene_view()` 一次，把 `.constraints` 喂判分器、把整个 view 喂 `of()`——两侧共用一个
        对象，EVAL_PROTOCOL §3 的「禁忌集只有一个来源」在对象层就成立，
        而不是靠「两次查询之间图没变」这种碰巧。
    """
    return ResolvedConstraints.of(
        scene_view(store, project_id, chapter, cast, secrets=secrets),
        cast,
    )
