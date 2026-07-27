# Novel Harness 工作台（React 前端）

给不写代码的小说作者用的浏览器工作台，落在精简 SQLite 引擎 + FastAPI 薄壳上。
方案见 [`../docs/UI_ARCHITECTURE.md`](../docs/UI_ARCHITECTURE.md)。

## 跑起来（开发）—— **日常写代码走这条**

两个进程：后端 uvicorn + 前端 Vite（Vite 把 `/api` 代理到 uvicorn）。
**改 `.tsx` 浏览器 0.1 秒自己刷新，改 `.py` 后端自己重启**——这是热更新，`nh serve` 没有。

```bash
# 0) 造一个空库（只要一次。建书/导入之后都在浏览器里做；但**建人物/别名今天还必须回终端**）
cd ..
uv run nh serve --db book.db --no-open   # 建库 + 起服务，看到 URL 就 Ctrl-C；库留下了

# 1) 后端（NH_BOOKS_DIR 可选：新书的稿子目录基址，默认 <库同级>/books）
NH_DB=book.db uv run uvicorn novel_harness.api.app:app --port 8000 --reload

# 2) 前端
cd frontend
npm install
npm run dev        # http://localhost:5173
```

⚠️ 第 1 步的端口**必须是 8000**——那是 `vite.config.ts` 里写死的代理目标。
不要在这一步用 `nh serve`：它没有 `--reload`，且它自己就发前端（发的是上一次构建的旧产物），
于是你会对着一个不会更新的页面改代码。

打开后：库是空的 → 首屏就是「开始一本书」——填书名建书、选一个 TXT 导入（切章落库），
然后进工作台。已有项目时，顶栏「＋新书 / 导入」随时再建一本或往当前书补导入。
（想要现成 demo 数据：`uv run python scripts/seed_demo.py /tmp/demo.db` 出一个 project_id，
用 `NH_DB=/tmp/demo.db` 起后端即可。）

## 构建（生产）—— **验证「打包后还对不对」走这条**

```bash
npm run build      # tsc -b && vite build → ../src/novel_harness/webui/
uv run nh serve --db book.db     # 一个进程，自动开浏览器
```

⚠️ **产物落在 Python 包里，不是 `frontend/dist/`。** 理由只有一条：`uv_build` 会把模块
目录下的任何文件打进 wheel，但不支持从模块外 force-include——产物落在包内，`uv build`
才自动带上前端。落在 `dist/` 就得再养一套 build hook。为什么这么定见 `vite.config.ts` 的注释。

`webui/` 存在时 FastAPI 的 `/` 直接服务它、`/assets/*` 也由它挂载（见 `api/app.py`）；
不在时降级到 `api/static/index.html` 那个原生 JS 原型（只读也能用），**并且 `nh serve`
会打一行黄字告诉你**——一个不报错的降级是最难自查的故障，得由起服务的那一刻说出来。

这条路径由三个东西守着，别拆：
- `tests/test_serve.py::test_webui_lives_inside_the_package` —— 产物必须在包内
- `tests/test_serve.py::test_vite_outdir_and_dist_agree` —— **Vite 写到哪**和
  **FastAPI 从哪读**是两个分处 ts / py 的字面量，改一个忘一个不会有任何东西红
- `ci.yml` 的 packaging job —— 验「wheel 里有没有 webui」+「装完之后找不找得到」。
  ⚠️ **它至今一次都没跑过**：这个仓库还没有 git remote。上面两条 pytest 是今天唯一真在跑的守卫。

## 测试

```bash
npm test           # vitest run（CI 的 frontend job 跑这条 + npm run build）
npm run test:watch
```

**fixture 不是手写的，别手写。** `src/__fixtures__/api.json` 由
`tests/test_frontend_contract.py` 从真 app（`TestClient` + 真 SQLite）dump 21 个端点的
真响应，规范化掉 ULID / 时间戳 / 路径之后冻住；组件测试吃的就是这一份。

