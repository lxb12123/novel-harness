import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, sseFrames, turnStream, ROUND_DONE } from "../test/harness";
import { devTerms, engineWords, machineWords, screenText } from "../test/screenGuard";
import type { EdgeType } from "../api/types";
import { EDGE_LABEL, edgeLabelText } from "../backendMessages";
import { useCoords } from "../store";
import { ActivityLog } from "./ActivityLog";
import { RightPanel } from "./RightPanel";
import { TopBar } from "./TopBar";
import { LeftRail } from "./LeftRail";
import { RosterDrawer } from "./RosterDrawer";
import { SettingsDrawer } from "./SettingsDrawer";
import { HistoryDrawer } from "./HistoryDrawer";
import { BookShelf } from "./BookShelf";
import { ChatPanel } from "./ChatPanel";
import { RulesTable } from "./RulesTable";
import { SystemNotifications } from "./SystemNotifications";

// ReactFlow 依赖真实布局测量，jsdom 给不了（同 `LocalGraph.test.tsx` 生前的写法）。
// **这份文件现在需要它**：角色卡把「人物关系」并进角色册之后，下面两条新测试会真的
// 选中一个人渲出卡片，`CharacterRelations` 内嵌的画布跟着挂载。
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, edges }: { nodes: { id: string; data: { label: string } }[]; edges: unknown[] }) => (
    <div data-testid="flow">
      {nodes.map((n) => (
        <span key={n.id} data-testid="flow-node">
          {n.data.label}
        </span>
      ))}
      <span data-testid="flow-edge-count">{edges.length}</span>
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  // `CharacterRelations` 的自定义节点要用这两个（连线的锚点）。**mock 工厂缺一个
  // 具名导出，vitest 直接报错**，所以组件那边一 import 就得在这儿跟一个。
  Handle: () => null,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
}));

// **对抗性验证：那张「形状判据」的网真的比词表强吗。**
//
// 2026-08-11 那一轮（`src/test/screenGuard.ts`）把「屏幕上不许出现研发术语」的判据
// 从词表换成了形状，并把它挂在三个地方：`ActivityLog.test.tsx`、
// `CanonEdit.boundary.test.tsx`、`ProposalReviewTab.test.tsx`。
//
// 复核结论两句话：**判据比原来强得多，扫描面比原来窄得多。**
// 那三个地方合起来只覆盖「日志页 + 右栏的两个编辑器 + 待确认」——
// 工作台上另外十几块屏幕**一次都没被扫过**，而其中两块（底栏时间线、原文依据）
// 当时各躺着一份和 `ProposalReviewTab` 一模一样的病：
//
//   `TYPE_ZH[e.type] ?? e.type`   → `PLANTED_IN` 原样上屏
//   `roster.find(...)?.name ?? id` → `location:01J8XK…` 原样上屏
//
// 所以这份文件干两件事：
//
// 1. **把扫描面铺开** —— 作者点得到的每一块屏幕各扫一遍。没被扫到的组件等于没有守卫。
// 2. **钉住兜底那一支** —— 正常数据下这些分支永远不亮，正是它们躲过守卫的方式。
//    喂一个故意的坏形态进去（同 `CanonEdit.boundary.test.tsx` 的 `PROJECT_GONE`），
//    这不是「手写夹具」那条禁令的例外：夹具是喂正常数据用的，坏形态按定义不在里面。
//
// 判据本身的完整性（引擎每加一个枚举值，那张网都得跟着收）由
// `tests/test_wording_guard.py` 拿 **Python 的枚举本身**驱动，不在这里手抄一份清单。

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    cast: "",
    activeTab: "roster",
    page: "workbench",
    selectedNodeId: "character:ID10",
    highlight: null,
    focusEventId: null,
    chatId: null,
  });
  // ReactFlow 需要 ResizeObserver 做尺寸测量；jsdom 里没有（同 `LocalGraph.test.tsx`）。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

/** 一份**带边**的人物快照。`characterState` 那份 dump 的 `edges` 是空的，
 *  于是原文依据那块屏幕在守卫眼里永远是「这一章还没有原文依据」。
 *  边取自 `states` 那份 dump（同一个真 app 的另一条端点），**不是手写的**。 */
const STATE_WITH_EDGES = { ...fixtures.characterState, edges: fixtures.states[0].edges };

const stateRoute = (body: unknown) => [{ match: /\/characters\/.*\/state/, body }];

// ⚠️ **「场景条」这一格 2026-08-14 撤了**（组件连同场景块、两条路由和 R4 一起删了，
// [ADR 0027](docs/adr/0027-scene-blocks-cut.md)）。它是这张网上少数几个**从来没被真夹具
// 喂活过**的格子——`fixtures.scenes` 那份真 dump 是 `[]`，因为真书里没人手写 `## 场景 N`，
// 而那正是砍掉它的理由。
//
// ⚠️ **「底栏时间线」这一格 2026-09-06 也撤了**（`BottomBar` 整个组件删了）：那条底栏
// 画的是选中人物每条边的区间条，而 ADR 0043 之后 `valid_to_chapter` 恒为 NULL，
// 每一条画出来都是「从第 N 章起一直有效」——和角色卡上「状态」那一格说的是同一句话。
// 它手上那三条守卫**没跟着一起走**，去处写在下面各自那条上。

