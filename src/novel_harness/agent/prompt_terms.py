"""模式二：人设 + 全部工具 schema 的双语化（国际化第三批）。

**为什么不是 `draft/prompt_terms.py` 那张表**：那张表管的是普通函数的返回值，
按 `language` 参数现算现返回——`assemble()` 每次调用都重新拼一遍字符串。这里
不行：每个工具的 `args=` 都是 Pydantic 类，docstring 和 `Field(description=...)`
在**类定义那一刻**就定死了，不会因为调用方传了个 `language` 就变。

所以这里走另一条路：**Pydantic 类一个字都不改**（永远是中文，`model_json_schema()`
对中文书原样直出——ZH 因此自动逐字节不变，不用另外验）；`tool_declarations()`
按 `language` 决定要不要对生成出来的 JSON 做一次**递归替换**：把每一处
`"description"` 键的值，按下面这张表从中文换成对应英文。

`_EN` 的键是**当前中文原文本身**，不是一个符号名。这是有意的：改了中文一处
忘了同步这张表，替换时那一处的键就对不上，`translate_tool_declarations()`
会在那个位置直接 `KeyError`——比"悄悄漏一句中文出去"这种失败要吵得多，
和 `tests/test_agent_tools.py::test_no_tool_schema_still_talks_about_secrets`
钉的那类缝是同一个精神。
"""

from __future__ import annotations

from typing import Any

from ..draft.length import DraftLanguage

