// 正在写的那一稿，**流进左边的编辑器**（ADR 0048 的第二半，作者 2026-09-12：
// 「我希望有个编辑的过程在左边也能看到」）。
//
// 写手写的那几十秒里，字一片一片到（`draft_delta`）。同一条流喂给编辑器——作者看着稿子
// 在正文该在的地方长出来；写完之后它**以未保存的样子留在编辑器里**（和作者自己敲的字一样，
// 对着保存版画痕迹：减去红、增加绿，`editMarks.ts`），作者按「保存」才写进书、按「放弃这一稿」
// 就丢掉。稿子进没进书由他那一次保存决定：保存请求带上 `draftId`，后端据此在候选表上记一笔。
// **稿子的字从不在右边画**（作者 2026-09-12：「一定要在左边写」）。
//
// ── 为什么是一个独立的 store，而不是 `useCoords` 里的一个字段 ──────────────
//
// `store.ts` 的铁律是「只放坐标，能从 API 拉的绝不进这里」。这份字**不是坐标**，
// 也**拉不到**（它是一条正在飞的流），而且要被两块隔着整棵组件树的屏幕同时读
// （`ChatPanel` 收事件，`CenterEditor` 画字）。所以它自己一个 store，活到这一轮收场。
//
// ── 三条规矩 ───────────────────────────────────────────────────────────────
//
// 1. **一次只跟一条流。** 一批几稿同时在飞时编辑器只有一个位子——第一条开出来的流进
//    编辑器，其余的留在桌上（右边每稿一行，字不画），作者点「放入编辑器」才换进来（`present`）。
// 2. **收场那一声（`draft_kept` / `draft_failed`）不清字。** 字露完之后由编辑器接手
//    （`placedInEditor`：整份进编辑器、标脏），那时流才放掉；编辑器没开着那一章的那一档，
//    这一轮收场时兜底清（`useRunTurn`）。
// 3. **归约是纯函数。** 「三条流交错着到」这种情形鼠标点不出来，只有单测点得出来。

import { create } from "zustand";
import type { ChatTurnEvent } from "./api/types";

export interface LiveDraft {
  chapter: number;
  /** 同一条字流的片归到一起（后端 `TurnEvent.stream`）。`-1` = 不是流，是作者从桌上
   *  点「放入编辑器」拿进来的一稿（整份一次到手）。 */
  stream: number;
  /** 已经到手的字，**原样**（写手的第一行是那句自述，`visibleBody` 负责不把它画进正文）。 */
  text: string;
  /** 后端已经收场（写好 / 半截 / 没写成）。字仍然留着，见上面第 2 条。 */
  done: boolean;
  /** 候选表里的编号（`draft_kept` 才带；机器码，不上屏）。作者按保存时随请求送回去。 */
  draftId: string;
}

/** 收到一条事件之后，编辑器里那条流该变成什么样。 */
export function liveDraftAfter(prev: LiveDraft | null, event: ChatTurnEvent): LiveDraft | null {
  switch (event.kind) {
    case "draft_started":
      // 已经跟着一条流：第二条留在右边。
      if (prev !== null) return prev;
      return { chapter: event.chapter ?? 0, stream: event.stream, text: "", done: false, draftId: "" };
    case "draft_delta":
      if (prev === null) {
        // 开跑那一声掉了（网抖了一下）：第一片字自己开格，别丢。
        return {
          chapter: event.chapter ?? 0,
          stream: event.stream,
          text: event.text,
          done: false,
          draftId: "",
        };
      }
      if (prev.stream !== event.stream) return prev;
      return { ...prev, text: prev.text + event.text };
    case "draft_kept":
      if (prev === null || prev.stream !== event.stream) return prev;
      return { ...prev, done: true, draftId: event.draft_id ?? "" };
    case "draft_failed":
      if (prev === null || prev.stream !== event.stream) return prev;
      return { ...prev, done: true };
    default:
      return prev;
  }
}

/** 写手那句自述的记号和它常被「正常化」成的那几种（照抄后端 `SELF_NOTE_LOOKALIKES`）。 */
const SELF_NOTE_MARKS = ["〖自述〗", "【自述】", "「自述」", "『自述』", "（自述）", "(自述)", "〖自述】", "【自述〗"];

/**
 * 流里的字该画进正文的那一部分：**去掉开头那句自述**。
 *
 * 写手的第一行是给作者挑版本用的那句话（`〖自述〗……`），后端落盘时会切掉它
 * （`agent/drafting.py::split_self_note`）。编辑器里画的是正文，那一行不该在里面闪一下再消失。
 * 第一行还没写完（还没换行）而它以记号开头时，一个字都不画——不知道它到哪儿结束。
 */
export function visibleBody(text: string): string {
  const leading = text.match(/^\s*/)?.[0].length ?? 0;
  const rest = text.slice(leading);
  if (!SELF_NOTE_MARKS.some((mark) => rest.startsWith(mark))) return text;
  const newline = rest.indexOf("\n");
  if (newline < 0) return "";
  return rest.slice(newline + 1).replace(/^\n+/, "");
}

/** 编辑器里那一稿现在的去处（右边那一行「第几稿」据此说它在哪儿）。 */
export interface PlacedDraft {
  chapter: number;
  draftId: string;
}

interface LiveDraftState {
  draft: LiveDraft | null;
  /** 左边的编辑器**正在画它**（`CenterEditor` 接手了才为真——它开着的正是这一章）。
   *  右边那一行据此只说「正在起草」，不把字画第二遍。 */
  inEditor: boolean;
  /** 写完之后放在编辑器里、还没保存的那一稿。 */
  placed: PlacedDraft | null;
  /** 作者已经按保存写进书的那几稿（这一次打开工作台以来）。 */
  saved: string[];
  apply: (event: ChatTurnEvent) => void;
  /** 作者从桌上点「放入编辑器」：整份一次到手，走和流一样的路。 */
  present: (draft: { chapter: number; draftId: string; text: string }) => void;
  setInEditor: (on: boolean) => void;
  /** 编辑器接手完毕：字全在编辑器里了（未保存），流放掉。 */
  placedInEditor: (placed: PlacedDraft) => void;
  savedFromEditor: (draftId: string) => void;
  discarded: () => void;
  clear: () => void;
}

export const useLiveDraft = create<LiveDraftState>((set) => ({
  draft: null,
  inEditor: false,
  placed: null,
  saved: [],
  apply: (event) => set((s) => ({ draft: liveDraftAfter(s.draft, event) })),
  present: ({ chapter, draftId, text }) =>
    set({ draft: { chapter, stream: -1, text, done: true, draftId } }),
  setInEditor: (on) => set({ inEditor: on }),
  placedInEditor: (placed) => set({ draft: null, inEditor: false, placed }),
  savedFromEditor: (draftId) =>
    set((s) => ({ placed: null, saved: draftId ? [...s.saved, draftId] : s.saved })),
  discarded: () => set({ placed: null }),
  clear: () => set({ draft: null, inEditor: false }),
}));
