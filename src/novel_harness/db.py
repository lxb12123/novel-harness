"""SQLite 连接与迁移（PLAN §8 Day 2）。

**本文件是全系统 `graph/` 之外唯一允许 import sqlite3 的地方**（`tests/test_arch_guard.py`
的守卫需要把它加进白名单：`db.py` + `graph/**`）。理由是它没得选——`connect()` 就是那个
import 的具身。为了不让这个例外扩散，它导出 `Connection` 别名：`decisions.py` 等需要
连接类型标注的模块 `from .db import Connection` 即可，不必自己 import sqlite3。

── connect() 的两条 PRAGMA 都不是可选的 ──────────────────────────────────
- `foreign_keys=ON`：SQLite **默认关闭**外键。不设它，001_init.sql 里的每一条
  REFERENCES 都只是注释——包括「edge.type 必须有互斥性语义」（§5.5 说这条要在第一条边
  写进库之前成立）和「evidence 被引用时不许删」。
- `journal_mode=WAL`：watchdog 的 debounce 写快照与面板的读会同时发生，rollback journal
  下读会被写阻塞。WAL 是持久属性，**库不是 WAL 时才设，且必须容忍它 BUSY**——见
  `_enable_wal()`。（这里曾经写着「每次设也无害」，那半句是错的，代价见那个函数。）

── migrate() 的幂等由 user_version 提供，不由 DDL 提供 ────────────────────
001_init.sql 里没有一个 IF NOT EXISTS，**重跑必报错，这是故意的**（见那个文件的头注释：
迁移文件里的 IF NOT EXISTS 会把「跑错了迁移」变成静默通过）。所以「连跑两次 migrate
不报错」这条验收由本文件的闸门满足：版本已到就一条语句都不跑。
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import suppress
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Final

from .json_contract import canonical_json_text, strict_sql_text

Connection = sqlite3.Connection
"""给 `graph/` 之外的模块用的连接类型别名——见模块 docstring 里的架构守卫说明。"""

IN_MEMORY: Final = ":memory:"

BUSY_TIMEOUT_MS: Final = 5_000
"""WAL 下写者仍然互斥。watchdog 的快照写 + API 的 canon 提交撞上时，默认 0 超时会
当场抛 `database is locked`；给 5 秒让它排队。"""

_MIGRATIONS_ANCHOR: Final = "novel_harness"
_MIGRATIONS_DIR: Final = "migrations"

_MIGRATION_NAME_RE: Final = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
"""迁移文件名的形状。不符合的 `.sql` 文件**报错，不是忽略**：一个因为手滑叫成
`001_init.sql.bak` 或 `init.sql` 的迁移被静默跳过，产出的是一个缺表的库。"""


class MigrationError(RuntimeError):
    """迁移目录本身不合法、库的版本比代码新，或升级前那份备份拷不成。"""


BACKUP_LABEL: Final = "升级前备份"
"""备份文件名里那句人话。**作者会在自己的文件夹里看见这个文件**，所以名字要自己解释自己。

完整形状：`book.db.升级前备份-2026-08-12-第7版.db`

- 前缀是**那本书的库的全名**（`book.db.…`）⇒ 一眼看出它属于哪本书，排序也挨着它；
- 中间是**哪一天** + **从第几版升上来之前**的那一份；
- 结尾仍是 `.db` ⇒ 真要回退时，改个名字就能用，不必懂任何工具。

