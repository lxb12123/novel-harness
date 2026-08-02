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

from ..graph import KnowledgeCell, KnowledgeState
from .context import ResolvedConstraints
from .length import DraftLanguage, LengthSpec


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


def length_instruction(spec: LengthSpec) -> str:
    """Render the language-specific output length request in reader-facing units."""
    if spec.language is DraftLanguage.ZH:
        return (
            f"请用中文写作，篇幅精确控制在 {spec.min_units}–{spec.max_units} 字，"
            f"目标约 {spec.target_units} 字。宁可比目标略短，绝不要超过 {spec.max_units} 字"
            "——一旦超过，整份草稿作废。"
        )
    return (
        f"Write in English. Keep the length precisely within {spec.min_units}–{spec.max_units} "
        f"words, targeting about {spec.target_units} words. Prefer slightly under the target "
        f"rather than ever exceeding {spec.max_units} words — an over-length draft is discarded."
    )


def system_prompt(spec: LengthSpec, house_style: str | None = None) -> str:
    """Combine the selected language's shared style with its required length instruction."""
    style = house_style
    if style is None:
        style = ZH_HOUSE_STYLE if spec.language is DraftLanguage.ZH else EN_HOUSE_STYLE
    return style.strip() + "\n\n" + length_instruction(spec)


def assemble(
    ctx: ResolvedConstraints,
    *,
    form: PromptForm,
    goal: str,
    length: LengthSpec,
    previous_tail: str = "",
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
        house_style=system_prompt(length, house_style),
    )
    section = graph_section(ctx, form)
    if section:
        # **只追加，不重排、不改写前面任何一个字节**——D4 的严格前缀性质就是这一行。
        messages[-1] = {**messages[-1], "content": messages[-1]["content"] + "\n\n" + section}
    return messages


def graph_section(ctx: ResolvedConstraints, form: PromptForm) -> str:
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
    ctx: ResolvedConstraints,
    *,
    goal: str,
    previous_tail: str,
    house_style: str,
) -> list[dict[str, str]]:
    """house-style + 上文 + 在场 + 本场目标。**图谱事实一个字都不在这儿。**

    合成一条用户消息而不是拆成多条同角色消息：OpenAI 兼容端点五花八门（本地 vLLM / Ollama /
    各家中转），连续同 role 消息有的接受有的 400，而 kill-gate 跑到一半因为消息形状被拒
    是最坏的失败时机（同 `provider.py` 对配置自洽性的那条理由）。
    """
    parts: list[str] = []
    tail = previous_tail.strip()[-800:]
    if tail:
        parts.append("【上文】\n" + tail)
    # cast 在 X0 里也有 —— ADR 0010 D6，它是作者的输入不是图谱查询的结果。
    parts.append("【在场】\n" + "、".join(ctx.cast))
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


def _matrix_block(ctx: ResolvedConstraints, form: PromptForm) -> str:
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


def _secrets_block(ctx: ResolvedConstraints, form: PromptForm) -> str:
    labels = ctx.secret_labels
    if not labels:
        return ""
    joined = "、".join(labels)
    if form is PromptForm.X1:
        return f"这一场不得写破：{joined}"
    quantifier = "这一条" if len(labels) == 1 else "这几条"
    return f"{joined}{quantifier}，这一场还写不得。"


def _forbidden_block(ctx: ResolvedConstraints, form: PromptForm) -> str:
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
