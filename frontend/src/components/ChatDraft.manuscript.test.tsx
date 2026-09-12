import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi, ROUND_DONE } from "../test/harness";
import { useCoords } from "../store";
import { CenterEditor } from "./CenterEditor";
import { ChatPanel } from "./ChatPanel";

// **中栏的两半在同一棵树上，这个文件量的就是它们之间那条缝。**
//
// 写作助手起草会**直接写进磁盘上那一章**（ADR 0021，不弹框）。于是「跑完一轮之后正文
// 可能已经不是屏幕上这一份了」第一次成真——而作者下一步很可能就是按保存。
// 不重读的后果不是显示滞后，是**他把助手写的一整章盖掉**。

beforeEach(() => {
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
