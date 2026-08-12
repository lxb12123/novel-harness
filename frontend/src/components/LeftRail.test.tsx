import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { LeftRail } from "./LeftRail";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("左栏", () => {
  it("章目录点一下要把章号传出去", async () => {
    const onOpen = vi.fn();
    renderWithApi(<LeftRail onOpenChapter={onOpen} />);
    const first = fixtures.chapters[0];
    (await screen.findByText(first.title)).click();
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(first.number));
  });

  it("没有章节时指向浏览器里的导入，不是终端命令", async () => {
    // 「先 nh import 一本书」曾经印在这儿——那句话在浏览器里给作者看等于把他推回终端。
    // 按钮从顶栏搬进了书架，所以这句指路也跟着改；**指的仍然必须是屏幕上真有的那个东西**。
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />, [{ match: /\/chapters$/, body: [] }]);
    expect(await screen.findByText(/「＋ 新书 \/ 导入」/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 新书 / 导入" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("nh import");
  });

  it("花名册已经不在左栏了 —— 它搬去了右栏第一格", async () => {
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />);
    await screen.findByText(fixtures.chapters[0].title);
    expect(screen.queryByRole("heading", { name: "花名册" })).toBeNull();
    // 人名一个都不该再出现在这一栏：留一份旧拷贝在这儿，两边就会各自漂。
    expect(screen.queryByText(fixtures.roster[0].name)).toBeNull();
  });
});
