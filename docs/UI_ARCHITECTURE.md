# Novel Harness UI 架构：写作工作台（落在精简引擎上）

> **这份文档回答一件事：怎么把「给不会写代码的小说作者用的可视化工作台」做出来，而不推翻 [ARCHITECTURE.md](ARCHITECTURE.md) 和 8 份 ADR。**
>
> 它把作者的 UI 设计文档（`~/Downloads/Novel Harness UI Design.docx`，写的是原始大架构：Neo4j + Qdrant + PostgreSQL + Harness Kernel + 全书抽取 + Validator + TipTap）**逐项落到现有 SQLite 引擎上**。凡是要靠 M2 起草 / M4 抽取 / 向量检索才有的，明确标「后续」，不画饼。
>
> 与 ARCHITECTURE.md 的关系：那份画的是**目标形态**（§4 就写了「浏览器面板 + FastAPI 壳还没写」）。这份是把那两层**具体设计出来**的第一版方案。冲突以 ARCHITECTURE.md 为准。

## 0. 为什么是这份，不是那份大架构

作者的核心诉求是对的：**CLI 不适合小说作者，他们不是程序员，必须有 GUI。** 这一点连 README 都承认（「用 WPS 不想碰命令行的作者，v1 还不适合你」）。

但 UI 文档描述的**后端**是本项目 ADR 逐条砍掉的东西。好消息是：**做那个产品界面，根本不需要那个后端。** 你要的三栏工作台、认知边界矩阵（头牌）、局部关系图、一致性检查、变更声明闭环——背后要的数据，现有引擎（`panel/` + `graph/` + `checks/` + `declare.py`）**已经全produce了**。缺的只是一层 FastAPI 壳 + React 前端，把它们从终端搬进浏览器。

**统计（对账 50 个 UI 元素）：**

| 状态 | 数量 | 含义 |
|---|---|---|
| 🟢 **现在能做** | ~35 | 现有引擎直接供数据，只差 HTTP 壳 + React |
| 🟡 **M2（起草）** | 7 | AI 规划/起草/生成/接受，`draft/` 未建，过 kill-gate 才验证留存 |
| 🟠 **M4（抽取）** | 4 | 变更确认页、自动提取变化，`extract/` 未建，`proposal_set` 表空 |
| 🔵 **v1.1（向量）** | 1 | Tab3 检索分数/语义检索，ADR 0002 触发条件制 |
| ⚫ **永久砍** | 2 | 事件因果图 + 「新增事件」，无 Event 节点、无 CAUSES 边（ADR 0005） |
| ⚪ **纯前端** | ~5 | 大纲/世界观/批注/置信度显示等，无引擎背书，降级为磁盘 markdown |

**三条必须现在就知道的诚实边界（否则会做出「编的」界面）：**

1. **Tab3 的「检索分数 / 语义相关度排序」在 v1 没有任何数据源。** 向量检索砍到 v1.1。v1 只能给**确定性证据**（声明产生的 Evidence 双指针 + F 分区「上一章」100% 召回）。UI 里出现任何 score 数字 = 编的。
2. **Tab5 的「置信度百分比」在 v1 不存在。** 一致性规则是确定性零 FP、二值（开火/沉默），`Issue` 没有置信度字段。UI 只能显示「确定性」，不能显示 %。数值置信度是 v2 LLM Validator。
3. **「事件因果图」和变更类型「新增事件」必须从设计里删掉。** 不是延后——是永久砍。没有 Event 节点、没有 CAUSES/RESULTS_IN 边，事件因果自动抽取永不做（关系抽取 F1 0.276，因果更低，且无任何规则依赖它）。

---

## 1. 后端：FastAPI 薄壳（不装业务）

原则（ARCHITECTURE §4）：**壳只把现有引擎函数暴露成 HTTP，不写业务。** 出参已经全是 Pydantic（`KnowledgeMatrix` / `StateSnapshot` / `Subgraph` / `SceneConstraints` / `Issue` / `Declaration`…），**直接当 API response schema**，用 `openapi-typescript` 生成前端类型，两端零手写 DTO。

### 1.1 装配与项目隔离

