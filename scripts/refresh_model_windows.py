#!/usr/bin/env python3
"""把 LiteLLM 的公共模型表裁成一份「模型名 → 上下文窗口」的快照。

## 为什么要这份快照

`max_context_tokens` 是能力表里**唯一测不起**的那个数（要发一个百万 token 的请求
才知道被不被拒，还可能被静默截断），而它决定**上文给作者 800 字还是 40,000 字** ——
两者差 50 倍，且变短了不报错，只显得「模型忽然变笨」。

手抄不是办法：2026-08-13 抄 Anthropic 抄了 13 条，下个季度就旧了；国内那几家的
文档站在维护者机器上还够不到。而这个生态**已经有一份共享真相源**：
`BerriAI/litellm` 的 `model_prices_and_context_window.json`（3,003 个模型，
社区持续提 PR，Aider 直接用它）。

## ⚠️ 只取 `max_input_tokens`，**不取输出上限**

实测对照（2026-08-13）：

    deepseek-v4-flash    我们（官方文档）out=384,000    公共表 out=8,192   ← 差 47 倍

8,192 是上一代的值。照它填的话，一次整章起草会被截成一小段。
**社区维护的表在「新模型的输出上限」这一维是滞后的**，而上下文窗口那一维九条全中。
所以这份快照只裁一列，输出上限仍然以本仓自己查证过的注册表为准。

## 用法（维护者，不是作者）

    uv run python scripts/refresh_model_windows.py

写到 `src/novel_harness/draft/model_windows.json`（**包内**，`uv build` 自动带上）。
跑完请看 `git diff`：这是别人的数据进我们的包，**别闭眼提交**。

来源许可：MIT（BerriAI/litellm，`enterprise/` 之外的部分）。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

SOURCE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
OUT = Path(__file__).resolve().parents[1] / "src" / "novel_harness" / "draft" / "model_windows.json"
SCHEMA = "nh-model-windows-v1"


def main() -> int:
    print(f"拉取 {SOURCE_URL}")
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:  # noqa: S310
        raw: dict[str, Any] = json.loads(response.read().decode("utf-8"))

    windows: dict[str, int] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue  # `sample_spec` 那种模板行
        value = entry.get("max_input_tokens")
        # **只收读得懂的正整数**（同 `provider._usage_count` 的立场）：
        # 表里混着 `null`、字符串、还有 `sample_spec` 那种模板行。
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            continue
        windows[name] = value

    payload = {
        "schema": SCHEMA,
        "source_url": SOURCE_URL,
        "source_license": "MIT (BerriAI/litellm)",
        "note": (
            "只裁了 max_input_tokens 一列。输出上限有意不取 —— 实测 deepseek-v4 那一档"
            "公共表写的是 8,192，而官方文档是 384,000（差 47 倍）。"
        ),
        "windows": dict(sorted(windows.items())),
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    size = OUT.stat().st_size / 1024
    print(f"写出 {OUT.relative_to(Path.cwd())}：{len(windows)} 个模型，{size:.0f} KB")
    print("**看一眼 git diff** —— 这是别人的数据进我们的包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
