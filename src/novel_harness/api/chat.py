"""写作助手（模式二）的 HTTP 壳 —— **`agent/` 这一层的唯一调用方**。

规格书是 [ADR 0019](../../docs/adr/0019-agent-loop-not-graph.md)。引擎侧四个文件
（`ports` / `index` / `tools` / `loop`）在这之前**在 `src/` 里一个调用方都没有**——
这个仓库栽过四次「能力建好了、最后一厘米没接」（`docs_dev/2026-08-06-…`），
这个文件就是那根线。

六条路由：开一段 / 列出来 / 看一段 / 删一段 / **跑一轮** / **停**。

── 三件这一层必须自己做对的事 ────────────────────────────────────────────

1. **`working_chapter` 不是可选项。** `project()` 在 `chapter is None` 时**不过滤**，
   而那不是安全默认值，是「没接线」默认值——正是 fail-open 的那一侧（第 90 章的
   `must_not_reveal` 是第 40 章那份的子集，留着它模型就以为只有两条不能说）。
   所以 `TurnBody.chapter` 是**必填**，没有默认值。它是查询坐标（AS OF），
   **不写进任何一行数据**（约束 10：作者永不填章号作为声明坐标；`ToolContext` 上
   根本没有写路径）。

2. **账必须真的落到日志页上。** `run_turn` 产出 `ModelCallReceipt`，它的字段是照着
   `record_call` 的签名长的——所以这里是一次**平移**，不是一次翻译。
   （翻译的地方就是能悄悄漏字段的地方，而漏掉的那个字段会让日志页少算一笔钱。
   `tests/test_chat_api.py` 有一条按字段名对签名的断言钉着这句话。）
   验收不是「`model_call` 里有行」，是 **`GET /activity` 里看得见**：
   `activity._CAPABILITY_LABEL` 里那条 `agent → 写作助手`。

3. **对话的原文不是这条路由的出参。** 出去的是一份**投影**：作者说的话 + 助手说的话。
   工具返回一条都不出去——它们是 `model_dump_json()` 出来的内部模型，里面躺着
   `NodeRef` 的裸 id（`secret:…:01J…`）。那种东西一旦被前端原样渲染就是屏幕上的
   研发术语（`frontend/src/test/screenGuard.ts` 的第三张网认的就是 `前缀:标识`），
   而**收窄的最强形态是根本没发出去**（同 `activity.py` 那次把 `params_json` 从
   SELECT 里删掉）。

── 边界三：草稿存哪儿 ────────────────────────────────────────────────────

`chat_session` / `chat_message` 里**没有一列是正文的落点**，也没有任何读路径拿它们
回答「第 N 章是什么」——正文的真相源在磁盘上（ADR 0007），版本锚在 `chapter_snapshot`。
一稿还没被作者接受的草稿就是**助手说过的一段话**，它留在对话里；接受它 = 写进磁盘，
走既有的 `PUT …/chapters/{n}/text`（`chapter_snapshot` 的 `UNIQUE (chapter_id,
text_sha256)` 让那一步天然幂等）。**反过来是错的**：把没被接受的草稿写进
`chapter_snapshot`，它当场出现在版本抽屉里，「哪一份正文是真的」就有了第二个答案，
而且是作者在界面上看得见的那一个。
"""

from __future__ import annotations

from threading import Lock
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agent.loop import (
    AgentMessage,
    Cancellation,
    Conversation,
    LedgerFn,
    ModelCallReceipt,
    ModelPort,
    PersistFn,
    Projection,
    Role,
    StopReason,
    TurnLimits,
    run_turn,
    stop_wording,
)
from ..agent.model import ProviderModelPort, agent_call_plan
from ..agent.ports import ToolContext
from ..agent.store import ChatConcurrency, ChatSessionRow, ChatStore, StoredChat
from ..db import Connection
from ..draft.capabilities import CapabilityError, ProviderCapabilities, ResolvedCallPlan
from ..draft.provider import ProviderConfig
from ..draft.rolling_summary import SummaryStore
from ..extract.call_audit import record_call
from ..graph.sqlite_events import SqliteEventStore
from ..graph.store import StoryGraph
from ..ids import EntityType, new_id
from .deps import agent_provider_config, get_conn, get_store, load_project, model_configuration_error


