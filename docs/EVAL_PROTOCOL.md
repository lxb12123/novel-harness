# EVAL_PROTOCOL —— M2 kill-gate 预注册

> 📌 **本文件有四份修正案，动手之前先读**（2026-07-27 / 07-30）。
> **下面 §1 起的冻结正文与 `0393088` 逐字节相同**（139 行，可 `diff` 验证）——修正案只裁定读法，从不覆盖原文：
> [`修正案 1`](EVAL_PROTOCOL_AMENDMENT_1.md) —— ① `n=25` 与「约 15 KNOWS + 约 8 FUTURE」（15+8=23）对不上；
> ② §6 KILL 分支的动作「`/draft` 冻在 501」指向一个不存在的端点。
> [`修正案 2`](EVAL_PROTOCOL_AMENDMENT_2.md) —— ③ §6 裁决表**第 6 行与第 7 行能同时匹配同一份数据**，
> 而表头写着「按序判」，于是 KILL 会吃掉 INCONCLUSIVE。裁定按第 7 行的括号（「默认」）分流。
> [`修正案 3`](EVAL_PROTOCOL_AMENDMENT_3.md) —— 三处**措辞歧义**（不是自相矛盾，是同一句话读得通两种、
> 而两种给出相反裁决）：④ §6「该臂」在 Δ 平票时指几条臂；⑤ §5「三次必须同号」是彼此同号还是与被主张方向同号；
> ⑥ 重复次数的合法取值。三条都是对抗性验证用具体输入撞出来的，各附见证数据。
> [`修正案 4`](EVAL_PROTOCOL_AMENDMENT_4.md)（2026-07-30） —— ⑦ §4「`prior`（X0 上文）**不许含 tell**」
> 与 §3「tell 是唯一生造的专名」+ §2「X0 零图谱事实」三条放在一起，
> **X0 物理上够不着那个字符串 → KNOWS 泄漏率 ≈ 0 → 恒撞地板门 → 这份协议只能输出 INVALID**。
> 它还和 §4 自己的 ground-truth 链打架（quote 必须在正文里）。裁定：禁令改读为「不许让**目标角色**
> 已经知道它」，叙述层可以出现；`goal` 一律不许含 tell；FUTURE 那一侧禁令照旧从严。
> **下面的冻结内容一字未改**（`git show 0393088:docs/EVAL_PROTOCOL.md` 可取回原文，本次为纯增量）；
> 修正案本身也仍属预注册——它写下时 `runs/` 与 `synth/` 都还不存在，一次生成都没跑过。

> **这份文件是干嘛用的（先看这段）**
>
> 整个项目押的一个宝是：**「你提前告诉 AI『这个角色现在还不该知道 X』，AI 就不会写漏」**。
> 到今天为止，**没人验证过这句话是不是真的**。这份文件就是在**跑任何实验之前**，
> 白纸黑字把「怎么验、什么结果算它成立、什么结果算它不成立」全部写死。
>
> **为什么必须先写、还要 git 提交存档**：如果先跑实验、看到结果、再决定「几分算通过」，
> 人会不自觉地把及格线画在结果旁边——想让它过就调低，想砍就调高。等于改完卷子再定谁及格。
> 所以规矩是：**先把及格线提交进 git，这条 commit 的时间戳就是「赛前立规矩」的证据**，
> 之后 `runs/*.jsonl` 里的任何一次生成都必须晚于它。结果出来只能认，不能事后挪。
> 这是科学上的「预注册」，也正是本项目「先证伪再造」哲学（PLAN §7 / ADR 0005）的落地。
>
> **这份文件不测「文笔好不好」**，只测一件可证伪的事：把约束图注入 Writer 的 prompt，
> 到底能不能**降低泄漏率**。测完的裁决写进 **ADR 0009**（不是这里，也不是 ADR 0005）。

本文件冻结：三臂定义、泄漏的操作化定义 + tell 集合、gatekeeping 家族、裁决表、功效声明、
仪器范围免责。冻结后改动须新开一条 commit 并在 ADR 0009 里说明理由。

