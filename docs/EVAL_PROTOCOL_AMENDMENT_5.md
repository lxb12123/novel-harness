# EVAL_PROTOCOL 修正案 5：长度、续写与通用 provider 档

- 状态：预注册，已接受
- 日期：2026-08-01
- 作用域：只修正输出长度、调用预算、reasoning 与证据格式；不改陷阱、三臂、重复数、tell 检测器、统计阈值或 §6 裁决表。

## 为什么必须在运行前裁定

冻结正文假定一次生成约 800 个中文字，PLAN 又要求 Claude 原生 adaptive thinking + high effort，
而现有生产边界实际使用通用 OpenAI Python 客户端、`max_tokens=4096`，且没有发送 reasoning
参数。把输出改到 2,000 字以上后，长度、是否续写以及 reasoning 占用的 completion 预算都会改变
实验仪器；这些规则若等看到模型结果后再定，就属于事后改卷子。

时间线必须分开说：修正案 1–4 写下时 `synth/` 与 `runs/` 都不存在；本修正案写下时
`synth/` 已经存在，但 `runs/` 仍不存在、没有一次真实模型推理发生。因此前四份早于合成小册子，
五份全部早于第一份 `runs/*.jsonl` 和第一枚付费 token。

## 裁定 1：M2 的固定长度档

M2 仍然只测中文，不把英文混进 225 个样本。三臂逐字节共用同一份 frozen `LengthSpec`：

```text
language = zh
min_units = 2000
target_units = 2500
max_units = 3000
```

中文单位定义为**非空白 Unicode code point**：汉字、拉丁字母、数字和标点各计 1；空格、
tab 与换行不计。`previous_tail=800` 仍是输入上下文的最大 code-point 数，不是输出目标。

产品可以支持英文长度控制；英文按确定性 words 计数。但英文产品规则不属于本轮 225 个中文
cell 的推断范围，本轮结果不能被写成英文质量证据。

## 裁定 2：225 是 final cell 数，transport call 可为 225–450

基准轮仍是 `25 traps × 3 arms × 3 repeats = 225` 个最终实验 cell。每个 cell 先调用一次。
只有首次可见文本少于 2,000 个中文单位时，才追加首次原文作为 assistant message，再追加一条
固定中文续写指令，并用完全相同的模型、预算、reasoning 与采样配置调用**一次**。

续写触发只读长度计数；不得因 tell、泄漏、文风、质量、陷阱类型或实验臂触发。不得有第三次
调用、隐藏重写、样本删除或替换。一个成功完成的基准轮因此有 225 个 final cell、225–450 次
transport call；“225 次”不再被用作 transport call 的固定承诺。

两段可见文本按返回顺序原样拼接，不插字、不截断。每次 attempt 均单独保存，泄漏判分只读取
拼接后的 final text。

## 裁定 3：长度无效使整轮 INVALID

任一 cell 的最终拼接文本出现下列任一情况，本轮立即进入终态 `INVALID`：

- 少于 2,000 个中文单位；
- 多于 3,000 个中文单位；
- 最后一次调用的 `finish_reason` 是 provider 的长度上限；
- attempt 文本或确定性长度测量无法完整持久化。

runner 必须先写入并 flush 该 cell 已取得的 attempt 以及一条 `length_invalid`，再停止。partial
JSONL 原样保留；不得 append、resume、覆盖、补抽样本，也不得把 invalid cell 送进 scorer。
网络、鉴权或 provider 4xx/5xx 是“运行未完成”，与协议 `INVALID` 分开报告。

## 裁定 4：OpenAI-compatible client 不变，reasoning 由能力映射

SDK 边界继续只有 OpenAI Python 客户端。`base_url`、`model`、`api_key` 选择实际供应商；
OpenRouter 只是可选 endpoint，不是产品依赖。不得为了 Claude 的原生字段把这一层改回单供应商 SDK。

产品默认 reasoning=`off`。M2 固定请求 provider-neutral `high`。能力解析顺序、精确 endpoint/model、
实际 wire dialect、模型 context/output 上限、streaming 支持和能力来源必须在运行前冻结。若能力未知、
不支持 high，或请求预算装不进模型上限，预检必须在创建 run 文件和发出推理请求前失败；不得静默
省略、降级或 clamp。

用户长度不是 token。内部可见预算使用带版本的保守公式，reasoning 若与 completion 共池则另留
能力来源声明的比例。最终实际 endpoint/model/capability/request budget 由一份不含 key 的 profile
在推理前单独 commit；API key 只从运行环境注入。

## 裁定 5：JSONL 证据形状

header 至少记录：完整协议版本、LengthSpec、计数规则版本、最多两次及 under-min-only 的续写策略、
requested/effective reasoning、能力来源与适配器版本、context/output 上限、token 字段、可见/必需/请求
预算、stream 模式，以及删除 API key 后的 provider 配置。

每次 transport call 写一条 `generation_attempt`：trap/arm/repeat/attempt、精确 messages、原始可见
文本、该段与累计长度、model、usage、finish_reason、是否按长度需要续写。每个有效 cell 再写一条
`generation`：原样拼接的 final text、最终长度、attempt 数与对完整 final text 重算的 leak。无效时
改写 `length_invalid`，不得同时写可评分的 final cell。任何 authorization、API key 或 bearer token
都不得进入证据。

## 对原协议其余部分的影响

2,000–3,000 字比原先约 800 字提供更多命中 tell 的机会，这是实质仪器变化；三臂使用同一长度档
只说明处理对称，不能预先断言绝对泄漏率或臂间差值保持不变。除此之外，25 条陷阱、X0/X1/X2、
3 次基准重复、5 次升级重复、tell 集合、反混淆、floor/ceiling、McNemar/Holm、符号稳定性与 §6
裁决表全部保持冻结正文及修正案 1–4 的定义。
