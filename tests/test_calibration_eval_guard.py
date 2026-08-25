"""三臂冻结 prompt 逐字节不动 —— **现在只剩这一条**。

原来还有两条钉着「`eval/` 判分链不许认识 `calibration`」（认识它的人迟早会用它改考卷）。
`eval/` 和 `gate.py` 随秘密下线整个删了（ADR 0039），那两条守的东西不复存在。

**冻结这一条留着**：`draft/assemble.py` 现在只有产品路在走，但它的哈希闸仍然是
「改这个文件必须显式动那个数」——那条纪律跟考试无关，它防的是「悄悄改渲染器」。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO / "src" / "novel_harness" / "eval"
ASSEMBLE = REPO / "src" / "novel_harness" / "draft" / "assemble.py"

ASSEMBLE_SHA256 = "bbea289747a0b7d611356ba169b1f77a05e9fbe41b6115bdfdb6fe71fdde2e64"
"""`draft/assemble.py` 全文的 sha256。**改那个文件就必须在同一笔里改这个数。**

这一版是 2026-08-24 的秘密下线（ADR 0039）：`graph_section` 里的认知矩阵块和
禁写清单块整个删了，只剩「尚未登场」。**改这个数是有意的动作**，见那笔提交。

上一版是 2026-08-22 的 M1-a（退化态不再发「【在场】未知」那一块）。
在此之前这道闸比的是 `git diff --exit-code`——它只看「工作区 vs 已暂存」，
拦得住「顺手改了忘了看」，**拦不住「改了并且提交」**，而真要改考卷的人当然会提交。
"""


def _assemble_digest(path: Path = ASSEMBLE) -> str:
    """**只看文件内容，不看任何进程外的状态**（这就是这道闸整件事的重点）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_three_arm_assembler_is_byte_identical() -> None:
    """`assemble()` 一个字节都没动（三臂共用出口，ADR 0010 D4 / EVAL_PROTOCOL §2 冻结）。"""
    assert _assemble_digest() == ASSEMBLE_SHA256, (
        "你改了 `src/novel_harness/draft/assemble.py` —— 那是 X0/X1/X2 三臂共用的渲染器，"
        "改它就是**改 M2 的考卷**：三臂 prompt 一变，`runs/*.jsonl` 里已经跑出来的结果"
        "就和新的不可比，而 ADR 0009 的裁决表是照着那些数读的。\n"
        "确实要改：在**同一笔**里把上面的 `ASSEMBLE_SHA256` 换成新文件的 sha256"
        "（`python -c \"import hashlib,pathlib;"
        "print(hashlib.sha256(pathlib.Path('src/novel_harness/draft/assemble.py')"
        ".read_bytes()).hexdigest())\"`），"
        "并在提交信息里写清楚**改了哪一行、为什么**。"
        "这个数一动，改考卷这件事就在 diff 里藏不住了。"
    )


def test_the_freeze_notices_a_change_that_was_already_committed(tmp_path: Path) -> None:
    """守卫的自守卫：**这道闸认的是文件内容，不是「有没有未提交改动」。**

    它坏掉时代表：有人把冻结换回了 `git diff --exit-code` 那种写法——
    2026-08-22 实测那种写法 `git commit` 一下就绿，于是一道自称「三臂 prompt 逐字节
    冻结」的守卫，实际效果只是「别把这个文件改脏了」。

    这里拿一份**在 git 里根本不存在**的副本当探针：摘要必须变。git 那种写法在这份
    副本上无从判断，正是因为它比的不是内容。
    """
    twin = tmp_path / "assemble.py"
    twin.write_bytes(ASSEMBLE.read_bytes() + "\n# 改了考卷\n".encode())

    assert _assemble_digest(twin) != ASSEMBLE_SHA256
    assert _assemble_digest(twin) != _assemble_digest()


def test_the_freeze_never_shells_out() -> None:
    """同上一条的第二半：**这个文件里不许再出现 `subprocess`**。

    它坏掉时代表：冻结又开始依赖仓库状态（git 索引、工作区是否干净、有没有远端）——
    而那三样都不是「考卷有没有被改」这个问题的答案。
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "subprocess" not in imported
