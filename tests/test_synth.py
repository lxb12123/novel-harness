"""`synth/` 的测试 —— **用本文件自己的小号 fixture，不碰 `synth/booklet.toml`。**

两条理由，第二条更硬：

1. 真小册子是**数据**，它会被改（陷阱重造是协议 §6 明文规定的动作：X0 泄漏率落在
   [0.50, 0.90] 之外就重造 `prior`）。测试吃它 = 每改一次数据就红一次，而红的是
   「build 对不对」这件毫不相干的事。
2. **CI 里没有那份数据也不该红。** `synth/` 不进 wheel，`ground_truth.json` 进 `.gitignore`
   ——一个 clone 下来还没跑过 build 的仓库必须能跑通全部测试。

所以这里造一份 3 章 / 1 secret / 1 future / 2 陷阱的最小小册子，形状和真的逐字段对齐。
它测的是**机制**（真实写入链、tell 的非 canonical 声明、七条放行条件），不是那本书。

⚠️ `synth/` 是顶层目录，不在 `src/` 里，所以它不随 `novel_harness` 装进 .venv——
下面那两行 sys.path 是为此，`# noqa: E402` 同理。这不是坏味道，是「合成小册子是仪器
不是产品」那条决定的直接后果（见 `synth/build.py` 的模块 docstring 第一段）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel_harness.panel.constraints import UnresolvedCast  # noqa: E402
from synth.build import BuildRefused, GroundTruth, build, load_booklet  # noqa: E402
from synth.leak_selfcheck import selfcheck  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════
# fixture：一份合格的小册子
# ══════════════════════════════════════════════════════════════════════════
#
# 每一格都被至少一条测试盯着，别顺手改：
# - 血脉秘密 / 玄血蛊：两者互不为子串（第 4 条 echo guard 的正例）
# - 萧决 ch2 KNOWS、苏挽 从不 KNOWS  → 第 2 章的 must_not_reveal 含血脉秘密
# - 血枭盟 first_appears=3，正文里只在第 3 章出现（第 3 条的正例：合法的「到点了才提」）
# - K01.prior 含玄血蛊（第 7 条：**缺了才报错**），两条 goal 都不含任何 tell（第 6 条）

BOOKLET = """\
[book]
title = "自检用小册子"
chapters = 3

[[character]]
canonical = "萧决"
aliases = ["萧公子"]

[[character]]
canonical = "苏挽"

[[character]]
canonical = "李管家"

[[secret]]
label = "血脉秘密"
tell = "玄血蛊"

  [[secret.knows]]
  who = "萧决"
  quote = "萧决盯着掌心那道暗纹，终于认出那是玄血蛊留下的印记。"

  [[secret.believes]]
  who = "李管家"
  value = "已泄露"
  quote = "李管家压低声音说，那件事怕是早就传出去了。"

[[future]]
name = "血枭盟"
label = "Faction"
first_appears = 3

[[trap]]
id = "K01"
kind = "KNOWS"
chapter = 2
cast = ["苏挽", "萧决"]
target = "血脉秘密"
goal = "写苏挽追问萧决掌心的伤，萧决避而不答"
prior = "廊下无人时，老仆自己嘀咕了一句：那道暗纹是玄血蛊留下的。苏挽当时不在场。"
reference = "苏挽伸手要看，萧决把手缩进袖中，只说是练功时伤的。"

[[trap]]
id = "F01"
kind = "FUTURE"
chapter = 2
cast = ["苏挽", "萧决"]
target = "血枭盟"
goal = "写两人在城门口被一队来历不明的人拦下"
prior = "城门口的盘查比往日严了三倍，没人说得清那是谁的人。"
reference = "为首的那人只亮了半枚令牌，苏挽认不出那是哪一家的。"
"""

PROSE = """\
青云记

第一章 廊下

夜色压下来，青云城主府的灯一盏盏熄了。
李管家压低声音说，那件事怕是早就传出去了。
苏挽在窗后听见了半句，却没听清。

第二章 掌心

萧决盯着掌心那道暗纹，终于认出那是玄血蛊留下的印记。
他把手收进袖中，谁也没有告诉。
苏挽追上来问他伤在哪里，他只是摇头。

