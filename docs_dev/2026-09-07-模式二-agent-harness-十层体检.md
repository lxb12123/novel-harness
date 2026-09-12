# 模式二（写作助手）Agent Harness 十层体检

**日期**：2026-09-07
**起因**：维护者给了一份 Agent Harness 的十层框架（Interface / Event / Orchestration /
Model / Context / Tool Runtime / State / Memory / Safety / Observability），要求「看模式二
做得怎么样」，并点名「Claude Code 在第三层做得很优秀，看看我现在这个」。
**做法**：逐层对着 `src/novel_harness/agent/`、`api/chat.py`、`draft/`、前端 `chat.ts` 读，
每条结论带文件行号。**不猜，没读到的就写没读到。**

> ⚠️ **这份文件不是权威**（`CLAUDE.md` 的 `docs_dev/` 规矩）。它记的是这一天我核到了什么、
> 哪几件要维护者拍板。任何一条要变成工程约束，必须落进 `docs/` 或开一份 ADR——
> 否则就有两个真相源了。

---

## 一句话结论

**③ 编排和 ⑤ 上下文是全仓最硬的两层，细到超出这份框架的描述**；①⑧⑨ 的「缺」大部分
**是有 ADR 的有意裁剪，不是漏**；真正的洞只有一个，但它同时是 ②⑦⑩ 三层的洞：

> **一次 run 不是一个持久实体。** 没有 `agent_run` / `agent_step` / `agent_event` 三张表，
> `model_call` 上没有 `run_id`，工具调用一行账都不记。于是那张「Run #812 · 总耗时 43.8s ·
> 7 次模型 · 13 次工具 · 最慢 graph_search 8.2s」的表，**今天在这个库上拼不出来**。

---

## 逐层

### ① Interface —— 大半是**有意不做**，剩下的洞不在对话区

| | |
|---|---|
| 已有 | `TurnProgress.steps`（人话步骤流，`frontend/src/chat.ts:283`）、稿子**真的逐字长出来**（`draft_delta`）、`asked_author` 的可点选项、停、跑完的回执 |
| 没有 | Run 编号、Step 状态点（✓/●/○）、Tool 名、Duration、Token 数 |

**框架里那张「Tool: search_repository / Duration 4.3s / Tokens 12,430」面板，在这个产品的
对话区里是被明令拒绝的，不是漏的**：`frontend/src/test/screenGuard.ts` 和
`tests/test_wording_guard.py` 拦着机器码和 `前缀:标识` 上屏，`ToolSpec.label`
（`agent/tools.py:1335`）存在的全部理由就是「别把 `character_chapters` 摆到用 WPS 的人脸上」。
这个产品的屏幕前坐的是小说作者，不是开发者。

**真缺口**：那份开发者视图**该有，但该在「活动记录」页上**（那儿本来就是调试面**且已经在显示
token**，`frontend/src/components/ActivityLog.tsx:206`）——而它今天拼不出来，因为 run 不是实体。
→ 归入下面的 **D1**。

### ② Event Layer —— 有事件，但是一根直连的管子，不是总线；**且不落盘**

已有，而且做得比框架描述细的部分：

- `TurnEventKind` 10 种 + `TurnEvent`（`agent/loop.py:1179`），**每条自带 `said_to_author`**
  ——措辞的唯一出处在后端，前端不翻译（ADR 0024）。
- `safe_emitter`（`loop.py:1440`）+ `_turn_frames` 的三条纪律（缓冲 / 丢弃 / 不抛，
  `api/chat.py:1382`）：**一个掉线的浏览器不许把这一轮弄崩**，这条已经做对了。
- 前端是纯函数 reducer（`applyTurnEvent`），交错到达的多稿能被单测钉死。

缺的三样：

1. **事件不持久化。** 刷新页面 / 断线重连，这一轮**已经发生过的事件全丢**，只剩最终回执。
2. **事件上没有 `run_id` / `step` / `seq` / `ts`。** 对照框架给的
   `{"type","run_id","step_id","tool","timestamp"}`，今天一个都没有。
3. **只有 tool / draft 两条线 + reply/stopped**。没有 `turn_started` / `step_started` /
   `step_completed` / `model_started`，所以「Planning ✓ Searching ✓ Writing ●」那种分步进度
   条画不出来——屏幕上只有一串流水句子。

