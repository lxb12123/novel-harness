import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, ROUND_DONE } from "../test/harness";
import { rawIds, screenText } from "../test/screenGuard";
import type { DraftCandidateView } from "../api/types";
import { COLUMN_MIN_PX } from "../drafts";
import { readCompareHandoff } from "../route";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";

// 这一轮写出来的那几稿，**在对话面板里**（ADR 0022 的桌子；入口 2026-09-12 起按
// ADR 0048：稿子写完直接写进那一章，第三档新标签页在 `DraftCompare.test.tsx`）。
//
// 这份文件量的是两种稿子各自长什么样：
//
//   写进去的 → **一行**（第几稿 · 字数 · 已写入 + 自述），正文在左边，这儿不摊第二份
//   没写进去的（作者中途改过那一章 / 那一章还不存在 / 按停砍断）→ 一张卡，
//     窄（默认）一稿一张、全收着；宽（拖出来的）几列并排、各自一个滚轮
//
// 喂的每一个字节都来自真 dump（`__fixtures__/api.json` 的 `chatTurn` / `drafts` /
// `draftDetail`）。**变体只改「第几稿 / 落没落盘 / 有没有自述」**——真 dump 那一轮
// 写了一稿且写进去了。

const REAL = fixtures.drafts.drafts[0] as DraftCandidateView;
const variant = (over: Partial<DraftCandidateView>): DraftCandidateView => ({ ...REAL, ...over });

/** 三稿都写进了那一章：一稿有自述、一稿**什么都没说**（自述是空串）。 */
const THREE: DraftCandidateView[] = [
  variant({ id: "draft:ID43", ordinal: 1, landed: true }),
  variant({ id: "draft:ID44", ordinal: 2, landed: true }),
  variant({ id: "draft:ID45", ordinal: 3, landed: true, note: "" }),
];

/** 三稿一稿都没写进去（作者中途改过那一章那一档）：这时它们才是卡。 */
const PENDING: DraftCandidateView[] = THREE.map((d) => ({ ...d, landed: false }));

const turnWith = (drafts: DraftCandidateView[]) => ({ ...fixtures.chatTurn, drafts });

/** 摊开一稿时后端给的那一份（真 dump），**正文换成一句认得出的话**：
 *  真 dump 的 `text` 和 `preview` 开头一模一样（预览就是它的前 120 字），
 *  拿正文的开头当判据分不出「摊开了」和「还是那段预览」。 */
const DETAIL = { ...fixtures.draftDetail, text: "（这一稿的全文，比预览长得多。）" };

const say = () => screen.getByRole("textbox", { name: "输入消息" });

/** 跑一轮，让那几稿摆出来。 */
async function runTurn(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(fixtures.chatDetail.messages[0].text);
  await user.type(say(), "这一场写三个版本我挑");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText(ROUND_DONE);
}

/** 摊开某一稿要打的那条路由（`/drafts/{id}`）—— 列表那条不带正文。 */
const fullTextCalls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((call) => String(call[0])).filter((u) => /\/drafts\/[^/?]+$/.test(u));

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
  // 宽档那几条把它撑起来过；不还回去，后面的测试量到的宽度就是编的。
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
});

/** 让这块屏幕量到一个宽度。**真浏览器里这个数来自作者拖分隔条**，
 *  jsdom 不排版（`clientWidth` 恒为 0），所以只能这么给。 */
function widthIs(px: number) {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => px,
  });
}

describe("写进那一章的稿子：一行，不摊正文（ADR 0048）", () => {
  it("每一稿一行：第几稿 · 字数 · 已写入第几章；**那一串内部标识一个字符都不上屏**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn(user);

    expect(document.querySelectorAll(".draft-landed")).toHaveLength(3);
    expect(screen.getByText("第 1 稿")).toBeInTheDocument();
    expect(screen.getAllByText(/已写入第 2 章/)).toHaveLength(3);
    // 正文在左边的编辑器里——这儿**一张卡都不画、一整章正文一个字节都不取**。
    expect(document.querySelector(".draft-card")).toBeNull();
    expect(screen.queryByRole("button", { name: /展开第/ })).toBeNull();
    await new Promise((r) => setTimeout(r, 40));
    expect(fullTextCalls(spy)).toEqual([]);
    expect(THREE[0].id).toMatch(/:/); // 探针：喂进去的真是那个形状
    expect(rawIds(screenText())).toEqual([]);
  });

  it("**说得出怎么退** —— 落盘不问作者（ADR 0021），那就欠他「改得掉」这一半", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) }]);
    await runTurn(user);
    await screen.findByText(/如需撤销.*历史/);
  });

  it("**自述是空的就什么都不画** —— 替它编一句「这一版更冷」正是引擎不许做的事", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) }]);
    await runTurn(user);

    expect(THREE[2].note).toBe(""); // 探针
    // 有自述的那两稿各一句，没有的那一稿一句都没有。
    expect(document.querySelectorAll(".draft-landed-note")).toHaveLength(2);
  });
});

