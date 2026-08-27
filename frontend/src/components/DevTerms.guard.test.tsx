import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi, sseFrames, turnStream } from "../test/harness";
import { devTerms, engineWords, machineWords, rawIds, screenText } from "../test/screenGuard";
import { EDGE_ZH, type EdgeType } from "../api/types";
import { useCoords } from "../store";
import { ActivityLog } from "./ActivityLog";
import { BottomBar } from "./BottomBar";
import { RightPanel } from "./RightPanel";
import { TopBar } from "./TopBar";
import { LeftRail } from "./LeftRail";
import { RosterDrawer } from "./RosterDrawer";
import { SettingsDrawer } from "./SettingsDrawer";
import { HistoryDrawer } from "./HistoryDrawer";
import { BookShelf } from "./BookShelf";
import { ChatPanel } from "./ChatPanel";
import { DraftCompare } from "./DraftCompare";
import { RulesTable } from "./RulesTable";
import { SystemNotifications } from "./SystemNotifications";

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
});

/** 一份**带边**的人物快照。`characterState` 那份 dump 的 `edges` 是空的，
 *  于是底栏和原文依据两块屏幕在守卫眼里永远是「暂无可显示的变化」。
 *  边取自 `states` 那份 dump（同一个真 app 的另一条端点），**不是手写的**。 */
const STATE_WITH_EDGES = { ...fixtures.characterState, edges: fixtures.states[0].edges };

const stateRoute = (body: unknown) => [{ match: /\/characters\/.*\/state/, body }];