- **一个服务进程 = 一个已 migrate 的 SQLite 文件**（env `NH_DB`，启动时 `migrate()`）。项目是这个库里的**行**，`project_id` 是 URL 路径前缀 `/projects/{project_id}`。
- **一请求一连接**：FastAPI 依赖复刻 CLI 的 `_open_project`——开**一条** `sqlite3.Connection`（绝不两条：`Ledger` 和 `SqliteStoryGraph` 必须共享它，`transaction()` 才能把 `put_evidence` + `upsert_edge` 包在一个事务里），`SqliteStoryGraph(conn)`，再用 `project.get(conn, project_id) → None ⇒ 404`。
- **绝不裸 `connect` 请求传来的路径**：typo 的路径会生成一个空库，给出一张「看起来正常、全 UNKNOWN」的假矩阵。用 `_connect_existing` 语义（库不存在就 die）。`db.connect` 只在启动和 `POST /projects` 用。
- **跨项目隔离在引擎内**：每个 store 方法都收 `project_id` 并按 `node.project_id`（FK）过滤。壳必须把**路径里的** `project_id` 注入每次调用，**忽略请求体里的任何 project_id**。
- v1 单用户单机（ADR 0007），除 `project_id` 外不需要 auth/租户。

### 1.2 端点清单

> `status`：🟢=BACKED_NOW（现在能做）· 🟡=STUB_M2 · 🟠=STUB_M4。所有 🟡🟠 端点返回稳定的 `501 {status:'not_implemented', milestone}`，让前端**灰置**按钮而不是 404。

| Method | Path | 调哪个引擎函数 | 响应（Pydantic） | 状态 |
|---|---|---|---|---|
| POST | `/projects` | `project.create` | `Project` | 🟢 |
| GET | `/projects` | `project.list`（**需新增 1 行**，非图表不违反守卫） | `list[Project]` | 🟢 |
| GET | `/projects/{pid}` | `project.get` | `Project`（None→404） | 🟢 |
| POST | `/projects/{pid}/import` | `importer.import_book` | `ImportReport` | 🟢 |
| POST | `/projects/{pid}/sync` | `importer.sync` | `SyncReport` | 🟢 |
| GET | `/projects/{pid}/chapters` | 读 `chapter` 表 / `current_snapshots` | `list[ChapterText]` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/text` | **读磁盘** `{root}/chapters/{n:04d}.md` | raw markdown | 🟢 |
| PUT | `/projects/{pid}/chapters/{n}/text` | **写磁盘** → `importer.sync` | `SyncReport` | 🟢 |
| GET | `/projects/{pid}/roster?label=` | `store.resolve(surfaces=None)` | `list[Resolution]` | 🟢 |
| GET | `/projects/{pid}/resolve?surface=` | `store.resolve([surface])` | `list[Resolution]` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/matrix?cast=&scope=` | `resolve_cast` → `panel.knowledge_matrix` | `KnowledgeMatrix` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/constraints?cast=` | `panel.scene_constraints`（收原始称呼） | `SceneConstraints` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/state?cast=&scope=` | `resolve_cast` → `panel.cast_states` | `list[StateSnapshot]` | 🟢 |
| GET | `/projects/{pid}/characters/{node_id}/state?chapter=` | `panel.character_state` | `StateSnapshot` | 🟢 |
| GET | `/projects/{pid}/subgraph?center=&chapter=&hops=&edge_types=` | `store.subgraph`（hops≤2） | `Subgraph` | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/check` | `parse_scenes` → `run_checks` | `list[Issue]` | 🟢 |
| POST | `/projects/{pid}/locate` | `Ledger.locate` | `list[QuoteCandidate]` | 🟢 |
| POST | `/projects/{pid}/nodes` | `Ledger.declare_node` | `Node` | 🟢 |
| POST | `/projects/{pid}/aliases` | `Ledger.declare_alias` | `StoredAlias` | 🟢 |
| POST | `/projects/{pid}/declare/knows` | `Ledger.declare_knows` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/declare/believes` | `Ledger.declare_believes` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/declare/where` | `Ledger.declare_where` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/draft` | — | 501 | 🟡 |
| POST | `/projects/{pid}/chapters/{n}/plan` | — | 501 | 🟡 |
| GET | `/projects/{pid}/runs` | —（`model_call` 空） | 501 | 🟡 |
| GET | `/projects/{pid}/chapters/{n}/proposals` | —（`proposal_set` 空） | 501 | 🟠 |
| POST | `/projects/{pid}/proposals/{id}/accept` | `upsert_edge` 就绪但无提案生产者 | 501 | 🟠 |

**关键陷阱（壳写错就退化成 fail-open）：**

