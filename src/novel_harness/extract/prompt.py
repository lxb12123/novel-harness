"""结构化章节分析的稳定 prompt 构造。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, TypedDict

__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisMessage",
    "build_analysis_messages",
]


ANALYSIS_SCHEMA_VERSION: Final = "chapter-analysis-v1"
ANALYSIS_PROMPT_VERSION: Final = "chapter-analysis-prompt-v11"
"""v9（2026-09-06）：**把这本书已经用过的字段名喂回去**，让模型先查再决定命名。

v8 那条「一个属性一个稳定的维度名，每章复用同一个」是**纯静态指令**——模型看不见
自己上一章叫过什么，于是每章现编一个。真书上的代价：贾环一个人 130 个字段名，
光「他在哪个衙门」就有 13 个说法（所属 / 所属衙门 / 所在衙门 / 所属营伍 /
神枢营职位 / 官职 / 职位 / 职务 / 职业 / 职责 / 身份 / 升职状态 / 神枢营身份），
第 26 章那次还直接换成了英文（`level` / `skills` / `cultivation level`）。

**不给固定字段表**（作者裁定）：小说的状态种类本来就无穷，钉死一张表就读不出
「武魂值」「侦破无头案」「被陛下知晓」这类题材特有的东西。收敛靠的是
`KNOWN_DIMENSIONS_HEADER` 那一段——**先查已有的，有就复用，没有才新开**。

⚠️ 那一段**不进 `prompt_hash`**，理由见 `build_analysis_messages` 的 docstring
（进去了会让同一章反复重付 + 每条 run 死在 PROMPT_DRIFT）。

v8（2026-09-04）：给 `state` 定了口径——**维度名的语言、稳定复用、该盖哪些属性、
哪几样不许当状态报**。v7（2026-08-25）拿掉 `revealed_facts`（秘密下线，ADR 0039）。

⚠️ **改这段 prompt 的正文会改 `prompt_hash`，而 `prompt_hash` 是 `extraction_run`
的唯一键的一部分**——改一个字 = 想让老章节吃到新口径，全书每一章都得重新调一次模型。
别为了措辞好看改它。

**它不会自己重跑。** 调度那一侧问的是「这一章抽过没有」（`chapter_refresh.py::
extraction_alignment` → `chapter_refresh_run`，按 项目/章/正文代数 找），
**判据里没有 `prompt_hash`**：换版本号之后已经抽过的章仍然算「抽过」，不会被自动
排上重抽。所以代价的形状是「新章吃新口径、老章保持原样，作者想升级老章得自己重跑
（花钱）」，不是「一改就自动重买一整本」。

── v8 改了什么、为什么值这个代价 ──────────────────────────────────────────

作者 2026-09-04 看着角色卡上只有「所在地 / 装备」两行问：状态就这几样吗？年龄、
性别、修为、寿元、感情状态、掌握的技能——这些「要是文章有交代的话」都该在这儿。
**这不是显示层的洞：v7 对 `dimension` 一个字的指引都没有**，模型爱写什么维度就写
什么维度，于是能不能记下修为纯看运气。这一版把四件事写进 prompt：

1. **维度名用正文的语言写。** 这正是 v7 排在这儿等下一次改动的那条——2026-08-27
   实测（真书三章）模型新建 9 个维度，其中 `mental state`、`true status` 两个是
   英文，而正文和另外七个维度名都是中文，角色卡直接渲染维度名 = 中英混排。
   **今天连本带利一起付了**（本来就是攒到这一刻才做的事）。
2. **一个属性一个稳定的维度名，每章复用同一个。** 这条是正确性，不是措辞：
   `HAS_STATE` 的折叠键就是维度**文本**（`ingest_helpers.py` 的 `graph_key`，
   落库走 `upsert_node` 按 (project, label, name) find-or-create）。同一件事这章
   叫「修为」下章叫「武功境界」＝ 两个维度，新值盖不掉旧值，卡上并排站着两行
   互相打架的话。