### ③ Orchestration —— **这一层是强项，闸门设计我认为不逊于 Claude Code 的可见部分**

`TurnLimits`（`agent/loop.py:1462`）七个闸，每一个都有实测来历：

| 闸 | 默认 | 拦什么 |
|---|---|---|
| `max_steps` | 8 | 步数 |
| `max_tokens` | 300k | 成本（**口径是 token 不是钱**：BYOK 下引擎不知道单价，那是有意的空） |
| `max_calls_per_step` | 6 | 一步派发几个工具——**实测形态是模型一次发 50 个 tool_call** |
| `parallel_tools` | 1（API 层放开到 3） | 并发；`save_draft` 在批里是屏障 |
| `repeat_limit` | 3 | 原地打转（同名同参） |
| `tool_failure_limit` | 3 | 卡住。**按工具名和不分名各数一个数**——只按名数的话「三个瞎编的工具名」永远是三条 1 次，闸一次都不响 |
| `unknown_name_limit` | 3 | **只增不减**。2026-08-13 在 722 章真书上实测：「失败、失败、成功」这个最自然的节奏能把前两个闸无限清零 |

配套：`StopReason` 11 种，**每一种都说得出自己为什么停**且各有一句给作者的话
（`stop_wording()`）；`Cancellation`（`loop.py:1560`）是线程安全的信号、**带进流式调用内部**，
所以「按停」是真打断而不是「等这轮跑完」；`_Running`（`api/chat.py:233`）用 `run_id` 比对挡掉
过期的 stop。

**缺**（都要拍板，见 D 系列）：Run/Step 不是实体（Turn 是唯一一级单位）；没有工具级超时
（只有 provider 的 600s）；没有重试（**有意**，ADR 0044：不替模型圆场）；没有暂停/恢复；
跨进程不能续跑。

### ④ Model Layer —— 有网关的**实质**，但没有一个门面，入口不止一个

已有：`ProviderConfig` / `ProviderCapabilities` / `plan_structured_call` /
`ResolvedCallPlan`（ADR 0044：结构化输出走线上 `response_format`，端点不认就退掉并**记住那条
路由**）、`ModelPort` 协议 + `ProviderModelPort`（把作者的「停」包进流里，`agent/model.py:253`）、
`ModelCallReceipt` 统一账单原料（`extract/call_audit.py:16`）。

**缺**：框架说的 `ModelGateway.generate/stream/tool_call/compact/embed` 那种**统一门面没有一个
类**。今天调用点散在 `agent/model.py`、`draft/provider.py`、`draft/product_draft.py`、
`extract/runner.py`，`ModelPort` 只覆盖 agent 那一条。retry / rate-limit / timeout 各写各的。
pricing 恒 null（**有意**：BYOK 下引擎不知道作者签的单价，而且 `priced_calls` 把这个「零」的
理由一起发出去，前端才分得开「没花钱」和「没记账」）。

### ⑤ Context Layer —— **比框架描述的还具体**

`project()` → `Projection`（`agent/loop.py:729`）已经是 ContextPlanner + Budgeter + Compactor
三合一，而且守着一条这个框架没提但更重要的纪律：**裁了什么必须说出来**——
`off_chapter` / `stale_manuscript` / `filled_shells` / `expired_rules` / `stubbed_results` /
`dropped_calls` / `dropped_reasoning` / `compressed_blocks` / `over_budget` /
`budget_units` / `tool_units` / `payload_units` 全部随投影回执一起出来。
静默截断读起来像「全给了」，那才是最贵的。

还有：工具声明占的那块**固定地板**进了账（ADR 0023 点名的那 23%）；装不下时把最旧的对话块压成
摘要、canonical 一字不动；收起的结果按编号取回（`REGISTRY_HEADER`）。

**没有 Retriever ——有意**（ADR 0002：v1 不做向量检索）。
**唯一的结构性意见**：这一整套住在 `loop.py` 里，那个文件 2416 行。拆不拆是审美/维护性问题，
不是能力问题 → 归 **D4**（低优先）。

### ⑥ Tool Runtime —— 表驱动，schema 与校验器同一份；缺三样

`ToolSpec`（`agent/tools.py:1326`）：`name` / `description` / `args`（**同时是校验器和发给模型
的 schema 来源**，所以「声明说收 A、实现却读 B」结构上不可能）/ `handler` / `label` /
`concurrent`。`TOOL_TABLE` 15 条，`BatchRunner` 管并发和屏障。