// ⚠️ **「场景条」这一格 2026-08-14 撤了**（组件连同场景块、两条路由和 R4 一起删了，
// [ADR 0027](docs/adr/0027-scene-blocks-cut.md)）。它是这张网上少数几个**从来没被真夹具
// 喂活过**的格子——`fixtures.scenes` 那份真 dump 是 `[]`，因为真书里没人手写 `## 场景 N`，
// 而那正是砍掉它的理由。底栏那一格今天扫的是仅剩的「人物与设定变化」一列。

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
    ["底栏时间线", <BottomBar key="b" />],
    ["书架页", <BookShelf key="bs" onOpenChapter={() => {}} />],
    ["花名册抽屉", <RosterDrawer key="rd" pid="project:ID1" onClose={() => {}} />],
    // 设置这扇窗 2026-08-14 变成左右分栏，**这一条只扫得到默认那一栏**（「连接服务」）。
    // 另一栏由紧跟在这个 each 后面那条单独的断言扫。
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
    // 并排比几稿那一页（ADR 0022）。它是**开在另一个标签页里**的一整页屏幕，
    // 而它手上全是形状可疑的东西：候选的内部标识（`draft:01J…`）、书的标识、
    // 那一稿的自述和定长预览。没被扫到的组件等于没有守卫，这一页尤其——
    // 作者在这儿读的是三章正文，任何一个漏出来的码都摆在正文旁边。
    ["并排比几稿", <DraftCompare key="dc" chapter={2} />],
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
    await waitFor(() => expect(document.body.textContent).toMatch(/[一-龥]/));
    await new Promise((r) => setTimeout(r, 60));
    expect(devTerms(screenText())).toEqual([]);
  });

  it("章目录那颗「⋯」摊开的样子（**默认不在屏幕上**）", async () => {
    // 同下面那条：菜单收着的时候上面那条 each 扫不到它，而它端着后端删章那句拒绝——
    // 那句话里带着五个计数，是这一栏上唯一一处「引擎内部有多少东西」会露头的地方。
    const user = userEvent.setup();
    renderWithApi(<BookShelf onOpenChapter={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "第一章 血脉的更多操作" }));
    await user.click(await screen.findByRole("button", { name: "删除本章" }));
    await screen.findByText(/删除第 1 章？/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("设置弹窗的另一栏（「前文长度」——**默认不在屏幕上**）", async () => {
    // 上面那条 each 渲染完就扫，扫的是默认那一栏。而这扇窗上最长的两段说明、
    // 那份公开模型表的回执、以及拉不到时后端那句话，全在另一栏——
    // **没被扫到的组件等于没有守卫**，这个仓库为这件事栽过。
    const user = userEvent.setup();
    renderWithApi(<SettingsDrawer onClose={() => {}} />);
    await user.click(await screen.findByRole("tab", { name: "前文长度" }));
    await screen.findByText("模型一次能读多少");
    expect(devTerms(screenText())).toEqual([]);
  });

  it.each([
    ["花名册", "roster", /青云城主府/],
    ["人物状态", "state", /萧决/],
    ["人物关系", "graph", /人物关系/],
    ["原文依据", "evidence", /原文依据/],
    ["写作提醒", "constraints", /尚未登场/],
    ["检查", "check", /检查本章/],
    ["待确认", "review", /关系冲突/],
    // 2026-08-13 新长出来的一格。它手上形状可疑的东西有两样：这一章总结在库里的
    // 来源和状态（`model` / `author` / `ACTIVE` / `RETRACTED`），以及后端拒绝时那句话。
    ["章节总结", "summary", /带得上/],
  ] as const)("右栏「%s」那一格上一个研发术语都没有", async (_name, tab, marker) => {
    // `CanonEdit.boundary.test.tsx` 只在**两个编辑器摊开**的时候扫右栏；
    // 八格里另外六格一次都没被扫过。
    useCoords.setState({ activeTab: tab });
    renderWithApi(<RightPanel />, stateRoute(STATE_WITH_EDGES));
    await screen.findAllByText(marker);
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

  it("接口拒绝时，花名册抽屉的错误框里也没有研发术语", async () => {
    // `errorShortAlias.message` 是真后端写的，里头躺着 `usable_for_rules` 和一句
    // 「ADR 0004」——**写给维护者的诊断**。这一格今天靠前端换一句自己的话挡住，
    // 而挡没挡住在这条断言之前没有任何东西验过。
    const user = userEvent.setup();
    renderWithApi(<RosterDrawer pid="project:ID1" onClose={() => {}} />, [
      { method: "POST", match: /\/aliases$/, status: 422, body: fixtures.errorShortAlias },
    ]);
    await user.click(screen.getByRole("button", { name: "加称呼" }));
    await user.type(screen.getByPlaceholderText("输入已有名称"), "测试角色");
    await user.type(screen.getByPlaceholderText("输入新的称呼"), "名");
    await user.click(screen.getByRole("checkbox")); // 绕过前端提示，逼服务端说话
    await user.click(screen.getByRole("button", { name: "加" }));

    await screen.findByText(/无法添加这个称呼/);
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
    await screen.findByText(/上次断在半路/);
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
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(fixtures.chatTurn.message);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：那几稿摆出来的时候（自述 + 预览 + 摊开之后的全文）", async () => {
    // **这一片是 2026-08-12 新长出来的屏幕**（ADR 0022），而它一次同时端着三样
    // 形状可疑的东西：候选的内部标识（`draft:01J…`，第三张网认的就是它）、
    // 「第几稿」这个数、以及一整章正文。真 dump 那一轮只写了一稿且没落盘——
    // **「一稿进了书」那一档正常数据下永远不亮**，正是它躲过守卫的方式。
    const user = userEvent.setup();
    const one = fixtures.drafts.drafts[0];
    const turn = {
      ...fixtures.chatTurn,
      drafts: [
        { ...one, id: "draft:ID43", ordinal: 1 },
        { ...one, id: "draft:ID44", ordinal: 2, landed: true },
        { ...one, id: "draft:ID45", ordinal: 3, note: "" },
      ],
    };
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turn }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "写三个版本");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("第 3 稿");
    // 摊开一版：那一条路由的返回里除了正文还有 `id` / `created_at` 这些字段，
    // 而「摊开」是作者最常做的那个动作。
    await user.click(screen.getByRole("button", { name: "展开第 3 稿" }));
    // 两版摊开着：进了书的那一版（默认摊开）+ 刚点开的这一版。
    await waitFor(() => expect(screen.getAllByText(fixtures.draftDetail.text)).toHaveLength(2));
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
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "查一下");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const strip = await screen.findByRole("status");
    await waitFor(() => expect(strip.textContent).toContain("正在"));
    expect(devTerms(screenText())).toEqual([]);
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("写作助手：正在逐字长出来的那一稿（含「还没落下第一个字」那一档）", async () => {
    // **两档都要扫**：一格刚开、还没有一个字（真 dump 那一轮跑得太快，这一档从不出现），
    // 和字已经在长。前者屏幕上只有一句引擎写的话，最容易在某次改动里变成一句英文。
    const user = userEvent.setup();
    const opened = JSON.parse(
      TURN_MIDDLE.find((f) => f.includes('"kind":"draft_started"'))!.split("\ndata: ")[1],
    );
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [sseFrames([{ event: "turn", data: opened }])[0], held],
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "写一稿");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("还没落下第一个字。");
    expect(devTerms(screenText())).toEqual([]);

    release(
      sseFrames([
        { event: "turn", data: { ...opened, kind: "draft_delta", text: "风雪落在肩上。", said_to_author: "" } },
      ])[0],
    );
    await screen.findByText("风雪落在肩上。");
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：它问了一句、作者还没答（**正常数据下这一档永远不亮**）", async () => {
    // 第十一种停法（`asked_author`）。真 dump 那一轮是 `done`，所以这块卡片在
    // 契约夹具里一次都不会被渲染 —— 正是它躲过守卫的方式。
    const user = userEvent.setup();
    const asked = {
      ...fixtures.chatTurn,
      reason: "asked_author",
      message: "它有件事拿不准，问了你一句，正等着你答。",
      asked: {
        question: "这一场你想让萧决知道那件事吗？",
        options: ["让他知道", "先瞒着"],
      },
    };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(asked) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "这一场怎么写");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByRole("group", { name: "它在等你回一句" });
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：流断在半路那一档（屏幕上只剩那句「说不清为什么」）", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: [TURN_MIDDLE[0]] },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(/这一轮没跑成/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("写作助手：作者按了停那一档（后端那句 + 半截的那一稿）", async () => {
    // 「按了停」在回执上是 `author_stopped`，而那一稿带着 `stopped_reason`
    // —— 两句都是后端写的中文，两句都得扫。
    const user = userEvent.setup();
    const one = fixtures.drafts.drafts[0];
    const stopped = {
      ...fixtures.chatTurn,
      reason: "author_stopped",
      message: "按你的意思停下了。已经查到的东西留着。",
      drafts: [{ ...one, stopped_reason: "作者中途按了停，这一稿没写完。" }],
    };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(stopped) },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "写一稿");
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
      message: "这段对话说得太长，装不下了。开一段新的对话——**你说过的话一句都没被删掉**。",
    };
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: full }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(screen.getByRole("textbox", { name: "跟写作助手说" }), "问一句");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("你说过的话一句都没被删掉");
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
});