3. **点名该盖的那批属性**（年龄 / 伤势 / 修为境界 / 掌握的招式 / 身份官职 / 立场 /
   身家 / 随身之物 / 婚配情感状态 / 寿元 / 眼下在追的目标），并明说**清单之外照样
   可以新开维度**——小说类型太多，穷举不了；这张清单要的是「大部分书重复的那一批
   有稳定名字」，不是把维度关进白名单。
4. **划清三条边界**：谁和谁（→ `relationship`）、在哪儿（→ `location`）、
   不会变的性别/性格/出身（→ `character_profiles`，界面上归「基础」那一格）。
   不划的话同一个事实会同时以两种形状进库，而这仓库最贵的病就是两个真相源。

**「只报正文说了的」也一并加重了一句**：清单会诱导模型去「找齐」，而每条 state
仍然必须配一句逐字引语——两头一起才拦得住「按常识补一个年龄」。

── v10（2026-09-10）：`relationship` 那一档**终于有了定义** ────────────────────

在这之前，四种 `kind` 里只有 `relationship` 的 `value` **一个字的说明都没有**：
schema 那行写着「subject + object + value」，然后就没了。`state` 那一档有整整一段
（dimension 是什么、value 写什么、保持几个词、跨章复用同一个名字），对照之下这是
一个明显的漏，而模型很诚实地填了它唯一能想到的东西——**这一场戏里发生了什么**。

真书上抽出来的样子（贾环，133 条关系边）：

    贾政   ← 怒斥、失望、认定其闯祸        ← 一场戏里的态度
    赵姨娘 ← 赠银与布匹                    ← 一个动作
    史湘云 ← 称呼为环三哥                  ← 一个称呼
    晴雯   ← 允许同去莲花山                ← 一次许可
    王子腾 ← 必须图穷匕现                  ← 一个打算
    王掌柜 ← 提供线索换取赌坊腾挪时间      ← 一笔交易

**而贾政是贾环的父亲，「父子」一条都没有。** 作者原话：「谁家好人关系是这种的，
不是应该是情侣、父亲母亲、老婆、暗恋对象之类的」。

所以 v10 给这一档补了三样，形状照着 `state` 那一段来：
1. **定义**——value 回答的是「他是她的谁」，那条**持续存在**的关系，不是这一章的事；
2. **反例清单**——上面那六类，逐条写进 prompt。**用实际产出的错误当反例**，
   不是编几个像样的：模型犯的正是这几种，泛泛说一句「不要写事件」拦不住；
3. **一句「吵架也要记父子」**——不补这句，模型会因为这一章只有冲突就判定
   「没说他们是什么关系」，而那正是贾政那条丢掉的方式。

⚠️ **这一版只管以后抽的章。** 已经落库的那 133 条不会自己变好，要重抽才有用，
而重抽 158 章是一次真花钱的动作——由维护者决定什么时候跑、跑哪几章。

── v11（2026-09-10）：`location` 那一档补同样的一段 ────────────────────────────

v10 试跑三章，第 2 章当场 FAILED：

    state_updates.0: invalid fields for location state update
    state_updates.1: invalid fields for location state update

`RawStateUpdate.require_kind_shape` 对 location 的要求是「**必须有 object，且 dimension
和 value 必须为空**」，而模型把地点写进了 `value`。**这是和 v10 那条一模一样的病**：
四种 `kind` 里只有 `state` 有整段说明，另外三种只有一行 schema，于是模型照着 `state`
的形状（dimension + value）去套 location。v10 只补了 relationship 这一档，location 漏了。

所以 v11 给它补上同一件事：地点写进 `object`、不要 dimension 和 value，并**点名那个
真实发生过的错误**（把地点写进 value 会让整条 update 被丢掉）。

