import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fixtures, stubFetch } from "../test/harness";
import { ChatRules } from "./ChatRules";

// **作者的规矩**摆出来、点得掉（[ADR 0023](docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。
//
// 这块面板是那条退路的另一半：规矩是**模型从作者随口一句话里提炼**出来的，ADR 自己把
// 「它会判错」写在代价里，而押的退路只有一句——**看得见 + 能取消，不是事前确认**。
//
// 三件这份文件必须量住的事：
//
// 1. **点 × 报的是那条规矩的 `seq`，不是它在屏幕上排第几。** 两者在真夹具里就不一样
//    （第一行的 `seq` 是 10），照下标发就会撤到别的消息上。
// 2. **界面不许自己把那一行抹掉。** 同一条规矩可能被记过好几遍，撤销在引擎侧按身份撤掉
//    每一份；那件事出错时后端照样 200，**唯一能看出来的地方就是重取回来它还在**。
//    乐观更新会把这个唯一的观测点关掉。
// 3. **三种空各说各的**（§10 约束 8）：没定过 / 定过都不作数了 / 根本没读出来。
//
// 喂进来的每一个字节都来自 `api.json`（真 app dump，`tests/test_frontend_contract.py`
// 每次重新 dump 对比）——手写夹具等于两份手写的东西互相验证。

const PID = "project:ID1";
const CHAT = "chat_session:ID47";

/** `renderWithApi` 会把 fetch 换掉，这里自己装一遍并多记一份「请求过哪些 URL」：
 *  「点的是哪一条」只有 URL 看得见。 */
