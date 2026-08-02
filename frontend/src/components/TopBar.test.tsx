import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { TopBar } from "./TopBar";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("顶栏", () => {
  it("AI 规划灰置；AI 起草已按修正案 7 开放（实验状态）", async () => {
    // 2026-08-02 修正案 7：起草先行开放（实验状态），规划仍是 M2 stub。
    renderWithApi(<TopBar />);

    const plan = await screen.findByRole("button", { name: "AI 规划" });
    const draft = await screen.findByRole("button", { name: "AI 起草" });
    expect(plan).toBeDisabled();
    expect(draft).toBeEnabled();
    expect(plan).toHaveAttribute("title", expect.stringContaining("M2"));
    expect(draft).toHaveAttribute("title", expect.stringContaining("实验状态"));
    // 点起草 = 打开起草抽屉；点灰按钮不该触发任何东西。
    plan.click();
    draft.click();
    expect(
      await screen.findByRole("heading", { name: /AI 起草 · 第 1 章/ })
    ).toBeInTheDocument();
  });
});
