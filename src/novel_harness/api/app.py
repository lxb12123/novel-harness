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
   `NodeProps` 是 `extra="allow"`——一个 Secret/未来节点会把作者写的 `props.twist` /
   `plot_note` 序列化出去（`graph.models.NodeRef` 的 docstring 有实测形态）。所有可能
   含完整 Node 的响应过 `_narrow`：Secret 或「first_appears > 当前章」的节点收窄成
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
    model_validator,
)

from .. import importer
from .. import onboarding
from .. import project as project_mod
from ..checks import ALL_CHECKS, CheckContext, run_checks
from ..settings import Settings as UserSettings
from ..settings import load as load_user_settings
from ..settings import save as save_user_settings
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
from ..graph import (
    AliasKind,
    EdgeType,
    GraphVersion,
    InformationScope,
    NodeLabel,
    NodeRef,
    SecretDetail,
)
from ..graph.store import (
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
    knowledge_matrix,
    resolve_cast,
    scene_constraints,
)
from ..text import (
    AmbiguousScene,
    SceneNotFound,
    SceneWriteRefused,
    parse_scenes,
    write_scene_directive,
)
from ..text import paragraphs as split_paragraphs
from .deps import (
    books_root,
    ensure_schema,
    get_conn,
    get_ledger,
    get_store,
    get_summarizer,
    load_project,
)
from .activity import router as activity_router
from .autopilot import router as autopilot_router
from .chat import router as chat_router
from .extraction import router as extraction_router
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

    `nh serve` 用它决定要不要提醒作者「你现在看到的是降级原型」。这个提醒是必需的：
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


# ── 出参收窄：Secret / 未来节点绝不整体序列化（§1.2 陷阱）──────────────────


def _is_sensitive_node(node: dict[str, Any], chapter: int | None) -> bool:
    """一个已 `model_dump` 的 Node 字典是不是「最不该被完整序列化」的那批。

    判据两条（§1.2 / `NodeRef` docstring）：label==Secret（秘密的 props 里装的正是秘密
    内容），或 first_appears > 当前章（这个节点按定义是关于未来的）。`chapter is None`
    时没有「当前章」可比（如 resolve 花名册查询），只认 Secret——未来判据留给带章号的
    读端。
    """
    if node.get("label") == NodeLabel.SECRET.value:
        return True
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
    yield


app = FastAPI(title="Novel Harness 工作台", lifespan=_lifespan)
app.include_router(activity_router)
app.include_router(autopilot_router)
app.include_router(chat_router)
app.include_router(extraction_router)
app.include_router(review_router)

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


@app.exception_handler(SceneNotFound)
async def _scene_not_found(_: Request, exc: SceneNotFound) -> JSONResponse:
    # 面板点了「写进场景 N」，但这一章没有那个场景号——什么都不发生比报错更糟（作者以为写了）。
    return _err(404, {"error": "scene_not_found", "message": str(exc)})


@app.exception_handler(AmbiguousScene)
async def _ambiguous_scene(_: Request, exc: AmbiguousScene) -> JSONResponse:
    # 同章两个同号 ## 场景 N，写哪个都是猜。
    return _err(409, {"error": "ambiguous_scene", "message": str(exc)})


@app.exception_handler(SceneWriteRefused)
async def _scene_write_refused(_: Request, exc: SceneWriteRefused) -> JSONResponse:
    # MalformedDirective（key 有两份）/ UnwritableValue（值里有表达不了的字符，回读发现）。
    # 系统看不懂作者的文件时的唯一正确动作：原样交还 + 说清哪里看不懂，绝不写坏它。
    return _err(422, {"error": "scene_write_refused", "message": str(exc)})


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


def _settings_response(settings: UserSettings) -> dict[str, Any]:
    """**永不回吐完整 key**——前端只需要「设没设」和「后四位」。
    完整 key 只在本机文件里，不出 HTTP（本地环回也算出口）。"""
    return {
        "base_url": settings.base_url,
        "model": settings.model,
        "api_key_set": settings.api_key_set,
        "api_key_preview": settings.api_key_preview,
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
    )
    save_user_settings(merged)
    return _settings_response(merged)


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


