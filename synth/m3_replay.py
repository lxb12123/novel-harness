"""M3 合成门槛的量具（docs/M3_GATE_PROTOCOL.md）：构造违规/干净段落 → R2/R3 → 真阳性/误报。

边界数据（``first_appears`` / 死亡边）全部来自 ``synth/m3_ground_truth.json``，
**运行期在内存叠加**到真 store 上：不写 ``gate.db``、不改 ``booklet.toml``、
不动 12 章正文——M2 仪器一个字节不碰（当前 M2 轮次可能正在跑同一本库）。

叠加层只实现 R2/R3 会调的两个方法（``resolve`` / ``state_at``），其余抛
``NotImplementedError``——规则是纯函数，契约就是「只从 ctx.store 取数据」。

用法：
    uv run python -m synth.m3_replay
        --db synth/gate.db --project <pid> --ground-truth synth/m3_ground_truth.json

退出码：门槛过（正题 ≥22/25 且 干净对照 0/25 且 干净正文 0 issue）= 0，否则 1。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_harness import db
from novel_harness.checks import CheckContext, Issue, run_checks
from novel_harness.graph import (
    AliasHit,
    EdgeType,
    HealthValue,
    InformationScope,
    KnowledgeMatrix,
    Node,
    NodeLabel,
    NodeProps,
    Resolution,
    StateSnapshot,
    StateValue,
    StoryGraph,
    Subgraph,
    UpsertResult,
)
from novel_harness.graph.sqlite_store import SqliteStoryGraph
from novel_harness.text.chapterize import chapterize

EXPECTED_ISSUE_TYPE = {"R2": "FUTURE_LEAK", "R3": "DEAD_SPEAKS"}
MIN_TRUE_POSITIVES = 22
TOTAL_CASES = 25

HEALTH_DIM_ID = "state:m3:health"


class OverlayGraph:
    """把考卷里的边界数据叠加到真 store 上。只实现 resolve / state_at。"""

    def __init__(
        self,
        real: StoryGraph,
        *,
        first_appears: dict[str, int],
        deaths: Sequence[dict[str, Any]],
    ) -> None:
        self._real = real
        self._first_appears = first_appears
        self._deaths = list(deaths)

    def _patched_node(self, node: Node) -> Node:
        first = self._first_appears.get(node.name)
        if first is None:
            return node
        return node.model_copy(
            update={"props": NodeProps(first_appears_chapter=first)}
        )

    def resolve(
        self,
        project_id: str,
        surfaces: Sequence[str] | None = None,
        *,
        rules_only: bool = False,
    ) -> list[Resolution]:
        out: list[Resolution] = []
        for resolution in self._real.resolve(
            project_id, surfaces, rules_only=rules_only
        ):
            hits = [
                AliasHit(
                    node=self._patched_node(hit.node),
                    kind=hit.kind,
                    usable_for_rules=hit.usable_for_rules,
                )
                for hit in resolution.hits
            ]
            out.append(resolution.model_copy(update={"hits": hits}))
        return out

    def state_at(
        self,
        project_id: str,
        node_id: str,
        chapter: int,
        *,
        scope: InformationScope = InformationScope.CANON,
    ) -> StateSnapshot:
        snapshot = self._real.state_at(
            project_id, node_id, chapter, scope=scope
        )
        # R3 的 has_appeared() 读的是快照里的 node.props——必须一起补，
        # 否则 first_appears 只出现在 resolve 侧，state_at 侧恒「已登场」。
        snapshot = snapshot.model_copy(
            update={"node": self._patched_node(snapshot.node)}
        )
        death = next(
            (
                d
                for d in self._deaths
                if d["name"] == snapshot.node.name
                and chapter >= int(d["chapter"])
            ),
            None,
        )
        if death is None:
            return snapshot
        dim = Node(
            id=HEALTH_DIM_ID,
            project_id=project_id,
            label=NodeLabel.STATE_DIM,
            name="健康",
            props=NodeProps(dim_key="health"),
        )
        dead_value = StateValue(
            dim=dim,
            dim_key="health",
            value="死",
            value_key=HealthValue.DEAD,
            since_chapter=int(death["chapter"]),
            evidence_id=None,
        )
        return snapshot.model_copy(update={"states": [*snapshot.states, dead_value]})

    def knowledge_matrix(
        self,
        project_id: str,
        chapter: int,
        cast: Sequence[str],
        *,
        secrets: Sequence[str] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> KnowledgeMatrix:
        raise NotImplementedError

    def subgraph(
        self,
        project_id: str,
        center: str,
        chapter: int,
        *,
        hops: int = 1,
        edge_types: Collection[EdgeType] | None = None,
        scope: InformationScope = InformationScope.CANON,
    ) -> Subgraph:
        raise NotImplementedError

    def upsert_edge(self, spec: Any) -> UpsertResult:
        raise NotImplementedError


@dataclass(frozen=True)
class CaseResult:
    id: str
    rule: str
    chapter: int
    true_positive: bool
    false_positive: bool
    violation_issues: tuple[Issue, ...]
    clean_issues: tuple[Issue, ...]


@dataclass(frozen=True)
class ReplaySummary:
    true_positives: int
    false_positives: int
    clean_prose_issues: int
    total_cases: int
    min_true_positives: int
    results: tuple[CaseResult, ...]

    @property
    def passed(self) -> bool:
        return (
            self.true_positives >= self.min_true_positives
            and self.false_positives == 0
            and self.clean_prose_issues == 0
        )


def _run_one(
    store: StoryGraph,
    project_id: str,
    case: dict[str, Any],
    *,
    violation: bool,
    checks: tuple[Any, ...],
) -> tuple[Issue, ...]:
    ctx = CheckContext(
        store=store,
        project_id=project_id,
        chapter=int(case["chapter"]),
        paragraphs=[case["violation"] if violation else case["clean"]],
    )
    return tuple(run_checks(ctx, checks=checks))


def replay(
    *,
    db_path: Path,
    project_id: str,
    ground_truth: Path,
    booklet: Path,
    checks: tuple[Any, ...] | None = None,
) -> ReplaySummary:
    """跑完整张考卷 + 干净正文抽查，返回汇总（不打印，测试直接吃这个）。"""
    from novel_harness.checks import ALL_CHECKS

    effective_checks = ALL_CHECKS if checks is None else checks
    data = json.loads(ground_truth.read_text(encoding="utf-8"))
    cases = data["cases"]
    if len(cases) != TOTAL_CASES:
        raise ValueError(f"考卷必须是 {TOTAL_CASES} 题，收到 {len(cases)}")
    if data["project_id"] != project_id:
        raise ValueError("考卷 project_id 与 --project 不一致")

    real = SqliteStoryGraph(db.connect(db_path))
    overlay = OverlayGraph(
        real,
        first_appears=data["first_appears"],
        deaths=data["deaths"],
    )

    results: list[CaseResult] = []
    tp = 0
    fp = 0
    for case in cases:
        violation_issues = _run_one(
            overlay, project_id, case, violation=True, checks=effective_checks
        )
        expected_type = EXPECTED_ISSUE_TYPE[case["rule"]]
        true_positive = any(
            i.rule == case["rule"] and i.issue_type == expected_type
            for i in violation_issues
        )
        clean_issues = _run_one(
            overlay, project_id, case, violation=False, checks=effective_checks
        )
        false_positive = len(clean_issues) > 0
        tp += true_positive
        fp += false_positive
        results.append(
            CaseResult(
                id=case["id"],
                rule=case["rule"],
                chapter=int(case["chapter"]),
                true_positive=true_positive,
                false_positive=false_positive,
                violation_issues=violation_issues,
                clean_issues=clean_issues,
            )
        )

    # 干净正文抽查：**不叠加 overlay**，用真库把 R2/R3 跑在 12 章正文上。
    cz = chapterize(booklet.read_text(encoding="utf-8"))
    clean_prose_issues = 0
    for index, chapter in enumerate(cz.chapters, start=1):
        ctx = CheckContext(
            store=real,
            project_id=project_id,
            chapter=index,
                paragraphs=chapter.body.splitlines(),
        )
        clean_prose_issues += len(run_checks(ctx, checks=effective_checks))

    return ReplaySummary(
        true_positives=tp,
        false_positives=fp,
        clean_prose_issues=clean_prose_issues,
        total_cases=len(cases),
        min_true_positives=MIN_TRUE_POSITIVES,
        results=tuple(results),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="synth/gate.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--ground-truth", default="synth/m3_ground_truth.json")
    parser.add_argument("--booklet", default="synth/booklet.txt")
    args = parser.parse_args()

    summary = replay(
        db_path=Path(args.db),
        project_id=args.project,
        ground_truth=Path(args.ground_truth),
        booklet=Path(args.booklet),
    )
    for result in summary.results:
        mark = "✓" if result.true_positive else "✗ TP"
        if result.false_positive:
            mark += " ✗ FP"
        print(
            f"{result.id} {result.rule} ch{result.chapter}: {mark}",
            flush=True,
        )
    print(
        f"真阳性 {summary.true_positives}/{summary.total_cases} "
        f"(门槛 ≥{summary.min_true_positives}) · "
        f"干净对照误报 {summary.false_positives} · "
        f"干净正文 issue {summary.clean_prose_issues}",
        flush=True,
    )
    if not summary.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
