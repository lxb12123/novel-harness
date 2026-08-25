"""`must_not_reveal` / `forbidden_entities` —— 未来与秘密进 prompt 的**唯一**闸门。

PLAN §5.4 / 原则 11：**完整 PLANNED 永不进 Writer prompt**，只转译为这两样东西。
§3.2 的面板把它们画在认知矩阵下面：

```
本场景 must_not_reveal：血脉秘密 · 玄铁令下落
本场景 forbidden_entities：幽泉窟(ch200 首现)
```

── 为什么这是个闸门而不是一个格式化函数 ──────────────────────────────

改 7 判定原设计的 `future_leak_penalty` / `rejected_content_penalty` / `stale_state_penalty`
是**类型错误**：硬约束被当成了软权重——只要某个未来章节片段的 dense_score 够高
（0.95），它就能盖过惩罚项挤进上下文。**正确做法是全部下沉为 filter，让泄漏在物理上
不可能发生，而不是大概率不会发生。** 于是「未来剧情泄漏率」从「靠调权重压低」变成
「结构上恒为 0，除非 filter 有 bug」。

这个模块就是那个 filter 的出口侧。它的四条实现约束直接来自那条原则：

1. **本模块产出的是「不许说什么」，永远不产出「未来发生了什么」。** `ForbiddenEntity`
   里有名字和首现章号，**没有** PLANNED 边的内容——因为那个字段一旦存在，某个下午
   就会有人把它拼进 prompt。
2. **出参里只有 `NodeRef`，没有 `Node`。** 上一条曾经只在字面上成立：出参带着完整的
   `Node`，而 `NodeProps` 是 `extra="allow"`，于是作者写在秘密节点上的 `twist` 和写在
   未来地点上的 `plot_note` 原样穿过闸门进了 prompt。完整论证见 `graph.models.NodeRef`。
3. **PLANNED 边根本没有读路径**（`QUERYABLE_SCOPES` = {CANON, PROVISIONAL}）。所以
   v1 的「PLANNED 转译」走的是 `resolve` → `node.props.first_appears_chapter`，
   不经过 `state_at` / `knowledge_matrix` / `subgraph`。这个绕法是 store 契约点名的，
   也是这条约束免费的原因。
4. **fail-closed：算不准就多禁，不是少禁。** 见 `scene_constraints`。这条与前三条
   方向一致但性质不同——前三条防的是「说了不该说的」，第 4 条防的是「以为没什么不能说」。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..graph import Node, NodeRef, StoryGraph


class UnresolvedCast(Exception):
    """场景块里有解析不出唯一节点的称呼，而调用方要求过一份完整的约束。

    **拿它当「问作者」的信号，不是当错误。** 「师兄」在一章里可能指 8 个人中的任何一个
    （PLAN §3.1 点名的就是这个场景），系统不能替他猜——猜错的产物是一条本该保密的秘密
    从 must_not_reveal 里消失。
    """


class ResolvedCast(BaseModel):
    """作者写在场景块里的 cast 原文 → node_id 的解析结果。

    **歧义（「师兄」→ 8 个人）和查无此人都进 `unresolved`，绝不进 `ids`。**
    这是 panel/knowledge.py 那句「不能由面板猜一个」在类型层的形状：解析不出来的人
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


class ForbiddenEntity(BaseModel):
    """一个「第 K 章才首现」而当前 N < K 的实体。面板渲染成「幽泉窟(ch200 首现)」。"""

    model_config = ConfigDict(frozen=True)

    node: NodeRef
    """**窄引用，不是 `Node`**：这个实体按定义就是关于未来的，它是全库最不该被完整
    序列化的一批节点。见 `graph.models.NodeRef`。"""

    first_appears_chapter: int
    """`node.props.first_appears_chapter`。**作者声明的**（ADR 0004：伏笔由作者意图
    定义，不由文本特征定义——墙上那把枪是不是伏笔取决于他第 200 章打不打算开枪，
    这个信息物理上不存在于已写文本里）。"""

    surfaces: list[str] = Field(default_factory=list)
    """这个实体**可以拿去匹配正文**的称呼，长度降序。

    只收 `Resolution.usable_for_rules` 为真的 surface：歧义的（「师兄」映射到 8 个人）
    绝不能进——把它列进 forbidden_entities，等于禁掉一个此刻在场的人的称呼，
    而 R2 会拿它在正文上开火。**可能为空**（这个实体的别名全都有歧义或被标短），
    那时它仍然要显示给作者看，只是 R2 匹配不了它——漏报，不是误报。
    """


