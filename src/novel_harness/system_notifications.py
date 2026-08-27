"""统一系统通知的去重 / 忽略 / 解决 / 动作坐标（ADR 0030 / 计划 Task 10）。

六种语义各自独立的失败形态共用一张通知表：

- `summary_mismatch`：总结核对发现可能冲突（只告警，不撤销/不用不了/不改 Canon）；
- `background_failure`：后台任务失败（provider 崩溃、重放过不去…）；
- `validation_blocked`：正文验证器阻断（保留了旧结果，只是新快照不自动总结/抽取）；
- `text_advisory`：保存之后的语义核对发现问题（026）。**只告警，两支照常跑。**
- `extraction_yielded_nothing`：这一章整理完了，但一件都没留下（027）。**不是失败**
  ——模型答了、我们也处理完了，产出为零。它和 `background_failure` 的差别是这一条。
- `import_toc_skipped`：导入时丢掉了目录页复制出来的假章（032，
  `text/chapterize.py::drop_toc_duplicates()`）。带一个「撤销」动作——
  `payload_json` 存着撤销要用的数据（丢了哪些 + 这本书当时的指纹），
  **只有这一档用这一列**，读它请走 `notification_payload()`，别直接查 SQL。

── 阻断与不阻断是两件事，别按「听起来像不像坏消息」分 ────────────────────
`validation_blocked` 那句「新正文不会再自动生成总结与情节」是真的：停下游的是
`chapter_refresh` 的 gate，通知只是那件事的回执。而事后语义核对（秘密有没有对
不该知道的人说破、这一段跟后面章节抵不抵触）按 ADR 0030 的窄例外只许告警。
**挂错档的后果是作者改一个老章就把那一章的自动整理停掉，而那不会有任何东西报错**
——只会表现成「总结怎么一直不更新」。`BLOCKING_KINDS` 是这条区分唯一的落笔处。

── 定位跟着通知走（M1-c）──────────────────────────────────────────────────
规则产出的 `Issue` 本来就带着精确的锚 `(para_index, quote_text, occurrence_k)`
（ADR 0006，**禁止 offset**）。以前那个锚停在报告里，通知只说「第 N 章的正文检查
发现需要留意的地方」，作者点不过去。现在 `enqueue_validation_blocked` 把第一条
Issue 的锚原样带上，标题也说出哪一段、哪条规则、哪一句。

── 去重纪律（不变量 10 / 29）──────────────────────────────────────────────
`dedupe_key` 由 kind + subject + summary hash + source hash 稳定计算，非空，
`UNIQUE(project_id, dedupe_key)`。IGNORED / RESOLVED 对精确 hash 对是终态，
重复核对不得重开；只有任一 hash 变化才允许新 OPEN。`background_failure`
没有 hash 时用 `kind + subject + operation + snapshot/job id` 的稳定键——
**绝不包含会变的 exception 文本**。
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from .db import Connection
from .graph import TextAnchor
from .ids import EntityType, new_id
from .text import SkippedTocEntry

if TYPE_CHECKING:  # 只为标注：通知层不该在运行时拖上 checks 那一层
    from .checks.service import SnapshotValidationReport

__all__ = [
    "BLOCKING_KINDS",
    "SystemNotification",
    "background_failure_dedupe_key",
    "dedupe_key_for",
    "enqueue_extraction_yielded_nothing",
    "enqueue_import_toc_skipped",
    "enqueue_notification",
    "enqueue_text_advisory",
    "enqueue_validation_blocked",
    "ignore_notification",
    "list_open_notifications",
    "materialize_notification_outbox",
    "notification_count",
    "notification_payload",
    "resolve_notification",
    "resolve_stale_chapter_advisories",
]

NotificationStatus = Literal["OPEN", "IGNORED", "RESOLVED"]
NotificationKind = Literal[
    "summary_mismatch",
    "background_failure",
    "validation_blocked",
    "text_advisory",
    "extraction_yielded_nothing",
    "import_toc_skipped",
]

BLOCKING_KINDS: Final[frozenset[str]] = frozenset({"validation_blocked"})
"""哪几种通知落下的同时意味着**这一章的下游被停掉了**。

