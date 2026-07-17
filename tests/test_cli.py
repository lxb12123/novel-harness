"""`nh` 的命令面（PLAN §8 Day 5 下午 / §8「本周 done_when」）。

本周的验收原文是**一条命令**：

    uv run nh panel --chapter 151 --cast 萧决,顾清音,李管家

所以这个文件的头等大事不是覆盖率，是 `test_the_weeks_done_when` 那一条：它拿一个真库
（不是 Fake）跑真 CLI，断言 §3.2 / README:20-31 那个框里的每一格。**面板是这个项目的
头牌，而 CLI 是它今天唯一的用户界面**——前端还是空壳。

── 这里为什么有一半的测试在断言「报错」──────────────────────────────────

ARCHITECTURE §10 约束 8 和 `checks/__init__.py:29-32` 说的是同一件事：**静默的零和
真的零不许长得一样**。而 CLI 是这条约束最容易破的地方——空库、打错的 project_id、
没声明过秘密的项目、解析不出来的称呼，每一个的自然产物都是「一张漂亮的空表 + exit 0」。
所以下面每条 `exit_code != 0` 的断言都在钉一个具体的等号，它们不是防御性测试。

── ⚠️ demo_novel.txt 是心跳 fixture，不是真书 ────────────────────────────

`tests/fixtures/demo_novel.txt` 由本仓库手写虚构（3 章，无版权）。它证明的是「切章器
在手写的脏数据上不切歪」，**不是** PLAN §8 Day 3 那条「找一本真实的 300 章网文 TXT，
切出章数与目录数一致」——**那条验收至今 BLOCKED，没有人提供过那本 TXT**。
别把这里的 3 == 3 当成真书覆盖率。（同 `text/chapterize.py` 模块 docstring 的警告。）
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from novel_harness import db
from novel_harness.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "demo_novel.txt"

PID = "project:cli00000:01J0CLI00000000000000000"

XIAO = "character:cli00000:01J0XIAO0000000000000000"
GU = "character:cli00000:01J0GU000000000000000000"
LI = "character:cli00000:01J0LI000000000000000000"
BLOOD = "secret:cli00000:01J0BLOOD000000000000000"
TOKEN = "secret:cli00000:01J0TOKEN000000000000000"
MANOR = "location:cli00000:01J0MANOR000000000000000"
BEIHUANG = "location:cli00000:01J0BEIHUANG000000000000"

runner = CliRunner()


# ══════════════════════════════════════════════════════════════════════════
# 一个真库
# ══════════════════════════════════════════════════════════════════════════


def _seed(conn: sqlite3.Connection) -> None:
    """§3.2 / README:20-31 那张框图的世界，**逐格照抄**。

    直接写 SQL 是**测试的特权**：`tests/test_arch_guard.py` 只扫 `src/`，而生产侧
    根本没有建节点的路径（`StoryGraph` 的五个方法一个都不建节点）——那正是
    `nh import` 落不了库的原因，见 `test_import_refuses_to_pretend_it_persisted`。
    """
    conn.execute(
        "INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (PID, "青云记", ".")
    )
    nodes = [
        (XIAO, "Character", "萧决"),
        (GU, "Character", "顾清音"),
        (LI, "Character", "李管家"),
        (BLOOD, "Secret", "血脉秘密"),
        (TOKEN, "Secret", "玄铁令下落"),
        (MANOR, "Location", "青云城主府"),
        (BEIHUANG, "Location", "北荒"),
    ]
    for node_id, label, name in nodes:
        conn.execute(
            "INSERT INTO node (id, project_id, label, name) VALUES (?,?,?,?)",
            (node_id, PID, label, name),
        )
        conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,?)",
            (f"alias:{node_id}", PID, node_id, name, "canonical"),
        )
    for secret_id in (BLOOD, TOKEN):
        conn.execute("INSERT INTO secret (id, project_id) VALUES (?,?)", (secret_id, PID))

    # 「师兄」→ 2 个人。§3.1 点名的那个场景（一章里 8 个角色都叫「师兄」）的最小形态：
    # 歧义是**跨行**事实，所以两行各自 usable_for_rules=1，歧义只能在查询时算出来。
    for node_id in (XIAO, LI):
        conn.execute(
            "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,?)",
            (f"alias:师兄:{node_id}", PID, node_id, "师兄", "title"),
        )

    edges = [
        ("e1", XIAO, BLOOD, "KNOWS", 88, "{}"),
        ("e2", XIAO, TOKEN, "KNOWS", 120, "{}"),
        ("e3", LI, BLOOD, "BELIEVES", 103, '{"believed_value":"已泄露"}'),
        ("e4", XIAO, BEIHUANG, "LOCATED_AT", 150, "{}"),
    ]
    for edge_id, src, dst, edge_type, valid_from, props in edges:
        conn.execute(
            "INSERT INTO edge (id, project_id, src, dst, type, props_json,"
            " valid_from_chapter, information_scope) VALUES (?,?,?,?,?,?,?,'CANON')",
            (edge_id, PID, src, dst, edge_type, props, valid_from),
        )
    conn.commit()


@pytest.fixture
def book(tmp_path: Path) -> Iterator[Path]:
    """一个建好、迁移过、填好数据的库文件的路径。"""
    path = tmp_path / "book.db"
    conn = db.connect(path)
    db.migrate(conn)
    _seed(conn)
    conn.close()
    yield path


def _panel(book: Path, *args: str):
    return runner.invoke(app, ["panel", "--db", str(book), "-p", PID, *args])


# ══════════════════════════════════════════════════════════════════════════
# 本周的 done_when
# ══════════════════════════════════════════════════════════════════════════


def test_the_weeks_done_when(book: Path) -> None:
    """**PLAN §8「本周 done_when」逐字就是这条命令。** 每一格都对着 README:20-31。

    第 152 章：萧决 ch88 起知道血脉秘密、ch120 起知道玄铁令下落；顾清音两条都不知道；
    李管家 ch103 起对血脉秘密持错误认知（以为已泄露）、对玄铁令下落不知道。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,顾清音,李管家")

    assert result.exit_code == 0, result.output
    out = result.output

    assert "认知边界 · 第 152 章" in out
    for name in ("萧决", "顾清音", "李管家", "血脉秘密", "玄铁令下落"):
        assert name in out

    # 行序 = 作者写的 cast 顺序（`ResolvedCast.ids` 的契约），不是 id 序、不是名字序。
    assert out.index("萧决") < out.index("顾清音") < out.index("李管家")

    assert "✓ 知道 (ch88)" in out
    assert "✓ 知道 (ch120)" in out
    assert "✗ 不知道" in out
    assert "⚠ 错误认知 (ch103)" in out
    # believed_value 是这一行存在的全部意义——只画「⚠ 错误认知」等于没说他以为的是什么。
    assert "以为「已泄露」" in out


