// 行级 diff（LCS）——版本对比用。把 old → current 表达成 same/add/del 的行序列。
// del = 从那一版删掉的、add = 现在这版新增的。O(n·m)，一章几百行足够。

export type DiffLine = { t: "same" | "add" | "del"; s: string };

export function lineDiff(oldText: string, curText: string): DiffLine[] {
  const A = oldText.split("\n");
  const B = curText.split("\n");
  const n = A.length;
  const m = B.length;

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
