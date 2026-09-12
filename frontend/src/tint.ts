// 写作助手放进编辑器的那一稿，**哪些字是新的、哪些字是改的**（ADR 0048 的第三半，作者
// 2026-09-12：「AI 编写的或者修改的……增加的绿色的底，修改的浅红色的底，点击保存这个底就消失」）。
//
// 判据是**段**：一段（一行）在旧稿里原样有 = 不涂；旧稿里没有 = 新增（绿）；旧稿里有一段
// 和它长得像（同一个位置附近被换掉的那一段）= 修改（浅红）。不做字级 diff——作者说过
// 红绿满屏的正文看着乱，段级的底色只标「这一段动过」，读得下去。
//
// 这一层不碰 DOM：给的是新稿坐标系里的 `[from, to)` 区间，画在哪儿归 `CodeEditor`。

import { lineDiff } from "./diff";

export type TintKind = "added" | "changed";

export interface TintRange {
  from: number;
  to: number;
  kind: TintKind;
}

/** 两段字像不像：字二元组的 Dice 系数（中文不分词，二元组够用）。 */
export function similarity(a: string, b: string): number {
  if (a === b) return 1;
  if (a.length < 2 || b.length < 2) return 0;
  const grams = (s: string) => {
    const out = new Map<string, number>();
    for (let i = 0; i + 1 < s.length; i++) {
      const g = s.slice(i, i + 2);
      out.set(g, (out.get(g) ?? 0) + 1);
    }
    return out;
  };
  const ga = grams(a);
  const gb = grams(b);
  let shared = 0;
  for (const [g, n] of ga) shared += Math.min(n, gb.get(g) ?? 0);
  return (2 * shared) / (a.length - 1 + (b.length - 1));
}

/** 「像」的门槛：一半以上的二元组重合，才算同一段改出来的。 */
export const CHANGED_THRESHOLD = 0.5;

/**
 * 新稿里该涂底色的区间。**坐标是新稿的**（`from`/`to` 是 `next` 的 code unit 下标）。
 *
 * 同一个 hunk（连着的一串删 / 增）里，增的那一行和删的哪一行像就算「改」；谁都不像就是
 * 「新增」。空行不涂。相邻同色的区间并成一段（少画几百个 mark）。
 */
export function tintRanges(prev: string, next: string): TintRange[] {
  const lines = lineDiff(prev, next);
  // 新稿每一行的起点下标。
  const nextLines = next.split("\n");
  const starts: number[] = [];
  let at = 0;
  for (const line of nextLines) {
    starts.push(at);
    at += line.length + 1;
  }

  const out: TintRange[] = [];
  let j = 0; // 走到新稿的第几行
  let i = 0; // 走到 diff 的第几条
  while (i < lines.length) {
    const entry = lines[i];
    if (entry.t === "same") {
      i++;
      j++;
      continue;
    }
    // 一个 hunk：连着的 del / add。
    const dels: string[] = [];
    const adds: { line: string; index: number }[] = [];
    while (i < lines.length && lines[i].t !== "same") {
      if (lines[i].t === "del") dels.push(lines[i].s);
      else adds.push({ line: lines[i].s, index: j++ });
      i++;
    }
    for (const add of adds) {
      if (add.line.trim() === "") continue;
      const changed = dels.some((d) => d.trim() !== "" && similarity(d, add.line) >= CHANGED_THRESHOLD);
      const from = starts[add.index];
      const to = from + add.line.length;
      const kind: TintKind = changed ? "changed" : "added";
      const last = out[out.length - 1];
      // 只隔着换行（和空行）的同色区间并成一段。
      if (last && last.kind === kind && next.slice(last.to, from).trim() === "") last.to = to;
      else out.push({ from, to, kind });
    }
  }
  return out;
}
