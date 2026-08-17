"""图层的出参类型。

PLAN §5.5 的硬要求：**StoryGraph 的所有出参是 Pydantic 模型，`props` 在 repository 层
就 `json.loads` 成 typed field，`dict` 和 `sqlite3.Row` 禁止越过接口。**

这条不是洁癖，它是 StoryGraph Protocol 的全部价值所在。§6 minor #9 诚实承认过
Protocol 是「自我安慰」——唯一实现 = 接口静默泄漏。那条批判是对的，但它有个例外：
**出参是 Pydantic 就换得掉实现，出参是 sqlite3.Row 就换不掉。** 接口能不能挡住实现细节，
全押在这个文件上，不在 store.py 上。

所以：本文件不 import sqlite3，也不 import 任何 store 实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# ══════════════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════════════


class NodeLabel(StrEnum):
    """8 类节点（PLAN §5.8）。

    增长规则（ADR 0005）：**只有当某条面板分区或某条规则真的要查它时，才允许加一类。**
    Volume / Scene / Skill / Event / Fact / State / RelationshipState / EvidenceRef
    在 v1 没有任何消费者——它们不在这里是裁决，不是遗漏。
    """

    CHARACTER = "Character"
    LOCATION = "Location"
    FACTION = "Faction"
    SECRET = "Secret"
    FORESHADOW = "Foreshadow"
    OBJECT = "Object"
    STATE_DIM = "StateDim"
    CHAPTER = "Chapter"


class EdgeType(StrEnum):
    """9 类关系（PLAN §5.8）。

    `DOES_NOT_KNOW` / `PARTIALLY_KNOWS` 是**永久删除**不是推迟（ADR 0005）：
    前者是组合爆炸（实体化后 +67,500 条边），改用闭世界推导「不存在 KNOWS 边 ⇒ 不知道」；
    后者欠定义（部分知道「什么」？），改用「秘密拆子事实 + 每子事实一条 KNOWS」。
    `CAUSES` / `RESULTS_IN` / `PRECEDES` 同样永不做：没有任何规则依赖它们。
    """

    LOCATED_AT = "LOCATED_AT"
    MEMBER_OF = "MEMBER_OF"

    RELATED_TO = "RELATED_TO"
    """**无向**（ADR 0008）。见 `UNDIRECTED_EDGE_TYPES`——那里有完整论证。"""

    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    HAS_STATE = "HAS_STATE"
    OWNS = "OWNS"
    PLANTED_IN = "PLANTED_IN"
    RESOLVED_IN = "RESOLVED_IN"


class InformationScope(StrEnum):
    """三层图谱 + 否决层（PLAN §5.4）。

    这四个是**全部**合法值：

    - `CURRENT` 不在这里：它是推导不是存储。`CURRENT = valid_to IS NULL AND scope=CANON`。
      存它就要回答「一条边被 supersede 时谁负责把 CURRENT 摘掉」，那个同步问题会在 M4
      以误报的形式爆炸。
    - `OPTIONAL` 已**永久删除**（v1 无消费者）。
    - `DRAFT` ≡ `PROVISIONAL`，不另设值。
    """

    CANON = "CANON"
    """作者确认过。可开火（规则用它报错）、可断言为真（进 prompt）。"""

    PROVISIONAL = "PROVISIONAL"
    """抽取的、带证据、未确认。只喂检索和面板灰显，**永不开火、永不断言为真**。"""

    PLANNED = "PLANNED"
    """未来，作者声明。**永不进 Writer prompt**，只转译为 must_not_reveal / forbidden_entities。"""

    REJECTED = "REJECTED"
    """作者否决。保留以防重抽。"""


class EdgeStatus(StrEnum):
    """行级生命周期，与时态**正交**。

    故意只有两个值。没有 `SUPERSEDED`——「被挤掉」已经由 `valid_to_chapter` 表达了，
    再存一个状态位就是把 §5.4 刚砍掉的 CURRENT 同步问题原样请回来。
    """

    ACTIVE = "ACTIVE"
    RETRACTED = "RETRACTED"
    """这条事实从未成立过。唯一来源是 supersede 的同章更正（见 store.upsert_edge）。"""


class EvidenceStatus(StrEnum):
    """边**对它那条证据的使用**的状态（挂在 edge 上，不在 evidence 上）。

    同一条 evidence 可以被多条边引用，各自独立失活。
    """

    NONE = "NONE"
    """无证据。作者直接声明的边、以及全部 PLANNED 边都是这个值。

    **它是哨兵值，不是「空」。** §5.5 的 state_at 过滤写的是 `evidence_status != 'STALE'`，
    而 SQL 里 `NULL != 'STALE'` 求值为 NULL 即假——用 NULL 表示无证据会让那条查询
    静默丢掉每一条作者声明的边，而作者声明正是整个产品（ADR 0004）。
    """

    FRESH = "FRESH"
    STALE = "STALE"
    """revalidate 在当前正文里找不到 quote_sha256 了（ADR 0006）。

    STALE 的边**不进审阅队列**，只做两件事：立刻停火（规则不再拿它报错，这就是它 90%
    的价值）+ 人物卡上一个灰点。**系统发现自己不确定时的默认动作是闭嘴，不是提问。**
    """


class Exclusivity(StrEnum):
    """edge_type 的基数语义（PLAN §5.5）。supersede 靠它决定挤掉哪些旧边。"""

    SINGLE_PER_SRC = "single_per_src"
    """一个人同一时刻只能在一个地方。仅 LOCATED_AT。"""

    SINGLE_PER_SRC_DST = "single_per_src_dst"
    """(src, dst) 单值。HAS_STATE / RELATED_TO / KNOWS / BELIEVES。"""

    MULTI = "multi"
    """可以多条同时有效。MEMBER_OF / OWNS / PLANTED_IN / RESOLVED_IN。"""


class EdgeSource(StrEnum):
    """出处。PLAN 没规定取值，这三个是拍的（见返回值 key_decisions）。

    对应的数据库列**没有** CHECK：它不参与任何过滤，写错了只是日志难看，
    而 SQLite 改 CHECK 要重建整张表。
    """

    AUTHOR = "author"
    EXTRACTOR = "extractor"
    SYSTEM = "system"
    """推导、迁移、decision_log 重放。"""


class AliasKind(StrEnum):
    CANONICAL = "canonical"
    ALIAS = "alias"
    NICKNAME = "nickname"
    TITLE = "title"


class KnowledgeState(StrEnum):
    """认知矩阵的三态（PLAN §3.2）。"""

    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    """错误认知。看 `KnowledgeCell.believed_value` 拿他以为的版本。"""

    UNKNOWN = "UNKNOWN"
    """**闭世界推导**：不存在 KNOWS/BELIEVES 边 ⇒ 不知道。

    这不是「查不到」，是一个断言。它是 O(已声明秘密) 而不是 O(角色 × 事实)——
    正是删掉 DOES_NOT_KNOW 换来的（ADR 0005）。
    """


# ══════════════════════════════════════════════════════════════════════════
# 领域常量
# ══════════════════════════════════════════════════════════════════════════

UNDIRECTED_EDGE_TYPES: Final[frozenset[EdgeType]] = frozenset({EdgeType.RELATED_TO})
"""方向没有语义的边类型。**存储时 (src, dst) 规范化成 (min(id), max(id))**（ADR 0008）。

