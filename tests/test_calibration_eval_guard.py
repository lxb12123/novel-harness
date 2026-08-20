"""Task 1 的硬守卫：三臂冻结 prompt 逐字节不动，eval runner/gate 不导入 calibration。"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO / "src" / "novel_harness" / "eval"
ASSEMBLE = REPO / "src" / "novel_harness" / "draft" / "assemble.py"


def test_eval_package_never_imports_calibration() -> None:
    """三臂判分链不得认识 SceneBrief —— 认识它的人迟早会用它改考卷。"""
    offenders: list[str] = []
    for path in sorted(EVAL_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any("calibration" in (alias.name or "") for alias in node.names):
                    offenders.append(f"{path.name}: import {node.names}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and "calibration" in node.module:
                    offenders.append(f"{path.name}: from {node.module} import …")
    assert not offenders, f"eval 包引用了校准模块：{offenders}"


def test_three_arm_assembler_is_byte_identical() -> None:
    """`assemble()` 一个字节都没动（三臂共用出口，ADR 0010 D4）。"""
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "--", str(ASSEMBLE.relative_to(REPO))],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert diff.returncode == 0, (
        f"`draft/assemble.py` 被改过了 —— 那是三臂共用的出口，改它 = 改考卷：\n{diff.stdout}"
    )


def test_calibration_lives_outside_eval_and_draft_frozen_paths() -> None:
    """校准只在产品路径；`eval/runner.py` / `eval/evidence.py` 都不碰它。"""
    for name in ("runner.py", "evidence.py"):
        source = (EVAL_DIR / name).read_text(encoding="utf-8")
        assert "calibration" not in source, f"eval/{name} 提了 calibration"
