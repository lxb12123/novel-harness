# Novel Harness v1 实施计划（最终版）

> 首席架构师定稿。以「编辑器优先」方案为骨架（评审总分 20.0，三位评审里两位判它胜出），嫁接其余三个方案被点名的好点子，并对两位对抗性批判者提出的每一条 fatal / serious 逐条回应。
>
> 第三位批判者（「时间线现实」）返回的是测试桩（`总判：测试` / `[fatal] a｜何时崩：b｜修法：c`），无内容可回应。我自己补上了时间线压力测试：见第 7 节的全职/业余系数与第 10 节的风险 3。

---

## 1. 一句话定义 v1

> **v1 完成的标志是：一位写中文长篇、正文用 Markdown、会开终端的作者，执行 `uvx novel-harness`，导入自己 200+ 章的小说，用半小时声明 10 个秘密和它们的持有者集合，之后在写第 151 章的任意一个场景时，右侧面板都能在他敲第一个字之前正确告诉他「这个场景在场的三个人里，谁还不知道血脉秘密、谁相信着一个错误版本」；并且当他点「起草这个场景」时，这份认知边界会进入 Writer 的 prompt，让 AI 也不会说漏嘴——而这件事在合成书的 25 个认知陷阱上有一个可复现的、跑在 `make eval` 里的数字支撑。**

这句话里每个成分都是可客观判定的：`uvx` 能不能装上（能/不能）、面板矩阵对不对（作者逐格核对）、约束有没有进 prompt（看 manifest）、数字有没有（`make eval` 输出表）。没有一处依赖「作者觉得写得好不好」这种被开发耗干的人做的主观判断。

---

## 2. 我改了你哪些决定（决断清单）

先说保住了什么，再说改了什么。因为下面的清单很长，我不想让你读完觉得项目被掏空了。

### 2.1 原文档的主张，一条都没丢

| 文档主张 | v1 状态 |
|---|---|
| 时间态图谱（valid_from / valid_to / canon_version / scope / evidence / confidence） | **100% 保留**，一个字段不少 |
| Evidence 是一级对象、精确指向原文 | **100% 保留，而且加强了**（双指针 + STALE 失活检测，见 §5.6） |
| 信息作用域隔离（PLANNED 不进 Writer Prompt，只转译为 must_not_reveal / forbidden_entities） | **100% 保留**（原则 11 是 v1 的头牌，不是附属品） |
| Agent 不得直接修改正式 Canon | **100% 保留**（三层图谱 + PROVISIONAL 边永不开火，见 §5.4） |
| 作者确认后才能 Commit | **100% 保留语义，改了粒度**（从「每条事实」改成「每次冲突」，见 §5.4） |
| 原子提交 + 幂等 | **100% 保留——而且只有单库才做得到，这正是我砍 Neo4j 的核心论证** |
| 局部关系图（写作页右侧、1–2 跳） | **100% 保留** |
| 人物认知图（KNOWS / BELIEVES） | **从第 22 节的第 4 条提到 README 第一行**，是整个项目的心脏 |
| Neo4j 和 Qdrant 共享稳定业务 ID | **保留原则，换了实现**（ULID，见 §5.3——你原来的 `character:{slug}` 会在别名合并那天炸） |

**一句话：我砍掉的是承载方式，保住的是每一条主张。而且我把你埋在第 22 节第 4 条、和「Docker Compose 一键运行」平级的那个东西，提到了 README 的第一行。**

### 2.2 我改了你的决定（逐条标出 + 论证）

#### 【改 1】Neo4j → SQLite。这直接顶了你第 9 轮的原话「我要的就是图还有向量」和原则 12「第一版必须保留 Neo4j + Qdrant」。

我不用「简单」这个理由，那个理由不足以推翻你的明确表态。我用三条：

**(a) 正确性，不是复杂度：Neo4j 让你第 15 节的 Commit Protocol 在数学上无法实现。** 第 15 节要求 Neo4j 提交 + canon_version 递增 + PostgreSQL commit_record + index_job 是一个原子动作。跨两个数据库没有分布式事务，而你在第 9 轮亲手删掉了 Kafka/Debezium——那是唯一的 Outbox 载体。于是 STALE_BASE_VERSION 检查、幂等键、索引水位线全部建立在一个可能已经漂移的版本号上。**你为了「图」引入 Neo4j，代价是这个架构最引以为傲的东西——确定性 Kernel、原则 2 到 5——从「数据库强制」降级成「祈祷不崩」。** 图谱进单库后，整个提交塌缩成一个事务，STALE_BASE_VERSION 变成一个 `SELECT ... FOR UPDATE`，幂等变成一个唯一索引。

**(b) Neo4j 的独门收益，在这个产品里恰好是被禁止使用的能力。** 实测见 `docs/adr/bench/`（500 章、79,430 节点 / 103,264 边 / 23.7MB，可复现）：以一个主要人物为起点、带时态过滤，**2 跳 154 个节点 → 3 跳 4,720 个节点（30 倍爆炸），而可达人物数一个都没多（86/200 → 86/200）**。爆炸全部来自 `APPEARS_IN` / `SUPPORTED_BY` 这类星形高度数边，扫出来的是 Fact / EvidenceRef / State。**3 跳既不可视化，也不增加信息。** 你第 10 轮自己定的原则是「不能让用户全程面对巨大关系图」，所以 v1 硬编码 hops ≤ 2——而带类型过滤的 2 跳实测只有 20 个节点，正好落在产品需要的区间。**Neo4j 相对关系库的独门优势正是 3+ 跳变长遍历和图算法。你付的是全部的架构税，买的是一个产品需求明令禁止的能力。**

> 校订说明（2026-07-16）：本节原稿写的是「200 人物图上 3 跳返回 447/450 个可达节点」。重跑 bench 后该数字不成立，已替换为实测值。结论方向不变且更强。详见 ADR 0001。

**(c) 反悔成本严重不对称。** SQLite → Neo4j = 导出一张边表 + import + 一天。Neo4j → 原子提交 = Kernel 级重写，或者把你刚删掉的 Kafka+Debezium 加回来。项目现在零代码、零真实负载，信息最不充分——此时应当选可逆的那边。

**诚实说明代价（四条，不粉饰）：**
1. 失去 Neo4j Browser。这是真损失，开发早期用它肉眼看图谱确实好用，没有等价替代。缓解：你的产品本身就是局部图谱视图；临时探索 dump 到 NetworkX/Gephi。接受现实：头两周会难受。
2. 变长遍历要写 SQL。但 v1 hops≤2，两层显式 JOIN 比递归 CTE 更快更好读（见 §5.5），一次性成本。
3. 将来的图算法（PageRank 找主角、社区发现找支线）要 NetworkX 在内存里算。**注意这跟你第 4 轮拒绝的方案不是一回事**：你当时拒绝的是「NetworkX 当存储 + SQLite 当持久化」——那个方案该拒，它没有事务、没有并发写。我说的是 SQLite 当唯一真相源（事务/约束/并发全有），NetworkX 只当临时计算层，24MB 的图 load 进内存 <1 秒，算完即弃。存储和计算是两个决定，你上次否掉的是前者，这次不涉及前者。
4. README 上「Neo4j + Qdrant」比「SQLite」唬人。这是真的。我把它明确列为**保留 Neo4j 的最强剩余理由**——但它是营销理由，不是工程理由。如果你想清楚了就是要这个，那是一个正当的产品决定，只是别用工程理由包装它。

**加回 Neo4j 的触发条件（写进 ADR 0001，别凭感觉）：** ① 出现一个 SQL 确实写不了或写不快的查询形状（注意是「形状」不是「慢」，慢就先看执行计划）；② 需要 GDS 级图算法且 NetworkX 内存方案顶不住（按当前 24MB，意味着图要涨 100 倍）；③ 单书超过约 500 万边。届时的正确迁移路径是把 Neo4j 降级为**投影**（真相和 canon_version 留在 SQLite，Neo4j 从 SQLite 异步重建，走你第 14 节**已经有的** `ProjectionWatermark`）——所以先做单库本来就是通往它的第一步，不是弯路。

#### 【改 2】Qdrant / 向量检索 → v1.1。同样顶了你第 9 轮的表态。

**我不说「以后再说」，我给你触发条件和选型。** 理由三条：

**(a) v1 的 10 个上下文分区里，只有 1 个需要 ANN。** 你第 11 节的 A–J 分区已经暗示了正确答案：A/B/C/D（硬事实、当前状态、称呼规则、认知边界）全部来自图；E（本章 Must/May/Must Not）来自 ChapterBrief；F（承接上一章）是 `WHERE chapter=N-1 ORDER BY scene_number DESC LIMIT 2`，确定性、100% 召回，用 ANN 去「找」上一章结尾是把一个必然正确的操作换成一个概率性操作；H（文风样本）是 payload 规则抽样；I/J 是常量。只有 G（相关历史证据）是真检索。**而 G 的确定性部分——「本场景人物最近 k 次出场」——用 mention 索引就是 `SELECT ... WHERE node_id IN (...) AND chapter < N ORDER BY chapter DESC LIMIT k`，比 ANN 准。** v1 交付 10/10 分区，9 个确定性 + 1 个精确索引。向量加的是**语义相似召回**，那是真价值，但它是 v1.1 的价值。

**(b) 认知边界这个核心主张，在结构上就不是检索问题。** 「第 152 章时谁不知道血脉秘密」是一个**集合差集**，不是段落召回。答案不在任何一段原文里——任何 RAG 都检索不到它，不是因为检索得不好，是因为它不存在于文本中，只存在于聚合里。**这条推论很重要：它意味着 v1 不需要跑 B+（向量 RAG）对照组来证明自己**——普通 RAG 在结构上就做不到这件事。B+ 只有当我将来宣称「通用检索更强」时才需要，v1 不宣称。

**(c) 向量的第一天成本不是「装 Qdrant」，是三件你现在做不对的事：** 中文 sparse 选型、分块策略、评测集。调研已经查明 Qdrant 官方 BM25 在中文上 MIRACL-zh Recall@10 = 0.0005（低于随机，issue #8014 至今未修），照抄官方 hybrid search 教程会得到一个「跑通了但 sparse 通道是死的、两周后才发现」的静默失效。而修它的正解（BGE-M3）要 2.2GB + torch，直接杀死 `uvx` 一条命令的装机叙事。**这三件事全都要等到有真实检索失败案例才能做对。**

**v1.1 加向量的方案（已选型，不是占位符）：** `fastembed`（ONNX，不拉 torch）+ `BAAI/bge-small-zh-v1.5`（dim 512, 90MB）+ `sqlite-vec`。全程无 Docker、无 torch、约 100MB 一次性后台下载。**触发条件：** kill-gate 通过后，作者在真实使用中报出「AI 忘了第 87 章那场戏」这类语义召回失败 ≥3 次。不是「等我有空」。

**现在就免费做的事（技术评审 minor 建议，我采纳）：** `chapter` 表加 `indexed_version INTEGER DEFAULT 0`，检索/上下文拼装的返回结构里带 `{canon_version, indexed_version, staleness}`，v1 里 `staleness` 恒为 0。字段免费，语义免费，历史补不回来。这就是把「水位线」从一个子系统缩成一行断言的做法。

#### 【改 3】TipTap 编辑器 → v1.1。改了第 4 节技术栈和第 22 节第 12 项。**这一条也顶了胜出方案本身。**

我保留了编辑器优先方案的骨架和它最强的三条洞察（规则只在作者确认的边上开火 / 价值曲线从 t=0 起步 / 证据是误报缓释而非审计），但我砍掉了它的编辑器。**这不是背叛它的论点，这是执行它的论点**——它论证的是「作者今天就愿意打开」，而「你不用换编辑器」比「我们的编辑器很好用」更接近这个论点。

