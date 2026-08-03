import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { DraftDrawer } from "./DraftDrawer";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "萧决,苏挽",
  });
});

const DRAFT_BODY = {
  experimental: true,
  note: "实验状态：未经 kill-gate 裁决，图谱约束是否有效尚未证实（修正案 7）。",
  text: "萧决道：「此剑无名。」",
  length: {
    language: "zh",
    unit: "characters",
    actual_units: 10,
    status: "under",
  },
  attempts: 2,
  model: "deepseek-v4-flash",
  finish_reason: "stop",
  prompt_tokens: 100,
  completion_tokens: 200,
};

describe("AI 起草（实验状态）", () => {
  it("带实验标注；起草成功后展示草稿与长度", async () => {
    renderWithApi(<DraftDrawer onClose={vi.fn()} />, [
      { method: "POST", match: /\/draft$/, body: DRAFT_BODY },
    ]);
    expect(screen.getAllByText(/实验状态/).length).toBeGreaterThan(0);
    expect(screen.getByPlaceholderText(/文白夹杂/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/写苏挽回府/), {
      target: { value: "萧决看剑。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "起草" }));

    expect(await screen.findByText(/此剑无名/)).toBeInTheDocument();
    expect(screen.getAllByText(/未经 kill-gate 裁决/).length).toBeGreaterThan(0);
    expect(screen.getByText(/10 characters/)).toBeInTheDocument();
  });
});
