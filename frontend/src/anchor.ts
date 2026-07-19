import type { Anchor } from "./store";

// 锚三元组 → 编辑器里的 [start, end) 字符区间。**按 quote 重寻，不用 offset**（ADR 0006）：
// 作者在别处改了字导致段号漂移时，靠 quote 重搜自愈——这正是双指针在前端的兑现。
//
// 段的定义要和后端 text.paragraphs() 一致（今天 == splitlines）。textarea 用 \n，
// 所以 doc.split("\n") 与之对齐，第 i 段的起始 offset = 前 i 段各自长度 + i 个换行。

/** 非重叠、左起数第 k 次（0-based），与后端 str.count / split 语义一致（不是 indexOf+1 的重叠计数）。 */
function nthIndex(hay: string, needle: string, k: number): number {
  let idx = hay.indexOf(needle);
  for (let n = 0; n < k && idx >= 0; n++) idx = hay.indexOf(needle, idx + needle.length);
  return idx;
}

export function locate(doc: string, a: Anchor): { start: number; end: number } | null {
  const q = a.quote_text;
  if (!q) return null;
  const paras = doc.split("\n");

  // 先在锚指的那一段里找（正常情况）。
  if (a.para_index >= 0 && a.para_index < paras.length) {
    let base = 0;
    for (let i = 0; i < a.para_index; i++) base += paras[i].length + 1; // +1 = 那个 \n
    const local = nthIndex(paras[a.para_index], q, a.occurrence_k);
    if (local >= 0) return { start: base + local, end: base + local + q.length };
  }

  // 段号漂了（作者改过正文）→ 全文按第 k 次兜底重寻。找不到就返回 null，让 UI 说人话。
  const g = nthIndex(doc, q, a.occurrence_k);
  return g >= 0 ? { start: g, end: g + q.length } : null;
}
