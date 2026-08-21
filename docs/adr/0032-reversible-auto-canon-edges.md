# ADR 0032：自动 Canon 的地点、状态、关系边先可逆、后自动

- **状态**：已接受
- **日期**：2026-08-17
- **推翻了**：不推翻新决策，而是**正式执行 [ADR 0020](0020-clean-extraction-auto-canon.md)
  已满足的推翻条件**：ADR 0020 说「出现『作者改不回来』的形态：某一维事实
  自动写进去了，而 `corrections.py` 没有对应入口。那时先补编辑入口」。
  本 ADR 确认 `LOCATED_AT / HAS_STATE / RELATED_TO` 三类正是这种缺口，
  并补上完整的纠错路由；在纠错入口完成前，这三类不得进入 auto-Canon
  allowlist。
- **相关**：[ADR 0020](0020-clean-extraction-auto-canon.md)（自动 Canon 的前提）、
  [ADR 0008](0008-related-to-is-undirected.md)（`RELATED_TO` 无向规范化）、
  [ADR 0006](0006-evidence-double-pointer.md)（证据双指针）

## 决策

**第一期只开放抽取器会自动写入、且产品已经能给出类型化编辑器的三类边：**

```python
AUTO_CANON_CORRECTABLE_EDGE_TYPES = frozenset({
    EdgeType.LOCATED_AT,
    EdgeType.HAS_STATE,
    EdgeType.RELATED_TO,
})
```

它同时是 auto-Canon allowlist 的**上界**；抽取模块不得另抄一份更宽的集合。
**无纠错入口不得 auto-Canon**：allowlist 之外的 edge type，在稳定 ID 的
读取、修改、软撤回、改归属、决策日志和 UI 跳转全部落地之前，一律不得
自动升入 Canon。

纠错请求只提交稳定 `edge_id`、类型化目标和 `expected_canon_version`，
不接收章号。服务端从旧边继承 `valid_from_chapter`，验证它仍是
ACTIVE/current CANON，并满足二者之一：

- `evidence_status != STALE`；或
- 存在 `replacement_edge_id = edge.id` 的 ACTIVE `canon_edge_override`
  （作者已接管该语义槽，即使恢复出的 extractor-origin 行后来证据 STALE
  也仍可继续修改、撤回、再次改归属）。

只有没有 ACTIVE override 保护的裸 STALE 机器边才拒绝。

核心机制：

- `canon_edge_override` 是 append-only 审计表：记录 slot_key、edge_type、
  source_edge_id、replacement_edge_id（NULL = 撤回 tombstone）、
  before/after props JSON、action=EDIT|REASSIGN|RETRACT、
  decision_log_id 和 status。每个语义槽至多一条 ACTIVE override。
- 语义槽由图层唯一函数 `canon_edge_slot_key()` 计算：
  location=`subject+type+valid_from`，state=`subject+type+dim_key+valid_from`，
  relation=`normalized_pair+type+valid_from`。纠错、抽取 staging、
  auto-Canon promotion 和重放全部调用它，禁止各自拼字符串。
- identity 改变时：同一事务软撤回旧边、写 ACTIVE author override、
  用继承的 `valid_from_chapter` 建/恢复 replacement
  （`source=AUTHOR, evidence_id=NULL`）。identity 不变时（只改
  HAS_STATE value / RELATED_TO 显示值）：先追加 before/after override，
  再用专用图层方法只更新当前 `props_json` 投影。
- A→B→A 恢复旧 identity 时保留旧行的 extractor origin/evidence，
  active override 的 replacement 指针赋予 author ownership；
  读路径、STALE 退休和机器重放都必须把该 active replacement 当作
  author-owned。
- 所有写入与 canon version bump、decision log 在一个业务事务。
  旧 edge ID 在成功修改后返回 replacement edge ID；客户端换选择状态，
  不能继续 PATCH 已 RETRACTED 的 ID。
- 抽取 staging/promotion 在写机器边前同时检查 ACTIVE override 和冲突槽
  中的 author-owned current edge，命中时只记 suppressed audit/proposal，
  禁止把 AUTHOR source 改回 extractor。

## 为什么

### 1. ADR 0020 的退路是「可查 + 可改」，不是「可查」

ADR 0020 自动升 Canon 时承认的代价是：作者可能一直不看日志，错误会
静默累积。退路是 `corrections.py` 的编辑入口。但今天的 `corrections.py`
只覆盖 knowers / participants / KNOWS↔BELIEVES；`LOCATED_AT /
HAS_STATE / RELATED_TO` 自动写进 Canon 之后，作者**看得到改不掉**——
这正是 ADR 0020 自己定义的那个不可接受状态。本 ADR 就是把它补上。

### 2. 类型受限是因为编辑器是按类型给的

「任意图编辑 API」会给作者一张能自由改 type/src/dst 的表单，等于把
时态模型（valid_from 继承、单值约束、RELATED_TO 无向规范化）全部暴露
给误操作。类型化编辑器把可改的维度压到产品能解释的范围内：
地点改目标、状态改值/主体、关系改对端/显示值——章号一律由证据决定。

### 3. override 是「作者接管」的唯一可追溯表达

作者纠错后机器重放（正文重抽 / 别名重放）必须知道「这个槽现在是作者的」。
把 `edge.source` 原地改成 author 会毁掉起源信息——正文换快照时旧机器
事实会逃过退休。append-only override + replacement 指针让
「起源 = extractor、当前归属 = author」同时成立，UI 可以诚实显示
「自动提取 · 作者已修改」。

## 代价（承认，不粉饰）

- `canon_edge_override` 是新的持久审计面，读路径和退休逻辑都要认它。
- A→B→A 恢复旧 identity 时保留 extractor origin，会让「这条边是谁的」
  变成一个需要查 override 才能回答的问题（读路径必须投影）。
- 自动 Canon allowlist 被三类限制住，其余可自动写入的边类型
  （如果将来有）在纠错入口补齐前不能开放。

## 什么条件下推翻本 ADR

- 出现「作者接管后仍被机器重放覆盖」的可复现竞态（override 保护失效）。
- 需要开放 allowlist 之外的新边类型自动 Canon——先补该类型的
  稳定 ID 读取/修改/撤回/改归属/审计入口，再开。

## 若此决策错误，修复成本是什么

**中等。** 纠错入口是 ADD 不是 DELETE：三类编辑器 + override 表建成后，
关闭 auto-Canon 是一个 allowlist 条件；但 override 读路径漏掉任何一处
（staging、retirement、subgraph、Writer）都会让作者纠错被静默覆盖，
那比没有入口更糟——所以「先可逆、后自动」的顺序本身不可逆。
