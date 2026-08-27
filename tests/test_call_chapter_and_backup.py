"""对抗性验证：记账（迁移 009）与升级前备份。

`tests/test_call_chapter.py` / `tests/test_migration_backup.py` 量的是这一刀**做到了
什么**；这一份量的是它**在哪几处仍然看不见**，以及那几张网罩自己会不会空转。
每一节的形状都一样：**先证明陷阱是真的，再证明我们没踩进去**——不证明陷阱，
守卫就只是句口号，而这个仓库栽过「假实现比真实现宽，于是测试绿而产品错」。

四件这一份独有的事：

1. **退掉兜底反查之后作者看见什么**（真库里今天 100 行账**全部**只有反查答得出，
   一行都没有新列）。上面那份断言的是「兜底还在」，这一份断言的是「没有它的那个
   世界长什么样」——不量出坏结果，就不知道那条兜底值多少钱。
2. **备份是不是一份真能用的库**：`integrity_check` + 外键 + 逐表行数。
   一个大小对、打得开、其实少了半张表的文件，比没有备份更糟。
3. **那张 AST 网罩认不认得属性形态的调用**（`call_audit.record_call(...)`）。
   `_ledger_call_sites` 只匹配 `ast.Name`，实测漏得掉。
4. **`/draft` 落账之后长出来的那种日志行，今天没有任何一条屏幕守卫扫过**
   （`test_wording_guard.py` 只种 extractor / 失败 run / 确认三种样本）。
"""

from __future__ import annotations

import ast
from hashlib import sha256
import os
import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_activity import detail_row_map, seed_call, seed_run
from test_call_chapter import SILENT_LEDGERS_TODAY
from test_wording_guard import dev_shapes

from novel_harness import activity
from novel_harness.db import (
    BACKUP_LABEL,
    MigrationError,
    connect,
    migrate,
    user_version,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_harness"

ZH_LENGTH = {"language": "zh", "min_units": 2_000, "target_units": 2_500, "max_units": 3_000}

DRAFT_TAIL = "夜色沉下来，城主府的灯一盏盏亮起。"
"""光标前那一截。**2026-08-26 起 `/draft` 只有行内续写**——整章起草那个入口零调用方，
随 `mode` / `goal` 一起删了（`api/app.py::DraftRequest`）。这个文件量的是账、出参键、
日志页那一行，**三样都和是哪种模式无关**，所以换的是请求体不是性质。"""

OLD_VERSION = 7
"""从第几版升上来。挑一个真存在的中间版本，跑的是真 `ALTER TABLE` 不是编出来的迁移。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BYOK 连接参数。**设置文件指向 tmp**，别读到开发机上真的那一份。"""
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """桩没打上就当场炸，别静默走真路径（同 `test_call_chapter.py`）。"""
    import novel_harness.draft.provider as provider

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("测试要发真请求了 —— 有一处桩没打上")

    monkeypatch.setattr(provider, "_build_client", refuse)


def _answers(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    from novel_harness.draft import generate
    from novel_harness.draft.provider import CompletionResult

    monkeypatch.setattr(
        generate,
        "complete",
        lambda messages, *, config, plan, client=None: CompletionResult(
            text=text, model="deepseek-v4-flash", finish_reason="stop", prompt_tokens=1_111
        ),
    )


def _calls(book: dict[str, str]) -> list[dict[str, Any]]:
    conn = connect(book["db"])
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, capability, chapter_number FROM model_call "
                "WHERE project_id = ? ORDER BY rowid",
                (book["pid"],),
            )
        ]
    finally:
        conn.close()


def _book_at(folder: Path, version: int = OLD_VERSION, *, projects: int = 1):
    """一个停在第 `version` 版、里面已经有东西的库。返回 `(路径, 打开着的连接)`。"""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "book.db"
    conn = connect(path)
    root = files("novel_harness") / "migrations"
    for entry in sorted(e.name for e in root.iterdir() if e.name.endswith(".sql")):
        if int(entry[:3]) > version:
            continue
        conn.executescript((root / entry).read_text(encoding="utf-8"))
    for i in range(projects):
        conn.execute(
            "INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)",
            (f"project:seed{i}", f"青云记{i}", f"/x/{i}"),
        )
    conn.commit()
    assert user_version(conn) == version
    return path, conn


