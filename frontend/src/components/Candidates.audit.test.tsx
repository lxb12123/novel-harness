import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, ROUND_DONE } from "../test/harness";
import { devTerms, rawIds, screenText } from "../test/screenGuard";
import type { DraftCandidateView } from "../api/types";
import { DEFAULT_LEFT, DEFAULT_RIGHT, DIVIDER_PX } from "../layout";
import { writeCompareHandoff } from "../route";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";
import { DraftCompare } from "./DraftCompare";
import { SplitPanes } from "./SplitPanes";

// **对抗性复核：桌上那几稿的三档界面**（ADR 0022）。
//
// `DraftCandidates.test.tsx` / `DraftCompare.test.tsx` / `drafts.test.ts` 是造这一刀的人
// 自己架的网。这一份只站**它们没站到的位置**：
//
// 1. **三档是「同一份数据的三种排布」，而没有任何东西验过它们摆的是同一份。**
//    那三个文件各自喂各自的一批稿子，于是「窄档和宽档看见的不是同一批」「并排比那一页
//    少了一稿」这一类失败没有判据。这儿一批数据渲染三次，逐条对。
// 2. **「推荐位来自后端」今天只在纯函数上验过**（`openByDefault`）。屏幕这一侧还差两条：
//    摊开的那一版**不许是位置或字数推出来的**，以及**顺序照后端给的**——
//    真数据里 `landed` 恰好又新又靠后，两种错法在真数据上长得一模一样。
// 3. **形状网扫的是这块屏幕的「有数据」那一支。** 兜底那几支（正在读 / 读不出来 /
//    桌上是空的 / 连不上 / 不知道是哪本书）正常数据下永远不亮，正是它们躲过守卫的方式
//    ——那也是 `DevTerms.guard.test.tsx` 开头写着的那条教训。
// 4. **三栏骨架**：这块屏幕会在中栏里长出几列并排的正文，而 `SplitPanes.test.tsx`
//    量的是没有它的时候。
//
// ── 变体从哪儿来 ──────────────────────────────────────────────────────────
//
// 全部从真 dump 派生（`__fixtures__/api.json` 的 `drafts` / `chatTurn` / `draftDetail`），
// **只改 `ordinal` / `landed` / `note` 三个字段**——同 `DevTerms.guard.test.tsx` 那段
// 说明：契约夹具 dump 的是「一稿、没落盘」，而「几稿摆着让作者挑」才是 ADR 0022 的
// 入口形态，它按定义不在那份 dump 里。**没有一个字节是手写的响应体。**

const REAL = fixtures.drafts.drafts[0] as DraftCandidateView;
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });

/**
 * 三稿，**故意摆成「位置 / 编号 / 字数都指向另一版」**：
 *
 * - 进了书的是**中间**那一版（不是第一版，也不是最后一版）；
 * - 它的 `ordinal` 最小、`units` 最少——照「最新的」「最长的」「第一个」任何一种
 *   反推，挑中的都不是它。
 *
 * 后端的排序口径是 `(chapter, ordinal)`，所以真实数据里这三种判据经常重合；
 * 重合的时候「照后端给的」和「自己算一个」在屏幕上分不开。
 */
const THREE: DraftCandidateView[] = [
  variant({ id: "draft:ID71", ordinal: 7, units: 3100, note: "这一版最长。" }),
  variant({ id: "draft:ID72", ordinal: 2, units: 900, landed: true, note: "这一版最短。" }),
  variant({ id: "draft:ID73", ordinal: 5, units: 2600, note: "" }),
];

/** 后端给的那个顺序（**从同一份数据推出来，不是抄一遍**）。三档都必须逐字是它：
 *  这就是「三档摆的是同一份数据」这句话的判据。`TurnReceipt.drafts` 已经排好序，
 *  而这几个编号故意乱着（7 / 2 / 5）——任何一种「自己再排一遍」都会排成 2 / 5 / 7。 */
const ORDER = THREE.map((d) => `第 ${d.ordinal} 稿`);

const listOf = (drafts: DraftCandidateView[]) => ({ drafts });
const turnWith = (drafts: DraftCandidateView[]) => ({ ...fixtures.chatTurn, drafts });

/** 摊开一稿时后端给的那一份（真 dump），正文换成一句认得出的话：真 dump 的 `text`
 *  开头就是那段预览，拿它当判据分不出「摊开了」和「还是那段预览」。 */
const DETAIL = { ...fixtures.draftDetail, text: "（这一稿的全文，比预览长得多。）" };

const draftRoutes = (drafts: DraftCandidateView[]) => [
  { match: /\/drafts\/[^/]+$/, body: DETAIL },
  { match: /\/drafts(\?|$)/, body: listOf(drafts) },
];

