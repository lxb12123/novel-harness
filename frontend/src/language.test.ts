import { beforeEach, describe, expect, it, vi } from "vitest";

describe("界面语言：默认跟系统、认不出退中文、能记住手动切换", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it("没存过、系统是英文 → 默认 en", async () => {
    vi.stubGlobal("navigator", { language: "en-US" });
    const { useLanguage } = await import("./language");
    expect(useLanguage.getState().language).toBe("en");
  });

  it("没存过、系统是别的语言（法语）→ 退回 zh", async () => {
    vi.stubGlobal("navigator", { language: "fr-FR" });
    const { useLanguage } = await import("./language");
    expect(useLanguage.getState().language).toBe("zh");
  });

  it("手动切换之后，存下来的值比系统语言优先", async () => {
    vi.stubGlobal("navigator", { language: "en-US" });
    const { useLanguage } = await import("./language");
    useLanguage.getState().setLanguage("zh");
    expect(useLanguage.getState().language).toBe("zh");

    // 模拟下一次打开：重新 import 这个模块（重新读 localStorage）
    vi.resetModules();
    const { useLanguage: reloaded } = await import("./language");
    expect(reloaded.getState().language).toBe("zh");
  });

  it("localStorage 存了坏值（不是 zh/en）→ 不崩，退回系统语言", async () => {
    localStorage.setItem("nh.ui-language.v1", "not-a-language");
    vi.stubGlobal("navigator", { language: "en-US" });
    const { useLanguage } = await import("./language");
    expect(useLanguage.getState().language).toBe("en");
  });
});
