import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { refusalText } from "../chat";
import { fixtures, stubFetch } from "../test/harness";
import { devTerms, engineWords, machineWords, rawIds, screenText } from "../test/screenGuard";
import { DEFAULT_LEFT, DEFAULT_RIGHT, DIVIDER_PX, KEY_PCT_STEP } from "../layout";
import { useCoords } from "../store";
import { ChatPanel } from "./ChatPanel";
import { SplitPanes } from "./SplitPanes";

// **对抗性复核：对话面板（3.5）。**
//
// 这一块屏幕是**持久化对话第一次上屏**，所以它错的方式和别的面板不一样：
// ADR 0019 边界一说得很清楚——「一旦某个工具把 `Node` 交出去过，那段秘密就已经在作者的
// 持久化对话里了，改代码不会把它删掉」。屏幕是那条链的最后一厘米。
//
// 五个问题，一节一个：
//
// 1. 秘密内容会不会上屏（含**前端自己去别处把原文捞回来**这一种）
// 2. 研发术语（**兜底那几支**：空的 / 出错 / 正在跑 / 被停 / 删除确认）
// 3. 三栏骨架有没有被这块新屏幕破坏
// 4. 「停」诚不诚实（`stopped=false` 不是失败；删不掉不是「删除失败」）
// 5. 断在半路的那一段在列表上认不认得出来
//
// ── 这份文件里手写的那几个错误响应，为什么不算违反「不许手写夹具」 ─────────────
//
// 契约夹具（`__fixtures__/api.json`）dump 的是**成功**响应，坏形态按定义不在里面
// （同 `DevTerms.guard.test.tsx` 的那段说明）。但手写坏形态有一个真实风险：
// **按自己以为的形状写，于是测的是一个不存在的后端。** 所以下面每一个错误响应体
// 都在 2026-08-11 拿真 app（`TestClient` + 真 SQLite）打过一遍，形状原样抄回来：
//
//   POST …/chats/{不存在}/turn → 404 {"detail":{"error":"chat_not_found","chat_id":…}}
//   DELETE …/chats/{不存在}    → 404 同上
//   POST …/chats/{不存在}/stop → 404 同上
//   POST /api/projects/{不存在}/chats → 404 {"detail":{"error":"project_not_found",…}}
//
// **注意那三个 404 里一个 `message` 都没有。** `ApiError` 在没有 `message` 时退回
// `body.error`（`api/client.ts`，`correctionError.ts` 那段注释把这条缝写在纸上了），
// 所以「直接渲染 `error.message`」= 把 `chat_not_found` 摆到小说作者脸上。

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
});

/** 真后端在「这段对话不在了」时的整个响应体（上面抄的那一份）。 */
const CHAT_GONE = { detail: { error: "chat_not_found", chat_id: "chat_session:ID42" } };
/** 真后端在「这本书不在了」时的整个响应体。 */
const PROJECT_GONE = { detail: { error: "project_not_found", project_id: "project:ID1" } };

/** 一个能卡住的响应：中间那一帧（还在跑）快到测不出来，得能停在原地。 */
function gated<T>(value: T) {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  return { handler: async () => (await gate, value), release: () => release() };
}

type Extra = Parameters<typeof stubFetch>[0];

/** 同 `renderWithApi`，但**从第一帧起**就盯着 fetch。
 *
 *  `renderWithApi` 先 stub 再 render，等它返回的时候第一批 GET 已经打完了——
 *  而「这块屏幕有没有自己去别处捞原文」恰恰要看**全部**请求，漏掉最早那几条
 *  等于只验了后半段。 */
function renderWatched(ui: ReactElement, extra: Extra = []) {
  stubFetch(extra);
  const spy = vi.spyOn(globalThis, "fetch");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
  return { urls: () => spy.mock.calls.map(([url]) => String(url)) };
}

const say = () => screen.getByRole("textbox", { name: "跟写作助手说" });
const sendBtn = () => screen.getByRole("button", { name: "发送" });
const errBoxes = () => [...document.querySelectorAll(".err-box")].map((e) => e.textContent ?? "");

/** 一屏静下来（几条 query 各自落地 + 重取）之后再扫。 */
async function settle() {
  await new Promise((r) => setTimeout(r, 60));
}

// ══════════════════════════════════════════════════════════════════════════
// 1. 秘密内容 —— 这是持久化对话第一次上屏
// ══════════════════════════════════════════════════════════════════════════

