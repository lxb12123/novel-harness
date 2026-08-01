# ADR 0010：Writer 边界 —— `assemble()` 能看见什么、绝不能看见什么

- 状态：已接受
- 日期：2026-07-30
- 回答了什么：`EVAL_PROTOCOL.md` §8 点名要求的那份 ADR（「`draft/` 核心落地时写 = Writer 边界：
  not-canon、`ResolvedConstraints` 类型不变式、labels-only、冻结的 provider 参数」）。
- 必须在：`src/novel_harness/draft/assemble.py` 存在之前。**这条不是排期偏好。**
  `tests/test_draft_boundary.py:44-45` 自己写着：那道守卫拦不住「完整 PLANNED 进 prompt」，
  因为判断「这段文字是不是把伏笔说破了」需要语义判断，而 ADR 0005 禁止本仓库做语义判断。
  **那一格只有 review 和这份 ADR 守得住**——它得先存在，review 才有东西可依。

> **仍属预注册。** 写下本文件时：
>
> ```bash
> ls runs/                                       # No such file or directory
> ls src/novel_harness/synth                     # No such file or directory
> git log --all --oneline -- 'runs/*' 'synth/*'  # 空
> ```
>
> 一次生成都没跑过。本文件里有一条**会影响实验结果**的裁定（§决策 D6，X0 里放不放 `cast`），
> 所以它必须和三份修正案受同一条纪律约束：**先自证没有数据，再裁定，并写明裁定往哪边错**。
> 第一个 `runs/*.jsonl` 落盘后，这条路就关了。

---

## 决策

`assemble(ctx: ResolvedConstraints, *, form, goal, previous_tail, house_style) -> list[dict]`
是 `draft/` 唯一的 prompt 出口。六条边界，**每一条都写清它由什么守住**：

### D1 Writer 的输出永远不是 canon（由 review + 类型缺口守）

`complete()` 返回的任何字符，**没有任何一条代码路径可以让它进图**。草稿只能落到磁盘或
`runs/*.jsonl`。要进图必须走 `declare.py` 的作者声明链——作者读到那句话、点确认、
引语定章号、`valid_from` 由证据派生（约束 6 / ADR 0006）。

这条在类型层已经有一半保障：`draft/` 拿到的是 `StoryGraph`（只读 Protocol）不是 `CanonWriter`，
而 `assemble()` 按 D2 连 store 都拿不到。**剩下那一半是纪律**：将来给 `/draft` 路由接
「一键采纳生成的设定」这类功能时，采纳的入口必须是 `declare_*`，不许是 `upsert_edge`。

### D2 入参只有 `ResolvedConstraints`，没有 store、没有 project_id（由签名守）

`assemble()` 不接 `StoryGraph`、不接 `SceneConstraints`、不接 `project_id`。
**它拿不到 store，就查不了第二遍。**

这不是形式主义：只要签名里有 store，某天就会有人为了「让模型知道得更全」在 `assemble()`
里补一次 `store.resolve()` 或 `knowledge_matrix()`，而那一刻起 prompt 里的事实
和 `eval/leak.py` 判分用的事实就是两次独立查询的结果——gate 测的不再是产品会发的东西。
`ResolvedConstraints` 是 frozen 的、算好的、两侧共用的**同一个对象**
（`draft/context.py` 的 Notes 已经为此禁止 runner 走 `resolve_constraints()`）。

### D3 labels-only：能进 prompt 的字段是一份白名单（由 arch-guard + review 守）

**允许**，且只允许这五个：

| 来源 | 内容 | 为什么安全 |
|---|---|---|
| `ctx.cast` | 作者写在场景块里的称呼原文 | 作者自己的输入，不是图谱派生 |
| `ctx.secret_labels` | 秘密的**显示名**（`血脉秘密`） | 与 tell 集合天然不相交（tell 那侧排除 canonical） |
| `ctx.forbidden_names` + 首现章 | 未来实体的名字 | **这一侧不相交性质不成立**，见下方「不对称」 |
| `ctx.matrix` 的 `state` / `since_chapter` / `believed_value` | 认知矩阵三态 + 误信值 | 全是 `KnowledgeCell` 的标量字段 |
| `ctx.matrix.characters/secrets` 的 `.name` | 窄引用的名字 | `NodeRef` 没有 `props` |

**禁止**，一条都不许：

- **`Node.props` 的任何键**——tell 住在这儿（`NodeProps` 是 `extra="allow"`，秘密节点上的
  `twist` 会顺着序列化进 prompt，保密清单自己泄密）。由第 4 道守卫的 `PROPS` 扫描器钉住。
