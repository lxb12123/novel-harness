"""`synth/build.py` 的测试 —— **用本文件自己的小号 fixture，不碰 `synth/booklet.toml`。**

两条理由，第二条更硬：

1. 真小册子是**数据**，它会被改。测试吃它 = 每改一次数据就红一次，而红的是
   「build 对不对」这件毫不相干的事。
2. **CI 里没有那份数据也不该红。** `synth/` 不进 wheel——一个 clone 下来还没跑过
   build 的仓库必须能跑通全部测试。

⚠️ `synth/` 是顶层目录，不在 `src/` 里，所以它不随 `novel_harness` 装进 .venv——
下面那两行 sys.path 是为此，`# noqa: E402` 同理。这不是坏味道，是「合成小册子是仪器
不是产品」那条决定的直接后果。

── 2026-08-24：这份文件从 25 条掉到 4 条 ────────────────────────────────

原来的 21 条考的是 M2「防泄漏」那张卷子的仪器：陷阱、tell、`ground_truth.json`、
`leak_selfcheck` 的七条放行条件。**秘密整套功能下线之后那些东西都不存在了**
（ADR 0039 / `docs/EVAL_PROTOCOL_RETIREMENT.md`）。

留下的 4 条考的是**造库**那一半，它服务的是 M3 那张卷子（R2/R3 误报门槛，
`synth/m3_replay.py` 在 build 出来的 `gate.db` 上跑）——**那张卷子零秘密**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synth.build import BuildRefused, build  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════
# fixture：一份合格的小册子
# ══════════════════════════════════════════════════════════════════════════
#
# 血枭盟 first_appears=3，正文里只在第 3 章出现——**合法的「到点了才提」**，
# 也就是 R2 的负例。M3 那张卷子考的正是这条边界。

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

[[future]]
name = "血枭盟"
label = "Faction"
first_appears = 3
"""

PROSE = """\
青云记

第一章 廊下

夜色压下来，青云城主府的灯一盏盏熄了。
李管家压低声音说，那件事怕是早就传出去了。
苏挽在窗后听见了半句，却没听清。

第二章 掌心

萧决盯着掌心那道暗纹，终于认出那是一枚旧印记。
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
    b, p = _write(tmp_path, booklet, prose)
    return build(booklet=b, prose=p, db=tmp_path / "gate.db")


# ══════════════════════════════════════════════════════════════════════════
# build：真实写入链
# ══════════════════════════════════════════════════════════════════════════


def test_build_walks_the_real_write_chain(tmp_path: Path) -> None:
    """节点和别名真的落了库，且 `chapters/` 是 `import_book` 写出来的。

    数字全部**从落库结果数**，不是从 TOML 数的——后者只能证明「解析对了」，
    而这条要证明的是「写进去了」。
    """
    result = _build(tmp_path)

    assert result.chapters == 3
    assert (result.characters, result.futures) == (3, 1)
    assert result.project_id

    # 正文真的过了 import_book（不是被谁塞进库里的）。
    assert (tmp_path / "chapters" / "0002.md").read_text(encoding="utf-8").startswith("第二章")


def test_build_refuses_a_chapter_count_that_disagrees(tmp_path: Path) -> None:
    """目录数和切出来的章数对不上就停下 —— 差一章 = 全书每条 `valid_from` 都错一章。"""
    with pytest.raises(BuildRefused, match="切章数对不上"):
        _build(tmp_path, booklet=BOOKLET.replace("chapters = 3", "chapters = 4"))


def test_build_refuses_an_existing_db(tmp_path: Path) -> None:
    """不覆盖已存在的库。

    往同一个库跑第二次不会报错，只会多出一个项目——**那不是错误，是两份看起来都对的
    真相**，而 `m3_replay` 那道「说得出考哪个」的门正是为这件事存在的。
    """
    _build(tmp_path)
    with pytest.raises(BuildRefused, match="已存在"):
        _build(tmp_path)


def test_a_typo_in_the_toml_is_refused(tmp_path: Path) -> None:
    """TOML 里一个手滑的键当场炸，不许静默忽略。

    被忽略的产物是一份「作者以为自己写了、实际什么都没写」的小册子——而它跑得通、
    出得来数字。**一个看起来正常的假结果**正是量具最不能有的东西（`extra="forbid"`）。
    """
    with pytest.raises(Exception, match="alias"):
        _build(tmp_path, booklet=BOOKLET.replace('aliases = ["萧公子"]', 'alias = ["萧公子"]'))
