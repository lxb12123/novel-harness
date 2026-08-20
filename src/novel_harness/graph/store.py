"""StoryGraph Protocol —— 图层的唯一入口（PLAN §8 Day 4「Protocol，五方法」）。

**先读这段诚实说明，别把这个 Protocol 当成它不是的东西。**

§6 minor #9 自己承认：Protocol 是「自我安慰」——唯一实现 = 接口静默泄漏。那条批判是对的，
而且当唯一实现（`sqlite_store.py`）和接口长得一模一样时尤其对。所以：

    **这个接口的价值全在出参类型上，不在方法签名上。**
    出参是 Pydantic 就换得掉实现，出参是 sqlite3.Row 就换不掉。

真正让 schema 可以随便改的保险是 `decision_log`（§5.7），不是这个文件。这里能做到的
只有一件事：让 `graph/` 外面的代码**拿不到 dict 和 Row**，于是 4 条规则和人物卡渲染
不会全都在解 SQLite 的 JSON 列。CI 的架构守卫（`graph/` 之外任何文件 import sqlite3
直接失败）是它的另一半——它连 import 都拦，而不是靠纪律。

哪五个方法：PLAN 说了「五方法」但没说是哪五个。这五个是拍的，逐个对得上一个真实消费者：

    | 方法              | 消费者                                             |
    |-------------------|----------------------------------------------------|
    | `resolve`         | text/mentions.py 的花名册、cast 解析、面板的 `cast` 参数、R2 |
    | `state_at`        | panel/state.py、R3 DEAD_SPEAKS、R4 LOCATION_CONFLICT |
    | `knowledge_matrix`| panel/knowledge.py ← **头牌**、draft 的 D 分区      |
    | `subgraph`        | 局部关系图（M5）                                    |
    | `upsert_edge`     | canon/commit.py、extract/incremental.py（M4）       |

没有 `get_node` / 按 ID 的通用 `get_edge` / 裸 `query`：节点和可查询边都随
StateSnapshot / KnowledgeMatrix / Resolution 一起出来。`CanonWriter` 只有一个按完整
幂等键读取边的窄口子，专供作者重放判定；开通用查询口子仍等于把 SQL 换个地方泄漏出去。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from contextlib import AbstractContextManager
from typing import Final, Protocol, runtime_checkable

from .models import (
    AliasSpec,
    ChapterSnapshot,
    ChapterSpec,
    ChapterText,
    ChapterUsage,
    Edge,
    EdgeSpec,
    EdgeType,
    Evidence,
    EvidenceSpec,
    InformationScope,
    KnowledgeMatrix,
    Node,
    NodeSpec,
    Resolution,
    SnapshotUsage,
    StateSnapshot,
    StoredAlias,
    StoredChapter,
    Subgraph,
    UpsertResult,
)

# ══════════════════════════════════════════════════════════════════════════
# 接口常量
# ══════════════════════════════════════════════════════════════════════════

MAX_HOPS: Final = 2
"""**硬上限，不是默认值。** 实测 3 跳 = 30 倍节点爆炸且可达人物一个都没多（§5.5）。
`hops > MAX_HOPS` 必须抛 `ValueError`，不许静默截断。"""

MAX_SUBGRAPH_NODES: Final = 30
"""§5.5 的 done_when：2 跳 + 类型过滤后 ≤30 节点，超过则折叠成聚合节点并置 `truncated`。"""

HOP2_EDGE_TYPES: Final[frozenset[EdgeType]] = frozenset(
    {EdgeType.RELATED_TO, EdgeType.KNOWS}
)
"""第 2 跳允许展开的边类型（§5.5：「只展开 RELATED_TO/KNOWS」）。

§5.5 举的反例是 `APPEARS_IN`，但那个类型在 9 类里**不存在**（它是被砍掉的 17/20 schema
的遗留）。v1 真正的星形高度数边是另外三条，别照抄那个例子就以为没事：

- `HAS_STATE`：全书每个人都连到「健康」这**一个** StateDim 节点
- `MEMBER_OF`：一个门派连着几百人
- `LOCATED_AT`：一座城连着所有到过的人

第 2 跳放开其中任意一条 = 2 跳返回全书。这就是这个常量存在的全部理由。
"""

QUERYABLE_SCOPES: Final[frozenset[InformationScope]] = frozenset(
    {InformationScope.CANON, InformationScope.PROVISIONAL}
)
"""`state_at` / `knowledge_matrix` / `subgraph` 只接受这两层，传别的必须抛 `ValueError`。

**`PLANNED` 被挡在读路径外是改 7 的要求，不是保守。** 改 7 的原话：
`future_leak_penalty` 这类是**类型错误**——硬约束被当成了软权重，dense_score 够高
就能盖过惩罚项挤进上下文。正确做法是「全部下沉为 filter，泄漏在物理上不可能发生，
而不是大概率不会发生」。这里就是那个 filter：**PLANNED 边没有任何读路径能把它捞进
Writer prompt。** 于是「未来剧情泄漏率」从「靠调权重压低」变成「结构上恒为 0」。

