# ADR 0043：事实只记起点，区间在读的时候算 —— 倒着补全不该炸

- **状态**：**已接受**（2026-09-06，维护者审过方案后裁定「行动」）。已落地，
  下面「要改哪些地方」那张表里的每一项都做完了；实际范围比提案里估的**小**，
  见文末「落地后补记」。
- **日期**：2026-09-06
- **推翻了**：不是原始需求文档，是**本仓库自己**——「闭开区间 `[valid_from, valid_to)`
  是事实的存储形态」这条设计，以及 §5.9 supersede「只进不退」那条纪律的**实现方式**
  （纪律本身不推翻，见下面「没有推翻什么」）。
- **相关**：[ADR 0006](0006-evidence-double-pointer.md)（证据双指针 + STALE 不进队列，
  `evidence_status` 那一条件不动）、[ADR 0008](0008-related-to-is-undirected.md)
  （`RELATED_TO` 无向、写入时规范化 —— 分组键要照抄它）、
  [ADR 0020](0020-clean-extraction-auto-canon.md)（干净抽取自动升 CANON，这条路正是
  被本 bug 卡死的那条）、`docs/ARCHITECTURE.md` §10 约束 3（时态过滤全系统只实现一次）

---

## 一句话

**`edge.valid_to_chapter` 是一个纯派生值，而把它提前存进行里，要求事实必须按章号顺序
到达。补全队列倒着跑，这个前提就破了。** 所以不再存它，改成读的时候算。

---

## 事实（可查证，不是判断）

### 1. `valid_to_chapter` 全仓只有一个写入方，且它永远写「下一条事实的起点」

```
queries.close_edge(conn, edge_id, valid_to)      ← 唯一会写入一个数的函数
  └─ 唯一调用点：sqlite_store.upsert_edge:400
       queries.close_edge(self._conn, old.id, spec.valid_from_chapter)
                                                  ^^^^^^^^^^^^^^^^^^^^^^^
```

另一处 `UPDATE edge SET … valid_to_chapter = NULL` 是 `restore_retracted_edge`
（A→B→A 恢复旧行），它只往回抹。

**作者面上没有「这条事实到第 X 章为止」这个动作**，`declare.py` 里没有，路由里也没有。
所以库里每一个非空的 `valid_to_chapter`，都恰好等于「同一个 (project, src, type[, dst],
scope) 分组里下一条事实的 `valid_from_chapter`」。

**它算得出来，所以存它是缓存。**

### 2. 缓存派生值的前提是「按顺序到达」，而补全队列故意不按顺序

`summary_schedule.scan_chapter_summary_state` 的排队键：

```python
key=lambda item: (-item.weight, abs(draft_chapter - item.chapter_number), item.chapter_number)
```

权重按「离作者正在写的那一章多近」给，于是一本写到第 158 章的书，补全顺序是
158 → 157 → 156 → …。**这个设计是对的**（保证作者手上那一章有料，见 ADR 0036），
本 ADR 不动它。

### 3. 两者相撞的实测结果（真书 `book.db`，2026-09-05）

| | |
|---|---|
| 分析失败 | **62 章 / 73 次运行**（50 次 `ingest_failure` + 23 次 `analysis_format`） |
| 有事件的章 | **11 章**（28、124、129、140、146、147、149、151、155、156、158） |
| 作者看到的 | 切到第 53 章，「事件」那一栏只有第 28 章那 7 条 |

重跑第 3 章拿到的原因（异常原文，此前被 `except Exception` 吞掉）：

```
SupersedeConflict: 乱序插入：已有 LOCATED_AT 边 edge:… 的 valid_from=156
晚于新边的 3。v1 的 supersede 只进不退……猜错的产物是重叠区间，所以宁可抛
```

对应的真实数据：

```
贾环 --LOCATED_AT--> 观音寺   [156, ∞)   CANON   ACTIVE
```

第 3 章的位置事实要进来时，`upsert_edge` 只有三条路，前两条都是坏的：