- **`/matrix` 必须走 `panel.knowledge_matrix` 不是 `store.knowledge_matrix`**——只有 panel 包装器挂 `unresolved_cast`。直调 store 会**静默丢掉「作者声明了但解析不出的人」那一行**，正是本项目要防的漏洞。
- **原始称呼 vs node_id 是故意不一致的**：`scene_constraints` + 全部 `/declare/*` 收**作者原始称呼**（内部自解析，调用方没机会弄丢人）；`knowledge_matrix` / `character_state` / `cast_states` 收**已解析的 node_id**。`/matrix` 和 `/state` 先调 `resolve_cast` 喂 `.ids` + `.unresolved`；`/constraints` 把 `?cast=` 原样透传。
- **出参收窄防泄密**：`resolve`（roster）和 `subgraph` 出**完整 `Node`**，而 `NodeProps` 是 `extra="allow"`——一个 Secret/未来节点会把作者写的 `props.twist`/`plot_note` 序列化出去。壳必须把任何 `label=Secret` 或 `first_appears>chapter` 的节点**收窄成 `NodeRef.of(node)`** 再 JSON（矩阵/约束/forbidden 已经是 `NodeRef`，安全）。
- **PROVISIONAL 灰显是独立第二次调用**（`scope=PROVISIONAL`），永不混进 CANON 响应（§5.4：PROVISIONAL 永不断言为真）。`PLANNED`/`REJECTED` 被 `require_queryable_scope` 拒 → 422。

### 1.3 错误映射

复刻 CLI 的 `_die_refused` 语义，一张表所有路由共用：

| 引擎异常 | HTTP | 响应体要点 |
|---|---|---|
| `project.get` None | 404 | `project_not_found` |
| `UnknownName` | 404 | `unknown_name`, surface |
| `AmbiguousName` | 409 | `ambiguous_name` + `candidates: list[NodeRef]`（消歧下拉，**服务端绝不替作者选**） |
| `AmbiguousQuote` | 409 | `ambiguous_quote` + `candidates: list[QuoteCandidate]`（让作者加长引语到唯一） |
| `SupersedeConflict` | 409 | `supersede_conflict`（乱序 valid_from，v1 拒绝不猜） |
| `WrongLabel` | 422 | got/want NodeLabel |
| `QuoteNotFound` | 422 | `quote_not_found`（提示：精确匹配、先 sync、复制别手打） |
| `ImportRefused` | 409 | `conflicts: list[str]`（内容不同的已存在文件，无 --force） |
| `SyncRefused` | 422 | `path`（某 NNNN.md 切出 0 或 >1 章） |
| pydantic `ValidationError` | 422 | 剥掉开发者 wrapper，留作者可读的一半（CLI 的 `_reason`） |
| `ValueError`（scope/hops>2/chapter<1/空名） | 422 | `bad_request` |
| `UnresolvedCast` | **读端不报错** | 矩阵/约束/state 返 200，装进 `unresolved_cast` 出「请作者消歧」横幅；只有 M2 起草路径 `require_resolved_cast()` 开火时才 409 |

### 1.4 正文在磁盘（ADR 0007）+ 锚桥（ADR 0006）

- **磁盘是正文的真相，DB 只是派生索引。** 应用内编辑器读写**磁盘** `chapters/NNNN.md`，不读 DB 快照。
  - 读：`{root}/chapters/{n:04d}.md`（`utf-8-sig`）→ raw markdown。**不**返 `current_snapshots`（那是给证据锚用的冻结快照，不是可编辑正文）。
  - 存（保存键）：先写磁盘 → 再 `importer.sync` 落快照。磁盘先、DB 跟（正是 CLI 回路：改 md → `nh sync`）。`import_book` 已拒绝覆盖内容不同的文件（无 --force），ADR 0007 的实质没破。
- **定位桥只有一个：`TextAnchor` 三元组 `(para_index, quote_text, occurrence_k)`，全链路禁 offset（ADR 0006）。** 编辑器靠「在第 para_index 段内重寻 quote_text 数到第 k 次」定位高亮，render 时现算、天然抗文本漂移。
- **`check` 端点的切段和 `locate` 都走 `text.paragraphs()`**（今天 == `splitlines()`，但是全库唯一定义）——路由里**不许**自己写 `.splitlines()`，否则 `text/anchor.py` 一改，`Issue` 锚和 `QuoteCandidate` 锚就「差一段」。

---

## 2. 前端：React 写作工作台（桌面优先）

### 2.1 三栏工作台（P1，桌面默认页）

