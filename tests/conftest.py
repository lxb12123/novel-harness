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
from pathlib import Path
from typing import NoReturn

import pytest

from test_api import book, client

__all__ = ["book", "client"]


@pytest.fixture(autouse=True)
def _no_capability_discovery_over_the_wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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

    # Task 16：后台 dispatcher 的轮询线程在每个 TestClient lifespan 里会真的
    # 去扫库并可能 claim 测试 pre-seed 的 attempt——那把套件搅成不确定。套件
    # 默认关掉它；只有 `test_background_recovery.py` 显式设回 1 来验恢复。
    monkeypatch.setenv("NH_BACKGROUND_RUNTIME", "0")
    # **测试不许读开发者机器上那份真设置**（`~/.config/novel-harness/settings.json`）。
    # 2026-09-13 之前这儿没有这一道：本机有钥匙的开发者跑全绿，CI 上 8 条红——那 8 条
    # 一直在靠维护者自己的模型配置过关，而没有一处说出来。每条测试拿一份自己的空文件；
    # 要「配好了」的测试自己 setenv `NH_LLM_*`（假值即可，套件里没有一次真出网）。
    monkeypatch.setenv("NH_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for name in ("NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

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


@pytest.fixture
def configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """「模型服务配好了」的最小样子：三样都在，路由是登记过的（`api.deepseek.com`，
    能力表不用出网就解析得出）。只管配置，**不管模型答什么**——要桩的测试自己
    monkeypatch `deps.complete`。上面那道隔离让每条测试默认没配好，这是它的反面。"""
    monkeypatch.setenv("NH_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("NH_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NH_LLM_API_KEY", "sk-test-not-a-real-key")
