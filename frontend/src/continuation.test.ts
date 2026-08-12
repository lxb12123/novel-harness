import { describe, expect, it } from "vitest";
import { TAIL_LIMIT, cleanSuggestion, shouldSuggest, tailBefore } from "./continuation";

const base = { before: "萧决推开门。", hasSelection: false, hasSuggestion: false };

describe("什么时候才去问模型", () => {
  it("停在一句话后面 —— 问", () => {
    expect(shouldSuggest(base)).toBe(true);
  });

  it("光标前没有实质内容就别问 —— 问了也只能瞎编", () => {
    expect(shouldSuggest({ ...base, before: "" })).toBe(false);
    expect(shouldSuggest({ ...base, before: "   \n\n  " })).toBe(false);
  });

  it("正选着一段文字时别问 —— 作者在选/删/查，不是在往下写", () => {
    expect(shouldSuggest({ ...base, hasSelection: true })).toBe(false);
  });

  it("已经有建议挂着就别再问 —— 再问一次是覆盖自己，纯浪费作者的钱", () => {
    expect(shouldSuggest({ ...base, hasSuggestion: true })).toBe(false);
  });

  it("正文还没加载完就别问 —— 那时的上文是空的，或者是上一章的", () => {
    expect(shouldSuggest({ ...base, loading: true })).toBe(false);
  });
});

describe("送过去的上文", () => {
  it("只取光标前面那一截", () => {
    expect(tailBefore("一二三四五", 3)).toBe("一二三");
  });

  it("超长时按 code point 截，不会把一个字切成两半", () => {
    const doc = "字".repeat(TAIL_LIMIT + 500);
    const tail = tailBefore(doc, doc.length);
    expect(Array.from(tail)).toHaveLength(TAIL_LIMIT);
    expect(tail.endsWith("字")).toBe(true);
  });

  it("越界的光标位置不会炸", () => {
    expect(tailBefore("短", 999)).toBe("短");
    expect(tailBefore("短", -5)).toBe("");
  });
});

describe("模型返回的清理", () => {
  it("去掉首尾空白（含全角空格），中间一个字不动", () => {
    expect(cleanSuggestion("　\n  他没有回头。\n\n他知道那扇门后面是什么。  \n")).toBe(
      "他没有回头。\n\n他知道那扇门后面是什么。",
    );
  });

  it("**不做去重**：判断「这两句是不是同一句」是语义判断，本仓库不做", () => {
    const echoed = "夜色沉下来。夜色沉下来，他推开门。";
    expect(cleanSuggestion(echoed)).toBe(echoed);
  });
});