router = APIRouter()

ChatId = Annotated[str, Path(min_length=1)]

TITLE_FROM_FIRST_SAID = 24
"""会话还没有名字时，拿作者第一句话的前多少个字当标题。

**只在标题为空时做一次**，之后作者改了就是他的。不做的话侧栏上是一排「新的对话」，
而作者要找的是三个月前那一段——那时唯一能认出它的东西就是他当时说的第一句话。
"""


class ChatBusy(RuntimeError):
    """这段对话正在跑一轮。"""


class _Running:
    """**正在跑的那几轮**，进程内。键是 `(项目, 会话)`。

    存在的理由只有一个：`POST …/stop` 是**另一个请求**，它要够得着正在跑的那一轮手里
    那个 `Cancellation`。跨进程不需要——重启之后没有任何一轮在跑，而库里那段对话
    照旧读得回来（resume 靠的是「缺哪几个 `tool_result`」，不是一个 running 标志位）。

    **不落库**也是有意的：一个存在库里的 `status='RUNNING'` 会在进程崩掉之后永远
    卡在那儿，而清它需要一个没人会写的看门狗。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._live: dict[tuple[str, str], Cancellation] = {}

    def begin(self, key: tuple[str, str]) -> Cancellation:
        with self._lock:
            if key in self._live:
                raise ChatBusy("这段对话正在跑上一轮")
            signal = Cancellation()
            self._live[key] = signal
            return signal

    def end(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._live.pop(key, None)

    def stop(self, key: tuple[str, str]) -> bool:
        with self._lock:
            signal = self._live.get(key)
        if signal is None:
            return False
        signal.stop()
        return True

    def running(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return key in self._live

    def clear(self) -> None:
        with self._lock:
            self._live.clear()


LIVE = _Running()
"""模块级单例（同 `api/autopilot.py::JOBS`）。测试用 `LIVE.clear()` 隔离。"""


# ══════════════════════════════════════════════════════════════════════════
# 出参：**对话的投影，不是对话的原文**
# ══════════════════════════════════════════════════════════════════════════


class ChatMessageView(BaseModel):
    """作者在屏幕上看得见的一条。

    **只有两种说话人。** 工具返回和「只叫工具没说话」的那几条不在这里——见模块
    docstring 第三条。少掉的那部分不是被藏起来了：它有一个数（`TurnReceipt.lookups`），
    而**零必须带着理由**这条规矩在这儿的形态是「查了几次说得出来，查到了什么不上屏」。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    """在这段对话历史里的位置。前端拿它当 key —— **不要用它当业务标识**。"""

    speaker: Literal["author", "assistant"]
    text: str


class ChatSessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str = ""
    created_at: str
    updated_at: str
    message_count: int = Field(ge=0)
    """这段对话历史里有多少条（含不上屏的那些）。"""

    running: bool = False
    """这一刻正在跑一轮。**进程内事实**，重启后恒为 `false`。"""

    pending_lookups: int = Field(default=0, ge=0)
    """上一轮**断在半路**、还缺结果的那几步（`Conversation.pending_calls`）。

    不为零 = 进程死在了模型调用和派发之间。下一轮会先把它们补跑掉（ADR 0019 的
    resume：看尾巴、补跑缺的、继续），所以这儿不需要一个「恢复」按钮。
    """


class ChatDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ChatSessionView
    messages: tuple[ChatMessageView, ...] = ()


