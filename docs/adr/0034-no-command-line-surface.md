# ADR 0034：命令行面整个删掉 —— 产品是桌面壳，调试是 Web 工作台，没有第三块表面

- **状态**：已接受
- **日期**：2026-08-20
- **推翻了**：不是原始需求文档，是**本仓库自己**——[ADR 0012](0012-book-owns-its-db.md) 的入口叙事
  （「`novel-harness` 不带参数 = 向上找最近的 `book.db`」，那是 `__main__.py` 存在的全部理由）、
  [ADR 0007](0007-manuscript-lives-on-disk.md) 里「uvx 一条命令」那一截的落地形态、
  以及 `PLAN.md` 通篇按命令行写的那个作者面。
- **相关**：[ADR 0007](0007-manuscript-lives-on-disk.md)（正文在磁盘、作者自己管文件）、
  [ADR 0012](0012-book-owns-its-db.md)（库跟着书走）、
  [ADR 0009](0009-m2-verdict.md) + [修正案 9](../EVAL_PROTOCOL_AMENDMENT_9.md)（M2 裁决，
  gate「保留为可跑仪器」——那句话是这次唯一被保下来的入口的理由）、
  [ADR 0033](0033-pre-draft-calibration-scene-brief.md)（迁移编号约定，同期的另一处收口）

## 决策

**删掉整块命令行面。** `nh` / `novel-harness` 两个可执行、17 个子命令、`cli.py`（1307 行）、
`__main__.py`、`pyproject.toml` 的 `[project.scripts]`、`typer` 依赖，以及
`test_cli` / `test_declare_cli` / `test_main` 三份测试，全删（合计 −2984 行）。

三条线从此各就各位：

| 线 | 形态 | 状态 |
|---|---|---|
| **产品线** | 桌面壳：启动器（建库 → 起内置服务 → 开窗口）+ 在工作台上交互 | 最终形态，壳本身待做 |
| **调试线** | Web 工作台（FastAPI + React），`python -m novel_harness.api` | 保留，日常开发就走它 |
| **删减线** | 命令行 | **本 ADR 删掉的东西** |

那 17 条的归置：

| 子命令 | 归宿 |
|---|---|
| `serve` | → `api/launch.py::launch()` 库函数（**桌面壳的地基**，见下） |
| `gate` | → `python -m novel_harness.gate`（唯一保下来的入口，见下） |
| `init` `import` `sync` `panel` `check` `draft` `summarize` `locate` `version` | **全删**——Web 工作台已 100% 等价 |
| `declare` 的 6 条（`character` `place` `secret` `alias` `dead` `appears`） | **全删**——声明抽屉（`DeclareDrawer`）等价 |
| `__main__.py` / `[project.scripts]` | 和 `cli.py` 一起删；`python -m novel_harness.api` 是唯一模块入口 |

## 为什么

**一、作者从来不敲它。** 产品的最终用户是 README 里那位「用 WPS、不想碰命令行」的作者。
命令行面从来不是他的界面——它是维护者调试时的界面，而维护者今天有 Web 工作台。
留着它 = 维护**第二块作者面**，而那块面没有用户。

**二、第二块表面的成本不是零，是双份的。** 每加一个能力要在两处实现、两处测、两处对措辞。
最贵的是措辞：`declare.py` 的三条拒绝消息曾经写着「先跑 `nh sync`」「用 `nh locate` 先试」
「先 `nh declare character`」，而这些字符串经 `api/app.py` 原样进 `message`、由抽屉逐字渲染给那位
不碰命令行的作者。为此专门长出了两张守卫网（`screenGuard.ts::SHELL_LINE` +
`test_wording_guard.py`）。**命令面删掉之后，那半张「命令名」表没有产品自己的命令可喂了**
——形状那半（`--开关`）留着，仍是作者屏幕的不变量。

**三、它已经在骗人了，而且不止一次。** `demo.sh` 心跳卡在一条早被删掉的 `nh declare knows`
上红着没人知道；更早一次是 R2/R3 进 `ALL_CHECKS` 而心跳还在等「跑了 1 条规则」，**红了四天**。
一块没人用的表面不会被人注意到坏了——它只会在别人依赖它的那天坏给你看。

## 搬家而不是删掉的两块，各有各的理由