/** 真 dump 的那一轮里**除回执以外**的那几帧（ADR 0024 第二刀）。
 *  卡住回执就能把屏幕停在「还在跑」那一刻 —— 而那正是这三块新屏幕唯一活着的时候。 */
const TURN_MIDDLE = fixtures.chatTurnEvents.filter((f) => !f.startsWith("event: receipt"));

// ══════════════════════════════════════════════════════════════════════════
// 1. 扫描面 —— 作者点得到的每一块屏幕
// ══════════════════════════════════════════════════════════════════════════

describe("扫描面：那三个测试文件之外的每一块屏幕", () => {
  it.each([
    ["顶栏", <TopBar key="t" />],
    ["左栏书架", <LeftRail key="l" onOpenChapter={() => {}} />],
    ["书架页", <BookShelf key="bs" onOpenChapter={() => {}} />],
    ["角色册抽屉", <RosterDrawer key="rd" pid="project:ID1" onClose={() => {}} />],
    // 设置这扇窗 2026-08-14 变成左右分栏，**这一条只扫得到默认那一栏**——
    // 2026-09-05 起默认栏是「通用」（只有界面语言那一行），所以这一格扫到的东西
    // 反而是全窗最少的。**另外两栏由紧跟在这个 each 后面那条单独的断言扫**，
    // 那儿才是这扇窗上形状可疑的东西真正待着的地方。
    ["设置弹窗", <SettingsDrawer key="sd" onClose={() => {}} />],
    ["历史抽屉", <HistoryDrawer key="hd" pid="project:ID1" chapter={1} onClose={() => {}} />],
    // ⚠️ **「声明抽屉」这一格 2026-08-14 撤了**（组件连同中栏那条选区工具条一起删了）。
    // 它端着后端每一句拒绝，是这张网上覆盖面最广的一格——**撤掉它就是覆盖面变小**，
    // 不是覆盖面变干净。`declare.py` 那几句话今天只走 `nh declare` / `nh locate`，
    // 终端上说「先跑 nh sync」是对的，所以没有屏幕再需要挡它们。
    // 哪天手工声明重新接进界面，**这一格要一起回来**。
    // 写作助手（模式二）。它是这块网上**风险最高**的一格：会话的内部标识
    // （`chat_session:…`）、停止原因的机器码（`done` / `context_full`）、
    // 上下文回执那七个 snake_case 字段，全都在它手上过一遍。
    ["写作助手", <ChatPanel key="cp" />],
    // 稿件并排对比那一页 2026-09-12 撤了（作者：「这块就不要了」）——那几稿每稿一行，在写作助手那一格里扫。
    // 「你交代过的」那张表（ADR 0028 + 迁移 016）。它手上形状可疑的东西有两样：
    // 会话的内部标识（`chat_session:…`，那一列今天只渲染标题，探针在下面那条断言里）
    // 和模型自己写的那句时效——**那句话是模型写的，不是引擎的措辞表出来的**，
    // 所以词表那张网罩不住它，只有形状判据罩得住。
    ["你交代过的", <RulesTable key="rt" />],
    // ⚠️ **「现在生效的规矩」这一格 2026-08-14 撤了**（组件 + 两份测试文件一起删）。
    // 它手上两样东西形状可疑——每条规矩的 `seq`（裸数字，三张网都认不出）和后端写的
    // 「它管到哪儿」那句话——**撤掉它同样是覆盖面变小**，同上面「声明抽屉」那一条。
    // 规矩的留痕哪天落到日志，这一格要跟着回来。理由见 `ChatPanel.tsx` 顶上那段。
  ])("「%s」上一个研发术语都没有", async (_name, ui) => {
    renderWithApi(ui, stateRoute(STATE_WITH_EDGES));
    // 等第一批查询落地：扫一块还没渲染出内容的屏幕等于什么都没扫。
    //
    // **就绪信号量的必须是 `screenText()`，不是 `document.body.textContent`。**
    // 2026-09-06 顶栏的「工作台」二字被作者去掉之后，那一栏的正文只剩品牌名
    // 「Novel Harness」——**整块屏幕一个中文字都没有**（它本来就是「一个字都不写，
    // 图标 + 悬浮出名字」，名字全在 `aria-label` 上）。于是这条 waitFor 永远等不到，
    // 顶栏那一格红了，而它报的是「就绪等不到」，不是「有研发术语」。
    // 换成 `screenText()` 之后，就绪信号和下面那条断言扫的是**同一块文本**——
    // 一块屏幕只要有东西可扫，它就一定等得到。
    await waitFor(() => expect(screenText()).toMatch(/[一-龥]/));
    await new Promise((r) => setTimeout(r, 60));
    expect(devTerms(screenText())).toEqual([]);
  });

  it("设置弹窗的另外两栏（「模型服务」「个性化」——**默认都不在屏幕上**）", async () => {
    // 上面那条 each 渲染完就扫，扫的是默认那一栏（2026-09-10 起是「个性化」）。
    // 而这扇窗上最长的两段说明、那份公开模型表的回执、拉不到时后端那句话、
    // 以及服务地址/模型/密钥那三个框，全在别的栏——
    // **没被扫到的组件等于没有守卫**，这个仓库为这件事栽过。
    //
    // ⚠️ 默认栏 2026-09-05 从「连接服务」换成了「通用」，于是**原来被 each 顺带
    // 扫着的那一栏掉出了网**。这条断言当天跟着从「扫一栏」改成「扫两栏」——
    // 换默认栏而不补这里，覆盖面会静悄悄地少一块。
    const user = userEvent.setup();
    renderWithApi(<SettingsDrawer onClose={() => {}} />);

    await user.click(await screen.findByRole("tab", { name: "模型服务" }));
    await screen.findByLabelText("API 密钥");
    // 「模型上下文长度」2026-09-06 搬进了这一栏（「前文长度」那一栏整个取消了），
    // 所以这一次点击就把两段最长的说明、那份清单的回执、和三个框全扫到了。
    await screen.findByText("模型上下文长度");
    expect(devTerms(screenText())).toEqual([]);

    await user.click(screen.getByRole("tab", { name: "个性化" }));
    await screen.findByText("界面语言 / Interface language");
    expect(devTerms(screenText())).toEqual([]);

    // 「系统功能」那一栏 2026-09-10 之前没在网里（它的两格是 2026-09-06 / 09 才长出来的）。
    // 这一次点击把三颗开关/框的说明全扫到——「novel-agent 模式」是顶栏已经在念的名字，
    // 它得能过这张网，否则顶栏那颗开关的悬浮字早该红了。
    await user.click(screen.getByRole("tab", { name: "系统功能" }));
    await screen.findByText("是否在 novel-agent 模式下续写");
    expect(devTerms(screenText())).toEqual([]);
  });

  it.each([
    ["角色册", "roster", /青云城主府/],
    // 「检验本章」2026-09-05 收成了一颗闪电图标（名字在 `aria-label`/`data-tip` 上），
    // 屏幕上不再有那四个字——就绪信号换成这一格一定会渲的小标题。
    ["检验规则", "check", /自定义规则/],
    // 2026-08-31 之前这一格叫「待确认」，标记是 /关系冲突/（提案卡）。待确认审阅
    // 搬进了「通知」（下面单独一行），这一格（key 还是 `review`，标签改成了「事件」）
    // 现在只剩已确认的情节。
    ["事件", "review", /萧决得知血脉秘密/],
    // 2026-08-13 新长出来的一格。它手上形状可疑的东西有两样：这一章总结在库里的
    // 来源和状态（`model` / `author` / `ACTIVE` / `RETRACTED`），以及后端拒绝时那句话。
    //
    // 就绪信号是那一行小标题。**别换成光秃秃的「章节总结」**：右栏那颗页签自己就叫
    // 这个名字，它在数据回来之前就渲好了，拿它当信号等于不等（2026-09-05 那句
    // 「写第 N 章时…带得上」被作者点名删掉，就绪信号跟着换到这儿）。
    ["章节总结", "summary", /第 \d+ 章 章节总结/],
    // ⚠️ 「通知」没有列在这张表里：默认夹具里 `summary_mismatch` 那条通知用的是
    // 契约测试自己手搭的占位码 `title_code="test_notice"`（真实生产从来没有生产过
    // 这一档，见 `tests/test_frontend_contract.py` 那条注释），字面量原样漏到屏幕上
    // 会被这条守卫正确地判成「研发术语」——但那是夹具的既有性质，不是这次改动引入的。
    // 「关系冲突」提案卡不漏机器码这件事，`CanonEdit.boundary.test.tsx` 已经钉住了
    // （`rawIds`/`machineWords`/`engineWords` 三张网都扫过），这儿不重复扫一次全屏。
  ] as const)("右栏「%s」那一格上一个研发术语都没有", async (_name, tab, marker) => {
    // `CanonEdit.boundary.test.tsx` 只在**两个编辑器摊开**的时候扫右栏；
    // 八格里另外六格一次都没被扫过。
    useCoords.setState({ activeTab: tab });
    renderWithApi(<RightPanel />, stateRoute(STATE_WITH_EDGES));
    await screen.findAllByText(marker);
    await new Promise((r) => setTimeout(r, 60));
    expect(devTerms(screenText())).toEqual([]);
  });

  it("角色册展开的那张卡（状态 + 关系 + 依据）上一个研发术语都没有", async () => {
    // 「人物状态」「人物关系」「原文依据」三个 tab 2026-08-31 并进角色册之前，
    // 各自在上面那条 `it.each` 里占一行。并进来之后卡片只在**真实在角色册里的人**
    // 身上展开——上面那条「角色册」用的 `selectedNodeId`（外层 `beforeEach` 里的
    // "character:ID10"）不在 `rosterWithCounts` 里，卡片今天展不开，那一行因此只扫得到
    // 折叠的列表本身，没有一行会去扫卡片。这条换一个真实存在的 id（萧决，
    // 和 `STATE_WITH_EDGES` 说的是同一个人）。
    useCoords.setState({ activeTab: "roster", selectedNodeId: "character:ID9" });
    renderWithApi(<RightPanel />, stateRoute(STATE_WITH_EDGES));
    await screen.findAllByText(/萧决/);
    await new Promise((r) => setTimeout(r, 60));
    expect(devTerms(screenText())).toEqual([]);
  });

  it.each([
    ["全报了", "runsAllReported"],
    ["报了一部分", "runs"],
    ["一次都没报", "runsUnreported"],
  ] as const)("用量条「%s」那一档上一个研发术语都没有", async (_name, key) => {
    // **2026-08-12 新长出来的两句话**（「另有 N 次没报，实际更多」/「用量未记录」+
    // 两句 title）。它们要说的正是引擎内部那件事——供应商没回报 usage——而那个词
    // 一旦漏出来就是 `stream_options` / `tokens_in` 摆到作者脸上。
    //
    // 三档各扫一遍，三份都是**真 dump**（`tests/test_frontend_contract.py` 在三个不同
    // 的时刻抓的）：只喂一种形状的样本，这条断言扫的就是一块永远长一个样的屏幕，
    // 而屏幕上那两句新话一次都不会被渲染到。
    useCoords.setState({ page: "log" });
    renderWithApi(<ActivityLog />, [{ match: /\/runs(\?|$)/, body: fixtures[key] }]);
    await screen.findByText(/模型调用 \d+ 次/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("接口拒绝时，角色册抽屉的错误框里也没有研发术语", async () => {
    // `errorShortAlias.message` 是真后端写的，里头躺着 `usable_for_rules` 和一句
    // 「ADR 0004」——**写给维护者的诊断**。这一格今天靠前端换一句自己的话挡住，
    // 而挡没挡住在这条断言之前没有任何东西验过。
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={() => {}} />, [
      { method: "POST", match: /\/aliases$/, status: 422, body: fixtures.errorShortAlias },
    ]);
    await user.click(screen.getByRole("button", { name: "加别名" }));
    await user.type(screen.getByPlaceholderText("输入已有名称"), "测试角色");
    await user.type(screen.getByPlaceholderText("输入新的别名"), "名");
    await user.click(screen.getByRole("checkbox")); // 绕过前端提示，逼服务端说话
    await user.click(screen.getByRole("button", { name: "加" }));

    await screen.findByText(/无法添加这个别名/);
    expect(devTerms(screenText())).toEqual([]);
    // 后端那句话确实脏 —— 探针过期了这条会红，那时说明后端改干净了，可以删掉它。
    expect(machineWords(fixtures.errorShortAlias.message)).toContain("usable_for_rules");
  });

  it("写作助手：会话列表摊开的时候（那儿每一行都挂着一个内部标识）", async () => {
    const user = userEvent.setup();
    // 断在半路那一档也一起扫：它的徽标只有 `pending_lookups` 非零时才出现，
    // 而真 dump 里那个数是 0 —— 正常数据下这条分支永远不亮，正是它躲过守卫的方式。
    renderWithApi(<ChatPanel />, [
      { match: /\/chats$/, body: [{ ...fixtures.chats[0], pending_lookups: 3, running: true }] },
    ]);
    await user.click(await screen.findByRole("button", { name: "对话列表" }));
    await screen.findByText(/上一轮中断/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：一轮跑完之后那一片（后端那句话 + 这一轮裁掉了什么）", async () => {
    const user = userEvent.setup();
    // 上下文回执那七个字段全非零 —— 真 dump 里它们全是 0，于是**那几句话一次都没被扫过**。
    const noisy = {
      ...fixtures.chatTurn,
      context: {
        off_chapter: 2,
        stale_lookups: 1,
        trimmed_results: 3,
        dropped_lookups: 1,
        dropped_reasoning: 1,
        lost_lookups: 1,
        full: true,
      },
    };
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: noisy }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(ROUND_DONE);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：那几稿摆出来的时候（每稿一行：第几稿 · 字数 · 在哪儿 + 自述）", async () => {
    // **这一片是 2026-08-12 新长出来的屏幕**（ADR 0022），而它同时端着两样形状可疑的东西：
    // 候选的内部标识（`draft:01J…`，第三张网认的就是它）和「第几稿」这个数。
    // 2026-09-12 起稿子的字不在这儿（ADR 0048：流进左边的编辑器），每稿只有一行——
    // 三种「在哪儿」都要扫到：已写入 / 在编辑器里 / 还在桌上（一颗「放入编辑器」）。
    // 真 dump 那一轮只写了一稿且没落盘，「一稿进了书」那一档正常数据下永远不亮。
    const user = userEvent.setup();
    const one = fixtures.chatTurn.drafts[0];
    const turn = {
      ...fixtures.chatTurn,
      drafts: [
        { ...one, id: "draft:ID43", ordinal: 1, landed: false },
        { ...one, id: "draft:ID44", ordinal: 2, landed: true },
        { ...one, id: "draft:ID45", ordinal: 3, landed: false, note: "" },
      ],
    };
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turn }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "写三个版本");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("第 3 稿");
    expect(screen.getAllByRole("button", { name: "放入编辑器" })).toHaveLength(2);
    expect(screen.getByText(/已写入第 \d+ 章/)).toBeInTheDocument();
    expect(devTerms(screenText())).toEqual([]);
  });

  // ── 一轮跑到一半那几块屏幕（ADR 0024 第二刀）─────────────────────────────
  //
  // **这三块是 2026-08-12 下午新长出来的**（进度行 / 逐字区 / 问题卡），而它们端着的
  // 东西形状最可疑：事件上的 `kind` / `tool` / `reason` 全是 snake_case 机器码，
  // 停下来问那一档还多一个 `asked_author`。没被扫到的组件等于没有守卫，这个仓库栽过。
  //
  // 喂的是**真 dump 的那一串帧**（`fixtures.chatTurnEvents`），不是手写的。

  it("写作助手：一轮跑到一半（进度行 + 它说的那段话）", async () => {
    const user = userEvent.setup();
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [...TURN_MIDDLE, held],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "查一下");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const strip = await screen.findByRole("status");
    await waitFor(() => expect(strip.textContent).toContain("正在"));
    expect(devTerms(screenText())).toEqual([]);
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });

  it("写作助手：正在起草的那一行（字在左边的编辑器里，这儿只有一行进度）", async () => {
    // **两档都要扫**：一稿刚开、还没有一个字（真 dump 那一轮跑得太快，这一档从不出现），
    // 和字已经在长。两档屏幕上都只有那一行引擎写的话（稿子的字 2026-09-12 起流进左边的
    // 编辑器，ADR 0048），最容易在某次改动里变成一句英文或一个机器码。
    const user = userEvent.setup();
    const opened = JSON.parse(
      TURN_MIDDLE.find((f) => f.includes('"kind":"draft_started"'))!.split("\ndata: ")[1],
    );
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    // 最后一帧永远卡住：这一行只活在这一轮跑着的时候，流一断它就换成「本轮未完成」。
    const forever = new Promise<string>(() => {});
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [sseFrames([{ event: "turn", data: opened }])[0], held, forever],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "写一稿");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(/正在起草第 \d+ 章…/);
    expect(devTerms(screenText())).toEqual([]);

    release(
      sseFrames([
        { event: "turn", data: { ...opened, kind: "draft_delta", text: "风雪落在肩上。", said_to_author: "" } },
      ])[0],
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText("风雪落在肩上。")).toBeNull();
    expect(screen.getByText(/正在起草第 \d+ 章…/)).toBeInTheDocument();
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：它问了一句、作者还没答（**正常数据下这一档永远不亮**）", async () => {
    // 第十一种停法（`asked_author`）。真 dump 那一轮是 `done`，所以这块卡片在
    // 契约夹具里一次都不会被渲染 —— 正是它躲过守卫的方式。
    const user = userEvent.setup();
    const asked = {
      ...fixtures.chatTurn,
      reason: "asked_author",
      message: "写作助手提出了一个问题，等待回答。",
      asked: {
        question: "这一场你想让萧决知道那件事吗？",
        options: ["让他知道", "先瞒着"],
      },
    };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(asked) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "这一场怎么写");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByRole("group", { name: "写作助手在等待回答" });
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：流断在半路那一档（屏幕上只剩那句「说不清为什么」）", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: [TURN_MIDDLE[0]] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(/本轮未完成/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：作者按了停那一档（后端那句 + 半截的那一稿）", async () => {
    // 「按了停」在回执上是 `author_stopped`，而那一稿带着 `stopped_reason`
    // —— 两句都是后端写的中文，两句都得扫。
    const user = userEvent.setup();
    const one = fixtures.chatTurn.drafts[0];
    const stopped = {
      ...fixtures.chatTurn,
      reason: "author_stopped",
      message: "已停止。已查到的内容保留。",
      // 它没问出那一句（`reply` 空）：回执上那句才会画出来给这儿扫（`chat.ts::receiptSays`）。
      reply: "",
      drafts: [{ ...one, landed: false, stopped_reason: "作者中途按了停，这一稿没写完。" }],
    };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(stopped) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "写一稿");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(stopped.message);
    await screen.findByText(/这一稿没写完/);
    expect(devTerms(screenText())).toEqual([]);
  });

  // ── 「现在生效的规矩」那一块 2026-08-14 整个撤了（组件 + 两份测试一起删）───────
  //
  // 这儿原来有六条：四条扫 `ChatRules` 自己的四种长相（两种空 / 读不出来 / 点 × 之后
  // 那句确认），两条扫写作助手顶上那颗按钮。撤掉它们**就是覆盖面变小**，不是覆盖面变干净
  // ——同上面「声明抽屉」那一格的写法。
  //
  // 撤的理由不在这一层：规矩的**有效期该由模型按情境判**（「男主在这片沙地时」），
  // 而引擎今天按章号算，那块面板把这个粗糙度摆到了作者面前
  // （`ChatPanel.tsx` 顶上那段写着全文）。
  //
  // **规矩的留痕哪天落到日志，这几条要跟着回来**——它扫的东西一样也没消失：
  // 规矩原文是模型写的、`seq` 是裸数字（三张网都认不出）、后端的拒绝只有码没有话。

  it("写作助手：顶上不再有「这一章的规矩」那颗按钮", async () => {
    // 探针式的一条：它防的是「删了组件、忘了删按钮」——那样按钮会渲染成一颗点开
    // 什么都没有的死键，而 `tsc` 看不见这种坏法（按钮上没有类型）。
    renderWithApi(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.queryByRole("button", { name: /规矩/ })).toBeNull();
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：后端那句话里的 `**` 是重音，不是两颗星号", async () => {
    // `stop_wording(CONTEXT_FULL)` 里就有这么一对。不渲染 = 作者看见两颗星号；
    // 改那句话 = 第二份措辞源。所以这一层只负责把它画出来。
    const user = userEvent.setup();
    const full = {
      ...fixtures.chatTurn,
      reason: "context_full",
      message: "这段对话说得太长，装不下了。开一段新的对话——**当前对话记录未删减**。",
    };
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: full }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("当前对话记录未删减");
    expect(document.body.textContent).not.toContain("**");
    // 停止原因是机器码，它一个字都不该跟着那句话上屏。
    expect(devTerms(screenText())).toEqual([]);
  });

  // ── 系统通知（国际化第四批 Phase B）────────────────────────────────────────
  //
  // 这一格以前从没被这张网扫过（补上之前是个真的洞：`SystemNotifications.tsx`
  // 不在 DevTerms 的扫描面里）。**它现在渲染的是码 + 参数，不是后端拼好的句子**
  // ——`messageForCode()` 拿到码去 `backendMessages.ts` 整句渲染，这条测试要
  // 验的是**这条新路径的渲染结果**真的会被这五张网扫到，不是从前那种
  // "后端已经把话拼好了，前端原样显示"的旧形状。
  //
  // 数据不是真 dump 的 fixture（`notifications` 那份契约夹具里的
  // `title_code: "test_notice"` 是占位符，不代表任何真的注册码）——这儿手搭
  // 一组覆盖高风险码的通知：`validation_blocked_title` 的 `rule_title`/
  // `issue_message` 是 `checks/` 的原文，`clash_title` 的 `conflict` 是封闭
  // 枚举，两者都是"这个参数会不会把机器词带上屏"的第一嫌疑对象。
  it("系统通知：码 + 参数渲染出来的整句上一个研发术语都没有", async () => {
    const notices = [
      {
        id: "notif:ID1",
        project_id: "project:ID1",
        kind: "validation_blocked",
        status: "OPEN",
        subject_type: "chapter",
        subject_id: "chapter:ID1",
        chapter_number: 3,
        title_code: "validation_blocked_title",
        title_params: {
          paragraph: 2,
          rule_title: "设定提前出现",
          rest: 1,
          issue_message: "血脉秘密在这一段被提前带出",
        },
        summary_sha256: null,
        source_sha256: null,
        jump: { para_index: 1, quote_text: "血脉秘密", occurrence_k: 0 },
        actions: [],
        created_at: "2026-08-27T00:00:00.000Z",
      },
      {
        id: "notif:ID2",
        project_id: "project:ID1",
        kind: "text_advisory",
        status: "OPEN",
        subject_type: "chapter",
        subject_id: "chapter:ID2",
        chapter_number: 5,
        title_code: "clash_title",
        title_params: { sentence: 3, chapter: 64, conflict: "setting", rest: 0 },
        summary_sha256: null,
        source_sha256: null,
        jump: { para_index: 2, quote_text: "玄铁令", occurrence_k: 0 },
        actions: [],
        created_at: "2026-08-27T00:00:00.000Z",
      },
    ];
    renderWithApi(<SystemNotifications />, [{ match: /\/notifications$/, body: notices }]);
    await screen.findByText(/设定提前出现/);
    expect(devTerms(screenText())).toEqual([]);
  });

  // ── 活动记录：真 dump 里没有的那几种码 + 参数组合（国际化第四批·笔二）───────
  //
  // `ActivityLog.test.tsx` 已经从头到尾扫过真 dump 里躺着的那几种形状（抽取
  // 成功/失败、一次模型调用、一条「抽取结果审阅」的确认）。**但真 dump 只留下了
  // 它抓那天恰好发生的组合**——`capability=agent/writer/summarizer/advisory`、
  // `kind` 除 `proposal_review`/`chapter_draft` 外的另外 13 档、`edge_type` 九档、
  // `run_error` 除 `provider_failure` 外的另外八档，**一次都没有被真的渲染过**。
  // 这些码的字符集在 `backendMessages.test.ts` 里逐条查过（那是「表里这一行本身
  // 干不干净」），但「组装进一整句、摆进 DOM」这一步没人验过——同 `SystemNotifications`
  // 当年的洞：`title_code`/`title_params` 拼起来的那句话，只在这儿才第一次真的渲染。
  // 手搭四条，各挑一样此前没被夹具覆盖过的：写作助手的模型调用、认知边类型的更正
  // （历史行形状）、自动升边的确认、以及一档非 `provider_failure` 的抽取失败原因。
  it("活动记录：夹具里没出现过的码 + 参数组合，渲染出来也没有研发术语", async () => {
    const page = {
      entries: [
        {
          id: "call:probe1",
          source: "model_call",
          ts: "2026-08-27T00:00:00.000Z",
          actor: "system",
          status: "succeeded",
          title_code: "call_entry_title",
          title_params: { capability: "agent" },
          subtitle_code: "call_subtitle",
          subtitle_params: { model: "deepseek-v4", tokens_in: 800, tokens_out: 200, ms: 700 },
          chapter_number: 3,
          jump: null,
        },
        {
          id: "decision:probe2",
          source: "decision",
          ts: "2026-08-27T00:00:01.000Z",
          actor: "author",
          status: "succeeded",
          title_code: "decision_entry_title",
          title_params: { actor: "author", kind: "knowledge_edit" },
          subtitle_code: "decision_subtitle_knowledge_edit",
          subtitle_params: { subject: "萧决", secret: "血脉秘密", before: "KNOWS", after: "LOCATED_AT" },
          chapter_number: 3,
          jump: { target: "chapter", label_code: "jump_go_to_chapter", label_params: { chapter: 3 }, chapter_number: 3, event_id: null, proposal_id: null, edge_id: null, endpoints: [] },
        },
        {
          id: "decision:probe3",
          source: "decision",
          ts: "2026-08-27T00:00:02.000Z",
          actor: "system",
          status: "succeeded",
          title_code: "decision_entry_title",
          title_params: { actor: "system", kind: "canon_edge_edit" },
          subtitle_code: "decision_subtitle_edge_declare",
          subtitle_params: { subject: "萧决", edge_type: "LOCATED_AT", target: "北荒" },
          chapter_number: 4,
          jump: { target: "canon_edge", label_code: "jump_edit_auto_edge", label_params: { chapter: 4 }, chapter_number: 4, event_id: null, proposal_id: null, edge_id: "edge:probe", endpoints: ["/api/projects/x/canon/edges/edge:probe"] },
        },
        {
          id: "extraction_run:probe4",
          source: "extraction",
          ts: "2026-08-27T00:00:03.000Z",
          actor: "system",
          status: "failed",
          title_code: "run_entry_title",
          title_params: { chapter: 5 },
          subtitle_code: "run_error",
          subtitle_params: { code: "provider_auth" },
          chapter_number: 5,
          jump: { target: "extraction_retry", label_code: "jump_retry_chapter", label_params: { chapter: 5 }, chapter_number: 5, event_id: null, proposal_id: null, edge_id: null, endpoints: ["/api/projects/x/chapters/5/extract"] },
        },
      ],
      next_cursor: null,
      actors: [
        { actor: "author", count: 1 },
        { actor: "system", count: 3 },
      ],
    };
    useCoords.setState({ page: "log" });
    renderWithApi(<ActivityLog />, [{ match: /\/activity(\?|$)/, body: page }]);

    await screen.findByText(/写作助手/);
    expect(devTerms(screenText())).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. 兜底那一支 —— 正常数据下永远不亮，所以永远没被扫过
// ══════════════════════════════════════════════════════════════════════════

/** 一条**引擎写得出、但契约夹具里没有**的边：伏笔埋在某一章。
 *
 *  `PLANTED_IN`/`RESOLVED_IN` 曾经不在三个组件里那三份「关系 → 中文」的拷贝里
 *  （2026-08-11 之前各自硬编码 7 行，漏了这两类），兜底是 `?? e.type`。
 *  **这一条就是那两行的形态。** */
const PLANTED = {
  ...fixtures.characterState,
  edges: [{ ...fixtures.states[0].edges[0], type: "PLANTED_IN" }],
};

describe("兜底：认不出的东西说人话，不是原样回吐", () => {
  it("每一类关系都说得出中文 —— 一个大写枚举名都不许漏出去", () => {
    // **判据是 `EDGE_LABEL` 本身**，不是在这里抄一份成员清单——但 `EDGE_LABEL`
    // 是 `Record<string, {zh,en}>`，不像原来的 `EDGE_ZH`（`Record<EdgeType, string>`）
    // 那样靠 TS 类型强制全列，还留着 `KNOWS`/`BELIEVES` 两个 ADR 0039 退役的历史键。
    // Python 那边的枚举和这份表键集合对不对得上，由
    // `tests/test_wording_guard.py::test_the_edge_label_wording_table_covers_every_edge_type` 钉。
    //
    // ⚠️ **2026-09-06 这一条从「渲染一遍底栏」改成「直接问措辞函数」**（底栏那个
    // 组件当天删了）。**这不是把守卫改弱了，是改宽了**：
    // · 原来它只覆盖一个宿主（`BottomBar`），而 `edgeLabelText` 今天有三个调用方
    //   （原文依据、人物关系、后端消息模板），换成直接钉函数，三个一起罩住；
    // · 它本来要防的那件事——各组件自己抄一份 7 行的表、兜底写 `?? e.type`——
    //   正是靠「全前端只剩这一份表」修掉的，所以判据本来就该落在那份表上。
    // 组件真的用了这份表（而不是又抄了一份），由下面「角色卡里的依据段」那条钉。
    const HISTORICAL_ONLY = new Set(["KNOWS", "BELIEVES"]);
    const liveEdgeTypes = Object.keys(EDGE_LABEL).filter(
      (k) => !HISTORICAL_ONLY.has(k),
    ) as EdgeType[];
    expect(liveEdgeTypes.length).toBeGreaterThan(0); // 表空了的话下面这个循环会假绿
    for (const type of liveEdgeTypes) {
      const said = edgeLabelText(type, "zh");
      expect(said).toBe(EDGE_LABEL[type].zh);
      // 兜底一旦退回 `?? type`，说出来的就是这个大写枚举名本身。
      expect(said).not.toContain(type);
      expect(engineWords(said)).toEqual([]);
    }
  });

  // 上面那两条原来各有一个底栏版本（`PLANTED_IN` 不原样上屏 / 查不到的对象说「—」）。
  // 底栏 2026-09-06 删了，而这一条在**仅剩的那个宿主**上验的是同样两件事——
  // 不是少了一条守卫，是那两条本来就在这儿重复着。
  it("角色卡里的依据段：认不出的边说人话，查不到的对象说「—」", async () => {
    // `dst` 指向一个**角色册里还没有**的节点：角色册和这份快照是两条独立缓存，
    // 后台整理刚建出来的节点会在前者里缺席一拍，而没有任何路径保证那一拍不会被
    // 作者看见。那一拍上的兜底必须是「—」，不是 `?? id`（那会摆出一串内部编号）。
    useCoords.setState({ activeTab: "roster", selectedNodeId: "character:ID9" });
    renderWithApi(<RightPanel />, [
      { match: /\/characters\/.*\/state/, body: { ...PLANTED, edges: [{ ...PLANTED.edges[0], dst: "location:ID99" }] } },
    ]);
    await screen.findByText(/埋在/);
    expect(devTerms(screenText())).toEqual([]);
    expect(document.body.textContent).toContain("—");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. 网本身 —— 它现在罩得住哪些，以及它**明确罩不住**哪些
// ══════════════════════════════════════════════════════════════════════════

describe("判据：形状那一半", () => {
  it("SCREAMING_SNAKE 是形状，不是词表 —— 明天新长出来的那个也一起收", () => {
    // 这七个是 `DiscardOutcome` / `LocateOutcome` 的真值。2026-08-11 那一版
    // `ENGINE_ENUM` 是一张 30 个词的手抄表，**这七个一个都不在里面**，
    // 而它们是「这条抽取结果为什么被丢掉」的原因码，正对着作者的屏幕。
    for (const value of [
      "SUPERSEDED_IN_ANALYSIS",
      "UNKNOWN_SURFACE",
      "AMBIGUOUS_SURFACE",
      "WRONG_LABEL",
      "BELOW_THRESHOLD",
      "NOT_FOUND",
      "DOES_NOT_KNOW", // 早已删掉的边类型，旧库里的历史行还带着它
    ]) {
      expect(engineWords(`这一条被丢掉了：${value}`)).toContain(value);
    }
  });

  it("没有下划线的裸大写值只能列，而那张清单由 pytest 拿枚举本身钉住", () => {
    for (const value of ["ACCEPTED", "EDITED", "EXACT", "FUZZY", "AMBIGUOUS", "NONE"]) {
      expect(engineWords(`当前：${value}`)).toContain(value);
    }
  });

  it("**它看不见小写的裸枚举值** —— 这条断言在描述现状，不是在批准它", () => {
    // HTTP 层有意把一批枚举小写化（`ActivityStatus = "failed"`、`actor = "author"`）。
    // 一个小写英文单词和界面上合法的英文（`token` / `ms` / `deepseek-v4`）形状上
    // 分不开，收它就假红，而假红会让下一个人把守卫关掉。
    // **所以这一类只能在源头堵**：那些值一律不许直接上屏。
    for (const value of ["author", "system", "failed", "extraction", "accept", "extractor"]) {
      expect(devTerms(`当前是 ${value} 这一档`)).toEqual([]);
    }
  });
});

describe("判据：不许假红", () => {
  it("作者界面上合法的英文一个都不许被咬", () => {
    // 假红比漏报更危险：它会让下一个人给守卫加一条豁免，然后豁免被拓宽。
    const clean = [
      "模型调用 · 抽取 · deepseek-v4-flash · 入 1200 / 出 400 token · 900 ms",
      "AI 设置 · 服务地址 · 模型名称 · API 密钥",
      "这本书导入的是 TXT 文件，用 WPS 打开也行",
      "2026/08/11 01:28:40 · 用时 1.0 秒",
      "已确认的情节（谁在场、谁知道了）· 可信程度 60%",
      "改「萧决 对 血脉秘密」· 现在是「知道」· 第 3 章起",
      "这台电脑上没有记价格：钥匙是你自己的。",
    ].join("\n");
    expect(devTerms(clean)).toEqual([]);
  });

  it("**自守卫**：把上面那段掺一个脏词进去，它立刻红", () => {
    // 一张什么都咬不到却一直绿着的网，是这个仓库吃过三次亏的那种失败形态。
    expect(devTerms("模型调用 · deepseek-v4 · provider_failure")).toEqual(["provider_failure"]);
    expect(devTerms("2026/08/11 01:28:40 · 当前 n:ID22")).toEqual(["n:ID22"]);
  });
});
