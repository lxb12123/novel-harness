import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, ROUND_DONE } from "../test/harness";
import { devTerms, rawIds, screenText } from "../test/screenGuard";
import type { DraftCandidateView } from "../api/types";
import { DEFAULT_LEFT, DEFAULT_RIGHT, DIVIDER_PX } from "../layout";
import { useLiveDraft } from "../liveDraft";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";
import { SplitPanes } from "./SplitPanes";

// **对抗性复核：桌上那几稿在对话里那几行**（ADR 0022；入口 2026-09-12 起按 ADR 0048：
// 对话里每稿一行、字不画；并排比那一页和「本章 N 稿」入口同日撤了）。
//
// `DraftCandidates.test.tsx` / `drafts.test.ts` 是造这一刀的人自己架的网。这一份只站
// **它们没站到的位置**：
//
// 1. **「在哪儿」来自后端那一列**。说「已写入」的那一行**不许是位置或字数推出来的**，
//    以及**顺序照后端给的**——真数据里 `landed` 恰好又新又靠后，两种错法在真数据上
//    长得一模一样。
// 2. **形状网扫的是这块屏幕的「有数据」那一支。** 兜底那一支（「放入编辑器」读不出来）
//    正常数据下永远不亮，正是它躲过守卫的方式——那也是 `DevTerms.guard.test.tsx` 开头
//    写着的那条教训。
// 3. **三栏骨架**：这块屏幕在中栏里长出几行，而 `SplitPanes.test.tsx` 量的是没有它的时候。
//
// ── 变体从哪儿来 ──────────────────────────────────────────────────────────
//
// 全部从真 dump 派生（`__fixtures__/api.json` 的 `chatTurn` / `draftDetail`），
// **只改 `ordinal` / `landed` / `note` 三个字段**——同 `DevTerms.guard.test.tsx` 那段
// 说明：契约夹具 dump 的是「一稿、没落盘」，而「几稿摆着让作者挑」才是 ADR 0022 的
// 入口形态，它按定义不在那份 dump 里。**没有一个字节是手写的响应体。**

const REAL = fixtures.chatTurn.drafts[0] as DraftCandidateView;
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });

/**
 * 三稿都**还在桌上**（一批三稿同时飞、一稿都没进书那一档），
 * **故意摆成「位置 / 编号 / 字数都指向另一版」**，编号乱着（7 / 2 / 5）。
 *
 * 后端的排序口径是 `(chapter, ordinal)`，所以真实数据里这几种判据经常重合；
 * 重合的时候「照后端给的」和「自己算一个」在屏幕上分不开。
 */
const THREE: DraftCandidateView[] = [
  variant({ id: "draft:ID71", ordinal: 7, units: 3100, landed: false, note: "这一版最长。" }),
  variant({ id: "draft:ID72", ordinal: 2, units: 900, landed: false, note: "这一版最短。" }),
  variant({ id: "draft:ID73", ordinal: 5, units: 2600, landed: false, note: "" }),
];

/** 同一批，但**中间**那一版写进了那一章——它的 `ordinal` 最小、`units` 最少，照
 *  「最新的」「最长的」「第一个」任何一种反推，挑中的都不是它。 */
const MIXED: DraftCandidateView[] = THREE.map((d) =>
  d.id === "draft:ID72" ? { ...d, landed: true } : d,
);

/** 后端给的那个顺序（**从同一份数据推出来，不是抄一遍**）。屏幕上必须逐字是它。
 *  `TurnReceipt.drafts` 已经排好序，而这几个编号故意乱着（7 / 2 / 5）——任何一种
 *  「自己再排一遍」都会排成 2 / 5 / 7。 */
const ORDER = THREE.map((d) => `第 ${d.ordinal} 稿`);

const turnWith = (drafts: DraftCandidateView[]) => ({ ...fixtures.chatTurn, drafts });

/** 放进编辑器时后端给的那一份（真 dump），正文换成一句认得出的话。 */
const DETAIL = { ...fixtures.draftDetail, text: "（这一稿的全文，比预览长得多。）" };

const draftRoutes = () => [{ match: /\/drafts\/[^/]+$/, body: DETAIL }];

/** 屏幕上按出现次序排的那几个「第 N 稿」。 */
const rowsOnScreen = () =>
  [...document.querySelectorAll(".draft-row b")].map((el) => el.textContent ?? "");
/** 取一稿全文那条路由（`/drafts/{id}`）被打了几次。 */
const fullTextCalls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => String(c[0])).filter((u) => /\/drafts\/[^/?]+$/.test(u));

const say = () => screen.getByRole("textbox", { name: "输入消息" });

/** 跑一轮，让那几稿摆出来。**每次自己造一个 `user`**：同一个测试里渲染三次时，
 *  上一次 `cleanup()` 之后旧的那个握着的是已经被卸掉的那棵树。 */
async function runTurn() {
  const user = userEvent.setup();
  await screen.findByText(fixtures.chatDetail.messages[0].text);
  await user.type(say(), "这一场写三个版本我挑");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText(ROUND_DONE);
}

beforeEach(() => {
  useLiveDraft.setState({ draft: null, inEditor: false, placed: null, saved: [] });
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
});

afterEach(() => {
  window.location.hash = "";
  localStorage.clear();
});

// ══════════════════════════════════════════════════════════════════════════
// 一、对话里：一稿一行，按后端的次序，字一个都不画
// ══════════════════════════════════════════════════════════════════════════

