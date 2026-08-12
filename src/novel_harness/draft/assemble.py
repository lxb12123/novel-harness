"""约束 → prompt 的**唯一**出口：X0 / X1 / X2 三臂（ADR 0010 / EVAL_PROTOCOL §2）。

三臂**不是三套 prompt 构造器**，是同一个函数的三个 `form` 取值。kill-gate 和产品起草共用它——
否则 gate 测的就不是产品会发的东西（同 `checks/base.py` 的「判分器 == Validator，同一份代码」）。

── 一、X0 是 X1/X2 的**严格前缀**，这是结构，不是承诺（ADR 0010 D4）────────

`_base()` 只有一份，三臂都走它；X1/X2 只在它算出的那段用户消息**尾部**追加一段图谱段。
于是「X0 = X1 去掉图谱段之后逐字节剩下的东西」不是一句自觉，而是
`x1[:-1] == x0[:-1]` 加 `x1[-1]["content"] == x0[-1]["content"] + "\\n\\n" + graph_section(...)`
两条可执行断言（`tests/test_draft_assemble.py` 钉着）。

**不许给某一臂单独加一句行为指令。** 那正是协议 §2 点名要修掉的 PLAN §5.7 内建混淆：
X1 与 X2 之间只许差「清单 vs 散文」这一个变量，多出来的任何一句「请特别注意……」都会让
`Δ2 − Δ1` 变成「form + 那句话」的合成效应，而 ADR 0009 会把它读成「form 重要」。

── 二、`cast` 三臂都有（ADR 0010 D6，本模块唯一影响实验结果的构造裁定）────

`cast` 是作者写在场景块 `<!-- nh: cast=... -->` 里的**称呼原文**，是输入不是图谱查询结果，
所以它不算「图谱事实」，X0 也有。把它从 X0 拿掉会让 X0 写到别人身上去 —— 那不是「产品去掉
图谱注入」，是**另一个任务**，臂间差异里会混进「写的根本不是同一场戏」。理由全文在 ADR 0010 D6，
连「这条裁定往哪边错」一起写了。**改这一条要重造小册子并重跑整轮。**

── 三、只有标签进 prompt，永不 tell（协议 §2 / 第 4 道 arch-guard）──────────

本模块只读 `ctx` 上的五类字段（ADR 0010 D3 的白名单）：`cast`、`secret_labels`、
`forbidden_names` + 首现章、`KnowledgeCell` 的三个标量、`matrix.characters/secrets` 的 `.name`。
**签名里没有 store**——拿不到 store 就查不了第二遍，prompt 里的事实和 `eval/leak.py` 判分用的
事实必然是同一个对象算出来的。`.props` 一个字符都不碰（tell 住在那儿）。

tell 一旦进了 X1/X2 的 prompt，两臂 100% 命中自己写进去的词，`Δ` 翻负，预注册裁决表逐字
读出「KILL 起草线」——**把一个本来对的项目砍掉，而全程没有任何东西会红。**

── 四、它拦不住什么（诚实交代）─────────────────────────────────────────────

- **`goal` / `previous_tail` / `house_style` 是三个自由文本入口。** 有人把 PLANNED 的内容
  用自然语言写进 `goal`（「写萧决发现自己血脉有异——他还不知道那是家族封印的反噬」），
  本模块、第 4 道 arch-guard、类型系统**全都看不见**：判它需要回答「这句话是不是把伏笔说破了」，
  那是语义判断，ADR 0005 在 v1 里禁止本仓库长出这种能力。守它的是 ADR 0010 D3 末尾那条
  review 判据，以及 `synth/leak_selfcheck.py` 的「`goal`/`prior` 不含任何 tell」放行条件
  （精确子串，零语义）。**修正案 4 裁定 B 把它升成了机器判据，但那道闸在 `synth/`，不在这儿。**
- **本模块不校验 X1 与 X2 的可比性。** 那是 `eval/confound_lint.py` 的活，且它是**独立的**
  第二双眼睛：这里的渲染模板改一次，那边就该重新报一次专名集合与字数比。
  本文件的测试里有一条同口径的自检，但它只覆盖测试里那几个 fixture，不是全量保证。
"""

from __future__ import annotations

from enum import StrEnum
from fractions import Fraction

from ..graph import KnowledgeCell, KnowledgeState
from .context import DraftContext, ResolvedConstraints
from .length import DraftLanguage, LengthSpec


