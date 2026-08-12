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

- `declare_state`（HAS_STATE / StateDim）：读者是 R3，R3 在 M3。
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

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from . import decisions, project
from .db import Connection
from .decisions import DecisionKind, Verdict
from .graph import (
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
    """


class UnknownName(DeclarationRefused):
    """这个称呼在本项目里解析不到任何节点。"""

    def __init__(self, surface: str) -> None:
        self.surface = surface
        super().__init__(
            f"没有叫「{surface}」的东西。先 nh declare character / place / secret 声明它，"
            "或者用 nh declare alias 把这个称呼挂到已有的节点上"
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
            "  M1 只做逐字精确匹配（标点、空格、全半角都算）——从稿子里复制粘贴，别手打。\n"
            "  也可能是这一章还没进库：先跑 nh sync。"
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
            "  把引语加长到只匹配一处（前后各多复制半句通常就够）。用 nh locate 先试。"
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

    `declare_node` / `declare_alias` **没有 `quote` 参数**：节点不是时态的，只有边有
    `valid_from`。「哪些声明要引语」在签名上因此是自明的——要 `valid_from` 的才要引语。
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
                    props=props or NodeProps(),
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

    def declare_knows(self, *, who: str, secret: str, quote: str) -> Declaration:
        """「他在这段原文里知道了这个秘密」。章号由引语决定，你没有输入过它。"""
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
        """「他以为的是另一个版本」。`believed_value` 进 `edge.props`，面板直接渲染它。"""
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
        """「他在这段原文里到了这个地方」。`LOCATED_AT` 的 exclusivity 是 single_per_src，
        所以这一条会自动闭合他上一个位置（`Declaration.closed`）。"""
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
    ) -> Declaration:
        """上面三个声明的**唯一实现**。

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