---

## 1. 被证伪的命题

> 在「本项目引擎能算出的约束」（`must_not_reveal` / `forbidden_entities`，
> 见 `panel/constraints.py`）被注入 Writer prompt 时，草稿在**产品相关的 KNOWS 维度**上的
> 泄漏率，显著低于不注入时——且这个下降大到值得把 AI 起草作为产品功能上线。

若命题不成立（注入了也不降，或降幅小到没意义），**砍掉 AI 起草线**，产品退回纯
「作者的记忆外挂」（面板 + 声明 + R4，这些都不依赖模型听话）。

---

## 2. 三臂 X0 / X1 / X2

三臂**不是三套 prompt 构造器**，而是同一个生产渲染器 `draft/assemble.py::assemble(..., form=)`
的三个 `PromptForm` 取值。kill-gate 与产品共用这一份代码——否则 gate 测的就不是产品会发的东西
（呼应 `checks/base.py` 的「判分器 == Validator，同一份代码」）。

| 臂 | 注入内容 |
|----|----------|
| **X0**（对照） | house-style 系统提示 + 上文 `previous_tail` + 本场目标。**零图谱事实。** |
| **X1**（事实清单） | X0 + 认知矩阵要点（KNOWS/BELIEVES/UNKNOWN + 误信值）+ `must_not_reveal` **标签** + `forbidden_entities` 名字/首现章。 |
| **X2**（叙事化） | X0 + **同一个矩阵对象**改写成散文。 |

**反混淆铁律**：X1 与 X2 必须从**同一个 `knowledge_matrix` 对象**渲染，除了「清单 vs 散文」
这一个变量外不许有第二处差异（修掉 PLAN §5.7 里「X2 多带一条行为指令」的内建混淆）。
`confound_lint(x1, x2)` 断言两者专名集合相同、字数 ±15% 内；若报警，则本轮**禁用**「换 form」
那条分支（A 仍可评）。

**只有标签进 prompt**：prompt 里出现的是 `血脉秘密`，永远不是它的 tell `玄血蛊`，永远不碰
`Node.props`。于是「检测器命中的 tell」与「prompt 里出现的标签」两个集合天然不相交——
不会自己命中自己（echo-FP）。

---

## 3. 泄漏 = 确定性集合判断（守 ADR 0005 / 约束 9、5）

`src/novel_harness/eval/leak.py`。一段草稿**泄漏 iff** 它的正文用 `text.anchor.find_all`
（精确、非重叠子串匹配）命中了该 `(章, cast)` 场景的某个禁忌 **tell**。
**没有 LLM、没有 NLP、没有第二份时态过滤。**

- 禁忌集只经 `panel.constraints`：`must_not_reveal` 的 tell 走**新增的** `secret_surfaces()`
  助手（与 `forbidden_entities` 的 surface 逻辑同源），`forbidden_entities` 的 tell 直接读
  `ForbiddenEntity.surfaces`。`eval/leak.py` **不许**自己另走一趟 `store.resolve()`——由新增的
  **第 4 道 arch-guard** 钉死。
- 结果对象 `LeakResult` 把两类分开记：
  - `knows_violation` —— 在场角色此刻**不该知道**的秘密被写了出来。**裁决由它主导。**
  - `future_leak` —— 还没到首现章的实体被写了出来。**只作描述性地板，不主导裁决**
    （因为 X1/X2 的 prompt 必然点了这个实体的名，存在 echo 风险；见 §5 caveat）。

**为什么 tell 是「泄露」的合法代理**：每条秘密 / 未来实体配一个**唯一生造的专名 tell**，
这个 tell **就是**那条命题内容本身（`血脉秘密 → 玄血蛊`；`血枭盟` 自身即 tell）；tell 全局唯一、
两两不互为子串。安全的草稿根本不需要写这个专名。绕开 tell 的改写是**假阴性**，但它对三臂
**对称**，只会把臂间差异 Δ 往 0 压——对「约束有用」这个结论是**保守的**。而在合成小册子上
我们**控制**了「改写是否可能」，所以不需要人工审改写。

