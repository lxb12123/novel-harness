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
from ..graph.store import NodeNotFound, StoreError
from ..system_notifications import (
    SystemNotification,
    enqueue_event_cast_changed,
    list_open_notifications,
    materialize_notification_outbox,
)
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


class CharacterEventRow(BaseModel):
    """这个人时间线上的一条。**一件事跟几个人相关，就在几个人的线上各出现一次。**

    维护者的原话（2026-08-25）：「事件是**比较小的一条总结**。只放到和它相关的那个
    角色下面……一件事情如果跟好多人相关，那就放到每个相关人的下面。」

    存储那一侧本来就是这个形状：`event_participant` / `event_knower` 是多对多，
    一件事跟三个人相关就挂三行。**这一批加的只是「按人看」这个出口**，没动一张表。

    `summary` 取的是**当前那一版**（`event_summary_head` → version，`_EVENT_COLS`
    里那个 COALESCE），所以作者改过的摘要在这条线上立刻是新的。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    chapter_number: int = Field(ge=1)
    summary: str
    participants: tuple[NodeRef, ...] = ()
    """在场的人。**窄引用**：`Node.props` 里装着作者写的 `twist` / `plot_note`。"""
    knowers: tuple[NodeRef, ...] = ()
    """知道这件事的人。它和 `participants` 一起决定了这条事件挂在谁名下
    （判据见 `queries.event_ids_for_one_character`）。"""
    cast_changed: SystemNotification | None = None
    """这条事件掉了参与者时的那条 `event_cast_changed` 通知（2026-08-28）。

    **拼在这里，不是让前端再发一次请求去 `GET .../notifications` 里找**——同
    `GET .../roster` 那条 `appearance_chapters` 的先例（`api/app.py::roster`）：
    「两个数都和花名册同一条出参回来，不是第二次请求……多一次往返就是多一次
    会失败、会晚到的东西」。红点比那个数更经不起晚到：数字晚到只是慢一秒，
    红点晚到是作者已经看完这一页走了，而这条事件掉的那个人他没看见。

    带的是**完整** `SystemNotification`（不是裸 `bool`）：前端要用 `id` 去调
    既有的 `ignore`/`resolve`，要用 `jump` 直接定位，要用 `title_code`/
    `title_params` 渲染——把这几样拆开发反而逼前端自己拼回一个通知形状。

    **一件事跟三个人相关，三个人的行上这一位都得是同一条通知**（同一个
    `subject_id`，不是各查各的）：`character_events()` 只查一次 OPEN 通知表，
    按 `subject_id` 建一次索引，三个人的三次调用各自命中同一条——`event_cast_
    changed` 的去重（同一事件同时最多一条 OPEN）保证了这一点，不需要额外
    去重逻辑。
    """


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
        raise HTTPException(404, {"error": "character_not_found"})
    if node.label is not NodeLabel.CHARACTER:
        raise HTTPException(422, {"error": "not_a_character"})
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


@router.get(
    "/api/projects/{project_id}/characters/{character_id}/events",
    response_model=list[CharacterEventRow],
)
def character_events(
    character_id: str,
    conn: Annotated[Connection, Depends(get_conn)],
    events: Any = Depends(get_event_store),
    proj: Any = Depends(load_project),
) -> list[CharacterEventRow]:
    """**这个人的全部事件**，按章号升序（2026-08-25）。

    今天跟事件有关的对外路由全是「按章看」或「按事件 id 看」——
    `…/chapters/{n}/events`、`…/events/{id}/summary`。**没有一条按人看的**，
    而作者点开花名册里的一个人时想看的正是那条线。

    ── ⚠️ 它**不收章号**，这不是忘了做时态 ────────────────────────────────

    出参是**全给 + 每条带章号**，要按「第 N 章那个时点」切片交给界面。
    换成后端切的话，作者点开一个人只看得到当前章之前的部分，
    而他打开花名册正是为了看整条线。完整论证（掉了哪两个条件、剩下三个为什么一个
    都不许再掉）在 `graph.queries.event_ids_for_one_character`。

    只出 CANON：PROVISIONAL 是抽取器猜的、没确认的，混进这条线等于把猜测当事实。

    ⚠️ **今天这条线在真书上是空的**，而那是已知的（作者那本 158 章的书里事件 0 条）：
    第一次真抽取跑完才会有。空不是坏——界面那一侧要说清是哪一种空
    （这一章还没整理过 / 整理了但没抽到），别写「暂无数据」。

    每一行的 `cast_changed`（2026-08-28）拼的是这条事件当前 OPEN 的
    `event_cast_changed` 通知——查一次 `list_open_notifications`，按
    `subject_id` 建索引，不逐条事件再发一次请求。见 `CharacterEventRow
    .cast_changed` 的说明。
    """
    _character_node(conn, proj.id, character_id)
    cast_changed_by_event = {
        n.subject_id: n
        for n in list_open_notifications(conn, proj.id)
        if n.kind == "event_cast_changed"
    }
    return [
        CharacterEventRow(
            event_id=view.event.id,
            chapter_number=view.event.chapter_number,
            summary=view.event.summary,
            participants=tuple(view.participants),
            knowers=tuple(view.knowers),
            cast_changed=cast_changed_by_event.get(view.event.id),
        )
        for view in events.events_for_one_character(proj.id, character_id)
    ]


# ══════════════════════════════════════════════════════════════════════════
# 花名册条目：改名 / 删除
#
# **它们是「抽取自动建人物」的配套，不是可选项**（ADR 0020 补记，2026-08-25）：
# 模型会认错——真书上的实例是「袭人」（满篇「寒气袭人」）。自动建 + 不能删 = 单向阀，
# 那个错会永远留在库里往上下文里塞噪声，而作者没有任何办法清掉它。
#
# 两条都带 `expected_canon_version`（同别名那几条）：作者拿着一份旧花名册点删除时
# 收到的是 409，不是「删掉了一个他没看见的、刚被抽取改过的东西」。
#
# ── 删除 2026-08-28 起不再拒绝（维护者裁定）──────────────────────────────
# 旧版本这里挂着关系/情节就 409（`NodeInUse`）。裁定换成「直接删 + 事后可见可改」：
# 删照做，但这个人参与过的每一件事因此掉了参与者，剩下的人身上要挂得出一条
# `event_cast_changed` 通知——作者从别人的角色卡上看见「这条掉了一个人」，
# 点过去、自己改。当年拒绝要防的事（悄悄蒸发、作者不知道自己带走了什么）
# 没有消失，只是从「事前拦住」换成了「事后找得到」。
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
    events: Any = Depends(get_event_store),
    proj: Any = Depends(load_project),
) -> NodeDeleted:
    """删掉花名册里的一条。**不拒绝**——挂着关系/情节也直接删（2026-08-28 裁定）。

    `edge.src|dst` / `event_participant` / `event_knower` 到 `node` 全是
    ON DELETE CASCADE：这个人参与过的关系、以及他在每一件事的在场/知情名单里
    那一行，跟着一起没。**事件本身不会没**——少的是名单里的一行，不是
    `story_event` 那一行。

    那些情节因此掉了参与者，而剩下的人不该发现不了：删之前先把这个人的全部
    事件记下来（级联一旦发生，`event_participant`/`event_knower` 就已经查不到
    这个人了，必须在那之前问），删完给每一件事挂一条 `event_cast_changed`
    通知——只告警，不进 `BLOCKING_KINDS`，不会连带停掉哪一章的总结/抽取。

    `expected_canon_version` 走**查询参数**：DELETE 的请求体在各家 HTTP 客户端上
    支持得参差不齐（fetch 里带 body 的 DELETE 不是所有代理都转发）。
    """
    _require_canon(conn, proj.id, expected_canon_version)
    # 必须在删之前问：级联一旦发生，这个人在 event_participant/event_knower 上
    # 的行就没了，`events_for_one_character` 会以为他什么都没参与过。
    affected = events.events_for_one_character(proj.id, node_id)
    try:
        usage = store.delete_node(proj.id, node_id)
    except NodeNotFound as exc:
        raise HTTPException(404, {"error": "node_not_found", "message": str(exc)}) from exc
    notified = False
    for view in affected:
        evidence = store.get_evidence(proj.id, view.event.evidence_id)
        if evidence is None:
            # 不该发生（`evidence_id` 是 NOT NULL 外键）——但通知点不到位置
            # 比没有通知更糟（作者点了却哪儿都不去），宁可漏这一条。
            continue
        remaining = {p.id for p in view.participants} | {k.id for k in view.knowers}
        remaining.discard(node_id)
        enqueue_event_cast_changed(
            conn,
            project_id=proj.id,
            event_id=view.event.id,
            chapter_number=view.event.chapter_number,
            title_code="event_cast_changed_title",
            title_params={"removed_name": usage.name, "remaining_count": len(remaining)},
            jump=evidence.anchor(),
        )
        notified = True
    _bump_canon(conn, proj.id)
    conn.commit()
    if notified:
        # 删除是前台同步动作，通知也该同步可见——不等下一次后台 outbox 扫描
        # （同 `_notify_toc_skipped` 那条口径：`app.py` 导入丢目录页假章时同款）。
        materialize_notification_outbox(conn, project_id=proj.id, lease_owner="delete_node")
    return NodeDeleted(id=node_id, name=usage.name, usage=usage)


def _require_canon(conn: Connection, project_id: str, expected: int) -> None:
    current = project_mod.require_canon_version(conn, project_id)
    if expected != current:
        raise HTTPException(
            409,
            {"error": "stale_canon_version", "params": {"expected": expected, "current": current}},
        )


def _bump_canon(conn: Connection, project_id: str) -> int:
    return project_mod.compare_and_bump_canon_version(
        conn, project_id, project_mod.require_canon_version(conn, project_id)
    )
