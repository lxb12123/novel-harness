# Novel Harness UI 架构：写作工作台（落在精简引擎上）

> **这份文档回答一件事：怎么把「给不会写代码的小说作者用的可视化工作台」做出来，而不推翻 [ARCHITECTURE.md](ARCHITECTURE.md) 和现有 ADR。**
>
> 它把作者的 UI 设计文档（`~/Downloads/Novel Harness UI Design.docx`，写的是原始大架构：Neo4j + Qdrant + PostgreSQL + Harness Kernel + 全书抽取 + Validator + TipTap）**逐项落到现有 SQLite 引擎上**。凡是要靠 M2 起草 / M4 抽取 / 向量检索才有的，明确标「后续」，不画饼。
>
> 与 ARCHITECTURE.md 的关系：那份同时记录**目标形态与当前状态**，本文件是 UI 两层的具体方案。
> 实现推进后这里可能落后；冲突一律以 ARCHITECTURE.md 的「当前状态」为准。

## 0. 为什么是这份，不是那份大架构

作者的核心诉求是对的：**CLI 不适合小说作者，他们不是程序员，必须有 GUI。** 这一点连 README 都承认（「用 WPS 不想碰命令行的作者，v1 还不适合你」）。

但 UI 文档描述的**后端**是本项目 ADR 逐条砍掉的东西。好消息是：**做那个产品界面，根本不需要那个后端。** 你要的三栏工作台、认知边界矩阵（头牌）、局部关系图、一致性检查、变更声明闭环——背后要的数据，现有引擎（`panel/` + `graph/` + `checks/` + `declare.py`）**已经全produce了**。缺的只是一层 FastAPI 壳 + React 前端，把它们从终端搬进浏览器。

**统计（对账 50 个 UI 元素）：**

| 状态 | 数量 | 含义 |
|---|---|---|
| 🟢 **现在能做** | ~35 | 现有引擎直接供数据，只差 HTTP 壳 + React |
| 🟡 **M2（起草）** | 7 | 后端 `/draft` 已按 **修正案 7 实验开放**（2026-08-02）。**但「AI 起草」那个抽屉 2026-08-10 删了**：填表式起草（先填「这一场要写什么」+ 在场角色再点按钮）与 ADR 0018「在场是写出来的结果」冲突，且一个功能不留两个入口——起草归模式二的 agent 面板，在那儿它是一次**工具调用**不是一个界面。`/draft` 端点一个字没动。AI 规划仍 501/灰置 |
| 🟢 **M4（抽取）** | 4 | 事件记忆切片已落地（2026-08-03）：显式后台抽取 → 提案/被动确认 → 作者审阅 → 安全事件上下文；`extract/` / `proposal_set` / 右栏「待确认」tab 均已实现。真书三章接受度验收待跑 |
| 🔵 **v1.1（向量）** | 1 | Tab3 检索分数/语义检索，ADR 0002 触发条件制 |
| ⚫ **永久砍** | 2 | 事件因果图 + 「新增事件」，无 Event 节点、无 CAUSES 边（ADR 0005） |
| ⚪ **纯前端** | ~5 | 大纲/世界观/批注/置信度显示等，无引擎背书，降级为磁盘 markdown |

**三条必须现在就知道的诚实边界（否则会做出「编的」界面）：**

1. **Tab3 的「检索分数 / 语义相关度排序」在 v1 没有任何数据源。** 向量检索砍到 v1.1。v1 只能给**确定性证据**（声明产生的 Evidence 双指针 + F 分区「上一章」100% 召回）。UI 里出现任何 score 数字 = 编的。
2. **Tab5 的「置信度百分比」在 v1 不存在。** 一致性规则是确定性零 FP、二值（开火/沉默），`Issue` 没有置信度字段。UI 只能显示「确定性」，不能显示 %。数值置信度是 v2 LLM Validator。
3. **「事件因果图」和变更类型「新增事件」必须从设计里删掉。** 不是延后——是永久砍。没有 Event 节点、没有 CAUSES/RESULTS_IN 边，事件因果自动抽取永不做（关系抽取 F1 0.276，因果更低，且无任何规则依赖它）。

---

## 1. 后端：FastAPI 薄壳（不装业务）

原则（ARCHITECTURE §4）：**壳只把现有引擎函数暴露成 HTTP，不写业务。** 出参大半是 Pydantic（`KnowledgeMatrix` / `StateSnapshot` / `Subgraph` / `SceneConstraints` / `Issue` / `Declaration`…），**直接当 API response schema**。

⚠️ **「用 `openapi-typescript` 生成、两端零手写 DTO」是原计划，今天没兑现，而且方向是反的**：收窄端点（`resolve` / `subgraph` / `state` / `nodes`）为了 `_narrow` 出 dict、签名是 `-> Any`，openapi 里没有 response schema，生成出来是 `unknown`。生成物 `frontend/src/api/schema.ts` 落后于壳（漏掉 `evidence` / `history` / `scenes` / 5 条 stub 等一批路径）、**全仓零 import**，而且它在 `frontend/.gitignore` 里——是个没人用的本地生成物。真正被前端消费的是**手写**的 `frontend/src/api/types.ts`（文件头自陈了原因，`frontend/README.md` 也记了）。要兑现得先给收窄端点补 `response_model`。

### 1.1 装配与项目隔离

- **一个服务进程 = 一个已 migrate 的 SQLite 文件**（env `NH_DB`，启动时 `migrate()`）。项目是这个库里的**行**，`project_id` 是 URL 路径前缀 `/projects/{project_id}`。
- **一请求一连接**：FastAPI 依赖复刻 CLI 的 `_open_project`——开**一条** `sqlite3.Connection`（绝不两条：`Ledger` 和 `SqliteStoryGraph` 必须共享它，`transaction()` 才能把 `put_evidence` + `upsert_edge` 包在一个事务里），`SqliteStoryGraph(conn)`，再用 `project.get(conn, project_id) → None ⇒ 404`。
- **绝不裸 `connect` 请求传来的路径**：typo 的路径会生成一个空库，给出一张「看起来正常、全 UNKNOWN」的假矩阵。用 `_connect_existing` 语义（库不存在就 die）。`db.connect` 只在启动和 `POST /projects` 用。
- **跨项目隔离在引擎内**：每个 store 方法都收 `project_id` 并按 `node.project_id`（FK）过滤。壳必须把**路径里的** `project_id` 注入每次调用，**忽略请求体里的任何 project_id**。
- v1 单用户单机（ADR 0007），除 `project_id` 外不需要 auth/租户。

### 1.2 端点清单

