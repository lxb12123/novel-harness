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
// 这里钉四件事，每一件都对应一种**不报错**的坏结局：
//
// 1. 它跟着左栏选中的那一章走 —— 打错章号的话，作者会在第 99 章上改掉第 1 章的总结。
// 2. 「你撤回的」和「还没生成」是两句话 —— 合并成一句，界面就会回头催他补一件他刚做完的事。
// 3. 撤回要先确认 —— 撤了再要一份新的得**再花一次钱**。
// 4. 作者自己写的那一段上不许贴「机器压缩的背景」那句免责。

/** 一章**有总结**的真 dump（`summaryGenerated` 是 `POST …/summary` 的真出参）。 */
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

  it("花名册空着的时候它照样能用（它和「这本书里有谁」没关系）", async () => {
    renderSpying(<RightPanel />, [{ match: /\/roster$/, body: [] }]);
    expect(await screen.findByDisplayValue(HAVE.summary!)).toBeInTheDocument();
    expect(screen.queryByText(/添加人物或设定后/)).toBeNull();
  });

  it("模型写的那一段带着免责，作者自己写的那一段不带", async () => {
    const { unmount } = renderSpying(<SummaryTab />);
    expect(await screen.findByText(/模型压出来的背景/)).toBeInTheDocument();
    unmount();

    renderSpying(<SummaryTab />, summaryRoute({ ...HAVE, author_written: true }));
    expect(await screen.findByText("这一段是你自己写的。")).toBeInTheDocument();
    expect(screen.queryByText(/模型压出来的背景/)).toBeNull();
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
    expect(await screen.findByText(/再跑一次模型/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "算了" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "撤回" }));
    await user.click(screen.getByRole("button", { name: "撤回它" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("「你撤回的」和「还没生成」是两句话，不是同一句", async () => {
    // 起草那边它们完全同义（两种都不进 prompt），可下一步动作正好相反。
    const { unmount } = renderSpying(<SummaryTab />, summaryRoute(NONE));
    expect(await screen.findByText(/这一章还没有总结/)).toBeInTheDocument();
    unmount();

    renderSpying(<SummaryTab />, summaryRoute(RETRACTED));
    expect(await screen.findByText(/被你撤回了/)).toBeInTheDocument();
    expect(screen.queryByText(/这一章还没有总结/)).toBeNull();
  });

  it("这一章还没写：不给任何会花钱的按钮，并且说清为什么", async () => {
    renderSpying(<SummaryTab />, summaryRoute(NO_TEXT));
    expect(await screen.findByText(/还没有正文/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成/ })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("生成那颗按钮**自己说它要花钱**，点了才打出去", async () => {
    const user = userEvent.setup();
    const { calls } = renderSpying(<SummaryTab />, summaryRoute(NONE));
    const button = await screen.findByRole("button", { name: /^生成/ });
    expect(button).toHaveTextContent("要跑一次模型");
    // **渲染这一格不会替作者按下任何一次付费调用。**
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.click(button);
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/summary"))).toBe(true),
    );
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
    expect(await screen.findByText(/没读出来/)).toBeInTheDocument();
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
