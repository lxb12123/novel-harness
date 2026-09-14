# ADR 0053：失败的单有限次自动重试

- **状态**：已接受（2026-09-14，维护者：「为什么异常的还有这么多」）
- **日期**：2026-09-14
- **推翻了**：Task 16 的一条纪律——「同 basis 已有终态 FAILED/BLOCKED 的 coverage attempt →
  `attention_required`，不自动重付；只有显式『重新整理』另建 manual intent」。那条手动入口
  2026-08-25 随命令行面一起删了，于是这条纪律的另一半（作者有路可走）早就不成立。
- **相关**：[ADR 0052](0052-thinking-off-by-default-author-budget.md)（红了一片的根因）、
  [ADR 0045](0045-conflicts-only-when-the-machine-overrules-the-author.md)（不把机器的账推给作者）。

## 真书上的那一天

ADR 0052 修好「思考吃空预算」之后，722 章里 31 章仍然红着——它们的单在旧版本上 FAILED 过，
按 Task 16 那条纪律永远 `attention_required`：新版本一张单都不会再下，芯片悬浮说明里那句
「将自动重试」说得比做得多，作者只能等正文换版本。同一天还查出两条同源的病：

- 抽取那一支 `enqueue()` 不带 `force`、`run()` 的结果不看：上次 FAILED 的 run 原样交回、
  `run()` 领不到直接返回，这一支却报 SUCCEEDED——调度器每一轮都判「抽取没跑」再下一张，
  单上这一支又什么都不做，**一轮 20 个名额里 18 个白占着**，前沿每半小时只推进 1～2 章。
- 每一张单的 `final_gate_state` 永远 PENDING（后台不接 alias adapter，`alias_phase` 停在
  NOT_STARTED），所以全部单每分钟被重新 claim 一次（`fencing_token` 到过 213）。不花钱，
  本 ADR 不动它——记在这儿是因为它和上面两条是同一种「终态不终」。

## 决策

1. **同一批缺口（同 run、同 epoch、同 mask）最多自动再下 `MAX_AUTO_RETRIES = 3` 张单。**
   `ensure_refresh_coverage`：最近一张还在跑 → 幂等复用；最近一张跑完了缺口却还在
   （FAILED，或报了成功而东西没落下来）→ 新的一行 `coverage:{mask}#{第几次}`，旧的原样留着；
   张数够了 → `attention_required`（红着不动，不占名额）。BLOCKED（规则拦下的）不重试：
   正文和规则集不变，再跑还是拦。迁移 039 把 018 那条「(run, epoch, kind, mask) 唯一」的
   索引改成非唯一——重试是新的一行，唯一键仍是 trigger_key。
2. **抽取那一支报什么 = 做什么。** `_ExtractionAdapter` 一律 `enqueue(force=True)`（只对
   FAILED 的 run 起作用，成功的照旧复用），跑完仍是 FAILED 就抛——这一支落 FAILED，
   走第 1 条的重试；够数了不再占名额。
3. **重试中的章不红、不通知。** 异常与通知按「最近一次 attempt」算，而一轮扫描先下单再对账：
   新那张是 PENDING。只有机器放弃了（够数）才把账推到作者面前——约束 7 的方向。

## 为什么是 3

3 = 三轮扫描（一个半小时）。失败的原因常常不在这一章身上（端点抖一下、预算不够、钥匙过期），
那种换一轮就好，一次就够；三次还不行的，第四次也不会行。计数把「报了成功而东西没落下来」
也算进去：那是旧版本留下的空单，不算的话每半小时还是白占一个名额。

## 代价

- 一个真坏了的模型 / 钥匙，每章最多多付 3 次；31 章 × 3 次 = 最多 93 次调用，按总结那一档
  几百 token 一次，是几分钱的量级。比「永远红着」便宜。
- 真书上第一轮：12 章红的重试总结 + 8 章旧空单真的重跑抽取，**20 个名额全是真活**
  （改之前是 2 真 18 空）。
- 悬浮说明「将自动重试」在够数之后仍然这么说——那时它是假的。要说实话得把「重试用完了」
  送到视图那一行上，另一条待办。

## 落地

- `chapter_refresh.py`（`MAX_AUTO_RETRIES`、`ensure_refresh_coverage` 的重试分支）、
  `migrations/039_coverage_retry_index.sql`、`api/background_runtime.py::_ExtractionAdapter`、
  `summary_schedule.py`（两处注释）。
- 守卫：`tests/test_chapter_refresh.py`（三张之后 `attention_required`、BLOCKED 不重试、
  在跑的幂等复用）、`tests/test_book_summary_status.py`（重试中不通知、够数才通知）、
  `tests/test_autonomy_runtime.py`（抽取那一支 force + 抛）、`tests/test_migrate.py`（039）。
