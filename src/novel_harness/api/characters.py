"""人物基础信息 + 别名生命周期（Task 11 / ADR 0031 / §4.5 / §6.5）。

界面固定为「本名 + 一排别名 chip」。canonical 不显示为 chip、不能通过别名接口改；
机器自动的 alias 显示轻量「自动」标记 + 证据入口；作者改了它 → 新 author 派生行
（原文 `derived_from_alias_id` 指回），原机器证据保留在历史。

**所有写带 `expected_canon_version`**：别名变更 bump canon version 并写
decision log（§5 022），作者拿旧版本表单改 → 409。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import project as project_mod
from ..db import Connection
from ..graph import (
    AliasKind,
    CanonEdgeRefused,
    NodeLabel,
    StoredAlias,
)
from ..graph.models import NodeRef
from ..graph.queries import fetch_node
from ..graph.store import NodeNotFound
from .deps import get_conn, get_event_store, get_store, load_project

router = APIRouter()


class CharacterBasicInfo(BaseModel):
    """人物基础信息出参（§4.5）：本名 + 全部 ACTIVE 别名（canonical 在 `character.name`）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    character: NodeRef
    profile: Any | None = None
    aliases: tuple[StoredAlias, ...] = ()


class AliasCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1)
    kind: AliasKind = AliasKind.ALIAS
    usable_for_rules: bool = True
    expected_canon_version: int = Field(ge=0)


class AliasEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str | None = None
    usable_for_rules: bool | None = None
    expected_canon_version: int = Field(ge=0)


class AliasReassign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_character_id: str = Field(min_length=1)
    expected_canon_version: int = Field(ge=0)


def _character_node(
    conn: Connection, project_id: str, character_id: str
) -> Any:
    node = fetch_node(conn, project_id, character_id)
    if node is None:
        raise HTTPException(404, {"error": "character_not_found", "character_id": character_id})
    if node.label is not NodeLabel.CHARACTER:
        raise HTTPException(422, {"error": "not_a_character", "character_id": character_id})
    return node


@router.get(
    "/api/projects/{project_id}/characters/{character_id}/profile",
    response_model=CharacterBasicInfo,
)
def character_profile(
    character_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    event_store: Any = Depends(get_event_store),
    proj: Any = Depends(load_project),
) -> CharacterBasicInfo:
    """一个人物的基础信息：本名 + 别名。**用精确 character ID，不用屏幕上的名字。**"""
    node = _character_node(conn, proj.id, character_id)
    aliases = store.aliases_of(proj.id, character_id)
    profile = None
    try:
        profile = event_store.profile(proj.id, character_id)
    except Exception:
        profile = None
    return CharacterBasicInfo(
        character=NodeRef(id=node.id, label=node.label.value, name=node.name),
        profile=profile,
        aliases=tuple(a for a in aliases if a.kind is not AliasKind.CANONICAL),
    )


@router.post("/api/projects/{project_id}/characters/{character_id}/aliases")
def create_character_alias(
    character_id: str,
    body: AliasCreate,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> StoredAlias:
    """给一个人物加别名（`+ 添加`）。canonical 拒；撞 `expected_canon_version` 409。"""
    _character_node(conn, proj.id, character_id)
    _require_canon(conn, proj.id, body.expected_canon_version)
    from ..graph.models import AliasSpec

    try:
        stored = store.add_alias(
            AliasSpec(
                project_id=proj.id,
                node_id=character_id,
                surface=body.surface,
                kind=body.kind,
                usable_for_rules=body.usable_for_rules,
            )
        )
    except Exception as exc:
        raise HTTPException(422, {"error": "bad_request", "message": str(exc)}) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return stored


@router.patch("/api/projects/{project_id}/aliases/{alias_id}")
def edit_alias(
    alias_id: str,
    body: AliasEdit,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> StoredAlias:
    """改 surface / usable。机器 alias 在此变成 author 派生行。"""
    _require_canon(conn, proj.id, body.expected_canon_version)
    try:
        stored = store.edit_alias(
            proj.id, alias_id, surface=body.surface, usable_for_rules=body.usable_for_rules
        )
    except (NodeNotFound, CanonEdgeRefused) as exc:
        raise HTTPException(404, {"error": "alias_not_found", "message": str(exc)}) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return stored


@router.post("/api/projects/{project_id}/aliases/{alias_id}/reassign")
def reassign_alias(
    alias_id: str,
    body: AliasReassign,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> StoredAlias:
    """改归属：旧 alias RETRACTED，目标 Character 新建 ACTIVE。"""
    _require_canon(conn, proj.id, body.expected_canon_version)
    try:
        stored = store.reassign_alias(proj.id, alias_id, to_node_id=body.to_character_id)
    except (NodeNotFound, CanonEdgeRefused) as exc:
        raise HTTPException(404, {"error": "alias_not_found", "message": str(exc)}) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return stored


@router.delete("/api/projects/{project_id}/aliases/{alias_id}")
def delete_alias(
    alias_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> dict[str, str]:
    """软撤回一条别名（§6.5 DELETE）。"""
    try:
        store.retract_alias(proj.id, alias_id)
    except (NodeNotFound, CanonEdgeRefused) as exc:
        raise HTTPException(404, {"error": "alias_not_found", "message": str(exc)}) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return {"id": alias_id, "status": "RETRACTED"}


def _require_canon(conn: Connection, project_id: str, expected: int) -> None:
    current = project_mod.require_canon_version(conn, project_id)
    if expected != current:
        raise HTTPException(
            409,
            {
                "error": "stale_canon_version",
                "expected": expected,
                "current": current,
            },
        )


def _bump_canon(conn: Connection, project_id: str) -> int:
    return project_mod.compare_and_bump_canon_version(
        conn, project_id, project_mod.require_canon_version(conn, project_id)
    )
