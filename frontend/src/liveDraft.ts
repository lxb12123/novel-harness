// 正在写的那一稿，**流进左边的编辑器**（ADR 0048 的第二半，作者 2026-09-12：
// 「我希望有个编辑的过程在左边也能看到」）。
//
// 写手写的那几十秒里，字一片一片到（`draft_delta`）。以前它只长在右边对话里的一格，
// 写完那一刻左边才换；现在同一条流同时喂给编辑器——作者看着稿子在正文该在的地方长出来，
// 写完落盘之后编辑器换成磁盘上那一版，中间没有一帧空白。
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
//    编辑器，其余的仍在右边各自一格（它们落盘之后最后一稿留在正文里，同 ADR 0048）。
// 2. **收场那一声（`draft_kept` / `draft_failed`）不清字。** 落盘之后磁盘上那一版要几十
//    毫秒才重取回来，这几十毫秒里清掉字就是先闪回旧稿再换新稿。字留到编辑器拿到新正文
//    （`CenterEditor` 看见新的 sha 才清），或者这一轮收场（`useRunTurn` 兜底清）。
// 3. **归约是纯函数。** 「三条流交错着到」这种情形鼠标点不出来，只有单测点得出来。

import { create } from "zustand";
import type { ChatTurnEvent } from "./api/types";

export interface LiveDraft {
  chapter: number;
  /** 同一条字流的片归到一起（后端 `TurnEvent.stream`）。 */
  stream: number;
  /** 已经到手的字，**原样**（写手的第一行是那句自述，`visibleBody` 负责不把它画进正文）。 */
  text: string;
  /** 后端已经收场（写好 / 半截 / 没写成）。字仍然留着，见上面第 2 条。 */
  done: boolean;
}

/** 收到一条事件之后，编辑器里那条流该变成什么样。 */
export function liveDraftAfter(prev: LiveDraft | null, event: ChatTurnEvent): LiveDraft | null {
  switch (event.kind) {
    case "draft_started":
      // 已经跟着一条流：第二条留在右边。
      if (prev !== null) return prev;
      return { chapter: event.chapter ?? 0, stream: event.stream, text: "", done: false };
    case "draft_delta":
      if (prev === null) {
        // 开跑那一声掉了（网抖了一下）：第一片字自己开格，别丢。
        return { chapter: event.chapter ?? 0, stream: event.stream, text: event.text, done: false };
      }
      if (prev.stream !== event.stream) return prev;
      return { ...prev, text: prev.text + event.text };
    case "draft_kept":
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

interface LiveDraftState {
  draft: LiveDraft | null;
  /** 左边的编辑器**正在画它**（`CenterEditor` 接手了才为真；作者手上有没保存的字时它不接）。
   *  右边那一格据此只留标题行，不把同一段字画两遍。 */
  inEditor: boolean;
  apply: (event: ChatTurnEvent) => void;
  setInEditor: (on: boolean) => void;
  clear: () => void;
}

export const useLiveDraft = create<LiveDraftState>((set) => ({
  draft: null,
  inEditor: false,
  apply: (event) => set((s) => ({ draft: liveDraftAfter(s.draft, event) })),
  setInEditor: (on) => set({ inEditor: on }),
  clear: () => set({ draft: null, inEditor: false }),
}));
