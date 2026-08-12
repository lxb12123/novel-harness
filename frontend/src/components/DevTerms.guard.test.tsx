import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, engineWords, machineWords, rawIds, screenText } from "../test/screenGuard";
import { EDGE_ZH, type EdgeType } from "../api/types";
import { useCoords } from "../store";
import { BottomBar } from "./BottomBar";
import { RightPanel } from "./RightPanel";
import { TopBar } from "./TopBar";
import { LeftRail } from "./LeftRail";
import { RosterDrawer } from "./RosterDrawer";
import { SettingsDrawer } from "./SettingsDrawer";
import { HistoryDrawer } from "./HistoryDrawer";
import { DeclareDrawer } from "./DeclareDrawer";
import { ChapterPrepPage } from "./ChapterPrepPage";
import { SceneBar } from "./SceneBar";
import { BookShelf } from "./BookShelf";

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
    selection: "萧决在青云城主府",
    selectedNodeId: "character:ID10",
    highlight: null,
    focusCell: null,
    focusEventId: null,
  });
});

/** 一份**带边**的人物快照。`characterState` 那份 dump 的 `edges` 是空的，
 *  于是底栏和原文依据两块屏幕在守卫眼里永远是「暂无可显示的变化」。
 *  边取自 `states` 那份 dump（同一个真 app 的另一条端点），**不是手写的**。 */
const STATE_WITH_EDGES = { ...fixtures.characterState, edges: fixtures.states[0].edges };

const stateRoute = (body: unknown) => [{ match: /\/characters\/.*\/state/, body }];

// ══════════════════════════════════════════════════════════════════════════
// 1. 扫描面 —— 作者点得到的每一块屏幕
// ══════════════════════════════════════════════════════════════════════════

describe("扫描面：那三个测试文件之外的每一块屏幕", () => {
  it.each([
    ["顶栏", <TopBar key="t" />],
    ["左栏书架", <LeftRail key="l" onOpenChapter={() => {}} />],
    ["底栏时间线", <BottomBar key="b" />],
    ["场景条", <SceneBar key="sb" />],
    ["核对页", <ChapterPrepPage key="p" />],
    ["书架页", <BookShelf key="bs" onOpenChapter={() => {}} />],
    ["花名册抽屉", <RosterDrawer key="rd" pid="project:ID1" onClose={() => {}} />],
    ["设置抽屉", <SettingsDrawer key="sd" onClose={() => {}} />],
    ["历史抽屉", <HistoryDrawer key="hd" pid="project:ID1" chapter={1} onClose={() => {}} />],
    ["声明抽屉", <DeclareDrawer key="dd" pid="project:ID1" quote="萧决在青云城主府" onClose={() => {}} />],
  ])("「%s」上一个研发术语都没有", async (_name, ui) => {
    renderWithApi(ui, stateRoute(STATE_WITH_EDGES));
    // 等第一批查询落地：扫一块还没渲染出内容的屏幕等于什么都没扫。
    await waitFor(() => expect(document.body.textContent).toMatch(/[一-龥]/));
    await new Promise((r) => setTimeout(r, 60));
    expect(devTerms(screenText())).toEqual([]);
  });

  it.each([
    ["花名册", "roster", /青云城主府/],
    ["人物认知", "matrix", /血脉秘密/],
    ["人物状态", "state", /萧决/],
    ["人物关系", "graph", /人物关系/],
    ["原文依据", "evidence", /原文依据/],
    ["写作提醒", "constraints", /不能说破/],
    ["检查", "check", /检查本章/],
    ["待确认", "review", /关系冲突/],
  ] as const)("右栏「%s」那一格上一个研发术语都没有", async (_name, tab, marker) => {
    // `CanonEdit.boundary.test.tsx` 只在**两个编辑器摊开**的时候扫右栏；
    // 八格里另外六格一次都没被扫过。
    useCoords.setState({ activeTab: tab });
    renderWithApi(<RightPanel />, stateRoute(STATE_WITH_EDGES));
    await screen.findAllByText(marker);
    await new Promise((r) => setTimeout(r, 60));
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
