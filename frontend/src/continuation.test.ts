import { describe, expect, it } from "vitest";
import {
  cleanSuggestion,
  nextChunkLength,
  shouldSuggest,
  tailAfter,
  tailBefore,
} from "./continuation";
import fixtures from "./__fixtures__/api.json";

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
  // **上限是外面传进来的**（后端按模型窗口算，跟着 `GET /api/settings` 回来）。
  // 这一组里出现的每一个数都是这条测试自己造的输入，不是「续写的上限」——
  // 那个常量 2026-08-22 从前端删了，它曾经把后端整套伸缩设计架空
  // （`tests/test_continuation_tail_limit.py` 现在钉着「前端不许再有一个」）。
  it("只取光标前面那一截", () => {
    expect(tailBefore("一二三四五", 3, 99)).toBe("一二三");
  });

  it("超长时按传进来的上限截，且不会把一个字切成两半", () => {
    const limit = 12;
    const doc = "字".repeat(limit + 500);
    const tail = tailBefore(doc, doc.length, limit);
    expect(Array.from(tail)).toHaveLength(limit);
    expect(tail.endsWith("字")).toBe(true);
  });

  it("🔴 上限**真的跟着那个参数走** —— 换一个更大的数就多带一些上文", () => {
    // 这条是这次修的那个 bug 的正面：换个窗口更大的模型，后端算出来的数变大，
    // 送出去的上文就该跟着变多。写死一个常量的实现在这条上会绿——所以它不测「等于 N」，
    // 它测「两个不同的上限拿到两个不同长度的结果」。
    const doc = "字".repeat(5_000);
    expect(Array.from(tailBefore(doc, doc.length, 800))).toHaveLength(800);
    expect(Array.from(tailBefore(doc, doc.length, 4_000))).toHaveLength(4_000);
  });

  it("上限来自后端那条设置返回（真 dump），前端不自己算", () => {
    // 吃的是 `tests/test_frontend_contract.py` 从真 app dump 的那份：这一位一旦被
    // 后端改名或删掉，这条当场红——而不是等到作者发现「AI 好像没在看我前面写的」。
    const limit = fixtures.settings.continuation_tail_limit;
    expect(typeof limit).toBe("number");
    expect(limit).toBeGreaterThan(0);
    const doc = "字".repeat(limit + 10);
    expect(Array.from(tailBefore(doc, doc.length, limit))).toHaveLength(limit);
  });

  it("越界的光标位置不会炸", () => {
    expect(tailBefore("短", 999, 99)).toBe("短");
    expect(tailBefore("短", -5, 99)).toBe("");
  });
});

describe("送过去的下文（改旧章时光标后面那截已经写好的正文）", () => {
  it("只取光标后面那一截", () => {
    expect(tailAfter("一二三四五", 3, 99)).toBe("四五");
  });

  it("🔴 超长时留住**紧挨着光标**的那一头，不是最后那一头", () => {
    // 切错方向的症状是「模型接的是三千字之后那一段」——读起来像它没听懂，
    // 而不像一个截断 bug，所以没有任何别的东西会红。
    expect(tailAfter("近近近远远远", 0, 3)).toBe("近近近");
  });

  it("在章末（光标后面什么都没有）→ 空串，那一块整块不出现", () => {
    const doc = "他没有回头。";
    expect(tailAfter(doc, doc.length, 99)).toBe("");
  });

  it("和上文共用同一个上限，同样不会把一个字切成两半", () => {
    const limit = 12;
    const doc = "字".repeat(limit + 500);
    expect(Array.from(tailAfter(doc, 0, limit))).toHaveLength(limit);
  });

  it("越界的光标位置不会炸", () => {
    expect(tailAfter("短", 999, 99)).toBe("");
    expect(tailAfter("短", -5, 99)).toBe("短");
  });
});

describe("方向键逐口接受：一口该吞多长", () => {
  it("一个词，下一个词开始前停手", () => {
    expect(nextChunkLength("没有点灯")).toBe(2); // 「没有」｜「点灯」
  });

  it("词后面紧跟的标点跟着一起吞，不用作者再按一次", () => {
    expect(nextChunkLength("点灯，他愣住了。")).toBe(3); // 「点灯，」｜「他愣住了。」
  });

  it("整段只剩标点、一个词都没有 —— 吞到底，不会卡在半路", () => {
    expect(nextChunkLength("，。")).toBe(2);
  });

  it("空字符串不炸", () => {
    expect(nextChunkLength("")).toBe(0);
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