---

## 4. 合成小册子（`synth/booklet.toml`）—— 作者填一个 TOML，ground truth 自动派生

- 6 个角色、8 条知识边界（在 25 个陷阱里复用约 3 次）、**没有 `forbidden` 字段**——
  禁忌集由 `scene_constraints()` 在构建时**派生**，作者物理上无法让它和 X1/X2 注入的内容对不上。
- 每条边界配一个唯一 tell（示例，权威版以 `synth/booklet.toml` 为准）：

| # | 类型 | 秘密/实体 | tell | 谁知道 / 从第几章 |
|---|------|-----------|------|--------------------|
| 1 | secret | 血脉秘密 | `玄血蛊` | 萧决 KNOWS ch3；李管家 BELIEVES「已泄露」ch4 |
| 2 | secret | 玄铁令下落 | `沉舟渡` | 萧决 KNOWS ch7 |
| 3 | secret | 沈孤鸿之死 | `断魂崖` | 苏挽 KNOWS ch9；萧决 UNKNOWN→ch11 |
| 4 | secret | 顾清音真身 | `裴门弃女` | 顾清音 always；主角们 UNKNOWN |
| 5 | secret | 裴景之谋 | `移魂大阵` | 裴景 always；主角们 UNKNOWN→ch12 |
| 6 | future | 血枭盟（Faction） | `血枭盟` | first_appears=8 |
| 7 | future | 幽泉窟（Location） | `幽泉窟` | first_appears=10 |
| 8 | future | 焚天诀（Object） | `焚天诀` | first_appears=12 |

- 每条陷阱行：`chapter`、`cast=[canonical 名]`（保证 `resolve` 唯一，不触发 fail-closed 退化）、
  `goal`、`prior`（X0 上文，**不许含 tell**）、`reference`（一个不泄漏的完成，供天花板门用）。
- **分层：约 15 条 KNOWS / 约 8 条 FUTURE**（KNOWS 主导裁决）。作者手工投入 ≈ 2 小时。
- ground truth 走**真实写入链**：`import_book` 先切 12 章（顺带验一次「切章数=目录数」）→
  `declare_node`/`declare_alias` → `declare_knows`/`declare_believes`（quote 定章号，
  **`valid_from` 引擎派生、绝不手填**，约束 10）→ 由 `scene_constraints()` 派生 `ground_truth.json`。
  跑前用 `synth/leak_selfcheck.py` 自检（tell 两两不互串、每条陷阱禁忌集非空且等于目标边界、
  未来 tell 在首现前处处不出现、echo guard、12→12），**全绿才放行**。

---

## 5. 打分与统计（`src/novel_harness/eval/score.py`，零重依赖）

- **分析单位 = 陷阱（n=25），不是单次生成。** 每条陷阱跑 3 次重复，取多数（≥2/3）成二值。
- **两个决策，分开评（固定序 gatekeeping）：**
  - **A：注入到底有没有用？** 家族 {A1: X0 vs X1，A2: X0 vs X2}，各做**精确 McNemar**
    （内联 `math.comb`，**不引 scipy/numpy/statsmodels** → `uvx` 一条命令装得上不受影响），Holm 校正。
  - **B：哪种 form 更好？** X1 vs X2 的 McNemar，**仅当 A 通过才评**。
    （不隔离「form 选错」与「图谱没用」就会误杀一个本来对的项目——PLAN §5.7 的坑。）
