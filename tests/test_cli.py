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

import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from typer.testing import CliRunner

from novel_harness import db, project
from novel_harness.checks import ALL_CHECKS
from novel_harness.cli import app
from novel_harness.declare import Ledger
from novel_harness.graph import (
    AliasKind,
    EdgeProps,
    EdgeSpec,
    EdgeType,
    InformationScope,
    NodeLabel,
    SecretDetail,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph

FIXTURE = Path(__file__).parent / "fixtures" / "demo_novel.txt"

_ANY_PID = "project:cli00000:01J0CLI00000000000000000"
"""**只给那些在读到 project_id 之前就该死的测试用**（库不存在 / 项目号打错）。

真的 project_id 由 `project.create()` 生成，见 `Seeded.pid`——写死一个然后指望它
存在，就是在假设 `nh init` 的 id 格式永远不变。
"""

runner = CliRunner()


# ══════════════════════════════════════════════════════════════════════════
# 一个真库
# ══════════════════════════════════════════════════════════════════════════


class Seeded(NamedTuple):
    """一个填好数据的库：路径 + project_id + 名字→node_id。

    **node_id 是 `upsert_node` 生成的真 ULID，不是写死的常量。** 它们只能是返回值：
    `SqliteStoryGraph` 只收 `edge_id_factory`，没有 node_id 的注入点——而为了让测试
    能写死 ID 去给生产代码加一个注入点，是让被测物迁就断言。
    """

    path: Path
    pid: str
    ids: dict[str, str]


def _seed(path: Path) -> Seeded:
    """§3.2 / README:20-31 那张框图的世界，**逐格照抄**，全程走生产侧的写路径。

    这里曾经直接写 SQL，理由是「生产侧根本没有建节点的路径」。**M1 之后那条路径存在了**
    （`project.create` + `Ledger.declare_node` / `declare_alias` + `store.upsert_edge`），
    所以这里走它——测试里手写第二份写路径就等于把被测物换成了自己：那份 SQL 会一直
    绿着，哪怕 `upsert_node` 某天忘了建 canonical 别名。

    那四个章号（88/103/120/150）仍然是字面量：约束 10 管的是**作者的输入面**，不是 store
    的写接口，而本文件没有一本书可以指（`declare_knows` 要引语在正文里真的存在）。
    「引语 → 章号 → valid_from」那条链由 `scripts/demo.sh` 的第二条泳道和
    `tests/test_declare_cli.py` 量，不由这里量。
    """
    conn = db.connect(path)
    db.migrate(conn)

    pid = project.create(conn, name="青云记", root_path=str(path.parent)).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)

    nodes: list[tuple[NodeLabel, str, SecretDetail | None]] = [
        (NodeLabel.CHARACTER, "萧决", None),
        (NodeLabel.CHARACTER, "顾清音", None),
        (NodeLabel.CHARACTER, "李管家", None),
        (NodeLabel.SECRET, "血脉秘密", SecretDetail()),
        (NodeLabel.SECRET, "玄铁令下落", SecretDetail()),
        (NodeLabel.LOCATION, "青云城主府", None),
        (NodeLabel.LOCATION, "北荒", None),
    ]
    ids = {name: ledger.declare_node(label, name, secret=secret).id for label, name, secret in nodes}

    # 「师兄」→ 2 个人。§3.1 点名的那个场景（一章里 8 个角色都叫「师兄」）的最小形态：
    # 歧义是**跨行**事实，所以两行各自 usable_for_rules=1，歧义只能在查询时算出来。
    for name in ("萧决", "李管家"):
        ledger.declare_alias(of=name, surface="师兄", kind=AliasKind.TITLE)

    edges: list[tuple[str, str, EdgeType, int, EdgeProps]] = [
        ("萧决", "血脉秘密", EdgeType.KNOWS, 88, EdgeProps()),
        ("萧决", "玄铁令下落", EdgeType.KNOWS, 120, EdgeProps()),
        ("李管家", "血脉秘密", EdgeType.BELIEVES, 103, EdgeProps(believed_value="已泄露")),
        ("萧决", "北荒", EdgeType.LOCATED_AT, 150, EdgeProps()),
    ]
    for src, dst, edge_type, valid_from, props in edges:
        store.upsert_edge(
            EdgeSpec(
                project_id=pid,
                src=ids[src],
                dst=ids[dst],
                type=edge_type,
                props=props,
                valid_from_chapter=valid_from,
                information_scope=InformationScope.CANON,
            )
        )

    conn.commit()
    conn.close()
    return Seeded(path=path, pid=pid, ids=ids)


@pytest.fixture
def book(tmp_path: Path) -> Iterator[Seeded]:
    """一个建好、迁移过、填好数据的库。"""
    yield _seed(tmp_path / "book.db")


