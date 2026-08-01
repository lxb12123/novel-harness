"""`draft/assemble.py` —— 三臂 prompt 渲染器（ADR 0010 的 D1–D6 就是本文件的验收标准）。

**这个文件测的不是「prompt 好不好」，是「仪器接没接反」。** 它守的四件事，每一件坏掉的样子
都不是「少个功能」，而是**整轮 kill-gate 读出一个假裁决**：

1. **三臂共用一个 base（D4）。** 某一臂多一句话 → 臂间差异里混进那句话的效应 →
   ADR 0009 把它当成「注入有用」或「form 重要」写进去。
2. **X1 与 X2 只差「清单 vs 散文」（协议 §2 反混淆铁律）。** 两臂专名不同或字数差太多 →
   `confound_lint` 报警 → FORM-PIVOT 分支本轮作废（协议 §6 第 3 行），白跑 75 次生成。
3. **tell 永不进 prompt（D3）。** 进了 → X1/X2 命中自己写进去的词 → `Δ` 翻负 →
   裁决表逐字读出 **KILL 起草线**，砍掉一条本来对的产品线，**全程没有东西会红**。
4. **cast 三臂都有（D6）。** 从 X0 拿掉 → X0 写到别人身上 → 那不是「去掉注入」是「换了个任务」。

复用 `test_knowledge.FakeGraph`（同 `test_draft_context.py`）：它是 StoryGraph 契约的参考实现。

── 本文件**测不到**什么（诚实交代，同仓库其它守卫的自述）──────────────────

- **`goal` / `previous_tail` 里的剧透。** 判它要回答「这句话是不是把伏笔说破了」= 语义判断，
  ADR 0005 禁止。守它的是 ADR 0010 D3 末尾那条 review 判据 + `synth/leak_selfcheck.py`
  的精确子串放行条件（修正案 4 裁定 B）。**这里的绿不代表 prompt 没剧透。**
- **第 2 条里的字数比与专名集合只在本文件这几个 fixture 上验过。** 真正的闸门是
  `eval/confound_lint.py`，它跑在每一轮真实渲染上。这里的自检是「改模板时立刻红」，
  不是「所有输入都合格」。
"""

from __future__ import annotations

import inspect
import re

import pytest
import novel_harness.draft.assemble as assemble_module
from test_knowledge import (
    BLOODLINE,
    GU_QINGYIN,
    LI_GUANJIA,
    PID,
    XIAO_JUE,
    FakeGraph,
    edge,
    node,
)

from novel_harness.draft.assemble import (
    DEFAULT_HOUSE_STYLE,
    EN_HOUSE_STYLE,
    PromptForm,
    ZH_HOUSE_STYLE,
    assemble,
    graph_section,
)
from novel_harness.draft.context import ResolvedConstraints, resolve_constraints
from novel_harness.draft.length import DraftLanguage, LengthSpec, M2_LENGTH_SPEC
from novel_harness.graph import EdgeType, NodeLabel

GOAL = "顾清音在书房追问萧决那晚到底发生了什么，萧决避而不答。"
TAIL = "廊下的灯笼灭了最后一盏，风里有股铁锈味。"
EN_LENGTH = LengthSpec(
    language=DraftLanguage.EN, min_units=1200, target_units=1500, max_units=1800
)

TELL = "玄血蛊"
"""血脉秘密的**内容 tell**（EVAL_PROTOCOL §4 第 1 条边界）。它是判分器那一侧的词。"""

TWIST = "萧决其实是魔尊之子，第 200 章揭晓"
"""挂在秘密节点 `props` 上的额外字段。`NodeProps` 是 `extra="allow"`，它真的存在。"""

XUEXIAO = node("faction:demo:01JA", NodeLabel.FACTION, "血枭盟", first_appears_chapter=8)
YOUQUAN = node("location:demo:01JB", NodeLabel.LOCATION, "幽泉窟", first_appears_chapter=10)


def _full_store(*, with_tell: bool = False) -> FakeGraph:
    """三态齐全 + 两个未来实体：萧决 KNOWS(ch3)、李管家 BELIEVES「已泄露」(ch4)、顾清音 UNKNOWN。"""
    bloodline = node(BLOODLINE.id, NodeLabel.SECRET, "血脉秘密", twist=TWIST)
    return FakeGraph(
        [XIAO_JUE, GU_QINGYIN, LI_GUANJIA, bloodline, XUEXIAO, YOUQUAN],
        [
            edge(XIAO_JUE.id, bloodline.id, EdgeType.KNOWS, 3),
            edge(LI_GUANJIA.id, bloodline.id, EdgeType.BELIEVES, 4, believed_value="已泄露"),
        ],
        extra_aliases={TELL: [bloodline]} if with_tell else None,
    )


