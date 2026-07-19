import { create } from "zustand";

// 全局 store 只放**坐标**（§2.3 铁律）：能从 API 拉的绝不进这里。
// projectId 一变 = 整棵 query 树失效；chapter / cast / activeTab / selection 都是坐标。
// cast 存的是作者写的称呼原文（不是 node_id），原样透传给后端的 resolve_cast。

export type Tab = "matrix" | "state" | "constraints" | "check";

interface Coords {
  projectId: string | null;
  chapter: number;
  /** 在场称呼原文，逗号/顿号分隔，原样发给后端。 */
  cast: string;
  activeTab: Tab;
  /** 编辑器当前选中的文本 —— declare 的引语来源（选区声明，零章号）。 */
  selection: string;

  setProject: (id: string) => void;
  setChapter: (n: number) => void;
  setCast: (c: string) => void;
  setTab: (t: Tab) => void;
  setSelection: (s: string) => void;
}

export const useCoords = create<Coords>((set) => ({
  projectId: null,
  chapter: 1,
  cast: "",
  activeTab: "matrix",
  selection: "",

  setProject: (projectId) => set({ projectId }),
  setChapter: (chapter) => set({ chapter }),
  setCast: (cast) => set({ cast }),
  setTab: (activeTab) => set({ activeTab }),
  setSelection: (selection) => set({ selection }),
}));
