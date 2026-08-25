"""约束 → prompt 的**唯一**出口（ADR 0010）。

── ⚠️ 2026-08-25：三臂没了，`PromptForm` 整个删了 ────────────────────────

这个模块原来是「同一个函数的三个 `form` 取值」（X0 对照 / X1 清单 / X2 散文），
为 M2 的 kill-gate 而生。M2 随秘密下线一起退役（[ADR 0039](../../../docs/adr/0039-secrets-offline.md)），
而那之后：

- 生产上只有一处赋值（`product_draft.py` 的 `form = PromptForm.X1`）；
- `NH_DRAFT_FORM` 那个协议 §6 点名的环境变量**从来没有被接上过**——全仓零处
  `getenv` / `environ` 读它，它只活在 docstring、一条测试说明和已退役的协议里。

所以 `PromptForm`、`DraftRequest.form`、`ChapterDraftRequest.form`、
`check_request` 里那段四选一校验，**同一笔全部删掉**。渲染器从此只有一种形态。

**下面那几节的论证留着，因为它们今天仍然管着这个渲染器**——只是「三臂之间不许有差异」
这句话现在读作「这一份 prompt 里不许出现没人负责的段落」。

── 一、图谱段只追加在**尾部**，不重排前面任何一个字节（ADR 0010 D4）────────

`_base()` 算出那段用户消息，图谱段只往它尾部追加。这条性质原来是为了让 X0 成为 X1 的
严格前缀（可逐字节验证），今天它剩下的价值是**改图谱段不会动到文风和上文那两块**——
而那两块是前缀缓存的全部价值所在（同 `product_assemble` 的 `[文风][记忆][用户]` 顺序）。

── 二、`cast` 进 prompt（ADR 0010 D6）────────────────────────────────────

`cast` 是**称呼原文**，是调用方算好的输入不是本模块的图谱查询结果。不给模型这份名单，
它就会写到别人身上去——那不是「少注入一点」，是**另一个任务**。理由全文在 ADR 0010 D6。

── 三、只有显示名进 prompt，`props` 一个字符都不碰 ──────────────────────

本模块只读 `ctx` 上的两类字段：`cast`、`forbidden_names` + 首现章。
（2026-08-24 之前还有三类跟秘密有关的——`secret_labels`、`KnowledgeCell` 的三个标量、
`matrix.characters/secrets` 的 `.name`。它们随秘密下线一起走了，ADR 0039。）
**签名里没有 store**——拿不到 store 就查不了第二遍，prompt 里的事实必然是调用方算好的
那一个对象上的。作者写在节点上的 `twist` / `plot_note` 住在 `.props` 里，本模块碰不到它。

── 四、它拦不住什么（诚实交代）─────────────────────────────────────────────

- **`goal` / `previous_tail` / `write_rule` 是三个自由文本入口。** 有人把 PLANNED 的内容
  用自然语言写进 `goal`（「写萧决发现自己血脉有异——他还不知道那是家族封印的反噬」），
  本模块、第 4 道 arch-guard、类型系统**全都看不见**：判它需要回答「这句话是不是把伏笔说破了」，
  那是语义判断，ADR 0005 在 v1 里禁止本仓库长出这种能力。守它的是 ADR 0010 D3 末尾那条
  review 判据。（原来还有 `synth/leak_selfcheck.py` 的机器判据，它随 M2 退役，ADR 0039。）
"""

from __future__ import annotations

from fractions import Fraction

from .context import DraftContext, ResolvedConstraints
from .length import DraftLanguage, LengthSpec


CONTINUATION_GOAL = "顺着上文往下写，接住作者已经起的头，不要另起一段新情节。"
"""续写模式的 `goal`（[ADR 0015](../../../docs/adr/0015-inline-continuation-is-a-short-draft.md) D3）。

**它是常量、住在后端，而不是前端传一个默认值**——`goal` 是 ADR 0010 点名的三个自由文本
入口之一（作者能把伏笔用自然语言写进去，本层看不见）。续写模式把这个入口**关掉**换成
这一句，是在缩小那个洞。前端能传的东西作者就能改，所以它不能住在前端。"""


# ══════════════════════════════════════════════════════════════════════════
# 逐字上文的截断长度 —— 默认值是考卷，产品档要显式加长（ADR 0019 边界五）
# ══════════════════════════════════════════════════════════════════════════

