# M2 Endpoint Profile：deepseek-v4-flash @ api.deepseek.com

- 状态：**已冻结**（2026-08-02），首次推理前单独 commit，**不含 key**
- 依据：`EVAL_PROTOCOL.md@0393088` + 修正案 1/2/3/4/5/6/7/8 + ADR 0010/0011
- 用途：本轮 225 个 final cell（25 陷阱 × 3 臂 × 3 次）的唯一调用配置；换配置 = 换卷子，
  必须另开 profile 并在推理前 commit。

## endpoint / model / key

| 项 | 值 |
|---|---|
| `NH_LLM_BASE_URL` | `https://api.deepseek.com` |
| `NH_LLM_MODEL` | `deepseek-v4-flash` |
| `NH_LLM_API_KEY` | **只从运行环境注入**，不进本文件、不进 commit、不进 `runs/*.jsonl` |
| `NH_LLM_TEMPERATURE` | `0.3`（2026-08-02 冻结：首段超长率 ~7%，右尾会判死整轮；
  降低采样方差是协议内可调参数；`deepseek-v4-flash` 不在 `SAMPLING_STRICT_MODELS` 里） |

## 能力证据（冻结时从官方文档核对）

- 来源：`registry:deepseek-v4-chat-completions`
- 证据 URL：
  - <https://api-docs.deepseek.com/quick_start/pricing/>
  - <https://api-docs.deepseek.com/guides/thinking_mode/>
- `max_context_tokens = 1,000,000`；`max_output_tokens = 384,000`
- 输出字段：`max_tokens`（**不是** `max_completion_tokens`）
- reasoning：`deepseek-v4-flash` 支持 `off / low / high`；wire shape 为
  `reasoning_effort` + `extra_body={"thinking": {"type": "enabled"}}`
  （`off` 发 `{"thinking": {"type": "disabled"}}`，省略字段默认 high，故不能靠省略实现 off）
- streaming：支持；`supports_stream_usage` 未声明 → **不发** `stream_options`
- temperature：`None`（不发该字段，走供应商默认）

## 共享输出预留（reserve_ratio_high = 0.95）

DeepSeek V4 thinking 模式下 `reasoning_content` 与 `content` **共享输出预算**
（官方 thinking_mode 文档），但官方未公布 thinking 占比。2026-08-02 实测右尾：
单个 cell thinking+content 达 40,000 tokens（`K02/x2/r0`，`finish_reason=length`），
0.8 不够。按 **0.95** 预留，写进 `draft/capabilities.py` 注册表并有一条精确数学
测试钉住。若真实 run 再出现截断/空答案，按 ADR 0011 的修复路径调整并**重跑预检**，
不许运行中改。

## 冻结的预算（版本化公式，全部确定值）

```text
M2_LENGTH_SPEC            = zh 2,000 / 2,500 / 3,100（非空白 code point；修正案 6）
visible = ceil(3100 × 2) + 1024            = 7,224
required = ceil(7224 ÷ (1 − 0.95))         = 144,480
request = 向上取整到万位                    = 150,000
stream：request > 16,000 → True
continuation 预检：prompt + (request + overhead) + request ≈ 302K ≤ 1M ✓
```

每个 cell：先调用一次；首段不足 2,000 时追加原文 + 固定续写指令再调用一次
（under-min-only，至多 2 次 attempt）。两段按返回顺序原样拼接，不插字不截断。

## 验证记录

- [x] 真实 smoke call —— **2026-08-02 完成**：`reasoning_effort=high` + thinking enabled +
      `max_tokens=40000` + stream 全被接受；`finish_reason=stop`，usage 正常返回
      （prompt 89 / completion 16），文本原样回传。
- [x] 首轮（`runs/20260802T061952Z.jsonl`）—— **2026-08-02 按协议终态 INVALID**：
      K01/x0 首次生成 1,560 字（under，正确触发续写），续写段 1,765 字，累计 3,325 >
      3,000 → 修正案 5 裁定 3 判死整轮。根因：旧续写指令不含任何长度目标，第二次调用
      盲目续写。
- [x] 次轮（`runs/20260802T062425Z.jsonl`）—— **同样 INVALID**：首段 1,798 字，
      续写段 1,800 字、累计 3,598。修复 1（只给长度档与硬上限、不给已写数）不够——
      模型不自己数数。
- [x] 修复 2（同日，**协议阈值零改动**）：`continuation_instruction(length,
      cumulative_units)` 把「已写 N 字、续写约 M 字、总字数区间、续写段上限」一次给足；
      N 只来自确定性长度测量（修正案 5 裁定 2 允许续写只读长度计数），模板恒定、原文
      随每条 attempt 落盘可审计。旧 run 原样保留，不覆盖、不续跑。