/** 作者写在秘密节点上的那几样东西的**真实形态**（ADR 0019 边界一点名的三个）：
 *  `NodeProps` 是 `extra="allow"`，`twist` / `plot_note` 原样穿过去；
 *  `SecretDetail.description` 是秘密正文本身。工具返回是 `model_dump_json()`
 *  出来的内部模型，所以它长这样——**带裸 id、带 snake_case 字段名**。 */
const POISON = JSON.stringify({
  node: {
    id: "secret:01J8XK5ZQ2",
    props: { twist: "萧决其实是前朝血脉", plot_note: "第 41 章由李管家亲口说破" },
  },
  secret: { description: "萧决身上那道胎记是前朝皇族的记认", valid_from: 41 },
});

describe("秘密内容：带毒的会话详情", () => {
  /** 后端的投影（`api/chat.py::_visible`）今天只放两种说话人出来，工具返回一条都不发。
   *  **这一条验的是「万一它变了，屏幕接不接」**——接了就是不可回收的那一种错。 */
  const poisoned = {
    ...fixtures.chatDetail,
    messages: [
      ...fixtures.chatDetail.messages,
      { seq: 4, speaker: "tool", text: POISON },
      { seq: 5, speaker: "system", text: "must_not_reveal: 血脉秘密" },
    ],
  };

  it("**投影之外的说话人一条都不渲染** —— 认不出来就闭嘴，不是原样摆出去", async () => {
    renderWatched(<ChatPanel />, [{ match: /\/chats\/[^/]+$/, body: poisoned }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await settle();

    // 探针：喂进去的那份**真的**带毒（这条塌了说明毒药过期了，不是代码变干净了）。
    expect(machineWords(POISON)).toContain("plot_note");
    expect(rawIds(POISON)).toContain("secret:01J8XK5ZQ2");

    const screenNow = screenText();
    expect(screenNow).not.toContain("前朝血脉");
    expect(screenNow).not.toContain("前朝皇族");
    expect(screenNow).not.toContain("李管家亲口说破");
    expect(devTerms(screenNow)).toEqual([]);
  });

  it("**反向：认得出的那两种一句都不许被吃掉**（过度收窄一样是 bug）", async () => {
    renderWatched(<ChatPanel />, [{ match: /\/chats\/[^/]+$/, body: poisoned }]);
    // 作者说的那句、助手说的那句、以及助手嘴里的**显示名**（那是它该说的东西）。
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    expect(screen.getByText(fixtures.chatDetail.messages[1].text)).toBeInTheDocument();
    expect(fixtures.chatDetail.messages[1].text).toContain("血脉"); // 探针：显示名真在里头
    expect(screen.getAllByText("你").length).toBe(1);
    expect(screen.getAllByText("写作助手").length).toBeGreaterThan(0);
  });

  it("**收起来的条数也不许把它们算进去** —— 一个数不上不该存在的东西", async () => {
    // 「看更早的 N 条」如果按 canonical 的长度算，那个 N 里就含着两条永远点不开的东西：
    // 作者点开之后少两条，而少的正是被拦下来的那两条。
    renderWatched(<ChatPanel />, [{ match: /\/chats\/[^/]+$/, body: poisoned }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await settle();
    expect(screen.queryByText(/看更早的/)).toBeNull();
  });

  it("**面板不去别处捞原文** —— 它打的每一条都在 `/chats` 底下", async () => {
    const user = userEvent.setup();
    const watch = renderWatched(<ChatPanel />);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "这一章能说破血脉吗");
    await user.click(sendBtn());
    await screen.findByText(fixtures.chatTurn.message);
    await settle();

    const stray = watch.urls().filter((u) => !/\/chats(\/|\?|$)/.test(u));
    // 一轮跑完要让正文那一侧失效重取（ADR 0021 起草会写磁盘），但**失效不是取**：
    // 这块屏幕自己一条都不该去打。多出来的任何一条都要在这儿解释清楚。
    expect(stray).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. 研发术语 —— 兜底那几支（正常数据下永远不亮，正是它们躲过守卫的方式）
// ══════════════════════════════════════════════════════════════════════════

describe("研发术语：整块面板的兜底分支", () => {
  it("**自守卫：那个坑今天还在** —— `error.message` 在没话的时候就是那串码", () => {
    // 下面几条断言全都建立在这一件事上。哪天 `ApiError` 不再退回 `body.error` 了，
    // 这条会红 —— 那时该做的是回头看那几条断言还证不证明得了东西，而不是删掉它。
    expect(new ApiError(404, CHAT_GONE.detail).message).toBe("chat_not_found");
    expect(machineWords(new ApiError(404, PROJECT_GONE.detail).message)).toEqual([
      "project_not_found",
    ]);
    // 而后端**写了话**的那一档一个字都不许被换掉（`refusalText` 的另一半）。
    expect(refusalText(new ApiError(409, { error: "chat_busy", message: "先按「停」再删。" }), "兜底")).toBe(
      "先按「停」再删。",
    );
    expect(refusalText(new ApiError(404, CHAT_GONE.detail), "兜底")).toBe("兜底");
    expect(refusalText(null, "兜底")).toBeNull();
  });

  it("一段对话都还没有的时候", async () => {
    renderWatched(<ChatPanel />, [{ match: /\/chats$/, body: [] }]);
    await screen.findByText(/跟它说一句话就开始/);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await screen.findByText(/还没有说过话/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("这段对话读不出来的时候", async () => {
    renderWatched(<ChatPanel />, [
      { match: /\/chats\/[^/]+$/, status: 404, body: CHAT_GONE },
    ]);
    await screen.findByText(/这段对话没读出来/);
    await settle();
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**列表读不出来的时候不许说「还没有说过话」** —— 那是一句它不知道真假的话", async () => {
    // 静默返回空 = 屏幕上是一个「看起来很正常的空面板」，而作者三个月的对话可能都在。
    // §10 约束 8：什么都没发生的时候必须说得出为什么。
    const user = userEvent.setup();
    renderWatched(<ChatPanel />, [{ match: /\/chats$/, status: 500, body: PROJECT_GONE }]);
    await settle();
    expect(screen.queryByText(/跟它说一句话就开始/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    expect(screen.queryByText(/还没有说过话/)).toBeNull();
    expect(errBoxes().join("\n")).toMatch(/没读出来/);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**跑一轮被拒的时候**（真后端那一份 404 里一个 `message` 都没有）", async () => {
    const user = userEvent.setup();
    renderWatched(<ChatPanel />, [
      { method: "POST", match: /\/turn$/, status: 404, body: CHAT_GONE },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());

    await waitFor(() => expect(errBoxes()).not.toHaveLength(0));
    expect(errBoxes().join("\n")).not.toContain("chat_not_found");
    expect(devTerms(screenText())).toEqual([]);
    // 他打的字照旧还回输入框：这一档后端是在落库之前拒的。
    expect(say()).toHaveValue("问一句");
  });

  it("**删不掉的时候**（同上，只有码没有话）", async () => {
    const user = userEvent.setup();
    renderWatched(<ChatPanel />, [
      { method: "DELETE", match: /\/chats\//, status: 404, body: CHAT_GONE },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(
      screen.getByRole("button", { name: `删掉这段对话：${fixtures.chats[0].title}` }),
    );
    await user.click(screen.getByRole("button", { name: "删掉" }));

    await waitFor(() => expect(errBoxes()).not.toHaveLength(0));
    expect(errBoxes().join("\n")).not.toContain("chat_not_found");
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**开一段新的失败的时候**（这本书不在了）", async () => {
    const user = userEvent.setup();
    renderWatched(<ChatPanel />, [
      { method: "POST", match: /\/chats$/, status: 404, body: PROJECT_GONE },
      { match: /\/chats$/, body: [] },
    ]);
    await screen.findByText(/跟它说一句话就开始/);
    await user.type(say(), "第一句");
    await user.click(sendBtn());

    await waitFor(() => expect(errBoxes()).not.toHaveLength(0));
    expect(errBoxes().join("\n")).not.toContain("project_not_found");
    expect(devTerms(screenText())).toEqual([]);
  });

  it("正在跑 + 按了停 + 删除确认摊开，三样同时在屏幕上", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWatched(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");
    await user.click(screen.getByRole("button", { name: "停" }));
    await screen.findByText(fixtures.chatStopped.message);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(
      screen.getByRole("button", { name: `删掉这段对话：${fixtures.chats[0].title}` }),
    );
    await screen.findByRole("button", { name: "删掉" });

    expect(devTerms(screenText())).toEqual([]);
    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**探针：后端那句「模型没配好」今天带着三个环境变量名**（这一条不归前端修）", async () => {
    // 2026-08-11 拿真 app 打出来的原话（把 BYOK 那份配置弄成解不出来）：
    //   422 {"detail":"模型没配好：… —— 先去顶栏 ⚙「AI 设置」…，或设 NH_LLM_BASE_URL /
    //        NH_LLM_MODEL / NH_LLM_API_KEY。"}
    // 那三个词是 SCREAMING_SNAKE，形状网当场咬住。**但前端不许改后端那句话**
    // （`correctionError.ts` 那段：措辞的源只能有一个，再看见引擎的词去改后端）——
    // 所以这儿只把它钉成一条会过期的探针：后端改干净的那天这条红，那时删掉它。
    const said =
      "模型没配好：没填服务地址 —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，" +
      "或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。";
    expect(engineWords(said)).toEqual(["NH_LLM_BASE_URL", "NH_LLM_MODEL", "NH_LLM_API_KEY"]);

    // 而**有话的时候前端照说，一个字不改**（这一条是纪律，不是探针）。
    const user = userEvent.setup();
    renderWatched(<ChatPanel />, [
      { method: "POST", match: /\/turn$/, status: 422, body: { detail: said } },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "问一句");
    await user.click(sendBtn());
    await screen.findByText(said);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. 三栏骨架 —— 中间那块被切成两半，两侧不许跟着动
// ══════════════════════════════════════════════════════════════════════════

const shell = (chat?: ReactElement) => (
  <SplitPanes
    left={<div>左栏</div>}
    center={<div data-testid="manuscript">正文</div>}
    chat={chat}
    right={<div>右栏</div>}
  />
);

describe("三栏没被这块新屏幕破坏", () => {
  it("**左栏右栏一个像素不动** —— 而且钉的是那两个数，不只是「前后相等」", () => {
    // 「前后相等」单独一条是骗得过的：两边一起错成同一个值它也绿。
    const expected =
      `${DEFAULT_LEFT}px ${DIVIDER_PX}px minmax(0, 1fr) ${DIVIDER_PX}px ${DEFAULT_RIGHT}px`;
    render(shell());
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(expected);
    cleanup();
    // 换成**真的**对话面板（不是一个占位 div）：它自己是 grid / flex 的话可能反噬外层。
    renderWatched(shell(<ChatPanel />));
    expect(screen.getByRole("main").style.gridTemplateColumns).toBe(expected);
  });

  it("面板关着的时候正文占满中栏，不留一条空白", () => {
    render(shell());
    expect(document.querySelector(".center-split")).toBeNull();
    // 中栏就是 `<main>` 的第三个格子本人——中间没有多一层，也没有一个 0 宽的对话格。
    const main = screen.getByRole("main");
    expect(main.children[2]).toBe(screen.getByTestId("manuscript"));
    expect(screen.getAllByRole("separator")).toHaveLength(2);
  });

  it("对半分是**可拖的**，而且拖动改的只是中栏内部那一行", () => {
    renderWatched(shell(<ChatPanel />));
    const main = screen.getByRole("main");
    const before = main.style.gridTemplateColumns;
    const bar = screen.getByRole("separator", { name: "调整正文和写作助手的分界" });
    const split = document.querySelector(".center-split") as HTMLElement;

    expect(split.style.gridTemplateColumns).toContain("50fr");
    for (let i = 0; i < 3; i++) fireEvent.keyDown(bar, { key: "ArrowLeft" });
    expect(Number(bar.getAttribute("aria-valuenow"))).toBe(50 + 3 * KEY_PCT_STEP);
    expect(main.style.gridTemplateColumns).toBe(before); // 外面三栏一个像素没动
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 4. 「停」诚不诚实
// ══════════════════════════════════════════════════════════════════════════

describe("「停」", () => {
  it("`stopped=false` 不许长得像失败 —— 它是「这会儿本来就没在跑」", async () => {
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWatched(<ChatPanel />, [{ method: "POST", match: /\/turn$/, body: turn.handler }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");

    expect(fixtures.chatStopped.stopped).toBe(false); // 探针：真 dump 那份就是这一档
    await user.click(screen.getByRole("button", { name: "停" }));

    const note = await screen.findByText(fixtures.chatStopped.message);
    expect(note.className).not.toContain("err");
    expect(errBoxes()).toEqual([]);
    expect(screenText()).not.toMatch(/失败|出错|错误/);
    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("**按了停却一声不吭是不允许的** —— 那颗按钮读起来就是坏的", async () => {
    // 这一轮要跑好几分钟，「停」是作者唯一能插手的地方。它没送到而屏幕纹丝不动，
    // 作者只能再按一次、再等一次，而钱一直在花。
    const user = userEvent.setup();
    const turn = gated(fixtures.chatTurn);
    renderWatched(<ChatPanel />, [
      { method: "POST", match: /\/turn$/, body: turn.handler },
      { method: "POST", match: /\/stop$/, status: 404, body: CHAT_GONE },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");
    // **判据不能是「屏幕变了」**：那一条秒表每秒都在走，等一秒它自己就绿了。
    // 数的是「说给作者的那几行」——运行条上那个计时器不在里面。
    const said = () => document.querySelectorAll(".err-box, .chat-receipt-note").length;
    expect(said()).toBe(0);

    await user.click(screen.getByRole("button", { name: "停" }));

    await waitFor(() => expect(said()).toBeGreaterThan(0));
    expect(screenText()).not.toContain("chat_not_found");
    expect(devTerms(screenText())).toEqual([]);
    turn.release();
    await screen.findByText(fixtures.chatTurn.message);
  });

  it("正在跑的那一段删不掉：那句话是「先停下来再删」，不是「删除失败」", async () => {
    const user = userEvent.setup();
    // 真后端这一档**有话**（`{"error":"chat_busy","message":"这段对话正在跑，先按「停」再删。"}`），
    // 所以前端一个字都不许换 —— 兜底那句只在后端一句话都没写时才轮得到。
    renderWatched(<ChatPanel />, [
      {
        method: "DELETE",
        match: /\/chats\//,
        status: 409,
        body: {
          detail: {
            error: "chat_busy",
            chat_id: "chat_session:ID42",
            message: "这段对话正在跑，先按「停」再删。",
          },
        },
      },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(
      screen.getByRole("button", { name: `删掉这段对话：${fixtures.chats[0].title}` }),
    );
    await user.click(screen.getByRole("button", { name: "删掉" }));

    await screen.findByText("这段对话正在跑，先按「停」再删。");
    expect(screenText()).not.toMatch(/删除失败|没能删掉/);
    expect(devTerms(screenText())).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 5. 断在半路的那一段，和跑完的那些长得一样吗
// ══════════════════════════════════════════════════════════════════════════

describe("断在半路", () => {
  it("**并排放着的时候两行必须长得不一样**", async () => {
    const user = userEvent.setup();
    const done = { ...fixtures.chats[0], title: "跑完的那一段" };
    const half = {
      ...fixtures.chats[0],
      id: "chat_session:ID77",
      title: "断在半路的那一段",
      pending_lookups: 2,
    };
    renderWatched(<ChatPanel />, [{ match: /\/chats$/, body: [done, half] }]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.click(screen.getByRole("button", { name: "对话列表" }));

    const rows = [...document.querySelectorAll(".chat-session")] as HTMLElement[];
    expect(rows).toHaveLength(2);
    const [doneRow, halfRow] = rows;
    expect(within(halfRow).getByText(/上次断在半路/)).toBeInTheDocument();
    expect(within(doneRow).queryByText(/上次断在半路/)).toBeNull();
    // 一个数都不上屏：`pending_lookups` 是「还缺几个结果」，不是作者能理解的量。
    expect(halfRow.textContent).not.toMatch(/\b2\b/);
    expect(devTerms(screenText())).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 6. 一轮的产物属于**哪一段**对话
// ══════════════════════════════════════════════════════════════════════════

describe("跑着的时候切去看另一段", () => {
  it("**那一轮失败了，错误框不许画在另一段上**", async () => {
    // 秒表和回执已经钉在自己那一段上了（`runFor` / `receipt.chat`），**错误框漏了**。
    // 一轮跑好几分钟，「等的时候切过去看另一段」是常态：另一段上会长出一个红框，
    // 而那一段什么都没发生 —— 作者读到的是一句关于别处的话，长得像这儿的事实。
    const user = userEvent.setup();
    const other = { ...fixtures.chats[0], id: "chat_session:ID99", title: "另一段对话" };
    const boom = gated(CHAT_GONE);
    renderWatched(<ChatPanel />, [
      { match: /\/chats$/, body: [fixtures.chats[0], other] },
      { method: "POST", match: /\/turn$/, status: 404, body: boom.handler },
    ]);
    await screen.findByText(fixtures.chatDetail.messages[0].text);
    await user.type(say(), "跑一个");
    await user.click(sendBtn());
    await screen.findByRole("status");

    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: other.title }));
    boom.release();
    await waitFor(() => expect(say()).not.toBeDisabled());
    await settle();

    expect(errBoxes()).toEqual([]);

    // 切回去，那句话还在 —— 属于它的那一段看得见，别的段上看不见。
    await user.click(screen.getByRole("button", { name: "对话列表" }));
    await user.click(screen.getByRole("button", { name: fixtures.chats[0].title }));
    expect(errBoxes()).not.toHaveLength(0);
  });
});
