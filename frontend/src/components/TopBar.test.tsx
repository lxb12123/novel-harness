import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { TopBar } from "./TopBar";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("顶栏", () => {
  it("M2 的 AI 规划/起草按钮是灰置 stub，不是被藏起来", async () => {
    // UI_ARCHITECTURE §2.2：5 条 501 stub 存在的唯一理由就是让作者看见
    // 「这里将来会有什么」。按钮必须渲染、必须 disabled、必须说明是 M2 未开放。
    renderWithApi(<TopBar />);

    const plan = await screen.findByRole("button", { name: "AI 规划" });
    const draft = await screen.findByRole("button", { name: "AI 起草" });
    expect(plan).toBeDisabled();
    expect(draft).toBeDisabled();
    expect(plan).toHaveAttribute("title", expect.stringContaining("M2"));
    expect(draft).toHaveAttribute("title", expect.stringContaining("M2"));
    // 点击灰按钮不该触发任何东西（比如开抽屉、切页面）。
    plan.click();
    draft.click();
    expect(await screen.findByRole("button", { name: "AI 规划" })).toBeDisabled();
  });
});
