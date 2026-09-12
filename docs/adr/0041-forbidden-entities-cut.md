# ADR 0041：`forbidden_entities`（未登场实体禁写清单）整个删掉 —— 维护者裁定，不是数据裁决

- **状态**：已接受
- **日期**：2026-08-31
- **推翻了**：不是原始需求文档，是**本仓库自己**——`panel/constraints.py::forbidden_entities()`
  连同它在右栏「写作提醒」、`draft/context.py`（产品起草的约束装配）、Mode 2 两个工具
  （`scene_constraints`/`book_index`）里的全部读取，以及它们背后「未登场实体不许提前
  出现，系统该主动挡一道」这条设计。
- **相关**：[ADR 0020](0020-clean-extraction-auto-canon.md)（PLANNED 转译原则的出处）、
  [ADR 0040](0040-future-leak-cut.md)（R2 FUTURE_LEAK 砍掉——**同一个字段的另一个消费者**，
  4 天前先走的那一刀，理由不同，见下方「为什么」）、[ADR 0039](0039-secrets-offline.md)
  （`must_not_reveal` 那半随秘密下线，`forbidden_entities` 是当时唯一剩下的那一半）、
  [ADR 0004](0004-declaration-over-extraction.md)（`first_appears_chapter` 是作者意图，
  不由文本特征定义）

## 决策

**`panel/constraints.py::forbidden_entities()`、`ForbiddenEntity` 类型、
`SceneConstraints.forbidden_entities` 字段，连同 `panel/constraints.py::scene_constraints()`
（bare 函数，只有 `/constraints` 路由这一个调用方）、`GET /api/projects/{pid}/chapters/{n}/constraints`
路由整个删掉。**

**没有删的**：

| 东西 | 现状 |
|---|---|
| `node.props.first_appears_chapter` 字段 | 保留。`declare_first_appearance()` 还在写它 |
| `declare_first_appearance()` | 保留，字段唯一的写入方 |
| R3 `DEAD_SPEAKS`（`dead_speaks.py:201`） | 保留，直接读 `node.props.first_appears_chapter`，
  从来不经过 `forbidden_entities()`——两者是同一字段的独立消费者，不是同一条链路 |
| `panel/constraints.py::resolve_cast()` / `ResolvedCast` / `UnresolvedCast` / `SceneView` /
  `scene_view()` | 保留。这一半答的是另一个问题（cast 原文能不能解析成唯一角色），
  `draft/context.py`、`agent/tools.py`、`/matrix`、`/state` 等多处独立依赖它 |
| `SceneConstraints.unresolved_cast` / `require_resolved_cast()` | 保留，起草前的歧义
  cast 守卫，跟未登场实体无关 |
| `ChapterEntry.future`（`agent/index.py`，章目录的「未来章」标记） | 保留，算法本来就
  与 `RosterEntry`/`forbidden_entities` 无关（纯章号比较） |
| `docs/adr/0020` / `0039` / `0040` 正文 | 一字不改，`forbidden_entities` 在那几份文档里
  作为历史记录留着 |

## 为什么 —— 跟 ADR 0040 不是同一种理由，别混着读

ADR 0040 砍 R2 时特意留了这句话没动：**"`panel/constraints.py::forbidden_entities()`
本身没有删——R2（FUTURE_LEAK）和右栏面板还在读它，这一刀只砍『拿它开一条规则』这一件事"**
（`checks/__init__.py` 模块docstring原话）。那次留下它，是因为它当时是**真的在被使用**的：

- `first_appears_chapter` **有** UI 入口（`declare_first_appearance()`，作者能填）——
  跟 R2 那个「从来没有输入框、结构上永远开不了火」的死锁完全不同。
- 右栏「写作提醒」真的在渲染这份清单。
- Mode 2 的 `scene_constraints` 工具真的在把它交给模型（`ConstraintsResult.forbidden_entities`）。
- Mode 2 的 `book_index` 工具真的在角色册每条上标 `future`（`RosterEntry.first_appears_chapter`）。

**这次删除不是「数据证明它没用」，是维护者的实时裁定：这条价值今天不需要了。**
跟 ADR 0040 用"输入路径结构性死锁"论证不同，本 ADR 没有类似的结构性论据——
`forbidden_entities()` 删除前是活的、有真实调用方、有真实作者可填的数据。
这条决定的正当性完全来自维护者的产品判断，不是任何一次真书实测或覆盖率门槛，
**这里不假装有一个数据论据**（同 EVAL_PROTOCOL 的规矩：没有就不编一个）。

## 代价（承认，不粉饰）

- **Mode 2（写作助手）失去了对「别提前写出未登场角色/地点」的主动信号，且是两处，不是一处**：
  - `scene_constraints` 工具不再告诉模型"这几个未登场的名字先别提"；
  - `book_index` 工具的角色册列表不再给每条未登场实体打 `future` 标记，
    `_future_note()` 里"这份返回里有 N 条来自未来"那句提示的计数也跟着**只剩章目录那一半**
    （角色册那一半此前实测：一本真书上章目录常年是 0 条未来、角色册里正躺着未登场的
    地点和人物——这条提示的"角色册也要数"那半价值随这次删除一起没了）。
  - 模型今天完全**没有任何自动信号**能知道某个角色/地点还没登场——这件事之前也不完美
    （R2 从未真正开过火，ADR 0040），但 `forbidden_entities` 至少在 Mode 2 的工具返回里
    是真实、可用的数据，这条删除是真的把它关掉了，不是关掉一个早已死掉的东西。
- **右栏「写作提醒」整块消失**，作者不再能一眼看到"本章尚未登场"清单。这一格同时还带着
  `unresolved_cast` 警告（"这些称呼未在角色册中找到"）——**这部分连带损失是意外的**：
  这条警告本身跟 forbidden_entities 无关，但它在 UI 上没有第二个家，随宿主一起消失了
  （见 `frontend/src/components/RightPanel.tsx` 的删除记录）。
- **"这一章提到"（`mentioned.py`/`useMentioned` 的显示）在 UI 上也失去了唯一的挂载点**——
  `CastLine` 组件同时渲染 cast 过滤条和这份提到清单，随「写作提醒」tab 一起删了。
  `mentioned.py`/`useMentioned` 本身**没有删**（其他地方仍在用），只是这一个展示位置没了。
  这条不是本 ADR 的决策范围内代价（维护者没有要求删它），是执行这次删除时**发现的一处
  没有独立归宿的收尾**，如实记在这里，不代表维护者已经决定它该消失。
- **测试覆盖面收缩**：`tests/test_story_wiki_leak.py` 里"未来人物 plot_note 不泄漏"这一路
  的反向断言（证明约束真的算过、不是在空转）删了——那个节点今天没有任何代码路径会碰它，
  断言留着也测不出东西。三种"毒"里另外两种（Secret 的 twist / secret 扩展表的 description）
  仍然被其他自守卫覆盖。

## 什么时候该重新考虑

**触发条件是维护者认定这条价值又需要了，不是某次真书实测出了具体的泄漏事故**——
本 ADR 本来就不是数据裁决出来的，撤销它也不需要等一个数据门槛。真要恢复，
`node.props.first_appears_chapter` 字段和 `declare_first_appearance()` 都还在，
`git` 历史里有完整实现可以找回来，不需要从零设计。
