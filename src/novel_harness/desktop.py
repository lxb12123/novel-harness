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
from typing import Any

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


DESKTOP_QUERY = "desktop=1"
"""窗口指向的地址后面带的一句：前端看见它就给 `<html>` 挂 `desktop`——顶栏从那一刻起
知道自己顶上没有系统标题条了（左边要给三颗窗口按钮让位，`styles.css` 的 `html.desktop`）。
用查询串而不是 UA：UA 要整串换掉（CM6 那类库靠它认引擎），查询串刷新也还在。"""


def _unify_titlebar(window: Any) -> None:
    """macOS：把系统标题条揉进工作台自己的顶栏（作者 2026-09-13：「顶部三个操作按钮移到
    下面那一行去左边去……名字还有那个缩小栏向右挪……把顶部那个栏白色的去掉」）。

    四件事，缺一样都不成：
    1. 标题条透明、不显示标题——工作台的顶栏从窗口最顶上开始画；
    2. `FullSizeContentView`——内容视图伸到标题条底下，否则透明了也只是露出一条空白；
    3. **擦掉 pywebview 涂在标题条容器上的那层底色**（它非 frameless 分支里给
       `NSTitlebarContainerView` 涂了 windowBackgroundColor）——不擦，透明是假的，那一条
       还是灰的、把顶栏盖住半截；
    4. 挂一个空的 `NSToolbar`、`UnifiedCompact` 样式——标题条从 28pt 长到 **38pt**，三颗窗口
       按钮也跟着**垂直居中到 19pt**；前端那条顶栏正好也是 38px、内容居中，两边就对齐了。
       不挂的话按钮停在 14pt，跟 38px 顶栏里的图标差 5px，看着像没对齐。

    命中测试实测过：这一带的点击照样落到 WKWebView（三颗按钮除外），顶栏上的按钮都按得到。
    拖窗口 WebKit 不会替我们做（它不认 `-webkit-app-region`），顶栏在鼠标按下时叫
    `_ShellApi.drag`。只在 macOS 上做；别的平台没有这条标题栏可揉。
    """
    if sys.platform != "darwin":
        return
    from AppKit import (
        NSColor,
        NSToolbar,
        NSWindowStyleMaskFullSizeContentView,
        NSWindowTitleHidden,
        NSWindowToolbarStyleUnifiedCompact,
    )

    win = window.native
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(NSWindowTitleHidden)
    win.setStyleMask_(win.styleMask() | NSWindowStyleMaskFullSizeContentView)
    win.contentView().superview().subviews().lastObject().setBackgroundColor_(NSColor.clearColor())
    toolbar = NSToolbar.alloc().initWithIdentifier_("novel-harness-titlebar")
    toolbar.setShowsBaselineSeparator_(False)
    win.setToolbar_(toolbar)
    win.setToolbarStyle_(NSWindowToolbarStyleUnifiedCompact)


class _ShellApi:
    """页面能叫到的那几下（`window.pywebview.api.*`）。今天只有拖窗口。"""

    def __init__(self) -> None:
        self.window: Any = None

    def drag(self) -> None:
        """顶栏空白处按下鼠标 → 拿当前这一下事件开始拖窗口（同 Tauri 的 `startDragging`）。
        pywebview 在别的线程上调 API，`performWindowDragWithEvent:` 得在主线程上叫。"""
        if sys.platform != "darwin" or self.window is None:
            return
        from AppKit import NSApp
        from PyObjCTools import AppHelper

        win = self.window.native
        AppHelper.callAfter(lambda: win.performWindowDragWithEvent_(NSApp.currentEvent()))

    def zoom(self) -> None:
        """双击顶栏空白处 → 照系统设置里「连按窗口标题栏时」那一档办（作者 2026-09-13：
        「双击应用顶部，他没有按照 mac 的规则去适配屏幕」）。原生标题条这一下是系统自己做的，
        标题条揉进顶栏之后得由我们代做：读 `AppleActionOnDoubleClick`——
        `Minimize` 最小化，`Fill` 铺满可见屏幕（macOS 15 起的那一档，不进全屏），
        其余（`Maximize` / 没设）= 缩放（`performZoom:`，再按一次缩回去）。"""
        if sys.platform != "darwin" or self.window is None:
            return
        from AppKit import NSUserDefaults
        from PyObjCTools import AppHelper

        win = self.window.native

        def act() -> None:
            action = NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
            if action == "Minimize":
                win.performMiniaturize_(None)
            elif action == "Fill":
                win.setFrame_display_animate_(win.screen().visibleFrame(), True, True)
            else:
                win.performZoom_(None)

        AppHelper.callAfter(act)


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

        api = _ShellApi()
        window = webview.create_window(
            APP_NAME,
            f"{started.url}/?{DESKTOP_QUERY}",
            js_api=api,
            width=1440,
            height=900,
            min_size=(960, 640),
        )
        api.window = window
        # 露面之前就把标题条揉进顶栏：`before_show` 在主线程上同步跑（pywebview 的
        # `Event(should_lock=True)`），NSWindow 那时已经建好、还没显示，作者看不到那一下切换。
        window.events.before_show += lambda: _unify_titlebar(window)
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