四条理由：
1. **4–6 周，v1 最大的单项开销。**
2. **它买来一整类 bug。** 技术评审的 fatal #2 说得对：后端字符 offset 和 ProseMirror 的 pos 不是同一个坐标系（node 边界各算 1，40 段的章节里差 41），JS 的 `String.length` 是 UTF-16 code unit 而 Python 的 `len()` 是 code point（网文人名爱用生僻字，扩展 B 区汉字如 𤩝 在 JS 里算 2 个单位，会产生只在特定章节出现的 off-by-one），而且服务端返回的 span 基于快照 N 时前端 doc 已经是 N+7 了。这些会以「偶尔位置差一两个字」的形态出现，被误当成小 bug 调两周。
3. **换编辑器是头号采纳障碍。** 「真实使用」批判者的 fatal #2 是对的：日更作者的编辑器是肌肉记忆——他的快捷键、他的字数统计、他的自动备份、他的断更恐惧。换编辑器 = 一次断更风险 = 掉推荐位 = 真金白银。
4. **决定性的一条：面板是产品，而面板不需要编辑器。** 为了托管一个面板去造一个编辑器，是反的。

**v1 的形态：正文是磁盘上的 Markdown（`chapters/151.md`），你继续用 VSCode / Obsidian / Typora / 任何能编辑 .md 的东西写。Novel Harness 是旁边那个浏览器窗口：它 watch 文件、显示面板、跑检查、起草场景、管图谱。** 场景元数据是一行 Markdown 注释：

```markdown
## 场景 3
<!-- nh: cast=萧决,顾清音,李管家 loc=青云城主府 goal=李管家试探萧决的身世 -->
正文……
```

丑，但零 UI 成本，而且面板上有「本场景在场角色」多选框——你点一下，这行注释被写回文件。50 行代码 vs 4 周。

**副作用（全是好的）：** offset 地狱整个消失（v1 没有行内 decoration）；`revalidate`（作者改了正文 → 证据失效检测）这条真正属于地基的逻辑从第一天起就被真实的编辑行为持续压测，而自研编辑器反而会因为可控而掩盖这个问题（这条洞察来自地基优先方案，我采纳）。

**GIF 变成什么：** 分屏。左边 VSCode 打字，右边浏览器面板实时更新在场角色的认知边界矩阵。8 秒。而且它多说了一句话：**不用换编辑器**。

#### 【改 4】PostgreSQL 控制面 15 张表 → SQLite 同库。改了原则 9 和第 16 节。

单用户本地工具下 SQLite 提供完全相同的事务/唯一键/幂等语义，但少一个容器、少一个连接池、少约 200MB 内存。你第 4 轮否决 SQLite 的理由是「不理解图语义」——但控制面根本不是图，那个理由在这里不成立。原则「控制面与故事真相分离」由 schema 内的表分组保留（`node`/`edge`/`alias`/`secret` vs `chapter_run`/`model_call`/`decision_log`），只换引擎。

#### 【改 5】全书自动抽取 → 别名角色册 + 增量。改了第 22 节第 2 项和第 17 节 Import 页。

这是**唯一一条会在第一天杀死产品的设计**。算账：300 章存量小说 ≈ 16,500 条待确认 Proposal ≈ 130 小时（16 个工作日）≈ 把整本书重读一遍的 3.5–5 倍。作者要花 130 小时把自己本来就知道的事教给电脑，然后才能开始写第 301 章。而且按 GenWebNovel / Conan 的实测数据（中文网文 NER 的 SOTA 只有 F1 73.74，ChatGPT 零样本 21.90；GPT-4 在叙事文本上抽人物关系 F1 只有 0.276），这 130 小时的产出物里关系层有一多半是错的。没有任何个人作者会走完这个流程——他们会在第 200 条 Proposal（大约第 1 小时）弃用。

**改成：** 导入 = 章节切分 + 别名角色册自动抽取 + 作者做**聚类确认**（不是逐条确认）。角色册是唯一值得前置人力的东西——量小（300 章的小说约 50–200 个实体）、作者几秒就能判、所有下游都以它为键，而且 GenWebNovel 显示 PER 恰好是抽得最准的一类，错误集中在别名归并，正好是人工最省力的地方。确认形态必须是对聚类的（「李明在 47 章里被叫作明哥 —— 全部接受？」= 1 次点击换 47 条），不是对条目的。半小时，不是 130 小时。

#### 【改 6】人物认知：从「抽取 + 检测」改成「声明 + 面板 + prompt 注入」。改了第 7 节和第 13 节的形态，但保住并放大了第 4 项的意图。

**这是整份计划最重要的单点改动。** 完整论证见 §3——它同时回应了两位批判者各自的头号 fatal。

#### 【改 7】11 项加权 `final_score` → 不存在。改了第 10 节。

v1 没有检索管线，所以这个公式没有消费者。但它背后的**设计错误必须记在 ADR 里**，因为 v1.1 加向量时会原样复活：`future_leak_penalty` / `rejected_content_penalty` / `stale_state_penalty` 这三项是**类型错误**——它们是硬约束，被当成了软权重。只要某个未来章节片段的 dense_score 够高（比如 0.95），它完全可以盖过惩罚项挤进上下文。一个确定性系统的核心不变量，被降级成了一个可以被别的分数投票推翻的软信号。**正确做法：这六项（future / rejected / stale / entity / scene / canon）全部下沉为 filter，泄漏在物理上不可能发生，而不是「大概率不会发生」。** 改完之后第 23 节的「未来剧情泄漏率」从「靠调权重压低」变成「结构上恒为 0，除非 filter 有 bug」——这才是 Harness 该有的样子。这条原则 v1 就在执行（见 §5.4 的 PLANNED 隔离），只是执行在图查询层而不是 ANN 层。

#### 【改 8】13 状态 ChapterRun 状态机 → 不存在。改了第 6 节。

状态机管的是「多步自动流水线的合法转移」。v1 没有自动流水线——作者手动点「起草这个场景」，一次 LLM 调用，出结果。没有可管的状态。Policy Engine 防的是 Agent 无限生成，v1 没有自治 Agent。Idempotency 防的是重试重复扣费，v1 单机单用户单次点击。**这些组件全都在解决尚不存在的问题。** 保留一张 `model_call` 表如实记录（capability / 模型 / 参数 / prompt_hash / 输入输出 / token / 耗时 / 成本 / attempt）——**这部分不可事后补，因为历史不会重演**；渲染层是 `nh run show <id>` 打印 + 面板角标「上次调用 3.2k tokens / ¥0.04」。

#### 【改 9】删除 `DOES_NOT_KNOW` 和 `PARTIALLY_KNOWS`。改了第 7 节关系列表。

- `DOES_NOT_KNOW` 是组合爆炸炸弹：实体化后额外产生约 67,500 条边，总量冲到 84,000。改为**闭世界推导：不存在 KNOWS 边 ⇒ 不知道**。这是 O(已声明秘密) 而不是 O(角色×事实)。
- `PARTIALLY_KNOWS` 是欠定义的（部分知道「什么」？），无法标注也就无法评测。改用**秘密拆子事实 + 每子事实一条 KNOWS**，表达力完全相同，零新增边类型。

#### 【改 10】全屏 Story Graph Explorer → v2。改了第 22 节第 13 项。

**用你第 10 轮自己的原则砍的：「不能让用户全程面对巨大关系图，写作主场是编辑器」。全屏图谱正是那个巨大关系图。** 加上实测 3 跳 = 全书，这个页面的核心交互（一到三跳展开）在数学上就是坏的。它是给作者「看着爽」的，不是给作者「写得对」的。局部图（1–2 跳、跟随光标）承担了 90% 的真实价值。

#### 【改 11】Docker Compose → `uvx novel-harness`。改了第 21 节和第 22 节第 17 项。

这一项不是砍，是被更好的方案替换。`uvx novel-harness` 比 `docker compose up` 更一键，而且不要求装 Docker Desktop。

#### 【改 12】LLM Validator → v2。改了第 22 节第 10 项的一半。

慢（10–30s）、花钱、结论主观（文风、模板化抒情、说明书式叙述）——作者会不同意，然后把它整个关掉。更要命的是**它的误报会连坐硬规则的可信度**，用户不会区分是哪个模块在吵。而且它的输出质量完全取决于 Prompt 迭代，Prompt 迭代需要真实草稿样本，v1 阶段样本量为零——此时写 LLM Validator 是在无数据的情况下猜规则。`ValidationIssue` 的 Pydantic Schema 和 `validation_report` 表 v1 就建好（`issue_type` 是开放字符串枚举，直接采用 ConStory-Bench 的 5 类 19 子类分类法，别自己发明第 23 节那 13 个指标），v2 直接往里填。

#### 【改 13】局部 Patch → v1.1。改了第 22 节第 11 项。

两个理由。其一，纯 Prompt + Diff 工程，v1.1 一周能加。其二更要命：Patch 会修改正文 offset，而 v1 的 Evidence 锚定和 revalidate 还没被真实压测过。**在地基没稳时引入一个持续制造 offset 漂移的功能，等于把最难调的 bug 提前引爆在最不该引爆的时候。** v1 的规则只报问题、给定位、给建议（`suggested_action` 由规则确定性产出——「师兄」→「萧决」的答案图上就写着，在一个已知答案的地方引入 LLM 是净损失），作者自己在自己的编辑器里改。改完触发 revalidate，反而给了地基一次真实检验。

#### 【改 14】Best-of-N + Critic Scoring + 拖拽 ScenePlanBoard → v2。改了第 12 节。

Best-of-N 是 N 倍成本换一个你还没能力评判好坏的东西——Critic 的打分质量本身就是个未验证假设。拖拽式 Scene Plan Board 是三周的活，而作者在 Markdown 里插一行 `## 场景 3` 10 秒完成同样的事。`ScenePlan` 的字段（scene_id / goal / cast / loc / must_include / must_exclude / expected_end_state）v1 完整定义并落 artifact，只是由作者手填、由代码顺序执行。生成策略换成 Best-of-N 时，Schema 一行不改。

---

## 3. 核心设计翻转：从「检测」到「预防」

这一节是整份计划的心脏。它同时回答了两位批判者各自的头号 fatal，而且它不是妥协，是一个更好的设计。

### 3.1 问题

胜出方案的 GIF 主角是 `ADDRESS_CONFLICT`（称呼冲突）：作者写下「师兄」，右侧红字「第 143 章两人已断绝师门关系」。它的 rule #2 是 `KNOWLEDGE_VIOLATION`（人物知识越权），号称「规则版能抓 80%」。

**技术评审的 fatal #1 说：这两条都做不了，而且理由是同一个——它们隐含要求「说话人归属 + 受话人识别」，这是未解的 NLP 问题。** 真实中文网文大量对白不带说话人标签（「「师兄。」/ 她抬眼。」），一章里可能有 8 个不同角色被称「师兄」；Aho-Corasick 只做字符串匹配，没有词边界（中文无空格）、没有指代消解。`KNOWLEDGE_VIOLATION` 更狠：「这句台词是否泄露了秘密 S」是语义判定，退化成关键词匹配后，「他忽然想起龙纹的传说」（叙述非台词）误报，「你身上那道疤」（换说法指代 S）漏报。

**这条批判是对的，而且它会在第 8 周（M2 的「真实小说 20 章、误报 < 1 条/章」生死线）以 5–20 条/章 的形态撞墙——那正是方案自己设的不许放行的关卡。项目会按自己的规则判死。**

同时，「真实使用」批判者的 fatal #4 说：**产品前提可能是反的**——日更作者要的是「帮我今天多写 2000 字」，不是「帮我少写错」。一个只会挑刺的工具，你只想关掉它。

### 3.2 解法

**把认知边界从「事后检测」改成「事前面板」。**

```
┌─ 认知边界 · 第 152 章 · 场景 3 ─────────────────────┐
│                                                      │
│  在场角色          血脉秘密        玄铁令下落        │
│  ─────────────────────────────────────────────       │
│  萧决              ✓ 知道 (ch88)   ✓ 知道 (ch120)    │
│  顾清音            ✗ 不知道        ✗ 不知道          │
│  李管家            ⚠ 错误认知      ✗ 不知道          │
│                      (ch103 起：                     │
│                       以为已泄露)                    │
│                                                      │
│  本场景 must_not_reveal：血脉秘密 · 玄铁令下落       │
│  本场景 forbidden_entities：幽泉窟(ch200 首现)       │
└──────────────────────────────────────────────────────┘
```

**这个面板不读正文。** 它读的是场景块里作者声明的 `cast`，去图里做一次集合查询，把结果显示出来，并注入 Writer prompt 的 D 分区。

