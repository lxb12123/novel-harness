# Novel Harness 工作台（React 前端）

给不写代码的小说作者用的浏览器工作台，落在精简 SQLite 引擎 + FastAPI 薄壳上。
方案见 [`../docs/UI_ARCHITECTURE.md`](../docs/UI_ARCHITECTURE.md)。

## 跑起来（开发）

两个进程：后端 uvicorn + 前端 Vite（Vite 把 `/api` 代理到 uvicorn）。

```bash
# 0) 造一个空库（唯一一步命令行；之后建书/导入都在浏览器里做）
cd ..
uv run python -c "from novel_harness.db import connect, migrate; migrate(connect('book.db'))"

# 1) 后端（NH_BOOKS_DIR 可选：新书的稿子目录基址，默认 <库同级>/books）
NH_DB=book.db uv run uvicorn novel_harness.api.app:app --port 8000 --reload

# 2) 前端
cd frontend
npm install
npm run dev        # http://localhost:5173
```

打开后：库是空的 → 首屏就是「开始一本书」——填书名建书、选一个 TXT 导入（切章落库），
然后进工作台。已有项目时，顶栏「＋新书 / 导入」随时再建一本或往当前书补导入。
（想要现成 demo 数据：`uv run python scripts/seed_demo.py /tmp/demo.db` 出一个 project_id，
用 `NH_DB=/tmp/demo.db` 起后端即可。）

## 构建（生产）

```bash
npm run build      # tsc -b && vite build → dist/
```

`dist/` 存在时，FastAPI 的 `/` 直接服务它、`/assets/*` 也由它挂载（见 `api/app.py`）。
所以生产只跑一个进程：`NH_DB=book.db uv run uvicorn novel_harness.api.app:app`。
`dist/` 不在时降级到 `api/static/index.html` 那个原生 JS 原型（只读也能用）。

## 类型

```bash
npm run gen:types  # 从 FastAPI 的 openapi.json 生成 src/api/schema.ts
```

⚠️ 当前收窄端点（resolve / subgraph / state / nodes）签名是 `-> Any`，openapi 里没有
response schema，生成的响应类型很薄。**`src/api/types.ts` 是这些形状的手写真相源**。
让 `gen:types` 真正兑现 = 给后端收窄端点补 `response_model`——那是后续。

## 现在做到哪（骨架 + declare 写闭环）

- ✅ **上手不碰命令行**：浏览器里建书 + 导入 TXT（GBK 老稿前端自动兜底解码），多项目切换
- ✅ 三栏工作台：左栏章目录/花名册、中栏正文编辑器、右栏智能面板
- ✅ 认知矩阵头牌（三态 ✓/⚠/✗ + since_chapter）、当前状态卡、约束、R4 check
- ✅ **declare 写闭环**：编辑器选一句原文 → 抽屉里选类型 + 填称呼 → `测这条引语`
  （locate 预览唯一性）→ 声明 → 回执展示**系统算出的 valid_from + 自动闭合的旧边**。
  **全程没有章号输入框**（约束 10）。歧义弹候选，服务端绝不替作者挑。
- ✅ TanStack Query（服务端状态唯一缓存）+ Zustand（只放坐标）

### 有意留到下一轮

- **CM6 编辑器**（§2.4）：现在中栏是 textarea。写闭环只要选区文本当引语，textarea 够用；
  CM6 换来的段落级高亮 + 富文本是 P1 升级。
- **局部关系图 Tab2**（React Flow）：引擎 `/subgraph` 已就绪，前端只差渲染层。
- **消歧选择器复用 `/resolve`**：现在消歧走 declare 的 `ambiguous_name` 候选。