PLAN 从没回答过「RELATED_TO 是有向的还是无向的」，而那个没答的问题有一个具身：
对称关系（师兄妹 / 夫妻 / 仇敌）作者会自然地两侧各声明一次，图层没有任何东西阻止它。
于是第 143 章「断绝师门」只落 c1→c2 一条边时，supersede 只闭合 c1→c2 的旧边，
c2→c1 的「师兄妹」[10,∞) 原封不动地继续有效——**同一个关系两个互斥的值同时是 CANON**，
而 §5.4 的定义是 CANON「可断言为真、可开火」。全程走的是 upsert_edge 的正常路径、
每一步都成功、UpsertResult 也如实报告了它闭合了什么，没有任何一个环节有机会察觉不对。

**判它无向的依据是这个仓库自己写的东西**：`edge_type` 那行注释写着「(A,B) 关系阶段单值」，
而「关系阶段」——师兄妹 / 断绝 / 主仆，看 test_supersede 里的取值——本来就是对称的：
A 和 B 处在「断绝」阶段，B 和 A 就处在「断绝」阶段。value 存的是关系**名**，不是**角色**。

为什么不选「有向 + edge_type 加一列 symmetric，upsert 时额外闭合反向边」：那条路要么
让 RELATED_TO 恒为对称（于是「师父」这类角色边照样表达不了，和无向没区别，却多了一份
需要同步的行），要么恒为有向（于是上面那个 bug 原样保留）。而**自动补一条镜像边**是
最坏的一种：它就是 §5.4 为了砍掉存储版 CURRENT 而拒绝过的那个东西——「同名字段存两处
就需要一个同步器」。无向存一份，没有同步器可言。

代价（诚实说明）：角色型关系（「A 是 B 的师父」）在 v1 表达不了。v1 没有这个消费者
（R5 读的是关系阶段，局部图渲染的是边上的标签），真要它时应当加一个**新的有向边类型**，
而不是把 RELATED_TO 掰成有向——按 ADR 0005 的增长规则，那时才会有一个真实的查询要它。
"""

CANONICAL_ALIAS_LABELS: Final[frozenset[NodeLabel]] = frozenset(
    {
        NodeLabel.CHARACTER,
        NodeLabel.LOCATION,
        NodeLabel.FACTION,
        NodeLabel.SECRET,
        NodeLabel.FORESHADOW,
        NodeLabel.OBJECT,
    }
)
"""`upsert_node` 会**自动建一条 canonical 别名**（surface == node.name）的 label。8 类减 2。

canonical 别名不是 node.name 的副本（那份论证在 001_init.sql 的 `idx_alias_canonical`
上）：它是**索引项**，没有它 mentions.py 编的那条 alternation 匹配不到本名。所以「要不要
建」这个问题等价于「这个东西会不会被人在正文里叫」。

**StateDim 不在**：`resolve(pid, None)` 是全项目花名册，mentions.py 拿它编译 alternation——
一条 surface=「健康」的 canonical 行会让 alternation 去正文里匹配每一个「健康」，
而 StateDim 是维度名不是称呼，没有人在对白里叫它。

**Chapter 不在**：300 章 = 300 条章标进花名册（「第一百零八章 血脉」），而 cli._open_store
用 `resolve(project)` 判「这个项目有东西吗」。章节不是一个被人叫的东西。
"""

HEALTH_DIM_KEY: Final = "health"
"""R3 DEAD_SPEAKS 认的那个 StateDim 的 `props.dim_key`。

为什么「死了」是一条 HAS_STATE 边而不是 node 上的一个字段：**它必须是时态的。**
存成 `node.props.status='dead'` 的话，萧决在第 89 章死了会让 R3 在第 50 章
也报「死人说话」——那是个 100% 误报，而误报 <1 条/章 是 M3 的生死线。
"""

HEALTH_DIM_NAME: Final = "生死"
"""这个维度**建出来时**的 `node.name`。**它不是身份，`dim_key` 才是。**

存在的理由只有一个：`ensure_state_dim` 建第一条时总得给它起个名。之后作者要是把它
改成「健康」，规则一个字都不受影响（`StateValue.dim_key` 比的是键，不是这个名）——
`NodeProps.dim_key` 的 docstring 说的就是这件事。

**它进不了花名册**（`CANONICAL_ALIAS_LABELS` 里没有 StateDim），所以正文里的
「生死」两个字永远不会被它匹配到。
"""

DEAD_VALUE_TEXT: Final = "死"
"""写进 `EdgeProps.value` 的那个字。**给人看的，规则不许解析它。**

它是常量而不是一个参数，理由是「谁写什么词」和「规则怎么判」必须彻底分开：
判据只有 `value_key`（`HealthValue.DEAD`）。哪天要让作者填「陨落 / 坐化 / 兵解」，
加的是一个**只影响这一行显示**的可选参数，`value_key` 那一侧一个字都不许动。

