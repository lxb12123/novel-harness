import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi, sseFrames, turnStream, ROUND_DONE } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { applyTurnEvent, NO_PROGRESS } from "../chat";
import type { ChatTurnEvent } from "../api/types";
import { useCoords } from "../store";
import { useLiveDraft } from "../liveDraft";
import { CenterEditor } from "./CenterEditor";
import { ChatPanel } from "./ChatPanel";

// **对抗性复核：一轮跑到一半那块屏幕（ADR 0024 第二刀）。**
//
// `ChatPanel.test.tsx` 量的是「它在不在」，`DevTerms.guard.test.tsx` 量的是
// 「几块常见的屏幕上有没有研发术语」。这份文件只问那两份**都问不到**的四件事：
//
// 1. **回话区有没有一个编出来的节奏。** ADR 0024 的红字写死了：逐字看得见的只有稿子，
//    回话那一档今天不流式。所以这块屏幕上任何「一个字一个字冒出来的回话」都是假的
//    ——而作者会照那个节奏判断它卡没卡住。判据不是读源码，是**把屏幕的每一次变化录下来**
//    （`recordScreen`）：打字机唯一的痕迹是中间那些「开了头还没说完」的画面。
//    同一个探子在**左边的编辑器**（稿子逐字流进去的地方，ADR 0048）必须给出**相反**的
//    结论，那条反证就在下面一条。
// 2. **那份夹具真的是后端 dump 的字节吗，屏幕真的吃的是它吗。** 前者看形状
//    （`event:` / `data:` / 空行都在），后者靠**改一改它再看屏幕跟不跟着变**——
//    一份被组件忽略掉的夹具和一份手写夹具一样没有价值。
// 3. **每一种事件都被画过一遍。** 真 dump 那一轮只走到十种里的五种，剩下五种
//    （`reply_delta` / `draft_delta` / `draft_failed` / `asked_author` / `turn_stopped`）
//    在契约夹具里**一次都不会被渲染**——而它们身上的字段和前五种一样多。
//    没被扫到的分支等于没有守卫，这个仓库栽过。
// 4. **问题卡的兜底那一支。** `DevTerms.guard.test.tsx` 只喂了「有选项」那一档；
//    没有选项、以及「问了但回执丢了」那两支是另外两块屏幕，它们上面的字全是新写的。

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 2,
    chatOpen: true,
    chatId: null,
    page: "workbench",
  });
  // 下面那条反证把编辑器也挂上：它的 store 活得比一个测试长，先归零；CodeMirror 6
  // 在 jsdom 里还要这个测量 API（同 `CenterEditor.test.tsx`）。
  useLiveDraft.setState({ draft: null, inEditor: false, placed: null, saved: [] });
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

/** 后端真的吐出来的那一整轮（`tests/test_frontend_contract.py` 从真 app 抓的原始字节）。 */
const REAL = fixtures.chatTurnEvents;
/** 除回执之外的那几帧。卡住回执 = 把屏幕停在「还在跑」那一刻。 */
const MIDDLE = REAL.filter((f) => !f.startsWith("event: receipt"));

/** 真 dump 里某一种事件的**整个载荷**。手写一个「我以为的形状」正是这条缝原本的病。 */
function realEvent(kind: string): ChatTurnEvent {
  const frame = REAL.find((f) => f.includes(`"kind":"${kind}"`));
  if (!frame) throw new Error(`真 dump 里没有 ${kind} —— 这条测试的底子没了`);
  return JSON.parse(frame.split("\ndata: ")[1]) as ChatTurnEvent;
}

const say = () => screen.getByRole("textbox", { name: "输入消息" });
const send = () => screen.getByRole("button", { name: "发送" });

/** 打开面板 → 说一句 → 发送。三步各处都一样，抽出来免得每条测试抄一遍。 */
async function ask(user: ReturnType<typeof userEvent.setup>, text = "问一句") {
  await screen.findByText(fixtures.chatDetail.messages[0].text);
  await user.type(say(), text);
  await user.click(send());
}

