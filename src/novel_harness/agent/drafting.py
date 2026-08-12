"""`DraftDesk` 的实现 —— **生成一稿（不动书）/ 把某一稿写进那一章（动书）**
（[ADR 0022](../../../docs/adr/0022-drafting-is-a-proposal-not-a-write.md)，机制推翻了
[ADR 0021](../../../docs/adr/0021-agent-writes-drafts-without-asking.md)，**精神原样保留**）。

`tools.py` 那三条工具只做两件事：算这一章的约束（边界二），然后把活儿交给注入进来的这个
端口。**「章号 → 一稿正文」的实现不在这儿**——它在 `draft/product_draft.py`，`/draft`
那条 HTTP 路由调的是同一个函数。这一层加的是四件事：长度档、**候选表**、落盘、留痕。

── 拆成两个动作之后，各自守什么 ──────────────────────────────────────────

| 动作 | 花钱 | 动书 | 它守的东西 |
|---|---|---|---|
| `write` | 花 | **不动** | 自述从写手那儿要（同一次调用白送）、正文进候选表不进对话 |
| `land` | 不花 | 动 | ADR 0021 那五条（sha 闸 / 只对已存在的章 / 先 `sync` / 留章标题 + 验「恰好一章」/ 空稿闸） |

**拆开之后 sha 闸守的东西变了，但它一个字都没松**：起草那一刻磁盘上那一份的哈希
跟着候选一起存进表（`draft_candidate.base_sha256`），落盘时拿它比对当前值。
不存的话，闸就只能拿「现在」跟「现在」比，也就是**永远放行**——而它守的是
「作者比它更晚改过的那一章」，放行的代价是作者的字被盖掉且**从没进过快照就是真没了**。

顺带一条这次拆分**新长出来的**闸：起草那会儿这一章还不存在（`base_sha256 IS NULL`），
而落盘时它存在了——那是作者在这几十秒里自己建的章，`expected_sha256=None` 会把闸关掉、
把他刚起的那一章盖掉。所以那一档**一律拒**（见 `_land`）。

── 为什么它在 `agent/` 而不在 `draft/` ────────────────────────────────────

`draft/product_draft.py` 的 docstring 写着「**它也不落盘**」，那不是分工洁癖：
行内续写（ADR 0015）按定义不落盘，`/draft` 从浏览器发起时作者眼前就是编辑器，
**只有模式二这条路要求写进磁盘**。把落盘塞进那一层等于给另外两个调用方一个
它们没要过的副作用。

反过来它也不在 `tools.py`：写盘要 `GraphStore`（带写入面）和一条连接，而
`ToolContext` 上**只有 `StoryGraph`**——「模型改不了作者的 canon」是一条类型保证
（边界一），为了落盘把写入面塞回那个 dataclass 就是用一次功能换掉那条保证。
所以写入面握在**这个闭包**手里：`tools.py` 那一层拿到的只有一个 `DraftDesk`。

一条推论，写在这儿免得下一个人当成遗漏：**模型没有「只写不草」这个动作**。
它不能拿一段自己编的文本去盖某一章——`land` 只收一个候选 id，而候选只能由
后端按当前章约束生成出来。
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Final

from .. import decisions, importer
from ..db import Connection
from ..draft.capabilities import (
    CapabilityError,
    ProviderCapabilities,
    ReasoningEffort,
    plan_call,
)
from ..draft.context import DraftContext
from ..draft.length import DEFAULT_LENGTH_POLICY, DraftLanguage, LengthSpec, count_units
from ..draft.product_draft import (
    ChapterDraftRequest,
    DraftRefused,
    SummarySource,
    draft_chapter,
)
from ..draft.provider import ProviderConfig, ProviderError
from ..events import EventStore
from ..extract.call_audit import ModelCallReceipt
from ..graph import GraphStore
from .candidates import DraftCandidate, DraftCandidateStore, StoredDraft
from .ports import DraftAsk, DraftProduct, LandingReport, ToolRefused

AGENT_DRAFT_LENGTH: Final[LengthSpec] = DEFAULT_LENGTH_POLICY.default_for(DraftLanguage.ZH)
"""写作助手起草一章用哪一档长度：**ADR 0011 D1 那张表里的产品默认档**（中文 2,000 /
2,500 / 3,000 字），不是新发明的一档。