- 零 LLM
- 零 NLP
- **零误报——因为它对文本不做任何断言。** 它只是在告诉你：你自己在第 88 章告诉过它的事。

### 3.3 为什么这是更好的设计，不是退让

1. **它是唯一没被占的地。** 扫遍商业产品（Sudowrite / NovelAI / Novelcrafter）、中文开源（NovelForge / novel-creator-skill / AI_NovelGenerator / AI-Writer）、学术（2508.03137 / SCORE / 2607.00918），**没有任何一个把「人物认知边界」做成可查询、可强制的一等约束**。所有项目的图都是「客观事实图」（谁和谁是什么关系），没有一个做「主观认知图」。
2. **它是 LLM 靠 scaling 短期解决不了的。** ToM 研究证实这是结构性弱点：GPT-4 在信息不对称对话（FANToM）上的跨题型一致性得分 26.6%（CoT）/ 8.2%（无 CoT），人类 87.5%；而推理模型（o3/R1）在 ToM 上并不稳定优于非推理模型，高阶任务上反而更差。**这封死了「等模型变强就好了」这条退路——它是 state tracking 问题，不是文笔问题。**
3. **预防的价值高于事后检测。** 作者忘的不是「我写错了」，是「他这章还不知道」。在他敲第一个字之前告诉他，比在他写完 3000 字之后红字质疑他，价值高一个量级，破坏力低一个量级。
4. **它绕开了 FANToM 里模型失败的那种题型。** 这里有一个决定性的结构区分：**「列出所有知道 X 的人」是 list 型问题（模型失败的那种），「这句台词是否泄露了 X」是 binary/choice 型问题（模型相对能做的那种）**。而我的面板连 choice 都不是——它是数据库查询。抽取是开放式枚举，校验是封闭式判定，**声明+查询是零判定**。原文档把这三件事混为一谈了。
5. **它回答了「产品前提反了」这条批判的一半。** 面板不是挑刺，是记忆外挂。它不说「你错了」，它说「这是你告诉过我的」。而另一半——「帮我多写字」——由 M2 的起草功能回答，而且起草的 prompt 里装的正是这个面板。

### 3.4 声明成本：为什么这次不是 130 小时

| 对象 | 量级 | 来源 | 成本 |
|---|---|---|---|
| `Secret` 节点 | **5–50 个/本** | 作者声明 | 20 分钟 |
| `KNOWS(角色, 秘密, since_chapter, evidence)` | 每个秘密 2–8 个持有者 | 作者声明 | 10 分钟 |
| `BELIEVES(角色, 秘密, believed_value)` | 稀少，0–5 个/本 | 作者声明 | 5 分钟 |
| 别名角色册 | 50–200 个实体 | **自动抽 + 作者聚类确认** | 30 分钟 |
| 场景 `cast` | 每场景 2–5 人 | mention 自动建议 + 作者点确认 | 5 秒/场景 |

**一本 200 万字的小说，全部前置声明成本 ≈ 1 小时。对比 130 小时。这个差距不是优化，是生死线。**

关键在于：**秘密不是「事实」，是「作者的意图」**。伏笔由作者意图定义、不由文本特征定义——墙上挂了一把枪，它是不是伏笔，取决于作者第 200 章打不打算开枪。**这个信息物理上不存在于已写文本中**，任何抽取器都是在猜。原文档把 Foreshadow / Secret 列为必须自动抽取项是一个范畴错误。而反过来说：作者手填「第 47 章埋了 X，计划第 200 章回收」+ 到期提醒，是 20 行代码、零抽取风险、90% 的价值。

### 3.5 那检测还剩什么

**设计铁律（写进 ADR 0005）：v1 的规则只做「集合判断」，不做「语义判断」。任何需要回答「这句话是什么意思」的规则，一律不进 v1。**

这条铁律划完之后，剩下四条：

| # | 规则 | 判定方式 | 需要读正文？ | FP 风险 |
|---|---|---|---|---|
| **R1** | **认知边界面板** | 集合查询（cast × secret） | **否** | **零（不做断言）** |
| **R2** | **FUTURE_LEAK**：正文提到了作者标记为「第 K 章才出现」的实体/秘密，而当前是第 N < K 章 | 唯一专名精确匹配 | 是 | 极低（专名由作者声明，可选距离性） |
| **R3** | **DEAD_SPEAKS**：已死/未登场角色开口说话 | 正则 `(全名)(道\|说道\|冷笑道\|问道\|答道)` × 图上 status | 是 | **零歧义——死人没有对话标签** |
| **R4** | **LOCATION_CONFLICT**：场景块声明的 `loc` 与人物当前所在地冲突 | 作者声明 vs 作者声明 | **否** | 零 |
| ~~R5~~ | ~~ADDRESS_CONFLICT~~ | 仅限显式说话人标签引语内 | 是 | **Day 1 下午实测覆盖率，<10% 当场砍，写进 ADR** |

注意 R1 和 R4 **完全不读正文**，而它们是零 FP 的核心。R2/R3 读正文，但都限定在**高信号位置**：R2 是作者亲自挑的唯一专名，R3 是说话人标签位置。**没有一条需要指代消解、没有一条需要说话人归属推断。**

R3 的形态值得单独说：`萧决道：「……」`，而萧决在第 89 章死了，现在是第 152 章。这不需要任何解释——死人没有对话标签。而「萧决当年……」（别人提到死者）不会触发，因为它不在标签位置。这就是「集合判断 vs 语义判断」这条线的价值。

**R5（称呼冲突）的处置：** 它是最有杀伤力的演示，也是最高 FP 风险的规则。我不拍脑袋决定，我**在 Day 1 下午用 2 小时实测**：拿 3 章真实小说，数一数 `(名字)(道|说道|冷笑道|问道|答道)` 这个模式覆盖了多少个称呼实例。

- 覆盖率 ≥ 10% → R5 进 v1，只在显式说话人标签 + 受话人在场且唯一时开火，其余一律不报。
- 覆盖率 < 10% → 当场砍掉，写进 ADR 0005，M6 的验收标准相应改写。

**2 小时，零依赖，Day 1 就知道答案。** 这是对技术评审 fatal #1 最便宜的正面回应。

---

## 4. 第 22 节 17 项逐条裁决

| # | 文档必做项 | 裁决 | 去向 | 理由 |
|---|---|---|---|---|
| 1 | 小说导入与章节/场景拆分 | **章节做，场景改声明** | — | 章节正则可行（含实测 bug，见 §8）。场景自动识别不准，错了作者要手工修，比手工插还慢。作者写一行 `## 场景 3` 10 秒完成。 |
| 2 | 初始图谱抽取与作者校对 | **改：只做别名角色册 + 聚类确认** | 增量抽取 → M4 | 130 小时墙。见【改 5】。 |
| 3 | Neo4j 时间故事图谱 | **图 100% 做，Neo4j → SQLite** | Neo4j → 触发条件制 | 见【改 1】。**这里我改了你的决定。** |
| 4 | 人物认知图 | **做，而且提到第一位；形态改声明+面板** | — | 见 §3。**这里我改了你的形态。** |
| 5 | Qdrant Dense+Sparse 混合检索 | **推迟** | v1.1（选型已定） | 见【改 2】。**这里我改了你的决定。** |
| 6 | Graph-guided Retrieval | **做**（图 → 上下文分区） | — | v1 的「retrieval」是确定性查询，不是 ANN。10/10 分区交付。 |
| 7 | Retrieval Manifest | **做，降级** | — | JSON artifact + 面板上的「本次给模型看了什么：人物卡 820 字 / 上一场景 800 字 / 约束 120 字」。20 行 vs 1 周，且比 JSON 更有用。不做 PG 表。 |
| 8 | 章节准备与场景规划 | **部分做** | AI 规划 / Best-of-N / 拖拽 Board → v2 | ChapterBrief 作者手填 YAML，场景块手插。见【改 14】。 |
| 9 | 逐场景生成 | **做，而且提前到 M2** | — | 它是唯一的「多写字」能力，也是唯一能证伪项目假设的实验。 |
| 10 | 硬规则 + LLM Validator | **硬规则做（重新定义 4 条）；LLM Validator 砍** | LLM Validator → v2 | 见 §3.5 和【改 12】。 |
| 11 | 局部 Patch | **砍** | v1.1 | 见【改 13】。 |
| 12 | TipTap 编辑器 | **砍** | v1.1 | 见【改 3】。**这里我改了你的决定，也改了胜出方案。** |
| 13 | 局部关系图与全屏图谱 | **局部图做（hops ≤ 2 硬上限）；全屏砍** | 全屏 → v2 | 见【改 10】。用你自己第 10 轮的原则砍的。 |
| 14 | 作者确认式 Canon Commit | **做，改 exception-driven** | — | 语义 100% 保留，粒度从「每条事实」改成「每次冲突」。见 §5.4。 |
| 15 | Qdrant 水位线 | **退化为一个整数 + 响应字段** | — | `indexed_version`，v1 恒为 0。字段免费，历史补不回来。 |
| 16 | Harness 运行追踪 | **数据做，页面砍** | Run Inspector 页 → v2 | `model_call` 表如实写满 + `nh run show` + 面板角标。可观测性的价值在「数据被记录了」，不在「数据被画出来了」。 |
| 17 | Docker Compose 一键运行 | **被替换** | — | `uvx novel-harness`。见【改 11】。 |

**永不做（不是推迟）：**

- `DOES_NOT_KNOW` / `PARTIALLY_KNOWS` 边（组合爆炸 / 欠定义，见【改 9】）
- `style_dense` / `interaction_dense` 命名向量（伪信号：中文没有现成文风 embedding 模型，你只能拿同一个模型编码同一段文本 = 把 raw_dense 存两遍；这类结构化信号本就该走 payload filter）
- 事件因果自动抽取（`CAUSES` / `RESULTS_IN` / `PRECEDES`）：Conan 关系 F1 0.276，因果只会更低；而且写作时没有任何 Validator 规则依赖它——纯成本，零收益
- 伏笔**自动抽取**（范畴错误，见 §3.4；伏笔**登记表** + 到期提醒做，20 行代码 90% 价值）
- 扁平 Proposal 队列（它是错误心智模型的 UI 化身；确认必须是对聚类的）
- LangGraph 适配层（「可选适配器」仍要求你设计一层双向映射抽象并永久维护它，而 v1 没有任何 capability 复杂到需要子图——Task Parser / Writer / Extractor 全是单次 LLM 调用 + Pydantic 解析。为一个不存在的需求维护抽象层是纯负债。）

---

## 5. 技术栈最终裁决

### 5.1 一张表

| 层 | 选择 | 被否决的选项 |
|---|---|---|
| 分发 | `uvx novel-harness`（PyPI + hatch build hook 打前端） | Docker Compose（6 容器 / 2–5GB）；Windows exe（见 §6 F1） |
| Python | 3.12（`.python-version` + `uv python pin`） | 3.14（实测核心栈能装通，但 3.12 对长尾依赖是零成本的尾部风险消除；不为新而新） |
| 存储 | **SQLite 单文件**（WAL + foreign_keys），`node`/`edge` 二表建图 | Neo4j + PostgreSQL + Artifact Store 三件套 |
| 全文检索 | **不做**。mention 索引即检索 | FTS5（unicode61 对中文不分词，整句塌成一两个 token——跟 Qdrant BM25 是同一个坑换马甲，见 §6 S11） |
| 向量 | **不做**（v1.1：fastembed + bge-small-zh-v1.5 + sqlite-vec） | Qdrant + BGE-M3（2.2GB + torch，杀死 uvx 叙事） |
| 提及匹配 | **编译一次的正则 alternation**（按长度降序排列，leftmost-first 即最长匹配） | pyahocorasick（C 扩展 = 平台 wheel 风险；200 别名 × 3000 字是微秒级，AC 是过早优化） |
| 分词 | jieba + 用户词典（全部人名/别名 `add_word(name, freq=10000)`） | 默认分词（把「顾清音」切成「顾/清音」） |
| 后端 | FastAPI + Pydantic v2 | — |
| 前端 | Vite + React，**只读面板，无编辑器** | TipTap / ProseMirror（见【改 3】）；Cytoscape 全屏图（见【改 10】） |
| 局部图 | React Flow，**hops ≤ 2 硬编码** | 递归 CTE（有 bug 且没必要，见 §5.5）；3 跳（实测=全书） |
| LLM | `def complete(messages, *, schema) -> Any` 一个函数 | Provider 全家桶（接口存在就够了；多接一家是接口定好后的一个下午） |
| ID | ULID：`{type}:{project_short}:{ulid}` | slug（`character:gu-qingyin` 在别名合并那天全线断链，见 §5.3） |