def _backups(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if BACKUP_LABEL in p.name)


def _table_counts(path: Path) -> dict[str, int] | None:
    """逐表行数。**打不开就是 `None`**——那也是「丢了」的一种长相，不许糊成 0。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in names}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 一、旧行：退掉兜底之后，作者看见的是什么
# ══════════════════════════════════════════════════════════════════════════


def _old_rows(book: dict[str, str], client: TestClient) -> dict[str, int]:
    """造两行**这一列出现之前**的账（extractor / summarizer 各一），返回 `{id: 该是第几章}`。"""
    extractor = seed_call(book, capability="extractor")
    seed_run(book, 2, call_id=extractor)
    summarizer = seed_call(book, capability="summarizer")
    conn = connect(book["db"])
    try:
        chapter_id = conn.execute(
            "SELECT id FROM chapter WHERE project_id = ? AND number = 1",
            (book["pid"],),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO chapter_summary (
                id, project_id, chapter_id, chapter_number, summary, summary_sha256,
                schema_version, prompt_hash, model_call_id
            ) VALUES ('summary:old', ?, ?, 1, '第一章讲了什么', ?, 'nh.summary.v1', 'ph', ?)
            """,
            (
                book["pid"],
                chapter_id,
                sha256("第一章讲了什么".encode("utf-8")).hexdigest(),
                summarizer,
            ),
        )
        conn.commit()
        # 前提：它们的新列真的是空的。不断言这一条，下面测的就是别的东西。
        assert [
            row["chapter_number"]
            for row in conn.execute(
                "SELECT chapter_number FROM model_call WHERE id IN (?, ?)",
                (extractor, summarizer),
            )
        ] == [None, None]
    finally:
        conn.close()
    return {extractor: 2, summarizer: 1}


def _chapter_on_screen(client: TestClient, pid: str, call_id: str) -> int | None:
    """展开详情「为哪一章」那一行说的章号。

    国际化第四批·笔二起这一行不再是拼好的「第 N 章」/「未记录」字符串，是
    `value_code="value_chapter"` + `value_params={"chapter": N 或 None}`——
    直接把那个原始章号还回去，调用方比对结构，不比对渲染出来的句子。
    """
    detail = client.get(f"/api/projects/{pid}/activity/{call_id}")
    assert detail.status_code == 200, detail.text
    row = detail_row_map(detail.json()["rows"])["detail_label_for_chapter"]
    assert row["value_code"] == "value_chapter"
    return row["value_params"]["chapter"]


def test_rows_from_before_this_column_keep_their_chapter_on_the_activity_page(
    book: dict[str, str], client: TestClient
) -> None:
    """作者库里今天躺着的那 100 行账，`chapter_number` **全部**是 NULL。

    它们的章号只有反查拿得到，所以这一整页的「为哪一章」今天 100% 靠兜底。
    """
    for call_id, chapter in _old_rows(book, client).items():
        assert _chapter_on_screen(client, book["pid"], call_id) == chapter