GATE_TAIL_CODE_POINTS = 800
"""`previous_tail` 的默认截断长度（code point）。**这个数是 X0 对照臂的定义，不是产品参数。**

ARCHITECTURE §9 / PLAN §551：「X0（只给上一场景最多 800 个输入 code point）」。
X0 存在的目的是**证明给得少会崩**，而三臂共用 `assemble()`——于是默认值一改就是改考卷
（EVAL_PROTOCOL §2 冻结，且反混淆铁律要求三臂只在图谱段上有差异）。

**因此：`eval/runner.py`、`eval/evidence.py`、`nh gate`、`nh draft` 一个字都不传，自动拿它。
想要更长的上文只能在调用点显式传 `previous_tail_limit=`**——让「谁把预算放大了」在
调用点看得见，而不是藏在一个所有人共享的常量里。
"""

TAIL_CONTEXT_SHARE = Fraction(1, 6)
"""逐字上文最多占「窗口减去输出预留」的几分之几。

**是比例不是绝对量**，理由同 `product_context.MEMORY_CONTEXT_SHARE`：换个模型自动缩放，
1M 窗口和 32k 窗口不该拿同一个数字。取 1/6 而不是取满：记忆层已经拿走 1/3
（`MEMORY_CONTEXT_SHARE`），两者加起来占一半，剩下的一半留给约束段、图谱段和余量。

**上文的优先级高于记忆层**（ADR 0019 边界五：Prune Before Summarize，逐字上文永不许被
记忆挤掉），但「优先级高」说的是砍的顺序，不是份额大——它只需要够长到接住语气和情绪。
"""

TAIL_UNITS_CEILING = 40_000
"""逐字上文的**失控闸**——正常情况下它不该起作用。

── 为什么它不是「每次都砍到这么长」──────────────────────────────────────

真正决定上文多长的是 `min(上一章实际有多长, 这里算出来的额度)`：截断走的是
`previous_tail[-limit:]`，**上一章不到 limit 就整篇原样进 prompt，一个字不切**。
所以常态下起作用的是「你那一章写了多少字」，不是这个常量。

这个数上一版是 12,000，**标定错了**：1M 窗口的模型 + 13,000 字的一章，那一章占窗口不到
3%，其余开销也绰绰有余——却因为撞上这个闸被砍掉 1,000 字，**而且砍在句子中间**。
上文这一层的全部意义是接住语气和情绪，切在半句上正好毁掉它要的那个东西。
闸门该拦的是病态输入（切章切错了、一「章」是一整卷），不是一章正常的长文。

── 那为什么还留着它 ──────────────────────────────────────────────────

因为 `previous_tail` 是**调用方给什么就是什么**。切章一旦出错（`CHAPTER_RE` 没匹配上，
整本书成了一「章」），没有这道闸就是把整本书按 token 计费发出去一次。
40,000 字已经不是一章是一卷，撞到它说明**上游坏了**，不是作者写得多。

要真的放开就调这一个数——它是一个数，不是散落各处的常量。
"""


def product_tail_limit(
    max_context_tokens: int | None,
    reserved_output_tokens: int,
) -> int:
    """产品档的逐字上文能有多长（code point），从模型的真实上下文窗口倒推。

    `max_context_tokens is None`（能力表没登记这个模型）→ 回落到 `GATE_TAIL_CODE_POINTS`。
    **不猜一个大窗口**：猜大了的后果是发出去被供应商拒（同 `provider.py` 那条理由）。

    下限也是 `GATE_TAIL_CODE_POINTS`：窗口小到算出来比它还短时保持今天的行为，
    **这条函数只许把上文变长，不许变短**——变短了没人会发现，只会觉得模型忽然变笨。

    Note:
        预算是按「字」算的（`TOKENS_PER_UNIT` 的口径），截断是按 code point 做的。
        同一段文本 code point ≥ 字（多出来的是空白），所以按 code point 截等于**少给**，
        方向偏保守；而 `TOKENS_PER_UNIT=2` 对中文本来就是往贵了算，余量足够吸收它。
    """
    # 局部导入：本模块是三臂共用的渲染器，**模块层不许依赖记忆层**——`product_context`
    # 会把 EventStore / db 一并拖进来，而「拿不到 store 就查不了第二遍」（ADR 0010 D2）
    # 靠的正是这条边界，不只是签名里没有 store。这里只借它那一个换算常量。
    from .product_context import TOKENS_PER_UNIT

    if max_context_tokens is None:
        return GATE_TAIL_CODE_POINTS
    headroom = max(0, max_context_tokens - max(0, reserved_output_tokens))
    units = int(Fraction(headroom, TOKENS_PER_UNIT) * TAIL_CONTEXT_SHARE)
    return max(GATE_TAIL_CODE_POINTS, min(units, TAIL_UNITS_CEILING))


ZH_WRITING_PROMPT = """你是一位中文长篇小说的写作搭档。根据作者提供的上文和本场目标，写出这一场的正文。

- 只输出正文：不写标题、章节号、小标题、创作说明，也不用 Markdown 标记。
- 使用第三人称，贴着场上人物的动作、对白和环境来写。
- 承接上文的语气和称呼，不重写已经写过的段落。
- 一次写完这一场，并让结尾自然收束。"""
"""中文草稿的默认写作提示；长度由 ``length_instruction()`` 单独提供。"""


