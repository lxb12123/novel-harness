"""桌面壳：一个窗口 + 内置服务（macOS `.app`，打成 `.dmg` 发给作者）。

它就是 `api/launch.py::prepare()` 加一扇窗（`docs_dev/2026-07-25-分发形态定为桌面应用走C方向.md`
定的形态：PyInstaller + pywebview，用系统自带的 WKWebView，不带 Chromium）：

    started = prepare(db)                      # 建库、绑端口
    threading.Thread(target=started.serve)     # 服务在工作线程上
    webview.create_window(started.url)         # 窗口在主线程上（系统要求）

── 东西放在哪（ADR 0050）──────────────────────────────────────────────────

| | 位置 | 为什么 |
|---|---|---|
| 库（`book.db`） | `~/Library/Application Support/Novel Harness/` | 应用数据，作者不该手碰；**不放 Documents**——iCloud 桌面与文稿同步会去同步一个正在写的 SQLite |
| 稿子（每本书一个文件夹） | `~/Documents/Novel Harness/` | 正文在磁盘（ADR 0007）、作者要用 WPS 开它——放他找得到的地方 |
| 连接设置（key） | `~/.config/novel-harness/settings.json` | 和 Web 工作台同一份（`settings.py`），换着用不用填两遍 |
| 日志 | `~/Library/Logs/Novel Harness/novel-harness.log` | 窗口版没有终端，报错得有地方落 |

**这几个位置一旦发出去就很难改**（路径进了库，库就不可搬家——ADR 0007 的教训），所以定在
这儿、写成 ADR，而不是散在代码里。

── 没有终端这件事 ──────────────────────────────────────────────────────────

PyInstaller 的窗口版把 `sys.stdout` / `sys.stderr` 置成 `None`，库里任何一句 `print` 都会
炸成 AttributeError（uvicorn 的日志、`launch()` 的那行 URL）。所以第一件事是把两个流接到
日志文件上——那也是作者能发给维护者的唯一东西。
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

APP_NAME = "Novel Harness"


def data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def books_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / APP_NAME / "novel-harness.log"


def _redirect_output(path: Path) -> None:
    """stdout / stderr 都落到日志（追加，行缓冲）。窗口版两个流是 `None`，不接上就没处报错。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 —— 活到进程结束
    sys.stdout = stream
    sys.stderr = stream


def _fail(title: str, detail: str) -> None:
    """起不来时给作者一个能看见的框，不是静静地闪退。`webview` 本身没起来就只能进日志。"""
    print(f"{title}\n{detail}", file=sys.stderr, flush=True)
    try:
        import webview

        window = webview.create_window(APP_NAME, html=f"<h2>{title}</h2><pre>{detail}</pre>", width=640, height=360)
        del window
        webview.start()
    except Exception:  # noqa: BLE001 —— 连报错框都开不了，日志里已经有了
        pass


def main() -> int:
    _redirect_output(log_path())
    try:
        from .api.launch import prepare

        data_dir().mkdir(parents=True, exist_ok=True)
        books_dir().mkdir(parents=True, exist_ok=True)
        started = prepare(data_dir() / "book.db", books_dir=books_dir())
        print(f"{started.url}\n  库：{started.db}\n  稿子：{books_dir()}", flush=True)
        threading.Thread(target=started.serve, name="novel-harness-server", daemon=True).start()

        import webview

        webview.create_window(APP_NAME, started.url, width=1440, height=900, min_size=(960, 640))
        # 窗口关掉就结束：让服务体面地停（正在写的那一笔落完），线程本身是 daemon，跟着进程走。
        webview.start()
        started.stop()
        return 0
    except Exception:  # noqa: BLE001 —— 顶层：任何没接住的都要落进日志并给作者看一眼
        detail = traceback.format_exc()
        _fail("工作台没有起来", detail + f"\n日志：{log_path()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
