import { create } from "zustand";

/** 锚三元组（ADR 0006，永不 offset）：段号 + 引语 + 第几次。R4 的 Issue / 证据都用它。 */
export interface Anchor {
  para_index: number;
  quote_text: string;
  occurrence_k: number;
}

// 全局 store 只放**坐标**（§2.3 铁律）：能从 API 拉的绝不进这里。
// projectId 一变 = 整棵 query 树失效；chapter / cast / activeTab 都是坐标。
// cast 存的是作者写的称呼原文（不是 node_id），原样透传给后端的 resolve_cast。

export type Tab =
  | "roster"
  | "state"
  | "constraints"
  | "graph"
  | "evidence"
  | "summary"
  | "check"
  | "review"
  | "notifications";
/** 当前页：工作台 / 活动记录。
 *
 *  **`"prep"`（章节准备）2026-08-13 删了**，理由记在 `TopBar.tsx` 那段注释里
 *  （三张读卡是右栏的第二个入口，两个表单写完没人读）。 */
export type Page = "workbench" | "log";
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
  /** 局部图 / 状态卡的中心节点 id（已解析）。花名册点选 / 局部图点选后设。 */
  selectedNodeId: string | null;
  /** 待跳转高亮的锚：点 R4 issue 时设，编辑器消费后清。**按 quote 重寻，不存 offset。** */
  highlight: Anchor | null;
  /** 当前页：工作台 / 章节准备（写第 N 章前的确定性简报）/ 活动记录。 */
  page: Page;
  /** 日志页跳过来要高亮的那一格。换章 / 换 tab 就清掉——它是一次跳转的余温，不是常驻状态。 */
  /** 日志页跳过来要打开的那条**已生效事件**（改它的知情 / 在场名单）。
   *  同 `focusCell`：id 来自后端的 `jump.event_id`，不是从那行字里认出来的。 */
  focusEventId: string | null;
  /** 日志页跳过来要打开的那条**自动 Canon 边**（改它 / 撤回它 / 改归属）。
   *  同 `focusEventId`：id 来自后端的 `jump.edge_id`（Task 8 / ADR 0032）。 */
  focusEdgeId: string | null;
  /** 中栏右半边的写作助手开着没有。**默认关着**——它是作者要的时候才展开的一块屏幕，
   *  不是常驻的（同「活动记录」那条：入口不是通知）。
   *
   *  **它是坐标不是偏好**，所以在这儿而不在 `SplitPanes` 的 localStorage 里：
   *  开合由顶栏那颗按钮切、由中栏读，两处隔着整棵组件树。那一半**多宽**才是偏好，
   *  留在 `layout.ts`。 */
  chatOpen: boolean;
  /** 现在摊开的是哪一段对话。作者可以同时留着好几段，各自 resume（ADR 0019）。
   *  `null` = 还没挑（面板会自己停在最近说过话的那一段）。 */
  chatId: string | null;
  /** 光标已经替**哪本书**落过位了。`null` = 一本都还没落过（刚开页面）。
   *
   *  「这是不是刚换的一本书」全靠它认（`chapterOnOpen` 的 `switched`）。它原先是 App 里的
   *  一个 ref，搬进来是因为**换书的人不只 App 一个**：左栏点另一本书的某一章时，
   *  光标在那一下就已经由作者亲手定好了，得有个地方说出来。 */
  cursorFor: string | null;

  setProject: (id: string) => void;
  setChapter: (n: number) => void;
  /** 打开 / 关掉 Canon 边纠错弹窗（Task 8）。`edgeId` 来自后端的 `jump.edge_id`。 */
  setEdgeFocus: (edgeId: string | null) => void;
  /** 连书带章一起翻过去 —— 左栏书架上点了**另一本**书的章目录。
   *
   *  **`cursorFor` 必须在同一次 set 里一起置上。** 不置的话 App 那个 effect 会把这一下
   *  认成一次「刚换的书」，于是 `chapterOnOpen` 把作者刚点的那一章顶成那本书的最后一章：
   *  他点第 1 章，屏幕上出来第 722 章。 */
  openBookAt: (id: string, n: number) => void;
  /** 记下「这本书的光标已经落好了」。**只有 App 里那个落位 effect 该调它。** */
  markCursor: (id: string) => void;
  setCast: (c: string) => void;
  setTab: (t: Tab) => void;
  /** 设中心节点并跳到局部图 tab（点一个人就想看他的图，是同一个动作）。 */
  focusNode: (id: string) => void;
  setHighlight: (a: Anchor | null) => void;
  setPage: (p: Page) => void;
  toggleChat: () => void;
  setChat: (id: string | null) => void;
  /** 从活动记录跳去某个模块：中栏回工作台、右栏切到那一格、高亮那一格 / 打开那条事件。
   *
   *  **章号不在这里换**：换章要走 `useOpenChapter`（离开的那一章交后台整理），
   *  全项目只有那一个换章入口。这里只管「跳过去之后停在哪一格」。 */
  jumpFromActivity: (to: {
    tab: Tab | null;
    eventId: string | null;
    edgeId: string | null;
    /** 后端 `jump.cast` 拼出来的那一串，原样落进 `castInclude`（**不是 `cast`**）。
     *  空串 = 这一档给不出坐标，面板照旧按推导算。
     *  前端不在这里挑人、不在这里合并——两者都是「替引擎决定谁在场」。 */
  }) => void;
}

