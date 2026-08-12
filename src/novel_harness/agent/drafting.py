"""`DraftFn` 的实现 —— **起草一章，然后直接写进那一章**（[ADR 0021](../../../docs/adr/0021-agent-writes-drafts-without-asking.md)）。

`tools.py` 的 `draft_chapter` 只做两件事：算这一章的约束（边界二），然后把
`(DraftAsk, DraftContext)` 交给注入进来的这个函数。**「章号 → 一稿正文」的实现不在这儿**
——它在 `draft/product_draft.py`，`/draft` 那条 HTTP 路由调的是同一个函数。
这一层加的是 ADR 0021 那三件事：长度档、落盘、留痕。

── 为什么它在 `agent/` 而不在 `draft/` ────────────────────────────────────

`draft/product_draft.py` 的 docstring 写着「**它也不落盘**」，那不是分工洁癖：
行内续写（ADR 0015）按定义不落盘，`/draft` 从浏览器发起时作者眼前就是编辑器，
**只有模式二这条路要求写进磁盘**。把落盘塞进那一层等于给另外两个调用方一个
它们没要过的副作用。

反过来它也不在 `tools.py`：写盘要 `GraphStore`（带写入面）和一条连接，而
`ToolContext` 上**只有 `StoryGraph`**——「模型改不了作者的 canon」是一条类型保证
（边界一），为了落盘把写入面塞回那个 dataclass 就是用一次功能换掉那条保证。
所以写入面握在**这个闭包**手里：`tools.py` 那一层拿到的仍然只有一个
`(DraftAsk, DraftContext) -> DraftProduct` 的可调用对象。

一条推论，写在这儿免得下一个人当成遗漏：**模型没有「只写不草」这个动作**。
它不能拿一段自己编的文本去盖某一章——能写进磁盘的只有刚刚由后端按当前章约束
生成的那一稿。

── 唯一那道闸：拒绝覆盖作者比它更晚改过的那一章 ──────────────────────────

ADR 0021 把「落盘前问一次」换成了「不问，但绝不盖掉更晚的改动」。形状同
`expected_canon_version` 那套乐观闸：**起草之前**记下磁盘上那一份的 `text_sha256`，
落盘前比对磁盘当前值（`importer.save_chapter` 里读了立刻比、比完立刻写），
不一致就不写、把理由说出来。

这道闸是**不对称**的（ADR 0021 结尾那段）：该拒没拒 = 作者的字被盖掉，
而被盖那一版**如果从没 `sync` 过就是真没了**。所以这一层在写之前先跑一次
`importer.sync()`——见 `_land()` 的「先把现在那一版落成快照」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

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
from .ports import DraftAsk, DraftFn, DraftProduct, ToolRefused

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


def _previous_tail(root: Path, chapter: int) -> str:
    """上一章的正文，整篇。**截多长由后端按模型窗口算**（`product_tail_limit`），
    这一层不留第二份长度常量——那正是 2026-08-10 从起草抽屉里清掉的病
    （前端一个 800、后端一个上万字）。

    第 1 章送空串，**不拿别的章冒充**。
    """
    if chapter <= 1:
        return ""
    return importer.read_chapter(root, chapter - 1) or ""


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
) -> DraftFn:
    """造一个「起草 + 落盘 + 留痕」的 `DraftFn`。**装配层调它**（`api/chat.py`）。

    Args:
        store / conn: 写入面和连接。**它们进的是这个闭包，不是 `ToolContext`**
            （见模块 docstring）。
        root: 项目根目录。收 `str` 是为了和 `ToolContext.root_path` 同形——
            装配层手里那个就是 `project.root_path`。
        capability: 已经解析好的能力证据。`plan` 不在这儿算——见 `draft()` 里那段。

    Returns:
        一个 `(DraftAsk, DraftContext) -> DraftProduct`。**拿到正文之后它不再为
        「落不落得了盘」抛异常**：无论写没写成都带着回执（`calls`）返回，否则那笔账会
        连同 `ToolOutcome.calls` 一起消失，而成本闸和日志页同时失明。
        唯一的例外是留痕失败，见 `_land`。
    """

    book_root = Path(root)

    def draft(ask: DraftAsk, ctx: DraftContext) -> DraftProduct:
        # **`plan_call` 在这儿算，不在工厂里算。** 放在工厂里的话，一个撑不起整章
        # 起草预算的模型会让 `POST …/turn` 整个 422——聊天本来是能用的，作者只会看到
        # 「写作助手用不了」。放在这儿，坏的只有起草这一个工具，而它说得出原因。
        try:
            plan = plan_call(length, AGENT_DRAFT_REASONING, capability)
        except (CapabilityError, ValueError) as exc:
            raise ToolRefused(
                f"这个模型撑不起一次整章起草（{exc}）。"
                "让作者去顶栏「AI 设置」换一个上下文更大的模型，或者他自己在编辑器里写。"
            ) from exc

        # **起草之前先记下它依据的是哪一份**（ADR 0021 那道乐观闸的另一半在
        # `importer.save_chapter`）。顺序不能反：模型调用要跑几十秒，作者就在旁边打字。
        current = importer.read_chapter(book_root, ask.chapter)
        base_sha = None if current is None else importer.text_digest(current)

        request = ChapterDraftRequest(
            goal=ask.goal,
            length=length,
            previous_tail=_previous_tail(book_root, ask.chapter),
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
                project_id=project_id,
                config=config,
                capability=capability,
                plan=plan,
                events=events,
                summaries=summaries,
                on_call=spent.append,
            )
        except DraftRefused as exc:
            # 发出去之前就被拒了 ⇒ 一分钱没花 ⇒ 这一档可以抛（`dispatch` 变成 ok=False）。
            raise ToolRefused(str(exc)) from exc
        except ProviderError as exc:
            # **不许让它逃出 `dispatch`**：`run_turn` 外面没有 try/except，漏出去
            # 作者看到的是一次崩溃，而不是「这一稿没写成，再试一次」。
            raise ToolRefused(
                f"这一稿没写成，联系不上写作模型：{exc}", calls=tuple(spent)
            ) from exc

        # ── 从这儿往下**一律不抛**：钱已经花掉了，回执必须回得去 ────────────
        text = drafted.result.text
        saved, note = _land(
            store,
            conn,
            project_id=project_id,
            root=book_root,
            chapter=ask.chapter,
            current=current,
            base_sha=base_sha,
            text=text,
        )
        return DraftProduct(text=text, calls=drafted.calls, saved=saved, note=note)

    return draft


def _land(
    store: GraphStore,
    conn: Connection,
    *,
    project_id: str,
    root: Path,
    chapter: int,
    current: str | None,
    base_sha: str | None,
    text: str,
) -> tuple[bool, str]:
    """把这一稿写进第 `chapter` 章。返回 `(写没写成, 说给模型听的那句话)`。

    **「写没写成」的每一种结局都是返回值，不是异常**（见 `chapter_drafter` 的返回值
    说明：抛出去 = 那次已经花过钱的调用连回执一起丢）。每一条不写的理由都要说得出口：
    模型据此决定下一句跟作者说什么，而「我写进去了」和「你刚改过，我没敢覆盖」
    是两句完全不同的话。

    **只有一件事仍然抛：留痕失败。** 写完盘之后那一行 `decision_log` 记不上时，
    这儿不吞（同 `api/chat.py::_ledger` 那条「记账失败也不吞」）——ADR 0021 拿
    「不挡，但每步留痕」换掉了「事前问一句」，一次**写了作者的正文却没留下痕迹**的落盘
    正好把那笔交易的另一半赖掉了，而它静默失败的形态是「作者的书被改了，日志页上没有
    这一行」。响一声很吵，但吵在对的方向。
    """
    if current is None:
        # ADR 0021 的范围限制，**这条 404 不许为 agent 放开**：新建一章要起章标题，
        # 而标题是切章的锚（切错了整本书章号会漂），`chapter_snapshot.chapter_id`
        # 也没有落点。所以交出正文，由作者建。
        return False, (
            f"第 {chapter} 章还不存在，所以这一稿没有存进去——新开一章要作者自己起章标题"
            "（书里靠那一行认章）。正文在上面，请他建好这一章再放进去。"
        )

    head = importer.single_chapter(current)
    if head is None:
        return False, (
            f"没有存进第 {chapter} 章：那一章现在的开头不是一行章标题（或者标题前面还有别的字）。"
            "这种时候动它会让整本书的章号错位，所以一个字都没写。正文在上面，交给作者。"
        )

    # 模型自己写了一行章标题时，**用作者那一行，不用它那一行**：标题是切章的依据，
    # 换标题是作者的动作（同上面那条 404 的理由）。它写的那份被丢掉，正文照旧。
    drafted = importer.single_chapter(text)
    body = (drafted.body if drafted else text).strip()
    if not body:
        # 空的一稿接上章标题**照样切得出恰好一章**，所以下面那道形状闸拦不住它——
        # 拦不住的后果是作者的一整章被一份空白盖掉。空返回是真会发生的
        # （供应商 4xx 之外还有「模型什么都没说」那一档，同 `StopReason.NO_OUTPUT`）。
        return False, (
            f"没有存进第 {chapter} 章：这一稿是空的，存上去等于把那一章清空。"
            "换个说法再让我写一次。"
        )
    candidate = importer.chapter_text(head.raw_heading, body)
    if importer.single_chapter(candidate) is None:
        # **写之前先验一次**，而不是等 `sync` 事后报错：`save_chapter` 是先写盘再 sync，
        # 那时正文已经盖上去了，作者要自己去修章标题才能存回来。这一稿多半自己又写了
        # 一行（或几行）章标题。
        return False, (
            f"没有存进第 {chapter} 章：这一稿接上原来的章标题之后切不成恰好一章"
            "（多半是稿子里自己又写了章标题）。正文在上面，交给作者。"
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
            "同步整本书会失败。正文在上面，请作者先把那个文件的开头修好。"
        )

    try:
        importer.save_chapter(
            store, project_id, root, chapter, candidate, expected_sha256=base_sha
        )
    except importer.ChapterChanged:
        # **唯一那道闸**（ADR 0021）。不是「问你可不可以」，是「你比它更晚改过」。
        return False, (
            f"没有存进第 {chapter} 章：写这一稿的时候作者又改过那一章，"
            "存上去会盖掉他刚写的字。正文在上面，让他自己决定要不要用。"
        )
    except importer.ChapterMissing:
        # 起草的这几十秒里那个文件被删了/改名了。同上面那条：交出正文，不重建。
        return False, (
            f"没有存进第 {chapter} 章：写这一稿的时候那一章的文件不在了。正文在上面，交给作者。"
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
    "chapter_drafter",
]