这不是渲染用的分类，是一句关于副作用的断言：只有 `validation_blocked` 那一档，
总结与抽取两支真的不会跑。新长出来的「事后发现的问题」**默认属于不阻断那一侧**
——阻断那一侧要新增成员，必须同时改 `chapter_refresh` 的 gate，否则通知在撒谎。
"""


class SystemNotification(BaseModel):
    """一条通知的出参。`jump` 是结构化坐标，前端禁止从文案反推。

    `title_code`/`title_params` 才是措辞的源（国际化第四批 Phase B）：前端拿
    `title_code` 去 `backendMessages.ts` 按当前界面语言整句渲染，`title_params`
    是填模板的原始事实。**出参里没有 `title`**——数据库那一列今天写的是
    `title_code` 的原样回声（给维护者 debug 用），从来不是给作者看的话，
    出参不带它，省得前端误以为它能直接显示。

    `title_code` 是 `Optional`：迁移（033）之前创建的旧通知没有这一位（DB 里
    是 NULL），那些行永远回不到"重新渲染"这条路——同 `decision_log` 的
    「历史行不回填」——前端在 `title_code` 缺失时怎么办不是本模块的事，
    但**不许在这儿编一个假的 code 糊弄过去**。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    kind: NotificationKind
    status: NotificationStatus
    subject_type: str
    subject_id: str
    chapter_number: int | None = None
    title_code: str | None = None
    title_params: dict[str, Any] | None = None
    summary_sha256: str | None = None
    source_sha256: str | None = None
    jump: TextAnchor | None = None
    actions: tuple[str, ...] = ()
    created_at: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dedupe_key_for(
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    summary_sha256: str | None,
    source_sha256: str | None,
) -> str:
    """`summary_mismatch` 的精确 hash 对去重键（不变量 10）。"""
    # hash 对可能有一个为 None（摘要被撤回 / 来源快照 legacy）。用非空占位
    # 保证即使 None 也给出非空键 —— NULL 会放行重复行，而重复通知正是要防的。
    return _sha(
        "\x00".join(
            (kind, subject_type, subject_id, summary_sha256 or "-", source_sha256 or "-")
        )
    )


def background_failure_dedupe_key(
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    operation: str,
    source_snapshot_id: str | None,
    job_id: str | None,
) -> str:
    """`background_failure` 的无 hash 稳定键。**exception 文本不许进来。**"""
    return _sha(
        "\x00".join(
            (
                kind,
                subject_type,
                subject_id,
                operation,
                source_snapshot_id or "-",
                job_id or "-",
            )
        )
    )