**2026-08-14 从 `declare.py` 搬到这儿**：那天抽取器长出了 `kind="death"`，于是它有了
第二个写入方。一个两处共用的常量放在其中一处，就是在等着有人在另一处写第二份。
""" 


class HealthValue(StrEnum):
    """`HAS_STATE` 到 health 维度时 `EdgeProps.value_key` 的取值。

    R3 只许比这个机器键，**永远不许去解析 `value` 里作者写的中文**（死 / 身死 / 陨落 /
    坐化 / 兵解 …）。那是「这句话是什么意思」，撞 ADR 0005 的铁律。
    """

    ALIVE = "alive"
    DEAD = "dead"


# ══════════════════════════════════════════════════════════════════════════
# props：JSON 列在这里落地成 typed field，此后不再有人 json.loads
# ══════════════════════════════════════════════════════════════════════════


class NodeProps(BaseModel):
    """`node.props_json` 解出来的东西。

    `extra="allow"`：未知 prop 不该让老数据解不开。但**列出来的字段才是契约**——
    规则和面板只许读具名字段，不许读 `model_extra`。
    """

    model_config = ConfigDict(extra="allow")

    first_appears_chapter: int | None = None
    """作者声明的「第 K 章才首现」。R2 FUTURE_LEAK 和 forbidden_entities 都读它，
    R3 的「未登场角色开口说话」也读它。None = 一开始就在。"""

    dim_key: str | None = None
    """仅 `StateDim` 节点：稳定机器键（health / cultivation / identity）。

    存在的理由是作者随时会把 node.name 从「健康」改成「生死」——规则挂在中文显示名上
    会在那一刻静默失效。
    """

    gender: str | None = None
    personality: str | None = None
    background: str | None = None
    character_notes: str | None = None
    main_character: bool | None = None


class EdgeProps(BaseModel):
    """`edge.props_json` 解出来的东西。"""

    model_config = ConfigDict(extra="allow")

    value: str | None = None
    """`HAS_STATE` 的显示值（金丹 / 元婴 / 死）。给人看的，规则不许解析它。"""

    value_key: str | None = None
    """`HAS_STATE` 的机器键。v1 只有 health 维度有（见 `HealthValue`）——
    修为/身份没有任何规则消费，按 ADR 0005 的增长规则就不该有键。"""

    believed_value: str | None = None
    """`BELIEVES` 的错误认知内容（「以为已泄露」）。面板 §3.2 直接渲染它。"""


# ══════════════════════════════════════════════════════════════════════════
# 一等对象
# ══════════════════════════════════════════════════════════════════════════


class Node(BaseModel):
    """图节点。`id` 是 ULID（ADR 0003），`name` / `chapter_number` 一律是**可变属性**。"""

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    label: NodeLabel
    name: str
    props: NodeProps = Field(default_factory=NodeProps)


class NodeRef(BaseModel):
    """节点的**窄引用**：id / label / name，**没有 props**。闸门只许放它出去。

    ── 为什么它必须存在 ──────────────────────────────────────────────────

    `NodeProps` 是 `extra="allow"`（那是对的：未知 prop 不该让老数据解不开）。于是作者
    写在节点上的任何额外字段都原样挂在 `Node.props.model_extra` 里，而
    `SceneConstraints.model_dump_json()` / `KnowledgeMatrix.model_dump_json()`
    ——API 出参和 prompt 拼装必然这么干——会把它们全吐出来。

    panel/constraints.py 的模块 docstring 立过一条硬约束：「本模块产出的是「不许说什么」，
    永远不产出「未来发生了什么」……因为那个字段一旦存在，某个下午就会有人把它拼进 prompt」。
    那句话字面上成立（`ForbiddenEntity` 确实没带 PLANNED 边的内容），但只要出参里还有一个
    完整的 `Node`，它保护的那条原则就在 props 那一层直接失守——**而 forbidden_entities 的
    节点按定义就是关于未来的**（`first_appears_chapter > chapter` 才会进来），
    must_not_reveal 的节点则是秘密本身。它们是全库最不该被完整序列化的一批节点。

    实测过的形态：`{"twist": "萧决其实是魔尊之子，第 200 章揭晓"}` 挂在 Secret 节点上、
    `{"plot_note": "萧决在此被顾清音所杀"}` 挂在 Location 节点上，两条都能出现在
    第 152 章的 Writer prompt 里。**那个 docstring 的预言应验了，只是提前了：
    它担心「那个字段一旦存在」，而 `extra="allow"` 让每一个字段都存在。**

    `NodeProps` 的 docstring 说「规则和面板只许读具名字段，不许读 model_extra」——
    但 `model_dump_json()` 不读 docstring。纪律管得住人手写的 `.props.plot_note`，
    管不住序列化。**不能改成 `extra="forbid"`**（那条 docstring 的理由是对的），
    问题不在存，在出——所以在出口这一侧换类型。

    `label` 是 8 个枚举值之一、`name` 是面板本来就要渲染的东西（§3.2 那张图里印着
    「本场景 must_not_reveal：血脉秘密 · 玄铁令下落」），两者都零内容风险。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: NodeLabel
    name: str

    @classmethod
    def of(cls, node: Node) -> NodeRef:
        """从一个完整节点上摘下窄引用。**这是 props 被丢掉的唯一地方，也是全部地方。**"""
        return cls(id=node.id, label=node.label, name=node.name)