### 5.2 Provider 与模型（由修正案 5 / ADR 0011 取代原 Claude-native 假设）

唯一 SDK 边界是 OpenAI Python client。`base_url`、`model`、`api_key` 选择 OpenAI、DeepSeek、
Claude-compatible、OpenRouter 或本地兼容端点；OpenRouter 只是可选 endpoint，不是产品依赖，
仓库不改回单供应商 SDK。

作者选择的是人类长度（首批只支持 `zh` characters / `en` words），不是 `max_tokens`。
内部 planner 按所选 endpoint/model 的**显式能力**生成 frozen call plan：operator override → 精确
registry → endpoint metadata → unknown。不能从模型名前缀或大 context window 猜 output 上限、
reasoning 字段或 streaming 支持；能力不足就预检失败，不静默 clamp。

产品 reasoning 默认 off；M2 固定 provider-neutral high，由 adapter 映射为该兼容 route 支持的
wire shape。M2 三臂仍必须同 endpoint、同 model、同 frozen plan。可见预算使用版本化公式
`ceil(max_units * 2.0) + 1024`，reasoning 与 completion 共池时另留 capability 声明的比例；
请求预算超过 16,000 只在明确支持时走 streaming。实际 endpoint/model/capability/request budget
在首个推理前写入不含 key 的 profile 并单独提交。

**纯本地模式开关（几乎免费，但它是 §6 S12 隐私问题的唯一解）：** `complete()` 在本地模式下抛 `LocalOnlyMode`。面板（R1/R4）和规则（R2/R3/R5）本来就是纯函数、本来就不调它，所以关掉后工具**依然完整可用**，只是不能自动抽取和起草。

### 5.3 ID：ULID，Day 2 就定死

```python
NH_UUID_NAMESPACE = uuid.UUID("...")  # 改这个常量 = 全量索引作废。配 10 个 golden 值测试钉死。

def new_id(t: EntityType, project_id: str) -> str:
    return f"{t}:{_short(project_id)}:{ulid.ULID()}"
```

`slug` / 人名 / `chapter_number` 一律降为**可变属性**。唯一例外是内容寻址的 `artifact:sha256:{hex}`。

**为什么这条不能等：** 胜出方案的 must_have 明写用 `character:gu-qingyin` 并称之为「未来迁 Neo4j 的唯一保险」——**这条保险是假的，而且它恰好在这个方案最核心的场景上炸**。M4 的增量抽取必然产出同一人物的多个候选（顾清音/清音/顾姑娘），作者一合并，slug 变了，全部 edge / evidence / alias 引用断链。**把别名当一等公民（提及匹配是整个产品的地基），却用别名派生的 slug 当主键——这是自相矛盾。** Day 2 改是零成本，M4 改是重写数据层。

选 ULID 而非 UUIDv4：单调可排序（时间前缀便于调试、便于按创建顺序扫描）。`project_short` 前缀是为了防跨项目误引用在日志里一眼可见。

### 5.4 三层图谱 + exception-driven 确认

```
CANON       ← 作者确认过。可开火（规则用它报错）、可断言为真（进 prompt）
PROVISIONAL ← 抽取的、带证据、未确认。只喂检索和面板灰显，永不开火、永不断言为真
PLANNED     ← 未来，作者声明。永不进 Writer prompt，只转译为 must_not_reveal / forbidden_entities
REJECTED    ← 作者否决。保留以防重抽
```

`CURRENT` **推导，不存储**：`CURRENT = valid_to_chapter IS NULL AND information_scope='CANON'`。**这不是节约，是正确性**：文档把 CURRENT 和 CANON 并列存储，会制造一个文档没解决的同步问题——一条边被 supersede 时谁负责把 CURRENT 摘掉？推导比存储更正确，且少一类可能的数据不一致。这个坑会在 M4（增量抽取开始批量产生 supersede）爆炸，而它的表现形式恰好是这个产品最怕的东西：误报。

`OPTIONAL` **永久删除**——v1 没有任何消费者。`DRAFT` ≡ `PROVISIONAL`。

**确认从 opt-out 改成 exception-driven（这是「真实使用」批判者 fatal #3 的解法，也是这条改动的全部价值）：**

高置信度 + 与现有 CANON 无冲突的抽取结果 → **默认自动落 PROVISIONAL，作者一次都不用点。** 只有两类弹到他面前：

1. 新事实与已确认 CANON **直接冲突**（「图上写着他在洛阳，这章他在北荒，哪个对？」）
2. 抽取器自己置信度 < 0.7 **且**涉及主要人物

**这两类在每章的真实量级是 0–2 条，30 秒解决。** 对比原设计的 30 条 × 30 秒 = 15 分钟/章 × 日更 2 章 = 每天 15–30 分钟的永久税——他码 4000 字大概 2–3 小时，这是 10–15% 的时间税，而且他算账时不会算「我少挨了 3 条差评」，他会算「我少写了 500 字」。

**这实质修改了原则 5「作者确认后才能 Commit」——但原则 5 的本意是「Agent 不得污染 Canon」，PROVISIONAL 层已经完整保住了这个本意（它根本不是 Canon）。原则没破，只是把「确认」的粒度从「每条事实」改成「每次冲突」。这一条不改，前面所有工作都白做。**

### 5.5 edge schema：互斥性元数据 + supersede 收敛点

> **实施订正（2026-07-16，M0 落地后）。本节写于代码之前，有三处措辞已被实现推翻，以代码和 ADR 为准：**
>
> 1. **`HAS_STATE` 的「(人, 状态维度) 单值」是本节承诺、但本节的机制实现不了的。** `exclusivity` 按 `(src, dst 节点 id)` 算，而维度键是 `dst.props.dim_key`——两个 StateDim 节点共享一个 `dim_key` 时，`state_at` 会同时返回 `health=dead` 和 `health=alive`，R3 全书误报。已补两道：唯一索引（进不来）+ 按 `dim_key` 分组的 raise。**这条注释本身正是让人不去查这个 case 的原因。**
> 2. **`state_at` 的 `WHERE src = :node` 假设了所有边都有向。** `RELATED_TO` 已定为无向（[ADR 0008](adr/0008-related-to-is-undirected.md)）——对称关系两侧各声明一次会制造两个互斥的 CANON，且不需要任何脏数据。读侧对无向类型放开 `dst`。
> 3. **「`graph/` 之外任何文件 `import sqlite3` 直接失败」这条判据拦不住它自己想拦的东西。** `from ..db import connect` 一行就绕过去了，`import sqlite3` 一次都不用出现。守卫的判据已改成「谁在碰图表」（AST 扫 SQL 字面量 + 扫 `connect` 的 import），并把绕法当 fixture 喂进扫描器测。

技术评审的 serious #4 是一个真缺口：CURRENT 改推导之后，`valid_to` **谁来写**？增量抽取产生「顾清音 LOCATED_AT 北荒 valid_from=151」时，必须同时闭合旧边。而「哪些旧边该被挤掉」取决于 edge type 的**基数语义**——`LOCATED_AT` 是 src 单值、`RELATED_TO` 是 (src,dst) 单值、`OWNS` 可以多条同时有效。而 StoryGraph 的五方法接口里**没有任何地方承载这个语义**。

```sql
CREATE TABLE edge_type (
  type TEXT PRIMARY KEY,
  exclusivity TEXT NOT NULL CHECK (exclusivity IN ('single_per_src','single_per_src_dst','multi'))
);
INSERT INTO edge_type VALUES
  ('LOCATED_AT',  'single_per_src'),      -- 一个人同一时刻只能在一个地方
  ('HAS_STATE',   'single_per_src_dst'),  -- (人, 状态维度) 单值
  ('RELATED_TO',  'single_per_src_dst'),  -- (A,B) 关系阶段单值
  ('KNOWS',       'single_per_src_dst'),  -- (人, 秘密) 单值
  ('BELIEVES',    'single_per_src_dst'),
  ('MEMBER_OF',   'multi'),
  ('OWNS',        'multi'),
  ('PLANTED_IN',  'multi'),
  ('RESOLVED_IN', 'multi');
```

`upsert_edge()` 里一处实现 supersede：按 `exclusivity` 决定 UPDATE 哪些旧边的 `valid_to_chapter`。十几行，但**必须在第一条边写进库之前就存在**，否则历史数据全是脏的，而 `state_at` 会同时返回「在青云城」和「在北荒」两条有效边 → 规则误报 → 生死线指标崩。

**`state_at` 的唯一实现：**

> ⚠️ **下面这一段 2026-09-06 起是历史**（[ADR 0043](adr/0043-facts-store-a-start-not-an-interval.md)）。
> `valid_to_chapter` 停用了（恒 NULL），排他类型改成「同一语义槽里取 `valid_from`
> 不晚于本章的最后一条」（`graph/queries.py::CURRENT_EDGE_CTE`），其余四个条件不变。
> **原文原样留着**，因为下面那句「边界必须单测」的道理一个字没变——只是今天那条边界
> 由两条事实（ch10 一条、ch143 一条）表达，不由一条边的两个端点表达。
> 权威见 [ARCHITECTURE.md 的数据模型那一节](ARCHITECTURE.md)。

```sql
SELECT * FROM edge
WHERE project_id = :pid AND src = :node
  AND valid_from_chapter <= :ch
  AND (valid_to_chapter IS NULL OR valid_to_chapter > :ch)
  AND information_scope = 'CANON'
  AND status = 'ACTIVE'
  AND evidence_status != 'STALE'
```

边界必须单测：`valid_from=10, valid_to=143` → ch9 不命中、ch10 命中、ch142 命中、**ch143 不命中**、ch150 不命中。这是整个时态模型最容易错的地方。

**架构守卫（比「禁止裸 SQL 的 grep」更硬）：** 所有时态过滤只在 `graph/queries.py` 实现一次。CI 检查：`graph/` 之外任何文件 `import sqlite3` 直接失败——它连 import 都拦，而不是靠纪律。StoryGraph 的所有出参是 **Pydantic 模型**（`Node` / `Edge` / `StateSnapshot`），`props` 在 repository 层就 `json.loads` 成 typed field，`dict` 和 `sqlite3.Row` 禁止越过接口——否则 4 条规则和人物卡渲染全都在解 SQLite 的 JSON 列，换实现时全部重写。

**局部图：删掉递归 CTE。** hops ≤ 2 写两层显式 JOIN 更快、更可控、更好读，且不会像 `UNION` 版那样把同一节点在 hop=1 和 hop=2 各返回一行。2 跳必须带强类型过滤（只展开 `RELATED_TO`/`KNOWS`，不展开 `APPEARS_IN` 这种星形高度数边），否则一定糊。done_when 改成「2 跳 + 类型过滤后 ≤30 节点，超过则折叠成聚合节点」。

### 5.6 Evidence 双指针 + STALE：闭嘴，不是提问

```
审计指针：  (chapter_snapshot_id, para_index, quote_text, quote_sha256)  → 指向不可变快照，永不失效
重定位指针：(chapter_id, quote_sha256, para_index_hint, occurrence_k)   → 在当前正文里定位
```

原文档只给了 `evidence_artifact_id + source_start_offset + source_end_offset`。这是个隐藏炸弹：Artifact 不可变是对的，但作者改了第 143 章后，旧 evidence 指向的是**历史版本**的原文——审计上正确，UI 上却无法在当前正文里定位，而作者会以为系统在骗他。更糟的是那条 Fact 的依据可能已经被删掉了，系统却静默地继续把它当 CANON 喂给 Writer。

**`revalidate`：** 正文变更 → 在新快照里找不到 `quote_sha256` 的 evidence → 其关联 edge 标 `evidence_status='STALE'`。

**关键：STALE 的边不进审阅队列。** 它只做两件事：

