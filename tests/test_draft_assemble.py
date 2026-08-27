"""`draft/assemble.py` —— 三臂 prompt 渲染器（ADR 0010 的 D1–D6 就是本文件的验收标准）。

**这个文件测的不是「prompt 好不好」，是「仪器接没接反」。** 它守的四件事，每一件坏掉的样子
都不是「少个功能」，而是**整轮 kill-gate 读出一个假裁决**：

1. **三臂共用一个 base（D4）。** 某一臂多一句话 → 臂间差异里混进那句话的效应 →
   ADR 0009 把它当成「注入有用」或「form 重要」写进去。
2. **X1 与 X2 只差「清单 vs 散文」（协议 §2 反混淆铁律）。** 两臂专名不同或字数差太多 →
   `confound_lint` 报警 → FORM-PIVOT 分支本轮作废（协议 §6 第 3 行），白跑 75 次生成。
3. **节点属性和别名永不进 prompt（D3）。** 作者写在节点上的 `twist` / `plot_note` 是剧透，
   非 canonical 别名装着他的意图 —— 进 prompt 的只许是显示名。
4. **cast 三臂都有（D6）。** 从 X0 拿掉 → X0 写到别人身上 → 那不是「去掉注入」是「换了个任务」。

复用 `test_fake_graph.FakeGraph`（同 `test_draft_context.py`）：它是 StoryGraph 契约的参考实现。

── 本文件**测不到**什么（诚实交代，同仓库其它守卫的自述）──────────────────

- **`goal` / `previous_tail` 里的剧透。** 判它要回答「这句话是不是把伏笔说破了」= 语义判断，
  ADR 0005 禁止。守它的是 ADR 0010 D3 末尾那条 review 判据。**这里的绿不代表 prompt 没剧透。**
- **第 2 条里的字数比与专名集合只在本文件这几个 fixture 上验过。** 这里的自检是
  「改模板时立刻红」，不是「所有输入都合格」。（原来还有一道 `eval/confound_lint.py`
  跑在每一轮真实渲染上，它随 M2 一起退役了，ADR 0039。）
"""

from __future__ import annotations

import inspect

import pytest
import novel_harness.draft.assemble as assemble_module
from test_fake_graph import (
    QINGYUN,
    GU_QINGYIN,
    LI_GUANJIA,
    PID,
    XIAO_JUE,
    FakeGraph,
    edge,
    node,
)