```
┌────────────────────────────────────────────────────────────────────────┐
│ 项目名 │ 第N章 │ 保存 │ 一致性检查 │ [AI规划·灰] [AI起草·灰]           │  顶栏
├──────────────┬──────────────────────────────┬──────────────────────────┤
│ 左栏 240px   │ 中栏 flex                     │ 右栏 380px               │
│ 导航         │ 应用内 markdown 编辑器         │ 智能面板 5 Tab           │
│              │ (读写 chapters/NNNN.md)        │                          │
│ · 章目录     │                               │ Tab1 当前状态            │
│ · 花名册     │ 章标题                        │ Tab2 局部关系图 (ReactFlow)│
│   (按 label) │ 场景块 ##场景N + nh:cast/goal │ Tab3 确定性证据 (无score) │
│ · 大纲/世界观│ 正文 …………                     │ Tab4 约束 + 认知矩阵(头牌) │
│   (磁盘 md)  │ [选中文字→查图谱/声明]         │ Tab5 一致性问题 (确定性)  │
│ · 最近运行   │ [Issue 按 anchor 高亮]        │                          │
│   (M2·隐藏)  │ [DeclareDrawer 手动声明]      │                          │
├──────────────┴──────────────────────────────┴──────────────────────────┤
│ 场景时间线 (场景序 + 边闭开区间)  │  运行遥测 (M2·折叠)                  │  底栏
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 组件树

```
<App>
├─ <QueryClientProvider>          TanStack Query（服务端状态唯一缓存）
├─ <GlobalStore Zustand>          只放坐标：projectId / chapter / sceneCast[] /
│                                 selectedNodeId / textAnchor / scope / activeTab
│
├─ <WorkbenchPage>   ◀── P1 三栏工作台
│  ├─ <TopBar>
│  │  ├─ <ProjectTitle>           ◀ GET /projects/{pid}
│  │  ├─ <VolumeChapterCrumb>     ◀ GET /chapters（卷=heading 推断，只读）
│  │  ├─ <SaveButton>             ▶ PUT /chapters/{n}/text → importer.sync
│  │  ├─ <AIPlanBtn disabled>     ◀ M2 stub
│  │  ├─ <AIDraftBtn disabled>    ◀ M2 stub
│  │  └─ <RunChecksButton>        ▶ POST /chapters/{n}/check
│  ├─ <LeftRail>
│  │  ├─ <ChapterTree>            ◀ GET /chapters（无卷分组）
│  │  ├─ <RosterSection label=…>  ◀ GET /roster?label=（resolve 过滤）
│  │  ├─ <DocLinks 大纲/世界观>    ◀ 磁盘 md（无图谱背书）
│  │  └─ <RecentRuns hidden>      ◀ M2 model_call（v1 空）
│  ├─ <CenterEditor>
│  │  ├─ <ChapterHeading>         ◀ GET /chapters/{n}/text
│  │  ├─ <SceneBlockBar>          ◀ parse_scenes；cast 多选 ▶ PUT /scenes 回写注释
│  │  ├─ <MarkdownEditor CM6>     ◀▶ GET/PUT /chapters/{n}/text（磁盘 md 是真相）
│  │  │   ├─ selection → deriveAnchor(para_index, quote_text, occurrence_k) ※禁 offset
│  │  │   ├─ <SelectionActionMenu> ▶ POST /resolve · declare knows|where|believes · 段级 check
│  │  │   └─ <IssueHighlightLayer> ◀ Issue.anchor → 段内重寻 quote 计数 k 高亮
│  │  ├─ <DeclareDrawer>          ▶ POST /declare/*（替代 M4 抽取审阅页）
│  │  │   └─ <QuoteLocatePreview>  ▶ POST /locate（选中句=quote，测唯一命中）
│  │  └─ <SnapshotDiffView>       ◀ chapter_snapshot 去重对比（降级，非版本史）
│  ├─ <RightPanel Tabs>
│  │  ├─ Tab1 <StateCards>        ◀ GET /state（地点/状态维/生死/出边）
│  │  ├─ Tab2 <LocalGraph ReactFlow> ◀ GET /subgraph(center=selectedNodeId, hops≤2)
│  │  ├─ Tab3 <DeterministicEvidence> ◀ Evidence 双指针 + state_at(N-1)（无 score）
│  │  ├─ Tab4 <ConstraintsBox>    ◀ GET /constraints（must_not_reveal / forbidden）
│  │  │       <KnowledgeMatrix>   ◀ GET /matrix（cast×secret 头牌）
│  │  └─ Tab5 <IssueList>         ◀ POST /check（Issue+anchor+evidence；显示「确定性」）
│  └─ <BottomBar>
│     ├─ <SceneTimeline>          ◀ parse_scenes 序 + edge.valid_from/valid_to
│     └─ <RunTelemetry collapsed> ◀ M2 model_call（v1 空）
│
├─ <ChapterPrepPage>  ◀── P2 章节准备（写第 N 章前的确定性简报）
│  ├─ <ChapterGoalCard>           ◀ 作者手填 brief（磁盘 md，无图谱背书）
│  ├─ <MainCastPicker>            ◀ GET /roster + /resolve
│  ├─ <CurrentStatePanel>         ◀ GET /state + /matrix
│  ├─ <PrevChapterState>          ◀ GET /state?chapter=N-1（F 分区，100% 召回）
│  ├─ <ForbiddenFuture>           ◀ forbidden_entities
│  └─ <SceneSkeletonBoard DnD>    ◀▶ parse_scenes / PUT /scenes（AI 骨架=M2 stub）
│
└─ <GraphExplorerPage>  ◀── P3 全屏图谱（中心漫游，真全图=v2）
   ├─ <GraphModeSwitch>           1 关系 / 3 伏笔 / 4 地点 / 5 认知(头牌) / 6 时间状态
   │                             （2 事件因果 = 永久砍，UI 里没有这一项）
   ├─ <GraphFilters>             ◀ subgraph(edge_types,chapter)：章/人物/类型/只看当前
   ├─ <TimeAxisSlider>           ◀ 不同 chapter 调 state_at（ch100 vs ch151 收敛点）
   ├─ <GraphCanvas Cytoscape>    ◀ GET /subgraph（点节点展开≤2 跳；3 跳硬禁 ValueError）
   └─ <KnowledgeMatrixMode>      ◀ GET /matrix（模式5 全屏）
```

### 2.3 状态管理：坐标进 Zustand，数据进 react-query

**铁律：能从 API 拉的绝不进全局 store。**

- **全局 store（Zustand）只放坐标**：`projectId`（换项目=整棵 query 树失效）、`chapter`、`sceneCast: string[]`（作者原始称呼，不是 node_id，从选中场景块的 `nh:` 注释解析）、`selectedNodeId`、`textAnchor`（编辑器选区派生，禁 offset）、`scope`、`activeTab`。
- **服务端状态（TanStack Query）装一切可拉数据**：`queryKey = [端点, projectId, chapter, cast/nodeId, scope]`，坐标一变自动重取。全是 Pydantic 出参，`openapi-typescript` 生成 TS 类型，零手写 DTO。
- **为什么这样分**：`KnowledgeMatrix`/`StateSnapshot` 是对给定坐标的确定性投影，react-query 的 staleness/refetch 免费搞定失效，且天然支持 ADR 0007 的两条实时路径：**面板**（场景元数据变→invalidate matrix/constraints，本机 2–5ms 瞬时）+ **规则**（正文变→debounce 2s→invalidate `/check`）。写路径（`declare_*`/`sync`）成功后精确 invalidate 受影响 key（如 `declare_where` 改了地点→失效该章 state/matrix/subgraph）。
- **唯一的本地可变状态**：CM6 编辑器 doc 自持，保存时才 PUT + sync，脏态用 `isDirty` 标记不进 react-query。

### 2.4 编辑器：CodeMirror 6，不是 TipTap

选 **CodeMirror 6**（`@codemirror/lang-markdown`）作应用内编辑器。

- **怎么守 ADR 0007**：编辑器只是磁盘 `chapters/NNNN.md` 的便利视图，不是新真相源。打开=读盘，保存=写回同一个 md 再 `importer.sync`。磁盘文件始终可被 VSCode/Obsidian 平行编辑（外部改动经 file-watch → sync 回流）。「文件是作者的」没破，GUI 是可选便利不是锁定。
- **为什么不 TipTap**：ADR 0007 把 TipTap 砍到 v1.1，核心不是「前端库不许用」（TipTap 只是前端库，不违反后端精简），而是它买来 ADR 0006 的坐标错位——ProseMirror 用持久化 pos，必须维护 `pos ↔ (para_index,quote,k)` 映射层，那正是 offset 地狱、正是 v1.1 才做的那层。**CM6 停在纯文本/markdown 心智**：段落=空行分隔的文本块，锚靠重寻 quote 定位，不需要任何持久化 position 映射。
- **取舍说明**：内置编辑器降低了非程序员门槛（换取采纳），代价是要自己扛 revalidate——但 CM6 + 磁盘回写让 revalidate 从第一天就被真实编辑行为压测（这是 ADR 0007 的「副作用全是好的」）。这是本方案**唯一松动 ADR 0007「v1 不做编辑器」时机**的地方，且是作者选定的产品方向（非程序员 GUI）的直接推论——松的是时机，守住的是实质（正文在磁盘）。TipTap 的触发条件明确：真有作者要求内置富文本时，图层/规则/面板全不动，只加 TipTap + 一层 pos↔锚映射（v1.1）。

### 2.5 图库：局部 React Flow / 全屏 Cytoscape

- **Tab2 局部子图 → React Flow（`@xyflow/react`）**：`subgraph` 硬上限 hops≤2、`truncated>30`，实际是十几个节点的小图。React Flow 是 DOM/SVG + React 原生，每个节点直接渲染成一张 React 富卡片（角色名 + label chip + 状态徽标 + 生死），边标签放 `valid_from`/是否公开。和编辑器/react-query 同一套 React 心智，选中文字→resolve→setSelectedNodeId→重渲染中心，联动零成本。
- **P3 全屏 → Cytoscape.js + fcose 布局**：全屏虽然 v1 仍是**中心漫游的 bounded 子图**（真 1–3 跳全图 = v2，实测 3 跳 4720 节点数学上坏），但要多中心累积、连续展开、force 布局、章范围过滤、时间轴重算——图库主场。canvas 渲染，几百节点仍流畅。规模逼近 WebGL 阈值可平滑换 Sigma.js，不动数据契约。两库共享同一份 `Subgraph` DTO 和 `GET /subgraph`，只是渲染层不同。

### 2.6 编辑器 ↔ 图谱联动（三方向，桥只有锚三元组）

- **方向一 · 选中文字查人物**：CM6 划选 → 算 `textAnchor(para_index, quote_text, occurrence_k)` → 取 quote 作 surface → `POST /resolve` → `Resolution`。歧义（「师兄」命中 8 人）弹候选选择器；唯一 → `setSelectedNodeId` → 派生重取 Tab1 `/state` + Tab2 `/subgraph`。相关原文降级为确定性证据，不做语义检索。
- **方向二 · 锚回跳高亮**：`Issue.anchor` / `Evidence.anchor()` 给同一三元组 → 编辑器定位到第 para_index 段 → 段内 `indexOf` 搜 quote、跳过前 k 次 → setSelection 滚动高亮。因为按 quote 重寻而非存 offset，作者别处改字导致段号漂移时用 quote 重搜自愈——这正是 ADR 0006 双指针的前端兑现。
- **方向三 · 选区声明（核心闭环，零章号）**：划选一句原文 → SelectionActionMenu「声明 X 知道秘密 / X 在某地」→ 选中文本直接作 `declare_knows/where/believes` 的 quote → 系统 locate 该 quote 落在第几章 → **自动写 `valid_from`，作者从不填章号（约束 10）**。`DeclareDrawer` 里先 `POST /locate` 试命中：0=不可用、>1=让作者加长引语到唯一。`Declaration` 回执展示「valid_from=chN（系统算的，你没输）」+ 被自动闭合/撤回的旧边（**产品招牌动作**）。

### 2.7 技术栈

React 18 + TS（桌面优先）· Vite · **TanStack Query**（服务端状态唯一缓存）· **Zustand**（坐标 store）· **CodeMirror 6**（守 ADR 0007）· **React Flow**（局部子图）· **Cytoscape.js + fcose**（全屏漫游）· Tailwind + Radix/shadcn · react-resizable-panels · TanStack Virtual · **openapi-typescript**（从 Pydantic 出参生成 TS 类型）· **FastAPI + uvicorn 薄壳**。

**明确不引入**：向量/embedding/RAG（ADR 0002）· TipTap/ProseMirror（v1.1）· Neo4j/图数据库客户端（ADR 0001）· 任何 LLM 起草/抽取 SDK（M2/M4 才接）。

---

## 3. 逐项对账矩阵

> 完整 50 行对账见本仓库 workflow 输出；下表按页汇总最需要注意的判定。

### 页面一 · 工作台

| UI 元素 | 背书 | 判定 |
|---|---|---|
| 项目名 / 当前章 / 保存 | `Project` / `chapter` 表 / `sync` | 🟢（卷非节点类型，只能 heading 推断，不做卷导航） |
| 一致性检查 | `run_checks` / R4 | 🟢（v1 仅 R4，印「跑了几条规则」避免静默零） |
| AI 规划 / AI 起草 | — | 🟡 M2（灰置 stub） |
| 左栏 人物/地点/势力/物品/伏笔 | `resolve` 按 label 过滤 | 🟢 读端就绪；⚠️ 势力/物品/伏笔 **M1 无 declare 写路径**，v1 初期空 |
| 左栏 大纲 / 世界观 | — | ⚪ 降级为磁盘 markdown，无图谱背书 |
| 中栏 正文 / 场景块 | 磁盘 md + `parse_scenes` | 🟢 |
| 中栏 AI 生成 / 局部重写 / 接受拒绝 | — | 🟡 M2 |
| 中栏 选中文字查图谱 / 选段跑检查 | `resolve` + `subgraph` + `run_checks` | 🟢 |
| 中栏 版本对比 | `chapter_snapshot`（去重非全量） | 🟢 降级为作者版 diff |
| Tab1 当前状态 | `character_state` / `cast_states` | 🟢 地点/已知秘密/目标有；⚠️ 修为/身体/持有/关系阶段读端就绪但**无 declare**（HAS_STATE=M3、RELATED_TO=M5），情绪未建模 |
| Tab2 局部关系图 | `subgraph`(hops≤2) | 🟢 |
| Tab3 证据（确定性） | `Evidence` 双指针 | 🟢 |
| **Tab3 检索分数 / 语义检索** | — | 🔵 **v1.1（无数据源，出 score = 编的）** |
| Tab4 不能提前泄露 / 未来实体 | `scene_constraints`（must_not_reveal / forbidden） | 🟢 |
| Tab4 必须/可以发生·文风·字数 | — | ⚪ 作者手填 brief，引擎不背书 |
| Tab5 问题/位置/事实来源/建议 | `Issue` + `TextAnchor` + evidence | 🟢 精确到段 |
| **Tab5 置信度** | — | ⚪ **v1 无此字段，只显示「确定性」不显示 %** |
| 底栏 Harness 10 步 / Token / 成本 | `model_call`（空） | 🟡 M2（v1 是单次调用不是 10 步 Kernel） |

### 页面二 · 章节准备

| UI 元素 | 背书 | 判定 |
|---|---|---|
| 主要人物 / 当前状态 / 上一章结束状态 | `resolve` / `state_at` / `state_at(N-1)` | 🟢（F 分区 100% 召回） |
| 禁止提前出现的未来内容 | `forbidden_entities` | 🟢 |
| 本章目标 / 必须承接（自由文本） | — | ⚪ 作者 brief，无 store |
| AI 推荐场景骨架 | — | 🟡 M2；v1 替代=作者手拖手填场景块 |

### 页面三 · 全屏图谱 & 联动 & 变更页

| UI 元素 | 背书 | 判定 |
|---|---|---|
| 模式1 人物关系 / 模式4 地点 / 模式5 认知（头牌） / 模式6 时间状态 | `subgraph` + `matrix` + `state_at` | 🟢 |
| 模式3 伏笔图 | `Foreshadow`/`PLANTED_IN` schema 就绪 | 🟢 需补 declare foreshadow；PLANNED 未来侧不进读路径 |
| **模式2 事件因果图** | — | ⚫ **永久砍，从 UI 删除** |
| 全屏 1–3 跳 Explorer | `subgraph` | v2（3 跳数学上坏）；v1 只交付 hops≤2 局部漫游 |
| 联动 AI 生成后自动提取变化 | — | 🟠 M4（`extract/` 未建）；v1 替代=手动 declare |
| **变更确认页（整页）** | `proposal_set`（空） | 🟠 **M4**；v1 用「作者手动 declare」替代整套「系统抽→作者审」心智 |
| 变更类型「新增事件」 | — | ⚫ 永久砍 |

---

## 4. v1 分期计划（4 个阶段，每阶段可交付）

> 只放 🟢 现在能做的东西。真书门槛（花名册 90% 提及、切章数=目录数）另行用真实 TXT 验，不在此编数。

### P0 · FastAPI 薄壳 + 只读工作台骨架 + 项目/章节导航
**交付**：作者能新建/打开项目、导入 TXT 看切章结果、在三栏骨架里浏览章目录与花名册、看到**认知矩阵头牌**与四个只读面板，全部由真实引擎数据渲染。**全部只读，不改一个字。**
- [BE] `novel_harness/api/` FastAPI app 工厂（启动 migrate 门、`_connect_existing` 语义、`project.get` 404 门）
- [BE] 统一错误映射层（§1.3 那张表）
- [BE] `POST /projects` · `POST /import` · `GET /chapters` · `GET /roster?label=`
- [BE] `GET /matrix`（**必经 `resolve_cast`**）· `GET /constraints` · `GET /state`
- [BE] 出参收窄契约：可能含 Secret/未来节点的响应一律出 `NodeRef`
- [FE] Vite + React 桌面三栏骨架 + 5 Tab 壳
- [FE] 认知矩阵渲染：`✓知道(chN)` / `⚠错误认知` / `✗不知道` + `unresolved_cast` 消歧横幅
- [FE] AI 规划/起草渲染成灰置 stub；最近运行隐藏
- [FE] `openapi-typescript` 从 Pydantic 出参生成前端类型

### P1 · 应用内 Markdown 编辑器 + 磁盘同步 + 场景块
**交付**：作者在应用内改正文、保存即落快照（文件仍在磁盘），编辑/拖动场景块并回写，拉出「当前 md vs 历史快照」作者版对比。**守住 ADR 0007。**
- [BE] `PUT /chapters/{n}/text`（写磁盘 → `sync`；`SyncRefused`→422 带 path）
- [BE] `GET /chapters/{n}/history` + diff（标注「快照按内容去重、非全量版本史」）
- [BE] 场景块解析/回写走 `text.scenes`
- [FE] CM6 编辑器（非富文本）读写磁盘 md + 显式/自动保存
- [FE] 场景块 UI（拖拽排序/增删/改目标 → 回写 md）
- [FE] `TextAnchor` 桥脚手架：段落级定位一律三元组，代码里彻底不留 offset

### P2 · declare 写入闭环 + 名称消歧 + 局部关系图 + R4 内联
**交付**：作者敲原始称呼 + 引语就能写图谱（系统算章号、给收据、自动关闭旧地点边）；名字歧义有选择器；选中正文能查节点/局部图；对本章跑 R4 并在正文精确高亮冲突。**这是 v1 核心闭环。**
- [BE] 每请求构造 `Ledger`（唯一同时持 store+conn，一条共享连接）
- [BE] `POST /nodes`（全 8 label）· `/aliases` · `/declare/{knows,believes,where}`（入参=称呼+引语，永不 node_id、永不章号）
- [BE] 返回 `Declaration` 收据（`valid_from` 标注「系统算的」+ closed/retracted 边）
- [BE] `POST /locate` · `GET /resolve` · `POST /check`（仅 R4）· `GET /subgraph`（hops>2→422）
- [FE] declare 表单：**坚决无章号输入框**；「测这条引语」调 locate；提交后显示「valid_from=chN（你没填）」+ 自动关闭的边
- [FE] 消歧选择器（复用于 cast 输入/declare 目标/名字搜索）
- [FE] Tab2 局部图（点节点展开≤2 跳；`RELATED_TO` 用 `peer_of` 不用 `e.dst`）
- [FE] 联动 1/2/4：选区→图谱、点节点看详情、冲突→高亮句+定位节点+「改正文 or 改图谱」

### P3 · 章节准备页 + 确定性证据/时间线/联动打磨
**交付**：作者进章前有一张全由确定性读端拼出的准备页；右栏证据与约束、底栏场景时间线补齐；大纲/世界观以磁盘 markdown 降级交付。**v1 收尾。**
- [BE] 准备页读端聚合：`state_at(N-1)` + `forbidden_entities` + `scene_constraints` + 当前章 matrix + CANON 侧已埋伏笔
- [BE] Tab3 确定性证据（`Evidence.anchor()` → 来源章/原文片段/是否 Canon/图谱边；**明确不给 score**）
- [BE] 底栏场景时间线（`parse_scenes` 序 + 边闭开区间；无全局事件线——事件未建模）
- [FE] P2 章节准备页（场景骨架=作者手拖手填，替代未实现的 AI 骨架）
- [FE] Tab4 约束页（must_not_reveal + forbidden 直读；作者手填字段清楚标注「引擎不背书」）
- [BE/FE] 扩展 `demo.sh` 心跳覆盖「导入→读面板→declare→再读矩阵变化→R4」端到端，作为 v1 交付验收线

---

## 5. 与 ADR 的关系

| ADR | 这份方案怎么对待 |
|---|---|
| 0001 无 Neo4j | ✅ 守住。图谱全走 SQLite `subgraph`，前端只是渲染层 |
| 0002 无向量 | ✅ 守住。Tab3 只给确定性证据；检索分数砍到 v1.1 |
| 0003 ULID | ✅ 守住。node_id 全程 ULID，前端不解析语义 |
| 0004 声明优于抽取 | ✅ 守住。变更页 = 作者手动 declare，不做「系统抽→作者审」直到 M4 |
| 0005 只做集合判断 | ✅ 守住。Tab5 只有确定性 R4；事件因果图永久删除 |
| 0006 禁 offset | ✅ 守住。编辑器↔图谱桥只有 `(para_index, quote, k)` |
| **0007 正文在磁盘 / v1 不做编辑器** | ⚠️ **实质守住，时机松动**：应用内 CM6 编辑器进 v1（作者选定的非程序员 GUI 方向的直接推论），但只读写磁盘 markdown，DB 永不是正文真相源。TipTap 富文本仍 v1.1 |
| 0008 RELATED_TO 无向 | ✅ 守住。前端取对端用 `peer_of` 不用 `e.dst` |

**唯一需要作者确认的松动**：ADR 0007 原写「v1 不做编辑器」。你选的方向（非程序员能用的 GUI）要求应用内编辑器——本方案让它进 v1，但用 CM6 over 磁盘 markdown 守住 ADR 0007 的实质。如果你想更保守（前端只读、作者仍用自己的编辑器），P1 可以只做只读渲染+file-watch，把编辑器推后。
