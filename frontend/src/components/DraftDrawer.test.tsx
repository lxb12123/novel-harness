import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithApi } from "../test/harness";
import { useCoords } from "../store";
import { DraftDrawer } from "./DraftDrawer";

beforeEach(() => {
  useCoords.setState({
    projectId: "project:ID1",
    chapter: 1,
    selectedNodeId: null,
    cast: "主角,访客",
  });
});

const DRAFT_BODY = {
  experimental: true,
  note: "实验状态：未经 kill-gate 裁决，图谱约束是否有效尚未证实（修正案 7）。",
  text: "风穿过未关的窗。",
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

function renderedCopy() {
  const placeholders = [...document.querySelectorAll<HTMLElement>("[placeholder]")]
    .map((element) => element.getAttribute("placeholder") ?? "")
    .join(" ");
  const options = [...document.querySelectorAll("option")]
    .map((option) => option.textContent ?? "")
    .join(" ");
  return `${document.body.textContent ?? ""} ${placeholders} ${options}`;
}

describe("AI 起草", () => {
  it("只展示作者需要的输入、草稿和长度，并固定使用生产起草路径", async () => {
    renderWithApi(<DraftDrawer onClose={vi.fn()} />, [
      { method: "POST", match: /\/draft$/, body: DRAFT_BODY },
    ]);
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(renderedCopy()).not.toMatch(
      /M2|ADR|kill-gate|修正案|实验状态|X0|X1|X2|萧决|顾清音|李管家|苏挽|魔尊|北荒|血脉秘密/,
    );
    expect(screen.getByPlaceholderText(/文白夹杂/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("描述这一场的目标、冲突和转折"), {
      target: { value: "让主角在冲突中作出选择。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "起草" }));

    expect(await screen.findByText("风穿过未关的窗。")).toBeInTheDocument();
    expect(screen.getByText("10 字")).toBeInTheDocument();
    expect(renderedCopy()).not.toMatch(
      /实验状态|kill-gate|attempts|deepseek-v4-flash|stop|tokens|under/,
    );

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const draftCall = fetchSpy.mock.calls.find(([url]) => /\/draft$/.test(String(url)));
    expect(draftCall).toBeDefined();
    expect(JSON.parse(String(draftCall?.[1]?.body))).toMatchObject({ form: "X1" });
  });
});