// ══════════════════════════════════════════════════════════════════════════
// 一、回话区没有假打字机（ADR 0024 的红字）
// ══════════════════════════════════════════════════════════════════════════

/**
 * 屏幕**每一次变化**之后那块区域上的字。
 *
 * ── 为什么打字机只能这么测 ──────────────────────────────────────────────
 *
 * 「跑完之后整段话在不在」对一个打字机实现**一样是绿的**（它最后也会把整段吐完）。
 * 假打字机唯一的痕迹是**中间那些状态**：屏幕上出现过「风雪落」而整段还没出现。
 * 所以这儿盯的是变化过程，不是终态——而这不需要假时钟（假时钟会和
 * `userEvent` / `waitFor` 打架，实测整份文件一起挂住）。
 */
function recordScreen(): { seen: string[]; stop: () => void } {
  const seen: string[] = [];
  const observer = new MutationObserver(() => seen.push(document.body.textContent ?? ""));
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  return { seen, stop: () => observer.disconnect() };
}

describe("回话区：整段一次到位，没有一个编出来的节奏", () => {
  it("**它从来没出现过半句** —— 整段话是一次摆上去的", async () => {
    const user = userEvent.setup();
    const said = realEvent("reply_text");
    expect(said.text.length).toBeGreaterThan(10); // 探针：太短的话「逐字」和「整段」分不开
    const head = said.text.slice(0, 3);
    // **那一段话本身要卡在录像开始之后**：先放一帧真 dump 的开场把进度行支起来，
    // 开录，再放它 —— 不然整段早就落地了，录到的只有终态，而终态测不出打字机。
    const gates = [0, 1].map(() => {
      let open!: (frame: string) => void;
      return { open: (f: string) => open(f), frame: new Promise<string>((r) => (open = r)) };
    });
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [MIDDLE[0], ...gates.map((g) => g.frame)],
      },
    ]);
    await ask(user, "说一段");
    await screen.findByText(realEvent("tool_started").said_to_author);

    const tape = recordScreen();
    try {
      gates[0].open(sseFrames([{ event: "turn", data: said }])[0]);
      await waitFor(() =>
        expect(within(screen.getByRole("status")).getByText(said.text)).toBeInTheDocument(),
      );
    } finally {
      tape.stop();
    }

    // 探针：整段话真的在录到的画面里出现过（否则下面那条在空集上转）。
    expect(tape.seen.some((frame) => frame.includes(said.text))).toBe(true);
    // **一帧都不许是「开了头但还没说完」。**
    expect(tape.seen.filter((f) => f.includes(head) && !f.includes(said.text))).toEqual([]);

    gates[1].open(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });

  it("**稿子那一档真的逐字——在左边的编辑器里**（反证：上面那条测得出打字机）", async () => {
    // 没有这一条，上面那条在一个「事件流根本没接上」的实现里也是绿的 ——
    // 而这一条量的正是同一个探子在**真的逐字**面前会怎么响。稿子 2026-09-12 起流进
    // 左边的编辑器（ADR 0048，作者：「一定要在左边写」），所以编辑器也挂上；
    // 右边那一侧一个字都不许出现——那是反证的另一半。
    const user = userEvent.setup();
    // 编辑器开着的是第 1 章（契约夹具里正文那一份就是第 1 章的），流也对着第 1 章写。
    useCoords.setState({ chapter: 1 });
    const opened = { ...realEvent("draft_started"), chapter: 1 };
    const delta = (text: string) => ({
      ...opened,
      kind: "draft_delta" as const,
      text,
      said_to_author: "",
    });
    // **每一片单独卡住**：不卡的话两片会在同一个批次里落地，屏幕上直接出现整段——
    // 那时上面那条探子测不出差别，而这条反证也就没有意义了。
    const gates = [0, 1, 2].map(() => {
      let open!: (frame: string) => void;
      return { open: (f: string) => open(f), frame: new Promise<string>((r) => (open = r)) };
    });
    renderWithApi(
      <>
        <CenterEditor />
        <ChatPanel />
      </>,
      [
        {
          method: "POST",
          match: /\/turn\/events$/,
          stream: [
            sseFrames([{ event: "turn", data: opened }])[0],
            ...gates.map((g) => g.frame),
          ],
        },
      ],
    );
    const content = () => document.querySelector(".cm-content")?.textContent ?? "";
    await waitFor(() => expect(content()).toContain("李管家什么也没说"));
    await ask(user, "写一稿");
    await screen.findByText(/正在写入本章/);

    const tape = recordScreen();
    try {
      gates[0].open(sseFrames([{ event: "turn", data: delta("风雪落在肩上，") }])[0]);
      await waitFor(() => expect(content()).toContain("风雪落在肩上，"));
      gates[1].open(sseFrames([{ event: "turn", data: delta("他终于抬起头。") }])[0]);
      await waitFor(() => expect(content()).toContain("风雪落在肩上，他终于抬起头。"));
    } finally {
      tape.stop();
    }
    // **半句真的出现过** —— 同一个探子，相反的结论。
    expect(
      tape.seen.some((f) => f.includes("风雪落在肩上，") && !f.includes("他终于抬起头。")),
    ).toBe(true);
    // 右边那一侧（对话面板）一个字都没有。
    expect(document.querySelector(".pane.chat")?.textContent).not.toContain("风雪落在肩上");

    gates[2].open(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });

  it("**`reply_delta` 到手也不拼字** —— 它不是被忘了，是被拒了", () => {
    // 这一档今天在产品上不响（回话那次调用不流式）。哪天它响了，接它的那一刻
    // 要一起决定界面上怎么画——**在那之前默默拼起来就是一个假的打字机**。
    const deltas: ChatTurnEvent[] = ["风", "雪", "落"].map((text) => ({
      ...realEvent("reply_text"),
      kind: "reply_delta",
      text,
    }));
    expect(deltas.reduce((acc, event) => applyTurnEvent(acc, event, "zh"), NO_PROGRESS)).toEqual(
      NO_PROGRESS,
    );
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 二、那份夹具是真 dump，而且屏幕真的吃它
// ══════════════════════════════════════════════════════════════════════════

describe("`chatTurnEvents`：真字节，而且真的被吃了", () => {
  it("它是**原始帧**，不是一份「事件对象数组」", () => {
    // 手写一份「我以为后端长这样」的对象数组是这条缝原本的病。真 dump 的判据是形状：
    // 每一帧自带 `event:` / `data:` / 结尾那个空行，最后一帧是回执。
    expect(REAL.length).toBeGreaterThan(3);
    for (const frame of REAL) {
      expect(frame.startsWith("event: ")).toBe(true);
      expect(frame.endsWith("\n\n")).toBe(true);
      expect(frame.split("\ndata: ")).toHaveLength(2);
    }
    expect(REAL.at(-1)!.startsWith("event: receipt")).toBe(true);
    expect(REAL.filter((f) => f.startsWith("event: turn")).length).toBeGreaterThan(2);
  });

  it("**改一改它，屏幕跟着变** —— 也就是屏幕上的字真的来自这份夹具", async () => {
    // 一份被组件忽略掉的夹具和一份手写夹具一样没有价值：两者都只是「看起来接上了」。
    const user = userEvent.setup();
    const started = realEvent("tool_started");
    const marker = "正在做一件只有这条测试知道的事。";
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          sseFrames([{ event: "turn", data: { ...started, said_to_author: marker } }])[0],
          held,
        ],
      },
    ]);
    await ask(user, "查一下");

    const strip = await screen.findByRole("status");
    await waitFor(() => expect(within(strip).getByText(marker)).toBeInTheDocument());
    // 反面：原来那一句不该还在（它是被替换掉的那一份）。
    expect(strip.textContent).not.toContain(started.said_to_author);
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });

  it("**默认那条 handler 喂的就是它** —— 组件测试不是在吃一份手写的流", async () => {
    // `harness.tsx` 的默认表里长连接那条给的是 `fixtures.chatTurnEvents`。
    // 这条断言钉的是「默认路径」本身：不额外给 handler 的那些测试吃的也是真字节。
    const user = userEvent.setup();
    renderWithApi(<ChatPanel />);
    const spy = vi.spyOn(globalThis, "fetch"); // **在 stub 装好之后**才盯得住
    await ask(user, "走默认那条");

    // 屏幕上那一行回执来自**真 dump 的最后一帧**，中间那几行来自它前面那几帧。
    // **数是从那一帧里读出来的**，不是抄一个常量：抄了的话这条断言就和夹具脱钩，
    // 而它要证的恰恰是「屏幕上的字真的来自这份夹具」。
    const receipt = JSON.parse(REAL.at(-1)!.split("\ndata: ")[1]);
    expect(receipt.lookups).toBeGreaterThan(0); // 探针：那一轮真的查过资料
    await screen.findByText(`本轮查询 ${receipt.lookups} 次资料`);
    expect(spy.mock.calls.some(([url]) => String(url).endsWith("/turn/events"))).toBe(true);
    // 那一轮真的经过了长连接：不流式那条一次都没被打。
    expect(spy.mock.calls.some(([url]) => String(url).endsWith("/turn"))).toBe(false);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 三、十种事件**每一种**都被画过一遍（真 dump 只走到五种）
// ══════════════════════════════════════════════════════════════════════════

/** 真 dump 里那五种之外的那几种。**它们在契约夹具下一次都不会被渲染。** */
const NEVER_IN_THE_FIXTURE = [
  "reply_delta",
  "draft_delta",
  "draft_failed",
  "asked_author",
  "turn_stopped",
] as const;

describe("每一种事件都得被画过一遍", () => {
  it.each(NEVER_IN_THE_FIXTURE)("`%s`：屏幕上没有一个研发术语", async (kind) => {
    // 底子是真 dump 里那条 `tool_started`（字段集合就是后端今天那一份），只换 `kind`
    // 和那一档自己会带的东西。**`kind` / `tool` / `reason` 这三位故意填成最脏的形状**：
    // 它们要是有一条路上屏，形状网会当场咬住。
    const user = userEvent.setup();
    const base = realEvent("tool_started");
    const event: ChatTurnEvent = {
      ...base,
      kind,
      tool: "scene_constraints",
      reason: kind === "turn_stopped" ? "asked_author" : null,
      text: kind.endsWith("_delta") ? "风雪落在肩上。" : "",
      said_to_author: kind.endsWith("_delta") ? "" : "它这会儿在忙别的。",
      asked:
        kind === "asked_author"
          ? { question: "这一场你想让萧决知道那件事吗？", options: ["让他知道", "先瞒着"] }
          : null,
    };
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        // 先开一条流（`draft_delta` 要有一格可接），再来这一条。
        stream: [
          sseFrames([{ event: "turn", data: realEvent("draft_started") }])[0],
          sseFrames([{ event: "turn", data: event }])[0],
          held,
        ],
      },
    ]);
    await ask(user, "跑一个");
    await screen.findByRole("status");

    await waitFor(() => expect(devTerms(screenText())).toEqual([]));
    // 探针：`tool` 那一位真的是一个会被网咬住的形状（否则上面那条永远绿）。
    expect(devTerms(event.tool)).toContain("scene_constraints");
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });

  it("**模型编出来的工具名也不上屏** —— `tool` 那一位是模型打进来的字", async () => {
    // 后端那一位认不出的时候是空的，但**认得出的时候它是原样带出来的**，
    // 而界面只拿它分派、一个字都不该画。这条从屏幕这一侧再钉一次
    // （后端那一侧在 `tests/test_stream_out.py`）。
    const user = userEvent.setup();
    const nasty = "secret:01K_must_not_reveal";
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          sseFrames([{ event: "turn", data: { ...realEvent("tool_started"), tool: nasty } }])[0],
          held,
        ],
      },
    ]);
    await ask(user, "查一下");
    await screen.findByRole("status");

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(
        realEvent("tool_started").said_to_author,
      ),
    );
    expect(screenText()).not.toContain(nasty);
    expect(devTerms(screenText())).toEqual([]);
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 四、问题卡的**兜底那两支**（守卫今天只扫了「有选项」那一档）
// ══════════════════════════════════════════════════════════════════════════