from novel_harness.draft.assemble import (
    DEFAULT_WRITING_PROMPT,
    EN_WRITING_PROMPT,
    WRITE_RULE_FORBIDDEN_HINTS,
    ZH_WRITING_PROMPT,
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
"""一条**非 canonical 别名**。别名装着作者的意图（ADR 0004），进 prompt 的只许是显示名。"""

TWIST = "萧决其实是魔尊之子，第 200 章揭晓"
"""挂在节点 `props` 上的额外字段。`NodeProps` 是 `extra="allow"`，它真的存在。"""

XUEXIAO = node("faction:demo:01JA", NodeLabel.FACTION, "血枭盟", first_appears_chapter=8)
YOUQUAN = node("location:demo:01JB", NodeLabel.LOCATION, "幽泉窟", first_appears_chapter=10)


def _full_store(*, with_tell: bool = False) -> FakeGraph:
    """两个未来实体（血枭盟 ch8 / 幽泉窟 ch10），外加一个挂着 `twist` 的已登场地点。"""
    poisoned = node(QINGYUN.id, NodeLabel.LOCATION, "青云城主府", twist=TWIST)
    return FakeGraph(
        [XIAO_JUE, GU_QINGYIN, LI_GUANJIA, poisoned, XUEXIAO, YOUQUAN],
        [edge(XIAO_JUE.id, poisoned.id, EdgeType.LOCATED_AT, 3)],
        extra_aliases={TELL: [poisoned]} if with_tell else None,
    )


def _full_ctx(*, with_tell: bool = False) -> ResolvedConstraints:
    return resolve_constraints(
        _full_store(with_tell=with_tell),
        PID,
        5,
        [XIAO_JUE.name, LI_GUANJIA.name, GU_QINGYIN.name],
    )


def _rendered(ctx: ResolvedConstraints) -> list[dict[str, str]]:
    """渲染一次，固定 goal / previous_tail。（2026-08-25 之前它叫 `_arm` 并收一个 `form`。）"""
    return assemble(ctx, goal=GOAL, length=M2_LENGTH_SPEC, previous_tail=TAIL)


def _text(messages: list[dict[str, str]]) -> str:
    return "\n".join(m["content"] for m in messages)


# ══════════════════════════════════════════════════════════════════════════
# ① D4：用户消息的基座 —— cast / 上文（图谱段 2026-08-26 起恒空，见下方③）
# ══════════════════════════════════════════════════════════════════════════


def test_the_cast_is_in_the_prompt() -> None:
    """ADR 0010 D6：`cast` 是调用方算好的**输入**，不是本模块的图谱查询结果。

    不给模型这份名单，它就会写到别人身上去 —— 那不是「少注入一点」，是另一个任务。
    """
    ctx = _full_ctx()
    text = _text(_rendered(ctx))
    for who in ctx.cast:
        assert who in text, f"少了在场角色 {who}"


def test_the_previous_tail_is_optional_and_leaves_no_empty_heading() -> None:
    """开篇（`previous_tail=""`）不该在 prompt 里留一个空的「上文」标题。"""
    ctx = _full_ctx()
    with_tail = _rendered(ctx)
    without = assemble(ctx, goal=GOAL, length=M2_LENGTH_SPEC)

    assert TAIL in with_tail[-1]["content"]
    assert "【上文】" in with_tail[-1]["content"]
    assert "【上文】" not in without[-1]["content"]
    assert without[-1]["content"].startswith("【在场】")
# ══════════════════════════════════════════════════════════════════════════
# ③ D3：tell 永不进 prompt / 图谱段 2026-08-26 起恒空
# ══════════════════════════════════════════════════════════════════════════


def test_graph_section_has_no_block_source_and_never_appears_in_the_prompt() -> None:
    """2026-08-26：唯一的图谱块（尚未登场）删了。**`graph_section()` 恒为 `""`**——
    连本该有未来实体的 fixture 也一样，「【本场设定要点】」不会再出现在任何 prompt 里。

    这条故意用 `_full_ctx()`（血枭盟 ch8 / 幽泉窟 ch10 都在）而不是空 ctx：
    用一个「以前会产出图谱段」的输入去证明它现在真的不产出，比用一个本来就没有
    未来实体的输入更能防「有人把 `_forbidden_block` 悄悄加回来了」这种回归。
    """
    ctx = _full_ctx()
    assert ctx.forbidden_names, "这个 fixture 本该有未来实体 —— 没有的话下面全是空转"

    assert graph_section(ctx) == ""
    assert "【本场设定要点】" not in _text(_rendered(ctx))


def test_no_alias_and_no_props_reach_the_prompt() -> None:
    """**节点属性里作者写的东西一个字都不许进 prompt。**

    `NodeProps` 是 `extra="allow"` 的：作者写在节点上的 `plot_note`（「第 200 章
    才揭晓」那类）会原样穿过任何一次 `model_dump_json()`。

    （这条原来还罩着秘密的 tell —— 那半随秘密下线一起走了，ADR 0039。留下的这半
    跟秘密无关：任何一类节点的 props 都装得下作者写的剧透。）
    """
    ctx = _full_ctx(with_tell=True)
    text = _text(_rendered(ctx))
    assert TWIST not in text, "prompt 里出现了 props 里的字"
    assert TELL not in text, "prompt 里出现了非 canonical 别名"


def test_the_tell_really_is_reachable_in_the_graph() -> None:
    """上一条的非空证明：别名和 twist **确实在图里**，是被 `ResolvedConstraints` 收窄挡掉的。

    没有这一条，只要哪天 fixture 少写了一个别名，上面那条会永远绿着通过。
    """
    store = _full_store(with_tell=True)

    hit = store.resolve(PID, [TELL])[0].unique_node
    assert hit is not None and hit.id == QINGYUN.id
    assert TWIST in hit.model_dump_json()


def test_the_default_writing_prompt_names_nobody_and_hints_at_no_constraint() -> None:
    """共用系统提示的两条硬约束（见 `DEFAULT_WRITING_PROMPT` 的 docstring）。

    第二条是关键：默认写作提示里若写了「不要写出角色还不知道的事」这类**通用版**约束，
    等于把处理组的东西发一份给对照组 —— X0 的泄漏率被自己压低，`Δ` 塌掉，实验读出
    「注入没用」，而真实原因是 base 里替它做了一半。

    下面是一张**关键词网**，不是语义检查：它抓得住顺手写出来的那一种，抓不住换个说法的那一种。
    """
    for name in ("萧决", "顾清音", "李管家", "青云城主府", "血枭盟", "幽泉窟", TELL):
        assert name not in DEFAULT_WRITING_PROMPT
    for hint in WRITE_RULE_FORBIDDEN_HINTS:
        assert hint not in DEFAULT_WRITING_PROMPT, f"默认写作提示里出现了 {hint!r} —— 它三臂共用"
    assert "600–1000" not in DEFAULT_WRITING_PROMPT


def test_default_writing_prompts_do_not_constrain_control_arm_content() -> None:
    assert "凭空" not in DEFAULT_WRITING_PROMPT
    assert "凭空" not in ZH_WRITING_PROMPT
    assert "invent" not in EN_WRITING_PROMPT.lower()
    assert "absent" not in EN_WRITING_PROMPT.lower()


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 入参：坏配置在这里红，不要留到跑了一半
# ══════════════════════════════════════════════════════════════════════════


def test_the_renderer_takes_no_form_argument_any_more() -> None:
    """**三臂删干净了**：多传一个 `form=` 当场 TypeError，不是被静默吃掉。

    没有这一条，某个还记着老签名的调用方会传一个谁都不读的 `form=`，
    而它在运行时长得跟正常调用一模一样。
    """
    ctx = _full_ctx()
    with pytest.raises(TypeError):
        assemble(ctx, form="X1", goal=GOAL, length=M2_LENGTH_SPEC)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        graph_section(ctx, "X1")  # type: ignore[call-arg]


def test_a_blank_goal_is_rejected_at_render_time() -> None:
    """空 goal 让模型自己编一场戏，而事后从账上看不出来这一稿为什么跑偏。"""
    ctx = _full_ctx()
    with pytest.raises(ValueError, match="goal"):
        assemble(ctx, goal="   ", length=M2_LENGTH_SPEC)


def test_assemble_needs_no_store_at_all() -> None:
    """ADR 0010 D2：签名里没有 store、没有 project_id。**拿不到 store 就查不了第二遍。**

    只要签名里有 store，某天就会有人为了「让模型知道得更全」在这里补一次查询——
    那一刻起 prompt 里的事实就不再是调用方算好的那一份，而**没有任何东西会红**。

    这条同时钉住「参数集合就这几个」：`form` 2026-08-25 从这张单子上删了（三臂下线），
    加回来一个谁都不读的参数是这个模块最容易长出来的那种赘生物。
    """
    params = set(inspect.signature(assemble).parameters)
    assert params == {
        "ctx",
        "goal",
        "length",
        "previous_tail",
        "previous_tail_limit",
        "write_rule",
    }
    assert inspect.signature(assemble).parameters["length"].default is inspect.Parameter.empty


def test_length_instruction_is_explicit_in_chinese_system_prompt() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(M2_LENGTH_SPEC)

    assert "中文" in prompt
    assert "2000–3100 字" in prompt
    assert "2500 字" in prompt
    assert "2000–2250 字之间自然收束" in prompt
    assert "绝不要超过 3100 字" in prompt
    assert "作废" in prompt
    assert "600–1000" not in prompt


def test_length_instruction_is_explicit_in_english_system_prompt() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(EN_LENGTH)

    assert "English" in prompt
    assert "1200–1800 words" in prompt
    assert "1500 words" in prompt
    assert "1200–1350 words" in prompt
    assert "over-length draft is discarded" in prompt


def test_custom_write_rule_cannot_bypass_the_length_instruction() -> None:
    assert hasattr(assemble_module, "system_prompt")
    prompt = assemble_module.system_prompt(EN_LENGTH, write_rule="  Keep the prose spare.  ")

    assert prompt.startswith("Keep the prose spare.\n\n")
    assert "1200–1800 words" in prompt
    assert "1500 words" in prompt


def test_the_system_prompt_carries_the_length_and_no_graph_facts() -> None:
    """系统消息里有长度档，**没有**图谱段——图谱事实只许出现在用户消息尾部。

    不能写成 `graph_section(ctx) not in system`：`graph_section()` 2026-08-26 起恒为
    `""`，而空串是任何字符串的子串，那条断言会恒假。改成直接找那句抬头文本。
    """
    ctx = _full_ctx()
    system = _rendered(ctx)[0]["content"]

    assert "2000–3100 字" in system
    assert "【本场设定要点】" not in system


def test_previous_tail_is_stripped_and_limited_to_its_last_800_code_points() -> None:
    ctx = _full_ctx()
    tail = "  " + "甲" * 100 + "🙂" * 800 + "  "
    expected = ("甲" * 100 + "🙂" * 800)[-800:]

    prompt = assemble(ctx, goal=GOAL, length=M2_LENGTH_SPEC, previous_tail=tail)
    assert "【上文】\n" + expected in prompt[-1]["content"]
    assert "甲" not in prompt[-1]["content"]


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 逐字上文的长度：默认值是地板，产品档按窗口放长（ADR 0019 边界五）
# ══════════════════════════════════════════════════════════════════════════


def test_the_default_tail_limit_is_the_old_floor() -> None:
    """**默认值 = 800 个输入 code point**（ARCHITECTURE §9）。

    这个数当年是 X0 对照臂的定义（「证明给得少会崩」的那一档）。三臂删了之后它是
    **谁都不传时的地板**——产品那条路显式传 `product_tail_limit(...)` 覆盖它。
    这条红了意味着有人动了那个地板，而**不传参数的调用方会安静地跟着变**。
    """
    assert assemble_module.GATE_TAIL_CODE_POINTS == 800
    assert (
        inspect.signature(assemble).parameters["previous_tail_limit"].default
        == assemble_module.GATE_TAIL_CODE_POINTS
    )
def test_a_longer_limit_keeps_more_of_the_tail_verbatim() -> None:
    """产品档要的就是这条：同一段上文，限额大 ⇒ 逐字进 prompt 的更多。"""
    ctx = _full_ctx()
    tail = "甲" * 5_000

    long_prompt = assemble(
        ctx,
        goal=GOAL,
        length=M2_LENGTH_SPEC,
        previous_tail=tail,
        previous_tail_limit=4_000,
    )
    assert "【上文】\n" + "甲" * 4_000 in long_prompt[-1]["content"]
    assert "甲" * 4_001 not in long_prompt[-1]["content"]


def test_a_non_positive_limit_drops_the_tail_instead_of_keeping_all_of_it() -> None:
    """`"abc"[-0:] == "abc"`。这条钉的就是那个陷阱：0 必须是「不给」，不是「全给」。"""
    ctx = _full_ctx()
    prompt = assemble(
        ctx,
        goal=GOAL,
        length=M2_LENGTH_SPEC,
        previous_tail=TAIL,
        previous_tail_limit=0,
    )
    assert "【上文】" not in prompt[-1]["content"]


def test_product_tail_limit_scales_with_the_real_context_window() -> None:
    """比例不是绝对量：窗口大一个量级，上文就长一截（到成本闸为止）。"""
    from novel_harness.draft.assemble import (
        TAIL_UNITS_CEILING,
        GATE_TAIL_CODE_POINTS,
        product_tail_limit,
    )

    small = product_tail_limit(32_000, 8_000)
    large = product_tail_limit(200_000, 8_000)

    assert GATE_TAIL_CODE_POINTS < small < large <= TAIL_UNITS_CEILING
    # 成本闸：1M 窗口不等于每次起草都塞 1M。
    assert product_tail_limit(1_000_000, 8_000) == TAIL_UNITS_CEILING
    assert product_tail_limit(10_000_000, 8_000) == TAIL_UNITS_CEILING


def test_product_tail_limit_never_goes_below_the_frozen_default() -> None:
    """**只许变长，不许变短。** 变短了没人会发现，只会觉得模型忽然变笨了。"""
    from novel_harness.draft.assemble import GATE_TAIL_CODE_POINTS, product_tail_limit

    # 能力表没登记这个模型 → 不猜一个大窗口（同 `memory_units_available`）。
    assert product_tail_limit(None, 8_000) == GATE_TAIL_CODE_POINTS
    # 输出预留比整个窗口还大 → 余量为 0，仍然保持今天的行为。
    assert product_tail_limit(4_000, 8_000) == GATE_TAIL_CODE_POINTS


def test_the_tail_budget_reuses_the_one_token_conversion() -> None:
    """换算只许有一处（`product_context.TOKENS_PER_UNIT`），别在这儿另定一个。"""
    from novel_harness.draft.assemble import (
        TAIL_CONTEXT_SHARE,
        TAIL_UNITS_CEILING,
        product_tail_limit,
    )
    from novel_harness.draft.product_context import TOKENS_PER_UNIT

    headroom = 120_000 - 8_000
    expected = int(headroom / TOKENS_PER_UNIT * TAIL_CONTEXT_SHARE)
    assert product_tail_limit(120_000, 8_000) == min(expected, TAIL_UNITS_CEILING)


def test_length_instruction_uses_human_units_not_tokens() -> None:
    assert hasattr(assemble_module, "length_instruction")
    assert "token" not in assemble_module.length_instruction(M2_LENGTH_SPEC).lower()


def test_a_chapter_that_fits_is_never_cut() -> None:
    """**常态下起作用的是「这一章有多长」，不是那个上限常量。**

    上一版 `TAIL_UNITS_CEILING` 标定成 12,000，于是 1M 窗口的模型 + 13,000 字的一章
    会被砍掉 1,000 字——而且**砍在句子中间**。上文这一层的全部意义是接住语气和情绪，
    切在半句上正好毁掉它要的那个东西；而那一章占 1M 窗口不到 3%，砍它省不下什么。

    这条钉住「装得下就整篇给，一个字不切」。
    """
    from novel_harness.draft.assemble import product_tail_limit

    chapter = "字" * 13_000
    limit = product_tail_limit(1_000_000, 150_000)

    assert limit >= len(chapter), "1M 窗口下一章 13,000 字必须装得下"
    assert chapter[-limit:] == chapter, "装得下就不许切"


def test_the_ceiling_only_catches_a_broken_upstream() -> None:
    """闸门该拦的是**病态输入**，不是一章正常的长文。

    `previous_tail` 是调用方给什么就是什么。切章一旦出错（整本书成了一「章」），
    没有这道闸就是把整本书按 token 计费发出去一次。
    所以上限要高到「正常的一章永远撞不到」，低到「一整本书一定撞到」。
    """
    from novel_harness.draft.assemble import TAIL_UNITS_CEILING

    longest_real_chapter = 20_000  # 网文里已经算很长的一章
    a_whole_book = 500_000

    assert longest_real_chapter < TAIL_UNITS_CEILING < a_whole_book
