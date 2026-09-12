import { describe, expect, it } from "vitest";
import { editMarks } from "./editMarks";

// 编辑器里没保存的改动的痕迹（`editMarks.ts`）：减去的行红、增加的行绿，按行算，和 git 一样。
// 谁改的不重要——写作助手写的和作者敲的走同一条路，所以这儿只喂字，不分来路。

const SAVED = "雨歇了。\n\n贾环站在废墟之中，久久没有动弹。\n\n“走吧。”他转身。";
const L = (text: string) => text.split("\n");

describe("和保存版比", () => {
  it("一个字都没改：一处痕迹都没有", () => {
    expect(editMarks(SAVED, SAVED)).toEqual([]);
  });

  it("末尾多一段：那一段绿，没有红；段落之间的空行不涂", () => {
    const now = SAVED + "\n\n周迅挣扎着爬起来。";
    expect(editMarks(SAVED, now)).toEqual([{ kind: "added", line: 6 }]);
    expect(L(now)[6]).toBe("周迅挣扎着爬起来。");
  });

  it("改了一段里的几个字：旧的那一行红、插在新的那一行前面，新的那一行绿", () => {
    const now = SAVED.replace("久久没有动弹", "许久没有动弹");
    expect(editMarks(SAVED, now)).toEqual([
      { kind: "removed", before: 2, lines: ["贾环站在废墟之中，久久没有动弹。"] },
      { kind: "added", line: 2 },
    ]);
  });

  it("删掉一段：红块插在原来的位置（下一段的前面），后面的段不涂", () => {
    const now = "雨歇了。\n\n“走吧。”他转身。";
    expect(editMarks(SAVED, now)).toEqual([
      { kind: "removed", before: 2, lines: ["贾环站在废墟之中，久久没有动弹。"] },
    ]);
    expect(L(now)[2]).toBe("“走吧。”他转身。");
  });

  it("删掉最后一段：红块挂在末尾（`before` 等于行数）", () => {
    const now = "雨歇了。\n\n贾环站在废墟之中，久久没有动弹。";
    expect(editMarks(SAVED, now)).toEqual([{ kind: "removed", before: 3, lines: ["“走吧。”他转身。"] }]);
  });

  it("整段删光：一块红挂着，别的什么都没有", () => {
    expect(editMarks(SAVED, "")).toEqual([
      { kind: "removed", before: 1, lines: L(SAVED).filter((s) => s !== "") },
    ]);
  });

  it("整章重写：旧的全红、新的全绿，先减后增——和 git 的顺序一样", () => {
    const now = "夜雨初歇，京城处处弥漫着劫后余生的肃杀气息。\n\n贾环被人带出城南破庙时，浑身衣衫已被雨水浸透。";
    expect(editMarks(SAVED, now)).toEqual([
      { kind: "removed", before: 0, lines: L(SAVED).filter((s) => s !== "") },
      { kind: "added", line: 0 },
      { kind: "added", line: 2 },
    ]);
  });

  it("空稿起草（新章）：整篇绿，一块红都没有", () => {
    expect(editMarks("", "第一段。\n\n第二段。")).toEqual([
      { kind: "added", line: 0 },
      { kind: "added", line: 2 },
    ]);
  });

  it("空行不算改动（磁盘那一版末尾多一个换行、作者多敲的一个回车，都不是一处修改）", () => {
    expect(editMarks(SAVED + "\n", SAVED)).toEqual([]);
    expect(editMarks(SAVED, SAVED + "\n\n")).toEqual([]);
    expect(editMarks("\n" + SAVED, SAVED)).toEqual([]);
    expect(editMarks(SAVED, SAVED.replace("\n\n", "\n\n\n"))).toEqual([]);
  });

  it("现在这份开头多了空行：行号仍是对着它原样数的", () => {
    const now = "\n\n" + SAVED + "\n\n新的一段。";
    expect(editMarks(SAVED, now)).toEqual([{ kind: "added", line: 8 }]);
    expect(L(now)[8]).toBe("新的一段。");
  });
});

describe("正在流进来的那一稿（`streaming`）", () => {
  it("还在打的最后一行不涂、不比；后面没有保留段压着的删除先不画", () => {
    // 写手正在重写第一段：第一行还没打完。这时候保存版的三段都「还没写到」，一块红都不画。
    expect(editMarks(SAVED, "夜雨初歇，京城", true)).toEqual([]);
    // 第一段打完、换行了：它是新的（绿）；旧的第一段是不是删了还不知道——后面还没有
    // 一段原样保留的正文钉住它，先不画红。
    expect(editMarks(SAVED, "夜雨初歇，京城处处肃杀。\n\n", true)).toEqual([{ kind: "added", line: 0 }]);
  });

  it("写到一段原样保留的正文，它前面的删除就钉死了：红块这时才出来", () => {
    const now = "夜雨初歇，京城处处肃杀。\n\n贾环站在废墟之中，久久没有动弹。\n\n“走";
    expect(editMarks(SAVED, now, true)).toEqual([
      { kind: "removed", before: 0, lines: ["雨歇了。"] },
      { kind: "added", line: 0 },
    ]);
  });

  it("空行不算钉子：只隔着一个空行的删除仍然不画（下一段可能原样写回来）", () => {
    const now = "夜雨初歇，京城处处肃杀。\n\n雨";
    expect(editMarks(SAVED, now, true)).toEqual([{ kind: "added", line: 0 }]);
  });

  it("流一收场，尾巴上的删除一起出来", () => {
    const now = "夜雨初歇，京城处处肃杀。\n\n贾环站在废墟之中，久久没有动弹。";
    // 流着（第二段刚换行、第三行还没打）：第一段的删除钉死了，尾巴上那一段的先不画。
    expect(editMarks(SAVED, now + "\n", true)).toEqual([
      { kind: "removed", before: 0, lines: ["雨歇了。"] },
      { kind: "added", line: 0 },
    ]);
    expect(editMarks(SAVED, now, false)).toEqual([
      { kind: "removed", before: 0, lines: ["雨歇了。"] },
      { kind: "added", line: 0 },
      { kind: "removed", before: 3, lines: ["“走吧。”他转身。"] },
    ]);
  });
});
