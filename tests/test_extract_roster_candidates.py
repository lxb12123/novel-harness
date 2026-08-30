"""scripts/extract_roster_candidates.py —— 角色册候选抽取（零语义，草稿）。

钉三件事：说话人标签位置的名字被数到、普通词（不知）不进、状语粘连
（贾环颔首道 → 贾环颔首）被机械过滤。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "extract_roster_candidates", ROOT / "scripts" / "extract_roster_candidates.py"
)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["extract_roster_candidates"] = _module
_spec.loader.exec_module(_module)
extract = _module.extract

TEXT = (
    "第一章 测试\n"
    "贾环道：「此剑无名。」\n"
    "王熙凤说道：“把他带下去。”\n"
    "贾环颔首道：「是。」\n"
    "不知道「他在想什么」\n"
    "贾环道：「再来。」\n"
)


def test_speaker_tag_names_are_counted() -> None:
    counter = extract(TEXT)
    assert counter["贾环"] == 2
    assert counter["王熙凤"] == 1


def test_adverb_glued_names_are_filtered() -> None:
    counter = extract(TEXT)
    assert "贾环颔首" not in counter


def test_common_word_is_stopped() -> None:
    counter = extract(TEXT)
    assert "不知" not in counter