def _full_ctx(*, with_tell: bool = False) -> ResolvedConstraints:
    return resolve_constraints(
        _full_store(with_tell=with_tell),
        PID,
        5,
        [XIAO_JUE.name, LI_GUANJIA.name, GU_QINGYIN.name],
        secrets=[BLOODLINE.id],
    )


def _arm(ctx: ResolvedConstraints, form: PromptForm) -> list[dict[str, str]]:
    """同一份 goal / previous_tail 喂三臂 —— 臂间**唯一**允许变的是 `form`。"""
    return assemble(ctx, form=form, goal=GOAL, length=M2_LENGTH_SPEC, previous_tail=TAIL)


def _text(messages: list[dict[str, str]]) -> str:
    return "\n".join(m["content"] for m in messages)


# ══════════════════════════════════════════════════════════════════════════
# ① D4：三臂共用一个 base，X0 是严格前缀
# ══════════════════════════════════════════════════════════════════════════


def test_x0_is_the_byte_exact_prefix_of_both_injected_arms() -> None:
    """**这条是 D4 从「一句承诺」变成「一条测试」的那一步。**

    不是「看起来差不多」：系统消息逐字节相同、用户消息的前缀逐字节相同，
    追加的那一段可以被 `graph_section()` 原样重建出来。
    """
    ctx = _full_ctx()
    x0 = _arm(ctx, PromptForm.X0)
    x1 = _arm(ctx, PromptForm.X1)
    x2 = _arm(ctx, PromptForm.X2)

    assert x0[:-1] == x1[:-1] == x2[:-1]  # 系统消息（含 house style）逐字节同一份
    assert [m["role"] for m in x0] == [m["role"] for m in x1] == [m["role"] for m in x2]

    base = x0[-1]["content"]
    assert x1[-1]["content"] == base + "\n\n" + graph_section(ctx, PromptForm.X1)
    assert x2[-1]["content"] == base + "\n\n" + graph_section(ctx, PromptForm.X2)
    # 反向也写出来：去掉图谱段之后剩下的，就是 X0，一个字节不多不少。
    assert x1[-1]["content"].removesuffix("\n\n" + graph_section(ctx, PromptForm.X1)) == base


def test_the_graph_section_is_empty_for_the_control_arm() -> None:
    """X0 = 「零图谱事实」（协议 §2）。这条是上面那条前缀性质的另一半。"""
    assert graph_section(_full_ctx(), PromptForm.X0) == ""


def test_the_control_arm_names_no_secret_and_no_future_entity() -> None:
    """X0 里不许出现秘密显示名和未来实体名——否则「对照」二字不成立。"""
    ctx = _full_ctx()
    text = _text(_arm(ctx, PromptForm.X0))

    for label in ctx.secret_labels:
        assert label not in text
    for name in ctx.forbidden_names:
        assert name not in text
    # 非空证明：这两组名字**确实存在**，是被 X0 挡掉的，不是 fixture 本来就空。
    assert ctx.secret_labels == ["血脉秘密"]
    assert ctx.forbidden_names == ["血枭盟", "幽泉窟"]


def test_the_cast_is_in_all_three_arms() -> None:
    """ADR 0010 D6：`cast` 是作者写在场景块里的**输入**，不是图谱查询的结果。

    把它从 X0 拿掉会让 X0 写到别人身上去 → 臂间差异里混进「写的不是同一场戏」，
    还可能把 X0 的泄漏率压到地板线 0.50 以下 → 协议 §6 第一行判 INVALID，
    而一个由 prompt 构造方式造成的 INVALID，重造多少次陷阱都不会好。
    """
    ctx = _full_ctx()
    for form in PromptForm:
        text = _text(_arm(ctx, form))
        for who in ctx.cast:
            assert who in text, f"{form} 少了在场角色 {who}"