**它和正在用的那个不会认错**：正在用的那个的名字**恰好**是 `book.db`
（`start.command` 写死这一个名字，`nh serve --db` 是显式路径，全仓没有一处
按 `*.db` 通配找库）——所以这份备份永远不会被当成书打开，作者也不会为了「清掉多余的库」
而删错那一个。
"""


def _sha256_text(raw: object) -> str | None:
    """Bridge SQLite audit checks to the one authoritative quote hash implementation."""
    decoded = strict_sql_text(raw)
    if decoded is None:
        return None
    try:
        # Lazy import avoids the decisions -> db module dependency becoming a cycle.
        from .decisions import quote_hash

        return quote_hash(decoded)
    except UnicodeError:
        return None


def _utf8_text(raw: object) -> int:
    return int(strict_sql_text(raw) is not None)


def connect(path: str | Path, *, check_same_thread: bool = True) -> Connection:
    """打开一个库并设好 PRAGMA/SQL 函数。**所有连接都必须从这里出来**，别自己连接。

    传 `IN_MEMORY`（":memory:"）拿一个临时库；内存库不支持 WAL，会静默停在 "memory"
    模式，这没关系（没有并发读者）。注册的 `nh_*` 函数是 proposal audit 触发器的
    Python/SQLite 表示桥；绕开本函数的写连接会因缺少它们而 fail closed。
    """
    target = str(path)
    if target != IN_MEMORY:
        Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)
        target = str(Path(target).expanduser())

    conn = sqlite3.connect(target, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.create_function("nh_json_canonical", 1, canonical_json_text, deterministic=True)
    conn.create_function("nh_sha256_text", 1, _sha256_text, deterministic=True)
    conn.create_function("nh_utf8_text", 1, _utf8_text, deterministic=True)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if target != IN_MEMORY:
        _enable_wal(conn)
    # 必须在任何事务之外设置，且**每条连接都要设**：它是连接级的，不是库级的。
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _enable_wal(conn: Connection) -> None:
    """把库切成 WAL —— **先读，不是 WAL 才写，且写失败不致命。**

    三条，每条都是实测出来的，别顺手简化回 `conn.execute("PRAGMA journal_mode = WAL")`：

    1. **切换 WAL 需要独占锁，而这条 PRAGMA 不走 busy handler。** 实测：写者持着
       BEGIN IMMEDIATE 时，它 0.00s 就抛 `database is locked`，而同一条连接上的普通写
       老老实实等满 `BUSY_TIMEOUT_MS`。也就是说它恰好**绕开**了上面那行 busy_timeout
       想提供的保护——两个进程同时打开一个**全新**的库，其中一个会崩在 connect() 里，
       崩在 API server 和 watchdog 各自 connect() 的那一刻。而首次运行正是
       `uvx novel-harness` 的第一印象，也正是 §8 M0 的验收（「一个陌生人在另一台机器上
       能跑起来」）。
    2. **库一旦已经是 WAL，这条 PRAGMA 是 no-op**（实测：此时哪怕有写者持着
       BEGIN IMMEDIATE，它也 0.00s 返回 'wal'）。所以先读一次就能把上面那个窗口
       缩到只剩「库还不是 WAL」的那一瞬——即只剩首次运行。
    3. **BUSY 不致命**：能撞上它，说明另一个进程正在建这个库；他会把 WAL 设好，
       我们下次连上就是 WAL。此刻唯一正确的动作是继续（migrate() 的闸门会排队），
       不是让整个进程死在一句性能优化上。

    内存库不支持 WAL（会静默停在 "memory"），调用方已按路径挡在外面。
    """
    row = conn.execute("PRAGMA journal_mode").fetchone()
    if row is not None and str(row[0]).lower() == "wal":
        return
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass


def user_version(conn: Connection) -> int:
    """当前 schema 版本。0 = 空库。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def backup_path(db_file: Path, *, from_version: int, today: date | None = None) -> Path:
    """升级前那份备份该叫什么、放哪儿。**和库同一个目录**（见 `BACKUP_LABEL`）。

    同一个目录是有意的：跨盘拷贝会在磁盘满或权限不对时**部分成功**，而作者也不会
    去别处找他的备份。代价是它占的空间和库一样大——12MB 一份，可以忽略。
    """
    stamp = (today or date.today()).isoformat()
    return db_file.with_name(f"{db_file.name}.{BACKUP_LABEL}-{stamp}-第{from_version}版.db")


def _main_db_file(conn: Connection) -> Path | None:
    """这条连接背后的那个文件。内存库 / 临时库返回 `None`（没有东西可丢，也没处可放）。"""
    for row in conn.execute("PRAGMA database_list"):
        if str(row[1]) != "main":
            continue
        target = str(row[2] or "")
        return Path(target) if target else None
    return None


