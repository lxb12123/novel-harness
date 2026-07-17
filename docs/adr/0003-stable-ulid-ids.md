# ADR 0003：业务 ID 用 ULID，不用 slug

- 状态：已接受
- 日期：2026-07-16
- 推翻了什么：需求文档第 9 轮的 ID 方案（`character:{slug}` / `chapter:{project_id}:{chapter_number}`）
- 必须在：**Day 2**，第一条边写进库之前

## 决策

```python
def new_id(t: EntityType, project_id: str) -> str:
    return f"{t}:{_short(project_id)}:{ulid.ULID()}"
```

`slug` / 人名 / `chapter_number` 一律降为**可变属性**。

唯一例外：内容寻址的 `artifact:sha256:{hex}`。

文档「Neo4j 和 Qdrant 必须共享稳定业务 ID」的**原则 100% 保留**——换的是实现。

## 论证

文档第 9 轮写的是：

```
character:{slug}
chapter:{project_id}:{chapter_number}
```

**这条「稳定 ID」是假的，而且它恰好在这个项目最核心的场景上炸。**

M4 的增量抽取必然产出同一人物的多个候选（顾清音 / 清音 / 顾姑娘）。作者一合并，slug 变了，**全部 edge / evidence / alias / decision_log 引用断链**。

更根本的矛盾：**这个项目把别名当一等公民**（提及匹配是整个产品的地基，见 ADR 0004），**却用别名派生的 slug 当主键**——这是自相矛盾。

`chapter_number` 同理：作者插入一章「第 47.5 章」或重排卷次，全部章节 ID 位移。

## 为什么选 ULID 而非 UUIDv4

- **单调可排序**：时间前缀便于调试、便于按创建顺序扫描
- `project_short` 前缀：防跨项目误引用，在日志里一眼可见

## 实施

```python
NH_UUID_NAMESPACE = uuid.UUID("...")   # 改这个常量 = 全量索引作废
```

配 **10 个 golden 值测试**钉死（固定 business_id 的输出硬编码进测试文件），任何人改动生成逻辑立刻红灯。

## 配套：append-only 决策日志（见 ADR 0007 的 `decision_log`）

ULID 保护的是「ID 不因别名合并而变」。但还有一件**真正不可重建的资产**：作者已经点过的每一次确认。

保护它的办法是 `decision_log`：只增不改，**用文本引语（`quote_text` + `quote_sha256`）而非 ID / offset 做锚**。这样即使将来换存储、改 edge schema、重跑抽取，作者点过的确认都能重放回新 schema。

没有它，任何一次 schema 变更都要求作者重新点一遍全部确认——**而那一刻就是项目结束的时刻**。对开源项目还有一层：v1 → v2 的架构大改是必然的，如果每次大改都要求早期用户重新审核一遍图谱，你会在 v2 发布当天流失掉全部早期用户，**而这些人恰恰是唯一会给你写 issue 和 PR 的人**。

两天的工作量。

## 若此决策错误，修复成本是什么

**不对称，这就是本 ADR 的全部理由：**

- Day 2 改：**零成本**（还没有数据）
- M4 改：**重写数据层** + 全量数据迁移 + 作者重新确认一遍

不存在「ULID 错了」的修复场景——ULID 的成本只是 ID 不可读（调试时要 `JOIN node` 看名字），这是已知且可接受的。