1. **立刻停火**（规则不再拿它报错——这就完成了它 90% 的价值，即防止「依据没了还在质疑作者」这个最伤的误报）
2. 人物卡上一个灰色小点「依据已变更」

只有当作者主动点开那个人物卡、或者恰好有一条 issue 需要引用这条边时，才提示一句「这条要不要重新确认？」。**永远不要主动推一个队列给他。**

> **原则：系统发现自己不确定时的默认动作是闭嘴，不是提问。**

（这条来自「真实使用」批判者的 serious #9，我原样采纳。嫁接建议里的 revalidate 设计意图是保护「误报 <1 条/章」，但如果它把 47 条 STALE 推成一个早晨的审核墙，它就制造了一个新的、周期性的、作者没法拒绝的工作量——正好是它想防的东西。）

**这条洞察还有一层：文档「Evidence 是一级对象」的直觉是对的，但它对的原因不是审计和可追溯，是误报缓释。这是个产品理由，文档把它当成了工程理由。**

### 5.7 决策日志（append-only，两天，买 schema 自由）

```sql
CREATE TABLE decision_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,          -- alias_merge | secret_declare | knows_declare | proposal_review | ...
  subject_name TEXT,           -- 人名，不是 ID
  quote_text TEXT,             -- 文本引语，不是 offset
  quote_sha256 TEXT,
  chapter_number INTEGER,
  para_index INTEGER,
  payload_json TEXT NOT NULL,
  decision TEXT NOT NULL,      -- accept | reject | edit
  actor TEXT NOT NULL DEFAULT 'author'
);
```

**只增不改，永不迁移，用文本引语而非 ID/offset 做锚。**

这是地基优先方案自己在 honest_weakness 里承认的「唯一一条硬腿」——它把 18 周方案里唯一真正不可重建的资产用两天保护掉了。它的原话值得引用：

> 「真正不可重建的只有一项：作者已经点过的 3000 次确认。而保护这一项，其实有一个远比整套方案便宜的办法——把每次 approve 落成一条 append-only 日志，用文本引语而非 ID/offset 做锚。那是大约两天的工作量，不是十八周。」

对这个计划的意义更大：v1 迟早要换存储（SQLite → Neo4j）、要改 edge schema、要重跑抽取。**有这张日志，作者点过的每一次确认都能重放回新 schema；没有它，任何一次 schema 变更都要作者重新点一遍全部确认，而那一刻就是项目结束的时刻。**

而且它还有一个开源专属价值：v1 → v2 的架构大改是必然的，如果每次大改都要求早期用户重新审核一遍图谱，你会在 v2 发布当天流失掉全部早期用户——**而这些人恰恰是唯一会给你写 issue 和 PR 的人。**

### 5.8 图 schema：8 类节点 / 9 类关系

**节点（8）：** `Character` / `Location` / `Faction` / `Secret` / `Foreshadow` / `Object` / `StateDim` / `Chapter`
**关系（9）：** `LOCATED_AT` / `MEMBER_OF` / `RELATED_TO` / `KNOWS` / `BELIEVES` / `HAS_STATE` / `OWNS` / `PLANTED_IN` / `RESOLVED_IN`
（别名是 `alias` 表，不是边——它是索引不是事实）

从 17/20 缩到 8/9。**规则（写进 ADR 0005）：只有当某条面板分区或某条规则真的要查它时，才允许加一个节点/边类型。** 这把 schema 设计从猜测变成需求驱动。`Volume` / `Scene` / `Skill` / `Event` / `Fact` / `State` / `RelationshipState` / `EvidenceRef` 在 v1 没有任何消费者——没有一条规则、没有一个面板分区会读它们。

`HAS_STATE` 的 dst 是 `StateDim` 节点（修为/身份/健康），props 存 value，exclusivity = `single_per_src_dst` = (人, 维度) 单值。这样基数语义干净。

**别名表的诚实处理：**

```sql
CREATE TABLE alias (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  surface TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('canonical','alias','nickname','title')),
  usable_for_rules INTEGER NOT NULL DEFAULT 1,
  UNIQUE(project_id, node_id, surface)
);
-- 一个 surface 映射到 >1 个 node_id 时（「师兄」），标记 ambiguous，规则不使用
-- 2 字以下别名 UI 层直接拒绝录入（「音」「决」是灾难）
```

**注意别名不做实体消解——这跟 GraphRAG 是反的，而且是故意的。** GraphRAG 按名字做实体消解，会把「顾姑娘/清音/魔尊」合并掉——**而这些别名差异恰恰编码了关系阶段和认知边界（化名 = 别人不知道他是谁 = 认知图的边）。它们是 canon，不是噪声。GraphRAG 的 entity resolution 会正好摧毁这个项目最有价值的信号。**

### 5.9 valid_from：作者永不填章号，一次都不行

「真实使用」批判者的 serious #8 完全正确。手填人物卡时问他「这条关系从第几章开始有效」——他不记得。200 万字写了三年，他连主角哪章突破金丹都要翻。他会填 1，或者随便填，或者放弃填。**结果：图谱里全是 `valid_from=1` 的边，时间态模型退化成当前值快照图——跟 NovelForge 一模一样，而时间态是这个项目全部差异化的地基。**

**修法：`valid_from` 只由证据自动决定。** 作者确认的是「这条事实在第 143 章的这段原文里出现过」（他看着原文点确认，零记忆负担），系统自己把 `valid_from=143`。`valid_to` 只在新证据 supersede 时自动闭合。

**手填表单里根本不该有章号输入框——有它就等于邀请污染。**

---

## 6. 对每一条 fatal / serious 的正面回应

### 「真实使用」批判者（日更 4000 字、用 WPS、不懂技术的作者）

| # | 级别 | 批判 | 回应 |
|---|---|---|---|
| 1 | fatal | `uvx` 对目标用户等于不存在（95% Windows + WPS，没装过 Python） | **接受方案 (A)，明确画像。** v1 的用户是「写中文长篇、正文用 Markdown、会开终端的作者」。**这句话写进 README 第一行和所有里程碑的 done_when。** 这是诚实且完全正当的选择——早期开源项目本来就该服务技术型早期用户。方案 (B)（Windows 签名 exe，2–3 周 + 每次发版的 Windows CI）不选，因为它解决了装机也解决不了第 2 条和第 4 条。 |
| 2 | fatal | 要求日更作者把码字主场从 WPS 换到陌生编辑器 = 断更风险 = 掉推荐位 | **修：v1 没有内置编辑器。** 正文在磁盘上，你继续用你自己的编辑器。见【改 3】。这条批判独立地印证了技术评审的 fatal #2（offset 地狱），两条批判指向同一刀。 |
| 3 | fatal | 确认是纯税（15–30 分钟/天，永久的） | **修：exception-driven + 三层图谱**（每章 0–2 条弹窗）。**加上：认知边界是作者声明的，不是抽取的**——它根本不产生 proposal。见 §5.4。 |
| 4 | fatal | 产品前提反了：作者要「多写字」不要「少写错」，而起草排在第 16 周 | **修：起草提到 M2（第 9 周），紧跟图谱层，排在规则检测和局部图之前。** 理由不是「用户想要」，是**它是唯一能回答「这个项目的假设成不成立」的实验**。同时把面板从「挑刺」重构成「记忆外挂」（§3.3 第 5 点）。**但我也在风险 1 里诚实承认：这条我无法用工程手段消除。** |
| 5 | serious | 冷启动墙（真书 60+ 人物 / 200+ 别名，设定在作者脑子里） | **修：** ① 秘密量级 5–50 条不是 5000 条（§3.4）；② 角色册自动抽 + 聚类确认（PER F1 76.60，归并是人工最省力处）；③ cast 由 mention 自动建议；④ **价值曲线从 t=0**：手填一个秘密 + 一条 KNOWS，面板就开始工作。⑤ M1 的 done_when 改成真书数字（见 §7）。 |
| 6 | fatal | ADDRESS_CONFLICT 需要对话归属，会满屏误报 | **修：降级为 R5，Day 1 下午 2 小时实测覆盖率，<10% 当场砍并写进 ADR。** 同时**换掉 rule #1**：头牌改成认知边界面板（零 NLP、零 FP）。见 §3.5。 |
| 7 | fatal | M2 只卡误报没卡真阳性——「什么都不报」能 100% 通过 | **修：双边门槛 + 合成小册子。** 「误报 <1 条/章 **且** 真阳性 ≥1 次/10 章」。后者在真书上无法测（没有 ground truth），所以它**强制**我嫁接合成小册子——`replay()` 出 ground truth，改完规则回车 5 秒出误报数**和漏报数**。这不是锦上添花，它是唯一能让 M2 的门槛不是自欺的东西。**沉默的工具死得比吵闹的工具更快，只是死得更安静，而且你的指标不会告诉你它死了。** |
| 8 | serious | `valid_from_chapter` 依赖作者不掌握的信息 | **修：作者永不填章号，一次都不行。** valid_from 由证据决定。见 §5.9。 |
| 9 | serious | EVIDENCE_STALE 把静默错误变成响亮的工作量 | **修：STALE 不进队列，只停火 + 灰点。「系统不确定时的默认动作是闭嘴，不是提问。」** 见 §5.6。 |
| 10 | serious | Aho-Corasick 短别名误命中（「清音袅袅」「决定」） | **修（四件事一起做）：** ① 换正则 alternation，去掉 C 扩展；② 最短 2 字，单字别名 UI 层拒绝；③ jieba 用户词典灌人名；④ **最重要的：核心机制不读正文**——R1/R4 由作者声明的 cast/loc 驱动，R2/R3 限定在高信号位置。见 §3.5。 |
| 11 | serious | FTS5 对中文默认不分词（同 Qdrant BM25 的坑换马甲） | **修：v1 没有 FTS5。** mention 索引即检索——它给你「顾清音的全部出场按章排序」，这是作者 90% 的真实需求，而且是精确的不是 BM25 的。这个静默失效整个消失。 |
| 12 | serious | 数据隐私：未发表稿子整章传给 API（盗文是这个圈子的头号焦虑） | **修（三件事，几乎免费）：** ① README 第三行说清哪些数据出本机、出多少、发给谁、能不能关；② 支持自定义 API base（三行代码，大量中文用户走中转/ollama）；③ **纯本地模式开关**——关掉后零 LLM、零网络，面板 + 规则全部本地跑，工具依然可用。它顺带让 tagline 更硬：**你的稿子不出你的电脑。** 这几乎免费，因为面板和规则本来就是纯函数。 |
| 13 | serious | 18 周是全职估算 | **修：16 周全职 / 26–30 周业余，明说，见 §7。** 并把 M6 的验收从「找一位陌生作者」改成「**你自己用它连续写完 10 章且一次都没关掉面板**」——100% 可判定，零不可控变量。 |
| 14 | serious | slug ID 在别名合并那天断链 | **修：ULID，Day 2。** 见 §5.3。**外加 append-only 决策日志（两天）**，见 §5.7。 |
| 15 | minor | 作者连写 20 章才同步 → 增量退化成批量 | 采纳第 3 条后基本自动消解（200 条自动进 PROVISIONAL，只有 0–5 条冲突弹出）。仍要做：**抽取后台批跑 + 进度条，绝不阻塞面板，绝不弹模态框。** |

### 技术可行性批判者

