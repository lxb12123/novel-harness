"""活动日志读端 —— ADR 0020 的「可查」那一半。

2026-08-10 之后系统会自动往作者的书里写东西（干净抽取结果直接升 CANON，
`actor='system'`）。那次裁决把「事前批准」换成了「事后可查 + 可改」，
**改**已经落在 `corrections.py`，本模块补上**查**。

三张控制面表，三个不同的问题，合成一条时间线：

| 表 | 回答什么 | 谁在写 |
|---|---|---|
| `extraction_run` | 跑了什么 | 后台抽取（`extract/runner.py`） |
| `model_call` | 花了多少 | 抽取 + 滚动总结（`extract/call_audit.py`） |
| `decision_log` | 改了什么、**谁改的** | 作者亲手点的 + 自动升 CANON |

── 出参为什么分两层 ───────────────────────────────────────────────────────

折叠行（`ActivityEntry`）只回答四个问题：**什么时候、什么东西、成没成、谁干的**。
展开详情（`ActivityDetail`）才带 token、审计信封和错误。

分层的真实理由不是省流量，是**泄漏面**：`decision_log.payload_json` 是一个开放的
JSON 信封，把它塞进每一行 = 作者一打开日志页就把全库的审计内容拉进了浏览器。
详情按需取，且一律过 `narrow_payload()`。

── `jump` 是坐标，不是标题的反推 ─────────────────────────────────────────

每条都带一个结构化的 `jump`：**「跳去哪个模块改」由后端算**，前端不许从标题里
猜。而且它只允许落在**今天真的存在**的编辑入口上（`/canon/events/{id}/cast` /
三条提案审阅路由）——指向一个不存在的目标，作者点下去
什么都不会发生，那比没有按钮更糟。`ActivityJump.endpoints` 由 HTTP 壳填（路由表是
壳的知识，见 `api/activity.py`），`tests/test_activity.py` 逐条验它们真能解析到
一条已注册的路由。

**今天有一类事实跳不过去，且这不是遗漏**：自动升上去的**边**（`LOCATED_AT` /
`HAS_STATE` / `RELATED_TO`）没有任何编辑入口——`corrections.py` 今天只改事件名单。
所以那种 `jump` 只给章号、`endpoints` 为空。ADR 0020 把这种形态写成了推翻自己的触发条件之一，日志页把它显式
显示出来，正是让那个条件第一次可观测。

── 本模块不读图 ──────────────────────────────────────────────────────────

这里的 SQL 只碰 `extraction_run` / `model_call` / `proposal_set`——三张都不是图表，
碰它们不产生第二份时态过滤（`tests/test_arch_guard.py` 的 `GRAPH_TABLES` 里故意没有
它们）。`decision_log` 一行 SQL 都不在这里：它的读入口只在 `decisions.py`。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from . import decisions
from .db import Connection
from .extract.control import ExtractionErrorCode, ExtractionRunStatus

__all__ = [
    "ActivityCost",
    "ActivityDetail",
    "ActivityEntry",
    "ActivityJump",
    "ActivityPage",
    "ActivitySource",
    "ActivityStatus",
    "ActorTally",
    "CostTotals",
    "DetailRow",
    "JumpTarget",
    "RunsPanel",
    "narrow_payload",
    "read_activity",
    "read_entry",
    "read_runs",
    "run_error_label",
]


class ActivitySource(StrEnum):
    """这一行是从哪张表来的。前端按它决定展开面板长什么样。"""

    EXTRACTION = "extraction"
    MODEL_CALL = "model_call"
    DECISION = "decision"


class ActivityStatus(StrEnum):
    """成没成。

    `model_call` / `decision_log` 只在事情**已经发生**之后才落行，所以它们恒为
    `succeeded`；只有 `extraction_run` 有真正的四态。**不要把它读成「这条对不对」**
    ——一条成功写下的错事实，status 也是 `succeeded`，对不对要作者自己看。
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RUNNING = "running"
    PENDING = "pending"


class JumpTarget(StrEnum):
    """跳去哪一类目标。

    除最后一档外，每一档都对应一个**今天真的存在**的入口（四个能改、一个能重来）；
    `CHAPTER` 是兜底——它明说「只能定位到这一章，没有更细的编辑目标」，
    而不是假装有一个。

    ── 兜底那一档只装「今天真的没有更细目标」的东西 ──────────────────────────
    2026-08-13 从里面搬走了两类，**它们当初落进兜底都不是因为没有目标**：

    - `SUMMARY`：右栏「章节总结」那一格（读 / 改 / 撤回 / 重新生成）当天才长出来，
      而写总结那次模型调用照旧退到「去第 N 章」——目标后来才有，这张表没跟着改。
    - `EXTRACTION_RETRY`：重跑一直在后端（`POST …/chapters/{n}/extract`），
      只是日志上没有那颗按钮。

    留在兜底里，那一档就从一句诚实话（「今天真的改不了」，ADR 0020 拿它当自己的
    推翻条件的观测点）变成一句骗人的话，而**骗人的那一句会把观测点一起关掉**。
    """

    EVENT_CAST = "event_cast"
    PROPOSAL = "proposal"
    SUMMARY = "summary"
    EXTRACTION_RETRY = "extraction_retry"
    """**它不是一个编辑入口，是一个「再来一次」。**

    别的几档跳过去之后作者动手改一样东西；这一档是一次没跑成的整理，
    作者要的是让它重跑（那条路由把那一行原地重置回排队，不新建行）。
    共用 `endpoints` 是因为它回答的是同一个问题：**今天有没有一条路能让这件事不一样。**
    """

    CANON_EDGE = "canon_edge"
    """自动升上去的地点 / 状态 / 关系边（Task 8 / ADR 0032）。

    跳过去打开 `CanonEdgeEditor`：按稳定 `edge_id` 修改、撤回或改归属，
    不再退到「去第 N 章」的兜底坐标。"""

    CHAPTER = "chapter"


class ActivityJump(BaseModel):
    """一条日志行的跳转坐标。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: JumpTarget
    label: str
    """按钮上的那句话。**措辞归后端**——它和 `target` 是同一次判断的两个产物，
    分开写迟早会出现「按钮说去改提案、坐标指向认知矩阵」。"""

    chapter_number: int | None = None
    event_id: str | None = None
    proposal_id: str | None = None
    edge_id: str | None = None
    """`CANON_EDGE` 那一档的稳定边 id（改归属/撤回也是它）。"""

    endpoints: tuple[str, ...] = ()
    """改这个目标要打的路由。**引擎不填，由 HTTP 壳补**（路由表是壳的知识）。

    空元组不是「还没填」而是一个断言：**今天没有任何路由能改这个东西**，前端不许
    画编辑按钮。现在三类自动边都有 `CANON_EDGE` 入口，落到这一档的只剩正在
    补纠错入口的新边类型。
    """

class ActivityEntry(BaseModel):
    """折叠行：作者扫一眼就该知道的全部。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """三张表各自的主键。前缀（`extraction_run:` / `call:` / `decision:`）就是 `source`，
    详情端点靠它分派。"""

    source: ActivitySource
    ts: str
    """ISO 8601 / UTC / 毫秒 / 'Z'。三张表用的是同一个 SQL DEFAULT，所以字典序 = 时序，
    跨表归并才成立（`decisions.Decision.ts` 讲了为什么不许 Python 侧生成它）。"""

    actor: str
    """`author` / `system`（开放字符串，同 `decision_log.actor`）。
    `extraction_run` / `model_call` 没有这一列——它们按定义是系统跑的，这里补成 `system`。"""

    status: ActivityStatus
    title: str
    subtitle: str
    """一行结果摘要。**零必须带着理由**（§10 约束 8）：token 没记就写「未记录」，
    不许渲染成 0。"""

    chapter_number: int | None = None
    jump: ActivityJump | None = None


