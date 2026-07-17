"""`uvx novel-harness` 的入口：起本地 API + 开浏览器。

v1 的分发叙事（ADR 0007）：一条命令，不装 Docker。目前是 stub——
Day 1 只验证打包路径通不通，不验证它有没有用。
"""

from __future__ import annotations

import sys

from . import __version__


def main() -> None:
    if "--version" in sys.argv[1:]:
        print(__version__)
        return
    print(f"novel-harness {__version__}")
    print("Web 面板尚未实现（M0 后半）。命令行请用 `nh --help`。")


if __name__ == "__main__":
    main()
