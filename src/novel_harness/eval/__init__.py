"""M2 kill-gate 的度量层：泄漏检测（集合判断）+ 统计（精确 McNemar）+ runner。

这一层是 kill-gate 的「判分器」，也是 `draft/` 起草核心的伴生物——它测的必须是产品会发的
那个 prompt（三臂 X0/X1/X2 = `draft/assemble.py` 的三个 form），否则 gate 的裁决不算数
（`checks/base.py`：判分器 == Validator，同一份代码）。

**这一层不做语义判断**（ADR 0005）：泄漏 = 集合判断（正文命中禁忌 tell），裁决 = 精确检验。
完整协议见 `docs/EVAL_PROTOCOL.md` **加四份修正案**（读协议一份不够）。

四块：
- `leak.py` —— 一次生成漏没漏。禁忌集只经 `panel/constraints`，本层不许自建（§3）。
- `score.py` —— `majority` / 精确 McNemar / Holm / **预注册裁决表 `decide()`**。
- `confound_lint.py` —— X1 与 X2 除「清单 vs 散文」外不许有第二处差异（§2 反混淆铁律）。
- `runner.py` —— **全仓唯一同时碰起草侧和判分侧的代码**，所以它必须住在这儿
  （`tests/test_draft_boundary.py` 的 `SCORER_DIRS` 写死了这条：它在墙外就能一个人破掉墙的两面）。

`runner` 在包级导出意味着 `import novel_harness.eval` 会连带拉起 `draft/`——这是**故意的**：
「判分器测的就是产品会发的那个 prompt」是这一层存在的前提，两侧装不上就该在 import 时炸，
而不是等跑到第 25 条陷阱才发现。

**`confound_lint` 这个名字故意不在下面的导出里。** 它同时是模块名和函数名，包级绑定函数会把
模块**彻底遮死**：`__init__` 先把子模块挂上去、再用函数覆盖同名属性，此后
`from ..eval import confound_lint`、`import ..eval.confound_lint as mod`（3.7+ 先走 getattr）
拿到的全是函数，`mod.LEN_TOLERANCE` 直接 AttributeError。
规矩：**与子模块同名的可调用对象一律不上包级**，要它就 `from .confound_lint import confound_lint`。
（`draft/assemble.py` 的 `assemble` 同理，那边写了同一条。）
"""

from __future__ import annotations

from .confound_lint import LEN_TOLERANCE, ConfoundReport, visible_len
from .leak import LeakResult, score_against, score_draft
from .runner import ARMS, PROTOCOL_VERSION, TrapSpec, load_traps, run_gate, stamped_path
from .score import (
    ALPHA,
    BASE_REPEATS,
    ESCALATED_REPEATS,
    FORM_PIVOT_MIN_DELTA,
    MIN_DELTA,
    MIN_DISCORDANT,
    ArmComparison,
    GateDecision,
    GateInput,
    TrapKind,
    TrapRuns,
    Verdict,
    compare_arms,
    decide,
    holm,
    majority,
    mcnemar_exact_p,
    sign_stable,
)

__all__ = [
    "ALPHA",
    "ARMS",
    "BASE_REPEATS",
    "ESCALATED_REPEATS",
    "FORM_PIVOT_MIN_DELTA",
    "LEN_TOLERANCE",
    "MIN_DELTA",
    "MIN_DISCORDANT",
    "PROTOCOL_VERSION",
    "ArmComparison",
    "ConfoundReport",
    "GateDecision",
    "GateInput",
    "LeakResult",
    "TrapKind",
    "TrapRuns",
    "TrapSpec",
    "Verdict",
    "compare_arms",
    "decide",
    "holm",
    "load_traps",
    "majority",
    "mcnemar_exact_p",
    "run_gate",
    "score_against",
    "score_draft",
    "sign_stable",
    "stamped_path",
    "visible_len",
]
