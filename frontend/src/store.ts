import { create } from "zustand";

/** 锚三元组（ADR 0006，永不 offset）：段号 + 引语 + 第几次。R4 的 Issue / 证据都用它。 */
export interface Anchor {
  para_index: number;
  quote_text: string;
  occurrence_k: number;
}

// 全局 store 只放**坐标**（§2.3 铁律）：能从 API 拉的绝不进这里。
// projectId 一变 = 整棵 query 树失效；chapter / cast / activeTab / selection 都是坐标。
// cast 存的是作者写的称呼原文（不是 node_id），原样透传给后端的 resolve_cast。

export type Tab = "matrix" | "state" | "constraints" | "graph" | "check";

interface Coords {
  projectId: string | null;
  chapter: number;
  /** 在场称呼原文，逗号/顿号分隔，原样发给后端。 */
  cast: string;
  activeTab: Tab;
  /** 编辑器当前选中的文本 —— declare 的引语来源（选区声明，零章号）。 */
  selection: string;
  /** 局部图 / 状态卡的中心节点 id（已解析）。roster 点选或选区 resolve 后设。 */
  selectedNodeId: string | null;
  /** 待跳转高亮的锚：点 R4 issue 时设，编辑器消费后清。**按 quote 重寻，不存 offset。** */
  highlight: Anchor | null;

  setProject: (id: string) => void;
  setChapter: (n: number) => void;
  setCast: (c: string) => void;
  setTab: (t: Tab) => void;
  setSelection: (s: string) => void;
  /** 设中心节点并跳到局部图 tab（点一个人就想看他的图，是同一个动作）。 */
  focusNode: (id: string) => void;
  setHighlight: (a: Anchor | null) => void;
}

export const useCoords = create<Coords>((set) => ({
  projectId: null,
  chapter: 1,
  cast: "",
  activeTab: "matrix",
  selection: "",
  selectedNodeId: null,
  highlight: null,

  setProject: (projectId) => set({ projectId }),
  setChapter: (chapter) => set({ chapter }),
  setCast: (cast) => set({ cast }),
  setTab: (activeTab) => set({ activeTab }),
  setSelection: (selection) => set({ selection }),
  focusNode: (selectedNodeId) => set({ selectedNodeId, activeTab: "graph" }),
  setHighlight: (highlight) => set({ highlight }),
}));
