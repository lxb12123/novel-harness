"""工作台启动器：建库 + 起内置服务 + 可选开浏览器。

这是原来 `nh serve` 干的四件事，从命令行提成了库函数 `launch()`——
桌面版（最终形态）的壳 import 它起服务，Web 调试线用 `python -m novel_harness.api`。
本模块**不是命令行**：不给子命令、不解析参数，所有配置走关键字参数/环境变量。
它替 `nh serve` 坐进 test_arch_guard 的 CONNECTION_OPENERS（这里是建库的装配层）。

两段：`prepare()` 建库、绑端口、交回地址和一个阻塞的 `serve()`；`launch()` 在它外面
加一行打印和「开浏览器」。桌面壳（`novel_harness.desktop`）只要前一半——它要先拿到地址
才能开窗口，而且它的服务跑在工作线程上、主线程留给窗口。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
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
        # **绑完立刻 listen**，不等 uvicorn。地址是在 `serve()` 之前就印出去 / 交出去的
        # （`launch()` 先 print 再 serve，桌面壳先开窗再 serve），这中间有一段空档：
        # 内核已经认这个端口、但还没人在听，谁这时候来连就是 ECONNREFUSED——本机快得
        # 看不见，CI 那种慢机器上 `test_serve` 就死在这一下。听起来之后连接会排在
        # backlog 里等 uvicorn 接手；uvicorn 对一个已经在 listen 的 socket 再 listen 一次无妨。
        sock.listen(128)
        return sock
    raise LaunchError(f"绑不上 {host}：{last}")


def _open_when_ready(url: str, host: str, port: int, timeout: float = 15.0) -> None:
    """等端口连得上了再开浏览器。

    `_bind` 现在绑完就 listen，所以这一步几乎立刻就过：连接先排在 backlog 里，uvicorn
    起来（约 240ms 的导入）就接手，浏览器那一次请求只是多等那一下，不会看到
    「无法访问此网站」。留着轮询是给 listen 之前那一小段和真起不来的情形兜底：
    等超时都没起来就什么都不做——终端上的那行报错才是他该看的，再弹一个空白页只是添乱。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


@dataclass(frozen=True)
class Workbench:
    """`prepare()` 交回来的：窗口该指向哪儿、一个阻塞到服务结束的 `serve()`、一个让它结束的
    `stop()`（桌面壳关窗口时叫；`launch()` 那条路靠 Ctrl-C，不叫它）。"""

    url: str
    db: Path
    serve: Callable[[], None]
    stop: Callable[[], None]


def prepare(
    db: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8756,
    books_dir: Path | None = None,
) -> Workbench:
    """建库 + 绑端口，**不起服务**：起服务是交回来的那个 `serve()` 的事（它阻塞到进程结束）。

    这一半和 `launch()` 的区别只有「谁来跑 `serve()`、跑在哪条线程上」：`launch()` 在
    主线程上直接跑；桌面壳把它放到工作线程，主线程留给窗口。库和端口的规矩一个字不差
    （`launch()` 的 docstring）。
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

    sock = _bind(host, port)
    actual = sock.getsockname()[1]
    # 0.0.0.0 是「所有网卡」，不是一个能访问的地址——别把它印进地址栏。
    browse_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{browse_host}:{actual}"

    server = uvicorn.Server(
        uvicorn.Config("novel_harness.api.app:app", host=host, port=actual, log_level="warning")
    )

    def stop() -> None:
        server.should_exit = True

    return Workbench(
        url=url, db=resolved_db, serve=lambda: server.run(sockets=[sock]), stop=stop
    )


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
    from .app import webui_built

    started = prepare(db, host=host, port=port, books_dir=books_dir)
    url = started.url

    # stdout 的全部内容（同老 nh serve 的纪律：一行机器可读的东西）。**必须 flush**：
    # 子进程/桌面壳靠读这行知道往哪儿开窗口，块缓冲会让它躺在缓冲区里等进程退出，
    # 而服务永不退出——那就永远读不到。
    print(url, flush=True)
    print(
        f"✓ 工作台起来了：{url}\n  库：{started.db}\n  停：Ctrl-C",
        file=sys.stderr,
    )
    if not webui_built():
        print(
            "⚠ 前端没构建 —— 现在服务的是 api/static 的只读原型（页面能开，但功能少一半）。\n"
            "  构建一次：cd frontend && npm install && npm run build",
            file=sys.stderr,
        )

    if open_browser:
        browse_host, actual = url.removeprefix("http://").split(":")
        threading.Thread(
            target=_open_when_ready, args=(url, browse_host, int(actual)), daemon=True
        ).start()

    started.serve()
