# -*- mode: python ; coding: utf-8 -*-
# 桌面壳的打包清单（PyInstaller）。`scripts/build_dmg.sh` 跑它；产物是 `Novel Harness.app`。
#
# 三件要交代清楚的事：
# 1. **包里的非 .py 文件**：前端产物（`novel_harness/webui/`）、迁移脚本（`migrations/*.sql`）、
#    模型窗口表（`draft/model_windows.json`）、jieba 的词典——PyInstaller 只跟着 import 走，
#    这些它看不见，得点名收进来（`collect_data_files`）。
# 2. **按字符串 import 的模块**：uvicorn 用 `"novel_harness.api.app:app"` 找 app、按配置挑
#    事件循环 / HTTP 协议实现，anyio 按名字挑后端——静态分析都跟不到，`hiddenimports` 点名。
# 3. **窗口版没有终端**：`console=False`，stdout / stderr 由 `novel_harness.desktop` 接到日志文件。

import os
import tomllib

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 版本号只有一处：pyproject.toml。这儿读它，不抄一份进 Info.plist。
with open(os.path.join(SPECPATH, "..", "pyproject.toml"), "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

datas = collect_data_files("novel_harness") + collect_data_files("jieba")
hiddenimports = (
    collect_submodules("novel_harness")
    + collect_submodules("uvicorn")
    + collect_submodules("anyio")
    + ["webview.platforms.cocoa"]
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Novel Harness",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="icon.icns",
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="Novel Harness")
app = BUNDLE(
    coll,
    name="Novel Harness.app",
    icon="icon.icns",
    bundle_identifier="dev.novelharness.app",
    info_plist={
        "CFBundleDisplayName": "Novel Harness",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        # WKWebView 里的页面要连本机 127.0.0.1 上的内置服务；不是 https，得放行。
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSMinimumSystemVersion": "12.0",
    },
)
