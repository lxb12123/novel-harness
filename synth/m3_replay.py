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


def resolve_project(conn: Any) -> str:
    """库里那**唯一**一个项目的 id。

    ── 为什么不拿考卷上那个号去找 ────────────────────────────────────────────
    考卷（`m3_ground_truth.json`）里记着一个 `project_id`，那是**写考卷那天那座图书馆
    的门牌号**。而造图书馆的 `synth/build.py` 走 `project.create`，**每建一次就换一个
    随机 ULID**（它自己的 docstring 写着「每次都建一个新项目」）。

    于是拿考卷上的号当查找键，等于要求「图书馆必须是那一次建的那一座」——
    2026-08-21 实测：重建一份库再 replay，25 道题答对 **0** 道，门直接不通过。
    能过这道门的全世界只有一份文件（本机那个 2026-07-30 建的 `gate.db`），
    它在 `.gitignore` 里、复制不出来，**于是这道预注册的门在任何新克隆上都不成立**。

    改成现取之后，考卷上那个号退成一条**出处记录**（写卷子那天用的是哪座馆），
    不再是查找键——题目、答案、阈值一个字没动，动的只是「怎么找到图书馆」。

    多于一个项目就报错而不是挑一个：`build.py` 说过往同一个库跑第二次「只会多出一个
    项目，库里于是有两份看起来都对的真相」。那种时候**说不知道**，不猜。
    """
    rows = conn.execute("SELECT id FROM project ORDER BY id").fetchall()
    if len(rows) == 1:
        return str(rows[0][0])
    if not rows:
        raise ValueError(
            "这个库里一个项目都没有 —— 它不是 `synth/build.py` 造出来的考场。"
            "先跑 `uv run python synth/build.py --out <新路径>`。"
        )
    ids = ", ".join(str(r[0]) for r in rows)
    raise ValueError(
        f"这个库里有 {len(rows)} 个项目（{ids}）—— 说不出该考哪一个。"
        "多半是往同一个库跑了两次 build.py；请指一个新路径重建。"
    )


def replay(
    *,
    db_path: Path,
    ground_truth: Path,
    booklet: Path,
    project_id: str | None = None,
    checks: tuple[Any, ...] | None = None,
) -> ReplaySummary:
    """跑完整张考卷 + 干净正文抽查，返回汇总（不打印，测试直接吃这个）。

    `project_id` 不给就从库里现取（见 `resolve_project`）。显式给了就照旧和考卷核对
    ——那是「我知道我在指哪一座馆」的用法，值得当场拦住指错。
    """
    from novel_harness.checks import ALL_CHECKS

    effective_checks = ALL_CHECKS if checks is None else checks
    data = json.loads(ground_truth.read_text(encoding="utf-8"))
    cases = data["cases"]
    if len(cases) != TOTAL_CASES:
        raise ValueError(f"考卷必须是 {TOTAL_CASES} 题，收到 {len(cases)}")
    if project_id is not None and data["project_id"] != project_id:
        raise ValueError("考卷 project_id 与 --project 不一致")

    # ⚠️ **先迁移再读。** `gate.db` 是一个长期躺在磁盘上的本地产物（gitignore，不进版本
    # 控制），而引擎的读路径会跟着迁移往前走：2026-08-20 合并保存闭环任务后
    # `queries.alias_rows` 开始 JOIN `alias_evidence`（迁移 023），于是一个停在旧版本的
    # 旧产物上 replay 会以 `no such table` 当场炸——**而它炸的是一条预注册的门**。
    # 迁移不改考卷：题目在 `m3_ground_truth.json` 和 `booklet.txt` 里，阈值是常量，
    # 迁移只补表补列（`test_populated_v1_database_migrates_without_changing_existing_rows`
    # 钉着「既有行一个字节不动」）。
    # **库不存在就当场说清楚，别顺手造一个空的出来。** `db.connect` 会把文件建出来，
    # 而 `synth/build.py` **有意不覆盖已存在的库**——于是新克隆上第一次跑会陷进一个
    # 很迷惑的形态：测试自己留下一个空 `gate.db`，然后 build.py 拒绝覆盖它，
    # 人得先想到去 `rm` 才能往下走。这一条把那个坑堵在入口。
    if not db_path.exists():
        raise FileNotFoundError(
            f"考场不存在：{db_path}。它是 `.gitignore` 里的本地产物（`*.db` 从不进版本库），"
            "新克隆上要先造一份：`uv run python synth/build.py`。"
        )

    conn = db.connect(db_path)
    db.migrate(conn)
    if project_id is None:
        project_id = resolve_project(conn)
    real = SqliteStoryGraph(conn)
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
    parser.add_argument("--project", default=None,
                        help="不给就从库里现取那唯一一个项目（见 resolve_project）")
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