def test_must_not_reveal_is_rendered_under_the_matrix(book: Path) -> None:
    """README 的框里矩阵下面就印着这一行。

    在场三人里顾清音和李管家都不是 KNOWS（BELIEVES 也算——把真相说破同样崩人设），
    所以两条秘密都得瞒。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,顾清音,李管家")

    assert result.exit_code == 0, result.output
    assert "本场景 must_not_reveal：" in result.output
    assert "血脉秘密" in result.output
    assert "玄铁令下落" in result.output


def test_a_secret_everyone_knows_leaves_must_not_reveal(book: Path) -> None:
    """cast 只剩萧决时，他两条都 KNOWS → 没有东西要瞒。

    **这一条是上一条的反面，缺了它上一条就是废的**：一个永远打印全部秘密的
    `must_not_reveal`（fail-closed 的退化值长得一模一样）也能让上一条绿。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决")

    assert result.exit_code == 0, result.output
    assert "本场景 must_not_reveal：（无）" in result.output


def test_the_matrix_is_as_of_the_queried_chapter(book: Path) -> None:
    """`--chapter` 是「AS OF 第几章」。ch87 → 萧决还不知道血脉秘密（闭开区间的下界）。

    §8 Day 5 点名的单测就是这个：ch87 UNKNOWN / ch88 KNOWS / ch152 KNOWS。
    这里在 **CLI 这一层**再钉一次——中间隔着 `--chapter` 的字符串转 int 和一整条装配链。
    """
    assert "✓ 知道 (ch88)" not in _panel(book, "--chapter", "87", "--cast", "萧决").output
    assert "✓ 知道 (ch88)" in _panel(book, "--chapter", "88", "--cast", "萧决").output


