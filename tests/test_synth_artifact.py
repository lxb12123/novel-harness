"""**真小册子**（`synth/booklet.toml` + `booklet.txt`）本身对不对。

和 `tests/test_synth.py` 分文件是有意的，两者钉的是两件事：

| 文件 | 钉的东西 | 数据来源 |
|---|---|---|
| `test_synth.py` | **机制**——build 的写入链、七条放行条件各自会不会报 | 自带的 3 章小号 fixture |
| 本文件 | **仪器**——那本真小册子造得合不合协议、能不能真跑一轮 | `synth/` 里那两个文件 |

`test_synth.py` 的 docstring 说它「不碰 `synth/booklet.toml`」，理由成立
（小册子是数据、陷阱重造是协议 §6 规定的动作）。**但那条理由只解释了它为什么不测，
没有解释谁来测**——而在本文件出现之前，答案是「没有人」：整套 pytest 全绿，
而那本决定 kill-gate 全部结论的小册子一次都没被机器看过。

**为什么值得单独一个文件**：kill-gate 的结论完全由这本小册子的质量决定。
陷阱造软了 → 撞 §6 地板 → INVALID；tell 互为子串 → 判分口径串味；
X1/X2 的 prompt 长度拉开 → FORM-PIVOT 被静默摘掉。
这些全都不会让别的测试红，只会让**一轮花了真钱的实验**产出一个没人看得出问题的结论。

⚠️ 本文件**会因为改小册子而红**，那是它的工作，不是噪音。重造 `prior` 之后它红了，
说明新的 `prior` 破了协议的某一条，去看它指的那一条，别把断言删掉。

⚠️ `synth/` 是顶层目录不在 `src/` 里（同 `test_synth.py` 的说明），下面的 sys.path 为此。
"""

from __future__ import annotations

import json
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel_harness.db import connect  # noqa: E402
from novel_harness.draft.capabilities import (  # noqa: E402
    ProviderCapabilities,
    ReasoningDialect,
    ReasoningEffort,
    plan_call,
)
from novel_harness.draft.length import M2_LENGTH_SPEC  # noqa: E402
from novel_harness.draft.provider import ProviderConfig  # noqa: E402
import novel_harness.eval.runner as runner_mod  # noqa: E402
from novel_harness.eval.runner import load_traps, run_gate  # noqa: E402
from novel_harness.graph.sqlite_store import SqliteStoryGraph  # noqa: E402
from synth.build import BuildResult, GroundTruth, build, load_booklet  # noqa: E402
from synth.leak_selfcheck import selfcheck  # noqa: E402

SYNTH = ROOT / "synth"
BOOKLET = SYNTH / "booklet.toml"
PROSE = SYNTH / "booklet.txt"

CHAPTERS = 12
"""EVAL_PROTOCOL §4：「12 章、每章 800–1200 字」。冻结值，不是从 TOML 数出来的。"""

BOUNDARIES = 8
"""§4 的知识边界表恰好 8 条（KNOWS + BELIEVES 合计）。"""

KNOWS_TRAPS, FUTURE_TRAPS = 15, 10
"""修正案 1 的裁定。**改这两个数 = 改卷子**，那要另开第五份修正案。"""


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[BuildResult, Path, Path]:
    """跑一次真 build，整个模块共用。

    落在 tmp 里而不是 `synth/`：`ground_truth.json` 在 `.gitignore` 里，
    一个 clone 下来还没跑过 build 的仓库必须能跑通全部测试。
    """
    d = tmp_path_factory.mktemp("synth-artifact")
    db, out = d / "gate.db", d / "ground_truth.json"
    return build(booklet=BOOKLET, prose=PROSE, db=db, out=out), db, out


def test_the_real_booklet_matches_the_frozen_protocol_shape(
    built: tuple[BuildResult, Path, Path],
) -> None:
    """章数 / 知识边界数 / 陷阱分层，三样都是协议冻死的，不许随小册子漂。"""
    result, _, _ = built
    bk = load_booklet(BOOKLET)

    assert result.chapters == CHAPTERS, "§4：12 章"
    assert bk.book.chapters == CHAPTERS, "TOML 声明的章数和落库数得一致"

    boundaries = sum(len(s.knows) + len(s.believes) for s in bk.secrets)
    assert boundaries == BOUNDARIES, f"§4 的知识边界表是 {BOUNDARIES} 条"
    assert result.knows_edges + result.believes_edges == BOUNDARIES

    kinds = Counter(t.kind for t in bk.traps)
    assert kinds["KNOWS"] == KNOWS_TRAPS and kinds["FUTURE"] == FUTURE_TRAPS, (
        f"修正案 1 定死 {KNOWS_TRAPS} KNOWS + {FUTURE_TRAPS} FUTURE，现在是 {dict(kinds)}"
    )


def test_every_tell_became_a_detectable_non_canonical_alias(
    built: tuple[BuildResult, Path, Path],
) -> None:
    """`tell_aliases == secrets`。**少一个就是少一条检测得到的秘密**，
    而那条陷阱会安静地永远判「没泄漏」——三臂齐低、Δ≈0，长得和 KILL 一模一样。
    """
    result, _, _ = built
    assert result.tell_aliases == result.secrets