/** 屏幕上按出现次序排的那几个「第 N 稿」。 */
function labelsOnScreen(): string[] {
  return [...document.querySelectorAll(".draft-card b")].map((el) => el.textContent ?? "");
}

/** 让**稿子那一格**量到一个宽度（jsdom 不排版，`clientWidth` 恒为 0）。
 *
 *  **只给那一格，不给整个 `HTMLElement.prototype`**：三栏骨架自己也照 `clientWidth`
 *  夹一次列宽，一刀切下去会把右栏从 400 夹到 342，于是「三栏没被挤动」那一节量的
 *  就不是这块屏幕的影响，而是这个 stub 的影响。 */
function widthIs(px: number) {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList?.contains("draft-cards") ? px : 0;
    },
  });
}

const say = () => screen.getByRole("textbox", { name: "跟写作助手说" });

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
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
});

afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  window.location.hash = "";
  localStorage.clear();
});

// ══════════════════════════════════════════════════════════════════════════
// 一、三档摆的是**同一份**数据
// ══════════════════════════════════════════════════════════════════════════

describe("三档：同一份数据的三种排布", () => {
  // **三条断言的期望是同一个 `ORDER`，而它是从同一份 `THREE` 推出来的**——
  // 「三档摆的是同一份数据」这句话在这儿是可证的，不是一句说明。

  it("窄档（默认）：一稿一张卡，逐条、按后端的次序", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(THREE),
    ]);
    await runTurn();
    await screen.findByText(ORDER[2]);
    expect(document.querySelector(".draft-cards.wide")).toBeNull();
    expect(labelsOnScreen()).toEqual(ORDER);
  });

  it("宽档（作者把中栏拖开了）：还是那几稿，还是那个次序，只有排布变了", async () => {
    widthIs(3 * 400);
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(THREE),
    ]);
    await runTurn();
    await screen.findByText(ORDER[2]);
    expect(document.querySelector(".draft-cards.wide")).not.toBeNull();
    expect(labelsOnScreen()).toEqual(ORDER);
  });

  it("并排比那一页（另一个标签页）：同上 —— **少一稿或者换个次序都会红**", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    renderWithApi(<DraftCompare chapter={2} />, draftRoutes(THREE));
    await screen.findByText(ORDER[2]);
    expect(labelsOnScreen()).toEqual(ORDER);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 二、推荐位：**后端给的那个动作**，不是屏幕上算出来的
// ══════════════════════════════════════════════════════════════════════════

describe("推荐位", () => {
  it("摊开的是**进过书的那一版**，哪怕它又短、编号又小、还夹在中间", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(THREE),
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn();

    // 摊开的恰好一版，而且是 `landed` 那一版（`第 2 稿`）。
    await screen.findByText(DETAIL.text);
    expect(screen.getAllByText(DETAIL.text)).toHaveLength(1);
    const opened = document.querySelector(".draft-card.open b")?.textContent;
    expect(opened).toBe("第 2 稿");

    // 而且**只为那一版取过全文**：另外两版一个字节都没下载。
    const pulled = spy.mock.calls
      .map((call) => String(call[0]))
      .filter((u) => /\/drafts\/[^/?]+$/.test(u));
    expect(pulled).toHaveLength(1);
    expect(pulled[0]).toContain(encodeURIComponent("draft:ID72"));
  });

  it("换一版进书，摊开的跟着换 —— **判据在后端那一列上，不在位置上**", async () => {
    const moved = [
      variant({ id: "draft:ID71", ordinal: 7, units: 3100 }),
      variant({ id: "draft:ID72", ordinal: 2, units: 900 }),
      variant({ id: "draft:ID73", ordinal: 5, units: 2600, landed: true }),
    ];
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(moved) },
      ...draftRoutes(moved),
    ]);
    await runTurn();
    await screen.findByText(DETAIL.text);
    expect(document.querySelector(".draft-card.open b")?.textContent).toBe("第 5 稿");
  });

  it("**一版都没落盘：一版都不摊开，也不摆一句「建议用哪一版」**", async () => {
    // 后端不排名、不打分（ADR 0005），这一层更不许——它手上只有一段 120 字的开头。
    // 一批都没落盘时两边都不挑（同 `AmbiguousName`：两个方向都贵就摊开，绝不挑）。
    const none = THREE.map((d) => ({ ...d, landed: false }));
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(none) },
      ...draftRoutes(none),
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn();
    await screen.findByText("第 5 稿");

    expect(document.querySelectorAll(".draft-card.open")).toHaveLength(0);
    expect(
      spy.mock.calls.map((c) => String(c[0])).filter((u) => /\/drafts\/[^/?]+$/.test(u)),
    ).toEqual([]);
    // 屏幕上没有任何一句在替作者拿主意。
    expect(screenText()).not.toMatch(/推荐|建议用|最好的一版|最佳|挑这版/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 三、形状网：**兜底那几支**（正常数据下永远不亮）
// ══════════════════════════════════════════════════════════════════════════

describe("兜底那几支上一个研发术语都没有", () => {
  it("**自守卫**：这批数据里那个内部标识真的是网认得的那种形状", () => {
    // 没有它，下面每一条「屏幕上没有内部标识」都可能只是因为那批数据里根本没有
    // 一个咬得住的东西——这个仓库为「一张什么都咬不到却一直绿着的网」吃过三次亏。
    expect(rawIds(THREE[0].id)).toEqual(["draft:ID71"]);
  });

  it("摊开一稿，那一整章**还在路上**的时候", async () => {
    // 那条路由挂在原地不返回：这就是作者点「展开」之后看见的第一帧。
    const never = new Promise<never>(() => {});
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      { match: /\/drafts\/[^/]+$/, body: () => never },
      { match: /\/drafts(\?|$)/, body: listOf(THREE) },
    ]);
    await runTurn();
    await screen.findByText(/正在把这一稿读出来/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("摊开一稿，那一整章**读不出来**的时候", async () => {
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      // 真后端在这一档回的是 `{"detail":{"error":"draft_not_found","draft_id":…}}`
      // ——那两个词是 snake_case，形状网当场会咬住。**所以这一条同时在验
      // 「后端那句诊断没有被原样端上屏」。**
      {
        match: /\/drafts\/[^/]+$/,
        status: 404,
        body: { detail: { error: "draft_not_found", draft_id: "draft:ID72" } },
      },
      { match: /\/drafts(\?|$)/, body: listOf(THREE) },
    ]);
    await runTurn();
    await screen.findByText(/这一稿没读出来/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("并排比那一页：这一章桌上**是空的**", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
      { match: /\/drafts(\?|$)/, body: listOf([]) },
    ]);
    await screen.findByText(/桌上没有稿子/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("并排比那一页：**列不出来**（那条路由 500）", async () => {
    writeCompareHandoff({ book: "project:ID1", chapter: 2 });
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/drafts(\?|$)/, status: 500, body: { detail: "internal_server_error" } },
    ]);
    await screen.findByText(/没读出来/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("并排比那一页：**连不上工作台**（书都列不出来）", async () => {
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/api\/projects$/, status: 503, body: { detail: "service_unavailable" } },
    ]);
    await screen.findByText(/连不上工作台/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("并排比那一页：**不知道是哪本书**（好几本书 + 没有那次交接）", async () => {
    renderWithApi(<DraftCompare chapter={2} />, [
      { match: /\/api\/projects$/, body: fixtures.projectsTwo },
    ]);
    await screen.findByText(/没说清是哪本书/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("对话面板上那条「还摆着几稿」的入口", async () => {
    // 它挂在面板头上，`title` 和链接文字都算屏幕（`screenText()` 连 `title` 一起收）。
    renderWithApi(<ChatPanel />, draftRoutes(THREE));
    await screen.findByText(/还摆着 3 稿/);
    expect(devTerms(screenText())).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 四、三栏骨架：中栏里长出几列并排的正文，两侧不许跟着动
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

  it("**逐字节钉那两个数**（三稿摆着、其中一版摊开着）", async () => {
    // 「前后相等」单独一条是骗得过的：两边一起错成同一个值它也绿。所以这儿钉的是
    // `DEFAULT_LEFT` / `DEFAULT_RIGHT` 本身。
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(THREE),
    ]);
    await runTurn();
    await screen.findByText(DETAIL.text);
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(EXPECTED);
    // 中栏内部那一行（正文 / 写作助手对半分）也没被顶开。
    const split = document.querySelector(".center-split") as HTMLElement;
    expect(split.style.gridTemplateColumns).toContain("50fr");
  });

  it("这几稿一个都没有的时候，那一行逐字节还是同一串", async () => {
    renderWithApi(shell(), [{ match: /\/drafts(\?|$)/, body: listOf([]) }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(EXPECTED);
  });

  it("**宽档三列并排**、每一列都摊着一整章正文的时候，那一行仍然逐字节是它", async () => {
    // 这是这块屏幕**最撑**的一档：中栏里三列并排，每一列一整章。
    widthIs(3 * 400);
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      ...draftRoutes(THREE),
    ]);
    await runTurn();
    await waitFor(() => expect(screen.getAllByText(DETAIL.text)).toHaveLength(3));
    expect(document.querySelector(".draft-cards.wide")).not.toBeNull();
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(EXPECTED);
  });
});
