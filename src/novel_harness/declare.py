"""声明层 —— 作者说「这条事实在这段原文里出现过」，系统自己算出那是第几章（PLAN §5.9 / ADR 0004）。

**这是全系统唯一给 `EdgeSpec.valid_from_chapter` 赋值的作者路径。**

── 约束 10：作者永不填章号，一次都不行 ────────────────────────────────────

§5.9 的论证：问作者「这条关系从第几章开始有效」，他不记得（200 万字写了三年，他连
主角哪章突破金丹都要翻）。他会填 1 → 时态模型退化成当前值快照图 → 这个项目全部
差异化的地基没了。

所以本模块的**每一个公开签名里都没有 chapter / valid_from / since / at**，一个都没有。
作者敲的只有一句引语；章号是「这句引语落在哪一章」的**产物**，血统是：

    ev.chapter_number ← chapter.number ← ChapterSpec.number ← chapterize 的 index

作者的输入从没进过这条链，`Ledger` 的签名里也没有一个位置能让它进来。
（`nh panel --chapter N` 里的 N 是查询参数「AS OF 第几章」，不是声明，那个合法。）

── 为什么这里同时拿 store 和 conn ────────────────────────────────────────

图和 `decision_log` 是**两条被刻意做成不同生命周期的日志**：前者随时会换存储、改
schema、被重抽；后者一个外键都没有（连 project_id 都没有，见 `decisions.py`），
它必须比它记录的一切活得更久。两个 handle 让这件事在签名上看得见,而不是藏在一个
God object 里——那个 God object 的下一步就是「顺手让 decision_log 外键到 project」。

本模块**一行 SQL 都没有**：图走 `GraphStore` 的方法，日志走 `decisions.append()`。
`from .db import Connection` 只做类型标注（同 `decisions.py` / `project.py` 的形状，
`tests/test_arch_guard.py` 明文允许）。

── M1 不做的三件事，写在这里免得下一个人以为是遗漏 ──────────────────────

- ~~`declare_state`（HAS_STATE / StateDim）：读者是 R3，R3 在 M3。~~ —— **2026-08-13 已做**
  （`declare_dead`）。触发条件早就满足了：R2/R3 在 2026-08-02 就进了 `ALL_CHECKS`，
  而生产上**一个 `value_key` 都没人写**、`StateDim` 一条创建路径都没有，于是
  `StateSnapshot.is_dead` 恒为 False —— R3 在结构上永远不可能开火。
  M3 那次「双边门槛已过」量的是 `synth/m3_replay.py` 在**内存里叠加**的死亡边，
  没有一条穿过生产写路径。同批补上的还有 `declare_first_appearance`
  （`node.props.first_appears_chapter`，R2 和 R3 的「未登场」那一半读它，
  在此之前同样零生产写入方）。
- `declare_related`（RELATED_TO）：读者是局部图（M5）和 R5，而 R5 的生死还压在
  `scripts/probe_speaker_tags.py` 那条探针上。
- `nh declare foreshadow` / PLANNED 边（PLANTED_IN / RESOLVED_IN）：**两条理由，
  2026-08-06 只剩一条。**

  ① ~~M1 没有消费者（ADR 0005 增长规则）~~ —— **已作废。** 模式二的推进侧要的正是
     「哪些伏笔埋了还没收」，那就是消费者。增长规则的触发条件已满足，
     **别再拿这条挡人**（它挡了一次就够了）。

  ② **它会给约束 10 的守卫开第一个例外，这条仍然成立**——PLANNED 的 `valid_from`
     确实是作者填的（001_init.sql 逐字写了「那不是回忆是决定」）。例外会被拓宽。
     所以要做 foreshadow，**先拿一份 ADR 裁掉这个例外的边界**，别直接加方法。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from . import decisions, project
from .db import Connection
from .decisions import DecisionKind, Verdict
from .graph import (
    DEAD_VALUE_TEXT,
    HEALTH_DIM_KEY,
    HEALTH_DIM_NAME,
    AliasKind,
    AliasSpec,
    Edge,
    EdgeProps,
    EdgeSource,
    EdgeSpec,
    EdgeType,
    Evidence,
    EvidenceSpec,
    GraphStore,
    HealthValue,
    InformationScope,
    Node,
    NodeLabel,
    NodeProps,
    NodeRef,
    NodeSpec,
    SecretDetail,
    StoredAlias,
)
from .text import anchor

CONTEXT_RADIUS: Final = 15
"""`QuoteCandidate.context` 在命中处前后各留的字数。**只影响拒绝消息，不影响任何锚。**"""


# ══════════════════════════════════════════════════════════════════════════
# 拒绝
# ══════════════════════════════════════════════════════════════════════════


class DeclarationRefused(Exception):
    """作者**主动发起**的一次声明，系统拒绝执行并把候选摆给他看。

    **这不违反约束 8。** 约束 8 治的是「系统主动推队列给作者」；这里是作者按了按钮，
    而系统不肯替他猜——方向相反。

    **绝不许挑一个。** 挑错的产物是一条 `valid_from` 写错的 CANON 边，而它在面板上
    长得完全正常：没有任何一条规则、任何一个面板分区、任何一次 review 会发现它。

    ── 这些话直接进小说作者的屏幕，所以里面不许有一行命令 ──────────────────

    `api/app.py` 的错误映射把 `str(exc)` 原样放进 `message`，`DeclareDrawer` 逐字渲染。
    而产品的最终用户是 README 里那位「用 WPS、不想碰命令行」的作者——**告诉他
    「先跑 nh sync」等于告诉他去一个他从没打开过的窗口**（2026-08-13 之前这里有两句
    这样的话，第三句在 `UnknownName` 里）。

    所以这一层的措辞只说两件事：**发生了什么** + **产品无关的那半句怎么办**
    （「让系统重新读一遍稿子」而不是「跑 nh sync」）。终端里那半句由 `cli.py` 的
    `_refusal_tail()` 接上，浏览器里那半句由抽屉上那颗按钮接上——
    **哪个壳负责哪半句，由壳自己知道，引擎不知道**。

    守卫：`tests/test_wording_guard.py::test_no_refusal_ever_tells_the_author_to_type_a_command`
    拿本模块每一个 `DeclarationRefused` 子类的真实消息去扫，
    浏览器那侧是 `screenGuard.ts::SHELL_LINE`（第五张网）。
    """


class UnknownName(DeclarationRefused):
    """这个称呼在本项目里解析不到任何节点。"""

    def __init__(self, surface: str) -> None:
        self.surface = surface
        super().__init__(
            f"没有叫「{surface}」的东西。先把它建出来（人物 / 地点 / 秘密都行），"
            "或者把这个称呼加到已经建好的那一个上"
        )


class AmbiguousName(DeclarationRefused):
    """这个称呼映射到多个节点（「师兄」，一章里可能有 8 个人被这么叫）。"""

    def __init__(self, surface: str, candidates: Sequence[NodeRef]) -> None:
        self.surface = surface
        self.candidates = list(candidates)
        names = "、".join(f"{c.name}({c.label.value})" for c in self.candidates)
        super().__init__(
            f"「{surface}」在这个项目里指 {len(self.candidates)} 个东西（{names}），"
            "系统不替你挑。用本名或者一个只属于一个人的称呼"
        )


class WrongLabel(DeclarationRefused):
    """解析到了，但它不是这个位置该有的那一类。"""

    def __init__(self, surface: str, got: NodeLabel, want: NodeLabel) -> None:
        self.surface = surface
        self.got = got
        self.want = want
        super().__init__(
            f"「{surface}」是 {got.value}，这里要的是 {want.value}。"
            # 放行的产物是一条 dst 是人的 KNOWS 边：它在 knowledge_matrix 的列序里
            # （走 `secret` 扩展表）根本不成列，于是作者看见的是「系统对这条没意见」。
            f"（一条 dst 不是 {want.value} 的边在面板上不成列——那正是最沉默的一种错）"
        )


class QuoteNotFound(DeclarationRefused):
    """这句话在当前正文里一处都定位不到。"""

    def __init__(self, quote: str) -> None:
        self.quote = quote
        super().__init__(
            f"这句话在当前正文里一处都找不到：「{quote}」。\n"
            "  这里只认一模一样的句子（标点、空格、全角半角都算）——从稿子里复制粘贴，别手打。\n"
            # **不许再写成「你选错了」。** 作者在别的软件里改完这一章回到工作台，正文他
            # 看得见（章列表和正文都直接扫磁盘），而这句话搜的是**库里的快照**——
            # 快照只有「读回改动」那一下落得下。他的选择没有问题，是这边还没读过那一版。
            "  也可能是这一章你在别的软件里改过，而这边还没读回来：\n"
            "  让系统重新读一遍稿子，再试一次。"
        )


class AmbiguousQuote(DeclarationRefused):
    """这句话在多处都能定位到。**摆候选，不挑。**"""

    def __init__(self, quote: str, candidates: Sequence[QuoteCandidate]) -> None:
        self.quote = quote
        self.candidates = list(candidates)
        lines = "\n".join(
            f"    第 {c.chapter_number:>3} 章 · 第 {c.para_index:>3} 段 · 第 {c.occurrence_k} 次"
            f"   {c.context}"
            for c in self.candidates
        )
        super().__init__(
            f"这句话在 {len(self.candidates)} 处都能定位到，系统不替你挑。\n"
            f"{lines}\n"
            "  把引语加长到只匹配一处（前后各多复制半句通常就够），\n"
            "  改完先试一次定位，看看还剩几处。"
        )


# ══════════════════════════════════════════════════════════════════════════
# 出参
# ══════════════════════════════════════════════════════════════════════════


class QuoteCandidate(BaseModel):
    """一句引语在当前正文里的一处命中。`locate()` 的出参。"""

    model_config = ConfigDict(frozen=True)

    chapter_id: str
    chapter_number: int
    snapshot_id: str
    para_index: int = Field(ge=0)
    occurrence_k: int = Field(ge=0)

    matched_text: str
    """**从正文里切出来的子串**，不是作者敲的那个串（M1 两者逐字节相等，M4 不等）。
    `put_evidence` 收的就是它。"""

    context: str
    """命中处前后各约 `CONTEXT_RADIUS` 字，**只给拒绝消息用，不是锚的一部分**。

    作者复制短句时会被拒好几次（「他终于明白」在 200 万字里出现 40 次），而这个产品
    最劝退的就是那种体验——把上下文摆出来，他才知道该往哪边加长。
    """


class Declaration(BaseModel):
    """一次成功的声明：一条边 + 它的依据 + 那条不可变的确认记录。"""

    model_config = ConfigDict(frozen=True)

    edge: Edge
    evidence: Evidence
    decision_id: str
    closed: list[Edge] = Field(default_factory=list)
    """被这条新边闭合的旧边（`valid_to_chapter` 被写上了）。"""

    retracted: list[Edge] = Field(default_factory=list)
    """被撤回的旧边（同章更正）。"""

    @property
    def valid_from(self) -> int:
        # 「valid_from 是 evidence 的函数」在类型层面就是这一行，不是一句口号：
        # 这个类里没有一个字段能让它是别的东西。
        return self.evidence.chapter_number


class FirstAppearance(BaseModel):
    """一次成功的「首现章」声明。**不是 `Declaration`**：它没有边，也没有证据行。

    出参是 `NodeRef` 不是 `Node`（`graph.models.NodeRef` 的完整论证）：这条声明最常用在
    「还没登场」的东西上，而那类节点的 props 里装的正是关于未来的东西。
    """

    model_config = ConfigDict(frozen=True)

    node: NodeRef
    chapter: int
    """算出来的首现章。**作者没有输入过它**——它是「这句引语落在哪一章」的产物。"""

    previous_chapter: int | None = None
    """改之前的值。`None` = 之前没标过（也就是「一开始就在」）。

    回执里带上它，是因为这条声明**会覆盖**上一次的答案，而覆盖掉的那个数
    在库里没有第二份（节点不是时态的）。作者至少要看得见自己改掉了什么。
    """

    decision_id: str


# ══════════════════════════════════════════════════════════════════════════
# Ledger
# ══════════════════════════════════════════════════════════════════════════


def _context(para: str, quote: str, occurrence_k: int) -> str:
    """命中处前后各 `CONTEXT_RADIUS` 字。

    用 `str.split` 而不是 `para.find` 循环：后者是「第 k 次出现」的第二份定义，而那个
    数**必须**和 `anchor._occurrences` 逐条一致（`tests/test_declare.py` 有一条断言把
    这两者钉在一起，含 "aaa"/"aa" 那种重叠情形）。`split` 与 `_occurrences` 同为
    非重叠、左起——`anchor._occurrences` 的 docstring 说的「与 str.count() 同义」就是它。
    """
    parts = para.split(quote)
    before = quote.join(parts[: occurrence_k + 1])
    after = quote.join(parts[occurrence_k + 1 :])
    head = "…" if len(before) > CONTEXT_RADIUS else ""
    tail = "…" if len(after) > CONTEXT_RADIUS else ""
    return f"{head}{before[-CONTEXT_RADIUS:]}{quote}{after[:CONTEXT_RADIUS]}{tail}"


class Ledger:
    """作者的声明入口。同时拿 `store` 和 `conn`（理由见模块 docstring）。

    这句原本写的是「**全仓库唯一**同时拿两个 handle 的地方」，**2026-08-10 删掉了「唯一」**：
    它早就不成立了（`extract/proposals.py::review_proposal` 一直是第二处），
    而 `corrections.py` 是第三处。三处的理由是同一条——图和 `decision_log`
    生命周期不同——所以要守的从来不是「只有一处」，是「**凡是改图的入口都必须同时写日志**」。
    真要钉，钉的是后者，而今天没有守卫钉它。

    入参里的 `who` / `secret` / `loc` / `of` **全是作者写的称呼原文，不是 node_id**
    ——同 `panel.constraints.resolve_cast` 收 `cast` 的形状（ARCHITECTURE §10.5 第 1 条）：
    调用方**没有机会**把歧义的「师兄」偷偷解析成第一个候选，因为它根本拿不到候选。

    `declare_node` / `declare_alias` **没有 `quote` 参数**：它们只是「这个东西存在」，
    没有任何一个数要算。**判据不是「是不是边」**——`declare_first_appearance` 写的是
    节点上的一个属性，它照样收引语，因为它要算的那个数（首现章）同样只能由证据决定。
    所以这条纪律的正确说法是：**签名里出现章号那一类的数，就必须收引语**（约束 10）。
    """

    def __init__(self, store: GraphStore, conn: Connection, project_id: str) -> None:
        self._store = store
        self._conn = conn
        self._project_id = project_id

    # ── 解析 ──────────────────────────────────────────────────────────────

    def _resolve_one(self, surface: str, *, want: NodeLabel | None = None) -> Node:
        resolution = self._store.resolve(self._project_id, [surface])[0]
        node = resolution.unique_node
        if node is None:
            if not resolution.hits:
                raise UnknownName(surface)
            raise AmbiguousName(surface, [NodeRef.of(h.node) for h in resolution.hits])
        if want is not None and node.label is not want:
            raise WrongLabel(surface, node.label, want)
        return node

    def _declared_node(self, label: NodeLabel, name: str) -> Node | None:
        """Return the existing declaration target without reaching through the graph boundary."""
        resolution = self._store.resolve(self._project_id, [name])[0]
        matches = {
            hit.node.id: hit.node
            for hit in resolution.hits
            if hit.node.label is label and hit.node.name == name
        }
        return next(iter(matches.values())) if len(matches) == 1 else None

    @staticmethod
    def _patched_props(previous: Node | None, props: NodeProps | None) -> NodeProps:
        """已有节点上的 props + 调用方**显式给过**的那几个字段。见 `declare_node`。

        `exclude_unset` 是判据：`NodeProps` 的每个字段默认值都是 `None`，不这么读的话
        「没提到 gender」和「把 gender 清空」在类型上完全一样，而前者是常态、
        后者今天没有任何调用方。
        """
        if previous is None:
            return props or NodeProps()
        if props is None:
            return previous.props
        return NodeProps.model_validate(
            {**previous.props.model_dump(), **props.model_dump(exclude_unset=True)}
        )

    def _edge_by_identity(self, spec: EdgeSpec) -> Edge | None:
        """Find the stored Canon edge an idempotent upsert would update, lifecycle included."""
        return self._store.find_edge_by_identity(spec)

    @staticmethod
    def _evidence_matches_candidate(
        evidence: Evidence,
        candidate: QuoteCandidate,
    ) -> bool:
        return (
            evidence.chapter_number == candidate.chapter_number
            and evidence.audit.chapter_snapshot_id == candidate.snapshot_id
            and evidence.audit.para_index == candidate.para_index
            and evidence.audit.quote_text == candidate.matched_text
            and evidence.relocate.chapter_id == candidate.chapter_id
            and evidence.relocate.occurrence_k == candidate.occurrence_k
        )

    def _bump_canon(self, expected_version: int) -> None:
        project.compare_and_bump_canon_version(
            self._conn,
            self._project_id,
            expected_version,
        )

    def _canon_transaction(self) -> AbstractContextManager[None]:
        """Require Ledger to own the transaction that couples a Canon write to its CAS."""
        if self._conn.in_transaction:
            raise RuntimeError(
                "Ledger 作者声明不允许已有外层事务：必须独占图事务，"
                "才能让 Canon 写入与版本 CAS 同成同败"
            )
        return self._store.transaction()

    # ── 定位（只读）────────────────────────────────────────────────────────

    def locate(self, quote: str) -> list[QuoteCandidate]:
        """这句引语在**当前**正文里的全部命中，按 (章, 段, 第几次) 升序。`nh locate` 用。

        只读：库里一个字节都不会变。判据是精确匹配——M1 的引语只有一个来源（作者从自己
        稿子里复制粘贴），difflib 是给 M4 抽取器的（ADR 0005 增长规则，见 text/anchor.py）。
        """
        out: list[QuoteCandidate] = []
        for ct in self._store.current_snapshots(self._project_id):
            paras = anchor.paragraphs(ct.text)
            for hit in anchor.find_all(paras, quote):
                out.append(
                    QuoteCandidate(
                        chapter_id=ct.chapter_id,
                        chapter_number=ct.number,
                        snapshot_id=ct.snapshot_id,
                        para_index=hit.para_index,
                        occurrence_k=hit.occurrence_k,
                        matched_text=hit.matched_text,
                        context=_context(paras[hit.para_index], hit.matched_text, hit.occurrence_k),
                    )
                )
        out.sort(key=lambda c: (c.chapter_number, c.para_index, c.occurrence_k))
        return out

    # ── 节点 / 别名 ───────────────────────────────────────────────────────

    def declare_node(
        self,
        label: NodeLabel,
        name: str,
        *,
        aliases: Sequence[str] = (),
        props: NodeProps | None = None,
        secret: SecretDetail | None = None,
    ) -> Node:
        """声明一个人物 / 地点 / 门派 / 秘密 / 物件。幂等（`upsert_node` 的键是 name）。

        `label is SECRET` 时 `secret` 必须给（`NodeSpec` 的 validator 强制：没有 secret 行
        的 Secret 节点在认知矩阵的默认列序里不成列）。

        ── `props` 是 patch 不是替换，且这条不是洁癖 ─────────────────────────

        `upsert_node` 撞上幂等键时是 `UPDATE props_json = :props`——**整列覆盖**。
        于是「再声明一次萧决，顺便标上首现章」会把抽取写进去的 `gender` /
        `personality` / `background` 悄悄抹掉，而没有任何一步会报错。
        这里只把**调用方显式给过的那几个字段**盖上去（`exclude_unset`，同
        `EventStore.update_profile` 收 `CharacterProfilePatch` 的形状），
        `props=None` 因此是「一个字段都不动」，不是「清空」。
        """
        with self._canon_transaction():
            current_version = project.require_canon_version(
                self._conn, self._project_id
            )
            previous = self._declared_node(label, name)
            node = self._store.upsert_node(
                NodeSpec(
                    project_id=self._project_id,
                    label=label,
                    name=name,
                    props=self._patched_props(previous, props),
                    secret=secret,
                )
            )
            stored = [
                self._store.add_alias(
                    AliasSpec(project_id=self._project_id, node_id=node.id, surface=surface)
                )
                for surface in aliases
            ]
            if previous != node or stored:
                self._bump_canon(current_version)
        # 日志在事务之后（理由见 `_declare_edge`）。节点侧没有引语：节点不是时态的。
        decisions.append(
            self._conn,
            project_id=self._project_id,
            kind=(
                DecisionKind.SECRET_DECLARE
                if label is NodeLabel.SECRET
                else DecisionKind.NODE_DECLARE
            ),
            decision=Verdict.ACCEPT,
            subject_name=node.name,
            payload={
                "label": label.value,
                "node_id": node.id,
                "aliases": [a.surface for a in stored],
            },
        )
        return node

    def declare_alias(
        self,
        *,
        of: str,
        surface: str,
        kind: AliasKind = AliasKind.ALIAS,
        usable_for_rules: bool = True,
    ) -> StoredAlias:
        """给一个已有的节点加一个称呼。

        别名**永不合并实体**（ADR 0004）：「顾姑娘 / 清音 / 魔尊」的差异编码的正是关系
        阶段和认知边界，是 canon 不是噪声。`kind=canonical` 会被 `AliasSpec` 拒——
        canonical 是 `upsert_node` 的独占物。
        """
        with self._canon_transaction():
            current_version = project.require_canon_version(
                self._conn, self._project_id
            )
            node = self._resolve_one(of)
            stored = self._store.add_alias(
                AliasSpec(
                    project_id=self._project_id,
                    node_id=node.id,
                    surface=surface,
                    kind=kind,
                    usable_for_rules=usable_for_rules,
                )
            )
            self._bump_canon(current_version)
        decisions.append(
            self._conn,
            project_id=self._project_id,
            kind=DecisionKind.ALIAS_MERGE,
            decision=Verdict.ACCEPT,
            subject_name=node.name,
            payload={
                "surface": stored.surface,
                "kind": stored.kind.value,
                "usable_for_rules": stored.usable_for_rules,
                "typed_surface": of,
            },
        )
        return stored

    # ── 边（唯一给 valid_from 赋值的地方）──────────────────────────────────
    #
    # ⚠️ **下面三条 2026-08-14 起没有任何作者入口了。**
    #
    # 作者的裁决：**「谁知道什么 / 谁以为什么 / 谁在哪儿」只走抽取那条路。** 手工那条是
    # 「你说，我记」，抽取那条是「我猜，你审」——两条路往同一批表里写同一种事实，就有两个
    # 真相源，而作者只会看见后者。所以删掉的是**入口**：
    #
    #     浏览器  中栏选区工具条 + 声明抽屉        （2026-08-14，commit 28e892e）
    #     HTTP    POST …/declare/{knows,believes,where} + POST …/locate
    #     CLI     nh declare {knows,believes,where}
    #
    # **方法本身留着，因为它们不是作者的路，是夹具的词汇**：
    #
    #     synth/build.py:413      M2 kill-gate 那本合成小册子就是用它建起来的，
    #                             ground truth 由建出来的图派生（EVAL_PROTOCOL）。
    #                             删它 = 改卷子，而协议是预注册的。
    #     scripts/seed_demo.py    demo.sh 的心跳
    #     tests/                  九个文件，四十余处「先造一条 KNOWS 边」
    #
    # 把这三个方法删掉，代价是重写 M2 的考卷装置，收益是零——**作者根本够不着它们**。
    # 哪天真要连库里这一层也拿掉，先想清楚 `synth/` 用什么替代，那是一次协议动作。
    #
    # ── 为什么下面那两个**不在**这一组里（这是这次改动的全部风险所在）──────────
    #
    # `declare_dead` 和 `declare_first_appearance` 长得像同一组，其实不是。
    #
    # **`declare_first_appearance` 仍是 R2 在生产上唯一的写入方**，而且**只能是**：
    # `first_appears_chapter` 主要用法是「这个东西我打算第 200 章才让它出场」——
    # 那是**作者的计划**，物理上不在已写文本里（ADR 0004：墙上那把枪是不是伏笔，
    # 取决于他第 200 章打不打算开枪）。模型读不出没写下来的意图。
    #
    # **`declare_dead` 2026-08-14 起不再唯一**：抽取器长出了 `kind="death"`
    # （`extract/models.py` 那段论证：模型在**封闭枚举**里挑一个，`value_key` 仍由
    # 引擎写死，铁律 2 没破）。它留下来是作为「改」的入口——模型漏了或判错时，
    # 作者手上得有一条路。端到端由 `tests/test_extractor_feeds_r3.py` 钉住。
    #
    # **这个坑这个仓库刚爬出来过**：`checks/__init__.py` 开头那段警告说的就是
    # 2026-08-02 到 08-13 之间那十一天——规则每天绿着，生产上结构性哑火。
    # 补法正是这两条入口。**别再把它们当成「手工声明的残留」删掉。**

    def declare_knows(self, *, who: str, secret: str, quote: str) -> Declaration:
        """「他在这段原文里知道了这个秘密」。章号由引语决定。**没有作者入口**（见上）。"""
        src = self._resolve_one(who)
        dst = self._resolve_one(secret, want=NodeLabel.SECRET)
        return self._declare_edge(
            kind=DecisionKind.KNOWS_DECLARE,
            src=src,
            dst=dst,
            type=EdgeType.KNOWS,
            props=EdgeProps(),
            typed_surface=who,
            quote=quote,
        )

    def declare_believes(
        self, *, who: str, secret: str, believed_value: str, quote: str
    ) -> Declaration:
        """「他以为的是另一个版本」。`believed_value` 进 `edge.props`。**没有作者入口。**"""
        src = self._resolve_one(who)
        dst = self._resolve_one(secret, want=NodeLabel.SECRET)
        return self._declare_edge(
            kind=DecisionKind.KNOWS_DECLARE,
            src=src,
            dst=dst,
            type=EdgeType.BELIEVES,
            props=EdgeProps(believed_value=believed_value),
            typed_surface=who,
            quote=quote,
        )

    def declare_where(self, *, who: str, loc: str, quote: str) -> Declaration:
        """「他在这段原文里到了这个地方」。`LOCATED_AT` 是 single_per_src，会自动闭合
        他上一个位置（`Declaration.closed`）。**没有作者入口。**"""
        src = self._resolve_one(who)
        dst = self._resolve_one(loc, want=NodeLabel.LOCATION)
        return self._declare_edge(
            kind=DecisionKind.LOCATED_DECLARE,
            src=src,
            dst=dst,
            type=EdgeType.LOCATED_AT,
            props=EdgeProps(),
            typed_surface=who,
            quote=quote,
        )

    def declare_dead(self, *, who: str, quote: str) -> Declaration:
        """「他在这段原文里死了」。R3 DEAD_SPEAKS 的生产写入方**之一**。

        **2026-08-14 起它不再是唯一的**：抽取器长出了 `kind="death"`（模型在封闭枚举里挑一个，`value_key` 仍由引擎写死），端到端由 `tests/test_extractor_feeds_r3.py` 钉住。**手工这条留着是「改」，不是「建」**。


        ── 三件必须一起发生的事，所以它们在一个方法里 ────────────────────────

        1. **生死维度得存在。** `StateDim` 是引擎的内部结构，作者不该、也没有入口手工
           建它（`AUTHORED_LABELS` 里没有它，那是有意的）。所以由这里
           `ensure_state_dim(HEALTH_DIM_KEY, …)` ——**按键不按名**，理由见那个方法。
        2. **边上要有机器键。** `EdgeProps.value_key = 'dead'` 是 `is_dead` 的判据；
           `value` 那个中文只给人看。**R3 永远不许去解析它**（死 / 陨落 / 坐化 / 兵解 ——
           那是「这句话是什么意思」，撞 ADR 0005 的铁律）。`value_key` 由引擎写死，
           不是从作者的字里认出来的，这条铁律才在结构上成立。
        3. **章号由引语算。** 走的是 `_declare_edge` 那唯一一处赋值（§5.9 / 约束 10）。

        `HAS_STATE` 的 exclusivity 是 `single_per_src_dst`，所以同一个维度上的旧值
        （比如上一次声明的状态）会被自动闭合，`Declaration.closed` 如实报告。

        **没有配对的「他活过来了」**：`HealthValue.ALIVE` 今天没有写入方。加它之前先想清楚
        「撤销一次说错了的死亡」和「他真的复活了」是两件事——前者是 `corrections.py`
        那一摊（撤回 + 写新的），后者才是这里的第二个动词。
        """
        src = self._resolve_one(who, want=NodeLabel.CHARACTER)
        dim = self._store.ensure_state_dim(self._project_id, HEALTH_DIM_KEY, HEALTH_DIM_NAME)
        return self._declare_edge(
            kind=DecisionKind.STATE_DECLARE,
            src=src,
            dst=dim,
            type=EdgeType.HAS_STATE,
            props=EdgeProps(value=DEAD_VALUE_TEXT, value_key=HealthValue.DEAD),
            typed_surface=who,
            quote=quote,
            extra_payload={"dim_key": HEALTH_DIM_KEY, "value_key": HealthValue.DEAD.value},
        )

    # ── 节点上的「首现章」（不是边，所以不在上面那一组里）────────────────────

    def declare_first_appearance(self, *, of: str, quote: str) -> FirstAppearance:
        """「他/它在这段原文里头一回露面」→ `node.props.first_appears_chapter`。

        R2 FUTURE_LEAK 和 R3 的「未登场角色开口说话」都读这个字段，而在这条方法之前
        **生产上没有任何东西写它**：两条规则于是结构上永远不可能开火。

        ── 为什么它收引语，不收一个数字 ──────────────────────────────────────

        因为它可以。首现章是「这个名字头一回出现在正文里」——那**是**一件有原文可指的事，
        所以约束 10 的那套照旧成立：作者从稿子里复制那句话，系统自己算出那是第几章，
        签名里没有一个位置能让他敲数字。

        **它够不着的那一半**：还没写到的实体（「幽泉窟第 200 章才首现」）没有引语可指——
        那个数只可能是作者的一次**决定**（同 PLANNED 的 `valid_from`，001_init.sql：
        「那不是回忆是决定」）。那条路今天只有 `POST /nodes` 的
        `first_appears_chapter` 字段，**浏览器上没有它的输入框**，而那是一个待裁决的
        产品问题，不是一个漏掉的表单——见那个字段的说明。

        ── 它不写 evidence 行 ────────────────────────────────────────────────

        `evidence` 是给时态**边**的（`valid_from` 靠它），而这里改的是节点的一个属性，
        节点不是时态的。依据落在 `decision_log` 那条不可变记录上（引语 + 章号 + 段号）。
        """
        node = self._resolve_one(of)
        cand = self._one_candidate(quote)
        with self._canon_transaction():
            current_version = project.require_canon_version(self._conn, self._project_id)
            previous = node.props.first_appears_chapter
            updated = self._store.set_first_appearance(
                self._project_id, node.id, cand.chapter_number
            )
            if previous != updated.props.first_appears_chapter:
                self._bump_canon(current_version)
        decision = decisions.append(
            self._conn,
            project_id=self._project_id,
            kind=DecisionKind.FIRST_APPEARANCE_DECLARE,
            decision=Verdict.ACCEPT,
            subject_name=node.name,
            # 定位后的原文子串，不是作者敲的那个串（同 `_declare_edge`，同一条理由）。
            quote_text=cand.matched_text,
            chapter_number=cand.chapter_number,
            para_index=cand.para_index,
            payload={
                "node_id": node.id,
                "label": node.label.value,
                "typed_surface": of,
                "occurrence_k": cand.occurrence_k,
                "previous": previous,
            },
        )
        return FirstAppearance(
            node=NodeRef.of(updated),
            chapter=cand.chapter_number,
            previous_chapter=previous,
            decision_id=decision.id,
        )

    def _declare_edge(
        self,
        *,
        kind: DecisionKind,
        src: Node,
        dst: Node,
        type: EdgeType,
        props: EdgeProps,
        typed_surface: str,
        quote: str,
        extra_payload: Mapping[str, Any] | None = None,
    ) -> Declaration:
        """上面四个声明的**唯一实现**。

        `extra_payload` 只往 `decision_log` 的 payload 里加键（`declare_dead` 用它记
        `dim_key` / `value_key`）。**它加不了 `valid_from` 那一类东西**——payload 是给
        重放读的旁注，边上写什么由上面那几行决定。

        为什么它必须只有一份：与 `graph/queries.py` 的时态过滤同理——写第二遍的那一次
        会忘掉「多于一个命中就拒绝」，而那条忘记的产物是一条 `valid_from` 错了的 CANON 边。

        ── 顺序：证据 + 边一个事务，日志最后。别改成日志先写 ────────────────

        `decision_log` 的三个触发器封死 INSERT / UPDATE / DELETE，**写错的一条永远删不掉**；
        而 `SupersedeConflict`（作者乱序声明）/ `IntegrityError` 是**预期异常**，概率远高于
        进程被 kill -9。日志先写 = 每一次拒绝都在那张不可变的表里留一条**假的 accept**，
        而它是全库唯一不可重建、也唯一必须不撒谎的资产。

        日志放最后 = 所有会失败的事都在日志之前失败完、事务整个回滚、一个字节都没写；
        代价只是微秒级崩溃窗口里丢一条确认记录，而那时边和证据仍在库里。

        另外：`decisions.append()` 自己 `conn.commit()`，**它进不了那个事务**（会把外层
        事务提前提交掉）。日志放最后同时避开了这颗雷。
        """
        cand = self._one_candidate(quote)
        # 到这一行为止库里一个字节都没变：歧义和找不到的代价是「什么都没发生」，
        # 不是「一条半成品」。
        with self._canon_transaction():
            current_version = project.require_canon_version(
                self._conn, self._project_id
            )
            identity = EdgeSpec(
                project_id=self._project_id,
                src=src.id,
                dst=dst.id,
                type=type,
                props=props,
                # 先用定位结果找到可能幂等的旧边；最终 spec 会再从 evidence 取这个值。
                valid_from_chapter=cand.chapter_number,
                information_scope=InformationScope.CANON,
                source=EdgeSource.AUTHOR,
            )
            previous = self._edge_by_identity(identity)
            previous_evidence = (
                self._store.get_evidence(
                    self._project_id,
                    previous.evidence_id,
                )
                if previous is not None and previous.evidence_id is not None
                else None
            )
            ev = (
                previous_evidence
                if previous_evidence is not None
                and self._evidence_matches_candidate(previous_evidence, cand)
                else self._store.put_evidence(
                    EvidenceSpec(
                        project_id=self._project_id,
                        chapter_snapshot_id=cand.snapshot_id,
                        para_index=cand.para_index,
                        occurrence_k=cand.occurrence_k,
                        quote_text=cand.matched_text,
                    )
                )
            )
            spec = identity.model_copy(
                update={
                    # ★ 全系统作者路径上唯一一次给 valid_from 赋值的地方（§5.9 / 约束 10）。
                    #   它只可能来自证据；作者输入进不到这一行。
                    "valid_from_chapter": ev.chapter_number,
                    "evidence_id": ev.id,
                }
            )
            result = self._store.upsert_edge(spec)
            if result.created or previous != result.edge:
                self._bump_canon(current_version)

        decision = decisions.append(
            self._conn,
            project_id=self._project_id,
            kind=kind,
            decision=Verdict.ACCEPT,
            # 人名，不是 ID（§5.7 原文）：ID 随重抽全部作废，重放不回去的日志等于没有日志。
            subject_name=src.name,
            # **必须是 ev.audit.quote_text（定位后的原文子串），不是作者敲的那个串。**
            # `decisions.quote_hash()` 不做任何归一化（它自己的 docstring 说了理由），
            # 所以只有传原文子串，`decision_log.quote_sha256` 才逐字节等于
            # `evidence.quote_sha256`。传错两边哈希永远不等，而**没有任何东西会报错**——
            # 直到某次 schema 变更要重放，才发现 3000 条确认一条都对不上号。
            quote_text=ev.audit.quote_text,
            chapter_number=ev.chapter_number,
            para_index=ev.audit.para_index,
            payload={
                "edge_type": type.value,
                "scope": InformationScope.CANON.value,
                "object_name": dst.name,
                # ★ 两端的 id 和名字**一起**记（不是取代名字，§5.7 那条仍然成立）。
                #   名字是给重放用的，id 是给「跳回去改这一格」用的：一条声明错了的
                #   「知道 / 以为」改得掉（`corrections.correct_knowledge` 就是为它写的），
                #   而 `activity._decision_jump` 只能从这两个 id 拼出那个坐标——从
                #   `subject_name` / `object_name` 反查是「按人名认」，歧义时必然认错人
                #   （「师兄」在一章里可能指 8 个人），而认错的产物是改了另一个人的认知。
                #   缺了它们，这一行会退到兜底坐标，于是 `endpoints` 空 ——
                #   而那个空元组在本仓的意思是「今天没有任何路由能改这个」，是假话。
                #   **旧库里的行没有这两个键**（`decision_log` 只增不改，重写不了），
                #   那些行照旧退到兜底坐标：老书降级成「只能跳到那一章」，不报错。
                "subject_id": src.id,
                "object_id": dst.id,
                "typed_surface": typed_surface,
                "evidence_id": ev.id,
                "occurrence_k": ev.relocate.occurrence_k,
                **dict(extra_payload or {}),
            },
        )
        return Declaration(
            edge=result.edge,
            evidence=ev,
            decision_id=decision.id,
            closed=result.closed,
            retracted=result.retracted,
        )

    def _one_candidate(self, quote: str) -> QuoteCandidate:
        """恰好一处命中才放行。**多于一处绝不取第一个**（§5.9）。"""
        cands = self.locate(quote)
        if not cands:
            raise QuoteNotFound(quote)
        if len(cands) > 1:
            raise AmbiguousQuote(quote, cands)
        return cands[0]