class ContextReceipt(BaseModel):
    """这一轮发给模型的那份上下文**裁掉了什么**（`agent.loop.Projection` 的回执）。

    静默截断读起来像「全给了」。这份回执存在的理由和 `/check` 的 `rules_run`、
    起草回执的 `memory` 是同一条：**零必须带着理由一起出现**（ARCHITECTURE §10 约束 8）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    off_chapter: int = 0
    """因为绑在**更后面**的章上而没进这一份的查询结果条数（ADR 0019 边界五）。"""

    stale_lookups: int = 0
    """助手手上那份正文**已经被作者改掉**、这一轮换成「重新读一次」的条数（边界三）。

    不为零 = 它上一轮读到的那一章和磁盘上那份对不上了。**不说的话作者永远不知道**
    助手手里曾经拿着一份旧稿——而那正是「哪一份正文是真的」有第二个答案的形态。
    """

    trimmed_results: int = 0
    dropped_lookups: int = 0
    dropped_reasoning: int = 0
    lost_lookups: int = 0
    full: bool = False
    """剪到只剩作者说过的话仍然装不下。**这一档不砍作者的话**，这一轮直接停。"""


class TurnReceipt(BaseModel):
    """跑完一轮的回执。**`TurnResult.maintainer_note` 不在这儿，一个字都不出去。**

    那是写给维护者的 `ProviderError` 原文（里面有端点地址和模型名），同
    `ExtractionRunError.message`——作者读不懂它，而且他能做的动作是去顶栏改设置，
    不是读一条英文诊断。作者看到的只有 `message`（`stop_wording()` 那一句）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ChatSessionView
    chapter: int = Field(ge=1)
    reason: StopReason
    message: str
    """说给作者的那一句。**措辞唯一出处是 `agent.loop.stop_wording()`**，前端不许再翻一遍。"""

    reply: str = ""
    messages: tuple[ChatMessageView, ...] = ()
    """这一轮新长出来的、上得了屏的那几条。"""

    steps: int = Field(default=0, ge=0)
    lookups: int = Field(default=0, ge=0)
    """这一轮查了几次（工具调用次数）。"""

    tokens_reported: int = Field(default=0, ge=0)
    calls_without_usage: int = Field(default=0, ge=0)
    """有几次调用没量准。**不为零时上面那个数是低估**，界面上不许把它当全部。"""

    context: ContextReceipt = ContextReceipt()