| # | 级别 | 批判 | 回应 |
|---|---|---|---|
| 1 | fatal | ADDRESS_CONFLICT / KNOWLEDGE_VIOLATION 都需要说话人归属，做不了 | **完全接受，而且这条批判重塑了整个计划。** KNOWLEDGE → 面板（零 NLP、零 FP、预防>检测）；ADDRESS → R5 + Day 1 覆盖率实测。**它提的修法（右侧面板列「本场景在场角色中谁不知道 S」）比原方案更好，我原样采纳并把它提到了 README 第一行。** 见 §3。 |
| 2 | fatal | offset ≠ ProseMirror pos（三重错位：node 边界 +41、UTF-16 vs code point、快照漂移） | **整个消失：v1 没有 TipTap。** 见【改 3】。且**后端永不发 offset**——Issue/Evidence 一律用 `(para_index, quote_text, occurrence_k)` 三元组。这个契约 Day 2 就定死。 |
| 3 | serious | 200ms 实时链路做不到；chapter_version 每 800ms 插一行 = GB 级 | **修：拆两条路径。** ① 面板由场景元数据驱动，**不读正文**，本机 HTTP 2–5ms，瞬时；② 规则 debounce 2s 后端跑（「停手 2 秒后闪红」，对一个「检查」功能完全够，它不是自动补全）。**这样避开了「TS 重写一遍规则」的重复实现**——判分器和 Validator 保持同一份 Python 代码。③ 快照：debounce 只 UPDATE 文本，`chapter_snapshot` 仅在显式保存/切章/关闭时插，且插之前比 `text_sha256` 去重。 |
| 4 | serious | edge 互斥性元数据不存在 → supersede 无处可写 → state_at 返回两条互斥边 → 误报 | **修：`edge_type` 表 + `exclusivity`，Day 2，在第一条边写进库之前。** 见 §5.5。 |
| 5 | serious | kill-gate 挂在真书 + 语义规则上测不出任何东西 | **修：挂在合成书 + 零噪声判分上。** 见 §7 M2。**并采纳它的第三臂建议**（X2：约束改写成叙事性提示），把「图没用」和「约束注入形态错了」分开——后者的解法在生成侧不在数据侧。 |
| 6 | serious | LLM 抽取的 evidence quote 对不上原文（10–30%），quote_hash 锚失效 | **修：** ① Prompt 硬性要求逐字引用、长度 10–40 字；② 后端用 `difflib.SequenceMatcher` 模糊定位，`ratio < 0.9` **直接丢弃、不入队列**（宁可漏）；③ **`quote_sha256` 用定位成功后的原文子串算，不是用 LLM 返回的字符串算**。 |
| 7 | minor | uvx 打包链路完全没排（React dist 进 wheel、C 扩展 wheel、PyPI 发版 CI） | **修：Day 1 下午（2h）建 GitHub Actions + PyPI trusted publishing，发 0.0.1 空包，在另一台机器（或 linux 容器）验证 `uvx novel-harness --version`。** 前端产物走 hatch build hook（打包时跑 npm build），**不 commit dist**——commit dist 会让每个 PR 的 diff 变成噪声，直接毁掉贡献者体验，而贡献者是这个方案的核心赌注之一。C 扩展风险由「正则 alternation 替代 pyahocorasick」消除。 |
| 8 | minor | 递归 CTE 有 bug 且过度设计 | **修：删掉，写两层显式 JOIN + 强类型过滤。** 见 §5.5。 |
| 9 | minor | StoryGraph Protocol 是自我安慰（唯一实现 = 接口静默泄漏） | **修：Pydantic 出参 + CI 禁 `graph/` 外 import sqlite3。** 见 §5.5。诚实说明：这**缓解**不**消除**——接口总会泄漏实现，尤其当唯一实现和它长得一模一样时。真正的保险是 §5.7 的决策日志，它让 schema 可以随便改。 |
| 10 | minor | 合成 demo 书制造立刻被打破的预期（demo 0 误报，真书 5 条/章） | **消失，而且是设计的直接推论：v1 的面板由声明驱动，不由抽取驱动，所以 demo 的质量 = 真实的质量。** 预期打破只存在于 M4 的增量抽取，那里 demo 展示真实抽取质量。**并接受 M0 = 3 周不是 2 周**（合成书是 5–8 天不是 3–4 天，别在计划里假装）。 |
| 11 | minor | 消掉双写一致性是一次性的、不可积累的；加回 Qdrant 时要在 5000 行假设了单事务的代码里追加 staleness | **修：现在就留字段。** `chapter.indexed_version INTEGER DEFAULT 0`，响应结构带 `{canon_version, indexed_version, staleness}`，v1 恒为 0。字段免费，语义免费，历史补不回来。**并当面谈砍 Neo4j/Qdrant 这件事**（就是本文档的第 2 节），因为它顶了你明确表达过的决定——这不是技术问题，但它会以「作者不想做下去」的形式变成项目风险。 |

---

## 7. 里程碑路线图（单人全职估算）

**业余系数：1.6–1.9x。** 如果你自己就是那个日更 4000 字的作者（这是最好的情况——自己是自己的用户），每天开发时间是码字之后的 1–2 小时，16 周 → **26–30 周**。这个数字我明写，不藏。

| M | 内容 | 全职 | 累计 | 完成标准（客观可判定） |
|---|---|---|---|---|
| **M0** | 骨架 + 打包链路 + 数据层 | **2 周** | 2 | ① 一个陌生人在另一台机器执行 `uvx novel-harness` 能跑起来（不是 `uv run`——Day 1 就验证过打包路径）；② 导入一本真实的 200+ 章 TXT，切出章数与该书目录数**完全一致**；③ `pytest` 全绿，含时态闭开区间边界、supersede 互斥、ULID golden 值、架构守卫四组；④ `scripts/demo.sh` 绿 |
| **M1** | 声明层 + 认知边界面板 ← **首个可发布物** | **3 周** | **5** | **在你自己 200+ 章的真书上**：① 30 分钟内声明完 10 个秘密 + 持有者集合，**全程没有输入过一次章号**；② 角色册自动抽 + 聚类确认在 30 分钟内覆盖全书 **90% 的人物提及**；③ 在第 151 章的 3 个不同场景上，面板给出的认知矩阵**逐格核对全对**；④ 面板对 cast 变更的响应 < 200ms |
| **M2** | 合成小册子 + 起草 + **kill-gate** ← **最早的证伪点** | **4 周** | **9** | ① 合成小册子：12 章 / 6 人物 / **25 个植入的认知陷阱**，`replay()` 出 ground truth，`leak_selfcheck` 12 章全过，专名两两互不为子串；② 起草：给一行场景目标 + cast，最终生成 2,000–3,000 个非空白中文 code point；③ **kill-gate 跑完**：X0/X1/X2 × 25 陷阱 × 3 次 = 225 个 final cell，每份不足 2,000 时最多续写一次，因此成功轮为 225–450 次 transport call，McNemar 出 p 值；④ 协议正文及修正案 1–5 的 git 时间戳**早于**第一个 `runs/*.jsonl` |
| **M3** | 规则检测（R2/R3/R4[/R5]） | **2 周** | 11 | **双边门槛**：真书连续 20 章，**误报 < 1 条/章**（人工判定，⚠️ **定义至今未预注册**——2026-07-27 核实：全仓没有任何地方定义「什么算误报」，`EVAL_PROTOCOL.md` 只冻了 M2，M3 这条生死线现在是裸奔的；本文 §12 自己说这是「整份计划里最便宜的一条纪律」）**且**合成小册子上真阳性 ≥ 22/25。任一不达标不许进 M4 |
| **M4** | 增量抽取 + 三层图谱 + exception-driven | **3 周** | 14 | 真书连续 3 章增量抽取：① 后台批跑不阻塞面板；② 弹给作者的冲突 ≤ 2 条/章；③ 弹出的冲突接受率 > 60%（低于 60% 说明 prompt 或 schema 有问题，返工而非放行）；④ 别名合并后**所有旧引用仍能解析**（ULID 的验收）；⑤ 每条 proposal 的 evidence 都能反查回原文并逐字节匹配（`ratio<0.9` 的已被丢弃） |
| **M5** | 局部图 + 打磨 + v1.0 发布 | **2 周** | **16** | ① 选中人名 → 1 跳图 ≤12 节点，2 跳 + 类型过滤 ≤30 节点、<300ms；② 点事件节点跳章并高亮证据；③ **你自己用它连续写完 10 章，一次都没关掉面板**；④ 干净机器 `uvx novel-harness --demo` 60 秒内复现 README 的 GIF |

> **M4 进度（2026-08-03）**：事件记忆切片已按
> [实施计划](superpowers/plans/2026-08-03-m4-event-memory.md) 落地——事件超边/档案 schema、
> 抽取 → 提案/幂等被动确认 → 作者审阅闭环、安全事件上下文、接受度指标均已实现且全绿；
> 上表 ①–⑤ 的真书三章接受度验收待跑（⑤ 的逐字节证据匹配已由定位器测试覆盖）。

### 为什么是这个顺序

- **M1 在第 5 周就是完整可发布物。** 面板是零误报的、有用的、且它就是差异化本身。对比：脊柱方案第 12 周才有 textarea + 一跳小图，地基方案第 18 周才有 CLI + 250 行 html，评测方案第 13 周交付一张 p 值表和零个用户。
- **残值曲线单调递增。** M0 停 = 一个能用的中文小说数据层；M1 停 = 一个认知边界记忆外挂（有用、可发布、有人会用）；M2 停 = 加上带数字的起草器。**任意时刻中断都有残值。**
- **kill-gate 在第 9 周，不是第 16 周。** 「真实使用」批判者要求把起草提前，技术评审要求 kill-gate 有零噪声判分——这两条合并成 M2。
- **规则检测（M3）排在起草（M2）之后。** 因为「起草带不带图谱约束有没有区别」比「规则能不能抓到错」更能决定这个项目该不该继续。检查是起草的副产品，不是反过来。

### kill-gate 的完整设计（M2）

```
X0（对照）：上一场景末尾最多 800 个输入 code point + 场景目标一行
X1（事实清单）：X0 + 图谱约束以项目符号事实块注入（分区 A–D + must_not_reveal + forbidden_entities）
X2（叙事化）：X0 + 同样的约束，但改写成叙事性提示
        「李管家至今以为血脉秘密已经泄露；他会用这个误解去解读萧决的每一句话。」
```

**为什么要 X2：** 脊柱方案的 biggest_risk 指出了一个真问题——**中文长篇 LLM 对「前文叙事文本」的权重远高于「项目符号事实块」**，模型很可能照样跟着上一场景的调子走。如果这是真的，X0≈X1，但那**不代表图没用**，只代表注入形态错了，而解法在生成侧（约束前置到 system、改写成叙事性提示、validate-and-regenerate）不在数据侧。**没有 X2，你会把「注入形态错了」误判成「图没用」，然后杀掉一个正确的项目。** 这一臂的边际成本约等于零（一个 graph→叙事文本的转译函数，一天）。

**判分（两项，都零噪声）：**
- `future_leak_rate`：正则。第 c 章草稿是否出现 c 之后才首现的唯一专名。合成书的专名**全书唯一且两两互不为子串**（这个细节直接抄评测方案，他们踩过坑：否则「顾清音」和「清音」会让判分器误判）。
- `knows_violation_rate`：正则 + 集合查询。**关键：合成书的渲染 prompt 强制要求所有对白带显式说话人标签**（`萧决道：「……」`）。于是说话人归属在合成文本上是干净的正则，而在真书上做不到的那件事，在我自己造的仪器上可以做到。

**诚实说明仪器的边界（这是评测方案 biggest_risk 的量化版）：** 强制显式说话人标签让合成书在文体上不像真实网文。**它测的是「约束注入是否降低违规」这个机制问题，不是「生成的小说好不好看」。它是仪器，不是产品。** 这句话写进 `EVAL_PROTOCOL.md` 的适用范围声明。

**统计与调用：** 每条陷阱每臂跑 3 次，配对设计，McNemar 检验。三臂统一使用一份
provider-neutral `reasoning=high` call plan；adapter 只向已确认支持的 route 发送对应字段，能力未知
或不支持时在创建 run 与推理前失败。每个 final cell 初始一次调用，仅首次不足 2,000 时固定续写
一次；续写不是按内容挑样本。具体规则与 INVALID 边界见修正案 5。

**预注册（`EVAL_PROTOCOL.md`，git commit 时间戳早于第一个结果）：**

| 结果 | 判定 | 行动 |
|---|---|---|
| X1 或 X2 相对 X0 的 (leak + knows) 违规率绝对下降 **≥15pt 且 p<0.05** | 图谱注入成立 | 继续 M3，数字上 README |
| X2 显著 > X1 | 图有用，但形态错了 | 全线改用叙事化注入，重跑 |
| X0 ≈ X1 ≈ X2 | **图谱注入对 Writer 无效** | **砍掉 AI 起草线，项目定位改为「作者的记忆外挂」** |

**最后一个分支很重要：即使 kill-gate 失败，面板本身仍然有价值，因为它不依赖模型听话。这个项目有一条真实的存活路径，不是安慰。**

