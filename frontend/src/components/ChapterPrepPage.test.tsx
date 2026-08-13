import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { ChapterPrepPage } from "./ChapterPrepPage";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, cast: "", page: "prep" });
});

describe("章节准备页", () => {
  it("在这一页也能换章 —— 它换掉的是**整块中栏**，挑章的控件不在这儿就等于换不了", async () => {
    // 那个控件原先在顶栏（一个 <select>，顶栏在每一页上都在）。搬去中栏之后，
    // 这一页一度只剩「回工作台」这一条路才能换章 —— 这条断言钉住那个洞已经补上。
    const user = userEvent.setup();
    renderWithApi(<ChapterPrepPage />);

    await user.click(await screen.findByRole("button", { name: "当前章节" }));
    await user.click(await screen.findByRole("option", { name: /第二章/ }));

    expect(useCoords.getState().chapter).toBe(fixtures.chapters[1].number);
  });

  it("这一页没有编辑器，所以标题只能看和挑，**不能双击改**", async () => {
    // 改标题改的是正文第一行，而这一页手上没有正文：改了就是往一份它没读过的稿子上写字。
    const user = userEvent.setup();
    renderWithApi(<ChapterPrepPage />);

    await user.dblClick(await screen.findByRole("button", { name: "当前章节" }));
    expect(screen.queryByRole("textbox", { name: "改这一章的标题" })).not.toBeInTheDocument();
  });
});