def test_the_previous_tail_is_optional_and_leaves_no_empty_heading() -> None:
    """开篇（`previous_tail=""`）不该在 prompt 里留一个空的「上文」标题。"""
    ctx = _full_ctx()
    with_tail = _arm(ctx, PromptForm.X0)
    without = assemble(ctx, form=PromptForm.X0, goal=GOAL, length=M2_LENGTH_SPEC)

    assert TAIL in with_tail[-1]["content"]
    assert "【上文】" in with_tail[-1]["content"]
    assert "【上文】" not in without[-1]["content"]
    assert without[-1]["content"].startswith("【在场】")


# ══════════════════════════════════════════════════════════════════════════
# ② 反混淆铁律：X1 与 X2 只差「清单 vs 散文」
# ══════════════════════════════════════════════════════════════════════════


def test_the_two_injected_arms_say_the_same_things_in_two_layouts() -> None:
    """专名集合相同、章号集合相同、字数在 ±15% 内 —— 与 `confound_lint` 同口径。

    这三项都是**集合/数值**判断，零语义（ADR 0005）。它们一起排除的是最贵的那种混淆：
    X2 顺手多带一句行为指令（PLAN §5.7 的内建混淆，协议 §2 点名要修掉的那条），
    于是 `Δ2 − Δ1` 量的是「form + 那句话」，而 ADR 0009 会把它读成「form 重要」。
    """
    ctx = _full_ctx()
    s1 = graph_section(ctx, PromptForm.X1)
    s2 = graph_section(ctx, PromptForm.X2)
    known = [*ctx.cast, *ctx.secret_labels, *ctx.forbidden_names]

    assert {n for n in known if n in s1} == {n for n in known if n in s2} == set(known)
    # 章号也必须一一对应：X2 少写一个「第 8 章首现」就是少注入了一条事实。
    assert set(re.findall(r"\d+", s1)) == set(re.findall(r"\d+", s2))
    assert 0.85 <= len(s2) / len(s1) <= 1.15, f"字数比 {len(s2) / len(s1):.3f} 出界"


def test_both_injected_arms_render_all_three_knowledge_states() -> None:
    """KNOWS / BELIEVES / UNKNOWN 三态都得渲染得出来。

    UNKNOWN 是**闭世界推导出来的断言**，不是「查不到」——它恰恰是这个产品要卖的那一格
    （「谁在第几章还不该知道什么」）。少了它，注入臂等于什么都没说。
    """
    ctx = _full_ctx()
    for form in (PromptForm.X1, PromptForm.X2):
        section = graph_section(ctx, form)
        assert "萧决" in section and "第 3 章" in section  # KNOWS + since_chapter
        assert "李管家" in section and "第 4 章" in section  # BELIEVES + since_chapter
        assert "顾清音" in section and "还不知道" in section  # UNKNOWN


def test_the_believed_value_reaches_both_injected_arms() -> None:
    """误信值（「以为已泄露」）是 BELIEVES 这一态的**全部内容**，丢了它 = 退化成 KNOWS。"""
    ctx = _full_ctx()
    for form in (PromptForm.X1, PromptForm.X2):
        assert "已泄露" in graph_section(ctx, form)


# ══════════════════════════════════════════════════════════════════════════
# ③ D3：tell 永不进 prompt
# ══════════════════════════════════════════════════════════════════════════


def test_no_tell_and_no_props_reach_any_arm() -> None:
    """**这条是本文件存在的主要理由。**

    tell 进了 X1/X2 → 两臂 100% 命中自己写进去的词 → `Δ` 翻负 → 预注册裁决表读出
    「KILL 起草线」：把一个本来对的项目砍掉，而全程没有任何东西会红。
    """
    ctx = _full_ctx(with_tell=True)
    for form in PromptForm:
        text = _text(_arm(ctx, form))
        assert TELL not in text, f"{form} 的 prompt 里出现了 tell"
        assert TWIST not in text, f"{form} 的 prompt 里出现了 props.twist"
    # 进 prompt 的是**显示名**，它和 tell 是两个字符串——这是「不自己命中自己」的机械保证。
    assert "血脉秘密" in _text(_arm(ctx, PromptForm.X1))


def test_the_tell_really_is_reachable_in_the_graph() -> None:
    """上一条的非空证明：tell 和 twist **确实在图里**，是被 `ResolvedConstraints` 收窄挡掉的。

    没有这一条，只要哪天 fixture 少写了一个别名，上面那条会永远绿着通过。
    """
    store = _full_store(with_tell=True)

    hit = store.resolve(PID, [TELL])[0].unique_node
    assert hit is not None and hit.id == BLOODLINE.id
    assert TWIST in hit.model_dump_json()


