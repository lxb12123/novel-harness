"""保存之后的事后语义核对 —— **只告警，永不阻断**（[ADR 0030](../../docs/adr/0030-versioned-summaries-and-advisory-reconciliation.md) 的窄例外）。

一条路上串着两问，它们的共同点是「写之前答不了，写之后答得准」：

    ① 这一章有没有对**不该知道的人**说破什么               （M1-b）
    ② 作者刚改的这一段跟**后面已经写完的章**抵不抵触        （轨道阶段 2）

── 一、为什么是「事后」，而不是拼 prompt 的时候就防住 ────────────────────

**关键是一条不对称：写之前不知道谁在场（那是写出来的结果），写之后知道（数得出来）。**
生成侧只能退化成「全书全禁」——安全，却安全得没用（AI 拿着一份「什么都别碰」的清单
写不出能用的东西）；验证侧拿真正文一数就是**精确**的。把它搬过来**不是降级处理，
是搬到了它唯一能算准的地方。**

第二问同理：ADR 0019 明写模式一不适用它（「停手 400 毫秒的交互里塞不下第二次往返」）
——**那句话仍然对**，所以这里不在续写那一次里验，而是等它落进正文之后，跟保存后跑
总结/抽取同一条路上再验一遍。

── 二、为什么只能用模型 ──────────────────────────────────────────────────

ADR 0005 的铁律是「只做集合判断，不做语义判断」，而这两问都是语义的：真书里秘密的
内容散在字里行间，合成小册子那种「每条秘密配一个生造专名（玄血蛊）」的形态**真书没有**。
所以这件事进不了 `checks/`。它能存在，靠的是 ADR 0030 给「写完之后用模型核对、只写
通知、不阻断、不改数据」开的那个窄例外——**「不阻断」是这条例外成立的前提**，不是
一个可以以后再收紧的实现细节。

落点因此只有一种通知：`text_advisory`（026）。**绝不许挂 `validation_blocked`**——
那一档会把这一章的总结与抽取两支停掉，作者改一个老章就换来「总结怎么一直不更新」，
而那件事不会有任何东西报错。

── 三、信息隔离：写的那个看不见验的那个看见的东西 ────────────────────────

    Writer 的上下文：前文、下文、**前面**章节的总结   —— 干净的，从没见过轨道
    验证这一侧      ：轨道（可能带着不该出现的内容）+ 刚写下的那段正文
    回给作者/Writer ：一条**结构化**评语，不含轨道原文

**解法不是过滤，是分开：写的和验的不必是同一个上下文。** 本模块住在 `draft/` 外面
（同 `track.py`，`tests/test_track_isolation.py` 钉着那一半），而这一半——「评语里
不许有轨道原文」——由出参形状本身保证：`TrackClash` 只有三个字段
（第几句 / 跟第几章 / 冲突类型），**没有任何一个自由文本字段可以装下那段总结**。
提示词里那句「不要复述后面章节的内容」只是省一次无用的生成，**不是这条约束的依靠**。

    ✅ 第 3 句 ↔ 第 64 章，设定冲突
    ❌ 「你写萧决不知道那件事是错的，第 64 章他才知道自己身上是玄血蛊」

结构化的出参能被机械检查，自由文本不能。作者要细节自己去翻第 64 章。

── 四、锚由我们算，不由模型给 ────────────────────────────────────────────

模型返回的是**句号**（我们编的号），不是引语。ADR 0006 那段病史里「模型返回的 quote
有 10–30% 对不上原文」在这儿从源头上不成立：`(para_index, quote_text, occurrence_k)`
三个分量全部来自我们自己切出来的那张句表，模型只能在里面挑一行。挑错行是误报（一条
可忽略的通知），挑一个不存在的号会被当场丢掉。

── 五、验证这一侧可以换模型 ──────────────────────────────────────────────

它干的是判对错不是写文章，可以更便宜更快。**换模型是零代价**——上下文本来就是分开的。
这里不写死任何模型名：`reviewer` 是注入的（`api/deps.py` 按现有 BYOK/capability 那套
装配），模型没配好 / 端点炸了只让这一次核对**安静地不发生**，绝不把栈甩给作者。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Final, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .db import Connection
from .draft.provider import CompletionResult
from .extract.call_audit import record_call
from .extract.control import AuditedCompletion
from .graph import KnowledgeCell, KnowledgeState, StoryGraph, TextAnchor
from .ids import EntityType, new_id
from .mentioned import mentioned_cast
from .panel.constraints import resolve_cast
from .panel.knowledge import knowledge_matrix
from .system_notifications import (
    background_failure_dedupe_key,
    enqueue_text_advisory,
    resolve_stale_chapter_advisories,
)
from .text import occurrence_at, paragraphs as split_paragraphs
from .track import Track, build_track, current_chapter_text

__all__ = [
    "ADVISORY_CAPABILITY",
    "ADVISORY_CELL_LIMIT",
    "ADVISORY_VERSION",
    "AdvisoryOutcome",
    "AdvisoryRequest",
    "ConflictKind",
    "Reviewer",
    "SecretSlip",
    "Sentence",
    "TrackClash",
    "numbered_sentences",
    "review_saved_chapter",
    "review_track_on_demand",
]


ADVISORY_VERSION: Final = "text-advisory-v1"
"""提示词与出参 schema 的版本。**改提示词就要改它**：`model_call.params_json` 里记着
这个数，而幂等判据是 prompt 的内容哈希——版本进了 prompt，改版就自然重新算一遍。

