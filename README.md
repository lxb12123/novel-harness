# Novel Harness

**唯一一个知道「谁在第几章还不该知道什么」的中文长篇写作引擎。**

> ⚠️ **Pre-alpha。** 已经能跑的是两条线：**终端里的**——SQLite 数据层 + 时态图层（`state_at` / supersede）、认知边界面板、声明层（敲引语，系统算章号）、R4 一致性规则、切章与场景块解析，出口是 `nh` 的 15 个子命令；**浏览器里的**——FastAPI 壳（27 条路由）+ React 工作台（三栏 + 右栏 6 个 tab + CodeMirror 6 + 局部关系图）。609 个 pytest + 18 个 vitest 全绿（前端吃的 fixture 是从真后端 dump 的，出参一改两头都红）。CI 里写了 `uvx` 装机路径的三级验证（含「装出来的包里真的有工作台」），但**这个仓库还没有远端，CI 一次都没跑过**——目前全部只在维护者本机手跑过。
> **还没有的**：AI 起草线（M2 在建——判分器和统计已落地，**被判的那个 prompt 三臂还一个字符都没写**）、抽取（M4）。
> **工作台今天最硬的一个洞**：只有 3 个前端组件有测试（`CenterEditor` / `LocalGraph` / `BottomBar` 等仍是零）。**完整缺口清单在 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#工作台的已知洞) 的「工作台的已知洞」，那儿是唯一副本，这里不留第二份。**
> 而且**没有一行代码在真书上跑过**：切章数对不对、R5 该不该活，都还等着一本中文小说 TXT。
> （四条真书验收里有两条**同时还缺代码**，书到手也验不了——见 ARCHITECTURE 的「四条真书验收」。）路线图见下。

你在第 88 章告诉过它谁知道那个秘密。第 152 章它还记得——
而且它不会让 AI 说漏嘴。

## 它解决什么

写到第 200 章时，你忘的通常不是「我写错了」，而是「**他这章还不知道**」。

所有 AI 写作工具的记忆都是「客观事实图」——谁和谁是什么关系、谁在哪。
**没有一个记「主观认知图」**：谁知道、谁不知道、谁相信着一个错误的版本。

Novel Harness 把它做成可查询、可强制执行的一等约束：

```
┌─ 认知边界 · 第 152 章 · 场景 3 ─────────────────────┐
│  在场角色          血脉秘密        玄铁令下落        │
│  ─────────────────────────────────────────────       │
│  萧决              ✓ 知道 (ch88)   ✓ 知道 (ch120)    │
│  顾清音            ✗ 不知道        ✗ 不知道          │
│  李管家            ⚠ 错误认知      ✗ 不知道          │
│                      (ch103 起：以为已泄露)          │
│                                                      │
│  本场景 must_not_reveal：血脉秘密 · 玄铁令下落       │
└──────────────────────────────────────────────────────┘
```

这个面板**不读正文**：它读你在场景里声明的在场角色，去图里做一次集合查询。
零 LLM、零 NLP、**零误报——因为它对文本不做任何断言**。它只是告诉你，你自己告诉过它的事。

而当你点「起草这个场景」时，同一份认知边界会进入模型的 prompt。

## 它不做什么

- **不替你写完一本书。** 它起草单个场景，你自己写。
- **不换掉你的编辑器。** 正文是磁盘上的 Markdown，你继续用 VSCode / Obsidian / Typora（[ADR 0007](docs/adr/0007-manuscript-lives-on-disk.md)）。工作台里现在也有一个 CodeMirror 编辑器，但它读写的是**同一份磁盘 Markdown**，不用它也行——DB 永远不是正文的真相源。
- **不自动抽取你的设定。** 秘密和伏笔是**作者的意图**，不是文本特征——墙上挂了把枪，它是不是伏笔取决于你第 200 章打不打算开枪。这个信息物理上不在已写文本里，抽取器只能猜（[ADR 0004](docs/adr/0004-declaration-over-extraction.md)）。你声明，它执行。一本 200 万字的书，全部声明成本约 1 小时。

## 你的稿子不出你的电脑

图谱、面板、一致性检查全部本地跑，零网络请求。只有你主动点「起草」或「抽取」时才调模型；
API 地址可自定义（中转 / 本地 ollama 都行）。

**今天这条是自动成立的，因为起草线还没建**：`draft/` 下只有一个模型出口 `provider.py`，
27 条路由里没有一条会调模型，不配置任何 key 时整个工具完整可用。
计划中的 `--local-only` 开关（一个显式的「彻底关掉」）**还没有实现**——
在它出现之前，别把它当成一个可以敲的命令。

## v1 的用户是谁

**写中文长篇、正文用 Markdown、会开终端的作者。**

