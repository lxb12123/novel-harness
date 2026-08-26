"""路由 —— 只把引擎函数包成 HTTP，不装业务。不碰连接（收 Depends(get_store)）。

只读面板：项目 / 花名册 / 认知矩阵（头牌）/ 场景约束 / 当前状态 / 局部子图。
编辑器（P1）：列章 / 读章正文 / 存盘 → sync（正文在磁盘，ADR 0007）。
写图谱（declare）+ 定位 + R4 check（P2）：作者敲称呼原文 + 引语，系统算章号。
M4 抽取/事件读端拆在 ``api/extraction.py``，提案审阅与改正在 ``api/review.py``，
活动日志（跑了什么 / 花了多少 / 谁改了什么）在 ``api/activity.py``，
写作助手的会话（模式二，ADR 0019）在 ``api/chat.py``；
只有 AI 规划这一条能力仍未开放，仍返 501。

── 两条贯穿本文件的纪律 ───────────────────────────────────────────────────

1. **出参收窄（§1.2 陷阱）**：`resolve` / `subgraph` / `state` 出**完整 `Node`**，而
   `NodeProps` 是 `extra="allow"`——一个未来节点会把作者写的 `props.twist` /
   `plot_note` 序列化出去（`graph.models.NodeRef` 的 docstring 有实测形态）。所有可能
   含完整 Node 的响应过 `_narrow`：「first_appears > 当前章」的节点收窄成
   `{id,label,name}`。这是出口侧的**唯一**收窄点（同时态过滤只写一次的道理）。

2. **错误映射（§1.3）**：引擎的每一类拒绝异常在这里映成一个稳定的 HTTP 码 + 结构化
   body，复刻 cli 的 `_die_refused` / `_reason`。**歧义一律 409 + candidates，服务端
   绝不替作者挑**（同 DeclarationRefused 的 docstring：挑错 = 一条 valid_from 错了的
   CANON 边，面板上长得完全正常）。
"""

from __future__ import annotations

import re
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from .. import importer
from .. import onboarding
from .. import project as project_mod
from ..settings import Settings as UserSettings
from ..settings import load as load_user_settings
from ..settings import save as save_user_settings
from .validation import router as validation_router
from ..declare import (
    AmbiguousName,
    AmbiguousQuote,
    DeclarationRefused,
    Ledger,
    QuoteNotFound,
    UnknownName,
    WrongLabel,
)
from ..draft.length import DEFAULT_LENGTH_POLICY, LengthSpec
# 模块级 import：`api/chat.py` → `agent/` 那条链本来就把它拉进来了
# （`sys.modules` 实测），所以这一行不多花任何启动时间。
from ..draft.product_draft import SummaryBackfillReply
from ..graph import (
    AliasKind,
    EdgeType,
    InformationScope,
    NodeLabel,
    NodeProps,
    NodeRef,
)
from ..graph.store import (
    ChapterInUse,
    NodeNotFound,
    SnapshotInUse,
    SnapshotIsCurrent,
    StoreError,
    SupersedeConflict,
)
from ..mentioned import mentioned_cast
from ..panel import (
    UnresolvedCast,
    cast_states,
    character_state,
    resolve_cast,
    scene_constraints,
)
from ..text import paragraphs as split_paragraphs
from .deps import (
    books_root,
    ensure_schema,
    get_conn,
    get_ledger,
    get_store,
    load_project,
    resolve_route_capabilities,
)
from . import manuscript
from .activity import router as activity_router
from .characters import router as characters_router
from .chat import router as chat_router
from .extraction import router as extraction_router
from .notifications import router as notifications_router
from .reconcile import router as reconcile_router
from .review import router as review_router

_STATIC = Path(__file__).resolve().parent / "static"
# React 工作台的构建产物。存在就服务它，否则降级到 static/ 的原生原型。
#
# **包内路径**（api → novel_harness → webui/），不是源码树里的 frontend/dist。
# 上一版指的是 `parents[3]/"frontend"/"dist"`，即仓库根下面——在源码树里跑得好好的，
# 可一旦 `pip install` 进了 site-packages，`parents[3]` 指向的是 site-packages 的上一层，
# 那儿没有 frontend/。于是装出来的包**静默**降级成 static/ 原型：不报错、不红、CI 全绿，
# 只是作者永远看不到工作台。这正是 §10 约束 8 说的那种失败形态——漂亮的空结果 + exit 0。
# Vite 的 outDir 已经改成直接往这儿输出（frontend/vite.config.ts 有为什么）。
_DIST = Path(__file__).resolve().parent.parent / "webui"
_CAST_SEP = re.compile(r"[,，、]")  # 半角逗号 / 全角逗号 / 顿号


def webui_built() -> bool:
    """React 工作台的构建产物在不在。

    启动器（`api/launch.py::launch()`）用它决定要不要提醒作者「你现在看到的是降级原型」。
    这个提醒是必需的：
    没构建时 `/` 照样返回 200（`api/static/index.html` 那个只读原型），页面能开、
    功能少一半——**一个不报错的降级是最难自查的故障**，得由起服务的那一刻说出来。
    """
    return (_DIST / "index.html").exists()


def _split_cast(cast: str) -> list[str]:
    """把作者写的在场称呼原文切成列表。不解析、不丢——解析交给引擎的 resolve_cast。"""
    return [part.strip() for part in _CAST_SEP.split(cast) if part.strip()]


def _split_edge_types(raw: str) -> list[EdgeType] | None:
    """`?edge_types=KNOWS,LOCATED_AT` → [EdgeType…]。空 = None（走 subgraph 的默认展开）。

    非法值直接让 `EdgeType(...)` 抛 ValueError → 422（同引擎的「不静默吞坏输入」）。
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [EdgeType(p) for p in parts] or None


# ── 出参收窄：未来节点绝不整体序列化（§1.2 陷阱）──────────────────────────


def _is_sensitive_node(node: dict[str, Any], chapter: int | None) -> bool:
    """一个已 `model_dump` 的 Node 字典是不是「最不该被完整序列化」的那批。

    判据一条（§1.2 / `NodeRef` docstring）：first_appears > 当前章 —— 这个节点按定义
    是关于未来的。

    ⚠️ 原来还有第二条 `label == Secret`，2026-08-25 随秘密下线（ADR 0039）。
    **它的消失让 `chapter is None` 这条分支变成了「什么都不收窄」**：没有当前章就判不了
    未来。今天这不构成泄漏，因为**唯一**传 `None` 的调用方是 `declare_node`——把作者刚
    自己建的那个节点原样递回去。`resolve` 那条无章号的花名册路径不走这里，它一律出
    `NodeRef`（比这儿更严）。**再有新的 `chapter=None` 调用方时，先回答「这批 Node 是
    谁的」，别默认它安全。**
    """
    if chapter is None:
        return False
    first = (node.get("props") or {}).get("first_appears_chapter")
    return first is not None and first > chapter


def _looks_like_node(obj: dict[str, Any]) -> bool:
    """恰好是一个完整 `Node` 的 dump：id + label + name + props 四键齐备。

    全库只有 `Node` 是这个形状——`Edge` 有 `type` 无 `name`/`label`，`StoredAlias` 有
    `surface` 无 `props`，已收窄的 `NodeRef` 有 id/label/name 但**无 props**（于是它天然
    穿过本函数不被再动一次，因为它已经安全）。所以这个签名唯一命中要收窄的东西。
    """
    return {"id", "label", "name", "props"} <= obj.keys()


def _narrow(obj: Any, chapter: int | None) -> Any:
    """递归遍历一个已 `model_dump(mode="json")` 的结构，把敏感的完整 Node 收窄成
    `{id,label,name}`。**这是出口侧收窄的唯一实现**——放一个choke point，而不是让每个
    端点自己记得收窄（那条纪律第一个漏的端点就是一次泄漏）。

    在 dump 之后的纯 dict/list 上走，是因为收窄判据（label / first_appears）dump 之后
    全在，而纯结构的递归不必逐个响应模型写一份 Node 替换。
    """
    if isinstance(obj, dict):
        if _looks_like_node(obj) and _is_sensitive_node(obj, chapter):
            return {"id": obj["id"], "label": obj["label"], "name": obj["name"]}
        return {k: _narrow(v, chapter) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_narrow(v, chapter) for v in obj]
    return obj


def _narrowed(model: Any, chapter: int | None) -> Any:
    """Pydantic 出参 → 收窄后的 JSON 结构。端点返回它，不返回裸模型。"""
    return _narrow(model.model_dump(mode="json"), chapter)


def _chapters_dir(proj: Any) -> Path:
    return Path(proj.root_path) / "chapters"


def _chapter_file(proj: Any, chapter: int) -> Path:
    return _chapters_dir(proj) / f"{chapter:04d}.md"


def _chapter_mentions(proj: Any, store: Any, chapter: int) -> list[str] | None:
    """本章正文里提到的花名册称呼。`None` = 这一章磁盘上没有正文（**不是「没提到人」**）。

    两者长得一样是这个仓库反复修的病（§10 约束 8：静默的零和真的零不许长得一样）——
    「章还没写」和「写了但一个花名册里的人都没提到」对作者是完全不同的两件事。
    """
    file = _chapter_file(proj, chapter)
    if not file.exists():
        return None
    paras = split_paragraphs(file.read_text(encoding="utf-8-sig"))
    return mentioned_cast(store, proj.id, paras)


def _effective_cast(proj: Any, store: Any, chapter: int, cast: str, include: str = "") -> list[str]:
    """面板要用的在场：**作者传了就听作者的，没传就从本章正文推**；`include` 只往里加人。

    这条是「在场人物不该是写之前填的表单」的落点：作者写完，引擎自己去正文里数
    花名册命中了谁。推导的方向是 fail-closed 的那一侧（`mentioned.py` 讲了为什么
    多算比少算安全），所以「不填」不再等于「面板全禁到没东西看」。

    **不传和传空串是同一件事。** 区分它们只会让调用方靠一个看不见的差别改变语义；
    真要全禁的调用方（M2 判分链）走的是 Python 里的 `scene_constraints`，不经过这里。

    正文不存在 → 推不出东西 → 空 cast → `scene_constraints` 照旧退化成全禁。

    ── `include` 为什么必须是另一个参数，不能塞进 `cast` ──────────────────────
    `cast` 的语义是**过滤**（「只看这几个人」），而它今天只有一个来源：作者亲手标的
    场景块。收窄是他自己要的，所以那个方向合法。

    活动日志的跳转坐标（`ActivityJump.cast`）是**系统自己**给的，而它按定义只知道一个人。
    把它塞进 `cast` 就是让系统替作者做了一次收窄，而

        must_not_reveal 的判据 = 「在场的人里**至少有一个**还不知道」

    少一个人 = 少一批禁令 = **fail-open**，正是 ADR 0018 §3 那条方向。
    实测形态：一章里萧决知道、李管家不知道 ⇒ 推导下这条秘密被禁；坐标把在场换成
    「只有萧决」之后它从 `must_not_reveal` 里消失了——`panel/constraints.py` 那段
    「李管家静默地从 cast 里消失」记的就是这种病，这个仓库修过一次。

    所以坐标走这一条：**它只能让在场变大，永远不能让它变小**。

    **空推导那一档一律不加人**（`if not base`）：一个人都没数出来的含义是「不知道谁
    在场」，`ResolvedCast.complete` 据此全禁。这时把坐标加进去会让在场从空变成一个人，
    `complete` 成立，全禁塌成「只按他一个人算」——同一种 fail-open，而且恰好发生在
    系统对这一章一无所知的时候。代价是那一章的跳转退回今天的行为（矩阵那边照旧说
    「没有在这一章找到刚才那一格」），**那是安全的那一侧**。
    """
    base = _split_cast(cast) or (_chapter_mentions(proj, store, chapter) or [])
    if not base:
        return []
    # 去重按称呼原文即可：同一个人的两个称呼由 `resolve_cast` 按 node_id 并成一行。
    return base + [surface for surface in _split_cast(include) if surface not in base]


INCLUDE = Query(
    "",
    description="一定要出现在这份在场里的称呼原文（跳转坐标用）。**只加不减**，不替换推导。",
)
"""三条读端共用同一份措辞和同一个默认值。

