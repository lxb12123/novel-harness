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

ASSEMBLE_SHA256 = "82c698a5231aaa80d1efe3152ed278f3b5a9b8eec0279407808a38f1c2e9d471"
"""`draft/assemble.py` 全文的 sha256。**改那个文件就必须在同一笔里改这个数。**

这一版是 2026-08-27 的**国际化第二批：装写作提示的框双语化了**。`_base()` 里
【上文】【在场】【这一场要写】三个标题和 cast 的列表分隔符（原来写死的顿号）、
`CONTINUATION_GOAL`（常量改成 `continuation_goal(language)` 函数），
全部从新模块 `draft/prompt_terms.py` 按 `length.language` 取；`EN_WRITING_PROMPT`
的人设第一句也补了「for an English-language novelist」（维护者点名的一处不对称——
中文那份写的是「**中文**长篇小说的写作搭档」，英文原来没点明语言）。
**中文取值是原来那几个字面量，逐字节不变**：`tests/test_draft_assemble.py` /
`tests/test_draft_continuation.py` 里那些钉死的精确字符串一个字都没有跟着改；
新增的 `tests/test_prompt_bilingual.py` 把整块记忆前言（含四个人物资料字段、
带参与者的事件）也逐字节钉了一遍，`tests/test_prompt_terms.py` 穷举词表本身。

上一版是 2026-08-26 的**国际化第一批 ⓪：唯一剩下的图谱块也删了**。删掉
`_forbidden_block()`（渲染「尚未登场、这一场不得出现：幽泉窟（第 10 章首现）」那一句）
连同 `graph_section()` 里那一支——维护者裁定「未登场的东西未来到底哪一章出现，本来就
不一定」，而浏览器上从来没有任何入口能设 `first_appears_chapter`（那个输入框 2026-08-13
就是有意裁掉的）。`panel/constraints.py::forbidden_entities()` 本身没删——R2 和右栏面板
还在用它，这一刀只砍它进 Writer prompt 的那一步。`graph_section()` 函数本身留着，
恒返回 `""`：它是下一个图谱块的落地点，不是这一个块专属的脚手架。

再上一版是 2026-08-26 的**注释重写，零行为变化**：只动了 `WRITE_RULE_FORBIDDEN_HINTS`
的 docstring——它双重过时（指着 `/draft`、`nh draft` 两个不存在的入口，还在拿三臂的
`Δ 塌掉` 当理由），而今天用那张网的只剩 `product_draft.check_request()` 一处。
**渲染逻辑一个字节没动**：这一笔的 diff 只有那一段字符串，可以逐行核。

⚠️ **那一版是这个数第一次因为「只改注释」而变。** 它就该这样——这道闸比的是全文哈希，
所以注释也算；「改卷子藏不住」这条性质的代价，就是每一次有意的编辑都得在这儿留一行。
审计的人看这一行就知道该不该去读 diff。

更早一版是 2026-08-25 的三臂下线：`PromptForm` 整个删了，`assemble()` / `graph_section()` /
`_forbidden_block()` 三个签名去掉 `form` 参数，`_forbidden_block` 只留清单那一种渲染
（散文那一版是为 X2 写的，没有别的消费者）。**改这个数是有意的动作**，见那笔提交。

再更早一版是 2026-08-24 的秘密下线（ADR 0039）：`graph_section` 里的认知矩阵块和禁写清单块
整个删了，只剩「尚未登场」。更早一版是 2026-08-22 的 M1-a（退化态不再发「【在场】未知」那一块）。
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
