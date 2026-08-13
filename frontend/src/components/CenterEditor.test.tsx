import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
    const box = screen.getByRole("textbox", { name: "改这一章的名字" });
    await user.clear(box);
    await user.type(box, "血脉（改）{Enter}");

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

  // ── 「读回改动」（2026-08-13）───────────────────────────────────────────────
  //
  // `POST …/sync` 从 M1.5 起就在后端，浏览器里零调用方。作者在 WPS 里改完回来，
  // **正文他看得见**（这块屏幕直接读磁盘），可「记录这句」搜的是库里的快照——
  // 那半条回路此前根本没有入口。

  it("这一行上有它，并且**自己说得清它在干什么**", async () => {
    renderWithApi(<CenterEditor />);
    const button = await screen.findByRole("button", { name: "读回改动" });
    // 它和「历史 / 保存」在同一条工具条上——这一行讲的就是「这份稿子」。
    expect(button.closest(".edbar")).not.toBeNull();
    expect(screen.getByText("在别的软件里改过这本书，就点它一下。")).toBeInTheDocument();
    expect(button).toHaveAttribute(
      "title",
      expect.stringContaining("不读进来的话，新写的句子记录不了"),
    );
  });

  it("按下去之后照说后端那句回执（措辞的唯一出处在后端）", async () => {
    const user = userEvent.setup();
    renderWithApi(<CenterEditor />);
    await user.click(await screen.findByRole("button", { name: "读回改动" }));

    await screen.findByText(/读回来了/);
    // 作者自己的文件不是错误，回执要说得出「没动它们」。
    await screen.findByText(/这些文件不是章节，没有动它们/);
  });

  it("**没有 file-watch**：光是打开这一章，一次 sync 都不会自己发出去", async () => {
    // 这条钉的是一个决定，不是一个 bug：sync 是写路径（每次落一条快照，而快照是
    // 证据的锚），「磁盘先、DB 跟」里的那个「跟」是作者的动作（ADR 0007）。
    // 哪天有人加了个 `useEffect(() => sync(), [])`，这里当场红。
    renderWithApi(<CenterEditor />);
    await screen.findByRole("button", { name: "读回改动" });
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await new Promise((r) => setTimeout(r, 60));
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith("/sync"))).toEqual([]);
  });
});
