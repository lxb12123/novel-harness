import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { fixtures, renderWithApi } from "../test/harness";
import { rawIds, screenText } from "../test/screenGuard";
import type { DraftCandidateView } from "../api/types";
import { COMPARE_OPEN_MAX } from "../drafts";
import { writeCompareHandoff } from "../route";
import { DraftCompare } from "./DraftCompare";

// 第三档：稿件并排对比那一页（`#/compare/{章号}`，ADR 0022）。
//
// **它是同一个应用的另一条路由**，不是另一个东西——工作台本来就是本地浏览器应用，
// 所以「在新标签页里并排读三稿」= 一条哈希路由，零新基础设施。
//
// 这一页最容易犯的错是**猜**：地址里只有章号，书是开链接那一下留下的。
// 留不下的时候它必须说「不知道」，而不是拿另一本书的稿子冒充（那时作者会对着
// 另一本书的正文做决定，而屏幕上没有任何东西提示他）。

const REAL = fixtures.drafts.drafts[0] as DraftCandidateView;
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });
const listOf = (drafts: DraftCandidateView[]) => ({ drafts });

/** 摊开一稿时后端给的那一份，正文换成一句认得出的话（同 `DraftCandidates.test.tsx`：
 *  真 dump 的正文开头和预览一模一样，拿它当判据分不出摊没摊开）。 */
const DETAIL = { ...fixtures.draftDetail, text: "（这一稿的全文。）" };

const fullTextCalls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((call) => String(call[0])).filter((u) => /\/drafts\/[^/?]+$/.test(u));

afterEach(() => {
  // jsdom 的地址在同一个文件的多个 test 之间是共享的（同 `setup.ts` 里那段 cleanup）。
  window.location.hash = "";
});

describe("并排读", () => {
  it("几稿几列，每一列都是全文", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    const three = [1, 2, 3].map((n) => variant({ id: `draft:ID4${n}`, ordinal: n }));
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/drafts(\?|$)/, body: listOf(three) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");

    await screen.findByText("第 3 稿");
    await waitFor(() => expect(screen.getAllByText(DETAIL.text)).toHaveLength(3));
    expect(document.querySelector(".compare-cols")).not.toBeNull();
    // 书名 + 章号：这一页开在另一个标签页里，**它得自己说清是哪本书的第几章**。
    expect(screen.getByText(/青云记 · 第 2 章/)).toBeInTheDocument();
    expect(rawIds(screenText())).toEqual([]);
    expect(fullTextCalls(spy).length).toBeLessThanOrEqual(3);
  });

  it("**这一页只读** —— 要用哪一版，回工作台跟助手说（一个功能不留两个入口）", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    renderWithApi(<DraftCompare chapter={2} />);
    await screen.findByText(/本页仅供阅读/);
    // 这儿没有「就用这一版」——落盘是助手的动作（ADR 0021 / 0022）。
    expect(screen.queryByRole("button", { name: /用这一版|写进|保存/ })).toBeNull();
  });

  it("桌上超过三稿：**先摊开最近的三份**，更早的收着、点一下才取", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    const five = [1, 2, 3, 4, 5].map((n) => variant({ id: `draft:ID4${n}`, ordinal: n }));
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/drafts(\?|$)/, body: listOf(five) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();

    await screen.findByText("第 5 稿");
    await waitFor(() => expect(fullTextCalls(spy)).toHaveLength(COMPARE_OPEN_MAX));
    // 收着的那两份说出来了（不说的话，那两列看起来像加载失败）。
    await screen.findByText(/共 5 稿/);

    await user.click(screen.getByRole("button", { name: "展开第 4 稿" }));
    await waitFor(() => expect(fullTextCalls(spy)).toHaveLength(COMPARE_OPEN_MAX + 1));
  });

  it("这一章桌上没有稿子的时候，说的是「没有」，不是一片空白", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 7 });
    renderWithApi(<DraftCompare chapter={7} />, [
      { match: /\/drafts(\?|$)/, body: listOf([]) },
    ]);
    await screen.findByText(/第 7 章尚无稿件/);
  });
});

describe("这一页是哪本书的", () => {
  it("库里只有一本书时不用问 —— 没有可猜的（工作台自己也是这么开的）", async () => {
    expect(fixtures.projects).toHaveLength(1); // 探针
    renderWithApi(<DraftCompare chapter={2} />);
    await screen.findByText("第 1 稿");
  });

  it("**好几本书 + 没有那次交接：说不知道，而且一条稿子都不去取**", async () => {
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/api\/projects$/, body: fixtures.projectsTwo },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");

    await screen.findByText(/此链接未指明/);
    await new Promise((r) => setTimeout(r, 40));
    expect(spy.mock.calls.filter(([u]) => /\/drafts/.test(String(u)))).toEqual([]);
  });

  it("**连不上后端和「不知道是哪本书」不是同一句话** —— 两句话对着两个不同的下一步", async () => {
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/api\/projects$/, status: 500, body: { detail: "boom" } },
    ]);
    await screen.findByText(/无法连接工作台服务/);
    expect(screen.queryByText(/此链接未指明/)).toBeNull();
  });

  it("那次交接是**为另一章**留的：同样不猜", async () => {
    // 作者拿一个旧链接开另一章 —— 拿另一章存下的书去读这一章，读的可能是另一本书。
    writeCompareHandoff({ book: "project:ID2", chapter: 5 });
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/api\/projects$/, body: fixtures.projectsTwo },
    ]);
    await screen.findByText(/此链接未指明/);
  });
});

describe("路由：这条地址真的开得出那一页", () => {
  it("`#/compare/2` 开的是并排比，不是工作台", async () => {
    window.location.hash = "#/compare/2";
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    renderWithApi(<App />);

    await screen.findByText(/稿件并排对比/);
    // 工作台那一整套（顶栏 / 三栏 / 编辑器）在这个标签页里一个都没挂。
    expect(screen.queryByRole("button", { name: "写作助手" })).toBeNull();
  });
});