**为什么不需要 B+（向量 RAG）对照组：** 评测方案正确地指出 B+ 是它那套 A/B/C 里最重要的缺失对照——但那是针对「通用检索更强」这个主张。**我的主张是「认知边界」，而它在结构上是集合差集不是段落召回，普通 RAG 检索不到，不是因为检索得不好而是因为答案不在任何一段原文里。** 所以 X0 vs X1/X2 就是我这个主张的正确实验。B+ 只有当我将来宣称通用检索优势时才需要——v1 不宣称，写进 `EVAL_PROTOCOL.md` 的适用范围。

---

## 8. 第一周开工清单

### Day 1 上午 — 脚手架

```bash
mkdir -p /Users/lixibin/Desktop/novel-harness && cd /Users/lixibin/Desktop/novel-harness
git init
uv init --package --name novel-harness
uv python pin 3.12
uv add fastapi "uvicorn[standard]" pydantic python-ulid jieba typer watchdog openai
uv add --dev pytest ruff
```

（`docs/adr/bench/` 里已经有调研跑出来的 `scale_bench.py` 和 `fanout.py`——保留，它们是 ADR 0001 的证据。）

### Day 1 下午（2h）— 说话人标签覆盖率实测 ← **决定 R5 生死**

```python
# scripts/probe_speaker_tags.py
SPEAKER = re.compile(r'(?P<name>[一-龥]{2,4})(?:道|说道|问道|答道|冷笑道|笑道|开口道)\s*[：:]?\s*[「“]')
```

拿 **3 章真实小说**，数两个数：
- 分母：全部称呼词实例（人工数，或用别名表匹配）
- 分子：落在 `SPEAKER` 模式内的实例

**判据（写进 `docs/adr/0005-set-judgment-only.md`）：≥10% → R5 进 v1；<10% → 当场砍，M6 验收标准改写。**

**零依赖、2 小时、Day 1 就知道最高风险规则的答案。** 这是对技术评审 fatal #1 最便宜的正面回应。

### Day 1 下午（2h）— PyPI 冒烟 ← **决定装机叙事**

建 GitHub Actions + PyPI trusted publishing，发 `0.0.1` 空包，**在另一台机器（或 `docker run -it python:3.12`）验证 `uvx novel-harness --version`**。前端产物走 hatch build hook，不 commit dist。

**2 小时买掉整个「5 分钟跑起来」叙事的风险。** 不做的话你会一直用 `uv run`，从没验证过打包路径，然后在 M0 末花 3–5 天补 CI 和 build hook。

### Day 1 傍晚 — 7 份 ADR 的标题 + 一行「若此决策错误，修复成本是什么」

```
docs/adr/0001-no-neo4j-in-v1.md          # 含触发条件；bench/ 是它的证据
docs/adr/0002-no-vector-retrieval-in-v1.md  # 含 v1.1 选型和触发条件
docs/adr/0003-stable-ulid-ids.md
docs/adr/0004-declaration-over-extraction.md
docs/adr/0005-set-judgment-only.md       # 铁律 + R5 覆盖率实测结果
docs/adr/0006-evidence-double-pointer.md
docs/adr/0007-manuscript-lives-on-disk.md
```

### Day 2 — 数据层（一次写全，别分期）

`src/novel_harness/db.py`：`connect()` 设 `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`；`migrate()` 按文件名跑 `migrations/*.sql`，用 `PRAGMA user_version` 记录版本。

`migrations/001_init.sql` **一次建全**：

- `project`
- `chapter(id, project_id, number, title, path, text_sha256, updated_at, **indexed_version INTEGER DEFAULT 0**)`
- `chapter_snapshot(id, chapter_id, text, text_sha256, created_at)` ← 证据的不可变审计锚
- `node(id, project_id, label, name, props_json)` ← **ULID**
- `alias(id, project_id, node_id, surface, kind, usable_for_rules)`
- `edge(id, project_id, src, dst, type, props_json, **valid_from_chapter, valid_to_chapter, information_scope, status, confidence, source, evidence_id, evidence_status**)` ← 时态字段一次写全
- **`edge_type(type, exclusivity)`** ← §5.5，必须在第一条边之前
- `secret(id, project_id, name, description, sub_of)`
- `evidence(id, chapter_snapshot_id, chapter_id, para_index, quote_text, quote_sha256, occurrence_k)`
- **`decision_log(...)`** ← §5.7，append-only
- `model_call(id, ts, capability, model, params_json, prompt_hash, in_artifact, out_artifact, tokens_in, tokens_out, ms, cost, attempt)`
- `validation_report`, `proposal_set`
- 索引：`idx_edge_src(project_id, src, type, valid_from_chapter)` + 对称 dst

验收：`pytest tests/test_migrate.py`，连跑两次 migrate 不报错（幂等）。

### Day 3 — ID + 决策日志 + 章节切分

`ids.py` + **golden test**（10 个固定 business_id 的输出硬编码进测试文件，任何人改动生成逻辑立刻红灯）。

`decisions.py`：append-only 写入，只有 `append()`，没有 `update()`。

`text/chapterize.py` — **用这个正则，别用直觉写的那个：**

```python
CHAPTER_RE = re.compile(
    r'^[ \t　]*(第[ \t　]*[0-9〇零一二三四五六七八九十百千两]+[ \t　]*[章节回])[ \t　]*(.*?)[ \t　]*$',
    re.M,
)
```

**实测的 bug：若按常规写法用 `[\s　]*` 作为行内空白，`\s` 含 `\n`，会把下一行整个吞成标题**——「第一百零八章」（无标题独占一行）会把正文首行「他说第三章很好看」吞进 title。必须用 `[ \t　]` 排除换行；全角空格 `　` 必须在字符类里，中文 TXT 里到处都是。

**找一本真实的 300 章网文 TXT，切出章数与目录数一致，把它切片存进 `tests/fixtures/`。** 这一步一定会遇到脏数据（卷标题、番外、作者的话），预留半天。

### Day 4 — 图层（含唯一收敛点）

`graph/models.py`（Pydantic `Node`/`Edge`/`StateSnapshot`，props 在这里 `json.loads` 成 typed field）
`graph/store.py`（Protocol，五方法）
`graph/sqlite_store.py`
`graph/queries.py` ← **`state_at` + `supersede` 的唯一实现，全系统时态过滤只在这里写一次**

三组测试：
- `test_state_at.py`：交接章边界（ch9✗ ch10✓ ch142✓ **ch143✗** ch150✗）——2026-09-06 起由两条事实表达，不由一条边的两个端点表达（ADR 0043）
- `test_supersede.py`：`LOCATED_AT`(single_per_src) 写入新边自动闭合旧边；**`state_at` 绝不返回两条互斥边**
- `test_arch_guard.py`：`graph/` 之外任何文件 `import sqlite3` → 失败

### Day 5 上午 — 秘密与认知（头牌）

`secret` 表 + `KNOWS`/`BELIEVES` 边，**全部作者声明**。

`panel/knowledge.py::knowledge_matrix(project_id, chapter, cast) -> Matrix` — 纯集合查询，零 LLM：

```sql
-- 闭世界：无 KNOWS 边 = 不知道。零 DOES_NOT_KNOW 边。
SELECT c.id, s.id,
  CASE WHEN k.id IS NOT NULL THEN 'KNOWS'
       WHEN b.id IS NOT NULL THEN 'BELIEVES'
       ELSE 'UNKNOWN' END
FROM (cast) c CROSS JOIN secret s
LEFT JOIN edge k ON k.type='KNOWS' AND k.src=c.id AND k.dst=s.id AND <时态过滤>
LEFT JOIN edge b ON b.type='BELIEVES' AND b.src=c.id AND b.dst=s.id AND <时态过滤>
WHERE s.project_id = :pid
```

配单测：一个人物在 ch88 得知秘密 → ch87 UNKNOWN、ch88 KNOWS、ch152 KNOWS。

### Day 5 下午 — 场景解析 + CLI + 心跳脚本

`text/scenes.py`：解析 `## 场景 N` + `<!-- nh: cast=... loc=... goal=... -->`，以及**写回**（面板点选 → 写进文件）。

`scripts/demo.sh` ← **从今天起这是项目的心跳**：

```bash
#!/usr/bin/env bash
set -euo pipefail
uv run nh import tests/fixtures/real_novel.txt
uv run nh panel --chapter 151 --scene 3     # 打印认知矩阵
uv run nh check --chapter 151               # 打印 issue + evidence
```

一开始全是 stub，之后每做完一块换掉一个 stub，**它必须永远绿**。

**这是防漂移最便宜的手段：** M0/M1/M2 是按层划分的里程碑，层与层的接缝（cast 声明能不能被 knowledge_matrix 消费、state_at 返回的边能不能被规则消费）要到 M3 才第一次接上——而接缝正是最容易崩的地方。这个脚本让「端到端还通着」变成一个每天可见的布尔值，而不是一个 8 周后才验证的假设。**对一个没有 code review、没有 CI 压力的单人项目，这个心跳可能是唯一有效的进度真相源。**（来自脊柱方案，我原样采纳。）

### 本周 done_when

```bash
uv run nh panel --chapter 151 --cast 萧决,顾清音,李管家
```

打印出正确的认知矩阵（数据是你手填的 3 个秘密）；`scripts/demo.sh` 绿；四组测试全绿；`uvx novel-harness --version` 在另一台机器上跑通；`docs/adr/0005` 里写着 R5 的覆盖率数字。

**前端挪到 Week 2（M0 后半）。这周一行 React 都不写。**

---

## 9. 仓库骨架

```
novel-harness/
├── README.md                      # 第一屏见 §11
├── EVAL_PROTOCOL.md               # M2 前 commit，git 时间戳为证
├── pyproject.toml                 # hatch build hook 打前端
├── .python-version                # 3.12
├── Makefile                       # make dev / make eval / make demo
├── .github/workflows/
│   ├── ci.yml                     # pytest + ruff + 架构守卫
│   └── release.yml                # PyPI trusted publishing（Day 1 就建）
├── docs/adr/
│   ├── 0001-no-neo4j-in-v1.md     # 含触发条件
│   ├── 0002-no-vector-retrieval-in-v1.md
│   ├── 0003-stable-ulid-ids.md
│   ├── 0004-declaration-over-extraction.md
│   ├── 0005-set-judgment-only.md  # 铁律 + R5 覆盖率实测
│   ├── 0006-evidence-double-pointer.md
│   ├── 0007-manuscript-lives-on-disk.md
│   └── bench/                     # 已存在：scale_bench.py / fanout.py（ADR 0001 的证据）
├── src/novel_harness/
│   ├── __main__.py                # uvx 入口：起 uvicorn 127.0.0.1:7601 + webbrowser.open
│   ├── cli.py                     # nh import / panel / check / draft / run show / graph show
│   ├── ids.py                     # ULID + golden test 常量
│   ├── db.py                      # connect / migrate
│   ├── migrations/001_init.sql
│   ├── decisions.py               # append-only decision_log
│   ├── text/
│   │   ├── chapterize.py          # [ \t　] 不是 [\s　]
│   │   ├── segment.py             # jieba + 用户词典（人名 add_word）
│   │   ├── mentions.py            # 正则 alternation，长度降序
│   │   ├── anchor.py              # (para_index, quote, k) + difflib 模糊回退
│   │   └── scenes.py              # 场景块解析与写回
│   ├── graph/
│   │   ├── models.py              # Pydantic 出参，禁 dict/Row 越界
│   │   ├── store.py               # StoryGraph Protocol
│   │   ├── sqlite_store.py
│   │   └── queries.py             # ★ 时态过滤 + supersede 的唯一实现
│   ├── canon/
│   │   ├── commit.py              # 三层图谱 + exception-driven
│   │   └── revalidate.py          # quote_sha256 失活 → STALE（不进队列）
│   ├── panel/
│   │   ├── knowledge.py           # ★ 认知边界矩阵（头牌）
│   │   ├── state.py               # 人物当前状态
│   │   └── constraints.py         # must_not_reveal / forbidden_entities
│   ├── checks/                    # ★ 判分器 == Validator，同一份代码
│   │   ├── base.py                # check(ctx) -> list[Issue]
│   │   ├── future_leak.py         # R2
│   │   ├── dead_speaks.py         # R3
│   │   ├── location_conflict.py   # R4
│   │   └── address_conflict.py    # R5（Day 1 覆盖率决定生死）
│   ├── draft/
│   │   ├── context.py             # A–J 分区确定性拼装 + manifest
│   │   ├── prompt.py              # X1 事实清单 / X2 叙事化 两种形态
│   │   └── provider.py            # complete(messages, schema) + LocalOnlyMode
│   ├── extract/                   # M4
│   │   ├── roster.py              # 角色册 + 别名聚类
│   │   └── incremental.py         # 只抽 diff，后台批跑
│   ├── watch.py                   # watchdog 监视 chapters/
│   └── api/
│       ├── main.py
│       └── routes/
├── web/                           # Vite + React，只读面板，无编辑器
│   └── src/
├── synth/                         # 合成小册子（M2，1 周）
│   ├── world.py                   # 世界状态机（纯函数，可单测）
│   ├── naming.py                  # 唯一专名，两两互不为子串
│   ├── script_gen.py              # 事件采样 + _plant_traps(n=25)
│   ├── state_table.py             # replay() → ground truth
│   ├── render.py                  # 强制显式说话人标签
│   └── leak_selfcheck.py
├── eval/
│   ├── probe_gen.py
│   ├── runner.py                  # 三臂 × 25 陷阱 × 3 次，逐条 flush；旧 run 拒绝覆盖
│   └── score.py                   # DuckDB + McNemar (statsmodels)
├── data/demo/                     # 随包分发的 12 章合成书（无版权）
├── tests/
│   ├── fixtures/                  # 真书切片
│   ├── test_ids.py                # golden values
│   ├── test_migrate.py            # 幂等
│   ├── test_chapterize.py         # \s 吞行回归
│   ├── test_mentions.py           # 顾清音不切成顾/清音；琴声清音袅袅不产生 mention
│   ├── test_state_at.py           # 交接章边界（ADR 0043 后由两条事实表达）
│   ├── test_supersede.py          # 互斥性
│   ├── test_checks.py             # 合成小册子当 fixture
│   └── test_arch_guard.py         # graph/ 外禁 import sqlite3
└── scripts/
    ├── probe_speaker_tags.py      # Day 1 下午
    └── demo.sh                    # ★ 心跳
```

