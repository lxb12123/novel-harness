# ADR 0052：默认不开思考，作者可以拨开并给它预算

- **状态**：已接受（2026-09-13，维护者裁定）
- **日期**：2026-09-13
- **推翻了**：不是原始需求文档。**它扩展 [ADR 0011](0011-bilingual-draft-length.md) D4 / D5**，
  不改写那两条：产品 reasoning 默认仍是 `off`，`off` 仍指线上真的发「关」；多出来的是
  **作者能自己推翻这个默认**，以及推翻之后预算怎么算。
- **相关**：[ADR 0012](0012-settings-not-in-the-book.md)（这两位存在作者机器上、不跟书走）、
  [ADR 0044](0044-structured-output-on-the-wire.md)（同一条路由上另一次「prompt 里求的事要发到线上」）。

## 真书上的那一天

作者的路由是一条中转 + `deepseek-v4.1-flash`——没登记，能力解析出来方言是 `NONE`。
产品对总结 / 抽取声明的 reasoning 是 `off`，但这条路由在线上表达不了「关」（`off` 在 `NONE`
方言下什么字段都不发），而省略字段的端点**默认开着思考**。思考和正文共用 `max_tokens`，
`off` 那一档的预算又是按「没有思考」算的：

| 调用 | `max_tokens` | 发生的事 |
|---|---|---|
| 章节总结（120 字） | 1,264 | 思考先吃掉 800～1,200；**75 / 99 次 `content` 为空**，3 次交回被截断的两个字 |
| 章节抽取（JSON） | 8,192 | 41 / 55 次 `finish_reason=length` 恰好卡在 8,192；42 / 55 条 run 失败 |

屏幕上是 722 章里一片红的「异常」。三条出路（关掉思考 / 留着思考按思考算预算 / 后台换模型）
摆给维护者之后，裁定如下。

## 决策

1. **默认不开思考，不管什么模型。** 维护者原话：「默认就是没有思考。默认不管什么模型都这样
   不开思考。」这句话有两层：产品对每一次调用声明的仍是 `off`（ADR 0011 D4 一字不动）；
   **不许按模型自动判「这个会思考所以默认开」**——拨开的只能是作者本人。
2. **设置页「系统功能」多一颗开关「是否允许模型思考」**（`Settings.allow_thinking`，默认关）。
   拨开之后才露出一格「思考预算」（`Settings.thinking_budget`）：**留空 = 地板值**，作者只能
   往上调，到顶为止。地板 `THINKING_BUDGET_MIN = 8,192`、顶 `THINKING_BUDGET_MAX = 32,768`，
   两个数**由后端回给前端**（`thinking_budget_min` / `_max`），占位符、范围说明、越界判断都读
   它们；范围外的数 HTTP 层当场 422，不 clamp。为什么是这两个数写在 `draft/capabilities.py`
   那两个常量的注释里（地板 = 实测下界的 ~8 倍；顶 + 最大可见预算仍在注册表最小输出上限之下）。
3. **预算是加法，不是比例。** `off` 上 `required = visible + thinking_token_budget`
   （`plan_call` / `plan_structured_call` 各多一个入参，两份 plan 各多一位）。ADR 0011 D5 那个
   按审计预留比放大的公式只管非 `off` 的档位，两本账不叠加（预留 > 0 且档位非 `off` 当场
   `CapabilityError`）。预留为 0 时 plan **逐字节等于从前**——M2 三臂 / gate 那条路永远是 0。
   `budget_formula_version` 不动：v1 公式在预留为 0 时原样成立，预留本身是 plan 上一条自述的字段。
4. **线上：预留 > 0 时一个 reasoning 字段都不发。** 「允许」不是「要求」：不发「开」，只是
   不再发「关」，端点按自己的默认来（DeepSeek 省略 = high，GPT-5.6 省略 = medium，没登记的
   路由本来就什么都收不到）。预留为 0 时照旧按方言发「关」（ADR 0011 D4 原样）。
5. **换算只写一处，七处调用都从那儿拿。** `api/deps.py::author_thinking_budget()`：关 → 0；
   开、没填 → 地板；开、填了 → 那个数。抽取 / 总结 / 事后核对（`deps`）、行内续写（`app`）、
   对话摘要 + 回话（`chat`）、起草台（`agent/drafting`，由装配层递进去——`agent/` 不读设置，
   同它拿能力证据的方式）。`tests/test_thinking_budget.py` 走 AST 钉住每一处都带了这个入参：
   漏一处的症状是「开关拨开了，一半的功能听、一半不听」，而不听的那一半屏幕上只是「总结还是红的」。

## 为什么不是别的做法

- **不把预留挂在 `ProviderCapabilities` 上。** 那个对象装的是这条路由的**证据**
  （`windows.py::capabilities_from_author` 明写「除了窗口，其余一位都不动」）；作者的预留是
  「这一次要多少输出」，和 `interruptible` / `prompt_token_budget` 同类，是 plan 的入参。
  代价是要穿过七处调用，所以补了那条 AST 守卫。
