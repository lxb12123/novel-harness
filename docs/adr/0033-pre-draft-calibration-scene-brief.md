# ADR 0033：写前校准 + 不可变 SceneBrief —— 模式二起草前的数据链

- 状态：**已接受**（2026-08-17）
- 日期：2026-08-17
- 回答了什么：`docs_dev/2026-08-17-模式二写前校准与写作执行简报任务.md` 定下的模式二
  写前数据链：空白新章不能因为 `UnknownCast` 就把正向记忆与「安全 cast 不确定」绑成
  同一个开关；Writer 必须拿到目标章当前正文与一份类型化、可反查的执行简报；作者意图、
  作者 Canon、机器抽取、机器总结和未知必须分开，不允许混成一段「系统结论」。

## 决策

模式二新增一条只读、类型化的写前校准链，三步走：

1. **`calibrate_scene`**：Agent 提交 `SceneProposal`（预计人物、封闭指令码、视角/语气/
   节奏/结尾码），后端按第 N 章时点做确定性校准（身份、时态、集合、覆盖、证据），
   产出 `CalibrationReport`。本工具**只做确定性判断，不做语义判断**（ADR 0005）。
2. **必要时 `ask_author`**：只有不问就必须猜、而猜错作者又看不见时才问；语义张力
   永远由 Agent 标为 `MACHINE_INFERENCE`，不许伪装成规则命中。
3. **`seal_scene_brief`**：后端重新校验全部事实引用与可见性，由固定模板渲染
   `SceneBrief` 与唯一的 `goal_spec`，生成不可变 `calibration_id`。`draft_chapter`
   只收 `chapter + calibration_id`，不再收自由文本 `goal`。

## 对既有 ADR 的补充与修改

### 补充 ADR 0018 / 0019：检索 cast、安全 cast、Writer 可见记忆三层分离

ADR 0018 的「在场由正文推出」继续有效，但**它只决定安全基集，不再决定记忆能不能加载**。
新增三层，全部由后端计算：

- `retrieval_cast`：由预计人物（作者原话 + Agent 提案）解析而来，**只用于检索候选
  档案、状态、关系、事件和摘要**。
- `safety_cast`：仍只从目标章当前正文的实际提及派生（ADR 0018 / 0015 D4 不变），
  决定 `must_not_reveal` / `forbidden_entities`，由后端按章即时重算。
- `writer_memory`：候选资料经确定性白名单后才允许进入 prompt。

预计人物**永远不能把空白章的安全基集从 `UnknownCast` 变成 `Resolved`**。正文基集为空
时保持 UnknownCast / 全秘密禁止说破；正文基集非空时，预计人物最多与它取并集
（只增不减）。「不得自行引入未列出的命名人物」只是软写作要求，不是安全证明。

### 补充 ADR 0017 / 0023：校准产物是非 Canon 的可重建临时工具产物

`CalibrationReport` / `SealedCalibration` / `SceneBrief` 是**非 Canon 派生产物**：

- 不写入 StoryGraph、章节总结、RememberedRule 或稳定 system 前缀；
- 可按 ID 重建、撤销、过期；`source_watermark` 由
  `schema_version + author_turn_id/hash + canon_version + target_sha256 +
  supporting_chapter_hashes + summary_row/snapshot_ids` 组成；
- 水位变化后 `draft_chapter` 明确拒绝旧 ID，要求重新校准；
- 对话裁剪后仍可按 ID 找回；过期报告在模型投影中缩成失效提示，不常驻稳定前缀。

存储使用独立 SQLite 表（迁移 `024_calibration.sql`），按内容/水位幂等，可跨 Agent
resume，**不是进程内 registry**。它不成为第二真相源：图与正文仍是唯一权威。

### 补充 ADR 0010：SceneBrief 只走产品专用装配，且是类型化安全简报

`SceneBrief` 只出现在产品起草路径（`draft_chapter` 的 PRODUCT 分支），三臂共用
`assemble()` 逐字节不动。本任务**不把作者/Agent 任意散文带入 Writer**：

- Writer 只接 Writer-safe `item_id`、封闭 directive kind 与安全参数；所有展示文字由
  后端按类型固定渲染，不接受 Agent 自填 `text`；
- 作者聊天原文不自动进入；只有绑定完整任务卡、指令联合、安全参数、turn/hash 与
  card hash 的 `AuthorInstructionRef` 才能形成 `AUTHOR_INTENT`；
- 未确认的普通请求投影进入独立 `projected_request_directives`，强度固定为
  `MACHINE_INFERENCE`，不得放入 `author_instructions`；
- UnknownCast 下白名单按**字段**判定：只允许封闭 enum/key、机器键、NodeRef 显示名、
  布尔/纯数值和章号；任意字符串 value/备注/关系/状态描述隐藏，关键词扫描只作附加
  纵深防御，不负责把任意字符串变成 Writer-safe；
- 若以后要恢复 rich free-text brief，必须另开 ADR 修改 labels-only 边界，不能靠
  关键词过滤暗中放行。

## 认识状态固定八类

`AUTHOR_INTENT`（作者确认过的本稿意图，模型不能自报） / `AUTHOR_CANON_FACT` /
`AUTHOR_BACKGROUND` / `EXTRACTED_CURRENT` / `OBSERVED_TEXT` / `MACHINE_SUMMARY` /
`MACHINE_INFERENCE` / `UNKNOWN`。`CANON` 只是生效范围，不再单独代表来源等级：
ADR 0020 之后系统可自动升 Canon，因此写前校准至少保留 `source` / `confidence` /
`evidence_id` / `evidence_status` / `valid_from_chapter` / `valid_to_chapter` /
正文快照一致性。

## 与并行通知任务的唯一交界

本任务只产出 `ContinuityConflictHandoff`（RETCON 才产），写入
`calibration_handoff_outbox`（producer 侧，迁移 024）；并行任务消费它并负责
`system_notification` 的持久化、右栏 UI、忽略/已解决生命周期。通知侧不得重新调用
LLM 再判断一次「是否冲突」。`RepairRequest` / `RepairPlan` / `ExecutionReceipt` 只
定义 adapter 契约，不实现通知状态、不编排总结/抽取，也不在安全修订候选能力完成前
暴露「自动修复」。

## 迁移编号约定

本任务使用迁移 **017**，紧跟 016 连续落地。**2026-08-19 修订**：草案原拟 024，
但并行任务 017–023 尚未并进本分支，静置一个 024 会造成 `_migrations()` 断号
（16→24）而直接抛 `MigrationError`，`nh serve` / `nh init` 全部起不来——比对
「终态好看」更重要的是今天能用。并行任务（保存 → 快照 → 验证 → 总结版本 →
Canon 纠错 → 系统通知 → 别名生命周期 → 规则集）原拟用 017–023，与本任务撞号：
**合并时必须把并行迁移整体后移一档（019–024）**，版本号才重新连续。

## 若此决策错误，修复成本是什么

**中等。** 校准链是纯加法：不写 Canon、不碰三臂 `assemble()`，撤销 = 不再让
`draft_chapter` 收 `calibration_id` 并把新表停用。真正贵的是「白名单放太松导致
tell 进 Writer」——那与 ADR 0010 D3 同一档，只能靠 poison 测试与 review 守。
