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

export type Tab =
  | "roster"
  | "matrix"
  | "state"
  | "constraints"
  | "graph"
  | "evidence"
  | "check"
  | "review";
export type Page = "workbench" | "prep" | "log";

/** 认知矩阵里的一格（人物 × 秘密）。日志页跳过来时用它高亮「就是这一格」。
 *  两个 id **都来自后端的 `jump`**，不是从标题里认出来的名字。 */
export interface FocusCell {
  character_id: string;
  secret_id: string;
}

interface Coords {
  projectId: string | null;
  chapter: number;
  /** 在场称呼原文，逗号/顿号分隔，原样发给后端。**这是一个过滤**（「只看这几个人」），
   *  唯一来源是作者在正文里亲手标的场景块——收窄是他自己要的。系统不许往这里写。 */
  cast: string;
  /** 「这几个人**也要**算在场里」，原样发给后端的 `?include=`。**只加不减。**
   *
   *  和 `cast` 分成两个字段是这次改动的全部安全性所在：日志页的跳转坐标是**系统**给的、
   *  而且按定义只知道一个人。把它写进 `cast` 就是让系统替作者做了一次收窄，而
   *  `must_not_reveal` 的判据是「在场的人里至少有一个还不知道」——少一个人 = 少一批禁令
   *  = fail-open（ADR 0018 §3）。后端 `_effective_cast` 那段注释记着实测形态。 */
  castInclude: string;
  activeTab: Tab;
  /** 编辑器当前选中的文本 —— declare 的引语来源（选区声明，零章号）。 */
  selection: string;
  /** 局部图 / 状态卡的中心节点 id（已解析）。roster 点选或选区 resolve 后设。 */
  selectedNodeId: string | null;
  /** 待跳转高亮的锚：点 R4 issue 时设，编辑器消费后清。**按 quote 重寻，不存 offset。** */
  highlight: Anchor | null;
  /** 当前页：工作台 / 章节准备（写第 N 章前的确定性简报）/ 活动记录。 */
  page: Page;
  /** 日志页跳过来要高亮的那一格。换章 / 换 tab 就清掉——它是一次跳转的余温，不是常驻状态。 */
  focusCell: FocusCell | null;
  /** 日志页跳过来要打开的那条**已生效事件**（改它的知情 / 在场名单）。
   *  同 `focusCell`：id 来自后端的 `jump.event_id`，不是从那行字里认出来的。 */
  focusEventId: string | null;

  setProject: (id: string) => void;
  setChapter: (n: number) => void;
  setCast: (c: string) => void;
  setTab: (t: Tab) => void;
  setSelection: (s: string) => void;
  /** 设中心节点并跳到局部图 tab（点一个人就想看他的图，是同一个动作）。 */
  focusNode: (id: string) => void;
  setHighlight: (a: Anchor | null) => void;
  setPage: (p: Page) => void;
  /** 从活动记录跳去某个模块：中栏回工作台、右栏切到那一格、高亮那一格 / 打开那条事件。
   *
   *  **章号不在这里换**：换章要走 `useOpenChapter`（离开的那一章交后台整理），
   *  全项目只有那一个换章入口。这里只管「跳过去之后停在哪一格」。 */
  jumpFromActivity: (to: {
    tab: Tab | null;
    cell: FocusCell | null;
    eventId: string | null;
    /** 后端 `jump.cast` 拼出来的那一串，原样落进 `castInclude`（**不是 `cast`**）。
     *  空串 = 这一档给不出坐标，面板照旧按推导算。
     *  前端不在这里挑人、不在这里合并——两者都是「替引擎决定谁在场」。 */
    include: string;
  }) => void;
}

export const useCoords = create<Coords>((set) => ({
  projectId: null,
  chapter: 1,
  cast: "",
  castInclude: "",
  // 默认停在花名册：打开一本书先看见「这本书里有谁」，其余几格都是「其中某个人怎么样」。
  activeTab: "roster",
  selection: "",
  selectedNodeId: null,
  highlight: null,
  page: "workbench",
  focusCell: null,
  focusEventId: null,

  setProject: (projectId) => set({ projectId }),
  // 换章 / 换 tab 都会让「刚才跳过来的是这一格」失效，留着它就是在别的章上画一个假高亮。
  // `castInclude` 跟着一起清：它是那次跳转的余温（把那一行推回表上），
  // 换了章之后它指的是另一章的表，留着只会凭空多出一行谁也没要求过的人。
  setChapter: (chapter) =>
    set({ chapter, focusCell: null, focusEventId: null, castInclude: "" }),
  // 作者亲手选了一场 = 他接管了「看谁」。这时还留着系统加的那个人，
  // 他选的那一场就不是他看到的那一场了。
  setCast: (cast) => set({ cast, castInclude: "" }),
  setTab: (activeTab) => set({ activeTab, focusCell: null, focusEventId: null, castInclude: "" }),
  setSelection: (selection) => set({ selection }),
  focusNode: (selectedNodeId) => set({ selectedNodeId, activeTab: "graph" }),
  setHighlight: (highlight) => set({ highlight }),
  setPage: (page) => set({ page }),
  jumpFromActivity: ({ tab, cell, eventId, include }) =>
    set((s) => ({
      page: "workbench",
      activeTab: tab ?? s.activeTab,
      // **上一次点场景块留下的过滤一定要被清掉**：要看的那一格可能根本不在里面，
      // 而那是作者为另一件事设的，不是为这次跳转设的。
      cast: "",
      // 后端给的坐标只**加**在推导出来的在场上（认知矩阵那一档给的是「这一格上那个人
      // 的称呼」——他可能在这一章正文里一次都没被点名，ADR 0018 推不出他那一行）。
      // 别的档给空串。**前端不在这里挑人，也不在这里合并**。
      castInclude: include,
      focusCell: cell,
      focusEventId: eventId,
    })),
}));