CONTINUATION_GOAL = "顺着上文往下写，接住作者已经起的头，不要另起一段新情节。"
"""续写模式的 `goal`（[ADR 0015](../../../docs/adr/0015-inline-continuation-is-a-short-draft.md) D3）。

**它是常量、住在后端，而不是前端传一个默认值**——`goal` 是 ADR 0010 点名的三个自由文本
入口之一（作者能把伏笔用自然语言写进去，本层看不见）。续写模式把这个入口**关掉**换成
这一句，是在缩小那个洞。前端能传的东西作者就能改，所以它不能住在前端。"""

UNKNOWN_CAST_LINE = "【在场】\n未知。因此这一段不得说破任何尚未公开的秘密。"
"""退化态的「在场」块（ADR 0015 D4）。**它同时说了两件事**：不知道谁在场，以及
由此推出的约束。分开说会让模型只读到前半句，而前半句单独出现时最自然的反应是自己猜一个。

常量在这儿而不是拼在 `_base()` 里，是为了让「续写到底给模型看了什么」可以被逐字节测。"""


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


class PromptForm(StrEnum):
    """三臂 = 同一个渲染器的三个取值（协议 §2 那张表）。"""

    X0 = "X0"
    """对照臂：house-style + 上文 + 在场 + 本场目标。**零图谱事实。**"""

    X1 = "X1"
    """事实清单臂：X0 + 认知矩阵三态（含 `believed_value`）+ 秘密显示名 + 未来实体名/首现章。"""

    X2 = "X2"
    """叙事化臂：X0 + **同一份矩阵**改写成散文。与 X1 只差「清单 vs 散文」这一个变量。"""


ZH_HOUSE_STYLE = """你是一位中文长篇小说的写作搭档。根据作者提供的上文和本场目标，写出这一场的正文。

- 只输出正文：不写标题、章节号、小标题、创作说明，也不用 Markdown 标记。
- 使用第三人称，贴着场上人物的动作、对白和环境来写。
- 承接上文的语气和称呼，不重写已经写过的段落。
- 一次写完这一场，并让结尾自然收束。"""
"""中文草稿的共享文风要求；长度由 ``length_instruction()`` 单独提供。"""


EN_HOUSE_STYLE = """You are a long-form fiction writing partner. Using the supplied prior text and scene goal, write the scene's prose.

- Output prose only: no title, chapter label, heading, writing notes, or Markdown.
- Write in third person through the characters' actions, dialogue, and surroundings.
- Continue the voice and names used in the prior text; do not rewrite material already written.
- Complete the scene in one pass and bring it to a natural close."""
"""English draft's shared style requirements; length is supplied separately."""


DEFAULT_HOUSE_STYLE = ZH_HOUSE_STYLE
"""Backward-compatible export for callers that previously selected the Chinese house style."""

HOUSE_STYLE_FORBIDDEN_HINTS = ("秘密", "不知道", "泄露", "剧透", "伏笔", "设定")
"""文风提示（**三臂共用**）里的禁词：出现任何一个 = 把约束漏给 X0，Δ 塌掉。

这是**关键词网**不是语义检查（ADR 0005）：抓得住顺手写出来的那一种，抓不住
换个说法的那一种。入口（/draft、nh draft）用它拒绝自定义文风；`assemble()` 本身
保持宽松（测试要用自己的文风），中性由入口守。
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


def system_prompt(spec: LengthSpec, house_style: str | None = None) -> str:
    """Combine the selected language's shared style with its required length instruction."""
    style = house_style
    if style is None:
        style = ZH_HOUSE_STYLE if spec.language is DraftLanguage.ZH else EN_HOUSE_STYLE
    return style.strip() + "\n\n" + length_instruction(spec)