class DetailRow(BaseModel):
    """展开详情里的一行「标签 → 值」。

    做成通用键值对而不是每种 source 一个响应模型，是为了让措辞全部留在后端：
    前端渲染一张定义列表，不写一行 `if source == …` 的文案分支。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    value: str


class ActivityCost(BaseModel):
    """这一步花了多少。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    capability: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    ms: int | None = None
    cost: float | None = None
    """这一次花了多少**美元**。`None` = 算不出来（**不是 0**）。

    ⚠️ **它是标价估算，不是账单。** 单价来自一份公开表（`draft/windows.py`），
    而作者可能有折扣、走中转、用免费额度。**屏幕上必须带「约」字**
    （`frontend` 那侧有测试钉着）。供应商自己报了花费时（OpenRouter 的 usage 里有）
    走的是真数——但今天两者在屏幕上一视同仁地写「约」，那是**少说**，不是骗人。

    🔴 **2026-08-13 之前这儿写着「今天恒为 None，因为 BYOK 之下引擎不知道作者签的是
    什么价」。** 那句话半对半错：签的什么价确实不知道，但**标价是公开的**，
    而「约 ¥0.01」比「未记录」有用得多。现在 `record_call` 写这一列了。

    前端仍然必须把 `None` 渲染成「未记录」而不是「¥0.00」——一张写着 0 元的账单
    是本仓库反复在修的那种失败形态（漂亮的空结果 + 200）。
    """


class ActivityDetail(BaseModel):
    """展开详情：跑了什么、结果是什么、花了多少。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: ActivityEntry
    """折叠行原样带回来，**详情响应自足**：前端不必把列表里那一行的字段拼进来
    （拼的那一刻两份就能不一致）。"""

    rows: tuple[DetailRow, ...] = ()
    cost: ActivityCost | None = None
    errors: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None
    """`decision_log` 那一行的审计信封（**已过 `narrow_payload`**）。
    只有 `source=decision` 才有；别的两个 source 恒为 None。"""


class ActorTally(BaseModel):
    """某个 actor 在这条时间线上一共有多少行。

    ADR 0020 点名的那件事：自动升开了之后 system 行会长得快得多，
    **作者自己点过的那 30 次确认会被淹没在几千条 system 行里**。
    所以这个计数是**不受当前过滤影响的全量值**——它存在的意义就是让作者看见
    「被藏起来的有多少」，跟着过滤一起变就什么都说明不了了。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str
    count: int = Field(ge=0)


class ActivityPage(BaseModel):
    """一页折叠行。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[ActivityEntry, ...] = ()
    next_cursor: str | None = None
    """下一页的游标（不透明字符串，原样回传给 `?cursor=`）。
    None = 到底了。**不许前端自己拼**——它的内部形状是 `ts|id`，改了就是改分页语义。"""

    actors: tuple[ActorTally, ...] = ()


class CostTotals(BaseModel):
    """整本书到今天为止的模型账。

    **这张表上每一个可空的合计都配一个计数**（`tokens_in/out` ↔ `metered_calls`、
    `cost` ↔ `priced_calls`）。一个合计不说清「它是不是全部」就是一句看起来确定的假话，
    而这张汇总同时端着两种假话的可能：**把不知道说成 0**，和**把知道的说成不知道**。
    两个数一起才躲得开这两边。

    ── 这儿**仍然没有**缓存命中的合计，理由从三条剩下两条 ──────────────────

    原来的第 ① 条（「这张汇总本身正躺在一条已知病里」——`tokens_in/out/ms` 是
    `COALESCE(…, 0)` 的非空 int，于是不报 usage 的书底栏写着「读入 0 token」，
    而同一条用量条上的 `cost` 早就渲染成「未记录」）**2026-08-12 修掉了**，
    修法就是上面那对计数。剩下两条今天还成立，而且头一条被这次改动**加强**了：

    1. **分子和分母各缺一批行，而且不是同一批。** `tokens_in` 今天只覆盖
       `metered_calls` 那几次；缓存那两列的 NULL 又是另一个子集（一条路由完全可以报了
       usage 却不报缓存量——那几家各自报什么只有 `draft/provider.py` 认得）。两个覆盖面
       不同的数相除，得到的不是全书命中率，是一个谁也解读不了的比值——要出这个数就得
       再配第三个计数，那时作者要在同一条上读三次「这是不是全部」。
    2. **这一刀要回答的三个问题，两个只有逐次才看得见。**「忽高忽低 ⇒ 前缀被弄脏了」
       按定义是**逐次之间**的方差；一个全书标量恰好把它平掉。所以测量点放在展开详情
       那一行（`_cache_text`），不放这儿。

    **加它的先决条件因此不再是「等 ① 被修好」**（已经修好了），
    而是「有人真的要读一个全书标量，且说得清它缺了哪几行」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: int = Field(default=0, ge=0)

    metered_calls: int = Field(default=0, ge=0)
    """其中**供应商报了 token 用量的**有几条（两个数至少给了一个）。
    `calls - metered_calls` 就是一个数都没给的那几次。

    它和 `priced_calls` 是同一对形状（计数 + 可空的合计），**照那一对长的，不是第二种**。
    """

    tokens_in: int | None = None
    tokens_out: int | None = None
    """**只是报了的那几次的合计**；一次都没报时是 None，不是 0。

    ── 为什么不能 `COALESCE(…, 0)` ────────────────────────────────────────
    供应商不报 usage 时 `model_call.tokens_in` 就是 NULL（`record_call` 照抄，
    绝不估算）。2026-08-12 起草改成可中断之后这一档从边角变成了常态：流式下只有
    `supports_stream_usage is True` 的路由才会被要 usage，而注册表 8 条里只有 OpenAI
    那 4 条是 True——**README 教作者填的 DeepSeek 两条都不是**。折成 0 的后果是
    作者花着真钱、屏幕上写着「读入 0 token」。

    ── 为什么也不能整个改成「不知道」 ──────────────────────────────────
    90 次报了、10 次没报时，那 90 次的合计**是真信息**，丢掉它是同一种假话的另一个方向。
    """

    ms: int | None = None
    """记下了耗时的那几次之和；一条都没记 → None。

    **它没有自己的计数，这不是漏了**：耗时是我们自己掐的表
    （`record_call(elapsed_ms: int)` 非空，而那是全库唯一的写入口），不是供应商报的——
    真书里每一行都有，「只记了一部分」这一档只可能来自手写行。哪天真长出一个不掐表的
    写入方，这儿要跟着补一个计数，**别改回 `COALESCE`**。
    """

    priced_calls: int = Field(default=0, ge=0)
    """其中**填了 `cost` 的**有几条。

    **2026-08-13 起不再恒为 0**（那一列有写入方了）。但它照旧可能小于总数：
    自建端点、公共表里没有的模型、供应商没报 token 数的那几次，都算不出钱。
    合计和「其中几条算得出」必须一起摆，否则那个合计是一句看起来确定的假话。"""

    cost: float | None = None
    """`priced_calls == 0` 时是 None 而不是 0.0（§10 约束 8）。"""


class RunsPanel(BaseModel):
    """底栏「最近运行 / Token / 成本」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[ActivityEntry, ...] = ()
    run_count: int = Field(default=0, ge=0)
    """全部抽取运行数（`entries` 只是最近的一截）。"""

    totals: CostTotals


# ══════════════════════════════════════════════════════════════════════════
# 收窄 —— 日志是一个全新的泄漏面
# ══════════════════════════════════════════════════════════════════════════

_NODE_KEYS: Final[frozenset[str]] = frozenset({"id", "label", "name", "props"})

_SECRET_DETAIL_KEYS: Final[frozenset[str]] = frozenset({"description", "sub_of"})
"""**历史行专用**（2026-08-25 起）。

原来它写的是 `frozenset(SecretDetail.model_fields)`——「从模型上取，不抄一份」，
那张表加一列这张网跟着变宽。**秘密下线之后那个模型没了，所以它现在只能是字面量。**

