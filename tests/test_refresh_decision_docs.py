"""保存触发刷新闭环的四份 ADR 必须存在、已接受，且钉住四条产品裁决。

本测试先于四份 ADR 写成：它红的时候就是「决策还没冻结」的时候。
它不验证架构实现的正确性，只验证**决策文档先于代码**这件事在文件系统上可查。
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr"


@pytest.mark.parametrize(
    ("number", "filename", "must_contain"),
    [
        (
            "0029",
            "0029-save-triggers-snapshot-refresh.md",
            ("source_snapshot_id", "已接受"),
        ),
        (
            "0030",
            "0030-versioned-summaries-and-advisory-reconciliation.md",
            ("核对不阻断", "已接受"),
        ),
        (
            "0031",
            "0031-auto-alias-with-reversible-correction.md",
            ("先纠错后自动", "已接受"),
        ),
        (
            "0032",
            "0032-reversible-auto-canon-edges.md",
            ("LOCATED_AT", "HAS_STATE", "RELATED_TO", "无纠错入口不得 auto-Canon", "已接受"),
        ),
    ],
)
def test_snapshot_refresh_adr_exists_and_is_accepted(
    number: str, filename: str, must_contain: tuple[str, ...]
) -> None:
    path = ADR / filename
    assert path.is_file(), f"{number} 的 ADR 文件不存在：{path}"
    body = path.read_text(encoding="utf-8")
    for needle in must_contain:
        assert needle in body, f"{number} 缺少关键内容「{needle}」"


def test_adr_index_lists_snapshot_refresh_decisions() -> None:
    index = (ADR / "README.md").read_text(encoding="utf-8")
    for filename in (
        "0029-save-triggers-snapshot-refresh.md",
        "0030-versioned-summaries-and-advisory-reconciliation.md",
        "0031-auto-alias-with-reversible-correction.md",
        "0032-reversible-auto-canon-edges.md",
    ):
        assert filename in index, f"ADR 索引没列 {filename}"
