"""快照绑定的正文验证服务 —— 在**不可变快照**上跑规则并持久化报告（Task 4）。

三种「验证」的分工（计划 §1）：本章节结构预检在写盘前、正文验证器在这里、
总结核对在 Task 10。「总结可能与正文不一致」和「正文触发了 R2/R3」不共表、
不共 UI 文案，也不共闸门。

service 的纪律：

- 正文从 `ChapterCommitToken.text` 构造段落（`text.paragraphs`），**不再从磁盘另读
  一版**——token 是保存后所有自动任务的唯一正文输入（ADR 0029）。
- 先读 017 的持久 `validation_ruleset_state`，报告里的 epoch/hash 与该行**完全一致**；
  缺行 = 迁移/项目创建损坏，报错，不在检查时悄悄造一个临时 epoch。
- 先 availability 后 check：正文没装入是 UNAVAILABLE（不阻断）；人物/状态集合为空
  是合法空集合（照跑，显示「本次未报告问题」）；规则抛异常是 error（阻断）。
- 报告落 `validation_report`，带 snapshot/generation/attempt/phase/hash/
  ruleset epoch+hash——旧行（phase=legacy）无法诚实反推来源快照，允许 NULL。
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..db import Connection
from ..graph import ChapterCommitToken, StoryGraph
from ..ids import EntityType, new_id
from . import catalog
from .base import CheckContext, Issue
from .catalog import RuleAvailability, RuleSpec


class RuleExecution(BaseModel):
    """一条规则的逐条执行状态（§4.2）。

    `state` 与 `issue_count` 是两回事：`clear` 可以是 0 条（合法空集合），
    `unavailable` 也可以是 0 条——不能从 `issues == []` 反推资料是否齐全。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    title: str
    state: Literal["clear", "blocked", "unavailable", "error"]
    issue_count: int = Field(ge=0)
    blocks_downstream: bool
    note: str | None = None


