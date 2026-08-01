# Novel Harness

中文长篇小说写作引擎。**一句话：唯一一个知道「谁在第几章还不该知道什么」的引擎。**

## 动手之前先读

**永远先读这两份：**

1. **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** ← **入口。** 系统是什么、怎么分层、范围怎么一层层升上去（v1 / v1.1 / v2）、10 条不可违反的约束。
2. [`docs/adr/`](docs/adr/) —— 单个决策为什么这么定、推翻了原始需求文档的哪一条、错了修复成本是什么。**动到某个决策相关的代码前先读对应 ADR。**

**按你要动的东西再补一份：**

3. [`docs/UI_ARCHITECTURE.md`](docs/UI_ARCHITECTURE.md) —— **动 `frontend/` 或 `src/novel_harness/api/` 之前必读。** 把作者的 UI 设计稿（画的是 Neo4j + Qdrant + TipTap 的原始大架构）逐项落到现有 SQLite 引擎上的方案：**三栏**布局（顶栏 + 左 210px + 中 flex + 右 400px + 底栏）、路由契约表、哪些能力要等 M2/M4。冲突以 ARCHITECTURE.md 为准。
4. [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md) —— **动 `eval/` 或 `draft/` 之前必读。** M2 kill-gate 的预注册：三臂设计、判分口径、什么结果算项目核心主张成立、什么结果算它不成立。**看到结果再定及格线 = 作弊**，所以这份必须先于任何 `runs/*.jsonl` 存在。
5. [`docs/PLAN.md`](docs/PLAN.md) —— 92KB 完整实施计划。**不要整份读**，按需 Grep（§5 技术裁决 / §7 里程碑 / §8 开工清单 / §9 骨架）。

原始需求文档在 `~/Downloads/Novel_Harness_完整聊天与Claude开发需求.md`。**它是历史，不是权威**——ARCHITECTURE.md 和 ADR 已经系统性地推翻了它的多处决定（Neo4j、Qdrant、TipTap、全书抽取、13 状态机）。**遇到冲突以 ARCHITECTURE.md 为准。**

### `docs_dev/` 是维护者的，不是你的

> **先分清两个人。** 本仓库里**「作者」一律指小说作者**，也就是产品的最终用户——README 里那个「用 WPS 不想碰命令行」的人。
> 而 `docs_dev/` 属于**维护者**（开发这个项目的人）。两者不是同一个人，别在文档和 UI 文案里混用这个词。

[`docs_dev/`](docs_dev/) 放维护者平时自己做决策留下的 md，命名是 **`YYYY-MM-DD-主题.md`**（时间在前，便于按时间排；主题用中文，说清这次定了什么）。规矩：

- **不主动读、不主动写、不主动整理。** 被点名了（「记一下」「看看我上周那份」）才动它。
- **它不是权威。** 它记的是维护者当时怎么想、为什么这么选。要变成工程约束，必须落进 `docs/` 或开一份 ADR——否则就有两个真相源了。
- 被叫去往里写时，按上面的命名规范建**新文件**，别往旧文件里追加（一份决策一个文件，时间戳才有意义）。

**它不会分发给小说作者。** `uv_build` + src-layout 只打包 `src/novel_harness/`，没有 MANIFEST.in 也没有 force-include，
所以 `docs_dev/`、`docs/`、`tests/` 都不进 wheel/sdist（sdist 里只有 `PKG-INFO` / `pyproject.toml` / `README.md` / `src/`）。
装包的人看不到它，只有 `git clone` 的开发者看得到。前端产物进 wheel 走的是「产物落在包内」
（2026-07-25），**不是 force-include ——别为了别的东西开那个口子，一开 `docs*/` 就会跟着漏进去。**

#### ⏳ 临时规则：做完一件事就回去打勾（**清单空了就删掉本小节**）

**这是上面「不主动写 `docs_dev/`」的唯一例外**，且只对一个文件的一节生效：
[`docs_dev/2026-07-25-架构现状与完成度快照.md`](docs_dev/2026-07-25-架构现状与完成度快照.md)
里的 **「待办看板」**。那份文件的其余部分是当天的快照，**照旧不许改**。

完成看板上任意一项时，在**同一次改动里**：

