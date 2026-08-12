import { describe, expect, it } from "vitest";
import { chapterOnOpen } from "./chapterCursor";

const many = Array.from({ length: 722 }, (_, i) => i + 1);

describe("打开一本书停在第几章", () => {
  it("换书 → 最后一章（作者是来接着写的，不是从第 1 章读起）", () => {
    expect(chapterOnOpen({ switched: true, chapter: 1, numbers: many })).toBe(722);
  });

  it("换书时哪怕新书里也有这个章号，照样去最后一章", () => {
    // 从 A 书第 3 章切到 B 书：B 也有第 3 章，但作者要的是 B 的结尾。
    expect(chapterOnOpen({ switched: true, chapter: 3, numbers: [1, 2, 3, 4] })).toBe(4);
  });

  it("没换书就别乱跳 —— 作者正看着第 300 章", () => {
    expect(chapterOnOpen({ switched: false, chapter: 300, numbers: many })).toBeNull();
  });

  it("章号在这本书里不存在了（稿子被删过）→ 落到最后一章", () => {
    expect(chapterOnOpen({ switched: false, chapter: 900, numbers: many })).toBe(722);
  });

  it("一章都没有的书 → 不动，交给中栏的空状态去说", () => {
    expect(chapterOnOpen({ switched: true, chapter: 1, numbers: [] })).toBeNull();
    expect(chapterOnOpen({ switched: false, chapter: 1, numbers: [] })).toBeNull();
  });

  it("章号不连续时取的是最后一条，不是最大值以外的猜测", () => {
    expect(chapterOnOpen({ switched: true, chapter: 1, numbers: [1, 5, 9] })).toBe(9);
  });
});