**缺**：`timeout`、`permissions`、`output_schema`。
**但风险不高**：这张表里没有 shell / 任意文件读写 / 网络，全是本地 SQLite 读 + 一个会花钱的
`draft_chapter`。唯一会写作者的书的是 `save_draft` → 见 ⑨。

### ⑦ State —— **对话态持久，运行态不持久**

持久的那半做得对：`chat_session` / `chat_message`（迁移 006），`PersistFn` **跑到一半就落盘**
（`api/chat.py:1150`），`Conversation.pending_calls` 记「哪几个 `tool_call` 还缺结果」，
断了下一轮就地配壳并告诉模型「这一步没跑完，需要就重新查一次」（`LOST_RESULT`）。

不持久的那半：**没有 run/turn/step 行**；`_Running` 是进程内存。所以
「进程死了 → 读 RunState → 从 Step 12 接着跑」**做不到**；做得到的是「从上一条消息接着说，
缺的自己重查」。**这是有意的降级**（ADR 0019：执行态就是一串 message + 哪几个查询还缺结果），
而且 `_Running` 的 docstring 明确拒绝过库表版本（「一个存在库里的 `status='RUNNING'` 会在进程
崩掉之后永远卡在那儿」）——**要补 D1 就必须先回答这条反对意见**。

### ⑧ Memory —— 这个产品的 memory 就是它的主业，只是不在 agent 层

图谱 / canon / 章节总结 / 事件记忆 = 长期记忆（domain 那一侧，M4）。
`remember_rule` = 作者规矩（ADR 0028：按情境失效）。
没有向量语义记忆（**有意**，ADR 0002）；没有 episodic memory（跨 run 的经验）。
**框架自己也说 Memory 是第二阶段能力**，这一层不构成问题。

### ⑨ Safety / Control —— 有一条真安全模型，但它不是 Policy Engine

- **ADR 0022：起草是提议不是落盘。** 危险动作被结构性地降级成候选——这比一个会被绕过的
  policy 判断强。
- **ADR 0021：落盘不问作者**（有意，保留至今）。
- 工具面很小，没有 shell / 网络 / 任意文件系统。
- `ask_author` 是唯一的「问用户」通道，而且它**必须同时在回执里**，不能只在事件流里
  （作者可能三个月后才打开那段对话）。
- **Policy Engine 是本仓明确砍掉的东西之一**（`CLAUDE.md` 那份「最容易犯的错」第 1 条点名）。

**真缺口**：`save_draft` 写作者的书，**没有任何一层能拦**——`ToolSpec` 上没有 `writes` 标记，
也就没有任何地方能挂审批或审计。→ **D3**。

### ⑩ Observability —— **账在，链路不在**

已有：`model_call` 每次调用一行——capability / model / params_json / prompt_hash /
in_artifact / out_artifact / tokens_in / tokens_out / cache_read / cache_write / **ms** /
cost / attempt / chapter_number。四条产线（agent / writer / summarizer / extractor / advisory）
**都填了 `elapsed_ms`**，我逐个核过。活动记录页三层展开 + `runs` 面板给 totals。

缺的两样，是同一个洞：

1. `model_call` **没有 `run_id`**，所以聚合不出一次 run 的全貌。
2. **工具调用完全不落账**——只有模型调用有账。所以「13 次工具调用、最慢 graph_search 8.2s」
   一个数都没有。

---

## 已经直接做掉的（不需要沟通那一档）

### ✅ 修：system prompt 每一轮都在让模型重查一份**不存在**的清单

`AGENT_SYSTEM_PROMPT` / `_EN` 的第二条原文是：

> **不许说破的东西是逐章算的。** 你上一轮查到的清单对另一章可能已经过期了——要为哪一章
> 写东西，就为哪一章重新查一次。

**那句话的对象已经不存在了**：`must_not_reveal` / `forbidden_entities` 随
[ADR 0039](../docs/adr/0039-secrets-offline.md)（2026-08-25）和
[ADR 0041](../docs/adr/0041-forbidden-entities-cut.md)（2026-08-31）整套删掉。核实过：
全仓 `grep` 不到任何一个叫 `must_not_reveal` 的字段；`SceneConstraints`
（`panel/constraints.py:128`）今天只剩 `chapter` / `unresolved_cast`；`scene_constraints`
工具返回的只有 `cast` / `cast_derived`。