EN_WRITING_PROMPT = """You are a long-form fiction writing partner. Using the supplied prior text and scene goal, write the scene's prose.

- Output prose only: no title, chapter label, heading, writing notes, or Markdown.
- Write in third person through the characters' actions, dialogue, and surroundings.
- Continue the voice and names used in the prior text; do not rewrite material already written.
- Complete the scene in one pass and bring it to a natural close."""
"""English draft's default writing prompt; length is supplied separately."""


DEFAULT_WRITING_PROMPT = ZH_WRITING_PROMPT
"""Backward-compatible export for callers that previously selected the Chinese default writing prompt."""

WRITE_RULE_FORBIDDEN_HINTS = ("秘密", "不知道", "泄露", "剧透", "伏笔", "设定")
"""写作提示（**三臂共用**）里的禁词：出现任何一个 = 把约束漏给 X0，Δ 塌掉。

这是**关键词网**不是语义检查（ADR 0005）：抓得住顺手写出来的那一种，抓不住
换个说法的那一种。入口（/draft、nh draft）用它拒绝自定义写作规则；`assemble()` 本身
保持宽松（测试要用自己的写作提示），中性由入口守。
"""


def length_instruction(spec: LengthSpec) -> str:
    """Render the language-specific output length request in reader-facing units."""
    # 2026-08-02 修复 5：可见目标下压到 (min)–(min+target)/2。
    # 模型首段长度有右尾（实测 3016–3158），超长没有补救手段（续写只允许
    # under-min），所以提示词让模型**宁短**：短了续写兜底，长了整轮判死。
    lower = spec.min_units
    upper = (spec.min_units + spec.target_units) // 2
    if spec.language is DraftLanguage.ZH:
        return (
            f"请用中文写作，篇幅精确控制在 {spec.min_units}–{spec.max_units} 字，"
            f"目标约 {spec.target_units} 字。请在 {lower}–{upper} 字之间自然收束，"
            f"绝不要超过 {spec.max_units} 字——一旦超过，整份草稿作废。"
        )
    return (
        f"Write in English. Keep the length precisely within {spec.min_units}–{spec.max_units} "
        f"words, targeting about {spec.target_units} words. Aim to end naturally around "
        f"{lower}–{upper} words and never exceed {spec.max_units} words — an over-length draft "
        "is discarded."
    )


def system_prompt(spec: LengthSpec, write_rule: str | None = None) -> str:
    """Combine the selected language's shared style with its required length instruction."""
    style = write_rule
    if style is None:
        style = (
            ZH_WRITING_PROMPT if spec.language is DraftLanguage.ZH else EN_WRITING_PROMPT
        )
    return style.strip() + "\n\n" + length_instruction(spec)


def assemble(
    ctx: DraftContext,
    *,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    previous_tail_limit: int = GATE_TAIL_CODE_POINTS,
    write_rule: str | None = None,
) -> list[dict[str, str]]:
    """把一个场景的约束渲染成 OpenAI 兼容的 `messages`。

    Args:
        ctx: 约束集（`draft/context.py`）。它的类型本身就是「cast 无歧义且非空」的证据
            （`ResolvedConstraints`）或「不知道在场是谁」（`UnknownCastConstraints`），
            所以这里**不必也无法**再判一次。
        goal: 这一场要写什么。**自由文本入口，本层看不见它有没有剧透**——见模块 docstring 第四节。
        previous_tail: 上文。空串 = 开篇，整个「上文」块不出现（不留一个空标题）。
        previous_tail_limit: 上文最多保留末尾多少个 code point。**默认值是 X0 对照臂的定义
            （`GATE_TAIL_CODE_POINTS`），三臂必须用这个默认值**；产品档显式传
            `product_tail_limit(...)` 算出来的值。`<= 0` = 完全不给上文。
        length: 已合法的输出篇幅与语言。调用边界已验证，本函数不再做 hard-max 校验。
        write_rule: 自定义写作规则，替换默认写作提示；为空时按 ``length.language``
            选择中英文默认写作提示。

    Returns:
        `[{"role": ..., "content": ...}]`。**本仓库少见的非 Pydantic 出参**，理由是它要原样
        进 `provider.complete(messages=...)` 的线上格式：包一层 Pydantic 只会在调用点
        `model_dump()` 再拆一次，而那次拆解是又一处可以悄悄改内容的地方。
        账上落的也是这个形状（`model_call.in_artifact`）——ADR 0010 说剧透漏进 prompt
        这类错误的**唯一**可发现路径是人去读存下来的 prompt 原文，
        那就别在存之前再变换一次形状。

    Raises:
        ValueError: `goal` 是空白。空 goal 让模型自己编一场戏，而它在账上看起来
            和正常行没有区别。
    """
    if not goal.strip():
        raise ValueError(
            "goal 是空的：这一场要写什么必须说清楚。"
            "空 goal 让模型自己编一场戏，而事后从账上看不出来这一稿为什么跑偏。"
        )

    messages = _base(
        ctx,
        goal=goal,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        write_rule=system_prompt(length, write_rule),
    )
    section = graph_section(ctx)
    if section:
        # **只追加，不重排、不改写前面任何一个字节**（ADR 0010 D4，模块 docstring 第一节）。
        messages[-1] = {**messages[-1], "content": messages[-1]["content"] + "\n\n" + section}
    return messages


