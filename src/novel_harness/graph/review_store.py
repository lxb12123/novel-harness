"""Narrow graph-boundary contracts used by M4 author review."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .models import Edge, Evidence, NodeRef


class EdgeReviewError(Exception):
    """Base class for review-time edge repository failures."""


class EdgeReviewValidationError(EdgeReviewError, ValueError):
    """A requested edge set is not wholly promotable provisional state."""


class ReviewableEdge(BaseModel):
    """A typed edge plus the display-safe names of both endpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge: Edge
    src: NodeRef
    dst: NodeRef


@runtime_checkable
class EdgeReviewStore(Protocol):
    def hydrate_provisional(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]: ...

    def clone_to_canon(
        self,
        project_id: str,
        edge_ids: Sequence[str],
    ) -> tuple[ReviewableEdge, ...]: ...

    def evidence(self, project_id: str, evidence_id: str) -> Evidence: ...