# ══════════════════════════════════════════════════════════════════════════
# 歧义：必须画出来，不许静默丢
# ══════════════════════════════════════════════════════════════════════════


def test_an_ambiguous_appellation_is_rendered_not_dropped(book: Path) -> None:
    """「师兄」→ 2 个人。**它必须出现在输出里**（§10.5 第 1 条 / `constraints.py:91`）。

    静默丢掉它的后果不是「少一行」，是作者声明了 3 个人、看见 2 行、以为系统对第 3 个人
    没意见。这条测试钉的是 `knowledge_matrix(..., unresolved=...)` 那个参数真的被
    CLI 传下去了**并且真的被画了出来**——传下去却不画是同一个 bug 挪了 20 行。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,师兄")

    assert result.exit_code == 0, result.output
    assert "师兄" in result.output
    assert "解析不出唯一角色" in result.output
    # 解析成功的那一行照画——歧义不该让头牌整片黑掉（`scene_constraints` 的 Notes）。
    assert "✓ 知道 (ch88)" in result.output


def test_an_ambiguous_cast_degrades_must_not_reveal_to_everything(book: Path) -> None:
    """fail-closed：cast 没数全就没资格说哪条秘密是安全的。

    萧决一个人时 must_not_reveal 是空的（上面那条测过）；加上一个解析不出来的「师兄」
    之后，它必须**退化成全部秘密**——而不是继续报「（无）」。
    **少禁一条的代价是崩人设，多禁一条的代价是 Writer 少写一段。**
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,师兄")

    assert result.exit_code == 0, result.output
    assert "本场景 must_not_reveal：（无）" not in result.output
    assert "血脉秘密" in result.output
    assert "玄铁令下落" in result.output


def test_cast_is_never_resolved_by_the_cli_itself(book: Path) -> None:
    """全部称呼都解析不出来时**必须死**，不许画一张零行的表。

    零行的表和「在场的人都没问题」在终端上一模一样。
    """
    result = _panel(book, "--chapter", "152", "--cast", "张三,李四")

    assert result.exit_code != 0
    assert "一个都没解析出唯一角色" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 静默的零 != 真的零
# ══════════════════════════════════════════════════════════════════════════


def test_a_missing_db_dies_instead_of_creating_an_empty_one(tmp_path: Path) -> None:
    """**`connect()` 会把库建出来**，所以打错一个字母的自然产物是一张「谁都不知道」的
    矩阵——闭世界推导下那是个断言。必须先拦。"""
    missing = tmp_path / "typo.db"
    result = runner.invoke(
        app, ["panel", "--db", str(missing), "-p", PID, "--chapter", "1", "--cast", "萧决"]
    )

    assert result.exit_code != 0
    assert "库不存在" in result.output
    assert not missing.exists(), "拦下来了却把库建出来了，那下一次运行就会静默通过"


def test_a_wrong_project_id_dies(book: Path) -> None:
    """项目号打错 → 花名册为空 → 零行矩阵。它长得像「没问题」，其实是「没数据」。"""
    result = runner.invoke(
        app,
        ["panel", "--db", str(book), "-p", "project:nope:x", "--chapter", "1", "--cast", "萧决"],
    )

    assert result.exit_code != 0
    assert "没有任何花名册行" in result.output


def test_an_empty_cast_dies(book: Path) -> None:
    """空 cast 的矩阵是零行。且 `ResolvedCast.complete` 对空 cast 返回 False——
    「一个人都没有」和「全员都知道」会产出同样的零约束。"""
    result = _panel(book, "--chapter", "152")

    assert result.exit_code != 0
    assert "--cast 是空的" in result.output


