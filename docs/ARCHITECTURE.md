# Novel Harness 总体设计

> **这份文档是入口。** 读它就够，不用读 92KB 的 [`PLAN.md`](PLAN.md)。
>
> 三份文档的分工：
> - **本文** = 系统是什么、怎么分层、范围怎么一层层升上去 ← 你在这里
> - [`PLAN.md`](PLAN.md) = 完整实施计划 + 对原始需求文档的逐条裁决（92KB，查证用）
> - [`adr/`](adr/) = 单个决策为什么这么定、推翻了什么、错了怎么办

---

## 1. 一句话

**唯一一个知道「谁在第几章还不该知道什么」的中文长篇写作引擎。**

作者在第 88 章声明谁知道那个秘密；第 152 章他写某个场景时，系统在他敲第一个字之前告诉他：在场的三个人里谁还不知道、谁相信着一个错误版本。同一份认知边界进入 AI 起草的 prompt，让模型也不会说漏嘴。

## 2. 为什么它能存在

扫遍商业产品（Sudowrite / NovelAI / Novelcrafter）、中文开源（InkOS 8k★ / NovelForge / AI_NovelGenerator）、学术（GraphRAG / Graphiti / SCORE），**所有项目的图都是「客观事实图」**（谁和谁什么关系、谁在哪）。**没有一个做「主观认知图」**（谁知道 / 谁不知道 / 谁信着错的）。

而这是 LLM 靠 scaling 短期解决不了的：它是 state tracking 问题，不是文笔问题。GPT-4 在信息不对称对话（FANToM）上的跨题型一致性 26.6%，人类 87.5%。

**其余一切（时态图谱、混合检索、Harness Kernel）都已经被别人做过。这一条没有。所以它是 README 第一行，其余全是支撑它的。**

## 3. 核心翻转：声明，不是抽取

| | 原始需求文档 | Novel Harness |
|---|---|---|
| 认知/秘密/伏笔从哪来 | LLM 自动抽取全书 | **作者声明** |
| 300 章的书的前置成本 | 16,500 条待确认 ≈ **130 小时** | **≈ 1 小时** |
| 面板的误报 | 取决于抽取质量（关系抽取 F1 ≈ 0.276） | **零——它不读正文，不做断言** |

两个理由：**(a)** 130 小时的墙没人翻得过去，而且翻过去拿到的一半是错的；**(b)** 更根本——**秘密和伏笔是作者的意图，不是文本特征**。墙上挂了把枪，它是不是伏笔取决于作者第 200 章打不打算开枪。**这个信息物理上不在已写文本里**，抽取器只能猜。

→ [ADR 0004](adr/0004-declaration-over-extraction.md)

## 4. 分层

