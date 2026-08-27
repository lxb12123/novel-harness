"""M3 合成门槛（docs/M3_GATE_PROTOCOL.md）的回归测试。

和 test_synth_artifact.py 同样的道理分文件：这里是**考卷 + 量具**本身——
门槛是不是真的会咬、考卷是不是 10 题、真跑一遍数字对不对。

⚠️ 本文件会因为改 `synth/m3_ground_truth.json` / 规则行为而红，那是它的工作。
门槛数字是预注册的（先 commit、后测量），别顺手把断言改成当前输出。

⚠️ **2026-08-27：题数从 25 降到 10。** R2 FUTURE_LEAK 砍了（[ADR 0040]
(../docs/adr/0040-future-leak-cut.md)），`m3_ground_truth.json` 里原来 15 道
`rule: "R2"` 的题删了，只剩 10 道 `rule: "R3"`。**这不是「改断言让它匹配新输出」**
——依据是 [`docs/M3_GATE_PROTOCOL_AMENDMENT_1.md`](../docs/M3_GATE_PROTOCOL_AMENDMENT_1.md)：
M3 门槛 2026-08-02 已经以 25/25 通过、M4 已在其上解锁并落地，这次改动发生在
通过判定之后，改的是「协议今天还测什么」，不是「当年测出的结果算不算数」。
本文件下面每一处从 25 改成 10 的数字，都能在那份修正案里找到出处。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synth.m3_replay import (  # noqa: E402
    EXPECTED_ISSUE_TYPE,
    MIN_TRUE_POSITIVES,
    TOTAL_CASES,
    replay,
    resolve_project,
)

SYNTH = ROOT / "synth"
GT = SYNTH / "m3_ground_truth.json"
BOOKLET = SYNTH / "booklet.txt"
DB = SYNTH / "gate.db"
PROJECT_ID = "project:01KYV174HMJ8BQD47KRF6K6FQK"


def _data() -> dict:
    return json.loads(GT.read_text(encoding="utf-8"))


def test_fixture_is_a_10_question_paper_with_clean_controls() -> None:
    data = _data()
    cases = data["cases"]
    assert len(cases) == TOTAL_CASES == 10
    assert {c["rule"] for c in cases} == {"R3"}
    assert {c["rule"] for c in cases} == set(EXPECTED_ISSUE_TYPE)
    for case in cases:
        assert 1 <= case["chapter"] <= 12
        assert case["violation"] != case["clean"]
        assert case["violation"].strip()
        assert case["clean"].strip()
    # 这个号今天是**出处记录**不是查找键（replay 从库里现取）——留着是为了钉住
    # 「这份考卷没被换过」，而不是「图书馆必须是那一座」。
    assert data["project_id"] == PROJECT_ID


def test_replay_passes_the_preregistered_gate() -> None:
    """真跑一遍：10/10 真阳性、干净对照 0 误报、干净正文 0 issue。"""
    # **不传 project_id**：从库里现取。考卷上那个号是写卷子那天那座图书馆的门牌号，
    # 而 `build.py` 每建一次库就换一个随机 ULID——拿它当查找键，等于要求「必须是那一次
    # 建的那一座」，于是重建一份就 0/10，这道门在任何新克隆上都不成立（ADR 见下）。
    summary = replay(db_path=DB, ground_truth=GT, booklet=BOOKLET)
    assert summary.true_positives == TOTAL_CASES == 10
    assert summary.false_positives == 0
    assert summary.clean_prose_issues == 0
    assert summary.passed


def test_gate_fails_when_the_rules_are_missing() -> None:
    """门槛会咬：一条规则都不给，10 题全落空 → 不过。

    ⚠️ **2026-08-14 之前这里给的是 R4**（一条不读正文的规则，所以题目必然全落空）。
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


def test_the_gate_now_requires_every_case_no_partial_credit() -> None:
    """2026-08-27 之前这条测的是「15 道 R2 低于 22 的及格线，所以 R3 若全坏，门槛必然
    不过」——那个论证靠的是「题目分成两组，一组够不够格不影响另一组」，R2 砍掉之后
    只剩一组，论证的前提没了。

    换成这条：**这份考卷不再有「大部分对就算过」的余地**——`MIN_TRUE_POSITIVES ==
    TOTAL_CASES`，10 道题错一道就不过。这不是随便定的：25/25 那次通过（`docs/
    M3_GATE_PROTOCOL_AMENDMENT_1.md` 记着）本来就包含了这 10 道 R3 题全部命中，
    所以要求它保持这个水平，不是重新给它一个更松的及格线（也不是按 22/25 的比例
    折算出一个 8.8 这种没有出处的数）。
    """
    assert MIN_TRUE_POSITIVES == TOTAL_CASES == 10


def test_the_gate_finds_its_library_without_the_paper_naming_it(tmp_path: Path) -> None:
    """图书馆从库里现取，**不靠考卷上那个门牌号**——否则这道门重建一次就永远不通过。

    ── 它补的是一次实测出来的真故障（2026-08-21）─────────────────────────────
    考卷 `m3_ground_truth.json` 里记着一个 `project_id`，而造库的 `synth/build.py` 走
    `project.create`，**每建一次就换一个随机 ULID**。原来 replay 拿考卷上那个号当查找
    键，于是「能过这道门的库」全世界只有一份——本机那个 2026-07-30 建的 `gate.db`，
    它在 `.gitignore` 里、复制不出来。实测：重建一份再跑，25 道题答对 **0** 道。
    也就是说**这道预注册的门在任何新克隆上都不成立**，而没有任何东西说得出来。

    改成现取之后实测：重建的库（另一个 ULID）照样 25/25。

    这里钉三种情形，因为「挑一个」和「说不知道」的差别在这个仓库里是有立场的：
    恰好一个 → 就是它；一个都没有 / 多于一个 → **报错，不猜**。
    """
    from novel_harness import project
    from novel_harness.db import connect, migrate

    conn = connect(tmp_path / "resolve.db")
    migrate(conn)

    # ① 空库：说不出考哪个，且要说得出「这不是 build.py 造的考场」。
    with pytest.raises(ValueError, match="一个项目都没有"):
        resolve_project(conn)

    # ② 恰好一个：就是它——**不比对任何写死的 id**。
    pid = project.create(conn, name="考场", root_path=".").id
    conn.commit()
    assert resolve_project(conn) == pid

    # ③ 两个：往同一个库跑了两次 build.py，「库里于是有两份看起来都对的真相」。
    #    这时候挑一个 = 有一半概率悄悄考错那一座，所以报错。
    other = project.create(conn, name="考场（第二次）", root_path=".").id
    conn.commit()
    with pytest.raises(ValueError, match="2 个项目"):
        resolve_project(conn)
    assert other  # 用一下，免得 lint 说它没人读

    conn.close()
