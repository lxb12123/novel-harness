# ADR 0044：要 JSON 就在线上要 —— `StructuredCallPlan` 带 `response_format`

- **状态**：已接受（2026-09-06）
- **推翻了**：不是原始需求文档，是**本仓库自己**——「需要结构化输出的调用，靠 prompt 里
  一句话求模型配合」这个做法，以及 `extract.analyze.parse_analysis` 那三个「不」里的
  **「不剥壳」**那一个。
- **相关**：[ADR 0043](0043-facts-store-a-start-not-an-interval.md)（同日的另一刀。
  两者都出自同一次排查：真书上 73 次抽取失败，50 次是乱序、23 次是这一条）、
  `draft/provider.py` 的 `_NO_STREAM` / `_NO_STREAM_OPTIONS`（这次照抄的那个模式）

---

## 一句话

`StructuredCallPlan` 这个类型的意思本来就是「这一次要结构化输出」，**而它一个字节都
没走到线上**。现在它自动带上 `response_format={"type": "json_object"}`。

---

## 事实（可查证）

### 1. 从前全靠 prompt 求模型配合

`extract/prompt.py` 里那一行：

> `Return JSON only: one JSON object, with no Markdown fences and no prose before or after it.`

而 `parse_analysis` 的立场是「不做修复、**不剥壳**、不重试」。模型不配合 = **整章作废**
（`_ingest_success` 是整章一个事务）。

### 2. 模型有相当一部分时候不配合，而且**不是内容问题**

真书 2026-09-05：73 次抽取失败里 **23 次**是 `analysis_format`。判据三条，都排除了
「某几章有毒」：

| | |
|---|---|
| 同一章两次结果不同 | 第 3 章：`ingest_failure`（说明那次 JSON 是好的）→ `analysis_format` → 重跑 **成功** |
| 不是被截断 | 96 次调用的 `finish_reason` 全是 `stop` |
| 不是章太长 | 失败的输出 890–3885 token，成功的 1132–3675，**完全重叠** |

抽取这一档**不发 temperature**（有意的：`SAMPLING_STRICT_MODELS` 里的模型会对非默认
采样返回 400），走模型默认采样。**所以它是每次调用各掷一次骰子**，换任何一本书都一样。

### 3. 同一个仓库里，两处需要 JSON，两套互相不知道的策略

| | 原来 |
|---|---|
| `extract.parse_analysis` | 「不做修复、**不剥壳**、不重试」——模型套个 ```json 围栏就整章作废 |
| `advisory_review._payload` | 从一开始就剥围栏，注释写着「模型爱加它」 |

同一个模型、同一种毛病、两个答案，**而严的那一侧在真书上失败了 23 次**。

---

## 决策

### 1. 键在**类型**上，不加参数

`_wire_kwargs_from_validated` 见到 `StructuredCallPlan` 就带上
`response_format={"type": "json_object"}`。

**为什么不是 `complete(..., json_object=True)`**：加参数意味着调用方有忘记的余地，
而「这一次要 JSON」这件事 `StructuredCallPlan` 这个类型**已经在说了**——它只是从前
只影响预算怎么算。今天全仓两个 JSON 消费者（抽取 / 事后核对）走的正是
`plan_structured_call`，所以一个都漏不掉，将来第三个也漏不掉。

**散文那两条路一个字节都不变**：产品起草和 M2 判分链走 `ResolvedCallPlan`
（`EVAL_PROTOCOL.md` §2：gate 测的必须是产品会发的东西）。
`test_a_structured_plan_differs_from_prose_by_response_format_and_nothing_else`
两头都钉：**带上这个键**、且**只带这一个键**。

### 2. 端点不认就退掉，并记住这条路由

`_NO_JSON_MODE`，照抄 `_NO_STREAM_OPTIONS` 的形状。判据是**错误里点了这个字段的名**，
不是「状态码是不是 400」——后者会把「模型名写错」「钥匙过期」也吞进重试，于是一次
真正的配置错误变成两次失败，而作者只看见后面那次的话术。

**退回去的那一档就是 2026-09-06 之前的行为，一字不差。所以最坏情况不比从前差**——
这是敢对所有结构化调用默认带上它的全部理由。

### 3. 剥围栏：一份实现，两个消费者

`provider.unfenced()`。抽取那三个「不」变成两个。

**剥围栏和圆场是两回事**：围栏是包装，里面那份 JSON 一个字节没变。改字段、补逗号、
失败重试照旧一律不做——那些会把「这个模型/这份 prompt 产不出合规 JSON」永久藏起来。

它今天是**第二道防线**：端点认 `response_format` 的话，围栏根本不会出现。这一层留给
那些不认、退回 prompt 求人的端点。

### 4. 解析失败要说得出是哪一格

`parse_analysis` 从前只抛一句「chapter analysis is not valid schema JSON」，把 pydantic
那份**指到具体字段**的 `ValidationError` 整个扔了。于是真书 23 次失败长得一模一样。
现在带上前两条错误（砍到 400 字：这一列是诊断，不是全量转储）。

---

## 效果（真书，2026-09-06）

改完重跑四章，其中第 6 章此前**连续失败两次**：

| 章 | 结果 |
|---|---|
| 6 / 9 / 10 | **成功**，8 / 8 / 7 条事件 |
| 7 | 仍然失败 |

**端点收下了 `response_format`**（没有触发退回），而且第 7 章的错误**换了一类**：

```
之前：Input should be an object                                  ← 整份响应不是对象
现在：Invalid JSON: key must be a string at line 1 column 3290    ← 是对象，但语法坏了
```

**「整份不是对象」那一类没了**（围栏 / 散文都属于它），剩下一类更少见的：模型在生成
中途吐出语法坏掉的 JSON。

⚠️ **这一改是大幅收窄，不是清零，别在文档里把它写成清零。** `json_object` 各家保证的
是「是个 JSON 对象」，**不保证语法一定合法**——那要 `json_schema` + `strict:true`，
不是所有端点都有。四章样本也太小，别当成比例。

---

## 若此决策错误，修复成本

**极低。** 三样东西各自独立、各自可退：

1. 不带 `response_format` —— 删 `_wire_kwargs_from_validated` 里那三行，回到从前；
2. 不剥围栏 —— `unfenced` 改成直接 `return text.strip()`；
3. 不带详细诊断 —— 那一列本来就只有维护者读。

真正不可逆的只有一样：**万一哪个端点在收下 `response_format` 之后行为变差**
（比如为了凑 JSON 而砍内容）。缓解是同一条：`_NO_JSON_MODE` 里加上那条路由就退回去了。

---

## 没有做什么

| | 为什么 |
|---|---|
| **失败自动重试** | 它是最诱人的一条，也是最会藏事的一条：重试成功之后，「这个模型产不出合规 JSON」这件事就再也没人知道了。**先让新诊断跑一轮真实数据**，看清错误分布再决定 |
| `json_schema` + `strict:true` | 能力更强（语法也保证），但**不是所有端点都有**，而这个产品的连接参数是作者自己填的（BYOK）。要上就得先有一层能力探测，那是另一件事 |
| 给抽取发 `temperature=0` | `SAMPLING_STRICT_MODELS` 里的模型会对非默认采样返回 400，而作者换模型不该换出一个「抽取突然用不了」。这条限制在 `deps.py` 里写着，本次不动 |
