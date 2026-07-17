# Novel Harness

中文长篇小说写作引擎。**一句话：唯一一个知道「谁在第几章还不该知道什么」的引擎。**

## 动手之前先读

1. **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** ← **入口，永远先读这个。** 系统是什么、怎么分层、范围怎么一层层升上去（v1 / v1.1 / v2）、10 条不可违反的约束。
2. [`docs/adr/`](docs/adr/) —— 单个决策为什么这么定、推翻了原始需求文档的哪一条、错了修复成本是什么。**动到某个决策相关的代码前先读对应 ADR。**
3. [`docs/PLAN.md`](docs/PLAN.md) —— 92KB 完整实施计划。**不要整份读**，按需 Grep（§5 技术裁决 / §7 里程碑 / §8 开工清单 / §9 骨架）。

原始需求文档在 `~/Downloads/Novel_Harness_完整聊天与Claude开发需求.md`。**它是历史，不是权威**——ARCHITECTURE.md 和 ADR 已经系统性地推翻了它的多处决定（Neo4j、Qdrant、TipTap、全书抽取、13 状态机）。**遇到冲突以 ARCHITECTURE.md 为准。**

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
uv build && uvx --from ./dist/novel_harness-*.whl novel-harness --version   # 打包路径冒烟
bash scripts/demo.sh                                                        # 心跳：端到端还通着吗
```

## 约定

- Python 3.12（已 pin）。`from __future__ import annotations`，类型标注齐全。
- ruff line-length=100。`docs/adr/bench/` 排除在 lint 外——**它是 ADR 0001 的实测证据，改写它 = 改写论证**。
- 注释用中文，只写「代码本身表达不了的约束」。
- 规则是纯函数 `check(ctx: CheckContext) -> list[Issue]`（这是开源贡献者的入口，别破坏这个形状）。
- `Issue` / `Evidence` 的锚是 `(para_index, quote_text, occurrence_k)`，**禁止 offset**（ADR 0006）。

## 现在做到哪

见 [`README.md`](README.md) 的路线图和 [`ARCHITECTURE.md` 的「当前状态」](docs/ARCHITECTURE.md#当前状态)（**那一节是权威，本节只是索引**）。**M0 进行中，406 个测试全绿。**

数据层 / 图层 / panel / R4 / `text/{chapterize,scenes}` / `nh {import,panel,check}` 都已落地，`demo.sh` 心跳绿着。
前端和 FastAPI 壳一行都没有——`fastapi` 在 `pyproject.toml` 里只是依赖声明。

**M0 剩下的全部卡在同一件东西上：手上没有一本真实中文小说 TXT。** 在拿到之前，下面这些数字都是未知，别在文档里替它们编一个：
- `scripts/probe_speaker_tags.py` 能跑了（正则从 `text/chapterize.py` import，不留第二份副本），但**覆盖率仍未测量**。≥10% 则 R5 进 v1，<10% 当场砍。结果填进 ADR 0005 的「实测结果」一节——**那节现在还是空的**。
- `text/chapterize.py` 的 M0 验收「真书切章数 = 目录数」**一次都没验过**（fixture 是手写的 46 行 5 章）。
- 同理还欠着：M1 的花名册 90% 提及、M3 的误报 < 1 条/章。**这三条都是真书门槛，不是代码门槛。**

另一件待办：PyPI 抢注 `novel-harness`（撰写时还是 404）+ 登记 trusted publishing。`release.yml` 已配好，缺账号那一步。
