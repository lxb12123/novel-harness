"""M1 角色册 90% 提及度量（docs/M1_ROSTER_METRIC.md）。

工具的三条纪律各钉一条：gold 与正文逐字核对（对不上拒绝出数）、
覆盖率按实例算、没覆盖的称呼列出来（= 作者该补的别名）。
"""

from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

from novel_harness import db, project
from novel_harness.declare import Ledger
from novel_harness.graph import NodeLabel
from novel_harness.graph.sqlite_store import SqliteStoryGraph

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "roster_coverage", ROOT / "scripts" / "roster_coverage.py"
)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["roster_coverage"] = _module  # dataclass 装饰期要能在 sys.modules 里找到自己
_spec.loader.exec_module(_module)
measure = _module.measure

BOOK = (
    "第一章 初见\n"
    "\n"
    "顾清音道：「师兄。」\n"
    "萧决站在窗前。\n"
    "顾姑娘又笑了一下。\n"
    "萧决没有说话。\n"
    "\n"
    "第二章 夜话\n"
    "\n"
    "萧决道：「你来了。」\n"
    "顾清音没有说话。\n"
    "苏挽推门进来。\n"
)


def _seed(tmp_path: Path) -> str:
    """建库：三个角色（带别名），返回 project_id。"""
    conn = db.connect(tmp_path / "book.db")
    db.migrate(conn)
    pid = project.create(conn, name="测试书", root_path=str(tmp_path / "书")).id
    store = SqliteStoryGraph(conn)
    ledger = Ledger(store, conn, pid)
    ledger.declare_node(NodeLabel.CHARACTER, "顾清音", aliases=["顾姑娘"])
    ledger.declare_node(NodeLabel.CHARACTER, "萧决", aliases=["萧公子"])
    ledger.declare_node(NodeLabel.CHARACTER, "苏挽")
    conn.close()
    return pid


def _write(tmp_path: Path, gold: dict) -> tuple[Path, Path]:
    text = tmp_path / "book.txt"
    text.write_text(BOOK, encoding="utf-8")
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return text, gold_path


def test_full_coverage_passes(tmp_path: Path) -> None:
    pid = _seed(tmp_path)
    text, gold = _write(
        tmp_path,
        {
            "project_id": pid,
            "chapters": [
                {"chapter": 1, "mentions": {"顾清音": 1, "顾姑娘": 1, "萧决": 2}},
                {"chapter": 2, "mentions": {"萧决": 1, "顾清音": 1, "苏挽": 1}},
            ],
        },
    )
    result = measure(
        db_path=tmp_path / "book.db", project_id=pid, text=text, gold=gold
    )
    assert result.total == 7
    assert result.covered == 7
    assert result.ratio == 1.0
    assert result.passed


def test_missing_alias_drops_below_threshold(tmp_path: Path) -> None:
    pid = _seed(tmp_path)
    text, gold = _write(
        tmp_path,
        {
            "project_id": pid,
            "chapters": [
                {
                    "chapter": 1,
                    # 「师兄」在正文里（顾清音道：「师兄。」），但角色册里没有 → 未覆盖
                    "mentions": {"顾清音": 1, "顾姑娘": 1, "萧决": 2, "师兄": 1},
                },
            ],
        },
    )
    result = measure(
        db_path=tmp_path / "book.db", project_id=pid, text=text, gold=gold
    )
    assert result.total == 5
    assert result.covered == 4
    assert result.ratio == pytest.approx(0.8)
    assert not result.passed
    assert result.chapters[0].missing == ("师兄",)


def test_gold_mismatch_refuses_to_report(tmp_path: Path) -> None:
    pid = _seed(tmp_path)
    text, gold = _write(
        tmp_path,
        {
            "project_id": pid,
            "chapters": [{"chapter": 1, "mentions": {"顾清音": 5}}],
        },
    )
    with pytest.raises(ValueError, match="对不上"):
        measure(db_path=tmp_path / "book.db", project_id=pid, text=text, gold=gold)


def test_gold_chapter_missing_from_text_refuses(tmp_path: Path) -> None:
    pid = _seed(tmp_path)
    text, gold = _write(
        tmp_path,
        {
            "project_id": pid,
            "chapters": [{"chapter": 9, "mentions": {"萧决": 1}}],
        },
    )
    with pytest.raises(ValueError, match="没有这一章"):
        measure(db_path=tmp_path / "book.db", project_id=pid, text=text, gold=gold)


def test_gold_project_mismatch_refuses(tmp_path: Path) -> None:
    _seed(tmp_path)
    text, gold = _write(
        tmp_path,
        {"project_id": "project:别的书", "chapters": []},
    )
    with pytest.raises(ValueError, match="不一致"):
        measure(
            db_path=tmp_path / "book.db",
            project_id="project:测试书",
            text=text,
            gold=gold,
        )
