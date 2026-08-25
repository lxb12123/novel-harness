# ADR 0039：秘密整套功能下线

- **状态**：已接受
- **日期**：2026-08-25
- **推翻了什么**：**这个项目的第一句话。** `CLAUDE.md` 开头写着「唯一一个知道『谁在第几章
  还不该知道什么』的引擎」，README 第一行是认知边界矩阵，PLAN §3.2 把它画成头牌面板。
  本 ADR 把那整条能力从引擎里删掉。
- **相关**：[ADR 0005](0005-set-judgment-only.md)（只做集合判断——`DOES_NOT_KNOW` /
  `PARTIALLY_KNOWS` 的删除论证挂在 `KNOWS` 上，那个承载体今天没了）、
  [ADR 0018](0018-cast-is-derived-not-declared.md)（§3 的单调性证明失去对象，见那份补记）、
  [ADR 0002](0002-no-vector-retrieval-in-v1.md)（论证 (b) 整节失效，见那份补记）、
  [ADR 0009](0009-m2-verdict.md)（M2 的裁决，作为历史成立）、
  [`docs/EVAL_PROTOCOL_RETIREMENT.md`](../EVAL_PROTOCOL_RETIREMENT.md)（那门考试的对象下线了）

## 决策

**删掉 `Secret` 节点、`KNOWS` / `BELIEVES` 边、认知边界矩阵，以及一切只为它们存在的东西。**

维护者裁定，原话：

> 「这个秘密我觉得至少现在没有用，重要的还是上下文，这个秘密只是完善。」

**这是一次产品裁定，不是一次数据裁决。** 它的依据是「作者今天要的是上下文，不是这个」，
**不是**「真书上这一格是空的所以删了不心疼」——那句话成立的原因是死锁没解开，不是秘密没用。
把它记成后者，等于给一条产品判断编一个数据依据。

## 删了什么

按切的顺序（六刀，六笔提交）：

| | 走的东西 |
|---|---|
| 一 | M2 那整套仪器：`eval/{leak,score,confound_lint,runner}.py`、`gate.py`、`synth/` 的秘密那一半、`synth/leak_selfcheck.py`、`synth/ground_truth.json` |
| 二 | 前端：`KnowledgeMatrix.tsx`、`/canon/knowledge` 的两个 hook 和它们的类型、抽屉里的「内容 / 所属秘密」两格、`Secret` 这一类 |
| 三 | `panel/knowledge.py`（`require_queryable_scope` 先搬进 `panel/scope.py` 保住）、`draft/assemble.py` 的矩阵与秘密块 |
| 四 | 声明链 `declare_knows` / `declare_believes`、改正链 `correct_knowledge` / `add_knowledge`、校准层的知识格 |
| 五 | 图的类型层：`NodeLabel.SECRET`、`EdgeType.{KNOWS,BELIEVES}`、`KnowledgeMatrix` / `KnowledgeCell` / `KnowledgeState` / `SecretDetail`、`knowledge_matrix` / `knowledge_edges_at`、以及 `revealed_facts` 那条限肢 |
| 六 | schema（028）：`secret` / `event_reveal` 两张表、`edge_type` 的两行、存量 Secret 节点与 KNOWS/BELIEVES 边 |

## 四处塌方：**留下来的东西，理由塌了**

这一节是本 ADR 最该被读到的部分。删掉一个功能容易，难的是**别让一段过期的辩护
继续替某样东西站岗**。下面四处的代码/决策都还在，但它们当年的理由已经不成立。

### 一、`SAFETY_FACT_TYPES` 现在是空集

`calibration/seal.py` 有一道闸：「安全相关的 RETCON 必须先走作者侧纠错才能封存」。
它的成员**只有** `FactType.KNOWS` 和 `BELIEVES`——两个都没了，**这道闸不再拦任何东西**。

**没有删它，空着摆在那儿 + 一段说明。** 空集和「这道闸不存在」是两件事：下一个人要
回答的是「今天还有没有一类事实，错了就必须先纠正再封存」（死人说话？未登场角色开口？），
而不是「为什么当初有人加了一道没用的闸」。**挑新成员是维护者的裁定。**

### 二、ADR 0018 §3 的单调性证明失去了对象

「cast 多算一个人 ⇒ 多禁一条 ⇒ fail-closed」——这条不对称整个消失了，因为
`SceneConstraints` 只剩 `forbidden_entities`，而它按章号算、与 cast 无关。
详见 [ADR 0018 的补记](0018-cast-is-derived-not-declared.md)。

**行为一处都没改**（`expand_ambiguous=True` 照旧、面板侧照旧不展开），因为改它是维护者的
裁定。`agent/tools.py::_derived_cast_from_text` 的 docstring 里写明了这件事。

### 三、ADR 0002 论证 (b) 整节失效

「『第 152 章谁不知道血脉秘密』是集合差集，任何 RAG 都检索不到」——那个例子没了，
连带塌掉的是「v1 不欠一个向量 RAG 对照组」这个推论。**决策不动，论证从三条腿变两条。**
详见 [ADR 0002 的补记](0002-no-vector-retrieval-in-v1.md)。

### 四、`UnknownCastConstraints` 不再是一个安全类型