**一份**是有意的：这个参数的全部安全性在于「只加不减」，而三处各写一遍
description 的下一步就是三处开始说不一样的话，然后有人照着其中一句改成过滤。
"""


@asynccontextmanager
async def _lifespan(_: FastAPI) -> Any:
    ensure_schema()  # 启动即验库在、schema 到位；库不存在直接炸，不建空库
    _auto_refresh_windows()  # 开着才跑，后台线程，拉不到当无事发生
    # Task 16：持久 attempt 的 dispatcher——保存/reconcile/显式重整只写 attempt，
    # 这里把 PENDING/过期 RUNNING 转成真实结果。wake 丢了靠持久扫描兜底（不变量 18）。
    # 测试套件用 `NH_BACKGROUND_RUNTIME=0` 关掉它（几千个 TestClient 每个起一个
    # 轮询线程会把套件搅成不确定）；生产不设这个变量 = 默认开。
    runtime = None
    if os.environ.get("NH_BACKGROUND_RUNTIME", "1") == "1":
        try:
            from .background_runtime import build_runtime

            runtime = build_runtime()
            runtime.start()
        except Exception:
            # 库/模型没那么好时也要能启动（作者可能先建书再看设置）——dispatcher
            # 的 adapter 只在真的 claim 到 attempt 时才构造。
            runtime = None
    try:
        yield
    finally:
        if runtime is not None:
            runtime.stop()


app = FastAPI(title="Novel Harness 工作台", lifespan=_lifespan)
app.include_router(activity_router)
app.include_router(characters_router)
app.include_router(chat_router)
app.include_router(extraction_router)
app.include_router(notifications_router)
app.include_router(reconcile_router)
app.include_router(review_router)
app.include_router(validation_router)

# 构建产物的静态资源（/assets/index-xxxx.js）。只有 dist 真的构建出来才挂载——
# 挂一个不存在的目录会在启动时炸，而测试套件不构建前端（那时走 static/ 原型兜底）。
if (_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")


# ══════════════════════════════════════════════════════════════════════════
# 错误映射（§1.3）—— 一张表，所有路由共用。复刻 cli 的 _die_refused / _reason。
#
# FastAPI 按 MRO 挑**最具体**的已注册 handler，所以基类（DeclarationRefused /
# StoreError）和子类可以同时注册，子类命中时不会落到基类。
# ══════════════════════════════════════════════════════════════════════════


def _err(status: int, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(UnknownName)
async def _unknown_name(_: Request, exc: UnknownName) -> JSONResponse:
    return _err(404, {"error": "unknown_name", "surface": exc.surface, "message": str(exc)})


@app.exception_handler(AmbiguousName)
async def _ambiguous_name(_: Request, exc: AmbiguousName) -> JSONResponse:
    # 候选摆出来（已是 NodeRef，无泄漏），**服务端绝不替作者选**——那是消歧下拉的料。
    return _err(
        409,
        {
            "error": "ambiguous_name",
            "surface": exc.surface,
            "candidates": [c.model_dump(mode="json") for c in exc.candidates],
            "message": str(exc),
        },
    )


@app.exception_handler(AmbiguousQuote)
async def _ambiguous_quote(_: Request, exc: AmbiguousQuote) -> JSONResponse:
    # 让作者把引语加长到唯一。candidates 带 context（前后各半句），不带任何 Node。
    return _err(
        409,
        {
            "error": "ambiguous_quote",
            "quote": exc.quote,
            "candidates": [c.model_dump(mode="json") for c in exc.candidates],
            "message": str(exc),
        },
    )


@app.exception_handler(WrongLabel)
async def _wrong_label(_: Request, exc: WrongLabel) -> JSONResponse:
    return _err(
        422,
        {
            "error": "wrong_label",
            "surface": exc.surface,
            "got": exc.got.value,
            "want": exc.want.value,
            "message": str(exc),
        },
    )


@app.exception_handler(QuoteNotFound)
async def _quote_not_found(_: Request, exc: QuoteNotFound) -> JSONResponse:
    return _err(422, {"error": "quote_not_found", "quote": exc.quote, "message": str(exc)})


@app.exception_handler(DeclarationRefused)
async def _declaration_refused(_: Request, exc: DeclarationRefused) -> JSONResponse:
    # 兜底：将来新增的 DeclarationRefused 子类不会静默变成 500。作者按了按钮系统不肯猜。
    return _err(409, {"error": "declaration_refused", "message": str(exc)})


@app.exception_handler(SupersedeConflict)
async def _supersede_conflict(_: Request, exc: SupersedeConflict) -> JSONResponse:
    # 乱序声明 valid_from，v1 拒绝不猜（§5.5：猜错 = state_at 同时返回两个互斥位置）。
    return _err(409, {"error": "supersede_conflict", "message": str(exc)})


@app.exception_handler(NodeNotFound)
async def _node_not_found(_: Request, exc: NodeNotFound) -> JSONResponse:
    # subgraph 的 center / state 的 node_id 不在本项目（含跨项目误引用）。
    return _err(404, {"error": "node_not_found", "message": str(exc)})


@app.exception_handler(SnapshotIsCurrent)
async def _snapshot_is_current(_: Request, exc: SnapshotIsCurrent) -> JSONResponse:
    # 想删「现在这一版」：先还原到别的版本（current 会跟着移过去），再删这一条。
    return _err(409, {"error": "snapshot_is_current", "message": str(exc)})


@app.exception_handler(SnapshotInUse)
async def _snapshot_in_use(_: Request, exc: SnapshotInUse) -> JSONResponse:
    # 明细一起给：界面要说得出「被几条什么引着」，不是只说一句「删不掉」。
    return _err(
        409,
        {
            "error": "snapshot_in_use",
            "usage": exc.usage.model_dump(mode="json"),
            "message": str(exc),
        },
    )


@app.exception_handler(ChapterInUse)
async def _chapter_in_use(_: Request, exc: ChapterInUse) -> JSONResponse:
    # 明细一起给：屏幕上要说得出**挡路的是什么**，不是只说一句「删不掉」——
    # 一句不带理由的拒绝会让作者去翻文件夹自己动手删，那才是真的会丢东西。
    return _err(
        409,
        {
            "error": "chapter_in_use",
            "usage": exc.usage.model_dump(mode="json"),
            "message": str(exc),
        },
    )


@app.exception_handler(StoreError)
async def _store_error(_: Request, exc: StoreError) -> JSONResponse:
    # 图层其余错误（QuoteMismatch 等）：作者/调用方的输入撞上了图层不变式。
    return _err(422, {"error": "store_error", "message": str(exc)})


@app.exception_handler(UnresolvedCast)
async def _unresolved_cast(_: Request, exc: UnresolvedCast) -> JSONResponse:
    # **读端不会走到这里**：矩阵/约束/state 退化 + unresolved_cast 横幅，返 200。
    # 只有将来 M2 起草路径 require_resolved_cast() 才抛它——那时 409 请作者先消歧。
    return _err(409, {"error": "unresolved_cast", "message": str(exc)})


@app.exception_handler(importer.ImportRefused)
async def _import_refused(_: Request, exc: importer.ImportRefused) -> JSONResponse:
    return _err(409, {"error": "import_refused", "conflicts": exc.conflicts, "message": str(exc)})


@app.exception_handler(importer.SyncRefused)
async def _sync_refused(_: Request, exc: importer.SyncRefused) -> JSONResponse:
    # 某个 NNNN.md 切出 0 或 >1 章。正文可能已写进磁盘，但 sync 拒绝落库。
    return _err(422, {"error": "sync_refused", "path": exc.path, "message": str(exc)})


@app.exception_handler(importer.ChapterChanged)
async def _chapter_changed(_: Request, exc: importer.ChapterChanged) -> JSONResponse:
    # 保存的乐观闸：调用方依据的那份正文已经过期。409，一个字节都不写。
    return _err(
        409,
        {
            "error": "chapter_changed",
            "chapter": exc.chapter,
            "message": str(exc),
        },
    )


@app.exception_handler(importer.ChapterLockTimeout)
async def _chapter_lock_timeout(_: Request, exc: importer.ChapterLockTimeout) -> JSONResponse:
    return _err(423, {"error": "lock_timeout", "message": str(exc)})


@app.exception_handler(ValidationError)
async def _validation_error(_: Request, exc: ValidationError) -> JSONResponse:
    # NodeSpec/AliasSpec 的 validator 写给作者的话（「别名『音』只有 1 个字…」）藏在
    # pydantic 4 行开发者输出里的半句。剥掉 wrapper，只留那半句（复刻 cli._reason）。
    msg = "；".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return _err(422, {"error": "invalid", "message": msg})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    # chapter<1 / scope 不可查 / hops>2 / 空名 等都从引擎抛 ValueError → 作者的输入错，422
    return _err(422, {"error": "bad_request", "message": str(exc)})


@app.get("/")
async def index() -> FileResponse:
    """SPA 入口。构建产物在就发 React 工作台，否则降级到 static/ 的原生原型（只读也能用）。"""
    built = _DIST / "index.html"
    return FileResponse(built if built.exists() else _STATIC / "index.html")


# ── 项目 / 花名册 / 面板（只读）─────────────────────────────────────────────


@app.get("/api/projects")
def projects(conn: Any = Depends(get_conn)) -> Any:
    return project_mod.list_all(conn)


class SettingsBody(BaseModel):
    """设置请求体。`api_key` 为空 = 保持原值（改地址/模型时不用重粘钥匙）。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""

    context_window: int | None = None
    """作者手填的上下文窗口。**这一位的空值语义和上面三个相反：空 = 清掉。**

    理由是作者必须收得回一个填错的数。`api_key` 留空保持原值，是因为屏幕上根本不回显它
    （不这样他改个地址就得重粘钥匙）；而这个数是回显着的，「留空 = 保持」会让那个框
    **变成一个只进不出的洞**。

    「有没有清」的判据是**这次请求里带没带这个键**（`model_fields_set`），不是它的值——
    否则任何一个没发这个字段的老客户端（或者别处一段只想改地址的代码）都会把它悄悄抹掉，
    而抹掉的症状是上文塌回 800 字，没有任何一处会红。
    """

    auto_update_model_windows: bool | None = None
    """每次打开工作台自动更新那份模型表。**同上：带没带这个键才是判据。**

    `None` 不是「关掉」，是「这次没提这件事」——只想改一下地址的请求不该顺手替作者
    把这个开关拨回去。真要关它，前端发的是 `false`。
    """