def _panel(book: Seeded, *args: str):
    return runner.invoke(app, ["panel", "--db", str(book.path), "-p", book.pid, *args])


# ══════════════════════════════════════════════════════════════════════════
# 本周的 done_when
# ══════════════════════════════════════════════════════════════════════════


def test_the_weeks_done_when(book: Seeded) -> None:
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


def test_must_not_reveal_is_rendered_under_the_matrix(book: Seeded) -> None:
    """README 的框里矩阵下面就印着这一行。

    在场三人里顾清音和李管家都不是 KNOWS（BELIEVES 也算——把真相说破同样崩人设），
    所以两条秘密都得瞒。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决,顾清音,李管家")

    assert result.exit_code == 0, result.output
    assert "本场景 must_not_reveal：" in result.output
    assert "血脉秘密" in result.output
    assert "玄铁令下落" in result.output


def test_a_secret_everyone_knows_leaves_must_not_reveal(book: Seeded) -> None:
    """cast 只剩萧决时，他两条都 KNOWS → 没有东西要瞒。

    **这一条是上一条的反面，缺了它上一条就是废的**：一个永远打印全部秘密的
    `must_not_reveal`（fail-closed 的退化值长得一模一样）也能让上一条绿。
    """
    result = _panel(book, "--chapter", "152", "--cast", "萧决")

    assert result.exit_code == 0, result.output
    assert "本场景 must_not_reveal：（无）" in result.output


def test_the_matrix_is_as_of_the_queried_chapter(book: Seeded) -> None:
    """`--chapter` 是「AS OF 第几章」。ch87 → 萧决还不知道血脉秘密（闭开区间的下界）。

    §8 Day 5 点名的单测就是这个：ch87 UNKNOWN / ch88 KNOWS / ch152 KNOWS。
    这里在 **CLI 这一层**再钉一次——中间隔着 `--chapter` 的字符串转 int 和一整条装配链。
    """
    assert "✓ 知道 (ch88)" not in _panel(book, "--chapter", "87", "--cast", "萧决").output
    assert "✓ 知道 (ch88)" in _panel(book, "--chapter", "88", "--cast", "萧决").output


# ══════════════════════════════════════════════════════════════════════════
# 歧义：必须画出来，不许静默丢
# ══════════════════════════════════════════════════════════════════════════


def test_an_ambiguous_appellation_is_rendered_not_dropped(book: Seeded) -> None:
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


def test_an_ambiguous_cast_degrades_must_not_reveal_to_everything(book: Seeded) -> None:
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


def test_cast_is_never_resolved_by_the_cli_itself(book: Seeded) -> None:
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
        app, ["panel", "--db", str(missing), "-p", _ANY_PID, "--chapter", "1", "--cast", "萧决"]
    )

    assert result.exit_code != 0
    assert "库不存在" in result.output
    assert not missing.exists(), "拦下来了却把库建出来了，那下一次运行就会静默通过"


def test_a_wrong_project_id_dies(book: Seeded) -> None:
    """项目号打错 → 花名册为空 → 零行矩阵。它长得像「没问题」，其实是「没数据」。"""
    result = runner.invoke(
        app,
        [
            "panel",
            "--db",
            str(book.path),
            "-p",
            "project:nope:x",
            "--chapter",
            "1",
            "--cast",
            "萧决",
        ],
    )

    assert result.exit_code != 0
    assert "没有任何花名册行" in result.output


def test_an_empty_cast_dies(book: Seeded) -> None:
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

    这个库同样走真 API 建：一个「声明了人、没声明秘密」的项目正是 `nh init` 之后
    第一天的样子，而它必须是真的那一天，不是手写 SQL 拼出来的近似物。
    """
    path = tmp_path / "nosecrets.db"
    conn = db.connect(path)
    db.migrate(conn)
    pid = project.create(conn, name="空书", root_path=str(tmp_path)).id
    Ledger(SqliteStoryGraph(conn), conn, pid).declare_node(NodeLabel.CHARACTER, "萧决")
    conn.commit()
    conn.close()

    result = runner.invoke(
        app, ["panel", "--db", str(path), "-p", pid, "--chapter", "152", "--cast", "萧决"]
    )

    assert result.exit_code != 0
    assert "一条秘密都没声明" in result.output


def test_chapter_zero_dies(book: Seeded) -> None:
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


def test_check_reports_a_location_conflict(book: Seeded, tmp_path: Path) -> None:
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
        [
            "check",
            "--db",
            str(book.path),
            "-p",
            book.pid,
            "--chapter",
            "151",
            "-f",
            str(manuscript),
        ],
    )

    assert result.exit_code != 0, result.output
    assert "[R4] LOCATION_CONFLICT" in result.output
    assert "北荒" in result.output
    assert "青云城主府" in result.output
    # 建议由规则确定性产出（PLAN 改 13），不是 LLM 编的。
    assert "建议：" in result.output


