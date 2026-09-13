# Novel Harness

> Novel Harness 是给小说作者用的 AI 写作 harness。
> Novel Harness is an AI writing harness for novelists.

## 架构设计 / Architecture

![Novel Harness 架构图](docs/architecture.svg)

## 安装 / Install

**macOS（Apple 芯片）**：下载 `NovelHarness-<版本>-macOS-arm64.dmg`，打开后把 `Novel Harness`
拖进 `Applications`。第一次打开时 macOS 会说「已损坏，无法打开」——**那不是坏了，是这个包没有
经过 Apple 公证**（没买开发者证书）。两个办法任选一个：

- 在 `Applications` 里**右键 → 打开**，再点一次「打开」；
- 或者打开「终端」粘贴这一行回车：`xattr -dr com.apple.quarantine "/Applications/Novel Harness.app"`

打开之后：书放在 `~/Documents/Novel Harness/`，模型连接在窗口右上角 ⚙ 里填（填一次就好）。
出问题时把 `~/Library/Logs/Novel Harness/novel-harness.log` 发给维护者。

**macOS (Apple silicon)**: download `NovelHarness-<version>-macOS-arm64.dmg`, open it and drag
`Novel Harness` into `Applications`. The first launch is blocked with "damaged and can't be
opened" — the app is not notarized (no Apple developer certificate), nothing is broken.
Either right-click the app → Open → Open, or run
`xattr -dr com.apple.quarantine "/Applications/Novel Harness.app"` in Terminal once.

自己打包 / Build it yourself: `bash scripts/build_dmg.sh`（要在 Mac 上跑；出的包只对当前架构有效）。

## 作者指南 / Author Guide

待补 · Coming soon.