它没有跟着一起删，因为 `decision_log` 三个触发器封死了 DELETE：**2026-08-25 之前
写下的 `secret_declare` 行还躺在库里，payload 里就是作者手打的那段秘密正文**。
删掉这张网 = 那些行从今天起从日志接口原样出去。没有写入方了，但有历史数据。
"""


def _is_secret_detail(value: Any) -> bool:
    """这份字典是不是一行**历史的** `secret` 扩展表（而不是恰好叫 secret 的窄引用）。

    判据是形状，不是键名，因为两者真的同名：老 `corrections.py` 的
    `payload["secret"]` 是 `{id,label,name}` 的窄引用（作者得看得出这条日志说的是
    哪个秘密），而带内容的那一份带 `description`、除了 `sub_of` 没有别的字段。
    **按键名清空会把「改的是哪个秘密」一起收窄掉**，那是另一种坏法。
    """
    return (
        isinstance(value, dict) and "description" in value and value.keys() <= _SECRET_DETAIL_KEYS
    )


def narrow_payload(value: Any) -> Any:
    """把一份审计信封收窄到可以出接口的样子。

    三条结构规则，递归执行：

    1. 任何**恰好长成一个完整 `Node` dump**（id/label/name/props 四键齐备）的字典，
       收窄成 `{id,label,name}`；
    2. 任何名为 `props` 的键，直接丢掉；
    3. 任何长成旧 `SecretDetail` dump 的字典（带 `description`），**整份清空**。
       **这一条今天没有写入方，只罩历史行**——见 `_SECRET_DETAIL_KEYS`。

    ── 为什么第 3 条当年必须单独存在，以及为什么它今天还在 ────────────────
    **作者写在一个 Secret 上的东西有两个存放处，`props` 只是其中一个。**
    `props.twist` 走 `NodeProps` 的 `extra="allow"`；而 `POST /nodes` 的
    `description` 落在 `secret` 扩展表上，那是作者真正打字打进去的那段秘密正文。
    第 1 条只在四键齐备时命中——`NodeSpec`（也就是当年 `declare_node` 的**入参**）
    没有 `id`，于是 props 被丢掉、`secret` 原样穿过去。

    秘密下线（ADR 0039）删掉了写入方，**没有删掉已经写下的行**：`decision_log` 的三个
    触发器封死了 DELETE。所以这一条留着。

    ── 为什么不复用 `api/app.py::_narrow` ────────────────────────────────
    那一个的判据是「first_appears > 当前章」，而它需要一个「当前章」。日志行没有当前章
    （一条 2026-08-10 的确认要拿哪一章去比？），`chapter=None` 时那个函数**什么都不
    收窄**——于是一个带 `props.plot_note` 的 Location 会原样穿过去。
    所以这里用更严的一条：**props 一个字都不出去**，不管挂在谁身上。
    （同 `/resolve` 那条「一律收窄，比 `_narrow(chapter=None)` 更严」的取舍。）

    ── 它罩不住什么（诚实交代）──────────────────────────────────────────
    这是**结构**收窄不是语义收窄。写入方要是把秘密内容平铺成
    `{"twist": "…"}` 塞进 payload，这里看不出来——**payload 的语义安全是写入方的
    责任**，本函数只兜住已经实测过的那几种泄漏形态（`graph.models.NodeRef` 的
    docstring 记着 props 那两种，第 3 条补的是历史秘密正文那一种）。今天六个写入点一个
    都不带这些东西，`tests/test_activity.py` 用一本**带毒的书 + 一个故意会漏的写入方**
    两头钉住了这件事。
    """
    if isinstance(value, dict):
        if _NODE_KEYS <= value.keys():
            return {"id": value["id"], "label": value["label"], "name": value["name"]}
        if _is_secret_detail(value):
            # 空字典而不是删掉这个键：「这儿本来有东西，它不该到这个面上来」比
            # 键凭空消失更诚实，前端也不必分「没有」和「不给」。
            return {}
        return {k: narrow_payload(v) for k, v in value.items() if k != "props"}
    if isinstance(value, list):
        return [narrow_payload(item) for item in value]
    return value


# ══════════════════════════════════════════════════════════════════════════
# 措辞表 —— 全部中文留在后端（前端不写文案分支）
# ══════════════════════════════════════════════════════════════════════════

_RUN_STATUS: Final[dict[str, ActivityStatus]] = {
    ExtractionRunStatus.PENDING: ActivityStatus.PENDING,
    ExtractionRunStatus.RUNNING: ActivityStatus.RUNNING,
    ExtractionRunStatus.SUCCEEDED: ActivityStatus.SUCCEEDED,
    ExtractionRunStatus.FAILED: ActivityStatus.FAILED,
    # 021 / Task 9：晚到的旧快照结果。Status 是「成功/失败」之外的一档——
    # 它没跑错，只是结果不再适用；记一笔 PENDING 直到有什么盖住它不合适，
    # 它也不会「重新来过」。沿用 FAILED 的粗略态最诚实：作者不需要为
    # 一条绝不会变成 current 的旧 run 操心。指标那侧不把它数成失败。
    ExtractionRunStatus.SUPERSEDED: ActivityStatus.FAILED,
}

_CAPABILITY_LABEL: Final[dict[str, str]] = {
    "extractor": "抽取",
    "summarizer": "章节总结",
    "writer": "起草",
    # `agent/loop.py::AGENT_CAPABILITY`。这一列认不出的是**原样回吐**的
    #（`_capability_label`），所以新长出一种花钱的动作就要在这儿补一行，
    # 否则日志页上写的是 `agent`。
    "agent": "写作助手",
    # `advisory_review.ADVISORY_CAPABILITY`。保存之后那一遍事后语义核对
    #（秘密有没有说破 / 跟后面的章抵不抵触）——它只写通知，不阻断任何东西。
    "advisory": "事后核对",
}

_KIND_LABEL: Final[dict[str, str]] = {
    # ⚠️ 带 †的四行**今天没有写入方**（秘密下线，ADR 0039），删不得：`decision_log`
    #    三个触发器封死 DELETE，2026-08-25 之前的行还在库里，而这张表漏一行就意味着
    #    作者的日志页上出现一句 `knowledge_edit`（`_kind_label` 兜底成「一次改动」，
    #    比英文好，但也把「当年这条改的是什么」抹掉了）。
    "alias_merge": "登记称呼",
    "node_declare": "登记条目",
    "secret_declare": "登记秘密",  # †
    "knows_declare": "声明认知",  # †
    "located_declare": "声明位置",
    "state_declare": "声明生死",
    "first_appearance_declare": "声明首次登场",
    "proposal_review": "抽取结果审阅",
    "knowledge_edit": "更正认知类型",  # †
    "knowledge_add": "补一条认知",  # †
    "event_edit": "更正事件名单",
    "event_summary_edit": "编辑情节摘要",
    "canon_edge_edit": "更正地点/状态/关系",
    "canon_edge_retract": "撤回地点/状态/关系",
    "chapter_draft": "写进正文",
}

_VERDICT_LABEL: Final[dict[str, str]] = {
    "accept": "接受",
    "reject": "否决",
    "edit": "改过之后接受",
}

_EDGE_LABEL: Final[dict[str, str]] = {
    # KNOWS / BELIEVES 已不是 `EdgeType` 的成员（ADR 0039），这两行**只为历史日志行
    # 存在**：老 `knows_declare` / `knowledge_edit` 的 payload 里就写着这两个字符串。
    "KNOWS": "知道",
    "BELIEVES": "以为",
    "LOCATED_AT": "在",
    "MEMBER_OF": "属于",
    "RELATED_TO": "关系",
    "HAS_STATE": "状态",
    "OWNS": "有",
    "PLANTED_IN": "埋在",
    "RESOLVED_IN": "回应于",
}
"""边类型 → 作者的说法。

这一行不是美化：日志页上那句 `萧决 LOCATED_AT 青云城主府` 会当着作者的面把引擎的
枚举值摆出来。
**别把这张表搬到前端去**——那样屏幕上的措辞就和 CLI / 别的日志行不是同一句话了，
而这一整节的标题写着「全部中文留在后端」。
"""

_NODE_LABEL: Final[dict[str, str]] = {
    "Character": "人物",
    "Location": "地点",
    "Faction": "势力",
    "Secret": "秘密",  # 已不是 NodeLabel 成员，只为历史日志行留着（同 _EDGE_LABEL）
    "Foreshadow": "伏笔",
    "Object": "物品",
    "StateDim": "状态",
    "Chapter": "章",
}
"""节点类别 → 作者的说法。同上：`萧决（Character）` 是登记条目那一行的原样输出。"""

_RUN_ERROR_LABEL: Final[dict[str, str]] = {
    ExtractionErrorCode.PROMPT_DRIFT: "这一章在排队期间被改过，整理没有继续（重新整理一次即可）",
    # ── provider 那四档 + 兜底（2026-08-25）──────────────────────────────
    #
    # 这五句从前是**一句**：「没能连上你配置的模型服务」。真书上撞到的那一次是
    # 401 CreditsError（余额耗尽），连上了，而作者被那句话指去查网络和地址。
    # **一句听起来很具体的假话，比一句诚实的「说不清」贵得多。**
    #
    # 分档判据在 `draft.provider.ProviderFailureKind`（只看 HTTP 状态码，不读文案）。
    # **这几句里一个状态码都不许出现**——那是机器码，同这张表的整条规矩。
    ExtractionErrorCode.PROVIDER_AUTH: (
        "模型服务没接受你的密钥。可能是密钥不对，也可能是这把密钥用不了你填的那个地址"
        "——有些服务商按套餐分了不同的地址"
    ),
    ExtractionErrorCode.PROVIDER_QUOTA: "你在模型服务商那儿的额度或余额不够了，去他们的后台看一眼",
    ExtractionErrorCode.PROVIDER_UNREACHABLE: "没能连上你配置的模型服务",
    ExtractionErrorCode.PROVIDER_UPSTREAM: "模型服务那边出了问题，过一会儿再试",
    ExtractionErrorCode.PROVIDER_FAILURE: "这一次没能调用模型，而系统没能说清是为什么",
    ExtractionErrorCode.CALL_RECORD_FAILURE: "模型答了，但这次调用没能记进账里，整理没有继续",
    ExtractionErrorCode.ANALYSIS_FORMAT: "模型这次答的东西读不出来",
    ExtractionErrorCode.INGEST_FAILURE: "整理结果没能写进这本书，这一章维持原样",
}
"""抽取失败的原因 → 作者的说法。**全仓唯一一份**（`run_error_label` 是它的读口）。

