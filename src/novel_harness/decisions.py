"""决策日志 —— append-only（PLAN §5.7 / ADR 0003）。

**这张表记的是全库唯一真正不可重建的资产：作者已经点过的每一次确认。**

其余一切都能重来：正文在磁盘上、图谱能重抽、索引能重建。作者点过的 3000 次确认不能——
没有这张日志，任何一次 schema 变更都要求他重新点一遍，**而那一刻就是项目结束的时刻**。
开源还有一层：v1→v2 的架构大改是必然的，每次大改都要求早期用户重审一遍图谱，
就会在 v2 发布当天流失掉全部早期用户，而这些人恰恰是唯一会写 issue 和 PR 的人。

── 本模块的 API 面上没有 update / delete，一个都没有 ─────────────────────
这不是「暂时还没写」。**`append()` 是唯一的写入口**；读有四个（`read` 重放序 /
`read_page` 日志页序 / `read_one` 取一条 / `tally_by_actor` 计数），**但一个都不改**。
`tests/test_decisions.py` 有一条测试在扫本模块的公开名字，看到修改类的入口就红。

读入口全在这里也是有意的：`decision_log` 的 SQL 只从本模块出去，别的模块（比如
`activity.py` 的日志读端）要它就调这几个函数，不自己写第二份 SELECT。

纪律会在某个赶时间的下午被绕过，所以同一条约束在三层上都成立：
  1. 本模块没有那个函数（API 层）
  2. `decision_log` 上的 **BEFORE INSERT / UPDATE / DELETE 三个**触发器 RAISE(ABORT)（引擎层）
  3. `decision_log` 一个外键都没有（连 project_id 都没有）——它必须比它记录的一切
     活得更久，指向 project 的外键会让「删掉项目」顺手删掉它

第 2 条曾经只有 UPDATE / DELETE 两个，而那**漏掉了三个写动词里的一个**：SQLite 默认
`PRAGMA recursive_triggers = 0`，此时 `INSERT OR REPLACE` 内部的隐式 DELETE 不触发
BEFORE DELETE 触发器——于是 REPLACE 大摇大摆走过去，把一条已落库的确认就地改写，
无痕、无报错、行数不变。**这段 docstring 当时正好在为一层实际不存在的防御作论证。**
（新加的那条走 BEFORE INSERT + `WHERE EXISTS`，对三个动词一视同仁，
且不依赖任何连接级 PRAGMA——`recursive_triggers` 是连接级的，靠它就又是一条纪律。）

── 锚是文本引语，不是 ID，也不是 offset ─────────────────────────────────
`(quote_text, quote_sha256, chapter_number, para_index)` 是这条记录里唯一在换存储、
改 edge schema、重跑抽取之后**还认得出来**的东西。ID 会随重抽全部作废，offset 会随
作者改稿全部错位——重放不回去的日志等于没有日志。

`subject_name` 存人名不存 node_id，是同一条理由（§5.7 原文就是「人名，不是 ID」）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from .db import Connection
from .ids import EntityType, new_id


class Verdict(StrEnum):
    """`decision_log.decision` 的取值。SQL 侧 CHECK 死了这三个。"""

    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    """作者改了系统的提议之后才接受。改成了什么在 `payload` 里——**这是三个里最有价值的
    一个**：它记的是系统猜错了、而作者亲手给出了正确答案。"""


class DecisionKind(StrEnum):
    """`kind` 的**约定**取值，不是强制。

    SQL 侧故意不 CHECK 它（§5.7 的原文就带着 `| ...`），`append()` 的 `kind` 参数也收
    任意 `str`：它不参与任何过滤分支，写错了只是日志难看，而 SQLite 改 CHECK 要重建
    整张表。这条取舍线和 `EdgeSource` / `validation_report.issue_type` 是同一条。
    """

    ALIAS_MERGE = "alias_merge"
    NODE_DECLARE = "node_declare"
    """建一个 Character / Location / Faction / Foreshadow / Object 节点。
    Secret 走 `SECRET_DECLARE`（§5.7 的原文单列了它）。"""

    SECRET_DECLARE = "secret_declare"
    KNOWS_DECLARE = "knows_declare"
    """KNOWS 和 BELIEVES 共用这一个：哪一条在 `payload["edge_type"]` 里。"""

    LOCATED_DECLARE = "located_declare"
    PROPOSAL_REVIEW = "proposal_review"

    KNOWLEDGE_EDIT = "knowledge_edit"
    """作者把一条**已经生效**的 KNOWS 改成了 BELIEVES（或反过来）。`corrections.py`。

    和 `KNOWS_DECLARE` 分开是因为重放时它们不是一回事：那边是「新增一条事实」，
    这边是「把已经写下的那条读错了」——payload 里有 `from` 和 `to` 两侧。
    """

    EVENT_EDIT = "event_edit"
    """作者改了一条已生效事件的知情 / 在场名单。`corrections.py`。"""

    CHAPTER_DRAFT = "chapter_draft"
    """写作助手起草完**直接把一稿写进了某一章**（[ADR 0021](../docs/adr/0021-agent-writes-drafts-without-asking.md)）。

    它和别的 kind 有一处根本不同：**这一行不是「往图里放了一条事实」，是「改了作者的
    正文」**。放进这张表的唯一理由是 ADR 0021 承诺的那条退路——「不挡，但每步留痕」，
    而这条时间线是作者唯一看得见「系统动过什么」的地方（`activity.py`），
    `actor` 那一列把它和作者自己按的保存分得开。

    **重放不看它**：正文的真相源在磁盘上（ADR 0007），退回上一版走的是
    `chapter_snapshot`，不是重放这条日志。所以 payload 里只有坐标（章号 / 字数 /
    落盘那一版的 `text_sha256`），没有正文本身。
    """


DEFAULT_ACTOR: Final = "author"

SYSTEM_ACTOR: Final = "system"
"""引擎自己做的改动（没经作者的手）。