def test_the_house_style_names_nobody_and_hints_at_no_constraint() -> None:
    """共用系统提示的两条硬约束（见 `DEFAULT_HOUSE_STYLE` 的 docstring）。

    第二条是关键：house style 里若写了「不要写出角色还不知道的事」这类**通用版**约束，
    等于把处理组的东西发一份给对照组 —— X0 的泄漏率被自己压低，`Δ` 塌掉，实验读出
    「注入没用」，而真实原因是 base 里替它做了一半。

    下面是一张**关键词网**，不是语义检查：它抓得住顺手写出来的那一种，抓不住换个说法的那一种。
    """
    for name in ("萧决", "顾清音", "李管家", "血脉秘密", "血枭盟", "幽泉窟", TELL):
        assert name not in DEFAULT_HOUSE_STYLE
    for hint in ("秘密", "不知道", "泄露", "剧透", "伏笔", "设定"):
        assert hint not in DEFAULT_HOUSE_STYLE, f"house style 里出现了 {hint!r} —— 它三臂共用"
    assert "600–1000" not in DEFAULT_HOUSE_STYLE


def test_default_styles_do_not_constrain_control_arm_content() -> None:
    assert "凭空" not in DEFAULT_HOUSE_STYLE
    assert "凭空" not in ZH_HOUSE_STYLE
    assert "invent" not in EN_HOUSE_STYLE.lower()
    assert "absent" not in EN_HOUSE_STYLE.lower()


# ══════════════════════════════════════════════════════════════════════════
# ④ 退化形态：没有约束可注入时，三臂必须收敛成同一份
# ══════════════════════════════════════════════════════════════════════════


def test_nothing_to_inject_collapses_all_three_arms_into_one() -> None:
    """没有秘密、没有未来实体 → 图谱段为空 → 三臂逐字节相同。

    这是**正确的退化**，不是 bug：注入的内容为空，臂间差异也该为零。
    反过来说，此时若 X1 仍多出一个「【本场设定要点】」空标题，那就是一句只有注入臂才有的
    额外指令 —— 正是反混淆铁律要禁的东西。
    """
    store = FakeGraph([XIAO_JUE, GU_QINGYIN, BLOODLINE], [])
    ctx = resolve_constraints(store, PID, 5, [XIAO_JUE.name], secrets=[])

    assert ctx.secret_labels == [] and ctx.forbidden_names == [] and ctx.matrix.cells == []
    assert graph_section(ctx, PromptForm.X1) == graph_section(ctx, PromptForm.X2) == ""
    assert _arm(ctx, PromptForm.X0) == _arm(ctx, PromptForm.X1) == _arm(ctx, PromptForm.X2)


def test_an_empty_must_not_reveal_still_renders_the_matrix() -> None:
    """对照：在场的人**全都知道** → 没有「不得写破」那一行，但矩阵照渲染。

    没有这一条，上面那条也可能是因为「图谱段恒为空」而绿的——而恒为空的注入臂
    等于三臂全是 X0，`Δ` 恒为 0，gate 读出 KILL。
    """
    store = FakeGraph(
        [XIAO_JUE, BLOODLINE], [edge(XIAO_JUE.id, BLOODLINE.id, EdgeType.KNOWS, 3)]
    )
    ctx = resolve_constraints(store, PID, 5, [XIAO_JUE.name], secrets=[BLOODLINE.id])

    assert ctx.secret_labels == [] and ctx.forbidden_names == []
    for form in (PromptForm.X1, PromptForm.X2):
        section = graph_section(ctx, form)
        assert "萧决" in section and "血脉秘密" in section
        assert "不得写破" not in section and "写不得" not in section
        assert "尚未登场" not in section


def test_no_forbidden_entities_leaves_no_dangling_line() -> None:
    """只有秘密没有未来实体：不许留下「尚未登场、这一场不得出现：」后面空一片。"""
    store = FakeGraph([XIAO_JUE, GU_QINGYIN, BLOODLINE], [])
    ctx = resolve_constraints(store, PID, 5, [GU_QINGYIN.name], secrets=[BLOODLINE.id])

    for form in (PromptForm.X1, PromptForm.X2):
        section = graph_section(ctx, form)
        assert "血脉秘密" in section
        assert "尚未登场" not in section and "头一回出现" not in section
        assert not section.rstrip().endswith("：")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 入参：坏配置在这里红，不要留到跑了一半
