import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, stubFetch } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { RightPanel } from "./RightPanel";
import { SummaryTab } from "./SummaryTab";

// 右栏「章节总结」那一格。它补的是这个仓库第六次「最后一厘米没接」：
// 滚动总结一直在花作者的钱、一直在影响每一稿，而他看不见、改不了、删不掉。
//
// 这里钉三件事，每一件都对应一种**不报错**的坏结局：
//
// 1. 它跟着左栏选中的那一章走 —— 打错章号的话，作者会在第 99 章上改掉第 1 章的总结。
// 2. 「你撤回的」和「还没生成」是两句话 —— 合并成一句，界面就会回头催他补一件他刚做完的事。
// 3. 撤回要先确认 —— 撤掉的那一份**系统**再也不会自己买回来
//    （`chapter_refresh._head_missing`）。**作者**按得回来：手动生成
//    （`POST …/summary`）2026-08-25 删过、2026-09-05 加回来了，所以那句确认里
//    「重新生成那颗按钮还在」是真的，删按钮的人要连它一起改。
//
// （2026-08-26 之前这里还有第 4 条「作者自己写的那一段上不许贴『机器压缩的背景』那句
// 免责」——「作者写的/机器写的」这条区分整个去掉了，那条规矩没有对象了，见 ADR 0017 补记。）

/** 一章**有总结**的真 dump（`summaryGenerated` 从 `GET …/summary` dump 出来，
 *  名字留着是因为它描述的是「已经生成过的那一章长什么样」）。 */
const HAVE = fixtures.summaryGenerated;

/** 三种「没有」。**从真 dump 派生**，不是手写一个形状：
 *  契约夹具喂的是正常数据，而这三档正常数据里没有——正是它们躲过守卫的方式。 */
const NONE = { ...HAVE, summary: null, created_at: null, retracted: false };
const RETRACTED = { ...NONE, retracted: true };
const NO_TEXT = { ...NONE, has_text: false };

const summaryRoute = (body: unknown) => [{ match: /\/chapters\/\d+\/summary$/, body }];

function renderSpying(ui: ReactElement, extra: Parameters<typeof stubFetch>[0] = []) {
  stubFetch(extra);
  const inner = globalThis.fetch;
  const calls: { url: string; method: string; body: string | null }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({
      url: String(url),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? init.body : null,
    });
    return inner(url as never, init);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { calls, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    cast: "",
    activeTab: "summary",
    page: "workbench",
  });
});

