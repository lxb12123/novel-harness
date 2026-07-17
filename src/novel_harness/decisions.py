"""决策日志 —— append-only（PLAN §5.7 / ADR 0003）。

**这张表记的是全库唯一真正不可重建的资产：作者已经点过的每一次确认。**

其余一切都能重来：正文在磁盘上、图谱能重抽、索引能重建。作者点过的 3000 次确认不能——
没有这张日志，任何一次 schema 变更都要求他重新点一遍，**而那一刻就是项目结束的时刻**。
开源还有一层：v1→v2 的架构大改是必然的，每次大改都要求早期用户重审一遍图谱，
就会在 v2 发布当天流失掉全部早期用户，而这些人恰恰是唯一会写 issue 和 PR 的人。

── 本模块的 API 面上没有 update / delete，一个都没有 ─────────────────────
这不是「暂时还没写」。**`append()` 是唯一的写入口**，读只有 `read()`。
`tests/test_decisions.py` 有一条测试在扫本模块的公开名字，看到修改类的入口就红。

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
    SECRET_DECLARE = "secret_declare"
    KNOWS_DECLARE = "knows_declare"
    PROPOSAL_REVIEW = "proposal_review"


DEFAULT_ACTOR: Final = "author"


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
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY ts ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    return [_row_to_decision(r) for r in conn.execute(sql, args).fetchall()]


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
