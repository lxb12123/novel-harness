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
版本号的真值在 pyproject.toml，spec 读它（`__init__.py` 里那份拷贝由测试钉着一致）。
图标（`desktop/icon.svg` → `icon-1024.png` → `icon.icns`）：首发是作者给的那支笔尖（`icons.tsx::NIB`）
放在紫色圆角方上；**2026-09-13 作者换成白底 + 黑色实心剪影的那只翅膀、朝右**（`icons.tsx::SPREAD_WING`，
原坐标一个字没改，按竖直中轴翻一次；第一版描了边、朝左，作者看了说还原度不高——原图就是实心的）。渲法：Chrome headless 截 `--default-background-color=00000000` 的透明底
（圆角外自然透明，不用再切角），`sips` 出各档、`iconutil` 合成 icns。

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

## 补记（2026-09-13）：第一次上手暴露的三处

作者装好 dmg、导入一本书之后指出两处「不对劲」，顺手查出第三处。都不是壳的代码，
是壳换了内核（WKWebView）之后 Web 工作台里原来看不出来的东西：

1. **目录和正文的滚动条常驻不隐。** WebKit 的两条脾气（都是在 WKWebView 里实测的，
   `styles.css` 那一段注释记着证据）：① 它**认** `scrollbar-color` 却**不画**它，写上就退回
   原生常驻滚动条，而且标准属性一出现，`::-webkit-scrollbar` 那套整个作废（Chrome 两套都认、
   标准属性优先，所以以前看不出）；② 容器的 `:hover` 变了之后它**不重算**滚动条伪元素的样式，
   `.x:hover::-webkit-scrollbar-thumb` 在 WebKit 里永远透明。修法两条：伪元素那套给
   Chrome / WebKit，标准属性只写进 `@supports not selector(::-webkit-scrollbar)`（Firefox）；
   颜色只经容器上的 `--scroll-thumb` 切，伪元素只读它。`CodeEditor.tsx` 的 CM6 主题同改。
2. **角色册只见「＋」，作者以为人物只能手动加。** 右栏原来有一道闸：角色册一空，「检验规则」和
   「事件」整格换成「先去加人」。那句是 2026-08-31 之前的遗物——今天检验规则查的是字面，
   「事件」的工具栏上就是「分析本章」，**正是把人物整理进角色册的入口**，闸门把入口一起藏了。
   闸拆了；每一格空着时各说各的空态，都写明它怎么填满（角色册：手动加一条，**或**在「事件」
   中分析本章，钥匙没填先指去顶栏「AI 设置」；事件 / 通知同理）。顺带补了一个早就在的洞：
   分析跑完那一刻列表和角色册现在会自己重取（`useExtractionRun`），以前要换一次 tab 才看见。
3. **界面语言默认英文。** 不是壳的问题：这台机器系统首选语言是 en-US，`navigator.language`
   在 Chrome 里也是 en-US；Web 工作台看到中文是因为那边的 localStorage 里早就切过一次。
   桌面版第一次开是一份新的 localStorage，⚙ 里切一次就记住。不改。
4. **「分析本章」点了只闪一下，什么都不说。** 两层：① 后端 500——工作台「新建 / 导入」
   走的 `onboarding.bootstrap_project()` → `project.insert()` 从 2026-08-04 起就不写
   `validation_ruleset_state` 那一行（只有 `project.create()` 写），于是从浏览器 / 桌面版建的
   每一本书「分析本章」都 500、保存后的整理和 30 分钟扫描静默跳过；Web 调试线那本书是
   018 迁移给旧项目补的行，所以一直没露。基线行现在在 `project.insert()` 里跟 project 行同一笔
   事务写，既有的书由迁移 038 补。② 前端 POST 失败时一个字都不说，只有按钮的三态闪一下——
   现在起步失败也走那句飘一下的话（4xx 用后端那句，5xx 一律「未能开始」，不把
   `Internal Server Error` 摆上屏）。这一条不是壳的问题，是壳第一次让一本「从零建的书」
   走完整条链才露出来的。
5. **系统标题条揉进顶栏**（作者：「顶部三个操作按钮移到下面那一行去左边去……名字还有那个
   缩小栏向右挪……把顶部那个栏白色的去掉」）。`desktop.py::_unify_titlebar`：标题条透明、
   不显示标题、`FullSizeContentView`、擦掉 pywebview 涂在 `NSTitlebarContainerView` 上的底色
   （不擦透明是假的）、挂一个空 `NSToolbar` 用 `UnifiedCompact`——标题条撑成 38pt、三颗窗口
   按钮垂直居中到 19pt；前端顶栏在 `html.desktop` 下也是 38px、左边让 78px（`desktop.ts`，
   壳的地址带 `?desktop=1` 认出自己）。命中测试实测这一带的点击照样落到 WKWebView。拖窗口
   WebKit 不认 `-webkit-app-region`，顶栏空白处按下鼠标叫壳的 `drag()`（pywebview 的 js_api →
   主线程 `performWindowDragWithEvent:`，同 Tauri 的 `startDragging`）；**双击**顶栏空白处叫
   `zoom()`——读系统「连按窗口标题栏时」那一档（`AppleActionOnDoubleClick`：Minimize / Fill /
   其余缩放）代原生标题条办这件事，双击在 mousedown 的第二下上认（第一下已经把窗口拖起来了，
   `dblclick` 到不了 WebKit）。同日作者嫌中间那组白托盘和顶栏一样高：整组收成 26px
   （图标 18px），38px 的顶栏上下各留 6px。