- **不按模型名猜方言去「真的关掉」。** ADR 0011 D4：「禁止从模型名前缀猜能力」「adapter 只向
  明确支持的 route 发送对应 wire 字段」。于是**在一条没登记的路由上，默认关等于「不发」**，
  而那正是真书上思考照样发生的原因。这条 ADR 的正文不修那个缺口——它给作者一条能自己走通
  的路。要让「默认关」在那条路由上真的关，两条正路：把那条路由（含它认哪种「关」）查证后
  登记进注册表；或者给作者一格「这条路由的推理方言」（D4 顺序里第一档 operator override
  本来就留着这个口子）。**前一条当天夜里就做了，见补记。**
- **不按 0.95 的审计比例放大。** 那个比例是对官方 DeepSeek `high` 的审计结论，套到一条中转
  上是替它编；作者给的数是他自己定的上限，加法说得清「多出来的这一截是谁要的」。

## 代价

- 抽取那一档（可见 8,192）加地板之后是 16,384，过了 16,000 的流式阈值——那条调用从此走流式。
  这是对的：一次带思考的调用可能跑几分钟，流式正是为「一次阻塞往返扛不住」开的；不吃流式的
  端点照旧被 `_NO_STREAM` 那条学会。
- 作者把顶值填给一个小窗口的模型时，plan 照旧当场拒（不 clamp），后台那一支的失败形态是
  总结红、通知里一条「生成异常」——和填错窗口的后果同形。
- **这条 ADR 不改变红色章的重试策略**：同一 basis 已有 FAILED 的单仍是 `attention_required`
  （`chapter_refresh.ensure_refresh_coverage`），芯片悬浮说明里那句「将自动重试」今天仍然
  说得比做得多。拨开开关之后，红的那些要等正文 / 规则集换过版本或 mask 变化才会再下单——
  那是另一条待办。

## 落地

- 后端：`draft/capabilities.py`（`THINKING_BUDGET_MIN / MAX`、两份 plan 的 `thinking_token_budget`、
  `_required_budget` / `_expected_required`）、`draft/provider.py`（预留 > 0 不发 reasoning 字段）、
  `settings.py`（`allow_thinking` / `thinking_budget`）、`api/app.py`（`SettingsBody`、出参含
  地板 / 顶）、`api/deps.py`（`author_thinking_budget` + 三处）、`api/chat.py`（两处 + 递给起草台）、
  `agent/model.py`（`agent_call_plan` 入参）、`agent/drafting.py`（`chapter_drafter` / `ChapterDesk`）。
- 前端：`SettingsDrawer.tsx`（「系统功能」栏那张卡）、`api/types.ts`、`styles.css`（`.set-row-head`）。
- 守卫：`tests/test_thinking_budget.py`（算术 / 线上形状 / 换算 / 七处一处不漏 / 换算只写一处）、
  `tests/test_settings.py`（默认关、往返、范围）、`SettingsDrawer.test.tsx`（六条）。

## 补记（2026-09-13 夜）：那条路由探过了，登记了

维护者：「可以去做一下」。六条 64-token 探针（`只回一个字：好`，`max_tokens=64`），
路由 `https://opencode.ai/zen/go/v1` + `deepseek-v4.1-flash`，客户端就是产品那一个
（`provider._build_client`，带 `x-opencode-session` 和自己的 User-Agent）：

| 发了什么 | `reasoning_content` | `completion_tokens`（其中 `reasoning_tokens`） | 结论 |
|---|---|---|---|
| 什么都不发 | 有（87 字） | 52（50） | **默认思考，且思考算在输出里** |
| `thinking: {"type": "disabled"}`（DeepSeek 方言） | 无 | 1 | **关得掉** |
| `reasoning_effort: "none"`（OpenAI 方言） | 无 | 1 | 也关得掉（顺带） |
| `reasoning: {effort: none, exclude: true}`（OpenRouter 方言） | 有（138 字） | 40（38） | **被忽略** |
| `thinking: enabled` + `reasoning_effort: low` | 有（57 字） | 37（35） | 认 |
| `thinking: enabled` + `reasoning_effort: high` | 有（58 字） | 16（14） | 认 |

`GET /models` 只回 id；`opencode.ai/docs/go` 与 `/docs/zen` 只有价目，**没有上下文 / 输出上限**。

于是注册表多了第一条中转路由（`capabilities.py::_build_registry`，`source =
registry:opencode-go-deepseek-v4.1-flash`）：DeepSeek 方言、`off / low / high`、思考共享输出、
两个上限 `None`（没公布就不编，作者手填的窗口照旧压在上面）、`reserve_ratio_high` 也不编
（没在这条路由上审计过比例；产品每一处请求的本来就是 `off`）。**只登记查证过的这一个型号**——
`/models` 里另外四个 deepseek 一个都没探。

验收走的是产品自己那条路（`api/deps._analyze_summary`，读作者真设置，开关关着）：真书第 670 章
（当天失败两次的那一章，7,516 字）**4.7 秒回来，`finish_reason=stop`，99 个 token，113 个字的总结**；
同一章上午的两次是 ~9 秒后交回空文本。

这一笔改变的是这条路由上「默认」的含义：**默认真的关了**，开关只在作者要思考时才有用。
其余路由一个字节没变（`test_registered_routes_never_touch_the_wire` 参数化在整张表上，新条目自动进）。

