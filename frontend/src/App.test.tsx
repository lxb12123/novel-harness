import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import { useShelf } from "./bookshelf";
import { useCoords } from "./store";
import { fixtures, renderWithApi } from "./test/harness";

// 工作台整屏装起来才看得见的那几条 —— 单个组件测不出来的，只有它们该待在这儿。
//
// 今天只有一条：**左栏点章 ↔ App 的光标落位** 之间那道缝。两边分开看都是对的
//（书架把书和章一起翻过去；App 换了书就落到最后一章好让作者接着写），
// 合起来才是错的：作者点的第 1 章会被后一步顶成最后一章，而**中间没有任何报错**。

const [second, first] = fixtures.projectsTwo; // 库里的顺序：契约样书、青云记
const twoBooks = [{ match: /\/api\/projects$/, body: fixtures.projectsTwo }];

beforeEach(() => {
  // 光标已经替「青云记」落好了，作者正看着它的第 3 章（夹具里最后一章就是第 3 章）。
  useCoords.setState({
    projectId: first.id,
    cursorFor: first.id,
    chapter: 3,
    page: "workbench",
    chatOpen: false,
  });
  useShelf.setState({ hidden: [], collapsed: [] });
});

/** 一本书那一段（书名行 + 它的章目录）。 */
async function section(name: string) {
  const title = await screen.findByRole("button", { name });
  return title.closest(".book") as HTMLElement;
}

describe("工作台整屏", () => {
  it("点另一本书的第 1 章，就停在第 1 章 —— 不会被「换书去最后一章」顶掉", async () => {
    renderWithApi(<App />, twoBooks);
    const other = await section(second.name);
    (await within(other).findByText(fixtures.chapters[0].title)).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    // 顶掉是**后一帧**才发生的（要等那本书的章目录到手），所以不能点完就断言。
    // 停一会儿再看：这条测试的全部主张就是「过了那一帧它还在第 1 章」。
    await new Promise((r) => setTimeout(r, 50));
    expect(useCoords.getState().chapter).toBe(1);
  });

  it("只点书名（没点章）照旧落到最后一章 —— 日更作者打开书是来接着写的", async () => {
    renderWithApi(<App />, twoBooks);
    const other = await section(second.name);
    within(other).getByRole("button", { name: second.name }).click();

    await waitFor(() => expect(useCoords.getState().projectId).toBe(second.id));
    await waitFor(() => expect(useCoords.getState().chapter).toBe(fixtures.chapters.at(-1)!.number));
  });
});
