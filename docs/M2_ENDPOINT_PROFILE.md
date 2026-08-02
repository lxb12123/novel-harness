# M2 Endpoint Profile：deepseek-v4-flash @ api.deepseek.com

- 状态：**已冻结**（2026-08-02），首次推理前单独 commit，**不含 key**
- 依据：`EVAL_PROTOCOL.md@0393088` + 修正案 1/2/3/4/5 + ADR 0010/0011
- 用途：本轮 225 个 final cell（25 陷阱 × 3 臂 × 3 次）的唯一调用配置；换配置 = 换卷子，
  必须另开 profile 并在推理前 commit。

## endpoint / model / key

| 项 | 值 |
|---|---|
| `NH_LLM_BASE_URL` | `https://api.deepseek.com` |
| `NH_LLM_MODEL` | `deepseek-v4-flash` |
| `NH_LLM_API_KEY` | **只从运行环境注入**，不进本文件、不进 commit、不进 `runs/*.jsonl` |

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

## 共享输出预留（reserve_ratio_high = 0.8）

DeepSeek V4 thinking 模式下 `reasoning_content` 与 `content` **共享输出预算**
（官方 thinking_mode 文档），但官方未公布 thinking 占比。按与 OpenRouter 一致的
**保守 80%** 预留，写进 `draft/capabilities.py` 注册表并有一条精确数学测试钉住。
若真实 smoke 出现截断/空答案，按 ADR 0011 的修复路径调整并**重跑预检**，不许运行中改。

## 冻结的预算（版本化公式，全部确定值）

```text
M2_LENGTH_SPEC            = zh 2,000 / 2,500 / 3,000（非空白 code point）
visible = ceil(3000 × 2) + 1024            = 7,024
required = ceil(7024 ÷ (1 − 0.8))          = 35,120
request = 向上取整到万位                    = 40,000
stream：request > 16,000 → True
continuation 预检：prompt + (request + overhead) + request ≤ 1M ✓
```

每个 cell：先调用一次；首段不足 2,000 时追加原文 + 固定续写指令再调用一次
（under-min-only，至多 2 次 attempt）。两段按返回顺序原样拼接，不插字不截断。

## 验证记录

- [x] 真实 smoke call —— **2026-08-02 完成**：`reasoning_effort=high` + thinking enabled +
      `max_tokens=40000` + stream 全被接受；`finish_reason=stop`，usage 正常返回
      （prompt 89 / completion 16），文本原样回传。
- [ ] 完整一轮 225 final cells / 225–450 transport calls

跑完之前 `runs/` 不存在、ADR 0009 不写；本 profile 的 commit 时间戳先于
第一份 `runs/*.jsonl` 是「先定卷子再答卷」的证据链最后一环。
