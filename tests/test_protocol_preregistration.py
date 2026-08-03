"""Guards for the additive M2 preregistration documents.

The body of ``EVAL_PROTOCOL.md`` is historical evidence.  New amendments may only
change its informational header, while current documentation must point readers at
the complete amendment set.
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
            "README.md",
            "协议本体之外还有八份修正案",
            "协议本体之外还有七份修正案",
        ),
        (
            "CLAUDE.md",
            "这件事已经做过八次",
            "这件事已经做过七次",
        ),
        (
            "docs/ARCHITECTURE.md",
            "它此后已经被改过八次",
            "它此后已经被改过七次",
        ),
        (
            "src/novel_harness/eval/__init__.py",
            "加八份修正案",
            "加七份修正案",
        ),
        (
            "src/novel_harness/cli.py",
            "+ 八份修正案",
            "+ 七份修正案",
        ),
        (
            "synth/booklet.toml",
            "八份修正案一个字都没碰",
            "七份修正案一个字都没碰",
        ),
    ],
)
def test_current_references_name_eight_amendments(
    relative_path: str, current: str, stale: str
) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert current in text
    assert stale not in text
