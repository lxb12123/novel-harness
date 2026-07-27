"""把 `test_api.py` 的 `book` / `client` 提升成全局 fixture。

`test_frontend_contract.py` 必须吃**同一个库**：契约测试的价值在于「前端 fixture 来自
真后端」，如果它自己另造一份数据，冻出来的就只是另一份手写数据——两份手写的东西互相
验证，正是这条缝原本的病。

只做 re-export，不搬定义：`BOOK` / `TWIST` / `PLOT_NOTE` 这些常量是 `test_api.py`
自己断言里要用的（「出现即泄漏」），搬走会让那些断言去 import 一个 conftest，
而 conftest 是 pytest 的装配点，不该变成别人的数据源。
"""

from __future__ import annotations

from test_api import book, client

__all__ = ["book", "client"]