def backup_before_migration(conn: Connection, *, from_version: int) -> Path | None:
    """迁移动手之前，给这个库拷一份完整的副本。返回备份路径；内存库返回 `None`。

    ── 为什么这件事必须由机器做 ──────────────────────────────────────────
    「写迁移之前先把 dev server 停掉」这条**规矩失败过两次**（005 / 008 两次都真的把
    作者那本 722 章的书自动升了级，没有备份也没有确认）。第三次不该再靠记性。
    它同时是给真作者的保险：一次坏迁移落在那本书上不可恢复，而作者不会手动备份。

    ── ⚠️ 为什么不是 `shutil.copyfile` ──────────────────────────────────
    库是 WAL 模式（作者目录里真的躺着 `book.db-wal`）。**刚提交、还没 checkpoint 的
    那些事务只在 `-wal` 里**，只拷主文件会得到一份少了最近改动、甚至根本打不开的备份
    ——而它看起来一切正常，等到真要用的那天才发现。所以走 SQLite 自己的通路
    （`VACUUM INTO`：它是引擎按当前快照读出来的一份完整库，WAL 里那部分自然在内）。
    `tests/test_migration_backup.py` 用「先写未 checkpoint 的行再备份」钉住这一条，
    并且顺手证明**朴素拷贝真的会丢**（不证明陷阱是真的，守卫就只是句口号）。

    ── 先写临时名、再原子改名 ────────────────────────────────────────────
    磁盘写满时 `VACUUM INTO` 可能留下一个**半截**的文件。它要是直接顶着最终的名字，
    下一次启动会看见「今天这一版已经备份过了」而跳过——一份残缺的备份冒充好的，
    比没有备份更糟。改名是原子的，所以最终那个名字只可能是拷完了的。

    ── 拷不成 = 拒绝迁移（不是「照迁但吵一声」）──────────────────────────
    吵一声会被无视（终端上那行黄字，双击起服务的作者根本不会读），而这条路上
    唯一能救命的东西恰好就是这份备份。拒绝的代价小得多：**正文在磁盘上**（ADR 0007），
    书今天照样写；库里那些不可重建的东西（`decision_log`，§10 约束 7）反而因此没被动过。
    何况「拷不成」的两个原因（磁盘满 / 目录不可写）本来也会让迁移自己失败，
    只是失败得更晚、更难看。
    """
    db_file = _main_db_file(conn)
    if db_file is None:
        return None

    dest = backup_path(db_file, from_version=from_version)
    # 同一天、同一个版本上已经拷过一份了（迁移失败后重启就是这一档）。
    # **不覆盖**：那一份要么和现在这份一样，要么比现在这份更早——更早的更值钱。
    # 判据是 `is_file()` 不是 `exists()`：那个位置上要是坐着一个**目录**（或别的什么东西），
    # `exists()` 会把它读成「今天已经备份过了」而放行迁移——一次静默跳过。走下面这条路
    # 的话它会在改名那一步炸出来，也就是拒绝迁移。**实测过这个分支**（写这份代码时
    # 第一版就是 `exists()`，是测试把它挖出来的）。
    if dest.is_file():
        return dest

    if conn.in_transaction:
        # **VACUUM 不能在事务里跑**（`cannot VACUUM from within a transaction`），
        # 而 `migrate()` 收到一条**正开着写事务**的连接是既有形状（`tests/test_migrate.py`
        # 的 v1 那条就是先 INSERT 再 migrate）。这里替它提交**不是新增的副作用**：
        # 下面 `_apply` 的 `executescript` 本来就会先隐式 COMMIT 掉外面这个事务
        # （见那个函数的注释），我们只是把同一件事提前了一毫秒。
        # 反过来（另开一条连接去拷）会拿到一份**看不见这些行**的备份——
        # 它们随后就被迁移一起提交进库了，于是备份从落地那一刻起就是残的。
        conn.commit()

    staging = dest.with_name(f"{dest.name}.拷贝中-{os.getpid()}")
    try:
        # 上一次拷到一半就崩了的残留（同一个 pid 才可能撞上，那就是我们自己的）。
        staging.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(staging),))
        os.replace(staging, dest)
    except (sqlite3.Error, OSError) as exc:
        # 清理本身再失败也不许盖掉上面那个真正的原因（作者要看的是「磁盘满了」，
        # 不是「删不掉一个临时文件」）。
        with suppress(OSError):
            staging.unlink(missing_ok=True)
        raise MigrationError(
            f"要升级这本书的数据（第 {from_version} 版 → 更新的版本），"
            f"按规矩得先拷一份备份放在旁边，但没拷成：{exc}\n"
            f"  想拷成：{dest}\n"
            "  没有备份就不动你的书，所以这次升级停下了 —— 库里一个字都没改，"
            "章节文件也在原处。\n"
            "  多半是磁盘满了（备份和库一样大），或者这个文件夹不让写。"
            "腾出空间之后重新打开一次就好。"
        ) from exc
    return dest


def migrate(conn: Connection) -> int:
    """把库跑到最新版本，返回迁移后的版本号。**连跑两次不报错**（PLAN §8 Day 2 的验收）。

    幂等由 `PRAGMA user_version` 闸门提供，不由 DDL 提供（001_init.sql 里没有一个
    IF NOT EXISTS，那是故意的）。并发首跑的正确性见 `_apply`。

    ── 真的要改 schema 时（且只在那时）先备份 ────────────────────────────
    闸门放行 = 这一次真的会跑 DDL，那就是唯一需要保险的时刻。两头都不拷：
    **版本已经到位**（日常那千百次重启，包括开着 `--reload` 的 dev server）一份都不拷；
    **`current == 0`** 是一个刚建出来的空库，里面还没有任何东西可丢。
    钩子挂在这儿而不是 `api/deps.py::ensure_schema()`：出事的那两次确实走的是那条路，
    但作者自己走的是 `nh serve`/`nh init`（`cli.py` 也调 `migrate`）——
    只保护出过事的那一条，等于把作者留在外面。
    """
    migrations = _migrations()
    latest = migrations[-1][0] if migrations else 0
    current = user_version(conn)

    if current > latest:
        # 用 v1 的代码打开一个 v2 的库然后往里写 = 按老 schema 的假设改新数据。
        # 这个方向只能拒绝，不能「尽力而为」。
        raise MigrationError(
            f"库的 schema 版本（{current}）比本代码知道的最新版本（{latest}）新；"
            f"请升级 novel-harness，不要用旧版本写这个库"
        )

    if 0 < current < latest:
        # 两条都要：`< latest` = 这一次真的会跑 DDL（版本已到位的那千百次重启一份都不拷）；
        # `> 0` = 库里已经有东西可丢（刚建出来的空库不拷）。
        backup_before_migration(conn, from_version=current)

    for version, _name, sql in migrations:
        if version <= current:
            continue
        current = _apply(conn, version, sql)

    return current


