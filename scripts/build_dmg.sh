#!/bin/bash
# 打 macOS 桌面包：前端产物 → PyInstaller 出 .app → 临时签名 → .dmg（落在 dist/）。
#
# L4（发版的桌面那一档，见 CLAUDE.md「命令」）。只能在 Mac 上跑（PyInstaller 不交叉编译），
# 出的包只对当前这台机器的架构（arm64 / x86_64）有效。
#
# ── 签名 ──────────────────────────────────────────────────────────────────
# 这儿做的是 **ad-hoc 签名**（`codesign -s -`）：没有 Apple 开发者证书（$99/年），也没有公证。
# 后果：本机打开没问题；**别人从网上下载之后**，Gatekeeper 会说「已损坏，无法打开」——
# 那不是坏了，是没公证。收包的人有两条路（README 写着）：右键 → 打开；或者在终端
# `xattr -dr com.apple.quarantine "/Applications/Novel Harness.app"`。真要分发就得走证书 + 公证。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
ARCH=$(uname -m)
APP=".build/dist/Novel Harness.app"
DMG="dist/NovelHarness-${VERSION}-macOS-${ARCH}.dmg"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "① 前端产物（落在包内 src/novel_harness/webui/）"
(cd frontend && npm run build --silent)

say "② 打包用的独立环境（.build/venv，Python 3.12 + pyinstaller + pywebview）"
uv venv .build/venv --python 3.12 --quiet --allow-existing
uv pip install --python .build/venv/bin/python --quiet . pyinstaller pywebview

say "③ PyInstaller → ${APP}"
rm -rf .build/dist .build/work
.build/venv/bin/pyinstaller --noconfirm --clean --distpath .build/dist --workpath .build/work desktop/NovelHarness.spec >/dev/null

say "④ 临时签名（ad-hoc）"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

say "⑤ .dmg → ${DMG}"
mkdir -p dist
rm -rf .build/dmg && mkdir -p .build/dmg
cp -R "$APP" .build/dmg/
ln -s /Applications .build/dmg/Applications
rm -f "$DMG"
hdiutil create -volname "Novel Harness" -srcfolder .build/dmg -ov -format UDZO "$DMG" >/dev/null

say "✓ 打好了：$DMG"
du -sh "$DMG" | cut -f1