describe("对话里那几行", () => {
  it("一稿一行，逐条、按后端的次序；**字一个都不画**，全文一个字节都不取", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(),
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn();
    await screen.findByText(ORDER[2]);
    expect(rowsOnScreen()).toEqual(ORDER);
    expect(document.querySelector(".draft-card")).toBeNull();
    expect(screen.queryByText(DETAIL.text)).toBeNull();
    await new Promise((r) => setTimeout(r, 40));
    expect(fullTextCalls(spy)).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 二、写进去的那一版：**后端给的那个动作**决定那一行说什么
// ══════════════════════════════════════════════════════════════════════════

describe("写进那一章的那一行说「已写入」，还在桌上的给「放入编辑器」（ADR 0048）", () => {
  it("哪怕写进去的那版又短、编号又小、还夹在中间 —— 判据在后端那一列上，不在位置上", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(MIXED) },
      ...draftRoutes(),
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn();
    await screen.findByText(ORDER[2]);

    expect(rowsOnScreen()).toEqual(ORDER);
    const rows = [...document.querySelectorAll(".draft-row")] as HTMLElement[];
    const landed = rows.filter((row) => /已写入/.test(row.textContent ?? ""));
    expect(landed.map((row) => row.querySelector("b")?.textContent)).toEqual(["第 2 稿"]);
    expect(within(landed[0]).queryByRole("button", { name: "放入编辑器" })).toBeNull();
    const desk = rows.filter((row) => !/已写入/.test(row.textContent ?? ""));
    expect(desk.map((row) => row.querySelector("b")?.textContent)).toEqual(["第 7 稿", "第 5 稿"]);
    for (const row of desk) expect(within(row).getByRole("button", { name: "放入编辑器" })).toBeInTheDocument();
    // 正文在左边——这儿**一版都不摊开、一整章正文一个字节都没取**。
    await new Promise((r) => setTimeout(r, 40));
    expect(fullTextCalls(spy)).toEqual([]);
  });

  it("换一版进书，那一行跟着换", async () => {
    const moved = THREE.map((d) => (d.id === "draft:ID73" ? { ...d, landed: true } : d));
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(moved) },
      ...draftRoutes(),
    ]);
    await runTurn();
    await screen.findByText("第 7 稿");
    const landed = [...document.querySelectorAll(".draft-row")].filter((row) =>
      /已写入/.test(row.textContent ?? ""),
    );
    expect(landed.map((row) => row.querySelector("b")?.textContent)).toEqual(["第 5 稿"]);
  });

  it("**一版都没落盘：一版都不摊开，也不摆一句「建议用哪一版」**", async () => {
    // 后端不排名、不打分（ADR 0005），这一层更不许。一批都没落盘时两边都不挑
    // （同 `AmbiguousName`：两个方向都贵就摊开，绝不挑）。
    const none = THREE;
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(none) },
      ...draftRoutes(),
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn();
    await screen.findByText("第 5 稿");

    expect(document.querySelector(".draft-card")).toBeNull();
    expect(screen.getAllByRole("button", { name: "放入编辑器" })).toHaveLength(3);
    expect(fullTextCalls(spy)).toEqual([]);
    // 屏幕上没有任何一句在替作者拿主意。
    expect(screenText()).not.toMatch(/推荐|建议用|最好的一版|最佳|挑这版/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 三、形状网：**兜底那几支**（正常数据下永远不亮）
// ══════════════════════════════════════════════════════════════════════════

describe("兜底那一支上一个研发术语都没有", () => {
  it("**自守卫**：这批数据里那个内部标识真的是网认得的那种形状", () => {
    // 没有它，下面每一条「屏幕上没有内部标识」都可能只是因为那批数据里根本没有
    // 一个咬得住的东西——这个仓库为「一张什么都咬不到却一直绿着的网」吃过三次亏。
    expect(rawIds(THREE[0].id)).toEqual(["draft:ID71"]);
  });

  it("对话里点「放入编辑器」，那一整章**读不出来**的时候", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      {
        match: /\/drafts\/[^/]+$/,
        status: 404,
        body: { detail: { error: "draft_not_found", draft_id: "draft:ID72" } },
      },
    ]);
    await runTurn();
    const row = screen.getByText("第 2 稿").closest(".draft-row") as HTMLElement;
    await userEvent.setup().click(within(row).getByRole("button", { name: "放入编辑器" }));
    await within(row).findByText(/稿件读取失败/);
    expect(useLiveDraft.getState().draft).toBeNull();
    expect(devTerms(screenText())).toEqual([]);
  });
});


// ══════════════════════════════════════════════════════════════════════════
// 四、三栏骨架：中栏里长出几行稿子，两侧不许跟着动
// ══════════════════════════════════════════════════════════════════════════

describe("三栏没被这几稿挤动", () => {
  const shell = () => (
    <SplitPanes
      left={<div>左栏</div>}
      center={<div>正文</div>}
      chat={<ChatPanel />}
      right={<div>右栏</div>}
    />
  );

  const EXPECTED =
    `${DEFAULT_LEFT}px ${DIVIDER_PX}px minmax(0, 1fr) ${DIVIDER_PX}px ${DEFAULT_RIGHT}px`;

  it("**逐字节钉那两个数**（三稿摆着、一稿进了书）", async () => {
    // 「前后相等」单独一条是骗得过的：两边一起错成同一个值它也绿。所以这儿钉的是
    // `DEFAULT_LEFT` / `DEFAULT_RIGHT` 本身。
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(MIXED) },
      ...draftRoutes(),
    ]);
    await runTurn();
    await screen.findByText(ORDER[2]);
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(EXPECTED);
    // 中栏内部那一行（正文 / 写作助手对半分）也没被顶开。
    const split = document.querySelector(".center-split") as HTMLElement;
    expect(split.style.gridTemplateColumns).toContain("50fr");
  });

  it("这几稿一个都没有的时候，那一行逐字节还是同一串", async () => {
    renderWithApi(shell());
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(EXPECTED);
  });
});
