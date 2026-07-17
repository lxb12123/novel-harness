# ADR 0002：v1 不做向量检索

- 状态：已接受
- 日期：2026-07-16
- 推翻了什么：需求文档原则 12、第 9 节（Qdrant Collection 设计）、第 10 节（图引导混合检索）、第 22 节第 5 项

## 决策

v1 **不引入 Qdrant，不做任何 ANN 检索**。v1 的「检索」是确定性 SQL 查询 + mention 索引。

v1.1 加向量时的选型**现在就定死**（不是占位符）：`fastembed`（ONNX，不拉 torch）+ `BAAI/bge-small-zh-v1.5`（dim 512, 90MB）+ `sqlite-vec`。全程无 Docker、无 torch、约 100MB 一次性后台下载。

## 论证

### (a) 文档第 11 节的 10 个上下文分区里，只有 1 个需要 ANN

文档自己的 A–J 分区已经暗示了答案：

| 分区 | 内容 | v1 的来源 | 需要 ANN？ |
|---|---|---|---|
| A | Hard Canon Facts | 图查询 | 否 |
| B | Current Character / World State | 图查询（`state_at`） | 否 |
| C | Relationship and Address Rules | 图查询 | 否 |
| D | Character Knowledge Boundaries | **集合差集**（见 (b)） | 否 |
| E | Chapter Must / May / Must Not | ChapterBrief（作者手填） | 否 |
| F | Previous Chapter Continuity | `WHERE chapter=N-1 ORDER BY scene_number DESC LIMIT 2` | 否 |
| G | Relevant Historical Evidence | **mention 索引**（确定性部分） | **唯一的候选** |
| H | Style and Dialogue Examples | payload 规则抽样 | 否 |
| I | Current Scene Plan | 场景块 | 否 |
| J | Output and Writing Rules | 常量 | 否 |

F 尤其值得说：用 ANN 去「找」上一章结尾，是**把一个必然正确的操作换成一个概率性操作**。

G 的确定性部分——「本场景人物最近 k 次出场」——是：

```sql
SELECT ... FROM mention WHERE node_id IN (:cast) AND chapter < :n
ORDER BY chapter DESC LIMIT :k
```

**比 ANN 准。** v1 交付 10/10 分区，9 个确定性 + 1 个精确索引。向量加的是**语义相似召回**——那是真价值，但它是 v1.1 的价值。

### (b) 项目的核心主张，在结构上就不是检索问题

「第 152 章时谁不知道血脉秘密」是一个**集合差集**，不是段落召回。

**答案不在任何一段原文里**——任何 RAG 都检索不到它，不是因为检索得不好，是因为它不存在于文本中，只存在于聚合里。

推论（写进 `EVAL_PROTOCOL.md` 的适用范围）：**v1 不需要跑「向量 RAG」对照组来证明自己**。普通 RAG 在结构上就做不到这件事。该对照组只有当项目将来宣称「通用检索更强」时才需要——v1 不宣称。

### (c) 向量的第一天成本不是「装 Qdrant」，是三件现在做不对的事

1. **中文 sparse 选型。** Qdrant 官方 BM25 在中文上基本不能用：`"tokenizer": "multilingual"` 不做中文分词（中文无空格），实测 MIRACL-zh recall@10 ≈ 0.05%、MAP 0.0006 —— 低于随机。见 [qdrant/qdrant#8014](https://github.com/qdrant/qdrant/issues/8014)，2026-01-30 提交，至今未修、无官方 workaround。**照抄官方 hybrid search 教程会得到一个「跑通了但 sparse 通道是死的、两周后才发现」的静默失效。**
2. **分块策略。** 文档第 9 节的 Scene-aware + Entity-aware + Temporal-aware Chunking 需要真实的检索失败案例才能调对。
3. **评测集。** 没有它，无法判断任何检索改动是改进还是退步。

修 (1) 的正解是 BGE-M3（单模型同出 dense + sparse + ColBERT，原生中文），但它要 **2.2GB + torch**——直接杀死 `uvx` 一条命令的装机叙事。

**这三件事全都要等到有真实检索失败案例才能做对。**

## 顺带永久删除的设计

`style_dense` / `interaction_dense` 命名向量（文档第 9 节）：**伪信号**。中文没有现成的文风 embedding 模型，你只能拿同一个模型编码同一段文本 = 把 `raw_dense` 存两遍。这类结构化信号本就该走 payload filter。

## 现在就免费做的事

`chapter` 表加 `indexed_version INTEGER DEFAULT 0`；检索 / 上下文拼装的返回结构里带 `{canon_version, indexed_version, staleness}`，v1 里 `staleness` 恒为 0。

**字段免费，语义免费，历史补不回来。** 这是把文档第 8 轮的「Index Watermark」从一个子系统缩成一行断言的做法——也是对「消掉双写一致性是一次性的、不可积累的」这条批判的回应：加回 Qdrant 时不必在几千行假设了单事务的代码里追加 staleness。

## 加向量的触发条件

kill-gate 通过后，**作者在真实使用中报出「AI 忘了第 87 章那场戏」这类语义召回失败 ≥ 3 次。**

不是「等我有空」。

## 若此决策错误，修复成本是什么

**约一周**：`fastembed` + `sqlite-vec` 建索引，接进 G 分区。因为 `indexed_version` 字段和 staleness 语义 v1 就在，接入点是已知的、单一的。