**这一节原来根本不存在**，`_run_errors` 直接把 `f"{code}：{message}"` 摆上屏，于是
一次 provider 抖动在小说作者的日志页上长这样：

    provider_failure：chapter analysis provider failed

一个 snake_case 机器码 + 一整句英文。它没被任何守卫抓到，因为**夹具里从来没有过一次
失败的抽取**——`api.json` 里三条 run 全是 `succeeded`，那条「屏幕上没有研发术语」的
断言扫的是一块永远干净的屏幕。判据没错，样本缺了一半。

`ExtractionRunError.message` 是写给维护者的英文诊断，**永不上屏**：库就在维护者手上，
而作者读不懂它。所以这里只翻 `code`，不拼 `message`。

⚠️ **2026-08-13：同一个 bug 在另一条路径上还活着，被这张表治好了第二次。**
日志页那条翻对了，可**审阅面板**（`ProposalReviewTab`）读的是另一条端点
（`GET …/extractions/{run_id}`），而那条端点当时把 `ExtractionRunError` 原样发出去，
界面渲染的就是 `message`——于是同一句英文绕开这张表又上了一次屏。根因在类型层：
前端的 `ExtractionRun.errors` 把字段名抄成了 `kind`/`message`，**可翻译的那个
`code` 在类型里根本够不着**。现在 `api/extraction.py` 在出门前就把这张表用上，
那句英文**不再出现在任何一条 HTTP 出参里**——不是「前端记得别渲染」，是它拿不到。
**别在前端补第二张表，也别在 `extract/` 里补第三张。**
"""

_ACTOR_LABEL: Final[dict[str, str]] = {
    decisions.DEFAULT_ACTOR: "作者",
    decisions.SYSTEM_ACTOR: "系统",
}

_UNRECORDED: Final = "未记录"


def actor_label(actor: str) -> str:
    """`author` / `system` → 中文。认不出的原样回吐（这一列是开放字符串）。"""
    return _ACTOR_LABEL.get(actor, actor)


def _capability_label(capability: str) -> str:
    return _CAPABILITY_LABEL.get(capability, capability)


def _kind_label(kind: str) -> str:
    """认不出的**不原样回吐**，退到一句中文（同 `_edge_label`，反着 `actor_label`）。

    `DecisionKind` 是封闭枚举，认不出只可能是这张表漏了一行——而漏掉的那一行会以
    `canon_edge_edit` 的形态出现在小说作者的日志页标题上。**这不是假想**：2026-08 有一次
    改动往那个枚举里加了两行（`knowledge_edit` / `event_edit`），加的人记得补了表，
    而「记得补」不是一道守卫。`test_the_wording_tables_cover_every_value_they_can_be_handed`
    盯表、这一行兜底，两条一起才轮不到运气。
    """
    return _KIND_LABEL.get(kind, "一次改动")


def _verdict_label(verdict: str) -> str:
    """同 `_kind_label`：`Verdict` 是封闭枚举，认不出只可能是表漏了行，退到一句中文。"""
    return _VERDICT_LABEL.get(verdict, "已处理")


def _edge_label(edge_type: str) -> str:
    """认不出的**不原样回吐**，退到一句中文。

    和 `actor_label` 反着来是有意的：`decision_log.actor` 是开放字符串（明天多一种
    actor，露出英文也好过显示成空白），而 `EdgeType` 是一个封闭枚举——认不出只可能是
    这张表漏了一行，而漏掉的那一行会以 `RELATED_TO` 的形态出现在小说作者的屏幕上。
    """
    return _EDGE_LABEL.get(edge_type, "关系")


def _node_label(label: str) -> str:
    return _NODE_LABEL.get(label, "条目")


def run_error_label(code: str) -> str:
    """同 `_edge_label`：认不出的**不原样回吐**，退到一句中文。

    `errors_json` 是 append-only 的审计资产，旧库里可能躺着今天已经删掉的码——
    那种行照样要显示成人话，而不是把 `provider_failure` 摆给作者。

    **公开（同 `actor_label`）**：日志页和审阅面板是两条读端，读的是同一批
    `extraction_run` 行。第二条（`api/extraction.py`）2026-08-13 接上来之前，
    它自己把那句英文发给了浏览器——见 `_RUN_ERROR_LABEL` 末尾那段。
    """
    return _RUN_ERROR_LABEL.get(code, "整理没有跑完（没有留下能看懂的原因）")


def _chapter_text(chapter: int | None) -> str:
    return _UNRECORDED if chapter is None else f"第 {chapter} 章"


def _elapsed_text(started: Any, finished: Any) -> str:
    """跑了多久。**不显示时刻**——时刻在折叠行上，且那儿是本地时间。

    两头缺一个就是「未记录」（§10 约束 8：零和空必须带着理由，不许糊成 0）。
    """
    begin, end = _text(started), _text(finished)
    if begin is None or end is None:
        return _UNRECORDED
    try:
        seconds = (
            datetime.fromisoformat(end.replace("Z", "+00:00"))
            - datetime.fromisoformat(begin.replace("Z", "+00:00"))
        ).total_seconds()
    except ValueError:
        return _UNRECORDED
    if seconds < 0:
        return _UNRECORDED
    return f"{seconds:.1f} 秒" if seconds < 60 else f"{seconds / 60:.1f} 分钟"


def _num(value: Any) -> str:
    """数字字段的显示。**None 是「没记」不是 0**（§10 约束 8）。"""
    return _UNRECORDED if value is None else str(value)


def _cache_text(read: Any, written: Any) -> str:
    """这一次调用里，输入有多少是**不用重新算**的。

    ── 屏幕上为什么不出现「缓存命中」这四个字 ──────────────────────────────
    作者关心的不是缓存这个机制，是「这次比原价省了多少」。「命中」是研发的说法
    （它甚至是 `screenGuard` 那条「小写裸枚举」盲区里的同类——收不住但不该上屏），
    所以这一行说的是**结果**：接着上次的那部分没有重新算。

    ── 三档必须分得开（§10 约束 8：零要带着理由一起出现）──────────────────
    | 库里 | 屏幕上 | 意思 |
    |---|---|---|
    | `NULL` | 「未记录」 | 端点根本没报这件事（或这行早于这两列） |
    | `0` | 「这次没接上……」 | 端点报了，真的一次都没命中 |
    | `>0` | 「N token 接着上次」 | 省下的那部分 |

    **这三档指向三个不同的动作**（去查端点支不支持 / 去查前缀被谁弄脏了 / 什么都不用做），
    所以把 `NULL` 渲染成 0 不是「显示得难看一点」，是把作者指向错误的一件事。

    写入那一档（只有 Anthropic 兼容端点报）**报了才说**：在 DeepSeek / OpenAI 上它恒为
    `None`，凭空多一行永远「未记录」只是噪音；而它一旦有数，那是作者真花掉的钱。
    """
    parts: list[str] = []
    if read is None:
        parts.append(_UNRECORDED)
    elif read == 0:
        parts.append("这次没接上，整段输入都重新算了")
    else:
        parts.append(f"{read} token 接着上次，没有重新算")
    if written == 0:
        parts.append("这次没有新存下内容")
    elif written is not None:
        parts.append(f"另存下 {written} token 供下次接")
    return " · ".join(parts)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    # bool 是 int 的子类，放进来会让 True 变成第 1 章。
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dig(payload: dict[str, Any], *path: str) -> Any:
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


# **这里原本有一个 `_short()`**（指纹只显示前 12 位）。它随着 prompt / artifact
# 那几行一起没了：截断一个 sha256 并不能让它变成作者看得懂的东西，只是让它变短——
# 而「把认不出的东西截短了再摆上屏」正是 `ProposalReviewTab` 那条
# `id.slice(-6)` 的病（`node:` 那条守卫就是被截断逃掉的）。


# ══════════════════════════════════════════════════════════════════════════
# 游标
# ══════════════════════════════════════════════════════════════════════════

_CURSOR_SEP: Final = "|"


def _encode_cursor(entry: ActivityEntry) -> str:
    return f"{entry.ts}{_CURSOR_SEP}{entry.id}"


def _parse_cursor(cursor: str | None) -> tuple[str, str] | None:
    """`"ts|id"` → (ts, id)。形状不对直接 ValueError（→ 422），不静默当作第一页。

    静默降级的后果是分页永远回到顶部，而作者只会看到「我怎么翻不动」——
    那正是本仓库反复在修的那种「漂亮的失败」。
    """
    if cursor is None:
        return None
    ts, sep, entry_id = cursor.partition(_CURSOR_SEP)
    if not sep or not ts or not entry_id:
        raise ValueError(f"游标形状不对（应为 `ts{_CURSOR_SEP}id`）：{cursor!r}")
    return ts, entry_id


def _before_clause(column: str, before: tuple[str, str] | None) -> tuple[str, list[Any]]:
    """严格早于游标的那些行。`(ts, id)` 复合比较——ts 只有毫秒精度，同毫秒两条靠 ULID 断平。"""
    if before is None:
        return "", []
    ts, entry_id = before
    return f" AND ({column} < ? OR ({column} = ? AND id < ?))", [ts, ts, entry_id]


# ══════════════════════════════════════════════════════════════════════════
# 折叠行：三个 source 各一个构造器
# ══════════════════════════════════════════════════════════════════════════


def _pending_by_snapshot(conn: Connection, project_id: str) -> dict[str, list[str]]:
    """还没审的提案，按它出自哪个快照分组。

    **只取 id**，不走 `ProposalStore.get`：那条路会把 `items_json` 一起读出来，
    而 `new_character` 提案的 items 里装着人物档案——一次只为了拿个 id 的读，
    没有理由把那些东西捞进内存。
    """
    rows = conn.execute(
        """
        SELECT id, snapshot_id FROM proposal_set
        WHERE project_id = ? AND status = 'PENDING' AND snapshot_id IS NOT NULL
        ORDER BY id ASC
        """,
        (project_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(str(row["snapshot_id"]), []).append(str(row["id"]))
    return out


def _run_jump(row: Any, pending: dict[str, list[str]]) -> ActivityJump:
    chapter = _int(row["chapter_number"])
    if str(row["status"]) == ExtractionRunStatus.FAILED:
        # ── 没跑成的那一条，作者要的不是「去第 N 章」，是「再来一次」 ──────────
        # **这个函数以前一眼都不看 status**：成功和失败走同一条路径（有待审就说去审阅，
        # 否则去第 N 章）。而重跑的能力后端一直都在——`POST …/chapters/{n}/extract`
        # 把那一行**原地重置**回排队（不删行、不新建行，`runner.enqueue` 的 force 分支）。
        # 于是屏幕上那句红字（「没能连上你配置的模型服务 · 失败」）是个死胡同，
        # 而它恰好是这一页上**唯一需要作者动手**的那一行。
        #
        # **status 排在待审提案前面**：同一份快照上可能躺着更早那次成功整理留下的提案，
        # 但这一行说的是「这一次没跑成」，它自己的下一步就是重来一次。那批提案在
        # 它们自己那一行上照旧点得到。
        return ActivityJump(
            target=JumpTarget.EXTRACTION_RETRY,
            label=f"再整理一次第 {chapter} 章",
            chapter_number=chapter,
        )
    waiting = pending.get(str(row["snapshot_id"]), [])
    if len(waiting) == 1:
        return ActivityJump(
            target=JumpTarget.PROPOSAL,
            label="去审阅这条待审提案",
            chapter_number=chapter,
            proposal_id=waiting[0],
        )
    if waiting:
        # **不替作者挑是哪一条**（同 AmbiguousName 那条纪律）：给章号，队列在那一章里。
        return ActivityJump(
            target=JumpTarget.PROPOSAL,
            label=f"第 {chapter} 章还有 {len(waiting)} 条待审",
            chapter_number=chapter,
        )
    return ActivityJump(
        target=JumpTarget.CHAPTER,
        label=f"去第 {chapter} 章",
        chapter_number=chapter,
    )


def _run_errors(row: Any) -> tuple[str, ...]:
    """这次整理为什么没跑完 —— **只翻 `code`，`message` 一个字都不带出来**。

    见 `_RUN_ERROR_LABEL`：那一列的 `message` 是写给维护者的英文诊断。
    """
    try:
        parsed = json.loads(row["errors_json"])
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(
        run_error_label(_text(item.get("code")) or "")
        for item in parsed
        if isinstance(item, dict)
    )


def _run_entry(row: Any, pending: dict[str, list[str]]) -> ActivityEntry:
    raw_status = str(row["status"])
    status = _RUN_STATUS.get(raw_status, ActivityStatus.PENDING)
    chapter = _int(row["chapter_number"])
    if status is ActivityStatus.SUCCEEDED:
        subtitle = (
            f"有效事件 {row['valid_event_count']} 条 · "
            f"丢弃 {row['discarded_event_count']} 条 · "
            f"待审提案 {row['proposal_count']} 条"
        )
    elif status is ActivityStatus.FAILED:
        errors = _run_errors(row)
        subtitle = errors[0] if errors else "抽取失败（没有留下错误明细）"
    elif status is ActivityStatus.RUNNING:
        subtitle = "正在跑"
    else:
        # 认不出的状态**不原样回吐**（同 `_edge_label`）：`f"状态：{raw_status}"` 会把
        # 引擎枚举摆到作者脸上，而 `_RUN_STATUS` 认不出只可能是那张表漏了一行。
        subtitle = "排队中" if raw_status == ExtractionRunStatus.PENDING else "等着整理"
    return ActivityEntry(
        id=str(row["id"]),
        source=ActivitySource.EXTRACTION,
        ts=str(row["created_at"]),
        actor=decisions.SYSTEM_ACTOR,
        status=status,
        title=f"第 {chapter} 章抽取",
        subtitle=subtitle,
        chapter_number=chapter,
        jump=_run_jump(row, pending),
    )


def _call_chapter(row: Any) -> int | None:
    """这次调用是为哪一章花的。**先看它自己那一列，答不上来再反查。**

    ── 反查为什么必须留着 ────────────────────────────────────────────────
    `model_call.chapter_number` 是 2026-08-12 才加的（`009_call_chapter.sql`）。
    作者库里已经躺着上百行**这一列是 NULL** 的旧账，它们的章号只有反查拿得到——
    改成只读这一列，那些行的章号会**当场消失而且不报错**。
    （新行两条路都答得出，答案相同；旧行只有反查；起草那条路只有这一列，
    因为它没有一张可反查的业务表——那正是加这一列的理由。）

    `or` 在这儿是安全的：三处章号都有 `>= 1` 的 CHECK，不存在会被当成假的 0。
    """
    own = _int(row["own_chapter"])
    if own is not None:
        return own
    return _int(row["run_chapter"]) or _int(row["summary_chapter"])


def _call_jump(row: Any, chapter: int | None) -> ActivityJump | None:
    """这一次调用，跳去哪儿。

    ── 判据是**这次调用留下了什么**，不是 capability 那个字符串 ────────────────
    「它是不是一次章节总结」这里问的是「库里有没有一行 `chapter_summary` 指着它」
    ——那是写入方留下的结构，而 `capability == "summarizer"` 是一份要两处一起改的
    字符串约定（写在 `draft/rolling_summary.py`，认在这儿）。按结构判，
    换个能力名不会让这颗按钮静默消失；而一次**没能写出总结**的调用（模型答了空文本）
    也不会假装那儿有一段总结可看。

    在这一档存在之前，写总结那次调用退到兜底坐标「去第 N 章」——而右栏那一格
    （`SummaryTab`）读改撤回都在，只是日志上没人指过去。
    """
    if chapter is None:
        return None
    if _text(row["summary_text"]) is not None:
        return ActivityJump(
            target=JumpTarget.SUMMARY,
            label=f"去看第 {chapter} 章的总结",
            chapter_number=chapter,
        )
    return ActivityJump(
        target=JumpTarget.CHAPTER,
        label=f"去第 {chapter} 章",
        chapter_number=chapter,
    )


def _call_entry(row: Any) -> ActivityEntry:
    label = _capability_label(str(row["capability"]))
    chapter = _call_chapter(row)
    tokens = f"入 {_num(row['tokens_in'])} / 出 {_num(row['tokens_out'])} token"
    elapsed = _UNRECORDED if row["ms"] is None else f"{row['ms']} ms"
    return ActivityEntry(
        id=str(row["id"]),
        source=ActivitySource.MODEL_CALL,
        ts=str(row["ts"]),
        actor=decisions.SYSTEM_ACTOR,
        status=ActivityStatus.SUCCEEDED,
        title=f"模型调用 · {label}",
        subtitle=f"{row['model']} · {tokens} · {elapsed}",
        chapter_number=chapter,
        jump=_call_jump(row, chapter),
    )


def _decision_jump(decision: decisions.Decision) -> ActivityJump | None:
    """这条确认改的东西，今天能从哪儿改回去。

    只认**今天真的存在**的编辑入口。认不出来就退到章号，认不出章号就不给 jump
    ——给一个点了没反应的按钮比不给按钮更糟。
    """
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    kind = decision.kind
    chapter = decision.chapter_number

    # 认知那两档（KNOWLEDGE_EDIT / KNOWLEDGE_ADD / KNOWS_DECLARE）原来跳「去认知矩阵
    # 改这一格」。**它们随秘密下线一起没了**（ADR 0039）：那一格、那条路由、那个
    # JumpTarget 成员都删了。历史行仍在 decision_log 里（三个触发器封死 DELETE），
    # 只是它们现在退到兜底坐标「去第 N 章」——那正是「今天没有任何路由能改这个东西」
    # 的诚实形态，而不是指着一颗点了 404 的按钮。
    if kind == decisions.DecisionKind.EVENT_EDIT:
        event_id = _text(payload.get("event_id"))
        if event_id:
            return ActivityJump(
                target=JumpTarget.EVENT_CAST,
                label="去改这条事件的知情 / 在场名单",
                chapter_number=_int(payload.get("chapter_number")) or chapter,
                event_id=event_id,
            )

    if kind == decisions.DecisionKind.PROPOSAL_REVIEW:
        events = payload.get("events")
        # **被否决的那条不给编辑入口**：`/canon/events/{id}/cast` 只改 CANON 事件，
        # 而 reject 之后它根本没升上去——指过去就是一个必然 404 的按钮。
        applied = decision.decision in (decisions.Verdict.ACCEPT, decisions.Verdict.EDIT)
        # 恰好一条时才给坐标：一次确认可以带一批事实，替作者挑第一条就是在猜。
        if applied and isinstance(events, list) and len(events) == 1:
            event_id = _text(_dig(events[0], "event_id")) if isinstance(events[0], dict) else None
            if event_id:
                return ActivityJump(
                    target=JumpTarget.EVENT_CAST,
                    label="去改这条事件的知情 / 在场名单",
                    chapter_number=chapter,
                    event_id=event_id,
                )
        if applied and isinstance(events, list) and len(events) > 1 and chapter is not None:
            # ── 这一行的空 `endpoints` 和别处的空**不是一个意思** ──────────────
            # 「有好几条，后端不替作者挑是哪一条」——那几条事件其实
            # `/canon/events/{id}/cast` 一打就通。两种含义共用一个空元组是出参形状
            # 的事，改不到这一层（日志页正照着今天这份契约在写）。
            # 但**一句「去第 4 章」会让作者以为那几条没救了**，而 ADR 0020 的整条退路
            # 就是「改得掉」——所以数目必须写进措辞里。
            # `promote_clean_facts` 一次升掉一整章的干净事实，所以这是常态不是边角。
            return ActivityJump(
                target=JumpTarget.CHAPTER,
                label=f"改了 {len(events)} 条事件，去第 {chapter} 章逐条改",
                chapter_number=chapter,
            )
        # 自动升上去的边（Task 8 / ADR 0032）：现在有真实编辑入口，不再落兜底。
        edges = payload.get("edges")
        if applied and isinstance(edges, list) and len(edges) == 1:
            edge_id = _text(_dig(edges[0], "edge_id")) if isinstance(edges[0], dict) else None
            if edge_id:
                label = "去改这条自动生成的边"
                if chapter is not None:
                    label += f"（第 {chapter} 章）"
                return ActivityJump(
                    target=JumpTarget.CANON_EDGE,
                    label=label,
                    chapter_number=chapter,
                    edge_id=edge_id,
                )

    if chapter is not None:
        return ActivityJump(
            target=JumpTarget.CHAPTER,
            label=f"去第 {chapter} 章",
            chapter_number=chapter,
        )
    return None


def _proposal_review_subtitle(decision: decisions.Decision) -> str:
    payload = decision.payload
    verdict = _verdict_label(decision.decision.value)
    parts = [
        f"事件 {_count(payload.get('events'))} 条",
        f"关系 {_count(payload.get('edges'))} 条",
    ]
    characters = _count(payload.get("characters"))
    if characters:
        parts.append(f"人物 {characters} 个")
    return f"{verdict}：" + " · ".join(parts)


def _decision_subtitle(decision: decisions.Decision) -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    kind = decision.kind
    subject = decision.subject_name or "—"

    if kind == decisions.DecisionKind.PROPOSAL_REVIEW:
        return _proposal_review_subtitle(decision)
    # 下面这三档（KNOWLEDGE_EDIT / KNOWLEDGE_ADD / KNOWS_DECLARE）**今天没有写入方**
    # （秘密下线，ADR 0039）。留着是因为历史行还在 `decision_log` 里，而它们的副标题
    # 是作者当年那次确认的全部内容——删掉这几个分支，那些行会退到最后那句
    # `subject`，日志页上只剩一个光秃秃的人名。
    if kind == decisions.DecisionKind.KNOWLEDGE_EDIT:
        secret = _text(_dig(payload, "secret", "name")) or "—"
        before = _edge_label(_text(_dig(payload, "from", "edge_type")) or "")
        after = _edge_label(_text(_dig(payload, "to", "edge_type")) or "")
        return f"{subject} 对「{secret}」：{before} → {after}"
    if kind == decisions.DecisionKind.KNOWLEDGE_ADD:
        # **不复用上面那一行**：这一格之前是「不知道」，没有「从什么改成什么」。
        # 硬套那句话的产物是 `_edge_label("")` 的兜底——屏幕上会写「关系 → 知道」。
        secret = _text(_dig(payload, "secret", "name")) or "—"
        after = _edge_label(_text(_dig(payload, "to", "edge_type")) or "")
        return f"{subject} 对「{secret}」：补上「{after}」"
    if kind == decisions.DecisionKind.EVENT_EDIT:
        knowers = payload.get("knowers") if isinstance(payload.get("knowers"), dict) else {}
        cast = payload.get("participants") if isinstance(payload.get("participants"), dict) else {}
        return (
            f"知情 +{_count(knowers.get('added'))} −{_count(knowers.get('removed'))} · "
            f"在场 +{_count(cast.get('added'))} −{_count(cast.get('removed'))}"
        )
    if kind == decisions.DecisionKind.CHAPTER_DRAFT:
        # **这一行说的是「你的正文被改了」**，所以它只报作者当场能核对的两个量：
        # 哪一章、多长。落盘那一版的 `text_sha256` 在 payload 里（版本抽屉靠它对号），
        # 但那是个作者认不得的东西，不上副标题（同 `_run_detail` 去掉指纹那条判据）。
        where = _chapter_text(_int(payload.get("chapter_number")) or decision.chapter_number)
        units = _int(payload.get("units"))
        return where if units is None else f"{where} · 约 {units} 字"
    if kind == decisions.DecisionKind.ALIAS_MERGE:
        surface = _text(payload.get("surface")) or "—"
        return f"{subject} ← 「{surface}」"
    if kind in (decisions.DecisionKind.KNOWS_DECLARE, decisions.DecisionKind.LOCATED_DECLARE):
        edge_type = _edge_label(_text(payload.get("edge_type")) or "")
        target = _text(payload.get("object_name")) or "—"
        return f"{subject} {edge_type} {target}"
    label = _text(payload.get("label"))
    return f"{subject}（{_node_label(label)}）" if label else subject


def _decision_entry(decision: decisions.Decision) -> ActivityEntry:
    return ActivityEntry(
        id=decision.id,
        source=ActivitySource.DECISION,
        ts=decision.ts,
        actor=decision.actor,
        # 一条已经落库的确认按定义已经发生了——它没有「失败」这个态。
        status=ActivityStatus.SUCCEEDED,
        title=f"{actor_label(decision.actor)} · {_kind_label(decision.kind)}",
        subtitle=_decision_subtitle(decision),
        chapter_number=decision.chapter_number,
        jump=_decision_jump(decision),
    )


# ══════════════════════════════════════════════════════════════════════════
# 读
# ══════════════════════════════════════════════════════════════════════════

# 两条 SELECT 都**只取会被读的列**。这不是省内存：`params_json` / `in_artifact` /
# `out_artifact` / `prompt_hash` 是这两张表上最容易长出敏感内容的几列（`001_init.sql`
# 明写 params 还要装 thinking / output_config），不取出来就不可能被谁「顺手也渲染一下」。
# 收窄的最强形态是**根本没拿到手**。
_RUN_SELECT: Final = """
SELECT id, chapter_number, snapshot_id, status, errors_json, valid_event_count,
       discarded_event_count, proposal_count, model_call_id,
       created_at, started_at, finished_at