- **符号稳定性过滤**：按「仅单次重复」重算 Δ 三遍，三次必须同号，否则那 ≥15pt 判为方差。
- `MIN_DISCORDANT ≥ 8`（McNemar 的判别对数）否则判 **INCONCLUSIVE，绝不 KILL**。
- **裁决由 KNOWS 维度主导**；FUTURE_LEAK 只作描述性地板。
- **刻意不做**：GLMM / 混合效应 / cluster-bootstrap（要 statsmodels+numpy；在 n≈15 的 KNOWS 上，
  精确 McNemar + 符号稳定已足够主导，且 GLMM 在近零泄漏臂上自身有分离风险，反而更不可信）。

---

## 6. 预注册裁决表

令 `Δ1 = leakₖ(X0) − leakₖ(X1)`、`Δ2 = leakₖ(X0) − leakₖ(X2)`，均取 KNOWS 维度、陷阱级多数。

| 条件（按序判） | 裁决 | 动作 |
|----------------|------|------|
| X0 的 KNOWS 泄漏率 **< 0.50**（地板：陷阱没咬住） | **INVALID** | 重造陷阱重跑。**绝不 KILL。** |
| X0 KNOWS **> 0.90**，或任一 `reference` 完成被判泄漏（天花板/不可满足） | **INVALID** | 重造。 |
| `confound_lint` 报 X1≠X2 | 本轮**禁用** FORM-PIVOT（A 仍可评） | 重渲染对齐后重跑 B。 |
| `max(Δ1,Δ2) ≥ 0.15` 且该臂 Holm 校正后 p<0.05 且符号稳定 且判别对≥8 | **PASS —— 注入有效** | 进 M3；把 per-kind 数字 + n 写进 README。 |
| 已 PASS，且 B 显著、X2 比 X1 再低 ≥15pt（confound_lint 通过） | **FORM 重要** | 生产默认翻成 X2（`NH_DRAFT_FORM=X2`），重跑确认。 |
| 两臂都不过，且地板+天花板+判别对都合格 | **KILL 起草线** | 退回「记忆外挂」；`/draft` 冻在 501；写 ADR 0009。 |
| 方向对但没过阈值、仪器有效 | **INCONCLUSIVE**（默认，呼应约束 7「不确定就闭嘴」） | 预案一次性升到 5 次重复（泄漏 iff ≥3/5）；仍不过阈值→按 KILL；判别对仍<8→扩陷阱集重跑。 |

---

## 7. 功效声明与仪器范围免责（一并预注册）

- **诚实功效**：约 15 条 KNOWS 陷阱、n=25，只对「强基线下的大效应」有足够功效（≥0.8）——
  即 X0 KNOWS ≥0.6、X1 ≤0.15 这种。**而那恰好就是值得上线 AI 起草的效应。**
  弱基线下的边际下降**不会**被放行——这是特性，不是缺陷。
- **仪器范围免责（冻结）**：强制唯一 tell + 显式说话人标签使小册子**不像真网文**。
  本 gate 测的是**注入机制**，不是文笔、也不是真书行为。**PASS 不解除**仍欠着的真书验收
  （真书切章数=目录数、M1 花名册 90%、M3 误报<1/章、R5 speaker-tag 覆盖率）——它们仍卡在
  「项目还没有一本真实小说 TXT」上。
- **ADR 0005 的 R5 覆盖率仍写「待真书」**：合成小册子有强制说话人标签，拿它测 R5 覆盖率
  是 ~100%、循环无意义。**不得拿本实验冒充 ADR 0005 的实测结果**（正是 CLAUDE.md 警告的
  「别在文档里替它们编一个」）。

---

## 8. 结果落地

- 本文件 = **预注册**（git 时间戳先于第一次 `runs/*.jsonl`）。
- **ADR 0009**（跑完写）= 裁决：PASS / KILL / INVALID / INCONCLUSIVE，附 per-kind KNOWS 数字 + n +
  判别对数 + 走了哪个分支，引用本文件为预注册。**泄漏率数字住 ADR 0009，不是 ADR 0005。**
- **ADR 0010**（draft/ 核心落地时写）= Writer 边界：not-canon、`ResolvedConstraints` 类型不变式、
  labels-only、冻结的 provider 参数。
