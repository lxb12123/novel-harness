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

> **这张图今天基本是现状了，只剩一处不是。** 标 ▓ 的两层、SQLite、终端里的 `nh`、FastAPI 壳、
> 浏览器面板都已存在；**图里写「只读，无编辑器」的那一格已经不准**——应用内 CodeMirror 6 编辑器
> 进了 v1（ADR 0007 的时机松动，见该 ADR 的「修订」）。`draft/` 三块（`provider` / `context` /
> `assemble`）已齐；`extract/` 的纯结构化边界（schema / prompt / 锚定 / 名称解析）已落地，
> 后台调用与提案入库仍在实现。逐项见[当前状态](#当前状态)。

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
| ~~**R5**~~ | ~~`ADDRESS_CONFLICT`~~ | ~~仅显式说话人标签内~~ | 是 | **已砍**：2026-08-02 实测 8.2% < 10%（[ADR 0014](adr/0014-r5-cut-by-quote-coverage.md)） |

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
| **M2** 4周 | 合成小册子 + 起草 + **kill-gate** ← 最早证伪点 | 12 章 / 25 个认知陷阱；三臂 × 25 × 3 = 225 个 final cell、成功轮 225–450 次 transport call；McNemar 出 p 值；协议及五份修正案早于第一个结果 | + 带数字的起草器 |
| **M3** 2周 | 规则 R2/R3/R4（R5 已砍） | **双边门槛**：真书 20 章误报 < 1 条/章 **且** 合成书真阳性 ≥ 22/25 | + 一致性检查 |
| **M4** 3周 | 增量抽取 + 三层图谱 + exception-driven | 弹给作者的冲突 ≤ 2 条/章；接受率 > 60%；别名合并后旧引用仍能解析 | + 半自动图谱 |
| **M5** 2周 | 局部图 + v1.0 | 2 跳 + 类型过滤 ≤ 30 节点 < 300ms；**你自己用它连续写完 10 章且一次没关掉面板** | v1.0 |

**M2 是公开的 kill-gate。** 三臂对照：X0（只给上一场景最多 800 个输入 code point）/ X1（+ 图谱约束事实清单）/ X2（+ 同样约束改写成叙事化提示）。

> **那个 800 是 X0 的定义，不是产品参数**（ADR 0019 边界五）。它存在的目的是证明「给得少会崩」，
> 而三臂和产品共用 `assemble()`，于是产品一度继承了对照组的上下文预算。
> 2026-08-10 起截断长度参数化（`previous_tail_limit`）：**三臂 / `nh gate` / `nh draft` 一个字不传，
> 照旧拿冻结的 800**；`/draft` 的 PRODUCT 档从 `capability.max_context_tokens` 倒推
> （`assemble.product_tail_limit()`，比例 + 成本闸，与记忆层同一个 `TOKENS_PER_UNIT` 换算）。

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
3. **`PROVISIONAL` 永不开火、永不断言为真。** ~~Agent 不得污染 Canon。~~
   **后半句 2026-08-10 被 [ADR 0020](adr/0020-clean-extraction-auto-canon.md) 推翻**：
   一条例外 bucket 都没进的抽取结果自动升 CANON（`actor='system'`），
   三个 bucket（冲突 / 主角低置信 / 新人物）照旧进队列。
   **前半句原样有效**——改的是「怎么离开 PROVISIONAL」，不是「PROVISIONAL 能不能被当真」。
   换来的保护是「可查 + 可改」，而**编辑能力必须先于自动生效落地**（否则中间那段时间
   是「系统自动改你的书而你改不回来」）。
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

**M0 + M1 + M1.5 已落地；M2 已由维护者裁定通过（修正案 9 / ADR 0009，非数据裁决）；M3 双边门槛已过、M4 事件记忆切片已落地并通过真书三章接受度验收**（抽取 → 提案/被动确认 → 作者审阅 → 安全事件上下文全闭环；111935 第 1–3 章：26 条有效事件、冲突 0 条/章、接受率 100%，2392 个 pytest + 483 个 vitest 全绿，`.sql` 和前端产物都在 wheel 里）：

> **本节的数字是全仓唯一副本，且 `tests/test_doc_numbers.py` 会拦住第二份。**
> `README.md` / `CLAUDE.md` / `frontend/README.md` 里只留指针，不许再抄一份数字过去。
> 这不是洁癖：2026-07-30 的审计发现「620 个 pytest」在三份文档里各躺一份、「27 条路由」躺了七份，
> 而真值早已是 690 和 32——**改代码的人没有义务记得去改七个地方，所以这个病只能靠「只有一处」治**。
> 同一次审计还发现更糟的一类：本节曾把三样**已经写完、且被 60 条测试覆盖着**的东西
> （`test_draft_context.py` 12 + `test_eval_score.py` 40 + `test_api.py` 的 8 条 stub 断言）
> 列在「还一个字符都没有」里——照它排期的人会去重写已完成的工作。
> 同「工作台的已知洞」那节，唯一副本 + 别处指针。
>
> 守卫钉住的是**能在运行时数出来**的那些（60 条路由 / 59 条 /api / 1 条 501 stub /
> 19 个错误映射 / 18 个子命令 / 24 张表 / 59 个端点 / `ALL_CHECKS` 3），
> 改错必红、**删掉也必红**（不静默 skip）。
> （这一行 2026-08-10 之前写的是「路由 42 / fixture 端点 34」那种词序，
> **而守卫的正则认的是「N 条路由」「N 个端点」**——于是它躺在被守卫盯着的那一节里、
> 却一个字都没被检查，真值早已是别的数。现在改成守卫认得的写法，它才真的被钉住。）
> **它罩不住 pytest / vitest 这两个数**——在 pytest 里数 pytest 要递归，
> 所以「2392」和「483」仍然只靠人手改，改代码后请顺手跑一次 `uv run pytest -q` 更新这一处。
> （2026-07-30 那天这个数从 690 走到 801，中途在文档里错过一次——**这条盲区是真的，不是假想的**。）

> ⚠️ **「全绿」目前只在本机成立。本仓库还没有 git remote，`ci.yml` / `release.yml` 一次都没执行过。**
> 那三个 job（test / frontend / packaging）写好了、本机逐条手跑过，但**它们至今没有拦住过任何东西**。
> 尤其是 packaging 那三级验证——它是唯一能发现「wheel 里没有前端」这种静默降级的东西，
> 而那正是 `_DIST` 曾经真出过的 bug。在建起远端之前，别把 CI 当成既有保护。

```
db.py  ids.py  decisions.py  project.py
migrations/{001_init,002_m4_events,003_proposal_audit_recovery,004_chapter_summary,005_fact_edit,
            006_chat_session,007_draft_candidate,008_cache_usage,
            009_call_chapter,010_candidate_stopped,011_rule_revocation}.sql（24 张表）
                                                ← 008 是 ALTER 不建表：`model_call` 多两列
                                                  （`cache_read_tokens` / `cache_write_tokens`）。
                                                  **它不是一个功能，是一次测量**：三家三个字段名
                                                  （DeepSeek `prompt_cache_hit_tokens` / OpenAI
                                                  `prompt_tokens_details.cached_tokens` / Anthropic
                                                  `cache_read_input_tokens` + `cache_creation_input_tokens`）
                                                  在 `draft/provider.py` 归一成一份 `CacheUsage`，
                                                  下游不许再认第二遍。**只改读取侧，请求一个字节没动**
                                                  （DeepSeek 的前缀缓存全自动）。两列可空且**没有
                                                  DEFAULT 0**：NULL=端点没报、0=真的一次都没命中，
                                                  两者指向相反的动作（去查怎么开启 / 去查前缀被谁弄脏了）。
                                                  日志页展开层多一行「接着上次的输入」；
                                                  `/runs` 的全书汇总**有意不加**（理由在 `CostTotals`）
                                                ← 009 也是 ALTER：`model_call.chapter_number`
                                                  （「这一次是为哪一章花的」）。四个写入方里三个
                                                  已经在填（抽取 / 章节总结 / 产品档起草），
                                                  写作助手那一档待接。**反查是兜底不是遗产**：
                                                  这一列出现之前的旧行全是 NULL，它们的章号只有
                                                  `extraction_run` / `chapter_summary` 反查拿得到，
                                                  所以 `activity._call_chapter` 两条路都留着
                                                  （先读列、答不出再反查）。**出参形状没变**：
                                                  日志页那一行原来就有「为哪一章」，只是恒为「未记录」
db.py::migrate() **在真的要跑 DDL 之前，给有数据的库拷一份备份**（2026-08-12）——
                                                  `book.db.升级前备份-<日期>-第N版.db`，同目录。
                                                  条件是 `0 < user_version < latest`：版本已到位的
                                                  那千百次重启（含 `--reload` 的 dev server）一份都不拷，
                                                  刚建出来的空库也不拷。**必须走 `VACUUM INTO` 不能
                                                  `copyfile`**：库是 WAL，只拷主文件会得到一份看起来
                                                  正常、其实少了最近全部改动的备份。拷不成 =
                                                  **拒绝迁移**（正文在磁盘上，书照写；而库里那些
                                                  不可重建的东西没被动过）。它治的是一个发生过两次的
                                                  真事故：开着 dev server 写迁移 = 对作者的真书做了一次
                                                  没有备份、没有确认的 schema 变更（005 / 008）
graph/{models,store,sqlite_store,queries}.py    ← state_at / supersede / subgraph
events/{models,store}.py                       ← M4 事件超边 / 角色档案 / 提案仓储契约
extract/{models,prompt,locate,analyze}.py       ← M4 严格 JSON 边界 / 确定性证据定位 / 不猜名称解析
extract/{service,runner,proposals,proposal_confirm,metrics}.py
                                                  ← 后台抽取闭环 / 提案审阅 / 幂等被动确认 / 接受度指标
extract/auto_canon.py                             ← 没进三个例外 bucket 的抽取结果**自动升 CANON**，
                                                    静默、fail-safe、幂等；每组写一条
                                                    `actor='system'` 的 decision_log
                                                    （2026-08-10 作者推翻「事前逐条确认」；
                                                    退路是 corrections.py，先有它才敢开这个）
panel/{knowledge,state,constraints}.py          ← 认知矩阵（头牌）+ PLANNED 进 prompt 的唯一闸门
checks/{base,location_conflict,future_leak,dead_speaks}.py
                                                  ← R2/R3/R4（R5 已砍；`ALL_CHECKS` 共三条）
text/{anchor,chapterize,scenes,mentions}.py     ← (para_index,quote,k) 唯一定义 / 切章 / 场景块 / 称呼匹配
declare.py  importer.py                         ← M1 声明层：引语定章号 + 证据链 + CanonWriter
cli.py                                          ← nh 的 18 个子命令（含 `nh serve` / `nh gate` / `nh draft` / `nh summarize`）
api/{app,deps,activity,autopilot,chat,extraction,review}.py
                                                ← M1.5 FastAPI 壳：60 条路由 + 19 个错误映射
                                                  （59 条 /api + 1 条 `GET /`；其中 1 条是 501 stub；
                                                  M4 抽取/事件读端 + 提案审阅/被动确认路由；
                                                  提案 edit + `/canon/…` 两条改正路由（corrections.py）；
                                                  活动日志两条 + `GET /runs`（2026-08-10 由 501 点亮）；
                                                  写作助手会话七条（开 / 列 / 看 / 删 / **跑一轮**（两种收法）/
                                                  **停**，`api/chat.py`）——`agent/` 这一层在此之前在 `src/`
                                                  里一个调用方都没有；
                                                  **跑一轮 2026-08-12 起有一条长连接**（ADR 0024 第二刀）：
                                                  `POST …/turn/events` 是 `text/event-stream`，中间是
                                                  `TurnEvent`、最后一帧还是**同一个** `TurnReceipt`；
                                                  工作台走这条，`POST …/turn`（跑完才回）留着——
                                                  它就是「换回请求/响应」那条退路的全部内容，
                                                  两条共用同一个 `_TurnRun`，差别只有 `on_event` 传不传。
                                                  **「停」没搬到流上**：流断了那一轮还在跑、还在花钱，
                                                  把「停」搬过去等于让作者唯一的插手方式跟着显示通道一起死
                                                  （也是不选 WebSocket 的主要理由；次要理由是这个壳全是
                                                  同步的，而 FastAPI 的 WebSocket 端点必须 `async def`）。
                                                  断线不新增机制，退回既有的 resume；
                                                  历史版本的删除——还原没有自己的路由，走 PUT text；
                                                  候选稿两条（列一章的几稿 / 摊开某一稿的全文，
                                                  ADR 0022）——**它不是版本历史**：那儿是已经在书里的，
                                                  这儿是还摆在桌上的）
corrections.py                                  ← 改一条**已生效（CANON）**的事实：KNOWS↔BELIEVES /
                                                  事件的 knowers / participants（撤回 + 写新的，旧行留着）
                                                  **2026-08-11 起浏览器里有调用方**（矩阵那一格 +
                                                  「待确认」那一格下半截的已确认情节名单）
activity.py                                     ← 「事后可查」（ADR 0020）：`extraction_run`（跑了什么）
                                                  + `model_call`（花了多少）+ `decision_log`（改了什么、
                                                  谁改的）归并成一条时间线。出参两层（折叠行 / 展开详情），
                                                  每条带一个结构化 `jump`——**跳去哪个模块改由后端算**，
                                                  且只落在今天真存在的编辑入口上；`narrow_payload()`
                                                  是这一层的收窄点（比 `_narrow` 严：日志行没有当前章）
frontend/src/                                   ← React 工作台：44 个非测试手写源文件、8910 行 TS/TSX（2771 行测试）
                                                  （数法：`frontend/src` 下的 `.ts/.tsx`，不含生成物 `api/schema.ts`）
                                                  （M4 审阅面板：ProposalReviewTab / StateCards / hooks；
                                                  ADR 0020 的「可查」页：ActivityLog —— 顶栏一个入口、
                                                  换的是**中栏**，跳转坐标一律吃后端的 `jump`；
                                                  ADR 0020 的「可改」：KnowledgeMatrix 的 `CellEditor`
                                                  + CanonEventCast + `correctionError.ts`（409/404/422
                                                  三种拒绝三句话，**409 绝不静默重试**））
frontend/src/__fixtures__/api.json              ← 从真 app dump 的 59 个端点出参（契约测试两头共用）
                                                  其中 `chatTurnEvents` 是**长连接那一轮的原始帧**
                                                  （`event:` / `data:` / 空行都是真的）：ADR 0024 原文说
                                                  「冻成一份录像」，落地当天更正了——冻在 `tests/` 里的
                                                  录像只是又一份手写夹具，而 `api.json` 的价值从来不在
                                                  「冻住」，在**前端吃的是后端 dump 出来的同一份字节**
novel_harness/webui/                            ← ↑ 的构建产物（生成物，不入库；随 wheel 分发）
draft/provider.py                               ← M2：统一模型出口（OpenAI 兼容，三臂与生产共用）
draft/context.py                                ← M2：`ResolvedConstraints`（cast 已解析 / X1X2 同一份矩阵，编码进类型）
draft/assemble.py                               ← M2：三臂 `PromptForm` 本体（X0 是 X1/X2 的严格前缀，ADR 0010）
draft/length.py                                 ← M2：双语长度域（中文按非空白 code point / 英文按词，修正案 5 冻结档）
draft/capabilities.py                           ← M2：能力注册表 + 版本化预算公式（精确路由，未知能力 fail-closed）
draft/generate.py                               ← M2：续写一次（under-min-only、至多 2 次 attempt，两段原样拼接）
draft/product_draft.py                          ← 「章号 + 目标 → 一稿正文」的**唯一实现**（产品档）。
                                                  2026-08-11 从 `api/app.py` 的 `/draft` 路由体里提出来的
                                                  （约 120 行：capability 探测 / 记忆预算 / 长度策略 /
                                                  form 选择 / 三条装配分支），因为 agent 的起草工具要调
                                                  **同一个函数**——抄一份进工具表 = 第二条会漂的起草路径。
                                                  它收一份算好的 `DraftContext`（不收 cast、不收 store），
                                                  **也不落盘**（那是 ADR 0021，发生在拿到这一稿之后）；
                                                  出参带 1–2 份 `ModelCallReceipt`，**记账留给持有 conn 的那一层**
draft/product_context.py                        ← M4：确定性事件记忆 → 写作上下文（近八章 + 12 旧事件）
draft/product_assemble.py                       ← M4：已确认记忆前言（不进 X0/X1/X2 kill-gate 调用）
                                                  2026-08-11 换序成 `[文风][记忆][用户]`（ADR 0019 边界六）：
                                                  逐章变的记忆原来排在跨章不变的文风前面，唯一稳定的那块
                                                  被夹在中间，前缀缓存价值为零。`assemble()` 一个字没动，
                                                  三臂走的不是这个函数（`test_product_assemble.py::
                                                  test_the_gate_never_reaches_this_module` 量的就是这条）
draft/rolling_summary.py                        ← M4 后续切片：后台章节滚动总结（幂等、机器摘要仅背景）
                                                  + `coverage()`：窗口里每章「没写 / 写了没总结 / 有」
draft/summarize.py                              ← M4 后续切片：章节摘要 prompt（`nh summarize` 补档，HTTP 同一条）
agent/{ports,index,tools,loop,store,model,drafting,candidates,rules}.py
                                                ← 模式二（ADR 0019）：**工具表就是权限边界**。
                                                  `ports.py` = 模型碰得到的全部东西（`ToolContext` +
                                                  起草接线口 + 两个**只读**窄端口：摘要 / 已确认事件；
                                                  没有 conn、没有 `CanonWriter`，写入面在类型层不存在）；
                                                  `index.py` = 书内索引四层（目录 / 人物轴 / 摘要区间 /
                                                  一章正文，越往下越贵，出处一律带章号，预算从
                                                  `capability.max_context_tokens` 倒推）；
                                                  `tools.py` = 表本身 + 派发（声明由表生成，追加不插队）
                                                  + `ask_author`（ADR 0024）：**模型决定什么时候问，
                                                  作者决定答什么**——出参形状是「一句话 + 几个可点的选项」
                                                  （散文进不来），handler 里一个 `context.` 都没有
                                                  （**引擎往问句里加不了一个字**；「这句话有没有说破秘密」
                                                  是语义判断，ADR 0005 不做），而它**结束这一轮**：
                                                  判据是出参的类型（`AuthorQuestion`），不是一张会漂的工具名表
                                                  + `BatchRunner`：**一批之内没有副作用的那几条同时跑**
                                                  （ADR 0022；判据是 `ToolSpec.concurrent`，默认 False，
                                                  今天只有 `draft_chapter` 是 True，`save_draft` 是屏障）。
                                                  **并发默认关着，只有装配层能开**——只有开连接的那一层
                                                  知道这条连接跨不跨得了线程，而 `check_same_thread=False`
                                                  并不让它变成线程安全的（实测：两条线程同一句 SQL 必
                                                  `InterfaceError`），所以碰库的每一段都要过
                                                  `ToolContext.db_lock`，慢的那一段（模型调用）在队外面；
                                                  `candidates.py` = **候选稿的存取**（ADR 0022）：
                                                  起草的产物落在 `draft_candidate` 表，**不落在书里、
                                                  也不落在对话里**——无状态的 wire 每一轮重发整个消息数组，
                                                  三稿 9,000 字进对话就是每一轮都在付它的钱。
                                                  对话里只有 id + **定长**预览（`PREVIEW_UNITS`）+ 那一稿的
                                                  自述；清理只清**已经进过书**的（那些在版本历史里退得回去），
                                                  没落过盘的一行都不删；
                                                  `loop.py` = **循环归模型、停止条件归代码**：canonical 对话
                                                  （`prefix` 与 `messages` 两个字段 ⇒ 稳定前缀排在最前面是
                                                  构造不出反例，边界六）+ `project(对话, 章号, budget)`
                                                  （绑别的章的工具返回不进这一份，边界五；剪枝顺序写死，
                                                  作者说的话永不被剪，装不下就停）+ 十一种停法
                                                  （说完了 / 步数 / 花费按 token / 一口气要做太多 /
                                                  作者打断 / 反复同一调用 / 什么都没说 /
                                                  同一工具连续失败 / 装不下 / 联系不上模型 /
                                                  **停下来问作者**），措辞唯一出处是 `stop_wording()`；
                                                  **每一次模型调用都必经 `ledger`（必填，无 `None` 取值）**
                                                  ——账记在持有 conn 的那一层，`ToolContext` 上仍然没有 conn；
                                                  \+ **边跑边发事件**（ADR 0024）：`on_event` 是一个
                                                  `Callable[[TurnEvent], None]`，形状同 `persist` / `ledger`，
                                                  **不传 = 一声不喊、行为逐字节不变**（那也是「换回请求/响应」
                                                  那条退路的全部内容）。**引擎不认识传输**——长连接是适配器的事；
                                                  **事件里没有工具查到了什么**（边界一：一轮的返回一直是投影
                                                  过的，事件流不许把它摊开，而推上屏过的东西改代码删不掉）。
                                                  `store.py` = 会话表（`chat_session`/`chat_message`）的读写：**判据是
                                                  「读回来重建出的 `Conversation` 和存进去之前逐字节相同」**——不相同 =
                                                  resume 之后模型看到的是另一段历史，而没有任何东西会报错；写入面只有
                                                  追加（canonical 只增不改），带一个乐观并发闸（两个标签页对着同一段会话
                                                  各跑一轮时后到的被拒，而不是交织成一段谁也读不懂的历史）；
                                                  `model.py` = `ModelPort` 的适配器：把作者按下的「停」**带进流式循环**
                                                  （信号一亮迭代器抛出去 → `complete()` 收敛成 `ProviderError` →
                                                  loop 先问信号再判故障），且**一个非 `ProviderError` 都不许漏出去**
                                                  （漏出去 = 一次正常的网络故障在作者屏幕上是崩溃）。
                                                  `drafting.py` = **起草那一摊的实现**（2026-08-11 ADR 0021；
                                                  2026-08-12 ADR 0022 把它拆成三个动作：生成（花钱、不动书）/
                                                  落盘（不花钱、动书、**仍然不问作者**）/ 按 id 读回全文）。
                                                  生成调 `draft/product_draft.py` 那个唯一实现，**那一稿的
                                                  自述由写它的那个模型在同一次调用里交**（引擎不给散文打分，
                                                  ADR 0005 一个字没破）；落盘走 `importer.save_chapter`，
                                                  也就是 `PUT …/chapters/{n}/text` 走的同一个函数（磁盘先、
                                                  DB 跟）。**不弹框**，唯一的闸是
                                                  「拒绝覆盖作者比它更晚改过的那一章」（起草前记 `text_sha256`
                                                  **跟着候选一起存**，落盘时比对磁盘当前值——不存的话闸只能
                                                  拿「现在」跟「现在」比，也就是永远放行）；只对**已经存在的章**成立
                                                  （新建一章要起章标题，标题是切章的锚）；章标题原样保留，
                                                  切不成恰好一章、或者一稿是空的，都**写之前**拒
                                                  （空稿接上章标题照样切得出一章，形状闸拦不住它，
                                                  而它的后果是作者的一整章被一份空白盖掉）；
                                                  落盘前先 `sync` 一次，
                                                  让「退回上一版」对**作者从没同步过的那一版**也成立。
                                                  写入面（`GraphStore` + conn）握在这个闭包里，
                                                  **`ToolContext` 上一个字都没多**（边界一仍是类型保证）。
                                                  起草那一次调用的回执走 `DraftProduct.calls` → loop 的
                                                  `ledger`，所以成本闸罩得住它、日志页看得见它。
                                                  另一条新长出来的闸：起草那会儿这一章还不存在、落盘时它有了
                                                  （作者刚建的），**一律拒**——`expected_sha256=None`
                                                  会把闸整个关掉。resume 已经通到 HTTP
                                                  （执行态 = 一串 message + 缺 result 的那几个调用）。
eval/{leak,score}.py                            ← M2：泄漏集合判断 + 精确 McNemar / Holm + `decide()` 裁决表
eval/confound_lint.py                           ← M2：X1 vs X2 除 form 外不许有第二处差异（§2 反混淆铁律）
eval/runner.py                                  ← M2：三臂 × N 次 → 独占新建 `out_path` → `GateInput`
                                                  （`nh gate` 默认写 `runs/`；全仓唯一同时碰两侧的代码）
eval/evidence.py                                ← M2：JSONL 证据重建器（离线重算每个记录，key 拒入）
synth/                                          ← M2：合成小册子（12 章正文 + booklet.toml + build + selfcheck）；**不进 wheel**
                                                  （M3：m3_ground_truth.json 考卷 + m3_replay.py 量具，同不进 wheel）
```

**「全绿」这句话曾经比它听起来的弱，现在不了。** 此前 `knowledge_matrix` 与 R4 的全部断言只跑在
`FakeGraph` 上——那个 Fake 自己手写了一遍闭开区间的五个条件，于是生产 SQL（`knowledge_edges_at`、
KNOWS 压 BELIEVES、STALE 停火、跨项目隔离）一条都没被执行过。`tests/test_store_conformance.py` 把
场景降解成纯数据再参数化成两个后端，**同一份断言 24 条 × {fake, real} 各跑一遍**，另有 6 条
Fake 够不着的（label 校验 / 重复边 StoreError / `secret_ids` 默认列序）只打真库。Fake 从此漂不动。

`cli.py` 在此之前是**只有 `--version` 的空壳**；现在 `nh panel` 渲染的就是本文档
开头那个框，走的是真 SqliteStoryGraph。面板不再只在终端里存在——同一个矩阵在浏览器工作台的右栏
第一个 tab 里（`api/app.py` 的那批路由 + `frontend/src/components/KnowledgeMatrix.tsx`；
条数在「当前状态」，这儿不留第二份）。

### M2 的当前形状：修正案 5–9 / ADR 0011 已冻结，维护者裁定通过（非数据裁决）

**判分器先于被判者**是这条链的建造顺序，不是偷懒：先有卷子和判分口径，再有被判的东西，
「看到结果再定及格线」在结构上就做不到。原有短输出链在 2026-07-30 两侧齐了；2026-08-01
又在第一次真实推理前冻结了[修正案 5](EVAL_PROTOCOL_AMENDMENT_5.md)与
[ADR 0011](adr/0011-bilingual-draft-length.md)：中文 M2 改为 2,000–3,000 字，225 指 final cell，
每份长度不足最多续写一次，provider 继续走通用 OpenAI-compatible client 并按能力映射 high reasoning。
（2026-08-02 修正案 6：超长 ≤100 字宽容，硬上限 3,100。）
**endpoint/profile 已于 2026-08-02 冻结**（`deepseek-v4-flash` @ `https://api.deepseek.com`，
见 [M2_ENDPOINT_PROFILE.md](M2_ENDPOINT_PROFILE.md)，不含 key、单独 commit）。
length/capability/streaming/continuation/JSONL 实现与回归测试已于 2026-08-01 全部落地并离线验证
（1079 个 pytest / 33 个 vitest 全绿，见下）；2026-08-02 又补了两件事：DeepSeek 共享输出预留
按 0.95 审计入册（thinking 与正文共池、官方无占比；实测右尾单 cell 达 40,000 tokens），
`nh gate` 按 env 从注册表解析并冻结 plan（high reasoning、request 150,000、streaming）。

已落地并有测试：`eval/leak.py`（草稿泄漏 = 纯集合判断，禁忌集只经 `panel/constraints`）、
`eval/score.py`（`majority` / 精确 McNemar / `compare_arms` / Holm，零重依赖）、
**`eval/score.decide()`**（FLOOR/CEILING 门 + 符号稳定 + `MIN_DISCORDANT` 的完整预注册裁决表，
2026-07-27 补，`tests/test_eval_score.py` 40 条）、
`draft/provider.py`（`ProviderConfig` frozen，三臂与生产共用同一次调用）、
**`draft/context.py`**（`ResolvedConstraints`：把「cast 已解析」和「X1/X2 吃同一份矩阵」两条纪律
从「runner 记得调 `require_resolved_cast`」升级成类型层强制，2026-07-27 补，12 条）、
`panel/constraints.secret_surfaces`（秘密的**内容 tell**，排除进 prompt 的显示名标签）、
**第 4 道 arch-guard `tests/test_draft_boundary.py`**（2026-07-27 补，11 条）、
**501 stub 路由**（UI_ARCHITECTURE §48 要的那批，2026-07-27 补；`/draft` 已于
2026-08-02 按修正案 7 开放为真实起草接口，M4 提案审阅与被动确认已于 2026-08-03 开放，
`GET /runs` 已于 2026-08-10 点亮——它那条 501 的理由写着「`model_call` 表今天是空的」，
而那句在 M4 落地那天就过期了（抽取和滚动总结都在记账）。今天只剩规划那条是 501。
`/draft` 曾是 EVAL_PROTOCOL 修正案 1 给 KILL 分支定的动作前提，该前提现已满足且被
修正案 7 改为「实验开放，裁决 KILL 时按修正案撤销」）、
**`draft/assemble.py`**（三臂本体，2026-07-30 补，17 条：X0 是 X1/X2 的**严格前缀**由
`x1[:-1] == x0[:-1]` 加一条尾部拼接断言钉死，不是一句自觉；`DEFAULT_HOUSE_STYLE` 三臂共用，
里面**不许出现「秘密 / 不知道 / 泄露 / 剧透 / 伏笔 / 设定」**，另有一条关键词集合测试守着）、
**`eval/confound_lint.py`**（2026-07-30 补，25 条：X1/X2 的人名集合差 + 可见字符长度比
`max/min ≤ 1.15`；纯集合判断无分词器，空态一律判 `ok=False`）、
**`eval/runner.py` + `nh gate`**（2026-08-01 收口，40 条：一条陷阱只调**一次** `scene_view()`
喂两侧，`repeats ∈ {3,5}`，`config=None` 显式判死，完整 `messages` 原样进 `runs/*.jsonl`，
结果文件独占新建、已存在则在第一次模型调用前拒绝且原样保留）、
**`draft/length.py`**（双语长度域，2026-08-01 补：中文按非空白 code point / 英文按词计数，
`COUNTING_RULE_VERSION` 版本化，`M2_LENGTH_SPEC` 即修正案 5 冻的 2,000–3,000 档）、
**`draft/capabilities.py`**（2026-08-01 补：能力注册表按精确 `(base_url, model)` 路由，
provider-neutral `high` 映射到各兼容端点的 wire shape，未知路由 fail-closed；
可见预算公式 `ceil(max_units * 2.0) + 1024` 版本化，超 16,000 才走 streaming）、
**`draft/generate.py`**（2026-08-01 补：修正案 5 的续写一次——只读长度计数触发、
至多 2 次 attempt、两段按返回顺序原样拼接，第三次调用与样本替换都判死）、
**`eval/evidence.py`**（2026-08-01 补，`tests/test_run_evidence.py` 769 行：把 `runs/*.jsonl`
当证据不当缓存——每个计数/分数/裁决输入都离线重算验证，API key 拒入）、
**前端 `DraftLengthControls.tsx`**（2026-08-01 补，4 条 vitest：双语长度档，`ChapterPrepPage`
已接入）、
**[ADR 0010](adr/0010-writer-boundary.md)**（Writer 边界 D1–D6，**先于 `assemble.py` 定形**）、
**[EVAL_PROTOCOL 修正案 4](EVAL_PROTOCOL_AMENDMENT_4.md)**（先于 `synth/` 定形，见下）。

> ~~**但 5 条 stub 只做完了一半。**~~ —— **2026-08-02 两侧兑现，且起草已实验开放。**
> 「AI 规划」仍是灰置 stub（规划未开放）；「AI 起草」已按
> [修正案 7](EVAL_PROTOCOL_AMENDMENT_7.md) 点亮，`/draft` 换成真实实现
> （连接参数走 AI 设置页 BYOK，响应带「实验状态」标注）。M2 有效裁决后再决定
> 是否去掉实验标注；裁决若 KILL，按修正案 7 撤销（一次显式 commit）。

第 4 道守卫钉的是**起草层与判分层之间那堵墙的两面**，两面都成立 kill-gate 才有意义：
① `eval/` 不许自建禁忌集（EVAL_PROTOCOL §3 点名要的那条）；② **`draft/` 永不拿 tell**——
这一面才是主要理由：`assemble.py` 顺手调一次 `secret_surfaces()` 就会让 X1/X2 命中自己
写进 prompt 的词，Δ 翻负，裁决表读出**一个假的 KILL**，而全程没有任何东西会红。
预注册管的是「不能事后挪及格线」，管不了「仪器接反」；这道守卫管后者。
两侧规则**故意不对称**（`draft/` 能看矩阵不能看 tell，`eval/` 反之）——不对称正是
「检测器命中的 tell」与「prompt 里的标签」两集合天然不相交的机械保证。
实弹验过：往真目录种三个违规文件，三条守卫分别红并指到行，删掉回绿。

**合成小册子 `synth/` 也已落地**（2026-07-30）：`booklet.txt`（12 章 / 每章 851–1114 字）、
`booklet.toml`（6 人 / 5 个秘密共 **8 条知识边界** / 3 个未来实体 / **25 条陷阱 = 15 KNOWS + 10 FUTURE**，
分层按修正案 1）、`build.py`（TOML → 走**真实写入链**落库 + 派生 `ground_truth.json`）、
`leak_selfcheck.py`（§4 的放行条件 + 修正案 4 的裁定 B/C，全是精确比较，零语义判断；
还逐项核对 TOML 与 ground truth 的 kind/chapter/cast/target/goal/prior/reference，旧派生物不能混过关）。
**它不进 wheel**（顶层目录，`uv_build` 只打包 `src/novel_harness/`），派生物 `ground_truth.json`
和 `chapters/` 在 `.gitignore` 里——`build.py` 重跑得出来，commit 一份进来反而会让自检
去验一份和库无关的文件，而「派生对不对」正是它唯一在验的东西。

`tests/test_synth.py`（28 条）测的是**机制**，用它自带的 3 章小号 fixture，故意不碰真小册子；
`tests/test_synth_artifact.py`（5 条）测的是**那本书本身**——章数/边界数/陷阱分层对不对协议、
`tell_aliases == secrets`、自检全绿、没有一条陷阱派生出空禁忌集，
最后**把 25 条陷阱 × 3 臂 × 3 次整轮跑一遍**（真 runner、真 `assemble`、真判分，只把模型换成桩）。
最后这条一次覆盖三件别处验不到的事：每条陷阱都走得通 `scene_view()`、`assemble()` 在真数据上
渲染得出三臂、**`confound_ok` 是 True**。第三件尤其：它若为 False，§6 第 3 行摘掉 FORM-PIVOT，
**决策 A 照评、结论照出**，只是少一个维度，而读结果的人不会知道它被摘过。

**依赖顺序不是随便排的**（记在这儿是给以后重跑的人看）：ADR 0010 先于 `assemble.py` 定形——
第 4 道守卫自己承认它拦不住「完整 PLANNED 进 prompt」（那要语义判断，ADR 0005 禁止），
那条只有 review 和 ADR 0010 守得住。修正案 4 先于 `synth/booklet.toml`——`prior` 怎么造是它裁的。
`synth/` 是最贵的一块且**一半不是写代码**：`declare_knows(quote=...)` 靠引语在正文里唯一命中派生
`valid_from`，所以 12 章正文得先被人写出来、每条声明的引语在全书恰好命中一次。
runner 的落点有硬约束：`tests/test_draft_boundary.py` 写明它必须落进 `eval/`，
落到 `cli.py` 或顶层 `synth/` 就绕过整堵墙——它是**同时碰两侧的唯一一段代码**。
`out_path` 同样是硬边界：runner 用独占新建而不是 `exists()` 后再写，两个进程撞名也只有一个能创建；
旧 run 存在时早于 `scene_view()` 和第一次模型调用判死，原字节不变。这里没有覆盖、追加或断点续跑。

**真正还没有的是第一轮有效真模型结果**（smoke 已过；前四轮均死于长度 INVALID，
是仪器问题不是结果——无任何泄漏率数字）。基准轮仍是
25 × 3 × 3 = 225 个 final cell；每份不足 2,000 字最多续写一次，
所以成功完成需 225–450 次 transport call。第一轮有效结果之前，
ADR 0009 不写（协议 §8 定死它「跑完写」）、任何地方都不会出现泄漏率数字。
**`runs/` 故意不在 `.gitignore` 里**：预注册说「协议先于结果 commit」，
而那句话只有在结果**也** commit 了的时候才可验证。

协议冻在 [`EVAL_PROTOCOL.md`](EVAL_PROTOCOL.md)，**已于 2026-07-25 单独提交进 git（`0393088`,
`2026-07-25T16:19:00-04:00`）——预注册到此成立**。它第 11 行把「先 commit 的 git 时间戳」定义成
预注册成立的**唯一**证据，那条 commit 就是它；提交时 `runs/` 不存在、一次生成都没跑过，
所以之后任何 `runs/*.jsonl` 都晚于它、都算数。**这条 commit 之后再改协议就等于改卷子**——
真要改，开一份新的、说明改了什么和为什么，别覆盖。

**它此后已经被改过九次，改法合规但你必须知道它们存在**：
[`EVAL_PROTOCOL_AMENDMENT_1.md`](EVAL_PROTOCOL_AMENDMENT_1.md)（两处口径不自洽）、
[`_2`](EVAL_PROTOCOL_AMENDMENT_2.md) / [`_3`](EVAL_PROTOCOL_AMENDMENT_3.md)（裁决表重叠 + 三处措辞歧义）、
[`_4`](EVAL_PROTOCOL_AMENDMENT_4.md)（**§4 的「`prior` 不许含 tell」让整台仪器不通电**）、
[`_5`](EVAL_PROTOCOL_AMENDMENT_5.md)（中文 2,000–3,000 字、一次长度续写、225 final cells /
225–450 calls、通用 high-reasoning 能力档与 JSONL 证据形状）、
[`_6`](EVAL_PROTOCOL_AMENDMENT_6.md)（**超长 ≤100 宽容**：上限 3,000 → 3,100；
2026-08-02 在四轮 INVALID 之后裁定，§6 及格线一个数字没动）、
[`_7`](EVAL_PROTOCOL_AMENDMENT_7.md)（**起草先行开放（实验状态）**：
产品放行决定，考试 `PROTOCOL_VERSION` 与裁决表原样）、
[`_8`](EVAL_PROTOCOL_AMENDMENT_8.md)（**长度降权**：±10% 宽容带 1,800–3,410，
带内记录不判死、带外才 INVALID；七轮全死于长度之后裁定）、
[`_9`](EVAL_PROTOCOL_AMENDMENT_9.md)（**维护者裁定 M2 通过**：非数据裁决，
泄漏统计未产出、科学主张未证明，`nh gate` 保留可补跑）。
九份都是**另开文件**、冻结正文逐字节未动（从 `## 1.` 起与 `0393088` byte-exact）。
1–4 写下时 `synth/` 尚不存在；第 5 份晚于 `synth/`，但五份都早于真实推理与 `runs/`，所以仍属预注册。
**动 `eval/` 或 `draft/` 之前要读的是「协议 + 这五份修正案 + ADR 0010/0011」，不是协议一份。**

> 第 4 份值得单独说一句，因为它是**第一份往对本项目有利的方向裁的**（前三份里修正案 1 明确
> 选了对自己更不利的 15/10）。它发现的是：tell 是唯一生造的专名，而 X0 的 prompt 里只有
> house-style + `prior` + `goal`、零图谱事实——若 `prior` 一个字不许提 tell，
> **X0 物理上够不着那个字符串**，泄漏率恒 ≈ 0，撞 §6 第一行的地板 → 永远 INVALID。
> 「一个永远判 INVALID 的 kill-gate 不是严格，是坏了」。
> 裁定把禁令改读成「不许让**目标角色**已经知道它」（叙述层可以出现），
> 并把「`goal` 一律不许含 tell」升级成 `synth/leak_selfcheck.py` 的机器判据。
> **它没有碰 §6 的任何阈值**——[0.50, 0.90] 那两道门本来就是为「陷阱强度」设的，
> 而陷阱强度正是这条裁定动的旋钮，协议早就规定了它的合法区间。

### 四条真书验收，一条都没验（**堵点不是同一个**）

这一节曾经写成「三条真书验收，同一个原因：手上没有真书 TXT」——**数错了，也归错因了**。
实际是四条，且其中两条**同时还缺代码**，书到手也验不了。这个区分不是文字游戏：
把它们一并算作「缺一本书」，会让人以为拿到书就能一次性验完四条，而真相是有一条**拿到书会假绿**。

**纯缺书（测量代码齐全，书一到手就能验）：**

- `text/chapterize.py` 被 17 个测试钉住（脏数据：卷标题 / 番外 / 作者的话），但「真书切章数 = 目录数」
  **一次都没验过**——手上只有 `tests/fixtures/demo_novel.txt` 那份 46 行 **3 章**的手写 fixture
  （文件里有 5 个 `第…` 开头的标题，但「第一卷 / 第二卷」**不该**被切成章，`卷` 不在 `CHAPTER_RE` 里；
  那正是 `test_importer.py` 单独钉住的一条，别把它数进章数里）。
  真书那边还欠一个小口子：**目录数得人肉数**，仓库里没有 TOC 解析器（`NH_DEMO_CHAPTERS` 手填）。
- ~~R5 的生死数~~ —— **2026-08-02 已测，R5 已砍**：真书样本 3 章（`187817.txt`，
  红楼梦同人）引语 97 条、带显式标签 8 条 = **8.2% < 10%**。口径先由
  [ADR 0014](adr/0014-r5-cut-by-quote-coverage.md) 统一为「引语条数」，
  ADR 0005 的「实测结果」一节已填。M3 规则集 = R2/R3/R4。

**同时缺代码（书到手也验不了，别排进「等书」那一栏）：**

- **M1 的花名册 90% 提及** —— **度量代码与 gold 标注口径仍缺**。`text/mentions.py`
  已于 2026-08-02 落地（R2/R3/R5 的共同前置），但「90% 提及」的度量器、gold 口径
  仍然没有执行体。M1 已被标「已落地」，而这条验收从来没有过执行体。

**M3 的误报 < 1 条/章** —— **代码已就绪、合成门槛已过（2026-08-02）。**
R4 之外，R2（未来实体提前出现）和 R3（死人/未登场角色开口说话）已落地，各自带闭嘴条件测试。
门槛已预注册（[M3_GATE_PROTOCOL.md](M3_GATE_PROTOCOL.md) + `synth/m3_ground_truth.json`，
2026-08-02：25 道正题 25/25、干净对照 0 误报、干净正文 0 issue）。
~~还欠的只剩一条：真书 20 章人工误报判定~~ —— **2026-08-03 已过（维护者判定）**：
`111935.txt` 第 1–20 章 0 误报（`187817.txt` 佐证），性质如实标注「干净文本」；
合成 25/25 证明该响时响。**M3 双边门槛通过，M4 解锁。**

> ⚠️ **但 R2/R3 在作者手里今天是死的（2026-08-06 盘点）——这是 capability gap，不是 verdict gap。**
> 判定本身成立：`M3_GATE_PROTOCOL.md` §五逐字标了「两本书都是干净文本，**规则未开火**」，
> 两腿分工写得清楚，合成那腿还可复现（`uv run python -m synth.m3_replay`，边界数据运行期内存叠加）。
>
> 死的是**输入路径**：`props.first_appears_chapter`（R2 判据）和 `value_key`（R3 的 `is_dead` 判据）
> **全仓只被读、从来没有生产写入方**——CLI 的 declare 子命令、HTTP 的 `DeclareNodeBody`、
> 前端输入框，一处都没有（`declare_state` 见 `declare.py:32` 明写未做）。唯一写入者是
> `synth/build.py` 和 `synth/m3_replay.py`，**那是仪器不是产品**。
> 那次真书跑的「30 角色花名册 + 17 首现章进图」用的脚本**不在仓库里**（`scripts/` 无此物），
> 所以那次跑不可复现。
>
> **所以「真书一到手跑 `nh check` 就能量误报率」这句话（本段原文，已删）是错的**：
> 今天在真库上跑必然接近 0 issue，而那正是它自称摆脱了的「空表自动通过的假绿」。
> 要让 R2/R3 在产品里活过来，先补这两个字段的作者输入路径。
> 详见 [`docs_dev/2026-08-06-引擎能力差最后一厘米没接线.md`](../docs_dev/2026-08-06-引擎能力差最后一厘米没接线.md)。

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
   ~~**剩下半个洞**~~ —— **2026-08-02 已补**（[ADR 0012](adr/0012-book-owns-its-db.md)）：
   **库跟着书走，没有全局默认位置**。一本小说 = 一个文件夹，`book.db` 住在文件夹里；
   `novel-harness` 不带参数 = 从当前目录向上找最近的 `book.db`（找不到给人话）。
   查找只向上不向下，多本书物理隔离；不把绝对路径写进库（ADR 0007 的教训照旧适用）。
3. ~~**前端没有运行时测试**~~ —— **2026-07-25 已补，且补法是钉这条缝的两头。**
   前端 ↔ 后端契约是这个仓库**唯一真正的双份维护成本**（引擎 → CLI/HTTP 两个薄壳是加法，
   前端 ↔ 后端才是乘法），而它坏起来是无声的：`tsc` 看不见后端，而 `api/types.ts`
   自称「手写真相源」——手写的东西和后端一致只是**当时**一致。

   关键决定是 **fixture 不手写**：`tests/test_frontend_contract.py` 从真 app（`TestClient`
   + 真 SQLite）dump 一批端点的真响应（条数在「当前状态」），规范化掉 ULID/时间戳/路径后冻在
   `frontend/src/__fixtures__/api.json`；组件测试吃的就是这一份。于是两头各有守卫——
   **后端出参一改 pytest 先红**（逐字节比对重新 dump 的结果），**形状变了没人改组件 vitest 红**。
   用手写 fixture 做前端测试等于两份手写的东西互相验证，那正是这条缝原本的病。

   实测两侧都会红（把 `since_chapter` 改名一试：pytest 报「出参对不上」+ 指向
   `NH_UPDATE_FIXTURES=1` 和 git diff，vitest 报组件渲不出那一格）。
   规模：vitest 18 条（认知矩阵三态 / 左栏空态 / 花名册抽屉的 ADR 0004 提示与拒绝形态）。
   **剩下的**：只有 3 个组件有测试，`CenterEditor` / `LocalGraph` / `BottomBar` 等仍是零。

4. ~~**「AI 起草」在界面上拿不到记忆层**~~ —— **2026-08-10 已补。** 病史留着（三个洞叠加，
   每一个单看都像小事，合起来就是「点 AI 起草走不到 `build_product_context`」，模型只拿到
   「上文 800 字 + 在场名单 + goal + 一串禁令」）：

   | | 当时的事实 | 现在 |
   |---|---|---|
   | a | 事件记忆 + 人物档案 + 滚动总结**只在 `form=PRODUCT` 时装配** | 不变，这一条本来就是对的 |
   | b | **前端硬编码发 `form: "X1"`**，测试还钉住了 | `DraftDrawer.tsx` 改发 `PRODUCT`；那条 vitest 反过来钉住 `PRODUCT`，并把「X0/X1/X2 是考卷、产品不该发」写进注释 |
   | c | 滚动总结**没有任何 HTTP 端点**，唯一入口是 `nh summarize` CLI（会花钱） | `GET  /chapters/{n}/summaries`（窗口覆盖率）+ `POST /chapters/{n}/summary`（显式生成，幂等） |

   补法里有三件事值得记住：

   - **「保存章节后自动生成」是明确不做的**，不是漏了：那会替作者按下一次他没按过的付费调用。
     只做显式触发（同 M4 抽取那条：后台跑，但由作者点）。
   - **空总结不再静默**（§10 约束 8）。`SummaryStore.coverage()` 把窗口里每一章分成三态
     ——没写 / 写了没总结 / 有——起草抽屉在**花钱之前**就说「第 N 章还没生成总结，
     这不是那几章没内容」，并给一个当场补的按钮；`/draft` 的响应也多了一个 `memory` 回执
     （装了几份档案、几条事件、几段总结、哪几章缺）。**零永远带着一句理由**。
   - **顺手补掉一个从没被发现的 bug**：PRODUCT 分支拿 `view.matrix.characters` 直接喂
     `build_product_context`，而 `resolve_cast` 不看 label——作者在「在场角色」里写一个
     地点名（唯一解析成 `Location`），它会一路穿过 `require_resolved_cast()`，撞在
     「cast must contain only Character references」上，**由全局 `ValueError` handler 原样
     发给作者一句英文的 422**——引擎的内部话冒充作者的输入错误。
     （ADR 0018 的推导路径不产生这种输入，`mentioned_cast` 只收 Character；出问题的是
     起草抽屉里那个作者**手打**的在场框。）现在按 label 过滤，滤空则退化成无记忆起草，
     并在 `memory` 回执里说出来。

   **同一天又挖出第四个洞，比前三个都大**（2026-08-10，同批已补）：

   > **起草抽屉根本不发【上文】。** `draftNow()` 只发 `goal / cast / length / form /
   > house_style`——**没有 `previous_tail` 这个键**。后端 `_base()` 见空串就整块跳过，
   > 不报错、不警告，于是模型写第 23 章时**完全不知道第 22 章最后一段长什么样**。

   它让同一批的另一项改动（把逐字上文从 800 放长到上万字）**从浏览器一点效果都没有**——
   压根没东西可截。所以那条「文笔和情绪不连续」的抱怨，根因不是上文太短，是**上文不存在**。
   补法：`DraftDrawer` 拉上一章正文整篇发过去，截多长由后端按模型窗口算
   （`product_tail_limit`），**前端不留第二份长度常量**。
   两条 vitest 钉住（第 1 章送空串而不是拿别的章冒充），且实测撤掉那一行会红。

   原始盘点：[`docs_dev/2026-08-06-引擎能力差最后一厘米没接线.md`](../docs_dev/2026-08-06-引擎能力差最后一厘米没接线.md)。
   **那份诊断自带推翻条件**：接完线之后作者若仍觉得起草质量没变，病因就不在接线——
   那时别继续接线，回头重新找。
   （第四个洞是那份诊断的**加强证据**：同一种病——能力建完了，最后一厘米没接。）

5. **认知矩阵只被当刹车用，没有任何函数产出「此刻手上有哪些牌」**
   （2026-08-06 盘点发现，**未补**）。全仓 grep `tension` / `asymmetr` / `张力` / `不对称`，
   生产代码零命中。唯一在做认知集合差的是 `draft/product_context.py:94` 的
   `cast_ids <= knower_ids`——而它的用途是**把不对称的事件过滤掉**。
   **素材全在，归约一个都没写**（`must_not_reveal` 的补集、`since_chapter` 的减法、
   `EventView.revealed_facts` 与 `must_not_reveal` 的交集，全部是纯集合运算，不撞 ADR 0005）。
   形态与工具表见 [`docs_dev/2026-08-06-图谱形态与推进侧工具表.md`](../docs_dev/2026-08-06-图谱形态与推进侧工具表.md)。

6. ~~**作者在浏览器里看不见系统自动改了什么**~~ —— **2026-08-10 两半都补上了：
   读端（`activity.py` + `GET /activity` / `GET /activity/{id}` / `GET /runs`，
   后者由一条理由已过期的 501 点亮）+ 页面（顶栏「活动记录」→ 中栏
   `frontend/src/components/ActivityLog.tsx`：一行一条折叠、点开看跑了什么和结果、
   跳到对应模块）。** [ADR 0020](adr/0020-clean-extraction-auto-canon.md) 把
   「事前逐条确认」换成了「事后可查 + 可改」，而**「可查」在那一天是一句空话**：
   三张表（`extraction_run` / `model_call` / `decision_log`）一直在写真数据，
   一条读端都没有，浏览器里更是一个字都看不到。

   > ~~**但「可改」只兑现了三分之一**~~ —— **2026-08-11 接上了**：1.1 落的那两条改正路由
   > （`POST /canon/knowledge`、`POST /canon/events/{id}/cast`）此前**在浏览器里零调用方**，
   > 认知矩阵和事件名单都只能看，日志页因此在那一行明说「直接改动的入口还没做」。
   > 现在：矩阵里「知道 / 以为」那两种格子点得开（`KnowledgeMatrix.tsx` 的 `CellEditor`，
   > **「不知道」的格子仍然没有入口**——那儿没有可改的事实，新增认知走声明抽屉，
   > 一个功能不留两个入口）；右栏「待确认」那一格下半截是本章**已确认情节**的知情/在场
   > 名单（`CanonEventCast.tsx`，勾选框=绝对集合，只发作者动过的那一维）。
   > `CAN_EDIT_HERE` 里 `knowledge_cell` / `event_cast` 因此从 `false` 变成 `true`，
   > 那句「入口还没做」删掉了——**它当时是诚实的，留到今天就是骗人的文案**。
   > `chapter` 那一档仍是 `false`（下一条那种边真的改不掉），观测点没关。
   >
   > 顺带补掉一个**沉默的假值**：`KnowledgeMatrix.version.canon_version` 一直是模型默认的
   > 0（图层填不了它——canon 版本住在 `project` 行上，不在图表里），而改这一格要拿它当
   > `expected_canon_version`。照原样发就是每次必撞 409。现在由 `/matrix` 这条壳填上，
   > **版本跟着作者看到的那份数据走**；从别的读端另取一次是第二个会漂的源，
   > 中间有人升过 CANON 时 CAS 会放过一次它本该拦下的改动。

   两件补页面的人必须知道的事：

   - **`jump` 由后端给，前端不许从标题反推。** 每条日志带一个结构化坐标 +
     `endpoints`（今天真能改这个东西的路由）。`endpoints` 是空元组时**不许画编辑按钮**
     ——那不是没填。但**它今天有两个意思，别只读成一个**：
     ① 真的没有路由能改（自动升上去的边，见下一条）；
     ② 有好几条、后端不替作者挑是哪一条（一次升掉一整章的干净事件、一章里有 ≥2 条待审提案）。
     ② 那几行**改得掉**——`/canon/events/{id}/cast` 对它们一打就通（实测 200）。
     两种含义共用一个空元组是出参形状的事；在形状改掉之前，**② 的 `label` 里带着数目**
     （「改了 N 条事件，去第 M 章逐条改」），别让作者把它读成「没救了」。
     `tests/test_activity.py::test_a_row_that_changed_several_events_does_not_read_as_unfixable`
     两头都钉着：措辞里有数目 + 那几条真的 200。
     **页面这一侧照这条写的**：`ActivityLog.tsx` 的 `jumpNote()` 对空元组只说
     「从这里点不到具体的某一处」，**一个「改不了」都不说**——两种含义在出参形状上
     一模一样，而从 `label` 的措辞去分辨就正好是「从字符串反推」那条禁令。
     `ActivityLog.test.tsx` 有一条断言钉住那句话里不许出现「改不了 / 没救」。
     `jumpNote()` 现在两个条件一起看（引擎有那条路 **且** 工作台有那个控件）——
     少看一个，作者就会点到一个「跳过去发现改不了」的按钮。

     > **2026-08-11：这个 bucket 里当时躺着第三种东西，而它是纯粹的错。**
     > 作者自己声明的那条「知道 / 以为」（`KNOWS_DECLARE`）此前退到兜底坐标、
     > `endpoints` 空——可 `/canon/knowledge` 改的正是这一格上已经存在的那条边，
     > 不管它当初是声明进来的还是确认进来的。真 dump 里这两行并排摆着：
     > 「更正认知类型：萧决 对「血脉秘密」」带着路由，「声明认知：萧决 知道 血脉秘密」
     > 说没救——**同一格，两句相反的话**。而声明是作者往图里放认知的主路径
     > （[ADR 0004](adr/0004-declaration-over-extraction.md)），也是他最容易把
     > 「知道」和「以为」写反的地方。根因是 `decision_log` 那条 payload 里**只有人名
     > 没有 id**，`_decision_jump` 拼不出坐标（按人名反查就是「从字符串反推」，
     > 而「师兄」在一章里可能指 8 个人）。现在 `declare._log_edge` 把两端的 id 和名字
     > **一起**记（名字仍在，§5.7 那条没破），`LOCATED_DECLARE` 照旧留在空 bucket 里
     > ——`LOCATED_AT` 今天真的没有编辑入口，那才是这个 bucket 该装的东西。
     > 旧库里的行没有那两个键，照旧退到兜底坐标（`decision_log` 只增不改，重写不了）。
     > 两头钉在 `tests/test_canon_edit_loop.py`：声明那一行的坐标打过去必须 200，
     > 而「所有声明行都给认知格坐标」的假实现打过去必须被拒。
     >
     > **同一天补掉的第二个**：撞上 409 之后那颗「看看最新的」此前靠调用方传
     > `onRefresh?.()` —— 右栏传了、章节核对页没传，于是同一颗按钮在一块屏幕上管用、
     > 在另一块上只把编辑器收起来（`main.tsx` 写着 `refetchOnWindowFocus: false`，
     > 没有任何东西会替他补那一次重读，作者在核对页上会一直撞同一个 409）。
     > 现在重取归 `CellEditor` 自己管（`useRefreshPanels`），挂载点少传一个 prop
     > 不再能让这条退路死掉。
   - **自动升上去的「边」今天真的改不掉。** 抽取只产
     `LOCATED_AT` / `HAS_STATE` / `RELATED_TO`，而 `corrections.py` 只改
     KNOWS↔BELIEVES 和事件名单。这正是 ADR 0020 写在「什么条件下推翻本 ADR」里的
     第二条（「出现『作者改不回来』的形态」）——**日志页把它显式显示出来，
     那条推翻条件才第一次可观测**。编一个假按钮出来等于把观测点关掉。
   - **跳过去那一格得真的在表上。** 矩阵的行由本章正文推（[ADR 0018](adr/0018-cast-is-derived-not-declared.md)），
     而声明的生效章由引语定（[ADR 0006](adr/0006-evidence-double-pointer.md)）——一句满是代词的声明
     会把坐标指向一章「他一次都没被点名」的正文，那一行不在表上，高亮和编辑入口一起落空，
     而屏幕上是一张**看起来完全正常的表**。所以 `jump` 多给一个 `cast` 坐标
     （作者认得的称呼；**前端一个字都不解析**，拿屏幕上的人名自己去凑
     就是「从标题反推」）。它由 HTTP 壳填而不是引擎填：判断一个称呼能不能唯一指回这个人
     要读花名册，而 `activity.py` 的规矩是不读图。
     **称呼有歧义（或那个人一个不含歧义的称呼都没有）时它是空的**——那时绝不替作者挑
     （[ADR 0004](adr/0004-declaration-over-extraction.md)），退回原来的行为，
     矩阵那边照旧说「没有在这一章找到刚才那一格」。
   - **那个坐标走 `?include=`，不走 `?cast=`——方向搞反就是泄漏。** 这是系统**第一次**
     自己往面板的在场里塞东西（在此之前 `cast` 要么是作者标的场景块，要么是从正文推的），
     而三条读端吃的是同一份在场：`?cast=` 是**过滤**，拿它装一个只知道一个人的坐标，
     等于系统替作者把在场收窄成一个人，于是
     `must_not_reveal`（判据「在场的人里至少有一个还不知道」）**少一批禁令 = fail-open**
     ——正是 [ADR 0018](adr/0018-cast-is-derived-not-declared.md) §3 那条方向，
     也是 `panel/constraints.py` 里「李管家静默地从 cast 里消失」那个修过的真 bug。
     所以 `?include=` 的语义是**只加不减**（`api/app.py::_effective_cast`）：矩阵多一行、
     禁令只多不少。**推导为空那一档一律不加人**——「一个人都没数出来」= 不知道谁在场 = 全禁，
     加一个人进去会把全禁塌成「只按他一个人算」，那时跳转退回今天的行为（安全的那一侧）。
     `tests/test_jump_cast.py`（后端，带一个「把坐标做成过滤」的探针）+
     `frontend/src/components/JumpCast.coord.test.tsx`（浏览器里真点一次，扫的是请求 URL
     落在哪个参数上）两头钉着。
     `event_cast` 那一档**不给**这个坐标：它跳的是一份名单，不是矩阵的一行。

7. ~~**写作助手（模式二）在浏览器里还没有界面**~~ —— **2026-08-12 已画完**
   （3.5 `002f46b` 中栏对半分 + 3.6 起草落盘 + ADR 0024 两刀：事件流上屏、问题卡）。
   **标题这句在 3.5 落地之后还挂了一天**，留着病史：它正是本条自己说的那个病
   —— 界面画完了，而说「没画」的那句话没人回来划掉。

   ⚠️ **但同一个形状今天又长出一条新的**（这是本仓「最后一厘米没接」的**第五次**）：

   > **「现在生效的规矩」在浏览器里看不见、也取消不了。**
   > 引擎侧齐了（`rules.py`：存 / 数重复 / 按章号过期 / 撤销，往返落盘也钉住了），
   > **缺的只是一条路由和一块面板**。
   > **在画完之前，[ADR 0023](adr/0023-context-is-pruned-by-rebuildability.md)
   > 的退路只兑现了一半** —— 规矩记得下、过得了期，但作者看不见它、也点不掉它。

   这条洞和第 5 条不同——它不是「能力没写」，是「界面没画」。

   同批留下的三条，写在这儿免得下一个人当成 bug（**第一条 2026-08-11 已补，留着病史**）：

   - ~~**`draft_chapter` 仍然回一句「没接线」。**~~ —— **2026-08-11 接上了**（3.6 /
     [ADR 0021](adr/0021-agent-writes-drafts-without-asking.md)）。三件当时没有答案的事
     各自的答案：
     ① **提成函数**：`draft/product_draft.py::draft_chapter()`，`/draft` 那条路由和
     agent 的工具**调同一个**（`/draft` 出参逐字节未变）；
     ② **长度档**：`DEFAULT_LENGTH_POLICY` 的产品默认（中文 2,000 / 2,500 / 3,000，
     ADR 0011 D1 那张表），**不是** `M2_LENGTH_SPEC`（那是考卷），也**不给模型一个旋钮**
     ——长度是作者的意愿（ADR 0013），所以 `DraftAsk` 上仍然没有 `length`；
     reasoning 走 `OFF` 而不是 `/draft` 的 `HIGH`：没登记的端点只给 `OFF`，
     照 `HIGH` 发的话作者换个自建端点就是「聊天好好的，只有起草每次失败」；
     ③ **账**：`DraftFn` 现在返回 `DraftProduct`（2026-08-12 起是「候选摘要 + `calls`」，
     落盘那两个字段随 ADR 0022 搬去了 `LandingReport`），
     回执经 `ToolOutcome.calls` 交给 loop 的 `ledger`，**成本闸那一半也补上了**——
     `TurnLimits.max_calls_per_step` 只管次数（六个 `draft_chapter` 是六稿正文，
     次数上完全合法），现在每派发完一个查一次 `max_tokens`。
   - ~~**HTTP 这一版不流式**（内部流式，打断才能中途生效）。~~ —— **2026-08-12 已补**
     （[ADR 0024](adr/0024-a-turn-is-a-conversation-not-a-black-box.md) 第二刀）：
     多一条 `POST …/turn/events`（SSE），工作台走它；`POST …/turn` 原样留着，
     两条共用同一个 `_TurnRun`。当时担心的那个问题（「一条流式响应会把『这一轮的产物
     什么时候写进库』变成一个新问题」）**没有发生，因为落库那条线一个字都没动**：
     `persist` 照旧每长出一条落一次（3.4 起就是这样），事件流是**另一根线**，
     它一条消息都不写库。**选 SSE 不选 WebSocket 的理由见 `api/chat.py` 模块 docstring**
     ——一句话：把「停」搬到那条 socket 上，流断了作者就没有插手的地方了，
     而那一轮还在跑、还在花钱。
   - **打断的粒度取决于 plan 流不流式。**
     ⚠️ **2026-08-12 起「起草」那一档已经流式了**（`6b13abd`）：`plan_call(interruptible=…)`
     让 `stream` 除了「预算过 16k」之外多一个理由「这次要可中断且端点确认支持流式」，
     唯一设值点是 `ChapterDesk.write`，三臂 / `nh gate` 一条都不传。
     **下面这段说的是对话回复那一档，它今天仍然不流式。**
     `stream` 由 `plan_call` 按冻结阈值（16k）从输出预算推出来，一次对话回复远在阈值之下。
     适配器按流式写、按流式测（`tests/test_agent_model.py`），到了那一档真的生效；
     到不了的那一档降级成「这一次调用跑完就停」，`loop` 的每步检查仍然在。
     **不许为了让它流式去抬输出预算**——那样对能力表没登记的模型
     （`supports_streaming is None`）`plan_call` 会 fail-closed 直接拒，作者换个自建端点
     写作助手整个不能用。

   接线那一刀（3.6）自己又留下三条，同样写在这儿免得下一个人当成 bug：

   - ~~**按「停」中断不了一次正在跑的起草。**~~ —— **2026-08-12 已补。**
     修法**不是**抬输出预算（那条禁令原样有效），是给 `plan_call` 加一个**显式输入**
     `interruptible`（默认 `False`）：`stream` 从此为「预算够大 **或** 这次要可中断
     **且**端点确认支持流式」而开。三条同时成立——**M2 三臂 / `nh gate` 永不设它**
     （wire 逐字节不变，拿 `git archive HEAD` 的旧树同桩捕获 83 次真实
     `create(**kwargs)` 对拷验过，sha256 相同；把 `interruptible=True` 塞进探针
     本身时 30 条当场变了，所以那个相同不是空转）；**未登记的端点仍然不流式**
     （`supports_streaming is None` ⇒ 落回非流式，**不 fail-closed 拒**，
     作者接自建端点零回归）。
     信号走的是**既有那一条**（`complete(client=…)` + `_CancellableClient` +
     `generate_draft` 已有的透传），只补上「`cancel` 从 agent 那层送到
     `product_draft.draft_chapter`」这最后一段——**没有第二个取消机制**。
     半截那一稿收进候选表并标注（迁移 010 的 `stopped_reason`）：按停那一刻的 token
     是付过钱的信息，而 ADR 0022 之后一稿本来就只是「提议」，半截只是短一点的提议。
     **标注跟着每一个读端走**（列表 / 回执 / `read_draft` / 屏幕上那张卡）——
     模型不知道那是被砍断的，**会把那个断口当成一种有意的写法去模仿**。
     账走的仍然是 `ToolRefused.calls`（3.6 那条），拿不到 usage 时落进
     `_estimate_tokens` 那条已有的路。**「能停下来」是确定的，「省钱」不是**：
     断开连接 ≠ 停止生成 ≠ 停止计费，取决于供应商。

     **它换来的三条新欠账，写在这儿免得下一个人当成 bug：**
     ① **起草那一档的 token 数在三条路由上从此多半是「未记录」。** 流式下只有
     `supports_stream_usage is True` 的端点才会被要 usage（`provider.py` 只对那一档加
     `stream_options`），而注册表里今天只有 OpenAI 那四条是 `True`——DeepSeek 两条、
     Anthropic 兼容、OpenRouter 都会退成 `None`。方向是诚实的（不报就说不知道，
     绝不编一个）。**这一条曾经带着一条更坏的后果**：底栏那个 `COALESCE(…,0)` 会把它
     显示成「入 0 / 出 0 token」——**2026-08-12 已修**。`CostTotals` 的三个合计改成可空，
     并配一个 `metered_calls`（同 `cost`/`priced_calls` 那一对）：报了的那几次照样合计，
     **同时说出有几次没报**，一次都没报就说「用量未记录」。往哪个方向糊都是假话，
     两个数一起才说得清。要真拿到那些数字，仍然得先给那几条路由登记 stream usage 的证据。
     ② **`agent/model.py::_visible_text` 是第二处读 chunk 的地方**（第一处是
     `provider.py::_from_stream`）。信号一亮流就抛出去，运输层那个累加器连同它累到的字
     一起被丢掉——而那些字是付过钱的。两边认的字段哪天分家，
     `tests/test_draft_interrupt.py` 里那条同片对拷会红。
     ③ **过期的「停」只在客户端报得出「想停哪一轮」时才被忽略**（`TurnBody.run_id` /
     `StopBody.run_id`，两边任一为空就不比对）。工作台每一轮都报，所以那条竞态
     （按停 → 上一轮自己跑完 → 新一轮开始 → 停止请求到达 → 杀掉新的那一轮）
     在产品上是关着的；`curl` 那条路照旧是老行为。
   - ~~**起草那一行账在日志页上答不出「为哪一章」**~~ —— **2026-08-12 已补**
     （迁移 009：`model_call.chapter_number`）。当时那句「**两样都改出参形状**」是错的，
     记在这儿因为它差点让这条被推迟：加列**一个字节都没改出参**——日志页那一行原来
     就有「为哪一章」，只是恒为「未记录」；`/draft` 的响应体和它旁边那一批端点的
     契约夹具都逐字节不变（拿 `git archive HEAD` 的旧树同桩对拷验过）。
     **反查必须留着**：这一列出现之前的旧行全是 NULL（作者库里已有上百行），
     直接改成只读这一列会让它们的章号当场消失而且不报错。
     四个写入方里三个已经在填，**写作助手那一档还没接**——注意它不能照会话的章号填：
     起草工具花的钱属于 `DraftAsk.chapter`，和作者此刻停在第几章可以不是同一个数。
   - ~~**对话里从此有了第二份正文，而过期判据只认第一份**~~ —— **2026-08-12 缩掉了
     大半**（ADR 0022）：`draft_chapter` 的返回里不再有整章正文，只有 id + **定长**预览
     （`PREVIEW_UNITS`）+ 那一稿的自述，正文落在 `draft_candidate` 表里。
     **剩下的那一小半仍然在**，写清楚免得被当成已经解决：那 120 字预览同样会在作者改完
     之后变旧，而 `tools.outdated_manuscript` 同样认不出它（判据是 `ChapterFullText`
     验不验得过）。差别是代价：从「一整章过期正文每一轮重发」缩到「一段开头」。
     `read_draft` 的返回是**候选的全文**，它按定义不过期——候选是不可变的，
     它也不声称自己是磁盘上那一章。
     实测钉在 `tests/test_draft_landing.py::
     test_the_copy_of_the_manuscript_a_draft_leaves_in_the_conversation_is_not_checked`，
     **带一半对照**（同一段正文经 `chapter_text` 进来就会被擦掉）。
     **别顺手照抄那个判据**：一稿不带章标题，而 `handle_chapter_text` 出的是整章、
     还可能被截断，直接比一定恒不相等 ⇒ 每一稿刚写完就被自己擦掉。

**M4 正在实现、尚未完成**：`events/` 契约与 `002_m4_events.sql` 已落地；`extract/` 已有纯
结构化 schema、确定性 prompt、精确优先的模糊证据定位与不猜名称解析，后台 provider 调用和
提案入库仍未落地（设计草案见 [M4_DESIGN.md](M4_DESIGN.md)）。
`text/mentions.py` 已于 2026-08-02 落地，R2/R3 已在 `ALL_CHECKS` 里跑（R5 已砍）。
（这一行 2026-07-30 之前还挂着 `synth/` 和 `draft/assemble.py`，那天两样都落地了。
留个记号：这一行**只列代码**——「代码有了但没跑过」是另一回事，见上面 M2 那节最后一段。）

`scripts/demo.sh` 心跳已经在跑，绿的。但它量的是接缝，喂的是手写 fixture——**它不替代上面任何一条真书验收**。

真实进度以代码和测试为准，不以本文档为准——**如果两者不一致，改本文档**（[`PLAN.md` §5.5](PLAN.md) 已因此订正过三处）。
