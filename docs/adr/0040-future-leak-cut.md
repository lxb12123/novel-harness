# ADR 0040：R2 FUTURE_LEAK 砍掉 —— 它的输入从来没有入口能填

- **状态**：已接受
- **日期**：2026-08-27
- **推翻了**：不是原始需求文档，是**本仓库自己**——`checks/future_leak.py`、
  `checks/catalog.py::SYSTEM_RULES` 里的 R2 `RuleSpec`、`ALL_CHECKS` 从 2 条变 1 条，
  以及它们背后「未登场实体提前出现该由规则拦」这条设计。
- **相关**：[ADR 0005](0005-set-judgment-only.md)（规则只做集合判断，那张表原来有 R2 一行——
  这份文档正文不改，R2 变成历史记录）、[ADR 0004](0004-declaration-over-extraction.md)
  （伏笔是作者意图，不由文本特征定义）、[ADR 0014](0014-r5-cut-by-quote-coverage.md) /
  [ADR 0027](0027-scene-blocks-cut.md)（R5、R4 死于同一类判据的更早两次）、
  `docs/M3_GATE_PROTOCOL_AMENDMENT_1.md`（M3 门槛因这一刀同步开的修正案，
  改的是那份协议，这份 ADR 只管规则本身）

## 决策

**`checks/future_leak.py`（R2 `FUTURE_LEAK`）整个删掉，连同它在 `ALL_CHECKS`
和 `checks/catalog.py::SYSTEM_RULES` 里的登记。** 迁移 031 把这次目录语义变化
带来的新 `ruleset_epoch` / `ruleset_hash` 写回所有既有项目。

**没有删的**：

| 东西 | 现状 |
|---|---|
| `node.props.first_appears_chapter` 字段 | 保留。`declare_first_appearance()` 还在写它 |
| `declare_first_appearance()` | 保留，字段唯一的写入方 |
| R3 `DEAD_SPEAKS`（`dead_speaks.py:82`） | 保留，「未登场角色开口说话」那一半继续读这个字段 |
| `panel/constraints.py::forbidden_entities()` | 保留——删完 R2 之后它还剩一个消费者：
  右栏「本场景设定要点」那一格，走 `GET …/chapters/{n}/constraints`（`api/app.py`） |
| `docs/adr/0005-set-judgment-only.md` 正文 | 一字不改，R2 在那张表里作为历史记录留着 |

删的只是「拿 `first_appears_chapter` 开一条会在 `POST …/chapters/{n}/check`
上跑的规则」这一件事，不是这个字段或它的其他消费者。

## 为什么

### 1. 输入从来没有入口，规则结构上开不了火

R2 的判据是`node.props.first_appears_chapter = K > 当前章 N`，而正文出现了
该实体的可用称呼。这个判据本身没有问题——两侧都是作者自己敲进去的字，
集合判断，零语义（同 R3 的「作者声明 vs 作者声明」）。

问题在输入路径：`first_appears_chapter` **从来没有一个浏览器入口能设它**。
那个输入框 2026-08-13 就是有意裁掉的（`StateCards.tsx` 里的注释指着同一条线，
`docs/ARCHITECTURE.md`「工作台的已知洞」记着）。R2 因此从 2026-08-02 落地起，
在真实产品上就是`ctx.forbidden_names` 恒为空 → 循环一次都不进 → 恒返回 `[]`。

**「零误报」在一条永远跑不起来的规则上是免费的**——这句话在 [ADR 0014](0014-r5-cut-by-quote-coverage.md)
（R5，显式说话人标签真书覆盖 8.2% < 10%）和 [ADR 0027](0027-scene-blocks-cut.md)
（R4，场景块真书覆盖 0%）里各写过一次。R2 是第三次，且是这三条里**最彻底**的
一次：R4/R5 至少还有「作者理论上可以手写标记」这条（虽然覆盖率证明他不会），
R2 连「理论上可以」都没有——没有输入框，作者physically 敲不进这个值。

### 2. 「未登场的东西未来哪一章出现」本来就不是一件事实

维护者的裁定原话：**「未登场的东西未来到底哪一章出现，本来就不一定」**。
`first_appears_chapter` 记的不是已发生的事，是作者的**计划**（同 R2 判据本身
论证过的逻辑：墙上那把枪是不是伏笔，取决于作者第 200 章打不打算开枪，
这个信息物理上不存在于已写文本里——ADR 0004）。要求作者预先把一个还没写完、
还可能改主意的计划填进表单，跟约束 6「不让作者填章号」是同一个方向：
**能从正文算出来的东西才该做成表单外的推导，需要作者预先承诺的东西不该做成
一个不填就报错的字段。**

### 3. 这不是一次「考砸了才删」的决定

R2 从 2026-08-02 落地、测试绿到今天，`docs/M3_GATE_PROTOCOL.md` 预注册的
合成小册子门槛（25/25）也在 2026-08-02 就通过了、M4 已经在这个通过判定之上
解锁并落地。删 R2 不是因为它没通过某个门槛——是产品裁定「这个能力今天用不上，
留着一条永远开不了火的规则不如删掉」。M3 那道门槛受到的影响记在
`docs/M3_GATE_PROTOCOL_AMENDMENT_1.md`，不在这份 ADR 里重复。

## 代价

- **右栏「本场景设定要点」那一格，作者提前提到未登场实体时不会再被规则拦一次。**
  `panel/constraints.py::forbidden_entities()` 还在算这份清单、右栏还在显示它——
  作者能**看到**「幽泉窟（第 200 章首现）」，只是正文里提前写出「幽泉窟」这个词
  不会再被 `POST …/chapters/{n}/check` 报成一条 issue。这是真的损失，但损失的是
  一个**从未在生产上生效过**的能力：`first_appears_chapter` 没有输入路径，
  这条规则在任何一本真实导入的书上都不可能开过火。
- **`ALL_CHECKS` 从 2 条变 1 条**，`docs/ARCHITECTURE.md`「当前状态」的规则数、
  `scripts/demo.sh` 的心跳断言、`tests/test_doc_numbers.py::test_demo_pins_the_
  real_rule_count` 跟着改——这三处已经在这次改动里一起更新，不是遗留待办。
- **`docs/M3_GATE_PROTOCOL.md` 预注册的 25 题门槛，10 题继续有效、15 题永久失去
  测量对象。** 详见 `docs/M3_GATE_PROTOCOL_AMENDMENT_1.md`：门槛本身在 R2 还活着
  时已经通过，这次改动不追溯改判那次通过，只改此后这份协议还测什么。

## 什么时候该重新考虑

**触发条件是「浏览器上长出一个能设 `first_appears_chapter` 的入口」，不是
「作者说他想要这条规则」。** 单独问后者会得到「是」——没有人会拒绝一条免费的
安全网；但那正是这条规则第一次被砍的理由的反面：**能力要接得住，不是喊出来的**。

那个输入框当初被裁掉，理由是「表单里有章号输入框 = 邀请污染」（约束 6）——
`first_appears_chapter` 本质就是一个章号输入。这条裁定和「R2 需要作者能填这个
字段」这个能力，**今天互相锁死**：只要「不让作者填章号」这条约束不松动，
就不会有本 ADR 说的那个入口，R2 也就没有值得复活的理由。真要两者都要，
需要先回答「这个章号输入框和其他被约束 6 挡住的输入框有什么本质不同」——
那是另一个决定，不是这份 ADR 的范围。