**代价不是措辞难看，是每一轮都花钱让模型去查一份查不到的东西**，而且它可能据此以为有隐藏
约束、写得更缩手缩脚。

改法是**只换对象、不动规矩**：「查到的东西绑在那一章上 / 那一章的约束由后端当场算 /
你传不进去」三句今天仍然逐字成立（`check_track` 的护栏、`_scene_context` 都在），保留；
「不许说破的清单」删掉。中英两档同步改，两份 docstring 记了为什么，
`tests/test_agent_loop_projection.py` 里那句引用它的过期注释一并更正。

**两件必须知道的事**：

- **只对新会话生效。** 前缀在开会话时逐字落库、读回时不重算（`agent/store.py` + 迁移 006），
  所以作者已经存在的对话里那句话还在。**这是有意的**——重写历史比留着一句过期的话更贵，
  也会让「这段对话当时是拿什么跑的」不再可信。
- 改的时候被 `test_no_chapter_bound_block_sits_in_the_stable_prefix` 当场咬了一次
  （我举例写了「第 40 章 → 第 90 章」，而稳定前缀里不许出现任何具体章号，否则缓存前提破了）。
  这条已经写进那份 docstring，下一个人不用再踩。

验证：`ruff` 绿，`test_agent_loop_projection` / `test_chat_store` / `test_context_policy` /
`test_agent_loop` 共 93 条绿。

### ✅ 同一类错的另外两处（同日、界面侧）

写作助手空态和会话列表空态都在向作者宣传同一个已下线的能力（「也能替你算这一章谁还不知道
什么」/「问它这一章有什么不能说」）。已改成只说 `agent/tools.py` 真有的那几样。
**这三处是同一个病：删能力时删了实现，没删掉那些替它招手的话。**

### ✅ 修：两道守卫的同一类误报（其中一条在 HEAD 上已经红了一周多）

体检本身没碰这两处，是跑全量测试时撞出来的。**两条都不是把守卫调松，是教它认单位。**

1. **`test_frontend_product_language`**——它已经会跳过 SVG 的 `d="…"`（2026-08-13 齿轮
   那次的病历就写在它的 docstring 里），但**几何不一定住在属性里**：翅膀图标的四条路径
   存成字符串数组再 `map`，于是 `"M330.5 613 C…"` 又一次被 `\bM\d+\b` 当成里程碑
   `M330` 抓住。改成两道过滤：属性一道，**整串都是路径语法的字符串字面量**一道
   （判据收得很紧：首字符 `M`/`m`、其余只有路径命令字母和数字标点、且够长）。

2. **`test_continuation_tail_limit`**——`continuation.ts` 里的 `IDLE_MS = 1000`
   （停手多久才去要建议）被当成写死的上文上限。**这条红在 HEAD 上挂了一周多**
   （2026-08-30 那次批量提交起），不是这次改出来的。
   收窄的判据是**单位**：名字以 `_MS` / `Ms` 结尾的常量，它的数是时间不是字数；
   没有人会把上文上限命名成 `TAIL_MS`。**没有调高门槛**——调高的话「有人写死一个 1200
   的上文上限」会跟着一起躲过去。

跑完：`uv run pytest -q` **2561 passed / 1 skipped**，`ruff` 绿，`tsc` 绿，
vitest 859 过 / 3 红（那三条是 `CodeEditor.test.tsx` 已知的 CM6 多实例优先级问题，
文件里记着「不要在这儿加代码硬凑绿」，与本次无关）。

---

## 要你拍板的（写在这儿，没动代码）

按依赖顺序。**D1 是其余几条的地基**。

### D1 —— 把一次 run 变成持久实体（同时补 ②⑦⑩）

**做什么**：三张表 + 一列。

- `agent_run`：id / project_id / chat_id / chapter / status / started_at / ended_at /
  stop_reason / steps / model_calls / tool_calls / tokens_reported / tokens_charged
- `agent_step`：run_id / seq / started_at / ended_at / model_call_id
- `agent_event`：run_id / seq / ts / kind / payload（**append-only**，用来回放和断线续传）
- `model_call` 加一列 `run_id`（可空；旧行 NULL——迁移 009 给 `chapter_number` 加列时就是这个
  先例，而且那次实测「旧行全是 NULL」）
- 工具调用也记一行（进 `agent_step` 或单开 `agent_tool_call`）

