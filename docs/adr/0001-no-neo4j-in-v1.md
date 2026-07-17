# ADR 0001：v1 不使用 Neo4j，故事图谱进 SQLite 单库

- 状态：已接受
- 日期：2026-07-16
- 推翻了什么：需求文档原则 12「第一版必须保留 Neo4j + Qdrant」，以及第 9 轮的原话「Neo4j Qdrant Kafka Debezium Temporal 复杂多 Agent 我要的就是图还有向量」

## 决策

v1 用 **SQLite 单文件**承载故事图谱，`node` / `edge` 二表建图，时态过滤与 supersede 收敛在 `graph/queries.py` 一处。不引入 Neo4j。

**「图」这个主张 100% 保留**——时间态属性、双时间轴、information_scope、evidence、confidence 一个字段不少。改的是承载方式，不是主张。

## 背景

原文档第 4 轮讨论过 NetworkX + SQLite，第 9 轮明确否决，选定 Neo4j + Qdrant。本 ADR 推翻的是这个选择，理由不是「Neo4j 太复杂」——那个理由不足以推翻一个明确表态。

## 论证

### (a) 正确性问题，不是复杂度问题：Neo4j 让文档第 15 节的 Commit Protocol 在数学上无法实现

第 15 节要求「Neo4j 事务提交新事实 → 生成新 Canon Version → PostgreSQL 写 Commit Record 与 Index Job」是一个原子动作。跨两个数据库没有分布式事务，而第 9 轮亲手删掉了 Kafka / Debezium —— 那是唯一的 Transactional Outbox 载体。

于是 `STALE_BASE_VERSION` 检查、幂等键、索引水位线全部建立在一个可能已经漂移的版本号上。**为了「图」引入 Neo4j，代价是这个架构最引以为傲的东西——确定性 Kernel、原则 2 到 5——从「数据库强制」降级成「祈祷不崩」。**

图谱进单库后：
- 整个提交塌缩成**一个事务**
- `STALE_BASE_VERSION` 变成一个 `SELECT ... FOR UPDATE`
- 幂等变成一个**唯一索引**

### (b) Neo4j 的独门收益，在这个产品里恰好是被禁止使用的能力

实测见 `bench/`（500 章规模，79,430 节点 / 103,264 边 / 23.7 MB）。以一个主要人物为起点、带时态过滤（as-of ch380）的可达性：

| 展开深度 | 节点数（fanout.py 的边类型集） | 可达 Character |
|---|---|---|
| 1 跳 | 69 | 1 / 200 |
| 2 跳 | 154 | 86 / 200 (43%) |
| **3 跳** | **4,720（30 倍爆炸）** | 86 / 200（**没有新增人物**） |

3 跳的节点数从 154 爆到 4,720，涨了 30 倍，但**一个新人物都没多带来**——爆炸全部来自 `APPEARS_IN` / `SUPPORTED_BY` 这类星形高度数边，扫出来的是 Fact / EvidenceRef / State 这些结构性节点。3 跳既不可视化（4,720 个节点糊成一团），也不增加信息（人物覆盖仍是 86）。

而文档第 10 轮自己定的原则是「**不能让用户全程面对巨大关系图**」。所以 v1 硬编码 **hops ≤ 2**。

带强类型过滤（只展开 `RELATED_TO` / `KNOWS`）的实测，正好落在产品需要的区间：

| 深度 | 节点数 | 计划的 done_when |
|---|---|---|
| 1 跳 | 12 | ≤ 12 ✓ |
| 2 跳 | 20 | ≤ 30 ✓ |
| 3 跳 | 153 | （不做） |

**Neo4j 相对关系库的独门优势正是 3+ 跳变长遍历和 GDS 图算法。我们付全部的架构税，买一个产品需求明令禁止的能力。**

补充：文档第 7 节要求的全部 5 类查询，在 SQLite 上（一个对 PostgreSQL 而言故意悲观的替身）实测全部 p95 < 10ms：