def enqueue_notification(
    conn: Connection,
    *,
    project_id: str,
    kind: NotificationKind,
    subject_type: str,
    subject_id: str,
    chapter_number: int | None,
    title_code: str,
    title_params: dict[str, Any] | None,
    dedupe_key: str,
    summary_sha256: str | None = None,
    source_sha256: str | None = None,
    jump: TextAnchor | None = None,
    actions: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
) -> str:
    """把一条通知写进持久 outbox（**同一事务**由调用方提交，不变量 29）。

    调用方（validation gate / 核对器 / 重放 dispatcher）负责把 enqueue 和业务
    状态改在一个事务里；本函数不自己 BEGIN。返回通知 outbox id。

    `payload` 是给「现成列都不够用」那一档的逃生舱（今天只有 `import_toc_skipped`
    用），落库前原样序列化成 JSON；`None` 落 NULL，不是空对象 `{}`。

    ── `title` 列今天写的是 `title_code` 本身，不是渲染出来的句子（国际化第四批
    Phase B）────────────────────────────────────────────────────────────
    真正的措辞源是 `title_code` + `title_params_json`，前端拿它们去
    `frontend/src/backendMessages.ts` 按当前界面语言整句渲染。`title` 列继续写
    只是为了满足它的 NOT NULL、给维护者在库里 debug 时留一个能认的锚——**前端不
    读这一列**，别指望它是给作者看的话。`title_params` 里的每一个值都必须先过
    「对作者安全」这道判断（结构化数据 / 作者自己的原文 = 安全；异常的 str()、
    内部字段名 = 不安全，那种情况要么整个不送这个码，要么先把值收窄成安全的
    形状——不能假设"反正是参数就没事"，`backendMessages.ts` 顶部写着这条教训
    的来处）。
    """
    outbox_id = new_id(EntityType.SYSTEM_NOTIFICATION, project_id)
    conn.execute(
        """
        INSERT INTO system_notification_outbox (
            id, project_id, intent, kind, subject_type, subject_id, chapter_number,
            title, title_code, title_params_json, summary_sha256, source_sha256,
            jump_para_index, jump_quote_text, jump_occurrence_k, actions_json,
            payload_json, dedupe_key
        ) VALUES (?, ?, 'CREATE_OR_UPDATE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outbox_id,
            project_id,
            kind,
            subject_type,
            subject_id,
            chapter_number,
            title_code,
            title_code,
            _json_payload(title_params),
            summary_sha256,
            source_sha256,
            jump.para_index if jump else None,
            jump.quote_text if jump else None,
            jump.occurrence_k if jump else None,
            _json(actions),
            _json_payload(payload),
            dedupe_key,
        ),
    )
    return outbox_id


def enqueue_import_toc_skipped(
    conn: Connection,
    *,
    project_id: str,
    skipped: list[SkippedTocEntry],
    fingerprint: str,
) -> str:
    """导入时丢掉了目录页假章的那条通知（032）。带「撤销」动作。

    `dedupe_key` 只按 `project_id`（+kind+operation）——同一个项目正常只会触发
    一次（导入是一次性的），真出现第二次时按标准的 outbox `ON CONFLICT DO UPDATE`
    刷新标题/payload，不会落成两条打架的通知。

    **不再收 `language`**（国际化第四批 Phase B）：标题不在这儿渲染，`count`
    是纯计数，对作者安全，原样进 `title_params`，前端按当前界面语言渲染。
    """
    count = len(skipped)
    return enqueue_notification(
        conn,
        project_id=project_id,
        kind="import_toc_skipped",
        subject_type="project",
        subject_id=project_id,
        chapter_number=None,
        title_code="import_toc_skipped_title",
        title_params={"count": count},
        dedupe_key=background_failure_dedupe_key(
            kind="import_toc_skipped",
            subject_type="project",
            subject_id=project_id,
            operation="import",
            source_snapshot_id=None,
            job_id=None,
        ),
        actions=("undo_toc_skip",),
        payload={
            "skipped": [[entry.position, entry.raw_heading] for entry in skipped],
            "fingerprint": fingerprint,
        },
    )


def enqueue_extraction_yielded_nothing(
    conn: Connection,
    *,
    project_id: str,
    snapshot_id: str,
    chapter_number: int,
    title_code: str,
    title_params: dict[str, Any] | None,
) -> str:
    """这一章整理完了、但**一件都没留下**时的那条通知（027）。

    ── 它为什么不是 `background_failure`，也不是 `text_advisory` ──────────

    **不是失败**：run 的 `status` 就是 `SUCCEEDED`，模型答了、我们也处理完了，
    只是产出为零。叫它失败会让作者去查一个不存在的故障。

    **也不带锚**：`text_advisory` 的 `jump` 是必填且引语非空的（026 有意定的，
    说不出「在哪一句」的告警作者点不过去），而这一条说的是**整整一章**的产出。
    为了复用而编一个假锚，等于把 026 那条纪律从内部拆掉。

    ── 去重按快照，不按章 ───────────────────────────────────────────────

    `subject_id` 用 `snapshot_id`：同一版正文重跑几次抽取只提醒一次（幂等），
    而作者改了正文再跑，那是**新的一版**，值得再说一次——他可能正是为了修这件事
    才去改的。按章去重的话第二次就哑了。

    **不进 `BLOCKING_KINDS`**：它不停任何下游，两支该跑照跑。
    """
    return enqueue_notification(
        conn,
        project_id=project_id,
        kind="extraction_yielded_nothing",
        subject_type="chapter_snapshot",
        subject_id=snapshot_id,
        chapter_number=chapter_number,
        title_code=title_code,
        title_params=title_params,
        dedupe_key=background_failure_dedupe_key(
            kind="extraction_yielded_nothing",
            subject_type="chapter_snapshot",
            subject_id=snapshot_id,
            operation="extract",
            source_snapshot_id=snapshot_id,
            job_id=None,
        ),
        source_sha256=None,
    )


def enqueue_validation_blocked(
    conn: Connection,
    *,
    report: SnapshotValidationReport,
    attempt_id: str,
) -> str:
    """正文验证阻断的那条通知 —— **带着第一条 Issue 的锚**（M1-c）。

    不变量 29：调用方（`chapter_refresh` 的验证闸门）负责把这一次 enqueue 和
    `validation_state = BLOCKED` 放进同一个事务；本函数不自己 BEGIN。

    ── 为什么只带第一条 ─────────────────────────────────────────────────────
    通知一行只有一个锚位，而作者要的是「从哪儿开始看」，不是一份清单——清单在
    「检查本章」那一格里。所以标题带头一条的原话加上「还有几处」，`jump` 带头一条
    的锚。顺序是确定的：规则按目录序跑，命中按段序出。
    """
    if not report.issues:
        # gate=blocked 的定义就是「至少一条规则报了至少一条 issue」。真走到这儿说明
        # 报告和闸门对不上，宁可炸也不落一条点不过去的通知——那正是这一层要补的洞。
        # 内部不变量违反，不是作者能看见的路径：不配 title_code，就是一句原始异常。
        raise ValueError(f"验证报告 {report.id} 判了 blocked 却一条 issue 都没有")
    return enqueue_notification(
        conn,
        project_id=report.project_id,
        kind="validation_blocked",
        subject_type="chapter",
        subject_id=report.chapter_id,
        chapter_number=report.chapter_number,
        title_code="validation_blocked_title",
        title_params=_validation_blocked_title_params(report),
        dedupe_key=background_failure_dedupe_key(
            kind="validation_blocked",
            subject_type="chapter",
            subject_id=report.chapter_id,
            operation=f"validation:{report.id}",
            source_snapshot_id=report.source_snapshot_id,
            job_id=attempt_id,
        ),
        jump=report.issues[0].anchor,
    )


def enqueue_text_advisory(
    conn: Connection,
    *,
    project_id: str,
    chapter_id: str,
    chapter_number: int | None,
    title_code: str,
    title_params: dict[str, Any] | None,
    dedupe_key: str,
    jump: TextAnchor,
    source_sha256: str | None = None,
    actions: tuple[str, ...] = (),
) -> str:
    """落一条**只告警、不阻断**的正文通知（026 / ADR 0030 的窄例外）。

    保存之后用模型做的语义核对走这里：秘密有没有对不该知道的人说破、这一段跟后面
    章节已经写死的设定抵不抵触。它和 `validation_blocked` 只差一件事，而那一件是
    全部——**它不停下游**：这一章的总结与抽取照常跑。

    `jump` **是必填的**。这一类问题只有模型说得出「在哪一句」，而说不出位置的告警
    作者点不过去；空引语当场报错，不落一条点不动的通知。
    """
    if not jump.quote_text.strip():
        raise ValueError("只告警的正文通知必须带着能点过去的引语，空锚不许落库")
    return enqueue_notification(
        conn,
        project_id=project_id,
        kind="text_advisory",
        subject_type="chapter",
        subject_id=chapter_id,
        chapter_number=chapter_number,
        title_code=title_code,
        title_params=title_params,
        dedupe_key=dedupe_key,
        source_sha256=source_sha256,
        jump=jump,
        actions=actions,
    )


def resolve_stale_chapter_advisories(
    conn: Connection, *, project_id: str, chapter_id: str, current_sha256: str
) -> int:
    """把这一章**照着旧正文写的**那些 `text_advisory` 标 RESOLVED，返回条数。

    事后语义核对核完一版新正文之后调它：上一版那条告警指着的那句话，作者很可能刚刚
    就是改掉了它——一条锚落不到任何地方的通知，作者点过去只会落空，而它永远不会自己走。
    这和 `finalize_reconciliation_run` 里 `supported` 解决旧 OPEN 是同一个动作，
    只是这一边的主语是「这一章的正文」而不是「这一条总结」。

    **判据是来源正文的 hash，不是「这一章的全部」**：同一版正文上刚落的那几条
    （`source_sha256` 相等）必须留着，否则一次幂等重扫就会把自己刚报的问题抹掉。

    **只动 OPEN**：`IGNORED` 是作者自己按下的终态（不变量 10），系统不许替他重开，
    也不许把它悄悄改写成「已解决」——那两句话在日志上不是同一件事。

    **不自己 BEGIN**（不变量 29）：它必须和调用方那一批新告警的 enqueue 在同一个事务里，
    否则崩溃点落在两者之间时右栏会短暂地既没有旧的也没有新的。
    """
    changed = conn.execute(
        """
        UPDATE system_notification
           SET status = 'RESOLVED', resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
         WHERE project_id = ? AND kind = 'text_advisory' AND subject_type = 'chapter'
           AND subject_id = ? AND status = 'OPEN'
           AND (source_sha256 IS NULL OR source_sha256 <> ?)
        """,
        (project_id, chapter_id, current_sha256),
    )
    return int(changed.rowcount or 0)


def _validation_blocked_title_params(report: SnapshotValidationReport) -> dict[str, Any]:
    """`validation_blocked_title` 这个码要填的原始事实（国际化第四批 Phase B）。

    **不发拼好的句子，发段号 / 规则名 / 还有几处 / 命中原话这几个原始值**——
    整句怎么拼（"第几段·哪条规则：哪一句" + 还有几处 + 固定的副作用说明）是
    `frontend/src/backendMessages.ts` 里那个模板函数的活，中英文的语序、标点
    各自决定。段号按作者的数法从 1 起——`TextAnchor` 内部是 0-based，两种数法
    只在这一处换算。

    `rule_title`（规则自己的名字，如「设定提前出现」）和 `issue_message`
    （规则命中的原话）都来自 `checks/` 那一层——**这两个 param 对作者安全**：
    `rule_title` 就是 `_rule_title()` 特意避免印 `R2`/`R3` 编号、只用规则自己
    人话名字的产物；`issue_message` 是 `Issue.message`，Issue 本来就是设计给
    「检查本章」那个面板直接显示的（`DevTerms.guard.test.tsx` 的"检查"那一档
    今天就在扫它），不是新增的风险面——只是**不随界面语言翻**：两者都是
    `checks/` 那一层的规则输出，双语化是另一批的事，这儿原样透传。
    """
    first = report.issues[0]
    return {
        "paragraph": first.anchor.para_index + 1,
        "rule_title": _rule_title(report, first.rule),
        "rest": len(report.issues) - 1,
        "issue_message": first.message,
    }


def _rule_title(report: SnapshotValidationReport, rule: str) -> str:
    """Issue 上那条规则在报告里叫什么名字；对不上返回空串（**绝不退回编号**）。

    自定义规则的 Issue 写的是 `custom:{rule_id}`，执行记录里却是裸 `rule_id`
    （那个前缀 `checks/custom.py` 只加在 Issue 上）——不脱这一层，作者自己写的
    规则在通知里就永远没名字。
    """
    titles = {execution.rule_id: execution.title for execution in report.rules}
    return titles.get(rule) or titles.get(rule.removeprefix("custom:")) or ""


def materialize_notification_outbox(
    conn: Connection,
    *,
    project_id: str,
    lease_owner: str,
    ttl_seconds: float = 30.0,
) -> int:
    """把 PENDING / 过期 RUNNING 的通知 outbox 逐条物化成 `system_notification`。

    幂等：重复 claim 同一 outbox 不会刷出重复行（`UNIQUE(project_id, dedupe_key)` +
    CREATE_OR_UPDATE 的 ON CONFLICT DO NOTHING 语义）。返回处理的条数。

    重放时**标题和锚一起更新**：它们是同一句话的两半，只刷一半会让通知说的段号和
    点过去的位置对不上（今天的去重键都含内容 hash，所以同键必同锚，这是防将来）。
    """
    rows = conn.execute(
        """
        SELECT id, intent, kind, subject_type, subject_id, chapter_number, title,
               title_code, title_params_json,
               summary_sha256, source_sha256, jump_para_index, jump_quote_text,
               jump_occurrence_k, actions_json, payload_json, dedupe_key
          FROM system_notification_outbox
         WHERE project_id = ? AND status = 'PENDING'
         ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        if row["intent"] == "CREATE_OR_UPDATE":
            conn.execute(
                """
                INSERT INTO system_notification (
                    id, project_id, kind, status, subject_type, subject_id,
                    chapter_number, title, title_code, title_params_json,
                    summary_sha256, source_sha256,
                    jump_para_index, jump_quote_text, jump_occurrence_k,
                    actions_json, payload_json, dedupe_key
                ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, dedupe_key)
                DO UPDATE SET title = excluded.title,
                              title_code = excluded.title_code,
                              title_params_json = excluded.title_params_json,
                              jump_para_index = excluded.jump_para_index,
                              jump_quote_text = excluded.jump_quote_text,
                              jump_occurrence_k = excluded.jump_occurrence_k,
                              payload_json = excluded.payload_json
                """,
                (
                    new_id(EntityType.SYSTEM_NOTIFICATION, project_id),
                    project_id,
                    row["kind"],
                    row["subject_type"],
                    row["subject_id"],
                    row["chapter_number"],
                    row["title"],
                    row["title_code"],
                    row["title_params_json"],
                    row["summary_sha256"],
                    row["source_sha256"],
                    row["jump_para_index"],
                    row["jump_quote_text"],
                    row["jump_occurrence_k"],
                    row["actions_json"],
                    row["payload_json"],
                    row["dedupe_key"],
                ),
            )
        elif row["intent"] == "RESOLVE":
            conn.execute(
                """
                UPDATE system_notification SET status = 'RESOLVED',
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                 WHERE project_id = ? AND dedupe_key = ? AND status = 'OPEN'
                """,
                (project_id, row["dedupe_key"]),
            )
        conn.execute("UPDATE system_notification_outbox SET status = 'DONE' WHERE id = ?",
                     (row["id"],))
    conn.commit()
    return len(rows)


def _open_rows(conn: Connection, project_id: str) -> list[SystemNotification]:
    rows = conn.execute(
        """
        SELECT id, project_id, kind, status, subject_type, subject_id, chapter_number,
               title, title_code, title_params_json,
               summary_sha256, source_sha256, jump_para_index, jump_quote_text,
               jump_occurrence_k, actions_json, created_at
          FROM system_notification
         WHERE project_id = ? AND status = 'OPEN'
         ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    return [_to_notification(r) for r in rows]


def list_open_notifications(conn: Connection, project_id: str) -> list[SystemNotification]:
    return _open_rows(conn, project_id)


def notification_count(conn: Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM system_notification WHERE project_id = ? AND status = 'OPEN'",
        (project_id,),
    ).fetchone()
    return int(row[0])


def notification_payload(conn: Connection, notification_id: str) -> dict[str, Any] | None:
    """读一条通知的 `payload_json`（今天只有 `import_toc_skipped` 写过它）。

    **不进 `SystemNotification` 出参**：撤销按钮只需要点得动，不需要前端理解
    payload 长什么样——那是后端自己读自己写的内部数据，同 `RetirementReport`
    的 `exclude=True` 是同一条纪律（内部账不上线）。`None` 有两种含义都合法：
    这一档通知本来就没有 payload，或者通知不存在——调用方（撤销的路由）
    该按「找不到就 404」处理，不必区分。
    """
    row = conn.execute(
        "SELECT payload_json FROM system_notification WHERE id = ?", (notification_id,)
    ).fetchone()
    if row is None or row["payload_json"] is None:
        return None
    return _json_payload_un(row["payload_json"])


def ignore_notification(conn: Connection, notification_id: str) -> None:
    """把一条 OPEN 通知标 IGNORED（精确 hash 对的终态，不重开）。"""
    conn.execute(
        "UPDATE system_notification SET status = 'IGNORED', "
        "ignored_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ? AND status = 'OPEN'",
        (notification_id,),
    )
    conn.commit()


def resolve_notification(conn: Connection, notification_id: str) -> None:
    """手动 RESOLVE（同「问题消失自动解决」，只是这次是作者点的）。"""
    conn.execute(
        "UPDATE system_notification SET status = 'RESOLVED', "
        "resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ? AND status = 'OPEN'",
        (notification_id,),
    )
    conn.commit()


def _to_notification(row) -> SystemNotification:
    jump = (
        TextAnchor(
            para_index=row["jump_para_index"],
            quote_text=row["jump_quote_text"],
            occurrence_k=row["jump_occurrence_k"],
        )
        if row["jump_para_index"] is not None and row["jump_quote_text"] is not None
        else None
    )
    return SystemNotification(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        status=row["status"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        chapter_number=row["chapter_number"],
        title_code=row["title_code"],
        title_params=_json_payload_un(row["title_params_json"])
        if row["title_params_json"] is not None
        else None,
        summary_sha256=row["summary_sha256"],
        source_sha256=row["source_sha256"],
        jump=jump,
        actions=_json_un(row["actions_json"]),
        created_at=row["created_at"],
    )


def _json(actions: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(actions), ensure_ascii=False, separators=(",", ":"))


def _json_un(text: str) -> tuple[str, ...]:
    import json

    return tuple(json.loads(text))


def _json_payload(payload: dict[str, Any] | None) -> str | None:
    import json

    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_payload_un(text: str) -> dict[str, Any]:
    import json

    return json.loads(text)
