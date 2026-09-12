// 编辑器里**没保存的改动的痕迹**（作者 2026-09-12：「不管是他写的还是他编辑的，只要没有保存
// 就要有那种编辑的痕迹，红色和绿色代表减去和增加」，指着一张 git diff 的图说「类似这种效果」）。
//
// 拿编辑器里现在这份正文和**上一次保存的那一版**比（`CenterEditor` 手上的 `savedBody`）：
//
// - 保存版里有、现在没有的段 = **减去**（红）。它已经不在正文里了，所以画成一块不可编辑的
//   字块，插回它原来的位置——作者看得见自己（或写作助手）删了什么；
// - 现在有、保存版里没有的段 = **增加**（绿），整行底色。
//
// 一段改了几个字 = 旧的那一段红、新的那一段绿——**按行算，和 git 一样**，不做字级 diff。
// 谁改的不重要：写作助手写进来的和作者自己敲的走同一条路，按「保存」痕迹就没了
// （那一刻保存版就是眼前这份）。这一层不碰 DOM：给的是「第几行是新增」「哪一行的前面该
// 插一块被删掉的字」，画在哪儿归 `CodeEditor`。
//
// **空行不参与比对。** 段落之间的空行长得一模一样，让它们参与，LCS 会拿它们当锚点，
// 把这一段的红块和另一段的绿行配成对；只比有字的行，一段改了就是「它的旧一行 + 新一行」。
// 代价是「作者删了一个空行」没有痕迹——保存按钮上的水滴仍然说它没保存。
//
// ── 正在流进来的那一稿（`streaming`）──────────────────────────────────────
//
// 写作助手的字一片片到、逐字露（`typewriter.ts`），那时候正文是**半份**：最后一行还在打，
// 后面的段落还没来。半份对着全份比，会把「还没写到的段」全判成删掉——而下一秒它可能就
// 原样写回来了，红块闪一下又没，作者看到的是一片乱跳的红。所以流着的时候：
//
// 1. 最后一行（还在打的那一行）不参与比对，也不涂色；
// 2. 只有**后面有一段原样保留的正文**压着的那些删除才画——它们已经被后面的段落钉死了；
//    尾巴上的删除等流写完再画。增加的行照画（那是已经到手的字）。
//
// 流一收场（`streaming` 为假），整份正文对整份保存版，尾巴上的删除才一起出来。

import { lineDiff } from "./diff";

export type EditMark =
  /** 现在这份正文的第 `line` 行（0 起）是新增的：整行涂绿。 */
  | { kind: "added"; line: number }
  /** 保存版里的这几段不在了：画一块红，插在现在这份正文第 `before` 行的**前面**
   *  （`before` 等于行数 = 插在末尾）。 */
  | { kind: "removed"; before: number; lines: string[] };

const blank = (s: string) => s.trim() === "";

export function editMarks(saved: string, current: string, streaming = false): EditMark[] {
  const all = current.split("\n");
  // 还在打的那一行不算。行号对着 `current` 原样说，所以这儿只是不看它，不挪别的行。
  const lines = streaming ? all.slice(0, -1) : all;
  const paras = lines.map((s, line) => ({ s, line })).filter((p) => !blank(p.s));
  const old = saved.split("\n").filter((s) => !blank(s));
  if (paras.length === 0 && old.length === 0) return [];
  if (old.length === 0) return paras.map((p) => ({ kind: "added", line: p.line }));
  if (paras.length === 0) {
    return streaming ? [] : [{ kind: "removed", before: all.length, lines: old }];
  }
  const diff = lineDiff(old.join("\n"), paras.map((p) => p.s).join("\n"));

  // 流着的时候，最后一段原样保留的正文之后的删除都还没钉死。
  let anchor = diff.length;
  if (streaming) {
    anchor = -1;
    for (let k = diff.length - 1; k >= 0; k--) {
      if (diff[k].t === "same") {
        anchor = k;
        break;
      }
    }
  }

  const marks: EditMark[] = [];
  let i = 0; // 走到 diff 的第几条
  let k = 0; // 走到现在这份正文的第几段
  while (i < diff.length) {
    if (diff[i].t === "same") {
      i++;
      k++;
      continue;
    }
    // 一个 hunk：连着的 del / add。删掉的那块画在这个 hunk 里第一段新字的前面
    //（没有新字就是下一段原样保留的正文前面；后面什么都没有就是末尾）。
    const before = k < paras.length ? paras[k].line : all.length;
    const removed: string[] = [];
    const added: EditMark[] = [];
    while (i < diff.length && diff[i].t !== "same") {
      if (diff[i].t === "del") removed.push(diff[i].s);
      else added.push({ kind: "added", line: paras[k++].line });
      i++;
    }
    // 先减后增：同一处的红块排在绿行前面（git 的顺序）。
    if (removed.length > 0 && i <= anchor) marks.push({ kind: "removed", before, lines: removed });
    marks.push(...added);
  }
  return marks;
}
