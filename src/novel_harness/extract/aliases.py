"""identity-first 的抽取别名（ADR 0031 / 计划 Task 12）。

── 为什么别名要先于事件解析 ──────────────────────────────────────────────
模型输出一个「新」称呼（「凤辣子」）时，它可能只是一个已知人物（王熙凤）的
别名。若按名字直接 resolve，「凤辣子」解析不到 → 整条事件被丢（unknown）或
被误判成「新人物」（new_character bucket）→ 于是同一个已出场的人物被重复
登记两遍，而 R2/R3/矩阵全指着错误的一列。**先定身份，再落事件。**

── 自动条件（§4.5，全部成立才自动 ACTIVE）────────────────────────────────
1. 目标称呼唯一解析到同项目 Character（`character_surface` → `resolve_one`）；
2. alias surface 当前未映射（全项目 ACTIVE alias 里没有它）；
3. 证据 quote 逐字包含目标称呼和 alias surface；
4. 同一 run 没有另一个候选把该 surface 指向别人；
5. `confidence >= 0.90`。

任一不满足 → `alias_resolution` 聚类提案，不自动选择。自动行 `source=extractor`
并保留证据（`alias_evidence`）。本模块的 SQL 只住 graph 层。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Connection
from ..graph import GraphStore, NodeLabel, NodeRef
from ..ids import EntityType, new_id
from .models import RawChapterAnalysis

__all__ = [
    "AliasResolution",
    "IdentityPassResult",
    "resolve_analysis_identity",
]

AUTO_ALIAS_MIN_CONFIDENCE: float = 0.90


@dataclass(frozen=True, slots=True)
class AliasResolution:
    """一条调用方已确认归属的称呼（作者点了提案 / 自动条件满足）。

    `target_node_id` = 这个人物的确定 id；`alias_id` = 落库后的别名行 id
    （新自动 ACTIVE 行 / 复用的既有行）。replay 里吃的就是这份确定结果。
    """

    surface: str
    target_node_id: str
    alias_id: str
    evidence_id: str | None
    fresh: bool
    """True = 本次新写了 ACTIVE extractor alias；False = 复用了既有行。"""


@dataclass(frozen=True, slots=True)
class IdentityPassResult:
    """两次解析之间落地的身份决定：谁已登记、谁进了提案。"""

    auto_aliases: tuple[AliasResolution, ...] = ()
    ambiguous_candidates: tuple[tuple[str, tuple[NodeRef, ...]], ...] = ()
    """`(surface, 多个可能人物)`——模型提议了新称呼，但目标唯一性不满足。"""
    blocked_surfaces: tuple[tuple[str, str], ...] = ()
    """`(surface, reason)`：自动条件不满足又没进 ambiguous 的那些失败称呼。"""

    @property
    def changed_surfaces(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {r.surface for r in self.auto_aliases}
                | {s for s, _ in self.ambiguous_candidates}
            )
        )


def resolve_analysis_identity(
    conn: Connection,
    graph: GraphStore,
    project_id: str,
    analysis: RawChapterAnalysis,
    *,
    chapter_id: str,
    chapter_snapshot_id: str,
    source_generation: int,
    extraction_application_id: str | None,
) -> IdentityPassResult:
    """把模型提议的称呼**先**落成身份决定，返回本次分析可用的 resolution_map 覆盖。

    只在**新自动 ACTIVE** 时写库（复用路径不动行、只读）；歧义 / 条件不满足
    只进提案清单，绝不替作者挑。所有写入带证据锚（`alias_evidence`）。
    """
    auto: list[AliasResolution] = []
    ambiguous: list[tuple[str, tuple[NodeRef, ...]]] = []
    blocked: list[tuple[str, str]] = []

    # 全项目 ACTIVE surface 集合：判「当前未映射」的原料（查询一次）。
    # SQL 只住 graph 层（arch guard / GRAPH_TABLES 清单）。
    from ..graph import queries as _gq

    active_surfaces: set[str] = _gq.active_alias_surfaces(conn, project_id)

    # 同一 run 里每个 surface 只能指向一个人（§4.5 条件 4）。
    run_targets: dict[str, str] = {}

    for candidate in analysis.aliases:
        surface = candidate.surface.strip()
        if not surface:
            continue
        target = _resolve_target(graph, project_id, candidate.character_surface)
        if target is None:
            blocked.append((surface, "目标人物解析不到或歧义"))
            continue
        existing = _existing_active_alias(conn, project_id, surface)
        if existing is not None:
            if existing["ambiguous"]:
                # 同一 surface 已被多个 ACTIVE alias 占用（跨人物「师兄」式歧义）。
                ambiguous.append((surface, (target,)))
            elif existing["node_id"] == target.id:
                # 已登记且指向同一个人：复用（Task 12 只保证「不重复登记」）。
                auto.append(
                    AliasResolution(
                        surface=surface,
                        target_node_id=target.id,
                        alias_id=str(existing["id"]),
                        evidence_id=None,
                        fresh=False,
                    )
                )
            else:
                # 同一 surface 已被另一个 ACTIVE alias 占用（跨人物）→ 歧义。
                ambiguous.append((surface, (target,)))
            continue
        if _same_run_conflict(run_targets, surface, target.id):
            ambiguous.append((surface, (target,)))
            continue
        if not _quote_mentions(candidate.quote, surface, candidate.character_surface):
            blocked.append((surface, "证据 quote 没逐字包含称呼与目标"))
            continue
        if candidate.confidence < AUTO_ALIAS_MIN_CONFIDENCE:
            ambiguous.append((surface, (target,)))
            continue
        if surface in active_surfaces:
            blocked.append((surface, "surface 已被其他人物占用"))
            continue

        # 全部自动条件满足：写 extractor alias + alias_evidence。
        alias_id, evidence_id = _auto_alias(
            conn,
            project_id=project_id,
            surface=surface,
            target_node_id=target.id,
            evidence_text=candidate.quote,
            chapter_id=chapter_id,
            chapter_snapshot_id=chapter_snapshot_id,
            source_generation=source_generation,
            extraction_application_id=extraction_application_id,
        )
        run_targets[surface] = target.id
        auto.append(
            AliasResolution(
                surface=surface,
                target_node_id=target.id,
                alias_id=alias_id,
                evidence_id=evidence_id,
                fresh=True,
            )
        )

    return IdentityPassResult(
        auto_aliases=tuple(auto),
        ambiguous_candidates=tuple(ambiguous),
        blocked_surfaces=tuple(blocked),
    )


def _resolve_target(
    graph: GraphStore, project_id: str, surface: str
) -> NodeRef | None:
    try:
        resolved = graph.resolve(project_id, [surface])
    except Exception:
        return None
    if not resolved or not resolved[0].hits:
        return None
    hits = [h for h in resolved[0].hits if h.node.label is NodeLabel.CHARACTER]
    if len(hits) != 1:
        return None
    return hits[0].node


def _existing_active_alias(conn: Connection, project_id: str, surface: str) -> dict | None:
    """查 surface 的 ACTIVE 别名。`ambiguous=True` = 它指向 >1 个不同人物。

    022 允许同名 alias 跨人物并存（「师兄」→ 两个人是真事，不能用全局唯一键抹掉）。
    这里把「已指向同一个 target」的复用和「跨人物歧义」分开，「不替你挑」那条铁律
    在后者上照旧成立。SQL 只住 graph 层。
    """
    from ..graph import queries as _gq

    return _gq.existing_active_alias(conn, project_id, surface)


def _same_run_conflict(run_targets: dict[str, str], surface: str, target: str) -> bool:
    seen = run_targets.get(surface)
    if seen is None:
        return False
    return seen != target


def _quote_mentions(quote: str, *surfaces: str) -> bool:
    return all(s in quote for s in surfaces if s)


def _auto_alias(
    conn: Connection,
    *,
    project_id: str,
    surface: str,
    target_node_id: str,
    evidence_text: str,
    chapter_id: str,
    chapter_snapshot_id: str,
    source_generation: int,
    extraction_application_id: str | None,
) -> tuple[str, str | None]:
    from novel_harness.graph import queries

    alias_id = new_id(EntityType.ALIAS, project_id)
    evidence_id = new_id(EntityType.EVIDENCE, project_id)
    # 证据：逐字 quote 才可落锚（§6.5：别名证据也要锚）。SQL 只住 graph 层。
    from ..graph import queries as _gq

    _gq.insert_alias_evidence_quote(
        conn,
        evidence_id=evidence_id,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_snapshot_id=chapter_snapshot_id,
        quote_text=evidence_text,
        para_index=0,
    )
    queries.insert_alias(
        conn,
        alias_id,
        project_id=project_id,
        node_id=target_node_id,
        surface=surface,
        kind=__import__("novel_harness.graph.models", fromlist=["AliasKind"]).AliasKind.ALIAS,
        usable_for_rules=True,
        source="extractor",
    )
    conn.execute(
        """
        INSERT INTO alias_evidence (
            alias_id, evidence_id, source_snapshot_id, source_generation,
            extraction_application_id, status
        ) VALUES (?, ?, ?, ?, ?, 'FRESH')
        """,
        (alias_id, evidence_id, chapter_snapshot_id, source_generation, extraction_application_id),
    )
    return alias_id, evidence_id
