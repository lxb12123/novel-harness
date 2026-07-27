"""`uvx novel-harness` 的入口。

v1 的分发叙事（ADR 0007）：一条命令，不装 Docker。**那条命令今天是 `nh serve`**——
起服务 / 建库 / 开浏览器都在那儿（cli.py），前端产物随 wheel 分发。

这里还没接上去，缺的不是代码而是一个决定：`novel-harness` 不带参数，
所以它得**自己知道作者的库放在哪**。那个默认位置一旦发出去就很难改（同 ADR 0007
里 `root_path` 存绝对路径的教训：路径进了库，库就不可搬家）。定了再接。
"""

from __future__ import annotations

import sys

from . import __version__


def main() -> None:
    if "--version" in sys.argv[1:]:
        print(__version__)
        return
    print(f"novel-harness {__version__}")
    print("起工作台：nh serve --db <你的库.db>")
    print("（本命令不带参数，还不知道该开哪个库；命令行全貌见 `nh --help`。）")


if __name__ == "__main__":
    main()