── ⚠️ 2026-08-23 加【那几章的原文片段】那一块时**故意没有升它**（维护者裁定）──

那次改动往轨道那一问的 prompt 里加了一块新内容（阶段 4 三级下探）。按上面那条规矩
本该升到 v2，**没升，理由是那条规矩在这一次是把钝刀**：

- 这个版本号写在**两问共用**的提示词里，升它 = 两问、全书一起作废重跑；
- 而真正改了内容的只有**轨道**那一问，且只在真的带回了片段的那几章——
  **那几章的 prompt 哈希本来就变了，本来就会重跑**。升版本对它没有额外作用；
- **秘密那一问的 prompt 一个字都没改**，升版本让它全书重付一遍，买不到任何东西。
  158 章的书 = 白跑 158 次。

**判据留给下一个人**：改动会不会让**某一份没变的 prompt** 产出不同的结论？
会 → 升；不会（纯追加，且只出现在需要它的那几份里）→ 不升，让哈希自己说话。
**别把「没升」读成「忘了升」**。"""

ADVISORY_CAPABILITY: Final = "advisory"
"""账上这一列的取值（`model_call.capability`）。**新长出一种花钱的动作就要去
`activity.py::_CAPABILITY_LABEL` 补一行中文**，那张表认不出的是原样回吐的——
作者的日志页上会出现一个英文机器码。"""

ADVISORY_CELL_LIMIT: Final = 60
"""一次最多把多少格「他还不知道」摆给模型看。**是名额，不是判据。**

一本书 50 条秘密、这一章提到 10 个人 = 500 行，其中绝大多数跟这一章毫无关系；
摆上去只会把模型的注意力摊薄，而它要找的是那一两句。砍掉的那些**不当作不存在**：
`AdvisoryOutcome.notes` 里会说「这一次只看了前 N 格」，零和省略都带着理由
（ARCHITECTURE §10 约束 8）。
"""

_SENTENCE_ENDERS: Final = "。！？!?…"
"""句子在哪儿断。**只认这几个终止符**，不做任何语义切分——这一层和 `text/` 一样，
一个语义判断都没有。"""

_SENTENCE_TRAILERS: Final = "」』”’）)》〉】"
"""终止符后面还能跟着的收尾符号。不吞掉它们的话，「他说：『走。』」会切出一个
孤零零的 `』` 当成第二句，而那一句作者点过去看见的是半个引号。"""

ReviewKind = Literal["secret", "track"]
"""两问各自的代号。**它同时是账上和去重键上的那个词**，所以别改字面量。"""

ConflictKind = Literal["setting", "timeline", "knowledge"]
"""冲突类型的封闭词表。**封闭是它的全部价值**：一个自由文本的「理由」字段能装下
后面章节的原文（信息隔离当场破功），一个三选一的枚举装不下任何东西。

- `setting`   设定对不上（玄铁令第 64 章写着只能在水底唤醒，这一章写它发白光）
- `timeline`  时间线对不上
- `knowledge` 谁在什么时候知道什么对不上
"""

_CONFLICT_LABEL: Final[dict[str, str]] = {
    "setting": "设定对不上",
    "timeline": "时间线对不上",
    "knowledge": "谁在什么时候知道什么，对不上",
}
"""上屏的中文。**枚举值本身不上屏**：`setting` 是机器码，作者屏幕上只该有人话。"""


# ══════════════════════════════════════════════════════════════════════════
# 出参：形状限死
# ══════════════════════════════════════════════════════════════════════════


class SecretSlip(BaseModel):
    """①「这一句像是对不该知道的人说破了什么」。

    `character` / `secret` 是**名字**而不是 node_id：模型只看得见名字，而它交回来的
    每一个都要在我们给出去的那份清单里逐字找得到，找不到就丢掉（见 `_keep_slips`）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence: int = Field(ge=1)
    character: str = Field(min_length=1)
    secret: str = Field(min_length=1)


class TrackClash(BaseModel):
    """②「这一句跟后面第几章抵触，哪一类抵触」。

    **三个字段就是全部，不许长第四个。** 多一个 `reason: str` 就等于给轨道原文开了
    一条进出参的路，而那条路一开，「写的那个看不见验的那个看见的东西」当场破功。
    `tests/test_advisory_review.py` 有一条把字段名集合钉死的守卫。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence: int = Field(ge=1)
    chapter: int = Field(ge=1)
    conflict: ConflictKind


class AdvisoryOutcome(BaseModel):
    """一次核对的回执。**两问各自跑没跑、为什么没跑，都要说得出来。**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    slips: tuple[SecretSlip, ...] = ()
    clashes: tuple[TrackClash, ...] = ()
    notices: tuple[str, ...] = ()
    """落进通知 outbox 的那几条的 id（**还没物化**，物化归调度那一侧）。"""

    notes: dict[str, str] = Field(default_factory=dict)
    """`{"secret": "...", "track": "..."}`。跑成了写一句结论，没跑写一句原因。

    **零必须带着理由**（§10 约束 8）：「这一章没问题」和「模型没配、压根没问」在
    右栏上长成同一个「什么都没有」，而这两件事的下一步动作完全相反。
    """