class SceneConstraints(BaseModel):
    """一个场景的约束集。进 Writer prompt 的 X1 事实清单 / X2 叙事化两种形态都读它。"""

    model_config = ConfigDict(frozen=True)

    chapter: int

    unresolved_cast: list[str] = Field(default_factory=list)
    """作者声明了、但解析不出唯一节点的称呼。**非空 = 这份在场不完整。**

    起草侧必须 `require_resolved_cast()`，别自己读这个字段判空。
    """

    forbidden_entities: list[ForbiddenEntity] = Field(default_factory=list)
    """首现章号在本章之后的实体，按首现章号升序（最快要登场的排前面）。"""

    def require_resolved_cast(self) -> None:
        """**起草 / 拼 prompt 之前必须调这一下。**

        解析不出来的称呼要弹给作者（「这一场的『师兄』是萧决还是李管家？」），
        不是替他猜一个，也不是拿着一份退化的约束去起草——退化的约束能防泄漏，
        但它会让 Writer 收到「全部秘密都不许提」，写出来的东西作者也不想要。
        panel/knowledge.py 的 docstring 说这个问题「要在 UI 上问作者」，
        而在此之前没有任何东西把它传到 UI。

        Raises:
            UnresolvedCast: `unresolved_cast` 非空。
        """
        if self.unresolved_cast:
            raise UnresolvedCast(
                f"第 {self.chapter} 章的场景里这些称呼解析不出唯一角色：{self.unresolved_cast}。"
                "请在面板上指定他们是谁——「师兄」在一章里可能指 8 个人，"
                "系统猜错的产物是一条本该保密的秘密从 must_not_reveal 里消失"
            )


def scene_constraints(
    store: StoryGraph,
    project_id: str,
    chapter: int,
    cast: Sequence[str],
    *,
    secrets: Sequence[str] | None = None,
) -> SceneConstraints:
    """算出本场景的 `must_not_reveal` / `forbidden_entities`。**算不准就多禁。**

    Args:
        cast: 作者写在场景块里的**称呼原文**（`<!-- nh: cast=萧决,顾清音,师兄 -->`），
            **不是 node_id**。顺序即矩阵行序。
        secrets: 候选秘密的 node_id。`None` = 本项目全部。

    Notes:
        **为什么收原文而不是 node_id。** 这个函数曾经收 node_id，把解析留给调用方——
        而调用方唯一能写的东西是 `[r.unique_node.id for r in res if r.unique_node]`
        （契约要求猜不出就不能猜）。于是「师兄」解析不出唯一角色时，李管家**静默地**
        从 cast 里消失，`any(state != KNOWS)` 少算一个人，血脉秘密从 must_not_reveal 里
        消失，Writer prompt 拿到「无需保密」→ 说漏嘴。原则 11 破功，而且是 **fail-open**：
        错误方向指向泄漏。
        病灶是接缝本身——`knowledge_matrix` 吃 node_id（对的：歧义要问作者，面板不能猜），
        `Scene.cast` 是称呼原文，中间那次 resolve 没人接住猜不出来的那个人。
        闸门收原文，接缝就不存在了：调用方**没有机会**丢人。

        **为什么退化成全部秘密而不是抛异常。** 两侧的代价不对称：多禁一条的代价是
        「Writer 少写一段」，漏禁一条的代价是「崩人设」。而面板（R1）还得渲染得出来——
        它要显示解析成功的那几行 + 把歧义称呼问给作者，抛异常会让头牌整片黑掉，
        且作者修不了一个异常。起草侧另有 `require_resolved_cast()` 拦。

        **ADR 0006「系统不确定时的默认动作是闭嘴，不是提问」在这里不适用**：
        那条针对的是 STALE 停火（少报一条 issue），而这里闭嘴的产物是泄漏，方向相反。

        **CANON 写死，不开 scope 参数。** 约束是要被断言为真的（「李管家不知道血脉
        秘密」会进 prompt），而 PROVISIONAL 是抽取器猜的、未确认的——拿它去约束
        Writer 就是让 Agent 的猜测变成了 Canon 的效力，原则 5 破在一个没人会看的地方。
    """
    return scene_view(store, project_id, chapter, cast, secrets=secrets).constraints


