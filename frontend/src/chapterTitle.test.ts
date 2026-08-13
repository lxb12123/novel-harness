import { describe, expect, it } from "vitest";
import { findChapters, joinTitle, splitTitle, titleOf, withTitle } from "./chapterTitle";

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

  it("拆开：章号一段、名字一段（改名只动后面那段）", () => {
    expect(splitTitle("第22章 女神？ 学姐？")).toEqual({
      marker: "第22章",
      gap: " ",
      name: "女神？ 学姐？",
    });
    // 中文数字、全角空格、章标里自带的空格 —— 都按后端那条正则的原样切。
    expect(splitTitle("　第 12 章　初入青云")).toEqual({
      marker: "第 12 章",
      gap: "　",
      name: "初入青云",
    });
    expect(splitTitle("第一百零八章")).toEqual({ marker: "第一百零八章", gap: "", name: "" });
    expect(splitTitle("第三节 论道")).toMatchObject({ marker: "第三节", name: "论道" });
  });

  it("认不出章号的那一行 → 整行都是名字（作者照旧改得动它）", () => {
    // 真书里有把首行写成正文的稿子。这时没有可按住的章号，退回「整行可改」。
    expect(splitTitle("他说第三章很好看。")).toEqual({
      marker: "",
      gap: "",
      name: "他说第三章很好看。",
    });
  });

  it("装回去：**章号原样带回**，中间那截空白也原样留着", () => {
    const parts = splitTitle("　第 12 章　初入青云");
    expect(joinTitle(parts, "再入青云")).toBe("第 12 章　再入青云");
    // 名字清空 = 这一章只剩章号。**这是合法的**（真书里大把无题章），
    // 而且切章照样成立 —— 章号还在。
    expect(joinTitle(parts, "  ")).toBe("第 12 章");
    // 本来就没名字的那种，现在起一个：中间补一个半角空格。
    expect(joinTitle(splitTitle("第一百零八章"), "重逢")).toBe("第一百零八章 重逢");
  });

  it("没有章号的那种，清空后是空串 —— 调用方据此拒绝这次改名", () => {
    // 那一行整个没了的话，下一行正文就顶上来当章标题了（`titleOf` 取首个非空行）。
    expect(joinTitle(splitTitle("他说第三章很好看。"), "")).toBe("");
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
