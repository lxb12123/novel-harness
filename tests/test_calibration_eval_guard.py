"""起草渲染器逐字节不动 —— **现在只剩这一条**。

原来还有两条钉着「`eval/` 判分链不许认识 `calibration`」（认识它的人迟早会用它改考卷）。
`eval/` 和 `gate.py` 随秘密下线整个删了（ADR 0039），那两条守的东西不复存在。

**冻结这一条留着，且理由已经换过一次**：它当年守的是「不许改 M2 的考卷」；
三臂 2026-08-25 删掉之后（`PromptForm` 整个走），`draft/assemble.py` 是**产品发给模型的
那一份 prompt 的唯一出口**——改它就是改每一位作者拿到的每一稿。哈希闸的意思因此从
「别偷偷改考卷」变成「别偷偷改产品」，**而那条纪律比前一条更该留着**。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO / "src" / "novel_harness" / "eval"
ASSEMBLE = REPO / "src" / "novel_harness" / "draft" / "assemble.py"

ASSEMBLE_SHA256 = "7960eb906990c17d3e833ddd94979718ff77b1094c2d1bde518cadad664629c4"
"""`draft/assemble.py` 全文的 sha256。**改那个文件就必须在同一笔里改这个数。**

这一版是 2026-08-25 的三臂下线：`PromptForm` 整个删了，`assemble()` / `graph_section()` /
`_forbidden_block()` 三个签名去掉 `form` 参数，`_forbidden_block` 只留清单那一种渲染
（散文那一版是为 X2 写的，没有别的消费者）。**改这个数是有意的动作**，见那笔提交。

上一版是 2026-08-24 的秘密下线（ADR 0039）：`graph_section` 里的认知矩阵块和禁写清单块
整个删了，只剩「尚未登场」。再上一版是 2026-08-22 的 M1-a（退化态不再发「【在场】未知」那一块）。
在此之前这道闸比的是 `git diff --exit-code`——它只看「工作区 vs 已暂存」，
拦得住「顺手改了忘了看」，**拦不住「改了并且提交」**，而真要改考卷的人当然会提交。
"""


def _assemble_digest(path: Path = ASSEMBLE) -> str:
    """**只看文件内容，不看任何进程外的状态**（这就是这道闸整件事的重点）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_prompt_assembler_is_byte_identical() -> None:
    """`assemble()` 一个字节都没动（产品发给模型的那份 prompt 的唯一出口，ADR 0010 D4）。"""
    assert _assemble_digest() == ASSEMBLE_SHA256, (
        "你改了 `src/novel_harness/draft/assemble.py` —— 那是**产品发给模型的那一份 prompt "
        "的唯一出口**，改它就是改每一位作者拿到的每一稿，而它没有任何别的守卫。\n"
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
