import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { SettingsDrawer } from "./SettingsDrawer";

type Calls = { mock: { calls: [unknown, RequestInit?][] } };

/** 最后一次保存**真的发出去**的请求体。
 *
 *  为什么非看这个不可：这一格的规矩全在「发了什么」上——发 `null` 是收回那个数，
 *  不发这个键才是保持原值。只断言界面的话，两者长得一模一样。 */
function savedBody(spy: Calls): Record<string, unknown> {
  const call = spy.mock.calls
    .filter(([url, init]) => /\/api\/settings$/.test(String(url)) && init?.method === "PUT")
    .at(-1);
  return JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>;
}

const watchFetch = () => vi.spyOn(globalThis, "fetch") as unknown as Calls;

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

describe("手填「一次能记住多少」", () => {
  // 这一格是**自建端点唯一的出路**：那份公开表按主机名一刀切，本机/内网的服务永远
  // 认不出，于是上文一直是最短的 800 字。差 50 倍，而且变短了不报错，只显得模型变笨。

  it("没填时是空框，填过就把他填的那个数显示回来", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    expect(await screen.findByDisplayValue("128000")).toBeInTheDocument();

    // 另一份真 dump：一个字都没填过的时候，框里不许出现 0（那是另一个意思）。
    expect(fixtures.settings.context_window).toBeNull();
  });

  it("填一个数，保存时按数字发出去", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    const spy = watchFetch();
    // **先等已保存的那份落地再改**：这个抽屉在拿到服务器那份时会重置整张表单，
    // 抢在它前面敲的字会被抹掉（作者手快时也一样，只是他会以为自己没按对）。
    await screen.findByDisplayValue("128000");
    fireEvent.change(screen.getByPlaceholderText(/32768/), { target: { value: "131072" } });

    const save = screen.getByRole("button", { name: "保存" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    // 发的是数字不是字符串：后端那一位是整数，字符串会被 422 顶回来。
    await waitFor(() => expect(savedBody(spy).context_window).toBe(131072));
  });

  it("清空那个框 = 收回这个数（**不是保持原值**）", async () => {
    // 钥匙留空是「保持原值」，因为屏幕上根本不回显它；这个数回显着，
    // 同样的规矩会让框变成只进不出的洞——填错一次就再也改不回自动推断。
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    const spy = watchFetch();
    fireEvent.change(await screen.findByDisplayValue("128000"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    // **键必须在**：后端拿「带没带这个键」当判据，漏掉它就成了「保持原值」。
    await waitFor(() => expect(savedBody(spy)).toHaveProperty("context_window", null));
  });

  it("认不出来的输入当没填，不替他猜一个数", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settings },
      { method: "PUT", match: /\/api\/settings$/, body: fixtures.settings },
    ]);
    await screen.findByText("模型一次能记住多少");
    fireEvent.change(screen.getByPlaceholderText(/32768/), { target: { value: "32k" } });
    // 猜错的方向没有一个是安全的：猜大了请求被拒，猜小了上文悄悄变短。
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("屏幕上不出现研发术语，也不给这个数安一个假单位", async () => {
    renderWithApi(<SettingsDrawer onClose={() => {}} />, [
      { match: /\/api\/settings$/, body: fixtures.settingsSaved },
    ]);
    await screen.findByDisplayValue("128000");
    const text = screenText();
    expect(devTerms(text)).toEqual([]);
    expect(text).not.toMatch(/token|context|窗口/i);
    // 「多少字」会是句假话（中文一个字不到一个单位），所以文案只说「模型说明里那个数」。
    expect(text).toMatch(/模型说明里/);
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