def _continuation_tail() -> tuple[int, str]:
    """行内续写值得带多少上文（code point），外加**这个数为什么是这个数**。

    ── 公式只有后端一份 ──────────────────────────────────────────────────

    这一格只是把 `draft/assemble.py::product_tail_limit()` 算出来的数送到浏览器。
    **前端不许再存一份**：那个函数按模型窗口伸缩，而 2026-08-22 之前前端写死 1,000，
    32k 和 1M 的模型送出去的都是 1,000 字——后端整套伸缩设计被一个常量架空了，
    而症状（「模型忽然变笨」）没有任何一处会红。这条缝两头由
    `tests/test_continuation_tail_limit.py` 钉着。

    ── 输出预留传 0，是有意的 ────────────────────────────────────────────

    这一格回答的是「这个模型的窗口最多值得带多少上文」，不是「这一次要给输出留多少」。
    后者由 `POST …/draft` 按这一稿的长度算（`plan.request_token_budget`），而
    `product_tail_limit` 对预留单调不增——那条路只会把这个数**往下夹**，绝不会往上抬。
    方向是有意选的：前端多送几百字只是白送几个字符（同一台机器上的 HTTP），
    少送就再也补不回来（`product_tail_limit` 的 docstring：「只许把上文变长，不许变短」）。

    ── 拿不到能力/窗口时**说出来**，不静默塌回 800 ────────────────────────

    `basis` 是这个数的出处，三档各对应一种真会发生的状态：

    - ``model_window``：认得出这条路由，按它的窗口算的；
    - ``unknown_window``：认得出路由但不知道窗口（自建端点），塌到地板值；
    - ``unconfigured``：连服务地址/模型都还没有，同样是地板值，但原因完全不同。

    后两档的数字一模一样，**只有这一位区分得开**——`deps.resolve_route_capabilities`
    那条注释讲的正是这个坑：少给上文是静默的，作者也说不出哪儿不对。

    Note:
        连接参数走 `_draft_provider_config()`（本文件下面那个），**不是另抄一份**：
        它必须和真正发请求的那条路解析出同一条路由，否则这一格报的数就是另一个模型的。
        `resolve_route_capabilities` 里「作者手填的窗口」那一档读的是盘上那份设置，
        而两个调用方都保证盘上已经是最新的（`PUT` 先 `save_user_settings` 再进这儿）。

        代价：这条读路由从此**可能出一次网**（只有 OpenRouter 那一档会，5 秒超时、
        成功失败都进 1 小时缓存，见 `draft/discovery.py`）。可以接受——`/draft` 本来
        每次都走同一条解析，而这一格不走就只能在前端再猜一个数。
    """
    from ..draft.assemble import product_tail_limit
    from ..draft.capabilities import CapabilityError

    try:
        capability = resolve_route_capabilities(_draft_provider_config())
    except (CapabilityError, ValueError):
        # 地板值也从同一个公式里取（`product_tail_limit(None, …)`），不在这儿抄一个 800。
        return product_tail_limit(None, 0), "unconfigured"
    if capability.max_context_tokens is None:
        return product_tail_limit(None, 0), "unknown_window"
    return product_tail_limit(capability.max_context_tokens, 0), "model_window"


