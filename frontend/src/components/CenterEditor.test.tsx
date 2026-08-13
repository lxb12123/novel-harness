import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { CenterEditor } from "./CenterEditor";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "",
    selection: "",
    setSelection: () => {},
    focusNode: () => {},
    highlight: null,
    setHighlight: () => {},
  });
  // CodeMirror 6 在 jsdom 里需要这两个测量 API。
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as Record<string, unknown>).ResizeObserver = RO;
});

describe("中栏编辑器", () => {
  it("顶栏那行是这一章的**标题**，正文从 /text 拉取（fixture 真出参）", async () => {
    // 章标题 = 正文第一行，而这一行正文来自 `chapterText` 那份真 dump——
    // 所以这条断言同时钉住「显示的标题和正文是同一份东西」。
    renderWithApi(<CenterEditor />);
    expect(await screen.findByText(fixtures.chapters[0].title)).toBeInTheDocument();
  });

  it("改标题 = 改正文第一行，走的是同一条「未保存 → 保存」的路", async () => {
    // 没有 rename 端点：标题在磁盘上就是正文的第一行，第二条写路径 = 第二份真相。
    const user = userEvent.setup();
    renderWithApi(<CenterEditor />);
    await screen.findByText(fixtures.chapters[0].title);

    await user.dblClick(screen.getByRole("button", { name: "当前章节" }));
    const box = screen.getByRole("textbox", { name: "改这一章的标题" });
    await user.clear(box);
    await user.type(box, "第一章 血脉（改）{Enter}");

    // 两边都得变，因为它们**是同一行字**：顶上那行标题，和正文的第一行。
    expect(within(screen.getByRole("button", { name: "当前章节" })).getByText("第一章 血脉（改）"))
      .toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector(".cm-line")?.textContent).toBe("第一章 血脉（改）"),
    );
    // 「未保存」= 这次改动落在编辑器手上那份正文里，等作者按保存（没有第二条写路径）。
    expect(screen.getByText("未保存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
  });

  it("嵌着的场景条为空时给出「下一步」提示", async () => {
    renderWithApi(<CenterEditor />);
    expect(await screen.findByText(/这一章还没有场景信息/)).toBeInTheDocument();
  });
});