FROM extraction_run
WHERE project_id = ?
"""

_CALL_SELECT: Final = """
SELECT id, ts, capability, model,
       tokens_in, tokens_out, ms, cost, attempt,
       cache_read_tokens, cache_write_tokens,
       chapter_number AS own_chapter,
       (SELECT r.chapter_number FROM extraction_run r WHERE r.model_call_id = model_call.id)
         AS run_chapter,
       (SELECT s.chapter_number FROM chapter_summary s WHERE s.model_call_id = model_call.id)
         AS summary_chapter,
       -- **这次调用写出来的那段总结正文。** 它是这条日志唯一说得出「它到底总结了什么」
       -- 的东西：展开层原来只有能力 / 模型 / token / 耗时，作者看得见花了钱，
       -- 看不见买到了什么。
       --
       -- 取的是**这一行调用产出的那一段**（`model_call_id` 是它的地址），不是这一章
       -- 此刻生效的那一段：作者后来改过 / 撤回过的话，最新那一行是他自己的字
       -- （`source='author'`，`model_call_id` 为空），而这条日志说的是当时买到了什么。
       -- 想看现在算数的那一段，跳过去（`_call_jump`）。
       (SELECT s.summary FROM chapter_summary s WHERE s.model_call_id = model_call.id)
         AS summary_text
