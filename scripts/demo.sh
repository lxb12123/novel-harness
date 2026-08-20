#!/usr/bin/env bash
#
# 心跳（ARCHITECTURE §11「心跳」）：端到端还通着 = 这个脚本的退出码。
#
# 2026-08-20 起它是 **API 心跳**：命令行面（nh）删了，产品线 = 桌面壳 + Web 工作台，
# 调试线 = `python -m novel_harness.api`。所以心跳改为：seed_demo 造库 → 起真服务 →
# curl 打真端点断言。它量的是接缝（作者声明 → 真 SQLite → 图 → 面板 / 规则），
# 和以前 CLI 心跳量的是同一条链，只是从浏览器那半边走。
#
# ── 两条泳道，各证一件事，谁都不替代谁 ────────────────────────────────────
#
# **泳道 1（读路径）**：seed_demo.py 是「作者已经确认过了」的替身（那四个章号是
# 字面量）。量它之后的接缝：花名册 / 认知矩阵（KNOWS·BELIEVES·UNKNOWN）/
# 闭开区间下界（ch87 不知道、ch88 知道）。
#
# **泳道 2（写路径）**：「引语 → 章号 → valid_from」那条链本身。POST /api/projects
# 建一本 → POST /import 一本 3 章 fixture → POST /declare/death（作者只给一句从正文
# 复制的话，章号是演算结果）→ 第 3 章 check 报 R3、第 2 章同字不报。
#
# **每一步都断言输出**：一条链路上大多数失败形态的自然产物是「漂亮的空 JSON + 200」
#（§10 约束 8）。只查 HTTP 状态码不够，要查内容。
#
# ── ⚠️ 这里用的不是真书 ───────────────────────────────────────────────────
# import 用的是手写的 3 章 fixture，它证明的只是「切章器在手写的脏数据上不切歪」。
# 换真书改两个环境变量即可，脚本本身不用动：
#
#   NH_DEMO_BOOK=~/books/real_novel.txt NH_DEMO_CHAPTERS=317 bash scripts/demo.sh

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" # 任意 cwd 可跑；uv run 要在项目里，下面的路径一律绝对或相对 ROOT