class SceneView(BaseModel):
    """`scene_constraints()` 内部本来就算了两样东西，这个类型把第二样也交出来。

    **为什么值得为它多一个类型：反混淆铁律**（EVAL_PROTOCOL §2）要求 kill-gate 的
    X1 与 X2 从**同一个 `knowledge_matrix` 对象**渲染，除「清单 vs 散文」外不许有第二处差异。
    如果矩阵由调用方各自去算，这条铁律就只能靠 runner 的自觉；而把矩阵和约束绑在同一个
    frozen 对象里，`assemble(view, form=X1)` 和 `assemble(view, form=X2)` 拿的**必然**是
    同一份——铁律从纪律变成类型保证。这和 `draft/context.py` 用类型编码「cast 已解析」
    是同一招。

    另一半理由是它本来就在那儿：矩阵是 `must_not_reveal` 的**中间结果**，
    让 `draft/` 再算一遍等于制造第二份可能漂移的真相（而且 `draft/` 也算不了——
    矩阵要 node_id，`resolve_cast` 在第 4 道 arch-guard 的 `WRITER_BANNED` 里）。
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
    *,
    secrets: Sequence[str] | None = None,
) -> SceneView:
    """`scene_constraints()` 的完整出参：约束 + 算它用的那份矩阵。参数语义完全相同。

    面板（R1）和判分器只要约束，走 `scene_constraints()`；起草层要矩阵，走这条。
    **两条路算的是同一次**——`scene_constraints()` 现在就是这个函数的一行封装。
    """
    resolved = resolve_cast(store, project_id, cast)
    return SceneView(
        constraints=SceneConstraints(
            chapter=chapter,
            unresolved_cast=resolved.unresolved,
            forbidden_entities=forbidden_entities(store, project_id, chapter),
        ),
        characters=list(resolved.refs),
    )


def forbidden_entities(
    store: StoryGraph,
    project_id: str,
    chapter: int,
) -> list[ForbiddenEntity]:
    """全书里「还没到首现章」的实体。R2 FUTURE_LEAK 和 Writer prompt 共用这一份。

    Notes:
        节点是从**花名册**（`resolve(surfaces=None)`）里发现的——store 契约里没有
        「列出全部节点」这个方法，而这是故意的（裸 query 会让「graph/ 外禁 import
        sqlite3」那条守卫失去意义）。**推论：一个连 canonical 别名行都没有的节点
        在这里是隐形的。** 那是导入器的 bug（每个节点都该有 canonical 行，
        `idx_alias_canonical` 保证至多一条但不保证至少一条），不该在这里补救——
        补救会把它藏起来。
    """
    nodes: dict[str, Node] = {}
    surfaces: dict[str, list[str]] = {}
    for resolution in store.resolve(project_id):
        for hit in resolution.hits:
            first = hit.node.props.first_appears_chapter
            if first is None or first <= chapter:
                continue
            nodes[hit.node.id] = hit.node
            surfaces.setdefault(hit.node.id, [])
            if resolution.usable_for_rules:
                surfaces[hit.node.id].append(resolution.surface)

    entities = [
        ForbiddenEntity(
            # 收窄成 NodeRef 就发生在这一行：props 里的 plot_note / twist 到此为止。
            node=NodeRef.of(node),
            # 上面的 continue 已经保证它不是 None；这里再取一次是为了不把 int | None 带出去。
            first_appears_chapter=node.props.first_appears_chapter or 0,
            # 长度降序 = leftmost-first 即最长匹配，可直接喂 mentions.py 的 alternation。
            surfaces=sorted(surfaces[node_id], key=len, reverse=True),
        )
        for node_id, node in nodes.items()
    ]
    return sorted(entities, key=lambda e: (e.first_appears_chapter, e.node.id))
