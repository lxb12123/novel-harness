import { describe, expect, it } from "vitest";
import { findChapters, titleOf, withTitle } from "./chapterTitle";

// 「章标题 = 正文首个非空行」这条是**后端定的**（`importer.chapter_files()`）。
// 这儿钉的是「前端改标题时改的是不是同一行」——不是同一行的话，
// 目录上的标题不会变，正文里却会多出一行看着像标题的字。

const DOC = "第一章 血脉\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n";

describe("章标题", () => {
  it("标题就是首个非空行，两头空白不算", () => {
    expect(titleOf(DOC)).toBe("第一章 血脉");
    expect(titleOf("  \n\n  第七章 试探  \n正文")).toBe("第七章 试探");
  });

  it("整份都是空的 → 没有标题（不是抛错，也不是拿正文冒充）", () => {
    expect(titleOf("")).toBe("");
    expect(titleOf("\n \n")).toBe("");
  });

  it("改标题只动那一行，正文一个字节不动", () => {
    expect(withTitle(DOC, "第一章 血脉（改）")).toBe(
      "第一章 血脉（改）\n\n萧决在青云城主府第一次听说了血脉秘密的真相。\n",
    );
  });

  it("**换的是首个非空行，不是第 0 行** —— 稿子前面有空行时两者不是同一行", () => {
    // 改错行 = 目录上的标题没变，正文里凭空多一行看着像标题的字。
    expect(withTitle("\n\n第三章 对峙\n正文", "第三章 新名")).toBe("\n\n第三章 新名\n正文");
  });

  it("空稿子上改标题 = 这一章从此有了第一行", () => {
    expect(withTitle("", "第一章")).toBe("第一章");
    expect(withTitle("\n\n", "第一章")).toBe("第一章\n\n\n");
  });

  it("找章节：标题和章号都算数 —— 722 章的书里他记得住的常常只有号", () => {
    const list = [
      { number: 1, title: "第一章 血脉" },
      { number: 12, title: "第十二章 对峙" },
      { number: 122, title: "第一百二十二章 血脉再现" },
    ];
    expect(findChapters(list, "血脉").map((c) => c.number)).toEqual([1, 122]);
    expect(findChapters(list, "12").map((c) => c.number)).toEqual([12, 122]);
    // 空串 = 不筛，**不是一条都不匹配**：单子刚打开时它就是空的。
    expect(findChapters(list, "  ")).toHaveLength(3);
  });
});