**买到什么**：
① 活动记录页能给出一次 run 的全貌（**开发者视图放这儿，不放对话区**，见 ① 那节）；
② SSE 支持 `Last-Event-ID`，刷新/断线能补发漏掉的事件；
③ 崩溃之后看得出「有一轮没收尾」。

**必须先回答的反对意见**（`_Running` 的 docstring 自己提的）：
> 一个存在库里的 `status='RUNNING'` 会在进程崩掉之后永远卡在那儿。

我的看法：这条反对**成立于「拿库表当活跃锁」**，不成立于「拿库表当事后记录」。
可行的分法是——**活跃锁继续留在进程内存**（`_Running` 不动），库表只记事实，
`status` 用 `started_at` + 心跳时间戳推断，读的时候超时的一律算 `abandoned`。
**但这是一个设计裁定，要你点头。**

**代价**：一次迁移 + loop/chat.py 各加一个记录点；`agent/` 不许直接碰库（这一层今天是
`ports.py` 的注入），所以记录函数要照 `LedgerFn` 的形状再注入一个。

### D2 —— 事件补粒度（依赖 D1）

加 `turn_started` / `step_started` / `step_finished` / `model_started` / `model_finished`，
事件带 `run_id` / `step` / `seq` / `ts`。

**要你定的是一件事**：这些新事件**上不上作者的屏**？
- 上：能画出「Planning ✓ Searching ✓ Writing ●」那种分步条，但那是开发者语汇，
  和 `screenGuard` 那条「机制不上作者的屏」正面顶。
- 不上：只进 `agent_event` 表和活动记录页。**我倾向这条**，理由见 ① 那节。

**不做的话会怎样**：今天这样也能用，只是断线之后那一轮之前发生过什么永远拼不回来。

### D3 —— `ToolSpec` 补三样：`writes` / `timeout` / `output_schema`

- **`writes: bool`**——今天只有 `save_draft` 是 `True`。**先只用来记账和当屏障，不做审批**
  （审批要不要有是另一个问题，ADR 0021 说落盘不问作者，那条今天仍然成立）。
- **`timeout`**——本地 SQLite 读挂不住，真正会挂的是 `draft_chapter`，它已经吃 provider 的
  600s。所以这一条**优先级低**，除非将来接 MCP / shell。
- **`output_schema`**——给模型声明返回形状。**这一条会改模型行为**（它会看见新的 schema），
  所以要在作者的真书上跑之前先想清楚，不能顺手加。

### D4 —— ModelGateway 收口（结构性欠账，风险低）

把 `agent/model.py` + `draft/provider.py` + `extract/runner.py` 的调用点收到一个门面后面，
统一 retry / timeout / rate-limit / 记账。

**注意一条既有的方向约束**：`draft/` 不许 import `agent/`（`extract/call_audit.py:23` 记着，
`ModelCallReceipt` 就是为这条从 `loop.py` 搬走的）。所以 gateway 只能住在 `draft/` 或一个新的
顶层包，**不能住在 `agent/`**。

**不做的话会怎样**：换模型/换供应商时要在四个地方各改一遍，而漏掉一处的症状是「某条产线还在
用老端点」——没有任何测试会红。

### D5 —— `loop.py` 2416 行拆不拆（低优先，纯维护性）

⑤ 那一整套（`project` / `Projection` / 压缩 / 预算）可以拆成 `agent/context.py`。
**纯搬家，不改行为。** 但它会把一大片 git blame 打散，而这个文件的注释密度是这个仓库
最贵的资产之一。**我不建议现在做**，记在这儿只是为了下次有人提的时候有个出处。

---

## 附：这次核过但**没有问题**的地方（省得下次重复查）

- 五个 `capability` 值（extractor / summarizer / writer / agent / advisory）在
  `frontend/src/backendMessages.ts::CAPABILITY_LABEL` 里**都有中文**，没有漂。
- 四条产线造 `ModelCallReceipt` 时**都填了 `elapsed_ms`**，活动记录页的耗时不缺 agent 那一档。
- `AGENT_PARALLEL_TOOLS = 3` 在 `api/chat.py:176` **真的接上了线**，不是「建好没接」。
- SSE 那条线的生命周期已经处理过「浏览器在第一个字节之前就关掉」那个坑
  （`_turn_frames` 不是生成器，线程在函数被调用时就起），而且注释里记着实测症状。
