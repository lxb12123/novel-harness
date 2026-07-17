#!/usr/bin/env bash
#
# 心跳（PLAN §8 Day 5 下午 / ARCHITECTURE §11「心跳」）。
#
#   端到端还通着 = 一个每天可见的布尔值。这个脚本的退出码就是那个布尔值。
#
# ── 它量的是接缝，不是功能 ────────────────────────────────────────────────
#
# 单测量的是每一层自己对不对，而 M0/M1/M2 是**按层划分**的里程碑——层与层的接缝
# （作者声明的 cast 能不能被 knowledge_matrix 消费、state_at 返回的边能不能被规则消费）
# 按计划要到 M3 才第一次接上。接缝正是最容易崩、且崩了最久没人知道的地方。
# 所以下面走的是一条完整的真链路：
#
#   作者声明（seed_demo.py，M1 声明层的替身）
#     → 真的 SQLite 文件 → SqliteStoryGraph → resolve_cast → knowledge_matrix
#     → 终端上那个框                                       ← README:20-31 逐格
#   同一个库 → parse_scenes → run_checks → Issue + (para_index, quote_text, k)
#
# **每一步都断言输出。** 只跑命令、只看退出码是不够的：这条链路上大多数失败形态的
# 自然产物是「一张漂亮的空表 + exit 0」（§10 约束 8）。一个打印空面板然后 exit 0 的
# demo 比没有 demo 更糟——它还提供安全感。
#
# ── ⚠️ 这里用的不是真书 ───────────────────────────────────────────────────
#
# PLAN 的原始草图第一行是 `nh import tests/fixtures/real_novel.txt`。**那个文件不存在，
# 而且它的缺席不是疏忽：PLAN §8 Day 3 的「真书切出章数 = 目录数」至今 BLOCKED，
# 没有人提供过那本 300 章的 TXT。** 这里退而用手写的 3 章 fixture，它证明的只是
# 「切章器在手写的脏数据上不切歪」——**对真书的分卷重启、加更章、几十种卷标题写法，
# 这个脚本一个字都没说，别拿它的绿当覆盖率。**
#
# 换真书是改这两个环境变量的事，脚本本身不用动：
#
#   NH_DEMO_BOOK=~/books/real_novel.txt NH_DEMO_CHAPTERS=317 bash scripts/demo.sh
#
# 章数要跟着一起给：一个不校验章数的 import 步骤等于没跑（切成 1 章也会绿）。

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" # 任意 cwd 可跑；uv run 要在项目里，下面的路径一律绝对或相对 ROOT

BOOK="${NH_DEMO_BOOK:-$ROOT/tests/fixtures/demo_novel.txt}"
CHAPTERS="${NH_DEMO_CHAPTERS:-3}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT # 库和稿子只活在 tmp 里：心跳不许碰作者的文件
DB="$TMP/demo.db"

# ══════════════════════════════════════════════════════════════════════════
# 断言
# ══════════════════════════════════════════════════════════════════════════

OUT=""
STATUS=0

# `|| STATUS=$?` 让命令进 OR 表，于是 set -e 不在这里开火——失败由下面的断言判决，
# 而不是让脚本在一条**预期会失败**的命令上无声退出。
_run() {
  printf '  $ %s\n' "$*"
  OUT=""
  STATUS=0
  OUT="$("$@" 2>&1)" || STATUS=$?
}

step() { printf '\n▸ %s\n' "$1"; }

fail() {
  printf '\n✗ 心跳断了：%s\n' "$1" >&2
  printf -- '─── 上一条命令的输出（退出码 %s）───\n%s\n' "$STATUS" "$OUT" >&2
  exit 1
}