**`serve` → `api/launch.py::launch()`。** `nh serve` 干的四件事（`connect` + `migrate` 建库 →
设 `NH_DB`/`NH_BOOKS_DIR` → 起 uvicorn → 挑端口 + 开浏览器）**是桌面壳的地基**，不是命令行的一部分。
提成库函数之后：桌面壳 `import` 它就能建库 + 起内置服务 + 开窗口；今天由 `start.command`
双击调用。`test_arch_guard` 的 `CONNECTION_OPENERS` 名单同步从 `{db.py, cli.py, __main__.py,
api/deps.py}` 换成 `{db.py, api/deps.py, api/launch.py, gate.py}`——**那份名单是「谁有资格开连接」，
不是「谁是命令行」**，所以它跟着装配层走。

**`gate` → `python -m novel_harness.gate`。** 它是**仪器不是产品**，作者永远不敲，
一个月不一定跑一次。但 EVAL_PROTOCOL 的预注册链、ADR 0009 和修正案 9 都指望它当入口
（修正案 9 的原话：「`nh gate` 保留为可跑仪器」），而四条真书验收一条都还没跑。
**保一个 30 行的薄入口成本≈0，不保才是省那 30 行。** 它明确不算 CLI：没有子命令、没有 `--help` 脚手架。

> 搬家时撞上一条没人写下来过的边界：**它不能住在 `eval/` 里**。
> `tests/test_draft_boundary.py` 那堵墙不许「开库 + 装配」进判分层——`cli.py` 从前在扫描范围外
> 所以没被咬，一搬进 `eval/gate.py` 就当场红。于是它落在**顶层 `gate.py`**，
> 并且顺手把 `_open_store` 的项目存在性检查从 `store.resolve()`（那是算约束的入口）
> 换成 `project.get()`（那才是查表）。**这条边界因此第一次被写下来。**

## 明确接受的损失

- **没有终端能用的作者面了。** 想脚本化地喂数据只能打 HTTP。这是有意的：
  `scripts/seed_demo.py` 那种维护者脚本直接调库函数，不需要经过命令面。
- **`uvx novel-harness` 那条「一条命令」的叙事没了。** ADR 0007 / 0012 的**实质**
  （稿子在磁盘、库跟着书走）一个字没变，变的只是谁来执行「找到库并打开它」——
  从 `__main__.py` 换成启动器 / 桌面壳。
- **wheel 装出来不再有 `nh` 可执行。** `uv build` 的产物里 `[project.scripts]` 是空的。

## 文档里那些 `nh xxx` 字样怎么读

**没有把历史文书重写一遍。** `PLAN.md`、`docs/superpowers/plans/*`、各份 ADR 的正文、
以及冻结的 `EVAL_PROTOCOL_AMENDMENT_*`（**动它们 = 改卷子**）里仍然写着 `nh serve` / `nh gate`
之类的字样——**那些是当时的记录，按定义就该保持当时的样子**。

判据是「这句话是在说今天，还是在说当时」：

- **说今天的**（ARCHITECTURE 的结构图与当前状态、UI_ARCHITECTURE、AGENTS/CLAUDE、
  `adr/README.md` 的状态列、M2/M4 那两份设计文档里的「入口是什么」）——**已随本 ADR 改掉**。
- **说当时的**（带日期的 `>` 引用块、「此前写着」、「2026-07-25 已补，当时是……」）——**原样留着**，
  必要时在后面追一段带日期的更正块，而不是涂改上一段。

## 若决策错误，修复成本

**低，但不对称。**

把命令行**加回来**很便宜：`launch()` 和 `gate` 的逻辑都已经是库函数，重新包一层 argparse
或 typer 是几十行的活，`git` 历史里还躺着那 1307 行原件。

真正贵的是**别的地方**：一旦重新长出第二块作者面，「两处实现、两处测、两处对措辞」的成本
和那两张措辞守卫网就一起回来了，而当年 `demo.sh` 红了四天没人发现的病根
（**没人用的表面不会有人发现它坏了**）也会一起回来。

**所以加回来之前先回答一个问题：这块面的用户是谁？** 如果答案还是「维护者调试用」，
那它和 Web 工作台重复；如果答案是「作者」，那它和 ADR 0007 里那位用 WPS 的人矛盾。
两个答案都不成立时，别加。
