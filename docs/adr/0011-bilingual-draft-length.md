# ADR 0011：双语长度契约与通用模型调用计划

- 状态：已接受
- 日期：2026-08-01
- 扩展：[ADR 0010](0010-writer-boundary.md) 的 D4/D5；不改写其历史裁定

## 问题

Writer 目前把中文 `600–1000 字` 与 `max_tokens=4096` 写死在两层。前者让用户不能请求长文或英文，
后者把人类长度、模型可见输出和 reasoning 预算混成一个旋钮。冻结 PLAN 还要求 Claude 原生
adaptive/high，而产品明确选择 OpenAI-compatible client，二者缺少可审计的映射。

## 决策

### D1 显式语言与人类单位

v1 首批只接受 `zh` 与 `en`，不从 prompt、项目、locale、模型名或 endpoint 猜语言。
frozen `LengthSpec(language, min_units, target_units, max_units)` 强制
`1 <= min <= target <= max`，单位由语言派生，调用方不能给中文伪装成 words。

产品默认及硬上限为：

| language | min / target / max | hard maximum |
|---|---:|---:|
| `zh` | 2,000 / 2,500 / 3,000 characters | 20,000 characters |
| `en` | 1,200 / 1,500 / 1,800 words | 12,000 words |

部署者可用文档化环境变量覆盖产品默认和 hard maximum；M2 的中文 2,000/2,500/3,000 常量不读
这些变量。

中文计数为非空白 Unicode code point。英文 word 从 Unicode letter/number 开始和结束，中间只允许
ASCII/curly apostrophe 或 hyphen；因此 `don't`、`state-of-the-art`、`GPT-5` 各算一个 word。

### D2 LengthSpec 属于三臂共享 base

`assemble()` 必须接收 `LengthSpec`。语言、house style 与精确长度指令只进入 X0/X1/X2 共享的
base；form-specific graph section 不得拥有自己的长度文字。ADR 0010 的 labels-only、同一 `ctx`、
X0 前缀和 not-canon 边界不变。`previous_tail` 只取最多 800 个输入 code point。

### D3 一次生成，长度不足最多续写一次

产品通常调用一次；首次少于 minimum 时，追加首次原文和固定语言续写指令，再调用一次。触发只读
确定性长度，不看泄漏、质量或 form。结果保留完整文本、实测长度、1–2 次 attempt、usage 与
finish reason。产品对仍过短、过长或疑似截断的文本返回清楚状态，不静默丢弃或截断付费内容。
M2 对同一情形按修正案 5 使整轮 `INVALID`。

### D4 通用能力解析与 reasoning 适配

唯一 SDK 仍是 OpenAI Python client；`base_url/model/key` 选择 GPT、DeepSeek、Claude-compatible、
OpenRouter 或本地兼容端点。OpenRouter 不被写成必选依赖。

能力解析优先级固定为：operator override → 精确 endpoint/model registry → endpoint metadata →
`unknown`。禁止从模型名前缀猜能力。产品 reasoning 默认 `off`；M2 请求 `high`。adapter 只向明确
支持的 route 发送对应 wire 字段；unknown 或不支持的非 off 请求预检失败，不静默降级。

### D5 人类长度到 token capacity 的版本化计划

token 只是内部 capacity。v1 可见预算为：

```text
visible = ceil(max_units * 2.0) + 1024
```

reasoning 与 completion 共池时，按能力记录的 reserve ratio 计算：

```text
required = ceil(visible / (1 - reserve_ratio))
```

显式 request budget 只能向上扩容。已知 output/context 上限不足时失败，绝不 clamp。请求预算超过
16,000 时必须使用 provider 明确支持的 streaming，并聚合成与非流式相同的结果契约；streaming
支持为 unknown 也不得冒险发送。API key 不进入 frozen plan、repr、日志或 JSONL。

### D6 发布边界不变

engine、类型、测试、文档和灰置 UI 可以先落地；公开 `/draft` 在 M2 PASS 与 ADR 0009 完成前仍返回
501。英文有回归测试不等于中文 M2 验证了英文生成质量。

## 被取代与保留

本 ADR 取代 `assemble()` 内的固定中文 `600–1000 字`、产品全局 `max_tokens=4096` 默认，以及
“通用 client 必须放弃 reasoning”的结论。它只扩展 ADR 0010 的签名和 frozen provider plan；
ADR 0010 的 D1–D6、历史时间线和 Writer 安全边界保持原样，不回写旧文件。

## 若决策错误，修复成本

计数或预算过保守时只需新增版本、保留旧证据版本并调整产品 policy；已跑的 M2 不能重解释。
若某兼容端点实际不接受已登记 wire shape，预检/真实 smoke 会在该 endpoint profile 上失败；修复
adapter 并新增测试和预运行决策，不能在运行中偷偷换 dialect。若双语默认不合适，部署者可调产品
policy，不影响冻结的中文实验。
