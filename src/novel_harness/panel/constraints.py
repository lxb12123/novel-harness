"""场景 cast 解析 —— 把作者写在场景块里的称呼原文变成解析结果，歧义留给作者判断。

**2026-08-31：`forbidden_entities`/`ForbiddenEntity`/`scene_constraints()` 整个删了**
（维护者裁定，见 [ADR 0041](../../../docs/adr/0041-forbidden-entities-cut.md)）。
这个模块原来还产出「未登场实体不许提」的禁写清单（PLAN §5.4 / 原则 11 说的那道
「完整 PLANNED 永不进 Writer prompt，只转译为它」的闸门），维护者认定这条价值已经
不在了，连同它在右栏面板、`draft/context.py`、Mode 2 的 `scene_constraints`/
`book_index` 两个工具里的全部读取一起删。**PLANNED 边本身仍然没有读路径**
（`QUERYABLE_SCOPES` = {CANON, PROVISIONAL}，`graph/sqlite_store.py::_check_scope`）
——那条防线是独立的、结构性的，没有跟着这次删除松动；本模块原来只是它的一个可选、
narrow 的转译出口，不是防线本身。完整代价见 ADR 0041。

留下的这部分——`resolve_cast()` / `ResolvedCast` / `UnresolvedCast`——答的是另一个
问题：作者写在场景块里的称呼（「师兄」这种）能不能解析成唯一一个角色。矩阵渲染、
起草、Mode 2 都还在用它，跟上面删掉的那部分是两件事。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..graph import NodeRef, StoryGraph


class UnresolvedCast(Exception):
    """场景块里有解析不出唯一节点的称呼，而调用方要求过一份完整的约束。

    **拿它当「问作者」的信号，不是当错误。** 「师兄」在一章里可能指 8 个人中的任何一个
    （PLAN §3.1 点名的就是这个场景），系统不能替他猜——猜错的产物是一个此刻在场的人
    从这一场的在场名单里静默消失。

    `code`/`params` 是 `frontend/src/backendMessages.ts` 的键 + 填模板用的原始事实
    （国际化第四批 Phase B）——**不再是一句算好的话**。`api/app.py` 捕到它时直接
    转发这一对，不再包一层"在场角色解析不了："之类的前缀：两条内层消息
    （`unresolved_cast_ambiguous`/`unresolved_cast_no_cast_declared`）本来就是
    完整句子，外层加前缀是"后端拼片段、前端拼整句"的形状，同 `validation_
    blocked_title` 要避免的问题是一类。
    """

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


class ResolvedCast(BaseModel):
    """作者写在场景块里的 cast 原文 → node_id 的解析结果。

    **歧义（「师兄」→ 8 个人）和查无此人都进 `unresolved`，绝不进 `ids`。**
    这是「不能由面板猜一个」在类型层的形状：解析不出来的人
    有一个自己的去处，而不是被 `if r.unique_node is not None` 静默滤掉。

    两侧都按出现顺序去重：作者写 `cast=萧决,师兄` 而「师兄」正好唯一指向萧决时，
    他是一个人、一行，不是两行（重复 id 会让矩阵的笛卡尔积 validator 报一个读不懂的错）。
    """

    model_config = ConfigDict(frozen=True)

    ids: list[str] = Field(default_factory=list)
    """解析成功的 node_id，顺序 = 作者写的顺序 = 面板行序。"""

    unresolved: list[str] = Field(default_factory=list)
    """解析不出唯一节点的称呼原文。**非空 = 这份 cast 不完整。**"""

    refs: list[NodeRef] = Field(default_factory=list)
    """`ids` 对应的**窄引用**（id/label/name，没有 props），同序。

    解析时本来就拿着 `Node`，交出来是为了让下游不必再解析一遍——而下游也解析不了
    （`resolve_cast` 在第 4 道 arch-guard 的 `WRITER_BANNED` 里）。
    **窄的，不是 `Node`**：`NodeProps` 是 `extra="allow"`，作者写的东西会穿过序列化。
    """

    @property
    def complete(self) -> bool:
        """全部称呼都解析成功，**且至少有一个人**。

        空 cast 也算不完整，这一条容易被忽略：`Scene.cast` 的默认值是 `[]`，作者写一个
        没有 `cast=` 的场景块就会走到这里。而 `any()` over empty 是 `False`——于是
        「一个人都没有」和「全员都知道」在出参上完全不可区分，两者都产出零约束。
        """
        return not self.unresolved and bool(self.ids)


def resolve_cast(store: StoryGraph, project_id: str, cast: Sequence[str]) -> ResolvedCast:
    """把作者写的称呼解析成 node_id，**并且把解析不出来的那些留下来**。

    Args:
        cast: 场景块里 `cast=萧决,顾清音,师兄` 的那几个词，作者写的原文。

    Notes:
        面板要渲染矩阵行时也该走这里，然后把 `unresolved` 一起传给
        `panel.knowledge_matrix(..., unresolved=...)`——否则面板会安静地少一行。

        ── ⚠️ 歧义在这儿进 `unresolved` 是**有意的**，别改成「候选全算在场」───────

        「把 8 个候选全算在场」那件事**确实在做，但不在这一层**：它住在
        `mentioned.py::mentioned_cast(expand_ambiguous=True)`，今天只有模式二传
        （`agent/tools.py::_derived_cast_from_text`）。那一侧算的是**给模型的禁令**，
        判据是「在场至少有一个人还不知道 ⇒ 就禁」，多算一个人只会多一批禁令。

        **这一层不一样：它的出参会被渲染给作者看。** 右栏那一格回答的是
        「这一章谁在场」——把「师兄」摊成 8 行没有解释的人是噪声，而且正犯了
        「引擎的机制不上作者的屏」那条。作者要的是一条「这个『师兄』是谁？」的提示，
        而 `unresolved` 就是那条提示的通道。

        **所以这个仓库里有两份歧义判据，是两个不同的目标，不是一份实现漏抄了。**
        下一个人看见它们不一致时的正确动作是读这两段注释，**不是去「修统一」**
        （2026-08-22 裁定）。
    """
    ids: list[str] = []
    refs: list[NodeRef] = []
    unresolved: list[str] = []
    for resolution in store.resolve(project_id, list(cast)):
        node = resolution.unique_node
        if node is None:
            # 契约保证解析不到的 surface 也会返回一个 hits=[] 的 Resolution（不许静默丢），
            # 所以这个分支既接歧义（hits > 1）也接查无此人（hits == 0）。
            if resolution.surface not in unresolved:
                unresolved.append(resolution.surface)
        elif node.id not in ids:
            ids.append(node.id)
            refs.append(NodeRef(id=node.id, label=node.label, name=node.name))
    return ResolvedCast(ids=ids, refs=refs, unresolved=unresolved)


class SceneConstraints(BaseModel):
    """一个场景的 cast 解析结果集。"""

    model_config = ConfigDict(frozen=True)

    chapter: int

    unresolved_cast: list[str] = Field(default_factory=list)
    """作者声明了、但解析不出唯一节点的称呼。**非空 = 这份在场不完整。**

    起草侧必须 `require_resolved_cast()`，别自己读这个字段判空。
    """

    def require_resolved_cast(self) -> None:
        """**起草 / 拼 prompt 之前必须调这一下。**

        解析不出来的称呼要弹给作者（「这一场的『师兄』是萧决还是李管家？」），
        不是替他猜一个，也不是拿着一份退化的约束去起草——退化的约束能防泄漏，
        但它会让 Writer 收到「全部秘密都不许提」，写出来的东西作者也不想要。
        这个问题**要在 UI 上问作者**，而在 `require_resolved_cast()` 之前没有任何
        东西把它传到 UI。

        Raises:
            UnresolvedCast: `unresolved_cast` 非空。
        """
        if self.unresolved_cast:
            raise UnresolvedCast(
                "unresolved_cast_ambiguous",
                chapter=self.chapter,
                unresolved=self.unresolved_cast,
            )


class SceneView(BaseModel):
    """一个场景的 cast 解析结果：约束（chapter + unresolved_cast）+ 已解析的在场角色。

    两样绑在一起交出去，是因为算约束时本来就先解析了 cast——让 `draft/` 再算一遍
    等于制造第二份可能漂移的真相，而且 `draft/` 也算不了：`resolve_cast` 在第 4 道
    arch-guard 的 `WRITER_BANNED` 里。

    （2026-08-24 之前 `characters` 是整张认知矩阵，这段 docstring 当时的论证是
    EVAL_PROTOCOL §2 的反混淆铁律——X1/X2 必须从同一个矩阵对象渲染。
    **那份卷子已退役**，见 `docs/EVAL_PROTOCOL_RETIREMENT.md`。留下的理由是上一段，
    它跟那张卷子无关，所以这个类型没跟着一起消失。）
    """

    model_config = ConfigDict(frozen=True)

    constraints: SceneConstraints
    characters: list[NodeRef] = Field(default_factory=list)
    """已解析的在场角色（**只有人物**），顺序 = 作者写的顺序。

    起草侧要它把「作者在在场里写了个地点名」挡掉（`product_draft`），
    而它拿不到 `resolve_cast`（第 4 道 arch-guard 的 `WRITER_BANNED`）——
    所以由这一层交出来，不让下游自己再解析一遍。

    （2026-08-24 之前这儿是整张认知矩阵。它随秘密下线一起走了，ADR 0039。）
    """


def scene_view(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
) -> SceneView:
    """算一次 cast 解析：约束 + 已解析的在场角色。

    Args:
        cast: 作者写在场景块里的**称呼原文**（`<!-- nh: cast=萧决,顾清音,师兄 -->`），
            **不是 node_id**。顺序即在场名单的顺序。

    Notes:
        **为什么收原文而不是 node_id。** 这个函数曾经收 node_id，把解析留给调用方——
        而调用方唯一能写的东西是 `[r.unique_node.id for r in res if r.unique_node]`
        （契约要求猜不出就不能猜）。于是「师兄」解析不出唯一角色时，李管家**静默地**
        从 cast 里消失，而没有任何一步会报错。**错误方向指向 fail-open**，
        这就是 `unresolved` 必须有自己的去处的原因。
    """
    resolved = resolve_cast(store, project_id, cast)
    return SceneView(
        constraints=SceneConstraints(
            chapter=chapter,
            unresolved_cast=resolved.unresolved,
        ),
        characters=list(resolved.refs),
    )
