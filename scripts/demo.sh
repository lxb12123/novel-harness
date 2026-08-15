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
#
# ── 两条泳道，各证一件事，谁都不替代谁 ────────────────────────────────────
#
# **泳道 1（第 1–3 节）：README:20-31 那个 ch88 世界。** 起点是 `seed_demo.py`——它是
# 「作者已经确认过了」这个状态的**替身**（那四个章号是字面量，它没有一本书可指）。
# 它量的是替身之后的全部接缝：
#
#   已确认的声明 → 真的 SQLite 文件 → SqliteStoryGraph → resolve_cast → knowledge_matrix
#     → 终端上那个框                                      ← README:20-31 逐格
#   同一个库 → run_checks → Issue + (para_index, quote_text, k)
#
# 时态下界（ch87/ch88）、fail-closed 的两面、「师兄」→2 人、R4 开火与闭嘴——全在这条。
#
# **泳道 2（第 4 节）：「引语 → 章号 → valid_from」那条链本身。** 泳道 1 量的是它的替身，
# 这条量的是它：nh init → nh import 一本 3 章的 fixture → nh declare knows --quote →
# 断言 stdout 上的 `valid_from = ch3`。**那个 3 是算出来的**——整条泳道的命令行里
# 没有任何一个章号入参，也没有任何一个旗标能让它有（§5.9 / §10 约束 10）。
# 这是这个项目全部差异化的地基，在此之前它每天只由单测量，心跳量的是它的替身。
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
# 1. 声明（M1 声明层的替身）
# ══════════════════════════════════════════════════════════════════════════
#
# `nh import` 曾经在这个位置，断言的是「非 0 退出 + 『落库没做』」。**M1 的建节点方法
# 落地了，那三行如期红了——那正是它们该做的事**（PLAN §8：「一开始全是 stub，之后每做完
# 一块换掉一个 stub」）。它搬去了第 4 节的真链路泳道，且断言反了过来：exit 0 + 库里真有
# N 章。放在那儿而不是这儿，是因为 `nh import` 现在要一个 project 和一个 db——而那正是
# 那条泳道的开头两步。

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
# 2. 头牌：认知边界面板
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
# 3. 规则：同一个库喂进 run_checks
# ══════════════════════════════════════════════════════════════════════════

check() { _run uv run nh check --db "$DB" -p "$PID" --chapter 151 -f "$1"; }

# ⚠️ **「规则会开火」那一段 2026-08-14 挪到泳道 2 去了**（脚本末尾）。
# 它原来是 R4（场景块声明的地点 vs 图上的地点），而 R4 连同场景块一起砍了（ADR 0027）。
# 顶上来的 R3 要一条「他死了」的边，而 `nh declare dead` 只收**引语**（约束 10：
# 作者永不填章号）——`seed_demo.py` 这一条泳道**磁盘上没有正文**，那句引语无处可找。
# 所以开火那一半跟着能算出章号的那条泳道走，这儿只留「它会闭嘴」这一半。

# 干净的一章。**这一条不是凑数**：泳道 2 那条只证明规则会开火，不证明它会闭嘴——
# 一条永远开火的规则同样能让那一条全绿，而那是 M3 生死线（误报 < 1 条/章）的死法。
step "nh check —— 没有对话标签，规则闭嘴（且必须报「跑了几条规则」）"
cat >"$TMP/ch151_clean.md" <<'EOF'
　　窗外的雪落下来。
EOF
check "$TMP/ch151_clean.md"
exits_zero "nh check（无问题）"
has "0 条 issue"
# 零 issue 必须带着「跑了几条规则」一起出现（§10 约束 8）：「无 issue」的真实含义是
# 「这 2 条规则没意见」，不是「这一章没问题」。
# ⚠️ 这个 2 = `len(ALL_CHECKS)`。**加规则或砍规则时要改这儿**——
# `tests/test_doc_numbers.py::test_demo_pins_the_real_rule_count` 会拦住忘了改的那一次
# （这一行曾经写着「1」，R2/R3 在 2026-08-02 落地后心跳断了四天没人发现）。
has "跑了 2 条规则"
has "future_leak"
has "dead_speaks"

# ══════════════════════════════════════════════════════════════════════════
# 4. 泳道 2：引语 → 章号 → valid_from（作者一次都没输过章号）
# ══════════════════════════════════════════════════════════════════════════
#
# 上面三节量的是 seed_demo.py 之后的接缝，而 seed_demo.py 里那四个章号是**字面量**——
# 它是「作者已经确认过了」的替身。这一节量的是被替掉的那半：一句从正文里复制的引语
# 怎么变成一个章号。**它是这个项目全部差异化的地基**（§5.9 / §10 约束 10）。
#
# 整条泳道的命令行里**没有一个章号入参**。下面那个 `valid_from = ch3` 里的 3 是系统
# 算出来的：那句引语落在 fixture 的第三章。`tests/test_no_chapter_input.py` 从静态那侧
# 钉同一条（declare 的命令面里没有任何 int 型参数）；这里从动态那侧钉——一条只有静态
# 守卫的约束，绕过它只需要一个新的旗标名。