⚠️ **不许在解析处「修字段」把它救回来。** ADR 0044 那条纪律写得很清楚：修字段 /
补逗号 / 失败重试一律不做——那是替模型圆场，会把「这个模型产不出合规 JSON」永久藏
起来。正确的两步是：**改 prompt（这儿）+ 那一章重跑一次**。
"""

_SYSTEM_PROMPT: Final = f"""You extract structured facts from one supplied novel chapter.
Schema version: {ANALYSIS_SCHEMA_VERSION}. Prompt version: {ANALYSIS_PROMPT_VERSION}.

Return JSON only: one JSON object, with no Markdown fences and no prose before or after it.
Use exactly these top-level keys: events, state_updates, character_profiles.

Extract events at story-beat granularity. Return 1-12 events, never scene-sized summaries.

Every event is exactly this object:
{{"summary": string, "quote": string, "participants": [surface names],
  "knowers": [surface names], "confidence": number from 0 through 1}}

Every state update is exactly this object:
{{"kind": "location" or "state" or "relationship" or "death", "subject": surface name,
  "object": surface name or null, "dimension": string or null,
  "value": string or null, "quote": string, "confidence": number from 0 through 1}}
The "kind" field is required. Use exactly one of these four shapes:
- "kind": "location": subject + object; dimension and value must be null or omitted.
- "kind": "state": subject + dimension + value; object must be null or omitted.
- "kind": "relationship": subject + object + value; dimension must be null or omitted.
- "kind": "death": subject only; object, dimension and value must be null or omitted.
For "kind": "location", the place itself goes in "object" - the name of the room, house,
city or region the subject is now in, written in the chapter's own language. Leave
"dimension" and "value" out entirely; a location update has no attribute name and no
value. Putting the place in "value" is the one mistake that has actually occurred here,
and the whole update is then thrown away.

Use "death" when the chapter states that the subject dies, is killed, or is confirmed
dead. Use it for the death itself, not for someone merely fearing, predicting, or
falsely reporting a death, and not for a character who is only wounded or unconscious.
Do not express a death as a "state" update with a dimension of your own wording.

For "kind": "state", "dimension" names one attribute of the subject that can change as
the story goes on, and "value" is what this chapter says that attribute is now. Write
both in the language of the supplied chapter, never in English unless the chapter itself
is in English: the author reads these words exactly as you write them. Keep "value" to a
few words, not a sentence.

Use one stable dimension name per attribute and reuse that same name in every chapter, so
that a later value replaces the earlier one instead of standing beside it. Report these
attributes whenever the chapter states them, each named in the chapter's own language:
age, injury or health, cultivation level or power rank, techniques or skills the subject
has mastered, office or rank or title, allegiance, wealth or resources, what the subject
carries or wears, marriage or romantic status, remaining lifespan, and the goal the
subject is pursuing. Open a dimension outside this list when the chapter tracks something
else about the subject; do not stretch one of these to cover it.

Report only what this chapter states or shows. Never infer an attribute the chapter does
not support, and never fill one in from your own knowledge of the world or genre.

For "kind": "relationship", "value" names **what these two people are to each other** -
the standing tie between them, the answer to "who is he to her". Kinship (father, mother,
son, elder brother, aunt), marriage and romance (wife, betrothed, lover, the one he
secretly loves), household and work ties (master and servant, teacher and disciple,
sworn brothers, subordinate), and standing stances that persist beyond this chapter
(sworn enemy, ally, rival) all belong here. Write it in the chapter's own language, in a
few words, and use the same wording every time you report the same tie, so that a later
chapter does not stand a second name for it beside the first.