_EN: dict[str, str] = {
    # ── scene_constraints ────────────────────────────────────────────────
    "查第 N 章正文里数出来谁在场。": "Check who's present, as counted from chapter N's text.",
    "查「第 N 章正文里数出来谁在场」。": 'Query "who\'s present, as counted from chapter N\'s text."',
    "要查第几章（AS OF 第几章，纯查询坐标，不会写进任何数据）。": (
        "Which chapter to query as of (a pure query coordinate — "
        "this never writes to any data)."
    ),
    # ── character_state ──────────────────────────────────────────────────
    (
        "查某个人在第 N 章的处境：在哪、各状态维度的值、登场了没有、是不是已经死了。"
        "**只认角色册上的名字**（book_index 里那份）：不在上面的人查不到，"
        "而那不是你说法不对——换个叫法再查一次也还是查不到，只是白花一步。"
    ): (
        "Check where someone stands as of chapter N: where they are, "
        "their state-dimension values, whether they've appeared yet, whether "
        "they're already dead. **Only recognizes names from the roster** "
        "(the one in book_index): if someone isn't on it, the query fails "
        "— and that's not you phrasing it wrong; a different name won't "
        "help either, it's just a wasted step."
    ),
    "查「某个人在第 N 章的处境」。": 'Query "where someone stands as of chapter N."',
    "要查第几章（AS OF 第几章，纯查询坐标）。": (
        "Which chapter to query as of (a pure query coordinate)."
    ),
    "人物的称呼，用作者在正文里的那个叫法。有歧义的叫法会被拒绝。": (
        "The character's name, using whatever the author calls them in "
        "the prose. An ambiguous name is refused."
    ),
    # ── draft_chapter（ADR 0047：chapter + brief + materials）─────────────
    (
        "起草第 N 章的一稿。**这一步不动书**：稿子存在一边，返回里给你它的编号、"
        "字数、开头的一段，以及写它的那个模型自己说的一句话。\n"
        "写之前先做三件事：分析作者的意图（写哪一章、新写还是重写、牵涉谁、牵涉哪几章）；"
        "由粗到细查资料（目录 → 总结 → 事件 → 角色卡 → 原文，只在需要细节时读原文，"
        "有目的地挑）；对照规矩（validation_rules 里的检验规则、作者交代过的有时限的规矩、"
        "你查到的事实矛盾）。然后把结论写进 brief，把写手够不着的远章资料放进 materials。\n"
        "写手自己有：文风、禁用字、这一章在场人物的角色卡、他们最近的事件、最近几章的总结、"
        "上一章结尾和这一章当前正文——**这些不用你抄进来**。\n"
        "方向清楚就写一稿、接着调 save_draft 存进去（不用问作者，他随时能退回去）；"
        "方向不清楚就一次要几稿（brief 各不同），把它们的自述摆给作者挑——"
        "**同一批里的几稿会同时写**，不比一稿慢多少。"
    ): (
        "Draft one version of chapter N. **This step does not touch the "
        "book**: the draft is set aside, and the return gives you its id, "
        "word count, an opening excerpt, and a note the model that wrote it "
        "left about itself.\n"
        "Do three things first: analyse what the author wants (which "
        "chapter, new or rewrite, who's involved, which chapters it "
        "touches); look things up from coarse to fine (index → summaries → "
        "events → character cards → text, reading the text only when you "
        "need detail, picking deliberately); check the rules (the check "
        "rules in validation_rules, the timed rules the author gave you, "
        "any factual clashes you found). Then put your conclusions in brief "
        "and the far-chapter material the writer can't reach in materials.\n"
        "The writer already has: the style, the forbidden words, the cards "
        "of the characters present in this chapter, their recent events, "
        "the last few chapters' summaries, the end of the previous chapter "
        "and this chapter's current text — **don't copy those in**.\n"
        "If the direction is clear, write one draft and follow up with "
        "save_draft (no need to ask the author, they can always revert it); "
        "if it's unclear, request several drafts at once (different briefs) "
        "and lay their notes in front of the author to choose — **drafts in "
        "the same batch are written concurrently**, barely slower than one."
    ),
    (
        "起草第 N 章的一稿（**chapter + brief + materials**，ADR 0047）。\n\n"
        "**这里没有、也永远不会有约束字段**（ADR 0019 边界二的另一半）：在场是后端从正文数的，\n"
        "文风 / 禁用字 / 角色卡 / 最近事件 / 最近总结 / 正文那六格是后端固定装配的——助手一个字\n"
        "插不进去。它能给的只有两格：**要写什么**（`brief`）和**写手固定装配够不着的资料**\n"
        "（`materials`）。两格都是纯文本、都跟着稿子存进候选表让作者看得见。\n\n"
        "它和 `DraftFn` 放在一起而不是和别的工具入参放在一起，是因为它是**注入契约的一半**：\n"
        "起草侧收的就是 `(DraftAsk, DraftContext)`。"
    ): (
        "Draft one version of chapter N (**chapter + brief + materials**, "
        "ADR 0047).\n\n"
        "**There is no constraints field here, and there never will be** "
        "(the other half of ADR 0019 boundary 2): who's present is counted "
        "from the text by the backend, and the six slots — style / forbidden "
        "words / character cards / recent events / recent summaries / text — "
        "are assembled by the backend; the assistant can't insert a word. It "
        "gives only two things: **what to write** (`brief`) and **material "
        "the writer's fixed assembly can't reach** (`materials`). Both are "
        "plain text and both are stored with the draft so the author can "
        "see them.\n\n"
        "It sits alongside `DraftFn` rather than with other tools' args "
        "because it is **half of an injection contract**: the drafting side "
        "receives exactly `(DraftAsk, DraftContext)`."
    ),
    "起草第几章。在场人物、文风、角色卡、最近的事件和总结由后端按这个章号自己装。": (
        "Which chapter to draft. Who's present, the style, character cards, "
        "recent events and summaries are assembled by the backend for this "
        "chapter number."
    ),
    (
        "这一稿要做什么、要守什么——用你自己的话，对着写手说。写之前先分析作者的意图、"
        "由粗到细查过资料、对照过检验规则和作者交代过的规矩，再把结论写在这儿："
        "写哪一段、从哪儿接、谁在场、要避开什么（比如「裕王第 154 章已死，不能当活人写」）、"
        "视角 / 语气 / 收在哪儿。"
    ): (
        "What this draft should do and what it must respect — in your own "
        "words, addressed to the writer. Before writing, analyse what the "
        "author wants, look things up from coarse to fine, and check the "
        "check rules and the rules the author gave you; then put the "
        "conclusions here: which passage, where it picks up, who's present, "
        "what to avoid (e.g. \"Prince Yu died in chapter 154, don't write him "
        "as alive\"), point of view / tone / where it ends."
    ),
    (
        "写手够不着的资料，每条一段：远章的总结、关键事件、原文节选。写手自己只有"
        "最近几章的总结和这些人最近的事件——牵涉更早的章、需要细节的地方，把你查到的"
        "那几段挑出来放在这儿。**有目的地挑，不是整本塞进来**：有预算上限，装不下从后往前砍。"
    ): (
        "Material the writer can't reach, one paragraph per item: summaries "
        "of far chapters, key events, excerpts of the text. The writer only "
        "has the last few chapters' summaries and these characters' recent "
        "events — where earlier chapters are involved or detail is needed, "
        "pick out the pieces you found and put them here. **Pick "
        "deliberately, don't dump the whole book**: there is a budget cap; "
        "whatever doesn't fit is trimmed from the end."
    ),
    # ── book_index ───────────────────────────────────────────────────────
    (
        "全书目录：章标题一览 + 角色册（人物 / 地点 / 门派 / 物件的**显示名**）。"
        "**先调这个再往下钻**，它是最便宜的一层。"
    ): (
        "Book-wide index: chapter titles + the roster (**display names** "
        "of characters / locations / factions / objects). **Call this "
        "before drilling down further** — it's the cheapest layer."
    ),
    "全书目录：章标题 + 角色册。**默认调用不带参数，这是最便宜的那一层。**": (
        "Book-wide index: chapter titles + roster. **Calling it with no "
        "arguments is the default, and the cheapest layer.**"
    ),
    (
        "章标题从第几章开始列（默认第 1 章）。预算装不下整本时返回里会写清楚"
        "给到第几章为止，把这个参数设成下一章再调一次就能接着往下拿。"
    ): (
        "Which chapter to start listing chapter titles from (defaults to "
        "chapter 1). When the budget can't fit the whole book, the return "
        "states exactly which chapter it went up to — set this to the "
        "next chapter and call again to keep going."
    ),
    (
        "只列这几类角色册条目（Character / Location / Faction / "
        "Foreshadow / Object），默认全给。角色册太长被裁掉整整一类时，"
        "用它把那一类单独拉回来。"
    ): (
        "Only list roster entries of these types (Character / Location / "
        "Faction / Foreshadow / Object); all types are given by default. "
        "When the roster is too long and an entire type gets cut, use "
        "this to pull that type back on its own."
    ),
    # ── character_chapters ───────────────────────────────────────────────
    (
        "某几个人出现在哪些章。给两个人就是求交集——「他第一次见她是哪章」问的就是这个，"
        "而这是一次确定性的集合运算，不是搜索。返回两条分开的轴："
        "正文里同时被提到的章、以及同一条已确认事件里同时在场/知情的章。"
        "**每条轴会自己说它瞎没瞎**（读不到正文 / 抽取没跑过），别把 0 当成「没发生过」。"
    ): (
        "Which chapters a few people appear in. Give two people and it's "
        "an intersection — 'which chapter did he first meet her' is "
        "exactly this question, and it's a deterministic set operation, "
        "not a search. Returns two separate axes: chapters where they're "
        "mentioned together in the prose, and chapters where they're "
        "together present/aware in the same confirmed event. **Each axis "
        "reports for itself whether it's blind** (can't read the prose / "
        "extraction hasn't run) — don't read a 0 as 'never happened.'"
    ),
    "某几个人出现在哪些章。**给多个人 = 求交集**（他们同时出现在哪几章）。": (
        "Which chapters a few people appear in. **Multiple people = an "
        "intersection** (which chapters they're all in together)."
    ),
    (
        "人物的称呼，用作者在正文里的那个叫法。给两个就是问「这两个人同时出现在哪几章」——"
        "「萧决第一次见顾清音是哪章」问的就是这个。有歧义的叫法会被拒绝。"
    ): (
        "The characters' names, using whatever the author calls them in "
        "the prose. Give two and it asks 'which chapters do these two "
        "both appear in' — 'which chapter did Xiao Jue first meet Gu "
        "Qingyin' is exactly this question. An ambiguous name is refused."
    ),
    # ── chapter_summaries ────────────────────────────────────────────────
    (
        "指定章号区间的章节摘要（每章一段，机器生成的背景，不是作者确认的事实）。"
        "区间由你给——先用 book_index / character_chapters 定位到大概哪一段，再拉这一段。"
        "返回会明说**哪几章有正文却没生成过摘要**（索引在那几章是瞎的），"
        "以及区间太长时哪一段没给。"
    ): (
        "Chapter summaries for a given chapter range (one paragraph per "
        "chapter, machine-generated background, not facts confirmed by "
        "the author). You supply the range — use book_index / "
        "character_chapters first to narrow down roughly where, then "
        "pull that range. The return states plainly **which chapters "
        "have prose but no generated summary yet** (the index is blind "
        "there), and which part of the range wasn't given if it's too "
        "long."
    ),
    "指定章号区间的滚动总结。**区间由你给**——你从 L0/L1 已经知道大概在哪儿了。": (
        "Rolling summaries for a given chapter range. **You supply the "
        "range** — you already know roughly where from L0/L1."
    ),
    "区间下界（含）。": "Lower bound of the range (inclusive).",
    "区间上界（含）。": "Upper bound of the range (inclusive).",
    # ── chapter_text ─────────────────────────────────────────────────────
    (
        "读一整章的正文原文，**最贵的一层**，确定要看哪一章之后再调。"
        "正文从磁盘上的稿子读，也就是作者此刻看见的那一份。"
    ): (
        "Read the full raw prose of one chapter — **the most expensive "
        "layer**; call it only once you're sure which chapter you need. "
        "The prose is read from the manuscript on disk, i.e. exactly "
        "what the author sees right now."
    ),
    "一章正文。**最贵的那一层**，钻到这儿之前先用上面三层定位。": (
        "One chapter's prose. **The most expensive layer** — use the "
        "three layers above to locate it before drilling down here."
    ),
    "读第几章的正文。": "Which chapter's prose to read.",
    # ── save_draft / read_draft ──────────────────────────────────────────
    (
        "把某一稿写进它那一章，**不用问作者**——他随时能在版本历史里退回去。"
        "只收稿子的编号：你没法拿别的文本去盖一章。"
        "返回里会说清楚存没存进去：作者在这中间改过那一章、或者那一章还不存在"
        "（新的一章要他自己起标题），都不会覆盖，那时把稿子读给他听、让他决定。"
    ): (
        "Write one draft into its chapter — **no need to ask the "
        "author**, they can always revert it in version history. Only "
        "takes the draft's id: you have no way to overwrite a chapter "
        "with any other text. The return states clearly whether it "
        "saved: if the author changed that chapter in the meantime, or "
        "the chapter doesn't exist yet (a new chapter needs them to "
        "title it themselves), it will not overwrite — in that case, "
        "read the draft to them and let them decide."
    ),
    (
        "按编号把某一稿的全文读回来。**很贵**（一整章会一直留在你的上下文里），"
        "只在真的要动那些字的时候调——比如作者说「把第一稿的开头接第二稿的结尾」。"
        "只想知道是哪一版的话，起草时给过的那句自述和开头一段就够了。"
    ): (
        "Read a draft's full text back by id. **Expensive** (a whole "
        "chapter will sit in your context from then on) — call it only "
        "when you genuinely need to work with those words, e.g. the "
        "author says 'take the opening of draft one and follow it with "
        "the ending of draft two.' If you just want to know which "
        "version is which, the note and opening excerpt already given "
        "at drafting time are enough."
    ),
    (
        "认一稿：**只有一个候选 id，没有别的**。\n\n"
        "`save_draft` 和 `read_draft` 共用它，而这个形状本身就是那道闸：\n"
        "**模型交不出一段正文**，它只能指着后端刚生成的某一稿说「这个」。\n"
        "合成一个「写正文（收 text）」的工具就是把 ADR 0019 边界一最硬的那半条拆掉——\n"
        "那时模型能拿任何一段字去盖作者的书。"
    ): (
        "Identify one draft: **only a candidate id, nothing else.**\n\n"
        "`save_draft` and `read_draft` share it, and this shape is "
        "itself the gate:\n"
        "**the model cannot hand over a piece of prose** — it can only "
        "point at a draft the backend just generated and say 'this "
        "one.'\n"
        "Assembling a 'write prose (takes text)' tool would tear out "
        "the hardest half of ADR 0019 boundary one — the model could "
        "then overwrite the author's book with any text at all."
    ),
    "哪一稿（起草时返回的那个编号）。**不要把这个编号念给作者听。**": (
        "Which draft (the id returned at drafting time). **Never read "
        "this id aloud to the author.**"
    ),
    # ── ask_author ───────────────────────────────────────────────────────
    (
        "停下来问作者一句，给他几个能直接点的选项。**说完这一句你这一轮就结束了**，"
        "他答完你会看见他的回答——所以别在同一轮里又问又猜。\n"
        "**什么时候问**：只在「不问就得猜，而猜错了他看不出来」的时候。"
        "那种事只有一类——答案在他脑子里，书里查不到："
        "这一场他想不想让某个人知道那件事、这条线往哪个方向收、两个版本里他要哪一个。\n"
        "**什么时候不问**：书里查得到的（谁在哪、哪章说过什么、上一章怎么写的）自己去查；"
        "改一改就能重来的小事（语气、长短）自己定，写完他看得见。"
        "每一轮都问他一句，等于把想事情这件事退回给他。\n"
        "**叫这个工具的同一步里，先用一句话交代背景**（你查到了什么、卡在哪儿）："
        "那句话进对话记录，问句和选项另外摆成一张卡。"
    ): (
        "Stop and ask the author one question, with a few options they "
        "can click directly. **Your turn ends the moment you say this** "
        "— once they answer, you'll see their reply, so don't ask and "
        "guess in the same turn.\n"
        "**When to ask**: only when 'not asking means guessing, and a "
        "wrong guess wouldn't show.' That's exactly one kind of thing — "
        "the answer lives in their head, not in the book: whether they "
        "want a character to learn something in this scene, which way a "
        "thread should resolve, which of two versions they want.\n"
        "**When not to ask**: anything the book can answer (who's "
        "where, what a chapter said, how the last chapter was written) "
        "— go look it up yourself; small things that can be redone with "
        "an edit (tone, length) — decide yourself, they'll see it once "
        "it's written. Asking every single turn just hands the thinking "
        "back to them.\n"
        "**In the same step you call this tool, first give one sentence "
        "of context** (what you found, where you're stuck): that "
        "sentence goes into the conversation record; the question and "
        "options are laid out separately as a card."
    ),
    "停下来问作者一句。**这不是一次查询，它没有章号**（见模块 docstring「问作者」）。": (
        "Stop and ask the author one question. **This is not a query — "
        "it has no chapter number** (see the module docstring's 'Asking "
        "the author' section)."
    ),
    "问他的那一句话，一句就够。用他自己的说法（人物名、场景），不要提工具名和编号。": (
        "The question to ask them, one sentence is enough. Use their "
        "own terms (character names, the scene) — don't mention tool "
        "names or ids."
    ),
    (
        "几个他能直接点的答案，每个都短。**它们是几条不同的走法，不是同一条的复述。**"
        "他也可以不点、直接说别的。"
    ): (
        "A few short answers they can click directly. **These are "
        "distinct paths, not restatements of the same one.** They can "
        "also skip them and say something else entirely."
    ),
    # ── remember_rule ────────────────────────────────────────────────────
    (
        "作者提了一条**怎么写**的要求（「别写打斗」「冷一点」「男主在这片沙地不杀人」），"
        "把它记下来。记下的那条以后每一轮你都看得见。\n"
        "**把「什么时候不算数了」写进那句话里**，用他自己的说法。"
        "他说「男主在这片沙地别杀人」，就照这么记——你以后读到它，"
        "自己看男主还在不在那片沙地；不在了就当它不存在，不必守。"
        "系统**不会**替你按章号把它作废（那是 2026-08-14 撤掉的机制）："
        "一条规矩活多久，由你每次读到它时判断。\n"
        "**只记怎么写，不记书里发生了什么**：人物、关系、谁知道什么那些是书里的事实，"
        "有它们自己的地方，从这儿进去只会变成一句谁也查不到的话。\n"
        "**同一条要用一模一样的措辞**：换个说法就成了两条，两条都会跟着你走。\n"
        "**没有章号这个参数**：记在第几章由这本书此刻打开的地方决定，你传不进来，"
        "他也不填。他还没停在任何一章上时这一次会被退回来——那时先接着聊，别硬记。\n"
        "**别每句话都记**：他随口的一句评价不是规矩，记多了等于给他攒了一堆他不知道"
        "自己定过的规矩——而他看不见这张单子。"
    ): (
        "The author gave a **how-to-write** requirement ('no fight "
        "scenes,' 'colder tone,' 'the male lead doesn't kill anyone on "
        "this stretch of sand') — write it down. What you write down, "
        "you'll see on every turn from now on.\n"
        "**Write 'when this stops applying' into that same sentence**, "
        "in their own words. They said 'don't have the male lead kill "
        "anyone on this stretch of sand' — record it exactly that way. "
        "From then on, when you read it back, judge for yourself "
        "whether the male lead is still on that stretch of sand; once "
        "he isn't, treat it as gone, no need to honor it. The system "
        "**will not** expire it by chapter number for you (that "
        "mechanism was removed on 2026-08-14): how long a rule lives is "
        "something you judge every time you read it.\n"
        "**Record only how-to-write, never what happened in the book**: "
        "characters, relationships, who knows what — those are facts "
        "about the book with their own place; going through here just "
        "turns them into a line no one can look up.\n"
        "**The exact same rule must use the exact same wording**: "
        "phrase it differently and it becomes two rules, and both will "
        "keep following you.\n"
        "**There is no chapter-number parameter**: which chapter it's "
        "recorded under is decided by wherever this book is currently "
        "open — you cannot pass it in, and neither does the author fill "
        "it in. If they haven't landed on any chapter yet, this call is "
        "refused — in that case, keep talking first, don't force a "
        "recording.\n"
        "**Don't record every sentence**: an offhand remark from them "
        "isn't a rule; recording too much just piles up a stack of "
        "rules they didn't know they'd set — and they can't see this "
        "list."
    ),
    (
        "记下作者刚定的一条规矩：**那句话 + 它管到什么时候。没有章号。**\n\n"
        "`until` 是 2026-08-15 加的（迁移 016）。作者的原话：「你在写某一章的时候用户讲过的\n"
        "规则，然后**你规定的时效**什么的都可以记一下」。**「你规定的」三个字定死了它的形状**：\n"
        "时效得是模型明确交出来的一个字段，埋在 `rule` 那句话里就没有可以记下来的东西。\n\n"
        "**它仍然不是章号**（约束 10）：模型写的是一句人话（「男主走出这片沙地为止」），\n"
        "引擎一个字都不解析，也永远不会把它换算成一个数。"
    ): (
        "Record a rule the author just set: **the sentence itself + "
        "when it stops applying. No chapter number.**\n\n"
        "`until` was added 2026-08-15 (migration 016). The author's own "
        "words: 'the rule the user mentioned while writing some "
        "chapter, and then **the expiry you decide** can also be "
        "recorded.' **Those three words, 'you decide,' fixed its "
        "shape**: the expiry has to be a field the model hands over "
        "explicitly — buried inside the `rule` sentence, there'd be "
        "nothing separate to record.\n\n"
        "**It is still not a chapter number** (constraint 10): what the "
        "model writes is a sentence in plain language ('until the male "
        "lead walks off this stretch of sand'); the engine never "
        "parses a single character of it, and never will convert it "
        "into a number."
    ),
    "他要的那一句，用他自己的说法，一句话。太长会被退回来让你压短。": (
        "The sentence they want, in their own words, one sentence. Too "
        "long and it's refused for you to shorten."
    ),
    (
        "**这条什么时候就不算数了**，用一句人话写清楚。比如「男主走出这片沙地为止」"
        "「这一场打完」「他和师父摊牌之前」「整本书都这样」。\n"
        "说不清就写「整本书都这样」——**别硬编一个条件**，编出来的条件下一轮是你自己在读。\n"
        "这句话你以后每一轮都会连着规矩一起看到，**它是你判断这条还作不作数的依据**；"
        "作者也会在那张表里看到它。"
    ): (
        "**When this stops applying**, spelled out in plain language. "
        "For example 'until the male lead walks off this stretch of "
        "sand,' 'once this fight scene is over,' 'before he confronts "
        "his master,' 'holds for the whole book.'\n"
        "If it's unclear, write 'holds for the whole book' — **don't "
        "invent a condition**: whatever condition you invent, you're "
        "the one reading it back next turn.\n"
        "You'll see this sentence alongside the rule on every turn from "
        "now on — **it's what you use to judge whether the rule still "
        "applies**; the author will also see it in that table."
    ),
    # ── get_result ───────────────────────────────────────────────────────
    (
        "把这一轮里**已收起**的某一条工具结果按编号取回来（编号和一行描述见上下文"
        "末尾的「已收起的结果」清单），或把**被压成摘要的对话块**按编号取回整块原文"
        "（编号见「更早的对话」清单，取块要传 kind=\"block\"）。只读，不重新执行任何查询。\n"
        "**取回的内容会重新占用上下文**：编号对应的内容之前被剪掉正是因为装不下，"
        "取回来之前系统会先量一次——装不下会拒绝并告诉你差多少，那时用 "
        "get_result(id, max_units=N) 只取一部分，或把要问的说得短一点，"
        "或重查一个更小的范围。\n"
        "**编号只活这一轮**：换了一轮就按正常方式重新查。"
    ): (
        "Retrieve a **collapsed** tool result from this turn by its id "
        "(see the 'collapsed results' list at the end of the context "
        "for ids and one-line descriptions), or retrieve the full "
        "original text of a **conversation block that was compressed "
        "into a summary** by its id (see the 'earlier conversation' "
        "list for ids; retrieving a block requires passing "
        "kind=\"block\"). Read-only — it never re-runs any query.\n"
        "**What you retrieve takes up context space again**: the "
        "content behind an id was cut precisely because it didn't fit "
        "— before handing it back, the system measures it first; if it "
        "still doesn't fit, the call is refused and told by how much. "
        "In that case, use get_result(id, max_units=N) to take only "
        "part of it, phrase your question shorter, or re-query a "
        "smaller range.\n"
        "**Ids only live for this turn**: once the turn changes, query "
        "normally again."
    ),
    (
        "`get_result` 的入参：**这一轮里的编号**（见投影末尾「已收起的结果」清单）。\n\n"
        "编号只活这一轮（同 `TurnMemo` 的生命周期），跨轮不承诺稳定——下一轮要内容就\n"
        "正常重查，那才是拿得到当前版本的路径。"
    ): (
        "`get_result`'s input: **an id from this turn** (see the "
        "'collapsed results' list at the end of the projection).\n\n"
        "Ids only live for this turn (same lifetime as `TurnMemo`) — no "
        "stability is promised across turns; if the next turn needs the "
        "content, query normally again, that's the path that gets you "
        "the current version."
    ),
    "「已收起的结果」清单里的编号。": "The id from the 'collapsed results' list.",
    "最多取回多少个字（超出会截断并标注）。上下文很紧时用它买得起一部分；不传 = 整份取回。": (
        "The most units to retrieve (anything beyond this is truncated "
        "and flagged). Use it to afford part of the content when "
        "context is tight; omit it to retrieve the whole thing."
    ),
    # ── check_track ──────────────────────────────────────────────────────
    (
        "拿第 N 章**已经落盘的正文**去跟后面那些已经写完的章对一遍，"
        "看有没有跟既有设定抵触。**只告警，不阻断**——改不改你自己定。\n"
        "**什么时候用**：你在改一章旧的（后面还有已经写完的章）。"
        "在最前沿写时它一步都不走，会直接告诉你「这儿是最前沿」。\n"
        "**它给你的是三个数**：第几句、跟第几章抵触、哪一类抵触。"
        "**不会告诉你后面那几章写了什么**——那是这一章的读者还不该知道的东西，"
        "拿到了你也不许写进正文。要细节请作者自己去翻那一章。\n"
        "空清单要连着那句话一起读：「没抵触」和「压根没核对」不是一回事。"
    ): (
        "Take chapter N's **prose as already saved to disk** and check "
        "it once against the chapters already written after it, looking "
        "for conflicts with existing settings. **Warns only, never "
        "blocks** — whether to change anything is your call.\n"
        "**When to use it**: you're revising an old chapter (there are "
        "already-written chapters after it). At the frontier, it does "
        "nothing at all and simply tells you 'this is the frontier.'\n"
        "**What it gives you is three numbers**: which sentence, which "
        "chapter it conflicts with, and what kind of conflict. **It "
        "will not tell you what those later chapters say** — that's "
        "something a reader of this chapter shouldn't know yet, and you "
        "may not write it into the prose even once you have it. For "
        "details, have the author go read that chapter themselves.\n"
        "Read an empty list together with that sentence: 'no conflict' "
        "and 'never checked at all' are not the same thing."
    ),
    (
        "拿这一章已经落盘的正文去跟**后面那些已经写完的章**对一遍（轨道阶段 3）。\n\n"
        "── 为什么只收一个章号 ────────────────────────────────────────────────\n\n"
        "ADR 0019 边界二：**模型没有机会影响这个工具的任何一个实质入参。** 正文由后端从\n"
        "磁盘读当前快照，轨道由后端算——模型递不进来一段自己编的正文，也指不定要跟哪几章比。\n"
        "它能决定的只有「问不问、问哪一章」，那正是「循环归模型」该有的那点自由。"
    ): (
        "Take this chapter's prose as already saved to disk and check "
        "it once against **the chapters already written after it** "
        "(Track, phase 3).\n\n"
        "── Why it only takes a chapter number ──────────────────────\n\n"
        "ADR 0019 boundary 2: **the model has no opportunity to "
        "influence any substantive input to this tool.** The prose is "
        "read by the backend from the current snapshot on disk, and "
        "Track is computed by the backend — the model cannot hand in a "
        "piece of prose it made up, nor point at which chapters to "
        "compare against. All it can decide is 'ask or not, ask about "
        "which chapter' — exactly the freedom 'the loop belongs to the "
        "model' should leave it."
    ),
    "要核对第几章。用它**已经落盘的当前正文**，不是你手里这一稿。": (
        "Which chapter to check. Uses its **current prose as already "
        "saved to disk**, not the draft you're holding."
    ),
    # ── character_card（2026-09-12，`agent/panels.py`）──────────────────────
    (
        "翻一个人的角色卡：右栏「角色册」里点开他看到的那一页——基本信息（性别 / 性格 / "
        "背景 / 备注）、别名、第 N 章时的处境（在哪、各状态、死没死）、"
        "他和谁有什么关系、他经历过的事（按章号，只有已确认的）。"
        "**只认角色册上的名字**（book_index 里那份）。要看某一章里发生了什么，"
        "用 chapter_events；只想知道他此刻的处境，character_state 更便宜。"
    ): (
        "Open a character's card: the page you see when you click them in "
        "the Roster panel on the right — basic info (gender / personality / "
        "background / notes), aliases, where they stand as of chapter N "
        "(where they are, each state, dead or not), who "
        "they're related to and how, and what they've been through (by "
        "chapter, confirmed events only). **Only recognizes names from the "
        "roster** (the one in book_index). To see what happens in a given "
        "chapter, use chapter_events; if you only need where they stand "
        "right now, character_state is cheaper."
    ),
    "查「一个人的角色卡」：右栏「角色册」里点开一个人看到的那一页。": (
        'Query "a character\'s card": the page you see when you click someone '
        "in the Roster panel on the right."
    ),
    "按第几章的时点看他的处境和关系（AS OF 第几章，纯查询坐标）。": (
        "Which chapter to view their state and relationships as of (a pure "
        "query coordinate)."
    ),
    # ── chapter_events ───────────────────────────────────────────────────
    (
        "看某几章的事件：右栏「事件」那一栏——这几章里已经确认、写进书里的每一条情节，"
        "带章号、在场的人和知道这件事的人。**只有已确认的**：抽取出来还没被作者确认的"
        "不在这里（那些在 notifications 里等他）。空清单要连着 notes 一起读："
        "「这几章没整理过」和「这几章没事发生」不是一回事。"
    ): (
        "See a few chapters' events: the Events panel on the right — every "
        "confirmed event written into the book for these chapters, with the "
        "chapter number, who was present and who knows about it. **Confirmed "
        "only**: extracted events the author hasn't confirmed yet aren't here "
        "(those wait for them in notifications). Read an empty list together "
        'with the notes: "these chapters haven\'t been processed" and "nothing '
        'happens in these chapters" are not the same thing.'
    ),
    (
        "查「这几章的事件」：右栏「事件」那一栏——已经确认、写进书里的情节，按章列。\n"
        "**区间由你给**：面板上是到当前章为止的全部，一次问整本书装不下。"
    ): (
        'Query "these chapters\' events": the Events panel on the right — the '
        "confirmed events written into the book, listed by chapter.\n"
        "**You give the range**: the panel shows everything up to the current "
        "chapter, and the whole book won't fit in one call."
    ),
    "区间上界（含）。只看一章就两个都填它。": (
        "Upper bound of the range (inclusive). To see one chapter, set both to it."
    ),
    # ── validation_rules ─────────────────────────────────────────────────
    (
        "看检验规则：右栏「检验规则」那一栏——作者给这本书定的规则（正文里不许出现的字，"
        "含已停用的），以及某一章最近一次检验的结果（命中了第几段、哪一句）。"
        "**只读**：跑检验是作者在界面上按的，这儿不跑。"
    ): (
        "See the check rules: the Rules panel on the right — the rules the "
        "author set for this book (text that must not appear in the prose, "
        "disabled ones included), plus the latest check result for a chapter "
        "(which paragraph and sentence it hit). **Read-only**: running a check "
        "is something the author does from the interface; this doesn't run it."
    ),
    "查「检验规则」：右栏那一栏——作者给这本书定的规则，以及某一章最近一次检验的结果。": (
        'Query "check rules": the panel on the right — the rules the author set '
        "for this book, plus the latest check result for a chapter."
    ),
    "顺带看这一章最近一次检验的结果；不传就只列规则。": (
        "Also show this chapter's latest check result; omit it to list only "
        "the rules."
    ),
    # ── notifications ────────────────────────────────────────────────────
    (
        "看通知：右栏「通知」那一栏——系统留给作者的提醒（后台没办成的事、检查命中、"
        "总结和正文对不上……）和等他确认的提案（抽出来的设定跟现有的对不上、待确认的情节）。"
        "返回先给一张按章数出来的表，再给最近的那些条目；传 chapter 只看某一章的。"
        "**只读**：处理 / 忽略一条通知是作者在界面上按的，这儿不动。"
    ): (
        "See the notifications: the Notifications panel on the right — "
        "reminders the system left for the author (something that didn't "
        "finish in the background, a check that hit, a summary that doesn't "
        "match the text…) and proposals waiting for their confirmation "
        "(an extracted fact that clashes with an existing one, an event to "
        "confirm). The return gives a per-chapter count table first, then "
        "the most recent entries; pass chapter to see only one chapter's. "
        "**Read-only**: handling or dismissing a notification is something "
        "the author does from the interface; this doesn't touch them."
    ),
    "查「通知」：右栏那一栏——系统留给作者的提醒，以及等他确认的提案。": (
        'Query "notifications": the panel on the right — reminders the system '
        "left for the author, plus proposals waiting for their confirmation."
    ),
    "只看和这一章有关的；不传就看全书的（按章数出来一张表，再给最近的那些）。": (
        "Only entries about this chapter; omit it to see the whole book's "
        "(a per-chapter count table, then the most recent entries)."
    ),
}