class Edge(BaseModel):
    """时态边。时间语义是闭开区间 `[valid_from_chapter, valid_to_chapter)`。

    边界必须单测（§8 Day 4）：`valid_from=10, valid_to=143` → ch9 ✗ / ch10 ✓ /
    ch142 ✓ / **ch143 ✗** / ch150 ✗。这是整个时态模型最容易错的地方。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    src: str
    dst: str
    type: EdgeType
    props: EdgeProps = Field(default_factory=EdgeProps)

    valid_from_chapter: int
    valid_to_chapter: int | None = None
    """None = 至今有效。**只有 supersede 写它**——所以 `EdgeSpec` 里没有这个字段。"""

    information_scope: InformationScope
    status: EdgeStatus = EdgeStatus.ACTIVE
    confidence: float = 1.0
    source: EdgeSource = EdgeSource.AUTHOR
    evidence_id: str | None = None
    evidence_status: EvidenceStatus = EvidenceStatus.NONE

    @model_validator(mode="after")
    def _check_interval(self) -> Edge:
        if self.valid_to_chapter is not None and self.valid_to_chapter <= self.valid_from_chapter:
            # 空区间 = 这条事实从未成立过，那是 supersede 的 bug 不是一条事实。
            # 同章更正的正确表达是 status=RETRACTED。
            raise ValueError(
                f"闭开区间非法：valid_to_chapter({self.valid_to_chapter}) 必须 > "
                f"valid_from_chapter({self.valid_from_chapter})；同章更正请用 RETRACTED"
            )
        return self

    @property
    def is_current(self) -> bool:
        """CURRENT 是**推导不是存储**（§5.4）。这个 property 就是那份推导的唯一定义。"""
        return (
            self.valid_to_chapter is None
            and self.information_scope is InformationScope.CANON
            and self.status is EdgeStatus.ACTIVE
        )

    def holds_at(self, chapter: int) -> bool:
        """这条边在第 `chapter` 章是否落在它的有效区间内。

        **只管区间，不管 scope / status / evidence_status。** 别拿它当 state_at 用——
        完整过滤是五个条件，少一个就会漏出 PROVISIONAL 或 STALE 的边去开火。
        """
        return self.valid_from_chapter <= chapter and (
            self.valid_to_chapter is None or self.valid_to_chapter > chapter
        )

    def peer_of(self, node_id: str) -> str:
        """这条边相对 `node_id` 的**对端**。

        有向边（LOCATED_AT / KNOWS / …）的对端就是 `dst`，但 `UNDIRECTED_EDGE_TYPES`
        里的边不是：它按 `(min(id), max(id))` 规范化存储，所以 `dst` 是谁取决于两个 ULID
        的字典序，是个抛硬币。`state_at(顾清音).edges` 里那条 RELATED_TO 的 `dst` 有一半
        概率就是顾清音自己。**别在消费侧写 `e.dst`，写 `e.peer_of(node_id)`。**
        """
        if self.src == node_id:
            return self.dst
        if self.dst == node_id:
            return self.src
        raise ValueError(f"边 {self.id} 的两端（{self.src} / {self.dst}）都不是 {node_id}")


class TextAnchor(BaseModel):
    """对外定位契约：`(para_index, quote_text, occurrence_k)`。**ADR 0006 Day 2 定死。**

    **后端永不对外发 offset。** offset 是三重错位的温床：坐标系（后端字符 offset vs
    ProseMirror pos，40 段的章节差 41）/ 单位（JS 的 `String.length` 是 UTF-16 code unit，
    Python 的 `len()` 是 code point，扩展 B 区汉字如 𤩝 差 1）/ 时间（服务端基于快照 N，
    前端 doc 已是 N+7）。它们会以「偶尔位置差一两个字」的形态出现，被误当成小 bug 调两周。

    Issue（checks/base.py）和 Evidence 共用这一个类型——它是那条契约的**唯一**载体，
    别在别处再定义一个三元组。
    """

    model_config = ConfigDict(frozen=True)

    para_index: int = Field(ge=0)
    """0-based。"""

    quote_text: str
    occurrence_k: int = Field(default=0, ge=0)
    """同一段里同一句话出现多次时的第几次，0-based。"""


class AuditPointer(BaseModel):
    """审计指针：指向不可变快照，**永不失效**（ADR 0006）。"""

    model_config = ConfigDict(frozen=True)

    chapter_snapshot_id: str
    para_index: int = Field(ge=0)
    quote_text: str
    quote_sha256: str = Field(min_length=64, max_length=64)
    """**用定位成功后的原文子串算，不是用 LLM 返回的字符串算。**

    抽取器返回的 quote 有 10–30% 对不上原文。正确顺序是：difflib 模糊定位 →
    ratio<0.9 直接丢弃（宁可漏）→ 对**命中的原文子串**取哈希。反过来做的话，
    锚从第一天起就是坏的。
    """


class RelocatePointer(BaseModel):
    """重定位指针：在**当前**正文里定位。作者随时在自己的编辑器里改稿，所以它会漂。"""

    model_config = ConfigDict(frozen=True)

    chapter_id: str
    quote_sha256: str = Field(min_length=64, max_length=64)
    para_index_hint: int = Field(ge=0)
    """提示，不是真相：relocate 成功后可以就地更新。审计指针的 para_index 永不更新。"""

    occurrence_k: int = Field(default=0, ge=0)


class Evidence(BaseModel):
    """双指针证据（ADR 0006）。

    Evidence 是一级对象——但**它对的原因不是审计和可追溯，是误报缓释**。
    这是个产品理由，原文档把它当成了工程理由。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    chapter_number: int
    audit: AuditPointer
    relocate: RelocatePointer

    def anchor(self) -> TextAnchor:
        """给 UI / Issue 的三元组。

        引语取自审计指针（不可变），段号取自重定位指针（跟着当前正文走）——
        双指针的意义正在这一行：**说的话是当年那句，指的位置是现在那个。**
        """
        return TextAnchor(
            para_index=self.relocate.para_index_hint,
            quote_text=self.audit.quote_text,
            occurrence_k=self.relocate.occurrence_k,
        )


class GraphVersion(BaseModel):
    """改 11：字段免费，语义免费，**历史补不回来**。

    v1 没有索引，所以 `staleness` 恒为 0。它现在就在这里，是为了 v1.1 加向量时不必
    在 5000 行假设了单事务的代码里追加 staleness。
    """

    model_config = ConfigDict(frozen=True)

    canon_version: int = 0
    indexed_version: int = 0
    staleness: int = 0


# ══════════════════════════════════════════════════════════════════════════
# 别名解析
# ══════════════════════════════════════════════════════════════════════════


class AliasHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    node: Node
    kind: AliasKind
    usable_for_rules: bool
    """`alias.usable_for_rules` 列的原值：作者/UI 对**这个 surface 本身**的判定。
    歧义不在这里——见 `Resolution.usable_for_rules`。"""


class Resolution(BaseModel):
    """一个 surface 的解析结果。"""

    model_config = ConfigDict(frozen=True)

    surface: str
    hits: list[AliasHit] = Field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        """一个 surface 映射到 >1 个 node（「师兄」，一章里可能有 8 个人被这么叫）。"""
        return len(self.hits) > 1

    @property
    def unique_node(self) -> Node | None:
        return self.hits[0].node if len(self.hits) == 1 else None

    @property
    def usable_for_rules(self) -> bool:
        """**R2/R3 只许在这里为 True 时开火。**

        ADR 0004 的两条约束（歧义 surface 规则不使用 + 短别名不许匹配）在这里合并成
        一个布尔值。放在这里而不是让每条规则自己判，是因为「每条规则自己判」意味着
        第 5 条社区贡献的规则一定会忘——而它忘的代价是误报，误报是 M3 的生死线。
        """
        return len(self.hits) == 1 and self.hits[0].usable_for_rules


# ══════════════════════════════════════════════════════════════════════════
# 出参聚合
# ══════════════════════════════════════════════════════════════════════════


