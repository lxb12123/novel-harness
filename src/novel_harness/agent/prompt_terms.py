"""模式二：人设 + 15 个工具 schema 的双语化（国际化第三批）。

**为什么不是 `draft/prompt_terms.py` 那张表**：那张表管的是普通函数的返回值，
按 `language` 参数现算现返回——`assemble()` 每次调用都重新拼一遍字符串。这里
不行：15 个工具的 `args=` 全是 Pydantic 类，docstring 和 `Field(description=...)`
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
    "查第 N 章哪些实体还没登场。返回的是实体的首现章号。": (
        "Check which entities haven't appeared yet as of chapter N. "
        "Returns each entity's first-appearance chapter."
    ),
    "查「第 N 章不许说破什么」。": 'Query "what must not be revealed as of chapter N."',
    "要查第几章（AS OF 第几章，纯查询坐标，不会写进任何数据）。": (
        "Which chapter to query as of (a pure query coordinate — "
        "this never writes to any data)."
    ),
    # ── character_state ──────────────────────────────────────────────────
    (
        "查某个人在第 N 章的处境：在哪、各状态维度的值、登场了没有、是不是已经死了。"
        "**只认花名册上的名字**（book_index 里那份）：不在上面的人查不到，"
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
    # ── draft_chapter ────────────────────────────────────────────────────
    (
        "起草第 N 章的一稿。**先 calibrate_scene + seal_scene_brief 拿到"
        " calibration_id，再调本工具**——起草目标只从那个不可变产物读取，"
        "你传不了、也不需要传任何目标文字。**这一步不动书**：稿子存在一边，返回里给你它的编号、"
        "字数、开头的一段，以及写它的那个模型自己说的一句话。"
        "方向清楚就写一稿、接着调 save_draft 存进去（不用问作者，他随时能退回去）；"
        "方向不清楚就一次要几稿，把它们的自述摆给作者挑——**同一批里的几稿会同时写**，"
        "不比一稿慢多少。"
        "**不要传约束**：不许说破什么由后端按这个章号当场重算，"
        "你上一轮看到的清单对这一章可能已经过期。"
    ): (
        "Draft one version of chapter N. **Call calibrate_scene + "
        "seal_scene_brief first to get a calibration_id, then call this "
        "tool** — the drafting goal is read only from that immutable "
        "artifact; you cannot pass, and don't need to pass, any goal text. "
        "**This step does not touch the book**: the draft is set aside, "
        "and the return gives you its id, word count, an opening excerpt, "
        "and a note the model that wrote it left about itself. If the "
        "direction is clear, write one draft and follow up with save_draft "
        "to save it (no need to ask the author, they can always revert "
        "it); if the direction is unclear, request several drafts at once "
        "and lay their notes in front of the author to choose — "
        "**drafts in the same batch are written concurrently**, barely "
        "slower than writing one. **Do not pass constraints**: what must "
        "not be revealed is recomputed by the backend on the spot for "
        "this chapter number — the list you saw last turn may already be "
        "stale for this chapter."
    ),
    (
        "起草第 N 章的一稿（**chapter + calibration_id**，ADR 0033）。\n\n"
        "**这里没有、也永远不会有约束字段**（ADR 0019 边界二）：不许说破什么由后端当场\n"
        "从第 N 章重新算，你上一轮看到的那份清单对这一章可能已经过期了。\n\n"
        "**也没有自由文本 goal**（ADR 0033）：`goal_spec` 只从不可变校准产物读取——\n"
        "外层 Agent 不负责抄写事实文字或目标散文，Writer 拿到的是校准层实际产出的版本。\n\n"
        "它和 `DraftFn` 放在一起而不是和别的工具入参放在一起，是因为它是**注入契约的一半**：\n"
        "起草侧收的就是 `(DraftAsk, DraftContext)`，而这两件东西里都没有模型给的约束。"
    ): (
        "Draft one version of chapter N (**chapter + calibration_id**, "
        "ADR 0033).\n\n"
        "**There is no constraints field here, and there never will be** "
        "(ADR 0019 boundary 2): what must not be revealed is recomputed by "
        "the backend on the spot from chapter N; the list you saw last "
        "turn may already be stale for this chapter.\n\n"
        "**Nor is there a free-text goal** (ADR 0033): `goal_spec` is read "
        "only from the immutable calibration artifact — the outer Agent "
        "is not responsible for copying fact text or goal prose; the "
        "Writer receives the version the calibration layer actually "
        "produced.\n\n"
        "It sits alongside `DraftFn` rather than with other tools' args "
        "because it is **half of an injection contract**: the drafting "
        "side receives exactly `(DraftAsk, DraftContext)`, and neither of "
        "those carries any constraint supplied by the model."
    ),
    "起草第几章。约束由后端按这个章号当场计算。": (
        "Which chapter to draft. Constraints are computed by the backend "
        "on the spot for this chapter number."
    ),
    (
        "seal_scene_brief 返回的那个不可变编号。起草目标只从它读取，"
        "你不需要也不应该在这里传任何目标文字。"
    ): (
        "The immutable id returned by seal_scene_brief. The drafting goal "
        "is read only from it — you don't need to, and shouldn't, pass "
        "any goal text here."
    ),
    # ── book_index ───────────────────────────────────────────────────────
    (
        "全书目录：章标题一览 + 花名册（人物 / 地点 / 门派 / 物件的**显示名**）。"
        "**先调这个再往下钻**，它是最便宜的一层。"
    ): (
        "Book-wide index: chapter titles + the roster (**display names** "
        "of characters / locations / factions / objects). **Call this "
        "before drilling down further** — it's the cheapest layer."
    ),
    "全书目录：章标题 + 花名册。**默认调用不带参数，这是最便宜的那一层。**": (
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
        "只列这几类花名册条目（Character / Location / Faction / "
        "Foreshadow / Object），默认全给。花名册太长被裁掉整整一类时，"
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
    # ── calibrate_scene ──────────────────────────────────────────────────
    (
        "写前校准：把你对作者当前要求的结构化理解（预计人物 + 封闭指令码 + "
        "视角/风格码）拿去按第 N 章时点核对真实数据。返回一份详细报告："
        "已解析人物、带来源的状态/关系/知识/事件/摘要、确定性冲突、未知项和"
        "覆盖回执。\n"
        "**只做确定性核对，不做语义判断**：报告不会替你判断「作者这句话和旧事实"
        "冲不冲突」——那种张力是你的 `MACHINE_INFERENCE`，需要时用 ask_author 问。\n"
        "**这是起草的前置步骤**：先调它，再视情况 ask_author，再 seal_scene_brief，"
        "最后才 draft_chapter。"
    ): (
        "Pre-draft calibration: take your structured understanding of "
        "the author's current request (expected cast + closed directive "
        "codes + viewpoint/tone codes) and check it against real data as "
        "of chapter N. Returns a detailed report: resolved characters, "
        "sourced state/relationships/knowledge/events/summaries, "
        "deterministic conflicts, unknowns, and a coverage receipt.\n"
        "**Only deterministic checking, never semantic judgment**: the "
        "report will not judge for you whether 'what the author just "
        "said conflicts with an old fact' — that tension is your own "
        "`MACHINE_INFERENCE`; ask with ask_author when you need to.\n"
        "**This is drafting's prerequisite step**: call this first, "
        "then ask_author if warranted, then seal_scene_brief, and only "
        "then draft_chapter."
    ),
    (
        "Agent 提交给校准层的临时创意提案（§6.1）。\n\n"
        "**没有 goal / task / event 自由文本字段**：模型只能提交封闭指令码、安全引用和\n"
        "固定 `AGENT_INFERRED`；不能提交任务散文、event beat 散文、`must_not_reveal`、\n"
        "`forbidden_entities`、Canon 写入或章号有效期字段。"
    ): (
        "A tentative creative proposal the Agent submits to the "
        "calibration layer (§6.1).\n\n"
        "**There is no free-text goal / task / event field**: the model "
        "may only submit closed directive codes, safe references, and "
        "the fixed `AGENT_INFERRED`; it may not submit task prose, "
        "event-beat prose, `must_not_reveal`, `forbidden_entities`, "
        "Canon writes, or a chapter-validity field."
    ),
    "要校准第几章（AS OF 第几章，纯查询坐标）。": (
        "Which chapter to calibrate as of (a pure query coordinate)."
    ),
    (
        "预计人物：作者当前要求里点名的人 + 你根据上下文补出的人。"
        "每一个都用作者在正文里的叫法。只用于检索与写作意图，不是本章实际在场名单。"
    ): (
        "Expected cast: people named in the author's current request, "
        "plus anyone you infer from context. Use the name the author "
        "uses in the prose for each. Used only for lookup and to convey "
        "writing intent — not the actual present-cast list for this "
        "chapter."
    ),
    (
        "封闭指令候选：只能从 ENTER_LOCATION / SEARCH_FOR / TEST_CHARACTER / "
        "DEFER_REVEAL / ADVANCE_CLUE 里选，参数只能引用花名册上的人物/地点/物件"
        "显示名。不要在这里写任务散文或事件概括。"
    ): (
        "Closed directive candidates: pick only from ENTER_LOCATION / "
        "SEARCH_FOR / TEST_CHARACTER / DEFER_REVEAL / ADVANCE_CLUE; "
        "arguments may only reference character/location/object display "
        "names from the roster. Do not write task prose or an event "
        "summary here."
    ),
    "视角人物（花名册上的称呼，只解析 NodeRef）。": (
        "The viewpoint character (a name from the roster; resolves only "
        "to a NodeRef)."
    ),
    "封闭语气码。": "Closed tone code.",
    "封闭节奏码。": "Closed pacing code.",
    "封闭结尾码。": "Closed ending code.",
    "Agent 提出的一条封闭指令候选。**不能塞任务/event/goal 散文。**": (
        "One closed directive candidate proposed by the Agent. **May "
        "not carry task/event/goal prose.**"
    ),
    (
        "封闭指令码。**不在这个集合里的创作细节不能进 Writer**"
        "（除非新增可机械验证的类型）。"
    ): (
        "Closed directive codes. **A creative detail outside this set "
        "cannot reach the Writer** (unless a new mechanically-verifiable "
        "type is added)."
    ),
    "预计人物的一个称呼。basis 固定 AGENT_INFERRED。": (
        "One name for an expected cast member. basis is fixed to "
        "AGENT_INFERRED."
    ),
    # ── seal_scene_brief ─────────────────────────────────────────────────
    (
        "把 calibrate_scene 的校准报告封存成不可变写作简报，拿到 calibration_id。"
        "封存时后端重新校验每一个事实引用、可见性和全部水位；"
        "返回的目标文字由后端从类型项固定渲染，你不需要手抄任何东西。\n"
        "**作者确认**：如果你想把这稿的要求标成「作者确认」，必须先把类型化任务卡"
        "摆给作者、等他下一句回复（这一轮结束），下一轮再带着他的选择来封存。"
        "没确认就封存也可以——那批指令会标成机器推演进 Writer。\n"
        "**推翻旧设定**：只有作者明确选了推翻非安全旧设定才传 "
        "author_choice=RETCON_NON_SAFETY + retcon_fact_ids。"
    ): (
        "Seal calibrate_scene's calibration report into an immutable "
        "writing brief, and get a calibration_id. Sealing re-validates "
        "every fact reference, visibility, and watermark on the "
        "backend; the goal text in the return is rendered fixed by the "
        "backend from the typed items — you don't need to copy anything "
        "by hand.\n"
        "**Author confirmation**: if you want this draft's requirements "
        "marked 'author-confirmed,' you must first lay the typed task "
        "card in front of the author and wait for their next reply "
        "(this turn ends here), then seal with their choice on the "
        "following turn. Sealing without confirmation is also fine — "
        "that batch of directives will be marked machine-inferred going "
        "into the Writer.\n"
        "**Overturning an existing setting**: only pass "
        "author_choice=RETCON_NON_SAFETY + retcon_fact_ids when the "
        "author has explicitly chosen to overturn a non-safety-related "
        "existing fact."
    ),
    "作者面对旧事实的三个选择。": "The author's three choices when facing an existing fact.",
    (
        "`seal_scene_brief` 的入参：**只有 inspection 编号和作者选择**。\n\n"
        "提案不重新提交：封存器从校准报告里取原始提案，重新校验全部可见性与水位。"
    ): (
        "`seal_scene_brief`'s input: **only the inspection id and the "
        "author's choice.**\n\n"
        "The proposal is not resubmitted: the sealer takes the original "
        "proposal from the calibration report and re-validates every "
        "visibility and watermark."
    ),
    "calibrate_scene 返回的校准报告编号。": (
        "The calibration report id returned by calibrate_scene."
    ),
    (
        "作者看过类型化任务卡之后的三个选择之一。"
        "**没有就不传**（未确认的请求投影保持机器推演强度）。"
        "传了就必须是在作者回复之后的那一轮——系统会绑定他真正说过的那句话。"
    ): (
        "One of the three choices, after the author has seen the typed "
        "task card. **Omit it if there isn't one** (an unconfirmed "
        "request stays projected at machine-inference strength). If you "
        "do pass it, it must be on the turn after the author replied — "
        "the system binds it to what they actually said."
    ),
    "author_choice=RETCON_NON_SAFETY 时必须点名要推翻的旧事实（校准报告里的 item_id）。": (
        "When author_choice=RETCON_NON_SAFETY, must name the existing "
        "facts being overturned (item_ids from the calibration report)."
    ),
    (
        "RETCON 时你（Agent）对这条矛盾的带来源推演，一句话。"
        "它永远只是机器推演，不会变成确定性规则命中。"
    ): (
        "During a RETCON, your (the Agent's) sourced inference about "
        "this tension, one sentence. It is always only a machine "
        "inference, and never becomes a deterministic rule hit."
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


__all__ = ["translate_tool_declarations"]
