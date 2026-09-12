import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi, ROUND_DONE, sseFrames } from "../test/harness";
import type { ChatTurnEvent } from "../api/types";
import { useLiveDraft } from "../liveDraft";
import { useCoords } from "../store";
import { CenterEditor } from "./CenterEditor";
import { ChatPanel } from "./ChatPanel";

// **中栏的两半在同一棵树上，这个文件量的就是它们之间那条缝。**
//
// 写作助手起草会**直接写进磁盘上那一章**（ADR 0021，不弹框）。于是「跑完一轮之后正文
// 可能已经不是屏幕上这一份了」第一次成真——而作者下一步很可能就是按保存。
// 不重读的后果不是显示滞后，是**他把助手写的一整章盖掉**。

beforeEach(() => {
  useLiveDraft.getState().clear();
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    chatOpen: true,
    chatId: null,
    page: "workbench",
    highlight: null,
  });
  // CodeMirror 6 在 jsdom 里需要这个（同 `CenterEditor.test.tsx`）。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("一轮跑完之后，正文那一侧", () => {
  it("**重读一遍磁盘上那一章** —— 助手可能刚把它写掉了", async () => {
    const user = userEvent.setup();
    let reads = 0;
    renderWithApi(
      <>
        <CenterEditor />
        <ChatPanel />
      </>,
      [
        {
          match: /\/chapters\/\d+\/text/,
          body: () => {
            reads++;
            return fixtures.chapterText;
          },
        },
      ],
    );
    await screen.findByText("第 1 章");
    await waitFor(() => expect(reads).toBe(1));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText(ROUND_DONE);

    // 回执里**没有**「它到底写没写」这一位，所以这一档一律重取：宁可白取一次，
    // 也不要让作者对着一份旧稿按保存。
    await waitFor(() => expect(reads).toBeGreaterThan(1));
  });

  it("**起草那一步一跑完就重读，不等这一轮收场**（ADR 0048：作者要的是直接在左边看到）", async () => {
    // 那一轮的最后一帧卡住：这一轮还在跑（模型还在说话），而磁盘上那一章已经变了。
    // 判据是 `tool_finished`（`tool` 是 `draft_chapter`、ok）——落盘就发生在那一步里。
    const user = userEvent.setup();
    let reads = 0;
    const frames = fixtures.chatTurnEvents as string[];
    const upToLanding = frames.slice(
      0,
      frames.findIndex((f) => f.includes('"kind":"tool_finished"') && f.includes('"tool":"draft_chapter"')) + 1,
    );
    expect(upToLanding.length).toBeGreaterThan(0); // 探针：真 dump 那一轮里真有那一步
    const held = new Promise<string>(() => {});
    renderWithApi(
      <>
        <CenterEditor />
        <ChatPanel />
      </>,
      [
        {
          match: /\/chapters\/\d+\/text/,
          body: () => {
            reads++;
            return fixtures.chapterText;
          },
        },
        { method: "POST", match: /\/turn\/events$/, stream: [...upToLanding, held] },
      ],
    );
    await screen.findByText("第 1 章");
    await waitFor(() => expect(reads).toBe(1));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 回执还没到（流卡着），正文那一侧已经重读过一次。
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(screen.queryByText(ROUND_DONE)).toBeNull();
  });

  it("没有未保存的字：重读回来的新正文装进编辑器 —— 否则他对着旧稿接着写", async () => {
    const user = userEvent.setup();
    let reads = 0;
    const drafted = { ...fixtures.chapterText, markdown: "（助手刚写进这一章的一整稿）" };
    renderWithApi(
      <>
        <CenterEditor />
        <ChatPanel />
      </>,
      [
        {
          match: /\/chapters\/\d+\/text/,
          body: () => (reads++ === 0 ? fixtures.chapterText : drafted),
        },
      ],
    );
    await screen.findByText("第 1 章");

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText(ROUND_DONE);

    await waitFor(() =>
      expect(document.querySelector(".cm-content")?.textContent).toContain("助手刚写进这一章"),
    );
  });

  it("**作者手上有没保存的字：一个字都不许盖**，只说一句", async () => {
    // 盖掉他没保存的那半段是**找不回来的**（版本历史只存保存过的），
    // 盖掉磁盘上那一版是找得回来的。两种错的代价不对称，所以这一档不替他挑。
    const user = userEvent.setup();
    let reads = 0;
    const drafted = { ...fixtures.chapterText, markdown: "（助手刚写进这一章的一整稿）" };
    renderWithApi(
      <>
        <CenterEditor />
        <ChatPanel />
      </>,
      [
        {
          match: /\/chapters\/\d+\/text/,
          body: () => (reads++ === 0 ? fixtures.chapterText : drafted),
        },
      ],
    );
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await user.click(content);
    await user.type(content, "作者刚打的半段");
    // 「未保存」2026-08-30 从文字改成了保存按钮角上的水滴徽标（见 icons.tsx）。
    await waitFor(() => expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull());

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText(ROUND_DONE);

    // 说一句：他下一步按保存会盖过磁盘上那一版（那一版在「历史」里找得回来）。
    await screen.findByText(/本章已在别处修改/);
    expect(content.textContent).toContain("作者刚打的半段");
    expect(content.textContent).not.toContain("助手刚写进这一章");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 正在写的那一稿流进左边（ADR 0048 第二半，作者：「有个编辑的过程在左边也能看到」）
// ══════════════════════════════════════════════════════════════════════════

/** 真 dump 那一轮里的真事件，只改 kind / text / chapter——字段集合仍是后端今天那一份。 */
const realEvent = (kind: string): ChatTurnEvent => {
  const frame = (fixtures.chatTurnEvents as string[]).find((f) => f.includes(`"kind":"${kind}"`));
  if (!frame) throw new Error(`真 dump 里没有 ${kind}`);
  return JSON.parse(frame.split("\ndata: ")[1]) as ChatTurnEvent;
};
/** 一条对着**第 1 章**写的流（真 dump 写的是第 2 章，编辑器开着的是第 1 章）。 */
const opened = { ...realEvent("draft_started"), chapter: 1 };
const piece = (text: string) => ({ ...opened, kind: "draft_delta" as const, text, said_to_author: "" });
const turnFrame = (data: unknown) => sseFrames([{ event: "turn", data }])[0];

describe("正在写的那一稿流进左边", () => {
  const shell = () => (
    <>
      <CenterEditor />
      <ChatPanel />
    </>
  );

  it("字一片片长在编辑器里，键盘锁着；右边那一格只留标题行，同一段字不画两遍", async () => {
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece("〖自述〗这一版更冷。\n\n")),
          turnFrame(piece("风雪落在肩上，")),
          turnFrame(piece("他终于抬起头。")),
          held,
        ],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(content.textContent).toContain("风雪落在肩上，他终于抬起头。"));
    // 开头那句自述不进正文（后端落盘时也会切掉它）。
    expect(content.textContent).not.toContain("自述");
    // 这几十秒里作者不能往里敲字：流进来的字和他的字会混在一起。
    expect(content.getAttribute("contenteditable")).toBe("false");
    expect(screen.getByText(/写作助手正在写入本章/)).toBeInTheDocument();
    // 右边只留标题行。
    expect(screen.getByText(/正在起草第 1 章，正文在左侧/)).toBeInTheDocument();
    expect(document.querySelector(".chat-drafting-text")).toBeNull();
  });

  it("第一片字到手之前编辑器里仍是原来的正文——不是一片空白", async () => {
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, stream: [turnFrame(opened), held] },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(/写作助手正在写入本章/);
    expect(content.textContent).toContain("李管家什么也没说。");
  });

  it("**作者手上有没保存的字：不接**，那条流仍在右边长，他的字一个都不动", async () => {
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(opened), turnFrame(piece("风雪落在肩上。")), held],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await user.click(content);
    await user.type(content, "作者刚打的半段");
    await waitFor(() => expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull());

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("风雪落在肩上。", { selector: ".chat-drafting-text" });
    expect(content.textContent).toContain("作者刚打的半段");
    expect(content.textContent).not.toContain("风雪落在肩上");
    expect(content.getAttribute("contenteditable")).toBe("true");
    expect(screen.queryByText(/写作助手正在写入本章/)).toBeNull();
  });

  it("落盘之后新正文到手，那条流让位，键盘解锁——中间没有一帧闪回旧稿", async () => {
    const user = userEvent.setup();
    let reads = 0;
    const drafted = {
      ...fixtures.chapterText,
      markdown: "第一章 试探\n\n风雪落在肩上，他终于抬起头。",
      text_sha256: "b".repeat(64),
    };
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    renderWithApi(shell(), [
      {
        match: /\/chapters\/\d+\/text/,
        body: () => (reads++ === 0 ? fixtures.chapterText : drafted),
      },
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece("风雪落在肩上，他终于抬起头。")),
          turnFrame({ ...realEvent("draft_kept"), chapter: 1 }),
          // 起草那一步跑完 = 那一章已经在磁盘上变了（`useRunTurn` 据此重取正文）。
          turnFrame({ ...realEvent("tool_finished"), tool: "draft_chapter", ok: true, chapter: 1 }),
          held,
        ],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(reads).toBe(1));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 新正文到手：编辑器里是磁盘上那一版，流已经放掉，键盘解锁。这一轮还没收场（流卡着）。
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    await waitFor(() => expect(useLiveDraft.getState().draft).toBeNull());
    expect(content.textContent).toContain("风雪落在肩上，他终于抬起头。");
    expect(content.getAttribute("contenteditable")).toBe("true");
    expect(screen.queryByText(/写作助手正在写入本章/)).toBeNull();
    expect(screen.queryByText(ROUND_DONE)).toBeNull();
    release(sseFrames([{ event: "receipt", data: fixtures.chatTurn }])[0]);
    await screen.findByText(ROUND_DONE);
  });
});