class StateValue(BaseModel):
    """一条 `HAS_STATE` 边的解读结果。"""

    model_config = ConfigDict(frozen=True)

    dim: Node
    """`StateDim` 节点。显示名在 `dim.name`（修为 / 身份 / 健康）。"""

    dim_key: str | None
    """`dim.props.dim_key` 的直取。规则比它，不比 `dim.name`。"""

    value: str | None
    value_key: str | None
    since_chapter: int
    evidence_id: str | None


class StateSnapshot(BaseModel):
    """`state_at` 的出参：某节点在第 `chapter` 章的全部有效**出边**。

    **出边 + 无向边的两端。** §5.5 的 SQL 只写了 `WHERE src = :node`，那是在「所有边都
    有向」这个前提下写的；`UNDIRECTED_EDGE_TYPES`（ADR 0008）打破了那个前提。
    有向边的入边请用 `subgraph(hops=1)`。
    """

    model_config = ConfigDict(frozen=True)

    node: Node
    chapter: int
    scope: InformationScope
    """这份快照是在哪一层里查的。默认 CANON。"""

    edges: list[Edge] = Field(default_factory=list)
    """全部有效边。`location` / `states` 是它的**投影，不是额外查询**。

    无向边（RELATED_TO）的 `src` 有一半概率就是 `node` 自己——取对端请用
    `Edge.peer_of(node.id)`，别写 `e.dst`（ADR 0008）。
    """

    location: Node | None = None
    """`LOCATED_AT` 的 dst。exclusivity=single_per_src 保证至多一个——
    如果这里出现了「同时在青云城和北荒」，那不是渲染问题，是 supersede 漏了，
    而下一步就是 R4 误报。R4 LOCATION_CONFLICT 读的就是这个字段。"""

    states: list[StateValue] = Field(default_factory=list)
    """全部 `HAS_STATE`。人物卡渲染它，R3 读它。"""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_dead(self) -> bool:
        """R3 DEAD_SPEAKS 的判据。

        闭世界，与「无 KNOWS 边 ⇒ 不知道」同构：**没有 health=dead 的边 ⇒ 活着。**

        **`computed_field` 不是装饰品**：它 2026-08-13 才补上，而在那之前
        `frontend/src/api/types.ts` 的 `StateSnapshot` 里已经写着 `is_dead: boolean`、
        `StateCards.tsx` / `ChapterPrepPage.tsx` 也已经在渲染那个「· 已亡」——
        可 `model_dump()` **从不输出 property**，于是那个字段在浏览器里恒为 `undefined`，
        角标一次都没画出来过，`tsc` 和 vitest 谁都看不见（契约夹具是从真 app dump 的，
        真 app 就没发过这个键，两头一致地缺）。
        **同一条链上第四个断点**：`first_appears_chapter` 没有写入方 / `value_key` 没有
        写入方 / `StateDim` 没有创建路径 / 算出来了但发不出去。
        """
        return any(
            s.dim_key == HEALTH_DIM_KEY and s.value_key == HealthValue.DEAD for s in self.states
        )

    def has_appeared(self) -> bool:
        """是否已登场。R3 的另一半（「未登场角色开口说话」）。"""
        first = self.node.props.first_appears_chapter
        return first is None or first <= self.chapter


class KnowledgeCell(BaseModel):
    """认知矩阵的一格（PLAN §3.2）。"""

    model_config = ConfigDict(frozen=True)

    character_id: str
    secret_id: str
    state: KnowledgeState
    since_chapter: int | None = None
    """`KNOWS`/`BELIEVES` 边的 valid_from_chapter，面板渲染成「✓ 知道 (ch88)」。
    UNKNOWN 时为 None。

    **作者从来没有输入过这个数字**（§5.9 / ADR 0006）：他是看着第 88 章的原文点的确认，
    章号由证据决定。手填表单里根本不该有章号输入框——有它就等于邀请污染。
    """

    believed_value: str | None = None
    """仅 BELIEVES：他以为的版本（「以为已泄露」）。"""

    evidence_id: str | None = None


