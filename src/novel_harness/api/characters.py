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
    NodeUsage,
    StoredAlias,
)
from ..graph.models import NodeRef
from ..graph.queries import fetch_node
from ..graph.store import NodeInUse, NodeNotFound, StoreError
from .deps import get_conn, get_event_store, get_store, load_project

router = APIRouter()


class CharacterBasicInfo(BaseModel):
    """人物基础信息出参（§4.5）：本名 + 全部 ACTIVE 别名（canonical 在 `character.name`）。

    `canon_version` 是编辑弹层下一次写动作的 `expected_canon_version`——它跟着
    **这次读取时**的项目版本走（`proj.canon_version`），前端不用自己去猜。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    character: NodeRef
    profile: Any | None = None
    aliases: tuple[StoredAlias, ...] = ()
    canon_version: int = Field(default=0, ge=0)


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


class NodeRename(BaseModel):
    """给花名册里那一条改个显示名。

    **入参里没有 label**：改名不改类别。一个建错类别的条目（把地点建成了人物）
    该走删除再新建，而不是原地变形——原地变形会让挂在它身上的边和名单在一瞬间
    变成「一个人物在另一个人物里」这种讲不通的东西。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    expected_canon_version: int = Field(ge=0)


class NodeDeleted(BaseModel):
    """删掉一条花名册条目之后的回执。

    带着 `usage`（删之前数出来的那份，全零）**是为了让回执说得出「删掉的是一个
    什么都没挂的条目」**——同 `delete_chapter` 那条路的做法。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    usage: NodeUsage


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
        canon_version=project_mod.require_canon_version(conn, proj.id),
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


# ══════════════════════════════════════════════════════════════════════════
# 花名册条目：改名 / 删除
#
# **它们是「抽取自动建人物」的配套，不是可选项**（ADR 0020 补记，2026-08-25）：
# 模型会认错——真书上的实例是「袭人」（满篇「寒气袭人」）。自动建 + 不能删 = 单向阀，
# 那个错会永远留在库里往上下文里塞噪声，而作者没有任何办法清掉它。
#
# 两条都带 `expected_canon_version`（同别名那几条）：作者拿着一份旧花名册点删除时
# 收到的是 409，不是「删掉了一个他没看见的、刚被抽取改过的东西」。
# ══════════════════════════════════════════════════════════════════════════


@router.patch("/api/projects/{project_id}/nodes/{node_id}")
def rename_node(
    node_id: str,
    body: NodeRename,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> NodeRef:
    """改花名册里那一条的显示名。canonical 别名跟着一起改（同一个事务）。"""
    _require_canon(conn, proj.id, body.expected_canon_version)
    try:
        node = store.rename_node(proj.id, node_id, body.name)
    except NodeNotFound as exc:
        raise HTTPException(404, {"error": "node_not_found", "message": str(exc)}) from exc
    except StoreError as exc:
        # 撞了同名、或者名字是空白。**这句话原样摆给作者**，所以 store 那一侧的
        # 措辞是中文的（同 `corrections.py` 那条「不许转发别人的 str(exc)」的道理，
        # 只是这儿转发的是本仓库自己写给作者的那一句）。
        raise HTTPException(409, {"error": "rename_refused", "message": str(exc)}) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return NodeRef.of(node)


@router.delete("/api/projects/{project_id}/nodes/{node_id}", response_model=NodeDeleted)
def delete_node(
    node_id: str,
    expected_canon_version: int,
    conn: Annotated[Connection, Depends(get_conn)],
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> NodeDeleted:
    """删掉花名册里的一条。**有关系或情节引着它就拒绝**，并把挡路的数给作者看。

    拒绝而不是连带删除：引擎记住的东西是这个产品唯一的资产，删一个人的时候顺手
    把他参与过的每一条关系和每一份名单带走，作者在按下那颗按钮的一瞬间不会知道
    自己失去了什么（`NodeUsage` 的完整论证在那儿）。

    `expected_canon_version` 走**查询参数**：DELETE 的请求体在各家 HTTP 客户端上
    支持得参差不齐（fetch 里带 body 的 DELETE 不是所有代理都转发）。
    """
    _require_canon(conn, proj.id, expected_canon_version)
    try:
        usage = store.delete_node(proj.id, node_id)
    except NodeNotFound as exc:
        raise HTTPException(404, {"error": "node_not_found", "message": str(exc)}) from exc
    except NodeInUse as exc:
        raise HTTPException(
            409,
            {
                "error": "node_in_use",
                "message": str(exc),
                "usage": exc.usage.model_dump(mode="json"),
            },
        ) from exc
    _bump_canon(conn, proj.id)
    conn.commit()
    return NodeDeleted(id=node_id, name=usage.name, usage=usage)


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
