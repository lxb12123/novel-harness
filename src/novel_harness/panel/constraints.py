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

from ..graph import AliasKind, InformationScope, KnowledgeState, Node, NodeRef, StoryGraph
from .knowledge import KnowledgeMatrix, knowledge_matrix


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
    """
    ids: list[str] = []
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
    return ResolvedCast(ids=ids, unresolved=unresolved)


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
    """作者声明了、但解析不出唯一节点的称呼。**非空时 `must_not_reveal` 是退化值**
    （全部秘密），不是算出来的答案——见 `scene_constraints`。

    起草侧必须 `require_resolved_cast()`，别自己读这个字段判空。
    """

    must_not_reveal: list[NodeRef] = Field(default_factory=list)
    """**在场的人里至少有一个还不知道**（或持错误认知）的秘密，列序 = 矩阵列序。

    判据是 `KnowledgeState != KNOWS`：BELIEVES 也算——李管家以为血脉秘密已泄露，
    这一场把真相说破同样是崩人设。

    `unresolved_cast` 非空或 cast 为空时，这里是**全部秘密**（fail-closed）。
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
    matrix: KnowledgeMatrix
    """算 `must_not_reveal` 用的**就是这一份**。X1 的「认知矩阵要点」也渲染它。"""


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
    matrix = knowledge_matrix(
        store,
        project_id,
        chapter,
        resolved.ids,
        secrets=secrets,
        scope=InformationScope.CANON,
        unresolved=resolved.unresolved,
    )
    if resolved.complete:
        must_not_reveal = [
            secret
            for secret in matrix.secrets
            if any(
                matrix.cell(character.id, secret.id).state is not KnowledgeState.KNOWS
                for character in matrix.characters
            )
        ]
    else:
        # fail-closed：在场的人我没数全，就没资格说哪条秘密是安全的。
        must_not_reveal = list(matrix.secrets)
    return SceneView(
        constraints=SceneConstraints(
            chapter=chapter,
            unresolved_cast=resolved.unresolved,
            must_not_reveal=must_not_reveal,
            forbidden_entities=forbidden_entities(store, project_id, chapter),
        ),
        matrix=matrix,
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


def secret_surfaces(
    store: StoryGraph,
    project_id: str,
    secrets: Sequence[NodeRef],
) -> dict[str, list[str]]:
    """每个 must_not_reveal 秘密「可拿去匹配正文」的**内容 tell**，node_id → surfaces（长度降序）。

    与 `forbidden_entities` 的 surface 逻辑同源（都走 `resolve` 花名册 + `usable_for_rules`），
    只有一处关键不同：**排除 canonical 别名**（= 秘密的显示名，如「血脉秘密」）。

    ── 为什么排除 canonical ──────────────────────────────────────────────
    显示名是进 Writer prompt 的**标签**（§3.2 面板印着「must_not_reveal：血脉秘密」，
    X1/X2 注入的也是这个名）。检测器若把它算成 tell，命中的就是 prompt 自己写进去的
    那个词——一次 echo 假阳性。真正「说漏嘴」的判据是**内容 tell**（「玄血蛊」），它以
    非 canonical 别名声明，与显示名不相交。这正是 M2 kill-gate 里 KNOWS 维度「检测集合 ⟂
    prompt 集合」得以成立、从而 FUTURE_LEAK 只能作地板的原因（docs/EVAL_PROTOCOL.md §3/§5）。

    **本函数为合成小册子的「唯一专名 tell」而生。** 真书里秘密内容散在语义里，集合判断
    够不着（ADR 0005）——那正是 kill-gate 要先在合成小册子上验证的东西。`eval/leak.py`
    只经本函数取 must_not_reveal 的 tell，不自己走 `store.resolve`
    （由 `tests/test_draft_boundary.py` 的第 4 道 arch-guard 钉死——那道守卫 2026-07-27 才真的
    写出来，在此之前这句话是空头支票）。**同一道守卫还禁止 `draft/` 引用本函数**：
    tell 是判分器那一侧的东西，进了 prompt 就是 echo 假阳性。
    """
    wanted = {s.id for s in secrets}
    if not wanted:
        return {}
    surfaces: dict[str, list[str]] = {}
    for resolution in store.resolve(project_id):
        if not resolution.usable_for_rules:
            continue
        hit = resolution.hits[0]  # usable_for_rules ⇒ 恰好一个 hit（见 Resolution.usable_for_rules）
        if hit.node.id not in wanted or hit.kind is AliasKind.CANONICAL:
            continue
        surfaces.setdefault(hit.node.id, []).append(resolution.surface)
    return {nid: sorted(ss, key=len, reverse=True) for nid, ss in surfaces.items()}