**Do not put this chapter's happenings in "value".** These are the mistakes to avoid,
each of which has actually been produced: a momentary feeling or reaction ("scolds him",
"disappointed in him", "grateful"), a single act ("gave him silver and cloth", "let him
come along", "traded a lead for time"), a form of address ("calls him third brother"),
an intention or plan ("must show his hand now"), or a whole clause describing the scene.
Those either belong to an event, or to nothing at all. If the chapter shows two people
interacting but never says what they are to each other, report no relationship for them.

Report an enduring tie the first time the chapter makes it plain, even when the chapter
is only about a quarrel: a father scolding his son still establishes father and son.

Three things are not "state" updates: who the subject is involved with is a
"relationship" update with that person as "object"; where the subject is, is a "location"
update; and traits that do not change - gender, personality, upbringing - belong in
"character_profiles" instead.
Return no more than 24 state updates.

Every event and state update must include a verbatim quote of no more than 120
characters copied from the supplied chapter. Never paraphrase a quote. There is no
minimum quote length: short verbatim utterances are fine. Quotes shorter than 10
characters may make up at most 30% of the chapter's quotes (at least one); otherwise
quote a longer verbatim sentence from the chapter that contains the utterance.

Use surface names from the chapter for participants, knowers, profile surfaces, subjects,
and objects. Never invent or return business IDs, chapter identifiers or numbers, scope,
status, evidence identifiers, or any other server-owned field.

A character profile is exactly this object:
{{"surface": string, "gender": string or null, "personality": string or null,
  "background": string or null, "character_notes": string or null,
  "confidence": number from 0 through 1}}
"""


class AnalysisMessage(TypedDict):
    role: Literal["system", "user"]
    content: str


KNOWN_DIMENSIONS_HEADER: Final = (
    "This book already uses these state dimension names, most-used first. "
    "Before you name a dimension, check this list: if one of them already means the "
    "attribute you are about to report, reuse that exact name. Only open a new name "
    "when none of them covers it."
)
"""复用已有字段名那一段的抬头。**这是唯一的字段名收敛机制**（作者 2026-09-06 裁定：
不给固定字段表——小说的状态种类本来就无穷）。"""


def build_analysis_messages(
    chapter_text: str, known_dimensions: Sequence[str] = ()
) -> list[AnalysisMessage]:
    """返回确定性的消息序列，章节原文逐字节作为文本包含在内。

    `known_dimensions` = 这本书已经用过的状态字段名（用得多的在前，由
    `SqliteStoryGraph.state_dimension_names` 给）。空的时候**一个字节都不多发**——
    第一章那次没得复用，而那时的消息序列必须和 2026-09-06 之前逐字节相同。

    ── ⚠️ 它进消息，但**不进 `prompt_hash`** ────────────────────────────────
    这一段的内容随图变化（别的章一抽完，这本书就多几个字段名）。而 `prompt_hash`
    是**运行身份**：`extraction_run` 的唯一键靠它认「同一章同一个问题只跑一条 run」，
    `runner.run` 还拿它做 `PROMPT_DRIFT` 检查（排队时和执行时的 prompt 必须一致）。
    把随图变化的东西算进去，后果是三条同时发作：
      · 每加一个字段名 → 旧 run 的 hash 对不上 → 建新 run → **同一章重新付一次钱**；
      · 排队到执行之间图一定会变（别的章在并行抽）→ **每一条 run 都死在 PROMPT_DRIFT**；
      · 而这两条都不是「抽取该重跑」的正当理由——章还是那一章，问题还是那个问题。
    所以身份由 `identity_prompt_bytes()` 算（只含系统提示 + 本章正文），
    实际发出去的字节由 `AnalysisRequest.prompt_bytes` 记进审计（`in_artifact`）。
    **两个哈希故意不相等**，各答一个问题：一个是「这是同一个问题吗」，
    一个是「那一次到底发了什么」。
    """
    messages: list[AnalysisMessage] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if known_dimensions:
        # 单独一条消息，不拼进系统提示：系统提示逐字节稳定，前缀缓存才命中得上
        # （ADR 0019 边界六那条纪律的同一个道理）。
        messages.append(
            {
                "role": "system",
                "content": KNOWN_DIMENSIONS_HEADER + "\n" + "\n".join(known_dimensions),
            }
        )
    messages.append({"role": "user", "content": chapter_text})
    return messages