三条都是硬的：

1. **`DraftAsk` 上没有 `length`，而且不许加。** 长度是**作者**的意愿
   （[ADR 0013](../../../docs/adr/0013-draft-length-is-a-request-parameter.md)：
   「作者是唯一知道这场该写多长的人」），不是模型可以主张的东西——给模型一个长度旋钮，
   它就能在一次工具调用里把作者的一章写成八千字，而作者从没要求过。
   工具入参里那条「唯一的时间坐标是 AS OF」是同一条纪律的另一半
   （`tests/test_agent_tools.py::QUERY_COORDINATES`）。
2. **不读 `LengthPolicy.from_env()`。** 那八个环境变量是**部署者**的旋钮（ADR 0011 D1），
   而 BYOK 的桌面作者没有环境变量——同 `_draft_provider_config` 那条「钥匙在设置页里」。
   今天走产品默认档，作者要改这一档时该长出来的是界面上的一个控件（同
   `DraftLengthControls`），不是这儿偷偷读一个他看不见的变量。
3. **它不是 `M2_LENGTH_SPEC`。** 那一档（上限 3,100）是考卷的定义，冻结在
   EVAL_PROTOCOL；产品这条路继承它就是拿考卷的参数跑产品，反过来改它就是改考卷。
"""

AGENT_DRAFT_REASONING: Final = ReasoningEffort.OFF
"""起草这一次调用的 reasoning 档。**`OFF` 而不是 `/draft` 用的 `HIGH`。**

ADR 0011 D4 的原文是「产品 reasoning 默认 `off`；M2 请求 `high`」，而这条路上还有
一个更硬的理由，和 `agent/model.py::AGENT_REASONING` 完全同源：
`resolve_capabilities` 对**没登记的模型**只给 `reasoning_levels={OFF}`，所以 `HIGH`
在作者自建的端点上是当场 `CapabilityError`。那会长成一种很难查的形态——
**聊天好好的，只有起草那一个工具每次都失败**。

（`/draft` 那条路仍然是 `HIGH`，那是它自己的既有行为，这一刀不动它。）
"""

SELF_NOTE_MARK: Final = "〖自述〗"
"""那一稿的自述，写在正文**第一行**、用这个记号开头。

── 为什么自述由**写它的那个模型**给（ADR 0022）──────────────────────────

起草是工具内部**另起的一次**模型调用；对话里那个模型**没看过正文**——它要说
「我推荐第二版，因为更冷」就得先把 9,000 字读进上下文，也就是把 ADR 0022 省下来的
东西原样花回去。让写手在同一次调用里多交三十个字是白送的。

**引擎全程没给散文打过分**（ADR 0005 一个字没破）：这一层只负责把那一行切下来，
它不判断这一版好不好、也不在模型没写的时候替它编一句。

── 为什么在开头不在结尾 ──────────────────────────────────────────────────

答太短时会有第二次调用把续写**接在后面**（ADR 0011 D3），结尾那一行会被埋进正文中间。
开头这一行不会动。
"""

SELF_NOTE_UNITS: Final = 60
"""自述最多留多少字。**它每一轮都会跟着预览一起重发**，所以它也要有硬上限
（同 `PREVIEW_UNITS` 那条理由）。模型写长了就截断，不是拒绝——那是它的散文，不是参数。
"""

_SELF_NOTE_ASK: Final = (
    f"\n\n另外：**正文第一行**先写一句 `{SELF_NOTE_MARK}`，用不超过三十个字说这一版你是"
    "怎么处理的（视角、情绪、取舍、跟别的版本比有什么不同），然后空一行再开始写正文。"
    "那一行不是正文，不会进书里；作者要在几稿之间挑的时候看的就是它。"
)
"""附在作者那句 `goal` 后面的一句话。