BOOK="${NH_DEMO_BOOK:-$ROOT/tests/fixtures/demo_novel.txt}"
CHAPTERS="${NH_DEMO_CHAPTERS:-3}"
PORT="${NH_DEMO_PORT:-48756}" # 固定、避开开发默认 8756；CI 上单跑不撞车
BASE="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
SRV=""
DB="$TMP/demo.db"
cleanup() {
  [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT # 库和稿子只活在 tmp 里：心跳不许碰作者的文件

# ── 断言 ───────────────────────────────────────────────────────────────────

step() { printf '\n▸ %s\n' "$1"; }
fail() {
  printf '\n✗ 心跳断了：%s\n' "$1" >&2
  [ -f "$TMP/resp.json" ] && printf -- '─── 上次响应 ───\n%s\n' "$(cat "$TMP/resp.json")" >&2
  [ -f "$TMP/srv.log" ] && printf -- '─── 服务日志（尾 15 行）───\n%s\n' "$(tail -15 "$TMP/srv.log")" >&2
  exit 1
}

# 打一个端点：$1=方法 $2=路径 [$3=JSON body（作为 JSON 字符串）]。echo 出 HTTP 状态码。
api() {
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -s -o "$TMP/resp.json" -w "%{http_code}" -X "$method" "$BASE$path" \
      -H 'Content-Type: application/json' -d "$body"
  else
    curl -s -o "$TMP/resp.json" -w "%{http_code}" -X "$method" "$BASE$path"
  fi
}

http_ok() { # $1=描述 $2=状态码
  [ "$2" = "200" ] || fail "$1 应当 200，实得 $2"
  [ -s "$TMP/resp.json" ] || fail "$1 响应体是空的"
}

# GET 但查询参数要 URL 编码（中文/逗号裸发会让 uvicorn 拒「Invalid HTTP request」）。
# $1=路径 后面每个参数都是 "k=v"，逐对 --data-urlencode。
apiq() {
  local path="$1"; shift
  local args=(-s -o "$TMP/resp.json" -w "%{http_code}" -G "$BASE$path")
  for kv in "$@"; do args+=(--data-urlencode "$kv"); done
  curl "${args[@]}"
}

json_ok() { # $1=描述 $2=python 表达式（读 d = resp.json）
  python3 -c "import json,sys; d=json.load(open('$TMP/resp.json')); sys.exit(0 if ($2) else 1)" \
    || fail "$1"
}

# ══════════════════════════════════════════════════════════════════════════
# 0. 播种 + 起服务
# ══════════════════════════════════════════════════════════════════════════

step "seed_demo.py —— 作者声明 3 个人 / 2 条秘密 / 4 条边"
if ! PID="$(uv run python "$ROOT/scripts/seed_demo.py" "$DB" 2>"$TMP/seed.err")"; then
  printf -- '─── seed_demo 诊断 ───\n%s\n' "$(cat "$TMP/seed.err")" >&2
  fail "seed_demo.py 建不出库"
fi
[ -n "$PID" ] || fail "seed_demo.py 没吐出 project_id"
[ -f "$DB" ] || fail "seed_demo.py 说成功了，但 $DB 不在"
printf '  project_id = %s\n' "$PID"

step "起服务：python -m novel_harness.api"
NH_DB="$DB" NH_PORT="$PORT" uv run python -m novel_harness.api >"$TMP/srv.log" 2>&1 &
SRV=$!
up=""
for _ in $(seq 1 50); do
  if curl -s -o /dev/null "$BASE/api/projects" 2>/dev/null; then up=1; break; fi
  sleep 0.2
done
[ -n "$up" ] || fail "服务 10 秒没起来（看上面的日志）"

# ══════════════════════════════════════════════════════════════════════════
# 1. 泳道 1 —— 读路径：花名册 / 认知矩阵 / 闭开区间下界
# ══════════════════════════════════════════════════════════════════════════

step "花名册 —— 3 个人都在"
code="$(api GET "/api/projects/$PID/roster")"
http_ok "roster" "$code"
json_ok "roster 里有 萧决/顾清音/李管家" \
  "all(n in [r.get('name') for r in d] for n in ('萧决','顾清音','李管家'))"

step "认知矩阵 ch152 —— KNOWS / BELIEVES / UNKNOWN 都要有"
code="$(apiq "/api/projects/$PID/chapters/152/matrix" "cast=萧决,顾清音,李管家")"
http_ok "matrix ch152" "$code"
json_ok "萧决×血脉秘密 = KNOWS（since 88）" \
  "any(c['state']=='KNOWS' and c['since_chapter']==88 for c in d['cells'])"
json_ok "李管家×血脉秘密 = BELIEVES（since 103）" \
  "any(c['state']=='BELIEVES' and c['since_chapter']==103 for c in d['cells'])"
json_ok "至少一格 UNKNOWN（顾清音/未知）" \
  "any(c['state']=='UNKNOWN' for c in d['cells'])"

step "时态下界 —— ch87 还不知道 / ch88 知道（闭开区间 [valid_from, ∞)）"
code="$(apiq "/api/projects/$PID/chapters/87/matrix" "cast=萧决")"
http_ok "matrix ch87" "$code"
json_ok "ch87 萧决×血脉秘密 仍是 UNKNOWN" \
  "all(c['state']=='UNKNOWN' for c in d['cells'] if c.get('secret_id'))"
code="$(apiq "/api/projects/$PID/chapters/88/matrix" "cast=萧决")"
http_ok "matrix ch88" "$code"
json_ok "ch88 萧决×血脉秘密 = KNOWS" \
  "any(c['state']=='KNOWS' and c['since_chapter']==88 for c in d['cells'])"

# ══════════════════════════════════════════════════════════════════════════
# 2. 泳道 2 —— 写路径：「引语 → 章号 → valid_from」+ R3 开火/闭嘴
# ══════════════════════════════════════════════════════════════════════════

step "建第二本书 —— POST /api/projects（root 服务器派生，写进 <库同级>/books）"
code="$(api POST "/api/projects" '{"name":"青云记"}')"
http_ok "create project" "$code"
PID2="$(python3 -c "import json; print(json.load(open('$TMP/resp.json'))['id'])")"
case "$PID2" in project:*) ;; *) fail "create 没吐出 project_id：$PID2" ;; esac
printf '  project_id = %s\n' "$PID2"