# case 的模式里 "$1" 是引号括起来的，所以 [ ] * 一律当字面量——R4 的 `[R4]` 靠这个。
has() { case "$OUT" in *"$1"*) ;; *) fail "输出里应当有「$1」" ;; esac; }
lacks() { case "$OUT" in *"$1"*) fail "输出里不该有「$1」" ;; esac; }
exits_zero() { [ "$STATUS" -eq 0 ] || fail "$1 应当成功，实得退出码 $STATUS"; }
exits_nonzero() { [ "$STATUS" -ne 0 ] || fail "$1 应当非 0 退出，实得 0"; }

# ══════════════════════════════════════════════════════════════════════════
# 1. 切章
# ══════════════════════════════════════════════════════════════════════════

step "nh import —— 切章 + 对账"
_run uv run nh import "$BOOK"
has "切出 $CHAPTERS 章"

# ⚠️ **这里在钉一个「还没做」，而不是一个 bug。** `nh import` 今天必须非 0 退出：
# 图层没有建节点的路径，chapter 表的主键又是 node.id，所以它落不了库；一个安静地
# 不落库的 import 会让后面 panel 的「没有花名册」看起来像 panel 的错。
# **M1 的建节点方法落地那天，这三行会红——那正是它该做的事**（PLAN §8：
# 「一开始全是 stub，之后每做完一块换掉一个 stub」）。届时把它换成断言库里真有 N 章。
exits_nonzero "nh import（落库未实现）"
has "落库没做"

# ══════════════════════════════════════════════════════════════════════════
# 2. 声明（M1 声明层的替身）
# ══════════════════════════════════════════════════════════════════════════

step "seed_demo.py —— 作者声明 3 个人 / 2 条秘密 / 4 条边"
printf '  $ uv run python scripts/seed_demo.py %s\n' "$DB"
if ! PID="$(uv run python "$ROOT/scripts/seed_demo.py" "$DB")"; then
  OUT="（诊断在上面的 stderr 里）"
  fail "seed_demo.py 建不出库"
fi
[ -n "$PID" ] || fail "seed_demo.py 没吐出 project_id"
[ -f "$DB" ] || fail "seed_demo.py 说成功了，但 $DB 不在"
printf '  project_id = %s\n' "$PID"

# ══════════════════════════════════════════════════════════════════════════
# 3. 头牌：认知边界面板
# ══════════════════════════════════════════════════════════════════════════

panel() { _run uv run nh panel --db "$DB" -p "$PID" "$@"; }

step "nh panel —— README:20-31 那个框，逐格"
panel --chapter 152 --cast 萧决,顾清音,李管家
exits_zero "nh panel"
has "认知边界 · 第 152 章"
has "✓ 知道 (ch88)"    # 萧决 × 血脉秘密
has "✓ 知道 (ch120)"   # 萧决 × 玄铁令下落
has "✗ 不知道"         # 顾清音
has "⚠ 错误认知 (ch103)" # 李管家 × 血脉秘密
# believed_value 是这一行存在的全部意义：只画「⚠ 错误认知」等于没说他以为的是什么。
has "以为「已泄露」"
has "本场景 must_not_reveal：血脉秘密 · 玄铁令下落"
printf '%s\n' "$OUT"

# ── 时态接缝：ch87 / ch88 ─────────────────────────────────────────────────
# 「作者在第 88 章声明」和「第 152 章面板还记得」之间隔着闭开区间 `[valid_from, valid_to)`。
# 只查 ch152 的话，一个把时态过滤整个丢掉的实现照样全绿——它对每一章都答「知道」。
# 下界这一格是这条链路上唯一一个**便宜且致命**的探针。
step "AS OF —— ch87 还不知道 / ch88 知道（闭开区间的下界）"
panel --chapter 87 --cast 萧决
exits_zero "nh panel --chapter 87"
lacks "✓ 知道 (ch88)"
has "✗ 不知道"
panel --chapter 88 --cast 萧决
exits_zero "nh panel --chapter 88"
has "✓ 知道 (ch88)"

