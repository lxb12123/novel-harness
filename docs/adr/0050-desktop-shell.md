# ADR 0050：桌面壳 —— 一个窗口 + 内置服务，打成 macOS `.app` / `.dmg`

- **状态**：已接受（2026-09-13，维护者提出：「对这个发包建立桌面版的 dmg」）
- **日期**：2026-09-13
- **推翻了**：不是原始需求文档，是 `docs_dev/2026-07-25-分发形态定为桌面应用走C方向.md`
  里「先不做 C（桌面包）」的时序——形态照那份定的做（PyInstaller + pywebview），只是提前了。
- **相关**：[ADR 0007](0007-disk-is-truth.md)（正文在磁盘、路径进库就不可搬家——本 ADR 定位置时
  照它算）、[ADR 0034](0034-no-command-line-surface.md)（没有命令行面；产品线就是这扇窗）、
  [ADR 0012](0012-settings-not-in-the-book.md)（连接设置不跟书走——本 ADR 沿用那个文件）

## 决策

**桌面壳 = `api/launch.py::prepare()` 加一扇窗。** 服务跑在工作线程上，主线程留给窗口
（系统要求）；窗口是系统自带的 WKWebView（pywebview），不带 Chromium，包 44 MB。

```
started = prepare(db, books_dir=…)          # 建库、绑端口（同 Web 工作台那一半）
threading.Thread(target=started.serve)      # 内置服务，daemon
webview.create_window(started.url)          # 窗口指向 http://127.0.0.1:<port>
webview.start(); started.stop()             # 窗口关了，服务体面地停
```

`launch()`（Web 工作台 / `start.command` 那条路）改成 `prepare()` 外面加一行打印和开浏览器——
两条路建库、绑端口的规矩一个字不差，只差「谁跑 `serve()`、跑在哪条线程」。

### 东西放在哪（**发出去就很难改**，所以写在这儿而不是散在代码里）

| | 位置 | 为什么 |
|---|---|---|
| 库 `book.db` | `~/Library/Application Support/Novel Harness/` | 应用数据，作者不该手碰。**不放 Documents**：iCloud「桌面与文稿」同步会去同步一个正在写的 SQLite |
| 稿子（每本书一个文件夹） | `~/Documents/Novel Harness/` | 正文在磁盘（ADR 0007）、作者要用 WPS 开它——放他找得到的地方 |
| 连接设置（key） | `~/.config/novel-harness/settings.json` | 和 Web 工作台同一份（ADR 0012），换着用不用填两遍 |
| 日志 | `~/Library/Logs/Novel Harness/novel-harness.log` | 窗口版没有终端；stdout / stderr 都接到这儿——**不接的话 PyInstaller 窗口版的两个流是 `None`，库里任何一句 print 都炸** |

### 打包（`scripts/build_dmg.sh`）

前端产物 → 独立的打包 venv（Python 3.12 + pyinstaller + pywebview，`.build/`）→ PyInstaller
按 `desktop/NovelHarness.spec` 出 `Novel Harness.app` → ad-hoc 签名 → `hdiutil` 出
`dist/NovelHarness-<版本>-macOS-<架构>.dmg`（里面一个 `.app` + 一个指向 `/Applications` 的快捷方式）。
版本号只有 pyproject.toml 一处，spec 读它。图标是作者给的那支笔尖（`icons.tsx::NIB`）放在
紫色圆角方上（`desktop/icon.svg` → `icon.icns`）。

清单里必须点名、PyInstaller 自己看不见的三样：包里的非 .py 文件（前端产物 / `migrations/*.sql` /
`model_windows.json`）、jieba 的词典、uvicorn 按字符串 import 的那几个模块和 `novel_harness.api.app`。
漏一样的症状都是「本机好好的、别人机器上一片白」，`tests/test_desktop.py` 钉着。

## 代价（承认，不粉饰）

- **没签名、没公证。** ad-hoc 签名只保证本机能开；**别人从网上下载之后**，Gatekeeper 会说
  「已损坏，无法打开」——那是没公证，不是坏了。收包的人：右键 → 打开；或者
  `xattr -dr com.apple.quarantine "/Applications/Novel Harness.app"`。真要分发得走
  Apple 开发者证书（$99/年）+ 公证，那一步没做，README 写明了。
- **只有当前架构、只有 Mac。** PyInstaller 不交叉编译：在 arm64 机器上打的包只给 arm64；
  Intel Mac、Windows 要各自在那种机器上跑一遍脚本（`release.yml` 的 matrix 还没接）。
- **没有自动更新、没有崩溃上报。** 出了问题作者能给的只有那份日志。
- **和 Web 工作台共用一份设置、但库是另一份**：Web 调试线的库在仓库旁边（`book.db`），
  桌面版的在 Application Support——同一台机器上两边看到的不是同一本书。这是有意的
  （桌面版不该碰开发者的库），但第一次会觉得「书呢」。

## 落地

- `src/novel_harness/api/launch.py`（`prepare()` / `Workbench` / `launch()`）、
  `src/novel_harness/desktop.py`、`desktop/{main.py, NovelHarness.spec, icon.svg, icon.icns}`、
  `scripts/build_dmg.sh`、`.gitignore`（`.build/`）。
- 守卫：`tests/test_serve.py`（`prepare()` 建库、绑端口、`serve()` 在工作线程上答得上话、
  `stop()` 让它回来）、`tests/test_desktop.py`（三个位置、没终端时两个流进日志、打包清单点名
  收了什么、构建脚本签名 + 出 dmg）。
- 实机验过（2026-09-13，macOS 26.3 / arm64）：`.app` 从 dmg 里开起来，内置服务在 8756 答话，
  服务的是真前端产物（不是只读原型），设置读到的是和 Web 工作台同一份，关窗口进程退出。