**它加在 `goal` 上，而不是加进 `assemble()`**：那一层是三臂共用的
（`draft/assemble.py`，EVAL_PROTOCOL §2 冻结），动它就是改考卷；而 `/draft` 那条 HTTP
路由传的是它自己的 `goal`，逐字节不受影响。
"""


def _previous_tail(root: Path, chapter: int) -> str:
    """上一章的正文，整篇。**截多长由后端按模型窗口算**（`product_tail_limit`），
    这一层不留第二份长度常量——那正是 2026-08-10 从起草抽屉里清掉的病
    （前端一个 800、后端一个上万字）。

    第 1 章送空串，**不拿别的章冒充**。
    """
    if chapter <= 1:
        return ""
    return importer.read_chapter(root, chapter - 1) or ""


def split_self_note(text: str) -> tuple[str, str]:
    """把写手那句自述从正文里切下来。返回 `(正文, 自述)`。

    **认不出就一个字都不动**（返回原文 + 空自述）：模型没照做是常态，而
    「猜一句自述出来」比没有自述坏得多——作者会拿它当模型的判断去挑版本。

    ── 为什么是「每一处都切」，不是「切掉第一行」 ────────────────────────────

    答太短时会有第二次调用把续写**接在后面**（ADR 0011 D3），而那一次发出去的提示里
    **还带着同一句「先写一行自述」**——于是记号会第二次出现，而且多半不在行首
    （两段是直接拼起来的）。实测过：一稿变成
    「风雪落在肩上。〖自述〗：更冷。\\n\\n风雪落在肩上。」，照「只切第一行」处理的话
    那半句**会跟着正文进作者的书**。

    所以规则是：**从记号切到行尾，每一处都切**；第一处的内容当自述。
    这个记号是引擎自己发明的（`SELF_NOTE_MARK`），正文里天然不会有它——
    误伤的代价因此接近零，而漏一处的代价是作者的书里多一行机器话。
    """
    if SELF_NOTE_MARK not in text:
        return text, ""
    note = ""
    kept: list[str] = []
    for line in text.split("\n"):
        head, mark, tail = line.partition(SELF_NOTE_MARK)
        if not mark:
            kept.append(line)
            continue
        if not note:
            note = tail.strip().lstrip("：: ").strip()
        kept.append(head.rstrip())
    if len(note) > SELF_NOTE_UNITS:
        note = note[:SELF_NOTE_UNITS] + "……"
    return "\n".join(kept).strip("\n"), note


class ChapterDesk:
    """起草那一摊的实现（`agent.ports.DraftDesk`）。**装配层造它**（`api/chat.py`）。

    它握着写入面（`GraphStore` + 一条连接 + 候选表），而 `ToolContext` 上只有
    `StoryGraph`——「模型改不了作者的 canon」因此仍然是类型保证，不是纪律（边界一）。

    **它记得这一轮产出过哪几稿**（`produced`）。那不是缓存，是给 HTTP 壳的回执：
    `POST …/turn` 要把这一轮生成的候选摆到界面上，而 loop 只认识 `ToolOutcome`
    ——让壳去翻工具返回的 JSON 就是第二处解析点。
    """

    def __init__(
        self,
        *,
        store: GraphStore,
        conn: Connection,
        project_id: str,
        root: str | Path,
        config: ProviderConfig,
        capability: ProviderCapabilities,
        events: EventStore,
        summaries: SummarySource,
        length: LengthSpec = AGENT_DRAFT_LENGTH,
        db_lock: AbstractContextManager[Any] | None = None,
    ) -> None:
        self._store = store
        self._conn = conn
        self._project_id = project_id
        self._root = Path(root)
        self._config = config
        self._capability = capability
        self._events = events
        self._summaries = summaries
        self._length = length
        # **碰库要排的那道队**，和 `ToolContext.db_lock` 必须是**同一把**
        # （见 `agent/ports.py` 上那段实测）。装配层不给就自己造一把——那是
        # 「这一轮不并发」的形态，锁本身几乎不要钱。
        self._db_lock: AbstractContextManager[Any] = db_lock or threading.RLock()
        self._candidates = DraftCandidateStore(conn, lock=self._db_lock)
        self.produced: list[DraftCandidate] = []
        """这一轮生成出来的那几稿，**按生成顺序**。落盘之后那一份的 `landed` 会被换掉。"""

    # ── 生成：花钱，不动书 ────────────────────────────────────────────────

    def write(self, ask: DraftAsk, ctx: DraftContext) -> DraftProduct:
        """生成一稿，收进候选表。**书一个字节都不动**（ADR 0022）。

        Returns:
            `DraftProduct`：候选的摘要行 + 这一稿花掉的那几笔。**无论如何都带着回执**
            ——`ToolRefused` 那条路也带（见 `ToolRefused.calls`），否则那笔已经付掉的钱
            会连同 `ToolOutcome.calls` 一起消失，而成本闸和日志页同时失明。

        Raises:
            ToolRefused: 模型撑不起一次整章起草 / 发出去之前就被拒 / 联系不上模型 /
                写手交回来的是空的。
        """
        # **`plan_call` 在这儿算，不在构造函数里算。** 放在构造里的话，一个撑不起整章
        # 起草预算的模型会让 `POST …/turn` 整个 422——聊天本来是能用的，作者只会看到
        # 「写作助手用不了」。放在这儿，坏的只有起草这一个工具，而它说得出原因。
        try:
            plan = plan_call(self._length, AGENT_DRAFT_REASONING, self._capability)
        except (CapabilityError, ValueError) as exc:
            raise ToolRefused(
                f"这个模型撑不起一次整章起草（{exc}）。"
                "让作者去顶栏「AI 设置」换一个上下文更大的模型，或者他自己在编辑器里写。"
            ) from exc

        # **起草之前先记下它依据的是哪一份**（ADR 0021 那道乐观闸的另一半在
        # `importer.save_chapter`）。顺序不能反：模型调用要跑几十秒，作者就在旁边打字。
        # 拆成两个动作之后这个哈希要**跟着候选进表**——落盘可能发生在好几轮之后。
        current = importer.read_chapter(self._root, ask.chapter)
        base_sha = None if current is None else importer.text_digest(current)

        request = ChapterDraftRequest(
            goal=ask.goal + _SELF_NOTE_ASK,
            length=self._length,
            previous_tail=_previous_tail(self._root, ask.chapter),
        )
        # **逐次收回执，不等整份出参。** 整章起草是一到两次调用（生成 + 至多一次续写，
        # ADR 0011 D3），而「第一次答上来了、续写那次断线」是真会发生的一档——那时
        # `ChapterDraft` 永远拼不出来，出参上那份 `calls` 也就永远交不出去，
        # 可钱已经付掉了。攒在这儿，抛的时候一起带走（`ToolRefused.calls`）。
        spent: list[ModelCallReceipt] = []
        try:
            drafted = draft_chapter(
                ctx,
                request=request,
                project_id=self._project_id,
                config=self._config,
                capability=self._capability,
                plan=plan,
                events=self._events,
                summaries=self._summaries,
                on_call=spent.append,
                # 那一层自己只把**装配**（要查事件、查摘要）圈进锁里；
                # 后面那次模型调用在锁外面，所以一批稿是真的同时在飞。
                db_lock=self._db_lock,
            )
        except DraftRefused as exc:
            # 发出去之前就被拒了 ⇒ 一分钱没花 ⇒ 这一档可以裸抛（`dispatch` 变成 ok=False）。
            raise ToolRefused(str(exc)) from exc
        except ProviderError as exc:
            # **不许让它逃出 `dispatch`**：`run_turn` 外面没有 try/except，漏出去
            # 作者看到的是一次崩溃，而不是「这一稿没写成，再试一次」。
            raise ToolRefused(
                f"这一稿没写成，联系不上写作模型：{exc}", calls=tuple(spent)
            ) from exc

        body, note = split_self_note(drafted.result.text)
        if not body.strip():
            # 空返回是真会发生的一档（供应商 4xx 之外还有「模型什么都没说」，同
            # `StopReason.NO_OUTPUT`）。**收进表没有意义**：一稿空白既落不了盘，
            # 也不该占一个 id 让模型以为手上有东西。钱已经花了，所以带着回执拒。
            raise ToolRefused(
                f"第 {ask.chapter} 章这一稿是空的，写作模型什么都没写出来。"
                "换个说法再让我写一次。",
                calls=tuple(drafted.calls),
            )

        candidate = self._candidates.put(
            self._project_id,
            chapter=ask.chapter,
            body=body,
            note=note,
            base_sha256=base_sha,
        )
        self.produced.append(candidate)
        return DraftProduct(candidate=candidate, calls=drafted.calls)

    # ── 落盘：不花钱，动书，**仍然不问作者** ──────────────────────────────

    def land(self, candidate_id: str) -> LandingReport:
        """把某一稿写进它那一章（ADR 0021 的机制原样保留）。

        **「写没写成」的每一种结局都是返回值，不是异常**：模型据此决定下一句跟作者说
        什么，而「我写进去了」和「你刚改过，我没敢覆盖」是两句完全不同的话。

        **只有两件事仍然抛**：这一稿根本不在（模型报了个不存在的 id），
        和留痕失败（见 `_log_landing`）。
        """
        stored = self._require(candidate_id)
        landed, note = _land(
            self._store,
            self._conn,
            project_id=self._project_id,
            root=self._root,
            chapter=stored.chapter,
            base_sha=stored.base_sha256,
            body=stored.body,
        )
        if landed:
            self._candidates.mark_landed(self._project_id, candidate_id)
            self.produced = [
                c.model_copy(update={"landed": True}) if c.id == candidate_id else c
                for c in self.produced
            ]
        return LandingReport(chapter=stored.chapter, landed=landed, note=note)

    # ── 读回：不花钱，不动书 ──────────────────────────────────────────────

    def recall(self, candidate_id: str) -> StoredDraft:
        """按 id 把一稿的全文拿回来。**只在作者要合并两版时才会被调用**（ADR 0022）。"""
        return self._require(candidate_id)

    def _require(self, candidate_id: str) -> StoredDraft:
        stored = self._candidates.get(self._project_id, candidate_id)
        if stored is None:
            raise ToolRefused(
                "这本书里没有这一稿（编号对不上，或者它已经被清理掉了）。"
                "重新起一稿，或者让作者说清楚他要的是哪一版。"
            )
        return stored


def chapter_drafter(
    *,
    store: GraphStore,
    conn: Connection,
    project_id: str,
    root: str | Path,
    config: ProviderConfig,
    capability: ProviderCapabilities,
    events: EventStore,
    summaries: SummarySource,
    length: LengthSpec = AGENT_DRAFT_LENGTH,
    db_lock: AbstractContextManager[Any] | None = None,
) -> ChapterDesk:
    """造一个起草台（生成 / 落盘 / 读回）。**装配层调它**（`api/chat.py`）。

    Args:
        store / conn: 写入面和连接。**它们进的是这个对象，不是 `ToolContext`**
            （见模块 docstring）。
        root: 项目根目录。收 `str` 是为了和 `ToolContext.root_path` 同形——
            装配层手里那个就是 `project.root_path`。
        capability: 已经解析好的能力证据。`plan` 不在这儿算——见 `ChapterDesk.write`。
        db_lock: 碰库排的那道队。**要和 `ToolContext.db_lock` 是同一把**（批内并发，
            ADR 0022）：两把锁 = 各排各的队 = 没排。
    """
    return ChapterDesk(
        store=store,
        conn=conn,
        project_id=project_id,
        root=root,
        config=config,
        capability=capability,
        events=events,
        summaries=summaries,
        length=length,
        db_lock=db_lock,
    )


def _land(
    store: GraphStore,
    conn: Connection,
    *,
    project_id: str,
    root: Path,
    chapter: int,
    base_sha: str | None,
    body: str,
) -> tuple[bool, str]:
    """把这一稿写进第 `chapter` 章。返回 `(写没写成, 说给模型听的那句话)`。

    每一条不写的理由都要说得出口（见 `ChapterDesk.land`）。

    **只有一件事仍然抛：留痕失败。** 写完盘之后那一行 `decision_log` 记不上时，
    这儿不吞（同 `api/chat.py::_ledger` 那条「记账失败也不吞」）——ADR 0021 拿
    「不挡，但每步留痕」换掉了「事前问一句」，一次**写了作者的正文却没留下痕迹**的落盘
    正好把那笔交易的另一半赖掉了，而它静默失败的形态是「作者的书被改了，日志页上没有
    这一行」。响一声很吵，但吵在对的方向。
    """
    current = importer.read_chapter(root, chapter)
    if current is None:
        # ADR 0021 的范围限制，**这条 404 不许为 agent 放开**：新建一章要起章标题，
        # 而标题是切章的锚（切错了整本书章号会漂），`chapter_snapshot.chapter_id`
        # 也没有落点。所以交出正文，由作者建。
        return False, (
            f"第 {chapter} 章还不存在，所以这一稿没有存进去——新开一章要作者自己起章标题"
            "（书里靠那一行认章）。把稿子给他看，请他建好这一章再放进去。"
        )

    if base_sha is None:
        # **拆成两个动作之后新长出来的一档**：起草那会儿这一章不存在，现在它存在了
        # ——那是作者在这中间自己建的。`expected_sha256=None` 会把闸整个关掉，
        # 于是他刚起的那一章被一份**根本不是基于它写的**稿子盖掉。
        return False, (
            f"没有存进第 {chapter} 章：写这一稿的时候那一章还不存在，现在它有了"
            "——那是作者刚建的，这一稿不是照着它写的，所以不覆盖。"
            "要用的话让我照现在这一章重写一稿。"
        )

    head = importer.single_chapter(current)
    if head is None:
        return False, (
            f"没有存进第 {chapter} 章：那一章现在的开头不是一行章标题（或者标题前面还有别的字）。"
            "这种时候动它会让整本书的章号错位，所以一个字都没写。稿子还在，交给作者。"
        )

    # 模型自己写了一行章标题时，**用作者那一行，不用它那一行**：标题是切章的依据，
    # 换标题是作者的动作（同上面那条 404 的理由）。它写的那份被丢掉，正文照旧。
    drafted = importer.single_chapter(body)
    text = (drafted.body if drafted else body).strip()
    if not text:
        # 空的一稿接上章标题**照样切得出恰好一章**，所以下面那道形状闸拦不住它——
        # 拦不住的后果是作者的一整章被一份空白盖掉。生成那一侧已经拒过一次空稿，
        # 这一条是第二道：候选表里那份 `body` 不是这一层写的，它只保证自己不清空一章。
        return False, (
            f"没有存进第 {chapter} 章：这一稿是空的，存上去等于把那一章清空。"
            "换个说法再让我写一次。"
        )
    candidate = importer.chapter_text(head.raw_heading, text)
    if importer.single_chapter(candidate) is None:
        # **写之前先验一次**，而不是等 `sync` 事后报错：`save_chapter` 是先写盘再 sync，
        # 那时正文已经盖上去了，作者要自己去修章标题才能存回来。这一稿多半自己又写了
        # 一行（或几行）章标题。
        return False, (
            f"没有存进第 {chapter} 章：这一稿接上原来的章标题之后切不成恰好一章"
            "（多半是稿子里自己又写了章标题）。稿子还在，交给作者。"
        )

    try:
        # **先把磁盘上现在那一版落成快照。** ADR 0021 承诺的退路是「版本历史里退得回去」，
        # 而 `chapter_snapshot` 里只有 `sync` 过的那些——作者在编辑器里改完还没同步的
        # 那一版，被盖掉就是**真没了**（那份 ADR 结尾的原话）。这一句让那条退路是真的。
        # 代价是多跑一次 `sync`（`save_chapter` 自己还要跑一次），而 `sync` 幂等：
        # 内容一字不差的章走 `unchanged_count`，不多出一行快照。
        importer.sync(store, project_id, root)
    except importer.SyncRefused as exc:
        return False, (
            f"没有存进第 {chapter} 章：这本书里有一个章节文件（{exc.path}）现在切不成一章，"
            "同步整本书会失败。稿子还在，请作者先把那个文件的开头修好。"
        )

    try:
        importer.save_chapter(
            store, project_id, root, chapter, candidate, expected_sha256=base_sha
        )
    except importer.ChapterChanged:
        # **唯一那道闸**（ADR 0021）。不是「问你可不可以」，是「你比它更晚改过」。
        return False, (
            f"没有存进第 {chapter} 章：写这一稿的时候作者又改过那一章，"
            "存上去会盖掉他刚写的字。稿子还在，让他自己决定要不要用。"
        )
    except importer.ChapterMissing:
        # 这中间那个文件被删了/改名了。同上面那条：交出正文，不重建。
        return False, (
            f"没有存进第 {chapter} 章：写这一稿的时候那一章的文件不在了。稿子还在，交给作者。"
        )
    except importer.SyncRefused:
        # 上面那次 `sync` 刚过，所以走到这儿只可能是**这几毫秒里**别的章被改坏了。
        # 正文**已经**在磁盘上（`save_chapter` 先写盘），所以这儿说的是「存了，但
        # 版本历史这一次没跟上」——磁盘是真相源（ADR 0007），不许反过来说没存。
        #
        # **留痕照留。** 这一支曾经直接 return、跳过下面那一行 `decision_log`，
        # 于是落到「作者的书被改了，日志页上没有这一行」——ADR 0021 拿「不挡，
        # 但每步留痕」换掉了「事前问一句」，只履行前半句就是把那笔交易赖掉一半。
        # 快照这一次没落下，所以那一行也说清楚了「版本历史没跟上」。
        _log_landing(conn, project_id=project_id, chapter=chapter, candidate=candidate)
        return True, (
            f"已经写进第 {chapter} 章了，但这本书里有别的章节文件切不成一章，"
            "所以这一次没能记进版本历史。请作者去看一眼。"
        )

    _log_landing(conn, project_id=project_id, chapter=chapter, candidate=candidate)
    return True, (
        f"已经写进第 {chapter} 章了（章标题保持原样）。"
        "不满意就在版本历史里退回上一版，活动记录里也有这一次的记录。"
    )


def _log_landing(conn: Connection, *, project_id: str, chapter: int, candidate: str) -> None:
    """日志页上那一行。**写成了就必须有它，一条出口都不许绕过去**（见 `_land` 的
    「留痕失败不吞」）—— 所以它是一个函数而不是抄两遍：抄两遍的那天会漏掉一支。
    """
    decisions.append(
        conn,
        project_id=project_id,
        kind=decisions.DecisionKind.CHAPTER_DRAFT,
        decision=decisions.Verdict.ACCEPT,
        # **作者在日志页上看得懂的那个名字**（`_decision_detail` 的「对象」那一行）。
        subject_name=f"第 {chapter} 章",
        chapter_number=chapter,
        payload={
            "chapter_number": chapter,
            "units": count_units(candidate, DraftLanguage.ZH),
            # 版本抽屉里那一版的锚。**不上屏**（作者认不得一段 sha256），
            # 它在这儿是为了让「日志那一行」和「快照那一版」对得上号。
            "text_sha256": importer.text_digest(candidate),
        },
        # 没有任何人点过这一次保存。**这一列就是 ADR 0020 / 0021 的整条退路**：
        # 作者能把系统自己做的和自己做的分开看。
        actor=decisions.SYSTEM_ACTOR,
    )


__all__ = [
    "AGENT_DRAFT_LENGTH",
    "AGENT_DRAFT_REASONING",
    "SELF_NOTE_MARK",
    "SELF_NOTE_UNITS",
    "ChapterDesk",
    "chapter_drafter",
    "split_self_note",
]
