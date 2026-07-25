"""M2 kill-gate 的度量层：泄漏检测（集合判断）+ 统计（精确 McNemar）。

这一层是 kill-gate 的「判分器」，也是 `draft/` 起草核心的伴生物——它测的必须是产品会发的
那个 prompt（三臂 X0/X1/X2 = `draft/assemble.py` 的三个 form），否则 gate 的裁决不算数
（`checks/base.py`：判分器 == Validator，同一份代码）。

**这一层不做语义判断**（ADR 0005）：泄漏 = 集合判断（正文命中禁忌 tell），裁决 = 精确检验。
完整协议见 docs/EVAL_PROTOCOL.md。
"""

from __future__ import annotations