describe("章节总结这一格", () => {
  it("它是右栏页签里的一格，点得到", async () => {
    useCoords.setState({ activeTab: "roster" });
    const user = userEvent.setup();
    renderSpying(<RightPanel />);
    await user.click(await screen.findByRole("button", { name: "章节总结" }));
    expect(await screen.findByDisplayValue(HAVE.summary!)).toBeInTheDocument();
  });

  it("**跟着左栏选中的那一章走** —— 读的是第 99 章，改的也是第 99 章", async () => {
    // 打错章号不会报错，只会让作者在第 99 章上改掉第 1 章的总结。
    useCoords.setState({ chapter: 99 });
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />);

    await screen.findByDisplayValue(HAVE.summary!);
    expect(calls.some((c) => c.url.includes("/chapters/99/summary"))).toBe(true);
    expect(calls.some((c) => /\/chapters\/(?!99\b)\d+\/summary$/.test(c.url))).toBe(false);

    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "改一段。");
    await user.click(screen.getByRole("button", { name: "保存这一段" }));

    await waitFor(() =>
      expect(
        calls.find((c) => c.method === "PATCH")?.url.includes("/chapters/99/summary"),
      ).toBe(true),
    );
  });

  it("角色册空着的时候它照样能用（它和「这本书里有谁」没关系）", async () => {
    renderSpying(<RightPanel />, [{ match: /\/roster$/, body: [] }]);
    expect(await screen.findByDisplayValue(HAVE.summary!)).toBeInTheDocument();
    expect(screen.queryByText(/添加人物或设定后/)).toBeNull();
  });

  it("模型服务没配好、又没有总结：第一句是「先连接模型」，不再说「保存后自动生成」", async () => {
    // 那两条路（保存后自动生成 / 点「生成」）没配好都走不通——先把路画出来（作者 2026-09-13）。
    renderSpying(<SummaryTab />, [
      ...summaryRoute(NONE),
      { match: /\/api\/settings$/, body: fixtures.settings }, // model_configured: false
    ]);
    expect(await screen.findByText("章节总结由模型生成，需先连接模型服务：")).toBeInTheDocument();
    expect(screen.queryByText(/保存正文后将自动生成/)).toBeNull();
  });

  it("改一段 → 保存，发出去的就是他打的那段字", async () => {
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />);
    await screen.findByDisplayValue(HAVE.summary!);

    // 没改之前不该有「保存」——那颗按钮亮着等于请他保存一份他没动过的东西。
    expect(screen.queryByRole("button", { name: "保存这一段" })).toBeNull();

    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "玄铁令易手那一场。");
    await user.click(screen.getByRole("button", { name: "保存这一段" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patched = calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(patched.body!)).toEqual({ summary: "玄铁令易手那一场。" });
  });

  it("撤回要先确认，而那句确认得说清「再要一份是要花钱的」", async () => {
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />);
    await screen.findByDisplayValue(HAVE.summary!);

    await user.click(screen.getByRole("button", { name: "撤回" }));
    expect(await screen.findByText(/调用一次模型/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "撤回" }));
    await user.click(screen.getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("「你撤回的」和「还没生成」是两句话，不是同一句", async () => {
    // 起草那边它们完全同义（两种都不进 prompt），可下一步动作正好相反。
    const { unmount } = renderSpying(<SummaryTab />, summaryRoute(NONE));
    expect(await screen.findByText(/本章尚无总结/)).toBeInTheDocument();
    unmount();

    renderSpying(<SummaryTab />, summaryRoute(RETRACTED));
    expect(await screen.findByText(/总结已撤回/)).toBeInTheDocument();
    expect(screen.queryByText(/本章尚无总结/)).toBeNull();
  });

  it("撤回之后那颗「重新生成」在，按一下就 POST —— 那是拿回机器总结的唯一路", async () => {
    // **系统永远不补撤回过的章**（`_head_missing`：他删一次系统买回来一次 =
    // 花他没按过的钱抹掉他刚做的动作）。所以这颗按钮不在，那一章就死在那儿了
    // ——2026-08-25 到 09-05 之间正是这个样子，屏幕上还写着「点『重新生成』」。
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />, summaryRoute(RETRACTED));
    const button = await screen.findByRole("button", { name: "重新生成" });
    await user.click(button);
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "POST" && /\/chapters\/\d+\/summary$/.test(c.url)),
      ).toBe(true),
    );
  });

  it("从没生成过的那一章上，同一颗按钮叫「生成」", async () => {
    // 两种「没有」下一步动作相同（都是生成），但**说法不同**：一种是他刚做的动作，
    // 一种是还没发生过的事。按钮上的字跟着那句话走。
    renderSpying(<SummaryTab />, summaryRoute(NONE));
    expect(await screen.findByRole("button", { name: "生成" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
  });

  it("有总结在用的时候**不**摆那颗按钮 —— 按下去后端判重，什么都不会发生", async () => {
    // 一颗按下去没反应的按钮比没有按钮更糟：`RollingSummarizer.ensure` 按 prompt
    // 内容地址判重，这一章的总结就是这一版正文压出来的，它直接返回旧的那一份。
    renderSpying(<SummaryTab />);
    await screen.findByDisplayValue(HAVE.summary!);
    expect(screen.queryByRole("button", { name: /生成/ })).toBeNull();
    expect(screen.getByRole("button", { name: "撤回" })).toBeInTheDocument();
  });

  it("这一章还没写：不给任何会花钱的按钮，并且说清为什么", async () => {
    renderSpying(<SummaryTab />, summaryRoute(NO_TEXT));
    expect(await screen.findByText(/尚无正文/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成/ })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("会花钱的按钮**只有那一颗**，别的怎么点都不会打出一次付费调用", async () => {
    // 这条测试的口径改过两次，而它护的东西一次没变：**界面不许替作者花钱。**
    //   · 2026-08-25 手动生成整条下线，它一度钉的是「那颗按钮不该存在」；
    //   · 2026-09-05 维护者把它加回来了（撤回过的章否则永远拿不回机器总结），
    //     于是它回到「付费入口恰好一个，且要他亲手按」。
    //
    // 它红了有两种可能，都要人来看一眼：
    //   · 付费入口的数目变了 —— 那是产品裁定，得先改裁定再改代码；
    //   · 这一格自己替作者打出了一次付费调用 —— 那是这个仓库修过第六次的那个病。
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />, summaryRoute(NONE));
    expect(await screen.findByText(/本章尚无总结/)).toBeInTheDocument();
    const paid = screen.getByRole("button", { name: "生成" });

    // 那一颗之外的每一颗都按一遍，一次 POST 都不许出来。
    // （「撤回它」要先点「撤回」确认，所以这一轮真正打出去的只有 DELETE 那条路——
    //  而这一章没有总结，连那条也走不到。）
    for (const button of screen.queryAllByRole("button")) {
      if (button !== paid) await user.click(button);
    }
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("「跳到原文」把中栏换回正文（作者可能正摊着活动记录）", async () => {
    const user = userEvent.setup();
    useCoords.setState({ page: "log" });
    renderSpying(<SummaryTab />);
    await screen.findByDisplayValue(HAVE.summary!);

    await user.click(screen.getByRole("button", { name: "跳到原文" }));
    expect(useCoords.getState().page).toBe("workbench");
  });

  it("读不出来 ≠ 没有总结 —— 那时不许摆一个空框", async () => {
    renderSpying(<SummaryTab />, [
      { match: /\/chapters\/\d+\/summary$/, status: 500, body: {} },
    ]);
    expect(await screen.findByText(/读取失败/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("后端拒绝时照说后端那句话，不换成自己编的一句", async () => {
    const user = userEvent.setup();
    const said = "这一段太长了（1001 字，最多 1000 字）。";
    renderSpying(<SummaryTab />, [
      { method: "PATCH", match: /\/chapters\/\d+\/summary$/, status: 422, body: { detail: said } },
    ]);
    await screen.findByDisplayValue(HAVE.summary!);
    await user.type(screen.getByRole("textbox"), "再加一句。");
    await user.click(screen.getByRole("button", { name: "保存这一段" }));

    expect(await screen.findByText(said)).toBeInTheDocument();
  });

  it.each([
    ["有总结", HAVE],
    ["没生成过", NONE],
    ["撤回过", RETRACTED],
    ["这一章还没写", NO_TEXT],
  ])("四种长相里「%s」那一档上一个研发术语都没有", async (_name, body) => {
    renderSpying(<SummaryTab />, summaryRoute(body));
    await waitFor(() => expect(document.body.textContent).toMatch(/[一-龥]/));
    await new Promise((r) => setTimeout(r, 30));
    expect(devTerms(screenText())).toEqual([]);
  });
});

// ── 总结 = 可反查的记忆点（T6）────────────────────────────────────────────────
//
// 作者要的是「迅速找到相关章节的总结，然后引用、对比、调研，再顺下去看全文」，
// 且明说**不用 RAG**。所以这里钉的四件事全是「它没有变成一个假的检索器」：
//
// 1. 芯片摆的是**这一段真提到的东西**，点得动。
// 2. 点下去列的是**别的章**，按章号排，带原文——不是一句「找到 3 条」。
// 3. **零带着理由**：一段谁也没提到、和一个人只出现在这一章，说两句不一样的话。
// 4. 屏幕上**一个「相关度」都没有**，一颗会花钱的按钮都没有。

const mentionsRoute = (body: unknown) => [
  { match: /\/chapters\/\d+\/summary\/mentions$/, body },
];

/** 芯片上会写哪几个名字 —— **从同一份 dump 里读**，不在这儿抄一遍。
 *  抄一遍就是又一份手写夹具，而这份文件正是为了不那么干才存在的。 */
const NAMED = fixtures.summaryMentions.mentions.map((m) => m.node.name);
const [WHO] = NAMED;

describe("总结下面那排记忆点", () => {
  it("摆的是这一段真提到的东西，每一个都点得动", async () => {
    renderSpying(<SummaryTab />);
    for (const name of NAMED) {
      expect(await screen.findByRole("button", { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it("点一个 → 列出还有哪几章的总结提到它，按章号排，带原文", async () => {
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />);
    await user.click(await screen.findByRole("button", { name: new RegExp(WHO) }));

    // **反查是一次 SQL，不是一次模型调用**：这一层从头到尾没有 POST。
    await waitFor(() =>
      expect(calls.some((c) => /\/summary-mentions$/.test(c.url))).toBe(true),
    );
    expect(calls.every((c) => c.method === "GET")).toBe(true);

    // 当前这一章不再重复列一遍（它就摊在上面那个框里）。
    expect(await screen.findByRole("button", { name: "第 7 章" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "第 1 章" })).toBeNull();
    // 原文摆在名单里：作者点开是为了读它、比它、引它，不是为了知道它存在。
    expect(screen.getAllByText(HAVE.summary!).length).toBeGreaterThan(1);
  });

  it("点章号 = 换到那一章，并把中栏换回正文", async () => {
    const user = userEvent.setup();
    useCoords.setState({ page: "log" });
    renderSpying(<SummaryTab />);
    await user.click(await screen.findByRole("button", { name: new RegExp(WHO) }));
    await user.click(await screen.findByRole("button", { name: "第 7 章" }));

    expect(useCoords.getState().chapter).toBe(7);
    expect(useCoords.getState().page).toBe("workbench");
  });

  it("再点一次收起来 —— 不至于点错了就没法退出", async () => {
    const user = userEvent.setup();
    renderSpying(<SummaryTab />);
    const chip = await screen.findByRole("button", { name: new RegExp(WHO) });
    await user.click(chip);
    expect(await screen.findByRole("button", { name: "第 7 章" })).toBeInTheDocument();
    await user.click(chip);
    await waitFor(() => expect(screen.queryByRole("button", { name: "第 7 章" })).toBeNull());
  });

  it("一个都没提到时说清为什么，而不是留一片空白", async () => {
    // 空白会被读成「引擎没在干活」；而这一档的下一步是去角色册把那个称呼建上。
    renderSpying(<SummaryTab />, mentionsRoute({ chapter: 1, mentions: [] }));
    expect(await screen.findByText(/未提及角色册中的任何条目/)).toBeInTheDocument();
  });

  it.each([
    ["还没生成", NONE],
    ["撤回过", RETRACTED],
  ])("这一章**没有总结**（%s）时整层不出现", async (_name, body) => {
    // 那时后端回的也是空表，可「这一段里没出现角色册上的任何人」在没有「这一段」的
    // 时候是一句假话 —— 它会让作者以为自己写的那一章里一个人都没有。
    renderSpying(<SummaryTab />, summaryRoute(body));
    await screen.findByRole("textbox");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText(/未提及角色册中的任何条目/)).toBeNull();
    for (const name of NAMED) {
      expect(screen.queryByRole("button", { name: new RegExp(name) })).toBeNull();
    }
  });

  it("只有这一章提到他的时候，说清「这里翻的是总结不是正文」", async () => {
    // 不说这句的话，「全书只有这一章提到他」会被读成一句关于**正文**的断言 —— 而它不是。
    const user = userEvent.setup();
    renderSpying(<SummaryTab />, [
      {
        match: /\/summary-mentions$/,
        body: { node: fixtures.summaryMentions.mentions[0].node, chapters: [] },
      },
    ]);
    await user.click(await screen.findByRole("button", { name: new RegExp(WHO) }));
    expect(await screen.findByText(/查找的是总结而非正文/)).toBeInTheDocument();
  });

  it("查不出来 ≠ 它谁也没提到", async () => {
    renderSpying(<SummaryTab />, [
      { match: /\/chapters\/\d+\/summary\/mentions$/, status: 500, body: {} },
    ]);
    expect(await screen.findByText(/下方为空不代表/)).toBeInTheDocument();
  });

  it("摊开之后屏幕上一个研发术语都没有（节点 id、Character、相关度都不许上屏）", async () => {
    const user = userEvent.setup();
    renderSpying(<SummaryTab />);
    await user.click(await screen.findByRole("button", { name: new RegExp(WHO) }));
    await screen.findByRole("button", { name: "第 7 章" });
    expect(devTerms(screenText())).toEqual([]);
    expect(screenText()).not.toMatch(/相关度|匹配度|相似/);
  });
});

describe("全书总结状态（Step 4）", () => {
  it("挂在这一格最上面：全书哪些章有/缺/不对齐/异常一眼看清", async () => {
    renderSpying(<SummaryTab />);
    // 真后端视图（测试 harness 的 `…/summary-status` stub）：5 章 = 1 有 + 2 缺
    // + 1 不对齐 + 1 异常；作者正写在第 4 章。
    //
    // **一章一颗芯片就是全部账目**（2026-09-05 作者点名去掉了下面那句总账：
    // 「全书 5 章：1 章有总结、2 章缺…」——同一份数在同一块屏幕上写第二遍）。
    // 所以这条测试从今天起数芯片，不认那句话。
    await screen.findByRole("button", { name: "第 1 章，有" });
    expect(screen.getByRole("button", { name: "第 3 章，缺" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "第 2 章，不对齐" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "第 5 章，空" })).toBeInTheDocument();
    // 异常那章挂「异常」而不是「缺」。
    expect(screen.getByRole("button", { name: "第 4 章，异常" })).toBeInTheDocument();
    expect(screenText()).not.toMatch(/章有总结|章缺。|在位信号/);
  });

  it("缺章/异常章有标记芯片，点击跳到那一章", async () => {
    const user = userEvent.setup();
    renderSpying(<SummaryTab />);
    await screen.findByText(/会自动补写/);
    const chip = screen.getByRole("button", { name: "第 3 章，缺" });
    expect(chip.className).toContain("status-chip-missing");
    await user.click(chip);
    expect(useCoords.getState().chapter).toBe(3);
    expect(useCoords.getState().page).toBe("workbench");
    expect(screen.getByRole("button", { name: "第 4 章，异常" })).toBeInTheDocument();
  });

  it("查不出来 ≠ 全书没状态（不许静默）", async () => {
    renderSpying(<SummaryTab />, [
      { match: /\/summary-status$/, status: 500, body: {} },
    ]);
    expect(await screen.findByText(/全书总结状态读取失败/)).toBeInTheDocument();
  });
});
