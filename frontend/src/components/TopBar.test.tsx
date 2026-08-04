import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { TopBar } from "./TopBar";

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", chapter: 1, selectedNodeId: null });
});

describe("顶栏", () => {
  it("只能从已经存在的章节中切换", async () => {
    const user = userEvent.setup();
    renderWithApi(<TopBar />);

    const chapter = await screen.findByRole("combobox", { name: "当前章节" });
    await waitFor(() => {
      expect(within(chapter).getAllByRole("option")).toHaveLength(fixtures.chapters.length);
    });
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();

    await user.selectOptions(chapter, String(fixtures.chapters[1].number));
    expect(useCoords.getState().chapter).toBe(fixtures.chapters[1].number);
  });

  it("只展示可用能力和产品文案，不泄漏演示内容或研发术语", async () => {
    renderWithApi(<TopBar />);

    expect(await screen.findByRole("button", { name: "AI 起草" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "AI 规划" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "本章出场人物" })).toHaveAttribute(
      "placeholder",
      "输入本章出场人物",
    );
    expect(document.body.textContent).not.toMatch(/萧决|顾清音|李管家|M2|valid_from|实验状态/);
  });
});
