"""`nh init` → `nh import` → `nh declare` → `nh panel`：M1 的整条回路，走真 CLI 真库。

**这个文件的头等大事是 `test_the_chapter_number_is_computed_not_typed`**：从 `nh init`
到 `nh panel`，整条链路上**没有出现过一个章号入参**，而 `nh declare knows` 的 stdout 上
印着 `valid_from = ch3`。那个 3 是「这句引语落在第 3 章」算出来的（§5.9 / 约束 10）。

它跟 `tests/test_no_chapter_input.py` 是两条判据，都要：那份守卫证明的是「命令面上没有
章号输入框」（一个静态事实），这里证明的是「没有输入框也真的算得出章号」。只有前者的话，
一个把 `valid_from` 恒填 1 的实现全绿——**而它在面板上长得完全正常**。

── 为什么这里几乎每条都在断言「报错」────────────────────────────────────

同 `tests/test_cli.py`：静默的零和真的零不许长得一样（§10 约束 8）。而声明层多一条——
**系统不确定时绝不许替作者猜**（§5.9）。歧义引语挑第一个、歧义称呼挑第一个，两者的产物
都是一条在面板上长得完全正常的错边：没有任何一条规则、任何一个面板分区、任何一次
review 会发现它。所以下面每条 `exit_code != 0` 都在钉一个「系统本可以猜，但它没有」。

⚠️ `tests/fixtures/demo_novel.txt` 是**手写的 3 章心跳 fixture，不是真书**（见
`tests/test_cli.py` 的模块 docstring）。这里的 3 == 3 不是 PLAN §8 Day 3 那条验收。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from novel_harness.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "demo_novel.txt"

BLOOD_QUOTE = "你身上流的不是萧家的血"
"""第三章第 2 段，全书唯一一处。作者是**复制**它的——这正是 M1 只做精确匹配的前提。"""

SNOW_QUOTE = "窗外的雪落下来，把整座青云城都盖住了"
"""同样在第三章。"""

HERB_QUOTE = "萧决蹲在药庐后墙根下"
"""第一章。跟上面那句配起来能把 supersede 逼出来。"""

runner = CliRunner()


class Book:
    """一本已经 `nh init` + `nh import` 过的书。**每条命令都走真 CLI。**"""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "book.db"
        self.root = tmp_path / "青云记"
        result = runner.invoke(
            app,
            ["init", "--name", "青云记", "--root", str(self.root), "--db", str(self.db)],
        )
        assert result.exit_code == 0, result.output
        self.pid = result.stdout.strip()

    def invoke(self, *args: str):  # noqa: ANN201
        return runner.invoke(app, [*args, "--db", str(self.db), "-p", self.pid])

    def ok(self, *args: str) -> str:
        result = self.invoke(*args)
        assert result.exit_code == 0, f"{args} 应当成功：\n{result.output}"
        return result.output

    def refused(self, *args: str) -> str:
        result = self.invoke(*args)
        assert result.exit_code != 0, f"{args} 应当被拒绝，实得 exit 0：\n{result.output}"
        return result.output


@pytest.fixture
def book(tmp_path: Path) -> Book:
    b = Book(tmp_path)
    b.ok("import", str(FIXTURE))
    return b


@pytest.fixture
def seeded(book: Book) -> Book:
    """3 章 + 萧决（决哥）+ 血脉秘密。**注意这里一个章号都没敲过。**"""
    book.ok("declare", "character", "萧决", "--alias", "决哥")
    book.ok("declare", "secret", "血脉秘密", "--description", "萧决不是萧家的血脉")
    return book


# ══════════════════════════════════════════════════════════════════════════
# nh init
# ══════════════════════════════════════════════════════════════════════════


def test_init_puts_only_the_project_id_on_stdout(tmp_path: Path) -> None:
    """**stdout 只有 project_id 一行**，别的话一律 stderr（同 `scripts/seed_demo.py`）。

    那一行是给 `PID=$(nh init ...)` 吃的。一个往 stdout 打欢迎语的 init 会让每一个用它的
    脚本拿到一个带着「✓ 建好了」的 project_id，然后下一条命令报「项目不存在」。
    """
    result = runner.invoke(
        app,
        ["init", "--name", "青云记", "--root", str(tmp_path / "r"), "--db", str(tmp_path / "b.db")],
        # stderr 分开收，才验得了「stdout 上只有那一行」。混在一起时这条测试是废的。
    )

    assert result.exit_code == 0
    assert result.stdout.strip().startswith("project:")
    assert len(result.stdout.strip().splitlines()) == 1, f"stdout 不止一行：{result.stdout!r}"


def test_init_creates_the_root_and_the_db(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "青云记"
    db = tmp_path / "青云记.db"
    result = runner.invoke(app, ["init", "--name", "青云记", "--root", str(root), "--db", str(db)])

    assert result.exit_code == 0
    assert root.is_dir(), "root 要 mkdir 出来：作者的下一步是往 chapters/ 里写字"
    assert db.exists()


def test_commands_refuse_an_unknown_project(tmp_path: Path) -> None:
    """项目号打错 → 死。**不替你建**：凭空建出来的项目 root_path 只能是猜的。"""
    db = tmp_path / "b.db"
    runner.invoke(app, ["init", "--name", "x", "--root", str(tmp_path / "r"), "--db", str(db)])
    result = runner.invoke(app, ["sync", "--db", str(db), "-p", "project:01NOPE"])

    assert result.exit_code != 0
    assert "不在" in result.output


def test_commands_refuse_a_missing_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sync", "--db", str(tmp_path / "nope.db"), "-p", "project:x"])

    assert result.exit_code != 0
    assert "库不存在" in result.output


# ══════════════════════════════════════════════════════════════════════════
# nh import / nh sync
# ══════════════════════════════════════════════════════════════════════════


def test_import_persists_three_chapters(book: Book) -> None:
    """**`nh import` 现在真的落库了。** 3 章、3 个文件，且稿子在 root 里。"""
    out = book.ok("import", str(FIXTURE))  # 第二次：幂等

    assert "库里现在 3 章" in out
    assert sorted(p.name for p in (book.root / "chapters").iterdir()) == [
        "0001.md",
        "0002.md",
        "0003.md",
    ]


def test_import_is_idempotent(book: Book) -> None:
    """重跑 = 新建 0 个、复用 3 个。不空 = 章数或章序变了，那是另一回事。"""
    out = book.ok("import", str(FIXTURE))

    assert "新建 0 个章节文件" in out
    assert "复用 3 个" in out


def test_import_refuses_to_overwrite_an_edited_chapter(book: Book) -> None:
    """**覆盖作者的稿子是这个项目最不能犯的错**，比任何一条误报都致命。

    误报他骂一句就过去了，覆盖掉的三千字要他重写。所以没有 `--force`，而且是两阶段的：
    一个字节都不写。
    """
    edited = book.root / "chapters" / "0001.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n作者后来加的一段。\n")

    out = book.refused("import", str(FIXTURE))

    assert "chapters/0001.md" in out, "冲突文件要逐条列出来：不说是哪几个，作者只能删目录重来"
    assert "作者后来加的一段。" in edited.read_text(encoding="utf-8"), "作者改的那个字必须还在"


def test_sync_picks_up_an_edit_made_in_the_editor(seeded: Book) -> None:
    """ADR 0007：`chapters/*.md` **就是稿子**。作者在 VSCode 里改完 → `nh sync` → 可定位。

    没有它，`nh import` 就是一次性播种，而「我刚写完的那句话」永远定位不到。
    """
    new_line = "李管家忽然压低了声音，说出了那个憋了十年的名字。"
    third = seeded.root / "chapters" / "0003.md"
    third.write_text(third.read_text(encoding="utf-8") + f"\n　　{new_line}\n", encoding="utf-8")

    assert "找不到" in seeded.refused("locate", "--quote", new_line)

    out = seeded.ok("sync")
    assert "更新 1 章" in out
    assert "新快照" in out
    assert "命中 1 处" in seeded.ok("locate", "--quote", new_line)


def test_sync_reports_an_empty_chapters_dir_as_a_failure(tmp_path: Path) -> None:
    """真的零 vs 静默的零：一个空的 chapters/ 的自然产物是「✓ 同步完成」+ exit 0，
    而作者的下一步 declare 会因为「引语找不到」被拒——他会去查引语，问题却在这里。"""
    b = Book(tmp_path)
    result = b.invoke("sync")

    assert result.exit_code != 0
    assert "一个 NNNN.md 都没有" in result.output


# ══════════════════════════════════════════════════════════════════════════
# ★ 约束 10：章号是算出来的
# ══════════════════════════════════════════════════════════════════════════


def test_the_chapter_number_is_computed_not_typed(seeded: Book) -> None:
    """**这是这个文件、也是 M1 的头等大事。**

    整条链路（init → import → declare character → declare secret → declare knows）**没有
    出现过一个章号入参**，而 stdout 上印着 `valid_from = ch3`。那个 3 是「这句引语落在
    第 3 章」算出来的：`ev.chapter_number ← chapter.number ← chapterize 的 index`。

    §5.9：问作者「这条关系从第几章开始有效」，他不记得（200 万字写了三年），他会填 1 →
    时态模型退化成快照图 → 项目全部差异化的地基没了。
    """
    out = seeded.ok(
        "declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE
    )

    assert "valid_from = ch3" in out
    assert "第 3 章 · 第 2 段 · 第 0 次" in out, "锚是三元组，永远不是 offset（ADR 0006）"
    assert BLOOD_QUOTE in out
    # 那两行不是装饰：作者看见 ch3 会以为是自己填的，然后下一次他会想去改它。
    assert "算出来的" in out
    assert "你没有输入过这个数字" in out
    assert "已记入 decision_log：decision:" in out


def test_a_quote_from_another_chapter_yields_another_chapter(seeded: Book) -> None:
    """**换一句引语就换一章** —— 证明那个数是引语的函数，不是一个碰巧对了的常量。

    只有上面那一条的话，`valid_from = 3` 硬编码全绿（fixture 恰好 3 章）。
    """
    out = seeded.ok(
        "declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", HERB_QUOTE
    )

    assert "valid_from = ch1" in out


def test_the_closed_open_interval_runs_through_the_real_declaration_path(seeded: Book) -> None:
    """`[valid_from, valid_to)` 的**下界**，穿过真实的声明链路。

    ch3 声明 → ch3 知道 / ch2 不知道。只查 ch3 的话，一个把时态过滤整个丢掉的实现照样
    全绿——它对每一章都答「知道」。这一格便宜且致命。
    """
    seeded.ok("declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE)

    assert "✓ 知道 (ch3)" in seeded.ok("panel", "--chapter", "3", "--cast", "萧决")
    assert "✗ 不知道" in seeded.ok("panel", "--chapter", "2", "--cast", "萧决")


def test_believes_renders_what_he_thinks_it_is(seeded: Book) -> None:
    """只画一个 ⚠ 而不说他以为的是什么，等于没说。"""
    out = seeded.ok(
        "declare", "believes", "--who", "萧决", "--secret", "血脉秘密",
        "--as", "只是流言", "--quote", HERB_QUOTE,
    )

    assert "valid_from = ch1" in out
    assert "只是流言" in out
    assert "⚠ 错误认知 (ch1)" in seeded.ok("panel", "--chapter", "2", "--cast", "萧决")


def test_declare_where_prints_the_supersede(seeded: Book) -> None:
    """**招牌动作。** 作者敲的是「他到了北荒」，系统顺手把「他在药庐」闭合到 `[1, 3)`。

    那个 3 同样是算出来的（= 新边的 valid_from）。不印的话，作者永远不知道系统替他维护了
    一条时间线——而他不知道的功能等于不存在的功能。
    """
    seeded.ok("declare", "place", "青云城主府")
    seeded.ok("declare", "place", "北荒")
    seeded.ok("declare", "where", "--who", "萧决", "--loc", "青云城主府", "--quote", HERB_QUOTE)

    out = seeded.ok("declare", "where", "--who", "萧决", "--loc", "北荒", "--quote", SNOW_QUOTE)

    assert "valid_from = ch3" in out
    assert "自动闭合" in out
    assert "青云城主府" in out
    assert "[1, 3)" in out, "闭开区间要原样印出来：150 和 149 差一章，而差一章是最贵的 bug"


# ══════════════════════════════════════════════════════════════════════════
# 拒绝：系统不确定时绝不替作者猜
# ══════════════════════════════════════════════════════════════════════════


def test_an_ambiguous_quote_is_refused_with_candidates(seeded: Book) -> None:
    """「萧决」在全书出现好几次 → 拒绝 + 摆候选。**绝不挑第一个。**

    挑错的产物是一条 `valid_from` 错了的 CANON 边，而它在面板上长得完全正常。
    这不违反约束 8：约束 8 治的是系统主动推队列；这里是作者按了按钮而系统不肯替他猜。
    """
    out = seeded.refused(
        "declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", "萧决"
    )

    assert "系统不替你挑" in out
    assert "第   1 章" in out and "第   3 章" in out, "候选要逐条摆出来"
    assert "药庐" in out, "候选要带上下文：不然作者不知道该往哪边加长引语"
    assert "没有 --pick" in out and "也没有 --chapter" in out


def test_a_quote_that_is_not_in_the_text_is_refused(seeded: Book) -> None:
    """找不到 → 提到 `nh sync`：最常见的原因是作者刚在编辑器里改过这一章。"""
    out = seeded.refused(
        "declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", "他推开了那扇门"
    )

    assert "一处都找不到" in out
    assert "nh sync" in out


def test_an_ambiguous_name_is_refused_with_candidates(book: Book) -> None:
    """「师兄」→ 2 个人 → 拒绝。挑错的产物是一条本该保密的秘密从 must_not_reveal 里消失，
    而那一格在面板上长得跟「他确实不知道」一模一样。"""
    book.ok("declare", "character", "萧决", "--alias", "师兄")
    book.ok("declare", "character", "李管家", "--alias", "师兄")
    book.ok("declare", "secret", "血脉秘密")

    out = book.refused(
        "declare", "knows", "--who", "师兄", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE
    )

    assert "系统不替你挑" in out
    assert "萧决" in out and "李管家" in out
    assert "must_not_reveal" in out


def test_an_unknown_name_is_refused(seeded: Book) -> None:
    out = seeded.refused(
        "declare", "knows", "--who", "张三", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE
    )

    assert "没有叫「张三」的东西" in out


def test_a_secret_slot_refuses_a_character(seeded: Book) -> None:
    """`--secret` 收到一个人 → 拒绝。一条 dst 是人的 KNOWS 边在面板上**不成列**——
    作者看见的是「系统对这条没意见」，那是最沉默的一种错。"""
    out = seeded.refused(
        "declare", "knows", "--who", "萧决", "--secret", "决哥", "--quote", BLOOD_QUOTE
    )

    assert "这里要的是 Secret" in out


def test_a_one_character_alias_needs_not_for_rules(seeded: Book) -> None:
    """ADR 0004：「音」「决」去 200 万字里做子串匹配 = 每章几十条误报 = 作者弃用。

    但它**存得下**——只是规则不拿它开火。拒绝的消息必须把这条出路说出来，
    否则作者只会以为系统不让他声明这个别名。
    """
    assert "ADR 0004" in seeded.refused("declare", "alias", "--of", "萧决", "--surface", "音")

    out = seeded.ok("declare", "alias", "--of", "萧决", "--surface", "音", "--not-for-rules")
    assert "规则不会拿这个称呼去正文里匹配" in out


def test_declare_alias_has_no_canonical_kind(seeded: Book) -> None:
    """canonical 是 `upsert_node` 的独占物（节点的 name 就是它）。从命令行递一个进去撞的是
    `idx_alias_canonical` 给的那条读不懂的 IntegrityError——所以 `--kind` 根本不收它。"""
    result = seeded.invoke(
        "declare", "alias", "--of", "萧决", "--surface", "x", "--kind", "canonical"
    )

    assert result.exit_code != 0


# ══════════════════════════════════════════════════════════════════════════
# nh locate
# ══════════════════════════════════════════════════════════════════════════


def test_locate_previews_chapter_paragraph_and_occurrence(book: Book) -> None:
    """`nh locate` 是 declare 那条拒绝的解药：先便宜地试出一句只匹配一处的引语。"""
    out = book.ok("locate", "--quote", BLOOD_QUOTE)

    assert "命中 1 处" in out
    assert "第   3 章 · 第   2 段 · 第 0 次" in out


def test_locate_reports_multiple_hits(book: Book) -> None:
    out = book.ok("locate", "--quote", "萧决")

    assert "命中 4 处" in out


def test_locate_exits_nonzero_when_it_finds_nothing(book: Book) -> None:
    """零命中的答案是「这句引语不能用」。印一行「命中 0 处」然后 exit 0，跟印「命中 1 处」
    在 `set -e` 的脚本眼里一模一样。"""
    result = book.invoke("locate", "--quote", "这句话不在书里")

    assert result.exit_code != 0
    assert "一处都找不到" in result.output


def test_locate_changes_nothing(seeded: Book) -> None:
    """只读。跑一次 locate 之后，面板必须一个字都没变。"""
    before = seeded.ok("panel", "--chapter", "3", "--cast", "萧决")
    seeded.ok("locate", "--quote", BLOOD_QUOTE)

    assert seeded.ok("panel", "--chapter", "3", "--cast", "萧决") == before


# ══════════════════════════════════════════════════════════════════════════
# 节点声明
# ══════════════════════════════════════════════════════════════════════════


def test_declare_character_is_idempotent(book: Book) -> None:
    """再声明一次不会建出第二个「萧决」。

    第二个的产物：`resolve("萧决")` 返回 2 个 hit → ambiguous → 面板上**整行消失**，
    且没有一步会报错。
    """
    book.ok("declare", "character", "萧决")
    book.ok("declare", "character", "萧决")
    book.ok("declare", "secret", "血脉秘密")

    out = book.ok(
        "declare", "knows", "--who", "萧决", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE
    )

    assert "valid_from = ch3" in out


def test_an_alias_resolves_the_same_node(seeded: Book) -> None:
    """`--who 决哥` 和 `--who 萧决` 是同一个人。别名**永不合并实体**，但解析到同一个节点。"""
    out = seeded.ok(
        "declare", "knows", "--who", "决哥", "--secret", "血脉秘密", "--quote", BLOOD_QUOTE
    )

    assert "✓ 萧决 KNOWS 血脉秘密" in out, "回执印的是本名，不是他这次敲的那个称呼"


def test_declare_secret_can_hang_a_sub_fact(seeded: Book) -> None:
    """子事实是 ADR 0005 删掉 `PARTIALLY_KNOWS` 之后「部分知道」的唯一表达法。"""
    out = seeded.ok("declare", "secret", "母亲的身份", "--sub-of", "血脉秘密")

    assert "是「血脉秘密」的子事实" in out


def test_sub_of_refuses_an_unknown_parent(seeded: Book) -> None:
    """`--sub-of` 也不许猜：它走的是跟 `--who` 同一套拒绝形态。"""
    out = seeded.refused("declare", "secret", "x", "--sub-of", "根本没这个秘密")

    assert "没有叫「根本没这个秘密」的东西" in out


def test_sub_of_refuses_a_character(seeded: Book) -> None:
    out = seeded.refused("declare", "secret", "x", "--sub-of", "萧决")

    assert "这里要的是 Secret" in out