# ── fail-closed 的两面 ────────────────────────────────────────────────────
# 上面那条 must_not_reveal 的断言**单独存在时是废的**：一个永远打印全部秘密的实现
# （fail-closed 的退化值长得一模一样）也能让它绿。所以这里量两面：
step "must_not_reveal —— 全知道时是空的（阳性对照的反面）"
panel --chapter 152 --cast 萧决
exits_zero "nh panel --cast 萧决"
has "本场景 must_not_reveal：（无）"

# 「师兄」→ 2 个人（§3.1 / §10.5 第 1 条）。cast 没数全就没资格说哪条秘密是安全的：
# 少禁一条的代价是崩人设，多禁一条的代价是 Writer 少写一段。
step "「师兄」→ 2 个人 —— 必须画出来，且 must_not_reveal 退化成全部秘密"
panel --chapter 152 --cast 萧决,师兄
exits_zero "nh panel --cast 萧决,师兄"
has "解析不出唯一角色"
lacks "本场景 must_not_reveal：（无）"
has "血脉秘密"
has "✓ 知道 (ch88)" # 歧义不该让解析成功的那一行也黑掉

# 全部解析不出来时必须死：零行的表和「在场的人都没问题」在终端上一模一样。
step "静默的零 != 真的零 —— 全员解析不出来必须非 0 退出"
panel --chapter 152 --cast 张三,李四
exits_nonzero "nh panel --cast 张三,李四"
has "一个都没解析出唯一角色"

# ══════════════════════════════════════════════════════════════════════════
# 4. 规则：同一个库喂进 run_checks
# ══════════════════════════════════════════════════════════════════════════

check() { _run uv run nh check --db "$DB" -p "$PID" --chapter 151 -f "$1"; }

# R4 = 作者声明 vs 作者声明：场景块写着在青云城主府，而图上萧决自第 150 章起在北荒。
step "nh check —— R4 抓到 LOCATION_CONFLICT"
cat >"$TMP/ch151_conflict.md" <<'EOF'
## 场景 1
<!-- nh: cast=萧决 loc=青云城主府 goal=李管家试探萧决的身世 -->

　　萧决把那碗药搁在石阶上，很久没有说话。
EOF
check "$TMP/ch151_conflict.md"
exits_nonzero "nh check（有冲突）"
has "[R4] LOCATION_CONFLICT"
has "北荒"
has "青云城主府"
has "建议：" # 建议由规则确定性产出（PLAN 改 13），不是 LLM 编的
# 锚是 (para_index, quote_text, occurrence_k)，**永远不是 offset**（ADR 0006）。
has "第 1 段"
has "第 0 次"
has "<!-- nh: cast=萧决 loc=青云城主府 goal=李管家试探萧决的身世 -->"
printf '%s\n' "$OUT"

# 干净的一章。**这一条不是凑数**：上面那条只证明 R4 会开火，不证明它会闭嘴——
# 一条永远开火的规则同样能让上面全绿，而那是 M3 生死线（误报 < 1 条/章）的死法。
step "nh check —— 场景改到北荒，R4 闭嘴（且必须报「跑了几条规则」）"
cat >"$TMP/ch151_clean.md" <<'EOF'
## 场景 1
<!-- nh: cast=萧决 loc=北荒 goal=雪夜独行 -->

　　窗外的雪落下来。
EOF
check "$TMP/ch151_clean.md"
exits_zero "nh check（无冲突）"
has "0 条 issue"
# 零 issue 必须带着「跑了几条规则」一起出现（§10 约束 8）：v1 的 ALL_CHECKS 只有 R4，
# 所以「无 issue」的真实含义是「R4 没意见」，不是「这一章没问题」。
has "跑了 1 条规则"
has "location_conflict"

printf '\n✓ 心跳正常：声明 → SqliteStoryGraph → knowledge_matrix → 面板，以及 → run_checks → issue。\n'
printf '  （量的是接缝，不是真书。上面 import 那一步用的是手写的 3 章 fixture。）\n'