这条缝两头各有守卫，缺一头都拦不住「后端改了、前端没跟上、还没人发现」：

| 谁红 | 拦的是什么 |
|---|---|
| `pytest tests/test_frontend_contract.py` | 后端出参变了（重新 dump 和冻住的逐字节比对） |
| `npm test` | 形状变了但没人改组件（组件渲不出来） |

**改了后端出参之后**：`NH_UPDATE_FIXTURES=1 uv run pytest tests/test_frontend_contract.py`，
**然后看 git diff**——那个 diff 就是前端要跟着改什么的清单。不看就更新等于把守卫关掉。

为什么不能手写 fixture：`tsc` 看不见后端，`src/api/types.ts` 又自称「手写真相源」——
手写的东西和后端一致只是**当时**一致。再拿手写 fixture 去测组件，就成了两份手写的东西
互相验证，而这正是这条缝原本的病。

## 类型

```bash
npm run gen:types  # 从 FastAPI 的 openapi.json 生成 src/api/schema.ts
```

⚠️ 当前收窄端点（resolve / subgraph / state / nodes）签名是 `-> Any`，openapi 里没有
response schema，生成的响应类型很薄。**`src/api/types.ts` 是这些形状的手写真相源**。
让 `gen:types` 真正兑现 = 给后端收窄端点补 `response_model`——那是后续。

## 现在做到哪（骨架 + declare 写闭环）

- ✅ **上手不碰命令行**：浏览器里建书 + 导入 TXT（GBK 老稿前端自动兜底解码），多项目切换，
  **建人物 / 地点 / 秘密 / 别名**（左栏花名册旁的 ＋ → `RosterDrawer`，2026-07-25 补）
- ✅ 两个页面：工作台 + 章节准备（写第 N 章前的确定性简报）
- ✅ 三栏工作台：左栏章目录/花名册、中栏 **CodeMirror 6** 编辑器、右栏智能面板（6 tab）
- ✅ 版本对比（快照 diff）、场景块编辑、底栏场景时间线
- ✅ 认知矩阵头牌（三态 ✓/⚠/✗ + since_chapter）、当前状态卡、约束、R4 check
- ✅ **declare 写闭环**（前提是花名册里已经有人——现在同一个界面里就能建）：编辑器选一句原文 → 抽屉里选类型 + 填称呼 → `测这条引语`
  （locate 预览唯一性）→ 声明 → 回执展示**系统算出的 valid_from + 自动闭合的旧边**。
  **全程没有章号输入框**（约束 10）。歧义弹候选，服务端绝不替作者挑。
- ✅ TanStack Query（服务端状态唯一缓存）+ Zustand（只放坐标）

### ⛔ 已知的洞

**清单的唯一副本在 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#工作台的已知洞)
的「工作台的已知洞」——这里不留第二份。** 它曾经在 README / CLAUDE.md / 本文件各有一份拷贝，
补掉一个洞要记得改三处；改漏了就有一份文档在骗人，而那比没有文档更糟。

今天最该记着的那一个：**只有 3 个组件有测试**（`KnowledgeMatrix` / `LeftRail` / `RosterDrawer`）。
`CenterEditor` / `LocalGraph` / `BottomBar` / `DeclareDrawer` / `ChapterPrepPage` 等仍是零——
契约那一层已经钉住了（见上面「测试」），但**组件自己渲染错**仍然只有作者会第一个撞上。

### 有意留到后续

- **局部图连续漫游的全屏页（P3 GraphExplorer）**：现在 Tab2 是局部子图（hops≤2）。
  Cytoscape 全屏漫游是 P3。
- **时态区间的历史收口**：底栏时间线现在画「当前有效」的边，被 supersede 的历史区间
  需要一个「取节点全历史边」的 reader（后续引擎加法）。
- **openapi-typescript 兑现**：收窄端点补 `response_model` 后，生成的类型才够厚。
