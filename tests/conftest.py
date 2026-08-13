"""把 `test_api.py` 的 `book` / `client` 提升成全局 fixture。

`test_frontend_contract.py` 必须吃**同一个库**：契约测试的价值在于「前端 fixture 来自
真后端」，如果它自己另造一份数据，冻出来的就只是另一份手写数据——两份手写的东西互相
验证，正是这条缝原本的病。

只做 re-export，不搬定义：`BOOK` / `TWIST` / `PLOT_NOTE` 这些常量是 `test_api.py`
自己断言里要用的（「出现即泄漏」），搬走会让那些断言去 import 一个 conftest，
而 conftest 是 pytest 的装配点，不该变成别人的数据源。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NoReturn

import pytest

from test_api import book, client

__all__ = ["book", "client"]


@pytest.fixture(autouse=True)
def _no_capability_discovery_over_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """整个测试套件不许真的去问端点的能力表（`draft/discovery.py`）。

    那个模块**默认会发 HTTP**，而它挂在 `resolve_with_discovery` 上、后者又挂在
    起草/抽取/总结/agent 四条装配路径上。少了这道网，一条测试只要把 `base_url`
    写成 `openrouter.ai` 就会**真的出网**——于是套件的绿灯开始依赖网络和一个
    第三方的可用性，而 CI 上表现为随机红。

    **它也顺手钉住了「登记过的路由一次网都不发」**：现有那 8 条精确路由在
    `resolve_with_discovery` 里就短路了，所以它们照旧跑得过这道网。哪天有人
    把短路删掉，整个套件会在这儿炸开，而不是安静地多出几百次网络往返。

    需要走发现逻辑的测试，自己往 `discover(fetch=...)` 注入假的那一层。
    """
    from novel_harness.draft import discovery

    def _refuse(url: str) -> NoReturn:
        raise AssertionError(
            f"测试里不许真发能力发现请求（{url}）——给 discover(fetch=...) 注入一个假的。"
        )

    monkeypatch.setattr(discovery, "_http_get_json", _refuse)
    # **两头都清**：缓存是模块级的，一条测试注入的假响应会活到下一条测试里去，
    # 而那种串味在这个模块上格外阴——第二条测试会「问不到网却拿到能力」。
    discovery.clear_cache()
    yield
    discovery.clear_cache()
