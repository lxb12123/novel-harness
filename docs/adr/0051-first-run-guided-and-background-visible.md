# ADR 0051：第一次打开——先连模型，再让后台看得见

- **状态**：已接受（2026-09-13，作者第一次正式用桌面版之后提的：「这个初始化的整体阶段都很差劲，
  我觉得需要有点统一」）
- **日期**：2026-09-13
- **推翻了**：不是原始需求文档。推翻的是本仓库自己的两条习惯——「后台整理只在活动记录里留痕、
  屏幕上不常驻任何状态」（[`no-engine-mechanism-on-the-authors-screen`] 那一条被读过了头），
  和「30 分钟扫描第一轮也等 30 分钟」。
- **相关**：[ADR 0050](0050-desktop-shell.md)（桌面壳；它的补记记着这一天的前四处）、
  [ADR 0012](0012-settings-not-in-the-book.md)（连接设置不跟书走）、
  [ADR 0020](0020-clean-extraction-auto-canon.md)（抽取直接进 CANON——本 ADR 只管作者看得见它在跑）。

## 作者原话里的四件事

导入一本书之后：角色册是空的，指去「事件」；「分析本章」按下去按钮变黄，换个 tab 回来就丢；
报错在右下角停两秒就没了；再点，闪一下，没反应、也不报错。

> 当用户初次导入一本书的时候，系统后台应该默默判断这个 key 模型配置了没有……这边的文字最应该
> 首选是引导用户配置一个模型……在引导前边应该加个说明的语句……配置好 key 后就应该开始生产 1 次
> ……铅笔这个图标右边处加一个状态灯。

## 决策

1. **「配好了没有」只判一处，判在后端。** `api/deps.py::model_configured()`：服务地址 / 模型 /
   钥匙三样都在（设置页优先、环境变量兜底，同 `_byok_values`）。`GET /api/settings` 多回一位
   `model_configured`，每一格、灯、续写都读它，前端不许自己再判一遍（环境变量那一档它看不见）。
2. **没配好，每一处靠模型的地方先说「先连接模型」**（`ModelGuide.tsx`）：一句开场白说这一格为什么
   是空的，再画那条路——⚙ AI 设置 → 模型服务 → 服务地址 · 模型 · API 密钥 → 应用，每一站用设置窗
   里真正的名字；「AI 设置」是一颗链接，直接开到「模型服务」那一栏（`store.openSettings("link")`，
   那扇窗的开合从 `TopBar` 的局部状态搬进了 `store`）。四个消费方：角色册、事件、章节总结、写作助手
   的输入框上面；行内续写没配好就**不开口**（`shouldSuggest` 的 `modelConfigured`）；「分析本章」那颗
   按钮没配好时按下去是去配，不是去跑。
3. **设置窗「应用」三样缺一样就不发，说清缺哪样。** 后端「空 = 保持原值」会把少填的一次存成半套，
   屏幕上像存好了，模型却调不起来。
4. **配好那一刻就扫一轮；没配好不扫；进程起来 15 秒就扫第一轮。** `BackgroundRuntime.kick()`
   由 `PUT /api/settings` 在配齐时调（`app.state.runtime`）；`_loop` 在 `configured()` 为假时跳过
   本轮（不下单、不留通知——那时该说话的是灰灯）；`FIRST_SWEEP_DELAY = 15s` 取代「第一轮也等
   30 分钟」。
5. **后台看得见：顶栏笔尖右边一盏灯。** 灰 = 没配好（点它去配）；绿 = 配好了闲着；黄 = 正在整理
   ——悬浮那句按右栏当前那一格说它在生成什么（角色册 / 事件 / 章节总结 / 通知；检验规则不由模型
   生成，只报章号），排队的也报。灯读 `GET /api/projects/{pid}/background`（`running` / `queued`，
   判据借 dispatcher 领单那一条 `OUTSTANDING_WHERE`，另加 `extraction_run`），**每 4 秒一问**——
   这是全仓唯一一处定时轮询，理由：后台在另一条线程上跑，没有任何推送能把「跑完了」送到屏幕上。
   同一个 hook 在「有活 → 没活」那一刻把角色册 / 情节 / 通知 / 总结 / 活动记录全部重取：
   作者要的是自动出现，不是换一次 tab 碰运气。
6. **「分析本章」的三条**：忙态还看 `/background`（换 tab 回来还是忙的）；回来的是上一次失败的
   run 就立刻带 `force` 重发（第二次点击不许没反应）、回来的是早已成功的就说「这一版正文已分析过」；
   失败那句留到点掉 × 、再点或换章，成功那句照旧四秒走。

## 为什么不是别的做法

- **不用 SSE 推后台状态**：后台线程和 HTTP 之间没有现成的通道，为一盏灯造一条长连接，代价
  比每 4 秒一条两句 SELECT 的 GET 大得多。桌面壳的 uvicorn 不记访问日志，这条轮询不进日志。
- **灯的悬浮文字按右栏那一格说**，不按后台真正在跑的分支说：作者盯着的是那一格，他关心的是
  「我看的这个什么时候有」；两个分支（抽取 / 总结）本来就是同一张单的两支。
- **没配好不扫，而不是扫了失败再通知**：一本 722 章的书，半小时二十条「后台任务未完成」，
  而作者还没填钥匙——那是把系统的账推到他脸上（§10 约束 7）。

## 代价

- 前端多一条 4 秒的轮询（Web 调试线的 `--reload` 日志里会看到它）。
- 「配好了」的判据是三样非空，**不验钥匙对不对**：填错的钥匙灯也是绿的，第一次调用失败才知道。
  验钥匙要打一次真请求，那是另一件事（可能的下一步：灯的第四态）。
- 组件测试的默认设置从「没配过」换成了「配好了」（`test/harness.tsx`）：没配好的样子由各自的
  测试自己前置 `fixtures.settings`。

## 落地

- 后端：`api/deps.py`（`_byok_values` / `model_configured`）、`api/background_status.py`（新）、
  `api/background_runtime.py`（`configured` / `kick` / `FIRST_SWEEP_DELAY`）、`api/app.py`
  （`model_configured` 出参、`app.state.runtime`、`PUT /api/settings` 的 kick）、
  `chapter_refresh.py`（`OUTSTANDING_WHERE` / `iso_timestamp`）。
- 前端：`ModelGuide.tsx`（新）、`TopBar.tsx::StatusLight`、`api/hooks.ts::useBackgroundStatus`、
  `store.ts`（`settingsOpen` / `openSettings`）、`SettingsDrawer.tsx`（`initialTab`、缺项不发）、
  `CanonEventCast.tsx::AnalyzeButton`、`RosterTab` / `SummaryTab` / `ChatPanel` 的空态、
  `continuation.ts::shouldSuggest`。
- 守卫：`tests/test_background_status.py`、`tests/test_autonomy_runtime.py`（不配不扫 / kick）、
  `tests/test_settings.py`（三样缺一样不算配好）、`TopBar.test.tsx`（三态）、
  `backgroundStatus.test.tsx`（跑完重取）、`CanonEventCast.test.tsx`（忙态 / 重发 / 留话）、
  `SettingsDrawer.test.tsx`（缺项不发）、`RightPanel` / `SummaryTab` / `ChatPanel` 的引导那几条。