def test_retiring_the_reverse_lookup_blanks_every_row_the_author_has_today(
    book: dict[str, str], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自守卫：**把兜底反查换成「只读新列」那种省事写法**，同一批行当场变「未记录」。

    ── 为什么要把坏结果量出来 ────────────────────────────────────────────
    上面那条只断言「兜底还在」。可它绿着的时候，没人知道退掉兜底的代价是多少——
    而 `009_call_chapter.sql` 的注释前半段恰好写着「加列之后那两条反查也可以退休」。
    这条测的就是那半句话的后果：它**不报错**，只是那一页的章号一夜之间全变成空，
    而没有任何别的东西会红。（真库实测：100 行账，100 行全靠反查。）
    """
    rows = _old_rows(book, client)
    monkeypatch.setattr(
        activity, "_call_chapter", lambda row: activity._int(row["own_chapter"])
    )
    blanked = {
        call_id: _chapter_on_screen(client, book["pid"], call_id) for call_id in rows
    }
    assert set(blanked.values()) == {None}, (
        f"退掉反查居然还答得出章号（{blanked}）—— 这条测试的前提没了，它在验一个空集"
    )


# ══════════════════════════════════════════════════════════════════════════
# 二、`/draft` 落了账，出参一个键都没多
# ══════════════════════════════════════════════════════════════════════════

DRAFT_RESPONSE_KEYS = frozenset(
    {
        "experimental",
        "note",
        "text",
        "memory",
        "track",
        "length",
        "attempts",
        "model",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
    }
)
"""`/draft` 响应体的全部键。**这是前端契约的一部分**（`useContinuation` 吃它）。

「账记上了」的正确形状是库里多几行，不是出参多一个 `call_id`。逐字节那一半由
`git archive HEAD` 出旧树、同桩同库对拷验过（17 种请求形状 + 真正发出去的
`_wire_kwargs` 全部 sha256 相同）；那件事测试做不到，这里冻的是它的耐久那一半——
**谁哪天想在这儿挂一个「这次花了多少钱」，会先撞上这条。**

── `track`（2026-08-23 加的那一格）：加它的时候这条守卫红了一次 ────────────

**那正是它在起作用。** 它和被它挡住的那一种（「这次花了多少钱」）差在哪：

- **账是副产物，它的家在库里**（`model_call`）。出参里再挂一份 = 第二个真相源，
  而且那一份还会先过期。
- **轨道是这一次请求算出来的东西，而且它有意没进 prompt**（`track.py`：后面章节的
  总结里可能写着这一章的读者还不该知道的事）。没有这一格，「后面哪几章跟这一段
  相关」就只活在那一次调用的栈上——**连「它到底算过没有」都没人看得见**，
  而这件事的病历正是「索引在跑、函数在，但写作那条路一次都不调」。

**加第 12 个键之前先回答同一个问题：它的家是不是在别处。**
"""


def test_the_draft_response_gained_no_key_when_the_bill_landed(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answers(monkeypatch, "字" * 2_400)
    before = len(_calls(book))
    reply = client.post(
        f"/api/projects/{book['pid']}/chapters/2/draft",
        json={"previous_tail": DRAFT_TAIL, "length": ZH_LENGTH},
    )
    assert reply.status_code == 200, reply.text
    assert set(reply.json()) == DRAFT_RESPONSE_KEYS
    # 不是空转：出参没变，**是因为账落在库里**，不是因为什么都没发生。
    assert len(_calls(book)) == before + 1


def test_a_bill_that_cannot_be_written_takes_the_finished_draft_down_with_it(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """记账不吞异常 ⇒ **一稿已经写完、钱也付了，作者拿到的是 500。**

    这是一个取舍不是一个 bug（`record_receipt` 的 docstring 写着理由：一条静默失败的
    记账就是日志页第二次骗人）。**没有测试的取舍会被下一个人当成意外修掉**，
    所以把它钉在这儿：要改方向，先改这条。
    """
    import novel_harness.extract.call_audit as call_audit

    _answers(monkeypatch, "字" * 2_400)
    monkeypatch.setattr(
        call_audit,
        "record_receipt",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    with pytest.raises(sqlite3.OperationalError):
        client.post(
            f"/api/projects/{book['pid']}/chapters/2/draft",
            json={"previous_tail": DRAFT_TAIL, "length": ZH_LENGTH},
        )
    assert _calls(book) == [], "账没记上，却还是留下了一行"


def test_the_ledger_itself_never_swallows_its_own_failure(book: dict[str, str]) -> None:
    """上一条量的是**路由**不吞；这一条量的是 `record_receipt` **自己**不吞。

    两半都要：上一条把 `record_receipt` 整个换掉了，所以它看不见那个函数内部改成
    `except: pass` 的那一天——而那一天日志页会开始第二次骗人（有调用、没有行、
    没有任何东西说过）。**突变探针实测过这条缝**：只有上一条时，把
    `record_receipt` 的 `raise` 换成 `return` 那一网全绿。
    """
    from novel_harness.extract.call_audit import ModelCallReceipt, record_receipt

    receipt = ModelCallReceipt(
        capability="writer",
        schema_version="nh.product_draft.v1",
        model="deepseek-v4-flash",
        finish_reason="stop",
        prompt_hash="ph",
        prompt_bytes=b"x",
        text="一稿",
        prompt_tokens=11,
        completion_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        elapsed_ms=7,
    )
    conn = connect(book["db"])
    try:
        # 同一个 id 落两次 ⇒ 第二次撞主键。这是「记账这一步真的失败了」最省事的复现。
        record_receipt(
            conn, receipt, project_id=book["pid"], chapter_number=2,
            call_id_factory=lambda _pid: "call:same",
        )
        with pytest.raises(sqlite3.Error):
            record_receipt(
                conn, receipt, project_id=book["pid"], chapter_number=2,
                call_id_factory=lambda _pid: "call:same",
            )
        assert not conn.in_transaction, "失败之后事务还开着 —— 下一次记账会当场撞 BEGIN"
        assert [row["id"] for row in _calls(book)] == ["call:same"]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 三、备份：它得是一份**真能用的库**
# ══════════════════════════════════════════════════════════════════════════


def test_the_backup_is_a_whole_database_not_a_file_of_the_right_size(tmp_path: Path) -> None:
    """大小对、打得开、其实少了半张表 —— 那比没有备份更糟，因为它会被信。

    所以逐项验：`integrity_check` / 外键 / **逐表行数与升级后的活库相等** /
    版本停在升级之前。
    """
    path, conn = _book_at(tmp_path / "书", projects=7)
    try:
        live_before = _table_counts(path)
        assert migrate(conn) > OLD_VERSION
    finally:
        conn.close()

    backup = _backups(tmp_path / "书")[0]
    frozen = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        assert frozen.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert frozen.execute("PRAGMA foreign_key_check").fetchall() == []
        assert int(frozen.execute("PRAGMA user_version").fetchone()[0]) == OLD_VERSION
    finally:
        frozen.close()
    # 迁移只加列不删行，所以两边逐表行数必须一样。少一张表 / 少一批行都会在这儿露出来。
    assert _table_counts(backup) == live_before
    assert _table_counts(backup)["project"] == 7


def test_the_backup_carries_rows_that_never_reached_the_main_file(tmp_path: Path) -> None:
    """WAL：刚提交、还没 checkpoint 的行只在 `-wal` 里。

    先证明陷阱是真的（**只拷主文件真的少了行**），再证明备份没少。
    朴素拷贝的两种长相都算「丢了」：读出来行数少，或者根本打不开（`_table_counts` 给 `None`）。
    """
    import shutil

    folder = tmp_path / "书"
    path, conn = _book_at(folder, projects=1)
    try:
        for i in range(400):
            conn.execute(
                "INSERT INTO project (id, name, root_path) VALUES (?, ?, ?)",
                (f"project:wal{i}", f"只在日志里的书{i}", f"/y/{i}"),
            )
        conn.commit()
        assert (folder / f"{path.name}-wal").stat().st_size > 0, "库不是 WAL —— 前提没了"
        naive = folder / "naive.copy"
        shutil.copyfile(path, naive)
        live = _table_counts(path)
        assert live is not None and live["project"] == 401
        migrate(conn)
    finally:
        conn.close()

    naive_counts = _table_counts(naive)
    # 朴素拷贝的三种长相：打不开 / 一张表都没有 / 表在但行少了。都算「丢了」。
    assert naive_counts is None or naive_counts.get("project", 0) < 401, (
        "只拷主文件居然一行都没少 —— WAL 里已经没有压着的东西了，这条在验空集"
    )
    assert _table_counts(_backups(folder)[0]) == live


def test_a_folder_that_cannot_be_written_leaves_the_schema_exactly_where_it_was(
    tmp_path: Path,
) -> None:
    """拷不成 ⇒ 拒绝迁移。**不只是版本号没动，DDL 也一条都没跑。**

    只断言 `user_version` 的话，一个「跑了 ALTER 但没写版本号」的半截迁移会照样绿，
    而那种库下一次启动会撞 `duplicate column name` 且没有恢复路径。
    """
    if os.geteuid() == 0:
        pytest.skip("root 无视目录权限")
    folder = tmp_path / "只读"
    path, conn = _book_at(folder)
    mode = folder.stat().st_mode
    folder.chmod(0o500)
    try:
        with pytest.raises(MigrationError):
            migrate(conn)
        assert user_version(conn) == OLD_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(model_call)")}
        assert "chapter_number" not in columns, "备份没拷成，DDL 却已经跑过了"
    finally:
        folder.chmod(mode)
        conn.close()
    assert _backups(folder) == []
    assert not list(folder.glob("*拷贝中*"))


def test_the_backup_never_lands_where_anything_goes_looking_for_a_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """备份是**书旁边**的一个 12MB 文件。它会不会被谁当成另一本书打开？

    今天不会，而理由必须是一条断言不是一句话：**全仓没有一处按 `*.db` 通配找库**。
    哪天有人写了（比如书架改成扫目录），备份会当场变成书架上的第二本书。
    """
    path, conn = _book_at(tmp_path / "书")
    try:
        migrate(conn)
    finally:
        conn.close()
    backup = _backups(tmp_path / "书")[0]

    from novel_harness.api import deps

    monkeypatch.setenv("NH_DB", str(path))
    monkeypatch.delenv("NH_BOOKS_DIR", raising=False)
    assert backup.parent == path.parent
    assert deps.books_root() not in backup.parents, "备份掉进了新书的稿子目录"

    assert not _db_globbers(sorted(SRC.rglob("*.py"))), (
        f"有人开始按 `*.db` 找库了，备份会被当成一本书："
        f"{_db_globbers(sorted(SRC.rglob('*.py')))}"
    )


def _db_globbers(paths: list[Path]) -> list[str]:
    """按 `*.db` 之类通配去找库的地方。**判据只有这一份**，上下两条测试共用。"""
    found: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") not in {"glob", "rglob"}:
                continue
            if any(
                isinstance(arg, ast.Constant) and str(arg.value).endswith(".db")
                for arg in node.args
            ):
                found.append(f"{path.name}:{node.lineno}")
    return found


def test_the_glob_scan_would_see_a_bookshelf_that_starts_scanning_for_databases(
    tmp_path: Path,
) -> None:
    """上一条的自守卫：真写一句扫库的代码，判据必须认出来。

    没有这一条的话，`_db_globbers` 哪天因为 AST 形状变了而一个都扫不到，
    上面那条会安静地全绿——而它守的恰好是「备份变成书架上的第二本书」。
    """
    probe = tmp_path / "shelf.py"
    probe.write_text(
        "def books(folder):\n"
        "    return sorted(folder.glob('*.db'))\n"
        "def more(folder):\n"
        "    return list(folder.rglob('*.md'))\n",
        encoding="utf-8",
    )
    assert [line.split(":")[1] for line in _db_globbers([probe])] == ["2"]


# ══════════════════════════════════════════════════════════════════════════
# 四、四个写入方：那张 AST 网罩得再密一点
# ══════════════════════════════════════════════════════════════════════════

RECEIPT_PRODUCERS_TODAY: frozenset[str] = frozenset(
    {"draft/product_draft.py", "agent/loop.py"}
)
"""今天**造** `ModelCallReceipt` 的地方。

一份回执 = 一次已经花掉的钱。造它的地方多一个而没人收，就是一笔账静默地不见了
——缓存那一刀抓到的正是「两个生产者能静默停止上报，而两千条测试一条不红」。
这张表只许在**同一次改动里连着收账那一头一起**变。
"""


def _ledger_sites(paths: list[Path]) -> list[tuple[str, int, str, bool]]:
    """每一处记账点：`(相对路径, 行号, 函数名, 说没说章号)`。

    **`ast.Name` 和 `ast.Attribute` 都要认。** 只认前者的话，一句
    `call_audit.record_call(...)` 就能新长出一个静默的记账点而这张网一声不响
    ——实测漏得掉（`tests/test_call_chapter.py::_ledger_call_sites` 今天就是那样）。
    """
    watched = {"record_call", "record_receipt"}
    found: list[tuple[str, int, str, bool]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name not in watched:
                continue
            said = any(kw.arg == "chapter_number" for kw in node.keywords)
            try:
                where = str(path.relative_to(SRC))
            except ValueError:
                where = path.name
            found.append((where, node.lineno, name, said))
    return found


def test_every_ledger_call_site_says_which_chapter_however_it_is_written() -> None:
    """欠账清单**只许缩不许长**，而且换个写法也躲不掉。

    清单本身（`SILENT_LEDGERS_TODAY`）从 `test_call_chapter.py` import ——
    两份欠账清单会各自过期，而过期的那份会先被人信。
    """
    sites = _ledger_sites(sorted(SRC.rglob("*.py")))
    assert sites, "一处记账点都没扫到 —— 这条守卫在空转"
    silent = {where for where, _line, _name, said in sites if not said}
    assert not silent - SILENT_LEDGERS_TODAY, (
        f"这些地方记了一笔账却没说是为哪一章：{sorted(silent - SILENT_LEDGERS_TODAY)}"
    )


def test_the_scan_sees_a_call_site_written_as_an_attribute(tmp_path: Path) -> None:
    """判据的自守卫：喂它一段**属性形态**的源码，它必须认出来。

    这不是假想的写法——`api/app.py` 就是在函数体里 import 之后再调的，
    改成 `from ..extract import call_audit` 再 `call_audit.record_receipt(...)`
    是同样自然的一笔。
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from .extract import call_audit\n"
        "call_audit.record_call(conn, project_id=pid)\n"
        "call_audit.record_receipt(conn, r, chapter_number=None)\n"
        "record_call(conn, project_id=pid)\n",
        encoding="utf-8",
    )
    assert [(name, said) for _p, _l, name, said in _ledger_sites([probe])] == [
        ("record_call", False),
        ("record_receipt", True),
        ("record_call", False),
    ]


