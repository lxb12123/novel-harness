import { create } from "zustand";

// 界面语言：**看屏幕的人用什么语言**，跟这本书自己写的是什么语言无关（维护者裁定 B，
// 2026-08-27）。`project.language` 决定「模型写正文用什么语言」；这个 store 决定
// 「按钮、菜单、错误框上摆的是哪种字」——两件事分得开是因为一个中文作者可以写一本
// 英文小说（网文出海就是这个形状），这时他要的是中文菜单包着英文正文，界面语言
// 跟着书走会把他锁进一种他没选过的界面语言。
//
// **不挂在任何一本书上**，所以书架页（列着多本、可能语言各不相同的书）不必回答
// 「这一页用哪本书的语言」——它跟当前打开哪本书完全无关，这条问题本身就不存在了。
//
// 这不是坐标（`useCoords` 只放坐标），也不是某本书的桌面摆法（`bookshelf.ts` 那种），
// 是**这台设备上这个人**的偏好，所以另起一个 store + localStorage（同 `layout.ts`/
// `bookshelf.ts` 的既有先例）。

export type Language = "zh" | "en";

const STORAGE_KEY = "nh.ui-language.v1";

function fromSystem(): Language {
  const raw = typeof navigator !== "undefined" ? navigator.language : "";
  // 认不出的语言退回 zh，同这个仓库别处的默认（`ToolContext.language` 默认 ZH）。
  return raw.toLowerCase().startsWith("en") ? "en" : "zh";
}

function isLanguage(value: unknown): value is Language {
  return value === "zh" || value === "en";
}

function readStoredLanguage(): Language | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    return isLanguage(raw) ? raw : null;
  } catch {
    return null; // 隐私模式读不到，退回系统语言，不是崩溃
  }
}

function writeStoredLanguage(language: Language): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, language);
  } catch {
    // 存不下就算了，切一次语言不该因此崩掉（同 layout.ts 的既有取舍）。
  }
}

interface LanguageStore {
  language: Language;
  setLanguage: (language: Language) => void;
}

export const useLanguage = create<LanguageStore>((set) => ({
  language: readStoredLanguage() ?? fromSystem(),
  setLanguage: (language) => {
    writeStoredLanguage(language);
    set({ language });
  },
}));