def assemble(
    ctx: DraftContext,
    *,
    form: PromptForm,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
    previous_tail_limit: int = GATE_TAIL_CODE_POINTS,
    house_style: str | None = None,
) -> list[dict[str, str]]:
    """把一个场景的约束渲染成 OpenAI 兼容的 `messages`。

    Args:
        ctx: **已解析**的约束集（`draft/context.py`）。它的类型本身就是「cast 无歧义且非空」
            的证据——退化态（`must_not_reveal` = 全部秘密）在这个类型里表示不出来，
            所以这里**不必也无法**再判一次。矩阵绑在它身上，X1/X2 拿的必然是同一份。
        form: 三臂之一。**接受字符串**（`PromptForm(form)` 归一）：协议 §6 的 FORM-PIVOT
            分支说生产默认翻成 `NH_DRAFT_FORM=X2`，那个值从环境变量上来时是 `str`。
        goal: 这一场要写什么。**自由文本入口，本层看不见它有没有剧透**——见模块 docstring 第四节。
        previous_tail: 上文。空串 = 开篇，整个「上文」块不出现（不留一个空标题）。
        previous_tail_limit: 上文最多保留末尾多少个 code point。**默认值是 X0 对照臂的定义
            （`GATE_TAIL_CODE_POINTS`），三臂必须用这个默认值**；产品档显式传
            `product_tail_limit(...)` 算出来的值。`<= 0` = 完全不给上文。
        length: 已合法的输出篇幅与语言。调用边界已验证，本函数不再做 hard-max 校验。
        house_style: 可选文风系统提示；为空时按 ``length.language`` 选择中英文默认文风。

    Returns:
        `[{"role": ..., "content": ...}]`。**本仓库少见的非 Pydantic 出参**，理由是它要原样
        进 `provider.complete(messages=...)` 的线上格式：包一层 Pydantic 只会在调用点
        `model_dump()` 再拆一次，而那次拆解是又一处可以悄悄改内容的地方。
        `runs/*.jsonl` 落盘的也是这个形状——ADR 0010 说 tell 漏进 prompt 这类错误的**唯一**
        可发现路径是人去读存下来的 prompt 原文，那就别在存之前再变换一次形状。

    Raises:
        ValueError: `form` 不是三臂之一；或 `goal` 是空白。空 goal 会让三臂都写不出确定的东西，
            这条陷阱只贡献噪声——而它在 `runs/*.jsonl` 里看起来和正常行没有区别。
    """
    form = PromptForm(form)
    if not goal.strip():
        raise ValueError(
            "goal 是空的：这一场要写什么必须说清楚。"
            "空 goal 让三臂各写各的，那条陷阱只往 Δ 里加方差，且事后从 runs/*.jsonl 看不出来。"
        )

    messages = _base(
        ctx,
        goal=goal,
        previous_tail=previous_tail,
        previous_tail_limit=previous_tail_limit,
        house_style=system_prompt(length, house_style),
    )
    section = graph_section(ctx, form)
    if section:
        # **只追加，不重排、不改写前面任何一个字节**——D4 的严格前缀性质就是这一行。
        messages[-1] = {**messages[-1], "content": messages[-1]["content"] + "\n\n" + section}
    return messages