def test_the_real_booklet_passes_every_selfcheck_condition(
    built: tuple[BuildResult, Path, Path],
) -> None:
    """§4 的放行条件 + 修正案 4 的裁定 B/C，全部落在 `leak_selfcheck.py` 里。

    它验的不是「TOML 写得对」，是「**从 TOML 派生出来的 ground truth** 对」——
    派生走的是真实写入链（`declare_knows` 靠引语在正文里唯一命中定章号），
    所以这条同时也是「12 章正文和 25 条陷阱互相对得上」的证明。
    """
    _, _, out = built
    problems = selfcheck(booklet=BOOKLET, prose=PROSE, ground_truth=out)
    assert problems == [], "真小册子没过自检：\n  " + "\n  ".join(problems)


def test_no_trap_derives_an_empty_forbidden_set(
    built: tuple[BuildResult, Path, Path],
) -> None:
    """一条陷阱若派生出空的禁忌集，它**永远判不出泄漏**——不是「没泄漏」，是量不到。

    这两件事在 `runs/*.jsonl` 里长得一模一样（votes 全 False），所以只能在这儿拦。
    KNOWS 看 `must_not_reveal`，FUTURE 看 `forbidden`：§3 说 KNOWS 主导裁决、
    FUTURE 只作描述性地板，但**地板也得是量出来的**。
    """
    _, _, out = built
    gt = GroundTruth.model_validate(json.loads(out.read_text(encoding="utf-8")))

    empty = [
        t.id
        for t in gt.traps
        if not (t.must_not_reveal if t.kind == "KNOWS" else t.forbidden)
    ]
    assert not empty, f"这些陷阱的禁忌集是空的，永远量不到泄漏：{empty}"
    assert all(t.tells for t in gt.traps), "每条陷阱都得有 tell，否则检测器没词可找"


def test_a_full_dry_run_of_the_gate_survives_the_real_booklet(
    built: tuple[BuildResult, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**25 条陷阱 × 3 臂 × 3 次，走真 runner、真 assemble、真判分，只把模型换成桩。**

    这是这个文件里最贵也最值的一条。它一次性覆盖了三件在别处都验不到的事：

    1. 25 条陷阱**每一条**都能走通 `scene_view()` —— 有一条的 cast 解析不了、
       章号越界、秘密名对不上，真跑那天会在花掉几十条生成之后才炸。
    2. `assemble()` 在**真数据**上渲染得出三臂 —— 别处只在手写的满配 ctx 上验过。
    3. **`confound_ok` 是 True** —— X1 与 X2 在真小册子上仍然只差「清单 vs 散文」。
       它若为 False，§6 第 3 行会摘掉 FORM-PIVOT，**决策 A 照评、结论照出**，
       只是少了一个维度，而读结果的人不会知道它被摘过。

    桩客户端回一句干净的、不含任何 tell 的话，所以本条**不断言泄漏率**——
    那是要真模型才有意义的数字，在这儿断言它等于把仪器读数写死。
    """
    _, db, out = built
    monkeypatch.setattr(
        runner_mod,
        "PROTOCOL_VERSION",
        "EVAL_PROTOCOL.md@0393088 + 修正案 1/2/3/4/5/6/7/8 + ADR 0010/0011",
    )
    gt = json.loads(out.read_text(encoding="utf-8"))
    traps = load_traps(gt)
    assert len(traps) == KNOWS_TRAPS + FUTURE_TRAPS

    calls = 0

    def create(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return types.SimpleNamespace(
            model="stub",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="。" * 2_100
                    ),
                    finish_reason="stop",
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    config = ProviderConfig(
        base_url="http://127.0.0.1:1/v1",
        model="test-model",
        api_key="not-needed",
    )
    capability = ProviderCapabilities(
        base_url=config.base_url,
        model=config.model,
        source="operator-test",
        source_urls=("https://example.test/capability",),
        max_context_tokens=128_000,
        max_output_tokens=16_000,
        max_tokens_field="max_tokens",
        reasoning_levels=frozenset({ReasoningEffort.OFF, ReasoningEffort.HIGH}),
        reasoning_dialect=ReasoningDialect.ANTHROPIC_COMPAT,
        reasoning_shares_output=False,
    )
    plan = plan_call(M2_LENGTH_SPEC, ReasoningEffort.HIGH, capability)

    with connect(db) as conn:
        store = SqliteStoryGraph(conn)
        gi = run_gate(
            store,
            gt["project_id"],
            traps,
            config=config,
            plan=plan,
            repeats=3,
            out_path=tmp_path / "dry.jsonl",
            client=client,
        )

    assert len(gi.traps) == KNOWS_TRAPS + FUTURE_TRAPS
    assert calls == (KNOWS_TRAPS + FUTURE_TRAPS) * 3 * 3, "25 × 3 臂 × 3 次"
    assert gi.confound_ok is True, (
        "X1 与 X2 在真小册子上没通过反混淆检查 —— FORM-PIVOT 会被静默摘掉。"
        "去看 jsonl 里的 confound 行；改 assemble()，别放宽 LEN_TOLERANCE"
        "（那是预注册的旋钮）。"
    )
