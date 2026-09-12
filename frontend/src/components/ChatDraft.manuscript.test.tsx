import { undo } from "@codemirror/commands";
import { EditorView } from "@codemirror/view";
import { screen, waitFor, within } from "@testing-library/react";
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
  // 这个 store 活得比一个测试长：上一条放进编辑器的那一稿不清掉，下一条会把它当成自己的。
  useLiveDraft.setState({ draft: null, inEditor: false, placed: null, saved: [] });
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

  it("字一片片长在编辑器里，键盘锁着；右边什么都不画，同一段字不画两遍", async () => {
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
    // 右边那一格什么都不画（作者：「不用特地提醒在左边什么的」）——步骤行「正在起草。」
    // 已经说了；同一段字更不画两遍。
    expect(screen.queryByText(/正文在左侧/)).toBeNull();
    expect(document.querySelector(".chat-drafting")).toBeNull();
    expect(document.querySelector(".chat-drafting-text")).toBeNull();
  });

  it("**到手的字匀速露出来，不是一段一段跳**（作者：「一段一段字的走，不是一个一个的，不美观」）", async () => {
    // 一次到手一整段：屏幕上不许一下全出来。判据是「露出来的字数单调增、中间真的有
    // 只露了一部分的一帧」——不用秒表，秒表在慢机器上会假红。
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    const paragraph = "风雪落在肩上，他终于抬起头。".repeat(12);
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, stream: [turnFrame(opened), turnFrame(piece(paragraph)), held] },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const seen: number[] = [];
    await waitFor(
      () => {
        const shown = content.textContent ?? "";
        if (shown.startsWith("风雪")) seen.push(shown.length);
        expect(shown).toBe(paragraph);
      },
      { timeout: 5000, interval: 10 },
    );
    expect(seen.length).toBeGreaterThan(1);
    expect(seen.some((n) => n > 0 && n < paragraph.length)).toBe(true);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
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

  it("**作者手上有没保存的字：稿子照样写在这儿、替掉它们**；撤销（⌘Z）退得回去", async () => {
    // 上一版在这一档把稿子退到右边去写（编辑器「不接」），作者：「为什么现在又整到右边去了」
    // ——稿子永远在左边。他那几段没丢：在编辑器的撤销历史里。
    const user = userEvent.setup();
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(opened), turnFrame(piece("风雪落在肩上。")), turnFrame(kept), held],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await user.click(content);
    await user.type(content, "作者刚打的半段");
    await waitFor(() => expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull());

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 稿子写在左边，右边一个字都没有；他的字被替掉了。
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    expect(content.textContent).toContain("风雪落在肩上。");
    expect(document.querySelector(".chat-drafting-text")).toBeNull();
    expect(content.textContent).not.toContain("作者刚打的半段");

    // ⌘Z：一步步退回去（流进来的字是几笔连着的编辑），他的字还在。
    const view = EditorView.findFromDOM(document.querySelector(".cm-editor") as HTMLElement)!;
    for (let i = 0; i < 20 && !content.textContent?.includes("作者刚打的半段"); i++) undo(view);
    expect(content.textContent).toContain("作者刚打的半段");
    expect(content.textContent).not.toContain("风雪落在肩上");
  });

  it("流着的时候痕迹按半份正文画：写完的段绿，还没被后面的段钉死的删除先不画红", async () => {
    // 半份对着全份比会把「还没写到的段」全判成删掉，红块闪一下又没（`editMarks.ts`）。
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(opened), turnFrame(piece("第一段新写的。\n\n第二段还在")), held],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(content.textContent).toContain("第二段还在"));
    await waitFor(() =>
      expect([...document.querySelectorAll(".cm-line.diff-add")].map((el) => el.textContent)).toEqual([
        "第一段新写的。",
      ]),
    );
    expect(document.querySelector(".diff-del")).toBeNull();
  });

  it("写完：整份进编辑器、**未保存**、痕迹对着保存版画（旧段红、新段绿）；键盘解锁；右边那一行说它在编辑器里", async () => {
    const user = userEvent.setup();
    let release!: (frame: string) => void;
    const held = new Promise<string>((r) => (release = r));
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    expect(kept.draft_id).toMatch(/:/); // 探针：收场那一声带着编号
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece("风雪落在肩上，他终于抬起头。")),
          turnFrame(kept),
          held,
        ],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 字全露完 → 它是作者手上一份未保存的修改：脏、可编辑、画着痕迹、保存按得动。
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    expect(content.textContent).toContain("风雪落在肩上，他终于抬起头。");
    expect(content.getAttribute("contenteditable")).toBe("true");
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
    // 流一收场，整份对整份：新写的那一段绿，被它换掉的原文红（不可编辑的一块，插在原处）。
    await waitFor(() =>
      expect(document.querySelector(".cm-line.diff-add")?.textContent).toContain("风雪落在肩上"),
    );
    expect([...document.querySelectorAll(".diff-del")].map((el) => el.textContent)).toEqual([
      "萧决在青云城主府第一次听说了血脉秘密的真相。",
      "李管家什么也没说。",
    ]);
    expect(screen.getByText(/按「保存」写入本章/)).toBeInTheDocument();
    // 没有「放弃这一稿」这颗按钮（作者：「没必要存在」）：不要它走「历史」或 ⌘Z。
    expect(screen.queryByRole("button", { name: /放弃/ })).toBeNull();
    // 磁盘那一侧**没动**：一轮里没有任何一次读正文之外的写。
    expect(screen.queryByText(/写作助手正在写入本章/)).toBeNull();

    // 这一轮收场，右边那一行说它在编辑器里、等着保存。
    release(sseFrames([{ event: "receipt", data: { ...fixtures.chatTurn, drafts: [{ ...fixtures.chatTurn.drafts[0], id: kept.draft_id, chapter: 1 }] } }])[0]);
    await screen.findByText(ROUND_DONE);
    expect(screen.getByText(/已放入编辑器，按「保存」写入本章/)).toBeInTheDocument();
  });

  it("写出来的和本章正文一字不差：不脏、没有痕迹，顶栏和右边那一行都说「与本章正文相同」", async () => {
    // 真书第 158 章：写手把现有正文照抄了回来，连着五稿逐字节相同。那时没有东西可保存，
    // 也没有红绿——不说清楚，作者以为界面坏了（「为什么我这边左边还没有？」）。
    const user = userEvent.setup();
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    const sameBody = "萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家什么也没说。\n";
    expect(fixtures.chapterText.markdown.endsWith(sameBody)).toBe(true); // 探针：正文就是这两段
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece(sameBody)),
          turnFrame(kept),
          sseFrames([{ event: "receipt", data: { ...fixtures.chatTurn, drafts: [{ ...fixtures.chatTurn.drafts[0], id: kept.draft_id, chapter: 1 }] } }])[0],
        ],
      },
    ]);
    await screen.findByText("第 1 章");
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "重写这一章");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    await screen.findByText(ROUND_DONE);

    expect(screen.getByText("写作助手的这一稿与本章正文相同")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(document.querySelector(".cm-line.diff-add")).toBeNull();
    expect(document.querySelector(".diff-del")).toBeNull();
    expect(screen.getByText(/已放入编辑器，与本章正文相同/)).toBeInTheDocument();
    expect(screen.queryByText(/按「保存」写入本章/)).toBeNull();
  });

  it("改一段（`revising`）：整章一片到手，写完直接放进编辑器、不逐字重打；痕迹上只有改的那一处", async () => {
    // 助手说改哪儿、写手只写那一段、后端拼回整章（ADR 0049）——编辑器拿到的是一整章，
    // 逐字重打一遍作者看到的是「整章都在动」，所以这条流不走打字机。
    const user = userEvent.setup();
    const revising = { ...opened, revising: true };
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    const revised = "萧决在青云城主府第一次听说了血脉秘密的真相。\n李管家沉默了很久，什么也没说。\n";
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(revising), turnFrame({ ...piece(revised), revising: true }), turnFrame(kept), held],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把那句改软一点");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    // 一次到位：没有「只露了一部分」的一帧——放进来那一刻整章就全在了。
    expect(content.textContent).toContain("李管家沉默了很久，什么也没说。");
    expect(content.textContent).toContain("萧决在青云城主府第一次听说了血脉秘密的真相。");
    expect(content.getAttribute("contenteditable")).toBe("true");
    // 痕迹只在改的那一处：没动的第一段不涂。
    expect([...document.querySelectorAll(".cm-line.diff-add")].map((el) => el.textContent)).toEqual([
      "李管家沉默了很久，什么也没说。",
    ]);
    expect([...document.querySelectorAll(".diff-del")].map((el) => el.textContent)).toEqual([
      "李管家什么也没说。",
    ]);
    expect(screen.getByText(/按「保存」写入本章/)).toBeInTheDocument();
    expect(screen.queryByText(/正在修改本章/)).toBeNull();
  });

  it("改一段还在路上：编辑器仍是原来的正文、锁着，顶栏说「正在修改本章」", async () => {
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, stream: [turnFrame({ ...opened, revising: true }), held] },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把那句改软一点");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText(/写作助手正在修改本章/);
    expect(content.textContent).toContain("李管家什么也没说。");
    expect(content.getAttribute("contenteditable")).toBe("false");
    expect(document.querySelector(".cm-line.diff-add")).toBeNull();
  });

  it("整章重写：旧章那一大块红折成一行「已删除 N 段」，新稿全绿在下面，点开才摊开旧稿", async () => {
    const user = userEvent.setup();
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    const held = new Promise<string>(() => {});
    const six = ["一", "二", "三", "四", "五", "六"].map((s) => s + "段的正文。").join("\n\n");
    renderWithApi(shell(), [
      { match: /\/chapters\/\d+\/text/, body: { ...fixtures.chapterText, markdown: "第一章 血脉\n\n" + six + "\n" } },
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(opened), turnFrame(piece("重写的第一段。\n\n重写的第二段。")), turnFrame(kept), held],
      },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("六段的正文。"));
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章重写");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));

    const fold = await screen.findByRole("button", { name: "已删除 6 段" });
    expect([...document.querySelectorAll(".cm-line.diff-add")].map((el) => el.textContent)).toEqual([
      "重写的第一段。",
      "重写的第二段。",
    ]);
    expect(document.querySelectorAll(".diff-del")).toHaveLength(0);
    // 红块在绿行前面：旧稿折成的那一行在新稿上面。
    expect(fold.compareDocumentPosition(document.querySelector(".cm-line.diff-add")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(fold);
    await waitFor(() => expect(document.querySelectorAll(".diff-del")).toHaveLength(6));
  });

  it("按「保存」：请求带上那一稿的编号，痕迹消失；右边那一行改说「已写入」", async () => {
    const user = userEvent.setup();
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    let putBody: Record<string, unknown> | null = null;
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece("风雪落在肩上，他终于抬起头。")),
          turnFrame(kept),
          sseFrames([{ event: "receipt", data: { ...fixtures.chatTurn, drafts: [{ ...fixtures.chatTurn.drafts[0], id: kept.draft_id, chapter: 1 }] } }])[0],
        ],
      },
      {
        method: "PUT",
        match: /\/chapters\/1\/text$/,
        onRequest: (init) => {
          putBody = init?.body ? JSON.parse(String(init.body)) : null;
        },
        body: fixtures.chapterSaved,
      },
    ]);
    await screen.findByText("第 1 章");
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    await screen.findByText(ROUND_DONE);

    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(putBody).not.toBeNull(), { timeout: 3000 });
    expect(putBody).toMatchObject({ draft_id: kept.draft_id, expected_text_sha256: fixtures.chapterText.text_sha256 });
    expect(String((putBody as unknown as Record<string, unknown>).markdown)).toContain("风雪落在肩上，他终于抬起头。");
    await waitFor(() => expect(document.querySelector(".cm-line.diff-add")).toBeNull());
    expect(document.querySelector(".diff-del")).toBeNull();
    expect(screen.queryByText(/按「保存」写入本章/)).toBeNull();
    expect(screen.getByText(/已写入第 1 章/)).toBeInTheDocument();
  });

  it("不要这一稿：「历史」里对着当前版本按「还原」，回到磁盘上那一版，痕迹消失，不脏；右边那一行回到「放入编辑器」", async () => {
    const user = userEvent.setup();
    const kept = { ...realEvent("draft_kept"), chapter: 1 };
    let puts = 0;
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [
          turnFrame(opened),
          turnFrame(piece("风雪落在肩上。")),
          turnFrame(kept),
          sseFrames([{ event: "receipt", data: { ...fixtures.chatTurn, drafts: [{ ...fixtures.chatTurn.drafts[0], id: kept.draft_id, chapter: 1 }] } }])[0],
        ],
      },
      { match: /\/history$/, body: fixtures.chapterHistory },
      { method: "PUT", match: /\/text$/, body: fixtures.chapterSaved, onRequest: () => void puts++ },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(kept.draft_id));
    await screen.findByText(ROUND_DONE);
    expect(screen.getByText(/已放入编辑器，按「保存」写入本章/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "历史" }));
    const rows = await screen.findAllByRole("listitem");
    const now = rows.find((r) => within(r).queryByText("当前"))!;
    await user.click(within(now).getByRole("button", { name: "还原" }));
    await user.click(await screen.findByRole("button", { name: "确认还原" }));

    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));
    expect(content.textContent).not.toContain("风雪落在肩上");
    await waitFor(() => expect(document.querySelector(".cm-line.diff-add")).toBeNull());
    expect(document.querySelector(".diff-del")).toBeNull();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(useLiveDraft.getState().placed).toBeNull();
    expect(puts).toBe(0); // 磁盘上就是这一版：不写盘
    expect(screen.getByRole("button", { name: "放入编辑器" })).toBeInTheDocument();
  });

  it("桌上的一稿按「放入编辑器」：整份进左边、未保存、画痕迹；右边那一行改说它在编辑器里", async () => {
    // 一批几稿的第二稿起、上一稿被替下来的，都走这条路——读它的地方也是左边的正文，
    // 右边从头到尾一个字都不画。
    const user = userEvent.setup();
    const desk = { ...fixtures.chatTurn.drafts[0], id: "draft:ID45", chapter: 1, landed: false };
    const detail = { ...fixtures.draftDetail, id: desk.id, chapter: 1, text: "桌上那一稿的全文。\n\n第二段。" };
    renderWithApi(shell(), [
      { method: "POST", match: /\/turn\/events$/, body: { ...fixtures.chatTurn, drafts: [desk] } },
      { match: /\/drafts\/[^/]+$/, body: detail },
    ]);
    await screen.findByText("第 1 章");
    const content = document.querySelector(".cm-content") as HTMLElement;
    await waitFor(() => expect(content.textContent).toContain("李管家什么也没说。"));
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText(ROUND_DONE);
    expect(screen.queryByText(/桌上那一稿的全文/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "放入编辑器" }));

    await waitFor(() => expect(content.textContent).toContain("桌上那一稿的全文。"));
    await waitFor(() => expect(useLiveDraft.getState().placed?.draftId).toBe(desk.id));
    expect(content.getAttribute("contenteditable")).toBe("true");
    expect(document.querySelector(".save-badge-icon.droplet")).not.toBeNull();
    expect([...document.querySelectorAll(".cm-line.diff-add")].map((el) => el.textContent)).toEqual([
      "桌上那一稿的全文。",
      "第二段。",
    ]);
    expect(document.querySelectorAll(".diff-del")).toHaveLength(2);
    // 右边那一行：不再是「放入编辑器」，说它在编辑器里等着保存；全文仍然不在右边。
    expect(screen.queryByRole("button", { name: "放入编辑器" })).toBeNull();
    expect(screen.getByText(/已放入编辑器，按「保存」写入本章/)).toBeInTheDocument();
    expect(document.querySelector(".pane.chat")?.textContent).not.toContain("桌上那一稿的全文");
  });

  it("翻上去看前面写的：画面留在原地，正中一颗「滑到最下方」；点它回到底、继续跟", async () => {
    const user = userEvent.setup();
    const held = new Promise<string>(() => {});
    renderWithApi(shell(), [
      {
        method: "POST",
        match: /\/turn\/events$/,
        stream: [turnFrame(opened), turnFrame(piece("风雪落在肩上，他终于抬起头。".repeat(20))), held],
      },
    ]);
    await screen.findByText("第 1 章");
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "把这一章写了");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText(/写作助手正在写入本章/);
    expect(screen.queryByRole("button", { name: /滑到最下方/ })).toBeNull();

    // jsdom 不排版：把滚动条的几何数装出来——正文比视口高、而且翻到了上面。
    const scroller = document.querySelector(".cm-scroller") as HTMLElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, get: () => 2000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, get: () => 500 });
    // **编辑器自己滚的那几下不算翻上去**：正文刚长出来一截、还没跟上底的那一帧也会发
    // scroll，离底几十像素——按位置判会在正文长到满一屏之后隔一会儿就掐断一次跟底。
    scroller.scrollTop = 100;
    scroller.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: /滑到最下方/ })).toBeNull();

    // 作者自己滚了一下（滚轮），紧跟着的 scroll 才是他翻上去了。
    scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }));
    scroller.scrollTop = 80;
    scroller.dispatchEvent(new Event("scroll"));

    const jump = await screen.findByRole("button", { name: /滑到最下方/ });
    await user.click(jump);
    await waitFor(() => expect(screen.queryByRole("button", { name: /滑到最下方/ })).toBeNull());

    // 他自己又滚回了底：不用按那颗按钮也重新贴上（按钮不再出现）。
    scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }));
    scroller.scrollTop = 60;
    scroller.dispatchEvent(new Event("scroll"));
    await screen.findByRole("button", { name: /滑到最下方/ });
    scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: 120 }));
    scroller.scrollTop = 1500;
    scroller.dispatchEvent(new Event("scroll"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /滑到最下方/ })).toBeNull());
  });
});

