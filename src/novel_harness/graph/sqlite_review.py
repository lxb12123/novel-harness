"""SQLite edge hydration and lossless PROVISIONAL→CANON promotion."""

from __future__ import annotations

from collections.abc import Sequence

from ..db import Connection
from . import queries
from .models import (
    Edge,
    EdgeProps,
    EdgeSpec,
    EdgeStatus,
    Evidence,
    EvidenceStatus,
    InformationScope,
    NodeRef,
)
from .review_store import EdgeReviewValidationError, ReviewableEdge
from .store import GraphStore


class SqliteEdgeReviewStore:
    def __init__(self, conn: Connection, graph: GraphStore) -> None:
        self._conn = conn
        self._graph = graph

    @staticmethod
    def _selection(edge_ids: Sequence[str]) -> tuple[str, ...]:
        ids = tuple(edge_ids)
        if not ids:
            raise EdgeReviewValidationError("边集合不能为空")
        if any(not edge_id for edge_id in ids):
            raise EdgeReviewValidationError("边 ID 不能为空")
        if len(ids) != len(set(ids)):
            raise EdgeReviewValidationError("边集合不能含重复 ID")
        return ids

    def _with_nodes(
        self,
        project_id: str,
        edges: Sequence[Edge],
    ) -> tuple[ReviewableEdge, ...]:
        nodes = queries.fetch_nodes(
            self._conn,
            project_id,
            {node_id for edge in edges for node_id in (edge.src, edge.dst)},
        )
        out: list[ReviewableEdge] = []
        for edge in edges:
            src = nodes.get(edge.src)
            dst = nodes.get(edge.dst)
            if src is None or dst is None:
                raise EdgeReviewValidationError(
                    f"边 {edge.id} 的端点缺失或跨项目：{edge.src}/{edge.dst}"
                )
            out.append(
                ReviewableEdge(edge=edge, src=NodeRef.of(src), dst=NodeRef.of(dst))
            )
        return tuple(out)

    def hydrate_provisional(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]:
        ids = self._selection(edge_ids)
        edges: list[Edge] = []
        for edge_id in ids:
            try:
                edge = queries.fetch_edge(self._conn, edge_id)
            except LookupError as exc:
                raise EdgeReviewValidationError(f"边不存在：{edge_id}") from exc
            if edge.project_id != project_id:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 属于项目 {edge.project_id}，不是 {project_id}"
                )
            if edge.information_scope is not InformationScope.PROVISIONAL:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 必须是 PROVISIONAL，当前是 {edge.information_scope.value}"
                )
            if edge.status is not EdgeStatus.ACTIVE:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 必须是 ACTIVE，当前是 {edge.status.value}"
                )
            if edge.valid_to_chapter is not None:
                raise EdgeReviewValidationError(f"边 {edge_id} 已闭合，不能提升")
            if (
                edge.evidence_id is None
                or edge.evidence_status is not EvidenceStatus.FRESH
            ):
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 必须有 FRESH evidence，当前是 "
                    f"{edge.evidence_status.value}"
                )
            edges.append(edge)

        return self._with_nodes(project_id, edges)

    def hydrate_current_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]:
        ids = self._selection(edge_ids)
        edges: list[Edge] = []
        for edge_id in ids:
            try:
                edge = queries.fetch_edge(self._conn, edge_id)
            except LookupError as exc:
                raise EdgeReviewValidationError(
                    f"current Canon 边不存在：{edge_id}"
                ) from exc
            if edge.project_id != project_id:
                raise EdgeReviewValidationError(
                    f"current Canon 边 {edge_id} 属于项目 {edge.project_id}，"
                    f"不是 {project_id}"
                )
            if not edge.is_current or edge.evidence_status is EvidenceStatus.STALE:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 已不是 ACTIVE、未闭合、非 STALE 的 current Canon"
                )
            edges.append(edge)
        return self._with_nodes(project_id, edges)

    def clone_to_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]:
        with self._graph.transaction():
            sources = self.hydrate_provisional(project_id, edge_ids)
            promoted: list[ReviewableEdge] = []
            for item in sources:
                edge = self._graph.upsert_edge(
                    EdgeSpec(
                        project_id=project_id,
                        src=item.edge.src,
                        dst=item.edge.dst,
                        type=item.edge.type,
                        props=item.edge.props,
                        valid_from_chapter=item.edge.valid_from_chapter,
                        information_scope=InformationScope.CANON,
                        confidence=item.edge.confidence,
                        source=item.edge.source,
                        evidence_id=item.edge.evidence_id,
                    )
                ).edge
                promoted.append(
                    ReviewableEdge(edge=edge, src=item.src, dst=item.dst)
                )
            return tuple(promoted)

    # ── 作者事后改一条已生效事实（corrections.py 的四个零件）──────────────────

    def node_refs(
        self,
        project_id: str,
        node_ids: Sequence[str],
    ) -> tuple[NodeRef, ...]:
        ids = self._selection(node_ids)
        nodes = queries.fetch_nodes(self._conn, project_id, set(ids))
        out: list[NodeRef] = []
        for node_id in ids:
            node = nodes.get(node_id)
            if node is None:
                raise EdgeReviewValidationError(f"节点不存在或跨项目：{node_id}")
            out.append(NodeRef.of(node))
        return tuple(out)

    def current_knowledge(
        self,
        project_id: str,
        character_id: str,
        secret_id: str,
    ) -> tuple[ReviewableEdge, ...]:
        edges = queries.current_knowledge_edges(
            self._conn, project_id, character_id, secret_id
        )
        return self._with_nodes(project_id, edges) if edges else ()

    def retract_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]:
        with self._graph.transaction():
            sources = self.hydrate_current_canon(project_id, edge_ids)
            return tuple(
                ReviewableEdge(
                    edge=queries.retract_edge(self._conn, item.edge.id),
                    src=item.src,
                    dst=item.dst,
                )
                for item in sources
            )

    def restore_canon(
        self,
        project_id: str,
        edge_id: str,
        *,
        props: EdgeProps,
    ) -> ReviewableEdge:
        with self._graph.transaction():
            try:
                edge = queries.fetch_edge(self._conn, edge_id)
            except LookupError as exc:
                raise EdgeReviewValidationError(f"边不存在：{edge_id}") from exc
            if edge.project_id != project_id:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 属于项目 {edge.project_id}，不是 {project_id}"
                )
            if (
                edge.information_scope is not InformationScope.CANON
                or edge.status is not EdgeStatus.RETRACTED
                or edge.valid_to_chapter is not None
            ):
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 不是一条未闭合的 RETRACTED CANON 边，不能改回来"
                )
            if edge.evidence_status is EvidenceStatus.STALE:
                raise EdgeReviewValidationError(f"边 {edge_id} 的依据已变更（STALE），不能改回来")
            spec = EdgeSpec(
                project_id=project_id,
                src=edge.src,
                dst=edge.dst,
                type=edge.type,
                props=props,
                valid_from_chapter=edge.valid_from_chapter,
                information_scope=InformationScope.CANON,
                confidence=edge.confidence,
                source=edge.source,
                evidence_id=edge.evidence_id,
            )
            # 复活一条边和插一条边一样会制造重叠区间，所以走的是 supersede 用的**同一个**
            # 冲突搜索（find_conflicts 只看 ACTIVE，所以它自己不会出现在结果里）。
            # 这里故意不闭合任何东西：作者的动作是「改回来」，不是「从今天起换一个值」。
            conflicts = queries.find_conflicts(
                self._conn, spec, queries.exclusivity_of(self._conn, edge.type)
            )
            if conflicts:
                raise EdgeReviewValidationError(
                    f"边 {edge_id} 改不回来：{[c.id for c in conflicts]} 已经占着同一段区间"
                )
            queries.update_edge_facets(
                self._conn,
                edge.id,
                spec,
                EvidenceStatus.NONE if spec.evidence_id is None else EvidenceStatus.FRESH,
            )
            restored = queries.activate_edge(self._conn, edge.id)
            return self._with_nodes(project_id, [restored])[0]

    def evidence(self, project_id: str, evidence_id: str) -> Evidence:
        try:
            evidence = queries.fetch_evidence(self._conn, evidence_id)
        except LookupError as exc:
            raise EdgeReviewValidationError(f"evidence 不存在：{evidence_id}") from exc
        if evidence.project_id != project_id:
            raise EdgeReviewValidationError(
                f"evidence {evidence_id} 属于 {evidence.project_id}，不是 {project_id}"
            )
        return evidence