@app.post("/api/projects/bootstrap", response_model=onboarding.BootstrapResult)
def bootstrap_project(
    body: BootstrapBody, conn: Any = Depends(get_conn)
) -> onboarding.BootstrapResult:
    """原子创建新书：项目、首章、快照与导入报告一起成功或一起消失。"""
    return onboarding.bootstrap_project(
        conn,
        books_root=books_root(),
        mode=body.mode,
        name=body.name,
        text=body.text if isinstance(body, ImportBootstrap) else None,
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


@app.post("/api/projects/{project_id}/sync")
def sync_project(store: Any = Depends(get_store), proj: Any = Depends(load_project)) -> Any:
    """把 {root}/chapters/*.md 的现状读进库（作者在别的编辑器改了稿之后走这条）。"""
    return importer.sync(store, proj.id, Path(proj.root_path))


@app.get("/api/projects/{project_id}")
def project_detail(proj: Any = Depends(load_project)) -> Any:
    return proj


@app.get("/api/projects/{project_id}/roster")
def roster(store: Any = Depends(get_store), proj: Any = Depends(load_project)) -> Any:
    """左栏花名册：全项目节点，收窄成 {id,label,name}（不整体序列化 Node.props，防秘密泄漏）。"""
    seen: dict[str, dict[str, str]] = {}
    for resolution in store.resolve(proj.id, None):
        for hit in resolution.hits:
            node = hit.node
            seen[node.id] = {"id": node.id, "label": node.label.value, "name": node.name}
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


@app.get("/api/projects/{project_id}/chapters/{chapter}/matrix")
def matrix(
    chapter: int,
    cast: str = Query("", description="在场称呼原文，逗号/顿号分隔；留空 = 由本章正文推"),
    include: str = INCLUDE,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """★ 认知边界矩阵（头牌）。必须经 resolve_cast，才挂得上 unresolved_cast（store 自己不知道）。

    **`version` 在这里才填得上，图层填不了**：canon 版本住在 `project` 行上，不在图表里
    （`graph/sqlite_store.py` 建这个模型时用的是 `GraphVersion()` 默认值，于是它一直是 0）。
    而这一格现在是可改的（`POST /canon/knowledge` 收 `expected_canon_version`），
    那个数**必须是作者看到这张表那一刻的版本**：从别的读端另取一次就是第二个会漂的源，
    中间要是有人升过 CANON，CAS 会放过一次它本该拦下的改动。
    """
    resolved = resolve_cast(store, proj.id, _effective_cast(proj, store, chapter, cast, include))
    view = knowledge_matrix(store, proj.id, chapter, resolved.ids, unresolved=resolved.unresolved)
    return view.model_copy(update={"version": GraphVersion(canon_version=proj.canon_version)})


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

    出参含完整 Node（node/location/states[].dim）→ 过 `_narrow`：这一章视角下的 Secret
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

    nodes/center 是完整 Node → 过 `_narrow`：这一章的 Secret / 未来节点收窄。
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
def chapter_text(chapter: int, proj: Any = Depends(load_project)) -> Any:
    """读磁盘 md（不是 DB 快照——那是给证据锚用的冻结版，不是可编辑的正文）。"""
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    return {"number": chapter, "markdown": file.read_text(encoding="utf-8-sig")}


@app.put("/api/projects/{project_id}/chapters/{chapter}/text")
def save_chapter(
    chapter: int,
    body: ChapterSave,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """保存：先写磁盘（文件是作者的），再 importer.sync 落快照。磁盘先、DB 跟。

    切出 0 或 >1 章时 sync 抛 `SyncRefused` → 全局 handler 映成 422 带 path（正文已写进
    磁盘，作者需修好章标题再存一次）。

    **不带乐观闸**（`expected_sha256=None`）：作者改的就是他眼前那份，中间没有第三方。
    agent 起草落盘走的是**同一个** `importer.save_chapter()`，只是必须给出它依据的那份
    哈希（ADR 0021）——两条路一个实现，闸是不是开着由调用方说，不由第二份保存逻辑说。
    """
    try:
        return importer.save_chapter(
            store, proj.id, Path(proj.root_path), chapter, body.markdown
        )
    except importer.ChapterMissing:
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})


# ── 场景块：## 场景 N + <!-- nh: cast=… loc=… goal=… -->（面板的「在场是谁」的来源）──


class SceneWrite(BaseModel):
    """回写一个场景块的 cast/loc/goal。**三个都发**：这一场的期望全态，不做 KEEP 增量——
    前端场景条是「读出当前值 → 改 → 存全部」，所以每次写的是完整意图。空 cast / 空 loc /
    空 goal = 删掉那个 key（write_scene_directive 的 None 语义）。
    """

    number: int
    cast: list[str] = []
    loc: str | None = None
    goal: str | None = None


@app.get("/api/projects/{project_id}/chapters/{chapter}/scenes")
def scenes(chapter: int, proj: Any = Depends(load_project)) -> Any:
    """这一章磁盘正文里的场景块（cast/loc/goal 全是称呼原文，不解析）。切段走 text.paragraphs()。"""
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    paras = split_paragraphs(file.read_text(encoding="utf-8-sig"))
    return [s.model_dump(mode="json") for s in parse_scenes(paras)]


@app.put("/api/projects/{project_id}/chapters/{chapter}/scenes")
def write_scene(
    chapter: int,
    body: SceneWrite,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """把 cast/loc/goal 无损写进第 chapter 章的场景 N，再 sync。**只动它拥有的那几个字符**
    （缩进/行尾/未知 key 逐字节保留，ADR 0007：正文是作者的文件）。写完回读验尸——
    值里有表达不了的字符 → UnwritableValue → 422，不写坏文件。

    返回回写后重新解析的场景列表，让前端拿到「系统看到的」而不是「它以为写进去的」。
    """
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    text = file.read_text(encoding="utf-8-sig")
    # 空 = 删该 key（None 语义）；非空原样写。三个都传 = 这一场的完整意图，不用 KEEP 哨兵。
    new_text = write_scene_directive(
        text,
        body.number,
        cast=body.cast,
        loc=body.loc or None,
        goal=body.goal or None,
    )
    file.write_text(new_text, encoding="utf-8")
    importer.sync(store, proj.id, Path(proj.root_path))
    paras = split_paragraphs(new_text)
    return [s.model_dump(mode="json") for s in parse_scenes(paras)]


# ══════════════════════════════════════════════════════════════════════════
# 写图谱：declare（P2 核心闭环）—— 作者敲称呼原文 + 引语，系统算章号
#
# 入参**没有一个章号字段**（§5.9 / 约束 10）：`valid_from` 只由引语落在哪一章决定，
# `Ledger` 的签名里没有位置能让作者填它。`who`/`secret`/`loc`/`of` 全是称呼原文，
# 本文件一次都不解析——解析在 `Ledger` 里，于是壳没有机会把歧义的「师兄」偷偷挑成
# 第一个候选（歧义 → AmbiguousName → 409 + candidates，让前端弹消歧下拉）。
# ══════════════════════════════════════════════════════════════════════════


class LocateBody(BaseModel):
    quote: str


class DeclareNodeBody(BaseModel):
    label: NodeLabel
    name: str
    aliases: list[str] = []
    description: str = ""
    """仅 label=Secret：秘密的内容，进 secret 扩展表（不进 node.props）。"""
    sub_of: str | None = None
    """仅 label=Secret：父秘密的**称呼原文**（拆子事实用）。"""


class DeclareAliasBody(BaseModel):
    of: str
    surface: str
    kind: AliasKind = AliasKind.ALIAS
    usable_for_rules: bool = True


class DeclareKnowsBody(BaseModel):
    who: str
    secret: str
    quote: str


class DeclareBelievesBody(BaseModel):
    who: str
    secret: str
    believed_value: str
    quote: str


class DeclareWhereBody(BaseModel):
    who: str
    loc: str
    quote: str


@app.post("/api/projects/{project_id}/locate")
def locate(
    body: LocateBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """这句引语在当前正文里的全部命中（只读预览，declare 前先试唯一性）。

    QuoteCandidate 不含 Node，无需收窄。0 命中不报错——它是合法答案（「这句还不可用」），
    由前端渲染成「加长引语 / 先 sync」。
    """
    return [c.model_dump(mode="json") for c in ledger.locate(body.quote)]


@app.post("/api/projects/{project_id}/nodes")
def declare_node(
    body: DeclareNodeBody,
    store: Any = Depends(get_store),
    ledger: Ledger = Depends(get_ledger),
    proj: Any = Depends(load_project),
) -> Any:
    """声明一个节点（8 类 label）。幂等（键 = name）。

    label=Secret 时把 `sub_of` 称呼解析成父秘密 node_id（复刻 cli 那处已知破例：`Ledger`
    今天没有收「父秘密称呼」的入口）——解析不出/歧义/不是 Secret，走同一套 declare 拒绝
    异常（→ 404/409/422）。返回的 Node 过 `_narrow`：Secret 收窄成 {id,label,name}。
    """
    secret: SecretDetail | None = None
    if body.label is NodeLabel.SECRET:
        parent_id = _resolve_parent_secret(store, proj.id, body.sub_of) if body.sub_of else None
        secret = SecretDetail(description=body.description, sub_of=parent_id)
    node = ledger.declare_node(body.label, body.name, aliases=body.aliases, secret=secret)
    return _narrowed(node, None)


def _resolve_parent_secret(store: Any, project_id: str, surface: str) -> str:
    """`sub_of` 称呼 → 父秘密 node_id。逐字复刻 cli._resolve_parent_secret 的拒绝形态：
    绝不挑第一个候选（歧义 → AmbiguousName，查无 → UnknownName，非 Secret → WrongLabel）。
    """
    resolution = store.resolve(project_id, [surface])[0]
    node = resolution.unique_node
    if node is None:
        if not resolution.hits:
            raise UnknownName(surface)
        raise AmbiguousName(surface, [NodeRef.of(hit.node) for hit in resolution.hits])
    if node.label is not NodeLabel.SECRET:
        raise WrongLabel(surface, node.label, NodeLabel.SECRET)
    return node.id


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


@app.post("/api/projects/{project_id}/declare/knows")
def declare_knows(
    body: DeclareKnowsBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """「他在这段原文里知道了这个秘密」。章号由引语算，入参里没有它。

    Declaration 收据带 valid_from（系统算的）+ 被自动闭合/撤回的旧边。不含 Node，无需收窄。
    """
    return ledger.declare_knows(who=body.who, secret=body.secret, quote=body.quote).model_dump(
        mode="json"
    )


@app.post("/api/projects/{project_id}/declare/believes")
def declare_believes(
    body: DeclareBelievesBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """「他以为的是另一个版本」——错误认知。believed_value 进 edge.props，面板直接渲染。"""
    return ledger.declare_believes(
        who=body.who,
        secret=body.secret,
        believed_value=body.believed_value,
        quote=body.quote,
    ).model_dump(mode="json")


@app.post("/api/projects/{project_id}/declare/where")
def declare_where(
    body: DeclareWhereBody,
    ledger: Ledger = Depends(get_ledger),
) -> Any:
    """「他在这段原文里到了这个地方」。LOCATED_AT 是 single_per_src——这条会自动闭合
    他上一个位置（Declaration.closed，产品招牌动作）。闭到哪一章同样是算出来的。
    """
    return ledger.declare_where(who=body.who, loc=body.loc, quote=body.quote).model_dump(
        mode="json"
    )


@app.post("/api/projects/{project_id}/chapters/{chapter}/check")
def check(
    chapter: int,
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """对第 chapter 章的磁盘正文跑一致性规则（M3 规则集 = R2 / R3 / R4，R5 已按 ADR 0014 砍掉）。

    切段和 parse_scenes 都走 `text.paragraphs()`（全库唯一定义，§1.4）——路由里**不许**
    自己写 `.splitlines()`，否则 anchor 一改，Issue 锚就和别处「差一段」。

    **不返裸 list[Issue]**：返 `{scene_count, rules_run, issues}`。静默的零和真的零不许
    长得一样（§10 约束 8 / cli.check 的原话）——0 个场景块 = R4 无事可做 = 必然零 issue，
    那个零不是「这章没问题」。前端据 scene_count / rules_run 把它和「跑了但没意见」分开。
    """
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    paras = split_paragraphs(file.read_text(encoding="utf-8-sig"))
    scenes = parse_scenes(paras)
    ctx = CheckContext(
        store=store,
        project_id=proj.id,
        chapter=chapter,
        scenes=tuple(scenes),
        paragraphs=paras,
    )
    issues = run_checks(ctx)
    return {
        "chapter": chapter,
        "scene_count": len(scenes),
        "rules_run": [c.__module__.rsplit(".", 1)[-1] for c in ALL_CHECKS],
        "issues": [i.model_dump(mode="json") for i in issues],
    }


# ══════════════════════════════════════════════════════════════════════════
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
    """AI 起草请求体（修正案 7，实验状态）。长度按 ADR 0013 随请求走。

    **两种形状，由 `mode` 区分**（ADR 0015）。它们的差别不是「参数可不可选」，
    而是「哪些东西允许由作者控制」——所以用校验器把两种形状分别钉死，而不是把
    `goal` / `cast` 一起放宽成可选，那样两种模式的约束就都没人守了。
    """

    mode: Literal["chapter", "continuation"] = "chapter"
    """`chapter` = 起草一整章（原行为）；`continuation` = 行内续写一两段（ADR 0015）。"""

    goal: str = ""
    """本场目标。**`chapter` 必填；`continuation` 必须为空**——续写的 `goal` 是后端常量
    （ADR 0015 D3：前端能传的东西作者就能改，而这是 ADR 0010 点名的泄漏入口之一）。"""

    cast: list[str] = []
    """在场称呼原文。**`chapter` 必填；`continuation` 可空**。

    续写留空 = 「不知道谁在场」→ 走全禁退化态（ADR 0015 D4）。填了则收紧到精确约束——
    **它是让续写写得更准的奖励，不是不填就不给用的门槛。**"""

    length: _DraftLengthBody
    form: str = "PRODUCT"
    previous_tail: str = ""
    house_style: str = ""

    @model_validator(mode="after")
    def _check_mode_shape(self) -> DraftRequest:
        if self.mode == "continuation":
            if self.goal.strip():
                raise ValueError(
                    "续写模式不接受 goal：这一段要写什么由上文决定，"
                    "提示语是后端常量（ADR 0015 D3）"
                )
            return self
        if not self.goal.strip():
            raise ValueError("起草一整章必须说清这一场要写什么（goal）")
        if not self.cast:
            raise ValueError("起草一整章必须声明在场角色（cast）")
        return self


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


@app.post("/api/projects/{project_id}/chapters/{chapter}/draft")
def draft(
    body: DraftRequest,
    chapter: int,
    project_id: str,
    store: Any = Depends(get_store),
    conn: Any = Depends(get_conn),
    proj: Any = Depends(load_project),
) -> dict[str, Any]:
    """AI 起草第 N 章（**实验状态**，修正案 7）。

    放行 ≠ 验证：响应带 ``experimental`` 标注，kill-gate 裁决前不声称图谱约束有效。
    若将来裁决 KILL，撤销本路由 = 一次显式 commit（修正案 7 原文）。

    ── 这条路由不再自己拼 prompt（2026-08-11）───────────────────────────────

    「章号 → 一稿正文」的实现搬去了 `draft/product_draft.py::draft_chapter()`，
    **因为 agent 的起草工具要调同一个函数**（3.6 / ADR 0021）。留在这儿的只有壳该干的
    三件事：BYOK 连接参数、算这一场的约束、把领域异常翻成给作者的话。

    **这条路由不落盘。** 行内续写（ADR 0015）按定义就不该落盘，而整章起草从浏览器
    发起时作者眼前就是编辑器——落盘是 agent 那条路的事（ADR 0021 只推翻了「agent 写正文
    要先问」，没有给这条路由加一个作者没按过的保存）。
    """
    from ..draft.capabilities import (
        CapabilityError,
        ReasoningEffort,
        plan_call,
        resolve_capabilities,
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

    try:
        config = _draft_provider_config()
        capability = resolve_capabilities(config.base_url, config.model)
        plan = plan_call(body.length, ReasoningEffort.HIGH, capability)
    except (ValidationError, ValueError, CapabilityError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"模型没配好：{exc} —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，"
                "或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。"
            ),
        )

    # **plan 提前到装配之前**：记忆层的预算要从 `capability.max_context_tokens` 倒推
    # （`memory_units_available`），所以得先知道模型是谁。它不依赖 messages，提前无副作用；
    # 而且「模型没配好」这种错在这儿就报出来，比装配完一大堆上下文再报便宜。
    request = ChapterDraftRequest(
        goal=body.goal,
        length=body.length,
        mode=body.mode,
        form=body.form,
        previous_tail=body.previous_tail,
        house_style=body.house_style,
    )
    try:
        # **在算约束之前先验一次 form / 文风。** `draft_chapter()` 自己也会验（agent 那条路
        # 没有这一步），这里多调一次是为了保住 422 的先后顺序：form 写错和在场角色解析不了
        # 同时发生时，作者收到的仍然是 form 那一句。重复的是执行，不是实现。
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
# **故意不做「保存章节后自动生成」**：那是一次作者没按过的付费调用，属于产品决策，
# 不该由一次保存顺手替他决定。只做显式触发。


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


@app.post("/api/projects/{project_id}/chapters/{chapter}/summary")
def generate_chapter_summary(
    chapter: int,
    proj: Any = Depends(load_project),
    summarizer: Any = Depends(get_summarizer),
) -> dict[str, Any]:
    """**显式**为第 chapter 章生成滚动总结（会调模型、会花钱）。

    幂等由 `RollingSummarizer.ensure` 保证：同一章 + 同一 `schema_version` + 同一
    `prompt_hash` 已经有了就直接返回，重复点不会重复付费。所以前端可以放心地
    「把缺的那几章挨个补一遍」而不必自己记住哪些补过。
    """
    from ..draft.provider import ProviderError
    from ..draft.rolling_summary import SummaryChapterNotFound, SummaryGenerationError

    if chapter < 1:
        raise HTTPException(status_code=422, detail="章号至少是 1")
    try:
        summary = summarizer.ensure(proj.id, chapter)
    except SummaryChapterNotFound:
        raise HTTPException(
            status_code=404,
            detail={"error": "chapter_not_found", "chapter": chapter},
        )
    except SummaryGenerationError as exc:
        raise HTTPException(status_code=502, detail=f"总结器返回了空文本：{exc}")
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}")
    return {
        "chapter_number": summary.chapter_number,
        "has_text": True,
        "summary": summary.summary,
        "created_at": summary.created_at,
    }


@app.post("/api/projects/{project_id}/chapters/{chapter}/plan", status_code=501)
def plan_stub() -> dict[str, str]:
    """AI 规划第 N 章的场景骨架（M2）。v1 的替代是作者手拖手填场景块（§2 表）。"""
    return _stub("M2")


# `GET /runs` 曾经也是这里的一条 501，理由写着「`model_call` 表今天是空的」。
# **那句话在 M4 落地那天就过期了**（抽取和滚动总结都在记账，见 `extract/call_audit.py`），
# 而一条声称「这个能力还没有」的 501 挡在一张真有数据的表前面，比空列表更能骗人。
# 2026-08-10 起它是一条真路由，搬去了 `api/activity.py`（和日志读端同一摊）。