def test_a_project_with_no_secrets_dies(tmp_path: Path) -> None:
    """**没有秘密可断言 != 他们什么都不知道。**

    这是本文件里最细的一条等号：矩阵会是 1 行 × 0 列，`_check_complete` 认为它完全
    合法（0 == 1 * 0），面板会画出一个只有人名的漂亮表格，exit 0。而作者会读成
    「系统说这三个人对秘密没有认知问题」。
    """
    path = tmp_path / "nosecrets.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("INSERT INTO project (id, name, root_path) VALUES (?,?,?)", (PID, "空书", "."))
    conn.execute(
        "INSERT INTO node (id, project_id, label, name) VALUES (?,?,?,?)",
        (XIAO, PID, "Character", "萧决"),
    )
    conn.execute(
        "INSERT INTO alias (id, project_id, node_id, surface, kind) VALUES (?,?,?,?,?)",
        (f"alias:{XIAO}", PID, XIAO, "萧决", "canonical"),
    )
    conn.commit()
    conn.close()

    result = _panel(path, "--chapter", "152", "--cast", "萧决")

    assert result.exit_code != 0
    assert "一条秘密都没声明" in result.output


def test_chapter_zero_dies(book: Path) -> None:
    """第 0 章不存在。静默返回全 UNKNOWN 会把一个 off-by-one 变成面板上的正常答案。

    store 会抛 `ValueError`；CLI 的活是把它变成一条人话 + 一个非 0 退出码，
    而不是一个 traceback。
    """
    result = _panel(book, "--chapter", "0", "--cast", "萧决")

    assert result.exit_code != 0
    assert "章号从 1 起" in result.output
    assert "Traceback" not in result.output


# ══════════════════════════════════════════════════════════════════════════
# nh check
# ══════════════════════════════════════════════════════════════════════════


def _chapter_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ch151.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_check_reports_a_location_conflict(book: Path, tmp_path: Path) -> None:
    """R4：场景声明在青云城主府，而萧决自第 150 章起在北荒。**作者声明 vs 作者声明。**

    这是 `checks/` 第一次被一个真实的、非测试的调用方喂到——在此之前 R4 是一条正确的
    纯函数，`Scene` 全仓库只在测试里被构造过。
    """
    manuscript = _chapter_file(
        tmp_path,
        "## 场景 1\n<!-- nh: cast=萧决 loc=青云城主府 goal=试探身世 -->\n\n正文正文。\n",
    )
    result = runner.invoke(
        app,
        ["check", "--db", str(book), "-p", PID, "--chapter", "151", "-f", str(manuscript)],
    )

    assert result.exit_code != 0, result.output
    assert "[R4] LOCATION_CONFLICT" in result.output
    assert "北荒" in result.output
    assert "青云城主府" in result.output
    # 建议由规则确定性产出（PLAN 改 13），不是 LLM 编的。
    assert "建议：" in result.output


def test_check_prints_the_anchor_triple_and_never_an_offset(book: Path, tmp_path: Path) -> None:
    """锚是 `(para_index, quote_text, occurrence_k)`（ADR 0006）。**后端永不对外发 offset。**

    指令行在第 1 行（0-based），`quote_text` 必须是那一行的原文。
    """
    manuscript = _chapter_file(
        tmp_path, "## 场景 1\n<!-- nh: cast=萧决 loc=青云城主府 -->\n"
    )
    result = runner.invoke(
        app,
        ["check", "--db", str(book), "-p", PID, "--chapter", "151", "-f", str(manuscript)],
    )

    assert "第 1 段" in result.output
    assert "第 0 次" in result.output
    assert "<!-- nh: cast=萧决 loc=青云城主府 -->" in result.output


def test_check_says_how_many_rules_it_ran(book: Path, tmp_path: Path) -> None:
    """**零 issue 必须带着「跑了几条规则」一起出现**（§10 约束 8 / `checks/__init__.py:29`）。

    v1 的 `ALL_CHECKS` 只有 R4，所以「无 issue」的真实含义是「R4 没意见」，不是
    「这一章没问题」——沉默的工具死得比吵闹的工具更快，只是死得更安静。
    """
    manuscript = _chapter_file(tmp_path, "## 场景 1\n<!-- nh: cast=萧决 loc=北荒 -->\n")
    result = runner.invoke(
        app,
        ["check", "--db", str(book), "-p", PID, "--chapter", "151", "-f", str(manuscript)],
    )

    assert result.exit_code == 0, result.output
    assert "跑了 1 条规则" in result.output
    assert "location_conflict" in result.output
    assert "0 条 issue" in result.output


