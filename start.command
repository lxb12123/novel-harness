#!/bin/bash
#
# 双击我。（macOS：.command 后缀会让 Finder 用 Terminal 打开并执行本文件。）
#
# ── 这是 A 档，不是 C 档 ──────────────────────────────────────────────────
# 真正的桌面包（.dmg，用户不知道有 Python）等两个前置：M2 kill-gate 出结果、
# 以及真有人在用。在那之前这个脚本是过渡形态——它服务的是「愿意多点一下、
# 但不想背命令」的作者，不是完全不碰终端的人。这条路子被 A1111 那一圈验证过无数遍。
# 完整取舍见 docs_dev/2026-07-25-分发形态定为桌面应用走C方向.md
#
# ── 库放在哪 ──────────────────────────────────────────────────────────────
# 就放在本脚本旁边（book.db）。**这不是在替「作者的库默认放哪」那个决定拍板**——
# 会双击这个文件的人手上有整个仓库目录，库跟着它走是此刻最不意外的选择。
# 真正的默认位置要等桌面包那一步再定，那个一旦发出去就很难改
# （同 ADR 0007 里 root_path 存绝对路径的教训：路径进了库，库就不可搬家）。

set -uo pipefail

# 双击时 cwd 是用户的家目录，不是脚本所在处。所有相对路径都要先落到这儿。
cd "$(dirname "$0")" || exit 1

DB="$PWD/book.db"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die() {
  printf '\n\033[31m✗ %s\033[0m\n\n' "$*"
  # 双击进来的窗口一 exit 就关，报错会一闪而过。停在这儿等他读完。
  read -r -p "按回车关闭这个窗口。" _
  exit 1
}

say "Novel Harness"

# ── 1. uv ────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  # 常见情形：装过 uv，但双击起的这个 shell 没读到把它加进 PATH 的那份配置。
  for candidate in "$HOME/.local/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    [ -x "$candidate" ] && export PATH="$(dirname "$candidate"):$PATH" && break
  done
fi
if ! command -v uv >/dev/null 2>&1; then
  die "没找到 uv（Python 的环境管理器，这个工具靠它跑）。
  装它：打开「终端」，粘贴这一行回车，然后重新双击本文件——

      curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ── 2. Python 依赖 ────────────────────────────────────────────────────────
say "① 检查依赖…"
uv sync --quiet || die "依赖装不上。把上面的报错整段发给维护者。"

# ── 3. 前端产物 ──────────────────────────────────────────────────────────
# 没有它 nh serve 照样起得来，只是降级成 api/static 的只读原型——那是个不报错的
# 降级，所以这儿要么把它构建出来，要么明说降级了，不能默默过去。
if [ ! -f "src/novel_harness/webui/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    say "② 第一次运行，构建界面（大约一分钟，只有这一次）…"
    (cd frontend && npm install --silent && npm run build) \
      || die "界面构建失败。把上面的报错整段发给维护者。"
  else
    warn "⚠ 没找到 npm，跳过界面构建。"
    warn "  接下来能打开，但看到的是功能少一半的只读原型。"
    warn "  要完整界面：装 Node.js（https://nodejs.org），然后重新双击本文件。"
  fi
fi

# ── 4. 起 ────────────────────────────────────────────────────────────────
say "③ 打开工作台…"
echo "  库：$DB"
echo "  关掉它：在这个窗口按 Control-C（直接关窗口也行）"
echo

# nh serve 自己会建库、挑端口（8756 被占就换）、等服务真起来了再开浏览器。
exec uv run nh serve --db "$DB"
