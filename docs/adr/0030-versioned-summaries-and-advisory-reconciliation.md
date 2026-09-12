# ADR 0030：三类总结 append-only 版本化、作者优先，核对只告警不阻断

- **状态**：已接受
- **日期**：2026-08-17
- **推翻了**：**本仓库自己**——「当前滚动总结 = 按 rowid/created_at 猜最新」的
  隐式做法（`draft/rolling_summary.py::_latest_per_chapter`），以及
  「总结核对会进入 `checks/` / 能阻断总结」这个**从未被写下来**的假设。
  同时对 [ADR 0005](0005-set-judgment-only.md) 开一个**窄例外**：
  总结与正文/证据的语义核对是 LLM 能力，但它只写系统通知，
  不进入 `checks/`、不阻断总结、不修改 Canon。
- **相关**：[ADR 0005](0005-set-judgment-only.md)（集合判断铁律 + 窄例外）、
  [ADR 0029](0029-save-triggers-snapshot-refresh.md)（保存触发刷新）、
  [ADR 0020](0020-clean-extraction-auto-canon.md)（自动 Canon 的可查可改）

## 决策

**三类总结（章节滚动总结、待确认情节摘要、Canon 情节摘要）统一走
append-only 版本 + 显式 head；机器结果先写任务审计，CAS 成功才投影成
ACTIVE 版本；作者编辑立即追加 AUTHOR 版本并切 head；「总结可能与正文
不一致」只通过统一系统通知提醒，永不控制总结是否可用。**

核心机制：

- `chapter_summary_head.chapter_id PRIMARY KEY` 每章一行，
  `current_summary_id` 可指向 ACTIVE 总结、RETRACTED tombstone，首先生成
  前为 NULL。head 只由显式 CAS 更新：

  ```sql
  UPDATE chapter_summary_head
  SET current_summary_id = :new_id, updated_at = strftime(...)
  WHERE chapter_id = :chapter_id
    AND current_summary_id IS :expected_head
    AND machine_intent_seq = :required_machine_intent_seq;
  ```

- 机器总结在创建 job 的同一事务冻结 `expected_head_version_id` 与
  `required_machine_intent_seq`；worker 只读冻结值。运行中作者编辑 ⇒
  head CAS 必失败，机器晚到只留 `summary_generation_result` 审计。
- 滚动总结撤回不是删行：追加 `status=RETRACTED, summary=NULL` 的 AUTHOR
  tombstone 并让 head 指向它。ACTIVE 行要求非空 summary。
- Writer 的当前章节记忆永远非空：ACTIVE head 返回总结；无 head 或
  tombstone 返回有界 raw-text fallback，并明确 `kind=raw_text_fallback`。
  不把原文谎称成模型总结。
- 「作者优先」是针对已经排队/运行的机器任务，不是永久 pin：作者编辑会
  击败此前任务；以后正文产生新 generation 时以该作者版作为新的 expected
  head，新正文的机器总结可以正常替换它。
- 核对（reconciliation）只产生结构化、可定位的结果，`possible_conflict`
  才创建冲突通知；`supported` 只解决旧通知。同一
  `summary_sha256 + source_sha256` 至多一条冲突通知，IGNORED/RESOLVED
  对该精确 hash 对是终态。