```
Q1  state-as-of-chapter (单人物)          p95 = 0.03ms
Q1b state-as-of-chapter (全部 200 人物)   p95 = 4.07ms
Q2  2-hop temporal subgraph @ch380        p95 = 0.31ms
Q3  knowledge boundary (单人物 @ch)       p95 = 0.05ms
Q3b leak check: char X 能否提及 fact F     p95 = 0.04ms
Q4b open foreshadows @ch (全书)           p95 = 2.61ms
Q5  causal chain depth<=8                 p95 = 0.03ms
```

延迟从来不是选 Neo4j 的理由。

### (c) 反悔成本严重不对称

- SQLite → Neo4j：导出一张边表 + import，**约一天**
- Neo4j → 原子提交：Kernel 级重写，或把刚删掉的 Kafka + Debezium 加回来

项目现在零代码、零真实负载，是信息最不充分的时刻。**此时应当选可逆的那边。**

## 诚实的代价（四条，不粉饰）

1. **失去 Neo4j Browser。** 这是真损失，开发早期用它肉眼看图谱确实好用，没有等价替代。缓解：产品本身就是局部图谱视图；临时探索 dump 到 NetworkX / Gephi。接受现实：**头两周会难受。**
2. **变长遍历要写 SQL。** 但 v1 hops ≤ 2，两层显式 JOIN 比递归 CTE 更快更好读。一次性成本。
3. **将来的图算法（PageRank 找主角、社区发现找支线）要 NetworkX 在内存里算。** 注意这跟第 4 轮拒绝的方案**不是一回事**：当时拒绝的是「NetworkX 当存储 + SQLite 当持久化」——那个方案该拒，它没有事务、没有并发写。这里是 SQLite 当唯一真相源（事务 / 约束 / 并发全有），NetworkX 只当临时计算层，24MB 的图 load 进内存 < 1 秒，算完即弃。**存储和计算是两个决定，上次否掉的是前者，这次不涉及前者。**
4. **README 上「Neo4j + Qdrant」比「SQLite」唬人。** 这是真的，且是保留 Neo4j 的**最强剩余理由**。但它是营销理由，不是工程理由。如果想清楚了就是要这个，那是一个正当的产品决定——只是别用工程理由包装它。

反向的产品收益：贡献者改一条规则不需要同时会 Cypher + SQL + 两库同步协议。

## 加回 Neo4j 的触发条件（凭条件，不凭感觉）

1. 出现一个 SQL 确实**写不了或写不快的查询形状**（是「形状」不是「慢」——慢就先看执行计划）
2. 需要 GDS 级图算法且 NetworkX 内存方案顶不住（按当前 24MB，意味着图要涨 100 倍）
3. 单书超过约 500 万边

**届时的正确迁移路径**：把 Neo4j 降级为**投影**——真相和 canon_version 留在 SQLite，Neo4j 从 SQLite 异步重建，走文档第 14 节**已经有的** `ProjectionWatermark`。所以先做单库本来就是通往它的第一步，不是弯路。

## 若此决策错误，修复成本是什么

**约一天**：`edge` 表导出 CSV → `neo4j-admin import`。时态字段、information_scope、evidence 引用全部原样搬迁，因为 schema 本来就是按属性图设计的。

## 证据

`bench/scale_bench.py`（构造 500 章 fixture 并跑 5 类查询）与 `bench/fanout.py`（跳数爆炸实测）。

```bash
cd docs/adr/bench
python scale_bench.py    # 生成 novel.db (~24MB) 并打印延迟表
python fanout.py         # 打印 1/2/3 跳的扇出对比
```

**注意 fixture 的边界**：这两个脚本建模的是**原文档的 17 节点 / 20 关系 schema**（含本计划已删除的 `DOES_NOT_KNOW` / `PARTIALLY_KNOWS`、以及 `Fact` / `State` / `EvidenceRef` 等节点），不是本计划最终的 8 节点 / 9 关系 schema。在最终 schema 下图会**显著更小**（79,430 个节点里有 30,000 个 EvidenceRef + 15,000 个 Fact 会消失）——这只会让本 ADR 的结论更强，不会更弱。
