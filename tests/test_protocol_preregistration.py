"""Guards for the additive M2 preregistration documents.

The body of ``EVAL_PROTOCOL.md`` is historical evidence.  New amendments may only
change its informational header, while current documentation must point readers at
the complete amendment set.

── 2026-08-24：这门考试的**对象**下线了，卷子没动 ──────────────────────

秘密整套功能被裁定下线（ADR 0039）。三臂的全部差别就在「注不注入认知矩阵」上，
所以没有秘密就没有 X1 / X2 —— **不是考试变难了，是题目不存在了**。

正文与九份修正案**一个字节未改，保留为历史**；退役这件事写在
`docs/EVAL_PROTOCOL_RETIREMENT.md`，协议头部（可改的那一半）加了一个指针。

**「正文未变」和「已退役」是两个独立断言，本文件里不许合并成一条。**
合并之后「有人偷偷改了正文」和「有人偷偷把退役说明删了」会长成同一条红，
而这两件事的下一步动作完全不同。

（`src/novel_harness/eval/__init__.py` 曾经也在下面那张「九份修正案」对照表里，
随 `eval/` 整个目录一起删掉了 —— 它是被考的那一侧，不是记账的那一侧。）
"""

from __future__ import annotations

import re
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "EVAL_PROTOCOL.md"
MARKER = "## 1. 被证伪的命题\n".encode()
FROZEN_BODY_SHA256 = "4977beeda66f1efb370f9a28a6c6dce43e37c3ccc8fc2fdd10d1b82cf9e4ac85"


def _body(data: bytes) -> bytes:
    """Return the frozen protocol body, rejecting a missing or duplicate marker."""
    assert data.count(MARKER) == 1
    return data[data.index(MARKER) :]


def test_frozen_protocol_body_still_matches_0393088() -> None:
    current_body = _body(PROTOCOL.read_bytes())
    assert sha256(current_body).hexdigest() == FROZEN_BODY_SHA256

    # 本地完整 clone 再对一次原 commit；CI/源码包可能是 shallow clone 或没有 .git，
    # 那时上面的内容寻址 hash 仍是独立、可移植的冻结证据。
    try:
        baseline = subprocess.check_output(
            ["git", "show", "0393088:docs/EVAL_PROTOCOL.md"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return
    assert current_body == _body(baseline)


def test_protocol_indexes_exactly_five_amendments() -> None:
    header = PROTOCOL.read_bytes().split(MARKER, 1)[0].decode()
    linked = tuple(
        int(number)
        for number in re.findall(r"EVAL_PROTOCOL_AMENDMENT_(\d+)\.md", header)
    )
    assert linked == (1, 2, 3, 4, 5)


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/EVAL_PROTOCOL_AMENDMENT_5.md",
        "docs/adr/0011-bilingual-draft-length.md",
    ],
)
def test_preregistration_assets_exist(relative_path: str) -> None:
    assert (ROOT / relative_path).is_file()


def test_adr_index_lists_0011() -> None:
    index = (ROOT / "docs/adr/README.md").read_text(encoding="utf-8")
    assert "[0011](0011-bilingual-draft-length.md)" in index


@pytest.mark.parametrize(
    ("relative_path", "current", "stale"),
    [
        (
            "docs/ROADMAP.md",
            "协议本体之外还有九份修正案",
            "协议本体之外还有八份修正案",
        ),
        (
            "CLAUDE.md",
            "这件事已经做过九次",
            "这件事已经做过八次",
        ),
        (
            "docs/ARCHITECTURE.md",
            "它此后已经被改过九次",
            "它此后已经被改过八次",
        ),
        (
            "synth/booklet.toml",
            "九份修正案一个字都没碰",
            "八份修正案一个字都没碰",
        ),
    ],
)
def test_current_references_name_nine_amendments(
    relative_path: str, current: str, stale: str
) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert current in text
    assert stale not in text


# ══════════════════════════════════════════════════════════════════════════
# 已退役（2026-08-24）—— 跟「正文未变」是两条独立断言
# ══════════════════════════════════════════════════════════════════════════

RETIREMENT = ROOT / "docs" / "EVAL_PROTOCOL_RETIREMENT.md"


def test_the_retirement_note_exists_and_says_it_cannot_be_run() -> None:
    """退役说明在，而且说得出「不能再跑」。

    它坏掉时代表：有人删了那份说明，于是下一个人读到的是一份看起来还能跑的卷子——
    然后去找那个已经不存在的 `synth/ground_truth.json`。
    """
    assert RETIREMENT.is_file(), "退役说明没了 —— 那份卷子会重新看起来像还能跑"
    text = RETIREMENT.read_text(encoding="utf-8")
    assert "不再可跑" in text or "不能" in text
    assert "0039" in text, "得指得到那条裁定，否则「为什么下线」查无出处"


def test_the_protocol_header_points_at_the_retirement_note() -> None:
    """协议**头部**（可改的那一半）带着那个指针。

    只在别处放一份说明是不够的：读协议的人从第一行开始读，而正文自己不会告诉他
    「这门考试的对象已经没了」。
    """
    header = PROTOCOL.read_bytes().split(MARKER, 1)[0].decode("utf-8")
    assert "EVAL_PROTOCOL_RETIREMENT.md" in header


def test_all_nine_amendments_are_still_on_disk() -> None:
    """九份修正案一份不少 —— **退役不等于可以开始删历史**。

    下线的是被考的那个东西；这几份记的是「当时怎么读这份卷子」，
    它们和正文一样是那段历史的一部分。
    """
    found = sorted(
        int(path.stem.rsplit("_", 1)[1])
        for path in (ROOT / "docs").glob("EVAL_PROTOCOL_AMENDMENT_*.md")
    )
    assert found == list(range(1, 10)), f"修正案少了或多了：{found}"