class KnowledgeMatrix(BaseModel):
    """**认知边界矩阵——整个项目的头牌**（PLAN §3.2 / README 第一行）。

    它是 `cast × secret` 的一次集合查询，**不读正文**：零 LLM、零 NLP、
    **零误报——因为它对文本不做任何断言**。它只是在告诉作者：你自己在第 88 章
    告诉过它的事。

    为什么它必须是数据库查询而不是模型调用：「列出所有知道 X 的人」是 list 型问题，
    正是 ToM 研究里模型失败的那种（GPT-4 在 FANToM 上跨题型一致性 26.6%，人类 87.5%）。
    抽取是开放式枚举，校验是封闭式判定，**声明+查询是零判定**。

    must_not_reveal / forbidden_entities **不在这里**——它们是 panel/constraints.py 的活，
    虽然 §3.2 的面板把它们画在同一个框里。
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    chapter: int
    scope: InformationScope
    version: GraphVersion = Field(default_factory=GraphVersion)

    unresolved_cast: list[str] = Field(default_factory=list)
    """作者在 cast 里写了、但**解析不出唯一节点**的称呼（「师兄」映射到 8 个人）。

    **这是「系统知道自己不完整」的唯一载体。** 没有它，下面那条 `_check_complete`
    只校验「已知行 × 已知列」的笛卡尔积完整，对「作者声明了 3 个人、我只算了 2 个」
    完全无感——而整行缺失正是它注释里那句「面板上少一格 = 作者以为系统没意见 =
    说漏嘴」的极端情形。

    非空时**面板必须把这些称呼显示出来问作者**（panel/knowledge.py 的 docstring 说
    「这个称呼有歧义要在 UI 上问作者，不能由面板猜一个」——这个字段就是那句话的通道）。
    这不违反 ADR 0006「系统不确定时的默认动作是闭嘴」：那条针对的是 STALE 停火
    （少报一条 issue），而这里闭嘴的产物是泄漏，方向相反。
    """

    characters: list[NodeRef] = Field(default_factory=list)
    """在场角色，顺序 = 作者在场景块里声明的 cast 顺序。窄引用，理由见 `NodeRef`。"""

    secrets: list[NodeRef] = Field(default_factory=list)
    """label=Secret 的节点。窄引用——**秘密的 props 里装的正是秘密的内容**，见 `NodeRef`。"""

    cells: list[KnowledgeCell] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_complete(self) -> KnowledgeMatrix:
        want = {(c.id, s.id) for c in self.characters for s in self.secrets}
        got = [(c.character_id, c.secret_id) for c in self.cells]
        if len(got) != len(want) or set(got) != want:
            # 矩阵必须是完整的笛卡尔积。UNKNOWN 格**必须被物化**：闭世界推导下
            # 「没有这一格」和「他不知道」是两个意思，而面板上少一格 = 作者以为
            # 系统没意见 = 说漏嘴。缺格是 bug，不是省事。
            # 注意这条**拦不到整行缺失**（作者声明的某个称呼没解析出来）——
            # 那一类由 `unresolved_cast` 承载，它是这条断言够不着的地方。
            raise ValueError(
                f"认知矩阵必须完整：期望 {len(want)} 格（{len(self.characters)} 角色 × "
                f"{len(self.secrets)} 秘密），实得 {len(got)} 格。闭世界要求 UNKNOWN 格也被物化"
            )
        return self

    def cell(self, character_id: str, secret_id: str) -> KnowledgeCell:
        for c in self.cells:
            if c.character_id == character_id and c.secret_id == secret_id:
                return c
        raise KeyError((character_id, secret_id))


class Subgraph(BaseModel):
    """局部关系图（PLAN §5.5 / 改 10）。**hops ≤ 2 是硬上限。**

    实测（`docs/adr/bench/`，500 章 / 79,430 节点 / 103,264 边）：带时态过滤从一个主要
    人物出发，**2 跳 154 个节点 → 3 跳 4,720 个节点（30 倍爆炸），而可达人物数一个都没多
    （86/200 → 86/200）**。爆炸全来自星形高度数边，扫出来的是 StateDim / Chapter 这类。
    **3 跳既不可视化，也不增加信息。** 全屏 Story Graph Explorer 因此砍到 v2——
    它的核心交互（一到三跳展开）在数学上就是坏的。
    """

    model_config = ConfigDict(frozen=True)

    center: Node
    chapter: int
    hops: int = Field(ge=1, le=2)
    scope: InformationScope
    nodes: list[Node] = Field(default_factory=list)
    """含 center 自身。**按 id 去重**——递归 CTE 的 UNION 写法会把同一节点在 hop=1 和
    hop=2 各返回一行，那是 §5.5 点名删掉递归 CTE 的理由之一。"""

    edges: list[Edge] = Field(default_factory=list)
    truncated: bool = False
    """节点数超过 `MAX_SUBGRAPH_NODES` 被折叠过（§5.5 的 done_when）。"""

    @model_validator(mode="after")
    def _check_unique_nodes(self) -> Subgraph:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"子图节点必须按 id 去重（两层 JOIN 写错会重复）：{dup}")
        if self.center.id not in set(ids):
            raise ValueError("子图必须含 center 自身")
        return self


# ══════════════════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════════════════


class EdgeSpec(BaseModel):
    """`upsert_edge` 的入参。

    **注意这里没有 `valid_to_chapter`，也没有 `status`。** 这不是省略，是 §5.5 的
    「supersede 收敛点」在类型层的实现：闭合旧边这件事只有 `upsert_edge` 里那一处
    实现有资格做，调用方**在物理上填不了**这两个字段。技术评审的 serious #4
    （「CURRENT 改推导之后 valid_to 谁来写」）就是靠这个缺口关掉的。

    `evidence_status` 同理不在这里：它由 `evidence_id` 推导（NULL → NONE，否则 FRESH），
    两列同生同死是数据库 CHECK 强制的。

    `extra="forbid"`（全模块独此一处）：Pydantic 默认会**静默忽略**多余字段，那样
    `EdgeSpec(..., valid_to_chapter=143)` 会毫无声息地什么都不做——一个以为自己闭合了
    旧边、实际没有的调用方，正好制造出这个设计要防的重叠区间。让它当场炸。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    src: str
    dst: str
    type: EdgeType

    valid_from_chapter: int = Field(ge=1)
    """CANON / PROVISIONAL：**由证据决定**，作者永不填（§5.9）。
    PLANNED：这是作者的意图（「计划第 200 章回收」）——那不是回忆是决定，他填得出来。
    """

    information_scope: InformationScope
    """没有默认值是**故意**的：选哪一层是 §5.4 的核心决定，不该被一个默认值替作者做了。"""

    props: EdgeProps = Field(default_factory=EdgeProps)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: EdgeSource = EdgeSource.AUTHOR
    evidence_id: str | None = None

    @model_validator(mode="after")
    def _canonicalize_undirected(self) -> EdgeSpec:
        """无向边（`UNDIRECTED_EDGE_TYPES`）的 `(src, dst)` 在**构造时**就规范化成
        `(min, max)`。所以 `EdgeSpec(src=顾清音, dst=萧决, type=RELATED_TO).src` 可能是萧决。

        为什么放在这里而不是 `upsert_edge` 里调一次：那样「记得规范化」就是一条纪律，
        而漏掉它的产物正是 ADR 0008 那条 bug——两条方向相反的边互不认识，各自带一个
        互斥的值，两个都是 CANON，且没有任何一步会报错。放在构造函数里，
        `upsert_edge` 的幂等键、find_conflicts、insert 全部自动落在同一个 (src,dst) 上，
        调用方**在物理上造不出**一条没规范化的无向边——同 `extra="forbid"` 挡
        `valid_to_chapter` 的那条理由。

        为什么是 `object.__setattr__` 而不是 `return self.model_copy(update=...)`：
        **后者在 `__init__` 这条校验路径上会被 pydantic 直接丢弃**（只发一条
        UserWarning，`EdgeSpec(...).src` 拿到的还是没规范化的值）。已实测。
        那正好是这条规范化静默失效、ADR 0008 那条 bug 原样复活的形态——
        而它唯一的症状是一条警告。`frozen=True` 挡的是调用方事后赋值，不是构造期归一化。
        """
        if self.type in UNDIRECTED_EDGE_TYPES and self.src > self.dst:
            src, dst = self.src, self.dst
            object.__setattr__(self, "src", dst)
            object.__setattr__(self, "dst", src)
        return self


class UpsertResult(BaseModel):
    """`upsert_edge` 的出参：把 supersede 干了什么如实说出来。

    `test_supersede.py` 靠它断言，而不是靠回查数据库——「写入新边自动闭合旧边」
    是个行为，行为要能被直接断言。
    """

    model_config = ConfigDict(frozen=True)

    edge: Edge
    created: bool
    """True = 新插了一行并跑了 supersede；False = 撞上幂等键、只更新了 props 类字段，
    **没跑 supersede**（理由见 store.upsert_edge 的契约）。"""

    closed: list[Edge] = Field(default_factory=list)
    """被闭合的旧边（`valid_to_chapter` 被写上了），已是更新后的值。"""

    retracted: list[Edge] = Field(default_factory=list)
    """被撤回的旧边（同章更正），已是更新后的值。"""