- **`secret_surfaces()` / `resolve_cast()`**——由 `WRITER_BANNED` 钉住。
- **PLANNED scope 的边内容**。`ResolvedConstraints` 的出参里物理上装不下它
  （秘密和未来实体都收窄成 `NodeRef`），**但守卫拦不住有人把它当成字符串拼进
  `goal` 或 `house_style` 参数**——那两个是自由文本入口。这就是本 ADR 存在的那一格。

**判据（写给 review 的，因为机器判不了）**：prompt 里出现的关于「还没发生的事」的信息，
必须能一一对应到 `ctx.forbidden_names` 里的一个**名字**加一个**章号**。
出现任何描述**内容**的句子（「他将在第 12 章发现自己是裴门弃女」）即为违反 D3，
无论它是从哪个参数塞进去的。

### D4 X1 与 X2 从同一个 `ctx` 渲染，X0 是它们的严格前缀（由测试守）

反混淆铁律（协议 §2）在类型层已经成立一半：矩阵绑在 frozen 的 `ctx` 上，
`assemble(ctx, form=X1)` 和 `assemble(ctx, form=X2)` 拿的**必然**是同一份。

本 ADR 补上另一半：**三臂共用同一个 `_base(goal, previous_tail, house_style)`**，
X1 / X2 只是在它之后追加图谱段。于是「X0 是 X1、X2 去掉图谱段之后逐字节剩下的东西」
可以写成一条测试，而不是一句承诺。**不许给某一臂单独加一句行为指令**——
那正是协议要修掉的 PLAN §5.7 内建混淆。

### D5 一轮 gate 之内 `ProviderConfig` 冻结（由 runner 的签名守）

runner 构造**一份** `ProviderConfig`，传给三臂的每一次 `complete(config=...)`。
**禁止 `config=None`**：那会走 `from_env()` 现读环境变量，中途谁 `export` 一下
`NH_LLM_TEMPERATURE`，这一轮的三臂就不再是同一次调用的三个取值了，而没有任何东西会红。

`ProviderConfig` 本身是 frozen 的，所以「传同一个对象」等价于「参数逐字节相同」。
那一份 config 必须原样写进 `runs/*.jsonl` 的头部——ADR 0009 要能复算，
「用什么模型跑的」不能靠回忆。

### D6 `cast` 属于「本场目标」，三臂都有（**本 ADR 唯一影响实验结果的裁定**）

协议 §2 写 X0 = 「house-style 系统提示 + 上文 `previous_tail` + 本场目标。**零图谱事实。**」
而 §4 的陷阱行里 `cast` 和 `goal` 是并列的两个字段。**「在场有谁」算不算图谱事实，
协议没说。** 两种读法给出不同的 X0，也就给出不同的 Δ。

**裁定：`cast` 三臂都有。** 理由是它的来源，不是它的形状——`cast` 是作者写在场景块
`<!-- nh: cast=... -->` 里的称呼原文，任何一个用这个产品的人都会写它；
它是**输入**，不是图谱查询的**结果**。X1 相对 X0 多出来的那一份东西，
协议列得很清楚：认知矩阵要点、`must_not_reveal` 标签、`forbidden_entities`——
全是「谁在第几章知道什么」这类**派生**事实。cast 不在那张单子上。

**这条裁定往哪边错**：把 cast 从 X0 拿掉会让 X0 写到别人身上去，泄漏率下降，
Δ 跟着变小，**对本项目更不利**——所以本裁定选的是对自己更有利的那一边，必须说清楚为什么它仍然对：

1. 拿掉 cast 的 X0 不是「产品去掉图谱注入」，是**另一个任务**。臂间差异里会混进
   「写的根本不是同一场戏」，那不是混淆的修正，那是混淆的引入。
2. 它会直接撞地板门：X0 的 KNOWS 泄漏率若因此掉到 0.50 以下，协议 §6 第一行判 **INVALID**
   要求重造陷阱——一个由 prompt 构造方式造成的 INVALID，重造多少次陷阱都不会好。
3. 三臂**对称**地都有 cast，所以它对 Δ 的贡献为零。协议 §3 对假阴性用的是同一个论证。

---

## 问题：守卫为什么在这一格失效

第 4 道 arch-guard（`tests/test_draft_boundary.py`，11 条）扫的是 **AST 上的符号引用**：
谁 import 了 `secret_surfaces`、谁读了 `.props`、谁调了 `store.resolve()`。
它对**字符串内容**一无所知，而 prompt 是字符串。