function renderSpying(ui: ReactElement, extra: Parameters<typeof stubFetch>[0] = []) {
  stubFetch(extra);
  const inner = globalThis.fetch;
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), method: (init?.method ?? "GET").toUpperCase() });
    return inner(url as never, init);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { calls, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

const panel = () => <ChatRules pid={PID} chatId={CHAT} chapter={2} />;

/** 真 dump 那两条：一条章级（说到第二遍升上来的）、一条批级。 */
const [WIDE, BATCH] = fixtures.chatRules.rules;

// ══════════════════════════════════════════════════════════════════════════
// 一、摆出来：一行一条，那句话 + 它管到哪儿
// ══════════════════════════════════════════════════════════════════════════

describe("摆出来", () => {
  it("两条各说各的：一条管整章，一条只管眼下这一轮", async () => {
    renderSpying(panel());
    await screen.findByText(WIDE.text);
    expect(screen.getByText(WIDE.scope)).toBeTruthy();
    expect(screen.getByText(BATCH.text)).toBeTruthy();
    expect(screen.getByText(BATCH.scope)).toBeTruthy();
    // **措辞不是这一层编的**：两句都逐字来自后端（`api/chat.py::_rule_view`）。
    expect(WIDE.scope).not.toBe(BATCH.scope);
  });

  it("那个 `seq` 一个字符都不上屏 —— 它是历史下标，不是给人看的编号", async () => {
    renderSpying(panel());
    await screen.findByText(WIDE.text);
    const shown = document.body.textContent ?? "";
    for (const rule of fixtures.chatRules.rules) {
      expect(shown).not.toContain(String(rule.seq));
    }
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 二、🔴 点掉一条
// ══════════════════════════════════════════════════════════════════════════

describe("点掉一条", () => {
  it("点 × 先问一句，「算了」什么都不发", async () => {
    const user = userEvent.setup();
    const { calls } = renderSpying(panel());
    await screen.findByText(WIDE.text);

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    // **那句话得说清他在做什么**：取消在库里是往历史里追加一条记录，不是删掉一行，
    // 而作者看到的是「这一行没了」——两者的差别他永远看不到。
    await screen.findByText(/不再管着你的稿子/);

    await user.click(screen.getByRole("button", { name: "算了" }));
    expect(screen.queryByText(/不再管着你的稿子/)).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toEqual([]);
  });

  it("🔴 确认之后报的是**那条规矩的 `seq`**，不是它在屏幕上排第几", async () => {
    // 真夹具里第一行的 `seq` 是 10。照下标（0）发的话，撤掉的是另一条消息——
    // 而那条路由会说「那一条不是你定下的规矩」，作者看到的是一句莫名其妙的拒绝。
    expect(WIDE.seq).not.toBe(0);

    const user = userEvent.setup();
    const { calls } = renderSpying(panel());
    await screen.findByText(WIDE.text);

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));

    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const deleted = calls.find((c) => c.method === "DELETE")!;
    expect(deleted.url).toContain(`/rules/${WIDE.seq}`);
    expect(deleted.url).not.toContain("/rules/0");
  });

  it("🔴 服务端没真的撤掉时，界面**不许**装作撤掉了", async () => {
    // 这条断言看起来很怪，而它是这个按钮的全部价值所在。
    //
    // 同一条规矩可能被记过好几遍，读端只摆最后那一条；撤销在引擎侧按**身份**撤掉每一份。
    // 那件事写错的时候（只划掉作者点的那个下标），后端照样 200、回执照样 `revoked: true`
    // ——**唯一能看出来的地方就是重取回来它还在**。界面要是先把那一行抹掉，
    // 这个唯一的观测点就被关掉了，而作者要到下次打开这块面板才发现规矩还在。
    const user = userEvent.setup();
    const { calls } = renderSpying(panel());
    await screen.findByText(WIDE.text);
    const reads = () => calls.filter((c) => c.method === "GET" && c.url.includes("/rules?")).length;
    const before = reads();

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));

    // **等它真的重取了一次**：清单是从服务端重新问出来的，不是本地算出来的。
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    // stub 那一头的清单一个字没变（正是「撤销没生效」的形态），所以它还在屏幕上。
    expect(screen.getByText(WIDE.text)).toBeTruthy();
  });

  it("正在跑一轮时后端那句拒绝原样上屏，这一层不另写一句", async () => {
    // 跑到一半点 × 会把那一轮的乐观并发闸撞红（作者已经付过钱的那一轮当场死掉），
    // 所以后端先拒。**那句话是后端写的**，这一层照抄。
    const user = userEvent.setup();
    const busy = "这段对话正在跑，跑完（或者按「停」）再取消这条规矩。";
    renderSpying(panel(), [
      {
        method: "DELETE",
        match: /\/rules\/\d+$/,
        status: 409,
        body: { detail: { error: "chat_busy", chat_id: CHAT, message: busy } },
      },
    ]);
    await screen.findByText(WIDE.text);

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));

    await screen.findByText(busy);
  });

  it("这一下根本没送出去时也要说一句，不许一声不吭", async () => {
    const user = userEvent.setup();
    renderSpying(panel(), [
      { method: "DELETE", match: /\/rules\/\d+$/, status: 500, body: {} },
    ]);
    await screen.findByText(WIDE.text);

    await user.click(screen.getByRole("button", { name: `取消这条规矩：${WIDE.text}` }));
    await user.click(screen.getByRole("button", { name: "取消它" }));

    // 一个字都不解释「为什么」（§10 约束 8：不知道就说不知道）。
    await screen.findByText(/没能取消/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 三、零带着理由（§10 约束 8）
// ══════════════════════════════════════════════════════════════════════════

describe("三种空各说各的", () => {
  it("一条都没定过：说它是怎么长出来的", async () => {
    renderSpying(panel(), [{ match: /\/rules\?/, body: fixtures.chatRulesNone }]);
    await screen.findByText(/还没有规矩在管着/);
    // **不许说成「定过、都过期了」**：那会让他去找一条从来不存在的规矩。
    expect(document.body.textContent).not.toMatch(/不作数/);
  });

  it("定过、这会儿都不作数了：把「规矩只管那一章」讲出来", async () => {
    // 不讲的话，规矩消失在作者眼里就是系统忘事——他会去重复一件他以为已经交代过的事。
    renderSpying(panel(), [{ match: /\/rules\?/, body: fixtures.chatRulesExpired }]);
    await screen.findByText(/一条都不作数了/);
    expect(screen.getByText(new RegExp(`${fixtures.chatRulesExpired.expired} 条规矩`))).toBeTruthy();
  });

  it("根本没读出来：**不许**说成「这一章没有规矩」", async () => {
    // 读不出来和一条都没有，下一步动作不同；而后者在读失败时是一句它不知道真假的话。
    renderSpying(panel(), [{ match: /\/rules\?/, status: 500, body: {} }]);
    await screen.findByText(/没读出来/);
    expect(document.body.textContent).not.toMatch(/还没有规矩在管着/);
  });

  it("只有一条时照样摆得出来（清单不为空的最小形态）", async () => {
    const one = { ...fixtures.chatRules, rules: [fixtures.chatRules.rules[0]] };
    renderSpying(panel(), [{ match: /\/rules\?/, body: one }]);
    await screen.findByText(WIDE.text);
    expect(screen.queryByText(BATCH.text)).toBeNull();
    expect(document.body.textContent).not.toMatch(/还没有规矩在管着/);
  });
});