1. 关掉旧的 → `[156, 3)`，倒着的区间，`001_init.sql:255` 的 CHECK 直接拒；
2. 不关直接并排 → 第 156 章之后贾环有两条 ACTIVE 位置边，「他第 160 章在哪」有两个答案；
3. 把新的写成 `[3, 156)` → 机械上可行，但要先回答「第 156 章那条结束之后，第 3 章那条
   要不要恢复」——那个问题今天没有消费者，猜错的产物是重叠区间。

所以它抛。**而 `_ingest_success` 用一个 `BEGIN IMMEDIATE` 写整章，一抛就
`conn.rollback()`——那一章的 7 条情节、在场名单、知情名单一起没了。**
这就是「62 章一条事件都没有」而不是「少了一条位置事实」的原因。

---

## 决策

### 1. `edge` 不再存 `valid_to_chapter`

每条边只记 `valid_from_chapter`（= 证据出现在第几章）。

### 2. 排他类型的「第 N 章生效的是哪一条」在**读的时候**算

分组键照抄 `find_conflicts` 今天的口径，一个字不改：

| exclusivity | 分组键 | 谁 |
|---|---|---|
| `single_per_src` | `(project, src, type, scope)` | `LOCATED_AT` |
| `single_per_src_dst` | `(project, src, dst, type, scope)` | `HAS_STATE`、`RELATED_TO` |
| `multi` | 不分组，全给 | `MEMBER_OF`、`OWNS`、`PLANTED_IN`、`RESOLVED_IN` |

「第 N 章生效的那条」 = 组内 `valid_from_chapter <= :ch` 中**最大**的那一条。
`TEMPORAL_WHERE` 其余四个条件（`information_scope` / `status='ACTIVE'` /
`evidence_status != 'STALE'` / `valid_from <= :ch`）**一个都不动**。

### 3. 同章更正（`RETRACTED`）原样保留

「他在青云城…… 然后他去了北荒」都在第 151 章 → 前一条 `status='RETRACTED'`。
这一档今天就靠 `status`，新模型里照旧靠 `status`，判据不变。
`find_conflicts` 因此不删，只收窄成「找同一组里 `valid_from == spec.valid_from` 的那条」。

### 4. `SupersedeConflict` 整个删掉

连同 `api/app.py:392` 那个 exception handler。**乱序不再是一种错误状态**，
它只是「多了一条更早的观察」。

---

## 没有推翻什么（这一节比上面更重要）

| 东西 | 现状 |
|---|---|
| **§5.9「supersede 只进不退」这条纪律** | **没推翻。** 它说的是「后来的事实盖住先前的」，而这一点在新模型里由「取 `valid_from` 最大的那条」表达 —— **同一条纪律，换了一种不需要按顺序写入的实现** |
| 注入算法（写正文时带哪几章的事件/总结） | **一个字不改。** `product_context.build_product_context` 照旧：先砍掉未来章，近的全给，远的按预算 `_take_from_newest` 从最新往前拿 |
| 补全顺序（最新往前倒） | **不改。** 只是从「硬性要求」变成「偏好」——顺序不再影响结果对不对 |
| `story_event`（情节表） | **不动。** 它本来就只有 `chapter_number`，没有区间，天生 order-independent |
| 分层隔离（PROVISIONAL 不许闭合 CANON） | 不动，分组键里带 `scope` |
| 无向边规范化（ADR 0008） | 不动，分组键吃的就是规范化后的 `(min,max)` |
| `evidence_status != 'STALE'`（ADR 0006） | 不动 |

---

## 要改哪些地方

**估计一天以内，但每一处都要人看，不能批量替换。**

| 文件 | 改什么 |
|---|---|
| `graph/queries.py` | `TEMPORAL_WHERE`（**7 处真的内插进 SQL，逐个判**：其中几处读的是「他这一路都经历了什么」，只用五个条件里的三个，不受影响）；`close_edge` 删；`find_conflicts` 收窄成同章 |
| `graph/sqlite_store.py::upsert_edge` | supersede 那一段（约 30 行）改成「只处理同章更正」 |
| `graph/store.py` | `upsert_edge` 的契约 docstring —— 那是这套语义的权威说明，要重写 |
| `graph/models.py` | `Edge.valid_to_chapter` 字段；`EdgeSpec` 那句「传 `valid_to_chapter=143` 会毫无声息地什么都不做」的警告可以删了 |
| `api/app.py` | `SupersedeConflict` 的 import 和 exception handler |
| `calibration/calibrate.py` | 4 处读 `edge.valid_to_chapter`（构造校准视图）→ 改成派生或直接去掉 |
| 迁移 036 | 清空 `valid_to_chapter`；重建 `edge` 表去掉那一列和 `CHECK (valid_to > valid_from)` |
| `tests/test_arch_guard.py` | 三道守卫的判据里有「时态过滤只此一份」，措辞要跟着改 |
| `tests/test_store_conformance.py` + 两份 `FakeGraph` | 一致性规格里的区间断言 |