const ASKED_BASE = {
  ...fixtures.chatTurn,
  reason: "asked_author",
  message: "写作助手提出了一个问题，等待回答。",
};

describe("问题卡：兜底那两支也要被扫到", () => {
  it("**没给选项那一档**：照实说去下面写，屏幕上没有研发术语", async () => {
    const user = userEvent.setup();
    const bare = { ...ASKED_BASE, asked: { question: "你想往哪个方向收？", options: [] } };
    renderWithApi(<ChatPanel />, [
      { method: "POST", match: /\/turn\/events$/, stream: turnStream(bare) },
    ]);
    await ask(user, "问我一句");

    const card = await screen.findByRole("group", { name: "写作助手在等待回答" });
    expect(within(card).getByText("请在下方输入回答")).toBeInTheDocument();
    // **不编两个选项出来**（同 `AmbiguousName`：不确定就摆出来，绝不替他挑）。
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**流断在「问了」和「回执」之间**：问题还在，而且那块屏幕上没有研发术语", async () => {
    // 这一支走的是 `progress.asked` 那条兜底（回执那一份根本没到手）。
    // 它和上面那一支是两块不同的屏幕：这一块同时挂着一个红框。
    const user = userEvent.setup();
    const asking = {
      ...realEvent("tool_started"),
      kind: "asked_author",
      tool: "",
      said_to_author: "写作助手提出了一个问题，等待回答。",
      asked: { question: "这一场你想让萧决知道那件事吗？", options: ["让他知道", "先瞒着"] },
    };
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: sseFrames([{ event: "turn", data: asking }]),
      },
    ]);
    await ask(user, "问我一句");

    await screen.findByText(/本轮未完成/);
    const card = screen.getByRole("group", { name: "写作助手在等待回答" });
    expect(within(card).getByText(asking.asked.question)).toBeInTheDocument();
    expect(devTerms(screenText())).toEqual([]);
  });

  it("**问句和选项一个字都不许被改写** —— 它们 100% 是模型自己的字", async () => {
    // 引擎往问句里加不了一个字（后端那条 handler 里连一个数据来源都没有）。
    // 这一层同理：不排序、不去重、不补「（推荐）」、不 trim。
    const user = userEvent.setup();
    const asked = {
      question: "  要不要把那件事说破？  ",
      options: ["说破", "说破", "先不说"],
    };
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: turnStream({ ...ASKED_BASE, asked }),
      },
    ]);
    await ask(user, "问我一句");

    const card = await screen.findByRole("group", { name: "写作助手在等待回答" });
    const buttons = within(card).getAllByRole("button").map((b) => b.textContent);
    // 顺序原样、重复原样（去重 = 替模型判断「这两条是一回事」= 语义判断）。
    expect(buttons).toEqual(asked.options);
    expect(card.textContent).toContain(asked.question.trim());
  });

  it("**点一个 = 他答了那一句**，而且发出去的是原文，不是被 trim 过的", async () => {
    const user = userEvent.setup();
    const asked = { question: "往哪边收？", options: ["  先瞒着  ", "让他知道"] };
    renderWithApi(<ChatPanel />, [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: turnStream({ ...ASKED_BASE, asked }),
      },
    ]);
    await ask(user, "问我一句");
    await screen.findByRole("group", { name: "写作助手在等待回答" });
    const spy = vi.spyOn(globalThis, "fetch");

    await user.click(screen.getByRole("button", { name: "先瞒着" }));

    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/turn/events"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body)).said).toBe(asked.options[0]);
    });
    // 输入框仍然空着：他没有被要求抄一遍。
    expect(say()).toHaveValue("");
  });
});