> **这张图今天基本是现状了，只剩两处不是。** 标 ▓ 的两层、SQLite、终端里的 `nh`、FastAPI 壳、
> 浏览器面板都已存在；**图里写「只读，无编辑器」的那一格已经不准**——应用内 CodeMirror 6 编辑器
> 进了 v1（ADR 0007 的时机松动，见该 ADR 的「修订」）。仍不存在的是：`draft/` 只有 `provider.py`
> （三臂本体 `assemble.py` 未建），`extract/` 整个没有。逐项见[当前状态](#当前状态)。

```
┌─ 浏览器面板（CodeMirror 6 编辑器，读写磁盘 md）── Vite + React
│  正文不在这里。作者用 VSCode/Obsidian 写磁盘上的 Markdown。
├─ FastAPI ─────────────────────────────────── 薄壳，不装业务
├─ 能力层 ▓ ────────────────────────────────── 纯函数，可换/可测/可贡献
│  panel/     认知矩阵、当前状态、约束转译
│  checks/    一致性规则 check(ctx) -> list[Issue]
│  draft/     上下文装配 + prompt + provider
│  extract/   增量抽取（M4）
├─ 图层 ▓ ──────────────────────────────────── 时态语义的唯一收敛点
│  graph/queries.py  ★ state_at + supersede 全系统只在这里实现一次
│  graph/models.py     Pydantic 出参，dict/Row 禁止越界
└─ SQLite 单文件 ───────────────────────────── 唯一真相源
```

标 ▓ 的两层才是这个项目本身。上面是壳，下面是存储。

**原始文档的 11 个 Kernel 子系统（Workflow Runtime / Policy Engine / Capability Registry / Idempotency Manager / Artifact Manager…）在 v1 全部不存在。** 它们解决的是「多步自动流水线 + 自治 Agent」的问题，而 v1 没有自动流水线：作者手动点一下 → 一次模型调用 → 出结果。没有可管的状态。

## 5. 状态：时间轴，不是状态机

原始文档的 13 状态 `ChapterRun` 状态机（`CREATED → TASK_PARSED → … → INDEXED`）**不存在**。真正的状态在每条边上：

```sql
edge(src, dst, type,
     valid_from_chapter,     -- 从第几章开始有效
     valid_to_chapter,       -- 到第几章失效（NULL = 至今有效）
     information_scope,      -- CANON / PROVISIONAL / PLANNED / REJECTED
     status, confidence, evidence_id, evidence_status)
```

「第 151 章时萧决在哪」是一个**闭开区间 `[valid_from, valid_to)`** 查询：

```sql
WHERE valid_from_chapter <= 151
  AND (valid_to_chapter IS NULL OR valid_to_chapter > 151)
  AND information_scope = 'CANON' AND status = 'ACTIVE'
  AND evidence_status != 'STALE'
```

### 三层作用域（+ 一个推导态）

| scope | 谁写的 | 规则能拿它报错？ | 进 Writer prompt？ |
|---|---|---|---|
| `CANON` | 作者确认过 | ✅ | ✅ |
| `PROVISIONAL` | 抽取的，带证据，未确认 | ❌ **永不开火** | ❌ 只喂面板灰显 |
| `PLANNED` | 作者声明的未来 | ❌ | ❌ **只转译成 must_not_reveal / forbidden_entities** |
| `REJECTED` | 作者否决 | ❌ | ❌ 保留以防重抽 |

**`CURRENT` 是推导，不存储**：`valid_to IS NULL AND scope='CANON'`。原始文档把 CANON 和 CURRENT 并列存储会制造一个它没解决的同步问题——一条边被取代时谁负责摘掉 CURRENT？推导少一整类数据不一致，而那类不一致的表现形式恰好是本产品最怕的：误报。

### `valid_to` 谁来写：`edge_type.exclusivity`

| 类型 | exclusivity | 含义 |
|---|---|---|
| `LOCATED_AT` | `single_per_src` | 一个人同时只能在一个地方 → 写新边自动闭合旧边 |
| `RELATED_TO` `KNOWS` `BELIEVES` `HAS_STATE` | `single_per_src_dst` | (A,B) 单值 |
| `OWNS` `MEMBER_OF` `PLANTED_IN` `RESOLVED_IN` | `multi` | 可多条同时有效 |

**这张表必须在第一条边写进库之前存在**，否则历史数据全是脏的，`state_at` 会同时返回「在青云城」和「在北荒」→ 规则误报 → M3 生死线崩。

### `valid_from` 由证据决定，作者永不填章号

问作者「这条关系从第几章开始有效」——他不记得。他会填 1，于是时态模型退化成快照图。**手填表单里根本不该有章号输入框。** 作者确认的是「这条事实在第 143 章这段原文里出现过」（看着原文点，零记忆负担），系统自己写 `valid_from=143`。

→ [ADR 0006](adr/0006-evidence-double-pointer.md)

## 6. 图 schema：8 类节点 / 9 类关系

**节点**：`Character` `Location` `Faction` `Secret` `Foreshadow` `Object` `StateDim` `Chapter`
**关系**：`LOCATED_AT` `MEMBER_OF` `RELATED_TO`※ `KNOWS` `BELIEVES` `HAS_STATE` `OWNS` `PLANTED_IN` `RESOLVED_IN`

※ **`RELATED_TO` 是无向的**，存储时 `(src,dst)` 规范化成 `(min,max)`，取对端用 `Edge.peer_of()` 而不是 `e.dst`。
对称关系（师兄妹/夫妻/仇敌）作者会两侧各声明一次，有向存储会让第 143 章的「断绝师门」只闭合一个方向 →
同一关系两个互斥的 CANON 同时有效。**这个 bug 不需要任何脏数据**——全程走正常路径，每步都成功。→ [ADR 0008](adr/0008-related-to-is-undirected.md)

（别名是 `alias` 表不是边——它是索引不是事实。）

**`node` 是全部 8 类节点的唯一身份表**；`secret` / `chapter` 是扩展表，主键就是对应的 `node.id`（1:1，复合外键连 label 一起校）。
这样 `edge.src/dst` 永远有 FK 到 `node`，`PLANTED_IN` 的 dst 是 Chapter 节点才建得出来。

从原始文档的 17/20 缩到 8/9。**增长规则：只有当某条面板分区或某条规则真的要查它时，才允许加一个类型。** 这把 schema 设计从猜测变成需求驱动。

**永久删除**：`DOES_NOT_KNOW`（实体化后多 67,500 条边 → 改闭世界推导：无 KNOWS 边 ⇒ 不知道）、`PARTIALLY_KNOWS`（欠定义，无法标注也就无法评测 → 改秘密拆子事实）。

## 7. 校验：集合判断铁律

> **任何需要回答「这句话是什么意思」的规则，一律不进 v1。**

因为 `ADDRESS_CONFLICT` / `KNOWLEDGE_VIOLATION` 隐含要求「说话人归属 + 受话人识别」——未解的 NLP 问题。中文网文大量对白不带说话人标签，一章里 8 个角色都叫「师兄」。硬做的结果是 5–20 条误报/章，项目会按自己的生死线判死。

| | 规则 | 怎么判 | 读正文？ | FP |
|---|---|---|---|---|
| **R1** | 认知边界面板 | `cast × secret` 集合查询 | **否** | **零** |
| **R2** | `FUTURE_LEAK` | 唯一专名精确匹配 | 是 | 极低（专名作者亲选） |
| **R3** | `DEAD_SPEAKS` | `(全名)(道\|说道)` × 图上 status | 是 | **零歧义——死人没有对话标签** |
| **R4** | `LOCATION_CONFLICT` | 作者声明 vs 作者声明 | **否** | 零 |
| **R5** | `ADDRESS_CONFLICT` | 仅显式说话人标签内 | 是 | **待 Day 1 探针判生死** |

R1/R4 完全不读正文，它们是零 FP 的核心。R2/R3 读正文但限定在高信号位置。**没有一条需要指代消解。**

规则是纯函数 `check(ctx: CheckContext) -> list[Issue]`——这也是开源贡献者的入口。`Issue` 的锚是 `(para_index, quote_text, occurrence_k)` 三元组，**禁止 offset**。

→ [ADR 0005](adr/0005-set-judgment-only.md)

## 8. 上下文装配：10 个分区，9 个是确定性的

| 分区 | 内容 | 来源 |
|---|---|---|
| A | Hard Canon Facts | 图 |
| B | Current Character / World State | 图（`state_at`） |
| C | Relationship and Address Rules | 图 |
| D | **Character Knowledge Boundaries** | **集合差集**（头牌） |
| E | Chapter Must / May / Must Not | ChapterBrief（作者手填） |
| F | Previous Chapter Continuity | `WHERE chapter=N-1`（确定性，100% 召回） |
| G | Relevant Historical Evidence | mention 索引 ← **唯一可能需要向量的** |
| H | Style and Dialogue Examples | 规则抽样 |
| I | Current Scene Plan | 场景块 |
| J | Output and Writing Rules | 常量 |

**D 在结构上不是检索问题，是集合差集**——答案不在任何一段原文里，任何 RAG 都检索不到。这是 v1 不需要向量、也不需要跑「向量 RAG 对照组」的原因。

## 9. 范围演进：一层一层升上去

每一层的原则：**任意时刻中断都有残值**，且**下一层不推翻上一层的数据模型**。

### v1（16 周全职 / 26–30 周业余）

| 里程碑 | 交付 | 完成标准（客观可判定） | 中断残值 |
|---|---|---|---|
| **M0** 2周 | 骨架 + 打包链路 + 数据层 | 陌生机器 `uvx novel-harness` 能跑；真书切章数 = 目录数；时态边界/supersede/ULID/架构守卫四组测试全绿 | 一个能用的中文小说数据层 |
| **M1** 3周 | 声明层 + **认知边界面板** ← 首个可发布物 | 真书上 30 分钟声明完 10 个秘密**且全程没输过一次章号**；花名册覆盖 90% 提及；第 151 章 3 个场景的矩阵逐格全对 | **一个认知边界记忆外挂（有用、可发布）** |
| **M2** 4周 | 合成小册子 + 起草 + **kill-gate** ← 最早证伪点 | 12 章 / 25 个认知陷阱；三臂 × 25 × 3 次 = 225 次生成；McNemar 出 p 值；`EVAL_PROTOCOL.md` 的 git 时间戳早于第一个结果 | + 带数字的起草器 |
| **M3** 2周 | 规则 R2/R3/R4[/R5] | **双边门槛**：真书 20 章误报 < 1 条/章 **且** 合成书真阳性 ≥ 22/25 | + 一致性检查 |
| **M4** 3周 | 增量抽取 + 三层图谱 + exception-driven | 弹给作者的冲突 ≤ 2 条/章；接受率 > 60%；别名合并后旧引用仍能解析 | + 半自动图谱 |
| **M5** 2周 | 局部图 + v1.0 | 2 跳 + 类型过滤 ≤ 30 节点 < 300ms；**你自己用它连续写完 10 章且一次没关掉面板** | v1.0 |

**M2 是公开的 kill-gate。** 三臂对照：X0（只给上一场景 800 字）/ X1（+ 图谱约束事实清单）/ X2（+ 同样约束改写成叙事化提示）。

| 结果 | 行动 |
|---|---|
| X1 或 X2 相对 X0 违规率绝对下降 ≥15pt 且 p<0.05 | 继续 M3，数字上 README |
| X2 显著 > X1 | 图有用但注入形态错了 → 全线改叙事化，重跑 |
| X0 ≈ X1 ≈ X2 | **砍掉 AI 起草线，项目定位改为「作者的记忆外挂」** |

**X2 这一臂是关键**：没有它，你会把「注入形态错了」误判成「图没用」，然后杀掉一个正确的项目。而最后一个分支是活的——**面板不依赖模型听话**。

### v1.1（有真实用户之后才做，每条都有触发条件）

| 加什么 | 触发条件 | 选型（已定，不是占位符） |
|---|---|---|
| **向量检索** | 作者报出「AI 忘了第 87 章那场戏」类语义召回失败 **≥ 3 次** | `fastembed`(ONNX，不拉 torch) + `bge-small-zh-v1.5`(512d, 90MB) + `sqlite-vec`。**不用 Qdrant + BGE-M3**（2.2GB + torch 会杀死 uvx 叙事） |
| **局部 Patch** | 规则的 `suggested_action` 被反复手动执行 | 纯 Prompt + Diff，一周。**必须排在 Evidence/revalidate 被真实压测之后**——它会持续制造 offset 漂移 |
| **TipTap 编辑器** | 真有作者要求内置编辑器 | 图层/规则/面板/起草全部不动，只加前端 + 一层 `(para_index, quote, k)` → ProseMirror pos 映射 |

### v2

| 加什么 | 为什么等到现在 |
|---|---|
| LLM Validator | 需要真实草稿样本迭代 Prompt；v1 阶段样本量为零。Schema 和表 v1 就建好（`issue_type` 直接用 ConStory-Bench 的 5 类 19 子类），v2 往里填 |
| 全屏 Story Graph Explorer | 实测 3 跳 = 4,720 节点，「一到三跳展开」在数学上就是坏的。局部图已承担 90% 价值 |
| Best-of-N + Critic | N 倍成本换一个你还没能力评判好坏的东西 |
| Run Inspector 页 | 数据 v1 就在（`model_call` 表写满）；可观测性的价值在「被记录了」不在「被画出来了」 |
| 事件因果链 | 关系抽取 F1 0.276，因果只会更低；且**没有任何规则依赖它**——纯成本零收益 |

### Neo4j：触发条件制，且是投影不是真相源

**不是「以后一定加」，是「满足条件才加」：**
1. 出现 SQL 写不了或写不快的**查询形状**（是形状不是慢——慢先看执行计划）
2. 需要 GDS 级图算法且 NetworkX 内存方案顶不住（当前 24MB，意味着图要涨 100 倍）
3. 单书超过约 500 万边

**届时的正确路径：Neo4j 降级为投影**——真相和 `canon_version` 留在 SQLite，Neo4j 异步重建，走 `ProjectionWatermark`。所以先做单库是通往它的第一步，不是弯路。

→ [ADR 0001](adr/0001-no-neo4j-in-v1.md)

### 永不做

`DOES_NOT_KNOW`/`PARTIALLY_KNOWS` 边 · `style_dense`/`interaction_dense` 命名向量（伪信号）· 事件因果自动抽取 · 伏笔自动抽取（范畴错误）· 扁平 Proposal 队列（错误心智模型的 UI 化身）· LangGraph 适配层（为不存在的需求维护抽象层）· Kafka / Debezium / Temporal · 无人工确认的 Canon 修改

## 10. 不可违反的约束

违反其中任何一条，都会以「误报」或「作者弃用」的形式在几周后炸出来。

1. **时态过滤只在 `graph/queries.py` 实现一次。** CI 守卫：`graph/` 之外 `import sqlite3` 直接失败（有明确的小允许名单）。
2. **StoryGraph 出参必须是 Pydantic 模型。** `dict` / `sqlite3.Row` 禁止越过接口——否则换实现时所有消费者都在解 JSON 列。
3. **`PROVISIONAL` 永不开火、永不断言为真。** Agent 不得污染 Canon。
4. **完整 `PLANNED` 永不进 Writer prompt**，只转译成 `must_not_reveal` / `forbidden_entities`。
5. **后端永不对外发 offset**，一律 `(para_index, quote_text, occurrence_k)`。
6. **业务 ID 是 ULID**，slug/人名/章号一律是可变属性。唯一例外 `artifact:sha256:{hex}`。
7. **`decision_log` 只增不改**，用文本引语做锚。它是唯一不可重建的资产（作者点过的每一次确认）。
8. **系统不确定时的默认动作是闭嘴，不是提问。** `STALE` 的边只停火 + 灰点，永不推队列。
9. **规则只做集合判断，不做语义判断。**
10. **作者永不填章号。**

## 10.5 M0 落地后，M1/M2 必须知道的三件事

这三条是 M0 对抗性验证的产物，**不知道就会把已经堵上的洞重新打开**。

1. **`scene_constraints(store, pid, chapter, cast)` 的 `cast` 收的是作者写的称呼原文，不是 node_id。**
   它自己内部 `resolve_cast()`——这样调用方**没有机会**把一个人弄丢。面板渲染行也走 `resolve_cast()`，
   并把 `.unresolved` 传给 `knowledge_matrix(..., unresolved=...)`。

2. **起草 / 拼 prompt 之前必须调 `SceneConstraints.require_resolved_cast()`。**
   「师兄」指向 8 个人时它抛 `UnresolvedCast`，要弹给作者问「这一场的师兄是谁」。
   ⚠️ **这是个「必须记得调」的守卫——它是本层最弱的一环。** M2 写 `draft/` 时若忘了调，
   fail-open 就回来了（约束退化成「全部秘密都不许说」，能防泄漏但 Writer 写不出东西）。
   **M2 的第一个任务应该是把这个守卫变成类型层强制**（比如让 prompt 拼装只接受一个
   `ResolvedConstraints` 类型，而它只能由 `require_resolved_cast()` 产出）。

3. **闸门只出 `NodeRef`（id/label/name），不出 `Node`。**
   `NodeProps` 是 `extra="allow"` 的，所以一个 `props.twist="萧决在此被顾清音所杀"` 会
   顺着 `must_not_reveal` 序列化进 prompt——**保密清单自己泄密**。类型收窄之后传 `Node` 会被 pydantic 当场拒。

## 11. 术语

| 词 | 意思 |
|---|---|
| **认知边界** | 谁在第几章知道什么。本项目的头牌，也是唯一没被占的地。 |
| **声明 / 抽取** | 作者告诉系统 / 系统从正文猜。本项目押前者。 |
| **CANON / PROVISIONAL / PLANNED** | 确认的 / 抽的未确认的 / 未来的。见 §5。 |
| **supersede** | 新边写入时自动闭合被它取代的旧边的 `valid_to`。 |
| **kill-gate** | M2 的公开证伪点。失败就砍掉起草线。见 §9。 |
| **合成小册子** | 程序生成的 12 章带 25 个认知陷阱的书。无版权，是仪器不是产品——它测「约束注入是否降低违规」，不测「小说好不好看」。 |
| **心跳** | `scripts/demo.sh`。端到端还通着 = 一个每天可见的布尔值。 |

---

## 当前状态

**M0 + M1 + M1.5 已落地，M2 进行中**（609 个 pytest + 18 个 vitest 全绿，`uvx` 装机路径每次 PR 都验，`.sql` 和前端产物都在 wheel 里）：

```
db.py  ids.py  decisions.py  project.py  migrations/001_init.sql（13 张表）
graph/{models,store,sqlite_store,queries}.py    ← state_at / supersede / subgraph
panel/{knowledge,state,constraints}.py          ← 认知矩阵（头牌）+ PLANNED 进 prompt 的唯一闸门
checks/{base,location_conflict}.py              ← R4（`ALL_CHECKS` 至今只有这一条）
text/{anchor,chapterize,scenes}.py              ← (para_index,quote,k) 唯一定义 / 切章 / 场景块
declare.py  importer.py                         ← M1 声明层：引语定章号 + 证据链 + CanonWriter
cli.py                                          ← nh 的 15 个子命令（含 `nh serve`）
api/{app,deps}.py                               ← M1.5 FastAPI 壳：27 条路由 + 17 个错误映射
frontend/src/                                   ← React 工作台：28 个手写源文件、2857 行 TS/TSX（278 行测试）
frontend/src/__fixtures__/api.json              ← 从真 app dump 的 21 个端点出参（契约测试两头共用）
novel_harness/webui/                            ← ↑ 的构建产物（生成物，不入库；随 wheel 分发）
draft/provider.py                               ← M2：统一模型出口（OpenAI 兼容，三臂与生产共用）
eval/{leak,score}.py                            ← M2：泄漏集合判断 + 精确 McNemar / Holm
```

**「全绿」这句话曾经比它听起来的弱，现在不了。** 此前 `knowledge_matrix` 与 R4 的全部断言只跑在
`FakeGraph` 上——那个 Fake 自己手写了一遍闭开区间的五个条件，于是生产 SQL（`knowledge_edges_at`、
KNOWS 压 BELIEVES、STALE 停火、跨项目隔离）一条都没被执行过。`tests/test_store_conformance.py` 把
场景降解成纯数据再参数化成两个后端，**同一份断言 24 条 × {fake, real} 各跑一遍**，另有 6 条
Fake 够不着的（label 校验 / 重复边 StoreError / `secret_ids` 默认列序）只打真库。Fake 从此漂不动。

`cli.py` 在此之前是**只有 `--version` 的空壳**；现在 `nh panel` 渲染的就是本文档
开头那个框，走的是真 SqliteStoryGraph。面板不再只在终端里存在——同一个矩阵在浏览器工作台的右栏
第一个 tab 里（`api/app.py` 的 27 条路由 + `frontend/src/components/KnowledgeMatrix.tsx`）。

### M2 的当前形状：判分器先于被判者

已落地并有测试：`eval/leak.py`（草稿泄漏 = 纯集合判断，禁忌集只经 `panel/constraints`）、
`eval/score.py`（`majority` / 精确 McNemar / `compare_arms` / Holm，零重依赖）、
`draft/provider.py`（`ProviderConfig` frozen，三臂与生产共用同一次调用）、
`panel/constraints.secret_surfaces`（秘密的**内容 tell**，排除进 prompt 的显示名标签）。

**还一个字符都没有的**：`draft/assemble.py`（三臂 `PromptForm` 本体）、`draft/context.py`、
`confound_lint`、整个 `synth/`（合成小册子 + 自检）、runner（`runs/*.jsonl` 的产出者）、
`score.decide()`（FLOOR/CEILING 门 + 符号稳定 + `MIN_DISCORDANT` 的完整裁决表）、
第 4 道 arch-guard `tests/test_draft_boundary.py`（`eval/leak.py:11` 已经在宣称它钉死了边界，
**但它不存在**——按本仓库自己的判据，一个不存在的守卫比一个永远绿的守卫更糟）、ADR 0009 / 0010。

协议冻在 [`EVAL_PROTOCOL.md`](EVAL_PROTOCOL.md)，**已于 2026-07-25 单独提交进 git（`0393088`,
`2026-07-25T16:19:00-04:00`）——预注册到此成立**。它第 11 行把「先 commit 的 git 时间戳」定义成
预注册成立的**唯一**证据，那条 commit 就是它；提交时 `runs/` 不存在、一次生成都没跑过，
所以之后任何 `runs/*.jsonl` 都晚于它、都算数。**这条 commit 之后再改协议就等于改卷子**——
真要改，开一份新的、说明改了什么和为什么，别覆盖。

### 三条真书验收，一条都没验（同一个原因：手上没有真书 TXT）

- `text/chapterize.py` 被 17 个测试钉住（脏数据：卷标题 / 番外 / 作者的话），但「真书切章数 = 目录数」
  **一次都没验过**——手上只有 `tests/fixtures/demo_novel.txt` 那份 46 行 5 章的手写 fixture。
- `scripts/probe_speaker_tags.py` 已能跑（从 `chapterize` import 正则，不留第二份副本），
  但 R5 的生死数（显式说话人标签覆盖率 ≥10%？）**仍未测量**，ADR 0005 的「实测结果」一节还是空的。
- M1 的花名册 90% 提及、M3 的误报 < 1 条/章，同理欠着。

**合成小册子不能顶替它们中的任何一条**：它有强制说话人标签、强制唯一 tell，测的是注入机制不是真书行为
（EVAL_PROTOCOL.md §7 已把这条免责一并预注册）。

### 工作台的已知洞

**本节是这份清单的唯一副本。** `README.md` / `CLAUDE.md` / `frontend/README.md` 都只留钩子指到这儿。
它曾经有三处拷贝：补掉一个洞要记得改三处，改漏了就有一份文档在骗人——
**而骗人的文档比没有文档更糟，它还提供安全感**（同 `demo.sh` 注释里那条道理）。

1. ~~**浏览器里建不了人物/别名**~~ —— **2026-07-25 已补。** 左栏「花名册」旁的 ＋ 打开
   `RosterDrawer`：建 6 类节点（`AUTHORED_LABELS`，**故意不含 `StateDim` / `Chapter`**——
   前者是引擎内部的状态维度，后者由 import 生成，把它们放进「新建」菜单等于邀请作者手工
   造出引擎的内部结构）+ 加称呼（`canonical` 不在选项里，它是 `upsert_node` 的独占物；
   1 字别名 + `usable_for_rules` 在**按下按钮之前**就提示，不替作者改）。
   空花名册的文案也从中性的「空」改成「导入只切章、不认人 —— 建第一个」：
   那不是一个中性状态，是作者会卡死在那儿的地方。
   端到端验过（纯 HTTP，不碰终端）：建书 → 导入 → 花名册 `[]` → 建人物/秘密/别名 →
   用别名「魔尊」声明 → `valid_from = ch1` 由引语算出 → 矩阵 `KNOWS (ch1)`。
   **但要记着**：这条 UI 只过了 `tsc` + 构建 + 后端 HTTP 验证，**没有人在浏览器里点过**——
   ——不过 `RosterDrawer` 本身已经有 7 条 vitest（第 3 条），渲染层至少不再是全裸的。
2. ~~**没有一条命令的入口**~~ —— **2026-07-25 已补。** `nh serve --db book.db` = 建库 + 起服务 +
   挑端口 + 开浏览器；`npm run build` 的产物落在**包内** `src/novel_harness/webui/`（`uv_build`
   自动打包模块目录下的非 `.py` 文件，实测），于是 `uv build` 天然带上前端。ADR 0007 的
   「一条命令，不装 Docker」到此兑现。守卫：`tests/test_serve.py` 的两条打包路径断言 +
   `ci.yml` packaging job 的三级验证（wheel 里有产物 / 包装得上 / 装完之后 `webui_built()` 为真）。
   **剩下半个洞**：`uvx novel-harness` 仍是 stub——它不带 `--db`，而**「作者的库默认放哪」还没定**。
   那个默认位置一旦发出去就很难改（同 ADR 0007 里 `root_path` 存绝对路径的教训：路径进了库，库就不可搬家）。
3. ~~**前端没有运行时测试**~~ —— **2026-07-25 已补，且补法是钉这条缝的两头。**
   前端 ↔ 后端契约是这个仓库**唯一真正的双份维护成本**（引擎 → CLI/HTTP 两个薄壳是加法，
   前端 ↔ 后端才是乘法），而它坏起来是无声的：`tsc` 看不见后端，而 `api/types.ts`
   自称「手写真相源」——手写的东西和后端一致只是**当时**一致。

   关键决定是 **fixture 不手写**：`tests/test_frontend_contract.py` 从真 app（`TestClient`
   + 真 SQLite）dump 21 个端点的真响应，规范化掉 ULID/时间戳/路径后冻在
   `frontend/src/__fixtures__/api.json`；组件测试吃的就是这一份。于是两头各有守卫——
   **后端出参一改 pytest 先红**（逐字节比对重新 dump 的结果），**形状变了没人改组件 vitest 红**。
   用手写 fixture 做前端测试等于两份手写的东西互相验证，那正是这条缝原本的病。

   实测两侧都会红（把 `since_chapter` 改名一试：pytest 报「出参对不上」+ 指向
   `NH_UPDATE_FIXTURES=1` 和 git diff，vitest 报组件渲不出那一格）。
   规模：vitest 18 条（认知矩阵三态 / 左栏空态 / 花名册抽屉的 ADR 0004 提示与拒绝形态）。
   **剩下的**：只有 3 个组件有测试，`CenterEditor` / `LocalGraph` / `BottomBar` 等仍是零。

**仍完全不存在的**：`extract/`（M4）。

`scripts/demo.sh` 心跳已经在跑，绿的。但它量的是接缝，喂的是手写 fixture——**它不替代上面任何一条真书验收**。

真实进度以代码和测试为准，不以本文档为准——**如果两者不一致，改本文档**（[`PLAN.md` §5.5](PLAN.md) 已因此订正过三处）。