def _translate(value: Any) -> Any:
    """递归替换。**只碰 `"description"` 这一个键**，`title`/`enum`/`type` 等结构性
    字段原样放过——Pydantic 自动生成的 `title` 已经是英文（字段名转 Title Case），
    不需要，也不该有第二份翻译。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if key == "description" and isinstance(val, str):
                out[key] = _EN[val]
            else:
                out[key] = _translate(val)
        return out
    if isinstance(value, list):
        return [_translate(item) for item in value]
    return value


def translate_tool_declarations(
    declarations: list[dict[str, Any]], language: DraftLanguage
) -> list[dict[str, Any]]:
    """`tool_declarations()` 的输出按语言过一遍。ZH = 原样返回（不做任何变换，
    所以中文书**逐字节不变**，不需要额外验证）；EN = 递归替换每一处 description。

    缺一条翻译时 `KeyError`——**不静默漏一句中文过去**，同
    `test_no_tool_schema_still_talks_about_secrets` 那条缝的精神：
    宁可当场炸给开发者看，也不要让一句中文混进英文书的 schema 里，
    等某个作者自己发现。
    """
    if language is DraftLanguage.ZH:
        return declarations
    return [_translate(declaration) for declaration in declarations]



# ══════════════════════════════════════════════════════════════════════════
# 国际化第三批（下半）：工具拒绝消息——ToolRefused/SealRefused/DraftRefused
# ══════════════════════════════════════════════════════════════════════════
#
# 这些不进 `tool_declarations()`：它们是**运行时才出现**的消息，只在某个工具真的
# 被拒时才生成一次，不是每一轮都发的稳定前缀/schema。所以不用上面那套"递归替换
# 生成好的 JSON"的机制——这里的每一条本来就是**现算**的（`raise ToolRefused(f"...")`
# 散落在各处的函数体里），直接用模板 + `.format()` 就够。
#
# ⚠️ **判据不是"意思对不对"，是"模型读完会走哪一步"**（2026-08-13 真书事故）：
# 722 章的书给第 723 章起草，连续两次查一个角色册里没有的新角色，两次都收到同一句
# "查无此人，或者这个叫法同时指向好几个人……换一个更具体的称呼"——而这两种情况的
# 正确下一步相反：查无此人时**换什么叫法都没用**，指向好几个人时**换个更具体的
# 称呼恰恰是对的**。合成一句 = 在第一种情况下引擎亲口鼓励模型再烧一步。那一轮八步
# 全花在查询上，一稿都没写出来。`UNKNOWN_CHARACTER`/`AMBIGUOUS_CHARACTER` 这两条
# 翻译时逐字对照过这条区分，不是逐句对意思。

_MESSAGES: dict[str, dict[DraftLanguage, str]] = {
    # ── agent/tools.py ───────────────────────────────────────────────────
    "no_manuscript_root": {
        DraftLanguage.ZH: "读不到这本书的正文目录，起草无法进行。让作者确认项目根目录已经接到工作台上。",
        DraftLanguage.EN: (
            "Can't reach this book's manuscript directory — drafting can't "
            "proceed. Have the author confirm the project root is connected "
            "to the workbench."
        ),
    },
    "not_a_character_state": {
        DraftLanguage.ZH: "「{surface}」不是人物（它是 {label}），没有「处境」可查。",
        DraftLanguage.EN: (
            '"{surface}" is not a character (it\'s a {label}) — there is '
            "no \"state\" to query."
        ),
    },
    "track_not_wired": {
        DraftLanguage.ZH: (
            "轨道核对没接线（这套工作台没配核对模型）。"
            "禁写清单和「尚未登场」照常生效，缺的只是「跟后面章节抵不抵触」这一问。"
        ),
        DraftLanguage.EN: (
            "Track checking isn't wired in (this workbench has no "
            "checking model configured). The forbidden list and "
            '"not yet appeared" still apply as normal — the only thing '
            "missing is the question of conflicts with later chapters."
        ),
    },
    "drafting_not_wired": {
        DraftLanguage.ZH: (
            "起草能力还没接到这个会话上（工具表已经有它，实现还没接进来）。"
            "这一轮请改用别的方式推进，或者让作者从界面上起草。"
        ),
        DraftLanguage.EN: (
            "Drafting isn't wired into this session yet (the tool table "
            "has it, but the implementation isn't connected). Use a "
            "different approach this turn, or have the author draft from "
            "the interface."
        ),
    },
    "no_collapsed_result_table": {
        DraftLanguage.ZH: "这一轮没有可取的已收起结果。",
        DraftLanguage.EN: "There are no collapsed results to retrieve this turn.",
    },
    "no_such_result_id": {
        DraftLanguage.ZH: "没有编号 {id} 的已收起结果。",
        DraftLanguage.EN: "There is no collapsed result with id {id}.",
    },
    "truncated_marker": {
        DraftLanguage.ZH: "（已截断）",
        DraftLanguage.EN: " (truncated)",
    },
    "unknown_tool_name": {
        DraftLanguage.ZH: "没有名为「{name}」的工具。可用的是：{names}。",
        DraftLanguage.EN: 'There is no tool named "{name}". Available tools: {names}.',
    },
    "bad_json_arguments": {
        DraftLanguage.ZH: "参数不是合法的 JSON（{msg}）。请把整个参数对象重发一次。",
        DraftLanguage.EN: (
            "Arguments are not valid JSON ({msg}). Resend the entire "
            "arguments object."
        ),
    },
    "arguments_not_an_object": {
        DraftLanguage.ZH: "参数必须是一个 JSON 对象，比如 {{\"chapter\": 40}}。",
        DraftLanguage.EN: 'Arguments must be a JSON object, e.g. {{"chapter": 40}}.',
    },
    "validation_failed_prefix": {
        DraftLanguage.ZH: "参数不合法 —— ",
        DraftLanguage.EN: "Invalid arguments — ",
    },
    "validation_whole_request": {
        DraftLanguage.ZH: "(整体)",
        DraftLanguage.EN: "(the whole request)",
    },
    # ── agent/index.py ───────────────────────────────────────────────────
    "unknown_labels_requested": {
        DraftLanguage.ZH: "labels 里有认不出来的类型：{unknown}。角色册只有这几类：{valid}。",
        DraftLanguage.EN: (
            "labels contains unrecognized types: {unknown}. The roster "
            "only has these types: {valid}."
        ),
    },
    "unknown_character": {
        DraftLanguage.ZH: (
            "「{surface}」这个名字，这本书的角色册里没有。**别换个说法再查一次**——"
            "角色册是一份定死的名单（调 book_index 能看全），不在名单上的人，"
            "换什么叫法都查不到，再查一次只是白花一步。"
            "他要是这一场你新写的人，就当新人物直接往下写；"
            "要是作者写过他而系统还不认得，那得作者去人物卡上补，这一轮里等不到。"
        ),
        DraftLanguage.EN: (
            'The name "{surface}" is not in this book\'s roster. '
            "**Do not try a different phrasing** — the roster is a fixed "
            "list (call book_index to see it in full); if someone isn't "
            "on it, no rephrasing will find them, trying again is just a "
            "wasted step. If they're a new character you're writing into "
            "this scene, just write them in as a new character; if the "
            "author wrote them before and the system just doesn't "
            "recognize them yet, that needs the author to add them on "
            "the character card — it can't happen within this turn."
        ),
    },
    "ambiguous_character": {
        DraftLanguage.ZH: (
            "「{surface}」这个叫法同时指向 {count} 个人"
            "（{sample}{more}）。"
            "**这一种换个说法是有用的**：挑其中一个的名字再查一次，或者用一个更具体的称呼。"
        ),
        DraftLanguage.EN: (
            'The name "{surface}" points to {count} different people at '
            "once ({sample}{more}). "
            "**In this case, trying a different phrasing does help**: "
            "query again with one of their names, or use a more specific "
            "reference."
        ),
    },
    "not_a_character_axis": {
        DraftLanguage.ZH: "「{surface}」不是人物（它是 {label}）。这个工具只查人物的出场轴；地点不在这里问。",
        DraftLanguage.EN: (
            '"{surface}" is not a character (it\'s a {label}). This tool '
            "only queries characters' appearance axis — locations aren't "
            "queried here."
        ),
    },
    "summaries_not_wired": {
        DraftLanguage.ZH: (
            "章节摘要的读端还没接到这个会话上（工具表已经有它，实现还在 HTTP 路由里）。"
            "这一轮请改用 chapter_text 直接读正文，或者让作者从界面上看。"
        ),
        DraftLanguage.EN: (
            "The chapter-summary read endpoint isn't wired into this "
            "session yet (the tool table has it, but the implementation "
            "is still only in the HTTP route). Use chapter_text to read "
            "the prose directly this turn, or have the author look from "
            "the interface."
        ),
    },
    "no_manuscript_root_for_text": {
        DraftLanguage.ZH: (
            "读不到项目目录，正文取不出来 —— 正文的真相源是磁盘上的 chapters/NNNN.md，"
            "数据库里那份只是派生索引（ADR 0007）。"
        ),
        DraftLanguage.EN: (
            "Can't reach the project directory, so the prose can't be "
            "retrieved — the source of truth for prose is "
            "chapters/NNNN.md on disk; the copy in the database is only "
            "a derived index (ADR 0007)."
        ),
    },
    "chapter_text_missing_on_disk": {
        DraftLanguage.ZH: (
            "第 {chapter} 章在磁盘上没有正文（{relative} 不存在）——"
            "作者还没写到那儿，或者那一章不在这个项目里。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} has no prose on disk ({relative} doesn't "
            "exist) — either the author hasn't written that far yet, or "
            "that chapter isn't in this project."
        ),
    },
    "already_missed": {
        DraftLanguage.ZH: (
            "\n**这一轮你已经撞上 {count} 个角色册外的名字**（{names}）。"
            "这几次查询一个字的结果都没换来，而这一轮的步数是有限的——**别再查人了**，"
            "用手上已经有的东西往下写。"
        ),
        DraftLanguage.EN: (
            "\n**You've hit {count} names outside the roster this turn** "
            "({names}). These queries haven't gotten you a single word "
            "of result, and this turn's steps are limited — **stop "
            "looking people up**, keep writing with what you already "
            "have."
        ),
    },
    "model_cant_handle_chapter": {
        DraftLanguage.ZH: (
            "这个模型撑不起一次整章起草（{exc}）。"
            "让作者去顶栏「AI 设置」换一个上下文更大的模型，或者他自己在编辑器里写。"
        ),
        DraftLanguage.EN: (
            "This model can't handle a full chapter draft ({exc}). Have "
            "the author switch to a model with a larger context window "
            'in the top bar\'s "AI Settings," or write it themselves in '
            "the editor."
        ),
    },
    "cant_reach_writer_model": {
        DraftLanguage.ZH: "这一稿没写成，联系不上写作模型：{exc}",
        DraftLanguage.EN: "This draft didn't get written — can't reach the writing model: {exc}",
    },
    "draft_came_back_empty": {
        DraftLanguage.ZH: (
            "第 {chapter} 章这一稿是空的，写作模型什么都没写出来。"
            "换个说法再让我写一次。"
        ),
        DraftLanguage.EN: (
            "This draft of chapter {chapter} came back empty — the "
            "writing model produced nothing. Rephrase and ask me to "
            "write it again."
        ),
    },
    "no_such_draft": {
        DraftLanguage.ZH: (
            "这本书里没有这一稿（编号对不上，或者它已经被清理掉了）。"
            "重新起一稿，或者让作者说清楚他要的是哪一版。"
        ),
        DraftLanguage.EN: (
            "This book has no such draft (the id doesn't match, or it's "
            "already been cleaned up). Start a new draft, or have the "
            "author say clearly which version they want."
        ),
    },
    # ── agent/drafting.py::_land()（032 之后的批次，2026-08-27）──────────────
    # `_land()` 不走 ToolRefused：它把「写没写成」全部当返回值（成功也在内），
    # 见它自己的 docstring。这十条覆盖它每一条 return 分支，键名前缀 `landing_`
    # 是这一节唯一的命名空间。
    "landing_target_chapter_missing": {
        DraftLanguage.ZH: (
            "第 {chapter} 章还不存在，所以这一稿没有存进去——新开一章要作者自己起"
            "章标题（书里靠那一行认章）。把稿子给他看，请他建好这一章再放进去。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} doesn't exist yet, so this draft wasn't "
            "saved — a new chapter needs the author to title it "
            "themselves (the book recognizes chapters by that line). "
            "Show him the draft and ask him to create the chapter "
            "first, then put it in."
        ),
    },
    "landing_chapter_created_after_draft": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：写这一稿的时候那一章还不存在，现在它有了"
            "——那是作者刚建的，这一稿不是照着它写的，所以不覆盖。"
            "要用的话让我照现在这一章重写一稿。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: it didn't exist when this "
            "draft was written, and now it does — the author just "
            "created it, and this draft wasn't written against it, so "
            "it won't overwrite it. To use it, have me rewrite a draft "
            "against the chapter as it stands now."
        ),
    },
    "landing_chapter_head_malformed": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：那一章现在的开头不是一行章标题"
            "（或者标题前面还有别的字）。这种时候动它会让整本书的章号错位，"
            "所以一个字都没写。稿子还在，交给作者。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: its current opening line "
            "isn't a chapter title (or there's other text before the "
            "title). Touching it now would throw off every chapter "
            "number in the book, so nothing was written. The draft is "
            "still here — hand it to the author."
        ),
    },
    "landing_draft_empty": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：这一稿是空的，存上去等于把那一章清空。"
            "换个说法再让我写一次。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: this draft is empty, and "
            "saving it would wipe out the chapter. Rephrase and ask me "
            "to write it again."
        ),
    },
    "landing_candidate_not_single_chapter": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：这一稿接上原来的章标题之后切不成恰好一章"
            "（多半是稿子里自己又写了章标题）。稿子还在，交给作者。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: appended to the original "
            "chapter title, this draft no longer parses as exactly one "
            "chapter (most likely it wrote its own chapter title "
            "again). The draft is still here — hand it to the author."
        ),
    },
    "landing_presync_refused": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：这本书里有一个章节文件（{path}）现在切不成"
            "一章，同步整本书会失败。稿子还在，请作者先把那个文件的开头修好。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: one of this book's chapter "
            "files ({path}) currently doesn't parse as a single "
            "chapter, so syncing the whole book would fail. The draft "
            "is still here — ask the author to fix that file's opening "
            "first."
        ),
    },
    "landing_chapter_changed": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：写这一稿的时候作者又改过那一章，"
            "存上去会盖掉他刚写的字。稿子还在，让他自己决定要不要用。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: the author changed that "
            "chapter again while this draft was being written, and "
            "saving would overwrite what he just wrote. The draft is "
            "still here — let him decide whether to use it."
        ),
    },
    "landing_chapter_file_missing": {
        DraftLanguage.ZH: (
            "没有存进第 {chapter} 章：写这一稿的时候那一章的文件不在了。"
            "稿子还在，交给作者。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} wasn't saved: that chapter's file went "
            "missing while this draft was being written. The draft is "
            "still here — hand it to the author."
        ),
    },
    "landing_saved_but_history_not_recorded": {
        DraftLanguage.ZH: (
            "已经写进第 {chapter} 章了，但这本书里有别的章节文件切不成一章，"
            "所以这一次没能记进版本历史。请作者去看一眼。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} has been saved, but another chapter "
            "file in this book doesn't parse as a single chapter, so "
            "this save wasn't recorded in version history this time. "
            "Ask the author to take a look."
        ),
    },
    "landing_saved": {
        DraftLanguage.ZH: (
            "已经写进第 {chapter} 章了（章标题保持原样）。"
            "不满意就在版本历史里退回上一版，活动记录里也有这一次的记录。"
        ),
        DraftLanguage.EN: (
            "Chapter {chapter} has been saved (the chapter title was "
            "kept as-is). If it's not right, roll back to the previous "
            "version in the version history — this save is also in "
            "the activity log."
        ),
    },
    # ── agent/panels.py（2026-09-12，右栏四栏的读工具）──────────────────────
    # 拒绝 + 返回里的 `notes`。**返回里的字这儿也走双语**：这四条是新写的，
    # 没有理由沿用 `index.py` 那批只有中文的 note。
    "not_a_character_card": {
        DraftLanguage.ZH: "「{surface}」不是人物（它是 {label}），没有角色卡可翻。",
        DraftLanguage.EN: (
            '"{surface}" is not a character (it\'s a {label}) — there is no '
            "character card to open."
        ),
    },
    "card_events_not_wired": {
        DraftLanguage.ZH: "事件那一栏没接到这个会话上：下面 `events` 是空的不代表他什么都没经历过。",
        DraftLanguage.EN: (
            "The events panel isn't wired into this session: an empty `events` "
            "below does not mean nothing ever happened to them."
        ),
    },
    "card_events_omitted": {
        DraftLanguage.ZH: (
            "他名下有 {total} 件事，预算只装得下 {kept} 件，**最早的 {omitted} 件没给**"
            "（要那几章的事就用 chapter_events 按章去看）。"
        ),
        DraftLanguage.EN: (
            "There are {total} events under their name; the budget fits {kept}, "
            "**the earliest {omitted} were left out** (use chapter_events to see "
            "those chapters one by one)."
        ),
    },
    "card_relations_omitted": {
        DraftLanguage.ZH: "记录过 {total} 段关系，预算装不下，**最早的 {omitted} 段没给**。",
        DraftLanguage.EN: (
            "{total} relationships are on record; the budget can't fit them all, "
            "**the earliest {omitted} were left out**."
        ),
    },
    "card_states_omitted": {
        DraftLanguage.ZH: (
            "他身上记着 {total} 个状态维度，预算装不下，**最早定下的 {omitted} 个没给**"
            "（character_state 单独查他时给得全一些）。"
        ),
        DraftLanguage.EN: (
            "{total} state dimensions are recorded for them; the budget can't fit "
            "them all, **the {omitted} set earliest were left out** (character_state "
            "on its own gives a fuller list)."
        ),
    },
    "events_not_wired": {
        DraftLanguage.ZH: "事件那一栏没接到这个会话上，这一轮读不到任何一章的事件。",
        DraftLanguage.EN: (
            "The events panel isn't wired into this session — no chapter's events "
            "can be read this turn."
        ),
    },
    "chapters_have_no_confirmed_events": {
        DraftLanguage.ZH: (
            "第 {first_chapter}–{last_chapter} 章一条已确认的事件都没有——可能是这几章还没"
            "整理过，也可能整理了但一件都没留下。**别读成「这几章什么都没发生」**，"
            "正文才是真相源。"
        ),
        DraftLanguage.EN: (
            "Chapters {first_chapter}–{last_chapter} have no confirmed events at all — "
            "either they haven't been processed yet, or they were and nothing stuck. "
            "**Don't read this as \"nothing happens in these chapters\"** — the text "
            "is the source of truth."
        ),
    },
    "chapter_events_omitted": {
        DraftLanguage.ZH: (
            "区间里有 {total} 件已确认的事，预算只装得下 {kept} 件，**最前面的 {omitted} 件"
            "没给**（要它们就把区间往前挪一段再问一次）。"
        ),
        DraftLanguage.EN: (
            "The range has {total} confirmed events; the budget fits {kept}, "
            "**the earliest {omitted} were left out** (to get them, move the range "
            "earlier and ask again)."
        ),
    },
    "rules_not_wired": {
        DraftLanguage.ZH: "检验规则那一栏没接到这个会话上，这一轮读不到规则。",
        DraftLanguage.EN: (
            "The rules panel isn't wired into this session — no rules can be read "
            "this turn."
        ),
    },
    "no_rules_yet": {
        DraftLanguage.ZH: "作者还没给这本书添加过检验规则（内置规则今天一条都没有）。",
        DraftLanguage.EN: (
            "The author hasn't added any check rules to this book yet (there are "
            "no built-in rules today)."
        ),
    },
    "rules_omitted": {
        DraftLanguage.ZH: "共 {total} 条规则，预算装不下，**后面 {omitted} 条没给**。",
        DraftLanguage.EN: (
            "{total} rules in total; the budget can't fit them all, **the last "
            "{omitted} were left out**."
        ),
    },
    "chapter_never_checked": {
        DraftLanguage.ZH: "第 {chapter} 章还没有检验过（作者没按过那颗闪电，保存后的自动检验也没跑过）。",
        DraftLanguage.EN: (
            "Chapter {chapter} has never been checked (the author hasn't pressed "
            "the bolt, and no post-save check has run)."
        ),
    },
    "check_is_stale": {
        DraftLanguage.ZH: "第 {chapter} 章最近那次检验对的是**更早的一版正文**，作者之后又改过：结果可能已经不作数。",
        DraftLanguage.EN: (
            "Chapter {chapter}'s latest check was against **an earlier version of "
            "the text**; the author has edited it since — the result may no longer "
            "hold."
        ),
    },
    "check_issues_omitted": {
        DraftLanguage.ZH: "那次检验命中 {total} 处，预算装不下，**后面 {omitted} 处没给**。",
        DraftLanguage.EN: (
            "That check hit {total} places; the budget can't fit them all, **the "
            "last {omitted} were left out**."
        ),
    },
    "notices_not_wired": {
        DraftLanguage.ZH: "通知那一栏没接到这个会话上，这一轮读不到通知。",
        DraftLanguage.EN: (
            "The notifications panel isn't wired into this session — no "
            "notifications can be read this turn."
        ),
    },
    "no_open_notices": {
        DraftLanguage.ZH: "通知栏是空的：没有待处理的通知，也没有等作者确认的提案。",
        DraftLanguage.EN: (
            "The notifications panel is empty: nothing open, and no proposals "
            "waiting for the author."
        ),
    },
    "no_open_notices_for_chapter": {
        DraftLanguage.ZH: "第 {chapter} 章没有待处理的通知，也没有等作者确认的提案。",
        DraftLanguage.EN: (
            "Chapter {chapter} has no open notifications and no proposals waiting "
            "for the author."
        ),
    },
    "notices_omitted": {
        DraftLanguage.ZH: "共 {total} 条通知，预算只装得下一部分，**最早的 {omitted} 条没给**（按章那张表是全的）。",
        DraftLanguage.EN: (
            "{total} notifications in total; the budget fits only some, **the "
            "earliest {omitted} were left out** (the per-chapter table is complete)."
        ),
    },
    "pending_omitted": {
        DraftLanguage.ZH: "共 {total} 条待确认的提案，预算只装得下一部分，**最早的 {omitted} 条没给**（按章那张表是全的）。",
        DraftLanguage.EN: (
            "{total} pending proposals in total; the budget fits only some, **the "
            "earliest {omitted} were left out** (the per-chapter table is complete)."
        ),
    },
    # 通知的抬头，措辞照抄面板那张表（`frontend/src/components/SystemNotifications.tsx::KIND_TITLE`）。
    "notice_kind_summary_mismatch": {
        DraftLanguage.ZH: "总结与正文可能对不上",
        DraftLanguage.EN: "The summary and the text may not match",
    },
    "notice_kind_background_failure": {
        DraftLanguage.ZH: "后台有一件事没办成",
        DraftLanguage.EN: "Something didn't finish in the background",
    },
    "notice_kind_validation_blocked": {
        DraftLanguage.ZH: "这一章的检查需要留意",
        DraftLanguage.EN: "This chapter's check needs attention",
    },
    "notice_kind_text_advisory": {
        DraftLanguage.ZH: "这一段值得再看一眼",
        DraftLanguage.EN: "This passage is worth another look",
    },
    "notice_kind_extraction_yielded_nothing": {
        DraftLanguage.ZH: "这一章什么都没整理出来",
        DraftLanguage.EN: "Nothing came out of processing this chapter",
    },
    "notice_kind_import_toc_skipped": {
        DraftLanguage.ZH: "导入时跳过了几个空章",
        DraftLanguage.EN: "A few empty chapters were skipped during import",
    },
    "notice_kind_event_cast_changed": {
        DraftLanguage.ZH: "一件事的参与者变了",
        DraftLanguage.EN: "The people in an event changed",
    },
    "notice_kind_proposal_conflict": {
        DraftLanguage.ZH: "有一处设定跟抽出来的内容对不上",
        DraftLanguage.EN: "Something extracted doesn't match an existing fact",
    },
    "notice_kind_proposal_low_confidence": {
        DraftLanguage.ZH: "有一条情节需要作者确认",
        DraftLanguage.EN: "There's an event that needs the author's confirmation",
    },
    "notice_kind_unknown": {
        DraftLanguage.ZH: "一条系统通知",
        DraftLanguage.EN: "A system notification",
    },
    # 提案对照那几行，措辞照抄面板（`SystemNotifications.tsx::factLine`）。
    "fact_line_location": {
        DraftLanguage.ZH: "{subject} 在 {target}",
        DraftLanguage.EN: "{subject} is at {target}",
    },
    "fact_line_relationship": {
        DraftLanguage.ZH: "{subject} 与 {target} {value}",
        DraftLanguage.EN: "{subject} and {target} — {value}",
    },
    "fact_line_relationship_bare": {
        DraftLanguage.ZH: "{subject} 与 {target}",
        DraftLanguage.EN: "{subject} and {target}",
    },
    "fact_line_state": {
        DraftLanguage.ZH: "{subject} 的 {target} 是 {value}",
        DraftLanguage.EN: "{subject}'s {target}: {value}",
    },
    "fact_line_state_bare": {
        DraftLanguage.ZH: "{subject} 的 {target}",
        DraftLanguage.EN: "{subject}'s {target}",
    },
    "unknown_name_placeholder": {
        DraftLanguage.ZH: "（认不出的一项）",
        DraftLanguage.EN: "(an unrecognized entry)",
    },
    "proposal_conflict_pair": {
        DraftLanguage.ZH: "当前：{current}；提议：{proposed}（原文：{quote}）",
        DraftLanguage.EN: "Current: {current}; proposed: {proposed} (source: {quote})",
    },
    "proposal_event_line": {
        DraftLanguage.ZH: "{summary}（原文：{quote}）",
        DraftLanguage.EN: "{summary} (source: {quote})",
    },
    # ── draft/product_draft.py ───────────────────────────────────────────
    "write_rule_forbidden_words": {
        DraftLanguage.ZH: (
            "自定义文风里不能出现这些词：{words}"
            "——这几个词是引擎自己在管的事，写进文风里只会和它打架。"
        ),
        DraftLanguage.EN: (
            "A custom writing style may not contain these words: {words}"
            " — the engine already manages these on its own; writing "
            "them into the style will only fight it."
        ),
    },
}


def message(key: str, language: DraftLanguage, **kwargs: object) -> str:
    """按语言取一条运行时消息，用 `kwargs` 填模板。缺一侧翻译时 `KeyError`——
    和 `translate_tool_declarations` 同一个纪律：宁可当场报错，也不让一句中文
    漏进英文书的对话历史（对话是持久化的，漏一次改代码删不掉）。
    """
    return _MESSAGES[key][language].format(**kwargs)


__all__ = ["message", "translate_tool_declarations"]
