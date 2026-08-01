# 架构决策记录（ADR）

每份 ADR 回答四件事：**决策是什么、推翻了原需求文档的哪一条、论证、若此决策错误修复成本是什么**。

v1 的完整实施计划见 [`../PLAN.md`](../PLAN.md)。

| # | 决策 | 推翻了原文档的 | 状态 |
|---|---|---|---|
| [0001](0001-no-neo4j-in-v1.md) | 故事图谱进 SQLite，不用 Neo4j | 原则 12、第 9 轮表态 | 已接受（含加回触发条件） |
| [0002](0002-no-vector-retrieval-in-v1.md) | v1 不做向量检索 | 原则 12、第 9/10 节 | 已接受（含 v1.1 选型与触发条件） |
| [0003](0003-stable-ulid-ids.md) | 业务 ID 用 ULID，不用 slug | 第 9 轮 ID 方案 | 已接受（Day 2 必须落地） |
| [0004](0004-declaration-over-extraction.md) | 声明优于抽取 | 第 22 节第 2 项、第 17 节 | 已接受（最重要的单点改动） |
| [0005](0005-set-judgment-only.md) | 只做集合判断，不做语义判断 | 第 13 节 Validator | 已接受（**R5 覆盖率待 Day 1 实测填入**） |
| [0006](0006-evidence-double-pointer.md) | Evidence 双指针 + STALE 不进队列 | 第 7/14 节的 offset 方案 | 已接受（Day 2 契约） |
| [0007](0007-manuscript-lives-on-disk.md) | 正文在磁盘上，v1 不做编辑器 | 第 4/17 节、第 22 节第 12 项 | 已接受（**2026-07-19 时机松动**：应用内 CM6 进 v1，正文真相源仍在磁盘） |
| [0008](0008-related-to-is-undirected.md) | `RELATED_TO` 无向，存储时规范化 | —（补 PLAN **从没回答过**的问题） | 已接受（M1 写关系边之前必须定死） |
| 0009 | M2 kill-gate 的裁决 | — | **未写，且现在不该写**——协议 §8 定死它「跑完写」。在有 `runs/*.jsonl` 之前写裁决，正是预注册要防的那件事 |
| [0010](0010-writer-boundary.md) | Writer 边界：`assemble()` 能看见什么 | —（补 `EVAL_PROTOCOL.md` §8 点名要的那份） | 已接受（**`draft/assemble.py` 存在之前必须定死**：第 4 道守卫明说拦不住「完整 PLANNED 进 prompt」，那一格只有它和 review 守得住） |
| [0011](0011-bilingual-draft-length.md) | 双语长度契约与通用模型调用计划 | 固定中文短输出 / Claude 原生调用假设 | 已接受（修正案 5 的产品边界；先于第一份 `runs/*.jsonl`） |

## 为什么这些 ADR 是公开的

一个仓库里有明确的、公开的、**会让作者自己停下来的 kill-gate 和触发条件**，是最强的可信度信号——比任何架构图都更能招来认真的贡献者。

## bench/

ADR 0001 的实测证据。可复现：

```bash
cd docs/adr/bench
python scale_bench.py    # 构造 500 章 fixture（novel.db, ~24MB）并打印 5 类查询的延迟表
python fanout.py         # 打印 1/2/3 跳的扇出对比
```

`novel.db` 是生成物，不要提交进 git。

**fixture 的边界**：它建模的是**原需求文档**的 17 节点 / 20 关系 schema，不是本计划最终的 8 节点 / 9 关系 schema。在最终 schema 下图会显著更小——这只会让 ADR 0001 的结论更强。
