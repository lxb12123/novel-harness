"""规则目录 —— R2/R3 的稳定语义字段与 `ruleset_hash` 的唯一注册源。

为什么要有这一个文件：018 迁移在 `validation_ruleset_state` 里冻结了
`SYSTEM_RULESET_V1_HASH`，而 Task 4 的 service 要按当前目录重算 hash 去对账。
两边各算各的 = 两份实现会漂；目录只有一个，迁移 SQL 里的字面量由测试钉住
（`tests/test_migrate.py` 断言 SQL 里的历史字面量 == 本文件的常量）。

── 什么进 hash、什么不进 ───────────────────────────────────────────────

进：`rule_id / schema_version / enabled / blocks_downstream / template / config`。
这些字段变了，机器任务的结果语义就变了，必须让旧任务失效。

不进：标题、说明（UI 文案）。只改文案就让所有机器任务失效，是拿作者的
钱付给自己改错的字。

── 冻结纪律 ─────────────────────────────────────────────────────────────

`SYSTEM_RULES` 的语义字段以后要改（加规则 / 改阻断属性 / 换 schema），
必须用新迁移为所有项目递增 `ruleset_epoch` 并重算总 hash；**不许偷偷改
017 里冻结的那个字面量**（迁移测试会红）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

from .base import Check, CheckContext
from .dead_speaks import check as dead_speaks_check


class RuleAvailability(StrEnum):
    """一条规则的正文输入是否可用（Task 4 的 availability 判据）。

    只有「正文没装入 / snapshot 读不出」这类**技术性**缺失才是 UNAVAILABLE；
    人物、别名、未来实体或状态集合为空是**合法空集合**——规则照跑，显示
    「本次未报告问题」，不能伪称资料不全。
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """一条确定性规则在目录里的完整描述。

    `availability` / `check` 不进 ruleset hash（`_SEMANTIC_FIELDS` 只挑语义字段）——
    换实现不改语义，不该让所有机器任务失效。
    """

    rule_id: str
    title: str
    description: str
    blocks_downstream: bool
    enabled: bool = True
    schema_version: str = "v1"
    template: Literal["system", "forbidden_literal"] = "system"
    config: dict[str, Any] = field(default_factory=dict)
    availability: Callable[[CheckContext], RuleAvailability] | None = None
    check: Check | None = None


def _paragraphs_available(ctx: CheckContext) -> RuleAvailability:
    """R2/R3 的 availability：正文段落装入 = AVAILABLE（空集合是合法空集合）。"""
    return (
        RuleAvailability.AVAILABLE
        if ctx.paragraphs is not None
        else RuleAvailability.UNAVAILABLE
    )


SYSTEM_RULES: Final[tuple[RuleSpec, ...]] = (
    RuleSpec(
        rule_id="R3",
        title="人物开口时机",
        description="已死或尚未登场的角色在说话人标签位置开口。",
        blocks_downstream=True,
        availability=_paragraphs_available,
        check=dead_speaks_check,
    ),
)
"""系统默认规则。**Task 4 之前它只有语义字段**——availability/callable 是 Task 4
的活，且不得改变这里已冻结的语义 JSON。

R2 `FUTURE_LEAK` 2026-08-27 删了（见 ADR 0040、迁移 031）：它读
`node.props.first_appears_chapter`，而浏览器上从来没有入口能设那个字段
（输入框 2026-08-13 有意裁掉），规则结构上开不了火——「未登场的东西未来到底
哪一章出现，本来就不一定」，那是一次决定，不是一件事实。"""


_SEMANTIC_FIELDS: Final[tuple[str, ...]] = (
    "rule_id",
    "schema_version",
    "enabled",
    "blocks_downstream",
    "template",
    "config",
)


def ruleset_semantic_json(rules: tuple[RuleSpec, ...] = SYSTEM_RULES) -> str:
    """按 rule id 排序后的稳定 JSON（§4.2 的编码口径，sort_keys + 紧凑分隔符）。"""
    payload = [
        {name: getattr(rule, name) for name in _SEMANTIC_FIELDS}
        for rule in sorted(rules, key=lambda spec: spec.rule_id)
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ruleset_hash(rules: tuple[RuleSpec, ...] = SYSTEM_RULES) -> str:
    """一套规则的语义 hash。A→B→A 可以同 hash——epoch 由数据库单调递增来区分。"""
    return hashlib.sha256(ruleset_semantic_json(rules).encode("utf-8")).hexdigest()


SYSTEM_RULESET_V1_HASH: Final = "eebe10055a2487574ea5ae03f1fcd0ac14211301dff6f46ea136482d91b2b166"
"""R2/R3 catalog + 空自定义规则集在 epoch=1 时的冻结 hash（018 迁移回填用）。

**硬编码字面量，不是 `ruleset_hash()`。** 2026-08-27 之前它是 `= ruleset_hash()`——
那时 `SYSTEM_RULES` 恰好就是 R2+R3，两者算出来同一个数，看不出区别。删 R2 之后
`SYSTEM_RULES` 只剩 R3，`ruleset_hash()` 会跟着变成一个新数；而这个常量记的是
**epoch=1 那一刻的历史值**，必须原地不动——018 迁移里冻的就是这串字面量
（`tests/test_migrate.py::test_migration_018_frozen_hash_matches_the_catalog_constant`
钉着两边相等）。改回 `ruleset_hash()` 会让这个「历史」常量偷偷跟着当前目录漂移，
018 那条测试却测不出来（它只比字符串，不管字符串是怎么算出来的）——
这正是 R2 删除这一刀真正踩过的坑，写在这里防第二次踩。"""

CURRENT_RULESET_EPOCH: Final[int] = 2
"""当前全局 ruleset 的 epoch 号。**手动维护，随 `SYSTEM_RULES` 的语义变化同笔递增**
——迁移 031 删 R2 时从 1 改成 2，是本仓库第一次真的动它。`project.create()`
新建项目时拿这个数字（不是硬编码的 1），这样新书从出生那一刻就站在当前 epoch 上，
不会落后于刚做完 031 迁移的旧书。"""

CURRENT_RULESET_HASH: Final = ruleset_hash()
"""当前 `SYSTEM_RULES` 的语义 hash。**跟 `SYSTEM_RULESET_V1_HASH` 相反，这个必须是
活的计算**——它就是要跟着 `SYSTEM_RULES` 走，`project.create()` 和迁移 031 都读它，
不读某个手抄的字面量。`tests/test_migrate.py` 有一条断言它和迁移 031 里冻的字面量
一致（同 018 对 V1_HASH 的那条纪律）。"""