> `status`：🟢=BACKED_NOW（现在能做）· 🟡=STUB_M2。M4 的抽取/审阅端点已点亮；余下 🟡 端点返回稳定的 `501 {status:'not_implemented', milestone}`，让前端**灰置**按钮而不是 404。
>
> 读这张表的两个前提：**Path 一律省了 `/api` 前缀**（真实路径是 `/api/projects/…`），表里也不列 `GET /`（SPA 入口，不是 API）。**剩下的 🟡 stub 今天是真实存在的端点**（`api/app.py`）——后端那一半兑现了，前端灰按钮那一半还没做（§2.2 末的现状标注）。份数是会漂的量，唯一副本在 [`ARCHITECTURE.md` 的「当前状态」](ARCHITECTURE.md#当前状态)。

| Method | Path | 调哪个引擎函数 | 响应（Pydantic 或收窄后的 dict） | 状态 |
|---|---|---|---|---|
| POST | `/projects` | `project.create` | `Project` | 🟢 |
| GET | `/projects` | `project.list_all`（非图表不违反守卫） | `list[Project]` | 🟢 |
| GET | `/projects/{pid}` | `project.get` | `Project`（None→404） | 🟢 |
| POST | `/projects/{pid}/import` | `importer.import_book` | `ImportReport` | 🟢 |
| POST | `/projects/{pid}/sync` | `importer.sync` | `SyncReport` | 🟢 |
| GET | `/projects/{pid}/chapters` | **扫磁盘** `{root}/chapters/*.md` | `list[{number,title}]`（title=首个非空行） | 🟢 |
| DELETE | `/projects/{pid}/chapters/{n}` | `importer.remove_chapter` → `store.delete_chapter` | `{deleted, number}`·**引擎在这一章上记过东西 → 409 `chapter_in_use` 带五个计数**（`edge.src/dst→node` 和 `evidence.chapter_id→chapter` 都是 CASCADE，不拦就是**无声**删掉那些记忆）·正文不是删掉是挪进 `{root}/deleted/`·**章号不重排**（它是全书 `valid_from` 的锚） | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/text` | **读磁盘** `{root}/chapters/{n:04d}.md` | `{number, markdown}` | 🟢 |
| PUT | `/projects/{pid}/chapters/{n}/text` | **写磁盘** → `importer.sync` | `SyncReport` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/history` | `store.chapter_snapshots` | `list[ChapterSnapshot]`（带 text 供前端 diff；**内容去重、非全量版本史**） | 🟢 |
| DELETE | `/projects/{pid}/chapters/{n}/snapshots/{sid}` | `store.delete_chapter_snapshot` | `{deleted, snapshot_id}`·**当前那条 409 / 被证据引着 409**（三条外键都没有 CASCADE，快照是审计锚） | 🟢 |
| — | **还原到某一版没有自己的路由** | 走上面那条 `PUT .../text` | 快照按内容去重 → 写回旧正文正好命中已有那条 → `is_current` 移回去、不新增一版 | 🟢 |
| ~~GET/PUT~~ | ~~`/projects/{pid}/chapters/{n}/scenes`~~ | — | **2026-08-14 删了**，连同场景块、R4 和中栏那条场景条（[ADR 0027](adr/0027-scene-blocks-cut.md)）：那套标记语法要作者手写，真书覆盖率 0% | ⚫ |
| GET | `/projects/{pid}/roster` | `store.resolve(surfaces=None)` | `list[{id,label,name}]`（**不整体序列化 `Node.props`**）·⚠️设计里的 `?label=` **后端没实现**：出全项目，按 label 分组是前端 `LeftRail` 做的 | 🟢 |
| GET | `/projects/{pid}/resolve?surface=` | `store.resolve([surface])` | `{surface, ambiguous, unique_id, hits[]}`（hits 一律收窄成 `NodeRef`） | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/mentioned` | `mentioned.mentioned_cast`（读磁盘正文） | `{chapter, has_text, surfaces[]}`·**`has_text=false`（章还没写）和 `surfaces=[]`（写了但没提到人）是两件事，别合并显示** | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/matrix?cast=&include=` | `resolve_cast` → `panel.knowledge_matrix` | `KnowledgeMatrix`·⚠️设计里还有 `&scope=`，**后端没实现**（见末条陷阱）·**`version.canon_version` 由这条壳填**（2026-08-11 起）：图层填不了它（canon 版本住在 `project` 行上，不在图表里），此前它一直是模型默认的 0，而改这一格要拿它当 `expected_canon_version` —— 照原样发是每次必撞 409 | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/constraints?cast=&include=` | `panel.scene_constraints`（收原始称呼） | `SceneConstraints` | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/state?cast=&include=` | `resolve_cast` → `panel.cast_states` | `list[StateSnapshot]`·⚠️设计里还有 `&scope=`，**后端没实现**（见末条陷阱） | 🟢 |
| GET | `/projects/{pid}/characters/{node_id}/state?chapter=` | `panel.character_state` | `StateSnapshot` | 🟢 |
| GET | `/projects/{pid}/subgraph?center=&chapter=&hops=&edge_types=` | `store.subgraph`（hops≤2） | `Subgraph` | 🟢 |
| GET | `/projects/{pid}/evidence/{evidence_id}` | `store.get_evidence` | `{id, chapter_number, quote_text, anchor}`（扁平；**不给 score**，v1 没有向量） | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/check` | `run_checks` | `{chapter, rules_run, issues}`——**不是裸 `list[Issue]`**：静默的零和真的零不许长得一样，前端据 `rules_run` 把「跑了没意见」和「哪条没跑」分开。⚠️ **`scene_count` 2026-08-14 从出参里去掉了**（ADR 0027：它是 R4 缺席的成色说明，而 R4 不在了） | 🟢 |
| POST | `/projects/{pid}/locate` | `Ledger.locate` | `list[QuoteCandidate]` | 🟢 |
| POST | `/projects/{pid}/nodes` | `Ledger.declare_node` | `Node` | 🟢 |
| POST | `/projects/{pid}/aliases` | `Ledger.declare_alias` | `StoredAlias` | 🟢 |
| POST | `/projects/{pid}/declare/knows` | `Ledger.declare_knows` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/declare/believes` | `Ledger.declare_believes` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/declare/where` | `Ledger.declare_where` | `Declaration` | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/draft` | `scene_view` → `assemble`（X0/X1/X2）或 `build_product_context` → `assemble_product`（PRODUCT，默认） | `{experimental, note, text, memory, length, …}`·**这一行 2026-08-02 起就不是 501 了**（修正案 7 实验开放）；`memory` 是记忆层回执，**零带着理由**（装了几份档案/事件/总结、哪几章缺总结）；`previous_tail` 的截断长度**分档**——PRODUCT 从模型窗口倒推（`product_tail_limit()`，2026-08-10 起），点名 X0/X1/X2 则原样拿冻结的 800（那是考卷，见 ADR 0019 边界五） | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/summaries` | `SummaryStore.coverage`（窗口边界由 `rolling_summary_window` 算） | `{chapter, window_first, window_last, chapters[], summarized, missing[]}`·**窗口不是全书**（近八章走事件记忆）；`missing` = 有正文没总结（**撤回过的也算缺**），`has_text=false` = 还没写，两者别合并 | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/summary` | `RollingSummarizer.ensure` | `{chapter_number, has_text, summary, created_at, retracted, author_written}`·**会调模型、会花钱**，幂等（同章同 prompt 只付一次）；**故意没有「保存后自动生成」**，自动那条走下面的 `autopilot`；**撤回过的章按这里会真的重来一次（再付一次钱）**，那是撤回语义里写死的退路 | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/summary` | `SummaryStore.coverage`（单章） | `{chapter_number, has_text, summary, created_at, retracted, author_written}`·这一章现在的总结。**没有的时候不许只回一个 null**：`has_text=false`（还没写）/ `retracted`（作者亲手撤的）/ 两者都不是（有正文没生成过）三种零分得开，因为下一步动作完全不同 | 🟢 |
| PATCH | `/projects/{pid}/chapters/{n}/summary` | `save_author_summary` | `{chapter_number, has_text, summary, created_at, retracted, author_written}`·换成作者自己写的那一段，**不花钱**。库里追加一行（迁移 013），模型写的那一行留着；交上来的就是屏幕上那一段时一行都不追加。空串 422（清空≠撤回，两个动作不共用入口）、超过 1000 字 422 | 🟢 |
| DELETE | `/projects/{pid}/chapters/{n}/summary` | `retract_summary` | `{chapter_number, has_text, summary, created_at, retracted, author_written}`·撤回，**不花钱、库里一行都不少**。语义定死为「这一章当作没总结」——起草不带它、覆盖率算作缺、想重来就再点生成。本来就没有 / 已经撤过都回 200（这个动作没有失败的形态） | 🟢 |
| POST | `/projects/{pid}/focus` | `focus.report_focus` | `{chapter, project_id}`·**免费心跳，只记「作者现在在哪一章」**（2026-08-18 §3）：换章/开书时上报，不触发任何总结/抽取/付费。防抖靠它 + 心跳超时（2 分钟）判「正写的章不碰」 | 🟢 |
| GET | `/projects/{pid}/summary-status` | `summary_schedule.book_summary_status` + `focus.resolve_draft_origin` | `{draft_chapter, focused_chapter, chapters[{chapter_number, has_text, state, weight, anomaly}]}`·**全书总结状态视图**（2026-08-18 §6 / Step 4）：逐章三态 + 异常标记 + 这一轮自治权重，全部查库、**GET 只读不写**。`state ∈ {empty, paired, missing, stale}`；`anomaly` = 最近一次总结 attempt 终态失败（不阻塞别的章，由 30 分钟自治轮重试） | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/summary/mentions` | `summary_index.mentions_in_chapter` | `{chapter, mentions[{node:{id,label,name}, surfaces[]}]}`·这一段总结提到了花名册里的哪些东西。**只做集合判断**（这个称呼出现了没有，`text/mentions.py` 那条 alternation + `rules_only`），不做任何相似度——找相似要向量 + 语义，那是被砍掉的 Qdrant 和 ADR 0005。出参**只有 `NodeRef`**：这批命中里按定义就有 Secret。**不并进 `…/summary`**（那个形状是四条动作共用的，也是 `…/summaries` 里的一行，每章挂芯片 = 一次覆盖率查询变成一次全书反查） | 🟢 |
| GET | `/projects/{pid}/nodes/{node_id}/summary-mentions` | `summary_index.chapters_mentioning` | `{node, chapters[{chapter_number, summary, surfaces[], author_written}]}`·**还有哪几章的总结提到它**，按章号升序，带那几段原文（作者点开是为了读它、比它、引它）。一次 SQL，**不调模型、不花钱**。`node_id` 不在本项目 → 404（`NodeNotFound`），**不回空表**：「他没在任何总结里出现过」和「这个 id 根本不存在」下一步动作完全不同。索引什么时候重建见 `summary_index.py` | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/autopilot` | `RollingSummarizer.ensure` + `runner.enqueue`（都进 `BackgroundTasks`） | 202 `{chapter, summary, extraction, extraction_run_id, errors[]}`·两个状态字取值 `queued`/`skipped`/`no_text`/`running`/`failed`/`retracted`/`unconfigured`·**作者撤回过的章一律不派**（判据是 `SummaryStore.latest()` 不是 `get()`：后者对撤回过的章回 None，于是他撤掉、切走一章，后台立刻替他买一份回来） | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/autopilot` | `SummaryStore.latest` + `extract.metrics.metrics_for_range` | `{chapter, summary_ready, extraction_ready, running, summary_state, extraction_state, errors[]}`·**只读，不排队不花钱** | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/plan` | — | 501 | 🟡 |
| GET | `/projects/{pid}/activity?actor=&limit=&cursor=` | `activity.read_activity`（`extraction_run` + `model_call` + `decision_log` 归并） | `{entries[], next_cursor, actors[]}`·**折叠层**：一行 = `{id, source, ts, actor, status, title, subtitle, chapter_number, jump}`，**payload 不在这一层**（那是泄漏面，按需取）。`actors[]` 的计数**不受 `actor` 过滤影响**——它要回答的正是「我筛掉了多少」（[ADR 0020](adr/0020-clean-extraction-auto-canon.md)） | 🟢 |
| GET | `/projects/{pid}/activity/{entry_id}` | `activity.read_entry`（按 id 前缀分派到三张表） | `{entry, rows[], cost, errors[], payload}`·**展开层**：`rows[]` 是「标签→值」的定义列表（措辞归后端，前端不写文案分支）；`payload` 只有 `source=decision` 才有，且过 `narrow_payload`（Node 形状收窄 + `props` 一律丢掉，比 `_narrow` 严——日志行没有「当前章」可比）。查无此条/跨项目 → 404 | 🟢 |
| GET | `/projects/{pid}/runs` | `activity.read_runs` | `{entries[], run_count, totals}`·**这一行 2026-08-10 起不是 501 了**：它当年的理由「`model_call` 表今天是空的」在 M4 落地那天就过期了（抽取和滚动总结都在记账）。`totals.cost` 恒为 `null` 而不是 `0.0`——`model_call.cost` 至今没有写入方，`priced_calls` 把这个零的理由一起发出去 | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/proposals` | `proposals.pending` | `list[ProposalRecord]` | 🟢 |
| POST | `/projects/{pid}/proposals/{id}/accept` | `review_proposal`（accept） | `ProposalResolution` | 🟢 |
| POST | `/projects/{pid}/proposals/{id}/reject` | `review_proposal`（reject/bystander） | `ProposalResolution` | 🟢 |
| POST | `/projects/{pid}/proposals/{id}/edit` | `review_proposal`（edit） | `ProposalResolution`·**按作者改过的样子落进 CANON**：`edited_summary` / `knower_ids` / `participant_ids` 至少给一样（后两个是**绝对集合**，`null`=这一维不动）。此前 `edit` 只存在于库里、没有路由，于是浏览器里只有 accept / reject 两个按钮·**前端调用方**（2026-08-13 起）：右栏「待确认」那一格里低置信情节卡上的「改一改」→ `ProposalReviewTab.tsx::ProposalEditor`，勾选框和「已确认的情节」那一格**共用同一份控件**（`CastPicker.tsx`）——两条路能力不一致的时候，作者会学会先驳回再重来，而那正好丢掉了证据链。后端只对「恰好 1 个 event、无 edge、无新人物」开放（`extract/proposal_validation.py`），条件不成立**不画那颗按钮** | 🟢 |
| POST | `/projects/{pid}/canon/knowledge` | `corrections.correct_knowledge` | `KnowledgeCorrection`·**改一条已生效的事实**：`{character_id, secret_id, to_type: KNOWS\|BELIEVES, believed_value?, expected_canon_version}`。机制是**撤回旧边 + 写新边**（旧行留着，`status=RETRACTED`），`valid_from` 从旧边继承——**入参里没有章号**（约束 10）。404=这一格今天是 UNKNOWN / 422=已经是那个类型或 `believed_value` 形状不对 / 409=`stale_base_version`·**前端调用方**（2026-08-11 起）：右栏「人物认知」那一格 → `KnowledgeMatrix.tsx` 的 `CellEditor`，版本取自同一张矩阵的 `version.canon_version`。**同一个编辑器挂在两处**（右栏 + 章节核对页），所以撞 409 之后的重取归编辑器自己管（`useRefreshPanels`），不靠挂载点传 `onRefresh` —— 少传一个可选 prop 就让退路死在一块屏幕上，那是已经发生过一次的形态 | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/canon/knowledge` | `corrections.add_knowledge` | `KnowledgeAddition`·**在认知矩阵一格空白上补一条**（2026-08-14，作者规则「每一格 LLM 无感生成、他能改**能增**」的后一半）：`{character_id, secret_id, type: KNOWS\|BELIEVES, believed_value?, expected_canon_version}`。**章号在路径上、请求体里一个都没有**——矩阵本来就是 AS OF 第 N 章渲染的，作者点的那一格就在他正看的那一章上，`valid_from` 就是那个 N（他一个数都没敲；全前端照旧零个 `<input type="number">`）。**这条边没有引语 ⇒ 没有证据**（`evidence_id IS NULL` + `evidence_status='NONE'`，绝不伪造）。409=`fact_already_exists`（这一格已经有事实了 —— **不悄悄兼做「改」**，判据是「现在有没有」不是「这一章看得见没有」）/ 409=`stale_base_version` / 422=形状不对（和「改」共用 `corrections._knowledge_shape` 一份措辞）·**前端调用方**：右栏「人物认知」那一格的空格子 → `KnowledgeMatrix.tsx` 的 `CellAdder`，章号取自 `matrix.chapter`、版本取自 `matrix.version.canon_version` | 🟢 |
| POST | `/projects/{pid}/canon/events/{event_id}/cast` | `corrections.correct_event_cast` | `EventCastCorrection`·改一条已生效**事件**的知情/在场名单：`{knower_ids?, participant_ids?, expected_canon_version}`，绝对集合、`null`=不动。删一个人 = 那一行 `status=RETRACTED`（行留着，读路径看不见）。空编辑 422·**前端调用方**（2026-08-11 起）：右栏「待确认」那一格下半截 → `CanonEventCast.tsx`，勾选框即绝对集合，**只发作者动过的那一维** | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/provisional/confirm` | `confirm_provisional_*`（幂等回执） | `ProvisionalConfirmation` | 🟢 |
| POST | `/projects/{pid}/chapters/{n}/extract` | `runner.enqueue`（后台执行） | `ExtractionRunView`（202） | 🟢 |
| GET | `/projects/{pid}/extractions/{run_id}` | `runner.get` | `ExtractionRunView`·**出参不是审计模型 `ExtractionRun`**（2026-08-13）：`errors` 是一串**已经翻好的中文**（`tuple[str, …]`，同 `ActivityDetail.errors`），`code` / `message` 都不出这道门。`ExtractionRunError.message` 是写给**维护者**的英文诊断，而这条端点的唯一消费者是浏览器里的审阅面板——它此前把整条 error 原样发出去，屏幕上是 `chapter analysis provider failed`。措辞的唯一出处是 `activity._RUN_ERROR_LABEL`（日志页那条读端早就在用），**前端不写第二张表** | 🟢 |
| GET | `/projects/{pid}/chapters/{n}/events?scope=` | `events_for_chapter` | `list[EventView]`·**两个 scope 前端都在用**（2026-08-11 起）：`PROVISIONAL` 是「待确认的情节」，`CANON` 是「已确认的情节」（改名单的那一半）。此前 `CANON` 在浏览器里一个字都没露过面 | 🟢 |
| POST | `/projects/{pid}/chats` | `ChatStore.create` | `ChatSessionView`（201）·**作者可以同时开好几段**，每段各自 resume。入参 `{title?, house_style?}`——`house_style` 进稳定前缀，**所以它必须跨章不变**（[ADR 0019](adr/0019-agent-loop-not-graph.md) 边界六）；工作台今天不给它输入框，一律发空串 | 🟢 |
| GET | `/projects/{pid}/chats` | `ChatStore.list` + 两次聚合 | `list[ChatSessionView]`·最近说过话的在前。**`pending_lookups` 在这条路由上是真算的**（不是吃默认值 0）：断在半路的那一段和跑完的那一段下一步动作不同，而这一页是作者唯一一次看得见全部会话的地方。**`message_count` 含不上屏的那些**（工具往返），所以界面上不许把它当「你们说了几句」显示 | 🟢 |
| GET | `/projects/{pid}/chats/{id}` | `ChatStore.load` | `ChatDetail`·**给的是整段**（canonical 只增不改，没有分页端点）。超长时的处置在前端：`chat.ts::tailWindow` 只渲染尾巴，早先那些点一下就全在——**一条都不许真丢** | 🟢 |
| DELETE | `/projects/{pid}/chats/{id}` | `ChatStore.delete` | `ChatDeleted`·**不动任何一行正文**（正文在磁盘上，那两张表里没有它，[ADR 0019](adr/0019-agent-loop-not-graph.md) 边界三）。正在跑的那一段 409 带一句人话，前端原样说、**不静默重试** | 🟢 |
| POST | `/projects/{pid}/chats/{id}/turn` | `agent.loop.run_turn` | `TurnReceipt`·`{chapter, said}`。**`chapter` 必填、无默认值**——投影在它缺席时不过滤，而那是「没接线」默认值不是安全默认值（边界五：第 90 章的禁说清单是第 40 章那份的**子集**）。`said` 留空 = resume。**这一版 HTTP 不流式**（内部流式），所以拿到的是一个跑完才回来的响应；出参是**对话的投影不是原文**（工具返回一条都不出去，只给一个 `lookups` 计数） | 🟢 |
| POST | `/projects/{pid}/chats/{id}/stop` | `LIVE.stop` | `ChatStopped`·**`stopped=false` 不是失败**（那一刻它本来就没在跑），200 + 一句人话。它不等这一轮跑完 | 🟢 |
| GET | `/projects/{pid}/drafts?chapter=&limit=` | `DraftCandidateStore.recent` | `{drafts: DraftCandidateView[]}`·最近的在前，**不带正文**（一次列 20 稿 = 20 章正文）。**它不是版本历史**：`/chapters/{n}/history` 里是**已经在书里**的，这儿是**还摆在桌上**的（[ADR 0022](adr/0022-drafting-is-a-proposal-not-a-write.md)——没落盘的候选在磁盘、快照里都不存在，没有这条路由作者关掉那一轮回执就再也找不到它们）。**前端调用方**（2026-08-12 起）：`ChatPanel` 头上那条「还摆着 N 稿 ↗」+ 并排比那一页 | 🟢 |
| GET | `/projects/{pid}/drafts/{draft_id}` | `DraftCandidateStore.get` | `DraftCandidateView + {text}`·**摊开那一版读的就是它**。404 `draft_not_found`·**前端只在作者亲手点开某一稿时才打**（`useDraftText(…, open)`），入口那一档只摊开 `landed` 那一版——三稿 ≈ 9,000 字硬摊在入口上，作者要读完三章才做得了一个决定 | 🟢 |

> **「推荐哪一版」不是引擎给的，前端也不许反推**（同「跳转坐标由后端给」那条禁令）。
> 唯一的判据是 `landed`——助手把哪一版 `save_draft` 进了书，那是一个**动作**不是一句评价
> （[ADR 0005](adr/0005-set-judgment-only.md)：引擎不给散文打分）。一批都没落盘时
> **两边都不挑**（同 `AmbiguousName`：两个方向都贵就摊开），界面照 `ordinal` 顺序摆。
> `note` 是**写那一稿的那个模型**自己交的一句话，**可能是空串**——空的时候界面上不许硬编一句。
> `id`（`draft:01J…`）**一个字符都不许上屏**，屏幕上说的是「第 N 稿」（`ordinal`）。

> **写作助手那六条上，措辞的唯一出处在后端。** `TurnReceipt.reason` 是 snake_case 机器码
> （`agent.loop.StopReason`，故意的——原样上屏会被两侧的形状判据当场咬住），
> 说给作者的那一句是 `TurnReceipt.message`（`stop_wording()`）。**前端不许再翻一遍**，
> 只补后端说不出来的那两句：作者按过停没有（`chat.ts::stopFootnote`——它那一轮跑到最后
> 一步才收到停，报 `done` 是对的，但屏幕上写「说完了」读起来像按钮坏了），
> 以及这一轮的上下文裁掉了什么（`receiptNotes`，零不写、非零必须带理由）。
>
> **`**` 那一对是重音不是星号。** `stop_wording(CONTEXT_FULL)` 里有一对字面量 `**`；
> 前端把它渲染成强调（`chat.ts::emphasize`）——**那不是第二份措辞源**，一个字都没换。
> 改那句话本身才是。

> **`/matrix` `/constraints` `/state` 的 `?cast=` 留空不再等于「一个人都没有」，而是「你自己去正文里数」**
> （[ADR 0018](adr/0018-cast-is-derived-not-declared.md)）。作者传了就听作者的；空着走 `mentioned_cast`；
> 正文不存在 → 推不出人 → 空 cast → `scene_constraints` 照旧退化成全禁。
> **顶栏那个「出场人物」输入框已经删掉**——在场人物是写出来的结果，不是写之前的输入。

> **`?include=` 和 `?cast=` 方向相反，不许混用**（2026-08-11 起）。`cast` 是**过滤**
> （「只看这几个人」），唯一来源曾经是作者亲手标的场景块（**2026-08-14 那条来源没了**，
> ADR 0027，于是 `cast` 今天在界面上没有写入方）；`include` 是**只加不减**
> （「这几个人也要算进来」），给的是日志页那个由**系统**算出来的跳转坐标（`ActivityJump.cast`）。
> 三条读端共用同一份在场，而 `must_not_reveal` 的判据是「在场的人里至少有一个还不知道」——
> 把一个只知道一个人的坐标塞进 `cast`，禁令会**少一批**（fail-open，ADR 0018 §3）。
> 推导为空那一档 `include` 一律不加人：那时的含义是「不知道谁在场 ⇒ 全禁」，加人会把它撬开。
> 前端 store 里因此是两个字段（`cast` / `castInclude`），`jumpFromActivity` 只写后一个。

> **`autopilot` 的触发点是「离开某一章」，不是「保存某一章」。** 这不是实现偏好，是成本事实：
> 滚动总结的幂等键是**正文的**哈希（`sha256(build_summary_messages(text))`），所以「保存后自动总结」
> 会在作者写一章的过程中每存一次就换一次哈希、重新付一次费——写一小时存 30 次 = 30 次调用。
> 作者说的是「**写完后**」，而机器能识别的最接近的信号是**换章**。所以前端在切章时打这条，
> **不许**把它挂到保存键上。它幂等（总结走 `ensure`、抽取走 `enqueue`），来回切章不重复付费。
>
> 三件事写死在 `api/autopilot.py` 里，改之前先读那份 docstring：**① 总是 202**（换章是无人值守的
> 动作，模型没配好就弹 4xx = 每换一章骂作者一次，真相放在回执体里）；**② 自动链路不自动重试**
> 失败的抽取（重试要再付一次钱，得作者点 `/extract?force=true`）；**③ 失败不许静默**——
> `chapter_summary` 表只记成功，所以后台总结炸掉在库里一个字节都没有，那条留痕是进程内的
> （重启即失，GET 会退回说「还没生成」）。**改过正文的章不会被自动重新总结**（已有一条就判
> `skipped`，同 `coverage()` 只问「有没有」不问「新不新」），显式那条按钮仍会按新哈希重生成。

**关键陷阱（壳写错就退化成 fail-open）：**

- **`/matrix` 必须走 `panel.knowledge_matrix` 不是 `store.knowledge_matrix`**——只有 panel 包装器挂 `unresolved_cast`。直调 store 会**静默丢掉「作者声明了但解析不出的人」那一行**，正是本项目要防的漏洞。
- **原始称呼 vs node_id 是故意不一致的**：`scene_constraints` + 全部 `/declare/*` 收**作者原始称呼**（内部自解析，调用方没机会弄丢人）；`knowledge_matrix` / `character_state` / `cast_states` 收**已解析的 node_id**。`/matrix` 和 `/state` 先调 `resolve_cast` 喂 `.ids` + `.unresolved`；`/constraints` 把 `?cast=` 原样透传。
- **出参收窄防泄密**：`resolve`（roster）和 `subgraph` 出**完整 `Node`**，而 `NodeProps` 是 `extra="allow"`——一个 Secret/未来节点会把作者写的 `props.twist`/`plot_note` 序列化出去。壳必须把任何 `label=Secret` 或 `first_appears>chapter` 的节点**收窄成 `NodeRef.of(node)`** 再 JSON（矩阵/约束/forbidden 已经是 `NodeRef`，安全）。
- **PROVISIONAL 灰显设计成独立第二次调用**（`scope=PROVISIONAL`），永不混进 CANON 响应（§5.4：PROVISIONAL 永不断言为真）；兑现时 `PLANNED`/`REJECTED` 要被 `require_queryable_scope` 拒 → 422。**但今天没有这个入口**：`/matrix` 和 `/state` 里 scope 硬编码 `InformationScope.CANON`，`?scope=` 一个字符都没实现，所以 v1 的界面上根本没有灰显那一层。设计留着不删——它是那条能力的载体，删了下次就得重新想一遍为什么要分两次调用。

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
| `QuoteNotFound` | 422 | `quote_not_found`（提示：逐字精确、复制别手打、**这一章可能还没读回来** —— 声明抽屉据它摆出「读回改动」；那句话里**不许出现命令**，见 ARCHITECTURE「已知洞」第 10 条） |
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

#### 2.1.1 中栏对半分：左边正文、右边写作助手（模式二，2026-08-11）

作者的原话是「**文章那块对半分，左边是文章右边是 agent**」——所以切的是**中栏这一块**，
不是整行：顶栏一颗「写作助手」开合，**左栏书架和右栏面板一个像素不动**
（他一边跟它说话，一边看得见这一章谁还不知道什么）。默认关着，关的时候连那根分隔条都不渲染。

```
├──────────────┬───────────────────┬───┬──────────────┬───────────────────┤
│ 左栏 240px   │ 正文（≥320px）     │ ⇔ │ 写作助手     │ 右栏 400px         │
│ **不动**     │                   │   │ （≥280px）   │ **不动**           │
```

三件这块布局自己要守的事，都在 `frontend/src/layout.ts`：

- **那根分隔条存的是百分比，不是像素**（`nh.chat-pane.v1`，和左右两栏那个键分开）。
  「对半分」是一个比例：存成像素的话窗口一变宽它就不再是一半，而首帧量不到容器宽
  （jsdom 里恒为 0）时根本算不出「一半是多少像素」。白捡的一件事是方向键那条交互
  **不需要量任何东西**，于是它在 jsdom 里真的可测。
- **中栏的下限跟着开合变**（`centerFloor()`）：开着的时候要同时装下正文和它。
  少算这一段，作者一开面板正文就被挤成一条缝——他连拖都没拖。
- **「作者拖成什么样」和「这一刻画成什么样」是两个数**：state 存意图，渲染前才按当前
  容器宽夹一遍。存夹完的结果 = **开一次写作助手就把他拖好的左右两栏永久压到下限**，
  关掉也回不来（窗口拖窄再拖宽是同一个故障）。

面板本身（`ChatPanel.tsx` / `ChatSessions.tsx`）守的四条，判据都在测试里：

1. **不许假装在逐字吐。** 这一版 HTTP 不流式（内部流式），拿到的是一个跑完才回来的响应。
   诚实的形态只有两样：**一个还在跑的信号 + 一个真实的秒表**，外加一句「跑完才会一次性
   出现整段回话」。编一个打字机动画出来，作者会按那个编的节奏判断它是不是卡住了。
2. **工具返回不上屏，前端也不许自己去把它捞回来补上**——后端出参已经是投影不是原文，
   那里头是 `NodeRef` 的裸标识。能说的只有一个数：这一轮查了几次。
3. **一轮钉在它自己那一段对话上。** 一轮跑好几分钟，而作者可以同时留着好几段：
   秒表和回执必须记着自己属于哪一段，否则它们会画在他此刻看着的那一段上，
   而那一段什么都没发生。
4. **断在半路的那一段在列表上看得出来**（`pending_lookups`），但**不画「恢复」按钮**——
   接着说一句（或者「接着往下」发一句空话）就会自动把缺的那几步补上。

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
│  │  ├─ <RosterSection label=…>  ◀ GET /roster（出全项目，按 label 分组在前端做）
│  │  ├─ <DocLinks 大纲/世界观>    ◀ 磁盘 md（无图谱背书）
│  │  └─ <RecentRuns hidden>      ◀ 用量搬去「活动记录」页顶上了（GET /runs，2026-08-10）
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
│  │  │        ├─ <CellEditor>    ▶ POST /canon/knowledge（「知道」↔「以为」）
│  │  │        └─ <CellAdder>     ▶ POST /chapters/{n}/canon/knowledge
│  │  │                             （「不知道」那一格上**补**一条；章号在路径上=这张表
│  │  │                              画的那一章，作者一个数都不敲；这一格已经有事实→409，
│  │  │                              **不兼做「改」**，一个能力不留两个入口）
│  │  └─ Tab5 <IssueList>         ◀ POST /check（Issue+anchor+evidence；显示「确定性」）
│  │      Tab6 <ProposalReviewTab> ◀ GET /proposals + /events?scope=PROVISIONAL
│  │         ▶ POST accept|reject|bystander · provisional/confirm · extract
│  │         └─ <CanonEventCast>  ◀ GET /events?scope=CANON
│  │                              ▶ POST /canon/events/{id}/cast（勾选框=绝对集合，
│  │                                只发动过的那一维；日志页跳过来时按 `jump.event_id` 展开）
│  │      Tab7 <SummaryTab>       ◀ 2026-08-13：GET /chapters/{n}/summary（**跟着左栏那一章走**）
│  │                                + GET /chapters/{n}/summaries（「这一稿带得上几段」那一句）
│  │                              ▶ PATCH（改，不花钱）· DELETE（撤回，不花钱、不删行）
│  │                                · POST（重新生成，**要跑一次模型**，按钮上自己说）
│  │                                **它不吃花名册**：这一段是正文压出来的，和「书里有谁」
│  │                                无关，所以花名册空着时它照常显示（右栏别的格全靠人）。
│  │                                「你撤回的」和「还没生成」说两句话——起草那边它们同义，
│  │                                下一步动作却相反
│  │         └─ <Memories>/<Trail>  ◀ 2026-08-13 下半：**每一段总结 = 一个可反查的记忆点**
│  │                                GET /chapters/{n}/summary/mentions（一排芯片）→ 点一个 →
│  │                                GET /nodes/{id}/summary-mentions（别的章按章号摊开，带原文，
│  │                                点章号 = setChapter + 回工作台）。**这一层没有会花钱的按钮**，
│  │                                也**不许出现「相关度 / 匹配度 / 相似」**——引擎在数字符串，
│  │                                写个百分比等于向作者承诺它读懂了剧情
│  ├─ <ChatPanel>     ◀── 2026-08-11 模式二（ADR 0019）：中栏右半边，顶栏开合，默认关
│  │  ├─ <ChatSessions>          ◀ GET /chats（多段并存，各自 resume）
│  │  │                          ▶ POST /chats · DELETE /chats/{id}（正在跑 ⇒ 409，原样说）
│  │  ├─ <Bubble ×N>             ◀ GET /chats/{id}（整段；长了只渲染尾巴，早先那些点一下全在）
│  │  ├─ <RunningStrip>          ▶ POST /chats/{id}/stop（`stopped=false` **不是失败**）
│  │  │                            **真秒表，无打字机**——这一版 HTTP 不流式
│  │  └─ <Receipt>               ◀ POST /chats/{id}/turn（`chapter` 必填 = 顶栏那一章；
│  │                               `said` 留空 = resume。措辞出处在后端，这里只补
│  │                               「你按过停」和「这一轮裁掉了什么」两句）
│  │     └─ <DraftCandidates>    ◀ TurnReceipt.drafts（ADR 0022：**同一份数据三档排布**）
│  │        │                      窄=一稿一张卡（推荐那版摊开、另两版自述+开头）；
│  │        │                      拖宽=几列并排各自滚（判据 `drafts.ts::sideBySide`）
│  │        └─ <DraftCard>       ◀ GET /drafts/{id}（**只在摊开时取**，不摊开零字节）
│  └─ <BottomBar>
│     ├─ <SceneTimeline>          ◀ parse_scenes 序 + edge.valid_from/valid_to
│     └─ <RunTelemetry collapsed> ◀ 同上：整理次数 / token / 花费在「活动记录」页顶上
│
├─ <ActivityLog>      ◀── 2026-08-10 ADR 0020 的「可查」，**换的是中栏**（左右两栏不动）
│  ├─ <UsageStrip>               ◀ GET /runs（花费恒「未记录」——`model_call.cost` 没有写入方）
│  ├─ <ActorFilter>              ◀ ActivityPage.actors（计数全量，**不随过滤缩**）
│  ├─ <EntryRow ×N>              ◀ GET /activity?actor=&limit=&cursor=（折叠层，无 payload）
│  │  └─ <EntryDetail>           ◀ GET /activity/{id}（rows / cost / errors；**payload 不上屏**）
│  │     └─ <JumpRow>            ▶ 只换坐标：chapter + 右栏 tab + 高亮那一格 / 展开那条情节
│  │                               （`jump.endpoints` 空 ⇒ 不画编辑按钮，只带你过去；
│  │                               坐标全来自后端的 `jump`，前端不从标题反推）
│  └─ <LoadMore>                 ◀ next_cursor 原样回传（不透明串，前端不拼）
│
├─ <DraftCompare>     ◀── 2026-08-12 ADR 0022 第三档，**唯一一条哈希路由**：`#/compare/{章号}`
│  │                      在新标签页里并排读几稿。工作台本来就是本地浏览器应用
│  │                      （`nh serve` 开的就是 localhost），所以这是**同一个应用的另一条
│  │                      路由，零新基础设施**——哈希不进请求行，服务端一个路径都不用多认。
│  │                      **地址里只有章号**：书是点链接那一下留在本地存储里的
│  │                      （`route.ts`，内部标识不进地址栏——地址栏也是屏幕）；
│  │                      交接读不到且库里不止一本书时**说不知道，不猜**。
│  ├─ <DraftCard ×N>              ◀ GET /drafts?chapter= + GET /drafts/{id}（默认摊开最近 3 列）
│  └─ 只读                        ▶ 无写路由：要用哪一版回工作台跟助手说（一个功能不留两个入口）
│
├─ ~~<ChapterPrepPage>~~  ◀── P2 章节准备 **2026-08-13 删，见下面「页面二」那一节**
│
└─ <GraphExplorerPage>  ◀── P3 全屏图谱（中心漫游，真全图=v2）
   ├─ <GraphModeSwitch>           1 关系 / 3 伏笔 / 4 地点 / 5 认知(头牌) / 6 时间状态
   │                             （2 事件因果 = 永久砍，UI 里没有这一项）
   ├─ <GraphFilters>             ◀ subgraph(edge_types,chapter)：章/人物/类型/只看当前
   ├─ <TimeAxisSlider>           ◀ 不同 chapter 调 state_at（ch100 vs ch151 收敛点）
   ├─ <GraphCanvas Cytoscape>    ◀ GET /subgraph（点节点展开≤2 跳；3 跳硬禁 ValueError）
   └─ <KnowledgeMatrixMode>      ◀ GET /matrix（模式5 全屏）
```

⚠️ **这棵树里有 2 个组件今天不存在**：`<RecentRuns hidden>` / `<RunTelemetry collapsed>`（都是 M2 的隐藏/折叠态，v1 本来就不显示）。`<AIPlanBtn disabled>` 仍是灰置 stub（规划未开放）；`<AIDraftBtn>` 已按 **修正案 7** 点亮并接上真实 `/draft`（实验状态，2026-08-02）——响应与 UI 都带「未经 kill-gate 裁决」标注。M2 有效裁决后再决定是否去掉实验标注。**Tab6 `<ProposalReviewTab>`（M4 审阅）已落地（2026-08-03）**：冲突/低置信/新人物卡 + 被动事件批量确认 + 显式抽取按钮。
**`<ActivityLog>`（ADR 0020 的「可查」）已落地（2026-08-10）。** 它当天只有 Tab6 那一格真能动手，1.1 的两条改正路由在浏览器里还没有调用方，日志页在那一行明说了这件事——**2026-08-11 那句话被改掉了，因为它不再成立**：`POST /canon/knowledge` 的调用方是 Tab4 矩阵里的 `<CellEditor>`，`POST /canon/events/{id}/cast` 的调用方是 Tab6 下半截的 `<CanonEventCast>`。日志页的 `CAN_EDIT_HERE` 里 `knowledge_cell` / `event_cast` 因此翻成 `true`。**2026-08-17（Task 8）`canon_edge` 那一档也翻成 `true` 了**：自动升上去的位置/状态/关系边现在有 `/canon/edges/{edge_id}` 的修改 / 撤回 / 改归属路由，日志行直接弹起 `<CanonEdgeEditor>`——ADR 0020 那条「作者改不回来」的推翻条件就此闭合（[ADR 0032](adr/0032-reversible-auto-canon-edges.md)）。

### 2.3 状态管理：坐标进 Zustand，数据进 react-query

**铁律：能从 API 拉的绝不进全局 store。**

- **全局 store（Zustand）只放坐标**：`projectId`（换项目=整棵 query 树失效）、`chapter`、`sceneCast: string[]`（作者原始称呼，不是 node_id，从选中场景块的 `nh:` 注释解析）、`selectedNodeId`、`textAnchor`（编辑器选区派生，禁 offset）、`scope`、`activeTab`。
- **服务端状态（TanStack Query）装一切可拉数据**：`queryKey = [端点, projectId, chapter, cast/nodeId, scope]`，坐标一变自动重取。出参形状今天由**手写**的 `src/api/types.ts` 定义（原计划的「`openapi-typescript` 生成、零手写 DTO」没兑现，为什么见 §1 开头）。
- **为什么这样分**：`KnowledgeMatrix`/`StateSnapshot` 是对给定坐标的确定性投影，react-query 的 staleness/refetch 免费搞定失效，且天然支持 ADR 0007 的两条实时路径：**面板**（场景元数据变→invalidate matrix/constraints，本机 2–5ms 瞬时）+ **规则**（正文变→debounce 2s→invalidate `/check`）。写路径（`declare_*`/`sync`）成功后精确 invalidate 受影响 key（如 `declare_where` 改了地点→失效该章 state/matrix/subgraph）。
- **唯一的本地可变状态**：CM6 编辑器 doc 自持，保存时才 PUT + sync，脏态用 `isDirty` 标记不进 react-query。
- **写作助手跑完一轮之后，正文那一侧一律重取**（`text` / `chapters` / `history` + 那五格面板，
  和作者自己按保存之后是同一批）。理由不是显示滞后：**助手起草直接写进磁盘上那一章**
  （ADR 0021，走的是 `PUT /chapters/{n}/text` 那条同一条路径），而 `TurnReceipt` 上
  **没有「它到底写没写」这一位**，所以只能一律重取——否则作者对着一份旧稿按保存，
  盖掉的是助手写的一整章。
  ⚠️ **这条的前提是编辑器不会拿重取到的正文盖掉他没保存的字**（`editorDoc.ts`：
  先比出处再看脏态，脏着就只说一句、一个字不动）。**两件事必须一起在**——
  只补前者 = 他一边打字一边跟助手说话，刚打的半段被静默吃掉，
  而**没保存过的东西哪儿都找不回来**（版本历史只存保存过的）。

### 2.4 编辑器：CodeMirror 6，不是 TipTap

选 **CodeMirror 6**（`@codemirror/lang-markdown`）作应用内编辑器。

- **怎么守 ADR 0007**：编辑器只是磁盘 `chapters/NNNN.md` 的便利视图，不是新真相源。打开=读盘，保存=写回同一个 md 再 `importer.sync`。磁盘文件始终可被 WPS/VSCode/Obsidian 平行编辑。

  > ⚠️ **这一行原来写着「外部改动经 file-watch → sync 回流」，而回流那一半从来没建**（`/sync` 在浏览器里零调用方）。**2026-08-13 补上的是一颗按钮，不是 file-watch**，而那是一个决定不是欠账：sync 是写路径（每次落一条快照，快照是证据的锚），「磁盘先、DB 跟」里的那个「跟」是作者的动作；自动跟着磁盘写库等于给他一条按不停也看不见的写入面，还要往 wheel 里加一个平台相关的监听依赖。理由全文和「剩下什么」在 [`ARCHITECTURE.md` 的「工作台的已知洞」第 10 条](ARCHITECTURE.md#工作台的已知洞)，**别在这儿写第二份**。
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
| 一致性检查 | `run_checks` / R2·R3 | 🟢（印「跑了几条规则」+ 规则名避免静默零；R4 按 [ADR 0027](adr/0027-scene-blocks-cut.md)、R5 按 ADR 0014 砍掉） |
| AI 规划 | — | 🟡 M2（灰置 stub） |
| ~~AI 起草（抽屉）~~ | ~~`/draft`~~ | **2026-08-10 删掉界面**，端点保留给模式二 agent 当工具 |
| 左栏 人物/地点/势力/物品/伏笔 | `resolve` 按 label 过滤 | 🟢 读端就绪；⚠️ 势力/物品/伏笔 **M1 无 declare 写路径**，v1 初期空 |
| 左栏 大纲 / 世界观 | — | ⚪ 降级为磁盘 markdown，无图谱背书 |
| 中栏 正文 | 磁盘 md | 🟢（**场景块那一半 2026-08-14 砍了**，ADR 0027） |
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
| 底栏 Harness 10 步 / Token / 成本 | `model_call` + `GET /runs` | 🟡 数据和读端 2026-08-10 都有了，**底栏那一格仍未画**——用量显示在「活动记录」页顶上（v1 是单次调用不是 10 步 Kernel） |

### ~~页面二 · 章节准备~~ —— **2026-08-13 整页删除**

作者的原话：「我记得我没有设计过这个东西，或者说我压根没打算用到这个 UI 上面。」
删之前逐项查过一遍，**没有一格是独一份的活功能**：

| UI 元素 | 它当时的样子 | 为什么删得掉 |
|---|---|---|
| 当前状态 / 认知矩阵 | `state_at` / `matrix`，第 N 章 | 右栏「人物状态」「人物认知」**是同一个组件、同一份数据**——第二个入口 |
| 禁止提前出现的未来内容 | `forbidden_entities` | 右栏「写作提醒」同一份 |
| 上一章结束状态 | `state_at(N-1)` | 唯一不重复的一格，但它 = 右栏那一格换个章号看 |
| 本章目标（自由文本） | localStorage `nh-brief:` | **写进去没有任何人读**：不进请求、不进起草、不落盘 |
| AI 起草长度 | localStorage `nh-draft-length:v1` | 同上。起草走 `agent/drafting.py` 的产品默认档，**根本不带作者这一档**（见 [ADR 0013](adr/0013-draft-length-is-a-request-parameter.md) 分界线） |
| AI 推荐场景骨架 | — | 从来没做（原计划 M2） |
| 在场输入框 | — | 更早就被 [ADR 0018](adr/0018-cast-is-derived-not-declared.md) 删了，那次它已从「写作前简报」降级成「核对」 |

**这一页是「设计稿上有、工程上没长出对应能力」的标本**：三张读卡在别处已经有了，
两个表单是**看着能填、填完什么都不会发生**的控件——比不做更糟，因为它承诺了一件做不到的事。
真要给这几张卡一个整页视图，先想清楚它比右栏多给了什么。

### 页面三 · 全屏图谱 & 联动 & 变更页

| UI 元素 | 背书 | 判定 |
|---|---|---|
| 模式1 人物关系 / 模式4 地点 / 模式5 认知（头牌） / 模式6 时间状态 | `subgraph` + `matrix` + `state_at` | 🟢 |
| 模式3 伏笔图 | `Foreshadow`/`PLANTED_IN` schema 就绪 | 🟢 需补 declare foreshadow；PLANNED 未来侧不进读路径 |
| **模式2 事件因果图** | — | ⚫ **永久砍，从 UI 删除** |
| 全屏 1–3 跳 Explorer | `subgraph` | v2（3 跳数学上坏）；v1 只交付 hops≤2 局部漫游 |
| 联动 AI 生成后自动提取变化 | — | 🟢 M4 已落地：`POST /extract` 显式后台抽取 + 运行状态轮询；抽取结果进 PROVISIONAL |
| **变更确认页（整页）** | `proposal_set` | 🟢 M4 已落地：右栏「待确认 · N」tab（冲突/低置信/新人物卡 + 被动事件批量确认）；~~进 CANON 必有作者动作~~ **[ADR 0020](adr/0020-clean-extraction-auto-canon.md) 已推翻后半句**：干净的抽取结果自动升 CANON，退路是「可查 + 可改」 |
| **改一条已经生效的事实** | `corrections.py` + 两条 `/canon/…` | 🟢 2026-08-11 已落地：矩阵那一格改「知道」↔「以为」、「待确认」那一格下半截改已确认情节的知情/在场名单。**没有章号输入框**（约束 10），**「不知道」的格子不给入口**（那儿没有可改的事实，新增走声明） |
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

### P1 · 应用内 Markdown 编辑器 + 磁盘同步 + ~~场景块~~

> ⚠️ **场景块那一条 2026-08-14 整个砍了**（[ADR 0027](adr/0027-scene-blocks-cut.md)）。
> 下面这一节留着是当时的计划记录，**别照它重做一遍**：它交付的东西今天一样都不在。
**交付**：作者在应用内改正文、保存即落快照（文件仍在磁盘），编辑/拖动场景块并回写，拉出「当前 md vs 历史快照」作者版对比。**守住 ADR 0007。**
- [BE] `PUT /chapters/{n}/text`（写磁盘 → `sync`；`SyncRefused`→422 带 path）
- [BE] `GET /chapters/{n}/history` + diff（标注「快照按内容去重、非全量版本史」）
- [BE] 场景块解析/回写走 `text.scenes`
- [FE] CM6 编辑器（非富文本）读写磁盘 md + 显式/自动保存
- [FE] 场景块 UI（拖拽排序/增删/改目标 → 回写 md）
- [FE] `TextAnchor` 桥脚手架：段落级定位一律三元组，代码里彻底不留 offset

### P2 · declare 写入闭环 + 名称消歧 + 局部关系图 + ~~R4 内联~~

> ⚠️ **R4 那一条 2026-08-14 砍了**（ADR 0027）。同上：别照它重做。
**交付**：作者敲原始称呼 + 引语就能写图谱（系统算章号、给收据、自动关闭旧地点边）；名字歧义有选择器；选中正文能查节点/局部图；对本章跑 R4 并在正文精确高亮冲突。**这是 v1 核心闭环。**
- [BE] 每请求构造 `Ledger`（唯一同时持 store+conn，一条共享连接）
- [BE] `POST /nodes`（全 8 label）· `/aliases` · `/declare/{knows,believes,where}`（入参=称呼+引语，永不 node_id、永不章号）
- [BE] 返回 `Declaration` 收据（`valid_from` 标注「系统算的」+ closed/retracted 边）
- [BE] `POST /locate` · `GET /resolve` · `POST /check`（R2/R3/R4）· `GET /subgraph`（hops>2→422）
- [FE] declare 表单：**坚决无章号输入框**；「测这条引语」调 locate；提交后显示「valid_from=chN（你没填）」+ 自动关闭的边
- [FE] 消歧选择器（复用于 cast 输入/declare 目标/名字搜索）
- [FE] Tab2 局部图（点节点展开≤2 跳；`RELATED_TO` 用 `peer_of` 不用 `e.dst`）
- [FE] 联动 1/2/4：选区→图谱、点节点看详情、冲突→高亮句+定位节点+「改正文 or 改图谱」

### P3 · 章节准备页 + 确定性证据/时间线/联动打磨
**交付**：作者进章前有一张全由确定性读端拼出的准备页；右栏证据与约束、底栏场景时间线补齐；大纲/世界观以磁盘 markdown 降级交付。**v1 收尾。**
- [BE] 准备页读端聚合：`state_at(N-1)` + `forbidden_entities` + `scene_constraints` + 当前章 matrix + CANON 侧已埋伏笔
- [BE] Tab3 确定性证据（`Evidence.anchor()` → 来源章/原文片段/是否 Canon/图谱边；**明确不给 score**）
- [BE] 底栏场景时间线（`parse_scenes` 序 + 边闭开区间；无全局事件线——事件未建模）
- ~~[FE] P2 章节准备页（场景骨架=作者手拖手填，替代未实现的 AI 骨架）~~ —— **做过，2026-08-13 又删了**（上面「页面二」那一节记着逐项判据）。**别照这一行重做一遍**：它交付的三张卡今天在右栏，两个表单当时就没接线。
- [FE] Tab4 约束页（must_not_reveal + forbidden 直读；作者手填字段清楚标注「引擎不背书」）
- [BE/FE] 扩展 `demo.sh` 心跳覆盖「导入→读面板→declare→再读矩阵变化→R4」端到端，作为 v1 交付验收线

---

## 5. 与 ADR 的关系

| ADR | 这份方案怎么对待 |
|---|---|
| 0001 无 Neo4j | ✅ 守住。图谱全走 SQLite `subgraph`，前端只是渲染层 |
| 0002 无向量 | ✅ 守住。Tab3 只给确定性证据；检索分数砍到 v1.1 |
| 0003 ULID | ✅ 守住。node_id 全程 ULID，前端不解析语义 |
| 0004 声明优于抽取 | ✅ 守住。M4 落地后「系统抽→作者审」仍逐条经作者确认（抽取只进 PROVISIONAL，进 CANON 必有一次作者动作；被动确认也走显式回执） |
| 0005 只做集合判断 | ✅ 守住。Tab5 只有确定性 R4；事件因果图永久删除 |
| 0006 禁 offset | ✅ 守住。编辑器↔图谱桥只有 `(para_index, quote, k)` |
| **0007 正文在磁盘 / v1 不做编辑器** | ⚠️ **实质守住，时机松动**：应用内 CM6 编辑器进 v1（作者选定的非程序员 GUI 方向的直接推论），但只读写磁盘 markdown，DB 永不是正文真相源。TipTap 富文本仍 v1.1 |
| 0008 RELATED_TO 无向 | ✅ 守住。前端取对端用 `peer_of` 不用 `e.dst` |

**唯一需要作者确认的松动**：ADR 0007 原写「v1 不做编辑器」。你选的方向（非程序员能用的 GUI）要求应用内编辑器——本方案让它进 v1，但用 CM6 over 磁盘 markdown 守住 ADR 0007 的实质。如果你想更保守（前端只读、作者仍用自己的编辑器），P1 可以只做只读渲染+file-watch，把编辑器推后。