FROM model_call
WHERE project_id = ?
"""

DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200
DEFAULT_RUNS: Final = 20


def read_activity(
    conn: Connection,
    project_id: str,
    *,
    actor: str | None = None,
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> ActivityPage:
    """一页折叠行，新的在前。

    `actor` 是**开放字符串的等值过滤**（`decision_log.actor` 那一列就是开放的）。
    `extraction_run` / `model_call` 按定义是系统跑的，所以只有 `actor is None` 或
    `actor == 'system'` 时它们才进这一页。

    三张表各多取一条再归并：任何排在第 `limit` 位之后的行都不可能被这一页要到，
    所以「归并结果比 limit 长」就是「还有下一页」。
    """
    if limit < 1 or limit > MAX_PAGE:
        raise ValueError(f"limit 只能是 1..{MAX_PAGE}，收到 {limit}")
    before = _parse_cursor(cursor)
    over = limit + 1
    entries: list[ActivityEntry] = []
    system_side = actor is None or actor == decisions.SYSTEM_ACTOR

    if system_side:
        pending = _pending_by_snapshot(conn, project_id)
        clause, args = _before_clause("created_at", before)
        runs = conn.execute(
            _RUN_SELECT + clause + " ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, *args, over),
        ).fetchall()
        entries.extend(_run_entry(row, pending) for row in runs)

        clause, args = _before_clause("ts", before)
        calls = conn.execute(
            _CALL_SELECT + clause + " ORDER BY ts DESC, id DESC LIMIT ?",
            (project_id, *args, over),
        ).fetchall()
        entries.extend(_call_entry(row) for row in calls)

    entries.extend(
        _decision_entry(item)
        for item in decisions.read_page(
            conn,
            project_id,
            actor=actor,
            before_ts=None if before is None else before[0],
            before_id=None if before is None else before[1],
            limit=over,
        )
    )

    entries.sort(key=lambda e: (e.ts, e.id), reverse=True)
    page = entries[:limit]
    has_more = len(entries) > limit
    return ActivityPage(
        entries=tuple(page),
        next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
        actors=_actor_tally(conn, project_id),
    )


def _actor_tally(conn: Connection, project_id: str) -> tuple[ActorTally, ...]:
    """整条时间线上每个 actor 各有多少行（**不受当前过滤影响**，见 `ActorTally`）。"""
    counts = dict(decisions.tally_by_actor(conn, project_id))
    system = conn.execute(
        "SELECT (SELECT COUNT(*) FROM extraction_run WHERE project_id = ?)"
        " + (SELECT COUNT(*) FROM model_call WHERE project_id = ?)",
        (project_id, project_id),
    ).fetchone()[0]
    counts[decisions.SYSTEM_ACTOR] = counts.get(decisions.SYSTEM_ACTOR, 0) + int(system)
    return tuple(
        ActorTally(actor=name, count=count)
        for name, count in sorted(counts.items())
        if count > 0
    )


def read_runs(
    conn: Connection,
    project_id: str,
    *,
    limit: int = DEFAULT_RUNS,
) -> RunsPanel:
    """底栏那一格：最近几次抽取 + 整本书的模型账。"""
    if limit < 1 or limit > MAX_PAGE:
        raise ValueError(f"limit 只能是 1..{MAX_PAGE}，收到 {limit}")
    pending = _pending_by_snapshot(conn, project_id)
    rows = conn.execute(
        _RUN_SELECT + " ORDER BY created_at DESC, id DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    run_count = conn.execute(
        "SELECT COUNT(*) FROM extraction_run WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    # **一个 `COALESCE` 都没有，那是有意的。** `SUM` 在「一行都没有」和「全是 NULL」
    # 两种情形上都返回 NULL，而那正好就是「不知道」——补成 0 就是把不知道说成零，
    # 也就是 `CostTotals.tokens_in` 记着的那条已知病。缺了几行由 `metered` 说出来。
    totals = conn.execute(
        """
        SELECT COUNT(*)                                        AS calls,
               SUM(CASE WHEN tokens_in IS NULL
                         AND tokens_out IS NULL THEN 0 ELSE 1 END) AS metered,
               SUM(tokens_in)                                  AS tokens_in,
               SUM(tokens_out)                                 AS tokens_out,
               SUM(ms)                                         AS ms,
               SUM(CASE WHEN cost IS NULL THEN 0 ELSE 1 END)   AS priced,
               SUM(cost)                                       AS cost
        FROM model_call WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    priced = int(totals["priced"] or 0)
    return RunsPanel(
        entries=tuple(_run_entry(row, pending) for row in rows),
        run_count=int(run_count),
        totals=CostTotals(
            calls=int(totals["calls"]),
            metered_calls=int(totals["metered"] or 0),
            tokens_in=_int(totals["tokens_in"]),
            tokens_out=_int(totals["tokens_out"]),
            ms=_int(totals["ms"]),
            priced_calls=priced,
            # 一条都没定过价 ⇒ None（「没记账」），**不是 0.0**（「一分钱没花」）。
            cost=None if priced == 0 else float(totals["cost"]),
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# 展开详情
# ══════════════════════════════════════════════════════════════════════════


def _cost_of(conn: Connection, project_id: str, call_id: str | None) -> ActivityCost | None:
    if not call_id:
        return None
    row = conn.execute(
        """
        SELECT id, capability, model, tokens_in, tokens_out, ms, cost
        FROM model_call WHERE id = ? AND project_id = ?
        """,
        (call_id, project_id),
    ).fetchone()
    if row is None:
        return None
    return ActivityCost(
        call_id=str(row["id"]),
        capability=str(row["capability"]),
        model=str(row["model"]),
        tokens_in=_int(row["tokens_in"]),
        tokens_out=_int(row["tokens_out"]),
        ms=_int(row["ms"]),
        cost=None if row["cost"] is None else float(row["cost"]),
    )


def _run_detail(conn: Connection, project_id: str, entry_id: str) -> ActivityDetail | None:
    """跑了什么、结果是什么。

    ── 这里为什么没有快照 id / schema 版本 / prompt 指纹 ────────────────────
    它们 2026-08-11 之前在这张表上，而这张表是**小说作者**的屏幕：
    `snapshot:01J8…` / `m4.analysis.v1` / 一段 sha256 对他没有任何意义，
    对维护者也不必从这里看（库就在他手上）。**日志页的判据不是「这个值有没有用」，
    是「作者认不认得」**——认不得的东西留在屏幕上，等于把研发术语摆到他脸上，
    而这一页恰好是他点进两个编辑入口的门厅。

    ── 「排队于 / 开始于 / 结束于」三行为什么变成了一行「用时」 ──────────────
    它们原来是 `str(row["created_at"])`，也就是 `2026-08-10T00:00:00.100Z` 原样上屏：
    机器格式、UTC、带 `T` 和 `Z`，而**同一条记录的折叠行早就是本地时间**
    （`ActivityLog.tsx::shownTime` 走 `toLocaleString("zh-CN")`）。同一件事在同一页上
    有两种写法，其中一种作者读不懂——按这个 docstring 自己立的判据，它就得走。

    **它躲过守卫的方式值得记一笔**：契约夹具的规范化器把每个 ISO 时间戳换成 `<ts>`
    （`tests/test_frontend_contract.py::_TS`），于是浏览器那侧的屏幕守卫扫到的是
    `<ts>` 而不是真值——**凡是被规范化器抹掉形状的东西，前端守卫都看不见**。
    这一条是从 Python 那侧（`tests/test_wording_guard.py` 打真 app）掉出来的。
    """
    row = conn.execute(
        _RUN_SELECT + " AND id = ?", (project_id, entry_id)
    ).fetchone()
    if row is None:
        return None
    entry = _run_entry(row, _pending_by_snapshot(conn, project_id))
    rows = (
        DetailRow(label="章节", value=_chapter_text(_int(row["chapter_number"]))),
        DetailRow(label="有效事件", value=str(row["valid_event_count"])),
        DetailRow(label="丢弃事件", value=str(row["discarded_event_count"])),
        DetailRow(label="待审提案", value=str(row["proposal_count"])),
        DetailRow(label="用时", value=_elapsed_text(row["started_at"], row["finished_at"])),
    )
    return ActivityDetail(
        entry=entry,
        rows=rows,
        cost=_cost_of(conn, project_id, _text(row["model_call_id"])),
        errors=_run_errors(row),
    )


def _summary_rows(row: Any) -> tuple[DetailRow, ...]:
    """这一次调用**买到了什么**（今天只有章节总结这一档说得出来）。

    没写出总结的调用（抽取 / 起草 / 写作助手）**一行都不加**，而不是加一行「未记录」：
    那不是一个缺失的值，是这一档调用本来就没有这个字段——凭空多一行永远「未记录」
    只是噪音（同 `_cache_text` 里「写入那一档报了才说」的取舍）。

    **「总结跑了但没留下正文」这一档不存在，所以这儿没有它的分支**：
    `RollingSummarizer.ensure` 先判空文本（空就抛，一行都不记），再把
    `model_call` 和 `chapter_summary` 写在**同一个事务**里——两者要么都在，要么都不在。
    哪天那两步被拆开，这里就要跟着长出一句「这次没留下正文」。
    """
    text = _text(row["summary_text"])
    return () if text is None else (DetailRow(label="这次写出来的总结", value=text),)


def _call_detail(conn: Connection, project_id: str, entry_id: str) -> ActivityDetail | None:
    """这一次调用花了多少。

    ── `params_json` 那一行为什么整条没了 ─────────────────────────────────
    它曾经是 `str(row["params_json"])` **原样出接口**，是日志读端唯一没过收窄的直通口。
    今天安全只因为写入方恰好只写 `{finish_reason, schema_version}` 两个键，而
    `001_init.sql` 的注释明写这一列还要长（「必须如实记录 thinking / output_config」）——
    **毒测覆盖今天的写入方，不覆盖明天的**。它对小说作者又是零信息量的，
    所以处置是「不出接口」而不是「再补一层收窄」：不出去的东西不需要被收窄。

    同理去掉的还有 prompt 指纹和两个 artifact 指纹（`artifact:sha256:…`）——
    见 `_run_detail` 的说明，判据是「作者认不认得」。

    ── 「这次写出来的总结」为什么在 `rows` 里，不在 `payload` 里 ──────────────
    `payload` 是审计信封（引擎内部字段，`ActivityDetail` 那条注释写着它一个字都不该
    渲染），而这一段是**作者自己的书**里的字——它属于「后端已经写成人话的那一份」。
    所以它走投影层，和别的行一样是一对「标签 → 值」，前端不必为它写一行文案分支。
    """
    row = conn.execute(_CALL_SELECT + " AND id = ?", (project_id, entry_id)).fetchone()
    if row is None:
        return None
    chapter = _call_chapter(row)
    rows = (
        DetailRow(label="能力", value=_capability_label(str(row["capability"]))),
        DetailRow(label="模型", value=str(row["model"])),
        DetailRow(label="为哪一章", value=_chapter_text(chapter)),
        *_summary_rows(row),
        DetailRow(label="入参 token", value=_num(row["tokens_in"])),
        DetailRow(label="出参 token", value=_num(row["tokens_out"])),
        DetailRow(
            label="接着上次的输入",
            value=_cache_text(_int(row["cache_read_tokens"]), _int(row["cache_write_tokens"])),
        ),
        DetailRow(label="耗时", value=_UNRECORDED if row["ms"] is None else f"{row['ms']} ms"),
        DetailRow(label="第几次尝试", value=str(row["attempt"])),
    )
    return ActivityDetail(
        entry=_call_entry(row),
        rows=rows,
        cost=ActivityCost(
            call_id=str(row["id"]),
            capability=str(row["capability"]),
            model=str(row["model"]),
            tokens_in=_int(row["tokens_in"]),
            tokens_out=_int(row["tokens_out"]),
            ms=_int(row["ms"]),
            cost=None if row["cost"] is None else float(row["cost"]),
        ),
    )


def _decision_detail(conn: Connection, project_id: str, entry_id: str) -> ActivityDetail | None:
    decision = decisions.read_one(conn, project_id, entry_id)
    if decision is None:
        return None
    rows = (
        DetailRow(label="类型", value=_kind_label(decision.kind)),
        DetailRow(label="裁决", value=_verdict_label(decision.decision.value)),
        DetailRow(label="谁改的", value=actor_label(decision.actor)),
        DetailRow(label="对象", value=decision.subject_name or _UNRECORDED),
        DetailRow(label="章节", value=_chapter_text(decision.chapter_number)),
        DetailRow(label="段落", value=_num(decision.para_index)),
        DetailRow(label="依据引语", value=decision.quote_text or _UNRECORDED),
    )
    return ActivityDetail(
        entry=_decision_entry(decision),
        rows=rows,
        payload=narrow_payload(decision.payload),
    )


_DETAIL_BY_PREFIX: Final = {
    "extraction_run": _run_detail,
    "call": _call_detail,
    "decision": _decision_detail,
}


def read_entry(conn: Connection, project_id: str, entry_id: str) -> ActivityDetail | None:
    """一条日志行的展开详情。查无此条（或跨项目）返 None，由壳翻成 404。

    分派靠 id 前缀（`ids.EntityType` 的第一段），**不是挨张表试一遍**：试一遍的话，
    一个拼错的 id 会在三张表上各扫一次才认输，而且哪一张表命中都是「对的」——
    前缀里已经写着它是什么了。
    """
    prefix = entry_id.split(":", 1)[0]
    reader = _DETAIL_BY_PREFIX.get(prefix)
    if reader is None:
        return None
    return reader(conn, project_id, entry_id)