class ChatStopped(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    stopped: bool
    """`false` = 这一刻它本来就没在跑（不是失败）。"""

    message: str


class ChatDeleted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: str
    deleted: bool


# ══════════════════════════════════════════════════════════════════════════
# 入参
# ══════════════════════════════════════════════════════════════════════════


class NewChat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    house_style: str = ""
    """作者的文风偏好。**进稳定前缀，所以它必须跨章不变**（ADR 0019 边界六）。

    它是自由文本，这一层看不出作者有没有把一条禁令写进去——判它要回答「这句话是不是
    把伏笔说破了」，那是语义判断（ADR 0005 在 v1 里禁止本仓库长出这种能力）。
    """


class TurnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    """作者此刻在写第几章。**必填，没有默认值**——见模块 docstring 第一条。"""

    said: str = ""
    """作者这一轮说的话。**留空 = 接着上次往下跑**（resume，或者上一轮撞了闸之后继续）。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


def build_agent_model(config: ProviderConfig, plan: ResolvedCallPlan) -> ModelPort:
    """造这一轮的模型端口。**测试替换的就是这一个函数**（同 `deps` 里那几个注入点）。"""
    return ProviderModelPort(config, plan)


def _ledger(conn: Connection, project_id: str) -> LedgerFn:
    """把一份账单原料落成一行 `model_call`。**逐字段平移，不翻译。**

    `record_call` 自己开 `BEGIN IMMEDIATE` 但**不 commit**（调用方负责自己的业务表），
    所以这里补上提交。**失败不吞**：账记不上的那一次调用已经花过钱了，
    而一条静默失败的记账就是日志页第二次骗人。
    """

    def record(receipt: ModelCallReceipt) -> None:
        try:
            record_call(
                conn,
                project_id=project_id,
                capability=receipt.capability,
                model=receipt.model,
                finish_reason=receipt.finish_reason,
                schema_version=receipt.schema_version,
                prompt_hash=receipt.prompt_hash,
                prompt_bytes=receipt.prompt_bytes,
                text=receipt.text,
                prompt_tokens=receipt.prompt_tokens,
                completion_tokens=receipt.completion_tokens,
                elapsed_ms=receipt.elapsed_ms,
                call_id_factory=lambda pid: new_id(EntityType.CALL, pid),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()

    return record


def _tool_context(
    proj: Any,
    store: StoryGraph,
    conn: Connection,
    *,
    chapter: int,
    max_context_tokens: int | None,
    reserved_output_tokens: int,
) -> ToolContext:
    """这一轮里模型碰得到的**全部**东西（`agent/ports.py` 是那一页的规格）。

    `drafter` 仍然是 `None`，**这是显式的缺席不是遗漏**：把「章号 → 一稿正文」抄进这儿
    等于第二条会漂的起草路径（`agent/tools.py` 写着那条理由），而正确的顺序是先把
    `api/app.py::draft` 那段提成 `draft/` 里的一个函数。工具在没接线时会明说自己没接线，
    不假装写了一稿。
    """
    return ToolContext(
        store=store,
        project_id=proj.id,
        root_path=proj.root_path,
        drafter=None,
        summaries=SummaryStore(conn),
        events=SqliteEventStore(conn),
        working_chapter=chapter,
        max_context_tokens=max_context_tokens,
        reserved_output_tokens=reserved_output_tokens,
    )


def _visible(messages: tuple[AgentMessage, ...], first_seq: int) -> tuple[ChatMessageView, ...]:
    """canonical → 屏幕上那几条。工具返回和「只叫工具没说话」的那些不出去。"""
    out: list[ChatMessageView] = []
    for offset, message in enumerate(messages):
        if message.role is Role.USER:
            speaker: Literal["author", "assistant"] = "author"
        elif message.role is Role.ASSISTANT and message.content.strip():
            speaker = "assistant"
        else:
            continue
        out.append(
            ChatMessageView(seq=first_seq + offset, speaker=speaker, text=message.content)
        )
    return tuple(out)


def _session_view(
    session: ChatSessionRow, conversation: Conversation, history_count: int, *, running: bool
) -> ChatSessionView:
    return ChatSessionView(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=history_count,
        running=running,
        pending_lookups=len(conversation.pending_calls),
    )


def _load(conn: Connection, project_id: str, chat_id: str) -> StoredChat:
    stored = ChatStore(conn).load(project_id, chat_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    return stored


def _plan_or_422() -> tuple[ProviderConfig, ProviderCapabilities, ResolvedCallPlan]:
    """模型配好了没 + 这一档它撑不撑得起。**两个问题两句话**，别合并。"""
    reason = model_configuration_error()
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    config = agent_provider_config()
    try:
        capability, plan = agent_call_plan(config)
    except (CapabilityError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"这个模型跑不了写作助手：{exc} —— 去顶栏 ⚙「AI 设置」换一个模型或服务地址。"
            ),
        )
    return config, capability, plan


# ══════════════════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════════════════


@router.post("/api/projects/{project_id}/chats", response_model=ChatSessionView, status_code=201)
def create_chat(
    body: NewChat,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatSessionView:
    """开一段新的对话。**作者可以同时开好几段**，每一段各自 resume。"""
    store = ChatStore(conn)
    session = store.create(proj.id, title=body.title, house_style=body.house_style or None)
    stored = store.load(proj.id, session.id)
    assert stored is not None  # 刚建好
    return _session_view(session, stored.conversation, 0, running=False)


@router.get("/api/projects/{project_id}/chats", response_model=list[ChatSessionView])
def list_chats(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> list[ChatSessionView]:
    """侧栏那一列，最近说过话的在前。

    **不逐条读消息**：列表页要的是「有哪几段」，把每段的历史都拉出来只为了数一个数，
    在一段几百条的对话上就是几百行的白读。所以两个数各走一次聚合查询。

    **`pending_lookups` 必须真的算**，不能让它吃默认值 0：那个字段说的是「上一轮断在
    半路」，而**这一页是作者唯一一次看得见全部会话的地方**——它恒为 0 的话，断在半路
    的那一段和跑完了的那一段在侧栏上长得一模一样，而两者的下一步动作不同。
    一个只在某一个读端说真话的字段，比没有这个字段更坏。
    """
    store = ChatStore(conn)
    counts = store.history_counts(proj.id)
    pending = store.pending_counts(proj.id)
    return [
        ChatSessionView(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=counts.get(session.id, 0),
            running=LIVE.running((proj.id, session.id)),
            pending_lookups=pending.get(session.id, 0),
        )
        for session in store.list(proj.id, limit=limit)
    ]


@router.get("/api/projects/{project_id}/chats/{chat_id}", response_model=ChatDetail)
def read_chat(
    chat_id: ChatId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatDetail:
    """看一段对话。404 = 这一段不在（或不属于这本书），和 501 分得开。"""
    stored = _load(conn, proj.id, chat_id)
    return ChatDetail(
        session=_session_view(
            stored.session,
            stored.conversation,
            stored.history_count,
            running=LIVE.running((proj.id, chat_id)),
        ),
        messages=_visible(stored.conversation.messages, 0),
    )


@router.delete("/api/projects/{project_id}/chats/{chat_id}", response_model=ChatDeleted)
def delete_chat(
    chat_id: ChatId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatDeleted:
    """删掉一段对话。**这不会动到任何一行正文**——正文在磁盘上，这两张表里没有它。

    正在跑的那一段**先拒掉**：删了它，那一轮跑完想把产物追加回去时会撞上一个不存在的
    会话，作者看到的是一句「在别的窗口里刚往前走了一步」——**一句和他刚做的事完全对不上
    的话**。先说「它正在跑」是准的。
    """
    if LIVE.running((proj.id, chat_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_busy",
                "chat_id": chat_id,
                "message": "这段对话正在跑，先按「停」再删。",
            },
        )
    if not ChatStore(conn).delete(proj.id, chat_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    return ChatDeleted(chat_id=chat_id, deleted=True)


@router.post("/api/projects/{project_id}/chats/{chat_id}/turn", response_model=TurnReceipt)
def run_chat(
    chat_id: ChatId,
    body: TurnBody,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
    store: StoryGraph = Depends(get_store),
) -> TurnReceipt:
    """跑一轮：模型说话、叫工具，直到它收手或者代码把它停下来（九种停法）。

    **这一版 HTTP 不流式**（内部是流式的，打断才能中途生效）。往浏览器流是 3.5 的决定，
    这儿不替它定。

    `said` 留空 = 接着上次往下跑。那就是 ADR 0019 的 resume：**看尾巴、补跑缺的、继续**
    （缺哪几个由 `Conversation.pending_calls` 回答，这里不写第二份）。

    **产物是边跑边落库的，不是跑完再整批落。** 「跑完再落」看起来更干净，但它让上面那句
    resume 变成一句空话：进程死在中途 ⇒ 这一轮一条都没进库 ⇒ 尾巴上没有那条带
    `tool_calls` 的 assistant ⇒ `pending_calls` 恒为空 ⇒ 没有任何东西可补，而那几次模型
    调用的钱**已经记在账上了**。所以 `run_turn` 收一个 `persist`（`agent/loop.py`），
    每长出一条就落一次。
    """
    config, capability, plan = _plan_or_422()
    chat_store = ChatStore(conn)
    key = (proj.id, chat_id)

    # **先占位，再读、再写。** 顺序反过来会有一个实测过的真故障：第二个窗口在第一轮
    # 还在跑的时候先把作者那句新话追加进去，于是第一轮跑完想追加自己的产物时撞上
    # 乐观并发闸——**先按发送的那一个被判失败，后按的那一个反而通过**。作者会看到
    # 「刷新一下再说」，而他什么都没做错。占住之后第二个窗口拿到的是「正在跑上一轮」，
    # 那句话是对的，而且它在写库之前就发生了。
    try:
        signal = LIVE.begin(key)
    except ChatBusy:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_busy",
                "chat_id": chat_id,
                "message": "这段对话正在跑上一轮，等它停下来，或者按「停」。",
            },
        )
    try:
        stored = _load(conn, proj.id, chat_id)
        conversation = stored.conversation
        history_count = stored.history_count
        said = body.said.strip()
        if said:
            conversation = conversation.with_author(said)
            # **作者那句话先落库**：这一轮后面任何一步炸掉（记账写不进去、库锁住），
            # 他说过的话都还在，下一轮接着往下走就是。
            history_count = _append(
                chat_store, proj.id, chat_id, history_count, conversation.messages[history_count:]
            )
            if not stored.session.title.strip():
                chat_store.rename(proj.id, chat_id, said[:TITLE_FROM_FIRST_SAID])
        elif not conversation.messages:
            raise HTTPException(
                status_code=422,
                detail="这段对话还没开始——先说一句你想让它做什么。",
            )

        # 这一轮开始时历史有多长。**出参那几条按它切**，不能按 `history_count` 切——
        # 后者会被下面那个落库回调一路推着走，切出来的就只剩最后一两条。
        turn_start = history_count

        def save(live: Conversation) -> None:
            """一轮跑到一半时把已经长出来的消息落库（`agent.loop.PersistFn`）。

            **走的是同一个 `append`**，只是不再等到最后才走一次：`chat_message` 上没有
            第二种写法，也不该有——「跑到哪一步」那样一列会造出第二份执行态。
            """
            nonlocal history_count
            history_count = _append(
                chat_store, proj.id, chat_id, history_count, live.messages[history_count:]
            )

        keep: PersistFn = save
        result = run_turn(
            conversation,
            context=_tool_context(
                proj,
                store,
                conn,
                chapter=body.chapter,
                max_context_tokens=capability.max_context_tokens,
                reserved_output_tokens=plan.request_token_budget,
            ),
            model=build_agent_model(config, plan),
            ledger=_ledger(conn, proj.id),
            limits=TurnLimits(),
            cancel=signal,
            persist=keep,
        )
        save(result.conversation)
        fresh = result.conversation.messages[turn_start:]
    finally:
        LIVE.end(key)

    session = chat_store.get(proj.id, chat_id)
    assert session is not None  # 上面刚读过它

    return TurnReceipt(
        session=_session_view(session, result.conversation, history_count, running=False),
        chapter=body.chapter,
        reason=result.reason,
        message=result.said_to_author,
        reply=result.reply,
        messages=_visible(fresh, turn_start),
        steps=result.steps,
        lookups=result.tool_calls,
        tokens_reported=result.tokens_reported,
        calls_without_usage=result.calls_without_usage,
        context=_context_receipt(result.projection),
    )


@router.post("/api/projects/{project_id}/chats/{chat_id}/stop", response_model=ChatStopped)
def stop_chat(
    chat_id: ChatId,
    proj: Any = Depends(load_project),
    conn: Connection = Depends(get_conn),
) -> ChatStopped:
    """按下「停」。**它不等这一轮跑完**——信号交给正在跑的那一轮，跑一轮的那个请求
    会以「按你的意思停下了」收尾，已经查到的东西留着。

    `stopped=false` 不是失败：那一刻它本来就没在跑（跑完了、或者从来没开始）。
    """
    if ChatStore(conn).get(proj.id, chat_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "chat_not_found", "chat_id": chat_id},
        )
    stopped = LIVE.stop((proj.id, chat_id))
    return ChatStopped(
        chat_id=chat_id,
        stopped=stopped,
        message=(
            stop_wording(StopReason.AUTHOR_STOPPED)
            if stopped
            else "这段对话这会儿没在跑，不用停。"
        ),
    )


def _append(
    store: ChatStore,
    project_id: str,
    chat_id: str,
    base_count: int,
    messages: tuple[AgentMessage, ...],
) -> int:
    try:
        return store.append(project_id, chat_id, base_count=base_count, messages=messages)
    except ChatConcurrency as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "chat_conflict",
                "chat_id": chat_id,
                "message": f"这段对话在别的窗口里刚往前走了一步，刷新一下再说。（{exc}）",
            },
        )


def _context_receipt(projection: Projection | None) -> ContextReceipt:
    if projection is None:
        return ContextReceipt()
    return ContextReceipt(
        off_chapter=projection.off_chapter,
        stale_lookups=projection.stale_manuscript,
        trimmed_results=projection.stubbed_results,
        dropped_lookups=projection.dropped_calls,
        dropped_reasoning=projection.dropped_reasoning,
        lost_lookups=projection.filled_shells,
        full=projection.over_budget,
    )


__all__ = ["LIVE", "build_agent_model", "router"]