2026-08-10 作者推翻「事前逐条确认」之后，抽取的干净结果直接升 CANON，日志里那一行没有
任何人点过。**它和作者亲手点的必须在日志里长得不一样**——否则「事后可查」查出来的是一份
分不清谁改的历史，而作者要跳去改的恰恰是系统改错的那几条。
"""


def quote_hash(quote_text: str) -> str:
    """引语的 sha256（UTF-8 原始字节，**不做任何归一化**）。

    不归一化是硬要求：`evidence.quote_sha256` 算的是「在正文里定位成功后的**原文子串**」
    （ADR 0006 配套第 3 条）。这边只要 NFC 一下，两边的哈希就永远对不上，而 decision_log
    的全部价值就是重放时能对上号。

    **别在别处再实现一遍这个函数**（text/anchor.py 请 import 它）——两份实现里只要有
    一份哪天加了 `.strip()`，历史日志就在那一刻静默失锚。
    """
    return hashlib.sha256(quote_text.encode("utf-8")).hexdigest()


class Decision(BaseModel):
    """一条已经落库的确认。`frozen=True` 是这张表的语义在类型层的复读。"""

    model_config = ConfigDict(frozen=True)

    id: str
    ts: str
    """ISO 8601 / UTC / 毫秒 / 'Z'，由 SQL 的 DEFAULT 写。

    **Python 侧永远不传它**：`datetime.now(UTC).isoformat()` 出的是 `+00:00` 后缀，
    与全库的 'Z' 混存会把同秒记录的字典序排错——而 `read()` 正是按 ts 排的。
    """

    project_id: str
    kind: str
    decision: Verdict
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_name: str | None = None
    quote_text: str | None = None
    quote_sha256: str | None = None
    chapter_number: int | None = None
    para_index: int | None = None
    """0-based（全库统一，见 ADR 0006）。"""

    actor: str = DEFAULT_ACTOR


def append(
    conn: Connection,
    *,
    project_id: str,
    kind: str,
    decision: Verdict,
    payload: Mapping[str, Any] | None = None,
    subject_name: str | None = None,
    quote_text: str | None = None,
    chapter_number: int | None = None,
    para_index: int | None = None,
    actor: str = DEFAULT_ACTOR,
) -> Decision:
    """记一条确认。**本模块唯一的写入口。**

    没有 `quote_sha256` 参数是故意的：它由 `quote_text` 现算（见 `quote_hash`）。
    开着这个口子，迟早会有调用方把**抽取器返回的那个字符串**的哈希传进来，而那个字符串
    有 10–30% 不是逐字原文——锚从写下去的那一刻起就是坏的，且没有任何东西会报错。
    调用方该传的 `quote_text` 是「作者眼睛看着点确认的那段**原文**」。

    没有 `ts` 参数同理：时间戳形状由 SQL 的 DEFAULT 统一（见 `Decision.ts`）。
    """
    if not project_id:
        raise ValueError("project_id 不能为空：决策日志按 project_id 重放，认不出主的记录等于丢了")
    if not kind:
        raise ValueError("kind 不能为空：重放时靠它分派到对应的重建逻辑")

    row = conn.execute(
        """
        INSERT INTO decision_log
            (id, project_id, kind, subject_name, quote_text, quote_sha256,
             chapter_number, para_index, payload_json, decision, actor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, ts, project_id, kind, subject_name, quote_text, quote_sha256,
                  chapter_number, para_index, payload_json, decision, actor
        """,
        (
            new_id(EntityType.DECISION, project_id),
            project_id,
            str(kind),
            subject_name,
            quote_text,
            None if quote_text is None else quote_hash(quote_text),
            chapter_number,
            para_index,
            json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True),
            Verdict(decision).value,
            actor,
        ),
    ).fetchone()
    conn.commit()
    return _row_to_decision(row)


def read(
    conn: Connection,
    project_id: str,
    *,
    kind: str | None = None,
    limit: int | None = None,
) -> list[Decision]:
    """按写入顺序读回一个项目的确认历史——**重放用的就是这个顺序**。

    ORDER BY (ts, id)：ts 是毫秒精度，同毫秒的两条靠 ULID 的时间前缀 + 单调性断平
    （ADR 0003 选 ULID 而非 UUIDv4 的理由之一就是这个）。顺序错了，「先合并别名再声明
    KNOWS」会重放成「先声明 KNOWS 再合并别名」——后者指向一个还不存在的节点。
    """
    sql = "SELECT * FROM decision_log WHERE project_id = ?"
    args: list[Any] = [project_id]
    if kind is not None:
        # v1/v2 allowed a UTF-8 BLOB in this TEXT column.  Treat that legacy storage
        # representation as the same discriminator so duplicate proposal history cannot hide
        # from filtered recovery reads.  Migration 003 rejects every future non-TEXT kind.
        sql += (
            " AND ((typeof(kind) = 'text' AND kind = ?)"
            " OR (typeof(kind) = 'blob' AND CAST(kind AS TEXT) = ?))"
        )
        args.extend((kind, kind))
    sql += " ORDER BY ts ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    return [_row_to_decision(r) for r in conn.execute(sql, args).fetchall()]


def read_one(conn: Connection, project_id: str, decision_id: str) -> Decision | None:
    """按 id 取一条（日志页展开详情用）。不存在或不属于这个项目 → None。

    **`project_id` 是过滤条件不是装饰**：这张表没有任何外键（连 project_id 都没有，
    见模块 docstring），所以「这条确认是不是这本书的」只有这一个判据。
    """
    row = conn.execute(
        "SELECT * FROM decision_log WHERE project_id = ? AND id = ?",
        (project_id, decision_id),
    ).fetchone()
    return None if row is None else _row_to_decision(row)


def read_page(
    conn: Connection,
    project_id: str,
    *,
    actor: str | None = None,
    before_ts: str | None = None,
    before_id: str | None = None,
    limit: int,
) -> list[Decision]:
    """**新的在前**的一页，可按 actor 过滤（日志页用）。

    ── 为什么不给 `read()` 加一个 `newest_first` 开关 ────────────────────
    `read()` 的 ASC 是**重放序**，顺序错了「先合并别名再声明 KNOWS」会重放成
    「先声明 KNOWS 再合并别名」——后者指向一个还不存在的节点。一个能被调用方翻转的
    开关迟早会让某次重放按倒序跑，而那种错**不报错**，只是重建出一张错的图。
    两个函数、两个方向、各自的 docstring，翻不错。

    `actor` 是等值过滤（那一列是开放字符串，同 `kind`）。`before_ts` / `before_id`
    是复合游标：ts 只有毫秒精度，同毫秒的两条靠 ULID 的单调前缀断平。
    """
    sql = "SELECT * FROM decision_log WHERE project_id = ?"
    args: list[Any] = [project_id]
    if actor is not None:
        sql += " AND actor = ?"
        args.append(actor)
    if before_ts is not None and before_id is not None:
        sql += " AND (ts < ? OR (ts = ? AND id < ?))"
        args.extend((before_ts, before_ts, before_id))
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [_row_to_decision(r) for r in conn.execute(sql, args).fetchall()]


def tally_by_actor(conn: Connection, project_id: str) -> dict[str, int]:
    """每个 actor 一共留了多少条确认。

    ADR 0020 点名要它：自动升 CANON 开了之后 system 行会长得快得多，作者自己点过的
    那几十次确认会被淹没——日志页要说得出「被藏起来的有多少」，光有过滤器不够。

    **这个 dict 不出接口**：`activity.py` 把它折进 `ActorTally` 才上 HTTP（铁律 4）。
    """
    rows = conn.execute(
        "SELECT actor, COUNT(*) AS n FROM decision_log WHERE project_id = ? GROUP BY actor",
        (project_id,),
    ).fetchall()
    return {str(row["actor"]): int(row["n"]) for row in rows}


def _row_to_decision(row: Any) -> Decision:
    # payload_json 在这里 json.loads 一次，此后无人再 loads——与 graph/models.py 的
    # 「dict / sqlite3.Row 禁止越过接口」是同一条规矩。
    return Decision(
        id=row["id"],
        ts=row["ts"],
        project_id=row["project_id"],
        kind=row["kind"],
        decision=Verdict(row["decision"]),
        payload=json.loads(row["payload_json"]),
        subject_name=row["subject_name"],
        quote_text=row["quote_text"],
        quote_sha256=row["quote_sha256"],
        chapter_number=row["chapter_number"],
        para_index=row["para_index"],
        actor=row["actor"],
    )