STALE_DRAFT_CLAIMS_TODAY: frozenset[str] = frozenset(
    {"draft/product_draft.py", "agent/ports.py", "agent/loop.py"}
)
"""还写着「`/draft` 一行账都不写」的文件。**这是一份过期清单，不是一份豁免清单。**

2026-08-12 起那句话是假的（`api/app.py::draft` 走 `on_call=bill`）。留在清单里的三份
属于别的 agent / 无主文件，这一轮碰不得。**「已做完文档说没做」比反过来更贵**——
照它排期的人会去重写一件已经完成、且被测试罩着的工作，而这个仓库为这件事付过一次账
（CLAUDE.md 里那段「拷贝会骗人」的第二个受害者）。
"""

CLAIM_WORDS = ("一行 `model_call` 都不写", "一行 model_call 都不写", "一行账都不写")


def _stale_draft_claims(paths: list[Path]) -> list[str]:
    """还在说「`/draft` 不记账」的地方。**判据只有这一份**，上下两条测试共用。"""
    found: list[str] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "/draft" in line and any(word in line for word in CLAIM_WORDS):
                try:
                    found.append(str(path.relative_to(SRC)))
                except ValueError:
                    found.append(path.name)
    return found


def test_no_new_copy_of_the_claim_that_drafting_is_not_billed() -> None:
    """那句话今天是假的，而它在 `src/` 里躺着好几份拷贝。清单只许缩不许长。"""
    stale = set(_stale_draft_claims(sorted(SRC.rglob("*.py"))))
    assert not stale - STALE_DRAFT_CLAIMS_TODAY, (
        f"这些地方还写着「`/draft` 一行账都不写」，而它 2026-08-12 起是假的："
        f"{sorted(stale - STALE_DRAFT_CLAIMS_TODAY)}"
    )