第三章 城门

城门外来了一队人，旗上绣着血枭盟三个字。
苏挽第一次听见这个名字。
"""


def _write(tmp_path: Path, booklet: str = BOOKLET, prose: str = PROSE) -> tuple[Path, Path]:
    b = tmp_path / "booklet.toml"
    p = tmp_path / "booklet.txt"
    b.write_text(booklet, encoding="utf-8")
    p.write_text(prose, encoding="utf-8")
    return b, p


def _build(tmp_path: Path, booklet: str = BOOKLET, prose: str = PROSE):
    """建一份完整的小册子：库 + ground_truth.json。返回 (result, booklet, prose, out)。"""
    b, p = _write(tmp_path, booklet, prose)
    out = tmp_path / "ground_truth.json"
    result = build(booklet=b, prose=p, db=tmp_path / "gate.db", out=out)
    return result, b, p, out


def _check(tmp_path: Path, booklet: str = BOOKLET, prose: str = PROSE) -> list[str]:
    _, b, p, out = _build(tmp_path, booklet, prose)
    return selfcheck(booklet=b, prose=p, ground_truth=out)


def _gt(out: Path) -> GroundTruth:
    return GroundTruth.model_validate_json(out.read_text(encoding="utf-8"))


def _recast(cast_line: str) -> str:
    """只换 K01 的 cast 行。两条陷阱的 cast 写法一样，所以要连着下一行 target 一起认。"""
    return BOOKLET.replace(
        'cast = ["苏挽", "萧决"]\ntarget = "血脉秘密"',
        f'{cast_line}\ntarget = "血脉秘密"',
    )


# ══════════════════════════════════════════════════════════════════════════
# build：真实写入链
# ══════════════════════════════════════════════════════════════════════════


def test_build_walks_the_real_write_chain(tmp_path: Path) -> None:
    """节点 / 别名 / 边 / 陷阱都落了，且 `chapters/` 是 `import_book` 写出来的。"""
    result, _, _, out = _build(tmp_path)

    assert result.chapters == 3
    assert (result.characters, result.secrets, result.futures) == (3, 1, 1)
    assert result.tell_aliases == result.secrets, "每条秘密必须恰好有一个 tell 别名"
    assert (result.knows_edges, result.believes_edges, result.traps) == (1, 1, 2)
    assert result.project_id

    # 正文真的过了 import_book（不是被谁塞进库里的）。
    assert (tmp_path / "chapters" / "0002.md").read_text(encoding="utf-8").startswith("第二章")
    assert _gt(out).project_id == result.project_id


def test_ground_truth_is_derived_not_written(tmp_path: Path) -> None:
    """禁忌集由 `scene_view()` 派生：作者在 TOML 里**没有** `forbidden` 这个字段可填。"""
    _, _, _, out = _build(tmp_path)
    traps = {t.id: t for t in _gt(out).traps}

    assert "血脉秘密" in traps["K01"].must_not_reveal
    assert traps["K01"].tells == ["玄血蛊"]
    assert traps["F01"].forbidden == ["血枭盟"]
    assert traps["F01"].tells == ["血枭盟"]
    # 陷阱行原样带出来，runner 直接吃这几个字段拼三臂。
    assert traps["K01"].cast == ["苏挽", "萧决"]
    assert "玄血蛊" in traps["K01"].prior


def test_the_detection_set_never_contains_the_prompt_label(tmp_path: Path) -> None:
    """**这条是整个 kill-gate 不会自己命中自己的机械保证。**

    进 prompt 的是显示名（`血脉秘密`），检测器找的是 tell（`玄血蛊`）。tell 声明成
    **非 canonical** 别名，而 `secret_surfaces()` 排除 canonical——两个集合不相交。
    搞反了：X1/X2 100% 命中自己写进去的词 → Δ 翻负 → 裁决表读出 KILL。
    """
    _, _, _, out = _build(tmp_path)
    k01 = next(t for t in _gt(out).traps if t.id == "K01")

    assert set(k01.tells) & set(k01.must_not_reveal) == set()
    assert "血脉秘密" not in k01.tells


def test_build_refuses_a_chapter_count_that_disagrees(tmp_path: Path) -> None:
    """协议 §4 的「顺带验一次切章数 = 目录数」。"""
    with pytest.raises(BuildRefused) as err:
        _build(tmp_path, booklet=BOOKLET.replace("chapters = 3", "chapters = 4"))
    assert "切出 3 章" in str(err.value)
    assert "写的是 4" in str(err.value)


def test_build_refuses_a_trap_whose_target_is_not_forbidden(tmp_path: Path) -> None:
    """在场的人此刻全都已经知道了 → 这条陷阱没有东西可漏，不许静默放行。"""
    booklet = _recast('cast = ["萧决"]')
    with pytest.raises(BuildRefused) as err:
        _build(tmp_path, booklet=booklet)
    assert "K01" in str(err.value)
    assert "血脉秘密" in str(err.value)


def test_build_refuses_a_degenerate_cast(tmp_path: Path) -> None:
    """**最要命的那一条。** cast 解析不出唯一角色时 `scene_view()` 是 fail-closed 的：
    `must_not_reveal` 退化成全部秘密，于是「target 在不在禁忌集里」永远为真，
    上面那条检查会全部通过，而 gate 拿到的是一份「全禁」的基线。
    """
    booklet = _recast('cast = ["查无此人"]')
    with pytest.raises(UnresolvedCast):
        _build(tmp_path, booklet=booklet)


def test_build_refuses_an_existing_db(tmp_path: Path) -> None:
    """跑第二次不会报错、只会多一个项目——那不是错误，是两份看起来都对的真相。"""
    _build(tmp_path)
    b, p = _write(tmp_path)
    with pytest.raises(BuildRefused) as err:
        build(booklet=b, prose=p, db=tmp_path / "gate.db", out=tmp_path / "again.json")
    assert "已存在" in str(err.value)


def test_a_typo_in_the_toml_is_refused(tmp_path: Path) -> None:
    """`extra="forbid"`：手滑的键被静默忽略 = 一份「作者以为自己写了」的小册子，
    而它跑得通、出得来数字。"""
    b, _ = _write(tmp_path, booklet=BOOKLET.replace("aliases = ", "alias = "))
    with pytest.raises(ValueError, match="alias"):
        load_booklet(b)


def test_duplicate_trap_ids_are_refused(tmp_path: Path) -> None:
    """陷阱 id 是统计的分析单位；重复会让 `score._validate` 在跑完 225 次生成之后才炸。"""
    b, _ = _write(tmp_path, booklet=BOOKLET.replace('id = "F01"', 'id = "K01"'))
    with pytest.raises(ValueError, match="trap.id"):
        load_booklet(b)


def test_build_refuses_an_ambiguous_tell(tmp_path: Path) -> None:
    """两处共用一个 tell → 那个 surface 挂在两个节点上 → 有歧义 → `usable_for_rules` 为假
    → `secret_surfaces()` 派生出空集 → 检测器永远找不到它，那条陷阱静默失效。
    """
    with pytest.raises(BuildRefused) as err:
        _build(tmp_path, booklet=BOOKLET.replace('tell = "玄血蛊"', 'tell = "血枭盟"'))
    assert "K01" in str(err.value)
    assert "歧义" in str(err.value)


def test_an_unknown_target_is_refused(tmp_path: Path) -> None:
    b, _ = _write(tmp_path, booklet=BOOKLET.replace('target = "血脉秘密"', 'target = "不存在的秘密"'))
    with pytest.raises(ValueError, match="不存在的秘密"):
        load_booklet(b)


# ══════════════════════════════════════════════════════════════════════════
# selfcheck：七条放行条件
# ══════════════════════════════════════════════════════════════════════════


def test_selfcheck_passes_a_well_formed_booklet(tmp_path: Path) -> None:
    """全绿才放行。这一条同时是第 3 条的**正例**：血枭盟在第 3 章（== 首现章）出现是合法的。"""
    assert _check(tmp_path) == []


def test_selfcheck_catches_nested_tells(tmp_path: Path) -> None:
    """互为子串的两个 tell 会让一次泄漏被算成两条陷阱同时泄漏，判别对数虚高。"""
    booklet = BOOKLET.replace('name = "血枭盟"', 'name = "玄血蛊毒"')
    booklet = booklet.replace('target = "血枭盟"', 'target = "玄血蛊毒"')
    problems = _check(tmp_path, booklet=booklet)
    assert any("[1" in p and "玄血蛊" in p and "玄血蛊毒" in p for p in problems), problems


def test_selfcheck_catches_the_echo_guard(tmp_path: Path) -> None:
    """显示名叫「玄血蛊之秘」而 tell 叫「玄血蛊」：prompt 印显示名，检测器在那句话上命中。"""
    booklet = BOOKLET.replace('label = "血脉秘密"', 'label = "玄血蛊之秘"')
    booklet = booklet.replace('target = "血脉秘密"', 'target = "玄血蛊之秘"')
    problems = _check(tmp_path, booklet=booklet)
    assert any("[4" in p and "玄血蛊" in p and "玄血蛊之秘" in p for p in problems), problems


def test_selfcheck_catches_a_future_tell_in_the_prose(tmp_path: Path) -> None:
    """裁定 C：未来实体的 tell 在首现章之前处处不出现，正文也算。"""
    prose = PROSE.replace("苏挽在窗后听见了半句，却没听清。", "苏挽在窗后听见了血枭盟三个字。")
    problems = _check(tmp_path, prose=prose)
    assert any("[3" in p and "第 1 章" in p and "血枭盟" in p for p in problems), problems


def test_selfcheck_catches_a_future_tell_in_the_preamble(tmp_path: Path) -> None:
    """前言按「第 0 章」算：它比任何一章都早。"""
    problems = _check(tmp_path, prose=PROSE.replace("青云记\n", "青云记（血枭盟卷）\n"))
    assert any("[3" in p and "前言" in p for p in problems), problems


def test_selfcheck_catches_a_future_tell_in_a_prior(tmp_path: Path) -> None:
    booklet = BOOKLET.replace(
        "城门口的盘查比往日严了三倍，没人说得清那是谁的人。",
        "城门口的盘查比往日严了三倍，据说是血枭盟的人。",
    )
    problems = _check(tmp_path, booklet=booklet)
    assert any("[3" in p and "F01" in p and "prior" in p for p in problems), problems


def test_selfcheck_catches_a_tell_in_a_goal(tmp_path: Path) -> None:
    """裁定 B：`goal` 含 tell 会强迫三臂都写它，Δ 被压到 0，而 confound_lint 抓不到。"""
    booklet = BOOKLET.replace(
        "写苏挽追问萧决掌心的伤，萧决避而不答",
        "写苏挽追问萧决掌心的玄血蛊印记，萧决避而不答",
    )
    problems = _check(tmp_path, booklet=booklet)
    assert any("[6" in p and "K01" in p and "玄血蛊" in p for p in problems), problems


def test_selfcheck_demands_the_tell_in_a_knows_prior(tmp_path: Path) -> None:
    """**这一条方向和别的相反：缺了才报错**（裁定 A）。

    prior 不含 tell → X0 物理上够不着那个字符串 → KNOWS 泄漏率恒 ≈ 0 → 恒判 INVALID，
    而重造多少次陷阱都不会好。
    """
    booklet = BOOKLET.replace(
        "廊下无人时，老仆自己嘀咕了一句：那道暗纹是玄血蛊留下的。苏挽当时不在场。",
        "廊下无人时，老仆自己嘀咕了一句。苏挽当时不在场。",
    )
    problems = _check(tmp_path, booklet=booklet)
    assert any("[7" in p and "K01" in p and "玄血蛊" in p for p in problems), problems
    # 只有 KNOWS 那一侧有这条要求：F01 的 prior 不含任何 tell 是**对的**。
    assert not any("[7" in p and "F01" in p for p in problems), problems


def test_selfcheck_catches_prose_edited_after_the_build(tmp_path: Path) -> None:
    """第 5 条验的是**现在**，不是 build 当时——正文加了一章，每条 chapter 都指着另一章了。"""
    _, b, p, out = _build(tmp_path)
    p.write_text(PROSE + "\n第四章 后事\n\n又过了三年。\n", encoding="utf-8")
    problems = selfcheck(booklet=b, prose=p, ground_truth=out)
    assert any("[5" in problem and "切出 4 章" in problem for problem in problems), problems


def test_selfcheck_catches_a_stale_ground_truth(tmp_path: Path) -> None:
    """ground_truth.json 是旧的 —— 它读磁盘上那三个文件，所以抓得到；
    一个从库里现算一遍的自检反而抓不到。"""
    _, b, p, out = _build(tmp_path)
    raw = json.loads(out.read_text(encoding="utf-8"))
    raw["traps"] = [t for t in raw["traps"] if t["id"] != "F01"]
    raw["traps"].append({**raw["traps"][0], "id": "K99"})
    out.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    problems = selfcheck(booklet=b, prose=p, ground_truth=out)
    assert any("[2" in problem and "F01" in problem for problem in problems), problems
    assert any("[2" in problem and "K99" in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("field", "old", "new"),
    [
        (
            "cast",
            'cast = ["苏挽", "萧决"]',
            'cast = ["萧决", "苏挽"]',
        ),
        (
            "goal",
            "写苏挽追问萧决掌心的伤，萧决避而不答",
            "写苏挽追问萧决掌心的伤，萧决转身离开",
        ),
        (
            "prior",
            "苏挽当时不在场。",
            "苏挽当时不在场，廊下也没有旁人。",
        ),
        (
            "reference",
            "萧决把手缩进袖中，只说是练功时伤的。",
            "萧决把手缩进袖中，只说是练功时伤的，随后合上了门。",
        ),
    ],
)
def test_selfcheck_catches_source_fields_changed_after_build(
    tmp_path: Path, field: str, old: str, new: str
) -> None:
    """TOML 的起草输入改过、ground truth 没重建时，绝不能拿旧输入去跑 gate。"""
    _, booklet, prose, out = _build(tmp_path)
    text = booklet.read_text(encoding="utf-8")
    booklet.write_text(text.replace(old, new, 1), encoding="utf-8")

    problems = selfcheck(booklet=booklet, prose=prose, ground_truth=out)

    assert any("[2" in problem and "K01" in problem and field in problem for problem in problems), (
        problems
    )


def test_selfcheck_catches_a_tell_that_became_the_label(tmp_path: Path) -> None:
    """派生出来的 tells 里混进了显示名 = 检测集合和 prompt 集合相交了。第 2 条当场红。"""
    _, b, p, out = _build(tmp_path)
    raw = json.loads(out.read_text(encoding="utf-8"))
    for trap in raw["traps"]:
        if trap["id"] == "K01":
            trap["tells"] = ["血脉秘密"]
    out.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    problems = selfcheck(booklet=b, prose=p, ground_truth=out)
    assert any(
        "[2" in problem and "期望：['玄血蛊']" in problem and "实际：['血脉秘密']" in problem
        for problem in problems
    ), problems


def test_selfcheck_messages_always_say_what_was_expected(tmp_path: Path) -> None:
    """一条只说「自检失败」的错误信息等于没有：每条都要能指到 TOML 的哪一行。"""
    booklet = BOOKLET.replace(
        "写苏挽追问萧决掌心的伤，萧决避而不答",
        "写苏挽追问萧决掌心的玄血蛊印记，萧决避而不答",
    ).replace(
        "廊下无人时，老仆自己嘀咕了一句：那道暗纹是玄血蛊留下的。苏挽当时不在场。",
        "廊下无人时，老仆自己嘀咕了一句。苏挽当时不在场。",
    )
    problems = _check(tmp_path, booklet=booklet)
    assert len(problems) >= 2
    for problem in problems:
        assert "K01" in problem or "F01" in problem, problem
        assert "期望" in problem, problem


# ══════════════════════════════════════════════════════════════════════════
# 生成物不入库
# ══════════════════════════════════════════════════════════════════════════


def test_the_generated_ground_truth_is_gitignored() -> None:
    """`ground_truth.json` 是 `build()` 从 TOML + 真实写入链派生的。

    手写它（或不小心 commit 一份旧的）等于把「派生对不对」这件事变成无人验证——
    而 selfcheck 的第 2 条正是在验它，它验的却会是一份和库无关的文件。
    """
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "synth/ground_truth.json" in ignored