# ══════════════════════════════════════════════════════════════════════════


def test_form_accepts_the_string_that_comes_from_the_environment() -> None:
    """协议 §6 的 FORM-PIVOT 分支说生产默认翻成 `NH_DRAFT_FORM=X2` —— 那个值是 `str`。

    `PromptForm(form)` 归一，顺带让写错的取值在**渲染时**就抛，而不是安静地当成 X0
    （那会让一整臂变成对照组，Δ 恒为 0，裁决表读出 KILL）。
    """
    ctx = _full_ctx()
    assert assemble(ctx, form="X2", goal=GOAL, length=M2_LENGTH_SPEC) == assemble(
        ctx, form=PromptForm.X2, goal=GOAL, length=M2_LENGTH_SPEC
    )
    with pytest.raises(ValueError):
        assemble(ctx, form="x2", goal=GOAL, length=M2_LENGTH_SPEC)  # 大小写不同 = 不是那个取值
    with pytest.raises(ValueError):
        assemble(ctx, form="X3", goal=GOAL, length=M2_LENGTH_SPEC)


def test_a_blank_goal_is_rejected_at_render_time() -> None:
    """空 goal 让三臂各写各的：那条陷阱只往 Δ 里加方差，而事后看 `runs/*.jsonl` 看不出异常。"""
    ctx = _full_ctx()
    with pytest.raises(ValueError, match="goal"):
        assemble(ctx, form=PromptForm.X1, goal="   ", length=M2_LENGTH_SPEC)


def test_assemble_needs_no_store_at_all() -> None:
    """ADR 0010 D2：签名里没有 store、没有 project_id。**拿不到 store 就查不了第二遍。**

    只要签名里有 store，某天就会有人为了「让模型知道得更全」在这里补一次查询——
    那一刻起 prompt 里的事实和 `eval/leak.py` 判分用的事实就是两次独立查询的结果，
    gate 测的不再是产品会发的东西。
    """
    params = set(inspect.signature(assemble).parameters)
    assert params == {"ctx", "form", "goal", "length", "previous_tail", "house_style"}
    assert inspect.signature(assemble).parameters["length"].default is inspect.Parameter.empty


def test_length_instruction_is_explicit_in_chinese_system_prompt() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(M2_LENGTH_SPEC)

    assert "中文" in prompt
    assert "2000–3000 字" in prompt
    assert "2500 字" in prompt
    assert "600–1000" not in prompt


def test_length_instruction_is_explicit_in_english_system_prompt() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(EN_LENGTH)

    assert "English" in prompt
    assert "1200–1800 words" in prompt
    assert "1500 words" in prompt


def test_custom_house_style_cannot_bypass_the_length_instruction() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(EN_LENGTH, house_style="  Keep the prose spare.  ")

    assert prompt.startswith("Keep the prose spare.\n\n")
    assert "1200–1800 words" in prompt
    assert "1500 words" in prompt


def test_all_arms_share_the_exact_same_bilingual_system_prompt() -> None:
    ctx = _full_ctx()
    arms = [_arm(ctx, form) for form in PromptForm]

    assert [arm[0] for arm in arms] == [arms[0][0]] * 3
    assert "2000–3000 字" in arms[0][0]["content"]
    assert graph_section(ctx, PromptForm.X1) not in arms[1][0]["content"]
    assert graph_section(ctx, PromptForm.X2) not in arms[2][0]["content"]


def test_previous_tail_is_stripped_and_limited_to_its_last_800_code_points() -> None:
    ctx = _full_ctx()
    tail = "  " + "甲" * 100 + "🙂" * 800 + "  "
    expected = ("甲" * 100 + "🙂" * 800)[-800:]

    for form in PromptForm:
        prompt = assemble(
            ctx, form=form, goal=GOAL, length=M2_LENGTH_SPEC, previous_tail=tail
        )
        assert "【上文】\n" + expected in prompt[-1]["content"]
        assert "甲" not in prompt[-1]["content"]


def test_length_instruction_uses_human_units_not_tokens() -> None:
    assert hasattr(assemble_module, "length_instruction")
    assert "token" not in assemble_module.length_instruction(M2_LENGTH_SPEC).lower()