PLANNED 是**可写不可读**的（`upsert_edge` 照收——伏笔的「计划第 200 章回收」就是它）。
它的唯一出口是 panel/constraints.py 转译成 must_not_reveal / forbidden_entities，
而那条路走 `resolve` 读 `node.props.first_appears_chapter`，不经过这三个方法。

`REJECTED` 同样不可读：它只是「保留以防重抽」的坟场（§5.4）。
"""


# ══════════════════════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════════════════════


class StoreError(Exception):
    """图层错误的基类。"""


class NodeNotFound(StoreError):
    """`node_id` 在本项目里不存在。跨项目误引用也走这里——ULID 的 `project_short`
    前缀就是为了让它在日志里一眼可见（ADR 0003）。"""


class SupersedeConflict(StoreError):
    """`upsert_edge` 撞上了 v1 不支持的乱序插入（见 `StoryGraph.upsert_edge` 的契约）。

    **必须抛，不许猜。** 猜错的产物是 `state_at` 同时返回「在青云城」和「在北荒」，
    那正是 §5.5 点名的死法：两条互斥边 → 规则误报 → M3 的「误报 <1 条/章」生死线崩。
    宁可让调用方看见一个异常。
    """


class SnapshotIsCurrent(StoreError):
    """要删的那条快照就是这一章**当前**正文对应的那条。

    删了它，`current_snapshots` / `chapter_snapshots(is_current)` 会查不到这一章的当前
    快照，`rolling_summary` 直接抛「has no current snapshot」。作者想丢掉的是「现在这一版」
    时，正确的动作是**先还原到别的版本**（那会把 current 移过去），再删这一条。
    """


class SnapshotInUse(StoreError):
    """要删的那条快照被证据 / 抽取记录 / 提案引着（`SnapshotUsage.total > 0`）。

    三条外键都没有 ON DELETE CASCADE，是有意的：一条证据的价值全在「那句话当年在这儿」，
    锚没了它就只是一句无出处的断言。**所以这里拒绝，而不是连带删除。**
    """

    def __init__(self, usage: SnapshotUsage) -> None:
        self.usage = usage
        super().__init__(
            f"快照 {usage.snapshot_id} 还被引用着"
            f"（证据 {usage.evidence} / 抽取 {usage.extraction_runs} / 提案 {usage.proposal_sets}）"
        )


class ChapterInUse(StoreError):
    """要删的那一章上，引擎已经记了东西（`ChapterUsage.total > 0`）。

    **这条拒绝拦的不是外键，是级联。** `edge.src/dst` → `node` 和
    `evidence.chapter_id` → `chapter` 两条都是 ON DELETE CASCADE，所以「删一章」
    技术上一句 DELETE 就过了，**而且一声不吭**：指着这一章的关系、锚在这一章正文里的
    证据、连同引着那些证据的情节，会在作者按下那颗按钮的一瞬间一起没掉。

    引擎记住的东西是这个产品**唯一**的资产。所以这里的默认动作是拒绝并把挡路的东西
    数给作者看，由他决定——不是替他决定那些记忆可以丢。
    """

    def __init__(self, usage: ChapterUsage) -> None:
        self.usage = usage
        # 这句话**是要上屏的**（`api/app.py` 原样发给前端）。所以它说三件事：
        # 挡路的是什么、有多少条、删了会怎样。**不说「怎么办」**——今天界面上确实
        # 没有一条路能把这些清掉，编一句「先去某处删掉它们」就是把作者支去一个空房间。
        # 词按 `SnapshotInUse` 那句的口径（证据 / 抽取 / 提案 已经在版本抽屉里上过屏）。
        super().__init__(
            f"第 {usage.chapter_number} 章上还记着东西"
            f"（证据 {usage.evidence} / 关系 {usage.edges} / 情节 {usage.events} / "
            f"抽取 {usage.extraction_runs} / 提案 {usage.proposal_sets}）。"
            "删掉这一章，这些会跟着一起没。"
        )


class QuoteMismatch(StoreError):
    """`EvidenceSpec.quote_text` 在快照的那个 `(para_index, occurrence_k)` 上不是逐字原文
    （段号越界、第 k 次不存在、那个位置上是别的字，三者同一种失败）。

    ADR 0006 配套第 3 条的那句「反过来做的话，锚从第一天起就是坏的」在这里被变成
    **不可能**，不是被检测：`EvidenceSpec` 里没有 `quote_sha256`，哈希只可能对
    「从快照里切出来的那个子串」取；而如果切不出来，就没有 evidence 行。
    一条锚错了的证据是查不出来的——它的产物是一条 `valid_from` 错了的 CANON 边，
    而它在面板上长得完全正常。
    """


# ══════════════════════════════════════════════════════════════════════════
# Protocol
# ══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class StoryGraph(Protocol):
    """故事图谱的读写接口。

    实现约束（对 `sqlite_store.py`）：

    1. **时态过滤只在 `graph/queries.py` 实现一次。** 五个条件缺一不可：
       `valid_from <= ch` / `(valid_to IS NULL OR valid_to > ch)` / `scope = :scope` /
       `status = 'ACTIVE'` / `evidence_status != 'STALE'`。
    2. **一个方法一个事务。** 单库的全部好处（原子提交、幂等、STALE_BASE_VERSION）
       都建立在这上面——这正是砍掉 Neo4j 的核心论证（§2.2a）。
    3. **出参永远是 `models.py` 里的类型。** 任何 `dict` / `sqlite3.Row` 越过这个接口
       都会让接口失去它唯一的价值。
    """

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        """把称呼（surface）解析成节点。**别名不做实体消解，这跟 GraphRAG 是反的，而且是故意的。**

        GraphRAG 按名字做 entity resolution 会把「顾姑娘 / 清音 / 魔尊」合并掉——
        而这些别名差异恰恰编码了关系阶段和认知边界（化名 = 别人不知道他是谁 = 认知图的边）。
        **它们是 canon，不是噪声。** 所以本方法只查表，永不合并。

        Args:
            project_id: 项目。
            surfaces: 要解析的称呼。**`None` = 返回全项目花名册**——text/mentions.py 靠它
                拿全部 surface 去编译那条正则 alternation（按长度降序排，leftmost-first
                即最长匹配）。50–200 个实体，一次全取是微秒级。
            rules_only: 只返回 `usable_for_rules` 为真的解析结果，即**恰好一个候选且
                该候选未被标短/不可用**。R2/R3 应当一律传 `True`。

        Returns:
            与 `surfaces` 一一对应且**同序**（`surfaces=None` 时按 surface 长度降序，
            直接可喂给 alternation 编译）。解析不到的 surface 也要返回一个 `hits=[]`
            的 `Resolution`——**不许静默丢掉**，否则调用方分不清「没这个人」和
            「我没问过这个人」。
        """
        ...

    def canon_version(self, project_id: str) -> int:
        """项目当前的 canon 水位（`project.canon_version`）。

        校准产物把这一位冻结进 `source_watermark`（ADR 0033）：水位变化后
        `draft_chapter` 明确拒绝旧校准 ID，而不是悄悄沿用。
        """
        ...

    def knowledge_edges_at(
        self,
        project_id: str,
        character_ids: Sequence[str],
        secret_ids: Sequence[str],
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> list[Edge]:
        """(人物, 秘密) 格上的 KNOWS / BELIEVES **原边**（带完整来源字段）。

        `knowledge_matrix` 把来源字段收窄成格子（state / since / believed_value /
        evidence_id）；写前校准要保留 `source` / `confidence` / `evidence_status` /
        有效区间，所以这里直接给原边。**只给校准层用，不给 Agent。**
        """
        ...

    def state_at(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> StateSnapshot:
        """某节点在第 `chapter` 章的有效边快照（**出边 + 无向边的两端**）。
        整个时态模型的收敛点。

        闭开区间 `[valid_from, valid_to)`。§8 Day 4 点名要单测的边界：
        `valid_from=10, valid_to=143` → ch9 ✗ / ch10 ✓ / ch142 ✓ / **ch143 ✗** / ch150 ✗。

        Args:
            chapter: 全书顺序位置，**从 1 起**。`< 1` 必须抛 `ValueError`，不许静默返回
                一个语义上不可能存在的答案——第 0 章的「全 UNKNOWN」在闭世界推导下是一个
                **断言**，它会把调用方的一个 off-by-one 变成面板上一个看起来正常的错误答案。
                上界不管：超过全书章数返回「最新状态」是闭开区间的正确语义。
            scope: 只接受 `QUERYABLE_SCOPES`（CANON / PROVISIONAL）。传 PLANNED 或
                REJECTED **必须抛 `ValueError`**。面板要灰显 PROVISIONAL 就调两次。

        Raises:
            NodeNotFound: `node_id` 不在本项目。
            ValueError: `scope` 不在 `QUERYABLE_SCOPES`，或 `chapter < 1`。
            StoreError: 同一维度上出现两条互斥边（见 Notes）。

        Notes:
            §5.5 的 SQL 是 `WHERE src = :node`，那是在「所有边都有向」的前提下写的。
            `UNDIRECTED_EDGE_TYPES`（ADR 0008）打破了它：RELATED_TO 按 `(min,max)` 规范化
            存储，方向是抛硬币，所以它从两端都查得到。取对端用 `Edge.peer_of()`。
            **有向边仍然只有出边**——KNOWS 的 dst 是秘密，反向查是无意义的。

            `StateSnapshot.location` 至多一个是 `LOCATED_AT` 的 exclusivity 保证的，
            不是这里挑一个的结果。**如果它返回了两条 LOCATED_AT，那是 supersede 漏了，
            应当让它炸出来，不许在这里悄悄取第一条。** `HAS_STATE` 同理，且判据是
            `dst.props.dim_key` 而**不是 dst 的节点 id**——两个 StateDim 节点共享一个
            dim_key 时 supersede 认为它们是两个维度，一条都不闭合，而 `is_dead` 的
            `any()` 会让 dead 永远压过 alive。
        """
        ...

    def knowledge_matrix(
        self,
        project_id: str,
        chapter: int,
        cast: Sequence[str],
        *,
        secrets: Sequence[str] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> KnowledgeMatrix:
        """**认知边界矩阵 —— 头牌**（§3.2 / §8 Day 5 / README 第一行）。

        纯集合查询，零 LLM、零 NLP、**不读正文**。闭世界：无 KNOWS/BELIEVES 边 ⇒ UNKNOWN。

        Args:
            cast: 在场角色的 node_id，**顺序即面板的行序**。由作者在场景块里声明
                （`<!-- nh: cast=萧决,顾清音,李管家 -->`），不是抽的。
            secrets: 列序。`None` = 本项目全部 `secret` 行。若作者把一个秘密拆成了子事实
                （ADR 0005 用它替代 PARTIALLY_KNOWS），父秘密和子事实**都会成列**——
                要不要折叠是面板层的判断，图层不猜。

        Returns:
            **完整的笛卡尔积**：`len(cells) == len(cast) * len(secrets)`。UNKNOWN 格必须
            物化（`KnowledgeMatrix` 的 validator 会强制这一点）——闭世界推导下
            「没有这一格」和「他不知道」是两个意思，面板上少一格 = 作者以为系统没意见。

            `characters` / `secrets` 是 **`NodeRef`（窄引用）不是 `Node`**：矩阵是 D 分区的
            料、整份序列化进 prompt，而 Secret 节点的 props 里装的就是秘密的内容。

            **这个方法看不见「作者声明了但没解析出来的人」**（它收的是 node_id）。
            整行缺失是 `_check_complete` 够不着的地方，由 `KnowledgeMatrix.unresolved_cast`
            承载，而那个字段只有 `panel.knowledge_matrix(..., unresolved=...)` 填得上。

        Raises:
            NodeNotFound: cast / secrets 里有 id 不在本项目。
            ValueError: `scope` 不在 `QUERYABLE_SCOPES`，或 `chapter < 1`（见 `state_at`）。

        Notes:
            单测（§8 Day 5）：一个人物在 ch88 得知秘密 → ch87 UNKNOWN、ch88 KNOWS、
            ch152 KNOWS。
        """
        ...

    def subgraph(
        self,
        project_id: str,
        center: str,
        chapter: int,
        *,
        hops: int = 1,
        edge_types: Collection[EdgeType] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> Subgraph:
        """局部关系图。**hops ≤ 2 硬编码，两层显式 JOIN，不许递归 CTE。**

        §5.5 删掉递归 CTE 的两条理由：hops≤2 时两层 JOIN 更快更可控更好读；且 `UNION`
        版会把同一节点在 hop=1 和 hop=2 各返回一行（`Subgraph` 的 validator 会抓这个）。

        Args:
            hops: 1 或 2。`> MAX_HOPS` **必须抛 `ValueError`**，不许静默截断成 2——
                静默截断会让调用方以为自己拿到了 3 跳。
            edge_types: 展开哪些类型。`None` 时：第 1 跳全展开，**第 2 跳只展开
                `HOP2_EDGE_TYPES`**。第 2 跳不带类型过滤一定糊——理由见那个常量的注释，
                v1 的星形边是 HAS_STATE/MEMBER_OF/LOCATED_AT，不是 §5.5 举的 APPEARS_IN。

        Returns:
            节点按 id 去重且含 `center`。超过 `MAX_SUBGRAPH_NODES` 时折叠并置
            `truncated=True`。

        Raises:
            NodeNotFound: `center` 不在本项目。
            ValueError: `hops` 越界，或 `scope` 不在 `QUERYABLE_SCOPES`。
        """
        ...

    def upsert_edge(self, spec: EdgeSpec) -> UpsertResult:
        """写一条边，并**在同一个事务里**跑 supersede。§5.5 的唯一收敛点。

        技术评审的 serious #4 是个真缺口：CURRENT 改推导之后 `valid_to` 谁来写？
        答案是这里，而且只有这里。`EdgeSpec` 里没有 `valid_to_chapter` / `status`
        就是为了让别处**在类型层面**写不了。

        ── 两个机制，互不干涉 ──────────────────────────────────────────────

        **1. 幂等键**（§2.2a「幂等变成一个唯一索引」）：
        `(project_id, src, dst, type, valid_from_chapter, information_scope)`。

        - 撞上了 → **只** `UPDATE props_json / confidence / source / evidence_id /
          evidence_status`，然后**直接返回 `created=False`，不跑 supersede**。
        - 没撞上 → INSERT，然后跑 supersede，返回 `created=True`。

        这两条「不」是有代价换来的，别顺手改：

        - **不碰 `valid_to_chapter`**：M4 的抽取会重跑（后台批跑 + 断点续跑）。若
          conflict 分支把 `valid_to` 重置成 NULL，一条已被 supersede 闭合的旧边就会
          复活成 `[88,∞)`，与 `[120,∞)` 重叠 → `state_at` 返回两条互斥边。
        - **不碰 `status`**：同理，会让一条被同章更正撤回的边在重跑时复活。
        - **不跑 supersede**：那条边的时态结构在它第一次 INSERT 时就已经建好了；
          再跑一次只会撞上「乱序」分支。这是重跑幂等的关键。

        代价（诚实说明）：**「把一条 RETRACTED 的事实重新声明回来」在 v1 不生效**——
        它会命中幂等键、被当成重跑、静默无事发生。v1 没有这个消费者（作者改主意的
        正常形态是在新章节声明新事实），要它时加第 6 个方法 `revive`，不要改这里的
        conflict 分支——那会拿生死线换一个边缘 UX。

        **2. supersede**（仅 `created=True` 时跑），按 `edge_type.exclusivity` 找冲突边：

        - `single_per_src`：同 `(project_id, src, type)` 的其它 ACTIVE 边（LOCATED_AT）
        - `single_per_src_dst`：同 `(project_id, src, dst, type)` 的其它 ACTIVE 边
        - `multi`：无冲突边，什么都不做

        **无向边（`UNDIRECTED_EDGE_TYPES`，ADR 0008）的 `(src, dst)` 在 `EdgeSpec` 的
        构造函数里就已经规范化成 `(min, max)`**，所以 `spec.src` / `spec.dst` 可能与
        调用方传进来的顺序相反，`result.edge` 上也是规范化后的那一对。这是故意的：
        作者会从两侧各声明一次同一个对称关系（师兄妹 / 夫妻 / 仇敌），而两条方向相反的
        边互不认识、各带一个互斥的值、两个都是 CANON——**且全程没有任何一步会报错**。
        规范化之后，反向声明撞的是幂等键，第二行根本建不出来。

        **冲突边的搜索范围必须限定在 `spec.information_scope` 这一层内。**
        这一条是硬要求，不是优化：跨层 supersede 会让抽取器写的 PROVISIONAL 边去闭合
        作者的 CANON 边——那就是 Agent 直接修改了正式 Canon，原则 5 当场破掉。
        分层隔离在这里从一句口号变成一个 WHERE 条件。

        对每条冲突边 `old`，按它和 `spec.valid_from_chapter` 的先后：

        - `old.valid_from < new.valid_from` → 闭合：`valid_to_chapter = new.valid_from`，
          进 `closed`。
        - `old.valid_from == new.valid_from` → **撤回**：`status='RETRACTED'`，进 `retracted`。
          （同章更正：「他在青云城…… 然后他去了北荒」都在第 151 章。不能闭合成
          `[151,151)`——空区间意思是这条事实从未成立，数据库 CHECK 会直接拒了它。）
        - `old.valid_from > new.valid_from` → **抛 `SupersedeConflict`**。

        最后一条的理由：v1 的 supersede 是**只进不退**的。§5.9 让 `valid_from` 由证据
        决定，而证据是按章推进的，所以正常路径不会出现「往中间插一条更早的事实」。
        真要正确处理它，得回答「先前那条事实在后一条结束后要不要恢复」——这个问题
        v1 没有消费者，而猜错的产物是重叠区间。**宁可抛。**

        Raises:
            NodeNotFound: `src` / `dst` 不在本项目。
            SupersedeConflict: 乱序插入（见上）。
            ValueError: `spec` 自身非法（`EdgeSpec` 的 validator 已挡掉大部分）。

        Notes:
            `information_scope` 之间**没有**提升/降级通道：PROVISIONAL → CANON 的提升
            在 v1 就表现为「在 CANON 层 upsert 同一条事实」（幂等键含 scope，所以那是
            新的一行，且 supersede 只在 CANON 层内找冲突）。原来那条 PROVISIONAL 行留在
            自己层里，不影响 `state_at`——它只读 CANON。M4 若需要显式否决（→ REJECTED），
            届时加第 6 个方法，别在这里加 scope 参数。
        """
        ...


@runtime_checkable
class CanonWriter(Protocol):
    """建节点 / 别名 / 秘密 / 章节 / 快照 / 证据 —— **图的写入面**。

    ── 为什么它不是 `StoryGraph` 的六个新方法 ────────────────────────────

    `StoryGraph` 的五个方法**一个字都不动**，理由是它有两个 Fake 实现
    （`tests/test_knowledge.py` / `tests/test_checks.py`）和一份刚把它们从漂移里拽
    回来的一致性规格（`tests/test_store_conformance.py`）。往 Protocol 上加一个方法
    = 两个 Fake 各长一个存根，而 `@runtime_checkable` 只查方法**存在**——那些存根
    会照样让 `isinstance(fake, StoryGraph)` 为真，却什么都不做。

    更根本的：`checks/base.py` 收的是 `store: StoryGraph`。规则**只读**。把写入面塞进
    它们看得见的类型里，等于邀请第 5 条社区贡献的规则去建节点。

    ── 每个方法一个事务，且它们各自是原子的 ───────────────────────────────

    `upsert_node` 要落 node + canonical 别名 (+ secret 行)，`put_chapter` 要落
    node + chapter 行 + 快照。**同生**不是风格问题：一个没有 chapter 行的 Chapter 节点
    没有 `number`，而 `number` 是 `state_at` 的全序键。跨方法的「记得按顺序调」是纪律，
    纪律会在某个赶时间的下午被绕过。
    """

    def transaction(self) -> AbstractContextManager[None]:
        """把多个写方法罩进**一个**事务。已经在事务里则不嵌套（SQLite 无嵌套事务）。

        它存在的理由只有一个具体的调用方：声明层的「先落证据、再落边」必须原子——
        一条 `evidence_id` 指向不存在的证据的边会让 `state_at` 的 STALE 过滤对着一条
        不存在的依据放行。

        **`decisions.append()` 不许进这个事务**：它自己 `conn.commit()`，会把外层事务
        提前提交掉。日志本来就该在事务之后写（写边失败是**预期异常**——乱序声明、
        引语有歧义——而 `decision_log` 的三个触发器封死了 INSERT/UPDATE/DELETE，
        在事务里先写日志 = 每一次拒绝都在那张不可变的表里留一条假的 accept，删不掉）。
        """
        ...

    def find_edge_by_identity(self, spec: EdgeSpec) -> Edge | None:
        """Return the exact idempotency-key match, including closed/retracted edges.

        This is deliberately narrower than a general ``get_edge``: the author declaration
        path needs the pre-upsert value to distinguish a real Canon facet change from a replay.
        """
        ...

    def upsert_node(self, spec: NodeSpec) -> Node:
        """建一个节点，或拿回那个已经存在的。**建节点的唯一入口。**

        幂等键 `(project_id, label, name)`——应用层的，不是唯一索引
        （`idx_node_name` 只是普通 INDEX：真书里同名人物是存在的，schema 不该替作者
        判定「两个『萧决』是同一个人」）。撞上了就更 props 返回，`secret` 行不重写。

        建新节点时**在同一个事务里**还会落：

        - `label in CANONICAL_ALIAS_LABELS` 时一条 canonical 别名（`surface = name`）。
          它的 `usable_for_rules` 是 `len(name) >= 2`——1 字名的人物真书里有，而
          `CHECK (usable_for_rules = 0 OR length(surface) >= 2)` 会让**建节点整个失败**。
          schema 的立场是「短 surface 可以存在，只是不许被规则拿去匹配正文」，不是
          「1 字名的人不许进这本书」。
        - `label is SECRET` 时一条 `secret` 行（`NodeSpec` 的 validator 保证两者同生）。

        幂等顺手关掉了「第二个『萧决』」那条路：那会让 `resolve('萧决')` 返回 2 个 hit
        → `Resolution.ambiguous` → `usable_for_rules` 为假 → **面板上整行消失**，
        而没有任何一步会报错。

        Returns:
            落库后的节点。

        Raises:
            StoreError: 幂等键撞出 >1 行（只有本方法建得出节点，那个状态不该存在）。
            ValueError: `spec` 自身非法（`NodeSpec` 的 validator 已挡掉 Chapter 和
                「Secret 却没有 secret 行」）。
        """
        ...

    def set_first_appearance(self, project_id: str, node_id: str, chapter: int) -> Node:
        """写 `node.props.first_appears_chapter`，**其余 props 一个字段都不动**。

        为什么不是调用方一句 `upsert_node`：`upsert_node` 撞上幂等键时是
        `UPDATE props_json = :props`——**整列覆盖**。于是「给顾清音标一下首现章」
        会把抽取写进去的 `gender` / `personality` / `background` 悄悄抹掉，
        而没有任何一步会报错。这里走的是 merge（同 `EventStore.update_profile`）。

        `chapter` 的血统由调用方负责，且今天只有一条：`declare.Ledger
        .declare_first_appearance` 把它取自**引语定位到的那一章**（约束 10——
        作者说的是「他在这段原文里头一回露面」，不是一个数字）。
        `POST /nodes` 的 `first_appears_chapter` 是另一条：那一条给的是**还没写到**
        的实体（第 200 章才首现的幽泉窟），它没有引语可指，见那个字段的说明。

        Raises:
            NodeNotFound: `node_id` 不在本项目。
        """
        ...

    def ensure_state_dim(self, project_id: str, dim_key: str, name: str) -> Node:
        """拿到这个项目里 `props.dim_key == dim_key` 的那个 StateDim，没有就建一个。

        ── 为什么它是一个方法，而不是调用方两句 `upsert_node` ────────────────

        `upsert_node` 的幂等键是 `(project_id, label, name)`，而 StateDim 的身份是
        **`dim_key`**（`idx_state_dim_key` 是 UNIQUE，`state_at` 按它分组、
        `is_dead` 按它比对）。两者不是一回事：作者把「生死」改名成「健康」之后，
        `upsert_node(name="生死")` 会去 INSERT 第二行，撞上那条 UNIQUE 索引，
        给调用方一个读不懂的 IntegrityError。**按名字找一个按键定身份的东西，
        是一条平时全绿、改过名才炸的路。**

        ── 为什么它在写入面上（`CanonWriter`），而不是一个 `get_state_dim` 读端 ──

        「查完再建」是两次调用之间的一条缝：并发下两边都查到 None，第二次 INSERT 撞
        UNIQUE。收敛成一个方法，调用方**在物理上**写不出那条缝——同 `upsert_edge`
        把 supersede 关在里面的理由。

        `name` 只在**建第一条时**用（见 `HEALTH_DIM_NAME`）：已经存在时原样返回，
        **绝不改名**——作者改过的显示名不该被一次声明悄悄改回去。

        今天唯一的调用方是 `declare.Ledger.declare_dead`（R3 DEAD_SPEAKS 要
        `dim_key='health'` 的那条 HAS_STATE 边）。**这是 ADR 0005 增长规则的一次合法
        加法，不是通用的「按 props 找节点」**：开那个口子等于把 SQL 换个地方泄漏出去。

        Returns:
            落库后的 StateDim 节点。它**没有** canonical 别名
            （`CANONICAL_ALIAS_LABELS` 里没有 StateDim），所以不进花名册、
            不会被 `mentions.py` 拿去匹配正文。

        Raises:
            StoreError: 同一个 `dim_key` 撞出多行（`idx_state_dim_key` 让它不该发生）。
        """
        ...

    def add_alias(self, spec: AliasSpec) -> StoredAlias:
        """给一个节点加一个称呼。**canonical 不走这里**（见 `AliasSpec` 的 validator）。

        别名故意**不做实体消解**（ADR 0004）：「顾姑娘 / 清音 / 魔尊」的差异编码的正是
        关系阶段和认知边界，是 canon 不是噪声。所以这里只是往表里加一行，永不合并。
        """
        ...

    def retire_stale_extractor_facts(
        self, project_id: str, chapter_id: str, current_snapshot_id: str
    ) -> int:
        """这一章换了新正文之后，让锚在**旧那一版**上的抽取事实退休（`STALE`）。

        返回退休了几条。**幂等**：没有旧锚时改 0 行，所以每次 sync 都调也不要紧。
        论证写在 `queries.retire_stale_extractor_facts`（为什么是 STALE 不是
        RETRACTED、为什么只动抽取器那些）。
        """
        ...

    def chapter_disk_stats(self, project_id: str) -> dict[int, tuple[int | None, int | None]]:
        """`{章号: (记下的 mtime_ns, 记下的 size)}` —— 一次查询问完整本书。

        「这一章在外面改过没有」的**快路**：拿它和 `os.stat` 比，一致就不读文件
        （迁移 015 / Git 的 index 用了二十年的那一招）。值里的 `None` = 没记过，
        调用方当成「必须重读」。
        """
        ...

    def put_chapter(self, spec: ChapterSpec) -> StoredChapter:
        """落一章：Chapter 节点 + `chapter` 行 + 一条快照，**一个事务**。

        幂等键 `(project_id, number)`（schema 的 UNIQUE）。已存在时**不抛异常**——
        `sync` 靠这条把作者在自己编辑器里改过的章读进来：heading / title / path /
        text_sha256 就地更新，正文变了就多一条快照（按 `UNIQUE(chapter_id, text_sha256)`
        去重：快照是证据的锚，不是版本历史，同内容只需要存在一次）。

        **旧快照永不删**：审计指针指着它，而那个指针的承诺是「永不失效」。

        Chapter 节点**没有** canonical 别名（`CANONICAL_ALIAS_LABELS` 里没有它）。

        Notes:
            `spec.number` 是全书顺序位置，由 `text/chapterize.py` 的 index 决定，
            **不是作者填的**，也不是正文里印的章号（分卷重启和番外会让后者重复，
            而 `state_at` 的 `valid_from_chapter <= :ch` 要求它是全序键）。
        """
        ...

    def current_snapshots(self, project_id: str) -> list[ChapterText]:
        """每一章的**当前**快照连正文，按章号升序。定位引语的料。

        判据是 `chapter_snapshot.text_sha256 == chapter.text_sha256` 的**精确等值**，
        不是「这一章最新的那条快照」：后者要在「哪个快照是当前的」这件事上猜，而
        `chapter.text_sha256` 已经把答案写在那儿了。猜错的产物是一条锚在旧正文上的证据。
        """
        ...

    def get_evidence(self, project_id: str, evidence_id: str) -> Evidence | None:
        """按 id 取一条证据（面板 Tab3「确定性证据」的读端）。不存在或跨项目 → `None`。

        矩阵格 / 状态边只带 `evidence_id`——要把「✓知道 ch88」还原成当年那句原文，
        得有这个 reader。它是 ADR 0005 增长规则的一次合法加法：Tab3 是真实消费者。
        跨项目当不存在（evidence 两个指针都不带 project_id，这里收口，别泄漏别项目的原文）。
        """
        ...

    def chapter_snapshots(self, project_id: str, number: int) -> list[ChapterSnapshot]:
        """某章的全部快照（内容去重后的历史版本），按 created_at 升序。章不存在 → `[]`。

        版本对比的读端。**快照按内容去重、不是全量版本史**（同内容只存一次）——它的第一
        身份是证据的锚，版本对比是白捡的副产物。`is_current` 判据是 `text_sha256` 精确
        等值（同 `current_snapshots`），不是「最新那条」：作者改回旧版时当前指向旧快照。
        """
        ...

    def delete_chapter_snapshot(self, project_id: str, number: int, snapshot_id: str) -> None:
        """删掉这一章的一条历史快照。**只删真的没人引的那种。**

        快照的第一身份是证据的锚，版本历史是白捡的副产物——所以这个删除是**给作者清理
        版本列表用的**，不是给引擎回收空间用的，三条不变式都必须先过：

        Args:
            number: 快照必须属于这一章。**收 `number` 不是为了查得更快**，是为了让
                「章 + 快照」这对坐标由一个地方校验：调用方分别传两个 id 就是给它一个
                传成两章的机会（同 `EvidenceSpec` 不收 `chapter_id` 的理由）。

        Raises:
            StoreError: `snapshot_id` 不存在、属于别的项目，或不属于第 `number` 章。
            SnapshotIsCurrent: 它是这一章当前正文对应的那条（先还原到别的版本再删）。
            SnapshotInUse: 有证据 / 抽取 / 提案引着它（异常里带 `SnapshotUsage` 明细）。

        实现必须把「查引用」和「删」罩进同一个事务：中间隔着一次声明的话，检查过的
        `usage=0` 会在 DELETE 执行时已经不成立，而外键会在那一刻才炸出来。
        """
        ...

    def delete_chapter(self, project_id: str, number: int) -> ChapterUsage:
        """把这一章从库里删掉（章行、它的节点、它的快照）。**只删引擎还没记过东西的那种。**

        磁盘上那个 .md **不归它管**（ADR 0007：正文在磁盘上，图层只存快照和记忆）——
        文件由 `importer.remove_chapter` 处理，它是这个方法唯一的调用方。

        Returns:
            删掉之前数出来的那份 `ChapterUsage`（全零）。**返回它而不是 `None`**：
            调用方要能把「删掉了，而且确实什么都没连着」写进日志，
            而不是事后再查一次一个已经不存在的章。

        Raises:
            StoreError: 这一章不在库里（磁盘上有、还没 sync 过也算，那时没有行可删）。
            ChapterInUse: 引擎在这一章上记过东西（异常里带 `ChapterUsage` 明细）。

        实现必须把「数引用」和「删」罩进同一个事务，理由同 `delete_chapter_snapshot`：
        中间隔着一次抽取的话，数出来的 0 在 DELETE 那一刻已经不成立——**而这一次不会
        撞外键报错，会静默级联删掉**（`edge.src/dst` 和 `evidence.chapter_id` 都是
        ON DELETE CASCADE），也就是说这个竞态没有第二道防线，只有事务。
        """
        ...

    def put_evidence(self, spec: EvidenceSpec) -> Evidence:
        """落一条双指针证据（ADR 0006）。**`evidence` 行的唯一产地。**

        实现必须按这个顺序，它就是 ADR 0006 配套第 3 条：
        从快照里按 `(para_index, occurrence_k)` **切出原文子串** → 核对它逐字等于
        `spec.quote_text` → 对**那个子串**取哈希。`EvidenceSpec` 里没有 `quote_sha256`，
        所以反过来做在类型层面就不可能。

        两个指针同时落，写入这一刻 `para_index == para_index_hint`：审计的那个指向
        不可变快照、永不更新；重定位的那个跟着作者改稿漂，relocate 成功可就地更新（M4）。

        Returns:
            `Evidence.audit.quote_text` 是**切出来的子串**，不是调用方传进来的那个串
            （M1 精确匹配下两者逐字节相等；M4 的模糊路径上不等）。
            `Evidence.chapter_number` 由 JOIN `chapter` 填——evidence 表里没有这一列，
            但它是本方法对调用方的承诺：声明层正是靠它写 `valid_from`（§5.9）。

        Raises:
            QuoteMismatch: 引语在那个锚上不是逐字原文（含段号 / k 越界）。
            StoreError: `chapter_snapshot_id` 不存在，或它属于别的项目。
        """
        ...


@runtime_checkable
class GraphStore(StoryGraph, CanonWriter, Protocol):
    """读写交集。`SqliteStoryGraph` 实现它；声明层和导入器收它。

    **为什么是一个交集而不是两个对象**：`transaction()` 在 writer 上、`upsert_edge` 在
    `StoryGraph` 上，而声明层要把「落证据」和「落边」罩进同一个事务。两个对象 =
    两条连接的可能 = 那个事务**静默地罩不住 `upsert_edge`**，于是一条边可以在证据回滚
    之后独自留在库里。一个对象一条连接，那个 bug 不存在。

    `checks/` 和 `panel/` 看见的仍然只有 `StoryGraph` 的五个方法：规则只读。
    """