---

## 代价与风险

1. **动的是 §10 约束 3**（时态过滤全系统只实现一次）。这条约束**不取消**，
   但它守的那份 SQL 要重写一遍 —— 而这个仓库对这一处的纪律是最严的（三道守卫）。
   **这是本方案最大的风险，不是技术上的，是「谁来复核」上的。**

2. **排他类型的读从「一个 WHERE」变成「分组取最大」**，SQL 复杂一点（窗口函数或
   相关子查询）。158 章的书上性能不是问题，但那 7 处 `TEMPORAL_WHERE` 得逐个判
   「这一处是不是排他读」——**批量替换一定会错**。

3. **丢一样今天不存在的能力**：「这条事实在第 X 章结束了，而且没有后继」。
   今天也表达不出来（唯一的写入方永远拿后继的起点当终点）。真要它，加一个显式的
   「终止观察」——那本身也只是一条观察点，不破坏新模型。

4. ~~⚠️ **`canon_edge_override`（作者覆盖层）我还没查它怎么和区间交互。**~~
   —— **落地前查了：不受影响。** 那张表上一个区间列都没有，它按
   `slot_key` 挂在边上，而 `canon_edge_slot_key()` 只吃 `valid_from_chapter`
   （location=`subject+type+valid_from`，state 再加 `dim_key`，relation 用规范化后的
   点对）。`valid_to` 从来没进过那个键。

---

## 若此决策错误，修复成本

**低，而且这是它最好的性质：这个改动是可逆的。**

`valid_to_chapter` 是派生值 → 任何时候都能按章序重算一遍写回去。

- 迁移分两步：**036 只清空不删列**，跑一阵子确认没问题，再开 037 删列。
  在 037 之前回退 = 重算一遍写回去，一条数据都不丢。
- 037 之后回退 = 加回列 + 重算，仍然算得回来。

真正不可逆的只有一样：**删掉 `SupersedeConflict` 之后，「乱序」不再报警**。
如果哪天发现有一类乱序其实是数据错误（而不是补全顺序），我们会静默接受它。
—— 缓解：新模型下「同组内两条 `valid_from` 相同且都 ACTIVE」是不变量，
可以在 conformance 测试里钉住。

---

## 改完之后那 62 章

**要重跑才能把事件补回来**，约 150 次模型调用（每章一次抽取；总结那一半已经补完了）。

跑法两种都对（这正是本 ADR 的目的）：

- 顺着 1 → 158：语义上最干净；
- 照旧倒着 158 → 1：和现在的调度一样，**改完之后结果一模一样**。

建议照旧倒着 —— 不为别的，那样这次改动就顺带**证明了它自己**：同一条队列、同一个顺序，
改之前 62 章失败，改之后应该 0 章失败。

---

## 验证计划

1. **反向验证（先写测试，看它红）**：先 upsert 一条 `LOCATED_AT [156,∞)`，
   再 upsert 同一个 src 的 `[3,∞)`。今天：`SupersedeConflict`。
   改完：两条都在，且 `state_at(160)` 仍然是第 156 章那条、`state_at(10)` 是第 3 章那条。
2. **顺序无关**：同样两条边，正着写一遍、倒着写一遍，`state_at` 在 1…160 每一章上
   逐章比对，两次结果必须逐格相等。
3. `tests/test_store_conformance.py` 两个后端全跑（真库 + FakeGraph）。
4. 真书上重跑第 3 章，`extraction_run` 落 `SUCCEEDED`，事件数 > 0。
5. **注入没变**：`product_context` 那一批测试一条都不许改 —— 改了就说明我动到了不该动的。

