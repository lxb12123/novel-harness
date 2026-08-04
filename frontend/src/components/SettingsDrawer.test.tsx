import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { SettingsDrawer } from "./SettingsDrawer";

describe("AI 设置（BYOK）", () => {
  it("用自然语言说明已保存的地址、模型和密钥", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    expect(await screen.findByDisplayValue("https://api.deepseek.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("deepseek-v4-flash")).toBeInTheDocument();
    expect(screen.getByText("API 密钥")).toBeInTheDocument();
    expect(screen.getByText(/不会显示完整密钥/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/M2|ADR|kill-gate|修正案|实验状态/);
    // 完整钥匙永不进 DOM：只有「已设置（****…）」这种预览。
    expect(document.body.textContent).not.toContain("sk-contract-key");
  });

  it("新钥匙保存成功后会关闭抽屉", async () => {
    const onClose = vi.fn();
    renderWithApi(<SettingsDrawer onClose={onClose} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    fireEvent.change(screen.getByPlaceholderText(/留空则保留/), {
      target: { value: "sk-new-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