# ══════════════════════════════════════════════════════════════════════════
# 句表：模型只能在这里面挑一行
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Sentence:
    """一句话，外加它的锚。**编号是我们编的，从 1 起，按全章顺序。**"""

    number: int
    para_index: int
    text: str
    occurrence_k: int

    @property
    def anchor(self) -> TextAnchor:
        return TextAnchor(
            para_index=self.para_index,
            quote_text=self.text,
            occurrence_k=self.occurrence_k,
        )


def _segments(para: str) -> Iterator[tuple[int, str]]:
    """一段 → `(起点, 原样子串)`。**起点是给 `occurrence_at` 用的，不出这个模块**
    （ADR 0006 禁止的是对外发 offset，不是文本层内部不许知道位置）。"""
    start = 0
    i = 0
    size = len(para)
    while i < size:
        if para[i] in _SENTENCE_ENDERS:
            j = i + 1
            while j < size and para[j] in _SENTENCE_ENDERS:
                j += 1
            while j < size and para[j] in _SENTENCE_TRAILERS:
                j += 1
            yield start, para[start:j]
            start = j
            i = j
            continue
        i += 1
    if start < size:
        yield start, para[start:]


def numbered_sentences(paragraphs: Sequence[str]) -> list[Sentence]:
    """把一章正文切成带锚的句表。**零语义判断**：只认几个终止符。

    Notes:
        `occurrence_k` **不是自己数的**，是问 `text/anchor.occurrence_at`——「第 k 次
        出现」是非重叠计数，自己数一份的话 `他走了。走了。` 这种段落上就已经和全库
        唯一那份定义分叉了，而症状是一条锚到隔壁半句的通知。

        问不出 k 的那一句（非重叠计数下它的起点被前一次吃掉了）**整句不进表**：
        进不了表 = 模型点不到它 = 不会产出一条点过去落在别处的通知。宁可少一句。
    """
    out: list[Sentence] = []
    for para_index, para in enumerate(paragraphs):
        for start, raw in _segments(para):
            lead = len(raw) - len(raw.lstrip())
            text = raw.strip()
            if not text:
                continue
            k = occurrence_at(para, text, start + lead)
            if k is None:
                continue
            out.append(
                Sentence(
                    number=len(out) + 1,
                    para_index=para_index,
                    text=text,
                    occurrence_k=k,
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════
# 一次调用的输入
# ══════════════════════════════════════════════════════════════════════════


class AdvisoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True, slots=True)
class AdvisoryRequest:
    """一次核对调用的全部输入；派生字段不能独立提供（同 `SummaryRequest`）。

    `prompt_hash` 是**内容地址**，幂等就靠它：正文没变、清单没变 ⇒ 同一份 prompt ⇒
    `model_call` 里已经有一条成功记录 ⇒ 这一次一分钱都不花。
    """

    kind: ReviewKind
    chapter_number: int
    messages: tuple[AdvisoryMessage, ...]
    prompt_bytes: bytes = field(init=False, repr=False)
    prompt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        encoded = json.dumps(
            [m.model_dump() for m in self.messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(self, "prompt_bytes", encoded)
        object.__setattr__(self, "prompt_hash", hashlib.sha256(encoded).hexdigest())

    def wire_messages(self) -> list[dict[str, str]]:
        return [m.model_dump() for m in self.messages]


class Reviewer(Protocol):
    """核对模型的契约。**返回 `CompletionResult`**（同 `RollingSummarizer` 的
    `analyzer`），解析和判定归本模块——运输层不认识这份 schema，也不该认识。"""

    def __call__(self, request: AdvisoryRequest) -> CompletionResult: ...


# ══════════════════════════════════════════════════════════════════════════
# 提示词
# ══════════════════════════════════════════════════════════════════════════


_SECRET_SYSTEM: Final = f"""你是中文长篇小说的后台核对器。你**只判对错，不写正文、不改正文**。
Prompt version: {ADVISORY_VERSION}.

下面给你两样东西：
【谁还不知道什么】这一章正文里被提到的人，各自到这一章为止还不知道、或者抱着错误
认知的那些事。这份清单是作者自己一条条登记下来的，**它是对的**。
【正文】这一章的正文，逐句编了号。

请只回答一个问题：**有没有哪一句，把清单上某个人还不知道的那件事，当着他说破了、
让他听见了、或者把他写成一副已经知道的样子。**

只输出 JSON，形如：
{{"findings":[{{"sentence":3,"character":"萧决","secret":"沈孤鸿之死"}}]}}

- `sentence`：出问题那一句的编号，必须是【正文】里真实出现过的编号；
- `character` / `secret`：**逐字抄**【谁还不知道什么】里的名字，不许换说法、不许自己造；
- 拿不准就不要报。什么都没发现时输出 {{"findings":[]}}。

不要输出任何解释、理由、Markdown 或多余的字。"""


_TRACK_SYSTEM: Final = f"""你是中文长篇小说的后台核对器。你**只判对错，不写正文、不改正文**。
Prompt version: {ADVISORY_VERSION}.

作者正在回头改一个旧章，而它后面还有已经写完的章。下面给你两样东西：
【后面已经写完的章】那几章的梗概。**它们已经写死了，不许改，也不许你复述。**
【正文】作者刚改的这一章，逐句编了号。

请只回答一个问题：**有没有哪一句，跟后面某一章已经写死的东西直接抵触。**

只输出 JSON，形如：
{{"findings":[{{"sentence":3,"chapter":64,"conflict":"setting"}}]}}

- `sentence`：出问题那一句的编号，必须是【正文】里真实出现过的编号；
- `chapter`：跟哪一章抵触，必须是【后面已经写完的章】里列出的章号；
- `conflict`：三选一 —— `setting`（设定对不上）/ `timeline`（时间线对不上）/
  `knowledge`（谁在什么时候知道什么，对不上）；
- 拿不准就不要报。什么都没发现时输出 {{"findings":[]}}。

不要输出任何解释、理由、Markdown 或多余的字。**尤其不要复述后面章节的内容。**"""


def _numbered_block(sentences: Sequence[Sentence]) -> str:
    return "\n".join(f"{s.number}. {s.text}" for s in sentences)


def _unknown_lines(cells: Sequence[tuple[str, str, KnowledgeCell]]) -> list[str]:
    """把「他还不知道」那几格渲染成人话。**KNOWS 的格子一行都不进来。**

    `BELIEVES` 也算「不知道」：李管家以为血脉秘密已泄露，这一场把真相说破同样是崩
    人设——判据和 `SceneConstraints.must_not_reveal` 是同一条（`state != KNOWS`），
    两处渲染不同是因为那一份进 Writer 的 prompt、这一份进核对器的 prompt。
    """
    lines: list[str] = []
    for who, secret, cell in cells:
        if cell.state is KnowledgeState.KNOWS:
            continue
        if cell.state is KnowledgeState.BELIEVES:
            believed = f"，他以为是「{cell.believed_value}」" if cell.believed_value else ""
            lines.append(f"- {who}：对「{secret}」抱着错误认知{believed}")
        else:
            lines.append(f"- {who}：还不知道「{secret}」")
    return lines


def _secret_request(
    chapter: int, sentences: Sequence[Sentence], lines: Sequence[str]
) -> AdvisoryRequest:
    body = (
        f"【谁还不知道什么】截至第 {chapter} 章：\n"
        + "\n".join(lines)
        + f"\n\n【正文】第 {chapter} 章，逐句编号：\n"
        + _numbered_block(sentences)
    )
    return AdvisoryRequest(
        kind="secret",
        chapter_number=chapter,
        messages=(
            AdvisoryMessage(role="system", content=_SECRET_SYSTEM),
            AdvisoryMessage(role="user", content=body),
        ),
    )


def _excerpt_block(track: Track) -> str:
    """三级下探回来的那几段原文（ADR 0038 阶段 4）。**没有就整块不出。**

    ── 它为什么必须进这一份 prompt ────────────────────────────────────────

    不进 = 「算完扔掉」，而那正是这一整批工作反复在修的病。三级只在**总结可证明地
    没提到某个锚点**时才下探（`track._dig`），所以这几段是那一章里唯一能回答
    「你写的这句跟它抵不抵触」的证据——二级那份总结对这几样东西只字未提。

    ── 它**只**进这一份 prompt ───────────────────────────────────────────

    这一侧是验证的上下文，它按定义就看得见后面章节的内容。Writer 那一侧从没见过
    轨道（`track.py` 模块头第一节），两条守卫钉着：`tests/test_track_isolation.py`
    静态扫 `draft/` + `calibration/`，外加一条拿轨道每一段去搜 prompt 的动态网。
    """
    if not track.excerpts:
        return ""
    lines = "\n".join(
        # 段号 +1：`para_index` 全系统 0-based，而这一行是说给模型听的人话。
        f"- 第 {item.chapter_number} 章第 {item.para_index + 1} 段：{item.text}"
        for item in track.excerpts
    )
    return (
        "\n\n【那几章的原文片段】（上面的总结没提到你正在改的某几样东西，"
        "所以把原文里提到它们的那几段补在这儿）：\n" + lines
    )


def _track_request(
    chapter: int, sentences: Sequence[Sentence], track: Track
) -> AdvisoryRequest:
    later = "\n".join(
        f"- 第 {row.chapter_number} 章：{row.summary}" for row in track.chapters
    )
    body = (
        f"【后面已经写完的章】（全书写到第 {track.frontier} 章）：\n"
        + later
        + _excerpt_block(track)
        + f"\n\n【正文】第 {chapter} 章，逐句编号：\n"
        + _numbered_block(sentences)
    )
    return AdvisoryRequest(
        kind="track",
        chapter_number=chapter,
        messages=(
            AdvisoryMessage(role="system", content=_TRACK_SYSTEM),
            AdvisoryMessage(role="user", content=body),
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# 解析 + 机械复核
# ══════════════════════════════════════════════════════════════════════════

_Finding = TypeVar("_Finding", SecretSlip, TrackClash)


def _payload(text: str) -> dict[str, Any]:
    """从模型的回话里抠出那个 JSON 对象。**抠不出来就抛**，不猜。

    只做一件宽容：剥掉 ```json 围栏。模型爱加它，而这跟「猜它想说什么」是两回事。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("核对器必须返回一个 JSON 对象")
    return value


def _pick(payload: dict[str, Any], model: type[_Finding]) -> list[_Finding]:
    """把 `{"findings":[...]}` 里的每一条**只按我们认识的那几个键**建出来。

    ── 为什么不是 `TypeAdapter(list[Model]).validate_python(...)` ──────────────

    两件事，都不是洁癖：

    1. **模型很爱多写一个 `"reason": "……第 64 章他才知道自己身上是玄血蛊"`。** 整份
       校验下 `extra="forbid"` 会把**整个答案**打掉（一条真的抵触也跟着没了）；这里
       只取三个键，那句理由**连落脚的地方都没有**——信息隔离不靠模型听话。
    2. **一条烂的不该毁掉一整份。** 逐条建，坏的那条丢掉，剩下的照常报。
    """
    out: list[_Finding] = []
    raw = payload.get("findings")
    if not isinstance(raw, list):
        return out
    fields = tuple(model.model_fields)
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model(**{key: item.get(key) for key in fields}))
        except ValidationError:
            continue
    return out


def _keep_slips(
    raw: Sequence[SecretSlip],
    *,
    sentences: Sequence[Sentence],
    allowed: Sequence[tuple[str, str]],
) -> tuple[SecretSlip, ...]:
    """只留下**机械上说得通**的那几条。这是结构化出参唯一的价值兑现处。

    三道全是集合判断：句号在不在表上、这个人这条秘密在不在我们给出去的那份清单里、
    同一条不重复。模型编一个人名、编一个秘密、指一个不存在的句号——三种都在这儿掉。
    """
    numbers = {s.number for s in sentences}
    pairs = set(allowed)
    seen: set[tuple[int, str, str]] = set()
    kept: list[SecretSlip] = []
    for item in raw:
        key = (item.sentence, item.character, item.secret)
        if item.sentence not in numbers or key in seen:
            continue
        if (item.character, item.secret) not in pairs:
            continue
        seen.add(key)
        kept.append(item)
    return tuple(kept)


def _keep_clashes(
    raw: Sequence[TrackClash],
    *,
    sentences: Sequence[Sentence],
    chapters: Sequence[int],
) -> tuple[TrackClash, ...]:
    """同 `_keep_slips`：句号要在表上，章号要在我们给出去的那几章里。"""
    numbers = {s.number for s in sentences}
    allowed = set(chapters)
    seen: set[tuple[int, int, str]] = set()
    kept: list[TrackClash] = []
    for item in raw:
        key = (item.sentence, item.chapter, item.conflict)
        if item.sentence not in numbers or item.chapter not in allowed or key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return tuple(kept)


# ══════════════════════════════════════════════════════════════════════════
# 通知
# ══════════════════════════════════════════════════════════════════════════


def _more(rest: int) -> str:
    return f"（另有 {rest} 处）" if rest else ""


def _slip_title(slips: Sequence[SecretSlip]) -> str:
    """**措辞要同时对得上「还不知道」和「误以为」两态。**

    出参上没有那一格的状态（也不该有：多一个字段就多一处要维护的口径），而
    「他还不知道」对一个持错误认知的人是句假话——李管家以为血脉秘密已泄露，
    问题不是他没听说过，是这一句把真相摆到了他面前。「还不该听见它」两种都成立。
    """
    first = slips[0]
    return (
        f"第 {first.sentence} 句：这一句像是把「{first.secret}」说破了，"
        f"而{first.character}到这一章还不该听见它。{_more(len(slips) - 1)}"
    )


def _clash_title(clashes: Sequence[TrackClash]) -> str:
    """**只有句号、章号、类型。** 后面那一章的原文一个字都不在这句话里——
    信息隔离的最后一米就在这儿，而它靠的是 `TrackClash` 压根没有装它的地方。"""
    first = clashes[0]
    return (
        f"第 {first.sentence} 句 ↔ 第 {first.chapter} 章：{_CONFLICT_LABEL[first.conflict]}。"
        f"{_more(len(clashes) - 1)}"
    )


def _file_notice(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    chapter_number: int,
    snapshot_id: str,
    kind: ReviewKind,
    title: str,
    anchor: TextAnchor,
    source_sha256: str,
) -> str:
    return enqueue_text_advisory(
        conn,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        title=title,
        # 去重键含来源快照：**正文一改才允许再提醒一次**，同一份正文反复扫不重开
        # （不变量 10）。作者按了「忽略」之后那个 hash 对是终态。
        dedupe_key=background_failure_dedupe_key(
            kind="text_advisory",
            subject_type="chapter",
            subject_id=chapter_id,
            operation=f"advisory:{kind}",
            source_snapshot_id=snapshot_id,
            job_id=None,
        ),
        jump=anchor,
        # 落库的这个数是「这条告警照的是哪一版正文」——作者改完之后
        # `resolve_stale_chapter_advisories` 靠它认出哪些该收走。
        source_sha256=source_sha256,
    )


# ══════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════


def _default_call_id(project_id: str) -> str:
    return new_id(EntityType.CALL, project_id)


# 「当前那一版快照」的读法**全仓只有一处**，住在 `track.py`（本模块已经 import 它，
# 而它不 import 本模块——方向对）。两份的下场是那条判据在两处各写一遍，漂了之后的
# 产物是一条锚在旧正文上的告警，而那种错没有任何东西会红。
_current_chapter = current_chapter_text


def _already_paid(conn: Connection, project_id: str, prompt_hash: str) -> bool:
    """这份 prompt 已经成功跑过一次了吗（**内容地址幂等**，同 `RollingSummarizer`）。

    正文没变 ⇒ 句表没变 ⇒ 清单没变 ⇒ 同一份 prompt。于是「同一份正文被扫第二遍」
    一分钱都不花，而这条路每保存一次就可能被触发一次。
    """
    row = conn.execute(
        "SELECT 1 FROM model_call WHERE project_id = ? AND capability = ? "
        "AND prompt_hash = ? AND call_state = 'SUCCEEDED' LIMIT 1",
        (project_id, ADVISORY_CAPABILITY, prompt_hash),
    ).fetchone()
    return row is not None


def _ask(
    conn: Connection,
    request: AdvisoryRequest,
    reviewer: Reviewer,
    *,
    project_id: str,
    call_id_factory: Callable[[str], str],
) -> str | None:
    """跑一次核对调用并把账记上。返回模型的回话；`None` = 这一份已经跑过了。

    **不自己判成败**：provider 抛错就让它抛出去，由 `_review_secrets` /
    `_review_track` 收成一句 note——这条路是后台的，它的失败形态是「什么都没发生」，
    不是一个栈。
    """
    if _already_paid(conn, project_id, request.prompt_hash):
        return None
    started = perf_counter()
    # **审计拷贝走 `AuditedCompletion`**（同 `RollingSummarizer`）：缓存那两列要从
    # `CacheUsage` 拍平成标量，自己在这儿拍一遍就是第二份口径，而它糊错的方向是
    # 「不支持缓存」和「一次都没命中」互换——两个相反的下一步动作。
    audited = AuditedCompletion.from_result(reviewer(request))
    elapsed_ms = max(0, int((perf_counter() - started) * 1_000))
    record_call(
        conn,
        project_id=project_id,
        capability=ADVISORY_CAPABILITY,
        model=audited.model,
        finish_reason=audited.finish_reason,
        schema_version=ADVISORY_VERSION,
        prompt_hash=request.prompt_hash,
        prompt_bytes=request.prompt_bytes,
        text=audited.text,
        prompt_tokens=audited.prompt_tokens,
        completion_tokens=audited.completion_tokens,
        cache_read_tokens=audited.cache_read_tokens,
        cache_write_tokens=audited.cache_write_tokens,
        cost=audited.cost,
        elapsed_ms=elapsed_ms,
        chapter_number=request.chapter_number,
        call_id_factory=call_id_factory,
    )
    conn.commit()
    return audited.text


def review_saved_chapter(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    chapter_number: int,
    *,
    reviewer: Reviewer,
    cell_limit: int = ADVISORY_CELL_LIMIT,
    call_id_factory: Callable[[str], str] = _default_call_id,
) -> AdvisoryOutcome:
    """两问跑一遍，把结论落成 `text_advisory` 通知。**永远不阻断。**

    Args:
        reviewer: 注入的核对模型（`api/deps.py` 装配）。**它炸了这一次核对就不发生**
            ——没有通知、没有栈、没有 500；`notes` 里说得出为什么。

    Raises:
        两问自己的失败（模型不可用、回了一段散文、图层查不到）**一律不往外抛**，
        收成 `notes` 里的一句话。真会冒出去的只有最后落库那一步的失败（库被别的
        连接锁着之类）——调度那一侧兜着它（`BackgroundRuntime._review_chapter`），
        **这儿不假装它不会发生**。

    Returns:
        一份回执。`notices` 是**通知 outbox 的 id**，还没物化——物化归调度那一侧
        （`api/background_runtime.py`），同别的通知生产者。

    Notes:
        **调用方要保证「这一章的正文已经稳定了」**（作者切走了，不是正改着）。本模块
        自己不问焦点：它拿到什么就核对什么，而「什么时候值得花这一次钱」是调度的判断。
        真被连着叫两次也不会连着付两次钱——`_already_paid` 是内容地址的。
    """
    found = _current_chapter(conn, project_id, chapter_number)
    if found is None:
        return AdvisoryOutcome(
            chapter=chapter_number,
            notes={"chapter": "这一章在库里没有当前快照，核对无从谈起。"},
        )
    paragraphs = split_paragraphs(found.text)
    sentences = numbered_sentences(paragraphs)
    if not sentences:
        return AdvisoryOutcome(
            chapter=chapter_number, notes={"chapter": "这一章的正文里一句话都没有。"}
        )

    notes: dict[str, str] = {}
    broke: list[str] = []
    slips = _review_secrets(
        conn,
        store,
        project_id,
        chapter_number,
        sentences=sentences,
        paragraphs=paragraphs,
        reviewer=reviewer,
        cell_limit=cell_limit,
        call_id_factory=call_id_factory,
        notes=notes,
        broke=broke,
    )
    clashes = _review_track(
        conn,
        store,
        project_id,
        chapter_number,
        sentences=sentences,
        text=found.text,
        reviewer=reviewer,
        call_id_factory=call_id_factory,
        notes=notes,
        broke=broke,
    )

    by_number = {s.number: s for s in sentences}
    notices: list[str] = []
    conn.execute("BEGIN IMMEDIATE")
    if not broke:
        # 两问都有了结论才敢收走旧账。**一问都没答上来（模型不可用）时一条都不动**——
        # 那一次的承诺是「什么都没发生」，而把作者昨天那条告警悄悄标成「已解决」
        # 是发生了一件事，还是他最看不见的那一种。
        resolve_stale_chapter_advisories(
            conn,
            project_id=project_id,
            chapter_id=found.chapter_id,
            current_sha256=found.sha256,
        )
    for kind, title, first in (
        ("secret", _slip_title(slips) if slips else "", slips[0].sentence if slips else 0),
        ("track", _clash_title(clashes) if clashes else "", clashes[0].sentence if clashes else 0),
    ):
        if not title:
            continue
        notices.append(
            _file_notice(
                conn,
                project_id=project_id,
                chapter_id=found.chapter_id,
                chapter_number=chapter_number,
                snapshot_id=found.snapshot_id,
                kind=kind,  # type: ignore[arg-type]
                title=title,
                anchor=by_number[first].anchor,
                source_sha256=found.sha256,
            )
        )
    conn.commit()
    return AdvisoryOutcome(
        chapter=chapter_number,
        slips=slips,
        clashes=clashes,
        notices=tuple(notices),
        notes=notes,
    )


def review_track_on_demand(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    chapter_number: int,
    *,
    reviewer: Reviewer,
    call_id_factory: Callable[[str], str] = _default_call_id,
) -> AdvisoryOutcome:
    """只答轨道那一问，**而且一条通知都不落**（轨道阶段 3：模式二自己调的那条）。

    ── 跟 `review_saved_chapter` 的差别只有两处，两处都是有意的 ──────────

    **一、不问秘密那一问。** 模型问的是「我刚写的这段跟后面打不打架」，
    秘密说破那一问归保存之后那一遍（它要的是稳定正文，不是写到一半的稿子）。

    **二、不落通知。** 那一遍是**系统**发现的、作者没做过任何动作，所以要在右栏
    留一条；这一次是**模型自己问的**，答案当场回给它。给它记一条通知等于让作者的
    右栏冒出一件他没做过的事——而右栏那一格的语义是「你该看一眼」，不是「模型问过什么」。
    幂等仍然共用同一份内容地址判据（`_already_paid`），所以模型连问两次不会连付两次。

    ── 出参为什么仍然是 `AdvisoryOutcome` ────────────────────────────────

    同一件事只该有一种回执形状。这一次 `slips` / `notices` 必然是空，
    而 `notes["track"]` 照旧**带着理由**——「没抵触」和「压根没核对」
    （在最前沿写 / 模型没配 / 已经付过钱）在右栏和在模型眼里都长成
    「什么都没有」，而这两件事的下一步动作完全相反（§10 约束 8）。
    """
    found = _current_chapter(conn, project_id, chapter_number)
    if found is None:
        return AdvisoryOutcome(
            chapter=chapter_number,
            notes={"track": "这一章在库里没有当前快照，核对无从谈起。"},
        )
    paragraphs = split_paragraphs(found.text)
    sentences = numbered_sentences(paragraphs)
    if not sentences:
        return AdvisoryOutcome(
            chapter=chapter_number, notes={"track": "这一章的正文里一句话都没有。"}
        )
    notes: dict[str, str] = {}
    clashes = _review_track(
        conn,
        store,
        project_id,
        chapter_number,
        sentences=sentences,
        text=found.text,
        reviewer=reviewer,
        call_id_factory=call_id_factory,
        notes=notes,
        broke=[],  # 收不走旧账，也就不必记谁没答上来
    )
    return AdvisoryOutcome(chapter=chapter_number, clashes=clashes, notes=notes)


def _review_secrets(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    chapter_number: int,
    *,
    sentences: Sequence[Sentence],
    paragraphs: Sequence[str],
    reviewer: Reviewer,
    cell_limit: int,
    call_id_factory: Callable[[str], str],
    notes: dict[str, str],
    broke: list[str],
) -> tuple[SecretSlip, ...]:
    """① 秘密有没有被说破（M1-b）。

    Notes:
        **歧义称呼在这一侧照旧出局**（`mentioned_cast` 走默认档，不 `expand_ambiguous`）。
        模式二那一侧把「师兄」的 8 个候选全算在场是对的——它算的是**给模型的禁令**，
        多算一个人只会多一批禁令。这一侧算的是**报不报警**，多算一个人就是多一条打扰，
        而 ADR 0030 的推翻条件第一条正是「误报率把通知变成纯噪声」。
        判不出「师兄」是谁时这一层的正确动作是闭嘴（§10 约束 7），不是猜一个。
    """
    try:
        surfaces = mentioned_cast(store, project_id, paragraphs)
        if not surfaces:
            notes["secret"] = "这一章的正文里没有出现花名册上的人，算不出该拿谁的认知来比。"
            return ()
        resolved = resolve_cast(store, project_id, surfaces)
        if not resolved.ids:
            notes["secret"] = "这一章提到的称呼一个都解析不到唯一的人，这一次不猜。"
            return ()
        matrix = knowledge_matrix(store, project_id, chapter_number, resolved.ids)
        cells = [
            (who.name, secret.name, matrix.cell(who.id, secret.id))
            for who in matrix.characters
            for secret in matrix.secrets
        ]
        lines = _unknown_lines(cells)
        if not lines:
            notes["secret"] = "这一章提到的人，对已登记的秘密全都知情，没有可说破的。"
            return ()
        total = len(lines)
        lines = lines[: max(0, cell_limit)]
        allowed = [
            (who, secret)
            for who, secret, cell in cells
            if cell.state is not KnowledgeState.KNOWS
        ][: max(0, cell_limit)]
        request = _secret_request(chapter_number, sentences, lines)
        answer = _ask(
            conn,
            request,
            reviewer,
            project_id=project_id,
            call_id_factory=call_id_factory,
        )
        if answer is None:
            notes["secret"] = "同一份正文和同一份清单已经核对过了，这一次没有再花钱。"
            return ()
        kept = _keep_slips(
            _pick(_payload(answer), SecretSlip), sentences=sentences, allowed=allowed
        )
    except Exception as exc:  # noqa: BLE001 —— 后台告警链的失败形态是「什么都没发生」
        # 记账那一步半途炸了的话事务还开着，下面落通知那一次 BEGIN 会撞上它。
        conn.rollback()
        broke.append("secret")
        notes["secret"] = f"这一次没能核对秘密有没有被说破：{exc}"
        return ()
    trimmed = f"（清单太长，这一次只看了前 {len(lines)} 条，共 {total} 条）" if total > len(lines) else ""
    notes["secret"] = (
        f"核对过了，{len(kept)} 处像是说破了什么{trimmed}。"
        if kept
        else f"核对过了，没发现说破{trimmed}。"
    )
    return kept


def _review_track(
    conn: Connection,
    store: StoryGraph,
    project_id: str,
    chapter_number: int,
    *,
    sentences: Sequence[Sentence],
    text: str,
    reviewer: Reviewer,
    call_id_factory: Callable[[str], str],
    notes: dict[str, str],
    broke: list[str],
) -> tuple[TrackClash, ...]:
    """② 跟后面已经写完的章抵不抵触（轨道阶段 2）。

    **在最前沿写就一步都不走**：`build_track` 那时只花一条 `MAX(number)` 查询，
    连花名册都不解析——「是最新章时行为一字不变」这条在这一层也成立。
    """
    try:
        track = build_track(conn, store, project_id, chapter=chapter_number, text=text)
        if track.at_frontier or not track.chapters:
            notes["track"] = track.note
            return ()
        request = _track_request(chapter_number, sentences, track)
        answer = _ask(
            conn,
            request,
            reviewer,
            project_id=project_id,
            call_id_factory=call_id_factory,
        )
        if answer is None:
            notes["track"] = "同一份正文和同一批后续章节已经核对过了，这一次没有再花钱。"
            return ()
        kept = _keep_clashes(
            _pick(_payload(answer), TrackClash),
            sentences=sentences,
            chapters=[row.chapter_number for row in track.chapters],
        )
    except Exception as exc:  # noqa: BLE001 —— 同上：这条路不许把栈甩给作者
        conn.rollback()
        broke.append("track")
        notes["track"] = f"这一次没能跟后面的章比对：{exc}"
        return ()
    notes["track"] = (
        f"跟后面 {len(track.chapters)} 章比过了，{len(kept)} 处对不上。"
        if kept
        else f"跟后面 {len(track.chapters)} 章比过了，没发现抵触。"
    )
    return kept