describe("没写进去的稿子：一张卡，窄档默认一稿一张", () => {
  it("三稿都在，屏幕上说的是「第几稿」，**那一串内部标识一个字符都不上屏**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) }]);
    await runTurn(user);

    expect(screen.getByText("第 1 稿")).toBeInTheDocument();
    expect(screen.getByText("第 2 稿")).toBeInTheDocument();
    expect(screen.getByText("第 3 稿")).toBeInTheDocument();
    expect(PENDING[0].id).toMatch(/:/); // 探针：喂进去的真是那个形状
    expect(rawIds(screenText())).toEqual([]);
  });

  it("**一版都不摊开**：后端没挑，这儿也不挑；一整章正文一个字节都没取", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn(user);

    expect(document.querySelectorAll(".draft-preview")).toHaveLength(3);
    await new Promise((r) => setTimeout(r, 40));
    expect(fullTextCalls(spy)).toEqual([]);
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(screen.queryByText(/如需撤销/)).toBeNull();
  });

  it("点「展开」才去取那一整章 —— 取回来的是全文，不是那段预览", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn(user);

    await user.click(screen.getByRole("button", { name: "展开第 3 稿" }));

    await screen.findByText(DETAIL.text);
    await waitFor(() => expect(fullTextCalls(spy)).toHaveLength(1));
    expect(fullTextCalls(spy)[0]).toContain(encodeURIComponent(PENDING[2].id));
    // 收得回去（作者读完一版接着挑下一版，三版全摊着比不了）。
    await user.click(screen.getByRole("button", { name: "收起第 3 稿" }));
    expect(screen.queryByText(DETAIL.text)).toBeNull();
  });

  it("**自述是空的就什么都不画**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) }]);
    await runTurn(user);

    expect(PENDING[2].note).toBe(""); // 探针
    expect(document.querySelectorAll(".draft-note")).toHaveLength(2);
  });
});

describe("被砍断的那一稿：屏幕必须说它没写完", () => {
  // 作者按「停」时已经写出来的那部分留了下来（迁移 010）。**它和一份写完的稿子在这块
  // 屏幕上长得一模一样**：预览一样、全文一样、字数只是少一点。不说的话，作者会以为
  // 写作模型就写成了这样——而这一整条链上没有第二个地方能告诉他。
  const STOPPED = "已按「停」中断，稿件未完成，止于此处。";

  it("**那句标注画出来了**，而且照抄后端那一句", async () => {
    const user = userEvent.setup();
    const half = [variant({ id: "draft:ID46", ordinal: 1, landed: false, stopped_reason: STOPPED })];
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(half) }]);
    await runTurn(user);

    expect(screen.getByText(STOPPED)).toBeInTheDocument();
    // **措辞的唯一出处在后端**：这一层不许按 `stopped_reason` 非空自己造一句。
    expect(document.querySelector(".draft-stopped")?.textContent).toBe(STOPPED);
  });

  it("**写完的那几稿身上一个字都不多**（自守卫：恒画等于没画）", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) }]);
    await runTurn(user);

    expect(PENDING.every((d) => d.stopped_reason === "")).toBe(true); // 探针：真 dump 就是空的
    expect(document.querySelector(".draft-stopped")).toBeNull();
  });

  it("它画在正文**前面** —— 它改变的是后面那段字该怎么读", async () => {
    const user = userEvent.setup();
    const half = [variant({ id: "draft:ID47", ordinal: 1, landed: false, stopped_reason: STOPPED })];
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(half) }]);
    await runTurn(user);

    const card = document.querySelector(".draft-card")!;
    const order = [...card.children].map((el) => el.className);
    expect(order.indexOf("draft-stopped")).toBeLessThan(order.indexOf("draft-preview"));
  });
});

describe("宽档：拖到一定宽度就并排", () => {
  it("三列并排、各自一个滚轮，全文都摊着 —— **拖到这个宽度就是他要并排读**", async () => {
    widthIs(3 * COLUMN_MIN_PX + 40);
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) },
      { match: /\/drafts\/[^/]+$/, body: DETAIL },
    ]);
    const spy = vi.spyOn(globalThis, "fetch");
    await runTurn(user);

    await waitFor(() => expect(fullTextCalls(spy)).toHaveLength(3));
    expect(document.querySelector(".draft-cards.wide")).not.toBeNull();
    expect(document.querySelectorAll(".draft-preview")).toHaveLength(0);
    // 并排的时候没有「展开」——它已经摊开了，那颗按钮只会是一句废话。
    expect(screen.queryByRole("button", { name: /展开第/ })).toBeNull();
  });

  it("宽度不够就还是窄档 —— 三条读不下去的竖缝比摞着更糟", async () => {
    widthIs(3 * COLUMN_MIN_PX - 1);
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(PENDING) }]);
    await runTurn(user);

    expect(document.querySelector(".draft-cards.wide")).toBeNull();
  });
});

describe("第三档的入口：那条链接", () => {
  it("链接指向同一个应用的另一条路由，**地址里只有章号**", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) }]);
    await runTurn(user);

    const link = screen.getByRole("link", { name: /并排查看第 2 章各稿/ });
    expect(link).toHaveAttribute("href", "#/compare/2");
    expect(link).toHaveAttribute("target", "_blank");
    expect(rawIds(link.getAttribute("href") ?? "")).toEqual([]);
  });

  it("点它的时候把「哪本书的第几章」留给那个标签页（地址里没有书）", async () => {
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />, [{ method: "POST", match: /\/turn\/events$/, body: turnWith(THREE) }]);
    await runTurn(user);
    expect(readCompareHandoff()).toBeNull();

    await user.click(screen.getByRole("link", { name: /并排查看第 2 章各稿/ }));

    expect(readCompareHandoff()).toEqual({ book: "project:ID1", chapter: 2 });
  });

  it("**回执被顶掉之后还找得回它们**：面板头上那条「还摆着几稿」", async () => {
    // 候选既不在正文里也不在对话里（ADR 0022）——没有这条入口，作者关掉那一轮的回执
    // 就再也看不到它们了。零的时候一个字都不画。
    renderWithApi(<ChatPanel />);
    const link = await screen.findByRole("link", { name: /本章 1 稿/ });
    expect(link).toHaveAttribute("href", "#/compare/2");
  });

  it("这一章桌上是空的时候，那条入口一个字都不画", async () => {
    renderWithApi(<ChatPanel />, [{ match: /\/drafts(\?|$)/, body: { drafts: [] } }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByRole("link", { name: /还摆着/ })).toBeNull();
  });
});
