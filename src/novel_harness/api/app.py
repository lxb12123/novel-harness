"""路由 —— 只把引擎函数包成 HTTP，不装业务。不碰连接（收 Depends(get_store)）。

只读面板：项目 / 花名册 / 认知矩阵（头牌）/ 场景约束 / 当前状态 / 局部子图。
编辑器（P1）：列章 / 读章正文 / 存盘 → sync（正文在磁盘，ADR 0007）。
写图谱（declare）+ 定位 + R4 check（P2）：作者敲称呼原文 + 引语，系统算章号。
起草/抽取是 M2/M4——本文件末尾 5 条 stub 稳定返 501（见 docs/UI_ARCHITECTURE.md §1.2）。

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
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .. import importer
from .. import project as project_mod
from ..checks import ALL_CHECKS, CheckContext, run_checks
from ..declare import (
    AmbiguousName,
    AmbiguousQuote,
    DeclarationRefused,
    Ledger,
    QuoteNotFound,
    UnknownName,
    WrongLabel,
)
from ..graph import AliasKind, EdgeType, InformationScope, NodeLabel, NodeRef, SecretDetail
from ..graph.store import NodeNotFound, StoreError, SupersedeConflict
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
from .deps import books_root, ensure_schema, get_conn, get_ledger, get_store, load_project

_STATIC = Path(__file__).resolve().parent / "static"
_UNSAFE_PATH = re.compile(r'[/\\:*?"<>|]')  # 书名里不能进目录名的字符
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
_CHAPTER_FILE = re.compile(r"^(\d{4,})\.md$")


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


@asynccontextmanager
async def _lifespan(_: FastAPI) -> Any:
    ensure_schema()  # 启动即验库在、schema 到位；库不存在直接炸，不建空库
    yield


app = FastAPI(title="Novel Harness 工作台", lifespan=_lifespan)

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


# ── 上手：建书 / 导入 TXT / 同步（让非程序员不碰命令行也能起步）──────────────


class CreateProject(BaseModel):
    name: str


class ImportText(BaseModel):
    text: str
    """整本 TXT 的正文。**由浏览器读文件解码后作为文本发来**（避开 python-multipart，
    也把编码难题交给浏览器：GBK 的老稿子前端用 TextDecoder 兜）。"""


def _new_book_root(name: str) -> Path:
    """给新书在 books_root 下开一个稿子目录。作者从不敲路径——按书名派生，撞了就加序号。"""
    base = books_root()
    slug = _UNSAFE_PATH.sub("", name).strip() or "book"
    root = base / slug
    n = 2
    while root.exists() and any(root.iterdir()):
        root = base / f"{slug}-{n}"
        n += 1
    root.mkdir(parents=True, exist_ok=True)
    return root


@app.post("/api/projects")
def create_project(body: CreateProject, conn: Any = Depends(get_conn)) -> Any:
    """新建一本书。root_path 由服务器在 books_root 下派生（§ADR 0007：那目录就是稿子）。

    空名 → project.create 抛 ValueError → 422。project.create 自己 commit。
    """
    root = _new_book_root(body.name)
    return project_mod.create(conn, name=body.name, root_path=str(root))


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


@app.get("/api/projects/{project_id}/chapters/{chapter}/matrix")
def matrix(
    chapter: int,
    cast: str = Query("", description="在场称呼原文，逗号/顿号分隔"),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """★ 认知边界矩阵（头牌）。必须经 resolve_cast，才挂得上 unresolved_cast（store 自己不知道）。"""
    resolved = resolve_cast(store, proj.id, _split_cast(cast))
    return knowledge_matrix(store, proj.id, chapter, resolved.ids, unresolved=resolved.unresolved)


@app.get("/api/projects/{project_id}/chapters/{chapter}/constraints")
def constraints(
    chapter: int,
    cast: str = Query(""),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """场景约束盒：scene_constraints 收原始称呼、内部自解析、fail-closed。"""
    return scene_constraints(store, proj.id, chapter, _split_cast(cast))


@app.get("/api/projects/{project_id}/chapters/{chapter}/state")
def state(
    chapter: int,
    cast: str = Query(""),
    store: Any = Depends(get_store),
    proj: Any = Depends(load_project),
) -> Any:
    """当前状态卡：cast_states 收已解析 node_id，所以先 resolve_cast。

    出参含完整 Node（node/location/states[].dim）→ 过 `_narrow`：这一章视角下的 Secret
    和未来节点收窄。unresolved 由前端另走 `/matrix` 的 `unresolved_cast` 拿。
    """
    resolved = resolve_cast(store, proj.id, _split_cast(cast))
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
    """列章：直接扫磁盘 {root}/chapters/NNNN.md（磁盘是正文真相源）。title = 首个非空行。"""
    out: list[dict[str, Any]] = []
    cdir = _chapters_dir(proj)
    if cdir.is_dir():
        for file in sorted(cdir.glob("*.md")):
            m = _CHAPTER_FILE.match(file.name)
            if not m:
                continue
            title = ""
            for line in file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    title = line.strip()
                    break
            out.append({"number": int(m.group(1)), "title": title})
    return out


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
    """
    file = _chapter_file(proj, chapter)
    if not file.exists():
        raise HTTPException(404, {"error": "chapter_not_found", "chapter": chapter})
    file.write_text(body.markdown, encoding="utf-8")
    return importer.sync(store, proj.id, Path(proj.root_path))


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
    """对第 chapter 章的磁盘正文跑一致性规则（v1 仅 R4 LOCATION_CONFLICT）。

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
# M2 / M4 的 stub —— 稳定的 501，**不是 404**（UI_ARCHITECTURE §1.2 第 48 行）
#
# 这 5 条（3 条 M2 起草 + 2 条 M4 抽取）背后的引擎一个字都还没写。它们今天存在的
# 理由只有一个：**让前端把按钮画成灰的，而不是把按钮藏起来。**
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


def _stub(milestone: str) -> dict[str, str]:
    """501 的响应体。形状由 UI_ARCHITECTURE §1.2 定死：`{status, milestone}`。

    `status_code=501` 写在装饰器上（而不是这里返 JSONResponse），是为了让它进 openapi
    ——前端从 schema 就能看见这条路由今天只会 501，不必等运行时撞上。
    """
    return {"status": _NOT_IMPLEMENTED, "milestone": milestone}


@app.post("/api/projects/{project_id}/chapters/{chapter}/draft", status_code=501)
def draft_stub() -> dict[str, str]:
    """AI 起草第 N 章（M2）。

    **这条 stub 是 kill-gate 的 KILL 分支能被执行的前提**（EVAL_PROTOCOL 修正案 1 的
    「修正 2」）：冻结的裁决表里，KILL 那一格的动作逐字写着「`/draft` 冻在 501」——
    而在这条路由存在之前，那个动作指向一个不存在的端点，即**裁决表里有一格是不可执行的**。
    补上它，KILL 那天的动作就是逐字可做的：把它留在 501、不往前走。

    修正案同时说清了为什么不把那句话改成「不建路由」：一条摆在那儿的 501 是对作者和
    后来者的公开承诺，撤销它需要一次显式的 commit；而「没建」是默认状态，任何人任何
    时候悄悄加回来都不会有人注意到。KILL 是那份协议里唯一「杀掉一条产品线」的动作，
    它需要的正是那种撤销起来有声音的形式。
    """
    return _stub("M2")


@app.post("/api/projects/{project_id}/chapters/{chapter}/plan", status_code=501)
def plan_stub() -> dict[str, str]:
    """AI 规划第 N 章的场景骨架（M2）。v1 的替代是作者手拖手填场景块（§2 表）。"""
    return _stub("M2")


@app.get("/api/projects/{project_id}/runs", status_code=501)
def runs_stub() -> dict[str, str]:
    """底栏「最近运行 / Token / 成本」（M2）。`model_call` 表今天是空的——

    返空列表比 501 更糟：一张空表长得像「你还没跑过」，而事实是「这个能力还没有」。
    这正是 §10 约束 8 说的那种失败形态（漂亮的空结果 + 200）。
    """
    return _stub("M2")


@app.get("/api/projects/{project_id}/chapters/{chapter}/proposals", status_code=501)
def proposals_stub() -> dict[str, str]:
    """变更确认页的提案列表（M4）。`extract/` 未建、`proposal_set` 表空。

    同上：空列表会被读成「这一章没有待确认的变更」，而真相是没有任何东西在生产提案。
    v1 用「作者手动 declare」替代整套「系统抽 → 作者审」的心智。
    """
    return _stub("M4")


@app.post("/api/projects/{project_id}/proposals/{proposal_id}/accept", status_code=501)
def accept_proposal_stub() -> dict[str, str]:
    """接受一条提案 → 落成边（M4）。`upsert_edge` 早就就绪，缺的是提案的生产者。"""
    return _stub("M4")