```python
# 守卫全绿，且 tell 一个字符都没有出现在代码里
goal = "写萧决发现自己血脉有异——他还不知道那是家族封印的反噬"
messages = assemble(ctx, form=PromptForm.X1, goal=goal, ...)
```

上面这个 `goal` 把 PLANNED 的内容用自然语言说了出来。它不碰 `props`、不 import 任何禁用符号、
不查第二遍图——**四条机器判据全过**。而它做的正是 §2「只有标签进 prompt」要禁的事。

要机器判它，得回答「这句话是不是把伏笔说破了」——那是语义判断，ADR 0005 的铁律
（**只做集合判断，不做语义判断**）在 v1 里禁止本仓库长出这种能力。
所以这一格的守法只能是：**把判据写成一句 review 时能照着念的话**，也就是 D3 末尾那条。

顺带说明为什么它比听起来严重：`goal` 和 `previous_tail` 在 kill-gate 里由
`synth/booklet.toml` 提供，而协议 §4 明写 `prior`（X0 上文）**不许含 tell**。
造小册子的人如果在 `goal` 里顺手剧透，**三臂会同时被污染**，Δ 反而看不出异常——
`confound_lint` 也抓不到（它比的是 X1 vs X2，不是 base）。
`synth/leak_selfcheck.py` 必须把「`goal`/`prior` 不含任何 tell」作为放行条件之一。

---

## 论证：为什么是这六条，不是别的

### 为什么不干脆让 `assemble()` 收 `SceneConstraints`

那会把「cast 解析成功了吗」这个问题留到运行期。`draft/context.py` 整个模块存在的理由
就是把它变成类型层强制（ARCHITECTURE §10.5 第 2 条点名的 M2 第一个任务）。
收 `SceneConstraints` 等于把刚编码进类型的不变式又解开——而它退化时的后果是
「Writer 收到『全部秘密都不许提』」，在 gate 里等于给某一臂换了份更严的卷子。

### 为什么不给 `assemble()` 加一个 `include_tell: bool = False` 的旋钮

有人会问：产品里作者自己想看 tell 呢？——**产品里作者看的是面板，不是 prompt。**
一个默认关着的旋钮和一个不存在的能力，在「某天有人为了调试打开它然后忘了关」这件事上
不是同一个风险等级。而这个旋钮打开一次的代价是：那一轮 gate 读出假 KILL，
砍掉一条本来对的产品线，且没有任何东西会红。

### 为什么 D5 不写成「runner 里断言两次调用参数相同」

因为那要在 runner 里维护第二份「什么算相同」的定义。传同一个 frozen 对象是**结构性**的保证：
不是「我检查过它们一样」，是「它们是同一个」。同 D4 让矩阵绑在 `ctx` 上是一招。

### 为什么这份 ADR 不列 prompt 的具体措辞

措辞会改，边界不该改。ADR 定的是「哪些**字段**能进」，`assemble.py` 定「怎么措辞」，
`tests/test_draft_boundary.py` 定「哪些**符号**不许碰」。三层各管一层，
把措辞写进 ADR 会让每次调 prompt 都变成一次改 ADR——而 ADR 改多了就没人当真了。

---

## 若此决策错误，修复成本是什么

**D1/D2/D4/D5 错了：便宜。** 它们是签名和构造方式，改签名 + 改 runner 一次，
测试会把所有调用点指出来。今天 `assemble()` 的调用点只有 runner 一个。

**D3 错了（放太严）：便宜但要重跑。** 如果实测发现 X1 因为信息太少而写不出东西
（比如模型完全不理解「血脉秘密」这个空标签指什么），那不是 D3 的错，是**协议 §2 的设定**——
它要测的正是「只给标签够不够」。改这一条等于改卷子，只能走 ADR 0009 如实记录，
不能悄悄放宽。

**D3 错了（放太松，tell 漏进 prompt）：最贵，且会静默。** 后果是那一轮 gate 的
X1/X2 命中自己写进去的词，Δ 翻负，裁决表逐字读出 **KILL**——**砍掉一条本来对的产品线**。
发现它的唯一办法是人去读 `runs/*.jsonl` 里存的 prompt 原文。所以 runner **必须**
把每一次调用的完整 messages 落盘，这不是审计洁癖，这是这条错误唯一的可发现路径。

**D6 错了：要重造小册子并重跑整轮**（225 次生成）。这也是为什么它必须写在跑之前：
跑完之后再讨论「X0 该不该有 cast」，无论结论是什么都不可信。