---

## 10. 最大的 3 个风险与止损

### 风险 1（最致命）：「帮我少写错」不是真需求，或不值得作者付出声明成本

**机制：** 中文网文作者的经济结构是「字数 × 订阅」，不是「质量 × 口碑」。他对设定错误的容忍度可能远高于对「每天多花 20 分钟」的容忍度。整个项目押的是「少写错」能形成真实需求，而这个假设**没有任何证据**——所有商业产品（Sudowrite / NovelCrafter）的付费意愿都集中在「帮我多写字」。

**为什么它排第一：** 因为它是我唯一**无法用工程手段消除**的风险。前面所有设计改动（面板、exception-driven、零 FP）都在降低成本侧，但如果收益侧是零，成本降到零也没用。

**止损：**
1. **第 5 周就发 v0.1**（M1 结束），用真实反馈证伪，而不是等 16 周。
2. **判据写死：发布后 4 周内，如果没有任何非作者本人的用户说「有用」，那就是答案。** 不是「再打磨一下」。
3. **M2 的起草功能提供「多写字」的那一半。** 如果面板没人要但起草有人要，项目转向「带记忆的起草器」——面板降级为起草的内部机制，不再是对外卖点。
4. **面板本身是记忆外挂而非挑刺器**（§3.3 第 5 点）——这降低了它被讨厌的概率，但不能创造需求。

**我不粉饰：这条风险如果成立，前面 16 周做的是一个漂亮的、没人需要的工具。**

### 风险 2：图谱约束对 Writer 无效

**机制：** 中文长篇 LLM 对「前文叙事文本」的权重远高于「项目符号事实块」。你把「李管家不知道血脉秘密」塞进 D 分区，模型很可能照样跟着 G 分区检索回来的那段旧原文的调子走，让李管家接话接得像是他知道。**如果这是真的，Graph→Context→Writer 这条价值链收益接近 0，而且再完美的 schema 也救不了——解法在生成侧，不在数据侧。**

**止损：**
1. **M2 的 kill-gate，第 9 周撞上它**（不是第 16 周）。
2. **三臂设计把「图没用」和「注入形态错了」分开。** 这是关键——没有 X2，你会把后者误判成前者，然后杀掉一个正确的项目。
3. **零噪声判分 + 配对设计 + McNemar。** 因为 temperature 不可设（400），方差是结构性的，5 次跑出来的差异跟噪声无法区分——评审给脊柱方案的 kill-gate 判低分正是因为这个（「5 章的样本量在 LLM 输出方差面前，得到的差异跟噪声无法区分，它给出的不是答案，是一个可以往任何方向解读的印象」）。
4. **失败分支是活的：** 砍掉 AI 起草，项目定位改为「作者的记忆外挂」。**面板不依赖模型听话，所以它在这个分支里完好无损。** 这是一条真实的存活路径。

### 风险 3：单人 16 周（业余 26–30 周）跑不完，或兴趣先耗尽

**机制：** 个人开源项目死于失去兴趣的概率，远大于死于架构错误。而 M3 的双边门槛会因为别名匹配质量、说话人标签覆盖率、图谱覆盖率反复卡——2 周变 4–6 周是常态。加上合成小册子（技术评审说是 5–8 天不是 3–4 天，我信他）和真书脏数据，真实总量可能 20–24 周全职。

**止损：**
1. **每个里程碑独立可发布，残值曲线从第 5 周起单调递增。** 这是这个方案相对其余三个的结构性优势：脊柱方案第 12 周前中断残值为零，地基方案第 10 周前为零，评测方案永远为零（作者本人都不是用户）。
2. **M6 的验收换成 100% 可判定的：「你自己用它连续写完 10 章且一次都没关掉面板」。** 不依赖「找到一个陌生作者」这个不可控变量。**而且如果你自己都会关掉它，那就是答案。**
3. **砍到底：** 全屏图、TipTap、LLM Validator、Best-of-N、Patch、Run Inspector、状态机、Policy Engine、Kafka/Debezium/Temporal、LangGraph 适配层——**一切不承重的东西全部推后。**
4. **心跳脚本（`scripts/demo.sh`）从 Day 5 起就在。** 它让「端到端还通着」变成每天可见的布尔值。
5. **预注册 `EVAL_PROTOCOL.md`（半小时成本）。** M3 的「误报 <1 条/章」的判定人就是想进 M4 的那个人——不预注册，第三周你会开始给自己找理由（「这条不算误报，是图谱抽错了」「这章比较特殊」），而这恰好就是这个方案批评原文档「没有校准信号」的那个病的复发。**这是整份计划里最便宜的一条纪律。**

---

## 11. 开源发布策略

### README 第一屏（逐行定死）

```markdown
# Novel Harness

**唯一一个知道「谁在第几章还不该知道什么」的中文长篇写作引擎。**

![demo](docs/demo.gif)   ← 8 秒：左边 VSCode 打字，右边面板实时显示在场角色的认知边界

你在第 88 章告诉过它谁知道那个秘密。第 152 章它还记得——
而且它不会让 AI 说漏嘴。

## 你的稿子不出你的电脑

图谱、面板、一致性检查全部本地跑，零网络请求。只有你主动点「起草」或
「抽取」时才调用模型；API 地址可自定义（中转 / 本地 ollama 都行），
也可以用 `--local-only` 彻底关掉——关掉之后工具依然完整可用。

## v1 的用户是谁

写中文长篇、正文用 Markdown、会开终端的作者。

如果你在用 WPS 且不想碰命令行，v1 还不适合你。这句话我们写在这里，
而不是等你装完再发现。

## 60 秒试一下

    uvx novel-harness --demo

自带一本 12 章的合成小说（我们程序生成的，无版权），第 8 章有一个植入的
认知陷阱。你不需要先导入自己的书。
```

### 第二屏：那个数字（M2 之后加）

```markdown
## 它到底有没有用？

合成书 12 章 / 25 个认知陷阱，同一个模型、同样的场景目标：

| 给 Writer 的上下文 | 认知越权 + 未来泄漏违规次数 |
|---|---|
| 只给上一场景 800 字 | 19 / 25 |
| + 图谱约束（事实清单） | 6 / 25 |
| + 图谱约束（叙事化改写） | 3 / 25 |

McNemar p < 0.001。`make eval` 可复现。

评测协议在跑出任何结果之前就 commit 了（见 EVAL_PROTOCOL.md 的 git 时间戳），
包括它的适用边界：合成书强制所有对白带显式说话人标签，所以它测的是
「约束注入是否降低违规」这个机制问题，不是「生成的小说好不好看」。
它是仪器，不是产品。
```

**为什么这样写：** GIF 负责让人点进来；数字负责让人相信不是玩具，并且在 HN 评论区回答「这不就是正则匹配吗」；预注册和适用边界负责回答「你自己造的靶子」——**主动说出自己的边界，比等别人指出来强，而且诚实本身就是传播素材。**

### 传播素材优先级

1. **GIF（M1，第 5 周）** — 唯一能被转发的东西。**主角必须是认知边界，不是称呼冲突。** 同样的工程量，换一个演示对象就把项目从「又一个中文写作工具」变成「一个有名字、有立场、有人会记住的东西」。称呼冲突留着做 R5，它是 hook，不是 identity。
2. **数字（M2，第 9 周）** — 上 HN / V2EX 的时机。
3. **合成书（M2）** — 无版权，可以直接放进仓库、文档、演示视频、issue 复现。它同时是 CI fixture、贡献者提第 5 条规则时的测试数据、所有 bug report 的可复现基线。
4. **`docs/adr/`** — 一个仓库里有明确的、公开的、会让作者自己停下来的 kill-gate 和触发条件，是最强的可信度信号，比任何架构图都能招来认真的贡献者。

### 发版节奏

| 时间 | 版本 | 内容 | 渠道 |
|---|---|---|---|
| **第 5 周** | v0.1 pre-alpha | 面板 + 角色册 + demo 书 | GitHub only，V2EX 一帖，不上 HN |
| **第 9 周** | v0.2 alpha | + 起草 + 那个数字 + EVAL_PROTOCOL | **HN / V2EX / 龙的天空 / Reddit r/LocalLLaMA** |
| **第 16 周** | v1.0 | + 规则 + 增量抽取 + 局部图 | 全渠道 |

**第 5 周就发，不等 16 周。** 理由：面板是完整的、零误报的、有用的，而且它就是差异化本身。早发 = 早拿反馈 = 早知道我在为谁做。而且残值：即使项目死在第 9 周的 kill-gate，v0.1 仍然是一个有用的东西。

### 贡献者入口（good first issue 的形状）

四条规则各自是**纯函数**：

```python
def check(ctx: CheckContext) -> list[Issue]: ...
```

任何人都能来提第 5 条规则，测试数据就是随包分发的合成小册子，改完规则回车 5 秒出误报数和漏报数。别名字典和人物卡也天然适合社区贡献。**单 SQLite + FastAPI + React，改一个规则不需要同时会 Cypher + SQL + 两库同步协议——这一条本身就是砍 Neo4j 的产品收益。**

---

## 附：一句话总结这份计划改了什么

原文档赌的是「用一套精密的确定性架构，把 AI 抽取出来的故事图谱管好」。

**这份计划把赌注换成：「用一套极简的架构，把作者本来就知道的东西强制执行到第 800 章」。**

前者赌的是 F1 0.276 的关系抽取和 <30% 的认知抽取；后者赌的是作者知道自己的书。**而后者才是文档里 Harness Kernel、时间图谱、信息作用域这套精密设计真正的用武之地——差异化不但成立，而且比原来更锋利：它从「我们用了时间知识图谱」（没人 care，且 Graphiti 已经做了）变成「唯一一个知道谁在第几章不该知道什么的引擎」（零覆盖，且 LLM 靠 scaling 短期解决不了）。**

价值从来不在「抽出认知图」，在「第 200 章时强制执行它」。作者忘的正是这个。
