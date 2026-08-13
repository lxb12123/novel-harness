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

    uv run python scripts/refresh_model_windows.py --fetched 2026-08-13

写到 `src/novel_harness/draft/model_windows.json`（**包内**，`uv build` 自动带上）。
跑完请看 `git diff`：这是别人的数据进我们的包，**别闭眼提交**。

来源许可：MIT（BerriAI/litellm，`enterprise/` 之外的部分）。
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from novel_harness.draft.windows import SOURCE_URL, render, trim

OUT = Path(__file__).resolve().parents[1] / "src" / "novel_harness" / "draft" / "model_windows.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetched",
        required=True,
        help="今天的日期（YYYY-MM-DD）。写进快照，用来回答「这份数据有多旧」。",
    )
    fetched = parser.parse_args().fetched

    print(f"拉取 {SOURCE_URL}")
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:  # noqa: S310
        raw = json.loads(response.read().decode("utf-8"))

    # **裁剪和渲染都借库里那一份**（`draft/windows.py`）。设置页那颗「更新」按钮走的
    # 是同一个函数 —— 两处各写一遍的话，作者点出来的快照和维护者提交的会慢慢分家，
    # 而那种分家只有在「同一个模型两边窗口不一样」的时候才被发现。
    windows, prices = trim(raw)
    if not windows:
        print("裁完一个对话模型都不剩 —— 没有覆盖原来那份。")
        return 1
    OUT.write_text(render(windows, prices, fetched=fetched), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"写出 {OUT.relative_to(Path.cwd())}：{len(windows)} 个窗口 + {len(prices)} 个单价，{size:.0f} KB")
    print("**看一眼 git diff** —— 这是别人的数据进我们的包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
