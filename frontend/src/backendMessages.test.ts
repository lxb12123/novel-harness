import { describe, expect, it } from "vitest";
import { allMessageCodes, messageForCode, rawTemplate } from "./backendMessages";

// 同 `tests/test_shared_prompt_terms.py` 那把尺：每个码两侧都要有值，英文侧
// 零中文字符。函数式模板（有条件分支的那三条）用空参数跑一次，够扫字符集。

const CJK = /[一-鿿　-〿＀-￯]/;

describe("每个码两侧都非空，英文侧零中文字符", () => {
  for (const code of allMessageCodes()) {
    it(code, () => {
      const zh = rawTemplate(code, "zh");
      const en = rawTemplate(code, "en");
      expect(zh.trim()).not.toBe("");
      expect(en.trim()).not.toBe("");
      expect(CJK.test(en)).toBe(false);
    });
  }
});

describe("认不出的码", () => {
  it("messageForCode 返回 undefined，不猜、不报错", () => {
    expect(messageForCode("no_such_code_at_all", "zh")).toBeUndefined();
    expect(messageForCode("no_such_code_at_all", "en")).toBeUndefined();
  });
});

describe("参数真的会被填进模板", () => {
  it("chapter_missing_message 的 {chapter} 换成真值", () => {
    expect(messageForCode("chapter_missing_message", "zh", { chapter: 5 })).toBe(
      "第 5 章已经不在了。刷新一下就对得上了。",
    );
    expect(messageForCode("chapter_missing_message", "en", { chapter: 5 })).toBe(
      "Chapter 5 is no longer there. Refresh and things will line up again.",
    );
  });

  it("validation_blocked_title：有 rule_title 和 rest 时都要出现", () => {
    const zh = messageForCode("validation_blocked_title", "zh", {
      paragraph: 3,
      rule_title: "设定提前出现",
      rest: 2,
      issue_message: "血脉秘密在第 2 章就出现了",
    });
    expect(zh).toContain("第 3 段·设定提前出现");
    expect(zh).toContain("血脉秘密在第 2 章就出现了");
    expect(zh).toContain("（另有 2 处）");

    const en = messageForCode("validation_blocked_title", "en", {
      paragraph: 3,
      rule_title: "设定提前出现",
      rest: 2,
      issue_message: "血脉秘密在第 2 章就出现了",
    });
    expect(en).toContain("paragraph 3·设定提前出现");
    expect(en).toContain("(2 more)");
    // checks/ 的原文（rule_title / issue_message）是不透明参数，原样透传，
    // 不指望它也是英文——那是另一批的范围。
  });

  it("extraction_yielded_nothing_title：unresolved 和 proposal_count 选中不同的分支", () => {
    const withProposals = messageForCode("extraction_yielded_nothing_title", "en", {
      lost: 12,
      unresolved: "true",
      proposal_count: 3,
    });
    expect(withProposals).toContain("aren't recognized in the roster yet");
    expect(withProposals).toContain("raised 3 items");

    const noProposals = messageForCode("extraction_yielded_nothing_title", "zh", {
      lost: 12,
      unresolved: "false",
      proposal_count: 0,
    });
    expect(noProposals).toContain("它们都没能落库");
    expect(noProposals).toContain("花名册里先得有人");
  });

  it("clash_title：conflict 枚举查表，不认识的枚举原样透传不崩", () => {
    expect(
      messageForCode("clash_title", "en", { sentence: 3, chapter: 64, conflict: "setting" }),
    ).toBe("Sentence 3 ↔ chapter 64: setting doesn't match.");
    expect(
      messageForCode("clash_title", "zh", { sentence: 3, chapter: 64, conflict: "unknown_kind" }),
    ).toBe("第 3 句 ↔ 第 64 章：unknown_kind。");
  });
});
