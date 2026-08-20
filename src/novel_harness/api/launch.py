"""工作台启动器：建库 + 起内置服务 + 可选开浏览器。

这是原来 `nh serve` 干的四件事，从命令行提成了库函数 `launch()`——
桌面版（最终形态）的壳 import 它起服务，Web 调试线用 `python -m novel_harness.api`。
本模块**不是命令行**：不给子命令、不解析参数，所有配置走关键字参数/环境变量。
它替 `nh serve` 坐进 test_arch_guard 的 CONNECTION_OPENERS（这里是建库的装配层）。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ..db import connect, migrate


class LaunchError(RuntimeError):
    """启动失败（端口绑不上等）。调用方（CLI/桌面壳）自己决定怎么给人看。"""


def _bind(host: str, wanted: int) -> socket.socket:
    """绑住端口，把**绑好的** socket 交给 uvicorn（`Server.run(sockets=[...])`）。

    为什么不是「先探测一个空闲端口，再让 uvicorn 自己去绑」：探测得 bind 完再 close，
    而 close 到 uvicorn bind 之间有一个窗口，端口可能被别人抢走——那时 uvicorn 报
    「地址已被占用」，可我们已经把那个端口印在终端上、甚至已经拿它开了浏览器。
    直接把绑好的 socket 递过去，「我们知道端口号」和「端口是我们的」就成了同一件事。

    `wanted` 被占（多半是上一个服务还开着）→ 让内核挑一个空闲的，不报错退出：
    作者要的是「打开工作台」，不是「学习什么是端口占用」。
    """
    last: OSError | None = None
    for candidate in (wanted, 0):
        sock = socket.socket()  # AF_INET：v1 只支持 IPv4 字面量，IPv6 地址会在下面报错退出
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, candidate))
        except OSError as exc:  # 被占 / 地址不可用 / <1024 无权限
            sock.close()
            last = exc
            continue
        return sock
    raise LaunchError(f"绑不上 {host}：{last}")


def _open_when_ready(url: str, host: str, port: int, timeout: float = 15.0) -> None:
    """等服务真的开始 accept 了再开浏览器。

    绑好但还没 listen 的端口会**拒绝**连接，所以这里轮询到连得上为止：立刻开浏览器
    多半只换来一张「无法访问此网站」，而服务其实半秒后就起来了——作者会以为它坏了。
    等超时都没起来就什么都不做：终端上的那行报错才是他该看的，再弹一个空白页只是添乱。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


def launch(
    db: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8756,
    books_dir: Path | None = None,
    open_browser: bool = True,
) -> None:
    """起浏览器工作台：建库 + 起服务 +（可选）开浏览器。

    - `db` 不存在就建一个**空书架**（不是空书）：首屏是「开始一本书」。
    - 默认只听 127.0.0.1：这里没有任何认证（路由全部裸奔），库里是作者未发表的稿子
      和情节——`host="0.0.0.0"` 等于把它们摊在局域网上。要那么干的人得自己写出来。
    - `os.environ["NH_DB"]` 在这里设：`api/deps.py::_db_path()` 靠它找库，并且**拒绝**
      连一个不存在的路径（怕 sqlite 悄悄建出空库，给作者一张「看起来正常、全 UNKNOWN」
      的假矩阵）——所以建库必须由装配层先做，这正是本模块在 CONNECTION_OPENERS 的原因。
    - 引擎的主线程就阻塞在 uvicorn 上；真开库是 deps.py 的事。
    """
    resolved_db = db.resolve()

    conn = connect(resolved_db)
    try:
        migrate(conn)
    finally:
        conn.close()

    os.environ["NH_DB"] = str(resolved_db)
    if books_dir is not None:
        os.environ["NH_BOOKS_DIR"] = str(books_dir.resolve())

    # uvicorn（+uvloop/httptools）与 api.app（+FastAPI）加起来约 240ms 的导入开销。
    # 放在函数体里，好让「只建库不开服务器」的用法不为它买单。
    import uvicorn

    from .app import webui_built

    sock = _bind(host, port)
    actual = sock.getsockname()[1]
    # 0.0.0.0 是「所有网卡」，不是一个能访问的地址——别把它印进地址栏。
    browse_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{browse_host}:{actual}"

    # stdout 的全部内容（同老 nh serve 的纪律：一行机器可读的东西）。**必须 flush**：
    # 子进程/桌面壳靠读这行知道往哪儿开窗口，块缓冲会让它躺在缓冲区里等进程退出，
    # 而服务永不退出——那就永远读不到。
    print(url, flush=True)
    print(
        f"✓ 工作台起来了：{url}\n  库：{resolved_db}\n  停：Ctrl-C",
        file=sys.stderr,
    )
    if not webui_built():
        print(
            "⚠ 前端没构建 —— 现在服务的是 api/static 的只读原型（页面能开，但功能少一半）。\n"
            "  构建一次：cd frontend && npm install && npm run build",
            file=sys.stderr,
        )

    if open_browser:
        threading.Thread(
            target=_open_when_ready, args=(url, browse_host, actual), daemon=True
        ).start()

    config = uvicorn.Config(
        "novel_harness.api.app:app", host=host, port=actual, log_level="warning"
    )
    uvicorn.Server(config).run(sockets=[sock])