1. 把那条 `- [ ]` 改成 `- [x]`，后面补「YYYY-MM-DD 完成 + 一句话怎么做的」；
2. **如果它同时改变了架构事实**（新模块、洞被补上、里程碑推进、测试数变了），
   **必须同步改 [`ARCHITECTURE.md` 的「当前状态」](docs/ARCHITECTURE.md#当前状态)**——
   那儿是权威，看板只是清单。**只改看板不改那边 = 又造出一份会骗人的文档**，
   而这个仓库刚刚才把「同一份清单三处拷贝」的问题清掉。

**自毁条件**：看板上所有 `- [ ]` 都变成 `- [x]` 的那一刻，做三件事——
① 删掉本小节；② 在看板里记一句「YYYY-MM-DD 全部完成，规则已移除」；
③ 把那份文件整份重写成一份新日期的快照。**别让这条规则在清单空了之后还留着。**

## 这个项目最容易犯的错

按「会让项目死」的顺序：

1. **加回被砍掉的东西。** Neo4j / Qdrant / TipTap / LLM Validator / 状态机 / Policy Engine 都是**有意砍的，且每条都有 ADR 和触发条件**。想加之前先读 ADR，确认触发条件真的满足了。「这样更完整」不是理由。
2. **写需要理解语义的规则。** 铁律：**只做集合判断，不做语义判断**。任何要回答「这句话是什么意思」的规则一律不进 v1（ADR 0005）。违反 = 5–20 条误报/章 = 作者弃用。
3. **在 `graph/queries.py` 之外写时态过滤。** 闭开区间 `[valid_from, valid_to)` 全系统只实现一次。`tests/test_arch_guard.py` 拦三道，**判据是「谁在碰图表」，不是「谁 import 了 sqlite3」**：① `graph/` 外 `import sqlite3`；② `graph/` 外对图表（`edge_type`/`edge`/`node`/`alias`/`secret`）写 SQL 字面量；③ `connect` 只有装配层（`db.py`/`cli.py`/`__main__.py`）能拿。只有 ① 是实测**不够**的——一个文件写 `from ..db import Connection, connect` 就能裸写第二份时态过滤、漏掉 `evidence_status != 'STALE'`，而 `sqlite3` 一次都不出现（那个绕法作为 probe 钉在守卫的自守卫里）。它是**边界守卫**不是唯一性守卫：`graph/` 内部写第二份，仍然只有 review 拦得住。
4. **让 `dict` 或 `sqlite3.Row` 越过 StoryGraph 接口。** 出参必须是 Pydantic。
5. **让完整 PLANNED 进 Writer prompt。** 只能转译成 `must_not_reveal` / `forbidden_entities`。泄漏 = 项目核心主张破功。
6. **让作者填章号。** `valid_from` 只由证据决定。表单里有章号输入框 = 邀请污染。
7. **主动推队列给作者。** 系统不确定时的默认动作是**闭嘴**，不是提问。

## 命令

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run nh --help
bash scripts/demo.sh                                                        # 心跳：端到端还通着吗

# ── M2 kill-gate 的合成小册子（`synth/`，仪器不是产品，不进 wheel）────────────
uv run python synth/build.py                # booklet.toml + booklet.txt → 库 + ground_truth.json
uv run python -m synth.leak_selfcheck       # ⚠️ 必须 -m：它和 build.py 共用一份 schema，
                                            #    `python synth/leak_selfcheck.py` 会 ImportError
# 真跑一轮（**会调模型、会花钱**：25 陷阱 × 3 臂 × 3 次 = 225 次生成）。作者永远不敲这条。
uv run nh gate --db synth/gate.db -p <pid> --ground-truth synth/ground_truth.json

# ── 工作台的三层，别混 ──────────────────────────────────────────────────────
# L1 日常开发（每分钟）：两个进程，热更新。改代码就走这条，不走 L2。
uv run nh serve --db book.db --no-open                     # 只为建库，看到 URL 就 Ctrl-C
NH_DB=book.db uv run uvicorn novel_harness.api.app:app --port 8000 --reload   # 8000 是 Vite 代理目标
cd frontend && npm run dev                                 # 前端 5173

# L2 集成验证（每周）：一个进程，验「打包后还对不对」。没有热更新。
cd frontend && npm run build && cd ..                      # 产物落 src/novel_harness/webui/
uv run nh serve --db book.db                               # 建库 + 起服务 + 开浏览器

# L3 发布验证（发版）：装出来的包里还有没有工作台。
# ⚠️ ci.yml 里写了 packaging job 验这个，但**本仓库至今没有 git remote，CI 一次都没跑过**。
#    在建起远端之前，下面这两行只有你手跑才作数——别把「CI 会拦住」当成既有保护。
cd frontend && npm run build && cd .. && uv build
uvx --from ./dist/novel_harness-*.whl python -c "import novel_harness.api.app as m; assert m.webui_built()"
```

**`npm run build` 的产物落在 `src/novel_harness/webui/`（包内），不是 `frontend/dist/`。**
`uv_build` 只打包模块目录下的东西且不支持从模块外 force-include——落在包内，`uv build` 才自动带上前端。
Vite 的 `outDir` 和 `api/app.py` 的 `_DIST` 是**两个必须同时改的字面量**，
`tests/test_serve.py::test_vite_outdir_and_dist_agree` 就是钉这条缝的（它坏掉时没有任何别的东西会红）。

## 约定

- Python 3.12（已 pin）。`from __future__ import annotations`，类型标注齐全。
- ruff line-length=100。`docs/adr/bench/` 排除在 lint 外——**它是 ADR 0001 的实测证据，改写它 = 改写论证**。
- 注释用中文，只写「代码本身表达不了的约束」。
- 规则是纯函数 `check(ctx: CheckContext) -> list[Issue]`（这是开源贡献者的入口，别破坏这个形状）。
- `Issue` / `Evidence` 的锚是 `(para_index, quote_text, occurrence_k)`，**禁止 offset**（ADR 0006）。

## 现在做到哪

见 [`README.md`](README.md) 的路线图和 [`ARCHITECTURE.md` 的「当前状态」](docs/ARCHITECTURE.md#当前状态)（**那一节是权威，本节只是索引**）。**M2 进行中，pytest + vitest 全绿。**

> **本节不写数字。** 测试条数、路由条数、子命令数这类会随每次改动漂的量，
> **唯一副本在 `ARCHITECTURE.md` 的「当前状态」**，这儿只留指针。
> 2026-07-30 的审计发现「620 个 pytest」在三处、「27 条路由」在七处各躺一份拷贝，而真值早就变了——
> 同「工作台的已知洞」那条规矩，且 `tests/test_doc_numbers.py` 现在会拦住重新抄一份的行为
> （它钉运行时数得出来的那些；pytest / vitest 两个数它罩不住，仍靠人手改那一处）。

已落地：数据层 / 图层 / panel / R4 / `text/{anchor,chapterize,scenes}` / 声明层 `declare.py` / `nh` 的子命令（M0+M1），
FastAPI 壳（`api/`）+ React 工作台（`frontend/`，手写 TS/TSX）（M1.5）。`demo.sh` 心跳绿着。
**前端 ↔ 后端契约由两头钉住**：`tests/test_frontend_contract.py` 从真 app dump 一批端点的真出参冻成 `frontend/src/__fixtures__/api.json`（出参一改 pytest 红），组件测试吃**同一份** fixture（形状变了没改组件 vitest 红）。**别手写前端 fixture**——两份手写的东西互相验证正是这条缝原本的病。改了后端出参跑 `NH_UPDATE_FIXTURES=1 uv run pytest tests/test_frontend_contract.py` 然后看 git diff。

**M2 kill-gate 的链已经合拢，但还没通电。** 判分侧（`eval/{leak,score,confound_lint}.py`）、
被判侧（`draft/{provider,context,assemble}.py`）、产出侧（`eval/runner.py` + `nh gate` + `synth/`）
都已落地并有测试。**建造顺序「判分器先于被判者」是有意的**：先有卷子和判分口径再有被判的东西，
「看到结果再定及格线」在结构上就做不到。
**修正案 5 与 ADR 0011 已预注册，长度/provider/续写实现仍待落地**；全量离线验证后才冻结
endpoint/profile 跑第一轮，跑完且检查 JSONL 才写 ADR 0009。
**差集和依赖顺序以 [`ARCHITECTURE.md` 的「当前状态」](docs/ARCHITECTURE.md#当前状态)为准，那儿是唯一副本**——
这份清单在 2026-07-30 之前两处拷贝一起过期，把三样已经写完、被 60 条测试覆盖着的东西说成「一个字符都没有」，
照它排期的人会去重写已完成的工作。**「已做完文档说没做」比反过来更贵。**

协议冻在 [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md)，**2026-07-25 已单独提交（`0393088`），预注册成立**——
它自称「先 commit 的 git 时间戳」是唯一证据，那条 commit 就是它（当时 `runs/` 还不存在）。
**此后再改协议 = 改卷子**：真要改就开新的一份并说明改了什么，别覆盖那条 commit 的内容。
**这件事已经做过五次**：`docs/EVAL_PROTOCOL_AMENDMENT_{1,2,3,4,5}.md`
（口径不自洽 / 裁决表重叠 / 措辞歧义 / **`prior` 不许含 tell 让 gate 恒判 INVALID** /
长度、续写与通用 provider 档）。都另开文件、冻结正文一字未动；1–4 早于 `synth/`，
第 5 份晚于 `synth/`，但五份都早于真实推理，所以仍属预注册。
**动 `eval/` 或 `draft/` 之前必读的是「协议 + 这五份修正案 + [ADR 0010](docs/adr/0010-writer-boundary.md) + [ADR 0011](docs/adr/0011-bilingual-draft-length.md)」，不是协议一份。**

**四条真书验收，一条都没验。但堵点不一样，别用一句「缺一本书」盖过去**——其中两条**同时还缺代码**，
书到手也验不了。在验之前这些数字都是未知，别在文档里替它们编一个：

**纯缺书（测量代码齐全）：**
- `scripts/probe_speaker_tags.py` 能跑了（`CHAPTER_RE` 从 `text/chapterize.py` import，不留第二份副本；
  `SPEAKER_RE` / `QUOTE_RE` 是脚本自己的），但**覆盖率仍未测量**。≥10% 则 R5 进 v1，<10% 当场砍。
  结果填进 ADR 0005 的「实测结果」一节——**那节现在还是空的**。
  ⚠️ 填之前先对齐口径：**探针算的分母和 ADR 0005 判据表里的分母不是同一个**，直接填等于用 A 的数字触发 B 的阈值。
  （ADR 0005 里还留着**第二份 SPEAKER 正则**且已和脚本漂移。）
- `text/chapterize.py` 的验收「真书切章数 = 目录数」**一次都没验过**（fixture 是手写的 46 行 **3 章**——
  「第一卷 / 第二卷」不算章，`卷` 不在 `CHAPTER_RE` 里，那是故意的）。

**同时缺代码（书到手也验不了）：**
- M1 的花名册 90% 提及 —— 缺 `text/mentions.py`，也缺度量代码。M1 已被标「已落地」，这条从没测过。
- M3 的误报 < 1 条/章 —— **今天不可测，而且会假绿**：`ALL_CHECKS` 只有 R4，R4 不读正文、零 FP，
  真书一到手跑 `nh check` 会输出接近 0 条 issue，**自动「通过」这条门槛**。
  那正是 `demo.sh` 自己警告的「一张漂亮的空表 + exit 0」。要先有 R2/R3，这条才有意义。

（合成小册子有强制说话人标签，**不得拿 M2 的实验冒充它们中的任何一条**。）

**工作台已知的洞**——**唯一副本在 [`ARCHITECTURE.md` 的「工作台的已知洞」](docs/ARCHITECTURE.md#工作台的已知洞)，
别在这儿写第二份**（它曾经有三处拷贝，补掉一个洞要记得改三处，改漏了就有一份文档在骗人）。

> 这儿原本有一句「最硬的一个是浏览器里建不了人物/别名（前端零调用）」——**它就是上面那条规矩的第一个受害者**：
> `dadab97` 把那个洞补掉了，权威副本也改了，唯独这份拷贝漏了，于是它骗了人整整一轮。
> 所以现在这儿**只留指针，不留内容**。想知道还剩哪些洞，点上面那个链接。

**PyPI 是有意推迟的，不是漏了**（2026-07-25 决定）：`novel-harness` 至今 404，但**包名还没最终定**，
而 PyPI 先到先得、不可改不可转让——名字没定就注册等于上枷锁。后果只有一条：
**打 `v*` tag 会让 `release.yml` 最后一步红**（那儿写了注释）。在此之前发版走 GitHub Release 附 wheel。
