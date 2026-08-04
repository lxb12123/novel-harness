import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { LeftRail } from "./LeftRail";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("左栏", () => {
  it("花名册按 label 分组，人名一个不落", async () => {
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />);
    for (const n of fixtures.roster) {
      expect(await screen.findByText(n.name)).toBeInTheDocument();
    }
  });

  it("空花名册只给一个清楚的下一步，不解释内部实现", async () => {
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />, [
      { match: /\/roster$/, body: [] },
    ]);
    expect(await screen.findByText(/添加第一个条目/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/导入只切章|认知矩阵|规则|ADR/);
  });

  it("点「建第一个」能开出建条目的抽屉 —— 空态那句话必须真的有出口", async () => {
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />, [
      { match: /\/roster$/, body: [] },
    ]);
    (await screen.findByText(/添加第一个条目/)).click();
    expect(await screen.findByRole("heading", { name: "花名册" })).toBeInTheDocument();
  });

  it("章目录点一下要把章号传出去", async () => {
    const onOpen = vi.fn();
    renderWithApi(<LeftRail onOpenChapter={onOpen} />);
    const first = fixtures.chapters[0];
    (await screen.findByText(first.title)).click();
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(first.number));
  });

  it("没有章节时指向浏览器里的导入，不是终端命令", async () => {
    // 「先 nh import 一本书」曾经印在这儿——那句话在浏览器里给作者看等于把他推回终端。
    renderWithApi(<LeftRail onOpenChapter={vi.fn()} />, [
      { match: /\/chapters$/, body: [] },
    ]);
    expect(await screen.findByText(/顶栏「导入」/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("nh import");
  });
});
