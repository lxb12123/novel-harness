# ADR 0006：Evidence 双指针 + STALE 不进队列

- 状态：已接受
- 日期：2026-07-16
- 推翻了什么：需求文档第 7 节 / 第 14 节的 `evidence_artifact_id + source_start_offset + source_end_offset`
- 后端契约：**Day 2 定死**

## 决策

### 1. Evidence 用双指针，不用 offset

```
审计指针：  (chapter_snapshot_id, para_index, quote_text, quote_sha256)  → 指向不可变快照，永不失效
重定位指针：(chapter_id, quote_sha256, para_index_hint, occurrence_k)   → 在当前正文里定位
```

### 2. 后端永不对外发 offset

Issue / Evidence 一律用 `(para_index, quote_text, occurrence_k)` 三元组。**这个契约 Day 2 就定死。**

### 3. STALE 的边不进审阅队列

> **原则：系统发现自己不确定时的默认动作是闭嘴，不是提问。**

## 论证

### 为什么 offset 不够

文档只给了 `evidence_artifact_id + source_start_offset + source_end_offset`。这是个隐藏炸弹：

Artifact 不可变是对的，但**作者改了第 143 章后，旧 evidence 指向的是历史版本的原文**——审计上正确，UI 上却无法在当前正文里定位，而作者会以为系统在骗他。

更糟：那条 Fact 的依据可能**已经被删掉了，系统却静默地继续把它当 CANON 喂给 Writer**。

### 为什么后端不发 offset

即使 v1 没有 TipTap（见 ADR 0007），offset 仍是三重错位的温床：

1. **坐标系不同**：后端字符 offset vs ProseMirror pos（node 边界各算 1，40 段的章节里差 41）
2. **单位不同**：JS 的 `String.length` 是 **UTF-16 code unit**，Python 的 `len()` 是 **code point**。网文人名爱用生僻字（扩展 B 区汉字如 𤩝 在 JS 里算 2 个单位）→ **只在特定章节出现的 off-by-one**
3. **时间不同**：服务端返回的 span 基于快照 N，前端 doc 已经是 N+7

这些会以「偶尔位置差一两个字」的形态出现，**被误当成小 bug 调两周**。

三元组锚没有这些问题，而且它对「作者在自己的编辑器里改了正文」这个 v1 的常态是天然鲁棒的。

## `revalidate` 的行为

正文变更 → 在新快照里找不到 `quote_sha256` 的 evidence → 其关联 edge 标 `evidence_status='STALE'`。

**STALE 的边只做两件事：**

1. **立刻停火**——规则不再拿它报错。**这就完成了它 90% 的价值**，即防止「依据没了还在质疑作者」这个最伤的误报
2. 人物卡上一个**灰色小点**「依据已变更」

只有当作者**主动**点开那个人物卡、或者恰好有一条 issue 需要引用这条边时，才提示一句「这条要不要重新确认？」。

**永远不要主动推一个队列给他。**

### 为什么这条很重要

`revalidate` 的设计意图是保护「误报 < 1 条/章」。但**如果它把 47 条 STALE 推成一个早晨的审核墙，它就制造了一个新的、周期性的、作者没法拒绝的工作量——正好是它想防的东西。**

### 一层更深的洞察

**文档「Evidence 是一级对象」的直觉是对的，但它对的原因不是审计和可追溯，是误报缓释。**

**这是个产品理由，文档把它当成了工程理由。**

## 配套：LLM 抽取的 quote 对不上原文（10–30%）

抽取器返回的 evidence quote 经常不是逐字原文。处理：

1. Prompt 硬性要求**逐字引用、长度 10–40 字**
2. 后端用 `difflib.SequenceMatcher` 模糊定位，**`ratio < 0.9` 直接丢弃、不入队列**（宁可漏）
3. **`quote_sha256` 用定位成功后的原文子串算，不是用 LLM 返回的字符串算**

第 3 条是关键——否则锚从第一天起就是坏的。

## 配套：`valid_from` 只由证据决定，作者永不填章号

手填人物卡时问作者「这条关系从第几章开始有效」——**他不记得**。200 万字写了三年，他连主角哪章突破金丹都要翻。

他会填 1，或者随便填，或者放弃填。**结果：图谱里全是 `valid_from=1` 的边，时间态模型退化成当前值快照图——而时间态是这个项目全部差异化的地基。**

**修法**：作者确认的是「这条事实在第 143 章的这段原文里出现过」（他看着原文点确认，**零记忆负担**），系统自己把 `valid_from=143`。`valid_to` 只在新证据 supersede 时自动闭合（见 `edge_type.exclusivity`）。

**手填表单里根本不该有章号输入框——有它就等于邀请污染。**

## 若此决策错误，修复成本是什么

**中。** 双指针是 evidence 表的两组列，去掉一组是加法的逆操作。但**三元组契约一旦泄漏 offset 给前端，收回来要改所有 API 消费者**——所以它必须 Day 2 定死，不能等。
