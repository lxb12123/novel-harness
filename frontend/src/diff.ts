// 行级 diff（LCS）——版本对比用，2026-09-12 起也给编辑器里「没保存的改动的痕迹」用
// （`editMarks.ts`）。把 old → current 表达成 same/add/del 的行序列。
// del = 从那一版删掉的、add = 现在这版新增的。O(n·m)，一章几百行足够。

export type DiffLine = { t: "same" | "add" | "del"; s: string };

export function lineDiff(oldText: string, curText: string): DiffLine[] {
  const A = oldText.split("\n");
  const B = curText.split("\n");
  // **先剥掉两头相同的行。** 那张 n·m 的表是按行数平方长的：作者改一段字时两份正文只差
  // 中间那几行，而编辑器里的痕迹**每敲一个键就算一遍**——不剥的话一章几百行 = 几十万格
  // 一个键，而且每次都要新分配一张表。剥完之后中间那截通常只有几行。
  let head = 0;
  while (head < A.length && head < B.length && A[head] === B[head]) head++;
  let tail = 0;
  while (
    tail < A.length - head &&
    tail < B.length - head &&
    A[A.length - 1 - tail] === B[B.length - 1 - tail]
  ) {
    tail++;
  }
  const out: DiffLine[] = A.slice(0, head).map((s) => ({ t: "same", s }));
  out.push(...lcs(A.slice(head, A.length - tail), B.slice(head, B.length - tail)));
  for (const s of A.slice(A.length - tail)) out.push({ t: "same", s });
  return out;
}

function lcs(A: string[], B: string[]): DiffLine[] {
  const n = A.length;
  const m = B.length;
  if (n === 0) return B.map((s) => ({ t: "add", s }));
  if (m === 0) return A.map((s) => ({ t: "del", s }));

  // dp[i][j] = A[i:] 与 B[j:] 的最长公共子序列长度
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) out.push({ t: "same", s: A[i++] }), j++;
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: "del", s: A[i++] });
    else out.push({ t: "add", s: B[j++] });
  }
  while (i < n) out.push({ t: "del", s: A[i++] });
  while (j < m) out.push({ t: "add", s: B[j++] });
  return out;
}

export function diffStats(lines: DiffLine[]): { add: number; del: number } {
  return {
    add: lines.filter((l) => l.t === "add").length,
    del: lines.filter((l) => l.t === "del").length,
  };
}
