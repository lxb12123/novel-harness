# ADR 0029：保存键触发快照刷新，固定协调器 + CAS 收口晚到结果

- **状态**：已接受
- **日期**：2026-08-17
- **推翻了**：**本仓库自己**——`ARCHITECTURE.md` / `UI_ARCHITECTURE.md`
  「只在离章时 autopilot、禁止挂保存键」的决定。原始需求文档没有这条；它是
  本仓库在「后台整理从哪触发」这个问题上自己拍的那一刀。
- **相关**：[ADR 0007](0007-manuscript-lives-on-disk.md)（正文真相源在磁盘）、
  [ADR 0020](0020-clean-extraction-auto-canon.md)（自动 Canon 的前提是「可查 + 可改」）、
  [ADR 0021](0021-agent-writes-drafts-without-asking.md)（落盘仍不问作者）、
  [ADR 0022](0022-drafting-is-a-proposal-not-a-write.md)（起草是提议不是写）

## 决策

**作者按保存键 → 正文先做零副作用结构预检，再原子替换文件并提交内容寻址快照；
随后进入一条固定的章节刷新 DAG：正文验证器先作闸门，通过后总结与抽取并行。**

这条 DAG 的每一笔输入都是同一个不可变 `ChapterCommitToken`：

```python
class ChapterCommitToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    chapter_id: str
    chapter_number: int
    source_snapshot_id: str
    source_generation: int = Field(ge=1)
    text_sha256: str
    text: str
    changed: bool
```

任务不得在运行中重新读取「当前正文」；token 同时带不可变 `source_snapshot_id`
和单调 `source_generation`。任何机器结果在生效前都要过一组 CAS：
`source_snapshot_id + source_generation + ruleset_epoch/hash + fencing_token`
（总结另校验 `expected_head`）。

配套规则：

- 保存成功**立即返回**，不等待模型；`changed=false` 只表示不用建新
  snapshot/generation，仍要幂等 `ensure_refresh_coverage` 补缺失分支。
- 同章保存频繁发生时，等待 2 秒静默窗口；尚未开始的旧快照任务直接
  `SUPERSEDED`，只运行最新快照，避免每次 Ctrl-S 都付费。
- 已经发给模型的旧任务不强行取消，但晚到结果永远不能成为 current，
  也不能写入 `FRESH`/Canon——旧 token 只能留审计。
- 从 S2 还原到历史已有的 S1 也属于变化和新 generation（防 ABA）。
- 四类持久工作（chapter refresh、summary reconciliation、system notification、
  alias replay）都由应用 lifespan 启动同一个恢复管理器，不依赖下一次保存或请求。
- 每章当前摘要由显式 head 决定，不再由 `rowid/created_at` 猜；
  每章必须有一行 `chapter_summary_head`，head 可以为 NULL。

## 为什么

### 1. 保存是作者唯一稳定、自然的「我写完这一版了」信号

旧设计的触发点是**离章 autopilot**：作者切到下一章时才整理上一章。后果是
「保存一次、切章再派一次」的双重 autopilot，以及「作者写完一章不切章，
这章就永远不整理」。保存键是作者每次都按的那个动作，把它挂上去，
整理跟着正文的真相源走，而不是跟着浏览行为走。

### 2. 不可变快照 + 单调 generation 是并发正确的唯一收口

保存后总结/抽取是几十秒到几分钟的模型调用，期间作者会继续保存。
没有不可变快照，任务读到一半的正文就变了，产出的总结/抽取既不对应任何
一版正文、也没办法回头查证。没有单调 generation，S1→S2→S1 会让第一轮
S1 的晚到任务通过 snapshot/hash 等值 CAS（ABA）复活。两个都钉死后，
「旧正文的结果永远无法重新成为 current」在结构上成立，不靠运气。

### 3. 成本合并是「保存触发」能成立的先决条件

保存键本来比离章触发更频繁。直接每次保存都付费会让作者弃用，所以
合并窗口、幂等 coverage attempt、terminal FAILED/BLOCKED 不自动重试
三者缺一不可。**「不重复付费」和「不重复调用」是两件事**：lease 只控制
谁可以工作，模型调用按至少一次设计，账本按次落审计。

## 代价（承认，不粉饰）

- 保存触发的付费调用是旧「离章才跑」没有的成本。我们用合并窗口 +
  幂等复用把它压到「每版新正文一次」，但压不掉的那一次是真实花费。
- 协调器是一段**固定** DAG，不是通用 Workflow Runtime；将来出现第二条
  自动流水线时它不能复用，得另开决策。
- `ChapterCommitToken` 冻结在任务创建时，若实现里有人「顺手再读一次
  当前正文」，错误会静默发生——这条不变量主要靠 review 和测试守。

## 什么条件下推翻本 ADR

- 真书实测显示保存触发的付费整理被作者高频关闭（成本接受度不成立）。
- 出现「旧快照结果重新成为 current」的可复现竞态（收口失效）。
- 出现第二条需要通用编排的自动流水线，且固定协调器无法表达。

## 若此决策错误，修复成本是什么

**中等。** 触发点换回离章是一次接线；但「快照 + generation + CAS」这套
并发收口换掉会更贵——它同时是抽取防复活（Task 9）、总结作者优先
（Task 6）、别名纠错重放（Task 11/12）的地基。地基错了不是改一个开关，
是要把五条链的生效条件全部重过一遍。
