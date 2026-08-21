# ADR 0035：换章 autopilot 整块砍掉 —— 一块在合并那天才发现已经没有用户的表面

- **状态**：已接受
- **日期**：2026-08-20
- **推翻了**：不是原始需求文档，是**本仓库自己**——`api/autopilot.py` 那份模块 docstring
  写下的整套「换章即后台」设计，以及它在 `UI_ARCHITECTURE.md` §「触发点」里那段成本论证。
- **相关**：[ADR 0029](0029-save-triggers-snapshot-refresh.md)（保存键触发快照刷新——**它才是
  真正推翻这套设计的那一份**，本 ADR 只是把尸体清掉）、
  [ADR 0027](0027-scene-blocks-cut.md) / [ADR 0034](0034-no-command-line-surface.md)（同一种刀法：
  一块没有用户的表面，留着就是负债）、[ADR 0021](0021-agent-writes-drafts-without-asking.md)
  （「退路是版本历史」——本 ADR 修的那个 bug 正是在拆作者的退路）

## 决策

**删掉 `POST/GET /api/projects/{pid}/chapters/{n}/autopilot` 两条端点及其整条实现链**：

| 层 | 删掉的东西 |
|---|---|
| 后端 | `api/autopilot.py`（`Dispatch` / `Readiness` / `AutopilotDispatch` / `AutopilotStatus` / `_JobRegistry`）+ `app.py` 里那两行挂载 |
| 后端测试 | `tests/test_autopilot_api.py`、`tests/test_autopilot_reads_disk.py` |
| 前端 | `types.ts` 的 5 个导出（`AutopilotError` / `AutopilotTask` / `AutopilotAck` / `AutopilotStatusState` / `AutopilotStatus`）、`hooks.ts` 的 `useRunAutopilot` / `fetchAutopilotStatus`、`test/harness.tsx` 的两条 stub |
| 契约 | `test_frontend_contract.py` 的 dump 段 + fixture 里 `autopilotDispatch` / `autopilotStatus` / `autopilotStatusRetracted` 三条（端点数 92 → 89） |

## 为什么

**它在合并那天已经没有调用方了，而没有人发现。**

并行的保存闭环任务在 Task 16（2026-08-17）就把前端那一半摘掉了——`frontend/src/autopilot.ts`
删除，换成 `chapterNavigation.ts`，理由写在那个文件里：

> 换章发一次 autopilot 是「双重 autopilot」的旧设计——作者切走而已（也许根本没保存）就凭空
> 付费总结/抽取。删掉这半边之后，付费动作只由保存/Ctrl-S 触发。

但**后端那一半留下了**：模块还在、路由还挂着、契约 fixture 还钉着它的出参。codex 自己的
`focus.py` 已经把它称作「**被删除的**旧 autopilot」——它是按删掉写的，只是文件没跟着走。

于是 2026-08-20 合并两条线时，仓库里同时存在：一条零调用方的付费入口、一套没有任何组件
使用的前端类型，以及**上一轮刚刚为它精心加固过的契约钉子**（`2dbcfad`「AutopilotTask 扩到
7 值 + autopilot 端点进契约 fixture」）。那笔改动本身没错——它做的时候那条端点确实是活的；
是并行任务把它的用户拿走了，而两条线各自都不知道。

**这正是 CLAUDE.md「废弃即删，不留备用」要防的形态**：一块没人用的表面不会有人发现它坏了，
只会在某天有人依赖它时坏给他看。

## 删的时候发现的那个 bug（本 ADR 最重要的一段）

`api/autopilot.py` 的 docstring 里写着一条纪律：

> **作者撤回过的章一律不派**（`retracted`）。判据是 `SummaryStore.latest()` 而不是 `get()`：
> 后者对撤回过的章回 None，也就是「还没生成」——于是他撤掉一份、切走一章，后台立刻替他买
> 一份回来，**顺带把他刚做的动作抹掉**。

`ARCHITECTURE.md` 也把这条抄进了滚动总结那一节，并明说新的保存触发链「拿 `latest()` 判要不要
派活」。**但代码不是这么写的**：`chapter_refresh._head_missing` 的判据是
`status = 'ACTIVE'`，于是 RETRACTED 被当成「缺总结」——**作者撤掉一份总结，下一次保存就被系统
重新买一份回来**。实测复现，且没有任何测试盖着。

为什么没人发现：**这条纪律唯一的测试
（`test_background_tidying_never_buys_back_a_retracted_summary`）走的正是 autopilot 端点**。
前端不再调它之后，那条测试还绿着——它测的是一条已经没有人走的路。

所以这一刀连着修了三件事：

1. `_head_missing` 改成只问「有没有 head」，撤回过的章**不算缺**；
2. 那条端到端测试改走保存路径（`PUT …/text` → `_trigger_refresh`），断言「保存之后没有一条
   总结分支的活被排出去」；
3. 补一条决策层单测 `test_a_retracted_summary_is_not_bought_back_by_the_next_save`。
   两条都做过反向验证：把判据改回 `ACTIVE`，两条都红。

**和 `SummaryStore.coverage()` 不矛盾**：那边是给界面看的读端，撤回后照旧显示「这一章没有
总结」（作者要看得见自己撤了）。`_head_missing` 回答的是另一个问题——**系统该不该自己掏钱补
一份**。答案是不该；想要新的一份，他自己点「重新生成」。

## 它写下过的纪律搬去了哪

删一块表面最贵的不是代码，是它 docstring 里那些**用故障换来的话**。逐条交代：

| 原来写在 `api/autopilot.py` | 今天住在哪 |
|---|---|
| 撤回过的章不许被自动买回来 | `chapter_refresh._head_missing`（+ 两条测试） |
| `coverage()` 只问「有没有」不问「新不新」 | `SummaryStore.coverage()` 自己的 docstring（`agent/index.py` 两处引用同步改指） |
| 失败不许静默（进程内 `_JobRegistry` 留痕） | `chapter_refresh_attempt` 的持久状态——**比原来强**，重启不丢 |
| 自动链路不自动重试失败的抽取 | `ensure_refresh_coverage` 的 `attention_required` |
| 总是 202、真相放回执体里 | 不再需要：保存那条路本来就有回执，且它不是「无人值守的换章」 |

## 若决策错误，修复成本

**低。** 代码在 git 历史里；`RollingSummarizer.ensure` / `ExtractionRunner.enqueue` 两个被它
调用的库函数一个字没动，重新包一层 HTTP 是几十行。

但**加回来之前先回答一个问题：谁触发它？** 如果答案是「换章」，那 ADR 0029 已经论证过为什么
不行（作者切走 ≠ 写完了，且和保存构成双重付费）。如果答案是「保存」，那今天已经有了。
**没有第三个答案时，别加。**