def test_check_prints_the_anchor_triple_and_never_an_offset(book: Seeded, tmp_path: Path) -> None:
    """锚是 `(para_index, quote_text, occurrence_k)`（ADR 0006）。**后端永不对外发 offset。**

    指令行在第 1 行（0-based），`quote_text` 必须是那一行的原文。
    """
    manuscript = _chapter_file(
        tmp_path, "## 场景 1\n<!-- nh: cast=萧决 loc=青云城主府 -->\n"
    )
    result = runner.invoke(
        app,
        [
            "check",
            "--db",
            str(book.path),
            "-p",
            book.pid,
            "--chapter",
            "151",
            "-f",
            str(manuscript),
        ],
    )

    assert "第 1 段" in result.output
    assert "第 0 次" in result.output
    assert "<!-- nh: cast=萧决 loc=青云城主府 -->" in result.output


def test_check_says_how_many_rules_it_ran(book: Seeded, tmp_path: Path) -> None:
    """**零 issue 必须带着「跑了几条规则」一起出现**（§10 约束 8 / `checks/__init__.py:29`）。

    2026-08-02 起 `ALL_CHECKS` 是 R2/R3/R4；「无 issue」的真实含义是
    「三条规则都没意见」，不是「这一章没问题」——沉默的工具死得比吵闹的工具更快，
    只是死得更安静。
    """
    manuscript = _chapter_file(tmp_path, "## 场景 1\n<!-- nh: cast=萧决 loc=北荒 -->\n")
    result = runner.invoke(
        app,
        [
            "check",
            "--db",
            str(book.path),
            "-p",
            book.pid,
            "--chapter",
            "151",
            "-f",
            str(manuscript),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "跑了 3 条规则" in result.output
    assert "location_conflict" in result.output
    assert "future_leak" in result.output
    assert "dead_speaks" in result.output
    assert "0 条 issue" in result.output


def test_draft_is_an_experimental_channel_that_prints_text(
    book: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实验通道：不走 kill-gate、不写证据，纯打印草稿给维护者看行为。"""
    import novel_harness.draft.generate as generate_mod
    from novel_harness.draft.provider import CompletionResult

    calls: list[dict] = []

    def fake(
        messages,
        *,
        config=None,
        plan=None,
        client=None,
    ) -> CompletionResult:
        del client
        calls.append({"config": config, "plan": plan})
        return CompletionResult(
            text="萧决道：「此剑无名。」", model="fake", finish_reason="stop"
        )

    monkeypatch.setattr(generate_mod, "complete", fake)
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "not-needed")
    monkeypatch.setenv("NH_LLM_TEMPERATURE", "0.3")

    result = runner.invoke(
        app,
        [
            "draft",
            "--goal", "萧决看剑。",
            "--cast", "萧决",
            "--chapter", "1",
            "--db", str(book.path),
            "-p", book.pid,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "萧决道：「此剑无名。」" in result.output
    assert calls and calls[0]["plan"] is not None


def test_check_without_scene_blocks_still_runs_the_rules_that_read_prose(
    book: Seeded, tmp_path: Path
) -> None:
    """没有场景块**不再是拒绝**，是一句说明。

    ── 这条测试 2026-08-13 反过来了，反的理由是它原来那句话过期了 ────────────

    原来这里断言 `exit_code != 0`，理由写着「没有场景块 = R4 无事可做 = 必然零 issue，
    不让那个零冒充体检报告」。那在 `ALL_CHECKS` 只有 R4 的那天是对的。R2/R3 在
    2026-08-02 进表之后就不对了：**那两条读的是正文，一个场景块都不需要**。
    而真书里没有人手写 `<!-- nh: -->`，于是那句 `_die` 的实际效果，是在整本真书上
    把 R2/R3 全部挡在门外——一句为了防「假的零」写的话，最后造出的是「一条都跑不了」。

    约束 8 那一半原样在：**那个零的成色必须说出来**，所以既印「跑了几条规则」，
    也印「哪一条今天没东西可查、为什么」。
    """
    manuscript = _chapter_file(tmp_path, "萧决站在城头上，看着北荒的方向。\n")
    result = runner.invoke(
        app,
        [
            "check",
            "--db",
            str(book.path),
            "-p",
            book.pid,
            "--chapter",
            "151",
            "-f",
            str(manuscript),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "0 个场景块" in result.output
    assert f"跑了 {len(ALL_CHECKS)} 条规则" in result.output
    # 零必须带着理由（§10 约束 8）：不说的话，「R4 没东西可查」和「R4 查过了没意见」
    # 在终端上一模一样，而那正是原来那句 `_die` 想防的东西。
    assert "没东西可查" in result.output


# ══════════════════════════════════════════════════════════════════════════
# nh import
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def empty_book(tmp_path: Path) -> Iterator[Seeded]:
    """一个 `nh init` 之后、一条声明都没有的项目。`nh import` 的起点。

    不用上面那个 `book`：它的 `root_path` 是 tmp_path 本身，而 import 会往
    `root_path/chapters/` 写文件——两个 fixture 共用一个目录会让「谁写的这个 0001.md」
    变成一个要读两个 fixture 才答得上来的问题。
    """
    root = tmp_path / "root"
    path = tmp_path / "empty.db"
    conn = db.connect(path)
    db.migrate(conn)
    pid = project.create(conn, name="青云记", root_path=str(root)).id
    conn.commit()
    conn.close()
    yield Seeded(path=path, pid=pid, ids={})


def _import(target: Seeded, txt: Path):
    return runner.invoke(app, ["import", str(txt), "--db", str(target.path), "-p", target.pid])


def test_import_chapterizes_the_heartbeat_fixture(empty_book: Seeded) -> None:
    """3 章。⚠️ **这不是 PLAN §8 Day 3 那条「真书章数 = 目录数」的验收**——
    见本模块 docstring：那条至今 BLOCKED，因为没有人提供过那本 300 章的 TXT。

    这里真正在钉的是脏数据的处置：卷标题 / 作者的话 / 番外**一个都不许被切成章**
    （多切一章 = 全书 index 集体偏移 = `state_at` 从此答隔壁那一章）。
    """
    result = _import(empty_book, FIXTURE)

    assert "切出 3 章" in result.output
    assert "第一章  少年萧决" in result.output
    assert "第二章  玄铁令" in result.output
    assert "第三章  （无标题）" in result.output
    # 「第一卷 风起青云」「作者的话：」「番外：」都不是章界，它们落在 body / preamble 里。
    assert "第一卷" not in result.output
    assert "番外" not in result.output


def test_import_persists_the_chapters(empty_book: Seeded, tmp_path: Path) -> None:
    """**落库了就要说落了几章，且盘上真的要有那三个文件。**

    这条测试原来叫 `test_import_refuses_to_pretend_it_persisted`，断言的是非 0 退出 +
    「落库没做」——那时 `StoryGraph` 的五个方法一个都建不出节点，而一个安静地不落库的
    import 会让 `nh panel` 的「没有花名册」看起来像 panel 的 bug。**M1 之后那条路径存在
    了**，所以这里反过来钉它真的走完：文件 + 库 + 回执三样都要在。

    章号来自**文件名的顺序位置**（`0001.md` → 第 1 章），而文件名来自 `chapterize` 的
    index——作者的输入进不到这条链上（约束 10）。
    """
    result = _import(empty_book, FIXTURE)

    assert result.exit_code == 0, result.output
    assert "✓ 落库" in result.output
    assert "库里现在 3 章" in result.output

    chapters = tmp_path / "root" / "chapters"
    assert sorted(p.name for p in chapters.iterdir()) == ["0001.md", "0002.md", "0003.md"]
    # 章标行原样、**没有 `# ` 前缀**：加了它 chapterize 就再也读不回来（importer.chapter_text）。
    assert chapters.joinpath("0001.md").read_text(encoding="utf-8").startswith("第一章 少年萧决\n\n")

    # 库这一侧走生产的读路径，不在测试里自己 SELECT。三章都进了花名册之外的 chapter 表，
    # 而 `current_snapshots` 是 `nh declare` 定位引语时用的那一个。
    conn = db.connect(empty_book.path)
    snapshots = SqliteStoryGraph(conn).current_snapshots(empty_book.pid)
    conn.close()
    assert [s.number for s in snapshots] == [1, 2, 3]


def test_import_is_idempotent(empty_book: Seeded) -> None:
    """重跑一次：不新建文件、不新建快照、仍然 exit 0。

    **没有 `--force`，也没有覆盖**（`importer.explode`）：作者的稿子是 chapters/*.md，
    覆盖掉的三千字要他重写，而一条误报他骂一句就过去了。
    """
    assert _import(empty_book, FIXTURE).exit_code == 0
    result = _import(empty_book, FIXTURE)

    assert result.exit_code == 0, result.output
    assert "新建 0 个章节文件，复用 3 个" in result.output


def test_import_with_no_chapter_marks_dies(empty_book: Seeded, tmp_path: Path) -> None:
    """零章 = 切章器没认出这本书的章标写法，不是「这本书是空的」。"""
    path = tmp_path / "plain.txt"
    path.write_text("这是一段没有任何章标的文字。\n", encoding="utf-8")
    result = _import(empty_book, path)

    assert result.exit_code != 0
    assert "一个章标都没切出来" in result.output


def test_import_of_a_missing_file_dies(empty_book: Seeded, tmp_path: Path) -> None:
    result = _import(empty_book, tmp_path / "nope.txt")

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


def test_the_box_is_not_ragged(book: Seeded) -> None:
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