如果你在用 WPS 且不想碰命令行，v1 还不适合你。这句话我们写在这里，而不是等你装完再发现。

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 骨架 + 打包链路 + 数据层 | ✅ 代码全绿 · ⏳ 四条真书验收一条没验 · ⏳ `uvx novel-harness` 仍是 stub |
| **M1** | 声明层 + 认知边界面板 ← **首个可发布物** | ✅ 已落地（终端） |
| M1.5 | FastAPI 壳 + React 工作台 | 🚧 主结构在 · 缺口清单见 [ARCHITECTURE](docs/ARCHITECTURE.md#工作台的已知洞) |
| **M2** | 合成小册子 + 起草 + **kill-gate** ← 最早的证伪点 | 🚧 **进行中**（判分器先于被判者落地，见下） |
| M3 | 一致性规则 R2 / R3 / R5 | — （今天只有 R4，另三条共同缺 `text/mentions.py`） |
| M4 | 增量抽取 + 三层图谱 | — |
| M5 | v1.0 | 局部关系图已提前进工作台（React Flow） |

**M2 是一个公开的 kill-gate**：如果图谱约束对模型无效（三臂对照 + McNemar 检验，协议在跑出任何结果之前就 commit），
我们会砍掉 AI 起草线，把项目定位改成「作者的记忆外挂」。完整预注册协议见 [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md)，
失败分支写在 [`docs/PLAN.md`](docs/PLAN.md) 第 7 节。

M2 今天的形状是**判分器先于被判者**：泄漏检测（纯集合判断）、精确 McNemar + Holm、统一模型出口都已落地并有测试；
而三臂本体（`draft/assemble.py` 的 `PromptForm`）、合成小册子 `synth/`、runner、裁决表 `decide()` **一个都还没有**。
在它们补齐之前，kill-gate 跑不起来，README 里也不会出现任何泄漏率数字。

## 设计决策

每份 ADR 都写明：决策是什么、**推翻了原始需求文档的哪一条**、论证、以及**若此决策错误，修复成本是什么**。

| # | 决策 |
|---|---|
| [0001](docs/adr/0001-no-neo4j-in-v1.md) | 故事图谱进 SQLite，不用 Neo4j（含加回的触发条件） |
| [0002](docs/adr/0002-no-vector-retrieval-in-v1.md) | v1 不做向量检索 |
| [0003](docs/adr/0003-stable-ulid-ids.md) | 业务 ID 用 ULID，不用 slug |
| [0004](docs/adr/0004-declaration-over-extraction.md) | 声明优于抽取 |
| [0005](docs/adr/0005-set-judgment-only.md) | 只做集合判断，不做语义判断 |
| [0006](docs/adr/0006-evidence-double-pointer.md) | Evidence 双指针；系统不确定时默认闭嘴 |
| [0007](docs/adr/0007-manuscript-lives-on-disk.md) | 正文在磁盘上（⚠️ 「v1 不做编辑器」已松动：应用内 CodeMirror 进 v1，实质仍守住） |
| [0008](docs/adr/0008-related-to-is-undirected.md) | `RELATED_TO` 无向，存储时 `(src,dst)` 规范化成 `(min,max)` |

## 五分钟看看它跑

**macOS 上最省事的一条：clone 下来，双击 `start.command`。** 它自己装依赖、构建界面、
起服务、开浏览器（缺 uv 会告诉你怎么装）。库落在仓库根的 `book.db`。

想看它在终端里跑完整条链：

```bash
git clone <repo> && cd novel-harness && uv sync
bash scripts/demo.sh          # 心跳：声明 → 图 → 面板 → 规则，一条命令跑完整条链
```

`demo.sh` 每一步都断言输出，退出码就是「端到端还通着吗」那个布尔值。
它量的是**接缝**，用的是手写的 3 章 fixture——**不是真书验证**。

想看工作台长什么样：

```bash
cd frontend && npm install && npm run build && cd ..
uv run python scripts/seed_demo.py /tmp/demo.db     # 造一份有内容的示例库
uv run nh serve --db /tmp/demo.db                   # 建库 + 起服务 + 开浏览器
```

导入自己的书也行，但**导入之后花名册会是空的**——导入只切章、不抽实体
（[ADR 0004](docs/adr/0004-declaration-over-extraction.md) 是有意的：秘密和伏笔是你的意图，
不是文本特征，抽取器只能猜）。左栏花名册旁的 ＋ 建第一个人物和秘密，认知矩阵才有行和列。
想先看成品形态就用上面的 `seed_demo.py`。

## 开发

```bash
uv sync
uv run pytest
uv run ruff check .
uv run nh --help
```

**日常写代码走两个进程**（Vite 把 `/api` 代理到 uvicorn，改 `.tsx` / `.py` 都热更新）：

```bash
uv run nh serve --db book.db --no-open   # 只为建库；看到 URL 就 Ctrl-C
NH_DB=book.db uv run uvicorn novel_harness.api.app:app --port 8000 --reload   # 8000 是代理目标
cd frontend && npm install && npm run dev                                     # 前端 5173
```

**`nh serve` 是发布形态，不是开发形态**——它没有热更新，且发的是上一次 `npm run build`
的产物。用它验证「打包之后还对不对」，一周一次，不是每次改代码。

详见 [`frontend/README.md`](frontend/README.md)。

## License

Apache-2.0
