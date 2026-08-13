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

describe("更新模型信息（那颗按钮）", () => {
  const settings = { match: /\/api\/settings$/, body: fixtures.settingsSaved };

  it("默认只说它是干什么的 —— **不自动跑**", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [settings]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    expect(screen.getByRole("button", { name: "更新模型信息" })).toBeEnabled();
    expect(document.body.textContent).toMatch(/能记住多长的上文/);
    // 屏幕上不许出现这份数据的来路（作者不需要认识 litellm / GitHub）。
    expect(document.body.textContent).not.toMatch(/litellm|github|json|快照/i);
  });

  it("点完必须说出变了什么 —— 不出声的按钮等于没生效", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      settings,
      {
        method: "POST",
        match: /model-windows\/refresh$/,
        body: {
          fetched: "2026-11-01",
          total: 2206,
          added: 12,
          changed: 3,
          removed: 1,
          path: "/home/author/.config/novel-harness/model_windows.json",
        },
      },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    fireEvent.click(screen.getByRole("button", { name: "更新模型信息" }));

    await waitFor(() => {
      expect(document.body.textContent).toContain("2206");
    });
    // **`changed` 必须摆出来**：一个模型的窗口被上游改小了，作者的上文会跟着变短，
    // 而那件事没有别的观测点。
    expect(document.body.textContent).toMatch(/变化 3/);
    expect(document.body.textContent).toMatch(/新增 12/);
  });

  it("拉不到时说人话，并且说清楚「原来那份还在用」", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      settings,
      {
        method: "POST",
        match: /model-windows\/refresh$/,
        status: 502,
        body: { detail: "没能拉到那份公开的模型表（URLError）。原来那份还在用，什么都没改。网络好了再点一次。" },
      },
    ]);
    await screen.findByDisplayValue("deepseek-v4-flash");
    fireEvent.click(screen.getByRole("button", { name: "更新模型信息" }));

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/原来那份还在用/);
    });
    // 失败之后按钮要能再点 —— 网络好了他会想立刻重试。
    expect(screen.getByRole("button", { name: "更新模型信息" })).toBeEnabled();
  });
});