def test_the_stale_claim_scan_can_actually_see_one(tmp_path: Path) -> None:
    """判据的自守卫：喂它一句真写过的原话，它必须认出来；旁边那句无关的不许咬。"""
    probe = tmp_path / "note.py"
    probe.write_text(
        '"""`/draft` 那条路径至今一行 `model_call` 都不写（api/app.py 里那段注释写着）。\n'
        "\n"
        "起草那条路今天一行账都记上了，所以这一句不该被咬。\n"
        '"""\n',
        encoding="utf-8",
    )
    assert _stale_draft_claims([probe]) == ["note.py"]


def test_the_receipt_producers_are_a_closed_set() -> None:
    """造回执的地方是封闭的。多一处 = 多一笔可能没人收的钱。"""
    produced = {
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ModelCallReceipt"
    }
    assert produced == RECEIPT_PRODUCERS_TODAY, (
        "造 `ModelCallReceipt` 的地方变了。**先确认它交给谁了**（哪一层调 "
        "`record_call` / `record_receipt`、章号由谁说），再改这张表。"
    )


# ══════════════════════════════════════════════════════════════════════════
# 五、起草那一行账在日志页上长什么样
# ══════════════════════════════════════════════════════════════════════════


def test_a_billed_draft_reads_like_chinese_on_the_activity_page(
    client: TestClient, book: dict[str, str], configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/draft` 落账之后，日志页上多出来的是**一种此前不存在的行**（capability=writer）。

    `test_wording_guard.py` 那条整页扫描只种 extractor / 失败 run / 确认三种样本，
    **这一种它一次都没扫过**——「样本里只躺着一种形状，等于守卫扫的是一块永远长一个样
    的屏幕」在这个仓库已经发生过一次（`_RUN_ERROR_LABEL` 那一节记着现场）。

    国际化第四批·笔二起，题目/副标题/跳转按钮/展开详情都是码 + 参数，不是拼好的
    中文——**渲染发生在前端**，这层 Python 测试量的是「结构对不对、参数里有没有
    夹带研发术语」，不是「屏幕上那句话是不是中文」。`capability=writer` 这个具体
    组合真的会渲染成什么样、渲染完干不干净，归 `DevTerms.guard.test.tsx` 管
    （同 Phase B 给 `SystemNotifications` 补的那道扫描）。
    """
    _answers(monkeypatch, "字" * 2_400)
    assert (
        client.post(
            f"/api/projects/{book['pid']}/chapters/2/draft",
            json={"previous_tail": DRAFT_TAIL, "length": ZH_LENGTH},
        ).status_code
        == 200
    )
    rows = _calls(book)
    assert [(row["capability"], row["chapter_number"]) for row in rows] == [("writer", 2)]

    base = f"/api/projects/{book['pid']}"
    page = client.get(f"{base}/activity", params={"limit": 50}).json()["entries"]
    entry = next(e for e in page if e["id"] == rows[0]["id"])
    assert entry["title_code"] == "call_entry_title"
    assert entry["title_params"] == {"capability": "writer"}
    assert entry["chapter_number"] == 2
    assert entry["jump"]["chapter_number"] == 2

    detail = client.get(f"{base}/activity/{rows[0]['id']}").json()
    values = detail_row_map(detail["rows"])
    assert values["detail_label_for_chapter"] == {
        "value_code": "value_chapter",
        "value_params": {"chapter": 2},
    }
    assert values["detail_label_capability"] == {
        "value_code": "value_capability",
        "value_params": {"capability": "writer"},
    }
    # 供应商报了多少就记多少；没报的那一半是「未记录」不是 0（§10 约束 8）。
    assert values["detail_label_tokens_in"] == {
        "value_code": "value_optional_number",
        "value_params": {"n": 1111},
    }
    assert values["detail_label_tokens_out"] == {
        "value_code": "value_optional_number",
        "value_params": {"n": None},
    }

    # 结构之外，量一遍**参数值本身**不是研发术语（渲染整句那半交给前端的
    # DevTerms.guard.test.tsx，这里只管后端不该往参数里塞什么）。
    param_values = [
        str(v)
        for source in (entry["title_params"], entry["subtitle_params"], entry["jump"]["label_params"])
        for v in source.values()
    ]
    for row in detail["rows"]:
        param_values.extend(str(v) for v in row["value_params"].values())
    offenders = {text: found for text in param_values if (found := dev_shapes(text))}
    assert not offenders, f"起草那一行的参数里混进了研发术语：{offenders}"