def _apply(conn: Connection, version: int, sql: str) -> int:
    """跑一个迁移文件，返回跑完之后库的版本。

    ── 一个事务 ──────────────────────────────────────────────────────────
    DDL 在 SQLite 里是事务性的，而 `executescript` 自己不开事务（它还会先隐式 COMMIT
    掉外面的事务，所以 BEGIN 只能写在脚本里面）。不包起来的话，一个跑到一半失败的 001
    会留下「建了 6 张表、user_version 仍是 0」的库，下一次 migrate 撞 `table project
    already exists` 而永远起不来，且没有恢复路径（DDL 故意没有 IF NOT EXISTS）。

    ── `PRAGMA user_version` 必须和 DDL 在同一个事务里 ─────────────────────
    001_init.sql 自己末尾也写了那一行，但闸门的正确性不能建立在「每个迁移作者都记得
    写它」上。而把它挪到脚本外面单独一个事务写（先 COMMIT DDL、再 PRAGMA）会开一个
    窗口：并发的第二个进程在这个窗口里看到「表已经建好、版本还是 0」——它既跑不了 DDL，
    也不认为有人跑过。所以拼进脚本，原子落地。
    （PRAGMA **不接受占位符**，只能拼字符串，所以先过 int()。）

    ── BEGIN IMMEDIATE，不是裸 BEGIN ──────────────────────────────────────
    裸 BEGIN 是 deferred：读快照在第一条语句时才取。将来某个迁移若「先读后写」，
    那次写会撞 SQLITE_BUSY_SNAPSHOT，而**它不走 busy handler**——那是一个只在并发下
    出现、且 busy_timeout 治不了的失败。IMMEDIATE 在 BEGIN 时就拿写锁，于是并发的
    第二个进程是**排队**（走 busy_timeout），不是跑到一半才发现。

    ── 输的那个进程 ──────────────────────────────────────────────────────
    排队本身**治不好**首跑竞态：两个进程都在事务外读到 user_version=0，然后先后进 DDL，
    后者必然撞 `table project already exists`。实测（6 进程用 barrier 对齐）6 个里挂 5 个。
    此时正确的判断是：我们的整个事务已经回滚（库是赢家建好的、完整的），而版本真的
    推进到了目标——**这不是错误，是我们迟到了。**
    判据用「版本推进了」而不是「异常长得像 already exists」：后者会把一个真写坏了的
    迁移一起放行，而那正是这个项目一路在砍的静默通过。
    """
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {int(version)};\nCOMMIT;")
    except Exception:
        conn.rollback()
        landed = user_version(conn)
        if landed >= version:
            return landed
        raise
    return version


def _migrations() -> list[tuple[int, str, str]]:
    """`(version, filename, sql)` 按版本升序。

    用 `importlib.resources` 而不是 `__file__` 拼路径：wheel 里没有源码树，
    `__file__` 相对路径这条在 `uvx novel-harness`（改 11 的唯一安装叙事）下就是坏的。
    从父包导航（`files("novel_harness") / "migrations"`）而不是
    `files("novel_harness.migrations")`——后者依赖 PEP 420 命名空间包解析（migrations/
    下没有 __init__.py），目录一空就崩。
    """
    root = files(_MIGRATIONS_ANCHOR) / _MIGRATIONS_DIR
    found: list[tuple[int, str, str]] = []
    for entry in root.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        m = _MIGRATION_NAME_RE.match(entry.name)
        if m is None:
            raise MigrationError(
                f"迁移文件名不合法：{entry.name}（必须形如 001_init.sql）。"
                f"名字不合法的迁移会被静默跳过，产出一个缺表的库——所以这里报错"
            )
        found.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))

    found.sort(key=lambda item: item[0])
    versions = [v for v, _, _ in found]
    # 版本号必须是 1..N 连着的。断号（001, 003）意味着有个迁移文件没进 wheel 或被删了，
    # 而此时 003 会在一个缺 002 那些表的库上跑起来——那是静默的数据损坏，不是缺文件。
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"迁移版本号必须从 1 连续递增，实得：{versions}")
    return found