def _settings_response(settings: UserSettings) -> dict[str, Any]:
    """**永不回吐完整 key**——前端只需要「设没设」和「后四位」。
    完整 key 只在本机文件里，不出 HTTP（本地环回也算出口）。"""
    tail_limit, tail_basis = _continuation_tail()
    return {
        "base_url": settings.base_url,
        "model": settings.model,
        "api_key_set": settings.api_key_set,
        "api_key_preview": settings.api_key_preview,
        # 这一位**要回显**（同上面那条「只进不出的洞」）：没填是 null，不是 0——
        # 屏幕上「没填」和「填了个 0」是两件事，混成一个数就再也分不开了。
        "context_window": settings.context_window,
        "auto_update_model_windows": settings.auto_update_model_windows,
        # 续写这一格搭这条已有的返回过来（**不另开接口**）：前端本来每次开工作台
        # 就在拿它，而这个数只随「换了模型/改了窗口」变，正是这条返回会变的时候。
        "continuation_tail_limit": tail_limit,
        "continuation_tail_basis": tail_basis,
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """读 AI 设置（BYOK）。不含完整 key。"""
    return _settings_response(load_user_settings())


@app.put("/api/settings")
def put_settings(body: SettingsBody) -> dict[str, Any]:
    """写 AI 设置到本机（`~/.config/novel-harness/settings.json`，0600）。"""
    current = load_user_settings()
    merged = UserSettings(
        base_url=body.base_url.strip() or current.base_url,
        model=body.model.strip() or current.model,
        api_key=body.api_key or current.api_key,
        # 带了这个键就照它写（含 null / 0 = 清掉），没带就原样留着。见 `SettingsBody`。
        context_window=(
            body.context_window
            if "context_window" in body.model_fields_set
            else current.context_window
        ),
        auto_update_model_windows=(
            bool(body.auto_update_model_windows)
            if "auto_update_model_windows" in body.model_fields_set
            else current.auto_update_model_windows
        ),
    )
    save_user_settings(merged)
    return _settings_response(merged)


class ModelWindowsPullFailed(Exception):
    """那份公开的模型表没拉下来（网络、超时、内容不对）。**旧的那份原样留着。**"""


def pull_model_windows() -> dict[str, Any]:
    """去拉一次那份公开的模型表，成功返回回执（新增/变化/减少各几条）。

    **这是全仓库唯一一份「拉那张表」的实现**，两个调用方共用：作者拨开关那一刻
    （`PUT /api/settings` 之后前端立刻点一次刷新），和**每次打开工作台时的自动更新**
    （`_auto_refresh_windows`）。两份实现的下一步是两条不一样的超时和两种「失败算不算数」。

    Raises:
        ModelWindowsPullFailed: 拉不到或裁完是空。调用方自己决定是报 502 还是当无事发生。
    """
    import json as _json
    import urllib.error
    import urllib.request
    from datetime import date

    from ..draft import windows as model_windows

    request = urllib.request.Request(
        model_windows.SOURCE_URL, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = _json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ModelWindowsPullFailed(
            f"没能拉到那份公开的模型表（{type(exc).__name__}）。"
            "原来那份还在用，什么都没改。网络好了再试一次。"
        ) from exc

    try:
        report = model_windows.refresh(raw, fetched=date.today().isoformat())
    except (ValueError, OSError) as exc:
        raise ModelWindowsPullFailed(str(exc)) from exc
    return report.model_dump()


def _auto_refresh_windows() -> threading.Thread | None:
    """开着「自动更新」时，工作台起来之后在后台悄悄拉一次那份表。

    三条边界，每一条都是「别把锦上添花变成门槛」：

    * **后台线程**：启动不等它。那是一次跨公网的请求，拿它挡在工作台前面，
      作者断网时就打不开自己的稿子了。
    * **拉不到就当无事发生**：启动时没网是常态。旧的那份原样留着（`windows.refresh`
      自己也拒绝用空表覆盖），屏幕上不弹任何东西——他没按过任何按钮。
    * **默认关着**。这一位由作者自己拨开，见 `settings.Settings.auto_update_model_windows`。
    """
    if not load_user_settings().auto_update_model_windows:
        return None

    def run() -> None:
        try:
            pull_model_windows()
        except ModelWindowsPullFailed:
            pass  # 见上：没网是常态，不是错误

    # **把线程还回去**只为一件事：测试能 join 它。没有这一下，「关着时一次都不许拉」
    # 那条断言就只能靠 sleep 猜，而那种测试要么慢要么假绿。
    thread = threading.Thread(target=run, name="nh-model-windows", daemon=True)
    thread.start()
    return thread


@app.post("/api/settings/model-windows/refresh")
def refresh_model_windows() -> dict[str, Any]:
    """作者点一下，去拉一次公开的模型窗口表（ADR 0025 那一层的数据源）。

    ── 为什么是一颗按钮，而不是自动 ────────────────────────────────────────

    这份数据决定**上文给他 800 字还是 40,000 字**，而它来自一个我们不控制的仓库。
    自动更新 = 别人改一行，作者明天的稿子上下文就变了，而他不知道为什么。
    **一颗按钮把「什么时候信任新数据」这件事留给他。**

    ── 三条边界 ──────────────────────────────────────────────────────────

    * **写到作者自己的目录**，不碰包里那份（`site-packages` 只读，重装就没）。
      读的时候作者那份优先 —— 他点过就说明他要的是新的。
    * **拉失败/裁完是空 ⇒ 不覆盖**，旧的那份原样留着，并回一句人话。
      「更新」把能用的数据换成空的，比不更新坏得多。
    * **回执说出变了什么**（新增/变化/减少各几条）。没有它这就是一颗不出声的按钮，
      作者点完只能猜有没有生效 —— 而这个仓库正在还的债有一半是那种形态。
    """
    try:
        return pull_model_windows()
    except ModelWindowsPullFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── 上手：建书 / 导入 TXT / 同步（让非程序员不碰命令行也能起步）──────────────


class CreateProject(BaseModel):
    name: str


class ImportBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["import"]
    name: str
    text: str


class BlankBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["blank"]
    name: str


BootstrapBody = Annotated[ImportBootstrap | BlankBootstrap, Field(discriminator="mode")]


class ImportText(BaseModel):
    text: str
    """整本 TXT 的正文。**由浏览器读文件解码后作为文本发来**（避开 python-multipart，
    也把编码难题交给浏览器：GBK 的老稿子前端用 TextDecoder 兜）。"""


@app.post("/api/projects")
def create_project(body: CreateProject, conn: Any = Depends(get_conn)) -> Any:
    """新建一本书。root_path 由服务器在 books_root 下派生（§ADR 0007：那目录就是稿子）。

    空名 → project.create 抛 ValueError → 422。project.create 自己 commit。
    """
    root = onboarding.create_legacy_root(books_root(), body.name)
    return project_mod.create(conn, name=body.name, root_path=str(root))


@app.post("/api/projects/bootstrap", response_model=manuscript.BootstrapView)
def bootstrap_project(
    body: BootstrapBody, conn: Any = Depends(get_conn)
) -> manuscript.BootstrapView:
    """原子创建新书：项目、首章、快照与导入报告一起成功或一起消失。

    出参多一份 `summary`（`api/manuscript.py`）：**导入回执的措辞归后端**。
    在它出现之前整份 `import_report` 被前端丢在地上，而里面躺着唯一一个**全书性**的
    信号——`preamble_chars` 异常 = 第一章的章标很可能没被认出来 = 全书 `valid_from`
    集体错一章，且界面上看不出任何异常。
    """
    return manuscript.bootstrap_view(
        onboarding.bootstrap_project(
            conn,
            books_root=books_root(),
            mode=body.mode,
            name=body.name,
            text=body.text if isinstance(body, ImportBootstrap) else None,
        )
    )


@app.post("/api/projects/{project_id}/import")
def import_book(
    body: ImportText,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """把一本 TXT 切章 → 写成 {root}/chapters/NNNN.md → 落库。一次性播种，日常回路用 sync。

    零章 → ImportRefused → 409（切章器没认出这本书的章标写法，不是「书是空的」）；
    已存在且内容不同 → ImportRefused 带 conflicts → 409；某文件切出 0/>1 章 → SyncRefused
    → 422。三者都走全局 handler，前端据 error 码提示作者。
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as fh:
        fh.write(body.text)
        tmp = Path(fh.name)
    try:
        return importer.import_book(store, proj.id, txt=tmp, root=Path(proj.root_path))
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/projects/{project_id}/sync", response_model=manuscript.SyncOutcome)
def sync_project(
    store: Any = Depends(get_store), proj: Any = Depends(load_project)
) -> manuscript.SyncOutcome:
    """把 {root}/chapters/*.md 的现状读进库（作者在别的软件里改了稿之后走这条）。

    **它是「正文看得见」和「这句话记得下」之间那半条回路。** 章列表和正文都直接扫磁盘，
    所以作者在 WPS 里改完回来，屏幕上立刻是新的；而 `locate` 搜的是**库里的快照**，
    快照只有这条路落得下——不跑它，他刚写的那句话选中之后会被告知「找不到」。

    **不花钱**（没有任何模型调用），但仍然只由作者显式触发：它往库里写快照，
    而「磁盘先、DB 跟」的那一下是作者的动作（ADR 0007），不是后台的。

    出参是 `SyncOutcome` 不是 `SyncReport`：屏幕上那句话由 `api/manuscript.py` 写。
    """
    return manuscript.sync_outcome(importer.sync(store, proj.id, Path(proj.root_path)))


@app.get("/api/projects/{project_id}")
def project_detail(proj: Any = Depends(load_project)) -> Any:
    return proj


@app.get("/api/projects/{project_id}/roster")
def roster(
    conn: Any = Depends(get_conn),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """左栏花名册：全项目节点，收窄成 {id,label,name} + 出场章数。

    **props 一个字段都不出**（不整体序列化 `Node.props`：作者写在节点上的 `twist` /
    `plot_note` 住在那儿）。

    两个数**都和花名册同一条出参回来**，不是第二次请求——左栏那一行要显示
    「贾环 · 42 章」，多一次往返就是多一次会失败、会晚到的东西。

    - `appearance_chapters` = **有多少章的总结提到过它**
      （`summary_index.appearance_counts`，一次 SQL，不调模型）。
      ⚠️ 没有总结的章不算，所以一本刚导进来的书这一列全是 0。那是诚实的：
      这一层不读正文。措辞的责任在前端那一行上（别把 0 写成「没出场」）。
    - `information_score` = **各章信息量的累计**（模型给他写的画像有多长，
      `graph.queries.character_information_totals`）。**只有人物有**，别的 label 恒 0。

    ⚠️ **`information_score` 今天只用来排序**，不参与任何判断。完整设计
    （够分直接建 / 不够分问一句 / 不答默认建进去）和「为什么这一批不接那道闸」
    在 ADR 0020 的第二份补记里。
    """
    from ..graph.queries import character_information_totals
    from ..summary_index import appearance_counts

    counts = appearance_counts(conn, store, proj.id)
    scores = character_information_totals(conn, proj.id)
    seen: dict[str, dict[str, Any]] = {}
    for resolution in store.resolve(proj.id, None):
        for hit in resolution.hits:
            node = hit.node
            seen[node.id] = {
                "id": node.id,
                "label": node.label.value,
                "name": node.name,
                "appearance_chapters": counts.get(node.id, 0),
                "information_score": scores.get(node.id, 0),
            }
    return list(seen.values())


@app.get("/api/projects/{project_id}/chapters/{chapter}/mentioned")
def mentioned(
    chapter: int,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """本章正文提到了花名册里的哪些称呼。**这不是「在场」**——见 `mentioned.py` 的模块说明。

    右栏靠它显示「这一章提到：…」，作者因此不必在写之前先填一遍出场人物。
    `has_text=false` 与 `surfaces=[]` 是两件事：前者是「这一章还没写」，后者是
    「写了，但一个花名册里的人都没被提到」。
    """
    surfaces = _chapter_mentions(proj, store, chapter)
    return {
        "chapter": chapter,
        "has_text": surfaces is not None,
        "surfaces": surfaces or [],
    }


@app.get("/api/projects/{project_id}/chapters/{chapter}/constraints")
def constraints(
    chapter: int,
    cast: str = Query(""),
    include: str = INCLUDE,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """场景约束盒：scene_constraints 收原始称呼、内部自解析、fail-closed。

    **`include` 在这一条上才是要命的**：它多一个人只会多一条禁令（安全），
    少一个人就是泄漏。`_effective_cast` 保证它只加不减，`tests/test_jump_cast.py` 钉着。
    """
    return scene_constraints(
        store, proj.id, chapter, _effective_cast(proj, store, chapter, cast, include)
    )


@app.get("/api/projects/{project_id}/chapters/{chapter}/state")
def state(
    chapter: int,
    cast: str = Query(""),
    include: str = INCLUDE,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """当前状态卡：cast_states 收已解析 node_id，所以先 resolve_cast。

    出参含完整 Node（node/location/states[].dim）→ 过 `_narrow`：这一章视角下的
    和未来节点收窄。unresolved 由前端另走 `/matrix` 的 `unresolved_cast` 拿。
    """
    resolved = resolve_cast(store, proj.id, _effective_cast(proj, store, chapter, cast, include))
    snapshots = cast_states(store, proj.id, chapter, resolved.ids)
    return _narrow([s.model_dump(mode="json") for s in snapshots], chapter)


@app.get("/api/projects/{project_id}/characters/{node_id}/state")
def character_state_endpoint(
    node_id: str,
    chapter: int = Query(..., ge=1, description="AS OF 第几章（查询参数，不写进任何数据）"),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """单个人物在第 chapter 章的状态快照。node_id 不在本项目 → NodeNotFound → 404。"""
    return _narrowed(character_state(store, proj.id, node_id, chapter), chapter)


@app.get("/api/projects/{project_id}/resolve")
def resolve(
    surface: str = Query(..., description="一个称呼原文，查它指向哪些节点"),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """称呼 → 候选节点（消歧下拉 / 选区查人物的数据源）。

    只出 `{id,label,name}`——消歧选择器要的就这些，而 resolve 是无章号的花名册查询，
    没有「当前章」能判未来节点，所以这里**一律收窄**（比 `_narrow(chapter=None)` 更严：
    连未来的 Character 也不整体序列化，反正下拉用不上 props）。
    """
    resolution = store.resolve(proj.id, [surface])[0]
    return {
        "surface": resolution.surface,
        "ambiguous": resolution.ambiguous,
        "unique_id": resolution.unique_node.id if resolution.unique_node else None,
        "hits": [
            {
                "node": NodeRef.of(hit.node).model_dump(mode="json"),
                "kind": hit.kind.value,
                "usable_for_rules": hit.usable_for_rules,
            }
            for hit in resolution.hits
        ],
    }


@app.get("/api/projects/{project_id}/subgraph")
def subgraph(
    center: str = Query(..., description="中心节点 node_id（已解析）"),
    chapter: int = Query(..., ge=1, description="AS OF 第几章"),
    hops: int = Query(1, ge=1, le=2, description="展开跳数，硬上限 2（3 跳数学上坏）"),
    edge_types: str = Query("", description="逗号分隔的 EdgeType，空=默认展开"),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """局部关系图（Tab2）。hops>2 由引擎抛 ValueError → 422；center 不存在 → 404。

    nodes/center 是完整 Node → 过 `_narrow`：这一章的未来节点收窄。
    """
    graph = store.subgraph(
        proj.id,
        center,
        chapter,
        hops=hops,
        edge_types=_split_edge_types(edge_types),
        scope=InformationScope.CANON,
    )
    return _narrowed(graph, chapter)


@app.get("/api/projects/{project_id}/evidence/{evidence_id}")
def evidence(
    evidence_id: str,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """Tab3 确定性证据：把矩阵格/状态边的 evidence_id 还原成「来源章 + 当年那句原文」。

    **不给分数**（v1 没有向量，出任何 score = 编的，§1.2）。出扁平视图 + anchor 三元组
    （前端能拿它回跳）。Evidence 不含 Node，无需收窄。不存在/跨项目 → 404。
    """
    ev = store.get_evidence(proj.id, evidence_id)
    if ev is None:
        raise HTTPException(404, {"error": "evidence_not_found", "evidence_id": evidence_id})
    return {
        "id": ev.id,
        "chapter_number": ev.chapter_number,
        "quote_text": ev.audit.quote_text,
        "anchor": ev.anchor().model_dump(mode="json"),
    }


# ── 编辑器：正文在磁盘（ADR 0007），DB 只是派生索引 ──────────────────────────


class ChapterSave(BaseModel):
    markdown: str
    expected_text_sha256: str
    """调用方**依据的那一份**正文的哈希，来自 `GET …/text` 或成功索引回执。

    它是保存的乐观闸（ADR 0021 那道闸的产品形态）：写盘前在锁内与磁盘/DB 当前值
    比对，不一致 → 409 `chapter_changed`，一个字节都不写。前端不能自己另算一份
    经过编辑器转换的「服务端 hash」。
    """


@app.get("/api/projects/{project_id}/chapters")
def chapters(proj: Any = Depends(load_project)) -> Any:
    """列章：直接扫磁盘 {root}/chapters/NNNN.md（磁盘是正文真相源）。title = 首个非空行。

    文件名解读走 `importer.chapter_files()`——**它和 `chapter_path()` 必须同解**，
    这里曾经抄着第二份 `^(\\d{4,})\\.md$`。
    """
    return [
        {"number": entry.number, "title": entry.title}
        for entry in importer.chapter_files(Path(proj.root_path))
    ]


@app.post("/api/projects/{project_id}/chapters", status_code=201)
def create_chapter(
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """在这本书末尾新起一章（空的）。**没有请求体，也没有章号入参。**

    章号由磁盘上现有的文件决定（`importer.append_chapter`）——同「作者不填 `valid_from`」
    那条规矩：让他填章号 = 邀请他把第 7 章建成第 3 章，而章号是全书的顺序键，
    错一位整本书的时态过滤跟着错。

    章标题也由这儿写（`第N章`），不是作者填的：它是切章的锚，写坏了这一章 sync 不进库。
    起完名再改走的是**改正文第一行**那条老路（`chapterTitle.ts`），没有第二个入口。
    """
    try:
        number, _ = importer.append_chapter(store, proj.id, Path(proj.root_path))
    except FileExistsError:
        # 另一个窗口（或另一个进程）在这两步之间已经把那一章建出来了。
        # **不覆盖**：那边可能已经写了字。让作者刷新一下看见它，而不是把它清空。
        raise HTTPException(
            409,
            {
                "error": "chapter_exists",
                "message": "这一章刚刚已经被建出来了（另一个窗口？）。刷新一下就能看见它。",
            },
        )
    entry = next(
        (e for e in importer.chapter_files(Path(proj.root_path)) if e.number == number),
        None,
    )
    return {"number": number, "title": entry.title if entry else ""}


@app.delete("/api/projects/{project_id}/chapters/{chapter}")
def delete_chapter(
    chapter: int,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """删掉一章。**引擎在它上面记过东西就拒绝**（409 `chapter_in_use`，带明细）。

    那条拒绝的完整论证在 `GraphStore.delete_chapter` / `ChapterInUse`：一句 DELETE
    会连着把指向这一章的关系和证据**无声地**级联掉，而那些是这个产品唯一的资产。

    正文不是删掉是**挪走**（`importer.remove_chapter` → 书文件夹底下的 `deleted/`）——
    作者按错了还找得回来。出参里**不带那个路径**：屏幕上不摆文件路径（作者不看路径），
    要找回来是在他自己的文件夹里找。

    **章号不重排。** 删掉第 3 章之后还是 1、2、4——章号是全书的顺序键，
    每一条边和每一条情节的 `valid_from` 都钉在它上面，重排一次等于把整本书的时态挪位。
    """
    try:
        importer.remove_chapter(store, proj.id, Path(proj.root_path), chapter)
    except importer.ChapterMissing:
        raise HTTPException(
            404,
            {
                "error": "chapter_missing",
                "message": f"第 {chapter} 章已经不在了。刷新一下就对得上了。",
            },
        )
    return {"deleted": True, "number": chapter}


@app.get("/api/projects/{project_id}/chapters/{chapter}/history")
def chapter_history(
    chapter: int,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """这一章的历史快照（版本对比的料）。**内容去重、非全量版本史**——同内容只存一次。

    正文可能不小，逐条带 text 是给前端做 diff 用的（一章几十 KB，可接受）。空 = 这一章
    还没进过库（先 sync/import）。
    """
    return [s.model_dump(mode="json") for s in store.chapter_snapshots(proj.id, chapter)]


@app.delete("/api/projects/{project_id}/chapters/{chapter}/snapshots/{snapshot_id}")
def delete_chapter_snapshot(
    chapter: int,
    snapshot_id: str,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """删掉一条历史版本。**只删真的没人引的那种**（图层的两条不变式，见
    `GraphStore.delete_chapter_snapshot`）：当前那条 → 409 `snapshot_is_current`；
    被证据/抽取/提案引着 → 409 `snapshot_in_use` 带明细；不存在/跨项目 → 422 `store_error`。

    **没有「还原」这条路由**：还原就是把旧正文写回磁盘，走 `PUT .../text` 那条——
    快照按内容去重，写回去 sha 命中已有那条，于是 `is_current` 移过去、不新增一行。
    """
    store.delete_chapter_snapshot(proj.id, chapter, snapshot_id)
    return {"deleted": True, "snapshot_id": snapshot_id}


@app.get("/api/projects/{project_id}/chapters/{chapter}/text")
def chapter_text(
    chapter: int,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """读磁盘 md（不是 DB 快照——那是给证据锚用的冻结版，不是可编辑的正文）。

    出参带 `text_sha256` 与 `snapshot_generation`：前者是下一次 PUT 的 expected
    server base；后者让前端在还原旧版后知道 generation 已经前进。
    """
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    markdown = file.read_text(encoding="utf-8-sig")
    return {
        "number": chapter,
        "markdown": markdown,
        "text_sha256": importer.text_digest(markdown),
        "snapshot_generation": store.current_chapter_generation(proj.id, chapter),
    }


@app.put("/api/projects/{project_id}/chapters/{chapter}/text")
def save_chapter(
    chapter: int,
    body: ChapterSave,
    conn: Any = Depends(get_conn),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """保存：写盘前结构预检 → 章级锁 → 原子替换 → 单章落库 → 提交后复核。

    返回 `ChapterSaveReceipt`。结构预检失败 422 且磁盘/DB 不动；expected hash 冲突
    409；锁超时 423；`os.replace` 成功后的目录 fsync / DB 提交失败返回 202
    `durability_failed` / `sync_failed`（`saved_to_disk=true,indexed=false`），
    恢复靠后续 reconcile，不是让前端重发 PUT。
    """
    try:
        receipt = importer.save_chapter(
            store,
            proj.id,
            Path(proj.root_path),
            chapter,
            body.markdown,
            expected_sha256=body.expected_text_sha256,
        )
    except importer.ChapterMissing:
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    _trigger_refresh(conn, store, proj.id, chapter, receipt)
    return receipt


def _trigger_refresh(
    conn: Any,
    store: Any,
    project_id: str,
    chapter: int,
    receipt: importer.ChapterSaveReceipt,
) -> None:
    """保存动作的固定刷新触发点（Task 16 / ADR 0029）。

    `changed=true` 时快照/generation 已经再往前走一版——dispatcher 只认持久
    attempt，所以这里为当前 generation 建一个覆盖性 attempt（缺哪支补哪支）。
    `changed=false`（同 hash）也调 `ensure_refresh_coverage`：正文没变 ≠ 首次
    整理做了，head/app 缺失时仍要补缺（幂等 coverage，不重复付费）。

    **绝不在内存里 enqueue 模型调用**：只写 `chapter_refresh_attempt`（lease/fence
    化），由后台 dispatcher（`NH_BACKGROUND_RUNTIME=1` 时自动开）转成结果。
    """
    current = next(
        (ct for ct in store.current_snapshots(project_id) if ct.number == chapter), None
    )
    if current is None:
        return
    generation = store.current_chapter_generation(project_id, chapter) or 1
    # 当前章防抖（2026-08-18 §3）：作者正盯着的那一章，保存时**不排总结**；
    # 验证/抽取照跑。切走 / 心跳过期后才重新够格（被清的位在下一次触发时补上）。
    from ..focus import is_focused

    skip_summary = is_focused(conn, project_id, chapter)
    try:
        from ..chapter_refresh import ensure_refresh_coverage

        result = ensure_refresh_coverage(
            conn,
            project_id=project_id,
            chapter_id=current.chapter_id,
            snapshot_id=current.snapshot_id,
            generation=generation,
            ruleset_epoch=_current_ruleset(conn, project_id)[0],
            ruleset_hash=_current_ruleset(conn, project_id)[1],
            skip_summary=skip_summary,
            retirement=receipt.retirement,
        )
    except Exception:
        # 触发失败不把「保存成功」拖下水：正文已落库，dispatcher 启动恢复会再扫。
        result = None
    if result is not None:
        conn.commit()


def _current_ruleset(conn: Any, project_id: str) -> tuple[int, str]:
    from ..checks.service import current_ruleset

    return current_ruleset(conn, project_id)


class FocusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)


@app.post("/api/projects/{project_id}/focus")
def report_focus(
    body: FocusBody,
    proj: Any = Depends(load_project),
    conn: Any = Depends(get_conn),
) -> dict[str, Any]:
    """作者换章/开书时上报「我现在在第 N 章」（2026-08-18 §3 当前章防抖）。

    **免费心跳，绝不触发任何总结/抽取/付费**：只 upsert「当前章」位置 + 心跳时间。
    它回答的唯一问题是「作者眼睛现在停在哪一章」——保存传的 chapter 是「保存哪一章」，
    两者不相等。读端（保存触发/调度器）靠它决定给不给某章排总结：正写的那章不排，
    切走/心跳过期后重新够格。**不校验章号存在**：开书汇报的是「我要停在哪」，不要求
    那一章已经进库。
    """
    from ..focus import report_focus as _set_focus

    _set_focus(conn, proj.id, body.chapter)
    conn.commit()
    return {"chapter": body.chapter, "project_id": proj.id}

# ⚠️ **场景块那两条路由（`GET`/`PUT …/chapters/{n}/scenes`）2026-08-14 删了**
# （[ADR 0027](../../../docs/adr/0027-scene-blocks-cut.md)），连同 `SceneWrite` 和 R4。
#
# 它们是「面板的『在场是谁』的来源」的第一版，而 ADR 0018 早就把主路换成了从正文数出来
# （`mentioned.py`）；剩下的那半条身份是「作者手标了就压过推算」——**而真书上他一次都
# 没标过，也不该被要求去标**。把它留着的代价不是几行死代码，是屏幕上一句永远为真的
# 「这一章还没有场景信息」。


# ══════════════════════════════════════════════════════════════════════════
# 写图谱：declare —— 作者敲称呼原文 + 引语，系统算章号
#
# ⚠️ **2026-08-14 起这里只剩四条**：建节点 / 建别名 / 声明死亡 / 声明首现。
# 「谁知道什么 / 谁以为什么 / 谁在哪儿」那三条删了——那类事实只走抽取那条路
# （作者裁决，见 `declare.py` 的「边」那一节）。剩下这四条各有不可替代的理由：
# 前两条是**抽取器自己的前置**（称呼解析不到花名册里唯一的人 = 整条事实被丢），
# 后两条撑着 R3 / R2：`first-appearance` 是 R2 **唯一且只能唯一**的写入方（首现章是
# 作者的计划，不在已写文本里，模型读不出），`death` 2026-08-14 起和抽取器的
# `kind="death"` 并列，留着当「改」的入口。
#
# 入参**没有一个章号字段**（§5.9 / 约束 10）：`valid_from` 只由引语落在哪一章决定，
# `Ledger` 的签名里没有位置能让作者填它。`who`/`loc`/`of` 全是称呼原文，
# 本文件一次都不解析——解析在 `Ledger` 里，于是壳没有机会把歧义的「师兄」偷偷挑成
# 第一个候选（歧义 → AmbiguousName → 409 + candidates，让前端弹消歧下拉）。
# ══════════════════════════════════════════════════════════════════════════


class DeclareNodeBody(BaseModel):
    label: NodeLabel
    name: str
    aliases: list[str] = []

    first_appears_chapter: int | None = Field(default=None, ge=1)
    """**「还没写到」的那一半**：这个东西要到第 K 章才头一回出现（R2 FUTURE_LEAK 读它）。

    ── 这一个数为什么是作者填的，而约束 10 说他永不填章号 ────────────────────

    两者不冲突，因为它们是两类数：

    - `valid_from` 是**回忆**（「这条关系从第几章开始有效」）。他不记得，会填 1，
      而填错的产物是一条在面板上长得完全正常的坏边——所以它只由证据决定（约束 10）。
    - 首现章是**决定**（「幽泉窟我打算第 200 章才让它出场」）。这个信息**物理上不在
      已写文本里**（ADR 0004：墙上那把枪是不是伏笔，取决于他第 200 章打不打算开枪），
      没有任何证据推得出它。同 PLANNED 边的 `valid_from`——001_init.sql 那句
      「那不是回忆是决定」说的就是这类。`NodeProps.first_appears_chapter` 和
      `ForbiddenEntity.first_appears_chapter` 两处 docstring 都写着「作者声明的」。

    **已经写到了的那一半不走这里**：`POST …/declare/first-appearance` 收一句引语、
    自己算出那是第几章（`Ledger.declare_first_appearance`）。**能由证据决定的，
    一律由证据决定**——这个字段只接它够不着的那部分。

    ⚠️ **浏览器上永远不会有它的输入框——2026-08-13 维护者裁定，不是漏掉。**
    `tests/test_canon_edit_boundary.py::test_no_screen_in_the_whole_workbench_posts_a_chapter`
    是一条**零基线**守卫：全前端一个 `<input type="number">` 都不许多、请求体里一个
    撞 `chapter` 的键都不许有。**那条零基线不变，`EXEMPT` 里一个键都不加。**

    理由：「还没写到」的东西按定义没有证据可指，那个数只能是作者拍的；而约束 6 的整条
    立场是「章号只由证据决定」。为这一个**边缘用法**开口，等于在唯一一条挡污染的线上
    开第一个洞——而常见那一半（已经写到了的）走上面那条引语路径，零输入。
    真要支持「预先声明一个还没写的东西在第几章出现」，那是 PLANNED / 伏笔性质的能力，
    该跟 foreshadow 一起走一份 ADR，不是在花名册抽屉里加个框。

    所以这个字段只有 HTTP / CLI 调用方能用，**这是终态不是过渡态**。
    """


class DeclareAliasBody(BaseModel):
    of: str
    surface: str
    kind: AliasKind = AliasKind.ALIAS
    usable_for_rules: bool = True


class DeclareDeadBody(BaseModel):
    who: str
    quote: str


class DeclareFirstAppearanceBody(BaseModel):
    of: str
    quote: str


@app.post("/api/projects/{project_id}/nodes")
def declare_node(
    body: DeclareNodeBody,
    store: Any = Depends(get_store),
    ledger: Ledger = Depends(get_ledger),
    proj: Any = Depends(load_project),
) -> Any:
    """声明一个节点（7 类 label）。幂等（键 = name）。"""
    node = ledger.declare_node(
        body.label,
        body.name,
        aliases=body.aliases,
        # `declare_node` 的 props 是 patch（`exclude_unset`）：`None` = 一个字段都不动，
        # 给了这一个 = 只盖这一个。别在这里传 `NodeProps()` ——那读起来像「没给」，
        # 但它会被当成一次显式的空 patch。
        props=(
            NodeProps(first_appears_chapter=body.first_appears_chapter)
            if body.first_appears_chapter is not None
            else None
        ),
    )
    return _narrowed(node, None)


@app.post("/api/projects/{project_id}/aliases")
def declare_alias(
    body: DeclareAliasBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """给已有节点加一个称呼。别名永不合并实体（ADR 0004）。

    单字别名 + usable_for_rules=True 会被 `AliasSpec` 的 validator 拒（ADR 0004）→
    ValidationError → 422 带作者可读的那半句。kind=canonical 同样被拒（upsert_node 独占）。
    """
    stored = ledger.declare_alias(
        of=body.of,
        surface=body.surface,
        kind=body.kind,
        usable_for_rules=body.usable_for_rules,
    )
    return stored.model_dump(mode="json")


# ⚠️ **`/declare/knows`、`/declare/believes`、`/declare/where` 2026-08-14 删了**
# （作者裁决：「谁知道什么 / 谁在哪儿」只走抽取那条路，见 `declare.py` 那段注释）。
#
# **下面那两条不是同一组，别顺手删掉**：`/declare/death` 和 `/declare/first-appearance`
# 撑着 R3 / R2：`first-appearance` 是 R2 唯一且只能唯一的写入方（首现章是作者的计划，
# 不在已写文本里）；`death` 和抽取器的 `kind="death"` 并列，留着当「改」的入口。


@app.post("/api/projects/{project_id}/declare/death")
def declare_dead(
    body: DeclareDeadBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """「他在这段原文里死了」。**R3 DEAD_SPEAKS 的生产写入方之一**（另一个是抽取器的
    `kind="death"`，2026-08-14 落地；这一条留着当「改」的入口）。

    这条路由之前，`StateSnapshot.is_dead` 在生产上恒为 False：`EdgeProps.value_key`
    零写入方、`StateDim` 零创建路径，于是「死人还在说话」结构上永远查不出来。

    章号照旧由引语算（`Declaration.valid_from`）。生死这个维度由引擎自己建
    （`ensure_state_dim`），请求体里没有它——它是引擎的内部结构，不该出现在作者填的表单里
    （同 `AUTHORED_LABELS` 有意不含 StateDim 的那条理由）。
    """
    return ledger.declare_dead(who=body.who, quote=body.quote).model_dump(mode="json")


@app.post("/api/projects/{project_id}/declare/first-appearance")
def declare_first_appearance(
    body: DeclareFirstAppearanceBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """「他/它在这段原文里头一回露面」→ 首现章。**R2 FUTURE_LEAK 的作者入口。**

    **请求体里没有章号，也不该有**：首现章 = 这句引语落在哪一章，系统自己算
    （约束 10 在这条路由上和别的写路由是同一套：入参里没有一个位置能填章号）。已经写到了的东西一律走这条；
    还没写到的（「第 200 章才出场」）没有引语可指，那一半在 `POST /nodes` 的
    `first_appears_chapter` 字段上，见那儿的说明。

    出参是 `FirstAppearance`：节点已是窄引用（`NodeRef`），不含 props，无需再收窄。
    """
    return ledger.declare_first_appearance(of=body.of, quote=body.quote).model_dump(mode="json")


# M2 / M4 剩余 stub —— 稳定的 501，**不是 404**（UI_ARCHITECTURE §1.2 第 48 行）
#
# M4 抽取与事件读端已经在 ``api/extraction.py`` 点亮；这里保留的能力仍未开放。它们今天
# 存在的理由只有一个：**让前端把按钮画成灰的，而不是把按钮藏起来。**
#
# 为什么必须是 501 而不是 404：404 在这个壳里已经有确切含义——「你要的那个东西不在」
# （项目/章/节点/证据查无此物）。让「这个能力还没做」也返 404，前端就**分不出**
# 「M2 才有」和「我把路径拼错了」：一个是要渲染灰按钮 + 里程碑提示，另一个是前端
# 自己的 bug 该报错。分不出时前端只有一条保守路可走——干脆不画那个按钮，于是作者
# 在界面上永远看不到「这里将来会有什么」。501 + milestone 把两件事分开，并且**把
# 里程碑名当数据发过去**，而不是让前端硬编码一张「路径 → 里程碑」表（那张表会和
# 后端漂移，且漂移的时候没有任何东西会红）。
#
# 这些路由**一个 Depends 都不接、一个路径参数都不声明**：「这个能力还没实现」这个
# 答案不取决于库里有什么。接了 load_project/get_store，答案就变成取决于项目在不在、
# 库连不连得上——前端拿到 404/500，按钮的灰与亮被无关的东西决定了。
#
# 实现的时候换掉的是**函数体**：路径不动、前端调用不动，501 消失那一刻灰按钮自然变亮。
# ══════════════════════════════════════════════════════════════════════════

_NOT_IMPLEMENTED = "not_implemented"
_DraftLengthBody = Annotated[
    LengthSpec,
    AfterValidator(DEFAULT_LENGTH_POLICY.validate_spec),
]


def _stub(milestone: str) -> dict[str, str]:
    """501 的响应体。形状由 UI_ARCHITECTURE §1.2 定死：`{status, milestone}`。

    `status_code=501` 写在装饰器上（而不是这里返 JSONResponse），是为了让它进 openapi
    ——前端从 schema 就能看见这条路由今天只会 501，不必等运行时撞上。
    """
    return {"status": _NOT_IMPLEMENTED, "milestone": milestone}


class DraftRequest(BaseModel):
    """**行内续写**请求体（ADR 0015）。长度按 ADR 0013 随请求走。

    ── 2026-08-26：这条路由上「起草一整章」那个入口删了 ──────────────────────

    它是一路死掉的：2026-08-10 删「AI 起草」抽屉，2026-08-14 删 `useDraft` hook
    （当时注释写明「后端 `POST …/draft` 一个字没动」），此后**浏览器零调用方**；
    而模式二的 `draft_chapter` 工具走的是**进程内直调**（`agent/drafting.py` →
    `draft.product_draft.draft_chapter`），根本不发 HTTP。

    **`draft/product_draft.py` 一个字没动**：整章起草的实现、它的 `mode="chapter"`
    默认值、模式二那条路全都活着。删掉的只是「从这条 HTTP 路由进整章起草」这个入口。

    于是 `mode` 和 `goal` 跟着走了：

    - `mode` —— 这条路由今天只有一种形状。留一个恒等于 `"continuation"` 的字段，
      等于在契约上摆一个假的选择，而 OpenAPI 会把它当真发布出去。
    - `goal` —— ADR 0015 D3 要求续写的提示语是**后端常量**（前端能传的东西作者就能改，
      而 `goal` 是 ADR 0010 点名的泄漏入口之一）。从前靠校验器拦「续写不许带 goal」，
      现在**这条纪律由形状本身保证**：请求体上没有这一位，就没有东西可拦。

    ⚠️ **这个模型没有 `extra="forbid"`，是有意的**（2026-08-25 定的，
    `test_the_draft_body_no_longer_takes_a_form` 写着理由）。后果要说清楚：
    还在发 `mode` / `goal` 的旧客户端**不会收到报错，会收到一段续写**。
    这个仓库里没有那样的调用方（上面那段就是在说这件事），所以今天没有受害者；
    `test_a_stale_chapter_shaped_body_now_gets_a_continuation` 把这个行为钉住，
    免得下一个人以为它会 422。
    """

    cast: list[str] = []
    """在场称呼原文。**可空**：留空 = 「不知道这一场有谁」（ADR 0015 D4）。

    填了则收紧到精确约束——**它是让续写写得更准的奖励，不是不填就不给用的门槛。**
    前端今天不传（`useContinuation`），但这一位是 D4 明写的设计，不是残留：
    「谁在场」是被那一段写出来的结果，不是前提，所以不能拿它当门槛。"""

    length: _DraftLengthBody
    previous_tail: str = ""
    following_text: str = ""
    """光标**后面**那截同章正文。

    作者跳回去改第 2 章、光标停在中间时，后面那几千字是**已经写好的正文**。
    不给模型看，它写出来的一段就可能跟紧接着的下一段接不上，或者把它重写一遍。
    截多长由后端那一份公式说了算（同 `previous_tail`，见 `_continuation_tail`），
    这一层只是收下——**前端不许自己判断给多少**。
    """

    write_rule: str = ""


def _draft_provider_config():
    """产品起草的连接参数：AI 设置页（BYOK）优先，环境变量兜底。

    ADR 0013 / 分发文档点名的「配置优先」路径：桌面作者没有环境变量，
    钥匙在设置页里（`settings.py`，本机 0600）。
    """
    from ..draft.provider import ProviderConfig
    from ..settings import load as load_user_settings

    user = load_user_settings()
    return ProviderConfig(
        base_url=user.base_url or os.environ.get("NH_LLM_BASE_URL", ""),
        model=user.model or os.environ.get("NH_LLM_MODEL", ""),
        api_key=user.api_key or os.environ.get("NH_LLM_API_KEY", ""),
        temperature=None,
    )


class _SummaryBackfillDesk:
    """行内续写取总结时的**第三个触发源**（`draft.product_draft.SummaryBackfill`）。

    它只做两件事：问这个窗口里每一章的总结能不能用（`summary_alignment`，唯一出口），
    给缺 / 旧的那几章下**同一种单**（`request_summary_backfill` → `chapter_refresh_attempt`）。
    这一次先不给那几章——生成一章总结是一次模型调用、几秒起步，而续写的整个预算是
    400 毫秒（ADR 0019）。后台补完，下一次续写就有了。

    **自己提交**：`/draft` 这条路上没有别的地方会 commit（记账那一行走
    `record_receipt` 自己提交），单不落盘就等于「报了没做」——今天刚修掉的同一个病。

    **一分钱都不花**：单只是 `chapter_refresh_attempt` 里的一行，真正的生成由后台
    dispatcher 领走（`api/background_runtime.py`）。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def request(
        self, project_id: str, first_chapter: int, last_chapter: int
    ) -> SummaryBackfillReply:
        from ..checks.service import current_ruleset
        from ..summary_schedule import QUEUED_OUTCOMES, request_summary_backfill

        window = range(first_chapter, last_chapter + 1)
        try:
            epoch, ruleset_hash = current_ruleset(self._conn, project_id)
            decisions = request_summary_backfill(
                self._conn,
                project_id,
                first_chapter=first_chapter,
                last_chapter=last_chapter,
                ruleset_epoch=epoch,
                ruleset_hash=ruleset_hash,
            )
            self._conn.commit()
        except Exception:  # noqa: BLE001
            # 待办这一侧不高兴（库缺 ruleset 行之类）不该让作者的续写 500。
            # **但也不能因此把没验过的总结喂进去**：答不出「能不能用」就一律不给
            # （同 ADR 0033 §8.2：无法证明与当前快照一致的总结不进 Writer）。
            self._conn.rollback()
            return SummaryBackfillReply(unusable=frozenset(window))
        return SummaryBackfillReply(
            # **默认拒**：只有明确答 `paired` 的那几章这一次用得上。查不到的章
            # （比如当前快照对不上，一行都没扫到）走的是「不在 decisions 里」，
            # 那一档必须落在拒的一侧，不能靠「没说不行就是行」漏过去。
            unusable=frozenset(n for n in window if decisions.get(n) != "paired"),
            ordered=tuple(
                sorted(n for n, outcome in decisions.items() if outcome in QUEUED_OUTCOMES)
            ),
        )


def _resolve_track(
    conn: Any, store: Any, project_id: str, *, chapter: int, text: str
) -> Any:
    """算这一次的轨道（`track.build_track`），**并且保证它绝不会把写作这条路弄崩**。

    ⚠️ **它算出来的东西一个字都不进 Writer 的 prompt**（`track.py` 模块头讲了为什么：
    后面章节的总结里可能写着这一章的读者还不该知道的事）。它的出口只有两个：
    响应里那一格 `track`，和将来验证那一侧。**别把它拼进任何一段 messages。**

    轨道是护栏不是素材：算不出来就没有护栏，但那不该变成「这一段写不出来」。
    所以这儿兜住一切异常，代价只有一条——`note` 必须说出「这一次没算成」，
    否则空轨道和「后面真的没有相关章节」在界面上长成同一个样子（§10 约束 8）。
    """
    from ..track import Track, build_track

    try:
        return build_track(conn, store, project_id, chapter=chapter, text=text)
    except Exception:  # noqa: BLE001
        # 倒排表补扫是一次写事务（`summary_index._rebuild`），炸在半路要把这条连接放干净——
        # 后面还有真正要落盘的东西（记账那一行）在用同一条连接。
        conn.rollback()
        return Track(
            chapter=chapter,
            frontier=0,
            note="这一次没能算出轨道（后面那些章有没有相关设定，这一稿不知道）。",
        )


@app.post("/api/projects/{project_id}/chapters/{chapter}/draft")
def draft(
    body: DraftRequest,
    chapter: int,
    project_id: str,
    store: Any = Depends(get_store),
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """**行内续写**第 N 章（ADR 0015；实验状态，修正案 7）。

    放行 ≠ 验证：响应带 ``experimental`` 标注，kill-gate 裁决前不声称图谱约束有效。
    若将来裁决 KILL，撤销本路由 = 一次显式 commit（修正案 7 原文）。

    ── 2026-08-26：这条路由只剩续写一种模式 ─────────────────────────────────

    「起草一整章」那个入口从这儿删了（理由在 `DraftRequest` 的 docstring 里）。
    **`draft/product_draft.py` 一个字没动**——整章起草的实现活着，模式二的
    `draft_chapter` 工具照旧进程内直调它，只是不再有第二条 HTTP 入口。

    ── 这条路由不自己拼 prompt（2026-08-11）────────────────────────────────

    「章号 → 一稿正文」的实现在 `draft/product_draft.py::draft_chapter()`，
    **因为 agent 的起草工具要调同一个函数**（3.6 / ADR 0021）。留在这儿的只有壳该干的
    三件事：BYOK 连接参数、算这一场的约束、把领域异常翻成给作者的话。

    **这条路由不落盘。** 行内续写按定义就不该落盘：那一段落进编辑器缓冲区，作者按了
    Tab 才是磁盘上的字（ADR 0015 D5）。落盘是 agent 那条路的事（ADR 0021 只推翻了
    「agent 写正文要先问」，没有给这条路由加一个作者没按过的保存）。
    """
    from ..draft.capabilities import (
        CapabilityError,
        ReasoningEffort,
        plan_call,
    )
    from ..draft.context import ResolvedConstraints, unknown_cast_constraints
    from ..draft.product_draft import ChapterDraftRequest, DraftRefused, check_request
    from ..draft.product_draft import draft_chapter as run_draft
    from ..draft.provider import ProviderError
    from ..draft.rolling_summary import SummaryStore
    from ..extract.call_audit import ModelCallReceipt, record_receipt
    from ..graph.sqlite_events import SqliteEventStore
    from ..ids import EntityType, new_id
    from ..panel.constraints import UnresolvedCast, scene_view

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")

    try:
        config = _draft_provider_config()
        # 作者手填的窗口在这儿压过一切（`deps.resolve_route_capabilities`）——
        # **这条路由是那个数唯一真正花钱的消费者**：它决定逐字上文给他 800 字还是上万字。
        capability = resolve_route_capabilities(config)
        # **`OFF`，不是从前那个 `HIGH`。** 论证不在这儿写第二份：见
        # `agent/drafting.py::AGENT_DRAFT_REASONING` 的 docstring —— 同一条理由、
        # 同一个症状（`resolve_capabilities` 对**没登记的路由**只给
        # `reasoning_levels={OFF}`，于是 `HIGH` 在作者自建的端点上是当场 `CapabilityError`）。
        #
        # 那份 docstring 的末尾曾经写着「`/draft` 那条路仍然是 `HIGH`，这一刀不动它」，
        # **那句话在 2026-08-14 是对的**：当时这条路由没有调用方。是 ADR 0015 把行内续写
        # 接到同一条路由上，把它的前提推翻了 —— 从此这个 `HIGH` 压在续写头上，作者停手
        # 400 毫秒就吃一个 422，而报的话是「模型没配好，先去填服务地址/模型/钥匙」，
        # 把他支去重填一份根本没问题的配置。（2026-08-26 两处一起改掉。）
        plan = plan_call(body.length, ReasoningEffort.OFF, capability)
    except (ValidationError, ValueError, CapabilityError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"模型没配好：{exc} —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，"
                "或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。"
            ),
        )

    # 轨道（`track.py`）：**作者是不是跳回去改旧章**，以及后面哪几章的总结跟这一段相关。
    # 零模型调用、零花费，两级都是查库 + 一条正则。就在最前沿写 = 空的，什么都不变。
    #
    # ⚠️ **它下面一行都不许流进 messages。** 出口只有响应里那一格 `track`
    # （见 `_resolve_track` 和 `track.py` 模块头；`tests/test_track_isolation.py` 钉着）。
    track = _resolve_track(
        conn,
        store,
        proj.id,
        chapter=chapter,
        # 「作者刚改的那段字」在这一层拿得到的最接近的东西：这一次请求带上来的正文。
        text="\n".join(part for part in (body.previous_tail, body.following_text) if part),
    )

    # **plan 提前到装配之前**：记忆层的预算要从 `capability.max_context_tokens` 倒推
    # （`memory_units_available`），所以得先知道模型是谁。它不依赖 messages，提前无副作用；
    # 而且「模型没配好」这种错在这儿就报出来，比装配完一大堆上下文再报便宜。
    request = ChapterDraftRequest(
        # `mode` / `goal` 这两位不再从请求体来（2026-08-26，见 `DraftRequest`）：
        # 这条路由只剩续写一种形状，而续写的提示语是后端常量——`draft_chapter()` 自己
        # 用 `CONTINUATION_GOAL` 顶掉这一位（它**只在整章那一支才读 `request.goal`**），
        # 所以这儿给空串是「这一位在这条路上没有意义」，不是「忘了填」。
        goal="",
        length=body.length,
        mode="continuation",
        previous_tail=body.previous_tail,
        # 【下文】**只在改旧章时给**。最新章的常态是往末尾写，光标后面没有字；
        # 而「是最新章时行为一字不变」是当时那一刀明写的验收条件，所以那一档一个字节都不动。
        # 要在最新章中间插写时也给，那是一次单独的产品决定，别藏在别的改动里。
        # 轨道没算成时 `at_frontier` 也是 True（fail-safe，见 `Track.at_frontier`）。
        following_text="" if track.at_frontier else body.following_text,
        write_rule=body.write_rule,
    )
    try:
        # **在算约束之前先验一次文风。** `draft_chapter()` 自己也会验（agent 那条路没有
        # 这一步），这里多调一次是为了保住 422 的先后顺序：文风里写了禁令词和在场角色
        # 解析不了同时发生时，作者收到的仍然是文风那一句。重复的是执行，不是实现。
        # （2026-08-25 之前它还验一个 `form`；三臂随 M2 一起删了。）
        check_request(request)
    except DraftRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        # 两条构造路径，**类型不同**（ADR 0015 D4）：拿到 `ResolvedConstraints` 就等于
        # 「cast 已解析且非空」，拿到 `UnknownCastConstraints` 就等于「不知道谁在场，全禁」。
        # 退化态只可能从这一支来，kill-gate 走不到——臂间比较不会被更严的卷子污染。
        if body.cast:
            ctx = ResolvedConstraints.of(
                scene_view(store, project_id, chapter, body.cast), body.cast
            )
        else:
            ctx = unknown_cast_constraints(store, project_id, chapter)
    except UnresolvedCast as exc:
        raise HTTPException(status_code=422, detail=f"在场角色解析不了：{exc}")

    def bill(receipt: ModelCallReceipt) -> None:
        """一次真的模型调用 = 一行 `model_call`。**当场落，不等这一稿拼完。**

        `draft_chapter` 一落地就叫这个（`on_call`），而「第一次答上来了、续写那次断线」
        是一档真会发生的失败——攒到最后记账的话，那时钱已经付掉、`calls` 却随异常没了。
        章号由这一层说：`ctx.chapter` 就是这一稿写的那一章（`draft_chapter` 也从它取，
        所以不存在两个来源对不上的可能）。
        """
        record_receipt(
            conn,
            receipt,
            project_id=project_id,
            chapter_number=ctx.chapter,
            call_id_factory=lambda pid: new_id(EntityType.CALL, pid),
        )

    try:
        drafted = run_draft(
            ctx,
            request=request,
            project_id=project_id,
            config=config,
            capability=capability,
            plan=plan,
            events=SqliteEventStore(conn),
            summaries=SummaryStore(conn),
            # 续写那一支要它（缺 / 旧的章下单 + 这次别用）；整章起草不看这一位。
            backfill=_SummaryBackfillDesk(conn),
            on_call=bill,
        )
    except DraftRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UnresolvedCast as exc:
        raise HTTPException(status_code=422, detail=f"在场角色解析不了：{exc}")
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}")

    # 账在上面那个 `bill` 里已经落了（2026-08-12）。**这儿一个字节都不许多**：
    # 这个响应体是前端契约的一部分，「账记上了」是库里多几行，不是出参多一个键。
    # （`drafted.calls` 仍然带着那 1–2 份原料，是留给不走 `on_call` 的调用方的。）
    result = drafted.result
    last = result.attempts[-1].result
    return {
        "experimental": True,
        "note": "实验状态：未经 kill-gate 裁决，图谱约束是否有效尚未证实（修正案 7）。",
        "text": result.text,
        "memory": drafted.memory,
        # `memory` 说的是「这一稿的 prompt 里装了什么」，`track` 说的正好相反：
        # 「这一次查到了后面哪几章跟它相关，而且它一个字都没进 prompt」。
        # 两格必须分开报——合成一格的那一天，就会有人顺手把它拼进记忆前言。
        "track": track.model_dump(mode="json"),
        "length": result.length.model_dump(mode="json"),
        "attempts": len(result.attempts),
        "model": last.model,
        "finish_reason": last.finish_reason,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }


# ── 章节滚动总结（M4 后续切片）──────────────────────────────────────────────
#
# 在这两条路由之前，滚动总结**只有 `nh summarize` 一个入口**（会调模型、会花钱），
# 于是任何一个从浏览器建起来的库里 `chapter_summary` 表恒为空，写作 prompt 的
# 【更早章节滚动总结】永远渲染成「- 暂无」，而作者看不到任何提示。2026-08-06 的
# 「最后一厘米」盘点把这条记成了三个洞里的第三个。
#
# ⚠️ **这一段原来写着「故意不做『保存章节后自动生成』」——那句话已经不成立了。**
# ADR 0029 之后保存就会触发（`PUT …/text` → `_trigger_refresh` → 落单 → dispatcher →
# `ensure()`），2026-08-25 起它更是仅剩的两个触发之一。
#
# ── 2026-08-25：这一摊从四条收成三条，付费入口一条都不在这儿 ─────────────────
#
# **总结的触发只剩两个，都是系统自动的**：保存之后、每 30 分钟扫描。两个都不经过
# 这一层——它们下的是 `chapter_refresh` 的单，由 `api/background_runtime.py` 那个
# adapter 去付钱（全 `src/` 唯一一处 `.ensure()`，`test_arch_guard.py` 钉着）。
#
# 所以这条路径上**再也没有会花钱的动作**，删掉的是 `POST …/summary`（右栏那颗
# 「生成（要跑一次模型）」按钮是它唯一的调用方）和 `POST …/summary/regenerate`
# （那条更早就死了：写 `PENDING` 然后回 `queued=True`，而没有任何一处认领章节的
# PENDING job）。剩下三条共用一个路径，因为它们是同一个东西的三个动作：
#
#   GET     这一章现在的总结（**没有的时候说清是哪一种没有**）
#   PATCH   换成作者自己写的那一段（不花钱）
#   DELETE  撤回（不花钱；**库里一行都不少**，见迁移 013）
#
# ⚠️ **撤回是终态**：两个自动触发都不碰撤回过的章，而作者手上已经没有「再生成一次」
# 那条退路了。他还能自己写一段（PATCH）。见 ADR 0030 的补记。
#
# `…/summaries`（复数）是另一件事，别合并：它回答「起草这一章时那一层覆盖成什么样」。


@app.get("/api/projects/{project_id}/chapters/{chapter}/summaries")
def chapter_summaries(
    chapter: int,
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """起草第 chapter 章时，滚动总结那一层覆盖的区间里每一章的状态。

    窗口就是「本章之前的全部章」，**不再由事件窗口倒推**。那个耦合曾经存在，方向是坏的：
    事件稀疏时事件边界一路退到第 1 章，滚动总结就一条都进不去——静默地退回「没有记忆」。
    事件和总结是互补的（一件事 vs 一整章讲了什么），各有各的预算，见 `MemoryBudget`。

    真正进 prompt 的是这个区间里**最近的、预算装得下的那些**（`build_product_context`
    从最新一端往回收）。这里报的是**覆盖率**，所以给全区间：作者要看的是「哪几章还没总结」，
    不是「这一稿用了哪几章」——后者在起草回执里。

    `missing` 是「有正文、但没生成过总结」的章号，**这才是要提示作者的那一种零**；
    没有正文的章在 `chapters[].has_text=false` 里，是另一件事，别合并显示。
    """
    from ..draft.rolling_summary import SummaryStore

    first, last = 1, chapter - 1
    rows = SummaryStore(conn).coverage(proj.id, first, last)
    return {
        "chapter": chapter,
        "window_first": first,
        "window_last": last,
        "chapters": [row.model_dump(mode="json") for row in rows],
        "summarized": len([row for row in rows if row.summary is not None]),
        "missing": [row.chapter_number for row in rows if row.has_text and row.summary is None],
    }


@app.get("/api/projects/{project_id}/summary-status")
def book_summary_status_view(
    proj: Any = Depends(load_project),
    conn: Any = Depends(get_conn),
    draft_chapter: int | None = Query(
        default=None,
        ge=1,
        description="权重原点；缺省取焦点章或「前沿章号 + 1」。",
    ),
) -> dict[str, Any]:
    """全书总结状态视图（2026-08-18 文档 §6 / Step 4）。

    一口气给「这本书现在长什么样」：每一章是配对 / 缺 / 不对齐 / 空，这一轮调度
    会给它多少权重，以及那章最近一次总结 attempt 是不是已经失败（异常标记）。
    全部查库，一次视图查询；**GET 不写任何东西**（异常→通知在 `autonomy_once`
    那一侧落地，不是在这里）。
    """
    from ..focus import focus_current_chapter, resolve_draft_origin
    from ..summary_schedule import book_summary_status

    if draft_chapter is None:
        draft_chapter, focused = resolve_draft_origin(conn, proj.id)
    else:
        focused = focus_current_chapter(conn, proj.id)
    states = book_summary_status(conn, proj.id, draft_chapter=draft_chapter)
    return {
        "draft_chapter": draft_chapter,
        "focused_chapter": focused,
        "chapters": [
            {
                "chapter_number": item.chapter_number,
                "has_text": item.has_text,
                "state": item.state,
                "weight": item.weight,
                "anomaly": item.anomaly,
            }
            for item in states
        ],
    }


def _summary_state(conn: Any, project_id: str, chapter: int) -> dict[str, Any]:
    """第 chapter 章现在的总结状态。**四条路由共用同一个出参形状。**

    形状就是 `…/summaries` 里那一行（`ChapterSummaryStatus`）：一个动作做完之后，
    界面拿到的和它重新读一遍拿到的**逐字节相同**——两个形状的话，「改完之后屏幕上
    显示的」和「刷新之后显示的」就有机会不一样，而那种不一样没有任何东西会报错。
    """
    from ..draft.rolling_summary import SummaryStore

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    rows = SummaryStore(conn).coverage(project_id, chapter, chapter)
    return rows[0].model_dump(mode="json")


@app.get("/api/projects/{project_id}/chapters/{chapter}/summary")
def chapter_summary(
    chapter: int,
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """这一章现在的总结。**没有的时候不许只回一个 null**（§10 约束 8）。

    三种「没有」在出参里分得开，因为它们的下一步动作完全不同：
    `has_text=false`（这一章还没写，没得总结）/ `retracted=true`（作者亲手撤掉的，
    别催他去补一件他刚做的事）/ 两者都不是（有正文、没生成过——那是要花钱的那一步）。
    """
    return _summary_state(conn, proj.id, chapter)


class SummaryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    """作者自己写的那一段。空串走 422 而不是「等于撤回」——**两个动作不许共用一个入口**：
    清空输入框然后保存，和按下「撤回」，在作者脑子里不是一件事。"""

    expected_version_id: str | None = None
    """作者编辑所依据的版本 id（019 head 元数据）。与当前 head 不一致 → 409。

    `None` 有两个意思：字段缺省（旧客户端，不做 CAS）和「作者预期当前无总结」
    （首次编辑，期望 head 为 NULL）。路由用 `model_fields_set` 区分两者。
    """


@app.patch("/api/projects/{project_id}/chapters/{chapter}/summary")
def edit_chapter_summary(
    chapter: int,
    body: SummaryEdit,
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """把这一章的总结换成作者自己写的这一段。**不花钱**，模型写的那一行留在库里。

    幂等：交上来的就是屏幕上那一段时一行都不追加（见 `save_author_summary`）。
    这一章还没有总结时也收——手写一份比先付一次钱再改要合理。
    """
    from ..draft.rolling_summary import (
        SummaryEditConflict,
        SummaryChapterNotFound,
        SummaryTextRejected,
        save_author_summary,
    )

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    try:
        save_author_summary(
            conn,
            project_id=proj.id,
            chapter_number=chapter,
            text=body.summary,
            expected_version_id=body.expected_version_id,
            expect_head="expected_version_id" in body.model_fields_set,
        )
    except SummaryTextRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SummaryEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SummaryChapterNotFound:
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "chapter": chapter},
        )
    return _summary_state(conn, proj.id, chapter)


@app.get("/api/projects/{project_id}/chapters/{chapter}/summary/history")
def chapter_summary_history(
    chapter: int,
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> list[dict[str, Any]]:
    """一章的 append-only 版本历史（019），旧 → 新。**一行都不删。**"""

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    row = conn.execute(
        """
        SELECT c.id FROM chapter c
         WHERE c.project_id = ? AND c.number = ?
        """,
        (proj.id, chapter),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "chapter": chapter},
        )
    versions = conn.execute(
        """
        SELECT id, summary, summary_sha256, schema_version, prompt_hash,
               source, status, source_snapshot_id, replaces_summary_id, created_at
          FROM chapter_summary
         WHERE project_id = ? AND chapter_number = ?
         ORDER BY created_at, rowid
        """,
        (proj.id, chapter),
    ).fetchall()
    return [dict(v) for v in versions]


@app.delete("/api/projects/{project_id}/chapters/{chapter}/summary")
def retract_chapter_summary(
    chapter: int,
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """撤回这一章的总结。**库里一行都不少**（迁移 013：追加一行标记撤回）。

    撤回之后这一章「当作没总结」：起草时不带它、覆盖率里算作缺。
    本来就没有总结、或者已经撤回过，都原样回一个 200 —— 这个动作没有失败的形态，
    而一个 404 只会让作者以为自己弄坏了什么。
    """
    from ..draft.rolling_summary import retract_summary

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    retract_summary(conn, project_id=proj.id, chapter_number=chapter)
    return _summary_state(conn, proj.id, chapter)


# ── 总结 = 可反查的记忆点（T6）───────────────────────────────────────────────
#
# 作者的原话：「每一个总结就相当于一本书的一个记忆点。我想迅速找到需要的内容或相关章节的
# 总结，然后引用、对比、调研，再顺下去看全文。**我不想用 RAG。**」
#
# 这两条路由就是那件事，而且它**不是找相似，是找相关**（判据只有「这个称呼出现了没有」，
# 一个语义判断都没有——完整论证在 `summary_index.py` 的模块 docstring 和迁移 014）。
#
#   GET …/chapters/{n}/summary/mentions   这一章的总结提到了哪些东西
#   GET …/nodes/{node_id}/summary-mentions  还有哪几章的总结提到它（按章号排）
#
# 两条都**不调模型、不花钱**，所以界面上可以随便点。


@app.get("/api/projects/{project_id}/chapters/{chapter}/summary/mentions")
def chapter_summary_mentions(
    chapter: int,
    conn: Any = Depends(get_conn),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """这一章**现在算数**的那段总结里，花名册的哪些东西被提到了。

    **为什么不并进 `GET …/summary` 的出参**：那个形状是四条动作路由共用的
    （`_summary_state`，「一个动作做完之后界面拿到的和它重新读一遍拿到的逐字节相同」），
    而它同时也是 `…/summaries` 里的一行——那一条要报整个窗口，每一章都挂一串芯片
    会让一次覆盖率查询变成一次全书反查。两件事，两个资源。

    出参里**只有 `NodeRef`**（id/label/name）：这批命中里会有作者还没写到的实体，
    而 `Node.props` 装的正是「第 200 章才揭晓」那类东西（§10.5 第 3 条）。

    这一章没有总结（没写 / 没生成 / 撤回过）→ `mentions: []`。**三种「没有」的区分
    不在这儿再答一遍**：屏幕上那一格读的是 `GET …/summary`，那儿已经在说那句话了，
    这儿再说一遍就是第二份措辞。
    """
    from ..summary_index import mentions_in_chapter

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    hits = mentions_in_chapter(conn, store, proj.id, chapter)
    return {
        "chapter": chapter,
        "mentions": [hit.model_dump(mode="json") for hit in hits],
    }


@app.get("/api/projects/{project_id}/nodes/{node_id}/summary-mentions")
def node_summary_mentions(
    node_id: str,
    conn: Any = Depends(get_conn),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """**还有哪几章的总结提到它**，按章号升序。一次 SQL，不调模型、不花钱。

    `node_id` 不在本项目 → `NodeNotFound` → 404（那张错误映射表接的）。
    **不返回空表**：「他没在任何总结里出现过」和「这个 id 根本不存在」是两件事，
    下一步动作也完全不同（§10 约束 8）。

    出参带每一章那段总结的**原文**：作者点开是为了读它、比它、引它，
    再要一次往返只是让他多等一轮。
    """
    from ..summary_index import chapters_mentioning

    # 出参里的 `node` 是**后端给的**，不是前端把刚点的那个芯片回填一遍：换一条进入路径
    # （从花名册、从活动日志点过来）时它手上只有一个 id，没有那个名字。
    return chapters_mentioning(conn, store, proj.id, node_id).model_dump(mode="json")


@app.post("/api/projects/{project_id}/chapters/{chapter}/plan", status_code=501)
def plan_stub() -> dict[str, str]:
    """AI 规划第 N 章的场景骨架（M2）。v1 的替代是作者手拖手填场景块（§2 表）。"""
    return _stub("M2")


# `GET /runs` 曾经也是这里的一条 501，理由写着「`model_call` 表今天是空的」。
# **那句话在 M4 落地那天就过期了**（抽取和滚动总结都在记账，见 `extract/call_audit.py`），
# 而一条声称「这个能力还没有」的 501 挡在一张真有数据的表前面，比空列表更能骗人。
# 2026-08-10 起它是一条真路由，搬去了 `api/activity.py`（和日志读端同一摊）。