> **补记（2026-08-25 → 2026-09-05）：撤回之后，系统不补，作者按得回来。**
>
> 这一条原文只说了「撤回不是删行」——**它没说撤回之后还能不能再拿回一份**。
> 那个问题的答案改过两次，而两次都值得写下来，因为第一次改错了。
>
> **① 2026-08-25：手动那条整条删掉**（按钮 + 路由 + `create_manual_attempt`），
> 总结的触发只剩两个，都是系统自动的。而这两个都按设计**不碰撤回过的章**
> （「他删一次，系统别买回来一次」，那条纪律 2026-08-21 踩过一次坑之后有两层守卫
> 钉着），实测「撤回之后又改正文」也仍然不买回来。于是撤回**成了终态**——
> 当时把这写成「裁定，不是洞」。
>
> **② 2026-09-05：那不是裁定，是没算这一笔。** 后果是撤回过的章**永远**拿不回
> 机器总结，而作者手上唯一的动作是自己手打一段；更糟的是那十天里屏幕上三句话
> （占位符 + 两种空态 + 撤回确认）继续指着「点『重新生成』」，
> 一句不存在的话。维护者看到之后裁定把 `POST …/chapters/{n}/summary` 加回来
> （原话：「撤回了之后肯定这个底部要留一个重新生成的按钮供用户立即生成」）。
>
> **今天的答案，两侧分开说：**
>
> | | |
> |---|---|
> | **系统**（保存之后 / 每 30 分钟扫描） | **永远不补撤回过的章**，包括作者后来又改了正文。这一半一个字没改，也不许改 |
> | **作者** | 两条回头路，都由他发起：自己写一段（`PATCH …/summary`，不花钱），或者点「重新生成」（`POST …/summary`，跑一次模型） |
>
> **分界线是「谁按的、谁付钱」**，不是「撤回是不是终态」。
> 名单和三个必答问题钉在
> `tests/test_arch_guard.py::test_only_one_place_per_trigger_kind_can_buy_a_chapter_summary`。
> ⚠️ **`POST …/summary/regenerate` 没有回来**：它是一条死路（往
> `summary_generation_job` 写 `status='PENDING'` 然后回 `queued=True`，而全仓没有
> 任何一处认领章节的 PENDING job）。回来的只有 `POST …/summary` 一条。


## 为什么

### 1. 版本历史是「机器总结可被质疑」的唯一退路

机器总结会错。作者改对之后，如果旧机器总结那一行被原地覆盖，
「当时系统给的是什么、作者改成了什么」就永远说不清了。append-only
版本 + 显式 head 让版本历史每一行都真的生效过，也让作者撤回本身
可追溯（tombstone 也是一版）。

### 2. 作者优先不需要锁，需要 CAS

「作者编辑立即生效、机器晚到不能覆盖」如果靠锁实现，作者编辑和机器
提交之间就有了共享可变状态。CAS 把竞争变成一次原子比较：期望 head
对不上就整体失败，只留审计。禁止查询「latest PASSED」也是同一件事的
另一半——那个查询会把 alias 生效前的初次验证报告误当最终闸门。

### 3. 核对是 ADR 0005 的窄例外，因为它的失败方向是「多一句提醒」

语义核对问的是「这段总结和正文一致吗」，这是语义判断。但它和 R2/R3
有本质区别：R2/R3 误报会让作者关掉规则，核对误报只是多一条通知。
它的全部权力是写一条**可忽略、可定位、可去重**的系统通知，不碰
summary head、不碰 event head、不碰 Canon、不碰 Writer 可用性。
「核对不阻断」因此是这条窄例外成立的前提。

## 代价（承认，不粉饰）

- 每次总结生效后都可能再花一次模型调用来核对，且核对失败也只能记
  `background_failure` 通知——它自己也是一个会失败的付费点。
- 版本表让总结读路径从「一条 SELECT 拿最新」变成「head → 版本」两步，
  所有读端都要改（Task 6/7/17）。
- 有界 raw-text fallback 是有意的诚实：首次总结完成前 Writer 拿到的
  不是总结。这会让 prompt 更长，但我们拒绝伪造一条「临时总结」。

## 什么条件下推翻本 ADR

- 核对误报率把系统通知变成纯噪声（作者开始忽略所有通知）。
- 出现「作者编辑被机器覆盖」的可复现竞态（CAS 失效）。
- 产品要求核对能阻断总结/Canon——那要另开一份完整 ADR 重议
  ADR 0005 的例外边界，不能悄悄把告警升级成闸门。

## 若此决策错误，修复成本是什么

**中等。** 版本/head/CAS 已经建成的话，退回「最新一行就是当前」需要
回填 head 并迁移读端；核对从告警升级为闸门是改结果权力，不是改机制。
反过来（先做无版本总结、后补版本）则要把所有已生成的总结历史迁移成
基线版本，且无法诚实地反推来源快照——所以新写入一律非空，
历史旧数据允许 `legacy + NULL`，但不假绑当前正文。