def graph_section(ctx: DraftContext, form: PromptForm) -> str:
    """X1/X2 相对 X0 多出来的那一段，**X0 恒为空串**。

    公开出来是为了让「X0 是前缀」这条性质可以被**逐字节**验证（测试拿它重建 X1 的内容），
    而不是靠肉眼比对两段渲染结果。`confound_lint` 若要单独比这一段也走这里。

    X1 与 X2 读的是**同一个** `ctx.matrix`、**同一批**格子、**同一个**顺序——它们的差别
    只在措辞模板。协议 §2 的反混淆铁律在这一层是「同一份数据两种排版」，不是「两次渲染」。
    """
    form = PromptForm(form)
    if form is PromptForm.X0:
        return ""

    blocks = [
        b
        for b in (
            _matrix_block(ctx, form),
            _secrets_block(ctx, form),
            _forbidden_block(ctx, form),
        )
        if b
    ]
    if not blocks:
        # 这一场确实没有任何图谱事实可注入（没有秘密、没有未来实体）。
        # 此时三臂逐字节相同，是**正确的退化**：注入的内容为空，臂间差异也该为零。
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
    house_style: str,
) -> list[dict[str, str]]:
    """house-style + 上文 + 在场 + 本场目标。**图谱事实一个字都不在这儿。**

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
    # 退化态（ADR 0015 D4）没有 cast：**明说「未知」**，不拿空串冒充一份精确清单。
    if isinstance(ctx, ResolvedConstraints):
        parts.append("【在场】\n" + "、".join(ctx.cast))
    else:
        parts.append(UNKNOWN_CAST_LINE)
    parts.append("【这一场要写】\n" + goal.strip())
    return [
        {"role": "system", "content": house_style},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


# ══════════════════════════════════════════════════════════════════════════
# 图谱段 —— 同一份数据的两种排版
# ══════════════════════════════════════════════════════════════════════════


def _panel_order(ctx: ResolvedConstraints) -> list[tuple[str, str, KnowledgeCell]]:
    """行 = 角色（作者写的 cast 顺序），列 = 秘密，取自 `characters`/`secrets` 而**不是**
    `cells` 的存储顺序——后者是 store 的实现细节，两臂逐格对应不能建立在它上面。
    """
    matrix = ctx.matrix
    return [
        (who.name, secret.name, matrix.cell(who.id, secret.id))
        for who in matrix.characters
        for secret in matrix.secrets
    ]


def _matrix_block(ctx: DraftContext, form: PromptForm) -> str:
    # 退化态没有矩阵：行就是 cast，没有 cast 就没有行。给一个空矩阵会让下游
    # 以为「查过了，确实没人知道任何事」——那是另一句话，而且是假的。
    if not isinstance(ctx, ResolvedConstraints):
        return ""
    cells = _panel_order(ctx)
    if not cells:
        return ""
    if form is PromptForm.X1:
        head = f"截至第 {ctx.chapter} 章的认知边界："
        return head + "\n" + "\n".join(_x1_cell(*c) for c in cells)
    head = f"截至第 {ctx.chapter} 章，眼下的情形是："
    return head + "".join(_x2_cell(*c) for c in cells)


def _x1_cell(who: str, secret: str, cell: KnowledgeCell) -> str:
    since = f"（第 {cell.since_chapter} 章起）" if cell.since_chapter is not None else ""
    if cell.state is KnowledgeState.KNOWS:
        return f"- {who}：知道「{secret}」{since}"
    if cell.state is KnowledgeState.BELIEVES:
        if cell.believed_value:
            return f"- {who}：误以为「{secret}」是「{cell.believed_value}」{since}"
        # BELIEVES 但没记下他以为的是什么。**不许在这儿编一个**：编出来的那句话
        # 会同时进 X1 和 X2，两臂一起被污染，而它不来自图。
        return f"- {who}：对「{secret}」持错误认知{since}"
    return f"- {who}：还不知道「{secret}」"


def _x2_cell(who: str, secret: str, cell: KnowledgeCell) -> str:
    since = f"从第 {cell.since_chapter} 章起" if cell.since_chapter is not None else ""
    if cell.state is KnowledgeState.KNOWS:
        return f"{who}{since}就知道「{secret}」。" if since else f"{who}已经知道「{secret}」。"
    if cell.state is KnowledgeState.BELIEVES:
        if cell.believed_value:
            return f"{who}{since}一直误以为「{secret}」是「{cell.believed_value}」。"
        return f"{who}{since}对「{secret}」抱着错误的认知。"
    return f"{who}到现在还不知道「{secret}」。"


def _secrets_block(ctx: DraftContext, form: PromptForm) -> str:
    labels = ctx.secret_labels
    if not labels:
        return ""
    joined = "、".join(labels)
    if form is PromptForm.X1:
        return f"这一场不得写破：{joined}"
    quantifier = "这一条" if len(labels) == 1 else "这几条"
    return f"{joined}{quantifier}，这一场还写不得。"


def _forbidden_block(ctx: DraftContext, form: PromptForm) -> str:
    """未来实体的名字 + 首现章。**这一侧「标签 ⟂ tell」不成立**：`血枭盟` 自身即检测词，
    X1/X2 必然点它的名 → echo 风险 → 协议让 `future_leak` 只作描述性地板、不主导裁决
    （`draft/context.py` 第三节说的就是这条）。这里照写不误，读结果的人要知道这一点。
    """
    entities = ctx.forbidden_entities
    if not entities:
        return ""
    if form is PromptForm.X1:
        listed = "、".join(
            f"{e.node.name}（第 {e.first_appears_chapter} 章首现）" for e in entities
        )
        return f"尚未登场、这一场不得出现：{listed}"
    first, *rest = entities
    prose = f"{first.node.name}要到第 {first.first_appears_chapter} 章才头一回出现"
    for e in rest:
        prose += f"，{e.node.name}要到第 {e.first_appears_chapter} 章"
    return prose + "；这一场里它们都还不该露面。"