class SecretDetail(BaseModel):
    """`secret` 扩展表那一行。**只在 `NodeSpec.label is SECRET` 时存在。**

    显示名不在这里：它是 `NodeSpec.name` → `node.name`（001_init.sql：node.name 是
    显示真相，扩展表不再存一份——同名字段存两处就需要一个同步器）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""

    sub_of: str | None = None
    """父秘密的 **node id**（= `secret.id`，扩展表主键就是 node.id）。

    子事实是 ADR 0005 删掉 `PARTIALLY_KNOWS` 之后「部分知道」的唯一表达法：
    秘密拆子事实、每子事实一条 KNOWS。表达力相同，零新增边类型。
    """


class NodeSpec(BaseModel):
    """`upsert_node` 的入参。幂等键 `(project_id, label, name)`。

    `extra="forbid"` 的理由同 `EdgeSpec`：静默忽略多余字段 = 一个以为自己写了什么、
    实际什么都没写的调用方。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    label: NodeLabel
    name: str = Field(min_length=1)
    props: NodeProps = Field(default_factory=NodeProps)
    secret: SecretDetail | None = None

    @model_validator(mode="after")
    def _chapter_nodes_are_born_with_their_row(self) -> NodeSpec:
        if self.label is NodeLabel.CHAPTER:
            raise ValueError(
                "Chapter 节点只能由 put_chapter 建：它必须和 chapter 行同生。"
                "没有 chapter 行就没有 number，而 number 是 state_at 的全序键"
                "（`valid_from_chapter <= :ch`），且它是 PLANTED_IN 的 dst"
            )
        return self

    @model_validator(mode="after")
    def _secret_row_and_label_live_and_die_together(self) -> NodeSpec:
        if (self.label is NodeLabel.SECRET) != (self.secret is not None):
            # 一个没有 secret 行的 Secret 节点在认知矩阵的默认列序里**根本不成列**
            # （queries.secret_ids 走 `FROM secret`，而 secrets=None 是面板的唯一路径）：
            # 作者看见的是「系统对这个秘密没意见」，实际是「系统不知道有这个秘密」。
            # 那正是头牌面板最怕的那种沉默。反过来，一个带 secret 行的 Character 会被
            # schema 的复合外键当场拒——但抛在这里才说得出人话。
            raise ValueError(
                f"label 与 secret 必须同生同死：label={self.label}、"
                f"secret={'有' if self.secret else '无'}。"
                "Secret 节点必须带 SecretDetail（否则它在认知矩阵的列序里不成列，"
                "面板会把「不知道有这个秘密」显示成「对这个秘密没意见」）；"
                "别的 label 不许带"
            )
        return self


class AliasSpec(BaseModel):
    """`add_alias` 的入参。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    node_id: str
    surface: str = Field(min_length=1)
    kind: AliasKind = AliasKind.ALIAS
    usable_for_rules: bool = True

    @model_validator(mode="after")
    def _canonical_belongs_to_upsert_node(self) -> AliasSpec:
        if self.kind is AliasKind.CANONICAL:
            # canonical 的 surface 必须 == node.name（001_init.sql 的约定），而这里够不到
            # node.name。从这条路放进来撞的是 idx_alias_canonical，给调用方一个读不懂的
            # IntegrityError。
            raise ValueError(
                "canonical 别名由 upsert_node 独占（它的 surface 必须 == node.name）："
                f"add_alias 只收 {sorted(k.value for k in AliasKind if k is not AliasKind.CANONICAL)}"
            )
        return self

    @model_validator(mode="after")
    def _short_surfaces_never_match_prose(self) -> AliasSpec:
        if len(self.surface) < 2 and self.usable_for_rules:
            # ADR 0004：「音」「决」是灾难。alias 表的 CHECK 拦得住这条，但它抛的是
            # IntegrityError——而这不是一个数据完整性错误，是作者需要一句人话的地方。
            # 注意拒的是 usable_for_rules，不是 surface 本身：1 字别名可以存在
            # （真书里有 1 字名的人物），只是不许被规则拿去匹配正文。
            raise ValueError(
                f"别名「{self.surface}」只有 {len(self.surface)} 个字，不能 usable_for_rules"
                "（ADR 0004：短别名去正文里匹配 = 满篇误报）。"
                "要留着它就传 usable_for_rules=False——存得下，只是规则不拿它开火"
            )
        return self


class StoredAlias(BaseModel):
    """`add_alias` 的出参：`alias` 表里那一行。"""

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    node_id: str
    surface: str
    kind: AliasKind
    usable_for_rules: bool


class ChapterSpec(BaseModel):
    """`put_chapter` 的入参。幂等键 `(project_id, number)`（schema 的 UNIQUE）。

    **没有 valid_from，也没有任何作者填的号**（§5.9 / 约束 10）：`number` 是
    `text/chapterize.py` 的文本顺序 index，即「这一章在全书里排第几」，不是正文里印的
    章号（分卷重启和番外会让后者重复，而 state_at 要求全序）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    number: int = Field(ge=1)

    heading: str = Field(min_length=1)
    """整行标题（「第一百零八章 血脉」）→ `node.name`。"""

    title: str = ""
    """标题**部分**（「血脉」）→ `chapter.title`。

    与 `heading` 不重复：node.name 是显示真相（001_init.sql），而 title 存的是那一行里
    去掉「第一百零八章」之后剩下的东西——`CHAPTER_RE` 的 group(2)。
    """

    path: str = Field(min_length=1)
    text: str

    disk_mtime_ns: int | None = None
    disk_size: int | None = None
    """磁盘上那个文件的 `(mtime, size)`。**快路的过滤器，不是真相**——真相是 `text`
    算出来的 sha（迁移 015 的完整论证：mtime 会撒谎，Git 也有这个病）。

    `None` = 调用方不是从一个文件读来的（`save_chapter` 之外的路径、测试的内存构造）。
    **写进库的 `None` 意味着「下次必须重读」**，那是 fail-safe 的那一侧。

    ⚠️ **调用方必须先 stat 再读**。反过来的话，文件在 stat 和 read 之间被改，
    记下的 mtime 会比读到的内容**新** → 下一次检查看着一致 → **那次改动永远发现不了**。
    先 stat 最坏是多读一次，无害（`importer._read_one_chapter` 钉着这个顺序）。
    """

    # text_sha256 **不是字段**：由 text 现算。开着这个口子迟早有调用方传一个跟 text 不符的
    # 哈希进来，而快照去重（UNIQUE(chapter_id, text_sha256)）会照单全收，且没有任何东西
    # 会报错——同 decisions.append() 没有 quote_sha256 参数，同一条理由。