step "导入 3 章 fixture —— 切章 + 写盘 + 落库"
python3 - "$BOOK" "$TMP/import_body.json" <<'PY'
import json, pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
pathlib.Path(sys.argv[2]).write_text(json.dumps({"text": text}), encoding="utf-8")
PY
code="$(api POST "/api/projects/$PID2/import" "$(cat "$TMP/import_body.json")")"
http_ok "import" "$code"
json_ok "切出 $CHAPTERS 章" "d.get('chapter_count', 0) >= $CHAPTERS"

# 找到落盘的第 2 / 第 3 章文件（import 写到项目的 chapters/ 目录，号 = 文件名）
CH3="$(find "$TMP/books" -name '0003.md' 2>/dev/null | head -1)"
CH2="$(find "$TMP/books" -name '0002.md' 2>/dev/null | head -1)"
[ -n "$CH3" ] || fail "导入后找不到第 3 章文件（find '$TMP/books' -name 0003.md 为空）"
[ -n "$CH2" ] || fail "导入后找不到第 2 章文件"
printf '  第 3 章文件 = %s\n' "$CH3"

step "声明一个人 —— POST /nodes（label=Character）"
code="$(api POST "/api/projects/$PID2/nodes" '{"label":"Character","name":"萧决"}')"
http_ok "declare node 萧决" "$code"
json_ok "节点建出来了" "d.get('label') == 'Character' and d.get('name') == '萧决'"

step "声明死亡 —— 章号由引语算的（作者不敲章号）"
code="$(api POST "/api/projects/$PID2/declare/death" '{"who":"萧决","quote":"你身上流的不是萧家的血"}')"
http_ok "declare/death" "$code"
json_ok "valid_from 是算出来的 ch3" "d.get('edge', {}).get('valid_from_chapter') == 3"
printf '  valid_from_chapter = %s\n' "$(python3 -c "import json,io; print(json.load(io.open('$TMP/resp.json',encoding='utf-8'))['edge']['valid_from_chapter'])")"

step "第 3 章 —— R3 抓到 DEAD_SPEAKS（死者说话：valid_from=3 起他死了）"
cat >"$CH3" <<'EOF'
　　夜里风大。

　　萧决道：「我还没死。」
EOF
code="$(api POST "/api/projects/$PID2/chapters/3/check")"
http_ok "check ch3" "$code"
json_ok "ch3 报了 DEAD_SPEAKS" \
  "any('dead_speaks' in (i.get('rule') or '').lower() for i in d['issues']) or any(i.get('rule')=='R3' for i in d['issues'])"
json_ok "ch3 的 issue 带「建议」（确定性产出，非 LLM）" \
  "any(i.get('suggested_action') for i in d['issues'])"

step "第 2 章 —— 同一段字，他还活着 → 0 issue"
cat >"$CH2" <<'EOF'
　　夜里风大。

　　萧决道：「我还没死。」
EOF
code="$(api POST "/api/projects/$PID2/chapters/2/check")"
http_ok "check ch2" "$code"
json_ok "ch2 没有 DEAD_SPEAKS" \
  "all('dead_speaks' not in (i.get('rule') or '').lower() for i in d['issues'])"
# ⚠️ 下面这句里的「跑了 2 条规则」是字面量，被 test_doc_numbers::test_demo_pins_the_real_rule_count
#    钉着（必须 == len(ALL_CHECKS)）。加规则时连它一起改——**没有别的东西会告诉你心跳断了**。
#    2026-08-20 改 API 心跳时它一度被写成宽松的 `>= 2`，守卫当场咬住；别再放宽。
json_ok "ch2 报「跑了 2 条规则」（§10 约束 8：零要和真零分开）" \
  "len(d.get('rules_run', [])) == 2"

printf '\n✓ 心跳正常。两条泳道：\n'
printf '  1. 种子库 → 花名册/矩阵/闭开区间（读路径，Web 那半边）。\n'
printf '  2. 建书 → import → declare/death（valid_from 由引语算出）→ R3 开火、AS-OF 闭嘴。\n'
printf '  （量的是接缝，不是真书：import 用的是手写的 3 章 fixture。）\n'