`draft/context.py` 那两个类型（已解析 / 不知道在场是谁）原来是靠「空 cast ⇒
`must_not_reveal` 退化成全部秘密 ⇒ fail-closed」正当化的。**空 cast 今天不让任何东西退化。**

两个类型留着，理由换成一条更朴素也仍然真实的：**一份你没有的在场名单，不许拿空列表冒充着
发给模型**（`assemble` 拿到退化型时整个不发【在场】块）。docstring 已改写。

## 计划里两条错的前提（都在动手时撞出来）

### 一、`event_reveal` 不是「零反向引用」

它是 `revealed_facts` 那条限肢的存储，而那条限肢从抽取 prompt 一路连到前端类型。
秘密没了它无处可解（那些短句只往已声明的秘密上对），所以确实该走——但这是一次比
「删一张没人引的表」大得多的切除。

**`ANALYSIS_PROMPT_VERSION` 因此升到 v7**，而 prompt 正文变了 ⇒ `prompt_hash` 变 ⇒
`extraction_run` 的唯一键变 ⇒ **全书每一章都要重新调一次模型**。这一笔认了那个代价，
因为真书今天的抽取产物是零（死锁还没解开），重跑不损失任何已有结果。
**同样的动作在别的时候是「把全书重付一次钱」**（见 `0c02c50` 那次 ADVISORY_VERSION 回滚）。

### 二、M3 那张卷子不是「零秘密」

`m3_ground_truth.json` 的 6 个 `first_appears` 里有 5 个是 Secret 节点，
15 道 R2 题里 13 道的违例句写的是**它们的别名**。第一刀挖掉 `booklet.toml` 的
`[[secret]]` 时，**25 题当场变成 12 题，而 `test_m3_replay` 一路绿着**——
因为 `synth/gate.db` 不进 git，磁盘上那份是旧的。028 把它 migrate 一次才露头。

> **「产物不进版本控制」+「测试吃产物」= 一条断了三天没人看见的守卫。**
> 这条教训跟秘密无关，值得单独记住。

修法是把那 5 个节点放回去（新开一节 `[[entity]]`：非角色节点 + 别名，**不带首现章**，
label 用 `Foreshadow`）。**卷子一个字节没改**——首现章由 `m3_replay` 在读的时候按名字覆盖，
库里那一格必须空着，因为干净正文抽查明写着「不叠加 overlay，用真库」。

## 明确**没有**做的事（不是漏了）

- **`node.label` 的 CHECK 里还写着 `'Secret'`。** 改它要重建整张 `node`，而 `node` 被
  一大批复合外键引着。写入方全部走 `NodeLabel`，那一侧已经关死；028 负责清存量。
- **`decision_log` 一行不动**（三个触发器封死 DELETE），所以
  `DecisionKind.{SECRET_DECLARE,KNOWS_DECLARE,KNOWLEDGE_EDIT,KNOWLEDGE_ADD}` **标废不删**、
  `activity.py` 的措辞表留着那几行、`narrow_payload` 的第三条收窄规则留着——
  它今天罩的是 2026-08-25 之前那些 payload 里作者手打的秘密正文。
- **`PromptForm.X0` / `X2` 现在没有生产调用方了**（三臂是 M2 的东西）。收窄成一个 form
  是一次独立的改动，得先回答「`NH_DRAFT_FORM` 这个环境变量还要不要」——**范围之外。**
- **冻结资产一个字节没动**：`EVAL_PROTOCOL.md` 正文与 `0393088` 逐字节相同，九份修正案
  原样。守卫改成钉三件事（正文未变 / 九份都在 / 退役说明存在且指得到），
  见 `docs/EVAL_PROTOCOL_RETIREMENT.md`。

## 泄漏网少罩了一样（诚实交代）

`SecretDetail.description` 是**唯一一味不在 `props` 里**的毒——它逼着出参收窄按**结构**做
而不是按字段名做。今天作者写的东西只有 `node.props` 一个存放处，
所以 `test_story_wiki_leak.py` / `test_activity.py` 那两张网现在证明不了
「收窄对第二个存放处也管用」。

**再出现第二个存放处时（扩展表、侧车表、随便什么），得有人回来往这里加一味毒。**
两处测试的注释里都写了这句。

## 如果这个决策是错的

**症状**：作者开始手工维护「谁知道什么」——在章节总结里写「注意：此时李管家还不知道」，
或者反复问助手「第 152 章他知道了吗」。那说明这条能力是真需求，只是当时的形态不对。

**修复成本：高，但有路。** git 历史里有完整实现（本 ADR 之前的六笔提交逐刀记录了删的是什么）。
真要加回来：

1. **不许直接 revert。** 那六刀同时改掉了一批**跟秘密无关**的东西的措辞和载体
   （泄漏网的毒药、M3 的 `[[entity]]`、`test_fake_graph.py` 的改名），revert 会把它们一起打回去。
2. **必须重新写一份预注册。** 旧协议的 tell 集合、ground truth、判分链都不存在了；
   拿它改一改充当新卷子 = 看到过结果再定及格线，那正是它当年要防的事。
3. **`SAFETY_FACT_TYPES`、ADR 0018 §3、ADR 0002 (b)** 三处要一起回来——它们不是自动恢复的，
   上面「四处塌方」那一节就是给那一天准备的清单。
