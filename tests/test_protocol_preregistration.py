"""Guards for the additive M2 preregistration documents.

The body of ``EVAL_PROTOCOL.md`` is historical evidence.  New amendments may only
change its informational header, while current documentation must point readers at
the complete amendment set.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "EVAL_PROTOCOL.md"
MARKER = "## 1. 被证伪的命题\n".encode()


def _body(data: bytes) -> bytes:
    """Return the frozen protocol body, rejecting a missing or duplicate marker."""
    assert data.count(MARKER) == 1
    return data[data.index(MARKER) :]


def test_frozen_protocol_body_still_matches_0393088() -> None:
    baseline = subprocess.check_output(
        ["git", "show", "0393088:docs/EVAL_PROTOCOL.md"], cwd=ROOT
    )
    assert _body(PROTOCOL.read_bytes()) == _body(baseline)


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
            "协议本体之外还有五份修正案",
            "协议本体之外还有四份修正案",
        ),
        (
            "CLAUDE.md",
            "这件事已经做过五次",
            "这件事已经做过四次",
        ),
        (
            "docs/ARCHITECTURE.md",
            "它此后已经被改过五次",
            "它此后已经被改过四次",
        ),
        (
            "src/novel_harness/eval/__init__.py",
            "加五份修正案",
            "加四份修正案",
        ),
        (
            "src/novel_harness/cli.py",
            "+ 五份修正案",
            "+ 四份修正案",
        ),
        (
            "synth/booklet.toml",
            "五份修正案一个字都没碰",
            "四份修正案一个字都没碰",
        ),
    ],
)
def test_current_references_name_five_amendments(
    relative_path: str, current: str, stale: str
) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert current in text
    assert stale not in text