def test_check_with_no_scene_blocks_dies(book: Path, tmp_path: Path) -> None:
    """没有场景块 = R4 无事可做 = 必然零 issue。不让那个零冒充体检报告。"""
    manuscript = _chapter_file(tmp_path, "萧决站在城头上，看着北荒的方向。\n")
    result = runner.invoke(
        app,
        ["check", "--db", str(book), "-p", PID, "--chapter", "151", "-f", str(manuscript)],
    )

    assert result.exit_code != 0
    assert "一个场景块都没有" in result.output


# ══════════════════════════════════════════════════════════════════════════
# nh import
# ══════════════════════════════════════════════════════════════════════════


def test_import_chapterizes_the_heartbeat_fixture() -> None:
    """3 章。⚠️ **这不是 PLAN §8 Day 3 那条「真书章数 = 目录数」的验收**——
    见本模块 docstring：那条至今 BLOCKED，因为没有人提供过那本 300 章的 TXT。

    这里真正在钉的是脏数据的处置：卷标题 / 作者的话 / 番外**一个都不许被切成章**
    （多切一章 = 全书 index 集体偏移 = `state_at` 从此答隔壁那一章）。
    """
    result = runner.invoke(app, ["import", str(FIXTURE)])

    assert "切出 3 章" in result.output
    assert "第一章  少年萧决" in result.output
    assert "第二章  玄铁令" in result.output
    assert "第三章  （无标题）" in result.output
    # 「第一卷 风起青云」「作者的话：」「番外：」都不是章界，它们落在 body / preamble 里。
    assert "第一卷" not in result.output
    assert "番外" not in result.output


def test_import_refuses_to_pretend_it_persisted() -> None:
    """**落不了库就必须非 0 退出。**

    这不是「没写完」：`StoryGraph` 的五个方法一个都建不出节点，而 `chapter` 表的主键
    就是 `node.id`。cli.py 自己写 `INSERT INTO node` 被 `tests/test_arch_guard.py` 拦着
    （它是装配层，不是图层），把 cli.py 加进 `GRAPH_TABLE_OWNERS` 则是把守卫变成许可证。
    一个安静地不落库的 `import` 会让 `nh panel` 的「没有花名册」看起来像个 panel 的 bug。
    """
    result = runner.invoke(app, ["import", str(FIXTURE)])

    assert result.exit_code != 0
    assert "落库没做" in result.output


def test_import_with_no_chapter_marks_dies(tmp_path: Path) -> None:
    """零章 = 切章器没认出这本书的章标写法，不是「这本书是空的」。"""
    path = tmp_path / "plain.txt"
    path.write_text("这是一段没有任何章标的文字。\n", encoding="utf-8")
    result = runner.invoke(app, ["import", str(path)])

    assert result.exit_code != 0
    assert "一个章标都没切出来" in result.output


def test_import_of_a_missing_file_dies(tmp_path: Path) -> None:
    result = runner.invoke(app, ["import", str(tmp_path / "nope.txt")])

    assert result.exit_code != 0
    assert "文件不存在" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════════════


def _independent_width(text: str) -> int:
    """**故意不 import `cli._width`。**

    实测过：这条测试原来是 `from novel_harness.cli import _width`，于是把 `_width` 变异成
    `len()`（正是它要防的那个 bug）**依然全绿**——渲染器和断言用的是同一个坏函数，
    它们完美地互相同意。那不是在测「框对齐了」，是在测「框跟自己一致」。
    所以这里重新算一遍宽度：断言必须有一个独立于被测物的真相源。
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def test_the_box_is_not_ragged(book: Path) -> None:
    """中文是双宽的：拿 `len()` 对齐，框会歪。**而那个框就是可交付物本身**（§3.2）。

    失败形态是一个每个人都看得见、却没有测试会红的丑框——所以它值一条测试。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,顾清音,李管家")
    lines = [line for line in result.output.splitlines() if line.startswith(("┌", "│", "└"))]

    assert len(lines) > 5
    assert len({_independent_width(line) for line in lines}) == 1, (
        "框的每一行必须一样宽：\n" + result.output
    )


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip()