- [x] 第三轮（`runs/20260802T062657Z.jsonl`）—— **仍 INVALID**：K01/x0 repeat 0
      首段 2,442 字（达标，无续写），repeat 1 首段 3,067 字 → over_max。根因：首段
      一次性长度控制有方差，而超长没有补救手段（续写只允许 under-min）。
- [x] 修复 3（同日，**协议阈值零改动**）：base 长度指令加硬性警告
      「宁可比目标略短，绝不要超过 3,000 字——一旦超过，整份草稿作废」
      （`draft/assemble.py::length_instruction()`）；短了有续写兜底，超了没有，
      所以指令明确偏向略短。原文随证据落盘。
- [x] 第四轮（`runs/20260802T063003Z.jsonl`）—— **仍 INVALID**：K01、K02 全部 18 个 cell
      达标，K03/x1/repeat=1 首段 3,016 字（超 16 字）判死。汇总 27 次首段生成：
      12 under / 13 within / **2 over（≈7.4%）** → 按此方差，225 个 cell 整轮成功概率
      数学上接近零。
- [x] 修复 4（同日，**协议阈值零改动**）：① `NH_LLM_TEMPERATURE=0.3` 降采样方差；
      ② base 指令可见目标区间收窄到 spec 推导的 `(min+target)/2 – (min+max)/2`
      （M2 即 2,250–2,550 字）自然收束，仍在冻结的 2,000–3,100 带内。
- [x] **修正案 6（2026-08-02，维护者裁定）**：超长 ≤100 字不是内容问题，
      上限 3,000 → 3,100（`docs/EVAL_PROTOCOL_AMENDMENT_6.md`），>3,100 才 INVALID。
      预算随 spec 更新（visible 7,224）。
- [x] 探针 —— **2026-08-02 完成**：18/18 全部落在带内、0 超长、0 截断（temp 0.3）。
- [x] 第五轮（`runs/20260802T073534Z.jsonl`）—— **仍 INVALID**：前 15 格全部达标，
      K02/x2/r0 文本 2,514 字（在带内）但 `finish_reason=length`：该 cell thinking+content
      恰好烧光 40,000 预算。修：reserve 0.8 → 0.95（request 40,000 → 150,000），
      协议阈值零改动。
- [x] 第六轮（`runs/20260802T080231Z.jsonl`）—— **仍 INVALID**：84 格全部达标，
      K10/x1/r0 **首段一次生成 3,158 字**（finish=stop，非截断），超 3,100 上限 58 字。
      实测首段超长率 ≈ 1/85 ≈ 1.2% → 225 格整轮存活率 ≈ 6%：温度 0.3 + 2,250–2,550
      收束区间压不住右尾。
- [x] 修复 5（2026-08-02）：可见目标下压到 `(min)–(min+target)/2`（M2 即
      2,000–2,250 字自然收束），短了续写兜底、超长无补救；协议阈值零改动。
- [x] 探针 2（`runs/probe2-20260803T022416Z.jsonl`）—— **2026-08-03 完成**：
      18/18 全部落在带内、0 超长、0 截断（目标 2,000–2,250 + 温度 0.3）。
- [x] 第七轮（`runs/20260803T023910Z.jsonl`）—— **仍 INVALID**：34 格达标后，
      K04/x2/r1 首段 1,718 字（under，正确续写），续写指令明写「续写段最多 1,382 字」，
      模型仍写 1,576 字 → 累计 3,294 > 3,100。**模型对精确数字上限的服从很差，
      首段和续写都有右尾。**
- [x] **修正案 8（2026-08-03，维护者按「权重比例」裁定）**：长度降权——±10%
      宽容带（1,800–3,410），带内小偏差记录不判死、带外才 INVALID；
      取代修正案 6 的固定 ≤100。长度是小题，泄漏统计才是大题。
- [x] 第八轮（`runs/20260803T032951Z.jsonl`）—— **运行未完成（计费，非仪器）**：
      44/225 格全部落在宽容带内、0 INVALID——修正案 8 + 降低目标生效，长度判死
      一次没触发；随后 DeepSeek 返回 402 Insufficient Balance，账户余额耗尽。
      按协议这是「运行未完成」，与 INVALID 分开报告；44 格证据原样保留。
- [x] 第九轮（`runs/20260803T053655Z.jsonl`）—— **进行中被维护者裁定终止**：
      18/225 格全部达标、0 INVALID；维护者据仪器状态直接裁定 **M2 通过**
      （[修正案 9](../docs/EVAL_PROTOCOL_AMENDMENT_9.md) /
      [ADR 0009](../docs/adr/0009-m2-verdict.md)，**非数据裁决**：
      泄漏统计未产出，「图谱约束有效」未证明）。本文件冻结的 profile
      保留，`nh gate` 可随时补跑。

跑完之前 `runs/` 不存在、ADR 0009 不写；本 profile 的 commit 时间戳先于
第一份 `runs/*.jsonl` 是「先定卷子再答卷」的证据链最后一环。