class SnapshotValidationReport(BaseModel):
    """一次快照绑定的验证报告（§4.2 / §6.2 的 `POST …/check` 出参）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    chapter_id: str
    chapter_number: int = Field(ge=1)
    source_snapshot_id: str
    source_generation: int = Field(ge=1)
    refresh_attempt_id: str | None
    phase: Literal["initial", "post_alias", "legacy"]
    text_sha256: str
    ruleset_epoch: int = Field(ge=1)
    ruleset_hash: str
    gate: Literal["passed", "blocked", "error", "superseded"]
    rules: tuple[RuleExecution, ...]
    issues: tuple[Issue, ...]


class RulesetStateMissing(RuntimeError):
    """项目缺 `validation_ruleset_state` 行：迁移/项目创建损坏。

    不能在这里悄悄造一个 epoch=1——那等于让损坏的库「看起来正常」，而 Task 5 的
    attempt 会把一个编造的 ruleset 冻结进机器任务。
    """


def current_ruleset(conn: Connection, project_id: str) -> tuple[int, str]:
    """017 的持久 ruleset 基线 `(epoch, hash)`。缺行 → `RulesetStateMissing`。"""
    row = conn.execute(
        "SELECT epoch, ruleset_hash FROM validation_ruleset_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise RulesetStateMissing(f"项目 {project_id} 没有 validation_ruleset_state 行（迁移损坏）")
    return int(row["epoch"]), str(row["ruleset_hash"])


def load_custom_rules(conn: Connection, project_id: str) -> tuple[RuleSpec, ...]:
    """项目定义的自定义确定性规则（024 / Task 13）升格成 catalog 的 RuleSpec。

    只读 `enabled=1` 的；禁用规则照旧留在库里（历史可查），不参与运行。
    规则语义变化（增改删启停）由写侧在**同一事务**递增 `ruleset_epoch` 并重算 hash。
    """
    from .custom import custom_rule_spec

    rows = conn.execute(
        "SELECT id, title, template, enabled, blocks_downstream, config_json "
        "FROM validation_rule WHERE project_id = ? AND enabled = 1 "
        "ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    specs: list[RuleSpec] = []
    for row in rows:
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        literal = config.get("literal", "")
        if not literal or not isinstance(literal, str):
            continue
        specs.append(
            custom_rule_spec(
                rule_id=row["id"],
                title=row["title"] or row["id"],
                literal=literal,
                blocks_downstream=bool(row["blocks_downstream"]),
            )
        )
    return tuple(specs)


def ruleset_semantic_hash(conn: Connection, project_id: str) -> str:
    """当前 validation_ruleset_state 的**语义 hash 对账**：system + custom 一起算。

    写侧（规则 CRUD）用它在同一事务里重算并更新 state 行；报告侧只读冻结值。
    """
    from .catalog import ruleset_hash

    all_rules = (*catalog.SYSTEM_RULES, *load_custom_rules(conn, project_id))
    return ruleset_hash(all_rules)


def validate_snapshot(
    conn: Connection,
    store: StoryGraph,
    token: ChapterCommitToken,
    *,
    ruleset_epoch: int,
    ruleset_hash: str,
    phase: Literal["initial", "post_alias", "legacy"] = "initial",
    refresh_attempt_id: str | None = None,
    paragraphs: Sequence[str] | None = None,
) -> SnapshotValidationReport:
    """在 token 的不可变正文上跑规则目录，落一条报告，返回它。

    `gate` 的口径：任一规则 error → `error`（阻断）；任一规则 blocked（发现问题）
    → `blocked`（阻断）；只有 unavailable / clear → `passed`（技术性 unavailable
    不阻断抽取）。
    """
    ctx = CheckContext(
        store=store,
        project_id=token.project_id,
        chapter=token.chapter_number,
        # paragraphs=None = 正文输入没装入（技术性 unavailable，不阻断）；
        # 调用方负责把 token 正文切成段落传进来（`split_paragraphs(token.text)`）。
        paragraphs=paragraphs,
    )
    rule_execs: list[RuleExecution] = []
    issues: list[Issue] = []
    # 024 / Task 13：把作者的确定性自定义规则并进目录再跑，让手动检查与保存后的
    # 自动验证共用一个实现（不建第二套）。自定义规则永远排在系统规则之后。
    all_rules = (*catalog.SYSTEM_RULES, *load_custom_rules(conn, token.project_id))
    for spec in all_rules:
        state: Literal["clear", "blocked", "unavailable", "error"] = "unavailable"
        count = 0
        note: str | None = None
        if spec.availability is not None and spec.check is not None:
            if spec.availability(ctx) is RuleAvailability.AVAILABLE:
                try:
                    found = spec.check(ctx)
                except Exception as exc:  # noqa: BLE001 —— 规则崩溃要整条报告变 error
                    state = "error"
                    note = f"规则执行异常：{exc}"
                else:
                    count = len(found)
                    state = "blocked" if count else "clear"
                    issues.extend(found)
        rule_execs.append(
            RuleExecution(
                rule_id=spec.rule_id,
                title=spec.title,
                state=state,
                issue_count=count,
                blocks_downstream=spec.blocks_downstream,
                note=note,
            )
        )

    if any(r.state == "error" for r in rule_execs):
        gate: Literal["passed", "blocked", "error", "superseded"] = "error"
    elif any(r.state == "blocked" for r in rule_execs):
        gate = "blocked"
    else:
        gate = "passed"

    report = SnapshotValidationReport(
        id=new_id(EntityType.REPORT, token.project_id),
        project_id=token.project_id,
        chapter_id=token.chapter_id,
        chapter_number=token.chapter_number,
        source_snapshot_id=token.source_snapshot_id,
        source_generation=token.source_generation,
        refresh_attempt_id=refresh_attempt_id,
        phase=phase,
        text_sha256=token.text_sha256,
        ruleset_epoch=ruleset_epoch,
        ruleset_hash=ruleset_hash,
        gate=gate,
        rules=tuple(rule_execs),
        issues=tuple(issues),
    )
    conn.execute(
        """
        INSERT INTO validation_report (
            id, project_id, chapter_id, chapter_number, chapter_snapshot_id,
            source_generation, refresh_attempt_id, phase, text_sha256,
            ruleset_epoch, ruleset_hash, gate, rules_json,
            issue_count, issues_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        """,
        (
            report.id,
            token.project_id,
            token.chapter_id,
            token.chapter_number,
            token.source_snapshot_id,
            token.source_generation,
            refresh_attempt_id,
            phase,
            token.text_sha256,
            ruleset_epoch,
            ruleset_hash,
            gate,
            json.dumps([r.model_dump(mode="json") for r in report.rules], ensure_ascii=False),
            len(issues),
            json.dumps([i.model_dump(mode="json") for i in report.issues], ensure_ascii=False),
        ),
    )
    return report