DB2="$TMP/real.db"
ROOT2="$TMP/qingyun"

step "真链路 —— nh init"
printf '  $ uv run nh init --name 青云记 --root %s --db %s\n' "$ROOT2" "$DB2"
if ! PID2="$(uv run nh init --name 青云记 --root "$ROOT2" --db "$DB2" 2>/dev/null)"; then
  OUT="（诊断在上面的 stderr 里）"
  fail "nh init 建不出项目"
fi
# stdout 只出 project_id 一行——多一个字这里就死（欢迎语走 stderr，见 cli.py::init）。
[ -n "$PID2" ] || fail "nh init 没往 stdout 吐 project_id"
case "$PID2" in project:*) ;; *) fail "nh init 的 stdout 不是一个干净的 project_id：$PID2" ;; esac
printf '  project_id = %s\n' "$PID2"

nh2() { _run uv run nh "$@" --db "$DB2" -p "$PID2"; }

step "真链路 —— nh import（切章 + 写盘 + 落库）"
_run uv run nh import "$BOOK" --db "$DB2" -p "$PID2"
exits_zero "nh import（落库）"
has "切出 $CHAPTERS 章"
# 「落库了几章」必须印出来：一个切了章、写了盘、却没落库的 import 在终端上跟成功
# 长得一模一样，而它的代价是下面每一条 declare 都报「引语找不到」——作者会去查引语。
has "库里现在 $CHAPTERS 章"

step "真链路 —— 声明一个人"
nh2 declare character 萧决
exits_zero "nh declare character"

# ── 这一步是全脚本的头等断言 ─────────────────────────────────────────────
# 作者敲的只有一句从正文里复制的话。他没有输入 3，也没有任何一个旗标能让他输入 3。
#
# ⚠️ **2026-08-14：这一步从 `nh declare knows` 换成了 `nh declare dead`。**
# 不是因为这条更好，是因为 `declare knows` / `declare where` 已经**不存在了**
# （`1364ba0`：手工声明「谁知道什么 / 谁在哪儿」退场，只留抽取那条路）。
# **这条心跳从那次改动起就一直是红的，没有人发现** —— 正是这个脚本自己反复警告的那种断法
# （CLAUDE.md：上一次断了四天）。头等断言本身一个字没变：**章号由引语算出来。**
step "真链路 —— nh declare dead：章号由引语算出来"
nh2 declare dead --who 萧决 --quote "你身上流的不是萧家的血"
exits_zero "nh declare dead"
has "ch3" # ← 3 是**算出来的**：这句话落在 fixture 的第三章。
printf '%s\n' "$OUT"

# ── 同一条闭开区间下界，这次穿过的是**真实的声明链路** ──────────────────────
#
# ch87/ch88 那一格（泳道 1）证明时态过滤在 seed 出来的边上活着；这一格证明它在一条
# `valid_from` 由引语算出来的边上也活着——两者之间隔着 evidence 表和整条
# `put_evidence → upsert_edge` 的血统。**而这一次量它的是一条规则**：
# 第 3 章起他死了，所以第 4 章的对话标签要报，第 2 章的不报。
step "真链路 —— nh check：R3 抓到 DEAD_SPEAKS（第 4 章）"
cat >"$TMP/ch4.md" <<'EOF'
　　夜里风大。

　　萧决道：「我还没死。」
EOF
_run uv run nh check --db "$DB2" -p "$PID2" --chapter 4 -f "$TMP/ch4.md"
exits_nonzero "nh check（第 4 章有问题）"
has "[R3] DEAD_SPEAKS"
has "萧决"
has "建议：" # 建议由规则确定性产出（PLAN 改 13），不是 LLM 编的
# 锚是 (para_index, quote_text, occurrence_k)，**永远不是 offset**（ADR 0006）。
has "第 2 段"
has "第 0 次"
printf '%s\n' "$OUT"

step "真链路 —— AS OF：第 2 章他还没死，同一段字一条都不报"
_run uv run nh check --db "$DB2" -p "$PID2" --chapter 2 -f "$TMP/ch4.md"
exits_zero "nh check（第 2 章无问题）"
has "0 条 issue"
lacks "DEAD_SPEAKS"

printf '\n✓ 心跳正常。两条泳道：\n'
printf '  1. 已确认的声明（seed_demo.py）→ SqliteStoryGraph → knowledge_matrix → 面板，\n'
printf '     以及 → run_checks（会闭嘴）。量的是替身之后的接缝。\n'
printf '  2. nh init → nh import → nh declare dead --quote → valid_from = ch3 → run_checks。\n'
printf '     量的是「引语 → 章号」那条链本身，而那个 3 作者一次都没输过；\n'
printf '     再拿一条规则把闭开区间量出来（ch4 报、ch2 不报）。\n'
printf '  （量的是接缝，不是真书：import 用的是手写的 3 章 fixture。）\n'