export const useCoords = create<Coords>((set) => ({
  projectId: null,
  chapter: 1,
  cast: "",
  castInclude: "",
  // 默认停在花名册：打开一本书先看见「这本书里有谁」，其余几格都是「其中某个人怎么样」。
  activeTab: "roster",
  selectedNodeId: null,
  highlight: null,
  page: "workbench",
  focusEventId: null,
  focusEdgeId: null,
  chatOpen: false,
  chatId: null,
  cursorFor: null,

  // 换书要**把摊开的那段对话一起放下**：`chat_session` 是按书存的，
  // 留着上一本书的 id 就是一次必然 404 的详情请求，而屏幕上会是一句
  // 「这段对话不在了」——一句完全对不上作者刚做的事的话。
  setProject: (projectId) => set({ projectId, chatId: null }),
  setEdgeFocus: (focusEdgeId) => set({ focusEdgeId }),
  // 换章 / 换 tab 都会让「刚才跳过来的是这一格」失效，留着它就是在别的章上画一个假高亮。
  // `castInclude` 跟着一起清：它是那次跳转的余温（把那一行推回表上），
  // 换了章之后它指的是另一章的表，留着只会凭空多出一行谁也没要求过的人。
  setChapter: (chapter) =>
    set({ chapter, focusEventId: null, focusEdgeId: null, castInclude: "" }),
  // 换书那份清理（`chatId`）+ 换章那份清理（余温三件）**一次做完**：分两步 set 的话，
  // 中间那一帧是「新书 + 旧章号」，而屏幕会照着它去拉一次别的书的正文。
  openBookAt: (projectId, chapter) =>
    set({
      projectId,
      chapter,
      cursorFor: projectId,
      chatId: null,
      focusEventId: null,
      focusEdgeId: null,
      castInclude: "",
    }),
  markCursor: (cursorFor) => set({ cursorFor }),
  // 作者亲手选了一场 = 他接管了「看谁」。这时还留着系统加的那个人，
  // 他选的那一场就不是他看到的那一场了。
  setCast: (cast) => set({ cast, castInclude: "" }),
  setTab: (activeTab) =>
    set({ activeTab, focusEventId: null, focusEdgeId: null, castInclude: "" }),
  focusNode: (selectedNodeId) => set({ selectedNodeId, activeTab: "graph" }),
  setHighlight: (highlight) => set({ highlight }),
  setPage: (page) => set({ page }),
  // 关掉不清 `chatId`：再打开时回到刚才那一段，作者不用重找。
  //
  // 这儿原本还有一条「打开时如果人在章节准备页，顺手回工作台」——那一页换掉的是整块中栏，
  // 助手在那儿没有位置。**那一页删了，这条跟着删**：剩下的两页（工作台 / 活动记录）
  // 换掉的都只有中栏左半边，助手照旧在它右边开着。
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
  setChat: (chatId) => set({ chatId }),
  jumpFromActivity: ({ tab, eventId, edgeId }) =>
    set((s) => ({
      page: "workbench",
      activeTab: tab ?? s.activeTab,
      // **上一次点场景块留下的过滤一定要被清掉**：要看的那一格可能根本不在里面，
      // 而那是作者为另一件事设的，不是为这次跳转设的。
      cast: "",
      castInclude: "",
      focusEventId: eventId,
      focusEdgeId: edgeId,
    })),
}));