---

## 落地后补记（2026-09-06）

**实际范围比提案估的小，小在一个当时没查的地方**：`valid_to_chapter` 这一列虽然
`story_event` / `event_knower` 上也有，但**那两张表从来没有人写过它**（真库里 168 条
事件、280 条知情记录，`valid_to_chapter` 全是 NULL，全仓也找不到写入方）。
所以 `TEMPORAL_WHERE` 那五个条件**一个字都没改**，改的只有 `edge` 的两处读
（`edges_at` / `edges_for_nodes`）。提案里「7 处逐个判」的活儿，实际是 2 处。

**一个提案里没写、实现时才发现的决定：`RANK()` 而不是 `ROW_NUMBER()`。**
同一语义槽里两条 `valid_from` **相同**且都 ACTIVE 是坏数据（同章更正本该把前一条标成
RETRACTED），`ROW_NUMBER()` 会闷声挑一条、把数据层的 bug 变成屏幕上一条看不出来的错
事实。`RANK()` 让它们并列返回，于是 `sqlite_store` 那道「一个槽拿到两条互斥边就炸」的
守卫照旧开火。**这一个词是本次改动里唯一一处「安全网没跟着搬家就会静默消失」的地方。**

**跟着删掉的（都是删之前就已经恒为空的东西）**：`UpsertResult.closed`、
`Declaration.closed`（全仓零读者）、`queries.close_edge`、`SupersedeConflict` 及
`api/app.py` 里它的 exception handler（`supersede_conflict` 那个错误码随之消失，
文档里的「错误映射数」19 → 18）。`find_conflicts` 更名为
`find_same_chapter_conflicts` 并收窄。

**测试的改法一律是「换断言不换纪律」**：凡是断言 `res.closed` / `valid_to_chapter`
的地方，全部改成断言**行为**（`located_at` / `state_at`）——那本来就是真正要钉的东西，
从前那些断言钉的是当年那套实现的出参。两条测试因此反了过来，各自在 docstring 里
写清了「原来钉什么、为什么反」：
`test_supersede.py::test_a_later_fact_does_not_block_an_earlier_one`（原
`test_out_of_order_raises_instead_of_guessing`）和
`test_declare.py::test_a_second_location_takes_over_without_touching_the_first`。

**新增的两条是本 ADR 的验收本身**：
- `test_a_later_fact_does_not_block_an_earlier_one` —— 先写第 151 章再补第 10 章，
  两条都进得去且逐章答案正确；
- `test_the_answer_does_not_depend_on_which_order_the_facts_arrived` —— 同样四条事实
  正着写一遍、倒着写一遍，1..200 每一章逐格比对。

**真书验证**（`book.db`，2026-09-06）：迁移 036 跑过（`user_version=36`，
`valid_to_chapter` 非空行 0 条）。挑三章 2026-09-05 死于 `SupersedeConflict` 的重跑：

| 章 | 改之前 | 改之后 |
|---|---|---|
| 4 | `ingest_failure`（乱序），0 条事件 | **SUCCEEDED，10 条事件** |
| 5 | 同上 | **SUCCEEDED，7 条事件** |
| 3 / 6 | 同上 | `analysis_format` —— **另一类失败**（模型这一次没吐出合规 JSON），和本 ADR 无关 |

于是 `贾环` 的位置边**同时**躺着第 4、5、156 章三条（写入顺序是 156 → 4 → 5，
正是生产上补全队列的倒序），走真 HTTP 逐章问：

```
第 4 章：醉香楼      第 10 章：荣国府
第 100 章：荣国府    第 158 章：观音寺
```

**这就是整份 ADR 要的那句话**：更早的事实补得进去，更晚的事实照旧盖在上面，
而两者谁先写进库不影响任何一章的答案。

**没解决的那一半**：`analysis_format`（模型返回的不是合规 JSON）是**另一条线**，
2026-09-05 的 73 次失败里有 23 次是它。本 ADR 不碰它，但那一档的诊断今天也留得下来了
（同日把三个 `except Exception` 改成带上异常本身）。