def graph_section(ctx: DraftContext) -> str:
    """追加在用户消息尾部的那一段图谱事实。**没有可注入的东西时返回空串。**

    公开出来是为了让「它只追加在尾部」这条性质可以被**逐字节**验证（测试拿它重建整段
    用户消息），而不是靠肉眼比对两段渲染结果。

    ⚠️ 三臂（`PromptForm`）2026-08-25 删掉，本函数因此不再有 `form` 参数；
    秘密下线（ADR 0039）之后这儿也只剩「尚未登场」一块。
    """
    blocks = [b for b in (_forbidden_block(ctx),) if b]
    if not blocks:
        # 这一场没有未来实体可禁。**返回空串而不是一个空标题**：
        # 「【本场设定要点】」后面空一片会让模型以为有一份它没读到的清单。
        return ""
    return "【本场设定要点】\n" + "\n".join(blocks)


# ══════════════════════════════════════════════════════════════════════════
# base —— 三臂共用的那一份（ADR 0010 D4）
# ══════════════════════════════════════════════════════════════════════════


def _base(
    ctx: DraftContext,
    *,
    goal: str,
    previous_tail: str,
    previous_tail_limit: int,
    write_rule: str,
) -> list[dict[str, str]]:
    """默认写作提示 + 上文 + 在场 + 本场目标。**图谱事实一个字都不在这儿。**

    合成一条用户消息而不是拆成多条同角色消息：OpenAI 兼容端点五花八门（本地 vLLM / Ollama /
    各家中转），连续同 role 消息有的接受有的 400，而 kill-gate 跑到一半因为消息形状被拒
    是最坏的失败时机（同 `provider.py` 对配置自洽性的那条理由）。
    """
    parts: list[str] = []
    # `[-0:]` 是整串不是空串，所以 `<= 0` 必须单独分支——否则「不给上文」会变成「全给」。
    tail = previous_tail.strip()[-previous_tail_limit:] if previous_tail_limit > 0 else ""
    if tail:
        parts.append("【上文】\n" + tail)
    # cast 在 X0 里也有 —— ADR 0010 D6，它是作者的输入不是图谱查询的结果。
    # **退化态（ADR 0015 D4）整块不出**（2026-08-22 M1-a）：以前这儿发一句
    # 「【在场】未知。因此这一段不得说破任何尚未公开的秘密。」——「未知」两个字不带信息，
    # 而后半句和下面禁写清单说的是同一件事。删的是重复，不是约束：禁写清单照旧渲染，
    # 那一侧本来就是这句话唯一有效力的形态。**别再加回来**：这一块要精确就得知道谁在场，
    # 而续写的那一刻它还没被写出来，那份精度只有保存之后的验证侧算得准。
    if isinstance(ctx, ResolvedConstraints):
        parts.append("【在场】\n" + "、".join(ctx.cast))
    parts.append("【这一场要写】\n" + goal.strip())
    return [
        {"role": "system", "content": write_rule},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
def _forbidden_block(ctx: DraftContext) -> str:
    """未来实体的名字 + 首现章。

    **这里必然点它们的名，那是设计不是泄漏**：不说「幽泉窟这一场不许出现」，
    模型就不知道该躲开哪个词。

    ⚠️ 2026-08-25 之前这里有两种渲染（X1 清单 / X2 散文），三臂删掉之后**只留清单那一种**
    ——它是生产上唯一跑过的那一份（`product_draft.py` 的 form 恒为 X1）。散文那一版在
    git 历史里，它是为「同一份数据两种排版」这个实验变量而写的，没有别的消费者。
    """
    entities = ctx.forbidden_entities
    if not entities:
        return ""
    listed = "、".join(
        f"{e.node.name}（第 {e.first_appears_chapter} 章首现）" for e in entities
    )
    return f"尚未登场、这一场不得出现：{listed}"
