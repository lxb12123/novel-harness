"""M3 合成门槛（docs/M3_GATE_PROTOCOL.md）的回归测试。

和 test_synth_artifact.py 同样的道理分文件：这里是**考卷 + 量具**本身——
门槛是不是真的会咬、考卷是不是 25 题、真跑一遍数字对不对。

⚠️ 本文件会因为改 `synth/m3_ground_truth.json` / 规则行为而红，那是它的工作。
门槛数字是预注册的（先 commit、后测量），别顺手把断言改成当前输出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synth.m3_replay import (  # noqa: E402
    EXPECTED_ISSUE_TYPE,
    MIN_TRUE_POSITIVES,
    TOTAL_CASES,
    replay,
)

SYNTH = ROOT / "synth"
GT = SYNTH / "m3_ground_truth.json"
BOOKLET = SYNTH / "booklet.txt"
DB = SYNTH / "gate.db"
PROJECT_ID = "project:01KYV174HMJ8BQD47KRF6K6FQK"


def _data() -> dict:
    return json.loads(GT.read_text(encoding="utf-8"))


def test_fixture_is_a_25_question_paper_with_clean_controls() -> None:
    data = _data()
    cases = data["cases"]
    assert len(cases) == TOTAL_CASES == 25
    assert {c["rule"] for c in cases} == {"R2", "R3"}
    assert {c["rule"] for c in cases} == set(EXPECTED_ISSUE_TYPE)
    for case in cases:
        assert 1 <= case["chapter"] <= 12
        assert case["violation"] != case["clean"]
        assert case["violation"].strip()
        assert case["clean"].strip()
    assert data["project_id"] == PROJECT_ID


def test_replay_passes_the_preregistered_gate() -> None:
    """真跑一遍：25/25 真阳性、干净对照 0 误报、干净正文 0 issue。"""
    summary = replay(
        db_path=DB,
        project_id=PROJECT_ID,
        ground_truth=GT,
        booklet=BOOKLET,
    )
    assert summary.true_positives == TOTAL_CASES == 25
    assert summary.false_positives == 0
    assert summary.clean_prose_issues == 0
    assert summary.passed


def test_gate_fails_when_the_rules_are_missing() -> None:
    """门槛会咬：一条规则都不给，25 题全落空 → 不过。

    ⚠️ **2026-08-14 之前这里给的是 R4**（一条不读正文的规则，所以 25 题必然全落空）。
    R4 随场景块一起砍了（[ADR 0027](../docs/adr/0027-scene-blocks-cut.md)），
    而这条测试要的从来不是 R4 本身，是「拿一组**跑不出真阳性**的规则去跑，门槛得咬住」。
    空元组是那件事最直接的形态。
    """
    summary = replay(
        db_path=DB,
        project_id=PROJECT_ID,
        ground_truth=GT,
        booklet=BOOKLET,
        checks=(),
    )
    assert not summary.passed
    assert summary.true_positives == 0
    assert summary.false_positives == 0


def test_r3_cases_are_what_keep_the_gate_from_passing_trivially() -> None:
    """15 道 R2 低于 22 的及格线：R3 若全坏，门槛必然不过（不需要 25 道题都敏感）。"""
    data = _data()
    r3 = sum(1 for c in data["cases"] if c["rule"] == "R3")
    assert r3 == 10
    assert TOTAL_CASES - r3 < MIN_TRUE_POSITIVES