class StoredChapter(BaseModel):
    """`put_chapter` 的出参。

    **不叫 `Chapter`**：`text/chapterize.py` 已经有一个 `Chapter`，它的 docstring 逐字
    写着「跟库里的 chapter 表不是一回事」——同名两个类会把那句话变成一个 import 陷阱。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """== 那个 Chapter 节点的 `node.id`（扩展表主键 = node.id）。"""

    project_id: str
    number: int
    title: str
    path: str
    text_sha256: str
    snapshot_id: str
    """**当前**快照，即 `text_sha256` 对得上的那一条。"""

    snapshot_generation: int = Field(ge=1)
    """这一章当前正文的**单调 generation**（017 迁移）。

    每次当前 `text_sha256` 真正切换（包括从 S2 还原到历史 S1）都加一；相同 hash
    重存不加。它是保存后所有自动任务判断「结果是否还新」的钥匙之一（ABA 防护）。
    """

    created: bool
    """True = 这一章的节点和 chapter 行是这次建的。"""

    snapshot_created: bool
    """True = 这次新落了一条快照（正文与上次不同）。False = 同内容，按
    `UNIQUE(chapter_id, text_sha256)` 复用了旧的——快照是证据的锚，不是版本历史。"""


class ChapterText(BaseModel):
    """一章的**当前**快照连正文。`current_snapshots` 的出参，定位引语的料。"""

    model_config = ConfigDict(frozen=True)

    chapter_id: str
    number: int
    snapshot_id: str
    text: str


class ChapterSnapshot(BaseModel):
    """一章的一条**历史**快照。`chapter_snapshots` 的出参，版本对比的料。

    **快照按内容去重，不是全量版本史**（001_init.sql：UNIQUE(chapter_id, text_sha256)，
    同内容只存一次）。它存在的第一理由是证据的锚（旧证据的引语指得回原文），版本对比
    是白捡的副产物——所以这里诚实地叫「内容不同的历史版本」，不叫「编辑历史」。
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    text_sha256: str
    text: str
    created_at: str
    is_current: bool
    """== `chapter.text_sha256`，即磁盘正文当前对应的那条。"""


class SnapshotUsage(BaseModel):
    """一条快照**被谁引着**。删之前必须先问它。

    三张表外键到 `chapter_snapshot` 且**都没有 ON DELETE CASCADE**（evidence /
    extraction_run / proposal_set）——那不是遗漏，是 001_init.sql 那行注释写死的
    「审计指针指向不可变快照，永不失效」。所以「删一条快照」不是一句 DELETE：
    删掉被引的那条，要么撞外键报错，要么（真让它级联）把一条证据的出处凭空抹掉。
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    evidence: int = Field(ge=0)
    """引这条快照当审计锚的证据数。"""
    extraction_runs: int = Field(ge=0)
    proposal_sets: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.evidence + self.extraction_runs + self.proposal_sets

    def is_free(self) -> bool:
        """没有任何东西引着 = 删了不会让谁失去出处。"""
        return self.total == 0


class ChapterUsage(BaseModel):
    """这一章**已经被引擎记住了多少东西**。删整章之前必须先问它。

    和 `SnapshotUsage` 同一个道理，但拦的东西更狠：Chapter 既是一行 `chapter`，
    也是一个 `node`（`put_chapter` 让两者同生），而 `edge.src/dst` 到 `node` 是
    **ON DELETE CASCADE** 的——一句 `DELETE FROM node` 会把指着这一章的
    PLANTED_IN / RESOLVED_IN **无声地**一起带走。`evidence.chapter_id` 到 `chapter`
    同样是 CASCADE，于是那些证据（连同引着它们的边和事件）也会跟着蒸发。

    **所以这里数的不是「删了会不会报错」，是「删了会不会让作者丢掉他不知道自己有的东西」。**
    数出来非零就拒绝，让作者看见挡路的是什么——而不是替他决定那些记忆可以丢。
    """

    model_config = ConfigDict(frozen=True)

    chapter_number: int = Field(ge=1)
    evidence: int = Field(ge=0)
    """锚在这一章正文里的证据条数（每一条都是「那句话当年在这儿」）。"""
    edges: int = Field(ge=0)
    """从这一章生效、或者指着这一章那个节点的关系条数。"""
    events: int = Field(ge=0)
    """记在这一章名下的情节条数。"""
    extraction_runs: int = Field(ge=0)
    proposal_sets: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.evidence + self.edges + self.events + self.extraction_runs + self.proposal_sets

    def is_free(self) -> bool:
        """引擎在这一章上什么都没记 = 删掉它不会让任何东西失去出处。"""
        return self.total == 0


class EvidenceSpec(BaseModel):
    """`put_evidence` 的入参。**证据的锚在类型层面就坏不了。**

    **没有 `quote_sha256`**：实现会从快照里按 `(para_index, occurrence_k)` 切出原文子串、
    核对它逐字等于 `quote_text`、再对**那个子串**取哈希（ADR 0006 配套第 3 条）。开着这个
    参数，迟早有调用方把「LLM 返回的那个字符串」的哈希传进来——而它有 10–30% 不是逐字
    原文，于是锚从写下去的那一刻起就是坏的，且没有任何东西会报错。同
    `decisions.append()` 和 `ChapterSpec.text_sha256`，同一条理由。

    **没有 `chapter_id`**：由 `chapter_snapshot_id` 反查。两个指针必须指向同一章，
    而让调用方分别传两个 id 就是给它一个传成两章的机会。

    **没有 `para_index_hint`**：初次落库时它恒等于 `para_index`（双指针在写入这一刻
    重合，此后审计那个永不动、重定位那个跟着作者改稿漂）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    chapter_snapshot_id: str
    para_index: int = Field(ge=0)
    occurrence_k: int = Field(default=0, ge=0)
    quote_text: str = Field(min_length=1)