// ══════════════════════════════════════════════════════════════════════════
// 2. 兜底那一支 —— 正常数据下永远不亮，所以永远没被扫过
// ══════════════════════════════════════════════════════════════════════════

/** 一条**引擎写得出、但契约夹具里没有**的边：伏笔埋在某一章。
 *
 *  `EdgeType` 有 9 个成员，而三个组件里那三份「关系 → 中文」的拷贝各只有 7 行，
 *  兜底是 `?? e.type`。**这一条就是那两行的形态。** */
const PLANTED = {
  ...fixtures.characterState,
  edges: [{ ...fixtures.states[0].edges[0], type: "PLANTED_IN" }],
};

/** 同一条边，但 `dst` 指向一个**花名册里还没有**的节点。
 *
 *  花名册和这份快照是两条独立缓存：后台整理刚建出来的节点会在前者里缺席一拍，
 *  而没有任何路径保证那一拍不会被作者看见。 */
const UNKNOWN_DST = {
  ...fixtures.characterState,
  edges: [{ ...fixtures.states[0].edges[0], dst: "location:ID99" }],
};

describe("兜底：认不出的东西说人话，不是原样回吐", () => {
  it("底栏：9 类关系每一类都说得出中文", async () => {
    // **判据是 `EDGE_ZH` 本身**（`Record<EdgeType, string>`，类型上强制全列），
    // 不是在这里抄一份 9 个成员的清单。Python 那边的枚举和这份表对不对得上，
    // 由 `tests/test_wording_guard.py` 钉。
    for (const type of Object.keys(EDGE_ZH) as EdgeType[]) {
      // 无向边（`RELATED_TO`）底栏按设计不画区间，它那一行不会出现。
      if (type === "RELATED_TO") continue;
      const { unmount } = renderWithApi(
        <BottomBar />,
        stateRoute({ ...fixtures.characterState, edges: [{ ...fixtures.states[0].edges[0], type }] }),
      );
      const label = await waitFor(() => {
        const el = document.querySelector(".tl-label");
        expect(el).not.toBeNull();
        return el as HTMLElement;
      });
      expect(label.textContent).toContain(EDGE_ZH[type]);
      expect(engineWords(screenText())).toEqual([]);
      unmount();
    }
  });

  it("底栏：`PLANTED_IN` 不再原样上屏", async () => {
    renderWithApi(<BottomBar />, stateRoute(PLANTED));
    await screen.findByText(/埋在/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("底栏：花名册里查不到的对象说「—」，不摆一串内部编号", async () => {
    renderWithApi(<BottomBar />, stateRoute(UNKNOWN_DST));
    await waitFor(() => expect(document.body.textContent).toContain("的变化"));
    await new Promise((r) => setTimeout(r, 60));
    expect(rawIds(screenText())).toEqual([]);
    expect(document.body.textContent).toContain("—");
  });

  it("原文依据：同样两条", async () => {
    useCoords.setState({ activeTab: "evidence" });
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
